"""`_matching` が結果の写しを弾くときの文面。

写しを読めなかったとき（`--result` が空、ファイルが読めない）は、その理由の 1 行だけを
出す。「結果にマージリクエストが無い」は、読めた写しに `mr` が無いときだけの文。
読めなかったときにまで出すと、読めない理由の後ろに誤った 2 行目が付く。
"""

from __future__ import annotations

import io
import json
import os
import shutil
import tempfile
import unittest

from ccnavi import review

NO_MR = "ccnavi: 結果にマージリクエストが無い\n"


class MatchingTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ccnavi-matching-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def _write(self, data: object) -> str:
        path = os.path.join(self.dir, "result.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        return path

    def _run(self, path: str, mark: dict | None = None) -> tuple[object, str]:
        err = io.StringIO()
        got = review._matching(err, path, mark or {})
        return got, err.getvalue()

    def test_empty_path_says_only_that_result_is_needed(self):
        got, err = self._run("")
        self.assertIsNone(got)
        self.assertEqual(err, "ccnavi: --result <json> が要る。リモートの写しは sh が渡す\n")

    def test_unreadable_file_says_only_why(self):
        got, err = self._run(os.path.join(self.dir, "missing.json"))
        self.assertIsNone(got)
        self.assertTrue(err.startswith("ccnavi: リモートを読めていない: "), err)
        self.assertEqual(err.count("\n"), 1, err)
        self.assertNotIn(NO_MR, err)

    def test_error_in_result_says_only_the_error(self):
        got, err = self._run(self._write({"error": "boom"}))
        self.assertIsNone(got)
        self.assertEqual(err, "ccnavi: リモートを読めていない: boom\n")

    def test_missing_mr_says_so(self):
        got, err = self._run(self._write({"host": "github"}))
        self.assertIsNone(got)
        self.assertEqual(err, NO_MR)

    def test_matching_mr_passes_quietly(self):
        path = self._write({"host": "github", "mr": {"number": 7, "url": "u"}})
        got, err = self._run(path, {"host": "github", "mr": 7})
        self.assertIsNotNone(got)
        self.assertEqual(err, "")

    def test_other_mr_is_named(self):
        path = self._write({"host": "github", "mr": {"number": 8, "url": "u"}})
        got, err = self._run(path, {"host": "github", "mr": 7})
        self.assertIsNone(got)
        self.assertEqual(err, "ccnavi: 依頼したマージリクエスト（7）と結果のもの（8）が違う\n")


if __name__ == "__main__":
    unittest.main()
