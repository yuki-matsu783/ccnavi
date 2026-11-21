"""ユーザの発言のたびに、共通の工程スキルをプロジェクトの `skills/` へコピーする。

fixture は tests/config/test_config_sync.py と同じ ConfigUnionHarness を使う。ワークスペースの
`.claude/skills/` に工程スキル（`process: true`）と、そうでないスキルを置く。

見るのは 3 つ。


1. 発言が、工程スキルの `SKILL.md` と `references/common/` の `.md` を、着手済みの親の
   ワークツリーの `skills/<工程>/` へコピーする（`ticket start` はコピーしない）。`references/` の
   それ以外(プロジェクト固有)と、`process: true` を持たないスキルはコピーしない
2. `common/` は共通のコピー。同じパスは共通の中身で上書きし、共通側に無くなった `.md` は消す
   (空になったディレクトリも片付ける)。`references/` の common 以外(プロジェクト固有)は
   上書きも削除もしない
3. 中身が同じなら何も書かず、知らせない

工程スキルの数え方と、シンボリックリンクがあるときに写さないことは、Go のテスト
（go/internal/tickets/sync/mirror）が確かめる。
"""

from __future__ import annotations

import json
import os

from tests import phasecopy
from tests.config.test_config_union import (
    LIB_PHASES,
    ConfigUnionHarness,
    git,
    read,
    ticket_text,
    write,
)

SCOPE = ("src/*",)


def process_skill(name: str) -> str:
    return f"---\nname: {name}\nprocess: true\ndescription: {name} の入口\n---\n\n# {name}\n"


def plain_skill(name: str) -> str:
    return f"---\nname: {name}\ndescription: {name}\n---\n\n# {name}\n"


class Started:
    """`ticket start` と、その後の発言（工程スキルをコピーする）の結果をまとめたもの。"""

    def __init__(self, start, prompt, said):
        self.returncode = start.returncode
        self.stdout = start.stdout + said
        self.stderr = start.stderr + prompt.stderr


