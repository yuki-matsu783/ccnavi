import re
import time
import unittest

from ccnavi import phase, shellread
from ccnavi.shellread import REASON_TAKEN_AS_CODE, REASON_UNTERMINATED, SEP, read

# 語の中の切れ目の目印。コマンドの区切り（SEP）と別の文字になる予定で、
# 実装が入るまでは無い。無い間は、それを前提にしたテストを skip する。
WORD_SEP = getattr(shellread, "WORD_SEP", None)


def show(text):
    """つなぎ目の目印を見えるようにする。見分けの付かない 2 つの文字列のうち
    どちらが返ったのかを、失敗メッセージが言えるように。"""
    text = text.replace(SEP, "<join>")
    if WORD_SEP:
        text = text.replace(WORD_SEP, "<word>")
    return text


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

    def test_つなぎ目の目印は持ち込めない(self):
        # 入力の側がこの目印を騙れると、1 本のコマンドを 2 本に見せられる。
        self.assertNotIn(SEP, self.readable("git" + SEP + "push"))


@unittest.skipUnless(hasattr(shellread, "WORD_SEP"), "shellread-sep の実装待ち")
class WordSepTest(unittest.TestCase):
    """目印を 2 つに分けたあとの読み（wip/design/shellread-sep.md §4「入力 → 返る文字列」）。

    コマンドとコマンドの間は SEP のまま。引用が 1 語につないだ空白と、語の中に
    入った演算子の文字の両側は WORD_SEP になる。ルールの `[^\\x00]*` が
    「同じコマンドの中」だけを指せるように、2 つを別の文字にする。
    """

    def readable(self, src):
        result = read(src)
        self.assertFalse(
            result.degraded,
            f"read({src!r}) が {result.reason} で諦めた。これは読めるはず",
        )
        return result.text

    def test_返る文字列は対応表のとおり(self):
        cases = {
            # コマンドの区切りは SEP のまま。
            "cd /repo && git push": "cd /repo" + SEP + "git push",
            "echo hi; git push": "echo hi" + SEP + "git push",
            # 引用がつないだ空白は WORD_SEP。
            'grep -n "git push" README.md': "grep -n git" + WORD_SEP + "push README.md",
            # 語の中の演算子の文字は両側が WORD_SEP。文字そのものは残る。
            'grep -n "x>y" notes.md': "grep -n x" + WORD_SEP + ">" + WORD_SEP + "y notes.md",
            """grep -n "regex: '(>" rules.yml""": (
                "grep -n regex:"
                + WORD_SEP
                + "'"
                + WORD_SEP
                + "("
                + WORD_SEP
                + WORD_SEP
                + ">"
                + WORD_SEP
                + " rules.yml"
            ),
            'grep -n "<<EOF" README.md': (
                "grep -n " + WORD_SEP + "<" + WORD_SEP + WORD_SEP + "<" + WORD_SEP + "EOF README.md"
            ),
            # 引用符だけで書かれた演算子 1 語は演算子のまま（shlex が引用の有無を返さない）。
            'grep -n ">" f': "grep -n > f",
            # 素の演算子は空白付きで残る。
            "echo x>f": "echo x > f",
            "cmd 2>&1": "cmd 2 >& 1",
            # 2 つの目印が 1 本の中に並ぶ形。引用の空白は WORD_SEP、パイプは SEP。
            'echo "a b" | curl -d @- x': "echo a" + WORD_SEP + "b" + SEP + "curl -d @- x",
            # コマンド置換の括弧はコマンドの区切り。
            "echo $(git push origin main)": "echo $" + SEP + "git push origin main",
            # 入力に混ざった目印はどちらも空白として読まれる。
            "git" + WORD_SEP + "push": "git push",
            "git" + SEP + "push": "git push",
        }
        for src, want in cases.items():
            with self.subTest(src=src):
                self.assertEqual(show(self.readable(src)), show(want))

    def test_引用だけの二重の山括弧は今までどおり諦める(self):
        # 許容した誤検知（ccnavi.md §12.2）。目印を分けても変わらない。
        result = read('grep -n "<<" f')
        self.assertTrue(result.degraded, "引用の << を普通に読んでしまった")
        self.assertEqual(result.reason, REASON_UNTERMINATED)

    def test_語の中の目印も持ち込めない(self):
        # コマンドの区切りと同じ。入力の側がこの目印を騙れると、`git␁push` のような
        # 実在しない 1 語に見せて raw-git を外せる。取り除いて `git push` と読む。
        text = self.readable("git" + WORD_SEP + "push")
        self.assertNotIn(WORD_SEP, text)
        self.assertEqual(text, "git push")


