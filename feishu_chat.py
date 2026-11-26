# -*- coding: utf8 -*-
"""飞书聊天机器人。

本机用长连接接收消息。普通问题调用 DeepSeek 回复。
用户要定时结果时，工人把 Python 写进 jobs/，提交到 GitHub，
由 Actions 到点运行，再把结果推回这个飞书会话。
刷步通知仍走原来的应用，这个文件只用新建的聊天应用。

准备：
  pip install lark-oapi requests
  复制 .env.example 为 .env，填入飞书、DeepSeek 和 GITHUB_PAT。

运行：
  python3 feishu_chat.py
"""
import json
import os
import re
import sys
import threading

import util.feishu_bot as feishuBot
import worker_agent

ROOT = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(ROOT, ".env")
PROMPT_FILE = os.path.join(ROOT, "chat_system_prompt.txt")
SESSION_FILE = os.path.join(ROOT, "chat_sessions.json")

DEFAULT_APP_ID = "cli_aa5d99097fb89c13"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_PROMPT = "你是一个助手。请用中文直接回答用户。这段是占位提示词，稍后会换成正式的系统提示词。"
HISTORY_LIMIT = 20

_sessions = {}
_sessions_guard = threading.Lock()
_chat_locks = {}
_locks_guard = threading.Lock()
_seen = set()
_seen_guard = threading.Lock()


def load_env_file(path):
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            os.environ.setdefault(key, value)


def system_prompt():
    text = os.environ.get("DEEPSEEK_SYSTEM_PROMPT", "").strip()
    if text:
        return text, "环境变量"
    if os.path.exists(PROMPT_FILE):
        with open(PROMPT_FILE, encoding="utf-8") as f:
            text = f.read().strip()
        if text:
            return text, "chat_system_prompt.txt"
    return DEFAULT_PROMPT, "占位"


