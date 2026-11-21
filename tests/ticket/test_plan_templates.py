"""作業・レビュー計画のテンプレート（読む側）の受入テスト。

読み込み・2 層の統合結果・検査・`--templates` の一覧・検索・`--check-file` の答えの中身は、
go/internal/tickets/proposal/templates の見本（`TestMatchesGolden`）が 1 字単位で見る。
ここで見るのは見本が持たない次の 4 つ。

1. 読み込み: フローと同じ読み手の保護（リンク・ハードリンク）
2. `ccnavi --templates` のテキストの出方、`--json` の答えの形（tests/fixtures/templates-json.json と
   templates-check-json.json。拡張と共有する）、使い方の誤りの拒否
3. `ccnavi --lint` への出方（レイヤーの名前と重さ）
4. 型の定義が変わったときの扱い
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import unittest

from tests import ROOT, can_symlink, common_path, config_path
from tests.inproc import run_ccnavi
from tests.ticket.test_ticket import write
from tests.ticketsample import PHASES

FIXTURE = os.path.join(ROOT, "tests", "fixtures", "templates-json.json")
# `--templates --json` の答えの形の版。
JSON_VERSION = 1

FLOW = """\
    id: wf-impl
    name: 実装の手順
    version: 1.0.0
    nodes:
      - {id: s, type: start, name: 開始, position: {x: 0, y: 0}, data: {}}
      - {id: p, type: prompt, name: 実装, position: {x: 1, y: 0}, data: {prompt: 先にテスト}}
      - {id: e, type: end, name: 終了, position: {x: 2, y: 0}, data: {}}
    connections:
      - {id: c1, from: s, to: p, fromPort: output, toPort: input}
      - {id: c2, from: p, to: e, fromPort: output, toPort: input}
"""

FEATURE = (
    """\
name: feature
description: 機能を設計から実装まで進める
when: 新しい機能を足すとき
plan:
  - design
  - {type: implement, after: [1]}
feedback:
  - implement-feedback
flows:
  2:
"""
    + FLOW
)

RESEARCH = """\
name: research
description: 調べてから設計する
plan:
  - research
  - {type: design, after: [1]}
