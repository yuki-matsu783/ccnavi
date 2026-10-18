"""手で動かした承認に仕込まれた着手の欄。

承認はチケットの中身を変えないので、提案の段階で書いた `base_sha`・`started_at` は手で動かした
承認ではそのまま承認済みチケットに入る。`--lint` は、着手済みで `base_sha` がワークツリーの HEAD の
祖先でないものを warn にする。`start` では止めない（`base_sha` だけなら判定が `blocked` で止め、
`start` は基準点を HEAD で書き直す）。判定にも入れない。
"""

from __future__ import annotations

import json
import os

from tests.ticket.test_phases import PhaseHarness
from tests.ticket.test_ticket import git, write


class PlantedBaseTest(PhaseHarness):
    def lint_says(self, ident):
        lint = json.loads(self.ccnavi("--lint", "--json").stdout)
        return [
            p
            for p in lint["problems"]
            if p["detail"].startswith(f"{ident}: ") and "started" in p["detail"]
        ]

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
