"""セッションの頭で渡す、ccnavi の作業の決まり（`.ccnavi/CCNAVI.md`）。

文面は md に外だししてある。実行ファイルは、md の条件と差し込みを解いて 1 つの文にするだけで、
何を言うかは持たない。ccnavi を入れたワークスペースには、導入スクリプトが同じ md を配る。

md の書き方は 3 つだけ（条件 if・else と、差し込み）。

- 条件: `<!-- ccnavi:if 名前 -->` と `<!-- ccnavi:endif -->` で行を囲む。名前が成り立つときだけ、
  囲んだ行を載せる。途中に `<!-- ccnavi:else -->` を 1 つ置くと、成り立たないときの行を分けられる。
  入れ子にできる（外側も内側も成り立つときだけ載る）。囲みの中は 1 段につき半角空白 2 つで
  インデントしてよい（載せるときに取る）。名前は `CONDITIONS`
- 差し込み: `{{名前}}` を、ワークスペースごとの値に置き換える。名前は `SUBSTITUTIONS`

条件の名前ごとに独立した関数（`CONDITIONS`）があり、その真偽で行を載せるかを決める。組み込みに無い
名前は、ワークスペースの `.ccnavi/scripts/ccnavimd.sh` の関数 `ccnavi_cond_<名前>` が判定する
（戻り値 0 が真、1 が偽。配布先で実行ファイルを触らずに条件を足せる）。関数が無い・判定できない
ときは、囲んだ行をそのまま出す。
条件は、文と判定が食い違わないための仕掛けでもある。ワークツリーの行は、それを支える
deny（id `workspace-root`）がワークスペースのルールにあるときだけ載せる（条件の名前は
`workspace_root`）。ルールが無いワークスペースでその行を言うと、文と判定が食い違う。
git の行は、組み込みの deny（生の git）がいつも止めるので条件を付けず、入口（ラッパー）の有無で
文を分ける（条件の名前は `git_wrapper`）。

md が無い・読めないときは、無いことだけを 1 行で言う。何も言わずに黙ると、決まりが渡って
いないことに誰も気づけない。長すぎるときは `MAX_CHARS` で切り、切ったことを末尾に言う。
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable, Collection
from typing import NamedTuple

from ..infra import modes, settings, tree
from ..tickets import ticket_places

FILE = "CCNAVI.md"

# 1 回の応答に載せる本文の上限（文字数）。固定。常駐の文脈なので毎回これだけ重くなる。
MAX_CHARS = 6000

# Claude Code が付き添いの有無を示す環境変数。`0` が付き添いなし（`claude -p` など）。
ATTENDED_ENV = "CLAUDE_CODE_SESSION_ATTENDED"


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


def _git_wrapper(ctx: Context) -> bool:
    """ワークスペースに git の入口（`ccnavi-git.sh`）がある。無ければ git の行は頼む形になる。"""
    return settings.has_git_wrapper(ctx.root)


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


def unattended() -> bool:
    """非対話のセッション。Claude Code が付き添いなしと示したときだけ真。

    見るのは環境変数 `CLAUDE_CODE_SESSION_ATTENDED` が `0` のことだけ。未設定・`1`・読めない値は、
    対話として扱う（非対話と誤るほうが、聞くべきところで自走するので害が大きい）。この変数は
    Claude Code が文書にしていない。変わりうるので、判定はここ 1 か所に置く。
    実測で確かめたことと確かめていないことは、設計書の限界の節にある。
    """
    return os.environ.get(ATTENDED_ENV) == "0"


def _non_interactive(ctx: Context) -> bool:
    """非対話のセッション（`unattended`）。"""
    return unattended()


# md の `<!-- ccnavi:if 名前 -->` の名前と、その真偽を返す関数。条件を足すときはここに 1 つ足す。
CONDITIONS: dict[str, Callable[[Context], bool]] = {
    "workspace_root": _workspace_root,
    "git_wrapper": _git_wrapper,
    "projects": _projects,
    "tickets": _tickets,
    "docs": _docs,
    "dry_run": _dry_run,
    "non_interactive": _non_interactive,
}
# 配布先が足す条件は、`.ccnavi/scripts/ccnavimd.sh` に関数 `ccnavi_cond_<名前>`
# としてまとめて書く。組み込みと同じ名前の関数は使わない。
# 置き場は他の sh と同じ `.ccnavi/scripts/`。
# エージェントの Write / Edit は止まる（組み込みの builtin:guard-scripts）ので、
# セッションの頭に載る文面を、エージェントが条件の側から書き換えられない。
SCRIPTS_DIR = "scripts"
SCRIPT_FILE = "ccnavimd.sh"
FUNCTION_PREFIX = "ccnavi_cond_"
_FUNCTION = re.compile(rf"^\s*{FUNCTION_PREFIX}(\w+)\s*\(\s*\)", re.MULTILINE)
CONDITION_TIMEOUT_SECONDS = 2.0
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")  # 条件の名前。sh の関数名に使える形
SUBSTITUTIONS = (
    "worktrees",  # ワークツリーの置き場（`.claude/worktrees`）
    "git_sh",  # git のラッパー（打てる形のコマンド）
    "ticket_sh",  # チケットの sh
    "stage_sh",  # 保護対象を下書きで直すための sh
    "worktree_sh",  # 直接作業のワークツリーを切る sh
    "handoff",  # ユーザに渡す物の置き場（`wip/handoff`）
    "scratch",  # 使い捨ての置き場（ワークスペースルート直下の `scratchpad` の絶対パス。無視される）
    "projects",  # プロジェクトの置き場（ワークスペースルートからの相対）
    "tickets_dir",  # 提案の置き場
    "approved_dir",  # 承認済みチケットの置き場
    "mode_env",  # CCNAVI_MODE
)

# 囲みの中の行は、深さに応じて md の側でインデントしてよい。載せるときに取る。
INDENT = "  "

_IF = re.compile(r"^\s*<!--\s*ccnavi:if\s+(\S+)\s*-->\s*$")
_ELSE = re.compile(r"^\s*<!--\s*ccnavi:else\s*-->\s*$")
_ENDIF = re.compile(r"^\s*<!--\s*ccnavi:endif\s*-->\s*$")
_DIRECTIVE = re.compile(r"<!--\s*ccnavi:")
_SUBSTITUTION = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def name_problems(text: str) -> list[str]:
    """条件の名前が、英数字と `_` だけ（先頭は数字でない）になっていない行。lint が error で言う。

    sh の関数名に使えない名前（`-` を含むなど）は、組み込みでも sh でも判定できないので、
    書き方の誤りとして扱う。
    """
    found: list[str] = []
    for number, line in enumerate(text.splitlines(), 1):
        opened = _IF.match(line)
        if opened and not NAME.match(opened.group(1)):
            found.append(
                f"{number} 行目: 条件の名前 {opened.group(1)} は指定できない"
                "（英数字と _ だけ。先頭は数字にしない。`-` は _ にする）"
            )
    return found


def problems(text: str, scripts: Collection[str] = ()) -> list[str]:
    """md の書き方の誤り。`ccnavi --lint` が使う。

    条件の名前（組み込みか、`scripts`＝sh に書かれた関数の名前にあるもの。形の誤りは
    `name_problems`）、差し込みの名前、対の取れていない囲みを見る。
    """
    found: list[str] = []
    elses: list[bool] = []  # 囲みごとに、else を置いたか
    for number, line in enumerate(text.splitlines(), 1):
        opened = _IF.match(line)
        if opened:
            elses.append(False)
            name = opened.group(1)
            if NAME.match(name) and name not in CONDITIONS and name not in scripts:
                found.append(
                    f"{number} 行目: 条件 {name} は指定できない"
                    f"（組み込みの条件でも {SCRIPT_FILE} の {function_name(name)} でもない）"
                )
        elif _ELSE.match(line):
            if not elses:
                found.append(f"{number} 行目: ccnavi:else に対応する ccnavi:if がありません。")
            elif elses[-1]:
                found.append(f"{number} 行目: 1 つの ccnavi:if に ccnavi:else は 1 つだけ")
            else:
                elses[-1] = True
        elif _ENDIF.match(line):
            if not elses:
                found.append(f"{number} 行目: ccnavi:endif に対応する ccnavi:if がありません。")
            else:
                elses.pop()
        elif _DIRECTIVE.search(line):
            found.append(f"{number} 行目: ccnavi: の囲みは 1 行に 1 つ、単独で書く")
        for name in _SUBSTITUTION.findall(line):
            if name not in SUBSTITUTIONS:
                found.append(f"{number} 行目: 差し込み {{{{{name}}}}} は指定できない")
    if elses:
        found.append("ccnavi:if に対応する ccnavi:endif がありません。")
    return found


def _dedent(line: str, depth: int) -> str:
    """囲みの深さの分だけ、行頭のインデント（`INDENT` 1 つにつき 1 段）を取る。"""
    for _ in range(depth):
        if not line.startswith(INDENT):
            break
        line = line[len(INDENT) :]
    return line


def render(text: str, flags: set[str], values: dict[str, str]) -> str:
    """条件に合う行だけを残し、差し込みを解く。空行は続けて 1 つにまとめ、前後の空行は取る。

    `flags` に無い名前の条件は成り立たないものとして扱い（組み込みに無い名前は、呼ぶ側が
    `script_flags` で決めて渡す）、`SUBSTITUTIONS` に無い差し込みは
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
        line = _dedent(line, len(stack))
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


