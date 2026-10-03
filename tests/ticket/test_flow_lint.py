"""`ccnavi --lint --flow <パス>` の受入テスト。

VS Code 拡張のフロー編集画面は、開くときと保存の前に編集中の本文を一時ファイルに書いて
これに掛け、正しいかの答えを実行ファイルから受ける。見るのは 5 つ。

1. SubagentStart と同じ読み手・同じ検査（`flow.load`）で読み、読めなければ `(flow)` の error
2. 読めるフローには `(flow)` の苦情が出ない
3. `--json` の形は README「lint の JSON」のまま（`where` が `(flow)`）
4. 形の誤り（`nodes` が無い、`id` が無い・重なる、`connections` が並びでない）は
   SubagentStart の読み（`flow.load`）とここで同じ理由になる
5. 診断の外では落として言う。`--lint` でない診断でも読まずにそう言う
6. `--json` には読めた中身（`flow.data`）を載せる。JSON にそのまま載らない値は印にする
   （拡張は自分の読みとこれを見比べ、食い違えば開かない・保存しない）
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from ccnavi.tickets import flow
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

    def flow_errors(self, path: str) -> list[dict]:
        return [p for p in self.flow_problems(path) if p["severity"] == "error"]

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
        # ユーザ向けの本文も、確かめたフローを名乗る。
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
        # connections が無いフローは読める（並べるときは線なし）。start・end が無いのは warn。
        self.assertEqual(self.flow_errors(self.file("nodes:\n  - {id: a, type: prompt}\n")), [])

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
        mark = flow.JSON_MARK
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
        self.assert_refused(path, flow.NOT_UTF8)
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


class FlowStructureTest(unittest.TestCase):
    """線の構造（`flow.structure_problems`）。読むのは止めない warn で、巡回は言わない。"""

    def problems(self, text: str) -> list[str]:
        data, why = flow.parse(text.encode("utf-8"))
        self.assertEqual(why, "")
        return flow.structure_problems(data)

    def test_a_sound_flow_says_nothing(self):
        self.assertEqual(self.problems(STRUCTURE_BASE), [])
        self.assertEqual(flow.structure_problems(yaml_load(WORKFLOW_YAML)), [])

    def test_lines_to_missing_nodes(self):
        said = self.problems(STRUCTURE_BASE + "  - {id: c5, from: p1, to: nowhere}\n")
        self.assertEqual(said, ["線 c5 の to が無いノード（nowhere）を指している"])
        said = self.problems(STRUCTURE_BASE + "  - {id: c5, from: ghost, to: e}\n")
        self.assertEqual(said, ["線 c5 の from が無いノード（ghost）を指している"])
        said = self.problems(STRUCTURE_BASE + "  - {from: p1}\n")
        self.assertEqual(said, ["線 connections[4] の to が無いノード（(空)）を指している"])

    def test_unreachable_nodes(self):
        text = STRUCTURE_BASE.replace(
            "  - {id: e, type: end, name: 終了}\n",
            "  - {id: e, type: end, name: 終了}\n  - {id: lone, type: prompt, name: 離れ}\n",
        )
        self.assertEqual(self.problems(text), ["start から届かないノードがある: lone（離れ）"])

    def test_lines_into_start_and_out_of_end(self):
        said = self.problems(STRUCTURE_BASE + "  - {id: c5, from: e, to: s}\n")
        self.assertIn("線 c5 が start（s（開始））に入っている", said)
        self.assertIn("線 c5 が end（e（終了））から出ている", said)

    def test_branch_and_question_exits_without_lines(self):
        text = STRUCTURE_BASE.replace("  - {id: c3, from: q, to: p1, fromPort: branch-1}\n", "")
        self.assertEqual(self.problems(text), ["問い q（方針） の出口に線が無い: 大きく"])
        # 分岐は項目の id でも出口を当てる。
        branch = """\
nodes:
  - {id: s, type: start}
  - id: b
    type: ifElse
    data: {branches: [{id: bt, label: 真}, {id: bf, label: 偽}]}
  - {id: e, type: end}
connections:
  - {id: c1, from: s, to: b}
  - {id: c2, from: b, to: e, fromPort: bt}
