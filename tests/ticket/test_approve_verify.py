"""`--approve --preview --verify`（承認を頼む前の確認）の受入テスト。

エージェントが提案を書いたあと、人に承認を依頼する前に自分で確かめる枝
（REQ-APV-13）と、書いた回にそれを伝える組み込みの案内（REQ-APV-14）。
見るのは 8 つ。

1. 承認できる状態なら 0（はい）で返り、識別子ごとに「通る」と出る。置かない
2. 承認の対象にしない提案があれば 3（いいえ）で返り、その理由が本文に出る
3. 承認待ちが無ければ 3。提案の置き場を間違えた回がここに出る
4. 承認待ちに無い識別子を指定すれば 3（絞りは `--approve` と同じ意味）
5. 範囲の超過だけなら 0。承認は止まらないので通るが、書けないことは行に添える
6. `--json` は `--preview --json` と同じ形に `verify` を足したもの。使い方の誤りは 1
7. `todo/` に提案を書くと、確認の案内が 1 つの文脈で 1 度だけ届く。案内は判定の表に
   足さないので、どの権限モードでも判定は変わらない
8. `--lint` が同じ提案について同じことを言う（承認と同じ関数を通す）
"""

from __future__ import annotations

import json
import os
import tempfile

from tests.ticket.test_phases import PhaseHarness, child_text, parent_text
from tests.ticket.test_ticket import write

APPROVE_VERSION = 1


def decision(result):
    """応答に載った判定。判定を返していなければ None（ccnavi は止めていない）。"""
    if not result.stdout.strip():
        return None
    return json.loads(result.stdout).get("hookSpecificOutput", {}).get("permissionDecision")


def context(result):
    """モデルへ渡した文。判定とは別の欄で、止めた回にも通した回にも載る。"""
    if not result.stdout.strip():
        return ""
    return json.loads(result.stdout).get("hookSpecificOutput", {}).get("additionalContext") or ""


# 答えの終了コード。0 = はい、3 = いいえ。1 は使い方と設定の誤りで、答えではない。
ANSWER_NO = 3


