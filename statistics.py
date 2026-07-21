import os
import sys
import time
import json
import tempfile
import random
import requests
import io
import platform
import multiprocessing
import shutil
import uuid
import urllib.parse
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver import ActionChains
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.desired_capabilities import DesiredCapabilities
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from serverchan_sdk import sc_send

from AliV3 import AliV3

# 全局变量用于收集总结日志
in_summary = False
summary_logs =[]

# 全局连续失败状态控制
consecutive_query_fails = 0
skip_query = False

consecutive_proxy_fails = 0
disable_global_proxy = False

def log(msg):
    full_msg = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(full_msg, flush=True)
    if in_summary:
        summary_logs.append(msg)  # 只收集纯消息，无时间戳

def desensitize_password(pwd):
    """脱敏密码显示"""
    if len(pwd) <= 3:
        return pwd
    return pwd[:3] + '*****'

def ensure_proxy_whitelist():
    """预检代理IP白名单状态，避免首次获取代理时阻塞导致 authCode 失效"""
    log("正在预检代理IP白名单状态...")
    apikey = os.getenv('DM_APIKEY')
    pwd = os.getenv('DM_PWD')
    proxy_api_url = f"http://need1.dmdaili.com:7771/dmgetip.asp?apikey={apikey}&pwd={pwd}&getnum=1&httptype=1&geshi=2&fenge=1&fengefu=&operate=all"
    
    for _ in range(3):
        try:
            response = requests.get(proxy_api_url, timeout=10)
            data = response.json()
            if data.get("code") == 605:
                log("代理IP已自动添加到白名单，等待15秒生效...")
                time.sleep(15)
                log("✅ 代理IP白名单生效完毕。")
                return
            elif data.get("code") == 0:
                log("✅ 代理IP白名单已在生效状态。")
                return
            elif data.get("code") == 1 and "Too Many Requests" in data.get("msg", ""):
                time.sleep(5)
            else:
                break
        except Exception as e:
            log(f"⚠ 预检代理白名单异常: {e}")
            break

def with_retry(func, max_retries=5, delay=1):
    """如果函数返回None或抛出异常，静默重试"""
    def wrapper(*args, **kwargs):
        for attempt in range(max_retries):
            try:
                result = func(*args, **kwargs)
                if result is not None:
                    return result
                time.sleep(delay + random.uniform(0, 1))  # 随机延迟
            except Exception:
                time.sleep(delay + random.uniform(0, 1))  # 随机延迟
        return None
    return wrapper

@with_retry
def extract_token_from_local_storage(driver):
    """从 localStorage 提取 X-JLC-AccessToken"""
    try:
        token = driver.execute_script("return window.localStorage.getItem('X-JLC-AccessToken');")
        if token:
            log(f"✅ 成功从 localStorage 提取 token: {token[:30]}...")
            return token
        else:
            alternative_keys =[
                "x-jlc-accesstoken",
                "accessToken", 
                "token",
                "jlc-token"
            ]
            for key in alternative_keys:
                token = driver.execute_script(f"return window.localStorage.getItem('{key}');")
                if token:
                    log(f"✅ 从 localStorage 的 {key} 提取到 token: {token[:30]}...")
                    return token
    except Exception as e:
        log(f"❌ 从 localStorage 提取 token 失败: {e}")
    
    return None

@with_retry
def extract_secretkey_from_devtools(driver):
    """使用 DevTools 从网络请求中提取 secretkey"""
    secretkey = None
    
    try:
        logs = driver.get_log('performance')
        
        for entry in logs:
            try:
                message = json.loads(entry['message'])
                message_type = message.get('message', {}).get('method', '')
                
                if message_type == 'Network.requestWillBeSent':
                    request = message.get('message', {}).get('params', {}).get('request', {})
                    url = request.get('url', '')
                    
                    if 'm.jlc.com' in url:
                        headers = request.get('headers', {})
                        secretkey = (
                            headers.get('secretkey') or 
                            headers.get('SecretKey') or
                            headers.get('secretKey') or
                            headers.get('SECRETKEY')
                        )
                        
                        if secretkey:
                            log(f"✅ 从请求中提取到 secretkey: {secretkey[:20]}...")
                            return secretkey
                
                elif message_type == 'Network.responseReceived':
                    response = message.get('message', {}).get('params', {}).get('response', {})
                    url = response.get('url', '')
                    
                    if 'm.jlc.com' in url:
                        headers = response.get('requestHeaders', {})
                        secretkey = (
                            headers.get('secretkey') or 
                            headers.get('SecretKey') or
                            headers.get('secretKey') or
                            headers.get('SECRETKEY')
                        )
                        
                        if secretkey:
                            log(f"✅ 从响应中提取到 secretkey: {secretkey[:20]}...")
                            return secretkey
                            
            except:
                continue
                
    except Exception as e:
        log(f"❌ DevTools 提取 secretkey 出错: {e}")
    
    return secretkey

@with_retry
def extract_secretkey_from_browser(driver):
    """从浏览器全局变量或 DevTools 中提取 secretkey"""
    try:
        sk = driver.execute_script("return window._my_secretkey;")
        if sk:
            log(f"✅ 从浏览器底层请求提取到合法秘钥: {sk[:20]}...")
            return sk
    except Exception:
        pass
    
    return extract_secretkey_from_devtools(driver)

def get_valid_proxy(account_index):
    global disable_global_proxy, consecutive_proxy_fails
    apikey = os.getenv('DM_APIKEY')
    pwd = os.getenv('DM_PWD')
    proxy_api_url = f"http://need1.dmdaili.com:7771/dmgetip.asp?apikey={apikey}&pwd={pwd}&getnum=1&httptype=1&geshi=2&fenge=1&fengefu=&operate=all"
    max_attempts = 100
    attempt = 0
    
    while attempt < max_attempts:
        try:
            log(f"账号 {account_index} - 正在获取代理IP (尝试 {attempt + 1}/{max_attempts})...")
            response = requests.get(proxy_api_url, timeout=10)
            
            try:
                data = response.json()
            except Exception:
                log(f"账号 {account_index} - ⚠ 代理API返回非JSON数据，接口返回: {response.text}")
                attempt += 1
                time.sleep(2)
                continue

            if data.get("code") == 605:
                log(f"账号 {account_index} - 代理IP已自动添加到白名单，等待15秒后重试...")
                attempt += 1
                time.sleep(15)
                continue 
            elif data.get("code") == 1 and "Too Many Requests" in data.get("msg", ""):
                log(f"账号 {account_index} - 代理API请求过快，等待5秒后重试...")
                attempt += 1
                time.sleep(5)
                continue
            elif data.get("code") == 0 and data.get("data"):
                proxy_info = data["data"][0]
                ip = proxy_info.get("ip")
                port = proxy_info.get("port")
                city = proxy_info.get("city", "未知地区")
                if ip and port:
                    proxy_url = f"http://{ip}:{port}"
                    proxies = {
                        "http": proxy_url,
                        "https": proxy_url
                    }
                    log(f"账号 {account_index} - ✅ 代理获取成功: {ip}:{port}[{city}]")
                    return proxies
            
            log(f"账号 {account_index} - ⚠ 代理获取失败，接口返回: {json.dumps(data, ensure_ascii=False)}")
            attempt += 1
            time.sleep(2)
        except Exception as e:
            log(f"账号 {account_index} - ❌ 获取代理IP异常: {e}")
            attempt += 1
            time.sleep(2)
    
    log(f"账号 {account_index} - ❌ 连续100次获取代理失败，放弃使用代理")
    consecutive_proxy_fails += 1
    if consecutive_proxy_fails >= 5:
        disable_global_proxy = True
        log("⚠ 连续5次代理获取/使用失败，接下来的账号全部放弃使用代理！")
    return None

