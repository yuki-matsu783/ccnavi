"""`ccnavi/` の依存の向きを、テストで守る。

パッケージは 33 モジュールの平屋で、層はディレクトリにも命名にも現れない。
向きは慣習だけで保たれていて、逆流しても誰も言わない。ここに層の順序を
1 か所だけ書き、その順序と食い違う import を名指しする。

読むのは `ccnavi/*.py` の import 文だけ。実行ファイルは起動しないので速い。

**関数の中の import も数える。** 循環は、頭の import を関数の中へ下ろすと
消えたように見える（`ops.py` の `from .review import WIP_ROOT` がそれ）。
`ast.walk` で全部拾うので、下ろしても隠れない。

見るのは 3 つ。

1. どのモジュールも層をちょうど 1 つ持つ。1 本足したら、どの層かを決めさせる
2. import の行き先は、同じ層か下の層。上を向いた 1 本を名指しする
3. 循環は `KNOWN_KNOTS` に書いた 2 組だけ。**多くても少なくても落とす。**
   ほどけたら一覧から消す、が要るようにしてある。消し忘れた一覧は、
   次に同じ場所が絡まったときに何も言わなくなる

層の中での import は自由にしてある。層は「どちらが先に読めるか」の順序で、
同じ層の中の結び付きは 3 番目が見る。

`main.py`（PyInstaller の入口）は `ccnavi/` の外なのでここには入らない。
中身は `__main__.py` と同じ 2 行で、`tests/core/test_entry.py` が見ている。
"""

from __future__ import annotations

import ast
import os
import unittest

from tests import ROOT

PACKAGE = os.path.join(ROOT, "ccnavi")

