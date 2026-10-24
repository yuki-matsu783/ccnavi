"""`ccnavi user handoff`。保護対象の下書き（`wip/handoff/`）を元の場所へ取り込む入口。

    ccnavi user handoff [<相対パス>…] [--json]                                見るだけ
    ccnavi user handoff [<相対パス>…] --yes --expect <ハッシュ> [--via board] [--json]   取り込む

見るだけの形を含めて、ユーザ専用（`user` の入口。hook が止める）。`--yes` で書くときは、標準入力が
ターミナルであることを求める。`--via board` はボードの子プロセスの経路で、
ターミナルを求めない代わりに
`--expect` と `--json` が揃うことと、選んだ種類がボードから取り込めること（`defense` は断る）を
求める。
取り込みの中身は `tickets.handoff`。cli を読まない。
"""

from __future__ import annotations

import argparse
import json
from typing import TextIO

from ..infra import settings
from ..infra.modes import EXIT_ERROR, EXIT_OK
from ..tickets import handoff, user_only, via
from . import cli_args

# `--handoff` と一緒に読むオプション（argparse の dest）。ほかのオプションが既定の値でなければ断る。
# 先頭の 4 つはラッパーが付けるもの、`--mode` から後ろは設定に重ねる切り替え。
_WITH_HANDOFF = frozenset(
    {
        "root",
        "cwd",
        "record_writes",
        "record_tree",
        "mode",
        "restore_if_deny",
        "guard_core_files",
        "guard_ticket_approval",
        "ticket_control",
        "handoff",
        "command",
        "yes",
        "expect",
        "via",
        "json",
    }
)


def usage_problem(parser: argparse.ArgumentParser, args: argparse.Namespace) -> str:
    """`--handoff` の組み合わせの誤り。正しければ空。設定を読む前に見る。"""
    for action in parser._actions:
        if action.dest in _WITH_HANDOFF or not action.option_strings:
            continue
        if getattr(args, action.dest, action.default) != action.default:
            return f"{action.option_strings[-1]} は user handoff と一緒には使えません"
    if args.yes not in ("", cli_args.BARE_YES):
        return "--yes には値を付けられません（user handoff <パス>… --yes --expect <ハッシュ>）"
    if args.expect is not None and not args.yes:
        return "--expect は --yes と一緒に指定してください"
    if args.via and args.via != via.VIA_BOARD:
        return "--via に渡せるのは board だけです"
    if args.via and (not args.yes or args.expect is None or not args.json):
        return "--via board は --yes --expect <ハッシュ> --json と一緒に指定してください"
    return ""


def _fail(stdout: TextIO, stderr: TextIO, as_json: bool, error: str) -> int:
    if as_json:
        answer = {"version": handoff.JSON_VERSION, "ok": False, "error": error}
        stdout.write(json.dumps(answer, ensure_ascii=True, indent=1))
        stdout.write("\n")
    else:
        stderr.write(f"ccnavi: {error}\n")
    return EXIT_ERROR


def run(
    stdin: TextIO,
    stdout: TextIO,
    stderr: TextIO,
    parser: argparse.ArgumentParser,
    conf: settings.Settings,
    root: str,
    args: argparse.Namespace,
) -> int:
    """保護対象の下書きを、見るだけか、`--yes` で取り込むかを決めて実行する。

    処理フロー:
    1. `usage_problem` で組み合わせを検査する。
    2. `--yes` のときは、経路を決めてから取り込む。
    ボードの経路（`--via board`）はターミナルを求めず、
       ターミナルの経路は `cli_args._from_terminal` で、ターミナルの前のユーザであることを確かめる。
       `handoff.prepare` で計画を作り、`handoff.apply` で `--expect` の一致を見ながら書く。
    3. `--yes` がないときは、`handoff.prepare` で計画を作って表示する（書かない）。
    4. `--json` のときは、取り込みか計画を JSON で出す。そうでなければ `_applied_text` か
       `_plan_text` で出す。

    終了コードは、取り込めたか（または計画を出せたか）で `EXIT_OK`、失敗で `EXIT_ERROR`。
    """
    refused = usage_problem(parser, args)
    if refused:
        return _fail(stdout, stderr, args.json, refused)
    wanted = list(args.command) or None
    if args.yes:
        board = args.via == via.VIA_BOARD
        if board:
            via.set_via(via.VIA_BOARD)
        elif not cli_args._from_terminal(stdin, conf, stderr, user_only.terminal("handoff")):
            return EXIT_ERROR
        else:
            via.set_via(via.VIA_TERMINAL)
        plan = handoff.prepare(conf, root, wanted)
        done = handoff.apply(plan, args.expect or "", via_board=board)
        if args.json:
            stdout.write(json.dumps(handoff.applied_json(done), ensure_ascii=True, indent=1))
            stdout.write("\n")
            return EXIT_OK if done.ok else EXIT_ERROR
        if not done.ok:
            stderr.write(f"ccnavi: {done.error}\n")
            return EXIT_ERROR
        _applied_text(stdout, done)
        return EXIT_OK
    plan = handoff.prepare(conf, root, wanted)
    if plan.error:
        return _fail(stdout, stderr, args.json, plan.error)
    if args.json:
        stdout.write(json.dumps(handoff.plan_json(plan), ensure_ascii=True, indent=1))
        stdout.write("\n")
        return EXIT_OK
    _plan_text(stdout, plan, wanted)
    return EXIT_OK


