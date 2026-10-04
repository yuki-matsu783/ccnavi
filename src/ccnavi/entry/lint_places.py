"""`--lint` のうち、置き場の検査。下書きの置き場が git に追跡されていないか、
プロジェクトの置き場が正しい形か、走査されないチケットの置き場が残っていないかを見る。
"""

from __future__ import annotations

import os

from ..infra import gitcmd, gitstate, settings, tree
from ..policy.rules import SEVERITY_ERROR, SEVERITY_WARN, Problem
from ..tickets import ticket as ticket_mod


def _scratch(conf: settings.Settings, root: str) -> list[Problem]:
    """下書きの置き場が、そのリポジトリの git に追跡されていないか（REQ-TKT-44）。

    実行前チェックはチケットの範囲を `scratchpad/` に当てない（`ticket.is_scratch_place`）。外して
    よい根拠は「git が追跡しないので統合先のブランチに乗らない」ことの 1 つだけ。

    **この警告で穴が無くなるわけではない。** 根拠が崩れた場合は、実行後チェックと
    サブエージェント終了時チェックが `scratchpad/` の変更を範囲外として報告する（`is_unscoped` の
    説明）。ここが言うのは、その報告が出はじめる前にユーザが気づけるようにするため。

    問うのは 2 つ。**追跡されているファイルが既にあるか**（`git ls-files`）と、これから
    書くものが追跡されるか（`git check-ignore`）。前者だけでは、まだ何も置いていない
    リポジトリで見逃す。後者だけでは、`/{SCRATCH}/` を足す前から追跡されていたファイルを
    見逃す（`.gitignore` は既に追跡されているファイルを対象にしない）。

    見るのはワークスペースと各プロジェクトのそれぞれの git。プロジェクトは自分の
    `.gitignore` を持つので、ワークスペース側の 1 行はそこまで及ばない。ワークツリーは見ない。
    あちらはブランチごとに中身が変わるうえ、実行時に実行後チェックとサブエージェント
    終了時チェックが拾うので、ここで数え上げると同じことを 2 度言うことになる。

    チケット制御が切れているときは言わない。そのとき範囲の判定自体が動かない。

    git が無ければ何も言わない。無いものを「入っていない」と報告すると、正しい設定に
    苦情を出すことになる。
    """
    if not conf.tickets_enabled:
        return []
    problems: list[Problem] = []
    # 前置きは `(scratch)` にする。プロジェクトのぶんも `(projects/<名前>)` は前置きにしない。
    # あちらはその層の設定についての苦情で、ここは追跡の話。同じ前置きにすると、
    # 「層について何も言わない」ことを見ているテストや読み手に、別の話が入り込む。
    where = [("(scratch)", root)]
    where += [
        (f"(scratch/{p.name})", tree.project_root(conf.projects, p.name))
        for p in tree.projects(conf.projects)
    ]
    place = ticket_mod.SCRATCH
    for name, home in where:
        tracked = _tracked(home, place)
        ignored = _ignored(home, place + "/")
        if tracked:
            why = f"`{place}/` に追跡されているファイルがある（{tracked}）"
        elif ignored is False:
            why = f"`{place}/` がこのリポジトリの git で無視されていない"
        else:
            continue
        problems.append(
            Problem(
                SEVERITY_WARN,
                name,
                f"{why}。実行前チェックはチケットの範囲をここに当てないので、追跡されて"
                "いると、承認した範囲の外のファイルがコミットに入りうる。入ったぶんは"
                "実行後チェックとサブエージェント終了時チェックが範囲外として報告するので、"
                "下書きのたびに範囲外と報告される。"
                f"このリポジトリの `.gitignore` に `/{place}/` を足して追跡から外すか"
                "（プロジェクトのリポジトリに運用の痕跡を残したくないなら"
                f" `.git/info/exclude` でもよい）、`{place}/` を使わずにチケットの範囲の"
                "中で作業してください",
            )
        )
    return problems


def _tracked(root: str, rel: str) -> str:
    """その置き場の下で git が追跡しているファイル 1 本。無ければ空。

    `.gitignore` は既に追跡されているファイルを対象にしないので、無視の設定だけを見ても
    「追跡されていない」は言えない。索引に何が入っているかを直接問う。
    """
    rc, out = gitcmd.output(root, ["ls-files", "--", rel + "/"], gitstate.TIMEOUT_SECONDS)
    if rc != 0:
        return ""
    first = out.strip().splitlines()
    return first[0] if first else ""


