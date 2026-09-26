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
    configsync,
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
from .modes import EXIT_ERROR, EXIT_OK

USAGE = """ccnavi guards agent tool calls and guides the agent to a safer alternative.

It is a hook command, not something to run by hand: it reads one hook payload
as JSON on stdin and writes its response to stdout.

Register it on PreToolUse to judge calls before they run, and on PostToolUse to
watch the working tree for protected files that changed anyway. Exercise it with

    echo '{"hook_event_name":"PreToolUse","tool_name":"Bash",
           "tool_input":{"command":"git push"}}' | ccnavi

To check the rules file and the settings without making a decision, run

    ccnavi --lint [--json]

It reads no payload, reports anything that could disable the guard as an error
or a warning, and exits non-zero when it reports an error. --json prints the
shape documented in README.md ("lint の JSON"); the VS Code extension reads it.

To try or lint one project's rules before saving them, hand the edited file in
by the project's name (this flag is for --test, --test-samples, --lint and
--explain only; anything else - a hook invocation, a ticket or review
subcommand - drops it and says so on stderr):

    ccnavi --test Write projects/lib/src/a.py --project-rules-file lib=/tmp/rules.yml

The phase types of one layer are handed in the same way (self is the
workspace's own layer; the common layer uses --phases):

    ccnavi --lint --project-phases-file self=/tmp/phases.yml

One child ticket's flow is checked the same way, read by the same reader and
the same checks that SubagentStart uses (--lint only; the VS Code extension
hands the edited flow in before it opens or saves one):

    ccnavi --lint --json --flow /tmp/flow.yml

The common layer's own three files are moved by --rules, --phases and --risk,
and where the layers are looked for by --projects and --project-home. All five
are on the same gate: diagnosis only, dropped everywhere else.

--root and --cwd say where this run is happening. The wrapper scripts in
.ccnavi/scripts/ work them out and pass them, so they are accepted once only;
a second one is refused rather than taken as an override.

To list the tickets, their places, the phase marks and the review holds
in a machine-readable form (the VS Code board extension reads this), run

    ccnavi --explain --json

It reads no payload and never reaches the remote. The shape is documented
in README.md ("ボードの JSON").

To try one call against the rules without running it, or to run every sample
in a file against them, run

    ccnavi --test Bash "git push" [--json]
    ccnavi --test-samples .ccnavi/common/rule-samples.yml [--json]

Both go through the same decision as the hook. --json prints the shape
documented in README.md ("試験の JSON"); the VS Code extension reads it.

To review the pending tickets and approve the work areas they declare, run

    ccnavi --approve
    ccnavi --approve i0002 i0002-01        (only these, e.g. from a filtered board;
                                            ids go last, after every flag)

It scans wip/proposals/todo/ in every worktree, shows what each ticket makes
writable and whether it needs a human review, then moves the approved ticket to
.ccnavi/approved/doing/. Only that place is consulted when judging calls, so
writing a proposal never widens the area on its own. Ids only narrow the batch:
an id that is not pending, or a child listed without its pending parent or
its parent's pending revision, approves nothing.

Before asking the user to approve, the agent verifies that the proposal it just
wrote is in a state that can be approved:

    ccnavi --approve --preview --verify [--json] [<id>...]

It places nothing and needs no terminal. Exit 0 is yes: every named ticket (or
every pending one, when no id is given) goes into the batch as it stands, so the
user can be asked. Exit 3 is no: an id that is not pending, no pending ticket at
all, or a proposal the approval drops. The reasons are printed per ticket. Exit
1 stays what it is everywhere else - a usage or settings error, not an answer -
so a wrong spelling is never read as a proposal to fix.

Two things are not a no, because --approve does not drop them either: scope that
exceeds the parent or the phase type (writes there stay blocked after approval),
and a proposal that cannot be read (the scan covers every worktree, before the
ids narrow it, so another session's draft would answer no). Both are printed.
Having nothing pending is the one place where the two differ: --approve calls
that a success with nothing to do, the verify calls it a no.

The VS Code board extension approves from an overlay instead of the terminal:

    ccnavi --approve --preview --json [<id>...]  (show the batch; places nothing)
    ccnavi --approve --yes <id,id,...> --digest <hex> --json [<id>...]
        (approve exactly what was shown; --digest is the preview's `digest`,
         the trailing ids are the same filter)

--yes needs no terminal; it refuses when the batch or the text changed since it
was shown, and when --digest is missing.
The next UserPromptSubmit / PreToolUse tells the model once about the new
copies (the same text the extension hands to Claude Code).

The parent agent moves tickets between states and asks for reviews through the
scripts in .ccnavi/scripts/, which call

    ccnavi ticket start|finish|cancel <id> [--reason <why>]
        (start writes the base point into .ccnavi/approved/doing/<id>.md; finish moves
         it to wip/proposals/review/ when the phase is reviewed, else to
         .ccnavi/approved/done/; cancel moves it to .ccnavi/approved/done/)
    ccnavi ticket record-risk <child> <factor> yes|no --reason <why>   (qualitative risk)
    ccnavi review prepare   --cwd <dir> --phase N --body-file <path>
    ccnavi review requested --cwd <dir> --phase N --result <json>
    ccnavi review confirm   --cwd <dir> --phase N --result <json>
    ccnavi review ready     --cwd <dir> --result <json>

ccnavi never reaches the remote itself. The script fetches the merge request,
its threads and reviews, and hands them over as --result <json>.

A human accepts unresolved review threads, from the parent worktree, with

    sh <workspace root>/.ccnavi/scripts/ccnavi-review.sh decide N

(the scripts live only in the workspace, so a worktree cut from a project
cannot reach them by the relative path)

which fetches the threads and runs

    ccnavi --reviewed N --accept-unresolved --result <json> --cwd <parent worktree>

A phase whose type says `review: chat` is reviewed in the session itself. There
is no merge request and no copy to read, so a human opens that gate from the
terminal with

    ccnavi --reviewed N --chat --cwd <parent worktree>

which only applies to phases whose type declared `chat`.

A human closes a parent early ("good enough for now") with

    sh <workspace root>/.ccnavi/scripts/ccnavi-review.sh close-early --reason <why>

which runs `ccnavi --close-early --reason <why> --result <json>` and then
un-drafts the merge request and files the leftovers as a new issue.

When starting a project parent overwrote the project's .ccnavi/ with the common
layer, the first review shows it. A parent that closes without any review stops
until a human has seen it at the terminal with

    ccnavi --config-synced <parent>
"""


