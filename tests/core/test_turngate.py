"""ターン分離の関門（`hook/turngate.py`）の部品。

宣言の表を直接渡して確かめる。組み込みの 1 件（`integrate` → `integrate-done`）のほかに、
`integrate` と関係の無い架空の組を使い、部品が特定の組に依存していないことを見る。

見るのは次の 5 つ。

- 同じ発言の中で A→B は止まり、発言を 1 回はさむと通る。通ると記録が消える
- キーが違う組、A の記録が無い B は対象外
- 条件の書き方（regex・glob・but・キーの解けない書き方）と、宣言の検査
- 組み込みの宣言が、`integrate` と `integrate-done` の実際の引数の形を読むこと
- 記録が壊れている・読めない・書けないときは閉じる側に倒れ、文面が次の一手を言うこと
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from ccnavi.hook import turngate
from ccnavi.hook.turngate import BUILTIN, Cond, Gate
from ccnavi.records import userprompt
from tests import put_messages

# integrate と関係の無い架空の組。「copy-in <名前>」のあと、発言をはさむまで
# 「copy-out <名前>」を打てない。
COPY = Gate(
    id="copy-out",
    first=Cond(regex=r"\bcopy-in\s+(?P<key>\S+)"),
    second=Cond(regex=r"\bcopy-out\s+(?P<key>\S+)"),
    first_name="copy-in",
    second_name="copy-out",
    message="DENY_NEEDS_USER_TURN__COPY",
    key_name="<名前>",
)
# キーを持たない架空の組（glob と but）。「deploy」のあと、発言をはさむまで「promote」を打てない。
DEPLOY = Gate(
    id="promote",
    first=Cond(glob="*deploy*", but=(r"deploy\s+--dry-run",)),
    second=Cond(glob="*promote*"),
    first_name="deploy",
    second_name="promote",
    message="DENY_NEEDS_USER_TURN__PROMOTE",
)
GATES = (COPY, DEPLOY)

SESSION = "s1"


class TurnGateTestCase(unittest.TestCase):
    def setUp(self):
        self.state = tempfile.mkdtemp(prefix="ccnavi-turngate-")
        self.addCleanup(shutil.rmtree, self.state, ignore_errors=True)
        self.clock = 1000.0

    def tick(self):
        self.clock += 10.0
        return self.clock

    def a(self, command, gates=GATES, session=SESSION, call="c1"):
        """A として打った（実行前に記録する）。止められたら Stop を返す。"""
        texts = [command]
        return turngate.check(gates, texts, self.state, session) or turngate.note(
            gates, texts, self.state, session, call, now=self.tick()
        )

    def b(self, command, gates=GATES, session=SESSION):
        return turngate.check(gates, [command], self.state, session)

    def speak(self, session=SESSION):
        userprompt.note(self.state, session, now=self.tick())

    def files(self):
        return sorted(n for n in os.listdir(self.state) if n.startswith(turngate.PREFIX))


class FlowTest(TurnGateTestCase):
    def test_同じ発言の中で_A_のあとの_B_は止まる(self):
        self.speak()
        self.assertIsNone(self.a("copy-in foo"))

        got = self.b("copy-out foo")

        self.assertIsNotNone(got)
        self.assertEqual(got.reason, turngate.NO_USER_TURN)
        self.assertEqual(got.gate.id, "copy-out")
        self.assertEqual(got.key, "foo")

    def test_発言を_1_回はさむと通る(self):
        self.a("copy-in foo")
        self.speak()

        self.assertIsNone(self.b("copy-out foo"))

    def test_通ったら記録が消え_次の_A_からまた数える(self):
        self.a("copy-in foo")
        self.speak()
        self.assertIsNone(self.b("copy-out foo"))
        self.assertEqual(self.files(), [])
        # 記録が無いので、もう 1 度打っても対象外。
        self.assertIsNone(self.b("copy-out foo"))

        self.a("copy-in foo", call="c2")
        self.assertIsNotNone(self.b("copy-out foo"))
        self.speak()
        self.assertIsNone(self.b("copy-out foo"))

    def test_発言が無いまま何度打っても止まる(self):
        self.a("copy-in foo")

        for _ in range(3):
            self.assertIsNotNone(self.b("copy-out foo"))

    def test_A_の前の発言は数えない(self):
        self.speak()
        self.a("copy-in foo")

        self.assertIsNotNone(self.b("copy-out foo"))

    def test_キーが違う組は対象外(self):
        self.a("copy-in foo")

        self.assertIsNone(self.b("copy-out bar"))
        self.assertIsNotNone(self.b("copy-out foo"))

    def test_キーは大文字小文字を区別しない(self):
        self.a("copy-in Foo")

        self.assertIsNotNone(self.b("copy-out FOO"))

    def test_A_の記録が無ければ_B_は対象外(self):
        self.assertIsNone(self.b("copy-out foo"))

    def test_B_だけではほかの宣言の記録を消さない(self):
        self.a("copy-in foo")
        self.a("deploy", call="c2")

        self.speak()
        self.assertIsNone(self.b("copy-out foo"))

        self.assertEqual(len(self.files()), 1)
        self.assertIn("promote", self.files()[0])

    def test_2_つ目の宣言も同じ部品で止まる(self):
        # 組み込みの integrate に依存しない。キーの無い宣言（glob と but）で確かめる。
        self.a("run deploy --prod")

        got = self.b("run promote")

        self.assertEqual(got.gate.id, "promote")
        self.assertEqual(got.key, "")
        self.speak()
        self.assertIsNone(self.b("run promote"))

    def test_but_にヒットした_A_は数えない(self):
        self.a("run deploy --dry-run")

        self.assertIsNone(self.b("run promote"))

    def test_複合コマンドは_1_つずつ見る(self):
        # but は同じコマンドの中だけ。別のコマンドの --dry-run で外れない。
        self.a("deploy --dry-run\x00deploy --prod")

        self.assertIsNotNone(self.b("promote"))

    def test_別のセッションの記録は見ない(self):
        self.a("copy-in foo", session="s1")

        self.assertIsNone(self.b("copy-out foo", session="s2"))
        self.speak(session="s2")
        self.assertIsNotNone(self.b("copy-out foo", session="s1"))

    def test_名前が前方一致するセッションも混ざらない(self):
        self.a("copy-in foo", session="s1-b")

        self.assertIsNone(self.b("copy-out foo", session="s1"))

    def test_同じコマンドの中の_A_と_B_は止まる(self):
        got = self.b("copy-in foo && copy-out foo")

        self.assertEqual(got.reason, turngate.SAME_COMMAND)
        self.assertIsNone(self.b("copy-in foo && copy-out bar"))

    def test_キーのない宣言の同じコマンドも止まる(self):
        self.assertEqual(self.b("deploy; promote").reason, turngate.SAME_COMMAND)

    def test_実行役が中で実行するコマンドも見る(self):
        texts = ["sh -c x", "copy-in foo"]
        turngate.note(GATES, texts, self.state, SESSION, "c1", now=self.tick())

        got = turngate.check(GATES, ["bash", "copy-out foo"], self.state, SESSION)

        self.assertIsNotNone(got)

    def test_キーが解けない書き方は_どのキーとも一致する(self):
        self.a("copy-in foo")

        self.assertIsNotNone(self.b("copy-out $name"))
        self.assertIsNotNone(self.b("copy-out 'foo'"))
        self.speak()
        self.assertIsNone(self.b("copy-out $name"))

    def test_A_のキーが解けないときはどの_B_も止める(self):
        self.a("copy-in $name")

        self.assertIsNotNone(self.b("copy-out whatever"))

    def test_呼び出しの記録を消すと_その_A_は数えない(self):
        self.a("copy-in foo", call="c1")

        turngate.forget_call(self.state, SESSION, "c1")

        self.assertIsNone(self.b("copy-out foo"))

    def test_失敗した_A_を消しても_前の_A_は残る(self):
        self.a("copy-in foo", call="c1")
        self.speak()
        self.a("copy-in foo", call="c2")
        turngate.forget_call(self.state, SESSION, "c2")

        # c1 のあとには発言がある。c2 は無かったことになる。
        self.assertIsNone(self.b("copy-out foo"))

    def test_呼び出しの識別子が空なら何も消さない(self):
        self.a("copy-in foo", call="")

        turngate.forget_call(self.state, SESSION, "")

        self.assertIsNotNone(self.b("copy-out foo"))

    def test_clear_でセッションの記録を全部消す_ほかのセッションは残る(self):
        self.a("copy-in foo", session="s1")
        self.a("copy-in foo", session="s2")

        turngate.clear(self.state, "s1")

        self.assertIsNone(self.b("copy-out foo", session="s1"))
        self.assertIsNotNone(self.b("copy-out foo", session="s2"))

    def test_置き場かセッションが空なら働かない(self):
        self.assertIsNone(turngate.note(GATES, ["copy-in foo"], "", SESSION, "c1"))
        self.assertIsNone(turngate.note(GATES, ["copy-in foo"], self.state, "", "c1"))
        self.assertIsNone(turngate.check(GATES, ["copy-out foo"], "", SESSION))
        self.assertIsNone(turngate.check(GATES, ["copy-out foo"], self.state, ""))


class BrokenRecordTest(TurnGateTestCase):
    def test_ユーザの発言の記録が無ければ止まる_次の発言で通る(self):
        self.a("copy-in foo")
        self.assertIsNone(userprompt.last(self.state, SESSION))

        self.assertEqual(self.b("copy-out foo").reason, turngate.NO_USER_TURN)
        self.speak()
        self.assertIsNone(self.b("copy-out foo"))

    def test_ユーザの発言の記録が壊れていれば止まる_次の発言で通る(self):
        self.a("copy-in foo")
        with open(os.path.join(self.state, f"userprompt-{SESSION}.json"), "w") as f:
            f.write("{not json")

        self.assertEqual(self.b("copy-out foo").reason, turngate.NO_USER_TURN)
        self.speak()
        self.assertIsNone(self.b("copy-out foo"))

    def test_壊れた記録は更新時刻で数え_どのキーとも一致する(self):
        self.a("copy-in foo")
        name = self.files()[0]
        path = os.path.join(self.state, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write("{not json")
        os.utime(path, (self.clock, self.clock))

        self.assertIsNotNone(self.b("copy-out bar"))
        self.speak()
        self.assertIsNone(self.b("copy-out bar"))

    def test_形の違う記録も壊れた記録として数える(self):
        self.a("copy-in foo")
        path = os.path.join(self.state, self.files()[0])
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"session": SESSION, "gate": "copy-out", "key": "foo", "at": "x"}, f)
        os.utime(path, (self.clock, self.clock))

        self.assertIsNotNone(self.b("copy-out foo"))

    def test_置き場を読めなければ_B_を止める(self):
        # 置き場の名前にファイルを置くと、listdir が NotADirectoryError になる。
        blocker = os.path.join(self.state, "file")
        with open(blocker, "w", encoding="utf-8") as f:
            f.write("x")

        got = turngate.check(GATES, ["copy-out foo"], blocker, SESSION)

        self.assertEqual(got.reason, turngate.UNREADABLE)

    def test_置き場がまだ無ければ_B_は対象外(self):
        missing = os.path.join(self.state, "nowhere")

        self.assertIsNone(turngate.check(GATES, ["copy-out foo"], missing, SESSION))

    def test_記録を書けなければ_A_を止める(self):
        blocker = os.path.join(self.state, "file")
        with open(blocker, "w", encoding="utf-8") as f:
            f.write("x")

        got = turngate.note(GATES, ["copy-in foo"], blocker, SESSION, "c1")

        self.assertEqual(got.reason, turngate.UNWRITABLE)

    def test_A_にヒットしないコマンドは書けなくても止めない(self):
        blocker = os.path.join(self.state, "file")
        with open(blocker, "w", encoding="utf-8") as f:
            f.write("x")

        self.assertIsNone(turngate.note(GATES, ["ls"], blocker, SESSION, "c1"))


EXTRA_MESSAGES = """\
version: 1
messages:
  DENY_NEEDS_USER_TURN__COPY:
    kind: deny
    values: [first_name, second_name, where, key]
    what:
      same_command: "{first_name} と {second_name} をまとめて打とうとしました{where}。"
      no_user_turn: "{first_name} を打ったあと、発言が無いまま {second_name} を打とうとしました{where}。"
    why: コピーの中身をユーザが見るためです。
    next: ユーザに見せて返事を待ち、あとで {second_name} {key} を打ち直す
    no_bypass: 分けても同じ関門に当たります。