def _projects(conf: settings.Settings, root: str) -> list[Problem]:
    """プロジェクトの置き場が正しい形か（REQ-MLT-16）。

    置き場が無いのは不備ではない。あるなら、ワークスペースの git で無視されていること、
    予約名（`common` / `self`、表記違いも含む）を使っていないこと、プロジェクトが
    `.claude/` を持たないことを見る。層の中身は
    `_layers` が見る。
    """
    problems: list[Problem] = []
    found = tree.projects(conf.projects)
    if not found:
        return problems
    rel = os.path.relpath(conf.projects, root).replace(os.sep, "/")
    if _ignored(root, rel) is False:
        problems.append(
            Problem(
                SEVERITY_WARN,
                "(projects)",
                f"{rel}/ がワークスペースの git で無視されていない。プロジェクトは自分の git を"
                "持つので、ワークスペースの `.gitignore` に入れてください",
            )
        )
    for p in found:
        where = f"(projects/{p.name})"
        if settings.is_reserved_layer_name(p.name):
            reserved = " と ".join(f"`{name}`" for name in settings.RESERVED_LAYER_NAMES)
            problems.append(
                Problem(
                    SEVERITY_ERROR,
                    where,
                    f"{reserved} は層の名前として予約してある（`{settings.LAYER_COMMON}` は共通層、"
                    f"`{settings.LAYER_SELF}` はワークスペース自身の層）。このプロジェクトは"
                    f"層として数えていない（id の `{p.name}:` がどちらの層を指すか決まらないため。"
                    "大文字小文字の違いは問わない）。ここに置いた宣言は 1 件も効いておらず、"
                    "このプロジェクトを行き先にするパスを持つツール（Read / Grep / Glob / Write / "
                    "Edit / NotebookEdit）は共通層だけで判定している。"
                    "プロジェクトを別の名前に変えてください",
                )
            )
        if os.path.isdir(os.path.join(p.root, ".claude")):
            # 文面の先頭「.claude/ を持つ」は VS Code 拡張（extensions/vscode/ccnavi-board の
            # projects-render.ts）が同じ事象の説明と重ねないために見ている。変えるならそちらも直す
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    where,
                    ".claude/ を持つ。Claude Code がそこのスキルを読み込み、そこへ cd するだけで"
                    "別のルートのように見える。"
                    f"プロジェクトの設定は {conf.project_home}/ に置いてください",
                )
            )
    return problems


def _ticket_places(conf: settings.Settings, root: str) -> list[Problem]:
    """走査されないチケットの置き場が残っていないか（REQ-MLT-16）。

    提案の置き場はどのツリーでも同じ相対（`wip/proposals/`）で、プロジェクト向けはその
    プロジェクトのツリーに置く（設計 11.5、REQ-MLT-14）。ワークスペースの
    `wip/<名前>/proposals/` は、名前が `projects/` に在っても在らなくても走査されない。走査
    されない置き場は、提案があっても画面にもボードにも出ない。気づかないうちに消えるのが
    いちばん困るので名指しし、名前が在るなら正しい置き場を案内する。error にはしない。
    判定は動いている。
    """
    parts = [p for p in conf.tickets.split("/") if p]
    if len(parts) < 2:
        # 名前を挟む位置が置き場のパスから決められない。言えないことは言わない。
        return []
    head, tail = parts[0], parts[1:]
    known = {p.name for p in tree.projects(conf.projects)}
    where = os.path.relpath(conf.projects, root).replace(os.sep, "/") if conf.projects else "(無し)"
    problems: list[Problem] = []
    try:
        names = sorted(os.listdir(os.path.join(root, head)))
    except OSError:
        return []
    for name in names:
        if name == tail[0]:
            continue
        place = os.path.join(root, head, name, *tail)
        if not os.path.isdir(place):
            continue
        rel = "/".join([head, name, *tail])
        if name in known:
            proper = "/".join([where, name, *parts])
            why = f"プロジェクト向けの提案はそのプロジェクトの側 {proper}/ に置いてください"
        else:
            why = (
                f"{name} が {where} のプロジェクトとして数えられていない"
                "（`.git` がまだ無いか、名前が違う）"
            )
        problems.append(
            Problem(SEVERITY_WARN, "(projects)", f"{rel}/ の提案は走査されていない。{why}")
        )
    return problems


def _ignored(root: str, rel: str) -> bool | None:
    """このパスをワークスペースの git が無視しているか。git が無ければ None。"""
    done = gitcmd.run(root, ["check-ignore", "-q", rel], gitstate.TIMEOUT_SECONDS)
    if done.failure:
        return None
    if done.code == 0:
        return True
    return False if done.code == 1 else None
