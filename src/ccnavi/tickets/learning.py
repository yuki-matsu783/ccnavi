"""学びの蓄積の記録（気づきの記録・受信箱・判定記録の要約）。
設計は docs/design/tickets/learning-wiki.md

子を閉じる前に、その子の気づきの記録が在り、追跡されているかを見る。記録は雛形と気づきを書く
操作（`ticket learn`）が書き、判定記録の要約は読むだけ。
"""

from __future__ import annotations

import datetime
import glob
import json
import os
import re
import shlex
from typing import TextIO

from ..infra import fsio, gitcmd, settings, tree
from ..records import audit, redact
from . import (
    approval,
    approval_checks,
    approval_marks,
    learning_records,
    phase,
    phasetypes,
    ticket_model,
)

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
INBOX_TRIES = 50


def record_rel(ident: str) -> str:
    return f"{LEARNING_DIR}/{ident}.md"


def digest_rel(ident: str) -> str:
    return f"{LEARNING_DIR}/{ident}.digest.json"


def inbox_rel(ident: str, stamp14: str, n: int = 0) -> str:
    directory = INBOX_DIR
    suffix = f"-{n}" if n > 0 else ""
    return f"{directory}/{ident}-{stamp14}{suffix}.md"


def stamp14(now_text: str) -> str:
    """ "2026-09-29T12:00:00+0900" の形から数字だけを取り、先頭 14 桁を返す。"""
    digits = "".join(ch for ch in now_text if ch.isdigit())
    return digits[:14]


def utc14() -> str:
    return datetime.datetime.now(datetime.UTC).strftime("%Y%m%d%H%M%S")


def write_new(path: str, text: str) -> str:
    """新しいファイルとして書く。既に在れば書かずに "exists"。成功なら空文字、駄目なら理由。"""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    except OSError as e:
        return str(e)
    try:
        with open(path, "x", encoding="utf-8", newline="") as f:
            f.write(text)
    except FileExistsError:
        return "exists"
    except OSError as e:
        return str(e)
    return ""


def unsafe_path(tree_root: str, rel: str) -> str:
    """書く先に途中のリンクや、作業ツリーの外を指すものが在れば、そのパス。無ければ空文字。"""
    parts = [p for p in rel.split("/") if p]
    if not parts:
        return ""
    cur = tree_root
    for part in parts:
        cur = os.path.join(cur, part)
        if os.path.islink(cur):
            return cur
    if os.path.exists(cur):
        real_root = os.path.realpath(tree_root)
        if not os.path.realpath(cur).startswith(real_root + os.sep):
            return cur
    return ""


def scaffold_text(ident: str, parent: str) -> str:
    return f"---\nticket: {ident}\nparent: {parent}\nlearnings: none\n---\n\n"


def clean_body(body: str) -> str:
    text = body.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub("[\u2028\u2029\x85]", "\n", text)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    text = redact.redact(text.strip())
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


def _frontmatter_end(lines: list[str]) -> int | None:
    """行の並びの frontmatter の閉じ `---` の位置。frontmatter が無ければ None。"""
    if not lines or lines[0].strip() != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return i
    return None


def _has_usable_frontmatter(text: str) -> bool:
    lines = text.split("\n")
    end = _frontmatter_end(lines)
    if end is None:
        return False
    return any(line.startswith("learnings:") for line in lines[1:end])


def _count_entries(text: str) -> str:
    """frontmatter の learnings: を、frontmatter の外の `## ` の行の数に合わせる。"""
    lines = text.split("\n")
    end = _frontmatter_end(lines)
    if end is None:
        return text
    n = sum(1 for line in lines[end + 1 :] if line.startswith("## "))
    for i in range(1, end):
        if lines[i].startswith("learnings:"):
            lines[i] = f"learnings: {n}"
            break
    return "\n".join(lines)


