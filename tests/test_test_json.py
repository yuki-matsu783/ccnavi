"""`--test --json` と `--test-samples`（試験の JSON）の受入テスト。

VS Code 拡張のルール設定画面が読む形を、`--test` と同じ判定で組んでいることを
確かめる。内部の関数は呼ばず、標準出力と終了コードだけを見る。見るのは 4 つ。

1. `--test --json` が判定・根拠・当たったルール・返る文面を 1 つの JSON で出す
2. 判定が対象を取り出せないツールは `known` が偽で出る
3. `--test-samples` が見本をタイプの期待と突き合わせ、食い違いを数える。
   文字で出すときは食い違いがあれば終了コード 1、JSON では常に 0
4. 拡張側のフィクスチャ（vscode-extension/ccnavi-board/test/fixtures/test.json と
   samples.json）と同じ形である

形を変えたら `CCNAVI_BOARD_FIXTURE=1` を付けてこのテストを走らせ、フィクスチャを書き直す。
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest

from tests.inproc import run_ccnavi

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, "vscode-extension", "ccnavi-board", "test", "fixtures")

RULES = {
    "version": 3,
    "deny": [
        {
            "id": "git-push",
            "match": "Bash",
            "glob": "*git push*",
            "message": "push は人が行う。ラッパに依頼する。",
        }
    ],
    "ask": [
        {
            "id": "secrets",
            "match": "Read",
            "glob": "*/secrets/*",
            "message": "秘密の置き場。読む前に確認する。",
        }
    ],
    "allow": [
        {"id": "inspection", "match": "Bash", "regex": r"^(ls|cat)\b", "message": "見るだけ"}
    ],
}

SAMPLES = """\
deny:
  - tool: Bash
    subject: "cd /repo && git push"
    why: push は人が行う
ask:
  - tool: Read
    subject: "/repo/secrets/token"
    why: 秘密の置き場
allow:
  - tool: Bash
    subject: "ls -la"
    why: 見るだけ
  - tool: Bash
    subject: "git push --force"
    why: 食い違いの見本。deny に当たるので allow の期待とずれる
  - tool: Bash
    subject: "# comment only"
    why: 何も走らないので判定に入らない
