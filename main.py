# -*- coding: utf8 -*-
import base64
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

import requests
from util.aes_help import encrypt_data, decrypt_data
import util.feishu_bot as feishuBot
import util.zepp_helper as zeppHelper


# 获取默认值转int
def get_int_value_default(_config: dict, _key, default):
    _config.setdefault(_key, default)
    return int(_config.get(_key))


# 获取当前时间对应的最大和最小步数（原方法 - 已弃用）
def get_min_max_by_time(hour=None, minute=None):
    if hour is None:
        hour = time_bj.hour
    if minute is None:
        minute = time_bj.minute
    time_rate = min((hour * 60 + minute) / (22 * 60), 1)
    min_step = get_int_value_default(config, 'MIN_STEP', 18000)
    max_step = get_int_value_default(config, 'MAX_STEP', 25000)
    return int(time_rate * min_step), int(time_rate * max_step)


# 根据星期几获取步数范围
def get_weekday_step_range():
    """
    根据当前是星期几返回不同的步数范围
    周一到周五：8000-12000
    周末（周六、周日）：6000-10000
    
    返回:
        tuple: (最小步数, 最大步数)
    """
    current_time = get_beijing_time()
    weekday = current_time.weekday()  # 0=周一, 1=周二, ..., 6=周日

    if weekday < 5:  # 周一到周五 (0-4)
        min_step = 6500
        max_step = 13000
        day_name = ["周一", "周二", "周三", "周四", "周五"][weekday]
    else:  # 周末 (5-6)
        min_step = 6000
        max_step = 10000
        day_name = "周六" if weekday == 5 else "周日"

    print(f"今天是{day_name}，步数范围：{min_step} ~ {max_step}")
    return min_step, max_step


# 虚拟ip地址
def fake_ip():
    # 随便找的国内IP段：223.64.0.0 - 223.117.255.255
    return f"{223}.{random.randint(64, 117)}.{random.randint(0, 255)}.{random.randint(0, 255)}"


# 账号脱敏
def desensitize_user_name(user):
    if len(user) <= 8:
        ln = max(math.floor(len(user) / 3), 1)
        return f'{user[:ln]}***{user[-ln:]}'
    return f'{user[:3]}****{user[-4:]}'


# 获取北京时间
def get_beijing_time():
    target_timezone = pytz.timezone('Asia/Shanghai')
    # 获取当前时间
    return datetime.now().astimezone(target_timezone)


# 格式化时间
def format_now():
    return get_beijing_time().strftime("%Y-%m-%d %H:%M:%S")


# 获取时间戳
def get_time():
    current_time = get_beijing_time()
    return "%.0f" % (current_time.timestamp() * 1000)


# 获取登录code
def get_access_token(location):
    code_pattern = re.compile("(?<=access=).*?(?=&)")
    result = code_pattern.findall(location)
    if result is None or len(result) == 0:
        return None
    return result[0]


def get_error_code(location):
    code_pattern = re.compile("(?<=error=).*?(?=&)")
    result = code_pattern.findall(location)
    if result is None or len(result) == 0:
        return None
    return result[0]


