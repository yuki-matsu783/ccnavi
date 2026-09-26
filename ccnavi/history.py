"""チケットの状態が動いた跡。1 行 1 JSON を、チケットごとのファイルに追記するだけ（ADR-0086）。

## 補助であって権威ではない

状態の正は置き場（ADR-0055、ADR-0058）。ここは「いつ・どの経路で・どこからどこへ動いたか」を
あとから読むための跡で、判定も状態の操作もここを読まない。置き場と食い違ったら置き場を信じる。
人が hook の外で置き場を動かした分（手で承認する、再開する）は、跡が残らない。

## 追記だけ

書き換える・消すコードは持たない。1 行をまるごと 1 回の write で、追記で開いたハンドルに
出す（記録の 1 行と同じ書き方。付録 B）。同じチケットの跡を 2 つのプロセスが同時に書いても
行は混ざらない。

## 書けなくても状態は止めない

跡は補助なので、書けなかったことを理由に状態の操作を止めない。ただし黙って捨てもしない。
書けなかった理由は `failures` に溜め、入口（`cli.run`）が `session` を抜けるときに
標準エラーへ警告として出す（`ccnavi: ...` の 1 行。ほかの書けなかった知らせと同じ出し方）。
置き場を動かす関数の多くは標準エラーを持たないので、溜めて入口で出す形にした。

## 置き場

承認済みチケットと同じツリーの承認済みの領域の `events/<識別子>.ndjson`。マーカーと同じく
親のブランチに乗って git で運ぶ（設計 9.2）。拡張子を `.jsonl` にしないのは、`*.jsonl` を
無視するリポジトリが多く（このリポジトリも判定の記録のために無視している）、無視されると
`ccnavi-push-approved.sh` の `git add` が黙って落とすから。
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from collections.abc import Iterator
from typing import TextIO

# 承認済みの領域の下の置き場と、ファイルの拡張子。
EVENTS_DIR = "events"
SUFFIX = ".ndjson"

# 1 本のファイルから読む上限（バイト）。跡は追記だけで伸び続けるので、ボードが毎回
# 全部を読まないように末尾だけを読む。1 行はおよそ 200 バイトなので、既定の件数には十分。
READ_LIMIT_BYTES = 64 * 1024

# ボード（`--explain --json`）に載せる件数。新しい側から。
BOARD_LIMIT = 20

# 動かした経路。
#   cli       エージェントが sh（ccnavi-ticket.sh / ccnavi-review.sh）から打った副命令
#   terminal  人が端末で打った判断（--approve / --reviewed / --close-early）
#   board     VS Code のボードから押した判断（--approve --yes / --reviewed --yes）
#   hook      hook（フェーズの終わりの告知が置くマーカー）
VIA_CLI = "cli"
VIA_TERMINAL = "terminal"
VIA_BOARD = "board"
VIA_HOOK = "hook"

# 種類。チケットの置き場が動いたもの。
KIND_APPROVED = "approved"  # todo → doing（承認）
KIND_REVISED = "revised"  # doing → doing（親の計画の改版）
KIND_RAISED = "raised"  # 無し → doing（人が起こした続きの子）
KIND_STARTED = "started"  # doing → doing（着手の欄）
KIND_FINISHED = "finished"  # doing → review か done
KIND_CANCELLED = "cancelled"  # doing → done（取り消しの欄）
KIND_SETTLED = "settled"  # review → done（人のレビューが済んだ）
# マーカー。親の跡に残す。`from` / `to` は null で、`phase` と `mark` を持つ。
# フェーズのマーカーを置いた（pending / requested / reviewed / skipped）
KIND_PHASE_MARK = "phase-mark"
KIND_PHASE_REOPENED = "phase-reopened"  # フェーズのマーカーを消した（同じ番号に子が足された）
KIND_PARENT_MARK = "parent-mark"  # 親のマーカーを置いた（ready / close-early / closed）

_state: dict = {"via": VIA_CLI, "failures": []}


@contextlib.contextmanager
def session(via: str, stderr: TextIO | None) -> Iterator[None]:
    """1 回の起動の間、動かした経路を覚え、抜けるときに書けなかった分を警告として出す。

    入れ子になっても外側の経路と溜まりに戻す。テストは同じプロセスで何度も起動するので、
    前の起動の経路や溜まりが次へ漏れないようにする。
    """
    before = dict(_state)
    _state["via"] = via
    _state["failures"] = []
    try:
        yield
    finally:
        failures = list(_state["failures"])
        _state.update(before)
        if stderr is not None:
            for line in failures:
                stderr.write(f"ccnavi: 警告: {line}\n")


def set_via(via: str) -> None:
    """いまの起動の経路を差し替える。`session` の中で、経路が後から分かったときに使う。"""
    _state["via"] = via


def via() -> str:
    return str(_state["via"])


def pending_failures() -> list[str]:
    """まだ出していない、書けなかった知らせ。テストが中身を見るため。"""
    return list(_state["failures"])


def path(approved_dir: str, ticket_id: str) -> str:
    return os.path.join(approved_dir, EVENTS_DIR, ticket_id + SUFFIX)


def stamp() -> str:
    """跡に書く時刻。UTC の ISO 8601（秒まで、`Z` 付き）。機械をまたいでも並べて読める。"""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def note(
    approved_dir: str,
    ticket_id: str,
    kind: str,
    source: str | None,
    target: str | None,
    **extra,
) -> str:
    """跡を 1 行足す。書けなかったら理由を返し、同じ理由を `failures` にも溜める。

    `source` / `target` は置き場の名前（`todo` / `doing` / `review` / `done`）。置き場が動かない
    もの（マーカー）は None。`extra` は空の値を落として足す。
    """
    entry: dict = {
        "at": stamp(),
        "ticket": ticket_id,
        "kind": kind,
        "from": source,
        "to": target,
        "via": via(),
    }
    for key, value in extra.items():
        if value is None or value == "" or value == [] or value == {}:
            continue
        entry[key] = value
    failed = _append(path(approved_dir, ticket_id), json.dumps(entry, ensure_ascii=False))
    if failed:
        _state["failures"].append(
            f"{ticket_id} の履歴（{kind}）を {path(approved_dir, ticket_id)} に書けない"
            f"（{failed}）。状態は動いた。正は置き場で、履歴は補助"
        )
    return failed


def _append(target: str, line: str) -> str:
    """1 行を追記する。書けたら空文字、駄目なら理由。リンクは辿らない。"""
    try:
        if os.path.islink(target):
            return "シンボリックリンクなので書かない"
        os.makedirs(os.path.dirname(target), exist_ok=True)
        flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(target, flags, 0o644)
        try:
            os.write(fd, (line + "\n").encode("utf-8"))
        finally:
            os.close(fd)
    except OSError as exc:
        return str(exc)
    return ""


def read(approved_dir: str, ticket_id: str, limit: int = BOARD_LIMIT) -> tuple[list[dict], str]:
    """跡の新しい側から `limit` 件を、古い順に。2 つめは読めなかった理由（無ければ空）。

    ファイルが無いのは跡が無いだけで、理由は空。読めない行（書きかけ、手で壊した行）は
    飛ばして数える。末尾の `READ_LIMIT_BYTES` だけを読むので、途中で切れた先頭の 1 行は捨てる。
    """
    target = path(approved_dir, ticket_id)
    try:
        if os.path.islink(target):
            return [], f"{target} はシンボリックリンクなので読まない"
        with open(target, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            start = max(0, size - READ_LIMIT_BYTES)
            f.seek(start)
            raw = f.read()
    except FileNotFoundError:
        return [], ""
    except OSError as exc:
        return [], f"{target} を読めない ({exc})"
    lines = raw.decode("utf-8", errors="replace").split("\n")
    if start > 0:
        lines = lines[1:]
    entries: list[dict] = []
    skipped = 0
    for line in lines:
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except ValueError:
            skipped += 1
            continue
        if not isinstance(value, dict):
            skipped += 1
            continue
        entries.append(value)
    why = f"{target} の {skipped} 行を読めなかった" if skipped else ""
    return (entries[-limit:] if limit > 0 else entries), why
