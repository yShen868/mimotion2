# -*- coding: utf8 -*-
"""飞书对话里的任务工人。模型通过工具写任务，GitHub Actions 负责到点运行。"""
import json

import requests

import util.github_api as github_api
import util.job_store as job_store
import util.repo_files as repo_files

ROOT = job_store.ROOT
MAX_ROUNDS = 16

WORKER_PROMPT = """你是这个仓库的开发工人。用户在飞书里用中文下指令。
阅读和修改的是 GitHub 上 master 的代码，不是让用户把文件贴过来。

改代码的闭环：
1. 先 list_dir、read_file。可以指定目录，例如 util 或 .github/workflows。
2. 已有文件用 search_replace，原文必须能在文件里唯一对上。新文件用 write_file。删除用 delete_file。
3. 改完必须 commit_push。不推送，GitHub 上就没有变化。
4. 推送后 Actions 会跑单测。通过或失败，刷步机器人会再发一条，内容是检查结果和变更文件。
5. 对话里说明改了什么和提交号，并告诉用户检查结果会另外发来。不要说部署已经完成。

范围：
- 可以改业务代码、定时任务和 workflow，也可以加功能。
- 不能读或写 .env、token_store.json、schedule_state.json、chat_sessions.json。
- 不要把「修改微信步数」改成每次 push 都执行。普通代码推送只做检查和通知，刷步仍按原来的定时跑。
- 不要索要或打印密钥。

做定时任务时：
1. 已有任务先 list_jobs 或 read_job，改的时候保留原来的抓取逻辑。
2. save_job 写完整 Python。commit 用 true，这样才会推到 GitHub，定时才会生效。
3. 第一次创建时 run_now 用 true，先跑一遍把结果推回来。只是改时间或改文案时 run_now 用 false，除非用户说现在跑一次。
4. 用中文回复任务名、北京时间、对应的 UTC cron、是否已提交、是否已触发。试跑可能要一两分钟才推到飞书。

时间：GitHub cron 是 UTC，五段，没有秒。北京时间 = UTC+8。
下午四点是 0 8 * * *。上午九点是 0 1 * * *。晚上十点是 0 14 * * *。
不要写每分钟都跑的 cron。只跑一次、不需要定时的任务，cron 留空。

脚本规则：
- 这是 jobs/<任务名>/main.py 的全文
- 必须包含 from jobs._runtime.notify import notify
- 要给用户看的内容交给 notify("...")。notify 返回 False 时 raise SystemExit(1)
- 失败也要用 notify 把原因推出去
- 不要读取或打印环境变量
- requests 已经安装。别的包放进 requirements，每行一个包名
- 需要密钥时，不要问用户要值。让用户在 GitHub 仓库 Secrets 新建一个大写名字，再把这个名字放进 secret_names

金价可以照下面写。第 1 个字段是最新价，下标 6 是时间，下标 12 是日期。接口变化时把错误推给用户。

import requests
from jobs._runtime.notify import notify

def main():
    try:
        response = requests.get(
            "https://hq.sinajs.cn/list=hf_GC",
            headers={"Referer": "https://finance.sina.com.cn"},
            timeout=20,
        )
        response.encoding = "gbk"
        raw = response.text.split('"', 2)[1]
        fields = raw.split(",")
        price = fields[0]
        when = fields[12] + " " + fields[6]
        text = "【金价】纽约黄金 " + price + " 美元/盎司\\n行情时间 " + when
    except Exception as e:
        text = "【金价】获取失败：" + str(e)
    if not notify(text):
        raise SystemExit(1)

if __name__ == "__main__":
    main()

定时结果用刷步同一个飞书机器人，发给已经在收刷步通知的那个人。不要让用户再配飞书应用密钥。
用户只是聊天、没有要求改代码或建任务时，不要改文件，也不要调用 save_job。
"""

