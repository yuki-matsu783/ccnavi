"""合意の手続き。ユーザが提案に合意する（承認する）までの手続きを持つ。

置き場は approval、合意の手続きは agree。approval は承認済みチケット・マーカー・子の記録が
どこにどう置かれているかを読み書きするだけで、ここ（agree）を知らない。agree は approval の
置き場を読み、提案を承認済みチケットの置き場へ動かす。向きは agree → approval の 1 本だけ。

## 何をするか

`ccnavi --agree` が呼ぶ手続きの全部。

- 承認の対象を組む（`gather`・`candidates`）。提案を走査し、承認済みチケットと突き合わせ、
  載せるものと落とすものに分ける。形の検査（`validate`・`plan_problems`・`revision_problems`）は
  ここで当てる
- ユーザに見せる（`screen`・`preview_body`）。見せたものと承認するものを同じ答えにするため、
  一覧を組む関数は 1 つ（`gather`）にしてある
- 見せたものから変わっていないかを確かめる（`approval_digest`・`read_set`・`verify_verdict`）
- 置き場へ動かす（`plan_batch`）。書き込みは approval の置き場の関数を通す
- 承認の事実をモデルに伝える（`news`・`approved_text`）

判定は承認済みチケットだけを読み、ここを通ったかどうかは見ない（承認したかどうかは置き場で
決まる）。置き場を手で動かす進め方もあるので、判定の側で要る構造の検査は approval の
`blocking_problems` に置いてある。
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field, replace
from typing import TextIO

from ..infra import fsio, settings, tree
from ..policy import rules
from . import approval, history, phase, phasetypes, syncstate, workflow
from . import ticket as ticket_mod


@dataclass
class Candidate:
    """承認の対象の 1 件。新規の提案か、親の改版か。

    リスクの点はここに無い。点は子を閉じるときに実績（差分）で数え、宣言の広さでは
    数えない（risk.py）。宣言の広さは、親が `human_review.reason` で言う。
    """

    ticket: ticket_mod.Ticket
    complaints: list[rules.Problem] = field(default_factory=list)
    # 範囲の超過（親の範囲・種類の上限を超えた項、regex の項）。承認は止めず、判定が
    # 切り詰める。判定に影響する（止まる）ので、判定に影響しない記述の注意（complaints の warn）
    # とは分けて持つ。
    overflow: list[rules.Problem] = field(default_factory=list)
    # 改版なら、いま使われている承認済みチケット。
    current: ticket_mod.Ticket | None = None
    # このチケットに使うフェーズの種類（共通層 + `project:` が指す層、設計 11.4.1）。
    # 承認の対象の中でもチケットごとに違いうるので、候補が引いたものを持っておく。
    types: dict | None = None
    # 承認画面に足す 1 行ずつの注記（フィードバック計画の証跡など）。
    notes: list[str] = field(default_factory=list)

    @property
    def is_revision(self) -> bool:
        return self.current is not None

    @property
    def plans_feedback(self) -> bool:
        return (
            self.is_revision
            and self.current is not None
            and self.current.feedback is None
            and self.ticket.feedback is not None
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

    batch: list[Candidate]
    rejected: list[tuple[ticket_mod.Ticket, list[rules.Problem]]]
    problems: list[str]
    pool: dict
    nothing_pending: bool
    broken: bool
    # 絞り込み（`only`）が通らなかった理由。空でなければ何も承認しない。
    refused: str = ""
    # 端末に出す 1 行（「承認待ち N 件のうち、指定の M 件だけを承認の対象にする」）。
    note: str = ""

    @property
    def text(self) -> str:
        if self.nothing_pending:
            return "承認待ちのチケットは無い。"
        return screen(self.batch, self.pool)

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

    フェーズの種類は承認の対象全体で 1 つに決まらない。どの層の種類を使うかは各チケットの
    `project:` が決める（設計 11.4.1）ので、候補を組むところで 1 件ずつ引き、
    引いたものを `Candidate` に持たせる。画面は候補が持つ種類を使う。
    """
    proposals, problems = ticket_mod.scan(root, conf.tickets, conf.projects)
    for problem in problems:
        stderr.write(f"ccnavi: {problem}\n")

    approved, notes = approval.scan(conf, root)
    for note in notes:
        stderr.write(f"ccnavi: {note}\n")
    closed, _ = approval.scan(conf, root, closed=True)
    review, _ = approval.scan_review(conf, root)

    pending, revisions = waiting(
        proposals, approved, closed, review, types_resolver(conf, root, approved)
    )
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
            return Gathered([], [], texts, {}, False, broken, "\n".join(lines))
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
            return Gathered([], [], texts, {}, False, broken, "\n".join(lines))
        waiting_count = len(pending) + len(revisions)
        pending = [t for t in pending if t.ticket in wanted]
        revisions = [t for t in revisions if t.ticket in wanted]
        note = f"承認待ち {waiting_count} 件のうち、指定の {len(wanted)} 件だけを承認の対象にする。"

    if not pending and not revisions:
        return Gathered([], [], texts, {}, True, broken, "", note)

    batch, rejected, pool = candidates(root, conf, pending, revisions, approved)
    for t, complaints in rejected:
        stderr.write(f"ccnavi: {t.ticket} は承認の対象にしない\n")
        for p in complaints:
            stderr.write(f"  {p}\n")
    return Gathered(batch, rejected, texts, pool, False, broken, "", note)


