import os
import sys
import time
import json
import random
import imaplib
import email
import re
import subprocess
import requests
import tempfile
import shutil
import psutil
import threading
import queue
from datetime import datetime
from email.utils import parsedate_to_datetime
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException

from Utils import pwdEncrypt

class BrowserError(Exception):
    """自定义异常: 用于精确标识浏览器底层打不开、崩溃或彻底超时的情况"""
    pass

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def read_config():
    config = {}
    config_path = "config.txt"
    if not os.path.exists(config_path):
        log(f"❌ 找不到配置文件: {config_path}")
        sys.exit(1)
    
    with open(config_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or ":" not in line:
                continue
            key, val = line.split(":", 1)
            config[key.strip()] = val.strip()
    return config

def force_kill_driver(driver):
    if not driver:
        return
    try:
        driver_pid = driver.service.process.pid
        try:
            parent = psutil.Process(driver_pid)
            for child in parent.children(recursive=True):
                try:
                    child.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            try:
                parent.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    except Exception:
        pass
    finally:
        try:
            driver.quit()
        except Exception:
            pass

def cleanup_zombie_chrome():
    current_time = time.time()
    for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'create_time']):
        try:
            name = proc.info.get('name')
            if name and ('chrome' in name.lower() or 'chromedriver' in name.lower()):
                cmdline = proc.info.get('cmdline')
                if cmdline:
                    cmd_str = ' '.join(cmdline)
                    if 'jlc_profile_' in cmd_str or '--headless' in cmd_str:
                        create_time = proc.info.get('create_time', current_time)
                        if current_time - create_time > 120:
                            proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

def create_chrome_driver(profile_dir, proxy_str=None, disable_images=True):
    options = Options()
    options.page_load_strategy = 'eager'
    options.add_argument(f"--user-data-dir={profile_dir}")
    
    if proxy_str:
        options.add_argument(f"--proxy-server=http://{proxy_str}")
    
    options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-gpu')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-software-rasterizer')
    options.add_argument('--disable-extensions')
    
    options.add_argument('--disable-background-networking')
    options.add_argument('--disable-background-timer-throttling')
    options.add_argument('--disable-backgrounding-occluded-windows')
    options.add_argument('--disable-renderer-backgrounding')
    options.add_argument('--disable-hang-monitor')
    options.add_argument('--disable-ipc-flooding-protection')
    options.add_argument('--disable-default-apps')
    options.add_argument('--disable-translate')
    options.add_argument('--disable-sync')
    options.add_argument('--metrics-recording-only')
    options.add_argument('--safebrowsing-disable-auto-update')
    options.add_argument('--enable-features=NetworkServiceInProcess2')
    options.add_argument('--disable-features=IsolateOrigins,site-per-process')
    options.add_argument('--js-flags=--max-old-space-size=512')
    
    options.add_argument('--window-size=1366,768')
    options.add_argument('--disable-blink-features=AutomationControlled')
    if disable_images:
        options.add_argument('--blink-settings=imagesEnabled=false')
    options.add_argument('--mute-audio')
    legacy_ua = "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/86.0.4240.198 Safari/537.36"
    options.add_argument(f"user-agent={legacy_ua}")
    options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})
    
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(20)
    driver.set_script_timeout(20)
    return driver

class HaoZhuMa:
    def __init__(self, host, user, pwd, sid):
        self.host = host if host.startswith("http") else f"http://{host}"
        self.user = user
        self.pwd = pwd
        self.sid = sid
        self.token = None

    def login(self):
        url = f"{self.host}/sms/?api=login&user={self.user}&pass={self.pwd}"
        try:
            resp = requests.get(url, timeout=10).json()
            if str(resp.get("code")) in ("0", "200"):
                self.token = resp.get("token")
                log("✅ 接码平台登录成功")
                return True
            log(f"❌ 接码平台登录失败: {resp}")
            return False
        except Exception as e:
            log(f"❌ 请求接码平台登录异常: {e}")
            return False

    def check_balance(self):
        url = f"{self.host}/sms/?api=getSummary&token={self.token}"
        try:
            resp = requests.get(url, timeout=10).json()
            if str(resp.get("code")) in ("0", "200"):
                money = float(resp.get("money", 0))
                log(f"💰 接码平台余额: {money} 元")
                return money
            return -1
        except Exception:
            return -1

    def get_phone(self, phone=None):
        base_url = f"{self.host}/sms/?api=getPhone&token={self.token}&sid={self.sid}&auther=sorenhugo58"
        url = f"{base_url}&phone={phone}" if phone else f"{base_url}&ascription=2&exclude=192"
        try:
            resp = requests.get(url, timeout=15).json()
            if str(resp.get("code")) in ("0", "200"):
                return resp.get("phone")
            log(f"⚠ 获取手机号失败: {resp}")
            return None
        except Exception as e:
            return None

    def get_message(self, phone, timeout=60):
        url = f"{self.host}/sms/?api=getMessage&token={self.token}&sid={self.sid}&phone={phone}"
        start_time = time.time()
        log(f"📡 开始监听 {phone} 的短信 (超时: {timeout}s)...")
        while time.time() - start_time < timeout:
            try:
                resp = requests.get(url, timeout=10).json()
                if str(resp.get("code")) in ("0", "200"):
                    code = resp.get("yzm")
                    if code:
                        log(f"✉ 成功提取到验证码: {code}")
                        return code
            except Exception:
                pass
            time.sleep(5)
        log("❌ 监听短信超时")
        return None

    def release_phone(self, phone):
        url = f"{self.host}/sms/?api=cancelRecv&token={self.token}&sid={self.sid}&phone={phone}"
        try:
            requests.get(url, timeout=5)
            log(f"♻ 已释放手机号: {phone}")
        except Exception:
            pass

    def add_blacklist(self, phone):
        url = f"{self.host}/sms/?api=addBlacklist&token={self.token}&sid={self.sid}&phone={phone}"
        try:
            resp = requests.get(url, timeout=10).json()
            if str(resp.get("code")) == "0":
                log(f"🚫 已成功将无效号码 {phone} 加入黑名单")
                return True
            log(f"⚠ 拉黑号码失败: {resp}")
            return False
        except Exception as e:
            log(f"❌ 请求拉黑号码异常: {e}")
            return False

