"""C1（ADR-0093 の 4.3・4.4。段階 2d）で sh が聞くこと。

状態を書く操作を「ロック → 途中の操作の確認 → C1 の外の変更の見分けとコミット → 取り込み →
未送信の確かめ → 書く → コミット → push」の 1 操作にするのは sh（`ccnavi-common.sh` の
`ccnavi_c1_*`）。実行ファイルはネットワークに出ず、コミットもしない（D17・4.5）。
ここが答えるのは 2 つだけで、どちらも 1 行 1 項目（`<鍵> <値>`。D33）。

- `ccnavi c1 family <識別子>`: その識別子の家族と、C1 の対象か
  - 対象は、置き場の綴りが相対で、家族の控えがあり（取り込み済み）、chat だけの家族でなく、
    止める理由（閉じた・gone・blocked・親のワークツリーが無い など）の無い家族（D11）
  - 止める理由のある取り込み済みの家族は `target stop`。sh は何も書かずに止める
  - origin の有無は sh が見る（ここは git の設定を読まない）
- `ccnavi c1 sort <親> [<版>]`: 親のワークツリーの置き場（`.ccnavi/approved/`・
  `wip/proposals/review/`）の変更を 4.4 の (b)・(c)・(d) に分ける
  - 版が無ければ未コミットの変更（C1 の 3）、版があれば `<版>..HEAD` でコミットに入った
    変更（C1 の 5）
  - (b) ccnavi が書いたと内容で分かるもの。hook のフェーズの終わりの告知が置く
    `<N>.pending`・`<N>.skipped` と跡の追記だけ（台帳は持たない）。`reviewed` は入れない
  - (c) 人が運ぶもの（人の判断）。C1 は運ばずに止める
  - (d) 見分けられないもの。C1 は止める
  - `keep` は record-risk が書いた `<子>.judge.json`（その子の `finish` の C1 が運ぶ。
    未コミットのときだけ）
  - `skip` は書きかけの一時ファイル（数えない、コミットもしない）

git はローカルの読み取りだけ（`status`・`diff`・`ls-tree`・`cat-file`）。
"""

from __future__ import annotations

import json
import os
import re
from typing import TextIO

from . import approval, fsio, gitcmd, history, phase, settings, syncstate
from . import ticket as ticket_mod

# 答えの頭の行。sh はこれが無ければ「実行ファイルが C1 を知らない（古い）」と読んで止める。
HEAD = "c1 1"

TARGET_YES = "yes"
TARGET_NO = "no"
TARGET_STOP = "stop"

KIND_B = "b"
KIND_C = "c"
KIND_D = "d"
KIND_KEEP = "keep"
KIND_SKIP = "skip"

# hook の告知が置くマーカーの欄（phase.announce）。これ以外の欄があれば (b) にしない。
_HOOK_MARK_FIELDS = frozenset({"at", "review", "source", "deferred_to", "tickets"})
_HOOK_MARKS = (approval.MARK_PENDING, approval.MARK_SKIPPED)
# 跡の行が必ず持つ欄（history.note）。
_EVENT_FIELDS = ("at", "ticket", "kind")
# 書きかけの一時ファイル（fsio の `.<名前>.<一意>.part.*`、フローの保存の `flows/.*.tmp`、
# configsync の `*.ccnavi-sync`）。
_TEMP = re.compile(r"(^|/)\.[^/]*\.part(\.[^/]*)?$|(^|/)flows/\.[^/]*\.tmp$|\.ccnavi-sync$")
# 人の判断が書くもの（4.4 の表）。フローの本文、reviewed・close-early の印、設定を見た印、
# 受け入れたスレッド、人の承認で置かれた写し。
_HUMAN_MARK_NAMES = (
    f"{approval.PARENT_MARK_CLOSE_EARLY}.json",
    "config-sync.json",
    approval.ACCEPTED_FILE,
)
_TIMEOUT = 20.0


def family_of(ident: str) -> str:
    """識別子の家族の親（3.3 の 5）。子の形（`<親>-<2 桁>`）なら親、そうでなければ自身。"""
    matched = ticket_mod.child_pattern().match(ident)
    return matched.group("parent") if matched else ident


