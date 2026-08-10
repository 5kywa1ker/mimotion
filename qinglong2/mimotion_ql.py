# -*- coding: utf8 -*-
"""
小米运动 (Zepp Life) 自动刷步数 - 青龙面板合并版 (qinglong2)
1. 支持环境变量: MIMOTION_CONFIG, MIMOTION_AES_KEY, STEP
2. 保持与 GitHub Actions 版本完全一致的登录和 token 级联处理逻辑
3. 适配青龙面板 notify 消息推送 (同时保留内置推送作为 fallback)
"""

import os
import re
import json
import time
import math
import uuid
import pytz
import random
import base64
import urllib.parse
import traceback
from datetime import datetime
import requests

# 尝试导入 Crypto 库 (pycryptodome)
try:
    from Crypto.Cipher import AES
    from Crypto.Random import get_random_bytes
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False
    print("提示: 未安装 pycryptodome 库，若需使用 token 加密保存功能，请在青龙依赖管理中安装 pycryptodome")

# 尝试导入青龙面板内置 notify 推送
try:
    from notify import send as ql_send
    HAS_QL_NOTIFY = True
except ImportError:
    ql_send = None
    HAS_QL_NOTIFY = False


# ==============================================================================
# 1. AES 加密模块 (用于华米登录请求及 Token 本地持久化加密)
# ==============================================================================
HM_AES_KEY = b'xeNtBVqzDc6tuNTh'  # 华米传输加密 16 bytes
HM_AES_IV = b'MAAAYAAAAAAAAABg'   # 华米传输加密固定 IV 16 bytes
AES_BLOCK_SIZE = 16

def _pkcs7_pad(data: bytes) -> bytes:
    pad_len = AES_BLOCK_SIZE - (len(data) % AES_BLOCK_SIZE)
    return data + bytes([pad_len]) * pad_len

def _pkcs7_unpad(data: bytes) -> bytes:
    if not data or len(data) % AES_BLOCK_SIZE != 0:
        raise ValueError(f"invalid padded data length {len(data)}")
    pad_len = data[-1]
    if pad_len < 1 or pad_len > AES_BLOCK_SIZE:
        raise ValueError(f"invalid padding length: {pad_len}")
    if data[-pad_len:] != bytes([pad_len]) * pad_len:
        raise ValueError("invalid PKCS#7 padding")
    return data[:-pad_len]

def _validate_key(key: bytes):
    if not isinstance(key, (bytes, bytearray)):
        raise TypeError("key must be bytes")
    if len(key) != 16:
        raise ValueError("key must be 16 bytes for AES-128")

def encrypt_data(plain: bytes, key: bytes, iv: bytes = None) -> bytes:
    if not HAS_CRYPTO:
        raise RuntimeError("Cryptographic library 'Crypto' is not available.")
    _validate_key(key)
    if not isinstance(plain, (bytes, bytearray)):
        raise TypeError("plain must be bytes")

    if iv is None:
        iv = get_random_bytes(AES_BLOCK_SIZE)
        cipher = AES.new(key, AES.MODE_CBC, iv)
        padded = _pkcs7_pad(plain)
        return iv + cipher.encrypt(padded)
    else:
        if len(iv) != AES_BLOCK_SIZE:
            raise ValueError(f"IV must be {AES_BLOCK_SIZE} bytes")
        cipher = AES.new(key, AES.MODE_CBC, iv)
        padded = _pkcs7_pad(plain)
        return cipher.encrypt(padded)

def decrypt_data(data: bytes, key: bytes, iv: bytes = None) -> bytes:
    if not HAS_CRYPTO:
        raise RuntimeError("Cryptographic library 'Crypto' is not available.")
    _validate_key(key)
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("data must be bytes")

    if iv is None:
        if len(data) < AES_BLOCK_SIZE:
            raise ValueError("data too short")
        iv = data[:AES_BLOCK_SIZE]
        ciphertext = data[AES_BLOCK_SIZE:]
        if len(ciphertext) == 0 or len(ciphertext) % AES_BLOCK_SIZE != 0:
            raise ValueError("invalid ciphertext length")
    else:
        if len(iv) != AES_BLOCK_SIZE:
            raise ValueError(f"IV must be {AES_BLOCK_SIZE} bytes")
        ciphertext = data
        if len(ciphertext) == 0 or len(ciphertext) % AES_BLOCK_SIZE != 0:
            raise ValueError("invalid ciphertext length")

    cipher = AES.new(key, AES.MODE_CBC, iv)
    decrypted_padded = cipher.decrypt(ciphertext)
    return _pkcs7_unpad(decrypted_padded)


# ==============================================================================
# 2. Zepp Life (华米 / 小米运动) API 模块
# ==============================================================================
def get_beijing_time():
    target_timezone = pytz.timezone('Asia/Shanghai')
    return datetime.now().astimezone(target_timezone)

def format_now():
    return get_beijing_time().strftime("%Y-%m-%d %H:%M:%S")

def get_time():
    current_time = get_beijing_time()
    return "%.0f" % (current_time.timestamp() * 1000)

def get_access_token_from_location(location):
    code_pattern = re.compile("(?<=access=).*?(?=&)")
    result = code_pattern.findall(location)
    if not result:
        return None
    return result[0]

def get_error_code_from_location(location):
    code_pattern = re.compile("(?<=error=).*?(?=&)")
    result = code_pattern.findall(location)
    if not result:
        return None
    return result[0]

def login_access_token(user, password):
    headers = {
        "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
        "user-agent": "MiFit6.14.0 (M2007J1SC; Android 12; Density/2.75)",
        "app_name": "com.xiaomi.hm.health",
        "appname": "com.xiaomi.hm.health",
        "appplatform": "android_phone",
        "x-hm-ekv": "1",
        "hm-privacy-ceip": "false"
    }
    login_data = {
        'emailOrPhone': user,
        'password': password,
        'state': 'REDIRECTION',
        'client_id': 'HuaMi',
        'country_code': 'CN',
        'token': 'access',
        'redirect_uri': 'https://s3-us-west-2.amazonaws.com/hm-registration/successsignin.html',
    }
    query = urllib.parse.urlencode(login_data)
    plaintext = query.encode('utf-8')
    cipher_data = encrypt_data(plaintext, HM_AES_KEY, HM_AES_IV)

    url1 = 'https://api-user.zepp.com/v2/registrations/tokens'
    r1 = requests.post(url1, data=cipher_data, headers=headers, allow_redirects=False, timeout=10)
    if r1.status_code != 303:
        return None, f"登录异常，status: {r1.status_code}"
    try:
        location = r1.headers["Location"]
        code = get_access_token_from_location(location)
        if code is None:
            return None, f"获取accessToken失败 {get_error_code_from_location(location)}"
    except Exception:
        return None, f"获取accessToken异常:{traceback.format_exc()}"
    return code, None

