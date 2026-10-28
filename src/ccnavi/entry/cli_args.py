"""引数の読み分け。

設定を上書きするオプション・診断だけのオプション・`--docs` と並べられないオプションと、
パスの見分け。

cli から分けた。cli を読まない。
"""

from __future__ import annotations

import argparse
import os
import re
from typing import TextIO

from ..catalog import docsearch, docsearch_query
from ..infra import fsio, pathcase, settings
from ..infra.modes import EXIT_ERROR, EXIT_OK
from ..policy import selfguard
from ..records import audit, prune
from ..tickets.model import ticket_ids, user_only
from . import cli_usage

# フラグで上書きする設定の欄と、空文字を「指定した」と読むかどうか。
# 空文字を受ける欄は、既定が None のフラグで受け渡す。「記録しない」「state の置き場を持たない」を
# 言えないと、診断のための 1 回が、走っているセッションの記録と state に必ず入り込む。
OVERRIDES = (
    ("log", True),
    ("rules", False),
    ("state", True),
    ("approved", False),
    ("phases", True),
    ("risk", True),
    ("projects", True),
)
# ワークツリーのルートからの相対で書く欄。区切りを "/" に揃え、前後の "/" を除く。
RELATIVE_OVERRIDES = ("tickets", "project_home")
# レイヤーの置き場を動かすフラグと、「渡されなかった」ときの値。`--project-rules-file` と
# 同じで診断の経路でだけ使われる。3 つめの欄が既定なのは、渡されたかどうかを OVERRIDES /
# RELATIVE_OVERRIDES と同じ読み方で決めるため（`--rules ""` は指定と数えず、
# `--risk ""` は数える）。
#
# 前の 3 本は共通レイヤーの中身（ルール・フェーズ定義・リスクの配点）、後の 2 本は
# レイヤーを探す先。`--projects` はプロジェクトのレイヤーの置き場、`--project-home` は
# 各 git プロジェクトルートの下の ccnavi ディレクトリの名前で、どちらも外すと
# プロジェクトのレイヤーがまるごと消える。実際に試すと `ticket finish <子> --project-home .nothere`
# で、実績リスク 55 (CRITICAL) の子が 25 (MEDIUM) になり、レビュー待ちを飛ばして
# 閉じた。中身を差し替えるのと結果が同じなので、同じ制限に載せる。
LAYER_OVERRIDES = (
    ("--rules", "rules", ""),
    ("--phases", "phases", None),
    ("--risk", "risk", None),
    ("--projects", "projects", None),
    ("--project-home", "project_home", ""),
)
# 記録と状態の置き場を動かすフラグと、「渡されなかった」ときの値。レイヤーの置き場と同じく
# 診断の経路でだけ使われる。`--log` / `--state` は判定の記録と実行後チェックの記録を止めたり
# 別の場所へ逃がしたりでき、`--approved` / `--tickets` は承認済みチケットと提案の置き場を
# 差し替えられる。sh（`ccnavi-ticket.sh` / `ccnavi-review.sh`）は受け取った引数を `"$@"` で
# そのまま渡すので、診断の外で受けると、エージェントが記録を消した形や置き場を差し替えた形で
# チケットとレビューのサブコマンドを動かせる。
PLACE_OVERRIDES = (
    ("--log", "log", None),
    ("--state", "state", None),
    ("--approved", "approved", None),
    ("--tickets", "tickets", ""),
)
# 無視したときの文面。置き場を動かすフラグで同じものを使う。制限が 2 つあるように読ませない。
DIAGNOSIS_ONLY = "ccnavi: {flag} は診断時のみ使用できます。今回の指定は無視されます。\n"
# `.ccnavi/scripts/` の sh が自分で計算して渡すパスと、渡されなかったときの値。
# どちらも「いまどこで動いているか」で、エージェントが申告するものではない。
#
# sh は自分のぶんを先に置き、エージェントの引数を後ろに繋ぐ
# （`exec "$bin" --root "$root" ticket "$@"`）。argparse は同じオプションを後勝ちで読むので、
# 後ろに 1 本足すだけで sh が渡した正しい値を上書きできた。`--root` は共通レイヤーの 3 本も
# `projects` も `approved` もそこから導かれる（`settings.load`）ので、1 本で全部動く。
# 実際に試すと、実際のツリーへシンボリックリンクを張った偽のルートを渡すと、子チケットが
# 実際の置き場に「リスク 0」で閉じられた。
#
# 正しい 1 本が先に在ることに頼らず、2 本目が在ること自体を断る。無視して先へ進むのでは
# なく止めるのは、この 2 つに「2 度渡す」正しい使い方が無いから。診断の 5 本と違って、
# 使われる経路の話ではない。
WRAPPER_FLAGS = (("--root", "root", None), ("--cwd", "cwd", ""))


