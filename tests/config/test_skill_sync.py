"""着手の前に共通の概念スキルをプロジェクトの `skills/` へ写す。

fixture は tests/config/test_config_sync.py と同じ ConfigUnionHarness を使う。ワークスペースの
`.claude/skills/` に概念スキル（`concept: true`）と、そうでないスキルを置く。

見るのは 3 つ。


1. 親の `ticket start` が、概念スキルの `SKILL.md` と `references/common/` の `.md` を
   親のワークツリーの `skills/<概念>/` へ写す。`references/` のそれ以外(プロジェクト固有)と、
   `concept: true` を持たないスキルは写さない
2. `common/` は共通の写し。同じパスは共通の中身で上書きし、共通側に無くなった `.md` は消す
   (空になったディレクトリも片付ける)。`references/` の common 以外(プロジェクト固有)は
   上書きも削除もしない。消す変更も、写しとして例外に通すのは共通側に無いファイルだけ
3. 中身が同じなら何も書かず、知らせない
"""

from __future__ import annotations

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


def concept_skill(name: str) -> str:
    return f"---\nname: {name}\nconcept: true\ndescription: {name} の入口\n---\n\n# {name}\n"


def plain_skill(name: str) -> str:
    return f"---\nname: {name}\ndescription: {name}\n---\n\n# {name}\n"