class UnwrappedTest(unittest.TestCase):
    """中で実行されるコマンドの層（wip/design/launcher-scripts.md 3.5.2 節、12 節 U1〜U3）。

    `env` `sudo` `sh -c` `xargs` のような実行役のコマンドは、別のコマンドを実行する。
    `read(src).unwrapped` は、実行役のコマンドを 1 枚ずつ外した層を SEP でつないだもの。
    元の形は含めない。層が無ければ空。
    """

    def layers(self, src):
        result = read(src)
        unwrapped = getattr(result, "unwrapped", None)
        self.assertIsNotNone(unwrapped, "Reading に unwrapped の欄が無い")
        if not unwrapped:
            return []
        # 語の中の目印（引用がつないだ空白）は層の区切りではない。層の並びを見るので空白に戻す。
        word_sep = WORD_SEP or "\x01"
        return [layer.replace(word_sep, " ") for layer in unwrapped.split(SEP)]

    def test_形ごとの層は表のとおり(self):
        # U1。3.5.2 節の表を 1 行ずつ。
        cases = {
            # 代入
            "FOO=1 rm x": ["rm x"],
            # 足し込みと配列の要素も代入。bash は残りをコマンドとして実行する
            "a+=1 rm x": ["rm x"],
            "a[1]=x rm x": ["rm x"],
            "a=1 b+=2 rm x": ["rm x"],
            # 区切りの前の道筋や拡張子が付いた名前
            "/usr/bin/git push": ["git push"],
            "git.exe status": ["git status"],
            # 実行役のコマンドと、飛ばすオプション・値・位置引数
            "env rm x": ["rm x"],
            "env FOO=1 BAR=2 rm x": ["rm x"],
            # env と sudo は `=` を含む引数を綴りを問わず代入に数える
            "env a+=1 rm x": ["rm x"],
            "env a.b=1 rm x": ["rm x"],
            "sudo a+=1 rm x": ["rm x"],
            "env -u HOME rm x": ["rm x"],
            "env --unset HOME rm x": ["rm x"],
            "env -C /tmp rm x": ["rm x"],
            "env -- rm x": ["rm x"],
            "command rm x": ["rm x"],
            "exec rm x": ["rm x"],
            "exec -a name rm x": ["rm x"],
            "nohup rm x": ["rm x"],
            "time rm x": ["rm x"],
            "time -f %e rm x": ["rm x"],
            "nice rm x": ["rm x"],
            "nice -n 5 rm x": ["rm x"],
            "sudo rm x": ["rm x"],
            "sudo -u me rm x": ["rm x"],
            "sudo -E -u me rm x": ["rm x"],
            "sudo -- rm x": ["rm x"],
            "doas rm x": ["rm x"],
            "doas -u me rm x": ["rm x"],
            "timeout 5 rm x": ["rm x"],
            "timeout -s KILL 5 rm x": ["rm x"],
            "timeout -k 1 5 rm x": ["rm x"],
            "stdbuf -o0 rm x": ["rm x"],
            "stdbuf -o 0 rm x": ["rm x"],
            "chrt 10 rm x": ["rm x"],
            "ionice -c 3 rm x": ["rm x"],
            "taskset 1 rm x": ["rm x"],
            # シェルにファイルを渡す形
            "sh x.sh a": ["x.sh a"],
            "sh -x x.sh a": ["x.sh a"],
            "bash x.sh a": ["x.sh a"],
            "zsh x.sh a": ["x.sh a"],
            "dash x.sh a": ["x.sh a"],
            "ksh x.sh a": ["x.sh a"],
            # 文字列を読み直す形
            "sh -c 'rm x'": ["rm x"],
            "bash -lc 'rm x'": ["rm x"],
            "eval rm x": ["rm x"],
            "eval 'rm x'": ["rm x"],
            # . / source
            ". x.sh a": ["x.sh a"],
            "source x.sh a": ["x.sh a"],
            # xargs
            "xargs rm x": ["rm x"],
            "xargs -0 rm": ["rm"],
            "xargs -n 1 -I {} rm {}": ["rm {}"],
            # find -exec の類
            "find . -name a -exec rm {} \\;": ["rm {}"],
            "find . -execdir rm {} +": ["rm {}"],
            "find . -ok rm {} \\;": ["rm {}"],
            "find . -okdir rm {} \\;": ["rm {}"],
            "find . -exec grep -l a {} \\; -exec rm {} \\;": ["grep -l a {}", "rm {}"],
        }
        for src, want in cases.items():
            with self.subTest(src=src):
                self.assertEqual(self.layers(src), want)

    def test_途中の層も並ぶ(self):
        # U1。承認のルールの `script` の枝は `sh …approve.sh` の層に当たる。外側から内側へ並ぶ。
        cases = {
            "env sh .ccnavi/scripts/ccnavi-approve.sh": [
                "sh .ccnavi/scripts/ccnavi-approve.sh",
                ".ccnavi/scripts/ccnavi-approve.sh",
            ],
            "/bin/sh x.sh": ["sh x.sh", "x.sh"],
            "/usr/bin/env rm x": ["env rm x", "rm x"],
            "nohup env rm x": ["env rm x", "rm x"],
            "sudo -u me sh -c 'rm x'": ["sh -c rm x", "rm x"],
            # 前に置いたリダイレクトは外す。外さないと実行役のコマンドが先頭に立たない。
            ">/dev/null env rm x": ["env rm x", "rm x"],
            "2>&1 env rm x": ["env rm x", "rm x"],
        }
        for src, want in cases.items():
            with self.subTest(src=src):
                self.assertEqual(self.layers(src), want)

    def test_層は深さ_4_で止まる(self):
        # U1。
        self.assertEqual(
            self.layers("env env env env rm x"),
            ["env env env rm x", "env env rm x", "env rm x", "rm x"],
        )
        layers = self.layers("env env env env env rm x")
        self.assertEqual(len(layers), 4, layers)
        self.assertNotIn("rm x", layers)

    def test_コマンドごとの層がつながる(self):
        # U1。全コマンドの層を SEP でつなぐ。層の無いコマンドは何も足さない。
        cases = {
            "cd /tmp && env rm x": ["rm x"],
            "env rm a; nohup rm b": ["rm a", "rm b"],
            "echo $(env rm x)": ["rm x"],
        }
        for src, want in cases.items():
            with self.subTest(src=src):
                self.assertEqual(self.layers(src), want)

    def test_実行役でないコマンドには層が無い(self):
        # U1。`echo` や `grep` の引数に書いたコマンド名は、実行されない。
        for src in [
            "rm -f x",
            "cat README.md",
            "echo env rm x",
            "git log --grep env",
            "grep -rn sudo docs",
        ]:
            with self.subTest(src=src):
                self.assertEqual(getattr(read(src), "unwrapped", None), "")

    def test_読み切れない形でも層を作り_degraded_と理由は残る(self):
        # U2。読み切れない形こそ、中で何が実行されるかを見たい。
        cases = {
            "sh -c 'rm x'": ["rm x"],
            'bash -lc "rm x"': ["rm x"],
            "eval 'rm x'": ["rm x"],
            "source x.sh a": ["x.sh a"],
            ". x.sh a": ["x.sh a"],
            "echo a | xargs rm x": ["rm x"],
            "find . -name a -exec rm {} \\;": ["rm {}"],
            'sudo -u deploy sh -c "git push"': ["sh -c git push", "git push"],
        }
        for src, want in cases.items():
            with self.subTest(src=src):
                result = read(src)
                self.assertTrue(result.degraded, f"{src!r} を普通に読んでしまった")
                self.assertEqual(result.reason, REASON_TAKEN_AS_CODE)
                self.assertEqual(self.layers(src), want)

    def test_閉じない引用とヒアドキュメントでは層を作らない(self):
        # U3。トークンに割れないので、層も推測しない。
        for src in [
            "env rm 'x",
            "sh -c 'rm x",
            'sudo -u me sh -c "rm x',
            "env cat <<EOF\nrm x\n",
        ]:
            with self.subTest(src=src):
                result = read(src)
                self.assertTrue(result.degraded, f"{src!r} を普通に読んでしまった")
                self.assertEqual(result.reason, REASON_UNTERMINATED)
                self.assertEqual(getattr(result, "unwrapped", None), "")


