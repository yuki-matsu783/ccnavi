"""記録を消す `--prune` の guard が、起動の sh（`ccnavi-launcher.sh`）経由の形も止める（#786）。

案内する打ち方を起動の sh に変えたので、
実行ファイルの名前だけを見ていると、記録を消す操作が通ってしまう。
"""

import unittest

from ccnavi.tickets.model import phase_forms

LAUNCHER = "sh .ccnavi/scripts/ccnavi-launcher.sh"


class PruneFormTest(unittest.TestCase):
    def test_launcher_prune_is_stopped(self):
        self.assertEqual(phase_forms.prune_form(f"{LAUNCHER} --prune"), "--prune")

    def test_launcher_prune_preview_passes(self):
        self.assertEqual(phase_forms.prune_form(f"{LAUNCHER} --prune --preview"), "")

    def test_direct_prune_is_still_stopped(self):
        self.assertEqual(phase_forms.prune_form("ccnavi --prune"), "--prune")


if __name__ == "__main__":
    unittest.main()
