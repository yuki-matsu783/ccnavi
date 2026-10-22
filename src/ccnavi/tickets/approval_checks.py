"""承認済みチケットの構造の検査。親子・統合先・先行・プロジェクトの欄を、判定の側で当てる。

提案を手で `doing/` へ動かす経路は承認の画面を通らないので、承認のときにしか当たらなかった
構造の検査はここで判定の側から当てる（`blocking_problems`）。親子の立ち位置、
先行チケットの状態、プロジェクトの欄の検査を置く。approval から分けた。approval は読まない。
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from ..infra import settings, tree
from ..policy import rules
from . import standing, ticket_model, workflow


def mark_imported(
    conf: settings.Settings,
    root: str,
    kept: list[ticket_model.Ticket],
    fams: standing.Families | None = None,
) -> None:
    """親子のチケットのうち、信頼できないチケットに理由を付ける（全部の親子のチケットが対象）。

    正とするのは親のブランチ（手元では `.claude/worktrees/<P>` で HEAD のブランチ名を変換したものが
    `P` になるツリー）の
    上のチケットだけ。次のチケットは読むが信頼しない（`blocked`。判定は範囲を使わずに止める）。

    - 親子のチケットが決まらない（親のワークツリーが無い・HEAD のブランチ名がフォルダ名と
      合わない・途中の操作がある）
    - 親のワークツリーの外にしか無いチケット（元ツリーに未コミットで残ったチケットなど）

    **チケットのリストは変えない（外さない）。
    ** 外すと「在る」ことで止まっていたもの（承認待ちの
    重複、開いた子のある親を閉じない）が通るようになる。理由を足すだけで、チケットが親のワークツリーにだけある
    親子のチケットには理由は付かない。すでに理由（`mark_blocked`）が
    あれば、理由を連ねる。
    """
    fams = fams or standing.Families(conf, root)
    for t in kept:
        st = family_standing(conf, root, t, fams)
        why = st.stop
        if not why and st.home is not None and not standing.same_tree(t.tree_root, st.home.root):
            why = outside_reason(st, t)
        if why and why not in t.blocked:
            t.blocked = f"{t.blocked} / {why}" if t.blocked else why


def outside_reason(st: standing.Standing, t: ticket_model.Ticket) -> str:
    """親のワークツリーの外にしか無いチケットを信頼しない理由と、ユーザが親のブランチへ移してコミットする手順。"""
    return (
        f"親のブランチ {st.branch_name} のワークツリーの外"
        f"（{t.tree or 'ワークスペースルート'}）にしか無いチケット。"
        "取り込み済みの親子のチケットでは、親のブランチ上のチケットだけが正。ユーザがそのチケットを親のワークツリー"
        f"（.claude/worktrees/{st.family}）の同じ置き場へ移してコミットと push をし、"
        "元のチケットを削除します。"
    )


def family_standing(
    conf: settings.Settings,
    root: str,
    t: ticket_model.Ticket,
    fams: standing.Families | None = None,
) -> standing.Standing:
    """このチケットが属する親子のチケットの立ち位置（`standing.Families.standing`）。

    親子のチケットは `parent`（無ければ自分）の識別子で引く。
    """
    fams = fams or standing.Families(conf, root)
    return fams.standing(t.parent or t.ticket, t.project or "")


def family_problems(
    conf: settings.Settings,
    root: str,
    t: ticket_model.Ticket,
    fams: standing.Families | None = None,
) -> list[rules.Problem]:
    """親子のチケットの提案を承認しない理由。

    親子のチケットが決まらない（親のワークツリーが無いなど）・閉じているなら承認しない。提案は親のブランチの上で書き、
    `ccnavi-ticket.sh propose` で送ってから承認を頼む決まりなので、
    親のワークツリーの外にある提案も承認しない。
    """
    fams = fams or standing.Families(conf, root)
    st = family_standing(conf, root, t, fams)
    if st.stop:
        return [rules.Problem(rules.SEVERITY_ERROR, t.ticket, family_stop_text(root, st))]
    if st.home is not None and not standing.same_tree(t.tree_root, st.home.root):
        return [
            rules.Problem(
                rules.SEVERITY_ERROR,
                t.ticket,
                f"提案が親のブランチ {st.branch_name} のワークツリーの外"
                f"（{t.tree or 'ワークスペースルート'}）にある。"
                "親子のチケットの提案は"
                f"親のワークツリー（.claude/worktrees/{st.family}）で書き、"
                f"'{settings.script_command(root, 'ccnavi-ticket.sh')} propose {st.family}' で"
                "親のブランチへ送ってから承認を頼んでください",
            )
        ]
    return []


def mr_problems(t: ticket_model.Ticket) -> list[rules.Problem]:
    """親チケットに `issue:` と `mr:`（親ブランチのマージリクエストの番号）が記録されているか。

    親の作成と承認の前提。チケット作業は issue と MR のセットで進める。値は `ccnavi-start.sh` が
    「必須」の行で出す。子には要らない。
    """
    if t.is_child:
        return []
    out: list[rules.Problem] = []
    if t.issue is None:
        out.append(
            rules.Problem(
                rules.SEVERITY_ERROR,
                t.ticket,
                f"親チケット {t.ticket} に issue: が記録されていない。`ccnavi-start.sh` が出した "
                "issue を issue: に書いてください（例 `issue: 12`、別のリポジトリなら "
                "`issue: owner/repo#12`）。チケット作業は issue と MR のセットで進める",
            )
        )
    if not t.mr:
        out.append(
            rules.Problem(
                rules.SEVERITY_ERROR,
                t.ticket,
                f"親チケット {t.ticket} に mr: が記録されていない。`ccnavi-start.sh` が出したマージ"
                "リクエストの番号を mr: に書いてください（例 `mr: 12`）。"
                "親のブランチのマージリクエストが"
                "無いと、レビューの依頼も正の特定もできない",
            )
        )
    return out


def branch_problems(
    conf: settings.Settings,
    root: str,
    t: ticket_model.Ticket,
    fams: standing.Families | None = None,
    others: list[ticket_model.Ticket] | None = None,
) -> list[rules.Problem]:
    """親の識別子を承認してよいか。

    親の識別子は親のブランチ名の `/` を `-` に置き換えたもの。2 つの親子のチケットが同じ識別子
    （大文字小文字は区別しない）を持つなら承認しない。`others` は比べるチケット
    （承認済みと承認待ち）
    """
    if t.is_child:
        return []
    mine = t.ticket.casefold()
    clash = sorted(
        {
            o.ticket
            for o in others or []
            if o.ticket != t.ticket
            and (o.parent or o.ticket) != t.ticket
            and o.project == t.project
            and mine == o.ticket.casefold()
        }
    )
    if clash:
        return [
            rules.Problem(
                rules.SEVERITY_ERROR,
                t.ticket,
                f"親の識別子 {t.ticket} と大文字小文字だけが違う識別子（{', '.join(clash)}）が"
                "ある。ワークツリーとブランチの名前がぶつかるので承認しない",
            )
        ]
    return []


def misplaced_home_text(root: str, family: str, shown: str) -> str:
    """承認済みチケットが親のワークツリーに無いときの理由と直し方。

    `home_problem` と ready が同じ文を出す。`ccnavi-user-push-approved.sh` と `ccnavi-sync.sh` は、
    ツリーごとの置き場を送る・親のブランチを取り込むだけで、ツリーの間でチケットを動かさないので、
    承認済みチケットは手で動かさず、承認し直す。
    """
    worktree_sh = settings.script_command(root, "ccnavi-worktree.sh")
    return (
        f"親 {family} の承認済みチケットが親のワークツリー"
        f"（{tree.WORKTREES_DIR}/{family}）ではなく {shown} に在る。"
        "ccnavi は親のワークツリーを「名前が親の識別子のもの」として探すので、"
        "別名のツリーで承認したチケットは、取り込み・クローズ済みのチケットの退避・"
        "Draft を解除する ready の対象にならない。承認済みチケットは手で動かさない。直す手順: "
        f"1) 親のワークツリーが無ければ '{worktree_sh} {family}' で切る "
        f"2) {shown} にある {family} の提案（wip/proposals/ の todo/ か review/）を"
        "親のワークツリーの同じ相対位置に移す "
        "3) ユーザに承認を取り直してもらう（承認済みチケットが親のワークツリーに置かれる） "
        f"4) {shown} に残った {family} の承認済みチケットの片付けをユーザに聞く。"
        "ccnavi-user-push-approved.sh と ccnavi-sync.sh は各ツリーの置き場を送る・取り込むだけで、"
        "ツリーの間でチケットを動かさない"
    )


def home_problem(root: str, conf: settings.Settings, t: ticket_model.Ticket) -> str:
    """承認済みチケットが、識別子と違う名前のワークツリーに在るときの理由。無ければ空。

    ワークスペースルートとプロジェクトの元ツリーは言わない（`ready` が退避の元として止める
    ツリーのうち、別名のワークツリーだけを早く止める）。親の識別子は、子なら親、親なら自分。
    """
    if not t.tree_root:
        return ""
    home = tree.tree_of(root, t.tree_root, conf.projects)
    if home is None or home.is_main:
        return ""
    family = t.parent or t.ticket
    if home.name == family:
        return ""
    return misplaced_home_text(root, family, home.name)


def place_problems(
    root: str, conf: settings.Settings, t: ticket_model.Ticket
) -> list[rules.Problem]:
    """提案を識別子と違う名前のワークツリーに書いていれば承認しない（`--lint` の warn と同じ条件）。

    承認済みチケットは提案を書いたツリーにそのまま置かれる。親のワークツリーが識別子と違う名前だと、
    親が取り込み済みにならず、承認済みチケットの自動コミットも動かず、`start` `finish` は通っても
    `ready` で止まる。承認のあとには取り返せないので、承認の前に止める。ワークスペースルートと
    プロジェクトの元ツリーの提案は言わない（親のワークツリーの名前は識別子で、
    `start` `finish` `ready` も同じ名前を求める）。
    """
    if not t.tree_root:
        return []
    home = tree.tree_of(root, t.tree_root, conf.projects)
    if home is None or home.is_main:
        return []
    family = t.parent or t.ticket
    if home.name == family:
        return []
    worktree_sh = settings.script_command(root, "ccnavi-worktree.sh")
    what = f"子の提案 {t.ticket}" if t.is_child else f"提案 {t.ticket}"
    return [
        rules.Problem(
            rules.SEVERITY_ERROR,
            t.ticket,
            f"{what} を識別子と違う名前のワークツリー {home.name} に書いている。"
            f"親のワークツリーは識別子と同じ名前（{family}）にする。承認済みチケットは提案を書いた"
            "ツリーに置かれるので、別名のままだと親が取り込み済みにならず、承認済みチケットの自動"
            "コミットが動かず、`ready` で止まる。承認のあとには取り返せないので、承認の前に直す: "
            f"'{worktree_sh} {family}' で切り、提案を移して元を削除してから承認を頼んでください",
        )
    ]


def family_stop_text(root: str, st: standing.Standing) -> str:
    """親子のチケットが決まらないので承認しない理由と、ユーザが打つ手順（`family_problems`）。"""
    hint = " / ".join(standing.guidance(root, st))
    return f"{st.stop}。承認しない。{hint}"


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
    PRED_MISSING: (
        "どの置き場にもありません。閉じて片付いた親子のチケットなら、"
        "取り込み済みなので predecessors から削除してください"
    ),
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
) -> dict[str, list[ticket_model.Ticket]]:
    """先行を引く対応表。識別子 → 正とする側のチケットの全部（`ops_close._places` と同じ集め方）。

    承認済みチケット（作業中・レビュー待ち・閉じた）はどれも数える。`todo/` の提案は、同じ識別子の
    承認済みチケットがどこにも無いときだけ数える（在れば改版の候補か書き損じ）。チケットが 2 つ以上
    残れば、どれが正か決まらない。

    どの置き場にも無い先行（閉じて片付いた別の親子のチケット）は引かない（`PRED_MISSING` になる）。
    手元の退避（`logs/archive/`）は PC ごとのもので、答えが割れるので読まない。
    """
    pool = _by_id(open_copies + review + closed)
    for t in proposals:
        if t.state == ticket_model.TODO and t.ticket not in pool:
            pool.setdefault(t.ticket, []).append(t)
    return pool


def align_imported(
    conf: settings.Settings, root: str, pool: dict[str, list[ticket_model.Ticket]]
) -> None:
    """親子のチケットの先行を、その親のブランチ上のチケットで読み直す。

    Chrome は先行を、参照の閉包にある親子のチケット `P` から引く。手元もそれに揃える。
    ただし通る向きには読み替えない（厳しくする向きだけ）。

    - 親子のチケットが決まらない（親のワークツリーが無い・HEAD のブランチ名がフォルダ名と
      合わない・途中の操作がある）:「親子のチケットが決まらない」にする（切り直しを案内する）
    - 親のワークツリーにその識別子のチケットが無い: 同じく「親子のチケットが決まらない」
      （親のブランチの外にしか無い）
    - 親のワークツリーのチケットが 1 つで、
      閉じていない（作業中・レビュー待ち・承認待ち）: それを採る
    - 親のワークツリーのチケットが閉じている、
      チケットが 2 つ以上: 前の対応表のまま（手元の全ツリーから
      引いた答え。前の対応表で満たしていなければ満たさないまま）
    """
    fams = standing.Families(conf, root)
    for ident, hits in list(pool.items()):
        if not hits:
            continue
        st = family_standing(conf, root, hits[0], fams)
        if st.stop:
            pool[ident] = [_undecided(hits[0], st.stop)]
            continue
        mine = [
            h for h in hits if st.home is not None and standing.same_tree(h.tree_root, st.home.root)
        ]
        if not mine:
            pool[ident] = [
                _undecided(
                    hits[0],
                    f"親のブランチ {st.family} のワークツリーに {ident} のチケットがありません"
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
    """満たしていない先行。子だけが先行を持つ。"""
    if not t.is_child:
        return []
    return [p for p in predecessor_states(t, pool) if not p.met]


def predecessor_problems(
    t: ticket_model.Ticket, pool: dict[str, list[ticket_model.Ticket]], approved_rel: str
) -> list[rules.Problem]:
    """承認を通さない先行の苦情。

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
                    f"先行 {p.ticket} が完了していない（いまは {p.label}）。{p.ticket} が "
                    f"{approved_rel}/{ticket_model.DONE}/ に入る（作業を終え、レビューが必要なら"
                    "ユーザのレビューが済む）まで承認しない。先行が不要なら "
                    "predecessors から削除して"
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
                    "predecessors から削除して出し直してください",
                )
            )
        elif p.state in (PRED_SELF, PRED_ANCESTOR, PRED_CYCLE):
            why = {
                PRED_SELF: "自分自身を先行に挙げている。自分が閉じるのを待つことはできない",
                PRED_ANCESTOR: "自分の親を先行に挙げている。親は子が全部閉じてから閉じるので、"
                "待っても満たされない",
                PRED_CYCLE: f"先行が循環している（{p.where}）。どれも他が閉じるのを待つので、"
                "待っても満たされない",
            }[p.state]
            problems.append(
                rules.Problem(
                    rules.SEVERITY_ERROR,
                    t.ticket,
                    f"先行 {p.ticket} は{PRED_LABELS[p.state]}。{why}。"
                    "predecessors から削除して出し直してください",
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
                    f"先行 {p.ticket} がどの置き場（todo/・doing/・review/・done/）にもありません。"
                    "閉じて片付いた親子のチケットなら、取り込み済みなので predecessors から"
                    "削除してください。表記の誤りなら直して出し直してください",
                )
            )
        else:
            problems.append(
                rules.Problem(
                    rules.SEVERITY_ERROR,
                    t.ticket,
                    f"先行 {p.ticket} が{p.label}。"
                    "どれが正か決まらないので満たしたとみなさない。"
                    "先に 1 つに決めてください"
                    "（先へ進んだ側を合流させるか、残ったワークツリーを片付ける）",
                )
            )
    return problems