"""


class MessageTest(TurnGateTestCase):
    def setUp(self):
        super().setUp()
        self.root = tempfile.mkdtemp(prefix="ccnavi-turngate-root-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        directory = put_messages(self.root)
        with open(os.path.join(directory, "test-copy.yml"), "w", encoding="utf-8") as f:
            f.write(EXTRA_MESSAGES)

    def test_止めた文は_4_つの段で_値が埋まっている(self):
        self.a("copy-in foo")
        got = self.b("copy-out foo")

        text = turngate.message(got, self.root)

        lines = text.split("\n")
        self.assertEqual(len(lines), 4)
        self.assertIn("copy-in を打ったあと", lines[0])
        self.assertIn("宣言 copy-out", lines[0])
        self.assertIn("キー foo", lines[0])
        self.assertTrue(lines[1].startswith("なぜ: "))
        self.assertTrue(lines[2].startswith("次: "))
        self.assertIn("copy-out foo を打ち直す", lines[2])
        self.assertTrue(lines[3].startswith("迂回しない: "))
        self.assertNotIn("{", text)

    def test_次は_1_行で_ADR_番号を書かない(self):
        self.a("copy-in foo")
        for stop in (
            self.b("copy-out foo"),
            self.b("copy-in foo && copy-out foo"),
            turngate.Stop(COPY, turngate.UNREADABLE, "foo", "permission denied"),
            turngate.Stop(COPY, turngate.UNWRITABLE, "foo", "disk full"),
        ):
            with self.subTest(reason=stop.reason):
                text = turngate.message(stop, self.root)
                self.assertEqual(sum(1 for line in text.split("\n") if line.startswith("次: ")), 1)
                self.assertNotRegex(text, r"ADR|adr-?\d")

    def test_解けないキーは仮の値のまま書く(self):
        self.a("copy-in foo")
        got = self.b("copy-out $name")

        self.assertEqual(got.key, "")
        self.assertIn("copy-out <名前> を打ち直す", turngate.message(got, self.root))

    def test_読み書きできないときは_ユーザに置き場を確かめてもらう(self):
        stop = turngate.Stop(COPY, turngate.UNREADABLE, "foo", "permission denied")

        text = turngate.message(stop, self.root)

        self.assertIn("permission denied", text)
        self.assertIn("次: ユーザに", text)


class ConditionTest(unittest.TestCase):
    def test_組み込みの宣言は組み立てられる(self):
        for gate in BUILTIN:
            with self.subTest(gate=gate.id):
                turngate.validate(gate)

    def test_テスト用の宣言も組み立てられる(self):
        for gate in GATES:
            turngate.validate(gate)

    def test_キーが片方にしか無い宣言は不正(self):
        gate = Gate("bad", Cond(regex=r"a\s+(?P<key>\S+)"), Cond(regex=r"b"), "a", "b", "", "")
        with self.assertRaises(ValueError):
            turngate.validate(gate)

    def test_regex_と_glob_の両方_どちらも無い条件は不正(self):
        for cond in (Cond(regex="a", glob="*a*"), Cond()):
            with self.subTest(cond=cond):
                gate = Gate("bad", cond, Cond(regex="b"), "a", "b", "", "")
                with self.assertRaises(ValueError):
                    turngate.validate(gate)

    def test_先読みと不正な式は不正(self):
        for text in (r"a(?=b)", r"a(?!b)", "a(", r"(a)\1"):
            with self.subTest(text=text):
                gate = Gate("bad", Cond(regex=text), Cond(regex="b"), "a", "b", "", "")
                with self.assertRaises(ValueError):
                    turngate.validate(gate)

    def test_id_の形(self):
        for gate_id in ("", "-a", "a b", "a/b"):
            with self.subTest(gate_id=gate_id):
                gate = Gate(gate_id, Cond(regex="a"), Cond(regex="b"), "a", "b", "", "")
                with self.assertRaises(ValueError):
                    turngate.validate(gate)

    def test_条件はルールと同じに大文字小文字を区別しない(self):
        got = turngate.hits(Cond(regex=r"copy-in\s+(?P<key>\S+)"), "x", ["COPY-IN Foo"])

        self.assertEqual(got, [("foo", "Foo")])

    def test_記録の名前は宣言_id_から決まる(self):
        self.assertEqual(turngate.rule_id(COPY), "builtin:turn-gate:copy-out")
        self.assertEqual(turngate.CODE, "DENY_NEEDS_USER_TURN")


class BuiltinFormsTest(TurnGateTestCase):
    """組み込みの宣言が、`ccnavi-git.sh integrate` と `integrate-done` の引数の形を読む。"""

    GIT = "sh /w/.ccnavi/scripts/ccnavi-git.sh"

    def first(self, tail):
        return turngate.hits(BUILTIN[0].first, "first", [f"{self.GIT} {tail}"])

    def second(self, tail):
        return turngate.hits(BUILTIN[0].second, "second", [f"{self.GIT} {tail}"])

    def test_integrate_は名前を_A_のキーにする(self):
        self.assertEqual(self.first("integrate foo"), [("foo", "foo")])
        self.assertEqual(
            self.first("integrate docs-69-readme"), [("docs-69-readme", "docs-69-readme")]
        )

    def test_integrate_の_abort_は_A_にならない(self):
        self.assertEqual(self.first("integrate --abort foo"), [])

    def test_integrate_done_は_メッセージの位置に関わらず名前を取る(self):
        sep = "\x01"
        msg = f"a{sep}b{sep}{sep}c"
        for tail in (
            "integrate-done foo",
            f"integrate-done foo -m {msg}",
            f"integrate-done -m {msg} foo",
            f"integrate-done --message {msg} foo",
            f"integrate-done --message={msg} foo",
            "integrate-done -m x -m y foo",
        ):
            with self.subTest(tail=tail):
                self.assertEqual(self.second(tail), [("foo", "foo")])

    def test_メッセージの語は名前と取らない(self):
        # 名前が foo の A のあと、メッセージに foo と書いただけの別の名前の B は対象外。
        self.assertEqual(self.second("integrate-done bar -m foo"), [("bar", "bar")])
        self.assertEqual(self.second("integrate-done -m foo bar"), [("bar", "bar")])

    def test_名前の無い_integrate_done_は_B_にならない(self):
        self.assertEqual(self.second("integrate-done"), [])
        self.assertEqual(self.second("integrate-done -m x"), [])

    def test_別のサブコマンドや別の名前の_sh_には反応しない(self):
        for tail in ("status", "commit -m integrate foo", "log integrate-done foo"):
            with self.subTest(tail=tail):
                self.assertEqual(self.first(tail), [])
                self.assertEqual(self.second(tail), [])
        self.assertEqual(
            turngate.hits(BUILTIN[0].second, "second", ["sh other.sh integrate-done foo"]), []
        )

    def test_引用の中の文字列は反応しない(self):
        # shellread は引用の中の空白を目印に置き換える。echo の引数は別のコマンドの語。
        quoted = "echo ccnavi-git.sh\x01integrate\x01foo"
        self.assertEqual(turngate.hits(BUILTIN[0].first, "first", [quoted]), [])

    def test_名前に空白を使った_A_と_B_は組む(self):
        turngate.note(BUILTIN, [f"{self.GIT} integrate Foo"], self.state, SESSION, "c1", now=1.0)

        got = turngate.check(BUILTIN, [f"{self.GIT} integrate-done foo -m x"], self.state, SESSION)

        self.assertEqual(got.gate.id, "integrate-user-turn")
        self.assertEqual(got.key, "foo")


if __name__ == "__main__":
    unittest.main()
