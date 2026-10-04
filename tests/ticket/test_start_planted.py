"""手で動かした承認に仕込まれた着手の欄。

承認はチケットの中身を変えないので、提案の段階で書いた `base_sha`・`started_at` は手で動かした
承認ではそのまま承認済みチケットに入る。`--lint` は、着手の欄があるのに状態の履歴に `started` の行が
無いものと、着手済みで `base_sha` がワークツリーの HEAD の祖先でないものを warn にする。`start` では
止めない（両方仕込めば「着手済み」で先に返り、`base_sha` だけなら判定が `blocked` で止め、`start` は
基準点を HEAD で書き直す）。判定にも入れない。
"""

from __future__ import annotations

import json
import os

from tests.ticket.test_phases import PhaseHarness, child_text
from tests.ticket.test_ticket import git, write


class PlantedBaseTest(PhaseHarness):
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

    def test_lint_warns_when_the_base_sha_is_off_the_worktree_history(self):
        self.family(plan=["research"])
        self.start_parent()
        self.commit_parent("start")
        said = [p for p in self.lint_all("i0001") if "HEAD の祖先でない" in p["detail"]]
        self.assertEqual(said, [])
        other = self.worktree("elsewhere", "main")
        write(os.path.join(other, "x.txt"), "x\n")
        git(other, "add", "-A")
        git(other, "commit", "--quiet", "-m", "elsewhere")
        stray = git(other, "rev-parse", "HEAD").strip()
        path = os.path.join(self.approved, "doing", "i0001.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        lines = [
            f'base_sha: "{stray}"' if line.startswith("base_sha:") else line
            for line in text.split("\n")
        ]
        write(path, "\n".join(lines))
        self.commit_parent("rewritten base")
        said = [p for p in self.lint_all("i0001") if "HEAD の祖先でない" in p["detail"]]
        # 判定には入れないので warn
        self.assertEqual([p["severity"] for p in said], ["warn"], said)

    def lint_all(self, ident):
        lint = json.loads(self.ccnavi("--lint", "--json").stdout)
        return [p for p in lint["problems"] if p["detail"].startswith(f"{ident}: ")]
