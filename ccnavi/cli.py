"""標準入出力とコマンドラインを 1 つの判定に繋ぐ。

run は終了コードを返す。自分で終了しないので、道具ぜんぶを別プロセス無しで
テストから動かせる。

ここに置くのは引数の解釈と振り分けだけ。hook のイベントごとの手順は events、
実行前の判定は judge、判定に添える文面は reasons、モードと終了コードは modes に
ある。チケットとレビューの操作（`ticket ...` / `review ...`）は operate が
ops / review へ渡す。
"""

from __future__ import annotations

import argparse
import os
import time
from typing import TextIO

from . import (
    approval,
    audit,
    diagnose,
    events,
    fsio,
    hookio,
    judge,
    lint,
    modes,
    ops,
    review,
    ruleload,
    selfguard,
    settings,
)

# パスの解決は judge に移した。tests/test_paths.py がここから import しているので、
# 名前だけ残す。
from .judge import full_path as full_path
from .modes import EXIT_ERROR, EXIT_OK

USAGE = """ccnavi guards agent tool calls and guides the agent to a safer alternative.

It is a hook command, not something to run by hand: it reads one hook payload
as JSON on stdin and writes its response to stdout.

Register it on PreToolUse to judge calls before they run, and on PostToolUse to
watch the working tree for protected files that changed anyway. Exercise it with

    echo '{"hook_event_name":"PreToolUse","tool_name":"Bash",
           "tool_input":{"command":"git push"}}' | ccnavi

To check the rules file and the settings without making a decision, run

    ccnavi --lint

It reads no payload, reports anything that could disable the guard as an error
or a warning, and exits non-zero when it reports an error.

To list the tickets, their approved copies, the phase marks and the gates
in a machine-readable form (the VS Code board extension reads this), run

    ccnavi --explain --json

It reads no payload and never reaches the remote. The shape is documented
in README.md ("ボードの JSON").

To try one call against the rules without running it, or to run every sample
in a file against them, run

    ccnavi --test Bash "git push" [--json]
    ccnavi --test-samples testdata/rule-samples.yml [--json]

Both go through the same decision as the hook. --json prints the shape
documented in README.md ("試験の JSON"); the VS Code extension reads it.

To review the pending tickets and approve the work areas they declare, run

    ccnavi --approve

It scans wip/tickets/ in every worktree, shows what each ticket makes writable
and whether it needs a human review, then keeps an approved copy under
.claude/ccnavi/tickets/. Only the copies are consulted when judging calls, so
editing a ticket never widens the area on its own.

The parent agent moves tickets between states and asks for reviews through the
scripts in .claude/scripts/, which call

    ccnavi ticket start|done|cancel <id> [--reason <why>]
    ccnavi ticket judge <child> <factor> yes|no --reason <why>   (qualitative risk)
    ccnavi review prepare   --cwd <dir> --phase N --body-file <path>
    ccnavi review requested --cwd <dir> --phase N --result <json>
    ccnavi review check     --cwd <dir> --phase N --result <json>
    ccnavi review handoff   --cwd <dir> --body-file <path> --result <json>
    ccnavi review ready     --cwd <dir> --result <json>

ccnavi never reaches the remote itself. The script fetches the merge request,
its threads and reviews, and hands them over as --result <json>.

A human accepts unresolved review threads with

    sh .claude/scripts/ccnavi-review.sh accept N

which fetches the threads and runs

    ccnavi --reviewed N --accept-unresolved --result <json> --cwd <parent worktree>

A human closes a parent early ("good enough for now") with

    sh .claude/scripts/ccnavi-review.sh wrapup --reason <why>

which runs `ccnavi review wrapup --reason <why> --result <json>` and then
un-drafts the merge request and files the leftovers as a new issue.
"""


# フラグで上書きする設定の欄と、空文字を「指定した」と読むかどうか。
# 空文字を受ける欄は、既定が None のフラグで運ぶ。「記録しない」「控えを持たない」を
# 言えないと、診断のための 1 回が、走っているセッションの記録と控えに必ず混ざる。
OVERRIDES = (
    ("log", True),
    ("rules", False),
    ("state", True),
    ("approved", True),
    ("phases", True),
    ("risk", True),
    ("projects", True),
)
# 作業ツリーのルートからの相対で書く欄。区切りを "/" に揃え、前後の "/" を落とす。
RELATIVE_OVERRIDES = ("tickets", "project_rules")


