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

import json
import os
import shutil
import time
from typing import TextIO

from . import rules, settings
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

# リスクの段階。設計 §17.3 の表。
LEVELS = ((70, "CRITICAL"), (40, "HIGH"), (20, "MEDIUM"), (0, "LOW"))
HIGH_RISK = 40

# 高影響領域。ここへの書き込みは、実行環境・CI・エージェント定義に波及する。
HIGH_IMPACT = (".claude", ".github", ".git", "ci", "Dockerfile", ".gitlab-ci.yml")

POINTS_NEW_SCOPE = 5
POINTS_HIGH_IMPACT = 35
POINTS_PROJECT_ROOT = 30
POINTS_GUARDED = 25

UNSCORED = (
    "expected_roots（想定委譲範囲）との照合",
    "sandbox_root の外かどうか",
    "ツール権限と確認の記憶の緩和",
)


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
    ticket.risk = meta.get("risk") if isinstance(meta.get("risk"), int) else 0
    ticket.tree = ticket.source_tree
    return ticket


def write_copy(
    approved_dir: str, ticket: ticket_mod.Ticket, source_tree: str, risk: int, approved_at: str
) -> str:
    """承認した提案を写す。書けなかった理由を返す。書けたら空文字。"""
    meta = {
        "approved_at": approved_at,
        "source_tree": source_tree,
        "source_path": ticket.path,
        "risk": risk,
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
    try:
        with open(mark_path(approved_dir, parent, phase, kind), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else {}


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


# 人が受け入れたスレッドの控え。フェーズの印とは別の場所に、親ごとに 1 つ置く。
ACCEPTED_FILE = "accepted.json"


def accepted_path(approved_dir: str, parent: str) -> str:
    return os.path.join(approved_dir, PHASES_DIR, parent, ACCEPTED_FILE)


def accepted_threads(approved_dir: str, parent: str) -> set[str]:
    """この親で、人が「未解決のまま進める」と受け入れたスレッドの識別。"""
    try:
        with open(accepted_path(approved_dir, parent), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return set()
    if not isinstance(data, dict):
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
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"threads": keep, "at": now()}, f, ensure_ascii=False, indent=1)
    except OSError as exc:
        return f"{path} ({exc})"
    return ""


def marks(approved_dir: str, parent: str, phase: int) -> dict[str, dict]:
    found = {}
    for kind in MARKS:
        data = read_mark(approved_dir, parent, phase, kind)
        if data is not None:
            found[kind] = data
    return found


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def score(
    proposed: ticket_mod.Ticket, rule_set: rules.RuleSet, tree_root: str
) -> tuple[int, list[str]]:
    """親のリスクを数え、加点した理由を人の読める形で返す。

    子は数えない。親の部分集合なので、新たに書けるようになる領域は無い。
    """
    points = 0
    why: list[str] = []
    for path in sorted(proposed.paths(rules.ALLOW) + proposed.paths(rules.ASK)):
        points += POINTS_NEW_SCOPE
        why.append(f"+{POINTS_NEW_SCOPE} 書き込み範囲 `{path}`")
        if path in ("", ".", "*", "**"):
            points += POINTS_PROJECT_ROOT
            why.append(f"+{POINTS_PROJECT_ROOT} `{path}` は作業ツリー全体を開ける")
            continue
        head = path.split("/")[0]
        if head in HIGH_IMPACT or path.endswith(".tf"):
            points += POINTS_HIGH_IMPACT
            why.append(f"+{POINTS_HIGH_IMPACT} `{path}` は実行環境・CI・エージェント定義に波及する")
        guarded = _guarded_by(path, rule_set, tree_root)
        if guarded:
            points += POINTS_GUARDED
            why.append(f"+{POINTS_GUARDED} `{path}` はルール {guarded} が守っている場所を含む")
    return points, why


def level(points: int) -> str:
    for threshold, name in LEVELS:
        if points >= threshold:
            return name
    return LEVELS[-1][1]


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
    """
    proposals, problems = ticket_mod.scan(root, conf.tickets)
    for problem in problems:
        stderr.write(f"ccnavi: {problem}\n")

    approved, notes = copies(conf.approved)
    for note in notes:
        stderr.write(f"ccnavi: {note}\n")
    closed, _ = copies(conf.approved, closed=True)
    known = by_id(approved + closed)

    # 承認待ち。写しが無いもの。閉じたものは対象外で、再開は人が写しを戻す。
    pending = [t for t in proposals if t.ticket not in known and t.state != ticket_mod.CANCELLED]
    if not pending:
        stdout.write("承認待ちのチケットは無い。\n")
        return 0

    pool = by_id(approved + pending)
    batch: list[tuple[ticket_mod.Ticket, int, list[str], list[rules.Problem]]] = []
    rejected: list[tuple[ticket_mod.Ticket, list[rules.Problem]]] = []
    for t in sorted(pending, key=lambda x: (x.parent or x.ticket, x.ticket)):
        complaints = validate(t, pool)
        if any(p.severity == rules.SEVERITY_ERROR for p in complaints):
            rejected.append((t, complaints))
            continue
        points, why = (0, []) if t.is_child else score(t, rule_set, t.tree_root)
        batch.append((t, points, why, complaints))

    for t, complaints in rejected:
        stderr.write(f"ccnavi: {t.ticket} は承認の対象にしない\n")
        for p in complaints:
            stderr.write(f"  {p}\n")
    if not batch:
        return 1

    stdout.write(screen(batch, pool) + "\n\n")
    total = max(points for _, points, _, _ in batch)
    if total >= HIGH_RISK:
        # 鍵は最も重い親の識別子。識別子順の先頭にすると、軽い親の識別子を
        # 打つだけで重い親まで束ごと通る。
        key = max(batch, key=lambda item: item[1])[0].ticket
        stdout.write(
            f"⚠ リスクスコア {total} ({level(total)})。ワンキーでは承認できない。\n"
            f"  承認するなら識別子を入力: [{key}] "
        )
        stdout.flush()
        if _read(stdin).strip() != key:
            stderr.write("ccnavi: 識別子が一致しないので承認しなかった\n")
            return 1
    else:
        stdout.write(f"この {len(batch)} 件を承認する場合は y、やめる場合はそれ以外: ")
        stdout.flush()
        if _read(stdin).strip().lower() not in ("y", "yes"):
            stderr.write("ccnavi: 承認しなかった\n")
            return 1

    stamp = now()
    for t, points, _, _ in batch:
        failed = write_copy(conf.approved, t, t.tree, points, stamp)
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
    batch: list[tuple[ticket_mod.Ticket, int, list[str], list[rules.Problem]]],
    pool: dict[str, ticket_mod.Ticket],
) -> str:
    """承認を求める画面を組む。

    frontmatter の全文は見せない。人に見せるのは「何が新たに書けるようになるか」
    「子は親からどれだけ絞ったか」「人間レビューの要否」「リスク」。
    新たに書けるようになる領域を最初に置く（REQ-APV-01）。
    """
    lines = [f"Ticket 承認リクエスト: {len(batch)} 件"]
    for t, points, why, complaints in batch:
        lines += [
            "",
            f"== {t.ticket}: {t.title}"
            + (f"（親 {t.parent}、フェーズ {t.phase}）" if t.is_child else "（親）"),
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
        if t.rationale.strip():
            lines.append("■ 理由（エージェントの記述）")
            lines += [f"    {line}" for line in t.rationale.strip().splitlines()]
        lines.append(f"■ 作業ツリー: {t.tree or '(main)'}  提案: {t.path}")
        if not t.is_child:
            lines.append(f"■ リスクスコア: {points} ({level(points)})")
            lines += [f"    {line}" for line in why] or ["    加点なし"]
        warnings = [p for p in complaints if p.severity == rules.SEVERITY_WARN]
        if warnings:
            lines.append("■ 記述のうち、判定に効かないもの")
            lines += [f"    {p.detail}" for p in warnings]
    lines += ["", "■ このスコアが見ていないもの"]
    lines += [f"    {item}" for item in UNSCORED]
    return "\n".join(lines)


def validate(t: ticket_mod.Ticket, pool: dict[str, ticket_mod.Ticket]) -> list[rules.Problem]:
    """承認の対象にしてよいかを見る。親子の制約はここでしか見られない。"""
    problems: list[rules.Problem] = []
    if not t.is_child:
        return problems
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
    return problems


def _read(stdin: TextIO) -> str:
    try:
        return stdin.readline()
    except (OSError, ValueError):
        return ""


def _write(path: str, text: str) -> str:
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError as exc:
        return f"書けない ({exc})"
    return ""


def _guarded_by(path: str, rule_set: rules.RuleSet, root: str) -> str:
    probe = os.path.join(root, path.replace("/", os.sep).rstrip("*"), "probe")
    hits = [
        rule.id or "(id 無し)"
        for rule in rule_set.deny + rule_set.ask
        if any(rule.matches(tool, probe) for tool in ("Write", "Edit", "MultiEdit"))
    ]
    return ", ".join(sorted(set(hits)))
