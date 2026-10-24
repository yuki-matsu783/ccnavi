"""直接作業の記録（`ccnavi-direct/<名前>`）を読む。読むだけで、書かない。

記録は対象リポジトリの git の共通ディレクトリの `ccnavi-direct/<名前>` にある（書くのは
`ccnavi-worktree.sh`）。1 行 1 項目の `<キー> <値>` で、頭は `version 4`。

ここが答えるのは「横で作業する指定が、いま有効か」だけ。有効な記録は次をすべて満たす。

- `side_by_side yes`
- `main_tree` が、記録を置いたリポジトリのメインのチェックアウトと同じ場所
- 作業ワークツリー（`.claude/worktrees/<名前>`）が在り、そのリポジトリのワークツリーとして
  登録されていて、同じ名前のブランチを開いている
- 共通の前提チェック: 記録の `common_dir` と、メインのチェックアウトの git の共通ディレクトリの
  実パスが、大文字小文字を区別せず同じ

ワークツリーを消せば、記録が残っていても無効になる（元の動きに戻る）。git は実行せず、
ファイルだけを読む（実行後チェックの中から毎回呼ばれる）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from . import pathcase, tree

DIRECT_DIR = "ccnavi-direct"
VERSION = "4"


@dataclass
class Record:
    """有効と確かめた記録 1 つ。"""

    name: str
    main_root: str
    base_branch: str = ""


def parse(text: str) -> dict[str, str]:
    """`<キー> <値>` の行をマップにする。空行と形の違う行は飛ばす。同じキーは後が勝つ。"""
    out: dict[str, str] = {}
    for line in text.splitlines():
        key, _, value = line.strip().partition(" ")
        if key:
            out[key] = value.strip()
    return out


def common_dir(tree_root: str) -> str | None:
    """このツリーの git の共通ディレクトリの実パス。分からなければ None。ファイルだけを読む。"""
    gitdir = tree.git_dir(tree_root)
    if gitdir is None or not os.path.isdir(gitdir):
        return None
    pointer = os.path.join(gitdir, "commondir")
    if os.path.isfile(pointer):
        try:
            with open(pointer, encoding="utf-8") as f:
                rel = f.read().strip()
        except (OSError, ValueError):
            return None
        gitdir = rel if os.path.isabs(rel) else os.path.join(gitdir, rel)
    try:
        return os.path.normpath(os.path.realpath(gitdir))
    except OSError:
        return None


def _same(a: str, b: str) -> bool:
    return pathcase.fold(os.path.normpath(a)) == pathcase.fold(os.path.normpath(b))


def _valid(root: str, main: tree.Tree, common: str, name: str, fields: dict[str, str]) -> bool:
    if fields.get("version") != VERSION or fields.get("side_by_side") != "yes":
        return False
    if not fields.get("main_tree"):
        return False
    if not _same(os.path.realpath(fields["main_tree"]), os.path.realpath(main.root)):
        return False
    recorded = fields.get("common_dir", "")
    if not recorded:
        return False
    try:
        recorded = os.path.realpath(recorded)
    except OSError:
        return False
    if not _same(recorded, common):
        return False
    if fields.get("base_branch") is None:
        return False
    work = tree.worktree_path(root, name)
    if not os.path.isdir(work) or os.path.islink(work) or not tree.is_worktree_of(main.root, work):
        return False
    wt_common = common_dir(work)
    if wt_common is None or not _same(wt_common, common):
        return False
    branch = tree.branch_of(work)
    return bool(branch) and pathcase.fold(tree.nfc(branch)) == pathcase.fold(tree.nfc(name))


def records_of(root: str, main: tree.Tree) -> list[Record]:
    """このメインのチェックアウトの、有効な横で作業する記録。"""
    common = common_dir(main.root)
    if common is None:
        return []
    base = os.path.join(common, DIRECT_DIR)
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return []
    out = []
    for name in names:
        path = os.path.join(base, name)
        if not os.path.isfile(path) or name.startswith("."):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                fields = parse(f.read())
        except (OSError, ValueError):
            continue
        if _valid(root, main, common, name, fields):
            out.append(Record(name, main.root, fields.get("base_branch", "")))
    return out


def side_by_side_roots(root: str, projects_dir: str = "") -> list[tree.Tree]:
    """有効な記録があるメインのチェックアウト（ワークスペース自身かプロジェクト）。"""
    mains = [tree.main_tree(root), *tree.projects(projects_dir)]
    return [m for m in mains if records_of(root, m)]
