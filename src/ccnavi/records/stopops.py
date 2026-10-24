"""propose の操作実績。どの提案を送ったか、送らなかったかを、どの経路を通っても確定する。

3 つの記録をつなぐ。

- 実行前（PreToolUse）: `ccnavi-ticket.sh propose <識別子>` を読んだら、`tool_use_id`・セッション・
  識別子・時刻を登録する（`register`）
- 結果ファイル: `ccnavi-ticket.sh` が出口で、どの経路を通っても 1 つ書く
  （`stopops-result/<開始秒>-<pid>.kv`）。標準出力の識別行は、出力が切られると消えるので頼らない
- 実行後（PostToolUse・PostToolUseFailure）: `tool_use_id` と、識別子・時刻の範囲で結果ファイルを
  結び付ける（`bind`）。エラーの中身は使わない。バックグラウンドの実行などで結び付けられなかったものは、
  Stop と集計のときに時刻の範囲で結び付け直し（`rebind`）、それでも結べなければ「未連携」にする

判定の対象は提案ごとの最終の有効版（`final_versions`）。途中の版は記録に残る。

経路（`route`）は 7 つ: 送った・変更なし・書くだけ（origin なし）・止めた・コードが未 push・
送る前に失敗・push 後の後処理で失敗。

保存先は state と同じ場所。ターンの開始時刻（`start_turn`）もここに置く（通知でない発言の時刻）。
"""

from __future__ import annotations

import os
from typing import Any

from ..infra import fsio, pathcase

RESULT_DIR = "stopops-result"

SENT = "sent"
UNCHANGED = "unchanged"
WRITE_ONLY = "write_only"
STOPPED = "stopped"
UNPUSHED_CODE = "unpushed_code"
FAILED_BEFORE = "failed_before"
FAILED_AFTER = "failed_after"
ROUTES = frozenset(
    {SENT, UNCHANGED, WRITE_ONLY, STOPPED, UNPUSHED_CODE, FAILED_BEFORE, FAILED_AFTER}
)
# 提案の最終の有効版になる経路。変更なしは、前に送った版がそのまま有効のままになる。
EFFECTIVE = frozenset({SENT, UNCHANGED})

UNLINKED = "unlinked"
SLACK_SECONDS = 2.0  # 時計の粗さ（sh は秒単位）と実行の前後の隙間
UNLINK_AFTER_SECONDS = 30.0  # 結び付け直しても見つからないまま、この秒数を過ぎたら未連携

_SCRIPT = "ccnavi-ticket.sh"
_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh"})


def _fold(path: str) -> str:
    """パスを比べるためのキー。区切りは `/` にそろえ、大文字小文字は区別しない。"""
    return pathcase.fold(path).replace("\\", "/")


_WRAPPERS = frozenset(
    {"env", "nohup", "time", "timeout", "sudo", "uv", "run", "nice", "command", "exec", "stdbuf"}
)


def _runs(before: list[str]) -> bool:
    """スクリプトの前に並ぶ語が、実行の前置き（シェル・`VAR=値`・env・timeout・オプション）だけか。"""
    for word in before:
        name = os.path.basename(_fold(word))
        plain = word.lstrip("-")
        if name in _SHELLS or name in _WRAPPERS or word.startswith("-") or plain.isdigit():
            continue
        if "=" in word and "/" not in word.split("=", 1)[0]:
            continue
        if word.replace(".", "", 1).isdigit():
            continue
        return False
    return True


def propose_ident(words: list[str]) -> str:
    """コマンドの語から、`ccnavi-ticket.sh propose <識別子>` の識別子を取る。無ければ空文字。

    識別子は `--` と `--reason <値>` を読み飛ばした最初の語（sh の `ticket_id` と同じ読み方）。
    """
    for at, word in enumerate(words):
        if os.path.basename(_fold(word)) != _SCRIPT:
            continue
        # 実行される位置だけ。`echo …` や `grep …` の引数の中は数えない。
        if not _runs(words[:at]):
            continue
        rest = words[at + 1 :]
        if not rest or rest[0] != "propose":
            continue
        skip = False
        for arg in rest[1:]:
            if skip:
                skip = False
            elif arg == "--reason":
                skip = True
            elif arg.startswith("-") and len(arg) > 1:
                continue
            else:
                return arg
        return ""
    return ""


