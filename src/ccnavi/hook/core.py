"""判定のコアと差し替え点。手元と Chrome（Pyodide）で同じ判定を動かすため、入出力を分ける。

承認・承認の取り下げ・レビュー済みを、同じ形の 3 段に分ける。

    Snapshot（入力）→ judge_approval / withdraw / confirm（判定）→ Changes（書くもの）→ Writer

- **Reader**: Snapshot の中身はファイルシステムから読む（Reader(FS)）。手元は
  作業ツリー、Chrome は API で読んだ中身を MEMFS に組んだ仮のツリー。
  core はブランチごとの blob の表（取っていないファイルを `NOT_FETCHED` にする形）を持たない
- **判定**: `judge_approval`（実行前チェックのモジュール `ccnavi.hook.judge` と
  紛れないように名前を変えた）は承認の対象と画面とダイジェストを、`withdraw` と `confirm` は通らない
  理由を返す
- **Changes**: 書き込みを値として並べたもの（`plan`）。書くときの落ち方（止める・言って続ける）
  もつけてある。`per_branch` はブランチごとの create / update / delete で、Chrome はこれを
  1 コミットにする
- **Writer(FS)**: `write_fs` が Changes をディスクに書く。fsio を通るので、C1 の記録層
  （`fsio.recording`）がそのまま使われる
- **Clock**: 時刻は `Snapshot.stamp`（空なら今）。plan の間は `fsio.clock` で固定し、承認の
  記録と履歴に同じ時刻を書く

判定そのもの（`agree.gather` / `candidates` / `waiting`、`review` の検査）は今のコードを
そのまま通る。ここは入口と出口の形を揃えるだけで、判定は変えない。

手元の入口（`--agree` の 4 つの枝と `review confirm`）もここに置く。コアを通す入口を
コアより下の段（agree・review）に置くと、import が循環する（tests/core/test_module_layers.py）。
`--reviewed` の決め方（review_decide）と親を閉じる操作（review_close）は review を読む末端で、
コアはどちらも読まない。
"""

from __future__ import annotations

import io
import json
import os
from dataclasses import dataclass, field
from typing import TextIO

from ..infra import fsio, modes, settings, tree
from ..tickets import (
    agree,
    agree_candidates,
    agree_digest,
    agree_screen,
    approval,
    approval_checks,
    approval_marks,
    history,
    phase,
    review,
    ticket_model,
    workflow,
)
from ..tickets import ticket as ticket_mod

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
        # 並べた後に行き先が置かれていたら動かさない（上書きしない。`approval.move_file`）。
        if os.path.lexists(op.path):
            return "行き先に既に在る"
        return fsio.move(op.source, op.path)
    if op.kind == fsio.OP_APPEND:
        return fsio.append(op.path, op.data)
    if op.kind == fsio.OP_REPLACE:
        return fsio.replace_bytes(op.path, op.content or b"", op.temp_suffix)
    return f"知らない書き込みの種類: {op.kind}"


# ---- 手元の入口（`--agree`。端末・ボードの `--yes`・`--preview`・`--verify`） ------------------
#
# 前は approval.py にあった。コアを通す入口なので、コアより下の段（agree）には置かない
# （agree → core → agree の循環になる。tests/core/test_module_layers.py）。


