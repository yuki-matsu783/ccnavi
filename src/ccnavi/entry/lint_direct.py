"""`--lint` のうち、直接作業（`ccnavi-worktree.sh` で切るワークツリー）の記録の検査。

見るのは、ワークスペース自身と各プロジェクトのメインのチェックアウトのgitの共通ディレクトリ
（`ccnavi-direct/`・`ccnavi-integrate/`・`ccnavi-integrate-done/`）と、`refs/ccnavi/direct/`、
一時ワークツリーの置き場（`logs/direct-tmp/`）。gitを起こすのはrefの一覧と、ブランチの有無だけ。

- info: 記録の無い直接作業のワークツリー（`--adopt` で引き取る）
- warn: 壊れた記録（版・取り込み先・起点・共通ディレクトリのどれかが欠ける）
- info: 有効な横で作業する記録があり、そのチェックアウトで自動復元が緩んでいること
- info: 作業ワークツリーもブランチも無い記録
- info: 残った一時ワークツリー・専用のref（t・c・k）・版 4 のmarker
- warn: 版を知らないmarker（版 4 でないもの）
"""

from __future__ import annotations

import os

from ..infra import directrec, gitcmd, pathcase, settings, tree
from ..policy.rules import SEVERITY_INFO, SEVERITY_WARN, Problem
from ..tickets import direct_candidates, pending_integrate
from ..tickets import ops_stop_integrate as osi

_NEED = ("base_branch", "snap_sha", "common_dir", "main_tree")


def _direct(conf: settings.Settings, root: str) -> list[Problem]:
    problems: list[Problem] = []
    for main in [tree.main_tree(root), *tree.projects(conf.projects)]:
        common = directrec.common_dir(main.root)
        if common is None:
            continue
        label = main.project or "self"
        problems.extend(_records(conf, root, main, common, label))
        problems.extend(_markers(root, main, common, label))
        problems.extend(_leftovers(root, main, common, label))
        problems.extend(_unrecorded(conf, root, main, common, label))
    return problems


def _file(path: str) -> dict[str, str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return directrec.parse(handle.read())
    except OSError:
        return {}


def _has_branch(top: str, name: str) -> bool:
    return gitcmd.run(top, ["show-ref", "--verify", "--quiet", f"refs/heads/{name}"]).ok


def _records(
    conf: settings.Settings, root: str, main: tree.Tree, common: str, label: str
) -> list[Problem]:
    """直接作業の記録（`ccnavi-direct/` の 1 件ごと）を見て、warn と info を返す。

    処理フロー:
    1. 記録の置き場（`common` の下の `directrec.DIRECT_DIR`）を一覧する。読めなければ何も返さない。
    2. 記録ごとに、版と必須の欄（`_NEED`）を見る。欠けていれば、壊れた記録として warn にし、
       取り込みは断られる旨と、直し方（消して切り直すか、`--adopt` で引き取る）を添える。
    3. 壊れていない記録は、ワークツリーもブランチも無ければ、片付け残りとして info にする。
    4. 手順 3 にヒットしなかった記録のうち、有効な記録（`directrec.records_of` に在る名前）は、
       横で作業する指定が有効だと info にする。

    戻り値は見つけた指摘の一覧（無ければ空）。`label` は指摘の場所の表記（`direct/<label>/…`）。
    """
    base = os.path.join(common, directrec.DIRECT_DIR)
    try:
        names = sorted(n for n in os.listdir(base) if not n.startswith("."))
    except OSError:
        return []
    valid = {r.name for r in directrec.records_of(root, main)}
    problems = []
    for name in names:
        path = os.path.join(base, name)
        if not os.path.isfile(path):
            continue
        fields = _file(path)
        where = f"(direct/{label}/{name})"
        missing = [k for k in _NEED if not fields.get(k)]
        if fields.get("version") != directrec.VERSION or missing:
            why = "版が 4 ではありません" if fields.get("version") != directrec.VERSION else ""
            if missing:
                why = (why + "、" if why else "") + f"{'・'.join(missing)} がありません"
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    where,
                    f"直接作業の記録（ccnavi-direct/{name}）が壊れています（{why}）。"
                    "取り込み先と起点を読めないので、integrate は断ります。"
                    "ユーザに確かめてから、記録を消して切り直すか、--adopt で引き取ります",
                )
            )
            continue
        worktree = tree.worktree_path(root, name)
        if not os.path.isdir(worktree) and not _has_branch(main.root, name):
            problems.append(
                Problem(
                    SEVERITY_INFO,
                    where,
                    f"記録（ccnavi-direct/{name}）はありますが、作業ワークツリーもブランチもありません。"
                    "取り込み済みの片付け残りか、手で消したものです。ユーザに確かめて、記録を消します",
                )
            )
        elif name in valid:
            problems.append(
                Problem(
                    SEVERITY_INFO,
                    where,
                    f"横で作業する指定が有効です。{main.root} の通常のソースの編集は、"
                    "実行後チェックで自動復元せず、出所不明の変更として報告します"
                    "（ccnavi の設定ファイルは戻します）。ワークツリーを消すと元の動きに戻ります",
                )
            )
    return problems