# 切り出した中身が、コマンドの先頭に立ったかどうか。
SUBST_MARK = re.compile(r"(^|\x00)zzmark($|[ \x00])")

# wip/design/shellread-subst.md §4.1。M を「呼ばれたら記録を残す関数」に置き換えて
# bash 3.2 と zsh で走らせた結果が元になっている。ここでは shell を走らせず、その表を期待にする。
#   中     shell が実行し、引用の中から切り出す（bare に残らない）
#   外     shell が実行し、引用の外から切り出す（bare にも残る）
#   -      shell が実行せず、切り出さない
#   割れる  bash 3.2 と zsh で答えが割れる。縮退か切り出すか（どちらも厳しい側）
#   それ以外は縮退の理由
SHELL_CASES = [
    ("01", 'echo "$(M)"', "中"),
    # バッククォートは読まずに止める（ADR-0047）。実行されるものは 02 16 18 20 22 35 40。
    ("02", 'echo "`M`"', "backquote"),
    ("03", 'echo "\\$(M)"', "-"),
    ("04", 'echo "\\`M\\`"', "-"),
    ("05", 'echo "\\\\$(M)"', "中"),
    ("06", "echo '$(M)'", "-"),
    ("07", "echo '`M`'", "-"),
    ("08", "echo $'$(M)'", "-"),
    ("09", 'echo "it\'s $(M)"', "中"),
    ("10", "echo hi # $(M)", "-"),
    ("11", "echo a#$(M)", "外"),
    ("12", "cat <<'EOF'\n$(M) `M`\nEOF", "-"),
    ("13", 'cat <<"EOF"\n$(M) `M`\nEOF', "-"),
    ("14", "cat <<\\EOF\n$(M) `M`\nEOF", "-"),
    ("15", "cat <<EOF\n$(M)\nEOF", "中"),
    ("16", "cat <<EOF\nuse `M` here\nEOF", "backquote"),
    ("17", "echo \"$(cat <<'EOF'\nfix: use `M` and $(M) (see §6)\ndon't\nEOF\n)\"", "-"),
    ("18", 'echo "$(cat <<EOF\nuse `M`\nEOF\n)"', "backquote"),
    ("19", 'echo "$(echo "$(M)")"', "中"),
    ("20", "echo `echo \\`M\\``", "backquote"),
    ("21", 'echo "${x:-$(M)}"', "中"),
    ("22", 'echo "${x:-`M`}"', "backquote"),
    ("23", 'echo "$((1 + 2))"', "-"),
    ("24", 'echo "$(( $(M) + 1 ))"', "中"),
    ("25", 'echo "$( (M) )"', "中"),
    ("26", "cat <(M)", "外"),
    ("27", 'echo "<(M)"', "-"),
    ("28", 'echo "$(case a in a) M;; esac)"', "割れる"),
    ("29", 'echo "$(echo "a)b"; M)"', "中"),
    ("30", "echo \"$(echo 'a)b'; M)\"", "中"),
    ("31", 'echo "$(echo \\")\\"; M)"', "-"),
    ("32", 'echo "$(echo a # )\nM)"', "中"),
    ("33", "echo \"$(echo '$(M)')\"", "-"),
    ("34", 'echo "$(M"', "unterminated-quote"),
    ("35", 'echo "`M"', "backquote"),
    ("36", 'cat <<< "$(M)"', "中"),
    ("37", 'echo "$(\nM\n)"', "中"),
    ("38", 'h="$(M)"', "中"),
    ("39", "h=$(M)", "外"),
    ("40", "h=`M`", "backquote"),
    ("41", 'echo "\\"$(M)\\""', "中"),
    ("42", 'a=(1); echo "${a[$(M)]}"', "中"),
    ("43", 'echo "$[1+2]"', "-"),
    ("44", 'echo x > "$(M)"', "中"),
    ("45", 'for f in "$(M)"; do :; done', "中"),
    ("46", '[[ "$(M)" = x ]]', "中"),
    ("47", "echo hi\nM", "外"),
    ("48", "ls |\nM", "外"),
    ("49", "echo hi # x\nM", "外"),
    ("50", "cat <<EOF\nx\nEOF\nM", "外"),
    ("51", "cat <<'EOF'\nx $(y)\nEOF\nM", "外"),
    ("52", "if true\nthen M\nfi", "外"),
    ("53", "diff <(echo a) >(M)", "外"),
    ("54", "cat < <(M)", "外"),
    ("55", "echo a#b; M", "外"),
    ("56", "echo a#b\nM", "外"),
    ("57", "echo $#; M", "外"),
    ("58", "echo ${#x}; M", "外"),
    ("59", "echo \"$(cat <<'EOF'\nfix: don't\nEOF\n)\"; M", "割れる"),
    ("60", "case a in a) M;; esac", "外"),
    ("61", "cat <<EOF\nx\n EOF\nEOF\nM", "外"),
    ("62", "cat <<A <<B\na\nA\nb $(M)\nB", "中"),
    ("63", 'echo "a\nM"', "-"),
    ("64", "echo 'a #b'; M", "外"),
    ("65", "echo hi # don't\nM", "外"),
]

