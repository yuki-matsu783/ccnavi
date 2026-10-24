"""親子のチケットの立ち位置。

正とする側は、全部の親子のチケットで同じく親のブランチに固定する。手元では
`.claude/worktrees/<P>`（HEAD のブランチ名の `/` を `-` に置き換えたものが `P` になるツリー）で、
ほかのツリーのコピーでは代用しない。判定に使うのは、親のワークツリーの有無・HEAD とフォルダ名の
一致・途中の操作というファイルだけで、手元に残る取り込みの記録には頼らない
（別の PC でも同じ結果になる）。判定は git もネットワークも起こさない（`tree.py` の前提）。

- 親のワークツリーがない・HEAD のブランチ名がフォルダ名と合わない・途中の操作がある:
  決まらない。その親子のチケットの承認も状態の操作も止める
- それ以外で親のワークツリーがある: 親のブランチ上のチケットだけが正

親のブランチがリモートから消えたことは、push の関門が止める。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from ..infra import pathcase, settings, tree
from . import ticket_ids

# `Standing.kind`: 止める理由の種別。案内（`guidance`）は文面ではなくこれで分ける。
STOP_BUSY = "busy"  # 親のワークツリーに途中の操作がある
STOP_MISMATCH = "mismatch"  # HEAD のブランチ名を変換した識別子がフォルダ名と合わない
STOP_ABSENT = "absent"  # 親のワークツリーがない
STOP_AMBIGUOUS = "ambiguous"  # 同じ識別子の親が複数のリポジトリにある


@dataclass(frozen=True)
class Standing:
    """親子のチケットの立ち位置。

    `stop` は決まらないので止める理由（空なら止めない）。`kind` はその種別（`STOP_*`。
    止めないなら空）。`head` は合わないときの、ワークツリーの HEAD の生のブランチ名
    （読めなければ空）。
    `home` は親のワークツリー（`.claude/worktrees/<P>` で、HEAD のブランチ名を変換したものが
    フォルダ名と一致するもの）。
    `branch` は親のブランチ名。ワークツリーの HEAD から読んだもので、読めなければ空。
    """

    family: str
    repo: str
    home: tree.Tree | None = None
    stop: str = ""
    branch: str = ""
    kind: str = ""
    head: str = ""

    @property
    def branch_name(self) -> str:
        """文面に出す親のブランチ名。HEAD から読めなければ `<ブランチ名>`。"""
        return self.branch or "<ブランチ名>"


class Families:
    """1 回の判定の中で、親子のチケットの立ち位置を引く窓口。

    ワークツリーの一覧は 1 度だけ読む
    （hook のたびに何度も走る `scan` の中で、親子のチケットごとにワークツリーを並べ直さないため）。
    """

    def __init__(self, conf: settings.Settings, root: str):
        self.conf = conf
        self.root = root
        self._worktrees: list[tree.Tree] | None = None
        self._standings: dict[tuple[str, str], Standing] = {}

    def worktrees(self) -> list[tree.Tree]:
        if self._worktrees is None:
            self._worktrees = tree.worktrees(self.root, self.conf.projects)
        return self._worktrees

    def standing(self, family_id: str, project: str = "") -> Standing:
        """この親子のチケットの立ち位置（正とする側の規則）。"""
        key = (project or "", family_id)
        if key not in self._standings:
            self._standings[key] = self._standing(family_id, project or "")
        return self._standings[key]

    def standing_any(self, family_id: str, project: str | None = None) -> Standing:
        """リポジトリが分かれば `standing`。分からなければ、同じ名前の親のワークツリーのある
        リポジトリを全部探す。

        同じ識別子の親子のチケットが 2 つ以上のリポジトリにあれば、どれとも決めずに止める
        （ワークスペースのユーザの付けた名前 `web-i0012` と、プロジェクト web の issue 12 の
        親子のチケットなど）。どこにも無ければワークスペース自身として引く（親のワークツリーが
        無いので止まる）。
        """
        if project is not None:
            return self.standing(family_id, project)
        repos_found: list[str] = []
        for w in self.worktrees():
            if w.name == family_id and tree.repo_key(w.project) not in repos_found:
                repos_found.append(tree.repo_key(w.project))
        if not repos_found:
            return self.standing(family_id, "")
        if len(repos_found) == 1:
            return self.standing(family_id, tree.project_of_key(repos_found[0]))
        hits = [self.standing(family_id, tree.project_of_key(r)) for r in repos_found]
        where = ", ".join(repos_found)
        return Standing(
            family_id,
            hits[0].repo,
            stop=(
                f"親子のチケット {family_id} が複数のリポジトリ（{where}）にある。どれか決まらない"
            ),
            kind=STOP_AMBIGUOUS,
        )

    def home_tree(self, family_id: str, project: str) -> tree.Tree | None:
        """親のワークツリー。名前が親の識別子で、元が同じリポジトリで、
        HEAD のブランチ名を変換したものが名前と一致するもの。無ければ None。
        ファイルだけを読む。"""
        work = self._named_tree(family_id, project)
        if work is not None and head_branch(work.root, family_id):
            return work
        return None

    def branch(self, family_id: str, project: str = "") -> str:
        """親のブランチ名。親のワークツリー（`.claude/worktrees/<P>`）の HEAD から読む。
        読めない・変換した識別子が名前と合わないときは空。ファイルだけを読む。
        """
        home = self.home_tree(family_id, project)
        return head_branch(home.root, family_id) if home is not None else ""

    def branch_any(self, family_id: str, project: str | None = None) -> str:
        """リポジトリの分からない親子のチケットの親のブランチ名。

        立ち位置（`standing_any`）の親のブランチ名。決まらなければ空。
        """
        return self.standing_any(family_id, project).branch

    def _named_tree(self, family_id: str, project: str) -> tree.Tree | None:
        for work in self.worktrees():
            if pathcase.fold(work.name) == pathcase.fold(family_id) and work.project == project:
                return work
        return None

    def _standing(self, family_id: str, project: str) -> Standing:
        """家族の親のワークツリーの状態を読む。止まるときは `stop` に理由を入れる。

        処理フロー:
        1. `home_tree` で親のワークツリーが在れば、それを正として返す。
        2. 無ければ、同名のフォルダ（`_named_tree`）を探す。在って途中の操作（`tree.busy_of`）
        があれば STOP_BUSY。
        3. 同名のフォルダが在って途中の操作が無ければ、HEAD
        のブランチを変換した識別子が違うので STOP_MISMATCH。
        4. フォルダが無ければ STOP_ABSENT。ほかのツリーのコピーでは代用しない。
        """
        repo = tree.repo_key(project)
        branch = self.branch(family_id, project)
        home = self.home_tree(family_id, project)
        if home is not None:
            return Standing(family_id, repo, home, branch=branch)
        named = self._named_tree(family_id, project)
        busy = tree.busy_of(named.root) if named is not None else ""
        where = f"{tree.WORKTREES_DIR.replace(os.sep, '/')}/{family_id}"
        head = ""
        if busy:
            kind = STOP_BUSY
            stop = (
                f"親のワークツリー（{where}）に途中の操作（{busy}）がある。"
                "済ませるか取りやめるまで、どのチケットを正とするかが決まらない"
            )
        elif named is not None:
            kind = STOP_MISMATCH
            head = tree.branch_of(named.root) or ""
            stop = (
                f"親のワークツリー（{where}）の HEAD のブランチ名を変換した識別子が"
                f"フォルダ名（{family_id}）と合わない。"
                "どのチケットを正とするかが決まらない"
            )
        else:
            kind = STOP_ABSENT
            stop = (
                f"親のワークツリー（{where}）がありません。"
                "ほかのツリーのコピーでは代用しない（どのチケットを正とするかが決まらない）"
            )
        return Standing(family_id, repo, None, stop=stop, branch=branch, kind=kind, head=head)


def head_branch(tree_root: str, family_id: str) -> str:
    """そのツリーの HEAD が指すブランチ名。変換（`ticket_ids.branch_to_id`）した識別子が
    `family_id` と一致するときだけ返し、そうでなければ空。ファイルだけを読む。

    NFC にそろえるのはフォルダ名（`family_id`。macOS の HFS+ は名前を NFD で返す）の側だけで、
    HEAD のブランチ名はそろえない。NFD のブランチ名は使えない名前（`ticket_ids.branch_problem`）で、
    sh（`ccnavi_head_branch`）もバイト列のまま比べて一致としない。同じワークツリーで答えを揃える。
    """
    head = tree.branch_of(tree_root)
    if head and ticket_ids.branch_to_id(head) == tree.nfc(family_id):
        return head
    return ""


def standing(conf: settings.Settings, root: str, family_id: str, project: str = "") -> Standing:
    """この親子のチケットの立ち位置（正とする側の規則）。1 回だけ引くときの形。"""
    return Families(conf, root).standing(family_id, project)


def standing_any(
    conf: settings.Settings, root: str, family_id: str, project: str | None = None
) -> Standing:
    """リポジトリの分からない親子のチケットの立ち位置（`Families.standing_any`）。"""
    return Families(conf, root).standing_any(family_id, project)


def same_tree(a: str, b: str) -> bool:
    """同じツリーのルートか（表記を揃えて比べる）。"""
    return (
        bool(a)
        and bool(b)
        and pathcase.fold(os.path.normpath(a)) == pathcase.fold(os.path.normpath(b))
    )


def guidance(root: str, st: Standing) -> list[str]:
    """止めたときの解き方。1 行ずつ。"""
    git = settings.script_command(root, "ccnavi-git.sh")
    name = st.family
    branch = st.branch_name
    if st.kind == STOP_BUSY:
        return [
            "親のワークツリーの途中の操作（merge・rebase など）を済ませるか取りやめてから"
            "再実行してください"
        ]
    if st.kind == STOP_MISMATCH:
        now = f"（今の HEAD: {st.head}）" if st.head else "（HEAD がブランチを指していない）"
        return [
            f"親のワークツリー（.claude/worktrees/{name}）の HEAD を、ブランチ名の `/` を `-` に"
            f"置き換えると {name} になるブランチに戻してください{now}"
        ]
    if st.kind == STOP_AMBIGUOUS:
        return [
            f"同じ名前 {name} の親のワークツリーが複数のリポジトリにあります。"
            "片方の親子のチケットを片付けるか、識別子を変えて、1 つのリポジトリに絞ってください"
        ]
    return [
        f"親のワークツリーを切り直してください（'{git} fetch origin {branch}' のあと "
        f"'{git} worktree add .claude/worktrees/{name} {branch}'。ブランチ名の `/` を `-` に"
        "置き換えたものがフォルダ名になる）。"
        "捨てた親子のチケットなら、親のワークツリーを片付ければよい"
        "（push の関門の対象から外れる）",
    ]
