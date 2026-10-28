"""承認の照合の材料と、改版の書き込み（承認済みチケットの計画の差し替え）。

承認の対象の指紋（`proposals_digest`）と、承認で書く本文（改版のコピー）を組む。
agree から分けた。agree を読まない。
"""

from __future__ import annotations

import hashlib

from ..infra import fsio
from ..policy import rules
from . import agree_candidates, approval, approval_marks
from .model import ticket as ticket_mod
from .model import ticket_model


def proposals_digest(batch: list[agree_candidates.Candidate]) -> str:
    """承認の対象の指紋。提案の識別子の一覧と、各提案ファイルの元のバイト列全体の SHA-256。

    値は 16 進（小文字）。

    ボード・ターミナル・Chrome の承認は、見せたときの指紋を承認の直前に取り直して比べる。
    解析した後の値では比べず、読んだバイト列そのものを使う。BOM、改行、本文だけの
    書き換えも拾うため。判定が読んだ中身（承認済みチケットやマーカーなど）と、承認で書く中身は
    入れない。承認の可否は、承認の直前に最新の状態から計算し直す。

    部分をそのままつながず、識別子と中身のハッシュを件数と並べて、そのリストのハッシュを取る。
    区切りの文字でつなぐと、その文字が中身に出たときにつなぎ目をずらせるため。

    バイト列は、解析したときに読んだもの（`Ticket.source`）を使う。ここで読み直すと、照合した
    中身と承認の対象に組んだ中身が別の読みになり、その間に書き換えられると見せていない中身が通る。
    """
    lines = []
    for cand in sorted(batch, key=lambda c: c.ticket.ticket):
        raw = cand.ticket.source
        if raw is None:
            raw = fsio.read_bytes(cand.ticket.path)
        lines.append(
            f"{cand.ticket.ticket}\n{'-' if raw is None else hashlib.sha256(raw).hexdigest()}"
        )
    lines.insert(0, str(len(lines)))
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def revise_copy(
    approved_dir: str,
    current: ticket_model.Ticket,
    revised: ticket_model.Ticket,
) -> str:
    """承認済みチケットの計画と範囲を差し替える。

    本文はそのまま（差し替えるのは計画・`phases:`・範囲。`revised_front`）。古い範囲は
    ccnavi の側には残らず、git の履歴だけに残る。改版の時刻はチケットに書かない。
    先行関係は書かない（承認済みチケットの計画から都度計算する）。
    """
    current.raw = revised_front(current, revised)
    failed = approval_marks.write_ticket(
        approval.copy_path(approved_dir, current.ticket), ticket_mod.render(current)
    )
    return failed


def revised_front(current: ticket_model.Ticket, revised: ticket_model.Ticket) -> dict:
    """改版で書く frontmatter。承認済みチケットの計画・`phases:`・範囲を差し替えたもの。

    `current` は変更せず、`raw` をコピーしたマップを返す。
    承認画面の「`phases:` の差分」も、この結果と同じものから組むので、
    画面に出した中身と書く中身が食い違わない。ステップの `after` も書く（`PlanItem.as_raw`）。
    `phases:`（計画が使う定義のコピー）は提案の値に差し替える。差し替えないと
    改版で足したステップ（フィードバック計画の立案は必ず改版）が `phases:`
    に無い定義名を使うことになり、承認したとたんに親と子が全部止まる。提案に `phases:`
    が無ければ外す。
    """
    front = dict(current.raw)
    # 範囲は提案の値に差し替える。提案に無い項は外す（全部外れた範囲は、提案の検査が止める）。
    for section in rules.SECTIONS:
        if section in revised.raw:
            front[section] = revised.raw[section]
        else:
            front.pop(section, None)
    front["plan"] = [item.as_raw() for item in revised.plan]
    if revised.feedback is not None:
        front["feedback"] = [item.as_raw() for item in revised.feedback]
    if ticket_mod.PHASES_KEY in revised.raw:
        front[ticket_mod.PHASES_KEY] = revised.raw[ticket_mod.PHASES_KEY]
    else:
        front.pop(ticket_mod.PHASES_KEY, None)
    return front
