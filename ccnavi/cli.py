"""標準入出力とコマンドラインを 1 つの判定に繋ぐ。

run は終了コードを返す。自分で終了しないので、道具ぜんぶを別プロセス無しで
テストから動かせる。

ここに置くのは引数の解釈と振り分けだけ。hook のイベントごとの手順は events、
実行前の判定は judge、判定につける文面は reasons、モードと終了コードは modes に
ある。チケットとレビューの操作（`ticket ...` / `review ...`）は operate が
ops / review へ渡す。
"""

from __future__ import annotations

import argparse
import contextlib
import os
import re
import tempfile
import time
from typing import TextIO

from . import (
    audit,
    c1,
    configsync,
    core,
    diaglog,
    diagnose,
    docsearch,
    events,
    fsio,
    history,
    hookio,
    judge,
    lint,
    modes,
    ops,
    phase,
    prune,
    review,
    selfguard,
    settings,
    suggest,
    version,
)
from .modes import EXIT_ERROR, EXIT_OK

USAGE = """ccnavi guards agent tool calls and guides the agent to a safer alternative.

It is a hook command, not something to run by hand: it reads one hook payload
as JSON on stdin and writes its response to stdout.

Register it on PreToolUse to judge calls before they run, and on PostToolUse to
watch the working tree for protected files that changed anyway. Exercise it with

    echo '{"hook_event_name":"PreToolUse","tool_name":"Bash",
           "tool_input":{"command":"git push"}}' | ccnavi

To say which build this is, run

    ccnavi --version [--json]

It prints the version, the commit it was built from ("unknown" when run from
source), the compat version the scripts in .ccnavi/scripts/ and the VS Code
extension compare with their own, and every flag it accepts. It reads no
payload and no settings. The --json shape is documented in README.md
("版の JSON").

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

To find a markdown document by what it is rather than by a line in its body,
search the frontmatter index of the workspace and its projects (paths are
from the workspace root, e.g. projects/lib/docs/x):

    ccnavi --docs [--type T] [--tag T] [--keyword K] [--path SUB] [--text SUB]
                  [--since DATE] [--until DATE] [--sort path|mtime|type|title]
                  [-r] [--limit N] [--format table|path|detail|json|jsonl|count]
                  [--no-refresh]

The same filter given twice is OR, different filters are AND, and case is
ignored. --type, --tag and --keyword match whole values; --path matches part
of the path without .md; --text matches part of the path, the mtime or any
frontmatter value; strings are compared in NFC. --since and --until take
YYYY-MM-DD[THH[:MM[:SS]]] and --until runs to the end of what it names (a date
to 23:59:59, THH to :59:59, THH:MM to :59). No match is still exit 0. Before
searching it brings the per-directory index.jsonl up to date (it writes only
where git ignores index.jsonl and the file is ccnavi's own; a tree that
ignores none is left out and named on stderr). The first run reads the head
of every markdown file; later runs read only those whose mtime moved.
--no-refresh reuses what is there. --json is --format json. The rows are
documented in README.md ("ドキュメントの索引"). These filters are for --docs
only; anywhere else they stop the run with exit 1, and flags that belong to
other runs stop --docs the same way.

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

To rotate the decision log and remove old logs and finished sessions' state
(SessionStart does the same on its own), run

    ccnavi --prune [--preview]

--preview only lists what would move. Without it, --prune needs a terminal.

To draft rules from the decision log (logs/decisions.jsonl and its rotated
decisions.*.jsonl), run

    ccnavi --suggest [--json]

It lists deny/ask drafts only: calls no rule mentioned that kept being handed
over, and denies that kept stopping the same call (review their message).
Each draft passed the same checks as --lint and --test-samples; the rest are
counted and dropped. Nothing is written. --json prints the shape documented in
README.md ("候補の JSON"); the VS Code extension reads it.

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
    ccnavi review confirm   --cwd <dir> --phase N --result <json> [--actor <account>]
    ccnavi review ready     --cwd <dir> --result <json>
    ccnavi sync paths
        (for .ccnavi/scripts/ccnavi-sync.sh: one "<key> <value>" per line - the
         approved and proposal places, the ccnavi directory, and the integration
         branch written in .claude/settings.local.json env; reads no environment
         for the integration branch)
    ccnavi sync check <parent> [<repo>]
        (for ccnavi-sync.sh after it took in <parent>: re-judges the family's
         approved tickets and its authority; a "check 1" line, then one
         "<severity> <detail>" per line; exit 1 when an error stops the family)

ccnavi never reaches the remote itself. The script fetches the merge request,
its threads and reviews, and hands them over as --result <json>.

A human accepts unresolved review threads, from the parent worktree, with

    sh <workspace root>/.ccnavi/scripts/ccnavi-review.sh decide N

(the scripts live only in the workspace, so a worktree cut from a project
cannot reach them by the relative path)

which fetches the threads and runs

    ccnavi --reviewed N --accept-unresolved --result <json> --cwd <parent worktree>
        [--actor=<account> --via=terminal|board]
        (the script passes the token owner when it can read it; the reviewed mark
         keeps the account and the way. Without it the mark is as before)

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
# 空文字を受ける欄は、既定が None のフラグで渡す。「記録しない」「控えを持たない」を
# 言えないと、診断のための 1 回が、走っているセッションの記録と控えに必ず入り込む。
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
# 同じで診断の経路でだけ使われる。3 つめの欄が既定なのは、渡されたかどうかを OVERRIDES /
# RELATIVE_OVERRIDES と同じ読み方で決めるため（`--rules ""` は指定と数えず、
# `--risk ""` は数える）。
#
# 前の 3 本は共通層の中身（ルール・フェーズの種類・リスクの配点）、後の 2 本は
# **層を探す先**。`--projects` はプロジェクトの層の置き場、`--project-home` は
# 各 git プロジェクトルートの下の ccnavi ディレクトリの名前で、どちらも外すと
# プロジェクトの層がまるごと消える。実際に試すと `ticket finish <子> --project-home .nothere`
# で、実績リスク 55 (CRITICAL) の子が 25 (MEDIUM) になり、レビュー待ちを飛ばして
# 閉じた（ADR-0067）。中身を差し替えるのと結果が同じなので、同じ制限に載せる。
LAYER_OVERRIDES = (
    ("--rules", "rules", ""),
    ("--phases", "phases", None),
    ("--risk", "risk", None),
    ("--projects", "projects", None),
    ("--project-home", "project_home", ""),
)
# 落としたときの文面。5 本のフラグで同じものを使う。制限が 2 つあるように読ませない。
DIAGNOSIS_ONLY = "ccnavi: {flag} は診断（--test / --lint / --explain）でだけ効く\n"
# `.ccnavi/scripts/` の sh が自分で計算して渡す綴りと、渡されなかったときの値。
# どちらも「いまどこで動いているか」で、エージェントが名乗るものではない。
#
# sh は自分のぶんを先に置き、エージェントの引数を後ろに繋ぐ
# （`exec "$bin" --root "$root" ticket "$@"`）。argparse は同じオプションを後勝ちで読むので、
# 後ろに 1 本足すだけで sh が渡した本物を上書きできた。`--root` は共通層の 3 本も
# `projects` も `approved` もそこから導かれる（`settings.load`）ので、1 本で全部動く。
# 実際に試すと、本物のツリーへシンボリックリンクを張った偽のルートを渡すと、子チケットが
# 本物の置き場に「リスク 0」で閉じられた（ADR-0067）。
#
# 正しい 1 本が先に在ることに頼らず、2 本目が在ること自体を断る。落として先へ進むのでは
# なく止めるのは、この 2 つに「2 度渡す」正しい使い方が無いから。診断の 5 本と違って、
# 使われる経路の話ではない。
WRAPPER_FLAGS = (("--root", "root", None), ("--cwd", "cwd", ""))


def _one_wrapper_flag_each(stderr: TextIO, args: argparse.Namespace) -> bool:
    """sh が渡す綴りが 2 度来ていないかを見て、1 本にまとめる。2 度来ていたら False。

    数えるのは argparse に任せる（`action="append"`）。argv を自分で数えると、
    オプションの位置に無い `--root` という語まで数えてしまう
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
    `.ccnavi/scripts/` の sh で、受け取った引数を実行ファイルへそのまま渡す（ADR-0067）。
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
    """1 回の起動を処理する。

    状態の履歴（history）の経路はここで決まる。既定はエージェントが sh から打つ副命令（`cli`）で、
    人の判断の経路（端末・ボード）と hook は枝の中で差し替える。状態の履歴を書けなかった知らせは、
    起動を抜けるときに標準エラーへ出す（状態の操作は止めない。ADR-0086）。
    """
    with history.session(history.VIA_CLI, stderr):
        return _run(stdin, stdout, stderr, argv)


