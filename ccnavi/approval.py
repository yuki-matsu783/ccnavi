"""承認済みの写し。人がチケットに合意したことの記録で、判定はここだけを読む。

## なぜ写しが権威なのか

チケットの提案はエージェントが書ける。判定が提案を直接読むと、範囲の外で
止められたエージェントが範囲を書き足して通れる。承認のときに写しを取り、判定は
写しだけを読む。書き足した提案は写しに届かない。

## 写しを守るのはルールの側

写しは `.claude/ccnavi/tickets/` に置く。

## 閉じる向きだけは承認が要らない

提案が `done/` か `cancelled/` に動いたら、hook が写しを `closed/` へ動かす。
範囲が消える向きなので、エージェントが動かしても危険は増えない。逆に写しを
戻す（再開）のは人の手でやる。

## フェーズの印

`phases/<親>/<N>.<種類>` に、依頼・レビュー済み・省略・通知済みの印を置く。
ゲート（phase.py）はこれを見る。中身は JSON 1 つで、いつ誰が置いたかが入る。
"""

from __future__ import annotations

import io
import json
import os
import shutil
from dataclasses import dataclass, field
from typing import TextIO

from . import fsio, phasetypes, rules, settings, tree
from . import ticket as ticket_mod

# 写しの下の置き場。
CLOSED_DIR = "closed"
PHASES_DIR = "phases"

# フェーズの印の種類。
MARK_REQUESTED = "requested"
MARK_REVIEWED = "reviewed"
MARK_SKIPPED = "skipped"
# pending は「終わったと 1 度伝えた」の印。同じ文を呼び出しごとに繰り返さないため。
MARK_PENDING = "pending"
MARKS = (MARK_REQUESTED, MARK_REVIEWED, MARK_SKIPPED, MARK_PENDING)


def copy_path(approved_dir: str, ticket_id: str) -> str:
    return os.path.join(approved_dir, ticket_id + ".md")


def closed_path(approved_dir: str, ticket_id: str) -> str:
    return os.path.join(approved_dir, CLOSED_DIR, ticket_id + ".md")


def copies(approved_dir: str, closed: bool = False) -> tuple[list[ticket_mod.Ticket], list[str]]:
    """写しの一覧。closed なら閉じた写し。2 つめは読めなかったものの説明。"""
    directory = os.path.join(approved_dir, CLOSED_DIR) if closed else approved_dir
    try:
        names = sorted(os.listdir(directory))
    except FileNotFoundError:
        return [], []
    except OSError as exc:
        return [], [f"写しの置き場を読めない ({exc})"]
    found, notes = [], []
    for name in names:
        if not name.endswith(".md"):
            continue
        path = os.path.join(directory, name)
        ticket = load_copy(path)
        if ticket is None:
            notes.append(f"写し {path} を読めない")
            continue
        if ticket.ticket != name[:-3]:
            notes.append(f"写し {path} の識別子 {ticket.ticket} がファイル名と違う")
            continue
        found.append(ticket)
    return found, notes


def load_copy(path: str) -> ticket_mod.Ticket | None:
    ticket, problems = ticket_mod.load(path)
    if ticket is None:
        return None
    meta = ticket.raw.get(ticket_mod.APPROVAL_KEY)
    if not isinstance(meta, dict):
        return None
    ticket.approved_at = str(meta.get("approved_at") or "")
    ticket.source_tree = str(meta.get("source_tree") or "")
    ticket.source_path = str(meta.get("source_path") or "")
    ticket.tree = ticket.source_tree
    return ticket


def write_copy(
    approved_dir: str, ticket: ticket_mod.Ticket, source_tree: str, approved_at: str
) -> str:
    """承認した提案を写す。書けなかった理由を返す。書けたら空文字。"""
    meta = {
        "approved_at": approved_at,
        "source_tree": source_tree,
        "source_path": ticket.path,
    }
    return _write(
        copy_path(approved_dir, ticket.ticket),
        ticket_mod.render(ticket, {ticket_mod.APPROVAL_KEY: meta}),
    )


def update_copy(approved_dir: str, ticket: ticket_mod.Ticket, fields: dict) -> str:
    """写しの、スクリプトが書く欄だけを更新する。範囲には触らない。"""
    allowed = {k: v for k, v in fields.items() if k in ticket_mod.SCRIPT_FIELDS}
    return _write(copy_path(approved_dir, ticket.ticket), ticket_mod.render(ticket, allowed))