def _override(conf: settings.Settings, args: argparse.Namespace) -> None:
    """フラグを設定に重ねる。フラグは何よりも強い。

    診断のための実行が、プロジェクト全体で共有しているファイルに触らずに
    別の場所を指せるように。
    """
    for name, accepts_empty in OVERRIDES:
        value = getattr(args, name)
        if value is not None and (accepts_empty or value):
            setattr(conf, name, value)
    for name in RELATIVE_OVERRIDES:
        value = getattr(args, name)
        if value:
            setattr(conf, name, fsio.slashed(value).strip("/"))


def run(stdin: TextIO, stdout: TextIO, stderr: TextIO, argv: list[str]) -> int:
    """1 回の起動を処理する。"""
    parser = argparse.ArgumentParser(prog="ccnavi", add_help=False)
    parser.add_argument("--root", default=None)
    parser.add_argument("--mode", default="")
    parser.add_argument("--rules", default="")
    parser.add_argument("--log", default=None)
    parser.add_argument("--state", default=None)
    parser.add_argument("--restore-if-deny", default="")
    parser.add_argument("--guard-core-files", default="")
    parser.add_argument("--guard-ticket-approval", default="")
    # リモートの写し（JSON）。.claude/scripts/ccnavi-review.sh が取ってきて渡す。
    parser.add_argument("--result", default="")
    parser.add_argument("--lint", action="store_true")
    parser.add_argument("--approve", action="store_true")
    parser.add_argument("--test", nargs=2, metavar=("TOOL", "SUBJECT"), default=None)
    # 見本をぜんぶ判定に掛ける。testdata/check_rules.py と VS Code 拡張が呼ぶ。
    parser.add_argument("--test-samples", metavar="FILE", default="")
    parser.add_argument("--explain", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--tickets", default="")
    parser.add_argument("--approved", default=None)
    parser.add_argument("--phases", default=None)
    parser.add_argument("--risk", default=None)
    # プロジェクトの置き場と、プロジェクトごとのルールファイル（設計 §25）。
    parser.add_argument("--projects", default=None)
    parser.add_argument("--project-rules", default="")
    # チケットの状態とレビューの操作。人か、親が保護済みスクリプトから呼ぶ。
    parser.add_argument("command", nargs="*")
    parser.add_argument("--cwd", default="")
    parser.add_argument("--phase", type=int, default=None)
    parser.add_argument("--body-file", default="")
    parser.add_argument("--reason", default="")
    parser.add_argument("--reviewed", type=int, default=None)
    parser.add_argument("--accept-unresolved", action="store_true")
    parser.add_argument("-h", "--help", action="store_true")
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return EXIT_ERROR
    if args.help:
        stderr.write(USAGE)
        return EXIT_ERROR

    root = args.root if args.root is not None else default_root()
    conf, problems = settings.load(root)

    _override(conf, args)
    # フラグは設定ファイルより強い。書かれた綴りのほうも、そこに合わせて差し替える。
    if args.guard_ticket_approval:
        conf.guard_ticket_approval_declared = args.guard_ticket_approval
    # この門は dry-run を取らない（selfguard.GATE_SETTINGS）。取れない語で書かれて
    # いたら、読めない値と同じ扱いで enable へ倒す。--lint はそれを error にする。
    conf.guard_ticket_approval = selfguard.resolve(
        stderr,
        args.guard_ticket_approval,
        conf.guard_ticket_approval,
        settings.GUARD_TICKET_APPROVAL_ENV,
        selfguard.GATE_SETTINGS,
    )

    # 検証だけを行う経路。payload を読まないので、判定に入る前にここで分かれる。
    # 苦情の扱いが逆になるのが分ける理由で、判定にとっては読み飛ばした設定の
    # 報告でしかないものが、検証にとっては結論そのものになる。
    if args.lint:
        return lint.report(
            stdout,
            root,
            conf,
            problems,
            args.mode,
            args.restore_if_deny,
            args.guard_core_files,
            conf.guard_ticket_approval,
        )

    # 診断の経路。どちらも payload を読まず、判定を実行にも記録にも繋げない。
    # 人が端末から叩いて「このルールは何に当たるのか」を確かめるための場所で、
    # 判定そのものは実運用と同じ関数を通る（REQ-DIA-03）。
    if args.test is not None:
        if args.json:
            return diagnose.test_json(stdout, stderr, conf, root, args.test[0], args.test[1])
        return diagnose.test(stdout, stderr, conf, root, args.test[0], args.test[1])
    if args.test_samples:
        return diagnose.test_samples(stdout, stderr, conf, root, args.test_samples, args.json)
    if args.explain and args.json:
        return diagnose.explain_json(stdout, stderr, conf, root)
    if args.explain:
        return diagnose.explain(stdout, stderr, conf, root)

    # 承認の経路。人が端末から叩くもので、payload を読まないのでここで分かれる。
    # 判定を 1 度も通らないのも分ける理由で、承認はツール呼び出しについての
    # 判断ではなく、これから効く範囲についての合意になる。
    if args.approve:
        if not conf.approved:
            stderr.write("ccnavi: 写しの置き場が空。--approved で指す先が要る\n")
            return EXIT_ERROR
        if not _from_terminal(stdin, conf, stderr, "--approve"):
            return EXIT_ERROR
        rule_set, _ = ruleload.load_rules(stderr, conf.rules, audit.Record(), root)
        approved = approval.approve(stdin, stdout, stderr, conf, rule_set, root)
        return EXIT_OK if approved == 0 else EXIT_ERROR

    # チケットの状態とレビューの操作。payload を読まない。
    if args.command or args.reviewed is not None:
        if args.reviewed is not None and not _from_terminal(stdin, conf, stderr, "--reviewed"):
            return EXIT_ERROR
        return operate(stdin, stdout, stderr, conf, root, args)

    for problem in problems:
        stderr.write(f"ccnavi: {problem}\n")

    mode = modes.resolve_mode(stderr, args.mode, conf)
    conf.restore_if_deny = selfguard.resolve(
        stderr, args.restore_if_deny, conf.restore_if_deny, settings.RESTORE_IF_DENY_ENV
    )
    conf.guard_core_files = selfguard.resolve(
        stderr,
        args.guard_core_files,
        conf.guard_core_files,
        settings.GUARD_CORE_FILES_ENV,
    )
    log = audit.Log(conf.log)
    deadline = time.monotonic() + judge.DEADLINE_SECONDS

    try:
        payload = hookio.decode(stdin)
    except hookio.NoPayload:
        # hook ではなく端末の前の人。何も判定していないので何も記録しない。
        stderr.write(USAGE)
        return EXIT_ERROR
    except hookio.Unusable as exc:
        stderr.write(f"ccnavi: {exc}\n")
        _record(
            stderr,
            log,
            audit.Record(
                mode=mode,
                decision=audit.SKIP,
                reason=audit.REASON_PAYLOAD_UNUSABLE,
            ),
        )
        return modes.fail_closed(mode)

    record = audit.Record(
        mode=mode,
        permission_mode=payload.permission_mode,
        event=payload.event,
        tool=payload.tool_name,
        subject=judge.subject_of(payload),
        session=payload.session_id,
    )

    code = events.decide(stdout, stderr, mode, conf, root, payload, record, deadline)
    _record(stderr, log, record)
    return code


def _from_terminal(stdin: TextIO, conf: settings.Settings, stderr: TextIO, flag: str) -> bool:
    """人の判断の経路が、端末の前の人から打たれているか。

    `--approve` と `--reviewed` は人の合意そのもの。エージェントが Bash から打てば
    その合意を自分で出せる。標準入力が端末であることを求めるのが、この経路が
    hook の中や `echo y |` から来ていないことの、いちばん安い証拠になる。
    CCNAVI_GUARD_TICKET_APPROVAL=disable で切れる（テストと、端末を持たない配管のため）。
    """
    if conf.guard_ticket_approval == selfguard.DISABLE:
        return True
    try:
        if stdin.isatty():
            return True
    except (AttributeError, ValueError):
        pass
    stderr.write(
        f"ccnavi: {flag} は端末から打つもの。標準入力が端末ではない"
        f"（{settings.GUARD_TICKET_APPROVAL_ENV}=disable で切れる）\n"
    )
    return False


def operate(
    stdin: TextIO,
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    args: argparse.Namespace,
) -> int:
    """チケットの状態とレビューの操作を振り分ける。

    `ticket start|done|cancel <識別子>` と `review prepare|requested|check`、それに
    人が打つ `--reviewed`。どれも payload を読まず、判定も記録もしない。
    リモートの写しは `--result <json>` で受け取る。exe はネットワークに出ない。
    """
    if not conf.approved:
        stderr.write("ccnavi: 写しの置き場が空。チケットによる制御を使っていない\n")
        return EXIT_ERROR
    cwd = args.cwd or os.getcwd()
    if args.reviewed is not None:
        code = review.reviewed(
            stdin,
            stdout,
            stderr,
            root,
            conf,
            cwd,
            args.reviewed,
            args.accept_unresolved,
            args.result,
        )
        return EXIT_OK if code == 0 else EXIT_ERROR

    words = list(args.command)
    kind = words[0] if words else ""
    verb = words[1] if len(words) > 1 else ""
    target = words[2] if len(words) > 2 else ""
    code = 1
    if kind == "ticket" and verb in ("start", "done", "cancel") and target:
        if verb == "start":
            code = ops.start(stdout, stderr, root, conf, target)
        elif verb == "done":
            code = ops.done(stdout, stderr, root, conf, target)
        else:
            code = ops.cancel(stdout, stderr, root, conf, target, args.reason)
    elif kind == "ticket" and verb == "judge":
        # ticket judge <子> <項目> yes|no --reason <根拠>
        if len(words) < 5:
            stderr.write("ccnavi: ticket judge には <子> <項目> yes|no と --reason <根拠> が要る\n")
        else:
            code = ops.judge(stdout, stderr, root, conf, words[2], words[3], words[4], args.reason)
    elif kind == "review" and verb == "prepare":
        if args.phase is None or not args.body_file:
            stderr.write("ccnavi: review prepare には --phase <N> と --body-file <path> が要る\n")
        else:
            code = review.prepare(stdout, stderr, root, conf, cwd, args.phase, args.body_file)
    elif kind == "review" and verb in ("requested", "check"):
        if args.phase is None or not args.result:
            stderr.write(f"ccnavi: review {verb} には --phase <N> と --result <json> が要る\n")
        elif verb == "requested":
            code = review.requested(stdout, stderr, root, conf, cwd, args.phase, args.result)
        else:
            code = review.check(stdout, stderr, root, conf, cwd, args.phase, args.result)
    elif kind == "review" and verb == "handoff":
        if not args.body_file or not args.result:
            stderr.write(
                "ccnavi: review handoff には --body-file <path> と --result <json> が要る\n"
            )
        else:
            code = review.handoff(stdout, stderr, root, conf, cwd, args.body_file, args.result)
    elif kind == "review" and verb == "ready":
        if not args.result:
            stderr.write("ccnavi: review ready には --result <json> が要る\n")
        else:
            code = review.ready(stdout, stderr, root, conf, cwd, args.result)
    elif kind == "review" and verb == "wrapup":
        # 人の判断。--approve / --reviewed と同じく端末を求める。
        if not args.result:
            stderr.write("ccnavi: review wrapup には --reason <理由> と --result <json> が要る\n")
        elif _from_terminal(stdin, conf, stderr, "review wrapup"):
            code = review.wrapup(stdin, stdout, stderr, root, conf, cwd, args.reason, args.result)
    else:
        stderr.write(USAGE)
    return EXIT_OK if code == 0 else EXIT_ERROR


def _record(stderr: TextIO, log: audit.Log, record: audit.Record) -> None:
    """1 行を書く。書けなかったことは報告して捨てる。
    それがツールの実行可否を変えられてはいけない。"""
    try:
        log.write(record)
    except OSError as exc:
        stderr.write(f"ccnavi: 記録を書けない: {exc}\n")


def default_root() -> str:
    """ワークスペースルート、つまり .claude を持つディレクトリを見つける。

    ここでは作業ディレクトリそのものに頼ってはいけない。hook は自分が走る
    ディレクトリを選べないから。代わりに上へ辿るので、ワークスペースの中の
    どこから起動しても同じワークスペースルートに行き着くし、思わぬ場所で起動された
    hook でもワークスペースの設定を読める。
    """
    # Claude Code はこれを渡してくるが、あることに依存してはいけない。
    from_env = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if from_env:
        return from_env
    found = _find_project_root()
    return found if found else "."


def _find_project_root() -> str:
    """作業ディレクトリからファイルシステムのルートまで上って .claude を探す。
    バージョン管理が自分のルートを見つけるのと同じやり方。"""
    try:
        directory = os.getcwd()
    except OSError:
        return ""
    while True:
        if os.path.isdir(os.path.join(directory, ".claude")):
            return directory
        parent = os.path.dirname(directory)
        if parent == directory:
            return ""
        directory = parent
