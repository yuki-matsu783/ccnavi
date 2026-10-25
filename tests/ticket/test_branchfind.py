"""issue・MR に紐づくブランチを探す仕組みの受入テスト。

見るのは 2 つ。

1. UserPromptSubmit: 依頼文に issue・MR の指定があれば、`ccnavi-start.sh` を打って着手させ、
   複数候補（終了コード 3）はユーザに確かめ、ホストに届かない（4）ときは MCP で代行させる指示が
   `additionalContext` に載る。表記はワークスペースルートの絶対パスから書く。
   指定が無い・外れ・チケット制御が disable のときは載らない。判定（止める・聞く）は返さない
2. サブコマンド `ccnavi branches <issue|mr> <番号> --result <json>`: 手元の候補（名前に番号を含む
   ブランチ・ワークツリー・`issue:` を持つチケット）を集め、sh が書いたホストの結果と合わせて出す。
   ホストの結果が無いときは「ホストは見ていない」と言う。チケットは識別子になるブランチ
   （`/` を `-` に置き換えて識別子と一致するもの）に結び付ける
"""

from __future__ import annotations

import json
import os

from ccnavi.infra import settings
from tests.ticket.test_phases import PhaseHarness, git, write
from tests.ticketsample import parent_text


def with_mr(text, mr):
    """提案の `mr:` を差し替える（`parent_text` は `mr: 1` を持つ）。"""
    return text.replace("\nmr: 1\n", f"\nmr: {mr}\n", 1)


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
        # 短い指示。終了コードごとの説明は ccnavi-start.sh の出力の「次:」に任せる
        self.assertIn(f"着手前に '{sh} --check'、続けて", said)
        self.assertIn("出力の「次:」に従う", said)
        self.assertIn("Draft MR とワークツリーができる", said)
        # 実装と調査の両方の分岐が入る
        self.assertIn("実装の依頼なら", said)
        self.assertIn("調べ・質問に答えるだけなら、チケット作業にせずそのまま答えてよい", said)
        self.assertIn("どちらか分からなければ、ユーザに聞く", said)
        for gone in ("終了コード 3", "終了コード 4", "返事を待つ", "<先頭の語>-<番号>-<slug>"):
            self.assertNotIn(gone, said)
        self.assertNotIn("ccnavi-branches.sh", said)

    def test_a_number_inside_a_word_gets_no_instruction(self):
        self.assertEqual(self.prompt("手順#2を見直して"), "")
        self.assertEqual(self.prompt("`#12` を直して", session="s2"), "")

    def test_the_short_instruction_has_both_branches(self):
        said = self.prompt("#1 を直して")
        self.assertIn("実装の依頼なら", said)
        self.assertIn("調べ・質問に答えるだけなら", said)
        self.assertLess(len(said.splitlines()), 4)

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
        text = parent_text("feature-152-login", ["research"], issue=152)
        self.propose_own("feature-152-login", text)
        self.approve()
        said = self.prompt("#152 を直して")
        self.assertIn("すでに feature-152-login(doing)", said)
        self.assertIn("--check を最初に打つ", said)
        self.assertIn("結び付いている", said)
        self.assertIn("実装の依頼なら、そのチケットのワークツリー（", said)
        self.assertIn("説明・調査だけなら、作業場所を変えずに答えてよい", said)
        self.assertNotIn("ccnavi-start.sh --issue", said)
        # 結び付かない番号は今までどおり
        self.assertIn("--issue 9", self.prompt("#9 を直して", session="s5"))

    def test_a_branch_name_with_the_number_is_not_a_mr_binding(self):
        text = parent_text("fix-12-foo", ["research"], issue=3)
        self.propose_own("fix-12-foo", text)
        self.approve()
        said = self.prompt("MR !12 を見て")
        self.assertIn("--mr 12", said)
        self.assertNotIn("結び付いている", said)

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

    def test_a_web_session_branch_with_the_digits_in_its_suffix_is_not_a_candidate(self):
        git(self.root, "branch", "claude/tender-volta-qmy99q", "main")
        data = self.json_of("issue", 99)
        self.assertEqual(data["candidates"], [])
        text = self.branches("issue", 99).stdout
        self.assertNotIn("qmy99q", text)
        self.assertIn("候補なし", text)

    def test_a_candidate_found_only_by_name_is_called_a_weak_ground(self):
        text = self.branches("issue", 152).stdout
        self.assertIn("案内: ", text)
        self.assertIn("弱い根拠", text)
        self.assertNotIn("\n次:", text)
        # チケットに結び付く候補だけなら言わない
        self.propose("feature-153-slash", parent_text("feature-153-slash", ["research"], issue=153))
        self.assertNotIn("弱い根拠", self.branches("issue", 153).stdout)

    def test_tickets_with_the_issue_join_the_candidates(self):
        # 候補は識別子になるブランチ
        self.propose("feature-152-login", parent_text("feature-152-login", ["research"], issue=152))
        self.propose("feature-9-other", parent_text("feature-9-other", ["research"], issue=9))
        data = self.json_of("issue", 152)
        self.assertFalse(data["host"]["checked"])
        by_name = {c["branch"]: c for c in data["candidates"]}
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

    def test_a_ticket_binds_to_the_branch_that_converts_to_its_identifier(self):
        # 識別子は feature-153-slash。ブランチは feature/153-slash（`/` を `-` に置き換えると一致）
        git(self.root, "branch", "feature/153-slash", "main")
        self.propose("feature-153-slash", parent_text("feature-153-slash", ["research"], issue=153))
        data = self.json_of("issue", 153)
        by_name = {c["branch"]: c for c in data["candidates"]}
        named = by_name["feature/153-slash"]
        self.assertIn("ticket", named["sources"])
        self.assertTrue(named["local"])
        self.assertEqual([t["ticket"] for t in named["tickets"]], ["feature-153-slash"])
        self.assertNotIn("feature-153-slash", by_name)
        text = self.branches("issue", 153).stdout
        self.assertIn("チケット feature-153-slash", text)

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
