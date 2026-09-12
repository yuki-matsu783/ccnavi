"""作業ツリー（git worktree）の特定。判定の鍵はファイルの行き先。

## なぜ行き先で決めるか

サブエージェントは親と作業ディレクトリを共有することがある。そこから子の
作業ツリーへ絶対パスで書いた呼び出しを `cwd` で判定すると、親のチケットで
判定されてしまう。行き先で決めれば、誰が書いてもその場所のチケットで判定され、
サブエージェントの起動の仕方が判定に影響しない（REQ-TKT-01）。

参考にした運用はここを `cwd` で決めていて、そのために並列実施を発効できずにいた。
「隔離はされるが統制は効かない」という穴の実体がこれ。

## 作業ツリーの確かめ方

`.claude/worktrees/<名前>/` の下にあるだけでは作業ツリーと呼ばない。
`.git` ファイルの `gitdir:` が main の `.git/worktrees/<名前>` を指し、そこの
`gitdir` ファイルが候補を指し返す、という相互参照が成り立つものだけを数える。
片方向だけだと、同じ名前のただのディレクトリを作業ツリーと読み違える。

## 最長一致

作業ツリーは main の中にあるので、短い側（main）に先に畳むと、
`.claude/worktrees/x/src/a` が main の `.claude/worktrees/x/src/a` として判定され、
x のチケットが効かなくなる。候補のうち最も長く一致したものを採る。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# 作業ツリーの置き場。CLAUDE.md の運用と対になる。ワークスペースルートの中に置くのは、
# セッションの道具と権限がそこまで届くようにするため。
WORKTREES_DIR = os.path.join(".claude", "worktrees")

# main の名前。空文字。チケットは持たない。
MAIN = ""

# ツリーの種類（設計 §25.3）。ワークスペースルート、プロジェクト（`projects/` の直下にある別の
# リポジトリ）、作業ツリー。プロジェクトはワークスペースの git には入らず、自分の git を持つ。
KIND_MAIN = "main"
KIND_PROJECT = "project"
KIND_WORKTREE = "worktree"


@dataclass
class Tree:
    """ツリー 1 つ。name が空ならワークスペースルート。

    project は、このツリーがどのプロジェクトのものか。ワークスペースなら空、
    プロジェクトならその名前、作業ツリーなら切り元のプロジェクトの名前
    （ワークスペースから切った作業ツリーなら空）。
    """

    name: str
    root: str
    project: str = ""
    kind: str = KIND_MAIN

    @property
    def is_main(self) -> bool:
        """チケットを持たないツリーか。ワークスペースルートと git プロジェクトルートがこれ。"""
        return self.kind != KIND_WORKTREE


def main_tree(root: str) -> Tree:
    return Tree(MAIN, _canonical(root))


def projects(projects_dir: str) -> list[Tree]:
    """プロジェクトの一覧。置き場の直下で `.git` を持つディレクトリだけ。深さ 1。

    置き場を空にしたプロジェクトはプロジェクトを持たない。`.git` はディレクトリでも
    ファイルでもよい（プロジェクトが submodule として置かれた形も、自分の git を持つ）。
    """
    if not projects_dir:
        return []
    try:
        names = sorted(os.listdir(projects_dir))
    except OSError:
        return []
    found = []
    for name in names:
        candidate = os.path.join(projects_dir, name)
        if os.path.isdir(candidate) and os.path.exists(os.path.join(candidate, ".git")):
            found.append(Tree(name, _canonical(candidate), project=name, kind=KIND_PROJECT))
    return found


def worktrees(root: str, projects_dir: str = "") -> list[Tree]:
    """main の下にある、本物の作業ツリーの一覧。切り元はワークスペースでもプロジェクトでもよい。

    読むのはファイルシステムだけで、git は起こさない。実行前の判定の中で
    呼ばれるので、外部プロセスを起こす場所にはできない。
    """
    base = os.path.join(root, WORKTREES_DIR)
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return []
    owners = [main_tree(root), *projects(projects_dir)]
    found = []
    for name in names:
        candidate = os.path.join(base, name)
        if not os.path.isdir(candidate):
            continue
        owner = owner_of(candidate, owners)
        if owner is not None:
            found.append(
                Tree(name, _canonical(candidate), project=owner.project, kind=KIND_WORKTREE)
            )
    return found


def owner_of(candidate: str, owners: list[Tree]) -> Tree | None:
    """この作業ツリーの切り元。ワークスペースかプロジェクトのどれかで、相互参照が成り立つもの。"""
    for owner in owners:
        if is_worktree_of(owner.root, candidate):
            return owner
    return None


def all_trees(root: str, projects_dir: str = "") -> list[Tree]:
    """ワークスペースルート、プロジェクト、作業ツリーの順。"""
    return [main_tree(root), *projects(projects_dir), *worktrees(root, projects_dir)]


def project_root(projects_dir: str, project: str) -> str:
    """この名前の git プロジェクトルート。無くても返す。空の名前はワークスペース（空文字）。"""
    return os.path.join(projects_dir, project) if project and projects_dir else ""


def is_worktree_of(root: str, candidate: str) -> bool:
    """candidate が root の作業ツリーであることを、相互参照で確かめる。"""
    gitfile = os.path.join(candidate, ".git")
    if not os.path.isfile(gitfile):
        return False
    try:
        with open(gitfile, encoding="utf-8") as f:
            line = f.read().strip()
    except OSError:
        return False
    if not line.startswith("gitdir:"):
        return False
    gitdir = line[len("gitdir:") :].strip()
    if not os.path.isabs(gitdir):
        gitdir = os.path.join(candidate, gitdir)
    gitdir = _canonical(gitdir)

    registry = _canonical(os.path.join(root, ".git", "worktrees"))
    if not gitdir.startswith(registry + os.sep):
        return False

    try:
        with open(os.path.join(gitdir, "gitdir"), encoding="utf-8") as f:
            back = f.read().strip()
    except OSError:
        return False
    return _canonical(back) == _canonical(gitfile)


def tree_of(root: str, full: str, projects_dir: str = "") -> Tree | None:
    """このパスが属するツリー。ワークスペースルートの外なら None。

    候補はワークスペースルート、プロジェクト、作業ツリーの全部で、最長一致を採る。上限は
    ワークスペースルートで、外に行き先があれば判定を持たない。
    """
    if not full:
        return None
    target = _canonical(full)
    best: Tree | None = None
    for tree in all_trees(root, projects_dir):
        inside = target == tree.root or target.startswith(tree.root + os.sep)
        if inside and (best is None or len(tree.root) > len(best.root)):
            best = tree
    return best


def relative(tree: Tree, full: str) -> str:
    """作業ツリーのルートからの相対。区切りは "/"。ルートそのものなら空文字。

    綴りの大文字小文字は元のまま返す。normcase を掛けた綴りから作ると、
    区別しない機械（Windows）では全部が小文字になり、チケットが `README.md` と
    書いた範囲に `readme.md` を当てることになって、永久に当たらない。
    `os.path.relpath` は比較にだけ normcase を使い、返す綴りは元のままなので、
    根（normcase 済み）と突き合わせても大文字小文字は保たれる。
    """
    target = _canonical(full)
    if target == tree.root:
        return ""
    return os.path.relpath(_resolved(full), tree.root).replace(os.sep, "/")


def worktree_path(root: str, name: str) -> str:
    """この名前の作業ツリーが置かれるはずの場所。在るかどうかは見ない。"""
    return os.path.join(root, WORKTREES_DIR, name)


def exact_name(root: str, name: str) -> bool:
    """この名前の作業ツリーが、綴りの大文字小文字までそのままで在るか。

    大文字小文字を区別しない機械では `I0001-02` というディレクトリが `i0001-02` として
    開けてしまう。名前が識別子だと言う以上、綴りまで同じであることを求める。
    """
    try:
        return name in os.listdir(os.path.join(root, WORKTREES_DIR))
    except OSError:
        return False


# 大文字小文字を区別しない機械かどうか。承認済みチケットの索引を引くときに、作業ツリーの
# 名前の綴りが違っても同じ識別子として結び付けるのは、この機械だけ。
CASE_INSENSITIVE = os.path.normcase("A") == "a"


def lookup(index: dict, name: str):
    """作業ツリーの名前で承認済みチケットを引く。区別しない機械では綴りの違いを許す。"""
    found = index.get(name)
    if found is not None or not CASE_INSENSITIVE:
        return found
    for key, value in index.items():
        if key.lower() == name.lower():
            return value
    return None


def _resolved(path: str) -> str:
    """行き着く先。綴りの大文字小文字は元のまま。"""
    try:
        resolved = os.path.realpath(path)
    except OSError:
        resolved = os.path.abspath(path)
    return os.path.normpath(resolved)


def _canonical(path: str) -> str:
    """同じ場所が同じ綴りになる形。大文字小文字は区別しない機械のために normcase。"""
    return os.path.normcase(_resolved(path))
