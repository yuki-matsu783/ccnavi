"""`ccnavi ticket new <先頭の語> <slug> --title <題> --plan <定義,定義,…>`。親の提案の雛形を作る。

手書きでは frontmatter の形を間違えやすく、`design` のフェーズを置いたのに `wip/design/*` が
`allow` に入らず、子が範囲の外で止まる。雛形は次を満たす。

- 識別子は `ticket next-id` と同じ決まりで決める（`--issue` があればその番号）
- `plan:` は並べた順に一直線（各項が直前の項を待つ）。終端が 1 つになる
- `phases:` は計画が使う定義の写し（`--fill-phases` と同じ中身）
- `allow:` は計画の各定義の `scope` を集めて、重複を除いて入れる。`scope: inherit` は集める
  ものが無い。それだけで `allow` が空になるときは `--allow <glob>` が要る
- 書き出し先は、cwd が入っているワークツリーの `wip/proposals/todo/`

判定側が `wip/` を最初から許可することはしない。承認していない範囲が黙って広がるため。
書くのは承認待ちの提案だけで、承認はしない。何も読み書きせずネットワークにも出ない点は
`ticket next-id` と同じ（書くのは提案 1 本だけ）。
"""

from __future__ import annotations

import os
from typing import TextIO

import yaml

from ..infra import fsio, settings, tree
from . import next_id, phasetypes
from . import ticket as ticket_mod

# 判定に使われる書き込みの道具。提案の書式（docs/design/tickets/proposal-format.md）と同じ。
_MATCH = "Write|Edit"
_RATIONALE = "（なぜこの変更が要るかを書く）"


def collect_allow(
    plan: list[str], types: dict[str, phasetypes.PhaseType], extra: list[str]
) -> list[str]:
    """計画の定義の scope を並び順に集め、重複を除いたもの。`extra` はその後ろ。"""
    seen: list[str] = []
    for name in plan:
        pt = types[name]
        if pt.inherits_scope:
            continue
        for glob in pt.scope_globs:
            if glob not in seen:
                seen.append(glob)
    for glob in extra:
        if glob not in seen:
            seen.append(glob)
    return seen


def plan_items(plan: list[str]) -> list:
    """一直線の計画。1 項目は何も待たず、以降は直前の項を待つ。"""
    items: list = []
    for number, name in enumerate(plan, start=1):
        items.append(name if number == 1 else {"type": name, "after": [number - 1]})
    return items


def render(
    ident: str,
    title: str,
    issue: int | None,
    plan: list[str],
    types: dict[str, phasetypes.PhaseType],
    allow: list[str],
) -> str:
    """提案の全文。"""
    front: dict = {"version": 1, "ticket": ident}
    if issue is not None:
        front["issue"] = issue
    front["title"] = title
    front["plan"] = plan_items(plan)
    front["phases"] = {name: phasetypes.as_copy(types[name]) for name in dict.fromkeys(plan)}
    front["rationale"] = _RATIONALE
    front["allow"] = [{"match": _MATCH, "glob": glob} for glob in allow]
    dumped = yaml.safe_dump(
        front, allow_unicode=True, sort_keys=False, default_flow_style=None, width=1000
    )
    return f"---\n{dumped}---\n"


def run(
    stdout: TextIO,
    stderr: TextIO,
    root: str,
    conf: settings.Settings,
    cwd: str,
    words: list[str],
    *,
    title: str,
    plan: str,
    issue: int | None,
    extra_allow: list[str],
) -> int:
    """`ticket new` の本体。終了コードは 0 が成功、1 が作れない理由がある。"""

    def refuse(why: str) -> int:
        stderr.write(f"ccnavi: ticket new: {why}\n")
        return 1

    if len(words) != 2:
        return refuse("<先頭の語> <slug> の 2 つで打つ")
    prefix, slug = words
    names = [n.strip() for n in plan.split(",") if n.strip()]
    if not title.strip():
        return refuse("--title <題> が要る")
    if not names:
        return refuse("--plan <定義,定義,…> が要る（phases.yml の work の定義をコンマで並べる）")

    here = tree.tree_of(root, cwd, conf.projects)
    if here is None or here.is_main:
        return refuse(
            "親の提案は識別子と同名のワークツリーに書く。先に "
            "`ccnavi-worktree.sh <識別子>` でワークツリーを切り、その中で打つ"
        )
    types = phasetypes.load_types(conf, root, here.project)
    if types is None:
        return refuse(
            f"フェーズ定義（{here.project or '自身'} のレイヤーの phases.yml）が無いか読めない"
        )
    for name in names:
        pt = types.get(name)
        if pt is None:
            return refuse(f"計画の定義 {name} が phases.yml に無い（あるのは {', '.join(types)}）")
        if pt.kind != phasetypes.KIND_WORK:
            return refuse(
                f"{name} は {pt.kind} の定義で、全体計画（--plan）には置けない。"
                "フィードバック計画は提案を作ってから `feedback:` に書く"
            )

    allow = collect_allow(names, types, extra_allow)
    if not allow:
        return refuse(
            "計画の定義がどれも scope: inherit で、allow に入れる範囲が無い。"
            "--allow <glob> で範囲を渡す"
        )

    number = issue if issue is not None else next_id.serial_now(conf, root)
    problem = next_id.identifier_problem(prefix, slug, number, conf.branch_prefixes)
    if problem:
        return refuse(problem)
    ident = f"{prefix}-{number}-{slug}"

    text = render(ident, title.strip(), issue, names, types, allow)
    read, found = ticket_mod.parse(text)
    if read is None or read.ticket != ident or not read.has_plan:
        detail = "; ".join(str(p) for p in found) or "読み直せない"
        return refuse(f"組んだ提案を読み直せなかった（{detail}）。何も書かない")

    path = os.path.join(here.root, conf.tickets, "todo", f"{ident}.md")
    if os.path.exists(path):
        return refuse(f"{path} が既にある。上書きしない")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    failed = fsio.write_bytes_atomic(path, text.encode("utf-8"))
    if failed:
        return refuse(f"{path} に書けない（{failed}）")

    if here.name != ident:
        stderr.write(
            f"ccnavi: ticket new: ワークツリーの名前 {here.name} が識別子 {ident} と違う。"
            "承認の前に識別子と同名のワークツリーへ移す（docs/claude/worktree.md）\n"
        )
    stdout.write(f"{path}\n")
    return 0
