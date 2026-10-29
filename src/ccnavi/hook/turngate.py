"""ターン分離の関門。「A を打ったら、ユーザの発言が 1 回はさまるまで B を打てない」を止める。

エージェントが自分の判断で止まる以外に守られない手順（差分をユーザに見せて返事を待つ、など）を、
「A のあとにユーザの発言が 1 回あった」という機械で確かめられる事実に置き換える（付き添いのある
セッションでは、ユーザの確認で置き換える。下の宣言の節）。止めるのは B の側
だけ。A と B が同じターンに両方起きたことを、あとから点検する処理は作らない。

## 宣言

宣言 1 件（`Gate`）が 1 組を表す。持つのは、id、A の条件、B の条件、A と B を同じ対象に結び付ける
キー（任意）、文面の素材。宣言の表を渡して判定する関数（`check`・`note`）なので、組を足すには
宣言を 1 件足す。特定の組には依存しない（tests/guard/test_turngate.py が、`integrate` と関係の無い
別の組で確かめる）。

- 条件（`Cond`）は `glob` か `regex` のどちらか 1 つ。読み方はルールと同じ
  （`rules.compile_condition`。大文字小文字を区別しない、先読み・後方参照は使えない）。
  適用する先はコマンドを `;` や `&&` で分けた
  1 つずつ（`shellread.SEP` で分けた単位）で、実行役が中で実行するコマンドも含む。`but` は
  「同じコマンドにこれがあれば、この条件にヒットしない」
- キーは `regex` の名前つきグループ `key`。A と B の両方が持てば、同じキーの組だけを対象にする。
  どちらも持たなければ、宣言ごとに 1 組（キーなし）。片方だけが持つ宣言は組み立てられない。キーは
  大文字小文字を区別せず比べる。変数・置換・glob などを含み、値に解けないキー（`$name`）は、どの
  キーとも一致する扱い（A の側なら、どの B も止める）。読めない書き方で関門を外せないようにするため
- 宣言の置き場は組み込みの表（`BUILTIN`）だけ。ユーザの了承の関門を、配布先のルールファイルで
  外されないようにするため。ルールファイルから足す道は、いまは持たない
- `ask_if_attended` を立てた宣言は、付き添いのある（対話の）セッションでは、発言を待つ関門を
  働かせず、B を確認ダイアログ（ask）に出す。ユーザの許可はダイアログという別の操作で得られるので、
  同じターンで確定できる。同じコマンドに A と B を並べた呼び出しだけは、対話でも止める（差分を
  見せる前に確定させない）。付き添いのないセッションは確認に答える人がいないので、これまでの関門が働く。
  付き添いの有無は `ccnavimd.unattended` が決める

## 状態

- A を許したとき（`note`）、`state/turngate-<セッション>-…json` に「宣言 id・キー・時刻・呼び出しの
  識別子」を 1 件 1 ファイルで残す。1 件 1 ファイルなので、並行する呼び出しが書き換え合わない。
  残すのは実行前（PreToolUse）。実行後（PostToolUse の成功）に残すと、同じメッセージで並べた呼び出し
  （A と B を並行して打つ）が、A の記録の前に B を通る。代わりに、A が最終的に拒否されたとき
  （`forget_call`）と、A が失敗したとき（PostToolUseFailure）に、その呼び出しの記録を消す。失敗した
  A は数えない。数えると、以前の A でユーザの返事を得たあとに、誤った再実行が失敗しただけで、B が
  もう 1 度止まる
- B の判定（`check`）は、同じ宣言・同じキーの記録があり、その時刻より後のユーザの発言の記録
  （`records/userprompt.py`）が無ければ止める。同じコマンドの中に A と B が両方ある場合も止める
  （まだ記録が無いため）
- 記録は、B が通ったとき（一致した記録すべて）と、セッションの開始・再開（`clear`）で消す。
  compact と
  clear では消さない（ターンの途中で compact が走りうる）
- サブエージェントの呼び出しも同じセッションの記録を使う。止める側にも通す側にも区別は無い

## 壊れた記録

閉じる側（B を止める）に倒す。壊れた記録が防御を消さないため。ただし文面が次の一手を言う。

- ユーザの発言の記録が無い・読めない: 「まだ発言が無い」。次のユーザの発言が書き直すので、詰まらない
- 関門の記録のファイルが読めない（壊れた・形が違う）: このセッションのどの B も止め、文に記録の
  パスを出す。時刻・宣言・キーは推さない。次のユーザの発言（`drop_broken`）が消すので、詰まらない。
  記録の A はその発言より前にあるので、消しても発言をはさんだ事実は変わらない
- 関門の置き場を読めない: B を止め、ユーザに置き場を確かめてもらう
- A の記録を書けない: A を止め、ユーザに置き場を確かめてもらう

セッション id と置き場が無い呼び出し（診断など、記録を持てない読み方）は、関門が働かない。
"""

