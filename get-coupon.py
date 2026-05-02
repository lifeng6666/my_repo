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
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from Utils import pwdEncrypt



def log(msg, show_time=True):
    """带时间戳的日志输出"""
    if show_time:
        full_msg = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    else:
        full_msg = msg
    print(full_msg, flush=True)


def is_on_3dp_site(url):
    """检查 URL 的域名是否为 jlc-3dp"""
    try:
        hostname = urlparse(url).hostname or ''
        return hostname.endswith('jlc-3dp.cn')
    except:
        return False


# =====================================================================
#  浏览器创建
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

    # --- 启用性能日志，用于捕获网络请求头中的 Secretkey ---
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


# =====================================================================
#  登录流程
# =====================================================================
def call_aliv3_with_timeout(timeout_seconds=180, max_retries=18):
    """调用 AliV3-login.py 获取 captchaTicket - 最多重试18次"""
    for attempt in range(max_retries):
        log(f"📞 正在调用 登录脚本 获取 captchaTicket (尝试 {attempt + 1}/{max_retries})...")

        process = None
        output_lines = []

        try:
            if not os.path.exists('AliV3-login.py'):
                log("❌ 错误: 找不到登录依赖 AliV3-login.py")
                log("❌ 登录脚本存在异常")
                sys.exit(1)

            process = subprocess.Popen(
                [sys.executable, 'AliV3-login.py'],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                errors='ignore'
            )

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
                    except:
                        pass
                    break

                try:
                    line = q.get(timeout=0.5)
                except queue.Empty:
                    if process.poll() is not None and not t.is_alive():
                        break
                    continue

                if line:
                    output_lines.append(line)

                    if wait_for_next_line:
                        captcha_ticket = line.strip()
                        log(f"✅ 成功获取 captchaTicket")
                        try:
                            process.terminate()
                            process.wait(timeout=5)
                        except:
                            pass
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
                                except:
                                    pass
                                return captcha_ticket
                        except:
                            pass

            if not captcha_ticket:
                if process and process.poll() is None:
                    try:
                        process.kill()
                        process.wait(timeout=5)
                    except:
                        pass

                if attempt < max_retries - 1:
                    log(f"⚠ 未获取到CaptchaTicket，等待5秒后第 {attempt + 2} 次重试...")
                    time.sleep(5)
            else:
                return captcha_ticket

        except Exception as e:
            log(f"❌ 调用登录脚本异常: {e}")

            if process and process.poll() is None:
                try:
                    process.kill()
                    process.wait(timeout=5)
                except:
                    pass

            if attempt < max_retries - 1:
                log(f"⚠ 未获取到CaptchaTicket，等待5秒后第 {attempt + 2} 次重试...")
                time.sleep(5)

    log("❌ 登录脚本存在异常")
    sys.exit(1)


def send_request_via_browser(driver, url, method='POST', body=None):
    """通过浏览器控制台发送请求"""
    try:
        if body:
            body_str = json.dumps(body, ensure_ascii=False)
            js_code = """
            var url = arguments[0];
            var bodyData = arguments[1];
            var callback = arguments[2];
            fetch(url, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Accept': 'application/json, text/plain, */*',
                    'AppId': 'JLC_PORTAL_PC',
                    'ClientType': 'PC-WEB'
                },
                body: bodyData,
                credentials: 'include'
            }).then(response => {
                if (!response.ok) { return JSON.stringify({error: "HTTP Error " + response.status}); }
                return response.json().then(data => JSON.stringify(data));
            }).then(data => callback(data)).catch(error => callback(JSON.stringify({error: error.toString()})));
            """
            result = driver.execute_async_script(js_code, url, body_str)
        else:
            js_code = """
            var url = arguments[0];
            var callback = arguments[1];
            fetch(url, {
                method: 'GET',
                headers: {'Content-Type': 'application/json', 'Accept': 'application/json, text/plain, */*', credentials: 'include'}
            }).then(response => response.json().then(data => JSON.stringify(data))).then(data => callback(data)).catch(error => callback(JSON.stringify({error: error.toString()})));
            """
            result = driver.execute_async_script(js_code, url)
        try:
            return json.loads(result)
        except json.JSONDecodeError:
            return None
    except Exception as e:
        log(f"❌ 浏览器请求执行失败: {e}")
        return None


