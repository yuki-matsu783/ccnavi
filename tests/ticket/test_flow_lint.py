"""`ccnavi --lint --flow <パス>` の受入テスト（ADR-0035・ADR-0085）。

VS Code 拡張のフロー編集画面は、開くときと保存の前に編集中の本文を一時ファイルに書いて
これに掛け、正しいかの答えを実行ファイルから受ける。見るのは 5 つ。

1. SubagentStart と同じ読み手・同じ検査（`flow.load`）で読み、読めなければ `(flow)` の error
2. 読めるフローには `(flow)` の苦情が出ない
3. `--json` の形は README「lint の JSON」のまま（`where` が `(flow)`）
4. 形の誤り（`nodes` が無い、`id` が無い・重なる、`connections` が並びでない）は
   SubagentStart の読み（`flow.load`）とここで同じ理由になる
5. 診断の外では落として言う。`--lint` でない診断でも読まずにそう言う
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from ccnavi import flow
from tests.inproc import run_ccnavi
from tests.ticket.test_flow import WORKFLOW_YAML
from tests.ticket.test_ticket import write


class FlowLintTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ccnavi-flow-lint-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.outside = tempfile.mkdtemp(prefix="ccnavi-flow-tmp-")
        self.addCleanup(shutil.rmtree, self.outside, True)

    def lint(self, path: str, *extra: str):
        return run_ccnavi(["--root", self.root, "--lint", "--json", "--flow", path, *extra])

    def flow_problems(self, path: str) -> list[dict]:
        result = self.lint(path)
        payload = json.loads(result.stdout)
        return [p for p in payload["problems"] if p["where"] == "(flow)"]

    def file(self, text: str, name: str = "flow.yml") -> str:
        return write(os.path.join(self.outside, name), text)

    def assert_refused(self, path: str, reason: str):
        problems = self.flow_problems(path)
        self.assertEqual(len(problems), 1, problems)
        self.assertEqual(problems[0]["severity"], "error")
        self.assertIn(reason, problems[0]["detail"])
        # 苦情は渡したパスを名乗る（画面が対象のファイルの綴りに直す）。
        self.assertTrue(problems[0]["detail"].startswith(path), problems[0]["detail"])

    def test_a_readable_flow_has_no_flow_problem(self):
        self.assertEqual(self.flow_problems(self.file(WORKFLOW_YAML)), [])
        # 人向けの本文も、確かめたフローを名乗る。
        path = self.file(WORKFLOW_YAML)
        text = run_ccnavi(["--root", self.root, "--lint", "--flow", path])
        self.assertIn(f"フロー: {path}", text.stdout)
        self.assertNotIn("(flow)", text.stdout)

    def test_the_json_keeps_the_lint_shape(self):
        path = self.file("nodes: [\n")
        result = self.lint(path)
        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["version"], 1)
        for key in ("root", "rules", "mode", "ticket_control", "projects", "errors", "warns"):
            self.assertIn(key, payload)
        flows = [p for p in payload["problems"] if p["where"] == "(flow)"]
        self.assertEqual(len(flows), 1)
        self.assertEqual(set(flows[0]), {"severity", "where", "detail"})
        self.assertGreaterEqual(payload["errors"], 1)

    def test_broken_yaml_is_an_error(self):
        self.assert_refused(
            self.file("nodes:\n  - id: s\n    type: [start\n"), "YAML として読めない"
        )
        self.assert_refused(self.file("nodes: []\n---\nnodes: []\n"), "YAML として読めない")

    def test_aliases_are_an_error(self):
        self.assert_refused(self.file("x: &a [1]\nnodes: [{id: a, data: *a}]\n"), flow.ALIASED)

    def test_shape_errors_are_the_same_reasons_as_subagent_start(self):
        for text, reason in (
            ("", "最上位がキーと値の並びではない"),
            ("- 1\n", "最上位がキーと値の並びではない"),
            ("name: x\n", "`nodes` の並びが無い"),
            ("nodes: [1]\n", "nodes[0] がキーと値の並びではない"),
            ("nodes:\n  - {type: start}\n", "nodes[0] に文字列の id が無い"),
            ("nodes:\n  - {id: 1, type: start}\n", "nodes[0] に文字列の id が無い"),
            ("nodes:\n  - {id: a}\n  - {id: a}\n", "ノードの id が重なっている（a）"),
            ("nodes: []\nconnections: {}\n", "`connections` が並びではない"),
            ("nodes: []\nconnections: [1]\n", "connections[0] がキーと値の並びではない"),
        ):
            with self.subTest(text=text):
                path = self.file(text)
                self.assert_refused(path, reason)
                # SubagentStart の読みも同じ理由で読まない。
                self.assertEqual(flow.load(path), (None, reason))
        # connections が無いフローは読める（並べるときは線なし）。
        self.assertEqual(self.flow_problems(self.file("nodes:\n  - {id: a, type: prompt}\n")), [])

    def test_links_and_non_regular_files_are_errors(self):
        real = self.file(WORKFLOW_YAML, "real.yml")
        link = os.path.join(self.outside, "link.yml")
        os.symlink(real, link)
        self.assert_refused(link, flow.LINKED)
        # ツリーの中なら、ルートからの途中のリンクも見る（SubagentStart と同じ）。
        os.makedirs(os.path.join(self.root, "elsewhere"))
        write(os.path.join(self.root, "elsewhere", "b.yml"), WORKFLOW_YAML)
        os.makedirs(os.path.join(self.root, ".ccnavi", "approved"))
        os.symlink(
            os.path.join(self.root, "elsewhere"),
            os.path.join(self.root, ".ccnavi", "approved", "flows"),
        )
        self.assert_refused(
            os.path.join(self.root, ".ccnavi", "approved", "flows", "b.yml"), flow.LINKED
        )
        # ディレクトリ（ふつうのファイルでないもの）も読まない。
        self.assert_refused(self.outside, flow.NOT_REGULAR)
        # ハードリンク。
        twin = os.path.join(self.outside, "twin.yml")
        os.link(real, twin)
        self.assert_refused(twin, flow.HARD_LINKED)

    def test_large_and_missing_files_are_errors(self):
        self.assert_refused(
            self.file("nodes: []\npad: " + "x" * (flow.FILE_LIMIT + 10) + "\n"), "大きすぎる"
        )
        self.assert_refused(os.path.join(self.outside, "nothing.yml"), "無い")

    def test_relative_paths_are_read_from_the_working_directory(self):
        self.file("nodes: [\n")
        result = run_ccnavi(
            ["--root", self.root, "--lint", "--json", "--flow", "flow.yml"], cwd=self.outside
        )
        flows = [p for p in json.loads(result.stdout)["problems"] if p["where"] == "(flow)"]
        self.assertEqual(len(flows), 1)
        self.assertIn(
            os.path.realpath(self.outside), os.path.realpath(flows[0]["detail"].split(":")[0])
        )

    def test_only_lint_reads_the_flag(self):
        path = self.file("nodes: [\n")
        payload = json.dumps(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "Read",
                "tool_input": {"file_path": os.path.join(self.root, "README.md")},
                "cwd": self.root,
            }
        )
        hook = run_ccnavi(["--root", self.root, "--mode", "enable", "--flow", path], input=payload)
        self.assertIn("--flow は診断", hook.stderr)
        tried = run_ccnavi(["--root", self.root, "--test", "Bash", "ls", "--json", "--flow", path])
        self.assertIn("--flow は --lint でだけ読む", tried.stderr)
        # 渡さなければ `(flow)` の苦情は出ない。
        plain = run_ccnavi(["--root", self.root, "--lint", "--json"])
        self.assertEqual(
            [p for p in json.loads(plain.stdout)["problems"] if p["where"] == "(flow)"], []
        )


if __name__ == "__main__":
    unittest.main()
