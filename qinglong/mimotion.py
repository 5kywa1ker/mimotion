# -*- coding: utf-8 -*-
"""
小米运动（Zepp Life）自动刷步数 - 青龙面板版

环境变量配置（在青龙面板中设置）：
  MIMOTION_CONFIG  - JSON格式的用户配置（必填）
  MIMOTION_AES_KEY - 16位字符密钥，用于加密保存登录token（推荐配置）
  MIMOTION_DATA_PATH - token持久化文件路径（可选，默认为脚本同目录）
  STEP - 固定步数（可选，优先于随机步数逻辑，方便测试；也可在 CONFIG 中配置 "STEP" 字段）

CONFIG 格式示例：
{
  "USER": "abcxxx@xx.com",
  "PWD": "password",
  "MIN_STEP": "18000",
  "MAX_STEP": "25000",
  "PUSH_PLUS_TOKEN": "",
  "PUSH_PLUS_HOUR": "",
  "PUSH_PLUS_MAX": "30",
  "PUSH_WECHAT_WEBHOOK_KEY": "",
  "TELEGRAM_BOT_TOKEN": "",
  "TELEGRAM_CHAT_ID": "",
  "SLEEP_GAP": "5",
  "USE_CONCURRENT": "False"
}
"""

import math
import traceback
from datetime import datetime
import pytz
import uuid
import json
import random
import re
import time
import os
import sys

# 将脚本所在目录加入 sys.path，确保能正确导入 util 模块
script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

from util.aes_help import encrypt_data, decrypt_data
import util.zepp_helper as zeppHelper
import util.push_util as push_util


# 获取默认值转int
# 非数字值回退默认值，避免配置错误阻断主流程
def get_int_value_default(_config: dict, _key, default):
    _config.setdefault(_key, default)
    try:
        return int(_config.get(_key))
    except (ValueError, TypeError):
        print(f"配置项 {_key} 不是有效数字（值：{_config.get(_key)}），使用默认值 {default}")
        return default


# 获取当前时间对应的最大和最小步数
def get_min_max_by_time(hour=None, minute=None):
    if hour is None:
        hour = time_bj.hour
    if minute is None:
        minute = time_bj.minute
    time_rate = min((hour * 60 + minute) / (22 * 60), 1)
    min_step = get_int_value_default(config, 'MIN_STEP', 18000)
    max_step = get_int_value_default(config, 'MAX_STEP', 25000)
    return int(time_rate * min_step), int(time_rate * max_step)


# 账号脱敏
def desensitize_user_name(user):
    if len(user) <= 8:
        ln = max(math.floor(len(user) / 3), 1)
        return f'{user[:ln]}***{user[-ln:]}'
    return f'{user[:3]}****{user[-4:]}'


# 获取北京时间
def get_beijing_time():
    target_timezone = pytz.timezone('Asia/Shanghai')
    return datetime.now().astimezone(target_timezone)


# 格式化时间
def format_now():
    return get_beijing_time().strftime("%Y-%m-%d %H:%M:%S")


# 获取时间戳
def get_time():
    current_time = get_beijing_time()
    return "%.0f" % (current_time.timestamp() * 1000)


