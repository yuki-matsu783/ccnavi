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

**本物とするのはこの置き場で、チケットの中の欄ではない。** そこに置けたのは書ける権限を持つ
ユーザだけだから。だから提案を手で `doing/` へ動かすことが、端末・ボード・Chrome に続く承認の
経路になる。その経路は承認の画面を通らないので、承認のときにしか当たらなかった構造の検査は
`blocking_problems` が判定の側で当てる。

**承認はチケットの中身を変えない。** 提案のバイト列をそのまま `doing/` へ動かし、欄を書き足さない
（改行も BOM も変えない）。どの経路で承認しても承認済みチケットは提案とバイト単位で同じになり、
欄の有無から「承認が途中で止まった」と読み違える形が無くなる。前の版は承認の記録
（`ccnavi_approved`）・`project:`・`workflow:` を書き足していた。残っている古い承認済みチケットは
そのまま読む（消さない）。

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

## 待ち方の固定

全体計画の待ち方（`workflow.compute` の結果）は、承認のときに `phases/<親>/workflow.yml` へ
固定する（`--agree` の新規の承認と改版が書く）。承認のあとに `phases.yml` が変わっても、作業中の
親の待ち方と延期の引き受け手がユーザの見ていないところで変わらないようにするため。読む側は
承認済みチケットを読むときにこのファイルを `Ticket.workflow` に入れる（`load_copy`）。
手で動かした承認にはこのファイルが無く、一直線で読む。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import yaml

from ..infra import fsio, settings, tree
from ..policy import rules
from . import approval_checks, approval_marks, archive, flow, history, syncstate
from . import ticket as ticket_mod

# 承認済みチケットの下の置き場。作業中（判定が読む）、閉じた、マーカーと記録。
DOING_DIR = ticket_mod.DOING
DONE_DIR = ticket_mod.DONE
# 続きの子の目印（トップレベルの欄）。前の版は承認の記録（`ccnavi_approved`）の中に書いていた。
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
    return os.path.join(base, ticket_mod.REVIEW, ticket_id + ".md")


def copies(approved_dir: str, closed: bool = False) -> tuple[list[ticket_mod.Ticket], list[str]]:
    """承認済みチケットの一覧。closed なら閉じたもの。2 つめは読めなかったものの説明。

    `state` を添える。閉じたものは取り消しの欄で `done` と `cancelled` に分ける。
    """
    directory = os.path.join(approved_dir, DONE_DIR if closed else DOING_DIR)
    return _load_dir(directory, approved_dir=approved_dir)


def _load_dir(
    directory: str, require_record: bool = False, approved_dir: str = ""
) -> tuple[list[ticket_mod.Ticket], list[str]]:
    found, unread = _load_dir_detail(directory, require_record, approved_dir)
    return found, [message for _, message in unread]