def preview_body(root: str, gathered: Gathered, digest: str) -> dict:
    """`--preview --json` が返す本体。`--verify --json` も同じものに答えを足して返す。"""
    return {
        "version": AGREE_VERSION,
        "root": root,
        "generated_at": approval.now(),
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
    if gathered.nothing_pending:
        text = (
            f"\n承認待ちのチケットは無い。提案は {tickets_rel}/todo/ に置いてください"
            "（承認済みの識別子と同じ名前で置いても承認待ちにはならない）。\n"
        )
        return Verdict(False, VERIFY_NOTHING, head + text + unreadable)

    lines = [head]
    if gathered.note:
        lines.append("\n" + gathered.note + "\n")
    names = [c.ticket.ticket for c in gathered.batch] + [t.ticket for t, _ in gathered.rejected]
    width = max((len(name) for name in names), default=0)
    rows: list[tuple[str, str, list[str]]] = []
    # 苦情の文字列から識別子を落とす（`Problem.__str__` は名指しのために持つが、行の頭に
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


def _unreadable(gathered: Gathered) -> str:
    """読めなかったものを名指しする段。落ちた枝でも通った枝でも同じものを出す。

    終了コードは動かさない。`--agree` も、承認待ちが 1 件も無いとき以外はこれで
    止まらないので、ここで落とすと「確かめは『いいえ』なのに承認は通る」になる。走査は絞る前の
    全ツリーを見るから、他のセッションの書きかけ 1 本で自分の提案が止まることにもなる。
    出さずに済ませることもしない。自分が書いた 1 本かもしれないので、件数とパスを本文に出す。
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


def approval_digest(text: str, batch: list[Candidate], read: dict[str, str] | None = None) -> str:
    """承認のダイジェスト。

    承認画面の本文・判定が読んだ中身（`read_set`）・承認済みチケットに写る中身の
    SHA-256 の 16 進（小文字）。

    ボードは preview のダイジェストを `--yes` に `--digest` で返す。識別子だけを比べると、
    見せたあとに提案の範囲や計画が書き換わっても、同じ識別子なら承認が通る。
    本文だけを比べても、画面に出ないのに承認済みチケットへ写る欄（`issue`、Markdown の
    本文、知らない frontmatter の欄）は見せたあとに書き換えられる。

    **判定が読んだ中身（`read_set`）をダイジェストに含める。** 提案だけでなく、
    判定が読んだ承認済みチケット・マーカー・フェーズの種類・統合先の同期状態のどれかが見せたあとに
    変われば、ダイジェストが変わる（読んだ先が増えた・減ったも同じ）。全ブランチの先頭（`head_sha`）は
    入れない（無関係なコミットで承認が通らなくならないように）。`read` は `read_set` の返す形
    （`<ブランチ>:<相対パス>` → 中身のハッシュ）。

    一括承認のチケットの写る中身（`_carried`）も残してダイジェストに含める。読んだ中身から
    決まるものだが、読みの記録（`fsio.reading`）を通らない読みが紛れても、写る中身の変化は
    取りこぼさないように。

    部分をそのままつながず、部分ごとのハッシュを件数と一緒に並べて、そのリストのハッシュを取る。
    区切りの文字でつなぐと、その文字が部分の中に出たときにつなぎ目をずらせる。Markdown の
    本文は生の制御文字（`\\x00` も）をそのまま通すので、どの文字も「中身に出ない」とは言えない。
    読んだ中身の部分は `<鍵>\\n<中身のハッシュ>`（ハッシュは 16 進の固定長なので、最後の改行で
    切れる）。
    """
    parts = [text] + [_carried(cand) for cand in batch]
    if read is not None:
        parts += [f"{key}\n{digest}" for key, digest in sorted(read.items())]
    lines = [str(len(parts))] + [hashlib.sha256(p.encode("utf-8")).hexdigest() for p in parts]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def read_set(conf: settings.Settings, root: str, seen: dict[str, str]) -> dict[str, str]:
    """判定が読んだ中身（`fsio.reading` が集めたもの）を、機械に依らない鍵に直す。

    鍵は `<リポジトリ>:<ブランチ>:<ツリーからの相対パス>`（"/" 区切り）。リポジトリは
    ワークスペース自身なら `self`、プロジェクトならその名前（同期状態の名前と同じ。ブランチ名が
    リポジトリをまたいで重なっても鍵は同じにならない。git の ref に `:` は使えない）。
    ブランチはそのツリーの HEAD が指すブランチ名（読めなければツリーの名前。ワークスペースルートの
    名前は空）で、`Changes.per_branch` と同じ決め方。ツリーは承認済みチケットを持ちうるもの全部
    （`trees`）で、最長一致。鍵に使ったツリーの HEAD の中身も `<リポジトリ>:<ブランチ>:(HEAD)` で
    入れる（切り離した HEAD の sha が変わればツリーの名前の鍵は同じでもダイジェストが変わる）。
    状態ディレクトリの下は、取り込みの同期状態（`sync/`）だけを `(控え):<相対パス>` で
    入れ、ほかのファイル（セッションごとの一時の状態）は入れない（判定の入力ではなく、読むたびに
    変わりうる）。ワークスペースの外は絶対パスのまま。
    """
    # 集めた読みのパスの表記はリンクをたどった先（`fsio.note_read`）なので、比べる側も
    # たどった先に揃えてから大文字小文字をそろえる（macOS の /tmp のようなリンクを経たルート、
    # Windows の表記の揺れ）。
    held = sorted(
        ((_real(t.root), t) for t in approval.trees(conf, root)),
        key=lambda x: len(x[0]),
        reverse=True,
    )
    folded_roots = [(_folded(real), real, t) for real, t in held]
    state_real = _real(conf.state) if conf.state else ""
    state = _folded(state_real) if state_real else ""
    names: dict[str, str] = {}
    out: dict[str, str] = {}
    for path, digest in seen.items():
        folded = _folded(path)
        if state and (folded == state or folded.startswith(state + os.sep)):
            rel = os.path.relpath(path, state_real).replace(os.sep, "/")
            if rel.split("/", 1)[0] == syncstate.SYNC_DIR:
                out[f"(控え):{rel}"] = digest
            continue
        owner = next(
            (
                (real, t)
                for key, real, t in folded_roots
                if folded == key or folded.startswith(key + os.sep)
            ),
            None,
        )
        if owner is None:
            out[f"(外):{fsio.slashed(path)}"] = digest
            continue
        real, t = owner
        if real not in names:
            prefix = f"{syncstate.repo_key(t.project)}:{tree.branch_of(t.root) or t.name}"
            names[real] = prefix
            head = tree.head_text(t.root)
            out[f"{prefix}:(HEAD)"] = fsio.content_digest(head) if head is not None else "-"
        rel = os.path.relpath(path, real).replace(os.sep, "/")
        out[f"{names[real]}:{rel}"] = digest
    return out


# 判定の置き場と働きを決める設定（`settings.load` と Claude Code の設定）の読み先。
_SETTINGS_FILES = (
    "pyproject.toml",
    settings.LOCAL_FILE,
    os.path.join(".claude", "settings.json"),
    settings.LOCAL_CLAUDE_SETTINGS,
)
# 承認の判定に影響する設定の値（環境変数からも来るので、ファイルの中身とは別に値そのものを入れる）。
# 置き場の表記とチケット制御だけ。状態ディレクトリ（`state`）は入れない（取り込みの同期状態は中身で
# `(控え):` に入る。置き場の表記だけが違う起動で見せ直しにならないように）。モードと承認の守りは
# 承認の答えを変えない（端末を求めるかどうか）ので入れない。
_SETTINGS_VALUES = ("tickets", "approved", "projects", "project_home")


def settings_read_set(conf: settings.Settings, root: str) -> dict[str, str]:
    """判定が読んだ設定（read_set に足す）。

    `settings.load` が読むファイル（`pyproject.toml`・`ccnavi.settings.local.json`）と
    Claude Code の設定（`.claude/settings.json`・`.claude/settings.local.json`）の中身を
    `(設定):<相対パス>` で、判定に影響する値（置き場の表記・チケット制御など。
    環境変数から来るもの）を `(設定値):<名前>` で入れる。
    見せたあとに置き場の表記や設定が変われば、同じ画面でもダイジェストが変わる。
    """
    out: dict[str, str] = {}
    for rel in _SETTINGS_FILES:
        try:
            with open(os.path.join(root, rel), "rb") as f:
                out[f"(設定):{rel.replace(os.sep, '/')}"] = fsio.content_digest(f.read())
        except OSError:
            out[f"(設定):{rel.replace(os.sep, '/')}"] = fsio.READ_ABSENT
    for name in _SETTINGS_VALUES:
        value = str(getattr(conf, name, "") or "")
        if value and os.path.isabs(value):
            inside = os.path.relpath(value, root)
            if not inside.startswith(".."):
                value = inside.replace(os.sep, "/")
        out[f"(設定値):{name}"] = fsio.content_digest(value)
    # チケット制御は書き方（空・enable）ではなく有効かどうかで入れる。
    out["(設定値):ticket_control"] = fsio.content_digest(str(conf.tickets_enabled))
    return out


def _real(path: str) -> str:
    try:
        return os.path.realpath(path)
    except OSError:
        return os.path.abspath(path)


def _folded(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def _carried(cand: Candidate) -> str:
    """承認済みチケットに写る中身。承認の記録の欄を足す前の内容で書き出す。

    改版は承認済みチケットの frontmatter の計画だけを差し替え、本文は承認済みチケットの
    ものを残す（`revise_copy`）。新規は提案をそのまま写す（`write_copy`）。
    """
    t = cand.ticket
    if cand.is_revision and cand.current is not None:
        front, body = revised_front(cand.current, t, cand.types), cand.current.body
    else:
        front, body = dict(t.raw), t.body
        front.pop(ticket_mod.WORKFLOW_KEY, None)
        if t.has_plan and not t.is_child:
            front[ticket_mod.WORKFLOW_KEY] = workflow.compute(t, cand.types).as_raw()
    front.pop(ticket_mod.APPROVAL_KEY, None)
    return ticket_mod.render(replace(t, raw=front, body=body))


def _batch_entry(cand: Candidate) -> dict:
    t = cand.ticket
    return {
        "ticket": t.ticket,
        "title": t.title,
        "parent": t.parent or None,
        "phase": t.phase if t.is_child else None,
        "revision": cand.is_revision,
        "tree": t.tree or "",
        "path": t.path,
        "overflow": [p.detail for p in cand.overflow],
    }


def approved_text(tickets: list[ticket_mod.Ticket], revisions: set[str], root: str) -> str:
    """チケットが承認されたことをモデルに伝える文。

    `--agree --yes` の `prompt`（拡張が Claude Code に渡す）と、hook が次の
    UserPromptSubmit / PreToolUse で渡す `additionalContext` の両方がここから出る。
    2 か所で文を持つと、ユーザが貼った文と hook が渡した文が食い違う。

    tickets は承認済みチケット（`ticket` `title` `parent` `phase` `is_child` を持つもの）。
    revisions は親の改版だった識別子。root はワークスペースルートで、sh のパスの表記に使う。
    """
    lines = ["[ccnavi] チケットが承認され、承認済みチケットの置き場（doing/）へ動いた。"]
    for t in tickets:
        if t.ticket in revisions:
            where = "親の改版。計画が新しくなった"
        elif t.is_child:
            where = f"親 {t.parent}、フェーズ {t.phase}"
        else:
            where = "親"
        title = f": {t.title}" if t.title else ""
        lines.append(f"- {t.ticket}{title}（{where}）")
    ticket_sh = settings.script_command(root, "ccnavi-ticket.sh")
    # 子の着手は親の着手を前提にする（設計 9.6、REQ-TKT-48）。順をここで言わないと、
    # 最初の子の着手で止まってから読むことになる。ただし勧めるのは、この回に承認された
    # 親が居るときだけ。改版と子だけの回で `start <親>` を勧めると、親は着手済みなので
    # 案内どおりに打つと「着手済み」で終わる。
    guide = "後工程を進める。"
    if any(not t.is_child and t.ticket not in revisions for t in tickets):
        guide += f"親は自分のワークツリーで '{ticket_sh} start <親>' を先に打つ。"
    if any(t.is_child for t in tickets):
        guide += (
            "子はワークツリー .claude/worktrees/<識別子> を親のブランチから切り、"
            f"'{ticket_sh} start <識別子>' で着手する（親が未着手だと止まる）。"
        )
    lines.append(guide)
    return "\n".join(lines)


# ---- 承認の事実を hook がモデルへ伝える


def _news_path(state_dir: str, session: str, agent_id: str) -> str:
    """このセッション（サブエージェントならその起動）が知っている承認済みチケットの状態ファイル。
    `once-<session>-<agent>.json`（ctxfile）と同じ場所に置く。"""
    session_part = fsio.safe_name(session) or "unknown"
    agent_part = fsio.safe_name(agent_id) or "main"
    return os.path.join(state_dir, f"approved-{session_part}-{agent_part}.json")


def _known(path: str) -> dict[str, str] | None:
    """状態ファイルにある「識別子 → 版」。状態ファイルが無ければ None。

    読めるのに壊れているときは空の辞書を返す。「無い」と同じに扱うと、まだ伝えて
    いない承認ごと現状を起点にして何も伝えないことになる。何も知らないことにして、
    伝える側を採る。
    """
    data, failed = fsio.read_json(path)
    if failed is not None:
        return None if isinstance(failed, FileNotFoundError) else {}
    known = data.get("known") if isinstance(data, dict) else None
    if isinstance(known, dict):
        return {k: str(v) for k, v in known.items() if isinstance(k, str)}
    return {}


def _write_known(stderr: TextIO, path: str, known: dict[str, str]) -> None:
    failed = fsio.write_json_atomic(path, {"known": dict(sorted(known.items()))})
    if failed:
        stderr.write(f"ccnavi: 承認を伝えた状態ファイルを書けない: {failed}\n")


def _mark(t: ticket_mod.Ticket) -> str:
    """承認済みチケット 1 枚の版。承認した時刻と、改版した時刻。

    改版（`revise_copy`）は承認済みチケットを書き換えるだけで識別子を増やさないので、識別子だけを
    比べても新しい合意だと分からない。版まで見る。
    """
    meta = t.raw.get(ticket_mod.APPROVAL_KEY)
    meta = meta if isinstance(meta, dict) else {}
    return f"{meta.get('approved_at') or ''}/{meta.get('revised_at') or ''}"


def _copy_marks(conf: settings.Settings, root: str) -> dict[str, ticket_mod.Ticket]:
    """いまある承認済みチケット。開いたものと閉じたもの。

    閉じたものも見る。承認の直後・次の hook の前に子が閉じることがあり、開いたものだけを
    見ると、その承認は誰にも伝わらないまま状態ファイルに入る。
    """
    found: dict[str, ticket_mod.Ticket] = {}
    for closed in (False, True):
        got, _ = approval.scan(conf, root, closed=closed)
        for t in got:
            found[t.ticket] = t
    review, _ = approval.scan_review(conf, root)
    for t in review:
        found[t.ticket] = t
    return found


def _fresh(known: dict[str, str], current: dict[str, ticket_mod.Ticket]) -> list[ticket_mod.Ticket]:
    """まだ伝えていない承認済みチケット。版が変わったもの（改版）も含む。"""
    out = []
    for ident, t in sorted(current.items()):
        recorded = known.get(ident)
        if recorded is None or recorded != _mark(t):
            out.append(t)
    return out


def baseline(
    stderr: TextIO, conf: settings.Settings, root: str, session: str, agent_id: str
) -> None:
    """状態ファイルが無ければ、いまの承認済みチケットを「知っているもの」として書く。文は出さない。

    SessionStart から呼ぶ。起動・再開・compact のどれでも来るが、状態ファイルがあれば
    触らない。compact の前に置かれた承認は、compact のあとにも 1 度は伝える。
    """
    if not conf.state or not conf.tickets_enabled:
        return
    path = _news_path(conf.state, session, agent_id)
    if _known(path) is None:
        _write_known(stderr, path, {i: _mark(t) for i, t in _copy_marks(conf, root).items()})


def news(stderr: TextIO, conf: settings.Settings, root: str, session: str, agent_id: str) -> str:
    """このセッションがまだ知らない承認済みチケットがあれば、その承認を伝える文。1 度だけ。

    最初の hook で状態ファイルが無ければ、いまの承認済みチケットを起点として書き、何も伝えない。
    それより後に置かれた承認済みチケットと、版の変わった承認済みチケット（親の改版）が「新しい承認」になる。
    状態ファイルを置けない（`--state ""`）ときは何も伝えない。
    診断の試し打ちで記録を汚さない側を採る。
    サブエージェントは自分の状態ファイルを持つので、起動より前の承認は伝えない。

    読み・判定・書きは直列化していない。同じセッションの hook が同時に走ると、同じ承認を
    2 度伝えることがある。伝えすぎる側なので受け入れる。取るべきでないのは逆で、
    競合のために伝えない形にはしない。
    """
    if not conf.state or not conf.tickets_enabled:
        return ""
    path = _news_path(conf.state, session, agent_id)
    known = _known(path)
    current = _copy_marks(conf, root)
    marks = {i: _mark(t) for i, t in current.items()}
    if known is None:
        _write_known(stderr, path, marks)
        return ""
    fresh = _fresh(known, current)
    if not fresh:
        return ""
    # 消えた承認済みチケットの分も残す。1 回読めなかっただけで「知らない」に戻すと、
    # 次の回に同じ承認をもう一度伝えることになる。
    _write_known(stderr, path, {**known, **marks})
    return approved_text(fresh, {t.ticket for t in fresh if t.ticket in known}, root)


def candidates(
    root: str,
    conf: settings.Settings,
    pending: list[ticket_mod.Ticket],
    revisions: list[ticket_mod.Ticket],
    approved: list[ticket_mod.Ticket],
) -> tuple[list[Candidate], list[tuple[ticket_mod.Ticket, list[rules.Problem]]], dict]:
    """承認の対象に入れるものと、落とすものに分ける。3 つめは親子を引くための対応表。

    承認（`agree`）・見せる（`preview`）・確かめる（`verify`）に加えて、`--lint` も
    ここを通る。承認で落ちるものを数える経路が 2 本あると、片方が気づかないうちに弱くなる
    （実際に `--lint` は `validate` だけを当てていて、順序で落ちる子に何も言わなかった）。
    """
    open_index = approval.by_id(approved)
    # 親子を引く対応表は、承認済みチケットと、今回の承認で通ったものだけ。落ちた親を対応表に残すと、
    # 承認されない親の範囲で子が検証され、親の承認を経ずに子の承認済みチケットができる。
    # pending は親が子より前に並ぶ（並べ替えの鍵が親の識別子）ので、子が引くときには
    # 親の通過が決まっている。
    pool = approval.by_id(approved)
    batch: list[Candidate] = []
    rejected: list[tuple[ticket_mod.Ticket, list[rules.Problem]]] = []
    # 層ごとの読み込みは 1 プロジェクト 1 回。承認の対象に同じ層のチケットが
    # 何件あっても、ファイルを読むのはその層につき 1 度で足りる。
    cache: dict[str, dict | None] = {}
    # 先行を引く対応表。先行を書いた子が居るときだけ、最初の 1 回で組む。
    preds: dict[str, list[ticket_mod.Ticket]] | None = None
    # 親子チケットの立ち位置と統合先の同期状態。1 回の承認で 1 度ずつだけ読む。
    fams = syncstate.Families(conf, root)

    def types_for(t: ticket_mod.Ticket) -> dict | None:
        name = project_of(t, pool)
        if name not in cache:
            cache[name] = phase.load_types(conf, root, name)
        return cache[name]

    for t in sorted(revisions, key=lambda x: x.ticket):
        current = open_index[t.ticket]
        types = types_for(t)
        complaints = _workflow_field(t) + revision_problems(root, conf, t, current, types)
        complaints += approval.family_problems(conf, root, t, fams)
        if any(p.severity == rules.SEVERITY_ERROR for p in complaints):
            rejected.append((t, complaints))
            continue
        cand = Candidate(ticket=t, complaints=complaints, current=current, types=types)
        if cand.plans_feedback:
            cand.notes = feedback_notes(root, conf, t)
        batch.append(cand)
        # 通った改版だけ、一緒に承認する子から見える親にする。落ちた改版の計画で子を
        # 通すと、承認されない番号の子が承認済みチケットになる。
        pool[t.ticket] = t

    # 今回の承認で通った子を親ごとに。後に続く子の順序の検査が、そのフェーズを
    # 開き直したものとして読む。
    # 子はフェーズの番号の順に並べる。識別子の順だと、後のフェーズの子（-02）が前のフェーズに
    # 足す子（-03）より先に検査され、開き直す前のマーカーで通ってしまう。
    added: dict[str, list[ticket_mod.Ticket]] = {}
    for t in sorted(
        pending, key=lambda x: (x.parent or x.ticket, x.is_child, x.phase or 0, x.ticket)
    ):
        types = types_for(t)
        complaints, overflow = validate(t, pool, types)
        complaints += _workflow_field(t)
        complaints += approval.project_problems(t, pool, conf)
        complaints += approval.family_problems(conf, root, t, fams)
        complaints += approval.integration_problems(conf, root, t, fams)
        if t.is_child and not any(p.severity == rules.SEVERITY_ERROR for p in complaints):
            parent = pool.get(t.parent)
            if parent is not None:
                complaints += phase.order_problems(
                    root, conf, t, parent, types, added.get(t.parent)
                )
        if t.is_child and t.predecessors:
            # 先行は `done/` に在って取り消しでないことを求める。同じ承認で通る
            # 先行も、まだ `todo/` に在るので満たさない。
            if preds is None:
                preds = approval.predecessor_pool(conf, root)
            complaints += approval.predecessor_problems(t, preds, conf.approved)
        if any(p.severity == rules.SEVERITY_ERROR for p in complaints):
            rejected.append((t, complaints))
            continue
        batch.append(Candidate(ticket=t, complaints=complaints, overflow=overflow, types=types))
        pool[t.ticket] = t
        if t.is_child:
            added.setdefault(t.parent, []).append(t)
    return batch, rejected, pool


def _workflow_field(t: ticket_mod.Ticket) -> list[rules.Problem]:
    """提案に待ち方のコピー（`workflow:`）が書いてあれば拒む。コピーを書くのは `--agree` だけ。"""
    if ticket_mod.WORKFLOW_KEY not in t.raw:
        return []
    return [
        rules.Problem(
            rules.SEVERITY_ERROR,
            t.ticket,
            f"`{ticket_mod.WORKFLOW_KEY}` は --agree が書く欄。提案には書かない",
        )
    ]


def project_of(t: ticket_mod.Ticket, pool: dict[str, ticket_mod.Ticket]) -> str:
    """このチケットの層を決める `project:`（設計 11.4.1）。

    子は親と同じ置き場に並ぶので、種類を引くには親のプロジェクトを使う。食い違えば
    `project_problems` が落とす。親が対応表に居ないときだけ、子の置き場の値をそのまま読む。
    """
    if t.is_child:
        parent = pool.get(t.parent)
        if parent is not None:
            return parent.project
    return t.project


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


def plan_batch(root: str, conf: settings.Settings, batch: list[Candidate], stamp: str) -> Planned:
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
    batch: list[Candidate],
    stamp: str,
) -> tuple[str, str] | None:
    """`plan_batch` の中身。書き込みは fsio の書き込みを溜める段に積み、見せる行も同じリストに積む。

    書けなかったときの扱い（止める・言って続ける・行を出す）は `fsio.policy` で添える。
    溜める段では書き込みが落ちないので、その扱いは Writer(FS) が書くときに当てる。
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
                    failed = revise_copy(
                        where, cand.current, t, stamp, cand.plans_feedback, cand.types
                    )
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
                    moved, failed = approval.settle_review(
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
            wf = workflow.compute(t, cand.types) if t.has_plan and not t.is_child else None
            failed = approval.admit(where, t, approval.source_branch(t), stamp, wf)
            if failed:
                return t.ticket, failed
            if t.is_child:
                group = stage.new_group()
                with fsio.policy(on_fail=fsio.FAIL_LINE, group=group):
                    carried = approval.carry_flow(conf, root, t, where)
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
                approval.clear_marks(
                    approval.home_dir(conf, root, t.parent, "", project=t.project),
                    t.parent,
                    t.phase,
                    announce=_reopened_line(t.parent, t.phase),
                )

    stage.line(f"\n承認した。{conf.approved}/{approval.DOING_DIR}/ へ動かした。")
    return None


def _origin_line(t: ticket_mod.Ticket) -> str:
    """どのプロジェクトの、どのツリーの、どの提案か（REQ-MLT-11）。

    プロジェクトは提案を置いた場所で決まる。ユーザはここで、書き込みが向かうリポジトリを
    見て承認する。提案はそのツリーからの相対パスで見せる。絶対パスは
    機械ごとに違い、承認のダイジェスト（画面の本文を含む）が Chrome と手元で揃わない。
    """
    return (
        f"■ プロジェクト: {t.project or 'ワークスペース'}"
        f"  ワークツリー: {t.tree or 'ワークスペースルート'}  提案: {approval.source_path(t)}"
    )


def screen(
    batch: list[Candidate],
    pool: dict[str, ticket_mod.Ticket],
) -> str:
    """承認を求める画面を組む。

    frontmatter の全文は見せない。ユーザに見せるのは「何が新たに書けるようになるか」
    「子が編集可能な範囲（親をどこまで絞ったか）」「人間レビューの要否」「リスク」「計画」。
    新たに書けるようになる領域を最初に置く（REQ-APV-01）。

    種類は候補が持っているものを使う。承認の対象の中でもチケットごとに層が違いうるので、
    画面の側で 1 つに決めない。
    """
    lines = [f"チケットの承認リクエスト: {len(batch)} 件"]
    for cand in batch:
        t = cand.ticket
        cand_types = cand.types
        if cand.is_revision and cand.current is not None:
            lines += ["", f"== {t.ticket}: {t.title}  親の改版"]
            lines += _plan_diff_lines(cand.current, t, cand_types)
            for note in cand.notes:
                lines.append(f"    {note}")
            lines.append(_origin_line(t))
            continue
        lines += [
            "",
            f"== {t.ticket}: {t.title}"
            + (
                f"  親 {t.parent} / フェーズ {_phase_label(t, pool, cand_types)}"
                if t.is_child
                else "  親チケット"
            ),
        ]
        if t.is_child:
            # 親は一緒に承認の対象に入っていることが普通。承認済みチケットだけを引くと
            # 「承認済みチケットが無い」になる。
            parent = pool.get(t.parent)
            lines.append("■ この子チケットで編集可能な範囲")
            lines.append(
                "    子の範囲は親の範囲の中に収まる。下に並ぶのは親から絞った結果で、"
                "親の範囲に無い場所がここで新しく編集できるようになることはない"
            )
            head = "親の範囲: " + (
                ", ".join(parent.paths(rules.ALLOW) + parent.paths(rules.ASK))
                if parent
                else "親がまだ承認されていない"
            )
            lines.append(f"    {head}")
            bound = _type_of(t, pool, cand_types)
            if bound is not None and not bound.inherits_scope:
                lines.append(f"    種類「{bound.title}」の範囲: " + ", ".join(bound.scope_globs))
        else:
            lines.append("■ このチケットで編集可能な範囲")
            lines.append(
                "    下に並ぶ場所にだけ、このチケットで編集できるようになる。"
                "allow は無確認で編集できる場所、ask は確認を挟んで編集できる場所、"
                "deny はこのチケットでも編集できない場所"
            )
            # チケットの範囲はルールの allow より強い（設計 7）。承認するユーザは「ルールで
            # 開けてあるから範囲の外でも書ける」と読み違えやすいので、承認の前に言う。
            lines.append(
                "    ルールの allow で許可してある場所も、この範囲の外では止まる。"
                "ルールの deny はこの範囲の中でも止まる"
            )
        for name in rules.SECTIONS:
            paths = t.paths(name)
            if paths:
                lines.append(f"    {name}: " + ", ".join(paths))
        if cand.overflow:
            # 範囲のすぐ下に置く。承認は止めないが、判定では止まる。判定に影響しない記述の
            # 注意と混ぜると、承認すれば書けると読み違える。
            # **「編集対象」と「書き込めない」は意図して分けてある。** 前半はチケットが宣言した側、
            # 後半は実際の書き込みが止まる側の話で、どちらか一方の語に揃えると、宣言と実行の
            # どちらを指しているのかが読めなくなる。ほかの見出しが「編集」で揃っているのを見て、
            # ここも揃えたくなるが、揃えない。
            lines.append("■ チケットで編集対象としているが、書き込めない場所")
            lines.append(
                "    親の範囲かフェーズの種類の上限を超えている。"
                "承認は可能だが、編集しようとすると判定が止める"
            )
            lines += [f"    {p.detail}" for p in cand.overflow]
        if t.is_child:
            state = "要" if t.review_required else "不要"
            lines.append(f"■ 人間レビュー: {state}")
            if t.review_reason:
                lines.append(f"    理由: {t.review_reason}")
            if t.predecessors:
                lines.append(f"■ 先行: {', '.join(t.predecessors)}")
                lines.append(
                    "    どれも done/ に在って取り消しでないこと。"
                    "満たしていなければ、承認も着手も止まる"
                )
        elif t.has_plan:
            lines.append("■ 全体計画")
            lines.append(
                "    承認すると、この順序で進めることに合意したことになる。"
                "前のフェーズが閉じるまで、次のフェーズの子は承認できない"
            )
            lines += _plan_lines(t.plan, 1, cand_types)
            lines += _workflow_lines(t, workflow.compute(t, cand_types))
            if t.feedback is not None:
                lines.append("■ フィードバック計画")
                lines += _plan_lines(t.feedback, len(t.plan) + 1, cand_types) or ["    対応なし"]
        if not t.is_child and t.issue is not None:
            lines.append(f"■ 課題: {ticket_mod.issue_label(t)}")
            lines.append(
                "    この親のマージリクエストの本文に Closes として書く番号。"
                "マージされると、この課題も閉じる"
            )
        if t.rationale.strip():
            lines.append("■ エージェントが書いた理由")
            lines += [f"    {line}" for line in t.rationale.strip().splitlines()]
        lines.append(_origin_line(t))
        warnings = [p for p in cand.complaints if p.severity == rules.SEVERITY_WARN]
        if warnings:
            lines.append("■ 判定に効かない記述")
            lines.append(
                "    提案に書いてあっても、判定はこれを読まない。"
                "承認しても、編集できる場所は変わらない"
            )
            lines += [f"    {p.detail}" for p in warnings]
    return "\n".join(lines)


def _plan_lines(items: list[ticket_mod.PlanItem], start: int, types: dict | None) -> list[str]:
    lines = []
    for i, item in enumerate(items):
        n = start + i
        pt = (types or {}).get(item.type)
        title = pt.title if pt is not None else item.type
        review = ""
        if item.deferred:
            review = "レビューは次と一緒に"
        elif item.review == ticket_mod.PLAN_REVIEW_MR:
            review = "レビュー要: 計画で強めた"
        elif pt is not None:
            review = {
                phasetypes.REVIEW_MR: "レビュー要: マージリクエスト",
                phasetypes.REVIEW_CHAT: "レビュー要: このセッションで",
            }.get(pt.review, "レビュー不要")
        lines.append(f"    {n}. {title} / {item.type}" + (f"  {review}" if review else ""))
    return lines


def _workflow_lines(t: ticket_mod.Ticket, wf: ticket_mod.Workflow) -> list[str]:
    """`dag` の計画の待ち。辺の書き漏れをユーザが見つける場所（設計 9.7）。"""
    found = workflow.lines(t, wf)
    if not found:
        return []
    return [
        "■ 待ち方（承認すると親に写し、後から phases.yml を直しても変わらない）",
        *("    " + x for x in found),
    ]


def _plan_diff_lines(
    current: ticket_mod.Ticket, revised: ticket_mod.Ticket, types: dict | None
) -> list[str]:
    lines = []
    if current.plan != revised.plan:
        lines.append("■ 全体計画の変更")
        lines.append("    いま:")
        lines += ["    " + x for x in _plan_lines(current.plan, 1, types)]
        lines.append("    改版:")
        lines += ["    " + x for x in _plan_lines(revised.plan, 1, types)]
    fresh = workflow.compute(revised, types)
    held = current.workflow
    if held is None or held.as_raw() != fresh.as_raw():
        lines.append("■ 待ち方の変更")
        before = workflow.lines(current, held) if held is not None else []
        after = workflow.lines(revised, fresh)
        lines.append("    いま:")
        lines += ["        " + x for x in before] or ["        一直線（前の番号を全部待つ）"]
        lines.append("    改版:")
        lines += ["        " + x for x in after] or ["        一直線（前の番号を全部待つ）"]
    if current.feedback != revised.feedback:
        lines.append("■ フィードバック計画")
        start = len(revised.plan) + 1
        lines += _plan_lines(revised.feedback or [], start, types) or [
            "    対応なし。見たうえで対応しない、という記録になる"
        ]
    return lines


def _phase_label(t: ticket_mod.Ticket, pool: dict, types: dict | None) -> str:
    pt = _type_of(t, pool, types)
    return f"{t.phase}: {pt.title}" if pt is not None else str(t.phase)


def _type_of(t: ticket_mod.Ticket, pool: dict, types: dict | None):
    parent = pool.get(t.parent) if t.is_child else None
    if parent is None or not parent.has_plan or t.phase is None or not types:
        return None
    item = parent.item_at(t.phase)
    return types.get(item.type) if item is not None else None


def waiting(
    proposals: list[ticket_mod.Ticket],
    approved: list[ticket_mod.Ticket],
    closed: list[ticket_mod.Ticket],
    review: list[ticket_mod.Ticket],
    types_for,
) -> tuple[list[ticket_mod.Ticket], list[ticket_mod.Ticket]]:
    """いま `--agree` で承認の対象に入るもの。新規の承認待ちと、親の改版。

    承認待ちは `todo/` に在って、どの置き場（作業中・レビュー待ち・閉じた）にも同じ識別子が
    無いもの。閉じたものは対象外で、再開はユーザが承認済みチケットを戻す。
    改版は、作業中の親の承認済みチケットがあり、`todo/` の提案の計画がそれと違うもの。
    計画が同じでも、いまの種類で計算した待ち方が承認済みチケットに書いたものと違えば改版になる
    （`phases.yml` を直した結果を進行中の親に反映する経路。設計 9.7）。`types_for` は
    チケットに使う種類を引く関数（`types_resolver`）。
    `--agree` と `--explain --json` が同じ答えを出すために、ここで 1 度だけ決める。
    統合先の同期状態の `done/` にある識別子（閉じた識別子の再利用）はここでは外さず、`candidates` が
    理由を添えて承認しない側に回す（何も出さずに消すことはしない）。
    """
    known = approval.by_id(approved + closed + review)
    open_index = approval.by_id(approved)
    todo = [t for t in proposals if t.state == ticket_mod.TODO]
    pending = [t for t in todo if t.ticket not in known]
    revisions = [
        t
        for t in todo
        if t.ticket in open_index
        and not t.is_child
        and t.has_plan
        and (
            _plan_differs(t, open_index[t.ticket])
            or _workflow_differs(t, open_index[t.ticket], types_for)
        )
    ]
    return pending, revisions


def _workflow_differs(proposal: ticket_mod.Ticket, current: ticket_mod.Ticket, types_for) -> bool:
    fresh = workflow.compute(proposal, types_for(proposal)).as_raw()
    held = current.workflow.as_raw() if current.workflow is not None else None
    return fresh != held


def types_resolver(conf: settings.Settings, root: str, approved: list[ticket_mod.Ticket]):
    """チケットに使う種類を引く関数。層ごとの読み込みは 1 プロジェクト 1 回。"""
    pool = approval.by_id(approved)
    cache: dict[str, dict | None] = {}

    def types_for(t: ticket_mod.Ticket) -> dict | None:
        name = project_of(t, pool)
        if name not in cache:
            cache[name] = phase.load_types(conf, root, name)
        return cache[name]

    return types_for


def _plan_differs(proposal: ticket_mod.Ticket, current: ticket_mod.Ticket) -> bool:
    return proposal.plan != current.plan or proposal.feedback != current.feedback


def feedback_notes(root: str, conf: settings.Settings, parent: ticket_mod.Ticket) -> list[str]:
    """フィードバック計画の承認に添える証跡。何を見たうえでの合意かを残す。"""
    accepted = approval.accepted_threads(
        approval.home_dir(conf, root, parent.ticket, "", project=parent.project), parent.ticket
    )
    notes = [f"受け入れ済みの未解決スレッド: {len(accepted)} 件"]
    if not parent.feedback:
        notes.append("対応なし。見たうえで対応しない、という記録になる")
        if accepted:
            notes.append("別に追うものは issue に回したか（ccnavi-review.sh decide）")
    return notes


def plan_problems(t: ticket_mod.Ticket, types: dict | None) -> list[rules.Problem]:
    """親の計画が種類の定義と合っているか（設計 9.7）。"""
    problems: list[rules.Problem] = []
    if not t.has_plan:
        return problems
    if types is None:
        problems.append(
            rules.Problem(
                rules.SEVERITY_ERROR,
                t.ticket,
                "`plan` があるのにフェーズの種類の定義（phases.yml）が読めない",
            )
        )
        return problems
    for key, items, kind in (
        ("plan", t.plan, phasetypes.KIND_WORK),
        ("feedback", t.feedback or [], phasetypes.KIND_FEEDBACK),
    ):
        seen_types = {item.type for item in items}
        for i, item in enumerate(items):
            pt = types.get(item.type)
            if pt is None:
                problems.append(
                    rules.Problem(
                        rules.SEVERITY_ERROR, t.ticket, f"`{key}[{i}]` の種類 `{item.type}` は無い"
                    )
                )
                continue
            if pt.kind != kind:
                where = "全体計画" if key == "plan" else "フィードバック計画"
                problems.append(
                    rules.Problem(
                        rules.SEVERITY_ERROR,
                        t.ticket,
                        f"`{key}[{i}]` の `{item.type}` は kind `{pt.kind}`。{where}には置けない",
                    )
                )
            if item.deferred and pt.review == phasetypes.REVIEW_NONE:
                problems.append(
                    rules.Problem(
                        rules.SEVERITY_ERROR,
                        t.ticket,
                        f"`{key}[{i}]` の `{item.type}` はレビュー不要の種類。延期するものが無い",
                    )
                )
            for need in pt.requires:
                if need not in seen_types:
                    problems.append(
                        rules.Problem(
                            rules.SEVERITY_ERROR,
                            t.ticket,
                            f"`{item.type}` を置くなら `{need}` も {key} に要る（requires）",
                        )
                    )
        # 延期の先は、レビューがある項でなければならない。全体計画は待ち方（`workflow`）が
        # 引き受け手を決めるので、そちらで見る。
        if key == "plan":
            continue
        for i, item in enumerate(items):
            if not item.deferred:
                continue
            target = next((x for x in items[i + 1 :] if not x.deferred), None)
            if target is None:
                continue
            tpt = types.get(target.type)
            if tpt is not None and tpt.review == phasetypes.REVIEW_NONE and target.review != "mr":
                problems.append(
                    rules.Problem(
                        rules.SEVERITY_ERROR,
                        t.ticket,
                        f"`{key}[{i}]` を延期した先の `{target.type}` にレビューが無い",
                    )
                )
    if not any(p.severity == rules.SEVERITY_ERROR for p in problems):
        problems.extend(workflow.problems(t, types))
    return problems


def revision_problems(
    root: str,
    conf: settings.Settings,
    revised: ticket_mod.Ticket,
    current: ticket_mod.Ticket,
    types: dict | None,
) -> list[rules.Problem]:
    """親の改版を受けてよいか（設計 9.7）。"""
    problems = plan_problems(revised, types)
    if any(p.severity == rules.SEVERITY_ERROR for p in problems):
        return problems
    # 変えられるのは計画だけ。
    if _scope_signature(revised) != _scope_signature(current):
        problems.append(
            rules.Problem(
                rules.SEVERITY_ERROR,
                revised.ticket,
                "改版で変えられるのは plan と feedback だけ。範囲が承認済みチケットと違う",
            )
        )
    if (
        revised.title != current.title
        or revised.issue != current.issue
        or revised.issue_repo != current.issue_repo
    ):
        problems.append(
            rules.Problem(
                rules.SEVERITY_ERROR,
                revised.ticket,
                "改版で変えられるのは plan と feedback だけ。題か課題番号が承認済みチケットと違う",
            )
        )
    # 全体計画: 子がある番号までは同じ順序でなければならない。
    if revised.plan != current.plan:
        frozen = _last_phase_with_children(conf, root, current.ticket)
        if frozen > len(revised.plan) or revised.plan[:frozen] != current.plan[:frozen]:
            problems.append(
                rules.Problem(
                    rules.SEVERITY_ERROR,
                    revised.ticket,
                    f"全体計画の {frozen} 番目までは子が承認されているので変えられない。"
                    "番号がずれると子の phase が指す先が変わる",
                )
            )
    # 延期の引き受け手: 子がある番号までは変えない。引き受け手が前へ動くと、延期した作業を
    # 済んだレビューが引き受けたことになり、誰にも見られずに終わる。
    frozen = _last_phase_with_children(conf, root, current.ticket)
    held = workflow.effective(current, types).review_at
    fresh = workflow.compute(revised, types).review_at
    moved = [
        n
        for n in sorted(set(held) | set(fresh))
        if held.get(n) != fresh.get(n)
        and (
            n <= frozen
            or any(at is not None and at <= frozen for at in (held.get(n), fresh.get(n)))
        )
    ]
    if moved:
        problems.append(
            rules.Problem(
                rules.SEVERITY_ERROR,
                revised.ticket,
                f"延期の引き受け手が変わる（{', '.join(map(str, moved))} 番目）。"
                f"{frozen} 番目までは子が承認されているので、延期の行き先は変えられない",
            )
        )
    # フィードバック計画: 無い状態から 1 回だけ、全体計画の最後のレビューが済んでから。
    if revised.feedback != current.feedback:
        if current.feedback is not None:
            # 残りの切り出し先は進め方で違う。マージリクエストがあれば issue に切り出せるが、
            # chat で回した親はホストに何も無いので、新しい親チケットの提案にする。
            elsewhere = (
                "残りは新しい親チケットの提案として wip/proposals/todo/ に書いてください"
                if phase.chat_only(root, conf, current.ticket)
                else "残りは別 issue に回してください（ccnavi-review.sh decide）"
            )
            problems.append(
                rules.Problem(
                    rules.SEVERITY_ERROR,
                    revised.ticket,
                    "フィードバック計画は 1 回だけ。承認済みのフィードバック作業フェーズに"
                    f"子を足してやり直すか、{elsewhere}",
                )
            )
        elif not phase.plan_finished(root, conf, current):
            problems.append(
                rules.Problem(
                    rules.SEVERITY_ERROR,
                    revised.ticket,
                    "フィードバック計画は、全体計画のフェーズが全部閉じてレビューが済んでから出す。"
                    "作業が終わるまで対応は計画できない",
                )
            )
    return problems


def revise_copy(
    approved_dir: str,
    current: ticket_mod.Ticket,
    revised: ticket_mod.Ticket,
    stamp: str,
    feedback_planned: bool,
    types: dict | None = None,
) -> str:
    """承認済みチケットの計画と待ち方を差し替える。範囲と承認の記録はそのまま。"""
    front = revised_front(current, revised, types)
    meta = dict(front.get(ticket_mod.APPROVAL_KEY) or {})
    meta["revised_at"] = stamp
    if feedback_planned:
        meta["feedback_at"] = stamp
    front[ticket_mod.APPROVAL_KEY] = meta
    current.raw = front
    failed = approval._write(
        approval.copy_path(approved_dir, current.ticket), ticket_mod.render(current)
    )
    if not failed:
        history.note(
            approved_dir,
            current.ticket,
            history.KIND_REVISED,
            ticket_mod.DOING,
            ticket_mod.DOING,
            feedback=True if feedback_planned else None,
        )
    return failed


def revised_front(
    current: ticket_mod.Ticket, revised: ticket_mod.Ticket, types: dict | None = None
) -> dict:
    """改版で書く frontmatter。承認済みチケットの frontmatter の計画と待ち方を差し替えたコピー。

    `current` は書き換えない。承認のダイジェスト（`digest`）も同じものから組むので、見せた
    中身と書く中身が食い違わない。
    """
    front = dict(current.raw)
    front["plan"] = [item.as_raw() for item in revised.plan]
    if revised.feedback is not None:
        front["feedback"] = [item.as_raw() for item in revised.feedback]
    front[ticket_mod.WORKFLOW_KEY] = workflow.compute(revised, types).as_raw()
    return front


def _scope_signature(t: ticket_mod.Ticket) -> tuple:
    return tuple((e.decision, e.glob, e.regex) for e in t.entries)


def _last_phase_with_children(conf: settings.Settings, root: str, parent_id: str) -> int:
    """この親で、子が承認された（開いていても閉じていても）いちばん後ろの番号。"""
    numbers = [
        t.phase
        for t in approval._everything(conf, root)
        if t.parent == parent_id and t.phase is not None
    ]
    return max(numbers) if numbers else 0


def validate(
    t: ticket_mod.Ticket, pool: dict[str, ticket_mod.Ticket], types: dict | None = None
) -> tuple[list[rules.Problem], list[rules.Problem]]:
    """承認の対象にしてよいかを見る。親子の制約はここでしか見られない。

    返すのは 2 つのリスト。1 つめはチケットの形の苦情で、error があれば承認しない。
    2 つめは範囲の超過（親の範囲・種類の上限を超えた項、regex の項）で、承認は止めない。
    判定が親と種類の上限で切り詰めるので、承認で止める理由が無い。

    形の検査を error に残すのは、**まとめて 1 度で見せて直させるため**。判定の側も同じ
    検査を当てる（`blocking_problems`）ので「判定では補えない」わけではないが、
    判定に任せると、承認の画面では通って、あとで書き込みが止まってから気づくことになる。
    承認はユーザがまとめて見て決める場所なので、そこで落ちるものはそこで言う。
    """
    problems: list[rules.Problem] = []
    overflow: list[rules.Problem] = []
    if not t.is_child:
        return plan_problems(t, types), overflow
    parent = pool.get(t.parent)
    problems.extend(approval.child_problems(t, parent))
    if parent is None or parent.is_child:
        return problems, overflow
    overflow.extend(ticket_mod.subset_problems(t, parent))
    if problems:
        # 番号が親の計画に無い。種類を引けないので、ここから先は見ても意味が無い。
        return problems, overflow
    if parent.has_plan and t.phase is not None:
        item = parent.item_at(t.phase)
        pt = (types or {}).get(item.type)
        if pt is None:
            problems.append(
                rules.Problem(
                    rules.SEVERITY_ERROR,
                    t.ticket,
                    f"{t.phase} 番目の種類 `{item.type}` の定義が読めない",
                )
            )
            return problems, overflow
        overflow.extend(phasetypes.scope_problems(t, pt))
    # regex の項は親の検査と種類の検査が同じ文で言う。画面に 2 度並べない。
    seen: set[str] = set()
    distinct = []
    for p in overflow:
        if p.detail not in seen:
            seen.add(p.detail)
            distinct.append(p)
    return problems, distinct
