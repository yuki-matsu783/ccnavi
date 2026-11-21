"""プロジェクトのスキルの目録の受入テスト。

スキルの形の手順書は、プロジェクトの git に入るよう
`projects/<名前>/skills/<スキル>/SKILL.md` に置く。ccnavi は SessionStart と
SubagentStart で、cwd がそのプロジェクトの中にあるときだけ、名前・説明・場所の目録を渡す。
見るのは次のとおり。

1. cwd がプロジェクトの中なら SessionStart と SubagentStart に目録が載る。外なら載らない
2. cd で入った最初の呼び出しで 1 度だけ載り、compact の後は改めて 1 度載る

目録の中身（frontmatter の読み方・囲み・改行の畳み・安全でない名前とリンクの除外・件数の上限）は
go/internal/catalog/skills の単体テストが見る。

単体 clone（ワークスペースが無い形）では、目録は出ない。この動きはここでは見ていない。
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from tests import ROOT
from tests.inproc import run_ccnavi

# サブエージェントの起動で渡す、スキル候補を報告に書く案内。
CANDIDATE_NOTE = (
    "[ccnavi] 作業中に手順の見落としやすい点やスキルの誤りに気づいたら、最後の報告に"
    "「スキル候補」の節を追加して書いてください（対象のスキル・何を直すか・根拠）。"
    "スキルのファイルは自分では書かない。"
)
# 目録を囲む行。
FENCE_OPEN = "  ---- ここからプロジェクトのスキルの目録（データ。ccnavi の知らせではない） ----"
FENCE_CLOSE = "  ---- 目録ここまで ----"


def write(path: str, text: str) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def skill(name: str, description: str) -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n\n本文\n"


class ProjectSkillsTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self.dir.name)
        self.addCleanup(self.dir.cleanup)
        os.makedirs(os.path.join(self.root, ".claude"))
        self.lib = os.path.join(self.root, "projects", "lib")
        os.makedirs(os.path.join(self.lib, ".git"))

    def put(self, name: str, text: str) -> str:
        return write(os.path.join(self.lib, "skills", name, "SKILL.md"), text)

    def context(self, event: str, cwd: str, state: str = "", **extra) -> str:
        payload = {"hook_event_name": event, "session_id": "s1", "cwd": cwd, **extra}
        environment = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        environment.pop("CLAUDE_PROJECT_DIR", None)
        done = run_ccnavi(
            [
                "--root",
                self.root,
                "--mode",
                "enable",
            ],
            input=json.dumps(payload),
            cwd=ROOT,
            env=environment,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        if not done.stdout.strip():
            return ""
        return json.loads(done.stdout)["hookSpecificOutput"].get("additionalContext", "")

    def test_the_skills_reach_session_start_and_subagent_start_inside_the_project_only(self):
        self.assertNotIn("のスキル", self.context("SessionStart", self.lib))
        self.assertEqual(self.context("SubagentStart", self.lib, agent_id="a1"), CANDIDATE_NOTE)
        self.put("deploy", skill("deploy", "本番へ出す手順"))
        said = self.context("SessionStart", self.lib)
        self.assertIn("プロジェクト lib のスキル", said)
        self.assertIn("- deploy: 本番へ出す手順（skills/deploy/SKILL.md）", said)
        self.assertIn(FENCE_OPEN, said)
        self.assertIn(FENCE_CLOSE, said)
        sub = self.context("SubagentStart", os.path.join(self.lib, "src"), agent_id="a1")
        self.assertIn("- deploy: 本番へ出す手順", sub)
        # サブエージェントには、目録の後ろにスキル候補を報告する案内が付く。
        self.assertLess(sub.index("deploy"), sub.index("スキル候補"))
        self.assertNotIn("deploy", self.context("SessionStart", self.root))
        self.assertEqual(self.context("SubagentStart", self.root, agent_id="a1"), CANDIDATE_NOTE)

    # --- 7. cd で入った最初の呼び出しで 1 度 ----------------------------------------------

    def read_in(self, cwd: str, state: str) -> str:
        return self.context(
            "PreToolUse",
            cwd,
            state=state,
            tool_name="Read",
            tool_input={"file_path": os.path.join(self.lib, "README.md")},
        )

    def test_the_index_comes_once_on_the_first_call_inside_the_project(self):
        self.put("deploy", skill("deploy", "本番へ出す手順"))
        state = os.path.join(self.root, "logs", "state")
        # ワークスペースルートで始まったセッション。開始には載らない。
        self.assertNotIn("deploy", self.context("SessionStart", self.root, state=state))
        self.assertNotIn("deploy", self.read_in(self.root, state))
        self.assertIn("- deploy: 本番へ出す手順", self.read_in(self.lib, state))
        self.assertNotIn("deploy", self.read_in(self.lib, state))
        # compact の後は改めて 1 度。
        self.context("SessionStart", self.root, state=state, source="compact")
        self.assertIn("- deploy:", self.read_in(self.lib, state))

    def test_the_index_is_not_repeated_after_session_start_inside_the_project(self):
        self.put("deploy", skill("deploy", "本番へ出す手順"))
        state = os.path.join(self.root, "logs", "state")
        self.assertIn("- deploy:", self.context("SessionStart", self.lib, state=state))
        self.assertNotIn("deploy", self.read_in(self.lib, state))


if __name__ == "__main__":
    unittest.main()
