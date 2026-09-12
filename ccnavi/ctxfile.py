"""当たったルールがモデルへ渡す文（additionalContext）を組む（REQ-PRE-12）。

文（`additionalContext`）と、ファイルの本文（`additionalContextFile`）と、
1 度だけ渡す文（`additionalContextOnce` / `additionalContextOnceFile`）の 3 つを
ここで並べる。once の控えも持つ。文脈はセッションと、サブエージェントなら
その 1 回の起動で分ける。

`additionalContextFile` と `additionalContextOnceFile` は、文の代わりに（または文に
続けて）ファイルの本文をモデルへ渡す。長い案内を rules.yml に抱えず、既にある md を
そのまま指すためのもの。

読むのはワークスペースの中だけ。ルールから任意のファイルをモデルに流し込める形に
しない。絶対パスと `..` で上に出るパスは lint が止め、実行時も読まない。

読む長さは固定の上限で切る。切ったときはそのことを本文の末尾に添える。黙って切ると、
モデルは途中で終わる文を「全部」だと思って読む。続きはファイルを読めば手に入るので、
そう言う。
"""

from __future__ import annotations

import os
import time
from typing import TextIO

from . import fsio, hookio, rules, settings, tree

# 1 回の応答に載せる本文の上限（文字数）。固定。超えた分は載せず、切ったことを言う。
MAX_CHARS = 4000


def bad_path(rel: str) -> str:
    """この欄の値がワークスペースの中を指せない理由。問題なければ空。"""
    if not rel:
        return ""
    slashed = rel.replace("\\", "/")
    if os.path.isabs(rel) or slashed.startswith("/") or (len(slashed) > 1 and slashed[1] == ":"):
        return "絶対パスは使えない。ルートからの相対パスで書く"
    if any(part == ".." for part in slashed.split("/")):
        return "`..` で上に出るパスは使えない"
    return ""


def locate(bases: list[str], rel: str) -> str:
    """候補のルートを順に見て、最初に在ったファイルの場所。無ければ空。"""
    if not rel or bad_path(rel):
        return ""
    for base in bases:
        full = os.path.join(base, rel.replace("/", os.sep))
        if os.path.isfile(full):
            return full
    return ""


def load(stderr: TextIO, bases: list[str], rel: str) -> str:
    """ファイルの本文。無ければ空。上限を超えたら先頭だけを返し、切ったことを末尾に添える。"""
    full = locate(bases, rel)
    if not full:
        return ""
    try:
        with open(full, encoding="utf-8", errors="replace") as f:
            head = f.read(MAX_CHARS + 1)
    except OSError as exc:
        stderr.write(f"ccnavi: {rel} を読めない: {exc}\n")
        return ""
    if len(head) <= MAX_CHARS:
        return head.strip()
    return (
        head[:MAX_CHARS].rstrip()
        + f"\n\n(ccnavi: {rel} は {MAX_CHARS} 文字を超えるので先頭だけを載せた。"
        "続きはこのファイルを読むこと)"
    )


def over_limit(full: str) -> bool:
    """このファイルは上限を超えるか。読めなければ False（無いのと同じ扱い）。"""
    try:
        with open(full, encoding="utf-8", errors="replace") as f:
            return len(f.read(MAX_CHARS + 1)) > MAX_CHARS
    except OSError:
        return False


# 「1 度だけ渡す文」の記憶を残す日数。セッションの開始で消えるのが本筋で、
# これは開始が来ないまま終わったセッションの分を掃くための上限。
ONCE_KEEP_DAYS = 3