def _plan_text(stdout: TextIO, plan: handoff.Plan, wanted: list[str] | None) -> None:
    """取り込みの計画を人が読む形で書く。何も反映せず、次に打つコマンドを添える。

    下書きの 1 件ごとに、選ばれたか・状態・error と warn・ターミナルのみの理由・conflict を出す。
    選ばれた下書きが無いか、error か conflict があれば、取り込めない理由を言って終える。
    取り込めるときだけ、`--expect` の値を含む `ccnavi user handoff` の行を出す。
    ボードからは取り込めない種類を含むときは、ターミナルで実行するよう添える。
    """
    stdout.write(f"ccnavi: 保護対象の下書き（{plan.tree} の {handoff.HANDOFF_REL}/）\n")
    if not plan.entries:
        stdout.write("  取り込める下書きはありません。\n")
    for entry in plan.entries:
        mark = "*" if entry.selected else " "
        stdout.write(f"{mark} [{entry.state}] {entry.rel}（{entry.kind.id}）\n")
        for line in entry.errors:
            stdout.write(f"    error {line}\n")
        for line in entry.warnings:
            stdout.write(f"    warn  {line}\n")
        if entry.defense:
            stdout.write(f"    ターミナルのみ {entry.defense}。ボードからは取り込めません\n")
        if entry.conflict:
            stdout.write(
                f"    conflict コピーしたあとに取り込み先が変わりました"
                f"（コピーした時点の元は {handoff.ORIG_SUFFIX}）。"
                "下書きに今の取り込み先の変更を入れてから、下書きと .orig を作り直してください\n"
            )
        if entry.selected and entry.diff:
            for line in entry.diff.split("\n"):
                stdout.write(f"    {line}\n")
    if plan.ignored:
        stdout.write(f"対象外: {plan.ignored} 件（取り込める種類の表にヒットしない）\n")
    chosen = plan.selected
    if not chosen:
        stdout.write("選んだ対象はありません。何も反映していません。\n")
        return
    if not plan.ok:
        stdout.write(
            "選んだ対象に error か conflict があるため、取り込めません。"
            "直すか、パスを選び直してください。何も反映していません。\n"
        )
        return
    paths = " ".join(e.rel for e in chosen) if wanted else ""
    command = "ccnavi user handoff " + (paths + " " if paths else "")
    stdout.write("何も反映していません。この内容で取り込むなら、次を実行します。\n")
    stdout.write(f"  {command}--yes --expect {plan.expect}\n")
    if any(not e.board for e in chosen):
        stdout.write(
            "  （defense の種類か、防御の記述を動かす rules.yml を含むため、ボードからは"
            "取り込めません。ターミナルで実行してください）\n"
        )


def _applied_text(stdout: TextIO, done: handoff.Applied) -> None:
    for entry in done.applied:
        stdout.write(f"取り込んだ: {entry.rel}（{entry.kind.id}）\n")
    for path in done.removed_drafts:
        stdout.write(f"下書きを削除しました: {path}\n")
    for path, reason in done.kept_drafts:
        stdout.write(f"下書きを残しました: {path}（{reason}）\n")
