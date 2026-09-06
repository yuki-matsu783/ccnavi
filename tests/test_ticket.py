"""チケットによる作業範囲の制御の受入テスト。道具を外から叩いて応答だけを見る。

見るのは 4 つ。

1. 承認された範囲の外への書き込みが止まること
2. 承認していないチケットは 1 ミリも効かないこと。効かないことを毎回言うこと
3. 承認したあとにチケットを書き換えても範囲が広がらないこと
4. 承認の画面が、書けるようになる領域・前回との差分・リスクを出すこと

3 つ目がこの機能の要になっている。チケットはエージェントが書けるファイルなので、
そこを判定が直接読んでいたら、止められたエージェントが自分で範囲を伸ばせる。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 不備の無いルール 1 件。チケットの範囲とは別に、ルールが効き続けることを見る。
GUARD_RULE = {
    "id": "guard-config",
    "match": "Write|Edit|MultiEdit",
    "pattern": ".claude/ccnavi/*",
    "message": "ガード自身の設定です。利用者に依頼してください。",
}

# allow は置かない。チケットの範囲が「ここは聞かない」を作る側であることを
# 見たいので、ルールの側が先に許してしまうとその境目が見えなくなる。
RULES = {"version": 2, "deny": [GUARD_RULE]}


def ticket_text(name: str, *areas: str, title: str = "作業", why: str = "理由") -> str:
    """frontmatter を組む。設計 §9.1 の綴りをそのまま使う。"""
    body = ["---", f"ticket: {name}", f"title: {title}", "rationale: |", f"  {why}"]
    if areas:
        body += ["target_directories:", "  write:"]
        body += [f'    "{area}": allow' for area in areas]
    body += ["---", "", "## 作業内容", "本文は判定に影響しない。"]
    return "\n".join(body) + "\n"


def write(path: str, text: str) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def ccnavi(root: str, *args: str, stdin: str = "") -> subprocess.CompletedProcess:
    """道具を 1 回動かす。

    環境変数を落とすのは、このリポジトリが ccnavi を自分自身に仕掛けているため。
    落とさないと、テストがコードではなく走った機械のことを報告する。
    """
    environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
    return subprocess.run(
        [sys.executable, "-m", "ccnavi", "--root", root, *args],
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=ROOT,
        env=environment,
    )


class TicketTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = self.dir.name
        self.addCleanup(self.dir.cleanup)

        self.rules = write(os.path.join(self.root, "rules.yml"), json.dumps(RULES))
        self.ticket = os.path.join(self.root, ".current-ticket.md")
        self.ledger = os.path.join(self.root, "approvals.jsonl")

    def judge(self, path: str, tool: str = "Write") -> subprocess.CompletedProcess:
        """1 件のツール呼び出しを判定させる。"""
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": tool,
            "cwd": self.root,
            "tool_input": {"file_path": path},
        }
        return ccnavi(
            self.root,
            "--mode",
            "block",
            "--rules",
            self.rules,
            "--log",
            "",
            "--state",
            "",
            "--ticket",
            self.ticket,
            "--ledger",
            self.ledger,
            stdin=json.dumps(payload),
        )

    def approve(self, answer: str) -> subprocess.CompletedProcess:
        return ccnavi(
            self.root,
            "--approve",
            "--rules",
            self.rules,
            "--ticket",
            self.ticket,
            "--ledger",
            self.ledger,
            stdin=answer + "\n",
        )

    def test_no_ticket_leaves_everything_open(self):
        """チケットも台帳も無ければ、ルールに当たらない書き込みは通る。

        チケットによる制御は任意で、置かないプロジェクトの挙動を変えない。
        """
        result = self.judge(os.path.join(self.root, "anywhere", "x.py"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("DENY_TICKET_SCOPE", result.stdout)

    def test_unapproved_ticket_does_not_narrow_anything(self):
        """未承認のチケットは範囲を絞らない。絞っていないことを毎回言う。

        ここを「絞る」にすると、承認していない範囲でエージェントが止まり、
        誰も承認していない宣言が権限として効いてしまう。
        """
        write(self.ticket, ticket_text("PROJ-1", "src"))
        result = self.judge(os.path.join(self.root, "outside", "x.py"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("DENY_TICKET_SCOPE", result.stdout)
        self.assertIn("has not been approved", result.stdout)

    def test_approved_scope_stops_the_outside(self):
        """承認された範囲の外への書き込みは止まり、中は通る。"""
        write(self.ticket, ticket_text("PROJ-1", "src"))
        self.assertEqual(self.approve("y").returncode, 0)

        outside = self.judge(os.path.join(self.root, "docs", "x.md"))
        self.assertEqual(outside.returncode, 0, outside.stderr)
        self.assertIn("DENY_TICKET_SCOPE", outside.stdout)
        self.assertIn("PROJ-1", outside.stdout)
        # 止めるだけでは足りない。次に何をすればよいかを言う。
        self.assertIn("--approve", outside.stdout)

        inside = self.judge(os.path.join(self.root, "src", "deep", "x.py"))
        self.assertEqual(inside.returncode, 0, inside.stderr)
        self.assertNotIn("DENY_TICKET_SCOPE", inside.stdout)

    def test_editing_the_ticket_does_not_widen_the_scope(self):
        """承認後にチケットへ範囲を書き足しても、効く範囲は変わらない。

        この機能の要。台帳が権威で、作業ツリーのチケットは提案でしかない。
        """
        write(self.ticket, ticket_text("PROJ-1", "src"))
        self.assertEqual(self.approve("y").returncode, 0)

        # エージェントが自分で範囲を伸ばした形。
        write(self.ticket, ticket_text("PROJ-1", "src", "docs"))
        result = self.judge(os.path.join(self.root, "docs", "x.md"))
        self.assertIn("DENY_TICKET_SCOPE", result.stdout)
        # 書き換えたことに気づける文が要る。黙って効かないのがいちばん悪い。
        self.assertIn("no longer matches what was approved", result.stdout)

    def test_the_ticket_file_itself_stays_writable(self):
        """チケットのファイルは範囲の外でも書ける。

        塞ぐと、いちど承認した範囲から出る道が無くなる。
        """
        write(self.ticket, ticket_text("PROJ-1", "src"))
        self.assertEqual(self.approve("y").returncode, 0)
        result = self.judge(self.ticket)
        self.assertNotIn("DENY_TICKET_SCOPE", result.stdout)

    def test_rules_still_win_inside_the_scope(self):
        """範囲の中でも、ルールが守る場所には書けない。

        `.claude` は高影響領域なので、承認には識別子の入力が要る。
        """
        write(self.ticket, ticket_text("PROJ-1", ".claude"))
        self.assertEqual(self.approve("PROJ-1").returncode, 0)
        result = self.judge(os.path.join(self.root, ".claude", "ccnavi", "rules.json"))
        self.assertIn("guard-config", result.stdout)

    def test_approval_screen_leads_with_the_writable_area(self):
        """承認の画面が、書けるようになる領域・差分・リスクを出す。"""
        write(self.ticket, ticket_text("PROJ-1", "src", why="設定コンポーネントの分割"))
        result = self.approve("y")
        self.assertEqual(result.returncode, 0, result.stderr)
        head = result.stdout.index("書き込みが許される領域")
        self.assertLess(head, result.stdout.index("リスクスコア"))
        self.assertIn("src", result.stdout)
        self.assertIn("設定コンポーネントの分割", result.stdout)
        self.assertIn("前回の承認が無い", result.stdout)

    def test_second_approval_shows_the_difference(self):
        """2 本目の承認は、前回からの差分を出す。"""
        write(self.ticket, ticket_text("PROJ-1", "src"))
        self.approve("y")
        write(self.ticket, ticket_text("PROJ-2", "docs"))
        result = self.approve("y")
        self.assertIn("前回: PROJ-1", result.stdout)
        self.assertIn("+ docs/", result.stdout)
        self.assertIn("- src/", result.stdout)

    def test_high_risk_needs_the_identifier_typed(self):
        """リスクが高いチケットは、y ひとつでは承認できない。"""
        # .claude は高影響領域。この 1 件で HIGH に届く。
        write(self.ticket, ticket_text("PROJ-9", ".claude"))
        refused = self.approve("y")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("チケット識別子を入力", refused.stdout)
        self.assertFalse(os.path.exists(self.ledger))

        accepted = self.approve("PROJ-9")
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertTrue(os.path.exists(self.ledger))

    def test_refusing_writes_nothing(self):
        """承認しなければ台帳は増えない。"""
        write(self.ticket, ticket_text("PROJ-1", "src"))
        result = self.approve("n")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(os.path.exists(self.ledger))

    def test_scope_paths_cannot_escape(self):
        """`..` を含む範囲は受け付けない。"""
        write(self.ticket, ticket_text("PROJ-1", "../elsewhere"))
        result = self.approve("y")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("`..`", result.stderr)

    def test_ask_is_not_read_as_allow(self):
        """このビルドに ask は無いので、ask の範囲を allow に読み替えない。"""
        text = "\n".join(
            [
                "---",
                "ticket: PROJ-1",
                "title: 作業",
                "target_directories:",
                "  write:",
                '    "src": ask',
                "---",
            ]
        )
        write(self.ticket, text)
        result = self.approve("y")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("allow の範囲が 1 件も無い", result.stderr)

    def test_lint_names_an_unapproved_ticket(self):
        """検証が、未承認のチケットを名指しする。"""
        write(self.ticket, ticket_text("PROJ-1", "src"))
        result = ccnavi(
            self.root,
            "--lint",
            "--rules",
            self.rules,
            "--mode",
            "block",
            "--ticket",
            self.ticket,
            "--ledger",
            self.ledger,
        )
        self.assertIn("PROJ-1 は未承認", result.stdout)


if __name__ == "__main__":
    unittest.main()
