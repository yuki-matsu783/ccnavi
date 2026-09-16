"""ファイルの読み書きの型（fsio）の受入テスト。

見るのは 1 つ。同じ名前への書き込みが一時的に失敗したとき（並列に走る hook が
同じ控えを置き換えている最中など）、数回だけ打ち直して書き切ること。待っても
変わらない失敗はすぐ理由を返すこと。
"""

from __future__ import annotations

import builtins
import errno
import os
import tempfile
import unittest
from unittest import mock

from ccnavi import fsio


class _FlakyOpen:
    """最初の N 回だけ、指定の errno で開けない open。"""

    def __init__(self, failures: int, code: int):
        self.left = failures
        self.code = code
        self.calls = 0
        self.real = builtins.open

    def __call__(self, *args, **kwargs):
        self.calls += 1
        if self.left > 0:
            self.left -= 1
            raise OSError(self.code, os.strerror(self.code))
        return self.real(*args, **kwargs)


class WriteRetryTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ccnavi-fsio-")
        self.path = os.path.join(self.dir, "nested", "backup")
        self.sleep = mock.patch("ccnavi.fsio.time.sleep")
        self.sleep.start()
        self.addCleanup(self.sleep.stop)

    def test_transient_failure_is_retried(self):
        flaky = _FlakyOpen(failures=2, code=errno.EINVAL)
        with mock.patch("builtins.open", flaky):
            failed = fsio.write_bytes(self.path, b"abc")
        self.assertEqual(failed, "")
        self.assertEqual(flaky.calls, 3)
        with open(self.path, "rb") as f:
            self.assertEqual(f.read(), b"abc")

    def test_text_write_is_retried_too(self):
        flaky = _FlakyOpen(failures=1, code=errno.EACCES)
        with mock.patch("builtins.open", flaky):
            failed = fsio.write_text(self.path, "x\n", newline="\n")
        self.assertEqual(failed, "")
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "x\n")

    def test_persistent_failure_returns_reason_after_retries(self):
        flaky = _FlakyOpen(failures=99, code=errno.EINVAL)
        with mock.patch("builtins.open", flaky):
            failed = fsio.write_bytes(self.path, b"abc")
        self.assertIn("Invalid argument", failed)
        self.assertEqual(flaky.calls, fsio._WRITE_RETRIES + 1)
        self.assertFalse(os.path.exists(self.path))

    def test_non_transient_failure_returns_at_once(self):
        flaky = _FlakyOpen(failures=99, code=errno.ENOSPC)
        with mock.patch("builtins.open", flaky):
            failed = fsio.write_bytes(self.path, b"abc")
        self.assertNotEqual(failed, "")
        self.assertEqual(flaky.calls, 1)


if __name__ == "__main__":
    unittest.main()
