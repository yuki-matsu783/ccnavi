"""標準入出力とコマンドラインを 1 つの判定に繋ぐ。

run は終了コードを返す。自分で終了しないので、道具ぜんぶを別プロセス無しで
テストから動かせる。
"""

from __future__ import annotations

import argparse
import contextlib
import os
import time
from collections.abc import Callable
from typing import TextIO

from . import (
    approval,
    audit,
    builtin,
    diagnose,
    hookio,
    lint,
    ops,
    phase,
    post,
    review,
    rules,
    selfguard,
    settings,
    shellread,
    tree,
)
from . import ticket as ticket_mod

# 1 回の起動に張る期限。呼び手は長く走った hook を打ち切って出力を捨てるので、
# それより先に自前の判定へ着地することが、遅い判定が黙った許可に化けるのを防ぐ。
DEADLINE_SECONDS = 3.0

# 終了コード。
EXIT_OK = 0  # 判定を書いた、あるいは言うことが無かった
EXIT_ERROR = 1  # 使い方の誤り、または読めない設定
EXIT_BLOCK = 2  # 判定を書けなかったので拒否側に倒す

# モードは判定をどう扱うかを決めるだけで、どう判定するかは決めない。
# 判定を続ける 2 つのモードは同じ経路を通るので、dry-run が報告するものが
# enable なら実際に起きたことと一致する。
#
# 名前はガードそのものの状態を言う。判定に deny と ask の 2 つがある以上、
# 名前が「止める」だけを言うと、確認で済む回に嘘をつくことになる。
# 弱いほうから強いほうへ並ぶのは lint の深刻度と同じ。
MODE_DISABLE = "disable"  # 判定しない
MODE_DRY_RUN = "dry-run"  # 判定して報告するが、呼び出しには手を出さない
MODE_ENABLE = "enable"  # 判定を呼び出しに適用する

# Claude Code 側の権限モード。ルールがどこも言及しなかった呼び出しの結末が、
# ここで変わる。ccnavi 自身のモード（上の 3 つ）とは別のもので、あちらは
# 「下した判定を適用するか」、こちらは「そもそも誰が判断するか」を決める。
#
# ルールが言及していない呼び出しについて、ccnavi は判定を持たない。持っていない
# 判定を ask として返すと、判断できるモードでも必ず人に止まり、auto モードが
# 実質効かなくなる。だから返さず、Claude Code の権限モードに従う。
# ルールに書いていないものは、ルールに書いていないものとして渡す。
PERMISSION_JUDGED = ("auto",)
# 人にも classifier にも確認できないモード。ここで ask を返すと「誰も答えないまま
# 通る」に化けるので、許可としない（REQ-PRE-08）。
PERMISSION_NO_JUDGE = ("dontAsk", "bypassPermissions")

# 判定を権限モードへ渡したことを表す内部の値。区画名（rules.ALLOW など）と
# 同じ変数に入るので、ルールファイルには現れない綴りにしてある。
HANDOVER = "(handover)"

# 返す理由に載せる理由コード。ccnavi.md 付録 B の体系から、今のビルドが実際に
# 下せる判定に対応するものだけを借りている。
#
# 付録 B のコードは「どの検査がその根拠を作ったか」の名前であって、ルール 1 件を
# 指す名前ではない。今のビルドが持つ検査は 1 つ（外から注入したルール集合を
# 正規化済みの対象に当てる）で、当てる先がコマンドかパスかで 2 つに割れる。
# だからコードはその割れ方に対応させ、どのルールだったかは出所が名指しする。
# ルール 1 件ごとにコードを持たせれば付録 B の粒度（ヒアドキュメントなら
# DENY_REDIRECT、DB 破壊なら DENY_DB_DESTRUCTIVE）に届くが、それはルール
# ファイルに欄を 1 つ足すことなので書式の版が上がる。この要件が求めるのは
# 「理由コードを含むこと」までなので、版を上げずに済む側を採る。
CODE_COMMAND = "DENY_COMMAND_PATTERN"  # Bash の実行される部分に当たった
CODE_PATH = "DENY_PATH"  # ファイルのパスに当たった
# ルールが ask と書いてある場所に当たった。「ここは毎回人間が見るべき」という
# 意図的な確認ポイントで、繰り返し出ること自体に値打ちがある。
CODE_RULE_ASK = "RULE_ASK"
# ルールがどこも言及していない。危険の表明ではない。ccnavi はこの呼び出しに
# ついて何も言えず、扱いを Claude Code の権限モードに委ねたことを言うだけ。
# 委ねた先が判断できないモードなら許可としないので、同じコードが渡した回と
# 断った回の両方に付く。どちらだったかは記録の decision 側が持つ。
CODE_UNDECLARED = "UNDECLARED"
# 実行後の監視が出すコードは post.py にある。あちらは判定ではなく、
# すでに起きたことの報告なので、同じ表に混ぜていない。
# 読み切れないコマンドの根拠は、宣言された禁止に当たったことではなく、
# 対象を確定できなかったこと。こちらは権限モードに委ねない。読めなかった
# という事実は判定の結果に現れず、委ねた先には伝わらないので、言えるのが
# ここしかない。生の文字列が deny に当たったときだけは拒否になり、
# そのときも「読めないまま当てた」ことを文面が断る。
CODE_UNCERTAIN = "PARSE_UNCERTAIN"
# 承認されたチケットの作業範囲の外に書こうとした。ルールに当たったのではなく、
# 宣言された範囲に入っていないことが根拠なので、コードを分けている。
# 受け取った側の次の一手が違う。ルールなら別の手段を探すことになるが、
# こちらは範囲の中で済ませるか、チケットを書き直して承認を求めることになる。
CODE_TICKET_SCOPE = "DENY_TICKET_SCOPE"
# チケットが `ask` と書いた場所。ルールの `ask` と同じく、人が 1 度見る場所。
CODE_TICKET_ASK = "TICKET_ASK"

# 範囲外で止めたことを記録に残すときのルール名。対応するルールがルールファイルに
# 無いので、括弧付きにして、ファイルの中を探しても見つからないことを見た目で示す。
TICKET_RULE = "(ticket-scope)"

# 範囲の中かどうかを問うツール。作業ツリーを書き換えるものだけ。
# Bash は入れていない。コマンド文字列に現れるパスは追えないので、ここで
# 当てると当たったり当たらなかったりする判定になる。シェル経由の書き込みは
# 実行後の監視が作業ツリーの実物を見て捕まえる。
SCOPE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")

# サブエージェントの起動ツール。ゲートが止める対象で、対象の文字列を持たないので
# 見出しだけを subject にする。
AGENT_TOOL = "Agent"