class MiMotionRunner:
    def __init__(self, _user, _passwd):
        self.user_id = None
        self.device_id = str(uuid.uuid4())
        user = str(_user)
        password = str(_passwd)
        self.invalid = False
        self.log_str = ""
        if user == '' or password == '':
            self.error = "用户名或密码填写有误！"
            self.invalid = True
            pass
        self.password = password
        if (user.startswith("+86")) or "@" in user:
            user = user
        else:
            user = "+86" + user
        if user.startswith("+86"):
            self.is_phone = True
        else:
            self.is_phone = False
        self.user = user

    # 登录
    def login(self):
        user_token_info = user_tokens.get(self.user)
        if user_token_info is not None:
            access_token = user_token_info.get("access_token")
            login_token = user_token_info.get("login_token")
            app_token = user_token_info.get("app_token")
            self.device_id = user_token_info.get("device_id")
            self.user_id = user_token_info.get("user_id")
            if self.device_id is None:
                self.device_id = str(uuid.uuid4())
                user_token_info["device_id"] = self.device_id
            ok, msg = zeppHelper.check_app_token(app_token, self.user_id)
            if ok:
                self.log_str += "使用加密保存的app_token\n"
                return app_token
            else:
                self.log_str += f"app_token失效 重新获取 last grant time: {user_token_info.get('app_token_time')}\n"
                app_token, msg = zeppHelper.grant_app_token(login_token)
                if app_token is None:
                    self.log_str += f"login_token 失效 重新获取 last grant time: {user_token_info.get('login_token_time')}\n"
                    login_token, app_token, user_id, msg = zeppHelper.grant_login_tokens(access_token, self.device_id,
                                                                                         self.is_phone)
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

        # access_token 失效 或者没有保存加密数据
        access_token, msg = zeppHelper.login_access_token(self.user, self.password)
        if access_token is None:
            self.log_str += "登录获取accessToken失败：%s" % msg
            return None
        login_token, app_token, user_id, msg = zeppHelper.grant_login_tokens(access_token, self.device_id,
                                                                             self.is_phone)
        if login_token is None:
            self.log_str += f"登录提取的 access_token 无效：{msg}"
            return None

        user_token_info = dict()
        user_token_info["access_token"] = access_token
        user_token_info["login_token"] = login_token
        user_token_info["app_token"] = app_token
        user_token_info["user_id"] = user_id
        user_token_info["access_token_time"] = get_time()
        user_token_info["login_token_time"] = get_time()
        user_token_info["app_token_time"] = get_time()
        if self.device_id is None:
            self.device_id = uuid.uuid4()
        user_token_info["device_id"] = self.device_id
        self.user_id = user_id
        user_tokens[self.user] = user_token_info
        return app_token

    # 主函数
    def login_and_post_step(self, min_step, max_step):
        if self.invalid:
            return "账号或密码配置有误", False
        app_token = self.login()
        if app_token is None:
            return "登陆失败！", False

        step = str(random.randint(min_step, max_step))
        self.log_str += f"已设置为随机步数范围({min_step}~{max_step}) 随机值:{step}\n"
        ok, msg = zeppHelper.post_fake_brand_data(step, app_token, self.user_id)
        return f"修改步数（{step}）[" + msg + "]", ok


def run_single_account(total, idx, user_mi, passwd_mi):
    idx_info = ""
    if idx is not None:
        idx_info = f"[{idx + 1}/{total}]"
    log_str = f"[{format_now()}]\n{idx_info}账号：{desensitize_user_name(user_mi)}\n"
    try:
        runner = MiMotionRunner(user_mi, passwd_mi)
        exec_msg, success = runner.login_and_post_step(min_step, max_step)
        log_str += runner.log_str
        log_str += f'{exec_msg}\n'
        exec_result = {"user": user_mi, "success": success, "msg": exec_msg}
    except:
        log_str += f"执行异常:{traceback.format_exc()}\n"
        exec_result = {"user": user_mi, "success": False, "msg": f"执行异常:{traceback.format_exc()}"}
    print(log_str)
    return exec_result


def get_token_data_path():
    """获取token持久化文件路径"""
    # 优先使用环境变量指定的路径
    data_path = os.environ.get("MIMOTION_DATA_PATH")
    if data_path:
        # 如果指定了目录，则在该目录下创建文件
        if os.path.isdir(data_path):
            return os.path.join(data_path, "mimotion_tokens.data")
        return data_path
    # 默认使用脚本同目录
    return os.path.join(script_dir, "mimotion_tokens.data")


def prepare_user_tokens() -> dict:
    """从加密文件加载token"""
    data_path = get_token_data_path()
    if os.path.exists(data_path):
        with open(data_path, 'rb') as f:
            data = f.read()
        try:
            decrypted_data = decrypt_data(data, aes_key, None)
            return json.loads(decrypted_data.decode('utf-8', errors='strict'))
        except:
            print("密钥不正确或者加密内容损坏 放弃token")
            return dict()
    else:
        return dict()


def persist_user_tokens():
    """将token加密保存到文件"""
    data_path = get_token_data_path()
    origin_str = json.dumps(user_tokens, ensure_ascii=False)
    cipher_data = encrypt_data(origin_str.encode("utf-8"), aes_key, None)
    with open(data_path, 'wb') as f:
        f.write(cipher_data)
        f.flush()
    print(f"Token已加密保存到：{data_path}")


def execute():
    user_list = users.split('#')
    passwd_list = passwords.split('#')
    exec_results = []
    if len(user_list) == len(passwd_list):
        idx, total = 0, len(user_list)
        if use_concurrent:
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                exec_results = list(executor.map(lambda x: run_single_account(total, x[0], *x[1]),
                                                 enumerate(zip(user_list, passwd_list))))
        else:
            for user_mi, passwd_mi in zip(user_list, passwd_list):
                exec_results.append(run_single_account(total, idx, user_mi, passwd_mi))
                idx += 1
                if idx < total:
                    time.sleep(sleep_seconds)
        if encrypt_support:
            persist_user_tokens()
        success_count = 0
        push_results = []
        for result in exec_results:
            push_results.append(result)
            if result['success'] is True:
                success_count += 1
        summary = f"\n执行账号总数{total}，成功：{success_count}，失败：{total - success_count}"
        print(summary)
        try:
            # 推送失败不影响刷步数主流程，仅打印异常
            push_util.push_results(push_results, summary, push_config)
        except Exception:
            print(f"推送通知异常（不影响刷步数结果）：{traceback.format_exc()}")
    else:
        print(f"账号数长度[{len(user_list)}]和密码数长度[{len(passwd_list)}]不匹配，跳过执行")
        exit(1)