def _names(directory: str) -> list[str]:
    """ディレクトリの中の名前。無ければ空（結果ファイルの置き場は、最初の propose まで無い）。"""
    try:
        return sorted(fsio.listdir(directory)) if directory else []
    except OSError:
        return []


def _call(tool_use_id: str, ident: str) -> str:
    """1 つの Bash に propose が複数あっても、それぞれ別の呼び出しとして扱うためのキー。"""
    return f"{tool_use_id}~{ident.casefold()}"


def _pre_path(state: str, tool_use_id: str, ident: str) -> str:
    return os.path.join(state, f"stopops-pre-{fsio.safe_name(_call(tool_use_id, ident))}.json")


def _op_path(state: str, tool_use_id: str, ident: str) -> str:
    return os.path.join(state, f"stopops-op-{fsio.safe_name(_call(tool_use_id, ident))}.json")


def _refs_path(state: str, session: str) -> str:
    return os.path.join(state, f"stoprefs-{fsio.safe_name(session)}.json")


def _turn_path(state: str, session: str) -> str:
    return os.path.join(state, f"stopturn-{fsio.safe_name(session)}.json")


def start_turn(state: str, session: str, now: float) -> None:
    """通知でない発言のとき、ターンの開始時刻を書く。"""
    if state and session:
        fsio.write_json_atomic(_turn_path(state, session), {"t": now})


def turn_start(state: str, session: str) -> float:
    """ターンの開始時刻。無ければ 0（セッションの最初から数える）。"""
    data = fsio.read_dict(_turn_path(state, session)) if state and session else None
    value = (data or {}).get("t", 0)
    return float(value) if isinstance(value, (int, float)) else 0.0


def register(state: str, session: str, tool_use_id: str, ident: str, now: float) -> None:
    """実行前に、propose の呼び出しを登録する。"""
    if not (state and session and tool_use_id and ident):
        return
    fsio.write_json_atomic(
        _pre_path(state, tool_use_id, ident),
        {"tool_use_id": tool_use_id, "session": session, "ident": ident, "t0": now, "bound": ""},
    )


def read_result(path: str) -> dict[str, Any] | None:
    """結果ファイル（key=value の行）を読む。読めない・route が不明なら None。"""
    text = fsio.read_text(path, errors="replace")
    if text is None:
        return None
    result: dict[str, Any] = {"files": []}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if not sep:
            continue
        if key == "file":
            sha, _, name = value.partition(" ")
            if sha and name:
                result["files"].append({"sha": sha, "path": name})
        else:
            result[key] = value
    if result.get("route") not in ROUTES or not result.get("id"):
        return None
    for key in ("started", "ended", "rc"):
        try:
            result[key] = float(result.get(key, 0))
        except ValueError:
            result[key] = 0.0
    result["file"] = path
    return result


def _results(state: str) -> list[dict[str, Any]]:
    found = []
    directory = os.path.join(state, RESULT_DIR)
    for name in _names(directory):
        if name.endswith(".kv"):
            result = read_result(os.path.join(directory, name))
            if result:
                found.append(result)
    return found


def _bound_files(state: str) -> set[str]:
    bound = set()
    for name in _names(state):
        if name.startswith("stopops-op-") and name.endswith(".json"):
            op = fsio.read_dict(os.path.join(state, name))
            if op and op.get("file"):
                bound.add(op["file"])
    return bound