#
# # pushplus消息推送
# def push_plus(title, content):
#     requestUrl = f"http://www.pushplus.plus/send"
#     data = {
#         "token": PUSH_PLUS_TOKEN,
#         "title": title,
#         "content": content,
#         "template": "html",
#         "channel": "wechat"
#     }
#     try:
#         response = requests.post(requestUrl, data=data)
#         if response.status_code == 200:
#             json_res = response.json()
#             print(f"pushplus推送完毕：{json_res['code']}-{json_res['msg']}")
#         else:
#             print("pushplus推送失败")
#     except:
#         print("pushplus推送异常")


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
        user = normalize_account(user)
        self.is_phone = user.startswith("+86")
        self.user = user
        # self.fake_ip_addr = fake_ip()
        # self.log_str += f"创建虚拟ip地址：{self.fake_ip_addr}\n"

    def _remember_token(self, info):
        if not info.get("device_id"):
            info["device_id"] = str(self.device_id)
        self.device_id = info.get("device_id") or self.device_id
        self.user_id = info.get("user_id")
        user_tokens[self.user] = info
        persist_user_tokens()

    def _refresh_from_saved(self, info):
        login_token = info.get("login_token")
        access_token = info.get("access_token")
        if login_token:
            app_token, msg = zeppHelper.grant_app_token(login_token)
            if app_token:
                info["app_token"] = app_token
                info["app_token_time"] = get_time()
                self.log_str += "重新获取 app_token 成功\n"
                self._remember_token(info)
                return app_token
            self.log_str += f"login_token 失效：{msg} last grant time: {info.get('login_token_time')}\n"
        if access_token:
            login_token, app_token, user_id, msg = zeppHelper.grant_login_tokens(
                access_token, self.device_id, self.is_phone
            )
            if login_token:
                info["login_token"] = login_token
                info["app_token"] = app_token
                info["user_id"] = user_id
                info["login_token_time"] = get_time()
                info["app_token_time"] = get_time()
                self.user_id = user_id
                self.log_str += "用 access_token 重新获取成功\n"
                self._remember_token(info)
                return app_token
            self.log_str += f"access_token 已失效：{msg} last grant time:{info.get('access_token_time')}\n"
        return None

    # 登录。7 天内直接用 json 里的 token，不再每次请求。
    def login(self, force_refresh=False):
        user_token_info = user_tokens.get(self.user)
        if user_token_info is not None:
            self.device_id = user_token_info.get("device_id") or self.device_id
            self.user_id = user_token_info.get("user_id")
            if self.device_id is None:
                self.device_id = str(uuid.uuid4())
                user_token_info["device_id"] = self.device_id
            if not force_refresh and token_still_valid(user_token_info):
                self.log_str += "使用 json 中的 token，未满 7 天，跳过重新获取\n"
                return user_token_info.get("app_token")
            self.log_str += "token 已超过 7 天或已失效，重新获取并写回 json\n"
            try:
                refreshed = self._refresh_from_saved(user_token_info)
            except Exception as e:
                if not force_refresh and user_token_info.get("app_token"):
                    self.log_str += f"重新获取失败，继续使用已保存 token：{e}\n"
                    return user_token_info.get("app_token")
                raise
            if refreshed:
                return refreshed

        access_token, msg = zeppHelper.login_access_token(self.user, self.password)
        if access_token is None:
            self.log_str += "登录获取accessToken失败：%s" % msg
            return None
        login_token, app_token, user_id, msg = zeppHelper.grant_login_tokens(access_token, self.device_id,
                                                                             self.is_phone)
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
            "device_id": str(self.device_id),
        }
        self._remember_token(user_token_info)
        return app_token

    def submit_step(self, step, app_token):
        ok, msg = zeppHelper.post_fake_brand_data(step, app_token, self.user_id)
        if ok or not is_token_rejected(msg):
            return ok, msg
        self.log_str += f"提交时 token 已过期（{msg}），重新获取后再写入\n"
        app_token = self.login(force_refresh=True)
        if app_token is None:
            return False, msg
        return zeppHelper.post_fake_brand_data(step, app_token, self.user_id)

    # 主函数
    def login_and_post_step(self, min_step, max_step):
        if self.invalid:
            return "账号或密码配置有误", False, None
        app_token = self.login()
        if app_token is None:
            return "登陆失败！", False, None

        step = str(random.randint(min_step, max_step))
        self.log_str += f"已设置为随机步数范围({min_step}~{max_step}) 随机值:{step}\n"
        ok, msg = self.submit_step(step, app_token)
        return f"修改步数（{step}）[" + msg + "]", ok, step

    def login_and_post_exact_step(self, step):
        if self.invalid:
            return "账号或密码配置有误", False, None
        app_token = self.login()
        if app_token is None:
            return "登陆失败！", False, None

        step = str(int(step))
        self.log_str += f"已设置为指定步数:{step}\n"
        ok, msg = self.submit_step(step, app_token)
        return f"修改步数（{step}）[" + msg + "]", ok, step


