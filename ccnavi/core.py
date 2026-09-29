"""判定のコアと差し口（ADR-0093 の 6 章。段階 2a）。

承認・承認の取り下げ・レビュー済みを、同じ形の 3 段に分ける。

    Snapshot（入力）→ judge_approval / withdraw / confirm（判定）→ Changes（書くもの）→ Writer

- **Reader**: 段階 2a では Snapshot の中身はファイルシステムから読む（Reader(FS)）。手元は
  作業ツリー、Chrome は MEMFS に組んだ仮のツリー（ADR-0093 の 8.2「段階 1〜2a は MEMFS」）。
  ブランチごとの blob の表から読む形（`NOT_FETCHED`）は、権威を `P` に固定する段階 2c 以降
- **判定**: `judge_approval`（6.2 の `judge`。実行前の判定のモジュール `ccnavi.judge` と
  紛れないように名前を変えた）は承認の対象と画面と指紋を、`withdraw` と `confirm` は通らない
  理由を返す
- **Changes**: 書き込みを値として並べたもの（`plan`）。書くときの落ち方（止める・言って続ける）
  も添えてある。`per_branch` はブランチごとの create / update / delete で、Chrome はこれを
  1 コミットにする（段階 3）
- **Writer(FS)**: `write_fs` が Changes をディスクに書く。fsio を通るので、C1 の記録層
  （`fsio.recording`）がそのまま効く
- **Clock**: 時刻は `Snapshot.stamp`（空なら今）。plan の間は `fsio.clock` で固定し、承認の
  記録と跡に同じ時刻を書く

判定そのもの（`approval.gather` / `candidates` / `waiting`、`review` の検査）は今のコードを
そのまま通る。ここは入口と出口の形を揃えるだけで、判定は変えない。

手元の入口（`--approve` の 4 つの枝と `review confirm`）もここに置く。コアを通す入口を
コアより下の段（approval・review）に置くと、import が循環する（tests/core/test_module_layers.py）。
"""

from __future__ import annotations

import io
import json
import os
from dataclasses import dataclass, field
from typing import TextIO

from . import approval, fsio, history, modes, review, rules, settings, tree
from . import ticket as ticket_mod

# ---- 入力 -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Actor:
    """誰がどの経路で動かしたか。`account` はホストのアカウント名か手元の git の user（8.9）。

    空なら書かない（段階 2a の手元の経路は、印と跡の中身を今のままにする）。
    """

    account: str = ""
    via: str = ""
    version: str = ""


