# -*- coding: utf8 -*-
"""任务脚本把结果交给 notify()，由 GitHub Actions 推回飞书。"""
import json
import os

import requests

TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
MESSAGE_URL = "https://open.feishu.cn/open-apis/im/v1/messages"

# 与 main.py 里刷步通知用的是同一个应用和接收人。Secret 没填时用这里的默认值。
STEP_APP_ID = "cli_aa379716a0f8dcd0"
STEP_RECEIVE_ID = "ou_919a73c3ec0b7b52ac2cecf1276ee7ab"
STEP_RECEIVE_TYPE = "open_id"


def _text(env, key):
    return str(env.get(key) or "").strip()


def pick_target(env):
    """默认同刷步机器人。只有另外配了聊天应用密钥时，才发回下指令的会话。"""
    chat_id = _text(env, "FEISHU_CHAT_APP_ID")
    chat_secret = _text(env, "FEISHU_CHAT_APP_SECRET")
    if chat_id and chat_secret:
        if _text(env, "FEISHU_CHAT_RECEIVE_ID"):
            receive_id = _text(env, "FEISHU_CHAT_RECEIVE_ID")
            receive_type = _text(env, "FEISHU_CHAT_RECEIVE_ID_TYPE") or "chat_id"
        else:
            receive_id = _text(env, "JOB_RECEIVE_ID")
            receive_type = _text(env, "JOB_RECEIVE_ID_TYPE") or "chat_id"
        return chat_id, chat_secret, receive_id, receive_type

    app_id = _text(env, "FEISHU_APP_ID") or STEP_APP_ID
    app_secret = _text(env, "FEISHU_APP_SECRET")
    receive_id = _text(env, "FEISHU_RECEIVE_ID") or STEP_RECEIVE_ID
    receive_type = _text(env, "FEISHU_RECEIVE_ID_TYPE") or STEP_RECEIVE_TYPE
    return app_id, app_secret, receive_id, receive_type


def notify(text):
    app_id, app_secret, receive_id, receive_id_type = pick_target(os.environ)
    if not app_id or not app_secret:
        print("[任务] 缺少飞书应用密钥")
        return False
    if not receive_id:
        print("[任务] 缺少接收方")
        return False
    text = str(text or "").strip()
    if not text:
        print("[任务] 推送内容是空的")
        return False
    if len(text) > 8000:
        text = text[:8000] + "…"
    try:
        token_response = requests.post(
            TOKEN_URL,
            json={"app_id": app_id, "app_secret": app_secret},
            timeout=15,
        )
        token_data = token_response.json()
    except Exception as e:
        print(f"[任务] 获取飞书 token 失败：{e}")
        return False
    token = token_data.get("tenant_access_token")
    if token_data.get("code") != 0 or not token:
        print(f"[任务] 获取飞书 token 失败：{token_data.get('code')}-{token_data.get('msg')}")
        return False
    body = {
        "receive_id": receive_id,
        "msg_type": "text",
        "content": json.dumps({"text": text}, ensure_ascii=False),
    }
    try:
        response = requests.post(
            MESSAGE_URL,
            params={"receive_id_type": receive_id_type},
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            json=body,
            timeout=15,
        )
        data = response.json()
    except Exception as e:
        print(f"[任务] 推送失败：{e}")
        return False
    if data.get("code") != 0:
        print(f"[任务] 推送失败：{data.get('code')}-{data.get('msg')}")
        return False
    print("[任务] 已推送")
    return True