def _relative_places(conf: settings.Settings) -> tuple[str, str] | None:
    """置き場の綴り（承認済み、レビュー待ち）。どちらかが絶対パスなら None（3.1 の 12）。"""
    approved = fsio.slashed(conf.approved or settings.DEFAULT_APPROVED).strip("/")
    tickets = fsio.slashed(conf.tickets or settings.DEFAULT_TICKETS).rstrip("/")
    raw = (conf.approved or "", conf.tickets or "")
    if any(os.path.isabs(v) or re.match(r"^[A-Za-z]:", v) for v in raw if v):
        return None
    return approved, f"{tickets.strip('/')}/{ticket_mod.REVIEW}"


def family(stdout: TextIO, conf: settings.Settings, root: str, ident: str) -> int:
    """`ccnavi c1 family <識別子>`。家族と、C1 の対象か。"""
    parent = family_of(ident)
    lines = [("family", parent)]
    places = _relative_places(conf)
    st = syncstate.standing_any(conf, root, parent)
    lines.append(("repo", st.repo))
    if places is not None:
        lines += [("approved", places[0]), ("review", places[1])]
    target, why = TARGET_YES, ""
    if places is None:
        target, why = TARGET_NO, "置き場の綴りが絶対パス（C1 と Chrome の対象外）"
    elif not st.imported:
        target, why = TARGET_NO, "家族の控えが無い（取り込み済みでない。今の手元の動きのまま）"
    elif _chat_only(conf, root, parent):
        target, why = TARGET_NO, "chat だけの家族（MR を持たない。今の手元の動きのまま）"
    elif st.stop or st.home is None:
        target, why = TARGET_STOP, st.stop or "親のワークツリーが決まらない"
    lines.append(("target", target))
    if why:
        lines.append(("why", why))
    if st.record is not None:
        lines.append(("state", st.record.state or "broken"))
    if target == TARGET_YES and st.home is not None:
        lines.append(("tree", st.home.root))
    if target == TARGET_STOP:
        for hint in syncstate.guidance(root, st):
            lines.append(("hint", hint))
    stdout.write(HEAD + "\n")
    for key, value in lines:
        stdout.write(f"{key} {' '.join(str(value).split())}\n")
    return 0


def _chat_only(conf: settings.Settings, root: str, parent: str) -> bool:
    try:
        return phase.chat_only(root, conf, parent)
    except (OSError, ValueError):
        return False


def sort(
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    parent: str,
    since: str = "",
) -> int:
    """`ccnavi c1 sort <親> [<版>]`。置き場の変更を (b)・(c)・(d) に分けて 1 行 1 つ。"""
    places = _relative_places(conf)
    st = syncstate.standing_any(conf, root, parent)
    if places is None or st.home is None:
        stderr.write(f"ccnavi: c1 sort: 家族 {parent} の親のワークツリーが決まらない\n")
        return 1
    tree_root = st.home.root
    changed, why = _changed(tree_root, places, since)
    if why:
        stderr.write(f"ccnavi: c1 sort: {why}\n")
        return 1
    stdout.write(HEAD + "\n")
    for rel in changed:
        kind = classify(tree_root, places[0], rel, since)
        stdout.write(f"{kind} {rel}\n")
    return 0


def _changed(tree_root: str, places: tuple[str, str], since: str) -> tuple[list[str], str]:
    """置き場の変更のパス（ツリーからの相対、"/" 区切り）。"""
    specs = [f":(literal){p}" for p in places]
    if since:
        done = gitcmd.run(
            tree_root,
            ["diff", "--name-only", "-z", "--no-renames", since, "HEAD", "--", *specs],
            _TIMEOUT,
            raw_paths=True,
        )
        if not done.ok:
            return [], f"{since}..HEAD の差分を読めない（{(done.err or done.failure).strip()}）"
        return sorted(p for p in done.out.split("\0") if p), ""
    done = gitcmd.run(
        tree_root,
        ["status", "--porcelain", "-z", "--untracked-files=all", "--no-renames", "--", *specs],
        _TIMEOUT,
        raw_paths=True,
    )
    if not done.ok:
        return [], f"git の状態を読めない（{(done.err or done.failure).strip()}）"
    return sorted(entry[3:] for entry in done.out.split("\0") if len(entry) > 3), ""


