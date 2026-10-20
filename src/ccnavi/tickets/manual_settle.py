"""手で動かした子の判定。ユーザが GitHubの画面などで子を `review/` から `done/` へ動かしたことを、
レビュー完了の意思として読む。

ccnavi が子を動かす経路（confirm・decide・close-early・feedback-plan・chrome）は、動かした子に
`settled_by` を書く。`finished_to: review` の子が `done/` にあって `settled_by` を持たなければ、
置き場の移動を誰かが手でしたことになる。

判定に使うのは、親チケットの正本の置き場（承認済みチケットと `phases/<親>/`）と、その git の履歴
だけ。PC ごとの state は使わない。ネットワークには出ない（`origin/<親のブランチ>` は最後に取得した
状態）。確定そのもの（版つきで台帳に受け入れる）は `confirm` が行い、ここは「確定してよい形か」を
言うだけ。

- 子が 1 つだけ動いたとき: 子 X はレビュー完了、フェーズは未完了（PARTIAL）
- 移動が未コミット・未 push: 確定しない（UNSHARED）。エージェントは代わりに push しない
- 依頼のマーカーが無い: 確認待ち（NO_REQUEST）。request を案内する
- `finished_to` を持たない子（この変更より前に閉じた子）: 手動とはみなさない（ROUTE_UNKNOWN）
"""

from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass, field

from ..infra import gitcmd, settings, tree
from . import approval_marks, phase, ticket_model

# 手動の移動の判定の結果。
NONE = "none"  # 該当しない（レビューが要らない・済み・手で動かした子が無い）
READY = "ready"  # 確定してよい形（confirm が受け入れる）
PARTIAL = "partial"  # 動かしたのは一部の子だけ
UNSHARED = "unshared"  # 移動が未コミット・未 push
NO_REQUEST = "no-request"  # 依頼の記録が無い
ROUTE_UNKNOWN = "route-unknown"  # 経路が分からない子しか無い（finished_to が無い）
BROKEN = "broken"  # 読めない・重複していて確かめられない

# 移動のコミットの時刻と、ホストのスレッドの作成時刻の間に置く余裕（秒）。
# ホストはユーザが push した時刻を返さないので、余裕の内側か後に作られたスレッドは受け入れない。
MARGIN_SECONDS = 600

_REVIEW = "review"


@dataclass
class Manual:
    state: str = NONE
    # 手で動かした子（識別子）
    children: list[str] = field(default_factory=list)
    # 手で動かした子の移動コミットのうち、いちばん新しいコミットの時刻（epoch 秒）
    moved_at: float = 0.0
    # まだ動いていない子（PARTIAL）
    waiting: list[str] = field(default_factory=list)
    note: str = ""
    # 外から移動の時刻をもらう形（Chrome）で、まだ時刻が無いとき真。動かした子の done/ のパス
    # （ツリーの根からの絶対パス）を `paths` に入れる。呼び手は時刻を読んで `moved_at` を渡し直す
    need_time: bool = False
    paths: list[str] = field(default_factory=list)

    def line(self) -> str:
        """status・confirm・Stop が出す 1 行。出すものが無ければ空。"""
        who = "、".join(self.children)
        if self.state == READY:
            return (
                f"レビュー完了確認待ち: 子 {who} が手で done/ に動かされている。"
                "親が confirm を打つと確定する"
            )
        if self.state == PARTIAL:
            return (
                f"子 {who} はレビュー完了、フェーズは未完了"
                f"（まだ review/・doing/ に残る子: {'、'.join(self.waiting)}）"
            )
        if self.state == UNSHARED:
            return (
                f"移動が未共有: 子 {who} の done/ への移動が未コミットか未 push。"
                "ユーザの commit・push が必要（確定しない）"
            )
        if self.state == NO_REQUEST:
            return "確認待ち（依頼の記録が無い）: 先に request を打つ"
        if self.state == ROUTE_UNKNOWN:
            return (
                "レビュー完了確認待ち（経路が分からない）: 子が done/ に動いているが、"
                "動かした経路の記録が無い。confirm か decide で確かめる"
            )
        if self.state == BROKEN:
            return f"手動移動を確かめられない: {self.note}"
        return ""


