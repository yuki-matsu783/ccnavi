"""Stop で、レビューの依頼まで進めずに終えようとしたときの促し。

チケット作業の決まった道（子の `finish`（親ブランチへ合流して push）→ すべての子が合流済みかの確認
→ ELI5 → `ccnavi-review.sh request`）の
途中で、エージェントが「進めてよいですか」と確認して終えてしまうのを、1 回だけ止めて次の一手を返す。
`ops_stop.unfinished_at_stop` は `finish` の打ち忘れ専用なので、ここは別の判定にしてある。

問いで終わった回も止める。
ユーザの判断が本当に要る問いなら、促しの文が「その問いを最後に書き直して終える」と言う。

## 止める条件

cwd のツリーが、承認済みの親（`doing/`、着手済み、信頼できる）に結び付いているときだけ。

- 子のツリーなら、その子が作業中（着手済みで終わっていない）
- 親のツリーなら、作業中の子が残っている。子が全部閉じて、レビューを要するフェーズに
  依頼（`requested` のマーカー）がまだ無い

`requested` が在る間（レビュー待ち）は止めない。変更要求（changes requested）のレビューが残る間も、
`reviewed` を置かないだけで `requested` は残るので、同じ扱いで止まらない。同じフェーズに子を足すと
`requested` が外れ、足した子が作業中になる。そのときは止めてよい。

止めない: ユーザが早めに閉じた親、レビューが要らないフェーズ（省略のマーカー）、延期したフェーズ。

実行中のサブエージェントが居る間（委任して待つ回）も止めない。これは呼び出し側（`events._request_nudge`）が
`subagent.running` で先に見て、ここへ来ない。そのため「1 回だけ」の記録も進まない。記録は
SubagentStart / SubagentStop が残す（`docs/21_design/tickets/subagents.md`）。

## 1 回だけ

キーは「親＋フェーズ」、値は状態の要約（次の一手の種類・残っている子・HEAD）。同じ要約では
1 回だけ止め、進めば（子を閉じる、合流する、コミットを足す）次に終えるときまた止めてよい。
記録は `finish` の促しと同じ `nudged-<セッション>.json` に、`request:` で始まるキーで置く
（チケットの識別子とは重ならない）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from ..infra import fsio, gitcmd, settings, tree
from ..policy import texts
from . import manual_settle, ops_close, ops_stop, phase
from .approval import checks as approval_checks
from .approval import marks as approval_marks
from .approval import store as approval
from .model import ticket_model

# Stop でレビューの依頼までの道を促すときの理由コード。止めるのは 1 回だけの促しで、
# 判定の deny ではない。
CODE_REQUEST_NUDGE = "NUDGE_TICKET_REQUEST"

# 次の一手の種類。
STEP_FINISH = "finish"
STEP_MERGE = "merge"
STEP_LATE_FIX = "late-fix"
STEP_ELI5 = "eli5"
STEP_REQUEST = "request"
# ユーザが子を手で done/ に動かした。親が confirm を打てば確定する。
STEP_CONFIRM = "confirm"

# 文面ファイルが場合分けで持つ段階（これ以外は依頼の段階として言う）。
_STEPS = (STEP_FINISH, STEP_MERGE, STEP_LATE_FIX, STEP_ELI5, STEP_REQUEST, STEP_CONFIRM)

# 記録のキーの頭。`finish` の促しのキー（チケットの識別子）と分ける。
_KEY_HEAD = "request:"


@dataclass
class Pending:
    """Stop で次の一手を促す相手。`children` は一手の相手（`finish` を打つ子、合流する子）。"""

    parent: ticket_model.Ticket
    number: int
    step: str
    worktree: str
    head: str
    children: list[str] = field(default_factory=list)
    title: str = ""

    @property
    def key(self) -> str:
        return f"{_KEY_HEAD}{self.parent.ticket}:{self.number}"

    @property
    def digest(self) -> str:
        """状態の要約。進めば変わる（子を閉じる、合流する、コミットを足す）。"""
        return f"{self.step}:{','.join(self.children)}@{self.head}"


def pending_at_stop(
    root: str, conf: settings.Settings, cwd: str, raw: approval.Raw | None = None
) -> Pending | None:
    """メインエージェントが終わろうとしたとき、レビューの依頼までの道の途中にいるか。

    条件はモジュールの説明のとおり。git か置き場を読めなければ None（促しは保護ではないので、
    読めないときは今までどおり何も出さずに通す）。`raw` は `ops_stop.unfinished_at_stop` と同じ。
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
    # 親のワークツリーが決まらない（無い・HEAD が違う・止まっている・同じ識別子が別のリポジトリに
    # もある）親は促しの対象にしない。ここで例外を抜けさせると、Stop の後続（統合の促しと
    # Stop のルール）まで飛ぶ。
    try:
        home = approval.home_dir(conf, root, parent.ticket, "", project=parent.project)
    except approval.NoParentWorktree:
        return None
    if approval_marks.read_parent_mark(home, parent.ticket, approval_marks.PARENT_MARK_CLOSE_EARLY):
        return None
    head = ops_stop.worktree_head_sha(here.root)
    if not head:
        return None
    # 作業中の子。子のツリーでは自分の子だけ（並行して動く別の子は別のセッションの作業）。
    if bound.is_child:
        doing = [bound] if bound.in_progress else []
    else:
        doing = [c for c in copies if c.parent == parent.ticket and c.in_progress and not c.blocked]
    if doing:
        doing.sort(key=lambda t: t.ticket)
        number = min((t.phase for t in doing if t.phase is not None), default=0)
        return Pending(parent, number, STEP_FINISH, here.root, head, [t.ticket for t in doing])
    if bound.is_child:
        return None
    phases = phase.phases_of(root, conf, parent.ticket, raw=raw)
    for ph in phases:
        # 終わっていて、レビューが要り、まだ済んでいないフェーズ。
        if not ph.gate_closed:
            continue
        if approval_marks.MARK_REQUESTED in ph.marks:
            # 依頼のあとにユーザが子を手で done/ に動かして共有した。confirm で確定する。
            manual = manual_settle.assess(root, conf, parent, ph, phases)
            if manual.state == manual_settle.READY:
                return Pending(
                    parent, ph.number, STEP_CONFIRM, here.root, head, manual.children, ph.title
                )
            continue
        step, children = _next_step(root, here.root, ph)
        return Pending(parent, ph.number, step, here.root, head, children, ph.title)
    return None


