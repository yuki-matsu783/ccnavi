"""ワークツリー（git worktree）の特定。判定の鍵はファイルの行き先。

## なぜ行き先で決めるか

サブエージェントは親と作業ディレクトリを共有することがある。そこから子の
ワークツリーへ絶対パスで書いた呼び出しを `cwd` で判定すると、親のチケットで
判定されてしまう。行き先で決めれば、誰が書いてもその場所のチケットで判定され、
サブエージェントの起動の仕方が判定に影響しない。

参考にした運用はここを `cwd` で決めていて、そのために並行実施に踏み切れずにいた。
「隔離はされるが統制は効かない」という穴の実体がこれ。

## ワークツリーの確かめ方

`.claude/worktrees/<名前>/` の下にあるだけではワークツリーと呼ばない。
`.git` ファイルの `gitdir:` がワークスペースルートの `.git/worktrees/<名前>` を指し、そこの
`gitdir` ファイルが候補を指し返す、という相互参照が成り立つものだけを数える。
片方向だけだと、同じ名前のただのディレクトリをワークツリーと読み違える。

## 最長一致

ワークツリーはワークスペースルートの中にあるので、短い側（ワークスペースルート）に
先に割り当てると、`.claude/worktrees/x/src/a` がワークスペースルートの
`.claude/worktrees/x/src/a` として判定され、
x のチケットが判定に使われなくなる。候補のうち最も長く一致したものを採る。
"""

from __future__ import annotations

import os
import unicodedata
from dataclasses import dataclass

from . import pathcase

# ワークツリーの置き場。CLAUDE.md の運用と対になる。ワークスペースルートの中に置くのは、
# セッションの道具と権限がそこまで届くようにするため。
WORKTREES_DIR = os.path.join(".claude", "worktrees")

# ワークスペースルートの名前。空文字。チケットは持たない。
MAIN = ""

# 手元の状態（退避・退役の記録・ロック）をリポジトリごとに分ける名前の、ワークスペース自身の分。
SELF = "self"

# ツリーの種類。ワークスペースルート、プロジェクト（`projects/` の直下にある別の
# リポジトリ）、ワークツリー。プロジェクトはワークスペースの git には入らず、自分の git を持つ。
KIND_MAIN = "main"
KIND_PROJECT = "project"
KIND_WORKTREE = "worktree"


@dataclass
class Tree:
    """ツリー 1 つ。name が空ならワークスペースルート。

    project は、このツリーがどのプロジェクトのものか。ワークスペースなら空、
    プロジェクトならその名前、ワークツリーなら元リポジトリがプロジェクトなら
    その名前（ワークスペースから切ったワークツリーなら空）。
    """

    name: str
    root: str
    project: str = ""
    kind: str = KIND_MAIN

    @property
    def is_main(self) -> bool:
        """チケットを持たないツリーか。ワークスペースルートとプロジェクトがこれ。"""
        return self.kind != KIND_WORKTREE


def repo_key(project: str) -> str:
    """手元の状態を分ける名前。ワークスペース自身は `self`、プロジェクトはその名前。"""
    return project or SELF


def project_of_key(repo: str) -> str:
    """`repo_key` の名前から、プロジェクトの名前（ワークスペース自身なら空）。"""
    return "" if repo == SELF else repo


def main_tree(root: str) -> Tree:
    return Tree(MAIN, _resolved(root))


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
            found.append(Tree(name, _resolved(candidate), project=name, kind=KIND_PROJECT))
    return found


def worktrees(root: str, projects_dir: str = "") -> list[Tree]:
    """ワークスペースルートの下にある、実際のワークツリーの一覧。

    元リポジトリはワークスペースでもプロジェクトでもよい。

    読むのはファイルシステムだけで、git は起こさない。実行前チェックの中で
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
                Tree(nfc(name), _resolved(candidate), project=owner.project, kind=KIND_WORKTREE)
            )
    return found


@dataclass
class ProjectlessWorktree:
    """元のプロジェクトを失ったワークツリー。診断が名指しするためのもの。

    gitdir は `.git` ファイルに書かれたとおりの先（読めた分）、gitdir_exists はその先が在るか。
    """

    name: str
    path: str
    gitdir: str
    gitdir_exists: bool

    def said(self) -> str:
        """`--lint` と `--explain` で共通の文面。"""
        if self.gitdir_exists:
            why = f"{self.gitdir} は在るが、そこからこのワークツリーへ戻る参照が成り立たない"
        else:
            why = f"{self.gitdir} の先が見つからない"
        return (
            f"ワークツリー {self.name} は元のプロジェクトがありません（{why}）。"
            "このパスはワークスペースルートとして判定され、そのプロジェクトの deny が効いていない。"
            "ワークツリーを削除するか、プロジェクトを戻してください"
        )


def worktrees_without_project(root: str, projects_dir: str = "") -> list[ProjectlessWorktree]:
    """`.claude/worktrees/` の下で、ワークツリーの形（`.git` が `gitdir:` で始まるファイル）なのに
    元のプロジェクトを見つけられないもの。

    `worktrees()` は数えないので、中のパスはワークスペースルートの扱いになる。その穴を診断で
    名指しするための一覧で、判定には使わない。読むのはファイルシステムだけで、git は起こさない。
    実行前チェックの経路からは呼ばない（走査が期限を圧迫する）。
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
        if not os.path.isdir(candidate) or owner_of(candidate, owners) is not None:
            continue
        written = _written_gitdir(candidate)
        if written is None:
            continue
        target = written if os.path.isabs(written) else os.path.join(candidate, written)
        found.append(ProjectlessWorktree(nfc(name), candidate, written, os.path.exists(target)))
    return found


