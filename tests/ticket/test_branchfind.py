"""issue・MR に紐づくブランチを探す仕組みの受入テスト。

見るのは 2 つ。

1. UserPromptSubmit: 依頼文に issue・MR の指定があれば、`ccnavi-start.sh` を打って着手させ、
   複数候補（終了コード 3）はユーザに確かめ、ホストに届かない（4）ときは MCP で代行させる指示が
   `additionalContext` に載る。表記はワークスペースルートの絶対パスから書く。
   指定が無い・外れ・チケット制御が disable のときは載らない。判定（止める・聞く）は返さない
2. 副命令 `ccnavi branches <issue|mr> <番号> --result <json>`: 手元の候補（名前に番号を含む
   ブランチ・ワークツリー・`issue:` を持つチケット）を集め、sh が書いたホストの結果と合わせて出す。
   ホストの結果が無いときは「ホストは見ていない」と言う。承認前の提案の `branch:` は使わない
"""

from __future__ import annotations

import json
import os

from ccnavi.infra import settings
from tests.ticket.test_phases import PhaseHarness, git, write
from tests.ticketsample import parent_text


def with_branch(text, branch):
    head, rest = text.split("\n", 3)[:3], text.split("\n", 3)[3]
    return "\n".join([*head, f"branch: {branch}", rest])


def with_mr(text, mr):
    head, rest = text.split("\n", 3)[:3], text.split("\n", 3)[3]
    return "\n".join([*head, f"mr: {mr}", rest])


