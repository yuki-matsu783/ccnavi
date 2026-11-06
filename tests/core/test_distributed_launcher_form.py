"""配布する文書・スキルが、PATH に無い `ccnavi` を直に打たせていないか（#786）。

配布先では `ccnavi` が PATH に無く、起動の sh 経由でしか動かない。
手順として書く `ccnavi --…` と `ccnavi ticket …` は、この形にする。
"""

import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# scripts/ccnavi-package.sh が配る、エージェントが読む場所
DISTRIBUTED = (".claude/skills", ".ccnavi/docs", ".ccnavi/messages", ".ccnavi/CCNAVI.md")
# コード片（`…`・'…'）か行頭に置いた、PATH 前提の呼び出し
BARE_CALL = re.compile(r"(?:^|[\s`'（(])ccnavi (?:--[a-z]|ticket )")


def _files(rel):
    path = os.path.join(ROOT, rel)
    if os.path.isfile(path):
        yield path
        return
    for base, _dirs, names in os.walk(path):
        for name in names:
            if name.endswith((".md", ".yml")):
                yield os.path.join(base, name)


class DistributedLauncherForm(unittest.TestCase):
    def test_no_bare_ccnavi_call(self):
        found = []
        for rel in DISTRIBUTED:
            for path in _files(rel):
                with open(path, encoding="utf-8") as fh:
                    for no, line in enumerate(fh, 1):
                        if BARE_CALL.search(line):
                            found.append(f"{os.path.relpath(path, ROOT)}:{no}")
        self.assertEqual(
            found, [], "PATH 前提の `ccnavi …` は `sh .ccnavi/scripts/ccnavi-launcher.sh …` に直す"
        )


if __name__ == "__main__":
    unittest.main()
