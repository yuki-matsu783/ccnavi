"""ファイルの読み書きの型（fsio）の受入テスト。

見るのは 1 つ。同じ名前への書き込みが一時的に失敗したとき（並列に走る hook が
同じ記録を置き換えている最中など）、数回だけ打ち直して書き切ること。待っても
変わらない失敗はすぐ理由を返すこと。
"""

from __future__ import annotations

import builtins
import errno
import os
import tempfile
import unittest
from unittest import mock

from ccnavi.infra import fsio


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
        self.sleep = mock.patch("ccnavi.infra.fsio.time.sleep")
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
    開こうとした側が共有違反で開けない。打ち直さないと、記録を読む側はそれを
    「まだ何も無い」と読む。数えを持つ記録ではそこで 0 に戻るので、書き込み側
    だけを直しても並んで走る hook に対応できない。
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
    条件に当たる」の 3 つを満たすこと。最後の 1 つが成り立たないと、直そうとした
    問題（書きかけを読まれる・書きかけを差し替える）が形を変えて戻る。
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

        素の open(path, "w") はここで空のファイルを残す。記録を読む側は空を
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
        """一時ファイルの名前は毎回変わり、本番と同じ条件に当たる。

        固定の名前（`<行き先>.part` など）だと、同時に書く 2 つが同じ一時
        ファイルを取り合い、片方の書きかけをもう片方が差し替える。
        先頭の `.` と `.part` は、承認済みの置き場に残ったものを運ぶ側
        （ccnavi-push-approved.sh と hook/c1.py）が除くため。元の名前と拡張子を
        残すのは、先頭の `.` を外せば（`temp_origin`）ctxfile.forget の
        「`once-` で始まり `.json` で終わる」という条件で掃除できるようにするため。
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
            self.assertTrue(name.startswith(".once-abc-def."), name)
            self.assertTrue(name.endswith(".part.json"), name)
            self.assertRegex(name, r"^\.[^/]*\.part(\.[^/]*)?$")
            origin = fsio.temp_origin(name)
            self.assertTrue(origin.startswith("once-abc-def."), origin)
            self.assertTrue(origin.endswith(".json"), origin)

    def test_temp_origin_leaves_other_names_alone(self):
        for name in ("once-abc-def.json", ".hidden.json", ".x.part", "a.b.part.json"):
            self.assertEqual(fsio.temp_origin(name), name)
        self.assertEqual(fsio.temp_origin(".a.b.c.x1y2_z.part.json"), "a.b.c.x1y2_z.part.json")


class WriteBytesAtomicTest(unittest.TestCase):
    """承認済みチケットの書き方（write_bytes_atomic）の受入テスト。

    見るのは、書き切れなかったときに前の中身が残って一時ファイルが残らないこと、
    バイト列を変えないこと、権限を引き継ぐこと、差し替えの前に fsync すること、
    一時ファイルの名前が承認済みチケットとして読まれず、運ばれない形であること。
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ccnavi-bytes-atomic-")
        self.path = os.path.join(self.dir, "i0001.md")

    def _names(self) -> list[str]:
        return sorted(os.listdir(self.dir))

    def _read(self) -> bytes:
        with open(self.path, "rb") as f:
            return f.read()

    def test_keeps_the_bytes_as_they_are(self):
        """改行も BOM も変えない。承認は提案とバイト単位で同じことが条件。"""
        content = b"\xef\xbb\xbf---\r\nticket: i0001\r\n---\r\n\xe6\x9c\xac\xe6\x96\x87\n\r\n"
        self.assertEqual(fsio.write_bytes_atomic(self.path, content), "")
        self.assertEqual(self._read(), content)
        self.assertEqual(self._names(), ["i0001.md"])

    def test_makes_missing_parent(self):
        deep = os.path.join(self.dir, "doing", "i0001.md")
        self.assertEqual(fsio.write_bytes_atomic(deep, b"ok"), "")
        with open(deep, "rb") as f:
            self.assertEqual(f.read(), b"ok")

    def test_failed_replace_keeps_the_previous_content_and_no_temporary_file(self):
        self.assertEqual(fsio.write_bytes_atomic(self.path, b"before"), "")
        with mock.patch("os.replace", side_effect=OSError(errno.ENOSPC, "no space")):
            failed = fsio.write_bytes_atomic(self.path, b"after")
        self.assertNotEqual(failed, "")
        self.assertEqual(self._read(), b"before")
        self.assertEqual(self._names(), ["i0001.md"])

    def test_failed_sync_keeps_the_previous_content_and_no_temporary_file(self):
        """fsync で落ちたら差し替えない。中身が届いたか分からないものを本番にしない。"""
        self.assertEqual(fsio.write_bytes_atomic(self.path, b"before"), "")
        with mock.patch.object(fsio, "_sync_file", side_effect=OSError(errno.EIO, "io")):
            failed = fsio.write_bytes_atomic(self.path, b"after")
        self.assertNotEqual(failed, "")
        self.assertEqual(self._read(), b"before")
        self.assertEqual(self._names(), ["i0001.md"])

    def test_syncs_before_replacing(self):
        order: list[str] = []
        real_replace = os.replace

        def replace(*args):
            order.append("replace")
            return real_replace(*args)

        with (
            mock.patch.object(fsio, "_sync_file", side_effect=lambda fd: order.append("sync")),
            mock.patch.object(fsio, "_sync_directory", side_effect=lambda d: order.append("dir")),
            mock.patch("os.replace", replace),
        ):
            self.assertEqual(fsio.write_bytes_atomic(self.path, b"x"), "")
        self.assertEqual(order, ["sync", "replace", "dir"])

    def test_unopenable_directory_is_not_a_failure(self):
        """親ディレクトリの fsync は開けない環境（Windows）では飛ばす。差し替えは済んでいる。"""
        real_open = os.open

        def no_dir(path, flags, *rest):
            if path == self.dir:
                raise PermissionError(errno.EACCES, "denied")
            return real_open(path, flags, *rest)

        with mock.patch("os.open", no_dir):
            self.assertEqual(fsio.write_bytes_atomic(self.path, b"x"), "")
        self.assertEqual(self._read(), b"x")

    @unittest.skipIf(os.name == "nt", "POSIX の権限ビットを見る")
    def test_keeps_the_mode_of_the_file_it_replaces(self):
        with open(self.path, "wb") as f:
            f.write(b"before")
        os.chmod(self.path, 0o640)
        self.assertEqual(fsio.write_bytes_atomic(self.path, b"after"), "")
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o640)

    @unittest.skipIf(os.name == "nt", "POSIX の権限ビットを見る")
    def test_new_file_gets_the_mode_a_plain_open_would_give(self):
        mask = os.umask(0o022)
        try:
            self.assertEqual(fsio.write_bytes_atomic(self.path, b"x"), "")
        finally:
            os.umask(mask)
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o644)

    def test_temporary_name_is_hidden_and_not_read_as_a_ticket(self):
        """一時ファイルは `.<元の名前>.<一意>.part`。`.md` で終わらず、運ぶ側の除外に当たる。"""
        seen: list[str] = []
        real = tempfile.mkstemp

        def watched(*args, **kwargs):
            handle, part = real(*args, **kwargs)
            seen.append(part)
            return handle, part

        with mock.patch("tempfile.mkstemp", watched):
            for _ in range(2):
                fsio.write_bytes_atomic(self.path, b"x")
        self.assertEqual(len(set(seen)), 2, seen)
        for part in seen:
            self.assertEqual(os.path.dirname(part), self.dir)
            name = os.path.basename(part)
            self.assertTrue(name.startswith(".i0001.md."), name)
            self.assertTrue(name.endswith(".part"), name)
            # ccnavi-push-approved.sh と hook/c1.py が運ばない一時ファイルの形。
            self.assertRegex(name, r"^\.[^/]*\.part(\.[^/]*)?$")

    def test_staged_write_is_queued_as_its_own_kind(self):
        with fsio.staging() as stage:
            self.assertEqual(fsio.write_bytes_atomic(self.path, b"x"), "")
            self.assertEqual(fsio.read_bytes(self.path), b"x")
        self.assertFalse(os.path.exists(self.path))
        (op,) = stage.items
        self.assertEqual(op.kind, fsio.OP_BYTES_ATOMIC)
        self.assertEqual(op.content, b"x")