def _written_gitdir(candidate: str) -> str | None:
    """`.git` ファイルの `gitdir:` に書かれた先（書かれたとおり）。

    ファイルでない・読めない・形が違えば None。
    """
    gitfile = os.path.join(candidate, ".git")
    if not os.path.isfile(gitfile):
        return None
    try:
        with open(gitfile, encoding="utf-8") as f:
            line = f.read().strip()
    except (OSError, ValueError):
        return None
    if not line.startswith("gitdir:"):
        return None
    return line[len("gitdir:") :].strip()


def owner_of(candidate: str, owners: list[Tree]) -> Tree | None:
    """このワークツリーの元リポジトリ。ワークスペースかプロジェクトのどれかで、相互参照が成り立つもの。"""
    for owner in owners:
        if is_worktree_of(owner.root, candidate):
            return owner
    return None


def all_trees(root: str, projects_dir: str = "") -> list[Tree]:
    """ワークスペースルート、プロジェクト、ワークツリーの順。"""
    return [main_tree(root), *projects(projects_dir), *worktrees(root, projects_dir)]


def project_root(projects_dir: str, project: str) -> str:
    """この名前の git プロジェクトルート。無くても返す。空の名前はワークスペース（空文字）。"""
    return os.path.join(projects_dir, project) if project and projects_dir else ""


def is_worktree_of(root: str, candidate: str) -> bool:
    """candidate が root のワークツリーであることを、相互参照で確かめる。"""
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
    registry = _key(os.path.join(root, ".git", "worktrees"))
    if not _key(gitdir).startswith(registry + os.sep):
        return False

    try:
        with open(os.path.join(gitdir, "gitdir"), encoding="utf-8") as f:
            back = f.read().strip()
    except OSError:
        return False
    return _key(back) == _key(gitfile)


def tree_of(root: str, full: str, projects_dir: str = "") -> Tree | None:
    """このパスが属するツリー。ワークスペースルートの外なら None。

    候補はワークスペースルート、プロジェクト、ワークツリーの全部で、最長一致を採る。上限は
    ワークスペースルートで、外に行き先があれば判定を持たない。
    """
    if not full:
        return None
    target = _key(full)
    best: Tree | None = None
    for tree in all_trees(root, projects_dir):
        base = pathcase.fold(tree.root)
        inside = target == base or target.startswith(base + os.sep)
        if inside and (best is None or len(tree.root) > len(best.root)):
            best = tree
    return best


def parts_under(base: str, full: str) -> list[str] | None:
    """`full` が `base` の下（か同じ）なら、`base` からの残りの名前を並べて返す。外なら None。

    突き合わせは、どの機械でも大文字小文字を区別しない（`pathcase.fold`）。返す名前の表記は
    `full` のまま。小文字にしたパスから作ると、チケットが `README.md` と書いた範囲に
    `readme.md` を当てることになって、永久に当たらない。
    """
    base_parts = _resolved(base).rstrip(os.sep).split(os.sep)
    parts = _resolved(full).rstrip(os.sep).split(os.sep)
    if len(parts) < len(base_parts):
        return None
    for want, got in zip(base_parts, parts, strict=False):
        if pathcase.fold(want) != pathcase.fold(got):
            return None
    return parts[len(base_parts) :]


def relative(tree: Tree, full: str) -> str:
    """作業ツリーのルートからの相対。区切りは "/"。ルートそのものなら空文字。

    ルートの外なら `..` で辿る形を返す。
    """
    rest = parts_under(tree.root, full)
    if rest is not None:
        return "/".join(rest)
    return os.path.relpath(_resolved(full), tree.root).replace(os.sep, "/")


