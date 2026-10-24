"""ブランチ名に使えない語の 2 つのコピーが一致することを、テストで守る。

`infra.settings` はブランチ名の先頭の語に使えない語（`_RESERVED_PREFIXES`）を持ち、
`tickets.ticket_ids` は識別子に使えない語（`RESERVED_BRANCH_IDS`）を持つ。中身は同じ
「統合先や保護されたブランチの名前」のリストだが、`infra` は `tickets` を読めない
（サブパッケージの順。tests/core/test_module_layers.py）ので、`settings` はコピーを持つ。
片方だけを直すと、先頭の語には使えるのに識別子には使えない語のような食い違いが、
気づかないうちに生まれる。
"""

from __future__ import annotations

import unittest

from ccnavi.infra import settings
from ccnavi.tickets import ticket_ids


class ReservedBranchNamesTest(unittest.TestCase):
    def test_settings_copy_matches_ticket_ids(self):
        self.assertEqual(
            ticket_ids.RESERVED_BRANCH_IDS,
            settings._RESERVED_PREFIXES,
            "infra/settings.py の _RESERVED_PREFIXES は tickets/ticket_ids.py の "
            "RESERVED_BRANCH_IDS のコピー。両方を同じに直す",
        )


if __name__ == "__main__":
    unittest.main()
