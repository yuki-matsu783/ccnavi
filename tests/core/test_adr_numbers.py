"""ADR の番号の決まりを、テストで守る（issue #98）。

番号は 1 枚につき 1 つ。同じ番号が 2 枚にあると、本文や実装に書いた
`ADR-00NN` がどちらの決定を指すか読めない。実際に 3 組（0051・0058・0060）が
重なっていて、うち 1 枚は題に番号を書いていなかった。

一覧（`docs/adr/README.md`）は人が手で書き足す形なので、枚を足したときに
番号を取り違えても誰も言わない。**人の注意ではなくここで止める。**
テストの ID に同じ番犬を置いた回（issue #88、`test/shared/test-ids.test.ts`）と
同じ形で、見るのは 3 つ。

1. 番号が重複しない
2. 題が `# ADR-<番号>:` で始まり、その番号がファイル名と合う
3. 一覧が、置いてある枚を 1 回ずつ載せ、実体と食い違わない

読むのは `docs/adr/` のファイルだけで、判定の実行ファイルには触らない。
"""

from __future__ import annotations

import os
import re
import unittest
from collections import Counter

from tests import ROOT

ADR_DIR = os.path.join(ROOT, "docs", "adr")

# `0064-extension-board-in-react.md` → 0064。README.md は番号を持たないので外れる。
FILE_NAME = re.compile(r"^(\d{4})-.+\.md$")

# 題の 1 行目。`# ADR-0001: 言語を Go から Python に移す` の形。
#
# 例に 0001 を使うのは、この行自体が「その番号を引いている箇所」として
# 数えられるため。README の「引かれている数」は grep で数え直せる形にしてある。
TITLE = re.compile(r"^# ADR-(\d{4}): \S")

# 一覧の 1 行。`| [0064](0064-extension-board-in-react.md) | ボードの画面を… |`
#
# 旧番号の表はリンクにしていないので、ここには入らない（入れると
# 「1 回ずつ」が数えられなくなる）。
LINK = re.compile(r"\[(\d{4})\]\((\d{4}-[^)]+\.md)\)")


def sheets() -> list[str]:
    """`docs/adr/` に置いてある枚のファイル名。番号の順に並べる。"""
    return sorted(f for f in os.listdir(ADR_DIR) if FILE_NAME.match(f))


def read(name: str) -> str:
    with open(os.path.join(ADR_DIR, name), encoding="utf-8") as f:
        return f.read()


class AdrNumbersTest(unittest.TestCase):
    def setUp(self):
        self.sheets = sheets()
        # 綴りが変わったことに気づかずに「重複なし」と言わないため。
        self.assertGreater(len(self.sheets), 50, f"ADR を数えられていない（{ADR_DIR}）")

    def test_numbers_are_unique(self):
        """番号は重複しない。足すときは最後尾の次を採る。"""
        where: dict[str, list[str]] = {}
        for name in self.sheets:
            where.setdefault(name[:4], []).append(name)
        duplicated = sorted(
            f"{num}: {' / '.join(names)}" for num, names in where.items() if len(names) > 1
        )
        self.assertEqual(
            [],
            duplicated,
            "同じ番号が 2 枚にある。引かれている数が少ないほうを空き番号へ動かし、"
            "旧番号を docs/adr/README.md の表に残す",
        )

    def test_title_carries_its_own_number(self):
        """題は `# ADR-<番号>:` で始まり、番号はファイル名と合う。"""
        wrong: list[str] = []
        for name in self.sheets:
            first = read(name).splitlines()[0] if read(name) else ""
            found = TITLE.match(first)
            if found is None:
                wrong.append(f"{name}: 題が `# ADR-<番号>: <題>` の形でない（{first[:40]}）")
            elif found.group(1) != name[:4]:
                wrong.append(f"{name}: 題の番号が ADR-{found.group(1)} でファイル名と違う")
        self.assertEqual([], wrong)

    def test_index_lists_every_sheet_once(self):
        """一覧は、置いてある枚を 1 回ずつ載せ、実体と食い違わない。"""
        linked = LINK.findall(read("README.md"))
        listed = [target for _, target in linked]

        missing = [name for name in self.sheets if name not in set(listed)]
        self.assertEqual([], missing, "一覧に無い枚がある（docs/adr/README.md）")

        phantom = [target for target in listed if target not in set(self.sheets)]
        self.assertEqual([], phantom, "一覧が指している枚が置かれていない")

        twice = sorted(name for name, count in Counter(listed).items() if count > 1)
        self.assertEqual([], twice, "一覧に 2 回載っている枚がある")

        mismatched = [f"[{num}]({target})" for num, target in linked if not target.startswith(num)]
        self.assertEqual([], mismatched, "一覧の番号とリンク先の番号が違う")