# wip/design/shellread-subst.md §4.2。(入力, text, bare)。␀ は SEP、␁ は WORD_SEP。
READINGS = [
    ('echo "$(git push origin main)"', "echo $␀git push origin main", "echo $"),
    ('grep -n "$(git push)" f', "grep -n $ f␀git push", "grep -n $ f"),
    ('h="$(git rev-parse HEAD)"', "h=$␀git rev-parse HEAD", "h=$"),
    ("echo $(git push origin main)", "echo $␀git push origin main", "echo $␀git push origin main"),
    ("echo $(git push) foo", "echo $ foo␀git push", "echo $ foo␀git push"),
    ("find $(pwd) -name x -delete", "find $ -name x -delete␀pwd", "find $ -name x -delete␀pwd"),
    ("cat <(sh evil.sh)", "cat $␀sh evil.sh", "cat $␀sh evil.sh"),
    ("diff <(ls a) >(tee f)", "diff $ $␀ls a␀tee f", "diff $ $␀ls a␀tee f"),
    ("grep -n x f\nsh evil.sh", "grep -n x f␀sh evil.sh", "grep -n x f␀sh evil.sh"),
    ("echo a#b; git push", "echo a#b␀git push", "echo a#b␀git push"),
    (
        "if true; then find . -delete; fi",
        "if␀true␀then␀find . -delete␀fi",
        "if␀true␀then␀find . -delete␀fi",
    ),
    ("! grep x f", "!␀grep x f", "!␀grep x f"),
    ("cat <<EOF\nuse $(rm -rf /tmp/x)\nEOF", "cat << EOF␀rm -rf /tmp/x", "cat << EOF"),
    (
        "cat <<'EOF' > notes.md\n$(git push)\nEOF\necho done",
        "cat << EOF > notes.md␀echo done",
        "cat << EOF > notes.md␀echo done",
    ),
    (
        "sh .ccnavi/scripts/ccnavi-git.sh commit -m \"$(cat <<'EOF'\nfix: don't push\nEOF\n)\"",
        "sh .ccnavi/scripts/ccnavi-git.sh commit -m $␀cat << EOF",
        "sh .ccnavi/scripts/ccnavi-git.sh commit -m $",
    ),
    (
        'gh issue create --body "use $(git push) here"',
        "gh issue create --body use␁$␁here␀git push",
        "gh issue create --body use␁$␁here",
    ),
    ('echo "$(echo "$(git push)")"', "echo $␀echo $␀git push", "echo $"),
    ('echo "${x:-$(git push)}"', "echo ${x:-$}␀git push", "echo ${x:-$}"),
    ("grep -n '$(git push)' f", "grep -n $␁(␁git␁push␁)␁ f", "grep -n $␁(␁git␁push␁)␁ f"),
    ("echo hi # $(git push)", "echo hi", "echo hi"),
    ("echo $((1 << 2))", "echo", "echo"),
    ("cat a.txt\n", "cat a.txt", "cat a.txt"),
    ("git" + SEP + "push", "git push", "git push"),
]


def marked(text):
    return text.replace("␀", SEP).replace("␁", WORD_SEP or "")


