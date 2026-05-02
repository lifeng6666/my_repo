import os
import sys
import time
import json
import tempfile
import subprocess
import re
import shutil
import threading
import queue
from datetime import datetime
from urllib.parse import urlparse
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException, NoSuchElementException

# 导入SM2加密方法
try:
    from Utils import pwdEncrypt
    print("✅ 成功加载 SM2 加密依赖")
except ImportError:
    print("❌ 错误: 未找到 Utils.py ，请确保同目录下存在该文件")
    sys.exit(1)


def log(msg, show_time=True):
    """带时间戳的日志输出"""
    if show_time:
        full_msg = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    else:
        full_msg = msg
    print(full_msg, flush=True)


# =====================================================================
#  通用券判定逻辑
# =====================================================================
def check_available_general_coupons(response_data):
    """
    解析嘉立创优惠券响应数据，判断是否有任意工艺的“通用券”可供领取。
    """
    if isinstance(response_data, str):
        try:
            data = json.loads(response_data)
        except json.JSONDecodeError:
            print("JSON 数据格式解析错误")
            return False, []
    else:
        data = response_data
        
    coupons = data.get("body", {}).get("coupons", [])
    available_coupons = []
    
    for coupon in coupons:
        name = coupon.get("name", "")
        receive_flag = coupon.get("receiveFlag", False)
        
        # 核心判断逻辑：名称包含“通用券” 且 后端下发状态为可领取
        if "通用券" in name and receive_flag:
            available_coupons.append(name)
            
    if available_coupons:
        return True, available_coupons
    else:
        return False, []


# =====================================================================
#  浏览器创建 & 底层网络拦截
# =====================================================================

