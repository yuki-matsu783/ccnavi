"""放置したセッションに、次のセッションへの引き継ぎを促す。

会話が大きく育ったまま長く放置すると、モデルが覚えている会話のコピー（プロンプトキャッシュ）が
時間切れで消える。そのあと同じ会話を続けると、会話全体を最初から送り直すことになり、
キャッシュが残っているときの数倍の料金がかかる。消える前に引き継ぎの文書を書かせておけば、
新しいセッションを小さな会話から始められる。キャッシュが残っているうちに書かせるぶんの
料金は、送り直しよりずっと安い。

2 つの働きがある。どちらも、会話の大きさが「基準 + 閾値」以上のときだけ動く。

- 知らせ（`notice`）: ターンの終わりに、ユーザへ 1 セッション 1 回だけ事情と勧めを出す。
- 待機（`start` → `check`）: ターンの終わりから待ち時間（キャッシュの保持時間の少し手前）が過ぎても
  ユーザが何も打たなければ、モデルを起こして引き継ぎ文書を `scratchpad/` に書かせる。

基準は、前回引き継ぎを書かせたときの会話の大きさ（最初は 0）。同じセッションで続けても、
閾値のぶん育つまでは次を促さない。会話が基準より小さくなったら（`/compact`・`/clear` のあと）
基準を 0 に戻す。

置き場は `<state>/idle/<セッション>.json`（待機の状態と基準）と `<セッション>.notice`
（知らせ済みの印）。待機は別プロセス（sh の `ccnavi-idle.sh`）が `sleep` で受け持ち、
ここは判定と状態だけを持つ。ネットワークには出ない。
"""

from __future__ import annotations

import json
import os
import secrets
import time
from dataclasses import dataclass

from ..infra import fsio

DEFAULT_TOKENS = 400000
# 1 時間のキャッシュの失効の 5 分前と、5 分のキャッシュの失効の 1 分前。
WAIT_LONG = 55 * 60
WAIT_SHORT = 4 * 60
# 起こすときの指示の先頭に付ける目印。この目印の付いた入力では、状態を戻さない。
MARKER = "[idle-handoff]"
# 状態ファイルを残す日数。
KEEP_DAYS = 7

WAITING = "waiting"
FIRED = "fired"
ACTIVE = "active"

# 末尾から読むトランスクリプトの大きさ（バイト）。最新の応答の行が入る長さがあればよい。
_TAIL_BYTES = 1024 * 1024
# キャッシュの保持時間を探して遡る応答の行数の上限。
_LOOKBACK = 40

_NOTICE = """\
ccnavi: この会話はだいぶ大きくなりました（約 {size} トークン）。

【なぜ知らせるか】
Claude は、会話の内容をいったん記憶（キャッシュ）しておいて、続きを安く処理しています。
この記憶は、最後のやり取りから約 {minutes} 分たつと消えます。
消えたあとで同じ会話を続けると、会話全体を最初から送り直すことになり、
記憶が残っているときより料金（利用枠の消費）がずっと多くかかります。会話が大きいほど差が開きます。

【ccnavi がすること】
このまま {wait_minutes} 分ほど何も打たずにいると、記憶が消える前に、Claude に
「次のセッションへの引き継ぎ文書」を書いてもらいます（置き場: scratchpad/。ファイルの
編集やコミットはしません）。

【おすすめ】
席を外したり、作業を終えるときは、そのまま放置して大丈夫です。戻ったら新しいセッションを
開き、引き継ぎ文書を読ませると、小さな会話から続きを始められます。
この会話をそのまま続けたいときは、何か入力してください。引き継ぎは書かれません。
（この知らせは 1 セッションに 1 回です。
　切るには、環境変数 CCNAVI_HANDOFF_SUGGEST_TOKENS を 0 にします）
"""

