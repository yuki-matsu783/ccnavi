"""直接作業の取り込み先のブランチが、チケットに結び付くか（`ccnavi tree base-bound`）。

`ccnavi-worktree.sh` が、ユーザのチェックアウトのブランチへ直接作業を切ってよいかを
決めるために聞く。`ops_stop_integrate.bound_check`（ワークツリーの名前が結び付くか）とは材料が違う。

- 識別子の衝突: ワークスペース全体のチケット・提案に、`branch_to_id(ブランチ)` の名前のものがある
- 取り込み先との結び付き: **取り込み先のリポジトリだけ**を見る。そのリポジトリの承認済みチケット
  （`doing/`・`done/`）の `branch:` 欄か識別子がこのブランチと一致する、またはそのリポジトリの退避に
  同じ名前の親がある。ほかのリポジトリの同じ名前のブランチでは断らない

デフォルトブランチにチケットのファイルが残っているだけでは結び付くと読まない。
そのファイルは名前も `branch:` も、このブランチと一致しないからである。
置き場を読めない、frontmatter を読めないなど決められないときは `unknown` になり、
呼ぶ側は断る。
"""

from __future__ import annotations

import os

from ..infra import fsio, pathcase, settings, tree
from .model import archive, ticket_ids, ticket_model
from .model import ticket as ticket_mod
from .ops_stop_integrate import _named_file, _tree_roots

YES = "yes"
NO = "no"
UNKNOWN = "unknown"


def _same(a: str, b: str) -> bool:
    return pathcase.fold(tree.nfc(a)) == pathcase.fold(tree.nfc(b))


def _owner(root: str, conf: settings.Settings, main_root: str) -> tree.Tree | None:
    """ユーザのチェックアウトにヒットする、ワークスペースかプロジェクト。どれでもなければ None。"""
    projects_dir = ""
    if conf.projects:
        projects_dir = (
            conf.projects if os.path.isabs(conf.projects) else os.path.join(root, conf.projects)
        )
    want = pathcase.fold(os.path.realpath(main_root))
    for owner in (tree.main_tree(root), *tree.projects(projects_dir)):
        if pathcase.fold(os.path.realpath(owner.root)) == want:
            return owner
    return None


def _front_of(path: str) -> dict | None:
    text = fsio.read_text(path)
    if text is None:
        return None
    front, _, problems = ticket_mod._frontmatter(text)
    return front if isinstance(front, dict) and not problems else None


def base_bound(root: str, conf: settings.Settings, main_root: str, branch: str) -> tuple[str, str]:
    """`(yes|no|unknown, 理由)`。理由は yes のときだけ。

    処理フロー:
    1. 取り込み先のリポジトリを決める（`_owner`）。決まらなければ unknown
    2. 各ツリーの承認済みと提案のどこかに、識別子と同じ名前のファイルが在れば yes（`_named_file`）
    3. 取り込み先の退避（`archive.archived_fields`）に同じ名前の親が在れば yes
    4. 取り込み先の承認済み（doing・done）で、ブランチか識別子に結び付くものが在れば yes。
       読めないファイルが在れば unsure を立てる（`_front_of`）
    5. 見つからず unsure も無ければ no、unsure があれば unknown
    """
    ident = ticket_ids.branch_to_id(branch)
    owner = _owner(root, conf, main_root)
    if owner is None:
        return UNKNOWN, ""
    for base_root in _tree_roots(root, conf):
        approved = settings.approved_dir(conf, base_root)
        proposals = os.path.join(base_root, conf.tickets.replace("/", os.sep))
        for base, state in (
            (approved, ticket_model.DOING),
            (approved, ticket_model.DONE),
            (proposals, ticket_model.TODO),
            (proposals, ticket_model.REVIEW),
        ):
            found = _named_file(os.path.join(base, state), ident)
            if found:
                rel = os.path.relpath(found, root).replace(os.sep, "/")
                return YES, f"チケットか提案（{rel}）の名前がブランチの識別子と同じ"
    archived = archive.archived_fields(root, owner.project, ident)
    if archived is not None and not archived.parent:
        return YES, "取り込み先のリポジトリの退避（ready 済み）に同じ名前の親がある"
    approved = settings.approved_dir(conf, owner.root)
    unsure = False
    for state in (ticket_model.DOING, ticket_model.DONE):
        directory = os.path.join(approved, state)
        try:
            entries = sorted(os.listdir(directory))
        except FileNotFoundError:
            continue
        except OSError:
            unsure = True
            continue
        for entry in entries:
            if not entry.endswith(".md"):
                continue
            path = os.path.join(directory, entry)
            if not os.path.isfile(path):
                continue
            front = _front_of(path)
            if front is None:
                unsure = True
                continue
            tied = str(front.get("branch") or "").strip()
            if (tied and _same(tied, branch)) or _same(
                str(front.get("ticket") or "").strip(), ident
            ):
                rel = os.path.relpath(path, root).replace(os.sep, "/")
                return YES, f"取り込み先のリポジトリの承認済みチケット（{rel}）に結び付く"
    return (UNKNOWN, "") if unsure else (NO, "")
