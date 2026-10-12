"""セッションの頭で渡す、ccnavi の作業の決まり（`.ccnavi/CCNAVI.md`）。

文面は md に外だししてある。実行ファイルは、md の条件と差し込みを解いて 1 つの文にするだけで、
何を言うかは持たない。ccnavi を入れたワークスペースには、導入スクリプトが同じ md を配る。

md の書き方は 2 つだけ。

- 条件: `<!-- ccnavi:if 名前 -->` と `<!-- ccnavi:endif -->` で行を囲む。名前が成り立つときだけ、
  囲んだ行を載せる。途中に `<!-- ccnavi:else -->` を 1 つ置くと、成り立たないときの行を分けられる。
  入れ子にできる（外側も内側も成り立つときだけ載る）。名前は `CONDITIONS`
- 差し込み: `{{名前}}` を、ワークスペースごとの値に置き換える。名前は `SUBSTITUTIONS`

条件の名前ごとに独立した関数（`CONDITIONS`）があり、その真偽で行を載せるかを決める。
条件は、文と判定が食い違わないための仕掛けでもある。ワークツリーと git の行は、それを支える
deny（`workspace-root` / `raw-git`）がワークスペースのルールにあるときだけ載せる。ルールが無い
ワークスペースでその行を言うと、文と判定が食い違う。

md が無い・読めないときは、無いことだけを 1 行で言う。何も言わずに黙ると、決まりが渡って
いないことに誰も気づけない。長すぎるときは `MAX_CHARS` で切り、切ったことを末尾に言う。
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from typing import NamedTuple

from ..infra import modes, settings, tree
from ..tickets import ticket_guard, ticket_places

FILE = "CCNAVI.md"

# 1 回の応答に載せる本文の上限（文字数）。固定。常駐の文脈なので毎回これだけ重くなる。
MAX_CHARS = 6000



class Context(NamedTuple):
    """条件の判定に渡す、そのセッションの状況。"""

    conf: settings.Settings
    root: str
    docs: bool  # 索引の案内（`--docs`）を出す回か
    deny_ids: set[str]  # ワークスペースのルールの deny の id
    mode: str


def _workspace_root(ctx: Context) -> bool:
    """ワークスペースのルールに `workspace-root` の deny がある。"""
    return "workspace-root" in ctx.deny_ids


def _raw_git(ctx: Context) -> bool:
    """`raw-git` の deny がある、または生の git を組み込みで止める。"""
    return "raw-git" in ctx.deny_ids or ticket_guard.raw_git_applies(ctx.conf, ctx.root)


def _projects(ctx: Context) -> bool:
    """プロジェクトの置き場に、`.git` を持つプロジェクトがある。"""
    return bool(tree.projects(ctx.conf.projects))


def _tickets(ctx: Context) -> bool:
    """チケット制御を使っている。"""
    return bool(ctx.conf.tickets_enabled)


def _docs(ctx: Context) -> bool:
    """md の索引の案内（`--docs`）を出す回。"""
    return ctx.docs


def _dry_run(ctx: Context) -> bool:
    """CCNAVI_MODE が dry-run。"""
    return ctx.mode == modes.DRY_RUN


# md の `<!-- ccnavi:if 名前 -->` の名前と、その真偽を返す関数。条件を足すときはここに 1 つ足す。
CONDITIONS: dict[str, Callable[[Context], bool]] = {
    "workspace-root": _workspace_root,
    "raw-git": _raw_git,
    "projects": _projects,
    "tickets": _tickets,
    "docs": _docs,
    "dry-run": _dry_run,
}
SUBSTITUTIONS = (
    "worktrees",  # ワークツリーの置き場（`.claude/worktrees`）
    "git_sh",  # git のラッパー（打てる形のコマンド）
    "ticket_sh",  # チケットの sh
    "stage_sh",  # 保護対象を下書きで直すための sh
    "scratch",  # 下書きの置き場（`wip/scratchpad`）
    "projects",  # プロジェクトの置き場（ワークスペースルートからの相対）
    "tickets_dir",  # 提案の置き場
    "approved_dir",  # 承認済みチケットの置き場
    "mode_env",  # CCNAVI_MODE
)

_IF = re.compile(r"^\s*<!--\s*ccnavi:if\s+(\S+)\s*-->\s*$")
_ELSE = re.compile(r"^\s*<!--\s*ccnavi:else\s*-->\s*$")
_ENDIF = re.compile(r"^\s*<!--\s*ccnavi:endif\s*-->\s*$")
_DIRECTIVE = re.compile(r"<!--\s*ccnavi:")
_SUBSTITUTION = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def problems(text: str) -> list[str]:
    """md の書き方の誤り。`ccnavi --lint` が使う。

    条件の名前、差し込みの名前、対の取れていない囲みを見る。
    """
    found: list[str] = []
    elses: list[bool] = []  # 囲みごとに、else を置いたか
    for number, line in enumerate(text.splitlines(), 1):
        opened = _IF.match(line)
        if opened:
            elses.append(False)
            if opened.group(1) not in CONDITIONS:
                found.append(f"{number} 行目: 条件 {opened.group(1)} は使えない")
        elif _ELSE.match(line):
            if not elses:
                found.append(f"{number} 行目: ccnavi:else に対応する ccnavi:if が無い")
            elif elses[-1]:
                found.append(f"{number} 行目: 1 つの ccnavi:if に ccnavi:else は 1 つだけ")
            else:
                elses[-1] = True
        elif _ENDIF.match(line):
            if not elses:
                found.append(f"{number} 行目: ccnavi:endif に対応する ccnavi:if が無い")
            else:
                elses.pop()
        elif _DIRECTIVE.search(line):
            found.append(f"{number} 行目: ccnavi: の囲みは 1 行に 1 つ、単独で書く")
        for name in _SUBSTITUTION.findall(line):
            if name not in SUBSTITUTIONS:
                found.append(f"{number} 行目: 差し込み {{{{{name}}}}} は使えない")
    if elses:
        found.append("ccnavi:if に対応する ccnavi:endif が無い")
    return found


def render(text: str, flags: set[str], values: dict[str, str]) -> str:
    """条件に合う行だけを残し、差し込みを解く。空行は続けて 1 つにまとめ、前後の空行は取る。

    名前が `CONDITIONS` に無い条件は成り立たないものとして扱い、`SUBSTITUTIONS` に無い差し込みは
    そのまま残す（書き間違いが見える形で届く。誤りは `problems` が lint で言う）。
    `else` は、いちばん内側の囲みの真偽を反転する。外側が成り立たないときは、反転しても載らない。
    """
    out: list[str] = []
    stack: list[bool] = []
    for line in text.splitlines():
        opened = _IF.match(line)
        if opened:
            stack.append(opened.group(1) in flags)
            continue
        if _ELSE.match(line):
            if stack:
                stack[-1] = not stack[-1]
            continue
        if _ENDIF.match(line):
            if stack:
                stack.pop()
            continue
        if not all(stack):
            continue
        out.append(_SUBSTITUTION.sub(lambda m: values.get(m.group(1), m.group(0)), line))
    lines: list[str] = []
    for line in out:
        if not line.strip() and (not lines or not lines[-1].strip()):
            continue
        lines.append(line.rstrip())
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def home(conf: settings.Settings) -> str:
    """ccnavi ディレクトリの名前（既定は `.ccnavi`）。"""
    return (conf.project_home or settings.DEFAULT_PROJECT_HOME).strip("/")


def path(conf: settings.Settings, root: str) -> str:
    return os.path.join(root, home(conf), FILE)


def flags_for(
    conf: settings.Settings, root: str, docs: bool, deny_ids: set[str], mode: str
) -> set[str]:
    """成り立つ条件の名前。`CONDITIONS` の関数を 1 つずつ呼んで集める。"""
    ctx = Context(conf, root, docs, deny_ids, mode)
    return {name for name, check in CONDITIONS.items() if check(ctx)}


def values_for(conf: settings.Settings, root: str) -> dict[str, str]:
    """差し込みの値。"""
    projects = os.path.relpath(conf.projects, root).replace("\\", "/") if conf.projects else ""
    return {
        "worktrees": tree.WORKTREES_DIR.replace("\\", "/"),
        "git_sh": settings.script_command(root, "ccnavi-git.sh"),
        "ticket_sh": settings.script_command(root, "ccnavi-ticket.sh"),
        "stage_sh": settings.script_command(root, "ccnavi-stage.sh"),
        "scratch": ticket_places.SCRATCH,
        "projects": projects,
        "tickets_dir": conf.tickets,
        "approved_dir": conf.approved,
        "mode_env": settings.MODE_ENV,
    }


def session_text(
    conf: settings.Settings, root: str, docs: bool, deny_ids: set[str], mode: str
) -> str:
    """セッションの頭で渡す作業の決まり。md が無い・読めないときは、そのことだけを言う。"""
    full = path(conf, root)
    shown = f"{home(conf)}/{FILE}"
    try:
        with open(full, encoding="utf-8") as f:
            raw = f.read()
    except (OSError, UnicodeDecodeError):
        return (
            f"[ccnavi] {shown} が無いか読めないので、作業の決まりを渡せない。"
            "導入スクリプト（ccnavi-setup.sh）で配る。"
        )
    text = render(raw, flags_for(conf, root, docs, deny_ids, mode), values_for(conf, root))
    if len(text) > MAX_CHARS:
        text = (
            text[:MAX_CHARS].rstrip()
            + f"\n（長すぎるので {MAX_CHARS} 文字で切った。続きは {shown} を読む）"
        )
    return text