def children_of(tickets: list[ticket_model.Ticket], parent_id: str) -> list[ticket_model.Ticket]:
    return [t for t in tickets if t.parent == parent_id]


def _reserved_project(t: ticket_model.Ticket) -> list[rules.Problem]:
    """`project:` がレイヤーの名前に予約してある表記なら error。"""
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
    """`project` が置き場と合っているか。

    プロジェクトを決めるのは提案を置いた場所。frontmatter の `project:` は
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
    # ないので、指せてはいけない。元の一覧で見ると通ってしまう。
    known = {
        p.name for p in tree.projects(conf.projects) if not settings.is_reserved_layer_name(p.name)
    }
    if t.project and t.project not in known:
        return [
            rules.Problem(
                rules.SEVERITY_ERROR,
                t.ticket,
                f"`project: {t.project}` は置き場 {conf.projects or '(無し)'} にありません。",
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

    親の計画か、親に固定したフェーズ定義（`phases:`）が壊れている（`after` の形・終端・最後の
    項の延期・延期の引き受け手、写しが無い・項の定義が写しに無い・定義の形と `kind`・引き受け手の
    レビュー。`workflow.errors`）ときも子を止める。壊れた計画では子の番号が何を待ち、どのレビューに
    含まれるかが決まらず、定義が引けなければ子の範囲の上限（定義の `scope`）も決まらない。
    ここに置くので、子の `--agree`・着手・書き込みが同じ答えで止まる。

    定義は親の写しから読み、`phases.yml` は読まない。`phases.yml` が壊れていても、承認のあとに
    直されても、進行中の親とその子はここで止まらない。

    保守用の子の印（`maintenance:`）の構造エラーもここで言う。統合の子（子・`phase: 0`・印の 3 つが
    そろうもの）だけは、`phase: 0` を親の計画の項と照合しない。
    """
    mark = t.maintenance_problem()
    if mark:
        return [rules.Problem(rules.SEVERITY_ERROR, t.ticket, mark)]
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
    if (
        parent.has_plan
        and t.phase is not None
        and not t.is_consolidate
        and parent.item_at(t.phase) is None
    ):
        return [
            rules.Problem(
                rules.SEVERITY_ERROR,
                t.ticket,
                f"{t.phase} 番目のフェーズは親 {parent.ticket} の計画にありません"
                f"（計画は {len(parent.numbered())} 番目まで）",
            )
        ]
    broken = workflow.errors(parent)
    if broken:
        return [
            rules.Problem(
                rules.SEVERITY_ERROR,
                t.ticket,
                f"親 {parent.ticket} の計画が不正なので、子も止める（{broken[0].detail}）。"
                "ユーザが親の承認済みチケットの計画か、固定した定義（phases:）を直してください",
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
    - 親の写し（`phases:`）と今の `phases.yml` の食い違い。進行中の親には効かないのが決まりで、
      判定で見ると承認のあとに定義を直しただけで進行中の親が止まる。`--lint` が warn で言う
    - フェーズの順序（`phase.order_problems`）。順序が狂っていても、その子の範囲を
      どの親で切り詰めるかは決まる。順序で止めるのは `held_phase`（レビュー待ちの間 `Agent` と
      シェルを止める）で、この検査とは別の仕組み。
      止めると、レビュー待ちの間その子が一切書けなくなり、レビュー待ちでも Write を通す形
      とは結果が食い違う。`--lint` が warn で言う
    - 範囲の超過。判定が親と定義の上限で切り詰めるので、止める理由が無い
    - 再開（`done/` から `doing/` へ手で戻す）で残った `completed_at` などの欄。ユーザの再開を
      止めないよう、`--lint` が warn で言うだけにする

    ここに入れる、中身だけで分かる矛盾。
    - `started_at` が無いのに `base_sha` がある。`start` は 2 つを一緒に書くので道具を通らない形で、
      `base_sha` は範囲外の検査とリスクの基準点に使われる
    - `workflow:` 欄がある。待ち方は計画の `after` から計算する
      もので、欄は読まない。書いてあると効くと読み手に思わせる
    - 計画と、親に固定した定義（`phases:`）の誤り（`after` の形・終端・最後の項の延期・延期の
      引き受け手、計画を持つのに写しが無い・項の定義が写しに無い・定義の形と `kind`・引き受け手の
      レビュー、子に書いた `phases:`。`workflow.errors`）。待ち方は計画から決まり、子がどの
      フェーズを待ち、どのレビューに含まれるかもそこで決まる。子の範囲の上限（定義の `scope`）は
      写しから決まるので、計画と定義が壊れていれば子の範囲も決まらない。手で動かした承認は
      `--agree` の検査を通らないので、ここで止める。止めるのは承認済みチケットそのものの形の誤りで、
      承認のあとに外の何か（`phases.yml` など）が動いて起きるものではない（直せるのはユーザが
      承認済みチケットを直すときだけ）。読むのは承認済みチケットだけで、`phases.yml` は読まない
    """
    problems = list(project_problems(t, pool, conf))
    problems.extend(content_problems(t))
    if not t.is_child and t.maintenance_problem():
        # 子の印の誤りは `child_problems` が言う。親が印を持つ形はここで止める。
        problems.append(rules.Problem(rules.SEVERITY_ERROR, t.ticket, t.maintenance_problem()))
    if t.is_child:
        missing = f"親 {t.parent} の承認済みチケットが作業中にありません（未承認か、閉じている）"
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
                "片方だけの状態は `start` を通して書かれたものではない。"
                "ユーザが承認済みチケットを確かめて直してください",
            )
        )
    if ticket_model.WORKFLOW_KEY in t.raw:
        problems.append(
            rules.Problem(
                rules.SEVERITY_ERROR,
                t.ticket,
                f"`{ticket_model.WORKFLOW_KEY}:` の欄がある。欄は読まない（待ち方は計画の項の "
                "`after` から決まる）。ユーザが欄を削除してください",
            )
        )
    problems.extend(workflow.errors(t))
    return problems


def resumed_fields(t: ticket_model.Ticket) -> list[str]:
    """作業中（`doing/`）なのに値が残っている、閉じるときに書く欄の名前。

    `finish` と `cancel` は `doing/` から動かすので、道具を通る限り残らない。残るのは、ユーザが
    `done/` から `doing/` へ手で戻した再開の形。判定では止めず、`--lint` が warn で言う。
    """
    values = {
        "completed_at": t.completed_at,
        "merged_sha": t.merged_sha,
        "cancelled_at": t.cancelled_at,
        "cancel_reason": t.cancel_reason,
        "finished_to": t.finished_to,
        "settled_by": t.settled_by,
    }
    return [name for name, value in values.items() if value]


def mark_blocked(conf: settings.Settings, kept: list[ticket_model.Ticket]) -> None:
    """判定が読む承認済みチケットに、信頼できない理由を付ける。

    理由を読むのは `phase_scope.scope_verdict` で、実行前チェック・実行後チェック・サブエージェント
    終了時チェックの 3 か所が同じ答えを引く。1 か所で付けるのは、3 か所が別々に検査を
    呼ぶと、同じ書き込みが実行前は通って実行後に範囲外と報告されるから。

    親を引く対応表は `kept` そのもの（`by_id`）で、判定が `parent` を引く索引と同じ。
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
