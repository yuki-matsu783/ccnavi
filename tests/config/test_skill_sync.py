"""着手の前に共通の概念スキルをプロジェクトの `skills/` へ写す（設計 11.13）。

fixture は tests/config/test_config_sync.py と同じ ConfigUnionHarness を使う。ワークスペースの
`.claude/skills/` に概念スキル（`concept: true`）と、そうでないスキルを置く。

見るのは 3 つ。

1. 親の `ticket start` が、概念スキルの `SKILL.md` と `references/` の `.md` を親のワークツリーの
   `skills/<概念>/` へ写す。`concept: true` を持たないスキルは写さない
2. 上書きと追加だけで、消さない。共通に無いファイルと、プロジェクト固有の reference は残る。
   同じパスのファイルは共通の中身で上書きする
3. 中身が同じなら何も書かず、知らせない
"""

from __future__ import annotations

import os

from tests.config.test_config_union import ConfigUnionHarness, git, read, ticket_text, write

SCOPE = ("src/*",)


def concept_skill(name: str) -> str:
    return f"---\nname: {name}\nconcept: true\ndescription: {name} の入口\n---\n\n# {name}\n"


def plain_skill(name: str) -> str:
    return f"---\nname: {name}\ndescription: {name}\n---\n\n# {name}\n"


class SkillSyncTest(ConfigUnionHarness):
    def source(self, *parts):
        return os.path.join(self.ws, ".claude", "skills", *parts)

    def start_parent(self, name="i0001"):
        self.propose(name, ticket_text(name, project="lib", allow=SCOPE), project="lib")
        approved = self.approve()
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
        tree = self.worktree(self.lib, name)
        started = self.ccnavi("ticket", "start", name)
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        return tree, started

    def test_start_copies_only_concept_skills(self):
        """1: 概念スキルの SKILL.md と references を写し、concept: true の無いスキルは写さない"""
        write(self.source("testing", "SKILL.md"), concept_skill("testing"))
        write(self.source("testing", "references", "unit", "basics.md"), "# 基本\n")
        write(self.source("testing", "references", "index.jsonl"), "{}\n")
        write(self.source("commit", "SKILL.md"), plain_skill("commit"))

        tree, started = self.start_parent()

        self.assertEqual(
            read(os.path.join(tree, "skills", "testing", "SKILL.md")), concept_skill("testing")
        )
        self.assertEqual(
            read(os.path.join(tree, "skills", "testing", "references", "unit", "basics.md")),
            "# 基本\n",
        )
        self.assertFalse(
            os.path.exists(os.path.join(tree, "skills", "testing", "references", "index.jsonl"))
        )
        self.assertFalse(os.path.exists(os.path.join(tree, "skills", "commit")))
        self.assertIn("skills/testing/SKILL.md", started.stdout)
        # 元リポジトリには触れない（親のブランチに乗って届く）。
        self.assertFalse(os.path.exists(os.path.join(self.lib, "skills")))

    def test_start_overwrites_and_never_deletes(self):
        """2: 同じパスは共通の中身で上書きし、共通に無いプロジェクト固有のファイルは残す"""
        write(self.source("testing", "SKILL.md"), concept_skill("testing"))
        write(self.source("testing", "references", "shared.md"), "共通の新しい中身\n")
        own = os.path.join(self.lib, "skills", "testing", "references", "own.md")
        stale = os.path.join(self.lib, "skills", "testing", "references", "shared.md")
        write(own, "プロジェクト固有\n")
        write(stale, "古い中身\n")
        git(self.lib, "add", "-A")
        git(self.lib, "commit", "-m", "seed")

        tree, _ = self.start_parent()

        base = os.path.join(tree, "skills", "testing", "references")
        self.assertEqual(read(os.path.join(base, "shared.md")), "共通の新しい中身\n")
        self.assertEqual(read(os.path.join(base, "own.md")), "プロジェクト固有\n")

    def test_same_content_is_not_reported(self):
        """3: 中身が同じなら知らせない"""
        write(self.source("testing", "SKILL.md"), concept_skill("testing"))
        _, first = self.start_parent("i0001")
        self.assertIn("概念スキル", first.stdout)
        tree = self.worktree(self.lib, "i0002")
        write(os.path.join(tree, "skills", "testing", "SKILL.md"), concept_skill("testing"))
        self.propose("i0002", ticket_text("i0002", project="lib", allow=SCOPE), project="lib")
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
        os.makedirs(self.source("testing", "references"), exist_ok=True)
        os.symlink(outside, self.source("testing", "references", "leak.md"))
        sources, why = skillsync.sources(self.ws)
        self.assertEqual(sources, {})
        self.assertIn("シンボリックリンク", why)
