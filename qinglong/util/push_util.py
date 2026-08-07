# -*- coding: utf-8 -*-
import base64
import hashlib
import hmac
import json
import time
import urllib.parse

import requests
from datetime import datetime
import pytz


def get_beijing_time():
    """获取北京时间"""
    target_timezone = pytz.timezone('Asia/Shanghai')
    return datetime.now().astimezone(target_timezone)


def format_now():
    """格式化当前时间"""
    return get_beijing_time().strftime("%Y-%m-%d %H:%M:%S")


class PushConfig:
    """推送配置类"""

    def __init__(self,
                 push_plus_token=None,
                 push_plus_hour=None,
                 push_plus_max=30,
                 push_wechat_webhook_key=None,
                 telegram_bot_token=None,
                 telegram_chat_id=None,
                 serverchan_key=None,
                 bark_key=None,
                 dingtalk_token=None,
                 dingtalk_secret=None,
                 feishu_webhook=None):
        self.push_plus_token = push_plus_token
        self.push_plus_hour = push_plus_hour
        self.push_plus_max = int(push_plus_max) if push_plus_max else 30
        self.push_wechat_webhook_key = push_wechat_webhook_key
        self.telegram_bot_token = telegram_bot_token
        self.telegram_chat_id = telegram_chat_id
        self.serverchan_key = serverchan_key
        self.bark_key = bark_key
        self.dingtalk_token = dingtalk_token
        self.dingtalk_secret = dingtalk_secret
        self.feishu_webhook = feishu_webhook


def push_plus(token, title, content):
    """
    推送消息类型为html 需要在外部组装html代码的content
    :param token: PUSHPLUS 的token
    :param title: 推送标题
    :param content: 推送内容
    :return: none
    """
    requestUrl = f"http://www.pushplus.plus/send"
    data = {
        "token": token,
        "title": title,
        "content": content,
        "template": "html",
        "channel": "wechat"
    }
    try:
        response = requests.post(requestUrl, data=data)
        if response.status_code == 200:
            json_res = response.json()
            print(f"pushplus推送完毕：{json_res['code']}-{json_res['msg']}")
        else:
            print("pushplus推送失败")
    except requests.exceptions.RequestException as e:
        print(f"pushplus推送网络异常: {e}")
    except Exception as e:
        print(f"pushplus推送未知异常: {e}")


def push_wechat_webhook(key, title, content):
    """
    推送企业微信通知，WebHook方式，需要注册企业微信并配置机器人到对应的推送群。然后提取对应的key

    :param key: WebHook机器人的key
    :param title: 推送标题
    :param content: 推送内容，使用纯文本效果会更好
    :return:
    """

    requestUrl = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={key}"

    payload = {
        "msgtype": "markdown_v2",
        "markdown_v2": {
            "content": buildWeChatContent(title, content)
        }
    }

    try:
        response = requests.post(requestUrl, json=payload)
        if response.status_code == 200:
            json_res = response.json()
            if json_res.get('errcode') == 0:
                print(f"企业微信推送完毕：{json_res['errmsg']}")
            else:
                print(f"企业微信推送失败：{json_res.get('errmsg', '未知错误')}")
        else:
            print("企业微信推送失败")
    except requests.exceptions.RequestException as e:
        print(f"企业微信推送异常: {e}")
    except Exception as e:
        print(f"企业微信推送发生未知异常: {e}")


def buildWeChatContent(title, content) -> str:
    return f"""# {title}\n{content}"""