def classify(tree_root: str, approved_rel: str, rel: str, since: str = "") -> str:
    """1 つのパスの分け。`since` が無ければ未コミット（今の中身と HEAD）、あれば
    HEAD と `since`。

    読めないものは (d)。
    """
    if _TEMP.search(rel):
        return KIND_SKIP
    if since:
        now, readable = gitcmd.blob(tree_root, "HEAD", rel, _TIMEOUT)
        before, readable_before = gitcmd.blob(tree_root, since, rel, _TIMEOUT)
    else:
        now, readable = _read(os.path.join(tree_root, rel.replace("/", os.sep)))
        before, readable_before = gitcmd.blob(tree_root, "HEAD", rel, _TIMEOUT)
    if not readable or not readable_before:
        return KIND_D
    inside = rel[len(approved_rel) + 1 :] if rel.startswith(approved_rel + "/") else ""
    if not inside:
        # レビュー待ちの置き場（提案の review/）。ccnavi の hook は書かない。
        return KIND_D
    parts = inside.split("/")
    if _hook_mark(parts, now, before) or _events_append(parts, now, before):
        return KIND_B
    if not since and _judge_record(parts):
        return KIND_KEEP
    if _human(parts, now, before):
        return KIND_C
    return KIND_D


def _read(path: str) -> tuple[bytes | None, bool]:
    try:
        if os.path.islink(path):
            return None, False
        with open(path, "rb") as f:
            return f.read(), True
    except FileNotFoundError:
        return None, True
    except OSError:
        return None, False


def _hook_mark(parts: list[str], now: bytes | None, before: bytes | None) -> bool:
    """`phases/<親>/<N>.(pending|skipped)` で、変更前は無く、中身が hook の欄だけの JSON。"""
    if len(parts) != 3 or parts[0] != approval.PHASES_DIR or before is not None or now is None:
        return False
    number, _, kind = parts[2].partition(".")
    if not number.isdigit() or kind not in _HOOK_MARKS:
        return False
    data = _json(now)
    if not isinstance(data, dict) or not isinstance(data.get("at"), str):
        return False
    return set(data) <= _HOOK_MARK_FIELDS


def _events_append(parts: list[str], now: bytes | None, before: bytes | None) -> bool:
    """`events/<識別子>.ndjson` で、変更前が前置きで、足した行がどれも跡の行として読める。"""
    if len(parts) != 2 or parts[0] != history.EVENTS_DIR or now is None:
        return False
    if not parts[1].endswith(history.SUFFIX):
        return False
    head = before or b""
    if not now.startswith(head) or len(now) == len(head):
        return False
    if head and not head.endswith(b"\n"):
        return False
    added = now[len(head) :]
    if not added.endswith(b"\n"):
        return False
    for line in added.decode("utf-8", errors="strict").split("\n")[:-1]:
        row = _json(line.encode("utf-8"))
        if not isinstance(row, dict):
            return False
        if not all(isinstance(row.get(key), str) for key in _EVENT_FIELDS):
            return False
    return True


def _judge_record(parts: list[str]) -> bool:
    """`phases/<親>/<子>.judge.json`（record-risk の記録）。"""
    return (
        len(parts) == 3
        and parts[0] == approval.PHASES_DIR
        and parts[2].endswith(f".{approval.CHILD_RECORD_JUDGE}.json")
    )


def _human(parts: list[str], now: bytes | None, before: bytes | None) -> bool:
    """人の判断が書くものの形（4.4 の表）。形だけで見る（(c) も (d) も C1 は止める）。"""
    if len(parts) == 2 and parts[0] == "flows" and parts[1].endswith((".yml", ".yaml")):
        return True
    if len(parts) == 2 and parts[0] == ticket_mod.DOING and before is None and now is not None:
        return True  # 人の承認で置かれた写し
    if len(parts) == 3 and parts[0] == approval.PHASES_DIR:
        name = parts[2]
        if name in _HUMAN_MARK_NAMES:
            return True
        number, _, kind = name.partition(".")
        if number.isdigit() and kind in (approval.MARK_REVIEWED, approval.MARK_SKIPPED):
            return True
    return False


def _json(data: bytes) -> object:
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
