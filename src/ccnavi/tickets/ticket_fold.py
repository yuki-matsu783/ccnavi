"""同じ識別子のチケットのまとめ方。正とするツリーを決め、どれを正とするか決まらない形を数える。

集めたチケットを並べて見るだけで、ファイルは読まない（集めるのは `ticket.scan`）。
ticket から分けた。ticket を読まない。
"""

from __future__ import annotations

from ..infra import tree
from . import ticket_model


def fold(
    hits: list[ticket_model.Ticket], approved: list[ticket_model.Ticket] | None = None
) -> list[ticket_model.Ticket]:
    """同じ識別子のチケットを、正とするツリーの側にまとめる。

    子のワークツリーは親のブランチから切るので、親の `wip/proposals/` がそのまま
    入っている。正とするのは親のツリー（親自身なら自分のツリー）の側。そこに 1 つ
    あればそれが正で、残りはほかのツリー上のチケット。

    親のツリーが無ければ元ツリー（ワークスペースルート。プロジェクトのチケットなら
    そのプロジェクト）の側を採る。ワークツリーは片付ければ消えるが、元ツリーは消えない。
    親のワークツリーを作る前と、合流して片付けた後がこの形で、ここで行き先を決めないと、
    片付けただけのチケットが「複数の場所にある」になり、状態の操作が止まる。

    ただし、元ツリーより先の置き場に在るチケットが 1 つでもあれば採らない。 元ツリーを
    正としてよい根拠は「他のツリー上のチケットは合流の結果で、同じか手前の状態」であって、合流
    していないワークツリーで先に進んだチケット（親のツリーで閉じ、子のツリーが取り込んだ形）
    があるときは成り立たない。そこで元ツリーを採ると、閉じた子をもう一度閉じ、リスクの
    記録を別の差分で書き直す。決めずに残し、ユーザに合流させる。

    どちらも持っていなければ全部残る。残りが 2 つ以上になったら、どれを優先するか
    決まらない（検証が「複数の場所にある」と言う状態）。

    リポジトリをまたいだ衝突はまとめない。識別子はユーザが選ぶ短い連番なので、プロジェクトが
    独立に振ればぶつかる。それは同じチケットではなく違うチケットなので、どちらかを正と
    すると、もう片方が気づかないうちに消えて `--lint` の「複数のリポジトリにある」も出なくなる。

    `approved` は同じ識別子の承認済みチケット（作業中・レビュー待ち・閉じた）の全部。
    在れば、正とするツリーは承認済みチケットだけで同じ順・同じ条件で決め
    （`authority`。承認済みチケットの側の `approval._authoritative` と同じ関数）、そのツリーに
    在る提案だけを残す。そこに提案が無ければ何も残らない。承認で正とするツリーの `todo/` が
    消えたあと、承認の前に切ったワークツリーに残った `todo/` の提案を承認待ちや改版と読まない
    ため。提案だけで正とする側を決めると、承認済みチケットが元ツリーにしか無いのに親の名前の
    ワークツリーに古い `todo/` が残る形で、古い側が正になる。改版は正とするツリーの `todo/` に
    置くので、改版は残る。

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
        if at_home:
            return at_home
        at_origin = [t for t in hits if t.tree == origin_tree(t)]
        if not at_origin or behind(at_origin, hits):
            return hits
        return at_origin
    where = authority(approved)
    if where is None:
        return hits
    return [t for t in hits if t.tree == where]


def authority(copies: list[ticket_model.Ticket]) -> str | None:
    """同じ識別子の承認済みチケット（作業中・レビュー待ち・閉じた）から、正とするツリーの名前を決める。

    親のツリー（親自身なら自分のツリー）→ 元ツリー（先へ進んだチケットが無いときだけ）の順。
    決まらないとき（どちらにも無い、元ツリーより先のチケットがある、リポジトリをまたぐ）は None。
    承認済みチケット（`approval._authoritative`）と、承認済みの識別子の提案（`fold`）が
    この 1 つの関数で決める。
    """
    if not copies or len({t.project for t in copies}) > 1:
        return None
    for t in copies:
        if t.tree == (t.parent or t.ticket):
            return t.tree
    at_origin = [t for t in copies if t.tree == origin_tree(t)]
    if not at_origin or behind(at_origin, copies):
        return None
    return origin_tree(at_origin[0])


def origin_tree(t: ticket_model.Ticket) -> str:
    """このチケットの元ツリーの名前。ワークスペースなら空、プロジェクトならその名前。

    ワークツリーの名前は識別子だが、元ツリーの名前はプロジェクトの名前（ワークスペース
    から切ったものなら空）。`tree.Tree.name` と同じ表記で並ぶ。
    """
    return t.project or tree.MAIN


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


def behind(some: list[ticket_model.Ticket], hits: list[ticket_model.Ticket]) -> bool:
    """`some` より先の置き場に在るチケットが `hits` にあるか。"""
    return max(progress(t.state) for t in hits) > max(progress(t.state) for t in some)


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


def collisions(hits: list[ticket_model.Ticket]) -> list[ticket_model.Ticket]:
    """どれを正とするか決まらないチケットの全部。決まっていれば空。

    まとめて 2 つ以上残り、かつその残りが `collided_states` に当たるときだけ入る。
    状態の操作が「複数の場所にある」で止まるのと、`--lint` が ERROR で言うのと、
    同じ条件（`lint_ticket._proposal_problems` も同じ関数を通る）。複数のツリーにあること自体は
    普通なので、まとめて 1 つに決まるものは数えない。
    """
    folded = fold(hits)
    if len(folded) > 1 and collided_states([t.state for t in folded]):
        return folded
    return []


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