# フラグで上書きする設定の欄と、空文字を「指定した」と読むかどうか。
# 空文字を受ける欄は、既定が None のフラグで運ぶ。「記録しない」「控えを持たない」を
# 言えないと、診断のための 1 回が、走っているセッションの記録と控えに必ず混ざる。
OVERRIDES = (
    ("log", True),
    ("rules", False),
    ("state", True),
    ("approved", False),
    ("phases", True),
    ("risk", True),
    ("projects", True),
)
# 作業ツリーのルートからの相対で書く欄。区切りを "/" に揃え、前後の "/" を落とす。
RELATIVE_OVERRIDES = ("tickets", "project_home")
# 層の置き場を動かすフラグと、「渡されなかった」ときの値。`--project-rules-file` と
# 同じで診断の経路でだけ効く。3 つめの欄が既定なのは、渡されたかどうかを OVERRIDES /
# RELATIVE_OVERRIDES と同じ読み方で決めるため（`--rules ""` は指定と数えず、
# `--risk ""` は数える）。
#
# 前の 3 本は共通層の中身（ルール・フェーズの種類・リスクの配点）、後の 2 本は
# **層を探す先**。`--projects` はプロジェクトの層の置き場、`--project-home` は
# 各 git プロジェクトルートの下の ccnavi ディレクトリの名前で、どちらも外すと
# プロジェクトの層がまるごと消える。実測では `ticket finish <子> --project-home .nothere`
# で、実績リスク 55 (CRITICAL) の子が 25 (MEDIUM) になり、レビュー待ちを飛ばして
# 閉じた（ADR-0067）。中身を差し替えるのと結果が同じなので、同じ門に載せる。
LAYER_OVERRIDES = (
    ("--rules", "rules", ""),
    ("--phases", "phases", None),
    ("--risk", "risk", None),
    ("--projects", "projects", None),
    ("--project-home", "project_home", ""),
)
# 落としたときの文面。5 本のフラグで同じものを使う。門が 2 つあるように読ませない。
DIAGNOSIS_ONLY = "ccnavi: {flag} は診断（--test / --lint / --explain）でだけ効く\n"
# `.ccnavi/scripts/` の sh が自分で計算して渡す綴りと、渡されなかったときの値。
# どちらも「いまどこで動いているか」で、エージェントが名乗るものではない。
#
# sh は自分のぶんを先に置き、エージェントの引数を後ろに繋ぐ
# （`exec "$bin" --root "$root" ticket "$@"`）。argparse は同じオプションを後勝ちで読むので、
# 後ろに 1 本足すだけで sh が渡した本物を上書きできた。`--root` は共通層の 3 本も
# `projects` も `approved` もそこから導かれる（`settings.load`）ので、1 本で全部動く。
# 実測では、本物のツリーへシンボリックリンクを張った偽のルートを渡すと、子チケットが
# 本物の置き場に「リスク 0」で閉じられた（ADR-0067）。
#
# 正しい 1 本が先に在ることに頼らず、2 本目が在ること自体を断る。落として先へ進むのでは
# なく止めるのは、この 2 つに「2 度渡す」正しい使い方が無いから。診断の 5 本と違って、
# 効く経路の話ではない。
WRAPPER_FLAGS = (("--root", "root", None), ("--cwd", "cwd", ""))