@dataclass
class Snapshot:
    """判定の入力（6.2）。段階 2a はファイルシステムから読む（Reader(FS)）。"""

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

    `mismatch` は見せた一覧・指紋（`shown_ids` / `shown_digest`）と今のものが違うときの中身。
    見せたものを渡さなければ None。
    """

    gathered: approval.Gathered
    screen_text: str
    digest: str
    messages: str
    mismatch: dict | None = None
    # 判定が読んだ中身（`<ブランチ>:<相対パス>` → 中身の指紋）。指紋はこれと本文から作る。
    read_set: dict[str, str] = field(default_factory=dict)

    @property
    def batch(self) -> list[approval.Candidate]:
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


def judge_approval(
    snapshot: Snapshot,
    only: list[str] | None = None,
    shown_ids: list[str] | None = None,
    shown_digest: str | None = None,
) -> Verdict:
    """承認の対象を組み、画面の本文と指紋を出す。見せたものを渡せば、今のものと比べる。

    比べ方は前の `--approve --yes` と同じ。絞り（`only`）が通らなかったときは、絞らない
    一覧を今の一覧として比べる（ボードが古い）。識別子と指紋のどちらかが違えば `mismatch`。
    """
    err = io.StringIO()
    # 判定が読んだ中身（read_set）を控え、指紋に入れる（ADR-0093 の 6.2。段階 2c）。
    with fsio.reading() as seen:
        gathered = approval.gather(err, snapshot.conf, snapshot.root, only)
        shown = gathered
        if shown_ids is not None and gathered.refused:
            shown = approval.gather(err, snapshot.conf, snapshot.root)
    read = approval.read_set(snapshot.conf, snapshot.root, seen)
    read.update(approval.settings_read_set(snapshot.conf, snapshot.root))
    text = shown.text
    digest = approval.approval_digest(text, shown.batch, read)
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
    """書くものの並び（6.2 の plan の答え）。

    `planned.stage.items` が書く順（書き込みと見せる行）、`planned.stopped` は途中で止まった
    ところ（止まるまでの分は書く）。`per_branch` はブランチごとのファイルの増減。
    """

    planned: approval.Planned
    root: str
    conf: settings.Settings

    @property
    def lines(self) -> list[str]:
        """人に見せる行（標準出力の分）。書き込みが落ちたときの行は入らない。"""
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
    """承認の書き込みを並べる。書かない（fsio の控える段）。"""
    stamp = snapshot.stamp or fsio.stamp()
    planned = approval.plan_batch(snapshot.root, snapshot.conf, verdict.batch, stamp)
    return Changes(planned, snapshot.root, snapshot.conf)


# ---- Writer(FS) -----------------------------------------------------------------------------


def write_fs(stdout: TextIO, stderr: TextIO, planned: approval.Planned) -> approval.Applied:
    """並べた書き込みをディスクに書く（Writer(FS)）。前の `_apply` と同じ落ち方をする。

    - `FAIL_STOP`: `undo` を消し、`ccnavi: <識別子>: <理由>` を言って止める。置いたものは戻さない
    - `FAIL_WARN`: 同じ形で言って続ける
    - `FAIL_LINE`: 行を出し、同じ組の残り（続く書き込みと行）を飛ばす
    - `FAIL_QUIET`: 黙って続ける
    - `FAIL_HISTORY`: 跡の書けなかった知らせに溜める（入口が警告で出す）

    並べる段で止まっていたら（`planned.stopped`）、並べた分を書いてから同じ形で言って止める。
    `view_only` の書き込みは書かない。`Call` は同じ組で書けた名札（`tag`）を渡して呼び、
    返った行を出す（マーカーの消去の行と跡は、実際に消せた種類で書く）。
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
            stderr.write(head + message + "\n")
            return approval.Applied(1, placed, rule.ticket, message)
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
        return approval.Applied(1, placed, ticket_id, reason)
    return approval.Applied(0, placed)


def _write_op(op: fsio.Op) -> str:
    """控えた書き込み 1 つをディスクに書く。書けたら空文字、駄目なら理由。"""
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


# ---- 手元の入口（`--approve`。端末・ボードの `--yes`・`--preview`・`--verify`） ------------------
#
# 前は approval.py に居た。コアを通す入口なので、コアより下の段（approval）には置かない
# （approval → core → approval の循環になる。tests/core/test_module_layers.py）。


