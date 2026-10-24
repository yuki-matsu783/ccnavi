"""`--lint` のうち、置き場の検査。プロジェクトの置き場が正しい形か、
走査されないチケットの置き場が残っていないかを見る。
"""

from __future__ import annotations

import os

from ..infra import gitcmd, gitstate, settings, tree
from ..policy.rules import SEVERITY_ERROR, SEVERITY_WARN, Problem

# `projects/` がワークスペースの git の索引に載っているときの `(projects)` の warn の先頭の句。
# ぶつかり（通常のファイル）と載せ忘れ（gitlink だけ）で共通にする。VS Code 拡張
# （extensions/vscode/ccnavi-board の src/webview/projects/text.ts の isTrackedProjectsDir）が
# この句で始まる苦情を見て `.gitignore` のボタンと「無視されていない」の帯を出さない。
# `tests/`（test_lint_places.py・test_setup.py）と導入スクリプト（scripts/ccnavi-setup.sh）も
# 同じ句を持つ。
# 載せ忘れは先頭の句の直後が `（入れ子のリポジトリとして` で、`tests/` がそれで見分ける。
# 変えるならそちらも直す。
_TRACKED_LEAD = "`{rel}/` はワークスペースの git が追跡している"
# 索引の mode のうち gitlink（入れ子のリポジトリを 1 つの版として載せたもの）。
_GITLINK_MODE = "160000"


def _projects(conf: settings.Settings, root: str) -> list[Problem]:
    """プロジェクトの置き場が正しい形か。

    置き場が無いのは不備ではない。あるなら、ワークスペースの git で無視されていること、
    予約名（`common` / `self`、表記違いも含む）を使っていないこと、プロジェクトの中に
    ワークツリーが無いことを見る。層の中身は
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
    # 末尾の `/` が無くても `/projects/` のようなディレクトリ向けの行にヒットする。
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
                    "大文字小文字の違いは問わない）。ここに置いた宣言は 1 件も使われておらず、"
                    "このプロジェクトを行き先にするパスを持つツール（Read / Grep / Glob / Write / "
                    "Edit / NotebookEdit）は共通レイヤーだけで判定している。"
                    "プロジェクトを別の名前に変えてください",
                )
            )
        problems.extend(_misplaced_worktrees(p, where))
    return problems


def _misplaced_worktrees(project: tree.Tree, where: str) -> list[Problem]:
    """プロジェクトの中の `.claude/worktrees/` にあるワークツリーを名指しする（warn）。

    ワークツリーはワークスペースの `.claude/worktrees/` に置く。プロジェクトの中のものは
    承認の走査（`tree.worktrees`）の対象外で、そこに書いた提案は「承認待ちに無い」になる。
    ワークスペースの ccnavi が古く、プロジェクトの `ccnavi-common.sh` のコピーをルートと取り違えて
    着手すると、この形になる。検出だけを行う。
    """
    base = os.path.join(project.root, ".claude", "worktrees")
    try:
        names = sorted(n for n in os.listdir(base) if os.path.isdir(os.path.join(base, n)))
    except OSError:
        return []
    if not names:
        return []
    return [
        Problem(
            SEVERITY_WARN,
            where,
            f"プロジェクトの中にワークツリーがある（{', '.join(names)}）。ワークツリーは"
            "ワークスペースの `.claude/worktrees/` に置く。ここのものは承認の走査の対象外で、"
            "書いた提案は承認待ちに並ばない。ワークスペースの ccnavi が古い"
            "（`projects/<名前>` のコピーをルートと取り違える）可能性があるので、"
            "`scripts/ccnavi-setup.sh --force` で配り直し、"
            "このワークツリーはユーザに伝えて片付ける",
        )
    ]


def _projectless_worktrees(conf: settings.Settings, root: str) -> list[Problem]:
    """元のプロジェクトが無いワークツリーを名指しする（warn）。判定と列挙は変えない。"""
    return [
        Problem(SEVERITY_WARN, f"(worktrees/{w.name})", w.said())
        for w in tree.worktrees_without_project(root, conf.projects)
    ]


def _projects_rel(conf: settings.Settings, root: str) -> str:
    """プロジェクトの置き場の、ワークスペースルートからの相対（"/" 区切り）。

    置き場は既定の `projects` に固定。診断のフラグで空やワークスペースの外を
    指されたときは空を返す（ワークスペースの git の話ではなくなる）。
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
    git が無い・失敗したときは None（何も言わない）。

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
    """置き場が索引に載っているなら、その苦情の文面。載っていなければ None。

    - 通常のファイルが 1 本以上ある: ぶつかり。ワークスペース自身のソースに `projects/` が
      あるので、`.gitignore` に足せという案内は誤り（ソースが追跡から外れる）。改名を案内する。
      gitlink も載っていれば 1 文足して名指しする
    - gitlink だけ: 載せ忘れ。`.gitignore` に入れる前に `git add -A` した人の索引の形で、
      改名は要らない。索引から外して無視に入れる 2 手を案内する。`.gitignore` に既に
      `/projects/` があっても、索引に載っている限り追跡は外れないので言う
    - `.gitmodules` で意図して置いたサブモジュールも mode だけで見るので載せ忘れになる

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
            f"ccnavi でプロジェクトを置かないなら、このままでも動く。そのときは `{rel}/` の下に "
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
        f"`.gitignore` に `/{rel}/` を追加しても追跡は外れない。"
        f"直すには、ワークスペースで `git rm -r --cached {rel}` を実行し"
        f"（ファイルは消えない。まだコミットしていなければ `git reset -- {rel}`）、"
        f"`.gitignore` に `/{rel}/` を追加して（既にあればそのまま）、コミットする"
    )


def _ticket_places(conf: settings.Settings, root: str) -> list[Problem]:
    """走査されないチケットの置き場が残っていないか。

    提案の置き場はどのツリーでも同じ相対（`wip/proposals/`）で、プロジェクト向けはその
    プロジェクトのツリーに置く。ワークスペースの
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
