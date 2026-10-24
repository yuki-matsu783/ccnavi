"""セッション開始で示す、却下された要求（REQ-SES-02）の受入テスト。

承認済みチケットの要求のうち、ルールの deny や上位（親の範囲・フェーズ定義の上限）に
退けられるものを、SessionStart で要求・理由・出所つきで示す。情報源は承認時の記録ではなく、
承認済みチケットとルールからの再計算。REQ-SES-03（上限を超えた許可を区別して示す）は作らない
ので、ここでは見ない。見るのは 7 つ。

1. 項がルールの deny にヒットする: 書かれた glob・deny の message・ルールの id が出る
   （allow と ask の両方）
2. 子の項が親の範囲を超える: 出所は「親 <親>」。
   フェーズ定義の上限を超える: 出所は「フェーズ定義 <名前>」
3. 却下が多いときは件数と先頭の数件に絞り、`--explain` へ誘う
4. 却下が無ければ何も足さない
5. cwd のツリーに結び付くチケットだけを見る。ワークスペースルートからは件数の 1 行まで
6. サブエージェント（agent_id がある）には出さない
7. ユーザに見える `systemMessage` とモデルに渡る `additionalContext` の両方に出る。
   終了コードは変わらない

却下の glob は、deny のルールの glob と同じ場所を指す書き方で揃える（ルールは `*/src/secret/*`、
チケットは `src/secret/*`）。重なりの判定の細部はここでは固定しない。
"""

from __future__ import annotations

import json
import os
import unittest

from tests import common_path
from tests.ticket.test_phases import PhaseHarness
from tests.ticket.test_ticket import git, write
from tests.ticketsample import RULES, child_text, parent_text

REJECT_WORD = "却下"
MESSAGE = "秘密の置き場は触れません。"
SECRET = "src/secret/*"
DENY = {
    "id": "no-secret",
    "match": "Write|Edit",
    "glob": "*/src/secret/*",
    "message": MESSAGE,
}
EXPLAIN = "--explain"


def with_ask(text: str, *globs: str) -> str:
    """提案の本文に ask の項を足す（`started_at` の前に差し込む）。"""
    lines = ["ask:"]
    for g in globs:
        lines += ["  - match: Write|Edit", f'    glob: "{g}"']
    return text.replace('started_at: ""', "\n".join(lines) + '\nstarted_at: ""', 1)


