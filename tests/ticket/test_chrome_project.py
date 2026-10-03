"""Chrome の入口のプロジェクトのリポジトリと「始める」（ADR-0093 の段階 5）の受入テスト。

見るのは 5 つ。

1. プロジェクトのリポジトリ（手元で `projects/<名前>` に clone されるもの。3.3 の 7）の家族を、
   手元と同じ形の仮のツリー（ワークスペースルート + `projects/<名前>` + そのワークツリー）で判定し、
   承認で書くもの（Changes）はその家族の親のブランチだけ
2. プロジェクトの層は D28 の計算（プロジェクトの統合先の層に、ワークスペースの共通層を
   `configsync.projected` で写したもの）。親のブランチの上の層は読まない
3. 控えはワークスペース（`sync/self/`）とプロジェクト（`sync/<名前>/`）に分けて組む
4. 「始める」（8.6）: issue の番号から識別子（`i0012`・`web-i0012`）を決め、統合先の
   `done/` にある・同じ名前のブランチがある・開いた家族に同じ識別子がある・互換の版が違う、
   のどれでも始められない
5. プロジェクト名が予約の名前（`common`・`self`）や識別子の形でなければ受けない
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from ccnavi import lint, version
from tests.ticket.test_core import STAMP, _chrome
from tests.ticket.test_phases import PHASES, child_text, parent_text

HEAD = "0" * 40
COMPAT = lint.SH_COMPAT_FILE.replace(os.sep, "/")
ACTOR = {"account": "lab-approver", "version": "9.9.9"}

RISK_COMMON = """version: 1
factors:
  - id: common-script
    title: 共通層のスクリプトの変更
    script: .ccnavi/common/scripts/risk.sh
    points: 1
