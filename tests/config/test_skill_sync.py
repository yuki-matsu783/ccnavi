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

    def test_start_copies_only_process_skills(self):
        """1: 工程スキルの SKILL.md と references をコピーし、
        process: true の無いスキルはコピーしない"""
        write(self.source("process-test", "SKILL.md"), process_skill("process-test"))
        write(self.source("process-test", "references", "common", "unit", "basics.md"), "# 基本\n")
        write(self.source("process-test", "references", "common", "ccnavi-index.jsonl"), "{}\n")
        write(self.source("commit", "SKILL.md"), plain_skill("commit"))

        tree, started = self.start_parent()

        self.assertEqual(
            read(os.path.join(tree, "skills", "process-test", "SKILL.md")),
            process_skill("process-test"),
        )
        self.assertEqual(
            read(
                os.path.join(
                    tree, "skills", "process-test", "references", "common", "unit", "basics.md"
                )
            ),
            "# 基本\n",
        )
        self.assertFalse(
            os.path.exists(
                os.path.join(
                    tree, "skills", "process-test", "references", "common", "ccnavi-index.jsonl"
                )
            )
        )
        self.assertFalse(os.path.exists(os.path.join(tree, "skills", "commit")))
        self.assertIn("skills/process-test/SKILL.md", started.stdout)
        # 元リポジトリには触れない（親のブランチに乗って届く）。
        self.assertFalse(os.path.exists(os.path.join(self.lib, "skills")))

    def test_start_overwrites_common_and_leaves_project_files(self):
        """2: common/ の同じパスは共通の中身で上書きし、project/ のファイルは残す"""
        write(self.source("process-test", "SKILL.md"), process_skill("process-test"))
        write(
            self.source("process-test", "references", "common", "shared.md"), "共通の新しい中身\n"
        )
        own = os.path.join(self.lib, "skills", "process-test", "references", "project", "own.md")
        stale = os.path.join(
            self.lib, "skills", "process-test", "references", "common", "shared.md"
        )
        write(own, "プロジェクト固有\n")
        write(stale, "古い中身\n")
        git(self.lib, "add", "-A")
        git(self.lib, "commit", "-m", "seed")

        tree, _ = self.start_parent()

        base = os.path.join(tree, "skills", "process-test", "references")
        self.assertEqual(read(os.path.join(base, "common", "shared.md")), "共通の新しい中身\n")
        self.assertEqual(read(os.path.join(base, "project", "own.md")), "プロジェクト固有\n")

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

    def test_symlinked_process_dir_is_not_a_process(self):
        """2: 工程スキルのディレクトリ自体がシンボリックリンクなら、工程スキルに数えない"""
        from ccnavi.tickets import skillsync

        write(self.source("process-test", "SKILL.md"), process_skill("process-test"))
        os.symlink(self.source("process-test"), self.source("evil"))
        self.assertEqual(skillsync.processes(self.ws), ["process-test"])

    def test_symlink_under_a_process_stops_the_copy(self):
        """2: 工程スキルの下にシンボリックリンクがあれば、何もコピーせず理由を返す"""
        from ccnavi.tickets import skillsync

        write(self.source("process-test", "SKILL.md"), process_skill("process-test"))
        outside = os.path.join(self.ws, "outside.md")
        write(outside, "外\n")
        os.makedirs(self.source("process-test", "references", "common"), exist_ok=True)
        os.symlink(outside, self.source("process-test", "references", "common", "leak.md"))
        sources, why = skillsync.sources(self.ws)
        self.assertEqual(sources, {})
        self.assertIn("シンボリックリンク", why)

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

    def test_only_the_common_references_are_copied(self):
        """1: references/ の common 以外(プロジェクト固有)は、
        ワークスペースにあってもコピーしない"""
        write(self.source("process-test", "SKILL.md"), process_skill("process-test"))
        write(self.source("process-test", "references", "common", "shared.md"), "共通\n")
        write(self.source("process-test", "references", "project", "workspace-only.md"), "固有\n")
        write(self.source("process-test", "references", "loose.md"), "直下\n")

        tree, _ = self.start_parent()

        base = os.path.join(tree, "skills", "process-test", "references")
        self.assertEqual(read(os.path.join(base, "common", "shared.md")), "共通\n")
        self.assertFalse(os.path.exists(os.path.join(base, "project")))
        self.assertFalse(os.path.exists(os.path.join(base, "loose.md")))

    def test_symlinked_common_dir_stops_the_copy(self):
        """2: common/ そのものがシンボリックリンクなら、何もコピーせず理由を返す"""
        from ccnavi.tickets import skillsync

        write(self.source("process-test", "SKILL.md"), process_skill("process-test"))
        outside = os.path.join(self.ws, "outside")
        write(os.path.join(outside, "leak.md"), "外\n")
        os.makedirs(self.source("process-test", "references"), exist_ok=True)
        os.symlink(outside, self.source("process-test", "references", "common"))
        sources, why = skillsync.sources(self.ws)
        self.assertEqual(sources, {})
        self.assertIn("シンボリックリンク", why)

    def test_start_deletes_common_files_gone_from_the_source(self):
        """2: 共通側から無くなった common/ の .md は消し、空になったディレクトリも片付ける"""
        write(self.source("process-test", "SKILL.md"), process_skill("process-test"))
        write(self.source("process-test", "references", "common", "kept.md"), "残る\n")
        base = os.path.join(self.lib, "skills", "process-test", "references")
        write(os.path.join(base, "common", "kept.md"), "古い\n")
        write(os.path.join(base, "common", "old", "gone.md"), "消える\n")
        write(os.path.join(base, "common", "note.txt"), "md でないものは触れない\n")
        write(os.path.join(base, "project", "own.md"), "プロジェクト固有\n")
        git(self.lib, "add", "-A")
        git(self.lib, "commit", "-m", "seed")

        tree, started = self.start_parent()

        out = os.path.join(tree, "skills", "process-test", "references")
        self.assertEqual(read(os.path.join(out, "common", "kept.md")), "残る\n")
        self.assertFalse(os.path.exists(os.path.join(out, "common", "old")))
        self.assertTrue(os.path.exists(os.path.join(out, "common", "note.txt")))
        self.assertEqual(read(os.path.join(out, "project", "own.md")), "プロジェクト固有\n")
        self.assertIn("削除した", started.stdout)