"""


def template(name: str, plan: str, description: str = "試し", extra: str = "") -> str:
    """`plan` は `plan:` の下の行（2 字下げ済み）。"""
    return f"name: {name}\ndescription: {description}\n{extra}plan:\n{plan}"


class Harness(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ccnavi-templates-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        write(common_path(self.root, "phases"), PHASES)

    def common(self, name: str, text: str) -> str:
        return write(self.path("common", name), text)

    def own(self, name: str, text: str) -> str:
        return write(self.path("self", name), text)

    def path(self, layer: str, name: str) -> str:
        if layer == "common":
            base = os.path.dirname(common_path(self.root, "rules"))
        elif layer == "self":
            base = os.path.dirname(config_path(self.root, "rules"))
        else:
            base = os.path.join(self.root, "projects", layer, ".ccnavi", "config", "project")
            os.makedirs(os.path.join(self.root, "projects", layer, ".git"), exist_ok=True)
        return os.path.join(base, "templates", name + ".yml")

    def ccnavi(self, *args: str):
        return run_ccnavi(["--root", self.root, *args])

    def answer(self, *args: str) -> dict:
        result = self.ccnavi("--templates", "--json", *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def entry(self, layer: str, name: str, **opts) -> dict:
        for row in self.answer(**opts)["templates"] if opts else self.answer()["templates"]:
            if row["name"] == name and row["layer"] == layer:
                return row
        self.fail(f"{layer}/{name} が答えに無い")

    def problems(self, row: dict, severity: str) -> list[str]:
        return [p["detail"] for p in row["problems"] if p["severity"] == severity]


class LayerTest(Harness):
    def test_no_templates_is_normal(self):
        answer = self.answer()
        self.assertEqual(answer["templates"], [])
        self.assertFalse(answer["layers"][0]["exists"])

    def test_linked_files_and_a_linked_place_are_not_read(self):
        place = self.path("self", "research")
        directory = os.path.dirname(place)
        os.makedirs(directory)
        outside = write(os.path.join(self.root, "outside.yml"), RESEARCH)
        if can_symlink():
            os.symlink(outside, place)
            row = self.entry("self", "research")
            self.assertFalse(row["valid"])
            self.assertIn("シンボリックリンク", self.problems(row, "error")[0])
            os.remove(place)
            write(os.path.join(self.root, "elsewhere", "research.yml"), RESEARCH)
            os.rmdir(directory)
            os.symlink(os.path.join(self.root, "elsewhere"), directory)
            layer = self.answer()["layers"][1]
            self.assertEqual(layer["templates"], [])
            self.assertIn("シンボリックリンク", layer["unreadable"])
            os.remove(directory)
            os.makedirs(directory)
        try:
            os.link(outside, place)
        except OSError:
            return
        row = self.entry("self", "research")
        self.assertFalse(row["valid"])
        self.assertIn("ハードリンク", self.problems(row, "error")[0])


class CheckTest(Harness):
    def test_a_review_false_is_allowed_and_a_changed_definition_only_warns(self):
        self.own("t", template("t", "  - {type: design, review: false}\n"))
        self.assertEqual(self.problems(self.entry("self", "t"), "error"), [])
        # 型が phases.yml から消えても、読むのも一覧も止まらない（使うときの承認で error になる）。
        self.own("feature", FEATURE)
        write(common_path(self.root, "phases"), PHASES.replace("  design:", "  design2:"))
        row = self.entry("self", "feature")
        self.assertEqual(self.problems(row, "error"), [])
        self.assertTrue(self.problems(row, "warn"))


class CliTest(Harness):
    def setUp(self):
        super().setUp()
        self.common("research", RESEARCH)
        self.own("feature", FEATURE)

    def test_the_text_outputs(self):
        out = self.ccnavi("--templates")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("research  [common]  調べてから設計する", out.stdout)
        self.assertIn("feature  [self]  機能を設計から実装まで進める", out.stdout)
        self.assertIn("いつ: 新しい機能を足すとき", out.stdout)
        # 複数の --keyword は和。ヒットしないときの文。
        both = self.answer("--keyword", "調べ", "--keyword", "feat")["templates"]
        self.assertEqual(sorted(r["name"] for r in both), ["feature", "research"])
        text = self.ccnavi("--templates", "--keyword", "無い語")
        self.assertIn("該当するテンプレートはありません。", text.stdout)
        # --show は中身を出す。
        out = self.ccnavi("--templates", "--show", "feature")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("テンプレート: feature（self）", out.stdout)
        self.assertIn("2. implement（after 1）", out.stdout)
        self.assertIn("name: feature", out.stdout)
        shown = json.loads(self.ccnavi("--templates", "--show", "feature", "--json").stdout)
        self.assertEqual(shown["template"]["name"], "feature")
        self.assertEqual(shown["template"]["text"], FEATURE)
        self.assertEqual(shown["template"]["sha"], hashlib.sha256(FEATURE.encode()).hexdigest())
        # 使わない理由を言う。統合結果の相手でないレイヤーの置き場の苦情は混ぜない。
        self.common("broken", "name: [\n")
        write(os.path.join(os.path.dirname(self.path("self", "x")), "notes.txt"), "x")
        write(self.path("lib", "lib"), template("lib", "  - design\n"))
        out = self.ccnavi("--templates").stdout
        self.assertIn("broken  [common]（使わない: invalid）  YAML として読めない", out)
        self.assertIn("notes.txt", out)
        other = self.ccnavi("--templates", "--layer", "lib").stdout
        self.assertNotIn("notes.txt", other)
        self.assertIn("lib  [lib]", other)
        # 書かずに検査する --check-file のテキスト。
        path = write(os.path.join(self.root, "tmp-check", "feature.yml"), FEATURE)
        out = self.ccnavi("--templates", "--check-file", path)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("読めた: はい", out.stdout)

    def test_failures_and_wrong_combinations_are_refused(self):
        out = self.ccnavi("--templates", "--show", "nothing")
        self.assertEqual(out.returncode, 1)
        self.assertIn("nothing", out.stderr)
        for args in (
            ["--templates", "--show", "nothing", "--json"],
            ["--templates", "--layer", "nowhere", "--json"],
            ["--templates", "--show", "feature", "--layer", "nowhere", "--json"],
        ):
            with self.subTest(args=args):
                out = self.ccnavi(*args)
                self.assertEqual(out.returncode, 1)
                answer = json.loads(out.stdout)
                self.assertFalse(answer["ok"])
                self.assertTrue(answer["error"])
                self.assertEqual(answer["version"], JSON_VERSION)
                self.assertEqual(out.stderr, "")
        for args, needle in (
            (["--show", "feature"], "--templates"),
            (["--layer", "self"], "--templates"),
            (["--templates", "--docs"], "--docs"),
            (["--templates", "--type", "adr"], "--docs でだけ"),
            (["--templates", "--lint"], "--lint"),
            (["--templates", "--show", "a", "--keyword", "b"], "--keyword"),
            (["--templates", "ticket"], "フラグ以外の語"),
            (["--templates", "--show", " "], "名前"),
            (["--lint", "--keyword", "x"], "--docs でだけ"),
        ):
            out = self.ccnavi(*args)
            self.assertEqual(out.returncode, 1, args)
            self.assertIn(needle, out.stderr, args)
            self.assertEqual(out.stdout, "", args)
        path = write(os.path.join(self.root, "tmp-check", "feature.yml"), FEATURE)
        for extra in (["--show", "x"], ["--draft", "p"], ["--keyword", "k"]):
            out = self.ccnavi("--templates", "--check-file", path, *extra)
            self.assertNotEqual(out.returncode, 0, extra)
            self.assertIn("--check-file", out.stderr)
        alone = self.ccnavi("--check-file", path)
        self.assertNotEqual(alone.returncode, 0)
        self.assertIn("--templates と一緒に使う", alone.stderr)
        result = self.ccnavi("--templates", "--check-file", path, "--layer", "nothing", "--json")
        self.assertEqual(result.returncode, 1)
        self.assertFalse(json.loads(result.stdout)["ok"])


class LintTest(Harness):
    def lint(self):
        result = self.ccnavi("--lint", "--json", "--mode", "enable")
        return [p for p in json.loads(result.stdout)["problems"] if "templates" in p["where"]]

    def test_problems_are_reported_with_their_layer(self):
        self.assertEqual(self.lint(), [])
        self.common("research", RESEARCH)
        self.own("feature", FEATURE)
        self.assertEqual(self.lint(), [])
        self.own("research", RESEARCH.replace("調べてから", "調べずに"))
        self.own("ends", template("ends", "  - design\n  - acceptance\n"))
        self.own("unknown", template("unknown", "  - docs\n"))
        write(self.path("lib", "broken"), "name: [\n")
        found = {(p["where"], p["severity"]) for p in self.lint()}
        self.assertEqual(
            found,
            {
                ("(templates/self)", "error"),
                ("(templates/self)", "warn"),
                ("(templates/lib)", "error"),
            },
        )
        details = " ".join(p["detail"] for p in self.lint())
        self.assertIn("research: ", details)
        self.assertIn("ends: ", details)
        result = self.ccnavi("--lint", "--mode", "enable")
        self.assertEqual(result.returncode, 1)
        self.assertTrue(any("templates/self" in line for line in result.stdout.splitlines()))


def showcase(root: str) -> None:
    """答えの形の見本にするワークスペース。統合結果・衝突・壊れたファイル・検査の苦情が一通り出る。"""
    write(common_path(root, "phases"), PHASES)
    base = os.path.dirname(common_path(root, "rules")) + "/templates/"
    own = os.path.dirname(config_path(root, "rules")) + "/templates/"
    write(base + "feature.yml", FEATURE)
    write(base + "research.yml", RESEARCH)
    write(base + "broken.yml", "name: [\n")
    write(own + "feature.yml", FEATURE)
    write(own + "research.yml", RESEARCH.replace("調べてから", "調べずに"))
    write(own + "local.yml", template("local", "  - docs\n", "このプロジェクトだけの作業"))
    write(own + "ends.yml", template("ends", "  - design\n  - acceptance\n", "終端が 2 つ"))
    write(own + "notes.txt", "x")
    os.makedirs(os.path.join(root, "projects", "lib", ".git"), exist_ok=True)
    write(
        os.path.join(root, "projects", "lib", ".ccnavi/config/project/templates/lib.yml"),
        template("lib", "  - research\n", "lib の調べもの"),
    )
    # 下書き（効力は無い）。TODO の残り・同名の既存・読めないもの。
    drafts = os.path.join(root, "wip", "handoff", ".ccnavi", "config", "project", "templates") + "/"
    write(
        drafts + "login.yml",
        FEATURE.replace("feature", "login").replace("機能を設計から", "TODO ログインを設計から"),
    )
    write(drafts + "research.yml", RESEARCH)
    write(drafts + "broken.yml", "name: [\n")
    # 同じ中身の下書きがもう 1 つのツリー（lib）にもある（also_in に値が入る）。
    write(
        os.path.join(
            root, "projects", "lib", "wip", "handoff", ".ccnavi/config/project/templates/login.yml"
        ),
        FEATURE.replace("feature", "login").replace("機能を設計から", "TODO ログインを設計から"),
    )


class CheckFileTest(Harness):
    """`--templates --check-file <パス>`。任意のパスのテンプレートを検査する（書かない）。"""

    def check(self, text: str, name: str = "feature", *args: str) -> dict:
        path = write(os.path.join(self.root, "tmp-check", name + ".yml"), text)
        result = self.ccnavi("--templates", "--check-file", path, "--json", *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_a_plan_only_template_has_one_graph_and_unreadable_yaml_is_invalid(self):
        got = self.check(RESEARCH, "research")
        self.assertEqual([p["part"] for p in got["plans"]], ["plan"])
        self.assertFalse(got["plans"][0]["items"][0]["review"])
        got = self.check("name: [\n")
        self.assertFalse(got["valid"])
        self.assertIsNone(got["data"])
        self.assertEqual(got["plans"], [])
        self.assertTrue(self.problems(got, "error"))

    def test_the_answer_matches_the_fixture(self):
        got = self.check(FEATURE)
        got["sha"] = "<sha>"
        for plan in got["plans"]:
            plan["source_sha"] = "<sha>"
        fixture_path = os.path.join(ROOT, "tests", "fixtures", "templates-check-json.json")
        with open(fixture_path, encoding="utf-8") as f:
            fixture = json.load(f)
        self.assertEqual(got, fixture["expect"])
        self.assertTrue(fixture["about"])


class ShapeTest(unittest.TestCase):
    """`--templates --json` の答えの形。拡張と共有する見本（templates-json.json）。"""

    def test_the_answer_matches_the_fixture(self):
        root = tempfile.mkdtemp(prefix="ccnavi-templates-shape-")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        showcase(root)
        result = run_ccnavi(["--root", root, "--templates", "--json"])
        self.assertEqual(result.returncode, 0, result.stderr)
        answer = json.loads(result.stdout.replace(json.dumps(root)[1:-1], "<root>"))
        with open(FIXTURE, encoding="utf-8") as f:
            fixture = json.load(f)
        self.assertEqual(self.plain(answer), self.plain(fixture["expect"]))
        self.assertTrue(fixture["about"])

    @classmethod
    def plain(cls, value):
        """YAML ライブラリの版で変わる、読めない理由の文言を外す。"""
        if isinstance(value, str):
            return re.sub(r"(YAML として読めない) \(.*\)", r"\1 (...)", value)
        if isinstance(value, list):
            return [cls.plain(v) for v in value]
        if isinstance(value, dict):
            return {k: cls.plain(v) for k, v in value.items()}
        return value


if __name__ == "__main__":
    unittest.main()
