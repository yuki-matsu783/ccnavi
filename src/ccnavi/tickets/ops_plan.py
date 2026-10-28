"""親の `start` が見る計画の有無。`ops.start` から呼ぶ。

- 計画（`plan`）の無い親は `start` を止める（`plan_missing`）。子の提案や子があっても止める。
  フェーズごとのレビューと進捗管理は計画が持つので、計画の無い親はそれが効かないまま進んでしまう。
- 計画のある親で子のないフェーズが残るときは、`start` の成功時に案内を出す（`childless_notice`）。
  止めない。子の `start` は親が着手済みであることが条件（`ops_close.parent_not_started`）なので、
  子が無いことで親の `start` を止めると、子に着手できなくなる。
"""

from __future__ import annotations

from typing import TextIO

from ..infra import settings
from . import phase
from .approval import store as approval
from .model import ticket_model


def plan_missing(stderr: TextIO, found: ticket_model.Ticket) -> bool:
    """計画の無い親なら理由を書いて True。子と、計画のある親は False。"""
    if found.is_child or found.has_plan:
        return False
    where = (
        f"projects/{found.project}/.ccnavi/config/project/phases.yml"
        if found.project
        else ".ccnavi/config/project/phases.yml"
    )
    stderr.write(
        f"ccnavi: 親 {found.ticket} に plan が無いので着手できない（子や子の提案があっても同じ）。"
        f"{where} に最小のフェーズ定義（例: work を 1 つ）を書き、親に plan: を追加します。"
        "phases.yml はユーザが持つ設定なので、書くのはユーザ。"
        "承認済みの親に plan を追加するには、plan を追加した提案を改版として出し、"
        "ユーザに承認し直してもらう。"
        "書き方は `ccnavi-launcher.sh --docs --keyword 提案書式` と `--keyword phases` で引く。\n"
    )
    return True


def childless_phases(root: str, conf: settings.Settings, found: ticket_model.Ticket) -> list[str]:
    """子（承認済みと `todo/` の提案の両方）のないフェーズの見出し。計画の無い親は空。"""
    if found.is_child or not found.has_plan:
        return []
    proposed, _ = approval.scan_proposals(conf, root)
    drafted = {
        t.phase
        for t in proposed
        if t.parent == found.ticket and t.state == ticket_model.TODO and t.phase is not None
    }
    return [
        p.label
        for p in phase.phases_of(root, conf, found.ticket)
        if not p.tickets and p.number not in drafted
    ]


def childless_notice(root: str, conf: settings.Settings, found: ticket_model.Ticket) -> list[str]:
    """`start` の成功時に足す案内の行。子のないフェーズが無ければ空。"""
    labels = childless_phases(root, conf, found)
    if not labels:
        return []
    return [
        f"子のない（子の提案もありません）フェーズがある: {', '.join(labels)}",
        "次: フェーズごとに子チケットを作ってください（子の着手は親が着手済みであることが条件）",
        "子のないまま親で進めても、`finish` で「終わっていないフェーズがある」と止まります",
    ]