class WriteTextDurableTest(unittest.TestCase):
    """承認済みチケットの書き直し（write_text_durable）。改行は write_text と同じに書く。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ccnavi-text-durable-")
        self.path = os.path.join(self.dir, "i0001.md")

    def _read(self, path: str) -> bytes:
        with open(path, "rb") as f:
            return f.read()

    def test_newline_matches_write_text(self):
        plain = os.path.join(self.dir, "plain.md")
        for newline in (None, "", "\n", "\r\n"):
            with self.subTest(newline=newline):
                text = "---\nticket: i0001\n---\n本文\n"
                self.assertEqual(fsio.write_text(plain, text, newline), "")
                self.assertEqual(fsio.write_text_durable(self.path, text, newline), "")
                self.assertEqual(self._read(self.path), self._read(plain))

    def test_failed_replace_keeps_the_previous_content(self):
        self.assertEqual(fsio.write_text_durable(self.path, "before"), "")
        with mock.patch("os.replace", side_effect=OSError(errno.ENOSPC, "no space")):
            self.assertNotEqual(fsio.write_text_durable(self.path, "after"), "")
        self.assertEqual(self._read(self.path), b"before")
        self.assertEqual(sorted(os.listdir(self.dir)), ["i0001.md"])

    def test_staged_write_keeps_lf_in_the_changes(self):
        with fsio.staging() as stage:
            self.assertEqual(fsio.write_text_durable(self.path, "a\nb\n"), "")
            self.assertEqual(fsio.read_text(self.path), "a\nb\n")
        self.assertFalse(os.path.exists(self.path))
        (op,) = stage.items
        self.assertEqual(op.kind, fsio.OP_TEXT_DURABLE)
        self.assertEqual(op.content, b"a\nb\n")


class WriteNewDurableTest(unittest.TestCase):
    """フローの移し（write_new_durable）。在れば書かず、無ければ途中を見せずに置く。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ccnavi-new-durable-")
        self.path = os.path.join(self.dir, "flows", "i0001-01-01.yml")

    def test_writes_when_absent(self):
        self.assertEqual(fsio.write_new_durable(self.path, b"a: 1\r\n"), "")
        with open(self.path, "rb") as f:
            self.assertEqual(f.read(), b"a: 1\r\n")
        self.assertEqual(os.listdir(os.path.dirname(self.path)), ["i0001-01-01.yml"])

    def test_does_not_overwrite(self):
        self.assertEqual(fsio.write_new_durable(self.path, b"before"), "")
        failed = fsio.write_new_durable(self.path, b"after")
        self.assertNotEqual(failed, "")
        with open(self.path, "rb") as f:
            self.assertEqual(f.read(), b"before")
        self.assertEqual(os.listdir(os.path.dirname(self.path)), ["i0001-01-01.yml"])

    def test_failed_replace_leaves_nothing(self):
        with mock.patch("os.replace", side_effect=OSError(errno.ENOSPC, "no space")):
            self.assertNotEqual(fsio.write_new_durable(self.path, b"x"), "")
        self.assertEqual(os.listdir(os.path.dirname(self.path)), [])

    def test_staged_write_refuses_an_existing_file(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "wb") as f:
            f.write(b"held")
        with fsio.staging() as stage:
            self.assertNotEqual(fsio.write_new_durable(self.path, b"x"), "")
        self.assertEqual(stage.items, [])

    def test_staged_write_is_queued_as_its_own_kind(self):
        with fsio.staging() as stage:
            self.assertEqual(fsio.write_new_durable(self.path, b"x"), "")
        (op,) = stage.items
        self.assertEqual(op.kind, fsio.OP_NEW_DURABLE)


class TempNameTest(unittest.TestCase):
    def test_temporary_names(self):
        for name in (
            ".workflow.yml.abc12345.part",
            ".once-a-b.x1y2.part.json",
            ".i0001.md.k_9.part",
        ):
            self.assertTrue(fsio.is_temp_name(name), name)
        for name in ("workflow.yml", "1.pending", "1.requested", ".hidden", "a.b.part", ".part"):
            self.assertFalse(fsio.is_temp_name(name), name)


if __name__ == "__main__":
    unittest.main()