def approve(
    stdin: TextIO,
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    only: list[str] | None = None,
) -> int:
    """未承認の提案をまとめてユーザに見せ、承認されたら承認済みチケットを置く。

    エージェントではなくユーザが端末から打つ経路。提案を書き直す方法は用意しない。
    チケットを書くのはエージェントの仕事で、承認する場所で書き替えられると、
    承認したユーザが承認したものの作者になる。

    承認の対象は「いま承認待ちのもの全部」。親が 1 本、その下の子が複数、という形が普通。
    子は親の部分集合なので、新たに書けるようになる領域は親の分だけ。

    親の改版（計画の変更）も一緒に承認の対象に入る。承認済みチケットは動かないのが原則で、改版はその
    唯一の例外（設計 9.7）。変えられるのは `plan` と `feedback` だけ。

    `only` は承認の対象を識別子で絞る（`ccnavi --agree <識別子>...`）。VS Code 拡張の
    ボードが絞り込みで見えている分だけを渡す。絞りは対象を狭めるだけで、絞らないときに
    落ちるものを通してはいけない。だから、承認待ちに無い識別子が入っていたら
    何も承認しない（ボードが古いときに、見せた以外のものを通さないため）。親の
    改版が承認待ちなのに対象から外した子も何も承認しない（外すと旧計画で検証される）。
    絞った対象に入らない親を持つ子は「親が承認されていない」で落ちる。

    判定と書き込みはコア（`judge_approval` → `plan` → `write_fs`）。
    """
    snapshot = read_fs(conf, root)
    verdict = judge_approval(snapshot, only)
    stderr.write(verdict.messages)
    _say_elsewhere(stderr, verdict.gathered)
    gathered = verdict.gathered
    if gathered.refused:
        return 1
    if gathered.nothing_pending:
        stdout.write("承認待ちのチケットは無い。\n")
        # 読めない提案があったなら、その旨は標準エラーに出ている。承認するものが
        # 無いのは正常だが、壊れた提案を「何も無い」で通す形にはしない。
        return 1 if gathered.broken else 0
    if not gathered.batch:
        return 1

    if gathered.note:
        stdout.write(gathered.note + "\n\n")
    stdout.write(gathered.text + "\n\n")
    stdout.write(f"この {len(gathered.batch)} 件を承認する場合は y、やめる場合はそれ以外: ")
    stdout.flush()
    if fsio.read_line(stdin).strip().lower() not in ("y", "yes"):
        stderr.write("ccnavi: 承認しなかった\n")
        return 1
    code = write_fs(stdout, stderr, plan(snapshot, verdict).planned).code
    if code == 0 and gathered.rejected:
        # 承認の対象の一部が落ちたときは、通ったぶんを置いてから失敗で終わる。置いたので
        # 繰り返してよく、落ちたものは上で名指ししてある。成功で終わると、
        # 端末を見ていない側（スクリプト、CI）は全部通ったと読む。
        stderr.write(
            f"ccnavi: {len(gathered.rejected)} 件は承認の対象にしなかった。"
            "直して出し直してください（通ったぶんの承認済みチケットは置いた）\n"
        )
        return 1
    return code


def preview(
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    as_json: bool,
    only: list[str] | None = None,
) -> int:
    """`--agree --preview`。一覧を見せるだけで、承認済みチケットは置かない。端末の壁は要らない。

    JSON の形は README「承認の JSON」。一覧が空でも 0 で返す。拡張は `batch` が空なら
    「承認待ちは無い」と出す。`only` はボードの絞り込みで見えている分（`--agree` と
    同じ意味）。見せる一覧と承認する対象が同じ絞りを通るようにする。
    """
    verdict = judge_approval(read_fs(conf, root), only)
    stderr.write(verdict.messages)
    _say_elsewhere(stderr, verdict.gathered)
    gathered = verdict.gathered
    if gathered.refused:
        return 1
    if not as_json:
        if gathered.note:
            stdout.write(gathered.note + "\n\n")
        stdout.write(gathered.text + "\n")
        return 0
    stdout.write(
        json.dumps(agree.preview_body(root, gathered, verdict.digest), ensure_ascii=False) + "\n"
    )
    return 0


