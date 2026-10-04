"""`src/ccnavi/` の import の向きを、テストで守る。

`src/ccnavi/` は役割ごとのサブパッケージ 6 つ（`PACKAGES`）に分かれ、その中にモジュールが並ぶ。
読む順は 2 つの表で書く。サブパッケージの順（`PACKAGES`）と、モジュールの段（`TIERS`）。
ディレクトリが言えるのは「どの役割か」までで、同じサブパッケージの中の読む順は言えない。
段がそれを補う。2 つの表と食い違う import を名指しする。

「層」ではなく「段」と呼ぶのは、このリポジトリでは層が設定の 3 層（共通層・
自身の層・プロジェクトの層）を指すため。ここで言う段はモジュールを読む順で、
別のもの。

読むのは `src/ccnavi/` の下の `.py` の import 文だけ。実行ファイルは起動しないので速い。
モジュールはドットでつないだ名前で呼ぶ（`infra.fsio`）。直下の `__init__` と
`__main__` はそのままの名前。

**関数の中の import も数える。** 循環は、先頭の import を関数の中へ移すと
消えたように見える。`ast.walk` ですべて拾うので、移しても隠れない。

見るのは 6 つ。

1. どのモジュールも段をちょうど 1 つ持つ。モジュールを足したら、どの段かを決めさせる
2. 置き場の形。`src/ccnavi/` の直下のディレクトリは `PACKAGES` と同じで、それより深い階層を
   作らない。どのサブパッケージにも `__init__.py` があり、中身は役割を書いた docstring だけ
   （再輸出しない。`from .infra import fsio` の行き先が `infra/__init__.py` にならないように）
3. import の行き先は、同じ段か下の段。上を向いた import を名指しする
4. 循環を 1 つも許さない。既知として通す一覧も持たない
5. import の行き先が、import 文に残らない書き方をしていない
6. サブパッケージをまたぐ import は、`PACKAGES` で下のサブパッケージへだけ

同じ段どうしの import は止めない。段は「どちらを先に読めるか」の順序であって、
同じ段の中の結び付きは 4 が見る。**同じ段どうしの問題（たとえば判定がチケットを
動かし始める形）は、段では止まらない。** サブパッケージをまたぐなら 6 が見る。

**5 が要るのは、1 から 4 と 6 が import 文しか読まないから。**
`importlib.import_module("ccnavi.hook.judge")` と、ドットの無い `import ccnavi` に
続く `ccnavi.hook.judge.…` は、行き先が import 文に残らないので 1 つも見つからない。
どちらも `src/ccnavi/` では 1 度も使っていないので、書き方ごと止めるほうが安い。
同じ理由で、パッケージの中の書き方を相対の 2 形に限る。同じサブパッケージは
`from . import x`、別のサブパッケージは `from ..infra import fsio`（`from ..infra.modes import
EXIT_OK` も可）。3 段以上の相対（`from ...`）、自分のサブパッケージを `..` で指す形
（`infra` の中の `from ..infra import fsio`。同じサブパッケージは `from . import fsio`）、
`from .. import infra`（行き先がサブパッケージ
そのもので、モジュールが import 文に出ない）、パッケージの中の `ccnavi.` で始まる絶対の import
は止める。**回避できないわけではない。** `getattr` や `exec` で組み立てれば、いまでも
隠せる。そこまで防ぐには import を実行時に捕まえるしかなく、この速さを手放す。

`main.py`（PyInstaller の入口）は `src/ccnavi/` の外なので、ここには入らない。
中身は `__main__.py` と同じ 2 行で、`tests/core/test_entry.py` が見ている。

`core` はいつも回るので（`.claude/skills/commit/references/test-groups.md`）、
`src/ccnavi/` の `.py` を変えたコミットでは必ずここも走る。
"""

from __future__ import annotations

import ast
import os
import unittest

from tests import SRC

PACKAGE = os.path.join(SRC, "ccnavi")

