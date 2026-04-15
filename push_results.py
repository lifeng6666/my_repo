import os
import sys
import requests
from datetime import datetime

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def get_push_title():
    return "🎉 嘉立创注册任务执行完成"

def push_to_telegram(text, file_path=None):
    """推送到Telegram，支持发送txt文件"""
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
    chat_id = os.getenv('TELEGRAM_CHAT_ID')
    if not bot_token or not chat_id: return False
    
    try:
        # 发送文字
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        params = {'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'}
        resp = requests.post(url, params=params, timeout=30)
        
        if resp.status_code == 200:
            log("Telegram-文字消息已推送")
        else:
            log(f"Telegram-文字推送失败, 接口返回: {resp.text}")
        
        # 发送TXT文件
        if file_path and os.path.exists(file_path):
            doc_url = f"https://api.telegram.org/bot{bot_token}/sendDocument"
            with open(file_path, 'rb') as f:
                # 文件类型 text/plain
                files = {'document': (os.path.basename(file_path), f, 'text/plain')}
                data = {'chat_id': chat_id}
                doc_resp = requests.post(doc_url, data=data, files=files, timeout=60)
                
                if doc_resp.status_code == 200:
                    log("Telegram-TXT文件已推送")
                else:
                    log(f"Telegram-文件推送失败, 接口返回: {doc_resp.text}")
    except Exception as e:
        log(f"Telegram-推送发生代码级异常: {e}")

