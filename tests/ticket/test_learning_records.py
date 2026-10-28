"""気づきの記録の読み取り（共有の関数）と、処理すべきものの判定。

読み取りの検査と整合の検査の 2 段、処理すべきものの列挙、親の finish が見る未コミットの検出。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest

from ccnavi.tickets import learning_records as lr

HEAD = "---\nticket: c-01-01\nparent: c\nlearnings: {n}\n---\n\n"
SEC = "## {t}\n\n- 種類: 未定\n- 内容: x\n{p}\n"


def git(cwd, *args):
    subprocess.run(
        ["git", "-C", cwd, "-c", "user.email=a@b", "-c", "user.name=t", *args],
        check=True,
        capture_output=True,
    )


def write(root, rel, text):
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


class ParseTest(unittest.TestCase):
    def parse(self, text):
        return lr.parse("wip/handoff/learnings/c-01-01.md", "c-01-01", text)

    def test_unreadable_forms(self):
        for text in (
            "",
            "learnings: none\n",
            "---\nticket: x\n---\n",
            "---\nlearnings: [\n---\n",
            "---\nlearnings: many\n---\n",
            "---\nlearnings: -1\n---\n",
            "---\nlearnings: true\n---\n",
            "---\n- a\n---\n",
            "---\nlearnings: none\n",
        ):
            record, why = self.parse(text)
            self.assertIsNone(record, text)
            self.assertTrue(why, text)

    def test_none_and_counts(self):
        record, _ = self.parse(HEAD.format(n="none"))
        self.assertIsNone(record.learnings)
        self.assertEqual(record.sections, [])
        record, _ = self.parse(HEAD.format(n=0))
        self.assertEqual(record.learnings, 0)

    def test_consistency(self):
        two = HEAD.format(n=2) + SEC.format(t="a", p="") + SEC.format(t="b", p="")
        record, _ = self.parse(two)
        self.assertEqual(lr.consistency_problem(record), "")
        record, _ = self.parse(HEAD.format(n="none") + SEC.format(t="a", p=""))
        self.assertIn("none", lr.consistency_problem(record))
        record, _ = self.parse(HEAD.format(n=3) + SEC.format(t="a", p=""))
        self.assertIn("合わない", lr.consistency_problem(record))
        record, _ = self.parse(
            HEAD.format(n=1) + SEC.format(t="a", p="- 処理: 反映 p\n- 処理: 対象外 y")
        )
        self.assertIn("2 つ", lr.consistency_problem(record))
        record, _ = self.parse(HEAD.format(n=1) + SEC.format(t="a", p="- 処理: たぶん"))
        self.assertIn("形が分からない", lr.consistency_problem(record))

    def test_process_forms(self):
        record, _ = self.parse(HEAD.format(n=1) + SEC.format(t="a", p="- 処理: 反映 pat"))
        self.assertEqual(record.sections[0].process, ("反映", "pat"))
        record, _ = self.parse(HEAD.format(n=1) + SEC.format(t="a", p="- 処理: 対象外 環境の都合"))
        self.assertEqual(record.sections[0].process, ("対象外", "環境の都合"))
        # 内容の継続行（字下げ）の `- 処理:` は処理の行ではない
        body = "## a\n\n- 内容: x\n  - 処理: 反映 pat\n"
        record, _ = self.parse(HEAD.format(n=1) + body)
        self.assertIsNone(record.sections[0].process)


class PendingTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ccnavi-lr-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        git(self.root, "init", "--quiet", "-b", "main")
        write(self.root, "README", "x\n")

    def commit(self):
        git(self.root, "add", "-f", "-A")
        git(self.root, "commit", "--quiet", "--allow-empty", "-m", "c")

    def record(self, ident, n, *sections):
        write(
            self.root,
            f"wip/handoff/learnings/{ident}.md",
            HEAD.format(n=n) + "".join(sections),
        )

    def pattern(self, name, sources):
        write(
            self.root,
            f"docs/wiki/patterns/{name}.md",
            f"---\ntype: learning-wiki\ntarget: docs\nsources: {sources}\n---\n\n本文\n",
        )

    def test_nothing_to_process(self):
        self.record("c-01-01", "none")
        self.commit()
        found = lr.pending(self.root)
        self.assertEqual((found.items, found.unreadable, found.inconsistent), ([], [], []))

    def test_section_without_process_line_is_listed(self):
        self.record("c-01-01", 1, SEC.format(t="落とし穴", p=""))
        self.commit()
        found = lr.pending(self.root)
        self.assertEqual([i.title for i in found.items], ["落とし穴"])
        self.assertIn("処理の行が無い", found.items[0].why)

    def test_reflect_needs_pattern_and_source(self):
        self.record("c-01-01", 1, SEC.format(t="a", p="- 処理: 反映 pat"))
        self.commit()
        self.assertIn("パターンが無い", lr.pending(self.root).items[0].why)
        self.pattern("pat", "[other]")
        self.commit()
        self.assertIn("sources", lr.pending(self.root).items[0].why)
        self.pattern("pat", "[other, c-01-01]")
        self.commit()
        self.assertEqual(lr.pending(self.root).items, [])

    def test_skip_needs_reason(self):
        self.record("c-01-01", 1, SEC.format(t="a", p="- 処理: 対象外"))
        self.commit()
        self.assertIn("理由が無い", lr.pending(self.root).items[0].why)
        self.record("c-01-01", 1, SEC.format(t="a", p="- 処理: 対象外 環境が原因"))
        self.commit()
        self.assertEqual(lr.pending(self.root).items, [])

    def test_broken_pattern_does_not_count_as_reflected(self):
        # type も target も無いパターンは --learnings が数えないので、反映済みとも見なさない
        write(self.root, "docs/wiki/patterns/bad.md", "---\nsources: [c-01-01]\n---\n")
        self.record("c-01-01", 1, SEC.format(t="a", p="- 処理: 反映 bad"))
        self.commit()
        self.assertIn("パターンが無い", lr.pending(self.root).items[0].why)

    def test_names_are_compared_without_case(self):
        self.pattern("Foo", "[C-01-01]")
        self.record("c-01-01", 1, SEC.format(t="a", p="- 処理: 反映 foo"))
        self.commit()
        self.assertEqual(lr.pending(self.root).items, [])

    def test_fullwidth_space_in_the_process_line(self):
        self.record("c-01-01", 1, SEC.format(t="a", p="- 処理:　対象外　環境の都合"))
        self.commit()
        found = lr.pending(self.root)
        self.assertEqual((found.items, found.inconsistent), ([], []))

    def test_any_file_in_the_inbox_counts_except_gitkeep(self):
        write(self.root, "docs/wiki/inbox/.gitkeep", "")
        write(self.root, "docs/wiki/inbox/note.txt", "x\n")
        write(self.root, "docs/wiki/inbox/NOTE.MD", "x\n")
        self.commit()
        self.assertEqual(
            sorted(i.path for i in lr.pending(self.root).items),
            ["docs/wiki/inbox/NOTE.MD", "docs/wiki/inbox/note.txt"],
        )

    def test_pattern_name_cannot_escape(self):
        self.record("c-01-01", 1, SEC.format(t="a", p="- 処理: 反映 ../x"))
        self.commit()
        self.assertTrue(lr.pending(self.root).items)

    def test_inbox_files_are_listed(self):
        write(self.root, "docs/wiki/inbox/feat-20260101000000.md", "---\nlearnings: 1\n---\n")
        self.commit()
        found = lr.pending(self.root)
        self.assertEqual([i.path for i in found.items], ["docs/wiki/inbox/feat-20260101000000.md"])

    def test_broken_records_are_reported_not_listed(self):
        write(self.root, "wip/handoff/learnings/bad.md", "no frontmatter\n")
        self.record("c-01-01", "none", SEC.format(t="a", p=""))
        self.commit()
        found = lr.pending(self.root)
        self.assertEqual(found.items, [])
        self.assertEqual([p for p, _ in found.unreadable], ["wip/handoff/learnings/bad.md"])
        self.assertEqual([p for p, _ in found.inconsistent], ["wip/handoff/learnings/c-01-01.md"])

    def test_symlink_record_is_unreadable(self):
        self.record("real", "none")
        os.symlink("real.md", os.path.join(self.root, "wip/handoff/learnings/link.md"))
        self.commit()
        found = lr.pending(self.root)
        self.assertEqual([p for p, _ in found.unreadable], ["wip/handoff/learnings/link.md"])

    def test_uncommitted_changes_are_not_read_but_detected(self):
        self.record("c-01-01", "none")
        self.commit()
        self.record("c-01-01", 1, SEC.format(t="a", p=""))
        write(self.root, "docs/wiki/patterns/new.md", "x\n")
        self.assertEqual(lr.pending(self.root).items, [])
        self.assertEqual(
            lr.uncommitted(self.root),
            ["docs/wiki/patterns/new.md", "wip/handoff/learnings/c-01-01.md"],
        )


class DefaultTest(unittest.TestCase):
    def test_learning_is_enabled_when_unset(self):
        from dataclasses import replace

        from ccnavi.infra import settings

        base = settings.Settings()
        self.assertTrue(base.learning_enabled)
        self.assertTrue(replace(base, learning="enable").learning_enabled)
        self.assertTrue(replace(base, learning="nonsense").learning_enabled)
        self.assertFalse(replace(base, learning="disable").learning_enabled)


if __name__ == "__main__":
    unittest.main()