def close_copy(approved_dir: str, ticket_id: str) -> str:
    """写しを closed/ へ動かす。"""
    source = copy_path(approved_dir, ticket_id)
    target = closed_path(approved_dir, ticket_id)
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.move(source, target)
    except OSError as exc:
        return f"写しを閉じられない ({exc})"
    return ""


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
    """このフェーズの印を全部消す。消せた種類を返す。"""
    cleared = []
    for kind in MARKS:
        path = mark_path(approved_dir, parent, phase, kind)
        try:
            os.remove(path)
            cleared.append(kind)
        except OSError:
            continue
    return cleared


# 親ごとの印。フェーズの番号に付かないもの。
#   ready.json   Draft を外した（外してよいと確かめた）。マージに進んでよいの合図
#   wrapup.json  人が「キリの良いところまでやった」と締めた。残りは別の issue へ
PARENT_MARK_READY = "ready"
PARENT_MARK_WRAPUP = "wrapup"


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


# 人が受け入れたスレッドの控え。フェーズの印とは別の場所に、親ごとに 1 つ置く。
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

    フェーズの印とは別の場所に置く。印は 2 つの理由で消える。同じ番号の印は
    `check` が通るたびに上書きされ、その番号に子が足されると `clear_marks` が
    丸ごと消す。どちらでも受け入れの記録が飛び、人がもう一度同じスレッドを
    受け入れることになる。人が 1 度言った「これは承知で進める」は、
    取り消されるまで残す。
    """
    if not threads:
        return ""
    path = accepted_path(approved_dir, parent)
    keep = sorted(accepted_threads(approved_dir, parent) | {str(t) for t in threads if str(t)})
    failed = fsio.write_json(path, {"threads": keep, "at": now()}, indent=1)
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
    """承認の束の 1 件。新規の提案か、親の改版か。

    リスクの点はここに無い。宣言の広さで数える点はやめた。点は子を閉じるときに
    実績（差分）で数える（risk.py）。宣言の広さは、親が `human_review.reason` で言う。
    """

    ticket: ticket_mod.Ticket
    complaints: list[rules.Problem] = field(default_factory=list)
    # 改版なら、いま効いている写し。
    current: ticket_mod.Ticket | None = None
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
) -> int:
    """未承認の提案を束で人に見せ、承認されたら写しを置く。

    エージェントではなく人が端末から叩く経路。提案を書き直す道は用意しない。
    チケットを書くのはエージェントの仕事で、承認する場所で書き替えられると、
    承認した人が承認したものの作者になる。

    束は「いま承認待ちのもの全部」。親が 1 本、その下の子が複数、という形が普通。
    子は親の部分集合なので、新たに書けるようになる領域は親の分だけ。

    親の改版（計画の変更）も同じ束に載る。写しは動かないのが原則で、改版はその
    唯一の例外（設計 §24.15.5）。変えられるのは `plan` と `feedback` だけ。
    """
    gathered = gather(stderr, conf, root)
    if gathered.nothing_pending:
        stdout.write("承認待ちのチケットは無い。\n")
        # 読めない提案があったなら、その旨は標準エラーに出ている。承認するものが
        # 無いのは正常だが、壊れた提案を「何も無い」で通す形にはしない。
        return 1 if gathered.broken else 0
    if not gathered.batch:
        return 1

    stdout.write(gathered.text + "\n\n")
    stdout.write(f"この {len(gathered.batch)} 件を承認する場合は y、やめる場合はそれ以外: ")
    stdout.flush()
    if fsio.read_line(stdin).strip().lower() not in ("y", "yes"):
        stderr.write("ccnavi: 承認しなかった\n")
        return 1
    return _apply(stdout, stderr, conf, gathered.batch, now())


# 承認の JSON の版。`--approve --preview --json` と `--approve --yes … --json` が名乗る。
# VS Code のボード拡張が読み、知らない番号なら読まずに版の違いを言う。
APPROVE_VERSION = 1


@dataclass
class Gathered:
    """いま `--approve` が見せる束と、その周りのもの。見せる・承認するの両方がここから出る。

    束を組む関数を 1 つにしてあるのは、拡張が見せたものと実行ファイルが承認する
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

    @property
    def text(self) -> str:
        if self.nothing_pending:
            return "承認待ちのチケットは無い。"
        return screen(self.batch, self.pool, self.types)

    @property
    def identifiers(self) -> list[str]:
        return sorted(c.ticket.ticket for c in self.batch)