@unittest.skipUnless(hasattr(shellread, "REASON_AMBIGUOUS_SUBST"), "shellread-subst の実装待ち")
class MovedTest(unittest.TestCase):
    """`cd` で移った先から見た綴り（ADR-0069、issue #61）。

    `read(src).moved` は、`cd` の行き先を引数に継ぎ足したコマンドを SEP でつないだもの。
    綴りの変わったコマンドだけが並ぶ。書かれた綴り（`text`）は動かさない。
    """

    def moved(self, src):
        result = read(src)
        moved = getattr(result, "moved", None)
        self.assertIsNotNone(moved, "Reading に moved の欄が無い")
        self.assertFalse(result.degraded, f"read({src!r}) が {result.reason} で諦めた")
        if not moved:
            return []
        word_sep = WORD_SEP or "\x01"
        return [command.replace(word_sep, " ") for command in moved.split(SEP)]

    def assert_moved(self, cases):
        for src, want in cases.items():
            with self.subTest(src=src):
                self.assertIn(show(want), [show(c) for c in self.moved(src)])

    def test_守られた場所へ入ってから書く形(self):
        # issue #61 の表。どれも綴りからディレクトリの名前が消える形。
        rules = ".ccnavi/common/rules.yml"
        self.assert_moved(
            {
                "cd .ccnavi/common && echo x > rules.yml": f"echo .ccnavi/common/x > {rules}",
                "cd .ccnavi && echo x > common/rules.yml": f"echo .ccnavi/x > {rules}",
                "cd .ccnavi/common; echo x > rules.yml": f"echo .ccnavi/common/x > {rules}",
                "(cd .ccnavi/common && echo x > rules.yml)": f"echo .ccnavi/common/x > {rules}",
                "cd .claude && echo x > settings.json": "echo .claude/x > .claude/settings.json",
                "cd .claude/hooks && echo x > lint-py.sh": (
                    "echo .claude/hooks/x > .claude/hooks/lint-py.sh"
                ),
                # 名前に拡張子が無くても同じ。`rm -rf hooks` は hook を丸ごと消す形。
                "cd .claude && rm -rf hooks": "rm -rf .claude/hooks",
                "cd .claude && cp /tmp/x settings.json": "cp /tmp/x .claude/settings.json",
                # 移るのを 2 回に分けても同じ。
                "cd .ccnavi && cd common && rm rules.yml": f"rm {rules}",
            }
        )

    def test_書かれた綴りは動かさない(self):
        # ここが要。ルールは綴りに固定して書かれているので、書かれた側に継ぎ足すと
        # いま当たっているものが外れる（サブエージェントの禁止の `…ccnavi-ticket.sh start`、
        # コマンドの頭に固定した組み込みの守り）。
        cases = {
            "cd .claude && echo x > settings.json": "cd .claude" + SEP + "echo x > settings.json",
            "cd .ccnavi && rm -rf common": "cd .ccnavi" + SEP + "rm -rf common",
        }
        for src, want in cases.items():
            with self.subTest(src=src):
                self.assertEqual(show(read(src).text), show(want))

    def test_コマンドの位置の語には継ぎ足さない(self):
        # `rm` が `.ccnavi/rm` になると `(^|\x00)rm` が当たらなくなる。
        for command in self.moved("cd .ccnavi && rm -rf common"):
            self.assertTrue(command.startswith("rm "), f"名前に継ぎ足した: {command!r}")

    def test_移っていないコマンドは並べない(self):
        for src in [
            "echo x > .ccnavi/common/rules.yml",
            "ls -la",
            "rm -rf /tmp/x",
            # `cd` 自身は書き込みではないので、当てる先に並べても止めるものが無い。
            "cd .claude",
            "cd .ccnavi && cd common",
            # 絶対パスの引数は継ぎ足す先が無い。
            "cd .ccnavi && rm /tmp/x",
            # オプションにも継ぎ足さない。
            "cd .ccnavi && rm -rf /tmp/x",
        ]:
            with self.subTest(src=src):
                self.assertEqual(self.moved(src), [])

    def test_上に戻る綴りは畳む(self):
        self.assert_moved(
            {
                "cd .claude/worktrees/w && echo x > ../../scripts/ccnavi-git.sh": (
                    "echo .claude/worktrees/w/x > .claude/scripts/ccnavi-git.sh"
                ),
                "cd a/b && rm ./x": "rm a/b/x",
                # 起点より上に出る `..` は畳まずに残す。
                "cd a && rm ../../x": "rm ../x",
                # 絶対パスはそのまま。そこから先の行き先になる。
                "cd /tmp && rm x": "rm /tmp/x",
                # 末尾の区切りは残す（名前がそこで終わる形に当てる守りのため）。
                "cd .claude && rm -rf hooks/": "rm -rf .claude/hooks/",
            }
        )

    def test_リダイレクトの行き先は名前の前でも継ぎ足す(self):
        # `> settings.json` だけで hook の登録は空になる。コマンドの位置より前に
        # 書いたリダイレクトも、コマンドが無い形も、行き先はパス（敵対的レビュー）。
        self.assert_moved(
            {
                "cd .claude && > settings.json": "> .claude/settings.json",
                "cd .claude && >settings.json cat /tmp/x": "> .claude/settings.json cat /tmp/x",
                "cd .claude && echo x >> settings.json": (
                    "echo .claude/x >> .claude/settings.json"
                ),
            }
        )

    def test_値にパスを取る語は値だけ継ぎ足す(self):
        # `dd` は守りが名指ししている動詞。`of=x` は代入の形をしているが引数。
        self.assert_moved(
            {
                "cd .claude && dd if=/tmp/x of=settings.json": (
                    "dd if=/tmp/x of=.claude/settings.json"
                ),
            }
        )

    def test_fd_の番号には継ぎ足さない(self):
        # `2>&1` の `1` はファイルではない。継ぎ足すと `.ccnavi/1` になり、
        # 守りの綴りが引数に現れる。
        self.assertEqual(self.moved("cd .ccnavi && rm ../a 2>&1"), ["rm a 2 >& 1"])

    def test_置換の中で移った先も並べる(self):
        # 中身は別の読みだが、中身の中で移った先は中身に届く（敵対的レビュー）。
        self.assert_moved(
            {
                'echo "$(cd .claude && echo x > settings.json)"': (
                    "echo .claude/x > .claude/settings.json"
                ),
                "x=$(cd .claude && rm settings.json)": "rm .claude/settings.json",
            }
        )

    def test_組み込みの_cd_も_cd(self):
        self.assert_moved(
            {
                "builtin cd .claude && rm settings.json": "rm .claude/settings.json",
                "command cd .claude && rm settings.json": "rm .claude/settings.json",
            }
        )

    def test_実行役のコマンドの中で実行されるコマンドにも継ぎ足す(self):
        # 名前がどの語に立つかは `_peel` と同じ読み方で出す。名前には継ぎ足さない。
        self.assert_moved(
            {
                "cd .claude && env rm settings.json": "env rm .claude/settings.json",
                "cd .claude && sudo -u me rm settings.json": (
                    "sudo -u me rm .claude/settings.json"
                ),
                "cd .claude && timeout 5 rm settings.json": "timeout 5 rm .claude/settings.json",
                "cd .claude && nohup cp /tmp/x settings.json": (
                    "nohup cp /tmp/x .claude/settings.json"
                ),
                # 中で実行されるコマンドが無い形でも、リダイレクトの行き先は継ぎ足す。
                "cd .claude && exec > settings.json": "exec > .claude/settings.json",
            }
        )

    def test_実行役のコマンドが移す先も読む(self):
        # `env -C` と `sudo --chdir` は、その 1 本だけ居場所を移す。`cd` と同じ穴。
        self.assert_moved(
            {
                "env -C .claude rm settings.json": "env -C .claude rm .claude/settings.json",
                "sudo --chdir=.claude rm settings.json": (
                    "sudo --chdir=.claude rm .claude/settings.json"
                ),
                "cd docs && env -C ../.claude rm settings.json": (
                    "env -C ../.claude rm .claude/settings.json"
                ),
            }
        )
        # 読めない行き先なら、その 1 本には継ぎ足さない。
        self.assertEqual(self.moved("env -C $X rm settings.json"), [])

    def test_サブシェルは行き先を出入りで戻す(self):
        self.assertEqual(self.moved("cd a && (cd b && ls c) && ls d"), ["ls a/b/c", "ls a/d"])

    def test_左がサブシェルになる区切りでは移らない(self):
        # `cd .claude &` も `cd .claude |` も、移るのはサブシェルのほう。後ろは
        # 元の場所のまま。継ぎ足すと、無関係な書き込みを止めることになる。
        for src in [
            "cd .claude & echo x > settings.json",
            "cd .claude | echo x > settings.json",
        ]:
            with self.subTest(src=src):
                self.assertEqual(self.moved(src), [])
        # 並びの頭で居た場所までは戻る。
        self.assertEqual(self.moved("cd .claude && cd docs | rm ../x"), ["rm x"])

    def test_case_の枝は読まない(self):
        # `case` の `)` はサブシェルの閉じと同じ綴りで来るので、枝の出入りが読めない。
        # 読むと、枝の中の `cd` が枝の外の書き込みに継ぎ足される（誤検知）。
        for src in [
            "(case x in x) cd .claude;; esac); echo x > settings.json",
            "case x in x) cd .claude;; esac; echo x > settings.json",
        ]:
            with self.subTest(src=src):
                self.assertEqual(self.moved(src), [])

    def test_行き先を読めない形では継ぎ足さないだけ(self):
        # 縮退させない。縮退は生の文字列に落ちるので、`cd - && rm -f <守られた場所>` の
        # ように、書かれた綴りで**今は止まっている**形が止まらなくなる（敵対的レビュー）。
        for src in [
            "cd - && rm x",
            "cd && rm x",
            "cd $HOME && rm x",
            'cd "$(git rev-parse --show-toplevel)" && rm x',
            "cd ~/work && rm x",
            "cd .cc* && rm x",
            "cd --foo && rm x",
            "pushd .claude && rm settings.json",
            "popd && rm x",
            "cd a b && rm x",
            "(cd - && rm x)",
        ]:
            with self.subTest(src=src):
                result = read(src)
                self.assertFalse(result.degraded, f"縮退した: {src!r} {result.reason}")
                self.assertEqual(result.moved, "", f"読めない行き先を読んだ: {src!r}")

    def test_読めない行き先の前で移った先は残る(self):
        self.assertEqual(
            self.moved("cd .claude && rm settings.json && cd - && rm x"),
            ["rm .claude/settings.json"],
        )

    def test_絶対パスへ移れば読めない行き先の後でも読める(self):
        self.assertEqual(self.moved("cd - && cd /tmp && rm x"), ["rm /tmp/x"])

    def test_居場所の長さと語数には上限がある(self):
        # `cd a` を並べると居場所の綴りが伸びる。畳み直す値段が長さに比例するので、
        # 上限が無いと全体が二乗になり、判定の期限（judge の 3 秒）の外で時間を使える
        # （敵対的レビュー）。
        chain = "cd a " + "&& cd a " * 4000 + "&& rm x"
        started = time.monotonic()
        result = read(chain)
        spent = time.monotonic() - started

        self.assertLess(spent, 1.0, f"{spent:.2f} 秒かかった")
        self.assertLess(len(result.moved), 100_000, "当てる先が大きくなりすぎる")
        # 上限を越えたら継ぎ足さない。読み自体は今までどおり。
        self.assertFalse(result.degraded)

    def test_語数の上限を越えたら継ぎ足さない(self):
        many = "cd a && " + " && ".join(f"rm x{i}" for i in range(shellread.MOVED_WORDS))
        self.assertLess(len(self.moved(many)), shellread.MOVED_WORDS)


