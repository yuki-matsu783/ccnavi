"""`ccnavi ticket status [<親>]`。承認済みチケットの状態を ccnavi が判断して言う。

エージェントがファイルと `git status` を読んで推測すると、手で置いた承認（欄が無く未コミット）を
「承認が途中で止まった」と読み違える。承認はチケットの中身を変えないので、どの経路で承認したかは
中身からは分からない。分かるのは置き場・状態の履歴・git で、それを読んで言い切るのがここ。

`.ccnavi/scripts/ccnavi-ticket.sh status [<親>]` が呼ぶ。親を渡せばその親子のチケットを、
渡さなければ作業中・レビュー待ち・承認待ちのチケットがある親子を全部、閉じたものも含めて出す。

- 読むだけで、何も書かない。ネットワークにも出ない。push 済みかは手元のリモート追跡の ref で
  見るので、古いかもしれないことを出力に添える
- サブエージェントも打てる（hook が止める副命令の一覧に入れていない）。実行ファイルは呼び手が
  サブエージェントかを知らないので出力を分けず、状態を動かすコマンドの行には「親（メインエージェント）
  だけが実行する」と書く
- 未コミットの承認済みチケットは運ばない。運ぶのはユーザ（`ccnavi-push-approved.sh`）で、
  エージェントが運ぶコマンドは出さない。取り込み済みの親子（C1 の対象）では、運ぶまで
  `start` が止まることを言う
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TextIO

from ..hook import c1
from ..infra import fsio, gitcmd, settings, tree
from ..tickets import approval, history, ops, syncstate
from ..tickets import ticket as ticket_mod

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
    ticket_mod.TODO: "承認待ち（todo/）",
    ticket_mod.DOING: "作業中（doing/）",
    ticket_mod.REVIEW: "レビュー待ち（review/）",
    ticket_mod.DONE: "閉じた（done/）",
    ticket_mod.CANCELLED: "閉じた（done/）",
}
_CLOSED = (ticket_mod.DONE, ticket_mod.CANCELLED)


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
            return f"コミット済み・未 push（{self.upstream} より先）"
        if self.kind == FILE_PUSHED:
            return f"push 済み（{self.upstream}）"
        if self.kind == FILE_NO_UPSTREAM:
            return "コミット済み（リモート追跡の ref が無いので、push 済みかは分からない）"
        return "git で読めない"


@dataclass
class Entry:
    """1 つの識別子の今。`hits` が 2 つ以上なら、どれが本物か決まらない。"""

    ident: str
    hits: list[ticket_mod.Ticket] = field(default_factory=list)

    @property
    def t(self) -> ticket_mod.Ticket:
        return self.hits[0]


def run(stdout: TextIO, stderr: TextIO, root: str, conf: settings.Settings, family: str) -> int:
    """状態を出す。親を渡して何も見つからなければ 1。"""
    raw = approval.read_raw(conf, root)
    doing, notes = approval.scan(conf, root, raw=raw)
    closed, more = approval.scan(conf, root, closed=True, raw=raw)
    notes += more
    review, more = approval.scan_review(conf, root, raw=raw)
    notes += more
    proposals, _ = approval.scan_proposals(conf, root, raw.everything)
    todo = [p for p in proposals if p.state == ticket_mod.TODO]

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
        stdout.write("ccnavi: 作業中・レビュー待ち・承認待ちのチケットは無い\n")
        return 0

    pool = approval.predecessor_pool_of(doing, review, closed, proposals)
    times = approval.approved_times(conf, doing + review + closed)
    files = _file_states(root, [h for e in entries.values() for h in e.hits])
    fams = syncstate.Families(conf, root)
    sync = settings.script_command(root, "ccnavi-sync.sh")
    stdout.write(
        "ccnavi: チケットの状態。手元の置き場・状態の履歴・git だけを読む"
        "（リモートへは取りに行かない）。\n"
        "  push 済みかは手元のリモート追跡の ref（最後に取り込んだときの姿）で見るので、"
        f"古いかもしれない。最新にするには先に '{sync} <親>' を打つ\n"
    )
    for name in families:
        members = sorted(
            (e for e in entries.values() if home(e) == name),
            key=lambda e: (bool(e.t.parent), e.t.phase or 0, e.ident),
        )
        ctx = _Family(root, conf, name, entries, pool, times, files, fams)
        stdout.write(f"\n== 親子 {name}\n")
        for e in members:
            for line in ctx.describe(e, e.ident in revisions):
                stdout.write(line + "\n")
    return 0


class _Family:
    """1 つの親子のチケットの中で、チケットごとの行を組む。"""

    def __init__(self, root, conf, name, entries, pool, times, files, fams):
        self.root, self.conf, self.name = root, conf, name
        self.entries, self.pool, self.times, self.files = entries, pool, times, files
        self.fams = fams
        self._c1: tuple[str, str] | None = None
        self.ticket_sh = settings.script_command(root, "ccnavi-ticket.sh")
        self.push_sh = settings.script_command(root, "ccnavi-push-approved.sh")

    def c1_target(self) -> tuple[str, str]:
        """この親子が C1 の対象か（`ccnavi c1 family` と同じ `c1.target`）と理由。1 度だけ引く。"""
        if self._c1 is None:
            verdict, why, _ = c1.target(self.conf, self.root, self.name)
            self._c1 = (verdict, why)
        return self._c1

    def describe(self, e: Entry, revision: bool) -> list[str]:
        t = e.t
        role = f"子（フェーズ {t.phase}）" if t.is_child else "親"
        if len(e.hits) > 1:
            where = ", ".join(f"{h.tree or 'ワークスペースルート'}:{h.state}" for h in e.hits)
            return [
                f"- {e.ident}  {role}  {t.title}".rstrip(),
                f"    置き場: 複数の場所にある（{where}）",
                "    止まっている理由: どれが本物か決まらないので、状態の操作は止まる",
                "    次の一手: ユーザに 1 つにしてもらう（先に進んだ側を合流させるか、"
                "残ったワークツリーを片付ける）",
            ]
        lines = [
            f"- {e.ident}  {role}  {t.title}".rstrip(),
            f"    置き場: {_STATE_LABELS.get(t.state, t.state)}  "
            f"ツリー: {t.tree or 'ワークスペースルート'}"
            + (f"  プロジェクト: {t.project}" if t.project else ""),
        ]
        file_state = self.files.get(t.path, FileState(FILE_UNKNOWN))
        if t.state == ticket_mod.TODO:
            lines.append(f"    置き場のファイル: {file_state.label()}")
            lines.append(
                "    次の一手: ユーザの承認を待つ（ボード、端末の ccnavi-agree.sh、"
                "GitHub の画面で doing/ へ動かす、のどれか）"
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
                lines.append(f"    閉じ方: 完了（{t.completed_at or '時刻なし'}）")
            lines.append(f"    置き場のファイル: {file_state.label()}")
            return lines
        lines.append(
            f"    着手: 着手済み（{t.started_at}、基準点 {t.base_sha or 'なし'}）"
            if t.started_at
            else "    着手: 未着手"
        )
        lines.append(f"    置き場のファイル: {file_state.label()}")
        if t.state == ticket_mod.REVIEW:
            lines.append(
                "    次の一手: ユーザのレビューを待つ（ユーザが ccnavi-review.sh confirm / decide"
                " で done/ へ動かす）"
            )
            return lines
        return lines + self._doing(t, file_state)

    def _doing(self, t: ticket_mod.Ticket, file_state: FileState) -> list[str]:
        """作業中のチケットの、止まっている理由・注意・次の一手。"""
        stops: list[str] = []
        warns: list[str] = []
        st = approval.family_standing(self.conf, self.root, t, self.fams)
        if st.imported and st.stop:
            stops.append(f"{st.stop}（この親子のチケットの状態は動かさない）")
            stops += syncstate.guidance(self.root, st)
        if t.blocked:
            stops.append(t.blocked)
        uncommitted = file_state.kind == FILE_UNCOMMITTED
        verdict, why = self.c1_target()
        if verdict == c1.TARGET_STOP and not (st.imported and st.stop):
            # C1 の sh（ccnavi-ticket.sh）が状態の操作を断る。
            stops.append(f"C1 で状態の操作が止まる: {why or '理由が分からない'}")
        carried = uncommitted and verdict == c1.TARGET_YES
        if not t.started_at:
            for p in approval.unmet_predecessors(t, self.pool):
                stops.append(f"先行 {p.ticket} が満たされていない（{p.label}）")
            parent_why = self._parent_not_started(t)
            if parent_why:
                stops.append(parent_why)
            if carried:
                stops.append(
                    "取り込み済みの親子（C1 の対象）で、承認済みチケットが親のブランチに"
                    "未コミット。ユーザが運ぶまで start は止まる"
                )
        else:
            if not _started_recorded(self.conf, t):
                warns.append(
                    "着手の欄（started_at）があるのに、状態の履歴に着手（started）の行が無い。"
                    "start を通さずに書かれた欄かもしれない。ユーザに started_at と base_sha を"
                    "確かめてもらう"
                )
        # 基準点が HEAD の祖先でないのは warn。`start` は止めない（基準点を HEAD で書き直す）。
        off = ops.base_off_head(self.root, self.conf, t)
        if off:
            warns.append(off)
        left = approval.resumed_fields(t)
        if left:
            warns.append(
                f"作業中なのに {', '.join(left)} に値が残っている（done/ から手で戻した再開）。"
                "ユーザにその欄を空にしてもらう"
            )
        if (
            not t.is_child
            and t.has_plan
            and not approval.has_record(t)
            and not fsio.lexists(
                approval.workflow_path(settings.approved_dir(self.conf, t.tree_root), t.ticket)
            )
        ):
            warns.append(
                "待ち方の固定（phases/<親>/workflow.yml）が無いので、全体計画を一直線"
                "（前の番号を全部待つ）で読んでいる。並行にしたければ、改版でユーザに"
                " --agree を通してもらう"
            )
        if t.workflow_record_differs:
            warns.append(
                "古い形の workflow: 欄の待ち方が今の phases.yml から計算した待ち方と違うので、"
                "全体計画を一直線（前の番号を全部待つ）で読んでいる。並行にしたければ、改版でユーザに"
                " --agree を通してもらう"
            )
        lines = [f"    止まっている理由: {s}" for s in stops]
        lines += [f"    注意: {w}" for w in warns]
        nexts: list[str] = []
        if uncommitted:
            nexts.append(
                f"承認済みチケットが未コミット。ユーザに '{self.push_sh} {self.name}' を"
                "打ってもらう（エージェントは運ばない）"
            )
        if stops:
            nexts.append("止まっている理由を解く（ユーザに確かめる）")
        elif not t.started_at:
            if uncommitted and verdict == c1.TARGET_NO:
                nexts.append("運ぶのを待たずに start へ進んでよい（この親子は C1 の対象ではない）")
            nexts += self._start_lines(t)
        elif t.is_child:
            nexts.append(
                f"作業を進め、終えたら '{self.ticket_sh} finish {t.ticket}' を打つ{PARENT_ONLY}"
            )
        else:
            nexts.append(
                "子を進める。全部のフェーズを閉じたら "
                f"'{self.ticket_sh} finish {t.ticket}' を打つ{PARENT_ONLY}"
            )
        return lines + [f"    次の一手: {n}" for n in nexts]

    def _start_lines(self, t: ticket_mod.Ticket) -> list[str]:
        owner = tree.project_root(self.conf.projects, t.project) or self.root
        worktree = tree.worktree_path(self.root, t.ticket)
        out = []
        if not (tree.is_worktree_of(owner, worktree) and tree.exact_name(self.root, t.ticket)):
            git = settings.script_command(self.root, "ccnavi-git.sh")
            where = f"projects/{t.project} の中で " if t.project else ""
            base = f" {t.parent}" if t.is_child else ""
            out.append(
                f'ワークツリーが無い。{where}\'{git} worktree add "{worktree}" -b {t.ticket}'
                f"{base}' で作る{PARENT_ONLY}"
            )
        out.append(f"'{self.ticket_sh} start {t.ticket}' で着手する{PARENT_ONLY}")
        return out

    def _parent_not_started(self, t: ticket_mod.Ticket) -> str:
        if not t.is_child:
            return ""
        e = self.entries.get(t.parent)
        if e is None:
            return f"親 {t.parent} が見つからない"
        if len(e.hits) > 1:
            return f"親 {t.parent} が複数の場所にある"
        parent = e.t
        if parent.state == ticket_mod.TODO:
            return f"親 {t.parent} がまだ承認されていない（todo/）"
        if parent.state != ticket_mod.DOING:
            return f"親 {t.parent} は作業中ではない（{parent.state}/）"
        if not parent.started_at:
            return f"親 {t.parent} が未着手。子より先に親に着手する"
        return ""


def _started_recorded(conf: settings.Settings, t: ticket_mod.Ticket) -> bool:
    """状態の履歴に着手の行があるか。読めなければ、あるとみなす（言わない側）。"""
    if not t.tree_root:
        return True
    entries, why = history.read(settings.approved_dir(conf, t.tree_root), t.ticket, limit=0)
    return bool(why) or any(x.get("kind") == history.KIND_STARTED for x in entries)


def _file_states(root: str, found: list[ticket_mod.Ticket]) -> dict[str, FileState]:
    """チケットのファイルごと（パスで引く）の git での姿。ツリーごとに git を数回だけ打つ。"""
    by_tree: dict[str, list[ticket_mod.Ticket]] = {}
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
            # 改名と複写は、元のパスが次の欄に続く。
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