def gather(stderr: TextIO, conf: settings.Settings, root: str) -> Gathered:
    """束を組む。提案を走査し、写しと突き合わせ、載せるものと落とすものに分ける。

    読めない提案や写し、落とした提案の理由は標準エラーにも出す。端末の人は
    そこで読み、拡張は JSON の `problems` / `rejected` で読む。
    """
    from . import phase

    proposals, problems = ticket_mod.scan(root, conf.tickets, conf.projects)
    for problem in problems:
        stderr.write(f"ccnavi: {problem}\n")

    approved, notes = copies(conf.approved)
    for note in notes:
        stderr.write(f"ccnavi: {note}\n")
    closed, _ = copies(conf.approved, closed=True)
    types = phase.load_types(conf)

    pending, revisions = waiting(proposals, approved, closed)
    broken = any(p.severity == rules.SEVERITY_ERROR for p in problems)
    texts = [str(p) for p in problems] + list(notes)
    if not pending and not revisions:
        return Gathered([], [], texts, {}, types, True, broken)

    batch, rejected, pool = _candidates(root, conf, pending, revisions, approved, types)
    for t, complaints in rejected:
        stderr.write(f"ccnavi: {t.ticket} は承認の対象にしない\n")
        for p in complaints:
            stderr.write(f"  {p}\n")
    return Gathered(batch, rejected, texts, pool, types, False, broken)


def preview(
    stdout: TextIO, stderr: TextIO, conf: settings.Settings, root: str, as_json: bool
) -> int:
    """`--approve --preview`。束を見せるだけで、写しは置かない。端末の壁は要らない。

    JSON の形は README「承認の JSON」。束が空でも 0 で返す。拡張は `batch` が空なら
    「承認待ちは無い」と出す。
    """
    gathered = gather(stderr, conf, root)
    if not as_json:
        stdout.write(gathered.text + "\n")
        return 0
    body = {
        "version": APPROVE_VERSION,
        "root": root,
        "generated_at": now(),
        "batch": [_batch_entry(c) for c in gathered.batch],
        "text": gathered.text,
        "rejected": [
            {"ticket": t.ticket, "problems": [str(p) for p in complaints]}
            for t, complaints in gathered.rejected
        ],
        "problems": gathered.problems,
    }
    stdout.write(json.dumps(body, ensure_ascii=False) + "\n")
    return 0


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
    }


def approve_yes(
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    expected: list[str],
    as_json: bool,
) -> int:
    """`--approve --yes <識別子,…>`。拡張のオーバーレイで人が押した承認。

    端末の壁は通らない。代わりに、見せた束と今の束が同じであることを求める。
    拡張が見せたあとに提案が増えていれば承認せず、食い違いを返す。見ていない
    ものを承認する道を塞ぐため。
    """
    wanted = sorted({s.strip() for s in expected if s.strip()})
    gathered = gather(stderr, conf, root)
    current = gathered.identifiers
    if wanted != current:
        if as_json:
            body = {
                "version": APPROVE_VERSION,
                "mismatch": {"expected": wanted, "current": current},
            }
            stdout.write(json.dumps(body, ensure_ascii=False) + "\n")
        stderr.write(
            "ccnavi: 見せた束と今の束が違う（見せた: "
            f"{', '.join(wanted) or '(無し)'} / 今: {', '.join(current) or '(無し)'}）。"
            "見直してから承認する\n"
        )
        return 1
    if not gathered.batch:
        stderr.write("ccnavi: 承認するものが無い\n")
        return 1

    lines = io.StringIO()
    code = _apply(lines, stderr, conf, gathered.batch, now())
    if code != 0:
        return code
    tickets = [c.ticket for c in gathered.batch]
    revisions = {c.ticket.ticket for c in gathered.batch if c.is_revision}
    prompt = _approved_text(tickets, revisions)
    if not as_json:
        stdout.write(lines.getvalue())
        return 0
    body = {
        "version": APPROVE_VERSION,
        "approved": [t.ticket for t in tickets],
        "copies": [copy_path(conf.approved, t.ticket) for t in tickets],
        "lines": [line for line in lines.getvalue().splitlines() if line.strip()],
        "prompt": prompt,
    }
    stdout.write(json.dumps(body, ensure_ascii=False) + "\n")
    return 0