def get_valid_proxy(timeout=None):
    api_url = "http://api.dmdaili.com/dmgetip.asp?apikey=b345ad7e&pwd=bca1fcb138fb91448d9cfe7f1099c6f6&getnum=1&httptype=1&geshi=2&fenge=1&fengefu=&operate=all"
    start_time = time.time()
    
    while True:
        if timeout and (time.time() - start_time) > timeout:
            log(f"❌ 代理API: 获取或测试代理已达到设定的超时时间 ({timeout}秒)")
            return None
            
        try:
            resp = requests.get(api_url, timeout=10)
            data = resp.json()
            
            if data.get("code") == 605:
                log(f"⚠ 代理API: 白名单未生效或需等待 ({data.get('msg')})，等待15秒...")
                time.sleep(15)
                continue
            elif data.get("code") == 1 and "Too Many Requests" in data.get("msg", ""):
                time.sleep(5)
                continue
            elif data.get("code") == 0 and data.get("data"):
                p_info = data["data"][0]
                ip, port, city = p_info.get("ip"), p_info.get("port"), p_info.get("city", "未知")
                proxy_str = f"{ip}:{port}" 
                log(f"🔗 获取到代理: {proxy_str} [位置: {city}]，正在测试...")
                
                try:
                    test_resp = requests.get("https://passport.jlc.com", proxies={"http": f"http://{proxy_str}", "https": f"http://{proxy_str}"}, timeout=5)
                    if test_resp.status_code == 200:
                        log("✅ 代理测试成功，延迟正常")
                        return proxy_str
                    else:
                        log(f"⚠ 代理测试失败，返回状态码: {test_resp.status_code}，重新获取...")
                        continue
                except requests.exceptions.ConnectTimeout:
                    log("⚠ 代理测试连接超时，重新获取...")
                    continue
                except requests.exceptions.ReadTimeout:
                    log("⚠ 代理测试读取超时，重新获取...")
                    continue
                except requests.exceptions.ProxyError as e:
                    log(f"⚠ 代理拒绝连接或代理错误 ({e})，重新获取...")
                    continue
                except requests.exceptions.ConnectionError as e:
                    log(f"⚠ 代理连接失败 ({e})，重新获取...")
                    continue
                except Exception as e:
                    log(f"⚠ 代理测试未知错误 ({type(e).__name__}: {e})，重新获取...")
                    continue
            else:
                log(f"❌ 代理API返回异常内容: {data}")
                time.sleep(3)
        except Exception as e:
            time.sleep(3)

def dp_fetch(driver, url, method="POST", body=None, extra_headers=None):
    try:
        headers_dict = {'Content-Type': 'application/json', 'Accept': 'application/json, text/plain, */*'}
        if extra_headers:
            headers_dict.update(extra_headers)
        
        headers_str = json.dumps(headers_dict, ensure_ascii=False)
        
        js_clear_sig = """
        document.cookie = 'signature=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/; domain=' + window.location.hostname;
        document.cookie = 'signature=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/; domain=.' + window.location.hostname.split('.').slice(-2).join('.');
        document.cookie = 'signature=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;';
        """
        
        if body is not None:
            body_str = json.dumps(body, ensure_ascii=False)
            body_part = f"body: JSON.stringify({body_str}),"
        else:
            body_part = ""

        js_code = js_clear_sig + f"""
        var callback = arguments[arguments.length - 1];
        var isDone = false;
        var timer = setTimeout(function() {{
            if (!isDone) {{
                isDone = true;
                callback({{"error": "JS内部fetch超时 (15s)"}});
            }}
        }}, 15000);

        fetch('{url}', {{
            method: '{method}',
            headers: {headers_str},
            {body_part}
            credentials: 'include'
        }}).then(async r => {{
            const text = await r.text();
            if (isDone) return;
            isDone = true;
            clearTimeout(timer);
            try {{
                callback(JSON.parse(text));
            }} catch(e) {{
                callback({{
                    error: "非JSON响应(可能被拦截)", 
                    status: r.status, 
                    snippet: text.substring(0, 200)
                }});
            }}
        }}).catch(e => {{
            if (isDone) return;
            isDone = true;
            clearTimeout(timer);
            callback({{error: e.toString()}});
        }});
        """
        
        for attempt in range(10):
            try:
                res = driver.execute_async_script(js_code)
            except TimeoutException as te:
                res = {"error": f"执行fetch超时: {str(te)}"}
            
            if isinstance(res, dict) and res.get("error") == "非JSON响应(可能被拦截)":
                if attempt < 9:
                    short_url = url.split("?")[0].split("/")[-1]
                    log(f"⚠ 接口 [{short_url}] 返回非JSON，等待1秒后重试请求 ({attempt+1}/10)...")
                    time.sleep(1)
                    continue
                else:
                    return res
            
            return res
            
    except Exception as e:
        log(f"❌ 浏览器 JS 发包执行失败: {e}")
        return {"error": str(e)}

