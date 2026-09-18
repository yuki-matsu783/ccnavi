"""承認済みチケット。人がチケットに合意したことの記録で、判定はここだけを読む。

## なぜ承認済みチケットが権威なのか

チケットの提案はエージェントが書ける。判定が提案を直接読むと、範囲の外で
止められたエージェントが範囲を書き足して通れる。承認のときに承認済みチケットを置き、判定は
承認済みチケットだけを読む。書き足した提案は承認済みチケットに届かない。

## 承認済みチケットを守るのは置き場

承認済みチケットは `.ccnavi/approved/` に置く。ccnavi ディレクトリの下なので、組み込みの
守りがエージェントの書き込みを止める。

## チケットは 1 本のファイルで、写しを持たない（ADR-0055）

承認は `wip/proposals/todo/` の提案を `.ccnavi/approved/doing/` へ動かす。写しを置いて
提案を残す形はやめた。エージェントが打つ `done` は `doing/` から `wip/proposals/review/`
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

import errno
import hashlib
import io
import json
import os
import shutil
from dataclasses import dataclass, field, replace
from typing import TextIO

from . import fsio, phasetypes, rules, settings, tree
from . import ticket as ticket_mod

# 承認済みチケットの下の置き場。作業中（判定が読む）、閉じた、マーカーと記録。
DOING_DIR = ticket_mod.DOING
DONE_DIR = ticket_mod.DONE
PHASES_DIR = "phases"
# 旧の置き場（ADR-0055 まで）。開いたものは直下、閉じたものはここ。`--lint` だけが見る。
LEGACY_CLOSED_DIR = "closed"

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
    try:
        names = sorted(os.listdir(directory))
    except FileNotFoundError:
        return [], []
    except OSError as exc:
        return [], [f"承認済みチケットの置き場を読めない ({exc})"]
    found, notes = [], []
    for name in names:
        if not name.endswith(".md"):
            continue
        path = os.path.join(directory, name)
        ticket, reason = load_copy(path, require_record)
        if ticket is None:
            notes.append(f"承認済みチケット {path} を読めない（{reason}）")
            continue
        if ticket.ticket != name[:-3]:
            notes.append(f"承認済みチケット {path} の識別子 {ticket.ticket} がファイル名と違う")
            continue
        ticket.state = state_of(directory, ticket)
        found.append(ticket)
    return found, notes


def state_of(directory: str, ticket: ticket_mod.Ticket) -> str:
    """置き場の名前から状態を引く。`done/` は取り消しの欄で 2 つに分ける。"""
    name = os.path.basename(directory)
    if name == DONE_DIR:
        return ticket_mod.CANCELLED if ticket.cancelled_at else ticket_mod.DONE
    return name


def load_copy(path: str, require_record: bool = False) -> tuple[ticket_mod.Ticket | None, str]:
    """承認済みチケットを 1 本読む。2 つめは読めなかった理由。

    理由を返すのは、読めない承認済みチケットを `--lint` が名指しするため。
    「読めない」だけでは、BOM のような目に見えない原因に手が届かない。

    `require_record` は `ccnavi_approved` の欄を必須にするか。`.ccnavi/approved/` は
    組み込みの守りがエージェントの書き込みを止めるので、置き場だけで承認と言える
    （ADR-0058）。`wip/proposals/review/` はエージェントが書ける側にあるので、そこは
    欄を求め続ける（`review_all`）。守りが 1 枚しか無い置き場で欄まで外すと、
    組み込みの deny をすり抜けて置かれたファイルが承認済みとして読まれる。
    """
    ticket, problems = ticket_mod.load(path)
    if ticket is None:
        detail = problems[0].detail if problems else "チケットとして読めない"
        return None, detail
    # `ccnavi_approved` は承認の記録であって、承認そのものではない（ADR-0058）。権威は
    # 置き場で、`.ccnavi/approved/` は組み込みの守りがエージェントの書き込みを止める。
    # 欄を必須にすると、端末もボードも無い人が置き場を動かして承認する道が塞がる。
    # 欄が無いぶんの検査（親子・計画・置き場）は `blocking_problems` が判定の側で当てる。
    meta = ticket.raw.get(ticket_mod.APPROVAL_KEY)
    if require_record and not isinstance(meta, dict):
        return None, f"`{ticket_mod.APPROVAL_KEY}` の欄が無い。承認を通っていない"
    if isinstance(meta, dict):
        ticket.approved_at = str(meta.get("approved_at") or "")
        ticket.source_tree = str(meta.get("source_tree") or "")
        ticket.source_path = str(meta.get("source_path") or "")
    # tree は「どのツリーで見つけたか」。scan_all が入れ直す。source_tree（どのツリーの
    # 提案を写したか）とは別物で、子のワークツリーの checkout では食い違う。
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
    """全ツリーの承認済みチケットを、重複を畳まずに集める。

    承認済みチケットは親チケットのブランチに乗るので、そこから切った子のワークツリーにも
    同じものが checkout されている。畳まない側は、ボードが「どこに写っているか」を
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
    """全ツリーのレビュー待ち（提案の置き場の `review/`）を、重複を畳まずに集める。

    ここに在るのは承認済みチケットが `done` で動いてきたもの。`ccnavi_approved` を持たない
    ファイルは読まない。この置き場はエージェントが書ける側にあり、守りは組み込みの
    deny 1 枚なので、欄を second layer として残す（ADR-0058）。
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
    閉じたあとも開いた版が残る。「権威の側に無ければ全部残す」に倒すと、閉じたチケットが
    開いたものとして復活する。権威のツリーがその識別子をどの置き場（作業中・レビュー待ち・
    閉じた）にも持っていないときだけ、見つかった側を残す（親のワークツリーを作る前に
    承認した分を落とさないため）。

    返す前に `mark_blocked` が「信じられない理由」の印を付ける。承認のときにしか
    当たらなかった構造の検査を、判定の側でも当てるため（ADR-0058）。
    """
    found, notes = scan_all(conf, root, closed)
    everything = _everything(conf, root)
    kept = _authoritative(found, everything)
    if not closed:
        # 判定が読むのは作業中の側だけ。閉じたものに印は要らない。
        mark_blocked(conf, kept, everything)
    return kept, notes


def scan_review(conf: settings.Settings, root: str) -> tuple[list[ticket_mod.Ticket], list[str]]:
    """レビュー待ちのチケット。権威のあるツリーの側だけを残す（`scan` と同じ規則）。"""
    found, notes = review_all(conf, root)
    return _authoritative(found, _everything(conf, root)), notes


def _everything(conf: settings.Settings, root: str) -> list[ticket_mod.Ticket]:
    """作業中・レビュー待ち・閉じたの全部を、重複を畳まずに。権威のツリーを決めるために使う。"""
    open_all, _ = scan_all(conf, root)
    closed_all, _ = scan_all(conf, root, closed=True)
    review, _ = review_all(conf, root)
    return open_all + closed_all + review