def perform_init_session(driver, max_retries=3):
    """执行 Session 初始化"""
    for i in range(max_retries):
        log(f"📡 初始化会话 (尝试 {i + 1}/{max_retries})...")
        response = send_request_via_browser(driver, "https://passport.jlc.com/api/cas/login/get-init-session", 'POST', {"appId": "JLC_PORTAL_PC", "clientType": "PC-WEB"})
        if response and response.get('success') == True and response.get('code') == 200:
            log("✅ 初始化会话成功")
            return True
        else:
            if i < max_retries - 1:
                log(f"⚠ 初始化会话失败，等待2秒后重试...")
                time.sleep(2)
    return False


def login_with_password(driver, username, password, captcha_ticket):
    """登录"""
    url = "https://passport.jlc.com/api/cas/login/with-password"
    try:
        encrypted_username = pwdEncrypt(username)
        encrypted_password = pwdEncrypt(password)
    except Exception as e:
        log(f"❌ SM2加密失败: {e}")
        return 'other_error', None

    body = {'username': encrypted_username, 'password': encrypted_password, 'isAutoLogin': False, 'captchaTicket': captcha_ticket}
    log(f"📡 发送登录请求...")
    response = send_request_via_browser(driver, url, 'POST', body)
    if not response: return 'other_error', None

    if response.get('success') == True and response.get('code') == 2017: return 'success', response
    if response.get('code') == 10208: return 'password_error', response
    return 'other_error', response


def verify_login_on_member_page(driver, max_retries=3):
    """验证登录"""
    for attempt in range(max_retries):
        log(f"🔍 验证登录状态 ({attempt + 1}/{max_retries})...")
        try:
            try:
                driver.get("https://member.jlc.com/")
            except TimeoutException:
                log("⚠ 验证页面加载超时，停止加载并尝试检查内容...")
                driver.execute_script("window.stop();")

            WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            time.sleep(3)
            page_source = driver.page_source
            if "客编" in page_source or "customerCode" in page_source:
                log(f"✅ 验证登录成功")
                return True
        except Exception as e:
            log(f"⚠ 验证登录失败: {e}")
        if attempt < max_retries - 1:
            log(f"⏳ 等待2秒后重试...")
            time.sleep(2)
    return False


def perform_login_flow(driver, username, password, max_retries=3):
    """执行完整的登录流程（包括Session初始化、登录、验证）"""
    session_fail_count = 0

    for login_attempt in range(max_retries):
        log(f"🔐 开始登录流程 (尝试 {login_attempt + 1}/{max_retries})...")

        try:
            try:
                driver.get("https://passport.jlc.com")
            except TimeoutException:
                log("⚠ 登录页面加载超时，尝试停止加载继续...")
                driver.execute_script("window.stop();")

            WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.TAG_NAME, "body")))

            if not perform_init_session(driver):
                session_fail_count += 1
                if session_fail_count >= 3:
                    log("❌ 浏览器环境存在异常")
                    raise Exception("初始化 Session 失败")
                raise Exception("初始化 Session 失败")

            session_fail_count = 0

            captcha_ticket = call_aliv3_with_timeout()
            if not captcha_ticket:
                raise Exception("获取 CaptchaTicket 失败")

            status, login_res = login_with_password(driver, username, password, captcha_ticket)
            if status == 'password_error':
                return 'password_error'
            if status != 'success':
                raise Exception("登录失败")

            if not verify_login_on_member_page(driver):
                raise Exception("登录验证失败")

            log("✅ 登录流程完成")
            return 'success'

        except Exception as e:
            log(f"❌ 登录流程异常: {e}")
            if login_attempt < max_retries - 1:
                log(f"⏳ 重试登录流程...")
                time.sleep(3)
            else:
                log(f"❌ 登录流程已达最大重试次数")
                return 'login_failed'

    return 'login_failed'


# =====================================================================
#  领券专用函数
# =====================================================================