class SubstTest(unittest.TestCase):
    """引用の有無によらず、shell が実行する中身を独立したコマンドとして読む
    （wip/design/shellread-subst.md §4.1 と §4.2）。改行、プロセス置換、語の途中の `#`、
    予約語の直後も同じ種類の穴として読む（同 §0）。
    """

    def test_shellが実行する中身だけを切り出す(self):
        for cid, src, want in SHELL_CASES:
            with self.subTest(id=cid, src=src):
                result = read(src.replace("M", "zzmark"))
                if result.degraded:
                    got = result.reason
                elif SUBST_MARK.search(result.text):
                    got = "外" if SUBST_MARK.search(result.bare) else "中"
                else:
                    got = "-"
                if want == "割れる":
                    self.assertNotEqual(got, "-", show(result.text))
                else:
                    self.assertEqual(got, want, show(result.text))

    def test_読みは対応表のとおり(self):
        for src, text, bare in READINGS:
            with self.subTest(src=src):
                result = read(src)
                self.assertFalse(result.degraded, result.reason)
                self.assertEqual(show(result.text), show(marked(text)))
                self.assertEqual(show(result.bare), show(marked(bare)))

    def test_縮退の理由は読みを止めたものを名指しする(self):
        cases = [
            ('grep -n "<<" f', shellread.REASON_UNTERMINATED),
            ('echo "$(git push"', shellread.REASON_UNTERMINATED),
            ("echo $(git push", shellread.REASON_UNTERMINATED_SUBST),
            ("echo `git push", shellread.REASON_BACKQUOTE),
            ('echo "`git push"', shellread.REASON_BACKQUOTE),
            ('echo "$(case a in a) git push;; esac)"', shellread.REASON_AMBIGUOUS_SUBST),
            ('echo "$(xargs echo < f)"', REASON_TAKEN_AS_CODE),
            ("cat <<'EOF' > notes.md", REASON_UNTERMINATED),
        ]
        for src, reason in cases:
            with self.subTest(src=src):
                result = read(src)
                self.assertTrue(result.degraded, f"{src!r} を普通に読んでしまった")
                self.assertEqual(result.reason, reason)

    def test_入れ子は16段まで読み_越えたら縮退する(self):
        def nested(depth):
            return "echo " + '"$(echo ' * depth + "git push" + ')"' * depth

        self.assertFalse(read(nested(16)).degraded, read(nested(16)).reason)
        for depth in (17, 400):
            with self.subTest(depth=depth):
                result = read(nested(depth))
                self.assertTrue(result.degraded)
                self.assertEqual(result.reason, shellread.REASON_AMBIGUOUS_SUBST)

    def test_切り出さない綴りは文字のまま(self):
        # 文字として書く道（単一引用、$'…'、\$(、\`、引用付き heredoc）を必ず残す。
        for src in [
            "grep -n '$(git push)' f",
            "echo $'$(git push)'",
            'grep -n "\\$(git push)" f',
            'grep -n "\\`git push\\`" f',
            "cat <<'EOF'\n$(git push) `git push`\nEOF",
        ]:
            with self.subTest(src=src):
                result = read(src)
                self.assertFalse(result.degraded, result.reason)
                self.assertNotRegex(result.text, r"(^|\x00)git push")

    def test_止めている間の免除はコマンドが全部ラッパースクリプトのときだけ(self):
        substituted = read('sh .ccnavi/scripts/ccnavi-git.sh commit -m "$(cat f)"')
        self.assertFalse(
            phase.exempt(substituted.text, substituted.reason),
            "置換の中の cat まで免除した",
        )
        lines = read(
            "sh .ccnavi/scripts/ccnavi-ticket.sh done x\nsh .ccnavi/scripts/ccnavi-git.sh status"
        )
        self.assertTrue(phase.exempt(lines.text, lines.reason), show(lines.text))

    def test_置換の中の状態を動かすスクリプトもサブエージェントに許さない(self):
        inner = read('echo "$(sh .ccnavi/scripts/ccnavi-ticket.sh done x)"')
        self.assertTrue(phase.forbidden(inner.text), show(inner.text))