def git_dir(tree_root: str) -> str | None:
    """このツリーの git ディレクトリ。分からなければ None。ファイルだけを読む。

    `.git` がディレクトリならそれ、ファイル（`gitdir: <場所>`、相対ならツリーから）ならその場所。
    """
    dotgit = os.path.join(tree_root, ".git")
    if not os.path.isfile(dotgit):
        return dotgit if os.path.isdir(dotgit) else None
    try:
        with open(dotgit, encoding="utf-8") as f:
            line = f.read().strip()
    except (OSError, ValueError):
        return None
    if not line.startswith("gitdir:"):
        return None
    gitdir = line[len("gitdir:") :].strip()
    if not os.path.isabs(gitdir):
        gitdir = os.path.join(tree_root, gitdir)
    return gitdir


def head_text(tree_root: str) -> str | None:
    """このツリーの `HEAD` の中身（前後の空白を除く）。読めなければ None。"""
    gitdir = git_dir(tree_root)
    if gitdir is None:
        return None
    try:
        with open(os.path.join(gitdir, "HEAD"), encoding="utf-8") as f:
            return f.read().strip()
    except (OSError, ValueError):
        return None


def has_origin(repo_root: str) -> bool:
    """そのリポジトリに `origin` の remote があるか。設定ファイルだけを読む（git は走らせない）。"""
    gitdir = git_dir(repo_root)
    if gitdir is None:
        return False
    try:
        common = os.path.join(gitdir, "commondir")
        if os.path.exists(common):
            with open(common, encoding="utf-8") as f:
                gitdir = os.path.normpath(os.path.join(gitdir, f.read().strip()))
        with open(os.path.join(gitdir, "config"), encoding="utf-8") as f:
            return '[remote "origin"]' in f.read()
    except (OSError, ValueError):
        return False


def origin_repo(repo_root: str) -> str:
    """そのリポジトリの `origin` の URL が指す `owner/repo`（パスの最後の 2 段）。読めなければ空。

    設定ファイルだけを読む（git は走らせない）。GitLab の入れ子のグループは最後の 2 段に縮める
    （依頼文の URL から読む `Ref.repo` と同じ形）。
    """
    gitdir = git_dir(repo_root)
    if gitdir is None:
        return ""
    try:
        common = os.path.join(gitdir, "commondir")
        if os.path.exists(common):
            with open(common, encoding="utf-8") as f:
                gitdir = os.path.normpath(os.path.join(gitdir, f.read().strip()))
        with open(os.path.join(gitdir, "config"), encoding="utf-8") as f:
            lines = f.read().splitlines()
    except (OSError, ValueError):
        return ""
    in_origin = False
    for line in lines:
        text = line.strip()
        if text.startswith("["):
            in_origin = text.replace(" ", "") == '[remote"origin"]'
        elif in_origin and text.replace(" ", "").startswith("url="):
            return _repo_of_url(text.split("=", 1)[1].strip())
    return ""


def _repo_of_url(url: str) -> str:
    rest = url.split("://", 1)[1] if "://" in url else url
    # `git@host:group/proj`（ssh の短い形）は `:` の後ろが、`host/group/proj` は最初の `/` の後ろが
    # パス
    if "://" not in url and ":" in rest.split("/", 1)[0]:
        path = rest.split(":", 1)[1]
    else:
        path = rest.split("/", 1)[1] if "/" in rest else ""
    parts = [p for p in path.removesuffix("/").removesuffix(".git").split("/") if p]
    return "/".join(parts[-2:]) if len(parts) >= 2 else ""


def origin_head(repo_root: str) -> str:
    """そのリポジトリの `origin/HEAD` が指すブランチ名（`refs/remotes/origin/HEAD` の中身）。
    無ければ空。

    ファイルだけを読む。`ccnavi_default_branch` が `symbolic-ref`
    で読むものと同じ。リンクされたワークツリーでは、`refs/remotes/` は共通の git ディレクトリ
    （ワークツリーの git ディレクトリの `commondir`）にあるので、そちらを読む。
    """
    gitdir = git_dir(repo_root)
    if gitdir is None:
        return ""
    try:
        with open(os.path.join(gitdir, "commondir"), encoding="utf-8") as f:
            common = f.read().strip()
        if common:
            gitdir = (
                common if os.path.isabs(common) else os.path.normpath(os.path.join(gitdir, common))
            )
    except (OSError, ValueError):
        pass
    try:
        with open(os.path.join(gitdir, "refs", "remotes", "origin", "HEAD"), encoding="utf-8") as f:
            text = f.read().strip()
    except (OSError, ValueError):
        return ""
    prefix = "ref: refs/remotes/origin/"
    return text[len(prefix) :].strip() if text.startswith(prefix) else ""


