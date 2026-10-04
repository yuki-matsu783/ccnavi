"""承認済みチケットの置き場。ユーザがチケットに合意したことの記録で、判定はここだけを読む。

置き場は approval、合意の手続きは agree。ここは承認済みチケット・フェーズのマーカー・子の記録が
どこにどう置かれているかを読み書きする。提案を集めてユーザに見せ、合意を受けて置き場へ動かす
手続き（`ccnavi --agree`）は `agree.py` にある。approval は agree を知らない。

## なぜ承認済みチケットを本物とするのか

チケットの提案はエージェントが書ける。判定が提案を直接読むと、範囲の外で
止められたエージェントが範囲を書き足して通れる。承認のときに承認済みチケットを置き、判定は
承認済みチケットだけを読む。書き足した提案は承認済みチケットに届かない。

## 承認済みチケットを守るのは置き場

承認済みチケットは `.ccnavi/approved/` に置く。ccnavi ディレクトリの下なので、組み込みの
保護がエージェントの書き込みを止める。

**本物とするのはこの置き場で、`ccnavi_approved` の欄ではない。** 欄は承認の記録で、
持たないチケットも承認済みとして読む。そこに置けたのは書ける権限を持つユーザだけだから。
だから提案を手で `doing/` へ動かすことが、端末とボードに続く 3 つめの承認の経路になる。
その経路は承認の画面を通らないので、承認のときにしか当たらなかった構造の検査は
`blocking_problems` が判定の側で当てる。

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
レビューが済むまで止める判定（phase.py）はこれを見る。中身は JSON 1 つで、いつ誰が置いたかが入る。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, replace

from ..infra import fsio, settings, tree
from ..policy import rules
from . import archive, flow, history, syncstate, workflow
from . import ticket as ticket_mod

# 承認済みチケットの下の置き場。作業中（判定が読む）、閉じた、マーカーと記録。
DOING_DIR = ticket_mod.DOING
DONE_DIR = ticket_mod.DONE
PHASES_DIR = "phases"

# フェーズのマーカーの種類。
MARK_REQUESTED = "requested"
MARK_REVIEWED = "reviewed"
MARK_SKIPPED = "skipped"
# pending は「終わったと 1 度伝えた」のマーカー。同じ文を呼び出しごとに繰り返さないため。
MARK_PENDING = "pending"
MARKS = (MARK_REQUESTED, MARK_REVIEWED, MARK_SKIPPED, MARK_PENDING)


def copy_path(approved_dir: str, ticket_id: str) -> str:
    """作業中の承認済みチケット（`doing/`）のパス。判定が読むのはここだけ。"""
    return os.path.join(approved_dir, DOING_DIR, ticket_id + ".md")


def closed_path(approved_dir: str, ticket_id: str) -> str:
    """閉じた承認済みチケット（`done/`）のパス。"""
    return os.path.join(approved_dir, DONE_DIR, ticket_id + ".md")


def review_path(tree_root: str, tickets_rel: str, ticket_id: str) -> str:
    """レビュー待ちのチケット（提案の置き場の `review/`）のパス。"""
    base = os.path.join(tree_root, tickets_rel.replace("/", os.sep))
    return os.path.join(base, ticket_mod.REVIEW, ticket_id + ".md")


def copies(approved_dir: str, closed: bool = False) -> tuple[list[ticket_mod.Ticket], list[str]]:
    """承認済みチケットの一覧。closed なら閉じたもの。2 つめは読めなかったものの説明。

    `state` を添える。閉じたものは取り消しの欄で `done` と `cancelled` に分ける。
    """
    directory = os.path.join(approved_dir, DONE_DIR if closed else DOING_DIR)
    return _load_dir(directory)


def _load_dir(
    directory: str, require_record: bool = False
) -> tuple[list[ticket_mod.Ticket], list[str]]:
    found, unread = _load_dir_detail(directory, require_record)
    return found, [message for _, message in unread]


def _load_dir_detail(
    directory: str, require_record: bool = False
) -> tuple[list[ticket_mod.Ticket], list[tuple[str, str]]]:
    """`_load_dir` の中身。読めなかったものを (パス, 説明) で返す（置き場ごとならパスは置き場）。"""
    try:
        # fsio を通す。承認の plan（書き込みを溜める段）の中では、
        # 同じ承認で動かした後の置き場を読む。
        names = sorted(fsio.listdir(directory))
    except FileNotFoundError:
        return [], []
    except OSError as exc:
        return [], [(directory, f"承認済みチケットの置き場を読めない ({exc})")]
    found: list[ticket_mod.Ticket] = []
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
        (os.path.join(tree_root, conf.tickets.replace("/", os.sep), ticket_mod.REVIEW), True),
    ):
        out.extend(_load_dir_detail(directory, require)[1])
    return out


def state_of(directory: str, ticket: ticket_mod.Ticket) -> str:
    """置き場の名前から状態を引く。`done/` は取り消しの欄で 2 つに分ける。"""
    name = os.path.basename(directory)
    if name == DONE_DIR:
        return ticket_mod.CANCELLED if ticket.cancelled_at else ticket_mod.DONE
    return name


def load_copy(path: str, require_record: bool = False) -> tuple[ticket_mod.Ticket | None, str]:
    """承認済みチケットを 1 本読む。2 つめは読めなかった理由。

    理由を返すのは、読めない承認済みチケットを `--lint` が名指しするため。
    「読めない」だけでは、BOM のような目に見えない原因に気づけない。

    `require_record` は `ccnavi_approved` の欄を必須にするか。`.ccnavi/approved/` は
    組み込みの保護がエージェントの書き込みを止めるので、置き場だけで承認と言える。
    `wip/proposals/review/` はエージェントが書ける側にあるので、そこは欄を求め続ける
    （`review_all`）。保護が 1 つしか無い置き場で欄まで外すと、組み込みの deny に止められずに
    置かれたファイルが承認済みとして読まれる。
    """
    ticket, problems = ticket_mod.load(path)
    if ticket is None:
        detail = problems[0].detail if problems else "チケットとして読めない"
        return None, detail
    # `ccnavi_approved` は承認の記録であって、承認そのものではない。本物とするのは
    # 置き場で、`.ccnavi/approved/` は組み込みの保護がエージェントの書き込みを止める。
    # 欄を必須にすると、端末もボードも無いユーザが置き場を動かして承認する経路が使えなくなる。
    # 欄が無いぶんの検査（親子・計画・置き場）は `blocking_problems` が判定の側で当てる。
    meta = ticket.raw.get(ticket_mod.APPROVAL_KEY)
    if require_record and not isinstance(meta, dict):
        return None, f"`{ticket_mod.APPROVAL_KEY}` の欄が無い。承認を通っていない"
    if isinstance(meta, dict):
        ticket.approved_at = str(meta.get("approved_at") or "")
        ticket.source_tree = str(meta.get("source_tree") or "")
        ticket.source_path = str(meta.get("source_path") or "")
    # tree は「どのツリーで見つけたか」。scan_all が入れ直す。source_tree（どのツリーの
    # 提案をコピーしたか）とは違うもので、子のワークツリーの checkout では食い違う。
    ticket.tree = ticket.source_tree
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
) -> tuple[list[ticket_mod.Ticket], list[str]]:
    """全ツリーの承認済みチケットを、重複をまとめずに集める。

    承認済みチケットは親チケットのブランチに乗るので、そこから切った子のワークツリーにも
    同じものが checkout されている。まとめないほうは、ボードが「どこにコピーがあるか」を
    見せるために使う。
    """
    found: list[ticket_mod.Ticket] = []
    notes: list[str] = []
    for t in trees(conf, root):
        got, complaints = copies(settings.approved_dir(conf, t.root), closed)
        for c in got:
            c.tree, c.tree_root, c.project = t.name, t.root, t.project
        found.extend(got)
        notes.extend(complaints)
    return found, notes


def review_all(conf: settings.Settings, root: str) -> tuple[list[ticket_mod.Ticket], list[str]]:
    """全ツリーのレビュー待ち（提案の置き場の `review/`）を、重複をまとめずに集める。

    ここに在るのは承認済みチケットが `finish` で動いてきたもの。`ccnavi_approved` を持たない
    ファイルは読まない。この置き場はエージェントが書ける側にあり、保護は組み込みの
    deny 1 つなので、欄を 2 つめの保護として残す。
    """
    found: list[ticket_mod.Ticket] = []
    notes: list[str] = []
    for t in trees(conf, root):
        directory = os.path.join(t.root, conf.tickets.replace("/", os.sep), ticket_mod.REVIEW)
        got, complaints = _load_dir(directory, require_record=True)
        for c in got:
            c.tree, c.tree_root, c.state = t.name, t.root, ticket_mod.REVIEW
            c.project = t.project
        found.extend(got)
        notes.extend(complaints)
    return found, notes


@dataclass
class Raw:
    """全ツリーの承認済みチケットを、まとめる前のまま読んだもの（作業中・閉じた・レビュー待ち）。

    1 回の判定の中で `scan`・`scan(closed=True)`・`scan_review`・`scan_proposals` を続けて呼ぶ
    呼び手が、同じ置き場を何度も読まないために持ち回る（`read_raw`）。覚えておいたものではなく、その
    呼び出しの中で今しがた読んだもの（`_everything` と同じ考え方）。
    """

    open_all: list[ticket_mod.Ticket]
    open_notes: list[str]
    closed_all: list[ticket_mod.Ticket]
    closed_notes: list[str]
    review: list[ticket_mod.Ticket]
    review_notes: list[str]

    @property
    def everything(self) -> list[ticket_mod.Ticket]:
        return self.open_all + self.closed_all + self.review


def read_raw(conf: settings.Settings, root: str) -> Raw:
    """承認済みチケットの置き場を 1 度ずつ読む（`Raw`）。"""
    open_all, open_notes = scan_all(conf, root)
    closed_all, closed_notes = scan_all(conf, root, closed=True)
    review, review_notes = review_all(conf, root)
    return Raw(open_all, open_notes, closed_all, closed_notes, review, review_notes)


def scan(
    conf: settings.Settings, root: str, closed: bool = False, raw: Raw | None = None
) -> tuple[list[ticket_mod.Ticket], list[str]]:
    """判定と承認が読む承認済みチケット。本物とするツリーの側だけを残す。

    本物とするのは親のツリー（親自身なら自分のツリー）。提案の `dedupe` と違い、そこに無ければ
    落とす。子のワークツリーに checkout されているのは切った時点の版なので、親のツリーで
    閉じたあとも開いた版が残る。「本物とする側に無ければ全部残す」にすると、閉じたチケットが
    開いたものとして復活する。親のツリーがその識別子をどの置き場（作業中・レビュー待ち・
    閉じた）にも持っていなければ元ツリー（ワークスペースルート。プロジェクトのチケットなら
    そのプロジェクト）の側を採り、そこにも無いときと、元ツリーより先の置き場に在るチケットが
    あるときだけ、見つかった側を全部残す（`ticket.fold` と同じ順・同じ条件）。

    返す前に `mark_blocked` が「信頼できない理由」を付ける。承認のときにしか
    当たらなかった構造の検査を、判定の側でも当てるため（置き場を手で動かす承認は
    `--agree` を通らない）。

    `raw` は呼び手が `read_raw` で読んだもの。渡せば置き場を読み直さない。
    """
    if raw is not None:
        found = raw.closed_all if closed else raw.open_all
        notes = raw.closed_notes if closed else raw.open_notes
        kept = _authoritative(found, raw.everything)
        if not closed:
            kept = archive.drop_archived(root, kept)
            mark_blocked(conf, kept)
            mark_imported(conf, root, kept)
        return kept, list(notes)
    found, notes = scan_all(conf, root, closed)
    # 読んだ側を `_everything` に渡す。渡さないと、この同じ式の中でまったく同じ
    # `scan_all` をもう 1 度呼ぶことになる（下記）。
    if closed:
        everything = _everything(conf, root, closed_all=found)
    else:
        everything = _everything(conf, root, open_all=found)
    kept = _authoritative(found, everything)
    if not closed:
        # 手元の退避にある（ready が閉じて移した）チケットの、子のワークツリーに残った古いチケットは
        # 作業中に戻さない（archive.drop_archived）。
        kept = archive.drop_archived(root, kept)
        # 判定が読むのは作業中の側だけ。閉じたものに理由は要らない。
        mark_blocked(conf, kept)
        mark_imported(conf, root, kept)
    return kept, notes


def scan_review(
    conf: settings.Settings, root: str, raw: Raw | None = None
) -> tuple[list[ticket_mod.Ticket], list[str]]:
    """レビュー待ちのチケット。本物とするツリーの側だけを残す（`scan` と同じ規則）。"""
    if raw is not None:
        kept = archive.drop_archived(root, _authoritative(raw.review, raw.everything))
        mark_imported(conf, root, kept)
        return kept, list(raw.review_notes)
    found, notes = review_all(conf, root)
    kept = archive.drop_archived(root, _authoritative(found, _everything(conf, root, review=found)))
    mark_imported(conf, root, kept)
    return kept, notes


def scan_proposals(
    conf: settings.Settings,
    root: str,
    everything: list[ticket_mod.Ticket] | None = None,
) -> tuple[list[ticket_mod.Ticket], list[ticket_mod.Problem]]:
    """提案（`todo/` と `review/`）を、承認済みチケットがどのツリーにあるかに従ってまとめる。

    承認済みの識別子の提案は、承認済みチケットで本物とするツリー（親のツリー → 元ツリー →
    決まらない。`ticket.authority`）を決め、そのツリーに在るものだけを残す。
    承認で本物とするツリーの `todo/` が消えても、承認の前に切ったワークツリーには `todo/` の提案が
    残る。提案だけでまとめると、その古い提案が承認待ちや改版（巻き戻し）として読まれる。
    承認済みチケットの側（`_authoritative`）は「本物とするツリーに無ければ落とす」なので、それと揃える。

    `everything` は承認済みチケットの全部（`Raw.everything` の形）。呼び手が読んであれば
    渡す。取り込み済みの親子のチケットも、本物とするのは親のブランチ `P` のワークツリー（名前 `P`）
    なので、同じ規則で決まる（理由を付けるのは `mark_imported` と `family_problems`）。
    """
    kept, problems, _ = read_proposals(conf, root, everything)
    return kept, problems


def read_proposals(
    conf: settings.Settings,
    root: str,
    everything: list[ticket_mod.Ticket] | None = None,
) -> tuple[list[ticket_mod.Ticket], list[ticket_mod.Problem], list[tuple[ticket_mod.Ticket, str]]]:
    """`scan_proposals` と同じもの（残した提案と苦情）。

    本物とするツリーの外に残った `todo/` の提案（`stale_proposals`）を 3 つ目に添える。
    """
    if everything is None:
        everything = _everything(conf, root)
    found, problems = ticket_mod.scan_all(root, conf.tickets, conf.projects)
    kept = ticket_mod.dedupe(found, everything)
    return kept, problems, stale_proposals(found, kept, everything)


def stale_proposals(
    found: list[ticket_mod.Ticket],
    kept: list[ticket_mod.Ticket],
    everything: list[ticket_mod.Ticket],
) -> list[tuple[ticket_mod.Ticket, str]]:
    """承認済みの識別子の `todo/` の提案のうち、本物とするツリーの外に在るので読まなかったもの。

    (提案, 本物とするツリーの名前) のリスト。`found` はまとめる前の提案、`kept` は残した側。
    本物とするツリーの `todo/` に同じ中身の提案が在るもの（親のツリーの改版が子のワークツリーに
    入っているだけ）は入れない。`review/` の提案も入れない（置き場が動いた結果で、承認の対象に
    ならない）。
    """
    kept_ids = {id(t) for t in kept}
    settled = _by_id(everything)
    out: list[tuple[ticket_mod.Ticket, str]] = []
    for ticket_id, hits in _by_id(found).items():
        copies = settled.get(ticket_id)
        if not copies:
            continue
        where = ticket_mod.authority(copies)
        if where is None:
            continue
        there = [t for t in hits if id(t) in kept_ids and t.state == ticket_mod.TODO]
        for t in hits:
            if t.state != ticket_mod.TODO or id(t) in kept_ids or t.tree == where:
                continue
            if any(_same_todo(t, u) for u in there):
                continue
            out.append((t, where))
    return out


def _same_todo(t: ticket_mod.Ticket, u: ticket_mod.Ticket) -> bool:
    """同じ中身の `todo/` の提案か。計画まで比べる。"""
    return t.plan == u.plan and t.feedback == u.feedback and t.raw == u.raw


def revision_elsewhere(t: ticket_mod.Ticket, current: ticket_mod.Ticket | None) -> bool:
    """本物とするツリーの外の `todo/` の提案が、計画の違う親の版（場所違いの改版か巻き戻しの元）か。

    条件は承認の改版（`agree.waiting`）と同じく、作業中の承認済みチケットがある親で、計画か
    フィードバック計画が違うもの。
    """
    return current is not None and not t.is_child and t.has_plan and plan_differs(t, current)


def plan_differs(proposal: ticket_mod.Ticket, current: ticket_mod.Ticket) -> bool:
    """提案の計画（全体計画かフィードバック計画）が承認済みチケットと違うか。改版の条件。"""
    return proposal.plan != current.plan or proposal.feedback != current.feedback


def revision_elsewhere_text(
    conf: settings.Settings,
    root: str,
    t: ticket_mod.Ticket,
    where: str,
    fams: syncstate.Families | None = None,
) -> str:
    """本物とするツリーの外に在る計画の違う版の案内。ファイルの場所と、改版を書く置き場を名指しする。

    場所はどちらもワークスペースルートからのパスで出す（ツリーの名前ではなく、
    `.claude/worktrees/<親>` や `projects/<名前>`）。`--lint` と `--agree`（`--verify` も）が
    同じ文面を出す。取り込み済みの親子のチケットでは、親子のチケットが決まらない・閉じているなら止まった理由と
    手順（`family_stop_text`。`family_problems` と同じ文面）を足す。決まっていれば、本物とする
    ツリーが親のワークツリー（`family_problems` が言う書く場所と同じ）なので、場所は繰り返さず
    push してから頼むことだけを足す。
    """
    rel = os.path.relpath(t.path, root).replace(os.sep, "/")
    place = tree_path(conf, root, where, t.project)
    todo = f"{conf.tickets}/{ticket_mod.TODO}/"
    text = (
        f"{t.ticket} の計画の違う提案が {rel} に在るが、本物とするツリー（{place}）の外なので"
        f"承認の対象にならない。改版なら {place} の {todo} に書き、古い版なら消してください"
    )
    fams = fams or syncstate.Families(conf, root)
    if fams.active:
        st = family_standing(conf, root, t, fams)
        if st.imported and st.stop:
            text += "。取り込み済みの親子のチケットが止まっている: " + family_stop_text(root, st)
        elif st.imported and st.home is not None:
            text += (
                "。取り込み済みの親子のチケットなので、書いたら push してから承認を頼んでください"
            )
    return text


def tree_path(conf: settings.Settings, root: str, name: str, project: str = "") -> str:
    """ツリーの名前を、ワークスペースルートからのパスに直す。ルート自身は「ワークスペースルート」。"""
    found = [t for t in trees(conf, root) if t.name == name]
    pick = next((t for t in found if t.project == project), found[0] if found else None)
    if pick is None:
        return name or "ワークスペースルート"
    rel = os.path.relpath(pick.root, root).replace(os.sep, "/")
    return "ワークスペースルート" if rel == "." else rel


def mark_imported(
    conf: settings.Settings,
    root: str,
    kept: list[ticket_mod.Ticket],
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


def outside_reason(st: syncstate.Standing, t: ticket_mod.Ticket) -> str:
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
    t: ticket_mod.Ticket,
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
    t: ticket_mod.Ticket,
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
    t: ticket_mod.Ticket,
    fams: syncstate.Families | None = None,
    others: list[ticket_mod.Ticket] | None = None,
) -> list[rules.Problem]:
    """親のブランチ名を承認してよいか。

    - `branch:` がそのリポジトリの統合先の名前に当たれば承認しない。比べる名前は
      `Families.integration_names`（環境変数・`.claude/settings.local.json`・統合先の取り込み結果・
      `origin/HEAD`・`origin/main`・`origin/master`）。固定のリストと字の検査は
      `ticket.branch_problem` が読むときに済ませている
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
    mine = ticket_mod.branch_name(t).casefold()
    clash = sorted(
        {
            o.ticket
            for o in others or []
            if o.ticket != t.ticket
            and (o.parent or o.ticket) != t.ticket
            and o.project == t.project
            and mine in {ticket_mod.branch_name(o).casefold(), o.ticket.casefold()}
        }
    )
    if clash:
        found.append(
            rules.Problem(
                rules.SEVERITY_ERROR,
                t.ticket,
                f"親のブランチ {ticket_mod.branch_name(t)} を別の親子のチケット"
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
    t: ticket_mod.Ticket,
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
    conf: settings.Settings, root: str, proposals: list[ticket_mod.Ticket]
) -> set[str]:
    """統合先の取り込み結果の `done/` か手元の退避に同じ識別子がある新規の提案。

    閉じた識別子の再利用を見つける。
    """
    fams = syncstate.Families(conf, root)
    archived: dict[str, set[str]] = {}

    def closed(t: ticket_mod.Ticket) -> bool:
        if t.project not in archived:
            archived[t.project] = archive.ids(root, t.project)
        if any(archive.same_id(t.ticket, i) for i in archived[t.project]):
            return True
        return fams.active and t.ticket in fams.done(syncstate.repo_key(t.project))[0]

    return {t.ticket for t in proposals if t.state == ticket_mod.TODO and closed(t)}


def _everything(
    conf: settings.Settings,
    root: str,
    *,
    open_all: list[ticket_mod.Ticket] | None = None,
    closed_all: list[ticket_mod.Ticket] | None = None,
    review: list[ticket_mod.Ticket] | None = None,
) -> list[ticket_mod.Ticket]:
    """作業中・レビュー待ち・閉じたの全部を、重複をまとめずに。本物とするツリーを決めるために使う。

    3 つは呼び手が持ち込める。**読んだものを覚えておくのではなく、
    同じ呼び出しの中で今しがた読んだものを渡してもらう仕組み。
    ** `scan` は `scan_all` を呼んだ直後にここを呼ぶので、
    渡さないと同じ引数の `scan_all` が 1 つの式の中で 2 回走る。
    置き場のチケットは 1 本ずつYAML として解析されるので、
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
    found: list[ticket_mod.Ticket], everything: list[ticket_mod.Ticket]
) -> list[ticket_mod.Ticket]:
    # 本物とするのは親のツリー → 元ツリー（先へ進んだチケットが無いときだけ）→
    # 決まらない（全部残す）。
    # 親のツリーが無いとき（作る前と、合流して片付けた後）は元ツリーを本物とする。ワークツリーは
    # 片付ければ消えるが、元ツリー（ワークスペースルート。プロジェクトのチケットならその
    # プロジェクト）は消えない。リポジトリをまたいだ衝突はまとめない。違うチケットなので、
    # 本物とする側を決めるとどちらかが気づかないうちに消え、`--lint` の「複数のリポジトリにある」も
    # 出なくなる。決め方は `ticket.authority` の 1 つで、承認済みの識別子の提案
    # （`ticket.fold`）も同じ関数で決める。
    seen = _by_id(everything)
    kept: list[ticket_mod.Ticket] = []
    for ticket_id, hits in _by_id(found).items():
        where = ticket_mod.authority(seen.get(ticket_id) or hits)
        if where is None:
            kept.extend(hits)
        else:
            kept.extend(t for t in hits if t.tree == where)
    return kept


def _by_id(found: list[ticket_mod.Ticket]) -> dict[str, list[ticket_mod.Ticket]]:
    """識別子ごとのチケットの全部。順序は見つけた順。"""
    grouped: dict[str, list[ticket_mod.Ticket]] = {}
    for t in found:
        grouped.setdefault(t.ticket, []).append(t)
    return grouped


def home_dir(
    conf: settings.Settings,
    root: str,
    ticket_id: str,
    parent: str,
    fallback_root: str = "",
    project: str | None = None,
) -> str:
    """この識別子の承認済みチケットを置くツリーの置き場。

    書く先も読む先も 1 つに決めるためのもの。子のワークツリーにも checkout されるが、
    そこへ書くと同じ識別子の承認済みチケットが 2 通りになる。

    探す順は、すでに持っているツリー（親のツリー → ワークスペースかプロジェクトの
    ルート → その他）、親のツリー、fallback_root（提案があったツリー）、
    ワークスペースルート。すでに在る側を先に見るのは、マーカーと記録を承認済みチケットと
    同じ場所に置くため。親のワークツリーは承認のあとに作られることがあり、そこを
    先に見ると、承認済みチケットとマーカーが別のツリーに分かれる。

    `project` はチケットのリポジトリ（ワークスペース自身なら空）。分からなければ None で、
    取り込み状態のあるリポジトリを全部探す（同じ識別子が 2 つのリポジトリにあれば決めない）。
    """
    home = parent or ticket_id
    # 取り込み済みの親子のチケットは、親のブランチ（親のワークツリー）だけに書く。
    # 本物とする側のツリーが無いときに元ツリーに未コミットで書く形は、取り込み済みならやめる。
    # 決まらない親子のチケットは、ここへ来る前に状態の操作と承認が止める。
    st = syncstate.standing_any(conf, root, home, project)
    if st.imported and st.home is not None and not st.stop:
        return settings.approved_dir(conf, st.home.root)
    named = ""
    holders: list[tuple[tree.Tree, str]] = []
    for t in trees(conf, root):
        where = settings.approved_dir(conf, t.root)
        if t.name == home:
            named = where
        if (
            fsio.exists(copy_path(where, ticket_id))
            or fsio.exists(closed_path(where, ticket_id))
            or fsio.exists(review_path(t.root, conf.tickets, ticket_id))
        ):
            holders.append((t, where))
    for wanted in (lambda t: t.name == home, lambda t: t.is_main, lambda t: True):
        for t, where in holders:
            if wanted(t):
                return where
    if named:
        return named
    return settings.approved_dir(conf, fallback_root or tree.main_tree(root).root)


def source_path(t: ticket_mod.Ticket) -> str:
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


def source_branch(t: ticket_mod.Ticket) -> str:
    """提案が乗っていたブランチの名前。

    HEAD がブランチを指していなければ（切り離した・壊れた・読めない）ツリーの名前
    （ワークスペースルートなら空）。Changes の `per_branch` の名前と同じ決め方。
    """
    found = tree.branch_of(t.tree_root) if t.tree_root else None
    return found or t.tree


def admit(
    approved_dir: str,
    ticket: ticket_mod.Ticket,
    source_tree: str,
    approved_at: str,
    wf: ticket_mod.Workflow | None = None,
) -> str:
    """承認した提案を `doing/` へ動かす。動かせなかった理由を返す。動かせたら空文字。

    承認の記録（`ccnavi_approved`）を足して書き、元の提案を消す。消せなければ書いた側を
    消して戻す。両方に残ると、以後どの操作も「複数の場所にある」で止まる。

    `source_tree` は提案が乗っていたブランチの名前、`source_path` はそのリポジトリからの
    相対パス。手元と Chrome で承認済みチケットの中身を同じにするため。前は
    ツリーの名前と絶対パスを書いていた。読む側（`load_copy`）はどちらの形も読み、判定は
    この 2 つを読まない（`diagnose` が見せるだけ）。
    """
    meta = {
        "approved_at": approved_at,
        "source_tree": source_tree,
        "source_path": source_path(ticket),
    }
    target = copy_path(approved_dir, ticket.ticket)
    # ユーザの書いた行（コメント、`|` のブロック）を保つ。読み直して書き出さず、承認の記録の
    # 欄と、置き場から決まった `project:`（frontmatter に無ければ）だけを足す。
    try:
        text = fsio.load_text(ticket.path)
    except OSError as exc:
        return f"提案を読めない ({exc})"
    if ticket.project and not ticket.declared_project:
        text = ticket_mod.insert_front(text, "project", ticket.project)
    if wf is not None:
        text = ticket_mod.insert_front(text, ticket_mod.WORKFLOW_KEY, wf.as_raw())
    failed = _write(target, ticket_mod.insert_front(text, ticket_mod.APPROVAL_KEY, meta))
    if failed:
        return failed
    # 消せなければ書いた側を消して戻す。承認の plan では落ちたときの枝が走らないので、
    # 同じ戻し方を Writer(FS) へ渡す。置けたと数えるのは消せたとき。
    with fsio.policy(
        message="提案を todo/ から動かせない ({reason})", undo=(target,), places=ticket.ticket
    ):
        failed = fsio.unlink(ticket.path)
    if failed:
        fsio.remove(target)
        return f"提案を todo/ から動かせない ({failed})"
    history.note(
        approved_dir,
        ticket.ticket,
        history.KIND_APPROVED,
        ticket_mod.TODO,
        ticket_mod.DOING,
        tree=source_tree,
    )
    return ""


def update_fields(path: str, fields: dict) -> str:
    """チケットの、スクリプトが書く欄だけを行単位で書き換える。ユーザの書いた本文は保つ。"""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError as exc:
        return f"読めない ({exc})"
    return _write(path, ticket_mod.set_fields(text, fields))


def close_copy(approved_dir: str, ticket_id: str) -> str:
    """承認済みチケットを `doing/` から `done/` へ動かす。"""
    return move_file(copy_path(approved_dir, ticket_id), closed_path(approved_dir, ticket_id))


def to_review(approved_dir: str, tree_root: str, tickets_rel: str, ticket_id: str) -> str:
    """承認済みチケットを `doing/` から提案の置き場の `review/` へ動かす。"""
    return move_file(
        copy_path(approved_dir, ticket_id), review_path(tree_root, tickets_rel, ticket_id)
    )


def carry_flow(
    conf: settings.Settings, root: str, proposal: ticket_mod.Ticket, approved_dir: str
) -> list[str]:
    """承認した子のフローを、提案のツリーから承認済みチケットのツリーへ動かす。知らせる行を返す。

    フローの置き場は承認済みチケットと同じツリー（設計 9.3.1）。ユーザは承認の前に、提案が在る
    ツリーの置き場へボードで保存する。承認で子が別のツリー（親のワークツリーなど）へ動くと、
    フローだけが元のツリーに残り、読まれなくなる（M-3）。承認はユーザの操作なので、ここで一緒に
    動かす。行き先に違う中身のフローが既に在れば上書きせず、そう言う（元のほうも残す）。
    リンク・ふつうのファイルでないもの・ハードリンクは移さない（`flow.load` と同じ読み方）。
    """
    source_root = proposal.tree_root or root
    source = flow.flow_file(conf, source_root, proposal.ticket)
    target = os.path.normpath(
        os.path.join(approved_dir, flow.FLOWS_DIR, f"{proposal.ticket}{flow.SUFFIX}")
    )
    try:
        if not fsio.lexists(source):
            return []
        if os.path.normcase(os.path.realpath(source)) == os.path.normcase(os.path.realpath(target)):
            return []
    except (OSError, ValueError):
        return []
    raw, why = flow.read_bytes(source, source_root)
    if raw is None:
        return [
            f"{proposal.ticket} のフロー {source} を移さなかった: {why}。"
            "ユーザが確かめて置き直してください"
        ]
    if fsio.lexists(target):
        held, _ = flow.read_bytes(target)
        if held != raw:
            return [
                f"{proposal.ticket} のフローを {target} へ移さなかった: 行き先に違う中身のフローが"
                f"既に在る（上書きしない）。{source} と見比べて、ユーザが 1 本に決める"
            ]
        fsio.remove(source)
        return []
    # 落ちたときの行は承認の plan でも同じものを出せるよう、書き込みにつける（`FAIL_LINE`）。
    cannot = f"{proposal.ticket} のフローを {target} へ移せない ({{reason}})。{source} に残っている"
    with fsio.policy(message=cannot):
        failed = fsio.write_new(target, raw)
    if failed:
        return [cannot.replace("{reason}", failed)]
    kept = (
        f"{proposal.ticket} のフローを {target} へコピーした。"
        f"元の {source} は消せなかった ({{reason}})"
    )
    with fsio.policy(message=kept):
        failed = fsio.unlink(source)
    if failed:
        return [kept.replace("{reason}", failed)]
    return [f"{proposal.ticket} のフローを {source} から {target} へ動かした"]


def move_file(source: str, target: str) -> str:
    """チケットを置き場から置き場へ動かす。動かせなかった理由を返す。

    行き先に同じ名前が既に在れば動かさない。何も出さずに上書きすると、閉じた側の記録
    （取り消しの欄など）が消える。同じ識別子が 2 つ在るのは `--lint` が名指しする。

    同じファイルシステムの中なら rename 1 回で済む。またぐとき（EXDEV）だけコピーして消す。
    消せなければコピーした側を消して戻す。両方に残ると、以後どの操作も「複数の場所にある」で
    止まる（`admit` と同じ）。Windows は開かれているファイルを消させないので、現実に起きる。

    コピーして消す処理に回すのは EXDEV に限る。rename が他の理由（元が無い、など）で失敗した
    ときまで回すと、コピーできずに戻す処理が、その間に別のプロセスが置いた行き先を消す。
    """
    if fsio.exists(target):
        return f"チケットを動かせない ({source} → {target}: 行き先に既に在る)"
    message = f"チケットを動かせない ({source} → {target}: " + "{reason})"
    with fsio.policy(message=message):
        failed = fsio.move(source, target)
    return message.replace("{reason}", failed) if failed else ""


def settle_review(
    conf: settings.Settings, root: str, parent_id: str, numbers: list[int]
) -> tuple[list[str], str]:
    """この親の、この番号のフェーズのレビュー待ちの子を `done/` へ動かす。

    ユーザがレビューを済ませたときに呼ぶ（`confirm` / `decide` / `--reviewed --chat` /
    `close-early` と、フィードバック計画の承認）。返すのは動かした識別子と、動かせなかった理由。
    """
    review, _ = scan_review(conf, root)
    moved: list[str] = []
    for t in sorted(review, key=lambda x: x.ticket):
        if t.parent != parent_id or t.phase not in numbers:
            continue
        where = home_dir(conf, root, t.ticket, parent_id, project=t.project)
        failed = move_file(t.path, closed_path(where, t.ticket))
        if failed:
            return moved, failed
        moved.append(t.ticket)
        history.note(
            where, t.ticket, history.KIND_SETTLED, ticket_mod.REVIEW, ticket_mod.DONE, phase=t.phase
        )
    return moved, ""


def next_child_id(conf: settings.Settings, root: str, parent_id: str, phase_no: int) -> str:
    """この親のこのフェーズの次の子の識別子。どの置き場に在る同じフェーズの子よりも後ろの連番。

    連番はフェーズごとに 1 から数える（`<親>-<フェーズ番号>-<連番>`）。フェーズ番号か連番が
    2 桁に収まらなければ識別子を組めないので ValueError（呼び手は何も書かずに止まる）。

    手元の退避（`logs/archive/`）にある子も数える（閉じた子の連番を使い回さない）。退避の子は
    親の識別子を大文字小文字を区別せずに比べる（`integration_problems` と同じ見方）。
    """
    top = ticket_mod.MAX_CHILD_NUMBER
    if not 0 <= phase_no <= top:
        raise ValueError(f"フェーズ {phase_no} は子の識別子に書けない（フェーズ番号は 0〜{top}）")
    seen = _everything(conf, root)
    proposals, _ = ticket_mod.scan(root, conf.tickets, conf.projects)
    # 退避は親のリポジトリ（プロジェクト）のものだけを見る。親が置き場に無ければ全部を見る。
    projects = {t.project for t in seen + proposals if t.ticket == parent_id}
    archived: set[str] = set()
    for project in sorted(projects) if projects else [None]:
        archived |= archive.ids(root, project)
    used = 0
    for t in seen + proposals:
        m = ticket_mod.child_pattern().match(t.ticket)
        if m and m.group("parent") == parent_id and int(m.group("phase")) == phase_no:
            used = max(used, int(m.group("seq")))
    for ident in sorted(archived):
        m = ticket_mod.child_pattern().match(ident)
        if (
            m
            and archive.same_id(m.group("parent"), parent_id)
            and int(m.group("phase")) == phase_no
        ):
            used = max(used, int(m.group("seq")))
    if used >= top:
        raise ValueError(
            f"親 {parent_id} のフェーズ {phase_no} の子が連番 {top} まで埋まっている。"
            "子の識別子の連番は 2 桁なので、続きの子を起こせない"
        )
    return ticket_mod.child_id(parent_id, phase_no, used + 1)


def existing_ticket_file(conf: settings.Settings, root: str, ident: str) -> str:
    """この識別子のファイルが、どこかの置き場にすでに在ればそのパス。無ければ空。

    読めないファイル（壊れた frontmatter など）も名前で拾う。大文字小文字は区別しない
    （区別しないファイルシステムでは同じファイルになる）。見るのは全ツリーの承認済みの
    `doing/` `done/` と、提案の `todo/` `review/`。
    """
    want = (ident + ".md").casefold()
    for t in trees(conf, root):
        approved = settings.approved_dir(conf, t.root)
        proposals = os.path.join(t.root, conf.tickets.replace("/", os.sep))
        places = [os.path.join(approved, DOING_DIR), os.path.join(approved, DONE_DIR)]
        places += [os.path.join(proposals, state) for state in ticket_mod.STATES]
        for place in places:
            try:
                names = os.listdir(place)
            except OSError:
                continue
            for name in names:
                if name.casefold() == want:
                    return os.path.join(place, name)
    return ""


def followup(
    conf: settings.Settings,
    root: str,
    parent: ticket_mod.Ticket,
    phase_no: int,
    children: list[ticket_mod.Ticket],
    items: list[str],
    stamp: str,
) -> tuple[str, str]:
    """レビューで残った指摘の続きの子を、ユーザの判断で `doing/` に直に起こす。

    ユーザが端末で「続きの子で直す」と選んだことが承認そのもの。同じフェーズの番号に足すので、
    そのフェーズは開き直り、マーカーは消える（REQ-TKT-21）。範囲は見た子の範囲の和。
    本文には指摘を書き写す。返すのは識別子と、起こせなかった理由。
    """
    try:
        ident = next_child_id(conf, root, parent.ticket, phase_no)
    except ValueError as exc:
        return "", str(exc)
    # 同じ識別子のファイルがどこかに在れば（読めなかったものも）上書きしない。
    taken = existing_ticket_file(conf, root, ident)
    if taken:
        return (
            ident,
            f"{ident} のファイルがすでに在る（{taken}）。上書きしないので、中身を確かめて片付ける",
        )
    where = home_dir(conf, root, parent.ticket, "", project=parent.project)
    front: dict = {
        "version": ticket_mod.VERSION,
        "ticket": ident,
        "parent": parent.ticket,
        "phase": phase_no,
    }
    if parent.project:
        front["project"] = parent.project
    # 先行は、見た子のうち取り消しでないもの。取り消した子は満たせないので、先行に入れると
    # 続きの子が着手できなくなる（満たすのは `done/` に在って取り消しでないものだけ）。
    # 範囲の和には入れる（見たのは同じフェーズの全部）。
    front["predecessors"] = [c.ticket for c in children if not c.cancelled_at]
    front["human_review"] = {"required": True, "reason": "レビューで残った指摘への対応"}
    front["title"] = f"フェーズ {phase_no} のレビューの指摘に応える"
    front["rationale"] = (
        f"フェーズ {phase_no} のレビューで残った指摘に応える。"
        "ユーザが端末で起こした続きの子で、承認はその判断で済んでいる。\n"
    )
    for name in rules.SECTIONS:
        entries = []
        seen: set[tuple] = set()
        for c in children:
            for raw in c.raw.get(name) or []:
                key = (
                    tuple(sorted((str(k), str(v)) for k, v in raw.items()))
                    if isinstance(raw, dict)
                    else (str(raw),)
                )
                if key not in seen:
                    seen.add(key)
                    entries.append(raw)
        if entries:
            front[name] = entries
    front.update({"started_at": "", "completed_at": "", "base_sha": ""})
    front[ticket_mod.APPROVAL_KEY] = {
        "approved_at": stamp,
        "source_tree": "",
        "source_path": "",
        "followup_of": [c.ticket for c in children],
    }
    body = ["", "## 引き継ぐ指摘", ""]
    body += [f"- {item}" for item in items] or ["（指摘の一覧は無い）"]
    body.append("")
    t = ticket_mod.Ticket(ticket=ident, raw=front, body="\n".join(body))
    failed = _write(copy_path(where, ident), ticket_mod.render(t))
    if failed:
        return ident, failed
    history.note(
        where,
        ident,
        history.KIND_RAISED,
        None,
        ticket_mod.DOING,
        phase=phase_no,
        followup_of=[c.ticket for c in children],
    )
    cleared = clear_marks(where, parent.ticket, phase_no)
    for warning in cleared.warnings:
        history.failed_to_write(warning)
    if cleared.failed:
        return ident, cleared.failed
    return ident, ""


def by_id(tickets: list[ticket_mod.Ticket]) -> dict[str, ticket_mod.Ticket]:
    return {t.ticket: t for t in tickets}


# ---- 先行（`predecessors`）を満たしているか。承認と着手の両方で求める

# 先行の状態。満たしたとみなすのは `done` だけ（`.ccnavi/approved/done/` に在り、取り消しでない）。
PRED_DONE = ticket_mod.DONE
PRED_CANCELLED = ticket_mod.CANCELLED
PRED_MISSING = "missing"
PRED_SCATTERED = "scattered"
# 形の上で満たせない先行。待っても通らないので、ふつうの error にする。
PRED_SELF = "self"  # 自分自身
PRED_ANCESTOR = "ancestor"  # 自分の親（子は親の中の作業で、親は子より先に閉じない）
PRED_CYCLE = "cycle"  # 先行を辿ると自分に戻る
# 取り込み済みの親子のチケットの先行で、その親子のチケットが決まらない・親のブランチ上に無い。
PRED_UNDECIDED = "undecided"
# 先行が閉じれば同じ提案のまま通る状態。承認では `rules.KIND_NOT_YET` の苦情にする。
PRED_WAITING = (ticket_mod.TODO, ticket_mod.DOING, ticket_mod.REVIEW)
PRED_LABELS = {
    ticket_mod.TODO: "承認待ち（todo/）",
    ticket_mod.DOING: "作業中（doing/）",
    ticket_mod.REVIEW: "レビュー待ち（review/）",
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
    open_copies: list[ticket_mod.Ticket],
    review: list[ticket_mod.Ticket],
    closed: list[ticket_mod.Ticket],
    proposals: list[ticket_mod.Ticket],
    root: str = "",
) -> dict[str, list[ticket_mod.Ticket]]:
    """先行を引く対応表。識別子 → 本物とする側のチケットの全部（`ops._places` と同じ集め方）。

    承認済みチケット（作業中・レビュー待ち・閉じた）はどれも数える。`todo/` の提案は、同じ識別子の
    承認済みチケットがどこにも無いときだけ数える（在れば改版の候補か書き損じ）。チケットが 2 つ以上
    残れば、どれが本物か決まらない。

    `root`（ワークスペースルート）を渡せば、どの置き場にも無い先行を手元の退避（`logs/archive/`）の
    `done/` から引く。`ready` が閉じた親子のチケットを退避した後も、先行を閉じたものとして読むため。
    引くのはリストのチケットが先行に書いた識別子だけ（退避を全部は読まない）。
    """
    pool = _by_id(open_copies + review + closed)
    for t in proposals:
        if t.state == ticket_mod.TODO and t.ticket not in pool:
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


def predecessor_pool(conf: settings.Settings, root: str) -> dict[str, list[ticket_mod.Ticket]]:
    """いまの置き場から先行を引く対応表を組む。"""
    open_copies, _ = scan(conf, root)
    review, _ = scan_review(conf, root)
    closed, _ = scan(conf, root, closed=True)
    proposals, _ = ticket_mod.scan(root, conf.tickets, conf.projects)
    pool = predecessor_pool_of(open_copies, review, closed, proposals, root)
    align_imported(conf, root, pool)
    return pool


def align_imported(
    conf: settings.Settings, root: str, pool: dict[str, list[ticket_mod.Ticket]]
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


def _undecided(sample: ticket_mod.Ticket, why: str) -> ticket_mod.Ticket:
    """先行の対応表に置く「親子のチケットが決まらない」の項目。理由は `blocked` に入れて渡す。"""
    return replace(sample, state=PRED_UNDECIDED, blocked=why)


def predecessor_states(
    t: ticket_mod.Ticket, pool: dict[str, list[ticket_mod.Ticket]]
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
            out.append(Predecessor(ident, hits[0].state or ticket_mod.DOING))
    return out


def _loop_back(origin: str, first: str, pool: dict[str, list[ticket_mod.Ticket]]) -> list[str]:
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
    t: ticket_mod.Ticket, pool: dict[str, list[ticket_mod.Ticket]]
) -> list[Predecessor]:
    """満たしていない先行。子だけが先行を持つ（親の `predecessors` は読まない）。"""
    if not t.is_child:
        return []
    return [p for p in predecessor_states(t, pool) if not p.met]


def predecessor_problems(
    t: ticket_mod.Ticket, pool: dict[str, list[ticket_mod.Ticket]], approved_rel: str
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
                    f"{approved_rel}/{ticket_mod.DONE}/ に入る（作業を終え、レビューが要るなら"
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


def children_of(tickets: list[ticket_mod.Ticket], parent_id: str) -> list[ticket_mod.Ticket]:
    return [t for t in tickets if t.parent == parent_id]


def mark_path(approved_dir: str, parent: str, phase: int, kind: str) -> str:
    return os.path.join(approved_dir, PHASES_DIR, parent, f"{phase}.{kind}")


def read_mark(approved_dir: str, parent: str, phase: int, kind: str) -> dict | None:
    return fsio.read_dict(mark_path(approved_dir, parent, phase, kind))


def write_mark(approved_dir: str, parent: str, phase: int, kind: str, data: dict) -> str:
    payload = dict(data)
    payload.setdefault("at", now())
    failed = _write(
        mark_path(approved_dir, parent, phase, kind), json.dumps(payload, ensure_ascii=False)
    )
    if not failed:
        history.note(
            approved_dir, parent, history.KIND_PHASE_MARK, None, None, phase=phase, mark=kind
        )
    return failed


@dataclass
class Cleared:
    """`clear_marks` の答え。`kinds` は消せた種類（書き込みを溜める段では消す種類）。

    `failed` は reviewed を消せなかった理由（空でなければ呼び手は止める）。`warnings` は
    ほかの種類を消せなかった知らせ（残っていて判定に使われる）。
    """

    kinds: list[str]
    failed: str = ""
    warnings: list[str] = field(default_factory=list)


# 消す順。reviewed を先に消す。消せなければ止めるので、ほかの種類に手を付ける前に決める。
_CLEAR_ORDER = (MARK_REVIEWED,) + tuple(k for k in MARKS if k != MARK_REVIEWED)


def clear_marks(
    approved_dir: str,
    parent: str,
    phase: int,
    announce=None,
) -> Cleared:
    """このフェーズのマーカーを全部消す（同じ番号に子を足した。REQ-TKT-21）。

    reviewed を消せなければ止める（`failed`）。残ればフェーズは済んだまま読まれ、足した子を
    見ないまま先へ進めてしまう（判定を厳しくする向き）。ほかの種類は
    消せなくても止めず、残っていて判定に使われると言う（`warnings`）。
    履歴（phase-reopened の `cleared`）には実際に消せた種類だけを書く。

    書き込みを溜める段（承認の plan）では、消す書き込みに同じ扱いをつけ、
    履歴と見せる行は Writer(FS) が書けた種類で書く（`fsio.Call`）。
    `announce` は消せた種類から見せる行を作る関数。
    """
    stage = fsio.current_stage()
    present = [k for k in _CLEAR_ORDER if fsio.lexists(mark_path(approved_dir, parent, phase, k))]
    if stage is not None:
        group = stage.new_group()
        for kind in present:
            path = mark_path(approved_dir, parent, phase, kind)
            with fsio.policy(group=group, tag=kind, **_clear_policy(path, phase, kind)):
                fsio.unlink(path)
        kinds = [k for k in MARKS if k in present]
        if kinds:
            # 見え方（Changes）には全部消えた後の履歴を載せる。ディスクへは Call が書く。
            with fsio.view_only():
                _note_reopened(approved_dir, parent, phase, kinds)

        # 履歴の時刻と経路は並べたときのもの（書く時に時計を読み直すと、Changes と食い違う）。
        at, via = fsio.stamp(), history.via()

        def run(done: set[str]) -> list[str]:
            cleared = [k for k in MARKS if k in done]
            if cleared:
                before = history.via()
                history.set_via(via)
                try:
                    with fsio.clock(at):
                        _note_reopened(approved_dir, parent, phase, cleared)
                finally:
                    history.set_via(before)
            return announce(cleared) if announce is not None and cleared else []

        planned = announce(kinds) if announce is not None and kinds else []
        stage.items.append(fsio.Call(run, group, planned))
        return Cleared(kinds)
    cleared: list[str] = []
    warnings: list[str] = []
    failed = ""
    for kind in present:
        path = mark_path(approved_dir, parent, phase, kind)
        reason = fsio.unlink(path)
        if not reason:
            cleared.append(kind)
            continue
        text = fsio.failure_text(fsio.Policy(**_clear_policy(path, phase, kind)), reason)
        if kind == MARK_REVIEWED:
            failed = text
            break
        warnings.append(text)
    cleared = [k for k in MARKS if k in cleared]
    if cleared:
        _note_reopened(approved_dir, parent, phase, cleared)
    return Cleared(cleared, failed, warnings)


def _clear_policy(path: str, phase: int, kind: str) -> dict:
    if kind == MARK_REVIEWED:
        return {
            "on_fail": fsio.FAIL_STOP,
            "prefix": "",
            "message": (
                f"フェーズ {phase} のレビュー済みのマーカー {path} を消せない（{{reason}}）。"
                "残るとフェーズは済んだまま読まれるので、ここで止める。ユーザがマーカーを消す"
            ),
        }
    return {
        "on_fail": fsio.FAIL_WARN,
        "prefix": "",
        "message": (
            f"フェーズ {phase} のマーカー {path} を消せなかった（{{reason}}）。"
            f"{kind} は残っていて効く"
        ),
    }


def _note_reopened(approved_dir: str, parent: str, phase: int, cleared: list[str]) -> None:
    history.note(
        approved_dir,
        parent,
        history.KIND_PHASE_REOPENED,
        None,
        None,
        phase=phase,
        cleared=cleared,
    )


# 親ごとのマーカー。フェーズの番号に付かないもの。
#   ready.json   Draft を外した（外してよいと確かめた）。「マージに進んでよい」の合図
#   close-early.json  ユーザが「キリの良いところまでやった」と早めに閉じた。残りは別の issue へ
#   closed.json  親を閉じた（`ticket finish <親>`）。どのフェーズをどこで見たかを残す
#
# closed.json が要るのは、提案（wip/）が統合先に取り込む前に消えるから。マージリクエストを
# 作らない進め方（全フェーズが `review: chat`）では、親を閉じた事実の残る先がここしか無い。
PARENT_MARK_READY = "ready"
PARENT_MARK_CLOSE_EARLY = "close-early"
PARENT_MARK_CLOSED = "closed"


def parent_mark_path(approved_dir: str, parent: str, name: str) -> str:
    return os.path.join(approved_dir, PHASES_DIR, parent, f"{name}.json")


def read_parent_mark(approved_dir: str, parent: str, name: str) -> dict | None:
    return fsio.read_dict(parent_mark_path(approved_dir, parent, name))


# 親のマーカーのうち、状態の履歴（history）に残すもの。親の閉じ方と Draft を外したこと。上書きの記録
# （configsync）は状態ではないので残さない。
PARENT_MARKS_IN_HISTORY = (PARENT_MARK_READY, PARENT_MARK_CLOSE_EARLY, PARENT_MARK_CLOSED)


def write_parent_mark(approved_dir: str, parent: str, name: str, data: dict) -> str:
    payload = dict(data)
    payload.setdefault("at", now())
    failed = _write(
        parent_mark_path(approved_dir, parent, name),
        json.dumps(payload, ensure_ascii=False, indent=1),
    )
    if not failed and name in PARENT_MARKS_IN_HISTORY:
        history.note(approved_dir, parent, history.KIND_PARENT_MARK, None, None, mark=name)
    return failed


# 子ごとの記録。実績のリスク（閉じるときに数えた点）と、定性項目の判定。
#   phases/<親>/<子>.risk.json   {points, level, hits, unmeasured, head, at}
#   phases/<親>/<子>.judge.json  {<項目>: {hit, reason, head, at}}
CHILD_RECORD_RISK = "risk"
CHILD_RECORD_JUDGE = "judge"


def child_record_path(approved_dir: str, parent: str, child: str, kind: str) -> str:
    return os.path.join(approved_dir, PHASES_DIR, parent, f"{child}.{kind}.json")


def read_child_record(approved_dir: str, parent: str, child: str, kind: str) -> dict | None:
    return fsio.read_dict(child_record_path(approved_dir, parent, child, kind))


def write_child_record(approved_dir: str, parent: str, child: str, kind: str, data: dict) -> str:
    return _write(
        child_record_path(approved_dir, parent, child, kind),
        json.dumps(data, ensure_ascii=False, indent=1),
    )


# ユーザが受け入れたスレッドの記録。フェーズのマーカーとは別の場所に、親ごとに 1 つ置く。
ACCEPTED_FILE = "accepted.json"


def accepted_path(approved_dir: str, parent: str) -> str:
    return os.path.join(approved_dir, PHASES_DIR, parent, ACCEPTED_FILE)


def accepted_threads(
    approved_dir: str,
    parent: str,
    phase: int | None = None,
    owner: ticket_mod.Ticket | None = None,
) -> set[str]:
    """この親で、ユーザが「未解決のまま進める」と受け入れたスレッドの識別。

    `phase` を渡すと、その番号のレビューで受け入れ済みと数えてよいものだけを返す。
    受け入れはそのフェーズと、それを待つ番号（コピーした待ち方の `waits`）にだけ当てはまる（設計
    9.8）。並行した別の枝のレビューには当てはまらない。番号を持たない受け入れ（`close-early`）は
    親全体に当てはまる。
    """
    data = fsio.read_dict(accepted_path(approved_dir, parent))
    if not data:
        return set()
    threads = {str(x) for x in data.get("threads") or [] if str(x)}
    if phase is None or owner is None:
        return threads
    at = data.get("phases") if isinstance(data.get("phases"), dict) else {}
    reach = {phase, *workflow.waits_of(owner, phase, None)}
    kept = set()
    for thread in threads:
        where = at.get(thread)
        scoped = isinstance(where, list)
        if not scoped or any(isinstance(n, int) and n in reach for n in where):
            kept.add(thread)
    return kept


def remember_accepted(
    approved_dir: str, parent: str, threads: list[str], phase: int | None = None
) -> str:
    """受け入れたスレッドを記録に足す。失敗したら、その説明を返す。

    フェーズのマーカーとは別の場所に置く。マーカーは 2 つの理由で消える。同じ番号のマーカーは
    `confirm` が通るたびに上書きされ、その番号に子が足されると `clear_marks` が
    丸ごと消す。どちらでも受け入れの記録が消え、ユーザがもう一度同じスレッドを
    受け入れることになる。ユーザが 1 度言った「これは承知で進める」は、
    取り消されるまで残す。
    """
    if not threads:
        return ""
    path = accepted_path(approved_dir, parent)
    data = fsio.read_dict(path) or {}
    at = dict(data.get("phases")) if isinstance(data.get("phases"), dict) else {}
    added = {str(t) for t in threads if str(t)}
    threads_before = accepted_threads(approved_dir, parent)
    keep = sorted(threads_before | added)
    for thread in added:
        if phase is None:
            # 番号を持たない受け入れ（`close-early`）は親全体に当てはまる。
            at.pop(thread, None)
        elif thread in at or thread not in threads_before:
            # 受け入れた番号を足していく。別の枝で受け入れ直した分も数に入れる。
            known = [n for n in at.get(thread, []) if isinstance(n, int)]
            at[thread] = sorted(set(known) | {phase})
    # 読んで、足して、書き戻す形。同じファイルの `_write_known` と同じく、
    # 途中を見せない書き方で置く。
    body = {"threads": keep, "phases": dict(sorted(at.items())), "at": now()}
    failed = fsio.write_json_atomic(path, body, indent=1)
    return f"{path} ({failed})" if failed else ""


def marks(approved_dir: str, parent: str, phase: int) -> dict[str, dict]:
    found = {}
    for kind in MARKS:
        data = read_mark(approved_dir, parent, phase, kind)
        if data is not None:
            found[kind] = data
    return found


def now() -> str:
    return fsio.stamp()


def _reserved_project(t: ticket_mod.Ticket) -> list[rules.Problem]:
    """`project:` が層の名前に予約してある表記なら error（設計 11.4）。"""
    if not t.project or not settings.is_reserved_layer_name(t.project):
        return []
    reserved = " と ".join(f"`{name}`" for name in settings.RESERVED_LAYER_NAMES)
    return [
        rules.Problem(
            rules.SEVERITY_ERROR,
            t.ticket,
            f"`project: {t.project}` は層の名前として予約してある表記（{reserved}）。"
            "その名前のプロジェクトは層として数えないので、このチケットの層が決まらない。"
            "ワークスペース自身の提案は `wip/proposals/` に置いてください。プロジェクトの提案なら、"
            "そのプロジェクトの名前を変えてから置いてください",
        )
    ]


def project_problems(
    t: ticket_mod.Ticket, pool: dict[str, ticket_mod.Ticket], conf: settings.Settings
) -> list[rules.Problem]:
    """`project` が置き場と合っているか（REQ-MLT-11）。

    プロジェクトを決めるのは提案を置いた場所（設計 11.5）。frontmatter の `project:` は
    宣言ではなく照合で、置き場と違えば承認しない。親と子は同じ置き場に並ぶので、継ぐ段は
    無い。承認の画面が置き場から引いた値を出し、それが承認済みチケットに残る。

    予約名（`common` / `self`）は指せない。置き場にその名前のディレクトリが在っても
    層としては数えないので（`ruleload.layers`）、指せると「層が決まらないチケット」を
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
    # 予約名は `known` から外す。置き場に `projects/self/` が在っても、それは層では
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
    t: ticket_mod.Ticket, parent: ticket_mod.Ticket | None, missing: str = ""
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
    conf: settings.Settings, t: ticket_mod.Ticket, pool: dict[str, ticket_mod.Ticket]
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
    """
    problems = list(project_problems(t, pool, conf))
    if t.is_child:
        missing = f"親 {t.parent} の承認済みチケットが作業中に無い（未承認か、閉じている）"
        problems.extend(child_problems(t, pool.get(t.parent), missing))
    return [p for p in problems if p.severity == rules.SEVERITY_ERROR]


def mark_blocked(conf: settings.Settings, kept: list[ticket_mod.Ticket]) -> None:
    """判定が読む承認済みチケットに、信頼できない理由を付ける。

    理由を読むのは `phase.scope_verdict` で、実行前チェック・実行後チェック・サブエージェント
    終了時チェックの 3 か所が同じ答えを引く。1 か所で付けるのは、3 か所が別々に検査を
    呼ぶと、同じ書き込みが実行前は通って実行後に範囲外と報告されるから。

    **親を引く対応表は `kept` そのもの**（`by_id`）で、判定が `parent` を引く索引と同じ。
    別の対応表で引くと、ここでは親が見つかって理由が付かないのに、判定の側では見つからず
    `parent=None` のまま子の宣言だけで範囲が決まる（閉じた親やレビュー待ちの親まで
    引ける対応表にすると、この形になる）。親の範囲で切り詰められないのに通る形は、
    承認していない範囲に書ける経路そのものなので、引けないなら止める側を採る。

    親が閉じたのに子が開いている形は、道具を通る限り起きない（`ops.close_problems` が
    開いた子のある親を閉じさせない）。置き場を手で動かして起きたなら、親を閉じたのは
    ユーザなので、その子を止めるのがユーザの意思に沿う。
    """
    pool = by_id(kept)
    for t in kept:
        problems = blocking_problems(conf, t, pool)
        t.blocked = problems[0].detail if problems else ""


def _write(path: str, text: str) -> str:
    with fsio.policy(message="書けない ({reason})"):
        failed = fsio.write_text(path, text)
    return f"書けない ({failed})" if failed else ""
