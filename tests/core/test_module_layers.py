"""`ccnavi/` の import の向きを、テストで守る。

`ccnavi/` は 1 つのディレクトリにモジュールが並ぶだけで、読む順はディレクトリにも
命名にも現れない。向きは慣習だけで保たれていて、逆流しても誰も言わない。ここに
読む順を 1 か所だけ書き、その順と食い違う import を名指しする。

「層」ではなく「段」と呼ぶのは、このリポジトリでは層が設定の 3 層（共通層・
自身の層・プロジェクトの層）を指すため。ここで言う段はモジュールを読む順で、
別のもの。

読むのは `ccnavi/*.py` の import 文だけ。実行ファイルは起動しないので速い。

**関数の中の import も数える。** 循環は、先頭の import を関数の中へ移すと
消えたように見える。`ast.walk` ですべて拾うので、移しても隠れない。

見るのは 5 つ。

1. どのモジュールも段をちょうど 1 つ持つ。モジュールを足したら、どの段かを決めさせる
2. `ccnavi/` の下にサブパッケージを作らない。作ると 1・3・4 の検査の対象から外れる
3. import の行き先は、同じ段か下の段。上を向いた import を名指しする
4. 循環は `KNOWN_KNOTS` に書いた 2 組だけ。**増えても減っても落とす。**
   解消したら一覧から消す、が要るようにしてある。消し忘れた一覧は、次に同じ
   場所で循環ができたときに何も言わなくなる
5. import の行き先が、import 文に残らない書き方をしていない

同じ段どうしの import は止めない。段は「どちらを先に読めるか」の順序であって、
同じ段の中の結び付きは 4 が見る。**同じ段どうしの事故（たとえば判定がチケットを
動かし始める形）は、このテストでは止まらない。**

**5 が要るのは、1 から 4 が import 文しか読まないから。**
`importlib.import_module("ccnavi.judge")` と、ドットの無い `import ccnavi` に
続く `ccnavi.judge.…` は、行き先が import 文に残らないので 1 つも見つからない。
どちらも `ccnavi/` では 1 度も使っていないので、綴りごと止めるほうが安い。
**すり抜けられないわけではない。** `getattr` や `exec` で組み立てれば、いまでも
隠せる。そこまで塞ぐには import を実行時に捕まえるしかなく、この速さを手放す。

`main.py`（PyInstaller の入口）は `ccnavi/` の外なので、ここには入らない。
中身は `__main__.py` と同じ 2 行で、`tests/core/test_entry.py` が見ている。

`core` はいつも回るので（`.claude/skills/commit/references/test-groups.md`）、
`ccnavi/*.py` を変えたコミットでは必ずここも走る。
"""

from __future__ import annotations

import ast
import os
import unittest

from tests import ROOT

PACKAGE = os.path.join(ROOT, "ccnavi")

# 段は下から上へ。下の段は上の段を知らない。
#
# **いまの依存の深さを写したもので、意味で先に引いた線ではない。** 設計書の章立て
# （ルール・実行前・実行後・チケット）で切ると双方向の辺が残って段にならないので、
# 深さで切ってある。注記はその段に何が居るかの説明であって、そこへ置く根拠ではない。
#
# 段をまたぐ付け替えをするなら、この表を先に直してから動かす。直さずに通ったなら、
# それは逆流していない。表を動かすときは、なぜその向きが正しいかをコミットに書く。
TIERS: tuple[tuple[str, str, frozenset[str]], ...] = (
    (
        "base",
        "同じパッケージのどのモジュールも読まない",
        frozenset(
            {
                "__init__",
                "audit",
                "fsio",
                "gitcmd",
                "globmatch",
                "hookio",
                "platformtag",
                "settings",
                "shellread",
                "tree",
            }
        ),
    ),
    (
        "read",
        "最下段だけを読む。git の状態・ルール・動作モード",
        frozenset({"gitstate", "modes", "rules"}),
    ),
    (
        "state",
        "作業ツリーとチケットの、いまの形を読む",
        frozenset({"builtin", "ctxfile", "risk", "selfguard", "ticket", "wrapguard"}),
    ),
    (
        "compose",
        "層ごとの設定を読んで、判定の材料に組む",
        frozenset({"phasetypes", "ruleload", "workflow"}),
    ),
    (
        "work",
        "承認済みチケットとフェーズと、それに添える文面",
        frozenset({"approval", "configsync", "phase", "reasons"}),
    ),
    (
        "decide",
        "判定と、チケット・レビューを動かす操作",
        frozenset({"judge", "ops", "post", "review"}),
    ),
    (
        "entry",
        "入口と診断。下の段からは読まれない",
        frozenset({"cli", "diagnose", "events", "lint", "subagent", "__main__"}),
    ),
)