class JLCClient:
    """调用嘉立创接口"""
    
    def __init__(self, access_token, secretkey, account_index, driver, proxies=None, cookies=None):
        self.base_url = "https://m.jlc.com"
        self.account_index = account_index
        self.driver = driver
        self.proxies = proxies
        self.cookies = cookies or {}
        
        self.message = ""
        self.jindou_count = 0  # 当前金豆数量
        
        xsrf_token = urllib.parse.unquote(self.cookies.get('XSRF-TOKEN', ''))
        
        self.headers = {
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'x-jlc-clienttype': 'WEB',
            'accept': 'application/json, text/plain, */*',
            'x-jlc-accesstoken': access_token,
            'secretkey': secretkey,
            'Referer': 'https://m.jlc.com/mapp/pages/my/index',
        }
        if xsrf_token:
            self.headers['x-xsrf-token'] = xsrf_token
        
    def send_request(self, url, method='GET', use_proxy=True):
        """发送 API 请求"""
        global disable_global_proxy, consecutive_proxy_fails
        
        # 如果要求使用代理，但实际上还没有代理，则获取一个
        if use_proxy and not disable_global_proxy and not self.proxies:
            self.proxies = get_valid_proxy(self.account_index)
            
        # 真正使用代理的条件：要求使用、没被禁用，且成功获取到了代理
        is_actually_using_proxy = use_proxy and not disable_global_proxy and self.proxies is not None
        
        max_retries = 20 if is_actually_using_proxy else 1
        
        for attempt in range(max_retries):
            try:
                # 重新判定，因为过程中可能 disable_global_proxy 被修改
                req_proxies = self.proxies if use_proxy and not disable_global_proxy else None
                
                if method.upper() == 'GET':
                    response = requests.get(url, headers=self.headers, cookies=self.cookies, timeout=10, proxies=req_proxies)
                else:
                    response = requests.post(url, headers=self.headers, cookies=self.cookies, timeout=10, proxies=req_proxies)
                
                if response.status_code == 200:
                    if req_proxies:
                        consecutive_proxy_fails = 0
                    try:
                        return response.json()
                    except ValueError:
                        log(f"账号 {self.account_index} - ❌ 请求成功但接口返回了非 JSON 数据，状态码: 200，原始响应: {response.text}")
                        return None
                else:
                    log(f"账号 {self.account_index} - ❌ 请求失败，状态码: {response.status_code}，原始响应: {response.text}")
                    return None
            except requests.exceptions.RequestException as e:
                if use_proxy and not disable_global_proxy and self.proxies:
                    if isinstance(e, requests.exceptions.ProxyError):
                        error_type = "代理拒绝连接/代理错误"
                    elif isinstance(e, requests.exceptions.ConnectTimeout):
                        error_type = "连接代理超时"
                    elif isinstance(e, requests.exceptions.ReadTimeout):
                        error_type = "代理响应超时"
                    elif isinstance(e, requests.exceptions.Timeout):
                        error_type = "请求超时"
                    elif isinstance(e, requests.exceptions.ConnectionError):
                        error_type = "连接错误"
                    else:
                        error_type = "未知请求异常"
                        
                    log(f"账号 {self.account_index} - ⚠ 代理无效 ({error_type}: {e})，准备重新获取代理...")
                    
                    self.proxies = get_valid_proxy(self.account_index)
                    if not self.proxies:
                        break
                else:
                    log(f"账号 {self.account_index} - ❌ 请求异常 ({url}): {e}")
                    return None
        
        if is_actually_using_proxy and self.proxies and use_proxy and not disable_global_proxy:
            log(f"账号 {self.account_index} - ❌ 连续 {max_retries} 次代理请求失败")
            consecutive_proxy_fails += 1
            if consecutive_proxy_fails >= 5:
                disable_global_proxy = True
                log("⚠ 连续5次代理获取/使用失败，接下来的账号全部放弃使用代理！")
                
        return None
    
    def get_user_info(self):
        """获取用户信息"""
        log(f"账号 {self.account_index} - 获取用户信息...")
        url = f"{self.base_url}/api/appPlatform/center/setting/selectPersonalInfo"
        
        max_retries = 5
        data = None
        for attempt in range(max_retries):
            # ⚠️ 明确不使用代理
            data = self.send_request(url, use_proxy=False)
            
            if data and data.get('success'):
                log(f"账号 {self.account_index} - ✅ 用户信息获取成功")
                return True
                
            # 重试前刷新页面，重新提取 token 和 secretkey
            if attempt < max_retries - 1:
                if data:
                    log(f"账号 {self.account_index} - ⚠ 获取用户信息未返回success，准备重试，原始响应: {json.dumps(data, ensure_ascii=False)}")
                try:
                    navigate_and_interact_m_jlc(self.driver, self.account_index)
                    
                    access_token = extract_token_from_local_storage(self.driver)
                    secretkey = extract_secretkey_from_browser(self.driver)
                    if access_token:
                        self.headers['x-jlc-accesstoken'] = access_token
                    if secretkey:
                        self.headers['secretkey'] = secretkey
                        
                    # 同步获取最新的 cookies
                    sel_cookies = self.driver.get_cookies()
                    self.cookies = {c['name']: c['value'] for c in sel_cookies}
                    xsrf_token = urllib.parse.unquote(self.cookies.get('XSRF-TOKEN', ''))
                    if xsrf_token:
                        self.headers['x-xsrf-token'] = xsrf_token
                except:
                    pass  # 静默继续
        
        error_msg = data.get('message', '未知错误') if data else '请求失败'
        if data:
            log(f"账号 {self.account_index} - ⚠ 获取用户信息接口原始返回: {json.dumps(data, ensure_ascii=False)}")
        log(f"账号 {self.account_index} - ❌ 获取用户信息失败: {error_msg}")
        return False
    
    def get_points(self):
        """获取金豆数量"""
        log(f"账号 {self.account_index} - 获取金豆数量...")
        url = f"{self.base_url}/api/activity/front/getCustomerIntegral"
        
        max_retries = 5
        data = None
        for attempt in range(max_retries):
            # ⚠️ 明确不使用代理
            data = self.send_request(url, use_proxy=False)
            
            if data and data.get('success'):
                jindou_count = data.get('data', {}).get('integralVoucher', 0)
                return jindou_count
            
            # 重试前刷新页面，重新提取 token 和 secretkey
            if attempt < max_retries - 1:
                if data:
                    log(f"账号 {self.account_index} - ⚠ 获取金豆未返回success，准备重试，原始响应: {json.dumps(data, ensure_ascii=False)}")
                try:
                    navigate_and_interact_m_jlc(self.driver, self.account_index)
                    
                    access_token = extract_token_from_local_storage(self.driver)
                    secretkey = extract_secretkey_from_browser(self.driver)
                    if access_token:
                        self.headers['x-jlc-accesstoken'] = access_token
                    if secretkey:
                        self.headers['secretkey'] = secretkey
                        
                    sel_cookies = self.driver.get_cookies()
                    self.cookies = {c['name']: c['value'] for c in sel_cookies}
                    xsrf_token = urllib.parse.unquote(self.cookies.get('XSRF-TOKEN', ''))
                    if xsrf_token:
                        self.headers['x-xsrf-token'] = xsrf_token
                except:
                    pass  # 静默继续
        
        if data:
            log(f"账号 {self.account_index} - ⚠ 获取金豆数量接口原始返回: {json.dumps(data, ensure_ascii=False)}")
        log(f"账号 {self.account_index} - ❌ 获取金豆数量失败")
        return None
    
    def execute_query_process(self):
        """执行金豆查询流程"""        
        # 1. 获取用户信息
        if not self.get_user_info():
            return False
        
        time.sleep(random.randint(1, 2))
        
        # 2. 获取金豆数量
        points = self.get_points()
        if points is not None:
            self.jindou_count = points
            log(f"账号 {self.account_index} - 当前金豆💰: {self.jindou_count}")
            return True
        else:
            return False

