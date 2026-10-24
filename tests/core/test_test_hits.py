"""`--test` が見せる根拠と payload が、hook の判定と同じものであることの受入テスト。

見るのは 3 つ。

1. ヒットしたルール（`rules`）は判定が適用したルールそのもので、ルールファイルを別に読み直さない。
   組み込み（設定ファイルの保護・チケットの状態の保護・退避した既定）は `outside` で、式は短く切る。
   レイヤーの和で重複として破棄される側のプロジェクトの定義も、同じ id のルールも位置で引ける
2. payload の欄（`--cwd`・`--permission-mode`・`--agent-id`）が判定に渡り、使った payload が出る。
   知らない権限モードと無いディレクトリは断る。切り替えの環境変数は hook と同じく解いてから
   判定に渡す
3. `--explain` の「どのルールも言及しない呼び出し」が、判定と同じ関数で権限モードごとの結末を出す
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest

from tests import ROOT, common_path
from tests.inproc import run_ccnavi

ALLOW_ALL = {"version": 1, "allow": [{"id": "all", "match": "Bash", "glob": "*"}]}
SCHEMA = {
    "version": 1,
    "deny": [
        {
            "id": "schema",
            "match": "Write|Edit",
            "glob": "*/schema/*",
            "message": "schema は手で書かない。マイグレーションを書く。",
        }
    ],
}


def git(cwd: str, *args: str) -> None:
    done = subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if done.returncode != 0:
        raise AssertionError(f"git {args}: {done.stderr}")


def write(path: str, text: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


class TestHitsTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ccnavi-test-hits-").replace("\\", "/")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.rules = write(os.path.join(self.root, "rules.yml"), json.dumps(ALLOW_ALL))

    def ccnavi(self, *args: str, rules: str = "", env: dict | None = None):
        environment = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        environment.pop("CLAUDE_PROJECT_DIR", None)
        environment.update(env or {})
        return run_ccnavi(
            [
                "--root",
                self.root,
                "--rules",
                rules or self.rules,
                "--log",
                "",
                "--state",
                "",
                *args,
            ],
            input="",
            cwd=ROOT,
            env=environment,
        )

    def body(self, *args: str, **kw) -> dict:
        done = self.ccnavi("--test", *args, "--json", **kw)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    # ---- 1. ヒットしたルール

    def test_builtin_guard_is_outside_with_a_short_form(self):
        os.makedirs(os.path.join(self.root, ".ccnavi", "config", "common"))
        body = self.body("Bash", "echo x > .ccnavi/config/common/rules.yml")
        self.assertEqual(body["verdict"], "deny")
        hit = body["rules"][0]
        self.assertEqual(
            (hit["id"], hit["source"], hit["section"]),
            ("builtin:guard-setting-files", "outside", "deny"),
        )
        # 組み込みの式は 2 万字を超える。そのまま出さず、切ったことと全体の字数を添える。
        self.assertLess(len(hit["written"]), 300)
        self.assertRegex(hit["written"], r"…（全 \d+ 字）$")
        self.assertLess(len(hit["pattern"]), 300)

        plain = self.ccnavi("--test", "Bash", "echo x > .ccnavi/config/common/rules.yml").stdout
        self.assertIn("deny:builtin:guard-setting-files（ルールファイルの外にある根拠）", plain)
        self.assertLess(max(len(line) for line in plain.splitlines()), 1000)

    def test_ticket_state_guard_is_outside(self):
        body = self.body("Write", f"{self.root}/wip/proposals/review/x.md")
        self.assertEqual(body["verdict"], "deny")
        by_id = {hit["id"]: hit for hit in body["rules"]}
        self.assertEqual(by_id["builtin:ticket-state"]["source"], "outside")
        self.assertEqual(by_id["builtin:ticket-state"]["section"], "deny")

    def test_fallback_defaults_are_outside(self):
        broken = write(os.path.join(self.root, "broken.yml"), "deny: [\n")
        body = self.body("Bash", "git push", rules=broken)
        self.assertEqual(body["verdict"], "deny")
        self.assertNotEqual(body["fallback"], "")
        self.assertTrue(body["rules"])
        self.assertEqual({hit["source"] for hit in body["rules"]}, {"outside"})

        # 期待の食い違いの行は、ルールファイルのルールだけを「ヒットしたルール」と言う。
        expectations = write(
            os.path.join(self.root, "expectations.yml"),
            'allow:\n  - tool: Bash\n    subject: "git push"\n    why: 食い違い\n',
        )
        plain = self.ccnavi("--test-expectations", expectations, rules=broken)
        self.assertEqual(plain.returncode, 1, plain.stdout)
        self.assertNotIn("ヒットしたルール", plain.stdout)

    def test_rule_hit_is_named_by_file_and_id(self):
        # id は必須。記録の名前はルールファイルの相対パスと書いた id の形になる。
        rules = write(
            os.path.join(self.root, "noid.yml"),
            json.dumps(
                {
                    "version": 1,
                    "deny": [
                        {
                            "id": "rm-build",
                            "match": "Bash",
                            "glob": "*rm -rf*",
                            "message": "削除しない",
                        }
                    ],
                }
            ),
        )
        body = self.body("Bash", "rm -rf build", rules=rules)
        self.assertEqual(body["verdict"], "deny")
        hit = body["rules"][0]
        self.assertEqual(
            (hit["id"], hit["source"], hit["section"], hit["kind"], hit["written"]),
            ("noid.yml:rm-build", "file", "deny", "glob", "*rm -rf*"),
        )

    def test_same_definition_in_two_projects_shows_the_one_that_hit(self):
        # レイヤーの和では後ろのプロジェクトの同じ定義が重複として破棄される。判定は行き先の
        # レイヤーで適用するので、破棄された側のプロジェクトにもヒットする。そのルールが見えること。
        git(self.root, "init", "--quiet", "-b", "main")
        write(os.path.join(self.root, ".gitignore"), "/projects/\n")
        write(common_path(self.root, "rules"), json.dumps({"version": 1}))
        for name in ("app", "lib"):
            project = os.path.join(self.root, "projects", name)
            os.makedirs(project)
            git(project, "init", "--quiet", "-b", "main")
            write(
                os.path.join(project, ".ccnavi", "config", "project", "rules.yml"),
                json.dumps(SCHEMA),
            )
        for name in ("app", "lib"):
            with self.subTest(project=name):
                done = run_ccnavi(
                    [
                        "--root",
                        self.root,
                        "--log",
                        "",
                        "--state",
                        "",
                        "--test",
                        "Write",
                        f"{self.root}/projects/{name}/schema/a.sql",
                        "--json",
                    ],
                    input="",
                    cwd=ROOT,
                    env={
                        **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
                        "CCNAVI_LEARNING": "disable",
                    },
                )
                body = json.loads(done.stdout)
                self.assertEqual(body["verdict"], "deny", done.stderr)
                hit = body["rules"][0]
                self.assertEqual(
                    (hit["id"], hit["source"], hit["section"], hit["written"]),
                    (
                        f"projects/{name}/.ccnavi/config/project/rules.yml:schema",
                        "file",
                        "deny",
                        "*/schema/*",
                    ),
                )

    # ---- 2. payload の欄

    def test_payload_is_shown_and_only_given_keys_change_the_json(self):
        body = self.body("Bash", "unknowncmd")
        self.assertNotIn("payload", body)
        plain = self.ccnavi("--test", "Bash", "unknowncmd").stdout
        self.assertIn(f"payload: cwd={self.root}  permission_mode=(無し)  agent_id=(無し)", plain)

        sub = os.path.join(self.root, "sub")
        os.makedirs(sub)
        body = self.body(
            "Bash", "unknowncmd", "--cwd", sub, "--permission-mode", "default", "--agent-id", "a1"
        )
        self.assertEqual(
            body["payload"],
            {"cwd": os.path.abspath(sub), "permission_mode": "default", "agent_id": "a1"},
        )

    def test_permission_mode_reaches_the_judgment(self):
        rules = write(os.path.join(self.root, "empty.yml"), json.dumps({"version": 1}))
        cases = [
            ((), "ask"),
            (("--permission-mode", "default"), "handover"),
            (("--permission-mode", "dontAsk"), "deny"),
        ]
        for extra, verdict in cases:
            with self.subTest(extra=extra):
                body = self.body("Bash", "unknowncmd", *extra, rules=rules)
                self.assertEqual((body["verdict"], body["code"]), (verdict, "UNDECLARED"))

    def test_guard_unwatched_is_resolved_like_the_hook(self):
        # hook は空白と大文字を落として読む。試験も同じ値で判定する。
        rules = write(os.path.join(self.root, "empty.yml"), json.dumps({"version": 1}))
        body = self.body(
            "Bash",
            "unknowncmd",
            "--permission-mode",
            "dontAsk",
            rules=rules,
            env={"CCNAVI_GUARD_UNWATCHED": " Disable"},
        )
        self.assertEqual(body["verdict"], "handover")

    def test_agent_id_reaches_the_judgment(self):
        command = "sh .ccnavi/scripts/ccnavi-ticket.sh start x"
        self.assertEqual(self.body("Bash", command)["verdict"], "allow")
        body = self.body("Bash", command, "--agent-id", "a1")
        self.assertEqual((body["verdict"], body["code"]), ("deny", "DENY_SUBAGENT_TICKET_OP"))

    def test_unknown_mode_and_missing_cwd_are_refused(self):
        cases = [
            (("--permission-mode", "dontask"), "権限モードの名前ではない"),
            (("--cwd", os.path.join(self.root, "missing")), "ディレクトリではない"),
        ]
        for extra, said in cases:
            for json_flag in ((), ("--json",)):
                with self.subTest(extra=extra, json=json_flag):
                    done = self.ccnavi("--test", "Bash", "ls", *extra, *json_flag)
                    self.assertEqual(done.returncode, 1, done.stdout)
                    self.assertEqual(done.stdout, "")
                    self.assertIn(said, done.stderr)

    def test_payload_flags_are_for_test_only(self):
        for route in ("--explain", "--lint", "--docs"):
            for flag in ("--permission-mode", "--agent-id"):
                with self.subTest(route=route, flag=flag):
                    done = self.ccnavi(route, flag, "default")
                    self.assertEqual(done.returncode, 1)
                    self.assertIn(f"{flag} は --test でだけ使える", done.stderr)

    def test_flags_between_tool_and_subject_and_extra_words_are_refused(self):
        """オプションを TOOL と SUBJECT の間に置いた形と、余った語は、黙って破棄せずに断る。"""
        for argv, said in (
            (
                ("--test", "Bash", "--permission-mode", "auto", "ls"),
                "オプション（--permission-mode）",
            ),
            (("--test", "--agent-id", "a1", "ls"), "オプション（--agent-id）"),
            (("--test", "Bash", "ls", "extra"), "余った語: extra"),
        ):
            with self.subTest(argv=argv):
                done = self.ccnavi(*argv)
                self.assertEqual(done.returncode, 1, done.stdout)
                self.assertEqual(done.stdout, "")
                self.assertIn(said, done.stderr)
        done = self.ccnavi("--test", "Bash", "ls", "--permission-mode", "auto")
        self.assertEqual(done.returncode, 0, done.stderr)

    # ---- 3. --explain の表

    def explained(self, env: dict | None = None) -> list[str]:
        done = self.ccnavi("--explain", env=env)
        self.assertEqual(done.returncode, 0, done.stderr)
        lines = done.stdout.splitlines()
        start = lines.index("■ どのルールにも一致しないツール呼び出し")
        return lines[start : start + 10]

    def row(self, lines: list[str], mode: str) -> str:
        return next(line for line in lines if line.split()[0:1] == [mode])

    def test_explain_follows_undeclared_verdict(self):
        lines = self.explained()
        self.assertIn(
            "読めた: 権限モードに委ねる / 読み切れない: ccnavi が確認を出す",
            self.row(lines, "default"),
        )
        self.assertIn("（classifier が判断）", self.row(lines, "auto"))
        self.assertIn(
            "読めた: ccnavi が止める / 読み切れない: ccnavi が止める", self.row(lines, "dontAsk")
        )
        self.assertIn("読めた: ccnavi が確認を出す", self.row(lines, "(不明・無し)"))

        # 書いたままの表記（空白・大文字）でも、hook と同じく disable と読む。読み切れない
        # 呼び出しは、それでも止める。
        lines = self.explained({"CCNAVI_GUARD_UNWATCHED": " Disable"})
        self.assertIn(
            "読めた: 権限モードに委ねる / 読み切れない: ccnavi が止める",
            self.row(lines, "bypassPermissions"),
        )
        self.assertTrue(any("CCNAVI_GUARD_UNWATCHED=disable" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
