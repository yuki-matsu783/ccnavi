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


class ReadRetryTest(unittest.TestCase):
    """読みの打ち直しの受入テスト。

    差し替え（write_text_atomic）は中身を壊さないが、Windows ではその一瞬に
    開こうとした側が共有違反で弾かれる。打ち直さないと、控えを読む側はそれを
    「まだ何も無い」と読む。数えを持つ控えではそこで 0 に戻るので、書き込み側
    だけを直しても並んで走る hook は捌けない。
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ccnavi-read-")
        self.path = os.path.join(self.dir, "once-abc-def.json")
        with open(self.path, "w", encoding="utf-8") as f:
            f.write('{"given": ["a"]}')

    def test_transient_failure_is_retried(self):
        flaky = _FlakyOpen(failures=2, code=errno.EACCES)
        with mock.patch("builtins.open", flaky):
            data, failed = fsio.read_json(self.path)
        self.assertIsNone(failed)
        self.assertEqual(data, {"given": ["a"]})
        self.assertEqual(flaky.calls, 3)

    def test_persistent_failure_returns_reason_after_retries(self):
        flaky = _FlakyOpen(failures=99, code=errno.EACCES)
        with mock.patch("builtins.open", flaky):
            _, failed = fsio.read_json(self.path)
        self.assertIsInstance(failed, OSError)
        self.assertEqual(flaky.calls, fsio._READ_RETRIES + 1)

    def test_missing_file_is_not_retried(self):
        """無いのは普通の状態。待っても現れないので、すぐ返す。"""
        flaky = _FlakyOpen(failures=99, code=errno.ENOENT)
        with mock.patch("builtins.open", flaky):
            _, failed = fsio.read_json(self.path)
        self.assertIsInstance(failed, OSError)
        self.assertEqual(flaky.calls, 1)

    def test_broken_content_is_not_retried(self):
        """中身が JSON でないのは、待っても変わらない。"""
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{ not json")
        calls = []
        real = builtins.open

        def watched(*args, **kwargs):
            calls.append(args[0])
            return real(*args, **kwargs)

        with mock.patch("builtins.open", watched):
            _, failed = fsio.read_json(self.path)
        self.assertIsInstance(failed, ValueError)
        self.assertEqual(len(calls), 1)


class WriteAtomicTest(unittest.TestCase):
    """途中を見せない書き方（write_text_atomic / write_json_atomic）の受入テスト。

    見るのは 3 つ。書き切れなかったときに前の中身が残ること、一時ファイルを
    置いていかないこと、そして一時ファイルの名前が「同じ場所・一意・本番と同じ
    ふるいに掛かる」の 3 つを満たすこと。最後の 1 つが崩れると、直そうとした
    事故（書きかけを読まれる・書きかけを差し替える）が形を変えて戻る。
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ccnavi-atomic-")
        self.path = os.path.join(self.dir, "once-abc-def.json")

    def _names(self) -> list[str]:
        return sorted(os.listdir(self.dir))

    def test_round_trip(self):
        self.assertEqual(fsio.write_json_atomic(self.path, {"given": ["a"]}), "")
        self.assertEqual(fsio.read_json(self.path)[0], {"given": ["a"]})
        # 差し替えが済めば、残るのは本番の 1 本だけ。
        self.assertEqual(self._names(), ["once-abc-def.json"])

    def test_makes_missing_parent(self):
        deep = os.path.join(self.dir, "state", "once-x-y.json")
        self.assertEqual(fsio.write_text_atomic(deep, "ok"), "")
        with open(deep, encoding="utf-8") as f:
            self.assertEqual(f.read(), "ok")

    def test_failed_write_keeps_the_previous_content(self):
        """差し替えに失敗しても、前の中身は消えない。

        素の open(path, "w") はここで空のファイルを残す。控えを読む側は空を
        「まだ 1 度も渡していない」と読むので、数えが 0 に戻る。
        """
        self.assertEqual(fsio.write_json_atomic(self.path, {"given": ["a"]}), "")
        with mock.patch("os.replace", side_effect=OSError(errno.ENOSPC, "no space")):
            failed = fsio.write_json_atomic(self.path, {"given": ["a", "b"]})
        self.assertNotEqual(failed, "")
        self.assertEqual(fsio.read_json(self.path)[0], {"given": ["a"]})

    def test_failed_write_leaves_no_temporary_file(self):
        with mock.patch("os.replace", side_effect=OSError(errno.ENOSPC, "no space")):
            fsio.write_text_atomic(self.path, "x")
        self.assertEqual(self._names(), [])

    def test_temporary_file_sits_next_to_the_destination(self):
        """一時ファイルは行き先と同じディレクトリに作る。

        os.replace が一瞬で終わるのは同じファイルシステムの中だけなので、
        他所に作るとコピーになってしまい、途中を見せない型が成り立たなくなる。
        """
        seen: list[str] = []
        real = tempfile.mkstemp

        def watched(*args, **kwargs):
            handle, part = real(*args, **kwargs)
            seen.append(part)
            return handle, part

        with mock.patch("tempfile.mkstemp", watched):
            fsio.write_text_atomic(self.path, "x")
        self.assertEqual(len(seen), 1)
        self.assertEqual(os.path.dirname(seen[0]), os.path.dirname(self.path))

    def test_temporary_names_are_unique_and_swept_by_the_same_sieve(self):
        """一時ファイルの名前は毎回変わり、本番と同じふるいに掛かる。

        固定の名前（`<行き先>.part` など）だと、同時に書く 2 つが同じ一時
        ファイルを取り合い、片方の書きかけをもう片方が差し替える。
        先頭と拡張子を残すのは、落ちて残った分を ctxfile.forget の
        「`once-` で始まり `.json` で終わる」ふるいで掃除させるため。
        """
        seen: list[str] = []
        real = tempfile.mkstemp

        def watched(*args, **kwargs):
            handle, part = real(*args, **kwargs)
            seen.append(os.path.basename(part))
            return handle, part

        with mock.patch("tempfile.mkstemp", watched):
            for _ in range(3):
                fsio.write_text_atomic(self.path, "x")

        self.assertEqual(len(set(seen)), 3, f"一時ファイルの名前が重なった: {seen}")
        for name in seen:
            self.assertTrue(name.startswith("once-abc-def."), name)
            self.assertTrue(name.endswith(".json"), name)
            self.assertNotEqual(name, "once-abc-def.json")


if __name__ == "__main__":
    unittest.main()