def _one_wrapper_flag_each(stderr: TextIO, args: argparse.Namespace) -> bool:
    """sh が渡す綴りが 2 度来ていないかを見て、1 本に均す。2 度来ていたら False。

    数えるのは argparse に任せる（`action="append"`）。argv を自分で数えると、
    オプションの位置に立っていない `--root` という語まで数えてしまう
    （`--reason=--root` のように別のオプションの値として書かれた形）。
    """
    for flag, name, absent in WRAPPER_FLAGS:
        given = getattr(args, name)
        if given is not None and len(given) > 1:
            stderr.write(f"ccnavi: {flag} は 1 度しか渡せない（{len(given)} 度渡された）\n")
            return False
        setattr(args, name, given[-1] if given else absent)
    return True


def _drop_outside_diagnosis(stderr: TextIO, args: argparse.Namespace) -> None:
    """診断の外で渡された層の置き場の差し替えを、標準エラーに出して落とす。

    フラグは設定ファイルより強いので、落とさないと保存していない `rules.yml` /
    `phases.yml` / `risks.yml` で判定と採点が走り、層そのものも外せる。届く経路は
    `.ccnavi/scripts/` の sh で、受け取った引数を実行ファイルへ素通しする（ADR-0067）。
    """
    for flag, name, absent in LAYER_OVERRIDES:
        if getattr(args, name) == absent:
            continue
        stderr.write(DIAGNOSIS_ONLY.format(flag=flag))
        setattr(args, name, absent)


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


def _json_out_of_test(argv: list[str]) -> list[str]:
    """`--test` が取る 2 語に混ざった `--json` を外し、末尾へ回す。

    `--test` は TOOL と SUBJECT の 2 語を取るので、`--test --json Bash ls` と書くと `--json` を
    TOOL として取る。`--json` はツールの名前にも調べるコマンドにもならない綴りなので、
    出力の形の指定として読む。
    """
    if "--test" not in argv:
        return argv
    at = argv.index("--test")
    taken = argv[at + 1 : at + 3]
    if "--json" not in taken:
        return argv
    kept = [word for word in taken if word != "--json"]
    return [*argv[: at + 1], *kept, *argv[at + 3 :], "--json"]


