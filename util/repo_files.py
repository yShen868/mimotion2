# -*- coding: utf8 -*-
"""读写 GitHub 上的仓库文件。密钥和运行记录不给模型碰。"""
import base64

READ_LIMIT = 12000
FILE_LIMIT = 200000

DENY_NAMES = {
    ".env",
    "token_store.json",
    "schedule_state.json",
    "chat_sessions.json",
    "encrypted_tokens.data",
}


def clean_path(relative):
    text = str(relative or "").strip().replace("\\", "/")
    if text in ("", "."):
        return ""
    if text.startswith("/") or ":" in text:
        raise ValueError("只能使用仓库里的相对路径")
    parts = []
    for part in text.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise ValueError("不能访问上级目录")
        parts.append(part)
    if not parts:
        return ""
    name = parts[-1]
    if name in DENY_NAMES or name.startswith(".env") or ".git" in parts:
        raise ValueError(f"不能阅读或修改 {name}")
    return "/".join(parts)


def replace_once(text, old, new):
    if not isinstance(old, str) or old == "":
        raise ValueError("要替换的原文是空的")
    count = text.count(old)
    if count == 0:
        raise ValueError("原文没有出现。请按 read_file 看到的内容原样提供要替换的片段")
    if count > 1:
        raise ValueError(f"原文出现了 {count} 次。请多带几行上下文，让它只匹配一处")
    return text.replace(old, new, 1)


def _decode_file(data, path):
    if not isinstance(data, dict) or data.get("type") != "file":
        raise ValueError(f"{path} 不是文件")
    if data.get("encoding") != "base64" or not data.get("content"):
        raise ValueError("文件太大或不是文本")
    raw = base64.b64decode(data["content"])
    if b"\x00" in raw[:8000]:
        raise ValueError("这是二进制文件")
    if len(raw) > FILE_LIMIT:
        raise ValueError("文件超过 20 万字节")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("文件不是 UTF-8 文本")


def fetch_text(client, path):
    branch = client.resolve_branch()
    status, data = client.contents(path, branch)
    if status == 404:
        raise ValueError(f"GitHub 上没有 {path}")
    return _decode_file(data, path)


def list_dir(client, path, dirty):
    path = clean_path(path)
    branch = client.resolve_branch()
    status, data = client.contents(path, branch)
    entries = []
    if status == 404:
        data = []
    elif isinstance(data, dict):
        raise ValueError(f"{path} 是文件，不是目录")
    else:
        for item in data:
            name = item.get("name") or ""
            if name.startswith(".env") or name in DENY_NAMES:
                continue
            entries.append(
                {
                    "path": item.get("path") or name,
                    "type": item.get("type") or "file",
                    "size": item.get("size") or 0,
                }
            )
    prefix = path + "/" if path else ""
    seen = {item["path"] for item in entries}
    for dirty_path, content in dirty.items():
        if not dirty_path.startswith(prefix):
            continue
        rest = dirty_path[len(prefix):]
        if "/" in rest:
            child = prefix + rest.split("/", 1)[0]
            if child not in seen:
                entries.append({"path": child, "type": "dir", "size": 0, "pending": True})
                seen.add(child)
            continue
        row = {"path": dirty_path, "type": "file", "size": 0 if content is None else len(content), "pending": True}
        replaced = False
        for index, item in enumerate(entries):
            if item["path"] == dirty_path:
                entries[index] = row
                replaced = True
                break
        if not replaced:
            entries.append(row)
    if status == 404 and not entries:
        raise ValueError("GitHub 上没有这个目录")
    entries.sort(key=lambda item: (item.get("type") != "dir", item["path"]))
    return entries[:200]


def read_file(client, path, dirty):
    path = clean_path(path)
    if path == "":
        raise ValueError("请指定文件路径")
    if path in dirty:
        content = dirty[path]
        if content is None:
            raise ValueError(f"{path} 已标记删除，还没有推送")
        source = "未推送的修改"
    else:
        content = fetch_text(client, path)
        source = "GitHub"
    truncated = len(content) > READ_LIMIT
    shown = content[:READ_LIMIT] if truncated else content
    result = {"path": path, "source": source, "truncated": truncated, "content": shown}
    if truncated:
        result["chars"] = len(content)
        result["note"] = "只返回了开头。search_replace 会在完整文件里查找原文。"
    return result


def remember_write(dirty, path, content):
    path = clean_path(path)
    if path == "":
        raise ValueError("请指定文件路径")
    if not isinstance(content, str):
        raise ValueError("文件内容必须是文本")
    if "\x00" in content:
        raise ValueError("不能写入二进制")
    if len(content) > FILE_LIMIT:
        raise ValueError("文件超过 20 万字节")
    dirty[path] = content
    return path


def remember_replace(client, dirty, path, old, new):
    path = clean_path(path)
    if path == "":
        raise ValueError("请指定文件路径")
    if not isinstance(new, str):
        raise ValueError("新内容必须是文本")
    if path in dirty and dirty[path] is not None:
        current = dirty[path]
    elif path in dirty:
        raise ValueError(f"{path} 已标记删除")
    else:
        current = fetch_text(client, path)
    updated = replace_once(current, old, new)
    if len(updated) > FILE_LIMIT:
        raise ValueError("替换后的文件超过 20 万字节")
    dirty[path] = updated
    return path


def remember_delete(client, dirty, path):
    path = clean_path(path)
    if path == "":
        raise ValueError("请指定文件路径")
    branch = client.resolve_branch()
    exists = client.remote_exists(path, branch)
    if path in dirty and dirty[path] is not None and not exists:
        del dirty[path]
        return path
    if not exists and path not in dirty:
        raise ValueError(f"GitHub 上没有 {path}")
    dirty[path] = None
    return path