def _load_dir_detail(
    directory: str, require_record: bool = False, approved_dir: str = ""
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
        ticket, reason = load_copy(path, require_record, approved_dir)
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
        out.extend(_load_dir_detail(directory, require, approved)[1])
    return out


def state_of(directory: str, ticket: ticket_mod.Ticket) -> str:
    """置き場の名前から状態を引く。`done/` は取り消しの欄で 2 つに分ける。"""
    name = os.path.basename(directory)
    if name == DONE_DIR:
        return ticket_mod.CANCELLED if ticket.cancelled_at else ticket_mod.DONE
    return name


def load_copy(
    path: str, require_record: bool = False, approved_dir: str = ""
) -> tuple[ticket_mod.Ticket | None, str]:
    """承認済みチケットを 1 本読む。2 つめは読めなかった理由。

    理由を返すのは、読めない承認済みチケットを `--lint` が名指しするため。
    「読めない」だけでは、BOM のような目に見えない原因に気づけない。

    `require_record` は `completed_at`（空でない値）を必須にするか。`.ccnavi/approved/` は
    組み込みの保護がエージェントの書き込みを止めるので、置き場だけで承認と言える。
    `wip/proposals/review/` はエージェントが書ける側にあるので、そこは `finish` が書く
    `completed_at` を 2 つめの保護として求める（`review_all`）。保護が 1 つしか無い置き場で
    欄まで外すと、組み込みの deny に止められずに置かれたファイルが承認済みとして読まれる。
    前は承認の記録 `ccnavi_approved` を求めていた。欄が `completed_at` に替わるだけで、どちらも
    組み込みの deny が破れてエージェントが `review/` に書ければ偽れる。2 つめの保護としての強さは
    変わらない。前の版の承認済みチケットも `finish` を通れば `completed_at` を持つので読める。

    `approved_dir` はそのチケットの承認済みの置き場。渡せば、親の待ち方を
    `phases/<親>/workflow.yml` から読んで `Ticket.workflow` に入れる。
    チケットの中の `workflow:` 欄は、
    前の版の承認の記録を持つ古い承認済みチケットのときだけ読む（ファイルが在ればファイルを採る）。
    それ以外のチケットの `workflow:` 欄は読まず、`blocking_problems` が止める。
    """
    ticket, problems = ticket_mod.load(path)
    if ticket is None:
        detail = problems[0].detail if problems else "チケットとして読めない"
        return None, detail
    # 前の版の承認が書いた記録（`ccnavi_approved`）。いまの承認は書かない。承認を本物とするのは
    # 置き場で、`.ccnavi/approved/` は組み込みの保護がエージェントの書き込みを止める。
    # 欄が無いぶんの検査（親子・計画・置き場）は `blocking_problems` が判定の側で当てる。
    if require_record and not ticket.completed_at:
        return None, "`completed_at` が無い。`finish` を通っていない"
    meta = ticket.raw.get(ticket_mod.APPROVAL_KEY)
    if approval_checks.has_record(ticket):
        ticket.approved_at = str(meta.get("approved_at") or "")
        ticket.source_tree = str(meta.get("source_tree") or "")
        ticket.source_path = str(meta.get("source_path") or "")
    else:
        # 新しい形では待ち方は欄に無い。手で書いた `workflow:` を承認済みの待ち方として効かせない。
        ticket.workflow = None
    if approved_dir and not ticket.is_child:
        held, why = read_workflow(approved_dir, ticket.ticket)
        if why:
            ticket.workflow = None
            ticket.workflow_unreadable = why
        elif held is not None:
            ticket.workflow = held
    # tree は「どのツリーで見つけたか」。scan_all が入れ直す。source_tree（どのツリーの
    # 提案をコピーしたか）とは違うもので、子のワークツリーの checkout では食い違う。
    ticket.tree = ticket.source_tree
    return ticket, ""


def workflow_path(approved_dir: str, parent: str) -> str:
    """親の待ち方を固定するファイル（`phases/<親>/workflow.yml`）。"""
    return os.path.join(
        approved_dir, approval_marks.PHASES_DIR, parent, approval_marks.WORKFLOW_FILE
    )


def workflow_bytes(wf: ticket_mod.Workflow) -> bytes:
    """待ち方のファイルの中身。改行は LF に固定する。

    Chrome のコミットと手元で同じバイト列にするため。
    """
    dumped = yaml.safe_dump(
        wf.as_raw(), allow_unicode=True, sort_keys=False, default_flow_style=False
    )
    return dumped.encode("utf-8")


def read_workflow(approved_dir: str, parent: str) -> tuple[ticket_mod.Workflow | None, str]:
    """親の待ち方のファイルを読む。無ければ (None, "")、読めなければ (None, 理由)。

    fsio を通す。承認の plan の中では同じ承認で書いた後の姿を読み、承認のダイジェストの
    `read_set` にも入る（判定が読んだものとして）。
    """
    path = workflow_path(approved_dir, parent)
    data = fsio.read_bytes(path)
    if data is None:
        if fsio.lexists(path):
            return None, f"待ち方のファイル {path} を読めない"
        return None, ""
    try:
        raw = yaml.safe_load(data.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError):
        return None, f"待ち方のファイル {path} を YAML として読めない"
    wf, bad = ticket_mod.parse_workflow(parent, raw)
    if wf is None:
        detail = bad[0].detail if bad else "形が読めない"
        return None, f"待ち方のファイル {path} の{detail}"
    return wf, ""


def write_workflow(approved_dir: str, parent: str, wf: ticket_mod.Workflow) -> str:
    """親の待ち方のファイルを書く。書けなかった理由を返す。書けたら空文字。

    同じ中身が既に在れば書かない（改版で待ち方が変わらないときに、変わらないファイルを
    書いたことにしない。Chrome はそれを 1 コミットの変更として運ぶ）。
    """
    path = workflow_path(approved_dir, parent)
    content = workflow_bytes(wf)
    if fsio.read_bytes(path) == content:
        return ""
    with fsio.policy(message="待ち方を書けない ({reason})"):
        failed = fsio.write_bytes(path, content)
    return f"待ち方を書けない ({failed})" if failed else ""


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

    ここに在るのは承認済みチケットが `finish` で動いてきたもの。`completed_at`（空でない値）を
    持たないファイルは読まない。この置き場はエージェントが書ける側にあり、保護は組み込みの
    deny 1 つなので、`finish` が書く欄を 2 つめの保護として残す（`load_copy`）。
    """
    found: list[ticket_mod.Ticket] = []
    notes: list[str] = []
    for t in trees(conf, root):
        directory = os.path.join(t.root, conf.tickets.replace("/", os.sep), ticket_mod.REVIEW)
        got, complaints = _load_dir(
            directory, require_record=True, approved_dir=settings.approved_dir(conf, t.root)
        )
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
            approval_checks.mark_blocked(conf, kept)
            approval_checks.mark_imported(conf, root, kept)
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
        approval_checks.mark_blocked(conf, kept)
        approval_checks.mark_imported(conf, root, kept)
    return kept, notes


def scan_review(
    conf: settings.Settings, root: str, raw: Raw | None = None
) -> tuple[list[ticket_mod.Ticket], list[str]]:
    """レビュー待ちのチケット。本物とするツリーの側だけを残す（`scan` と同じ規則）。"""
    if raw is not None:
        kept = archive.drop_archived(root, _authoritative(raw.review, raw.everything))
        approval_checks.mark_imported(conf, root, kept)
        return kept, list(raw.review_notes)
    found, notes = review_all(conf, root)
    kept = archive.drop_archived(root, _authoritative(found, _everything(conf, root, review=found)))
    approval_checks.mark_imported(conf, root, kept)
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
    settled = approval_checks._by_id(everything)
    out: list[tuple[ticket_mod.Ticket, str]] = []
    for ticket_id, hits in approval_checks._by_id(found).items():
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
        st = approval_checks.family_standing(conf, root, t, fams)
        if st.imported and st.stop:
            text += (
                "。取り込み済みの親子のチケットが止まっている: "
                + approval_checks.family_stop_text(root, st)
            )
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
    seen = approval_checks._by_id(everything)
    kept: list[ticket_mod.Ticket] = []
    for ticket_id, hits in approval_checks._by_id(found).items():
        where = ticket_mod.authority(seen.get(ticket_id) or hits)
        if where is None:
            kept.extend(hits)
        else:
            kept.extend(t for t in hits if t.tree == where)
    return kept


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
    wf: ticket_mod.Workflow | None = None,
) -> str:
    """承認した提案を `doing/` へ動かす。動かせなかった理由を返す。動かせたら空文字。

    **中身は変えない。** 提案をバイト列のまま読み（`fsio.read_bytes`）、同じバイト列を `doing/` に
    書いて元を消す。欄を書き足さず、改行も BOM も変えない。端末・ボード・Chrome・手で動かす、の
    どれで承認しても承認済みチケットが提案とバイト単位で同じになるように。消せなければ書いた側を
    消して戻す。両方に残ると、以後どの操作も「複数の場所にある」で止まる。

    `wf` は計画を持つ親の待ち方。`phases/<親>/workflow.yml` に固定する（チケットには書かない）。
    待ち方は提案を動かす前に書き、そのあとの段で落ちたら前の中身へ戻す。承認済みチケットだけが
    置かれて待ち方が無い形は、手で動かした承認と同じに一直線で読まれ、取り下げの検査も通って
    しまうので、待ち方だけが残る側（承認済みチケットが無いので効かない）に寄せる。
    `source_tree` は提案が乗っていたブランチの名前で、状態の履歴に残す。
    """
    target = copy_path(approved_dir, ticket.ticket)
    content = fsio.read_bytes(ticket.path)
    if content is None:
        return f"提案を読めない ({ticket.path})"
    restore: tuple[tuple[str, bytes | None], ...] = ()
    if wf is not None:
        held = workflow_path(approved_dir, ticket.ticket)
        restore = ((held, fsio.read_bytes(held)),)
        failed = write_workflow(approved_dir, ticket.ticket, wf)
        if failed:
            return failed
    with fsio.policy(message="書けない ({reason})", restore=restore):
        failed = fsio.write_bytes(target, content)
    if failed:
        fsio.put_back(restore)
        return f"書けない ({failed})"
    # 消せなければ書いた側を消して戻す。承認の plan では落ちたときの枝が走らないので、
    # 同じ戻し方を Writer(FS) へ渡す。置けたと数えるのは消せたとき。
    with fsio.policy(
        message="提案を todo/ から動かせない ({reason})",
        undo=(target,),
        restore=restore,
        places=ticket.ticket,
    ):
        failed = fsio.unlink(ticket.path)
    if failed:
        fsio.remove(target)
        fsio.put_back(restore)
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
    return approval_marks._write(path, ticket_mod.set_fields(text, fields))


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
    # 続きの子の目印。提案を経ずにここで作るチケットなので、作るときに書く（承認で中身を
    # 変えることには当たらない）。時刻は状態の履歴の `raised` に残る。
    front[FOLLOWUP_KEY] = [c.ticket for c in children]
    body = ["", "## 引き継ぐ指摘", ""]
    body += [f"- {item}" for item in items] or ["（指摘の一覧は無い）"]
    body.append("")
    t = ticket_mod.Ticket(ticket=ident, raw=front, body="\n".join(body))
    failed = approval_marks._write(copy_path(where, ident), ticket_mod.render(t))
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
    cleared = approval_marks.clear_marks(where, parent.ticket, phase_no)
    for warning in cleared.warnings:
        history.failed_to_write(warning)
    if cleared.failed:
        return ident, cleared.failed
    return ident, ""


def predecessor_pool(conf: settings.Settings, root: str) -> dict[str, list[ticket_mod.Ticket]]:
    """いまの置き場から先行を引く対応表を組む。"""
    open_copies, _ = scan(conf, root)
    review, _ = scan_review(conf, root)
    closed, _ = scan(conf, root, closed=True)
    proposals, _ = ticket_mod.scan(root, conf.tickets, conf.projects)
    pool = approval_checks.predecessor_pool_of(open_copies, review, closed, proposals, root)
    approval_checks.align_imported(conf, root, pool)
    return pool
