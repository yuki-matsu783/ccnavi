"""`.ccnavi/scripts/` の sh がチケットについて聞くこと。読むだけで、何も書かない。

sh はチケットの YAML も JSON も自分で読まない。読むのは実行ファイルで、sh には 1 行 1 項目
（`<キー> <値>`。sh は jq を使わない）で返す。答えの頭の行が無ければ、sh は「実行ファイルが
このサブコマンドに答えられなかった」と読み、呼ぶ側が決めた向きに倒す
（止める側の検査なら止める側、ツリーを動かす側なら動かさない側）。

- `ccnavi tree parent <ツリー> <名前>`: そのツリーが、名前の親子のチケットの親のワークツリーか。
  置き場（承認済みの `doing/`・`done/`、提案の `todo/`・`review/`）に `ticket: <名前>` の
  親チケットか提案があれば `parent yes`。子チケット（`parent:` を持つ）は数えない。
  ファイルが在るのに frontmatter を読めなければ `parent unreadable`。
  `ready` の後は手元の退避（`logs/archive/<リポジトリ>/done/`）の親も数える
  （ツリーが `.claude/worktrees/` の下のときだけ）。
  続けて、push の関門（sh）が「初回の push か、消えた親ブランチの再作成か」を見分ける材料を返す。
  `approved`（承認済みの親チケットだけを見て、在るか）・`requested`（`.requested` の
  マーカーが在るか）・`archived`（手元の退避に親が在るか）・`started`（承認済みの親チケットの
  `started_at` が空でないか。着手は push してから完了するので、着手済みなら一度は push した）・
  `mr`（親チケットの `mr:` 欄）。
  答えられなかったときはこの 5 行を返さない（sh は `approved` が無ければ `unknown` と読む）

- `ccnavi tree bound <ツリー> <名前>`: その名前の直接作業のワークツリーが、チケットに結び付くか
  （`ccnavi-git.sh integrate` が断る条件。Stop の促しと同じ判定）。`bound yes|no` に続けて、
  結び付くなら `why <理由>`。判定は `ops_stop_integrate.bound_reason`。

- `ccnavi tree base-bound <メインのチェックアウト> <ブランチ>`: 直接作業の取り込み先のブランチが、
  チケットに結び付くか（`ccnavi-worktree.sh` が切る前に断る条件）。`bound yes|no|unknown` に続けて、
  yes なら `why <理由>`。識別子の衝突はワークスペース全体、結び付きは取り込み先のリポジトリだけを
  見る（`base_bound.base_bound`）。unknown（決められない）のときも、呼ぶ側は断る。

- `ccnavi tree guarded <メインのチェックアウト> <パス>…`: パスが自己防衛の守る対象にヒットするか。
  `guarded yes|no` に続けて、ヒットするパスごとに `path <パス>`。`ccnavi-git.sh integrate` が、
  確定をターミナルから打つよう案内するかどうかを決めるために聞く。

- `ccnavi tree handoff-pending <ツリー> [<起点>]`: 取り込んでいない下書き（`wip/handoff/`）の件数。
  `pending <件数>` に続けて、1 件ごとに `path <パス>`。`<起点>`（コミット）を渡すと、置き場の
  ファイルを起点の木の元のパス（置き場を除いた相対パス。大文字小文字・NFC/NFD の違いは同じもの）と
  突き合わせ、元があって中身が違う写しごとに `foreign <パス>`、元が無い下書き（新規のメモ）ごとに
  `note <パス>` を足す。起点の木を読めなければ `tree 1` を出さずに失敗する。

git はローカルの読み取りだけ。ネットワークには出ない。
"""

from __future__ import annotations

import os
import re
from typing import TextIO

from ..infra import fsio, pathcase, settings, tree
from ..policy import ruleload, selfguard_targets
from ..tickets import (
    approval,
    approval_marks,
    archive,
    base_bound,
    donecopy,
    handoff,
    ops_stop_integrate,
    ticket_ids,
    ticket_model,
)
from ..tickets import ticket as ticket_mod

# 答えの頭の行。
TREE_HEAD = "tree 1"

YES = "yes"
NO = "no"
UNREADABLE = "unreadable"

