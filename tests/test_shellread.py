import re
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
            # 区切りの前の道筋や拡張子が付いた名前
            "/usr/bin/git push": ["git push"],
            "git.exe status": ["git status"],
            # 実行役のコマンドと、飛ばすオプション・値・位置引数
            "env rm x": ["rm x"],
            "env FOO=1 BAR=2 rm x": ["rm x"],
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
    ("02", 'echo "`M`"', "中"),
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
    ("16", "cat <<EOF\nuse `M` here\nEOF", "中"),
    ("17", "echo \"$(cat <<'EOF'\nfix: use `M` and $(M) (see §6)\ndon't\nEOF\n)\"", "-"),
    ("18", 'echo "$(cat <<EOF\nuse `M`\nEOF\n)"', "中"),
    ("19", 'echo "$(echo "$(M)")"', "中"),
    ("20", "echo `echo \\`M\\``", "外"),
    ("21", 'echo "${x:-$(M)}"', "中"),
    ("22", 'echo "${x:-`M`}"', "中"),
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
    ("35", 'echo "`M"', "unterminated-substitution"),
    ("36", 'cat <<< "$(M)"', "中"),
    ("37", 'echo "$(\nM\n)"', "中"),
    ("38", 'h="$(M)"', "中"),
    ("39", "h=$(M)", "外"),
    ("40", "h=`M`", "外"),
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
    ("echo `find . -delete`", "echo $␀find . -delete", "echo $␀find . -delete"),
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
    ("cat <<EOF\nuse `rm -rf /tmp/x`\nEOF", "cat << EOF␀rm -rf /tmp/x", "cat << EOF"),
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
        'gh issue create --body "use `git push` here"',
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
            ("echo `git push", shellread.REASON_UNTERMINATED_SUBST),
            ('echo "`git push"', shellread.REASON_UNTERMINATED_SUBST),
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

    def test_ゲートの免除はコマンドが全部ラッパースクリプトのときだけ(self):
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

    def test_coproc_の後ろをコマンドの先頭として読む(self):
        # 敵対的レビューで見つかった予約語の漏れ（shellread-subst-04）。zsh は名前の無い形の
        # 中身を実行し、bash 4 以降は `coproc NAME <複合コマンド>` を持つ。
        for src, text in [
            ("coproc { find . -delete; }", "coproc␀{␀find . -delete␀}"),
            ("coproc find . -delete", "coproc␀find . -delete"),
            ("coproc NAME { find . -delete; }", "coproc␀NAME␀{␀find . -delete␀}"),
            ("coproc NAME while true; do x; done", "coproc␀NAME␀while␀true␀do␀x␀done"),
            ("coproc NAME find . -delete", "coproc␀NAME find . -delete"),
            ("coproc ( find . -delete )", "coproc␀find . -delete"),
            ("echo coproc", "echo coproc"),
            ("echo coproc { x; }", "echo coproc { x␀}"),
        ]:
            with self.subTest(src=src):
                result = read(src)
                self.assertFalse(result.degraded, result.reason)
                self.assertEqual(show(result.text), show(marked(text)))


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
            # シェルは代入の右辺を広げないが、並べる（許容した誤検知。ccnavi.md §12.2）。
            "x={a,b}": ["{a,b}"],
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
            "echo `{git,push}`": ["{git,push}"],
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


if __name__ == "__main__":
    unittest.main()