def extract_secretkey_from_logs(driver):
    """从浏览器性能日志中提取任意请求标头里的 Secretkey"""
    try:
        logs = driver.get_log('performance')
        for entry in logs:
            try:
                log_entry = json.loads(entry['message'])
                message = log_entry.get('message', {})
                method = message.get('method', '')
                params = message.get('params', {})

                if method == 'Network.requestWillBeSent':
                    headers = params.get('request', {}).get('headers', {})
                    for key, value in headers.items():
                        if key.lower() == 'secretkey' and value:
                            return value

                elif method == 'Network.requestWillBeSentExtraInfo':
                    headers = params.get('headers', {})
                    for key, value in headers.items():
                        if key.lower() == 'secretkey' and value:
                            return value
            except:
                continue
    except Exception as e:
        log(f"⚠ 读取性能日志异常: {e}")
    return None


def send_coupon_request(driver, url, body_str, content_type='application/json', secret_key=None):
    """通过浏览器在当前页面上下文中发送领券 POST 请求，自动附加 XSRF-TOKEN"""
    try:
        headers = {'Content-Type': content_type}
        if secret_key:
            headers['Secretkey'] = secret_key
        headers_json = json.dumps(headers)

        js_code = """
        var url = arguments[0];
        var bodyData = arguments[1];
        var headersObj = JSON.parse(arguments[2]);
        var callback = arguments[3];

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
            headersObj['X-XSRF-TOKEN'] = xsrfToken;
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
        result = driver.execute_async_script(js_code, url, body_str, headers_json)

        if result:
            try:
                return json.loads(result)
            except json.JSONDecodeError:
                log(f"⚠ 响应非JSON: {str(result)[:200]}")
                return None
        return None
    except Exception as e:
        log(f"❌ 发送领券请求失败: {e}")
        return None


def open_page_and_wait_sso(driver, url):
    """打开页面并等待 10 秒获取 SSO 登录状态"""
    log(f"🔗 打开页面: {url.split('?')[0]}...")
    try:
        driver.get(url)
    except TimeoutException:
        log("⚠ 页面加载超时，停止加载继续...")
        try:
            driver.execute_script("window.stop();")
        except:
            pass
    log("⏳ 等待10秒获取SSO登录状态...")
    time.sleep(10)


def refresh_page_and_wait(driver):
    """刷新当前页面并等待 10 秒"""
    try:
        driver.refresh()
    except TimeoutException:
        try:
            driver.execute_script("window.stop();")
        except:
            pass
    time.sleep(10)


def clear_performance_logs(driver):
    """清空已有的性能日志"""
    try:
        driver.get_log('performance')
    except:
        pass


def navigate_3dp_via_passport(driver):


    passport_url = (
        "https://passport.jlc.com/login?appId=JLC_3DP"
        "&redirectUrl=https%3A%2F%2Fwww.jlc-3dp.cn%2Fbenefit"
        "%3Futm_a%3D3D1001%26utm_b%3Dppc001%26bd_vid%3D10498918222589111581"
        "&backCode=1"
    )

    log(f"🔗 打开 passport 跳转页...")
    try:
        driver.get(passport_url)
    except TimeoutException:
        log("⚠ 页面加载超时，停止加载继续...")
        try:
            driver.execute_script("window.stop();")
        except:
            pass

    try:
        WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    except:
        pass
    time.sleep(5)

    log("🔍 等待「嘉立创集团用户登录中心」页面并点击「进入系统」...")
    start_time = time.time()
    max_wait = 60
    clicked = False

    while time.time() - start_time < max_wait:
        current_url = driver.current_url

        if is_on_3dp_site(current_url):
            log(f"✅ 已到达目标页面: {current_url.split('?')[0]}")
            break

        try:
            title = driver.title
        except:
            title = ""

        if "嘉立创集团用户登录中心" in title and not clicked:
            try:
                enter_btn = driver.find_element(By.XPATH, "//button//span[contains(., '进入系统')]")
                driver.execute_script("arguments[0].click();", enter_btn)
                log("✅ 已点击「进入系统」按钮")
                clicked = True
                time.sleep(3)
                continue
            except NoSuchElementException:
                log("⚠ 页面是登录中心，但暂未找到按钮，继续等待...")
            except Exception as e:
                log(f"⚠ 点击异常: {e}")

        time.sleep(1)

    final_url = driver.current_url
    if is_on_3dp_site(final_url):
        log(f"✅ 成功到达领券页面: {final_url.split('?')[0]}")
    else:
        log(f"⚠ 未能到达领券页面，当前URL: {final_url.split('?')[0]}")
        log("⚠ 尝试直接打开目标页面...")
        target_url = "https://www.jlc-3dp.cn/benefit?utm_a=3D1001&utm_b=ppc001&bd_vid=10498918222589111581"
        try:
            driver.get(target_url)
        except TimeoutException:
            try:
                driver.execute_script("window.stop();")
            except:
                pass

    log("⏳ 等待页面资源加载 (10s)...")
    time.sleep(10)


# =====================================================================
#  三张券的领取逻辑
# =====================================================================

def claim_3dp_30_20(driver, coupon_result):
    """一、3D打印30-20券"""
    coupon_name = "3D打印30-20券"
    api_url = "https://www.jlc-3dp.cn/3dp/coupon/receiveCouponsV2"
    body = json.dumps({
        "operationPromotionEnum": "ACTIVITY_TYPE_TRIPLE_CHOICE_2025_05_04",
        "couponIdList": ["06A5A456D2AD803E2873BD3371046C7DF9AF0FFB675570D4"]
    })

    log(f"🎫 === 开始领取{coupon_name} ===")

    clear_performance_logs(driver)
    navigate_3dp_via_passport(driver)
    secret_key = extract_secretkey_from_logs(driver)

    if secret_key:
        log(f"✅ 成功获取 Secretkey")
    else:
        log(f"⚠ 未获取到 Secretkey，仍将尝试发包...")

    last_message = None

    for attempt in range(3):
        if attempt > 0:
            log(f"⏳ 刷新页面等待10秒后第 {attempt + 1} 次重试...")
            clear_performance_logs(driver)
            refresh_page_and_wait(driver)
            new_key = extract_secretkey_from_logs(driver)
            if new_key:
                secret_key = new_key
                log(f"✅ 重新获取 Secretkey 成功")

        response = send_coupon_request(driver, api_url, body, 'application/json', secret_key)

        if response is None or '_fetch_error' in response:
            err = response.get('_fetch_error', '请求失败') if response else '请求失败'
            log(f"⚠ 请求异常: {err}")
            last_message = err
            continue

        success = response.get('success')
        code = response.get('code')
        message = response.get('message') or ''

        if success == True and code == 200:
            log(f"✅ {coupon_name}领取成功")
            coupon_result[coupon_name] = {'success': True}
            return

        if success == False and code == 500:
            if '已领取' in message or '最多可领券' in message:
                log(f"⚠ {coupon_name}: {message}")
                coupon_result[coupon_name] = {'success': False, 'reason': message}
                return

        log(f"⚠ 未预期的响应: {json.dumps(response, ensure_ascii=False)[:200]}")
        last_message = message or json.dumps(response, ensure_ascii=False)[:100]

    log(f"❌ {coupon_name}领取失败（已达最大重试次数）")
    coupon_result[coupon_name] = {'success': False, 'reason': last_message or '重试后仍失败'}


def claim_3dp_material(driver, coupon_result):
    """二、3D打印高值材料券"""
    coupon_name = "3D打印高值材料券"
    page_url = "https://www.jlc-3dp.cn/freePrint"
    api_url = "https://www.jlc-3dp.cn/3dp/coupon/receiveCoupons"
    body = "operationPromotionEnum=FREE_NEW_MULTI_MATERIAL"
    content_type = "application/x-www-form-urlencoded"

    log(f"🎫 === 开始领取{coupon_name} ===")

    clear_performance_logs(driver)
    open_page_and_wait_sso(driver, page_url)
    secret_key = extract_secretkey_from_logs(driver)

    if secret_key:
        log(f"✅ 成功获取 Secretkey")
    else:
        log(f"⚠ 未获取到 Secretkey，仍将尝试发包...")

    last_message = None

    for attempt in range(3):
        if attempt > 0:
            log(f"⏳ 刷新页面等待10秒后第 {attempt + 1} 次重试...")
            clear_performance_logs(driver)
            refresh_page_and_wait(driver)
            new_key = extract_secretkey_from_logs(driver)
            if new_key:
                secret_key = new_key
                log(f"✅ 重新获取 Secretkey 成功")

        response = send_coupon_request(driver, api_url, body, content_type, secret_key)

        if response is None or '_fetch_error' in response:
            err = response.get('_fetch_error', '请求失败') if response else '请求失败'
            log(f"⚠ 请求异常: {err}")
            last_message = err
            continue

        success = response.get('success')
        code = response.get('code')
        message = response.get('message') or ''

        if success == True and code == 200:
            log(f"✅ {coupon_name}领取成功")
            coupon_result[coupon_name] = {'success': True}
            return

        if success == False and code == 10003:
            reason = "当前账号已经领取过免费券"
            log(f"⚠ {coupon_name}: {reason}")
            coupon_result[coupon_name] = {'success': False, 'reason': reason}
            return

        if success == False and code == 10002:
            reason = "当前账号未绑定微信无法领券"
            log(f"⚠ {coupon_name}: {reason}")
            coupon_result[coupon_name] = {'success': False, 'reason': reason}
            return

        log(f"⚠ 未预期的响应: {json.dumps(response, ensure_ascii=False)[:200]}")
        last_message = message or json.dumps(response, ensure_ascii=False)[:100]

    log(f"❌ {coupon_name}领取失败（已达最大重试次数）")
    coupon_result[coupon_name] = {'success': False, 'reason': last_message or '重试后仍失败'}


def claim_fpc_coupons(driver, coupon_result):
    """三、FPC新客两张券"""
    page_url = "https://jlc-fpc.com/promotional"
    api_url = "https://jlc-fpc.com/api/fpcPortal/coupon/receiveFpcPromotionActivityCoupon"

    coupons = [
        {
            'name': "FPC新客免费打样券",
            'body': json.dumps({
                "couponIdList": ["283858320353345537"],
                "promotionId": "320383501245042690"
            })
        },
        {
            'name': "FPC 100元优惠券",
            'body': json.dumps({
                "couponIdList": ["460339879753932802"],
                "promotionId": "320383501245042690"
            })
        }
    ]

    log(f"🎫 === 开始领取FPC新客券 ===")
    open_page_and_wait_sso(driver, page_url)

    for coupon_info in coupons:
        coupon_name = coupon_info['name']
        
        if coupon_result.get(coupon_name, {}).get('success'):
            continue
            
        body = coupon_info['body']

        log(f"📤 领取 {coupon_name}...")

        last_message = None
        claimed = False

        for attempt in range(3):
            if attempt > 0:
                log(f"⏳ 刷新页面等待10秒后第 {attempt + 1} 次重试...")
                refresh_page_and_wait(driver)

            response = send_coupon_request(driver, api_url, body)

            if response is None or '_fetch_error' in response:
                err = response.get('_fetch_error', '请求失败') if response else '请求失败'
                log(f"⚠ 请求异常: {err}")
                last_message = err
                continue

            success = response.get('success')
            code = response.get('code')
            message = response.get('message') or ''

            if success == True and code == 200:
                log(f"✅ {coupon_name}领取成功")
                coupon_result[coupon_name] = {'success': True}
                claimed = True
                break

            if success == False and code == 207:
                reason = "当前账号已经领取过"
                log(f"⚠ {coupon_name}: {reason}")
                coupon_result[coupon_name] = {'success': False, 'reason': reason}
                claimed = True
                break

            log(f"⚠ 未预期的响应: {json.dumps(response, ensure_ascii=False)[:200]}")
            last_message = message or json.dumps(response, ensure_ascii=False)[:100]

        if not claimed:
            log(f"❌ {coupon_name}领取失败（已达最大重试次数）")
            coupon_result[coupon_name] = {'success': False, 'reason': last_message or '重试后仍失败'}


# =====================================================================
#  单账号处理 & 主函数
# =====================================================================

def process_single_account(username, password, account_index, total_accounts):
    """处理单个账号的完整领券流程"""
    coupon_names_ordered = [
        "3D打印30-20券",
        "3D打印高值材料券",
        "FPC新客免费打样券",
        "FPC 100元优惠券"
    ]
    coupon_result = {}
    for name in coupon_names_ordered:
        coupon_result[name] = {'success': False, 'reason': '未执行'}

    max_account_retries = 3

    for retry in range(max_account_retries):
        if retry > 0:
            log(f"🔄 检测到账号处理异常，准备进行全流程重试 ({retry + 1}/{max_account_retries})...")

        driver = None
        user_data_dir = tempfile.mkdtemp()
        success_this_round = True

        try:
            log(f"🌐 启动浏览器 (账号 {account_index}/{total_accounts})...")
            driver = create_chrome_driver(user_data_dir)

            # ====== 阶段 1: 登录 ======
            login_status = perform_login_flow(driver, username, password, max_retries=3)

            if login_status == 'password_error':
                reason = '账号或密码不正确'
                for name in coupon_names_ordered:
                    if coupon_result[name].get('reason') == '未执行' or '异常' in coupon_result[name].get('reason', ''):
                        coupon_result[name] = {'success': False, 'reason': reason}
                return {'username': username, 'index': account_index, 'coupons': coupon_result}

            if login_status != 'success':
                raise Exception("登录流程失败")

            # ====== 阶段 2: 依次领取三组券 ======
            if not coupon_result["3D打印30-20券"].get('success'):
                claim_3dp_30_20(driver, coupon_result)
                
            if not coupon_result["3D打印高值材料券"].get('success'):
                claim_3dp_material(driver, coupon_result)
                
            if not coupon_result["FPC新客免费打样券"].get('success') or not coupon_result["FPC 100元优惠券"].get('success'):
                claim_fpc_coupons(driver, coupon_result)

        except Exception as e:
            log(f"❌ 账号处理异常: {e}")
            success_this_round = False
            for name in coupon_names_ordered:
                if not coupon_result[name].get('success'):
                    coupon_result[name] = {'success': False, 'reason': f'异常: {str(e)[:80]}'}
        finally:
            if driver:
                try:
                    driver.quit()
                    log("🔒 浏览器已关闭")
                except:
                    pass
            if os.path.exists(user_data_dir):
                try:
                    shutil.rmtree(user_data_dir, ignore_errors=True)
                except:
                    pass

        if success_this_round:
            break

    return {'username': username, 'index': account_index, 'coupons': coupon_result}


def main():
    if len(sys.argv) < 3:
        print("用法: python lingquan.py 账号1,账号2... 密码1,密码2...")
        sys.exit(1)

    usernames = sys.argv[1].split(',')
    passwords = sys.argv[2].split(',')

    if len(usernames) != len(passwords):
        log("❌ 账号密码数量不匹配")
        sys.exit(1)

    log(f"检测到 {len(usernames)} 个账号需要领券", show_time=False)

    all_results = []

    for i, (u, p) in enumerate(zip(usernames, passwords), 1):
        log(f"{'='*50}", show_time=False)
        log(f"🚀 正在处理账号 {i}/{len(usernames)}", show_time=False)
        log(f"{'='*50}", show_time=False)
        result = process_single_account(u, p, i, len(usernames))
        all_results.append(result)
        if i < len(usernames):
            log("⏳ 等待5秒后处理下一个账号...")
            time.sleep(5)

    # ===================== 汇总输出 =====================
    coupon_names_ordered = [
        "3D打印30-20券",
        "3D打印高值材料券",
        "FPC新客免费打样券",
        "FPC 100元优惠券"
    ]

    log(f"{'='*50}", show_time=False)
    log("📊 领券结果总结", show_time=False)
    log(f"{'='*50}", show_time=False)

    for result in all_results:
        log(f"账号{result['index']}({result['username']})", show_time=False)
        for name in coupon_names_ordered:
            info = result['coupons'].get(name, {'success': False, 'reason': '未执行'})
            if info['success']:
                status_str = "✔️"
            else:
                status_str = f"失败，原因:{info.get('reason', '未知')}"
            log(f"  {name}：{status_str}", show_time=False)

    log(f"{'='*50}", show_time=False)
    sys.exit(0)


if __name__ == "__main__":
    main()