_WAKE = """\
{marker} ユーザが {minutes} 分ほど席を外しているようです。会話の記憶（プロンプトキャッシュ）が
もうすぐ切れます。切れたあとで続けると料金がかさむので、今のうちに、次のセッションへの
引き継ぎ文書を、ワークスペースルート直下の `scratchpad/`（無ければ作る）に
`handoff-<日時>.md` の名前で書いてください。

書くこと:
- 目的（何のための作業か）
- 済んでいて、確かめたこと
- 進行中のこと
- 決まっていない判断（ユーザに聞くこと）
- 次にやる 1 手（具体的に）

書き方:
- 「このセッションで確かめたこと」と「前から持ち越していて、再確認していないこと」を分ける。
- 「待ち」「ブロック」「できない」の行には、いつ・どこで聞いたか、答えをどこで見るかを書く。
  次のセッションは、これらの行を信じる前に確かめる。

してはいけないこと:
- 新しい作業を始めない。コミットも push もしない。

書く価値のあるものが無ければ、1 行でそう答えて止まってください。
書き終えたら、ファイルの場所を 1 行で伝えて止まってください。
"""


@dataclass(frozen=True)
class Usage:
    """直近の応答の時点での会話の大きさと、キャッシュの保持時間。"""

    tokens: int
    # キャッシュの保持時間（秒）。300 か 3600。
    ttl: int


def threshold(raw: str) -> int:
    """閾値（トークン数）を読む。0 は無効。空・読めない値は既定、負数は 0（無効）。"""
    text = (raw or "").strip()
    if not text:
        return DEFAULT_TOKENS
    try:
        value = int(text)
    except ValueError:
        return DEFAULT_TOKENS
    return max(value, 0)


def unattended(env: dict[str, str]) -> bool:
    """見ている人がいない実行（`claude -p`・SDK）か。待機がプロセスを引き止めるので除く。"""
    if env.get("CLAUDE_CODE_SESSION_ATTENDED") == "0":
        return True
    return env.get("CLAUDE_CODE_ENTRYPOINT", "").startswith("sdk")


def usage_of(transcript_path: str) -> Usage | None:
    """トランスクリプトの末尾から、直近の応答の会話の大きさとキャッシュの保持時間を読む。

    サブエージェントの行（`isSidechain`）は別の会話なので除く。大きさが分からなければ None。
    保持時間は、直近でキャッシュを書き込んだ応答の内訳（`cache_creation`）で決める。
    5 分側に書き込みがあれば 5 分、無ければ 1 時間。どの応答にも書き込みが無ければ 1 時間とみなす。
    """
    if not transcript_path:
        return None
    raw = _tail(transcript_path)
    if raw is None:
        return None
    rows = _assistant_usages(raw)
    if not rows:
        return None
    first = rows[0]
    tokens = sum(_int(first.get(k)) for k in _SIZE_KEYS)
    return Usage(tokens=tokens, ttl=_ttl_of(rows[:_LOOKBACK]))


_SIZE_KEYS = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")


def _int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _tail(path: str) -> str | None:
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(size - _TAIL_BYTES, 0))
            data = handle.read()
    except OSError:
        return None
    text = data.decode("utf-8", errors="replace")
    if size > _TAIL_BYTES:
        # 先頭の欠けた行を捨てる。
        text = text.partition("\n")[2]
    return text


