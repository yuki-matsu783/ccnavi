"""sh の URL の資格情報の伏せ（`ccnavi_mask_url`・doctor の `doctor_mask`）。

トークンに `@` が入っても、ホストの手前の最後の `@` まで伏せること。scheme の付いた形と、
scheme の無い `user:token@host:path` の形の両方を見る。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import unittest

from tests import ROOT

COMMON = os.path.join(ROOT, ".ccnavi", "scripts", "ccnavi-common.sh")
DOCTOR = os.path.join(ROOT, "scripts", "ccnavi-doctor.sh")
SH = shutil.which("sh")

CASES = [
    ("https://oauth2:SEC@RET@host.example/g/p.git", "https://<伏せた>@host.example/g/p.git"),
    ("oauth2:SEC@RET@host.example:g/p.git", "<伏せた>@host.example:g/p.git"),
    ("git@host.example:g/p.git", "git@host.example:g/p.git"),
]


def run(script: str, value: str) -> str:
    # 値は引数でなく環境変数で渡す。Windows の引数は、`'` などをそのまま運べない。
    env = {**os.environ, "CCNAVI_PROBE": value}
    done = subprocess.run(
        [SH, "-c", script, COMMON], env=env, capture_output=True, text=True, check=True
    )
    return done.stdout.rstrip("\n")


def doctor_mask_source() -> str:
    # doctor は読み込むと診断を始めるので、関数の定義だけを取り出す。
    with open(DOCTOR, encoding="utf-8") as f:
        found = re.search(r"^doctor_mask\(\) \{\n.*?^\}\n", f.read(), re.M | re.S)
    assert found, "doctor_mask が見つからない"
    return found.group(0)


@unittest.skipUnless(SH, "sh が無い")
class MaskUrlTest(unittest.TestCase):
    def test_common_mask_url(self):
        script = '. "$0"; ccnavi_mask_url "$CCNAVI_PROBE"'
        for value, want in CASES:
            with self.subTest(value=value):
                self.assertEqual(run(script, value), want)

    def test_doctor_mask(self):
        script = doctor_mask_source() + 'doctor_mask "$CCNAVI_PROBE"'
        for value, want in CASES:
            with self.subTest(value=value):
                self.assertEqual(run(script, "git clone " + value), "git clone " + want)