def grant_login_tokens(access_token, device_id, is_phone=False):
    url = "https://account.huami.com/v2/client/login"
    headers = {
        "app_name": "com.xiaomi.hm.health",
        "x-request-id": str(uuid.uuid4()),
        "accept-language": "zh-CN",
        "appname": "com.xiaomi.hm.health",
        "cv": "50818_6.14.0",
        "v": "2.0",
        "appplatform": "android_phone",
        "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
    }
    if is_phone:
        data = {
            "app_name": "com.xiaomi.hm.health",
            "app_version": "6.14.0",
            "code": access_token,
            "country_code": "CN",
            "device_id": device_id,
            "device_model": "phone",
            "grant_type": "access_token",
            "third_name": "huami_phone",
        }
    else:
        data = {
            "allow_registration=": "false",
            "app_name": "com.xiaomi.hm.health",
            "app_version": "6.14.0",
            "code": access_token,
            "country_code": "CN",
            "device_id": device_id,
            "device_model": "android_phone",
            "dn": "account.zepp.com,api-user.zepp.com,api-mifit.zepp.com,api-watch.zepp.com,app-analytics.zepp.com,api-analytics.huami.com,auth.zepp.com",
            "grant_type": "access_token",
            "lang": "zh_CN",
            "os_version": "1.5.0",
            "source": "com.xiaomi.hm.health:6.14.0:50818",
            "third_name": "email",
        }
    resp = requests.post(url, data=data, headers=headers).json()
    _login_token, _userid, _app_token = None, None, None
    try:
        result = resp.get("result")
        if result != "ok":
            return None, None, None, f"客户端登录失败：{result}"
        _login_token = resp["token_info"]["login_token"]
        _app_token = resp["token_info"]["app_token"]
        _userid = resp["token_info"]["user_id"]
    except Exception:
        print("提取login_token失败：%s" % json.dumps(resp, ensure_ascii=False, indent=2))
    return _login_token, _app_token, _userid, None

def grant_app_token(login_token: str):
    url = f"https://account-cn.huami.com/v1/client/app_tokens?app_name=com.xiaomi.hm.health&dn=api-user.huami.com%2Capi-mifit.huami.com%2Capp-analytics.huami.com&login_token={login_token}"
    headers = {'User-Agent': 'MiFit/5.3.0 (iPhone; iOS 14.7.1; Scale/3.00)'}
    resp = requests.get(url, headers=headers)
    if resp.status_code != 200:
        return None, f"请求异常：{resp.status_code}"
    resp = resp.json()
    result = resp.get("result")
    if result != "ok":
        error_code = resp.get("error_code")
        return None, f"请求失败：{error_code}"
    app_token = resp['token_info']['app_token']
    return app_token, None

def check_app_token(app_token):
    url = "https://api-mifit-cn3.zepp.com/huami.health.getUserInfo.json"
    params = {
        "r": "00b7912b-790a-4552-81b1-3742f9dd1e76",
        "userid": "1188760659",
        "appid": "428135909242707968",
        "channel": "Normal",
        "country": "CN",
        "cv": "50818_6.14.0",
        "device": "android_31",
        "device_type": "android_phone",
        "lang": "zh_CN",
        "timezone": "Asia/Shanghai",
        "v": "2.0"
    }
    headers = {
        "User-Agent": "MiFit6.14.0 (M2007J1SC; Android 12; Density/2.75)",
        "Accept-Encoding": "gzip",
        "hm-privacy-diagnostics": "false",
        "country": "CN",
        "appplatform": "android_phone",
        "hm-privacy-ceip": "true",
        "x-request-id": str(uuid.uuid4()),
        "timezone": "Asia/Shanghai",
        "channel": "Normal",
        "cv": "50818_6.14.0",
        "appname": "com.xiaomi.hm.health",
        "v": "2.0",
        "apptoken": app_token,
        "lang": "zh_CN",
        "clientid": "428135909242707968"
    }
    response = requests.get(url, params=params, headers=headers)
    if response.status_code != 200:
        return False, f"请求异常：{response.status_code}"
    res_json = response.json()
    message = res_json.get("message")
    if message == "success":
        return True, None
    else:
        return False, message