def verify(
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    as_json: bool,
    only: list[str] | None = None,
) -> int:
    """`--agree --preview --verify`。いま `--agree` を打てば通るかを、置かずに返す。

    エージェントが提案を書いたあと、ユーザに承認を頼む前に自分で確かめるための枝
    （REQ-APV-13）。置かないところも端末を求めないところも `--preview` と同じで、
    違うのは「通るかどうか」を終了コードと本文で言うこと。

    答えを `--preview` 自身に持たせない理由。読む側（VS Code のボード拡張）は 0 以外を
    失敗として扱うので、承認の対象にしない提案が 1 件あるだけで一覧を出せなくなる。
    見せる枝と確かめる枝を分け、判定そのものは `gather` の 1 か所に置く。

    通る = 指定したもの（指定が無ければ承認待ち全部）が、そのまま承認の対象に入る。
    次のどれかがあれば通らない。

    - 絞りが通らない（承認待ちに無い識別子、親の改版を外した子）
    - 承認待ちが 1 件も無い。承認を頼む前の確認としては失敗で、
      提案の置き場を間違えた回がここに出る
    - 承認の対象にしない提案がある（`rejected`）

    落とさないものが 2 つある。どちらも `--agree` が落とさないもので、ここで落とすと
    「確かめは『いいえ』なのに承認は通る」という食い違いになる。

    - 範囲の超過（`overflow`）。承認は止まらず、判定が切り詰めるだけ。承認しても
      書けない場所が残るのは伝える値打ちがあるから、その行につけて見せる
    - 読めない提案（`problems`）。走査は絞る前の全ツリーを見るので、他のセッションの
      書きかけ 1 本で、自分の提案が通るのに「直せ」と言われることになる。
      出さずに済ませることはせず、件数と文面を本文に出す（自分が書いた 1 本かもしれないので）

    返すのは「はい」（0）か「いいえ」（`modes.EXIT_ANSWER_NO`）。使い方と設定の誤りで返す
    1 とは分ける。同じ値にすると、打ち方を間違えた回と提案が落ちる回が読む側から
    区別できず、直すものが無いのに提案を直しに行くことになる。
    """
    judged = judge_approval(read_fs(conf, root), only)
    stderr.write(judged.messages)
    if as_json:
        # テキストの本文には `verify_verdict` が名指しの段を入れる。JSON の本体には無いので
        # 標準エラーへ出す。
        _say_elsewhere(stderr, judged.gathered)
    gathered = judged.gathered
    verdict = agree.verify_verdict(gathered, conf.tickets)
    # 新規の親のブランチ名が既にあるブランチと同じか。warn なので答えは変えない。
    fresh = [c.ticket for c in gathered.batch if not c.is_revision and not c.ticket.is_child]
    branches = agree_candidates.existing_branch_warnings(root, conf, fresh, [], [], [])
    if as_json:
        body = agree.preview_body(root, gathered, judged.digest)
        body["verify"] = {"ok": verdict.ok, "reason": verdict.reason}
        body["branch_warnings"] = branches
        stdout.write(json.dumps(body, ensure_ascii=False) + "\n")
    else:
        stdout.write(verdict.text)
        if branches:
            stdout.write("\n既にあるブランチと同じ名前の親（warn。承認は止めない）:\n")
            for line in branches:
                stdout.write(f"  - {line}\n")
    return modes.EXIT_OK if verdict.ok else modes.EXIT_ANSWER_NO


def approve_yes(
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    expected: list[str],
    as_json: bool,
    only: list[str] | None = None,
    digest: str = "",
) -> int:
    """`--agree --yes <識別子,…> --digest <ダイジェスト> [<絞り>...]`。

    拡張のオーバーレイで押した承認。

    端末の壁は通らない。代わりに、見せた一覧と今の一覧が同じであることを求める。
    拡張が見せたあとに提案が増えていれば承認せず、食い違いを返す。見ていない
    ものを承認する経路を使えなくするため。識別子に加えて、見せた承認画面の本文・判定が読んだ中身・
    承認済みチケットに書き込む中身のダイジェスト（`digest`）も比べる。識別子が同じでも、見せたあとに提案の範囲や計画、
    画面に出ない欄（`issue` など）が書き換われば承認しない。
    ダイジェストが無ければ承認しない。

    引数は 2 つに分かれる。`--yes` は「オーバーレイに出ていた識別子」で、後ろに並べる語は
    「そのとき掛けていた絞り」（`--agree --preview` に渡したものと同じ）。分けないと検査が
    必ず通る。絞りだけで対象を狭めて、その狭めた対象と見せた識別子を比べると、いつでも一致する。
    絞りは preview と同じものを通し、比べるのは「その絞りで今できる一覧」と「見せた識別子」。
    """
    wanted = sorted({s.strip() for s in expected if s.strip()})
    narrowed = [s.strip() for s in (only or []) if s.strip()]
    shown = digest.strip()
    if not shown:
        stderr.write(
            "ccnavi: --yes には --digest（見せた承認画面の本文・判定が読んだ中身・"
            "承認済みチケットに書き込む中身のダイジェスト）が要る\n"
        )
        return 1
    # 絞りが通らなかった（承認待ちに無い識別子が入っている、親の改版を外した）ときは、
    # ボードが古い。拡張には食い違いとして返し、一覧を読み直させる（`judge_approval` が比べる）。
    snapshot = read_fs(conf, root)
    verdict = judge_approval(snapshot, narrowed, shown_ids=wanted, shown_digest=digest)
    stderr.write(verdict.messages)
    _say_elsewhere(stderr, verdict.gathered)
    gathered = verdict.gathered
    if verdict.mismatch is not None:
        current = verdict.mismatch["current"]
        if as_json:
            body = {"version": agree.AGREE_VERSION, "mismatch": verdict.mismatch}
            stdout.write(json.dumps(body, ensure_ascii=False) + "\n")
        if wanted != current:
            stderr.write(
                "ccnavi: 見せた一覧と今の一覧が違う（見せた: "
                f"{', '.join(wanted) or '(無し)'} / 今: {', '.join(current) or '(無し)'}）。"
                "見直してから承認してください\n"
            )
        else:
            stderr.write(
                "ccnavi: 見せた承認画面の本文・判定が読んだ中身・承認済みチケットに書き込む中身が、"
                "今のものと違う（識別子は同じで、提案か、判定が読んだ承認済みチケット・マーカーなどの"
                "中身が変わった）。見直してから承認してください\n"
            )
        return 1
    if not gathered.batch:
        stderr.write("ccnavi: 承認するものが無い\n")
        return 1

    lines = io.StringIO()
    applied = write_fs(lines, stderr, plan(snapshot, verdict).planned)
    if applied.code != 0:
        # 途中で止まった。置いたものはそのまま残るので、どこまで置いたかを返す。それを言わずに
        # 失敗を返すと、ユーザは「何も起きていない」と読む（README「承認の JSON」の `partial`）。
        if as_json:
            body = {
                "version": agree.AGREE_VERSION,
                "partial": {
                    "placed": applied.placed,
                    "ticket": applied.stopped_at,
                    "reason": applied.reason,
                    # 止まるまでに出た行（マーカーを消した、改版した）。端末は stdout で見えるが、
                    # 拡張はこの JSON しか見ないので、同じものを渡す。
                    "lines": [line for line in lines.getvalue().splitlines() if line.strip()],
                },
            }
            stdout.write(json.dumps(body, ensure_ascii=False) + "\n")
        else:
            stdout.write(lines.getvalue())
        return applied.code
    tickets = [c.ticket for c in gathered.batch]
    revisions = {c.ticket.ticket for c in gathered.batch if c.is_revision}
    prompt = agree_screen.approved_text(tickets, revisions, root)
    if not as_json:
        stdout.write(lines.getvalue())
        return 0
    body = {
        "version": agree.AGREE_VERSION,
        "approved": [t.ticket for t in tickets],
        "copies": [
            approval.copy_path(
                approval.home_dir(conf, root, t.ticket, t.parent, t.tree_root, project=t.project),
                t.ticket,
            )
            for t in tickets
        ],
        "lines": [line for line in lines.getvalue().splitlines() if line.strip()],
        "prompt": prompt,
    }
    stdout.write(json.dumps(body, ensure_ascii=False) + "\n")
    return 0


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


