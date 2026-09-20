"""ルールの `{!root}` が「ワークスペースルートの外」に展開されることの受入テスト。

設計は wip/design/i0061-not-root.md（親チケット i0061 のフェーズ 1）。
先読みが使えないので、実行ファイルがルートを 1 文字ずつ
「ここで終わる／ここが違う／ここは同じ」の入れ子に展開する。

**このファイルは実装より先に書いてある。** 実装が入るまで落ちるのが正しい。
落ち方が「機能が無い」であって「テストが壊れている」ではないことだけを見ること。

内部の構造には触れない。見るのは外から観測できるものだけ:

  - `rules.load` が返すルール集合と苦情（Problem）
  - 組み立てた式が当たるか当たらないか
  - hook として叩いたときの判定
  - `--lint` の言い分

`_build` の戻り値の形をどう変えるか（設計 §4.4 の案 1 か案 2）は実装フェーズの
判断なので、ここでは縛らない。
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import unittest

from ccnavi import rules
from tests import ROOT
from tests.inproc import run_ccnavi

# 「ワークスペースルートの外」。設計 §3.2 の、置ける唯一の形。
NOT_ROOT = "^{!root}"

# 展開しても現れてはいけない綴り。設計 §2.5。
# 繰り返しは `_unsupported` が見ないので、ここで見る。
_QUANTIFIER = re.compile(r"(?<!\\)[*+]|(?<!\\)\{\d")
_LOOKAROUND = ("(?=", "(?!", "(?<=", "(?<!")


def absolute(path: str) -> str:
    r"""`C:\...` と書いたパスを、いまの機械で絶対パスになる綴りに直す。

    展開はルートを 1 文字ずつ写すだけなので、区切りが `\` でも中身は変わらない。
    変わるのは**絶対かどうか**で、`rules.real_root` が呼ぶ `os.path.realpath` は、
    相対のパスなら頭に cwd を足す。POSIX で `C:\Users\...` をそのまま渡すと、ルートが
    `<cwd>/C:\Users\...` に化けて、「中」のはずのパスが全部「外」になり、長さの境界も
    cwd のぶんだけずれる。設計 §2.3 の表は Windows の綴りのまま残して、頭だけを機械に
    合わせる（CLAUDE.md「実行環境」: 4 つのどれでも動くように書く）。

    `\` は POSIX でも普通の 1 文字として残る（`realpath` が切るのは `/` だけ）。
    展開した式は `\` と `/` のどちらも区切りとして当てるので、そこは直さなくてよい。

    `C:` 以外のドライブ（`D:`）は `/drive-d/` に替える。ドライブごとに別の綴りにするのは、
    「別々の 2 つのドライブは互いに外」を後から足したときに、黙って同じ絶対パスへ
    潰れないようにするため。**POSIX の絶対パスはどれも `/` で始まるので、最外段
    （ルートの 1 文字目）の「違う」だけは、ここでは試せない。** その段を縛れるのは
    Windows で回したときの `D:` だけで、Linux だけで回していると穴に気づけない。
    """
    if os.name == "nt":
        return path
    drive, colon, rest = path.partition(":")
    if colon == "" or len(drive) != 1:
        return path
    head = "/" if drive.upper() == "C" else f"/drive-{drive.lower()}/"
    return head + (rest[1:] if rest[:1] in ("\\", "/") else rest)


def write(path: str, text: str) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def rules_file(directory: str, *rule: dict, section: str = "deny") -> str:
    """ルールファイルを 1 枚書いて綴りを返す。"""
    return write(
        os.path.join(directory, "rules.yml"),
        json.dumps({"version": 1, section: list(rule)}),
    )


def outside_rule(expression: str = NOT_ROOT, **extra) -> dict:
    rule = {
        "id": "outside-workspace",
        "match": "Write|Edit|NotebookEdit",
        "regex": expression,
        "message": "ワークスペースの外です。",
    }
    rule.update(extra)
    return rule


class NotRootExpansionTest(unittest.TestCase):
    """展開した式が、どの綴りを「外」と数えるか。設計 §2。"""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = rules_file(self.dir.name, outside_rule())

    def compiled(self, root: str):
        """そのルートで組み立てた式を返す。苦情が出ていれば落とす。"""
        rule_set, problems = rules.load(self.path, root)
        self.assertEqual([str(p) for p in problems], [], f"root={root!r}")
        return rule_set.deny[0].compiled

    def assert_outside(self, root: str, *paths: str):
        pattern = self.compiled(root)
        for path in paths:
            with self.subTest(root=root, path=path):
                self.assertTrue(pattern.search(path), f"外のはずが当たらない: {path!r}")

    def assert_inside(self, root: str, *paths: str):
        pattern = self.compiled(root)
        for path in paths:
            with self.subTest(root=root, path=path):
                self.assertFalse(pattern.search(path), f"中のはずが当たる: {path!r}")

    # --- 観点 1: 設計 §2.3 の表 ---

    def test_the_table_of_what_counts_as_outside(self):
        root = absolute(r"C:\Users\u\Desktop\git\ccnavi")
        self.assert_outside(
            root,
            absolute(r"C:\Users\u\Desktop\git\other\x.md"),  # 途中で違う
            absolute(r"C:\Users\u\DesktopXgit\ccnavi\x.md"),  # 区切りの位置に別の字が来る
            absolute(r"C:\Users\u\Desktop"),  # ルートより上
            absolute(r"C:\Users\u\Desk"),  # ルートより上。名前の途中で終わる
            root[:-1],  # 同上。ルートの最後の 1 字が足りない
            absolute(r"C:\Users\u\Desktop\git\ccnavi-fork\x.md"),  # 前置きが一致して続く
            absolute(r"D:\ccnavi\x.md"),  # 別のドライブ
        )
        self.assert_inside(
            root,
            root + r"\README.md",
            root + r"\.claude\worktrees\x\scratchpad\draft.yml",
            absolute("C:/Users/u/Desktop/git/ccnavi/README.md"),  # 区切りの綴りが違う
            root.lower() + r"\readme.md",  # 大文字小文字が違う
        )

    # --- 観点 2: 末尾が区切りのルート（レビューで見つかった最重の欠陥）---

    def test_root_that_ends_with_a_separator(self):
        """ルートの末尾の区切りを落とさないと、ルート直下まで「外」になる。

        Windows では `os.path.realpath('/')` が `C:\\` を返すので、
        ドライブ直下をワークスペースにした環境は必ずここを踏む。
        既存の `rules.root_pattern` は同じ `rstrip` を既に持っている。
        """
        ccnavi = absolute(r"C:\Users\u\Desktop\git\ccnavi")
        roots = [ccnavi + "\\", ccnavi + "/"]
        if os.name == "nt":
            # ドライブ直下。POSIX の `/` は正規化すると空になり、拒否するのが正しいので
            # ここでは見ない。そちらは test_root_that_normalizes_to_nothing_is_refused が見る。
            roots += ["C:\\", "C:/"]
        for root in roots:
            with self.subTest(root=root):
                stem = root.rstrip("\\/")
                self.assert_inside(root, stem + r"\README.md", stem + r"\sub\x.md")
                # 「中」だけを見ると、何にも当たらない実装でも通ってしまう。
                # 同じルートで「外」も見て、式が本当に展開されていることを縛る。
                self.assert_outside(root, stem + r"-fork\x.md", absolute(r"D:\elsewhere\x.md"))

    def test_root_that_normalizes_to_nothing_is_refused(self):
        """正規化した結果が空になるルートでは「外」が定義できない。設計 §2.0。

        `/` の意味が機械で違うので、綴りではなく**正規化の結果**で場合分けする。
        POSIX では `realpath('/')` が `/` で、`rstrip` すると空になる（拒否が正しい）。
        Windows では `C:\\` を返すので `C:` が残り、それは正しく展開できる
        ルートなので拒否してはいけない。

        綴りだけを見て「`/` なら拒否」と書くと、Windows でだけ落ちるテストになる。
        """
        for root in ("\\", "/"):
            with self.subTest(root=root, real=rules.real_root(root)):
                _, problems = rules.load(self.path, root)
                if rules.real_root(root):
                    self.assertEqual([str(p) for p in problems], [], "正規化できるルートは通るべき")
                else:
                    self.assertTrue(problems, "空になるルートは苦情になるべき")

    def test_the_root_itself_is_inside(self):
        """対象がルートそのものなら「中」。敵対的レビューで見つかった欠陥の回帰テスト。

        最内に `\\Z` を混ぜると、ルートを最後までなぞり切った位置で「外」に落ちる。
        `Glob` を `path` 省略で呼ぶと対象が cwd（＝ルート）になるので、
        いちばん普通の呼び出しが「外」に化ける。

        各段の `\\Z` は「ルートより上」を拾うためのもので、最内とは意味が違う。
        """
        root = absolute(r"C:\Users\u\Desktop\git\ccnavi")
        self.assert_inside(root, root, root + "\\", root + "/")
        self.assert_outside(root, absolute(r"C:\Users\u\Desktop\git"), root + "-fork")

    # --- 観点 4・5: 綴りの揺れ ---

    def test_mixed_separators_and_regex_metacharacters_in_the_root(self):
        self.assert_inside(absolute(r"C:\Users/u\Desktop"), absolute(r"C:\Users\u\Desktop\x.md"))
        meta = absolute(r"C:\Users\u\a+b(c)[d].e\ccnavi")
        self.assert_inside(meta, meta + r"\x.md")
        self.assert_outside(meta, absolute(r"C:\Users\u\aXb(c)[d].e\ccnavi\x.md"))

    # --- 観点 6: 大文字小文字と、既知の例外 ---

    def test_case_folding_counts_as_inside(self):
        root = absolute(r"C:\Users\taniyama\ccnavi")
        self.assert_inside(root, root.swapcase() + r"\x.md")
        self.assert_outside(root, absolute(r"C:\Users\taniyama\ccnaviX\x.md"))

    def test_turkish_dotted_i_is_a_known_exception(self):
        """`re.IGNORECASE` は `İ`/`ı` を `I`/`i` と同一視する。設計 §2.4。

        ファイルシステムは同一視しないので、本当は別の場所だが「中」と数える。
        意図した挙動として固定する。直すなら設計 §2.4 から変えること。
        """
        root = absolute(r"C:\Users\taniyama\ccnavi")
        self.assert_inside(
            root,
            root.replace("i", "\u0130") + r"\x.md",  # İ
            root.replace("i", "\u0131") + r"\x.md",  # ı
        )

        # 「中」だけを見ると、何にも当たらない実装でも通ってしまう。
        # 同じルートで「外」も縛り、式が本当に展開されていることを確かめる。
        self.assert_outside(root, root.replace("i", "j") + r"\x.md")

    def test_other_scripts_are_still_outside(self):
        """トルコ語の I 系以外は、綴りが違えばきちんと「外」になる。"""
        root = absolute(r"C:\Users\u\ccnavi")
        self.assert_outside(
            root,
            absolute(r"C:\Users\u\ｃcnavi\x.md"),  # 全角
            absolute(r"C:\Users\u\ccnavß\x.md"),
        )

    # --- 観点 7: 展開結果が契約を守る ---

    def test_expansion_has_no_quantifier_and_no_lookaround(self):
        """展開結果は繰り返しも先読みも含まない。設計 §2.5。

        `_unsupported` は繰り返しを見ないので、そこに掛け直すだけでは
        この性質を確かめられない。だからここで直接見る。
        """
        root = absolute(r"C:\Users\u\Desktop\git\ccnavi")
        compiled = self.compiled(root)
        # 先に「本当に展開されている」ことを縛る。何にも当たらない式なら、
        # 繰り返しも先読みも含まないのは当たり前で、この検査は無意味になる。
        self.assertTrue(compiled.search(absolute(r"D:\elsewhere\x.md")))
        pattern = compiled.pattern
        self.assertIsNone(_QUANTIFIER.search(pattern), f"繰り返しが混ざっている: {pattern[:200]}")
        for literal in _LOOKAROUND:
            self.assertNotIn(literal, pattern)

    def test_the_written_spelling_is_kept(self):
        """観点 14。書いた綴りは残り、置き換わるのは翻訳後の式だけ。

        既存の `{root}` と同じ扱い（rules.py の `_build` のコメント）。
        `--explain` と報告が 475 字の展開結果を出すと、人には読めない。
        """
        rule_set, _ = rules.load(self.path, absolute(r"C:\Users\u\ccnavi"))
        rule = rule_set.deny[0]
        self.assertEqual(rule.regex, NOT_ROOT)
        self.assertNotIn("{!root}", rule.compiled.pattern)

    # --- 観点 12: 後ろに続く式 ---

    def test_an_expression_may_follow_the_placeholder(self):
        """`^{!root}.*\\.py$` は「外にある .py」として意味を持つ。設計 §3.3。"""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = rules_file(directory.name, outside_rule(NOT_ROOT + r".*\.py$"))
        root = absolute(r"C:\Users\u\ccnavi")
        rule_set, problems = rules.load(path, root)
        self.assertEqual([str(p) for p in problems], [])
        pattern = rule_set.deny[0].compiled
        self.assertTrue(pattern.search(absolute(r"C:\other\a.py")))
        self.assertFalse(pattern.search(absolute(r"C:\other\a.md")))
        self.assertFalse(pattern.search(root + r"\a.py"))

    # --- 観点 15: 層 ---

    def test_the_placeholder_always_means_the_workspace_root(self):
        """どの層に書いても展開先はワークスペースルート。設計 §2.6。

        層ごとにルートが変わると、プロジェクトの層に書いた 1 行が
        別の場所を指すことになる。
        """
        root = absolute(r"C:\Users\u\ccnavi")
        first_compiled = self.compiled(root)
        self.assertTrue(first_compiled.search(absolute(r"D:\elsewhere\x.md")), "展開されていない")
        first = first_compiled.pattern
        elsewhere = tempfile.TemporaryDirectory()
        self.addCleanup(elsewhere.cleanup)
        other = rules_file(elsewhere.name, outside_rule())
        rule_set, _ = rules.load(other, root)
        self.assertEqual(rule_set.deny[0].compiled.pattern, first)


class NotRootLimitTest(unittest.TestCase):
    """ルートが長すぎるときの扱い。設計 §4。"""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def long_root(self, length: int) -> str:
        """ちょうどその長さのルートの綴り。実在しなくてよい。

        末尾が区切りにならないようにする。区切りで終わると `rstrip` で 1 字縮み、
        測りたい境界からずれる（設計 §2.0）。
        """
        body = ("d" * 9 + "\\") * (length // 10 + 2)
        root = (absolute("C:\\") + body)[:length]
        if root.endswith(("\\", "/")):
            root = root[:-1] + "d"
        return root

    # --- 観点 8: 境界 ---

    def test_the_boundary_is_two_hundred_and_fifty_six(self):
        """255 字は通り、256 字は error で名指しされる。設計 §4.1。"""
        path = rules_file(self.dir.name, outside_rule())
        ok = self.long_root(255)
        self.assertEqual(len(ok), 255)
        _, problems = rules.load(path, ok)
        self.assertEqual([str(p) for p in problems], [], "255 字は通るべき")

        too_long = self.long_root(256)
        self.assertEqual(len(too_long), 256)
        _, problems = rules.load(path, too_long)
        self.assertTrue(problems, "256 字は苦情になるべき")
        said = " ".join(str(p) for p in problems)
        self.assertIn("outside-workspace", said, "どのルールか名指しすること")
        self.assertIn("256", said, "実際の長さを出すこと")

    # --- 観点 10: 例外が外へ漏れない ---

    def test_a_very_long_root_does_not_raise(self):
        """閾値より遥かに長いルートでも、未処理例外にならず苦情になる。

        **このテストは `except RecursionError` の側を実行しない。** `_expand_not_root` が
        256 字で先に弾くので、`re.compile` まで届かない。組み立てが落ちるのは 493 字で、
        256〜493 の範囲は事前の検査で全部止まる（変異テストで、`except RecursionError` を
        丸ごと消してもこのテストが落ちないことを確認済み）。

        ここで縛っているのは「長すぎるルートを渡しても呼び出し側へ例外が漏れない」ことだけ。
        それでも `rules.py` が `RecursionError` を捕まえているのは、閾値や展開の形を
        変えれば到達しうるため。理由はそちらのコメントにある。
        """
        path = rules_file(self.dir.name, outside_rule())
        for length in (600, 1200):
            with self.subTest(length=length):
                try:
                    _, problems = rules.load(path, self.long_root(length))
                except RecursionError:
                    self.fail("RecursionError が外へ漏れた")
                self.assertTrue(problems)

    # --- 観点 9: 生成できないときの倒れ方 ---

    def test_deny_falls_closed(self):
        """組み立てられない `deny` は、`match` の全部を止める。設計 §4.3。

        捨てると「守りが消える」ほうに落ちる。ここが素通りになると、
        このチケットで作ったものが丸ごと意味を失う。
        """
        path = rules_file(self.dir.name, outside_rule())
        rule_set, _ = rules.load(path, self.long_root(600))
        self.assertTrue(rule_set.deny, "deny が捨てられている（素通りになる）")
        pattern = rule_set.deny[0].compiled
        self.assertIsNotNone(pattern)
        for path_to_write in (absolute(r"C:\anywhere\x.md"), absolute(r"D:\somewhere\else\y.txt")):
            self.assertTrue(pattern.search(path_to_write), "match の全部に当たるべき")

    def test_ask_falls_closed_like_deny(self):
        """組み立てられない `ask` も `match` の全部に当たる。設計 §4.3。

        設計は deny / ask / allow の 3 つを決めているが、テストは deny と allow しか
        見ていなかった（敵対的レビューの指摘）。確認は戻せるので、ask は deny と同じ向き。
        """
        path = rules_file(
            self.dir.name,
            {"id": "outside-workspace", "match": "Write|Edit", "regex": NOT_ROOT},
            section="ask",
        )
        rule_set, problems = rules.load(path, self.long_root(600))
        self.assertTrue(problems)
        self.assertTrue(rule_set.ask, "ask が捨てられている")
        self.assertTrue(rule_set.ask[0].compiled.search(absolute(r"C:\anywhere\x.md")))

    def test_the_written_message_survives_a_failure(self):
        """組み立てに失敗しても、人が書いた文面は消えない。

        敵対的レビューで見つかった契約違反の回帰テスト。`--explain` と記録は
        書いた綴りを出す約束で、それは glob / regex だけでなく文面にも掛かる。
        止められた側へ返す説明は別の欄に持つ。
        """
        path = rules_file(self.dir.name, outside_rule(message="人が書いた本当の文面"))
        rule_set, _ = rules.load(path, self.long_root(600))
        rule = rule_set.deny[0]
        self.assertEqual(rule.message, "人が書いた本当の文面")
        self.assertIn("組み立てられない", rule.spoken_message())

    def test_allow_falls_the_other_way(self):
        """組み立てられない `allow` は、どれにも当たらない。設計 §4.3。

        当たる扱いにすると ccnavi が黙る範囲が広がる。deny とは逆に倒す。
        """
        path = rules_file(
            self.dir.name,
            {"id": "outside-workspace", "match": "Write|Edit", "regex": NOT_ROOT},
            section="allow",
        )
        rule_set, problems = rules.load(path, self.long_root(600))
        self.assertTrue(problems)
        matched = [
            r for r in rule_set.allow if r.compiled and r.compiled.search(absolute(r"C:\x\y.md"))
        ]
        self.assertEqual(matched, [], "allow は当たらない側に倒すべき")


class NotRootWritingTest(unittest.TestCase):
    """どこに書けるか。設計 §3。"""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = os.path.realpath(self.dir.name)

    def problems_for(self, rule: dict) -> str:
        path = rules_file(self.dir.name, rule)
        _, problems = rules.load(path, self.root)
        return " ".join(str(p) for p in problems)

    # --- 観点 13 ---

    def test_only_right_after_the_caret(self):
        self.assertIn("error", self.problems_for(outside_rule("x{!root}")))
        self.assertIn("error", self.problems_for(outside_rule("^{!root}a{!root}")))
        self.assertEqual(self.problems_for(outside_rule(NOT_ROOT)), "")

    def test_the_placeholder_is_refused_in_a_glob(self):
        """`glob` は fnmatch に翻訳されるので、入れ子の展開を埋める場所が無い。設計 §3.1。"""
        rule = {
            "id": "outside-workspace",
            "match": "Write",
            "glob": "{!root}",
            "message": "ワークスペースの外です。",
        }
        self.assertIn("error", self.problems_for(rule))

    def test_a_quantifier_right_after_the_placeholder_is_an_error(self):
        """直後の量化子は error。敵対的レビューで見つかった欠陥の回帰テスト。

        `^{!root}?` と書けるままにすると、展開結果ごと省略できる式になり、
        「外だけを止める」はずの deny が**ワークスペースの中への書き込みまで**止める。
        warn では本番の rules.yml に入ってしまう。
        """
        for suffix in ("?", "*", "+", "{0}", "{0,0}", "{2,}"):
            with self.subTest(suffix=suffix):
                said = self.problems_for(outside_rule(NOT_ROOT + suffix))
                self.assertIn("error", said, f"`{suffix}` が通ってしまう")

    def test_a_structural_suffix_is_only_a_warning(self):
        """`^{!root}[\\\\/]foo` は壊れてはいないが、まず勘違い。設計 §3.3。

        展開結果が食い終わる位置がパスの区切りである保証は無い。
        止めるほどではないので warn。
        """
        said = self.problems_for(outside_rule(NOT_ROOT + r"[\\/]foo"))
        self.assertIn("warn", said)
        self.assertNotIn("error", said)


class NotRootJudgeTest(unittest.TestCase):
    """hook として叩いたときの判定。設計 §4.7。"""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = os.path.realpath(self.dir.name)
        self.rules = rules_file(self.dir.name, outside_rule())

    def judge(self, path: str, mode: str = "enable") -> dict:
        payload = json.dumps(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "Write",
                "cwd": self.root,
                "tool_input": {"file_path": path},
            }
        )
        environment = {k: v for k, v in os.environ.items() if not k.startswith("CCNAVI_")}
        done = run_ccnavi(
            [
                "--root",
                self.root,
                "--mode",
                mode,
                "--rules",
                self.rules,
                "--log",
                "",
                "--state",
                "",
                "--approved",
                "",
                "--guard-core-files",
                "disable",
            ],
            input=payload,
            cwd=ROOT,
            env=environment,
        )
        if not done.stdout.strip():
            return {}
        return json.loads(done.stdout)["hookSpecificOutput"]

    def test_outside_is_denied_and_inside_is_not(self):
        with tempfile.TemporaryDirectory() as elsewhere:
            outside = os.path.join(os.path.realpath(elsewhere), "draft.yml")
            out = self.judge(outside)
        self.assertEqual(out.get("permissionDecision"), "deny", out)
        self.assertIn("outside-workspace", out.get("permissionDecisionReason", ""))

        out = self.judge(os.path.join(self.root, "README.md"))
        self.assertNotEqual(out.get("permissionDecision"), "deny", out)

    # --- 観点 11 ---

    def test_dry_run_does_not_stop(self):
        with tempfile.TemporaryDirectory() as elsewhere:
            outside = os.path.join(os.path.realpath(elsewhere), "draft.yml")
            out = self.judge(outside, mode="dry-run")
        self.assertNotEqual(out.get("permissionDecision"), "deny", out)
        # 「止まらない」だけを見ると、ルールが当たっていない実装でも通る。
        # `enable` なら止めていたことを告げる文が出ていることまで縛る。
        self.assertIn("outside-workspace", json.dumps(out, ensure_ascii=False), out)


if __name__ == "__main__":
    unittest.main()
