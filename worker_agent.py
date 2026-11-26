# -*- coding: utf8 -*-
"""飞书对话里的任务工人。模型通过工具写任务，GitHub Actions 负责到点运行。"""
import json

import requests

import util.github_api as github_api
import util.job_store as job_store

ROOT = job_store.ROOT
MAX_ROUNDS = 8

WORKER_PROMPT = """你是这个仓库的任务工人。用户在飞书里用中文下指令。
普通问题直接回答。用户要一个会重复发生、或要你落地执行的结果时，调用工具写任务，不要只给方案。

你只能创建、修改、删除、立即运行 jobs/ 下的定时任务。
不能改刷步代码，不能改 run.yml，不能索要或打印任何密钥的值。

做任务时：
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
用户只是聊天、没有要求落地时，不要调用 save_job。一次只处理一个任务。
"""

TOOL_APPENDIX = """
你可以调用工具，把定时任务写进这个仓库并由 GitHub Actions 运行。
只能动 jobs/<任务名>/ 和对应的 job-*.yml，不能改刷步。
cron 用 UTC 五段。北京时间下午四点是 0 8 * * *。
脚本必须调用 notify(文字) 推送结果。commit 为 true 才会真正生效。
第一次创建任务时 run_now 为 true。用户没要求落地时不要保存任务。
"""

TOOLS = [
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
    "list_jobs": list_jobs,
    "read_job": read_job,
    "save_job": save_job,
    "delete_job": delete_job,
    "run_job": run_job,
    "job_status": job_status,
}


def execute(name, args, context):
    print(f"[任务] {name} {args.get('job_id', '')}".rstrip())
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
