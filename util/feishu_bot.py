# -*- coding: utf8 -*-
import json

import requests

TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
MESSAGE_URL = "https://open.feishu.cn/open-apis/im/v1/messages"
CHATS_URL = "https://open.feishu.cn/open-apis/im/v1/chats"


def get_tenant_access_token(app_id: str, app_secret: str) -> str | None:
    try:
        response = requests.post(
            TOKEN_URL,
            json={"app_id": app_id, "app_secret": app_secret},
            timeout=15,
        )
        data = response.json()
    except Exception as e:
        print(f"[飞书] 获取 token 异常：{e}")
        return None
    if data.get("code") != 0 or not data.get("tenant_access_token"):
        print(f"[飞书] 获取 token 失败：{data.get('code')}-{data.get('msg')}")
        return None
    return data["tenant_access_token"]


def list_chats(token: str) -> list:
    try:
        response = requests.get(
            CHATS_URL,
            headers={"Authorization": f"Bearer {token}"},
            params={"page_size": 20},
            timeout=15,
        )
        data = response.json()
    except Exception as e:
        print(f"[飞书] 获取群列表异常：{e}")
        return []
    if data.get("code") != 0:
        print(f"[飞书] 获取群列表失败：{data.get('code')}-{data.get('msg')}")
        return []
    return data.get("data", {}).get("items") or []


def format_notice(msg_type: str, title: str, lines) -> str:
    """统一通知格式。msg_type 是类型标识，例如「步数」。"""
    parts = [f"【{msg_type}】{title}"]
    for line in lines:
        if line:
            parts.append(line)
    return "\n".join(parts)


def send_text(app_id: str, app_secret: str, text: str, receive_id: str = "", receive_id_type: str = "chat_id") -> bool:
    """用应用机器人给指定会话发文本。未填 receive_id 时，取机器人所在的第一个群。"""
    if not app_id or not app_secret:
        print("[飞书] 未配置 App ID 或密钥，跳过推送")
        return False

    token = get_tenant_access_token(app_id, app_secret)
    if not token:
        return False

    if not receive_id:
        if receive_id_type != "chat_id":
            print("[飞书] 未配置接收方 ID，跳过推送")
            return False
        chats = list_chats(token)
        if not chats:
            print("[飞书] 机器人不在任何群里。请先把机器人拉进目标群，或填写 FEISHU_RECEIVE_ID")
            return False
        receive_id = chats[0].get("chat_id") or ""
        name = chats[0].get("name") or ""
        print(f"[飞书] 接收群：{name} ({receive_id})")
        if len(chats) > 1:
            print("  机器人在多个群中，当前使用第一个。可设置 FEISHU_RECEIVE_ID 指定群")

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
        print(f"[飞书] 推送异常：{e}")
        return False
    if data.get("code") != 0:
        print(f"[飞书] 推送失败：{data.get('code')}-{data.get('msg')}")
        return False
    print("[飞书] 推送成功")
    return True
