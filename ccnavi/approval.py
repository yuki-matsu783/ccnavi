"""承認済みチケット。人がチケットに合意したことの記録で、判定はここだけを読む。

## なぜ承認済みチケットが権威なのか

チケットの提案はエージェントが書ける。判定が提案を直接読むと、範囲の外で
止められたエージェントが範囲を書き足して通れる。承認のときに承認済みチケットを置き、判定は
承認済みチケットだけを読む。書き足した提案は承認済みチケットに届かない。

## 承認済みチケットを守るのは置き場

承認済みチケットは `.ccnavi/approved/` に置く。ccnavi ディレクトリの下なので、組み込みの
守りがエージェントの書き込みを止める。

**権威はこの置き場で、`ccnavi_approved` の欄ではない（ADR-0058）。** 欄は承認の記録で、
持たないチケットも承認済みとして読む。そこに置けたのは書ける権限を持つ人だけだから。
だから提案を手で `doing/` へ動かすことが、端末とボードに続く 3 つめの承認の経路になる。
その経路は承認の画面を通らないので、承認のときにしか当たらなかった構造の検査は
`blocking_problems` が判定の側で当てる。

## チケットは 1 本のファイルで、写しを持たない（ADR-0055）

承認は `wip/proposals/todo/` の提案を `.ccnavi/approved/doing/` へ動かす。
エージェントが打つ `finish` は `doing/` から `wip/proposals/review/`
（レビュー要）か `.ccnavi/approved/done/`（不要）へ動かし、人がレビューを済ませると
`review/` から `done/` へ動く。人が動かす向きは `.ccnavi/approved/` へ、エージェントが
動かす向きは `wip/proposals/` へ。

## 閉じる向きだけは承認が要らない

範囲が消える向きなので、エージェントが動かしても危険は増えない。逆に承認済みチケットを
`done/` から戻す（再開）のは人の手でやる。

## フェーズのマーカー

`phases/<親>/<N>.<種類>` に、依頼・レビュー済み・省略・通知済みのマーカーを置く。
レビューが済むまで止める判定（phase.py）はこれを見る。中身は JSON 1 つで、いつ誰が置いたかが入る。
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field, replace
from typing import TextIO

from . import flow, fsio, history, phasetypes, rules, settings, syncstate, tree, workflow
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
    """作業中の承認済みチケット（`doing/`）の綴り。判定が読むのはここだけ。"""
    return os.path.join(approved_dir, DOING_DIR, ticket_id + ".md")


def closed_path(approved_dir: str, ticket_id: str) -> str:
    """閉じた承認済みチケット（`done/`）の綴り。"""
    return os.path.join(approved_dir, DONE_DIR, ticket_id + ".md")


def review_path(tree_root: str, tickets_rel: str, ticket_id: str) -> str:
    """レビュー待ちのチケット（提案の置き場の `review/`）の綴り。"""
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
        # fsio を通す。承認の plan（控える段）の中では、同じ承認で動かした後の置き場を読む。
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
    組み込みの守りがエージェントの書き込みを止めるので、置き場だけで承認と言える
    （ADR-0058）。`wip/proposals/review/` はエージェントが書ける側にあるので、そこは
    欄を求め続ける（`review_all`）。守りが 1 つしか無い置き場で欄まで外すと、
    組み込みの deny に止められずに置かれたファイルが承認済みとして読まれる。
    """
    ticket, problems = ticket_mod.load(path)
    if ticket is None:
        detail = problems[0].detail if problems else "チケットとして読めない"
        return None, detail
    # `ccnavi_approved` は承認の記録であって、承認そのものではない（ADR-0058）。権威は
    # 置き場で、`.ccnavi/approved/` は組み込みの守りがエージェントの書き込みを止める。
    # 欄を必須にすると、端末もボードも無い人が置き場を動かして承認する経路が使えなくなる。
    # 欄が無いぶんの検査（親子・計画・置き場）は `blocking_problems` が判定の側で当てる。
    meta = ticket.raw.get(ticket_mod.APPROVAL_KEY)
    if require_record and not isinstance(meta, dict):
        return None, f"`{ticket_mod.APPROVAL_KEY}` の欄が無い。承認を通っていない"
    if isinstance(meta, dict):
        ticket.approved_at = str(meta.get("approved_at") or "")
        ticket.source_tree = str(meta.get("source_tree") or "")
        ticket.source_path = str(meta.get("source_path") or "")
    # tree は「どのツリーで見つけたか」。scan_all が入れ直す。source_tree（どのツリーの
    # 提案を写したか）とは違うもので、子のワークツリーの checkout では食い違う。
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
    同じものが checkout されている。まとめないほうは、ボードが「どこに写っているか」を
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
    ファイルは読まない。この置き場はエージェントが書ける側にあり、守りは組み込みの
    deny 1 つなので、欄を 2 つめの守りとして残す（ADR-0058）。
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


def scan(
    conf: settings.Settings, root: str, closed: bool = False
) -> tuple[list[ticket_mod.Ticket], list[str]]:
    """判定と承認が読む承認済みチケット。権威のあるツリーの側だけを残す。

    権威は親のツリー（親自身なら自分のツリー）。提案の `dedupe` と違い、そこに無ければ
    落とす。子のワークツリーに checkout されているのは切った時点の版なので、親のツリーで
    閉じたあとも開いた版が残る。「権威の側に無ければ全部残す」にすると、閉じたチケットが
    開いたものとして復活する。親のツリーがその識別子をどの置き場（作業中・レビュー待ち・
    閉じた）にも持っていなければ元ツリー（ワークスペースルート。プロジェクトのチケットなら
    そのプロジェクト）の側を採り、そこにも無いときと、元ツリーより先の置き場に在る写しが
    あるときだけ、見つかった側を全部残す（`ticket.fold` と同じ順・同じ条件）。

    返す前に `mark_blocked` が「信頼できない理由」の印を付ける。承認のときにしか
    当たらなかった構造の検査を、判定の側でも当てるため（ADR-0058）。
    """
    found, notes = scan_all(conf, root, closed)
    # 読んだ側を `_everything` に渡す。渡さないと、この同じ式の中でまったく同じ
    # `scan_all` をもう 1 度呼ぶことになる（下記）。
    if closed:
        everything = _everything(conf, root, closed_all=found)
    else:
        everything = _everything(conf, root, open_all=found)
    kept = _authoritative(found, everything)
    if not closed:
        # 判定が読むのは作業中の側だけ。閉じたものに印は要らない。
        mark_blocked(conf, kept)
        mark_imported(conf, root, kept)
    return kept, notes


def scan_review(conf: settings.Settings, root: str) -> tuple[list[ticket_mod.Ticket], list[str]]:
    """レビュー待ちのチケット。権威のあるツリーの側だけを残す（`scan` と同じ規則）。"""
    found, notes = review_all(conf, root)
    kept = _authoritative(found, _everything(conf, root, review=found))
    mark_imported(conf, root, kept)
    return kept, notes


def mark_imported(
    conf: settings.Settings,
    root: str,
    kept: list[ticket_mod.Ticket],
    fams: syncstate.Families | None = None,
) -> None:
    """取り込み済みの家族の写しに、信頼できない理由の印を付ける（ADR-0093 の 3.3）。

    取り込み済みの家族は、家族の控えがある家族。

    権威は親のブランチ `P`（手元では `.claude/worktrees/<P>` で HEAD が `P` を指すツリー）の
    写しだけ。次の写しは読むが信頼しない（`blocked`。判定は範囲を使わずに止める）。

    - 家族が決まらない（控えが `gone`・`blocked`・壊れている、`present` なのに親のワークツリーが
      無い）
    - 家族が閉じている（統合先の `done/` が権威）
    - 親のワークツリーの外にしか無い写し（元ツリーに未コミットで残った写しなど。3.5）

    **写しの並びは変えない（落とさない）。** 落とすと「在る」ことで止まっていたもの（承認待ちの
    重複、開いた子のある親を閉じない）が通るようになる。印を足すだけなので、控えの無い家族と、
    控えがあっても親のワークツリーの写しだけの家族では、答えは前と同じ。すでに印（`mark_blocked`）が
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
    """親のワークツリーの外にしか無い写しを信頼しない理由と、人が運ぶ手順（ADR-0093 の 3.5）。"""
    return (
        f"親のブランチ {st.family} のワークツリーの外"
        f"（{t.tree or 'ワークスペースルート'}）にしか無い写し。"
        "取り込み済みの家族では親のブランチの写しだけが本物。人がその写しを親のワークツリー"
        f"（.claude/worktrees/{st.family}）の同じ置き場へ運んでコミットと push をし、"
        "元の写しを消す"
    )


def family_standing(
    conf: settings.Settings,
    root: str,
    t: ticket_mod.Ticket,
    fams: syncstate.Families | None = None,
) -> syncstate.Standing:
    """このチケットの家族の立ち位置（`syncstate.Families.standing`）。家族は `parent` か自分。"""
    fams = fams or syncstate.Families(conf, root)
    return fams.standing(t.parent or t.ticket, t.project or "")


def family_problems(
    conf: settings.Settings,
    root: str,
    t: ticket_mod.Ticket,
    fams: syncstate.Families | None = None,
) -> list[rules.Problem]:
    """取り込み済みの家族の提案を承認しない理由（ADR-0093 の 3.3・3.6・8.4）。

    家族が決まらない・閉じているなら承認しない。提案は親のブランチの上で書く（3.2）ので、
    親のワークツリーの外にある提案も承認しない。控えの無い家族は何も言わない。
    """
    fams = fams or syncstate.Families(conf, root)
    if not fams.active:
        return []
    st = family_standing(conf, root, t, fams)
    if not st.imported:
        return []
    if st.stop:
        hint = " / ".join(syncstate.guidance(root, st))
        return [rules.Problem(rules.SEVERITY_ERROR, t.ticket, f"{st.stop}。承認しない。{hint}")]
    if st.home is not None and not syncstate.same_tree(t.tree_root, st.home.root):
        return [
            rules.Problem(
                rules.SEVERITY_ERROR,
                t.ticket,
                f"提案が親のブランチ {st.family} のワークツリーの外"
                f"（{t.tree or 'ワークスペースルート'}）にある。取り込み済みの家族の提案は"
                f"親のワークツリー（.claude/worktrees/{st.family}）で書いて push してから"
                "承認を頼んでください",
            )
        ]
    return []


def integration_problems(
    conf: settings.Settings,
    root: str,
    t: ticket_mod.Ticket,
    fams: syncstate.Families | None = None,
) -> list[rules.Problem]:
    """新規の提案の識別子を統合先の `done/` と比べる（ADR-0093 の 3.3 の 4）。

    開いた家族でも統合先の `done/` は常に一緒に読む。古い統合先から切った `P` で、閉じた
    識別子の再利用が新規の承認として通らないように。統合先の `done/` は控え（D26）から、
    家族の控えの有無に依らず、取り込んだ跡のあるリポジトリ（`sync/<リポジトリ>/`）の全提案に当てる。
    控えが壊れている・読めない・入れ替えが終わらないときは、確かめられないので「決まらない」として
    承認しない（何も出さずに通すことはしない）。一度も取り込んでいないリポジトリは何も言わない
    （今のまま）。
    """
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
                f"統合先の控え（sync/{repo}/integration）を読めない（{why}）。閉じた識別子の"
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
    """統合先の控えの `done/` に同じ識別子がある新規の提案（3.3 の 4）。"""
    fams = syncstate.Families(conf, root)
    if not fams.active:
        return set()
    return {
        t.ticket
        for t in proposals
        if t.state == ticket_mod.TODO and t.ticket in fams.done(syncstate.repo_key(t.project))[0]
    }


def _everything(
    conf: settings.Settings,
    root: str,
    *,
    open_all: list[ticket_mod.Ticket] | None = None,
    closed_all: list[ticket_mod.Ticket] | None = None,
    review: list[ticket_mod.Ticket] | None = None,
) -> list[ticket_mod.Ticket]:
    """作業中・レビュー待ち・閉じたの全部を、重複をまとめずに。権威のツリーを決めるために使う。

    3 つは呼び手が持ち込める。**控えではなく、同じ呼び出しの中で今しがた読んだものを
    渡してもらう仕組み。** `scan` は `scan_all` を呼んだ直後にここを呼ぶので、渡さないと
    同じ引数の `scan_all` が 1 つの式の中で 2 回走る。置き場のチケットは 1 本ずつ
    YAML として解析されるので、この重複はチケットの本数にそのまま比例する。

    渡すのは「自分が読んだ側」だけで、残りはここで読む。読む範囲も読む順も変わらない。
    控えを持たないので、判定の途中でファイルが動く経路（`ticket finish` が親を締めた後、
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
    at_home = {t.ticket for t in everything if t.tree == (t.parent or t.ticket)}
    seen = _by_id(everything)
    # 親のツリーが無いとき（作る前と、合流して片付けた後）は元ツリーが権威。ワークツリーは
    # 片付ければ消えるが、元ツリー（ワークスペースルート。プロジェクトのチケットならその
    # プロジェクト）は消えない。ただし元ツリーより先の置き場に在る写しがあれば採らない
    # （合流していない側が新しい形）。`ticket.fold` と同じ順・同じ条件で決める。
    at_origin = {ticket_id for ticket_id, hits in seen.items() if _origin_is_current(hits)}
    # リポジトリをまたいだ衝突はまとめない（`ticket.fold` と同じ）。違うチケットなので、権威を
    # 決めるとどちらかが気づかないうちに消え、`--lint` の「複数のリポジトリにある」も出なくなる。
    crossing = {ticket_id for ticket_id, hits in seen.items() if len({t.project for t in hits}) > 1}
    kept: list[ticket_mod.Ticket] = []
    for ticket_id, hits in _by_id(found).items():
        home = hits[0].parent or hits[0].ticket
        if ticket_id in crossing:
            kept.extend(hits)
        elif ticket_id in at_home:
            kept.extend(t for t in hits if t.tree == home)
        elif ticket_id in at_origin:
            kept.extend(t for t in hits if t.tree == ticket_mod.origin_tree(t))
        else:
            kept.extend(hits)
    return kept


