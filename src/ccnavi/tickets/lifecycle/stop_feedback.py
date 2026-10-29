"""Stop で、フィードバック計画が決まらないまま終えようとしたときの促し。

全体計画のレビューが済んだ親は、閉じる前にフィードバック計画が要る（指摘が無ければ
`feedback: []`、あれば対応のフェーズを並べた親の改版）。局面が「フィードバック計画待ち」
（`phase.stage`）のまま終えようとしたとき、1 回だけ止めて、次の一手を返す。

## 止める条件

cwd のツリーが、承認済みで着手済みの親子に結び付き、親が blocked でなく、親の局面が
「フィードバック計画待ち」のとき。止め方は `ops_stop_request` と同じ（`stop_hook_active`・
サブエージェント・state の置き場・実行中のサブエージェントを見る。見るのは呼び出し側）。

## 1 回だけ

キーは `feedback:<親>`、値は状態の要約（親・HEAD・改版を提案済みか）。同じ要約では 1 回だけ止め、
HEAD が進むか、未提案から提案済みに変わればまた止めてよい。記録は `nudged-<セッション>.json`
に置く。

## 提案済みか

親のワークツリーの提案の置き場（設定の `tickets` の `todo/`）に、親と同じ識別子の提案があり、
`feedback:` を持つこと。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from ...infra import fsio, settings, tree
from ...policy import texts
from ..approval import checks as approval_checks
from ..approval import marks as approval_marks
from ..approval import store as approval
from ..model import standing, ticket_model
from ..model import ticket as ticket_mod
from ..phase import gates as phase
from . import stop as ops_stop

# Stop でフィードバック計画を促すときの理由コード。止めるのは 1 回だけの促しで、
# 判定の deny ではない。
CODE_FEEDBACK_NUDGE = "NUDGE_TICKET_FEEDBACK"

# 親の局面（`phase.stage`）のうち、促す局面。
STAGE_WAITING = phase.STAGE_FEEDBACK_WAITING

_KEY_HEAD = "feedback:"


@dataclass
class Waiting:
    """促す相手。todo は提案の置き場の絶対パス、proposal は改版の提案のファイル（無ければ空）。"""

    parent: ticket_model.Ticket
    head: str
    todo: str
    proposal: str = ""

    @property
    def key(self) -> str:
        return f"{_KEY_HEAD}{self.parent.ticket}"

    @property
    def digest(self) -> str:
        return f"{self.parent.ticket}@{self.head}:{'proposed' if self.proposal else 'none'}"


def waiting_at_stop(
    root: str, conf: settings.Settings, cwd: str, raw: approval.Raw | None = None
) -> Waiting | None:
    """メインエージェントが終わろうとしたとき、フィードバック計画待ちで止まっているか。

    条件はモジュールの説明のとおり。置き場か git を読めなければ None（促しは保護ではないので、
    読めないときは今までどおり何も出さずに通す）。
    """
    here = tree.tree_of(root, cwd or os.getcwd(), conf.projects)
    if here is None or here.is_main:
        return None
    copies, _ = approval.scan(conf, root, raw=raw)
    index = approval_checks.by_id(copies)
    bound = tree.lookup(index, here.name)
    if bound is None or bound.blocked:
        return None
    parent = index.get(bound.parent) if bound.is_child else bound
    if parent is None or parent.is_child or parent.blocked or not parent.in_progress:
        return None
    # 親のワークツリー。決まらない（無い・HEAD が違う・止まっている）親は促しの対象にしない。
    standing_ = standing.standing_any(conf, root, parent.ticket, parent.project)
    if standing_.home is None or standing_.stop:
        return None
    home = settings.approved_dir(conf, standing_.home.root)
    if approval_marks.read_parent_mark(home, parent.ticket, approval_marks.PARENT_MARK_CLOSE_EARLY):
        return None
    if phase.stage(root, conf, parent, raw) != STAGE_WAITING:
        return None
    head = ops_stop.worktree_head_sha(here.root)
    if not head:
        return None
    todo = os.path.join(standing_.home.root, conf.tickets.replace("/", os.sep), ticket_model.TODO)
    return Waiting(parent, head, todo, _proposed(todo, parent.ticket))


def _proposed(todo: str, ident: str) -> str:
    """提案の置き場に、親と同じ識別子で `feedback:` を持つ提案があればそのファイル。無ければ空。"""
    try:
        names = sorted(os.listdir(todo))
    except OSError:
        return ""
    for name in names:
        if not name.endswith(".md"):
            continue
        path = os.path.join(todo, name)
        found, _ = ticket_mod.load(path)
        if found is not None and found.ticket == ident and found.feedback is not None:
            return path
    return ""


def nudged_before(state_dir: str, session: str, found: Waiting) -> bool:
    """このセッションで、同じ状態のまま促したことがあるか。促すのは 1 回だけ。"""
    data = fsio.read_dict(ops_stop.nudge_path(state_dir, session)) or {}
    return data.get(found.key) == found.digest


def remember_nudge(state_dir: str, session: str, found: Waiting) -> str:
    """促した (親, 状態) を記録する。書けなければ理由。git で共有する履歴には入れない。"""
    path = ops_stop.nudge_path(state_dir, session)
    data = fsio.read_dict(path) or {}
    data[found.key] = found.digest
    return fsio.write_json_atomic(path, dict(sorted(data.items())))


def feedback_nudge(root: str, found: Waiting) -> str:
    """Stop を止めて渡す文。改版が提案済みかで言うことを分ける。文は文面ファイルが持つ。"""
    p = found.parent
    values = {
        "parent": p.ticket,
        "stage": STAGE_WAITING,
        "proposal": found.proposal,
        "todo": found.todo,
        "user_stop_note": ops_stop.USER_STOP_NOTE,
    }
    said = texts.say(
        root,
        "NUDGE_TICKET_FEEDBACK",
        values,
        {"next": "proposed" if found.proposal else "drafting"},
    )
    return "\n".join([f"[ccnavi] {CODE_FEEDBACK_NUDGE} (parent: {p.ticket})", said])