def function_name(name: str) -> str:
    """md の条件の名前に対応する、sh の関数の名前。"""
    return FUNCTION_PREFIX + name


def script_path(conf: settings.Settings, root: str) -> str:
    return os.path.join(root, home(conf), SCRIPTS_DIR, SCRIPT_FILE)


def script_names(conf: settings.Settings, root: str) -> set[str]:
    """`ccnavimd.sh` に関数として書かれている条件の名前。

    名前は Unicode の単語文字の連なりで拾う（非 ASCII の文字も入りうる）。
    英数字と `_` に限る検査（`NAME`）は、ここではしない。組み込みと同じ名前は除く。
    """
    try:
        with open(script_path(conf, root), encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError):
        return set()
    return {n for n in _FUNCTION.findall(text) if n not in CONDITIONS}


def run_script(conf: settings.Settings, root: str, name: str, ctx: Context) -> bool:
    """条件の関数を走らせる。戻り値 0 が真、1 が偽。判定できなければ真（囲んだ行を出す）。

    判定できないのは、走らせられない・時間切れ・0 と 1 以外の戻り値（関数が無い 127、
    シェルのエラーなど）。黙って落とすと、載るはずの決まりが誰にも気づかれずに届かなくなるため、
    載せる側に倒す。

    渡す状況は環境変数: `CCNAVI_ROOT`（ワークスペースルート）、`CCNAVI_DOCS`（索引の案内を
    出す回なら 1）、`CCNAVI_DENY_IDS`（ルールの deny の id を空白区切り）、`CCNAVI_MODE`。
    cwd はワークスペースルート。
    """
    environment = dict(os.environ)
    environment.update(
        {
            "CCNAVI_ROOT": root,
            "CCNAVI_DOCS": "1" if ctx.docs else "0",
            "CCNAVI_DENY_IDS": " ".join(sorted(ctx.deny_ids)),
            settings.MODE_ENV: ctx.mode,
        }
    )
    path = script_path(conf, root).replace("\\", "/")
    try:
        done = subprocess.run(
            # 読み込みの失敗は 3 で抜ける。構文の誤りで `.` が 1 を返す sh（macOS の bash）でも、
            # 偽の 1 と取り違えずに判定できないものとして扱うため
            ["sh", "-c", '. "$1" || exit 3; "$2"', "sh", path, function_name(name)],
            cwd=root,
            capture_output=True,
            env=environment,
            timeout=CONDITION_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return True
    return done.returncode != 1


def script_flags(conf: settings.Settings, root: str, raw: str, ctx: Context) -> set[str]:
    """md が使っている、組み込みに無い条件の名前のうち、成り立つもの。使っていない sh は走らせない。

    関数が無い名前は判定できないので、成り立つものとして囲んだ行をそのまま出す
    （誤りは `ccnavi --lint` が言う）。
    """
    available = script_names(conf, root)
    used = {m.group(1) for m in map(_IF.match, raw.splitlines()) if m} - CONDITIONS.keys()
    return {n for n in used if n not in available or run_script(conf, root, n, ctx)}


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
        "worktree_sh": settings.script_command(root, "ccnavi-worktree.sh"),
        "handoff": ticket_places.HANDOFF,
        "scratch": os.path.join(root, ticket_places.SCRATCH_ROOT).replace("\\", "/"),
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
    flags = flags_for(conf, root, docs, deny_ids, mode)
    flags |= script_flags(conf, root, raw, Context(conf, root, docs, deny_ids, mode))
    text = render(raw, flags, values_for(conf, root))
    if len(text) > MAX_CHARS:
        text = (
            text[:MAX_CHARS].rstrip()
            + f"\n（長すぎるので {MAX_CHARS} 文字で切った。続きは {shown} を読む）"
        )
    return text