def confirm(
    snapshot: Snapshot,
    parent_id: str,
    phase_no: int,
    result,
    changed_since_request: str,
) -> Checked:
    """レビュー済みにしてよいかを見て、通れば書くもの（子を `done/` へ、
    レビュー済みのマーカー）を並べる。

    `result` はホストから取得した結果（`review_host.Result`、`ccnavi-review.sh` が組む形）。
    読めなかったときの扱い（`--result` が無い、読めない）は読む側（手元は `confirm_local`）が持つ。
    `changed_since_request` は依頼の後にユーザが見るものが動いたかの説明（空なら動いていない）。
    手元は git の差分、Chrome は compare API から作る。
    """
    conf, root = snapshot.conf, snapshot.root
    parent = _open_parent(root, conf, parent_id)
    if parent is None:
        return Checked([f"ccnavi: 親 {parent_id} の承認済みチケットが作業中に無い"], None)
    # フェーズの引き方と「依頼し直す」案内の文面は review の非公開の関数を使う（手元の
    # confirm と同じ文面にするため。review の中だけの約束なので公開名にはしない）。
    ph = review._phase(root, conf, parent, phase_no)
    if ph is None:
        return Checked([f"ccnavi: {parent.ticket} にフェーズ {phase_no} の子が無い"], None)
    if approval_marks.MARK_REVIEWED in ph.marks:
        # 重ね打ちでマーカーと履歴を書き直さない（`_already_requested` と同じ文面の形）
        return Checked([f"ccnavi: フェーズ {phase_no} はレビュー済み"], None)
    requested_mark = ph.marks.get(approval_marks.MARK_REQUESTED)
    if requested_mark is None:
        return Checked(["ccnavi: 依頼の記録が無い。先に request してください"], None)
    if changed_since_request:
        return Checked(
            [
                f"ccnavi: {changed_since_request}。ユーザが見たものと今の HEAD が違う。"
                f"{review._redo_request(root, phase_no)}"
            ],
            None,
        )
    problems = review.matching_problems(result, requested_mark)
    if problems:
        return Checked(problems, None)
    problems = review.review_problems(root, conf, parent, phase_no, result)
    if problems:
        return Checked(problems, None)
    stamp = snapshot.stamp or fsio.stamp()
    notes = io.StringIO()
    # 履歴の経路は Snapshot の経路（Chrome なら chrome）。取り下げと同じ形。アカウントと
    # 拡張の版も履歴に足す（分かるときだけ。手元で引けなかったときは前と同じ中身）。
    actor = snapshot.actor
    with (
        fsio.staging() as stage,
        fsio.clock(stamp),
        history.session(actor.via or history.via(), notes, actor.account, actor.version),
    ):
        stopped = review.settle_and_mark(
            stage, root, conf, parent, ph, reviewed_mark(result.mr.number, [], snapshot.actor)
        )
    if notes.getvalue():
        return Checked(_unwritten(notes.getvalue()), None)
    return Checked([], Changes(agree.Planned(stage, stopped), root, conf))