def call_aliv3_script(script_name, proxy_str, timeout_seconds=180):
    log(f"📞 调用 {script_name} 获取 CaptchaTicket...")
    if not os.path.exists(script_name):
        return None
        
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    if proxy_str:
        env["GLOBAL_PROXY"] = proxy_str 
    
    process = subprocess.Popen(
        [sys.executable, script_name],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="ignore",
        env=env
    )
    
    start_time = time.time()
    ticket = None
    output_buffer = []
    
    q = queue.Queue()
    
    def enqueue_output(out, queue_obj):
        try:
            for line in iter(out.readline, ''):
                queue_obj.put(line)
        except Exception:
            pass
        finally:
            try:
                out.close()
            except Exception:
                pass

    t = threading.Thread(target=enqueue_output, args=(process.stdout, q))
    t.daemon = True
    t.start()
    
    while True:
        if time.time() - start_time > timeout_seconds:
            output_buffer.append(f"❌ 脚本执行超时 ({timeout_seconds}s)")
            process.kill()
            break
            
        try:
            line = q.get(timeout=0.05)
        except queue.Empty:
            if process.poll() is not None:
                break
            continue
        
        clean_line = line.strip()
        if clean_line:
            output_buffer.append(clean_line) 
            
        if "SUCCESS: Obtained CaptchaTicket:" in line:
            try:
                next_line = q.get(timeout=5.0)
                if next_line:
                    ticket = next_line.strip()
                    log("✅ 成功截获 CaptchaTicket")
                    process.terminate()
                    return ticket
            except queue.Empty:
                pass
                
        if "captchaTicket" in line:
            match = re.search(r'"captchaTicket"\s*:\s*"([^"]+)"', line)
            if match:
                ticket = match.group(1)
                log(f"✅ 从 JSON 中截获 CaptchaTicket")
                process.terminate()
                return ticket

    if process.poll() is None:
        process.terminate()
        
    if not ticket:
        log(f"❌ 异常：调用 {script_name} 未能成功获取 CaptchaTicket，脚本日志如下：")
        for msg in output_buffer:
            log(f"[{script_name}] {msg}")
            
    return ticket

def get_email_code(user, pwd, customer_code, timeout=60):
    log(f"📧 开始登录邮箱 {user} 获取验证码 (超时 {timeout}s)...")
    start_time = time.time()
    end_time = start_time + timeout

    while time.time() < end_time:
        mail = None
        try:
            mail = imaplib.IMAP4_SSL("imap.gmail.com")
            mail.login(user, pwd)
            
            stat, count_data = mail.select("inbox")
            try:
                num_messages = int(count_data[0])
            except:
                num_messages = 0

            if num_messages > 0:
                check_limit = max(0, num_messages - 10)
                
                for i in range(num_messages, check_limit, -1):
                    try:
                        typ, msg_data = mail.fetch(str(i), '(RFC822)')
                        for response_part in msg_data:
                            if isinstance(response_part, tuple):
                                msg = email.message_from_bytes(response_part[1])
                                
                                date_str = msg.get("Date")
                                email_timestamp = 0
                                try:
                                    if date_str:
                                        email_dt = parsedate_to_datetime(date_str)
                                        email_timestamp = email_dt.timestamp()
                                except:
                                    pass

                                if email_timestamp > 0 and email_timestamp < (start_time - 5):
                                    continue
                                
                                full_body = ""
                                if msg.is_multipart():
                                    for part in msg.walk():
                                        content_type = part.get_content_type()
                                        if content_type in ["text/plain", "text/html"]:
                                            try:
                                                payload = part.get_payload(decode=True)
                                                if payload:
                                                    full_body += payload.decode(errors='ignore')
                                            except:
                                                pass
                                else:
                                    try:
                                        payload = msg.get_payload(decode=True)
                                        if payload:
                                            full_body = payload.decode(errors='ignore')
                                    except:
                                        pass
                                
                                if f"尊敬的客户{customer_code}" in full_body:
                                    match = re.search(r"验证码.*?(\d{6})", full_body)
                                    if match:
                                        code = match.group(1)
                                        log(f"✅ 成功从邮件提取验证码: {code} (客编匹配成功)")
                                        return code
                    except Exception:
                        continue
        except Exception as e:
            log(f"⚠ 邮箱连接或读取异常: {e}")
        finally:
            if mail:
                try:
                    mail.logout()
                except:
                    pass
        time.sleep(3)
        
    log("❌ 邮箱接收验证码超时")
    return None