if __name__ == "__main__":
    print(f"====== 小米运动刷步数 青龙面板版 ======")
    print(f"当前时间：{get_beijing_time().strftime('%Y-%m-%d %H:%M:%S')} (北京时间)")

    # 北京时间
    time_bj = get_beijing_time()
    encrypt_support = False
    user_tokens = dict()

    # 读取 AES_KEY（支持多种环境变量名兼容）
    aes_key_str = os.environ.get("MIMOTION_AES_KEY") or os.environ.get("AES_KEY")
    if aes_key_str:
        aes_key = aes_key_str.encode('utf-8')
        if len(aes_key) == 16:
            encrypt_support = True
            user_tokens = prepare_user_tokens()
        else:
            print(f"AES_KEY长度不正确({len(aes_key)})，无法使用加密保存功能，需要16个字符")
    else:
        print("未配置AES_KEY，每次执行将重新登录（无法保存登录状态）")

    # 读取 CONFIG（支持多种环境变量名兼容）
    config_str = os.environ.get("MIMOTION_CONFIG") or os.environ.get("CONFIG")
    if not config_str:
        print("未配置 MIMOTION_CONFIG 或 CONFIG 环境变量，无法执行")
        exit(1)

    # region 初始化参数
    config = dict()
    try:
        # 兼容青龙面板环境变量中换行/制表符被转义为字面量（如 \n）的情况
        config_str = config_str.strip().replace('\\n', '\n').replace('\\t', '\t').replace('\\r', '\r')
        config = dict(json.loads(config_str))
    except:
        print("CONFIG格式不正确，请检查配置，请严格按照JSON格式：使用双引号包裹字段和值，逗号不能多也不能少")
        traceback.print_exc()
        exit(1)

    # 创建推送配置对象
    push_config = push_util.PushConfig(
        push_plus_token=config.get('PUSH_PLUS_TOKEN'),
        push_plus_hour=config.get('PUSH_PLUS_HOUR'),
        push_plus_max=get_int_value_default(config, 'PUSH_PLUS_MAX', 30),
        push_wechat_webhook_key=config.get('PUSH_WECHAT_WEBHOOK_KEY'),
        telegram_bot_token=config.get('TELEGRAM_BOT_TOKEN'),
        telegram_chat_id=config.get('TELEGRAM_CHAT_ID'),
        serverchan_key=config.get('SERVERCHAN_KEY'),
        bark_key=config.get('BARK_KEY'),
        dingtalk_token=config.get('DINGTALK_TOKEN'),
        dingtalk_secret=config.get('DINGTALK_SECRET'),
        feishu_webhook=config.get('FEISHU_WEBHOOK')
    )
    sleep_seconds = config.get('SLEEP_GAP')
    if sleep_seconds is None or sleep_seconds == '':
        sleep_seconds = 5
    sleep_seconds = float(sleep_seconds)
    users = config.get('USER')
    passwords = config.get('PWD')
    if users is None or passwords is None:
        print("未正确配置账号密码（USER/PWD），无法执行")
        exit(1)
    min_step, max_step = get_min_max_by_time()
    # 固定步数：优先读取 STEP 环境变量，其次 CONFIG 中的 STEP 字段（方便测试）
    fixed_step = os.environ.get("STEP") or config.get('STEP')
    if fixed_step is not None and str(fixed_step).strip() != '':
        try:
            step_int = int(fixed_step)
            if step_int > 0:
                print(f"检测到固定步数 STEP={step_int}，跳过时间比例随机计算")
                min_step = max_step = step_int
            else:
                print(f"STEP 必须为正整数（当前：{fixed_step}），忽略并使用随机步数")
        except (ValueError, TypeError):
            print(f"STEP 不是有效数字（当前：{fixed_step}），忽略并使用随机步数")
    use_concurrent = config.get('USE_CONCURRENT')
    if use_concurrent is not None and str(use_concurrent).lower() == 'true':
        use_concurrent = True
    else:
        print(f"多账号执行间隔：{sleep_seconds}秒")
        use_concurrent = False
    # endregion

    execute()
    print(f"\n====== 执行完毕 ======")