from __future__ import annotations

import functools
import hashlib
import os
import re
import time
from dataclasses import dataclass

from ..infra import fsio, shellread
from ..policy import rules, texts
from ..records import userprompt

# 拒否コード。宣言ごとには分けない。どの宣言で止めたかは、記録の `rules`（RULE_PREFIX + id）と
# 文面が言う。
CODE = "DENY_NEEDS_USER_TURN"
RULE_PREFIX = "builtin:turn-gate:"

# 記録のファイル名。prune は `turngate-` で始まる名前を、セッションの記録と読む。
PREFIX = "turngate-"
SUFFIX = ".json"

# キーの名前つきグループ。
KEY = "key"
# 値に解けないキー。どのキーとも一致する。
WILDCARD = "*"
_SAFE_KEY = re.compile(r"[A-Za-z0-9._-]+")

# 止めた理由。
SAME_COMMAND = "same-command"
NO_USER_TURN = "no-user-turn"
UNREADABLE = "unreadable"
BROKEN = "broken"
UNWRITABLE = "unwritable"
ASK = "ask"

# 文面に載せるキーの長さの上限（識別子の長さの上限と同じ）。
_SHOWN = 64


@dataclass(frozen=True)
class Cond:
    """A か B の条件。`regex` か `glob` のどちらか 1 つ。

    `but` は regex のリストで、同じコマンドにどれかがあれば、この条件にヒットしない。
    """

    regex: str = ""
    glob: str = ""
    but: tuple[str, ...] = ()


@dataclass(frozen=True)
class Gate:
    """宣言 1 件。「`first` を打ったら、ユーザの発言が 1 回はさまるまで `second` を打てない」。

    文面は文面ファイルの `message`（識別子）が持つ。
    4 つの段（何が起きたか・なぜ・次の一手・迂回しない）のうち、
    段 1 は `what` の場合分け（`same_command`・`no_user_turn`）で、
    `first_name` と `second_name`、宣言の id とキー（`where`）、`key` を埋める。
    別の組を足すときは、文面ファイルにも 1 件足す。
    """

    id: str
    first: Cond
    second: Cond
    first_name: str
    second_name: str
    message: str
    # 次の一手の `{key}` に、キーが解けなかったとき入れる仮の値。
    key_name: str = "<キー>"
    # 付き添いのあるセッションでは、発言を待つ代わりに B を確認（ask）に出す。
    ask_if_attended: bool = False
    # ask の文を引く識別子。`ask_if_attended` のときだけ使う。
    ask_message: str = ""


@dataclass(frozen=True)
class Stop:
    """B を止める結論。`key` は文面に出す B のキー（キーなしの宣言では空）。"""

    gate: Gate
    reason: str
    key: str = ""
    detail: str = ""


@dataclass(frozen=True)
class _Entry:
    gate: str
    key: str
    at: float
    path: str


def rule_id(gate: Gate) -> str:
    """記録の `rules` に残す名前。"""
    return RULE_PREFIX + gate.id