def append_entry(text: str, entry: str, ident: str = "", parent: str = "") -> str:
    text = text.lstrip("﻿").replace("\r\n", "\n")
    if not _has_usable_frontmatter(text):
        text = scaffold_text(ident, parent) + text.lstrip("\n")
    if not text.endswith("\n"):
        text += "\n"
    if not text.endswith("\n\n"):
        text += "\n"
    return _count_entries(text + entry)


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
    paths = list(dict.fromkeys(sorted(glob.glob(pattern)) + [log]))
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


def command_word(subject: str) -> str:
    """実行の文字列から、先頭の環境変数の代入と `env` を飛ばした最初の語の名前。"""
    try:
        tokens = shlex.split(subject, posix=True)
    except ValueError:
        return ""
    i = 0
    after_env = False
    while i < len(tokens):
        tok = tokens[i]
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tok, re.DOTALL):
            i += 1
        elif tok == "env":
            after_env = True
            i += 1
        elif after_env and tok.startswith("-"):
            i += 1
        else:
            break
    if i >= len(tokens):
        return ""
    word = re.split(r"[/\\]", tokens[i])[-1]
    if not re.fullmatch(r"[A-Za-z0-9._+-]+", word):
        return ""
    return word


def build_digest(records: list[dict], recorded: bool, tree_name: str | None = None) -> dict:
    """ログの記録から、判定の件数と繰り返した止めを作る。`tree_name` があれば、その木の記録だけ。

    `repeated` は、強制された deny のうち（先頭のルート・ツール・コマンドの語）が同じものを
    回数の多い順に並べ、2 回以上のものだけ入れる。
    """
    if tree_name is not None:
        records = [r for r in records if r.get("tree", "") == tree_name]
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
                word = command_word(subject)
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


def head_text(tree_root: str, rel: str) -> str | None:
    """HEAD に入っている `rel` の中身。HEAD に無い、リンクである、読めないときは None。"""
    rc, out = gitcmd.output(tree_root, ["ls-tree", "HEAD", "--", rel], GIT_TIMEOUT)
    if rc != 0 or not out.strip():
        return None
    parts = out.split()
    if len(parts) < 2 or parts[0] not in ("100644", "100755") or parts[1] != "blob":
        return None
    rc, out = gitcmd.output(tree_root, ["show", f"HEAD:{rel}"], GIT_TIMEOUT)
    if rc != 0:
        return None
    return out


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
    return (
        conf.learning_enabled
        and found.is_child
        and not found.is_consolidate
        and not phase_off(root, conf, found)
    )


def record_missing(
    stderr: TextIO, root: str, conf: settings.Settings, found: ticket_model.Ticket
) -> bool:
    if not applies(root, conf, found):
        return False
    child_tree = tree.worktree_path(root, found.ticket)
    rel = record_rel(found.ticket)
    text = head_text(child_tree, rel)
    if text is not None and text.strip():
        return False
    stderr.write(
        f"ccnavi: {found.ticket} の気づきの記録（{rel}）が、子のブランチの HEAD に無いか、"
        "空か、リンクになっている（コミットが必要です）。"
        f"'{settings.script_command(root, 'ccnavi-ticket.sh')} learn' を実行して記録を書き、"
        "git add と git commit をしてから閉じてください"
        "（気づきが無ければ learnings: none のままでよい）\n"
    )
    return True


