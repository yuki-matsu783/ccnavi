"""`ccnavi user mirror`。プロジェクトのミラーを、ワークスペースの共通レイヤーに揃える入口。

    ccnavi user mirror [<プロジェクト名>…] [--json]                                見るだけ
    ccnavi user mirror [<プロジェクト名>…] --yes --expect <値> [--via board] [--json]   揃える

見るだけの形を含めて、ユーザ専用（`user` の入口。hook が止める）。`--yes` で書くときは、標準入力が
ターミナルであることを求める。`--via board` は拡張の子プロセスの経路で、ターミナルを求めない代わりに
`--expect` と `--json` を付けて呼ぶことを求める。書くのはワークツリーだけで、コミットはしない。
中身は `tickets.mirrorsync`。cli を読まない。
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
from typing import TextIO

from ..infra import settings
from ..infra.modes import EXIT_ERROR, EXIT_OK
from ..tickets import mirrorsync, user_only, via
from . import cli_args

# `--mirror` と一緒に読むオプション（argparse の dest）。ほかのオプションが既定の値でなければ断る。
# 先頭の 4 つはラッパーが付けるもの、`--mode` から後ろは設定に重ねる切り替え。
_WITH_MIRROR = frozenset(
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
        "mirror",
        "command",
        "yes",
        "expect",
        "via",
        "json",
    }
)

_ACTION_TEXT = {
    mirrorsync.ACTION_CREATE: "追加",
    mirrorsync.ACTION_UPDATE: "更新",
    mirrorsync.ACTION_DELETE: "削除",
}


def usage_problem(parser: argparse.ArgumentParser, args: argparse.Namespace) -> str:
    """`--mirror` の組み合わせの誤り。正しければ空。設定を読む前に見る。"""
    for action in parser._actions:
        if action.dest in _WITH_MIRROR or not action.option_strings:
            continue
        if getattr(args, action.dest, action.default) != action.default:
            return f"{action.option_strings[-1]} は user mirror と同時には指定できません"
    if args.yes not in ("", cli_args.BARE_YES):
        return "--yes には値を付けられません（user mirror [<名前>…] --yes --expect <値>）"
    if args.expect is not None and not args.yes:
        return "--expect は --yes と一緒に指定してください"
    if args.yes and args.expect is None:
        return "--yes には --expect が必要です（user mirror を --yes なしで実行すると、値が出ます）"
    if args.via and args.via != via.VIA_BOARD:
        return "--via に渡せるのは board だけです"
    if args.via and (not args.yes or args.expect is None or not args.json):
        return "--via board は --yes --expect <値> --json と一緒に指定してください"
    return ""


def _fail(stdout: TextIO, stderr: TextIO, as_json: bool, error: str) -> int:
    if as_json:
        answer = {"version": mirrorsync.JSON_VERSION, "ok": False, "error": error}
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
    """ミラーの状態を見るだけか、`--yes` で揃えるかを決めて実行する。

    処理フロー:
    1. `usage_problem` で組み合わせを検査する。
    2. `--yes` のときは、経路を決めてから揃える。ボードの経路（`--via board`）はターミナルを求めず、
       ターミナルの経路は `cli_args._from_terminal` で、
       ターミナルの前にいるユーザであることを確かめる。
       `mirrorsync.prepare` で今の差分を求め、`mirrorsync.apply` が `--expect` の一致を見て書く。
    3. `--yes` がないときは、`mirrorsync.prepare` の結果を表示する（書かない）。
    4. `--json` のときは JSON で、そうでなければ人が読む形で出す。

    終了コードは、揃えられたか（または状態を出せたか）で `EXIT_OK`、失敗で `EXIT_ERROR`。
    """
    refused = usage_problem(parser, args)
    if refused:
        return _fail(stdout, stderr, args.json, refused)
    wanted = list(args.command) or None
    if args.yes:
        board = args.via == via.VIA_BOARD
        if board:
            via.set_via(via.VIA_BOARD)
        elif not cli_args._from_terminal(stdin, conf, stderr, user_only.terminal("mirror")):
            return EXIT_ERROR
        else:
            via.set_via(via.VIA_TERMINAL)
        plan = mirrorsync.prepare(conf, root, wanted)
        done = mirrorsync.apply(conf, plan, args.expect or "")
        if args.json:
            stdout.write(json.dumps(mirrorsync.applied_json(done), ensure_ascii=True, indent=1))
            stdout.write("\n")
            return EXIT_OK if done.ok else EXIT_ERROR
        if not done.ok:
            stderr.write(f"ccnavi: {done.error}\n")
            if done.synced:
                _applied_text(stdout, done)
            return EXIT_ERROR
        _applied_text(stdout, done)
        return EXIT_OK
    plan = mirrorsync.prepare(conf, root, wanted)
    if plan.error:
        return _fail(stdout, stderr, args.json, plan.error)
    if args.json:
        stdout.write(json.dumps(mirrorsync.plan_json(plan), ensure_ascii=True, indent=1))
        stdout.write("\n")
        return EXIT_OK
    _plan_text(stdout, plan, wanted, root)
    return EXIT_OK


def _plan_text(stdout: TextIO, plan: mirrorsync.Plan, wanted: list[str] | None, root: str) -> None:
    """ミラーの状態を人が読む形で書く。何も反映せず、揃えるときに実行するコマンドを添える。"""
    stdout.write(
        "ccnavi: 共通レイヤーのミラー（各プロジェクトの .ccnavi/config/common/）と"
        "工程スキル（skills/）\n"
    )
    if not plan.projects:
        stdout.write("  プロジェクトはありません。\n")
        return
    if plan.common_empty:
        stdout.write(
            "  共通レイヤーにコピーするものがありません。ミラーは消さず、何も揃えません。\n"
        )
    for project in plan.projects:
        if not project.selected:
            continue
        if project.state == mirrorsync.STATE_SYNCED:
            stdout.write(f"  [同期済み] {project.name}\n")
        elif project.state == mirrorsync.STATE_ERROR:
            stdout.write(f"  [調べられない] {project.name}: {project.error}\n")
        else:
            stdout.write(f"  [ずれ {len(project.changes)} 件] {project.name}\n")
            for change in project.changes:
                stdout.write(f"      {_ACTION_TEXT[mirrorsync.action_of(change)]} {change.rel}\n")
            if project.mirror_path and plan.common_path:
                if any(not mirrorsync.is_skill(c) for c in project.changes):
                    stdout.write(
                        "      .ccnavi/ の中身の差を見るには: diff -ru "
                        f"{shlex.quote(project.mirror_path)} {shlex.quote(plan.common_path)}\n"
                    )
                if any(mirrorsync.is_skill(c) for c in project.changes):
                    stdout.write(
                        "      skills/ の元は、ワークスペースの .claude/skills/<工程>/ の"
                        " SKILL.md と references/common/。"
                        f"{shlex.quote(os.path.join(project.root, 'skills'))} と見比べる\n"
                    )
    chosen = plan.chosen
    if not chosen:
        stdout.write("揃えるミラーはありません。\n")
        return
    names = " ".join(shlex.quote(p.name) for p in chosen) if wanted else ""
    script = settings.script_command(root, "ccnavi-user-mirror.sh")
    command = f"{script} " + (names + " " if names else "")
    stdout.write(
        "何も反映していません。この内容で揃えるなら、次を実行します。ワークツリーに書くだけで、"
    )
    stdout.write("コミットはしません。\n")
    stdout.write(f"  {command}--yes --expect {plan.expect}\n")
    if any(mirrorsync.action_of(c) != mirrorsync.ACTION_CREATE for p in chosen for c in p.changes):
        stdout.write(
            "更新と削除は、ミラーの今の中身を共通レイヤーの中身で上書き・削除します。"
            "ミラーや skills/ に手を入れていたなら、上の案内で確かめてから実行します。\n"
        )


def _applied_text(stdout: TextIO, done: mirrorsync.Applied) -> None:
    for name, files in done.synced:
        stdout.write(f"揃えた: {name}（{len(files)} 件。コミットはしていません）\n")
        for rel in files:
            stdout.write(f"  {rel}\n")