# 層は下から上へ。下の層は上の層を知らない。
#
# いまの形をそのまま写したもので、設計として先に引いた線ではない。効くのは
# 「ここから先は崩さない」という向きで、層をまたぐ付け替えをするときは、
# この表を先に直してから動かす（直さずに通ったなら、それは逆流していない）。
LAYERS: tuple[tuple[str, str, frozenset[str]], ...] = (
    (
        "base",
        "素材。副作用の無い部品と、素の入出力",
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
    ("read", "読み手。設定・git・ルールを読む", frozenset({"gitstate", "modes", "rules"})),
    (
        "state",
        "状態。作業ツリーとチケットの、いまの形",
        frozenset({"builtin", "ctxfile", "risk", "selfguard", "ticket"}),
    ),
    ("layer", "層。3 層の和を組む", frozenset({"phasetypes", "ruleload"})),
    ("work", "承認とフェーズと、それに添える文面", frozenset({"approval", "phase", "reasons"})),
    ("decide", "判定と、チケット・レビューの操作", frozenset({"judge", "ops", "post", "review"})),
    (
        "entry",
        "入口と診断。ここを import するものは無い",
        frozenset({"cli", "diagnose", "events", "lint", "subagent", "__main__"}),
    ),
)

# 残っている循環。ほどく提案は出したうえで、いまは既知として通す。
#
# - approval → reasons → phase → approval
#   承認済みチケットの走査（approval）と、フェーズの状態（phase）が互いを読む。
#   文面（reasons）が phase を読むので、輪は 3 本で閉じる
# - ops ↔ review
#   `ops` が `review` の置き場の綴りを関数の中で引き、`review` が
#   `ops.close_problems` と `ops.cancel` を呼ぶ
#
# 組は「名前を並べたもの」で持つ。輪の向きではなく、絡まっている顔ぶれを見る。
KNOWN_KNOTS: frozenset[tuple[str, ...]] = frozenset(
    {
        ("approval", "phase", "reasons"),
        ("ops", "review"),
    }
)

LAYER_ORDER = {name: i for i, (name, _, _) in enumerate(LAYERS)}
LAYER_OF = {mod: name for name, _, mods in LAYERS for mod in mods}
LAYER_NOTE = {name: note for name, note, _ in LAYERS}


def modules() -> list[str]:
    """`ccnavi/` に置いてあるモジュールの名前。"""
    return sorted(f[:-3] for f in os.listdir(PACKAGE) if f.endswith(".py"))


def imports_of(module: str, known: set[str]) -> set[str]:
    """そのモジュールが import している、同じパッケージのモジュール。

    頭のものも関数の中のものも同じに数える。`from . import a, b` と
    `from .a import x` と `from ccnavi.a import x` の 3 通りを拾う。
    """
    with open(os.path.join(PACKAGE, module + ".py"), encoding="utf-8") as f:
        tree = ast.parse(f.read())

    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level == 1 and node.module is None:
                # from . import approval, audit, ...
                found |= {alias.name for alias in node.names}
            elif node.level == 1 and node.module:
                # from .modes import EXIT_OK
                found.add(node.module.split(".")[0])
            elif not node.level and node.module and node.module.startswith("ccnavi."):
                found.add(node.module.split(".")[1])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("ccnavi."):
                    found.add(alias.name.split(".")[1])
    return found & known


def graph() -> dict[str, set[str]]:
    known = set(modules())
    return {mod: imports_of(mod, known) for mod in sorted(known)}


def knots(edges: dict[str, set[str]]) -> set[tuple[str, ...]]:
    """互いに行き来できるモジュールの組（2 本以上のもの）。

    Tarjan は再帰が深くなるので、素直に 2 度の深さ優先で求める（Kosaraju）。
    モジュールは数十本なので、速さは問題にならない。
    """
    order: list[str] = []
    seen: set[str] = set()

    def walk(start: str, nexts: dict[str, set[str]], out: list[str]) -> None:
        # 再帰にしないのは、深さではなく、落ちたときに読める形を選んだため。
        stack = [(start, iter(sorted(nexts.get(start, ()))))]
        seen.add(start)
        while stack:
            node, rest = stack[-1]
            following = next(rest, None)
            if following is None:
                stack.pop()
                out.append(node)
            elif following not in seen:
                seen.add(following)
                stack.append((following, iter(sorted(nexts.get(following, ())))))

    for mod in sorted(edges):
        if mod not in seen:
            walk(mod, edges, order)

    backward: dict[str, set[str]] = {mod: set() for mod in edges}
    for mod, targets in edges.items():
        for target in targets:
            backward[target].add(mod)

    seen = set()
    groups: set[tuple[str, ...]] = set()
    for mod in reversed(order):
        if mod in seen:
            continue
        group: list[str] = []
        walk(mod, backward, group)
        if len(group) > 1:
            groups.add(tuple(sorted(group)))
    return groups


class ModuleLayersTest(unittest.TestCase):
    def setUp(self):
        self.modules = modules()
        # 綴りが変わったことに気づかずに「逆流なし」と言わないため。
        self.assertGreater(len(self.modules), 20, f"モジュールを数えられていない（{PACKAGE}）")
        self.edges = graph()

    def test_every_module_sits_in_exactly_one_layer(self):
        """どのモジュールも層をちょうど 1 つ持つ。"""
        placed = [mod for name, _, mods in LAYERS for mod in sorted(mods)]
        twice = sorted({mod for mod in placed if placed.count(mod) > 1})
        self.assertEqual([], twice, "同じモジュールが 2 つの層にある（LAYERS）")

        missing = [mod for mod in self.modules if mod not in LAYER_OF]
        self.assertEqual(
            [],
            missing,
            "層の決まっていないモジュールがある。LAYERS に足す。層の意味は "
            + " / ".join(f"{name}: {note}" for name, note, _ in LAYERS),
        )

        phantom = sorted(set(LAYER_OF) - set(self.modules))
        self.assertEqual([], phantom, "LAYERS が、置かれていないモジュールを挙げている")

    def test_no_module_imports_a_higher_layer(self):
        """import の行き先は、同じ層か下の層。"""
        upward: list[str] = []
        for mod in sorted(self.edges):
            here = LAYER_OF.get(mod)
            if here is None:
                continue  # 層が無いことは 1 つ目のテストが言う
            for target in sorted(self.edges[mod]):
                there = LAYER_OF.get(target)
                if there is None:
                    continue
                if LAYER_ORDER[there] > LAYER_ORDER[here]:
                    upward.append(f"{mod}({here}) -> {target}({there})")
        self.assertEqual(
            [],
            upward,
            "下の層が上の層を import している。呼ぶ側から材料を渡すか、"
            "共通の部分を下の層へ出す。層そのものを組み替えるなら LAYERS を先に直す",
        )

    def test_only_the_known_knots_are_cyclic(self):
        """循環は KNOWN_KNOTS に書いた組だけ。増えても減っても落とす。"""
        found = knots(self.edges)

        fresh = sorted(" ↔ ".join(group) for group in found - KNOWN_KNOTS)
        self.assertEqual(
            [],
            fresh,
            "新しい循環ができている。片方向に直す（関数の中へ import を下ろすのは"
            "隠すだけで、ここは同じに数える）",
        )

        gone = sorted(" ↔ ".join(group) for group in KNOWN_KNOTS - found)
        self.assertEqual(
            [],
            gone,
            "既知の循環がほどけている。KNOWN_KNOTS から消す（残すと、次に同じ場所が"
            "絡まったときに何も言わなくなる）",
        )


if __name__ == "__main__":
    unittest.main()
