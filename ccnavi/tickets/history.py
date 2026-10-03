"""チケットの状態が動いた跡。1 行 1 JSON を、チケットごとのファイルに追記するだけ（ADR-0086）。

## 補助であって権威ではない

状態の正は置き場（ADR-0055、ADR-0058）。ここは「いつ・どの経路で・どこからどこへ動いたか」を
あとから読むための跡で、判定も状態の操作もここを読まない。置き場と食い違ったら置き場を信頼する。
ユーザが hook の外で置き場を動かした分（手で承認する、再開する）は、跡が残らない。

## 追記だけ

書き換える・消すコードは持たない。1 行をまるごと 1 回の write で、追記で開いたハンドルに
出す（記録の 1 行と同じ書き方。付録 B）。同じチケットの跡を 2 つのプロセスが同時に書いても
行が混ざらないのは、POSIX の `O_APPEND`（書くたびに末尾へ位置を移してから 1 回で書く）に頼っている。
Windows の追記はその保証が弱く、同時に書けば行が混ざりうる（読む側は読めない行を飛ばして数える）。

書く中身はどんな文字でも落とさない。不正な UTF-8 から来たサロゲート（`--reason` に混ざる）は
`\\udcff` の形の JSON のエスケープで書き、読めば元の文字列に戻る。

## 書けなくても状態は止めない

跡は補助なので、書けなかったことを理由に状態の操作を止めない。ただし何も言わずに捨てもしない。
書けなかった理由は `failures` に溜め、入口（`cli.run`）が `session` を抜けるときに
標準エラーへ警告として出す（`ccnavi: ...` の 1 行。ほかの書けなかった知らせと同じ出し方）。
置き場を動かす関数の多くは標準エラーを持たないので、溜めて入口で出す形にした。

## 置き場

承認済みチケットと同じツリーの承認済みの領域の `events/<識別子>.ndjson`。マーカーと同じく
親のブランチに入れて git で運ぶ（設計 9.2）。拡張子を `.jsonl` にしないのは、`*.jsonl` を
無視するリポジトリが多く（このリポジトリも判定の記録のために無視している）、無視されると
`ccnavi-push-approved.sh` の `git add` が気づかないうちに落とすから。
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Iterator
from typing import TextIO

from ..infra import fsio
from . import ticket as ticket_mod

# 承認済みの領域の下の置き場と、ファイルの拡張子。
EVENTS_DIR = "events"
SUFFIX = ".ndjson"

# 1 本のファイルから読む上限（バイト）。跡は追記だけで大きくなり続けるので、ボードが毎回
# 全部を読まないように末尾だけを読む。1 行はおよそ 200 バイトなので、既定の件数には十分。
READ_LIMIT_BYTES = 64 * 1024

# ボード（`--explain --json`）に載せる件数。新しい側から。
BOARD_LIMIT = 20

# 動かした経路。
#   cli       エージェントが sh（ccnavi-ticket.sh / ccnavi-review.sh）から打った副命令
#   terminal  ユーザが端末で打った判断（--agree / --reviewed / --close-early）
#   board     VS Code のボードから押した判断（--agree --yes / --reviewed --yes）
#   hook      hook（フェーズの終わりの告知が置くマーカー）
VIA_CLI = "cli"
VIA_TERMINAL = "terminal"
VIA_BOARD = "board"
VIA_HOOK = "hook"
#   chrome    Chrome 拡張から押した判断（承認の取り下げなど。ADR-0093 の 8.8）
VIA_CHROME = "chrome"

# 種類。チケットの置き場が動いたもの。
KIND_APPROVED = "approved"  # todo → doing（承認）
KIND_REVISED = "revised"  # doing → doing（親の計画の改版）
KIND_RAISED = "raised"  # 無し → doing（ユーザが起こした続きの子）
KIND_STARTED = "started"  # doing → doing（着手の欄）
KIND_FINISHED = "finished"  # doing → review か done
KIND_CANCELLED = "cancelled"  # doing → done（取り消しの欄）
KIND_SETTLED = "settled"  # review → done（ユーザのレビューが済んだ）
KIND_WITHDRAWN = "withdrawn"  # doing → todo（承認の取り下げ。ADR-0093 の 8.8）
# マーカー。親の跡に残す。`from` / `to` は null で、`phase` と `mark` を持つ。
# フェーズのマーカーを置いた（pending / requested / reviewed / skipped）
KIND_PHASE_MARK = "phase-mark"
KIND_PHASE_REOPENED = "phase-reopened"  # フェーズのマーカーを消した（同じ番号に子が足された）
KIND_PARENT_MARK = "parent-mark"  # 親のマーカーを置いた（ready / close-early / closed）

_state: dict = {"via": VIA_CLI, "failures": [], "extra": {}}


@contextlib.contextmanager
def session(via: str, stderr: TextIO | None, actor: str = "", version: str = "") -> Iterator[None]:
    """1 回の起動の間、動かした経路を覚え、抜けるときに書けなかった分を警告として出す。

    入れ子になっても外側の経路と溜まりに戻す。テストは同じプロセスで何度も起動するので、
    前の起動の経路や溜まりが次へ漏れないようにする。

    `actor`・`version` は、この間に書く跡の行に足す欄（ADR-0093 の 7.3・8.8。Chrome の
    承認は、ホストのアカウントと拡張の版を跡に残す）。空なら足さない（手元の跡は前のまま）。
    """
    before = dict(_state)
    _state["via"] = via
    _state["failures"] = []
    _state["extra"] = {k: v for k, v in (("actor", actor), ("version", version)) if v}
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


def set_actor(actor: str) -> None:
    """いまの起動の間に書く跡の行に、アカウントの欄を足す（ADR-0093 の 8.9。段階 5 の decide）。

    空なら何もしない（跡は前のまま）。`session` を抜けるときに前の値へ戻る。
    """
    if actor:
        _state["extra"] = {**_state["extra"], "actor": actor}


def extra() -> dict:
    """いまの起動の間に跡の行へ足す欄（`actor`・`version`）の写し。"""
    return dict(_state["extra"])


def via() -> str:
    return str(_state["via"])


def pending_failures() -> list[str]:
    """まだ出していない、書けなかった知らせ。テストが中身を見るため。"""
    return list(_state["failures"])


def path(approved_dir: str, ticket_id: str) -> str:
    """跡のファイルの綴り。識別子の形でなければ空文字（置き場の外を指させない）。

    呼び手は識別子を検査済みのチケットから渡すが、ここでも同じ検査を当てる（多重の守り）。
    """
    if not ticket_mod.is_valid_id(ticket_id):
        return ""
    return os.path.join(approved_dir, EVENTS_DIR, ticket_id + SUFFIX)


def stamp() -> str:
    """跡に書く時刻。UTC の ISO 8601（秒まで、`Z` 付き）。機械をまたいでも並べて読める。

    時計は fsio の差し口（`fsio.clock`）を通る。承認の plan は承認の記録と同じ時刻を書く。
    """
    return fsio.utc_stamp()


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
    # 起動の間の欄（`session` の actor・version）。呼び手が同じ欄を渡せばそちらを採る。
    entry.update(_state.get("extra") or {})
    for key, value in extra.items():
        if value is None or value == "" or value == [] or value == {}:
            continue
        entry[key] = value
    target = path(approved_dir, ticket_id)
    if not target:
        failed = "識別子の形ではないので、ファイルの名前に使わない"
        _state["failures"].append(
            f"{ticket_id!r} の履歴（{kind}）を書かない（{failed}）。状態は動いた"
            "（状態の正は置き場で、履歴は補助）"
        )
        return failed
    template = (
        f"{ticket_id} の履歴（{kind}）を {target} に書けない"
        "（{reason}）。状態は動いた（状態の正は置き場で、履歴は補助）"
    )
    try:
        # 知らない型が混ざっても落とさず、綴りにして残す。
        line = json.dumps(entry, ensure_ascii=False, default=str)
    except (TypeError, ValueError) as exc:
        line, failed = "", f"JSON にできない ({exc})"
    else:
        # 承認の plan（fsio の控える段）では書けなかったときの枝が走らないので、
        # 同じ知らせを Writer(FS) が溜められるようにつけておく。
        with fsio.policy(
            on_fail=fsio.FAIL_HISTORY, message=template, prefix="", undo=(), places=""
        ):
            failed = _append(target, line)
    if failed:
        _state["failures"].append(template.replace("{reason}", failed))
    return failed


def failed_to_write(message: str) -> None:
    """跡を書けなかった知らせを溜める（Writer(FS) が、控えた追記を書けなかったときに呼ぶ）。"""
    _state["failures"].append(message)


def _append(target: str, line: str) -> str:
    """1 行を追記する。書けたら空文字、駄目なら理由。リンクは辿らない（fsio.append）。"""
    # サロゲート（不正な UTF-8 から来た文字）は `\udcff` の形で書く。JSON のエスケープなので、
    # 読めば元の文字列に戻り、何も落とさない。書く前に作るので、書けない理由がここで出ることは無い。
    # 1 行 1 JSON の区切りは `\n` だけ（fsio.append は改行を書き換えない）。
    data = (line + "\n").encode("utf-8", errors="backslashreplace")
    return fsio.append(target, data)


def read(approved_dir: str, ticket_id: str, limit: int = BOARD_LIMIT) -> tuple[list[dict], str]:
    """跡の新しい側から `limit` 件を、古い順に。2 つめは読めなかった理由（無ければ空）。

    ファイルが無いのは跡が無いだけで、理由は空。読めない行（書きかけ、手で壊した行）は
    飛ばして数える。末尾の `READ_LIMIT_BYTES` だけを読むので、途中で切れた先頭の 1 行は捨てる。
    切れ目がちょうど行の頭に当たったときは、その行は完全なので捨てない（1 バイト手前から読んで、
    直前が改行かで見分ける）。識別子の形でなければ読まず、理由を返す。
    """
    target = path(approved_dir, ticket_id)
    if not target:
        return [], f"{ticket_id!r} は識別子の形ではないので、履歴を読まない"
    try:
        if os.path.islink(target):
            return [], f"{target} はシンボリックリンクなので読まない"
        with open(target, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            start = max(0, size - READ_LIMIT_BYTES)
            f.seek(max(0, start - 1))
            raw = f.read()
    except FileNotFoundError:
        return [], ""
    except OSError as exc:
        return [], f"{target} を読めない ({exc})"
    if start > 0:
        # 先頭の 1 バイトは切れ目の直前。改行ならその次から完全な行、違えば最初の改行までが切れ端。
        cut = 1 if raw[:1] == b"\n" else raw.find(b"\n") + 1
        raw = raw[cut:] if cut > 0 else b""
    lines = raw.decode("utf-8", errors="replace").split("\n")
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