def post_fake_brand_data(step, app_token, userid):
    t = get_time()
    today = time.strftime("%F")

    data_json = '%5B%7B%22data_hr%22%3A%22%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F9L%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2FVv%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F0v%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F9e%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F0n%5C%2Fa%5C%2F%5C%2F%5C%2FS%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F0b%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F1FK%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2FR%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F9PTFFpaf9L%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2FR%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F0j%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F9K%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2FOv%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2Fzf%5C%2F%5C%2F%5C%2F86%5C%2Fzr%5C%2FOv88%5C%2Fzf%5C%2FPf%5C%2F%5C%2F%5C%2F0v%5C%2FS%5C%2F8%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2FSf%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2Fz3%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F0r%5C%2FOv%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2FS%5C%2F9L%5C%2Fzb%5C%2FSf9K%5C%2F0v%5C%2FRf9H%5C%2Fzj%5C%2FSf9K%5C%2F0%5C%2F%5C%2FN%5C%2F%5C%2F%5C%2F%5C%2F0D%5C%2FSf83%5C%2Fzr%5C%2FPf9M%5C%2F0v%5C%2FOv9e%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2FS%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2Fzv%5C%2F%5C%2Fz7%5C%2FO%5C%2F83%5C%2Fzv%5C%2FN%5C%2F83%5C%2Fzr%5C%2FN%5C%2F86%5C%2Fz%5C%2F%5C%2FNv83%5C%2Fzn%5C%2FXv84%5C%2Fzr%5C%2FPP84%5C%2Fzj%5C%2FN%5C%2F9e%5C%2Fzr%5C%2FN%5C%2F89%5C%2F03%5C%2FP%5C%2F89%5C%2Fz3%5C%2FQ%5C%2F9N%5C%2F0v%5C%2FTv9C%5C%2F0H%5C%2FOf9D%5C%2Fzz%5C%2FOf88%5C%2Fz%5C%2F%5C%2FPP9A%5C%2Fzr%5C%2FN%5C%2F86%5C%2Fzz%5C%2FNv87%5C%2F0D%5C%2FOv84%5C%2F0v%5C%2FO%5C%2F84%5C%2Fzf%5C%2FMP83%5C%2FzH%5C%2FNv83%5C%2Fzf%5C%2FN%5C%2F84%5C%2Fzf%5C%2FOf82%5C%2Fzf%5C%2FOP83%5C%2Fzb%5C%2FMv81%5C%2FzX%5C%2FR%5C%2F9L%5C%2F0v%5C%2FO%5C%2F9I%5C%2F0T%5C%2FS%5C%2F9A%5C%2Fzn%5C%2FPf89%5C%2Fzn%5C%2FNf9K%5C%2F07%5C%2FN%5C%2F83%5C%2Fzn%5C%2FNv83%5C%2Fzv%5C%2FO%5C%2F9A%5C%2F0H%5C%2FOf8%5C%2F%5C%2Fzj%5C%2FPP83%5C%2Fzj%5C%2FS%5C%2F87%5C%2Fzj%5C%2FNv84%5C%2Fzf%5C%2FOf83%5C%2Fzf%5C%2FOf83%5C%2Fzb%5C%2FNv9L%5C%2Fzj%5C%2FNv82%5C%2Fzb%5C%2FN%5C%2F85%5C%2Fzf%5C%2FN%5C%2F9J%5C%2Fzf%5C%2FNv83%5C%2Fzj%5C%2FNv84%5C%2F0r%5C%2FSv83%5C%2Fzf%5C%2FMP%5C%2F%5C%2F%5C%2Fzb%5C%2FMv82%5C%2Fzb%5C%2FOf85%5C%2Fz7%5C%2FNv8%5C%2F%5C%2F0r%5C%2FS%5C%2F85%5C%2F0H%5C%2FQP9B%5C%2F0D%5C%2FNf89%5C%2Fzj%5C%2FOv83%5C%2Fzv%5C%2FNv8%5C%2F%5C%2F0f%5C%2FSv9O%5C%2F0ZeXv%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F1X%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F9B%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2FTP%5C%2F%5C%2F%5C%2F1b%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F0%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F9N%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2F%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%5C%2Fv7%2B%22%2C%22date%22%3A%222021-08-07%22%2C%22data%22%3A%5B%7B%22start%22%3A0%2C%22stop%22%3A1439%2C%22value%22%3A%22UA8AUBQAUAwAUBoAUAEAYCcAUBkAUB4AUBgAUCAAUAEAUBkAUAwAYAsAYB8AYB0AYBgAYCoAYBgAYB4AUCcAUBsAUB8AUBwAUBIAYBkAYB8AUBoAUBMAUCEAUCIAYBYAUBwAUCAAUBgAUCAAUBcAYBsAYCUAATIPYD0KECQAYDMAYB0AYAsAYCAAYDwAYCIAYB0AYBcAYCQAYB0AYBAAYCMAYAoAYCIAYCEAYCYAYBsAYBUAYAYAYCIAYCMAUB0AUCAAUBYAUCoAUBEAUC8AUB0AUBYAUDMAUDoAUBkAUC0AUBQAUBwAUA0AUBsAUAoAUCEAUBYAUAwAUB4AUAwAUCcAUCYAUCwKYDUAAUUlEC8IYEMAYEgAYDoAYBAAUAMAUBkAWgAAWgAAWgAAWgAAWgAAUAgAWgAAUBAAUAQAUA4AUA8AUAkAUAIAUAYAUAcAUAIAWgAAUAQAUAkAUAEAUBkAUCUAWgAAUAYAUBEAWgAAUBYAWgAAUAYAWgAAWgAAWgAAWgAAUBcAUAcAWgAAUBUAUAoAUAIAWgAAUAQAUAYAUCgAWgAAUAgAWgAAWgAAUAwAWwAAXCMAUBQAWwAAUAIAWgAAWgAAWgAAWgAAWgAAWgAAWgAAWgAAWREAWQIAUAMAWSEAUDoAUDIAUB8AUCEAUC4AXB4AUA4AWgAAUBIAUA8AUBAAUCUAUCIAUAMAUAEAUAsAUAMAUCwAUBYAWgAAWgAAWgAAWgAAWgAAWgAAUAYAWgAAWgAAWgAAUAYAWwAAWgAAUAYAXAQAUAMAUBsAUBcAUCAAWwAAWgAAWgAAWgAAWgAAUBgAUB4AWgAAUAcAUAwAWQIAWQkAUAEAUAIAWgAAUAoAWgAAUAYAUB0AWgAAWgAAUAkAWgAAWSwAUBIAWgAAUC4AWSYAWgAAUAYAUAoAUAkAUAIAUAcAWgAAUAEAUBEAUBgAUBcAWRYAUA0AWSgAUB4AUDQAUBoAXA4AUA8AUBwAUA8AUA4AUA4AWgAAUAIAUCMAWgAAUCwAUBgAUAYAUAAAUAAAUAAAUAAAUAAAUAAAUAAAUAAAUAAAWwAAUAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAeSEAeQ8AcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcBcAcAAAcAAAcCYOcBUAUAAAUAAAUAAAUAAAUAUAUAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcCgAeQAAcAAAcAAAcAAAcAAAcAAAcAYAcAAAcBgAeQAAcAAAcAAAegAAegAAcAAAcAcAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcCkAeQAAcAcAcAAAcAAAcAwAcAAAcAAAcAIAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcCIAeQAAcAAAcAAAcAAAcAAAcAAAeRwAeQAAWgAAUAAAUAAAUAAAUAAAUAAAcAAAcAAAcBoAeScAeQAAegAAcBkAeQAAUAAAUAAAUAAAUAAAUAAAUAAAcAAAcAAAcAAAcAAAcAAAcAAAegAAegAAcAAAcAAAcBgAeQAAcAAAcAAAcAAAcAAAcAAAcAkAegAAegAAcAcAcAAAcAcAcAAAcAAAcAAAcAAAcA8AeQAAcAAAcAAAeRQAcAwAUAAAUAAAUAAAUAAAUAAAUAAAcAAAcBEAcA0AcAAAWQsAUAAAUAAAUAAAUAAAUAAAcAAAcAoAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAYAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcBYAegAAcAAAcAAAegAAcAcAcAAAcAAAcAAAcAAAcAAAeRkAegAAegAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAEAcAAAcAAAcAAAcAUAcAQAcAAAcBIAeQAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcBsAcAAAcAAAcBcAeQAAUAAAUAAAUAAAUAAAUAAAUBQAcBYAUAAAUAAAUAoAWRYAWTQAWQAAUAAAUAAAUAAAcAAAcAAAcAAAcAAAcAAAcAMAcAAAcAQAcAAAcAAAcAAAcDMAeQIAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcBQAeQwAcAAAcAAAcAAAcAMAcAAAeSoAcA8AcDMAcAYAeQoAcAwAcFQAcEMAeVIAaTYAbBcNYAsAYBIAYAIAYAIAYBUAYCwAYBMAYDYAYCkAYDcAUCoAUCcAUAUAUBAAWgAAYBoAYBcAYCgAUAMAUAYAUBYAUA4AUBgAUAgAUAgAUAsAUAsAUA4AUAMAUAYAUAQAUBIAASsSUDAAUDAAUBAAYAYAUBAAUAUAUCAAUBoAUCAAUBAAUAoAYAIAUAQAUAgAUCcAUAsAUCIAUCUAUAoAUA4AUB8AUBkAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAA.22%2C%22date%22%3A%222021-08-07%22%2C%22data%22%3A%5B%7B%22start%22%3A0%2C%22stop%22%3A1439%2C%22value%22%3A%22UA8AUBQAUAwAUBoAUAEAYCcAUBkAUB4AUBgAUCAAUAEAUBkAUAwAYAsAYB8AYB0AYBgAYCoAYBgAYB4AUCcAUBsAUB8AUBwAUBIAYBkAYB8AUBoAUBMAUCEAUCIAYBYAUBwAUCAAUBgAUCAAUBcAYBsAYCUAATIPYD0KECQAYDMAYB0AYAsAYCAAYDwAYCIAYB0AYBcAYCQAYB0AYBAAYCMAYAoAYCIAYCEAYCYAYBsAYBUAYAYAYCIAYCMAUB0AUCAAUBYAUCoAUBEAUC8AUB0AUBYAUDMAUDoAUBkAUC0AUBQAUBwAUA0AUBsAUAoAUCEAUBYAUAwAUB4AUAwAUCcAUCYAUCwKYDUAAUUlEC8IYEMAYEgAYDoAYBAAUAMAUBkAWgAAWgAAWgAAWgAAWgAAUAgAWgAAUBAAUAQAUA4AUA8AUAkAUAIAUAYAUAcAUAIAWgAAUAQAUAkAUAEAUBkAUCUAWgAAUAYAUBEAWgAAUBYAWgAAUAYAWgAAWgAAWgAAWgAAUBcAUAcAWgAAUBUAUAoAUAIAWgAAUAQAUAYAUCgAWgAAUAgAWgAAWgAAUAwAWwAAXCMAUBQAWwAAUAIAWgAAWgAAWgAAWgAAWgAAWgAAWgAAWgAAWREAWQIAUAMAWSEAUDoAUDIAUB8AUCEAUC4AXB4AUA4AWgAAUBIAUA8AUBAAUCUAUCIAUAMAUAEAUAsAUAMAUCwAUBYAWgAAWgAAWgAAWgAAWgAAWgAAUAYAWgAAWgAAWgAAUAYAWwAAWgAAUAYAXAQAUAMAUBsAUBcAUCAAWwAAWgAAWgAAWgAAWgAAUBgAUB4AWgAAUAcAUAwAWQIAWQkAUAEAUAIAWgAAUAoAWgAAUAYAUB0AWgAAWgAAUAkAWgAAWSwAUBIAWgAAUC4AWSYAWgAAUAYAUAoAUAkAUAIAUAcAWgAAUAEAUBEAUBgAUBcAWRYAUA0AWSgAUB4AUDQAUBoAXA4AUA8AUBwAUA8AUA4AUA4AWgAAUAIAUCMAWgAAUCwAUBgAUAYAUAAAUAAAUAAAUAAAUAAAUAAAUAAAUAAAUAAAWwAAUAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAeSEAeQ8AcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcBcAcAAAcAAAcCYOcBUAUAAAUAAAUAAAUAAAUAUAUAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcCgAeQAAcAAAcAAAcAAAcAAAcAAAcAYAcAAAcBgAeQAAcAAAcAAAegAAegAAcAAAcAcAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcCkAeQAAcAcAcAAAcAAAcAwAcAAAcAAAcAIAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcCIAeQAAcAAAcAAAcAAAcAAAcAAAeRwAeQAAWgAAUAAAUAAAUAAAUAAAUAAAcAAAcAAAcBoAeScAeQAAegAAcBkAeQAAUAAAUAAAUAAAUAAAUAAAUAAAcAAAcAAAcAAAcAAAcAAAcAAAegAAegAAcAAAcAAAcBgAeQAAcAAAcAAAcAAAcAAAcAAAcAkAegAAegAAcAcAcAAAcAcAcAAAcAAAcAAAcAAAcA8AeQAAcAAAcAAAeRQAcAwAUAAAUAAAUAAAUAAAUAAAUAAAcAAAcBEAcA0AcAAAWQsAUAAAUAAAUAAAUAAAUAAAcAAAcAoAcAAAcAAAcAAAcAAAcAAAcAAAcAYAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcBYAegAAcAAAcAAAegAAcAcAcAAAcAAAcAAAcAAAcAAAeRkAegAAegAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAEAcAAAcAAAcAAAcAUAcAQAcAAAcBIAeQAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcBsAcAAAcAAAcBcAeQAAUAAAUAAAUAAAUAAAUAAAUBQAcBYAUAAAUAAAUAoAWRYAWTQAWQAAUAAAUAAAUAAAcAAAcAAAcAAAcAAAcAAAcAMAcAAAcAQAcAAAcAAAcAAAcDMAeQIAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcBQAeQwAcAAAcAAAcAAAcAMAcAAAeSoAcA8AcDMAcAYAeQoAcAwAcFQAcEMAeVIAaTYAbBcNYAsAYBIAYAIAYAIAYBUAYCwAYBMAYDYAYCkAYDcAUCoAUCcAUAUAUBAAWgAAYBoAYBcAYCgAUAMAUAYAUBYAUA4AUBgAUAgAUAgAUAsAUAsAUA4AUAMAUAYAUAQAUBIAASsSUDAAUDAAUBAAYAYAUBAAUAUAUCAAUBoAUCAAUBAAUAoAYAIAUAQAUAgAUCcAUAsAUCIAUCUAUAoAUA4AUB8AUBkAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAA.22%2C%22date%22%3A%222021-08-07%22%2C%22data%22%3A%5B%7B%22start%22%3A0%2C%22stop%22%3A1439%2C%22value%22%3A%22UA8AUBQAUAwAUBoAUAEAYCcAUBkAUB4AUBgAUCAAUAEAUBkAUAwAYAsAYB8AYB0AYBgAYCoAYBgAYB4AUCcAUBsAUB8AUBwAUBIAYBkAYB8AUBoAUBMAUCEAUCIAYBYAUBwAUCAAUBgAUCAAUBcAYBsAYCUAATIPYD0KECQAYDMAYB0AYAsAYCAAYDwAYCIAYB0AYBcAYCQAYB0AYBAAYCMAYAoAYCIAYCEAYCYAYBsAYBUAYAYAYCIAYCMAUB0AUCAAUBYAUCoAUBEAUC8AUB0AUBYAUDMAUDoAUBkAUC0AUBQAUBwAUA0AUBsAUAoAUCEAUBYAUAwAUB4AUAwAUCcAUCYAUCwKYDUAAUUlEC8IYEMAYEgAYDoAYBAAUAMAUBkAWgAAWgAAWgAAWgAAWgAAUAgAWgAAUBAAUAQAUA4AUA8AUAkAUAIAUAYAUAcAUAIAWgAAUAQAUAkAUAEAUBkAUCUAWgAAUAYAUBEAWgAAUBYAWgAAUAYAWgAAWgAAWgAAWgAAUBcAUAcAWgAAUBUAUAoAUAIAWgAAUAQAUAYAUCgAWgAAUAgAWgAAWgAAUAwAWwAAXCMAUBQAWwAAUAIAWgAAWgAAWgAAWgAAWgAAWgAAWgAAWgAAWREAWQIAUAMAWSEAUDoAUDIAUB8AUCEAUC4AXB4AUA4AWgAAUBIAUA8AUBAAUCUAUCIAUAMAUAEAUAsAUAMAUCwAUBYAWgAAWgAAWgAAWgAAWgAAWgAAUAYAWgAAWgAAWgAAUAYAWwAAWgAAUAYAXAQAUAMAUBsAUBcAUCAAWwAAWgAAWgAAWgAAWgAAUBgAUB4AWgAAUAcAUAwAWQIAWQkAUAEAUAIAWgAAUAoAWgAAUAYAUB0AWgAAWgAAUAkAWgAAWSwAUBIAWgAAUC4AWSYAWgAAUAYAUAoAUAkAUAIAUAcAWgAAUAEAUBEAUBgAUBcAWRYAUA0AWSgAUB4AUDQAUBoAXA4AUA8AUBwAUA8AUA4AUA4AWgAAUAIAUCMAWgAAUCwAUBgAUAYAUAAAUAAAUAAAUAAAUAAAUAAAUAAAUAAAUAAAWwAAUAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAeSEAeQ8AcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcBcAcAAAcAAAcCYOcBUAUAAAUAAAUAAAUAAAUAUAUAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcCgAeQAAcAAAcAAAcAAAcAAAcAAAcAYAcAAAcBgAeQAAcAAAcAAAegAAegAAcAAAcAcAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcCkAeQAAcAcAcAAAcAAAcAwAcAAAcAAAcAIAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcCIAeQAAcAAAcAAAcAAAcAAAcAAAeRwAeQAAWgAAUAAAUAAAUAAAUAAAUAAAcAAAcAAAcBoAeScAeQAAegAAcBkAeQAAUAAAUAAAUAAAUAAAUAAAUAAAcAAAcAAAcAAAcAAAcAAAcAAAegAAegAAcAAAcAAAcBgAeQAAcAAAcAAAcAAAcAAAcAAAcAkAegAAegAAcAcAcAAAcAcAcAAAcAAAcAAAcAAAcA8AeQAAcAAAcAAAeRQAcAwAUAAAUAAAUAAAUAAAUAAAUAAAcAAAcBEAcA0AcAAAWQsAUAAAUAAAUAAAUAAAUAAAcAAAcAoAcAAAcAAAcAAAcAAAcAAAcAAAcAYAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcBYAegAAcAAAcAAAegAAcAcAcAAAcAAAcAAAcAAAcAAAeRkAegAAegAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAEAcAAAcAAAcAAAcAUAcAQAcAAAcBIAeQAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcBsAcAAAcAAAcBcAeQAAUAAAUAAAUAAAUAAAUAAAUBQAcBYAUAAAUAAAUAoAWRYAWTQAWQAAUAAAUAAAUAAAcAAAcAAAcAAAcAAAcAAAcAMAcAAAcAQAcAAAcAAAcAAAcDMAeQIAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcBQAeQwAcAAAcAAAcAAAcAMAcAAAeSoAcA8AcDMAcAYAeQoAcAwAcFQAcEMAeVIAaTYAbBcNYAsAYBIAYAIAYAIAYBUAYCwAYBMAYDYAYCkAYDcAUCoAUCcAUAUAUBAAWgAAYBoAYBcAYCgAUAMAUAYAUBYAUA4AUBgAUAgAUAgAUAsAUAsAUA4AUAMAUAYAUAQAUBIAASsSUDAAUDAAUBAAYAYAUBAAUAUAUCAAUBoAUCAAUBAAUAoAYAIAUAQAUAgAUCcAUAsAUCIAUCUAUAoAUA4AUB8AUBkAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAA.22%2C%22date%22%3A%222021-08-07%22%2C%22data%22%3A%5B%7B%22start%22%3A0%2C%22stop%22%3A1439%2C%22value%22%3A%22UA8AUBQAUAwAUBoAUAEAYCcAUBkAUB4AUBgAUCAAUAEAUBkAUAwAYAsAYB8AYB0AYBgAYCoAYBgAYB4AUCcAUBsAUB8AUBwAUBIAYBkAYB8AUBoAUBMAUCEAUCIAYBYAUBwAUCAAUBgAUCAAUBcAYBsAYCUAATIPYD0KECQAYDMAYB0AYAsAYCAAYDwAYCIAYB0AYBcAYCQAYB0AYBAAYCMAYAoAYCIAYCEAYCYAYBsAYBUAYAYAYCIAYCMAUB0AUCAAUBYAUCoAUBEAUC8AUB0AUBYAUDMAUDoAUBkAUC0AUBQAUBwAUA0AUBsAUAoAUCEAUBYAUAwAUB4AUAwAUCcAUCYAUCwKYDUAAUUlEC8IYEMAYEgAYDoAYBAAUAMAUBkAWgAAWgAAWgAAWgAAWgAAUAgAWgAAUBAAUAQAUA4AUA8AUAkAUAIAUAYAUAcAUAIAWgAAUAQAUAkAUAEAUBkAUCUAWgAAUAYAUBEAWgAAUBYAWgAAUAYAWgAAWgAAWgAAWgAAUBcAUAcAWgAAUBUAUAoAUAIAWgAAUAQAUAYAUCgAWgAAUAgAWgAAWgAAUAwAWwAAXCMAUBQAWwAAUAIAWgAAWgAAWgAAWgAAWgAAWgAAWgAAWgAAWREAWQIAUAMAWSEAUDoAUDIAUB8AUCEAUC4AXB4AUA4AWgAAUBIAUA8AUBAAUCUAUCIAUAMAUAEAUAsAUAMAUCwAUBYAWgAAWgAAWgAAWgAAWgAAWgAAUAYAWgAAWgAAWgAAUAYAWwAAWgAAUAYAXAQAUAMAUBsAUBcAUCAAWwAAWgAAWgAAWgAAWgAAUBgAUB4AWgAAUAcAUAwAWQIAWQkAUAEAUAIAWgAAUAoAWgAAUAYAUB0AWgAAWgAAUAkAWgAAWSwAUBIAWgAAUC4AWSYAWgAAUAYAUAoAUAkAUAIAUAcAWgAAUAEAUBEAUBgAUBcAWRYAUA0AWSgAUB4AUDQAUBoAXA4AUA8AUBwAUA8AUA4AUA4AWgAAUAIAUCMAWgAAUCwAUBgAUAYAUAAAUAAAUAAAUAAAUAAAUAAAUAAAUAAAUAAAWwAAUAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAeSEAeQ8AcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcBcAcAAAcAAAcCYOcBUAUAAAUAAAUAAAUAAAUAUAUAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcCgAeQAAcAAAcAAAcAAAcAAAcAAAcAYAcAAAcBgAeQAAcAAAcAAAegAAegAAcAAAcAcAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcCkAeQAAcAcAcAAAcAAAcAwAcAAAcAAAcAIAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcCIAeQAAcAAAcAAAcAAAcAAAcAAAeRwAeQAAWgAAUAAAUAAAUAAAUAAAUAAAcAAAcAAAcBoAeScAeQAAegAAcBkAeQAAUAAAUAAAUAAAUAAAUAAAUAAAcAAAcAAAcAAAcAAAcAAAcAAAegAAegAAcAAAcAAAcBgAeQAAcAAAcAAAcAAAcAAAcAAAcAkAegAAegAAcAcAcAAAcAcAcAAAcAAAcAAAcAAAcA8AeQAAcAAAcAAAeRQAcAwAUAAAUAAAUAAAUAAAUAAAUAAAcAAAcBEAcA0AcAAAWQsAUAAAUAAAUAAAUAAAUAAAcAAAcAoAcAAAcAAAcAAAcAAAcAAAcAAAcAYAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcBYAegAAcAAAcAAAegAAcAcAcAAAcAAAcAAAcAAAcAAAeRkAegAAegAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAEAcAAAcAAAcAAAcAUAcAQAcAAAcBIAeQAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcBsAcAAAcAAAcBcAeQAAUAAAUAAAUAAAUAAAUAAAUBQAcBYAUAAAUAAAUAoAWRYAWTQAWQAAUAAAUAAAUAAAcAAAcAAAcAAAcAAAcAAAcAMAcAAAcAQAcAAAcAAAcAAAcDMAeQIAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcAAAcBQAeQwAcAAAcAAAcAAAcAMAcAAAeSoAcA8AcDMAcAYAeQoAcAwAcFQAcEMAeVIAaTYAbBcNYAsAYBIAYAIAYAIAYBUAYCwAYBMAYDYAYCkAYDcAUCoAUCcAUAUAUBAAWgAAYBoAYBcAYCgAUAMAUAYAUBYAUA4AUBgAUAgAUAgAUAsAUAsAUA4AUAMAUAYAUAQAUBIAASsSUDAAUDAAUBAAYAYAUBAAUAUAUCAAUBoAUCAAUBAAUAoAYAIAUAQAUAgAUCcAUAsAUCIAUCUAUAoAUA4AUB8AUBkAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAfgAAp3.tz%22%3A32%2C%22did%22%3A%22DA932FFFFE8816E7%22%2C%22src%22%3A24%7D%5D%2C%22summary%22%3A%22%7B%5C%22v%5C%22%3A6%2C%5C%22slp%5C%22%3A%7B%5C%22st%5C%22%3A1628296479%2C%5C%22ed%5C%22%3A1628296479%2C%5C%22dp%5C%22%3A0%2C%5C%22lt%5C%22%3A0%2C%5C%22wk%5C%22%3A0%2C%5C%22usrSt%5C%22%3A-1440%2C%5C%22usrEd%5C%22%3A-1440%2C%5C%22wc%5C%22%3A0%2C%5C%22is%5C%22%3A0%2C%5C%22lb%5C%22%3A0%2C%5C%22to%5C%22%3A0%2C%5C%22dt%5C%22%3A0%2C%5C%22rhr%5C%22%3A0%2C%5C%22ss%5C%22%3A0%7D%2C%5C%22stp%5C%22%3A%7B%5C%22ttl%5C%22%3A18272%2C%5C%22dis%5C%22%3A10627%2C%5C%22cal%5C%22%3A510%2C%5C%22wk%5C%22%3A41%2C%5C%22rn%5C%22%3A50%2C%5C%22runDist%5C%22%3A7654%2C%5C%22runCal%5C%22%3A397%2C%5C%22stage%5C%22%3A%5B%7B%5C%22start%5C%22%3A327%2C%5C%22stop%5C%22%3A341%2C%5C%22mode%5C%22%3A1%2C%5C%22dis%5C%22%3A481%2C%5C%22cal%5C%22%3A13%2C%5C%22step%5C%22%3A680%7D%2C%7B%5C%22start%5C%22%3A342%2C%5C%22stop%5C%22%3A367%2C%5C%22mode%5C%22%3A3%2C%5C%22dis%5C%22%3A2295%2C%5C%22cal%5C%22%3A95%2C%5C%22step%5C%22%3A2874%7D%2C%7B%5C%22start%5C%22%3A368%2C%5C%22stop%5C%22%3A377%2C%5C%22mode%5C%22%3A4%2C%5C%22dis%5C%22%3A1592%2C%5C%22cal%5C%22%3A88%2C%5C%22step%5C%22%3A1664%7D%2C%7B%5C%22start%5C%22%3A378%2C%5C%22stop%5C%22%3A386%2C%5C%22mode%5C%22%3A3%2C%5C%22dis%5C%22%3A1072%2C%5C%22cal%5C%22%3A51%2C%5C%22step%5C%22%3A1245%7D%2C%7B%5C%22start%5C%22%3A387%2C%5C%22stop%5C%22%3A393%2C%5C%22mode%5C%22%3A4%2C%5C%22dis%5C%22%3A1036%2C%5C%22cal%5C%22%3A57%2C%5C%22step%5C%22%3A1124%7D%2C%7B%5C%22start%5C%22%3A394%2C%5C%22stop%5C%22%3A398%2C%5C%22mode%5C%22%3A3%2C%5C%22dis%5C%22%3A488%2C%5C%22cal%5C%22%3A19%2C%5C%22step%5C%22%3A607%7D%2C%7B%5C%22start%5C%22%3A399%2C%5C%22stop%5C%22%3A414%2C%5C%22mode%5C%22%3A4%2C%5C%22dis%5C%22%3A2220%2C%5C%22cal%5C%22%3A120%2C%5C%22step%5C%22%3A2371%7D%2C%7B%5C%22start%5C%22%3A415%2C%5C%22stop%5C%22%3A427%2C%5C%22mode%5C%22%3A3%2C%5C%22dis%5C%22%3A1268%2C%5C%22cal%5C%22%3A59%2C%5C%22step%5C%22%3A1489%7D%2C%7B%5C%22start%5C%22%3A428%2C%5C%22stop%5C%22%3A433%2C%5C%22mode%5C%22%3A1%2C%5C%22dis%5C%22%3A152%2C%5C%22cal%5C%22%3A4%2C%5C%22step%5C%22%3A238%7D%2C%7B%5C%22start%5C%22%3A434%2C%5C%22stop%5C%22%3A444%2C%5C%22mode%5C%22%3A3%2C%5C%22dis%5C%22%3A2295%2C%5C%22cal%5C%22%3A95%2C%5C%22step%5C%22%3A2874%7D%2C%7B%5C%22start%5C%22%3A445%2C%5C%22stop%5C%22%3A455%2C%5C%22mode%5C%22%3A4%2C%5C%22dis%5C%22%3A1592%2C%5C%22cal%5C%22%3A88%2C%5C%22step%5C%22%3A1664%7D%2C%7B%5C%22start%5C%22%3A456%2C%5C%22stop%5C%22%3A466%2C%5C%22mode%5C%22%3A3%2C%5C%22dis%5C%22%3A1072%2C%5C%22cal%5C%22%3A51%2C%5C%22step%5C%22%3A1245%7D%2C%7B%5C%22start%5C%22%3A467%2C%5C%22stop%5C%22%3A477%2C%5C%22mode%5C%22%3A4%2C%5C%22dis%5C%22%3A1036%2C%5C%22cal%5C%22%3A57%2C%5C%22step%5C%22%3A1124%7D%2C%7B%5C%22start%5C%22%3A478%2C%5C%22stop%5C%22%3A488%2C%5C%22mode%5C%22%3A3%2C%5C%22dis%5C%22%3A488%2C%5C%22cal%5C%22%3A19%2C%5C%22step%5C%22%3A607%7D%2C%7B%5C%22start%5C%22%3A489%2C%5C%22stop%5C%22%3A499%2C%5C%22mode%5C%22%3A4%2C%5C%22dis%5C%22%3A2220%2C%5C%22cal%5C%22%3A120%2C%5C%22step%5C%22%3A2371%7D%2C%7B%5C%22start%5C%22%3A500%2C%5C%22stop%5C%22%3A511%2C%5C%22mode%5C%22%3A3%2C%5C%22dis%5C%22%3A1268%2C%5C%22cal%5C%22%3A59%2C%5C%22step%5C%22%3A1489%7D%2C%7B%5C%22start%5C%22%3A512%2C%5C%22stop%5C%22%3A522%2C%5C%22mode%5C%22%3A1%2C%5C%22dis%5C%22%3A152%2C%5C%22cal%5C%22%3A4%2C%5C%22step%5C%22%3A238%7D%5D%7D%2C%5C%22goal%5C%22%3A8000%2C%5C%22tz%5C%22%3A%5C%2228800%5C%22%7D%22%2C%22source%22%3A24%2C%22type%22%3A0%7D%5D'

    find_date = re.compile(r".*?date%22%3A%22(.*?)%22%2C%22data.*?")
    find_step = re.compile(r".*?ttl%5C%22%3A(.*?)%2C%5C%22dis.*?")
    data_json = re.sub(find_date.findall(data_json)[0], today, str(data_json))
    data_json = re.sub(find_step.findall(data_json)[0], str(step), str(data_json))

    url = f'https://api-mifit-cn.huami.com/v1/data/band_data.json?&t={t}&r={str(uuid.uuid4())}'
    head = {
        "apptoken": app_token,
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data = f'userid={userid}&last_sync_data_time=1597306380&device_type=0&last_deviceid=DA932FFFFE8816E7&data_json={data_json}'

    response = requests.post(url, data=data, headers=head)
    if response.status_code != 200:
        return False, f"请求修改步数异常：{response.status_code}"
    res_json = response.json()
    message = res_json.get("message")
    if message == "success":
        return True, message
    else:
        return False, message


# ==============================================================================
# 3. 消息推送适配层 (青龙面板 notify 原生支持 + 内置 fallback)
# ==============================================================================
def push_plus(token, title, content):
    requestUrl = "http://www.pushplus.plus/send"
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
            print(f"pushplus推送完毕：{json_res.get('code')}-{json_res.get('msg')}")
        else:
            print("pushplus推送失败")
    except Exception as e:
        print(f"pushplus推送异常: {e}")

def push_wechat_webhook(key, title, content):
    requestUrl = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={key}"
    payload = {
        "msgtype": "markdown_v2",
        "markdown_v2": {
            "content": f"# {title}\n{content}"
        }
    }
    try:
        response = requests.post(requestUrl, json=payload)
        if response.status_code == 200:
            json_res = response.json()
            if json_res.get('errcode') == 0:
                print(f"企业微信推送完毕：{json_res.get('errmsg')}")
            else:
                print(f"企业微信推送失败：{json_res.get('errmsg')}")
        else:
            print("企业微信推送失败")
    except Exception as e:
        print(f"企业微信推送异常: {e}")

def push_telegram_bot(bot_token, chat_id, content):
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
                print(f"telegram bot推送完毕")
            else:
                print(f"telegram bot推送失败: {json.dumps(json_res)}")
        else:
            print(f"telegram bot推送失败: {response.status_code}")
    except Exception as e:
        print(f"telegram bot推送异常: {e}")

def send_notification(exec_results, summary, config):
    title = f"{format_now()} 小米运动刷步通知"
    content_text = summary + "\n"
    for item in exec_results:
        status_str = "成功" if item['success'] else "失败"
        content_text += f"\n- 账号: {item['user']} 刷步{status_str}，结果: {item['msg']}"

    # 1. 优先使用青龙面板 notify 推送
    if HAS_QL_NOTIFY:
        try:
            print("使用青龙面板内置 notify 模块发送通知...")
            ql_send(title, content_text)
            print("青龙内置 notify 发送成功")
            return
        except Exception as e:
            print(f"青龙内置 notify 发送异常: {e}，尝试使用内置渠道推送...")

    # 2. 回退使用内置推送配置
    if config:
        # PushPlus
        push_plus_token = config.get('PUSH_PLUS_TOKEN')
        if push_plus_token and push_plus_token != 'NO':
            html = f'<div><b>{summary}</b></div><ul>'
            for item in exec_results:
                st = "成功" if item['success'] else "失败"
                html += f'<li><span>账号：{item["user"]}</span> 刷步{st}：{item["msg"]}</li>'
            html += '</ul>'
            push_plus(push_plus_token, title, html)

        # 企业微信 Webhook
        wechat_key = config.get('PUSH_WECHAT_WEBHOOK_KEY')
        if wechat_key and wechat_key != 'NO':
            push_wechat_webhook(wechat_key, title, content_text)

        # Telegram
        tg_token = config.get('TELEGRAM_BOT_TOKEN')
        tg_chat = config.get('TELEGRAM_CHAT_ID')
        if tg_token and tg_token != 'NO' and tg_chat:
            tg_html = f'<b>{summary}</b>\n'
            for item in exec_results:
                st = "成功" if item['success'] else "失败"
                tg_html += f'• 账号：<code>{item["user"]}</code> 刷步{st}：{item["msg"]}\n'
            push_telegram_bot(tg_token, tg_chat, tg_html)


# ==============================================================================
# 4. 核心 Runner 类 (与 GA 版本 Token 级联处理完全一致)
# ==============================================================================
def desensitize_user_name(user):
    if len(user) <= 8:
        ln = max(math.floor(len(user) / 3), 1)
        return f'{user[:ln]}***{user[-ln:]}'
    return f'{user[:3]}****{user[-4:]}'

class MiMotionRunner:
    def __init__(self, _user, _passwd, user_tokens):
        self.user_id = None
        self.device_id = str(uuid.uuid4())
        user = str(_user).strip()
        password = str(_passwd).strip()
        self.invalid = False
        self.log_str = ""
        self.user_tokens = user_tokens
        if user == '' or password == '':
            self.error = "用户名或密码填写有误！"
            self.invalid = True
        self.password = password
        if user.startswith("+86") or "@" in user:
            self.user = user
        else:
            self.user = "+86" + user
        self.is_phone = self.user.startswith("+86")

    def login(self):
        user_token_info = self.user_tokens.get(self.user)
        if user_token_info is not None:
            access_token = user_token_info.get("access_token")
            login_token = user_token_info.get("login_token")
            app_token = user_token_info.get("app_token")
            self.device_id = user_token_info.get("device_id") or str(uuid.uuid4())
            self.user_id = user_token_info.get("user_id")

            # 检查 app_token
            ok, msg = check_app_token(app_token)
            if ok:
                self.log_str += "使用加密保存的app_token\n"
                return app_token
            else:
                self.log_str += f"app_token失效 重新获取 last grant time: {user_token_info.get('app_token_time')}\n"
                # 检查 login_token 是否可用
                app_token, msg = grant_app_token(login_token)
                if app_token is None:
                    self.log_str += f"login_token 失效 重新获取 last grant time: {user_token_info.get('login_token_time')}\n"
                    login_token, app_token, user_id, msg = grant_login_tokens(access_token, self.device_id, self.is_phone)
                    if login_token is None:
                        self.log_str += f"access_token 已失效：{msg} last grant time:{user_token_info.get('access_token_time')}\n"
                    else:
                        user_token_info["login_token"] = login_token
                        user_token_info["app_token"] = app_token
                        user_token_info["user_id"] = user_id
                        user_token_info["login_token_time"] = get_time()
                        user_token_info["app_token_time"] = get_time()
                        self.user_id = user_id
                        return app_token
                else:
                    self.log_str += "重新获取app_token成功\n"
                    user_token_info["app_token"] = app_token
                    user_token_info["app_token_time"] = get_time()
                    return app_token

        # access_token 失效 或 首次登录无本地 token 缓存
        access_token, msg = login_access_token(self.user, self.password)
        if access_token is None:
            self.log_str += f"登录获取accessToken失败：{msg}"
            return None

        login_token, app_token, user_id, msg = grant_login_tokens(access_token, self.device_id, self.is_phone)
        if login_token is None:
            self.log_str += f"登录提取的 access_token 无效：{msg}"
            return None

        user_token_info = {
            "access_token": access_token,
            "login_token": login_token,
            "app_token": app_token,
            "user_id": user_id,
            "access_token_time": get_time(),
            "login_token_time": get_time(),
            "app_token_time": get_time(),
            "device_id": self.device_id
        }
        self.user_tokens[self.user] = user_token_info
        self.user_id = user_id
        return app_token

    def login_and_post_step(self, step_num):
        if self.invalid:
            return "账号或密码配置有误", False
        app_token = self.login()
        if app_token is None:
            return "登录失败！", False

        self.log_str += f"提交步数：{step_num}\n"
        ok, msg = post_fake_brand_data(step_num, app_token, self.user_id)
        return f"修改步数（{step_num}）[{msg}]", ok


# ==============================================================================
# 5. Token 本地持久化 (加密保存/读取)
# ==============================================================================
DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "encrypted_tokens.data")