def _authoritative(
    found: list[ticket_mod.Ticket], everything: list[ticket_mod.Ticket]
) -> list[ticket_mod.Ticket]:
    at_home = {t.ticket for t in everything if t.tree == (t.parent or t.ticket)}
    by_id: dict[str, list[ticket_mod.Ticket]] = {}
    for t in found:
        by_id.setdefault(t.ticket, []).append(t)
    kept: list[ticket_mod.Ticket] = []
    for ticket_id, hits in by_id.items():
        home = hits[0].parent or hits[0].ticket
        kept.extend(hits if ticket_id not in at_home else [t for t in hits if t.tree == home])
    return kept


def dir_of(conf: settings.Settings, ticket: ticket_mod.Ticket) -> str:
    """この承認済みチケットが見つかったツリーの置き場。書き戻す先。"""
    return settings.approved_dir(conf, ticket.tree_root)


def home_dir(
    conf: settings.Settings, root: str, ticket_id: str, parent: str, fallback_root: str = ""
) -> str:
    """この識別子の承認済みチケットを置くツリーの置き場。

    書く先も読む先も 1 つに決めるためのもの。子のワークツリーにも checkout されるが、
    そこへ書くと同じ識別子の承認済みチケットが 2 通りになる。

    探す順は、すでに持っているツリー（親のツリー → ワークスペースかプロジェクトの
    ルート → その他）、親のツリー、fallback_root（提案があったツリー）、
    ワークスペースルート。すでに在る側を先に見るのは、マーカーと記録を承認済みチケットと
    同じ場所に置くため。親のワークツリーは承認のあとに作られることがあり、そこを
    先に見ると、承認済みチケットとマーカーが別のツリーに分かれる。
    """
    home = parent or ticket_id
    named = ""
    holders: list[tuple[tree.Tree, str]] = []
    for t in trees(conf, root):
        where = settings.approved_dir(conf, t.root)
        if t.name == home:
            named = where
        if (
            os.path.exists(copy_path(where, ticket_id))
            or os.path.exists(closed_path(where, ticket_id))
            or os.path.exists(review_path(t.root, conf.tickets, ticket_id))
        ):
            holders.append((t, where))
    for wanted in (lambda t: t.name == home, lambda t: t.is_main, lambda t: True):
        for t, where in holders:
            if wanted(t):
                return where
    if named:
        return named
    return settings.approved_dir(conf, fallback_root or tree.main_tree(root).root)


def admit(approved_dir: str, ticket: ticket_mod.Ticket, source_tree: str, approved_at: str) -> str:
    """承認した提案を `doing/` へ動かす。動かせなかった理由を返す。動かせたら空文字。

    承認の記録（`ccnavi_approved`）を足して書き、元の提案を消す。消せなければ書いた側を
    消して戻す。両方に残ると、以後どの操作も「複数の場所にある」で止まる。
    """
    meta = {
        "approved_at": approved_at,
        "source_tree": source_tree,
        "source_path": ticket.path,
    }
    target = copy_path(approved_dir, ticket.ticket)
    # 人の書いた行（コメント、`|` のブロック）を保つ。読み直して書き出さず、承認の記録の
    # 欄と、置き場から決まった `project:`（frontmatter に無ければ）だけを足す。
    try:
        with open(ticket.path, encoding="utf-8") as f:
            text = f.read()
    except OSError as exc:
        return f"提案を読めない ({exc})"
    if ticket.project and not ticket.declared_project:
        text = ticket_mod.insert_front(text, "project", ticket.project)
    failed = _write(target, ticket_mod.insert_front(text, ticket_mod.APPROVAL_KEY, meta))
    if failed:
        return failed
    try:
        os.remove(ticket.path)
    except OSError as exc:
        fsio.remove(target)
        return f"提案を todo/ から動かせない ({exc})"
    return ""


def update_copy(approved_dir: str, ticket: ticket_mod.Ticket, fields: dict) -> str:
    """作業中の承認済みチケットの、スクリプトが書く欄だけを更新する。範囲には触らない。"""
    return update_fields(copy_path(approved_dir, ticket.ticket), fields)


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


def move_file(source: str, target: str) -> str:
    """チケットを置き場から置き場へ動かす。動かせなかった理由を返す。

    行き先に同じ名前が既に在れば動かさない。黙って上書きすると、閉じた側の記録
    （取り消しの欄など）が消える。同じ識別子が 2 つ在るのは `--lint` が名指しする。

    同じファイルシステムの中なら rename で 1 手。またぐとき（EXDEV）だけ写して消す。
    消せなければ写した側を消して戻す。両方に残ると、以後どの操作も「複数の場所にある」で
    止まる（`admit` と同じ）。Windows は開かれているファイルを消させないので、現実に起きる。

    写して消す側へ流すのは EXDEV に限る。rename が他の理由（元が無い、など）で失敗した
    ときまで流すと、写せずに戻す手が、その間に別のプロセスが置いた行き先を消す。
    """
    if os.path.exists(target):
        return f"チケットを動かせない ({source} → {target}: 行き先に既に在る)"
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        os.rename(source, target)
        return ""
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            return f"チケットを動かせない ({source} → {target}: {exc})"
    try:
        shutil.copy2(source, target)
    except OSError as exc:
        fsio.remove(target)
        return f"チケットを動かせない ({source} → {target}: {exc})"
    try:
        os.remove(source)
    except OSError as exc:
        fsio.remove(target)
        return f"チケットを動かせない ({source} → {target}: {exc})"
    return ""


def settle_review(
    conf: settings.Settings, root: str, parent_id: str, numbers: list[int]
) -> tuple[list[str], str]:
    """この親の、この番号のフェーズのレビュー待ちの子を `done/` へ動かす。

    人がレビューを済ませたときに呼ぶ（`check` / `accept` / `--reviewed --chat` / `wrapup` と、
    フィードバック計画の承認）。返すのは動かした識別子と、動かせなかった理由。
    """
    review, _ = scan_review(conf, root)
    moved: list[str] = []
    for t in sorted(review, key=lambda x: x.ticket):
        if t.parent != parent_id or t.phase not in numbers:
            continue
        where = home_dir(conf, root, t.ticket, parent_id)
        failed = move_file(t.path, closed_path(where, t.ticket))
        if failed:
            return moved, failed
        moved.append(t.ticket)
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
    where = home_dir(conf, root, parent.ticket, "")
    front: dict = {
        "version": ticket_mod.VERSION,
        "ticket": ident,
        "parent": parent.ticket,
        "phase": phase_no,
    }
    if parent.project:
        front["project"] = parent.project
    front["predecessors"] = [c.ticket for c in children]
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
    clear_marks(where, parent.ticket, phase_no)
    return ident, ""


