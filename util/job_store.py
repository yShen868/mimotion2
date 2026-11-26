# -*- coding: utf8 -*-
"""定时任务的本地文件。模型只提供脚本，workflow 由这里生成。"""
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

JOB_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,30}$")
RECEIVE_RE = re.compile(r"^[A-Za-z0-9_]{1,128}$")
CRON_FIELD_RE = re.compile(r"^[0-9*,/\-]+$")
SECRET_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,40}$")
REQUIREMENT_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.\-]*"
    r"(\[[A-Za-z0-9_,]+\])?"
    r"([<>=!~]=?[A-Za-z0-9.*]+)?$"
)

SECRET_DENY = {
    "PAT",
    "GITHUB_PAT",
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "CONFIG",
    "AES_KEY",
    "DEEPSEEK_API_KEY",
}

RUNTIME_FILES = (
    "jobs/__init__.py",
    "jobs/_runtime/__init__.py",
    "jobs/_runtime/notify.py",
)


def validate_receive_id(receive_id):
    receive_id = str(receive_id or "").strip()
    if not RECEIVE_RE.fullmatch(receive_id):
        raise ValueError("当前会话 id 不能写入任务")
    return receive_id


def validate_job_id(job_id):
    job_id = str(job_id or "").strip()
    if not JOB_ID_RE.fullmatch(job_id) or job_id.endswith("-"):
        raise ValueError("任务名要用小写字母开头，只能含小写字母、数字和连字符，例如 gold-price")
    return job_id


def minute_ok(field):
    if field == "*":
        return False
    for part in field.split(","):
        if not part:
            return False
        step = None
        base = part
        if "/" in part:
            base, step_text = part.split("/", 1)
            if not step_text.isdigit() or int(step_text) < 5:
                return False
            step = int(step_text)
        if base == "*":
            continue
        if "-" in base:
            if step is None:
                return False
            left, right = base.split("-", 1)
            if not left.isdigit() or not right.isdigit():
                return False
            continue
        if not base.isdigit() or int(base) > 59:
            return False
    return True


def validate_cron(cron):
    cron = str(cron or "").strip()
    if not cron:
        return ""
    parts = cron.split()
    if len(parts) != 5:
        raise ValueError("cron 必须是五段 UTC 时间，例如下午四点北京时间写成 0 8 * * *")
    for part in parts:
        if not CRON_FIELD_RE.fullmatch(part):
            raise ValueError("cron 含有非法字符")
    if not minute_ok(parts[0]):
        raise ValueError("分钟不能写成每分钟都跑。可以用 0、30 或 */15")
    return " ".join(parts)


def validate_requirements(text):
    lines = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if not REQUIREMENT_RE.fullmatch(line):
            raise ValueError(f"依赖写得不合法：{line}。每行一个包名，例如 beautifulsoup4")
        lines.append(line)
    if len(lines) > 20:
        raise ValueError("依赖最多 20 行")
    return "\n".join(lines)


def validate_script(script):
    if not isinstance(script, str) or not script.strip():
        raise ValueError("脚本是空的")
    if len(script) > 20000:
        raise ValueError("脚本超过 20000 字，请拆短")
    if "\x00" in script or "../" in script or "..\\" in script:
        raise ValueError("脚本不能包含上级目录路径")
    if "notify(" not in script:
        raise ValueError("脚本必须 from jobs._runtime.notify import notify，并用 notify(文字) 推送结果")
    if not script.endswith("\n"):
        script += "\n"
    return script


def validate_secret_names(value):
    if not value:
        return []
    if isinstance(value, str):
        value = [part.strip() for part in value.split(",") if part.strip()]
    if not isinstance(value, list):
        raise ValueError("secret_names 必须是密钥名数组，不能是密钥的值")
    if len(value) > 8:
        raise ValueError("额外密钥最多 8 个")
    names = []
    for item in value:
        name = str(item).strip()
        if (
            name in SECRET_DENY
            or name.startswith("GITHUB_")
            or name.startswith("FEISHU_")
        ):
            raise ValueError(f"不能把 {name} 交给任务。刷步的飞书密钥会自动带上，PAT、CONFIG、AES_KEY 不会进入任务")
        if not SECRET_RE.fullmatch(name):
            raise ValueError(f"密钥名 {name} 必须全是大写字母、数字和下划线")
        if name not in names:
            names.append(name)
    return names


def one_line(text, limit):
    text = str(text or "").replace("\r", " ").replace("\n", " ").strip()
    if len(text) > limit:
        text = text[:limit]
    return text


def job_paths(job_id):
    job_id = validate_job_id(job_id)
    return {
        "script": f"jobs/{job_id}/main.py",
        "requirements": f"jobs/{job_id}/requirements.txt",
        "meta": f"jobs/{job_id}/job.json",
        "workflow": f".github/workflows/job-{job_id}.yml",
    }


def workflow_filename(job_id):
    return f"job-{validate_job_id(job_id)}.yml"