def _run(stdin: TextIO, stdout: TextIO, stderr: TextIO, argv: list[str]) -> int:
    # 前方一致を受けない。受けると `--close` や `--review` が `--close-early` / `--reviewed` として
    # 走り、全部綴った形しか見ない組み込みの deny（`phase._CLI_FORMS`）に止められない。
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
    # 見せた承認画面の本文・判定が読んだ中身・承認済みチケットに写る中身の指紋
    # （preview の `digest`）。`--yes` と一緒に渡す。
    parser.add_argument("--digest", default="")
    parser.add_argument("--test", nargs=2, metavar=("TOOL", "SUBJECT"), default=None)
    # 見本をぜんぶ判定に掛ける。tools/check_rules.py と VS Code 拡張が呼ぶ。
    parser.add_argument("--test-samples", metavar="FILE", default="")
    parser.add_argument("--explain", action="store_true")
    # 記録のローテートと、古い記録・終わったセッションの控えの削除（prune）。人が端末から打つ。
    parser.add_argument("--prune", action="store_true")
    # 記録からルールの候補を作る（suggest）。読むだけで、何も書かない。
    parser.add_argument("--suggest", action="store_true")
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
    # md の frontmatter の索引を引く（docsearch）。読むのと、無視された索引を書くだけ。
    # 絞り込みと出力の形のフラグは `--docs` でだけ使われる（DOCS_FLAGS）。
    parser.add_argument("--docs", action="store_true")
    parser.add_argument("--type", action="append", default=None)
    parser.add_argument("--tag", action="append", default=None)
    parser.add_argument("--keyword", action="append", default=None)
    parser.add_argument("--path", action="append", default=None)
    parser.add_argument("--text", action="append", default=None)
    parser.add_argument("--since", default=None)
    parser.add_argument("--until", default=None)
    parser.add_argument("--sort", default=None)
    parser.add_argument("-r", "--reverse", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--format", default=None)
    parser.add_argument("--no-refresh", action="store_true")
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
    # 版・組み立ての元のコミット・受け付けるフラグ・互換の版を言う。拡張と sh が起動のときに読む。
    parser.add_argument("--version", action="store_true")
    # この実行で書いた・消したパスを、控えの置き場の下のファイルに 1 行 1 つで書き出す
    # （ADR-0093 の 4.3「fsio の記録層」）。C1（段階 2d）の sh が渡し、
    # 一覧のパスだけをコミットする。
    parser.add_argument("--record-writes", default="")
    # 一覧の基点（C1 の親のワークツリー。段階 2d）。渡すと一覧はこのツリーからの相対になり、
    # 置き場の外に書いたら error（D34 の例外を除く）。
    parser.add_argument("--record-tree", default="")
    # 対話の decide の前半（ADR-0093 の段階 2d のレビューの決定 A）。選択と指紋をこのファイルに
    # 書くだけで、何も置かない（控えの置き場の下だけ）。
    parser.add_argument("--choose-out", default="")
    # 統合先の名前（ADR-0093 の D30。段階 2b）。環境変数は読まず、sh が決めて渡す。
    # いまは `--lint` の識別子の予約（3.1 の 5）が読む。
    parser.add_argument("--integration-branch", default="")
    # レビュー済みの印に入れるアカウント（ADR-0093 の 8.9。段階 4）。ccnavi-review.sh が
    # トークンの持ち主をホストに聞いて渡す（実行ファイルはネットワークに出ない）。
    # `review confirm` と、書く形の decide（`--reviewed N --accept-unresolved`。段階 5）が読む。
    parser.add_argument("--actor", default="")
    # decide の印の経路（`terminal`・`board`。8.9。段階 5）。`--actor` と一緒にだけ受ける
    # （アカウントを引けなかったときはマーカーも状態の履歴も前と同じにするため）。
    parser.add_argument("--via", default="")
    try:
        args = parser.parse_args(_json_out_of_test(argv))
    except SystemExit:
        return EXIT_ERROR
    if args.help:
        stderr.write(USAGE)
        return EXIT_ERROR
    if args.record_tree and not args.record_writes:
        stderr.write("ccnavi: --record-tree は --record-writes と一緒に使う\n")
        return EXIT_ERROR
    if args.actor and not _ACTOR.fullmatch(args.actor):
        stderr.write(
            "ccnavi: --actor にはホストのアカウント名（英数字と _ . - の 1〜100 字）を渡す\n"
        )
        return EXIT_ERROR
    if args.actor and list(args.command[:2]) != ["review", "confirm"] and not _decide_writes(args):
        stderr.write(
            "ccnavi: --actor は review confirm か、書く形の decide"
            "（--reviewed <N> --accept-unresolved）と一緒に使う\n"
        )
        return EXIT_ERROR
    if args.via and (not args.actor or not _decide_writes(args)):
        stderr.write("ccnavi: --via は decide の --actor と一緒に使う\n")
        return EXIT_ERROR
    if args.via and args.via not in (history.VIA_TERMINAL, history.VIA_BOARD):
        stderr.write(f"ccnavi: --via に渡せるのは {history.VIA_TERMINAL} か {history.VIA_BOARD}\n")
        return EXIT_ERROR
    # 経路と食い違う組み合わせは受けない。端末で 1 件ずつ選ぶ形（--yes 無し）はボードの経路でない。
    # --yes はボードの押した選択と、C1 の中の端末の decide（選ぶのを先に済ませた形）の両方が通る
    if args.via == history.VIA_BOARD and not args.yes:
        stderr.write("ccnavi: --via board は --yes（ボードで押した選択）と一緒に使う\n")
        return EXIT_ERROR
    if args.record_writes and not args.version:
        return _recorded_run(stdin, stdout, stderr, parser, args)
    return _parsed(stdin, stdout, stderr, parser, args)


def _recorded_run(
    stdin: TextIO,
    stdout: TextIO,
    stderr: TextIO,
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
) -> int:
    """`--record-writes <ファイル>` つきの 1 回。書いたパスを集め、終わったら書き出す。

    **書き出す先**は、ワークスペースルートの既定の控えの置き場（`logs/state`）の下の `c1/`
    だけ。`--state` と `CCNAVI_STATE` の上書きは見ない（上書きで置き場を動かせば、
    どこへでも書ける経路になる）。行き先・置き場・ルートは行き着く先（リンクを解いた綴り、
    大文字小文字をそろえたもの）で比べ、`c1/` までの途中にリンクがあれば断る。書き出しは
    同じディレクトリの一時ファイルから `os.replace` で置き換えるので、行き先にリンクが
    あってもその先は開かない（リンクの行き先は書き換えない。Windows でも同じ）。

    **一覧の形**は 1 行 1 つ、ワークスペースルートからの相対（区切りは "/"）。ルートの外と、
    別のドライブ（Windows）は絶対パスのまま。既定の控えの置き場の下（リポジトリに入らない）と
    一覧のファイル自身は載せない。上書きした控えの置き場への書き込みは載る（C1 が
    「置き場の外」として止める側）。置き場の外が入っていたら止めるのは C1（段階 2d）で、
    ここは集めて書くだけ。

    **終了コード**: コマンドが落ちても、そこまでに書いたパスで一覧を書く（C1 は落ちた回も
    戻すのに一覧を使う）。一覧を書けなければ、コマンドの結果に依らず 1 で終わる
    （C1 はそれを「書いたものが分からない」として扱う）。行き先が断られたときはコマンドを
    走らせずに 1 で終わる。
    """
    wrapper = argparse.Namespace(**vars(args))
    if not _one_wrapper_flag_each(stderr, wrapper):
        return EXIT_ERROR
    root = wrapper.root if wrapper.root is not None else default_root()
    real_root = _real(root)
    state = os.path.join(real_root, settings.DEFAULT_STATE)
    place = os.path.join(state, "c1")
    given = os.path.abspath(args.record_writes)
    target = os.path.join(_real(os.path.dirname(given)), os.path.basename(given))
    refused = _record_place_problem(place, target, args.record_writes)
    if refused:
        stderr.write(
            f"ccnavi: --record-writes の書き出し先は {place}{os.sep}... の下だけ（{refused}）\n"
        )
        return EXIT_ERROR
    base = _real(os.path.abspath(args.record_tree)) if args.record_tree else real_root
    # 上書きした控えの置き場（`--state`・`CCNAVI_STATE`）も、リポジトリに入らない書き込み
    # （レビューの下書きなど）の置き場なので一覧から外す（ADR-0093 の段階 2d のレビュー）。
    conf_for_state, _ = settings.load(root)
    _override(conf_for_state, args)
    moved_state = _real(conf_for_state.state) if conf_for_state.state else state
    with fsio.recording() as written:
        code = _parsed(stdin, stdout, stderr, parser, args)
    lines = []
    reals = []
    for path in written:
        real = fsio.parent_resolved(path)
        if _inside(real, state) or _inside(real, moved_state) or _same(real, target):
            continue
        rel = real
        if _inside(real, base):
            try:
                rel = os.path.relpath(real, base)
            except ValueError:
                rel = real
        lines.append(rel.replace(os.sep, "/"))
        reals.append(real)
    failed = _write_record(target, "".join(f"{line}\n" for line in lines))
    if failed:
        stderr.write(f"ccnavi: 書いたパスの一覧を {target} に書けない ({failed})\n")
        return EXIT_ERROR
    if args.record_tree:
        outside = _outside_places(root, args, base, reals)
        if outside:
            stderr.write(
                "ccnavi: C1 がコミットするのは状態だけで、置き場の外に書き込みがあった（"
                + ", ".join(outside)
                + "）。コミットしない\n"
            )
            return code if code != EXIT_OK else EXIT_ERROR
    return code


def _outside_places(root: str, args: argparse.Namespace, base: str, reals: list[str]) -> list[str]:
    """一覧のうち、C1 の置き場（承認済み・レビュー待ち）の外のもの（ADR-0093 の 4.3）。

    例外は 1 つだけ（D34）: `ticket start` の中で configsync が写したプロジェクトの層と、
    指す先を直した配点のスクリプト（`configsync.is_synced_write` が内容で読めるもの）。
    """
    conf, _ = settings.load(root)
    _override(conf, args)
    approved = settings.approved_dir(conf, base)
    review_dir = os.path.join(
        base, (conf.tickets or settings.DEFAULT_TICKETS).replace("/", os.sep), "review"
    )
    places = [_real(approved), _real(review_dir)]
    starting = list(args.command[:2]) == ["ticket", "start"]
    found = []
    for real in reals:
        if _inside(real, base) and any(_inside(real, p) for p in places):
            continue
        if starting and _inside(real, base) and configsync.is_synced_write(conf, root, real):
            continue
        found.append(real)
    return found


def _real(path: str) -> str:
    try:
        return os.path.realpath(path)
    except OSError:
        return os.path.normpath(os.path.abspath(path))


def _record_place_problem(place: str, target: str, given: str) -> str:
    """書き出し先が `place` の下でない・途中にリンクがある理由。よければ空文字。"""
    if os.path.lexists(place) and os.path.islink(place):
        return "c1 がシンボリックリンク"
    if not _inside(target, place) or _same(target, place):
        return f"{given} は置き場の外"
    if os.path.islink(target):
        return f"{given} はシンボリックリンク"
    return ""


def _write_record(target: str, text: str) -> str:
    """一覧を書く。一時ファイルに書いて置き換える（行き先のリンクは辿らない）。"""
    directory = os.path.dirname(target)
    try:
        os.makedirs(directory, exist_ok=True)
        # 作った後にも、途中のディレクトリがリンクで外へ出ていないかを見る。
        if _real(directory) != os.path.normpath(directory):
            return "書き出し先のディレクトリがリンクを含む"
        handle, part = tempfile.mkstemp(dir=directory, prefix=".record.", suffix=".part")
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            os.replace(part, target)
        except BaseException:
            with contextlib.suppress(OSError):
                os.remove(part)
            raise
    except OSError as exc:
        return str(exc)
    return ""


def _same(a: str, b: str) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def _inside(path: str, base: str) -> bool:
    """`path` が `base` の下（か同じ）か。

    どちらも行き着く先の綴りで渡す。大文字小文字は区別しない。
    """
    path = os.path.normcase(os.path.normpath(path))
    base = os.path.normcase(os.path.normpath(base))
    return path == base or path.startswith(base.rstrip(os.sep) + os.sep)


def _parsed(
    stdin: TextIO,
    stdout: TextIO,
    stderr: TextIO,
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
) -> int:
    # 設定もワークスペースも読まない。フラグは上の定義から引くので、足したものはそのまま並ぶ。
    if args.version:
        return version.report(stdout, parser, args.json)
    if not _one_wrapper_flag_each(stderr, args):
        return EXIT_ERROR
    # `--docs` に添えたほかの経路のフラグは、黙って無視せずに止める。層の置き場の差し替えは
    # この後で落とされ、`--ticket-control` などは設定に重ねられるので、その前に見る。
    if args.docs:
        refused = _not_with_docs(args)
        if refused:
            stderr.write(f"ccnavi: --docs は {refused} と一緒に使えない\n")
            return EXIT_ERROR

    root = args.root if args.root is not None else default_root()
    conf, problems = settings.load(root)

    # 層の置き場（中身の 3 本と、層を探す先の 2 本）の差し替えは診断の経路でだけ使われる。
    # hook からの判定にも、チケットとレビューの副命令にも差し替えの手段を残すと、
    # 設定を保存せずに緩める経路になるので、そこでは無視する（ADR-0067）。
    # 診断は payload を読まず、判定を実行にも記録にも繋げないので、保存していない設定を
    # 指しても実運用に漏れない。
    diagnosing = (
        args.lint
        or args.test is not None
        or bool(args.test_samples)
        or args.explain
        or args.suggest
    )
    if not diagnosing:
        _drop_outside_diagnosis(stderr, args)

    _override(conf, args)
    # フラグは設定ファイルより強い。書かれた綴りのほうも、そこに合わせて差し替える。
    if args.guard_ticket_approval:
        conf.guard_ticket_approval_declared = args.guard_ticket_approval
    # この切り替えの環境変数は dry-run を取らない（selfguard.GATE_SETTINGS）。取れない語で書かれて
    # いたら、読めない値と同じく enable として扱う。--lint はそれを error にする。
    conf.guard_ticket_approval = selfguard.resolve(
        stderr,
        args.guard_ticket_approval,
        conf.guard_ticket_approval,
        settings.GUARD_TICKET_APPROVAL_ENV,
        selfguard.GATE_SETTINGS,
    )
    # チケット制御も 2 値。読めない値は enable（使う側）として扱う。切ったつもりで
    # 綴りを誤った設定は、判定では有効なままになり、--lint が error で名指しする。
    if args.ticket_control:
        conf.ticket_control_declared = args.ticket_control
    conf.ticket_control = selfguard.resolve(
        stderr,
        args.ticket_control,
        conf.ticket_control,
        settings.TICKET_CONTROL_ENV,
        selfguard.GATE_SETTINGS,
    )

    # ドキュメントの索引を引く経路。payload を読まず、判定も記録もしない。
    # 絞り込みのフラグは `--docs` でだけ読む。ほかの経路で渡されたら止める。このフラグが
    # 無かったころは argparse が知らないフラグとして止めていたので、落として先へ進めると
    # `ticket start X --limit 3` のような打ち間違いが通るようになる（`--flow` は診断の中の
    # 差し替えで、落としても何も動かないので落とすだけにしている）。
    if not args.docs:
        stray = _docs_flags_given(args)
        if stray:
            for flag in stray:
                stderr.write(DOCS_ONLY.format(flag=flag))
            return EXIT_ERROR
    else:
        return _docs(stdout, stderr, conf, root, args)

    # 1 つの層だけを差し替える形。使われる経路は共通層の 3 本と同じ。
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

    conf.integration_branch = args.integration_branch.strip()

    # フローの確かめは `--lint` だけが読む。判定にも採点にも影響しないが、ほかの差し替えと同じく
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
    # 人が端末から打って「このルールは何に当たるのか」を確かめるための場所で、
    # 判定そのものは実運用と同じ関数を通る（REQ-DIA-03）。
    if args.test is not None:
        if args.json:
            return diagnose.test_json(stdout, stderr, conf, root, args.test[0], args.test[1])
        return diagnose.test(stdout, stderr, conf, root, args.test[0], args.test[1])
    if args.test_samples:
        return diagnose.test_samples(stdout, stderr, conf, root, args.test_samples, args.json)
    if args.suggest:
        return suggest.report(stdout, conf, root, args.json)
    if args.explain and args.json:
        return diagnose.explain_json(stdout, stderr, conf, root)
    if args.explain:
        return diagnose.explain(stdout, stderr, conf, root)

    # 後始末の経路。payload を読まない。セッションの開始でも同じものが走る（events）。
    if args.prune:
        return _prune(stdin, stdout, stderr, conf, root, args.preview)

    # 承認の経路。人が端末から打つもので、payload を読まないのでここで分かれる。
    # 判定を 1 度も通らないのも分ける理由で、承認はツール呼び出しについての
    # 判断ではなく、これから判定に使われる範囲についての合意になる。
    if args.approve:
        if not conf.tickets_enabled:
            stderr.write(f"ccnavi: チケット制御が disable（{settings.TICKET_CONTROL_ENV}）\n")
            return EXIT_ERROR
        if args.preview and args.yes:
            stderr.write("ccnavi: --preview と --yes は同時に付けられない\n")
            return EXIT_ERROR
        # 確かめるだけの枝は `--preview` と一緒に使う。単独で打てる形にすると、組み込みの
        # deny（phase.ticket_approval_rule）が免除するのは `--preview` の付いた `--approve`
        # だけなので、エージェントが打てないものを案内することになる。
        if args.verify and not args.preview:
            stderr.write("ccnavi: --verify は --approve --preview と一緒に使う\n")
            return EXIT_ERROR
        # 承認できる状態かを確かめるだけ。置かないのは `--preview` と同じで、違うのは
        # 通るかどうかを終了コードで返すところ（REQ-APV-13）。答えは 0（はい）と
        # 3（いいえ）で、使い方と設定の誤りの 1 とは分ける。
        if args.verify:
            return core.verify(stdout, stderr, conf, root, args.json, list(args.command))
        # 見るだけの経路。承認済みチケットを置かないので端末の確認は要らない。後ろに並べた語は
        # `--approve` と同じで、承認の対象に入れる識別子（ボードの絞り込みで見えている分）。
        if args.preview:
            code = core.preview(stdout, stderr, conf, root, args.json, list(args.command))
            return EXIT_OK if code == 0 else EXIT_ERROR
        # 拡張のオーバーレイで人が押した承認。端末の確認の代わりに、見せた一覧と今の一覧が
        # 同じであることを求める。エージェントがこれを Bash で打つ形は組み込みの
        # deny（phase.ticket_approval_rule）が止める。
        if args.yes:
            history.set_via(history.VIA_BOARD)
            # 後ろに並べた語は preview に渡したのと同じ絞り。`--yes` は見せた識別子。
            code = core.approve_yes(
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
        history.set_via(history.VIA_TERMINAL)
        # `--approve` の後ろに並べた語は、承認の対象に入れる識別子。無ければ承認待ち全部。
        approved = core.approve(stdin, stdout, stderr, conf, root, only=list(args.command))
        return EXIT_OK if approved == 0 else EXIT_ERROR

    # チケットの状態とレビューの操作。payload を読まない。
    # 着手で上書きした設定を、人が端末で見たと残す。
    # レビューの代わりなので、人の判断と同じ扱いにする。
    if args.config_synced:
        if not conf.tickets_enabled:
            stderr.write(f"ccnavi: チケット制御が disable（{settings.TICKET_CONTROL_ENV}）\n")
            return EXIT_ERROR
        if not _from_terminal(stdin, conf, stderr, "--config-synced"):
            return EXIT_ERROR
        history.set_via(history.VIA_TERMINAL)
        code = configsync.acknowledge(stdin, stdout, stderr, conf, root, args.config_synced)
        return EXIT_OK if code == 0 else EXIT_ERROR

    # 取り込みの sh（`ccnavi-sync.sh`）が綴りを聞く経路。読むだけで、チケット制御の有無に依らない。
    if list(args.command) == ["sync", "paths"]:
        return EXIT_OK if sync_paths(stdout, root, conf) == 0 else EXIT_ERROR
    # 取り込みの後の検査（ADR-0093 の 4.2 の 4。段階 2c）。error があれば sh が家族を止める。
    if len(args.command) in (3, 4) and list(args.command[:2]) == ["sync", "check"]:
        repo = args.command[3] if len(args.command) == 4 else None
        code = sync_check(stdout, stderr, root, conf, args.command[2], repo)
        return EXIT_OK if code == 0 else EXIT_ERROR
    # C1（ADR-0093 の 4.3・4.4。段階 2d）の sh が聞くこと。読むだけで、何も書かない。
    if len(args.command) == 3 and list(args.command[:2]) == ["c1", "family"]:
        if not _FAMILY.fullmatch(args.command[2]) or ".." in args.command[2]:
            stderr.write(f"ccnavi: c1 family の {args.command[2]!r} は識別子の形ではない\n")
            return EXIT_ERROR
        return EXIT_OK if c1.family(stdout, conf, root, args.command[2]) == 0 else EXIT_ERROR
    if len(args.command) in (3, 4) and list(args.command[:2]) == ["c1", "sort"]:
        since = args.command[3] if len(args.command) == 4 else ""
        if not _FAMILY.fullmatch(args.command[2]) or ".." in args.command[2]:
            stderr.write(f"ccnavi: c1 sort の {args.command[2]!r} は識別子の形ではない\n")
            return EXIT_ERROR
        if since and not _REVISION.fullmatch(since):
            stderr.write(f"ccnavi: c1 sort の版 {since!r} は読めない\n")
            return EXIT_ERROR
        code = c1.sort(stdout, stderr, conf, root, args.command[2], since)
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
    # この切り替えの環境変数は enable / disable の 2 値。止めずに報告する段は CCNAVI_MODE=dry-run が
    # 持つので、ここに dry-run は無い。読めない値は enable になる。
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
        diaglog.get("ccnavi", root).warn("hook の payload を読めない", mode=mode)
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

    history.set_via(history.VIA_HOOK)
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
    # 診断ログ。受け付けたイベントと最終判定だけ。subject（コマンドの全文）は書かない。
    # 呼び出しごとに走るので DEBUG に置き、既定の INFO では行を増やさない。
    diaglog.get("ccnavi", root).debug(
        "hook を判定した",
        event=record.event,
        tool=record.tool,
        mode=mode,
        decision=record.decision,
        code=record.code,
        exit=code,
    )
    return code


# `--docs` でだけ読むフラグと、渡されなかったときの値。
DOCS_FLAGS = (
    ("--type", "type", None),
    ("--tag", "tag", None),
    ("--keyword", "keyword", None),
    ("--path", "path", None),
    ("--text", "text", None),
    ("--since", "since", None),
    ("--until", "until", None),
    ("--sort", "sort", None),
    ("--reverse", "reverse", False),
    ("--limit", "limit", None),
    ("--format", "format", None),
    ("--no-refresh", "no_refresh", False),
)
DOCS_ONLY = "ccnavi: {flag} は --docs でだけ使える\n"
# `--docs` と一緒に使えないフラグ。ほかの経路と、その経路でだけ読むもの。何も出さずに無視すると、
# 打った人は反映されたと思う。
_NOT_WITH_DOCS = (
    "--lint",
    "--test",
    "--test-samples",
    "--explain",
    "--suggest",
    "--prune",
    "--approve",
    "--reviewed",
    "--close-early",
    "--config-synced",
    "--yes",
    "--preview",
    "--verify",
    "--digest",
    "--result",
    "--tickets",
    "--phase",
    "--body-file",
    "--reason",
    "--accept-unresolved",
    "--chat",
    "--flow",
    "--project-rules-file",
    "--project-phases-file",
    # 判定・チケット・レビューの経路の設定と、sh が渡す綴り。`--docs` は読まない。
    # 層の置き場（`--rules` から `--project-home`）は診断の外では落として先へ進むが、
    # `--docs` で落とすと「そのプロジェクトの置き場で引いた」と読まれるので止める。
    # `--root` は引く場所そのもの、`--log` / `--state` は記録の置き場で `--docs` は何も記録
    # しないので、受けて効かせる（結果は変わらない）。
    "--mode",
    "--rules",
    "--restore-if-deny",
    "--guard-core-files",
    "--guard-ticket-approval",
    "--ticket-control",
    "--approved",
    "--phases",
    "--risk",
    "--projects",
    "--project-home",
    "--cwd",
    "--record-writes",
    "--record-tree",
    "--choose-out",
    "--integration-branch",
    "--actor",
    "--via",
)
# 空文字も「渡した」と数えるフラグ（既定が None で、空に意味があるもの）。
_EMPTY_COUNTS = ("approved", "phases", "risk", "projects")


def _docs_flags_given(args: argparse.Namespace) -> list[str]:
    """渡された `--docs` 用のフラグ。"""
    return [flag for flag, name, absent in DOCS_FLAGS if getattr(args, name) != absent]


def _not_with_docs(args: argparse.Namespace) -> str:
    """`--docs` に添えられた、ほかの経路のフラグ（最初の 1 つ）。無ければ空。"""
    for flag in _NOT_WITH_DOCS:
        name = flag[2:].replace("-", "_")
        value = getattr(args, name)
        # `is` で比べる。`--reviewed 0` の 0 は False と等しいので、`in` だと見落とす。
        if value is None or value is False:
            continue
        if value == "" and name not in _EMPTY_COUNTS:
            continue
        return flag
    return ""


def _docs(
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    args: argparse.Namespace,
) -> int:
    """`--docs`。ワークスペースと、索引の対象になるプロジェクトの md を引く。

    ほかの経路のフラグは `_run` が設定を読む前に断っている（`_not_with_docs`）。
    """
    if args.command:
        stderr.write(
            f"ccnavi: --docs にはフラグ以外の語を付けられない（{' '.join(args.command)}）\n"
        )
        return EXIT_ERROR
    fmt = args.format or ("json" if args.json else "table")
    if args.json and fmt != "json":
        stderr.write("ccnavi: --json と、json 以外の --format は同時に指定できない\n")
        return EXIT_ERROR
    query = docsearch.Query(
        types=args.type or [],
        tags=args.tag or [],
        keywords=args.keyword or [],
        paths=args.path or [],
        texts=args.text or [],
        since=args.since or "",
        until=args.until or "",
        sort=args.sort or "path",
        reverse=args.reverse,
        limit=args.limit or 0,
        format=fmt,
    )
    return docsearch.run(stdout, stderr, conf, root, query, refresh=not args.no_refresh)


def _prune(
    stdin: TextIO,
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    preview: bool,
) -> int:
    """記録と控えの後始末を 1 度走らせ、動かしたものを出す。

    消すのは記録なので、`--preview`（見るだけ）でなければ端末を求める。エージェントが
    Bash から打てると、しきい値の環境変数を 0 に近づけて前に並べるだけで、自分の呼び出しの
    記録を消せる。
    """
    if not preview and not _from_terminal(stdin, conf, stderr, "--prune"):
        return EXIT_ERROR
    report = prune.run(root, conf.log, conf.state, dry_run=preview)
    for line in prune.lines(report, preview):
        stdout.write(line + "\n")
    for problem in report.problems:
        stderr.write(f"ccnavi: {problem}\n")
    return EXIT_OK


def _from_terminal(stdin: TextIO, conf: settings.Settings, stderr: TextIO, flag: str) -> bool:
    """人の判断の経路が、端末の前の人から打たれているか。

    `--approve` と `--reviewed` と `--close-early` は人の合意そのもの。エージェントが
    Bash から打てばその合意を自分で出せる。標準入力が端末であることを求めるのが、この経路が
    hook の中や `echo y |` から来ていないことの、いちばん手間の少ない証拠になる。
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
    # 人の判断を自分で出す方法を教えることになる。切り方は README の設定の表にある。
    stderr.write(
        f"ccnavi: {flag} は端末から打つもの。標準入力が端末ではない。"
        "利用者に端末で打ってもらってください\n"
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
    bypass = _c1_bypass(root, conf, args, cwd)
    if bypass:
        stderr.write(
            f"ccnavi: 家族 {bypass} は取り込み済み（C1 の対象）。状態の操作は "
            f"'{settings.script_command(root, 'ccnavi-ticket.sh')}' か "
            f"'{settings.script_command(root, 'ccnavi-review.sh')}' から打ってください。"
            "C1 を通らない書き込みは親のブランチへ送られないので、何も書かずに止めた"
            "（ADR-0093 の 4.3）\n"
        )
        return EXIT_ERROR
    if args.close_early:
        if not args.result:
            stderr.write("ccnavi: --close-early には --reason <理由> と --result <json> が要る\n")
            return EXIT_ERROR
        # 人の判断。`--approve` / `--reviewed` と同じく端末を求める。
        if not _from_terminal(stdin, conf, stderr, "--close-early"):
            return EXIT_ERROR
        history.set_via(history.VIA_TERMINAL)
        code = review.close_early(stdin, stdout, stderr, root, conf, cwd, args.reason, args.result)
        return EXIT_OK if code == 0 else EXIT_ERROR
    if args.reviewed is not None and args.accept_unresolved and (args.preview or args.yes):
        if args.preview and args.yes:
            stderr.write("ccnavi: --preview と --yes は同時に付けられない\n")
            return EXIT_ERROR
        if not args.json or not args.result:
            stderr.write("ccnavi: ボードの経路は --json と --result <json> を付けて打つ\n")
            return EXIT_ERROR
        history.set_via(history.VIA_BOARD)
        _decide_actor(args)
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
    if args.reviewed is not None and args.choose_out:
        # 対話の decide の前半（利用者の決定 A）。人が端末で選び、選択と指紋を控えの置き場に
        # 書くだけ。置くのは sh が C1 の中で `--yes <選択> --digest <指紋>` で打つ。
        if not _inside(_real(os.path.abspath(args.choose_out)), _real(conf.state)):
            stderr.write("ccnavi: --choose-out の書き出し先は控えの置き場の下だけ\n")
            return EXIT_ERROR
        code = review.choose(
            stdin, stdout, stderr, root, conf, cwd, args.reviewed, args.result, args.choose_out
        )
        return EXIT_OK if code == 0 else EXIT_ERROR
    if args.reviewed is not None:
        history.set_via(history.VIA_TERMINAL)
        _decide_actor(args)
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
            code = core.confirm_local(
                stdout, stderr, root, conf, cwd, args.phase, args.result, args.actor
            )
    elif kind == "review" and verb == "ready":
        if not args.result:
            stderr.write("ccnavi: review ready には --result <json> が要る\n")
        else:
            code = review.ready(stdout, stderr, root, conf, cwd, args.result)
    else:
        stderr.write(USAGE)
    return EXIT_OK if code == 0 else EXIT_ERROR


def _c1_bypass(root: str, conf: settings.Settings, args: argparse.Namespace, cwd) -> str:
    """C1 を通らずに、取り込み済みの家族の状態を書こうとしているなら、その家族。無ければ空。

    状態の操作（`ticket start|finish|cancel`、`review requested|confirm|ready`、残った指摘の
    行き先の `--reviewed N --accept-unresolved`）は、C1 の対象の家族では sh が `--record-tree` を
    付けて起こす。付いていなければ、sh の引数の読み違いや直打ちで C1 を通っていない
    （ADR-0093 の段階 2d のレビュー）。人の判断の操作（`--reviewed --chat`・`--config-synced`・
    `--close-early`）と、書かない形（`--preview`・`--choose-out`・`review prepare`）は見ない。
    """
    if args.record_tree:
        return ""
    words = list(args.command)
    family_id = ""
    if len(words) >= 3 and words[0] == "ticket" and words[1] in ("start", "finish", "cancel"):
        family_id = c1.family_of(words[2])
    elif (
        len(words) >= 2 and words[0] == "review" and words[1] in ("requested", "confirm", "ready")
    ) or (
        args.reviewed is not None
        and args.accept_unresolved
        and not args.preview
        and not args.choose_out
        and not args.chat
    ):
        where = cwd[-1] if isinstance(cwd, list) and cwd else cwd
        parent = phase.parent_for_cwd(root, conf, where) if where else None
        if parent is not None:
            family_id = parent.parent or parent.ticket
    if not family_id or not _FAMILY.fullmatch(family_id):
        return ""
    verdict, _, _ = c1.target(conf, root, family_id)
    return family_id if verdict == c1.TARGET_YES else ""


def sync_paths(stdout: TextIO, root: str, conf: settings.Settings) -> int:
    """`ccnavi-sync.sh` が要る綴りを 1 行 1 項目（`<鍵> <値>`）で返す（ADR-0093 の 4.2・D33）。

    sh は jq を使わず、JSON も読まない。置き場の綴り（ツリーのルートからの相対）と、
    `.claude/settings.local.json` の `env` に書かれた統合先の名前をここが読んで渡す。
    統合先の名前は環境変数からは読まない（sh が先に環境変数を見る。D30）。
    ネットワークにも git にも触らない。
    """
    lines = (
        ("approved", fsio.slashed(conf.approved).strip("/")),
        ("proposals", fsio.slashed(conf.tickets).strip("/")),
        ("home", fsio.slashed(conf.project_home or settings.DEFAULT_PROJECT_HOME).strip("/")),
        ("integration", settings.integration_local(root)),
    )
    for key, value in lines:
        stdout.write(f"{key} {value}\n")
    return 0


def _decide_writes(args) -> bool:
    """書く形の decide か（`--reviewed N --accept-unresolved` の、見るだけ・chat でないもの）。"""
    return (
        args.reviewed is not None
        and args.accept_unresolved
        and not args.preview
        and not args.choose_out
        and not args.chat
    )


def _decide_actor(args) -> None:
    """decide のマーカーと状態の履歴に入れるアカウントと経路（ADR-0093 の 8.9。段階 5）。

    `--actor` があれば履歴の行にアカウントを足し、`--via` があれば経路を差し替える。印の
    `actor`・`via` は `review.apply_decision` がこの起動の値から書く。
    無ければマーカーも状態の履歴も前と同じ。
    """
    if not args.actor:
        return
    history.set_actor(args.actor)
    if args.via:
        history.set_via(args.via)


# 親の識別子の形（ticket._ID と同じ）。sh から渡る引数なので、パスとして読まれる綴りを入れない。
_FAMILY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
# `--actor` の形。拡張がホストから読むアカウント名の形（`github.ts` の NAME）と同じ。
_ACTOR = re.compile(r"[A-Za-z0-9_.-]{1,100}")
# `c1 sort` の版。sha か `refs/remotes/origin/<親>` の形だけ（git の引数として読まれる綴りを
# 入れない）。
_REVISION = re.compile(r"[0-9a-f]{7,64}|refs/remotes/origin/[A-Za-z0-9][A-Za-z0-9._-]*")


# `sync check` の答えの頭の行。sh はこれが無ければ「検査を実行できなかった」（古い実行ファイルが
# 知らない副命令を断った、など）と読み、家族を止めない（検査の error と分ける）。
SYNC_CHECK_HEAD = "check 1"


def sync_check(
    stdout: TextIO,
    stderr: TextIO,
    root: str,
    conf: settings.Settings,
    family: str,
    repo: str | None = None,
) -> int:
    """`ccnavi-sync.sh` が取り込みの後に打つ検査。頭に `check 1`、続けて 1 行 1 件。

    error が 1 つでもあれば 1。sh は最初の error の中身を家族の控えの `reason` に書いて
    `blocked` にする（ADR-0093 の 4.2 の 4）。`repo` は控えの名前（`self` かプロジェクト名）。
    ネットワークにも git にも触らない。
    """
    for name, value in (("識別子", family), ("リポジトリ", repo or "self")):
        if not _FAMILY.fullmatch(value) or ".." in value:
            stderr.write(f"ccnavi: sync check の{name} {value!r} は識別子の形ではない\n")
            return 1
    problems = lint.family_check(conf, root, family, repo)
    stdout.write(SYNC_CHECK_HEAD + "\n")
    for p in problems:
        detail = " ".join(p.detail.split())
        stdout.write(f"{p.severity} {detail}\n")
    return 1 if any(p.severity == lint.SEVERITY_ERROR for p in problems) else 0


def _record(stderr: TextIO, log: audit.Log, record: audit.Record) -> None:
    """1 行を書く。書けなかったことは報告して捨てる。
    書けなかったことでツールの実行可否を変えてはいけない。"""
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
