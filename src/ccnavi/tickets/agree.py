"""合意の手続き。ユーザが提案に合意する（承認する）までの手続きを持つ。

承認の候補とその検査は `agree_candidates`、承認の画面は `agree_screen`、承認の対象の指紋と
書く本文は `agree_digest` に分けてある。どれも agree を読まない。

置き場は approval、合意の手続きは agree。approval は承認済みチケット・マーカー・子の記録が
どこにどう置かれているかを読み書きするだけで、ここ（agree）を知らない。agree は approval の
置き場を読み、提案を承認済みチケットの置き場へ動かす。向きは agree → approval の 1 本だけ。

## 何をするか

`ccnavi --agree` が呼ぶ手続きの全部。

- 承認の対象を組む（`gather`・`agree_candidates.candidates`）。提案を走査し、
  承認済みチケットと突き合わせ、載せるものと落とすものに分ける。形の検査（`agree_candidates` の
  `validate`・`plan_problems`・`revision_problems`）はここで当てる
- ユーザに見せる（`agree_screen.screen`・`preview_body`）。見せたものと承認するものを
  同じ答えにするため、一覧を組む関数は 1 つ（`gather`）にしてある
- 見せたものから変わっていないかを確かめる（`agree_digest` の `approval_digest`・`read_set`、
  `verify_verdict`）
- 置き場へ動かす（`plan_batch`）。書き込みは approval の置き場の関数を通す。新規の承認は提案の
  中身を変えずに動かすだけで、書き足すものは無い（待ち方は承認済みチケットの計画から都度計算する）
- 承認の事実をモデルに伝える文を組む（`agree_screen.approved_text`。拡張が `--agree --yes` の
  `prompt` で渡す）

判定は承認済みチケットだけを読み、ここを通ったかどうかは見ない（承認で本物とするのは置き場）。置き場を手で
動かす進め方もあるので、判定の側で要る構造の検査は approval の `blocking_problems` に置いてある。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TextIO

from ..infra import fsio, settings
from ..policy import rules
from . import (
    agree_candidates,
    agree_digest,
    agree_screen,
    approval,
    approval_checks,
    approval_marks,
    approval_ops,
    phase,
    ticket_ids,
    ticket_model,
)

# 承認の JSON の版。`--agree --preview --json` と `--agree --yes … --json` が出す。
# VS Code のボード拡張が読み、知らない番号なら読まずに版の違いを言う。
AGREE_VERSION = 1


@dataclass
class Gathered:
    """いま `--agree` が見せる一覧と、その周りのもの。見せる・承認するの両方がここから出る。

    一覧を組む関数を 1 つにしてあるのは、拡張が見せたものと実行ファイルが承認する
    ものを同じ答えにするため。`--explain --json` の `pending_approval` も同じ
    `waiting` を通る。
    """

    batch: list[agree_candidates.Candidate]
    rejected: list[tuple[ticket_model.Ticket, list[rules.Problem]]]
    problems: list[str]
    pool: dict
    nothing_pending: bool
    broken: bool
    # 絞り込み（`only`）が通らなかった理由。空でなければ何も承認しない。
    refused: str = ""
    # 端末に出す 1 行（「承認待ち N 件のうち、指定の M 件だけを承認の対象にする」）。
    note: str = ""
    # 本物とするツリーの外に在る計画の違う版の案内（`approval.revision_elsewhere_text`）。
    # 承認待ちには入れない。書く場所を名指しするためだけに持つ（本文とダイジェストには入れない）。
    elsewhere: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        if self.nothing_pending:
            return "承認待ちのチケットは無い。"
        return agree_screen.screen(self.batch, self.pool)

    @property
    def identifiers(self) -> list[str]:
        return sorted(c.ticket.ticket for c in self.batch)


def gather(
    stderr: TextIO, conf: settings.Settings, root: str, only: list[str] | None = None
) -> Gathered:
    """承認の対象を組む。提案を走査し、承認済みチケットと突き合わせ、載せるものと落とすものに分ける。

    読めない提案や承認済みチケット、落とした提案の理由は標準エラーにも出す。端末のユーザは
    そこで読み、拡張は JSON の `problems` / `rejected` で読む。

    `only` は承認の対象を識別子で絞る（`ccnavi --agree <識別子>...`、拡張のオーバーレイ）。
    ボードが絞り込みで見えている分だけを渡す。絞りは対象を狭めるだけで、絞らないときに
    落ちるものを通してはいけない。だから、承認待ちに無い識別子が入っていたら何も
    承認しない（ボードが古いときに、見せた以外のものを通さないため）。親の改版が
    承認待ちなのに対象から外した子も何も承認しない（外すと旧計画で検証される）。
    通らなかった理由は `refused` に入れて返す。呼び手はそれを見て何もしない。

    フェーズ定義は承認の対象全体で 1 つに決まらない。使う定義は親の写し（`phases:`）で、写しを
    確かめる `phases.yml` は各チケットの `project:` が決める（設計 11.4.1）ので、候補を組むところで
    1 件ずつ引き、引いたものを `Candidate` に持たせる。画面は候補が持つ定義を使う。
    """
    raw = approval.read_raw(conf, root)
    proposals, problems, stale = approval.read_proposals(conf, root, raw.everything)
    for problem in problems:
        stderr.write(f"ccnavi: {problem}\n")

    approved, notes = approval.scan(conf, root, raw=raw)
    for note in notes:
        stderr.write(f"ccnavi: {note}\n")
    closed, _ = approval.scan(conf, root, closed=True, raw=raw)
    review, _ = approval.scan_review(conf, root, raw=raw)
    # 本物とするツリーの外に書いた改版（と、改版の前に切ったワークツリーに残った古い版）は承認待ちに
    # 入れない。黙って外さず、書く場所を名指しする。出すのは呼び手（`core_base.say_elsewhere` と
    # `verify_verdict` の本文）で、ここでは標準エラーに書かない（同じ名指しを 2 度出さない）。
    open_index = approval_checks.by_id(approved)
    elsewhere = [
        approval.revision_elsewhere_text(conf, root, t, where)
        for t, where in stale
        if approval.revision_elsewhere(t, open_index.get(t.ticket))
    ]

    pending, revisions = waiting(proposals, approved, closed, review)
    broken = any(p.severity == rules.SEVERITY_ERROR for p in problems)
    texts = [str(p) for p in problems] + list(notes)
    note = ""

    if only:
        # 識別子の検査は「承認待ちが無い」より先。承認待ちが空でも、指定したものが
        # 無いのは失敗で、終了コードが他の承認待ちの有無で変わらないように。
        wanted = [i for i in dict.fromkeys(only) if i]
        known = {t.ticket for t in pending + revisions}
        unknown = [i for i in wanted if i not in known]
        if not wanted or unknown:
            lines = [
                f"承認待ちに無い: {', '.join(unknown) or '(空の識別子)'}",
                "何も承認しない。ボードを更新して承認待ちを確かめてください",
            ]
            for line in lines:
                stderr.write(f"ccnavi: {line}\n")
            return Gathered([], [], texts, {}, False, broken, "\n".join(lines), elsewhere=elsewhere)
        # 親の改版を外して子だけ通すと、子は承認済みチケット（旧計画）で検証される。絞らなければ
        # 改版後の計画で落ちるものが通ることになるので、親も並べるまで何も承認しない。
        skipped = {t.ticket for t in revisions if t.ticket not in wanted}
        blocked = [t for t in pending if t.ticket in wanted and t.is_child and t.parent in skipped]
        if blocked:
            lines = [
                f"{t.ticket}: 親 {t.parent} の改版が承認待ちなのに承認の対象に無い" for t in blocked
            ]
            lines.append("何も承認しない。親の改版も承認の対象に入れてください")
            for line in lines:
                stderr.write(f"ccnavi: {line}\n")
            return Gathered([], [], texts, {}, False, broken, "\n".join(lines), elsewhere=elsewhere)
        waiting_count = len(pending) + len(revisions)
        pending = [t for t in pending if t.ticket in wanted]
        revisions = [t for t in revisions if t.ticket in wanted]
        note = f"承認待ち {waiting_count} 件のうち、指定の {len(wanted)} 件だけを承認の対象にする。"

    if not pending and not revisions:
        return Gathered([], [], texts, {}, True, broken, "", note, elsewhere=elsewhere)

    batch, rejected, pool = agree_candidates.candidates(root, conf, pending, revisions, approved)
    for t, complaints in rejected:
        stderr.write(f"ccnavi: {t.ticket} は承認の対象にしない\n")
        for p in complaints:
            stderr.write(f"  {p}\n")
    return Gathered(batch, rejected, texts, pool, False, broken, "", note, elsewhere=elsewhere)


def preview_body(root: str, gathered: Gathered, digest: str) -> dict:
    """`--preview --json` が返す本体。`--verify --json` も同じものに答えを足して返す。"""
    return {
        "version": AGREE_VERSION,
        "root": root,
        "generated_at": approval_marks.now(),
        "batch": [_batch_entry(c) for c in gathered.batch],
        "text": gathered.text,
        "digest": digest,
        "rejected": [
            {"ticket": t.ticket, "problems": [str(p) for p in complaints]}
            for t, complaints in gathered.rejected
        ],
        "problems": gathered.problems,
    }


# `--verify` が返す理由。JSON の `verify.reason` に出る。
VERIFY_OK = "ok"
VERIFY_REFUSED = "refused"
VERIFY_NOTHING = "nothing-pending"
VERIFY_REJECTED = "rejected"


@dataclass
class Verdict:
    """`--verify` の答え。通るかどうかと、その理由の名前と、端末に出す本文。"""

    ok: bool
    reason: str
    text: str


def verify_verdict(gathered: Gathered, tickets_rel: str) -> Verdict:
    """`--verify` の答えを組む。判定は `gather` が済ませてあり、ここは読み替えるだけ。"""
    head = "承認の可否（確かめるだけ。承認済みチケットは置かない）\n"
    # 読めなかったものは、落ちた枝でも必ず出す。むしろこの 2 つ（絞りが通らない・承認待ちが
    # 1 件も無い）が「読めないのは自分が書いた 1 本」である見込みのいちばん高い枝で、
    # そこで出さないと、置いたばかりのユーザに「todo/ に置け」とだけ言うことになる。
    unreadable = _unreadable(gathered)
    if gathered.refused:
        return Verdict(False, VERIFY_REFUSED, head + "\n" + gathered.refused + "\n" + unreadable)
    elsewhere = _elsewhere(gathered)
    if gathered.nothing_pending:
        text = f"\n承認待ちのチケットは無い。提案は {tickets_rel}/todo/ に置いてください"
        # 場所違いの改版を名指ししたときは「同じ名前で置いても承認待ちにならない」を言わない。
        # 改版そのものができないと読めるため（書く場所は上の段が言う）。
        text += (
            "。\n"
            if elsewhere
            else "（承認済みの識別子と同じ名前で置いても承認待ちにはならない）。\n"
        )
        return Verdict(False, VERIFY_NOTHING, head + elsewhere + text + unreadable)

    lines = [head]
    if gathered.note:
        lines.append("\n" + gathered.note + "\n")
    names = [c.ticket.ticket for c in gathered.batch] + [t.ticket for t, _ in gathered.rejected]
    width = max((len(name) for name in names), default=0)
    rows: list[tuple[str, str, list[str]]] = []
    # 苦情の文面から識別子を落とす（`Problem.__str__` は名指しのために持つが、行の頭に
    # 同じものが出ている）。残すのは重さと中身。
    for cand in gathered.batch:
        notes = [f"{p.severity}: {p.detail}" for p in cand.complaints]
        notes += [f"承認しても書けない: {p.detail}" for p in cand.overflow]
        rows.append((cand.ticket.ticket, "通る", notes))
    for t, complaints in gathered.rejected:
        rows.append((t.ticket, "落ちる", [f"{p.severity}: {p.detail}" for p in complaints]))
    lines.append("\n")
    for name, mark, notes in sorted(rows):
        lines.append(f"  {name.ljust(width)}  {mark}\n")
        lines += [_note_line(note) for note in notes]

    lines.append(elsewhere)
    lines.append(unreadable)

    if gathered.rejected:
        reason = VERIFY_REJECTED
        tail = (
            f"\n{len(gathered.rejected)} 件が承認の対象にならない。"
            "提案を直してから、ユーザに承認を依頼してください。\n"
        )
    else:
        reason = VERIFY_OK
        tail = f"\n{len(gathered.batch)} 件が承認の対象に入る。ユーザに承認を依頼してよい。\n"
    lines.append(tail)
    return Verdict(reason == VERIFY_OK, reason, "".join(lines))


def _elsewhere(gathered: Gathered) -> str:
    """本物とするツリーの外に在る計画の違う版（承認待ちに入らない）を名指しする段。"""
    if not gathered.elsewhere:
        return ""
    lines = ["\n承認待ちに入らない改版がある（本物とするツリーの外）。\n"]
    lines += [_note_line(line) for line in gathered.elsewhere]
    return "".join(lines)


def _unreadable(gathered: Gathered) -> str:
    """読めなかったものを名指しする段。落ちた枝でも通った枝でも同じものを出す。

    終了コードは動かさない。`--agree` も、承認待ちが 1 件も無いとき以外はこれで
    止まらないので、ここで落とすと「確かめは『いいえ』なのに承認は通る」になる。走査は絞る前の
    全ツリーを見るから、他のセッションの書きかけ 1 本で自分の提案が止まることにもなる。
    出さずに済ませることもしない。自分が書いた 1 本かもしれないので、件数と文面を本文に出す。
    """
    if not gathered.problems:
        return ""
    lines = [
        f"\n読めなかったファイルが {len(gathered.problems)} 件ある"
        "（提案か承認済みチケット。提案なら承認待ちに並ばない）。\n"
    ]
    lines += [_note_line(problem) for problem in gathered.problems]
    lines.append("        いま書いた提案が混じっていないか確かめてください。\n")
    return "".join(lines)


def _note_line(note: str) -> str:
    """行の下に添える 1 件。改行を含む苦情（ルールの `message` は複数行を書ける）は、
    2 行目からも同じだけ下げる。下げないと、次の行が新しい段落に見える。"""
    head, *rest = note.splitlines() or [""]
    return "".join([f"      - {head}\n"] + [f"        {line}\n" for line in rest])


def _batch_entry(cand: agree_candidates.Candidate) -> dict:
    t = cand.ticket
    return {
        "ticket": t.ticket,
        "title": t.title,
        "parent": t.parent or None,
        "phase": t.phase if t.is_child else None,
        "revision": cand.is_revision,
        "tree": t.tree or "",
        "path": t.path,
        # 親のブランチ名。`branch:` が無ければ識別子。
        "branch": ticket_ids.branch_name(t),
        "existing_branch": cand.existing_branch,
        "overflow": [p.detail for p in cand.overflow],
    }


@dataclass
class Applied:
    """承認済みチケットを置いた結果。途中で止まったときに、どこまで置いたかを呼び手へ返す。

    置いたものは戻さない（戻す途中でまた落ちる）。代わりに、どこで止まって何が置かれたかを
    そのまま返し、拡張がユーザに伝える（README「承認の JSON」の `partial`）。
    """

    code: int
    placed: list[str]
    stopped_at: str = ""
    reason: str = ""


def _reopened_line(parent: str, phase: int):
    def announce(kinds: list[str]) -> list[str]:
        return [
            f"  {parent} のフェーズ {phase} のマーカー（{', '.join(kinds)}）を消した。"
            "全部閉じたらレビューをもう一度頼むことになる"
        ]

    return announce


@dataclass
class Planned:
    """承認の書き込みを並べたもの。`stopped` は途中で止まった（識別子, 理由）。止まらなければ None。

    止まったときも、そこまでに並べた分は書く（前と同じく、置いたものは戻さない）。
    """

    stage: fsio.Stage
    stopped: tuple[str, str] | None


def plan_batch(
    root: str, conf: settings.Settings, batch: list[agree_candidates.Candidate], stamp: str
) -> Planned:
    """承認の書き込みを、ディスクに書かずに並べる。時刻は `stamp` に固定する。

    書き込みを並べる（ここ、plan）と、並べたものを書く
    （`core.write_fs`、Writer(FS)）の 2 段。Chrome は同じ plan の結果を 1 コミットにする。
    承認の対象の順に、改版は承認済みチケットを書き換え、新規は承認済みチケットを置く。
    """
    with fsio.staging() as stage, fsio.clock(stamp):
        stopped = _apply_steps(stage, root, conf, batch, stamp)
    return Planned(stage, stopped)


def _apply_steps(
    stage: fsio.Stage,
    root: str,
    conf: settings.Settings,
    batch: list[agree_candidates.Candidate],
    stamp: str,
) -> tuple[str, str] | None:
    """`plan_batch` の中身。書き込みは fsio の書き込みを溜める段に積み、見せる行も同じ順序で積む。

    書けなかったときの扱い（止める・言って続ける・行を出す）は `fsio.policy` で添える。
    書き込みを溜める段では書き込みが落ちないので、その扱いは Writer(FS) が書くときに当てる。
    """
    for cand in batch:
        t = cand.ticket
        with fsio.policy(
            on_fail=fsio.FAIL_STOP,
            ticket=t.ticket,
            message="{reason}",
            prefix="",
            undo=(),
            places="",
            group=0,
        ):
            if cand.is_revision and cand.current is not None:
                where = approval.home_dir(
                    conf, root, t.ticket, t.parent, t.tree_root, project=t.project
                )
                with fsio.policy(places=t.ticket):
                    failed = agree_digest.revise_copy(where, cand.current, t, cand.plans_feedback)
                if failed:
                    return t.ticket, failed
                removing = "改版の提案を todo/ から消せない ({reason})"
                with fsio.policy(on_fail=fsio.FAIL_WARN, message=removing):
                    failed = fsio.unlink(t.path)
                if failed:
                    stage.line(
                        f"ccnavi: {t.ticket}: {removing.replace('{reason}', failed)}",
                        stream=fsio.STREAM_ERR,
                    )
                what = "フィードバック計画" if cand.plans_feedback else "全体計画"
                stage.line(f"  {t.ticket} の{what}を改版した")
                if cand.plans_feedback:
                    # レビューの結果を見たうえでの計画なので、最後のレビューはここで済む。
                    with fsio.policy(prefix="マーカーを置けない: "):
                        failed = phase.settle_last_review(where, t, stamp)
                    if failed:
                        return t.ticket, f"マーカーを置けない: {failed}"
                    # 全体計画の子でレビュー待ちに残っているものは、見たうえでの計画なので閉じる。
                    moved, failed = approval_ops.settle_review(
                        conf, root, t.ticket, list(range(1, len(t.plan) + 1))
                    )
                    if failed:
                        return t.ticket, failed
                    if moved:
                        stage.line(f"  レビュー待ちの子を閉じた: {', '.join(moved)}")
                    stage.line(
                        "  全体計画の最後のレビューを済んだ扱いにした。残った指摘は"
                        "フィードバック作業フェーズの confirm が数える"
                    )
                continue
            where = approval.home_dir(
                conf, root, t.ticket, t.parent, t.tree_root, project=t.project
            )
            failed = approval_ops.admit(where, t, approval.source_branch(t))
            if failed:
                return t.ticket, failed
            if t.is_child:
                group = stage.new_group()
                with fsio.policy(on_fail=fsio.FAIL_LINE, group=group):
                    carried = approval_ops.carry_flow(conf, root, t, where)
                for line in carried:
                    stage.line(f"  {line}", group=group)
            # 終わったフェーズに子を足したら、そのフェーズのマーカーは消す。マーカーは
            # 「その時点の子が全部見られた」以上の意味を持たない（REQ-TKT-21）。
            # 消すのは置けたあと。先に消すと、書けずに終わった（置き場が塞がっている、権限が無い）
            # ときに、子は 1 枚も増えていないのに済んでいたレビューが巻き戻る
            # （test_a_failed_copy_does_not_clear_the_marks_of_a_reviewed_phase）。
            # Writer(FS) は並べた順に書き、止まったらその先を書かないので、この順が保たれる。
            # 置いた直後に落ちる（打ち切られる・電源が切れる）と「子は増えたのにマーカーは残る」
            # ＝見られていない子がいるのに止まらなくなるが、そちらは起きうる間が
            # ファイル 1 つを書く間だけで、頻度がはるかに低い。順番の入れ替えでは直らない
            # （両方を防ぐなら、マーカーの時刻と子の承認時刻を比べて止めるかどうかを決める
            # 作りが要る）。
            # マーカーを消せたかは書くときに分かるので、行は Writer(FS) が消せた種類で出す。
            # reviewed を消せなければそこで止める（`clear_marks`）。
            if t.is_child and t.phase is not None:
                approval_marks.clear_marks(
                    approval.home_dir(conf, root, t.parent, "", project=t.project),
                    t.parent,
                    t.phase,
                    announce=_reopened_line(t.parent, t.phase),
                )

    stage.line(f"\n承認した。{conf.approved}/{approval.DOING_DIR}/ へ動かした。")
    return None


def waiting(
    proposals: list[ticket_model.Ticket],
    approved: list[ticket_model.Ticket],
    closed: list[ticket_model.Ticket],
    review: list[ticket_model.Ticket],
) -> tuple[list[ticket_model.Ticket], list[ticket_model.Ticket]]:
    """いま `--agree` で承認の対象に入るもの。新規の承認待ちと、親の改版。

    承認待ちは `todo/` に在って、どの置き場（作業中・レビュー待ち・閉じた）にも同じ識別子が
    無いもの。閉じたものは対象外で、再開はユーザが承認済みチケットを戻す。
    改版は、作業中の親の承認済みチケットがあり、`todo/` の提案の計画か写し（`phases:`）がそれと
    違うもの（`approval.plan_differs`。計画は定義・`review`・推移的な待ちの並びで、写しは定義を
    読んだ形で比べる）。待ち方は計画から
    決まるので、待ち方だけの改版は無い。
    `--agree` と `--explain --json` が同じ答えを出すために、ここで 1 度だけ決める。
    統合先の取り込み結果の `done/` にある識別子（閉じた識別子の再利用）はここでは外さず、
    `candidates` が理由を添えて承認しない側に回す（何も出さずに消すことはしない）。
    """
    known = approval_checks.by_id(approved + closed + review)
    open_index = approval_checks.by_id(approved)
    todo = [t for t in proposals if t.state == ticket_model.TODO]
    pending = [t for t in todo if t.ticket not in known]
    revisions = [
        t
        for t in todo
        if t.ticket in open_index
        and not t.is_child
        and t.has_plan
        and approval.plan_differs(t, open_index[t.ticket])
    ]
    return pending, revisions