def render_workflow(job_id, cron, secret_names, cron_note):
    job_id = validate_job_id(job_id)
    lines = [f"name: 任务 {job_id}", ""]
    note = one_line(cron_note, 80).replace("#", " ")
    if note:
        lines.append(f"# {note}")
        lines.append("")
    lines.extend(["on:", "  workflow_dispatch:"])
    if cron:
        lines.extend(["  schedule:", f'    - cron: "{cron}"'])
    lines.extend(
        [
            "",
            "permissions:",
            "  contents: read",
            "",
            "concurrency:",
            f"  group: job-{job_id}",
            "  cancel-in-progress: false",
            "",
            "jobs:",
            "  run:",
            "    runs-on: ubuntu-latest",
            "    timeout-minutes: 10",
            "    steps:",
            "      - name: Checkout code",
            "        uses: actions/checkout@v4",
            "        with:",
            "          persist-credentials: false",
            "",
            "      - name: 设置 Python",
            "        uses: actions/setup-python@v5",
            "        with:",
            '          python-version: "3.10"',
            "",
            "      - name: 安装依赖",
            "        run: |",
            "          pip3 install requests",
            f"          pip3 install -r jobs/{job_id}/requirements.txt",
            "",
            "      - name: 运行任务",
            "        env:",
            "          PYTHONPATH: ${{ github.workspace }}",
            "          FEISHU_APP_ID: ${{ secrets.FEISHU_APP_ID }}",
            "          FEISHU_APP_SECRET: ${{ secrets.FEISHU_APP_SECRET }}",
            "          FEISHU_RECEIVE_ID: ${{ secrets.FEISHU_RECEIVE_ID }}",
            "          FEISHU_RECEIVE_ID_TYPE: ${{ secrets.FEISHU_RECEIVE_ID_TYPE }}",
        ]
    )
    for name in secret_names:
        lines.append(f"          {name}: ${{{{ secrets.{name} }}}}")
    lines.extend([f"        run: python3 jobs/{job_id}/main.py", ""])
    return "\n".join(lines)


def build_job_files(job_id, title, cron, cron_note, script, requirements, secret_names, receive_id, receive_id_type):
    paths = job_paths(job_id)
    meta = {
        "id": job_id,
        "title": title,
        "cron": cron,
        "cron_note": cron_note,
        "secret_names": secret_names,
        "receive_id": receive_id,
        "receive_id_type": receive_id_type,
    }
    requirement_text = requirements + "\n" if requirements else "# 无额外依赖\n"
    return [
        {"path": paths["script"], "content": script},
        {"path": paths["requirements"], "content": requirement_text},
        {"path": paths["meta"], "content": json.dumps(meta, ensure_ascii=False, indent=2) + "\n"},
        {
            "path": paths["workflow"],
            "content": render_workflow(job_id, cron, secret_names, cron_note),
        },
    ]


def safe_join(root, relative):
    root_real = os.path.realpath(root)
    path = os.path.realpath(os.path.join(root_real, relative))
    if path != root_real and not path.startswith(root_real + os.sep):
        raise ValueError("路径越界")
    return path


def write_files(root, files):
    for item in files:
        path = safe_join(root, item["path"])
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(item["content"])


def list_jobs(root):
    jobs_dir = os.path.join(root, "jobs")
    found = []
    if not os.path.isdir(jobs_dir):
        return found
    for name in sorted(os.listdir(jobs_dir)):
        if name.startswith("_") or name.startswith("."):
            continue
        meta_path = os.path.join(jobs_dir, name, "job.json")
        if not os.path.isfile(meta_path):
            continue
        try:
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(meta, dict):
            continue
        found.append(
            {
                "id": meta.get("id") or name,
                "title": meta.get("title") or "",
                "cron": meta.get("cron") or "",
                "cron_note": meta.get("cron_note") or "",
            }
        )
    return found


def read_job(root, job_id):
    paths = job_paths(job_id)
    script_path = safe_join(root, paths["script"])
    meta_path = safe_join(root, paths["meta"])
    if not os.path.isfile(script_path) or not os.path.isfile(meta_path):
        raise ValueError(f"没有任务 {job_id}")
    with open(script_path, encoding="utf-8") as f:
        script = f.read()
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)
    truncated = False
    if len(script) > 8000:
        script = script[:8000]
        truncated = True
    return {"meta": meta, "script": script, "truncated": truncated}


def remove_job_files(root, job_id):
    removed = []
    folder = safe_join(root, f"jobs/{validate_job_id(job_id)}")
    for relative in job_paths(job_id).values():
        path = safe_join(root, relative)
        if os.path.isfile(path):
            os.remove(path)
            removed.append(relative)
    if os.path.isdir(folder) and not os.listdir(folder):
        os.rmdir(folder)
    return removed


def runtime_changes():
    changes = []
    for relative in RUNTIME_FILES:
        path = safe_join(ROOT, relative)
        if not os.path.isfile(path):
            raise ValueError(f"缺少运行时文件 {relative}")
        with open(path, encoding="utf-8") as f:
            changes.append({"path": relative, "content": f.read()})
    return changes