class SessionRejectsTest(PhaseHarness):
    def setUp(self):
        super().setUp()
        rules = {**RULES, "deny": [*RULES["deny"], DENY]}
        write(common_path(self.root, "rules"), json.dumps(rules))

    # ---- 道具

    def session(self, cwd=None, agent_id=""):
        """SessionStart を打って (終了コード, systemMessage, additionalContext) を返す。"""
        payload = {
            "hook_event_name": "SessionStart",
            "cwd": cwd or self.parent_tree,
            "session_id": "s1",
            "source": "startup",
        }
        if agent_id:
            payload["agent_id"] = agent_id
        result = self.ccnavi("--mode", "enable", stdin=json.dumps(payload))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        if not result.stdout.strip():
            return result.returncode, "", ""
        out = json.loads(result.stdout)
        context = out.get("hookSpecificOutput", {}).get("additionalContext", "")
        return result.returncode, out.get("systemMessage", ""), context

    def approve_all(self, *names):
        shown = self.ccnavi("--agree", "--preview", "--json")
        digest = json.loads(shown.stdout)["digest"]
        done = self.ccnavi("user", "agree", "--yes", ",".join(names), "--digest", digest, "--json")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        copies = json.loads(done.stdout)["copies"]
        for name in names:
            found = [c for c in copies if os.path.basename(c) == name + ".md"]
            self.assertTrue(found, f"{name} が承認されていない\n{done.stdout}")

    def approved_parent(self, allow, plan=("research", "design"), name="i0001"):
        """親を提案して承認し、着手する。"""
        self.propose(name, parent_text(name, list(plan), allow=allow))
        self.commit_parent()
        self.approve_all(name)
        self.start_parent(name)

    def reject_by_deny(self):
        """親の項が deny にヒットする状態。"""
        self.approved_parent(("src/*", "wip/*", SECRET))

    def shown(self, cwd=None):
        _, system, context = self.session(cwd)
        return system, context

    # ---- 1. deny にヒットする

    def test_denyにヒットする項は要求と理由と出所が出る(self):
        self.reject_by_deny()

        system, context = self.shown()

        for text in (system, context):
            self.assertIn(REJECT_WORD, text)
            self.assertIn(SECRET, text)  # 要求（書かれた glob）
            self.assertIn(MESSAGE, text)  # 理由（deny の message）
            self.assertIn("no-secret", text)  # 出所（ルールの id）

    def test_askの項がdenyにヒットするときも出る(self):
        self.propose("i0001", with_ask(parent_text("i0001", ["research", "design"]), SECRET))
        self.commit_parent()
        self.approve_all("i0001")
        self.start_parent()

        system, context = self.shown()

        for text in (system, context):
            self.assertIn(SECRET, text)
            self.assertIn(MESSAGE, text)

    # ---- 2. 上位の限りを超える

    def test_子の項が親の範囲を超えると出所は親になる(self):
        self.approved_parent(("wip/*",))
        # 親の範囲は wip/*。子が docs/* を求める。
        self.propose("i0001-01-01", child_text("i0001-01-01", "i0001", 1, ("docs/*",), False))
        self.commit_parent()
        self.approve_all("i0001-01-01")
        tree = self.worktree("i0001-01-01", "i0001")
        self.assertEqual(self.ccnavi("ticket", "start", "i0001-01-01").returncode, 0)

        system, context = self.shown(tree)

        for text in (system, context):
            self.assertIn("docs/*", text)
            self.assertIn("親 i0001", text)

    def test_子の項がフェーズ定義の上限を超えると出所は定義になる(self):
        # research の上限は wip/research/*。親の範囲（src/*）には収まるが、定義を超える。
        self.approved_parent(("src/*", "wip/*"))
        self.propose("i0001-01-01", child_text("i0001-01-01", "i0001", 1, ("src/*",), False))
        self.commit_parent()
        self.approve_all("i0001-01-01")
        tree = self.worktree("i0001-01-01", "i0001")
        self.assertEqual(self.ccnavi("ticket", "start", "i0001-01-01").returncode, 0)

        system, context = self.shown(tree)

        for text in (system, context):
            self.assertIn("src/*", text)
            self.assertIn("フェーズ定義 research", text)
            # 出所に親は出ない。context に載る作業中の親の一覧（親 i0001 の行）は別の話
            self.assertNotIn("親 i0001", text.partition("[ccnavi] このPCで作業中の親:")[0])

    # ---- 3. 絞る

    def test_多いときは件数と先頭の数件に絞りexplainへ誘う(self):
        globs = [f"src/secret/{i:02d}/*" for i in range(12)]
        self.approved_parent(("src/*", "wip/*", *globs))

        system, context = self.shown()

        for text in (system, context):
            self.assertIn("12 件", text)
            self.assertIn(globs[0], text)
            self.assertNotIn(globs[-1], text)
            shown = [g for g in globs if g in text]
            self.assertLessEqual(len(shown), 5, text)
            self.assertIn(EXPLAIN, text)
        # 常駐の文脈なので、却下の段落は短い。
        part = [p for p in context.split("\n\n") if REJECT_WORD in p]
        self.assertLessEqual(len(part), 1, context)
        self.assertLessEqual(len(part[0].splitlines()), 12, part[0])

    # ---- 4. 無ければ出さない

    def test_却下が無ければ何も足さない(self):
        self.approved_parent(("src/*", "wip/*"))

        system, context = self.shown()

        self.assertNotIn(REJECT_WORD, system)
        self.assertNotIn(REJECT_WORD, context)

    # ---- 5. 見るツリー

    def test_ほかのツリーの却下は出さない(self):
        self.approved_parent(("src/*", "wip/*"))
        other = self.worktree("i0002", "main")
        write(
            os.path.join(other, "wip", "proposals", "todo", "i0002.md"),
            parent_text("i0002", ["research", "design"], allow=("src/*", SECRET)),
        )
        git(other, "add", "-A")
        git(other, "commit", "--quiet", "-m", "propose")
        self.approve_all("i0002")

        system, context = self.shown()  # i0001 のツリー

        self.assertNotIn(SECRET, system + context)
        self.assertNotIn(REJECT_WORD, system + context)

    def test_ワークスペースルートからは件数の1行まで(self):
        self.reject_by_deny()

        system, context = self.shown(self.root)

        for text in (system, context):
            self.assertIn(REJECT_WORD, text)
            self.assertIn("1 件", text)
            self.assertNotIn(SECRET, text)
            self.assertNotIn(MESSAGE, text)
        lines = [ln for ln in context.splitlines() if REJECT_WORD in ln]
        self.assertEqual(len(lines), 1, context)

    # ---- 6. サブエージェント

    def test_サブエージェントには出さない(self):
        self.reject_by_deny()

        code, system, context = self.session(agent_id="a1")

        self.assertEqual(code, 0)
        self.assertNotIn(REJECT_WORD, system + context)
        self.assertNotIn(SECRET, system + context)

    # ---- 7. 出力の形

    def test_判定と終了コードは変わらない(self):
        self.reject_by_deny()

        code, system, context = self.session()

        self.assertEqual(code, 0)
        self.assertTrue(system and context, "systemMessage と additionalContext の両方に出す")

    def test_案内を出してもdenyにヒットする書き込みは止まる(self):
        self.reject_by_deny()
        self.session()

        target = os.path.join(self.parent_tree, "src", "secret", "x.py")
        result = self.hook("PreToolUse", "Write", self.parent_tree, file_path=target)
        self.assertIn("deny", result.stdout)

    # ---- 8. `--explain` の全件の節

    def explained(self):
        result = self.ccnavi(EXPLAIN)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def section_of(self, out):
        """節の見出しから次の見出しまで。"""
        head = "■ 上位の設定に却下された要求"
        self.assertIn(head, out)
        body = out.split(head, 1)[1].split("\n■", 1)[0]
        return body.splitlines()[1:]

    def test_explainは却下を全件出す(self):
        globs = [f"src/secret/{i:02d}/*" for i in range(12)]
        self.approved_parent(("src/*", "wip/*", *globs))

        lines = self.section_of(self.explained())

        rows = [ln for ln in lines if "src/secret/" in ln]
        self.assertEqual(len(rows), 12, "\n".join(lines))
        for g in globs:
            self.assertEqual(len([ln for ln in rows if g in ln]), 1, g)

    def test_explainの1行に要求と理由と出所が入る(self):
        self.reject_by_deny()

        lines = self.section_of(self.explained())

        rows = [ln for ln in lines if SECRET in ln]
        self.assertEqual(len(rows), 1, "\n".join(lines))
        self.assertIn("i0001", rows[0])  # どのチケットか
        self.assertIn(MESSAGE, rows[0])  # 理由
        self.assertIn("no-secret", rows[0])  # 出所

    def test_explainの節の末尾に近似であることを添える(self):
        self.reject_by_deny()

        text = "\n".join(self.section_of(self.explained()))

        self.assertIn("近似", text)

    def test_explainは上位の出所も出す(self):
        self.approved_parent(("wip/*",))
        self.propose("i0001-01-01", child_text("i0001-01-01", "i0001", 1, ("docs/*",), False))
        self.commit_parent()
        self.approve_all("i0001-01-01")
        self.worktree("i0001-01-01", "i0001")
        self.assertEqual(self.ccnavi("ticket", "start", "i0001-01-01").returncode, 0)

        text = "\n".join(self.section_of(self.explained()))

        self.assertIn("docs/*", text)
        self.assertIn("親 i0001", text)

    def test_explainは却下が無ければ節を出さない(self):
        self.approved_parent(("src/*", "wip/*"))

        out = self.explained()

        self.assertNotIn("上位の設定に却下された要求", out)

    def test_explainのjsonに却下の欄は無い(self):
        self.reject_by_deny()

        result = self.ccnavi(EXPLAIN, "--json")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn(REJECT_WORD, result.stdout)
        self.assertNotIn(MESSAGE, result.stdout)


if __name__ == "__main__":
    unittest.main()