def by_id(tickets: list[ticket_mod.Ticket]) -> dict[str, ticket_mod.Ticket]:
    return {t.ticket: t for t in tickets}


def children_of(tickets: list[ticket_mod.Ticket], parent_id: str) -> list[ticket_mod.Ticket]:
    return [t for t in tickets if t.parent == parent_id]


def mark_path(approved_dir: str, parent: str, phase: int, kind: str) -> str:
    return os.path.join(approved_dir, PHASES_DIR, parent, f"{phase}.{kind}")


def read_mark(approved_dir: str, parent: str, phase: int, kind: str) -> dict | None:
    return fsio.read_dict(mark_path(approved_dir, parent, phase, kind))


def write_mark(approved_dir: str, parent: str, phase: int, kind: str, data: dict) -> str:
    payload = dict(data)
    payload.setdefault("at", now())
    return _write(
        mark_path(approved_dir, parent, phase, kind), json.dumps(payload, ensure_ascii=False)
    )


def clear_marks(approved_dir: str, parent: str, phase: int) -> list[str]:
    """このフェーズのマーカーを全部消す。消せた種類を返す。"""
    cleared = []
    for kind in MARKS:
        path = mark_path(approved_dir, parent, phase, kind)
        try:
            os.remove(path)
            cleared.append(kind)
        except OSError:
            continue
    return cleared


# 親ごとのマーカー。フェーズの番号に付かないもの。
#   ready.json   Draft を外した（外してよいと確かめた）。マージに進んでよいの合図
#   wrapup.json  人が「キリの良いところまでやった」と締めた。残りは別の issue へ
#   closed.json  親を閉じた（`ticket done <親>`）。どのフェーズをどこで見たかを残す
#
# closed.json が要るのは、提案（wip/）が統合先へ戻す前に消えるから。マージリクエストを
# 作らない運び方（全フェーズが `review: chat`）では、締めた事実の残る先がここしか無い。
PARENT_MARK_READY = "ready"
PARENT_MARK_WRAPUP = "wrapup"
PARENT_MARK_CLOSED = "closed"


def parent_mark_path(approved_dir: str, parent: str, name: str) -> str:
    return os.path.join(approved_dir, PHASES_DIR, parent, f"{name}.json")


def read_parent_mark(approved_dir: str, parent: str, name: str) -> dict | None:
    return fsio.read_dict(parent_mark_path(approved_dir, parent, name))


def write_parent_mark(approved_dir: str, parent: str, name: str, data: dict) -> str:
    payload = dict(data)
    payload.setdefault("at", now())
    return _write(
        parent_mark_path(approved_dir, parent, name),
        json.dumps(payload, ensure_ascii=False, indent=1),
    )


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


def accepted_threads(approved_dir: str, parent: str) -> set[str]:
    """この親で、人が「未解決のまま進める」と受け入れたスレッドの識別。"""
    data = fsio.read_dict(accepted_path(approved_dir, parent))
    if not data:
        return set()
    return {str(x) for x in data.get("threads") or [] if str(x)}