class BraceTest(unittest.TestCase):
    """引用の外のブレース展開を並べる（ADR-0046）。展開はしない。

    期待は bash 3.2 と zsh で実測した結果。どちらかのシェルが広げる形を並べる。
    """

    def test_どちらかのシェルが広げる形を並べる(self):
        cases = {
            "{git,push,origin,main}": ["{git,push,origin,main}"],
            "{rm,-rf,/tmp/x}": ["{rm,-rf,/tmp/x}"],
            "cp f{,.bak}": ["{,.bak}"],
            "grep -rn x --exclude-dir={node_modules,.git} .": ["{node_modules,.git}"],
            "echo {1..3}": ["{1..3}"],
            "echo {a..c}": ["{a..c}"],
            "echo {-2..1}": ["{-2..1}"],
            # zsh（と bash 4 以降）だけが広げる形。
            "echo {1..5..2}": ["{1..5..2}"],
            "echo {1..a}": ["{1..a}"],
            "echo ${x:-{a,b}}": ["{a,b}"],
            # 中に引用や置換があっても、カンマが引用の外なら広がる。
            'echo {a,"b c"}': ['{a,"b c"}'],
            "echo {a,'b'}": ["{a,'b'}"],
            "echo {a,$(echo b)}": ["{a,$(echo b)}"],
            "echo $(echo p){a,b}": ["{a,b}"],
            # 入れ子は外側だけ。外側が閉じなければ内側。
            "echo {a,{b,c}}": ["{a,{b,c}}"],
            "echo {x{a,b}": ["{a,b}"],
            "echo a{b,c}d{e,f}": ["{b,c}", "{e,f}"],
            "echo {,}": ["{,}"],
            # 生の CR は語を割らない。bash は広げて `git` に `<CR>push origin main` を渡す
            # （敵対的レビュー）。
            "{git,\rpush,origin,main}": ["{git,\rpush,origin,main}"],
            # シェルは代入の右辺、case のパターン、[[ ]] の中を広げないが、並べる
            # （許容した誤検知。ccnavi.md §12.2）。
            "x={a,b}": ["{a,b}"],
            "case $x in {a,b}) :;; esac": ["{a,b}"],
            "[[ $f == *.{jpg,png} ]]": ["{jpg,png}"],
        }
        for src, want in cases.items():
            with self.subTest(src=src):
                self.assertEqual(read(src).braces, want)

    def test_広がらない形は並べない(self):
        for src in [
            "echo {a}",
            "echo {}",
            "echo {a..}",
            "echo {aa..c}",
            "echo '{a,b}'",
            'echo "{a,b}"',
            "echo $'{a,b}'",
            "echo \\{a,b}",
            "echo {a\\,b}",
            'echo {"a,b"}',
            "echo { a,b }",
            "echo {a,b",
            "echo a,b}",
            "echo {a,b;echo c}",
            'echo "${x:-{a,b}}"',
            "find . -exec rm {} \\;",
            "git show HEAD@{1}",
            "docker ps --format {{.ID}},{{.Names}}",
            "{ echo a; }",
            "cat <<EOF\n{a,b}\nEOF",
            "cat <<'EOF'\n{a,b}\nEOF",
            "echo hi # {a,b}",
            "sh -c \"echo '{a,b}'\"",
        ]:
            with self.subTest(src=src):
                self.assertEqual(read(src).braces, [])

    def test_置換の中身と読み直す文字列の中も並べる(self):
        cases = {
            'echo "$({git,push})"': ["{git,push}"],
            "cat <<EOF\n$({git,push})\nEOF": ["{git,push}"],
            "sh -c '{git,push}'": ["{git,push}"],
            "eval '{a,b}'": ["{a,b}"],
            # 外側と読み直しの両方で見つけても 1 件。
            "eval echo {a,b}": ["{a,b}"],
        }
        for src, want in cases.items():
            with self.subTest(src=src):
                self.assertEqual(read(src).braces, want)

    def test_読みそのものは変えない(self):
        # 判定がルールより先に止めるので、読みを変える理由が無い。
        for src, text in {
            "echo {a,b}": "echo {a,b}",
            "cp f{,.bak}": "cp f{,.bak}",
            "echo ${x:-{a}} {b,c}": "echo ${x:-{a}} {b,c}",
        }.items():
            with self.subTest(src=src):
                result = read(src)
                self.assertFalse(result.degraded, result.reason)
                self.assertEqual(result.text, text)

    def test_縮退しても並べる(self):
        result = read("echo {a,b} | xargs rm")
        self.assertTrue(result.degraded)
        self.assertEqual(result.reason, REASON_TAKEN_AS_CODE)
        self.assertEqual(result.braces, ["{a,b}"])


