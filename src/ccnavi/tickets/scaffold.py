"""親チケットの提案の雛形を作る（`ccnavi ticket new <識別子> --title <題> --plan <型,型,...>`）。

提案を手で書くと frontmatter の形を間違えやすく、とくにフェーズ定義の `scope` を `allow` に
写し忘れて、`design` フェーズの子が `wip/design/*` に書けず詰まる。この雛形の生成は、選んだ
フェーズの `scope` を `phases.yml` から集め、重複を除いて `allow` に書く（判定側で `wip/` を
既定許可にはしない。承認していない範囲が黙って広がるため）。

## 決まり

- 対象は親だけ。子の識別子（`<親>-<NN>-<NN>`）は断る
- 出力先は、識別子と同名のワークツリーの `<提案の置き場>/todo/<識別子>.md`。cwd がそのワークツリーの
  中でなければ、切り方を案内して止まる
- `phases.yml` が無いか読めないときは、承認の検査を通る plan を作れない（設計 9.7）ので止まる
- plan の型は `work` の定義だけ（`feedback` の定義は `feedback:` に置くもので、この雛形は出さない）
- plan の `after` は一直線（各項が前の項を待つ。終端が 1 つになり、承認の検査を通る）
- `scope: inherit` の型だけで `allow` が空になるときは止める（`allow` か `ask` が 1 件は要る）
- 既にある提案・承認済みチケットは上書きしない
- 書いたあとに `plan_phases.fill` で `phases:`（定義の写し）を差し込む。無いと `--agree` が落ちる

ネットワークにも git にも触らない。
"""

from __future__ import annotations

import json
import os
import unicodedata
from typing import TextIO

from ..infra import fsio, settings, tree
from ..infra.modes import EXIT_ERROR, EXIT_OK
from . import phasetypes, plan_phases, ticket_ids, ticket_model
from . import ticket as ticket_mod

# 雛形に添える、ユーザが書く欄の目印。
RATIONALE_TODO = "（ここにこの作業をする理由を書く）"

# `allow` に書く項の `match`。判定に使われるのは Write / Edit / NotebookEdit を含む項だけ。
ALLOW_MATCH = "Write|Edit"


def parse_plan(text: str) -> list[str]:
    """`--plan design,acceptance,implement` を型の並びにする。空の要素は捨てる。"""
    return [part.strip() for part in (text or "").split(",") if part.strip()]


def collect_allow(
    types: dict[str, phasetypes.PhaseType], plan: list[str]
) -> tuple[list[str], list[str]]:
    """plan の順に各型の `scope` を集め、重複を除いた glob の並びと、`inherit` の型の並び。

    重複は大文字小文字を区別せずに数える（範囲の照合が区別しないため）。
    """
    seen: set[str] = set()
    globs: list[str] = []
    inherited: list[str] = []
    for name in plan:
        pt = types[name]
        if pt.scope is None:
            if name not in inherited:
                inherited.append(name)
            continue
        for glob in pt.scope_globs:
            key = glob.lower()
            if key not in seen:
                seen.add(key)
                globs.append(glob)
    return globs, inherited


def render(ident: str, title: str, plan: list[str], allow: list[str], issue: int | None) -> str:
    """雛形の全文。`phases:` は書かない（書いたあとに `plan_phases.fill` が差し込む）。"""
    lines = [ticket_model.FENCE, f"version: {ticket_model.VERSION}", f"ticket: {ident}"]
    if issue is not None:
        lines.append(f"issue: {issue}")
    lines.append(f"title: {_quote(title)}")
    lines.append("plan:")
    for number, name in enumerate(plan, start=1):
        if number == 1:
            lines.append(f"  - {name}")
        else:
            lines.append(f"  - {{type: {name}, after: [{number - 1}]}}")
    lines.append("rationale: |")
    lines.append(f"  {RATIONALE_TODO}")
    lines.append("allow:")
    for glob in allow:
        lines.append(f"  - match: {ALLOW_MATCH}")
        lines.append(f"    glob: {_quote(glob)}")
    lines.append(ticket_model.FENCE)
    lines += [
        "## 背景",
        "",
        "## 変更の内容",
        "",
        "## 計画の理由",
        "",
    ]
    return "\n".join(lines)


def _quote(text: str) -> str:
    """YAML の二重引用符の文字列にする（JSON の文字列は YAML として読める）。"""
    return json.dumps(text, ensure_ascii=False)