#
# # 启动主函数
# def push_to_push_plus(exec_results, summary):
#     # 判断是否需要pushplus推送
#     if PUSH_PLUS_TOKEN is not None and PUSH_PLUS_TOKEN != '' and PUSH_PLUS_TOKEN != 'NO':
#         if PUSH_PLUS_HOUR is not None and PUSH_PLUS_HOUR.isdigit():
#             if time_bj.hour != int(PUSH_PLUS_HOUR):
#                 print(f"当前设置push_plus推送整点为：{PUSH_PLUS_HOUR}, 当前整点为：{time_bj.hour}，跳过推送")
#                 return
#         html = f'<div>{summary}</div>'
#         if len(exec_results) >= PUSH_PLUS_MAX:
#             html += '<div>账号数量过多，详细情况请前往github actions中查看</div>'
#         else:
#             html += '<ul>'
#             for exec_result in exec_results:
#                 success = exec_result['success']
#                 if success is not None and success is True:
#                     html += f'<li><span>账号：{exec_result["user"]}</span>刷步数成功，接口返回：{exec_result["msg"]}</li>'
#                 else:
#                     html += f'<li><span>账号：{exec_result["user"]}</span>刷步数失败，失败原因：{exec_result["msg"]}</li>'
#             html += '</ul>'
#         push_plus(f"{format_now()} 刷步数通知", html)
#
#
# def run_single_account(total, idx, user_mi, passwd_mi):
#     idx_info = ""
#     if idx is not None:
#         idx_info = f"[{idx + 1}/{total}]"
#     log_str = f"[{format_now()}]\n{idx_info}账号：{desensitize_user_name(user_mi)}\n"
#     try:
#         runner = MiMotionRunner(user_mi, passwd_mi)
#         exec_msg, success = runner.login_and_post_step(min_step, max_step)
#         log_str += runner.log_str
#         log_str += f'{exec_msg}\n'
#         exec_result = {"user": user_mi, "success": success,
#                        "msg": exec_msg}
#     except:
#         log_str += f"执行异常:{traceback.format_exc()}\n"
#         log_str += traceback.format_exc()
#         exec_result = {"user": user_mi, "success": False,
#                        "msg": f"执行异常:{traceback.format_exc()}"}
#     print(log_str)
#     return exec_result


# def execute():
#     user_list = users.split('#')
#     passwd_list = passwords.split('#')
#     exec_results = []
#     if len(user_list) == len(passwd_list):
#         idx, total = 0, len(user_list)
#         if use_concurrent:
#             import concurrent.futures
#             with concurrent.futures.ThreadPoolExecutor() as executor:
#                 exec_results = executor.map(lambda x: run_single_account(total, x[0], *x[1]),
#                                             enumerate(zip(user_list, passwd_list)))
#         else:
#             for user_mi, passwd_mi in zip(user_list, passwd_list):
#                 exec_results.append(run_single_account(total, idx, user_mi, passwd_mi))
#                 idx += 1
#                 if idx < total:
#                     # 每个账号之间间隔一定时间请求一次，避免接口请求过于频繁导致异常
#                     time.sleep(sleep_seconds)
#         if encrypt_support:
#             persist_user_tokens()
#         success_count = 0
#         push_results = []
#         for result in exec_results:
#             push_results.append(result)
#             if result['success'] is True:
#                 success_count += 1
#         summary = f"\n执行账号总数{total}，成功：{success_count}，失败：{total - success_count}"
#         print(summary)
#         push_to_push_plus(push_results, summary)
#     else:
#         print(f"账号数长度[{len(user_list)}]和密码数长度[{len(passwd_list)}]不匹配，跳过执行")
#         exit(1)


