"""ADR の番号の決まりを、テストで守る（issue #98）。

番号は 1 枚につき 1 つ。同じ番号が 2 枚にあると、本文や実装に書いた
`ADR-00NN` がどちらの決定を指すか読めない。実際に 3 組（0051・0058・0060）が
重なっていて、うち 1 枚は題に番号を書いていなかった。

一覧（`docs/30_adr.md`）は人が手で書き足す形なので、1 枚足したときに
番号を取り違えても誰も言わない。テストの ID に同じ検査を置いた回
（issue #88、`test/shared/test-ids.test.ts`）と同じ形で、見るのは 3 つ。

1. 番号が重複しない
2. 題が `# ADR-<番号>:` で始まり、その番号がファイル名と合う
3. ADR が、`docs/30_adr.md` の「置き場と分け方」の表にあるディレクトリに置かれている

止められるのは合流の後。 ADR を足しただけのコミットではこのテストは回らない
（`.claude/skills/commit/references/test-groups.md` の「`docs/30_adr/` → `core`」で
回るのは、その表を見てコミットしたときだけ。hook は未登録で、CI も無い）。
重複はもともと並行して伸びた枝が合流したときに起きるので、気づく場所が
「合流して誰かが `core` を回したとき」に早まる、という形で役に立つ。

読むのは `docs/30_adr/` のファイルだけで、判定の実行ファイルには触らない。
"""

from __future__ import annotations

import os
import re
import unittest

from tests import ROOT

ADR_DIR = os.path.join(ROOT, "docs", "30_adr")
ADR_INDEX = os.path.join(ROOT, "docs", "30_adr.md")

# `0064-extension-board-in-react.md` → 0064。番号を持たないファイルは外れる。
FILE_NAME = re.compile(r"^(\d{4})-.+\.md$")

# 題の 1 行目。`# ADR-0001: 言語を Go から Python に移す` の形。
#
# 例に 0001 を使うのは、この行自体が「その番号を引いている箇所」として
# 数えられるため。README の「引かれている数」は grep で数え直せる形にしてある。
TITLE = re.compile(r"^# ADR-(\d{4}): \S")

# 入口の表の 1 行。`| `30_adr/rules/` | 判定とルール… |`
TOPIC_ROW = re.compile(r"^\| `30_adr/([\w-]+)/` \|", re.M)


def sheets() -> list[str]:
    """`docs/30_adr/<主題>/` に置いてある ADR のパス（`<主題>/<ファイル名>`）。番号の順に並べる。"""
    found = []
    for topic in os.listdir(ADR_DIR):
        folder = os.path.join(ADR_DIR, topic)
        if os.path.isdir(folder):
            found.extend(f"{topic}/{f}" for f in os.listdir(folder) if FILE_NAME.match(f))
    return sorted(found, key=os.path.basename)


def number(path: str) -> str:
    return os.path.basename(path)[:4]


def read(path: str) -> str:
    with open(os.path.join(ADR_DIR, path), encoding="utf-8") as f:
        return f.read()


class AdrNumbersTest(unittest.TestCase):
    def setUp(self):
        self.sheets = sheets()
        # 書き方が変わったことに気づかずに「重複なし」と言わないため。
        self.assertGreater(len(self.sheets), 50, f"ADR を数えられていない（{ADR_DIR}）")

    def test_numbers_are_unique(self):
        """番号は重複しない。足すときは最後尾の次を採る。"""
        where: dict[str, list[str]] = {}
        for name in self.sheets:
            where.setdefault(number(name), []).append(name)
        duplicated = sorted(
            f"{num}: {' / '.join(names)}" for num, names in where.items() if len(names) > 1
        )
        self.assertEqual(
            [],
            duplicated,
            "同じ番号が 2 枚にある。引かれている数が少ないほうを空き番号へ動かし、"
            "旧番号を docs/30_adr.md の表に残す",
        )

    def test_title_carries_its_own_number(self):
        """題は `# ADR-<番号>:` で始まり、番号はファイル名と合う。"""
        wrong: list[str] = []
        for name in self.sheets:
            lines = read(name).splitlines()
            # 頭の frontmatter（.ccnavi/docs/frontmatter.md）を飛ばし、
            # その次の空でない行を題とみなす。
            if lines and lines[0] == "---" and "---" in lines[1:]:
                lines = lines[lines.index("---", 1) + 1 :]
            lines = [line for line in lines if line.strip()]
            first = lines[0] if lines else ""
            found = TITLE.match(first)
            if found is None:
                wrong.append(f"{name}: 題が `# ADR-<番号>: <題>` の形でない（{first[:40]}）")
            elif found.group(1) != number(name):
                wrong.append(f"{name}: 題の番号が ADR-{found.group(1)} でファイル名と違う")
        self.assertEqual([], wrong)

    def test_every_sheet_sits_in_a_listed_topic(self):
        """ADR は、入口の表にあるディレクトリのどれかに置かれている。"""
        with open(ADR_INDEX, encoding="utf-8") as f:
            listed = set(TOPIC_ROW.findall(f.read()))
        self.assertTrue(listed, f"入口の表からディレクトリを読めない（{ADR_INDEX}）")
        unlisted = sorted({n.split("/")[0] for n in self.sheets} - listed)
        self.assertEqual([], unlisted, "表に無いディレクトリがある。docs/30_adr.md の表に 1 行足す")
        empty = sorted(t for t in listed if not os.path.isdir(os.path.join(ADR_DIR, t)))
        self.assertEqual([], empty, "表にあって置かれていないディレクトリがある")

    def test_no_sheet_sits_at_the_top(self):
        """ADR を主題のディレクトリの外（`docs/30_adr/` 直下）に置かない。"""
        top = sorted(f for f in os.listdir(ADR_DIR) if FILE_NAME.match(f))
        self.assertEqual([], top)