"""


def write(directory: str, name: str, text: str) -> str:
    path = os.path.join(directory, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def ccnavi(root: str, rules_path: str, *args: str) -> subprocess.CompletedProcess:
    """見るのはルールだけ。写しと控えは外し、記録も残さない。"""
    environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
    return run_ccnavi(
        [
            "--root",
            root,
            "--rules",
            rules_path,
            "--approved",
            "",
            "--state",
            "",
            "--log",
            "",
            *args,
        ],
        input="",
        cwd=ROOT,
        env=environment,
    )


def _portable(value, root: str):
    """フィクスチャに書くときは、走らせた場所を消す。区切りはどちらの向きでも。"""
    if isinstance(value, dict):
        return {k: _portable(v, root) for k, v in value.items()}
    if isinstance(value, list):
        return [_portable(v, root) for v in value]
    if isinstance(value, str):
        for form in (root.replace("\\", "/"), root.replace("/", "\\")):
            value = value.replace(form, "/ws")
        return value
    return value


def _same_keys(case: unittest.TestCase, fixture: dict, body: dict) -> None:
    case.assertEqual(sorted(fixture), sorted(body))


class TestJsonTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name.replace("\\", "/")
        self.rules_path = write(self.tmp.name, "rules.yml", json.dumps(RULES))
        self.samples_path = write(self.tmp.name, "samples.yml", SAMPLES)

    def tearDown(self):
        self.tmp.cleanup()

    def test_one_call_carries_verdict_rules_and_response(self):
        done = ccnavi(
            self.root, self.rules_path, "--test", "Bash", "cd /repo && git push", "--json"
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        body = json.loads(done.stdout)
        self.assertEqual(body["version"], 1)
        self.assertTrue(body["known"])
        self.assertEqual(body["verdict"], "deny")
        self.assertEqual(body["code"], "DENY_COMMAND_PATTERN")
        hit = body["rules"][0]
        self.assertEqual(
            (hit["id"], hit["section"], hit["kind"], hit["source"]),
            ("git-push", "deny", "glob", "file"),
        )
        self.assertEqual(hit["written"], "*git push*")
        self.assertNotEqual(hit["pattern"], "")
        self.assertIn("push は人が行う", body["response"])

        # 文字の出力と同じ判定であること。
        plain = ccnavi(self.root, self.rules_path, "--test", "Bash", "cd /repo && git push")
        self.assertTrue(
            plain.stdout.startswith("verdict: deny (DENY_COMMAND_PATTERN)\n"), plain.stdout
        )

        self._check_fixture("test.json", body)

    def test_unknown_tool_is_not_judged(self):
        done = ccnavi(self.root, self.rules_path, "--test", "WebFetch", "https://x", "--json")
        self.assertEqual(done.returncode, 0, done.stderr)
        body = json.loads(done.stdout)
        self.assertFalse(body["known"])
        self.assertEqual(body["verdict"], "")
        self.assertEqual(body["rules"], [])

    def test_samples_count_mismatches_and_skips(self):
        done = ccnavi(self.root, self.rules_path, "--test-samples", self.samples_path, "--json")
        self.assertEqual(done.returncode, 0, done.stderr)
        body = json.loads(done.stdout)
        self.assertEqual(body["version"], 1)
        self.assertEqual(
            body["counts"],
            {
                "deny": {"ok": 1, "total": 1},
                "ask": {"ok": 1, "total": 1},
                "allow": {"ok": 2, "total": 3},
            },
        )
        self.assertEqual(body["mismatches"], 1)
        self.assertEqual(body["skipped"], 1)
        by_subject = {s["subject"]: s for s in body["samples"]}
        wrong = by_subject["git push --force"]
        self.assertFalse(wrong["ok"])
        self.assertEqual((wrong["expected"], wrong["verdict"]), ("allow", "deny"))
        self.assertEqual(wrong["rules"][0]["id"], "git-push")
        skipped = by_subject["# comment only"]
        self.assertTrue(skipped["ok"])
        self.assertTrue(skipped["skipped"])
        # `/repo` は走らせた場所に読み替わる。
        self.assertEqual(
            by_subject["/repo/secrets/token"]["resolved_subject"], f"{self.root}/secrets/token"
        )

        # 文字で出すときは、食い違いがあれば終了コード 1。
        plain = ccnavi(self.root, self.rules_path, "--test-samples", self.samples_path)
        self.assertEqual(plain.returncode, 1, plain.stdout)
        self.assertIn("食い違い: allow のはずが deny", plain.stdout)
        self.assertIn("食い違い 1 件、判定に入らなかったもの 1 件", plain.stdout)

        self._check_fixture("samples.json", body)

    def test_unreadable_samples_fail_loudly(self):
        done = ccnavi(
            self.root, self.rules_path, "--test-samples", os.path.join(self.root, "missing.yml")
        )
        self.assertEqual(done.returncode, 1)
        self.assertIn("見本を読めない", done.stderr)

    def _check_fixture(self, name: str, body: dict) -> None:
        path = os.path.join(FIXTURES, name)
        if os.environ.get("CCNAVI_BOARD_FIXTURE"):
            write(
                FIXTURES,
                name,
                json.dumps(_portable(body, self.root), ensure_ascii=False, indent=1) + "\n",
            )
        with open(path, encoding="utf-8") as f:
            fixture = json.load(f)
        _same_keys(self, fixture, body)
        if "samples" in body:
            _same_keys(self, fixture["samples"][0], body["samples"][0])
            _same_keys(self, fixture["samples"][0]["rules"][0], body["samples"][0]["rules"][0])
        else:
            _same_keys(self, fixture["rules"][0], body["rules"][0])


if __name__ == "__main__":
    unittest.main()
