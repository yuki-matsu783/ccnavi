"""標準入出力とコマンドラインを 1 つの判定に繋ぐ。

run は終了コードを返す。終了を呼び手に任せるので、道具ぜんぶを別プロセス無しで
テストから動かせる。

ここに置くのは引数の解釈と振り分けだけ。hook のイベントごとの手順は events、
実行前チェックは judge、判定につける文面は reasons、モードと終了コードは modes に
ある。チケットとレビューの操作（`ticket ...` / `review ...`）は `cli_ops.operate` が
ops / review へ渡す。`--help` の本文は `cli_usage`、オプションの読み分けは `cli_args` に分けてある。
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import io
import os
import tempfile
import time
import traceback
from typing import TextIO

from ..hook import events, failsafe, judge, reasons, subagent
from ..infra import fsio, hookio, modes, pathcase, settings, tree
from ..infra.modes import EXIT_BLOCK, EXIT_ERROR, EXIT_OK
from ..policy import selfguard
from ..records import audit, diaglog
from ..tickets.approval import store as approval
from ..tickets.approval import write_and_push
from ..tickets.core import api as core
from ..tickets.lifecycle import branchfind, write_and_push_merge
from ..tickets.model import user_only, via
from ..tickets.review import request as review
from . import (
    cli_args,
    cli_handoff,
    cli_idle,
    cli_learnings,
    cli_mirror,
    cli_ops,
    cli_plan,
    cli_stopreview,
    cli_templates,
    cli_text,
    cli_usage,
    diagnose,
    lint,
    sh_ask,
    suggest,
    version,
)


def _json_out_of_test(argv: list[str]) -> list[str]:
    """`--test` が取る 2 語に混ざった `--json` を外し、末尾へ回す。

    `--test` は TOOL と SUBJECT の 2 語を取るので、`--test --json Bash ls` と書くと `--json` を
    TOOL として取る。`--json` はツールの名前にも調べるコマンドにもならない語なので、
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


def _take_test_words(argv: list[str]) -> tuple[list[str], list[str] | None]:
    """`--test` と続く 2 語を argparse に渡す前に抜き出す。

    返すのは残りの語と、抜いた 2 語（無ければ None）。

    `nargs=2` は版で扱いが違い、3.12 は 2 語の中のオプションの名前
    （`--test Bash --permission-mode auto ls`）を語として取らずに argparse の誤りで抜けてしまい、
    `_test_usage` の案内まで届かない。
    続く 2 語をそのまま取れば、版に依らず同じ形で `_test_usage` が読める。2 語に満たないときと
    `--` の後ろにあるときは、そのまま argparse に任せる。
    """
    end = argv.index("--") if "--" in argv else len(argv)
    if "--test" not in argv[:end]:
        return argv, None
    at = argv.index("--test")
    words = argv[at + 1 : at + 3]
    if len(words) < 2:
        return argv, None
    return [*argv[:at], *argv[at + 3 :]], words


def _help_of_test(argv: list[str]) -> list[str]:
    """`--test ... --help` を、`--help test`（test の節）の形にする。

    `--test` は続く 2 語を取るので、`--test --help` は語が足りず argparse の誤りで落ち、
    `--help` に届かない。`--help` があるときだけ、`--test` と続く語（オプションでないもの）を外して
    話題の語 `test` に置き換える。
    """
    end = argv.index("--") if "--" in argv else len(argv)
    if "--test" not in argv[:end] or not {"-h", "--help"} & set(argv[:end]):
        return argv
    at = argv.index("--test")
    words = []
    for word in argv[at + 1 : at + 3]:
        if word.startswith("-"):
            break
        words.append(word)
    return [*argv[:at], *argv[at + 1 + len(words) :], "test"]


def run(stdin: TextIO, stdout: TextIO, stderr: TextIO, argv: list[str]) -> int:
    """1 回の起動を処理する。

    マーカーに書く経路（via）はここで決まる。既定はエージェントが sh から打つサブコマンド
    （`cli`）で、ユーザの判断の経路（ターミナル・ボード）と hook は枝の中で差し替える。
    """
    with via.session(via.VIA_CLI):
        try:
            return _run(stdin, stdout, stderr, argv)
        except approval.NoParentWorktree as exc:
            # 書く側（承認・着手・完了・レビュー）の置き場が決まらない。親のワークツリーの
            # ほかのコピーへは書かないので止める。hook の経路は自前の受け皿（`_hook`）で
            # 終わるので、ここへは来ない。
            stderr.write(
                f"ccnavi: 親のワークツリーが決まらないので止めた: {exc}。"
                "親のワークツリーを準備してから（ccnavi-start.sh）打ち直してください\n"
            )
            return EXIT_ERROR


def _test_usage(parser: argparse.ArgumentParser, args: argparse.Namespace) -> str:
    """`--test` と、その payload の欄の組み合わせの誤り。正しければ空。

    どれも通知なく無視せずに断る。無視すると、試したつもりの呼び出しと違う答えを読む。

    - `--permission-mode` / `--agent-id` は `--test` の payload の欄で、ほかの経路
      （`--lint`・`--explain`・`--docs` など）には読む先が無い
    - `--test` は TOOL と SUBJECT の 2 語を取る。その間にオプションを置くと（`--test Bash
      --permission-mode auto '<cmd>'`）、オプションの名前が SUBJECT になり、後ろの語は余る
    - 余った語（`--test Bash ls extra` の `extra`）
    """
    if args.test is None:
        if args.permission_mode or args.agent_id:
            flag = "--permission-mode" if args.permission_mode else "--agent-id"
            return f"{flag} は --test でだけ指定できる"
        return ""
    known = parser._option_string_actions
    for word in args.test:
        if word.partition("=")[0] in known:
            return (
                f"--test の TOOL と SUBJECT にオプション（{word}）が入った。"
                "オプションは --test <TOOL> <SUBJECT> の前か後ろに置く"
            )
    if args.command:
        return f"--test は TOOL と SUBJECT の 2 語だけを取る（余った語: {' '.join(args.command)}）"
    return ""


def _plan_order_usage(args: argparse.Namespace, reordering: bool) -> str:
    """`--plan-order` の組み合わせの誤り。正しければ空。"""
    if args.agree:
        return "--plan-order は --agree と同時に指定できない（承認の外の補助）"
    if args.plan_order is None:
        if args.fill_phases:
            return "--fill-phases は --plan-order <親> と一緒に使う"
        return "--order・--write・--expect は --plan-order <親> と一緒に使う"
    if args.fill_phases:
        if reordering or args.json:
            return "--fill-phases は --json・--order・--write・--expect と同時に指定できない"
        return ""
    if args.write:
        if args.order is None:
            return "--write は --order <JSON>（元の番号での直接の先行）と一緒に使う"
        if args.expect is None:
            return (
                "--write は --expect <ハッシュ>（--plan-order <親> --json が返す source_sha）と"
                "一緒に使う"
            )
        return ""
    if args.expect is not None:
        return "--expect は --write と一緒に使う"
    if not args.json:
        return (
            "--plan-order <親> は --json（読むだけ。--order で振り直した答え）か、"
            "--order <JSON> --expect <ハッシュ> --write（書き戻す）か、"
            "--fill-phases（計画が使う定義だけを phases.yml から親の提案の phases: にコピーする）と"
            "一緒に使う"
        )
    return ""


