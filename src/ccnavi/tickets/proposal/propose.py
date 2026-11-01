"""提案を親のブランチへ送る（`ccnavi ticket approval-request <識別子>`）。

エージェントが書いた承認待ちの提案（`wip/proposals/todo/`）と、子のフローの下書き
（`wip/proposals/flows/`）は、書いただけでは手元にしかない。別のPCで承認して作業に入るには、
親のブランチに載って届かなければならない。送る入口がここ。

実行ファイルは書き換えずに、送る提案のパスを「この実行で書いたもの」の一覧に載せるだけ
（`fsio.note_input`）。ロック・取り込み・`commit --only`・push は sh（`ccnavi-ticket.sh
approval-request`）が
write-and-push の手順で行う。コミットされるのは、未追跡のものと書き換えたものだけ。
変わっていなければコミットせず、未 push のコミットがあればそれを送る。

## 決まり

- 対象は、識別子の親子の提案だけ。`todo/<親>.md`、`todo/<子>.md`、`flows/<子>.yml`。
- 計画を持つ親の提案（`todo/<親>.md` で `plan:` か `feedback:` を持つもの）だけ、
  送る前に計画の `after` の形と終端を検査する
  （`workflow.after_problems`・`workflow.terminal_problems`）。
  承認で落ちる誤りを承認の前に返すためである。誤りがあれば何も送らない。
  子の提案・子のフロー・計画の無い提案・読めない提案は検査しない
- まだ承認されていない（最初の）親の提案は、すぐ始まるステップに子の提案が 1 つも無ければ、
  送りはするが標準エラーに警告する（`workflow.childless_ready`。承認の確認と同じ判定）。親を先に承認して子を
  後から出す進め方も正規なので止めない。改版には効かせない
- 親のワークツリー（`.claude/worktrees/<親>`）の中のものだけを見る
- 送る提案が 1 本も無ければ、何も載せずに止める
- origin の無い親子には送る先がないので、何も載せずに案内して終える
- 親のワークツリーが決まらない親子は止める（write-and-push と同じ理由）

ネットワークにも git にも触らない。
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import TextIO

from ...infra import fsio, pathcase, settings
from ...policy import texts
from ..approval import checks as approval_checks
from ..approval import store as approval
from ..approval import write_and_push
from ..config import workflow
from ..model import ticket, ticket_ids, ticket_model

FLOWS = "flows"


def _belongs(stem: str, parent: str) -> bool:
    """ファイル名の語幹が、この親か、その子の識別子か。名前は大文字小文字を問わずに比べる。"""
    if pathcase.fold(stem) == pathcase.fold(parent):
        return True
    matched = ticket_ids.child_pattern().fullmatch(stem)
    return matched is not None and pathcase.fold(matched.group("parent")) == pathcase.fold(parent)


def _candidates(base: str, tickets: str, parent: str) -> list[str]:
    """親のワークツリー `base` の中の、この親子の提案と子のフローの下書き（絶対パス、名前順）。"""
    found: list[str] = []
    for folder, suffixes in ((ticket_model.TODO, (".md",)), (FLOWS, (".yml", ".yaml"))):
        directory = os.path.join(base, *tickets.strip("/").split("/"), folder)
        try:
            names = sorted(fsio.listdir(directory))
        except OSError:
            continue
        for name in names:
            stem, ext = os.path.splitext(name)
            if pathcase.fold(ext) not in suffixes or not _belongs(stem, parent):
                continue
            path = os.path.join(directory, name)
            if os.path.isfile(path) and not os.path.islink(path):
                found.append(path)
    return found


def _plan_problems(paths: list[str], parent: str) -> list[str]:
    """計画を持つ親の提案の `after` と終端の誤りを返す。文面は `workflow` が返すものを使う。"""
    found: list[str] = []
    for path in paths:
        stem, ext = os.path.splitext(os.path.basename(path))
        folder = os.path.basename(os.path.dirname(path))
        in_todo = pathcase.fold(folder) == pathcase.fold(ticket_model.TODO)
        if (
            not in_todo
            or pathcase.fold(stem) != pathcase.fold(parent)
            or pathcase.fold(ext) != ".md"
        ):
            continue
        try:
            loaded, _ = ticket.load(path)
        except ValueError:  # UTF-8 で読めない提案は検査せず、今までどおり送る
            continue
        if loaded is None or not (loaded.plan or loaded.feedback):
            continue
        found += [
            p.detail for p in workflow.after_problems(loaded) + workflow.terminal_problems(loaded)
        ]
    return found


def _childless_problems(
    conf: settings.Settings, root: str, paths: list[str], parent: str
) -> list[str]:
    """最初の親の提案で、すぐ始まるステップに子の提案が無い警告。承認の確認と同じ文面を返す。"""
    own = next(
        (
            p
            for p in paths
            if pathcase.fold(os.path.basename(p)) == pathcase.fold(parent + ".md")
            and pathcase.fold(os.path.basename(os.path.dirname(p)))
            == pathcase.fold(ticket_model.TODO)
        ),
        "",
    )
    if not own:
        return []
    try:
        loaded, _ = ticket.load(own)
    except ValueError:
        return []
    if loaded is None or not loaded.plan:
        return []
    open_, _ = approval.scan(conf, root)
    closed, _ = approval.scan(conf, root, closed=True)
    if parent in approval_checks.by_id(open_ + closed):
        return []  # 承認済みの親の改版。子はもう承認の側にある
    phases = set()
    for path in paths:
        matched = ticket_ids.child_pattern().fullmatch(os.path.splitext(os.path.basename(path))[0])
        if matched is not None and path.endswith(".md"):
            phases.add(int(matched.group("phase")))
    return [p.detail for p in workflow.childless_ready(loaded, phases)]


def run(
    stdout: TextIO,
    stderr: TextIO,
    root: str,
    conf: settings.Settings,
    ident: str,
    check: bool = False,
    gate: Callable[[list[str]], int] | None = None,
) -> int:
    """`ticket approval-request <識別子>`。送る提案を一覧に載せ、載せたものを標準出力に 1
    行ずつ出す。

    `gate` は、送る提案の識別子（`todo/` の `.md`）を受け取り、承認の画面と同じ検査を通す
    （`core.approve.approval_request_gate`。通らなければ 0 以外）。ここは提案の層なので、
    検査そのものは呼び手から受け取る。`check` は検査だけをして、何も一覧に載せない。
    """
    parent = write_and_push.family_of(ident)
    verdict, why, st = write_and_push.target(conf, root, parent)
    if verdict == write_and_push.TARGET_STOP or st.home is None:
        stderr.write(f"ccnavi: 提案を送れません（{why or '親のワークツリーが決まらない'}）。\n")
        return 1
    if verdict == write_and_push.TARGET_NO:
        stdout.write(
            f"案内: {parent} には送る先がありません（{why}）。提案は手元に書かれたままです。\n"
        )
        return 0
    base = st.home.root
    tickets = conf.tickets or settings.DEFAULT_TICKETS
    paths = _candidates(base, tickets, parent)
    if not paths:
        stderr.write(
            f"ccnavi: 送る提案がありません（親のワークツリー {base} の "
            f"{tickets.strip('/')}/{ticket_model.TODO}/{parent}.md など）。"
            "提案は識別子と同名のワークツリーの中に書いてください。\n"
        )
        return 1
    wrong = _plan_problems(paths, parent)
    if wrong:
        stderr.write(
            f"ccnavi: {parent} の提案の計画に誤りがあるので送りません（何も送っていません）。\n"
        )
        for detail in wrong:
            stderr.write(f"  {detail}\n")
        stderr.write(f"次: 直して `ccnavi-ticket.sh approval-request {parent}` を打ち直す\n")
        return 1
    for detail in _childless_problems(conf, root, paths, parent):
        stderr.write(f"ccnavi: 注意: {detail}\n")
    if gate is not None:
        sending = [
            os.path.splitext(os.path.basename(p))[0]
            for p in paths
            if p.endswith(".md") and os.path.basename(os.path.dirname(p)) == ticket_model.TODO
        ]
        if gate(sending) != 0:
            return 1
    if check:
        stdout.write("確認: ok（approval-request を打てる状態。提案は送っていません）\n")
        stdout.write(texts.say(root, "NEXT_APPROVAL_REQUEST_CHECKED", {"parent": parent}) + "\n")
        return 0
    for path in paths:
        fsio.note_input(path)
        rel = os.path.relpath(path, base).replace(os.sep, "/")
        stdout.write(f"提案: {rel}\n")
    return 0