def for_rules(
    stderr: TextIO,
    state_dir: str,
    payload: hookio.Input,
    group: list[rules.Rule],
    bases: list[str] | None = None,
) -> str:
    """当たったルールがモデルへ渡す文。1 件ずつ閉じた文なので空行で割る。

    `additionalContext` は当たるたびに渡す。`additionalContextOnce` は、この文脈で
    初めて当たったときだけ渡す。両方あれば初回は並べて、2 回目からは前者だけ。
    文脈はセッションと、サブエージェントならその 1 回の起動（agent_id）で分ける。
    サブエージェントは親の文脈を持たないので、親で渡した文は子にも 1 度渡す。

    `additionalContextFile` / `additionalContextOnceFile` は、文に続けてファイルの本文を
    渡す。探す先は `bases` の順（行き先の作業ツリー、プロジェクト、ルート）で、最初に
    在ったものを読む。無ければ文だけ。once の記憶は文とファイルで分けず、ルール 1 件で
    1 度と数える。

    控えを置く場所が無いとき（`--state ""`）は once の文も毎回渡す。覚えられないなら
    黙るのではなく言うほうに倒す。届かない文は書いていないのと同じになるから。
    """
    parts: list[str] = []
    remembered: set[str] | None = None
    for rule in group:
        every = _with_file(
            stderr, bases or [], rule.additional_context, rule.additional_context_file
        )
        if every:
            parts.append(every)
        if not rule.additional_context_once and not rule.additional_context_once_file:
            continue
        if state_dir:
            if remembered is None:
                remembered = _load_once(stderr, state_dir, payload)
            key = rule.id or f"{rule.match} {rule.glob or rule.regex}"
            if key in remembered:
                continue
            remembered.add(key)
        once = _with_file(
            stderr, bases or [], rule.additional_context_once, rule.additional_context_once_file
        )
        if once:
            parts.append(once)
    if remembered is not None:
        _save_once(stderr, state_dir, payload, remembered)
    return "\n\n".join(parts)


def _with_file(stderr: TextIO, bases: list[str], text: str, rel: str) -> str:
    """文とファイルの本文を空行で並べる。どちらか無ければ在るほうだけ。"""
    body = load(stderr, bases, rel) if rel else ""
    return "\n\n".join(p for p in (text, body) if p)


def bases(conf: settings.Settings, root: str, target: tree.Tree | None) -> list[str]:
    """ルールが指すファイルを探すルートの並び。近いほうから。

    行き先（Bash なら cwd）が作業ツリーの中なら、まずその作業ツリー。そこに無ければ
    切り元のプロジェクト、最後にワークスペースルート。作業ツリーで直している最中の
    案内文がそのまま効くように、作業ツリーを先に見る。
    """
    bases: list[str] = []
    if target is not None and not target.is_main:
        bases.append(target.root)
    if target is not None and target.project:
        bases.append(tree.project_root(conf.projects, target.project))
    bases.append(root)
    return bases


def _once_path(state_dir: str, session: str, agent_id: str) -> str:
    session_part = fsio.safe_name(session) or "unknown"
    agent_part = fsio.safe_name(agent_id) or "main"
    return os.path.join(state_dir, f"once-{session_part}-{agent_part}.json")


def _load_once(stderr: TextIO, state_dir: str, payload: hookio.Input) -> set[str]:
    path = _once_path(state_dir, payload.session_id, payload.agent_id)
    data, failed = fsio.read_json(path)
    if failed is not None:
        if not isinstance(failed, FileNotFoundError):
            stderr.write(f"ccnavi: 1 度だけ渡す文の控えを読めない: {failed}\n")
        return set()
    given = data.get("given") if isinstance(data, dict) else None
    return {s for s in given if isinstance(s, str)} if isinstance(given, list) else set()


def _save_once(stderr: TextIO, state_dir: str, payload: hookio.Input, given: set[str]) -> None:
    path = _once_path(state_dir, payload.session_id, payload.agent_id)
    failed = fsio.write_json(path, {"given": sorted(given)})
    if failed:
        stderr.write(f"ccnavi: 1 度だけ渡す文の控えを書けない: {failed}\n")


def forget(state_dir: str, session: str) -> None:
    """このセッションの「1 度だけ渡す文」の記憶を全部捨てる。古いセッションの分も掃く。"""
    if not state_dir or not os.path.isdir(state_dir):
        return
    mine = os.path.basename(_once_path(state_dir, session, "")).rsplit("-", 1)[0] + "-"
    cutoff = time.time() - ONCE_KEEP_DAYS * 86400
    for name in os.listdir(state_dir):
        if not name.startswith("once-") or not name.endswith(".json"):
            continue
        path = os.path.join(state_dir, name)
        try:
            if name.startswith(mine) or os.path.getmtime(path) < cutoff:
                os.remove(path)
        except OSError:
            # 消せなくても次の開始でまた試す。ここで止めるほどのものではない。
            continue