# 残っている循環。いまは既知として通す。
#
# - approval ↔ phase
#   承認済みチケットの走査（approval）と、フェーズの状態（phase）が互いを直に読む。
#   これが循環の中心。文面（reasons）は approval から読まれて phase を読むので、
#   同じ組に入る
# - ops ↔ review
#   `ops` が `review` の置き場の綴りを関数の中で引き、`review` が
#   `ops.close_problems` と `ops.cancel` を呼ぶ
#
# 組は「どのモジュールが入っているか」で持つ。循環の向きや本数は見ない。
KNOWN_KNOTS: frozenset[tuple[str, ...]] = frozenset(
    {
        ("approval", "phase", "reasons"),
        ("ops", "review"),
    }
)

TIER_ORDER = {name: i for i, (name, _, _) in enumerate(TIERS)}
TIER_OF = {mod: name for name, _, mods in TIERS for mod in mods}
TIER_MEANING = " / ".join(f"{name}: {note}" for name, note, _ in TIERS)


def modules() -> list[str]:
    """`ccnavi/` に置いてあるモジュールの名前。"""
    return sorted(f[:-3] for f in os.listdir(PACKAGE) if f.endswith(".py"))


def subpackages() -> list[str]:
    """`ccnavi/` の下のディレクトリ。モジュールが 1 つのディレクトリに並んでいるかを見る。"""
    return sorted(
        name
        for name in os.listdir(PACKAGE)
        if name != "__pycache__" and os.path.isdir(os.path.join(PACKAGE, name))
    )


def parse(module: str) -> ast.Module:
    with open(os.path.join(PACKAGE, module + ".py"), encoding="utf-8") as f:
        return ast.parse(f.read())


def imports_of(module: str, known: set[str]) -> set[str]:
    """そのモジュールが import している、同じパッケージのモジュール。

    頭のものも関数の中のものも同じに数える。拾うのは 4 通り。

        from . import approval, audit      from .modes import EXIT_OK
        from ccnavi import settings        from ccnavi.modes import EXIT_OK

    後ろの 2 つは `main.py` が使っている形。パッケージの中で同じ書き方をされても
    見落とさないように、相対と同じに数える。
    """
    found: set[str] = set()
    for node in ast.walk(parse(module)):
        if isinstance(node, ast.ImportFrom):
            if (node.level == 1 and node.module is None) or (
                not node.level and node.module == "ccnavi"
            ):
                found |= {alias.name for alias in node.names}
            elif node.level == 1 and node.module:
                found.add(node.module.split(".")[0])
            elif not node.level and node.module and node.module.startswith("ccnavi."):
                found.add(node.module.split(".")[1])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("ccnavi."):
                    found.add(alias.name.split(".")[1])
    return found & known