class PromptHintTest(PhaseHarness):
    def propose_own(self, name, text):
        """承認する提案は、識別子と同じ名前のワークツリーに書いてコミットする。"""
        tree = self.worktree(name, "main")
        self.propose(name, text, tree=tree)
        git(tree, "add", "-A")
        git(tree, "commit", "--quiet", "-m", "propose " + name)

    def prompt(self, text, *extra, session="s1"):
        payload = {
            "hook_event_name": "UserPromptSubmit",
            "cwd": self.parent_tree,
            "session_id": session,
            "prompt": text,
        }
        result = self.ccnavi("--mode", "enable", *extra, stdin=json.dumps(payload))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        if not result.stdout.strip():
            return ""
        out = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertNotIn("permissionDecision", out)
        return out.get("additionalContext") or ""

    def test_an_issue_and_an_mr_get_one_instruction_each(self):
        said = self.prompt("#152 と !5 を見て直して")
        sh = settings.script_command(self.root, "ccnavi-start.sh")
        self.assertIn(f"'{sh} --issue 152'", said)
        self.assertIn(f"'{sh} --mr 5'", said)
        self.assertIn(os.path.realpath(self.root).replace("\\", "/"), said)
        # ユーザに確かめて返事を待つこと、選べる 3 つ、承認前の提案の branch: を使わないこと
        self.assertIn("返事を待つ", said)
        self.assertIn("既存のブランチで続ける", said)
        self.assertIn("<先頭の語>-<番号>-<slug>", said)
        self.assertIn("やめる", said)
        self.assertIn("承認前の提案の branch: は使わない", said)
        self.assertIn("ccnavi-git.sh", said)
        self.assertIn("終了コード 3（候補が複数）", said)
        self.assertIn("終了コード 4（ホストに届かない）", said)
        self.assertIn("MCP で代行し、同じコマンドを打ち直す", said)
        self.assertIn("終了コード 1・2 なら、出力の理由をユーザに伝える", said)
        self.assertNotIn("ccnavi-branches.sh", said)

    def test_a_ccnavi_notice_is_not_a_reference(self):
        notice = "[ccnavi] 取り込み済み: https://example.com/acme/widgets/pull/296 を閉じた\n"
        self.assertEqual(self.prompt(notice), "")
        # 通知の外にも指定があるときは、外の分だけ拾う
        said = self.prompt(notice + "\n#10 を直して", session="s2")
        sh = settings.script_command(self.root, "ccnavi-start.sh")
        self.assertIn(f"'{sh} --issue 10'", said)
        self.assertNotIn("296", said)

    def test_pasted_content_and_other_session_messages_are_not_references(self):
        self.assertEqual(self.prompt("<pasted_content>ログ #10 と MR !7</pasted_content>"), "")
        self.assertEqual(self.prompt("<system-reminder>issue 11</system-reminder>"), "")
        self.assertEqual(
            self.prompt("Another Claude session sent a message:\nPR #12 を出した", session="s3"),
            "",
        )
        said = self.prompt("<pasted_content>#10</pasted_content> と !7", session="s4")
        self.assertIn("--mr 7", said)
        self.assertNotIn("--issue 10", said)

    def test_a_number_tied_to_a_ticket_does_not_say_start(self):
        text = with_branch(parent_text("feature-152-login", ["research"], issue=152), "topic/x")
        self.propose_own("feature-152-login", text)
        self.approve()
        said = self.prompt("#152 を直して")
        self.assertIn("すでに feature-152-login(doing)", said)
        self.assertIn("結び付いている", said)
        self.assertIn("ワークツリー", said)
        self.assertIn("言い直してもらう", said)
        self.assertNotIn("ccnavi-start.sh --issue", said)
        # 結び付かない番号は今までどおり
        self.assertIn("--issue 9", self.prompt("#9 を直して", session="s5"))

    def test_a_branch_name_with_the_number_is_not_a_mr_binding(self):
        text = with_branch(parent_text("fix-12-foo", ["research"], issue=3), "fix-12-foo")
        self.propose_own("fix-12-foo", text)
        self.approve()
        said = self.prompt("MR !12 を見て")
        self.assertIn("--mr 12", said)
        self.assertNotIn("結び付いている", said)

    def test_a_mr_branch_of_a_ticket_says_continue(self):
        text = with_branch(parent_text("feature-3-foo", ["research"], issue=3), "mr-12")
        self.propose_own("feature-3-foo", text)
        self.approve()
        said = self.prompt("MR !12 を見て")
        self.assertIn("すでに feature-3-foo(doing)", said)
        self.assertNotIn("--mr 12", said)

    def test_a_mr_field_of_an_approved_ticket_says_continue(self):
        self.propose_own("feature-3-foo", with_mr(parent_text("feature-3-foo", ["research"]), 12))
        self.approve()
        said = self.prompt("MR !12 を見て")
        self.assertIn("すでに feature-3-foo(doing)", said)
        self.assertNotIn("--mr 12", said)
        # 番号が別なら結び付かない
        other = self.prompt("MR !13 を見て", session="s2")
        self.assertIn("--mr 13", other)
        self.assertNotIn("結び付いている", other)

    def test_a_mr_field_of_a_proposal_says_continue(self):
        self.propose("feature-3-foo", with_mr(parent_text("feature-3-foo", ["research"]), 12))
        said = self.prompt("MR !12 を見て")
        self.assertIn("すでに feature-3-foo(todo)", said)
        self.assertIn("結び付いている", said)
        self.assertNotIn("--mr 12", said)

    def test_the_mr_field_and_the_branch_convention_are_both_kept(self):
        # `mr:` の一致を先に見る。慣習名（branch: mr-N）は併用で残る
        by_field = with_mr(parent_text("feature-3-foo", ["research"]), 12)
        by_name = with_branch(parent_text("feature-4-bar", ["research"]), "mr-14")
        self.propose_own("feature-3-foo", by_field)
        self.propose_own("feature-4-bar", by_name)
        self.approve()
        self.assertIn("すでに feature-3-foo(doing)", self.prompt("MR !12 を見て"))
        self.assertIn("すでに feature-4-bar(doing)", self.prompt("MR !14 を見て", session="s2"))

    def test_the_mr_field_is_not_an_issue_binding(self):
        self.propose("feature-3-foo", with_mr(parent_text("feature-3-foo", ["research"]), 12))
        self.assertIn("--issue 12", self.prompt("#12 を直して"))

    def test_the_same_number_is_said_once_per_session(self):
        self.assertIn("--issue 152", self.prompt("#152 を直して"))
        self.assertEqual(self.prompt("#152 の続き"), "")
        # 別の番号は言う。別のセッションは別に数える
        self.assertIn("--issue 153", self.prompt("#152 と #153"))
        self.assertIn("--issue 152", self.prompt("#152 を直して", session="other"))

    def test_no_reference_no_instruction(self):
        self.assertEqual(self.prompt("README の見出し # 概要 を直して。色は #fff"), "")

    def test_ticket_control_disabled_says_nothing(self):
        self.assertEqual(self.prompt("#152 を直して", "--ticket-control", "disable"), "")

    def test_dry_run_still_says_it(self):
        # 指示を足すだけで止めないので、dry-run でも同じ文を足す（モードが disable なら hook は
        # 何もしない。events.decide の入口）
        self.assertIn("--issue 152", self.prompt("#152 を直して", "--mode", "dry-run"))