# サブパッケージは下から上へ。下のサブパッケージは上のサブパッケージを知らない。
#
# 段（TIERS）と違い、こちらは役割で引いた線。依存の向きがこの順に収まることを
# 6 の検査で確かめる。モジュールがどのサブパッケージに居るかはディレクトリで決まり、
# ここには書かない。サブパッケージを足す・並べ替えるときは、この表を先に直す。
PACKAGES: tuple[tuple[str, str], ...] = (
    ("infra", "土台。ファイル・git・パス照合・hook の入出力・設定・シェルの読み・ワークツリー"),
    ("records", "記録。伏せ字・判定の記録・診断ログ・後始末・拒否の数え"),
    ("policy", "ルール。読み込み・照合・組み込み・層の合成・自己防衛・文脈ファイル"),
    (
        "tickets",
        "チケット。承認済みチケットの置き場（approval）と合意の手続き（agree）、フェーズ、操作",
    ),
    ("hook", "hook の判定と、その文面・実行後の監視・イベント"),
    ("entry", "入口。CLI・診断・lint・提案・版"),
)

# 段は下から上へ。下の段は上の段を知らない。
#
# **いまの依存の深さをなぞったもので、意味で先に引いた線ではない。** 設計書の章立て
# （ルール・実行前・実行後・チケット）で切ると双方向の辺が残って段にならないので、
# 深さで切ってある。注記はその段に何が居るかの説明であって、そこへ置く根拠ではない。
# 役割の線はサブパッケージ（PACKAGES）が引く。
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
                "infra.fsio",
                "infra.gitcmd",
                "infra.globmatch",
                "infra.hookio",
                "infra.platformtag",
                "infra.settings",
                "infra.shellread",
                "infra.tree",
                "infra.yamlread",
                "records.redact",
            }
        ),
    ),
    (
        "read",
        "最下段だけを読む。git の状態・ルール・動作モード・記録の 1 行と後始末・拒否の数え",
        frozenset(
            {
                "infra.gitstate",
                "infra.modes",
                "policy.rules",
                "records.audit",
                "records.diaglog",
                "records.prune",
                "records.repeat",
            }
        ),
    ),
    (
        "state",
        "作業ツリーとチケットの、いまの形を読む",
        frozenset(
            {
                "hook.wrapguard",
                "policy.builtin",
                "policy.ctxfile",
                "policy.selfguard",
                "tickets.archive",
                "tickets.flow",
                "tickets.history",
                "tickets.risk",
                "tickets.syncstate",
                "tickets.ticket",
            }
        ),
    ),
    (
        "compose",
        "層ごとの設定を読んで、判定の材料に組む",
        frozenset(
            {
                "hook.docsearch",
                "hook.projskills",
                "policy.ruleload",
                "tickets.phasetypes",
                "tickets.workflow",
            }
        ),
    ),
    (
        "work",
        "承認済みチケットとフェーズと、それに添える文面",
        frozenset(
            {
                "hook.reasons",
                "tickets.agree",
                "tickets.approval",
                "tickets.branchfind",
                "tickets.configsync",
                "tickets.phase",
            }
        ),
    ),
    (
        "decide",
        "判定と、チケット・レビューを動かす操作",
        frozenset(
            {"hook.c1", "hook.core", "hook.judge", "tickets.ops", "hook.post", "tickets.review"}
        ),
    ),
    (
        "entry",
        "入口と診断。下の段からは読まれない",
        frozenset(
            {
                "__main__",
                "entry.cli",
                "entry.diagnose",
                "entry.lint",
                "entry.lint_branch",
                "entry.lint_ticket",
                "entry.lint_layers",
                "entry.lint_places",
                "entry.lint_project",
                "entry.lint_rules",
                "entry.status",
                "entry.suggest",
                "entry.version",
                "hook.events",
                "hook.subagent",
            }
        ),
    ),
)

TIER_ORDER = {name: i for i, (name, _, _) in enumerate(TIERS)}
TIER_OF = {mod: name for name, _, mods in TIERS for mod in mods}
TIER_MEANING = " / ".join(f"{name}: {note}" for name, note, _ in TIERS)
PACKAGE_ORDER = {name: i for i, (name, _) in enumerate(PACKAGES)}
PACKAGE_MEANING = " / ".join(f"{name}: {note}" for name, note in PACKAGES)
# 直下に置いてよいモジュール。パッケージの目印と `python -m ccnavi` の入口だけ。
TOP_LEVEL = frozenset({"__init__", "__main__"})