"""


def workspace(compat=version.COMPAT, **extra):
    files = {
        ".claude/settings.json": json.dumps({"env": {"CCNAVI_TICKET_CONTROL": "enable"}}) + "\n",
        ".ccnavi/common/phases.yml": PHASES,
        COMPAT: f"#!/bin/sh\nCCNAVI_COMPAT={compat}\n",
    }
    files.update(extra)
    return {
        "integration": {"name": "main", "source": "default", "head": HEAD},
        "files": files,
        "binary": [],
        "links": [],
    }


def project_integration(**extra):
    files = {".ccnavi/config/rules.yml": '{"version": 1, "deny": []}\n'}
    files.update(extra)
    return {"head": HEAD, "files": files, "binary": []}


def family_files(ident="web-i0012", issue=12):
    return {
        f"wip/proposals/todo/{ident}.md": parent_text(ident, ["research"], issue=issue),
        f"wip/proposals/todo/{ident}-01.md": child_text(
            f"{ident}-01", ident, 1, ["wip/research/*"], False
        ),
        # 親のブランチの上の層は読まない（置き場の外なので拡張はそもそも読まない。3.3 の 6）
    }


class ChromeProjectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ccnavi-chrome-project-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.chrome = _chrome()

    def ask(self, op, family="web-i0012", branches=None, project="web", ws=None, **extra):
        snapshot = {
            "integration": {"name": "trunk", "source": "setting", "head": HEAD},
            "branches": {
                "trunk": project_integration(),
                **(
                    branches
                    if branches is not None
                    else {family: {"head": HEAD, "files": family_files()}}
                ),
            },
            "absent": [],
            "project": project,
            "workspace": ws or workspace(),
        }
        request = {
            "schema": self.chrome.SCHEMA,
            "op": op,
            "family": family,
            "stamp": STAMP,
            "settings": snapshot["workspace"]["files"][".claude/settings.json"],
            "snapshot": snapshot,
            **extra,
        }
        return json.loads(self.chrome.handle(json.dumps(request), os.path.join(self.tmp, "memfs")))

    def test_the_board_of_a_project_family_lists_its_proposals(self):
        board = self.ask("board")
        self.assertNotIn("error", board, board)
        self.assertEqual([e["ticket"] for e in board["batch"]], ["web-i0012", "web-i0012-01"])
        self.assertTrue(board["write"]["allowed"], board["write"])
        self.assertEqual(board["rejected"], [])
        # 画面の本文はプロジェクトの置き場を名指しする（仮のツリーの綴りは出さない）
        self.assertNotIn(os.path.join(self.tmp, "memfs"), board["text"])

    def test_approving_a_project_family_writes_only_its_branch(self):
        board = self.ask("board")
        shown = {"ids": [e["ticket"] for e in board["batch"]], "digest": board["digest"]}
        body = self.ask("plan", only=board["only"], shown=shown, actor=ACTOR)
        self.assertNotIn("error", body, body)
        self.assertIsNone(body["mismatch"])
        self.assertEqual(list(body["changes"]), ["web-i0012"])
        paths = {r["path"]: r for r in body["changes"]["web-i0012"]}
        self.assertEqual(paths["wip/proposals/todo/web-i0012.md"]["op"], "delete")
        copy = paths[".ccnavi/approved/doing/web-i0012.md"]["content"]
        self.assertIn("source_tree: web-i0012", copy)
        self.assertIn("source_path: wip/proposals/todo/web-i0012.md", copy)
        events = paths[".ccnavi/approved/events/web-i0012.ndjson"]["content"]
        self.assertIn('"via": "chrome"', events)
        self.assertIn('"actor": "lab-approver"', events)

    def test_the_project_layer_is_computed_from_the_integration_branches(self):
        place = self.chrome._placement(None)
        snap = {
            "integration": {"name": "trunk"},
            "branches": {
                "trunk": project_integration(
                    **{
                        ".ccnavi/config/phases.yml": "version: 1\nphases: {}\n",
                        ".ccnavi/config/risks.yml": "version: 1\nfactors: []\n",
                    }
                )
            },
            "project": "web",
            "workspace": workspace(**{".ccnavi/common/risks.yml": RISK_COMMON}),
        }
        layer = self.chrome.project_layer(snap, place)
        # 共通層にあるファイルは写し（配点の script はプロジェクトの層の置き場へ）、無いものは残す
        self.assertEqual(layer[".ccnavi/config/phases.yml"], PHASES)
        self.assertIn("script: .ccnavi/scripts/risk.sh", layer[".ccnavi/config/risks.yml"])
        self.assertEqual(layer[".ccnavi/config/rules.yml"], '{"version": 1, "deny": []}\n')

    def test_records_are_kept_apart_for_the_workspace_and_the_project(self):
        place = self.chrome._placement(None)
        snap = {
            "integration": {"name": "trunk", "source": "setting", "head": HEAD},
            "branches": {
                "trunk": project_integration(
                    **{".ccnavi/approved/done/web-i0001.md": "x\n", "README.md": "r\n"}
                ),
                "web-i0012": {"head": HEAD, "files": family_files()},
            },
            "absent": ["web-i0009"],
            "project": "web",
            "workspace": workspace(),
        }
        records = self.chrome.records(snap, place, ["web-i0012", "web-i0009"])
        self.assertIn("branch trunk", records["sync/web/integration/head"])
        self.assertIn("branch main", records["sync/self/integration/head"])
        self.assertIn("sync/web/integration/.ccnavi/approved/done/web-i0001.md", records)
        self.assertIn("sync/self/integration/.ccnavi/common/phases.yml", records)
        self.assertNotIn("sync/web/integration/README.md", records)
        self.assertIn("state present", records["sync/web/families/web-i0012"])
        self.assertIn("state gone", records["sync/web/families/web-i0009"])
        self.assertFalse(any(k.startswith("sync/self/families/") for k in records))

    def test_a_reserved_or_malformed_project_name_is_refused(self):
        for name in ("common", "Self", "../x", "a b"):
            with self.subTest(name=name):
                self.assertIn("error", self.ask("board", project=name))

    def test_a_project_repository_needs_the_workspace(self):
        request = {
            "schema": self.chrome.SCHEMA,
            "op": "board",
            "family": "web-i0012",
            "snapshot": {
                "integration": {"name": "trunk", "source": "setting", "head": HEAD},
                "branches": {"trunk": project_integration()},
                "absent": [],
                "project": "web",
            },
        }
        body = json.loads(self.chrome.handle(json.dumps(request), os.path.join(self.tmp, "m")))
        self.assertIn("ワークスペースの統合先", body["error"])

    def test_a_different_compat_in_the_workspace_refuses_writing(self):
        board = self.ask("board", ws=workspace(compat=version.COMPAT + 1))
        self.assertFalse(board["write"]["allowed"])
        self.assertIn("拡張を更新する", board["write"]["reason"])


class StartTest(unittest.TestCase):
    """「始める」（8.6）。識別子は ticket.issue_identifier の 1 つだけから決める（3.1 の 11）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ccnavi-chrome-start-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.chrome = _chrome()

    def start(self, issue, project="", branches=None, taken=(), compat=version.COMPAT, done=()):
        integ = {
            ".claude/settings.json": "{}\n",
            COMPAT: f"#!/bin/sh\nCCNAVI_COMPAT={compat}\n",
            **{f".ccnavi/approved/done/{d}.md": parent_text(d, ["research"]) for d in done},
        }
        snapshot = {
            "integration": {"name": "main", "source": "default", "head": HEAD},
            "branches": {"main": {"head": HEAD, "files": integ}, **(branches or {})},
            "absent": [],
        }
        if project:
            snapshot["project"] = project
            snapshot["workspace"] = workspace(compat=compat)
        request = {
            "schema": self.chrome.SCHEMA,
            "op": "start",
            "snapshot": snapshot,
            "issue": issue,
            "taken": list(taken),
        }
        return json.loads(self.chrome.handle(json.dumps(request), os.path.join(self.tmp, "m")))

    def test_the_identifier_comes_from_the_issue(self):
        self.assertEqual(
            self.start(12),
            {"identifier": "i0012", "integration": "main", "problems": [], "schema": 1},
        )
        self.assertEqual(self.start(12345)["identifier"], "i12345")
        body = self.start(12, project="web")
        self.assertEqual((body["identifier"], body["problems"]), ("web-i0012", []))

    def test_a_closed_identifier_is_refused(self):
        body = self.start(55, done=["i0055"])
        self.assertTrue(any("done/ で閉じている" in p for p in body["problems"]), body)
        self.assertIn("フォールバック", body["problems"][-1])
        body = self.start(55, done=["I0055"])
        self.assertTrue(any("done/ で閉じている" in p for p in body["problems"]), body)

    def test_an_existing_branch_or_open_family_is_refused(self):
        body = self.start(12, taken=["I0012"])
        self.assertTrue(any("同じ名前のブランチ" in p for p in body["problems"]), body)
        other = {
            "topic": {
                "head": HEAD,
                "files": {
                    "wip/proposals/todo/i0012-01.md": child_text("i0012-01", "i0012", 1, ["src/*"])
                },
            }
        }
        body = self.start(12, branches=other)
        self.assertTrue(any("開いた家族 topic" in p for p in body["problems"]), body)

    def test_a_different_compat_is_refused(self):
        body = self.start(12, compat=version.COMPAT + 1)
        self.assertTrue(any("互換" in p for p in body["problems"]), body)

    def test_bad_numbers_are_refused(self):
        for bad in (0, -3, True, "12", None):
            with self.subTest(bad=bad):
                self.assertIn("error", self.start(bad))


if __name__ == "__main__":
    unittest.main()