def _one_wrapper_flag_each(stderr: TextIO, args: argparse.Namespace) -> bool:
    """sh が渡すパスが 2 度来ていないかを見て、1 本にまとめる。2 度来ていたら False。

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


def _diagnosing(args: argparse.Namespace) -> bool:
    """診断の経路か。置き場を動かすフラグはここでだけ効く。

    診断は payload を読まず、判定を実行にも記録にも繋げないので、保存していない設定を
    指しても実運用に影響しない。
    """
    return bool(
        args.lint
        or args.test is not None
        or bool(args.test_expectations)
        or args.explain
        or args.suggest
    )


def _drop_outside_diagnosis(stderr: TextIO, args: argparse.Namespace) -> None:
    """診断の外で渡された置き場の差し替え（レイヤー・記録・状態・チケット）を、
    標準エラーに出して無視する。

    フラグは設定ファイルより強いので、無視しないと保存していない `rules.yml` /
    `phases.yml` / `risks.yml` で判定と採点が走り、レイヤーそのものも外せる。届く経路は
    `.ccnavi/scripts/` の sh で、受け取った引数を実行ファイルへそのまま渡す。だからレイヤーを動かす
    フラグは診断の経路（`--lint`・`--test` など）でだけ受ける。
    """
    for flag, name, absent in (*LAYER_OVERRIDES, *PLACE_OVERRIDES):
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


def _real(path: str) -> str:
    try:
        return os.path.realpath(path)
    except OSError:
        return os.path.normpath(os.path.abspath(path))


def _same(a: str, b: str) -> bool:
    return pathcase.fold(os.path.normpath(a)) == pathcase.fold(os.path.normpath(b))


def _inside(path: str, base: str) -> bool:
    """`path` が `base` の下（か同じ）か。

    どちらも行き着く先のパスで渡す。大文字小文字は区別しない。
    """
    path = pathcase.fold(os.path.normpath(path))
    base = pathcase.fold(os.path.normpath(base))
    return path == base or path.startswith(base.rstrip(os.sep) + os.sep)


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
DOCS_ONLY = "ccnavi: {flag} は --docs でだけ指定できる\n"
# 値なしで渡された `--yes`。取り込み（`user handoff --yes`）とミラーの同期
# （`user mirror --yes`）だけが読む（ほかの経路の `--yes` は識別子を取る）。
BARE_YES = "\x00bare"
# `--docs` と同時に指定できないフラグ。ほかの経路と、その経路でだけ読むもの。
# 何も出さずに無視すると、打ったユーザは反映されたと思う。
_NOT_WITH_DOCS = (
    "--lint",
    "--learnings",
    "--handoff",
    "--mirror",
    "--test",
    "--test-expectations",
    "--explain",
    "--suggest",
    "--prune",
    "--agree",
    "--reviewed",
    "--close-early",
    "--yes",
    "--preview",
    "--verify",
    "--digest",
    "--result",
    "--tickets",
    "--phase",
    "--abandon",
    "--body-file",
    "--reason",
    "--note",
    "--target",
    "--signal",
    "--accept-unresolved",
    "--flow",
    "--project-rules-file",
    "--project-phases-file",
    "--project-risk-file",
    # 判定・チケット・レビューの経路の設定と、sh が渡すパス。`--docs` は読まない。
    # レイヤーの置き場（`--rules` から `--project-home`）は診断の外では無視して先へ進むが、
    # `--docs` で無視すると「そのプロジェクトの置き場で引いた」と読まれるので止める。
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
    "--permission-mode",
    "--agent-id",
    "--record-writes",
    "--record-tree",
    "--choose-out",
    "--accept-plan",
    "--accept-done",
    "--accept-check",
    "--placed-out",
    "--actor",
    "--via",
)
# 空文字も「渡した」と数えるフラグ（既定が None で、空に意味があるもの）。
_EMPTY_COUNTS = ("approved", "phases", "risk", "projects")


def _docs_flags_given(args: argparse.Namespace) -> list[str]:
    """渡された `--docs` 用のフラグ。"""
    return [flag for flag, name, absent in DOCS_FLAGS if getattr(args, name) != absent]


def _not_with_docs(args: argparse.Namespace, own: str = "") -> str:
    """`--docs` に添えられた、ほかの経路のフラグ（最初の 1 つ）。無ければ空。

    `own` は、いま調べている経路自身のフラグ（`--learnings`）。数えない。
    """
    for flag in _NOT_WITH_DOCS:
        if flag == own:
            continue
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
    query = docsearch_query.Query(
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
    """記録と state の後始末を 1 度走らせ、動かしたものを出す。

    消すのは記録なので、`--preview`（見るだけ）でなければターミナルを求める。エージェントが
    Bash から打てると、しきい値の環境変数を 0 に近づけて前に並べるだけで、自分の呼び出しの
    記録を消せる。
    """
    if not preview and not _from_terminal(stdin, conf, stderr, user_only.terminal("prune")):
        return EXIT_ERROR
    report = prune.run(root, conf.log, conf.state, dry_run=preview)
    for line in prune.lines(report, preview):
        stdout.write(line + "\n")
    for problem in report.problems:
        stderr.write(f"ccnavi: {problem}\n")
    return EXIT_OK


def _from_terminal(stdin: TextIO, conf: settings.Settings, stderr: TextIO, flag: str) -> bool:
    """ユーザの判断の経路が、ターミナルの前のユーザから打たれているか。

    `user agree`・`user reviewed`・`user close-early`（内部は `--agree` 等）は
    ユーザの合意そのもの。エージェントが
    Bash から打てばその合意を自分で出せる。標準入力がターミナルであることを求めるのが、この経路が
    hook の中や `echo y |` から来ていないことの、いちばん手間の少ない証拠になる。
    CCNAVI_GUARD_TICKET_APPROVAL=disable で切れる（テストと、ターミナルを持たない実行環境のため）。
    """
    if conf.guard_ticket_approval == selfguard.DISABLE:
        return True
    try:
        if stdin.isatty():
            return True
    except (AttributeError, ValueError):
        pass
    # 切り方（CCNAVI_GUARD_TICKET_APPROVAL）は文面に書かない。読むのはエージェントで、書けば
    # ユーザの判断を自分で出す方法を教えることになる。
    # 切り方は docs/41_guide/settings.md の環境変数の表にある。
    stderr.write(
        f"ccnavi: {flag} はユーザがターミナルから実行する必要があります。"
        "標準入力がターミナルではないため、実行できません。"
        "ユーザにターミナルでの実行を依頼してください。\n"
    )
    return False


# `write-and-push sort` の版。sha か `refs/remotes/origin/<親のブランチ>` の形だけ（git
# の引数として読まれる
# 文字列を入れない）。親のブランチは識別子か、親チケットの `branch:` に書ける名前。
_SHA = re.compile(r"[0-9a-f]{7,64}")
_ORIGIN_REF = "refs/remotes/origin/"


def _revision_ok(since: str) -> bool:
    if _SHA.fullmatch(since):
        return True
    if not since.startswith(_ORIGIN_REF):
        return False
    name = since[len(_ORIGIN_REF) :]
    return ticket_ids.is_valid_id(name) or not ticket_ids.branch_problem(name)


def _record(stderr: TextIO, log: audit.Log, record: audit.Record) -> None:
    """1 行を書く。書けなかったことは報告して破棄する。
    書けなかったことでツールの実行可否を変えてはいけない。"""
    try:
        log.write(record)
    except OSError as exc:
        stderr.write(f"ccnavi: ログを保存できませんでした：{exc}\n")


# `--help` の話題を決めるオプション。先頭から順に見て、最初にヒットしたものを使う。
_HELP_FLAGS = (
    ("version", lambda a: a.version),
    ("lint", lambda a: a.lint),
    ("test", lambda a: a.test is not None or bool(a.test_expectations)),
    ("explain", lambda a: a.explain),
    ("docs", lambda a: a.docs),
    ("templates", lambda a: a.templates),
    ("learnings", lambda a: a.learnings),
    ("logs", lambda a: a.prune or a.suggest),
    ("agree", lambda a: a.agree or a.plan_order is not None or a.fill_phases),
    (
        "scripts",
        lambda a: (
            a.reviewed is not None
            or a.close_early
            or any(
                getattr(a, flag)
                for flag in (
                    "record_writes",
                    "placed_out",
                    "choose_out",
                    "accept_plan",
                    "accept_check",
                    "accept_done",
                    "no_issue",
                    "via",
                )
            )
        ),
    ),
)


def help_text(args: argparse.Namespace) -> str:
    """`--help` の本文を返す。`--all` なら全文。
    話題のオプションか語（`--help docs`・`ticket --help`）があれば、その節を返す。
    なければ話題の一覧を返す。"""
    if args.all:
        return cli_usage.USAGE
    for name, given in _HELP_FLAGS:
        if given(args):
            return cli_usage.topic(name)
    if not args.command:
        return cli_usage.OVERVIEW
    word = cli_usage.TOPIC_WORDS.get(args.command[0])
    if word:
        return cli_usage.topic(word)
    unknown = f"ccnavi: 話題 '{args.command[0]}' はありません。話題は次のとおりです。"
    return f"{unknown}\n\n{cli_usage.OVERVIEW}"