def remember_accepted(approved_dir: str, parent: str, threads: list[str]) -> str:
    """受け入れたスレッドを控えに足す。失敗したら、その説明を返す。

    フェーズのマーカーとは別の場所に置く。マーカーは 2 つの理由で消える。同じ番号のマーカーは
    `check` が通るたびに上書きされ、その番号に子が足されると `clear_marks` が
    丸ごと消す。どちらでも受け入れの記録が飛び、人がもう一度同じスレッドを
    受け入れることになる。人が 1 度言った「これは承知で進める」は、
    取り消されるまで残す。
    """
    if not threads:
        return ""
    path = accepted_path(approved_dir, parent)
    keep = sorted(accepted_threads(approved_dir, parent) | {str(t) for t in threads if str(t)})
    # 読んで、足して、書き戻す形。同じファイルの `_write_known` と同じく、
    # 途中を見せない書き方で置く。
    failed = fsio.write_json_atomic(path, {"threads": keep, "at": now()}, indent=1)
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

    リスクの点はここに無い。宣言の広さで数える点はやめた。点は子を閉じるときに
    実績（差分）で数える（risk.py）。宣言の広さは、親が `human_review.reason` で言う。
    """

    ticket: ticket_mod.Ticket
    complaints: list[rules.Problem] = field(default_factory=list)
    # 範囲の超過（親の範囲・種類の上限を超えた項、regex の項）。承認は止めず、判定が
    # 切り詰める。判定に効く（止まる）ので、判定に効かない記述の注意（complaints の warn）とは
    # 混ぜずに持つ。
    overflow: list[rules.Problem] = field(default_factory=list)
    # 改版なら、いま効いている承認済みチケット。
    current: ticket_mod.Ticket | None = None
    # このチケットに効くフェーズの種類（共通層 + `project:` が指す層、設計 §11.4.1）。
    # 承認の対象の中でもチケットごとに違いうるので、候補が引いたものを持ち歩く。
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


def approve(
    stdin: TextIO,
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    rule_set: rules.RuleSet,
    root: str,
    only: list[str] | None = None,
) -> int:
    """未承認の提案をまとめて人に見せ、承認されたら承認済みチケットを置く。

    エージェントではなく人が端末から叩く経路。提案を書き直す道は用意しない。
    チケットを書くのはエージェントの仕事で、承認する場所で書き替えられると、
    承認した人が承認したものの作者になる。

    承認の対象は「いま承認待ちのもの全部」。親が 1 本、その下の子が複数、という形が普通。
    子は親の部分集合なので、新たに書けるようになる領域は親の分だけ。

    親の改版（計画の変更）も一緒に承認の対象に入る。承認済みチケットは動かないのが原則で、改版はその
    唯一の例外（設計 §9.7）。変えられるのは `plan` と `feedback` だけ。

    `only` は承認の対象を識別子で絞る（`ccnavi --approve <識別子>...`）。VS Code 拡張の
    ボードが絞り込みで見えている分だけを渡す。絞りは対象を狭めるだけで、絞らないときに
    落ちるものを通してはいけない。だから、承認待ちに無い識別子が混じっていたら
    何も承認しない（ボードが古いときに、見せた以外のものを通さないため）。親の
    改版が承認待ちなのに対象から外した子も何も承認しない（外すと旧計画で検証される）。
    絞った対象に入らない親を持つ子は「親が承認されていない」で落ちる。
    """
    gathered = gather(stderr, conf, root, only)
    if gathered.refused:
        return 1
    if gathered.nothing_pending:
        stdout.write("承認待ちのチケットは無い。\n")
        # 読めない提案があったなら、その旨は標準エラーに出ている。承認するものが
        # 無いのは正常だが、壊れた提案を「何も無い」で通す形にはしない。
        return 1 if gathered.broken else 0
    if not gathered.batch:
        return 1

    if gathered.note:
        stdout.write(gathered.note + "\n\n")
    stdout.write(gathered.text + "\n\n")
    stdout.write(f"この {len(gathered.batch)} 件を承認する場合は y、やめる場合はそれ以外: ")
    stdout.flush()
    if fsio.read_line(stdin).strip().lower() not in ("y", "yes"):
        stderr.write("ccnavi: 承認しなかった\n")
        return 1
    code = _apply(stdout, stderr, root, conf, gathered.batch, now()).code
    if code == 0 and gathered.rejected:
        # 承認の対象の一部が落ちたときは、通ったぶんを置いてから失敗で終わる。置いたので
        # 繰り返してよく、落ちたものは上で名指ししてある。成功で終わると、
        # 端末を見ていない側（スクリプト、CI）は全部通ったと読む。
        stderr.write(
            f"ccnavi: {len(gathered.rejected)} 件は承認の対象にしなかった。"
            "直して出し直すこと（通ったぶんの承認済みチケットは置いた）\n"
        )
        return 1
    return code


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
    types: dict | None
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
        return screen(self.batch, self.pool, self.types)

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
    落ちるものを通してはいけない。だから、承認待ちに無い識別子が混じっていたら何も
    承認しない（ボードが古いときに、見せた以外のものを通さないため）。親の改版が
    承認待ちなのに対象から外した子も何も承認しない（外すと旧計画で検証される）。
    通らなかった理由は `refused` に入れて返す。呼び手はそれを見て何もしない。

    フェーズの種類は承認の対象全体で 1 つに決まらない。どの層の種類が効くかは各チケットの
    `project:` が決める（設計 §11.4.1）ので、候補を組むところで 1 件ずつ引き、
    引いたものを `Candidate` が持ち歩く。`Gathered.types` は対象全体の種類を持たず、
    いつも None。画面は候補が持つ種類を使う。
    """
    types = None
    proposals, problems = ticket_mod.scan(root, conf.tickets, conf.projects)
    for problem in problems:
        stderr.write(f"ccnavi: {problem}\n")

    approved, notes = scan(conf, root)
    for note in notes:
        stderr.write(f"ccnavi: {note}\n")
    closed, _ = scan(conf, root, closed=True)
    review, _ = scan_review(conf, root)

    pending, revisions = waiting(proposals, approved, closed, review)
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
                "何も承認しない。ボードを更新して承認待ちを確かめる",
            ]
            for line in lines:
                stderr.write(f"ccnavi: {line}\n")
            return Gathered([], [], texts, {}, types, False, broken, "\n".join(lines))
        # 親の改版を外して子だけ通すと、子は承認済みチケット（旧計画）で検証される。絞らなければ
        # 改版後の計画で落ちるものが通ることになるので、親も並べるまで何も承認しない。
        skipped = {t.ticket for t in revisions if t.ticket not in wanted}
        blocked = [t for t in pending if t.ticket in wanted and t.is_child and t.parent in skipped]
        if blocked:
            lines = [
                f"{t.ticket}: 親 {t.parent} の改版が承認待ちなのに承認の対象に無い" for t in blocked
            ]
            lines.append("何も承認しない。親も並べる")
            for line in lines:
                stderr.write(f"ccnavi: {line}\n")
            return Gathered([], [], texts, {}, types, False, broken, "\n".join(lines))
        waiting_count = len(pending) + len(revisions)
        pending = [t for t in pending if t.ticket in wanted]
        revisions = [t for t in revisions if t.ticket in wanted]
        note = f"承認待ち {waiting_count} 件のうち、指定の {len(wanted)} 件だけを承認の対象にする。"

    if not pending and not revisions:
        return Gathered([], [], texts, {}, types, True, broken, "", note)

    batch, rejected, pool = _candidates(root, conf, pending, revisions, approved)
    for t, complaints in rejected:
        stderr.write(f"ccnavi: {t.ticket} は承認の対象にしない\n")
        for p in complaints:
            stderr.write(f"  {p}\n")
    return Gathered(batch, rejected, texts, pool, types, False, broken, "", note)


def preview(
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    as_json: bool,
    only: list[str] | None = None,
) -> int:
    """`--approve --preview`。一覧を見せるだけで、承認済みチケットは置かない。端末の壁は要らない。

    JSON の形は README「承認の JSON」。一覧が空でも 0 で返す。拡張は `batch` が空なら
    「承認待ちは無い」と出す。`only` はボードの絞り込みで見えている分（`--approve` と
    同じ意味）。見せる一覧と承認する対象が同じ絞りを通るようにする。
    """
    gathered = gather(stderr, conf, root, only)
    if gathered.refused:
        return 1
    if not as_json:
        if gathered.note:
            stdout.write(gathered.note + "\n\n")
        stdout.write(gathered.text + "\n")
        return 0
    body = {
        "version": APPROVE_VERSION,
        "root": root,
        "generated_at": now(),
        "batch": [_batch_entry(c) for c in gathered.batch],
        "text": gathered.text,
        "digest": approval_digest(gathered.text, gathered.batch),
        "rejected": [
            {"ticket": t.ticket, "problems": [str(p) for p in complaints]}
            for t, complaints in gathered.rejected
        ],
        "problems": gathered.problems,
    }
    stdout.write(json.dumps(body, ensure_ascii=False) + "\n")
    return 0


def approval_digest(text: str, batch: list[Candidate]) -> str:
    """承認の指紋。承認画面の本文と承認済みチケットに写る中身の SHA-256 の 16 進（小文字）。

    ボードは preview の指紋を `--yes` に `--digest` で返す。識別子だけを比べると、
    見せたあとに提案の範囲や計画が書き換わっても、同じ識別子なら承認が通る。
    本文だけを比べても、画面に出ないのに承認済みチケットへ写る欄（`issue`、Markdown の
    本文、知らない frontmatter の欄）は見せたあとに書き換えられる。だから、本文に続けて
    束のチケットを順に書き出した中身も覆う。

    書き出した中身は承認のときに書くもの（`_apply` の `write_copy` / `revise_copy`）と
    同じ組み立てで、承認の記録の欄（`ccnavi_approved`）だけを除く。記録は承認した時刻を
    持つので、入れると呼ぶたびに指紋が変わる。本文も呼ぶたびに変わる中身を持たない。

    部分をそのままつながず、部分ごとの指紋を件数と一緒に並べて、その並びの指紋を取る。
    区切りの文字でつなぐと、その文字が部分の中に出たときにつなぎ目をずらせる。Markdown の
    本文は生の制御文字（`\\x00` も）を素通しするので、どの文字も「中身に出ない」とは言えない。
    """
    parts = [text] + [_carried(cand) for cand in batch]
    lines = [str(len(parts))] + [hashlib.sha256(p.encode("utf-8")).hexdigest() for p in parts]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _carried(cand: Candidate) -> str:
    """承認済みチケットに写る中身。承認の記録の欄を足す前の姿で書き出す。

    改版は承認済みチケットの frontmatter の計画だけを差し替え、本文は承認済みチケットの
    ものを残す（`revise_copy`）。新規は提案をそのまま写す（`write_copy`）。
    """
    t = cand.ticket
    if cand.is_revision and cand.current is not None:
        front, body = revised_front(cand.current, t), cand.current.body
    else:
        front, body = dict(t.raw), t.body
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


