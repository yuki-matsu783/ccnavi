"""操作記録。ユーザの判断と外部への書き込みを、実行する前に保存する。

decide・close-early・request・comment・ready は、ホストへの返信・解決・投稿とローカルへの適用が
複数の段階に分かれ、途中で止まりうる。止まったあとに「どこまで済んだか」を同じ記録から読み、
打ち直しや `--resume` で続きから進める。

置き場は `logs/state/ops/<操作ID>.json`。git の外に置き、エージェントの直接の書き込みは
チケットゲート（`ticket_guard.gate_rules`）が止める。実行ファイルを直接呼ぶ形は
`ccnavi ops …` で、状態の操作としてエージェントには打たせない（`user_only.SUBS`）。

記録に持つのは構造化した中身だけで、送る本文は持たない（`shown`・`choices` から送るたびに作り直す。
改ざんしにくくするため）。保存できなければ呼び手は何もしない（返信・解決にも進まない）。

ネットワークにも git にも触れない。ロックは、同じ PC の並行実行を防ぐだけ（別の PC は照合で防ぐ）。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import time
import uuid
from typing import Any

from ..infra import fsio

VERSION = 1
OPS_DIR = "ops"

# 操作の種類。
KIND_DECIDE = "decide"
KIND_CLOSE_EARLY = "close-early"
KIND_REQUEST = "request"
KIND_COMMENT = "comment"
KIND_READY = "ready"
KINDS = (KIND_DECIDE, KIND_CLOSE_EARLY, KIND_REQUEST, KIND_COMMENT, KIND_READY)

# 送信の段階の状態。
PENDING = "pending"  # まだ送っていない（または送ったか確かめていない）
SENT = "sent"  # 送ったと確かめた
UNKNOWN = "unknown"  # 送ったか確定できない。自動では送り直さない
STATES = (PENDING, SENT, UNKNOWN)

# 段階の種類。
STAGE_REPLY = "reply"  # スレッドへの返信・解決
STAGE_ISSUE = "issue"
STAGE_COMMENT = "comment"
STAGE_MR = "mr"
STAGE_PUSH = "push"
STAGE_DRAFT = "draft"
STAGE_KINDS = (STAGE_REPLY, STAGE_ISSUE, STAGE_COMMENT, STAGE_MR, STAGE_PUSH, STAGE_DRAFT)

# 同じ PC の並行実行を防ぐロックの古さの上限（秒）。これより古いロックは、
# 落ちたプロセスの残りとして取り直す。
LOCK_STALE_SECONDS = 120.0
# 記録の読み書きの短いロック（`_rmw`）を待つ長さと、古いとみなす長さ（秒）。
RMW_WAIT_SECONDS = 5.0
RMW_STALE_SECONDS = 30.0


class OpError(Exception):
    """記録を読み書きできない、または形が合わない。"""


def ops_dir(state: str) -> str:
    return os.path.join(state, OPS_DIR)


def record_path(state: str, op_id: str) -> str:
    return os.path.join(ops_dir(state), f"{op_id}.json")


def valid_id(op_id: str) -> bool:
    """操作IDの形（`uuid4().hex` の 32 桁）。パスの一部になるので、形を決めて外を断る。"""
    return len(op_id) == 32 and all(c in "0123456789abcdef" for c in op_id)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def new_id() -> str:
    return uuid.uuid4().hex


def id_for(*parts: str) -> str:
    """内容から決まる操作ID（32 桁の 16 進）。

    エージェントの操作（request・comment・ready）は、同じ操作を打ち直して復旧する。打ち直しが
    同じ記録に当たるよう、操作の鍵（種類・親・MR・本文の鍵など）から ID を決める。
    """
    joined = "\x1f".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:32]


def _stage(stage_id: str, kind: str) -> dict[str, Any]:
    return {
        "id": stage_id,
        "kind": kind,
        "state": PENDING,
        "url": "",
        "account": "",
        "tries": 0,
        "at": "",
    }


def begin(
    state: str,
    kind: str,
    *,
    repo: dict[str, str],
    parent: str = "",
    phase: int = 0,
    mr: int = 0,
    request_head: str = "",
    shown: list[dict[str, Any]] | None = None,
    choices: dict[str, str] | None = None,
    plan: dict[str, Any] | None = None,
    stages: list[tuple[str, str]] | None = None,
    op_id: str = "",
) -> str:
    """操作を始める記録を保存して、操作ID を返す。保存できなければ OpError。

    op_id を渡すと、その ID で作る（復旧で同じ ID を使い回す）。既にあれば上書きしない。
    stages は（段階ID, 種類）を送る順に並べたもの。
    """
    if kind not in KINDS:
        raise OpError(f"知らない操作の種類: {kind}")
    op_id = op_id or new_id()
    if not valid_id(op_id):
        raise OpError(f"操作IDの形が不正: {op_id!r}")
    path = record_path(state, op_id)
    if os.path.exists(path):
        _add_stages(state, op_id, stages or [])
        return op_id
    now = _now()
    record = {
        "version": VERSION,
        "id": op_id,
        "kind": kind,
        "repo": repo,
        "parent": parent,
        "phase": phase,
        "mr": mr,
        "request_head": request_head,
        "shown": shown or [],
        "choices": choices or {},
        "plan": plan or {},
        "applied": {},
        "stages": [_stage(sid, skind) for sid, skind in (stages or [])],
        "created_at": now,
        "updated_at": now,
    }
    _save(state, record)
    return op_id


def _add_stages(state: str, op_id: str, stages: list[tuple[str, str]]) -> None:
    """既にある記録に、まだ無い段階だけを足す（単発の投稿が段階を順に足していく）。"""
    if not stages:
        return
    with _rmw(state, op_id):
        record = load(state, op_id)
        if record is None:
            return
        have = {s.get("id") for s in record["stages"]}
        new = [_stage(sid, skind) for sid, skind in stages if sid not in have]
        if new:
            record["stages"].extend(new)
            _save(state, record)


def _save(state: str, record: dict[str, Any]) -> None:
    os.makedirs(ops_dir(state), exist_ok=True)
    record["updated_at"] = _now()
    text = json.dumps(record, ensure_ascii=False, indent=1) + "\n"
    failed = fsio.write_text_atomic(record_path(state, record["id"]), text, newline="\n")
    if failed:
        raise OpError(f"操作記録を保存できない（{failed}）")


def load(state: str, op_id: str) -> dict[str, Any] | None:
    """記録を読む。無ければ None。壊れていれば OpError。"""
    if not valid_id(op_id):
        raise OpError(f"操作IDの形が不正: {op_id!r}")
    text = fsio.read_text(record_path(state, op_id))
    if text is None:
        return None
    try:
        record = json.loads(text)
    except ValueError as exc:
        raise OpError(f"操作記録 {op_id} を読めない（JSON ではない）") from exc
    if (
        not isinstance(record, dict)
        or record.get("version") != VERSION
        or record.get("id") != op_id
        or not isinstance(record.get("stages"), list)
    ):
        raise OpError(f"操作記録 {op_id} の形が合わない")
    return record


@contextlib.contextmanager
def locked(state: str, op_id: str):
    """操作ID ごとのロック。同じ PC の並行実行を、待たずに断る（取れなければ OpError）。"""
    if not valid_id(op_id):
        raise OpError(f"操作IDの形が不正: {op_id!r}")
    os.makedirs(ops_dir(state), exist_ok=True)
    path = os.path.join(ops_dir(state), f"{op_id}.lock")
    fd = -1
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:
                age = time.time() - os.path.getmtime(path)
            except OSError:
                continue
            if age < LOCK_STALE_SECONDS:
                raise OpError("同じ操作が実行中です。待ってから打ち直してください") from None
            with contextlib.suppress(OSError):
                os.remove(path)
    if fd < 0:
        raise OpError("操作のロックを取れない")
    try:
        os.write(fd, str(os.getpid()).encode("ascii"))
        yield
    finally:
        os.close(fd)
        with contextlib.suppress(OSError):
            os.remove(path)


@contextlib.contextmanager
def _rmw(state: str, op_id: str):
    """記録の読み書きの間だけ持つ短いロック。並行する更新が互いの書き込みを失わせないため。

    `locked`（操作の実行中を断るロック）とは別で、数秒待つ。取れなければ OpError。
    """
    if not valid_id(op_id):
        raise OpError(f"操作IDの形が不正: {op_id!r}")
    os.makedirs(ops_dir(state), exist_ok=True)
    path = os.path.join(ops_dir(state), f"{op_id}.rmw")
    deadline = time.time() + RMW_WAIT_SECONDS
    fd = -1
    while fd < 0:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            # 待つ上限は先に見る（消せない・読めないときに、上限を飛ばして回り続けない）
            if time.time() > deadline:
                raise OpError("操作記録を更新中の別のプロセスがある。打ち直してください") from None
            try:
                stale = time.time() - os.path.getmtime(path) > RMW_STALE_SECONDS
            except OSError:
                stale = False
            if stale:
                with contextlib.suppress(OSError):
                    os.remove(path)
            else:
                time.sleep(0.02)
    try:
        yield
    finally:
        os.close(fd)
        with contextlib.suppress(OSError):
            os.remove(path)


def stage_set(
    state: str,
    op_id: str,
    stage_id: str,
    new_state: str,
    url: str = "",
    account: str = "",
) -> None:
    """段階の状態を書く。sent に進めるときは URL と送ったアカウントを残す。

    sent を戻さない（sent の段階は繰り返さない）。無い段階は OpError。
    """
    with _rmw(state, op_id):
        _stage_set(state, op_id, stage_id, new_state, url, account)


def _stage_set(
    state: str,
    op_id: str,
    stage_id: str,
    new_state: str,
    url: str,
    account: str,
) -> None:
    if new_state not in STATES:
        raise OpError(f"知らない段階の状態: {new_state}")
    record = load(state, op_id)
    if record is None:
        raise OpError(f"操作記録 {op_id} が無い")
    for stage in record["stages"]:
        if stage.get("id") != stage_id:
            continue
        if stage.get("state") == SENT and new_state != SENT:
            raise OpError(f"段階 {stage_id} は送信済み。状態を戻せない")
        if new_state != stage.get("state") or new_state == PENDING:
            stage["tries"] = int(stage.get("tries") or 0) + (1 if new_state != SENT else 0)
        stage["state"] = new_state
        stage["at"] = _now()
        if url:
            stage["url"] = url
        if account:
            stage["account"] = account
        _save(state, record)
        return
    raise OpError(f"操作記録 {op_id} に段階 {stage_id} が無い")


# 照合の結果。
V_SENT = "sent"  # 送ったと確かめた（URL を保存する）
V_SEND = "send"  # 送っていないと確定した（同じ段階IDで送る）
V_UNKNOWN = "unknown"  # 送ったか確定できない（自動では送らない）
V_STOP = "stop"  # 複数ある、読めない（止めて、確かめ方を表示する）

# 作成直後は一覧に反映されるのが遅れうる。この秒数のあいだは、0 件でも未送信と確定しない。
RECENT_SECONDS = 120.0


def _parse_time(text: str) -> float | None:
    try:
        return time.mktime(time.strptime(text[:19], "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, OverflowError):
        return None


def reconcile(
    record: dict[str, Any],
    stage_id: str,
    found: list[str],
    complete: bool | None,
    account: str,
    now: float | None = None,
) -> tuple[str, str]:
    """送信の照合。ホストの一覧から目印で探した結果（found）と、段階の記録から結論を出す。

    found は、目印（`ccnavi:op=<操作ID> stage=<段階ID>`）が一致し、送ったアカウントの作成だった
    ものの URL。complete は一覧を上限で打ち切らずに最後まで読めたか（読めなかった・失敗は None）。
    account は今のトークンの持ち主。返すのは（結論, 理由）。

    - 1 件だけ: 送った（V_SENT）
    - 複数: 止める（V_STOP）
    - 0 件で、最後まで読めて、送ったアカウントと同じで、作成直後でない: 送っていないと確定（V_SEND）
    - 0 件で、打ち切った・作成直後・アカウントが違う: 確定できない（V_UNKNOWN）
    - 一覧を読めない: 止める（V_STOP）
    """
    stage = next((s for s in record["stages"] if s.get("id") == stage_id), None)
    if stage is None:
        return V_STOP, f"段階 {stage_id} が記録に無い"
    if complete is None:
        return V_STOP, "ホストの一覧を読めなかった"
    if len(found) > 1:
        return V_STOP, f"目印の一致が {len(found)} 件ある（重複して送られている）"
    if len(found) == 1:
        return V_SENT, ""
    sent_by = str(stage.get("account") or "")
    if sent_by and account and sent_by != account:
        return V_UNKNOWN, f"送ったアカウント（{sent_by}）と今のトークンの持ち主（{account}）が違う"
    if not complete:
        return V_UNKNOWN, "一覧を上限で打ち切ったので、0 件とは言い切れない"
    at = _parse_time(str(stage.get("at") or ""))
    clock = time.time() if now is None else now
    if stage.get("tries") and at is not None and clock - at < RECENT_SECONDS:
        return V_UNKNOWN, "送ってから間がなく、ホストの一覧に反映されていないかもしれない"
    return V_SEND, ""


def applied_set(state: str, op_id: str, key: str, value: Any) -> None:
    """ローカルに適用したものを残す（子・マーカー・台帳の項目・push した先頭など）。"""
    with _rmw(state, op_id):
        record = load(state, op_id)
        if record is None:
            raise OpError(f"操作記録 {op_id} が無い")
        record["applied"][key] = value
        _save(state, record)


def unfinished(record: dict[str, Any]) -> list[dict[str, Any]]:
    """送り終えていない段階（sent でないもの）。"""
    return [s for s in record["stages"] if s.get("state") != SENT]


def finished(record: dict[str, Any]) -> bool:
    return not unfinished(record)


def remove(state: str, op_id: str) -> None:
    """記録を消す。呼び手が `finished` を確かめたあとだけ呼ぶ。"""
    fsio.remove(record_path(state, op_id))


def retire_parent(state: str, parent: str) -> list[str]:
    """親の記録のうち、すべての段階が送り終わったものを消し、消した操作ID を返す。

    親を閉じる片付け（ready）が呼ぶ。未完了の記録と、読めない記録は消さない。
    """
    gone = []
    if not parent:
        # 親が空だと、親を持たない記録を全部消してしまう
        return gone
    for op_id in all_ids(state):
        try:
            record = load(state, op_id)
        except OpError:
            continue
        if record is not None and record.get("parent") == parent and finished(record):
            remove(state, op_id)
            gone.append(op_id)
    return gone


def all_ids(state: str) -> list[str]:
    """置いてある記録の操作ID（古い順）。"""
    path = ops_dir(state)
    try:
        names = os.listdir(path)
    except OSError:
        return []
    found = []
    for name in names:
        stem, ext = os.path.splitext(name)
        if ext == ".json" and valid_id(stem):
            try:
                found.append((os.path.getmtime(os.path.join(path, name)), stem))
            except OSError:
                continue
    return [stem for _, stem in sorted(found)]


def untouched(record: dict[str, Any]) -> bool:
    """何も起きていない記録（どの段階も送っておらず、ローカルにも何も適用していない）。"""
    return not record.get("applied") and all(s.get("state") == PENDING for s in record["stages"])


def discard_untouched(state: str, kind: str, parent: str, phase: int, keep: str = "") -> list[str]:
    """同じ対象の、何も起きていない古い記録を消す。消した操作ID を返す。

    判断をやり直したとき（前の判断が返信の前で止まった）に、使われない記録が未完了として
    残り続けないようにする。何かが送られた記録・適用された記録は消さない。
    """
    gone = []
    for op_id in all_ids(state):
        if op_id == keep:
            continue
        try:
            record = load(state, op_id)
        except OpError:
            continue
        if (
            record is not None
            and record.get("kind") == kind
            and record.get("parent") == parent
            and record.get("phase") == phase
            and untouched(record)
        ):
            remove(state, op_id)
            gone.append(op_id)
    return gone


def pending(state: str) -> list[dict[str, Any]]:
    """未完了の記録（送り終えていない段階が残るもの）。読めない記録は、壊れた印つきで返す。"""
    out = []
    for op_id in all_ids(state):
        try:
            record = load(state, op_id)
        except OpError as exc:
            out.append({"id": op_id, "broken": str(exc), "stages": [], "kind": ""})
            continue
        if record is not None and not finished(record):
            out.append(record)
    return out


def complete_if_done(state: str, op_id: str) -> bool:
    """すべての段階が sent なら記録を消して True。"""
    record = load(state, op_id)
    if record is None:
        return True
    if finished(record):
        remove(state, op_id)
        return True
    return False


def summary(record: dict[str, Any]) -> str:
    """未完了の記録を 1 行で言う（status・ボード・Stop が出す）。"""
    if record.get("broken"):
        return f"{record['id']}: 壊れている（{record['broken']}）"
    left = ", ".join(f"{s['id']}={s['state']}" for s in unfinished(record))
    where = f"{record.get('parent', '')} のフェーズ {record.get('phase', '')}".strip()
    return f"{record['kind']}（{where}）{record['id']}: 未完了の段階 {left}"
