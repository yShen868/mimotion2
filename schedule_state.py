# -*- coding: utf-8 -*-
"""用 schedule_state.json 记录北京时间当天是否已经成功刷过步数。"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

STATE_FILE = "schedule_state.json"
BEIJING = timezone(timedelta(hours=8))


def now_beijing():
    return datetime.now(BEIJING)


def load_state():
    if not os.path.isfile(STATE_FILE):
        return {}
    with open(STATE_FILE, encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def write_skip(skip):
    output = os.environ.get("GITHUB_OUTPUT")
    line = f"skip={'true' if skip else 'false'}\n"
    if not output:
        print(line.strip())
        return
    with open(output, "a", encoding="utf-8") as f:
        f.write(line)


def check():
    event = os.environ.get("EVENT_NAME", "")
    today = now_beijing().strftime("%Y-%m-%d")
    state = load_state()
    recorded = state.get("date") or ""
    if event != "schedule":
        print(f"手动触发，直接执行。北京时间今天是 {today}，已有记录：{state.get('executed_at') or '无'}")
        write_skip(False)
        return
    if recorded == today:
        print(f"北京时间 {today} 已在 {state.get('executed_at')} 执行成功，跳过本次自动任务")
        write_skip(True)
        return
    print(f"北京时间 {today} 还没有成功记录，开始执行")
    write_skip(False)


def mark():
    now = now_beijing()
    state = {
        "date": now.strftime("%Y-%m-%d"),
        "executed_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "timezone": "Asia/Shanghai",
        "trigger": os.environ.get("EVENT_NAME", ""),
    }
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"已记录今天的执行：{state['executed_at']}")


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "check"
    if command == "check":
        check()
    elif command == "mark":
        mark()
    else:
        print(f"未知命令：{command}")
        sys.exit(1)