def navigate_and_interact_m_jlc(driver, account_index):
    """访问 m.jlc.com 并等待页面正常加载完业务请求"""
    log(f"账号 {account_index} - 刷新页面以获取 Token 和 SecretKey...")
    
    try:
        driver.get("https://m.jlc.com/")
        WebDriverWait(driver, 12).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        time.sleep(4)
    except Exception as e:
        log(f"账号 {account_index} - 页面刷新出错: {e}")

def run_aliv3_task(username, password, output_file):
    """
    独立进程运行 AliV3，将日志写入文件。
    这样即使进程被 kill，文件内容依然存在。
    """
    with open(output_file, 'w', encoding='utf-8') as f:
        with redirect_stdout(f):
            try:
                # 尝试从全局获取 AliV3，或者重新导入
                if 'AliV3' in globals() and globals()['AliV3']:
                    ali_cls = globals()['AliV3']
                else:
                    from AliV3 import AliV3 as ali_cls
                
                ali = ali_cls()
                ali.main(username=username, password=password)
            except Exception as e:
                print(f"Error executing AliV3 in process: {e}")

def get_ali_auth_code(username, password, account_index=0):
    """
    调用 AliV3 获取 authCode，超时控制 (180s)
    """
    if AliV3 is None:
        return None
    
    # 创建临时文件用于存储子进程的 stdout
    fd, temp_path = tempfile.mkstemp()
    os.close(fd) # 关闭文件描述符，只保留路径
    
    auth_code = None
    ali_output = ""
    
    try:
        # 启动子进程运行 AliV3
        p = multiprocessing.Process(target=run_aliv3_task, args=(username, password, temp_path))
        p.start()
        
        # 等待进程结束，超时 180 秒
        p.join(timeout=180)
        
        if p.is_alive():
            log(f"账号 {account_index} - ❌ 登录超时 (超过180秒)，正在强制终止 登录脚本...")
            p.terminate()
            p.join() # 确保进程已退出
            
            # 读取已生成的日志以便调试
            try:
                with open(temp_path, 'r', encoding='utf-8') as f:
                    ali_output = f.read()
            except Exception:
                ali_output = "无法读取超时日志"
            
            log(f"--- 超时前的 登录脚本(AliV3) 日志 ---\n{ali_output}\n--------------------------")
            return None # 超时返回 None
            
        else:
            # 正常结束，读取日志
            try:
                with open(temp_path, 'r', encoding='utf-8') as f:
                    ali_output = f.read()
            except Exception:
                ali_output = ""

    finally:
        # 清理临时文件
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except:
                pass

    # 解析输出获取 authCode
    for line in ali_output.split('\n'):
        line = line.strip()
        if not line:
            continue
        
        # 尝试提取 JSON 部分，应对带前缀的情况
        json_str = line
        if not json_str.startswith('{') and '{' in json_str:
            json_str = json_str[json_str.find('{'):]

        try:
            data = json.loads(json_str)
            # 检查 authCode
            if isinstance(data, dict):
                # 兼容 success 字段，有些接口返回 true, 有些返回 "true" 或不返回
                # 重点检查 data.authCode
                inner_data = data.get('data')
                if isinstance(inner_data, dict) and 'authCode' in inner_data:
                    auth_code = inner_data['authCode']
                    break
            
            # 检查密码错误 (用于在外部判断)
            if isinstance(data, dict) and data.get('code') == 10208:
                pass
        except json.JSONDecodeError:
            continue
            
    # 如果没获取到 authCode，返回整个输出供外部记录日志
    if not auth_code:
        return ali_output 
        
    return auth_code