class ApproveVerifyTest(PhaseHarness):
    def verify(self, *extra):
        """`--approve --preview --verify`。ハーネスの `check`（review check）とは別物なので、
        名前を分ける（同じ名前で上書きすると、レビュー絡みのテストを足した回に黙って入れ替わる）。"""
        return self.ccnavi("--approve", "--preview", "--verify", *extra)

    # ---- 1. 通る

    def test_pending_batch_passes_and_places_nothing(self):
        self.propose("i0001", parent_text("i0001", ["research", "design"]))
        self.propose("i0001-01", child_text("i0001-01", "i0001", 1, ("wip/research/*",), False))
        self.commit_parent()

        result = self.verify()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("i0001", result.stdout)
        self.assertIn("通る", result.stdout)
        self.assertIn("承認を依頼してよい", result.stdout)
        self.assertNotIn("落ちる", result.stdout)
        # 確かめただけ。承認済みチケットは置かれていない。
        self.assertFalse(os.path.exists(os.path.join(self.approved, "doing", "i0001.md")))
        self.assertFalse(os.path.exists(os.path.join(self.approved, "doing", "i0001-01.md")))

    def test_check_narrowed_to_one_id(self):
        """絞りは `--approve` と同じ意味。指定した 1 件だけを見る。"""
        self.propose("i0001", parent_text("i0001", ["research"]))
        self.commit_parent()

        result = self.verify("i0001")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("指定の 1 件", result.stdout)

    # ---- 2. 落ちる提案がある

    def test_a_rejected_proposal_fails_the_check_with_its_reason(self):
        self.propose("i0001", parent_text("i0001", ["research", "design"]))
        # 計画に無い番号の子。承認の対象にしない側に載る。
        self.propose("i0001-05", child_text("i0001-05", "i0001", 5, ("wip/research/*",)))
        self.commit_parent()

        result = self.verify()
        self.assertEqual(result.returncode, ANSWER_NO, result.stdout + result.stderr)
        self.assertIn("i0001-05", result.stdout)
        self.assertIn("落ちる", result.stdout)
        self.assertIn("計画に無い", result.stdout)
        self.assertIn("直してから", result.stdout)
        # 通るほうも同じ画面に出る。直す相手が分かるように。
        self.assertIn("通る", result.stdout)

    # ---- 3. 承認待ちが無い

    def test_nothing_pending_is_a_failed_check(self):
        result = self.verify()
        self.assertEqual(result.returncode, ANSWER_NO, result.stdout + result.stderr)
        self.assertIn("承認待ちのチケットは無い", result.stdout)
        self.assertIn("todo/", result.stdout)

    # ---- 4. 承認待ちに無い識別子

    def test_an_unknown_id_is_a_failed_check(self):
        self.propose("i0001", parent_text("i0001", ["research"]))
        self.commit_parent()

        result = self.verify("i0002")
        self.assertEqual(result.returncode, ANSWER_NO, result.stdout + result.stderr)
        self.assertIn("承認待ちに無い", result.stdout)

    # ---- 5. 範囲の超過は落とさない

    def test_scope_overflow_passes_but_is_shown(self):
        self.propose("i0001", parent_text("i0001", ["research", "design"]))
        # 種類（調査）の範囲を超える子。承認は止まらず、判定が切り詰める。
        self.propose("i0001-01", child_text("i0001-01", "i0001", 1, ("wip/design/*",)))
        self.commit_parent()

        result = self.verify()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("承認しても書けない", result.stdout)
        self.assertIn("承認を依頼してよい", result.stdout)

    # ---- 6. JSON

    def test_json_is_the_preview_body_with_the_answer(self):
        self.propose("i0001", parent_text("i0001", ["research"]))
        self.commit_parent()

        result = self.verify("--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        body = json.loads(result.stdout)
        self.assertEqual(body["version"], APPROVE_VERSION)
        self.assertEqual([b["ticket"] for b in body["batch"]], ["i0001"])
        self.assertTrue(body["digest"])
        self.assertEqual(body["verify"], {"ok": True, "reason": "ok"})

    def test_json_says_why_it_failed(self):
        """読めない提案しか無ければ、承認待ちが 1 件も無いのと同じ（そこで落ちる）。"""
        write(os.path.join(self.parent_tree, "wip", "proposals", "todo", "broken.md"), "---\n: :\n")
        self.commit_parent()

        result = self.verify("--json")
        self.assertEqual(result.returncode, ANSWER_NO, result.stdout + result.stderr)
        body = json.loads(result.stdout)
        self.assertEqual(body["verify"], {"ok": False, "reason": "nothing-pending"})
        self.assertTrue(any("broken.md" in p for p in body["problems"]))

    def test_a_broken_proposal_elsewhere_does_not_fail_a_sound_one(self):
        """読めない提案は終了コードを動かさない。`--approve` もそこでは落ちないから。

        走査は絞る前の全ツリーを見るので、ここで落とすと、他のセッションの書きかけ 1 本で
        「確かめでは 1、承認は 0」になる。黙らせもしない（自分が書いた 1 本かもしれない）。
        """
        self.propose("i0001", parent_text("i0001", ["research"]))
        write(os.path.join(self.parent_tree, "wip", "proposals", "todo", "broken.md"), "---\n: :\n")
        self.commit_parent()

        result = self.verify()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("読めなかったファイル", result.stdout)
        self.assertIn("broken.md", result.stdout)
        self.assertIn("承認を依頼してよい", result.stdout)
        # 同じ状態で本物の承認も通る。確かめと承認の答えが割れないことが要点。
        approved = self.ccnavi("--approve", stdin="y\n")
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.approved, "doing", "i0001.md")))

    def test_verify_needs_preview(self):
        """`--verify` は `--preview` に相乗りする。単独ではエージェントが打てない。

        使い方の誤りは 1。答えの「いいえ」（3）とは分ける。読む側が取り違えると、
        直すものが無いのに提案を直しに行く。
        """
        result = self.ccnavi("--approve", "--verify")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertNotEqual(result.returncode, ANSWER_NO)
        self.assertIn("--preview", result.stderr)

    # ---- 8. --lint が同じことを言う

    def test_lint_says_what_the_verify_says(self):
        """承認で落ちるものを数える経路は 1 本（`approval.candidates`）。

        以前は `--lint` だけが `approval.validate` を当てていて、順序で落ちる子・計画に
        無い番号・`project:` の食い違い・改版の検査に無言だった。同じ事実を数える経路が
        2 本あると、片方が黙って弱くなる。`--lint` は severity の体系で終わるので、
        承認で落ちる提案（error）があれば非ゼロで終わる。
        """
        self.propose("i0001", parent_text("i0001", ["research", "design"]))
        # 計画に無い番号の子。承認の対象にしない側に載る。
        self.propose("i0001-05", child_text("i0001-05", "i0001", 5, ("wip/research/*",)))
        self.commit_parent()

        verified = self.verify()
        self.assertEqual(verified.returncode, ANSWER_NO, verified.stdout)
        self.assertIn("計画に無い", verified.stdout)

        lint = self.ccnavi("--lint")
        self.assertNotEqual(lint.returncode, 0, lint.stdout + lint.stderr)
        said = lint.stdout + lint.stderr
        self.assertIn("i0001-05: ", said)
        self.assertIn("計画に無い", said)

    # ---- 7. 書いた回に案内が届く

    def test_writing_a_proposal_tells_the_agent_to_check_first(self):
        target = os.path.join(self.parent_tree, "wip", "proposals", "todo", "i0002.md")
        first = self.hook("PreToolUse", "Write", self.parent_tree, file_path=target)
        self.assertIn("--approve --preview --verify", context(first))
        self.assertIn("承認を依頼する前に", context(first))

    def test_the_notice_comes_once_per_context(self):
        todo = os.path.join(self.parent_tree, "wip", "proposals", "todo")
        first = self.hook("PreToolUse", "Write", self.parent_tree, file_path=todo + "/i0002.md")
        self.assertIn("--approve --preview --verify", context(first))
        again = self.hook("PreToolUse", "Write", self.parent_tree, file_path=todo + "/i0003.md")
        self.assertNotIn("--approve --preview --verify", context(again))

    def test_the_notice_changes_no_verdict(self):
        """案内は判定の表に足さない。だから `todo/` の扱いは、案内を入れる前と同じ。

        表に allow を 1 本足す形も試したが、`todo/` が「ccnavi が言及する場所」になり、
        どのタイプも言及しないときの倒し方（judge.undeclared_verdict）を通らなくなる。
        確認できる者が居ないモードの deny も、知らない綴りのモードを ask に倒す既定も、
        そこだけ外れていた（ADR-0059）。**同じ場所とどのルールも言及しない場所が、
        どの権限モードでも同じ判定になること**を杭にする。文は届いたままであることも見る。
        """
        todo = os.path.join(self.parent_tree, "wip", "proposals", "todo", "i0002.md")
        other = os.path.join(self.parent_tree, "src", "keep.py")
        for mode, verdict in (
            ("bypassPermissions", "deny"),
            ("dontAsk", "deny"),
            ("unknownMode", "ask"),
        ):
            with self.subTest(permission_mode=mode):
                inside = self.hook(
                    "PreToolUse", "Write", self.parent_tree, permission_mode=mode, file_path=todo
                )
                beyond = self.hook(
                    "PreToolUse", "Write", self.parent_tree, permission_mode=mode, file_path=other
                )
                self.assertEqual(decision(inside), verdict, inside.stdout)
                self.assertEqual(decision(beyond), verdict, beyond.stdout)
                # 止めた回にも文は届く（判定とは別の欄）。1 つの文脈で 1 度だけなので、
                # 最初の 1 回だけを見る。
                if mode == "bypassPermissions":
                    self.assertIn("--approve --preview --verify", context(inside))

    def test_the_notice_stays_inside_the_workspace(self):
        """ワークスペースの外に同じ並びを掘っても、提案を書いたことにはしない。

        当てる式はワークスペースルートで留めてある。ツリー（ワークツリー・プロジェクト）は
        どれもルートの下なので、正しい置き場は全部入り、外は入らない。
        """
        elsewhere = os.path.join(tempfile.gettempdir(), "wip", "proposals", "todo", "evil.sh")
        result = self.hook("PreToolUse", "Write", self.parent_tree, file_path=elsewhere)
        self.assertNotIn("--approve --preview --verify", context(result))

    def test_the_notice_does_not_reach_other_places(self):
        other = os.path.join(self.parent_tree, "src", "keep.py")
        result = self.hook("PreToolUse", "Write", self.parent_tree, file_path=other)
        self.assertNotIn("--approve --preview --verify", context(result))