class SkillSyncTest(ConfigUnionHarness):
    def source(self, *parts):
        return os.path.join(self.ws, ".claude", "skills", *parts)

    def start_parent(self, name="i0001"):
        self.propose(
            name, ticket_text(name, project="lib", plan=["build"], allow=SCOPE), project="lib"
        )
        approved = self.approve()
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
        tree = self.worktree(self.lib, name)
        started = self.ccnavi("ticket", "start", name)
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        return tree, started

    def test_start_copies_only_concept_skills(self):
        """1: 概念スキルの SKILL.md と references を写し、concept: true の無いスキルは写さない"""
        write(self.source("testing", "SKILL.md"), concept_skill("testing"))
        write(self.source("testing", "references", "common", "unit", "basics.md"), "# 基本\n")
        write(self.source("testing", "references", "common", "ccnavi-index.jsonl"), "{}\n")
        write(self.source("commit", "SKILL.md"), plain_skill("commit"))

        tree, started = self.start_parent()

        self.assertEqual(
            read(os.path.join(tree, "skills", "testing", "SKILL.md")), concept_skill("testing")
        )
        self.assertEqual(
            read(
                os.path.join(tree, "skills", "testing", "references", "common", "unit", "basics.md")
            ),
            "# 基本\n",
        )
        self.assertFalse(
            os.path.exists(
                os.path.join(
                    tree, "skills", "testing", "references", "common", "ccnavi-index.jsonl"
                )
            )
        )
        self.assertFalse(os.path.exists(os.path.join(tree, "skills", "commit")))
        self.assertIn("skills/testing/SKILL.md", started.stdout)
        # 元リポジトリには触れない（親のブランチに乗って届く）。
        self.assertFalse(os.path.exists(os.path.join(self.lib, "skills")))

    def test_start_overwrites_common_and_leaves_project_files(self):
        """2: common/ の同じパスは共通の中身で上書きし、project/ のファイルは残す"""
        write(self.source("testing", "SKILL.md"), concept_skill("testing"))
        write(self.source("testing", "references", "common", "shared.md"), "共通の新しい中身\n")
        own = os.path.join(self.lib, "skills", "testing", "references", "project", "own.md")
        stale = os.path.join(self.lib, "skills", "testing", "references", "common", "shared.md")
        write(own, "プロジェクト固有\n")
        write(stale, "古い中身\n")
        git(self.lib, "add", "-A")
        git(self.lib, "commit", "-m", "seed")

        tree, _ = self.start_parent()

        base = os.path.join(tree, "skills", "testing", "references")
        self.assertEqual(read(os.path.join(base, "common", "shared.md")), "共通の新しい中身\n")
        self.assertEqual(read(os.path.join(base, "project", "own.md")), "プロジェクト固有\n")

    def test_same_content_is_not_reported(self):
        """3: 中身が同じなら知らせない"""
        write(self.source("testing", "SKILL.md"), concept_skill("testing"))
        _, first = self.start_parent("i0001")
        self.assertIn("概念スキル", first.stdout)
        tree = self.worktree(self.lib, "i0002")
        write(os.path.join(tree, "skills", "testing", "SKILL.md"), concept_skill("testing"))
        self.propose(
            "i0002", ticket_text("i0002", project="lib", plan=["build"], allow=SCOPE), project="lib"
        )
        self.assertEqual(self.approve().returncode, 0)
        second = self.ccnavi("ticket", "start", "i0002")
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertNotIn("概念スキル", second.stdout)

    def test_symlinked_concept_dir_is_not_a_concept(self):
        """2: 概念スキルのディレクトリ自体がシンボリックリンクなら、概念スキルに数えない"""
        from ccnavi.tickets import skillsync

        write(self.source("testing", "SKILL.md"), concept_skill("testing"))
        os.symlink(self.source("testing"), self.source("evil"))
        self.assertEqual(skillsync.concepts(self.ws), ["testing"])

    def test_symlink_under_a_concept_stops_the_copy(self):
        """2: 概念スキルの下にシンボリックリンクがあれば、何も写さず理由を返す"""
        from ccnavi.tickets import skillsync

        write(self.source("testing", "SKILL.md"), concept_skill("testing"))
        outside = os.path.join(self.ws, "outside.md")
        write(outside, "外\n")
        os.makedirs(self.source("testing", "references", "common"), exist_ok=True)
        os.symlink(outside, self.source("testing", "references", "common", "leak.md"))
        sources, why = skillsync.sources(self.ws)
        self.assertEqual(sources, {})
        self.assertIn("シンボリックリンク", why)

    def test_the_start_c1_carries_the_copied_skills(self):
        """1: 書いたパスの一覧(基点は親のワークツリー)で、写した概念スキルは外でも通す"""
        write(self.source("testing", "SKILL.md"), concept_skill("testing"))
        write(self.source("testing", "references", "common", "unit", "basics.md"), "# 基本\n")
        tree = self.worktree(os.path.join(self.projects, "lib"), "i0001")
        write(
            os.path.join(tree, "wip", "proposals", "todo", "i0001.md"),
            phasecopy.insert(
                ticket_text("i0001", project="lib", plan=["build"], allow=SCOPE), LIB_PHASES
            ),
        )
        approved = self.approve()
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
        write(
            os.path.join(self.state, "sync", "lib", "families", "i0001"),
            "remote origin\nbranch i0001\nsha 0\nfetched_at 1\nstate present\nreason \n",
        )
        target = os.path.join(self.ws, "logs", "state", "c1", "lib", "i0001.t.writes")
        started = self.ccnavi(
            "--record-writes", target, "--record-tree", tree, "ticket", "start", "i0001"
        )
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        listed = read(target).splitlines()
        self.assertIn("skills/testing/SKILL.md", listed)
        self.assertIn("skills/testing/references/common/unit/basics.md", listed)
        self.assertNotIn("置き場の外", started.stderr)

    def test_only_an_exact_copy_in_an_approved_parent_reads_as_synced(self):
        """1: 中身が違う、元リポジトリ、概念でないスキル、消す変更は写しと読まない"""
        from ccnavi.infra import settings
        from ccnavi.tickets import skillsync

        write(self.source("testing", "SKILL.md"), concept_skill("testing"))
        write(self.source("commit", "SKILL.md"), plain_skill("commit"))
        tree, _ = self.start_parent()
        conf, _ = settings.load(self.ws)
        copied = os.path.join(tree, "skills", "testing", "SKILL.md")
        self.assertTrue(skillsync.is_synced_write(conf, self.ws, copied))
        # 手で書き換えた中身は外す。
        write(copied, "書き換えた\n")
        self.assertFalse(skillsync.is_synced_write(conf, self.ws, copied))
        self.assertTrue(
            skillsync.is_synced_write(conf, self.ws, copied, concept_skill("testing").encode())
        )
        # 消す変更は写しではない。
        self.assertFalse(skillsync.is_synced_write(conf, self.ws, copied, None))
        # 概念スキルでないものと、概念スキルの外のファイルは外す。
        other = os.path.join(tree, "skills", "commit", "SKILL.md")
        write(other, plain_skill("commit"))
        self.assertFalse(skillsync.is_synced_write(conf, self.ws, other))
        loose = os.path.join(tree, "skills", "testing", "notes.txt")
        write(loose, concept_skill("testing"))
        self.assertFalse(skillsync.is_synced_write(conf, self.ws, loose))
        # 元リポジトリのファイルは外す。
        main_copy = os.path.join(self.lib, "skills", "testing", "SKILL.md")
        write(main_copy, concept_skill("testing"))
        self.assertFalse(skillsync.is_synced_write(conf, self.ws, main_copy))

    def test_only_the_common_references_are_copied(self):
        """1: references/ の common 以外(プロジェクト固有)は、ワークスペースにあっても写さない"""
        write(self.source("testing", "SKILL.md"), concept_skill("testing"))
        write(self.source("testing", "references", "common", "shared.md"), "共通\n")
        write(self.source("testing", "references", "project", "workspace-only.md"), "固有\n")
        write(self.source("testing", "references", "loose.md"), "直下\n")

        tree, _ = self.start_parent()

        base = os.path.join(tree, "skills", "testing", "references")
        self.assertEqual(read(os.path.join(base, "common", "shared.md")), "共通\n")
        self.assertFalse(os.path.exists(os.path.join(base, "project")))
        self.assertFalse(os.path.exists(os.path.join(base, "loose.md")))

    def test_a_copy_outside_common_does_not_read_as_synced(self):
        """1: 中身が共通と同じでも、references/ の common 以外は写しと読まない"""
        from ccnavi.infra import settings
        from ccnavi.tickets import skillsync

        write(self.source("testing", "SKILL.md"), concept_skill("testing"))
        write(self.source("testing", "references", "common", "shared.md"), "共通\n")
        write(self.source("testing", "references", "project", "same.md"), "共通\n")
        tree, _ = self.start_parent()
        conf, _ = settings.load(self.ws)
        base = os.path.join(tree, "skills", "testing", "references")
        self.assertTrue(
            skillsync.is_synced_write(conf, self.ws, os.path.join(base, "common", "shared.md"))
        )
        write(os.path.join(base, "project", "same.md"), "共通\n")
        self.assertFalse(
            skillsync.is_synced_write(conf, self.ws, os.path.join(base, "project", "same.md"))
        )

    def test_symlinked_common_dir_stops_the_copy(self):
        """2: common/ そのものがシンボリックリンクなら、何も写さず理由を返す"""
        from ccnavi.tickets import skillsync

        write(self.source("testing", "SKILL.md"), concept_skill("testing"))
        outside = os.path.join(self.ws, "outside")
        write(os.path.join(outside, "leak.md"), "外\n")
        os.makedirs(self.source("testing", "references"), exist_ok=True)
        os.symlink(outside, self.source("testing", "references", "common"))
        sources, why = skillsync.sources(self.ws)
        self.assertEqual(sources, {})
        self.assertIn("シンボリックリンク", why)

    def test_start_deletes_common_files_gone_from_the_source(self):
        """2: 共通側から無くなった common/ の .md は消し、空になったディレクトリも片付ける"""
        write(self.source("testing", "SKILL.md"), concept_skill("testing"))
        write(self.source("testing", "references", "common", "kept.md"), "残る\n")
        base = os.path.join(self.lib, "skills", "testing", "references")
        write(os.path.join(base, "common", "kept.md"), "古い\n")
        write(os.path.join(base, "common", "old", "gone.md"), "消える\n")
        write(os.path.join(base, "common", "note.txt"), "md でないものは触れない\n")
        write(os.path.join(base, "project", "own.md"), "プロジェクト固有\n")
        git(self.lib, "add", "-A")
        git(self.lib, "commit", "-m", "seed")

        tree, started = self.start_parent()

        out = os.path.join(tree, "skills", "testing", "references")
        self.assertEqual(read(os.path.join(out, "common", "kept.md")), "残る\n")
        self.assertFalse(os.path.exists(os.path.join(out, "common", "old")))
        self.assertTrue(os.path.exists(os.path.join(out, "common", "note.txt")))
        self.assertEqual(read(os.path.join(out, "project", "own.md")), "プロジェクト固有\n")
        self.assertIn("消した", started.stdout)

    def test_the_start_c1_carries_the_deletions(self):
        """1: 消した分も、書いたパスの一覧で置き場の外でも通す"""
        write(self.source("testing", "SKILL.md"), concept_skill("testing"))
        gone = os.path.join(self.lib, "skills", "testing", "references", "common", "gone.md")
        write(gone, "消える\n")
        git(self.lib, "add", "-A")
        git(self.lib, "commit", "-m", "seed")
        tree = self.worktree(os.path.join(self.projects, "lib"), "i0001")
        write(
            os.path.join(tree, "wip", "proposals", "todo", "i0001.md"),
            phasecopy.insert(
                ticket_text("i0001", project="lib", plan=["build"], allow=SCOPE), LIB_PHASES
            ),
        )
        self.assertEqual(self.approve().returncode, 0)
        write(
            os.path.join(self.state, "sync", "lib", "families", "i0001"),
            "remote origin\nbranch i0001\nsha 0\nfetched_at 1\nstate present\nreason \n",
        )
        target = os.path.join(self.ws, "logs", "state", "c1", "lib", "i0001.t.writes")
        started = self.ccnavi(
            "--record-writes", target, "--record-tree", tree, "ticket", "start", "i0001"
        )
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        self.assertIn("skills/testing/references/common/gone.md", read(target).splitlines())
        self.assertNotIn("置き場の外", started.stderr)

    def test_deletions_read_as_synced_only_for_files_gone_from_the_source(self):
        """1: 消す変更を写しと読むのは、共通側に無い common/ の .md だけ"""
        from ccnavi.infra import settings
        from ccnavi.tickets import skillsync

        write(self.source("testing", "SKILL.md"), concept_skill("testing"))
        write(self.source("testing", "references", "common", "shared.md"), "共通\n")
        tree, _ = self.start_parent()
        conf, _ = settings.load(self.ws)
        base = os.path.join(tree, "skills", "testing")
        in_source = os.path.join(base, "references", "common", "shared.md")
        gone = os.path.join(base, "references", "common", "gone.md")
        project_side = os.path.join(base, "references", "project", "own.md")
        skill_file = os.path.join(base, "SKILL.md")
        self.assertFalse(skillsync.is_synced_write(conf, self.ws, in_source, None))
        self.assertTrue(skillsync.is_synced_write(conf, self.ws, gone, None))
        self.assertFalse(skillsync.is_synced_write(conf, self.ws, project_side, None))
        self.assertFalse(skillsync.is_synced_write(conf, self.ws, skill_file, None))
