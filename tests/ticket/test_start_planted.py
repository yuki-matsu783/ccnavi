"""手で動かした承認に仕込まれた着手の欄。

承認はチケットの中身を変えないので、提案の段階で書いた `base_sha`・`started_at` は手で動かした
承認ではそのまま承認済みチケットに入る。`ticket start` は、既にある `base_sha` がワークツリーの
HEAD の祖先でなければ止める。`--lint` は、着手の欄があるのに状態の履歴に `started` の行が無いものを
warn にする（判定には入れない）。
"""

from __future__ import annotations

import json
import os

from tests.ticket.test_phases import PhaseHarness, child_text
from tests.ticket.test_ticket import git, write


class PlantedBaseTest(PhaseHarness):
    def plant(self, path, fields):
        with open(path, encoding="utf-8") as f:
            text = f.read()
        for key, value in fields.items():
            text = text.replace(f'{key}: ""', f'{key}: "{value}"', 1)
        write(path, text)

    def test_a_base_sha_off_the_worktree_history_stops_start(self):
        self.family(plan=["research"])
        other = self.worktree("elsewhere", "main")
        write(os.path.join(other, "x.txt"), "x\n")
        git(other, "add", "-A")
        git(other, "commit", "--quiet", "-m", "elsewhere")
        stray = git(other, "rev-parse", "HEAD").strip()
        self.plant(os.path.join(self.approved, "doing", "i0001.md"), {"base_sha": stray})
        self.commit_parent("planted")
        started = self.ccnavi("ticket", "start", "i0001")
        self.assertNotEqual(started.returncode, 0, started.stdout)
        self.assertIn("HEAD の祖先でない", started.stderr)
        with open(os.path.join(self.approved, "doing", "i0001.md"), encoding="utf-8") as f:
            self.assertIn('started_at: ""', f.read())

    def test_a_base_sha_on_the_worktree_history_lets_start_through(self):
        self.family(plan=["research"])
        base = git(self.parent_tree, "rev-parse", "HEAD~1").strip()
        self.plant(os.path.join(self.approved, "doing", "i0001.md"), {"base_sha": base})
        self.commit_parent("planted")
        started = self.ccnavi("ticket", "start", "i0001")
        self.assertEqual(started.returncode, 0, started.stderr)

    def lint_says(self, ident):
        lint = json.loads(self.ccnavi("--lint", "--json").stdout)
        return [
            p
            for p in lint["problems"]
            if p["detail"].startswith(f"{ident}: ") and "started" in p["detail"]
        ]

    def test_lint_warns_about_start_fields_without_a_started_row(self):
        self.family(plan=["research"])
        self.start_parent()
        self.commit_parent("start")
        self.assertEqual(self.lint_says("i0001"), [])
        head = git(self.parent_tree, "rev-parse", "HEAD").strip()
        child = child_text("i0001-01", "i0001", 1, ("wip/research/*",), False)
        child = child.replace('started_at: ""', 'started_at: "2026-09-01T00:00:00+0900"')
        child = child.replace('base_sha: ""', f'base_sha: "{head}"')
        write(os.path.join(self.approved, "doing", "i0001-01.md"), child)
        self.commit_parent("moved by hand with start fields")
        said = self.lint_says("i0001-01")
        self.assertEqual([p["severity"] for p in said], ["warn"], said)
