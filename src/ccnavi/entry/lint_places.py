"""`--lint` のうち、置き場の検査。下書きの置き場が git に追跡されていないか、
プロジェクトの置き場が正しい形か、走査されないチケットの置き場が残っていないかを見る。
"""

from __future__ import annotations

import os

from ..infra import gitcmd, gitstate, settings, tree
from ..policy.rules import SEVERITY_ERROR, SEVERITY_WARN, Problem
from ..tickets import ticket_places


def _scratch(conf: settings.Settings, root: str) -> list[Problem]:
    """下書きの置き場が、そのリポジトリの git に追跡されていないか（REQ-TKT-44）。

    実行前チェックはチケットの範囲を `scratchpad/` に当てない
    （`ticket_places.is_scratch_place`）。外してよい根拠は「git が追跡しないので
    統合先のブランチに乗らない」ことの 1 つだけ。

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
    # あちらはそのレイヤーの設定についての苦情で、ここは追跡の話。同じ前置きにすると、
    # 「レイヤーについて何も言わない」ことを見ているテストや読み手に、別の話が入り込む。
    where = [("(scratch)", root)]
    where += [
        (f"(scratch/{p.name})", tree.project_root(conf.projects, p.name))
        for p in tree.projects(conf.projects)
    ]
    place = ticket_places.SCRATCH
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


# `projects/` がワークスペースの git の索引に載っているときの `(projects)` の warn の先頭の句。
# ぶつかり（通常のファイル）と載せ忘れ（gitlink だけ）で共通にする。VS Code 拡張
# （extensions/vscode/ccnavi-board の src/webview/projects/text.ts の isTrackedProjectsDir）が
# この句で始まる苦情を見て `.gitignore` のボタンと「無視されていない」の帯を出さない。
# `tests/`（test_lint_places.py・test_setup.py）と導入スクリプト（scripts/ccnavi-setup.sh）も
# 同じ句を持つ。
# 載せ忘れは先頭の句の直後が `（入れ子のリポジトリとして` で、`tests/` がそれで見分ける。
# 変えるならそちらも直す（設計 wip/design/i0064-fixed-places.md §4.2）。
_TRACKED_LEAD = "`{rel}/` はワークスペースの git が追跡している"
# 索引の mode のうち gitlink（入れ子のリポジトリを 1 つの版として載せたもの）。
_GITLINK_MODE = "160000"


def _projects(conf: settings.Settings, root: str) -> list[Problem]:
    """プロジェクトの置き場が正しい形か（REQ-MLT-16）。

    置き場が無いのは不備ではない。あるなら、ワークスペースの git で無視されていること、
    予約名（`common` / `self`、表記違いも含む）を使っていないこと、プロジェクトが
    `.claude/` を持たないことを見る。層の中身は
    `_layers` が見る。無視の確認は、置き場のディレクトリが在るなら、プロジェクトが 0 件でも行う。
    ディレクトリ自体が無いワークスペースでは何も言わない（プロジェクトを使わない人への苦情になる）。

    その前に、置き場がワークスペースの git の索引に載っていないかを見る（`_in_index`）。
    載っていれば「無視されていない」の代わりにそれを言う。プロジェクトが 1 つも無くても
    見る。これからプロジェクトを置こうとする人に要る知らせで、入れ子のリポジトリが
    消えて gitlink だけが索引に残っている場合もある。
    """
    problems: list[Problem] = []
    rel = _projects_rel(conf, root)
    indexed = _in_index(root, rel) if rel else None
    if indexed is not None:
        problems.append(Problem(SEVERITY_WARN, "(projects)", indexed))
    # 無視の確認は、置き場のディレクトリが在るときだけ行う（プロジェクトが 0 件でもよい）。
    # ディレクトリが無いのはプロジェクトを使っていないワークスペースで、何も言わない。
    # `rel` が空（`--projects ""` やワークスペースの外を指す診断のフラグ）のときは、
    # ワークスペースの git の話ではないので確認を飛ばす。
    # 末尾の `/` は付けない。`projects` がシンボリックリンクだと `projects/` は
    # git が rc=128（beyond a symbolic link）で断る。ディレクトリが実在すれば、
    # 末尾の `/` が無くても `/projects/` のようなディレクトリ向けの行に当たる。
    if rel and indexed is None and os.path.isdir(conf.projects) and _ignored(root, rel) is False:
        problems.append(
            Problem(
                SEVERITY_WARN,
                "(projects)",
                f"{rel}/ がワークスペースの git で無視されていない。プロジェクトは自分の git を"
                "持つので、ワークスペースの `.gitignore` に入れてください",
            )
        )
    for p in tree.projects(conf.projects):
        where = f"(projects/{p.name})"
        if settings.is_reserved_layer_name(p.name):
            reserved = " と ".join(f"`{name}`" for name in settings.RESERVED_LAYER_NAMES)
            problems.append(
                Problem(
                    SEVERITY_ERROR,
                    where,
                    f"{reserved} はレイヤーの名前として予約してある"
                    f"（`{settings.LAYER_COMMON}` は共通レイヤー、"
                    f"`{settings.LAYER_SELF}` はワークスペース自身のレイヤー）。このプロジェクトは"
                    f"レイヤーとして数えていない"
                    f"（id の `{p.name}:` がどちらのレイヤーを指すか決まらないため。"
                    "大文字小文字の違いは問わない）。ここに置いた宣言は 1 件も効いておらず、"
                    "このプロジェクトを行き先にするパスを持つツール（Read / Grep / Glob / Write / "
                    "Edit / NotebookEdit）は共通レイヤーだけで判定している。"
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


def _projects_rel(conf: settings.Settings, root: str) -> str:
    """プロジェクトの置き場の、ワークスペースルートからの相対（"/" 区切り）。

    置き場は既定の `projects` に固定。診断のフラグで空やワークスペースの外を
    指されたときは空を返し、索引は見ない（ワークスペースの git の話ではなくなる）。
    """
    if not conf.projects:
        return ""
    try:
        rel = os.path.relpath(conf.projects, root).replace(os.sep, "/")
    except ValueError:
        # Windows で別のドライブを指している。
        return ""
    if rel in (".", "") or rel == ".." or rel.startswith("../"):
        return ""
    return rel


def _index_under(root: str, rel: str) -> tuple[str, list[str]] | None:
    """索引で `<rel>/` の下に載っているものを、通常のファイルと gitlink に分けて返す。

    返すのは（最初の通常のファイル、gitlink の一覧）。通常のファイルが無ければ 1 つ目は空。
    通常のファイルは文面に 1 本目しか使わないので、残りは持たない（ワークスペースの
    ソースが `projects/` の下に数万本あっても一覧を作らない）。

    `ls-files -s -z` で読む。各行は `<mode> <oid> <stage>\\t<path>` で、`-z` なので
    パスの引用（`core.quotepath`）に左右されない。mode `160000` が gitlink で、それ以外
    （`100644`・`100755`・`120000`）は通常のファイル。衝突中の段で同じパスが並ぶのは 1 本に畳む。
    git が無い・失敗したときは None（`_tracked` と同じく何も言わない）。

    `_tracked`（`_scratch` も使う）とは別に置く。あちらは 1 本目の名前だけを返し、mode を見ない。
    """
    rc, out = gitcmd.output(
        root, ["ls-files", "-s", "-z", "--", rel + "/"], gitstate.TIMEOUT_SECONDS
    )
    if rc != 0:
        return None
    first_file = ""
    links: list[str] = []
    seen_links: set[str] = set()
    for record in out.split("\0"):
        head, tab, path = record.partition("\t")
        if not tab or not path:
            continue
        if head.split(" ", 1)[0] == _GITLINK_MODE:
            if path not in seen_links:
                seen_links.add(path)
                links.append(path)
        elif not first_file:
            first_file = path
    return first_file, links


# sh が語を割る・展開する文字。案内に載せるパスがこれを含むときだけ `'…'` で囲む
# （`_sh_word`）。導入スクリプト（scripts/ccnavi-setup.sh の sh_word）が同じ集合を持つ。
_SH_SPECIAL = frozenset(" \t\n'\"\\$`!*?[](){}<>|&;#~")


def _sh_word(path: str) -> str:
    """案内のコマンドに載せるパスを、sh で 1 語として読める表記にする。

    割れる文字（`_SH_SPECIAL`）を含まなければそのまま（`projects/lib`）。含めば `'…'` で囲み、
    中の `'` は `'\\''` に置く（`projects/my lib` → `'projects/my lib'`、
    `projects/it's` → `'projects/it'\\''s'`）。英数字以外でも、日本語のように sh で割れない
    文字は囲まない。導入スクリプトの sh_word と 1 字違わず同じ表記にする。
    """
    if not any(c in _SH_SPECIAL for c in path):
        return path
    return "'" + path.replace("'", "'\\''") + "'"


def _in_index(root: str, rel: str) -> str | None:
    """置き場が索引に載っているなら、その苦情の文面。載っていなければ None（設計 §4.1・§4.2）。

    - 通常のファイルが 1 本以上ある: **ぶつかり**。ワークスペース自身のソースに `projects/` が
      あるので、`.gitignore` に足せという案内は誤り（ソースが追跡から外れる）。改名を案内する。
      gitlink も載っていれば 1 文足して名指しする
    - gitlink だけ: **載せ忘れ**。`.gitignore` に入れる前に `git add -A` した人の索引の形で、
      改名は要らない。索引から外して無視に入れる 2 手を案内する。`.gitignore` に既に
      `/projects/` があっても、索引に載っている限り追跡は外れないので言う
    - `.gitmodules` で意図して置いたサブモジュールも mode だけで見るので載せ忘れになる
      （設計 §4.5 の分岐 2）

    導入スクリプト（scripts/ccnavi-setup.sh）が同じ条件と同じ文面を持つ。変えるならそちらも直す
    （tests/sh/test_setup.py が 1 文目の一致を見る）。
    """
    split = _index_under(root, rel)
    if split is None:
        return None
    first_file, links = split
    lead = _TRACKED_LEAD.format(rel=rel)
    if first_file:
        text = (
            f"{lead}（例: `{first_file}`）。"
            f"ccnavi はワークスペース直下の `{rel}/` をプロジェクトの置き場として使い、"
            "名前は変えられない。"
            f"このままだと `{rel}/` の下で `.git` を持つディレクトリ（サブモジュールを含む）が"
            "プロジェクトとして数えられ、その中の設定が判定に使われる。"
            f"直すには、ワークスペースの `{rel}/` を別の名前に移す（例: `git mv {rel} apps`）。"
            f"ccnavi でプロジェクトを置かないなら、このままでも動く。そのときは `{rel}/` の下に"
            "`.git` を持つものを置かない"
        )
        if links:
            named = "、".join(f"`{p}`" for p in links)
            text += (
                f"。索引には入れ子のリポジトリ（{named}）も載っている。"
                "ccnavi のプロジェクトとして使うなら、改名の前に"
                f" `git rm --cached {' '.join(_sh_word(p) for p in links)}` で索引から外し、"
                f"改名のあとで `{rel}/` の下へ戻す"
            )
        return text
    if not links:
        return None
    more = f" ほか {len(links) - 1} 件" if len(links) > 1 else ""
    return (
        f"{lead}（入れ子のリポジトリとして: `{links[0]}`{more}）。"
        "`.gitignore` に入れる前に `git add` したものとみられる。"
        "プロジェクトは自分の git を持つので、ワークスペースの git には載せない。"
        "載せたままだと、ワークスペースのコミットがプロジェクトの版を記録し続け、"
        f"`.gitignore` に `/{rel}/` を足しても追跡は外れない。"
        f"直すには、ワークスペースで `git rm -r --cached {rel}` を打ち"
        f"（ファイルは消えない。まだコミットしていなければ `git reset -- {rel}`）、"
        f"`.gitignore` に `/{rel}/` を足して（既にあればそのまま）、コミットする"
    )


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
    # 相対が求まらない（`--projects ""`・ワークスペースの外・別のドライブ）ときは、
    # `relpath` を呼ばずに指定の文字列をそのまま使う（`_projects` と同じ扱い）。
    where = _projects_rel(conf, root) or conf.projects or "(無し)"
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