def _origin_is_current(hits: list[ticket_mod.Ticket]) -> bool:
    """元ツリーがその識別子を持ち、かつ先へ進んだ写しが他に無いか（`ticket.fold` と同じ）。"""
    origin = [t for t in hits if t.tree == ticket_mod.origin_tree(t)]
    return bool(origin) and not ticket_mod.behind(origin, hits)


def _by_id(found: list[ticket_mod.Ticket]) -> dict[str, list[ticket_mod.Ticket]]:
    """識別子ごとの写りの全部。並びは見つけた順。"""
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
    取り込みの控えのあるリポジトリを全部探す（同じ識別子が 2 つのリポジトリにあれば決めない）。
    """
    home = parent or ticket_id
    # 取り込み済みの家族は親のブランチ（親のワークツリー）だけに書く（ADR-0093 の 3.4）。
    # 元ツリーに未コミットで書く形（ADR-0073）はやめる。決まらない家族は、ここへ来る前に
    # 状態の操作と承認が止める。
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
    """提案の、そのリポジトリ（ツリー）からの相対パス。区切りは "/"（ADR-0093 の D22）。

    ツリーの外にある（ツリーが分からない）ときだけ、綴りをそのまま返す。
    """
    if t.tree_root and t.path:
        try:
            rel = tree.relative(tree.Tree(t.tree, t.tree_root), t.path)
        except ValueError:
            # Windows で別のドライブ（relpath が相対を作れない）。綴りのまま返す。
            return t.path
        if rel and not rel.startswith("../") and rel != "..":
            return rel
    return t.path


def source_branch(t: ticket_mod.Ticket) -> str:
    """提案が乗っていたブランチの名前（ADR-0093 の D22）。

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
    相対パス（ADR-0093 の D22）。手元と Chrome で写しの中身を同じにするため。前は
    ツリーの名前と絶対パスを書いていた。読む側（`load_copy`）はどちらの形も読み、判定は
    この 2 つを読まない（`diagnose` が見せるだけ）。
    """
    meta = {
        "approved_at": approved_at,
        "source_tree": source_tree,
        "source_path": source_path(ticket),
    }
    target = copy_path(approved_dir, ticket.ticket)
    # 人の書いた行（コメント、`|` のブロック）を保つ。読み直して書き出さず、承認の記録の
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
    """チケットの、スクリプトが書く欄だけを行単位で書き換える。人の書いた本文は保つ。"""
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

    フローの置き場は承認済みチケットと同じツリー（設計 9.3.1）。人は承認の前に、提案が在る
    ツリーの置き場へボードで保存する。承認で子が別のツリー（親のワークツリーなど）へ動くと、
    フローだけが元のツリーに残り、読まれなくなる（M-3）。承認は人の操作なので、ここで一緒に
    動かす。行き先に違う中身のフローが既に在れば上書きせず、そう言う（元のほうも残す）。
    リンク・ふつうのファイルでないもの・ハードリンクは運ばない（`flow.load` と同じ読み方）。
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
            f"{proposal.ticket} のフロー {source} を運ばなかった: {why}。"
            "ユーザが確かめて置き直してください"
        ]
    if fsio.lexists(target):
        held, _ = flow.read_bytes(target)
        if held != raw:
            return [
                f"{proposal.ticket} のフローを {target} へ運ばなかった: 行き先に違う中身のフローが"
                f"既に在る（上書きしない）。{source} と見比べて、人が 1 本に決める"
            ]
        fsio.remove(source)
        return []
    # 落ちたときの行は承認の plan でも同じものを出せるよう、書き込みにつける（`FAIL_LINE`）。
    cannot = f"{proposal.ticket} のフローを {target} へ運べない ({{reason}})。{source} に残っている"
    with fsio.policy(message=cannot):
        failed = fsio.write_new(target, raw)
    if failed:
        return [cannot.replace("{reason}", failed)]
    kept = (
        f"{proposal.ticket} のフローを {target} へ写した。元の {source} は消せなかった ({{reason}})"
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

    同じファイルシステムの中なら rename 1 回で済む。またぐとき（EXDEV）だけ写して消す。
    消せなければ写した側を消して戻す。両方に残ると、以後どの操作も「複数の場所にある」で
    止まる（`admit` と同じ）。Windows は開かれているファイルを消させないので、現実に起きる。

    写して消す処理に回すのは EXDEV に限る。rename が他の理由（元が無い、など）で失敗した
    ときまで回すと、写せずに戻す処理が、その間に別のプロセスが置いた行き先を消す。
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

    人がレビューを済ませたときに呼ぶ（`confirm` / `decide` / `--reviewed --chat` /
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


def next_child_id(conf: settings.Settings, root: str, parent_id: str) -> str:
    """この親の次の子の識別子。どの置き場に在る子よりも後ろの連番。"""
    seen = _everything(conf, root)
    proposals, _ = ticket_mod.scan(root, conf.tickets, conf.projects)
    used = 0
    for t in seen + proposals:
        m = ticket_mod.child_pattern().match(t.ticket)
        if m and m.group("parent") == parent_id:
            used = max(used, int(m.group("seq")))
    return f"{parent_id}-{used + 1:02d}"


def followup(
    conf: settings.Settings,
    root: str,
    parent: ticket_mod.Ticket,
    phase_no: int,
    children: list[ticket_mod.Ticket],
    items: list[str],
    stamp: str,
) -> tuple[str, str]:
    """レビューで残った指摘の続きの子を、人の判断で `doing/` に直に起こす（ADR-0055）。

    人が端末で「続きの子で直す」と選んだことが承認そのもの。同じフェーズの番号に足すので、
    そのフェーズは開き直り、マーカーは消える（REQ-TKT-21）。範囲は見た子の範囲の和。
    本文には指摘を写す。返すのは識別子と、起こせなかった理由。
    """
    ident = next_child_id(conf, root, parent.ticket)
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
    # 続きの子が着手できなくなる（ADR-0088）。範囲の和には入れる（見たのは同じフェーズの全部）。
    front["predecessors"] = [c.ticket for c in children if not c.cancelled_at]
    front["human_review"] = {"required": True, "reason": "レビューで残った指摘への対応"}
    front["title"] = f"フェーズ {phase_no} のレビューの指摘に応える"
    front["rationale"] = (
        f"フェーズ {phase_no} のレビューで残った指摘に応える。"
        "利用者が端末で起こした続きの子で、承認はその判断で済んでいる。\n"
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


# ---- 先行（`predecessors`）を満たしているか（ADR-0088）

# 先行の状態。満たしたとみなすのは `done` だけ（`.ccnavi/approved/done/` に在り、取り消しでない）。
PRED_DONE = ticket_mod.DONE
PRED_CANCELLED = ticket_mod.CANCELLED
PRED_MISSING = "missing"
PRED_SCATTERED = "scattered"
# 形の上で満たせない先行。待っても通らないので、ふつうの error にする。
PRED_SELF = "self"  # 自分自身
PRED_ANCESTOR = "ancestor"  # 自分の親（子は親の中の作業で、親は子より先に閉じない）
PRED_CYCLE = "cycle"  # 先行を辿ると自分に戻る
# 取り込み済みの家族の先行で、その家族が決まらない・親のブランチの写しに無い
# （ADR-0093 の 3.3 の 5）。
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
    PRED_UNDECIDED: "家族が決まらない",
}


@dataclass
class Predecessor:
    """先行 1 本の今。`where` は複数の場所にあるときの在り処（`ツリー:置き場` の並び）。"""

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
) -> dict[str, list[ticket_mod.Ticket]]:
    """先行を引く池。識別子 → 権威のある写りの全部（`ops._places` と同じ集め方）。

    承認済みチケット（作業中・レビュー待ち・閉じた）はどれも数える。`todo/` の提案は、同じ識別子の
    承認済みチケットがどこにも無いときだけ数える（在れば改版の候補か書き損じ）。写りが 2 つ以上
    残れば、どれが本物か決まらない。
    """
    pool = _by_id(open_copies + review + closed)
    for t in proposals:
        if t.state == ticket_mod.TODO and t.ticket not in pool:
            pool.setdefault(t.ticket, []).append(t)
    return pool


def predecessor_pool(conf: settings.Settings, root: str) -> dict[str, list[ticket_mod.Ticket]]:
    """いまの置き場から先行を引く池を組む。"""
    open_copies, _ = scan(conf, root)
    review, _ = scan_review(conf, root)
    closed, _ = scan(conf, root, closed=True)
    proposals, _ = ticket_mod.scan(root, conf.tickets, conf.projects)
    pool = predecessor_pool_of(open_copies, review, closed, proposals)
    align_imported(conf, root, pool)
    return pool


def align_imported(
    conf: settings.Settings, root: str, pool: dict[str, list[ticket_mod.Ticket]]
) -> None:
    """取り込み済みの家族の先行を、その家族の親のブランチの写しで読み直す（ADR-0093 の 3.3 の 5）。

    Chrome は先行を、参照の閉包の家族の `P` から引く。手元もそれに揃える。ただし**通る向きには
    読み替えない**（段階 2c は締める向きだけ）。

    - 家族が決まらない（控えが `gone`・`blocked`・壊れている、親のワークツリーが無い）:
      「家族が決まらない」にする（切り直しを案内する）
    - 親のワークツリーにその識別子の写しが無い: 同じく「家族が決まらない」（親のブランチの外に
      しか無い）
    - 親のワークツリーの写しが 1 つで、閉じていない（作業中・レビュー待ち・承認待ち）: それを採る
    - 親のワークツリーの写しで閉じている、写しが 2 つ以上: 前の池のまま（手元の全ツリーから
      引いた答え。前の池で満たしていなければ満たさないまま）
    - 閉じた家族（統合先の `done/` に親の写しがある）と控えの無い家族: 前の池のまま
    """
    fams = syncstate.Families(conf, root)
    if not fams.active:
        return
    for ident, hits in list(pool.items()):
        if not hits:
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
                    f"親のブランチ {st.family} のワークツリーに {ident} の写しが無い"
                    f"（{', '.join(h.tree or '(ワークスペースルート)' for h in hits)} にしか無い）",
                )
            ]
        elif len(mine) == 1 and mine[0].state != PRED_DONE:
            pool[ident] = mine


def _undecided(sample: ticket_mod.Ticket, why: str) -> ticket_mod.Ticket:
    """先行の池に置く「家族が決まらない」の印。理由は `blocked` に入れて運ぶ。"""
    return replace(sample, state=PRED_UNDECIDED, blocked=why)


def predecessor_states(
    t: ticket_mod.Ticket, pool: dict[str, list[ticket_mod.Ticket]]
) -> list[Predecessor]:
    """このチケットの先行のそれぞれの今。並びは `predecessors` の順（重ねて書いたものは 1 つ）。"""
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

    辿るのは池で 1 つに決まるチケットだけ（決まらないものは別の苦情になる）。閉じた（`done/`）
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
    """承認で落とす先行の苦情（ADR-0088）。

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
                    "人のレビューが済む）まで承認しない。先行が要らないなら predecessors から外して"
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
                    f"先行 {p.ticket} の家族が決まらない（{p.where}）。取り込み済みの家族の先行は"
                    "その親のブランチの写しで確かめる。親のワークツリーを切り直すか、"
                    "'ccnavi-sync.sh' で取り込み直してから出し直してください",
                )
            )
        elif p.state == PRED_MISSING:
            problems.append(
                rules.Problem(
                    rules.SEVERITY_ERROR,
                    t.ticket,
                    f"先行 {p.ticket} がどの置き場（todo/・doing/・review/・done/）にも無い。"
                    "綴りを直すか、predecessors から外して出し直してください",
                )
            )
        else:
            problems.append(
                rules.Problem(
                    rules.SEVERITY_ERROR,
                    t.ticket,
                    f"先行 {p.ticket} が{p.label}。どれが本物か決まらないので満たしたとみなさない。"
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
    """`clear_marks` の答え。`kinds` は消せた種類（控える段では消す種類）。

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
    見ないまま先へ進めてしまう（判定を締める向き。ADR-0093 の 11.3）。ほかの種類は
    消せなくても止めず、残っていて判定に使われると言う（`warnings`）。
    跡（phase-reopened の `cleared`）には実際に消せた種類だけを書く。

    控える段（承認の plan）では、消す書き込みに同じ扱いをつけ、跡と見せる行は Writer(FS) が
    書けた種類で書く（`fsio.Call`）。`announce` は消せた種類から見せる行を作る関数。
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
            # 見え方（Changes）には全部消えた後の跡を載せる。ディスクへは Call が書く。
            with fsio.view_only():
                _note_reopened(approved_dir, parent, phase, kinds)

        # 跡の時刻と経路は並べたときのもの（書く時に時計を読み直すと、Changes と食い違う）。
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
                "残るとフェーズは済んだまま読まれるので、ここで止める。人がマーカーを消す"
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
#   close-early.json  人が「キリの良いところまでやった」と締めた。残りは別の issue へ
#   closed.json  親を閉じた（`ticket finish <親>`）。どのフェーズをどこで見たかを残す
#
# closed.json が要るのは、提案（wip/）が統合先に取り込む前に消えるから。マージリクエストを
# 作らない運び方（全フェーズが `review: chat`）では、締めた事実の残る先がここしか無い。
PARENT_MARK_READY = "ready"
PARENT_MARK_CLOSE_EARLY = "close-early"
PARENT_MARK_CLOSED = "closed"


def parent_mark_path(approved_dir: str, parent: str, name: str) -> str:
    return os.path.join(approved_dir, PHASES_DIR, parent, f"{name}.json")


def read_parent_mark(approved_dir: str, parent: str, name: str) -> dict | None:
    return fsio.read_dict(parent_mark_path(approved_dir, parent, name))


# 親のマーカーのうち、状態の跡（history）に残すもの。締めと Draft を外した印。設定を写した印
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


# 人が受け入れたスレッドの控え。フェーズのマーカーとは別の場所に、親ごとに 1 つ置く。
ACCEPTED_FILE = "accepted.json"


def accepted_path(approved_dir: str, parent: str) -> str:
    return os.path.join(approved_dir, PHASES_DIR, parent, ACCEPTED_FILE)


def accepted_threads(
    approved_dir: str,
    parent: str,
    phase: int | None = None,
    owner: ticket_mod.Ticket | None = None,
) -> set[str]:
    """この親で、人が「未解決のまま進める」と受け入れたスレッドの識別。

    `phase` を渡すと、その番号のレビューで受け入れ済みと数えてよいものだけを返す。
    受け入れはそのフェーズと、それを待つ番号（写しの `waits`）にだけ当てはまる（設計 9.8）。
    並行した別の枝のレビューには当てはまらない。番号を持たない受け入れ（`close-early`）は
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
    """受け入れたスレッドを控えに足す。失敗したら、その説明を返す。

    フェーズのマーカーとは別の場所に置く。マーカーは 2 つの理由で消える。同じ番号のマーカーは
    `confirm` が通るたびに上書きされ、その番号に子が足されると `clear_marks` が
    丸ごと消す。どちらでも受け入れの記録が消え、人がもう一度同じスレッドを
    受け入れることになる。人が 1 度言った「これは承知で進める」は、
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


@dataclass
class Candidate:
    """承認の対象の 1 件。新規の提案か、親の改版か。

    リスクの点はここに無い。点は子を閉じるときに実績（差分）で数え、宣言の広さでは
    数えない（risk.py）。宣言の広さは、親が `human_review.reason` で言う。
    """

    ticket: ticket_mod.Ticket
    complaints: list[rules.Problem] = field(default_factory=list)
    # 範囲の超過（親の範囲・種類の上限を超えた項、regex の項）。承認は止めず、判定が
    # 切り詰める。判定に影響する（止まる）ので、判定に影響しない記述の注意（complaints の warn）
    # とは分けて持つ。
    overflow: list[rules.Problem] = field(default_factory=list)
    # 改版なら、いま使われている承認済みチケット。
    current: ticket_mod.Ticket | None = None
    # このチケットに使うフェーズの種類（共通層 + `project:` が指す層、設計 11.4.1）。
    # 承認の対象の中でもチケットごとに違いうるので、候補が引いたものを持っておく。
    types: dict | None = None
    # 承認画面に足す 1 行ずつの注記（フィードバック計画の証跡など）。
    notes: list[str] = field(default_factory=list)

    @property
    def is_revision(self) -> bool:
        return self.current is not None

    @property
    def plans_feedback(self) -> bool:
        return (
            self.is_revision
            and self.current is not None
            and self.current.feedback is None
            and self.ticket.feedback is not None
        )


# 承認の JSON の版。`--approve --preview --json` と `--approve --yes … --json` が名乗る。
# VS Code のボード拡張が読み、知らない番号なら読まずに版の違いを言う。
APPROVE_VERSION = 1


@dataclass
class Gathered:
    """いま `--approve` が見せる一覧と、その周りのもの。見せる・承認するの両方がここから出る。

    一覧を組む関数を 1 つにしてあるのは、拡張が見せたものと実行ファイルが承認する
    ものを同じ答えにするため。`--explain --json` の `pending_approval` も同じ
    `waiting` を通る。
    """

    batch: list[Candidate]
    rejected: list[tuple[ticket_mod.Ticket, list[rules.Problem]]]
    problems: list[str]
    pool: dict
    nothing_pending: bool
    broken: bool
    # 絞り込み（`only`）が通らなかった理由。空でなければ何も承認しない。
    refused: str = ""
    # 端末に出す 1 行（「承認待ち N 件のうち、指定の M 件だけを承認の対象にする」）。
    note: str = ""

    @property
    def text(self) -> str:
        if self.nothing_pending:
            return "承認待ちのチケットは無い。"
        return screen(self.batch, self.pool)

    @property
    def identifiers(self) -> list[str]:
        return sorted(c.ticket.ticket for c in self.batch)


def gather(
    stderr: TextIO, conf: settings.Settings, root: str, only: list[str] | None = None
) -> Gathered:
    """承認の対象を組む。提案を走査し、承認済みチケットと突き合わせ、載せるものと落とすものに分ける。

    読めない提案や承認済みチケット、落とした提案の理由は標準エラーにも出す。端末の人は
    そこで読み、拡張は JSON の `problems` / `rejected` で読む。

    `only` は承認の対象を識別子で絞る（`ccnavi --approve <識別子>...`、拡張のオーバーレイ）。
    ボードが絞り込みで見えている分だけを渡す。絞りは対象を狭めるだけで、絞らないときに
    落ちるものを通してはいけない。だから、承認待ちに無い識別子が入っていたら何も
    承認しない（ボードが古いときに、見せた以外のものを通さないため）。親の改版が
    承認待ちなのに対象から外した子も何も承認しない（外すと旧計画で検証される）。
    通らなかった理由は `refused` に入れて返す。呼び手はそれを見て何もしない。

    フェーズの種類は承認の対象全体で 1 つに決まらない。どの層の種類を使うかは各チケットの
    `project:` が決める（設計 11.4.1）ので、候補を組むところで 1 件ずつ引き、
    引いたものを `Candidate` に持たせる。画面は候補が持つ種類を使う。
    """
    proposals, problems = ticket_mod.scan(root, conf.tickets, conf.projects)
    for problem in problems:
        stderr.write(f"ccnavi: {problem}\n")

    approved, notes = scan(conf, root)
    for note in notes:
        stderr.write(f"ccnavi: {note}\n")
    closed, _ = scan(conf, root, closed=True)
    review, _ = scan_review(conf, root)

    pending, revisions = waiting(
        proposals, approved, closed, review, types_resolver(conf, root, approved)
    )
    broken = any(p.severity == rules.SEVERITY_ERROR for p in problems)
    texts = [str(p) for p in problems] + list(notes)
    note = ""

    if only:
        # 識別子の検査は「承認待ちが無い」より先。承認待ちが空でも、指定したものが
        # 無いのは失敗で、終了コードが他の承認待ちの有無で変わらないように。
        wanted = [i for i in dict.fromkeys(only) if i]
        known = {t.ticket for t in pending + revisions}
        unknown = [i for i in wanted if i not in known]
        if not wanted or unknown:
            lines = [
                f"承認待ちに無い: {', '.join(unknown) or '(空の識別子)'}",
                "何も承認しない。ボードを更新して承認待ちを確かめてください",
            ]
            for line in lines:
                stderr.write(f"ccnavi: {line}\n")
            return Gathered([], [], texts, {}, False, broken, "\n".join(lines))
        # 親の改版を外して子だけ通すと、子は承認済みチケット（旧計画）で検証される。絞らなければ
        # 改版後の計画で落ちるものが通ることになるので、親も並べるまで何も承認しない。
        skipped = {t.ticket for t in revisions if t.ticket not in wanted}
        blocked = [t for t in pending if t.ticket in wanted and t.is_child and t.parent in skipped]
        if blocked:
            lines = [
                f"{t.ticket}: 親 {t.parent} の改版が承認待ちなのに承認の対象に無い" for t in blocked
            ]
            lines.append("何も承認しない。親の改版も承認の対象に入れてください")
            for line in lines:
                stderr.write(f"ccnavi: {line}\n")
            return Gathered([], [], texts, {}, False, broken, "\n".join(lines))
        waiting_count = len(pending) + len(revisions)
        pending = [t for t in pending if t.ticket in wanted]
        revisions = [t for t in revisions if t.ticket in wanted]
        note = f"承認待ち {waiting_count} 件のうち、指定の {len(wanted)} 件だけを承認の対象にする。"

    if not pending and not revisions:
        return Gathered([], [], texts, {}, True, broken, "", note)

    batch, rejected, pool = candidates(root, conf, pending, revisions, approved)
    for t, complaints in rejected:
        stderr.write(f"ccnavi: {t.ticket} は承認の対象にしない\n")
        for p in complaints:
            stderr.write(f"  {p}\n")
    return Gathered(batch, rejected, texts, pool, False, broken, "", note)


def preview_body(root: str, gathered: Gathered, digest: str) -> dict:
    """`--preview --json` が返す本体。`--verify --json` も同じものに答えを足して返す。"""
    return {
        "version": APPROVE_VERSION,
        "root": root,
        "generated_at": now(),
        "batch": [_batch_entry(c) for c in gathered.batch],
        "text": gathered.text,
        "digest": digest,
        "rejected": [
            {"ticket": t.ticket, "problems": [str(p) for p in complaints]}
            for t, complaints in gathered.rejected
        ],
        "problems": gathered.problems,
    }


# `--verify` が返す理由。JSON の `verify.reason` に出る。
VERIFY_OK = "ok"
VERIFY_REFUSED = "refused"
VERIFY_NOTHING = "nothing-pending"
VERIFY_REJECTED = "rejected"


@dataclass
class Verdict:
    """`--verify` の答え。通るかどうかと、その理由の名前と、端末に出す本文。"""

    ok: bool
    reason: str
    text: str


def verify_verdict(gathered: Gathered, tickets_rel: str) -> Verdict:
    """`--verify` の答えを組む。判定は `gather` が済ませてあり、ここは読み替えるだけ。"""
    head = "承認の可否（確かめるだけ。承認済みチケットは置かない）\n"
    # 読めなかったものは、落ちた枝でも必ず出す。むしろこの 2 つ（絞りが通らない・承認待ちが
    # 1 件も無い）が「読めないのは自分が書いた 1 本」である見込みのいちばん高い枝で、
    # そこで出さないと、置いたばかりの人に「todo/ に置け」とだけ言うことになる。
    unreadable = _unreadable(gathered)
    if gathered.refused:
        return Verdict(False, VERIFY_REFUSED, head + "\n" + gathered.refused + "\n" + unreadable)
    if gathered.nothing_pending:
        text = (
            f"\n承認待ちのチケットは無い。提案は {tickets_rel}/todo/ に置いてください"
            "（承認済みの識別子と同じ名前で置いても承認待ちにはならない）。\n"
        )
        return Verdict(False, VERIFY_NOTHING, head + text + unreadable)

    lines = [head]
    if gathered.note:
        lines.append("\n" + gathered.note + "\n")
    names = [c.ticket.ticket for c in gathered.batch] + [t.ticket for t, _ in gathered.rejected]
    width = max((len(name) for name in names), default=0)
    rows: list[tuple[str, str, list[str]]] = []
    # 苦情の綴りから識別子を落とす（`Problem.__str__` は名指しのために持つが、行の頭に
    # 同じものが出ている）。残すのは重さと中身。
    for cand in gathered.batch:
        notes = [f"{p.severity}: {p.detail}" for p in cand.complaints]
        notes += [f"承認しても書けない: {p.detail}" for p in cand.overflow]
        rows.append((cand.ticket.ticket, "通る", notes))
    for t, complaints in gathered.rejected:
        rows.append((t.ticket, "落ちる", [f"{p.severity}: {p.detail}" for p in complaints]))
    lines.append("\n")
    for name, mark, notes in sorted(rows):
        lines.append(f"  {name.ljust(width)}  {mark}\n")
        lines += [_note_line(note) for note in notes]

    lines.append(unreadable)

    if gathered.rejected:
        reason = VERIFY_REJECTED
        tail = (
            f"\n{len(gathered.rejected)} 件が承認の対象にならない。"
            "提案を直してから、利用者に承認を依頼してください。\n"
        )
    else:
        reason = VERIFY_OK
        tail = f"\n{len(gathered.batch)} 件が承認の対象に入る。利用者に承認を依頼してよい。\n"
    lines.append(tail)
    return Verdict(reason == VERIFY_OK, reason, "".join(lines))


def _unreadable(gathered: Gathered) -> str:
    """読めなかったものを名指しする段。落ちた枝でも通った枝でも同じものを出す。

    終了コードは動かさない。`--approve` も、承認待ちが 1 件も無いとき以外はこれで
    止まらないので、ここで落とすと「確かめは『いいえ』なのに承認は通る」になる。走査は絞る前の
    全ツリーを見るから、他のセッションの書きかけ 1 本で自分の提案が止まることにもなる。
    出さずに済ませることもしない。自分が書いた 1 本かもしれないので、件数と綴りを本文に出す。
    """
    if not gathered.problems:
        return ""
    lines = [
        f"\n読めなかったファイルが {len(gathered.problems)} 件ある"
        "（提案か承認済みチケット。提案なら承認待ちに並ばない）。\n"
    ]
    lines += [_note_line(problem) for problem in gathered.problems]
    lines.append("        いま書いた提案が混じっていないか確かめてください。\n")
    return "".join(lines)


def _note_line(note: str) -> str:
    """行の下に添える 1 件。改行を含む苦情（ルールの `message` は複数行を書ける）は、
    2 行目からも同じだけ下げる。下げないと、次の行が新しい段落に見える。"""
    head, *rest = note.splitlines() or [""]
    return "".join([f"      - {head}\n"] + [f"        {line}\n" for line in rest])


def approval_digest(text: str, batch: list[Candidate], read: dict[str, str] | None = None) -> str:
    """承認の指紋。承認画面の本文・判定が読んだ中身（`read_set`）・承認済みチケットに写る中身の
    SHA-256 の 16 進（小文字）。

    ボードは preview の指紋を `--yes` に `--digest` で返す。識別子だけを比べると、
    見せたあとに提案の範囲や計画が書き換わっても、同じ識別子なら承認が通る。
    本文だけを比べても、画面に出ないのに承認済みチケットへ写る欄（`issue`、Markdown の
    本文、知らない frontmatter の欄）は見せたあとに書き換えられる。

    **判定が読んだ中身（ADR-0093 の 6.2 の `read_set`。段階 2c）を指紋に含める。** 提案だけでなく、
    判定が読んだ承認済みチケット・マーカー・フェーズの種類・統合先の控えのどれかが見せたあとに
    変われば、指紋が変わる（読んだ先が増えた・減ったも同じ）。全ブランチの先頭（`head_sha`）は
    入れない（無関係なコミットで承認が通らなくならないように）。`read` は `read_set` の返す形
    （`<ブランチ>:<相対パス>` → 中身の指紋）。

    束のチケットの写る中身（`_carried`）も残して指紋に含める。読んだ中身から決まるものだが、読みの
    記録（`fsio.reading`）を通らない読みが紛れても、写る中身の変化は取りこぼさないように。

    部分をそのままつながず、部分ごとの指紋を件数と一緒に並べて、その並びの指紋を取る。
    区切りの文字でつなぐと、その文字が部分の中に出たときにつなぎ目をずらせる。Markdown の
    本文は生の制御文字（`\\x00` も）をそのまま通すので、どの文字も「中身に出ない」とは言えない。
    読んだ中身の部分は `<鍵>\\n<中身の指紋>`（指紋は 16 進の固定長なので、最後の改行で切れる）。
    """
    parts = [text] + [_carried(cand) for cand in batch]
    if read is not None:
        parts += [f"{key}\n{digest}" for key, digest in sorted(read.items())]
    lines = [str(len(parts))] + [hashlib.sha256(p.encode("utf-8")).hexdigest() for p in parts]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def read_set(conf: settings.Settings, root: str, seen: dict[str, str]) -> dict[str, str]:
    """判定が読んだ中身（`fsio.reading` の控え）を、機械に依らない鍵に直す（ADR-0093 の 6.2）。

    鍵は `<リポジトリ>:<ブランチ>:<ツリーからの相対パス>`（"/" 区切り）。リポジトリは
    ワークスペース自身なら `self`、プロジェクトならその名前（控えの名前と同じ。ブランチ名が
    リポジトリをまたいで重なっても鍵は同じにならない。git の ref に `:` は使えない）。
    ブランチはそのツリーの HEAD が指すブランチ名（読めなければツリーの名前。ワークスペースルートの
    名前は空）で、`Changes.per_branch` と同じ決め方。ツリーは承認済みチケットを持ちうるもの全部
    （`trees`）で、最長一致。鍵に使ったツリーの HEAD の中身も `<リポジトリ>:<ブランチ>:(HEAD)` で
    入れる（切り離した HEAD の sha が変わればツリーの名前の鍵は同じでも指紋が変わる）。
    控えの置き場の下は、取り込みの控え（`sync/`）だけを `(控え):<相対パス>` で
    入れ、ほかの控え（セッションごとの一時の状態）は入れない（判定の入力ではなく、読むたびに
    変わりうる）。ワークスペースの外は絶対パスのまま。
    """
    # 読みの控えの綴りはリンクをたどった先（`fsio.note_read`）なので、比べる側も
    # たどった先に揃えてから大文字小文字をそろえる（macOS の /tmp のようなリンクを経たルート、
    # Windows の綴りの揺れ）。
    held = sorted(
        ((_real(t.root), t) for t in trees(conf, root)), key=lambda x: len(x[0]), reverse=True
    )
    folded_roots = [(_folded(real), real, t) for real, t in held]
    state_real = _real(conf.state) if conf.state else ""
    state = _folded(state_real) if state_real else ""
    names: dict[str, str] = {}
    out: dict[str, str] = {}
    for path, digest in seen.items():
        folded = _folded(path)
        if state and (folded == state or folded.startswith(state + os.sep)):
            rel = os.path.relpath(path, state_real).replace(os.sep, "/")
            if rel.split("/", 1)[0] == syncstate.SYNC_DIR:
                out[f"(控え):{rel}"] = digest
            continue
        owner = next(
            (
                (real, t)
                for key, real, t in folded_roots
                if folded == key or folded.startswith(key + os.sep)
            ),
            None,
        )
        if owner is None:
            out[f"(外):{fsio.slashed(path)}"] = digest
            continue
        real, t = owner
        if real not in names:
            prefix = f"{syncstate.repo_key(t.project)}:{tree.branch_of(t.root) or t.name}"
            names[real] = prefix
            head = tree.head_text(t.root)
            out[f"{prefix}:(HEAD)"] = fsio.content_digest(head) if head is not None else "-"
        rel = os.path.relpath(path, real).replace(os.sep, "/")
        out[f"{names[real]}:{rel}"] = digest
    return out


# 判定の置き場と働きを決める設定（`settings.load` と Claude Code の設定）の読み先。
_SETTINGS_FILES = (
    "pyproject.toml",
    settings.LOCAL_FILE,
    os.path.join(".claude", "settings.json"),
    settings.LOCAL_CLAUDE_SETTINGS,
)
# 承認の判定に影響する設定の値（環境変数からも来るので、ファイルの中身とは別に値そのものを入れる）。
# 置き場の綴りとチケット制御だけ。控えの置き場（`state`）は入れない（取り込みの控えは中身で
# `(控え):` に入る。置き場の綴りだけが違う起動で見せ直しにならないように）。モードと承認の守りは
# 承認の答えを変えない（端末を求めるかどうか）ので入れない。
_SETTINGS_VALUES = ("tickets", "approved", "projects", "project_home")


def settings_read_set(conf: settings.Settings, root: str) -> dict[str, str]:
    """判定が読んだ設定（ADR-0093 の 6.2 の read_set に足す）。

    `settings.load` が読むファイル（`pyproject.toml`・`ccnavi.settings.local.json`）と
    Claude Code の設定（`.claude/settings.json`・`.claude/settings.local.json`）の中身を
    `(設定):<相対パス>` で、判定に影響する値（置き場の綴り・チケット制御など。
    環境変数から来るもの）を `(設定値):<名前>` で入れる。
    見せたあとに置き場の綴りや設定が変われば、同じ画面でも指紋が変わる。
    """
    out: dict[str, str] = {}
    for rel in _SETTINGS_FILES:
        try:
            with open(os.path.join(root, rel), "rb") as f:
                out[f"(設定):{rel.replace(os.sep, '/')}"] = fsio.content_digest(f.read())
        except OSError:
            out[f"(設定):{rel.replace(os.sep, '/')}"] = fsio.READ_ABSENT
    for name in _SETTINGS_VALUES:
        value = str(getattr(conf, name, "") or "")
        if value and os.path.isabs(value):
            inside = os.path.relpath(value, root)
            if not inside.startswith(".."):
                value = inside.replace(os.sep, "/")
        out[f"(設定値):{name}"] = fsio.content_digest(value)
    # チケット制御は綴り（空・enable）ではなく有効かどうかで入れる。
    out["(設定値):ticket_control"] = fsio.content_digest(str(conf.tickets_enabled))
    return out


def _real(path: str) -> str:
    try:
        return os.path.realpath(path)
    except OSError:
        return os.path.abspath(path)


def _folded(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def _carried(cand: Candidate) -> str:
    """承認済みチケットに写る中身。承認の記録の欄を足す前の姿で書き出す。

    改版は承認済みチケットの frontmatter の計画だけを差し替え、本文は承認済みチケットの
    ものを残す（`revise_copy`）。新規は提案をそのまま写す（`write_copy`）。
    """
    t = cand.ticket
    if cand.is_revision and cand.current is not None:
        front, body = revised_front(cand.current, t, cand.types), cand.current.body
    else:
        front, body = dict(t.raw), t.body
        front.pop(ticket_mod.WORKFLOW_KEY, None)
        if t.has_plan and not t.is_child:
            front[ticket_mod.WORKFLOW_KEY] = workflow.compute(t, cand.types).as_raw()
    front.pop(ticket_mod.APPROVAL_KEY, None)
    return ticket_mod.render(replace(t, raw=front, body=body))


def _batch_entry(cand: Candidate) -> dict:
    t = cand.ticket
    return {
        "ticket": t.ticket,
        "title": t.title,
        "parent": t.parent or None,
        "phase": t.phase if t.is_child else None,
        "revision": cand.is_revision,
        "tree": t.tree or "",
        "path": t.path,
        "overflow": [p.detail for p in cand.overflow],
    }


def approved_text(tickets: list[ticket_mod.Ticket], revisions: set[str], root: str) -> str:
    from . import reasons

    return reasons.approved(tickets, revisions, root)


# ---- 承認の事実を hook がモデルへ伝える


def _news_path(state_dir: str, session: str, agent_id: str) -> str:
    """このセッション（サブエージェントならその起動）が知っている承認済みチケットの控え。
    `once-<session>-<agent>.json`（ctxfile）と同じ並びに置く。"""
    session_part = fsio.safe_name(session) or "unknown"
    agent_part = fsio.safe_name(agent_id) or "main"
    return os.path.join(state_dir, f"approved-{session_part}-{agent_part}.json")


def _known(path: str) -> dict[str, str] | None:
    """控えにある「識別子 → 版」。控えが無ければ None。

    読めるのに壊れているときは空の辞書を返す。「無い」と同じに扱うと、まだ伝えて
    いない承認ごと現状を起点にして何も伝えないことになる。何も知らないことにして、
    伝える側を採る。
    """
    data, failed = fsio.read_json(path)
    if failed is not None:
        return None if isinstance(failed, FileNotFoundError) else {}
    known = data.get("known") if isinstance(data, dict) else None
    if isinstance(known, dict):
        return {k: str(v) for k, v in known.items() if isinstance(k, str)}
    return {}


def _write_known(stderr: TextIO, path: str, known: dict[str, str]) -> None:
    failed = fsio.write_json_atomic(path, {"known": dict(sorted(known.items()))})
    if failed:
        stderr.write(f"ccnavi: 承認を伝えた控えを書けない: {failed}\n")


def _mark(t: ticket_mod.Ticket) -> str:
    """承認済みチケット 1 枚の版。承認した時刻と、改版した時刻。

    改版（`revise_copy`）は承認済みチケットを書き換えるだけで識別子を増やさないので、識別子だけを
    比べても新しい合意だと分からない。版まで見る。
    """
    meta = t.raw.get(ticket_mod.APPROVAL_KEY)
    meta = meta if isinstance(meta, dict) else {}
    return f"{meta.get('approved_at') or ''}/{meta.get('revised_at') or ''}"


def _copy_marks(conf: settings.Settings, root: str) -> dict[str, ticket_mod.Ticket]:
    """いまある承認済みチケット。開いたものと閉じたもの。

    閉じたものも見る。承認の直後・次の hook の前に子が閉じることがあり、開いたものだけを
    見ると、その承認は誰にも伝わらないまま控えに入る。
    """
    found: dict[str, ticket_mod.Ticket] = {}
    for closed in (False, True):
        got, _ = scan(conf, root, closed=closed)
        for t in got:
            found[t.ticket] = t
    review, _ = scan_review(conf, root)
    for t in review:
        found[t.ticket] = t
    return found


def _fresh(known: dict[str, str], current: dict[str, ticket_mod.Ticket]) -> list[ticket_mod.Ticket]:
    """まだ伝えていない承認済みチケット。版が変わったもの（改版）も含む。"""
    out = []
    for ident, t in sorted(current.items()):
        recorded = known.get(ident)
        if recorded is None or recorded != _mark(t):
            out.append(t)
    return out


def baseline(
    stderr: TextIO, conf: settings.Settings, root: str, session: str, agent_id: str
) -> None:
    """控えが無ければ、いまの承認済みチケットを「知っているもの」として書く。文は出さない。

    SessionStart から呼ぶ。起動・再開・compact のどれでも来るが、控えがあれば
    触らない。compact の前に置かれた承認は、compact のあとにも 1 度は伝える。
    """
    if not conf.state or not conf.tickets_enabled:
        return
    path = _news_path(conf.state, session, agent_id)
    if _known(path) is None:
        _write_known(stderr, path, {i: _mark(t) for i, t in _copy_marks(conf, root).items()})


def news(stderr: TextIO, conf: settings.Settings, root: str, session: str, agent_id: str) -> str:
    """このセッションがまだ知らない承認済みチケットがあれば、その承認を伝える文。1 度だけ。

    最初の hook で控えが無ければ、いまの承認済みチケットを起点として書き、何も伝えない。
    それより後に置かれた承認済みチケットと、版の変わった承認済みチケット（親の改版）が「新しい承認」になる。
    控えを置けない（`--state ""`）ときは何も伝えない。診断の試し打ちで記録を汚さない側を採る。
    サブエージェントは自分の控えを持つので、起動より前の承認は伝えない。

    読み・判定・書きは直列化していない。同じセッションの hook が同時に走ると、同じ承認を
    2 度伝えることがある。伝えすぎる側なので受け入れる。取るべきでないのは逆で、
    競合のために伝えない形にはしない。
    """
    if not conf.state or not conf.tickets_enabled:
        return ""
    path = _news_path(conf.state, session, agent_id)
    known = _known(path)
    current = _copy_marks(conf, root)
    marks = {i: _mark(t) for i, t in current.items()}
    if known is None:
        _write_known(stderr, path, marks)
        return ""
    fresh = _fresh(known, current)
    if not fresh:
        return ""
    # 消えた承認済みチケットの分も残す。1 回読めなかっただけで「知らない」に戻すと、
    # 次の回に同じ承認をもう一度伝えることになる。
    _write_known(stderr, path, {**known, **marks})
    return approved_text(fresh, {t.ticket for t in fresh if t.ticket in known}, root)


def candidates(
    root: str,
    conf: settings.Settings,
    pending: list[ticket_mod.Ticket],
    revisions: list[ticket_mod.Ticket],
    approved: list[ticket_mod.Ticket],
) -> tuple[list[Candidate], list[tuple[ticket_mod.Ticket, list[rules.Problem]]], dict]:
    """承認の対象に入れるものと、落とすものに分ける。3 つめは親子を引くための池。

    承認（`approve`）・見せる（`preview`）・確かめる（`verify`）に加えて、`--lint` も
    ここを通る。承認で落ちるものを数える経路が 2 本あると、片方が気づかないうちに弱くなる
    （実際に `--lint` は `validate` だけを当てていて、順序で落ちる子に何も言わなかった）。
    """
    from . import phase

    open_index = by_id(approved)
    # 親子を引く池は、承認済みチケットと、今回の承認で通ったものだけ。落ちた親を池に残すと、
    # 承認されない親の範囲で子が検証され、親の承認を経ずに子の承認済みチケットができる。
    # pending は親が子より前に並ぶ（並べ替えの鍵が親の識別子）ので、子が引くときには
    # 親の通過が決まっている。
    pool = by_id(approved)
    batch: list[Candidate] = []
    rejected: list[tuple[ticket_mod.Ticket, list[rules.Problem]]] = []
    # 層ごとの読み込みは 1 プロジェクト 1 回。承認の対象に同じ層のチケットが
    # 何件あっても、ファイルを読むのはその層につき 1 度で足りる。
    cache: dict[str, dict | None] = {}
    # 先行を引く池。先行を書いた子が居るときだけ、最初の 1 回で組む。
    preds: dict[str, list[ticket_mod.Ticket]] | None = None
    # 家族の立ち位置と統合先の控え（ADR-0093 の 3.3）。1 回の承認で 1 度ずつだけ読む。
    fams = syncstate.Families(conf, root)

    def types_for(t: ticket_mod.Ticket) -> dict | None:
        name = project_of(t, pool)
        if name not in cache:
            cache[name] = phase.load_types(conf, root, name)
        return cache[name]

    for t in sorted(revisions, key=lambda x: x.ticket):
        current = open_index[t.ticket]
        types = types_for(t)
        complaints = _workflow_field(t) + revision_problems(root, conf, t, current, types)
        complaints += family_problems(conf, root, t, fams)
        if any(p.severity == rules.SEVERITY_ERROR for p in complaints):
            rejected.append((t, complaints))
            continue
        cand = Candidate(ticket=t, complaints=complaints, current=current, types=types)
        if cand.plans_feedback:
            cand.notes = feedback_notes(root, conf, t)
        batch.append(cand)
        # 通った改版だけ、一緒に承認する子から見える親にする。落ちた改版の計画で子を
        # 通すと、承認されない番号の子が承認済みチケットになる。
        pool[t.ticket] = t

    # 今回の承認で通った子を親ごとに。後に続く子の順序の検査が、そのフェーズを
    # 開き直したものとして読む。
    # 子はフェーズの番号の順に並べる。識別子の順だと、後のフェーズの子（-02）が前のフェーズに
    # 足す子（-03）より先に検査され、開き直す前のマーカーで通ってしまう。
    added: dict[str, list[ticket_mod.Ticket]] = {}
    for t in sorted(
        pending, key=lambda x: (x.parent or x.ticket, x.is_child, x.phase or 0, x.ticket)
    ):
        types = types_for(t)
        complaints, overflow = validate(t, pool, types)
        complaints += _workflow_field(t)
        complaints += project_problems(t, pool, conf)
        complaints += family_problems(conf, root, t, fams)
        complaints += integration_problems(conf, root, t, fams)
        if t.is_child and not any(p.severity == rules.SEVERITY_ERROR for p in complaints):
            parent = pool.get(t.parent)
            if parent is not None:
                complaints += phase.order_problems(
                    root, conf, t, parent, types, added.get(t.parent)
                )
        if t.is_child and t.predecessors:
            # 先行は `done/` に在って取り消しでないことを求める（ADR-0088）。同じ承認で通る
            # 先行も、まだ `todo/` に在るので満たさない。
            if preds is None:
                preds = predecessor_pool(conf, root)
            complaints += predecessor_problems(t, preds, conf.approved)
        if any(p.severity == rules.SEVERITY_ERROR for p in complaints):
            rejected.append((t, complaints))
            continue
        batch.append(Candidate(ticket=t, complaints=complaints, overflow=overflow, types=types))
        pool[t.ticket] = t
        if t.is_child:
            added.setdefault(t.parent, []).append(t)
    return batch, rejected, pool


def _workflow_field(t: ticket_mod.Ticket) -> list[rules.Problem]:
    """提案に待ち方の写し（`workflow:`）が書いてあれば拒む。写しを書くのは `--approve` だけ。"""
    if ticket_mod.WORKFLOW_KEY not in t.raw:
        return []
    return [
        rules.Problem(
            rules.SEVERITY_ERROR,
            t.ticket,
            f"`{ticket_mod.WORKFLOW_KEY}` は --approve が書く欄。提案には書かない",
        )
    ]


def project_of(t: ticket_mod.Ticket, pool: dict[str, ticket_mod.Ticket]) -> str:
    """このチケットの層を決める `project:`（設計 11.4.1）。

    子は親と同じ置き場に並ぶので、種類を引くには親のプロジェクトを使う。食い違えば
    `project_problems` が落とす。親が池に居ないときだけ、子の置き場の値をそのまま読む。
    """
    if t.is_child:
        parent = pool.get(t.parent)
        if parent is not None:
            return parent.project
    return t.project


@dataclass
class Applied:
    """承認済みチケットを置いた結果。途中で止まったときに、どこまで置いたかを呼び手へ返す。

    置いたものは戻さない（戻す途中でまた落ちる）。代わりに、どこで止まって何が置かれたかを
    そのまま返し、拡張が人に伝える（README「承認の JSON」の `partial`）。
    """

    code: int
    placed: list[str]
    stopped_at: str = ""
    reason: str = ""


def _reopened_line(parent: str, phase: int):
    def announce(kinds: list[str]) -> list[str]:
        return [
            f"  {parent} のフェーズ {phase} のマーカー（{', '.join(kinds)}）を消した。"
            "全部閉じたらレビューをもう一度頼むことになる"
        ]

    return announce


@dataclass
class Planned:
    """承認の書き込みを並べたもの。`stopped` は途中で止まった（識別子, 理由）。止まらなければ None。

    止まったときも、そこまでに並べた分は書く（前と同じく、置いたものは戻さない）。
    """

    stage: fsio.Stage
    stopped: tuple[str, str] | None


def plan_batch(root: str, conf: settings.Settings, batch: list[Candidate], stamp: str) -> Planned:
    """承認の書き込みを、ディスクに書かずに並べる。時刻は `stamp` に固定する。

    書き込みを並べる（ここ、ADR-0093 の 6.2 の plan）と、並べたものを書く
    （`core.write_fs`、Writer(FS)）の 2 段。Chrome は同じ plan の結果を 1 コミットにする。
    承認の対象の順に、改版は承認済みチケットを書き換え、新規は承認済みチケットを置く。
    """
    with fsio.staging() as stage, fsio.clock(stamp):
        stopped = _apply_steps(stage, root, conf, batch, stamp)
    return Planned(stage, stopped)


def _apply_steps(
    stage: fsio.Stage,
    root: str,
    conf: settings.Settings,
    batch: list[Candidate],
    stamp: str,
) -> tuple[str, str] | None:
    """`plan_batch` の中身。書き込みは fsio の控える段に積み、見せる行も同じ並びに積む。

    書けなかったときの扱い（止める・言って続ける・行を出す）は `fsio.policy` で添える。
    控える段では書き込みが落ちないので、その扱いは Writer(FS) が書くときに当てる。
    """
    from . import phase

    for cand in batch:
        t = cand.ticket
        with fsio.policy(
            on_fail=fsio.FAIL_STOP,
            ticket=t.ticket,
            message="{reason}",
            prefix="",
            undo=(),
            places="",
            group=0,
        ):
            if cand.is_revision and cand.current is not None:
                where = home_dir(conf, root, t.ticket, t.parent, t.tree_root, project=t.project)
                with fsio.policy(places=t.ticket):
                    failed = revise_copy(
                        where, cand.current, t, stamp, cand.plans_feedback, cand.types
                    )
                if failed:
                    return t.ticket, failed
                removing = "改版の提案を todo/ から消せない ({reason})"
                with fsio.policy(on_fail=fsio.FAIL_WARN, message=removing):
                    failed = fsio.unlink(t.path)
                if failed:
                    stage.line(
                        f"ccnavi: {t.ticket}: {removing.replace('{reason}', failed)}",
                        stream=fsio.STREAM_ERR,
                    )
                what = "フィードバック計画" if cand.plans_feedback else "全体計画"
                stage.line(f"  {t.ticket} の{what}を改版した")
                if cand.plans_feedback:
                    # レビューの結果を見たうえでの計画なので、最後のレビューはここで済む。
                    with fsio.policy(prefix="マーカーを置けない: "):
                        failed = phase.settle_last_review(where, t, stamp)
                    if failed:
                        return t.ticket, f"マーカーを置けない: {failed}"
                    # 全体計画の子でレビュー待ちに残っているものは、見たうえでの計画なので閉じる。
                    moved, failed = settle_review(
                        conf, root, t.ticket, list(range(1, len(t.plan) + 1))
                    )
                    if failed:
                        return t.ticket, failed
                    if moved:
                        stage.line(f"  レビュー待ちの子を閉じた: {', '.join(moved)}")
                    stage.line(
                        "  全体計画の最後のレビューを済んだ扱いにした。残った指摘は"
                        "フィードバック作業フェーズの confirm が数える"
                    )
                continue
            where = home_dir(conf, root, t.ticket, t.parent, t.tree_root, project=t.project)
            wf = workflow.compute(t, cand.types) if t.has_plan and not t.is_child else None
            failed = admit(where, t, source_branch(t), stamp, wf)
            if failed:
                return t.ticket, failed
            if t.is_child:
                group = stage.new_group()
                with fsio.policy(on_fail=fsio.FAIL_LINE, group=group):
                    carried = carry_flow(conf, root, t, where)
                for line in carried:
                    stage.line(f"  {line}", group=group)
            # 終わったフェーズに子を足したら、そのフェーズのマーカーは消す。マーカーは
            # 「その時点の子が全部見られた」以上の意味を持たない（REQ-TKT-21）。
            # 消すのは置けたあと。先に消すと、書けずに終わった（置き場が塞がっている、権限が無い）
            # ときに、子は 1 枚も増えていないのに済んでいたレビューが巻き戻る
            # （test_a_failed_copy_does_not_clear_the_marks_of_a_reviewed_phase）。
            # Writer(FS) は並べた順に書き、止まったらその先を書かないので、この順が保たれる。
            # 置いた直後に落ちる（打ち切られる・電源が切れる）と「子は増えたのにマーカーは残る」
            # ＝見られていない子がいるのに止まらなくなるが、そちらは起きうる間が
            # ファイル 1 つを書く間だけで、頻度がはるかに低い。順番の入れ替えでは直らない
            # （両方を防ぐなら、マーカーの時刻と子の承認時刻を比べて止めるかどうかを決める
            # 作りが要る）。
            # マーカーを消せたかは書くときに分かるので、行は Writer(FS) が消せた種類で出す。
            # reviewed を消せなければそこで止める（`clear_marks`）。
            if t.is_child and t.phase is not None:
                clear_marks(
                    home_dir(conf, root, t.parent, "", project=t.project),
                    t.parent,
                    t.phase,
                    announce=_reopened_line(t.parent, t.phase),
                )

    stage.line(f"\n承認した。{conf.approved}/{DOING_DIR}/ へ動かした。")
    return None


def _origin_line(t: ticket_mod.Ticket) -> str:
    """どのプロジェクトの、どのツリーの、どの提案か（REQ-MLT-11）。

    プロジェクトは提案を置いた場所で決まる。人はここで、書き込みが向かうリポジトリを
    見て承認する。提案はそのツリーからの相対パスで見せる（ADR-0093 の D22）。絶対パスは
    機械ごとに違い、承認の指紋（画面の本文を含む）が Chrome と手元で揃わない。
    """
    return (
        f"■ プロジェクト: {t.project or 'ワークスペース'}"
        f"  ワークツリー: {t.tree or 'ワークスペースルート'}  提案: {source_path(t)}"
    )


def screen(
    batch: list[Candidate],
    pool: dict[str, ticket_mod.Ticket],
) -> str:
    """承認を求める画面を組む。

    frontmatter の全文は見せない。人に見せるのは「何が新たに書けるようになるか」
    「子が編集可能な範囲（親をどこまで絞ったか）」「人間レビューの要否」「リスク」「計画」。
    新たに書けるようになる領域を最初に置く（REQ-APV-01）。

    種類は候補が持っているものを使う。承認の対象の中でもチケットごとに層が違いうるので、
    画面の側で 1 つに決めない。
    """
    lines = [f"チケットの承認リクエスト: {len(batch)} 件"]
    for cand in batch:
        t = cand.ticket
        cand_types = cand.types
        if cand.is_revision and cand.current is not None:
            lines += ["", f"== {t.ticket}: {t.title}  親の改版"]
            lines += _plan_diff_lines(cand.current, t, cand_types)
            for note in cand.notes:
                lines.append(f"    {note}")
            lines.append(_origin_line(t))
            continue
        lines += [
            "",
            f"== {t.ticket}: {t.title}"
            + (
                f"  親 {t.parent} / フェーズ {_phase_label(t, pool, cand_types)}"
                if t.is_child
                else "  親チケット"
            ),
        ]
        if t.is_child:
            # 親は一緒に承認の対象に入っていることが普通。承認済みチケットだけを引くと
            # 「承認済みチケットが無い」になる。
            parent = pool.get(t.parent)
            lines.append("■ この子チケットで編集可能な範囲")
            lines.append(
                "    子の範囲は親の範囲の中に収まる。下に並ぶのは親から絞った結果で、"
                "親の範囲に無い場所がここで新しく編集できるようになることはない"
            )
            head = "親の範囲: " + (
                ", ".join(parent.paths(rules.ALLOW) + parent.paths(rules.ASK))
                if parent
                else "親がまだ承認されていない"
            )
            lines.append(f"    {head}")
            bound = _type_of(t, pool, cand_types)
            if bound is not None and not bound.inherits_scope:
                lines.append(f"    種類「{bound.title}」の範囲: " + ", ".join(bound.scope_globs))
        else:
            lines.append("■ このチケットで編集可能な範囲")
            lines.append(
                "    下に並ぶ場所にだけ、このチケットで編集できるようになる。"
                "allow は無確認で編集できる場所、ask は確認を挟んで編集できる場所、"
                "deny はこのチケットでも編集できない場所"
            )
            # チケットの範囲はルールの allow より強い（設計 7）。承認する人は「ルールで
            # 開けてあるから範囲の外でも書ける」と読み違えやすいので、承認の前に言う。
            lines.append(
                "    ルールの allow で許可してある場所も、この範囲の外では止まる。"
                "ルールの deny はこの範囲の中でも止まる"
            )
        for name in rules.SECTIONS:
            paths = t.paths(name)
            if paths:
                lines.append(f"    {name}: " + ", ".join(paths))
        if cand.overflow:
            # 範囲のすぐ下に置く。承認は止めないが、判定では止まる。判定に影響しない記述の
            # 注意と混ぜると、承認すれば書けると読み違える。
            # **「編集対象」と「書き込めない」は意図して分けてある。** 前半はチケットが宣言した側、
            # 後半は実際の書き込みが止まる側の話で、どちらか一方の語に揃えると、宣言と実行の
            # どちらを指しているのかが読めなくなる。ほかの見出しが「編集」で揃っているのを見て、
            # ここも揃えたくなるが、揃えない。
            lines.append("■ チケットで編集対象としているが、書き込めない場所")
            lines.append(
                "    親の範囲かフェーズの種類の上限を超えている。"
                "承認は可能だが、編集しようとすると判定が止める"
            )
            lines += [f"    {p.detail}" for p in cand.overflow]
        if t.is_child:
            state = "要" if t.review_required else "不要"
            lines.append(f"■ 人間レビュー: {state}")
            if t.review_reason:
                lines.append(f"    理由: {t.review_reason}")
            if t.predecessors:
                lines.append(f"■ 先行: {', '.join(t.predecessors)}")
                lines.append(
                    "    どれも done/ に在って取り消しでないこと。"
                    "満たしていなければ、承認も着手も止まる"
                )
        elif t.has_plan:
            lines.append("■ 全体計画")
            lines.append(
                "    承認すると、この並びで進めることに合意したことになる。"
                "前のフェーズが閉じるまで、次のフェーズの子は承認できない"
            )
            lines += _plan_lines(t.plan, 1, cand_types)
            lines += _workflow_lines(t, workflow.compute(t, cand_types))
            if t.feedback is not None:
                lines.append("■ フィードバック計画")
                lines += _plan_lines(t.feedback, len(t.plan) + 1, cand_types) or ["    対応なし"]
        if not t.is_child and t.issue is not None:
            lines.append(f"■ 課題: {ticket_mod.issue_label(t)}")
            lines.append(
                "    この親のマージリクエストの本文に Closes として書く番号。"
                "マージされると、この課題も閉じる"
            )
        if t.rationale.strip():
            lines.append("■ エージェントが書いた理由")
            lines += [f"    {line}" for line in t.rationale.strip().splitlines()]
        lines.append(_origin_line(t))
        warnings = [p for p in cand.complaints if p.severity == rules.SEVERITY_WARN]
        if warnings:
            lines.append("■ 判定に効かない記述")
            lines.append(
                "    提案に書いてあっても、判定はこれを読まない。"
                "承認しても、編集できる場所は変わらない"
            )
            lines += [f"    {p.detail}" for p in warnings]
    return "\n".join(lines)


def _plan_lines(items: list[ticket_mod.PlanItem], start: int, types: dict | None) -> list[str]:
    lines = []
    for i, item in enumerate(items):
        n = start + i
        pt = (types or {}).get(item.type)
        title = pt.title if pt is not None else item.type
        review = ""
        if item.deferred:
            review = "レビューは次と一緒に"
        elif item.review == ticket_mod.PLAN_REVIEW_MR:
            review = "レビュー要: 計画で強めた"
        elif pt is not None:
            review = {
                phasetypes.REVIEW_MR: "レビュー要: マージリクエスト",
                phasetypes.REVIEW_CHAT: "レビュー要: このセッションで",
            }.get(pt.review, "レビュー不要")
        lines.append(f"    {n}. {title} / {item.type}" + (f"  {review}" if review else ""))
    return lines


def _workflow_lines(t: ticket_mod.Ticket, wf: ticket_mod.Workflow) -> list[str]:
    """`dag` の計画の待ち。辺の書き漏れを人が見つける場所（設計 9.7）。"""
    found = workflow.lines(t, wf)
    if not found:
        return []
    return [
        "■ 待ち方（承認すると親に写し、後から phases.yml を直しても変わらない）",
        *("    " + x for x in found),
    ]


def _plan_diff_lines(
    current: ticket_mod.Ticket, revised: ticket_mod.Ticket, types: dict | None
) -> list[str]:
    lines = []
    if current.plan != revised.plan:
        lines.append("■ 全体計画の変更")
        lines.append("    いま:")
        lines += ["    " + x for x in _plan_lines(current.plan, 1, types)]
        lines.append("    改版:")
        lines += ["    " + x for x in _plan_lines(revised.plan, 1, types)]
    fresh = workflow.compute(revised, types)
    held = current.workflow
    if held is None or held.as_raw() != fresh.as_raw():
        lines.append("■ 待ち方の変更")
        before = workflow.lines(current, held) if held is not None else []
        after = workflow.lines(revised, fresh)
        lines.append("    いま:")
        lines += ["        " + x for x in before] or ["        一直線（前の番号を全部待つ）"]
        lines.append("    改版:")
        lines += ["        " + x for x in after] or ["        一直線（前の番号を全部待つ）"]
    if current.feedback != revised.feedback:
        lines.append("■ フィードバック計画")
        start = len(revised.plan) + 1
        lines += _plan_lines(revised.feedback or [], start, types) or [
            "    対応なし。見たうえで対応しない、という記録になる"
        ]
    return lines


def _phase_label(t: ticket_mod.Ticket, pool: dict, types: dict | None) -> str:
    pt = _type_of(t, pool, types)
    return f"{t.phase}: {pt.title}" if pt is not None else str(t.phase)


def _type_of(t: ticket_mod.Ticket, pool: dict, types: dict | None):
    parent = pool.get(t.parent) if t.is_child else None
    if parent is None or not parent.has_plan or t.phase is None or not types:
        return None
    item = parent.item_at(t.phase)
    return types.get(item.type) if item is not None else None


def waiting(
    proposals: list[ticket_mod.Ticket],
    approved: list[ticket_mod.Ticket],
    closed: list[ticket_mod.Ticket],
    review: list[ticket_mod.Ticket],
    types_for,
) -> tuple[list[ticket_mod.Ticket], list[ticket_mod.Ticket]]:
    """いま `--approve` で承認の対象に入るもの。新規の承認待ちと、親の改版。

    承認待ちは `todo/` に在って、どの置き場（作業中・レビュー待ち・閉じた）にも同じ識別子が
    無いもの。閉じたものは対象外で、再開は人が承認済みチケットを戻す。
    改版は、作業中の親の承認済みチケットがあり、`todo/` の提案の計画がそれと違うもの。
    計画が同じでも、いまの種類で計算した待ち方が承認済みチケットの写しと違えば改版になる
    （`phases.yml` を直した結果を進行中の親に反映する経路。設計 9.7）。`types_for` は
    チケットに使う種類を引く関数（`types_resolver`）。
    `--approve` と `--explain --json` が同じ答えを出すために、ここで 1 度だけ決める。
    統合先の控えの `done/` にある識別子（ADR-0093 の 3.3 の 4）はここでは外さず、`candidates` が
    理由を添えて承認しない側に回す（何も出さずに消すことはしない）。
    """
    known = by_id(approved + closed + review)
    open_index = by_id(approved)
    todo = [t for t in proposals if t.state == ticket_mod.TODO]
    pending = [t for t in todo if t.ticket not in known]
    revisions = [
        t
        for t in todo
        if t.ticket in open_index
        and not t.is_child
        and t.has_plan
        and (
            _plan_differs(t, open_index[t.ticket])
            or _workflow_differs(t, open_index[t.ticket], types_for)
        )
    ]
    return pending, revisions


def _workflow_differs(proposal: ticket_mod.Ticket, current: ticket_mod.Ticket, types_for) -> bool:
    fresh = workflow.compute(proposal, types_for(proposal)).as_raw()
    held = current.workflow.as_raw() if current.workflow is not None else None
    return fresh != held


def types_resolver(conf: settings.Settings, root: str, approved: list[ticket_mod.Ticket]):
    """チケットに使う種類を引く関数。層ごとの読み込みは 1 プロジェクト 1 回。"""
    from . import phase

    pool = by_id(approved)
    cache: dict[str, dict | None] = {}

    def types_for(t: ticket_mod.Ticket) -> dict | None:
        name = project_of(t, pool)
        if name not in cache:
            cache[name] = phase.load_types(conf, root, name)
        return cache[name]

    return types_for


def _plan_differs(proposal: ticket_mod.Ticket, current: ticket_mod.Ticket) -> bool:
    return proposal.plan != current.plan or proposal.feedback != current.feedback


def feedback_notes(root: str, conf: settings.Settings, parent: ticket_mod.Ticket) -> list[str]:
    """フィードバック計画の承認に添える証跡。何を見たうえでの合意かを残す。"""
    accepted = accepted_threads(
        home_dir(conf, root, parent.ticket, "", project=parent.project), parent.ticket
    )
    notes = [f"受け入れ済みの未解決スレッド: {len(accepted)} 件"]
    if not parent.feedback:
        notes.append("対応なし。見たうえで対応しない、という記録になる")
        if accepted:
            notes.append("別に追うものは issue に回したか（ccnavi-review.sh decide）")
    return notes


def plan_problems(t: ticket_mod.Ticket, types: dict | None) -> list[rules.Problem]:
    """親の計画が種類の定義と合っているか（設計 9.7）。"""
    problems: list[rules.Problem] = []
    if not t.has_plan:
        return problems
    if types is None:
        problems.append(
            rules.Problem(
                rules.SEVERITY_ERROR,
                t.ticket,
                "`plan` があるのにフェーズの種類の定義（phases.yml）が読めない",
            )
        )
        return problems
    for key, items, kind in (
        ("plan", t.plan, phasetypes.KIND_WORK),
        ("feedback", t.feedback or [], phasetypes.KIND_FEEDBACK),
    ):
        seen_types = {item.type for item in items}
        for i, item in enumerate(items):
            pt = types.get(item.type)
            if pt is None:
                problems.append(
                    rules.Problem(
                        rules.SEVERITY_ERROR, t.ticket, f"`{key}[{i}]` の種類 `{item.type}` は無い"
                    )
                )
                continue
            if pt.kind != kind:
                where = "全体計画" if key == "plan" else "フィードバック計画"
                problems.append(
                    rules.Problem(
                        rules.SEVERITY_ERROR,
                        t.ticket,
                        f"`{key}[{i}]` の `{item.type}` は kind `{pt.kind}`。{where}には置けない",
                    )
                )
            if item.deferred and pt.review == phasetypes.REVIEW_NONE:
                problems.append(
                    rules.Problem(
                        rules.SEVERITY_ERROR,
                        t.ticket,
                        f"`{key}[{i}]` の `{item.type}` はレビュー不要の種類。延期するものが無い",
                    )
                )
            for need in pt.requires:
                if need not in seen_types:
                    problems.append(
                        rules.Problem(
                            rules.SEVERITY_ERROR,
                            t.ticket,
                            f"`{item.type}` を置くなら `{need}` も {key} に要る（requires）",
                        )
                    )
        # 延期の先は、レビューがある項でなければならない。全体計画は待ち方（`workflow`）が
        # 引き受け手を決めるので、そちらで見る。
        if key == "plan":
            continue
        for i, item in enumerate(items):
            if not item.deferred:
                continue
            target = next((x for x in items[i + 1 :] if not x.deferred), None)
            if target is None:
                continue
            tpt = types.get(target.type)
            if tpt is not None and tpt.review == phasetypes.REVIEW_NONE and target.review != "mr":
                problems.append(
                    rules.Problem(
                        rules.SEVERITY_ERROR,
                        t.ticket,
                        f"`{key}[{i}]` を延期した先の `{target.type}` にレビューが無い",
                    )
                )
    if not any(p.severity == rules.SEVERITY_ERROR for p in problems):
        problems.extend(workflow.problems(t, types))
    return problems


def revision_problems(
    root: str,
    conf: settings.Settings,
    revised: ticket_mod.Ticket,
    current: ticket_mod.Ticket,
    types: dict | None,
) -> list[rules.Problem]:
    """親の改版を受けてよいか（設計 9.7）。"""
    from . import phase

    problems = plan_problems(revised, types)
    if any(p.severity == rules.SEVERITY_ERROR for p in problems):
        return problems
    # 変えられるのは計画だけ。
    if _scope_signature(revised) != _scope_signature(current):
        problems.append(
            rules.Problem(
                rules.SEVERITY_ERROR,
                revised.ticket,
                "改版で変えられるのは plan と feedback だけ。範囲が承認済みチケットと違う",
            )
        )
    if (
        revised.title != current.title
        or revised.issue != current.issue
        or revised.issue_repo != current.issue_repo
    ):
        problems.append(
            rules.Problem(
                rules.SEVERITY_ERROR,
                revised.ticket,
                "改版で変えられるのは plan と feedback だけ。題か課題番号が承認済みチケットと違う",
            )
        )
    # 全体計画: 子がある番号までは同じ並びでなければならない。
    if revised.plan != current.plan:
        frozen = _last_phase_with_children(conf, root, current.ticket)
        if frozen > len(revised.plan) or revised.plan[:frozen] != current.plan[:frozen]:
            problems.append(
                rules.Problem(
                    rules.SEVERITY_ERROR,
                    revised.ticket,
                    f"全体計画の {frozen} 番目までは子が承認されているので変えられない。"
                    "番号がずれると子の phase が指す先が変わる",
                )
            )
    # 延期の引き受け手: 子がある番号までは変えない。引き受け手が前へ動くと、延期した作業を
    # 済んだレビューが引き受けたことになり、誰にも見られずに終わる。
    frozen = _last_phase_with_children(conf, root, current.ticket)
    held = workflow.effective(current, types).review_at
    fresh = workflow.compute(revised, types).review_at
    moved = [
        n
        for n in sorted(set(held) | set(fresh))
        if held.get(n) != fresh.get(n)
        and (
            n <= frozen
            or any(at is not None and at <= frozen for at in (held.get(n), fresh.get(n)))
        )
    ]
    if moved:
        problems.append(
            rules.Problem(
                rules.SEVERITY_ERROR,
                revised.ticket,
                f"延期の引き受け手が変わる（{', '.join(map(str, moved))} 番目）。"
                f"{frozen} 番目までは子が承認されているので、延期の行き先は変えられない",
            )
        )
    # フィードバック計画: 無い状態から 1 回だけ、全体計画の最後のレビューが済んでから。
    if revised.feedback != current.feedback:
        if current.feedback is not None:
            # 残りの切り出し先は運び方で違う。マージリクエストがあれば issue に切り出せるが、
            # chat で回した親はホストに何も無いので、新しい親チケットの提案にする。
            elsewhere = (
                "残りは新しい親チケットの提案として wip/proposals/todo/ に書いてください"
                if phase.chat_only(root, conf, current.ticket)
                else "残りは別 issue に回してください（ccnavi-review.sh decide）"
            )
            problems.append(
                rules.Problem(
                    rules.SEVERITY_ERROR,
                    revised.ticket,
                    "フィードバック計画は 1 回だけ。承認済みのフィードバック作業フェーズに"
                    f"子を足してやり直すか、{elsewhere}",
                )
            )
        elif not phase.plan_finished(root, conf, current):
            problems.append(
                rules.Problem(
                    rules.SEVERITY_ERROR,
                    revised.ticket,
                    "フィードバック計画は、全体計画のフェーズが全部閉じてレビューが済んでから出す。"
                    "作業が終わるまで対応は計画できない",
                )
            )
    return problems


def revise_copy(
    approved_dir: str,
    current: ticket_mod.Ticket,
    revised: ticket_mod.Ticket,
    stamp: str,
    feedback_planned: bool,
    types: dict | None = None,
) -> str:
    """承認済みチケットの計画と待ち方を差し替える。範囲と承認の記録はそのまま。"""
    front = revised_front(current, revised, types)
    meta = dict(front.get(ticket_mod.APPROVAL_KEY) or {})
    meta["revised_at"] = stamp
    if feedback_planned:
        meta["feedback_at"] = stamp
    front[ticket_mod.APPROVAL_KEY] = meta
    current.raw = front
    failed = _write(copy_path(approved_dir, current.ticket), ticket_mod.render(current))
    if not failed:
        history.note(
            approved_dir,
            current.ticket,
            history.KIND_REVISED,
            ticket_mod.DOING,
            ticket_mod.DOING,
            feedback=True if feedback_planned else None,
        )
    return failed


def revised_front(
    current: ticket_mod.Ticket, revised: ticket_mod.Ticket, types: dict | None = None
) -> dict:
    """改版で書く frontmatter。承認済みチケットの frontmatter の計画と待ち方を差し替えた写し。

    `current` は書き換えない。承認の指紋（`digest`）も同じものから組むので、見せた
    中身と書く中身が食い違わない。
    """
    front = dict(current.raw)
    front["plan"] = [item.as_raw() for item in revised.plan]
    if revised.feedback is not None:
        front["feedback"] = [item.as_raw() for item in revised.feedback]
    front[ticket_mod.WORKFLOW_KEY] = workflow.compute(revised, types).as_raw()
    return front


def _scope_signature(t: ticket_mod.Ticket) -> tuple:
    return tuple((e.decision, e.glob, e.regex) for e in t.entries)


def _last_phase_with_children(conf: settings.Settings, root: str, parent_id: str) -> int:
    """この親で、子が承認された（開いていても閉じていても）いちばん後ろの番号。"""
    numbers = [
        t.phase for t in _everything(conf, root) if t.parent == parent_id and t.phase is not None
    ]
    return max(numbers) if numbers else 0


def _reserved_project(t: ticket_mod.Ticket) -> list[rules.Problem]:
    """`project:` が層の名前に予約してある綴りなら error（設計 11.4）。"""
    if not t.project or not settings.is_reserved_layer_name(t.project):
        return []
    reserved = " と ".join(f"`{name}`" for name in settings.RESERVED_LAYER_NAMES)
    return [
        rules.Problem(
            rules.SEVERITY_ERROR,
            t.ticket,
            f"`project: {t.project}` は層の名前として予約してある綴り（{reserved}）。"
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


def validate(
    t: ticket_mod.Ticket, pool: dict[str, ticket_mod.Ticket], types: dict | None = None
) -> tuple[list[rules.Problem], list[rules.Problem]]:
    """承認の対象にしてよいかを見る。親子の制約はここでしか見られない。

    返すのは 2 つの並び。1 つめはチケットの形の苦情で、error があれば承認しない。
    2 つめは範囲の超過（親の範囲・種類の上限を超えた項、regex の項）で、承認は止めない。
    判定が親と種類の上限で切り詰めるので、承認で止める理由が無い。

    形の検査を error に残すのは、**まとめて 1 度で見せて直させるため**。判定の側も同じ
    検査を当てる（`blocking_problems`、ADR-0058）ので「判定では補えない」わけではないが、
    判定に任せると、承認の画面では通って、あとで書き込みが止まってから気づくことになる。
    承認は人がまとめて見て決める場所なので、そこで落ちるものはそこで言う。
    """
    problems: list[rules.Problem] = []
    overflow: list[rules.Problem] = []
    if not t.is_child:
        return plan_problems(t, types), overflow
    parent = pool.get(t.parent)
    problems.extend(child_problems(t, parent))
    if parent is None or parent.is_child:
        return problems, overflow
    overflow.extend(ticket_mod.subset_problems(t, parent))
    if problems:
        # 番号が親の計画に無い。種類を引けないので、ここから先は見ても意味が無い。
        return problems, overflow
    if parent.has_plan and t.phase is not None:
        item = parent.item_at(t.phase)
        pt = (types or {}).get(item.type)
        if pt is None:
            problems.append(
                rules.Problem(
                    rules.SEVERITY_ERROR,
                    t.ticket,
                    f"{t.phase} 番目の種類 `{item.type}` の定義が読めない",
                )
            )
            return problems, overflow
        overflow.extend(phasetypes.scope_problems(t, pt))
    # regex の項は親の検査と種類の検査が同じ文で言う。画面に 2 度並べない。
    seen: set[str] = set()
    distinct = []
    for p in overflow:
        if p.detail not in seen:
            seen.add(p.detail)
            distinct.append(p)
    return problems, distinct


def child_problems(
    t: ticket_mod.Ticket, parent: ticket_mod.Ticket | None, missing: str = ""
) -> list[rules.Problem]:
    """子と親の構造の検査。承認（`validate`）と判定（`blocking_problems`）が同じ答えを引く。

    どれも「子の範囲をどの親で切り詰めるか」が決まらない形なので、承認でも判定でも
    通さない。1 か所に置くのは、置き場を動かして承認する運び（ADR-0058）で判定の側の
    検査だけが古くなると、承認を通ったチケットと通らないチケットで答えが分かれるから。

    「種類の定義が読めない」はここに入れない。壊れているのは設定で、チケットの形は
    正しい。判定は注記を添えて親の範囲で切り詰める（`judge.ticket_verdict`）。
    """
    if parent is None:
        # 文面だけは呼び手が差し替える。承認のときは「まだ承認されていない」しか起きないが、
        # 判定のときは「承認されたが閉じた」も同じ検査に当たる。検査は同じで、読む人の
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
    """判定がこの承認済みチケットを信頼できない理由。空なら信頼してよい（ADR-0058）。

    承認のときにしか当たらなかった検査のうち、当たらないと「範囲をどこで切り詰めるか」が
    決まらないものだけを置く。置き場を動かして承認する運びは `--approve` を通らないので、
    同じ検査を判定の側でも当てる。当たれば範囲は使われず、その場所は止まる。

    ここに入れないもの。
    - 計画の形（`plan_problems`）。範囲に影響しないので `--lint` が言う
    - フェーズの順序（`phase.order_problems`）。順序が狂っていても、その子の範囲を
      どの親で切り詰めるかは決まる。線を引いているのはここで、ADR-0024 ではない
      （あちらは `held_phase` が `Agent` とシェルを止める話で、この検査とは別の仕組み）。
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
    """判定が読む承認済みチケットに、信頼できない理由の印を付ける（ADR-0058）。

    印を読むのは `phase.scope_verdict` で、実行前の判定・実行後の監視・サブエージェント
    終了時の検査の 3 か所が同じ答えを引く。1 か所で付けるのは、3 か所が別々に検査を
    呼ぶと、同じ書き込みが実行前は通って実行後に範囲外と報告されるから。

    **親を引く池は `kept` そのもの**（`by_id`）で、判定が `parent` を引く索引と同じ。
    別の池で引くと、ここでは親が見つかって印が付かないのに、判定の側では見つからず
    `parent=None` のまま子の宣言だけで範囲が決まる（閉じた親やレビュー待ちの親まで
    引ける池にすると、この形になる）。親の範囲で切り詰められないのに通る形は、
    承認していない範囲に書ける経路そのものなので、引けないなら止める側を採る。

    親が閉じたのに子が開いている形は、道具を通る限り起きない（`ops.close_problems` が
    開いた子のある親を閉じさせない）。置き場を手で動かして起きたなら、親を閉じたのは
    人なので、その子を止めるのが人の意思に沿う。
    """
    pool = by_id(kept)
    for t in kept:
        problems = blocking_problems(conf, t, pool)
        t.blocked = problems[0].detail if problems else ""


def _write(path: str, text: str) -> str:
    with fsio.policy(message="書けない ({reason})"):
        failed = fsio.write_text(path, text)
    return f"書けない ({failed})" if failed else ""