# 登录名来自 CONFIG 密钥或本地配置。AES 密钥取登录名前 16 个字符，不足补 0。
# json 里只保存密文和时间，不写登录账号，也不写密钥。
TOKEN_STORE_FILE = "token_store.json"
TOKEN_VALID_MS = 7 * 24 * 60 * 60 * 1000
LOGIN_KEY_BYTES = 16
token_login_name = ""


def normalize_account(user):
    user = str(user).strip()
    if user.startswith("+86") or "@" in user:
        return user
    return "+86" + user


def login_aes_key(login_name):
    raw = str(login_name).strip().encode("utf-8")
    return raw[:LOGIN_KEY_BYTES].ljust(LOGIN_KEY_BYTES, b"0")


def token_still_valid(info):
    if not info or not info.get("app_token") or not info.get("app_token_time"):
        return False
    try:
        age = int(get_time()) - int(float(info.get("app_token_time")))
    except (TypeError, ValueError):
        return False
    return age < TOKEN_VALID_MS


def is_token_rejected(msg):
    text = str(msg or "").lower()
    if any(code in text for code in ("401", "403")):
        return True
    keys = ("token", "auth", "expired", "expire", "过期", "失效", "invalid", "未登录")
    return any(key in text for key in keys)


def _load_legacy_tokens(legacy_key):
    data_path = "encrypted_tokens.data"
    if legacy_key is None or len(legacy_key) != 16 or not os.path.exists(data_path):
        return None
    try:
        with open(data_path, "rb") as f:
            data = f.read()
        plain = decrypt_data(data, legacy_key, None)
        loaded = json.loads(plain.decode("utf-8", errors="strict"))
    except Exception:
        return None
    if isinstance(loaded, dict) and loaded:
        print("已从旧的 encrypted_tokens.data 读出 token，接下来写入 json")
        return loaded
    return None


def _write_token_file(token_text, saved_at):
    body = {
        "saved_at": saved_at,
        "token": token_text,
    }
    with open(TOKEN_STORE_FILE, "w", encoding="utf-8") as f:
        json.dump(body, f, ensure_ascii=False, indent=2)
        f.write("\n")


def prepare_user_tokens(legacy_key=None) -> dict:
    if os.path.exists(TOKEN_STORE_FILE):
        try:
            with open(TOKEN_STORE_FILE, "r", encoding="utf-8") as f:
                body = json.load(f)
            cipher = base64.b64decode(body["token"])
            plain = decrypt_data(cipher, aes_key, None)
            loaded = json.loads(plain.decode("utf-8", errors="strict"))
            if not isinstance(loaded, dict):
                raise ValueError("token json 内容无效")
            if "name_prefix" in body:
                _write_token_file(body["token"], body.get("saved_at") or format_now())
            return loaded
        except Exception:
            print("token 文件无法解密，将重新获取并覆盖")
            return dict()
    legacy = _load_legacy_tokens(legacy_key)
    if legacy is not None:
        return legacy
    return dict()


def persist_user_tokens():
    if not user_tokens:
        return
    origin = json.dumps(user_tokens, ensure_ascii=False).encode("utf-8")
    cipher = encrypt_data(origin, aes_key, None)
    _write_token_file(base64.b64encode(cipher).decode("ascii"), format_now())
    print(f"已加密写入 {TOKEN_STORE_FILE}")


# ==================== 本地执行配置 ====================
# 请在这里直接修改配置，无需设置环境变量

# 小米运动账号配置（手机号需要加+86前缀，或者使用邮箱）
USER = "109@qq.com"  # 例如: "+8613812345678" 或 "example@email.com"
PWD = "yue3"  # 小米运动密码
# AES密钥配置（用于加密保存token，可选功能）
AES_KEY = "asdhf34564edsqwe"  # 例如: "your16charkey123"

# 飞书应用机器人。密钥不要写进仓库，用环境变量 FEISHU_APP_SECRET 或 GitHub Secret。
FEISHU_APP_ID = "cli_aa379716a0f8dcd0"
FEISHU_APP_SECRET = ""
# 接收方。单聊填 open_id（ou_ 开头）
FEISHU_RECEIVE_ID = "ou_919a73c3ec0b7b52ac2cecf1276ee7ab"
FEISHU_RECEIVE_ID_TYPE = "open_id"  # chat_id / open_id / user_id / email


