"""同じ理由で繰り返し止めたことを数える（issue #149 の 3）。

エージェントは止められると、言い回しを少し変えて同じことを打ち直すことがある。
文面が次の一手を伝えていないか、そもそもユーザに聞くべき場面かのどちらかで、
どちらでも打ち直しを重ねても前へ進まない。そこで、同じルールが同じ呼び出しを
止めた回数をセッションごとに数え、N 回目から拒否の文面に「言い換えずに相談する」
一文を足す。ターンの終わりにはユーザへの報告にも載せる。

**判定は変えない。** ここが足すのは文面だけで、止めた呼び出しは止めたまま。
数えられなかった（記録を読めない・書けない）ときは何も足さず、何も言わない。数えの失敗が
判定に影響する経路は作らない。

数える鍵は（判定を下したルールの id, 対象を均した文字列のハッシュ）。均し方は控えめで、

- 引用符（`'` `"` `` ` ``）を落とす
- 連続する空白（改行・タブを含む）を 1 つの空白にし、前後を落とす

の 2 つだけ。大文字と小文字は分けたまま（パスもコマンドも大小を区別する環境がある）。
引用の付け外しと空白の置き方だけを変えた打ち直しを、同じ呼び出しとして数えるため。
記録にはハッシュだけを置き、対象の文字列そのものは置かない（記録に認証情報を残さない）。

記録はセッションごとに state の置き場の `denied-<セッション>.json` 1 本。実行前チェックの
期限（3 秒）の中で走るので、止めた回に 1 回読んで 1 回書くだけにする。
"""

from __future__ import annotations

import hashlib
import os

from . import audit, fsio

# N の既定。環境変数 CCNAVI_DENY_REPEAT で動かせる（settings.DENY_REPEAT_ENV）。
DEFAULT_THRESHOLD = 3

# 均すときに落とす引用符。
_QUOTES = str.maketrans("", "", "'\"`")


def threshold(written: str) -> int:
    """書かれた N。読めない値と 2 未満は既定に戻す。

    1 は「止めるたびに相談せよ」になり、1 回目の拒否の文面が案内する代わりの方法を
    試す前に止まる。0 以下は数える意味が無い。どちらも何も出さずに既定に戻す。
    """
    try:
        n = int(written.strip())
    except ValueError:
        return DEFAULT_THRESHOLD
    return n if n >= 2 else DEFAULT_THRESHOLD


def normalize(subject: str) -> str:
    """同じ呼び出しとして数えるための均し。モジュールの説明を参照。"""
    return " ".join(subject.translate(_QUOTES).split())


def rule_of(record: audit.Record) -> str:
    """止めた理由の名前。判定を下したルールの id、無ければ根拠の種別。"""
    if record.rules:
        return record.rules[0]
    return record.code or "(deny)"


def key_of(rule: str, subject: str) -> str:
    """記録の鍵。ルールの id と、均した対象のハッシュ。"""
    digest = hashlib.sha256(normalize(subject).encode("utf-8")).hexdigest()[:16]
    return f"{rule} {digest}"


def _path(state_dir: str, session: str) -> str:
    return os.path.join(state_dir, f"denied-{fsio.safe_name(session)}.json")


def count(state_dir: str, record: audit.Record) -> int:
    """この拒否を数えて、同じ鍵のこれまでの回数（今回を含む）を返す。数えられなければ 0。

    state の置き場が無い（`--state ""`）ときも 0。試験（`--test`）はこれに当たるので、
    試し打ちが本番の数えを進めない。payload にセッションが無いときも数えない。
    セッションを見分けられないまま 1 つの記録にまとめると、別のセッションの拒否まで
    合わせて数え、打ち直していない相手に「言い換えるな」と言うことになる。
    """
    if not state_dir or not record.subject or not record.session:
        return 0
    try:
        path = _path(state_dir, record.session)
        data = fsio.read_dict(path) or {}
        rule = rule_of(record)
        key = key_of(rule, record.subject)
        entry = data.get(key)
        if not isinstance(entry, dict):
            entry = {"rule": rule, "tool": record.tool, "count": 0, "told": 0}
        n = int(entry.get("count") or 0) + 1
        entry["count"] = n
        data[key] = entry
        if fsio.write_json_atomic(path, data):
            return 0
        return n
    except Exception:  # noqa: BLE001  数えの失敗で判定を落とさない
        return 0


def note(rule: str, n: int) -> str:
    """N 回目から拒否の文面の末尾に足す一文。"""
    return (
        f"[ccnavi] 同じ理由（{rule}）でこの呼び出しを {n} 回止めました。"
        "言い換えて打ち直さず、何をしたいのかと止まった理由をユーザに伝えて相談してください。"
    )


def at_stop(state_dir: str, session: str, n: int) -> str:
    """ターンの終わりにユーザへ言うこと。N 回に達した鍵のうち、まだ言っていない回数のもの。

    言った回数は記録に残し、同じ回数のまま次のターンで繰り返さない。書けなければ
    言わない（言ったと覚えられないまま言うと、ターンの終わりのたびに同じ行が並ぶ）。
    """
    if not state_dir or not session:
        return ""
    try:
        path = _path(state_dir, session)
        data = fsio.read_dict(path)
        if not data:
            return ""
        lines = []
        for entry in data.values():
            if not isinstance(entry, dict):
                continue
            got = int(entry.get("count") or 0)
            if got < n or got <= int(entry.get("told") or 0):
                continue
            lines.append(
                f"  {entry.get('rule') or '(deny)'}（{entry.get('tool') or '?'}）: {got} 回"
            )
            entry["told"] = got
        if not lines or fsio.write_json_atomic(path, data):
            return ""
    except Exception:  # noqa: BLE001  報告の失敗でターンの終わりを落とさない
        return ""
    return "\n".join(
        [
            "[ccnavi] このセッションで、同じ理由で同じ呼び出しを繰り返し止めました。"
            "言い換えて同じ呼び出しを繰り返していないか、ユーザの判断が要る場面ではないかを"
            "確かめてください。",
            *sorted(lines),
        ]
    )