def extract_text(message_type, content):
    """取出用户文字。不是文字消息时返回 None。"""
    if message_type != "text":
        return None
    try:
        data = json.loads(content or "{}")
    except json.JSONDecodeError:
        return ""
    text = data.get("text") or ""
    text = re.sub(r"@_user_\d+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def remember(message_id):
    with _seen_guard:
        if message_id in _seen:
            return False
        _seen.add(message_id)
        if len(_seen) > 2000:
            _seen.clear()
            _seen.add(message_id)
        return True


def chat_lock(chat_id):
    with _locks_guard:
        lock = _chat_locks.get(chat_id)
        if lock is None:
            lock = threading.Lock()
            _chat_locks[chat_id] = lock
        return lock


def load_sessions():
    if not os.path.exists(SESSION_FILE):
        return {}
    try:
        with open(SESSION_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception as e:
        print(f"[聊天] 读取会话记录失败，将重新开始：{e}")
    return {}


def save_sessions(sessions):
    tmp = SESSION_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(sessions, f, ensure_ascii=False, indent=2)
    os.replace(tmp, SESSION_FILE)


def sender_open_id(sender):
    if sender is None:
        return ""
    sender_id = getattr(sender, "sender_id", None)
    if sender_id is None and isinstance(sender, dict):
        sender_id = sender.get("sender_id")
    if sender_id is None:
        return ""
    if isinstance(sender_id, dict):
        return sender_id.get("open_id") or ""
    return getattr(sender_id, "open_id", "") or ""


def answer(app_id, app_secret, message_id, chat_id, text, open_id):
    owner = os.environ.get("FEISHU_OWNER_OPEN_ID", "").strip()
    if owner and open_id != owner:
        feishuBot.reply_text(app_id, app_secret, message_id, "这个机器人只接受指定账号的指令。")
        return
    if text is None:
        feishuBot.reply_text(app_id, app_secret, message_id, "请发文字消息。图片和文件我暂时处理不了。")
        return
    if not text:
        feishuBot.reply_text(app_id, app_secret, message_id, "请直接输入要问的内容。在群里需要 @ 我。")
        return

    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        feishuBot.reply_text(
            app_id,
            app_secret,
            message_id,
            "还不能回答。请先在 .env 填写 DEEPSEEK_API_KEY，然后重启 feishu_chat.py。",
        )
        return

    base_prompt, source = system_prompt()
    prompt = worker_agent.compose_prompt(base_prompt, source)
    model = os.environ.get("DEEPSEEK_MODEL", "").strip() or DEFAULT_MODEL
    base_url = os.environ.get("DEEPSEEK_BASE_URL", "").strip() or DEFAULT_BASE_URL

    with chat_lock(chat_id):
        with _sessions_guard:
            history = list(_sessions.get(chat_id) or [])
            history.append({"role": "user", "content": text})
            history = history[-HISTORY_LIMIT:]
            _sessions[chat_id] = history
            save_sessions(_sessions)
            sent = list(history)
        def on_progress(notice):
            feishuBot.send_text(app_id, app_secret, notice, chat_id, "chat_id")

        try:
            reply = worker_agent.run_turn(
                api_key,
                base_url,
                model,
                prompt,
                sent,
                {"chat_id": chat_id, "on_progress": on_progress},
            )
        except worker_agent.AgentError as e:
            print(f"[聊天] 任务中途失败：{e}")
            reply = f"中途失败：{e}"
            if e.trace:
                reply += "\n已执行：\n" + "\n".join(e.trace)
        except Exception as e:
            print(f"[聊天] DeepSeek 调用失败：{e}")
            with _sessions_guard:
                current = list(_sessions.get(chat_id) or [])
                if current and current[-1] == {"role": "user", "content": text}:
                    current.pop()
                    _sessions[chat_id] = current
                    save_sessions(_sessions)
            feishuBot.reply_text(app_id, app_secret, message_id, f"这次没有生成回复：{e}")
            return

        if len(reply) > 8000:
            reply = reply[:8000] + "…"
        with _sessions_guard:
            history = list(_sessions.get(chat_id) or [])
            history.append({"role": "assistant", "content": reply})
            _sessions[chat_id] = history[-HISTORY_LIMIT:]
            save_sessions(_sessions)

    feishuBot.reply_text(app_id, app_secret, message_id, reply)


def on_message(data):
    try:
        event = data.event
        message = event.message
        sender = event.sender
        if sender is not None and getattr(sender, "sender_type", "") == "app":
            return
        message_id = getattr(message, "message_id", "") or ""
        if not message_id or not remember(message_id):
            return
        text = extract_text(getattr(message, "message_type", "") or "", getattr(message, "content", "") or "")
        chat_id = getattr(message, "chat_id", "") or message_id
        open_id = sender_open_id(sender)
        print(f"[聊天] 收到消息 {message_id}，发送者 {open_id or '未知'}")
        threading.Thread(
            target=answer,
            args=(
                os.environ["FEISHU_CHAT_APP_ID"],
                os.environ["FEISHU_CHAT_APP_SECRET"],
                message_id,
                chat_id,
                text,
                open_id,
            ),
            daemon=True,
        ).start()
    except Exception as e:
        print(f"[聊天] 处理事件失败：{e}")


def main():
    load_env_file(ENV_FILE)
    app_id = os.environ.get("FEISHU_CHAT_APP_ID", "").strip() or DEFAULT_APP_ID
    app_secret = os.environ.get("FEISHU_CHAT_APP_SECRET", "").strip()
    os.environ["FEISHU_CHAT_APP_ID"] = app_id
    if not app_secret:
        print("[聊天] 缺少 FEISHU_CHAT_APP_SECRET。请复制 .env.example 为 .env 并填入 App Secret。")
        sys.exit(1)
    os.environ["FEISHU_CHAT_APP_SECRET"] = app_secret

    global _sessions
    _sessions = load_sessions()
    base_prompt, source = system_prompt()
    prompt = worker_agent.compose_prompt(base_prompt, source)
    shown = "内置任务工人" if source == "占位" else source
    key_state = "已配置" if os.environ.get("DEEPSEEK_API_KEY", "").strip() else "未配置"
    print(f"[聊天] 应用：{app_id}")
    print(f"[聊天] DeepSeek 密钥：{key_state}")
    print(f"[聊天] 系统提示词：{shown}（{len(prompt)} 字）")
    print(worker_agent.github_api.setup_line())
    owner = os.environ.get("FEISHU_OWNER_OPEN_ID", "").strip()
    if owner:
        print(f"[聊天] 只接受发送者 {owner}")
    else:
        print("[聊天] 未限制发送者。群里其他人也能让机器人改仓库。请设置 FEISHU_OWNER_OPEN_ID。")
    print("[聊天] 正在连接飞书。连上之后，再到开发者后台把事件订阅保存为「使用长连接接收事件」。")

    import lark_oapi as lark

    handler = lark.EventDispatcherHandler.builder("", "").register_p2_im_message_receive_v1(on_message).build()
    client = lark.ws.Client(app_id, app_secret, event_handler=handler, log_level=lark.LogLevel.INFO)
    client.start()


if __name__ == "__main__":
    main()
