"""学びの蓄積の記録（気づきの記録・受信箱・判定記録の要約）。
設計は docs/design/tickets/learning-wiki.md

子を閉じる前に、その子の気づきの記録が在り、追跡されているかを見る。記録は雛形と気づきを書く
操作（`ticket learn`）が書き、判定記録の要約は読むだけで直さない。
"""

from __future__ import annotations

import glob
import json
import os
import re
from typing import TextIO

from ..infra import fsio, gitcmd, settings, tree
from ..records import audit, redact
from . import approval, approval_checks, approval_marks, phase, phasetypes, ticket_model

LEARNING_DIR = "wip/handoff/learnings"
INBOX_DIR = "docs/wiki/inbox"
TARGETS = ("rule", "risk", "phase", "template", "skill", "docs")
UNDECIDED = "未定"
SIGNALS = ("訂正", "落とし穴", "失敗と回避", "誤り")
DEFAULT_SIGNAL = "訂正"
NOTE_LIMIT = 2000
CUT_MARK = "…(切り詰め)"
TITLE_LIMIT = 60
GIT_TIMEOUT = 5.0


def record_rel(ident: str) -> str:
    return f"{LEARNING_DIR}/{ident}.md"


def digest_rel(ident: str) -> str:
    return f"{LEARNING_DIR}/{ident}.digest.json"


def inbox_rel(ident: str, stamp14: str) -> str:
    return f"{INBOX_DIR}/{ident}-{stamp14}.md"


def stamp14(now_text: str) -> str:
    """ "2026-09-29T12:00:00+0900" の形から数字だけを取り、先頭 14 桁を返す。"""
    digits = "".join(ch for ch in now_text if ch.isdigit())
    return digits[:14]


def scaffold_text(ident: str, parent: str) -> str:
    return f"---\nticket: {ident}\nparent: {parent}\nlearnings: none\n---\n\n"


def clean_body(body: str) -> str:
    text = redact.redact(body.strip())
    if len(text) > NOTE_LIMIT:
        return text[:NOTE_LIMIT] + CUT_MARK
    return text


def title_of(body: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:TITLE_LIMIT]
    return ""


def validate(body: str, target: str, signal: str) -> str:
    if not body.strip():
        return "本文が空"
    if target and target not in TARGETS:
        return f"--target は {' / '.join(TARGETS)} のどれか"
    if signal and signal not in SIGNALS:
        return f"--signal は {' / '.join(SIGNALS)} のどれか"
    return ""


def entry_text(body: str, target: str, signal: str, now_text: str) -> str:
    cleaned = clean_body(body)
    kind = target or UNDECIDED
    sig = signal or DEFAULT_SIGNAL
    lines = cleaned.split("\n")
    content = "\n".join([lines[0]] + ["  " + line if line else "" for line in lines[1:]])
    return (
        f"## {title_of(cleaned)}\n"
        "\n"
        f"- 種類: {kind}\n"
        f"- 信号: {sig}\n"
        f"- 内容: {content}\n"
        f"- 記録: {now_text}\n"
    )


_LEARNINGS_LINE = re.compile(r"^learnings:[ \t]*(\S*)")


def bump_count(text: str) -> str:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return text
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return text
    for i in range(1, end):
        line = lines[i]
        m = _LEARNINGS_LINE.match(line)
        if m is None:
            continue
        value = m.group(1)
        if value == "none":
            new = "1"
        elif re.fullmatch(r"\d+", value):
            new = str(int(value) + 1)
        else:
            return text
        eol = line[len(line.rstrip("\r\n")) :]
        lines[i] = f"learnings: {new}{eol}"
        return "".join(lines)
    return text


def append_entry(text: str, entry: str) -> str:
    out = bump_count(text)
    if out and not out.endswith("\n"):
        out += "\n"
    if not out.endswith("\n\n"):
        out += "\n"
    return out + entry