# 理由に載せる対象の長さの上限。対象はエージェントが今書いたものなので、
# ここでは同じものを指せれば足りる。ヒアドキュメントは 1 ファイル分を運べるので、
# 全文を載せると理由の本体が下へ流れて読まれなくなる。
SUBJECT_LIMIT = 200

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
    parser.add_argument("--guard-cli", default="")
    # リモートの写し（JSON）。.claude/scripts/ccnavi-review.sh が取ってきて渡す。
    parser.add_argument("--result", default="")
    parser.add_argument("--lint", action="store_true")
    parser.add_argument("--approve", action="store_true")
    parser.add_argument("--test", nargs=2, metavar=("TOOL", "SUBJECT"), default=None)
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

    # フラグは何よりも強い。診断のための実行が、プロジェクト全体で共有している
    # ファイルに触らずに別の場所を指せるように。
    # 空文字も受ける。「記録しない」「控えを持たない」を言えないと、診断の
    # ための 1 回が、走っているセッションの記録と控えに必ず混ざる。
    if args.log is not None:
        conf.log = args.log
    if args.rules:
        conf.rules = args.rules
    if args.state is not None:
        conf.state = args.state
    if args.tickets:
        conf.tickets = args.tickets.replace("\\", "/").strip("/")
    if args.approved is not None:
        conf.approved = args.approved
    if args.phases is not None:
        conf.phases = args.phases
    if args.risk is not None:
        conf.risk = args.risk
    if args.projects is not None:
        conf.projects = args.projects
    if args.project_rules:
        conf.project_rules = args.project_rules.replace("\\", "/").strip("/")
    conf.guard_cli = selfguard.resolve(
        stderr, args.guard_cli, conf.guard_cli, settings.GUARD_CLI_ENV
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
            conf.guard_cli,
        )

    # 診断の経路。どちらも payload を読まず、判定を実行にも記録にも繋げない。
    # 人が端末から叩いて「このルールは何に当たるのか」を確かめるための場所で、
    # 判定そのものは実運用と同じ関数を通る（REQ-DIA-03）。
    if args.test is not None:
        return diagnose.test(stdout, stderr, conf, root, args.test[0], args.test[1])
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
        rule_set, _ = load_rules(stderr, conf.rules, audit.Record(), root)
        approved = approval.approve(stdin, stdout, stderr, conf, rule_set, root)
        return EXIT_OK if approved == 0 else EXIT_ERROR

    # チケットの状態とレビューの操作。payload を読まない。
    if args.command or args.reviewed is not None:
        if args.reviewed is not None and not _from_terminal(stdin, conf, stderr, "--reviewed"):
            return EXIT_ERROR
        return operate(stdin, stdout, stderr, conf, root, args)

    for problem in problems:
        stderr.write(f"ccnavi: {problem}\n")

    mode = resolve_mode(stderr, args.mode, conf)
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
    deadline = time.monotonic() + DEADLINE_SECONDS

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
        return fail_closed(mode)

    record = audit.Record(
        mode=mode,
        permission_mode=payload.permission_mode,
        event=payload.event,
        tool=payload.tool_name,
        subject=subject_of(payload),
        session=payload.session_id,
    )

    code = decide(stdout, stderr, mode, conf, root, payload, record, deadline)
    _record(stderr, log, record)
    return code


def decide(
    stdout: TextIO,
    stderr: TextIO,
    mode: str,
    conf: settings.Settings,
    root: str,
    payload: hookio.Input,
    record: audit.Record,
    deadline: float,
) -> int:
    """イベントごとの処理に振り分ける。

    振り分けだけをここに置く。イベントが増えるたびに 1 本の関数が伸びると、
    どのイベントで何が起きるのかを読むのに全部を読むことになる。
    """
    if mode == MODE_DISABLE:
        record.decision, record.reason = audit.SKIP, audit.REASON_MODE_DISABLED
        return EXIT_OK
    if payload.event == hookio.PRE_TOOL_USE:
        return decide_before(stdout, stderr, mode, conf, root, payload, record, deadline)
    if payload.event == hookio.POST_TOOL_USE:
        return decide_after(stdout, stderr, mode, conf, root, payload, record)
    if payload.event == hookio.SESSION_START:
        return decide_at_start(stdout, mode, conf, root, payload, record)
    if payload.event == hookio.USER_PROMPT_SUBMIT:
        return decide_at_prompt(stderr, conf, root, payload, record)
    if payload.event == hookio.STOP:
        return decide_at_stop(stdout, stderr, conf, root, payload, record)
    if payload.event == hookio.SUBAGENT_START:
        return decide_at_subagent_start(stdout, conf, root, payload, record)
    if payload.event == hookio.SUBAGENT_STOP:
        return decide_at_subagent_stop(stdout, stderr, mode, conf, root, payload, record)
    # 判定を持たないイベントは誤りではない。想定していない登録が
    # 作業を止めてはいけない。
    record.decision, record.reason = audit.SKIP, audit.REASON_EVENT_NOT_CHECKED
    return EXIT_OK