"""
        self.assertEqual(self.problems(branch), ["分岐 b の出口に線が無い: 偽"])
        branch += "  - {id: c3, from: b, to: e, fromPort: bf}\n"
        self.assertEqual(self.problems(branch), [])

    def test_missing_start_and_end(self):
        said = self.problems("nodes:\n  - {id: a, type: prompt}\n")
        self.assertIn("start が無い（どこから始めるかが決まらない）", said)
        self.assertIn("end が無い（どこで終わるかが決まらない）", said)

    def test_loops_are_not_warned(self):
        text = STRUCTURE_BASE + "  - {id: c5, from: p1, to: q}\n"
        self.assertEqual(self.problems(text), [])

    def test_groups_are_left_out(self):
        # グループに繋がる線も、届かないグループも言わない（手順ではない）。
        text = STRUCTURE_BASE + "  - {id: c5, from: p1, to: g}\n"
        self.assertEqual(self.problems(text), [])

    def test_multi_select_questions_have_one_output(self):
        # 複数選択の問いは出口を分けない（`output` の 1 本。画面の portsOf と同じ）
        multi = """\
nodes:
  - {id: s, type: start}
  - id: q
    type: askUserQuestion
    name: 方針
    data: {multiSelect: true, options: [{label: 小さく}, {label: 大きく}]}
  - {id: e, type: end}
connections:
  - {id: c1, from: s, to: q}
"""
        said = self.problems(multi)
        self.assertIn("問い q（方針） の出口に線が無い: output（複数選択）", said)
        # 選択肢ごとの出口（小さく・大きく）は探さない
        self.assertFalse([line for line in said if "小さく" in line], said)
        linked_out = multi + "  - {id: c2, from: q, to: e, fromPort: output}\n"
        self.assertEqual(self.problems(linked_out), [])
        # fromPort を書かない線も output として読む
        self.assertEqual(self.problems(multi + "  - {id: c2, from: q, to: e}\n"), [])

    def test_a_branch_exit_into_a_group_is_a_used_exit(self):
        # グループへ出る線は手順に数えないが、出口は使っている（「出口に線が無い」とは言わない）
        text = STRUCTURE_BASE.replace(
            "  - {id: c3, from: q, to: p1, fromPort: branch-1}\n",
            "  - {id: c3, from: q, to: g, fromPort: branch-1}\n",
        )
        self.assertEqual(self.problems(text), [])

    def test_lines_to_missing_nodes_are_capped(self):
        extra = "".join(
            f"  - {{id: x{i}, from: p1, to: ghost{i}}}\n" for i in range(flow.ITEM_LIMIT + 3)
        )
        said = self.problems(STRUCTURE_BASE + extra)
        missing = [line for line in said if "無いノード" in line]
        self.assertEqual(len(missing), flow.ITEM_LIMIT + 1, said)
        self.assertEqual(missing[-1], "無いノードを指す線は…ほか 3 件")

    def test_broken_shapes_do_not_raise(self):
        for data in ({"nodes": [{"id": "a", "data": [1]}], "connections": [{"from": [1]}]}, 5):
            with self.subTest(data=data):
                self.assertIsInstance(flow.structure_problems(data), list)


def yaml_load(text: str):
    data, why = flow.parse(text.encode("utf-8"))
    assert why == "", why
    return data


class FlowCandidatesTest(unittest.TestCase):
    """選べるエージェントとスキルの名前（`flow.catalog`）と、綴りの warn（`name_problems`）。"""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ccnavi-flow-cand-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_builtins_only_without_a_claude_dir(self):
        cat = flow.catalog(self.root)
        self.assertEqual(
            cat["agents"],
            [{"name": n, "source": "builtin"} for n in ("general-purpose", "Explore", "Plan")],
        )
        self.assertEqual(cat["skills"], [])

    def test_project_agents_and_skills_are_listed(self):
        agents = os.path.join(self.root, ".claude", "agents")
        write(os.path.join(agents, "reviewer.md"), "---\nname: code-reviewer\n---\n本文\n")
        write(os.path.join(agents, "plain.md"), "frontmatter なし\n")
        write(os.path.join(agents, "notes.txt"), "---\nname: nope\n---\n")
        skills = os.path.join(self.root, ".claude", "skills")
        write(os.path.join(skills, "commit", "SKILL.md"), "---\nname: 'commit'\n---\n")
        write(os.path.join(skills, "bare", "SKILL.md"), "本文だけ\n")
        os.makedirs(os.path.join(skills, "empty"))
        cat = flow.catalog(self.root)
        project = [a["name"] for a in cat["agents"] if a["source"] == "project"]
        self.assertEqual(project, ["plain", "code-reviewer"])
        self.assertEqual([s["name"] for s in cat["skills"]], ["bare", "commit"])

    def test_misspelled_names_are_warned(self):
        write(os.path.join(self.root, ".claude", "skills", "commit", "SKILL.md"), "x\n")
        cat = flow.catalog(self.root)
        text = """\