def _run(stdin: TextIO, stdout: TextIO, stderr: TextIO, argv: list[str]) -> int:
    """引数を読み、組み合わせを検査してから、実際の処理へ渡す。

    処理フロー:
    1. `user_only.take_entry` で入口の印（`--entry`）を外し、`user_only.rewrite` で、
       ユーザ専用の入口（`ccnavi user <操作>`）を今の形に読み替える。印の無い入口は、
       `_parsed` が設定を読んでから断る（テストの逃げ道を設定で見るため）。
    2. argparse のパーサを組み、`_take_test_words` などで `--test` の引数を整えて読む。
    3. `--help` なら `cli_args.help_text` を出して終わる。
    4. ユーザ専用のオプションを、`user` の入口の外では `user_only.refused` で断る。
    5. 組み合わせを検査する（`--all`、`_test_usage`、`--yes`、`--record-tree`、
       `--actor`、`--via`）。
    6. `--record-writes` があり `--version` でなければ `_recorded_run`、それ以外は `_parsed` へ
       渡し、その終了コードを返す。

    終了コードは、読み替えや検査で断ったときは `EXIT_BLOCK` か `EXIT_ERROR`、`--help` と
    引数の解析の失敗は `EXIT_ERROR`、それ以外は `_parsed` か `_recorded_run` が返す値。
    """
    # ユーザ専用の入口（`ccnavi user <操作>`）を、今の内部の形に読み替える。入口から来たかどうかは
    # argparse の結果（args）には入れない。オプションで偽装できないようにするため。
    try:
        argv, marker = user_only.take_entry(argv)
        argv, entry = user_only.rewrite(argv)
    except user_only.UserEntryError as exc:
        stderr.write(f"ccnavi: {exc}\n")
        return EXIT_BLOCK
    # 前方一致を受けない。受けると `--close` や `--review` が `--close-early` / `--reviewed` として
    # 走り、省略せずに書いた形しか見ない組み込みの deny（`phase_forms._CLI_FORMS`）に止められない。
    parser = argparse.ArgumentParser(prog="ccnavi", add_help=False, allow_abbrev=False)
    # パスは sh が計算して渡す。2 度来ていないかを見るので、リストで受ける（WRAPPER_FLAGS）。
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
    # リモートから取得した結果（JSON）。.ccnavi/scripts/ccnavi-review.sh が取ってきて渡す。
    parser.add_argument("--result", default="")
    parser.add_argument("--lint", action="store_true")
    parser.add_argument("--agree", action="store_true")
    # 承認の対象の一覧を見るだけ（承認済みチケットを置かない）。
    # VS Code の拡張がオーバーレイに出すために打つ。
    parser.add_argument("--preview", action="store_true")
    # 承認できる状態かを確かめるだけ（`--preview` と一緒に使う）。
    # 通るかどうかを終了コードで返す。エージェントがユーザに承認を頼む前に打つ。
    parser.add_argument("--verify", action="store_true")
    # 親の提案の計画を書き換える補助（`--agree` の外の独立したフラグ。エージェントも打ってよい）。
    # `--json`（読むだけ。`--order` で振り直した答え）、`--order <JSON> --expect <ハッシュ> --write`
    # （振り直した順序を提案へ書き戻す）、`--fill-phases`（計画が使う定義だけを phases.yml から
    # `phases:` にコピーする）。
    parser.add_argument("--plan-order", default=None, metavar="PARENT")
    parser.add_argument("--fill-phases", action="store_true")
    parser.add_argument("--order", default=None, metavar="JSON")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--expect", default=None, metavar="SHA")
    # 見せた一覧の識別子（カンマ区切り）。拡張のオーバーレイでユーザが押した承認。
    # ターミナルは要らない。
    # 値なしの `--yes` は `user handoff` と `user mirror` だけが読む（`_run` が、
    # ほかの経路では断る）。
    parser.add_argument("--yes", nargs="?", const=cli_args.BARE_YES, default="")
    # 見せた提案の識別子の一覧と各提案ファイルのバイト列のダイジェスト（`--agree --yes` は
    # preview の `digest`、`--reviewed --yes` は指摘のダイジェスト）。`--yes` と一緒に渡す。
    parser.add_argument("--digest", default="")
    # ターミナルの `--agree` が実際に書けたパスの一覧を JSON で書き出すファイル。画面（標準出力）は
    # ユーザの確認に使うので別にした。`ccnavi-user-agree.sh` が読み、書けた分と
    # 書けなかった分を言う。
    parser.add_argument("--placed-out", default="", metavar="FILE")
    parser.add_argument("--test", nargs=2, metavar=("TOOL", "SUBJECT"), default=None)
    # `--test` の payload に載せる Claude Code の権限モードとサブエージェントの印。hook は
    # この 2 つを Claude Code から受け取り、判定がそれで変わる。呼び出しが来たディレクトリは
    # `--cwd`（sh が渡すものと同じフラグ）で渡す。
    parser.add_argument("--permission-mode", default="")
    parser.add_argument("--agent-id", default="")
    # 期待する判定をぜんぶ判定に掛ける。scripts/check_rules.py と VS Code 拡張が呼ぶ。
    parser.add_argument("--test-expectations", metavar="FILE", default="")
    parser.add_argument("--explain", action="store_true")
    # 記録のローテートと、古い記録・終わったセッションの記録の削除（prune）。
    # ユーザがターミナルから打つ。
    parser.add_argument("--prune", action="store_true")
    # 記録からルールの候補を作る（suggest）。読むだけで、何も書かない。
    parser.add_argument("--suggest", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--tickets", default="")
    parser.add_argument("--approved", default=None)
    parser.add_argument("--phases", default=None)
    parser.add_argument("--risk", default=None)
    # プロジェクトの置き場と、ccnavi ディレクトリ。
    parser.add_argument("--projects", default=None)
    parser.add_argument("--project-home", default="")
    # 1 つのプロジェクトのルールファイルを名前で差し替える（<名前>=<パス>）。診断だけ。
    # VS Code 拡張が編集中のプロジェクトのルールを保存せずに試すために渡す。
    parser.add_argument("--project-rules-file", default="")
    # 同じ差し替えをレイヤーのフェーズ定義に対して行う（<名前>=<パス>、
    # 名前は self かプロジェクト）。
    # VS Code 拡張のフェーズ管理画面が、編集中のレイヤーの定義を保存せずに検証するために渡す。
    parser.add_argument("--project-phases-file", default="")
    # 同じ差し替えをレイヤーのリスクの配点に対して行う（<名前>=<パス>、
    # 名前は self かプロジェクト）。
    # VS Code 拡張のリスク管理画面が、編集中のレイヤーの配点を保存せずに、
    # 共通レイヤーと合わせて検証するために渡す。
    parser.add_argument("--project-risk-file", default="")
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
    # 学びの蓄積の wiki のパターンごとの状態と観測元の数の一覧。読むだけ（`--json` で機械可読）。
    parser.add_argument("--learnings", action="store_true")
    # 作業・レビュー計画のテンプレートの一覧・検索（`--keyword`）・表示（`--show`）。読むだけ。
    # `--layer` は統合結果の相手のレイヤー（self かプロジェクト名。既定は self）。
    parser.add_argument("--templates", action="store_true")
    parser.add_argument("--show", default=None, metavar="NAME")
    parser.add_argument("--layer", default="")
    # 任意のパスのテンプレートを検査する（`--templates --check-file <パス>`）。読むだけ。
    # 保存の前の検査に使う。
    parser.add_argument("--check-file", default=None, metavar="PATH")
    # テンプレートの下書き。`--draft <親>` は完了した親から下書きを `wip/handoff/` に作る。効力が
    # 無いのでエージェントが打ってよい。`--name` は下書きの名前、`--to common|project` と
    # `--layer` は取り込み先。取り込みは `user handoff`（テンプレート専用の取り込みは無い）。
    parser.add_argument("--draft", default=None, metavar="PARENT")
    parser.add_argument("--name", default="")
    parser.add_argument("--to", default="")
    # 保護対象の下書きの取り込み（`ccnavi user handoff [<パス>…]`）。ユーザ専用で、`user` の入口が
    # 読み替える。パスは位置引数（`command`）で受ける。`--yes` と `--expect` が揃ったときだけ書く。
    parser.add_argument("--handoff", action="store_true")
    # 共通レイヤーのミラーを揃える（`ccnavi user mirror [<プロジェクト名>…]`）。ユーザ専用で、
    # `user` の入口が読み替える。名前は位置引数（`command`）で受ける。`--yes` と `--expect` が
    # 揃ったときだけ書く。
    parser.add_argument("--mirror", action="store_true")
    # チケットの状態とレビューの操作。ユーザか、親が保護済みスクリプトから呼ぶ。
    parser.add_argument("command", nargs="*")
    parser.add_argument("--cwd", action="append", default=None)
    parser.add_argument("--phase", type=int, default=None)
    # `ticket approval-request <識別子> --check`。送れる状態かを確かめるだけで、何も載せない。
    parser.add_argument("--check", action="store_true")
    # `worktree retire --abandon <親>`。放棄した親の片付けの記録を消す（ユーザがターミナルで打つ）。
    parser.add_argument("--abandon", default=None, metavar="PARENT")
    parser.add_argument("--body-file", default="")
    parser.add_argument("--reason", default="")
    # `ticket new` の引数。タイトル・計画の型（カンマ区切り）・issue の番号。
    parser.add_argument("--title", default="")
    parser.add_argument("--plan", default="")
    parser.add_argument("--issue", default=None)
    # 親のブランチのマージリクエストの番号（`ccnavi-start.sh` が出す）。親の提案の `mr:` に書く。
    parser.add_argument("--mr", type=int, default=None)
    # `ticket new --scope <glob>`（繰り返せる）。今回の作業の範囲。`inherit` の型の許可を作る。
    parser.add_argument("--scope", action="append", default=[])
    # `ticket new <識別子> --template <名前> [--layer <名前>]`。計画と子のフローの下書きを
    # テンプレートから作る（`--plan` の代わり。`--templates`（一覧）とは別のオプション）。
    parser.add_argument("--template", default="")
    # `ticket new --template` が、テンプレートのフィードバック計画も書く（既定は書かない）。
    parser.add_argument("--with-feedback", action="store_true")
    # `ticket revise <識別子> --feedback`。`feedback: []`（対応なし）の改版の雛形を作る。
    parser.add_argument("--feedback", action="store_true")
    # `ticket learn [<識別子>] [--note <本文>] [--target <種類>] [--signal <信号>]`。
    parser.add_argument("--note", default="")
    parser.add_argument("--target", default="")
    parser.add_argument("--signal", default="")
    parser.add_argument("--reviewed", type=int, default=None)
    parser.add_argument("--accept-unresolved", action="store_true")
    # ユーザがターミナルで打つ、親を早めに閉じる操作。`--agree` / `--reviewed` と同じく、
    # ユーザの判断はフラグで受ける。
    parser.add_argument("--close-early", action="store_true")
    parser.add_argument("-h", "--help", action="store_true")
    # `--help --all` が全文を出す（既定は話題の一覧）。
    parser.add_argument("--all", action="store_true")
    # 版・組み立ての元のコミット・受け付けるフラグ・互換の版を言う。拡張と sh が起動のときに読む。
    parser.add_argument("--version", action="store_true")
    # この実行で書いた・消したパスを、state の置き場の下のファイルに 1 行 1 つで書き出す
    # （fsio の記録層が記録する）。write-and-push の sh が渡し、一覧のパスだけをコミットする。
    parser.add_argument("--record-writes", default="")
    # 一覧の基点（write-and-push の親のワークツリー）。渡すと一覧はこのツリーからの相対になり、
    # 置き場の外に書いたら error
    parser.add_argument("--record-tree", default="")
    # 対話の decide の前半。選択とダイジェストをこのファイルに書くだけで、
    # 何も置かない（state の置き場の
    # 下だけ）。ユーザが選ぶのを write-and-push のロックの外で済ませ、
    # ロックを持ったままユーザを待たないため。
    parser.add_argument("--choose-out", default="")
    # 受け入れの計画。decide の `--yes` と `--close-early` で、受け入れる指摘と返信の本文を
    # このファイルに書くだけで、何も置かない（state の置き場の下だけ）。sh がこれで返信と解決を
    # 済ませてから、置く呼び出しを打つ。置くときは `--accept-done` が計画を読む。
    parser.add_argument("--accept-plan", default="")
    parser.add_argument("--accept-done", default="")
    # `--close-early` の 2 段目の前に、計画のあとで未解決の指摘が変わっていないかだけを確かめる
    # （何も書かない）。`--no-issue` は返信の文言のため（残りを issue に書き出さない）。
    parser.add_argument("--accept-check", default="")
    parser.add_argument("--no-issue", action="store_true")
    # レビュー済みのマーカーに入れるアカウント（押したユーザではなくトークンの持ち主）。
    # ccnavi-review.sh がトークンの持ち主をホストに聞いて渡す。実行ファイルは
    # ネットワークに出ない。
    # 読むのは `review confirm` と、書く形の decide（`--reviewed N --accept-unresolved`）。
    parser.add_argument("--actor", default="")
    # decide が書くレビュー済みのマーカーの経路（`via`）で、`terminal` か `board` を渡す。
    # `--actor` があるときだけ受け付ける。アカウントを引けなかったときに、
    # マーカーを前と同じ中身にするため。
    # `user handoff --yes` の board 経路（`--expect` と `--json` と組む）でも受け付ける。
    parser.add_argument("--via", default="")
    try:
        rest, test_words = _take_test_words(_json_out_of_test(_help_of_test(argv)))
        args = parser.parse_args(rest)
    except SystemExit:
        return EXIT_ERROR
    if test_words is not None and args.test is None:
        args.test = test_words
    if args.help:
        # 話題の一覧か節だけを出す。全文は `--all` で、読む量が多い。ヘルプは何も動かさないので、
        # ユーザ専用のオプションと一緒でも（`--help --prune`）断らずに節を出す。
        stderr.write(cli_args.help_text(args))
        return EXIT_ERROR
    # ユーザ専用のオプションは、`user` の入口の外では断る。見るだけの形
    # （`--agree --preview` など）は通す。
    if entry is None:
        refused_here = user_only.refused(args)
        if refused_here:
            stderr.write(f"ccnavi: {refused_here}\n")
            return EXIT_BLOCK
    if args.all:
        stderr.write("ccnavi: --all は --help と一緒にだけ指定できます（全文を出す）。\n")
        return EXIT_ERROR
    refused = _test_usage(parser, args)
    if refused:
        stderr.write(f"ccnavi: {refused}\n")
        return EXIT_ERROR
    if args.yes == cli_args.BARE_YES and not (args.handoff or args.mirror):
        stderr.write(
            "ccnavi: --yes には識別子を渡す（値なしの --yes は user handoff と user mirror だけ）\n"
        )
        return EXIT_ERROR
    if args.record_tree and not args.record_writes:
        stderr.write("ccnavi: --record-tree は --record-writes と一緒に指定してください。\n")
        return EXIT_ERROR
    if args.actor and not cli_ops._ACTOR.fullmatch(args.actor):
        stderr.write(
            "ccnavi: --actor にはホストのアカウント名（英数字と _ . - の 1〜100 字）を渡す\n"
        )
        return EXIT_ERROR
    if (
        args.actor
        and list(args.command[:2]) != ["review", "confirm"]
        and not cli_ops._decide_writes(args)
    ):
        stderr.write(
            "ccnavi: --actor は review confirm か、反映する形の decide"
            "（--reviewed <N> --accept-unresolved）と一緒に指定してください。\n"
        )
        return EXIT_ERROR
    # 取り込みを書く形（`user handoff --yes`）は、拡張の子プロセスから board で呼ぶ。`--via` の
    # 組み合わせは取り込みの入口（cli_handoff.usage_problem）が見る（`--json` なら JSON で答える）。
    # ミラーを揃える形（`user mirror --yes`）も同じ作り（cli_mirror.usage_problem）。
    if args.handoff or args.mirror:
        pass
    elif args.via and (not args.actor or not cli_ops._decide_writes(args)):
        stderr.write("ccnavi: --via は decide の --actor と一緒に指定してください。\n")
        return EXIT_ERROR
    if args.via and args.via not in (via.VIA_TERMINAL, via.VIA_BOARD):
        stderr.write(f"ccnavi: --via に渡せるのは {via.VIA_TERMINAL} か {via.VIA_BOARD}\n")
        return EXIT_ERROR
    # 経路と食い違う組み合わせは受けない。
    # ターミナルで 1 件ずつ選ぶ形（--yes 無し）はボードの経路でない。
    # --yes はボードの押した選択と、
    # write-and-push の中のターミナルの decide（選ぶのを先に済ませた形）
    # の両方が通る
    if args.via == via.VIA_BOARD and not args.yes and not (args.handoff or args.mirror):
        stderr.write(
            "ccnavi: --via board は --yes（ボードで押した選択）と一緒に指定してください。\n"
        )
        return EXIT_ERROR
    unmarked = entry if entry is not None and not marker else None
    if args.record_writes and not args.version:
        return _recorded_run(stdin, stdout, stderr, parser, args, unmarked)
    return _parsed(stdin, stdout, stderr, parser, args, unmarked)


def _recorded_run(
    stdin: TextIO,
    stdout: TextIO,
    stderr: TextIO,
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    unmarked: user_only.Verb | None = None,
) -> int:
    """`--record-writes <ファイル>` つきの 1 回。書いたパスを集め、終わったら書き出す。

    書き出す先は、ワークスペースルートの既定の state の置き場（`logs/state`）の下の
    `write-and-push/`
    だけ。`--state` の上書きは見ない（上書きで置き場を動かせば、
    どこへでも書ける経路になる）。行き先・置き場・ルートは行き着く先（リンクを解いたパス、
    大文字小文字をそろえたもの）で比べ、`write-and-push/` までの途中にリンクがあれば断る。書き出しは
    同じディレクトリの一時ファイルから `os.replace` で置き換えるので、行き先にリンクが
    あってもその先は開かない。

    一覧の形は 1 行 1 つ、ワークスペースルートからの相対（区切りは "/"）。ルートの外と、
    別のドライブ（Windows）は絶対パスのまま。既定の state の置き場の下（リポジトリに入らない）と
    一覧のファイル自身は載せない。上書きした state の置き場への書き込みは載る（write-and-push が
    「置き場の外」として止める側）。置き場の外が入っていたら止めるのは write-and-push で、
    ここは集めて書くだけ。

    終了コード: コマンドが失敗しても、そこまでに書いたパスで一覧を書く（write-and-push
    は失敗した回も
    戻すのに一覧を使う）。一覧を書けなければ、コマンドの結果に依らず 1 で終わる
    （write-and-push はそれを「書いたものが分からない」として扱う）。
    行き先が断られたときはコマンドを
    走らせずに 1 で終わる。
    """
    wrapper = argparse.Namespace(**vars(args))
    if not cli_args._one_wrapper_flag_each(stderr, wrapper):
        return EXIT_ERROR
    root = wrapper.root if wrapper.root is not None else default_root()
    real_root = cli_args._real(root)
    state = os.path.join(real_root, settings.DEFAULT_STATE)
    place = os.path.join(state, "write-and-push")
    given = os.path.abspath(args.record_writes)
    target = os.path.join(cli_args._real(os.path.dirname(given)), os.path.basename(given))
    refused = _record_place_problem(place, target, args.record_writes)
    if refused:
        stderr.write(
            f"ccnavi: --record-writes の書き出し先は {place}{os.sep}... の下だけ（{refused}）\n"
        )
        return EXIT_ERROR
    # `--placed-out` の結果のファイルも、一覧のファイル自身と同じく載せない（承認が書いた
    # 置き場ではなく、呼び手が受け取る控え。載ると sh が相対パスでない行を送ろうとして止まる）
    placed_given = os.path.abspath(args.placed_out) if args.placed_out else ""
    placed_target = (
        os.path.join(cli_args._real(os.path.dirname(placed_given)), os.path.basename(placed_given))
        if placed_given
        else ""
    )
    base = cli_args._real(os.path.abspath(args.record_tree)) if args.record_tree else real_root
    # 上書きした state の置き場（`--state`）も、リポジトリに入らない書き込み
    # （レビューの下書きなど）の置き場なので一覧から外す。`--state` が効くのは診断の経路だけで、
    # それ以外では無視されるので、ここでも見ない（見ると、一覧から外したい場所を指せてしまう）。
    moved_state = state
    if cli_args._diagnosing(args):
        conf_for_state, _ = settings.load(root)
        cli_args._override(conf_for_state, args)
        if conf_for_state.state:
            moved_state = cli_args._real(conf_for_state.state)
    with fsio.recording() as written:
        code = _parsed(stdin, stdout, stderr, parser, args, unmarked)
    lines = []
    reals = []
    for path in written:
        real = fsio.parent_resolved(path)
        if (
            cli_args._inside(real, state)
            or cli_args._inside(real, moved_state)
            or cli_args._same(real, target)
            or (placed_target and cli_args._same(real, placed_target))
        ):
            continue
        rel = real
        if cli_args._inside(real, base):
            try:
                rel = os.path.relpath(real, base)
            except ValueError:
                rel = real
        lines.append(rel.replace(os.sep, "/"))
        reals.append(real)
    failed = _write_record(target, "".join(f"{line}\n" for line in lines))
    if failed:
        stderr.write(f"ccnavi: 反映したパスの一覧を {target} に保存できませんでした ({failed})\n")
        return EXIT_ERROR
    if args.record_tree:
        outside = _outside_places(root, args, base, reals)
        if outside:
            stderr.write(
                "ccnavi: write-and-push がコミットするのは状態だけで、"
                "置き場の外に書き込みがあった（" + ", ".join(outside) + "）。コミットしない\n"
            )
            return code if code != EXIT_OK else EXIT_ERROR
    return code


def _outside_places(root: str, args: argparse.Namespace, base: str, reals: list[str]) -> list[str]:
    """一覧のうち、write-and-push の置き場（承認済み・レビュー待ち）の外のもの。

    write-and-push がコミットするのは状態だけ。

    例外は `review ready` が消した途中の作業の置き場（`wip/`）のファイル（消したもの、
    つまり今は無いものだけ。`review.remove_wip` が追跡済みのものだけを消す）。
    """
    conf, _ = settings.load(root)
    cli_args._override(conf, args)
    approved = settings.approved_dir(conf, base)
    review_dir = os.path.join(
        base, (conf.tickets or settings.DEFAULT_TICKETS).replace("/", os.sep), "review"
    )
    places = [cli_args._real(approved), cli_args._real(review_dir)]
    if list(args.command[:2]) == ["ticket", "approval-request"]:
        # 提案を送る操作だけは、承認待ちの提案（todo/）と子のフローの下書き（flows/）も
        # 置き場に数える。
        tickets_dir = (conf.tickets or settings.DEFAULT_TICKETS).replace("/", os.sep)
        places += [
            cli_args._real(os.path.join(base, tickets_dir, name)) for name in ("todo", "flows")
        ]
    readying = list(args.command[:2]) == ["review", "ready"]
    found = []
    for real in reals:
        if cli_args._inside(real, base) and any(cli_args._inside(real, p) for p in places):
            continue
        if readying and _ready_removed_wip(real, base):
            continue
        found.append(real)
    return found


def _ready_removed_wip(real: str, base: str) -> bool:
    """`review ready` が消した途中の作業の置き場（`wip/`）の下のファイルか。

    `ready` が消すものを選ぶのと同じ判定（`review.in_wip`。大文字小文字と `\\` の区切りを
    問わない）を、write-and-push のツリー（`base`）からの相対に適用する。通すのは、
    そのツリーの中で、
    今は無い
    （消した）ものだけ。ツリーの外・`..` を含む相対・今も在るものは通さない。
    """
    if os.path.lexists(real) or not cli_args._inside(real, base) or cli_args._same(real, base):
        return False
    try:
        rel = os.path.relpath(real, base)
    except ValueError:
        return False
    parts = rel.split(os.sep)
    if any(p in ("", ".", "..") for p in parts):
        return False
    return review.in_wip("/".join(parts))


def _record_place_problem(place: str, target: str, given: str) -> str:
    """書き出し先が `place` の下でない・途中にリンクがある理由。よければ空文字。"""
    if os.path.lexists(place) and os.path.islink(place):
        return "write-and-push がシンボリックリンク"
    if not cli_args._inside(target, place) or cli_args._same(target, place):
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
        if cli_args._real(directory) != os.path.normpath(directory):
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


def _parsed(
    stdin: TextIO,
    stdout: TextIO,
    stderr: TextIO,
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    unmarked: user_only.Verb | None = None,
) -> int:
    """解析済みの引数を、経路ごとの処理へ振り分ける。

    処理フロー:
    1. `--version` は `version.report` で出して終わる。
    2. 1 つだけ許すフラグと、`cli_templates` や `cli_learnings` の使い方を検査する。
    3. `settings.load` で設定を読む。読めない例外は、hook の経路だけ `_hook_crashed` で受け、
       ほかの経路では例外のまま伝える。
    4. 診断の経路かを決め（`_diagnosing`）、診断でない経路では差し替えの引数を破棄する
       （`_drop_outside_diagnosis`）。フラグの値を設定へ重ねてから（`_override`）、
       ガードとチケット制御の切り替えを `selfguard.resolve` で解く。ガードを解いた直後に、
       入口の印が無い `user` の入口を断る（`user_only.direct_refusal`。ガードが切れていれば通す）。
    5. 取り込み・テンプレート・学び・索引（`--docs`）の経路へ分ける。
    6. 検証（`--lint`）、診断（`--test`・`--test-expectations`・`--suggest`・`--explain`）、
       後始末（`--prune`）を順に見る。
    7. 計画の書き換え（`cli_plan`）と承認（`--agree` の `core` の関数）を見る。
    8. 残りの位置引数は、`ops`・`sync`・`tree`・`write-and-push`・`branches`・`stop-review` の
       どれかにヒットすれば、それぞれの関数へ渡す。
    9. 位置引数が残るか、`--reviewed`・`--close-early` があれば `cli_ops.operate` へ渡す。
    10. どれにもヒットしなければ `_hook` で、hook の判定として扱う。

    終了コードは、経路の関数が返す値をそのまま返すか、`EXIT_OK` か `EXIT_ERROR` に寄せる。
    検証（`--verify`）は 0 か `modes.EXIT_ANSWER_NO` を返し、承認が途中で止まったときは
    `modes.EXIT_PARTIAL` をそのまま返す。
    """
    # 版だけを出す。フラグは上の定義から引くので、足したものはそのまま並ぶ。
    if args.version:
        return version.report(stdout, parser, args.json)
    if not cli_args._one_wrapper_flag_each(stderr, args):
        return EXIT_ERROR
    if cli_templates.asked(args):
        refused = cli_templates.usage_problem(args)
        if refused:
            stderr.write(f"ccnavi: {refused}\n")
            return EXIT_ERROR
    if cli_learnings.asked(args):
        refused = cli_learnings.usage_problem(args)
        if refused:
            stderr.write(f"ccnavi: {refused}\n")
            return EXIT_ERROR
    # `--docs` に添えたほかの経路のフラグは、気づかれないまま無視せずに止める。
    # レイヤーの置き場の差し替えはこの後で無視され、`--ticket-control` などは
    # 設定に重ねられるので、その前に見る。
    if args.docs:
        refused = cli_args._not_with_docs(args)
        if refused:
            stderr.write(f"ccnavi: --docs は {refused} と同時に指定できない\n")
            return EXIT_ERROR

    root = args.root if args.root is not None else default_root()
    try:
        conf, problems = settings.load(root)
    except Exception as exc:  # noqa: BLE001 hook の経路では、終了コード 1 で通る形にしない
        # 設定を読めずに落ちたら、hook の経路だけは受け皿で終える。payload はまだ読んでいないので
        # イベントが分からず、PreToolUse と同じく止める。ほかの経路は、打った人が見られるので
        # 例外のまま伝える。
        if not _hook_route(args):
            raise
        return _hook_crashed(stdout, stderr, _unloaded(root), root, _HookSoFar(), exc)

    # レイヤーの置き場（中身の 3 本と、レイヤーを探す先の 2 本）と、記録・state・
    # 承認済みチケット・提案の置き場（`--log` / `--state` / `--approved` / `--tickets`）の
    # 差し替えは診断の経路でだけ使われる。
    # hook からの判定にも、チケットとレビューのサブコマンドにも差し替えの手段を残すと、
    # 設定を保存せずに緩める経路になるので、そこでは無視する。
    # 診断は payload を読まず、判定を実行にも記録にも繋げないので、保存していない設定を
    # 指しても実運用に影響しない。
    diagnosing = cli_args._diagnosing(args)
    if not diagnosing:
        cli_args._drop_outside_diagnosis(stderr, args)

    cli_args._override(conf, args)
    # フラグは設定ファイルより強い。書かれた値のほうも、そこに合わせて差し替える。
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
    # 入口の印（`--entry`）が無い `user <操作>` は、実行ファイルを直に打った形。ユーザが打つ入口は
    # sh（`ccnavi-user-*.sh`）で、承認のあとの後始末はそこが行う。テストと端末の無い実行環境は、
    # 端末要求と同じ切り替え（`CCNAVI_GUARD_TICKET_APPROVAL=disable`）で通す。
    if unmarked is not None and conf.guard_ticket_approval != selfguard.DISABLE:
        script = settings.script_command(
            root, f"{user_only.USER_SCRIPT_PREFIX}{unmarked.script}.sh"
        )
        stderr.write(f"ccnavi: {user_only.direct_refusal(unmarked, script)}\n")
        return EXIT_BLOCK
    # チケット制御も 2 値。読めない値は enable（使う側）として扱う。切ったつもりで
    # 値を書き誤った設定は、判定では有効なままになり、--lint が error で名指しする。
    if args.ticket_control:
        conf.ticket_control_declared = args.ticket_control
    conf.ticket_control = selfguard.resolve(
        stderr,
        args.ticket_control,
        conf.ticket_control,
        settings.TICKET_CONTROL_ENV,
        selfguard.GATE_SETTINGS,
    )

    # ドキュメントの索引を引く経路。
    # 絞り込みのフラグは `--docs` でだけ読む。ほかの経路で渡されたら止める。このフラグが
    # 無かったころは argparse が知らないフラグとして止めていたので、無視して先へ進めると
    # `ticket start X --limit 3` のような打ち間違いが通るようになる（`--flow` は診断の中の
    # 差し替えで、無視しても何も動かないので無視するだけにしている）。
    # 保護対象の下書きの取り込み（`user handoff`）。書くとき（`--yes`）は、hook の文字列照合だけを
    # 歯止めにせず、
    # 実行時にもターミナルの前のユーザが打ったことを確かめる（見るだけの形は求めない）。
    # ボードの経路（`--via board`）はターミナルを持たないので、
    # `--expect` の一致と hook の止めに置く。
    if args.handoff:
        return cli_handoff.run(stdin, stdout, stderr, parser, conf, root, args)
    # ミラーを揃える（`user mirror`）も同じ。書くとき（`--yes`）はターミナルの前のユーザを確かめ、
    # ボードの経路は `--expect` の一致と hook の止めに置く。
    if args.mirror:
        return cli_mirror.run(stdin, stdout, stderr, parser, conf, root, args)
    if args.templates:
        return cli_templates.run(stdout, stderr, conf, root, args)
    if args.learnings:
        return cli_learnings.run(stdout, stderr, conf, root, args)
    if not args.docs:
        stray = cli_args._docs_flags_given(args)
        if stray:
            for flag in stray:
                stderr.write(cli_args.DOCS_ONLY.format(flag=flag))
            return EXIT_ERROR
    else:
        return cli_args._docs(stdout, stderr, conf, root, args)

    # 1 つのレイヤーだけを差し替える形。使われる経路は共通レイヤーの 3 本と同じ。
    for flag, value, swaps in (
        ("--project-rules-file", args.project_rules_file, conf.project_rules_files),
        ("--project-phases-file", args.project_phases_file, conf.project_phases_files),
        ("--project-risk-file", args.project_risk_file, conf.project_risk_files),
    ):
        if not value:
            continue
        if not diagnosing:
            stderr.write(cli_args.DIAGNOSIS_ONLY.format(flag=flag))
            continue
        name, sep, path = value.partition("=")
        if not sep or not name or not path:
            stderr.write(f"ccnavi: {flag} は <名前>=<パス> の形で指定してください。\n")
            return EXIT_ERROR
        swaps[name] = os.path.abspath(path)

    # フローの確かめは `--lint` だけが読む。ほかの差し替えと同じく、
    # 診断の外では無視して言う。診断でも `--lint` でなければ読む先が無いので、そう言って無視する。
    if args.flow:
        if not diagnosing:
            stderr.write(cli_args.DIAGNOSIS_ONLY.format(flag="--flow"))
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
    # ユーザがターミナルから打って「このルールは何にヒットするのか」を確かめるための場所で、
    # 判定そのものは実運用と同じ関数を通る。判定が読む切り替えの環境変数も、hook と同じ
    # 関数で解いてから渡す（`_resolve_switches`）。
    if args.test is not None or args.test_expectations or (args.explain and not args.json):
        _resolve_switches(stderr, conf, args)
    if args.test is not None:
        call = diagnose.Call(
            cwd=args.cwd, permission_mode=args.permission_mode, agent_id=args.agent_id
        )
        if args.json:
            return diagnose.test_json(stdout, stderr, conf, root, args.test[0], args.test[1], call)
        return diagnose.test(stdout, stderr, conf, root, args.test[0], args.test[1], call)
    if args.test_expectations:
        return diagnose.test_expectations(
            stdout, stderr, conf, root, args.test_expectations, args.json
        )
    if args.suggest:
        return suggest.report(stdout, conf, root, args.json)
    if args.explain and args.json:
        return diagnose.explain_json(stdout, stderr, conf, root, args.mode, args.ticket_control)
    if args.explain:
        return diagnose.explain(stdout, stderr, conf, root)

    # 後始末の経路。payload を読まない。セッションの開始でも同じものが走る（events）。
    if args.prune:
        return cli_args._prune(stdin, stdout, stderr, conf, root, args.preview)

    # 親の提案の計画を書き換える補助の経路。`--agree` のターミナルの確かめ（`_from_terminal`）より
    # 手前で分ける。書くのは承認待ちの親の提案（エージェントも書ける置き場）だけで、承認は
    # しない。`--agree` と一緒には受けない（組み込みの止めと経路が食い違わないように）。
    reordering = args.order is not None or args.write or args.expect is not None
    if args.plan_order is not None or args.fill_phases or reordering:
        problem = _plan_order_usage(args, reordering)
        if problem:
            stderr.write(f"ccnavi: {problem}\n")
            return EXIT_ERROR
        if not conf.tickets_enabled:
            stderr.write(
                f"ccnavi: チケット制御が無効になっています（{settings.TICKET_CONTROL_ENV}）。\n"
            )
            return EXIT_ERROR
        if args.fill_phases:
            return cli_plan.fill_phases(stdout, stderr, conf, root, args.plan_order.strip())
        return cli_plan.reorder(
            stdout,
            stderr,
            conf,
            root,
            args.plan_order.strip(),
            args.json,
            args.order,
            args.write,
            args.expect or "",
        )

    # 承認の経路。ユーザがターミナルから打つもので、payload を読まないのでここで分かれる。
    # 判定を 1 度も通らないのも分ける理由で、承認は、これから判定に使われる
    # 範囲についての合意になる。
    if args.agree:
        if not conf.tickets_enabled:
            stderr.write(
                f"ccnavi: チケット制御が無効になっています（{settings.TICKET_CONTROL_ENV}）。\n"
            )
            return EXIT_ERROR
        if args.preview and args.yes:
            stderr.write("ccnavi: --preview と --yes は同時に指定できません。\n")
            return EXIT_ERROR
        # 確かめるだけの枝は `--preview` と一緒に使う。単独で打てる形にすると、組み込みの
        # deny（phase_forms.ticket_approval_rule）が免除するのは `--preview` の付いた `--agree`
        # だけなので、エージェントが打てないものを案内することになる。
        if args.verify and not args.preview:
            stderr.write("ccnavi: --verify は --agree --preview と一緒に指定してください。\n")
            return EXIT_ERROR
        # 承認できる状態かを確かめるだけ。置かないのは `--preview` と同じで、違うのは
        # 通るかどうかを終了コードで返すところ。答えは 0（はい）と
        # 3（いいえ）で、使い方と設定の誤りの 1 とは分ける。
        if args.verify:
            return core.verify(stdout, stderr, conf, root, args.json, list(args.command))
        # 見るだけの経路。承認済みチケットを置かないのでターミナルの確認は要らない。後ろに並べた語は
        # `--agree` と同じで、承認の対象に入れる識別子（ボードの絞り込みで見えている分）。
        if args.preview:
            code = core.preview(stdout, stderr, conf, root, args.json, list(args.command))
            return EXIT_OK if code == 0 else EXIT_ERROR
        # 拡張のオーバーレイでユーザが押した承認。ターミナルの確認の代わりに、見せた一覧と今の一覧が
        # 同じであることを求める。エージェントがこれを Bash で打つ形は組み込みの
        # deny（phase_forms.ticket_approval_rule）が止める。
        if args.yes:
            via.set_via(via.VIA_BOARD)
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
        if not cli_args._from_terminal(stdin, conf, stderr, user_only.terminal("approve")):
            return EXIT_ERROR
        via.set_via(via.VIA_TERMINAL)
        # `--agree` の後ろに並べた語は、承認の対象に入れる識別子。無ければ承認待ち全部。
        approved = core.approve(
            stdin, stdout, stderr, conf, root, only=list(args.command), placed_out=args.placed_out
        )
        if approved == modes.EXIT_PARTIAL:
            return modes.EXIT_PARTIAL
        return EXIT_OK if approved == 0 else EXIT_ERROR

    # チケットの状態とレビューの操作。payload を読まない。
    # 操作記録（`logs/state/ops/`）の読み書き。sh が送信の段階を記録するために呼ぶ。
    # チケット制御の有無に依らない（decide・review-request などの段階は、
    # 制御が有効なときだけ使われる）。
    if args.command and args.command[0] == "ops":
        return cli_ops.ops_command(stdout, stderr, conf, list(args.command[1:]))
    # 取り込みの sh（`ccnavi-sync.sh`）がパスを聞く経路。読むだけで、チケット制御の有無に依らない。
    if list(args.command) == ["sync", "paths"]:
        return EXIT_OK if cli_ops.sync_paths(stdout, root, conf) == 0 else EXIT_ERROR
    # 親を閉じた後の片付けの列挙元（`retire/<親>.json`）。
    # ネットワークにも git にも触れず、チケット制御の有無に依らない。
    if list(args.command) == ["sync", "retired"]:
        return EXIT_OK if cli_ops.sync_retired(stdout, conf) == 0 else EXIT_ERROR
    # 取り込みの後の検査（正とする側と、手元と統合先のレイヤーの食い違い）。
    # error があれば sh が親子のチケットの取り込みを止める。warn は表示するだけ。
    if len(args.command) in (3, 4) and list(args.command[:2]) == ["sync", "check"]:
        repo = args.command[3] if len(args.command) == 4 else None
        code = cli_ops.sync_check(stdout, stderr, root, conf, args.command[2], repo)
        return EXIT_OK if code == 0 else EXIT_ERROR
    # sh が文面ファイルの文を引く。読むだけで、チケット制御の有無に依らない。
    if args.command and args.command[0] == "text":
        return cli_text.run(stdout, stderr, root, list(args.command[1:]))
    # sh がチケットについて聞くこと。
    # 答えはチケット制御の有無に依らない（sh の前の見方がそうだった）。
    if len(args.command) == 4 and list(args.command[:2]) == ["tree", "parent"]:
        code = sh_ask.parent_tree(stdout, stderr, root, conf, args.command[2], args.command[3])
        return EXIT_OK if code == 0 else EXIT_ERROR
    if len(args.command) == 4 and list(args.command[:2]) == ["tree", "bound"]:
        code = sh_ask.bound_tree(stdout, stderr, root, conf, args.command[2], args.command[3])
        return EXIT_OK if code == 0 else EXIT_ERROR
    if len(args.command) == 4 and list(args.command[:2]) == ["tree", "leftover"]:
        code = sh_ask.leftover_tree(stdout, stderr, root, conf, args.command[2], args.command[3])
        return EXIT_OK if code == 0 else EXIT_ERROR
    if len(args.command) == 4 and list(args.command[:2]) == ["tree", "base-bound"]:
        code = sh_ask.base_bound_tree(stdout, stderr, root, conf, args.command[2], args.command[3])
        return EXIT_OK if code == 0 else EXIT_ERROR
    if len(args.command) >= 4 and list(args.command[:2]) == ["tree", "guarded"]:
        code = sh_ask.guarded_tree(
            stdout, stderr, root, conf, args.command[2], list(args.command[3:])
        )
        return EXIT_OK if code == 0 else EXIT_ERROR
    if len(args.command) in (3, 4) and list(args.command[:2]) == ["tree", "handoff-pending"]:
        code = sh_ask.handoff_pending_tree(stdout, stderr, *args.command[2:])
        return EXIT_OK if code == 0 else EXIT_ERROR
    if len(args.command) == 6 and list(args.command[:2]) == ["sync", "tidy"]:
        code = cli_ops.sync_tidy(
            stdout, stderr, root, conf, args.cwd or os.getcwd(), *args.command[2:]
        )
        return EXIT_OK if code == 0 else EXIT_ERROR
    # write-and-push（取り込み済みの親子のチケットの状態の操作を、取り込み・書く・コミット・push の
    # 1 操作に
    # する sh）が聞くこと。読むだけで、何も書かない。
    if len(args.command) == 3 and list(args.command[:2]) == ["write-and-push", "family"]:
        if not cli_ops._FAMILY.fullmatch(args.command[2]) or ".." in args.command[2]:
            stderr.write(
                f"ccnavi: write-and-push family の {args.command[2]!r} "
                "は有効な識別子の形式ではありません。\n"
            )
            return EXIT_ERROR
        return (
            EXIT_OK
            if write_and_push.family(stdout, conf, root, args.command[2]) == 0
            else EXIT_ERROR
        )
    if len(args.command) in (3, 4) and list(args.command[:2]) == ["write-and-push", "sort"]:
        since = args.command[3] if len(args.command) == 4 else ""
        if not cli_ops._FAMILY.fullmatch(args.command[2]) or ".." in args.command[2]:
            stderr.write(
                f"ccnavi: write-and-push sort の {args.command[2]!r} "
                "は有効な識別子の形式ではありません。\n"
            )
            return EXIT_ERROR
        if since and not cli_args._revision_ok(since):
            stderr.write(f"ccnavi: write-and-push sort の版 {since!r} は読めない\n")
            return EXIT_ERROR
        code = write_and_push.sort(stdout, stderr, conf, root, args.command[2], since)
        return EXIT_OK if code == 0 else EXIT_ERROR
    if len(args.command) >= 3 and list(args.command[:2]) == ["write-and-push", "merge"]:
        if (
            not cli_ops._FAMILY.fullmatch(args.command[2])
            or ".." in args.command[2]
            or len(args.command) != 3
        ):
            stderr.write(
                f"ccnavi: write-and-push merge の {args.command[2]!r} "
                "は有効な識別子の形式ではありません。\n"
            )
            return EXIT_ERROR
        return (
            EXIT_OK
            if write_and_push_merge.plan(stdout, conf, root, args.command[2]) == 0
            else EXIT_ERROR
        )

    # issue・MR に紐づくブランチを探す。`ccnavi-branches.sh` が呼ぶ。
    # ホストの結果は sh が取ってきて `--result` で渡す。
    if len(args.command) == 3 and args.command[0] == "branches":
        cwd = args.cwd or os.getcwd()
        code = branchfind.report(
            stdout,
            stderr,
            conf,
            root,
            cwd,
            args.command[1],
            args.command[2],
            args.result,
            args.json,
        )
        return EXIT_OK if code == 0 else EXIT_ERROR

    # 放置したセッションの引き継ぎ促し。payload は入口の中で読む（`ccnavi-idle.sh` が呼ぶ）。
    if args.command[:1] == ["idle"]:
        return cli_idle.run(stdin, stdout, stderr, conf, list(args.command))

    # Stop の AI 判定の判定役（agent hook）が打つ入口。payload は読まない。
    if args.command[:1] == ["stop-review"]:
        return cli_stopreview.run(stdin, stdout, stderr, conf, list(args.command), root)

    if args.command or args.reviewed is not None or args.close_early:
        # 残った指摘を見せるだけの `--preview` と、
        # オーバーレイで押した `--yes` はターミナルを求めない。
        # `--yes` を守るのは、見せた指摘のダイジェストの一致と、
        # シェルから打つ形を止める組み込みの deny。
        board = args.reviewed is not None and args.accept_unresolved and (args.preview or args.yes)
        if (
            args.reviewed is not None
            and not board
            and not cli_args._from_terminal(stdin, conf, stderr, user_only.terminal("reviewed"))
        ):
            return EXIT_ERROR
        return cli_ops.operate(stdin, stdout, stderr, conf, root, args)

    return _hook(stdin, stdout, stderr, conf, problems, root, args)


@dataclasses.dataclass
class _HookSoFar:
    """hook の 1 回で、例外までにどこまで進んだか。受け皿（`_hook_crashed`）が読む。

    モードを解く前に落ちたら enable として止める側に倒す。記録は 1 度しか試さない。
    記録そのものが例外で終わった回に、受け皿でもう 1 度書こうとして同じ例外を重ねないため。
    """

    mode: str = modes.ENABLE
    payload: hookio.Input | None = None
    record: audit.Record | None = None
    log: audit.Log | None = None
    recorded: bool = False


def _hook_route(args: argparse.Namespace) -> bool:
    """この起動が hook の経路（`_parsed` の最後の `_hook`）に進むか。

    設定を読む前に分ける必要があるときに使う。`_parsed` の振り分けで、payload を読まない経路の
    どれにもヒットしないもの。経路を足したら、ここにも足す。
    """
    return not (
        args.docs
        or args.learnings
        or args.handoff
        or args.mirror
        or cli_args._diagnosing(args)
        or args.prune
        or args.plan_order is not None
        or args.fill_phases
        or args.order is not None
        or args.write
        or args.expect is not None
        or args.agree
        or args.command
        or args.reviewed is not None
        or args.close_early
    )


def _unloaded(root: str) -> settings.Settings:
    """設定を読めなかった回の、記録と state の既定の置き場だけを持つ設定。"""
    return settings.Settings(
        log=os.path.join(root, settings.DEFAULT_LOG),
        state=os.path.join(root, settings.DEFAULT_STATE),
    )


def _hook(
    stdin: TextIO,
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    problems: list[str],
    root: str,
    args: argparse.Namespace,
) -> int:
    """hook として起動された 1 回。途中のどこで例外が出ても、終了コード 1 で抜けさせない。

    例外を抜けさせると Python は終了コード 1 で終わり、Claude Code は PreToolUse の呼び出しを
    そのまま通す。判定の本体だけでなく、モードの解決・payload の読み取り・記録・診断ログの
    どこで落ちても、内部エラーとして終える。
    """
    so_far = _HookSoFar()
    try:
        return _hook_steps(stdin, stdout, stderr, conf, problems, root, args, so_far)
    except Exception as exc:  # noqa: BLE001 想定外の例外を、終了コード 1 で通る形にしない
        return _hook_crashed(stdout, stderr, conf, root, so_far, exc)


def _resolve_switches(stderr: TextIO, conf: settings.Settings, args: argparse.Namespace) -> None:
    """判定が読む切り替えの環境変数を解いて、設定に戻す。hook と診断（`--test` など）で同じもの。

    解く前の値は書いたままの表記（大文字・前後の空白）を持つ。判定はどれも解いた後の値と
    比べるので、診断が解かずに判定を呼ぶと、hook とは違う答えになる。
    """
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


def _hook_steps(
    stdin: TextIO,
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    problems: list[str],
    root: str,
    args: argparse.Namespace,
    so_far: _HookSoFar,
) -> int:
    """hook の 1 回分の処理。`_hook` から呼ばれ、途中の進み具合を `so_far` に残す。

    処理フロー:
    1. 設定の読み込みで出た問題を stderr に書く。
    2. `modes.resolve_mode` でモードを決め、`_resolve_switches` で切り替えを解き、
       `audit.Log` を作る。
    3. `hookio.decode` で payload を読む。ターミナルの前のユーザ（payload が無い）には使い方を出して
       終える。読めない payload は、記録と診断ログを残してから `modes.fail_closed` で終える。
    4. payload から記録の枠（`audit.Record`）を作る。
    5. `events.decide` で判定する。例外なら `_internal_error` で応答を組む。出力は手元の
       バッファに溜める。
    6. `cli_args._record` で記録を書き、診断ログを書く。最後にバッファを stdout へ出す。

    終了コードは判定が返したもの。payload が無い・読めないときは `EXIT_ERROR` か
    `modes.fail_closed` の値。
    """
    for problem in problems:
        stderr.write(f"ccnavi: {problem}\n")

    mode = modes.resolve_mode(stderr, args.mode, conf)
    so_far.mode = mode
    _resolve_switches(stderr, conf, args)
    log = audit.Log(conf.log)
    so_far.log = log
    deadline = time.monotonic() + judge.DEADLINE_SECONDS

    try:
        payload = hookio.decode(stdin)
    except hookio.NoPayload:
        # hook ではなくターミナルの前のユーザ。使い方を出す。
        stderr.write(cli_usage.OVERVIEW)
        return EXIT_ERROR
    except hookio.Unusable as exc:
        stderr.write(
            reasons.payload_unusable(
                root, audit.REASON_PAYLOAD_UNUSABLE, str(exc), event=exc.event, mode=mode
            )
            + "\n"
        )
        diaglog.get("ccnavi", root).warn("hook の payload を読めない", mode=mode)
        so_far.recorded = True
        cli_args._record(
            stderr,
            log,
            audit.Record(
                mode=mode,
                event=exc.event,
                decision=audit.SKIP,
                reason=audit.REASON_PAYLOAD_UNUSABLE,
            ),
        )
        # ツールが分からない PreToolUse（とイベントすら読めなかった回）は dry-run でも止める。
        return modes.fail_closed(mode, "" if exc.event in ("", hookio.PRE_TOOL_USE) else None)

    so_far.payload = payload
    record = audit.Record(
        mode=mode,
        permission_mode=payload.permission_mode,
        event=payload.event,
        tool=payload.tool_name,
        session=payload.session_id,
        agent=payload.agent_id,
    )
    so_far.record = record

    # 出力は手元に溜め、記録と診断ログを終えてから出す。例外で終えられなかった回に、途中まで
    # 書いた JSON と内部エラーの応答が混ざって、どちらも読まれなくなるのを避けるため。
    buffered = io.StringIO()
    try:
        via.set_via(via.VIA_HOOK)
        record.subject = judge.subject_of(payload)
        code = events.decide(buffered, stderr, mode, conf, root, payload, record, deadline)
    except Exception as exc:  # noqa: BLE001 想定外の例外を、終了コード 1 で通る形にしない
        buffered = io.StringIO()
        code = _internal_error(buffered, stderr, mode, conf, root, payload, record, exc)
    so_far.recorded = True
    cli_args._record(stderr, log, record)
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
    stdout.write(buffered.getvalue())
    return code


def _hook_crashed(
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    so_far: _HookSoFar,
    exc: Exception,
) -> int:
    """判定の本体の外（モードの解決・payload の読み取り・記録など）で落ちた回の受け皿。

    payload を読む前に落ちたら、何のイベントか分からないので PreToolUse と同じく止める。
    記録はまだ試していないときだけ書く。
    """
    payload = so_far.payload or hookio.Input()
    record = so_far.record or audit.Record(
        mode=so_far.mode,
        event=payload.event,
        tool=payload.tool_name,
        session=payload.session_id,
    )
    out = io.StringIO()
    code = _internal_error(out, stderr, so_far.mode, conf, root, payload, record, exc)
    if not so_far.recorded:
        so_far.recorded = True
        try:
            cli_args._record(stderr, so_far.log or audit.Log(conf.log), record)
        except Exception as again:  # noqa: BLE001 受け皿の中では例外を重ねない
            stderr.write(f"ccnavi: ログを保存できませんでした：{type(again).__name__}\n")
    stdout.write(out.getvalue())
    return code


def _internal_error(
    stdout: TextIO,
    stderr: TextIO,
    mode: str,
    conf: settings.Settings,
    root: str,
    payload: hookio.Input,
    record: audit.Record,
    exc: Exception,
) -> int:
    """イベントの処理が想定外の例外で終わったときの応答と記録。

    例外を抜けさせると Python は終了コード 1 で終わる。Claude Code は 0 と 2 以外を止める扱いに
    しないので、PreToolUse の呼び出しがそのまま通り、記録も残らない。ここで受けて、判定に
    達せなかった回として終える。

    - PreToolUse: ツールの種類で決める（`modes.fail_closed`）。書き込み・Agent・Task・
      ツールが分からない回は dry-run でも止める。サブエージェントの報告用のツールは通す。
      読み取りは最低限の deny にヒットしなければ通す。標準エラーの文がモデルに届く
    - PostToolUse: ツールはもう走っているので止めるものは無い。enable は終了コード 2 と
      標準エラーで、実行後の検査をしていないことをモデルに言う（dry-run は 0）
    - payload を読む前に落ちた（イベントが分からない）: PreToolUse と同じく止める
    - それ以外: 終了コード 0 で、ユーザに `systemMessage` で知らせる。2 で終えると、Stop と
      SubagentStop は終われなくなり、UserPromptSubmit は依頼文を消すので、ccnavi の不具合が
      作業そのものを止める。SubagentStop は子の範囲を確かめられなかった回と同じ印を置き、
      親に届くようにする（`systemMessage` はユーザにしか見えない）
    """
    kind = type(exc).__name__
    frames = traceback.extract_tb(exc.__traceback__)
    where = f"{os.path.basename(frames[-1].filename)}:{frames[-1].lineno}" if frames else ""
    log_path = os.path.join(root, diaglog.DIAG_DIR, "ccnavi.log")
    # 例外の文は書かない。コマンドやパスの断片が混ざることがある。型と場所があれば辿れる。
    # 例外の出どころが診断ログそのものでも、ここでは重ねない。
    with contextlib.suppress(Exception):
        diaglog.get("ccnavi", root).error(
            "hook の処理が例外で終わった",
            event=payload.event,
            tool=payload.tool_name,
            mode=mode,
            error=kind,
            at=where,
        )
    record.decision, record.reason = audit.SKIP, audit.REASON_INTERNAL_ERROR
    record.code, record.rules = "", []
    record.detail = f"{kind} at {where}" if where else kind
    if payload.event in ("", hookio.PRE_TOOL_USE, hookio.POST_TOOL_USE):
        if payload.event == hookio.POST_TOOL_USE:
            code = modes.fail_closed(mode)
        else:
            # 呼び出しの前（イベントが分からない回を含む）は、ツールの種類で決める。設定を読まずに
            # 通してよいと確かめられた読み取りだけを通す（`hook.failsafe`）。
            code = modes.fail_closed(mode, payload.tool_name, safe=failsafe.is_safe(payload, root))
        passed = code == 0 and payload.event != hookio.POST_TOOL_USE
        stderr.write(
            reasons.internal_error(
                root, audit.REASON_INTERNAL_ERROR, payload.event, kind, log_path, passed=passed
            )
            + "\n"
        )
        return code
    if payload.event == hookio.SUBAGENT_STOP:
        subagent.unchecked_after_error(stderr, conf, root, payload)
    hookio.write_system_message(
        stdout,
        reasons.internal_error_notice(audit.REASON_INTERNAL_ERROR, payload.event, kind, log_path),
    )
    return EXIT_OK


def default_root() -> str:
    """ワークスペースルート、つまり .claude を持つディレクトリを見つける。ワークツリーは除く。

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


def _is_worktree_dir(directory: str) -> bool:
    """`<ワークスペースルート>/.claude/worktrees/<名前>` の位置にあるディレクトリか。

    大文字小文字は、どの機械でも区別しない。Windows で clone したワークツリーは
    表記が変わることがあり、機械ごとに判定が割れると Windows でだけ失敗するため。
    """
    holder = pathcase.fold(os.path.dirname(directory))
    return holder.endswith(os.sep + pathcase.fold(tree.WORKTREES_DIR))


def _find_project_root() -> str:
    """作業ディレクトリからファイルシステムのルートまで上って .claude を探す。
    バージョン管理が自分のルートを見つけるのと同じやり方。

    ワークツリー（`<ワークスペースルート>/.claude/worktrees/<名前>`）は `.claude` を追跡して
    いて自分も `.claude` を持つ。そこで止まるとワークスペースルートを取り違えるので、
    その位置のディレクトリは飛ばして親のワークスペースルートまで上る。"""
    try:
        directory = os.getcwd()
    except OSError:
        return ""
    while True:
        if os.path.isdir(os.path.join(directory, ".claude")) and not _is_worktree_dir(directory):
            return directory
        parent = os.path.dirname(directory)
        if parent == directory:
            return ""
        directory = parent