def approve_yes(
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    expected: list[str],
    as_json: bool,
    only: list[str] | None = None,
    digest: str = "",
) -> int:
    """`--approve --yes <識別子,…> --digest <指紋> [<絞り>...]`。拡張のオーバーレイで押した承認。

    端末の壁は通らない。代わりに、見せた一覧と今の一覧が同じであることを求める。
    拡張が見せたあとに提案が増えていれば承認せず、食い違いを返す。見ていない
    ものを承認する道を塞ぐため。識別子に加えて、見せた承認画面の本文と承認済みチケットに
    写る中身の指紋（`digest`）も比べる。識別子が同じでも、見せたあとに提案の範囲や計画、
    画面に出ない欄（`issue` など）が書き換われば承認しない。
    指紋が無ければ承認しない。

    引数は 2 つに分かれる。`--yes` は「オーバーレイに出ていた識別子」で、後ろに並べる語は
    「そのとき掛けていた絞り」（`--approve --preview` に渡したものと同じ）。分けないと検査が
    素通りする。絞りだけで対象を狭めて、その狭めた対象と見せた識別子を比べると、いつでも一致する。
    絞りは preview と同じものを通し、比べるのは「その絞りで今できる一覧」と「見せた識別子」。
    """
    wanted = sorted({s.strip() for s in expected if s.strip()})
    narrowed = [s.strip() for s in (only or []) if s.strip()]
    shown = digest.strip()
    if not shown:
        stderr.write(
            "ccnavi: --yes には --digest（見せた承認画面の本文と承認済みチケットに写る中身の"
            "指紋）が要る\n"
        )
        return 1
    gathered = gather(stderr, conf, root, narrowed)
    # 絞りが通らなかった（承認待ちに無い識別子が混じっている、親の改版を外した）ときは、
    # ボードが古い。拡張には食い違いとして返し、一覧を読み直させる。
    now_shown = gather(stderr, conf, root) if gathered.refused else gathered
    current = now_shown.identifiers
    current_digest = approval_digest(now_shown.text, now_shown.batch)
    if wanted != current or shown.lower() != current_digest:
        if as_json:
            body = {
                "version": APPROVE_VERSION,
                "mismatch": {
                    "expected": wanted,
                    "current": current,
                    "digest": {"expected": digest, "current": current_digest},
                },
            }
            stdout.write(json.dumps(body, ensure_ascii=False) + "\n")
        if wanted != current:
            stderr.write(
                "ccnavi: 見せた一覧と今の一覧が違う（見せた: "
                f"{', '.join(wanted) or '(無し)'} / 今: {', '.join(current) or '(無し)'}）。"
                "見直してから承認する\n"
            )
        else:
            stderr.write(
                "ccnavi: 見せた承認画面の本文と承認済みチケットに写る中身が、今のものと違う"
                "（識別子は同じで、提案の中身が変わった）。見直してから承認する\n"
            )
        return 1
    if not gathered.batch:
        stderr.write("ccnavi: 承認するものが無い\n")
        return 1

    lines = io.StringIO()
    applied = _apply(lines, stderr, root, conf, gathered.batch, now())
    if applied.code != 0:
        # 途中で止まった。置いたものはそのまま残るので、どこまで置いたかを返す。黙って失敗を
        # 返すと、人は「何も起きていない」と読む（README「承認の JSON」の `partial`）。
        if as_json:
            body = {
                "version": APPROVE_VERSION,
                "partial": {
                    "placed": applied.placed,
                    "ticket": applied.stopped_at,
                    "reason": applied.reason,
                    # 止まるまでに出た行（マーカーを消した、改版した）。端末は stdout で見えるが、
                    # 拡張はこの JSON しか見ないので、同じものを渡す。
                    "lines": [line for line in lines.getvalue().splitlines() if line.strip()],
                },
            }
            stdout.write(json.dumps(body, ensure_ascii=False) + "\n")
        else:
            stdout.write(lines.getvalue())
        return applied.code
    tickets = [c.ticket for c in gathered.batch]
    revisions = {c.ticket.ticket for c in gathered.batch if c.is_revision}
    prompt = _approved_text(tickets, revisions, root)
    if not as_json:
        stdout.write(lines.getvalue())
        return 0
    body = {
        "version": APPROVE_VERSION,
        "approved": [t.ticket for t in tickets],
        "copies": [
            copy_path(home_dir(conf, root, t.ticket, t.parent, t.tree_root), t.ticket)
            for t in tickets
        ],
        "lines": [line for line in lines.getvalue().splitlines() if line.strip()],
        "prompt": prompt,
    }
    stdout.write(json.dumps(body, ensure_ascii=False) + "\n")
    return 0


def _approved_text(tickets: list[ticket_mod.Ticket], revisions: set[str], root: str) -> str:
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
    いない承認ごと現状を起点にして黙ることになる。何も知らないことにして、
    伝える側へ倒す。
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
    見ると、その承認は誰にも伝わらないまま控えに吸われる。
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
    控えを置けない（`--state ""`）ときは黙る。診断の試し打ちで記録を汚さない側に倒す。
    サブエージェントは自分の控えを持つので、起動より前の承認は伝えない。

    読み・判定・書きは直列化していない。同じセッションの hook が同時に走ると、同じ承認を
    2 度伝えることがある。伝えすぎる側なので受け入れる。取るべきでないのは逆で、
    競合のために黙る形にはしない。
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
    return _approved_text(fresh, {t.ticket for t in fresh if t.ticket in known}, root)