TOOL_APPENDIX = """
你可以阅读并修改 GitHub 上 master 的项目文件。流程是 list_dir / read_file，再用 search_replace 或 write_file，最后 commit_push。
推送后 Actions 会检查，完成或失败由刷步机器人另发变更。不要说部署已经完成。
不能读或写 .env、token_store.json、schedule_state.json。不要让刷步 workflow 在每次 push 时执行。
定时任务仍用 save_job。cron 用 UTC 五段，北京时间下午四点是 0 8 * * *。
用户没要求改代码或建任务时，不要改文件。
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "列出 GitHub 仓库某个目录。path 为空表示仓库根目录。",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "相对路径，例如 util 或 .github/workflows"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "阅读 GitHub 上的一个文本文件。改之前先读。",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_replace",
            "description": "替换已有文件里唯一的一段原文。适合修改功能。原文必须和文件里完全一致。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old": {"type": "string", "description": "要被替换的原文"},
                    "new": {"type": "string", "description": "替换后的文本"},
                },
                "required": ["path", "old", "new"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "新建文件，或覆盖整个文件。改已有文件时优先用 search_replace。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": "删除 GitHub 上的一个文件。推送前只是标记。",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "commit_push",
            "description": "把本轮修改推到 master。推送后 Actions 会检查，并用刷步机器人另发结果。",
            "parameters": {
                "type": "object",
                "properties": {"message": {"type": "string", "description": "提交说明，写清为什么改"}},
                "required": ["message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_jobs",
            "description": "列出本机已有的定时任务。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_job",
            "description": "读取一个任务的脚本和定时配置，修改前先读。",
            "parameters": {
                "type": "object",
                "properties": {"job_id": {"type": "string", "description": "例如 gold-price"}},
                "required": ["job_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_job",
            "description": "创建或覆盖一个任务。程序会自己生成 workflow。commit 为 true 时推送到 GitHub。run_now 为 true 时立刻跑一次。接收人由系统填当前会话。",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "description": "小写字母开头，只能含小写字母、数字、连字符"},
                    "title": {"type": "string"},
                    "cron": {"type": "string", "description": "五段 UTC cron。只手动运行就传空字符串"},
                    "cron_note": {"type": "string", "description": "给人看的时间，例如每天下午四点（北京时间）"},
                    "script": {"type": "string", "description": "main.py 全文，必须调用 notify"},
                    "requirements": {"type": "string", "description": "额外 pip 包，可空。不要写 requests"},
                    "secret_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "还需要注入的 GitHub Secret 名字，只能是大写名字，不能是值",
                    },
                    "commit": {"type": "boolean"},
                    "run_now": {"type": "boolean"},
                },
                "required": ["job_id", "title", "script"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_job",
            "description": "删除一个任务和它的 workflow。commit 为 true 时同步删除 GitHub 上的文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"},
                    "commit": {"type": "boolean"},
                },
                "required": ["job_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_job",
            "description": "立刻触发一个已经提交到 GitHub 的任务。",
            "parameters": {
                "type": "object",
                "properties": {"job_id": {"type": "string"}},
                "required": ["job_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "job_status",
            "description": "查看一个任务最近几次 GitHub Actions 运行结果。",
            "parameters": {
                "type": "object",
                "properties": {"job_id": {"type": "string"}},
                "required": ["job_id"],
            },
        },
    },
]


class AgentError(RuntimeError):
    def __init__(self, message, trace):
        super().__init__(message)
        self.trace = trace


def compose_prompt(base_text, source):
    if source == "占位":
        return WORKER_PROMPT
    extra = base_text.strip() if base_text else ""
    if not extra:
        return WORKER_PROMPT
    return extra + "\n" + TOOL_APPENDIX


def as_bool(value, default):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "y", "是")
    return bool(value)


def _progress(context, text):
    callback = context.get("on_progress")
    if not callback or context.get("progressed"):
        return
    context["progressed"] = True
    try:
        callback(text)
    except Exception as e:
        print(f"[任务] 进度消息失败：{e}")


def _dump(data):
    text = json.dumps(data, ensure_ascii=False)
    if len(text) > 7000:
        text = text[:7000] + "…"
    return text


def _dirty(context):
    store = context.get("dirty")
    if store is None:
        store = {}
        context["dirty"] = store
    return store


def _client(context):
    client = context.get("github")
    if client is None:
        client = github_api.load_client()
        context["github"] = client
    return client


def list_dir(args, context):
    entries = repo_files.list_dir(_client(context), args.get("path"), _dirty(context))
    return {"ok": True, "entries": entries}


def read_file(args, context):
    data = repo_files.read_file(_client(context), args.get("path"), _dirty(context))
    data["ok"] = True
    return data


def search_replace(args, context):
    path = repo_files.remember_replace(
        _client(context),
        _dirty(context),
        args.get("path"),
        args.get("old"),
        args.get("new"),
    )
    return {"ok": True, "path": path, "pending": True}


def write_file(args, context):
    path = repo_files.remember_write(_dirty(context), args.get("path"), args.get("content"))
    return {"ok": True, "path": path, "pending": True}


def delete_file(args, context):
    path = repo_files.remember_delete(_client(context), _dirty(context), args.get("path"))
    return {"ok": True, "path": path, "pending": True}


def commit_push(args, context):
    message = str(args.get("message") or "").strip()
    if not message:
        raise ValueError("提交说明是空的")
    if len(message) > 500:
        message = message[:500]
    dirty = _dirty(context)
    if not dirty:
        raise ValueError("没有未推送的修改。先改文件，再推送。")
    changes = []
    for path, content in list(dirty.items()):
        repo_files.clean_path(path)
        changes.append({"path": path, "content": content})
    _progress(context, "正在把改动推到 GitHub。检查完成后，刷步机器人会再发一条结果。")
    client = _client(context)
    sha, branch = client.commit(changes, message)
    dirty.clear()
    return {
        "ok": True,
        "sha": sha[:7],
        "branch": branch,
        "files": [item["path"] for item in changes],
        "url": f"https://github.com/{client.owner}/{client.repo}/commit/{sha}",
        "next": "代码已推到 master。Actions 检查结束后，刷步机器人会另发结果和变更列表。刷步定时不会因此立刻执行。",
    }


def list_jobs(_args, _context):
    return {"ok": True, "jobs": job_store.list_jobs(ROOT)}


def read_job(args, _context):
    job_id = job_store.validate_job_id(args.get("job_id"))
    data = job_store.read_job(ROOT, job_id)
    data["ok"] = True
    return data


def save_job(args, context):
    job_id = job_store.validate_job_id(args.get("job_id"))
    title = job_store.one_line(args.get("title"), 40)
    if not title:
        raise ValueError("标题是空的")
    cron = job_store.validate_cron(args.get("cron"))
    cron_note = job_store.one_line(args.get("cron_note"), 80)
    script = job_store.validate_script(args.get("script"))
    requirements = job_store.validate_requirements(args.get("requirements"))
    secret_names = job_store.validate_secret_names(args.get("secret_names"))
    receive_id = job_store.validate_receive_id(context.get("chat_id"))
    files = job_store.build_job_files(
        job_id,
        title,
        cron,
        cron_note,
        script,
        requirements,
        secret_names,
        receive_id,
        "chat_id",
    )
    files.extend(job_store.runtime_changes())
    job_store.write_files(ROOT, files)
    commit = as_bool(args.get("commit"), True)
    run_now = as_bool(args.get("run_now"), False)
    result = {
        "ok": True,
        "job_id": job_id,
        "cron": cron,
        "cron_note": cron_note,
        "committed": False,
        "local_written": True,
    }
    if "os.environ" in script or "getenv" in script:
        result["warning"] = "脚本读取了环境变量。不要把密钥写进推送内容。"
    if not commit:
        result["message"] = "只写在本机，GitHub Actions 还不会跑。需要生效时用 commit=true 再保存一次。"
        return result
    _progress(context, "正在把任务提交到 GitHub，并准备运行。")
    try:
        client = github_api.load_client()
        sha, branch = client.commit(files, f"chore: 更新任务 {job_id}")
    except github_api.GitHubError as e:
        result["ok"] = False
        result["error"] = str(e)
        result["message"] = "文件已经写在本机，但没有推到 GitHub，定时不会生效。"
        return result
    result["committed"] = True
    result["sha"] = sha[:7]
    result["branch"] = branch
    result["actions_url"] = client.actions_url(job_store.workflow_filename(job_id))
    note = _schedule_note(client, branch)
    if note:
        result["schedule_note"] = note
    if not run_now:
        return result
    try:
        client.dispatch(job_store.workflow_filename(job_id), branch)
        result["triggered"] = True
    except github_api.GitHubError as e:
        result["triggered"] = False
        result["trigger_error"] = str(e)
    return result


def _schedule_note(client, branch):
    try:
        data = client.request("GET", f"/repos/{client.owner}/{client.repo}")
    except github_api.GitHubError:
        return ""
    default_branch = data.get("default_branch") or ""
    if default_branch and branch != default_branch:
        return "定时只在仓库默认分支生效。这次提交不在默认分支，到点不会自动跑。"
    return ""


def delete_job(args, context):
    job_id = job_store.validate_job_id(args.get("job_id"))
    commit = as_bool(args.get("commit"), True)
    if commit:
        _progress(context, "正在从 GitHub 删除这个任务。")
        client = github_api.load_client()
        branch = client.resolve_branch()
        changes = []
        for relative in job_store.job_paths(job_id).values():
            if client.remote_exists(relative, branch):
                changes.append({"path": relative, "content": None})
        if not changes:
            raise github_api.GitHubError(f"远程没有任务 {job_id}")
        sha, branch = client.commit(changes, f"chore: 删除任务 {job_id}")
        job_store.remove_job_files(ROOT, job_id)
        return {"ok": True, "job_id": job_id, "committed": True, "sha": sha[:7], "branch": branch}
    removed = job_store.remove_job_files(ROOT, job_id)
    if not removed:
        raise ValueError(f"本机没有任务 {job_id}")
    return {"ok": True, "job_id": job_id, "committed": False, "removed": removed}


def run_job(args, context):
    job_id = job_store.validate_job_id(args.get("job_id"))
    _progress(context, "正在触发 GitHub Actions。")
    client = github_api.load_client()
    branch = client.resolve_branch()
    client.dispatch(job_store.workflow_filename(job_id), branch)
    return {
        "ok": True,
        "job_id": job_id,
        "branch": branch,
        "actions_url": client.actions_url(job_store.workflow_filename(job_id)),
    }


def job_status(args, _context):
    job_id = job_store.validate_job_id(args.get("job_id"))
    client = github_api.load_client()
    return {"ok": True, "job_id": job_id, "runs": client.runs(job_store.workflow_filename(job_id))}


HANDLERS = {
    "list_dir": list_dir,
    "read_file": read_file,
    "search_replace": search_replace,
    "write_file": write_file,
    "delete_file": delete_file,
    "commit_push": commit_push,
    "list_jobs": list_jobs,
    "read_job": read_job,
    "save_job": save_job,
    "delete_job": delete_job,
    "run_job": run_job,
    "job_status": job_status,
}


def execute(name, args, context):
    label = args.get("job_id") or args.get("path") or ""
    print(f"[任务] {name} {label}".rstrip())
    handler = HANDLERS.get(name)
    if handler is None:
        return {"ok": False, "error": f"没有工具 {name}"}
    if not isinstance(args, dict):
        return {"ok": False, "error": "参数必须是对象"}
    try:
        return handler(args, context)
    except (ValueError, github_api.GitHubError) as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        print(f"[任务] {name} 失败：{e}")
        return {"ok": False, "error": str(e)}


def trace_line(name, args, result):
    if not result.get("ok"):
        return None
    job_id = args.get("job_id") or result.get("job_id") or ""
    if name == "save_job":
        if not result.get("committed"):
            return f"任务 {job_id} 只写在本机，还没有推送"
        line = f"已提交任务 {job_id}（{result.get('sha')}）"
        if result.get("triggered"):
            line += "，并已触发一次"
        elif result.get("trigger_error"):
            line += "，触发失败：" + result["trigger_error"]
        return line
    if name == "delete_job" and result.get("committed"):
        return f"已删除任务 {job_id}"
    if name == "run_job":
        return f"已触发任务 {job_id}"
    if name == "commit_push":
        files = "、".join(result.get("files") or [])
        return f"已推送 {result.get('sha')}：{files}。检查结果稍后由刷步机器人发送。"
    return None


def complete(api_key, base_url, model, messages):
    response = requests.post(
        base_url.rstrip("/") + "/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": messages,
            "tools": TOOLS,
            "tool_choice": "auto",
        },
        timeout=120,
    )
    try:
        data = response.json()
    except Exception:
        raise RuntimeError(f"DeepSeek 返回的不是 JSON，HTTP {response.status_code}")
    if response.status_code != 200 or data.get("error"):
        err = data.get("error") or {}
        message = err.get("message") if isinstance(err, dict) else str(err)
        raise RuntimeError(message or f"HTTP {response.status_code}")
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("模型没有返回内容")
    message = choices[0].get("message") or {}
    if not isinstance(message, dict):
        raise RuntimeError("模型没有返回内容")
    return message


def _arguments(function):
    raw = function.get("arguments") if isinstance(function, dict) else None
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        raise ValueError("参数不是 JSON")
    if not isinstance(parsed, dict):
        raise ValueError("参数必须是对象")
    return parsed


def run_turn(api_key, base_url, model, prompt, history, context):
    messages = [{"role": "system", "content": prompt}]
    messages.extend(history)
    trace = []
    try:
        for _ in range(MAX_ROUNDS):
            message = complete(api_key, base_url, model, messages)
            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                content = (message.get("content") or "").strip()
                if content:
                    return content
                if trace:
                    return "\n".join(trace)
                return "这次没有生成回复。"
            messages.append(
                {
                    "role": "assistant",
                    "content": message.get("content") or "",
                    "tool_calls": tool_calls,
                }
            )
            for call in tool_calls:
                function = call.get("function") or {}
                name = function.get("name") or ""
                try:
                    args = _arguments(function)
                except ValueError as e:
                    args = {}
                    result = {"ok": False, "error": str(e)}
                else:
                    result = execute(name, args, context)
                line = trace_line(name, args, result)
                if line:
                    trace.append(line)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id") or "",
                        "content": _dump(result),
                    }
                )
    except RuntimeError as e:
        if trace:
            raise AgentError(str(e), trace)
        raise
    if trace:
        return "步骤太多，先停下来。已经做了这些：\n" + "\n".join(trace)
    return "步骤太多，先停下来。请把要改的任务再说具体一点。"