def prepare_user_tokens(aes_key_bytes) -> dict:
    if not os.path.exists(DATA_PATH):
        return dict()
    try:
        with open(DATA_PATH, 'rb') as f:
            data = f.read()
        decrypted_data = decrypt_data(data, aes_key_bytes, None)
        return json.loads(decrypted_data.decode('utf-8', errors='strict'))
    except Exception as e:
        print(f"读取/解密 token 数据失败 ({e})，将重新尝试登录")
        return dict()

def persist_user_tokens(user_tokens, aes_key_bytes):
    try:
        origin_str = json.dumps(user_tokens, ensure_ascii=False)
        cipher_data = encrypt_data(origin_str.encode("utf-8"), aes_key_bytes, None)
        with open(DATA_PATH, 'wb') as f:
            f.write(cipher_data)
        print("已成功加密保存 Token 数据至加密文件")
    except Exception as e:
        print(f"保存 Token 加密数据失败: {e}")


# ==============================================================================
# 6. 计算步数 (支持 STEP 自定义及随机生成)
# ==============================================================================
def get_int_value_default(_config: dict, _key, default):
    _config.setdefault(_key, default)
    try:
        return int(_config.get(_key))
    except (ValueError, TypeError):
        return default

def calculate_step(config: dict) -> int:
    # 优先使用环境变量 STEP (如果设置)
    step_env = os.environ.get("STEP", "").strip()
    if step_env:
        try:
            print(f"检测到自定义步数环境变量 STEP={step_env}")
            return int(step_env)
        except ValueError:
            print(f"警告: STEP={step_env} 无法转换为整数，使用随机步数计算规则")

    # 未使用 STEP 时，按照与 GA 版一致的时间线性增加步数逻辑
    time_bj = get_beijing_time()
    time_rate = min((time_bj.hour * 60 + time_bj.minute) / (22 * 60), 1)
    min_step = get_int_value_default(config, 'MIN_STEP', 18000)
    max_step = get_int_value_default(config, 'MAX_STEP', 25000)
    cur_min = int(time_rate * min_step)
    cur_max = int(time_rate * max_step)

    # 保护: 当区间无效时使用默认值
    if cur_min >= cur_max:
        cur_min, cur_max = min_step, max_step

    target_step = random.randint(cur_min, cur_max)
    print(f"已按时间线性计算步数范围({cur_min}~{cur_max}) 随机生成的目标步数: {target_step}")
    return target_step


