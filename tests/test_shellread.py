import unittest

from ccnavi.shellread import REASON_TAKEN_AS_CODE, REASON_UNTERMINATED, SEP, read


def show(text):
    """つなぎ目の印を見えるようにする。見分けの付かない 2 つの文字列のうち
    どちらが返ったのかを、失敗メッセージが言えるように。"""
    return text.replace(SEP, "<join>")


class ReadTest(unittest.TestCase):
    def readable(self, src):
        result = read(src)
        self.assertFalse(
            result.degraded,
            f"read({src!r}) が {result.reason} で諦めた。これは読めるはず",
        )
        return result.text

    def test_実行される語はそのまま残る(self):
        cases = {
            "git push origin main": "git push origin main",
            "git   push": "git push",
            "/usr/bin/git push": "/usr/bin/git push",
            "cd /repo && git push": "cd /repo" + SEP + "git push",
            "echo hi; git push": "echo hi" + SEP + "git push",
            # 2 行に割ったコマンドは 1 行で書いたのと同じ。
            "git \\\n  push origin main": "git push origin main",
            # 引用されたパスはそのパスのまま。空白を含む名前を引用するのは
            # 普通の書き方であって、隠す手口ではない。
            'cat "/home/u/.env"': "cat /home/u/.env",
        }
        for src, want in cases.items():
            with self.subTest(src=src):
                self.assertEqual(show(self.readable(src)), show(want))

    def test_引用が1語につないだ語は複数として読まれない(self):
        # ここが全部の目的。どれも実際に出た拒否で、ガードについて書く作業が
        # いちばんガードの禁止語に触れる。
        for src in [
            'grep -n "git push" README.md',
            'echo "git push origin main"',
            'git commit -m "git push を拒否する理由を書く"',
            "perl -pi -e 's/git push/git pull/' README.md",
        ]:
            with self.subTest(src=src):
                self.assertNotIn("git push", self.readable(src))

    def test_リダイレクトは演算子として残る(self):
        # 書き込み先を見るルールが立つ土台。空白の有無で形が変わらないこと。
        cases = {
            "echo x > f": "echo x > f",
            "echo x>f": "echo x > f",
            "cat a>>b": "cat a >> b",
            "cmd 2>&1": "cmd 2 >& 1",
        }
        for src, want in cases.items():
            with self.subTest(src=src):
                self.assertEqual(show(self.readable(src)), show(want))

    def test_語の中の演算子は演算子として読まれない(self):
        # ルールの regex を grep で引く作業が、いちばんリダイレクトの形に触れる。
        # 引用の中の ">" は文字であって、書き込み先を連れてこない。
        cases = [
            ("""grep -n "regex: '(>" rules.yml""", "> rules.yml"),
            ('grep -n "x>y" notes.md', "x>y"),
            # ヒアドキュメントについて書く作業も同じ。区切り記号と同じ綴りが
            # 引用の中に出るが、そこで本文が始まるわけではない。
            ('grep -n "<<EOF" README.md', "<<"),
        ]
        for src, absent in cases:
            with self.subTest(src=src):
                self.assertNotIn(absent, self.readable(src))

    def test_ヒアドキュメントの本文は実行位置ではない(self):
        src = "cat <<'EOF' > notes.md\ngit push origin main\nEOF\necho done"
        text = self.readable(src)
        self.assertNotIn("git push", text, "本文がコマンドとして読まれた")
        # 本文の後ろも読まないと、ヒアドキュメントが残りを隠す手口になる。
        self.assertIn("echo done", text)
        # 区切り記号と同じ行に残っている語も同じ。
        self.assertIn("notes.md", text)

    def test_字下げしたヒアドキュメントも閉じる(self):
        src = "\tcat <<-EOF\n\tgit push\n\tEOF\n\techo done"
        self.assertNotIn("git push", self.readable(src))

    def test_コメントはコマンドではない(self):
        self.assertEqual(self.readable("echo hi # git push origin main"), "echo hi")

    def test_コマンド置換は独立したコマンドを走らせる(self):
        self.assertIn("git push", self.readable("echo $(git push origin main)"))

    def test_算術式は落ちる(self):
        # 中でコマンドは走らない。落とす理由は別にあって、左シフトの `<<` が
        # ヒアドキュメントの区切り記号と同じ綴りだから。残すと本文の始まりに
        # 見えて、閉じない本文としてコマンド全体が読めなくなる。
        for src in ["echo $((1 << 2))", "echo $(( 1 << 2 ))", "n=$((i + 1))"]:
            with self.subTest(src=src):
                self.assertNotIn("<<", self.readable(src))

    def test_代入は後ろのコマンドを隠さない(self):
        for src in [
            "GIT_DIR=/repo/.git git push",
            'DIR="/repo with space" git push',
            "A=1 B=2 git push",
        ]:
            with self.subTest(src=src):
                self.assertIn("git push", self.readable(src))

    def test_コマンド名を引用しても隠れない(self):
        # shlex が語を組み直すので、諦める対象ではなく本来の push として読める。
        for src in ['"git" push', 'g"it" push', "git \\push"]:
            with self.subTest(src=src):
                self.assertIn("git push", self.readable(src))

    def test_文字列をコードとして実行する呼び出しは諦める(self):
        for src in [
            'bash -c "git push"',
            "sh -c 'git push'",
            'bash -lc "git push"',
            'sudo -u deploy sh -c "git push"',
            'eval "$command"',
            "echo origin | xargs git push",
            "find . -name x -exec git push \\;",
        ]:
            with self.subTest(src=src):
                result = read(src)
                self.assertTrue(result.degraded, f"{src!r} を普通に読んでしまった")
                self.assertEqual(result.reason, REASON_TAKEN_AS_CODE)

    def test_閉じない引用符は諦める(self):
        for src in ['echo "git push', "echo 'git push", "cat <<EOF\ngit push\n"]:
            with self.subTest(src=src):
                result = read(src)
                self.assertTrue(result.degraded, f"{src!r} を普通に読んでしまった")
                self.assertEqual(result.reason, REASON_UNTERMINATED)

    def test_つなぎ目の印は持ち込めない(self):
        # 入力の側がこの印を騙れると、1 本のコマンドを 2 本に見せられる。
        self.assertNotIn(SEP, self.readable("git" + SEP + "push"))


if __name__ == "__main__":
    unittest.main()