# 最初の利用は 1 件。差分をユーザに見せて返事を待つ手順書の約束を、機械にする。
# キーは `<名前>`。`integrate-done` は `-m <メッセージ>` を名前の前後どちらにも置ける
# （`ig_done_args`）ので、メッセージの引数を読み飛ばしてから名前を取る。
# `--abort` は先頭が `-` なので
# 名前として取れず、A にならない。
INTEGRATE_GATE = Gate(
    id="integrate-user-turn",
    first=Cond(regex=r"ccnavi-git\.sh\s+integrate\s+(?P<key>[^-\s\x00\x01][^\s\x00\x01]*)"),
    second=Cond(
        regex=(
            r"ccnavi-git\.sh\s+integrate-done\s+"
            r"(?:(?:-m|--message)\s+[^\s\x00]+\s+|--message=[^\s\x00]+\s+)*"
            r"(?P<key>[^-\s\x00\x01][^\s\x00\x01]*)"
        )
    ),
    first_name="ccnavi-git.sh integrate",
    second_name="ccnavi-git.sh integrate-done",
    message="DENY_NEEDS_USER_TURN__INTEGRATE",
    key_name="<名前>",
    ask_if_attended=True,
    ask_message="NOTICE_ASK_USER_CONFIRM__INTEGRATE",
)

BUILTIN: tuple[Gate, ...] = (INTEGRATE_GATE,)


@functools.cache
def _compiled(cond: Cond, label: str) -> tuple[re.Pattern, tuple[re.Pattern, ...]]:
    """条件を式にする。条件が不正なら ValueError。"""
    if bool(cond.regex) == bool(cond.glob):
        raise ValueError(f"{label}: regex か glob のどちらか 1 つを書いてください")
    pattern, problem = rules.compile_condition(cond.glob, cond.regex, label)
    if pattern is None:
        raise ValueError(f"{label}: {problem}")
    buts = []
    for text in cond.but:
        got, problem = rules.compile_condition("", text, label + " の but")
        if got is None:
            raise ValueError(f"{label} の but: {problem}")
        buts.append(got)
    return pattern, tuple(buts)


def _keyed(cond: Cond, label: str) -> bool:
    return KEY in _compiled(cond, label)[0].groupindex


def validate(gate: Gate) -> None:
    """宣言を組み立てられるか。不正なら ValueError（条件、キーの有無の食い違い、id）。"""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", gate.id):
        raise ValueError(f"{gate.id}: id は英数字・`.`・`_`・`-` で書いてください")
    first = _keyed(gate.first, f"{gate.id} の first")
    second = _keyed(gate.second, f"{gate.id} の second")
    if first != second:
        raise ValueError(
            f"{gate.id}: キー（名前つきグループ {KEY}）は first と second の両方に書くか、"
            "両方に書かないかのどちらかです"
        )


def _normal(raw: str) -> str:
    """キーを比べる形にする。解けない書き方は WILDCARD。"""
    return raw.casefold() if _SAFE_KEY.fullmatch(raw) else WILDCARD


def _same(a: str, b: str) -> bool:
    return WILDCARD in (a, b) or a == b


def hits(cond: Cond, label: str, texts: list[str]) -> list[tuple[str, str]]:
    """条件にヒットした箇所の (比べるキー, 書かれたキー)。キーのない条件では ("", "")。

    texts のそれぞれを `shellread.SEP` で分けたコマンドごとに見る。
    """
    pattern, buts = _compiled(cond, label)
    keyed = KEY in pattern.groupindex
    found: list[tuple[str, str]] = []
    for text in texts:
        for command in text.split(shellread.SEP):
            if not command or any(but.search(command) for but in buts):
                continue
            for match in pattern.finditer(command):
                if not keyed:
                    found.append(("", ""))
                    continue
                raw = match.group(KEY) or ""
                found.append((_normal(raw), raw))
    return list(dict.fromkeys(found))


def _safe(text: str, limit: int) -> str:
    return fsio.safe_name(text, limit)


def _digest(gate_id: str, key: str) -> str:
    return hashlib.sha1(f"{gate_id}\0{key}".encode()).hexdigest()[:10]


def _entry_name(session: str, gate_id: str, key: str, call: str) -> str:
    return (
        f"{PREFIX}{_safe(session, 64)}-{_safe(gate_id, 40)}-{_digest(gate_id, key)}"
        f"-{_safe(call, 40)}{SUFFIX}"
    )


def _session_names(state: str, session: str) -> tuple[list[str], str]:
    """このセッションのものらしい記録のファイル名と、置き場を読めなかった理由。"""
    try:
        names = os.listdir(state)
    except FileNotFoundError:
        return [], ""
    except OSError as exc:
        return [], str(exc)
    head = f"{PREFIX}{_safe(session, 64)}-"
    return sorted(n for n in names if n.startswith(head) and n.endswith(SUFFIX)), ""