def query_account(username, password, account_index, total_accounts, retry_count=0):
    """为单个账号执行完整的查询流程"""
    retry_label = ""
    if retry_count > 0:
        retry_label = f" (重试{retry_count})"
    
    log(f"开始处理账号 {account_index}/{total_accounts}{retry_label}")
    
    # 初始化结果字典
    result = {
        'account_index': account_index,
        'query_success': False,
        'jindou_count': 0,
        'token_extracted': False,
        'secretkey_extracted': False,
        'retry_count': retry_count,
        'password_error': False,  #标记密码错误
        'actual_password': None,  # 实际使用的密码
        'backup_index': -1,  # 使用的备用密码索引，-1表示原密码
        'critical_error': False,  #标记严重错误（如多次调用依赖失败），需跳过重试
        'jlc_login_success': False # 标记金豆查询的JLC登录是否成功
    }
    
    # 显式创建临时目录用于 user-data-dir，以便后续清理
    user_data_dir = tempfile.mkdtemp()
    driver = None
    
    backup_passwords =[]

    try:
        # 1. 获取 authCode（用于 JLC 登录）
        log(f"账号 {account_index} - 正在调用 登录(AliV3) 依赖进行登录...")
        
        # 确保 AliV3 已加载
        if AliV3 is None:
             log(f"账号 {account_index} - ❌ 登录依赖未正确加载，无法登录")
             return result

        current_password = password  # 默认原密码
        current_backup_index = -1  # -1 表示原密码
        auth_code = None
        auth_result = None

        # 尝试密码（原密码 + 备用密码）
        while True:
            # 在这里加入 18 次重试循环，以处理网络不稳定导致的 authCode 获取失败
            # 如果是 10208 密码错误，会立即中断重试并切换密码
            is_pwd_error = False
            max_auth_retries = 18
            
            for auth_attempt in range(max_auth_retries):
                # 调用get_ali_auth_code，支持超时
                auth_result = get_ali_auth_code(username, current_password, account_index)
                
                # get_ali_auth_code 返回 None 表示超时
                if auth_result is None:
                    pass # 超时，继续重试
                elif isinstance(auth_result, str) and len(auth_result) > 100:
                    # 说明返回的是日志内容，未提取到 authCode
                    ali_output = auth_result
                    
                    # 检查是否包含错误码 10208（账密错误）
                    for line in ali_output.split('\n'):
                        line = line.strip()
                        if not line.startswith('{') and '{' in line:
                            line = line[line.find('{'):]
                        try:
                            data = json.loads(line)
                            if isinstance(data, dict) and data.get('code') == 10208:
                                is_pwd_error = True
                                break
                        except:
                            continue
                    
                    if is_pwd_error:
                        # 密码错误不需要重试调用，直接跳出内层循环进行密码切换
                        break
                else:
                    # 成功获取 authCode
                    auth_code = auth_result
                    break
                
                # 仅在非密码错误且未达到最大尝试次数时等待重试
                if auth_attempt < max_auth_retries - 1 and not is_pwd_error:
                    log(f"账号 {account_index} - ⚠ 未获取到AuthCode，等待5秒后第 {auth_attempt + 2} 次重试...")
                    time.sleep(5)

            # 处理重试循环后的结果
            
            if is_pwd_error:
                log(f"账号 {account_index} - ❌ 密码错误 ({'原密码' if current_backup_index == -1 else f'备用密码{current_backup_index + 1}'})")
                
                # 尝试下一个备用密码
                if current_backup_index == -1:
                    current_backup_index = 0
                else:
                    current_backup_index += 1
                    
                if current_backup_index >= len(backup_passwords):
                    # 所有密码都尝试完毕
                    log(f"账号 {account_index} - ❌ 所有备用密码尝试失败，跳过此账号")
                    result['password_error'] = True
                    return result
                
                current_password = backup_passwords[current_backup_index]
                log(f"账号 {account_index} - 🔄 尝试备用密码: {desensitize_password(current_password)}")
                continue # 继续循环尝试新密码
            
            if not auth_code:
                if auth_result is None:
                     return result
                else:
                     log(f"账号 {account_index} - ❌ 连续 {max_auth_retries} 次调用登录依赖失败，未返回有效AuthCode")
                     log("❌ 登录脚本输出如下：")
                     log(auth_result)
                     result['critical_error'] = True  # 标记为严重错误
                     return result
            else:
                # 成功获取 authCode
                result['actual_password'] = current_password
                result['backup_index'] = current_backup_index
                log(f"账号 {account_index} - ✅ 成功获取 authCode")
                break

        # 2. 金豆查询流程（使用获取到的 authCode）
        global skip_query
        if skip_query:
            log(f"账号 {account_index} - ⚠ 由于前面账号连续失败，跳过金豆查询流程")
            result['query_success'] = False
        else:
            log(f"账号 {account_index} - 开始金豆查询流程...")
            
            global disable_global_proxy, consecutive_proxy_fails
            current_proxies = None
            browser_success = False
            max_browser_retries = 5  # 本地网络启动，不需要太多重试
            
            # 浏览器初始化及加载的内部重试机制
            for browser_attempt in range(max_browser_retries):
                # 每次重试重新配置 Chrome Options
                chrome_options = Options()
                
                # 优化策略：设为 eager，只要 DOM 加载完就不再等第三方图片和脚本
                chrome_options.page_load_strategy = 'eager'
                
                chrome_options.add_argument("--headless=new")
                chrome_options.add_argument("--no-sandbox")
                chrome_options.add_argument("--disable-dev-shm-usage")
                chrome_options.add_argument("--disable-gpu")
                chrome_options.add_argument("--disable-software-rasterizer") # 禁用软件光栅化
                chrome_options.add_argument("--window-size=1920,1080")
                chrome_options.add_argument(f"--user-data-dir={user_data_dir}")
                chrome_options.add_argument("--disable-blink-features=AutomationControlled")
                chrome_options.add_argument("--blink-settings=imagesEnabled=false")  # 禁用图像加载
                chrome_options.add_argument("--ignore-certificate-errors")
                chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
                chrome_options.add_experimental_option('useAutomationExtension', False)
                chrome_options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})
                
                log(f"账号 {account_index} - 正在启动浏览器 (尝试 {browser_attempt + 1}/{max_browser_retries})...")
                current_proxies = None
                
                try:
                    driver = webdriver.Chrome(options=chrome_options)
                    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
                    
                    # 注入请求拦截器，在第一时间捕获前端生成的有效 secretkey
                    intercept_js = """
                    if (!window._jlc_sk_intercepted) {
                        window._jlc_sk_intercepted = true;
                        const origFetch = window.fetch;
                        window.fetch = async function(...args) {
                            try {
                                if (args[1] && args[1].headers) {
                                    let h = args[1].headers;
                                    let sk = null;
                                    if (typeof h.get === 'function') {
                                        sk = h.get('secretkey') || h.get('SecretKey') || h.get('secretKey');
                                    } else {
                                        sk = h['secretkey'] || h['SecretKey'] || h['secretKey'];
                                    }
                                    if (sk) window._my_secretkey = sk;
                                }
                            } catch(e) {}
                            return origFetch.apply(this, args);
                        };
                        
                        const origSetHeader = XMLHttpRequest.prototype.setRequestHeader;
                        XMLHttpRequest.prototype.setRequestHeader = function(key, val) {
                            if (key && key.toLowerCase() === 'secretkey') {
                                window._my_secretkey = val;
                            }
                            return origSetHeader.apply(this, arguments);
                        };
                    }
                    """
                    driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {'source': intercept_js})
                    
                    driver.set_page_load_timeout(15)
                    
                    driver.get("https://m.jlc.com/")
                    WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
                    
                    # 等待页面加载和后台API网络请求初始化完成
                    time.sleep(4) 
                    
                    browser_success = True
                    break
                    
                except Exception as e:
                    # 发生异常立即关闭失效的 Driver
                    if driver:
                        try:
                            driver.quit()
                        except Exception:
                            pass
                        driver = None
                        
                    error_str = str(e).replace('\n', ' ')[:100]
                    log(f"账号 {account_index} - ❌ 浏览器加载页面失败: {error_str} (尝试 {browser_attempt + 1}/{max_browser_retries})")
                    
                    # 清理旧的 user_data_dir 防止下一次启动被锁定抛出异常
                    if user_data_dir and os.path.exists(user_data_dir):
                        try:
                            shutil.rmtree(user_data_dir, ignore_errors=True)
                            user_data_dir = tempfile.mkdtemp()
                        except Exception:
                            pass
                            
                    time.sleep(2)
            
            if not browser_success:
                log(f"账号 {account_index} - ❌ 连续 {max_browser_retries} 次尝试启动浏览器并加载页面均失败，进入外层兜底重试。")
                return result
            
            # 使用已获取的 authCode 进行 JLC 登录
            log(f"账号 {account_index} - 正在使用 authCode 登录 m.jlc.com...")
            
            # 提取页面的 secretkey
            pre_secretkey = extract_secretkey_from_browser(driver)
            fallback_secret = str(uuid.uuid4()).encode('utf-8').hex()
            use_secret = pre_secretkey if pre_secretkey else fallback_secret
            
            if not pre_secretkey:
                log(f"账号 {account_index} - 已自动生成模拟密钥: {use_secret[:20]}...")
            
            # 使用 Python requests 底层引擎来完全穿透 WAF 的 404 IP拦截
            login_success = False
            
            sel_cookies = driver.get_cookies()
            session_cookies = {c['name']: c['value'] for c in sel_cookies}
            xsrf_token = urllib.parse.unquote(session_cookies.get('XSRF-TOKEN', ''))
            
            api_url = "https://m.jlc.com/api/login/login-by-code"
            req_headers = {
                'Accept': 'application/json, text/plain, */*',
                'X-JLC-ClientType': 'WEB',
                'X-JLC-AccessToken': 'NONE',
                'Origin': 'https://m.jlc.com',
                'Referer': 'https://m.jlc.com/pages/my/index',
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'secretkey': use_secret
            }
            
            if xsrf_token:
                req_headers['x-xsrf-token'] = xsrf_token
            
            # 为了防范机房 IP 触发 WAF 404，如果配置了代理，此处获取并使用代理
            if not disable_global_proxy and current_proxies is None:
                current_proxies = get_valid_proxy(account_index)
            
            is_actually_using_proxy = not disable_global_proxy and current_proxies is not None
            max_login_retries = 20 if is_actually_using_proxy else 1
            network_error_exhausted = False
            
            for login_attempt in range(max_login_retries):
                try:
                    log(f"账号 {account_index} - 正在发起登录请求 (代理: {'启用' if current_proxies else '未启用'}) (尝试 {login_attempt + 1}/{max_login_retries})...")
                    
                    # 按照抓包格式，优先使用 multipart/form-data 模拟发送
                    m_files = {'code': (None, auth_code)}
                    resp = requests.post(api_url, headers=req_headers, cookies=session_cookies, files=m_files, proxies=current_proxies, timeout=15)
                    
                    # 如果遇到 WAF 拦截或者解析拦截导致的 404，进行 payload 降维打击探测
                    if resp.status_code == 404:
                        log(f"账号 {account_index} - ⚠ multipart表单遇到路由 404，可能被拦截，尝试切换为 JSON 格式...")
                        resp = requests.post(api_url, headers=req_headers, cookies=session_cookies, json={'code': auth_code}, proxies=current_proxies, timeout=15)
                        
                    if resp.status_code == 404:
                        log(f"账号 {account_index} - ⚠ JSON格式仍遇 404，尝试切换为 URL Encoded 格式...")
                        resp = requests.post(api_url, headers=req_headers, cookies=session_cookies, data={'code': auth_code}, proxies=current_proxies, timeout=15)
                        
                    if resp.status_code == 200:
                        if current_proxies:
                            consecutive_proxy_fails = 0
                        try:
                            resp_data = resp.json()
                            if resp_data.get('code') == 200 and resp_data.get('data', {}).get('accessToken'):
                                access_token = resp_data['data']['accessToken']
                                
                                # 同步会话Cookie并注入Token
                                session_cookies.update(resp.cookies.get_dict())
                                for c_name, c_value in resp.cookies.get_dict().items():
                                    try:
                                        driver.add_cookie({'name': c_name, 'value': c_value, 'domain': '.jlc.com', 'path': '/'})
                                    except:
                                        pass
                                        
                                driver.execute_script(f"window.localStorage.setItem('X-JLC-AccessToken', '{access_token}');")
                                login_success = True
                                log(f"账号 {account_index} - ✅ 登录接口请求成功！")
                            else:
                                log(f"账号 {account_index} - ❌ 接口请求通过，但业务返回异常: {resp.text}")
                        except Exception:
                            log(f"账号 {account_index} - ❌ 请求成功，但解析异常非JSON结构: {resp.text}")
                        break  # 请求成功或遇到业务错误，直接跳出重试循环
                    else:
                        log(f"账号 {account_index} - ❌ 底层登录接口响应失败，HTTP 状态码: {resp.status_code}, 返回: {resp.text}")
                        break  # 接口响应报错非网络错误，同样跳出不再重试
                        
                except requests.exceptions.RequestException as e:
                    if is_actually_using_proxy and current_proxies:
                        if isinstance(e, requests.exceptions.ProxyError):
                            error_type = "代理拒绝连接/代理错误"
                        elif isinstance(e, requests.exceptions.ConnectTimeout):
                            error_type = "连接代理超时"
                        elif isinstance(e, requests.exceptions.ReadTimeout):
                            error_type = "代理响应超时"
                        elif isinstance(e, requests.exceptions.Timeout):
                            error_type = "请求超时"
                        elif isinstance(e, requests.exceptions.ConnectionError):
                            error_type = "连接错误"
                        else:
                            error_type = "未知请求异常"
                            
                        log(f"账号 {account_index} - ⚠ 底层登录请求网络异常 ({error_type}: {e})，准备重新获取代理...")
                        current_proxies = get_valid_proxy(account_index)
                        if not current_proxies:
                            network_error_exhausted = True
                            break
                        
                        # 标记最后一次如果还是异常就说明耗尽了次数
                        if login_attempt == max_login_retries - 1:
                            network_error_exhausted = True
                    else:
                        log(f"账号 {account_index} - ❌ 底层登录请求发生网络异常: {e}")
                        break
                except Exception as e:
                    log(f"账号 {account_index} - ❌ 底层登录请求发生未知异常: {e}")
                    break
                    
            if network_error_exhausted and is_actually_using_proxy and not disable_global_proxy:
                log(f"账号 {account_index} - ❌ 连续 {max_login_retries} 次底层登录代理请求网络异常或失败")
                consecutive_proxy_fails += 1
                if consecutive_proxy_fails >= 5:
                    disable_global_proxy = True
                    log("⚠ 连续5次代理获取/使用失败，接下来的账号全部放弃使用代理！")
            
            if login_success:
                result['jlc_login_success'] = True  # 标记金豆查询的JLC登录成功
                
                # 重新刷新让前端以已登录态发起真实的秘钥协商
                navigate_and_interact_m_jlc(driver, account_index)
                
                access_token = extract_token_from_local_storage(driver)
                secretkey = extract_secretkey_from_browser(driver)
                
                result['token_extracted'] = bool(access_token)
                result['secretkey_extracted'] = bool(secretkey)
                
                if access_token and secretkey:
                    log(f"账号 {account_index} - ✅ 成功提取 token 和 secretkey")
                    
                    jlc_client = JLCClient(access_token, secretkey, account_index, driver, current_proxies, cookies=session_cookies)
                    query_success = jlc_client.execute_query_process()
                    
                    # 记录金豆查询结果
                    result['query_success'] = query_success
                    result['jindou_count'] = jlc_client.jindou_count
                    
                    if query_success:
                        log(f"账号 {account_index} - ✅ 金豆查询流程完成")
                    else:
                        log(f"账号 {account_index} - ❌ 金豆查询流程失败")
                else:
                    log(f"账号 {account_index} - ❌ 无法提取到 token 或 secretkey，跳过金豆查询")
            else:
                log(f"账号 {account_index} - ❌ m.jlc.com 登录接口返回失败")

    except Exception as e:
        log(f"账号 {account_index} - ❌ 程序执行错误: {e}")
    finally:
        # 安全退出 Driver
        if driver:
            try:
                driver.quit()
                log(f"账号 {account_index} - 浏览器已关闭")
            except Exception:
                pass
        
        # 清理临时目录
        if user_data_dir and os.path.exists(user_data_dir):
            try:
                shutil.rmtree(user_data_dir, ignore_errors=True)
            except Exception:
                pass
    
    return result

