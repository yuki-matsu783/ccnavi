"""子チケットの識別子の形 `<親>-<2 桁のフェーズ番号>-<2 桁のフェーズ内の連番>`。

見るのは 5 つ。

1. 新しい形を読む。親が `-` や数字を含んでも（`web-i0012-05-01`）右から 2 段を剥がして親を割り出す
2. 識別子の中のフェーズ番号と `phase:` が食い違えば error で読まない
3. 旧い形（`<親>-<2 桁>`）の子は error で読まない
4. 続きの子の採番（`next_child_id`）は、親とフェーズごとに最大の連番 + 1
5. 識別子だけから親を割り出す `family_of`（hook の C1 と Chrome の入口）が同じ答えを出す
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import tempfile
import unittest

from ccnavi.hook import c1
from ccnavi.infra import settings
from ccnavi.tickets import approval
from ccnavi.tickets import ticket as ticket_mod

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def child_text(name, parent, phase):
    return "\n".join(
        [
            "---",
            "version: 1",
            f"ticket: {name}",
            f"parent: {parent}",
            f"phase: {phase}",
            "title: t",
            "allow:",
            '- {glob: "src/*", match: "Write|Edit"}',
            "---",
            "",
        ]
    )


def errors(problems):
    return [p.detail for p in problems if p.severity == ticket_mod.SEVERITY_ERROR]


class ChildIdFormTest(unittest.TestCase):
    def test_the_new_form_is_read(self):
        for name, parent, phase in (
            ("i0012-05-01", "i0012", 5),
            ("i0012-02-02", "i0012", 2),
            ("i0012-00-01", "i0012", 0),
            ("web-i0012-05-01", "web-i0012", 5),
            ("a-1-03-10", "a-1", 3),
        ):
            with self.subTest(name=name):
                t, problems = ticket_mod.parse(child_text(name, parent, phase))
                self.assertIsNotNone(t, problems)
                self.assertEqual([], errors(problems))
                self.assertEqual(t.phase, phase)
                self.assertTrue(t.is_child)

    def test_the_pattern_strips_two_steps_from_the_right(self):
        m = ticket_mod.child_pattern().match("web-i0012-05-01")
        self.assertIsNotNone(m)
        self.assertEqual(
            (m.group("parent"), m.group("phase"), m.group("seq")), ("web-i0012", "05", "01")
        )
        m = ticket_mod.child_pattern().match("x-01-02-03")
        self.assertEqual(
            (m.group("parent"), m.group("phase"), m.group("seq")), ("x-01", "02", "03")
        )
        for name in ("i0012", "i0012-05", "i0012-5-01", "i0012-05-1", "-05-01", "i0012-05-01-"):
            with self.subTest(name=name):
                self.assertIsNone(ticket_mod.child_pattern().match(name))

    def test_child_id_pads_both_numbers(self):
        self.assertEqual(ticket_mod.child_id("i0012", 5, 1), "i0012-05-01")
        self.assertEqual(ticket_mod.child_id("web-i0012", 0, 12), "web-i0012-00-12")

    def test_a_phase_that_disagrees_with_the_id_is_an_error(self):
        t, problems = ticket_mod.parse(child_text("i0012-02-01", "i0012", 5))
        self.assertIsNone(t)
        messages = errors(problems)
        self.assertEqual(1, len(messages), problems)
        self.assertIn("フェーズ番号（02）が `phase: 5` と食い違う", messages[0])
        self.assertIn("`i0012-05-<2 桁の連番>`", messages[0])

    def test_a_phase_of_three_digits_cannot_be_written(self):
        for name in ("i0012-99-01", "i0012-00-01"):
            with self.subTest(name=name):
                t, problems = ticket_mod.parse(child_text(name, "i0012", 100))
                self.assertIsNone(t)
                messages = errors(problems)
                self.assertEqual(1, len(messages), problems)
                self.assertIn("`phase: 100` は子の識別子に書けない", messages[0])
                # 書けない識別子（`i0012-100-..`）を勧めない
                self.assertNotIn("i0012-100", messages[0])
        t, problems = ticket_mod.parse(child_text("i0012-99-01", "i0012", 99))
        self.assertIsNotNone(t, problems)

    def test_the_old_form_is_an_error(self):
        for name in ("i0012-01", "i0012-05", "web-i0012-01"):
            parent = name.rsplit("-", 1)[0]
            with self.subTest(name=name):
                t, problems = ticket_mod.parse(child_text(name, parent, 1))
                self.assertIsNone(t)
                messages = errors(problems)
                self.assertEqual(1, len(messages), problems)
                self.assertIn(
                    f"子の識別子は `{parent}-<2 桁のフェーズ番号>-<2 桁の連番>` の形で書く",
                    messages[0],
                )
                self.assertIn(f"`{parent}-05-01`", messages[0])

    def test_a_child_of_another_parent_is_an_error(self):
        t, problems = ticket_mod.parse(child_text("i0013-01-01", "i0012", 1))
        self.assertIsNone(t)
        self.assertTrue(any("子の識別子は" in m for m in errors(problems)), problems)


class FamilyOfTest(unittest.TestCase):
    CASES = (
        ("i0012-05-01", "i0012"),
        ("web-i0012-05-01", "web-i0012"),
        ("i0012", "i0012"),
        ("web-i0012", "web-i0012"),
        # 旧い形は子として扱わない（自身が親）
        ("i0012-01", "i0012-01"),
        ("abc-01-02-03", "abc-01"),
    )

    def test_the_hook_strips_two_steps(self):
        for ident, family in self.CASES:
            with self.subTest(ident=ident):
                self.assertEqual(c1.family_of(ident), family)

    def test_the_chrome_entry_gives_the_same_answer(self):
        path = os.path.join(
            ROOT, "extensions", "chrome", "ccnavi-approval", "py", "ccnavi_chrome.py"
        )
        spec = importlib.util.spec_from_file_location("ccnavi_chrome_family", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for ident, family in self.CASES:
            with self.subTest(ident=ident):
                self.assertEqual(module.family_of(ident), family)


class NextChildIdTest(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="ccnavi-child-ids-")
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)
        subprocess.run(
            ["git", "init", "--quiet", "-b", "main"], cwd=self.ws, check=True, capture_output=True
        )
        self.conf, _ = settings.load(self.ws)

    def put(self, where, name, parent, phase):
        path = os.path.join(self.ws, *where.split("/"), name + ".md")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(child_text(name, parent, phase))

    def test_numbers_are_counted_per_phase(self):
        self.put(".ccnavi/approved/done", "i0001-02-01", "i0001", 2)
        self.put(".ccnavi/approved/done", "i0001-05-01", "i0001", 5)
        self.put(".ccnavi/approved/doing", "i0001-05-02", "i0001", 5)
        self.put("wip/proposals/todo", "i0001-05-03", "i0001", 5)
        # 別の親の子は数えない（親が同じ綴りで始まっても）
        self.put(".ccnavi/approved/doing", "i0001-x-02-07", "i0001-x", 2)
        self.assertEqual(approval.next_child_id(self.conf, self.ws, "i0001", 2), "i0001-02-02")
        self.assertEqual(approval.next_child_id(self.conf, self.ws, "i0001", 5), "i0001-05-04")
        self.assertEqual(approval.next_child_id(self.conf, self.ws, "i0001", 3), "i0001-03-01")
        self.assertEqual(approval.next_child_id(self.conf, self.ws, "i0001", 0), "i0001-00-01")
        self.assertEqual(approval.next_child_id(self.conf, self.ws, "i0001-x", 2), "i0001-x-02-08")

    def test_numbers_past_two_digits_are_refused(self):
        self.put(".ccnavi/approved/done", "i0001-02-99", "i0001", 2)
        with self.assertRaises(ValueError) as caught:
            approval.next_child_id(self.conf, self.ws, "i0001", 2)
        self.assertIn("連番 99 まで埋まっている", str(caught.exception))
        with self.assertRaises(ValueError):
            approval.next_child_id(self.conf, self.ws, "i0001", 100)
        self.assertEqual(approval.next_child_id(self.conf, self.ws, "i0001", 3), "i0001-03-01")


class FollowupRefusesTest(NextChildIdTest):
    """続きの子は、組めない識別子や先に在るファイルの上では何も書かずに止まる。"""

    def parent(self):
        return ticket_mod.Ticket(ticket="i0001", raw={}, body="")

    def snapshot(self):
        found = {}
        for current, dirs, names in os.walk(self.ws):
            dirs[:] = [d for d in dirs if d != ".git"]
            for name in names:
                path = os.path.join(current, name)
                with open(path, "rb") as f:
                    found[os.path.relpath(path, self.ws)] = f.read()
        return found

    def followup(self, phase_no):
        return approval.followup(
            self.conf, self.ws, self.parent(), phase_no, [], ["指摘"], "2026-10-04T00:00:00Z"
        )

    def test_a_full_phase_writes_nothing(self):
        self.put(".ccnavi/approved/done", "i0001-02-99", "i0001", 2)
        before = self.snapshot()
        ident, failed = self.followup(2)
        self.assertEqual(ident, "")
        self.assertIn("連番 99 まで埋まっている", failed)
        self.assertEqual(before, self.snapshot())

    def test_a_phase_past_two_digits_writes_nothing(self):
        before = self.snapshot()
        ident, failed = self.followup(100)
        self.assertEqual(ident, "")
        self.assertIn("フェーズ 100 は子の識別子に書けない", failed)
        self.assertEqual(before, self.snapshot())

    def test_an_existing_file_is_not_overwritten_even_if_unreadable(self):
        self.put(".ccnavi/approved/done", "i0001-02-01", "i0001", 2)
        # 読めないファイル（frontmatter が壊れている）は採番に数えられないが、名前で拾って止まる
        for where in (".ccnavi/approved/doing", "wip/proposals/review", "wip/proposals/todo"):
            with self.subTest(where=where):
                path = os.path.join(self.ws, *where.split("/"), "I0001-02-02.md")
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    f.write("壊れた中身\n")
                before = self.snapshot()
                ident, failed = self.followup(2)
                self.assertEqual(ident, "i0001-02-02")
                self.assertIn("すでに在る", failed)
                self.assertIn("I0001-02-02.md", failed)
                self.assertEqual(before, self.snapshot())
                os.remove(path)

    def test_a_free_number_is_written(self):
        self.put(".ccnavi/approved/done", "i0001-02-01", "i0001", 2)
        ident, failed = self.followup(2)
        self.assertEqual((ident, failed), ("i0001-02-02", ""))
        self.assertTrue(
            os.path.exists(os.path.join(self.ws, ".ccnavi", "approved", "doing", ident + ".md"))
        )


if __name__ == "__main__":
    unittest.main()
