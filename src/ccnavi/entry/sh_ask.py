"""`.ccnavi/scripts/` の sh がチケットについて聞くこと。読むだけで、何も書かない。

sh はチケットの YAML も JSON も自分で読まない。読むのは実行ファイルで、sh には 1 行 1 項目
（`<鍵> <値>`。sh は jq を使わない）で返す。答えの頭の行が無ければ、sh は「実行ファイルが
この副命令を知らない（古い）か、答えられなかった」と読み、呼ぶ側が決めた向きに倒す
（止める側の検査なら止める側、ツリーを動かす側なら動かさない側）。

- `ccnavi tree parent <ツリー> <名前>`: そのツリーが、名前の親子のチケットの親のワークツリーか。
  置き場（承認済みの `doing/`・`done/`、提案の `todo/`・`review/`）に `ticket: <名前>` の
  親チケットか提案があれば `parent yes`。子チケット（`parent:` を持つ）は数えない。
  ファイルが在るのに frontmatter を読めなければ `parent unreadable`。
  `ready` の後は手元の退避（`logs/archive/<リポジトリ>/done/`）の親も数える
  （ツリーが `.claude/worktrees/` の下のときだけ）
- `ccnavi sync closed <P> <リポジトリ> <統合先> <ツリー>`: 親子のチケット P が統合先で閉じているか。
  `integration` は統合先（`refs/remotes/origin/<統合先>`）の `done/` に、識別子が P で子でなく、
  親のワークツリーの親チケットと同じ親と言える親チケットがあるか。同じ親かは
  `syncstate.same_parent`。手元の退避からは推定しない（マージ済みかは sh がホストに聞く）

git はローカルの読み取り（`show`）だけ。ネットワークには出ない。
"""

from __future__ import annotations

import os
import re
from typing import TextIO

from ..infra import fsio, gitcmd, pathcase, settings, tree
from ..tickets import approval, archive, syncstate, ticket_ids, ticket_model
from ..tickets import ticket as ticket_mod

# 答えの頭の行。
TREE_HEAD = "tree 1"
CLOSED_HEAD = "closed 1"

YES = "yes"
NO = "no"
UNREADABLE = "unreadable"

# 取り込み状態を分けるリポジトリの名前（`self` かプロジェクト名）。cli_ops と同じ形。
_REPO_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_GIT_TIMEOUT = 20.0


def _ident_ok(stderr: TextIO, verb: str, what: str, value: str) -> bool:
    if ticket_ids.is_valid_id(value) and ".." not in value:
        return True
    stderr.write(f"ccnavi: {verb} の{what} {value!r} は有効な識別子の形式ではありません。\n")
    return False


def _tree_of(root: str, conf: settings.Settings, path: str, name: str) -> tree.Tree:
    """sh が渡したツリーのパスを、名前を付けたワークツリーとして読む。

    登録されていないディレクトリでも、そのパスの置き場を見る（sh の前の見方と同じ）。
    プロジェクトは、ワークスペースかプロジェクトのワークツリーなら元リポジトリから決める。
    """
    full = os.path.abspath(path)
    owners = [tree.main_tree(root), *tree.projects(_projects_dir(root, conf))]
    owner = tree.owner_of(full, owners)
    return tree.Tree(name, full, project=owner.project if owner else "", kind=tree.KIND_WORKTREE)


def _projects_dir(root: str, conf: settings.Settings) -> str:
    if not conf.projects:
        return ""
    return conf.projects if os.path.isabs(conf.projects) else os.path.join(root, conf.projects)


def _under_worktrees(root: str, path: str) -> bool:
    base = pathcase.fold(os.path.normpath(os.path.join(root, tree.WORKTREES_DIR)))
    full = pathcase.fold(os.path.normpath(os.path.abspath(path)))
    return full.startswith(base + os.sep)