def create_chrome_driver(user_data_dir=None):
    """创建Chrome浏览器实例"""
    chrome_options = Options()

    # --- 防检测核心配置 ---
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option('useAutomationExtension', False)

    # --- 稳定性配置 ---
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--disable-software-rasterizer")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--window-size=1920,1080")

    # --- 启用性能日志，用于捕获网络请求头中的 Secretkey 等 ---
    chrome_options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})

    if user_data_dir:
        chrome_options.add_argument(f"--user-data-dir={user_data_dir}")

    driver = webdriver.Chrome(options=chrome_options)
    driver.set_page_load_timeout(60)
    driver.set_script_timeout(60)

    # --- CDP 命令防检测 ---
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": """
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """
    })
    return driver


def clear_performance_logs(driver):
    """清空已有的性能日志"""
    try:
        driver.get_log('performance')
    except:
        pass


def extract_custom_headers_from_logs(driver, header_keys):
    """从浏览器性能日志中提取指定的请求标头"""
    found_headers = {}
    keys_lower = [k.lower() for k in header_keys]
    try:
        logs = driver.get_log('performance')
        for entry in logs:
            try:
                log_entry = json.loads(entry['message'])
                message = log_entry.get('message', {})
                method = message.get('method', '')
                params = message.get('params', {})

                headers = {}
                if method == 'Network.requestWillBeSent':
                    headers = params.get('request', {}).get('headers', {})
                elif method == 'Network.requestWillBeSentExtraInfo':
                    headers = params.get('headers', {})

                for key, value in headers.items():
                    if key.lower() in keys_lower and value:
                        found_headers[key.lower()] = value
            except:
                continue
    except Exception as e:
        log(f"⚠ 读取性能日志异常: {e}")
    return found_headers


def send_post_request(driver, url, body_dict, extra_headers=None):
    """通过浏览器上下文发送 application/json 的 POST 请求"""
    if extra_headers is None:
        extra_headers = {}
        
    headers_json = json.dumps(extra_headers)
    body_str = json.dumps(body_dict, ensure_ascii=False) if body_dict is not None else "{}"

    js_code = """
    var url = arguments[0];
    var bodyData = arguments[1];
    var extraHeaders = JSON.parse(arguments[2]);
    var callback = arguments[3];

    var headersObj = {
        'Content-Type': 'application/json',
        'Accept': 'application/json, text/plain, */*'
    };

    // 从 Cookie 中读取 XSRF-TOKEN 并附加到请求头
    var xsrfToken = '';
    var cookies = document.cookie.split(';');
    for (var i = 0; i < cookies.length; i++) {
        var cookie = cookies[i].trim();
        if (cookie.indexOf('XSRF-TOKEN=') === 0) {
            xsrfToken = decodeURIComponent(cookie.substring('XSRF-TOKEN='.length));
            break;
        }
    }
    if (xsrfToken) {
        headersObj['x-xsrf-token'] = xsrfToken;
    }

    // 覆盖/合并传入的额外 headers (如 secretkey, x-jlc-accesstoken)
    for (var key in extraHeaders) {
        headersObj[key] = extraHeaders[key];
    }

    fetch(url, {
        method: 'POST',
        headers: headersObj,
        body: bodyData,
        credentials: 'include'
    }).then(function(response) {
        return response.text();
    }).then(function(data) {
        callback(data);
    }).catch(function(error) {
        callback(JSON.stringify({"_fetch_error": error.toString()}));
    });
    """
    
    try:
        result = driver.execute_async_script(js_code, url, body_str, headers_json)
        if result:
            try:
                return json.loads(result)
            except json.JSONDecodeError:
                return result # 如果不是标准JSON，返回原始字符串
        return None
    except Exception as e:
        log(f"❌ 发送请求失败: {e}")
        return None


# =====================================================================
#  登录流程
# =====================================================================

def call_aliv3min_with_timeout(timeout_seconds=180, max_retries=18):
    """调用 AliV3-login.py 获取 captchaTicket"""
    for attempt in range(max_retries):
        log(f"📞 正在调用 登录脚本 获取 captchaTicket (尝试 {attempt + 1}/{max_retries})...")
        process = None
        output_lines = []

        try:
            if not os.path.exists('AliV3-login.py'):
                log("❌ 错误: 找不到登录依赖 AliV3-login.py")
                sys.exit(1)

            process = subprocess.Popen(
                [sys.executable, 'AliV3-login.py'],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding='utf-8', errors='ignore'
            )

            q = queue.Queue()
            def enqueue_output(out, queue_obj):
                try:
                    for line in iter(out.readline, ''): queue_obj.put(line)
                except: pass
                finally:
                    try: out.close()
                    except: pass

            t = threading.Thread(target=enqueue_output, args=(process.stdout, q))
            t.daemon = True
            t.start()

            start_time = time.time()
            captcha_ticket = None
            wait_for_next_line = False

            while True:
                elapsed = time.time() - start_time
                if elapsed > timeout_seconds:
                    log(f"⏰ 登录脚本超过 {timeout_seconds} 秒未完成，强制终止...")
                    try:
                        process.kill()
                        process.wait(timeout=5)
                    except: pass
                    break

                try:
                    line = q.get(timeout=0.5)
                except queue.Empty:
                    if process.poll() is not None and not t.is_alive(): break
                    continue

                if line:
                    output_lines.append(line)
                    if wait_for_next_line:
                        captcha_ticket = line.strip()
                        log(f"✅ 成功获取 captchaTicket")
                        try:
                            process.terminate()
                            process.wait(timeout=5)
                        except: pass
                        return captcha_ticket

                    if "SUCCESS: Obtained CaptchaTicket:" in line:
                        wait_for_next_line = True
                        continue

                    if "captchaTicket" in line:
                        try:
                            match = re.search(r'"captchaTicket"\s*:\s*"([^"]+)"', line)
                            if match:
                                captcha_ticket = match.group(1)
                                log(f"✅ 成功获取 captchaTicket")
                                try:
                                    process.terminate()
                                    process.wait(timeout=5)
                                except: pass
                                return captcha_ticket
                        except: pass

            if not captcha_ticket:
                if process and process.poll() is None:
                    try:
                        process.kill()
                        process.wait(timeout=5)
                    except: pass
                if attempt < max_retries - 1:
                    time.sleep(5)
            else:
                return captcha_ticket

        except Exception as e:
            log(f"❌ 调用登录脚本异常: {e}")
            if process and process.poll() is None:
                try:
                    process.kill()
                    process.wait(timeout=5)
                except: pass
            if attempt < max_retries - 1:
                time.sleep(5)

    log("❌ 登录脚本存在异常")
    sys.exit(1)


def perform_init_session(driver, max_retries=3):
    """执行 Session 初始化"""
    for i in range(max_retries):
        log(f"📡 初始化会话 (尝试 {i + 1}/{max_retries})...")
        response = send_post_request(driver, "https://passport.jlc.com/api/cas/login/get-init-session", {"appId": "JLC_PORTAL_PC", "clientType": "PC-WEB"})
        if response and isinstance(response, dict) and response.get('success') == True and response.get('code') == 200:
            log("✅ 初始化会话成功")
            return True
        else:
            if i < max_retries - 1: time.sleep(2)
    return False


def login_with_password(driver, username, password, captcha_ticket):
    """登录发包"""
    url = "https://passport.jlc.com/api/cas/login/with-password"
    try:
        encrypted_username = pwdEncrypt(username)
        encrypted_password = pwdEncrypt(password)
    except Exception as e:
        log(f"❌ SM2加密失败: {e}")
        return 'other_error', None

    body = {'username': encrypted_username, 'password': encrypted_password, 'isAutoLogin': False, 'captchaTicket': captcha_ticket}
    log(f"📡 发送登录请求...")
    response = send_post_request(driver, url, body)
    
    if not response or not isinstance(response, dict): return 'other_error', response
    if response.get('success') == True and response.get('code') == 2017: return 'success', response
    if response.get('code') == 10208: return 'password_error', response
    return 'other_error', response


def verify_login_on_member_page(driver, max_retries=3):
    """验证登录"""
    for attempt in range(max_retries):
        log(f"🔍 验证登录状态 ({attempt + 1}/{max_retries})...")
        try:
            try: driver.get("https://member.jlc.com/")
            except TimeoutException:
                driver.execute_script("window.stop();")

            WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            time.sleep(3)
            page_source = driver.page_source
            if "客编" in page_source or "customerCode" in page_source:
                log(f"✅ 验证登录成功")
                return True
        except Exception as e: pass
        if attempt < max_retries - 1: time.sleep(2)
    return False


def perform_login_flow(driver, username, password, max_retries=3):
    """完整的登录主流程"""
    session_fail_count = 0
    for login_attempt in range(max_retries):
        log(f"🔐 开始登录流程 (尝试 {login_attempt + 1}/{max_retries})...")
        try:
            try: driver.get("https://passport.jlc.com")
            except TimeoutException: driver.execute_script("window.stop();")

            WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.TAG_NAME, "body")))

            if not perform_init_session(driver):
                session_fail_count += 1
                if session_fail_count >= 3: raise Exception("初始化 Session 失败")
                raise Exception("初始化 Session 失败")

            session_fail_count = 0

            captcha_ticket = call_aliv3min_with_timeout()
            if not captcha_ticket: raise Exception("获取 CaptchaTicket 失败")

            status, login_res = login_with_password(driver, username, password, captcha_ticket)
            if status == 'password_error': return 'password_error'
            if status != 'success': raise Exception("登录失败")

            if not verify_login_on_member_page(driver): raise Exception("登录验证失败")

            log("✅ 登录流程完成")
            return 'success'

        except Exception as e:
            log(f"❌ 登录流程异常: {e}")
            if login_attempt < max_retries - 1: time.sleep(3)
            else: return 'login_failed'
    return 'login_failed'


# =====================================================================
#  主业务流程执行 (步骤 1 ~ 5)
# =====================================================================

def safe_visit_with_sso_wait(driver, url, visited_domains):
    """带域名SSO记录的页面访问：新域名等待10s，旧域名仅等待3s"""
    domain = urlparse(url).hostname
    log(f"🔗 准备页面环境: {url.split('?')[0]}")
    clear_performance_logs(driver)
    
    try:
        driver.get(url)
    except TimeoutException:
        try: driver.execute_script("window.stop();")
        except: pass
        
    if domain not in visited_domains:
        log(f"⏳ 首次访问域名 [{domain}]，等待10秒同步 SSO 状态...")
        time.sleep(10)
        visited_domains.add(domain)
    else:
        time.sleep(3)


def format_date(date_str):
    """格式化时间字符串提取 yyyy.mm.dd"""
    if not date_str: return "未知"
    match = re.search(r'(\d{4})-(\d{2})-(\d{2})', date_str)
    if match: return f"{match.group(1)}.{match.group(2)}.{match.group(3)}"
    return "未知"


def execute_step_1(driver):
    """1.检查pcb券是否触发风控"""
    log("▶ [步骤 1] 检查PCB券风控: 开始执行")
    log("  [-] 正在提取凭证 headers (secretkey)...")
    headers = extract_custom_headers_from_logs(driver, ['secretkey'])
    
    if 'secretkey' in headers:
        log("  [+] 成功提取到 secretkey")
    else:
        log("  [!] 未能从浏览器日志中提取到 secretkey，尝试直接发包")

    url = "https://www.jlc.com/api/newOrder/NewOrderList/v1/validate-receive-coupon-risk-level"
    log(f"  [-] 正在发送 POST 风控检测请求...")
    res = send_post_request(driver, url, {}, headers)
    
    if isinstance(res, dict):
        log(f"  [-] 接口返回状态码: {res.get('code')}")
        if res.get('code') == 200:
            flag = res.get('data', {}).get('authenticateFlag')
            log(f"  [+] 接口响应成功，解析风控状态 authenticateFlag: {flag}")
            return {'success': True, 'risk': flag, 'raw': res}
        else:
            log(f"  [x] 接口返回状态异常: {str(res)[:200]}")
    else:
        log(f"  [x] 接口返回非JSON数据: {str(res)[:200]}")
    return {'success': False, 'raw': res}


def execute_step_2(driver):
    """2.检查通用券是否可以领取"""
    log("▶ [步骤 2] 检查是否有通用券可领取: 开始执行")
    log("  [-] 正在提取凭证 headers (secretkey)...")
    url = "https://www.jlc.com/api/newOrder/NewOrderList/v1/checkCanReceiveCouponsNew"
    headers = extract_custom_headers_from_logs(driver, ['secretkey'])
    
    log(f"  [-] 正在发送 POST 请求查询通用券状态...")
    res = send_post_request(driver, url, {}, headers)
    
    if isinstance(res, dict):
        log(f"  [-] 接口返回状态码: {res.get('code')}")
        if res.get('code') == 200:
            log("  [+] 请求成功，正在解析响应数据列表...")
            is_avail, names = check_available_general_coupons(res)
            if is_avail:
                log(f"  [+] 匹配成功，发现可领取的通用券: {', '.join(names)}")
            else:
                log("  [-] 当前无可领取的通用券")
            return {'success': True, 'available': is_avail, 'names': names, 'raw': res}
        else:
            log(f"  [x] 接口返回状态异常: {str(res)[:200]}")
    else:
        log(f"  [x] 接口返回非JSON数据: {str(res)[:200]}")
    return {'success': False, 'raw': res}


def execute_step_3(driver):
    """3.每月礼包并记录领到的券"""
    log("▶ [步骤 3] 领取每月礼包: 开始执行")
    url1 = "https://m.jlc.com/api/appPlatform/couponPage/receiveCoupon"
    log("  [-] 正在提取礼包领取凭证 (secretkey, x-jlc-accesstoken, x-jlc-clienttype)...")
    headers = extract_custom_headers_from_logs(driver, ['secretkey', 'x-jlc-accesstoken', 'x-jlc-clienttype'])
    
    log("  [-] 正在发送首个 POST 请求领取礼包...")
    res1 = send_post_request(driver, url1, {"id": 43}, headers)
    if isinstance(res1, dict):
        log(f"  [-] 领奖接口返回状态码: {res1.get('code')}")
        if res1.get('code') == 200 and res1.get('success') == True:
            coupon_ids = res1.get('data', [])
            log(f"  [+] 礼包领取动作成功，获取到券ID列表: {coupon_ids}")
            
            url2 = "https://m.jlc.com/api/cgi/operationService/front/customerCoupon/queryCustomerCouponGroup"
            log("  [-] 正在发送第二个 POST 请求查询对应礼包券详情名称...")
            res2 = send_post_request(driver, url2, {"customerCouponIds": coupon_ids}, headers)
            names = []
            if isinstance(res2, dict):
                log(f"  [-] 查询详情接口返回状态码: {res2.get('code')}")
                if res2.get('code') == 200:
                    for item in res2.get('data', []):
                        name = item.get('couponResponseDto', {}).get('name')
                        if name: names.append(name)
                    log(f"  [+] 券详情解析成功，成功拉取 {len(names)} 张券的名称")
                    return {'success': True, 'status': 'claimed', 'names': names, 'raw': res2}
                else:
                    log(f"  [x] 查询券详情组接口异常: {str(res2)[:200]}")
                    return {'success': False, 'raw': res2}
            else:
                log(f"  [x] 查询详情接口返回非JSON数据: {str(res2)[:200]}")
                return {'success': False, 'raw': res2}
                
        elif res1.get('code') == 1027:
            msg = res1.get('message', '您已领取过优惠券')
            log(f"  [-] 检测到当前礼包当月已被领取过，后端提示: {msg}")
            return {'success': True, 'status': 'already_claimed', 'reason': msg, 'raw': res1}
        else:
            log(f"  [x] 领奖动作接口响应异常: {str(res1)[:200]}")
    else:
        log(f"  [x] 领奖接口返回非JSON数据: {str(res1)[:200]}")
        
    return {'success': False, 'raw': res1}


def execute_step_4(driver):
    """4.查询账号下所有处于可用状态券的有效期"""
    log("▶ [步骤 4] 查询账号下可用券: 开始执行")
    url = "https://member.jlc.com/api/integrated/customerOrderCenter/getEffectiveCouponsList"
    log("  [-] 正在发送 POST 请求拉取账号优惠券列表...")
    res = send_post_request(driver, url, {"sortStatus": None})
    
    valid_coupons = []
    if isinstance(res, dict):
        log(f"  [-] 接口返回状态码: {res.get('code')}")
        if res.get('code') == 200:
            log("  [+] 数据拉取成功，开始解析可用优惠券...")
            
            # 递归遍历整个JSON树，兼容多种券字段名结构
            def find_valid_coupons(data_node, default_end_time="未知"):
                if isinstance(data_node, dict):
                    # 同时校验 couponName 和 name
                    name = data_node.get('couponName') or data_node.get('name')
                    # 同时校验 endDate 和 endTime
                    end_time = data_node.get('endDate') or data_node.get('endTime') or default_end_time
                    
                    if name and 'sortStatus' in data_node:
                        # 严格遵循用户指定的判定条件
                        if str(data_node.get('sortStatus')) == '2':
                            valid_coupons.append(f"{name}(有效期至{format_date(end_time)})")
                    
                    # 向下继承传递时间
                    current_group_end = data_node.get('endDate') or data_node.get('endTime') or default_end_time
                    for k, v in data_node.items():
                        find_valid_coupons(v, current_group_end)
                        
                elif isinstance(data_node, list):
                    for item in data_node:
                        find_valid_coupons(item, default_end_time)

            find_valid_coupons(res)
            log(f"  [+] 查找完毕，当前账号下共提取出 {len(valid_coupons)} 张可用券")
            return {'success': True, 'coupons': valid_coupons, 'raw': res}
        else:
            log(f"  [x] 接口返回状态异常: {str(res)[:200]}")
    else:
        log(f"  [x] 接口返回非JSON数据: {str(res)[:200]}")
        
    return {'success': False, 'raw': res}


def execute_step_5(driver):
    """5.取消该账号所有微信绑定并读取公众号绑定状态"""
    log("▶ [步骤 5] 微信解绑与状态读取: 开始执行")
    url_info = "https://member.jlc.com/api/integrated/wechat/user/info"
    log("  [-] 正在发送 POST 请求获取账号全局绑定关系...")
    res_info = send_post_request(driver, url_info, {})
    
    result = {'success': False, 'cas_unbound_count': 0, 'cas_fail_count': 0, 'cas_total': 0, 
              'oa_bind': False, 'qr_url': None, 'qr_valid': None, 'raw': res_info}
              
    if isinstance(res_info, dict):
        log(f"  [-] 状态查询接口返回状态码: {res_info.get('code')}")
        if res_info.get('code') == 200:
            log("  [+] 成功读取到账号绑定信息树，正在分析...")
            result['success'] = True
            data = res_info.get('data', {})
            
            # 5.1 处理微信绑定 (CAS)
            cas_list = data.get('customerBindWechatCasSysInfo', [])
            result['cas_total'] = len(cas_list)
            if result['cas_total'] > 0:
                log(f"  [!] 检测到到存在 {result['cas_total']} 个微信CAS绑定记录，准备全部执行解绑...")
            else:
                log("  [-] 当前账号无任何微信CAS绑定记录，跳过解绑环节")

            for idx, cas in enumerate(cas_list, 1):
                union_id = cas.get('unionid') or cas.get('unionId')
                open_id = cas.get('openId')
                if union_id and open_id:
                    url_unbind = "https://member.jlc.com/api/integrated/wechat/user/unbind"
                    unbind_body = {
                        "unionId": union_id,
                        "openId": open_id,
                        "weixinCustomerId": 0,
                        "unbindFlag": "0"
                    }
                    log(f"  [-] 正在请求解绑第 {idx} 个目标 (UnionID: {union_id[:8]}***) ...")
                    res_unbind = send_post_request(driver, url_unbind, unbind_body)
                    if isinstance(res_unbind, dict) and res_unbind.get('code') == 200:
                        log(f"  [+] 解绑成功")
                        result['cas_unbound_count'] += 1
                    else:
                        log(f"  [x] 解绑接口响应失败: {str(res_unbind)[:100]}")
                        result['cas_fail_count'] += 1
                        
            # 5.2 读取公众号绑定 (Customer)
            log("  [-] 正在查询微信公众号关联状态...")
            oa_list = data.get('customerBindWechatCustomerSysInfo', [])
            if oa_list:
                log("  [+] 存在公众号绑定关系")
                result['oa_bind'] = True
                result['oa_bind_time'] = oa_list[0].get('bindTime')
                result['oa_bind_type'] = oa_list[0].get('bindType', '未知方式')
            else:
                log("  [-] 未发现公众号关联信息")
                
            # 5.3 获取绑定二维码
            qr_obj = data.get('qrObj', {})
            result['qr_url'] = qr_obj.get('imageUrl', '未获取到URL')
            result['qr_valid'] = qr_obj.get('validTime', '未知')
            log("  [+] 新绑定用二维码信息提取完成")
            
        else:
            log(f"  [x] 状态读取接口返回异常状态: {str(res_info)[:200]}")
    else:
        log(f"  [x] 状态读取接口返回非JSON数据: {str(res_info)[:200]}")
        
    return result


def process_single_account(username, password, account_index, skip_steps):
    """处理单个账号完整流程"""
    result = {
        'username': username,
        'index': account_index,
        's1': None, 's2': None, 's3': None, 's4': None, 's5': None
    }
    
    driver = None
    user_data_dir = tempfile.mkdtemp()
    visited_domains = set()

    try:
        log(f"🌐 启动浏览器 (账号 {account_index})...")
        driver = create_chrome_driver(user_data_dir)

        # ====== 登录 ======
        login_status = perform_login_flow(driver, username, password, max_retries=3)
        if login_status != 'success':
            for i in range(1, 6):
                result[f's{i}'] = {'success': False, 'error': f'登录失败: {login_status}'}
            return result

        # ====== 阶段1 & 阶段2 ======
        if 1 not in skip_steps or 2 not in skip_steps:
            safe_visit_with_sso_wait(driver, "https://www.jlc.com/newOrder/#/collectCoupons?spm=JLC.MEMBER", visited_domains)
            if 1 not in skip_steps: result['s1'] = execute_step_1(driver)
            if 2 not in skip_steps: result['s2'] = execute_step_2(driver)

        # ====== 阶段3 ======
        if 3 not in skip_steps:
            safe_visit_with_sso_wait(driver, "https://m.jlc.com/pages/coupon-page/index?id=43", visited_domains)
            result['s3'] = execute_step_3(driver)

        # ====== 阶段4 ======
        if 4 not in skip_steps:
            safe_visit_with_sso_wait(driver, "https://member.jlc.com/integrated/content/couponList?spm=JLC.MEMBER", visited_domains)
            result['s4'] = execute_step_4(driver)

        # ====== 阶段5 ======
        if 5 not in skip_steps:
            safe_visit_with_sso_wait(driver, "https://member.jlc.com/integrated/security-setting?spm=JLC.MEMBER", visited_domains)
            result['s5'] = execute_step_5(driver)

    except Exception as e:
        log(f"❌ 账号处理异常: {e}")
        for i in range(1, 6):
            if i not in skip_steps and result[f's{i}'] is None:
                result[f's{i}'] = {'success': False, 'error': f'发生异常退出'}
    finally:
        if driver:
            try: driver.quit()
            except: pass
        if os.path.exists(user_data_dir):
            try: shutil.rmtree(user_data_dir, ignore_errors=True)
            except: pass

    return result


def main():
    if len(sys.argv) < 3:
        print("用法: python status.py 账号1,账号2... 密码1,密码2... [跳过步骤,例:1,3]")
        sys.exit(1)

    usernames = sys.argv[1].split(',')
    passwords = sys.argv[2].split(',')
    
    skip_steps = []
    if len(sys.argv) >= 4:
        skip_str = sys.argv[3]
        skip_steps = [int(x) for x in re.findall(r'\d+', skip_str)]
        log(f"已配置跳过步骤: {skip_steps}", show_time=False)

    if len(usernames) != len(passwords):
        log("❌ 账号密码数量不匹配")
        sys.exit(1)

    log(f"检测到 {len(usernames)} 个账号需要处理", show_time=False)
    all_results = []

    for i, (u, p) in enumerate(zip(usernames, passwords), 1):
        log(f"\n{'='*50}", show_time=False)
        log(f"🚀 正在处理账号 {i}/{len(usernames)}", show_time=False)
        log(f"{'='*50}", show_time=False)
        res = process_single_account(u, p, i, skip_steps)
        all_results.append(res)
        if i < len(usernames):
            log("⏳ 等待5秒后处理下一个账号...")
            time.sleep(5)

    # ===================== 汇总输出 =====================
    log(f"\n{'='*50}", show_time=False)
    log("📊 执行结果总结", show_time=False)
    log(f"{'='*50}", show_time=False)

    for i, r in enumerate(all_results):
        if i > 0:
            log(f"{'='*50}", show_time=False)
        log(f"\n账号{r['index']}({r['username']})", show_time=False)
        
        # 阶段1
        if 1 not in skip_steps and r['s1']:
            if r['s1'].get('success'):
                status = "❌已风控，需要实名" if r['s1']['risk'] else "✔未风控"
                log(f"PCB免费券风控:{status}", show_time=False)
            else:
                log(f"PCB免费券风控:❌查询失败", show_time=False)

        # 阶段2
        if 2 not in skip_steps and r['s2']:
            if r['s2'].get('success'):
                if r['s2']['available']:
                    log(f"是否有通用券可领取:✔有，快去领吧 ({','.join(r['s2']['names'])})", show_time=False)
                else:
                    log(f"是否有通用券可领取:❌无", show_time=False)
            else:
                log(f"是否有通用券可领取:❌查询失败", show_time=False)

        # 阶段3
        if 3 not in skip_steps and r['s3']:
            if r['s3'].get('success'):
                if r['s3']['status'] == 'claimed':
                    log(f"每月礼包内容:✔\n" + "\n".join(r['s3']['names']), show_time=False)
                else:
                    log(f"每月礼包内容:❌领取失败，{r['s3'].get('reason', '原因未知')}", show_time=False)
            else:
                log(f"每月礼包内容:❌领取失败，请求异常", show_time=False)

        # 阶段4
        if 4 not in skip_steps and r['s4']:
            if r['s4'].get('success'):
                coupons = r['s4']['coupons']
                if coupons:
                    log(f"账号下所有可用券:\n" + "\n".join(coupons), show_time=False)
                else:
                    log(f"账号下所有可用券:❌当前无可用券", show_time=False)
            else:
                log(f"账号下所有可用券:❌查询失败", show_time=False)

        # 阶段5
        if 5 not in skip_steps and r['s5']:
            if r['s5'].get('success'):
                # 微信绑定状况
                cas_total = r['s5']['cas_total']
                unbound = r['s5']['cas_unbound_count']
                fail = r['s5']['cas_fail_count']
                
                if cas_total == 0:
                    log("微信绑定情况:✔无微信绑定", show_time=False)
                elif fail == 0:
                    log(f"微信绑定情况:✔共绑定了{cas_total}个微信，已经全部取消", show_time=False)
                else:
                    log(f"微信绑定情况:❌✔共绑定了{cas_total}个微信，有{fail}个取消绑定失败", show_time=False)
                    
                # 微信公众号状况
                if r['s5']['oa_bind']:
                    dt_str = format_date(r['s5'].get('oa_bind_time'))
                    b_type = r['s5'].get('oa_bind_type')
                    log(f"微信公众号绑定情况:✔已绑定过，绑定时间{dt_str}，绑定方式{b_type}", show_time=False)
                else:
                    log(f"微信公众号绑定情况:未绑定过", show_time=False)
                    
                # 二维码输出
                qr_url = r['s5'].get('qr_url')
                qr_valid = format_date(r['s5'].get('qr_valid'))
                log("微信公众号绑定二维码，可以复制到浏览器打开，扫描后会覆盖绑定原有的公众号:", show_time=False)
                log(f"{qr_url}", show_time=False)
                log(f"(有效期至{qr_valid})", show_time=False)
            else:
                log("微信绑定情况:❌读取失败", show_time=False)

    log(f"\n{'='*50}", show_time=False)
    sys.exit(0)


if __name__ == "__main__":
    main()
