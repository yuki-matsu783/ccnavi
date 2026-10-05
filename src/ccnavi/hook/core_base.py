"""判定のコアの入力・判定・書き込み。手元と Chrome（Pyodide）で同じ判定を動かすため、
入出力を分ける。

承認・承認の取り下げ・レビュー済みを、同じ形の 3 段に分ける。

    Snapshot（入力）→ judge_approval / withdraw / confirm（判定）→ Changes（書くもの）→ Writer

- **Reader**: Snapshot の中身はファイルシステムから読む（Reader(FS)）。手元は
  作業ツリー、Chrome は API で読んだ中身を MEMFS に組んだ仮のツリー。
  core はブランチごとの blob の表（取っていないファイルを `NOT_FETCHED` にする形）を持たない
- **判定**: `judge_approval`（実行前チェックのモジュール `ccnavi.hook.judge` と
  紛れないように名前を変えた）は承認の対象と画面とダイジェストを返す。通らない理由を返す
  `withdraw` と `confirm` は `core_withdraw` と `core_review` にある
- **Changes**: 書き込みを値として並べたもの（`plan`）。書くときの落ち方（止める・言って続ける）
  もつけてある。`per_branch` はブランチごとの create / update / delete で、Chrome はこれを
  1 コミットにする
- **Writer(FS)**: `write_fs` が Changes をディスクに書く。fsio を通るので、C1 の記録層
  （`fsio.recording`）がそのまま使われる
- **Clock**: 時刻は `Snapshot.stamp`（空なら今）。plan の間は `fsio.clock` で固定し、承認の
  記録と履歴に同じ時刻を書く

判定そのもの（`agree.gather` / `candidates` / `waiting`、`review` の検査）は今のコードを
そのまま通る。ここは入口と出口の形を揃えるだけで、判定は変えない。

このモジュールは入力・判定・書き込みの共通部分だけを持つ。`confirm` の答え（`Checked`）と
`reviewed_mark`、並べる段で履歴を書けないと分かったときの知らせ（`_unwritten`）も、
`core_review` と `core_withdraw` の両方が使うのでここに置く。他の `core_*` はこのモジュールを読み、
`core` は全部を読んで名前を再エクスポートする（呼び出し側は `core.<名前>` のまま引く）。

"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass, field
from typing import TextIO

from ..infra import fsio, settings, tree
from ..tickets import (
    agree,
    agree_candidates,
    agree_digest,
    history,
    review,
)

# ---- 入力 -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Actor:
    """誰がどの経路で動かしたか。`account` はホストのアカウント名か、手元ではトークンの持ち主。

    空なら書かない（マーカーと履歴の中身を前のままにする）。
    """

    account: str = ""
    via: str = ""
    version: str = ""


@dataclass
class Snapshot:
    """判定の入力。ファイルシステムから読む（Reader(FS)）。"""

    root: str
    conf: settings.Settings
    # 時刻（`fsio.stamp` と同じ、オフセット付き）。空なら plan のときの今。
    stamp: str = ""
    actor: Actor = field(default_factory=Actor)


def read_fs(conf: settings.Settings, root: str, stamp: str = "", actor: Actor | None = None):
    """Reader(FS)。手元の作業ツリー（か Chrome の仮のツリー）をそのまま読む Snapshot。"""
    return Snapshot(root=root, conf=conf, stamp=stamp, actor=actor or Actor())


# ---- 判定 -----------------------------------------------------------------------------------


@dataclass
class Verdict:
    """`judge_approval` の答え。`messages` は標準エラーに出す文面（呼び手が出す）。

    `mismatch` は見せた一覧・ダイジェスト（`shown_ids` / `shown_digest`）と今のものが違うときの
    中身。
    見せたものを渡さなければ None。
    """

    gathered: agree.Gathered
    screen_text: str
    digest: str
    messages: str
    mismatch: dict | None = None
    # 判定が読んだ中身（`<ブランチ>:<相対パス>` → 中身のハッシュ）。
    # ダイジェストはこれと本文から作る。
    read_set: dict[str, str] = field(default_factory=dict)

    @property
    def batch(self) -> list[agree_candidates.Candidate]:
        return self.gathered.batch

    @property
    def rejected(self):
        return self.gathered.rejected

    @property
    def problems(self) -> list[str]:
        return self.gathered.problems

    @property
    def identifiers(self) -> list[str]:
        return self.gathered.identifiers


def _say_elsewhere(stderr: TextIO, gathered: agree.Gathered) -> None:
    """本物とするツリーの外に在る計画の違う版の案内（`Gathered.elsewhere`）を標準エラーに出す。

    `--verify` のテキストは本文に同じ段を持つので、そこでは呼ばない（同じ名指しを 2 度出さない）。
    """
    for line in gathered.elsewhere:
        stderr.write(f"ccnavi: {line}\n")


def judge_approval(
    snapshot: Snapshot,
    only: list[str] | None = None,
    shown_ids: list[str] | None = None,
    shown_digest: str | None = None,
) -> Verdict:
    """承認の対象を組み、画面の本文とダイジェストを出す。見せたものを渡せば、今のものと比べる。

    比べ方は前の `--agree --yes` と同じ。絞り（`only`）が通らなかったときは、絞らない
    一覧を今の一覧として比べる（ボードが古い）。識別子とダイジェストのどちらかが違えば `mismatch`。
    """
    err = io.StringIO()
    # 判定が読んだ中身（read_set）を記録し、ダイジェストに入れる。
    with fsio.reading() as seen:
        gathered = agree.gather(err, snapshot.conf, snapshot.root, only)
        shown = gathered
        if shown_ids is not None and gathered.refused:
            shown = agree.gather(err, snapshot.conf, snapshot.root)
        # 書き込む中身は読みの中で組む。動かす提案のバイト列を判定の読みにも入れるため。
        carried = [agree_digest.carried(cand) for cand in shown.batch]
    read = agree_digest.read_set(snapshot.conf, snapshot.root, seen)
    read.update(agree_digest.settings_read_set(snapshot.conf, snapshot.root))
    text = shown.text
    digest = agree_digest.approval_digest(text, shown.batch, read, carried)
    mismatch = None
    if shown_ids is not None:
        wanted = sorted({s.strip() for s in shown_ids if s.strip()})
        current = shown.identifiers
        expected = (shown_digest or "").strip()
        if wanted != current or expected.lower() != digest:
            mismatch = {
                "expected": wanted,
                "current": current,
                "digest": {"expected": shown_digest or "", "current": digest},
            }
    return Verdict(gathered, text, digest, err.getvalue(), mismatch, read)


# ---- 書くもの --------------------------------------------------------------------------------

CREATE = "create"
UPDATE = "update"
DELETE = "delete"


@dataclass
class Changes:
    """書くもののリスト（plan の答え）。

    `planned.stage.items` が書く順（書き込みと見せる行）、`planned.stopped` は途中で止まった
    ところ（止まるまでの分は書く）。`per_branch` はブランチごとのファイルの増減。
    """

    planned: agree.Planned
    root: str
    conf: settings.Settings

    @property
    def lines(self) -> list[str]:
        """ユーザに見せる行（標準出力の分）。書き込みが落ちたときの行は入らない。"""
        out: list[str] = []
        for item in self.planned.stage.items:
            if isinstance(item, fsio.Line) and item.stream == fsio.STREAM_OUT:
                out.append(item.text)
            elif isinstance(item, fsio.Call):
                out.extend(item.lines)
        return out

    def per_branch(self) -> dict[str, list[dict]]:
        """ブランチ（ツリー）ごとの `{op, path, content}`。path はそのツリーからの相対（"/"）。

        ブランチの名前はツリーの HEAD から読む（読めなければツリーの名前）。ツリーの外は
        キーを空文字、path を絶対パスにする。同じ承認で作って消したものは載せない。
        """
        trees = tree.all_trees(self.root, self.conf.projects)
        out: dict[str, list[dict]] = {}
        for spelled, content, before in self.planned.stage.touched():
            if content is None and not before:
                continue
            owner = _owner(trees, spelled)
            if owner is None:
                name, rel = "", spelled
            else:
                found = tree.branch_of(owner.root)
                name = found if found else owner.name
                rel = tree.relative(owner, spelled)
            op = DELETE if content is None else (UPDATE if before else CREATE)
            entry: dict = {"op": op, "path": rel}
            if content is not None:
                entry["content"] = content
            out.setdefault(name, []).append(entry)
        for entries in out.values():
            entries.sort(key=lambda e: e["path"])
        return dict(sorted(out.items()))


def _owner(trees: list[tree.Tree], path: str) -> tree.Tree | None:
    target = os.path.normcase(os.path.realpath(os.path.dirname(path)))
    best = None
    for t in trees:
        if (target == t.root or target.startswith(t.root + os.sep)) and (
            best is None or len(t.root) > len(best.root)
        ):
            best = t
    return best


def plan(snapshot: Snapshot, verdict: Verdict) -> Changes:
    """承認の書き込みを並べる。書かない（fsio の溜める段）。"""
    stamp = snapshot.stamp or fsio.stamp()
    planned = agree.plan_batch(snapshot.root, snapshot.conf, verdict.batch, stamp)
    return Changes(planned, snapshot.root, snapshot.conf)


# ---- Writer(FS) -----------------------------------------------------------------------------


def write_fs(stdout: TextIO, stderr: TextIO, planned: agree.Planned) -> agree.Applied:
    """並べた書き込みをディスクに書く（Writer(FS)）。前の `_apply` と同じ落ち方をする。

    - `FAIL_STOP`: `undo` を消し、`ccnavi: <識別子>: <理由>` を言って止める。置いたものは戻さない
    - `FAIL_WARN`: 同じ形で言って続ける
    - `FAIL_LINE`: 行を出し、同じ組の残り（続く書き込みと行）を飛ばす
    - `FAIL_QUIET`: 何も出さずに続ける
    - `FAIL_HISTORY`: 履歴の書けなかった知らせに溜める（入口が警告で出す）

    並べる段で止まっていたら（`planned.stopped`）、並べた分を書いてから同じ形で言って止める。
    `view_only` の書き込みは書かない。`Call` は同じ組で書けた名札（`tag`）を渡して呼び、
    返った行を出す（マーカーの消去の行と履歴は、実際に消せた種類で書く）。
    """
    placed: list[str] = []
    skipped: set[int] = set()
    done: dict[int, set[str]] = {}
    for item in planned.stage.items:
        group = item.policy.group if isinstance(item, fsio.Op) else item.group
        if group and group in skipped:
            continue
        if isinstance(item, fsio.Line):
            (stderr if item.stream == fsio.STREAM_ERR else stdout).write(item.text + "\n")
            continue
        if isinstance(item, fsio.Call):
            for line in item.run(done.get(item.group, set())):
                stdout.write(line + "\n")
            continue
        if item.view_only:
            continue
        failed = _write_op(item)
        rule = item.policy
        if not failed:
            if rule.places:
                placed.append(rule.places)
            if rule.tag:
                done.setdefault(rule.group, set()).add(rule.tag)
            continue
        message = fsio.failure_text(rule, failed)
        head = f"ccnavi: {rule.ticket}: " if rule.ticket else "ccnavi: "
        if rule.on_fail == fsio.FAIL_STOP:
            for path in rule.undo:
                fsio.remove(path)
            fsio.put_back(rule.restore)
            stderr.write(head + message + "\n")
            return agree.Applied(1, placed, rule.ticket, message)
        if rule.on_fail == fsio.FAIL_WARN:
            stderr.write(head + message + "\n")
        elif rule.on_fail == fsio.FAIL_LINE:
            stdout.write(f"  {message}\n")
            skipped.add(rule.group)
        elif rule.on_fail == fsio.FAIL_HISTORY:
            history.failed_to_write(message)
    if planned.stopped is not None:
        ticket_id, reason = planned.stopped
        stderr.write((f"ccnavi: {ticket_id}: " if ticket_id else "ccnavi: ") + reason + "\n")
        return agree.Applied(1, placed, ticket_id, reason)
    return agree.Applied(0, placed)


def _write_op(op: fsio.Op) -> str:
    """溜めた書き込み 1 つをディスクに書く。書けたら空文字、駄目なら理由。"""
    if op.kind == fsio.OP_TEXT:
        return fsio.write_text(op.path, op.text, op.newline)
    if op.kind == fsio.OP_TEXT_ATOMIC:
        return fsio.write_text_atomic(op.path, op.text, op.newline)
    if op.kind == fsio.OP_BYTES:
        return fsio.write_bytes(op.path, op.content or b"")
    if op.kind == fsio.OP_NEW:
        return fsio.write_new(op.path, op.content or b"")
    if op.kind == fsio.OP_REMOVE:
        fsio.remove(op.path)
        return ""
    if op.kind == fsio.OP_UNLINK:
        return fsio.unlink(op.path)
    if op.kind == fsio.OP_MOVE:
        # 並べた後に行き先が置かれていたら動かさない（上書きしない。`approval_ops.move_file`）。
        if os.path.lexists(op.path):
            return "行き先に既に在る"
        return fsio.move(op.source, op.path)
    if op.kind == fsio.OP_APPEND:
        return fsio.append(op.path, op.data)
    if op.kind == fsio.OP_REPLACE:
        return fsio.replace_bytes(op.path, op.content or b"", op.temp_suffix)
    return f"知らない書き込みの種類: {op.kind}"


# ---- レビュー済み ------------------------------------------------------------------------------


def reviewed_mark(
    mr: int, accepted: list[str], actor: Actor | None = None, stamp: str = ""
) -> dict:
    """レビュー済みのマーカーの中身。空の欄は書かない（`review.reviewed_mark`）。"""
    who = actor or Actor()
    return review.reviewed_mark(mr, accepted, who.account, who.via, stamp)


@dataclass
class Checked:
    """`confirm` と `withdraw` の答え。`problems` は標準エラーに出す文面（行のリスト）。

    通らなければ `changes` は None。
    """

    problems: list[str]
    changes: Changes | None


def _unwritten(text: str) -> list[str]:
    """並べる段で履歴を書けないと分かった知らせ。書いても履歴が残らないので、error として返す。

    手元の Writer(FS) なら警告で続ける所だが、ここで分かるのは書く前（識別子の形が違う、
    リンクになっている）なので、書かずに止めて直させる。
    """
    return [line.replace("ccnavi: 警告: ", "ccnavi: ", 1) for line in text.splitlines() if line]
