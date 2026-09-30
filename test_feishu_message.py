# -*- coding: utf8 -*-
"""手动检查 GitHub 密钥，并发送一条只含提示和时间的飞书测试消息。不修改步数。"""
import json
import os
import sys

import pytz
from datetime import datetime

import util.feishu_bot as feishuBot


def beijing_now():
    return datetime.now().astimezone(pytz.timezone("Asia/Shanghai"))


def secret_state(name):
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return "未配置"
    return "已配置"


def main():
    now = beijing_now().strftime("%Y-%m-%d %H:%M:%S")
    names = [
        "CONFIG",
        "AES_KEY",
        "FEISHU_APP_ID",
        "FEISHU_APP_SECRET",
        "FEISHU_RECEIVE_ID",
        "FEISHU_RECEIVE_ID_TYPE",
    ]
    print(f"北京时间：{now}")
    missing = []
    for name in names:
        state = secret_state(name)
        print(f"{name}：{state}")
        if state == "未配置" and name != "FEISHU_RECEIVE_ID_TYPE":
            missing.append(name)

    config_raw = os.environ.get("CONFIG") or ""
    if config_raw.strip():
        try:
            json.loads(config_raw)
            print("CONFIG 格式：JSON 正确")
        except Exception:
            print("CONFIG 格式：不是合法 JSON")
            missing.append("CONFIG")

    aes_key = os.environ.get("AES_KEY") or ""
    if aes_key and len(aes_key) != 16:
        print(f"AES_KEY 长度：{len(aes_key)}，需要 16 位")
        missing.append("AES_KEY")

    if missing:
        print("缺少密钥：" + "、".join(dict.fromkeys(missing)))

    lines = [f"时间：{now}"]
    for name in names:
        lines.append(f"{name}：{secret_state(name)}")
    if missing:
        lines.append("有密钥未配置，请到仓库 Secrets 里补全。")
    else:
        lines.append("密钥都已配置。这是一条手动测试消息，没有修改步数。")

    feishu_ready = secret_state("FEISHU_APP_ID") == "已配置" and secret_state("FEISHU_APP_SECRET") == "已配置" and secret_state("FEISHU_RECEIVE_ID") == "已配置"
    if not feishu_ready:
        print("飞书密钥不完整，无法发送测试消息")
        sys.exit(1)

    text = feishuBot.format_notice("测试", "密钥与消息检查", lines)
    ok = feishuBot.send_text(
        os.environ.get("FEISHU_APP_ID", ""),
        os.environ.get("FEISHU_APP_SECRET", ""),
        text,
        os.environ.get("FEISHU_RECEIVE_ID", ""),
        os.environ.get("FEISHU_RECEIVE_ID_TYPE") or "open_id",
    )
    if not ok or missing:
        sys.exit(1)
    print("测试消息已发送")


if __name__ == "__main__":
    main()