def inbox_text(ident: str, entry: str, now_text: str) -> str:
    return (
        "---\n"
        "type: learning-wiki\n"
        f"title: 受信箱 {ident} {now_text}\n"
        "description: 対話セッションで依頼された気づき 1 件（統合の前）\n"
        "tags: [inbox]\n"
        "keywords: [受信箱, 気づき]\n"
        f"ticket: {ident}\n"
        "learnings: 1\n"
        "---\n"
        "\n"
        f"{entry}"
    )


def read_log_records(log: str) -> tuple[list[dict], bool]:
    if not log:
        return [], False
    base, ext = os.path.splitext(log)
    pattern = f"{glob.escape(base)}.*{ext}"
    paths = sorted(glob.glob(pattern)) + [log]
    records: list[dict] = []
    recorded = False
    for path in paths:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                recorded = True
                for line in f:
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(obj, dict):
                        records.append(obj)
        except OSError:
            continue
    return records, recorded


def build_digest(records: list[dict], recorded: bool) -> dict:
    kinds = {
        "deny": audit.DENY,
        "ask": audit.ASK,
        "nudge": audit.NUDGE,
        "handover": audit.HANDOVER,
    }
    counts = {name: sum(1 for r in records if r.get("decision") == v) for name, v in kinds.items()}
    repeated_counts: dict[tuple[str, str, str], int] = {}
    for rec in records:
        if rec.get("decision") != audit.DENY or not rec.get("enforced"):
            continue
        rules = rec.get("rules")
        if not isinstance(rules, list) or not rules or not isinstance(rules[0], str):
            continue
        rule = rules[0]
        tool = str(rec.get("tool") or "")
        word = ""
        if tool in ("Bash", "PowerShell"):
            subject = rec.get("subject")
            if isinstance(subject, str):
                parts = subject.split()
                if parts:
                    word = re.split(r"[/\\]", parts[0])[-1]
        key = (rule, tool, word)
        repeated_counts[key] = repeated_counts.get(key, 0) + 1
    ordered = sorted(repeated_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    repeated = [
        {"rule": rule, "tool": tool, "word": word, "count": n}
        for (rule, tool, word), n in ordered
        if n >= 2
    ]
    return {"recorded": recorded, "counts": counts, "repeated": repeated}


def tracked(tree_root: str, rel: str) -> bool:
    rc, out = gitcmd.output(tree_root, ["ls-files", "--", rel], GIT_TIMEOUT)
    return rc == 0 and bool(out.strip())


def phase_off(root: str, conf: settings.Settings, found: ticket_model.Ticket) -> bool:
    """この子のフェーズ定義（親に固定した写し）が learning: off か。"""
    if not found.is_child or found.phase is None:
        return False
    copies, _ = approval.scan(conf, root)
    parent = approval_checks.by_id(copies).get(found.parent)
    if parent is None or not parent.has_plan:
        return False
    item = parent.item_at(found.phase)
    pt = phasetypes.types_of(parent).get(item.type) if item is not None else None
    return pt is not None and pt.learning == phasetypes.LEARNING_OFF


def applies(root: str, conf: settings.Settings, found: ticket_model.Ticket) -> bool:
    """学びの蓄積がこの子に効くか。全体が enable で、子で、フェーズ定義が off でない。"""
    return conf.learning_enabled and found.is_child and not phase_off(root, conf, found)


def record_missing(
    stderr: TextIO, root: str, conf: settings.Settings, found: ticket_model.Ticket
) -> bool:
    if not applies(root, conf, found):
        return False
    child_tree = tree.worktree_path(root, found.ticket)
    rel = record_rel(found.ticket)
    path = os.path.join(child_tree, rel)
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError:
            content = ""
        if content.strip() and tracked(child_tree, rel):
            return False
    stderr.write(
        f"ccnavi: {found.ticket} の気づきの記録（{rel}）が無いか、空か、追跡されていない。"
        f"'{settings.script_command(root, 'ccnavi-ticket.sh')} learn' を打って記録を書き、"
        "git add してから閉じてください（気づきが無ければ learnings: none のままでよい）\n"
    )
    return True


def _bound_ticket(root: str, conf: settings.Settings, name: str) -> ticket_model.Ticket | None:
    copies, _ = approval.scan(conf, root)
    index = approval_checks.by_id(copies)
    return tree.lookup(index, name)


def run(
    stdout: TextIO,
    stderr: TextIO,
    root: str,
    conf: settings.Settings,
    cwd: str,
    ident_arg: str,
    note: str,
    target: str,
    signal: str,
) -> int:
    if not conf.learning_enabled:
        stderr.write(f"ccnavi: 学びの蓄積が disable（{settings.LEARNING_ENV}）\n")
        return 1

    here = phase.worktree_at(root, conf, cwd)
    if here is None:
        ident = "root"
        tree_root = root
        bound = None
    else:
        ident = here.name
        tree_root = here.root
        bound = _bound_ticket(root, conf, here.name)

    if ident_arg and ident_arg != ident:
        stderr.write(
            f"ccnavi: 作業ディレクトリは {ident} のもの。"
            f"他の作業（{ident_arg}）の記録へは書けない\n"
        )
        return 1

    now_text = approval_marks.now()

    if not note.strip():
        if bound is None:
            stderr.write(
                "ccnavi: チケットに結び付かない作業では --note だけ使える（受信箱に書く）\n"
            )
            return 1
        parent = bound.parent if bound.is_child else ""
        rec_path = os.path.join(tree_root, record_rel(ident))
        try:
            os.makedirs(os.path.dirname(rec_path), exist_ok=True)
        except OSError as e:
            stderr.write(f"ccnavi: 書けない（{e}）\n")
            return 1
        if not os.path.exists(rec_path):
            msg = fsio.write_text(rec_path, scaffold_text(ident, parent))
            if msg:
                stderr.write(f"ccnavi: 書けない（{msg}）\n")
                return 1
        records, recorded = read_log_records(conf.log)
        digest = build_digest(records, recorded)
        digest_text = json.dumps(digest, ensure_ascii=True, indent=1) + "\n"
        msg = fsio.write_text(os.path.join(tree_root, digest_rel(ident)), digest_text)
        if msg:
            stderr.write(f"ccnavi: 書けない（{msg}）\n")
            return 1
        stdout.write(
            f"記録: {record_rel(ident)}"
            "（気づきを書いて git add する。"
            "無ければ learnings: none のまま）\n"
        )
        stdout.write(f"要約: {digest_rel(ident)}（git add する。直さない）\n")
        return 0

    msg = validate(note, target, signal)
    if msg:
        stderr.write(f"ccnavi: {msg}\n")
        return 1
    entry = entry_text(note, target, signal, now_text)

    if bound is not None:
        parent = bound.parent if bound.is_child else ""
        rec_path = os.path.join(tree_root, record_rel(ident))
        try:
            os.makedirs(os.path.dirname(rec_path), exist_ok=True)
            if os.path.exists(rec_path):
                with open(rec_path, encoding="utf-8") as f:
                    text = f.read()
            else:
                text = scaffold_text(ident, parent)
        except OSError as e:
            stderr.write(f"ccnavi: 書けない（{e}）\n")
            return 1
        msg = fsio.write_text(rec_path, append_entry(text, entry))
        if msg:
            stderr.write(f"ccnavi: 書けない（{msg}）\n")
            return 1
        stdout.write(f"追記した: {record_rel(ident)}\n")
        return 0

    rel = inbox_rel(ident, stamp14(now_text))
    inbox_path = os.path.join(tree_root, rel)
    try:
        os.makedirs(os.path.dirname(inbox_path), exist_ok=True)
    except OSError as e:
        stderr.write(f"ccnavi: 書けない（{e}）\n")
        return 1
    msg = fsio.write_text(inbox_path, inbox_text(ident, entry, now_text))
    if msg:
        stderr.write(f"ccnavi: 書けない（{msg}）\n")
        return 1
    stdout.write(f"受信箱に書いた: {rel}\n")
    return 0