# ===================================================


def pick_config(config_data, key, default=""):
    env_val = os.environ.get(key)
    if env_val:
        return env_val
    if config_data and config_data.get(key):
        return config_data.get(key)
    return default


def build_step_notice(success, step, detail, test=False):
    time_str = get_beijing_time().strftime("%Y-%m-%d %H:%M:%S")
    kind = "测试步数" if test else "步数"
    if success:
        lines = [
            f"时间：{time_str}",
            f"步数：{step}",
        ]
        if test:
            lines.append("这是测试写入。下次成功后会在这个数字上加 1。")
        else:
            lines.append("已按这个数量写入今天的步数。")
        return feishuBot.format_notice(kind, "今日步数已更新", lines)
    lines = [f"时间：{time_str}"]
    if step:
        lines.append(f"步数：{step}")
        lines.append("这次没有写入成功。")
    else:
        lines.append("步数：未生成")
    if detail:
        lines.append(f"原因：{detail}")
    return feishuBot.format_notice(kind, "今日步数没有更新", lines)


def notify_feishu(config_data, text):
    feishuBot.send_text(
        pick_config(config_data, "FEISHU_APP_ID", FEISHU_APP_ID),
        pick_config(config_data, "FEISHU_APP_SECRET", FEISHU_APP_SECRET),
        text,
        pick_config(config_data, "FEISHU_RECEIVE_ID", FEISHU_RECEIVE_ID),
        pick_config(config_data, "FEISHU_RECEIVE_ID_TYPE", FEISHU_RECEIVE_ID_TYPE) or "open_id",
    )


def run_local(fixed_step=None):
    """本地执行主函数 - 单账号版本。fixed_step 有值时写入这个固定步数。"""
    global time_bj, encrypt_support, user_tokens, aes_key, config, min_step, max_step, token_login_name

    print("=" * 50)
    print("小米运动刷步数工具 - 本地版")
    print("=" * 50)

    # 北京时间
    time_bj = get_beijing_time()
    print(f"当前时间：{format_now()}")
    print("-" * 50)

    # ========== 配置读取：优先环境变量，其次本地配置 ==========

    # 1. 读取账号密码配置
    user = None
    pwd = None
    config_data = None

    # 优先从环境变量 CONFIG 中读取
    if os.environ.get("CONFIG"):
        try:
            config_data = json.loads(os.environ.get("CONFIG"))
            user = config_data.get('USER')
            pwd = config_data.get('PWD')
            if user and pwd:
                print("✓ 账号密码：从环境变量 CONFIG 中读取")
            else:
                print("⚠ 环境变量 CONFIG 中未找到有效的账号密码配置")
        except Exception as e:
            print(f"⚠ 解析环境变量 CONFIG 失败：{e}")

    # 如果环境变量中没有，使用本地配置
    if not user or not pwd:
        user = USER
        pwd = PWD
        if user == "your_phone_or_email" or pwd == "your_password":
            print("✗ 错误：请先在代码中配置您的账号和密码！")
            print("  请修改 USER 和 PWD 常量的值，或设置环境变量 CONFIG")
            exit(1)
        print("✓ 账号密码：使用本地配置常量")

    print(f"  111账号：{desensitize_user_name(user)}")
    print(f"  user：{user}")
    print(f"  pwd：{pwd}")

    # token 用登录名前 16 位加密后写入 json。登录名优先来自 CONFIG 密钥。
    token_login_name = normalize_account(user)
    aes_key = login_aes_key(token_login_name)
    encrypt_support = True
    legacy_raw = os.environ.get("AES_KEY") or AES_KEY or ""
    legacy_key = legacy_raw.encode("utf-8") if len(legacy_raw.encode("utf-8")) == 16 else None
    user_tokens = prepare_user_tokens(legacy_key)
    if user_tokens and not os.path.exists(TOKEN_STORE_FILE):
        persist_user_tokens()
    print("✓ token：用登录名前 16 位加密，写入 token_store.json")
    print("  未满 7 天直接复用；接口提示过期或满 7 天后重新获取并覆盖 json")

    print("-" * 50)

    # 根据星期几获取步数范围；测试模式使用指定步数
    if fixed_step is None:
        min_step, max_step = get_weekday_step_range()
    else:
        fixed_step = int(fixed_step)
        min_step, max_step = fixed_step, fixed_step
        print(f"测试模式，指定步数：{fixed_step}")

    # 初始化配置字典（用于兼容现有代码）
    config = {}

    # 执行刷步数
    print("\n开始执行刷步数...")
    print("-" * 50)

    success = False
    try:
        runner = MiMotionRunner(user, pwd)
        if fixed_step is None:
            exec_msg, success, step = runner.login_and_post_step(min_step, max_step)
        else:
            exec_msg, success, step = runner.login_and_post_exact_step(fixed_step)

        print(runner.log_str)
        print(f"执行结果：{exec_msg}")

        if success:
            print("\n✓ 刷步数成功！")
        else:
            print("\n✗ 刷步数失败！")
        notice = build_step_notice(success, step, exec_msg, test=fixed_step is not None)

    except Exception as e:
        print(f"执行异常：{str(e)}")
        traceback.print_exc()
        success = False
        failed_step = str(fixed_step) if fixed_step is not None else None
        notice = build_step_notice(False, failed_step, str(e), test=fixed_step is not None)

    notify_feishu(config_data, notice)

    print("=" * 50)
    return success


