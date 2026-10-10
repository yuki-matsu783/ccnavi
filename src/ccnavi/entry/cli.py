"""標準入出力とコマンドラインを 1 つの判定に繋ぐ。

run は終了コードを返す。自分で終了しないので、道具ぜんぶを別プロセス無しで
テストから動かせる。

ここに置くのは引数の解釈と振り分けだけ。hook のイベントごとの手順は events、
実行前チェックは judge、判定につける文面は reasons、モードと終了コードは modes に
ある。チケットとレビューの操作（`ticket ...` / `review ...`）は `cli_ops.operate` が
ops / review へ渡す。`--help` の本文は `cli_usage`、旗の読み分けは `cli_args` に分けてある。
どれも cli を読まない。
"""

from __future__ import annotations

import argparse
import contextlib
import os
import tempfile
import time
from typing import TextIO

from ..hook import c1, core, events, judge
from ..infra import fsio, hookio, modes, settings
from ..infra.modes import EXIT_BLOCK, EXIT_ERROR, EXIT_OK
from ..policy import selfguard
from ..records import audit, diaglog
from ..tickets import (
    branchfind,
    configsync,
    history,
    review,
)
from . import cli_args, cli_ops, cli_usage, diagnose, lint, plan_order, suggest, version


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


def run(stdin: TextIO, stdout: TextIO, stderr: TextIO, argv: list[str]) -> int:
    """1 回の起動を処理する。

    状態の履歴（history）の経路はここで決まる。既定はエージェントが sh から打つ副命令（`cli`）で、
    ユーザの判断の経路（端末・ボード）と hook は枝の中で差し替える。履歴を書けなかった知らせは、
    起動を抜けるときに標準エラーへ出す（状態の履歴は補助で状態の正は置き場なので、状態の操作は止めない）。
    """
    with history.session(history.VIA_CLI, stderr):
        return _run(stdin, stdout, stderr, argv)


# 前の名前。「合意」を表す語を 1 つにするため `--agree` に改名した。別名にはしない（これで承認が
# 動くことは無い。残すと組み込みの保護とヘルプの両方に 2 つの表記が残り続ける）。
# argparse に登録しないので、ヘルプにも `--version` の flags 一覧にも出ない。
RENAMED_APPROVE = "--approve"
RENAMED_APPROVE_NOTICE = (
    "ccnavi: --approve は --agree に改名しました。sh なら ccnavi-agree.sh を使ってください。\n"
)


def _asks_renamed_approve(argv: list[str]) -> bool:
    """前の名前 `--approve`（`--approve=x` の形も）が渡されているか。"""
    return any(word == RENAMED_APPROVE or word.startswith(RENAMED_APPROVE + "=") for word in argv)


