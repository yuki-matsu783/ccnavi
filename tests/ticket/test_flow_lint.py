"""`ccnavi --lint --flow <パス>` の受入テスト。

VS Code 拡張のフロー編集画面は、開くときと保存の前に編集中の本文を一時ファイルに書いて
これに掛け、正しいかの答えを実行ファイルから受ける。見るのは 5 つ。

1. SubagentStart と同じ読み手・同じ検査で読み、読めなければ `(flow)` の error
2. 読めるフローには `(flow)` の苦情が出ない
3. `--json` の形は docs/11_requirements/json-shapes.md「lint の JSON」のまま（`where` が `(flow)`）
4. 形の誤り（`nodes` が無い、`id` が無い・重なる、`connections` がリストでない）を
   SubagentStart と同じ理由で言う
5. 診断の外では無視して言う。`--lint` でない診断でも読まずにそう言う
6. `--json` には読めた中身（`flow.data`）を載せる。JSON にそのまま載らない値は目印にする
   （拡張は自分の読みとこれを見比べ、食い違えば開かない・保存しない）

構造の検査（線・到達・ループ）と、候補（エージェント・スキル）の読み方は go の flow のテストが見る。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from tests import requires_symlink
from tests.inproc import run_ccnavi
from tests.ticket.test_flow import WORKFLOW_YAML
from tests.ticket.test_ticket import write

# 読まない理由（SubagentStart と同じ文）と、読むファイルの大きさの上限、JSON に載らない値の目印。
LINKED = "ファイルか、ツリーのルートからそこまでの途中がシンボリックリンクなので読まない"
NOT_REGULAR = "ふつうのファイルではない（名前付きパイプ・デバイスなど）ので読まない"
HARD_LINKED = "ハードリンク（ほかのパスからも同じ中身を開ける）なので読まない。"
ALIASED = "YAML の別名（`*名前`）があるので読まない。"
NOT_UTF8 = "UTF-8 として読めない"
FILE_LIMIT = 256 * 1024
JSON_MARK = "$ccnavi"


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

    def flow_errors(self, path: str) -> list[dict]:
        return [p for p in self.flow_problems(path) if p["severity"] == "error"]

    def file(self, text: str, name: str = "flow.yml") -> str:
        return write(os.path.join(self.outside, name), text)

    def assert_refused(self, path: str, reason: str):
        problems = self.flow_problems(path)
        self.assertEqual(len(problems), 1, problems)
        self.assertEqual(problems[0]["severity"], "error")
        self.assertIn(reason, problems[0]["detail"])
        # 苦情には渡したパスを出す（画面が対象のファイルのパスに直す）。
        self.assertTrue(problems[0]["detail"].startswith(path), problems[0]["detail"])

    def test_a_readable_flow_has_no_flow_problem(self):
        self.assertEqual(self.flow_problems(self.file(WORKFLOW_YAML)), [])
        # ユーザ向けの本文も、確かめたフローの名前を出す。
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
        self.assert_refused(self.file("x: &a [1]\nnodes: [{id: a, data: *a}]\n"), ALIASED)

    def test_shape_errors_are_the_same_reasons_as_subagent_start(self):
        for text, reason in (
            ("", "最上位がマッピングではない"),
            ("- 1\n", "最上位がマッピングではない"),
            ("name: x\n", "`nodes` のリストがありません。"),
            ("nodes: [1]\n", "nodes[0] がマッピングではない"),
            ("nodes:\n  - {type: start}\n", "nodes[0] に文字列の id がありません。"),
            ("nodes:\n  - {id: 1, type: start}\n", "nodes[0] に文字列の id がありません。"),
            ("nodes:\n  - {id: a}\n  - {id: a}\n", "ノードの id が重なっている（a）"),
            ("nodes: []\nconnections: {}\n", "`connections` がリストではない"),
            ("nodes: []\nconnections: [1]\n", "connections[0] がマッピングではない"),
        ):
            with self.subTest(text=text):
                path = self.file(text)
                self.assert_refused(path, reason)
        # connections が無いフローは読める（並べるときは線なし）。start・end が無いのは warn。
        self.assertEqual(self.flow_errors(self.file("nodes:\n  - {id: a, type: prompt}\n")), [])

    @requires_symlink
    def test_links_and_non_regular_files_are_errors(self):
        real = self.file(WORKFLOW_YAML, "real.yml")
        link = os.path.join(self.outside, "link.yml")
        os.symlink(real, link)
        self.assert_refused(link, LINKED)
        # ツリーの中なら、ルートからの途中のリンクも見る（SubagentStart と同じ）。
        os.makedirs(os.path.join(self.root, "elsewhere"))
        write(os.path.join(self.root, "elsewhere", "b.yml"), WORKFLOW_YAML)
        os.makedirs(os.path.join(self.root, ".ccnavi", "approved"))
        os.symlink(
            os.path.join(self.root, "elsewhere"),
            os.path.join(self.root, ".ccnavi", "approved", "flows"),
        )
        self.assert_refused(
            os.path.join(self.root, ".ccnavi", "approved", "flows", "b.yml"), LINKED
        )
        # ディレクトリ（ふつうのファイルでないもの）も読まない。
        self.assert_refused(self.outside, NOT_REGULAR)
        # ハードリンク。
        twin = os.path.join(self.outside, "twin.yml")
        os.link(real, twin)
        self.assert_refused(twin, HARD_LINKED)

    def test_large_and_missing_files_are_errors(self):
        self.assert_refused(
            self.file("nodes: []\npad: " + "x" * (FILE_LIMIT + 10) + "\n"), "大きすぎる"
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
            os.path.realpath(self.outside), os.path.realpath(flows[0]["detail"].split(": ")[0])
        )

    def flow_data(self, text: str):
        result = self.lint(self.file(text))
        payload = json.loads(result.stdout)
        self.assertIn("flow", payload)
        return payload["flow"]["data"]

    def test_the_json_carries_what_was_read(self):
        path = self.file(WORKFLOW_YAML)
        payload = json.loads(self.lint(path).stdout)
        self.assertEqual(payload["flow"]["path"], path)
        self.assertEqual(payload["flow"]["data"]["nodes"][0]["id"], "s")
        # 読めなければ中身は null（苦情は error）。
        broken = json.loads(self.lint(self.file("nodes: [\n")).stdout)
        self.assertIsNone(broken["flow"]["data"])
        # --flow を渡さなければ欄ごと無い（今までの形のまま）。
        plain = json.loads(run_ccnavi(["--root", self.root, "--lint", "--json"]).stdout)
        self.assertNotIn("flow", plain)

    def test_values_are_read_by_pyyaml_and_marked_when_json_cannot_hold_them(self):
        mark = JSON_MARK
        head = "nodes:\n  - id: a\n    data:\n"
        cases = (
            ("0755", 493),
            ("yes", True),
            ("on", True),
            ("1:30", 90),
            ("0o17", "0o17"),
            ("1e3", "1e3"),
            ("1_000", 1000),
            ("1.", {mark: "float", "value": 1.0}),
            ("1.5", {mark: "float", "value": 1.5}),
            ("!!float 1", {mark: "float", "value": 1.0}),
            (".inf", {mark: "float", "text": "inf"}),
            (".nan", {mark: "float", "text": "nan"}),
            ("2026-01-01", {mark: "date", "text": "2026-01-01"}),
            ("!!binary aGk=", {mark: "bytes", "text": "aGk="}),
            ("123456789012345678901", {mark: "int", "text": "123456789012345678901"}),
            ("{1: a}", {mark: "map", "items": [[1, "a"]]}),
            ('{"$ccnavi": a}', {mark: "map", "items": [["$ccnavi", "a"]]}),
            ("~", None),
            ("'yes'", "yes"),
        )
        for value, read in cases:
            with self.subTest(value=value):
                data = self.flow_data(f"{head}      v: {value}\n")
                self.assertEqual(data["nodes"][0]["data"]["v"], read)

    def test_merge_keys_are_read_as_pyyaml_reads_them(self):
        # 別名を使わないマージキーは PyYAML が展開する。拡張の読み手は展開しないので、
        # 画面は食い違いとして断る。
        data = self.flow_data("nodes: [{<<: {id: a}}]\n")
        self.assertEqual(data["nodes"], [{"id": "a"}])

    def test_broken_utf8_is_an_error_and_a_bom_is_dropped(self):
        path = os.path.join(self.outside, "bad.yml")
        with open(path, "wb") as f:
            f.write(b"nodes:\n  - id: a\n    name: \xff\xfe\n")
        self.assert_refused(path, NOT_UTF8)
        bom = os.path.join(self.outside, "bom.yml")
        with open(bom, "wb") as f:
            f.write(b"\xef\xbb\xbfnodes:\n  - id: a\n")
        self.assertEqual(self.flow_errors(bom), [])

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


# 線の構造の検査に掛ける見本。start → 問い → (小さく) p1 → end。
STRUCTURE_BASE = """\
nodes:
  - {id: s, type: start, name: 開始}
  - id: q
    type: askUserQuestion
    name: 方針
    data:
      questionText: どちらで
      options: [{label: 小さく}, {label: 大きく}]
  - {id: p1, type: prompt, name: 読む, data: {prompt: 読む}}
  - {id: e, type: end, name: 終了}
  - {id: g, type: group, name: 枠}