def _markers(root: str, main: tree.Tree, common: str, label: str) -> list[Problem]:
    base = os.path.join(common, osi._RECORD_DIR)
    try:
        names = sorted(n for n in os.listdir(base) if not n.startswith("."))
    except OSError:
        return []
    problems = []
    for name in names:
        kind = osi.marker_kind(base, name)
        where = f"(direct/{label}/{name})"
        if kind == "unknown":
            problems.append(
                Problem(
                    SEVERITY_WARN,
                    where,
                    f"取り込みの marker（ccnavi-integrate/{name}）の版を知りません。"
                    "integrate は断ります。版 4 でない記録は ccnavi が読めません。ユーザが統合先の"
                    "staged を git status で確かめ、不要なら git reset --merge で戻してから、"
                    "記録のディレクトリを手で消します",
                )
            )
        elif kind == "v4":
            problems.append(
                Problem(
                    SEVERITY_INFO,
                    where,
                    f"取り込みの途中の marker（ccnavi-integrate/{name}）が残っています。"
                    "integrate-done か integrate --abort で終わらせます",
                )
            )
    return problems


def _leftovers(root: str, main: tree.Tree, common: str, label: str) -> list[Problem]:
    problems = []
    key = pending_integrate.repo_key(root, main.root)
    tmp_base = os.path.join(root, "logs", "direct-tmp", key)
    try:
        for entry in sorted(os.listdir(tmp_base)):
            problems.append(
                Problem(
                    SEVERITY_INFO,
                    f"(direct/{label}/{entry})",
                    f"一時ワークツリー（{os.path.join(tmp_base, entry)}）が残っています。"
                    "integrate --abort か integrate-done が片付けます。中身を確かめずに消しません",
                )
            )
    except OSError:
        pass
    listed = gitcmd.run(main.root, ["for-each-ref", "--format=%(refname)", "refs/ccnavi/direct"])
    for ref in listed.out.splitlines() if listed.ok else []:
        parts = ref.split("/")
        if len(parts) >= 5 and parts[4] in ("t", "c", "k"):
            problems.append(
                Problem(
                    SEVERITY_INFO,
                    f"(direct/{label}/{parts[3]})",
                    f"取り込みの途中の ref（{ref}）が残っています。"
                    "integrate --abort か integrate-done が片付けます",
                )
            )
    return problems


def _unrecorded(
    conf: settings.Settings, root: str, main: tree.Tree, common: str, label: str
) -> list[Problem]:
    """記録の無い直接作業のワークツリー（info）。

    更新前に切ったものと、手の `worktree add` で切ったもの。
    """
    base = os.path.join(root, tree.WORKTREES_DIR)
    try:
        entries = sorted(os.listdir(base))
    except OSError:
        return []
    problems = []
    for name in entries:
        work = os.path.join(base, name)
        if not os.path.isdir(work) or os.path.islink(work):
            continue
        mine = directrec.common_dir(work)
        if mine is None or pathcase.fold(mine) != pathcase.fold(common):
            continue
        if not direct_candidates.integrable_name(name) or tree.branch_of(work) != name:
            continue
        if os.path.isfile(os.path.join(common, directrec.DIRECT_DIR, name)):
            continue
        here = tree.Tree(name, work, project=main.project, kind=tree.KIND_WORKTREE)
        if osi.bound_reason(root, conf, here):
            continue
        problems.append(
            Problem(
                SEVERITY_INFO,
                f"(direct/{label}/{name})",
                f".claude/worktrees/{name} に、直接作業の記録（ccnavi-direct）がありません。"
                "integrate は断ります。"
                "ユーザに取り込み先を確かめて、"
                f"ccnavi-worktree.sh {name} --adopt --base <取り込み先> で引き取ります",
            )
        )
    return problems