def parent_blocked(
    stderr: TextIO, root: str, conf: settings.Settings, found: ticket_model.Ticket
) -> bool:
    """親の `finish` の学びの検査。止めたなら理由を出して True。

    学び以外の検査をすべて通したあと、親の今の HEAD で次を見る。統合を済ませた後も毎回、
    今の HEAD で検査し直す。
    - 記録・パターン・受信箱の置き場に、未コミットか未追跡の変更がある
    - 読み取れない記録か、整合の検査に当たる記録がある
    - 処理すべきものがある（`consolidate` を案内する）
    全部の記録が `none` で受信箱も空なら通る。
    """
    if found.is_child or not conf.learning_enabled:
        return False
    parent_tree = tree.worktree_path(root, found.ticket)
    if not os.path.isdir(parent_tree):
        return False
    ticket_sh = settings.script_command(root, "ccnavi-ticket.sh")
    dirty = learning_records.uncommitted(parent_tree)
    if dirty:
        stderr.write(
            f"ccnavi: {found.ticket} の学びの置き場に未コミットか未追跡の変更がある: "
            f"{', '.join(dirty)}。コミットしてから '{ticket_sh} finish {found.ticket}' "
            "を打ち直してください\n"
        )
        return True
    found_items = learning_records.pending(parent_tree)
    if found_items.broken:
        write_broken(stderr, found_items)
        return True
    if found_items.items:
        stderr.write(
            f"ccnavi: {found.ticket} には、判定されていない気づきがある"
            f"（{len(found_items.items)} 件）:\n"
        )
        for item in found_items.items:
            stderr.write(f"  - {item.describe()}\n")
        stderr.write(
            f"  '{ticket_sh} consolidate {found.ticket}' で統合の子を作り、"
            f"その子を処理して閉じたあと、"
            f"'{ticket_sh} finish {found.ticket}' を打ち直してください\n"
        )
        return True
    return False