nodes:
  - {id: a1, type: subAgent, data: {builtInType: explore}}
  - {id: a2, type: subAgent, data: {builtInType: Explore}}
  - {id: a3, type: subAgent, data: {builtInType: genral-purpose}}
  - {id: a4, type: subAgent, data: {description: 種類なし}}
  - {id: k1, type: skill, data: {name: comit}}
  - {id: k2, type: skill, data: {name: commit}}
  - {id: k3, type: skill, data: {name: "plugin:pdf"}}
  - {id: k4, type: skill, data: {name: ""}}
subAgentFlows:
  - {id: f, nodes: [{id: in, type: skill, data: {name: nested}}]}
"""
        said = flow.name_problems(yaml_load(text), cat)
        self.assertEqual(len(said), 4, said)
        self.assertIn("a1", said[0])
        self.assertIn("大文字小文字が違う（Explore）", said[0])
        self.assertIn("genral-purpose", said[1])
        self.assertIn("comit", said[2])
        self.assertIn("nested", said[3])

    def test_frontmatter_is_read_as_yaml(self):
        agents = os.path.join(self.root, ".claude", "agents")
        # 折り返し（>-）と行末の注釈で名前が化けない。閉じていない frontmatter は読まない
        write(
            os.path.join(agents, "folded.md"), "---\nname: >-\n  folded-name\ndescription: x\n---\n"
        )
        write(os.path.join(agents, "noted.md"), "---\nname: noted  # 注釈\n---\n")
        write(os.path.join(agents, "open.md"), "---\nname: never-closed\n")
        write(os.path.join(agents, "number.md"), "---\nname: 5\n---\n")
        # 拡張子の大文字小文字は区別しない。ディレクトリは数えない
        write(os.path.join(agents, "UPPER.MD"), "---\nname: upper\n---\n")
        os.makedirs(os.path.join(agents, "dir.md"))
        cat = flow.catalog(self.root)
        project = sorted(a["name"] for a in cat["agents"] if a["source"] == "project")
        self.assertEqual(project, ["folded-name", "noted", "number", "open", "upper"])

    def test_links_on_the_way_are_not_followed(self):
        outside = tempfile.mkdtemp(prefix="ccnavi-flow-cand-out-")
        self.addCleanup(shutil.rmtree, outside, True)
        write(os.path.join(outside, "agents", "far.md"), "---\nname: far\n---\n")
        write(os.path.join(outside, "skills", "far", "SKILL.md"), "---\nname: far\n---\n")
        try:
            os.symlink(outside, os.path.join(self.root, ".claude"), target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"シンボリックリンクを作れない: {exc}")
        cat = flow.catalog(self.root)
        self.assertEqual([a["source"] for a in cat["agents"]], ["builtin"] * 3)
        self.assertEqual(cat["skills"], [])


class FlowLintExtrasTest(unittest.TestCase):
    """`--lint --json --flow` の `flow.rendered` と `flow.candidates`、構造と綴りの warn。"""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ccnavi-flow-extra-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def lint(self, text: str) -> dict:
        path = write(os.path.join(self.root, "flow.yml"), text)
        result = run_ccnavi(["--root", self.root, "--lint", "--json", "--flow", path])
        return json.loads(result.stdout)

    def test_rendered_is_what_subagent_start_lists(self):
        payload = self.lint(WORKFLOW_YAML)
        self.assertEqual(payload["flow"]["rendered"], flow.render(yaml_load(WORKFLOW_YAML))[0])
        self.assertTrue(payload["flow"]["rendered"][0].startswith("1. [start] 開始"))
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