def _approved_text(tickets: list[ticket_mod.Ticket], revisions: set[str]) -> str:
    from . import reasons

    return reasons.approved(tickets, revisions)


# ---- 承認の事実を hook がモデルへ伝える


def _news_path(state_dir: str, session: str, agent_id: str) -> str:
    """このセッション（サブエージェントならその起動）が知っている写しの控え。
    `once-<session>-<agent>.json`（ctxfile）と同じ並びに置く。"""
    session_part = fsio.safe_name(session) or "unknown"
    agent_part = fsio.safe_name(agent_id) or "main"
    return os.path.join(state_dir, f"approved-{session_part}-{agent_part}.json")


def _known(path: str) -> dict[str, str] | None:
    """控えにある「識別子 → 印」。控えが無ければ None。

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
    if isinstance(known, list):
        # 印を持たなかった頃の控え。識別子は伝えたものとして扱い、印は空にする。
        # 空の印は「伝えたが、いつの写しかは分からない」の意味で、_fresh が改版と
        # 見なさない（写しは必ず approved_at を持つので、空は古い控えにしか無い）。
        return {s: "" for s in known if isinstance(s, str)}
    return {}


def _write_known(stderr: TextIO, path: str, known: dict[str, str]) -> None:
    failed = fsio.write_json(path, {"known": dict(sorted(known.items()))})
    if failed:
        stderr.write(f"ccnavi: 承認を伝えた控えを書けない: {failed}\n")


def _mark(t: ticket_mod.Ticket) -> str:
    """写し 1 枚の印。承認した時刻と、改版した時刻。

    改版（`revise_copy`）は写しを書き換えるだけで識別子を増やさないので、識別子だけを
    比べても新しい合意だと分からない。印まで見る。
    """
    meta = t.raw.get(ticket_mod.APPROVAL_KEY)
    meta = meta if isinstance(meta, dict) else {}
    return f"{meta.get('approved_at') or ''}/{meta.get('revised_at') or ''}"


def _copy_marks(conf: settings.Settings) -> dict[str, ticket_mod.Ticket]:
    """いまある写し。開いたものと閉じたもの。

    閉じたものも見る。承認の直後・次の hook の前に子が閉じることがあり、開いたものだけを
    見ると、その承認は誰にも伝わらないまま控えに吸われる。
    """
    found: dict[str, ticket_mod.Ticket] = {}
    for closed in (False, True):
        got, _ = copies(conf.approved, closed=closed)
        for t in got:
            found[t.ticket] = t
    return found


def _fresh(known: dict[str, str], current: dict[str, ticket_mod.Ticket]) -> list[ticket_mod.Ticket]:
    """まだ伝えていない写し。印が変わったもの（改版）も含む。"""
    out = []
    for ident, t in sorted(current.items()):
        recorded = known.get(ident)
        if recorded is None or (recorded != "" and recorded != _mark(t)):
            out.append(t)
    return out


def baseline(stderr: TextIO, conf: settings.Settings, session: str, agent_id: str) -> None:
    """控えが無ければ、いまの写しを「知っているもの」として書く。文は出さない。

    SessionStart から呼ぶ。起動・再開・compact のどれでも来るが、控えがあれば
    触らない。compact の前に置かれた承認は、compact のあとにも 1 度は伝える。
    """
    if not conf.state or not conf.tickets_enabled:
        return
    path = _news_path(conf.state, session, agent_id)
    if _known(path) is None:
        _write_known(stderr, path, {i: _mark(t) for i, t in _copy_marks(conf).items()})


def news(stderr: TextIO, conf: settings.Settings, session: str, agent_id: str) -> str:
    """このセッションがまだ知らない写しがあれば、その承認を伝える文。1 度だけ。

    最初の hook で控えが無ければ、いまの写しを起点として書き、何も伝えない。
    それより後に置かれた写しと、印の変わった写し（親の改版）が「新しい承認」になる。
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
    current = _copy_marks(conf)
    marks = {i: _mark(t) for i, t in current.items()}
    if known is None:
        _write_known(stderr, path, marks)
        return ""
    fresh = _fresh(known, current)
    if not fresh:
        return ""
    # 消えた写しの分も残す。写しが 1 回読めなかっただけで「知らない」に戻すと、
    # 次の回に同じ承認をもう一度伝えることになる。
    _write_known(stderr, path, {**known, **marks})
    return _approved_text(fresh, {t.ticket for t in fresh if t.ticket in known})