class SkillSyncTest(ConfigUnionHarness):
    def prompt(self, session="s1"):
        """ユーザの発言を 1 件渡す（工程スキルをコピーする）。実行後チェックの基準も取る。"""
        if getattr(self, "_began", None) != session:
            self._began = session
            start = {"hook_event_name": "SessionStart", "session_id": session, "source": "startup"}
            start["cwd"] = self.ws
            self.ccnavi("--mode", "enable", stdin=json.dumps(start))
        done = self.hook("", self.ws, event="UserPromptSubmit", session=session)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return done

    def start_ticket(self, name):
        """親の `ticket start` を打ち、続けてユーザの発言を渡す。"""
        started = self.ccnavi("ticket", "start", name)
        prompted = self.prompt()
        return Started(started, prompted, self.reason(prompted))

    def source(self, *parts):
        return os.path.join(self.ws, ".claude", "skills", *parts)

    def start_parent(self, name="i0001"):
        self.propose(
            name, ticket_text(name, project="lib", plan=["build"], allow=SCOPE), project="lib"
        )
        approved = self.approve()
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
        tree = self.worktree(self.lib, name)
        started = self.start_ticket(name)
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        return tree, started

    def test_start_copies_the_common_files_and_leaves_the_project_files(self):
        """1・2: 工程スキルの SKILL.md と common/ の .md をコピーし、共通側の中身で上書きする。

        共通側に無くなった common/ の .md は消し、空のディレクトリも片付ける。process: true の
        無いスキル・common 以外の references・.md でないものは、コピーも削除もしない。
        """
        write(self.source("process-test", "SKILL.md"), process_skill("process-test"))
        common = self.source("process-test", "references", "common")
        write(os.path.join(common, "unit", "basics.md"), "# 基本\n")
        write(os.path.join(common, "ccnavi-index.jsonl"), "{}\n")
        write(os.path.join(common, "shared.md"), "共通の新しい中身\n")
        write(self.source("process-test", "references", "project", "workspace-only.md"), "固有\n")
        write(self.source("process-test", "references", "loose.md"), "直下\n")
        write(self.source("commit", "SKILL.md"), plain_skill("commit"))
        seeded = os.path.join(self.lib, "skills", "process-test", "references")
        write(os.path.join(seeded, "common", "shared.md"), "古い中身\n")
        write(os.path.join(seeded, "common", "old", "gone.md"), "消える\n")
        write(os.path.join(seeded, "common", "note.txt"), "md でないものは触れない\n")
        write(os.path.join(seeded, "project", "own.md"), "プロジェクト固有\n")
        git(self.lib, "add", "-A")
        git(self.lib, "commit", "-m", "seed")

        tree, started = self.start_parent()

        skill_dir = os.path.join(tree, "skills", "process-test")
        out = os.path.join(skill_dir, "references")
        self.assertEqual(read(os.path.join(skill_dir, "SKILL.md")), process_skill("process-test"))
        self.assertEqual(read(os.path.join(out, "common", "unit", "basics.md")), "# 基本\n")
        self.assertEqual(read(os.path.join(out, "common", "shared.md")), "共通の新しい中身\n")
        self.assertEqual(read(os.path.join(seeded, "common", "shared.md")), "古い中身\n")
        self.assertFalse(os.path.exists(os.path.join(out, "common", "old")))
        self.assertTrue(os.path.exists(os.path.join(out, "common", "note.txt")))
        self.assertFalse(os.path.exists(os.path.join(out, "common", "ccnavi-index.jsonl")))
        self.assertEqual(read(os.path.join(out, "project", "own.md")), "プロジェクト固有\n")
        self.assertFalse(os.path.exists(os.path.join(out, "project", "workspace-only.md")))
        self.assertFalse(os.path.exists(os.path.join(out, "loose.md")))
        self.assertFalse(os.path.exists(os.path.join(tree, "skills", "commit")))
        self.assertIn("skills/process-test/SKILL.md", started.stdout)
        self.assertIn("削除した", started.stdout)
        # 元リポジトリには触れない（親のブランチに乗って届く）。
        self.assertFalse(os.path.exists(os.path.join(self.lib, "skills", "commit")))

    def test_same_content_is_not_reported(self):
        """3: 中身が同じなら知らせない"""
        write(self.source("process-test", "SKILL.md"), process_skill("process-test"))
        _, first = self.start_parent("i0001")
        self.assertIn("skills/process-test", first.stdout)
        tree = self.worktree(self.lib, "i0002")
        write(
            os.path.join(tree, "skills", "process-test", "SKILL.md"), process_skill("process-test")
        )
        self.propose(
            "i0002", ticket_text("i0002", project="lib", plan=["build"], allow=SCOPE), project="lib"
        )
        self.assertEqual(self.approve().returncode, 0)
        second = self.start_ticket("i0002")
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertNotIn("skills/process-test", second.stdout)

    def test_the_start_does_not_copy_the_skills(self):
        """1: `ticket start` 自身は工程スキルをコピーしない。コピーするのは次のユーザの発言"""
        write(self.source("process-test", "SKILL.md"), process_skill("process-test"))
        tree = self.worktree(os.path.join(self.projects, "lib"), "i0001")
        write(
            os.path.join(tree, "wip", "proposals", "todo", "i0001.md"),
            phasecopy.insert(
                ticket_text("i0001", project="lib", plan=["build"], allow=SCOPE), LIB_PHASES
            ),
        )
        approved = self.approve()
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
        target = os.path.join(self.ws, "logs", "state", "write-and-push", "lib", "i0001.t.writes")

        started = self.ccnavi(
            "--record-writes", target, "--record-tree", tree, "ticket", "start", "i0001"
        )

        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        self.assertFalse(os.path.exists(os.path.join(tree, "skills", "process-test")))
        self.assertNotIn("skills/", read(target) if os.path.exists(target) else "")
        self.prompt()
        self.assertTrue(os.path.isfile(os.path.join(tree, "skills", "process-test", "SKILL.md")))