def _from_terminal(stdin: TextIO, conf: settings.Settings, stderr: TextIO, flag: str) -> bool:
    """人の判断の経路が、端末の前の人から打たれているか。

    `--approve` と `--reviewed` は人の合意そのもの。エージェントが Bash から打てば
    その合意を自分で出せる。標準入力が端末であることを求めるのが、この経路が
    hook の中や `echo y |` から来ていないことの、いちばん安い証拠になる。
    CCNAVI_GUARD_CLI=disable で切れる（テストと、端末を持たない配管のため）。
    """
    if conf.guard_cli == selfguard.DISABLE:
        return True
    try:
        if stdin.isatty():
            return True
    except (AttributeError, ValueError):
        pass
    stderr.write(
        f"ccnavi: {flag} は端末から打つもの。標準入力が端末ではない"
        f"（{settings.GUARD_CLI_ENV}=disable で切れる）\n"
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


def watch_context(
    stderr: TextIO, conf: settings.Settings, root: str, record: audit.Record
) -> tuple[list[post.Watched], post.ScopeGuard | None]:
    """ターンの区切りで作業ツリーを見る 2 つが、共通して使う持ち物。

    保護領域も範囲も、実行前の判定と同じ経路で解く。別に書くと、実行前に
    通った書き込みがターンの終わりに咎められる（あるいはその逆）ことになり、
    どちらが本当の宣言なのかを誰も言えなくなる。
    """
    return watched_for(stderr, conf, root, record), scope_guard(conf, root)


def watched_for(
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    record: audit.Record,
    payload: hookio.Input | None = None,
) -> list[post.Watched]:
    """実行後に見るツリーと、それぞれに当てるルール（設計 §25.7）。

    payload が無ければ全部のツリー（ターンの区切り）。あればワークスペースルートと、
    この呼び出しが触ったツリー（パスを持つツールは行き先、Bash は cwd）。
    ルールの引き方は実行前の判定と同じ。プロジェクトとその作業ツリーには
    そのプロジェクトのルール、ワークスペースのツリーにはワークスペースのルール。
    """
    ws = tree.main_tree(root)
    if payload is None:
        trees = tree.all_trees(root, conf.projects)
    else:
        trees = [ws]
        where = record.subject if payload.tool_name in PATH_TOOLS else payload.cwd
        t = tree.tree_of(root, where, conf.projects) if where else None
        if t is not None and t.root != ws.root:
            trees.append(t)
    loaded: dict[str, tuple[rules.RuleSet, str]] = {}
    out = []
    for t in trees:
        if t.project not in loaded:
            if t.project:
                home = tree.project_root(conf.projects, t.project)
                rule_set, source = load_rules(
                    stderr, settings.project_rules_path(conf, home), record, root
                )
                if source != builtin.SOURCE:
                    prefix_ids(rule_set, t.project)
            else:
                rule_set, source = load_rules(stderr, conf.rules, record, root)
            loaded[t.project] = (rule_set, source)
        rule_set, source = loaded[t.project]
        out.append(post.Watched(t, rule_set, source))
    return out


def scope_guard(conf: settings.Settings, root: str) -> post.ScopeGuard | None:
    """承認済みの写しを、実行後の側から当てる持ち物。写しを使っていなければ None。"""
    if not conf.approved:
        return None
    copies, _ = approval.copies(conf.approved)
    return post.ScopeGuard(
        root=root, approved=conf.approved, copies=approval.by_id(copies), projects=conf.projects
    )


def decide_at_prompt(
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    payload: hookio.Input,
    record: audit.Record,
) -> int:
    """利用者が何か言ったとき。ターンの基準をここで取る。

    何も返さない。このイベントで返した文はモデルのコンテキストに入るので、
    まだ何も起きていない時点で 1 段積むことになる。ここでやるのは、
    ターンの終わりに「このターンで何が変わったか」を言えるようにする控えだけ。
    """
    watched, scope = watch_context(stderr, conf, root, record)
    post.at_prompt(stderr, conf.state, (conf.state, conf.log), watched, scope, payload, record)
    return EXIT_OK


def decide_at_stop(
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    payload: hookio.Input,
    record: audit.Record,
) -> int:
    """ターンが終わったとき。宣言した保護領域の今の状態を人へ報告する。

    宛先が人なので `systemMessage` で返す。呼び出しごとの報告はモデルへ届く
    経路に載せてあり、そこは既に足りている。足りていないのは、ターンが終わった
    あとに人が「結局どこが変わったのか」を 1 度で見る場所のほう。

    exit 2 は使わない。このイベントでの exit 2 はターンを続けさせる意味になり、
    報告のために作業を終わらせない形になる。何も止めずに、言うだけにする。

    モードを見ない。dry-run でも disable でも報告する。ここは呼び出しにも
    作業ツリーにも手を出さず、見えたことを言うだけなので、モードが約束している
    ものを何ひとつ破らない。むしろ dry-run は「まだ何も適用していないが何が
    起きているか」を見るためのモードなので、ここが黙ると見る手立てが減る。
    """
    watched, scope = watch_context(stderr, conf, root, record)
    text = post.at_stop(stderr, conf.state, (conf.state, conf.log), watched, scope, payload, record)
    if text:
        hookio.write_system_message(stdout, text)
    return EXIT_OK


def decide_at_start(
    stdout: TextIO,
    mode: str,
    conf: settings.Settings,
    root: str,
    payload: hookio.Input,
    record: audit.Record,
) -> int:
    """セッションが始まったとき。大きい対象の控えをここで 1 度だけ取る。

    ここで取るのは実行ファイルで、ツール呼び出しのたびに写すには大きすぎる。
    このイベントは 1 セッションに 1 回しか来ないので、重い仕事を置く先になる。

    判定は返さない。何も起きていない時点なので、言うことがあるとすれば
    控えを取れなかったことだけになる。
    """
    setting = effective_setting(mode, conf.guard_core_files)
    outcomes = selfguard.at_start(
        setting,
        conf.state,
        payload.session_id,
        root,
        selfguard.targets(root, conf.rules, conf.bin, project_rules_files(conf)),
    )
    record.decision, record.enforced = audit.ALLOW, True
    if not outcomes:
        return EXIT_OK
    record.guarded = [f"{o.target.key}:{o.action}" for o in outcomes]
    hookio.write_context(stdout, hookio.SESSION_START, selfguard.report(outcomes))
    return EXIT_OK


def effective_setting(mode: str, declared: str) -> str:
    """CCNAVI_MODE を掛けたあとの、実際に効く設定。

    判定を適用しないモードでは、戻す側もファイルに触らない。dry-run は
    「呼び出しにも作業ツリーにも手を出さない」ことがモードの約束で、
    守る側だけがその約束の外に出ると、試している最中に誰も頼んでいない
    ファイル操作が起きる。試すことが怖くなれば、誰も試さなくなる。
    """
    if declared == selfguard.DISABLE:
        return selfguard.DISABLE
    return declared if mode == MODE_ENABLE else selfguard.DRY_RUN


def guard_setting_files(
    stderr: TextIO,
    mode: str,
    conf: settings.Settings,
    root: str,
    payload: hookio.Input,
    record: audit.Record,
    step: Callable[..., list[selfguard.Outcome]],
) -> str:
    """ccnavi 自身の設定ファイルを控えと突き合わせる 1 回。返す文を作る。

    実行前と実行後で違うのは呼ぶ関数だけなので、そこを引数で受け取る。
    対象の決め方も設定の解き方も 1 か所にまとまり、片方だけが別の対象を
    見ている、という食い違いが起きない。
    """
    setting = effective_setting(mode, conf.guard_core_files)
    outcomes = step(
        stderr,
        setting,
        conf.state,
        payload.session_id,
        root,
        selfguard.targets(root, conf.rules, conf.bin, project_rules_files(conf)),
    )
    if not outcomes:
        return ""
    record.guarded = [f"{o.target.key}:{o.action}" for o in outcomes]
    return selfguard.report(outcomes)


def decide_before(
    stdout: TextIO,
    stderr: TextIO,
    mode: str,
    conf: settings.Settings,
    root: str,
    payload: hookio.Input,
    record: audit.Record,
    deadline: float,
) -> int:
    """実行前の判定に達し、何が起きたかを record に書き込む。
    記録と応答が必ず同じ結論から作られるようにするため。"""
    # 控えを取るのは判定より先。ここで取る断面が、この呼び出しが何かを壊した
    # ときに戻る先になる。判定で弾かれた呼び出しでも先に取っておくのは、
    # 弾けなかったものだけが作業ツリーに届くので、届いた側から見れば
    # 「直前」は判定の前だから。
    guard = guard_setting_files(stderr, mode, conf, root, payload, record, selfguard.before)

    if not record.subject:
        record.decision, record.reason = audit.SKIP, audit.REASON_NO_SUBJECT
        if guard:
            hookio.write_context(stdout, hookio.PRE_TOOL_USE, guard)
        return EXIT_OK

    rule_set, source, target = rules_for(stderr, conf, root, payload, record)
    if target is None and payload.cwd:
        # Bash には行き先が無い。記録には cwd のツリーを添える。判定には使わない。
        target = tree.tree_of(root, payload.cwd, conf.projects)
    if target is not None:
        record.tree, record.project = target.name, target.project
    # 設定ファイルを守る側が有効なら、そこへシェルから書き込む形を止める
    # ルールを judgment に足す。戻せるだけでは足りないので、同じ場所を
    # 実行前にも止める。既定に落ちているときは足さない。組み込みの既定が
    # 同じ形を既に持っていて、二重に当たると同じ話が 2 度返る。
    if not record.fallback and effective_setting(mode, conf.guard_core_files) != selfguard.DISABLE:
        selfguard.add_rules(
            rule_set, conf.bin, selfguard.project_rules_clause(conf.projects, conf.project_rules)
        )
    # チケットの状態の置き場を守る。動かすのはスクリプトだけで、直接の作成・移動は
    # 誰がやっても止める。写しを使っているときだけ足す。
    if conf.approved:
        rule_set.deny.extend(ticket_mod.guard_rules(conf.tickets))
        # 人の判断の経路（承認・レビュー済みの受け入れ・状態とレビューの操作）を、
        # 実行ファイルを直接打つ形で通さない。スクリプト 2 本の中身がこれ。
        if conf.guard_cli != selfguard.DISABLE:
            rule_set.deny.append(phase.cli_guard_rule(conf.bin))

    subject = screen(payload.tool_name, record.subject, record)

    if not subject:
        # コマンドは在るが、実行される部分が残らなかった。コメントだけの行が
        # これにあたる。ルールを当てる先が無いので、どの区画にも当たらず
        # 権限モードへ渡る先になるが、何も走らないものについて誰かの判断を
        # 求める意味は無い。
        record.decision, record.reason = audit.SKIP, audit.REASON_NOTHING_TO_RUN
        if guard:
            hookio.write_context(stdout, hookio.PRE_TOOL_USE, guard)
        return EXIT_OK

    fallback = fallen_back(record.detail or conf.rules) if record.fallback else ""
    notices = [text for text in (guard, fallback) if text]

    # サブエージェントには、状態を動かすスクリプトもレビューのスクリプトも打たせない。
    # 閉じるのは親だけ（REQ-TKT-10）。ルールより先に見る。ルールが allow と
    # 書いていても、この 2 本はサブエージェントの手には渡さない。
    if (
        conf.approved
        and payload.agent_id
        and payload.tool_name in phase.SHELL_TOOLS
        and phase.forbidden(subject)
    ):
        record.code, record.rules = phase.CODE_SUBAGENT, [TICKET_RULE]
        return refuse(stdout, mode, record, rules.DENY, notices + [subagent_forbidden(subject)])

    # ゲート。人間レビュー要のフェーズが終わっていて印が無い間、サブエージェントの
    # 起動と、例外の 3 本以外のシェル実行を止める（REQ-TKT-15）。ルールより先に見る。
    if conf.approved and payload.tool_name in phase.GATED_TOOLS:
        parent = phase.parent_for_cwd(root, conf, payload.cwd)
        closed = phase.gate(root, conf, parent.ticket) if parent is not None else None
        exempt = payload.tool_name == "Bash" and phase.exempt(subject, record.degraded)
        if closed is not None and not exempt:
            record.code, record.rules = phase.CODE_GATE, [TICKET_RULE]
            reason = phase.gate_reason(closed, payload.tool_name)
            return refuse(stdout, mode, record, rules.DENY, notices + [reason])

    # 作業ツリーの切り元と写しの `project:` の食い違いは、ルールより先に見る。
    # 範囲の宣言ではなく配線の誤りなので、ルールが allow と言っていても通さない。
    if conf.approved and target is not None and payload.tool_name in SCOPE_TOOLS:
        mismatch = project_mismatch(conf, root, target, record.subject)
        if mismatch:
            record.code, record.rules = CODE_TICKET_PROJECT, [TICKET_RULE]
            return refuse(stdout, mode, record, rules.DENY, notices + [mismatch])

    # 強い区画から順に見て、最初に当たったところで止める。deny に当たった
    # 呼び出しについて ask の区画を調べる意味は無いし、調べれば「拒否だが
    # 確認もしろ」という読めない結論に届く道ができる。
    verdict, group = "", []
    for name in rules.SECTIONS:
        if name == rules.ALLOW and record.degraded:
            # 読み切れなかったコマンドに allow は当てない。当てる先は
            # 実行される部分ではなく生の文字列なので、そこで許可を出すのは
            # 「読めなかった文字列にそう書いてあった」を根拠に通すことになる。
            # deny と ask は当てたままにする。生の文字列に当たりすぎるぶんは
            # 厳しい側へ外れるだけで、そのことは文面が断る。
            break
        for rule in rule_set.section(name):
            if time.monotonic() > deadline:
                stderr.write("ccnavi: 判定を終える前に期限に達した\n")
                record.decision, record.reason = audit.SKIP, audit.REASON_DEADLINE_EXCEEDED
                return fail_closed(mode)
            if rule.matches(payload.tool_name, subject):
                group.append(rule)
        if group:
            verdict = name
            break

    record.rules = [rule.id or f"({verdict})" for rule in group]

    # チケットはルールが何も言わなかったときだけ見る。ルールのほうが強い。
    # 順番を逆にすると、ルールが許した場所をチケットが閉じられることになり、
    # 人が書いた宣言よりエージェントが書いた宣言のほうが強くなる。
    ticket_reason = ""
    if not verdict:
        verdict, ticket_reason = ticket_verdict(conf, root, payload.tool_name, record.subject)
        if ticket_reason:
            record.rules = [TICKET_RULE]

    if verdict == rules.ALLOW:
        record.decision, record.enforced = audit.ALLOW, True
        if notices:
            # 通した回にも言う。ガードが今なにを見ていないのかを黙っていると、
            # 誰も知らないまま作業が進む。呼び出しごとに出るのでうるさいが、
            # うるさいのが正しい。壊れた設定と未承認のチケットはどちらも
            # 短命であるべきで、黙って居座られるより気づかれたほうがよい。
            hookio.write_context(stdout, hookio.PRE_TOOL_USE, "\n\n".join(notices))
        return EXIT_OK

    if verdict == rules.DENY and group:
        # 当たった理由はまとめて 1 回で返す。1 つずつ返すと、エージェントも
        # 1 つずつ直すことになり、そのたびに往復が 1 回増える。
        reasons = [
            reason_for(rule, payload.tool_name, subject, source, record.degraded) for rule in group
        ]
        record.code = code_for(payload.tool_name, record.degraded)
        if any(rule.id == phase.CLI_RULE_ID for rule in group):
            record.code = phase.CODE_CLI
    elif verdict == rules.DENY:
        # チケットの範囲の外。ルールが 1 件も当たっていないので group は空。
        reasons = [ticket_reason]
        record.code = CODE_TICKET_SCOPE
    elif verdict == rules.ASK and group:
        reasons = [
            reason_for(rule, payload.tool_name, subject, source, record.degraded) for rule in group
        ]
        record.code = CODE_RULE_ASK
    elif verdict == rules.ASK:
        # チケットが ask と書いた場所。人が 1 度見る場所として宣言されている。
        reasons = [ticket_reason]
        record.code = CODE_TICKET_ASK
    else:
        # どの区画も言及しなかった。ccnavi はこの呼び出しの判定を持たない。
        # 結末は Claude Code の権限モードが決める。
        record.code = CODE_UNCERTAIN if record.degraded else CODE_UNDECLARED
        verdict = undeclared_verdict(payload.permission_mode, record.degraded)
        reasons = [
            undeclared(
                payload.tool_name, subject, conf.rules, record.degraded, verdict == rules.DENY
            )
        ]

    if verdict == HANDOVER:
        # 判定は渡した。ここで残せるのは記録だけで、それがこの分担の要点になる。
        # ルールが言及していない場所は log の handover を数えれば分かり、その数は
        # 人に聞いた回とも、classifier が通した回とも混ざらない。
        #
        # 文面は返さない。呼び出しごとに「ルールが言及していない」と言うと、
        # 渡した先が判断するだけの回に毎度コンテキストを 1 段積むことになる。
        # 穴の在処は記録から読む。
        record.decision, record.enforced = audit.HANDOVER, False
        if notices:
            hookio.write_context(stdout, hookio.PRE_TOOL_USE, "\n\n".join(notices))
        return EXIT_OK

    return refuse(stdout, mode, record, verdict, notices + reasons)


def refuse(stdout: TextIO, mode: str, record: audit.Record, verdict: str, parts: list[str]) -> int:
    """拒否か確認を返す。dry-run なら返す代わりに、返していたはずだと言う。"""
    # 空行で割るのは、1 件ずつが閉じた文であることを見た目でも保つため。
    reason = "\n\n".join(parts)
    decision = audit.DENY if verdict == rules.DENY else audit.ASK

    if mode == MODE_DRY_RUN:
        record.decision, record.enforced = decision, False
        would = "stopped" if verdict == rules.DENY else "asked the user about"
        hookio.write_context(
            stdout,
            hookio.PRE_TOOL_USE,
            f"[ccnavi dry-run] {MODE_ENABLE} would have {would} this call:\n" + reason,
        )
        return EXIT_OK

    record.decision, record.enforced = decision, True
    hookio.write_verdict(stdout, hookio.DENY if verdict == rules.DENY else hookio.ASK, reason)
    return EXIT_OK


def subagent_forbidden(subject: str) -> str:
    shown = " ".join(subject.split())[:SUBJECT_LIMIT]
    return "\n".join(
        [
            f"[ccnavi] {phase.CODE_SUBAGENT}",
            f"subject: {shown}",
            "チケットの状態を動かす操作、レビューの依頼・確認、リモートへの push は、"
            "親（メインエージェント）だけが行います。サブエージェントは自分のチケットの"
            "範囲で作業を終えたら、コミットまでして結果を報告して終わってください。"
            "合流と push と閉じるのは親の仕事です。",
        ]
    )


def decide_at_subagent_start(
    stdout: TextIO,
    conf: settings.Settings,
    root: str,
    payload: hookio.Input,
    record: audit.Record,
) -> int:
    """サブエージェントが始まったとき。cwd の作業ツリーに関わる、開いている子の一覧を渡す。

    止められないイベントなので判定はしない。判定はファイルの行き先で決まるので、
    ここで渡す文は案内でしかない。親のプロンプトに書き忘れがあっても、
    サブエージェントが自分のツリーと範囲を知れるようにする。

    渡すのは cwd で決める。親の作業ツリーならその親の開いている子、子の作業ツリーなら
    その子自身。main と、チケットの無い作業ツリーからの起動には何も渡さない。
    全部の子を渡していた版は、別のセッションが main で調査を委譲したときにも無関係な
    子の範囲を案内し、調査役が自分の居場所を迷う形になった（SubagentStop と同じ絞り方）。
    """
    record.decision, record.enforced = audit.ALLOW, True
    if not conf.approved:
        return EXIT_OK
    copies, _ = approval.copies(conf.approved)
    index = approval.by_id(copies)
    t = tree.tree_of(root, payload.cwd or os.getcwd(), conf.projects)
    bound = tree.lookup(index, t.name) if t is not None and not t.is_main else None
    if bound is None:
        return EXIT_OK
    children = [bound] if bound.is_child else [c for c in copies if c.parent == bound.ticket]
    if not children:
        return EXIT_OK
    closed, _ = approval.copies(conf.approved, closed=True)
    done = {t.ticket for t in closed}
    lines = [
        "[ccnavi] 承認済みで開いている子チケット。"
        "書き込みは行き先の作業ツリーのチケットで判定される。"
    ]
    parents = approval.by_id(copies)
    types = phase.load_types(conf) or {}
    for t in sorted(children, key=lambda x: x.ticket):
        where = tree.worktree_path(root, t.ticket)
        state = "作業ツリーあり" if os.path.isdir(where) else "作業ツリー無し（効かない）"
        waiting = [p for p in t.predecessors if p not in done]
        label = str(t.phase)
        hint = ""
        parent = parents.get(t.parent)
        item = parent.item_at(t.phase) if parent is not None and t.phase is not None else None
        pt = types.get(item.type) if item is not None else None
        if pt is not None:
            label = f"{t.phase}: {pt.title}"
            if pt.agent:
                hint = f" 種類の案内: エージェント {pt.agent}"
        lines.append(
            f"  {t.ticket}（親 {t.parent}、フェーズ {label}）: {where} [{state}]"
            + (f" 先行が未完了: {', '.join(waiting)}" if waiting else "")
            + hint
        )
        for name in rules.SECTIONS:
            paths = t.paths(name)
            if paths:
                lines.append(f"    {name}: " + ", ".join(paths))
    hookio.write_context(stdout, hookio.SUBAGENT_START, "\n".join(lines))
    return EXIT_OK


def decide_at_subagent_stop(
    stdout: TextIO,
    stderr: TextIO,
    mode: str,
    conf: settings.Settings,
    root: str,
    payload: hookio.Input,
    record: audit.Record,
) -> int:
    """サブエージェントが終わろうとしたとき。範囲外の変更が残っていれば 1 回だけ差し戻す。

    見るのは、cwd が子の作業ツリーならその子、親の作業ツリーならその親の開いている
    子の全部。`base_sha..HEAD` のコミット済みの差分と未コミットの両方を見る。
    """
    record.decision, record.enforced = audit.ALLOW, True
    if not conf.approved:
        return EXIT_OK
    copies, _ = approval.copies(conf.approved)
    index = approval.by_id(copies)
    t = tree.tree_of(root, payload.cwd or os.getcwd(), conf.projects)
    targets: list[ticket_mod.Ticket] = []
    bound = tree.lookup(index, t.name) if t is not None and not t.is_main else None
    if bound is not None:
        targets = [bound] if bound.is_child else [c for c in copies if c.parent == bound.ticket]
    findings = []
    for child in targets:
        outside, unreadable = phase.scope_findings(root, conf, child, index.get(child.parent))
        if unreadable:
            stderr.write(f"ccnavi: {child.ticket} の作業ツリーを読めない: {unreadable}\n")
            continue
        for rel in outside:
            findings.append((child, rel))
    if not findings:
        return EXIT_OK

    record.decision, record.paths = audit.DENY, [f"{c.ticket}:{rel}" for c, rel in findings]
    record.rules, record.code = [TICKET_RULE], post.CODE_TICKET_SCOPE
    lines = [
        f"[ccnavi] {post.CODE_TICKET_SCOPE}: 子チケットの範囲の外に変更が残っています（"
        f"{len(findings)} 件）。範囲の中へ戻すか、要るなら親に伝えて次のチケットにしてください。"
    ]
    for child, rel in findings[: post.REPORT_LIMIT]:
        area = ", ".join(child.paths(rules.ALLOW) + child.paths(rules.ASK)) or "(空)"
        lines.append(f"  {child.ticket}: {rel}  範囲は {area}")
    text = "\n".join(lines)

    already = _bounced(conf.state, payload.agent_id)
    if mode == MODE_ENABLE and not already:
        _remember_bounce(stderr, conf.state, payload.agent_id)
        record.enforced = True
        stderr.write(text + "\n")
        return EXIT_BLOCK
    record.enforced = False
    hookio.write_context(stdout, hookio.SUBAGENT_STOP, text)
    return EXIT_OK


def _ignored_bounce(state_dir: str, payload: hookio.Input) -> str:
    """終わったサブエージェントが差し戻しを受けていたなら、その旨。印は消す。"""
    if payload.tool_name != AGENT_TOOL:
        return ""
    agent_id = str(payload.tool_response.get("agentId") or "")
    if not agent_id or not _bounced(state_dir, agent_id):
        return ""
    with contextlib.suppress(OSError):
        os.remove(_bounce_path(state_dir, agent_id))
    return (
        f"[ccnavi] {post.CODE_TICKET_SCOPE}: サブエージェント {agent_id} は範囲外の変更を"
        "差し戻されたまま終わっています。合流する前に、その子の作業ツリーの範囲外の"
        "変更を確かめてください。"
    )


def _bounce_path(state_dir: str, agent_id: str) -> str:
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in agent_id)[:64] or "unknown"
    return os.path.join(state_dir, f"subagent-{safe}.bounced")