def _candidates(
    root: str,
    conf: settings.Settings,
    pending: list[ticket_mod.Ticket],
    revisions: list[ticket_mod.Ticket],
    approved: list[ticket_mod.Ticket],
    types: dict | None,
) -> tuple[list[Candidate], list[tuple[ticket_mod.Ticket, list[rules.Problem]]], dict]:
    """束に載せるものと、落とすものに分ける。3 つめは親子を引くための池。"""
    from . import phase

    open_index = by_id(approved)
    pool = by_id(approved + pending)
    batch: list[Candidate] = []
    rejected: list[tuple[ticket_mod.Ticket, list[rules.Problem]]] = []

    for t in sorted(revisions, key=lambda x: x.ticket):
        current = open_index[t.ticket]
        complaints = revision_problems(root, conf, t, current, types)
        if any(p.severity == rules.SEVERITY_ERROR for p in complaints):
            rejected.append((t, complaints))
            continue
        cand = Candidate(ticket=t, complaints=complaints, current=current)
        if cand.plans_feedback:
            cand.notes = feedback_notes(root, conf, t)
        batch.append(cand)
        # 通った改版だけ、同じ束の子から見える親にする。落ちた改版の計画で子を
        # 通すと、承認されない番号の子が写しになる。
        pool[t.ticket] = t

    for t in sorted(pending, key=lambda x: (x.parent or x.ticket, x.ticket)):
        complaints = validate(t, pool, types)
        complaints += project_problems(t, pool, conf)
        if t.is_child and not any(p.severity == rules.SEVERITY_ERROR for p in complaints):
            parent = pool.get(t.parent)
            if parent is not None:
                complaints += phase.order_problems(root, conf, t, parent, types)
        if any(p.severity == rules.SEVERITY_ERROR for p in complaints):
            rejected.append((t, complaints))
            continue
        batch.append(Candidate(ticket=t, complaints=complaints))
    return batch, rejected, pool


def _apply(
    stdout: TextIO, stderr: TextIO, conf: settings.Settings, batch: list[Candidate], stamp: str
) -> int:
    """承認された束を写しに落とす。改版は写しを書き換え、新規は写しを置く。"""
    from . import phase

    for cand in batch:
        t = cand.ticket
        if cand.is_revision and cand.current is not None:
            failed = revise_copy(conf.approved, cand.current, t, stamp, cand.plans_feedback)
            if failed:
                stderr.write(f"ccnavi: {t.ticket}: {failed}\n")
                return 1
            what = "フィードバック計画" if cand.plans_feedback else "全体計画"
            stdout.write(f"  {t.ticket} の{what}を改版した\n")
            if cand.plans_feedback:
                # レビューの結果を見たうえでの計画なので、最後のレビューはここで済む。
                failed = phase.settle_last_review(conf, t, stamp)
                if failed:
                    stderr.write(f"ccnavi: {t.ticket}: 印を置けない: {failed}\n")
                    return 1
                stdout.write(
                    "  全体計画の最後のレビューを済んだ扱いにした。残った指摘は"
                    "フィードバック作業フェーズの check が数える\n"
                )
            continue
        failed = write_copy(conf.approved, t, t.tree, stamp)
        if failed:
            stderr.write(f"ccnavi: {t.ticket}: {failed}\n")
            return 1
        # 終わったフェーズに子を足したら、そのフェーズの印は消す。印は
        # 「その時点の子が全部見られた」以上の意味を持たない（REQ-TKT-21）。
        if t.is_child and t.phase is not None:
            cleared = clear_marks(conf.approved, t.parent, t.phase)
            if cleared:
                stdout.write(
                    f"  {t.parent} のフェーズ {t.phase} の印（{', '.join(cleared)}）を消した。"
                    "全部閉じたらレビューをもう一度頼むことになる\n"
                )

    stdout.write(f"\n承認した。{conf.approved} に写しを置いた。\n")
    stdout.write("この範囲は次のツール呼び出しから効く。\n")
    return 0