def push_telegram_bot(bot_token, chat_id, content):
    """
    推送消息类型为html 需要在外部组装html content
    :param bot_token: telegram bot token
    :param chat_id: telegram bot chat_id
    :param content: 推送内容
    :return: none
    """
    requestUrl = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    payload = {
        "chat_id": int(chat_id),
        "text": content,
        "parse_mode": "HTML"
    }
    try:
        response = requests.post(requestUrl, json=payload)
        if response.status_code == 200:
            json_res = response.json()
            if json_res.get('ok') is True:
                print(f"telegram bot推送完毕：{json_res['result']['message_id']}")
            else:
                print(f"telegram bot推送失败: {json.dumps(json_res)}")
        else:
            print(f"telegram bot推送失败: {response.status_code}")
    except requests.exceptions.RequestException as e:
        print(f"telegram bot推送异常: {e}")
    except Exception as e:
        print(f"telegram bot推送发生未知异常: {e}")


def push_serverchan(key, title, content):
    """
    推送Server酱(ServerChan3 Turbo)
    :param key: Server酱 SendKey
    :param title: 推送标题
    :param content: 推送内容
    :return: none
    """
    requestUrl = f"https://sctapi.ftqq.com/{key}.send"
    data = {
        "title": title,
        "desp": content
    }
    try:
        response = requests.post(requestUrl, data=data)
        if response.status_code == 200:
            json_res = response.json()
            if json_res.get('code') == 0:
                print(f"server酱推送完毕：{json_res.get('message')}")
            else:
                print(f"server酱推送失败：{json_res.get('message', '未知错误')}")
        else:
            print(f"server酱推送失败: {response.status_code}")
    except requests.exceptions.RequestException as e:
        print(f"server酱推送异常: {e}")
    except Exception as e:
        print(f"server酱推送发生未知异常: {e}")


def push_bark(key, title, content):
    """
    推送Bark（iOS），支持官方服务或自建服务
    :param key: Bark设备key（如 abc123）或完整地址（如 https://api.day.app/abc123）
    :param title: 推送标题
    :param content: 推送内容
    :return: none
    """
    if str(key).startswith("http"):
        # 支持自建 Bark 服务完整地址
        device_key = str(key).rstrip("/").split("/")[-1]
        push_url = str(key).rsplit("/", 1)[0] + "/push"
    else:
        device_key = key
        push_url = "https://api.day.app/push"
    data = {
        "device_key": device_key,
        "title": title,
        "body": content,
        "level": "active"
    }
    try:
        response = requests.post(push_url, json=data)
        if response.status_code == 200:
            json_res = response.json()
            if json_res.get('code') == 200:
                print("bark推送完毕")
            else:
                print(f"bark推送失败：{json_res.get('message', '未知错误')}")
        else:
            print(f"bark推送失败: {response.status_code}")
    except requests.exceptions.RequestException as e:
        print(f"bark推送异常: {e}")
    except Exception as e:
        print(f"bark推送发生未知异常: {e}")


def push_dingtalk(token, secret, title, content):
    """
    推送钉钉群机器人，支持加签安全设置
    :param token: 钉钉机器人 access_token
    :param secret: 钉钉机器人加签密钥（未开启加签可传None）
    :param title: 推送标题
    :param content: 推送内容
    :return: none
    """
    requestUrl = f"https://oapi.dingtalk.com/robot/send?access_token={token}"
    if secret:
        timestamp = str(round(time.time() * 1000))
        string_to_sign = f"{timestamp}\n{secret}"
        hmac_code = hmac.new(secret.encode('utf-8'), string_to_sign.encode('utf-8'), digestmod=hashlib.sha256).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
        requestUrl += f"&timestamp={timestamp}&sign={sign}"
    payload = {
        "msgtype": "text",
        "text": {"content": f"{title}\n{content}"}
    }
    try:
        response = requests.post(requestUrl, json=payload)
        if response.status_code == 200:
            json_res = response.json()
            if json_res.get('errcode') == 0:
                print(f"钉钉推送完毕：{json_res.get('errmsg')}")
            else:
                print(f"钉钉推送失败：{json_res.get('errmsg', '未知错误')}")
        else:
            print(f"钉钉推送失败: {response.status_code}")
    except requests.exceptions.RequestException as e:
        print(f"钉钉推送异常: {e}")
    except Exception as e:
        print(f"钉钉推送发生未知异常: {e}")