def bind(state: str, tool_use_id: str, now: float) -> dict[str, Any] | None:
    """実行後に、結果ファイルを `tool_use_id` の呼び出しへ結び付ける。最後に結べた操作を返す。

    1 つの Bash に propose が複数あれば、登録は識別子ごとにある。それぞれ結ぶ。
    """
    bound = None
    if state and tool_use_id:
        for name in _names(state):
            if name.startswith("stopops-pre-") and name.endswith(".json"):
                pre = fsio.read_dict(os.path.join(state, name))
                if pre and pre.get("tool_use_id") == tool_use_id:
                    bound = _bind_one(state, pre, now) or bound
    return bound


def _bind_one(state: str, pre: dict[str, Any], now: float) -> dict[str, Any] | None:
    """1 件の登録に、結果ファイルを結ぶ。

    識別子が合い、開始が登録の時刻より後で、終了が今より前（どちらも粗さの分を許す）、まだ
    どの呼び出しにも結ばれていない結果ファイルのうち、最も新しいもの。
    """
    if pre.get("bound"):
        return None
    taken = _bound_files(state)
    lower, upper = float(pre["t0"]) - SLACK_SECONDS, now + SLACK_SECONDS
    fit = [
        r
        for r in _results(state)
        if r["id"] == pre["ident"]
        and r["file"] not in taken
        and r["started"] >= lower
        and r["ended"] <= upper
    ]
    if not fit:
        return None
    result = max(fit, key=lambda r: r["ended"])
    tool_use_id, ident = pre["tool_use_id"], pre["ident"]
    op = dict(result, tool_use_id=tool_use_id, session=pre["session"], ident=ident)
    fsio.write_json_atomic(_op_path(state, tool_use_id, ident), op)
    pre["bound"] = result["file"]
    fsio.write_json_atomic(_pre_path(state, tool_use_id, ident), pre)
    return op


def rebind(state: str, session: str, now: float) -> None:
    """結び付いていない登録を、時刻の範囲で結び直す。見つからないまま古くなったものは未連携にする。"""
    for name in _names(state):
        if not (name.startswith("stopops-pre-") and name.endswith(".json")):
            continue
        pre = fsio.read_dict(os.path.join(state, name))
        if not pre or pre.get("session") != session or pre.get("bound"):
            continue
        if _bind_one(state, pre, now):
            continue
        if now - float(pre["t0"]) > UNLINK_AFTER_SECONDS:
            pre["bound"] = UNLINKED
            fsio.write_json_atomic(os.path.join(state, name), pre)


def unlinked(state: str, session: str) -> int:
    """未連携の呼び出しの数。集計で数える。"""
    count = 0
    for name in _names(state):
        if name.startswith("stopops-pre-") and name.endswith(".json"):
            pre = fsio.read_dict(os.path.join(state, name))
            if pre and pre.get("session") == session and pre.get("bound") == UNLINKED:
                count += 1
    return count


def turn_ops(state: str, session: str, since: float) -> list[dict[str, Any]]:
    """このターン（`since` 以降に始まった呼び出し）の操作実績。終了の古い順。"""
    ops = []
    for name in _names(state):
        if not (name.startswith("stopops-op-") and name.endswith(".json")):
            continue
        op = fsio.read_dict(os.path.join(state, name))
        if op and op.get("session") == session and float(op.get("started", 0)) >= since - 1:
            ops.append(op)
    return sorted(ops, key=lambda o: (o["ended"], o["tool_use_id"]))