def random_chinese_chars(count=3):
    first_names = "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨标记朱秦尤许何吕施张孔曹严华金魏陶姜"
    last_names = "伟芳娜秀丽敏静坚勇婷杰娟涛明超强霞平刚桂英"
    
    if count <= 0:
        return ""
    if count == 1:
        return random.choice(first_names)
        
    name = random.choice(first_names)
    for _ in range(count - 1):
        name += random.choice(last_names)
    return name

def register_account(hzm, config, email_index, fixed_password):
    profile_dirs = []

    def create_new_profile_dir():
        d = tempfile.mkdtemp(prefix="jlc_profile_")
        profile_dirs.append(d)
        return d

    account_info = {
        "customerCode": "", "password": fixed_password, "phone": "",
        "email": "", "attributionName": "未设置"
    }
    
    driver = None
    proxy_str = None
    
    def safe_get_page(target_driver, url, max_retries=2):
        for attempt in range(max_retries):
            try:
                target_driver.get(url)
                return  
            except TimeoutException as te:
                log(f"⚠ 页面加载超时 ({attempt+1}/{max_retries}): 触发 window.stop() 强行停止渲染...")
                try:
                    target_driver.execute_script("window.stop();")
                except:
                    pass
                if attempt == max_retries - 1:
                    raise BrowserError(f"连续 {max_retries} 次加载 {url} 失败: {te}")
                time.sleep(2)
            except Exception as e:
                log(f"⚠ 页面加载底层异常 ({attempt+1}/{max_retries}): {e}")
                if attempt == max_retries - 1:
                    raise BrowserError(f"页面加载彻底崩溃: {e}")
                time.sleep(2)

    def safe_fetch(url, method="POST", body=None, max_retries=3, **kwargs):
        nonlocal driver, proxy_str
        for attempt in range(max_retries):
            res = dp_fetch(driver, url, method, body, **kwargs)
            if isinstance(res, dict) and "error" not in res:
                return res
            
            err_msg = res.get("error") if isinstance(res, dict) else res
            log(f"⚠ 接口网络异常或请求失效 (尝试 {attempt+1}/{max_retries}): {err_msg}")
            
            if attempt < max_retries - 1:
                log("🔄 触发防断连：继承之前的登录状态重启...")
                try: 
                    saved_temp_cookies = driver.get_cookies()
                    force_kill_driver(driver)
                except: saved_temp_cookies = []
                
                if proxy_str:
                    log("🔄 尝试获取新代理进行重连...")
                    new_proxy = get_valid_proxy(timeout=300)
                    if new_proxy:
                        proxy_str = new_proxy
                    else:
                        log("⚠ 获取新代理失败，继续使用旧代理重试。")

                driver = create_chrome_driver(create_new_profile_dir(), proxy_str, disable_images=True)
                
                if "member.jlc.com" in url:
                    domain = "https://member.jlc.com"
                    restore_url = "https://member.jlc.com/integrated/accountInfo/userAccountInfo?spm=JLC.MEMBER"
                elif "passport.jlc.com" in url:
                    domain = "https://passport.jlc.com"
                    restore_url = "https://passport.jlc.com/m/register"
                else:
                    domain = "https://m.jlc.com"
                    restore_url = "https://m.jlc.com/m/register"

                try:
                    driver.set_page_load_timeout(10)
                    driver.get(f"{domain}/favicon.ico")
                except: pass
                
                valid_keys = ['name', 'value', 'domain', 'path', 'secure', 'httpOnly', 'expiry', 'sameSite']
                for c in saved_temp_cookies:
                    clean_c = {k: v for k, v in c.items() if k in valid_keys}
                    try: driver.add_cookie(clean_c)
                    except: pass
                
                driver.set_page_load_timeout(20)
                safe_get_page(driver, restore_url)
                time.sleep(3)
                
        return {"error": "Max retries exceeded"}

    try:
        proxy_str = None 
        driver = create_chrome_driver(create_new_profile_dir(), proxy_str, disable_images=True)

        phone = None
        sms_code = None
        
        for get_sms_loop in range(100):
            log(f"🌐 打开注册页面... (本阶段第 {get_sms_loop + 1} 次尝试)")
            safe_get_page(driver, "https://passport.jlc.com/m/register")
            time.sleep(random.uniform(3.5, 5.5))
            
            phone = None
            for phone_attempt in range(20):
                phone = hzm.get_phone()
                if phone:
                    break
                log(f"⚠ 第 {phone_attempt + 1}/20 次尝试获取手机号失败，等待 3 秒后重试...")
                time.sleep(3)
                
            if not phone:
                raise Exception("连续 20 次尝试未能从接码平台获取到手机号，主动终止注册")
            account_info["phone"] = phone
            log(f"📱 成功获取手机号: {phone}")

            ticket = call_aliv3_script("AliV3-register.py", proxy_str)
            if not ticket:
                hzm.release_phone(phone)
                raise Exception("获取 CaptchaTicket 失败")

            enc_phone = pwdEncrypt(phone)
            log("📡 发送 send-security-code...")
            r1 = safe_fetch("https://passport.jlc.com/api/cas/register/mobile/send-security-code", "POST", {
                "phoneNumber": enc_phone, "captchaTicket": ticket, "appId": "JLC_MOBILE_APP"
            })
            if r1.get("code") != 200:
                hzm.release_phone(phone)
                raise Exception(f"发验证码失败: {r1}")

            sms_code = hzm.get_message(phone)
            if not sms_code:
                log("❌ 注册流程执行异常断开: 获取短信验证码超时")
                hzm.add_blacklist(phone)
                continue 

            break 

        if not sms_code:
            raise Exception("连续 100 次获取短信验证码超时，放弃本次注册任务")

        log("🔄 释放浏览器并准备获取代理...")
        try:
            temp_cookies_1 = driver.get_cookies()
            force_kill_driver(driver)
        except:
            temp_cookies_1 = []
            pass
        
        proxy_success = False
        for proxy_attempt in range(10):
            log(f"🔗 开始获取代理并重建环境 (尝试 {proxy_attempt+1}/10)...")
            proxy_str = get_valid_proxy(timeout=300)
            if not proxy_str:
                log("⚠ 获取新代理超时或失败。")
                continue
                
            ip_get_time = time.time()
            temp_driver = create_chrome_driver(create_new_profile_dir(), proxy_str, disable_images=True)
            
            log("🌐 [代理] 重建浏览器环境，跨域恢复 Cookie 状态...")
            try:
                temp_driver.set_page_load_timeout(10)
                try:
                    temp_driver.get("https://passport.jlc.com/favicon.ico")
                except TimeoutException:
                    pass 
                
                valid_keys = ['name', 'value', 'domain', 'path', 'secure', 'httpOnly', 'expiry', 'sameSite']
                for c in temp_cookies_1:
                    clean_c = {k: v for k, v in c.items() if k in valid_keys}
                    try: temp_driver.add_cookie(clean_c)
                    except: pass
                    
                temp_driver.set_page_load_timeout(20)
                safe_get_page(temp_driver, "https://passport.jlc.com/m/register")
                
                if (time.time() - ip_get_time) > 50:
                    log("⚠ 页面加载完毕但代理 IP 寿命（60秒）已耗尽，准备重试获取新代理...")
                    force_kill_driver(temp_driver)
                    continue
                    
                # 成功恢复环境
                driver = temp_driver
                proxy_success = True
                break
                
            except BrowserError as be:
                log(f"⚠ 代理环境页面加载失败: {be}")
                force_kill_driver(temp_driver)
                continue
            except Exception as e:
                log(f"⚠ 新代理加载页面失败或未知异常: {e}")
                force_kill_driver(temp_driver)
                continue
                
        if not proxy_success:
            hzm.release_phone(phone)
            raise Exception("连续 10 次获取代理并重建浏览器环境失败，放弃当前任务，重新开始注册")
            
        driver.set_page_load_timeout(20)
        time.sleep(random.uniform(1.5, 2.5))

        log("📡 发送 get-init-session...")
        r2 = safe_fetch("https://passport.jlc.com/api/cas/register/get-init-session", "POST", {
            "appId": "JLC_MOBILE_APP", "redirectUrl": "https://m.jlc.com/pages/my/index#/from=jlc_cas", "clientType": "MOBILE-WEB"
        })
        if r2.get("code") != 200:
            raise Exception(f"Session 初始化失败: {r2}")
        
        log("📡 发送 register/submit...")
        r3 = safe_fetch("https://passport.jlc.com/api/cas/register/submit", "POST", {
            "phoneNumber": enc_phone, "validateCode": sms_code
        })
        
        customer_code = ""
        if r3.get("code") == 2005:
            customer_code = r3.get("data", {}).get("customerCode")
        elif r3.get("code") == 2007:
            log("⚠ 该手机号已注册过，执行 continue-register 以继续注册...")
            r3_1 = safe_fetch("https://passport.jlc.com/api/cas/register/continue-register", "POST", {})
            if r3_1.get("code") == 2005:
                customer_code = r3_1.get("data", {}).get("customerCode")
            else:
                raise Exception(f"continue-register 失败: {r3_1}")
        elif r3.get("code") == 102281:
            log(f"❌ 注册流程执行异常断开: 触发风控限制 (102281)")
            hzm.add_blacklist(phone)  
            raise Exception(f"触发风控限制 (102281): {r3}") 
        else:
            raise Exception(f"注册接口返回异常: {r3}")
            
        hzm.release_phone(phone)
        if not customer_code:
            raise Exception("未能成功提取客编")
        
        account_info["customerCode"] = customer_code
        log(f"✅ 注册成功！客编: {customer_code}")

        log("📡 确认协议...")
        safe_fetch("https://passport.jlc.com/api/cas/sso/doc/batch-read", "POST", {
            "appId": "JLC_MOBILE_APP", "protocolClientType": "MOBILE", "protocolTypes": ["USER", "PRIVACY"]
        })

        log("📡 拉取用户信息...")
        r_user = safe_fetch("https://passport.jlc.com/api/cas/sso/get-user-info", "POST", {"appId": "JLC_MOBILE_APP"})
        if r_user.get("code") != 200:
            log(f"⚠ 获取用户信息失败: {r_user}")

        enc_pass = pwdEncrypt(fixed_password)
        log(f"📡 设置统一登录密码...")
        r_pwd = safe_fetch("https://passport.jlc.com/api/cas/register/set-password", "POST", {
            "password": enc_pass, "appId": "JLC_MOBILE_APP"
        })
        if r_pwd.get("code") == 200:
            log(f"✅ 密码设置成功: {fixed_password}")
        else:
            raise Exception(f"设置密码失败: {r_pwd}")

        safe_fetch("https://passport.jlc.com/api/cas/secure/check-callback-url", "POST", {"callbackUrl": "https://m.jlc.com"})

        log("🔄 注册阶段结束，关闭代理浏览器，无代理进行归属设置...")
        try:
            force_kill_driver(driver)
        except: pass
        time.sleep(2)

        driver = create_chrome_driver(create_new_profile_dir(), proxy_str=None, disable_images=True)
        
        log("🌐 浏览器已启动，准备执行新注册账号登录流程...")
        login_success = False
        login_headers = {'AppId': 'JLC_PORTAL_PC', 'ClientType': 'PC-WEB'}
        
        for login_attempt in range(3):
            try:
                safe_get_page(driver, "https://passport.jlc.com/login")
                time.sleep(2)
                
                log(f"📡 发送登录初始化会话 (尝试 {login_attempt+1})...")
                dp_fetch(driver, "https://passport.jlc.com/api/cas/login/get-init-session", "POST", {
                    "appId": "JLC_PORTAL_PC", "clientType": "PC-WEB"
                }, extra_headers=login_headers)
                
                log("📞 调用 AliV3-login.py 过登录滑块...")
                login_ticket = call_aliv3_script("AliV3-login.py", None)
                if not login_ticket:
                    raise Exception("获取登录 CaptchaTicket 失败")
                    
                enc_user = pwdEncrypt(customer_code)
                log("📡 发送登录请求...")
                login_res = dp_fetch(driver, "https://passport.jlc.com/api/cas/login/with-password", "POST", {
                    "username": enc_user,
                    "password": enc_pass,
                    "isAutoLogin": False,
                    "captchaTicket": login_ticket
                }, extra_headers=login_headers)
                
                if login_res.get("success") and login_res.get("code") == 2017:
                    log("✅ 登录请求成功！")
                else:
                    raise Exception(f"登录接口遭拒或密码错误: {login_res}")
                    
                log("🔍 验证登录状态...")
                safe_get_page(driver, "https://member.jlc.com/")
                
                for wait_idx in range(5):
                    time.sleep(2)
                    if "客编" in driver.page_source or "customerCode" in driver.page_source or customer_code in driver.page_source:
                        log("✅ 验证登录态成功！")
                        login_success = True
                        break
                    else:
                        log(f"⏳ 页面数据渲染中或跳转中，等待重试... ({wait_idx+1}/5)")
                
                if login_success:
                    break
                else:
                    raise Exception("登录态未能成功在页面中渲染完毕，可能被隐式拦截或重定向失败")
                    
            except Exception as e:
                log(f"⚠ 登录尝试 {login_attempt+1} 失败: {e}")
                time.sleep(3)
                
        if not login_success:
            raise Exception("新注册账号登录彻底失败，可能注册出现异常，终止后续绑定环节")

        log("🌐 开始设置账号归属...")
        for attempt in range(3):
            try:
                driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
                    'source': """
                    window.__jlc_secretkey = null;
                    const origSetRequestHeader = XMLHttpRequest.prototype.setRequestHeader;
                    XMLHttpRequest.prototype.setRequestHeader = function(name, value) {
                        if (name.toLowerCase() === 'secretkey') {
                            window.__jlc_secretkey = value;
                        }
                        return origSetRequestHeader.apply(this, arguments);
                    };
                    """
                })

                driver.get_log('performance')
                safe_get_page(driver, "https://member.jlc.com/integrated/accountInfo/userAccountInfo?spm=JLC.MEMBER")
                
                extra_headers = {}
                log("🔄 正在拦截页面合法鉴权头...")
                
                for sniff_loop in range(2):
                    start_wait = time.time()
                    found_key = False
                    
                    while time.time() - start_wait < 10:
                        logs = driver.get_log('performance')
                        for entry in logs:
                            try:
                                msg = json.loads(entry['message'])['message']
                                if msg['method'] == 'Network.requestWillBeSent':
                                    req = msg['params']['request']
                                    if 'member.jlc.com/api/' in req['url'] and req['method'].upper() != 'OPTIONS':
                                        req_headers = {str(k).lower(): str(v) for k, v in req['headers'].items()}
                                        if 'secretkey' in req_headers:
                                            extra_headers['secretkey'] = req_headers['secretkey']
                                            found_key = True
                                            break
                            except Exception:
                                continue
                        if found_key:
                            break
                        time.sleep(0.5)
                        
                    if 'secretkey' in extra_headers:
                        break
                    
                    if sniff_loop == 0:
                        log("⚠ 嗅探未抓到鉴权头，触发页面刷新重试...")
                        driver.refresh()
                
                if 'secretkey' in extra_headers:
                    log(f"✅ 成功截获合法鉴权头(SecretKey): {extra_headers['secretkey'][:10]}...")
                else:
                    log("⚠ 遍历了所有请求仍未截获鉴权头，启动 JS 缓存兜底方案...")
                    fallback_sk = driver.execute_script("""
                        return window.__jlc_secretkey || 
                               window.localStorage.getItem('secretkey') || 
                               window.localStorage.getItem('secretKey') || 
                               window.sessionStorage.getItem('secretkey') || 
                               window.sessionStorage.getItem('secretKey');
                    """)
                    if fallback_sk:
                        extra_headers['secretkey'] = fallback_sk
                        log(f"✅ 成功通过 JS 底层缓存兜底提取到 SecretKey: {fallback_sk[:10]}...")
                    else:
                        raise Exception("鉴权头(SecretKey)彻底缺失无法进行归属绑定，可能登录已失效")

                extra_headers['support-cookie-samesite'] = 'true'
                time.sleep(5)
                
                dp_fetch(driver, "https://member.jlc.com/api/integrated/customerAttribution/queryCustomerAttributionByParam", "POST", {"source": "JLC"}, extra_headers=extra_headers)
                
                hzm.get_phone(phone) 
                t_stamp = int(time.time() * 1000)
                dp_fetch(driver, f"https://member.jlc.com/api/integrated/customer/type/sendSmsNew?source=JLC&_t={t_stamp}", "GET", extra_headers=extra_headers)
                
                r_merge = dp_fetch(driver, "https://member.jlc.com/api/integrated/customerInvoiceInfo/group/showMergeData", "POST", extra_headers=extra_headers)
                
                sms_code2 = hzm.get_message(phone)
                if not sms_code2:
                    hzm.release_phone(phone)
                    raise Exception("归属设置获取短信超时")

                attr_name = random_chinese_chars(3)
                
                payload_attr_name = attr_name
                
                log(f"📡 发送 configAttribution 归属设置主请求...")
                r_attr = dp_fetch(driver, "https://member.jlc.com/api/integrated/customerAttribution/configAttribution", "POST", {
                    "source": "JLC", "smsCode": sms_code2.strip(), "customerType": 2, "attributionPersonName": payload_attr_name
                }, extra_headers=extra_headers)
                
                if r_attr.get("success") is True and r_attr.get("code") == 200:
                    log(f"✅ 归属接口返回设置成功！")
                else:
                    log(f"❌ configAttribution 接口返回异常: {r_attr}")
                    raise Exception(f"归属设置明确拒绝: {r_attr}")
                
                r_check = dp_fetch(driver, "https://member.jlc.com/api/integrated/customerAttribution/queryCustomerAttributionConfig", "POST", {}, extra_headers=extra_headers)
                if r_check.get("code") == 200 and r_check.get("data", {}).get("attributionName"):
                    account_info["attributionName"] = attr_name
                    log(f"✅ 成功设置归属名: {attr_name}")
                    break
                else:
                    raise Exception(f"验证归属失败: {r_check}")
            except Exception as e:
                log(f"⚠ 归属设置第 {attempt+1} 次尝试失败: {e}")
                if attempt == 2:
                    log("❌ 超过最大重试，跳过归属设置")

        log("🌐 开始绑定邮箱...")
        for attempt in range(3):
            try:
                safe_get_page(driver, "https://passport.jlc.com/set-email")
                time.sleep(3)
                
                dp_fetch(driver, "https://passport.jlc.com/api/cas/sso/get-user-info", "POST", {"appId": "JLC_BIZ_GATEWAY"})
                
                hzm.get_phone(phone)
                
                ticket2 = call_aliv3_script("AliV3-update_email_by_phone.py", None)
                r_sm = dp_fetch(driver, "https://passport.jlc.com/api/cas/modify/email/send-mobile-code", "POST", {
                    "captchaTicket": ticket2, "appId": "JLC_BIZ_GATEWAY"
                })
                if r_sm.get("code") != 200:
                    raise Exception(f"发送原手机验证码失败: {r_sm}")

                dp_fetch(driver, "https://passport.jlc.com/api/cas/modify/email/get-init-session", "POST", {"appId": "JLC_BIZ_GATEWAY"})
                
                sms_code3 = hzm.get_message(phone)
                if not sms_code3:
                    hzm.release_phone(phone)
                    raise Exception("绑定邮箱获取短信超时")

                r_chk = dp_fetch(driver, "https://passport.jlc.com/api/cas/modify/email/check-mobile-code", "POST", {"validateCode": sms_code3})
                if r_chk.get("code") != 2062:
                    raise Exception(f"校验手机验证码失败: {r_chk}")

                ticket3 = call_aliv3_script("AliV3-update_new_email.py", None)
                
                base_email = config["邮箱"].split("@")[0]
                domain = config["邮箱"].split("@")[1]
                target_email = f"{base_email}+{email_index}@{domain}"
                enc_email = pwdEncrypt(target_email)
                log(f"📡 正在向新邮箱 {target_email} 发送验证码...")
                
                r_ne = dp_fetch(driver, "https://passport.jlc.com/api/cas/modify/email/send-new-email-code", "POST", {
                    "email": enc_email, "captchaTicket": ticket3, "appId": "JLC_BIZ_GATEWAY"
                })
                if r_ne.get("code") != 200:
                    raise Exception(f"❌ 发送新邮箱验证码失败: {r_ne}")
                log("✅ 新邮箱验证码发送请求成功，准备登录邮箱查收...")

                email_code = get_email_code(config["邮箱"], config["邮箱密码"], customer_code)
                if not email_code:
                    raise Exception("无法从邮箱获取验证码")
                r_ce = dp_fetch(driver, "https://passport.jlc.com/api/cas/modify/email/change-email", "POST", {
                    "email": enc_email, "validateCode": email_code
                })
                if r_ce.get("code") == 2063:
                    account_info["email"] = target_email
                    log(f"✅ 成功绑定邮箱: {target_email}")
                    break
                else:
                    raise Exception(f"邮箱最终绑定请求失败: {r_ce}")
            except Exception as e:
                log(f"⚠ 绑定邮箱第 {attempt+1} 次尝试失败: {e}")
                if attempt == 2:
                    log("❌ 超过最大重试，绑定邮箱失败")
                    account_info["email"] = "未绑定"

        return account_info

    except BrowserError as e:
        log(f"❌ 浏览器或网络底层异常断开: {e}")
        return {"error": "browser_error"}
    except Exception as e:
        err_str = str(e).lower()
        if any(kw in err_str for kw in ["timeout", "timed out", "renderer", "session", "chrome not reachable", "disconnected", "no such window", "failed to start"]):
            log(f"❌ 浏览器引擎打不开或异常崩溃: {e}")
            return {"error": "browser_error"}
            
        log(f"❌ 注册流程执行业务异常断开: {e}")
        return None
    finally:
        try:
            if driver:
                force_kill_driver(driver)
        except:
            pass
        time.sleep(1)
        for d in profile_dirs:
            shutil.rmtree(d, ignore_errors=True)

