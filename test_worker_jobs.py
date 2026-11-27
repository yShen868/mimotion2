# -*- coding: utf8 -*-
import base64
import tempfile
import unittest

import util.github_api as github_api
import util.job_store as job_store
import util.repo_files as repo_files
from jobs._runtime.notify import pick_target
from scripts.notify_code_push import build_notice


class JobStoreTest(unittest.TestCase):
    def test_cron_beijing_afternoon(self):
        self.assertEqual(job_store.validate_cron("0 8 * * *"), "0 8 * * *")
        self.assertEqual(job_store.validate_cron(""), "")
        with self.assertRaises(ValueError):
            job_store.validate_cron("* * * * *")
        with self.assertRaises(ValueError):
            job_store.validate_cron("*/2 * * * *")
        with self.assertRaises(ValueError):
            job_store.validate_cron("0 8 * * *; echo")

    def test_rejects_secret_injection(self):
        with self.assertRaises(ValueError):
            job_store.validate_secret_names(["PAT"])
        with self.assertRaises(ValueError):
            job_store.validate_secret_names(["CONFIG"])
        with self.assertRaises(ValueError):
            job_store.validate_secret_names(["AES_KEY"])
        with self.assertRaises(ValueError):
            job_store.validate_secret_names(["GITHUB_TOKEN"])
        self.assertEqual(job_store.validate_secret_names(["QWEATHER_KEY"]), ["QWEATHER_KEY"])

    def test_workflow_stays_inside_job(self):
        text = job_store.render_workflow(
            "gold-price",
            "0 8 * * *",
            ["QWEATHER_KEY"],
            "每天下午四点（北京时间）",
        )
        self.assertIn('cron: "0 8 * * *"', text)
        self.assertIn("secrets.QWEATHER_KEY", text)
        self.assertIn("secrets.FEISHU_APP_SECRET", text)
        self.assertIn("secrets.FEISHU_RECEIVE_ID", text)
        self.assertNotIn("FEISHU_CHAT_APP_SECRET", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn("contents: read", text)
        self.assertNotIn("secrets.PAT", text)
        self.assertNotIn("secrets.CONFIG", text)
        self.assertNotIn("secrets.AES_KEY", text)
        self.assertNotIn("secrets.GITHUB_TOKEN", text)
        self.assertIn("python3 jobs/gold-price/main.py", text)
        with self.assertRaises(ValueError):
            job_store.workflow_filename("../run")

    def test_script_must_notify(self):
        with self.assertRaises(ValueError):
            job_store.validate_script("print('hi')\n")
        with self.assertRaises(ValueError):
            job_store.validate_script("notify('../etc/passwd')\n")
        script = job_store.validate_script("from jobs._runtime.notify import notify\nnotify('ok')")
        self.assertTrue(script.endswith("\n"))

    def test_roundtrip(self):
        files = job_store.build_job_files(
            "gold-price",
            "金价",
            "0 8 * * *",
            "每天下午四点",
            "from jobs._runtime.notify import notify\nnotify('ok')\n",
            "",
            [],
            "oc_abc",
            "chat_id",
        )
        with tempfile.TemporaryDirectory() as root:
            job_store.write_files(root, files)
            found = job_store.list_jobs(root)
            self.assertEqual(found[0]["id"], "gold-price")
            self.assertEqual(found[0]["cron"], "0 8 * * *")
            loaded = job_store.read_job(root, "gold-price")
            self.assertIn("notify", loaded["script"])
            self.assertNotIn("_runtime", [item["id"] for item in found])
            removed = job_store.remove_job_files(root, "gold-price")
            self.assertIn(".github/workflows/job-gold-price.yml", removed)
            self.assertEqual(job_store.list_jobs(root), [])

    def test_repo_parse(self):
        self.assertEqual(github_api.parse_repo("git@github.com:yShen868/mimotion2.git"), ("yShen868", "mimotion2"))
        self.assertEqual(github_api.parse_repo("https://github.com/yShen868/mimotion2"), ("yShen868", "mimotion2"))

    def test_notify_target_prefers_job_chat_when_secret_empty(self):
        app_id, _secret, receive_id, receive_type = pick_target(
            {
                "FEISHU_CHAT_APP_ID": "cli_chat",
                "FEISHU_CHAT_APP_SECRET": "secret",
                "FEISHU_CHAT_RECEIVE_ID": "",
                "FEISHU_CHAT_RECEIVE_ID_TYPE": "open_id",
                "JOB_RECEIVE_ID": "oc_chat",
                "JOB_RECEIVE_ID_TYPE": "chat_id",
            }
        )
        self.assertEqual(app_id, "cli_chat")
        self.assertEqual(receive_id, "oc_chat")
        self.assertEqual(receive_type, "chat_id")

    def test_notify_reuses_step_bot(self):
        app_id, secret, receive_id, receive_type = pick_target(
            {
                "FEISHU_APP_SECRET": "step-secret",
                "JOB_RECEIVE_ID": "oc_from_chat_bot",
                "JOB_RECEIVE_ID_TYPE": "chat_id",
            }
        )
        self.assertEqual(app_id, "cli_aa379716a0f8dcd0")
        self.assertEqual(secret, "step-secret")
        self.assertEqual(receive_id, "ou_919a73c3ec0b7b52ac2cecf1276ee7ab")
        self.assertEqual(receive_type, "open_id")


class RepoFilesTest(unittest.TestCase):
    def test_rejects_secret_and_parent_paths(self):
        with self.assertRaises(ValueError):
            repo_files.clean_path(".env")
        with self.assertRaises(ValueError):
            repo_files.clean_path("token_store.json")
        with self.assertRaises(ValueError):
            repo_files.clean_path("../main.py")
        with self.assertRaises(ValueError):
            repo_files.clean_path("/etc/passwd")
        self.assertEqual(repo_files.clean_path("util/feishu_bot.py"), "util/feishu_bot.py")

    def test_replace_must_match_once(self):
        self.assertEqual(repo_files.replace_once("ab", "a", "c"), "cb")
        with self.assertRaises(ValueError):
            repo_files.replace_once("aba", "a", "c")
        with self.assertRaises(ValueError):
            repo_files.replace_once("ab", "z", "c")

    def test_search_replace_uses_remote_text(self):
        encoded = base64.b64encode(b"print('hi')\n").decode()

        class Fake:
            def resolve_branch(self):
                return "master"

            def contents(self, path, branch):
                self.path = path
                return 200, {"type": "file", "encoding": "base64", "content": encoded}

        dirty = {}
        path = repo_files.remember_replace(Fake(), dirty, "main.py", "print('hi')", "print('ok')")
        self.assertEqual(path, "main.py")
        self.assertEqual(dirty["main.py"], "print('ok')\n")

    def test_push_notice_lists_files(self):
        text = build_notice(
            {
                "head_commit": {"id": "abcdef1234567890", "message": "增加金价任务\n\n详情"},
                "commits": [{"added": ["jobs/gold-price/main.py"], "modified": ["worker_agent.py"], "removed": []}],
            },
            "success",
            "yShen868/mimotion2",
        )
        self.assertIn("检查通过", text)
        self.assertIn("新增 jobs/gold-price/main.py", text)
        self.assertIn("修改 worker_agent.py", text)
        self.assertIn("刷步定时没有因此触发", text)
        self.assertIn("https://github.com/yShen868/mimotion2/commit/abcdef1234567890", text)


if __name__ == "__main__":
    unittest.main()