def _candidates(
    root: str,
    conf: settings.Settings,
    pending: list[ticket_mod.Ticket],
    revisions: list[ticket_mod.Ticket],
    approved: list[ticket_mod.Ticket],
) -> tuple[list[Candidate], list[tuple[ticket_mod.Ticket, list[rules.Problem]]], dict]:
    """承認の対象に入れるものと、落とすものに分ける。3 つめは親子を引くための池。"""
    from . import phase

    open_index = by_id(approved)
    # 親子を引く池は、承認済みチケットと、今回の承認で通ったものだけ。落ちた親を池に残すと、
    # 承認されない親の範囲で子が検証され、親の承認という門を通らずに子の承認済みチケットができる。
    # pending は親が子より前に並ぶ（並べ替えの鍵が親の識別子）ので、子が引くときには
    # 親の通過が決まっている。
    pool = by_id(approved)
    batch: list[Candidate] = []
    rejected: list[tuple[ticket_mod.Ticket, list[rules.Problem]]] = []
    # 層ごとの読み込みは 1 プロジェクト 1 回。承認の対象に同じ層のチケットが
    # 何件あっても、ファイルを読むのはその層につき 1 度で足りる。
    cache: dict[str, dict | None] = {}

    def types_for(t: ticket_mod.Ticket) -> dict | None:
        name = project_of(t, pool)
        if name not in cache:
            cache[name] = phase.load_types(conf, root, name)
        return cache[name]

    for t in sorted(revisions, key=lambda x: x.ticket):
        current = open_index[t.ticket]
        types = types_for(t)
        complaints = revision_problems(root, conf, t, current, types)
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
        complaints += project_problems(t, pool, conf)
        if t.is_child and not any(p.severity == rules.SEVERITY_ERROR for p in complaints):
            parent = pool.get(t.parent)
            if parent is not None:
                complaints += phase.order_problems(
                    root, conf, t, parent, types, added.get(t.parent)
                )
        if any(p.severity == rules.SEVERITY_ERROR for p in complaints):
            rejected.append((t, complaints))
            continue
        batch.append(Candidate(ticket=t, complaints=complaints, overflow=overflow, types=types))
        pool[t.ticket] = t
        if t.is_child:
            added.setdefault(t.parent, []).append(t)
    return batch, rejected, pool


