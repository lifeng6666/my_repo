import json
import subprocess
import time
import random
from functools import partial
from urllib.parse import parse_qs
from DrissionPage import ChromiumPage, ChromiumOptions
from pathlib import Path
import requests

subprocess.Popen = partial(subprocess.Popen, encoding='utf-8', errors='ignore')

class AliV3:
    def __init__(self):
        self.captchaTicket = None
        self.CertifyId = None
        
        # 获取的验证参数
        self.verifyParam = None
        self.deviceToken = None

        self.headers = {
            'Accept': '*/*',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'Origin': 'https://account.dji.com',
            'Pragma': 'no-cache',
            'Referer': 'https://account.dji.com/',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'cross-site',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0',
            'sec-ch-ua': '"Microsoft Edge";v="143", "Chromium";v="143", "Not A(Brand";v="24"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
        }
        
        # 拦截到的数据容器
        self.intercepted_data = None

    def _setup_browser(self):
        """配置并启动 DrissionPage"""
        chrome_options = ChromiumOptions()
    
        # 核心修复点：随机分配端口，隔离上下文，避免与主程序的 DrissionPage 实例冲突
        chrome_options.auto_port()

        chrome_options.set_argument('--headless=new')  # 无头模式
        chrome_options.set_argument('--no-sandbox')    # 禁用沙箱模式
        chrome_options.set_argument('--window-size=415,900') # 页面大小设置为415*900
        
        # 防检测参数
        chrome_options.set_argument('--disable-blink-features=AutomationControlled')     # 禁用 Blink 功能
        chrome_options.set_pref('credentials_enable_service', False)     # 禁用凭证服务

        chrome_options.set_argument('--disable-gpu')     # 禁用 GPU 加速
        chrome_options.set_argument('--disable-dev-shm-usage')     # 禁用设备共享内存使用
        chrome_options.set_argument('--disable-extensions')     # 禁用扩展
        chrome_options.set_argument('--disable-logging')     # 禁用日志记录
        chrome_options.set_argument('--disable-background-networking')     # 禁用后台网络活动
        chrome_options.set_argument('--disable-default-apps')     # 禁用默认应用
        chrome_options.set_argument('--disable-sync')     # 禁用同步服务
        chrome_options.set_argument('--disable-translate')     # 禁用翻译服务
        chrome_options.set_argument('--no-first-run')     # 禁用首次运行
        chrome_options.set_argument('--safebrowsing-disable-auto-update')     # 禁用安全浏览自动更新
        chrome_options.set_argument('--ignore-certificate-errors')     # 忽略证书错误
        chrome_options.set_argument('--ignore-ssl-errors')     # 忽略 SSL 错误
        chrome_options.set_argument('--disable-web-security')     # 禁用 Web 安全
        chrome_options.set_argument('--allow-running-insecure-content')     # 允许运行不安全内容
        chrome_options.set_argument('--disable-features=IsolateOrigins,site-per-process')     # 禁用 IsolateOrigins 功能
        chrome_options.set_argument('--disable-site-isolation-trials')     # 禁用站点隔离试验
        chrome_options.set_argument('--single-process')     # 单进程模式
        chrome_options.set_argument('--disable-setuid-sandbox')     # 禁用 Setuid 沙箱
        chrome_options.set_argument('--disable-hang-monitor')     # 禁用挂起监控
        chrome_options.set_argument('--disable-popup-blocking')     # 禁用弹出窗口阻塞
        chrome_options.set_argument('--disable-prompt-on-repost')     # 禁用重新提交提示
        chrome_options.set_argument('--disable-backgrounding-occluded-windows')     # 禁用遮挡窗口后台运行
        chrome_options.set_argument('--disable-renderer-backgrounding')     # 禁用渲染器后台运行
        chrome_options.set_argument('--disable-ipc-flooding-protection')     # 禁用 IPC 洪水保护
        chrome_options.set_argument('--memory-pressure-off')     # 禁用内存压力保护
        chrome_options.set_argument('--js-flags=--max-old-space-size=512')     # 设置最大旧空间大小为 512MB
        chrome_options.set_timeouts(base=60, page_load=60, script=60)     # 超时时间设置为 60 秒
        
        # 随机 User-Agent
        ua_list = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
        ]
        chrome_options.set_user_agent(random.choice(ua_list))

        chrome_page = ChromiumPage(addr_or_opts=chrome_options)
        chrome_page.set.timeouts(base=60, page_load=60, script=60)
        
        return chrome_page

    def _safe_quit_browser(self, page):
        """安全关闭浏览器"""
        if page is None:
            return
        try:
            page.quit()
        except Exception:
            pass
        try:
            if hasattr(page, '_browser') and page._browser:
                page._browser.quit()
        except Exception:
            pass

    def _run_cdp_safe(self, page, method, **kwargs):
        """安全执行 CDP 命令"""
        max_retries = 3
        for i in range(max_retries):
            try:
                return page.run_cdp(method, **kwargs)
            except Exception as e:
                if i < max_retries - 1:
                    time.sleep(0.5)
                else:
                    raise e

    def _slide_logic(self, page):
        """基于JS脚本逻辑的Python实现"""
        try:
            slider = page.ele('#aliyunCaptcha-sliding-slider', timeout=5)
            wrapper = page.ele('#aliyunCaptcha-sliding-wrapper', timeout=5)
            
            if not slider or not wrapper:
                print("❌ 未找到滑块元素")
                return False

            # 获取位置信息
            slider_rect = slider.rect
            wrapper_rect = wrapper.rect
            
            # 计算起点 (slider 中心)
            start_x = slider_rect.location[0] + slider_rect.size[0] / 2
            start_y = slider_rect.location[1] + slider_rect.size[1] / 2
            
            # 需要滑动的距离
            distance_needed = wrapper_rect.size[0] - slider_rect.size[0]
            
            # 1. 过冲: 20-80px
            overshoot = 20 + random.random() * 60
            end_x = start_x + distance_needed + overshoot
            
            # 2. Y轴大幅漂移
            y_drift = (random.random() - 0.5) * 100
            end_y = start_y + y_drift
            
            # 3. 时间: 200ms - 500ms
            total_duration_ms = 200 + random.random() * 300
            start_time = time.time() * 1000
            
            print(f"📍 起点: ({int(start_x)}, {int(start_y)})")
            print(f"🏁 终点 (含过冲 {int(overshoot)}px): ({int(end_x)}, {int(end_y)})")
            
            # 4. 执行滑动 (使用 CDP Input.dispatchMouseEvent 以获得更细粒度的控制)
            # MouseDown
            self._run_cdp_safe(page, 'Input.dispatchMouseEvent', type='mousePressed', x=start_x, y=start_y, button='left', clickCount=1)
            
            while True:
                now = time.time() * 1000
                elapsed = now - start_time
                progress = elapsed / total_duration_ms
                
                if progress > 1:
                    progress = 1
                
                # EaseOutQuart
                ease = 1 - pow(1 - progress, 4)
                current_x = start_x + (end_x - start_x) * ease
                current_y_drift = y_drift * ease
                jitter = (random.random() - 0.5) * 6
                current_y = start_y + current_y_drift + jitter
                
                # MouseMove
                self._run_cdp_safe(page, 'Input.dispatchMouseEvent', type='mouseMoved', x=current_x, y=current_y)
                
                if progress >= 1:
                    break
                
                # 随机间隔 5-10ms
                time.sleep((5 + random.random() * 5) / 1000)
            
            # 4.3 极短停顿
            time.sleep(random.random() * 0.05)
            
            # 4.4 MouseUp (在过冲位置直接松开)
            self._run_cdp_safe(page, 'Input.dispatchMouseEvent', type='mouseReleased', x=end_x, y=end_y, button='left', clickCount=1)
            print(f"🖱️ 滑动完成，耗时 {int(time.time()*1000 - start_time)}ms")
            
            return True

        except Exception as e:
            print(f"滑动过程出错: {e}")
            return False

    def getCap(self):
        page = None
        local_html_path = Path(__file__).parent / 'aliv3.html'
        target_url = local_html_path.as_uri() + "?prefix=1tbpug&SceneId=6mw4mrmg"
        max_browser_retries = 3
        
        for browser_attempt in range(1, max_browser_retries + 1):
            try:
                print(f"\n🌐 浏览器实例 {browser_attempt}/{max_browser_retries}")
                
                self._safe_quit_browser(page)
                time.sleep(1)
                
                page = self._setup_browser()
                
                # 定义拦截回调
                def on_request_paused(**kwargs):
                    try:
                        req_id = kwargs['requestId']
                        request = kwargs.get('request', {})
                        url = request.get('url', '')
                        
                        # 目标：拦截 https://1tbpug.captcha-open.aliyuncs.com/
                        if 'captcha-open.aliyuncs.com' in url:
                            # 获取 Payload
                            post_data = request.get('postData')
                            
                            # 只有当请求包含 CaptchaVerifyParam 时才拦截
                            if post_data and 'CaptchaVerifyParam' in post_data:
                                print(f"🛑 拦截到目标验证请求: {url}")
                                self.intercepted_data = post_data
                                print("✅ 已提取请求载荷")
                                try:
                                    page.run_cdp('Fetch.failRequest', requestId=req_id, errorReason='Aborted')
                                except Exception:
                                    pass
                            else:
                                # 放行初始化请求或其他非验证请求
                                try:
                                    page.run_cdp('Fetch.continueRequest', requestId=req_id)
                                except Exception:
                                    pass
                        else:
                            # 放行其他请求
                            try:
                                page.run_cdp('Fetch.continueRequest', requestId=req_id)
                            except Exception:
                                pass
                    except Exception as e:
                        # 防止回调报错影响流程
                        try:
                            page.run_cdp('Fetch.continueRequest', requestId=kwargs['requestId'])
                        except:
                            pass

                # 启用 Fetch 拦截
                self._run_cdp_safe(page, 'Fetch.enable', patterns=[{'urlPattern': '*'}])
                # 设置回调
                page.driver.set_callback('Fetch.requestPaused', on_request_paused)
                
                max_retries = 10
                for attempt in range(1, max_retries + 1):
                    print(f"\n🔄 第 {attempt}/{max_retries} 次尝试获取验证码...")
                    self.intercepted_data = None
                    self.verifyParam = None
                    self.deviceToken = None
                    self.CertifyId = None
                    
                    try:
                        # 打开/刷新页面
                        page.get(target_url, timeout=30)
                    except Exception as e:
                        print(f"页面加载异常: {e}")
                        if 'timeout' in str(e).lower() or 'stuck' in str(e).lower():
                            print("检测到浏览器超时，准备重启浏览器...")
                            break
                        continue
                    
                    time.sleep(1)
                    
                    # 等待滑块元素出现
                    # 使用 ele_displayed 确保元素可见
                    try:
                        if not page.wait.ele_displayed('#aliyunCaptcha-sliding-slider', timeout=10):
                            print("加载滑块超时，刷新重试...")
                            continue
                    except Exception as e:
                        print(f"等待滑块元素异常: {e}")
                        if 'timeout' in str(e).lower() or 'stuck' in str(e).lower():
                            print("检测到浏览器超时，准备重启浏览器...")
                            break
                        continue
                    
                    time.sleep(0.5)
                    
                    # 执行自动化过滑块逻辑
                    try:
                        if not self._slide_logic(page):
                            continue
                    except Exception as e:
                        print(f"滑块逻辑异常: {e}")
                        if 'timeout' in str(e).lower() or 'stuck' in str(e).lower():
                            print("检测到浏览器超时，准备重启浏览器...")
                            break
                        continue
                    
                    # 等待拦截数据 (最多10秒)
                    print("⏳ 等待拦截 CaptchaVerifyParam...")
                    wait_start = time.time()
                    while time.time() - wait_start < 10:
                        if self.intercepted_data:
                            break
                        time.sleep(0.1)
                    
                    if not self.intercepted_data:
                        print("❌ 超时未拦截到验证数据，重试...")
                        continue
                    
                    # 解析拦截到的数据
                    try:
                        # 数据格式: AccessKeyId=...&CaptchaVerifyParam=...
                        parsed = parse_qs(self.intercepted_data)
                        if 'CaptchaVerifyParam' in parsed:
                            # CaptchaVerifyParam 是 URL 编码的 JSON 字符串
                            verify_param_json = parsed['CaptchaVerifyParam'][0]
                            json_data = json.loads(verify_param_json)
                            
                            self.verifyParam = json_data.get('data')
                            self.deviceToken = json_data.get('deviceToken')
                            self.CertifyId = json_data.get('certifyId')
                            
                            print("🎉 成功解析验证参数")
                            
                            # 立即调用 check-ali-captcha
                            check_res = self.Sumbit_All()
                            
                            # 检查验证结果
                            if check_res and check_res.get('success') and check_res.get('code') == 200:
                                res_data = check_res.get('data', {})
                                if res_data.get('checkSuccess') is False:
                                    print(f"❌ 滑块验证失败: {res_data.get('errMessage')}，重试...")
                                    continue
                                elif 'captchaTicket' in res_data:
                                    print("✅ 滑块验证成功！")
                                    self._safe_quit_browser(page)
                                    return True
                            
                            print(f"❌ 接口验证返回异常: {check_res}，重试...")
                        else:
                            print("❌ 拦截数据中缺失 CaptchaVerifyParam，重试...")
                    
                    except Exception as e:
                        print(f"❌ 解析数据或验证异常: {e}，重试...")
                        continue
                else:
                    continue
                
                print("🔄 浏览器需要重启...")
                continue

            except Exception as e:
                print(f"浏览器实例异常: {e}")
                self._safe_quit_browser(page)
                page = None
                time.sleep(2)
                continue

        print("❌ 达到最大重试次数，获取滑块验证失败。")
        self._safe_quit_browser(page)
        return False

    def Sumbit_All(self):
        if not self.verifyParam:
            print("Missing verifyParam, skipping Sumbit_All")
            return None

        _data = self.verifyParam
        deviceToekn = self.deviceToken
        print('deviceToekn', deviceToekn)

        cookies = {
            'device_id': 'c7d0a5f4b554477fae0e1ba29f84fb63',
            'HWWAFSESID': 'bcd7d8b4f625fb57ac',
            'HWWAFSESTIME': '1766299533105',
            'Qs_lvt_290854': '1766237893%2C1766299553',
            'Qs_pv_290854': '2499244294467079700%2C852781256760664000',
            '__sameSiteCheck__': '1',
            '_c_WBKFRo': '03ctatXDH7wXL1GIRpFWI9AUfuGhSVMzyOf5q8oX',
            '_nb_ioWEgULi': '',
        }

        headers = {
            'accept': 'application/json, text/plain, */*',
            'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
            'cache-control': 'no-cache, no-store, must-revalidate',
            'content-type': 'application/json',
            'origin': 'https://passport.jlc.com',
            'referer': 'https://passport.jlc.com/window/login?appId=JLC_PORTAL_PC&redirectUrl=https%3A%2F%2Fwww.jlc.com%2F',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0',
            'secretkey': '35616236663038352d643366382d343131662d396239622d366439643132653639373764',
            'x-jlc-clientuuid': '445de653-7a24-4242-88dd-0878479726aa-1766237894098',
        }

        captcha_verify_param = {
            "sceneId": "6mw4mrmg",
            "certifyId": self.CertifyId,
            "deviceToken": deviceToekn,
            "data": _data
        }

        captcha_verify_param_str = json.dumps(captcha_verify_param, separators=(',', ':'))

        json_data = {
            'captchaVerifyParam': captcha_verify_param_str,
            'sceneType': 'register',
            'aliyunSceneId': '6mw4mrmg',
        }

        try:
            response = requests.post(
                'https://passport.jlc.com/api/cas/captcha/v2/check-ali-captcha',
                cookies=cookies,
                headers=headers,
                json=json_data,
                timeout=30
            )

            print(f"Check API Status: {response.status_code}")
            print("Check API Response:", response.text)
            
            resp_json = response.json()
            
            try:
                # 尝试获取 ticket
                if resp_json.get('success') and resp_json.get('data', {}).get('captchaTicket'):
                    self.captchaTicket = resp_json['data']['captchaTicket']
                    print("\nSUCCESS: Obtained CaptchaTicket:")
                    print(self.captchaTicket)
            except Exception as e:
                print("Failed to get captchaTicket from response:", e)

            return resp_json

        except Exception as e:
            print(f"Sumbit_All Error: {e}")
            return None

    def main(self):
        # 使用 DrissionPage 获取验证码
        if self.getCap():
            pass
        else:
            print("验证码获取失败，无法继续。")


if __name__ == '__main__':
    ali = AliV3()
    ali.main()
