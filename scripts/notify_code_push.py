# -*- coding: utf8 -*-
"""代码推送到 master 并通过检查后，用刷步机器人发变更。"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def build_notice(event, status, repo):
    head = event.get("head_commit") or {}
    raw_message = str(head.get("message") or "").strip()
    title = raw_message.splitlines()[0] if raw_message else "无说明"
    sha = str(head.get("id") or "")
    short = sha[:7]
    seen = []
    for commit in event.get("commits") or []:
        for kind, label in (("added", "新增"), ("modified", "修改"), ("removed", "删除")):
            for path in commit.get(kind) or []:
                line = f"{label} {path}"
                if line not in seen:
                    seen.append(line)
    if status == "success":
        result = "检查通过，代码已在 master"
    else:
        result = "检查失败，代码已在 master，但单测没过"
    lines = [f"【代码】{result}", f"提交：{short} {title}", "刷步定时没有因此触发。"]
    if seen:
        lines.append("变更：")
        lines.extend(seen[:40])
        if len(seen) > 40:
            lines.append(f"还有 {len(seen) - 40} 个文件")
    full = str(head.get("id") or "")
    if repo and full:
        lines.append(f"https://github.com/{repo}/commit/{full}")
    return "\n".join(lines)


def main():
    event_path = os.environ.get("GITHUB_EVENT_PATH", "")
    event = {}
    if event_path and os.path.isfile(event_path):
        with open(event_path, encoding="utf-8") as handle:
            event = json.load(handle)
    text = build_notice(
        event,
        os.environ.get("CHECK_RESULT", ""),
        os.environ.get("PUSH_REPO", ""),
    )
    from jobs._runtime.notify import notify

    if not notify(text):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