def push_feishu(webhook, title, content):
    """
    推送飞书群机器人
    :param webhook: 飞书机器人 webhook 完整地址
    :param title: 推送标题
    :param content: 推送内容
    :return: none
    """
    payload = {
        "msg_type": "text",
        "content": {"text": f"{title}\n{content}"}
    }
    try:
        response = requests.post(webhook, json=payload)
        if response.status_code == 200:
            json_res = response.json()
            if json_res.get('code') == 0:
                print(f"飞书推送完毕：{json_res.get('msg')}")
            else:
                print(f"飞书推送失败：{json_res.get('msg', '未知错误')}")
        else:
            print(f"飞书推送失败: {response.status_code}")
    except requests.exceptions.RequestException as e:
        print(f"飞书推送异常: {e}")
    except Exception as e:
        print(f"飞书推送发生未知异常: {e}")


# 组装纯文本推送内容
def build_push_content(exec_results, summary, config: PushConfig) -> str:
    content = f'{summary}'
    if len(exec_results) >= config.push_plus_max:
        content += '\n账号数量过多，详细情况请前往青龙面板日志中查看'
    else:
        for exec_result in exec_results:
            success = exec_result['success']
            if success is not None and success is True:
                content += f'\n- 账号：{exec_result["user"]}刷步数成功，接口返回：{exec_result["msg"]}'
            else:
                content += f'\n- 账号：{exec_result["user"]}刷步数失败，失败原因：{exec_result["msg"]}'
    return content


def push_results(exec_results, summary, config: PushConfig):
    """推送所有结果"""
    if not_in_push_time_range(config):
        return
    push_to_push_plus(exec_results, summary, config)
    push_to_wechat_webhook(exec_results, summary, config)
    push_to_telegram_bot(exec_results, summary, config)
    push_to_serverchan(exec_results, summary, config)
    push_to_bark(exec_results, summary, config)
    push_to_dingtalk(exec_results, summary, config)
    push_to_feishu(exec_results, summary, config)


def not_in_push_time_range(config: PushConfig) -> bool:
    """
    检查是否在推送时间范围内
    青龙面板版本：只根据当前时间判断，不再依赖 cron_change_time 文件
    """
    if not config.push_plus_hour:
        return False  # 如果没有设置推送时间，则总是推送

    time_bj = get_beijing_time()

    if config.push_plus_hour.isdigit():
        if time_bj.hour == int(config.push_plus_hour):
            print(f"当前设置推送整点为：{config.push_plus_hour}, 当前整点为：{time_bj.hour}，执行推送")
            return False
        # 允许1小时误差（青龙面板定时任务可能有延迟）
        if abs(time_bj.hour - int(config.push_plus_hour)) == 1:
            print(f"当前设置推送整点为：{config.push_plus_hour}, 当前整点为：{time_bj.hour}，在允许误差范围内，执行推送")
            return False

    print(f"当前整点时间为：{time_bj.hour}，不在配置的推送时间({config.push_plus_hour})，不执行推送")
    return True


def push_to_push_plus(exec_results, summary, config: PushConfig):
    """推送到PushPlus"""
    if config.push_plus_token and config.push_plus_token != '' and config.push_plus_token != 'NO':
        html = f'<div>{summary}</div>'
        if len(exec_results) >= config.push_plus_max:
            html += '<div>账号数量过多，详细情况请前往青龙面板日志中查看</div>'
        else:
            html += '<ul>'
            for exec_result in exec_results:
                success = exec_result['success']
                if success is not None and success is True:
                    html += f'<li><span>账号：{exec_result["user"]}</span>刷步数成功，接口返回：{exec_result["msg"]}</li>'
                else:
                    html += f'<li><span>账号：{exec_result["user"]}</span>刷步数失败，失败原因：{exec_result["msg"]}</li>'
            html += '</ul>'
        push_plus(config.push_plus_token, f"{format_now()} 刷步数通知", html)
    else:
        print("未配置 PUSH_PLUS_TOKEN 跳过PUSHPLUS推送")