def main():
    if len(sys.argv) < 4:
        print("用法: python jlc.py 注册数量 统一密码 邮箱初始数字")
        print("示例: python jlc.py 10 Password123 3")
        sys.exit(1)

    try:
        reg_count = int(sys.argv[1])
        fixed_password = sys.argv[2]
        start_email_num = int(sys.argv[3])
    except ValueError:
        print("❌ 错误: 参数类型不正确，数量和邮箱初始数字必须为整数")
        sys.exit(1)

    config = read_config()
    hzm = HaoZhuMa(config["服务器地址"], config["API账号"], config["API密码"], config["项目ID"])
    
    if not hzm.login():
        sys.exit(1)
        
    bal = hzm.check_balance()
    if bal < 0.3:
        log("❌ 余额不足 0.3 元，拒绝运行")
        sys.exit(1)

    success_accounts = []
    success_count = 0
    consecutive_failures = 0  

    while success_count < reg_count:
        cleanup_zombie_chrome()
        
        current_attempt = consecutive_failures + 1
        log(f"{'='*50}")
        log(f"🚀 开始注册任务进度: {success_count + 1}/{reg_count} (当前账号尝试第 {current_attempt} 次)")
        log(f"{'='*50}")

        current_email_index = start_email_num + success_count
        res = register_account(hzm, config, current_email_index, fixed_password)
        
        if res and res.get("customerCode"):
            line = f"客编: {res['customerCode']} | 密码: {res['password']} | 手机号: {res['phone']} | 邮箱: {res['email']} | 归属: {res['attributionName']}"
            success_accounts.append(line)
            with open("account.txt", "a", encoding="utf-8") as f:
                f.write(line + "\n")
            log(f"🎉 账号 {res['customerCode']} 数据已保存到 account.txt")
            
            success_count += 1
            consecutive_failures = 0  
            
            if success_count < reg_count:
                wait_time = random.randint(120, 480)
                log(f"⏳ 随机等待 {wait_time} 秒后继续下一个注册...")
                time.sleep(wait_time)
                
        elif res and res.get("error") == "browser_error":
            log("⚠ 检测到浏览器打不开或页面加载崩溃，本次失败不计入重试次数")
            time.sleep(3)
            continue
            
        else:
            consecutive_failures += 1  
            
            if consecutive_failures >= 10:
                log(f"❌ 触发安全保护：已连续失败 {consecutive_failures} 次，为防止浪费资源，任务强行终止！")
                break  
                
            log(f"❌ 本次注册未能成功提取账号信息，准备重试... (当前连续失败: {consecutive_failures}/10)")
            time.sleep(10)

    log("✨ 任务运行结束！")
    if success_accounts:
        log("以下为成功注册的账号列表：")
        for acc in success_accounts:
            log(acc)
    else:
        log("⚠ 本次运行未能成功注册任何账号。")

    if success_count < reg_count:
        log("❌ 有账号注册失败")
        sys.exit(1)

if __name__ == "__main__":
    main()