def parent_tree(
    stdout: TextIO, stderr: TextIO, root: str, conf: settings.Settings, path: str, name: str
) -> int:
    """`ccnavi tree parent <ツリー> <名前>`。頭に `tree 1`、続けて `parent yes|no|unreadable`。

    `unreadable` は、親チケットか提案の置き場に名前のファイルが在るのに frontmatter を読めず、
    親かどうかを決められなかったこと。止める側の検査は親として扱い、ツリーを動かす側は
    親でないとして扱う（呼ぶ側が決める）。
    """
    if not _ident_ok(stderr, "tree parent", "名前", name):
        return 1
    if not path or not os.path.isdir(path):
        stderr.write(f"ccnavi: tree parent のツリー {path!r} が見つかりません。\n")
        return 1
    work = _tree_of(root, conf, path, name)
    answer = _holds_parent(conf, work)
    # 手元の退避を見るのは `.claude/worktrees/<名前>` の形のときだけ（退避のリポジトリは
    # ワークツリーの元リポジトリで決まる）。
    if answer != YES and _under_worktrees(root, path):
        held = archive.archived_fields(root, work.project, name)
        if _is_parent(held, name):
            answer = YES
    stdout.write(TREE_HEAD + "\n")
    stdout.write(f"parent {answer}\n")
    return 0


def _holds_parent(conf: settings.Settings, work: tree.Tree) -> str:
    """そのツリーの置き場に `ticket: <名前>` の親の承認済みチケットか提案があるか。

    チケットとしての検査（範囲の欄など）は掛けず、frontmatter の `ticket` と `parent` だけを見る。
    """
    approved = settings.approved_dir(conf, work.root)
    proposals = os.path.join(work.root, conf.tickets.replace("/", os.sep))
    unreadable = False
    for path in (
        approval.copy_path(approved, work.name),
        approval.closed_path(approved, work.name),
        os.path.join(proposals, ticket_model.TODO, f"{work.name}.md"),
        os.path.join(proposals, ticket_model.REVIEW, f"{work.name}.md"),
    ):
        if not os.path.isfile(path):
            continue
        text = fsio.read_text(path)
        front, _, problems = (None, None, None) if text is None else ticket_mod._frontmatter(text)
        if not isinstance(front, dict) or problems:
            unreadable = True
            continue
        if str(front.get("ticket") or "").strip() == work.name and not front.get("parent"):
            return YES
    return UNREADABLE if unreadable else NO


def _home_copy(
    conf: settings.Settings, tree_root: str, ident: str
) -> tuple[bool, syncstate.DoneCopy | None]:
    """親のワークツリーの承認済みの親チケット（`doing/` か `done/`）が在るかと、その欄。"""
    base = settings.approved_dir(conf, tree_root)
    for state in (ticket_model.DOING, ticket_model.DONE):
        text = fsio.read_text(os.path.join(base, state, f"{ident}.md"))
        if text is not None:
            return True, syncstate._copy_fields(text)
    return False, None


def _is_parent(copy: syncstate.DoneCopy | None, ident: str) -> bool:
    return copy is not None and copy.ticket == ident and not copy.parent


def sync_closed(
    stdout: TextIO,
    stderr: TextIO,
    root: str,
    conf: settings.Settings,
    family: str,
    repo: str,
    integration: str,
    path: str,
) -> int:
    """`ccnavi sync closed <P> <リポジトリ> <統合先> <ツリー>`。頭に `closed 1`、続けて 2 行。"""
    if not _ident_ok(stderr, "sync closed", "識別子", family):
        return 1
    if not _REPO_NAME.fullmatch(repo) or ".." in repo:
        stderr.write(
            f"ccnavi: sync closed のリポジトリ {repo!r} は有効な識別子の形式ではありません。\n"
        )
        return 1
    check = gitcmd.run(root, ["check-ref-format", "--branch", integration], _GIT_TIMEOUT)
    if not integration or integration.startswith("-") or not check.ok:
        stderr.write(f"ccnavi: sync closed の統合先 {integration!r} はブランチ名の形ではない\n")
        return 1
    if not path or not os.path.isdir(path):
        stderr.write(f"ccnavi: sync closed のツリー {path!r} が見つかりません。\n")
        return 1
    have_mine, mine = _home_copy(conf, path, family)

    approved_rel = fsio.slashed(conf.approved).strip("/")
    closed = NO
    shown = gitcmd.run(
        path,
        ["show", f"refs/remotes/origin/{integration}:{approved_rel}/done/{family}.md"],
        _GIT_TIMEOUT,
    )
    if shown.ok:
        theirs = syncstate._copy_fields(shown.out)
        if have_mine and _is_parent(theirs, family) and syncstate.same_parent(mine, theirs):
            closed = YES

    stdout.write(CLOSED_HEAD + "\n")
    stdout.write(f"integration {closed}\n")
    return 0