def hiding_in(module: str) -> list[str]:
    """そのモジュールで使われている、行き先を import 文から隠す綴り。

    - `import ccnavi`（ドット無し）。`import ccnavi.judge` と違い、行き先が
      import 文に出ない。使うときは `ccnavi.judge.…` という属性の参照になる
    - `importlib` / `__import__` / `sys.modules`。行き先が文字列になる
    """
    found: list[str] = []
    for node in ast.walk(parse(module)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "ccnavi":
                    found.append(f"{module}.py:{node.lineno} import ccnavi")
                elif alias.name.split(".")[0] == "importlib":
                    found.append(f"{module}.py:{node.lineno} import importlib")
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[0] == "importlib":
                found.append(f"{module}.py:{node.lineno} from importlib import ...")
        elif isinstance(node, ast.Name) and node.id == "__import__":
            found.append(f"{module}.py:{node.lineno} __import__")
        elif (
            isinstance(node, ast.Attribute)
            and node.attr == "modules"
            and isinstance(node.value, ast.Name)
            and node.value.id == "sys"
        ):
            found.append(f"{module}.py:{node.lineno} sys.modules")
    return found


def graph() -> dict[str, set[str]]:
    known = set(modules())
    return {mod: imports_of(mod, known) for mod in sorted(known)}


def knots(edges: dict[str, set[str]]) -> set[tuple[str, ...]]:
    """互いに行き来できるモジュールの組（2 つ以上のもの）。

    数十本しかないので、行ける先を広げきってから、行きと帰りの両方があるものを
    集める。速い代わりに読みにくい手（Tarjan / Kosaraju）は要らない。
    """
    reach = {mod: set(targets) for mod, targets in edges.items()}
    growing = True
    while growing:
        growing = False
        for mod in reach:
            wider = set(reach[mod])
            for target in reach[mod]:
                wider |= reach.get(target, set())
            if wider != reach[mod]:
                reach[mod] = wider
                growing = True

    groups: set[tuple[str, ...]] = set()
    for mod in edges:
        group = tuple(
            sorted(
                other
                for other in edges
                if other == mod or (other in reach[mod] and mod in reach[other])
            )
        )
        if len(group) > 1:
            groups.add(group)
    return groups


class ModuleTiersTest(unittest.TestCase):
    def setUp(self):
        self.modules = modules()
        # 綴りが変わったことに気づかずに「逆流なし」と言わないため。
        self.assertGreater(len(self.modules), 20, f"モジュールを数えられていない（{PACKAGE}）")
        self.edges = graph()

    def test_every_module_sits_in_exactly_one_tier(self):
        """どのモジュールも段をちょうど 1 つ持つ。"""
        placed = [mod for _, _, mods in TIERS for mod in sorted(mods)]
        twice = sorted({mod for mod in placed if placed.count(mod) > 1})
        self.assertEqual([], twice, "同じモジュールが 2 つの段にある（TIERS）")

        missing = [mod for mod in self.modules if mod not in TIER_OF]
        self.assertEqual(
            [],
            missing,
            "段の決まっていないモジュールがある。TIERS に足す。置き場は「import して"
            f"いる先のうち一番高い段」と同じか、その 1 つ上。段の意味は {TIER_MEANING}",
        )

        phantom = sorted(set(TIER_OF) - set(self.modules))
        self.assertEqual([], phantom, "TIERS が、置かれていないモジュールを挙げている")

    def test_the_package_stays_flat(self):
        """`ccnavi/` の下にサブパッケージを作らない。"""
        self.assertEqual(
            [],
            subpackages(),
            "`ccnavi/` にサブパッケージができている。このテストは `ccnavi/*.py` しか"
            "見ないので、中のモジュールは段を持たないまま素通りする。TIERS を"
            "入れ子に直すか、モジュールを `ccnavi/` の直下に戻す",
        )

    def test_no_module_imports_a_higher_tier(self):
        """import の行き先は、同じ段か下の段。"""
        upward = [
            f"{mod}({TIER_OF[mod]}) -> {target}({TIER_OF[target]})"
            for mod in sorted(self.edges)
            if mod in TIER_OF
            for target in sorted(self.edges[mod])
            if target in TIER_OF and TIER_ORDER[TIER_OF[target]] > TIER_ORDER[TIER_OF[mod]]
        ]
        self.assertEqual(
            [],
            upward,
            "下の段が上の段を import している。呼ぶ側から材料を渡すか、共通の部分を"
            "下の段へ出す。段そのものを組み替えるなら TIERS を先に直す",
        )

    def test_only_the_known_knots_are_cyclic(self):
        """循環は KNOWN_KNOTS に書いた組だけ。増えても減っても落とす。"""
        found = knots(self.edges)

        fresh = sorted(" ↔ ".join(group) for group in found - KNOWN_KNOTS)
        self.assertEqual(
            [],
            fresh,
            "KNOWN_KNOTS に無い循環がある。新しくできたか、既知の循環に入る"
            "モジュールが変わったか。片方向に直す（関数の中へ import を移すのは"
            "隠すだけで、ここは同じに数える）",
        )

        gone = sorted(" ↔ ".join(group) for group in KNOWN_KNOTS - found)
        self.assertEqual(
            [],
            gone,
            "KNOWN_KNOTS に書いた循環が見つからない。解消したか、入るモジュールが"
            "変わったか。一覧を直す（残すと、次に同じ場所で循環ができたときに"
            "何も言わなくなる）",
        )

    def test_no_module_hides_where_it_is_going(self):
        """行き先を import 文から隠す綴りを使わない。"""
        hidden = [spell for mod in self.modules for spell in hiding_in(mod)]
        self.assertEqual(
            [],
            hidden,
            "import の行き先が import 文に残らない書き方をしている。1 から 4 は"
            "この形を 1 つも見つけられないので、綴りのほうを止める。どうしても"
            "要るなら、なぜ要るかを添えてここに例外を書く",
        )


if __name__ == "__main__":
    unittest.main()
