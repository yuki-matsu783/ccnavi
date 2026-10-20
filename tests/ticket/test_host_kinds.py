"""origin の URL からのホストの分類（GitHub / GitLab）と接続先。実行ファイル側の読み手。

同じ表（`tests/fixtures/host-kinds.json`）を、sh（`tests/sh/test_host_kinds_sh.py`）と Chrome 拡張
（`test/hostclass.test.ts`）も読む。分類に使うホストは小文字・ポート無しで、`.` の区切りで照合する。
"""

from __future__ import annotations

import json
import os
import unittest

from ccnavi.tickets import review_host
from tests import ROOT

TABLE = os.path.join(ROOT, "tests", "fixtures", "host-kinds.json")


def cases() -> list[dict]:
    with open(TABLE, encoding="utf-8") as f:
        return json.load(f)["cases"]


class HostKindsTest(unittest.TestCase):
    def test_every_case_in_the_shared_table(self):
        for case in cases():
            with self.subTest(origin=case["origin"]):
                got = review_host.classify(case["origin"])
                self.assertEqual(got.kind, case["kind"])
                self.assertEqual(got.host, case["host"])
                self.assertEqual(got.path, case["path"])
                self.assertEqual(review_host.remote_kind(case["origin"]), case["kind"])
                if "problem" in case:
                    self.assertIn(case["problem"], got.problem)
                    self.assertEqual((got.api, got.graphql), ("", ""))
                else:
                    self.assertEqual(got.problem, "")
                    self.assertEqual(got.api, case["api"])
                    self.assertEqual(got.graphql, case["graphql"] or "")

    def test_a_ghe_com_origin_with_a_port_stops_with_the_reason(self):
        problem = review_host.transport_problem("https://nri.ghe.com:8443/acme/widgets.git")
        self.assertIn("ポート", problem)
        self.assertIn("API の URL", problem)

    def test_matching_is_by_label_not_by_suffix(self):
        for host in ("evilgithub.com", "github.com.evil", "evilghe.com", "ghe.com.evil"):
            self.assertEqual(review_host.remote_kind(f"https://{host}/a/b.git"), "gitlab", host)

    def test_an_unreadable_form_is_still_unreadable(self):
        for url in ("ssh://user@host.example.com/g/p.git", "not a url"):
            self.assertEqual(review_host.remote_kind(url), "")


if __name__ == "__main__":
    unittest.main()