def push_to_wechat_webhook(exec_results, summary, config: PushConfig):
    """推送到企业微信"""
    if config.push_wechat_webhook_key and config.push_wechat_webhook_key != '' and config.push_wechat_webhook_key != 'NO':

        content = f'## {summary}'
        if len(exec_results) >= config.push_plus_max:
            content += '\n- 账号数量过多，详细情况请前往青龙面板日志中查看'
        else:
            for exec_result in exec_results:
                success = exec_result['success']
                if success is not None and success is True:
                    content += f'\n- 账号：{exec_result["user"]}刷步数成功，接口返回：{exec_result["msg"]}'
                else:
                    content += f'\n- 账号：{exec_result["user"]}刷步数失败，失败原因：{exec_result["msg"]}'
        push_wechat_webhook(config.push_wechat_webhook_key, f"{format_now()} 刷步数通知", content)
    else:
        print("未配置 PUSH_WECHAT_WEBHOOK_KEY 跳过微信推送")


def push_to_telegram_bot(exec_results, summary, config: PushConfig):
    """推送到Telegram"""
    if (config.telegram_bot_token and config.telegram_bot_token != '' and config.telegram_bot_token != 'NO' and
            config.telegram_chat_id and config.telegram_chat_id != ''):
        html = f'<b>{summary}</b>'
        if len(exec_results) >= config.push_plus_max:
            html += '<blockquote>账号数量过多，详细情况请前往青龙面板日志中查看</blockquote>'
        else:
            for exec_result in exec_results:
                success = exec_result['success']
                if success is not None and success is True:
                    html += f'<pre><blockquote>账号：{exec_result["user"]}</blockquote>刷步数成功，接口返回：<b>{exec_result["msg"]}</b></pre>'
                else:
                    html += f'<pre><blockquote>账号：{exec_result["user"]}</blockquote>刷步数失败，失败原因：<b>{exec_result["msg"]}</b></pre>'
        push_telegram_bot(config.telegram_bot_token, config.telegram_chat_id, html)
    else:
        print("未配置 TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHAT_ID 跳过telegram推送")


def push_to_serverchan(exec_results, summary, config: PushConfig):
    """推送到Server酱"""
    if config.serverchan_key and config.serverchan_key != '' and config.serverchan_key != 'NO':
        push_serverchan(config.serverchan_key, f"{format_now()} 刷步数通知", build_push_content(exec_results, summary, config))
    else:
        print("未配置 SERVERCHAN_KEY 跳过server酱推送")


def push_to_bark(exec_results, summary, config: PushConfig):
    """推送到Bark"""
    if config.bark_key and config.bark_key != '' and config.bark_key != 'NO':
        push_bark(config.bark_key, f"{format_now()} 刷步数通知", build_push_content(exec_results, summary, config))
    else:
        print("未配置 BARK_KEY 跳过bark推送")


def push_to_dingtalk(exec_results, summary, config: PushConfig):
    """推送到钉钉"""
    if config.dingtalk_token and config.dingtalk_token != '' and config.dingtalk_token != 'NO':
        push_dingtalk(config.dingtalk_token, config.dingtalk_secret, f"{format_now()} 刷步数通知",
                      build_push_content(exec_results, summary, config))
    else:
        print("未配置 DINGTALK_TOKEN 跳过钉钉推送")


def push_to_feishu(exec_results, summary, config: PushConfig):
    """推送到飞书"""
    if config.feishu_webhook and config.feishu_webhook != '' and config.feishu_webhook != 'NO':
        push_feishu(config.feishu_webhook, f"{format_now()} 刷步数通知", build_push_content(exec_results, summary, config))
    else:
        print("未配置 FEISHU_WEBHOOK 跳过飞书推送")