def run(stdin: TextIO, stdout: TextIO, stderr: TextIO, argv: list[str]) -> int:
    """1 回の起動を処理する。"""
    # 前方一致を受けない。受けると `--close` や `--review` が `--close-early` / `--reviewed` として
    # 走り、全部綴った形しか見ない組み込みの deny（`phase._CLI_FORMS`）を抜ける。
    parser = argparse.ArgumentParser(prog="ccnavi", add_help=False, allow_abbrev=False)
    # 綴りは sh が計算して渡す。2 度来ていないかを見るので、束ねて受ける（WRAPPER_FLAGS）。
    parser.add_argument("--root", action="append", default=None)
    parser.add_argument("--mode", default="")
    parser.add_argument("--rules", default="")
    parser.add_argument("--log", default=None)
    parser.add_argument("--state", default=None)
    parser.add_argument("--restore-if-deny", default="")
    parser.add_argument("--guard-core-files", default="")
    parser.add_argument("--guard-ticket-approval", default="")
    # チケット制御を使うか。enable / disable。VS Code 拡張が試し打ちで disable を渡す。
    parser.add_argument("--ticket-control", default="")
    # リモートの写し（JSON）。.ccnavi/scripts/ccnavi-review.sh が取ってきて渡す。
    parser.add_argument("--result", default="")
    parser.add_argument("--lint", action="store_true")
    parser.add_argument("--approve", action="store_true")
    # 承認の対象の一覧を見るだけ（承認済みチケットを置かない）。
    # VS Code の拡張がオーバーレイに出すために打つ。
    parser.add_argument("--preview", action="store_true")
    # 承認できる状態かを確かめるだけ（`--preview` と一緒に使う）。承認済みチケットは置かず、
    # 通るかどうかを終了コードで返す。エージェントが人に承認を頼む前に打つ。
    parser.add_argument("--verify", action="store_true")
    # 見せた一覧の識別子（カンマ区切り）。拡張のオーバーレイで人が押した承認。端末は要らない。
    parser.add_argument("--yes", default="")
    # 見せた承認画面の本文と承認済みチケットに写る中身の指紋（preview の `digest`）。
    # `--yes` と一緒に渡す。
    parser.add_argument("--digest", default="")
    parser.add_argument("--test", nargs=2, metavar=("TOOL", "SUBJECT"), default=None)
    # 見本をぜんぶ判定に掛ける。tools/check_rules.py と VS Code 拡張が呼ぶ。
    parser.add_argument("--test-samples", metavar="FILE", default="")
    parser.add_argument("--explain", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--tickets", default="")
    parser.add_argument("--approved", default=None)
    parser.add_argument("--phases", default=None)
    parser.add_argument("--risk", default=None)
    # プロジェクトの置き場と、ccnavi ディレクトリ（設計 11）。
    parser.add_argument("--projects", default=None)
    parser.add_argument("--project-home", default="")
    # 1 つのプロジェクトのルールファイルを名前で差し替える（<名前>=<パス>）。診断だけ。
    # VS Code 拡張が編集中のプロジェクトのルールを保存せずに試すために渡す。
    parser.add_argument("--project-rules-file", default="")
    # 同じ差し替えを層のフェーズの種類に対して行う（<名前>=<パス>、名前は self かプロジェクト）。
    # VS Code 拡張のフェーズ管理画面が、編集中の層の種類を保存せずに検証するために渡す。
    parser.add_argument("--project-phases-file", default="")
    # 子チケットのフロー 1 本を、SubagentStart と同じ読みで確かめる（`--lint` だけ）。
    # VS Code 拡張のフロー編集画面が、開くときと保存の前に編集中の本文を一時ファイルで渡す。
    parser.add_argument("--flow", default="")
    # チケットの状態とレビューの操作。人か、親が保護済みスクリプトから呼ぶ。
    parser.add_argument("command", nargs="*")
    parser.add_argument("--cwd", action="append", default=None)
    parser.add_argument("--phase", type=int, default=None)
    parser.add_argument("--body-file", default="")
    parser.add_argument("--reason", default="")
    parser.add_argument("--reviewed", type=int, default=None)
    parser.add_argument("--accept-unresolved", action="store_true")
    parser.add_argument("--chat", action="store_true")
    # 人が端末で打つ締め。`--approve` / `--reviewed` と同じく、人の判断はフラグで受ける。
    parser.add_argument("--close-early", action="store_true")
    # 人が端末で見たと残す、着手で上書きした設定（レビューの無いまま閉じる親、設計 11.12）。
    parser.add_argument("--config-synced", default="")
    parser.add_argument("-h", "--help", action="store_true")
    try:
        args = parser.parse_args(_json_out_of_test(argv))
    except SystemExit:
        return EXIT_ERROR
    if args.help:
        stderr.write(USAGE)
        return EXIT_ERROR
    if not _one_wrapper_flag_each(stderr, args):
        return EXIT_ERROR

    root = args.root if args.root is not None else default_root()
    conf, problems = settings.load(root)

    # 層の置き場（中身の 3 本と、層を探す先の 2 本）の差し替えは診断の経路でだけ効く。
    # hook からの判定にも、チケットとレビューの副命令にも差し替えの手段を残すと、
    # 設定を保存せずに緩める道になるので、そこでは無視する（ADR-0067）。
    # 診断は payload を読まず、判定を実行にも記録にも繋げないので、保存していない設定を
    # 指しても実運用に漏れない。
    diagnosing = args.lint or args.test is not None or bool(args.test_samples) or args.explain
    if not diagnosing:
        _drop_outside_diagnosis(stderr, args)

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
    # チケット制御も 2 値。読めない値は enable（使う側）に倒す。切ったつもりで
    # 綴りを誤った設定は、判定では効いたままになり、--lint が error で名指しする。
    if args.ticket_control:
        conf.ticket_control_declared = args.ticket_control
    conf.ticket_control = selfguard.resolve(
        stderr,
        args.ticket_control,
        conf.ticket_control,
        settings.TICKET_CONTROL_ENV,
        selfguard.GATE_SETTINGS,
    )

    # 1 つの層だけを差し替える形。効く経路は共通層の 3 本と同じ。
    for flag, value, swaps in (
        ("--project-rules-file", args.project_rules_file, conf.project_rules_files),
        ("--project-phases-file", args.project_phases_file, conf.project_phases_files),
    ):
        if not value:
            continue
        if not diagnosing:
            stderr.write(DIAGNOSIS_ONLY.format(flag=flag))
            continue
        name, sep, path = value.partition("=")
        if not sep or not name or not path:
            stderr.write(f"ccnavi: {flag} は <名前>=<パス> の形で書く\n")
            return EXIT_ERROR
        swaps[name] = os.path.abspath(path)

    # フローの確かめは `--lint` だけが読む。判定にも採点にも効かないが、ほかの差し替えと同じく
    # 診断の外では落として言う。診断でも `--lint` でなければ読む先が無いので、そう言って落とす。
    if args.flow:
        if not diagnosing:
            stderr.write(DIAGNOSIS_ONLY.format(flag="--flow"))
            args.flow = ""
        elif not args.lint:
            stderr.write("ccnavi: --flow は --lint でだけ読む\n")
            args.flow = ""
        else:
            args.flow = os.path.abspath(args.flow)

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
            as_json=args.json,
            flow_path=args.flow,
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
        if not conf.tickets_enabled:
            stderr.write(f"ccnavi: チケット制御が disable（{settings.TICKET_CONTROL_ENV}）\n")
            return EXIT_ERROR
        if args.preview and args.yes:
            stderr.write("ccnavi: --preview と --yes は同時に付けられない\n")
            return EXIT_ERROR
        # 確かめるだけの枝は `--preview` に相乗りする。単独で打てる形にすると、組み込みの
        # deny（phase.ticket_approval_rule）が免除するのは `--preview` の付いた `--approve`
        # だけなので、エージェントが打てないものを案内することになる。
        if args.verify and not args.preview:
            stderr.write("ccnavi: --verify は --approve --preview と一緒に使う\n")
            return EXIT_ERROR
        # 承認できる状態かを確かめるだけ。置かないのは `--preview` と同じで、違うのは
        # 通るかどうかを終了コードで返すところ（REQ-APV-13）。答えは 0（はい）と
        # 3（いいえ）で、使い方と設定の誤りの 1 とは分ける。
        if args.verify:
            return approval.verify(stdout, stderr, conf, root, args.json, list(args.command))
        # 見るだけの経路。承認済みチケットを置かないので端末の壁は要らない。後ろに並べた語は
        # `--approve` と同じで、承認の対象に入れる識別子（ボードの絞り込みで見えている分）。
        if args.preview:
            code = approval.preview(stdout, stderr, conf, root, args.json, list(args.command))
            return EXIT_OK if code == 0 else EXIT_ERROR
        # 拡張のオーバーレイで人が押した承認。端末の壁の代わりに、見せた一覧と今の一覧が
        # 同じであることを求める。エージェントがこれを Bash で打つ形は組み込みの
        # deny（phase.ticket_approval_rule）が止める。
        if args.yes:
            # 後ろに並べた語は preview に渡したのと同じ絞り。`--yes` は見せた識別子。
            code = approval.approve_yes(
                stdout,
                stderr,
                conf,
                root,
                args.yes.split(","),
                args.json,
                list(args.command),
                digest=args.digest,
            )
            return EXIT_OK if code == 0 else EXIT_ERROR
        if not _from_terminal(stdin, conf, stderr, "--approve"):
            return EXIT_ERROR
        rule_set, _ = ruleload.load_rules(stderr, conf, audit.Record(), root)
        # `--approve` の後ろに並べた語は、承認の対象に入れる識別子。無ければ承認待ち全部。
        approved = approval.approve(
            stdin, stdout, stderr, conf, rule_set, root, only=list(args.command)
        )
        return EXIT_OK if approved == 0 else EXIT_ERROR

    # チケットの状態とレビューの操作。payload を読まない。
    # 着手で上書きした設定を、人が端末で見たと残す。レビューの代わりなので、人の判断と同じ門。
    if args.config_synced:
        if not conf.tickets_enabled:
            stderr.write(f"ccnavi: チケット制御が disable（{settings.TICKET_CONTROL_ENV}）\n")
            return EXIT_ERROR
        if not _from_terminal(stdin, conf, stderr, "--config-synced"):
            return EXIT_ERROR
        code = configsync.acknowledge(stdin, stdout, stderr, conf, root, args.config_synced)
        return EXIT_OK if code == 0 else EXIT_ERROR

    if args.command or args.reviewed is not None or args.close_early:
        # 残った指摘を見せるだけの `--preview` と、オーバーレイで押した `--yes` は端末を求めない。
        # `--yes` の守りは、見せた指摘の指紋の一致と、シェルから打つ形を止める組み込みの deny。
        board = args.reviewed is not None and args.accept_unresolved and (args.preview or args.yes)
        if (
            args.reviewed is not None
            and not board
            and not _from_terminal(stdin, conf, stderr, "--reviewed")
        ):
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
    # この門は enable / disable の 2 値。止めずに報告する段は CCNAVI_MODE=dry-run が
    # 持つので、ここに dry-run は無い。読めない値は enable に倒れる。
    conf.guard_unwatched = selfguard.resolve(
        stderr,
        "",
        conf.guard_unwatched,
        settings.GUARD_UNWATCHED_ENV,
        selfguard.GATE_SETTINGS,
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

    `--approve` と `--reviewed` と `--close-early` は人の合意そのもの。エージェントが
    Bash から打てばその合意を自分で出せる。標準入力が端末であることを求めるのが、この経路が
    hook の中や `echo y |` から来ていないことの、いちばん安い証拠になる。
    CCNAVI_GUARD_TICKET_APPROVAL=disable で切れる（テストと、端末を持たない実行環境のため）。
    """
    if conf.guard_ticket_approval == selfguard.DISABLE:
        return True
    try:
        if stdin.isatty():
            return True
    except (AttributeError, ValueError):
        pass
    # 切り方（CCNAVI_GUARD_TICKET_APPROVAL）は文面に書かない。読むのはエージェントで、書けば
    # 人の判断を自分で出す道を教えることになる。切り方は README の設定の表にある。
    stderr.write(
        f"ccnavi: {flag} は端末から打つもの。標準入力が端末ではない。利用者に端末で打ってもらう\n"
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

    `ticket start|finish|cancel <識別子>` と `review prepare|requested|confirm`、それに
    人が打つ `--reviewed` と `--close-early`。どれも payload を読まず、判定も記録もしない。
    リモートの写しは `--result <json>` で受け取る。exe はネットワークに出ない。
    """
    if not conf.tickets_enabled:
        stderr.write(f"ccnavi: チケット制御が disable（{settings.TICKET_CONTROL_ENV}）\n")
        return EXIT_ERROR
    cwd = args.cwd or os.getcwd()
    if args.close_early:
        if not args.result:
            stderr.write("ccnavi: --close-early には --reason <理由> と --result <json> が要る\n")
            return EXIT_ERROR
        # 人の判断。`--approve` / `--reviewed` と同じく端末を求める。
        if not _from_terminal(stdin, conf, stderr, "--close-early"):
            return EXIT_ERROR
        code = review.close_early(stdin, stdout, stderr, root, conf, cwd, args.reason, args.result)
        return EXIT_OK if code == 0 else EXIT_ERROR
    if args.reviewed is not None and args.accept_unresolved and (args.preview or args.yes):
        if args.preview and args.yes:
            stderr.write("ccnavi: --preview と --yes は同時に付けられない\n")
            return EXIT_ERROR
        if not args.json or not args.result:
            stderr.write("ccnavi: ボードの経路は --json と --result <json> を付けて打つ\n")
            return EXIT_ERROR
        if args.preview:
            code = review.decide_preview(
                stdout, stderr, root, conf, cwd, args.reviewed, args.result
            )
        else:
            code = review.decide_yes(
                stdout,
                stderr,
                root,
                conf,
                cwd,
                args.reviewed,
                args.result,
                args.yes,
                args.digest,
            )
        return EXIT_OK if code == 0 else EXIT_ERROR
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
            args.chat,
        )
        return EXIT_OK if code == 0 else EXIT_ERROR

    words = list(args.command)
    kind = words[0] if words else ""
    verb = words[1] if len(words) > 1 else ""
    target = words[2] if len(words) > 2 else ""
    code = 1
    if kind == "ticket" and verb in ("start", "finish", "cancel") and target:
        if verb == "start":
            code = ops.start(stdout, stderr, root, conf, target)
        elif verb == "finish":
            code = ops.finish(stdout, stderr, root, conf, target)
        else:
            code = ops.cancel(stdout, stderr, root, conf, target, args.reason)
    elif kind == "ticket" and verb == "record-risk":
        # ticket record-risk <子> <項目> yes|no --reason <根拠>
        if len(words) < 5:
            stderr.write(
                "ccnavi: ticket record-risk には <子> <項目> yes|no と --reason <根拠> が要る\n"
            )
        else:
            code = ops.record_risk(
                stdout, stderr, root, conf, words[2], words[3], words[4], args.reason
            )
    elif kind == "review" and verb == "prepare":
        if args.phase is None or not args.body_file:
            stderr.write("ccnavi: review prepare には --phase <N> と --body-file <path> が要る\n")
        else:
            code = review.prepare(stdout, stderr, root, conf, cwd, args.phase, args.body_file)
    elif kind == "review" and verb in ("requested", "confirm"):
        if args.phase is None or not args.result:
            stderr.write(f"ccnavi: review {verb} には --phase <N> と --result <json> が要る\n")
        elif verb == "requested":
            code = review.requested(stdout, stderr, root, conf, cwd, args.phase, args.result)
        else:
            code = review.confirm(stdout, stderr, root, conf, cwd, args.phase, args.result)
    elif kind == "review" and verb == "ready":
        if not args.result:
            stderr.write("ccnavi: review ready には --result <json> が要る\n")
        else:
            code = review.ready(stdout, stderr, root, conf, cwd, args.result)
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