def _run(stdin: TextIO, stdout: TextIO, stderr: TextIO, argv: list[str]) -> int:
    # 何も読まず何も動かさずに終える。承認の経路にも判定にも入れない。
    if _asks_renamed_approve(argv):
        stderr.write(RENAMED_APPROVE_NOTICE)
        return EXIT_BLOCK  # 2。使い方の誤り（1）と分け、案内だけで終える
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
    # 承認できる状態かを確かめるだけ（`--preview` と一緒に使う）。承認済みチケットは置かず、
    # 通るかどうかを終了コードで返す。エージェントがユーザに承認を頼む前に打つ。
    parser.add_argument("--verify", action="store_true")
    # 親の提案の計画を書き換える補助（`--agree` の外の独立したフラグ。エージェントも打ってよい）。
    # いまは `--fill-phases`（計画が使う定義だけを phases.yml から `phases:` に写す）だけ。
    parser.add_argument("--plan-order", default=None, metavar="PARENT")
    parser.add_argument("--fill-phases", action="store_true")
    # 見せた一覧の識別子（カンマ区切り）。拡張のオーバーレイでユーザが押した承認。端末は要らない。
    parser.add_argument("--yes", default="")
    # 見せた承認画面の本文・判定が読んだ中身・承認済みチケットに書き込む中身のダイジェスト
    # （preview の `digest`）。`--yes` と一緒に渡す。
    parser.add_argument("--digest", default="")
    parser.add_argument("--test", nargs=2, metavar=("TOOL", "SUBJECT"), default=None)
    # 見本をぜんぶ判定に掛ける。tools/check_rules.py と VS Code 拡張が呼ぶ。
    parser.add_argument("--test-samples", metavar="FILE", default="")
    parser.add_argument("--explain", action="store_true")
    # 記録のローテートと、古い記録・終わったセッションの記録の削除（prune）。ユーザが端末から打つ。
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
    # チケットの状態とレビューの操作。ユーザか、親が保護済みスクリプトから呼ぶ。
    parser.add_argument("command", nargs="*")
    parser.add_argument("--cwd", action="append", default=None)
    parser.add_argument("--phase", type=int, default=None)
    parser.add_argument("--body-file", default="")
    parser.add_argument("--reason", default="")
    parser.add_argument("--reviewed", type=int, default=None)
    parser.add_argument("--accept-unresolved", action="store_true")
    parser.add_argument("--chat", action="store_true")
    # ユーザが端末で打つ、親を早めに閉じる操作。`--agree` / `--reviewed` と同じく、
    # ユーザの判断はフラグで受ける。
    parser.add_argument("--close-early", action="store_true")
    parser.add_argument("-h", "--help", action="store_true")
    # 版・組み立ての元のコミット・受け付けるフラグ・互換の版を言う。拡張と sh が起動のときに読む。
    parser.add_argument("--version", action="store_true")
    # この実行で書いた・消したパスを、state の置き場の下のファイルに 1 行 1 つで書き出す
    # （fsio の記録層が記録する）。C1 の sh が渡し、一覧のパスだけをコミットする。
    parser.add_argument("--record-writes", default="")
    # 一覧の基点（C1 の親のワークツリー）。渡すと一覧はこのツリーからの相対になり、
    # 置き場の外に書いたら error
    # （例外は `start` の中で configsync が写したミラーだけ）。
    parser.add_argument("--record-tree", default="")
    # 対話の decide の前半。選択とダイジェストをこのファイルに書くだけで、
    # 何も置かない（state の置き場の
    # 下だけ）。ユーザが選ぶのを C1 のロックの外で済ませ、ロックを持ったままユーザを待たないため。
    parser.add_argument("--choose-out", default="")
    # 統合先の名前。リポジトリには置かず、手元では環境変数 `CCNAVI_INTEGRATION_BRANCH`（未設定なら
    # ホストのデフォルトブランチ）で決まる。実行ファイルは環境変数を読まず、sh が決めて渡す。
    # いまは `--lint` の識別子の予約（統合先と同じ名前の識別子を使わせない）が読む。
    parser.add_argument("--integration-branch", default="")
    # レビュー済みのマーカーに入れるアカウント（押したユーザではなくトークンの持ち主）。
    # ccnavi-review.sh がトークンの持ち主をホストに聞いて渡す。実行ファイルは
    # ネットワークに出ない。
    # 読むのは `review confirm` と、書く形の decide（`--reviewed N --accept-unresolved`）。
    parser.add_argument("--actor", default="")
    # decide が書くレビュー済みのマーカーの経路（`via`）で、`terminal` か `board` を渡す。
    # `--actor` があるときだけ受け付ける。アカウントを引けなかったときに、
    # マーカーも履歴も前と同じ中身にするため。
    parser.add_argument("--via", default="")
    try:
        args = parser.parse_args(_json_out_of_test(argv))
    except SystemExit:
        return EXIT_ERROR
    if args.help:
        stderr.write(cli_usage.USAGE)
        return EXIT_ERROR
    if args.record_tree and not args.record_writes:
        stderr.write("ccnavi: --record-tree は --record-writes と一緒に使う\n")
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
            "ccnavi: --actor は review confirm か、書く形の decide"
            "（--reviewed <N> --accept-unresolved）と一緒に使う\n"
        )
        return EXIT_ERROR
    if args.via and (not args.actor or not cli_ops._decide_writes(args)):
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

    **書き出す先**は、ワークスペースルートの既定の state の置き場（`logs/state`）の下の `c1/`
    だけ。`--state` の上書きは見ない（上書きで置き場を動かせば、
    どこへでも書ける経路になる）。行き先・置き場・ルートは行き着く先（リンクを解いたパス、
    大文字小文字をそろえたもの）で比べ、`c1/` までの途中にリンクがあれば断る。書き出しは
    同じディレクトリの一時ファイルから `os.replace` で置き換えるので、行き先にリンクが
    あってもその先は開かない（リンクの行き先は書き換えない。Windows でも同じ）。

    **一覧の形**は 1 行 1 つ、ワークスペースルートからの相対（区切りは "/"）。ルートの外と、
    別のドライブ（Windows）は絶対パスのまま。既定の state の置き場の下（リポジトリに入らない）と
    一覧のファイル自身は載せない。上書きした state の置き場への書き込みは載る（C1 が
    「置き場の外」として止める側）。置き場の外が入っていたら止めるのは C1 で、
    ここは集めて書くだけ。

    **終了コード**: コマンドが落ちても、そこまでに書いたパスで一覧を書く（C1 は落ちた回も
    戻すのに一覧を使う）。一覧を書けなければ、コマンドの結果に依らず 1 で終わる
    （C1 はそれを「書いたものが分からない」として扱う）。行き先が断られたときはコマンドを
    走らせずに 1 で終わる。
    """
    wrapper = argparse.Namespace(**vars(args))
    if not cli_args._one_wrapper_flag_each(stderr, wrapper):
        return EXIT_ERROR
    root = wrapper.root if wrapper.root is not None else default_root()
    real_root = cli_args._real(root)
    state = os.path.join(real_root, settings.DEFAULT_STATE)
    place = os.path.join(state, "c1")
    given = os.path.abspath(args.record_writes)
    target = os.path.join(cli_args._real(os.path.dirname(given)), os.path.basename(given))
    refused = _record_place_problem(place, target, args.record_writes)
    if refused:
        stderr.write(
            f"ccnavi: --record-writes の書き出し先は {place}{os.sep}... の下だけ（{refused}）\n"
        )
        return EXIT_ERROR
    base = cli_args._real(os.path.abspath(args.record_tree)) if args.record_tree else real_root
    # 上書きした state の置き場（`--state`）も、リポジトリに入らない書き込み
    # （レビューの下書きなど）の置き場なので一覧から外す。
    conf_for_state, _ = settings.load(root)
    cli_args._override(conf_for_state, args)
    moved_state = cli_args._real(conf_for_state.state) if conf_for_state.state else state
    with fsio.recording() as written:
        code = _parsed(stdin, stdout, stderr, parser, args)
    lines = []
    reals = []
    for path in written:
        real = fsio.parent_resolved(path)
        if (
            cli_args._inside(real, state)
            or cli_args._inside(real, moved_state)
            or cli_args._same(real, target)
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
    """一覧のうち、C1 の置き場（承認済み・レビュー待ち）の外のもの。

    C1 がコミットするのは状態だけ。

    例外は 2 つ。`ticket start` の中で configsync がミラーしたプロジェクトの
    `.ccnavi/common/`（`configsync.is_synced_write` が内容で読めるもの）と、`review ready` が
    消した途中の作業の置き場（`wip/`）のファイル（消したもの、つまり今は無いものだけ。
    `review.remove_wip` が追跡済みのものだけを消す）。
    """
    conf, _ = settings.load(root)
    cli_args._override(conf, args)
    approved = settings.approved_dir(conf, base)
    review_dir = os.path.join(
        base, (conf.tickets or settings.DEFAULT_TICKETS).replace("/", os.sep), "review"
    )
    places = [cli_args._real(approved), cli_args._real(review_dir)]
    starting = list(args.command[:2]) == ["ticket", "start"]
    readying = list(args.command[:2]) == ["review", "ready"]
    found = []
    for real in reals:
        if cli_args._inside(real, base) and any(cli_args._inside(real, p) for p in places):
            continue
        if readying and _ready_removed_wip(real, base):
            continue
        if (
            starting
            and cli_args._inside(real, base)
            and configsync.is_synced_write(conf, root, real)
        ):
            continue
        found.append(real)
    return found


def _ready_removed_wip(real: str, base: str) -> bool:
    """`review ready` が消した途中の作業の置き場（`wip/`）の下のファイルか。

    `ready` が消すものを選ぶのと同じ判定（`review.in_wip`。大文字小文字と `\\` の区切りを
    問わない）を、C1 のツリー（`base`）からの相対に当てる。通すのは、そのツリーの中で、今は無い
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
        return "c1 がシンボリックリンク"
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
) -> int:
    # 設定もワークスペースも読まない。フラグは上の定義から引くので、足したものはそのまま並ぶ。
    if args.version:
        return version.report(stdout, parser, args.json)
    if not cli_args._one_wrapper_flag_each(stderr, args):
        return EXIT_ERROR
    # `--docs` に添えたほかの経路のフラグは、黙って無視せずに止める。レイヤーの置き場の差し替えは
    # この後で落とされ、`--ticket-control` などは設定に重ねられるので、その前に見る。
    if args.docs:
        refused = cli_args._not_with_docs(args)
        if refused:
            stderr.write(f"ccnavi: --docs は {refused} と一緒に使えない\n")
            return EXIT_ERROR

    root = args.root if args.root is not None else default_root()
    conf, problems = settings.load(root)

    # レイヤーの置き場（中身の 3 本と、レイヤーを探す先の 2 本）
    # の差し替えは診断の経路でだけ使われる。
    # hook からの判定にも、チケットとレビューの副命令にも差し替えの手段を残すと、
    # 設定を保存せずに緩める経路になるので、そこでは無視する。
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

    # ドキュメントの索引を引く経路。payload を読まず、判定も記録もしない。
    # 絞り込みのフラグは `--docs` でだけ読む。ほかの経路で渡されたら止める。このフラグが
    # 無かったころは argparse が知らないフラグとして止めていたので、落として先へ進めると
    # `ticket start X --limit 3` のような打ち間違いが通るようになる（`--flow` は診断の中の
    # 差し替えで、落としても何も動かないので落とすだけにしている）。
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
            stderr.write(f"ccnavi: {flag} は <名前>=<パス> の形で書く\n")
            return EXIT_ERROR
        swaps[name] = os.path.abspath(path)

    conf.integration_branch = args.integration_branch.strip()

    # フローの確かめは `--lint` だけが読む。判定にも採点にも影響しないが、ほかの差し替えと同じく
    # 診断の外では落として言う。診断でも `--lint` でなければ読む先が無いので、そう言って落とす。
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
    # ユーザが端末から打って「このルールは何に当たるのか」を確かめるための場所で、
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
        return cli_args._prune(stdin, stdout, stderr, conf, root, args.preview)

    # 親の提案の計画を書き換える補助の経路。`--agree` の端末の確かめ（`_from_terminal`）より
    # 手前で分ける。書くのは承認待ちの親の提案（エージェントも書ける置き場）だけで、承認は
    # しない。`--agree` と一緒には受けない（組み込みの止めと経路が食い違わないように）。
    if args.plan_order is not None or args.fill_phases:
        if args.agree:
            stderr.write("ccnavi: --plan-order は --agree と一緒に使えない（承認の外の補助）\n")
            return EXIT_ERROR
        if args.plan_order is None:
            stderr.write("ccnavi: --fill-phases は --plan-order <親> と一緒に使う\n")
            return EXIT_ERROR
        if not args.fill_phases:
            stderr.write(
                "ccnavi: --plan-order <親> は --fill-phases と一緒に使う"
                "（計画が使う定義だけを phases.yml から親の提案の phases: に写す）\n"
            )
            return EXIT_ERROR
        if not conf.tickets_enabled:
            stderr.write(f"ccnavi: チケット制御が disable（{settings.TICKET_CONTROL_ENV}）\n")
            return EXIT_ERROR
        return plan_order.fill_phases(stdout, stderr, conf, root, args.plan_order.strip())

    # 承認の経路。ユーザが端末から打つもので、payload を読まないのでここで分かれる。
    # 判定を 1 度も通らないのも分ける理由で、承認はツール呼び出しについての
    # 判断ではなく、これから判定に使われる範囲についての合意になる。
    if args.agree:
        if not conf.tickets_enabled:
            stderr.write(f"ccnavi: チケット制御が disable（{settings.TICKET_CONTROL_ENV}）\n")
            return EXIT_ERROR
        if args.preview and args.yes:
            stderr.write("ccnavi: --preview と --yes は同時に付けられない\n")
            return EXIT_ERROR
        # 確かめるだけの枝は `--preview` と一緒に使う。単独で打てる形にすると、組み込みの
        # deny（phase_forms.ticket_approval_rule）が免除するのは `--preview` の付いた `--agree`
        # だけなので、エージェントが打てないものを案内することになる。
        if args.verify and not args.preview:
            stderr.write("ccnavi: --verify は --agree --preview と一緒に使う\n")
            return EXIT_ERROR
        # 承認できる状態かを確かめるだけ。置かないのは `--preview` と同じで、違うのは
        # 通るかどうかを終了コードで返すところ（REQ-APV-13）。答えは 0（はい）と
        # 3（いいえ）で、使い方と設定の誤りの 1 とは分ける。
        if args.verify:
            return core.verify(stdout, stderr, conf, root, args.json, list(args.command))
        # 見るだけの経路。承認済みチケットを置かないので端末の確認は要らない。後ろに並べた語は
        # `--agree` と同じで、承認の対象に入れる識別子（ボードの絞り込みで見えている分）。
        if args.preview:
            code = core.preview(stdout, stderr, conf, root, args.json, list(args.command))
            return EXIT_OK if code == 0 else EXIT_ERROR
        # 拡張のオーバーレイでユーザが押した承認。端末の確認の代わりに、見せた一覧と今の一覧が
        # 同じであることを求める。エージェントがこれを Bash で打つ形は組み込みの
        # deny（phase_forms.ticket_approval_rule）が止める。
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
        if not cli_args._from_terminal(stdin, conf, stderr, "--agree"):
            return EXIT_ERROR
        history.set_via(history.VIA_TERMINAL)
        # `--agree` の後ろに並べた語は、承認の対象に入れる識別子。無ければ承認待ち全部。
        approved = core.approve(stdin, stdout, stderr, conf, root, only=list(args.command))
        return EXIT_OK if approved == 0 else EXIT_ERROR

    # チケットの状態とレビューの操作。payload を読まない。
    # 取り込みの sh（`ccnavi-sync.sh`）がパスを聞く経路。読むだけで、チケット制御の有無に依らない。
    if list(args.command) == ["sync", "paths"]:
        return EXIT_OK if cli_ops.sync_paths(stdout, root, conf) == 0 else EXIT_ERROR
    # 取り込みの後の検査（本物とする側とレイヤーの食い違い）。
    # error があれば sh が親子のチケットの取り込みを止める。
    if len(args.command) in (3, 4) and list(args.command[:2]) == ["sync", "check"]:
        repo = args.command[3] if len(args.command) == 4 else None
        code = cli_ops.sync_check(stdout, stderr, root, conf, args.command[2], repo)
        return EXIT_OK if code == 0 else EXIT_ERROR
    # C1（取り込み済みの親子のチケットの状態の操作を、取り込み・書く・コミット・push の 1 操作に
    # する sh）が聞くこと。読むだけで、何も書かない。
    if len(args.command) == 3 and list(args.command[:2]) == ["c1", "family"]:
        if not cli_ops._FAMILY.fullmatch(args.command[2]) or ".." in args.command[2]:
            stderr.write(f"ccnavi: c1 family の {args.command[2]!r} は識別子の形ではない\n")
            return EXIT_ERROR
        return EXIT_OK if c1.family(stdout, conf, root, args.command[2]) == 0 else EXIT_ERROR
    if len(args.command) in (3, 4) and list(args.command[:2]) == ["c1", "sort"]:
        since = args.command[3] if len(args.command) == 4 else ""
        if not cli_ops._FAMILY.fullmatch(args.command[2]) or ".." in args.command[2]:
            stderr.write(f"ccnavi: c1 sort の {args.command[2]!r} は識別子の形ではない\n")
            return EXIT_ERROR
        if since and not cli_args._revision_ok(since):
            stderr.write(f"ccnavi: c1 sort の版 {since!r} は読めない\n")
            return EXIT_ERROR
        code = c1.sort(stdout, stderr, conf, root, args.command[2], since)
        return EXIT_OK if code == 0 else EXIT_ERROR

    # issue・MR に紐づくブランチを探す。`ccnavi-branches.sh` が呼ぶ。
    # 読むだけで、何も書かない。
    # ホストの結果は sh が取ってきて `--result` で渡す（実行ファイルはネットワークに出ない）。
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

    if args.command or args.reviewed is not None or args.close_early:
        # 残った指摘を見せるだけの `--preview` と、オーバーレイで押した `--yes` は端末を求めない。
        # `--yes` を守るのは、見せた指摘のダイジェストの一致と、
        # シェルから打つ形を止める組み込みの deny。
        board = args.reviewed is not None and args.accept_unresolved and (args.preview or args.yes)
        if (
            args.reviewed is not None
            and not board
            and not cli_args._from_terminal(stdin, conf, stderr, "--reviewed")
        ):
            return EXIT_ERROR
        return cli_ops.operate(stdin, stdout, stderr, conf, root, args)

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
        # hook ではなく端末の前のユーザ。何も判定していないので何も記録しない。
        stderr.write(cli_usage.USAGE)
        return EXIT_ERROR
    except hookio.Unusable as exc:
        stderr.write(f"ccnavi: {exc}\n")
        diaglog.get("ccnavi", root).warn("hook の payload を読めない", mode=mode)
        cli_args._record(
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
    return code


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