# ==================== GitHub Actions 执行配置（原版） ====================
#
# if __name__ == "__main__":
#     # 北京时间
#     time_bj = get_beijing_time()
#     encrypt_support = False
#     user_tokens = dict()
#     if os.environ.__contains__("AES_KEY") is True:
#         aes_key = os.environ.get("AES_KEY")
#         if aes_key is not None:
#             aes_key = aes_key.encode('utf-8')
#             if len(aes_key) == 16:
#                 encrypt_support = True
#         if encrypt_support:
#             user_tokens = prepare_user_tokens()
#         else:
#             print("AES_KEY未设置或者无效 无法使用加密保存功能")
#     if os.environ.__contains__("CONFIG") is False:
#         print("未配置CONFIG变量，无法执行")
#         exit(1)
#     else:
#         # region 初始化参数
#         config = dict()
#         try:
#             config = dict(json.loads(os.environ.get("CONFIG")))
#         except:
#             print("CONFIG格式不正确，请检查Secret配置，请严格按照JSON格式：使用双引号包裹字段和值，逗号不能多也不能少")
#             traceback.print_exc()
#             exit(1)
#         PUSH_PLUS_TOKEN = config.get('PUSH_PLUS_TOKEN')
#         PUSH_PLUS_HOUR = config.get('PUSH_PLUS_HOUR')
#         PUSH_PLUS_MAX = get_int_value_default(config, 'PUSH_PLUS_MAX', 30)
#         sleep_seconds = config.get('SLEEP_GAP')
#         if sleep_seconds is None or sleep_seconds == '':
#             sleep_seconds = 5
#         sleep_seconds = float(sleep_seconds)
#         users = config.get('USER')
#         passwords = config.get('PWD')
#         if users is None or passwords is None:
#             print("未正确配置账号密码，无法执行")
#             exit(1)
#         min_step, max_step = get_min_max_by_time()
#         use_concurrent = config.get('USE_CONCURRENT')
#         if use_concurrent is not None and use_concurrent == 'True':
#             use_concurrent = True
#         else:
#             print(f"多账号执行间隔：{sleep_seconds}")
#             use_concurrent = False
#         # endregion
#         execute()


if __name__ == "__main__":
    # 失败时返回非 0，定时任务才不会把今天记成已执行
    if not run_local():
        exit(1)