def push_to_wechat(text, file_path=None):
    """推送到企业微信，支持发送txt文件"""
    webhook_key = os.getenv('WECHAT_WEBHOOK_KEY')
    if not webhook_key: return False
    
    try:
        url = webhook_key if webhook_key.startswith('https://') else f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={webhook_key}"
        
        # 发送文字
        body = {"msgtype": "text", "text": {"content": text}}
        resp = requests.post(url, json=body, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            if data.get('errcode') == 0:
                log("企业微信-文字消息已推送")
            else:
                log(f"企业微信-文字推送业务失败, 接口返回: {data}")
        else:
            log(f"企业微信-文字推送请求失败, HTTP状态码: {resp.status_code}, 返回: {resp.text}")

        # 发送TXT文件
        if file_path and os.path.exists(file_path):
            key = webhook_key.split('key=')[-1] if 'key=' in webhook_key else webhook_key
            upload_url = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/upload_media?key={key}&type=file"
            
            with open(file_path, 'rb') as f:
                # 文件类型 text/plain
                files = {'media': (os.path.basename(file_path), f, 'text/plain')}
                up_resp = requests.post(upload_url, files=files, timeout=60)
                
                if up_resp.status_code == 200:
                    up_data = up_resp.json()
                    if up_data.get('errcode') == 0:
                        media_id = up_data.get('media_id')
                        send_url = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={key}"
                        file_body = {"msgtype": "file", "file": {"media_id": media_id}}
                        send_resp = requests.post(send_url, json=file_body, timeout=30)
                        
                        if send_resp.status_code == 200 and send_resp.json().get('errcode') == 0:
                            log("企业微信-TXT文件已推送")
                        else:
                            log(f"企业微信-文件发送失败, 接口返回: {send_resp.text}")
                    else:
                        log(f"企业微信-文件上传失败, 接口返回: {up_data}")
                else:
                    log(f"企业微信-文件上传请求失败, 接口返回: {up_resp.text}")
    except Exception as e:
        log(f"企业微信-推送发生代码级异常: {e}")

def push_to_dingtalk(text):
    webhook = os.getenv('DINGTALK_WEBHOOK')
    if not webhook: return
    try:
        url = webhook if webhook.startswith('https://') else f"https://oapi.dingtalk.com/robot/send?access_token={webhook}"
        resp = requests.post(url, json={"msgtype": "text", "text": {"content": text}}, timeout=30)
        if resp.status_code == 200 and resp.json().get('errcode') == 0:
            log("钉钉-推送成功")
        else:
            log(f"钉钉-推送失败, 接口返回: {resp.text}")
    except Exception as e:
        log(f"钉钉-推送异常: {e}")

def push_to_pushplus(text):
    token = os.getenv('PUSHPLUS_TOKEN')
    if not token: return
    try:
        resp = requests.post("http://www.pushplus.plus/send", json={"token": token, "title": get_push_title(), "content": text}, timeout=30)
        if resp.status_code == 200 and resp.json().get('code') == 200:
            log("PushPlus-推送成功")
        else:
            log(f"PushPlus-推送失败, 接口返回: {resp.text}")
    except Exception as e:
        log(f"PushPlus-推送异常: {e}")

def push_to_serverchan(text):
    sckey = os.getenv('SERVERCHAN_SCKEY')
    if not sckey: return
    try:
        resp = requests.post(f"https://sctapi.ftqq.com/{sckey}.send", data={"title": get_push_title(), "desp": text}, timeout=30)
        if resp.status_code == 200 and resp.json().get('data', {}).get('error') == 'SUCCESS':
            log("Server酱-推送成功")
        else:
            log(f"Server酱-推送失败, 接口返回: {resp.text}")
    except Exception as e:
        log(f"Server酱-推送异常: {e}")

def push_to_serverchan3(text):
    sckey = os.getenv('SERVERCHAN3_SCKEY')
    if not sckey: return
    try:
        from serverchan_sdk import sc_send
        resp = sc_send(sckey, get_push_title(), text, {"tags": "嘉立创|注册"})
        if resp.get("code") == 0:
            log("Server酱3-推送成功")
        else:
            log(f"Server酱3-推送失败, 接口返回: {resp}")
    except ImportError:
        log("未安装 serverchan_sdk，跳过 Server酱3 推送。如果需要请在 workflow 中 pip install serverchan-sdk")
    except Exception as e:
        log(f"Server酱3-推送异常: {e}")

def push_to_coolpush(text):
    skey = os.getenv('COOLPUSH_SKEY')
    if not skey: return
    try:
        resp = requests.get(f"https://push.xuthus.cc/send/{skey}?c={text}", timeout=30)
        if resp.status_code == 200:
            log("酷推-推送成功")
        else:
            log(f"酷推-推送失败, 接口返回: {resp.text}")
    except Exception as e:
        log(f"酷推-推送异常: {e}")

def push_to_custom(text):
    webhook = os.getenv('CUSTOM_WEBHOOK')
    if not webhook: return
    try:
        resp = requests.post(webhook, json={"title": get_push_title(), "content": text}, timeout=30)
        if resp.status_code == 200:
            log("自定义API-推送成功")
        else:
            log(f"自定义API-推送失败, 接口返回: {resp.text}")
    except Exception as e:
        log(f"自定义API-推送异常: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        log("执行异常: 未提供需推送的结果文件路径参数。")
        sys.exit(1)
        
    result_file = sys.argv[1]
    if not os.path.exists(result_file):
        log(f"未找到文件: {result_file}")
        sys.exit(0)
        
    with open(result_file, "r", encoding="utf-8") as f:
        file_content = f.read().strip()
        
    if not file_content:
        log("文件内容为空，跳过推送流程。")
        sys.exit(0)
        
    # 构建文字推送内容
    push_text = f"{get_push_title()}\n\n详细注册数据如下：\n{file_content}"
    log("================ 开始进行多平台推送 ================")
    
    # 只要对应的 secret 被配置了，就会尝试调用（文件作为第二参数，如果平台不支持会自动在内部被忽略）
    push_to_telegram(push_text, result_file)
    push_to_wechat(push_text, result_file)
    push_to_dingtalk(push_text)
    push_to_pushplus(push_text)
    push_to_serverchan(push_text)
    push_to_serverchan3(push_text)
    push_to_coolpush(push_text)
    push_to_custom(push_text)
    
    log("================ 推送流程执行结束 ================")