# ==============================================================================
# 7. 主程序入口
# ==============================================================================
def run_single_account(total, idx, user_mi, passwd_mi, step_num, user_tokens):
    idx_info = f"[{idx + 1}/{total}]" if idx is not None else ""
    log_str = f"[{format_now()}]\n{idx_info}账号：{desensitize_user_name(user_mi)}\n"
    try:
        runner = MiMotionRunner(user_mi, passwd_mi, user_tokens)
        exec_msg, success = runner.login_and_post_step(step_num)
        log_str += runner.log_str
        log_str += f'{exec_msg}\n'
        exec_result = {"user": user_mi, "success": success, "msg": exec_msg}
    except Exception:
        err_msg = traceback.format_exc()
        log_str += f"执行异常:\n{err_msg}\n"
        exec_result = {"user": user_mi, "success": False, "msg": f"执行异常:{err_msg}"}
    print(log_str)
    return exec_result

def main():
    print("=== 小米运动 (Zepp Life) 刷步数 - 青龙面板版 ===")

    # 1. 从 MIMOTION_CONFIG (优先) 或 CONFIG 读取配置
    raw_config = os.environ.get("MIMOTION_CONFIG") or os.environ.get("CONFIG")
    if not raw_config:
        print("错误: 未配置 MIMOTION_CONFIG (或 CONFIG) 环境变量，无法继续执行！")
        exit(1)

    config = {}
    try:
        config = json.loads(raw_config)
    except Exception as e:
        print("错误: MIMOTION_CONFIG 格式不正确，请确保为合法 JSON 字符串！")
        print(f"具体错误: {e}")
        exit(1)

    users = config.get('USER')
    passwords = config.get('PWD')
    if not users or not passwords:
        print("错误: 未在配置中指定 USER 和 PWD！")
        exit(1)

    # 2. 读取 MIMOTION_AES_KEY (优先) 或 AES_KEY
    aes_key_str = os.environ.get("MIMOTION_AES_KEY") or os.environ.get("AES_KEY")
    aes_key_bytes = None
    encrypt_support = False
    if aes_key_str and HAS_CRYPTO:
        key_encoded = aes_key_str.encode('utf-8')
        if len(key_encoded) == 16:
            aes_key_bytes = key_encoded
            encrypt_support = True
        else:
            print("警告: MIMOTION_AES_KEY 必须为 16 字节字符，本次运行将不启用本地 Token 加密存储。")

    # 3. 准备 Token 缓存
    user_tokens = prepare_user_tokens(aes_key_bytes) if encrypt_support else dict()

    # 4. 计算步数
    step_num = calculate_step(config)

    # 5. 执行账号步数更新
    user_list = str(users).split('#')
    passwd_list = str(passwords).split('#')

    if len(user_list) != len(passwd_list):
        print(f"错误: 账号数[{len(user_list)}]与密码数[{len(passwd_list)}]不匹配！")
        exit(1)

    total = len(user_list)
    sleep_seconds = float(config.get('SLEEP_GAP') or 5)
    use_concurrent = config.get('USE_CONCURRENT') in [True, "True", "true"]

    exec_results = []
    if use_concurrent:
        import concurrent.futures
        print("已启用多线程并发模式执行...")
        with concurrent.futures.ThreadPoolExecutor() as executor:
            futures = [
                executor.submit(run_single_account, total, i, user_list[i], passwd_list[i], step_num, user_tokens)
                for i in range(total)
            ]
            for future in futures:
                exec_results.append(future.result())
    else:
        for idx, (u, p) in enumerate(zip(user_list, passwd_list)):
            exec_results.append(run_single_account(total, idx, u, p, step_num, user_tokens))
            if idx < total - 1 and sleep_seconds > 0:
                time.sleep(sleep_seconds)

    # 6. 保存 Token
    if encrypt_support:
        persist_user_tokens(user_tokens, aes_key_bytes)

    # 7. 汇总与推送
    success_count = sum(1 for r in exec_results if r.get('success'))
    summary = f"执行账号总数: {total}，成功: {success_count}，失败: {total - success_count}"
    print(f"\n{summary}")

    send_notification(exec_results, summary, config)


if __name__ == "__main__":
    main()