def _bounced(state_dir: str, agent_id: str) -> bool:
    return bool(state_dir) and os.path.exists(_bounce_path(state_dir, agent_id))


def _remember_bounce(stderr: TextIO, state_dir: str, agent_id: str) -> None:
    if not state_dir:
        return
    try:
        os.makedirs(state_dir, exist_ok=True)
        with open(_bounce_path(state_dir, agent_id), "w", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    except OSError as exc:
        stderr.write(f"ccnavi: 差し戻しの回数を書けない: {exc}\n")


def decide_after(
    stdout: TextIO,
    stderr: TextIO,
    mode: str,
    conf: settings.Settings,
    root: str,
    payload: hookio.Input,
    record: audit.Record,
) -> int:
    """実行後の監視を 1 回動かし、言うことがあれば返す。

    期限を渡していない。実行前の期限は、遅い判定が黙った許可に化けるのを
    防ぐためのもので、止められるイベントでしか意味を持たない。ここは
    何も止めていないので、遅れは待ち時間にしかならない。作業ツリーを読む側は
    自前の短い時間を持っていて、そこに達したら何も言わずに終わる。
    """
    # 設定ファイルを先に戻す。ルールを読むより前でなければならない。あとから
    # 戻すと、この呼び出しが書き換えたルールファイルをそのまま読んで保護領域を
    # 決めることになり、`deny` を空にされた版で「守るものは無い」と判断する。
    # 守りの根拠を、この呼び出しが触れる前の状態に返してから読む。
    guard = guard_setting_files(stderr, mode, conf, root, payload, record, selfguard.after)

    watched = watched_for(stderr, conf, root, record, payload)
    if len(watched) > 1:
        record.tree, record.project = watched[-1].tree.name, watched[-1].tree.project
    # 既定に落ちたことをこのイベントでは言わない。実行前の判定が呼び出しごとに
    # 言っているので、同じターンで 2 度届く。届く数が増えると、どちらも
    # 読まれなくなる。記録には fallback が残る。
    # 提案の状態を写しへ写す。閉じた子の写しはここで closed/ へ動く。
    # 範囲は実行前の判定と同じ経路で解く。
    if conf.approved:
        phase.sync(stderr, root, conf)
    scope = scope_guard(conf, root)

    text = post.check(
        stderr,
        enforcing=mode == MODE_ENABLE,
        restore=effective_setting(mode, conf.restore_if_deny),
        state_dir=conf.state,
        mine=(conf.state, conf.log),
        watched=watched,
        scope=scope,
        payload=payload,
        record=record,
    )
    # 設定ファイルについて言うことは、実行後の監視の報告より前に置く。
    # ガード自身が触られた回は、他の何よりそれが先に読まれてほしい。
    if guard:
        text = f"{guard}\n\n{text}" if text else guard
    # フェーズが終わったばかりなら、ここで 1 度だけ言う。ゲートは次の呼び出しから。
    if conf.approved:
        parent = phase.parent_for_cwd(root, conf, payload.cwd)
        said = phase.announce(stderr, root, conf, parent) if parent is not None else ""
        if said:
            text = f"{text}\n\n{said}" if text else said
        # サブエージェントが差し戻しを無視して終わったなら、親にそれを言う。
        # 差し戻しは 1 回きりなので、2 度目の終了は黙って通っている。
        bounced = _ignored_bounce(conf.state, payload)
        if bounced:
            text = f"{text}\n\n{bounced}" if text else bounced
    if not text:
        return EXIT_OK

    # 差し戻すのは、直前の実行が汚したと言える分があるときだけ。セッションが
    # 始まる前から在った変更は言うが差し戻さない。差し戻しは「あなたが直せ」で、
    # 誰が書いたか分からないものにそれを言うと、他人の書きかけを消しにいく。
    pushback = record.decision == audit.DENY

    if pushback and mode == MODE_ENABLE:
        # このイベントで差し戻す経路は exit 2 と標準エラーだけ。ツールは
        # すでに走っているので取り消せず、渡せるのは「次に何をするか」になる。
        stderr.write(text + "\n")
        return EXIT_BLOCK

    if pushback:
        # warn は差し戻さない。呼び出しを止めないことがこのモードの約束で、
        # 差し戻しは止めはしないが次の一手を変えさせる。変えさせないまま
        # 数えるためのモードなので、届け先を報告の側にする。
        text = f"[ccnavi dry-run] {MODE_ENABLE} would have sent this back as a correction:\n" + text
    hookio.write_context(stdout, hookio.POST_TOOL_USE, text)
    return EXIT_OK


def load_rules(
    stderr: TextIO, rules_path: str, record: audit.Record, root: str = ""
) -> tuple[rules.RuleSet, str]:
    """ルール集合と、それがどこから来たかを返す。

    読めなければ組み込みの既定に落ちる。「設定が読めない」は「判断できない」
    ではなく「設定が壊れている」。拒否側へ倒すと、壊れたファイルを直すための
    呼び出しまで止まって回復できなくなる。既定モードが block なので、
    ファイルを置く前に hook を登録しただけでセッションが死ぬ（REQ-PRE-06）。

    出所は、いま当てているルールがどこから来たか。既定に落ちているなら
    読めなかったファイルではない。そのファイルを名乗ると、見に行った人が
    当たったルールを見つけられない。
    """
    try:
        rule_set, problems = rules.load(rules_path, root)
    except (OSError, ValueError) as exc:
        stderr.write(f"ccnavi: ルールを読めない: {exc}\n")
        rule_set, problems = builtin.load()
        record.fallback = builtin.FALLBACK
        record.detail = rules_path
    for problem in problems:
        stderr.write(f"ccnavi: {problem}\n")
    return rule_set, builtin.SOURCE if record.fallback else rules_path


# 行き先で判定するツール。subject に解決済みのパスが入っている。
PATH_TOOLS = ("Read", "Write", "Edit", "MultiEdit", "NotebookEdit")


def rules_for(
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    payload: hookio.Input,
    record: audit.Record,
) -> tuple[rules.RuleSet, str, tree.Tree | None]:
    """この呼び出しに当てるルール集合と、その出所と、行き先のツリー（設計 §25.4）。

    パスを持つツールは行き先で 1 本に決まる。行き先のツリーがプロジェクトのものなら、
    その git プロジェクトルートにあるルールファイル。ワークスペースのツリーなら
    ワークスペースのルール。プロジェクトを数えていないワークスペースでは、いつも
    ワークスペースのルールになる。

    Bash はワークスペースと全プロジェクトのルールの和。呼び出しがどのプロジェクトの
    ものかは当てない。当てる仕掛け（cwd、cd の追跡、引数の語の走査）は「どのルール
    ファイルを引くか」にしか効かず、副作用は結局実行後の監視が拾う。和なら deny と
    ask は増える側に倒れ、緩むのは allow の共有だけになる（REQ-MLT-05）。読めない
    プロジェクトは和から外し、外したことを記録に残す（REQ-MLT-06）。
    """
    if payload.tool_name in PATH_TOOLS:
        target = tree.tree_of(root, record.subject, conf.projects)
        if target is not None and target.project:
            where = tree.project_root(conf.projects, target.project)
            rule_set, source = load_rules(
                stderr, settings.project_rules_path(conf, where), record, root
            )
            if not record.fallback:
                prefix_ids(rule_set, target.project)
            return rule_set, source, target
        rule_set, source = load_rules(stderr, conf.rules, record, root)
        return rule_set, source, target

    rule_set, source = load_rules(stderr, conf.rules, record, root)
    if payload.tool_name not in phase.SHELL_TOOLS:
        return rule_set, source, None
    skipped = []
    for p in tree.projects(conf.projects):
        path = settings.project_rules_path(conf, tree.project_root(conf.projects, p.project))
        try:
            extra, problems = rules.load(path, root)
        except (OSError, ValueError) as exc:
            stderr.write(f"ccnavi: プロジェクト {p.project} のルールを読めない: {exc}\n")
            skipped.append(p.project)
            continue
        for problem in problems:
            stderr.write(f"ccnavi: {problem}\n")
        prefix_ids(extra, p.project)
        for name in rules.SECTIONS:
            rule_set.section(name).extend(extra.section(name))
    if skipped:
        note = "unreadable project rules: " + ",".join(skipped)
        record.detail = f"{record.detail}; {note}" if record.detail else note
    return rule_set, source, None


def project_rules_files(conf: settings.Settings) -> list[tuple[str, str]]:
    """置き場にあるプロジェクトと、そのルールファイル。selfguard の守る対象に渡す。"""
    return [
        (p.name, settings.project_rules_path(conf, tree.project_root(conf.projects, p.name)))
        for p in tree.projects(conf.projects)
    ]


def prefix_ids(rule_set: rules.RuleSet, project: str) -> None:
    """プロジェクトのルールの id にプロジェクトの名前を添える。`lib:source` の形（REQ-MLT-07）。

    プロジェクトどうしで同じ id があっても衝突せず、記録を読んだ人がどのファイルを
    見に行けばよいかが id だけで分かる。ワークスペースのルールは裸の id のまま。
    """
    for name in rules.SECTIONS:
        for rule in rule_set.section(name):
            if rule.id:
                rule.id = f"{project}:{rule.id}"


def fail_closed(mode: str) -> int:
    """「判定に達せなかった」ときの終了コード。

    enable は呼び出しを止める。達せなかった判定が許可に化けてはならないから。
    dry-run は通す。呼び出しに手を出さないことがそのモードの約束なので、
    自分の失敗で作業を止めるようでは意味がない。
    """
    return EXIT_OK if mode == MODE_DRY_RUN else EXIT_BLOCK


def _record(stderr: TextIO, log: audit.Log, record: audit.Record) -> None:
    """1 行を書く。書けなかったことは報告して捨てる。
    それがツールの実行可否を変えられてはいけない。"""
    try:
        log.write(record)
    except OSError as exc:
        stderr.write(f"ccnavi: 記録を書けない: {exc}\n")


def subject_of(payload: hookio.Input) -> str:
    """このツールでルールを当てる欄を選ぶ。"""
    if payload.tool_name in phase.SHELL_TOOLS:
        return payload.field_value("command")
    if payload.tool_name == "NotebookEdit":
        path = payload.field_value("notebook_path") or payload.field_value("file_path")
        return full_path(path, payload.cwd)
    if payload.tool_name in ("Read", "Write", "Edit", "MultiEdit"):
        return full_path(payload.field_value("file_path"), payload.cwd)
    if payload.tool_name == AGENT_TOOL:
        # 起動には対象の文字列が無い。ゲートが止める対象なので、見出しを subject に
        # して判定に入れる。
        return payload.field_value("description") or payload.field_value("prompt") or "(agent)"
    return ""


def full_path(path: str, cwd: str) -> str:
    """ファイルのパスを、行き着く先が 1 つに決まる綴りに直す。

    来たままの文字列に当てると、同じ場所を別の綴りで書くだけでルールを外せる。
    相対パスは呼び出し側の作業ディレクトリ次第で意味が変わるし、`..` を挟めば
    `secrets/` を通らない綴りで `secrets/` の中に届く。シンボリックリンクなら
    名前を 1 つ増やすだけで済む。守る対象は名前ではなく場所なので、
    場所まで解いてから当てる。

    解けなかったときも、絶対パスにして `..` を畳むところまではやる。
    まだ存在しないファイルへの書き込みがこれにあたる。
    """
    if not path:
        return ""
    base = cwd or os.getcwd()
    joined = os.path.join(base, os.path.expanduser(path))
    try:
        return os.path.realpath(joined)
    except OSError:
        return os.path.normpath(os.path.abspath(joined))


def screen(tool: str, subject: str, record: audit.Record) -> str:
    """シェルのコマンドを、実際に実行される部分まで絞る。読み切れなかったときは
    record にそう書き残す。

    ルールはコマンドについて書かれたものであって、文字列についてではない。
    git push を引用した文書は push ではないし、そこで拒否を返すことは、
    やっていないことをやったと読み手に告げることになる。

    読み切れないコマンドは生の文字列に落とす。これは以前の挙動そのものなので、
    今まで捕まえていたものが抜けることはない。変わるのは、返す拒否が
    どちらの拒否なのかを名乗らなければならない点。
    """
    if tool != "Bash":
        return subject
    reading = shellread.read(subject)
    if reading.degraded:
        record.degraded = reading.reason
        return subject
    return reading.text


def fallen_back(rules_path: str) -> str:
    """ルールファイルを読めずに組み込みの既定へ落ちたことを伝える文。

    通した回にも返す。ここを黙ると、ガードが立っているように見えて実際には
    プロジェクトのルールを 1 件も見ていない、という状態が続く。それは
    ガードが止まっていることより悪い。止まっていれば誰かが気づくから。

    直し方に Write / Edit を名指しするのは、既定の側がシェルからこの場所への
    書き込みを止めているため。止めた先に道が無いと、拒否は行き止まりになる。
    """
    return (
        "[ccnavi] the rule file at "
        + rules_path
        + " could not be read, so ccnavi is judging with its built-in defaults. "
        "The project's own rules are not in force right now. Repair that file with "
        "the Write or Edit tool; while the defaults are in force the shell cannot "
        "write to it, so that the path that breaks it and the path that fixes it "
        "are not the same one."
    )


# 作業ツリーの切り元と、チケットが承認されたプロジェクトが食い違っている。
CODE_TICKET_PROJECT = "DENY_TICKET_PROJECT_MISMATCH"


def project_mismatch(conf: settings.Settings, root: str, t: tree.Tree, full: str) -> str:
    """作業ツリーの切り元と、そこに結び付く写しの `project:` が違えば、その理由の文。

    範囲の宣言ではなく配線の誤りなので、ルールより先に見る（REQ-MLT-12）。ルールが
    allow と言っていても通さない。子は親から継ぐ。判定はエージェントの申告を見ない。
    行き先のツリーが誰のものかは、そのツリーの `.git` が指す先で決まっている。
    """
    if t.is_main:
        return ""
    copies, _ = approval.copies(conf.approved)
    index = approval.by_id(copies)
    ticket = index.get(t.name)
    if ticket is None:
        return ""
    parent = index.get(ticket.parent) if ticket.is_child else None
    owner = parent.project if parent is not None else ticket.project
    if owner == t.project:
        return ""
    source = approval.copy_path(conf.approved, t.name)
    return "\n".join(
        [
            f"[ccnavi] {CODE_TICKET_PROJECT} (source: {source})",
            f"subject: {full}",
            f"ticket: {ticket.ticket} ({ticket.title}) was approved for project "
            f"'{owner or '(workspace)'}', but worktree {t.name} was cut from "
            f"'{t.project or '(workspace)'}'",
            "Do not write here. The worktree must be created from the repository the ticket "
            "names (projects/<project>), or the ticket must be proposed again for this "
            "repository and approved by the user.",
        ]
    )


def ticket_verdict(
    conf: settings.Settings,
    root: str,
    tool: str,
    full: str,
) -> tuple[str, str]:
    """チケットが承認された範囲について何を言うかを返す。判定と、その理由の文。

    鍵はファイルの行き先。解いた先が `.claude/worktrees/<名前>/` の中なら、その名前と
    同じ識別子の写しで判定する。main の直下ならチケットは無く、ルールだけで判定する。
    呼び出し元の cwd も agent_id も使わない（REQ-TKT-01）。

    範囲の中は allow。ルールが何も言っていない場所で、チケットだけが
    「ここで作業する」と宣言しているので、そこは聞かずに通す。範囲の外は deny。
    チケットが境界を明示している以上、外に出たことは「宣言に反した」になる。
    子は親と合わせて厳しい側が勝つ。

    チケット自身の提案ファイルについては何も言わない。承認された範囲の外に
    あるのが普通で、そこを deny にすると、いちど承認した範囲から出る道が無くなる。
    """
    if not conf.approved or tool not in SCOPE_TOOLS or not full:
        return "", ""
    t = tree.tree_of(root, full, conf.projects)
    if t is None or t.is_main:
        return "", ""
    copies, _ = approval.copies(conf.approved)
    index = approval.by_id(copies)
    # 区別しない機械では綴りの違いを許す。SubagentStart / SubagentStop / 実行後の監視と
    # 同じ引き方。ここだけ厳密に引くと、`I0001-01` と切った作業ツリーは案内では
    # 「効いている」と言われながら判定では権限モード任せに落ちる（敵対的レビューで実測）。
    ticket = tree.lookup(index, t.name)
    if ticket is None or ticket.is_own_file(full):
        return "", ""
    rel = tree.relative(t, full)
    verdict = ticket.decide(rel)
    parent = index.get(ticket.parent) if ticket.is_child else None
    if parent is not None:
        verdict = ticket_mod.combine(verdict, parent.decide(rel))
    if verdict == rules.ALLOW:
        return rules.ALLOW, ""

    area = ", ".join(ticket.paths(rules.ALLOW) + ticket.paths(rules.ASK)) or "(空)"
    source = approval.copy_path(conf.approved, t.name)
    head = [
        f"subject: {full}",
        f"ticket: {ticket.ticket} ({ticket.title}), approved {ticket.approved_at}, "
        f"worktree {t.name}",
        f"scope: {area}",
    ]
    if verdict == rules.ASK:
        return rules.ASK, "\n".join(
            [
                f"[ccnavi] {CODE_TICKET_ASK} (source: {source})",
                *head,
                "This path is one the ticket marks `ask`: the user looks at each write here. "
                "Say what you are changing and why.",
            ]
        )
    return rules.DENY, "\n".join(
        [
            f"[ccnavi] {CODE_TICKET_SCOPE} (source: {source})",
            *head,
            "This path is outside the work area the ticket for this worktree declares. Do the "
            "work inside that area, or, if the task genuinely needs this path, tell the parent "
            "so it can propose a ticket that covers it and ask the user to run 'ccnavi --approve'. "
            "Editing a proposal alone changes nothing.",
        ]
    )


def code_for(tool: str, degraded: str) -> str:
    """拒否の根拠コード。対象がコマンドかパスかで割れる。"""
    if degraded:
        return CODE_UNCERTAIN
    return CODE_COMMAND if tool == "Bash" else CODE_PATH


def undeclared_verdict(permission_mode: str, degraded: str) -> str:
    """どの区画も言及しなかった呼び出しを、権限モードごとにどう扱うか。

    渡すのは「ルールが言及していない」ときだけ。読み切れなかったコマンド
    （degraded）は渡さない。ccnavi が読めなかったという事実は判定の結果に
    現れないので、渡すと「判断材料が足りない」ことが誰にも伝わらないまま
    モードの既定に落ちる。読めなかったことを言えるのはここだけ。

    知らないモードは ask に倒す。名前が 1 つ増えたときに、それが素通りではなく
    確認になるように。設定漏れがガードの消失にならない側へ既定を置く。
    """
    if permission_mode in PERMISSION_NO_JUDGE:
        return rules.DENY
    if not degraded and permission_mode in PERMISSION_JUDGED:
        return HANDOVER
    return rules.ASK


def undeclared(tool: str, subject: str, rules_path: str, degraded: str, refused: bool) -> str:
    """どの区画も言及しなかった呼び出しに返す文。

    危険だとは言わない。言えないから権限モードに委ねている。根拠は設定の穴で
    あって呼び出しの中身ではないので、危険の表明として書くと、受け取った側は
    存在しない危険を探すことになる。

    直し方を書くのは、これが繰り返されるのが穴の側の問題だから。同じ場所で
    何度も止まるなら、答えるべきなのは呼び出しごとの是非ではなく
    「この場所を allow に書くかどうか」になる。

    refused は、確認できる者が居ないモードで許可としなかったことを示す。
    そのときは同じ根拠でも受け取った側の次の一手が違う。人に聞けるなら
    「言えば通るかもしれない」だが、聞けないなら「言っても通らない」ので、
    先に設定を直すか、人が居るセッションでやり直すしかない。
    """
    shown = " ".join(subject.split())
    if len(shown) > SUBJECT_LIMIT:
        shown = shown[:SUBJECT_LIMIT] + f"…(+{len(subject) - SUBJECT_LIMIT})"

    lines = [
        f"[ccnavi] {CODE_UNCERTAIN if degraded else CODE_UNDECLARED} (source: {rules_path})",
        f"subject: {shown}",
    ]
    if degraded:
        lines.append(unreadable(degraded))
        lines.append(
            "ccnavi is asking rather than deciding because it could not tell what this call "
            "would actually do. Say what the command is for, or rewrite it in a form that can "
            "be read: no heredoc, no string handed to something that runs it."
        )
    elif refused:
        lines.append(
            f"No rule in {rules_path} mentions this {'command' if tool == 'Bash' else 'path'}, "
            "and this session has no one to ask: its permission mode answers on its own or not "
            "at all. An undeclared call is not allowed here. Either do the work with something "
            f"the allow section of {rules_path} already covers, or tell the user what needs to "
            "be added there and let them decide."
        )
    else:
        lines.append(
            f"No rule in {rules_path} mentions this {'command' if tool == 'Bash' else 'path'}, "
            "so ccnavi has no verdict of its own and leaves the call to Claude Code's permission "
            "mode, which in this session asks the user. This is not a warning about the call "
            "itself. If this is ordinary work for this project, say so and ask the user to add "
            f"it to the allow section of {rules_path}; that is what stops the same question from "
            "coming back."
        )
    return "\n".join(lines)


def reason_for(rule: rules.Rule, tool: str, subject: str, rules_path: str, degraded: str) -> str:
    """当たったルール 1 件を、それだけで読んで成立する理由に組む。

    載せるのは 3 つ。何に当たったか（対象）、どういう筋の根拠か（理由コード）、
    それを言っているのはどの設定か（出所）。どれが欠けても、受け取った側は
    自分の呼び出しのどこが引っかかったのかを自分では辿れず、
    文面を信じるか無視するかの二択になる。

    件ごとに閉じた形にするのは、1 回の応答に複数の理由が入り、そのうちどれが
    利用者の目に入るかが決まらないため。「上に書いた事情が下の全部に掛かる」形は、
    1 件だけが切り出されて見えた瞬間に意味を失う。読めなかったという断りが
    件ごとに繰り返されるのはその代金で、繰り返しのほうが誤読より安い。
    """
    shown = subject
    if len(shown) > SUBJECT_LIMIT:
        shown = shown[:SUBJECT_LIMIT] + f"…(+{len(subject) - SUBJECT_LIMIT})"
    # 改行を含む対象は 1 行に畳む。理由の骨格が対象の中身で割られると、
    # どこまでが対象でどこからが言い分なのかが読めなくなる。
    shown = " ".join(shown.split())

    # コードはルールが置かれていた区画から決まる。拒否と確認で同じコードを
    # 返すと、受け取った側は「止まった」のか「聞かれている」のかを文面から
    # 推し量ることになる。
    code = CODE_RULE_ASK if rule.decision == rules.ASK else code_for(tool, degraded)
    # 出所は「どのファイルのどのルール」まで。ファイル名だけでは、同じ名前の
    # ルールファイルが複数ある構成で直しに行く先が決まらない。
    source = f"{rules_path}#{rule.id}" if rule.id else rules_path
    if rule.id == phase.CLI_RULE_ID:
        # 組み込み。ルールファイルには無いので、そこを探させない。
        code, source = phase.CODE_CLI, f"builtin#{rule.id} ({settings.GUARD_CLI_ENV})"

    lines = [f"[ccnavi] {code} (source: {source})", f"subject: {shown}"]
    if degraded:
        lines.append(unreadable(degraded))
    lines.append(rule.message)
    return "\n".join(lines)


def unreadable(reason: str) -> str:
    """コマンドを読めないまま出した 1 件に添える断り。

    2 つの違う失敗に同じ文を使わせないために要る。「禁止されたコマンドを実行した」と
    「読めない文字列のどこかにその語がある」では次にやることが違うし、
    後者なのに前者を渡された読み手は、書いた覚えのないコマンドを探しに行く。
    """
    what = {
        shellread.REASON_UNTERMINATED: ("a quote or heredoc in this command never closes"),
        shellread.REASON_TAKEN_AS_CODE: (
            "this command hands a string to something that runs it as code"
        ),
    }.get(reason, "this command could not be read")
    return (
        "note: " + what + ", so this rule was matched against the raw text of the "
        "command instead of against what runs. If these words are only being "
        "written down and not run, nothing was deliberately forbidden: move the "
        "text into a file, or split the command up, and try again."
    )


def resolve_mode(stderr: TextIO, flag_value: str, conf: settings.Settings) -> str:
    """設定された値をモードにする。

    設定ファイルが決められるのは warn と block だけで、その編集は次のツール
    呼び出しから効く。disable を言えるのは環境だけ。設定ファイルは作業ツリーの中に
    あってエージェントが書けるので、そこに書かれた disable を効かせると、
    見張られている側が見張りを止められてしまう。

    フラグは両方より強い。テストが、たまたま走った環境に依存せずモードを
    固定できるように。

    どこにも値が無ければ enable。設定の欠落が、ガードの欠落にならないように。
    """
    source, value = settings.MODE_ENV, conf.mode
    if flag_value:
        source, value = "--mode", flag_value

    normalized = value.lower()

    if normalized == MODE_DRY_RUN:
        return MODE_DRY_RUN

    if normalized == MODE_DISABLE:
        # disable の経路は 1 本だけ。セッションを起動した人の環境から来て、
        # かつ作業ツリーの中の何もそれを求めていないとき。設定ファイルもフラグも
        # エージェントが書ける場所から来るし、そこでの編集は次のツール呼び出しから
        # 効くので、どちらの off を認めても、見張られている側が見張りを
        # 止められることになる。
        from_file = conf.mode_declared_in_file.lower()
        from_env = conf.mode_from_environment.lower()
        if not flag_value and from_env == MODE_DISABLE and from_file != MODE_DISABLE:
            return MODE_DISABLE
        stderr.write(
            f"ccnavi: {source}={MODE_DISABLE} is ignored because it comes from inside "
            f"the project; start the session with {settings.MODE_ENV}={MODE_DISABLE} "
            "in the environment instead\n"
        )
        return MODE_ENABLE

    if normalized in ("", MODE_ENABLE):
        return MODE_ENABLE

    # 解釈できない値も最も強いモードに着地するが、それを言うことに意味がある。
    # 名前を変えた設定や打ち間違いが、黙っていると意図した選択に見えてしまい、
    # 誰にも見えない理由でガードが締まることになる。
    stderr.write(
        f"ccnavi: {source}={value!r} is not a mode; using {MODE_ENABLE}. "
        f"Valid modes are {MODE_DISABLE}, {MODE_DRY_RUN} and {MODE_ENABLE}\n"
    )
    return MODE_ENABLE


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