def _unwritten(text: str) -> list[str]:
    """並べる段で履歴を書けないと分かった知らせ。書いても履歴が残らないので、error として返す。

    手元の Writer(FS) なら警告で続ける所だが、ここで分かるのは書く前（識別子の形が違う、
    リンクになっている）なので、書かずに止めて直させる。
    """
    return [line.replace("ccnavi: 警告: ", "ccnavi: ", 1) for line in text.splitlines() if line]


# `confirm_local` は前の `review.confirm` を移したもの。手元の入力の読み方（cwd の親、git の
# 差分、`--result` で取得した結果）は review の非公開の関数をそのまま使う。
# review だけが持つ読み方で、外に出す値打ちが無いので公開名にはしない。
def confirm_local(
    stdout: TextIO,
    stderr: TextIO,
    root: str,
    conf: settings.Settings,
    cwd: str,
    phase_no: int,
    result_path: str,
    actor: str = "",
) -> int:
    """依頼の後を見る。通ればマーカーを置いて先へ進めるようになる。

    検査と書くものの並べ方はコア（`confirm`）。ここは手元の入力
    （cwd の親、git の差分、`--result` で取得した結果）を読んで渡し、
    並べたものを書く（Writer(FS)）。Chrome の「レビュー済み」も同じコアを通る。
    前は review.py にあった（コアより下の段に置くと review → core → review の循環になる）。

    `actor` は `ccnavi-review.sh` がトークンの持ち主を引いて `--actor` で渡すアカウント。
    あればマーカーに `actor` と `via: cli` を書く。空（引けなかった）
    ならマーカーも履歴も前と同じ。
    """
    found = review._parent_phase(stderr, root, conf, cwd, phase_no)
    if found is None:
        return 1
    parent, ph = found
    requested_mark = ph.marks.get(approval_marks.MARK_REQUESTED)
    moved = ""
    result = None
    # 読む順は前と同じ。依頼の記録が無ければ差分も取得した結果も見ない。
    # 動いていれば取得した結果を読まない。レビュー済みなら差分も取得した結果も読まない
    # （コアが「レビュー済み」で止める。Chrome と同じ文面）
    if requested_mark is not None and approval_marks.MARK_REVIEWED not in ph.marks:
        moved = review._moved_since_request(
            tree.worktree_path(root, parent.ticket), conf, requested_mark
        )
        if not moved:
            result = review._result(stderr, result_path)
            if result is None:
                return 1
    who = Actor(actor, history.VIA_CLI) if actor else Actor()
    checked = confirm(read_fs(conf, root, actor=who), parent.ticket, phase_no, result, moved)
    for line in checked.problems:
        stderr.write(line + "\n")
    if checked.changes is None:
        return 1
    return write_fs(stdout, stderr, checked.changes.planned).code


def reviewable(snapshot: Snapshot, parent_id: str) -> list[dict]:
    """依頼済みで、まだレビュー済みでないフェーズ。Chrome のボードが出す候補。

    並べるだけで、通るかは見ない（通るかは `confirm` がホストから取得した結果で決める）。
    答えは `{phase, mr, host, children}` のリスト。`mr` と `host` は依頼のマーカーの値。
    """
    parent = _open_parent(snapshot.root, snapshot.conf, parent_id)
    if parent is None:
        return []
    out = []
    for ph in phase.phases_of(snapshot.root, snapshot.conf, parent.ticket):
        mark = ph.marks.get(approval_marks.MARK_REQUESTED)
        if mark is None or approval_marks.MARK_REVIEWED in ph.marks:
            continue
        try:
            mr = int(mark.get("mr") or 0)
        except (TypeError, ValueError):
            mr = 0
        out.append(
            {
                "phase": ph.number,
                "mr": mr,
                "host": str(mark.get("host") or ""),
                "children": [t.ticket for t in ph.tickets],
            }
        )
    return out