def _assistant_usages(raw: str) -> list[dict]:
    """新しい順に、サブエージェントでない応答の usage を返す。"""
    rows: list[dict] = []
    for line in reversed(raw.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if not isinstance(obj, dict) or obj.get("type") != "assistant" or obj.get("isSidechain"):
            continue
        message = obj.get("message")
        usage = message.get("usage") if isinstance(message, dict) else None
        if isinstance(usage, dict):
            rows.append(usage)
    return rows


def _ttl_of(rows: list[dict]) -> int:
    for usage in rows:
        made = usage.get("cache_creation")
        if not isinstance(made, dict):
            continue
        short = _int(made.get("ephemeral_5m_input_tokens"))
        long = _int(made.get("ephemeral_1h_input_tokens"))
        if short or long:
            return 300 if short else 3600
    return 3600


def wait_seconds(ttl: int, override: str = "") -> int:
    """放置してから起こすまでの待ち時間（秒）。上書きが正の整数ならそれ。"""
    text = (override or "").strip()
    if text.isdigit() and int(text) > 0:
        return int(text)
    return WAIT_SHORT if ttl <= 300 else WAIT_LONG


# ---- 状態


def _dir(state: str) -> str:
    return os.path.join(state, "idle")


def _path(state: str, session: str, suffix: str = ".json") -> str:
    return os.path.join(_dir(state), fsio.safe_name(session) + suffix)


def _read(state: str, session: str) -> dict:
    return fsio.read_dict(_path(state, session)) or {}


def _write(state: str, session: str, data: dict) -> bool:
    os.makedirs(_dir(state), exist_ok=True)
    return not fsio.write_json_atomic(_path(state, session), data)


def _base(data: dict, size: int) -> int:
    """基準。会話が基準より小さくなっていたら 0 に戻っているものとして読む。"""
    base = _int(data.get("base"))
    return base if size >= base else 0


def due(data: dict, size: int, limit: int) -> bool:
    """この大きさが、促す対象か。"""
    return limit > 0 and size >= _base(data, size) + limit


def session_ok(session: str) -> bool:
    """状態ファイルの名前に使える識別子か。"""
    return bool(session) and fsio.safe_name(session) == session


@dataclass(frozen=True)
class Plan:
    """待機の指示。sh が `sleep` してから `check` を呼ぶ。"""

    wait: int
    token: str


def start(
    state: str,
    session: str,
    usage: Usage | None,
    limit: int,
    wait_override: str = "",
) -> Plan | None:
    """ターンの終わりに待機を始める。始めないなら None。

    見ている人がいない実行・サブエージェント・無効・促す大きさに届かない・発火済みは呼ぶ前に
    除くか、ここで None にする。状態は `waiting` と印（token）にして、基準は持ち越す。
    """
    if usage is None or not session_ok(session):
        return None
    data = _read(state, session)
    if data.get("state") == FIRED or not due(data, usage.tokens, limit):
        return None
    token = secrets.token_hex(8)
    new = {
        "state": WAITING,
        "token": token,
        "due": usage.tokens,
        "base": _base(data, usage.tokens),
        "at": int(time.time()),
    }
    if not _write(state, session, new):
        return None
    return Plan(wait=wait_seconds(usage.ttl, wait_override), token=token)


def check(state: str, session: str, token: str, wait: int) -> str | None:
    """待機のあとで呼ぶ。起こすべきなら指示の文面を返し、状態を `fired` にして基準を進める。

    状態が `waiting` でなくなっている、または印が違う（その間にユーザが打った、あとの待機に
    置き換わった）ときは None。
    """
    if not session_ok(session):
        return None
    data = _read(state, session)
    if data.get("state") != WAITING or data.get("token") != token:
        return None
    new = dict(data, state=FIRED, base=_int(data.get("due")))
    new.pop("token", None)
    if not _write(state, session, new):
        return None
    return _WAKE.format(marker=MARKER, minutes=max(wait // 60, 1))


def reset(state: str, session: str, prompt: str, background: bool = False) -> None:
    """ユーザの入力で待機をやめる。基準は残す。

    起こすときの指示（目印付き）と、バックグラウンド作業の完了通知は、ユーザの入力ではないので
    状態を戻さない。
    """
    if not session_ok(session) or MARKER in prompt or background:
        return
    data = _read(state, session)
    if not data or data.get("state") == ACTIVE:
        return
    data.pop("token", None)
    data["state"] = ACTIVE
    _write(state, session, data)


def notice(
    state: str, session: str, usage: Usage | None, limit: int, wait_override: str = ""
) -> str | None:
    """ユーザへの知らせ。出すなら文面を返し、知らせ済みの印を置く。1 セッションに 1 回。"""
    if usage is None or not session_ok(session):
        return None
    data = _read(state, session)
    if not due(data, usage.tokens, limit):
        return None
    mark = _path(state, session, ".notice")
    if os.path.exists(mark):
        return None
    os.makedirs(_dir(state), exist_ok=True)
    if fsio.write_text_atomic(mark, "1\n"):
        return None
    wait = wait_seconds(usage.ttl, wait_override)
    return _NOTICE.format(
        size=f"{usage.tokens:,}",
        minutes=usage.ttl // 60,
        wait_minutes=max(wait // 60, 1),
    )


def prune(state: str, now: float | None = None) -> None:
    """古い状態ファイルを消す。"""
    folder = _dir(state)
    cutoff = (now if now is not None else time.time()) - KEEP_DAYS * 86400
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for name in names:
        path = os.path.join(folder, name)
        try:
            if os.path.getmtime(path) < cutoff:
                fsio.remove(path)
        except OSError:
            continue
