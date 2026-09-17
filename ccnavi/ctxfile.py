"""当たったルールがモデルへ渡す文（additionalContext）を組む（REQ-PRE-12）。

文（`additionalContext`）と、ファイルの本文（`additionalContextFile`）と、
1 度だけ渡す文（`additionalContextOnce` / `additionalContextOnceFile`）の 3 つを
ここで並べる。どの回に渡すかを刻む `every` と、その数えの控えもここが持つ。
文脈はセッションと、サブエージェントならその 1 回の起動で分ける。

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

    渡るかどうかは「渡す回」で決まる。`every: N` はその刻みで、当たった回数が N の
    倍数になった回だけが渡す回になる。`every` を書かなければ刻みは 1 で、当たるたびが
    渡す回。2 つの文はどちらもその渡す回を基準に読む。

    | 欄 | いつ渡るか | `every: 5` のとき |
    |---|---|---|
    | `additionalContext` | 渡す回のたび | 5・10・15…回目 |
    | `additionalContextOnce` | 渡す回の最初の 1 回 | 5 回目だけ |

    両方あれば最初の渡す回は並べて、次の渡す回からは前者だけ。文脈はセッションと、
    サブエージェントならその 1 回の起動（agent_id）で分ける。サブエージェントは親の
    文脈を持たないので、親で渡した文は子にも 1 度渡す。

    `additionalContextFile` / `additionalContextOnceFile` は、文に続けてファイルの本文を
    渡す。探す先は `bases` の順（行き先のワークツリー、プロジェクト、ルート）で、最初に
    在ったものを読む。無ければ文だけ。数えは文とファイルで分けず、ルール 1 件で 1 回と
    数える。

    控えを置く場所が無いとき（`--state ""`）は刻まず、once の文も毎回渡す。覚えられない
    なら黙るのではなく言うほうに倒す。届かない文は書いていないのと同じになるから。
    """
    parts: list[str] = []
    counted: dict[str, int] | None = None
    consulted = False
    for rule in group:
        has_once = bool(rule.additional_context_once or rule.additional_context_once_file)
        # 数えを控えに残すのは、刻みを持つルールと once を持つルールだけ。ほかは
        # どのみち毎回渡すので、数えても判定 1 回ぶんの書き込みが増えるだけになる。
        if state_dir and (rule.every > 1 or has_once):
            if not consulted:
                counted = _load_once(stderr, state_dir, payload)
                consulted = True
            if counted is None:
                # 読めなかったときは、覚えていないものとして渡し、書き戻さない。
                # `--state ""` と同じ「覚えられないなら言う」側だが、上書きだけは
                # しない。ここで書くと、読めなかっただけの控えを空で潰すことになる。
                delivering, first = True, True
            else:
                key = rule.id or f"{rule.match} {rule.glob or rule.regex}"
                hits = counted.get(key, 0) + 1
                counted[key] = hits
                # 渡す回は刻みの倍数になった回。その最初は刻みの回そのものなので、
                # 「once を渡したか」を別の欄で覚えなくてよい。欄を 2 つ持つと、
                # 片方だけ古い控えが生まれる。
                delivering, first = hits % rule.every == 0, hits == rule.every
        else:
            delivering, first = True, True
        if delivering:
            # 文の `{root}` は、止めたときの文面と同じくワークスペースルートの実パスにする。
            text = _with_file(
                stderr,
                bases or [],
                rules.fill_root(rule.additional_context, rule.root),
                rule.additional_context_file,
            )
            if text:
                parts.append(text)
        if has_once and first:
            once = _with_file(
                stderr,
                bases or [],
                rules.fill_root(rule.additional_context_once, rule.root),
                rule.additional_context_once_file,
            )
            if once:
                parts.append(once)
    if counted is not None:
        _save_once(stderr, state_dir, payload, counted)
    return "\n\n".join(parts)


