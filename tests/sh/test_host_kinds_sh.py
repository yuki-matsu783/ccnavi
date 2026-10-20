"""origin の URL からのホストの分類と接続先。sh（`ccnavi_host_parse`）の読み手。

実行ファイル側（`tests/ticket/test_host_kinds.py`）と Chrome 拡張も、同じ表
（`tests/fixtures/host-kinds.json`）を読む。

`CCNAVI_SH_DIR` で、読む sh の出どころを差し替えられる。既定はこのツリーの `.ccnavi/scripts/`。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest

from tests import ROOT

SH_DIR = os.path.join(ROOT, os.environ.get("CCNAVI_SH_DIR", "") or ".ccnavi/scripts")
HOST_SH = os.path.join(SH_DIR, "ccnavi-common-host.sh")
TABLE = os.path.join(ROOT, "tests", "fixtures", "host-kinds.json")
SHELL = shutil.which("sh") or shutil.which("bash")

PROBE = """. "$1"
ccnavi_host_parse "$2"
rc=$?
printf 'rc=%s\\nkind=%s\\nclass=%s\\npath=%s\\napi=%s\\ngraphql=%s\\nwhy=%s\\n' \\
  "$rc" "${ccnavi_h_kind:-}" "${ccnavi_h_class_host:-}" "${ccnavi_h_path:-}" \\
  "${ccnavi_h_api_base:-}" "${ccnavi_h_graphql_base:-}" "${ccnavi_h_parse_why:-}"
"""


def read(origin: str) -> dict[str, str]:
    done = subprocess.run(
        [SHELL, "-c", PROBE, "probe", HOST_SH, origin],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    return dict(line.split("=", 1) for line in done.stdout.splitlines() if "=" in line)


@unittest.skipIf(SHELL is None, "sh が無い")
class HostKindsShTest(unittest.TestCase):
    def test_every_case_in_the_shared_table(self):
        with open(TABLE, encoding="utf-8") as f:
            table = json.load(f)["cases"]
        for case in table:
            with self.subTest(origin=case["origin"]):
                got = read(case["origin"])
                if "problem" in case:
                    self.assertEqual(got["rc"], "4")
                    self.assertIn(case["problem"], got["why"])
                    continue
                self.assertEqual(got["rc"], "0")
                self.assertEqual(got["kind"], case["kind"])
                self.assertEqual(got["class"], case["host"])
                self.assertEqual(got["path"], case["path"])
                self.assertEqual(got["api"], case["api"])
                self.assertEqual(got["graphql"], case["graphql"] or "")

    def test_unreadable_forms_stop(self):
        self.assertEqual(read("not a url")["rc"], "1")


if __name__ == "__main__":
    unittest.main()