def screen(
    batch: list[Candidate],
    pool: dict[str, ticket_mod.Ticket],
    types: dict | None = None,
) -> str:
    """承認を求める画面を組む。

    frontmatter の全文は見せない。人に見せるのは「何が新たに書けるようになるか」
    「子は親からどれだけ絞ったか」「人間レビューの要否」「リスク」「計画」。
    新たに書けるようになる領域を最初に置く（REQ-APV-01）。
    """
    lines = [f"Ticket 承認リクエスト: {len(batch)} 件"]
    for cand in batch:
        t = cand.ticket
        if cand.is_revision and cand.current is not None:
            lines += ["", f"== {t.ticket}: {t.title}（親の改版）"]
            lines += _plan_diff_lines(cand.current, t, types)
            for note in cand.notes:
                lines.append(f"    {note}")
            lines.append(f"■ 作業ツリー: {t.tree or '(main)'}  提案: {t.path}")
            continue
        lines += [
            "",
            f"== {t.ticket}: {t.title}"
            + (
                f"（親 {t.parent}、フェーズ {_phase_label(t, pool, types)}）"
                if t.is_child
                else "（親）"
            ),
        ]
        if t.is_child:
            # 親は同じ束の中に居ることが普通。写しだけを引くと「写しが無い」になる。
            parent = pool.get(t.parent)
            lines.append("■ 親からどれだけ絞ったか（新たに書けるようになる領域は無い）")
            head = "親 " + (
                ", ".join(parent.paths(rules.ALLOW) + parent.paths(rules.ASK))
                if parent
                else "(写しが無い)"
            )
            lines.append(f"    {head}")
            bound = _type_of(t, pool, types)
            if bound is not None and not bound.inherits_scope:
                lines.append(f"    種類 {bound.title}: " + ", ".join(bound.scope_globs))
        else:
            lines.append("■ このチケットで書き込みが許される領域（これ以外はすべて止まる）")
        for name in rules.SECTIONS:
            paths = t.paths(name)
            if paths:
                lines.append(f"    {name}: " + ", ".join(paths))
        if t.is_child:
            state = "要" if t.review_required else "不要"
            lines.append(
                f"■ 人間レビュー: {state}" + (f"（{t.review_reason}）" if t.review_reason else "")
            )
            if t.predecessors:
                lines.append(f"■ 先行: {', '.join(t.predecessors)}")
        elif t.has_plan:
            lines.append("■ 全体計画（この並びに合意する）")
            lines += _plan_lines(t.plan, 1, types)
            if t.feedback is not None:
                lines.append("■ フィードバック計画")
                lines += _plan_lines(t.feedback, len(t.plan) + 1, types) or ["    （対応なし）"]
        if t.rationale.strip():
            lines.append("■ 理由（エージェントの記述）")
            lines += [f"    {line}" for line in t.rationale.strip().splitlines()]
        lines.append(f"■ 作業ツリー: {t.tree or '(main)'}  提案: {t.path}")
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
            review = "レビュー要" if pt.review == "mr" else "レビュー不要"
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
) -> tuple[list[ticket_mod.Ticket], list[ticket_mod.Ticket]]:
    """いま `--approve` の束に載るもの。新規の承認待ちと、親の改版。

    承認待ちは写しが無いもの。閉じたものは対象外で、再開は人が写しを戻す。
    改版は、開いている親の写しがあり、提案の計画が写しと違うもの。
    `--approve` と `--explain --json` が同じ答えを出すために、ここで 1 度だけ決める。
    """
    known = by_id(approved + closed)
    open_index = by_id(approved)
    pending = [t for t in proposals if t.ticket not in known and t.state != ticket_mod.CANCELLED]
    revisions = [
        t
        for t in proposals
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
    accepted = accepted_threads(conf.approved, parent.ticket)
    notes = [f"受け入れ済みの未解決スレッド: {len(accepted)} 件"]
    if not parent.feedback:
        notes.append("対応なし。見たうえで対応しない、という記録になる")
        if accepted:
            notes.append("受け入れた分は別 issue に切り出したか（ccnavi-review.sh handoff）")
    return notes


def plan_problems(t: ticket_mod.Ticket, types: dict | None) -> list[rules.Problem]:
    """親の計画が種類の定義と噛み合っているか（設計 §24.15.2）。"""
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
    """親の改版を受けてよいか（設計 §24.15.5）。"""
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
                "改版で変えられるのは plan と feedback だけ。範囲が写しと違う",
            )
        )
    if revised.title != current.title or revised.issue != current.issue:
        problems.append(
            rules.Problem(
                rules.SEVERITY_ERROR,
                revised.ticket,
                "改版で変えられるのは plan と feedback だけ。題か課題番号が写しと違う",
            )
        )
    # 全体計画: 子がある番号までは同じ並びでなければならない。
    if revised.plan != current.plan:
        frozen = _last_phase_with_children(conf, current.ticket)
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
            problems.append(
                rules.Problem(
                    rules.SEVERITY_ERROR,
                    revised.ticket,
                    "フィードバック計画は 1 回だけ。承認済みのフィードバック作業フェーズに"
                    "子を足して"
                    "やり直すか、残りは別 issue に切り出す（ccnavi-review.sh handoff）",
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
    """写しの計画を差し替える。範囲と承認の記録はそのまま。"""
    front = dict(current.raw)
    front["plan"] = [item.as_raw() for item in revised.plan]
    if revised.feedback is not None:
        front["feedback"] = [item.as_raw() for item in revised.feedback]
    meta = dict(front.get(ticket_mod.APPROVAL_KEY) or {})
    meta["revised_at"] = stamp
    if feedback_planned:
        meta["feedback_at"] = stamp
    front[ticket_mod.APPROVAL_KEY] = meta
    current.raw = front
    return _write(copy_path(approved_dir, current.ticket), ticket_mod.render(current))


def _scope_signature(t: ticket_mod.Ticket) -> tuple:
    return tuple((e.decision, e.glob, e.regex) for e in t.entries)


def _last_phase_with_children(conf: settings.Settings, parent_id: str) -> int:
    """この親で、子が承認された（開いていても閉じていても）いちばん後ろの番号。"""
    open_copies, _ = copies(conf.approved)
    closed_copies, _ = copies(conf.approved, closed=True)
    numbers = [
        t.phase
        for t in open_copies + closed_copies
        if t.parent == parent_id and t.phase is not None
    ]
    return max(numbers) if numbers else 0


def project_problems(
    t: ticket_mod.Ticket, pool: dict[str, ticket_mod.Ticket], conf: settings.Settings
) -> list[rules.Problem]:
    """`project:` が置き場に在るプロジェクトを指しているか（REQ-MLT-11）。

    子は親から継ぐ。提案に書いていなければここで埋め、写しに書かれる。書いてあって
    親と違えば承認しない。決めるのは人で、承認の画面に出た値が写しに残る。
    """
    if t.is_child:
        parent = pool.get(t.parent)
        if parent is None:
            return []
        if not t.project:
            t.project = parent.project
            if parent.project:
                t.raw["project"] = parent.project
        elif t.project != parent.project:
            return [
                rules.Problem(
                    rules.SEVERITY_ERROR,
                    t.ticket,
                    f"`project: {t.project}` が親 {parent.ticket} の "
                    f"{parent.project or '(ワークスペース)'} と違う。子は親から継ぐ",
                )
            ]
        return []
    known = {p.name for p in tree.projects(conf.projects)}
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
) -> list[rules.Problem]:
    """承認の対象にしてよいかを見る。親子の制約はここでしか見られない。"""
    problems: list[rules.Problem] = []
    if not t.is_child:
        return plan_problems(t, types)
    parent = pool.get(t.parent)
    if parent is None:
        problems.append(
            rules.Problem(rules.SEVERITY_ERROR, t.ticket, f"親 {t.parent} が承認されていない")
        )
        return problems
    if parent.is_child:
        problems.append(
            rules.Problem(
                rules.SEVERITY_ERROR, t.ticket, f"親 {t.parent} 自身が子。深さは 2 段まで"
            )
        )
        return problems
    problems.extend(ticket_mod.subset_problems(t, parent))
    if parent.has_plan and t.phase is not None:
        item = parent.item_at(t.phase)
        if item is None:
            problems.append(
                rules.Problem(
                    rules.SEVERITY_ERROR,
                    t.ticket,
                    f"{t.phase} 番目のフェーズは親 {parent.ticket} の計画に無い"
                    f"（計画は {len(parent.numbered())} 番目まで）",
                )
            )
            return problems
        pt = (types or {}).get(item.type)
        if pt is None:
            problems.append(
                rules.Problem(
                    rules.SEVERITY_ERROR,
                    t.ticket,
                    f"{t.phase} 番目の種類 `{item.type}` の定義が読めない",
                )
            )
            return problems
        problems.extend(phasetypes.scope_problems(t, pt))
    return problems


def _write(path: str, text: str) -> str:
    failed = fsio.write_text(path, text)
    return f"書けない ({failed})" if failed else ""