def project_of(t: ticket_mod.Ticket, pool: dict[str, ticket_mod.Ticket]) -> str:
    """このチケットの層を決める `project:`（設計 §11.4.1）。

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


def _apply(
    stdout: TextIO,
    stderr: TextIO,
    root: str,
    conf: settings.Settings,
    batch: list[Candidate],
    stamp: str,
) -> Applied:
    """承認された対象を承認済みチケットに落とす。改版は承認済みチケットを書き換え、新規は承認済みチケットを置く。"""
    from . import phase

    placed: list[str] = []
    for cand in batch:
        t = cand.ticket
        if cand.is_revision and cand.current is not None:
            where = home_dir(conf, root, t.ticket, t.parent, t.tree_root)
            failed = revise_copy(where, cand.current, t, stamp, cand.plans_feedback)
            if failed:
                stderr.write(f"ccnavi: {t.ticket}: {failed}\n")
                return Applied(1, placed, t.ticket, failed)
            placed.append(t.ticket)
            try:
                os.remove(t.path)
            except OSError as exc:
                stderr.write(f"ccnavi: {t.ticket}: 改版の提案を todo/ から消せない ({exc})\n")
            what = "フィードバック計画" if cand.plans_feedback else "全体計画"
            stdout.write(f"  {t.ticket} の{what}を改版した\n")
            if cand.plans_feedback:
                # レビューの結果を見たうえでの計画なので、最後のレビューはここで済む。
                failed = phase.settle_last_review(where, t, stamp)
                if failed:
                    stderr.write(f"ccnavi: {t.ticket}: マーカーを置けない: {failed}\n")
                    return Applied(1, placed, t.ticket, f"マーカーを置けない: {failed}")
                # 全体計画の子でレビュー待ちに残っているものは、見たうえでの計画なので閉じる。
                moved, failed = settle_review(conf, root, t.ticket, list(range(1, len(t.plan) + 1)))
                if failed:
                    stderr.write(f"ccnavi: {t.ticket}: {failed}\n")
                    return Applied(1, placed, t.ticket, failed)
                if moved:
                    stdout.write(f"  レビュー待ちの子を閉じた: {', '.join(moved)}\n")
                stdout.write(
                    "  全体計画の最後のレビューを済んだ扱いにした。残った指摘は"
                    "フィードバック作業フェーズの check が数える\n"
                )
            continue
        where = home_dir(conf, root, t.ticket, t.parent, t.tree_root)
        failed = admit(where, t, t.tree, stamp)
        if failed:
            stderr.write(f"ccnavi: {t.ticket}: {failed}\n")
            return Applied(1, placed, t.ticket, failed)
        placed.append(t.ticket)
        # 終わったフェーズに子を足したら、そのフェーズのマーカーは消す。マーカーは
        # 「その時点の子が全部見られた」以上の意味を持たない（REQ-TKT-21）。
        # 消すのは置けたあと。先に消すと、書けずに終わった（置き場が塞がっている、権限が無い）
        # ときに、子は 1 枚も増えていないのに済んでいたレビューが巻き戻る
        # （test_a_failed_copy_does_not_clear_the_marks_of_a_reviewed_phase）。
        # 置いた直後に落ちる（打ち切られる・電源が切れる）と「子は増えたのにマーカーは残る」
        # ＝見られていない子がいるのに止まらなくなるが、そちらは窓がファイル 1 つぶんで、
        # 頻度が桁違いに低い。順番の入れ替えでは直らない（両方の穴を塞ぐなら、マーカーの時刻と
        # 子の承認時刻を比べて止めるかどうかを決める作りが要る）。
        if t.is_child and t.phase is not None:
            cleared = clear_marks(home_dir(conf, root, t.parent, ""), t.parent, t.phase)
            if cleared:
                kinds = ", ".join(cleared)
                stdout.write(
                    f"  {t.parent} のフェーズ {t.phase} のマーカー（{kinds}）を消した。"
                    "全部閉じたらレビューをもう一度頼むことになる\n"
                )

    stdout.write(f"\n承認した。{conf.approved}/{DOING_DIR}/ へ動かした。\n")
    return Applied(0, placed)


def _origin_line(t: ticket_mod.Ticket) -> str:
    """どのプロジェクトの、どのツリーの、どの提案か（REQ-MLT-11）。

    プロジェクトは提案を置いた場所が決める。人はここで、書き込みが向かうリポジトリを
    見て承認する。
    """
    return (
        f"■ プロジェクト: {t.project or '(ワークスペース)'}"
        f"  ワークツリー: {t.tree or '(main)'}  提案: {t.path}"
    )


def screen(
    batch: list[Candidate],
    pool: dict[str, ticket_mod.Ticket],
    types: dict | None = None,
) -> str:
    """承認を求める画面を組む。

    frontmatter の全文は見せない。人に見せるのは「何が新たに書けるようになるか」
    「子は親からどれだけ絞ったか」「人間レビューの要否」「リスク」「計画」。
    新たに書けるようになる領域を最初に置く（REQ-APV-01）。

    種類は候補が持っているものを使う。承認の対象の中でもチケットごとに層が違いうるので、
    画面の側で 1 つに決めない。
    """
    lines = [f"Ticket 承認リクエスト: {len(batch)} 件"]
    for cand in batch:
        t = cand.ticket
        cand_types = cand.types if cand.types is not None else types
        if cand.is_revision and cand.current is not None:
            lines += ["", f"== {t.ticket}: {t.title}（親の改版）"]
            lines += _plan_diff_lines(cand.current, t, cand_types)
            for note in cand.notes:
                lines.append(f"    {note}")
            lines.append(_origin_line(t))
            continue
        lines += [
            "",
            f"== {t.ticket}: {t.title}"
            + (
                f"（親 {t.parent}、フェーズ {_phase_label(t, pool, cand_types)}）"
                if t.is_child
                else "（親）"
            ),
        ]
        if t.is_child:
            # 親は一緒に承認の対象に入っていることが普通。承認済みチケットだけを引くと
            # 「承認済みチケットが無い」になる。
            parent = pool.get(t.parent)
            lines.append("■ 親からどれだけ絞ったか（新たに書けるようになる領域は無い）")
            head = "親 " + (
                ", ".join(parent.paths(rules.ALLOW) + parent.paths(rules.ASK))
                if parent
                else "(承認済みチケットが無い)"
            )
            lines.append(f"    {head}")
            bound = _type_of(t, pool, cand_types)
            if bound is not None and not bound.inherits_scope:
                lines.append(f"    種類 {bound.title}: " + ", ".join(bound.scope_globs))
        else:
            lines.append("■ このチケットで書き込みが許される領域（これ以外はすべて止まる）")
            # チケットの範囲はルールの allow より強い（設計 §7）。承認する人は「ルールで
            # 開けてあるから範囲の外でも書ける」と読み違えやすいので、承認の前に言う。
            lines.append(
                "    ルールの allow で開けてある場所も、この範囲の外では止まる。"
                "ルールの deny はこの範囲の中でも止まる"
            )
        for name in rules.SECTIONS:
            paths = t.paths(name)
            if paths:
                lines.append(f"    {name}: " + ", ".join(paths))
        if cand.overflow:
            # 範囲のすぐ下に置く。承認は止めないが、判定では止まる。判定に効かない記述の
            # 注意と混ぜると、承認すれば書けると読み違える。
            lines.append("■ 範囲のうち、判定で止まるもの（承認しても書けない）")
            lines += [f"    {p.detail}" for p in cand.overflow]
        if t.is_child:
            state = "要" if t.review_required else "不要"
            lines.append(
                f"■ 人間レビュー: {state}" + (f"（{t.review_reason}）" if t.review_reason else "")
            )
            if t.predecessors:
                lines.append(f"■ 先行: {', '.join(t.predecessors)}")
        elif t.has_plan:
            lines.append("■ 全体計画（この並びに合意する）")
            lines += _plan_lines(t.plan, 1, cand_types)
            if t.feedback is not None:
                lines.append("■ フィードバック計画")
                lines += _plan_lines(t.feedback, len(t.plan) + 1, cand_types) or [
                    "    （対応なし）"
                ]
        if not t.is_child and t.issue is not None:
            lines.append(f"■ 課題: #{t.issue}（マージリクエストの本文で Closes に使う）")
        if t.rationale.strip():
            lines.append("■ 理由（エージェントの記述）")
            lines += [f"    {line}" for line in t.rationale.strip().splitlines()]
        lines.append(_origin_line(t))
        warnings = [p for p in cand.complaints if p.severity == rules.SEVERITY_WARN]
        if warnings:
            lines.append("■ 記述のうち、判定に効かないもの")
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
            review = "レビュー要（計画で強めた）"
        elif pt is not None:
            review = {
                phasetypes.REVIEW_MR: "レビュー要（マージリクエスト）",
                phasetypes.REVIEW_CHAT: "レビュー要（このセッションで）",
            }.get(pt.review, "レビュー不要")
        lines.append(f"    {n}. {title}（{item.type}）" + (f"  {review}" if review else ""))
    return lines


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
    if current.feedback != revised.feedback:
        lines.append("■ フィードバック計画")
        start = len(revised.plan) + 1
        lines += _plan_lines(revised.feedback or [], start, types) or [
            "    （対応なし。見たうえで対応しないという記録になる）"
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
    review: list[ticket_mod.Ticket] = (),
) -> tuple[list[ticket_mod.Ticket], list[ticket_mod.Ticket]]:
    """いま `--approve` で承認の対象に入るもの。新規の承認待ちと、親の改版。

    承認待ちは `todo/` に在って、どの置き場（作業中・レビュー待ち・閉じた）にも同じ識別子が
    無いもの。閉じたものは対象外で、再開は人が承認済みチケットを戻す。
    改版は、作業中の親の承認済みチケットがあり、`todo/` の提案の計画がそれと違うもの。
    `--approve` と `--explain --json` が同じ答えを出すために、ここで 1 度だけ決める。
    """
    known = by_id(approved + closed + list(review))
    open_index = by_id(approved)
    todo = [t for t in proposals if t.state == ticket_mod.TODO]
    pending = [t for t in todo if t.ticket not in known]
    revisions = [
        t
        for t in todo
        if t.ticket in open_index
        and not t.is_child
        and t.has_plan
        and _plan_differs(t, open_index[t.ticket])
    ]
    return pending, revisions


def _plan_differs(proposal: ticket_mod.Ticket, current: ticket_mod.Ticket) -> bool:
    return proposal.plan != current.plan or proposal.feedback != current.feedback


def feedback_notes(root: str, conf: settings.Settings, parent: ticket_mod.Ticket) -> list[str]:
    """フィードバック計画の承認に添える証跡。何を見たうえでの合意かを残す。"""
    accepted = accepted_threads(home_dir(conf, root, parent.ticket, ""), parent.ticket)
    notes = [f"受け入れ済みの未解決スレッド: {len(accepted)} 件"]
    if not parent.feedback:
        notes.append("対応なし。見たうえで対応しない、という記録になる")
        if accepted:
            notes.append("受け入れた分は別 issue に切り出したか（ccnavi-review.sh handoff）")
    return notes


def plan_problems(t: ticket_mod.Ticket, types: dict | None) -> list[rules.Problem]:
    """親の計画が種類の定義と噛み合っているか（設計 §9.7）。"""
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
        # 延期の先は、レビューがある項でなければならない。
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
    return problems


def revision_problems(
    root: str,
    conf: settings.Settings,
    revised: ticket_mod.Ticket,
    current: ticket_mod.Ticket,
    types: dict | None,
) -> list[rules.Problem]:
    """親の改版を受けてよいか（設計 §9.7）。"""
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
    if revised.title != current.title or revised.issue != current.issue:
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
    # フィードバック計画: 無い状態から 1 回だけ、全体計画の最後のレビューが済んでから。
    if revised.feedback != current.feedback:
        if current.feedback is not None:
            # 残りの切り出し先は運び方で違う。MR があれば issue に切り出せるが、
            # chat で回した親はホストに何も無いので、新しい親チケットの提案にする。
            elsewhere = (
                "残りは新しい親チケットの提案として wip/proposals/todo/ に書く"
                if phase.chat_only(root, conf, current.ticket)
                else "残りは別 issue に切り出す（ccnavi-review.sh handoff）"
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
) -> str:
    """承認済みチケットの計画を差し替える。範囲と承認の記録はそのまま。"""
    front = revised_front(current, revised)
    meta = dict(front.get(ticket_mod.APPROVAL_KEY) or {})
    meta["revised_at"] = stamp
    if feedback_planned:
        meta["feedback_at"] = stamp
    front[ticket_mod.APPROVAL_KEY] = meta
    current.raw = front
    return _write(copy_path(approved_dir, current.ticket), ticket_mod.render(current))


def revised_front(current: ticket_mod.Ticket, revised: ticket_mod.Ticket) -> dict:
    """改版で書く frontmatter。承認済みチケットの frontmatter の計画だけを差し替えた写し。

    `current` は書き換えない。承認の指紋（`digest`）も同じものから組むので、見せた
    中身と書く中身がずれない。
    """
    front = dict(current.raw)
    front["plan"] = [item.as_raw() for item in revised.plan]
    if revised.feedback is not None:
        front["feedback"] = [item.as_raw() for item in revised.feedback]
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
    """`project:` が層の名札に予約してある綴りなら error（設計 §11.4）。"""
    if not t.project or not settings.is_reserved_layer_name(t.project):
        return []
    reserved = " と ".join(f"`{name}`" for name in settings.RESERVED_LAYER_NAMES)
    return [
        rules.Problem(
            rules.SEVERITY_ERROR,
            t.ticket,
            f"`project: {t.project}` は層の名札に予約してある綴り（{reserved}）。"
            "その名前のプロジェクトは層として数えないので、このチケットの層が決まらない。"
            "ワークスペース自身の提案は `wip/proposals/` に置く。プロジェクトの提案なら、"
            "そのプロジェクトの名前を変えてから置く",
        )
    ]


def project_problems(
    t: ticket_mod.Ticket, pool: dict[str, ticket_mod.Ticket], conf: settings.Settings
) -> list[rules.Problem]:
    """`project` が置き場と噛み合っているか（REQ-MLT-11）。

    プロジェクトを決めるのは提案を置いた場所（設計 §11.5）。frontmatter の `project:` は
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
                f"{t.declared_project} の提案はワークスペースの {where}/ に置く",
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
                f"{parent.project or 'ワークスペース'}と違う。子は親と同じ置き場に置く",
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
    判定が親と種類の上限で切り詰めるので、承認で止める理由が無い。形の検査は判定では
    補えない（親が無い子は、どの範囲で切り詰めるかが決まらない）ので残す。
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


