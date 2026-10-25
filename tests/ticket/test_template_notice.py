"""親の `finish`（親が閉じる出力）の、テンプレート化の案内 1 行。

見るのは次のとおり。

1. 計画を持つ親が閉じると、`ready` の前に `ccnavi --templates --draft <親>` を打つ案内が 1 行出る
2. 出さない: その親の識別子の下書きが在る、同じ計画の形のテンプレートが取り込み済み、
   子の `finish`、`ready` 済み（材料が退避されて拾えない。`close_notice` が空を返す）
2b. マージリクエストを作らない親（全フェーズが MR で見ない）には、`ready` に触れない文面を出す
3. 案内は出力に足すだけ。終了コードも、閉じる判定も変えない
4. 案内のとおりに打てば下書きができ、下書きができたあとは案内を出さない
"""

from __future__ import annotations

import os

from ccnavi.infra import settings
from ccnavi.tickets import template_drafts
from tests import ROOT
from tests.inproc import run_ccnavi
from tests.ticket.test_archive import ReadyHarness
from tests.ticket.test_phases import PhaseHarness
from tests.ticket.test_ticket import write
from tests.ticketsample import child_text, parent_text

MARK = "テンプレートにするなら"
TEMPLATE_SAME = "name: same\ndescription: 設計だけ\nwhen: 設計をするとき\nplan: [design]\n"
TEMPLATE_OTHER = (
    "name: other\ndescription: 設計を 2 回\nwhen: 設計を重ねるとき\nplan: [design, design]\n"
)


def notice_lines(result):
    return [line for line in result.stdout.splitlines() if MARK in line]


