"""長い入力でも照合が線形で終わること（#241）。

時間そのものは機械で変わるので、同じ式を長さ n と 4n に適用した比で見る。
2 乗なら 16 倍、線形なら 4 倍前後になる。閾値は余裕を見て 10 倍にしてある。
"""

from __future__ import annotations

import re
import time
import unittest

from ccnavi.policy import rules, selfguard_shell


def took(pattern: re.Pattern, subject: str) -> float:
    start = time.perf_counter()
    pattern.search(subject)
    return time.perf_counter() - start


class LinearTest(unittest.TestCase):
    def assert_linear(self, pattern: re.Pattern, make, n: int = 4000):
        small, large = make(n), make(n * 4)
        # 短い側が速すぎて比がぶれないよう、数回の最小を取る。
        a = min(took(pattern, small) for _ in range(3))
        b = min(took(pattern, large) for _ in range(3))
        self.assertLess(b, max(a, 0.005) * 10, f"{a:.4f}s -> {b:.4f}s")

    def test_glob_ルールの照合は長さに比例する(self):
        rule, problems = rules._build(
            {"id": "t", "match": "Bash", "glob": "*git push*", "message": "m"}, "ask", 0
        )
        self.assertEqual(problems, [])
        self.assert_linear(rule.compiled, lambda n: "echo " + "a" * n, n=5000)

    def test_glob_先頭に星の無い書き方は文字列の途中にヒットしない(self):
        rule, _ = rules._build(
            {"id": "t", "match": "Bash", "glob": "git push", "message": "m"}, "ask", 0
        )
        self.assertIsNotNone(rule.compiled.search("git push"))
        self.assertIsNone(rule.compiled.search("cd /repo && git push"))

    def test_sed_の枝は_i_が並んでも長さに比例する(self):
        pattern = re.compile(rf"{selfguard_shell._WRITE_VERBS}(\.ccnavi/x)")
        self.assert_linear(pattern, lambda n: "sed -i" + " -i" * n + " x", n=4000)

    def test_sed_の枝のヒットの仕方は変わらない(self):
        pattern = re.compile(rf"{selfguard_shell._WRITE_VERBS}(\.ccnavi/x)")
        for subject, want in (
            ("sed -i s/a/b/ .ccnavi/x", True),
            ("sed -n -i s/a/b/ .ccnavi/x", True),
            ("sed -ni s/a/b/ .ccnavi/x", True),
            ("sed s/a/b/ -i .ccnavi/x", True),
            ("sed --i s/a/b/ .ccnavi/x", True),
            ("sed -i s/a/b/ other", False),
            # `where` が `-i` の語の途中から始まる形も、これまでどおりヒットする。
            ("sed -i.ccnavi/x f", True),
            ("sed s/a/b/ .ccnavi/x", False),
            ("sed -i s/a/b/\x00cat .ccnavi/x", False),
        ):
            with self.subTest(subject=subject):
                self.assertEqual(pattern.search(subject) is not None, want)

    def test_生の文字列の長さにも上限がある(self):
        limit = selfguard_shell.RAW_LENGTH_LIMIT
        self.assertTrue(selfguard_shell.too_many_redirects(">" + "a" * limit))
        self.assertFalse(selfguard_shell.too_many_redirects(">" + "a" * (limit - 1)))
        # `>` の無い生の文字列には掛けない。
        self.assertFalse(selfguard_shell.too_many_redirects("a" * (limit * 2)))


if __name__ == "__main__":
    unittest.main()