def package_of(module: str) -> str:
    """モジュールの居るサブパッケージ。直下のモジュールなら空。"""
    return module.split(".")[0] if "." in module else ""


def path_of(module: str) -> str:
    return os.path.join(PACKAGE, *module.split(".")) + ".py"


def directories() -> list[str]:
    """`src/ccnavi/` の直下のディレクトリ（`__pycache__` を除く）。"""
    return sorted(
        name
        for name in os.listdir(PACKAGE)
        if name != "__pycache__" and os.path.isdir(os.path.join(PACKAGE, name))
    )


def modules() -> list[str]:
    """`src/ccnavi/` に置いてあるモジュールの、ドットでつないだ名前。

    直下の `.py` と、直下のディレクトリの `.py`。サブパッケージの `__init__.py` は数えない
    （中身が docstring だけであることを 2 で見る）。
    """
    found = [f[:-3] for f in os.listdir(PACKAGE) if f.endswith(".py")]
    for package in directories():
        found += [
            f"{package}.{f[:-3]}"
            for f in os.listdir(os.path.join(PACKAGE, package))
            if f.endswith(".py") and f != "__init__.py"
        ]
    return sorted(found)


def parse(module: str) -> ast.Module:
    with open(path_of(module), encoding="utf-8") as f:
        return ast.parse(f.read())


def _targets(module: str, node: ast.AST) -> set[str]:
    """1 つの import 文の行き先（ドットでつないだモジュール名）。行き先が言えない形は空。"""
    here = package_of(module)
    found: set[str] = set()
    if isinstance(node, ast.ImportFrom):
        parts = node.module.split(".") if node.module else []
        if node.level == 1 and here:
            # 同じサブパッケージ。`from . import x` / `from .x import y`
            found |= (
                {f"{here}.{a.name}" for a in node.names} if not parts else {f"{here}.{parts[0]}"}
            )
        elif node.level == 1:
            # 直下のモジュールから。`from .entry import cli` / `from .entry.cli import main`
            if len(parts) >= 2:
                found.add(".".join(parts[:2]))
            elif parts:
                found |= {f"{parts[0]}.{a.name}" for a in node.names}
            else:
                found |= {a.name for a in node.names}
        elif node.level == 2 and here and parts:
            # 別のサブパッケージ。`from ..infra import fsio` / `from ..infra.modes import X`
            if len(parts) >= 2:
                found.add(".".join(parts[:2]))
            else:
                found |= {f"{parts[0]}.{a.name}" for a in node.names}
        elif not node.level and parts and parts[0] == "ccnavi":
            # 止める形（5）だが、行き先は数える。数えないと 3・4・6 から抜ける
            if len(parts) >= 3:
                found.add(".".join(parts[1:3]))
            elif len(parts) == 2:
                found |= {f"{parts[1]}.{a.name}" for a in node.names}
            else:
                found |= {a.name for a in node.names}
    elif isinstance(node, ast.Import):
        for alias in node.names:
            parts = alias.name.split(".")
            if parts[0] == "ccnavi" and len(parts) >= 3:
                found.add(".".join(parts[1:3]))
            elif parts[0] == "ccnavi" and len(parts) == 2:
                found.add(parts[1])
    return found


def imports_of(module: str, known: set[str]) -> set[str]:
    """そのモジュールが import している、同じパッケージのモジュール。

    頭のものも関数の中のものも同じに数える。行き先は 2 階層で解く。

        from . import ticket, flow          （tickets の中）-> tickets.ticket, tickets.flow
        from ..infra import fsio            -> infra.fsio
        from ..infra.modes import EXIT_OK   -> infra.modes
        from .entry import cli              （直下の __main__）-> entry.cli
        from ccnavi.infra import settings   -> infra.settings（5 で止める形）

    パッケージの中で `ccnavi.` の絶対の形を書かれても見落とさないように、相対と同じに数える。
    """
    found: set[str] = set()
    for node in ast.walk(parse(module)):
        found |= _targets(module, node)
    return found & known


