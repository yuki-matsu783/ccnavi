"""承認済みチケットの構造の検査。親子・統合先・先行・プロジェクトの欄を、判定の側で当てる。

提案を手で `doing/` へ動かす経路は承認の画面を通らないので、承認のときにしか当たらなかった
構造の検査はここで判定の側から当てる（`blocking_problems`）。親子の取り込み状態と統合先、
先行チケットの状態、プロジェクトの欄の検査を置く。approval から分けた。approval は読まない。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace

from ..infra import settings, tree
from ..policy import rules
from . import approval_marks, archive, syncstate, ticket_ids, ticket_model

# 前の版の承認が記録（`ccnavi_approved`）に必ず書いていた欄。続きの子も `source_tree` と
# `source_path` を空で書いていた。
_RECORD_KEYS = ("approved_at", "source_tree", "source_path")


def has_record(t: ticket_model.Ticket) -> bool:
    """前の版の承認が書いた記録（`ccnavi_approved`）を持つ古い承認済みチケットか。

    古い形として扱うと、取り下げは中身の一致を見ずに記録の欄で決まり、`workflow:` の欄も
    待ち方として読まれる。`ccnavi_approved: {}` のような書きかけの記録で古い形を装えないよう、
    前の版が必ず書いた欄が揃い、`approved_at` が空でないときだけ古い形とみなす。提案に
    記録の欄があれば `--agree` と `--lint` が error にする
    （`agree_candidates.record_field_problems`）。
    """
    meta = t.raw.get(ticket_model.APPROVAL_KEY)
    if not isinstance(meta, dict) or not all(k in meta for k in _RECORD_KEYS):
        return False
    return bool(str(meta.get("approved_at") or "").strip())


def mark_imported(
    conf: settings.Settings,
    root: str,
    kept: list[ticket_model.Ticket],
    fams: syncstate.Families | None = None,
) -> None:
    """取り込み済みの親子のチケットに、信頼できない理由を付ける。

    取り込み済みの親子のチケットとは、取り込み状態のある親子のチケットのこと。

    本物とするのは親のブランチ `P`（手元では `.claude/worktrees/<P>` で HEAD が `P` を指すツリー）の
    上のチケットだけ。次のチケットは読むが信頼しない（`blocked`。判定は範囲を使わずに止める）。

    - 親子のチケットが決まらない（取り込み状態が `gone`・`blocked`・壊れている、
      `present` なのに親のワークツリーが無い）
    - 親子のチケットが閉じている（統合先の `done/` を本物とする）
    - 親のワークツリーの外にしか無いチケット（元ツリーに未コミットで残ったチケットなど）

    **チケットのリストは変えない（落とさない）。
    ** 落とすと「在る」ことで止まっていたもの（承認待ちの
    重複、開いた子のある親を閉じない）が通るようになる。理由を足すだけなので、取り込み状態の無い親子のチケットと、
    取り込み状態があってもチケットが親のワークツリーにだけある親子のチケットでは、答えは前と同じ。すでに理由（`mark_blocked`）が
    あれば、理由を連ねる（前の理由を消さない）。
    """
    fams = fams or syncstate.Families(conf, root)
    if not fams.active:
        return
    for t in kept:
        st = family_standing(conf, root, t, fams)
        if not st.imported:
            continue
        why = st.stop
        if not why and st.home is not None and not syncstate.same_tree(t.tree_root, st.home.root):
            why = outside_reason(st, t)
        if why and why not in t.blocked:
            t.blocked = f"{t.blocked} / {why}" if t.blocked else why


def outside_reason(st: syncstate.Standing, t: ticket_model.Ticket) -> str:
    """親のワークツリーの外にしか無いチケットを信頼しない理由と、ユーザが親のブランチへ移してコミットする手順。"""
    return (
        f"親のブランチ {st.branch_name} のワークツリーの外"
        f"（{t.tree or 'ワークスペースルート'}）にしか無いチケット。"
        "取り込み済みの親子のチケットでは、親のブランチ上のチケットだけが本物。ユーザがそのチケットを親のワークツリー"
        f"（.claude/worktrees/{st.family}）の同じ置き場へ移してコミットと push をし、"
        "元のチケットを消す"
    )


def family_standing(
    conf: settings.Settings,
    root: str,
    t: ticket_model.Ticket,
    fams: syncstate.Families | None = None,
) -> syncstate.Standing:
    """このチケットが属する親子のチケットの立ち位置（`syncstate.Families.standing`）。

    親子のチケットは `parent`（無ければ自分）の識別子で引く。
    """
    fams = fams or syncstate.Families(conf, root)
    return fams.standing(t.parent or t.ticket, t.project or "")


def family_problems(
    conf: settings.Settings,
    root: str,
    t: ticket_model.Ticket,
    fams: syncstate.Families | None = None,
) -> list[rules.Problem]:
    """取り込み済みの親子のチケットの提案を承認しない理由。

    親子のチケットが決まらない・閉じているなら承認しない。提案は親のブランチの上で書き、
    push してから承認を頼む決まりなので、
    親のワークツリーの外にある提案も承認しない。取り込み状態の無い親子のチケットには何も言わない。
    """
    fams = fams or syncstate.Families(conf, root)
    if not fams.active:
        return []
    st = family_standing(conf, root, t, fams)
    if not st.imported:
        return []
    if st.stop:
        return [rules.Problem(rules.SEVERITY_ERROR, t.ticket, family_stop_text(root, st))]
    if st.home is not None and not syncstate.same_tree(t.tree_root, st.home.root):
        return [
            rules.Problem(
                rules.SEVERITY_ERROR,
                t.ticket,
                f"提案が親のブランチ {st.branch_name} のワークツリーの外"
                f"（{t.tree or 'ワークスペースルート'}）にある。"
                "取り込み済みの親子のチケットの提案は"
                f"親のワークツリー（.claude/worktrees/{st.family}）で書いて push してから"
                "承認を頼んでください",
            )
        ]
    return []


def branch_problems(
    conf: settings.Settings,
    root: str,
    t: ticket_model.Ticket,
    fams: syncstate.Families | None = None,
    others: list[ticket_model.Ticket] | None = None,
) -> list[rules.Problem]:
    """親のブランチ名を承認してよいか。

    - `branch:` がそのリポジトリの統合先の名前に当たれば承認しない。比べる名前は
      `Families.integration_names`（環境変数・`.claude/settings.local.json`・統合先の取り込み結果・
      `origin/HEAD`・`origin/main`・`origin/master`）。固定のリストと字の検査は
      `ticket_ids.branch_problem` が読むときに済ませている
    - 2 つの親子のチケットが同じブランチを名乗る（この親のブランチ名が、同じリポジトリの開いた別の
      チケットの親のブランチ名か識別子と同じ。大文字小文字は区別しない）なら承認しない。`others` は
      比べるチケット（承認済みと承認待ち）
    """
    if t.is_child:
        return []
    fams = fams or syncstate.Families(conf, root)
    found: list[rules.Problem] = []
    if t.branch:
        folded = t.branch.casefold()
        hits = [n for n in fams.integration_names(t.project or "") if n.casefold() == folded]
        if hits:
            found.append(
                rules.Problem(
                    rules.SEVERITY_ERROR,
                    t.ticket,
                    f"`branch: {t.branch}` は統合先の名前（{', '.join(hits)}）に当たる。"
                    "統合先を親のブランチにしない",
                )
            )
    mine = ticket_ids.branch_name(t).casefold()
    clash = sorted(
        {
            o.ticket
            for o in others or []
            if o.ticket != t.ticket
            and (o.parent or o.ticket) != t.ticket
            and o.project == t.project
            and mine in {ticket_ids.branch_name(o).casefold(), o.ticket.casefold()}
        }
    )
    if clash:
        found.append(
            rules.Problem(
                rules.SEVERITY_ERROR,
                t.ticket,
                f"親のブランチ {ticket_ids.branch_name(t)} を別の親子のチケット"
                f"（{', '.join(clash)}）も親のブランチか識別子として使っている。"
                "2 つの親子のチケットが同じブランチを名乗ると、"
                "どちらのチケットを本物とするか決まらないので承認しない",
            )
        )
    return found


def family_stop_text(root: str, st: syncstate.Standing) -> str:
    """親子のチケットが決まらない・閉じているので承認しない理由と、ユーザが打つ手順（`family_problems`）。"""
    hint = " / ".join(syncstate.guidance(root, st))
    return f"{st.stop}。承認しない。{hint}"


def integration_problems(
    conf: settings.Settings,
    root: str,
    t: ticket_model.Ticket,
    fams: syncstate.Families | None = None,
) -> list[rules.Problem]:
    """新規の提案の識別子を統合先の `done/` と比べる。

    開いた親子のチケットでも統合先の `done/` は常に一緒に読む。古い統合先から切った `P` で、
    閉じた識別子の再利用が新規の承認として通らないように。統合先の `done/` は取り込み結果（最後に
    取り込んだ `origin/<統合先>` から書き出したもの）から、親子のチケットの取り込み状態の有無に
    依らず、取り込んだことのあるリポジトリ（`sync/<リポジトリ>/`）の全提案に当てる。
    取り込み結果が壊れている・読めない・入れ替えが終わらないときは、確かめられないので「決まらない」として
    承認しない（何も出さずに通すことはしない）。一度も取り込んでいないリポジトリは何も言わない
    （今のまま）。

    手元の退避（`logs/archive/`。`ready` が閉じた親子のチケットを移した先）にある識別子も、閉じた
    識別子として数える。統合先には閉じたチケットを残さないので、手元ではここが閉じた記録になる
    （別の機械では見えない）。退避は取り込み状態の有無に依らず見る。退避との比べは大文字小文字を
    区別しない（区別しないファイルシステムでは、ブランチとワークツリーの名前がぶつかるため）。
    """
    if any(archive.same_id(t.ticket, i) for i in archive.ids(root, t.project)):
        return [
            rules.Problem(
                rules.SEVERITY_ERROR,
                t.ticket,
                f"{t.ticket} は手元の退避（{archive.ARCHIVE_DIR.replace(os.sep, '/')}/）で"
                "閉じている。閉じた識別子を新規に承認しない。識別子を変えて出し直してください",
            )
        ]
    fams = fams or syncstate.Families(conf, root)
    if not fams.active:
        return []
    repo = syncstate.repo_key(t.project)
    integ = fams.integration(repo)
    if integ is None:
        return []
    sync = settings.script_command(root, "ccnavi-sync.sh")
    ids, why = fams.done(repo)
    if why:
        return [
            rules.Problem(
                rules.SEVERITY_ERROR,
                t.ticket,
                f"統合先の取り込み結果（sync/{repo}/integration）を読めない（{why}）。閉じた識別子の"
                "再利用を確かめられないので決まらない。承認しない。オンラインで"
                f" '{sync}' を打って統合先を取り込み直してください",
            )
        ]
    if t.ticket in ids:
        return [
            rules.Problem(
                rules.SEVERITY_ERROR,
                t.ticket,
                f"{t.ticket} は統合先（{integ.branch or '?'}）の done/ で閉じている。"
                "閉じた識別子を新規に承認しない。識別子を変えて出し直してください",
            )
        ]
    return []


def integration_closed(
    conf: settings.Settings, root: str, proposals: list[ticket_model.Ticket]
) -> set[str]:
    """統合先の取り込み結果の `done/` か手元の退避に同じ識別子がある新規の提案。

    閉じた識別子の再利用を見つける。
    """
    fams = syncstate.Families(conf, root)
    archived: dict[str, set[str]] = {}

    def closed(t: ticket_model.Ticket) -> bool:
        if t.project not in archived:
            archived[t.project] = archive.ids(root, t.project)
        if any(archive.same_id(t.ticket, i) for i in archived[t.project]):
            return True
        return fams.active and t.ticket in fams.done(syncstate.repo_key(t.project))[0]

    return {t.ticket for t in proposals if t.state == ticket_model.TODO and closed(t)}


def _by_id(found: list[ticket_model.Ticket]) -> dict[str, list[ticket_model.Ticket]]:
    """識別子ごとのチケットの全部。順序は見つけた順。"""
    grouped: dict[str, list[ticket_model.Ticket]] = {}
    for t in found:
        grouped.setdefault(t.ticket, []).append(t)
    return grouped


def by_id(tickets: list[ticket_model.Ticket]) -> dict[str, ticket_model.Ticket]:
    return {t.ticket: t for t in tickets}


# ---- 先行（`predecessors`）を満たしているか。承認と着手の両方で求める

# 先行の状態。満たしたとみなすのは `done` だけ（`.ccnavi/approved/done/` に在り、取り消しでない）。
PRED_DONE = ticket_model.DONE
PRED_CANCELLED = ticket_model.CANCELLED
PRED_MISSING = "missing"
PRED_SCATTERED = "scattered"
# 形の上で満たせない先行。待っても通らないので、ふつうの error にする。
PRED_SELF = "self"  # 自分自身
PRED_ANCESTOR = "ancestor"  # 自分の親（子は親の中の作業で、親は子より先に閉じない）
PRED_CYCLE = "cycle"  # 先行を辿ると自分に戻る
# 取り込み済みの親子のチケットの先行で、その親子のチケットが決まらない・親のブランチ上に無い。
PRED_UNDECIDED = "undecided"
# 先行が閉じれば同じ提案のまま通る状態。承認では `rules.KIND_NOT_YET` の苦情にする。
PRED_WAITING = (ticket_model.TODO, ticket_model.DOING, ticket_model.REVIEW)
PRED_LABELS = {
    ticket_model.TODO: "承認待ち（todo/）",
    ticket_model.DOING: "作業中（doing/）",
    ticket_model.REVIEW: "レビュー待ち（review/）",
    PRED_CANCELLED: "取り消し済み（done/ で cancelled_at を持つ）",
    PRED_MISSING: "どの置き場にも無い",
    PRED_SCATTERED: "複数の場所にある",
    PRED_SELF: "自分自身",
    PRED_ANCESTOR: "自分の親",
    PRED_CYCLE: "先行を辿ると自分に戻る",
    PRED_UNDECIDED: "親子のチケットが決まらない",
}


@dataclass
class Predecessor:
    """先行 1 本の今。`where` は複数の場所にあるときの在り処（`ツリー:置き場` のリスト）。"""

    ticket: str
    state: str
    where: str = ""

    @property
    def met(self) -> bool:
        return self.state == PRED_DONE

    @property
    def waiting(self) -> bool:
        """先行が閉じれば満たす状態か。取り消し・無い・複数の場所は、待っても満たさない。"""
        return self.state in PRED_WAITING

    @property
    def label(self) -> str:
        text = PRED_LABELS.get(self.state, self.state)
        return f"{text}: {self.where}" if self.where else text


def predecessor_pool_of(
    open_copies: list[ticket_model.Ticket],
    review: list[ticket_model.Ticket],
    closed: list[ticket_model.Ticket],
    proposals: list[ticket_model.Ticket],
    root: str = "",
) -> dict[str, list[ticket_model.Ticket]]:
    """先行を引く対応表。識別子 → 本物とする側のチケットの全部（`ops_close._places` と同じ集め方）。

    承認済みチケット（作業中・レビュー待ち・閉じた）はどれも数える。`todo/` の提案は、同じ識別子の
    承認済みチケットがどこにも無いときだけ数える（在れば改版の候補か書き損じ）。チケットが 2 つ以上
    残れば、どれが本物か決まらない。

    `root`（ワークスペースルート）を渡せば、どの置き場にも無い先行を手元の退避（`logs/archive/`）の
    `done/` から引く。`ready` が閉じた親子のチケットを退避した後も、先行を閉じたものとして読むため。
    引くのはリストのチケットが先行に書いた識別子だけ（退避を全部は読まない）。
    """
    pool = _by_id(open_copies + review + closed)
    for t in proposals:
        if t.state == ticket_model.TODO and t.ticket not in pool:
            pool.setdefault(t.ticket, []).append(t)
    if root:
        # 引くのは、その先行を書いたチケットと同じリポジトリ（プロジェクト）の退避だけ。
        wanted: dict[str, set[str]] = {}
        for t in open_copies + review + closed + proposals:
            for ident in t.predecessors:
                if ident not in pool:
                    wanted.setdefault(ident, set()).add(t.project)
        for ident in sorted(wanted):
            found = [h for p in sorted(wanted[ident]) for h in archive.find(root, ident, p)]
            if found:
                pool[ident] = found
    return pool


def align_imported(
    conf: settings.Settings, root: str, pool: dict[str, list[ticket_model.Ticket]]
) -> None:
    """取り込み済みの親子のチケットの先行を、その親のブランチ上のチケットで読み直す。

    Chrome は先行を、参照の閉包にある親子のチケット `P` から引く。手元もそれに揃える。
    ただし**通る向きには読み替えない**（厳しくする向きだけ）。

    - 親子のチケットが決まらない（取り込み状態が `gone`・`blocked`・壊れている、
      親のワークツリーが無い）:「親子のチケットが決まらない」にする（切り直しを案内する）
    - 親のワークツリーにその識別子のチケットが無い: 同じく「親子のチケットが決まらない」
      （親のブランチの外にしか無い）
    - 親のワークツリーのチケットが 1 つで、
      閉じていない（作業中・レビュー待ち・承認待ち）: それを採る
    - 親のワークツリーのチケットが閉じている、
      チケットが 2 つ以上: 前の対応表のまま（手元の全ツリーから
      引いた答え。前の対応表で満たしていなければ満たさないまま）
    - 閉じた親子のチケット（統合先の `done/` に親のチケットがある）と、取り込み状態の無い
      親子のチケット: 前の対応表のまま
    """
    fams = syncstate.Families(conf, root)
    if not fams.active:
        return
    for ident, hits in list(pool.items()):
        if not hits:
            continue
        # 手元の退避から引いた先行は閉じたもの。親のブランチ上のチケットでは読み直さない。
        if all(archive.is_archived_path(root, h.path) for h in hits):
            continue
        st = family_standing(conf, root, hits[0], fams)
        if not st.imported or st.closed:
            continue
        if st.stop:
            pool[ident] = [_undecided(hits[0], st.stop)]
            continue
        mine = [
            h
            for h in hits
            if st.home is not None and syncstate.same_tree(h.tree_root, st.home.root)
        ]
        if not mine:
            pool[ident] = [
                _undecided(
                    hits[0],
                    f"親のブランチ {st.family} のワークツリーに {ident} のチケットが無い"
                    f"（{', '.join(h.tree or '(ワークスペースルート)' for h in hits)} にしか無い）",
                )
            ]
        elif len(mine) == 1 and mine[0].state != PRED_DONE:
            pool[ident] = mine


def _undecided(sample: ticket_model.Ticket, why: str) -> ticket_model.Ticket:
    """先行の対応表に置く「親子のチケットが決まらない」の項目。理由は `blocked` に入れて渡す。"""
    return replace(sample, state=PRED_UNDECIDED, blocked=why)


def predecessor_states(
    t: ticket_model.Ticket, pool: dict[str, list[ticket_model.Ticket]]
) -> list[Predecessor]:
    """このチケットの先行のそれぞれの今。順序は `predecessors` の順（重ねて書いたものは 1 つ）。"""
    out: list[Predecessor] = []
    for ident in dict.fromkeys(t.predecessors):
        hits = pool.get(ident, [])
        if ident == t.ticket:
            out.append(Predecessor(ident, PRED_SELF))
            continue
        if t.parent and ident == t.parent:
            out.append(Predecessor(ident, PRED_ANCESTOR))
            continue
        if len(hits) == 1 and hits[0].state != PRED_DONE:
            loop = _loop_back(t.ticket, ident, pool)
            if loop:
                out.append(Predecessor(ident, PRED_CYCLE, " → ".join([t.ticket, *loop])))
                continue
        if not hits:
            out.append(Predecessor(ident, PRED_MISSING))
        elif len(hits) == 1 and hits[0].state == PRED_UNDECIDED:
            out.append(Predecessor(ident, PRED_UNDECIDED, hits[0].blocked))
        elif len(hits) > 1:
            where = ", ".join(f"{h.tree or '(ワークスペースルート)'}:{h.state}" for h in hits)
            out.append(Predecessor(ident, PRED_SCATTERED, where))
        else:
            out.append(Predecessor(ident, hits[0].state or ticket_model.DOING))
    return out


def _loop_back(origin: str, first: str, pool: dict[str, list[ticket_model.Ticket]]) -> list[str]:
    """`first` から先行を辿って `origin` に戻る経路。戻らなければ空。

    辿るのは対応表で 1 つに決まるチケットだけ（決まらないものは別の苦情になる）。閉じた（`done/`）
    チケットの先の先行は辿らない。閉じたものは満たしているので、そこで循環が切れる。
    """
    seen: set[str] = set()
    stack: list[tuple[str, list[str]]] = [(first, [first])]
    while stack:
        ident, route = stack.pop()
        if ident in seen:
            continue
        seen.add(ident)
        hits = pool.get(ident, [])
        if len(hits) != 1 or hits[0].state == PRED_DONE or not hits[0].is_child:
            continue
        for nxt in hits[0].predecessors:
            if nxt == origin:
                return [*route, origin]
            stack.append((nxt, [*route, nxt]))
    return []


def unmet_predecessors(
    t: ticket_model.Ticket, pool: dict[str, list[ticket_model.Ticket]]
) -> list[Predecessor]:
    """満たしていない先行。子だけが先行を持つ（親の `predecessors` は読まない）。"""
    if not t.is_child:
        return []
    return [p for p in predecessor_states(t, pool) if not p.met]


def predecessor_problems(
    t: ticket_model.Ticket, pool: dict[str, list[ticket_model.Ticket]], approved_rel: str
) -> list[rules.Problem]:
    """承認で落とす先行の苦情。

    満たしたとみなすのは `done/` に在って取り消しでないものだけ。

    先行が閉じれば同じ提案が通るもの（承認待ち・作業中・レビュー待ち）は `rules.KIND_NOT_YET`。
    書いた側に直すものは無く、`--lint` は warn で言う（フェーズの順序と同じ扱い）。取り消し・
    どこにも無い・複数の場所は、待っても満たさないので、ふつうの error にする。
    """
    problems: list[rules.Problem] = []
    for p in unmet_predecessors(t, pool):
        if p.waiting:
            problems.append(
                rules.Problem(
                    rules.SEVERITY_ERROR,
                    t.ticket,
                    f"先行 {p.ticket} が閉じていない（いまは {p.label}）。{p.ticket} が "
                    f"{approved_rel}/{ticket_model.DONE}/ に入る（作業を終え、レビューが要るなら"
                    "ユーザのレビューが済む）まで承認しない。先行が要らないなら "
                    "predecessors から外して"
                    "出し直してください",
                    rules.KIND_NOT_YET,
                )
            )
        elif p.state == PRED_CANCELLED:
            problems.append(
                rules.Problem(
                    rules.SEVERITY_ERROR,
                    t.ticket,
                    f"先行 {p.ticket} は{p.label}。取り消した先行は満たせないので承認しない。"
                    "predecessors から外して出し直してください",
                )
            )
        elif p.state in (PRED_SELF, PRED_ANCESTOR, PRED_CYCLE):
            why = {
                PRED_SELF: "自分自身を先行に挙げている。自分が閉じるのを待つことはできない",
                PRED_ANCESTOR: "自分の親を先行に挙げている。親は子が全部閉じてから閉じるので、"
                "待っても満たさない",
                PRED_CYCLE: f"先行が輪になっている（{p.where}）。どれも他が閉じるのを待つので、"
                "待っても満たさない",
            }[p.state]
            problems.append(
                rules.Problem(
                    rules.SEVERITY_ERROR,
                    t.ticket,
                    f"先行 {p.ticket} は{PRED_LABELS[p.state]}。{why}。"
                    "predecessors から外して出し直してください",
                )
            )
        elif p.state == PRED_UNDECIDED:
            problems.append(
                rules.Problem(
                    rules.SEVERITY_ERROR,
                    t.ticket,
                    f"先行 {p.ticket} が属する親子のチケットが決まらない（{p.where}）。"
                    "取り込み済みの親子のチケットの先行は"
                    "その親のブランチ上のチケットで確かめる。親のワークツリーを切り直すか、"
                    "'ccnavi-sync.sh' で取り込み直してから出し直してください",
                )
            )
        elif p.state == PRED_MISSING:
            problems.append(
                rules.Problem(
                    rules.SEVERITY_ERROR,
                    t.ticket,
                    f"先行 {p.ticket} がどの置き場（todo/・doing/・review/・done/）にも無い。"
                    "表記を直すか、predecessors から外して出し直してください",
                )
            )
        else:
            problems.append(
                rules.Problem(
                    rules.SEVERITY_ERROR,
                    t.ticket,
                    f"先行 {p.ticket} が{p.label}。"
                    "どれが本物か決まらないので満たしたとみなさない。"
                    "先に 1 つに決めてください"
                    "（先へ進んだ側を合流させるか、残ったワークツリーを片付ける）",
                )
            )
    return problems


def children_of(tickets: list[ticket_model.Ticket], parent_id: str) -> list[ticket_model.Ticket]:
    return [t for t in tickets if t.parent == parent_id]


def _reserved_project(t: ticket_model.Ticket) -> list[rules.Problem]:
    """`project:` がレイヤーの名前に予約してある表記なら error（設計 11.4）。"""
    if not t.project or not settings.is_reserved_layer_name(t.project):
        return []
    reserved = " と ".join(f"`{name}`" for name in settings.RESERVED_LAYER_NAMES)
    return [
        rules.Problem(
            rules.SEVERITY_ERROR,
            t.ticket,
            f"`project: {t.project}` はレイヤーの名前として予約してある表記（{reserved}）。"
            "その名前のプロジェクトはレイヤーとして数えないので、このチケットのレイヤーが決まらない。"
            "ワークスペース自身の提案は `wip/proposals/` に置いてください。プロジェクトの提案なら、"
            "そのプロジェクトの名前を変えてから置いてください",
        )
    ]


def project_problems(
    t: ticket_model.Ticket, pool: dict[str, ticket_model.Ticket], conf: settings.Settings
) -> list[rules.Problem]:
    """`project` が置き場と合っているか（REQ-MLT-11）。

    プロジェクトを決めるのは提案を置いた場所（設計 11.5）。frontmatter の `project:` は
    宣言ではなく照合で、置き場と違えば承認しない。親と子は同じ置き場に並ぶので、継ぐ段は
    無い。承認の画面が置き場から引いた値を出し、それが承認済みチケットに残る。

    予約名（`common` / `self`）は指せない。置き場にその名前のディレクトリが在っても
    レイヤーとしては数えないので（`ruleload.layers`）、指せると「レイヤーが決まらないチケット」を
    承認することになる。
    """
    if t.declared_project and t.declared_project != t.project:
        where = conf.tickets
        return [
            rules.Problem(
                rules.SEVERITY_ERROR,
                t.ticket,
                f"`project: {t.declared_project}` が置き場"
                f"（{t.project or 'ワークスペース'}）と違う。"
                f"{t.declared_project} の提案はワークスペースの {where}/ に置いてください",
            )
        ]
    reserved = _reserved_project(t)
    if reserved:
        return reserved
    if t.is_child:
        parent = pool.get(t.parent)
        if parent is None or t.project == parent.project:
            return []
        return [
            rules.Problem(
                rules.SEVERITY_ERROR,
                t.ticket,
                f"置き場（{t.project or 'ワークスペース'}）が親 {parent.ticket} の"
                f"置き場（{parent.project or 'ワークスペース'}）と違う。"
                "子は親と同じ置き場に置いてください",
            )
        ]
    # 予約名は `known` から外す。置き場に `projects/self/` が在っても、それはレイヤーでは
    # ないので、指せてはいけない。素の一覧で見ると通ってしまう。
    known = {
        p.name for p in tree.projects(conf.projects) if not settings.is_reserved_layer_name(p.name)
    }
    if t.project and t.project not in known:
        return [
            rules.Problem(
                rules.SEVERITY_ERROR,
                t.ticket,
                f"`project: {t.project}` は置き場 {conf.projects or '(無し)'} に無い",
            )
        ]
    return []


def child_problems(
    t: ticket_model.Ticket, parent: ticket_model.Ticket | None, missing: str = ""
) -> list[rules.Problem]:
    """子と親の構造の検査。承認（`validate`）と判定（`blocking_problems`）が同じ答えを引く。

    どれも「子の範囲をどの親で切り詰めるか」が決まらない形なので、承認でも判定でも
    通さない。1 か所に置くのは、置き場を動かして承認する進め方で判定の側の
    検査だけが古くなると、承認を通ったチケットと通らないチケットで答えが分かれるから。

    「種類の定義が読めない」はここに入れない。壊れているのは設定で、チケットの形は
    正しい。判定は注記を添えて親の範囲で切り詰める（`judge.ticket_verdict`）。
    """
    if parent is None:
        # 文面だけは呼び手が差し替える。承認のときは「まだ承認されていない」しか起きないが、
        # 判定のときは「承認されたが閉じた」も同じ検査に当たる。検査は同じで、読むユーザの
        # 次にすることが違うだけなので、分けるのは言葉だけにする。
        detail = missing or f"親 {t.parent} が承認されていない"
        return [rules.Problem(rules.SEVERITY_ERROR, t.ticket, detail)]
    if parent.is_child:
        return [
            rules.Problem(
                rules.SEVERITY_ERROR, t.ticket, f"親 {t.parent} 自身が子。深さは 2 段まで"
            )
        ]
    if parent.has_plan and t.phase is not None and parent.item_at(t.phase) is None:
        return [
            rules.Problem(
                rules.SEVERITY_ERROR,
                t.ticket,
                f"{t.phase} 番目のフェーズは親 {parent.ticket} の計画に無い"
                f"（計画は {len(parent.numbered())} 番目まで）",
            )
        ]
    return []


def blocking_problems(
    conf: settings.Settings, t: ticket_model.Ticket, pool: dict[str, ticket_model.Ticket]
) -> list[rules.Problem]:
    """判定がこの承認済みチケットを信頼できない理由。空なら信頼してよい。

    承認のときにしか当たらなかった検査のうち、当たらないと「範囲をどこで切り詰めるか」が
    決まらないものだけを置く。置き場を動かして承認する進め方は `--agree` を通らないので、
    同じ検査を判定の側でも当てる。当たれば範囲は使われず、その場所は止まる。

    ここに入れないもの。
    - 計画の形（`plan_problems`）。範囲に影響しないので `--lint` が言う
    - フェーズの順序（`phase.order_problems`）。順序が狂っていても、その子の範囲を
      どの親で切り詰めるかは決まる。順序で止めるのは `held_phase`（レビュー待ちの間 `Agent` と
      シェルを止める）で、この検査とは別の仕組み。
      止めると、レビュー待ちの間その子が一切書けなくなり、レビュー待ちでも Write を通す形
      とは結果が食い違う。`--lint` が warn で言う
    - 範囲の超過。判定が親と種類の上限で切り詰めるので、止める理由が無い
    - 再開（`done/` から `doing/` へ手で戻す）で残った `completed_at` などの欄。ユーザの再開を
      止めないよう、`--lint` が warn で言うだけにする

    ここに入れる、中身だけで分かる矛盾。
    - `started_at` が無いのに `base_sha` がある。`start` は 2 つを一緒に書くので道具を通らない形で、
      `base_sha` は範囲外の検査とリスクの基準点に使われる
    - 前の版の承認の記録を持たないのに `workflow:` 欄がある。待ち方は `--agree` が
      `phases/<親>/workflow.yml` に書くもので、欄を承認済みの待ち方として効かせない
    - 待ち方のファイルが読めない。一直線と読むと、ユーザが承認した待ち方と違う順で進む
    - 親のツリーの未着手のチケットが、手元の退避の閉じたチケットと同じ識別子
      （`archive.drop_archived`）。使い直した識別子か、退避と同じものかを見分けられない
    """
    problems = list(project_problems(t, pool, conf))
    problems.extend(content_problems(t))
    if t.is_child:
        missing = f"親 {t.parent} の承認済みチケットが作業中に無い（未承認か、閉じている）"
        problems.extend(child_problems(t, pool.get(t.parent), missing))
    return [p for p in problems if p.severity == rules.SEVERITY_ERROR]


def content_problems(t: ticket_model.Ticket) -> list[rules.Problem]:
    """作業中の承認済みチケットの、中身だけで分かる欄の矛盾（判定が止める分）。"""
    problems: list[rules.Problem] = []
    if t.base_sha and not t.started_at:
        problems.append(
            rules.Problem(
                rules.SEVERITY_ERROR,
                t.ticket,
                "`started_at` が無いのに `base_sha` がある。2 つは着手（`start`）が一緒に書く欄で、"
                "片方だけの形は道具を通っていない。ユーザが承認済みチケットを確かめて直してください",
            )
        )
    if ticket_model.WORKFLOW_KEY in t.raw and not has_record(t):
        problems.append(
            rules.Problem(
                rules.SEVERITY_ERROR,
                t.ticket,
                f"`{ticket_model.WORKFLOW_KEY}:` の欄がある。待ち方は --agree が "
                f"{approval_marks.PHASES_DIR}/<親>/{approval_marks.WORKFLOW_FILE} "
                "に書くもので、チケットには書かない。"
                "ユーザが欄を消してください",
            )
        )
    if t.workflow_unreadable:
        problems.append(rules.Problem(rules.SEVERITY_ERROR, t.ticket, t.workflow_unreadable))
    if t.archived_clash:
        problems.append(rules.Problem(rules.SEVERITY_ERROR, t.ticket, t.archived_clash))
    return problems


def resumed_fields(t: ticket_model.Ticket) -> list[str]:
    """作業中（`doing/`）なのに値が残っている、閉じるときに書く欄の名前。

    `finish` と `cancel` は `doing/` から動かすので、道具を通る限り残らない。残るのは、ユーザが
    `done/` から `doing/` へ手で戻した再開の形。判定では止めず、`--lint` が warn で言う。
    """
    values = {
        "completed_at": t.completed_at,
        "cancelled_at": t.cancelled_at,
        "cancel_reason": t.cancel_reason,
    }
    return [name for name, value in values.items() if value]


def mark_blocked(conf: settings.Settings, kept: list[ticket_model.Ticket]) -> None:
    """判定が読む承認済みチケットに、信頼できない理由を付ける。

    理由を読むのは `phase_scope.scope_verdict` で、実行前チェック・実行後チェック・サブエージェント
    終了時チェックの 3 か所が同じ答えを引く。1 か所で付けるのは、3 か所が別々に検査を
    呼ぶと、同じ書き込みが実行前は通って実行後に範囲外と報告されるから。

    **親を引く対応表は `kept` そのもの**（`by_id`）で、判定が `parent` を引く索引と同じ。
    別の対応表で引くと、ここでは親が見つかって理由が付かないのに、判定の側では見つからず
    `parent=None` のまま子の宣言だけで範囲が決まる（閉じた親やレビュー待ちの親まで
    引ける対応表にすると、この形になる）。親の範囲で切り詰められないのに通る形は、
    承認していない範囲に書ける経路そのものなので、引けないなら止める側を採る。

    親が閉じたのに子が開いている形は、道具を通る限り起きない（`ops_close.close_problems` が
    開いた子のある親を閉じさせない）。置き場を手で動かして起きたなら、親を閉じたのは
    ユーザなので、その子を止めるのがユーザの意思に沿う。
    """
    pool = by_id(kept)
    for t in kept:
        problems = blocking_problems(conf, t, pool)
        t.blocked = problems[0].detail if problems else ""
