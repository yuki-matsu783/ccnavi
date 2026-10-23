"""ccnavi-common-host.sh の失敗の返事の読み取り（HTTP の番号と本文）の単体テスト。

ホストの返事は、本文のあとに gh / glab の標準エラーが続く形で渡る。
本文の数字に引かれないこと、日本語を途中で壊さずに切ること、トークンを伏せることを見る。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import unittest

from tests import ROOT

SHELL = shutil.which("sh") or shutil.which("bash")
JQ = shutil.which("jq")
HOST_SH = os.path.join(ROOT, ".ccnavi", "scripts", "ccnavi-common-host.sh")


def call(func: str, text: str, token: str = "") -> str:
    script = f'. "$1"\nccnavi_h_jq="$2"\nccnavi_h_token="$3"\n{func} "$4"\n'
    done = subprocess.run(
        [SHELL, "-c", script, "sh", HOST_SH, JQ or "", token, text],
        capture_output=True,
        check=True,
    )
    return done.stdout.decode("utf-8")


@unittest.skipUnless(SHELL and JQ, "sh と jq が要る")
class HostFailTest(unittest.TestCase):
    def test_status_prefers_known_shapes_over_body_digits(self):
        text = '{"error":"HTTP 500 in text"}\nglab: 403 Forbidden'
        self.assertEqual(call("ccnavi_host_fail_status", text), "403")

    def test_status_reads_glab_http_line(self):
        text = '{"error":"The provided content-type \'\' is not supported."}\nglab: HTTP 415'
        self.assertEqual(call("ccnavi_host_fail_status", text), "415")

    def test_status_reads_curl_and_gh(self):
        self.assertEqual(
            call("ccnavi_host_fail_status", "curl: (22) The requested URL returned error: 404"),
            "404",
        )
        self.assertEqual(call("ccnavi_host_fail_status", "gh: Not Found (HTTP 404)"), "404")

    def test_status_empty_when_unreachable(self):
        self.assertEqual(call("ccnavi_host_fail_status", "curl: (28) Connection timed out"), "")

    def test_body_cuts_by_characters_not_bytes(self):
        out = call("ccnavi_host_fail_body", "あ" * 300)
        self.assertEqual(out, "あ" * 200)

    def test_body_is_one_line_without_control_characters(self):
        out = call("ccnavi_host_fail_body", "a\r\nb\x1b[31mc\td")
        self.assertEqual(out, "a b [31mc d")

    def test_body_masks_token(self):
        out = call("ccnavi_host_fail_body", "denied: Bearer s3cret end", token="s3cret")
        self.assertNotIn("s3cret", out)
        self.assertIn("***", out)


if __name__ == "__main__":
    unittest.main()