def _requested_mark(snapshot: Snapshot, parent_id: str, phase_no: int) -> dict | None:
    """依頼のマーカー。親・フェーズ・依頼の記録のどれかが無ければ None。"""
    parent = _open_parent(snapshot.root, snapshot.conf, parent_id)
    ph = review._phase(snapshot.root, snapshot.conf, parent, phase_no) if parent else None
    return ph.marks.get(approval_marks.MARK_REQUESTED) if ph is not None else None


def requested_head(snapshot: Snapshot, parent_id: str, phase_no: int) -> str | None:
    """依頼のマーカーに記録された親の先頭。依頼の記録（か親・フェーズ）が無ければ None。

    Chrome は依頼の後に親のブランチが動いたかを compare API で確かめる。比べる相手は
    TS に読ませず、ここが出す。
    """
    mark = _requested_mark(snapshot, parent_id, phase_no)
    return None if mark is None else str(mark.get("head") or "")


def moved_on_host(
    snapshot: Snapshot, parent_id: str, phase_no: int, head: str, changed: list[str] | None
) -> str:
    """ホストで読んだ親の先頭 `head` と変更の一覧から、依頼の後にユーザが見るものが動いたかを言う。

    手元の confirm と同じ関数（`review.moved_since`）で決める。依頼の記録が無ければ空を返し、
    `confirm` が「依頼の記録が無い」で止める。
    """
    mark = _requested_mark(snapshot, parent_id, phase_no)
    return "" if mark is None else review.moved_since(snapshot.conf, mark, head, changed)


def _open_parent(root: str, conf: settings.Settings, parent_id: str) -> ticket_model.Ticket | None:
    """作業中の親の承認済みチケット（本物とする側）。`phase.parent_for_cwd` と同じ引き方。"""
    open_copies, _ = approval.scan(conf, root)
    found = tree.lookup(approval_checks.by_id(open_copies), parent_id)
    if found is None or found.is_child:
        return None
    return found


# ---- 承認の取り下げ ----------------------------------------------------------------------------


def withdraw(
    snapshot: Snapshot,
    ids: list[str],
    prior_proposals: dict[str, bytes],
    reason: str = "",
) -> Checked:
    """承認を未承認（`todo/`）に戻す。条件は 8.8。通れば書くものを並べる。

    `prior_proposals` は識別子ごとの「承認コミットの親にあった `todo/<識別子>.md` のバイト列」。
    承認コミットを引くのはホストの API を読む側（Chrome）で、引けなかった識別子は渡さない。
    判定は緩めない。どれか 1 つでも条件に当たらなければ何も並べない。

    承認は中身を変えないので、`doing/` の中身が承認コミットの親の提案とバイト単位で同じなら、
    改版も着手もされていない（`_content_problems`）。親は、固定した待ち方
    （`phases/<親>/workflow.yml`）も一緒に消す。
    """
    conf, root = snapshot.conf, snapshot.root
    raw = approval.read_raw(conf, root)
    approved, _ = approval.scan(conf, root, raw=raw)
    closed, _ = approval.scan(conf, root, closed=True, raw=raw)
    review_waiting, _ = approval.scan_review(conf, root, raw=raw)
    proposals, _ = approval.scan_proposals(conf, root, raw.everything)
    open_index = approval_checks.by_id(approved)
    problems: list[str] = []
    wanted = [i for i in dict.fromkeys(ids) if i]
    if not wanted:
        problems.append("取り下げる識別子が無い")
    for ident in wanted:
        problems += [
            f"{ident}: {p}"
            for p in _withdraw_problems(
                conf,
                root,
                ident,
                open_index.get(ident),
                approved + closed + review_waiting,
                proposals,
                prior_proposals,
                compare=True,
            )
        ]
    if problems:
        return Checked(problems, None)
    stamp = snapshot.stamp or fsio.stamp()
    actor = snapshot.actor
    notes = io.StringIO()
    with (
        fsio.staging() as stage,
        fsio.clock(stamp),
        history.session(actor.via or history.via(), notes),
    ):
        stopped = None
        for ident in wanted:
            copy = open_index[ident]
            where = settings.approved_dir(conf, copy.tree_root)
            todo = os.path.join(
                copy.tree_root, conf.tickets.replace("/", os.sep), ticket_model.TODO, ident + ".md"
            )
            with fsio.policy(
                on_fail=fsio.FAIL_STOP, ticket=ident, message="書けない ({reason})", prefix=""
            ):
                fsio.write_bytes(todo, prior_proposals[ident])
                # 消せなければ戻した提案を消して、両方に残さない（`approval.admit` と同じ）。
                with fsio.policy(
                    message="承認済みチケットを doing/ から消せない ({reason})", undo=(todo,)
                ):
                    fsio.unlink(approval.copy_path(where, ident))
                held = approval.workflow_path(where, ident)
                if not copy.is_child and fsio.lexists(held):
                    with fsio.policy(message="待ち方のファイルを消せない ({reason})"):
                        fsio.unlink(held)
            history.note(
                where,
                ident,
                history.KIND_WITHDRAWN,
                ticket_model.DOING,
                ticket_model.TODO,
                actor=actor.account,
                version=actor.version,
                reason=reason,
            )
            stage.line(f"  {ident} の承認を取り下げた（doing/ → todo/）")
    if notes.getvalue():
        return Checked(_unwritten(notes.getvalue()), None)
    return Checked([], Changes(agree.Planned(stage, stopped), root, conf))