def should_retry(merged_success, password_error):
    """判断是否需要重试：如果金豆查询未成功，且不是密码错误"""
    global skip_query
    query_needs_retry = not merged_success['query'] and not skip_query
    need_retry = query_needs_retry and not password_error
    return need_retry

def process_single_account(username, password, account_index, total_accounts):
    """处理单个账号，包含重试机制，并合并多次尝试的最佳结果"""
    max_retries = 3  # 最多重试3次
    merged_result = {
        'account_index': account_index,
        'query_success': False,
        'jindou_count': 0,
        'token_extracted': False,
        'secretkey_extracted': False,
        'retry_count': 0,  # 记录最后使用的retry_count
        'password_error': False,  # 标记密码错误
        'actual_password': None,  # 实际使用的密码
        'backup_index': -1,  # 使用的备用密码索引，-1表示原密码
        'critical_error': False,   # 标记严重错误
        'jlc_login_success': False
    }
    
    merged_success = {'query': False}

    for attempt in range(max_retries + 1):  # 第一次执行 + 重试次数
        try:
            result = query_account(username, password, account_index, total_accounts, retry_count=attempt)
        except Exception as e:
            log(f"账号 {account_index} - ⚠ 发生未捕获异常，将进行重试: {e}")
            result = merged_result.copy()
        
        # 如果检测到密码错误，立即停止重试
        if result.get('password_error'):
            merged_result['password_error'] = True
            # 停止后续尝试
            break
        
        # 如果检测到严重错误（如多次调用登录依赖失败），立即停止重试，处理下一个账号
        if result.get('critical_error'):
            merged_result['critical_error'] = True
            break

        # 合并结果
        if result.get('jlc_login_success'):
            merged_result['jlc_login_success'] = True
            
        if result.get('actual_password') is not None and merged_result.get('actual_password') is None:
            merged_result['actual_password'] = result['actual_password']
            merged_result['backup_index'] = result['backup_index']
        
        # 合并金豆结果：如果本次成功且之前未成功，则更新
        if result['query_success'] and not merged_success['query']:
            merged_success['query'] = True
            merged_result['jindou_count'] = result['jindou_count']
        
        # 即使查询失败，也保留已获取到的金豆数据（用于Excel显示）
        if not merged_success['query']:
            # 取最大的金豆值（优先保留有数据的结果）
            if result['jindou_count'] > merged_result['jindou_count']:
                merged_result['jindou_count'] = result['jindou_count']
        
        # 更新其他字段（如果之前未知）
        if not merged_result['token_extracted'] and result['token_extracted']:
            merged_result['token_extracted'] = result['token_extracted']
        
        if not merged_result['secretkey_extracted'] and result['secretkey_extracted']:
            merged_result['secretkey_extracted'] = result['secretkey_extracted']
        
        # 更新retry_count为最后一次尝试的
        merged_result['retry_count'] = result['retry_count']

        # 检查是否还需要重试（排除密码错误的情况）
        if not should_retry(merged_success, merged_result['password_error']) or attempt >= max_retries:
            break
        else:
            log(f"账号 {account_index} - 🔄 准备第 {attempt + 1} 次重试，等待 {random.randint(2, 6)} 秒后重新开始...")
            time.sleep(random.randint(2, 6))
    
    # 最终设置success字段基于合并
    merged_result['query_success'] = merged_success['query']

    # ---------------- 连续失败跳过逻辑 ----------------
    global consecutive_query_fails, skip_query

    # 检查金豆查询连续失败 (确保已经通过了金豆平台的JLC登录)
    if not skip_query and merged_result['jlc_login_success']:
        if not merged_result['query_success']:
            consecutive_query_fails += 1
            if consecutive_query_fails >= 50:
                skip_query = True
                log("⚠ 连续50个账号金豆查询失败，接下来的账号跳过金豆查询流程！")
        else:
            consecutive_query_fails = 0
    # ------------------------------------------------
    
    return merged_result