def approve(
    stdin: TextIO,
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    rule_set: rules.RuleSet,
    root: str,
    only: list[str] | None = None,
) -> int:
    """未承認の提案をまとめて人に見せ、承認されたら承認済みチケットを置く。

    エージェントではなく人が端末から打つ経路。提案を書き直す道は用意しない。
    チケットを書くのはエージェントの仕事で、承認する場所で書き替えられると、
    承認した人が承認したものの作者になる。

    承認の対象は「いま承認待ちのもの全部」。親が 1 本、その下の子が複数、という形が普通。
    子は親の部分集合なので、新たに書けるようになる領域は親の分だけ。

    親の改版（計画の変更）も一緒に承認の対象に入る。承認済みチケットは動かないのが原則で、改版はその
    唯一の例外（設計 9.7）。変えられるのは `plan` と `feedback` だけ。

    `only` は承認の対象を識別子で絞る（`ccnavi --approve <識別子>...`）。VS Code 拡張の
    ボードが絞り込みで見えている分だけを渡す。絞りは対象を狭めるだけで、絞らないときに
    落ちるものを通してはいけない。だから、承認待ちに無い識別子が混じっていたら
    何も承認しない（ボードが古いときに、見せた以外のものを通さないため）。親の
    改版が承認待ちなのに対象から外した子も何も承認しない（外すと旧計画で検証される）。
    絞った対象に入らない親を持つ子は「親が承認されていない」で落ちる。

    判定と書き込みはコア（`judge_approval` → `plan` → `write_fs`、ADR-0093 の 6.2）。
    """
    snapshot = read_fs(conf, root)
    verdict = judge_approval(snapshot, only)
    stderr.write(verdict.messages)
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
            "直して出し直すこと（通ったぶんの承認済みチケットは置いた）\n"
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
    """`--approve --preview`。一覧を見せるだけで、承認済みチケットは置かない。端末の壁は要らない。

    JSON の形は README「承認の JSON」。一覧が空でも 0 で返す。拡張は `batch` が空なら
    「承認待ちは無い」と出す。`only` はボードの絞り込みで見えている分（`--approve` と
    同じ意味）。見せる一覧と承認する対象が同じ絞りを通るようにする。
    """
    verdict = judge_approval(read_fs(conf, root), only)
    stderr.write(verdict.messages)
    gathered = verdict.gathered
    if gathered.refused:
        return 1
    if not as_json:
        if gathered.note:
            stdout.write(gathered.note + "\n\n")
        stdout.write(gathered.text + "\n")
        return 0
    stdout.write(
        json.dumps(approval.preview_body(root, gathered, verdict.digest), ensure_ascii=False) + "\n"
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
    """`--approve --preview --verify`。いま `--approve` を打てば通るかを、置かずに返す。

    エージェントが提案を書いたあと、人に承認を頼む前に自分で確かめるための枝
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

    落とさないものが 2 つある。どちらも `--approve` が落とさないもので、ここで落とすと
    「確かめは『いいえ』なのに承認は通る」という食い違いになる。

    - 範囲の超過（`overflow`）。承認は止まらず、判定が切り詰めるだけ。承認しても
      書けない場所が残るのは伝える値打ちがあるから、その行に添えて見せる
    - 読めない提案（`problems`）。走査は絞る前の全ツリーを見るので、他のセッションの
      書きかけ 1 本で、自分の提案が通るのに「直せ」と言われることになる。黙らせはせず、
      件数と綴りを本文に出す（自分が書いた 1 本かもしれないので）

    返すのは「はい」（0）か「いいえ」（`modes.EXIT_ANSWER_NO`）。使い方と設定の誤りで返す
    1 とは分ける。同じ値にすると、打ち方を間違えた回と提案が落ちる回が読む側から
    区別できず、直すものが無いのに提案を直しに行くことになる。
    """
    judged = judge_approval(read_fs(conf, root), only)
    stderr.write(judged.messages)
    gathered = judged.gathered
    verdict = approval.verify_verdict(gathered, conf.tickets)
    if as_json:
        body = approval.preview_body(root, gathered, judged.digest)
        body["verify"] = {"ok": verdict.ok, "reason": verdict.reason}
        stdout.write(json.dumps(body, ensure_ascii=False) + "\n")
    else:
        stdout.write(verdict.text)
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
    """`--approve --yes <識別子,…> --digest <指紋> [<絞り>...]`。拡張のオーバーレイで押した承認。

    端末の壁は通らない。代わりに、見せた一覧と今の一覧が同じであることを求める。
    拡張が見せたあとに提案が増えていれば承認せず、食い違いを返す。見ていない
    ものを承認する道を塞ぐため。識別子に加えて、見せた承認画面の本文・判定が読んだ中身・
    承認済みチケットに写る中身の指紋（`digest`）も比べる。識別子が同じでも、見せたあとに提案の範囲や計画、
    画面に出ない欄（`issue` など）が書き換われば承認しない。
    指紋が無ければ承認しない。

    引数は 2 つに分かれる。`--yes` は「オーバーレイに出ていた識別子」で、後ろに並べる語は
    「そのとき掛けていた絞り」（`--approve --preview` に渡したものと同じ）。分けないと検査が
    素通りする。絞りだけで対象を狭めて、その狭めた対象と見せた識別子を比べると、いつでも一致する。
    絞りは preview と同じものを通し、比べるのは「その絞りで今できる一覧」と「見せた識別子」。
    """
    wanted = sorted({s.strip() for s in expected if s.strip()})
    narrowed = [s.strip() for s in (only or []) if s.strip()]
    shown = digest.strip()
    if not shown:
        stderr.write(
            "ccnavi: --yes には --digest（見せた承認画面の本文・判定が読んだ中身・"
            "承認済みチケットに写る中身の指紋）が要る\n"
        )
        return 1
    # 絞りが通らなかった（承認待ちに無い識別子が混じっている、親の改版を外した）ときは、
    # ボードが古い。拡張には食い違いとして返し、一覧を読み直させる（`judge_approval` が比べる）。
    snapshot = read_fs(conf, root)
    verdict = judge_approval(snapshot, narrowed, shown_ids=wanted, shown_digest=digest)
    stderr.write(verdict.messages)
    gathered = verdict.gathered
    if verdict.mismatch is not None:
        current = verdict.mismatch["current"]
        if as_json:
            body = {"version": approval.APPROVE_VERSION, "mismatch": verdict.mismatch}
            stdout.write(json.dumps(body, ensure_ascii=False) + "\n")
        if wanted != current:
            stderr.write(
                "ccnavi: 見せた一覧と今の一覧が違う（見せた: "
                f"{', '.join(wanted) or '(無し)'} / 今: {', '.join(current) or '(無し)'}）。"
                "見直してから承認する\n"
            )
        else:
            stderr.write(
                "ccnavi: 見せた承認画面の本文・判定が読んだ中身・承認済みチケットに写る中身が、"
                "今のものと違う（識別子は同じで、提案か、判定が読んだ承認済みチケット・マーカーなどの"
                "中身が変わった）。見直してから承認する\n"
            )
        return 1
    if not gathered.batch:
        stderr.write("ccnavi: 承認するものが無い\n")
        return 1

    lines = io.StringIO()
    applied = write_fs(lines, stderr, plan(snapshot, verdict).planned)
    if applied.code != 0:
        # 途中で止まった。置いたものはそのまま残るので、どこまで置いたかを返す。黙って失敗を
        # 返すと、人は「何も起きていない」と読む（README「承認の JSON」の `partial`）。
        if as_json:
            body = {
                "version": approval.APPROVE_VERSION,
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
    prompt = approval.approved_text(tickets, revisions, root)
    if not as_json:
        stdout.write(lines.getvalue())
        return 0
    body = {
        "version": approval.APPROVE_VERSION,
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


# ---- レビュー済み（8.9） ----------------------------------------------------------------------


def reviewed_mark(
    mr: int, accepted: list[str], actor: Actor | None = None, stamp: str = ""
) -> dict:
    """レビュー済みの印の中身（8.9）。空の欄は書かない（`review.reviewed_mark`）。"""
    who = actor or Actor()
    return review.reviewed_mark(mr, accepted, who.account, who.via, stamp)


@dataclass
class Checked:
    """`confirm` と `withdraw` の答え。`problems` は標準エラーに出す文面（行の並び）。

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
    """レビュー済みにしてよいかを見て、通れば書くもの（子を `done/` へ、レビュー済みの印）を並べる。

    `result` はホストの写し（`review.Result`、`ccnavi-review.sh` が組む形）。読めなかった
    ときの扱い（`--result` が無い、読めない）は読む側（手元は `confirm_local`）が持つ。
    `changed_since_request` は依頼の後に人が見るものが動いたかの説明（空なら動いていない）。
    手元は git の差分、Chrome は compare API から作る（8.9）。
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
    requested_mark = ph.marks.get(approval.MARK_REQUESTED)
    if requested_mark is None:
        return Checked(["ccnavi: 依頼の記録が無い。先に request すること"], None)
    if changed_since_request:
        return Checked(
            [
                f"ccnavi: {changed_since_request}。人が見たものと今の HEAD が違う。"
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
    # 跡の経路は Snapshot の経路（Chrome なら chrome）。取り下げと同じ形（8.9）。
    with (
        fsio.staging() as stage,
        fsio.clock(stamp),
        history.session(snapshot.actor.via or history.via(), notes),
    ):
        stopped = review.settle_and_mark(
            stage, root, conf, parent, ph, reviewed_mark(result.mr.number, [], snapshot.actor)
        )
    if notes.getvalue():
        return Checked(_unwritten(notes.getvalue()), None)
    return Checked([], Changes(approval.Planned(stage, stopped), root, conf))


def _unwritten(text: str) -> list[str]:
    """並べる段で跡を書けないと分かった知らせ。書いても跡が残らないので、error として返す。

    手元の Writer(FS) なら警告で続ける所だが、ここで分かるのは書く前（識別子の形が違う、
    リンクになっている）なので、書かずに止めて直させる。
    """
    return [line.replace("ccnavi: 警告: ", "ccnavi: ", 1) for line in text.splitlines() if line]


# `confirm_local` は前の `review.confirm` を移したもの。手元の入力の読み方（cwd の親、git の
# 差分、`--result` の写し）は review の非公開の関数をそのまま使う。review だけが持つ読み方で、
# 外に出す値打ちが無いので公開名にはしない。
def confirm_local(
    stdout: TextIO,
    stderr: TextIO,
    root: str,
    conf: settings.Settings,
    cwd: str,
    phase_no: int,
    result_path: str,
) -> int:
    """依頼の後を見る。通ればマーカーを置いて先へ進めるようになる。

    検査と書くものの並べ方はコア（`confirm`、ADR-0093 の 8.9）。ここは手元の入力
    （cwd の親、git の差分、`--result` の写し）を読んで渡し、並べたものを書く（Writer(FS)）。
    Chrome の「レビュー済み」も同じコアを通る。前は review.py に居た（コアより下の段に
    置くと review → core → review の循環になる）。
    """
    found = review._parent_phase(stderr, root, conf, cwd, phase_no)
    if found is None:
        return 1
    parent, ph = found
    requested_mark = ph.marks.get(approval.MARK_REQUESTED)
    moved = ""
    result = None
    # 読む順は前と同じ。依頼の記録が無ければ差分も写しも見ない。動いていれば写しを読まない。
    if requested_mark is not None:
        moved = review._moved_since_request(
            tree.worktree_path(root, parent.ticket), conf, requested_mark
        )
        if not moved:
            result = review._result(stderr, result_path)
            if result is None:
                return 1
    checked = confirm(read_fs(conf, root), parent.ticket, phase_no, result, moved)
    for line in checked.problems:
        stderr.write(line + "\n")
    if checked.changes is None:
        return 1
    return write_fs(stdout, stderr, checked.changes.planned).code


def _open_parent(root: str, conf: settings.Settings, parent_id: str) -> ticket_mod.Ticket | None:
    """作業中の親の承認済みチケット（権威のある側）。`phase.parent_for_cwd` と同じ引き方。"""
    open_copies, _ = approval.scan(conf, root)
    found = tree.lookup(approval.by_id(open_copies), parent_id)
    if found is None or found.is_child:
        return None
    return found


# ---- 承認の取り下げ（8.8） --------------------------------------------------------------------


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
    """
    conf, root = snapshot.conf, snapshot.root
    approved, _ = approval.scan(conf, root)
    closed, _ = approval.scan(conf, root, closed=True)
    review_waiting, _ = approval.scan_review(conf, root)
    proposals, _ = ticket_mod.scan(root, conf.tickets, conf.projects)
    open_index = approval.by_id(approved)
    problems: list[str] = []
    wanted = [i for i in dict.fromkeys(ids) if i]
    if not wanted:
        problems.append("取り下げる識別子が無い")
    for ident in wanted:
        problems += [
            f"{ident}: {p}"
            for p in _withdraw_problems(
                conf,
                ident,
                open_index.get(ident),
                approved + closed + review_waiting,
                proposals,
                prior_proposals,
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
                copy.tree_root, conf.tickets.replace("/", os.sep), ticket_mod.TODO, ident + ".md"
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
            history.note(
                where,
                ident,
                history.KIND_WITHDRAWN,
                ticket_mod.DOING,
                ticket_mod.TODO,
                actor=actor.account,
                version=actor.version,
                reason=reason,
            )
            stage.line(f"  {ident} の承認を取り下げた（doing/ → todo/）")
    if notes.getvalue():
        return Checked(_unwritten(notes.getvalue()), None)
    return Checked([], Changes(approval.Planned(stage, stopped), root, conf))


def _withdraw_problems(
    conf: settings.Settings,
    ident: str,
    copy: ticket_mod.Ticket | None,
    everything: list[ticket_mod.Ticket],
    proposals: list[ticket_mod.Ticket],
    prior_proposals: dict[str, bytes],
) -> list[str]:
    if copy is None:
        return ["承認済みチケットが作業中（doing/）に無い"]
    home = copy.parent or copy.ticket
    if copy.tree != home:
        return [f"親のブランチ {home} の doing/ に無い（{copy.tree or 'ワークスペースルート'}）"]
    meta = copy.raw.get(ticket_mod.APPROVAL_KEY)
    meta = meta if isinstance(meta, dict) else {}
    found: list[str] = []
    if meta.get("revised_at") or meta.get("feedback_at"):
        found.append("改版した承認は取り下げられない（改版で動いたものを戻せない）")
    if meta.get("followup_of") or not meta.get("source_path"):
        found.append("続きの子（followup）か、承認の記録の無い承認済みチケットは取り下げられない")
    if copy.started_at:
        found.append(
            "着手済み。取りやめるなら "
            f"`ccnavi-ticket.sh cancel {ident} --reason <理由>` をエージェントに頼む"
            "（done/ に取り消しの記録が残る）"
        )
    if not copy.is_child:
        if any(t.parent == ident for t in everything):
            found.append("子の承認済みチケットがある")
        if any(t.parent == ident and t.state == ticket_mod.TODO for t in proposals):
            found.append("todo/ に子の提案がある")
        marks_dir = os.path.join(
            settings.approved_dir(conf, copy.tree_root), approval.PHASES_DIR, ident
        )
        try:
            if fsio.listdir(marks_dir):
                found.append(f"{approval.PHASES_DIR}/{ident}/ にマーカーがある")
        except FileNotFoundError:
            pass
        except OSError as exc:
            # 読めないなら、無いとは言えない（取り下げを緩めない）。
            found.append(f"{approval.PHASES_DIR}/{ident}/ を読めない ({exc})")
    todo = os.path.join(
        copy.tree_root, conf.tickets.replace("/", os.sep), ticket_mod.TODO, ident + ".md"
    )
    if fsio.lexists(todo):
        found.append("todo/ に同じ識別子の提案がある（戻す先が塞がっている）")
    if ident not in prior_proposals:
        found.append("承認コミットの親に提案が無い（承認コミットを引けない）")
    return found
