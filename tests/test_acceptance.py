"""受入テスト。すべて外から道具を動かす。

標準入力に payload を 1 件渡し、読み返すのは標準出力・標準エラー・終了コードだけ。
内部の関数を呼ばないので、中身が動いてもテストは真であり続ける。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES = os.path.join(ROOT, "testdata", "rules.yml")


def run(mode="enable", payload="", log=""):
    """道具を 1 回動かし、呼び手から見えるものだけを返す。

    モードは環境ではなくフラグで固定する。このリポジトリは ccnavi を自分自身に
    仕掛けているので、テストを走らせるセッションが既にモードを持っている。
    それを読むテストは、コードではなく走った機械のことを報告してしまう。
    """
    environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}

    args = [sys.executable, "-m", "ccnavi", "--rules", RULES, "--mode", mode]
    args += ["--log", log] if log else ["--log", ""]

    return subprocess.run(
        args,
        input=payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=ROOT,
        env=environment,
    )


def pre_tool_use(tool, field, value):
    return json.dumps(
        {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": {field: value}}
    )


def verdict(case, result):
    """Claude Code が読む応答の形。"""
    try:
        return json.loads(result.stdout)["hookSpecificOutput"]
    except (ValueError, KeyError) as exc:
        case.fail(
            f"標準出力が期待した JSON ではない: {exc}\n"
            f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
        )


class VerdictTest(unittest.TestCase):
    def test_拒否には理由と代わりの手段が付く(self):
        result = run(payload=pre_tool_use("Bash", "command", "git push origin main"))

        self.assertEqual(result.returncode, 0, "判定は JSON で運ぶので終了コードは 0")
        out = verdict(self, result)
        self.assertEqual(out["permissionDecision"], "deny")
        self.assertEqual(out["hookEventName"], "PreToolUse")
        # 道具の目的そのもの。代わりの手段を名指ししない拒否は、
        # エージェントに手段を発明させることになる。
        self.assertIn("ask the user", out["permissionDecisionReason"])

    def test_通す呼び出しは何も言わない(self):
        result = run(payload=pre_tool_use("Bash", "command", "git status"))

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "", "通る呼び出しでは標準出力は空")

    def test_当たった理由はまとめて返る(self):
        result = run(payload=pre_tool_use("Bash", "command", "sed -i s/a/b/ .env"))

        reason = verdict(self, result)["permissionDecisionReason"]
        for want in ("sed", "perl"):
            self.assertIn(want, reason, "1 つずつ返すとエージェントが往復することになる")

    def test_知らないイベントは作業を止めない(self):
        payload = json.dumps(
            {
                "hook_event_name": "SomethingElse",
                "tool_name": "Bash",
                "tool_input": {"command": "git push"},
            }
        )
        result = run(payload=payload)

        self.assertEqual(result.returncode, 0, "判定を持たないイベントで止めてはいけない")
        self.assertEqual(result.stdout, "")

    def test_直接起動は黙って成功せず失敗する(self):
        result = run(payload="")

        self.assertNotEqual(
            result.returncode, 0, "payload 無しで 0 だと、設置を誤った hook が正常に見える"
        )
        self.assertIn("hook", result.stderr, "使い方を説明していない")

    def test_dry_runは手を出さずに報告する(self):
        result = run(
            mode="dry-run", payload=pre_tool_use("Bash", "command", "git push origin main")
        )

        out = verdict(self, result)
        self.assertNotIn("permissionDecision", out, "dry-run は報告するだけで手を出さない")
        self.assertIn("git push", out["additionalContext"])

    def test_disableは何も判定しない(self):
        # disable は起動した人の環境からしか効かない。フラグからは効かない。
        environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        environment["CCNAVI_MODE"] = "disable"
        result = subprocess.run(
            [sys.executable, "-m", "ccnavi", "--rules", RULES, "--log", ""],
            input=pre_tool_use("Bash", "command", "git push origin main"),
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=ROOT,
            env=environment,
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")


def reasons(case, result):
    """1 回の応答に入った理由を、件ごとに切り分けて返す。

    件と件は空行で割れている。受け取った側が 1 件だけを切り出して読めることが
    要求そのものなので、テストもまず切り分けてから 1 件ずつ見る。
    """
    text = verdict(case, result)["permissionDecisionReason"]
    return [part for part in text.split("\n\n") if part.strip()]


class ReasonTest(unittest.TestCase):
    """理由が、対象・理由コード・出所を持ち、単独で読んで成立すること。"""

    def test_理由は対象と理由コードと出所を名指しする(self):
        # 文面だけでは、受け取った側は自分の呼び出しのどこが引っかかったのかを
        # 辿れない。信じるか無視するかしか残らず、直しにも行けない。
        got = reasons(self, run(payload=pre_tool_use("Bash", "command", "git push origin main")))

        self.assertEqual(len(got), 1)
        self.assertIn("git push origin main", got[0], "何に当たったのかが無い")
        self.assertIn("DENY_COMMAND_PATTERN", got[0], "理由コードが無い")
        self.assertIn(f"{RULES}#git-push", got[0], "どの設定が言っているのかが無い")

    def test_ファイルの理由は行き着く先を対象として名指しする(self):
        # 当てたのは来たままの綴りではなく解いた先なので、対象もそちらを言う。
        # 来たままの綴りを見せると、当たった理由と対象がずれて読めなくなる。
        got = reasons(self, run(payload=pre_tool_use("Read", "file_path", "docs/../.env")))

        self.assertEqual(len(got), 1)
        self.assertIn(os.path.join(ROOT, ".env"), got[0])
        self.assertIn("DENY_PATH", got[0])
        self.assertIn(f"{RULES}#dotenv", got[0])

    def test_複数返った理由は一件ずつ単独で読んで成立する(self):
        # 同じ出来事に複数の判定が同時に当たるとき、そのうちどれが利用者の目に
        # 入るかは決まらない。上の 1 行を下の全部が参照する形は、1 件だけが
        # 切り出されて見えた瞬間に意味を失う。
        got = reasons(self, run(payload=pre_tool_use("Bash", "command", "sed -i s/a/b/ .env")))

        self.assertEqual(len(got), 2, "2 つのルールに当たったはず")
        for i, part in enumerate(got):
            with self.subTest(reason=i):
                self.assertIn("sed -i s/a/b/ .env", part, "対象を他の件に預けている")
                self.assertIn("[ccnavi] DENY_", part, "理由コードを他の件に預けている")
                self.assertIn(f"{RULES}#", part, "出所を他の件に預けている")

    def test_読めなかった判定は件ごとにそう名乗る(self):
        # 読めなかったという断りは、1 回だけ先頭に置くと、その下の 1 件だけを
        # 読んだ人には届かない。届かなかった人は、書いた覚えのないコマンドを
        # 実行したと告げられたことになる。
        got = reasons(
            self,
            run(payload=pre_tool_use("Bash", "command", 'bash -c "git push and read .env"')),
        )

        self.assertEqual(len(got), 2)
        for i, part in enumerate(got):
            with self.subTest(reason=i):
                self.assertIn("PARSE_UNCERTAIN", part)
                self.assertIn("raw text", part, "読めたときと同じ文面になっている")

    def test_理由は他の判定の結果に言及しない(self):
        # 「上の」「下の」で他の件を指した瞬間、1 件だけ読んだ人には
        # 指した先が無い文になる。
        text = verdict(self, run(payload=pre_tool_use("Bash", "command", "sed -i s/a/b/ .env")))[
            "permissionDecisionReason"
        ]

        for pointer in ("the rules below", "the rules above", "listed below"):
            self.assertNotIn(pointer, text)


class ReadingTest(unittest.TestCase):
    def test_コマンドについて書くことは実行ではない(self):
        # ルールはコマンドについて書かれたものであって文字列についてではない。
        # 生の文字列を探すと、そのコマンドに言及した文書・検索・メッセージが
        # すべてコマンドに見え、返る拒否は本物と見分けが付かない。
        # どれも実際に出た拒否。
        for command in [
            'grep -n "git push" README.md',
            'echo "git push origin main"',
            'git commit -m "git push を拒否する理由を書く"',
            "# git push origin main",
            "echo $((1 << 2))",
        ]:
            with self.subTest(command=command):
                result = run(payload=pre_tool_use("Bash", "command", command))
                self.assertEqual(
                    result.stdout, "", f"語に言及しただけの呼び出しを止めた: {command!r}"
                )

    def test_ヒアドキュメントの本文は禁止語として読まれない(self):
        # 本文は実行位置ではないので、そこに書かれた語では止まらない。
        # ヒアドキュメントそのものは別のルールで止まるので、返る理由は
        # ヒアドキュメントの 1 件だけになる。
        command = "cat <<'EOF' > notes.md\ngit push origin main\nEOF"
        reason = verdict(self, run(payload=pre_tool_use("Bash", "command", command)))[
            "permissionDecisionReason"
        ]

        self.assertIn("Heredocs are not used here", reason)
        self.assertNotIn("git push is not run by the agent", reason, "本文が読まれた")

    def test_コマンドそのものは今も止まる(self):
        # 同じ変更のもう半分。何がコマンドかを狭める変更が安全なのは、
        # 今まで捕まえていたものが 1 つも抜けないときだけなので、
        # 同じ push に至る書き方を並べて確かめる。
        for command in [
            "git push origin main",
            "git   push",
            "cd /repo && git push",
            "echo hi; git push origin main",
            "/usr/bin/git push",
            "git \\\n  push origin main",
            "GIT_DIR=/repo/.git git push",
            "echo $(git push origin main)",
            '"git" push',
        ]:
            with self.subTest(command=command):
                out = verdict(self, run(payload=pre_tool_use("Bash", "command", command)))
                self.assertEqual(out.get("permissionDecision"), "deny", f"通した: {command!r}")

    def test_読めなかった拒否はそう名乗る(self):
        # 文字列をコードとして実行する呼び出しは読めないので、生の文字列に当てる。
        # それを言うことが「禁止されたコマンドを実行した」と
        # 「その語がどこかにある」の違いで、次の一手を残すのは後者だけ。
        unread = verdict(
            self, run(payload=pre_tool_use("Bash", "command", 'bash -c "git push origin main"'))
        )
        self.assertEqual(unread.get("permissionDecision"), "deny", "読めない呼び出しを通した")
        self.assertIn("raw text", unread["permissionDecisionReason"])

        plain = verdict(self, run(payload=pre_tool_use("Bash", "command", "git push origin main")))
        self.assertNotIn(
            "raw text", plain["permissionDecisionReason"], "読めた拒否が読めなかったと名乗った"
        )


class HeredocTest(unittest.TestCase):
    def test_ヒアドキュメントは止まる(self):
        # ヒアドキュメントで書いたファイルは、Write / Edit に掛かる権限の宣言も
        # 編集後の検査も通らない。中身をファイルに落とす経路がそこだけ素通しに
        # なるので、書き出す形も、プログラムへ渡す形も同じように止める。
        for command in [
            "cat <<'EOF' > notes.md\nhello\nEOF",
            "cat <<EOF >> config.yml\na: 1\nEOF",
            "cat <<EOF | tee out.txt\nx\nEOF",
            "\tcat <<-EOF > f\n\tx\n\tEOF",
            "uv run python - <<'PY'\nprint(1)\nPY",
            'grep x <<< "$text"',
        ]:
            with self.subTest(command=command):
                out = verdict(self, run(payload=pre_tool_use("Bash", "command", command)))
                self.assertEqual(out.get("permissionDecision"), "deny", f"通した: {command!r}")

    def test_代わりの手段は名指しされる(self):
        # 止めるだけでは足りない。ここで Write を名指ししないと、
        # 同じことを別の綴りで書き直すだけになる。
        reason = verdict(
            self, run(payload=pre_tool_use("Bash", "command", "cat <<'EOF' > f\nx\nEOF"))
        )["permissionDecisionReason"]
        self.assertIn("Write", reason)

    def test_スクリプトをファイルに置いて渡す形は通る(self):
        # ヒアドキュメントの代わりに勧めている書き方そのものが止まっては、
        # 代替手段として成立しない。
        for command in [
            "uv run python scratch.py",
            "echo hi > f.txt",
            "psql -f query.sql",
        ]:
            with self.subTest(command=command):
                result = run(payload=pre_tool_use("Bash", "command", command))
                self.assertEqual(result.stdout, "", f"代替手段を止めた: {command!r}")

    def test_引用された記号を止めるのは許容した誤検知(self):
        # shlex は引用された << と素の << を同じ文字列で返し、どちらだったかを
        # 問い合わせる手段が無い。だからこれはヒアドキュメントに見えて止まる。
        # 直す対象ではなく、許容すると決めた誤検知として設計に書いてある
        # （ccnavi.md §12.3 ①、§23.1 L-7）。このテストは、次に来た人が
        # 黙って直して別のところを壊さないように、決めた側を固定する。
        out = verdict(self, run(payload=pre_tool_use("Bash", "command", 'grep -n "<<" README.md')))

        self.assertEqual(out.get("permissionDecision"), "deny")
        # 止まる側に倒れるだけでなく、本来の禁止と混ざらない文面であること。
        # 誤検知を許容できるのは、返る文面が読み手に次の一手を残すからで、
        # そこが崩れると許容の前提が消える。
        self.assertIn("raw text", out["permissionDecisionReason"])


class PathTest(unittest.TestCase):
    def test_迂回した綴りでも保護領域に届く判定になる(self):
        # 守る対象は名前ではなく場所。同じ場所を指す別の綴りで
        # ルールを外せてはいけない。
        for path in [
            ".env",
            "./.env",
            "docs/../.env",
            os.path.join(ROOT, ".env"),
        ]:
            with self.subTest(path=path):
                out = verdict(self, run(payload=pre_tool_use("Read", "file_path", path)))
                self.assertEqual(out.get("permissionDecision"), "deny", f"すり抜けた: {path!r}")

    def test_対象の無い呼び出しは判定されない(self):
        # 空のパスに作業ディレクトリを返してしまうと、対象の無い呼び出しが
        # ディレクトリへの操作に見える。
        result = run(payload=pre_tool_use("Read", "file_path", ""))
        self.assertEqual(result.stdout, "")


class RecordTest(unittest.TestCase):
    def logged(self, mode, *payloads):
        """複数の payload を 1 つの記録ファイルに通して読み返す。"""
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "log.jsonl")
            for payload in payloads:
                run(mode=mode, payload=payload, log=path)
            with open(path, encoding="utf-8") as f:
                return [json.loads(line) for line in f if line.strip()]

    def test_通した呼び出しも含めてすべて記録される(self):
        got = self.logged(
            "enable",
            pre_tool_use("Bash", "command", "git push origin main"),
            pre_tool_use("Bash", "command", "go build ./..."),
            pre_tool_use("Task", "prompt", "something"),
            json.dumps({"hook_event_name": "SessionStart"}),
            json.dumps({"hook_event_name": "PreCompact"}),
        )

        self.assertEqual(len(got), 5, "記録の無い呼び出しは、動かなかったガードと区別が付かない")
        want = [
            ("deny", None),
            ("allow", None),
            ("skip", "no-subject"),
            # セッション開始は判定を持つイベントになった。大きい対象の控えを
            # ここで 1 度だけ取る。
            ("allow", None),
            # 判定を持たないイベントは、誤りではなく素通り（REQ-HKS-03）。
            ("skip", "event-not-checked"),
        ]
        for i, (decision, reason) in enumerate(want):
            self.assertEqual(got[i]["decision"], decision, f"{i + 1} 行目")
            self.assertEqual(got[i].get("reason"), reason, f"{i + 1} 行目")

    def test_dry_runは適用しなかった判定を記録する(self):
        got = self.logged("dry-run", pre_tool_use("Bash", "command", "git push origin main"))

        self.assertEqual(len(got), 1)
        # warn で走らせる理由そのものが「何を止めるはずだったか」を数えることなので、
        # 何も起きなくても判定は記録に残らないといけない。
        self.assertEqual(got[0]["decision"], "deny")
        self.assertFalse(got[0]["enforced"], "warn は呼び出しを通したはず")
        self.assertEqual(got[0].get("rules"), ["git-push"])

    def test_読めたものと読めなかったものを分けて記録する(self):
        got = self.logged(
            "enable",
            pre_tool_use("Bash", "command", "git push origin main"),
            pre_tool_use("Bash", "command", 'bash -c "git push origin main"'),
        )

        self.assertEqual(len(got), 2)
        self.assertNotIn("degraded", got[0], "読めたコマンドに印が付いている")
        # これが無いと、ガードが止めたもののうちどれだけが読み切れないまま
        # 出た判定なのかを記録が答えられない。
        self.assertIn("degraded", got[1], "生の文字列で下した判定に印が無い")


if __name__ == "__main__":
    unittest.main()