def final_versions(ops: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """提案ごとの最終の有効版。識別子 → その版の操作。

    有効なのは送った版。変更なしは、直前の有効版のままなので版を動かさない。有効な版が 1 つも無い
    提案は、最後の操作（送れなかった理由）を返す。途中の版は、各操作の記録に残っている。
    """
    latest: dict[str, dict[str, Any]] = {}
    for op in ops:
        ident = op["ident"]
        if op["route"] == UNCHANGED and ident in latest and latest[ident]["route"] in EFFECTIVE:
            continue
        if op["route"] in EFFECTIVE or latest.get(ident, {}).get("route") not in EFFECTIVE:
            latest[ident] = op
    return latest


def target_key(op: dict[str, Any]) -> str:
    """判定の対象キー。識別子と、版（送った先頭、無ければ道）から決める。"""
    version = str(op.get("commit") or op["route"])[:12]
    return f"proposal:{op['ident']}:{version}"


# --- 未送信の提案 --------------------------------------------------------------

UNSENT = "unsent"


def written_ident(file_path: str, tickets_dir: str) -> str:
    """書き込んだパスが `<提案の置き場>/todo/<識別子>.md` なら識別子。違えば空文字。

    大文字小文字は区別しない。置き場は設定の相対パス（既定 `wip/proposals`）。
    """
    path = _fold(file_path)
    marker = f"/{_fold(tickets_dir).strip('/')}/todo/"
    at = path.rfind(marker)
    if at < 0 or len(path) != len(file_path):
        return ""
    name = path[at + len(marker) :]
    if "/" in name or not name.endswith(".md") or len(name) <= 3:
        return ""
    return file_path.replace("\\", "/")[at + len(marker) : -3]


def _write_path(state: str, session: str, ident: str) -> str:
    return os.path.join(
        state, f"stopwrite-{fsio.safe_name(session)}-{fsio.safe_name(ident.casefold())}.json"
    )


def note_written(state: str, session: str, ident: str, path: str, now: float) -> None:
    """提案を `todo/` へ書いた（Write・Edit）ことを、書いた時刻つきで残す。"""
    if state and session and ident:
        fsio.write_json_atomic(
            _write_path(state, session, ident),
            {"session": session, "ident": ident, "path": path, "t": now},
        )


def turn_writes(state: str, session: str, since: float) -> list[dict[str, Any]]:
    """このターンに書いた提案。"""
    found = []
    prefix = f"stopwrite-{fsio.safe_name(session)}-"
    for name in _names(state):
        if name.startswith(prefix) and name.endswith(".json"):
            row = fsio.read_dict(os.path.join(state, name))
            if row and float(row.get("t", 0)) >= since:
                found.append(row)
    return sorted(found, key=lambda r: (r["t"], r["ident"]))


def unsent_writes(writes: list[dict[str, Any]], ops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """書いたのに、書いた後に送った操作が無い提案。

    送ったかは、送った操作の `files` に `todo/<識別子>.md` があり、その終了が書いた時刻より
    後（粗さの分を許す）かで決める。親の propose は子の提案も一緒に送るので、識別子では決めない。
    """
    sent: dict[str, float] = {}
    for op in ops:
        if op["route"] != SENT:
            continue
        for item in op.get("files") or []:
            tail = _fold(str(item.get("path", ""))).rsplit("/todo/", 1)[-1]
            sent[tail] = max(sent.get(tail, 0.0), float(op["ended"]))
    unsent = []
    for row in writes:
        tail = f"{row['ident']}.md".casefold()
        if sent.get(tail, -1.0) < float(row["t"]) - SLACK_SECONDS:
            unsent.append(row)
    return unsent


def unsent_key(ident: str) -> str:
    return f"proposal:{ident}:{UNSENT}"


# --- issue・MR の指定 -------------------------------------------------------------


def note_refs(state: str, session: str, labels: list[str], now: float) -> None:
    """依頼に issue・MR の指定があれば、ラベルを時刻つきで足す（セッションの間は残す）。"""
    if not (state and session and labels):
        return
    row = fsio.read_dict(_refs_path(state, session)) or {"refs": []}
    kept = [r for r in row["refs"] if r.get("label") not in labels][-19:]
    kept += [{"label": label, "t": now} for label in labels]
    fsio.write_json_atomic(_refs_path(state, session), {"refs": kept})


def session_refs(state: str, session: str) -> list[str]:
    """このセッションで指定された issue・MR のラベル（古い順）。"""
    row = fsio.read_dict(_refs_path(state, session)) if state and session else None
    return [str(r["label"]) for r in (row or {}).get("refs", []) if r.get("label")]