def assess(
    root: str,
    conf: settings.Settings,
    parent: ticket_model.Ticket,
    ph: phase.Phase,
    phases: list[phase.Phase] | None = None,
    *,
    external: bool = False,
    moved_at: float | None = None,
) -> Manual:
    """このフェーズ（延期を引き受けた分を含む）の手動の移動を判定する。

    `external` は git を持たない呼び手（Chrome）。ホストの親のブランチを読んでいるので、共有済み
    と見なし、移動の時刻だけを外から受ける。`moved_at`（epoch 秒）が無ければ `need_time` で返し、
    読めなかった（0 以下）なら確かめられないものとして BROKEN で返す。
    """
    if approval_marks.MARK_REVIEWED in ph.marks or not ph.review_required:
        return Manual()
    if phases is None:
        phases = phase.phases_of(root, conf, parent.ticket)
    numbers = {*ph.covers, ph.number}
    group = [p for p in phases if p.number in numbers]
    children = [t for p in group for t in p.tickets]
    states = {t.ticket: p.states.get(t.ticket, "") for p in group for t in p.tickets}
    ids = [t.ticket for t in children]
    if len(ids) != len(set(ids)):
        return Manual(BROKEN, note="同じ識別子の子が重複している")
    done = [t for t in children if states[t.ticket] == ticket_model.DONE]
    reviewed = [t for t in done if t.finished_to == _REVIEW]
    unknown = [t for t in done if not t.finished_to and not t.settled_by]
    hand = [t for t in reviewed if not t.settled_by]
    # レビューを待つ側の子（review/・doing/）。取り消した子は数えない
    waiting = [
        t.ticket
        for t in children
        if states[t.ticket] in (ticket_model.REVIEW, ticket_model.DOING, ticket_model.TODO)
    ]
    if not hand:
        if unknown and not waiting and not reviewed:
            return Manual(ROUTE_UNKNOWN, [t.ticket for t in unknown])
        return Manual()
    names = [t.ticket for t in hand]
    if waiting:
        return Manual(PARTIAL, names, waiting=waiting)
    if ph.marks.get(approval_marks.MARK_REQUESTED) is None:
        return Manual(NO_REQUEST, names)
    if external:
        if moved_at is None:
            return Manual(READY, names, need_time=True, paths=[t.path for t in hand])
        if not math.isfinite(moved_at) or moved_at <= 0:
            return Manual(BROKEN, names, note="移動の時刻を読めない")
        # コミットの時刻は書いた側の時計で決まる。未来にずれていれば、今の時刻に抑える
        # （移動の後に付いた指摘を、移動の前のものとして受け入れないため）
        return Manual(READY, names, moved_at=min(moved_at, time.time()))
    shared = _shared(root, parent, hand)
    if isinstance(shared, str):
        return Manual(UNSHARED if shared == "unshared" else BROKEN, names, note=shared)
    return Manual(READY, names, moved_at=shared)


def _shared(root: str, parent: ticket_model.Ticket, hand: list[ticket_model.Ticket]):
    """動かした子の done/ のファイルが HEAD にコミット済みで、origin/<親のブランチ> に入っているか。

    共有されていれば移動コミットの時刻（いちばん新しいもの）、そうでなければ理由の文字列
    （`unshared` は未共有、それ以外は読めなかった理由）。
    """
    tree_root = tree.worktree_path(root, parent.ticket)
    branch = tree.branch_of(tree_root)
    if not branch:
        return "親のブランチを読めない"
    newest = 0.0
    for t in hand:
        rel = os.path.relpath(t.path, tree_root) if t.path else ""
        if not rel or rel.startswith(".."):
            return f"{t.ticket} の done/ のファイルが親のツリーの外にある"
        dirty = gitcmd.run(tree_root, ["status", "--porcelain", "--", rel])
        if not dirty.ok:
            return "git status を読めない"
        if dirty.out.strip():
            return "unshared"
        log = gitcmd.run(tree_root, ["log", "--diff-filter=A", "--format=%ct %H", "-1", "--", rel])
        if not log.ok:
            return "git log を読めない"
        fields = log.out.split()
        if len(fields) != 2:
            return "unshared"
        stamp, sha = fields
        contained = gitcmd.run(tree_root, ["merge-base", "--is-ancestor", sha, f"origin/{branch}"])
        if contained.code == 1:
            return "unshared"
        if not contained.ok:
            return "origin/ のブランチを読めない"
        newest = max(newest, float(stamp))
    return newest