def _with_file(stderr: TextIO, bases: list[str], text: str, rel: str) -> str:
    """文とファイルの本文を空行で並べる。どちらか無ければ在るほうだけ。"""
    body = load(stderr, bases, rel) if rel else ""
    return "\n\n".join(p for p in (text, body) if p)


def bases(conf: settings.Settings, root: str, target: tree.Tree | None) -> list[str]:
    """ルールが指すファイルを探すルートの並び。近いほうから。

    行き先（Bash なら cwd）がワークツリーの中なら、まずそのワークツリー。そこに無ければ
    その元リポジトリ、最後にワークスペースルート。ワークツリーで直している最中の
    案内文がそのまま効くように、ワークツリーを先に見る。
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


def _load_once(stderr: TextIO, state_dir: str, payload: hookio.Input) -> dict[str, int] | None:
    """この文脈で、どのルールが何回当たったか。まだ無ければ空、**読めなければ None**。

    `given` は「鍵 → 回数」。古い版の ccnavi が書いた形（鍵の並び）は「1 回当たった」
    として読む。`every` を書かないルールでは 1 回でも「once は渡した」になるので、
    古い控えを引き継いだセッションの見え方は今までと変わらない。

    「まだ無い」と「読めない」を分けて返すのは、呼ぶ側が上書きしてよいかを
    決められるようにするため。同じ扱いにすると、読めなかった回に「まだ 1 回も
    当たっていない」ものとして書き戻し、覚えていたぶんを消してしまう。
    読めないのは控えが在るときにしか起きないので、消す先はいつも中身のある控えになる。
    """
    path = _once_path(state_dir, payload.session_id, payload.agent_id)
    data, failed = fsio.read_json(path)
    if failed is not None:
        if not isinstance(failed, FileNotFoundError):
            stderr.write(f"ccnavi: 渡した回の控えを読めない: {failed}\n")
            return None
        return {}
    given = data.get("given") if isinstance(data, dict) else None
    if isinstance(given, list):
        return {s: 1 for s in given if isinstance(s, str)}
    if not isinstance(given, dict):
        return {}
    return {k: n for k, n in given.items() if isinstance(k, str) and isinstance(n, int)}


def _save_once(
    stderr: TextIO, state_dir: str, payload: hookio.Input, given: dict[str, int]
) -> None:
    path = _once_path(state_dir, payload.session_id, payload.agent_id)
    # 取り合いになる控えなので、途中を見せない書き方で置く。素の open(path, "w") だと
    # 書いている最中は空で、そこを別の呼び出しに読まれると「まだ 1 回も当たっていない」に
    # 倒れる。途中で落ちたときも空のまま残り、次の起動が同じ読み違いをする。
    failed = fsio.write_json_atomic(path, {"given": dict(sorted(given.items()))})
    if failed:
        stderr.write(f"ccnavi: 渡した回の控えを書けない: {failed}\n")


def forget(state_dir: str, session: str) -> None:
    """このセッションの数え（渡した回）を全部捨てる。古いセッションの分も掃く。

    捨てると「1 度だけ渡す文」はまた渡り、`every` の刻みも 0 から数え直しになる。
    どちらも「この文脈で何回目か」を見ているので、文脈が変われば一緒に忘れる。
    """
    if not state_dir or not os.path.isdir(state_dir):
        return
    mine = os.path.basename(_once_path(state_dir, session, "")).rsplit("-", 1)[0] + "-"
    cutoff = time.time() - ONCE_KEEP_DAYS * 86400
    for name in os.listdir(state_dir):
        if not name.endswith(".json"):
            continue
        # 承認を伝えた控え（approval.news）は同じ場所に置く。こちらはセッションの
        # 再開で捨てず、古いものだけ一緒に掃く。
        stale = name.startswith("approved-")
        if not stale and not name.startswith("once-"):
            continue
        path = os.path.join(state_dir, name)
        try:
            if (not stale and name.startswith(mine)) or os.path.getmtime(path) < cutoff:
                os.remove(path)
        except OSError:
            # 消せなくても次の開始でまた試す。ここで止めるほどのものではない。
            continue
