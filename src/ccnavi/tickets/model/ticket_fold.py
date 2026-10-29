"""同じ識別子のチケットのまとめ方。正とするツリーを決め、どれを正とするか決まらない形を数える。

集めたチケットを並べて見る（集めるのは `ticket.scan`）。
ticket から分けた。ticket を読まない。
"""

from __future__ import annotations

from . import ticket_model


def fold(
    hits: list[ticket_model.Ticket], approved: list[ticket_model.Ticket] | None = None
) -> list[ticket_model.Ticket]:
    """同じ識別子のチケットを、正とするツリーの側にまとめる。

    正とするのは親のツリー（親自身なら自分のツリー）の側だけ。子のワークツリーは親のブランチから
    切るので、親の `wip/proposals/` がそのまま入っているが、子のワークツリーや元ツリー
    （ワークスペースルート・プロジェクトのルート）に残った古いコピーは正としない。親のツリーに
    あればそれが正で、残りはほかのツリー上のチケット（`--lint` の診断にだけ使う）。

    親のツリーに無ければ決めずに全部残す（親のワークツリーが無い・そこにチケットが無いと
    いう状態は、立ち位置の側が「親子のチケットが決まらない」として止める）。

    リポジトリをまたいだ衝突はまとめない。識別子はユーザが選ぶ短い連番なので、プロジェクトが
    独立に振ればぶつかる。それは同じチケットではなく違うチケットなので、どちらかを正と
    すると、もう片方が気づかないうちに消えて `--lint` の「複数のリポジトリにある」も出なくなる。

    `approved` は同じ識別子の承認済みチケット（作業中・レビュー待ち・閉じた）の全部。
    在れば、正とするツリーは承認済みチケットだけで決め
    （`authority`。承認済みチケットの側の `approval._authoritative` と同じ関数）、そのツリーに
    在る提案だけを残す。そこに提案が無ければ何も残らない。承認で正とするツリーの `todo/` が
    消えたあと、承認の前に切ったワークツリーに残った `todo/` の提案を承認待ちや改版と読まない
    ため。改版は正とするツリーの `todo/` に置くので、改版は残る。

    `approved` を渡さないと、承認済みの識別子でも提案だけでまとめる。 正とするツリーの
    外に残った古い提案が残るので、承認待ち・改版・ボードの提案の欄を決める呼び手は
    `approval.scan_proposals` を通す。
    """
    if approved and len({t.project for t in [*approved, *hits]}) > 1:
        return hits
    if not approved:
        if len({t.project for t in hits}) > 1:
            return hits
        home = hits[0].parent or hits[0].ticket
        at_home = [t for t in hits if t.tree == home]
        return at_home or hits
    where = authority(approved)
    if where is None:
        return hits
    return [t for t in hits if t.tree == where]


def authority(copies: list[ticket_model.Ticket]) -> str | None:
    """同じ識別子の承認済みチケット（作業中・レビュー待ち・閉じた）から、正とするツリーの名前を決める。

    親のツリー（親自身なら自分のツリー）だけを読む。そこに無い（親のワークツリーが無い、親の
    ワークツリーにチケットが無い、リポジトリをまたぐ）ときは None で、ほかのツリーのコピーで
    代用しない。承認済みチケット（`approval._authoritative`）と、承認済みの識別子の提案（`fold`）が
    この 1 つの関数で決める。
    """
    if not copies or len({t.project for t in copies}) > 1:
        return None
    for t in copies:
        if t.tree == (t.parent or t.ticket):
            return t.tree
    return None


def collided_states(states: list[str]) -> list[str]:
    """1 つのツリーの中で、どれを正とするか決まらない置き場のリスト。決まっていれば空。

    同じ識別子が 2 つの置き場に在るのは、動かす途中で止まった形跡（コピーできたが消せなかった）。
    ただし `todo/` は親の改版の途中なので、承認済みチケットと並んでいてよい。
    `--lint` の ERROR と、ボードの `scattered` が同じ数え方をするためにここに置く。
    """
    distinct = sorted(set(states))
    if len(distinct) > 1 and ticket_model.TODO not in distinct:
        return distinct
    if len(states) > 1 and len(distinct) < len(states):
        return states
    return []


def collisions(
    hits: list[ticket_model.Ticket], across_trees: bool = False
) -> list[ticket_model.Ticket]:
    """どれを正とするか決まらないチケットの全部。決まっていれば空。

    まとめて 2 つ以上残り、同じツリーの中で `collided_states` にヒットするときだけ入る
    （状態の操作が「複数の場所」で止まるのと、`--lint` が ERROR で言うのとで同じ条件。
    `lint_ticket._proposal_problems` も同じ関数を通る）。複数のツリーにあること自体は
    普通で、親のツリーが決めるので数えない。

    `across_trees` は親のツリーに無く、ほかのツリーのコピーだけが残る形も数える（ボードが
    候補を全部出すとき）。閉じたチケットだけがほかのツリーに残っているのは数えない
    （合流した親のコピーがワークツリーにも残る普通の形）。
    """
    folded = fold(hits)
    if len(folded) < 2:
        return []
    if len({t.tree for t in folded}) == 1:
        return folded if collided_states([t.state for t in folded]) else []
    if across_trees and any(progress(t.state) < progress(ticket_model.DONE) for t in folded):
        return folded
    return []


def progress(state: str) -> int:
    """置き場の進み具合。`todo` < `doing` < `review` < `done`。空は `doing` として読む。

    承認済みチケットは置き場を持たないことがある（`state` が空）。判定と同じく
    作業中として数える。
    """
    order = (ticket_model.TODO, ticket_model.DOING, ticket_model.REVIEW, ticket_model.DONE)
    state = state or ticket_model.DOING
    if state == ticket_model.CANCELLED:
        state = ticket_model.DONE  # 取り消しも閉じた側。置き場は `done/`
    return order.index(state) if state in order else 0


def by_ticket(found: list[ticket_model.Ticket]) -> dict[str, list[ticket_model.Ticket]]:
    """識別子ごとのチケットの全部。順序は見つけた順。"""
    grouped: dict[str, list[ticket_model.Ticket]] = {}
    for t in found:
        grouped.setdefault(t.ticket, []).append(t)
    return grouped


def dedupe(
    found: list[ticket_model.Ticket], approved: list[ticket_model.Ticket] | None = None
) -> list[ticket_model.Ticket]:
    """同じ識別子が複数のツリーにあるとき、正とするツリーの側だけを残す。

    `approved` は、まとめる前の承認済みチケットの全部。承認済みの識別子は、そのチケットで
    正とするツリーを決める（`fold`）。承認済みの無い識別子（新規の提案）は今までどおり。
    渡さないと、承認済みの識別子でも提案だけでまとめるので、正とするツリーの外に残った
    古い提案（承認の前に切ったワークツリーの `todo/` など）が残る。
    """
    settled = by_ticket(approved or [])
    kept: list[ticket_model.Ticket] = []
    for ticket_id, hits in by_ticket(found).items():
        kept.extend(fold(hits, settled.get(ticket_id)))
    return kept
