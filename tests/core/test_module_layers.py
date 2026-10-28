"""`src/ccnavi/` の import の向きを、テストで守る。

`src/ccnavi/` は役割ごとのサブパッケージに分かれ、大きいものはさらに分かれる。
読む順は 2 つの表で書く。サブパッケージの順（`PACKAGES`。入れ子を持てる木）と、
モジュールの段（`TIERS`）。ディレクトリが言えるのは「どの役割か」までで、同じ
サブパッケージの中の読む順は言えない。段がそれを補う。2 つの表と食い違う import を名指しする。

「レイヤー」ではなく「段」と呼ぶのは、このリポジトリではレイヤーが設定の 3 つのレイヤー
（共通レイヤー・自身のレイヤー・プロジェクトのレイヤー）を指すため。ここで言う段は
モジュールを読む順で、別のもの。

読むのは `src/ccnavi/` の下の `.py` の import 文だけ。実行ファイルは起動しないので速い。
モジュールはドットでつないだ名前で呼ぶ（`infra.fsio`、`tickets.review.close`）。
直下の `__init__` と `__main__` はそのままの名前。

関数の中の import も数える。 循環は、先頭の import を関数の中へ移すと
消えたように見える。`ast.walk` ですべて拾うので、移しても隠れない。

見るのは 6 つ。

1. どのモジュールも段をちょうど 1 つ持つ。モジュールを足したら、どの段かを決めさせる。
   `TIERS` の名前は、ドットでつないだ全体の名前で引く
2. 置き場の形。ディレクトリは `PACKAGES` に書いたものだけで、深さは
   `ccnavi/<サブパッケージ>/<サブパッケージ>/` まで。どのサブパッケージにも `__init__.py` があり、
   中身は役割を書いた docstring だけ（再輸出しない。`from ..infra import fsio` の行き先が
   `infra/__init__.py` にならないように）。`SPLIT` に挙げたパッケージの直下には
   `__init__.py` 以外を置かない
3. import の行き先は、同じ段か下の段。上を向いた import を名指しする
4. 循環を 1 つも許さない。既知として通す一覧も持たない
5. import の行き先が、import 文に残らない書き方をしていない
6. 同じ親の下の兄弟は、`PACKAGES` で段が下のものだけを読む。どの深さにも同じ関数でかける

同じ段どうしのモジュールの import は止めない。段は「どちらを先に読めるか」の順序であって、
同じ段の中の結び付きは 4 が見る。同じ段どうしの問題（たとえば判定がチケットを
動かし始める形）は、段では止まらない。 サブパッケージをまたぐなら 6 が見る。

6 の読み方。2 つのモジュールの共通の親を見つけ、その直下でそれぞれが入っている枝を比べる。
枝が兄弟のサブパッケージどうしなら、読む側の段より読まれる側の段が小さいことを求める
（同じ段の兄弟も互いに読めない）。親の直下にあるモジュールは、その親の子のサブパッケージを
読める。子のサブパッケージは、親の直下のモジュールを読めない。

5 が要るのは、1 から 4 と 6 が import 文しか読まないから。
`importlib.import_module("ccnavi.hook.judge")` と、ドットの無い `import ccnavi` に
続く `ccnavi.hook.judge.…` は、行き先が import 文に残らないので 1 つも見つからない。
どちらも `src/ccnavi/` では 1 度も使っていないので、書き方ごと止めるほうが安い。
同じ理由で、パッケージの中の書き方を相対に限り、点の数を共通の親まで上がる最短の数にする。
同じサブパッケージは `from . import x`、同じ親の兄弟は `from ..model import ticket`、
別の大きなサブパッケージは `from ...infra import fsio`（`from ..infra.modes import EXIT_OK`
も可）。余分に重ねた点、`from .. import model`（行き先がサブパッケージそのもので、
モジュールが import 文に出ない）、パッケージの中の `ccnavi.` で始まる絶対の import は止める。
回避できないわけではない。 `getattr` や `exec` で組み立てれば、いまでも
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

# サブパッケージの順を書く木。1 行は (名前, 段, 役割, 子)。
#
# 同じ親の下の兄弟は、段が小さいサブパッケージだけを読める。段が同じ兄弟は互いに読めない。
# 段（TIERS）と違い、こちらは役割で引いた線。依存の向きがこの順に収まることを
# 6 の検査で確かめる。モジュールがどのサブパッケージに居るかはディレクトリで決まり、
# ここには書かない。サブパッケージを足す・並べ替えるときは、この表を先に直す。
# 深さの上限は MAX_DEPTH。
Node = tuple[str, int, str, tuple]

PACKAGES: tuple[Node, ...] = (
    (
        "infra",
        0,
        "土台。ファイル・git・パス照合・hook の入出力・設定・シェルの読み・ワークツリー",
        (),
    ),
    ("records", 1, "記録。伏せ字・判定の記録・診断ログ・後始末・拒否の数え", ()),
    ("policy", 2, "ルール。読み込み・照合・組み込み・レイヤーの合成・自己防衛・文脈ファイル", ()),
    (
        "tickets",
        3,
        "チケット。承認済みチケットの置き場（approval）と合意の手続き（agree）、"
        "承認・レビュー済みの判定のコア（core）、write-and-push の見分け、フェーズ、操作",
        (
            (
                "model",
                0,
                "型と名前。チケット・フェーズ・提案の形、記録の置き場の名前、承認の記録の見分け。ほかのサブパッケージすべてから読まれる。新しい機能は上のサブパッケージに足す",
                (),
            ),
            (
                "config",
                1,
                "設定の読み取り。フェーズの型・ワークフロー・リスクの配点・設定の検査",
                (),
            ),
            ("flow", 1, "フローの読み取りと描画。フロー・描画・形・文面", ()),
            (
                "approval",
                2,
                "承認済みチケットの置き場。記録の読み書き・検査・印・時刻・write-and-push の見分け",
                (),
            ),
        ),
    ),
    ("catalog", 4, "目録。文書の索引と --docs の引き方・プロジェクトのスキルの目録", ()),
    ("hook", 5, "hook の判定と、その文面・実行後の監視・イベント", ()),
    ("entry", 6, "入口。CLI・診断・lint・提案・版", ()),
)

# 深さの上限。`ccnavi/<サブパッケージ>/<サブパッケージ>/` まで。
MAX_DEPTH = 2
# 直下にモジュールを置かず、サブパッケージだけにしたパッケージ（`__init__.py` は除く）。
SPLIT: frozenset[str] = frozenset()

# 段は下から上へ。下の段は上の段を知らない。
#
# **いまの依存の深さをなぞったもので、意味で先に引いた線ではない。** 設計書の章立て
# （ルール・実行前・実行後・チケット）で切ると双方向の辺が残って段にならないので、
# 深さで切ってある。注記はその段に何が居るかの説明であって、そこへ置く根拠ではない。
# 役割の線はサブパッケージ（PACKAGES）が引く。
#
# 段をまたいで移すなら、この表を先に直してから動かす。直さずに通ったなら、
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
                "infra.pathcase",
                "infra.pathsafe",
                "infra.platformtag",
                "infra.settings",
                "infra.shellread_scan",
                "infra.shellread_words",
                "infra.tree",
                "infra.yamlread",
                "records.redact",
                "records.wiki",
                "records.wiki_load",
                "tickets.model.user_only",
                "tickets.model.oplog",
                "hook.subagent_outcome",
                "tickets.model.direct_candidates",
                "tickets.model.handoff_names",
            }
        ),
    ),
    (
        "read",
        "最下段だけを読む。git の状態・ルール・動作モード・記録の 1 行と後始末・拒否の数え",
        frozenset(
            {
                "hook.agentkind",
                "hook.failsafe",
                "hook.turngate",
                "infra.directrec",
                "infra.gitstate",
                "infra.shellread",
                "infra.shellread_cd",
                "infra.modes",
                "policy.rules",
                "policy.texts",
                "records.audit",
                "records.busy",
                "records.diaglog",
                "records.failstreak",
                "records.idlehandoff",
                "records.prune",
                "records.repeat",
                "records.stopeval",
                "records.stopops",
                "records.stopreview",
                "records.turn",
                "records.userprompt",
            }
        ),
    ),
    (
        "state",
        "ワークツリーとチケットの、いまの形を読む",
        frozenset(
            {
                "hook.parentwrite",
                "hook.rootwrite",
                "hook.wrapguard",
                "policy.builtin",
                "policy.ctxfile",
                "policy.selfguard",
                "policy.selfguard_targets",
                "policy.selfguard_shell",
                "tickets.model.archive",
                "tickets.model.donecopy",
                "tickets.flow.graph",
                "tickets.flow.text",
                "tickets.flow.shape",
                "tickets.flow.render",
                "tickets.model.via",
                "tickets.model.review_host",
                "tickets.config.risk",
                "tickets.model.standing",
                "tickets.model.ticket",
                "tickets.model.ticket_fields",
                "tickets.model.ticket_fold",
                "tickets.model.ticket_guard",
                "tickets.model.ticket_ids",
                "tickets.model.ticket_model",
                "tickets.model.proposal_form",
                "tickets.model.ticket_places",
            }
        ),
    ),
    (
        "compose",
        "レイヤーごとの設定を読んで、判定の材料に組む",
        frozenset(
            {
                "hook.ccnavimd",
                "catalog.docsearch",
                "catalog.docsearch_index",
                "catalog.docsearch_query",
                "catalog.projskills",
                "hook.proposalhint",
                "policy.ruleload",
                "policy.rule_lint",
                "policy.subjects",
                "tickets.config.check",
                "tickets.config.phasetypes",
                "tickets.config.workflow",
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
                "tickets.agree_candidates",
                "tickets.agree_screen",
                "tickets.agree_digest",
                "tickets.approval.store",
                "tickets.approval.checks",
                "tickets.approval.marks",
                "tickets.approval.ops",
                "tickets.approval.times",
                "tickets.branchfind",
                "tickets.configsync",
                "tickets.overview",
                "tickets.skillsync",
                "tickets.phase",
                "tickets.manual_settle",
                "tickets.model.phase_forms",
                "tickets.phase_scope",
                "tickets.plan_order",
                "tickets.plan_phases",
                "tickets.plan_templates",
                "tickets.scaffold",
                "tickets.session_rejects",
                "tickets.template_drafts",
                "tickets.handoff",
                "tickets.mirrorsync",
                "tickets.promptsync",
                "tickets.learning",
                "tickets.learning_records",
                "tickets.template_use",
            }
        ),
    ),
    (
        "decide",
        "判定と、チケット・レビューを動かす操作",
        frozenset(
            {
                "tickets.approval.write_and_push",
                "tickets.write_and_push_merge",
                "tickets.mirror_gap",
                "tickets.core",
                "tickets.core_approve",
                "tickets.core_base",
                "tickets.core_review",
                "hook.judge",
                "hook.agentlaunch",
                "hook.childnote",
                "tickets.next_id",
                "tickets.propose",
                "tickets.consolidate",
                "tickets.learning_notice",
                "tickets.ops",
                "tickets.ops_close",
                "tickets.ops_plan",
                "tickets.ops_stop",
                "tickets.ops_stop_integrate",
                "tickets.base_bound",
                "tickets.ops_stop_request",
                "tickets.ops_stop_feedback",
                "tickets.pending_integrate",
                "hook.post",
                "hook.post_findings",
                "hook.post_report",
                "tickets.retire",
                "tickets.review",
                "tickets.review_close",
                "tickets.review_decide",
                "tickets.worktrees",
            }
        ),
    ),
    (
        "entry",
        "入口と診断。下の段からは読まれない",
        frozenset(
            {
                "__main__",
                "entry.cli",
                "entry.cli_usage",
                "entry.cli_args",
                "entry.cli_ops",
                "entry.cli_learnings",
                "entry.lint_wiki",
                "entry.diagnose",
                "entry.diagnose_board",
                "entry.diagnose_explain",
                "entry.diagnose_shared",
                "entry.diagnose_try",
                "entry.lint",
                "entry.lint_branch",
                "entry.lint_ticket",
                "entry.lint_layers",
                "entry.lint_direct",
                "entry.lint_places",
                "entry.lint_project",
                "entry.cli_plan",
                "entry.cli_templates",
                "entry.cli_handoff",
                "entry.cli_idle",
                "entry.cli_mirror",
                "entry.cli_stopreview",
                "entry.lint_templates",
                "entry.lint_texts",
                "entry.sh_ask",
                "entry.cli_text",
                "entry.status",
                "entry.suggest",
                "entry.version",
                "entry.ext",
                "hook.events",
                "hook.stopjudge",
                "hook.subagent",
            }
        ),
    ),
)

TIER_ORDER = {name: i for i, (name, _, _) in enumerate(TIERS)}
TIER_OF = {mod: name for name, _, mods in TIERS for mod in mods}
TIER_MEANING = " / ".join(f"{name}: {note}" for name, note, _ in TIERS)
# 直下に置いてよいモジュール。パッケージの目印と `python -m ccnavi` の入口だけ。
TOP_LEVEL = frozenset({"__init__", "__main__"})


def package_paths(tree: tuple[Node, ...], prefix: str = "") -> dict[str, int]:
    """木に書いたサブパッケージの、ドットでつないだ名前 → 同じ親の下での段。"""
    found: dict[str, int] = {}
    for name, rank, _, children in tree:
        path = f"{prefix}{name}"
        found[path] = rank
        found.update(package_paths(children, f"{path}."))
    return found


def meaning_of(tree: tuple[Node, ...], prefix: str = "") -> str:
    """木の読み方の説明。エラーの文面に添える。"""
    parts = []
    for name, _, note, children in tree:
        parts.append(f"{prefix}{name}: {note}")
        if children:
            parts.append(meaning_of(children, f"{prefix}{name}."))
    return " / ".join(parts)


PACKAGE_RANK = package_paths(PACKAGES)
PACKAGE_MEANING = meaning_of(PACKAGES)


def package_of(module: str) -> str:
    """モジュールの居るサブパッケージ（ドットでつないだ名前）。直下のモジュールなら空。"""
    return module.rpartition(".")[0]


def path_of(module: str) -> str:
    return os.path.join(PACKAGE, *module.split(".")) + ".py"


def directories(root: str = PACKAGE) -> list[str]:
    """`src/ccnavi/` の下のディレクトリを、ドットでつないだ名前で（`__pycache__` を除く）。"""
    found: list[str] = []
    for current, names, _ in os.walk(root):
        names[:] = sorted(n for n in names if n != "__pycache__")
        for name in names:
            rel = os.path.relpath(os.path.join(current, name), root)
            found.append(rel.replace(os.sep, "."))
    return sorted(found)


def modules(root: str = PACKAGE) -> list[str]:
    """`src/ccnavi/` に置いてあるモジュールの、ドットでつないだ名前。

    どの深さの `.py` も数える。サブパッケージの `__init__.py` は数えない
    （中身が docstring だけであることを 2 で見る）。直下の `__init__` は数える。
    """
    found: list[str] = []
    for current, names, files in os.walk(root):
        names[:] = [n for n in names if n != "__pycache__"]
        rel = os.path.relpath(current, root)
        prefix = "" if rel == "." else rel.replace(os.sep, ".") + "."
        for f in files:
            if not f.endswith(".py"):
                continue
            if f == "__init__.py" and prefix:
                continue
            found.append(prefix + f[:-3])
    return sorted(found)


def parse(module: str) -> ast.Module:
    with open(path_of(module), encoding="utf-8") as f:
        return ast.parse(f.read())


def _resolve(parts: list[str], names: list[str], known: set[str], packages: set[str]) -> set[str]:
    """`from <parts> import <names>` の行き先のモジュール（ドットでつないだ名前）。

    `parts` がモジュールならそれ、サブパッケージなら `parts.<name>` のうちモジュールのもの。
    サブパッケージそのものや、見つからない名前は含めない（サブパッケージそのものは 5 が止める）。
    """
    full = ".".join(parts)
    if full in known:
        return {full}
    found: set[str] = set()
    if full in packages or not parts:
        for name in names:
            candidate = f"{full}.{name}" if full else name
            if candidate in known:
                found.add(candidate)
    return found


def _base(module: str, level: int) -> list[str] | None:
    """相対 import の起点（点の数 `level` だけ上がったパッケージ）。root を越えたら None。"""
    here = package_of(module).split(".") if package_of(module) else []
    up = level - 1
    if up > len(here):
        return None
    return here[: len(here) - up]


def _targets(
    module: str, node: ast.AST, known: set[str] | None = None, packages: set[str] | None = None
) -> set[str]:
    """1 つの import 文の行き先（ドットでつないだモジュール名）。行き先が言えない形は空。"""
    known = known if known is not None else set(modules())
    packages = packages if packages is not None else set(directories())
    found: set[str] = set()
    if isinstance(node, ast.ImportFrom):
        parts = node.module.split(".") if node.module else []
        names = [a.name for a in node.names]
        if node.level:
            base = _base(module, node.level)
            if base is None:
                return found
            found |= _resolve(base + parts, names, known, packages)
        elif parts and parts[0] == "ccnavi":
            # 止める形（5）だが、行き先は数える。数えないと 3・4・6 から抜ける
            found |= _resolve(parts[1:], names, known, packages)
    elif isinstance(node, ast.Import):
        for alias in node.names:
            parts = alias.name.split(".")
            if parts[0] == "ccnavi" and len(parts) >= 2:
                found |= _resolve(parts[1:], [], known, packages)
    return found


def imports_of(
    module: str,
    known: set[str],
    packages: set[str] | None = None,
    tree: ast.Module | None = None,
) -> set[str]:
    """そのモジュールが import している、同じパッケージのモジュール。

    頭のものも関数の中のものも同じに数える。行き先はどの深さでも解く。

        from . import ticket, flow          （tickets.review の中）-> tickets.review.ticket, ...
        from ..model import ticket          （tickets.review の中）-> tickets.model.ticket
        from ...infra.modes import EXIT_OK  （tickets.review の中）-> infra.modes
        from .entry import cli              （直下の __main__）-> entry.cli
        from ccnavi.infra import settings   -> infra.settings（5 で止める形）

    パッケージの中で `ccnavi.` の絶対の形を書かれても見落とさないように、相対と同じに数える。
    """
    packages = packages if packages is not None else set(directories())
    found: set[str] = set()
    for node in ast.walk(tree if tree is not None else parse(module)):
        found |= _targets(module, node, known, packages)
    return found


def shortest_level(module: str, target_parts: list[str], packages: set[str]) -> int:
    """`module` から `target_parts`（モジュールかサブパッケージ）への、最短の相対の点の数。

    共通の親まで上がる数。同じサブパッケージの中なら 1。
    """
    here = package_of(module).split(".") if package_of(module) else []
    full = ".".join(target_parts)
    # 行き先のモジュールが居るサブパッケージ。行き先がサブパッケージそのものならそれ自身
    there = target_parts if full in packages else target_parts[:-1]
    common = 0
    for a, b in zip(here, there, strict=False):
        if a != b:
            break
        common += 1
    return len(here) - common + 1


def hiding_in_tree(module: str, tree: ast.Module, known: set[str], packages: set[str]) -> list[str]:
    """そのモジュールで使われている、行き先を import 文から隠す書き方。

    - `import ccnavi`（ドット無し）。`import ccnavi.hook.judge` と違い、行き先が
      import 文に出ない。使うときは `ccnavi.hook.judge.…` という属性の参照になる
    - `importlib` / `__import__` / `sys.modules`。行き先が文字列になる
    - 相対の点の数が、共通の親まで上がる最短の数でない形。余分に重ねた形は行き先を
      読み違える。同じサブパッケージを `..` で指す形も、同じ行き先に書き方が 2 つできる
    - `from .. import model`（`from . import approval` を直下から書くのも同じ）。行き先が
      サブパッケージそのもので、使うモジュールが import 文に出ない
    - パッケージの中の `ccnavi.` で始まる絶対の import。相対に揃えておかないと、
      同じ行き先に書き方が 2 つでき、読み比べにくい
    """
    found: list[str] = []
    rel = module.replace(".", "/") + ".py"
    for node in ast.walk(tree):
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
            elif node.level:
                found += _relative_problems(module, node, rel, known, packages)
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


def _relative_problems(
    module: str, node: ast.ImportFrom, rel: str, known: set[str], packages: set[str]
) -> list[str]:
    spelled = f"from {'.' * node.level}{node.module or ''} import ..."
    base = _base(module, node.level)
    if base is None:
        return [f"{rel}:{node.lineno} {spelled} （ccnavi の外へ出る）"]
    parts = base + (node.module.split(".") if node.module else [])
    full = ".".join(parts)
    problems: list[str] = []
    if full in packages:
        # `from .. import model`：名前がサブパッケージそのものなら、モジュールが import 文に出ない
        for alias in node.names:
            if f"{full}.{alias.name}" in packages:
                problems.append(
                    f"{rel}:{node.lineno} {spelled} （行き先がサブパッケージ `{alias.name}` "
                    f"そのもの。中のモジュールを import する）"
                )
        reached = [a.name for a in node.names if f"{full}.{a.name}" in known]
        target = parts + reached[:1] if reached else parts
    elif full in known:
        target = parts
    else:
        return [f"{rel}:{node.lineno} {spelled} （行き先が見つからない）"]
    # 点の数は、1 つの文の中で行き先が揃っているので、先頭の行き先で測る
    want = shortest_level(module, target, packages)
    if node.level != want:
        problems.append(
            f"{rel}:{node.lineno} {spelled} （点は {want} つ。共通の親まで上がる最短の数にする）"
        )
    return problems


def hiding_in(module: str) -> list[str]:
    return hiding_in_tree(module, parse(module), set(modules()), set(directories()))


def graph() -> dict[str, set[str]]:
    known = set(modules())
    packages = set(directories())
    return {mod: imports_of(mod, known, packages) for mod in sorted(known)}


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


def order_violations(edges: dict[str, set[str]], tree: tuple[Node, ...]) -> list[str]:
    """同じ親の下の兄弟が、段の同じか上の兄弟を読んでいる import。どの深さにもかける。

    2 つのモジュールの共通の親の直下で、それぞれが入っている枝を比べる。枝がどちらも木に
    書いたサブパッケージなら、読まれる側の段が読む側より小さいことを求める。読む側が親の
    直下のモジュールなら、子のサブパッケージを読める。読まれる側が親の直下のモジュールなら、
    子のサブパッケージからは読めない。
    """
    rank = package_paths(tree)
    found: list[str] = []
    for mod in sorted(edges):
        for target in sorted(edges[mod]):
            a, b = mod.split("."), target.split(".")
            depth = 0
            while depth < min(len(a), len(b)) - 1 and a[depth] == b[depth]:
                depth += 1
            parent = ".".join(a[:depth])
            mine = ".".join(a[: depth + 1])
            theirs = ".".join(b[: depth + 1])
            if mine == theirs:
                continue
            mine_is_package = depth + 1 < len(a)
            theirs_is_package = depth + 1 < len(b)
            if mine_is_package and not theirs_is_package:
                found.append(
                    f"{mod} -> {target}（{parent or 'ccnavi'} の直下のモジュールを読んでいる）"
                )
            elif (
                mine_is_package
                and theirs_is_package
                and mine in rank
                and theirs in rank
                and rank[theirs] >= rank[mine]
            ):
                found.append(f"{mod} -> {target}")
    return found


def unlisted_directories(found: list[str], tree: tuple[Node, ...]) -> tuple[list[str], list[str]]:
    """(木に無いディレクトリ, 木にあるのに無いディレクトリ)。"""
    listed = set(package_paths(tree))
    return sorted(set(found) - listed), sorted(listed - set(found))


def too_deep(found: list[str], limit: int = MAX_DEPTH) -> list[str]:
    """深さが上限を超えているディレクトリ。`infra` が深さ 1、`tickets.review` が 2。"""
    return sorted(d for d in found if len(d.split(".")) > limit)


def loose_modules(mods: list[str], split: frozenset[str]) -> list[str]:
    """`split` のパッケージの直下にあるモジュール。"""
    return sorted(m for m in mods if package_of(m) in split)


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
            "段の決まっていないモジュールがある。TIERS にドットでつないだ全体の名前"
            "（`infra.fsio`、`tickets.review.close`）で足す。置き場は「import している先の"
            "うち一番高い段」と同じか、その 1 つ上。"
            f"段の意味は {TIER_MEANING}。どのサブパッケージに置くかは {PACKAGE_MEANING}",
        )

        phantom = sorted(set(TIER_OF) - set(self.modules))
        self.assertEqual(
            [],
            phantom,
            "TIERS が、置かれていないモジュールを挙げている。モジュールを移したなら、"
            "TIERS の名前（ドットでつないだ全体の名前）も直す",
        )

    def test_the_package_has_the_listed_subpackages_only(self):
        """ディレクトリは PACKAGES と同じで、深さは上限まで。"""
        found = directories()
        extra, absent = unlisted_directories(found, PACKAGES)
        self.assertEqual(
            ([], []),
            (extra, absent),
            "ディレクトリが PACKAGES と違う（左: 表に無いディレクトリ、右: 表にあって無い"
            "ディレクトリ）。サブパッケージを足すなら PACKAGES に役割と順を書いてから作る。"
            f"いまの役割は {PACKAGE_MEANING}",
        )
        self.assertEqual(
            [],
            too_deep(found),
            f"ディレクトリが深すぎる。深さは `ccnavi/<サブパッケージ>/<サブパッケージ>/` "
            f"まで（{MAX_DEPTH}）。サブパッケージの下に更に作らず、サブパッケージの直下に戻す",
        )
        self.assertEqual([], too_deep(list(package_paths(PACKAGES))), "PACKAGES が深すぎる")
        stray = sorted(mod for mod in self.modules if not package_of(mod) and mod not in TOP_LEVEL)
        self.assertEqual(
            [],
            stray,
            "`src/ccnavi/` の直下にモジュールがある。直下に置くのは `__init__` と `__main__` だけ。"
            f"役割に合うサブパッケージへ置く（{PACKAGE_MEANING}）",
        )
        self.assertEqual(
            [],
            loose_modules(self.modules, SPLIT),
            "サブパッケージに分けたパッケージの直下にモジュールがある（SPLIT）。"
            "新しいファイルは必ずどれかのサブパッケージに入れる。合うものが無ければ、"
            "新しいサブパッケージを作って PACKAGES に 1 行足す",
        )

    def test_every_subpackage_has_an_init_with_only_a_docstring(self):
        """どのサブパッケージにも `__init__.py` があり、中身は役割を書いた docstring だけ。"""
        dirs = directories()
        missing = [
            package
            for package in dirs
            if not os.path.isfile(os.path.join(PACKAGE, *package.split("."), "__init__.py"))
        ]
        self.assertEqual([], missing, "`__init__.py` の無いサブパッケージがある")
        busy = [
            package
            for package in dirs
            if package not in missing
            and not _only_a_docstring(os.path.join(PACKAGE, *package.split("."), "__init__.py"))
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

    def test_imports_between_siblings_go_downward(self):
        """同じ親の下の兄弟は、PACKAGES で段が下のサブパッケージだけを読む。"""
        self.assertEqual(
            [],
            order_violations(self.edges, PACKAGES),
            "サブパッケージが、段の同じか上のサブパッケージを import している。順は "
            f"{PACKAGE_MEANING}。呼ぶ側から材料を渡すか、共通の部分を下のサブパッケージへ"
            "出す。モジュールの役割が違っていたなら、合うサブパッケージへ移す",
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
            "相対で、点の数は共通の親まで上がる最短の数にする（同じサブパッケージは"
            "`from . import x`、兄弟は `from ..model import ticket`、別の大きなサブパッケージは"
            "`from ...infra import fsio`）。サブパッケージそのものは import せず、中の"
            "モジュールを import する。どうしても要るなら、なぜ要るかを添えてここに例外を書く",
        )


# --- 検査そのものを確かめる。わざと違反する入力で、止まることを見る。 ---
#
# 本物の木（PACKAGES）はいまの形しか持たないので、入れ子・最短の点の数・深さの上限は、
# 小さな作り物の表で確かめる。ここが落ちたら、検査が働かなくなっている。

FAKE: tuple[Node, ...] = (
    ("base", 0, "土台", ()),
    (
        "big",
        1,
        "大きい",
        (
            ("low", 0, "下", ()),
            ("mid_a", 1, "中", ()),
            ("mid_b", 1, "中（mid_a と同じ段）", ()),
            ("top", 2, "上", (("deep", 0, "さらに下", ()),)),
        ),
    ),
)
FAKE_MODULES = {
    "__main__",
    "base.x",
    "big.root_mod",
    "big.low.x",
    "big.mid_a.x",
    "big.mid_b.x",
    "big.top.x",
    "big.top.deep.x",
}


class OrderTableTest(unittest.TestCase):
    """入れ子の表と、深さに依らない順の検査。"""

    def test_nested_table_is_read_at_every_depth(self):
        self.assertEqual(
            {"base", "big", "big.low", "big.mid_a", "big.mid_b", "big.top", "big.top.deep"},
            set(package_paths(FAKE)),
        )

    def test_a_sibling_may_read_a_lower_sibling(self):
        edges = {"big.top.x": {"big.low.x", "big.mid_a.x"}, "big.mid_a.x": {"big.low.x"}}
        self.assertEqual([], order_violations(edges, FAKE))

    def test_a_sibling_reading_a_higher_sibling_is_caught(self):
        edges = {"big.low.x": {"big.top.x"}}
        self.assertEqual(["big.low.x -> big.top.x"], order_violations(edges, FAKE))

    def test_siblings_on_the_same_tier_do_not_read_each_other(self):
        edges = {"big.mid_a.x": {"big.mid_b.x"}}
        self.assertEqual(["big.mid_a.x -> big.mid_b.x"], order_violations(edges, FAKE))

    def test_the_order_is_checked_one_level_down_too(self):
        edges = {"big.top.deep.x": {"big.top.x"}}
        # 子から親の直下の兄弟モジュールではなく、同じ親の中の順（deep は top の子）
        self.assertEqual([], order_violations({"big.top.x": {"big.top.deep.x"}}, FAKE))
        self.assertEqual(
            ["big.top.deep.x -> big.top.x（big.top の直下のモジュールを読んでいる）"],
            order_violations(edges, FAKE),
        )

    def test_a_deep_module_reading_across_the_top_is_judged_at_the_common_parent(self):
        # base と big は兄弟（段 0 と 1）。深い所から base へ下りるのは良く、逆は悪い
        self.assertEqual([], order_violations({"big.top.deep.x": {"base.x"}}, FAKE))
        self.assertEqual(
            ["base.x -> big.top.deep.x"], order_violations({"base.x": {"big.top.deep.x"}}, FAKE)
        )

    def test_a_module_directly_under_a_parent_may_read_its_children_only_that_way(self):
        self.assertEqual([], order_violations({"big.root_mod": {"big.top.x"}}, FAKE))
        self.assertEqual(
            ["big.top.x -> big.root_mod（big の直下のモジュールを読んでいる）"],
            order_violations({"big.top.x": {"big.root_mod"}}, FAKE),
        )

    def test_a_sibling_missing_from_the_table_is_not_silently_passed_for_edges_inside_it(self):
        # 同じサブパッケージの中の import は、順の検査の対象外（段が見る）
        self.assertEqual([], order_violations({"big.low.x": {"big.low.x"}}, FAKE))


class ShapeTest(unittest.TestCase):
    """置き場の形：表に無いディレクトリ・深さの上限・直下のモジュール。"""

    def test_a_directory_missing_from_the_table_is_named(self):
        extra, absent = unlisted_directories(["base", "big", "big.low", "stray"], FAKE)
        self.assertEqual(["stray"], extra)
        self.assertIn("big.top", absent)

    def test_a_directory_listed_in_the_table_but_absent_is_named(self):
        _, absent = unlisted_directories(["base"], FAKE)
        self.assertIn("big", absent)

    def test_depth_beyond_the_limit_is_caught(self):
        self.assertEqual(["big.top.deep"], too_deep(list(package_paths(FAKE)), 2))
        self.assertEqual([], too_deep(["infra", "alpha.beta"], 2))

    def test_the_real_table_stays_within_the_limit(self):
        self.assertEqual([], too_deep(list(package_paths(PACKAGES))))

    def test_a_loose_module_in_a_split_package_is_caught(self):
        mods = ["alpha.loose", "alpha.beta.thing", "gamma.thing"]
        self.assertEqual(["alpha.loose"], loose_modules(mods, frozenset({"alpha"})))
        self.assertEqual([], loose_modules(mods, frozenset()))


class RelativeImportTest(unittest.TestCase):
    """相対 import の点の数と、行き先がサブパッケージそのものの形。"""

    PACKAGES = set(package_paths(FAKE))

    def problems(self, module: str, source: str) -> list[str]:
        return hiding_in_tree(module, ast.parse(source), set(FAKE_MODULES), self.PACKAGES)

    def targets(self, module: str, source: str) -> set[str]:
        return imports_of(module, set(FAKE_MODULES), self.PACKAGES, ast.parse(source))

    def test_the_shortest_spelling_passes_at_every_depth(self):
        self.assertEqual([], self.problems("big.top.x", "from . import x\n"))
        self.assertEqual([], self.problems("big.top.x", "from .deep import x\n"))
        self.assertEqual([], self.problems("big.top.x", "from ..low import x\n"))
        self.assertEqual([], self.problems("big.top.deep.x", "from ...low import x\n"))
        self.assertEqual([], self.problems("big.top.deep.x", "from ....base import x\n"))
        self.assertEqual([], self.problems("big.top.x", "from ...base import x\n"))
        self.assertEqual([], self.problems("big.low.x", "from . import x\n"))

    def test_one_dot_too_many_is_caught(self):
        self.assertTrue(self.problems("big.top.x", "from ...low import x\n"))
        self.assertTrue(self.problems("big.top.deep.x", "from ....low import x\n"))
        self.assertTrue(self.problems("base.x", "from ..base import x\n"))

    def test_naming_your_own_subpackage_with_dots_is_caught(self):
        self.assertTrue(self.problems("big.low.x", "from ..low import x\n"))

    def test_too_few_dots_for_a_sibling_is_caught(self):
        self.assertTrue(self.problems("big.top.x", "from .low import x\n"))

    def test_going_above_the_root_is_caught(self):
        self.assertTrue(self.problems("base.x", "from ... import x\n"))

    def test_a_subpackage_itself_as_the_target_is_caught(self):
        self.assertTrue(self.problems("big.top.x", "from .. import low\n"))
        self.assertTrue(self.problems("big.top.x", "from . import deep, low\n"))
        self.assertTrue(self.problems("__main__", "from . import base\n"))
        self.assertEqual([], self.problems("__main__", "from .base import x\n"))

    def test_targets_are_resolved_at_every_depth(self):
        self.assertEqual({"big.low.x"}, self.targets("big.top.deep.x", "from ...low import x\n"))
        # サブパッケージそのものは行き先に数えない（5 が止める）
        self.assertEqual(set(), self.targets("big.top.x", "from . import deep\n"))
        self.assertEqual({"big.top.deep.x"}, self.targets("big.top.x", "from .deep import x\n"))
        self.assertEqual(
            {"base.x"}, self.targets("big.top.deep.x", "def f():\n    from ....base import x\n")
        )

    def test_the_tickets_style_absolute_form_is_still_caught(self):
        self.assertTrue(self.problems("big.top.x", "from ccnavi.big.low import x\n"))


if __name__ == "__main__":
    unittest.main()