def _entries(state: str, session: str) -> tuple[list[_Entry], list[str], str]:
    """このセッションの記録、読めない記録のパス、置き場を読めなかった理由。"""
    names, failed = _session_names(state, session)
    found: list[_Entry] = []
    broken: list[str] = []
    for name in names:
        path = os.path.join(state, name)
        data, bad = fsio.read_json(path)
        if bad is None and isinstance(data, dict):
            if data.get("session") != session:
                continue
            at, gate, key = data.get("at"), data.get("gate"), data.get("key")
            if (
                isinstance(at, (int, float))
                and not isinstance(at, bool)
                and isinstance(gate, str)
                and isinstance(key, str)
            ):
                found.append(_Entry(gate, key, float(at), path))
                continue
        if os.path.lexists(path):  # 読む間に消えた（B が通って消した）ものは数えない
            broken.append(path)
    return found, broken, failed


def drop_broken(state: str, session: str) -> None:
    """読めない記録を消す。ユーザの発言で呼ぶ（記録の A は、この発言より前にある）。"""
    if state and session:
        for path in _entries(state, session)[1]:
            fsio.remove(path)


def check(
    gates: tuple[Gate, ...],
    texts: list[str],
    state: str,
    session: str,
    *,
    same_command_only: bool = False,
) -> Stop | None:
    """B を打とうとしているコマンドを止めるか。止めるなら理由を返す。通すなら None。

    texts は、判定が適用するコマンドの文字列（読み直したコマンドと、実行役が中で実行するコマンド）。
    通すときは、一致した記録を消す（B が通ったので、次の A からまた数える）。

    処理フロー:
    1. 宣言ごとに B にヒットする箇所を探す。無ければ次の宣言へ。
    2. 同じコマンドに、同じキーの A もあれば止める（まだ記録が無い）。
    3. このセッションの記録を読み、同じ宣言・同じキーの記録を集める。置き場を読めない、または
       読めない記録があれば止める。
    4. 記録が無ければ通す。あれば、ユーザの発言の記録（`userprompt.last`）を読み、記録の時刻より
       後の発言が無い記録が 1 つでもあれば止める。無い・読めなければ「まだ発言が無い」と読む。
    5. 全部の記録より後に発言があれば、一致した記録を消して通す。

    same_command_only が真なら、手順 2（同じコマンドに A と B が並ぶ）だけを見る。付き添いのある
    セッションで B を確認に出す宣言（`ask_if_attended`）に使う。記録は読まない・消さない。
    """
    if not state or not session:
        return None
    for gate in gates:
        label = gate.id
        b_hits = hits(gate.second, f"{label} の second", texts)
        if not b_hits:
            continue
        a_hits = hits(gate.first, f"{label} の first", texts)
        for b_key, b_raw in b_hits:
            if any(_same(b_key, a_key) for a_key, _ in a_hits):
                return Stop(gate, SAME_COMMAND, _shown(b_raw))
        if same_command_only:
            continue
        entries, broken, failed = _entries(state, session)
        if failed:
            return Stop(gate, UNREADABLE, _shown(b_hits[0][1]), failed)
        if broken:
            return Stop(gate, BROKEN, _shown(b_hits[0][1]), broken[0])
        matched: list[tuple[_Entry, str]] = [
            (e, b_raw)
            for e in entries
            if e.gate == gate.id
            for b_key, b_raw in b_hits
            if _same(b_key, e.key)
        ]
        if not matched:
            continue
        spoken = userprompt.last(state, session)
        for entry, b_raw in matched:
            if spoken is None or spoken <= entry.at:
                return Stop(gate, NO_USER_TURN, _shown(b_raw))
        for entry, _ in matched:
            fsio.remove(entry.path)
    return None


def split_by_attendance(
    gates: tuple[Gate, ...], attended: bool
) -> tuple[tuple[Gate, ...], tuple[Gate, ...]]:
    """宣言を (発言を待つ関門で見るもの, 確認（ask）に出すもの) に分ける。

    確認に出すのは、付き添いのあるセッションで `ask_if_attended` を立てた宣言だけ。
    """
    asked = tuple(g for g in gates if attended and g.ask_if_attended)
    return tuple(g for g in gates if g not in asked), asked


