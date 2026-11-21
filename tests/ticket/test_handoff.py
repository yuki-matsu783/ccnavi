"""保護対象の下書きの取り込み（`ccnavi user handoff`。go/internal/tickets/sync/handoff）。

取り込みの判断（取り込み先の表・断る形・`--expect` のハッシュ・差分の見え方・テンプレートと
`defense` の検査・防御の記述を動かす変更）は、go の handoff の見本（`golden_test.go`・
`parts_test.go`・`handoff_test.go`）が見る。ここで見るのは、見本が持たない次の 3 つ。

1. 入口: 組み合わせの拒否と終了コード、見る JSON の形（拡張と共有）、テキストの案内、
   書いたら下書きを消し、取り込み先の綴りを保つ
2. 経路: 書くにはターミナルか `--via board`。`defense` と、防御の記述を動かす rules.yml、
   読めない rules.yml の修復はボードから断る
3. 同じ回に取り込む対象を、取り込んだあとの状態で検査する（テンプレートと phases.yml、
   共通と self の同名テンプレート）
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import textwrap
import unittest

from tests import ROOT, can_case_sensitive, common_path, config_path
from tests.inproc import run_ccnavi
from tests.ticket.test_ticket import write
from tests.ticketsample import PHASES, RULES

DOCS = ".ccnavi/docs/guide.md"
SCRIPT = ".ccnavi/scripts/ccnavi-x.sh"
SETTINGS = ".claude/settings.json"
# 下書きの置き場で読み替える階層名（`.ccnavi/scripts/ccnavi-handoff-names.txt` の表）。
DRAFT_NAMES = {".claude": "handoff-.claude"}

TEMPLATE = textwrap.dedent(
    """\
    name: feature
    description: 機能を設計から実装まで進める
    when: 新しい機能を足すとき
    plan:
      - design
      - {type: implement, after: [1]}
    """
)


def put(path: str, data: bytes) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return path


def to_draft(rel: str) -> str:
    """取り込み先の相対パスを、下書きの置き場での綴りにする（先頭の階層だけを読み替える）。"""
    head, _, rest = rel.partition("/")
    return "/".join(x for x in (DRAFT_NAMES.get(head, head), rest) if x)


def slurp(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


class HandoffCase(unittest.TestCase):
    """ワークスペースルートの置き場に下書きを置いて、取り込みを動かす道具。"""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ccnavi-handoff-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        write(common_path(self.root, "rules"), json.dumps(RULES))
        write(common_path(self.root, "phases"), PHASES)
        write(self.target(DOCS), "# 手引き\n\n古い文\n")
        write(self.target(SCRIPT), "#!/bin/sh\necho old\n")
        write(self.target(SETTINGS), '{"hooks": {}}\n')

    # ---- 置き場

    def target(self, rel: str) -> str:
        return os.path.join(self.root, *rel.split("/"))

    def draft_path(self, rel: str) -> str:
        return os.path.join(self.root, "wip", "handoff", *to_draft(rel).split("/"))

    def stage(self, rel: str, data: bytes | None = None) -> str:
        """`ccnavi-stage.sh` と同じく、元を下書きにコピーし、下書きを `data` にする。"""
        current = slurp(self.target(rel)) if os.path.exists(self.target(rel)) else None
        path = self.draft_path(rel)
        put(path, current if data is None else data)
        return path

    # ---- 起動

    def run_handoff(self, *args, guard="disable", env=None):
        environment = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        environment.pop("CLAUDE_PROJECT_DIR", None)
        environment["CCNAVI_GUARD_TICKET_APPROVAL"] = guard
        environment.update(env or {})
        # ボードの経路（`--via board`）は拡張の子プロセスが呼ぶ形なので、印も board にする。
        entry = "board" if "board" in args else "sh"
        return run_ccnavi(
            ["--root", self.root, "--entry", entry, "user", "handoff", *args],
            env=environment,
            cwd=ROOT,
        )

    def view(self, *paths) -> dict:
        result = self.run_handoff(*paths, "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def entry(self, answer: dict, rel: str) -> dict:
        found = [e for e in answer["entries"] if e["path"] == rel]
        self.assertEqual(len(found), 1, answer)
        return found[0]

    def take(self, *paths, expect=None, extra=()):
        if expect is None:
            expect = self.view(*paths)["expect"]
        return self.run_handoff(*paths, "--yes", "--expect", expect, *extra, "--json")

    def board(self, expect: str, *paths):
        return self.run_handoff(
            *paths, "--yes", "--expect", expect, "--via", "board", "--json", guard="enable"
        )


class CliTest(HandoffCase):
    def test_exit_codes_and_refused_combinations(self):
        self.stage(DOCS, b"new\n")
        put(self.draft_path(SCRIPT), b"a\x00\n")
        # 見るだけは、下書きに error があっても 0。
        result = self.run_handoff("--json")
        self.assertEqual(result.returncode, 0)
        self.assertFalse(json.loads(result.stdout)["ok"])
        for args in (
            (self.target(DOCS), "--json"),
            ("../x.md", "--json"),
            ("--yes", "--json"),
            ("--yes", "--expect", "x", "--via", "terminal", "--json"),
            ("--lint",),
            ("--docs",),
            ("--yes", "i1"),
            ("--templates",),
        ):
            with self.subTest(args=args):
                result = self.run_handoff(*args)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        for args in (
            ("--yes", "--via", "board", "--json"),
            ("--yes", "--expect", "x", "--via", "board"),
            ("--expect", "x", "--via", "board", "--json"),
        ):
            with self.subTest(args=args):
                result = self.run_handoff(*args, guard="enable")
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(slurp(self.target(DOCS)), "# 手引き\n\n古い文\n".encode())
        outside = run_ccnavi(["--root", self.root, "--handoff"], cwd=ROOT)
        self.assertEqual(outside.returncode, 2)
        self.assertIn("ccnavi-user-handoff.sh", outside.stderr)

    def test_the_view_json_has_the_agreed_shape(self):
        self.stage(DOCS, b"new\n")
        answer = self.view()
        self.assertEqual(
            sorted(answer),
            ["entries", "expect", "ignored", "ignored_paths", "ok", "tree", "version"],
        )
        self.assertEqual(answer["version"], 1)
        self.assertEqual(answer["tree"], self.root)
        self.assertEqual(
            sorted(answer["entries"][0]),
            sorted(
                [
                    "path",
                    "draft",
                    "kind",
                    "state",
                    "board",
                    "selected",
                    "errors",
                    "warnings",
                    "diff",
                    "entry_sha",
                ]
            ),
        )

    def test_the_text_answer_shows_the_diff_and_the_command(self):
        self.stage(DOCS, "# 手引き\n\n新しい文\n".encode())
        result = self.run_handoff()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("-古い文", result.stdout)
        self.assertIn("+新しい文", result.stdout)
        expect = self.view()["expect"]
        self.assertIn(f"ccnavi-user-handoff.sh --yes --expect {expect}", result.stdout)
        self.assertIn("何も反映していません", result.stdout)
        self.assertNotIn("次:", result.stdout)
        # 名指しした下書きのパスは引用して案内する。空白を含むパスを貼っても、同じパスを指す。
        rel = ".ccnavi/docs/my guide.md"
        write(self.target(rel), "# 手引き\n")
        self.stage(rel, "# 手引き\n\n足した\n".encode())
        result = self.run_handoff(rel)
        self.assertEqual(result.returncode, 0, result.stderr)
        expect = self.view(rel)["expect"]
        self.assertIn(f"ccnavi-user-handoff.sh '{rel}' --yes --expect {expect}", result.stdout)


class WriteTest(HandoffCase):
    def test_yes_writes_cleans_the_draft_and_a_new_template_is_listed(self):
        draft = self.stage(DOCS, b"new\n")
        rel = ".ccnavi/config/project/templates/feature.yml"
        write(self.draft_path(rel), TEMPLATE)
        result = self.take()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "version": 1,
                "ok": True,
                "applied": [{"path": rel, "kind": "template"}, {"path": DOCS, "kind": "docs"}],
                "removed_drafts": [f"wip/handoff/{rel}", f"wip/handoff/{DOCS}"],
                "kept_drafts": [],
            },
        )
        self.assertEqual(slurp(self.target(DOCS)), b"new\n")
        self.assertEqual(slurp(self.target(rel)), TEMPLATE.encode())
        self.assertFalse(os.path.lexists(draft))
        listed = run_ccnavi(["--root", self.root, "--templates", "--json"], cwd=ROOT)
        names = [t["name"] for t in json.loads(listed.stdout)["templates"]]
        self.assertEqual(names, ["feature"])

    def test_the_target_keeps_its_spelling(self):
        write(self.draft_path(".ccnavi/docs/GUIDE.md"), "new\n")
        if not can_case_sensitive():
            self.skipTest("大文字小文字を区別しないファイルシステムでは同じ名前になる")
        self.assertEqual(self.take().returncode, 0)
        self.assertEqual(slurp(self.target(DOCS)), b"new\n")
        self.assertFalse(os.path.exists(self.target(".ccnavi/docs/GUIDE.md")))


class RouteTest(HandoffCase):
    def test_the_terminal_is_needed_to_write_and_the_board_route_skips_it(self):
        self.stage(DOCS, b"new\n")
        self.stage(SCRIPT, b"#!/bin/sh\necho new\n")
        both = self.view()["expect"]
        result = self.run_handoff("--yes", "--expect", both, guard="enable")
        self.assertEqual(result.returncode, 1)
        self.assertIn("ターミナル", result.stderr)
        self.assertEqual(self.run_handoff("--json", guard="enable").returncode, 0)
        # defense はボードから断る。
        result = self.board(both)
        self.assertEqual(result.returncode, 1)
        answer = json.loads(result.stdout)
        self.assertFalse(answer["ok"])
        self.assertIn("defense", answer["error"])
        self.assertIn("ターミナル", answer["error"])
        self.assertEqual(slurp(self.target(SCRIPT)), b"#!/bin/sh\necho old\n")
        self.assertEqual(slurp(self.target(DOCS)), "# 手引き\n\n古い文\n".encode())
        # defense を外して選べば、ボードの経路でターミナル無しに書ける。
        result = self.board(self.view(DOCS)["expect"], DOCS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(slurp(self.target(DOCS)), b"new\n")
        self.assertEqual(slurp(self.target(SCRIPT)), b"#!/bin/sh\necho old\n")


class BrokenRulesRepairTest(HandoffCase):
    """共通の rules.yml が読めない間の修復。

    エージェントは直接直せず（`builtin:guard-ccnavi-config` が Write / Edit を止める）、
    `wip/handoff/` に下書きを置いて、ユーザがターミナルの `ccnavi user handoff` で取り込む。
    今の中身が読めない rules.yml の取り込みは、確かめられないのでターミナルに限る。"""

    RULES_REL = ".ccnavi/config/common/rules.yml"

    def test_the_draft_is_terminal_only_and_the_terminal_takes_it(self):
        broken = b"deny:\n  - id: [unclosed\n<<<<<<< HEAD\n"
        write(common_path(self.root, "rules"), broken.decode())
        fixed = json.dumps(RULES).encode()
        self.stage(self.RULES_REL, fixed)
        answer = self.view()
        row = self.entry(answer, self.RULES_REL)
        self.assertEqual(row["kind"], "layer-config")
        self.assertFalse(row["board"], row)
        result = self.board(answer["expect"])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("ターミナル", json.loads(result.stdout)["error"])
        self.assertEqual(slurp(self.target(self.RULES_REL)), broken)
        result = self.take()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(slurp(self.target(self.RULES_REL)), fixed)
        self.assertFalse(os.path.exists(self.draft_path(self.RULES_REL)))


class BoardDefenseRulesTest(HandoffCase):
    """`rules.yml` の取り込みのうち、防御の記述（`guard-*` と今ある deny）を動かすものは
    ターミナルに限る（どの変更が動かすかは go の handoff の見本が見る）。"""

    RULES_REL = ".ccnavi/config/common/rules.yml"

    def test_removing_a_deny_is_terminal_only(self):
        rules = {
            **RULES,
            "deny": [*RULES["deny"], {**RULES["deny"][0], "id": "keep-out", "glob": "*/x/*"}],
        }
        write(common_path(self.root, "rules"), json.dumps(rules))
        before = slurp(self.target(self.RULES_REL))
        rules["deny"] = rules["deny"][:1]
        self.stage(self.RULES_REL, json.dumps(rules).encode())
        answer = self.view()
        row = self.entry(answer, self.RULES_REL)
        self.assertFalse(row["board"], row)
        self.assertEqual(row["kind"], "layer-config")
        text = self.run_handoff()
        self.assertIn("ターミナルのみ", text.stdout)
        self.assertIn("keep-out", text.stdout)
        result = self.board(answer["expect"])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        error = json.loads(result.stdout)["error"]
        self.assertIn("防御の記述", error)
        self.assertIn("ターミナル", error)
        self.assertEqual(slurp(self.target(self.RULES_REL)), before)
        # ターミナルの前のユーザなら取り込める。
        self.assertEqual(self.take().returncode, 0)


class LayerConfigTest(HandoffCase):
    RULES_REL = ".ccnavi/config/common/rules.yml"
    PHASES_REL = ".ccnavi/config/common/phases.yml"

    def test_broken_layer_files_are_errors(self):
        phases = ".ccnavi/config/project/phases.yml"
        write(config_path(self.root, "phases"), "version: 1\nphases: {}\n")
        self.stage(self.RULES_REL, b"version: 1\ndeny: [\n")
        self.stage(phases, b"version: 1\nphases:\n  design:\n    kind: nope\n")
        answer = self.view()
        row = self.entry(answer, self.RULES_REL)
        self.assertEqual(row["kind"], "layer-config")
        self.assertEqual(row["state"], "rejected")
        self.assertIn("ルールを読めない", " ".join(row["errors"]))
        self.assertEqual(self.entry(answer, phases)["state"], "rejected", answer)

    def test_a_template_taken_with_the_phases_is_checked_against_the_new_phases(self):
        rel = ".ccnavi/config/project/templates/feature.yml"
        text = TEMPLATE.replace("{type: implement, after: [1]}", "{type: brand-new, after: [1]}")
        write(self.draft_path(rel), text)
        alone = self.entry(self.view(rel), rel)
        self.assertIn("brand-new", " ".join(alone["warnings"]))
        new_phases = PHASES + (
            "  brand-new:\n"
            "    kind: work\n"
            "    title: 新しい型\n"
            "    review: true\n"
            '    scope: ["src/*"]\n'
        )
        self.stage(self.PHASES_REL, new_phases.encode())
        together = self.view(rel, self.PHASES_REL)
        self.assertNotIn("brand-new", " ".join(self.entry(together, rel)["warnings"]))
        self.assertTrue(together["ok"], together)


class TemplateTest(HandoffCase):
    REL = ".ccnavi/config/project/templates/feature.yml"
    COMMON = ".ccnavi/config/common/templates/feature.yml"

    def test_the_collision_is_judged_after_the_import(self):
        write(self.target(self.COMMON), TEMPLATE.replace("新しい機能", "別の"))
        write(self.draft_path(self.REL), TEMPLATE)
        errors = " ".join(self.entry(self.view(), self.REL)["errors"])
        self.assertIn("共通に同名で中身の違う", errors)
        # 共通の同名も同じ回に同じ中身へ直すなら、衝突しない。
        write(self.draft_path(self.COMMON), TEMPLATE)
        answer = self.view()
        self.assertTrue(answer["ok"], answer)


if __name__ == "__main__":
    unittest.main()