def withdrawable(snapshot: Snapshot, family: str) -> list[tuple[str, str, list[str]]]:
    """親子のチケットの作業中（`doing/`）のチケットごとの (識別子, 題, 取り下げられない理由)。

    Chrome のボードが「取り下げ」を出すかを決めるのに使う。条件は `withdraw` と同じで、
    承認コミットの親の提案（`prior_proposals`）だけは引ける前提で見る（引くのはホストを読む側。
    引けなければ `withdraw` がその理由で止める）。一覧は承認コミットを引く前に組むので、
    中身の一致（`_content_problems`）は見ない。承認の記録の無い新しい形か、古い形で記録の
    条件を満たすものを出す（粗く出しても、押したときに `withdraw` が新しい Snapshot で
    全部を見直して止める。判定は緩めない）。
    """
    conf, root = snapshot.conf, snapshot.root
    raw = approval.read_raw(conf, root)
    approved, _ = approval.scan(conf, root, raw=raw)
    closed, _ = approval.scan(conf, root, closed=True, raw=raw)
    review_waiting, _ = approval.scan_review(conf, root, raw=raw)
    proposals, _ = approval.scan_proposals(conf, root, raw.everything)
    everything = approved + closed + review_waiting
    out = []
    for copy in sorted(approved, key=lambda t: t.ticket):
        if (copy.parent or copy.ticket) != family or copy.tree != family:
            continue
        problems = _withdraw_problems(
            conf, root, copy.ticket, copy, everything, proposals, {copy.ticket: b""}
        )
        out.append((copy.ticket, copy.title, problems))
    return out