def run(
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    cwd: str,
    ident: str,
    title: str,
    plan_text: str,
    issue: int | None,
) -> int:
    """`ticket new` の本体。書けたら 0、書かなければ 1（理由は標準エラー）。"""

    def refuse(why: str) -> int:
        stderr.write(f"ccnavi: ticket new: {why}。何も書かない\n")
        return EXIT_ERROR

    problem = ticket_ids.id_problem(ident)
    if problem:
        return refuse(f"識別子 {ident!r} が使えない（{problem}）")
    if ticket_ids.child_pattern().fullmatch(ident):
        return refuse(
            f"{ident} は子の識別子の形（<親>-<NN>-<NN>）。雛形を出すのは親だけ。"
            "子の提案は手で書く（設計 9.3）"
        )
    if not (title or "").strip():
        return refuse("--title <題> が要る")
    if any(unicodedata.category(ch) in ("Cc", "Zl", "Zp") for ch in title):
        return refuse("--title に改行や制御文字は使えない")
    if issue is not None and issue <= 0:
        return refuse("--issue には正の整数を渡す")
    plan = parse_plan(plan_text)
    if not plan:
        return refuse('--plan "design,acceptance,implement" のように型をカンマで区切って渡す')

    where = tree.tree_of(root, cwd, conf.projects)
    if (
        where is None
        or where.kind != tree.KIND_WORKTREE
        or unicodedata.normalize("NFC", where.name) != ident
    ):
        git_sh = settings.script_command(root, "ccnavi-git.sh")
        return refuse(
            f"cwd が .claude/worktrees/{ident} の中ではない。"
            "提案は識別子と同名のワークツリーに書く。"
            f"先に '{git_sh} worktree add {tree.worktree_path(root, ident)}"
            f" -b {ident} <統合先>' で切り、そのワークツリーの中から打つ"
        )

    types = phasetypes.load_types(conf, root, where.project)
    if types is None:
        return refuse(
            f"フェーズ定義（{where.project or '自身'} のレイヤーの phases.yml）が無いか読めない。"
            "plan の型の定義を読めず、承認の検査を通る plan と写し（phases:）を作れない"
            "（設計 9.7）。phases.yml を用意してから打つ"
        )
    unknown = [n for n in dict.fromkeys(plan) if n not in types]
    if unknown:
        return refuse(
            f"plan の型 {', '.join(unknown)} が phases.yml に無い。使える型: "
            + ", ".join(n for n, t in types.items() if t.kind == phasetypes.KIND_WORK)
        )
    feedback = [n for n in dict.fromkeys(plan) if types[n].kind != phasetypes.KIND_WORK]
    if feedback:
        return refuse(
            f"plan の型 {', '.join(feedback)} は feedback の定義。plan には work の定義だけを置く"
        )

    allow, inherited = collect_allow(types, plan)
    if not allow:
        return refuse(
            f"plan の型（{', '.join(inherited)}）が scope: inherit だけで、allow に書く範囲が"
            "集まらない。allow か ask が 1 件は要る（設計 9.3）。範囲を持つ型を plan に足す"
        )
    if len(allow) > ticket_mod.MAX_SCOPE_ENTRIES:
        return refuse(
            f"集めた範囲が {len(allow)} 件で、上限の {ticket_mod.MAX_SCOPE_ENTRIES} 件を超える。"
            "plan の型を減らす"
        )

    taken = _existing(conf, where.root, ident)
    if taken:
        return refuse(f"{ident} の提案か承認済みチケットが既に在る（{taken}）。上書きしない")

    path = os.path.join(
        where.root,
        (conf.tickets or settings.DEFAULT_TICKETS).replace("/", os.sep),
        ticket_model.TODO,
        ident + ".md",
    )
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    except OSError as exc:
        return refuse(f"{os.path.dirname(path)} を作れない（{exc}）")
    failed = fsio.write_new_durable(
        path, render(ident, title.strip(), plan, allow, issue).encode("utf-8")
    )
    if failed:
        return refuse(f"{path} に書けない（{failed}）")

    # 写し（phases:）を差し込む。無いと --agree が「項の定義が写しに無い」で落ちる。
    target, _ = ticket_mod.load(path)
    done = plan_phases.Filled(why=f"{path} を親の提案として読めない") if target is None else None
    if target is not None:
        target.project = where.project
        done = plan_phases.fill(conf, root, target, None)
    if done is not None and done.why:
        fsio.remove(path)
        return refuse(f"phases: を差し込めなかった（{done.why}）")

    stdout.write(f"{ident} の雛形を {path} に書いた\n")
    stdout.write(f"plan: {' -> '.join(plan)}（各項は前の項を待つ一直線）\n")
    stdout.write("allow（phases.yml の scope を集めた）:\n")
    for glob in allow:
        stdout.write(f"  {ALLOW_MATCH} {glob}\n")
    stdout.write(
        "次にやること: rationale と本文を書き、allow を実際の作業に合わせて絞る・足す。"
        "並行させたい項は after を直す。plan を変えたら "
        f"'ccnavi --plan-order {ident} --fill-phases' で phases: を打ち直す。"
        "'ccnavi --lint' で確かめてから、承認をユーザに頼む\n"
    )
    return EXIT_OK


def _existing(conf: settings.Settings, top: str, ident: str) -> str:
    """同じ識別子の提案か承認済みチケットがこのツリーに在れば、そのパス。無ければ空。"""
    tickets = os.path.join(top, (conf.tickets or settings.DEFAULT_TICKETS).replace("/", os.sep))
    approved = settings.approved_dir(conf, top)
    for base, states in (
        (tickets, (ticket_model.TODO, ticket_model.REVIEW)),
        (approved, (ticket_model.DOING, ticket_model.DONE)),
    ):
        for state in states:
            candidate = os.path.join(base, state, ident + ".md")
            if os.path.lexists(candidate):
                return candidate
    return ""