_REQUESTED = re.compile(r"[0-9]+\.requested")


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
    """`ccnavi tree parent <ツリー> <名前>`。頭に `tree 1`、続けて 6 行。

    ```
    parent yes|no|unreadable
    approved yes|no|unreadable
    requested yes|no
    archived yes|no
    started yes|no
    mr <番号>|-
    ```

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
    approved, mr, started = _approved_parent(conf, work)
    # 手元の退避を見るのは `.claude/worktrees/<名前>` の形のときだけ（退避のリポジトリは
    # ワークツリーの元リポジトリで決まる）。
    archived = NO
    if _under_worktrees(root, path):
        held = archive.archived_fields(root, work.project, name)
        if _is_parent(held, name):
            archived = YES
            if answer != YES:
                answer = YES
            if not mr:
                mr = _archived_mr(root, work.project, name)
    stdout.write(TREE_HEAD + "\n")
    stdout.write(f"parent {answer}\n")
    stdout.write(f"approved {approved}\n")
    stdout.write(f"requested {_requested(conf, work)}\n")
    stdout.write(f"archived {archived}\n")
    stdout.write(f"started {started}\n")
    stdout.write(f"mr {mr or '-'}\n")
    return 0


def bound_tree(
    stdout: TextIO, stderr: TextIO, root: str, conf: settings.Settings, path: str, name: str
) -> int:
    """`ccnavi tree bound <ツリー> <名前>`。

    頭に `tree 1`、続けて `bound yes|no`、結び付くなら `why <理由>`。

    `ccnavi-git.sh integrate` が、直接作業のワークツリーを取り込んでよいかを決めるために聞く。
    sh はチケットの置き場を自分で読まない。
    """
    if not _ident_ok(stderr, "tree bound", "名前", name):
        return 1
    if not path or not os.path.isdir(path):
        stderr.write(f"ccnavi: tree bound のツリー {path!r} が見つかりません。\n")
        return 1
    why = ops_stop_integrate.bound_reason(root, conf, _tree_of(root, conf, path, name))
    stdout.write(TREE_HEAD + "\n")
    stdout.write(f"bound {YES if why else NO}\n")
    if why:
        stdout.write(f"why {why}\n")
    return 0


def base_bound_tree(
    stdout: TextIO, stderr: TextIO, root: str, conf: settings.Settings, path: str, branch: str
) -> int:
    """`ccnavi tree base-bound <メインのチェックアウト> <ブランチ>`。

    頭に `tree 1`、続けて `bound yes|no|unknown`、yes なら `why <理由>`。
    """
    if not branch or "\n" in branch or "\x00" in branch:
        stderr.write(
            f"ccnavi: tree base-bound のブランチ {branch!r} は有効な名前ではありません。\n"
        )
        return 1
    if not path or not os.path.isdir(path):
        stderr.write(f"ccnavi: tree base-bound のツリー {path!r} が見つかりません。\n")
        return 1
    answer, why = base_bound.base_bound(root, conf, path, branch)
    stdout.write(TREE_HEAD + "\n")
    stdout.write(f"bound {answer}\n")
    if why:
        stdout.write(f"why {why}\n")
    return 0


def guarded_tree(
    stdout: TextIO,
    stderr: TextIO,
    root: str,
    conf: settings.Settings,
    path: str,
    rels: list[str],
) -> int:
    """`ccnavi tree guarded <メインのチェックアウト> <パス>…`。

    頭に `tree 1`、続けて `guarded yes|no`、守る対象にヒットするパスごとに `path <パス>`。
    パスはメインのチェックアウトからの相対。判定は `selfguard_targets.targets`（自己防衛が
    守る設定ファイル・実行ファイル）と、行き着く先まで解いたパスを大文字小文字を畳んで比べる。
    """
    if not path or not os.path.isdir(path):
        stderr.write(f"ccnavi: tree guarded のツリー {path!r} が見つかりません。\n")
        return 1
    found = selfguard_targets.targets(
        root, conf.rules, conf.bin, ruleload.layer_files(conf, root), conf.projects
    )
    guarded = {pathcase.fold(os.path.realpath(t.path)) for t in found}
    hits = [
        rel
        for rel in rels
        if rel
        and pathcase.fold(os.path.realpath(os.path.join(path, rel.replace("/", os.sep)))) in guarded
    ]
    stdout.write(TREE_HEAD + "\n")
    stdout.write(f"guarded {YES if hits else NO}\n")
    for rel in hits:
        stdout.write(f"path {rel}\n")
    return 0


def handoff_pending_tree(stdout: TextIO, stderr: TextIO, path: str, start: str = "") -> int:
    """`ccnavi tree handoff-pending <ツリー> [<起点>]`。

    頭に `tree 1`、続けて `pending <件数>` と、取り込んでいない下書きごとに `path <パス>`。
    `<起点>` があれば、続けて `foreign <パス>`（元があり中身が違う写し）と `note <パス>`
    （元の無い下書き）。`ccnavi-git.sh integrate` が、下書き（`wip/handoff/`）の取り込み漏れと、
    取り込み結果に含まれない下書きを見分ける条件に使う。
    """
    if not path or not os.path.isdir(path):
        stderr.write(f"ccnavi: tree handoff-pending のツリー {path!r} が見つかりません。\n")
        return 1
    root = os.path.abspath(path)
    left = handoff.pending(root)
    surveyed = handoff.survey(root, start) if start else ([], [])
    if surveyed is None:
        stderr.write(f"ccnavi: tree handoff-pending の起点 {start!r} の木を読めません。\n")
        return 1
    stdout.write(TREE_HEAD + "\n")
    stdout.write(f"pending {len(left)}\n")
    for rel in left:
        stdout.write(f"path {rel}\n")
    for rel in surveyed[0]:
        stdout.write(f"foreign {rel}\n")
    for rel in surveyed[1]:
        stdout.write(f"note {rel}\n")
    return 0


def _front(text: str | None) -> dict | None:
    """チケットの frontmatter。読めなければ None（チケットとしての検査は掛けない）。"""
    if text is None:
        return None
    front, _, problems = ticket_mod._frontmatter(text)
    return front if isinstance(front, dict) and not problems else None


def _mr_of(front: dict) -> str:
    """親チケットの `mr:` 欄。正の整数だけ数える（無い・形が違うなら空）。"""
    value = front.get("mr")
    if isinstance(value, bool):
        return ""
    text = str(value).strip() if value is not None else ""
    return text if text.isdigit() and int(text) > 0 else ""


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
        front = _front(fsio.read_text(path))
        if front is None:
            unreadable = True
            continue
        if str(front.get("ticket") or "").strip() == work.name and not front.get("parent"):
            return YES
    return UNREADABLE if unreadable else NO


def _approved_parent(conf: settings.Settings, work: tree.Tree) -> tuple[str, str, str]:
    """承認済みの置き場（`doing/`・`done/`）に親チケットが在るかと、その `mr:` 欄、着手済みか。

    提案（`todo/`・`review/`）は数えない。push の関門が見るのは、承認された親だけ。
    着手済みは `started_at` が空でないこと（`ticket start` は write-and-push で push
    してから完了するので、
    着手済みなら一度は push した）。親チケットが無い・読めないときは `no`。
    """
    approved = settings.approved_dir(conf, work.root)
    unreadable = False
    for path in (
        approval.copy_path(approved, work.name),
        approval.closed_path(approved, work.name),
    ):
        if not os.path.isfile(path):
            continue
        front = _front(fsio.read_text(path))
        if front is None:
            unreadable = True
            continue
        if str(front.get("ticket") or "").strip() == work.name and not front.get("parent"):
            started = YES if str(front.get("started_at") or "").strip() else NO
            return YES, _mr_of(front), started
    return (UNREADABLE if unreadable else NO), "", NO


def _requested(conf: settings.Settings, work: tree.Tree) -> str:
    """`approved/phases/<名前>/` に `<N>.requested` が 1 つ以上あるか。"""
    directory = os.path.join(
        settings.approved_dir(conf, work.root), approval_marks.PHASES_DIR, work.name
    )
    try:
        names = os.listdir(directory)
    except OSError:
        return NO
    return YES if any(_REQUESTED.fullmatch(n) for n in names) else NO


def _archived_mr(root: str, project: str, ident: str) -> str:
    """手元の退避の親チケットの `mr:` 欄。無い・読めないなら空。"""
    # 大文字小文字だけが違う識別子の退避も同じものとして引く（`archived_fields` と同じ）。
    name = archive.archived_name(root, project, ident)
    data = (
        archive.archived_bytes(root, project, f"{ticket_model.DONE}/{name}.md")
        if name is not None
        else None
    )
    if data is None:
        return ""
    try:
        front = _front(data.decode("utf-8"))
    except UnicodeDecodeError:
        return ""
    return _mr_of(front) if front is not None else ""


def _is_parent(copy: donecopy.DoneCopy | None, ident: str) -> bool:
    return copy is not None and copy.ticket == ident and not copy.parent