def child_problems(t: ticket_mod.Ticket, parent: ticket_mod.Ticket | None) -> list[rules.Problem]:
    """子と親の構造の検査。承認（`validate`）と判定（`blocking_problems`）が同じ答えを引く。

    どれも「子の範囲をどの親で切り詰めるか」が決まらない形なので、承認でも判定でも
    通さない。1 か所に置くのは、置き場を動かして承認する運び（ADR-0058）で判定の側の
    検査だけが古くなると、承認を通ったチケットと通らないチケットで答えが割れるから。

    「種類の定義が読めない」はここに入れない。壊れているのは設定で、チケットの形は
    正しい。判定は注記を添えて親の範囲で切り詰める（`judge.ticket_verdict`）。
    """
    if parent is None:
        return [rules.Problem(rules.SEVERITY_ERROR, t.ticket, f"親 {t.parent} が承認されていない")]
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
    """判定がこの承認済みチケットを信じられない理由。空なら信じてよい（ADR-0058）。

    承認のときにしか当たらなかった検査のうち、当たらないと「範囲をどこで切り詰めるか」が
    決まらないものだけを置く。置き場を動かして承認する運びは `--approve` を通らないので、
    同じ検査を判定の側でも当てる。当たれば範囲は効かず、その場所は止まる。

    ここに入れないもの。
    - 計画の形（`plan_problems`）。範囲には効かないので `--lint` が言う
    - フェーズの順序（`phase.order_problems`）。Write はフェーズのゲートを通す決まり
      （ADR-0024）で、判定で止めるとその決定と食い違う。`--lint` が言う
    - 範囲の超過。判定が親と種類の上限で切り詰めるので、止める理由が無い
    """
    problems = list(project_problems(t, pool, conf))
    if t.is_child:
        problems.extend(child_problems(t, pool.get(t.parent)))
    return [p for p in problems if p.severity == rules.SEVERITY_ERROR]


def _parent_pool(everything: list[ticket_mod.Ticket]) -> dict[str, ticket_mod.Ticket]:
    """親を引くための池。開いた写しを先に、無ければレビュー待ち・閉じた写しを。

    閉じた親を引けるようにするのは、親を閉じたあとも子が開いたまま残る形があるから
    （人が置き場を動かして親を閉じた直後など）。引けないと「親が承認されていない」に
    倒れ、正当な子まで止まる。同じ識別子が複数のツリーにあるときは、権威のツリー
    （親の名前のツリー）の側を先に入れる。子のワークツリーに残った古い写しで計画を
    読むと、閉じたあとに開いた版が復活する。
    """
    order = {
        ticket_mod.DOING: 0,
        ticket_mod.REVIEW: 1,
        ticket_mod.DONE: 2,
        ticket_mod.CANCELLED: 3,
    }
    ranked = sorted(
        everything,
        key=lambda t: (order.get(t.state, 9), t.tree != (t.parent or t.ticket)),
    )
    pool: dict[str, ticket_mod.Ticket] = {}
    for t in ranked:
        pool.setdefault(t.ticket, t)
    return pool


def mark_blocked(
    conf: settings.Settings, kept: list[ticket_mod.Ticket], everything: list[ticket_mod.Ticket]
) -> None:
    """判定が読む承認済みチケットに、信じられない理由の印を付ける（ADR-0058）。

    印を読むのは `phase.scope_verdict` で、実行前の判定・実行後の監視・サブエージェント
    終了時の検査の 3 か所が同じ答えを引く。1 か所で付けるのは、3 か所が別々に検査を
    呼ぶと、同じ書き込みが実行前は通って実行後に咎められるから。
    """
    pool = _parent_pool(everything)
    for t in kept:
        problems = blocking_problems(conf, t, pool)
        t.blocked = problems[0].detail if problems else ""


def _write(path: str, text: str) -> str:
    failed = fsio.write_text(path, text)
    return f"書けない ({failed})" if failed else ""
