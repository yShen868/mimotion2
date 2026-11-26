# -*- coding: utf8 -*-
"""用个人令牌提交任务文件，并触发 GitHub Actions。"""
import os
import re
import subprocess
import time
import urllib.parse

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = "https://api.github.com"
BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]+$")


class GitHubError(RuntimeError):
    def __init__(self, message, retryable=False):
        super().__init__(message)
        self.retryable = retryable


def token_from_env():
    for name in ("GITHUB_PAT", "PAT", "GH_TOKEN"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def parse_repo(url):
    text = str(url or "").strip().rstrip("/")
    if text.endswith(".git"):
        text = text[:-4]
    if text.startswith("git@github.com:"):
        slug = text.split(":", 1)[1]
    elif "github.com/" in text:
        slug = text.split("github.com/", 1)[1]
    else:
        return "", ""
    parts = [part for part in slug.split("/") if part]
    if len(parts) < 2:
        return "", ""
    return parts[0], parts[1]


def git_origin():
    try:
        output = subprocess.check_output(
            ["git", "remote", "get-url", "origin"],
            cwd=ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return ""
    return output.strip()


def resolve_repo():
    slug = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if "/" in slug:
        owner, repo = slug.split("/", 1)
        return owner.strip(), repo.strip().replace(".git", "")
    return parse_repo(git_origin())


def setup_line():
    token = token_from_env()
    owner, repo = resolve_repo()
    branch = os.environ.get("GITHUB_BRANCH", "").strip() or "仓库默认分支"
    if not token:
        return "[任务] GitHub 令牌未配置。可以聊天，但不能提交和触发 Actions。请在 .env 设置 GITHUB_PAT。"
    if not owner or not repo:
        return "[任务] GitHub 令牌已配置，但没认出仓库。请设置 GITHUB_REPOSITORY=owner/name。"
    return f"[任务] 可以提交任务到 {owner}/{repo}，分支：{branch}。"


class GitHub:
    def __init__(self, token, owner, repo, branch=None):
        self.token = token
        self.owner = owner
        self.repo = repo
        self.branch = branch

    def raw(self, method, path, body=None):
        try:
            return requests.request(
                method,
                API + path,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": "mimotion-worker",
                },
                json=body,
                timeout=30,
            )
        except requests.RequestException as e:
            raise GitHubError(f"连接 GitHub 失败：{e}")

    def _message(self, response):
        try:
            data = response.json()
            message = data.get("message") or response.text
        except Exception:
            message = response.text
        message = str(message)[:500]
        if response.status_code in (401, 403):
            return (
                f"GitHub 拒绝了令牌（{response.status_code}）：{message}。"
                "确认 GITHUB_PAT 对这个仓库有 Contents、Workflows、Actions 读写。"
            )
        if response.status_code == 404:
            return f"GitHub 上找不到目标（404）：{message}"
        return f"GitHub 请求失败（{response.status_code}）：{message}"

    def request(self, method, path, body=None, ok=(200,)):
        response = self.raw(method, path, body)
        if response.status_code not in ok:
            raise GitHubError(self._message(response))
        if response.status_code == 204 or not response.content:
            return {}
        return response.json()

    def resolve_branch(self):
        if self.branch:
            branch = self.branch
        else:
            data = self.request("GET", f"/repos/{self.owner}/{self.repo}")
            branch = data.get("default_branch") or ""
            self.branch = branch
        if not branch or not BRANCH_RE.fullmatch(branch) or ".." in branch:
            raise GitHubError("分支名不合法。请在 .env 设置 GITHUB_BRANCH。")
        return branch

    def remote_exists(self, path, branch):
        quoted = urllib.parse.quote(path, safe="/")
        response = self.raw("GET", f"/repos/{self.owner}/{self.repo}/contents/{quoted}?ref={urllib.parse.quote(branch)}")
        return response.status_code == 200

    def _commit_once(self, changes, message):
        if not changes:
            raise GitHubError("没有需要提交的文件")
        branch = self.resolve_branch()
        ref_data = self.request("GET", f"/repos/{self.owner}/{self.repo}/git/ref/heads/{branch}")
        head = ref_data["object"]["sha"]
        current = self.request("GET", f"/repos/{self.owner}/{self.repo}/git/commits/{head}")
        tree = []
        for change in changes:
            if change["content"] is None:
                tree.append({"path": change["path"], "mode": "100644", "type": "blob", "sha": None})
                continue
            blob = self.request(
                "POST",
                f"/repos/{self.owner}/{self.repo}/git/blobs",
                {"content": change["content"], "encoding": "utf-8"},
                ok=(200, 201),
            )
            tree.append({"path": change["path"], "mode": "100644", "type": "blob", "sha": blob["sha"]})
        new_tree = self.request(
            "POST",
            f"/repos/{self.owner}/{self.repo}/git/trees",
            {"base_tree": current["tree"]["sha"], "tree": tree},
            ok=(200, 201),
        )
        new_commit = self.request(
            "POST",
            f"/repos/{self.owner}/{self.repo}/git/commits",
            {"message": message, "tree": new_tree["sha"], "parents": [head]},
            ok=(200, 201),
        )
        ref_response = self.raw(
            "PATCH",
            f"/repos/{self.owner}/{self.repo}/git/refs/heads/{branch}",
            {"sha": new_commit["sha"]},
        )
        if ref_response.status_code not in (200, 201):
            text = self._message(ref_response)
            retryable = "fast forward" in text.lower()
            raise GitHubError(text, retryable=retryable)
        return new_commit["sha"], branch

    def commit(self, changes, message, retries=2):
        try:
            return self._commit_once(changes, message)
        except GitHubError as e:
            if e.retryable and retries > 0:
                time.sleep(1)
                return self.commit(changes, message, retries - 1)
            if e.retryable:
                raise GitHubError("远程分支刚被别的提交更新，重试后仍失败。请再说一次。")
            raise

    def dispatch(self, workflow_file, branch):
        quoted = urllib.parse.quote(workflow_file)
        path = f"/repos/{self.owner}/{self.repo}/actions/workflows/{quoted}/dispatches"
        last = "没能触发工作流"
        for attempt in range(5):
            response = self.raw("POST", path, {"ref": branch})
            if response.status_code == 204:
                return
            last = self._message(response)
            if response.status_code in (404, 422) and attempt < 4:
                time.sleep(3)
                continue
            raise GitHubError(last)
        raise GitHubError(last)

    def runs(self, workflow_file):
        quoted = urllib.parse.quote(workflow_file)
        data = self.request("GET", f"/repos/{self.owner}/{self.repo}/actions/workflows/{quoted}/runs?per_page=5")
        found = []
        for run in data.get("workflow_runs") or []:
            found.append(
                {
                    "status": run.get("status"),
                    "conclusion": run.get("conclusion"),
                    "event": run.get("event"),
                    "url": run.get("html_url"),
                    "created_at": run.get("created_at"),
                }
            )
        return found

    def actions_url(self, workflow_file):
        return f"https://github.com/{self.owner}/{self.repo}/actions/workflows/{workflow_file}"


def load_client():
    token = token_from_env()
    if not token:
        raise GitHubError("没有 GitHub 令牌。在 .env 设置 GITHUB_PAT，权限要包含这个仓库的 Contents、Workflows、Actions 读写。")
    owner, repo = resolve_repo()
    if not owner or not repo:
        raise GitHubError("没有认出 GitHub 仓库。在 .env 设置 GITHUB_REPOSITORY=owner/name。")
    branch = os.environ.get("GITHUB_BRANCH", "").strip() or None
    return GitHub(token, owner, repo, branch)