class BranchesCommandTest(PhaseHarness):
    def setUp(self):
        super().setUp()
        for name in ("feature-152-login", "fix/152-typo", "issue-152", "feature-1520-other"):
            git(self.root, "branch", name, "main")
        # 手元に無く origin にだけあるブランチ（取ってきた ref）
        sha = git(self.root, "rev-parse", "main").strip()
        git(self.root, "update-ref", "refs/remotes/origin/152-from-host", sha)
        git(self.root, "update-ref", "refs/remotes/origin/HEAD", sha)
        self.login_tree = os.path.join(self.root, ".claude", "worktrees", "feature-152-login")
        git(self.root, "worktree", "add", "--quiet", self.login_tree, "feature-152-login")

    def branches(self, kind, number, result="", *extra, cwd=None):
        args = ["branches", kind, str(number), "--cwd", cwd or self.root]
        if result:
            args += ["--result", result]
        return self.ccnavi(*args, *extra)

    def json_of(self, kind, number, result="", *extra, **kw):
        done = self.branches(kind, number, result, *extra, "--json", **kw)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return json.loads(done.stdout)

    def host(self, data):
        return write(os.path.join(self.root, "host.json"), json.dumps(data))

    def test_local_candidates_without_the_host(self):
        done = self.branches("issue", 152)
        self.assertEqual(done.returncode, 0, done.stderr)
        text = done.stdout
        self.assertIn("ホストは見ていない", text)
        lines = [line for line in text.splitlines() if line.startswith("候補 ")]
        names = [line.split()[1] for line in lines if "在りか=" in line]
        self.assertEqual(
            sorted(names), ["152-from-host", "feature-152-login", "fix/152-typo", "issue-152"]
        )
        login = next(line for line in lines if line.startswith("候補 feature-152-login "))
        self.assertIn("ワークツリー=.claude/worktrees/feature-152-login", login)
        self.assertIn("在りか=手元", login)
        remote = next(line for line in lines if line.startswith("候補 152-from-host "))
        self.assertIn("在りか=origin", remote)
        self.assertIn("候補 4 件", text)

    def test_tickets_with_the_issue_and_the_unapproved_branch_field(self):
        # 承認前の提案の branch: は使わない。候補は識別子のブランチ
        self.propose(
            "feature-152-login",
            with_branch(parent_text("feature-152-login", ["research"], issue=152), "topic/x"),
        )
        self.propose("feature-9-other", parent_text("feature-9-other", ["research"], issue=9))
        data = self.json_of("issue", 152)
        self.assertFalse(data["host"]["checked"])
        by_name = {c["branch"]: c for c in data["candidates"]}
        self.assertNotIn("topic/x", by_name)
        login = by_name["feature-152-login"]
        self.assertIn("ticket", login["sources"])
        self.assertIn("name", login["sources"])
        self.assertEqual([t["ticket"] for t in login["tickets"]], ["feature-152-login"])
        self.assertEqual(login["tickets"][0]["state"], "todo")
        self.assertFalse(login["tickets"][0]["approved"])
        self.assertEqual(login["worktrees"], [".claude/worktrees/feature-152-login"])
        # チケットに結び付く候補が先に並ぶ
        self.assertEqual(data["candidates"][0]["branch"], "feature-152-login")
        self.assertEqual([t["ticket"] for t in data["issue_tickets"]], ["feature-152-login"])

    def test_an_approved_parent_names_its_branch_field(self):
        # 承認済みの親は `branch:` のブランチを宣言する。移る前の識別子のブランチにも結び付ける
        text = with_branch(
            parent_text("feature-152-login", ["research"], issue=152), "feature/152-login"
        )
        write(
            os.path.join(self.login_tree, "wip", "proposals", "todo", "feature-152-login.md"),
            text,
        )
        git(self.login_tree, "add", "-A")
        git(self.login_tree, "commit", "--quiet", "-m", "propose")
        agreed = self.ccnavi("--agree", stdin="y\n")
        self.assertEqual(agreed.returncode, 0, agreed.stdout + agreed.stderr)
        data = self.json_of("issue", 152)
        by_name = {c["branch"]: c for c in data["candidates"]}
        named = by_name["feature/152-login"]
        self.assertEqual(named["sources"], ["ticket"])
        self.assertFalse(named["local"] or named["origin"])
        self.assertEqual(named["tickets"][0]["state"], "doing")
        self.assertTrue(named["tickets"][0]["approved"])
        self.assertEqual(
            [t["ticket"] for t in by_name["feature-152-login"]["tickets"]], ["feature-152-login"]
        )
        text = self.branches("issue", 152).stdout
        self.assertIn("候補 feature/152-login  在りか=まだ無い  由来=チケット", text)
        self.assertIn("チケット feature-152-login  承認済み  状態=doing", text)

    def test_host_merge_requests_join_the_candidates(self):
        result = self.host(
            {
                "checked": True,
                "host": "github.com",
                "repo": "acme/widgets",
                "mrs": [
                    {
                        "number": 7,
                        "branch": "topic/login",
                        "state": "open",
                        "url": "https://github.com/acme/widgets/pull/7",
                        "title": "Closes #152",
                        "fork": False,
                    },
                    {"number": "x", "branch": "bad"},
                ],
            }
        )
        data = self.json_of("issue", 152, result)
        self.assertTrue(data["host"]["checked"])
        topic = next(c for c in data["candidates"] if c["branch"] == "topic/login")
        self.assertEqual(topic["sources"], ["mr"])
        self.assertEqual(topic["mrs"][0]["number"], 7)
        self.assertFalse(topic["local"] or topic["origin"])
        self.assertNotIn("bad", [c["branch"] for c in data["candidates"]])
        text = self.branches("issue", 152, result).stdout
        self.assertIn("ホスト: github.com acme/widgets を見た", text)
        self.assertIn("候補 topic/login  在りか=ホストだけ  由来=MR  MR=!7(open)", text)

    def test_an_mr_lists_its_source_branch_only(self):
        result = self.host(
            {
                "checked": True,
                "host": "gitlab.com",
                "repo": "acme/widgets",
                "mrs": [{"number": 5, "branch": "issue-152", "state": "opened", "fork": True}],
            }
        )
        data = self.json_of("mr", 5, result)
        self.assertEqual([c["branch"] for c in data["candidates"]], ["issue-152"])
        self.assertTrue(data["candidates"][0]["local"])
        text = self.branches("mr", 5, result).stdout
        self.assertIn("MR=!5(opened,フォーク)", text)

    def test_tickets_with_the_mr_field_join_the_candidates(self):
        self.propose("feature-3-foo", with_mr(parent_text("feature-3-foo", ["research"]), 12))
        self.propose("feature-4-bar", with_mr(parent_text("feature-4-bar", ["research"]), 13))
        data = self.json_of("mr", 12)
        self.assertEqual([c["branch"] for c in data["candidates"]], ["feature-3-foo"])
        self.assertEqual(data["candidates"][0]["sources"], ["ticket"])
        self.assertEqual([t["ticket"] for t in data["issue_tickets"]], ["feature-3-foo"])
        self.assertEqual(self.json_of("mr", 99)["candidates"], [])
        # issue の枝は `mr:` を見ない
        self.assertEqual(self.json_of("issue", 12)["issue_tickets"], [])

    def test_an_mr_without_the_host_says_so_and_has_no_candidate(self):
        result = self.host({"checked": False, "reason": "origin が無い"})
        text = self.branches("mr", 5, result).stdout
        self.assertIn("ホストは見ていない（origin が無い）", text)
        self.assertIn("候補なし", text)

    def test_ticket_control_disabled_skips_the_tickets(self):
        self.propose("feature-152-login", parent_text("feature-152-login", ["research"], issue=152))
        data = self.json_of("issue", 152, "", "--ticket-control", "disable")
        self.assertFalse(data["tickets_checked"])
        self.assertEqual(data["issue_tickets"], [])

    def test_bad_arguments(self):
        for kind, number in (("pr", "1"), ("issue", "0"), ("issue", "x"), ("mr", "-1")):
            with self.subTest(kind=kind, number=number):
                self.assertEqual(self.branches(kind, number).returncode, 1)

    def test_outside_the_workspace(self):
        done = self.branches("issue", 1, cwd=os.path.dirname(self.root))
        self.assertEqual(done.returncode, 1)
        self.assertIn("ワークスペースの外", done.stderr)