connections:
  - {id: c1, from: s, to: q}
  - {id: c2, from: q, to: p1, fromPort: branch-0}
  - {id: c3, from: q, to: p1, fromPort: branch-1}
  - {id: c4, from: p1, to: e}
"""


# loop の見本。start → p1 → l(loop) → 繰り返す: p1 / 抜ける: end。
LOOP_BASE = """\
nodes:
  - {id: s, type: start, name: 開始}
  - {id: p1, type: prompt, name: 直す, data: {prompt: 直す}}
  - id: l
    type: loop
    name: 直す
    data: {condition: テストが落ちる, maxIterations: 3, branches: [
      {id: body, label: 繰り返す}, {id: done, label: 抜ける}]}
  - {id: e, type: end, name: 終了}
connections:
  - {id: c1, from: s, to: p1}
  - {id: c2, from: p1, to: l}
  - {id: c3, from: l, to: p1, fromPort: body}
  - {id: c4, from: l, to: e, fromPort: done}
"""


class FlowLintExtrasTest(unittest.TestCase):
    """`--lint --json --flow` の `flow.rendered` と `flow.candidates`、構造と表記の warn。"""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ccnavi-flow-extra-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def lint(self, text: str) -> dict:
        path = write(os.path.join(self.root, "flow.yml"), text)
        result = run_ccnavi(["--root", self.root, "--lint", "--json", "--flow", path])
        return json.loads(result.stdout)

    def test_rendered_is_what_subagent_start_lists(self):
        payload = self.lint(WORKFLOW_YAML)
        self.assertTrue(payload["flow"]["rendered"][0].startswith("1. [start] 開始"))
        self.assertIn("3. [askUserQuestion] 方針", payload["flow"]["rendered"][2])
        broken = self.lint("nodes: [\n")
        self.assertIsNone(broken["flow"]["rendered"])
        # 候補は読めなくても載る（フローに依らない）。
        self.assertEqual(broken["flow"]["candidates"]["agents"][0]["name"], "general-purpose")

    def test_candidates_come_from_the_workspace(self):
        write(os.path.join(self.root, ".claude", "agents", "r.md"), "---\nname: rev\n---\n")
        payload = self.lint(WORKFLOW_YAML)
        names = [a["name"] for a in payload["flow"]["candidates"]["agents"]]
        self.assertIn("rev", names)
        self.assertEqual(payload["flow"]["candidates"]["skills"], [])

    def test_structure_and_names_are_warns_not_errors(self):
        text = STRUCTURE_BASE + "  - {id: c5, from: p1, to: nowhere}\n"
        text = text.replace(
            "{id: p1, type: prompt, name: 読む, data: {prompt: 読む}}",
            "{id: p1, type: subAgent, name: 読む, data: {builtInType: Explorer}}",
        )
        path = os.path.join(self.root, "flow.yml")
        payload = self.lint(text)
        flows = [p for p in payload["problems"] if p["where"] == "(flow)"]
        self.assertEqual({p["severity"] for p in flows}, {"warn"}, flows)
        self.assertEqual(len(flows), 2, flows)
        for p in flows:
            self.assertTrue(p["detail"].startswith(path), p["detail"])
        # 読めているので中身と手順は載る。
        self.assertIsNotNone(payload["flow"]["data"])
        self.assertIsNotNone(payload["flow"]["rendered"])


if __name__ == "__main__":
    unittest.main()