class CloseNoticeTest(ReadyHarness):
    def conf(self):
        conf, _ = settings.load(self.root)
        return conf

    def draft_file(self, name="i0001", to="project"):
        """親のツリーの `wip/handoff/` の、テンプレートの下書き 1 本。"""
        layer = "common" if to == "common" else "project"
        return os.path.join(
            self.parent_tree,
            "wip",
            "handoff",
            ".ccnavi",
            "config",
            layer,
            "templates",
            name + ".yml",
        )

    def common_template(self, name, text):
        directory = settings.common_templates_dir(self.conf(), self.root)
        return write(os.path.join(directory, name + ".yml"), text)

    def test_a_closed_parent_with_a_plan_says_how_to_make_a_template_in_one_line(self):
        self.closable()
        closed = self.parent_closed
        self.assertEqual(closed.returncode, 0, closed.stderr)
        lines = notice_lines(closed)
        self.assertEqual(len(lines), 1, closed.stdout)
        self.assertIn("`ready` の前に", lines[0])
        self.assertIn("ccnavi --templates --draft i0001", lines[0])
        self.assertIn("退避されて拾えない", lines[0])
        # 下書きを残したままでは ready が止まるので、そのことも言う。
        self.assertIn("ccnavi-user-handoff.sh", lines[0])
        self.assertIn("削除してから ready", lines[0])
        # 案内は、ready の案内の後ろに足す。
        self.assertGreater(closed.stdout.index(MARK), closed.stdout.index("logs/archive/"))

    def test_an_existing_draft_of_the_parent_silences_it(self):
        self.closable(
            before_close=lambda: write(self.draft_file(), "name: i0001\nplan: [design]\n")
        )
        closed = self.parent_closed
        self.assertEqual(closed.returncode, 0, closed.stderr)
        self.assertEqual(notice_lines(closed), [])
        self.assertIn("logs/archive/", closed.stdout)

    def test_a_common_draft_of_the_parent_silences_it_too(self):
        self.closable(
            before_close=lambda: write(
                self.draft_file(to="common"), "name: i0001\nplan: [design]\n"
            )
        )
        self.assertEqual(notice_lines(self.parent_closed), [])

    def test_a_draft_under_another_name_does_not(self):
        self.closable(before_close=lambda: write(self.draft_file("other"), "name: other\n"))
        self.assertEqual(len(notice_lines(self.parent_closed)), 1)

    def test_an_adopted_template_with_the_same_plan_silences_it(self):
        self.closable(before_close=lambda: self.common_template("same", TEMPLATE_SAME))
        closed = self.parent_closed
        self.assertEqual(closed.returncode, 0, closed.stderr)
        self.assertEqual(notice_lines(closed), [])

    def test_an_adopted_template_with_another_plan_does_not(self):
        self.closable(before_close=lambda: self.common_template("other", TEMPLATE_OTHER))
        self.assertEqual(len(notice_lines(self.parent_closed)), 1)

    def test_a_template_of_the_project_layer_counts_too(self):
        def adopt():
            directory = settings.layer_templates_dir(self.conf(), self.root)
            write(os.path.join(directory, "same.yml"), TEMPLATE_SAME)

        self.closable(before_close=adopt)
        self.assertEqual(notice_lines(self.parent_closed), [])

    def test_a_child_finish_does_not_say_it(self):
        self.family(plan=["design"])
        self.propose("i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["wip/design/*"]))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.run_child("i0001-01-01", [("wip/design/plan.md", "d\n")])
        closed = self.close_child("i0001-01-01")
        self.assertEqual(closed.returncode, 0, closed.stderr)
        self.assertEqual(notice_lines(closed), [])

    def test_the_notice_names_a_command_that_works_and_then_goes_quiet(self):
        self.closable()
        self.assertEqual(len(notice_lines(self.parent_closed)), 1)
        env = {
            **{k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")},
            "CCNAVI_LEARNING": "disable",
        }
        env.pop("CLAUDE_PROJECT_DIR", None)
        made = run_ccnavi(
            ["--root", self.root, "--templates", "--draft", "i0001"], env=env, cwd=ROOT
        )
        self.assertEqual(made.returncode, 0, made.stdout + made.stderr)
        self.assertTrue(os.path.isfile(self.draft_file()))
        self.assertEqual(template_drafts.close_notice(self.conf(), self.root, "i0001"), "")

    def test_the_notice_text_is_a_single_line_with_the_parent(self):
        self.closable()
        text = template_drafts.close_notice(self.conf(), self.root, "i0001")
        self.assertEqual(text.count("\n"), 1)
        self.assertTrue(text.endswith("\n"))

    def test_after_ready_the_materials_are_gone_so_it_says_nothing(self):
        fixture = self.closable()
        self.assertEqual(self.ready(fixture).returncode, 0)
        self.assertEqual(template_drafts.close_notice(self.conf(), self.root, "i0001"), "")

    def test_an_unknown_parent_says_nothing(self):
        self.assertEqual(template_drafts.close_notice(self.conf(), self.root, "nothing"), "")


class NoMrCloseNoticeTest(PhaseHarness):
    """全フェーズが MR で見ない親（`research` だけ）を閉じる。"""

    def close_without_mr(self, before_close=None):
        self.propose("i0001", parent_text("i0001", ["research"], copy=self.phases_text))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.propose(
            "i0001-01-01",
            child_text("i0001-01-01", "i0001", 1, ["wip/research/*"], review=False),
        )
        self.commit_parent("propose child")
        self.assertEqual(self.approve().returncode, 0)
        self.run_child("i0001-01-01", [("wip/research/summary.md", "s\n")])
        self.assertEqual(self.close_child("i0001-01-01").returncode, 0)
        self.commit_parent("close 01")
        self.merge("i0001-01-01")
        self.propose("i0001", parent_text("i0001", ["research"], feedback=[]))
        self.assertEqual(self.approve().returncode, 0)
        if before_close:
            before_close()
        closed = self.ccnavi("ticket", "finish", "i0001")
        self.assertEqual(closed.returncode, 0, closed.stderr)
        return closed

    def test_a_parent_without_mr_gets_a_notice_that_never_mentions_ready(self):
        closed = self.close_without_mr()
        self.assertIn("Draft を解除する手順はありません", closed.stdout)
        lines = notice_lines(closed)
        self.assertEqual(len(lines), 1, closed.stdout)
        self.assertIn("ccnavi --templates --draft i0001", lines[0])
        self.assertIn("取り込む前に", lines[0])
        self.assertNotIn("ready", lines[0])
        self.assertGreater(closed.stdout.index(MARK), closed.stdout.index("Draft を解除"))
        self.assertTrue(closed.stdout.endswith("\n"))

    def test_the_notice_of_a_parent_without_mr_goes_quiet_after_the_draft(self):
        def draft():
            write(
                os.path.join(
                    self.parent_tree,
                    "wip/handoff/.ccnavi/config/project/templates/i0001.yml".replace("/", os.sep),
                ),
                "name: i0001\nplan: [research]\n",
            )

        self.assertEqual(notice_lines(self.close_without_mr(draft)), [])
