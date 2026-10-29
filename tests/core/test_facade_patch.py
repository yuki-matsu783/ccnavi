"""ファサードが束ね直した名前への patch を、テストで止める。

`src/ccnavi/` には、別のモジュールの名前を同じ名前で束ね直す書き方（`X = mod.X`）がある。
名前を再輸出するだけの薄いファサード（`tickets/core/api.py`・`entry/diagnose.py`）と、
モジュールの一部だけを束ね直すもの（`tickets/phase/gates.py` の `load_types`、`hook/judge.py` の
`PATH_TOOLS` など）。`mock.patch.object(core, "x")` は `core` の属性を差し替えるだけで、
実体のモジュールの中の呼び出し（`core_base.x(...)`）には効かない。効いたつもりで通るテストは、
差し替えたはずの処理を実物のまま走らせる。

束ね直した名前は手で並べず、`src/ccnavi/` を `ast` で読んで集める（`_reexports`）。
モジュールの先頭の階層にある、import したモジュールの同じ名前の属性を指す代入
（`X = mod.X`）を 1 つの再輸出と数える。名前を変える別名（`DOING_DIR = ticket_model.DOING`）と
`_` で始まる名前は、そのモジュールの中で使うためのものなので数えない。

`tests/` を `ast` で走査し、次の形を見つける。許可リスト（`ALLOWED`）にない patch は失敗にする。

- `mock.patch.object(<モジュール>, "名前")`（`cli.core` のような属性参照も含む）。
  モジュールは、そのテストの import（`from ccnavi.tickets.model import ticket as ticket_mod`）か、
  `src/ccnavi/` の中で 1 つに決まるモジュール名で引く
- `mock.patch("ccnavi.<サブパッケージ>.<モジュール>.名前")`

patch は実体のモジュールに対して行う。
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from tests import ROOT, SRC

TESTS = Path(ROOT) / "tests"
PACKAGE = Path(SRC) / "ccnavi"

# (tests/ からの相対パス, patch 先の式, 名前)。許可する理由をそれぞれに書く。今は無い。
ALLOWED: set[tuple[str, str, str]] = set()


def _dotted(path: Path) -> str:
    """`src/ccnavi/` の下のモジュールの、ドットでつないだ名前（`tickets.core.api`）。"""
    return ".".join(path.relative_to(PACKAGE).with_suffix("").parts)


def _reexports(source: str) -> set[str]:
    """モジュールの先頭の階層で、import したモジュールの同じ名前を束ね直した名前。"""
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            imported.update(a.asname or a.name for a in node.names)
        elif isinstance(node, ast.Import):
            imported.update((a.asname or a.name).split(".")[0] for a in node.names)
    names: set[str] = set()
    for node in tree.body:
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
            continue
        target, value = node.targets[0], node.value
        if (
            isinstance(target, ast.Name)
            and not target.id.startswith("_")
            and isinstance(value, ast.Attribute)
            and isinstance(value.value, ast.Name)
            and value.value.id in imported
            and value.attr == target.id
        ):
            names.add(target.id)
    return names


def _facades() -> dict[str, set[str]]:
    """ファサードのモジュール（ドットでつないだ名前）と、そこが束ね直した名前。"""
    found = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        names = _reexports(path.read_text(encoding="utf-8"))
        if names:
            found[_dotted(path)] = names
    return found


def _modules() -> dict[str, list[str]]:
    """モジュールの名前（最後の 1 段）から、ドットでつないだ名前への表。"""
    table: dict[str, list[str]] = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        if "__pycache__" not in path.parts:
            table.setdefault(path.stem, []).append(_dotted(path))
    return table


def _aliases(tree: ast.AST) -> dict[str, str]:
    """テストの import が付けた名前から、ccnavi のモジュールへの表。"""
    table = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("ccnavi."):
            base = node.module.removeprefix("ccnavi.")
            for a in node.names:
                table[a.asname or a.name] = f"{base}.{a.name}"
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.asname and a.name.startswith("ccnavi."):
                    table[a.asname] = a.name.removeprefix("ccnavi.")
    return table


def _is_patch(func: ast.expr, last: str) -> bool:
    """`mock.patch.object` / `patch.object`（`last` が `object`）か、`mock.patch` / `patch` か。"""
    if last == "object":
        return (
            isinstance(func, ast.Attribute)
            and func.attr == "object"
            and _is_patch(func.value, "patch")
        )
    if isinstance(func, ast.Name):
        return func.id == "patch"
    return isinstance(func, ast.Attribute) and func.attr == "patch"


def _facade_patches(
    tree: ast.AST, facades: dict[str, set[str]], modules: dict[str, list[str]]
) -> list[tuple[str, str]]:
    """ファサードが束ね直した名前への patch。(patch 先の式, 名前) で返す。"""
    aliases = _aliases(tree)
    found: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        first = node.args[0]
        if _is_patch(node.func, "object"):
            if len(node.args) < 2:
                continue
            name = node.args[1]
            if not (isinstance(name, ast.Constant) and isinstance(name.value, str)):
                continue
            tail = first.attr if isinstance(first, ast.Attribute) else getattr(first, "id", "")
            owners = [aliases[tail]] if tail in aliases else modules.get(tail, [])
            if any(name.value in facades.get(owner, ()) for owner in owners):
                found.append((ast.unparse(first), name.value))
        elif _is_patch(node.func, "patch"):
            if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
                continue
            owner, _, rest = first.value.rpartition(".")
            if owner.startswith("ccnavi.") and rest in facades.get(
                owner.removeprefix("ccnavi."), ()
            ):
                found.append((owner, rest))
    return found


class FacadePatchTest(unittest.TestCase):
    def test_no_patch_on_a_facade_name_outside_the_allowed_list(self):
        facades, modules = _facades(), _modules()
        unknown = []
        seen = set()
        for path in sorted(TESTS.rglob("*.py")):
            rel = path.relative_to(TESTS).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for target, name in _facade_patches(tree, facades, modules):
                seen.add((rel, target, name))
                if (rel, target, name) not in ALLOWED:
                    unknown.append(f"tests/{rel}: patch {target}.{name}")
        self.assertEqual(
            [],
            unknown,
            "ファサードが束ね直した名前への patch は、実体のモジュール（core_approve など）の中の"
            "呼び出しに効かない。実体のモジュールに対して patch する",
        )
        self.assertEqual(set(), ALLOWED - seen, "許可リストに、もう無い patch が残っている")

    def test_the_collection_finds_the_known_facades(self):
        """集め方が壊れて何も見つけなくなったら、上の検査は黙って通る。知っている形で確かめる。"""
        facades = _facades()
        self.assertLessEqual({"approve", "judge_approval", "confirm"}, facades["tickets.core.api"])
        self.assertLessEqual({"explain", "test", "board"}, facades["entry.diagnose"])
        self.assertLessEqual(
            {"types_path", "layer_types", "load_types"}, facades["tickets.phase.gates"]
        )
        self.assertIn("PATH_TOOLS", facades["hook.judge"])
        self.assertNotIn("DOING_DIR", facades.get("tickets.approval.store", set()))

    def test_the_scan_finds_each_form(self):
        facades = {
            "tickets.core.api": {"a", "c"},
            "entry.diagnose": {"b", "d"},
            "tickets.phase.gates": {"g", "h"},
        }
        modules = {
            "core": ["tickets.core.api"],
            "core_base": ["tickets.core.base"],
            "diagnose": ["entry.diagnose"],
            "phase": ["tickets.phase.gates"],
        }
        tree = ast.parse(
            "from ccnavi.tickets.phase import gates as phase_mod\n"
            "mock.patch.object(core, 'a')\n"
            "mock.patch.object(cli.diagnose, 'b')\n"
            "mock.patch('ccnavi.tickets.core.api.c')\n"
            "mock.patch('ccnavi.entry.diagnose.d')\n"
            "mock.patch('ccnavi.tickets.core.base.e')\n"
            "mock.patch.object(core_base, 'f')\n"
            "mock.patch.object(phase_mod, 'g')\n"
            "mock.patch('ccnavi.tickets.phase.gates.h')\n"
            "mock.patch.object(phase, 'own')\n"
            "mock.patch('ccnavi.tickets.phase.gates.own')\n"
        )
        self.assertEqual(
            [
                ("core", "a"),
                ("cli.diagnose", "b"),
                ("ccnavi.tickets.core.api", "c"),
                ("ccnavi.entry.diagnose", "d"),
                ("phase_mod", "g"),
                ("ccnavi.tickets.phase.gates", "h"),
            ],
            sorted(_facade_patches(tree, facades, modules), key=lambda p: p[1]),
        )

    def test_the_collection_counts_only_same_name_rebinding(self):
        source = (
            "from . import ticket_model, phasetypes\n"
            "load_types = phasetypes.load_types\n"
            "DOING_DIR = ticket_model.DOING\n"
            "_FENCE = ticket_model.FENCE\n"
            "def f():\n"
            "    inner = phasetypes.inner\n"
            "local = unknown.local\n"
        )
        self.assertEqual({"load_types"}, _reexports(source))


if __name__ == "__main__":
    unittest.main()