def has_branch(repo_root: str, name: str) -> bool:
    """そのリポジトリに、手元のブランチか origin のブランチ `name` があるか。

    ファイルだけを読む（git は起こさない。承認画面を組む判定の中から呼ばれる）。見るのは git
    ディレクトリの `refs/heads/<名前>`・`refs/remotes/origin/<名前>` と `packed-refs` の行。
    `..` や空の段を含む名前は読まない（git ディレクトリの外を読まない）。
    """
    parts = name.split("/") if isinstance(name, str) else []
    if not parts or any(p in ("", ".", "..") for p in parts):
        return False
    gitdir = git_dir(repo_root)
    if gitdir is None:
        return False
    wanted = (f"refs/heads/{name}", f"refs/remotes/origin/{name}")
    for ref in wanted:
        if os.path.isfile(os.path.join(gitdir, *ref.split("/"))):
            return True
    try:
        with open(os.path.join(gitdir, "packed-refs"), encoding="utf-8") as f:
            for line in f:
                _, _, ref = line.strip().partition(" ")
                if ref in wanted:
                    return True
    except (OSError, ValueError):
        pass
    return False


# 途中の操作の目印（git ディレクトリの中の名前）。
BUSY_MARKS = (
    "MERGE_HEAD",
    "CHERRY_PICK_HEAD",
    "REVERT_HEAD",
    "rebase-merge",
    "rebase-apply",
    "sequencer",
)


def busy_of(tree_root: str) -> str:
    """このツリーで途中の操作（merge・cherry-pick・revert・rebase）があればその目印の名前。無ければ空。

    ファイルだけを見る。
    """
    gitdir = git_dir(tree_root)
    if gitdir is None:
        return ""
    for name in BUSY_MARKS:
        if os.path.lexists(os.path.join(gitdir, name)):
            return name
    return ""


def branch_of(tree_root: str) -> str | None:
    """このツリーの作業ツリーが今いるブランチの名前。分からなければ None。

    読むのはファイルだけで、git は起こさない（判定の中から呼ばれうる）。`.git` が
    ディレクトリならその `HEAD`、ファイル（`gitdir: <場所>`、相対ならツリーから）ならその
    場所の `HEAD`。`ref: refs/heads/<名前>` の形だけを名前として読み、切り離した HEAD
    （sha）・空・不正な中身・読めないものは None（呼び手はツリーの名前で代える）。
    """
    head = head_text(tree_root)
    if head is None:
        return None
    prefix = "ref: refs/heads/"
    name = head[len(prefix) :].strip() if head.startswith(prefix) else ""
    return name or None


def worktree_path(root: str, name: str) -> str:
    """この名前のワークツリーが置かれる場所。

    大文字小文字だけが違う名前は同じ場所として扱うので、在るときはディレクトリの実際の表記で返す。
    無いときは与えた名前のまま組む。
    """
    base = os.path.join(root, WORKTREES_DIR)
    try:
        existing = os.listdir(base)
    except OSError:
        existing = []
    folded = pathcase.fold(nfc(name))
    for entry in sorted(existing):
        if pathcase.fold(nfc(entry)) == folded:
            return os.path.join(base, entry)
    return os.path.join(base, name)


def nfc(name: str) -> str:
    """ディレクトリの名前を NFC にそろえる。

    識別子は NFC に限る。macOS の HFS+ は名前を NFD で返すので、日本語の識別子のワークツリーを
    識別子と同じ表記として引くためにそろえる（APFS と Windows・Linux は書いた表記のまま返す）。
    """
    return unicodedata.normalize("NFC", name)


def lookup(index: dict, name: str):
    """ワークツリーの名前で承認済みチケットを引く。大文字小文字はどの機械でも区別しない。

    綴りまで同じものがあればそれを返す（大小だけ違うキーが並んでいても誤って結び付けない）。
    """
    found = index.get(name)
    if found is not None:
        return found
    folded = pathcase.fold(name)
    for key, value in index.items():
        if pathcase.fold(key) == folded:
            return value
    return None


def _resolved(path: str) -> str:
    """行き着く先。表記の大文字小文字は元のまま。"""
    try:
        resolved = os.path.realpath(path)
    except OSError:
        resolved = os.path.abspath(path)
    return os.path.normpath(resolved)


def _key(path: str) -> str:
    """同じ場所かを比べるための鍵。大文字小文字はどの機械でも区別しない（`pathcase.fold`）。"""
    return pathcase.fold(_resolved(path))
