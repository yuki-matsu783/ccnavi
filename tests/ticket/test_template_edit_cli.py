"""`ccnavi --templates` の保存・削除・改名・移動（`--save-draft` など）の受入テスト。

1. 下見（`--yes` なし）は何も書かない
2. `--yes --expect` が揃うと、下書きの中身を書き換える
3. 下書きを消す
4. 取り込み済みのテンプレートを消す
5. 組み合わせの誤り（`--move-template` に `--to` が無い）は止まる
6. ユーザ専用の形（`--delete-template`）はエージェントの Bash で止まる。`--version --json` の
   `features` に編集の振る舞いが入る
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from ccnavi.tickets import plan_templates
from tests.ticket.test_template_drafts import DENY, FLOW_IMPL, Drafts
from tests.ticket.test_ticket import write


class EditCli(Drafts):
    def setUp(self):
        super().setUp()
        self.done_parent(
            ["research", "design", "implement"],
            feedback=["implement-feedback"],
            flows={"i0001-03-01": FLOW_IMPL},
        )
        self.assertEqual(self.draft().returncode, 0)
        self.sha = self.generalize()
        self.work = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.work, ignore_errors=True)

    def body_file(self, text: str) -> str:
        path = os.path.join(self.work, "body.yml")
        write(path, text)
        return path

    def edited_body(self) -> str:
        return (
            self.read_draft()
            .decode("utf-8")
            .replace("機能を設計から実装まで進める", "別の説明に変える")
        )

    def denied(self, command) -> bool:
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "cwd": self.parent_tree,
            "session_id": "s1",
            "tool_input": {"command": command},
        }
        result = self.ccnavi(
            "--mode", "enable", "--guard-ticket-approval", "enable", stdin=json.dumps(payload)
        )
        return DENY in self.reason(result)

    # 1. 下見は何も書かない

    def test_save_without_yes_writes_nothing(self):
        body = self.body_file(self.edited_body())
        result = self.tpl("--templates", "--save-draft", "i0001", "--body-file", body)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("何も書いていない", result.stdout)
        self.assertEqual(plan_templates.digest(self.read_draft()), self.sha)

    # 2. 保存

    def test_save_with_yes_and_expect_rewrites_the_draft(self):
        new = self.edited_body()
        body = self.body_file(new)
        result = self.tpl(
            "--templates",
            "--save-draft",
            "i0001",
            "--body-file",
            body,
            "--yes",
            "--expect",
            self.sha,
            "--json",
            guard="disable",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(json.loads(result.stdout)["written"])
        self.assertEqual(self.read_draft().decode("utf-8"), new)

    # 3. 下書きを消す

    def test_delete_draft_removes_the_draft(self):
        result = self.tpl(
            "--templates",
            "--delete-draft",
            "i0001",
            "--yes",
            "--expect",
            self.sha,
            "--json",
            guard="disable",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(os.path.exists(self.draft_path()))

    # 4. 取り込み済みを消す

    def test_delete_template_removes_the_collected_common_template(self):
        imported = self.import_("i0001", "--to", "common", "--yes", "--expect", self.sha)
        self.assertEqual(imported.returncode, 0, imported.stdout + imported.stderr)
        self.assertTrue(os.path.exists(self.common_file()))
        shown = self.tpl("--templates", "--show", "i0001", "--json")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        sha = json.loads(shown.stdout)["template"]["sha"]
        result = self.tpl(
            "--templates",
            "--delete-template",
            "i0001",
            "--on",
            "common",
            "--yes",
            "--expect",
            sha,
            "--json",
            guard="disable",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(os.path.exists(self.common_file()))

    # 5. 組み合わせの誤り

    def test_move_without_to_is_refused(self):
        result = self.tpl("--templates", "--move-template", "i0001", "--on", "common")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--to", result.stderr)

    # 6. ユーザ専用の形と、features

    def test_the_agent_cannot_type_the_delete_template(self):
        for command in (
            "ccnavi --templates --delete-template i0001 --on common",
            "ccnavi --templates --delete-template i0001 --on common --yes --expect abc",
            "./.ccnavi/bin/ccnavi --templates --delete-template i0001 --on project --layer lib",
        ):
            self.assertTrue(self.denied(command), command)

    def test_version_lists_the_edit_feature(self):
        result = self.tpl("--version", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("templates.edit", json.loads(result.stdout)["features"])


if __name__ == "__main__":
    unittest.main()