def _withdraw_problems(
    conf: settings.Settings,
    root: str,
    ident: str,
    copy: ticket_model.Ticket | None,
    everything: list[ticket_model.Ticket],
    proposals: list[ticket_model.Ticket],
    prior_proposals: dict[str, bytes],
    compare: bool = False,
) -> list[str]:
    """取り下げられない理由。`compare` なら中身の一致（`_content_problems`）も見る（書く側）。"""
    if copy is None:
        return ["承認済みチケットが作業中（doing/）に無い"]
    home = copy.parent or copy.ticket
    if copy.tree != home:
        return [f"親のブランチ {home} の doing/ に無い（{copy.tree or 'ワークスペースルート'}）"]
    found: list[str] = []
    if copy.blocked:
        # 親子のチケットが決まらない・親のブランチの外のチケットなど。
        # 状態の操作と同じく止める
        found.append(copy.blocked)
    if approval_checks.has_record(copy):
        # 承認で記録（`ccnavi_approved`）を書いていた頃の古い形。承認で欄が足されているので
        # 承認コミットの親の提案とは一致しない。記録の欄で決める（前の条件のまま）。
        meta = copy.raw[ticket_model.APPROVAL_KEY]
        # 今の改版は時刻を書かないが、待ち方のファイルを必ず書く。古い形の承認は待ち方を
        # 欄に持ち、ファイルを持たないので、ファイルがあれば改版したものとして止める。
        held = approval.workflow_path(settings.approved_dir(conf, copy.tree_root), ident)
        if (
            meta.get("revised_at")
            or meta.get("feedback_at")
            or (not copy.is_child and fsio.lexists(held))
        ):
            found.append("改版した承認は取り下げられない（改版で動いたものを戻せない）")
        if meta.get("followup_of") or not meta.get("source_path"):
            found.append(
                "続きの子（followup）か、承認の記録の無い承認済みチケットは取り下げられない"
            )
    elif copy.raw.get(approval.FOLLOWUP_KEY):
        found.append(
            "続きの子（followup）は提案を経ずに作ったチケットなので、戻す提案が無い。"
            "取り下げられない"
        )
    if copy.started_at:
        found.append(
            "着手済み。取りやめるなら "
            f"`ccnavi-ticket.sh cancel {ident} --reason <理由>` をエージェントに頼んでください"
            "（done/ に取り消しの記録が残る）"
        )
    if not copy.is_child:
        if any(t.parent == ident for t in everything):
            found.append("子の承認済みチケットがある")
        if any(t.parent == ident and t.state == ticket_model.TODO for t in proposals):
            found.append("todo/ に子の提案がある")
        marks_dir = os.path.join(
            settings.approved_dir(conf, copy.tree_root), approval_marks.PHASES_DIR, ident
        )
        try:
            # 待ち方のファイルはマーカーではない（承認で置く）。中身は `_content_problems` が見る。
            if [n for n in fsio.listdir(marks_dir) if n != approval_marks.WORKFLOW_FILE]:
                found.append(f"{approval_marks.PHASES_DIR}/{ident}/ にマーカーがある")
        except FileNotFoundError:
            pass
        except OSError as exc:
            # 読めないなら、無いとは言えない（取り下げを緩めない）。
            found.append(f"{approval_marks.PHASES_DIR}/{ident}/ を読めない ({exc})")
    todo = os.path.join(
        copy.tree_root, conf.tickets.replace("/", os.sep), ticket_model.TODO, ident + ".md"
    )
    if fsio.lexists(todo):
        found.append("todo/ に同じ識別子の提案がある（戻す先が塞がっている）")
    if ident not in prior_proposals:
        found.append("承認コミットの親に提案が無い（承認コミットを引けない）")
    elif compare and not approval_checks.has_record(copy):
        found += _content_problems(conf, root, copy, prior_proposals[ident])
    return found


def _content_problems(
    conf: settings.Settings, root: str, copy: ticket_model.Ticket, prior: bytes
) -> list[str]:
    """新しい形の承認済みチケットが、承認したときのままか。

    承認は提案を中身を変えずに動かすので、`doing/` の中身が承認コミットの親の提案（`prior`）と
    バイト単位で同じなら、改版も着手もされていない。改行を揃えて読む `fsio.read_text` は使わない
    （CRLF と LF の違いも、承認の後の書き換えとして数える）。

    親は加えて、`phases/<親>/workflow.yml` の中身が、`prior` と今の `phases.yml` から計算した
    待ち方（`workflow.compute`）と同じであることを求める。待ち方だけの改版は `doing/` を変えない
    ので、ここで見る。ファイルが無いなら通す。改版は必ずこのファイルを書くので、無いのは
    待ち方を書かない承認（計画の無い親か、手で動かした承認）で、待ち方の改版は起きていない。
    承認のあとに `phases.yml` が変わっていれば計算が変わって一致せず、止める側に倒れる。
    """
    current = fsio.read_bytes(copy.path)
    if current is None:
        return ["承認済みチケットを読めない"]
    if current != prior:
        return [
            "承認のあとに承認済みチケットの中身が変わった（改版か着手）。"
            "承認コミットの親の提案と同じでないものは取り下げられない"
        ]
    if copy.is_child:
        return []
    where = settings.approved_dir(conf, copy.tree_root)
    path = approval.workflow_path(where, copy.ticket)
    held = fsio.read_bytes(path)
    if held is None and fsio.lexists(path):
        return [
            f"{approval_marks.PHASES_DIR}/{copy.ticket}/{approval_marks.WORKFLOW_FILE} を読めない"
        ]
    if held is None:
        # 手で動かした承認（計画を持つ親でも待ち方のファイルを書かない）か、計画の無い親。
        # 改版は必ず待ち方のファイルを書くので、無ければ待ち方の改版は起きていない。
        return []
    try:
        proposed, _ = ticket_mod.parse(prior.decode("utf-8"))
    except UnicodeDecodeError:
        proposed = None
    if proposed is None:
        return ["承認コミットの親の提案をチケットとして読めないので、待ち方を確かめられない"]
    types = phase.load_types(conf, root, copy.project)
    if held != approval.workflow_bytes(workflow.compute(proposed, types)):
        return [
            "承認のあとに待ち方が変わった（待ち方の改版か、phases.yml の変更）。"
            "承認したときの待ち方と同じでないものは取り下げられない"
        ]
    return []