def rewrites_of(src, form):
    return [text for f, text in read(src).rewrites if f == form]


class CommandNameTest(unittest.TestCase):
    """実行するときにシェルが決めるコマンド名を並べる（ADR-0047）。"""

    def test_コマンド名の位置の展開とグロブを並べる(self):
        cases = {
            "c=git; $c push origin main": ["$c"],
            # 置換は走査が `$` 1 文字に置き換える。
            "$(echo git) push origin main": ["$"],
            "/usr/bin/gi? push origin main": ["/usr/bin/gi?"],
            "/bin/r[m] -rf /tmp/x": ["/bin/r[m]"],
            "git${IFS}push origin main": ["git${IFS}push"],
            '"$c" push': ["$c"],
            "FOO=1 $c status": ["$c"],
            ">/dev/null $c status": ["$c"],
            "2>&1 $c status": ["$c"],
            "! $c status": ["$c"],
            "if $c; then :; fi": ["$c"],
            "echo $($c status)": ["$c"],
            # 実行役のコマンドを外した層の先頭。
            "env $c status": ["$c"],
            ">/dev/null env $c status": ["$c"],
            "sudo -u me $c status": ["$c"],
            "sh $G status": ["$G"],
            ". $venv/bin/activate": ["$venv/bin/activate"],
            # bash 4.1 以降の名前付き fd も、前に置いたリダイレクト（敵対的レビュー）。
            "{fd}>/dev/null $c push": ["$c"],
            "{fd}>&2 $c push": ["$c"],
            # 外側が縮退していない `eval` は、読み直した層も見る。
            'FOO=1 eval "$c status"': ["$c"],
        }
        for src, want in cases.items():
            with self.subTest(src=src):
                self.assertEqual(rewrites_of(src, shellread.FORM_COMMAND_NAME), want)

    def test_コマンド名でない位置は並べない(self):
        for src in [
            "echo $HOME *.py",
            "x=$(pwd)",
            "FOO=$HOME/x make",
            "a[1]=x",
            "x+=1",
            "[ -f x ] && echo y",
            "[[ -f $x ]] && echo y",
            "case $x in a) echo a;; esac",
            "for f in *.py; do echo $f; done",
            "timeout $T make",
            "nice -n $N make",
            "command -v $x",
            "cd $S && ls",
            "echo '$c' | cat",
            # 外側が縮退する `eval` と `sh -c` の文字列の中は見ない。確認に落ちる（ADR-0047）。
            'sh -c "$c status"',
            'eval "$(ssh-agent -s)"',
            'eval "$x"',
        ]:
            with self.subTest(src=src):
                self.assertEqual(rewrites_of(src, shellread.FORM_COMMAND_NAME), [])

    def test_コマンドを大量に並べても線形で読む(self):
        # 敵対的レビュー。並べた綴りの重複除去が二乗で、8000 本で 20 秒を超えていた。
        src = "; ".join(f"$c{i} push" for i in range(8000))
        start = time.monotonic()
        names = rewrites_of(src, shellread.FORM_COMMAND_NAME)
        self.assertEqual(len(names), 8000)
        self.assertLess(time.monotonic() - start, 3.0)


class BackquoteTest(unittest.TestCase):
    """実行されるバッククォートで読みを止める（ADR-0047）。"""

    def test_実行されるバッククォートを並べる(self):
        cases = {
            "echo `id`": "`id`",
            'echo "`id`"': "`id`",
            'echo "odd ` one"': "`",
            "h=`id`": "`id`",
            'echo "${x:-`id`}"': "`id`",
            "echo $((`id` + 1))": "`id`",
            'echo "$(echo `id`)"': "`id`",
            "cat <<EOF\nuse `id`\nEOF": "`id`",
            "sh -c 'echo `id`'": "`id`",
        }
        for src, shown in cases.items():
            with self.subTest(src=src):
                self.assertEqual(rewrites_of(src, shellread.FORM_BACKQUOTE), [shown])

    def test_文字として書いたバッククォートは並べない(self):
        for src in [
            "echo '`id`'",
            'echo "\\`id\\`"',
            "echo \\`id\\`",
            "echo $'`id`'",
            "cat <<'EOF'\n`id`\nEOF",
            "echo hi # `id`",
        ]:
            with self.subTest(src=src):
                result = read(src)
                self.assertFalse(result.degraded, result.reason)
                self.assertEqual(result.rewrites, [])


class AmbiguousFormTest(unittest.TestCase):
    """シェルで読みが割れる形を並べる（ADR-0047）。"""

    def test_読みが割れる形を並べる(self):
        deep = "echo " + '"$(echo ' * 17 + "x" + ')"' * 17
        cases = {
            'echo "$(case a in a) x;; esac)"': "case inside $( )",
            "echo $((echo a) | cat)": "$((…) …)",
            deep: "$( ) nested more than 16 deep",
            "coproc { find . -delete; }": "coproc",
            "coproc NAME find . -delete": "coproc",
            "select x in a b; do :; done": "select",
        }
        for src, shown in cases.items():
            with self.subTest(src=src[:40]):
                self.assertIn(shown, rewrites_of(src, shellread.FORM_AMBIGUOUS))

    def test_引数に書いた予約語は並べない(self):
        for src in [
            "echo coproc",
            "echo select",
            "psql -c 'select 1'",
            # `for` と `case` の直後の語は変数名と調べる値（敵対的レビュー）。
            "for select in a b; do echo $select; done",
            "for coproc in a b; do :; done",
            "case select in select) echo hi;; esac",
            "echo $((1 << 2))",
            'echo "$(echo just in time)"',
        ]:
            with self.subTest(src=src):
                self.assertEqual(rewrites_of(src, shellread.FORM_AMBIGUOUS), [])


if __name__ == "__main__":
    unittest.main()