def asks(gates: tuple[Gate, ...], texts: list[str]) -> Stop | None:
    """B を打とうとしているコマンドが、確認に出す宣言にヒットするか。ヒットした宣言とキーを返す。"""
    for gate in gates:
        found = hits(gate.second, f"{gate.id} の second", texts)
        if found:
            return Stop(gate, ASK, _shown(found[0][1]))
    return None


def _shown(raw: str) -> str:
    """文面に載せるキー。そのまま打てる形（解ける書き方で、長すぎない）のときだけ。"""
    return raw if len(raw) <= _SHOWN and _SAFE_KEY.fullmatch(raw) else ""


def note(
    gates: tuple[Gate, ...],
    texts: list[str],
    state: str,
    session: str,
    call: str,
    now: float | None = None,
) -> Stop | None:
    """A を許した呼び出しを記録する。書けなければ、A を止める結論を返す。

    call は呼び出しの識別子（`tool_use_id`）。空なら時刻から作る（失敗した呼び出しの記録を
    消せなくなるだけで、止める側の働きは変わらない）。
    """
    if not state or not session:
        return None
    stamp = time.time() if now is None else now
    token = call or f"t{time.time_ns()}"
    for gate in gates:
        for key, raw in hits(gate.first, f"{gate.id} の first", texts):
            try:
                os.makedirs(state, exist_ok=True)
            except OSError as exc:
                return Stop(gate, UNWRITABLE, _shown(raw), str(exc))
            failed = fsio.write_json_atomic(
                os.path.join(state, _entry_name(session, gate.id, key, token)),
                {"session": session, "gate": gate.id, "key": key, "at": stamp, "call": call},
            )
            if failed:
                return Stop(gate, UNWRITABLE, _shown(raw), failed)
    return None


def forget_call(state: str, session: str, call: str) -> None:
    """その呼び出しが残した記録を消す。A が拒否された・失敗したときに呼ぶ。

    呼び出しの識別子が空のときは何もしない（別の呼び出しの記録を消さないため）。
    """
    if not state or not session or not call:
        return
    names, _ = _session_names(state, session)
    tail = f"-{_safe(call, 40)}{SUFFIX}"
    for name in names:
        if name.endswith(tail):
            fsio.remove(os.path.join(state, name))


def clear(state: str, session: str) -> None:
    """このセッションの記録をすべて消す。起動と再開で呼ぶ。"""
    if not state or not session:
        return
    names, _ = _session_names(state, session)
    for name in names:
        fsio.remove(os.path.join(state, name))


def ask_message(stop: Stop, root: str) -> str:
    """確認ダイアログに添える文。文は `Gate.ask_message` の識別子が持つ。"""
    gate = stop.gate
    where = f"（宣言 {gate.id}" + (f"、キー {stop.key}" if stop.key else "") + "）"
    values = {
        "first_name": gate.first_name,
        "second_name": gate.second_name,
        "where": where,
        "key": stop.key or gate.key_name,
    }
    return texts.say(root, gate.ask_message, values)


def message(stop: Stop, root: str) -> str:
    """止めた文。何が起きたか・なぜ・次の一手・迂回しない、の順に 1 行ずつ。

    文は文面ファイルが持つ。宣言の文面（`Gate.message`）は止めた理由が「同じコマンド」か「発言が無い」
    のとき、記録を読み書きできないときは `DENY_NEEDS_USER_TURN__STATE`。
    """
    gate = stop.gate
    key = stop.key
    where = f"（宣言 {gate.id}" + (f"、キー {key}" if key else "") + "）"
    values = {
        "first_name": gate.first_name,
        "second_name": gate.second_name,
        "where": where,
    }
    if stop.reason in (UNREADABLE, UNWRITABLE, BROKEN):
        values["detail"] = stop.detail
        cases = {"what": stop.reason, "next": "broken" if stop.reason == BROKEN else "place"}
        return texts.say(root, "DENY_NEEDS_USER_TURN__STATE", values, cases)
    values["key"] = key or gate.key_name
    case = "same_command" if stop.reason == SAME_COMMAND else "no_user_turn"
    return texts.say(root, gate.message, values, {"what": case, "next": case})