def hiding_in(module: str) -> list[str]:
    """そのモジュールで使われている、行き先を import 文から隠す書き方。

    - `import ccnavi`（ドット無し）。`import ccnavi.hook.judge` と違い、行き先が
      import 文に出ない。使うときは `ccnavi.hook.judge.…` という属性の参照になる
    - `importlib` / `__import__` / `sys.modules`。行き先が文字列になる
    - 3 段以上の相対（`from ...`）。`src/ccnavi/` は 2 階層までなので、外へ出るか、行き先を
      読み違える
    - 自分の居るサブパッケージを `..` で指す形（`infra` の中の `from ..infra import fsio`）。
      同じサブパッケージは `from . import fsio` と書く。2 つの書き方が同じ行き先に並ぶのを避ける
    - `from .. import infra`（`from . import infra` を直下から書くのも同じ）。行き先が
      サブパッケージそのもので、使うモジュールが import 文に出ない
    - パッケージの中の `ccnavi.` で始まる絶対の import。相対の 2 形に揃えておかないと、
      同じ行き先に書き方が 2 つでき、読み比べにくい
    """
    found: list[str] = []
    rel = os.path.relpath(path_of(module), PACKAGE).replace(os.sep, "/")
    packages = {name for name, _ in PACKAGES}
    for node in ast.walk(parse(module)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "ccnavi":
                    found.append(f"{rel}:{node.lineno} import ccnavi")
                elif alias.name.startswith("ccnavi."):
                    found.append(f"{rel}:{node.lineno} import {alias.name}")
                elif alias.name.split(".")[0] == "importlib":
                    found.append(f"{rel}:{node.lineno} import importlib")
        elif isinstance(node, ast.ImportFrom):
            head = node.module.split(".")[0] if node.module else ""
            if not node.level and head == "importlib":
                found.append(f"{rel}:{node.lineno} from importlib import ...")
            elif not node.level and head == "ccnavi":
                found.append(f"{rel}:{node.lineno} from {node.module} import ...")
            elif node.level >= 3:
                found.append(f"{rel}:{node.lineno} from {'.' * node.level}{node.module or ''}")
            elif node.level == 2 and not node.module:
                found.append(f"{rel}:{node.lineno} from .. import ...")
            elif (
                node.level == 2 and node.module and node.module.split(".")[0] == package_of(module)
            ):
                found.append(
                    f"{rel}:{node.lineno} from ..{node.module} import ... "
                    f"（同じサブパッケージは `from . import x` と書く）"
                )
            elif (
                node.level == 1
                and not node.module
                and not package_of(module)
                and any(alias.name in packages for alias in node.names)
            ):
                found.append(f"{rel}:{node.lineno} from . import <サブパッケージ>")
        elif isinstance(node, ast.Name) and node.id == "__import__":
            found.append(f"{rel}:{node.lineno} __import__")
        elif (
            isinstance(node, ast.Attribute)
            and node.attr == "modules"
            and isinstance(node.value, ast.Name)
            and node.value.id == "sys"
        ):
            found.append(f"{rel}:{node.lineno} sys.modules")
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


def _only_a_docstring(path: str) -> bool:
    with open(path, encoding="utf-8") as f:
        body = ast.parse(f.read()).body
    return (
        len(body) == 1
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    )


class ModuleTiersTest(unittest.TestCase):
    def setUp(self):
        self.modules = modules()
        # 書き方が変わったことに気づかずに「逆流なし」と言わないため。
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
            "段の決まっていないモジュールがある。TIERS にドットでつないだ名前（`infra.fsio`）で"
            "足す。置き場は「import している先のうち一番高い段」と同じか、その 1 つ上。"
            f"段の意味は {TIER_MEANING}。どのサブパッケージに置くかは {PACKAGE_MEANING}",
        )

        phantom = sorted(set(TIER_OF) - set(self.modules))
        self.assertEqual(
            [],
            phantom,
            "TIERS が、置かれていないモジュールを挙げている。サブパッケージを移したなら、"
            "TIERS の名前（`<サブパッケージ>.<モジュール>`）も直す",
        )

    def test_the_package_has_the_listed_subpackages_only(self):
        """直下のディレクトリは PACKAGES と同じで、それより深い階層を作らない。"""
        self.assertEqual(
            sorted(name for name, _ in PACKAGES),
            directories(),
            "`src/ccnavi/` の直下のディレクトリが PACKAGES と違う。サブパッケージを足すなら "
            f"PACKAGES に役割と順を書いてから作る。いまの役割は {PACKAGE_MEANING}",
        )
        stray = sorted(mod for mod in self.modules if not package_of(mod) and mod not in TOP_LEVEL)
        self.assertEqual(
            [],
            stray,
            "`src/ccnavi/` の直下にモジュールがある。直下に置くのは `__init__` と `__main__` だけ。"
            f"役割に合うサブパッケージへ置く（{PACKAGE_MEANING}）",
        )
        deeper = sorted(
            f"{package}/{name}"
            for package in directories()
            for name in os.listdir(os.path.join(PACKAGE, package))
            if name != "__pycache__" and os.path.isdir(os.path.join(PACKAGE, package, name))
        )
        self.assertEqual(
            [],
            deeper,
            "サブパッケージの下にディレクトリがある。このテストは 2 階層までしか読まないので、"
            "中のモジュールは段を持たないまま素通りする。サブパッケージの直下に戻す",
        )

    def test_every_subpackage_has_an_init_with_only_a_docstring(self):
        """どのサブパッケージにも `__init__.py` があり、中身は役割を書いた docstring だけ。"""
        missing = [
            package
            for package in directories()
            if not os.path.isfile(os.path.join(PACKAGE, package, "__init__.py"))
        ]
        self.assertEqual([], missing, "`__init__.py` の無いサブパッケージがある")
        busy = [
            package
            for package in directories()
            if package not in missing
            and not _only_a_docstring(os.path.join(PACKAGE, package, "__init__.py"))
        ]
        self.assertEqual(
            [],
            busy,
            "サブパッケージの `__init__.py` に docstring 以外がある。再輸出しない"
            "（`from ..infra import fsio` の行き先が `__init__.py` に見え、5 と 6 が読み違える）。"
            "役割を書いた docstring だけにする",
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

    def test_imports_across_subpackages_go_downward(self):
        """サブパッケージをまたぐ import は、PACKAGES で下のサブパッケージへだけ。"""
        upward = [
            f"{mod} -> {target}"
            for mod in sorted(self.edges)
            if package_of(mod) in PACKAGE_ORDER
            for target in sorted(self.edges[mod])
            if package_of(target) in PACKAGE_ORDER
            and PACKAGE_ORDER[package_of(target)] > PACKAGE_ORDER[package_of(mod)]
        ]
        self.assertEqual(
            [],
            upward,
            "下のサブパッケージが上のサブパッケージを import している。順は "
            f"{' < '.join(name for name, _ in PACKAGES)}。呼ぶ側から材料を渡すか、"
            "共通の部分を下のサブパッケージへ出す。モジュールの役割が違っていたなら、"
            f"合うサブパッケージへ移す（{PACKAGE_MEANING}）",
        )

    def test_no_module_sits_in_a_cycle(self):
        """循環を 1 つも許さない。"""
        found = sorted(" ↔ ".join(group) for group in knots(self.edges))
        self.assertEqual(
            [],
            found,
            "import が循環している。片方向に直す（関数の中へ import を移すのは"
            "隠すだけで、ここは同じに数える）。共通の部分を下の段へ出すか、呼ぶ側から"
            "材料を渡す",
        )

    def test_no_module_hides_where_it_is_going(self):
        """行き先を import 文から隠す書き方を使わない。"""
        hidden = [spell for mod in self.modules for spell in hiding_in(mod)]
        self.assertEqual(
            [],
            hidden,
            "import の行き先が import 文に残らない書き方をしている。1 から 4 と 6 は"
            "この形を見つけられないか読み違えるので、書き方のほうを止める。パッケージの中は"
            "`from . import x`（同じサブパッケージ）と `from ..infra import fsio`（別の"
            "サブパッケージ）の 2 形で書く。自分のサブパッケージを `..` で指さず、同じ"
            "サブパッケージの中は `from . import x`。どうしても要るなら、なぜ要るかを添えてここに"
            "例外を書く",
        )


if __name__ == "__main__":
    unittest.main()
