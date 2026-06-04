import sys
import time
import json
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

def log(message):
    """将日志输出到 sys.stderr"""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    sys.stderr.write(f"[{timestamp}] {message}\n")
    sys.stderr.flush()

def main():
    # 1. 配置 Chrome 选项
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")  # 新版无头模式
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--window-size=1920,1080")
    # 添加 User-Agent 防止被简单的反爬虫拦截
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    # 初始化 Driver
    try:
        driver = webdriver.Chrome(options=chrome_options)
    except Exception as e:
        log(f"无法启动 WebDriver: {e}")
        sys.exit(1)

    target_url = "https://passport.jlc.com"
    api_url = "https://passport.jlc.com/api/cas/login/get-init-session"
    lsid_value = None
    max_retries = 3

    try:
        # 循环尝试，最多3次
        for attempt in range(1, max_retries + 1):
            log(f"--- 开始第 {attempt} 次尝试 ---")

            try:
                # 2. 打开网页或刷新
                if attempt == 1:
                    log(f"正在打开网页: {target_url}")
                    driver.get(target_url)
                else:
                    log("未获取到lsId，正在刷新网页...")
                    driver.refresh()

                # 3. 等待网页主体完全加载
                log("等待网页主体加载...")
                WebDriverWait(driver, 15).until(
                    EC.presence_of_element_located((By.TAG_NAME, "body"))
                )
                log("网页加载完成")

                # 4. 控制台发送请求
                log(f"正在通过控制台发送 POST 请求: {api_url}")
                
                # 使用 execute_async_script 以便等待 fetch 请求完成
                # 请求主体: {"appId":"JLC_OSHWHUB","clientType":"PC-WEB"}
                js_script = """
                var done = arguments[0];
                var url = 'https://passport.jlc.com/api/cas/login/get-init-session';
                var payload = {"appId":"JLC_OSHWHUB","clientType":"PC-WEB"};

                fetch(url, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify(payload)
                })
                .then(response => {
                    console.log('API请求成功');
                    // 给予浏览器一点时间处理 Set-Cookie 标头
                    setTimeout(() => { done("success"); }, 1000);
                })
                .catch(error => {
                    console.error('API请求失败', error);
                    done("error");
                });
                """
                
                # 设置脚本超时时间
                driver.set_script_timeout(10)
                status = driver.execute_async_script(js_script)
                log(f"请求执行状态: {status}")

                # 5. 在响应标头(体现为浏览器Cookie)里查找 lsid
                log("正在检查 Cookies 中的 lsId...")
                
                # 获取特定 cookie
                cookie_item = driver.get_cookie("lsId")
                
                if cookie_item:
                    lsid_value = cookie_item.get("value")
                    log(f"成功获取到 lsId: {lsid_value}")
                    break  # 成功获取，跳出循环
                else:
                    log("当前尝试未找到 lsId Cookie。")

            except Exception as e:
                log(f"第 {attempt} 次尝试发生错误: {str(e)}")
                time.sleep(2) # 出错后稍作等待

        # 循环结束后的处理
        if lsid_value:
            # 6. 成功获取，按格式输出到 sys.stdout
            print("cookies = {")
            print(f"    'lsId': '{lsid_value}',")
            print("}")
            print("headers = {")
            print("}")
            sys.exit(0)
        else:
            # 3次都没有成功
            log("已达到最大重试次数，未能获取 lsId。")
            sys.exit(1)

    finally:
        # 清理资源
        if 'driver' in locals():
            driver.quit()

if __name__ == "__main__":
    main()
