"""投稿 API の失敗と不正な応答、操作記録を失った後の打ち直し（AT21・AT41）。

GitLab の代役（`Host`）の POST を、テストごとに差し替えて失敗させる。代役は同じプロセスの
スレッドで動くので、`Host.do_POST` を差し替えれば効く。共通の道具は
`tests/sh/write_and_push_harness.py` にある。
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import unittest
from unittest import mock

from tests.sh.write_and_push_harness import (
    APPROVED,
    PARENT,
    Host,
    WriteAndPushHostHarness,
    executable,
    write,
)


def post_http_500(self):
    length = int(self.headers.get("Content-Length", "0") or 0)
    self.rfile.read(length)
    self.reply(500, {"message": "boom"})


def post_not_json(self):
    length = int(self.headers.get("Content-Length", "0") or 0)
    self.rfile.read(length)
    body = b"<html>gateway</html>"
    self.send_response(201)
    self.send_header("Content-Length", str(len(body)))
    self.end_headers()
    self.wfile.write(body)


def post_without_id(self):
    """201 で JSON を返すが、投稿の id（GitLab は url の素）が無い。"""
    length = int(self.headers.get("Content-Length", "0") or 0)
    self.rfile.read(length)
    self.reply(201, {"message": "ok"})


# 失敗の形。どれも「成功と表示せず、操作記録を未完了で残す」ことを見る
BAD_POSTS = (
    ("http-500", post_http_500),
    ("not-json", post_not_json),
    ("no-id", post_without_id),
)


@unittest.skipIf(shutil.which("jq") is None or shutil.which("curl") is None, "jq と curl が要る")
class PostFailureTest(WriteAndPushHostHarness):
    """request・comment・ready の投稿が失敗したとき、成功と言わず、操作記録を未完了で残す。"""

    def records(self):
        found = []
        for path in sorted(glob.glob(os.path.join(self.state, "ops", "*.json"))):
            with open(path, encoding="utf-8") as f:
                found.append(json.load(f))
        return found

    def stage_states(self, kind):
        return [
            s["state"] for r in self.records() if r.get("kind") == kind for s in r.get("stages", [])
        ]

    def request_args(self):
        body = write(os.path.join(self._base, "body.md"), "見てほしい\n")
        return ("review-request", "--phase", "1", "--body-file", body, "--eli5", self.eli5())

    def ready_exe(self):
        """ready の補助コメントの下書きを返す実行ファイルの代役（実際の ready は下書きを置く）。"""
        note = write(os.path.join(self._base, "ready-note.md"), "Draft を外した\n")
        return executable(
            os.path.join(self._base, "bin", "readyhalf"),
            "#!/bin/sh\n"
            'case " $* " in\n'
            '*" review ready "*)\n'
            f"  '{self.half}' \"$@\" || exit $?\n"
            f"  printf '%s\\n' '{note}'\n"
            "  exit 0 ;;\n"
            "esac\n"
            f"exec '{self.half}' \"$@\"\n",
        )

    # ---- AT21: 投稿 API の失敗と不正な応答

    def test_a_failed_or_malformed_post_is_not_reported_as_success(self):
        """AT21: request・comment・ready。成功と言わず、操作記録は未完了（sent でない）で残る。"""
        comment_body = write(os.path.join(self._base, "comment.md"), "コメント\n")
        cases = {
            "request": (self.request_args(), {}),
            "comment": (("comment", "--body-file", comment_body), {}),
            "ready": (("ready",), {"CCNAVI_ENTRY_PATH": self.ready_exe()}),
        }
        for name, bad in BAD_POSTS:
            for kind, (args, extra) in cases.items():
                with self.subTest(post=name, command=kind), mock.patch.object(Host, "do_POST", bad):
                    # 前の回の記録を消し、各回を独立にする
                    shutil.rmtree(os.path.join(self.state, "ops"), ignore_errors=True)
                    Host.notes = []
                    done = self.review(*args, **extra)
                    self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
                    self.assertNotIn("OK: 記録した", done.stdout)
                    self.assertNotIn("同じ依頼は投稿済み", done.stdout)
                    states = self.stage_states(kind)
                    self.assertTrue(states, f"{kind} の操作記録が残っていない")
                    self.assertNotIn("sent", states)
                    if kind == "ready":
                        self.assertIn("補助のコメントは未完了", done.stderr)
                    if kind == "request":
                        self.assertFalse(
                            os.path.exists(
                                os.path.join(self.tree, APPROVED, "phases", PARENT, "1.requested")
                            ),
                            "投稿が失敗したのに依頼済みのマーカーを置いた",
                        )

    def test_a_post_response_without_a_url_is_not_reported_as_success(self):
        """AT21: 応答が JSON でも投稿の URL を作れない（id が無い）ときは、成功と言わない。"""
        comment_body = write(os.path.join(self._base, "comment.md"), "コメント\n")
        with mock.patch.object(Host, "do_POST", post_without_id):
            done = self.review("comment", "--body-file", comment_body)
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("sent", self.stage_states("comment"))
        self.assertNotIn("note_null", done.stdout + done.stderr)
        self.assertIn("unknown", self.stage_states("comment"))

    def test_a_post_without_an_op_record_is_stopped_before_sending(self):
        """ops begin が失敗したら、二重に送らないよう何も送らずに止める。"""
        comment_body = write(os.path.join(self._base, "comment.md"), "コメント\n")
        no_begin = executable(
            os.path.join(self._base, "bin", "nobegin"),
            "#!/bin/sh\n"
            'case " $* " in\n'
            '*" ops begin "*) exit 1 ;;\n'
            "esac\n"
            f"exec '{self.half}' \"$@\"\n",
        )
        for name, bad in BAD_POSTS:
            with self.subTest(post=name), mock.patch.object(Host, "do_POST", bad):
                Host.notes = []
                done = self.review(
                    "comment", "--body-file", comment_body, **{"CCNAVI_ENTRY_PATH": no_begin}
                )
                self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
                self.assertNotIn("OK: 記録した", done.stdout)
                self.assertEqual(Host.notes, [])

    # ---- AT41: 操作記録を失った後の request の打ち直し

    def test_a_request_retyped_after_losing_the_op_record_is_not_posted_twice(self):
        """AT41: 別の PC や記録の消失の想定。操作記録を消して打ち直しても、本文の内容のキーで
        既存の投稿を見つけ、二重に投稿しない。"""
        args = self.request_args()
        first = self.review(*args)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(len(Host.notes), 1)
        shutil.rmtree(os.path.join(self.state, "ops"))
        self.assertEqual(self.records(), [])
        again = self.review(*args)
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertIn("投稿済み", again.stdout)
        self.assertEqual(len(Host.notes), 1)
        # 投稿し直さないまま、記録は送信済みに直る
        self.assertEqual(set(self.stage_states("request")) - {"sent"}, set())


if __name__ == "__main__":
    unittest.main()