def _next_step(root: str, worktree: str, ph: phase.Phase) -> tuple[str, list[str]]:
    """依頼の前の道のどこにいるか。合流済みでない子があれば確認、無ければ ELI5、あれば依頼。

    合流するのは子の `finish` で、ここは「すべての子が合流済みか」を確かめるだけ。合流済みは、
    finish が書いた `merged_sha`（合流した子の先頭）が親の HEAD の祖先であること。
    合流済みでも、子のワークツリーが残る PC で finish の後に子のブランチへ積んだコミットが
    親に無ければ、合流されていない修正があると言う（`ops_close.unmerged_fix`）。
    どちらも依頼の前提の検査 `review._unmet` と同じ確認。
    """
    unmerged: list[str] = []
    late: list[str] = []
    for child in sorted(ph.tickets, key=lambda t: t.ticket):
        if ph.states.get(child.ticket) not in (ticket_model.REVIEW, ticket_model.DONE):
            continue
        merged = (
            bool(child.merged_sha)
            and gitcmd.run(
                worktree,
                ["merge-base", "--is-ancestor", child.merged_sha, "HEAD"],
                ops_close.TIMEOUT_SECONDS,
            ).ok
        )
        if not merged:
            unmerged.append(child.ticket)
        elif ops_close.unmerged_fix(root, worktree, child):
            late.append(child.ticket)
    if unmerged:
        return STEP_MERGE, unmerged
    if late:
        return STEP_LATE_FIX, late
    if not os.path.isfile(os.path.join(worktree, "wip", "eli5", f"phase-{ph.number}.html")):
        return STEP_ELI5, []
    return STEP_REQUEST, []


def nudged_before(state_dir: str, session: str, found: Pending) -> bool:
    """このセッションで、同じ状態のまま促したことがあるか。促すのは 1 回だけ。"""
    data = fsio.read_dict(ops_stop.nudge_path(state_dir, session)) or {}
    if data.get(found.key) == found.digest:
        return True
    # 子のツリーで `finish` の促し（`ops_stop.nudged_before`。キーは子の識別子、値は HEAD）を出した
    # のと同じ状態は、こちらでは促さない。同じ状態を 2 つの促しが 1 回ずつ止めないため。
    return (
        found.step == STEP_FINISH
        and len(found.children) == 1
        and data.get(found.children[0]) == found.head
    )


def remember_nudge(state_dir: str, session: str, found: Pending) -> str:
    """促した (親, フェーズ, 状態) を記録する。書けなければ理由。git で共有する履歴には入れない。"""
    path = ops_stop.nudge_path(state_dir, session)
    data = fsio.read_dict(path) or {}
    data[found.key] = found.digest
    return fsio.write_json_atomic(path, dict(sorted(data.items())))


def request_nudge(root: str, found: Pending) -> str:
    """Stop を止めて渡す文。決まった道の途中で、許可は要らないことと、次に打つコマンドを言う。

    処理フロー:
    1. `found.step` で、いまの段階を分ける（子の finish・confirm・合流の確認・合流後の修正・
       ELI5・依頼）。文は段階ごとに文面ファイルが持つ（`NUDGE_TICKET_REQUEST`）。
    2. 子の finish だけは、子ごとのコマンドを並べた値を組んで渡す。
    3. 許可は要らないことと、この案内が同じ状態では 1 回だけであることは、文面ファイルが添える。
    """
    p = found.parent
    n = found.number
    ticket_sh = settings.script_command(root, "ccnavi-ticket.sh")
    values = {
        "parent": p.ticket,
        "phase": str(n),
        "phase_name": f"フェーズ {n}" + (f"（{found.title}）" if found.title else ""),
        "children": ", ".join(found.children),
        "finish_cmds_q": "、".join(f"`{ticket_sh} finish {c}`" for c in found.children),
        "user_stop_note": ops_stop.USER_STOP_NOTE,
    }
    step = found.step if found.step in _STEPS else STEP_REQUEST
    case = step.replace("-", "_")
    said = texts.say(root, "NUDGE_TICKET_REQUEST", values, {"what": case, "next": case})
    return "\n".join([f"[ccnavi] {CODE_REQUEST_NUDGE} (parent: {p.ticket}, phase: {n})", said])