def write_broken(stderr: TextIO, found: learning_records.Pending) -> None:
    """読み取れない記録と整合の検査に当たる記録を名指しする。統合では直せない。"""
    for path, why in found.unreadable:
        stderr.write(f"ccnavi: 気づきの記録 {path} を読み取れない: {why}\n")
    for path, why in found.inconsistent:
        stderr.write(f"ccnavi: 気づきの記録 {path} が整合の検査に当たる: {why}\n")
    stderr.write(
        "  統合では直せない。記録を直して（訂正や追加は新しい節を足す。処理済みの節の本文は"
        "書き換えない）コミットしてから打ち直してください\n"
    )


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
    """`learn` の本体。結果は stdout、理由は stderr に出す。終了コードは 0 が書けた、1 が止め。

    処理フロー:
    1. 学びの蓄積が無効なら止める。
    2. `phase.worktree_at` で今いるワークツリーを求める。ツリーの外（ルートも含む）なら止める。
    3. `_bound_ticket` で、このツリーがチケットに結び付くかを見る。`ident_arg` が違えば止める。
    4. `note` が空なら、チケットに結び付く作業に限り、記録の雛形（`scaffold_text`）と要約
       （`build_digest`）を書いて終える。結び付かなければ止める（終了コード 1）。
    5. `note` があれば `validate` と `entry_text` で本文を作り、チケットに結び付くなら記録へ
       `append_entry` で追記し、結び付かないなら受信箱に新しい名前で書く（`inbox_rel`）。
    """
    if not conf.learning_enabled:
        stderr.write(f"ccnavi: 学びの蓄積が disable（{settings.LEARNING_ENV}）\n")
        return 1

    # 今いるワークツリーと、それがチケットに結び付くかを求める
    here = phase.worktree_at(root, conf, cwd)
    if here is None:
        # ワークスペースルートとプロジェクトのルートには書かない（追跡されず、誰がまとめるかも
        # 決まらない置き場になる）。ワークツリーを切って、その中で打ち直す。
        worktree_sh = settings.script_command(root, "ccnavi-worktree.sh")
        stderr.write(
            "ccnavi: ワークスペースルートとプロジェクトのルートには気づきを書けない。"
            f"'{worktree_sh} <名前>' でワークツリーを切り、その中で打ち直してください\n"
        )
        return 1
    ident = here.name
    tree_root = here.root
    tree_name = here.name
    bound = _bound_ticket(root, conf, here.name)

    if ident_arg and ident_arg != ident:
        stderr.write(
            f"ccnavi: 作業ディレクトリは {ident} のもの。"
            f"他の作業（{ident_arg}）の記録へは書けない\n"
        )
        return 1

    now_text = approval_marks.now()

    # --note が無いときは、チケットに結び付く作業なら記録の雛形と要約を書いて終える
    if not note.strip():
        if bound is None:
            stderr.write(
                "ccnavi: チケットに結び付かない作業では --note だけ使える（受信箱に書く）\n"
            )
            return 1
        parent = bound.parent if bound.is_child else ""
        bad = unsafe_path(tree_root, record_rel(ident)) or unsafe_path(tree_root, digest_rel(ident))
        if bad:
            stderr.write(
                f"ccnavi: {bad} はシンボリックリンクか、作業ツリーの外を指すので反映しません\n"
            )
            return 1
        rec_path = os.path.join(tree_root, record_rel(ident))
        try:
            os.makedirs(os.path.dirname(rec_path), exist_ok=True)
        except OSError as e:
            stderr.write(f"ccnavi: 書けない（{e}）\n")
            return 1
        if not os.path.exists(rec_path):
            blank = True
        else:
            try:
                with open(rec_path, encoding="utf-8", errors="replace") as f:
                    blank = not f.read().strip()
            except OSError as e:
                stderr.write(f"ccnavi: 書けない（{e}）\n")
                return 1
        if blank:
            msg = fsio.write_text_atomic(rec_path, scaffold_text(ident, parent))
            if msg:
                stderr.write(f"ccnavi: 書けない（{msg}）\n")
                return 1
        records, recorded = read_log_records(conf.log)
        digest = build_digest(records, recorded, tree_name)
        digest_text = json.dumps(digest, ensure_ascii=True, indent=1) + "\n"
        msg = fsio.write_text_atomic(os.path.join(tree_root, digest_rel(ident)), digest_text)
        if msg:
            stderr.write(f"ccnavi: 書けない（{msg}）\n")
            return 1
        stdout.write(
            f"記録: {record_rel(ident)}"
            "（気づきを書いて git add と git commit をする。"
            "無ければ learnings: none のまま）\n"
        )
        stdout.write(f"要約: {digest_rel(ident)}（git add と git commit をする）\n")
        return 0

    # --note があるときは、本文を組んで記録か受信箱へ書く
    msg = validate(note, target, signal)
    if msg:
        stderr.write(f"ccnavi: {msg}\n")
        return 1
    entry = entry_text(note, target, signal, now_text)

    # チケットに結び付くときは記録へ追記する
    if bound is not None:
        parent = bound.parent if bound.is_child else ""
        bad = unsafe_path(tree_root, record_rel(ident))
        if bad:
            stderr.write(
                f"ccnavi: {bad} はシンボリックリンクか、作業ツリーの外を指すので反映しません\n"
            )
            return 1
        rec_path = os.path.join(tree_root, record_rel(ident))
        try:
            os.makedirs(os.path.dirname(rec_path), exist_ok=True)
            if os.path.exists(rec_path):
                with open(rec_path, encoding="utf-8") as f:
                    text = f.read()
            else:
                text = scaffold_text(ident, parent)
        except (OSError, ValueError) as e:
            stderr.write(f"ccnavi: 書けない（{e}）\n")
            return 1
        msg = fsio.write_text_atomic(rec_path, append_entry(text, entry, ident, parent))
        if msg:
            stderr.write(f"ccnavi: 書けない（{msg}）\n")
            return 1
        stdout.write(f"追記した: {record_rel(ident)}\n")
        return 0

    # 結び付かないときは受信箱に、名前が重ならない新しいファイルを書く
    bad = unsafe_path(tree_root, INBOX_DIR)
    if bad:
        stderr.write(
            f"ccnavi: {bad} はシンボリックリンクか、作業ツリーの外を指すので反映しません\n"
        )
        return 1
    stamp = utc14()
    for n in range(INBOX_TRIES):
        rel = inbox_rel(ident, stamp, n=n)
        msg = write_new(os.path.join(tree_root, rel), inbox_text(ident, entry, now_text))
        if msg == "exists":
            continue
        if msg:
            stderr.write(f"ccnavi: 書けない（{msg}）\n")
            return 1
        stdout.write(f"受信箱に書いた: {rel}\n")
        return 0
    stderr.write("ccnavi: 受信箱の名前が決まらない\n")
    return 1