# 推送函数
def push_summary():
    if not summary_logs:
        return
    
    title = "嘉立创金豆查询总结"
    text = "\n".join(summary_logs)
    full_text = f"{title}\n{text}"  # 有些平台不需要单独标题
    
    # Telegram
    telegram_bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
    telegram_chat_id = os.getenv('TELEGRAM_CHAT_ID')
    if telegram_bot_token and telegram_chat_id:
        try:
            url = f"https://api.telegram.org/bot{telegram_bot_token}/sendMessage"
            params = {'chat_id': telegram_chat_id, 'text': full_text}
            response = requests.get(url, params=params)
            if response.status_code == 200:
                log("Telegram-日志已推送")
            else:
                log(f"Telegram-推送失败: {response.text}")
        except Exception as e:
            log(f"Telegram-推送异常: {e}")

    # 企业微信 (WeChat Work)
    wechat_webhook_key = os.getenv('WECHAT_WEBHOOK_KEY')
    if wechat_webhook_key:
        try:
            if wechat_webhook_key.startswith('https://'):
                url = wechat_webhook_key
            else:
                url = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={wechat_webhook_key}"
            body = {"msgtype": "text", "text": {"content": full_text}}
            response = requests.post(url, json=body)
            # 检查状态码
            if response.status_code != 200:
                log(f"企业微信-推送失败 (HTTP {response.status_code}): {response.text}")
            else:
                # 解析 JSON
                try:
                    resp_json = response.json()
                    errcode = resp_json.get('errcode')
                    if errcode == 0:
                        log("企业微信-日志已推送")
                    else:
                        errmsg = resp_json.get('errmsg', '未知错误')
                        log(f"企业微信-推送失败 (errcode={errcode}, errmsg={errmsg})")
                except Exception as e:
                    log(f"企业微信-推送响应解析失败: {e}, 原始响应: {response.text}")
        except Exception as e:
            log(f"企业微信-推送异常: {e}")

    # 钉钉 (DingTalk)
    dingtalk_webhook = os.getenv('DINGTALK_WEBHOOK')
    if dingtalk_webhook:
        try:
            if dingtalk_webhook.startswith('https://'):
                url = dingtalk_webhook
            else:
                url = f"https://oapi.dingtalk.com/robot/send?access_token={dingtalk_webhook}"
            body = {"msgtype": "text", "text": {"content": full_text}}
            response = requests.post(url, json=body)
            if response.status_code != 200:
                log(f"钉钉-推送失败 (HTTP {response.status_code}): {response.text}")
            else:
                try:
                    resp_json = response.json()
                    errcode = resp_json.get('errcode')
                    if errcode == 0:
                        log("钉钉-日志已推送")
                    else:
                        errmsg = resp_json.get('errmsg', '未知错误')
                        log(f"钉钉-推送失败 (errcode={errcode}, errmsg={errmsg})")
                except Exception as e:
                    log(f"钉钉-推送响应解析失败: {e}, 原始响应: {response.text}")
        except Exception as e:
            log(f"钉钉-推送异常: {e}")

    # PushPlus
    pushplus_token = os.getenv('PUSHPLUS_TOKEN')
    if pushplus_token:
        try:
            url = "http://www.pushplus.plus/send"
            body = {"token": pushplus_token, "title": title, "content": text}
            response = requests.post(url, json=body)
            if response.status_code == 200:
                log("PushPlus-日志已推送")
            else:
                log(f"PushPlus-推送失败: {response.text}")
        except Exception as e:
            log(f"PushPlus-推送异常: {e}")

    # Server酱
    serverchan_sckey = os.getenv('SERVERCHAN_SCKEY')
    if serverchan_sckey:
        try:
            url = f"https://sctapi.ftqq.com/{serverchan_sckey}.send"
            body = {"title": title, "desp": text}
            response = requests.post(url, data=body)
            if response.status_code == 200:
                log("Server酱-日志已推送")
            else:
                log(f"Server酱-推送失败: {response.text}")
        except Exception as e:
            log(f"Server酱-推送异常: {e}")

    # Server酱3
    serverchan3_sckey = os.getenv('SERVERCHAN3_SCKEY') 
    if serverchan3_sckey:
        try:
            textSC3 = "\n\n".join(summary_logs)
            titleSC3 = title
            options = {"tags": "嘉立创|查询"}  # 可选参数，根据需求添加
            response = sc_send(serverchan3_sckey, titleSC3, textSC3, options)            
            if response.get("code") == 0:  # 新版成功返回 code=0
                log("Server酱3-日志已推送")
            else:
                log(f"Server酱3-推送失败: {response}")                
        except Exception as e:
            log(f"Server酱3-推送异常: {str(e)}")    

    # 酷推 (CoolPush)
    coolpush_skey = os.getenv('COOLPUSH_SKEY')
    if coolpush_skey:
        try:
            url = f"https://push.xuthus.cc/send/{coolpush_skey}?c={full_text}"
            response = requests.get(url)
            if response.status_code == 200:
                log("酷推-日志已推送")
            else:
                log(f"酷推-推送失败: {response.text}")
        except Exception as e:
            log(f"酷推-推送异常: {e}")

    # 自定义API
    custom_webhook = os.getenv('CUSTOM_WEBHOOK')
    if custom_webhook:
        try:
            body = {"title": title, "content": text}
            response = requests.post(custom_webhook, json=body)
            if response.status_code == 200:
                log("自定义API-日志已推送")
            else:
                log(f"自定义API-推送失败: {response.text}")
        except Exception as e:
            log(f"自定义API-推送异常: {e}")

