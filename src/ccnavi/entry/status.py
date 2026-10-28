"""`ccnavi ticket status [<親>]`。承認済みチケットの状態を ccnavi が判断して言う。

エージェントがファイルと `git status` を読んで推測すると、手で置いた承認（欄が無く未コミット）を
「承認が途中で止まった」と読み違える。承認はチケットの中身を変えないので、どの経路で承認したかは
中身からは分からない。分かるのは置き場・git で、それを読んで言い切るのがここ。

`.ccnavi/scripts/ccnavi-ticket.sh status [<親>]` が呼ぶ。親を渡せばその親子のチケットを、
渡さなければ作業中・レビュー待ち・承認待ちのチケットがある親子を全部、閉じたものも含めて出す。

- 読むだけで、何も書かない。ネットワークにも出ない。push 済みかは手元のリモート追跡の ref で
  見るので、古いかもしれないことを出力に添える
- サブエージェントも打てる（hook が止めるサブコマンドの一覧に入れていない）。実行ファイルは呼び手が
  サブエージェントかを知らないので出力を分けず、状態を動かすコマンドの行には「親（メインエージェント）
  だけが実行する」と書く
- 未コミットの承認済みチケットは運ばない。運ぶのはユーザ（`ccnavi-user-push-approved.sh`）で、
  エージェントが運ぶコマンドは出さない。取り込み済みの親子（write-and-push の対象）では、運ぶまで
  `start` が止まることを言う
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TextIO

from ..infra import gitcmd, settings, tree
from ..tickets import ops_stop, overview
from ..tickets.approval import checks as approval_checks
from ..tickets.approval import store as approval
from ..tickets.approval import times as approval_times
from ..tickets.approval import write_and_push
from ..tickets.model import oplog, standing, ticket_model
from ..tickets.phase import gates as phase
from ..tickets.phase import manual_settle

TIMEOUT_SECONDS = 10.0

# 置き場のファイルの git での姿。`FILE_UNCOMMITTED` はファイルそのものがまだコミットに無い
# （承認が運ばれていない）。`FILE_MODIFIED` はコミット済みのファイルに未コミットの変更がある
# （着手などで書いた欄）。
FILE_UNCOMMITTED = "uncommitted"
FILE_MODIFIED = "modified"
FILE_UNPUSHED = "unpushed"
FILE_PUSHED = "pushed"
FILE_NO_UPSTREAM = "no-upstream"
FILE_UNKNOWN = "unknown"

PARENT_ONLY = "（親（メインエージェント）だけが実行する）"

_STATE_LABELS = {
    ticket_model.TODO: "承認待ち（todo/）",
    ticket_model.DOING: "作業中（doing/）",
    ticket_model.REVIEW: "レビュー待ち（review/）",
    ticket_model.DONE: "クローズ済み（done/）",
    ticket_model.CANCELLED: "クローズ済み（done/）",
}
_CLOSED = (ticket_model.DONE, ticket_model.CANCELLED)


@dataclass
class FileState:
    kind: str
    upstream: str = ""

    def label(self) -> str:
        if self.kind == FILE_UNCOMMITTED:
            return "未コミット"
        if self.kind == FILE_MODIFIED:
            return "コミット済みのファイルに未コミットの変更がある（着手などで書いた欄）"
        if self.kind == FILE_UNPUSHED:
            return f"コミット済み・未push（{self.upstream} に未反映の変更があります）"
        if self.kind == FILE_PUSHED:
            return f"push 済み（{self.upstream}）"
        if self.kind == FILE_NO_UPSTREAM:
            return "コミット済み（リモート追跡参照がないため、push済みか確認できません）"
        return "Gitから状態を取得できません。"


@dataclass
class Entry:
    """1 つの識別子の今。`hits` が 2 つ以上なら、どれが正か決まらない。"""

    ident: str
    hits: list[ticket_model.Ticket] = field(default_factory=list)

    @property
    def t(self) -> ticket_model.Ticket:
        return self.hits[0]


def run(stdout: TextIO, stderr: TextIO, root: str, conf: settings.Settings, family: str) -> int:
    """状態を出す。親を渡して何も見つからなければ 1。

    処理フロー:
    1. 作業中・完了・レビュー・提案の各チケットを集め、識別子ごとの `Entry` にまとめる。
       承認済みと同じ識別子の提案は、改版の候補として状態の相手にしない。
    2. 親（`family`）を渡されたら、その親と子のチケットを対象にする。見つからなければ、
       標準エラーに理由と注記を出して 1 で終える。渡されていないときは、作業中のものがある親を
       並べる。対象が無ければ、その旨を出して 0 で終える。
    3. 手元の置き場と git だけを読み、先頭に読んだ範囲と、リモートとの差の注意を出す。
    4. このPCで作業中の親の一覧（引数が無いときだけ）と、親ごとに子の状態と詳細
       （`_Family.describe`）、手作業の行（`_manual_lines`）を出す。
    5. 最後に、途中で止まった操作の一覧（`_unfinished_ops`）を出す。
    """
    raw = approval.read_raw(conf, root)
    doing, notes = approval.scan(conf, root, raw=raw)
    closed, more = approval.scan(conf, root, closed=True, raw=raw)
    notes += more
    review, more = approval.scan_review(conf, root, raw=raw)
    notes += more
    proposals, _ = approval.scan_proposals(conf, root, raw.everything)
    todo = [p for p in proposals if p.state == ticket_model.TODO]

    entries: dict[str, Entry] = {}
    for t in doing + review + closed:
        entries.setdefault(t.ticket, Entry(t.ticket)).hits.append(t)
    # 承認済みチケットと同じ識別子の `todo/` は改版の候補か書き損じで、状態の相手ではない。
    revisions = {t.ticket for t in todo if t.ticket in entries}
    for t in todo:
        if t.ticket not in entries:
            entries.setdefault(t.ticket, Entry(t.ticket)).hits.append(t)

    def home(e: Entry) -> str:
        return e.t.parent or e.t.ticket

    if family:
        chosen = {e.ident for e in entries.values() if home(e) == family}
        if not chosen:
            stderr.write(
                f"ccnavi: 親 {family} のチケットが見つからない"
                f"（{conf.approved}/ と {conf.tickets}/ の下を全ツリーで探した）\n"
            )
            for note in notes:
                stderr.write(f"  {note}\n")
            return 1
        families = [family]
    else:
        busy = {home(e) for e in entries.values() if any(h.state not in _CLOSED for h in e.hits)}
        families = sorted(busy)
    if not families:
        stdout.write("ccnavi: 作業中・レビュー待ち・承認待ちのチケットはありません。\n")
        _unfinished_ops(stdout, conf)
        return 0

    pool = approval_checks.predecessor_pool_of(doing, review, closed, proposals)
    # 承認と lint と同じ引き方にする。
    approval_checks.align_imported(conf, root, pool)
    times = approval_times.approved_times(conf, doing + review + closed)
    files = _file_states(root, [h for e in entries.values() for h in e.hits])
    fams = standing.Families(conf, root)
    sync = settings.script_command(root, "ccnavi-sync.sh")
    stdout.write(
        "ccnavi: チケットの状態。手元の置き場・git だけを読む"
        "（リモートからの取得は行いません）。\n"
        "  push済みかどうかは、最後に取得したリモートの状態に基づくため、"
        f"最新の状態と異なる場合があります。最新にするには先に '{sync} <親>' を実行してください。\n"
    )
    rows = {r.ident: r for r in overview.collect(conf, root, doing, review, closed, proposals)}
    if not family and rows:
        # 引数なし: このPCで作業中の親を 1 行ずつ、そのあとに親ごとの詳細
        stdout.write("\n== このPCで作業中の親\n")
        for row in rows.values():
            stdout.write(overview.line(row) + "\n")
    for name in families:
        members = sorted(
            (e for e in entries.values() if home(e) == name),
            key=lambda e: (bool(e.t.parent), e.t.phase or 0, e.ident),
        )
        ctx = _Family(root, conf, name, entries, pool, times, files, fams, raw)
        stdout.write(f"\n== 親子 {name}\n")
        row = rows.get(name)
        if row is not None and not family:
            for child, state in overview.child_states(root, row, doing, review):
                stdout.write(f"子のワークツリー: {child}  {state}\n")
        for e in members:
            for line in ctx.describe(e, e.ident in revisions):
                stdout.write(line + "\n")
        for line in _manual_lines(root, conf, name, doing):
            stdout.write(line + "\n")
    _unfinished_ops(stdout, conf)
    return 0


def _manual_lines(
    root: str, conf: settings.Settings, parent_id: str, doing: list[ticket_model.Ticket]
) -> list[str]:
    """手で done/ に動かされた子の確認待ち・未共有などを、フェーズごとに 1 行で出す。"""
    parent = approval_checks.by_id(doing).get(parent_id)
    if parent is None or parent.is_child:
        return []
    phases = phase.phases_of(root, conf, parent_id)
    out = []
    for ph in phases:
        if not ph.review_required:
            continue
        line = manual_settle.assess(root, conf, parent, ph, phases).line()
        if line:
            out.append(f"  フェーズ {ph.number}: {line}")
    return out


def _unfinished_ops(stdout: TextIO, conf: settings.Settings) -> None:
    """送り終えていない操作（引き継ぎ未完了）があれば、1 行ずつ出す。"""
    if not conf.state:
        return
    left = oplog.pending(conf.state)
    if not left:
        return
    stdout.write("\n== 引き継ぎ未完了（ホストへの送信が終わっていない操作）\n")
    for record in left:
        stdout.write(f"  {oplog.summary(record)}\n")
    stdout.write("  続きは 'ccnavi ops show <操作ID>' で確かめ、decide は --resume で再開する\n")


class _Family:
    """1 つの親子のチケットの中で、チケットごとの行を組む。"""

    def __init__(self, root, conf, name, entries, pool, times, files, fams, raw=None):
        self.root, self.conf, self.name = root, conf, name
        self.entries, self.pool, self.times, self.files = entries, pool, times, files
        self.fams = fams
        # `run` の頭で読んだ置き場。status は置き場を動かさないので、子ごとに読み直さずに渡す。
        self.raw = raw
        self._write_and_push: tuple[str, str] | None = None
        self.ticket_sh = settings.script_command(root, "ccnavi-ticket.sh")
        self.push_sh = settings.script_command(root, "ccnavi-user-push-approved.sh")

    def write_and_push_target(self) -> tuple[str, str]:
        """この親子が write-and-push の対象か（`ccnavi write-and-push family` と同じ
        `write_and_push.target`）と理由。1 度だけ引く。"""
        if self._write_and_push is None:
            verdict, why, _ = write_and_push.target(self.conf, self.root, self.name)
            self._write_and_push = (verdict, why)
        return self._write_and_push

    def describe(self, e: Entry, revision: bool) -> list[str]:
        """チケット 1 件の状態の行を組む（`run` の親ごとの詳細）。

        処理フロー:
        1. 同じ識別子が複数の置き場にあれば、場所を並べて止まる理由と次の操作だけを返す。
        2. 未承認（`todo`）は、置き場のファイルの状態と承認を待つ旨を返す。
        3. 承認の時刻と改版の有無を足す。閉じたもの（`_CLOSED`）は、取り消し・完了の別と
           置き場のファイルを返して終える。
        4. 作業中（`doing`）は `_doing` で止まっている理由と次の一手を足す。
        5. レビュー（`review`）は `phase.review_next` で次の操作を決める。依頼前は
           「レビュー準備中」、依頼後はユーザを待つ、と言い分ける。
        """
        t = e.t
        role = f"子（フェーズ {t.phase}）" if t.is_child else "親"
        if len(e.hits) > 1:
            where = ", ".join(f"{h.tree or 'ワークスペースルート'}:{h.state}" for h in e.hits)
            return [
                f"- {e.ident}  {role}  {t.title}".rstrip(),
                f"    置き場: 複数の場所にある（{where}）",
                "    止まっている理由: 正しいチケットを特定できないため、状態を変更できません。",
                "    次の操作: ユーザにチケットの重複を解消してもらってください。"
                "進行した側の変更をマージするか、不要なワークツリーを整理します。",
            ]
        lines = [
            f"- {e.ident}  {role}  {t.title}".rstrip(),
            f"    置き場: {_STATE_LABELS.get(t.state, t.state)}  "
            f"ツリー: {t.tree or 'ワークスペースルート'}"
            + (f"  プロジェクト: {t.project}" if t.project else ""),
        ]
        file_state = self.files.get(t.path, FileState(FILE_UNKNOWN))
        if t.state == ticket_model.TODO:
            lines.append(f"    置き場のファイル: {file_state.label()}")
            lines.append(
                "    次の操作: ユーザの承認を待つ（VS Code のボード、Chrome のボード、ターミナルの "
                "ccnavi-user-agree.sh、GitLab や GitHub の画面などで doing/ へ動かす、のどれか）"
            )
            return lines
        when = self.times.get(t.path)
        lines.append(f"    承認: {when.label() if when else '不明'}")
        if revision:
            lines.append("    改版: 同じ識別子の提案が todo/ にある（承認待ちの改版）")
        if t.state in _CLOSED:
            if t.cancelled_at:
                why = f"。理由: {t.cancel_reason}" if t.cancel_reason else ""
                lines.append(f"    閉じ方: 取り消し（{t.cancelled_at}{why}）")
            else:
                # 合流（finish が親のブランチへ取り込んだ子の先頭）。子だけが持つ
                merged = f"、合流 {t.merged_sha[:12]}" if t.merged_sha else ""
                lines.append(f"    閉じ方: 完了（{t.completed_at or '時刻なし'}{merged}）")
            lines.append(f"    置き場のファイル: {file_state.label()}")
            return lines
        lines.append(
            f"    着手: 着手済み（{t.started_at}、基準点 {t.base_sha or 'なし'}）"
            if t.started_at
            else "    着手: 未着手"
        )
        lines.append(f"    置き場のファイル: {file_state.label()}")
        if t.state == ticket_model.REVIEW:
            # 依頼の前は「レビュー準備中」で、動くのは親（合流・ELI5・request）。
            # 依頼の後に初めてユーザを待つ。
            nxt = phase.review_next(self.root, self.conf, t, raw=self.raw)
            if nxt and "request --phase" in nxt:
                nxt += PARENT_ONLY
            # 子のフェーズが読めないと、依頼済みかが分からない。分からないまま「待つ」と言うと、
            # 依頼の前でもユーザに回ったと読める（依頼の打ち忘れ）。
            lines.append(
                "    次の操作: "
                + (
                    nxt
                    or "フェーズを読めないので、レビューを依頼済みかを言えない。ユーザに回るのは、"
                    "親が合流・ELI5 を済ませて ccnavi-review.sh request で依頼した後"
                    "（依頼済みなら、ユーザが confirm / decide か Chrome 拡張のレビュー済みで "
                    "done/ へ動かすのを待つ）"
                    + PARENT_ONLY
                )
            )
            return lines
        return lines + self._doing(t, file_state)

    def _doing(self, t: ticket_model.Ticket, file_state: FileState) -> list[str]:
        """作業中のチケットの、止まっている理由・注意・次の一手。

        処理フロー:
        1. 親子の立ち位置（`family_standing`）と、チケットの止め（`blocked`）から、
           止まっている理由を集める。
        2. 未着手なら、write-and-push の対象か、先行や親の状態で止まっていないかを見る。
        3. 基準点が HEAD の祖先か、作業中なのに閉じるときに書く欄（`completed_at` など）に
           値が残っていないかを、注意として集める。
        4. 次の一手を決める。止まっていれば原因の解消、未着手なら開始の手順、
           着手後は finish を案内する。
        """
        stops: list[str] = []
        warns: list[str] = []
        st = approval_checks.family_standing(self.conf, self.root, t, self.fams)
        if st.stop:
            stops.append(f"{st.stop}（この親子のチケットの状態は動かさない）")
            stops += standing.guidance(self.root, st)
        if t.blocked:
            stops.append(t.blocked)
        uncommitted = file_state.kind == FILE_UNCOMMITTED
        verdict, why = self.write_and_push_target()
        if verdict == write_and_push.TARGET_STOP and not st.stop:
            # write-and-push の sh（ccnavi-ticket.sh）が状態の操作を断る。
            stops.append(
                f"write-and-push では状態の操作を実行できません: {why or '理由が分からない'}"
            )
        carried = uncommitted and verdict == write_and_push.TARGET_YES
        if not t.started_at:
            for p in approval_checks.unmet_predecessors(t, self.pool):
                stops.append(f"先行チケット {p.ticket} が開始条件を満たしていません（{p.label}）")
            parent_why = self._parent_not_started(t)
            if parent_why:
                stops.append(parent_why)
            if carried:
                stops.append(
                    "取り込み済みの親子（write-and-push の対象）で、"
                    "承認済みチケットが親のブランチに"
                    "未コミット。ユーザがコミットして push するまで start は実行できません"
                )
        # 基準点が HEAD の祖先でないのは warn。`start` は止めない（基準点を HEAD で書き直す）。
        off = ops_stop.base_off_head(self.root, self.conf, t)
        if off:
            warns.append(off)
        left = approval_checks.resumed_fields(t)
        if left:
            warns.append(
                f"作業中なのに {', '.join(left)} に値が残っている（done/ から手で戻した再開）。"
                "ユーザにその欄を空にしてもらう"
            )
        review_left = phase.resumed_review(self.root, self.conf, t, raw=self.raw)
        if review_left:
            warns.append(review_left)
        lines = [f"    止まっている理由: {s}" for s in stops]
        lines += [f"    注意: {w}" for w in warns]
        nexts: list[str] = []
        if uncommitted:
            nexts.append(
                f"承認済みチケットが未コミット。ユーザに '{self.push_sh} {self.name}' を"
                "実行してもらう（エージェントはコミットも push もしない）"
            )
        if stops:
            nexts.append("止まっている原因を解消する（ユーザに確かめる）")
        elif not t.started_at:
            if uncommitted and verdict == write_and_push.TARGET_NO:
                nexts.append(
                    "コミットと push を待たずに start へ進んでよい（この親子は write-and-push "
                    "の対象ではない）"
                )
            nexts += self._start_lines(t)
        elif t.is_child:
            nexts.append(
                f"作業を進め、終えたら '{self.ticket_sh} finish {t.ticket}' を実行する{PARENT_ONLY}"
            )
        else:
            nexts.append(
                "子を進める。全部のフェーズを閉じたら "
                f"'{self.ticket_sh} finish {t.ticket}' を実行する{PARENT_ONLY}"
            )
        return lines + [f"    次の操作: {n}" for n in nexts]

    def _start_lines(self, t: ticket_model.Ticket) -> list[str]:
        owner = tree.project_root(self.conf.projects, t.project) or self.root
        worktree = tree.worktree_path(self.root, t.ticket)
        out = []
        if not tree.is_worktree_of(owner, worktree):
            if t.is_child:
                git = settings.script_command(self.root, "ccnavi-git.sh")
                where = f"projects/{t.project} の中で " if t.project else ""
                out.append(
                    f'ワークツリーがありません。{where}\'{git} worktree add "{worktree}" '
                    f"-b {t.ticket} {t.parent}' で作る{PARENT_ONLY}"
                )
            else:
                # 親のワークツリーは親のブランチ（MR の元ブランチ）を直接チェックアウトする。
                # 識別子の名前のブランチは作らない。
                start = settings.script_command(self.root, "ccnavi-start.sh")
                out.append(
                    f"親のワークツリーがありません。'{start} --mr <MR の番号>'（MR が無ければ "
                    f"'--issue <issue の番号>'）で、親のブランチを直接チェックアウトして作る"
                    f"{PARENT_ONLY}"
                )
        out.append(f"'{self.ticket_sh} start {t.ticket}' で開始する{PARENT_ONLY}")
        return out

    def _parent_not_started(self, t: ticket_model.Ticket) -> str:
        if not t.is_child:
            return ""
        e = self.entries.get(t.parent)
        if e is None:
            return f"親 {t.parent} が見つからない"
        if len(e.hits) > 1:
            return f"親 {t.parent} が複数の場所にある"
        parent = e.t
        if parent.state == ticket_model.TODO:
            return f"親 {t.parent} がまだ承認されていない（todo/）"
        if parent.state != ticket_model.DOING:
            return f"親 {t.parent} は作業中ではない（{parent.state}/）"
        if not parent.started_at:
            return (
                f"親チケット {t.parent} が未着手です。"
                "子チケットより先に、親チケットを開始してください。"
            )
        return ""


def _file_states(root: str, found: list[ticket_model.Ticket]) -> dict[str, FileState]:
    """チケットのファイルごと（パスで引く）の git での姿。ツリーごとに git を数回だけ打つ。"""
    by_tree: dict[str, list[ticket_model.Ticket]] = {}
    for t in found:
        if t.tree_root and t.path:
            by_tree.setdefault(t.tree_root, []).append(t)
    out: dict[str, FileState] = {}
    for tree_root, tickets in by_tree.items():
        rels = {t.path: os.path.relpath(t.path, tree_root).replace(os.sep, "/") for t in tickets}
        found = _dirty(tree_root, list(rels.values()))
        if found is None:
            continue
        added, dirty = found
        upstream = _upstream(tree_root)
        ahead = _ahead(tree_root, upstream, list(rels.values())) if upstream else set()
        for path, rel in rels.items():
            if rel in added:
                out[path] = FileState(FILE_UNCOMMITTED)
            elif rel in dirty:
                out[path] = FileState(FILE_MODIFIED)
            elif not upstream:
                out[path] = FileState(FILE_NO_UPSTREAM)
            elif ahead is None:
                out[path] = FileState(FILE_UNKNOWN)
            elif rel in ahead:
                out[path] = FileState(FILE_UNPUSHED, upstream)
            else:
                out[path] = FileState(FILE_PUSHED, upstream)
    return out


def _dirty(tree_root: str, rels: list[str]) -> tuple[set[str], set[str]] | None:
    """(まだコミットに無いパス, 未コミットの変更があるパス)。git を読めなければ None。

    前者は未追跡と、索引に足しただけのもの（`??`・`A`）。改名で入ったもの（`R`）も、
    新しいパスはまだコミットに無いので前者に入れる。
    """
    done = gitcmd.run(
        tree_root,
        ["status", "--porcelain=v1", "-z", "--untracked-files=all", "--", *rels],
        TIMEOUT_SECONDS,
        raw_paths=True,
    )
    if not done.ok:
        return None
    added: set[str] = set()
    found: set[str] = set()
    fields = done.out.split("\0")
    i = 0
    while i < len(fields):
        item = fields[i]
        i += 1
        if len(item) < 4:
            continue
        found.add(item[3:])
        if item[:2] == "??" or item[0] in "ARC":
            added.add(item[3:])
        if item[0] in "RC":
            # 改名とコピーは、元のパスが次の欄に続く。
            if i < len(fields):
                found.add(fields[i])
            i += 1
    return added, found


def _upstream(tree_root: str) -> str:
    """今のブランチのリモート追跡の ref。設定が無ければ `origin/<ブランチ>` があればそれ。"""
    rc, out = gitcmd.output(
        tree_root,
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
        TIMEOUT_SECONDS,
    )
    if rc == 0 and out.strip():
        return out.strip()
    branch = tree.branch_of(tree_root)
    if not branch:
        return ""
    ref = f"refs/remotes/origin/{branch}"
    rc, _ = gitcmd.output(tree_root, ["rev-parse", "--verify", "--quiet", ref], TIMEOUT_SECONDS)
    return f"origin/{branch}" if rc == 0 else ""


def _ahead(tree_root: str, upstream: str, rels: list[str]) -> set[str] | None:
    """リモート追跡の ref と分かれたあとに、手元のコミットで変えたパス。読めなければ None。"""
    done = gitcmd.run(
        tree_root,
        ["diff", "--name-only", "-z", "--no-renames", f"{upstream}...HEAD", "--", *rels],
        TIMEOUT_SECONDS,
        raw_paths=True,
    )
    if not done.ok:
        return None
    return {p for p in done.out.split("\0") if p}
