"""承認済みチケットの置き場。ユーザがチケットに合意したことの記録で、判定はここだけを読む。

置き場は approval、合意の手続きは agree。ここは承認済みチケット・フェーズのマーカー・子の記録が
どこにどう置かれているかを読み書きする。提案を集めてユーザに見せ、合意を受けて置き場へ動かす
手続き（`ccnavi --agree`）は `tickets/proposal/agree.py` にある。approval は agree を知らない。

マーカーと記録の読み書きは `approval_marks`、構造の検査（親子・統合先・先行・プロジェクト）は
`approval_checks`、承認の時刻（表示だけ）は `approval_times` に分けてある。
どれも approval を読まない。

チケットを動かす・書く操作（承認・欄の書き換え・閉じる・レビューへ送る・続きの子を起こす）は
`approval_ops` にある。こちらは approval を読む向きで、approval は approval_ops を知らない。

## なぜ承認済みチケットを正とするのか

チケットの提案はエージェントが書ける。判定が提案を直接読むと、範囲の外で
止められたエージェントが範囲を書き足して通れる。承認のときに承認済みチケットを置き、判定は
承認済みチケットだけを読む。書き足した提案は承認済みチケットに届かない。

## 承認済みチケットを守るのは置き場

承認済みチケットは `.ccnavi/approved/` に置く。ccnavi ディレクトリの下なので、組み込みの
保護がエージェントの書き込みを止める。

正とするのはこの置き場で、チケットの中の欄ではない。 そこに置けたのは書ける権限を持つ
ユーザだけだから。だから提案を手で `doing/` へ動かすことが、ターミナル・ボード・Chrome に続く承認の
経路になる。その経路は承認の画面を通らないので、承認のときにしかヒットしなかった構造の検査は
`blocking_problems` が判定の側で適用する。

承認はチケットの中身を変えない。提案のバイト列（改行も BOM も）をそのまま `doing/` へ動かし、
欄を書き足さない。
どの経路で承認しても承認済みチケットは提案とバイト単位で同じになり、
欄の有無から「承認が途中で止まった」と読み違える形が無くなる。

## チケットは 1 本のファイルで、コピーを持たない

承認は `wip/proposals/todo/` の提案を `.ccnavi/approved/doing/` へ動かす。
エージェントが打つ `finish` は `doing/` から `wip/proposals/review/`
（レビュー要）か `.ccnavi/approved/done/`（不要）へ動かし、ユーザがレビューを済ませると
`review/` から `done/` へ動く。ユーザが動かす向きは `.ccnavi/approved/` へ、エージェントが
動かす向きは `wip/proposals/` へ。

## 閉じる向きだけは承認が要らない

範囲が消える向きなので、エージェントが動かしても危険は増えない。逆に承認済みチケットを
`done/` から戻す（再開）のはユーザの手でやる。

## フェーズのマーカー

`phases/<親>/<N>.<種類>` に、依頼・レビュー済み・省略・通知済みのマーカーを置く。
レビューが済むまで止める判定（tickets/phase/gates.py）はこれを見る。中身は JSON 1 つで、
いつ誰が置いたかが入る。

## 先行関係は計画から都度計算する

計画を持つ親の先行関係（`workflow.compute` の結果）は、承認済みチケットを読むたびに計画のステップの
`after` から計算して `Ticket.workflow` に入れる（`load_copy`）。ファイルにもチケットの欄にも
持たない。承認済みチケットの計画は承認済みの置き場にあり、変えられるのは改版（`--agree`）と
ユーザの手だけなので、計算の元も固定されている。`phases.yml` は計算の入力にならないので、承認の
あとに直しても進行中の親の先行関係は変わらない。手で動かした承認も同じに計画の `after` で読む。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from ...infra import fsio, settings, tree
from ...policy import rules
from ..config import phasetypes, workflow
from ..model import standing, ticket_fold, ticket_model
from ..model import ticket as ticket_mod
from . import checks as approval_checks

# 承認済みチケットの下の置き場。作業中（判定が読む）、閉じた、マーカーと記録。
DOING_DIR = ticket_model.DOING
DONE_DIR = ticket_model.DONE
# 続きの子の目印（トップレベルの欄）。
FOLLOWUP_KEY = "followup_of"


def copy_path(approved_dir: str, ticket_id: str) -> str:
    """作業中の承認済みチケット（`doing/`）のパス。判定が読むのはここだけ。"""
    return os.path.join(approved_dir, DOING_DIR, ticket_id + ".md")


def closed_path(approved_dir: str, ticket_id: str) -> str:
    """閉じた承認済みチケット（`done/`）のパス。"""
    return os.path.join(approved_dir, DONE_DIR, ticket_id + ".md")


def review_path(tree_root: str, tickets_rel: str, ticket_id: str) -> str:
    """レビュー待ちのチケット（提案の置き場の `review/`）のパス。"""
    base = os.path.join(tree_root, tickets_rel.replace("/", os.sep))
    return os.path.join(base, ticket_model.REVIEW, ticket_id + ".md")


def copies(approved_dir: str, closed: bool = False) -> tuple[list[ticket_model.Ticket], list[str]]:
    """承認済みチケットの一覧。closed なら閉じたもの。2 つめは読めなかったものの説明。

    `state` を添える。閉じたものは取り消しの欄で `done` と `cancelled` に分ける。
    """
    directory = os.path.join(approved_dir, DONE_DIR if closed else DOING_DIR)
    return _load_dir(directory)


def _load_dir(
    directory: str, require_record: bool = False
) -> tuple[list[ticket_model.Ticket], list[str]]:
    found, unread = _load_dir_detail(directory, require_record)
    return found, [message for _, message in unread]


def _load_dir_detail(
    directory: str, require_record: bool = False
) -> tuple[list[ticket_model.Ticket], list[tuple[str, str]]]:
    """`_load_dir` の中身。読めなかったものを (パス, 説明) で返す（置き場ごとならパスは置き場）。"""
    try:
        # fsio を通す。承認の plan（書き込みを溜める段）の中では、
        # 同じ承認で動かした後の置き場を読む。
        names = sorted(fsio.listdir(directory))
    except FileNotFoundError:
        return [], []
    except OSError as exc:
        return [], [(directory, f"承認済みチケットの置き場を読めない ({exc})")]
    found: list[ticket_model.Ticket] = []
    unread: list[tuple[str, str]] = []
    for name in names:
        if not name.endswith(".md"):
            continue
        path = os.path.join(directory, name)
        ticket, reason = load_copy(path, require_record)
        if ticket is None:
            unread.append((path, f"承認済みチケット {path} を読めない（{reason}）"))
            continue
        if ticket.ticket != name[:-3]:
            unread.append(
                (path, f"承認済みチケット {path} の識別子 {ticket.ticket} がファイル名と違う")
            )
            continue
        ticket.state = state_of(directory, ticket)
        found.append(ticket)
    return found, unread


def unreadable_copies(conf: settings.Settings, tree_root: str) -> list[tuple[str, str]]:
    """そのツリーの読めない承認済みチケット（作業中・閉じた・レビュー待ち）を (パス, 説明) で。"""
    approved = settings.approved_dir(conf, tree_root)
    out: list[tuple[str, str]] = []
    for directory, require in (
        (os.path.join(approved, DOING_DIR), False),
        (os.path.join(approved, DONE_DIR), False),
        (os.path.join(tree_root, conf.tickets.replace("/", os.sep), ticket_model.REVIEW), True),
    ):
        out.extend(_load_dir_detail(directory, require)[1])
    return out


def state_of(directory: str, ticket: ticket_model.Ticket) -> str:
    """置き場の名前から状態を引く。`done/` は取り消しの欄で 2 つに分ける。"""
    name = os.path.basename(directory)
    if name == DONE_DIR:
        return ticket_model.CANCELLED if ticket.cancelled_at else ticket_model.DONE
    return name


def load_copy(path: str, require_record: bool = False) -> tuple[ticket_model.Ticket | None, str]:
    """承認済みチケットを 1 本読む。2 つめは読めなかった理由。

    理由を返すのは、読めない承認済みチケットを `--lint` が名指しするため。
    「読めない」だけでは、BOM のような目に見えない原因に気づけない。

    `require_record` は `completed_at`（空でない値）を必須にするか。`.ccnavi/approved/` は
    組み込みの保護がエージェントの書き込みを止めるので、置き場だけで承認と言える。
    `wip/proposals/review/` はエージェントが書ける側にあるので、そこは `finish` が書く
    `completed_at` を 2 つめの保護として求める（`review_all`）。保護が 1 つしか無い置き場で
    欄まで外すと、組み込みの deny に止められずに置かれたファイルが承認済みとして読まれる。

    計画を持つ親には、計画のステップの `after` から計算した先行関係（`workflow.compute`）と、
    親に固定したフェーズ定義（`phases:` を読んだもの。`phasetypes.types_of`）を入れる。
    チケットの中に `workflow:` 欄があれば `blocking_problems` が止める。
    """
    ticket, problems = ticket_mod.load(path)
    if ticket is None:
        detail = problems[0].detail if problems else "チケットとして読めない"
        return None, detail
    # 承認を正とするのは置き場で、`.ccnavi/approved/` は組み込みの保護がエージェントの書き込みを
    # 止める。欄は無いので、親子・計画・置き場の検査は `blocking_problems` が判定の側で適用する。
    if require_record and not ticket.completed_at:
        return None, "`completed_at` がありません。`finish` を通っていない"
    if not ticket.is_child and ticket.has_plan:
        ticket.workflow = workflow.compute(ticket)
    # 親に固定したフェーズ定義（`phases:`）を読んで持たせる。承認のあとの判定はこれを読み、
    # `phases.yml` を読まない。形の誤りは読めない定義を入れないだけで、ここでは落とさない
    # （`blocking_problems` が止める）。
    phasetypes.types_of(ticket)
    # tree は「どのツリーで見つけたか」。scan_all が入れ直す。
    ticket.tree = ""
    return ticket, ""


def trees(conf: settings.Settings, root: str) -> list[tree.Tree]:
    """承認済みチケットを持ちうるツリー全部。ワークスペース、プロジェクト、ワークツリー。"""
    return [
        tree.main_tree(root),
        *tree.projects(conf.projects),
        *tree.worktrees(root, conf.projects),
    ]


def scan_all(
    conf: settings.Settings, root: str, closed: bool = False
) -> tuple[list[ticket_model.Ticket], list[str]]:
    """全ツリーの承認済みチケットを、重複をまとめずに集める。

    承認済みチケットは親チケットのブランチに含まれるので、そこから切った子のワークツリーにも
    同じものが checkout されている。まとめないほうは、ボードが「どこにコピーがあるか」を
    見せるために使う。
    """
    found: list[ticket_model.Ticket] = []
    notes: list[str] = []
    for t in trees(conf, root):
        got, complaints = copies(settings.approved_dir(conf, t.root), closed)
        for c in got:
            c.tree, c.tree_root, c.project = t.name, t.root, t.project
        found.extend(got)
        notes.extend(complaints)
    return found, notes


def review_all(conf: settings.Settings, root: str) -> tuple[list[ticket_model.Ticket], list[str]]:
    """全ツリーのレビュー待ち（提案の置き場の `review/`）を、重複をまとめずに集める。

    ここに在るのは承認済みチケットが `finish` で動いてきたもの。`completed_at`（空でない値）を
    持たないファイルは読まない。この置き場はエージェントが書ける側にあり、保護は組み込みの
    deny 1 つなので、`finish` が書く欄を 2 つめの保護として残す（`load_copy`）。
    """
    found: list[ticket_model.Ticket] = []
    notes: list[str] = []
    for t in trees(conf, root):
        directory = os.path.join(t.root, conf.tickets.replace("/", os.sep), ticket_model.REVIEW)
        got, complaints = _load_dir(directory, require_record=True)
        for c in got:
            c.tree, c.tree_root, c.state = t.name, t.root, ticket_model.REVIEW
            c.project = t.project
        found.extend(got)
        notes.extend(complaints)
    return found, notes


@dataclass
class Raw:
    """全ツリーの承認済みチケットを、まとめる前のまま読んだもの（作業中・閉じた・レビュー待ち）。

    1 回の判定の中で `scan`・`scan(closed=True)`・`scan_review`・`scan_proposals` を続けて呼ぶ
    呼び手が、同じ置き場を何度も読まないために持ち回る（`read_raw`）。覚えておいたものではなく、その
    呼び出しの中で今しがた読んだもの（`all_tickets` と同じ考え方）。
    """

    open_all: list[ticket_model.Ticket]
    open_notes: list[str]
    closed_all: list[ticket_model.Ticket]
    closed_notes: list[str]
    review: list[ticket_model.Ticket]
    review_notes: list[str]

    @property
    def everything(self) -> list[ticket_model.Ticket]:
        return self.open_all + self.closed_all + self.review


def read_raw(conf: settings.Settings, root: str) -> Raw:
    """承認済みチケットの置き場を 1 度ずつ読む（`Raw`）。"""
    open_all, open_notes = scan_all(conf, root)
    closed_all, closed_notes = scan_all(conf, root, closed=True)
    review, review_notes = review_all(conf, root)
    return Raw(open_all, open_notes, closed_all, closed_notes, review, review_notes)


def scan(
    conf: settings.Settings, root: str, closed: bool = False, raw: Raw | None = None
) -> tuple[list[ticket_model.Ticket], list[str]]:
    """判定と承認が読む承認済みチケット。正とするツリーの側だけを残す。

    正とするのは親のツリー（親自身なら自分のツリー）。提案の `dedupe` と違い、そこに無ければ
    外す。子のワークツリーに checkout されているのは切った時点の版なので、親のツリーで
    閉じたあとも開いた版が残る。「正とする側に無ければ全部残す」にすると、閉じたチケットが
    開いたものとして復活する。親のツリーがその識別子をどの置き場（作業中・レビュー待ち・
    閉じた）にも持っていなければ元ツリー（ワークスペースルート。プロジェクトのチケットなら
    そのプロジェクト）の側を採り、そこにも無いときと、元ツリーより先の置き場に在るチケットが
    あるときだけ、見つかった側を全部残す（`ticket_fold.fold` と同じ順・同じ条件）。

    返す前に `mark_blocked` が「信頼できない理由」を付ける。承認のときにしか
    ヒットしなかった構造の検査を、判定の側でも適用するため（置き場を手で動かす承認は
    `--agree` を通らない）。

    `raw` は呼び手が `read_raw` で読んだもの。渡せば置き場を読み直さない。
    """
    if raw is not None:
        found = raw.closed_all if closed else raw.open_all
        notes = raw.closed_notes if closed else raw.open_notes
        kept = _authoritative(found, raw.everything)
        if not closed:
            approval_checks.mark_blocked(conf, kept)
            approval_checks.mark_imported(conf, root, kept)
        return kept, list(notes)
    found, notes = scan_all(conf, root, closed)
    # 読んだ側を `all_tickets` に渡す。渡さないと、この同じ式の中でまったく同じ
    # `scan_all` をもう 1 度呼ぶことになる（下記）。
    if closed:
        everything = all_tickets(conf, root, closed_all=found)
    else:
        everything = all_tickets(conf, root, open_all=found)
    kept = _authoritative(found, everything)
    if not closed:
        # 判定が読むのは作業中の側だけ。閉じたものに理由は要らない。
        approval_checks.mark_blocked(conf, kept)
        approval_checks.mark_imported(conf, root, kept)
    return kept, notes


def scan_review(
    conf: settings.Settings, root: str, raw: Raw | None = None
) -> tuple[list[ticket_model.Ticket], list[str]]:
    """レビュー待ちのチケット。正とするツリーの側だけを残す（`scan` と同じ規則）。"""
    if raw is not None:
        kept = _authoritative(raw.review, raw.everything)
        approval_checks.mark_imported(conf, root, kept)
        return kept, list(raw.review_notes)
    found, notes = review_all(conf, root)
    kept = _authoritative(found, all_tickets(conf, root, review=found))
    approval_checks.mark_imported(conf, root, kept)
    return kept, notes


def scan_proposals(
    conf: settings.Settings,
    root: str,
    everything: list[ticket_model.Ticket] | None = None,
) -> tuple[list[ticket_model.Ticket], list[ticket_mod.Problem]]:
    """提案（`todo/` と `review/`）を、承認済みチケットがどのツリーにあるかに従ってまとめる。

    承認済みの識別子の提案は、承認済みチケットで正とするツリー（親のツリー → 元ツリー →
    決まらない。`ticket_fold.authority`）を決め、そのツリーに在るものだけを残す。
    承認で正とするツリーの `todo/` が消えても、承認の前に切ったワークツリーには `todo/` の提案が
    残る。提案だけでまとめると、その古い提案が承認待ちや改版（巻き戻し）として読まれる。
    承認済みチケットの側（`_authoritative`）は「正とするツリーに無ければ外す」なので、それと揃える。

    `everything` は承認済みチケットの全部（`Raw.everything` の形）。呼び手が読んであれば
    渡す。取り込み済みの親子のチケットも、正とするのは親のブランチ `P` のワークツリー（名前 `P`）
    なので、同じ規則で決まる（理由を付けるのは `mark_imported` と `family_problems`）。
    """
    kept, problems, _ = read_proposals(conf, root, everything)
    return kept, problems


def read_proposals(
    conf: settings.Settings,
    root: str,
    everything: list[ticket_model.Ticket] | None = None,
) -> tuple[
    list[ticket_model.Ticket], list[ticket_mod.Problem], list[tuple[ticket_model.Ticket, str]]
]:
    """`scan_proposals` と同じもの（残した提案と苦情）。

    正とするツリーの外に残った `todo/` の提案（`stale_proposals`）を 3 つ目に添える。
    """
    if everything is None:
        everything = all_tickets(conf, root)
    found, problems = ticket_mod.scan_all(root, conf.tickets, conf.projects)
    kept = ticket_fold.dedupe(found, everything)
    return kept, problems, stale_proposals(found, kept, everything)


def stale_proposals(
    found: list[ticket_model.Ticket],
    kept: list[ticket_model.Ticket],
    everything: list[ticket_model.Ticket],
) -> list[tuple[ticket_model.Ticket, str]]:
    """承認済みの識別子の `todo/` の提案のうち、正とするツリーの外に在るので読まなかったもの。

    (提案, 正とするツリーの名前) のリスト。`found` はまとめる前の提案、`kept` は残した側。
    正とするツリーの `todo/` に同じ中身の提案が在るもの（親のツリーの改版が子のワークツリーに
    入っているだけ）は入れない。`review/` の提案も入れない（置き場が動いた結果で、承認の対象に
    ならない）。
    """
    kept_ids = {id(t) for t in kept}
    settled = approval_checks._by_id(everything)
    out: list[tuple[ticket_model.Ticket, str]] = []
    for ticket_id, hits in approval_checks._by_id(found).items():
        copies = settled.get(ticket_id)
        if not copies:
            continue
        where = ticket_fold.authority(copies)
        if where is None:
            continue
        there = [t for t in hits if id(t) in kept_ids and t.state == ticket_model.TODO]
        for t in hits:
            if t.state != ticket_model.TODO or id(t) in kept_ids or t.tree == where:
                continue
            if any(_same_todo(t, u) for u in there):
                continue
            out.append((t, where))
    return out


def _same_todo(t: ticket_model.Ticket, u: ticket_model.Ticket) -> bool:
    """同じ中身の `todo/` の提案か。計画まで比べる。"""
    return t.plan == u.plan and t.feedback == u.feedback and t.raw == u.raw


def revision_elsewhere(t: ticket_model.Ticket, current: ticket_model.Ticket | None) -> bool:
    """正とするツリーの外の `todo/` にある提案が、計画・コピー・範囲の違う親の版かどうか。

    場所違いの改版か、巻き戻しの元。

    条件は承認の改版（`agree.waiting`）と同じく、作業中の承認済みチケットがある親で、計画・
    フィードバック計画・コピー（`phases:`）・範囲のどれかが違うもの（`plan_differs`）。
    """
    return current is not None and not t.is_child and t.has_plan and plan_differs(t, current)


def plan_signature(t: ticket_model.Ticket, part: str) -> tuple | None:
    """計画の比べる形。ステップごとの「定義・`review`・推移的に辿った先行（`workflow.compute`）」の並び。

    `after` の書き方が違っても先行が同じなら違いにしない（`PlanItem.__eq__` も `after` を見ない）。
    `after: [2]` と、`--explain` の「先行 1, 2」をコピーした `after: [1, 2]` は同じ先行。
    `part` は `plan` か `feedback`。フィードバック計画が無い（None）ことと空（`[]`）は分ける。
    """
    if part == "feedback" and t.feedback is None:
        return None
    wf = workflow.compute(t)
    start = 1 if part == "plan" else len(t.plan) + 1
    items = t.plan if part == "plan" else (t.feedback or [])
    return tuple(
        (item.type, item.review, tuple(wf.waits.get(n, [])))
        for n, item in enumerate(items, start=start)
    )


def phases_signature(t: ticket_model.Ticket) -> tuple | None:
    """親のコピー（`phases:`）の比べる形。定義を読んだ形（`PhaseType.key()`）を id の順に並べる。

    YAML の書き方・キーの順・既定値を省いた書き方の違いは違いにしない。読めない定義があれば、
    読んだままの値で比べる（形の誤りを直しただけの提案も違いとして拾う）。コピーが無ければ None。
    """
    if not phasetypes.has_copy(t):
        return None
    types, problems = phasetypes.read_copy(t.phases_raw)
    if any(p.severity == rules.SEVERITY_ERROR for p in problems):
        return ("unread", repr(t.phases_raw))
    return tuple(sorted((ident, pt.key()) for ident, pt in types.items()))


def scope_signature(t: ticket_model.Ticket) -> tuple:
    """チケットの範囲（deny / ask / allow の項）を比べる形。項の並び順と重複は区別しない。"""
    return tuple(sorted({(e.decision, e.glob, e.regex) for e in t.entries}))


def plan_differs(proposal: ticket_model.Ticket, current: ticket_model.Ticket) -> bool:
    """提案の計画（全体計画かフィードバック計画）・コピー（`phases:`）・範囲が承認済みチケットと違うか。
    改版の条件。

    計画は `plan_signature` で比べる。先行関係は計画から決まるので、先行関係だけの改版は無い。
    コピーは `phases_signature` で比べる。計画が同じでコピーだけが違う提案も改版の候補にする
    （`--plan-order <親> --fill-phases` で今の `phases.yml` に揃えた提案を出せるように）。
    範囲は `scope_signature` で比べる。範囲だけが違う提案も改版の候補にする。
    """
    return (
        phases_signature(proposal) != phases_signature(current)
        or scope_signature(proposal) != scope_signature(current)
        or any(
            plan_signature(proposal, part) != plan_signature(current, part)
            for part in ("plan", "feedback")
        )
    )


def revision_elsewhere_text(
    conf: settings.Settings,
    root: str,
    t: ticket_model.Ticket,
    where: str,
    fams: standing.Families | None = None,
) -> str:
    """正とするツリーの外に在る計画・コピー・範囲の違う版の案内。ファイルの場所と、改版を書く置き場を名指しする。

    場所はどちらもワークスペースルートからのパスで出す（ツリーの名前ではなく、
    `.claude/worktrees/<親>` や `projects/<名前>`）。`--lint` と `--agree`（`--verify` も）が
    同じ文面を出す。取り込み済みの親子のチケットでは、親子のチケットが決まらないなら止まった理由と
    手順（`family_stop_text`。`family_problems` と同じ文面）を足す。決まっていれば、正とする
    ツリーが親のワークツリー（`family_problems` が言う書く場所と同じ）なので、場所は繰り返さず
    承認を頼む前に `approval-request` で親のブランチへ送る手順だけを足す。
    """
    rel = os.path.relpath(t.path, root).replace(os.sep, "/")
    place = tree_path(conf, root, where, t.project)
    todo = f"{conf.tickets}/{ticket_model.TODO}/"
    text = (
        f"{t.ticket} の計画・コピー・範囲の違う提案が {rel} に在るが、"
        f"正とするツリー（{place}）の外なので承認の対象にならない。"
        f"改版なら {place} の {todo} に書き、古い版なら削除してください"
    )
    fams = fams or standing.Families(conf, root)
    st = approval_checks.family_standing(conf, root, t, fams)
    if st.stop:
        text += "。親子のチケットが止まっている: " + approval_checks.family_stop_text(root, st)
    elif st.home is not None:
        propose = f"{settings.script_command(root, 'ccnavi-ticket.sh')} approval-request {t.ticket}"
        text += f"。書いたら '{propose}' で親のブランチへ送ってから承認を頼んでください"
    return text


def tree_path(conf: settings.Settings, root: str, name: str, project: str = "") -> str:
    """ツリーの名前を、ワークスペースルートからのパスに直す。ルート自身は「ワークスペースルート」。"""
    found = [t for t in trees(conf, root) if t.name == name]
    pick = next((t for t in found if t.project == project), found[0] if found else None)
    if pick is None:
        return name or "ワークスペースルート"
    rel = os.path.relpath(pick.root, root).replace(os.sep, "/")
    return "ワークスペースルート" if rel == "." else rel


def all_tickets(
    conf: settings.Settings,
    root: str,
    *,
    open_all: list[ticket_model.Ticket] | None = None,
    closed_all: list[ticket_model.Ticket] | None = None,
    review: list[ticket_model.Ticket] | None = None,
) -> list[ticket_model.Ticket]:
    """作業中・レビュー待ち・閉じたの全部を、重複をまとめずに。正とするツリーを決めるために使う。

    3 つは呼び手が持ち込める。**読んだものを覚えておくのではなく、
    同じ呼び出しの中で今しがた読んだものを渡してもらう仕組み。
    ** `scan` は `scan_all` を呼んだ直後にここを呼ぶので、
    渡さないと同じ引数の `scan_all` が 1 つの式の中で 2 回走る。
    置き場のチケットは 1 本ずつ YAML として解析されるので、
    この重複はチケットの本数にそのまま比例する。

    渡すのは「自分が読んだ側」だけで、残りはここで読む。読む範囲も読む順も変わらない。
    読んだものを覚えておかないので、判定の途中でファイルが動く経路（`ticket finish` が親を閉じた後、
    `settle_review` が子を動かした後）でも、持ち込まなかった側は読み直される。
    """
    if open_all is None:
        open_all, _ = scan_all(conf, root)
    if closed_all is None:
        closed_all, _ = scan_all(conf, root, closed=True)
    if review is None:
        review, _ = review_all(conf, root)
    return open_all + closed_all + review


def _authoritative(
    found: list[ticket_model.Ticket], everything: list[ticket_model.Ticket]
) -> list[ticket_model.Ticket]:
    # 正とするのは親のツリーだけ。無ければ決まらない（全部残す）。ほかのツリーのコピーで代用しない。
    # リポジトリをまたいだ衝突はまとめない。違うチケットなので、正とする側を決めるとどちらかが
    # 気づかないうちに消え、`--lint` の「複数のリポジトリにある」も出なくなる。決め方は
    # `ticket_fold.authority` の 1 つで、承認済みの識別子の提案（`ticket_fold.fold`）も
    # 同じ関数で決める。
    seen = approval_checks._by_id(everything)
    kept: list[ticket_model.Ticket] = []
    for ticket_id, hits in approval_checks._by_id(found).items():
        where = ticket_fold.authority(seen.get(ticket_id) or hits)
        if where is None:
            kept.extend(hits)
        else:
            kept.extend(t for t in hits if t.tree == where)
    return kept


class NoParentWorktree(ValueError):
    """親のワークツリーが無い（HEAD のブランチ名がフォルダ名と合わない・止まっている）。

    ほかのツリーのコピーで代用しないので、書く先が決まらない。
    """


def home_dir(
    conf: settings.Settings,
    root: str,
    ticket_id: str,
    parent: str,
    project: str | None = None,
) -> str:
    """この識別子の承認済みチケットを置くツリーの置き場。親のワークツリーの置き場だけ。

    書く先も読む先も 1 つに決めるためのもの。子のワークツリーにも checkout されるが、
    そこへ書くと同じ識別子の承認済みチケットが 2 通りになる。親のワークツリー以外へは切り替えない。
    親のワークツリーが無い（HEAD のブランチ名がフォルダ名と合わない場合を含む）・親子のチケットが
    止まっているときは `NoParentWorktree`（ほかのツリーのコピーで代用しない）。

    `project` はチケットのリポジトリ（ワークスペース自身なら空）。分からなければ None で、
    同じ名前の親のワークツリーのあるリポジトリを全部探す（同じ識別子が 2 つの
    リポジトリにあれば決めない）。
    """
    home = parent or ticket_id
    st = standing.standing_any(conf, root, home, project)
    if st.home is None or st.stop:
        raise NoParentWorktree(
            st.stop or f"親のワークツリー（.claude/worktrees/{home}）がありません"
        )
    return settings.approved_dir(conf, st.home.root)


def home_dir_read(
    conf: settings.Settings,
    root: str,
    ticket_id: str,
    parent: str,
    project: str | None = None,
) -> str:
    """`home_dir` の読む側。親のワークツリーが決まらないときは、存在しない置き場のパスを返す。

    読む側（マーカー・記録）は、親のワークツリーが決まらなければ「何も無い」と読む。
    決まらないのは、親のワークツリーが無い・HEAD のブランチが違う・途中の操作がある、
    のどれでも同じ。フォルダが実在しても（別のブランチを checkout したままなど）、
    その中のマーカーは読まない。
    ほかのツリーのコピーでは代用しない。書く側は `home_dir` を使う（決まらなければ止まる）。
    返したパスへは書かない（書くとそこに置き場ができる）。
    """
    home = parent or ticket_id
    try:
        return home_dir(conf, root, ticket_id, parent, project=project)
    except NoParentWorktree:
        return nowhere(conf, root, home)


def nowhere(conf: settings.Settings, root: str, home: str) -> str:
    """親のワークツリーが決まらないときに読む、存在しない置き場のパス。

    ワークツリーの置き場（`.claude/worktrees/`）の外の、state の下の決まった名前にする。
    誰も作らないので、読む側は「何も無い」と読む。
    """
    base = conf.state or os.path.join(root, settings.DEFAULT_STATE)
    return settings.approved_dir(conf, os.path.join(base, "no-parent-worktree", home))


def source_path(t: ticket_model.Ticket) -> str:
    """提案の、そのリポジトリ（ツリー）からの相対パス。区切りは "/"。

    手元と Chrome で承認済みチケットの中身を同じにするため、絶対パスは書かない。ツリーの外にある
    （ツリーが分からない）ときだけ、パスをそのまま返す。
    """
    if t.tree_root and t.path:
        try:
            rel = tree.relative(tree.Tree(t.tree, t.tree_root), t.path)
        except ValueError:
            # Windows で別のドライブ（relpath が相対を作れない）。パスのまま返す。
            return t.path
        if rel and not rel.startswith("../") and rel != "..":
            return rel
    return t.path