def calculate_year_end_prediction(current_beans):
    """计算年底金豆预测数量"""
    try:
        now = datetime.now()
        year_end = datetime(now.year, 12, 31)
        # 计算剩余天数（从明天开始算）
        remaining_days = (year_end - now).days
        if remaining_days < 0:
            remaining_days = 0
            
        # 按照一周大约22个金豆计算
        # 每天平均约 22/7 个
        estimated_future_beans = int(remaining_days * (22 / 7))
        return current_beans + estimated_future_beans
    except Exception:
        return current_beans

def main():
    global in_summary
    
    if len(sys.argv) < 3:
        print("用法: python jlc.py 账号1,账号2,账号3... 密码1,密码2,密码3...[失败退出标志][账号组编号]")
        print("示例: python jlc.py user1,user2,user3 pwd1,pwd2,pwd3")
        print("示例: python jlc.py user1,user2,user3 pwd1,pwd2,pwd3 true")
        print("示例: python jlc.py user1,user2,user3 pwd1,pwd2,pwd3 true 4")
        print("失败退出标志: 不传或任意值-关闭, true-开启(任意账号查询失败时返回非零退出码)")
        print("账号组编号: 只能输入数字，输入其他值则忽略")
        sys.exit(1)
    
    usernames =[u.strip() for u in sys.argv[1].split(',') if u.strip()]
    passwords =[p.strip() for p in sys.argv[2].split(',') if p.strip()]
    
    # 解析失败退出标志，默认为关闭
    enable_failure_exit = False
    if len(sys.argv) >= 4:
        enable_failure_exit = (sys.argv[3].lower() == 'true')
    
    # 解析第4个参数（账号组编号），只接受纯数字，其他值忽略
    account_group = None
    if len(sys.argv) >= 5:
        if sys.argv[4].isdigit():
            account_group = sys.argv[4]
    
    log(f"失败退出功能: {'开启' if enable_failure_exit else '关闭'}")
    
    if len(usernames) != len(passwords):
        log("❌ 错误: 账号和密码数量不匹配!")
        sys.exit(1)
    
    total_accounts = len(usernames)
    
    # --- 前置预检代理白名单 ---
    ensure_proxy_whitelist()
    # -----------------------

    log(f"开始处理 {total_accounts} 个账号的查询任务")
    
    # 存储所有账号的结果
    all_results =[]
    
    for i, (username, password) in enumerate(zip(usernames, passwords), 1):
        log(f"开始处理第 {i} 个账号")
        result = process_single_account(username, password, i, total_accounts)
        all_results.append(result)
        
        if i < total_accounts:
            wait_time = random.randint(5, 10)  # 查询之间随机延迟5-10秒
            log(f"等待 {wait_time} 秒后处理下一个账号...")
            time.sleep(wait_time)
    
    # 输出详细总结
    log("=" * 70)
    log("📊 详细查询任务完成总结")
    log("=" * 70)
    
    query_success_count = 0
    retried_accounts =[]  # 合并所有重试过的账号
    password_error_accounts =[]  # 密码错误的账号
    
    # 记录失败的账号
    failed_accounts =[]
    
    for result in all_results:
        account_index = result['account_index']
        retry_count = result.get('retry_count', 0)
        password_error = result.get('password_error', False)
        
        if password_error:
            password_error_accounts.append(account_index)
        
        if retry_count > 0:
            retried_accounts.append(account_index)
        
        # 检查是否有失败情况（排除密码错误）
        if not result['query_success'] and not password_error:
            failed_accounts.append(account_index)
        
        retry_label = ""
        if retry_count > 0:
             retry_label = f"[重试{retry_count}次]"
        
        # 密码错误账号的特殊显示
        if password_error:
            log(f"账号 {account_index} 详细结果:[密码错误]")
            log("  └── 状态: ❌ 账号或密码错误，跳过此账号")
        else:
            log(f"账号 {account_index} 详细结果:{retry_label}")
            
            # 显示金豆数量
            current_jindou = result['jindou_count']
            if current_jindou > 0:
                log(f"  ├── 当前金豆: {current_jindou}")
            else:
                log(f"  ├── 金豆状态: 无法获取金豆信息")
            
            # 预测年底金豆
            if current_jindou > 0:
                predicted_beans = calculate_year_end_prediction(current_jindou)
                log(f"  ├── 预计年底: ≈{predicted_beans} 金豆 (按周均22个预测)")
            
            if result['query_success']:
                query_success_count += 1
        
        log("  " + "-" * 50)
    
    # 总体统计
    in_summary = True  # 启用总结收集（推送内容从此处开始）
    if account_group is not None:
        log(f"📈账号组{account_group} 嘉立创查询总体统计:")
    else:
        log("📈 嘉立创查询总体统计:")
    log(f"  ├── 总账号数: {total_accounts}")
    log(f"  ├── 金豆查询成功: {query_success_count}/{total_accounts}")
    
    # 计算成功率
    query_rate = (query_success_count / total_accounts) * 100 if total_accounts > 0 else 0
    
    log(f"  └── 金豆查询成功率: {query_rate:.1f}%")
    
    # 失败账号列表（排除密码错误）
    failed_query = [r['account_index'] for r in all_results if not r['query_success'] and not r.get('password_error', False)]
    
    if failed_query:
        log(f"  ⚠ 金豆查询失败账号: {', '.join(map(str, failed_query))}")
        
    if password_error_accounts:
        log(f"  ⚠密码错误的账号: {', '.join(map(str, password_error_accounts))}")
       
    if not failed_query and not password_error_accounts:
        log("  🎉 所有账号全部查询成功!")
    elif password_error_accounts and not failed_query:
        log("  ⚠除了密码错误账号，其他账号全部查询成功!")
    
    log("=" * 70)

    # 推送总结 - 只有在有失败时推送（包括密码错误）
    all_failed_accounts = failed_accounts + password_error_accounts
    if all_failed_accounts:
        push_summary()
    
    # 生成 password-changed.txt
    changed_accounts =[result for result in all_results if result.get('backup_index', -1) >= 0 and not result.get('password_error', False) and result['actual_password'] is not None]
    if changed_accounts:
        with open('password-changed.txt', 'w', encoding='utf-8') as f:
            for result in changed_accounts:
                username = usernames[result['account_index'] - 1]
                f.write(f"{username}:{result['actual_password']}\n")
            f.write("\n")
        log("✅ 已生成 password-changed.txt 文件")
    else:
        log("✅ 没有使用非原密码的账号，无需生成 password-changed.txt")
    
    # 保存结果到JSON文件，供汇总脚本使用
    try:
        result_data = {
            'group_index': int(account_group) if account_group else 0,
            'accounts':[]
        }
        
        for i, result in enumerate(all_results):
            username = usernames[result['account_index'] - 1]
            account_data = {
                'account_index': result['account_index'],
                'username': username,
                'jindou': result['jindou_count'],
                'query_success': result['query_success'],
                'password_error': result.get('password_error', False),
                'actual_password': result.get('actual_password')
            }
            result_data['accounts'].append(account_data)
        
        # 使用账号组编号作为文件名的一部分
        group_num = int(account_group) if account_group else 0
        result_filename = f'jlc_result_{group_num}.json'
        
        with open(result_filename, 'w', encoding='utf-8') as f:
            json.dump(result_data, f, ensure_ascii=False, indent=2)
        
        log(f"✅ 已生成结果文件: {result_filename}")
    except Exception as e:
        log(f"⚠ 保存结果文件失败: {e}")
    
    # 根据失败退出标志决定退出码
    all_failed_accounts = failed_accounts + password_error_accounts
    if enable_failure_exit and all_failed_accounts:
        log(f"❌ 检测到失败的账号: {', '.join(map(str, all_failed_accounts))}")
        if password_error_accounts:
            log(f"❌ 其中密码错误的账号: {', '.join(map(str, password_error_accounts))}")
        log("❌ 由于失败退出功能已开启，返回报错退出码以获得邮件提醒")
        sys.exit(1)
    else:
        if enable_failure_exit:
            log("✅ 所有账号查询成功，程序正常退出")
        else:
            log("✅ 程序正常退出")
        sys.exit(0)

if __name__ == "__main__":
    main()
