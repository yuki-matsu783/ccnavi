"""チケットとレビューの副命令（`ticket` / `review` / `c1` など）を ops・review へ渡す。

`sync` の問い合わせ（`sync paths` / `sync check`）もここで答える。

cli から分けた。cli を読まない。
"""

from __future__ import annotations

import argparse
import os
import re
from typing import TextIO

from ..hook import c1, core
from ..infra import fsio, settings
from ..infra.modes import EXIT_ERROR, EXIT_OK
from ..tickets import (
    history,
    ops,
    phase,
    review,
    review_close,
    review_decide,
    ticket_ids,
    worktrees,
)
from . import cli_args, cli_usage, lint, status


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
    ユーザが打つ `--reviewed` と `--close-early`。どれも payload を読まず、判定も記録もしない。
    リモートから取得した結果は `--result <json>` で受け取る。exe はネットワークに出ない。
    """
    cwd = args.cwd or os.getcwd()
    words = list(args.command)
    if words[:1] == ["worktree"]:
        # ワークツリーの片付けはチケット制御が無くても打てる（ccnavi-clean.sh --worktree の中身）。
        # tidy はチケットを読むので、チケット制御が要る。
        return _worktree(stdout, stderr, conf, root, args, words, cwd)
    if not conf.tickets_enabled:
        stderr.write(f"ccnavi: チケット制御が disable（{settings.TICKET_CONTROL_ENV}）\n")
        return EXIT_ERROR
    bypass = _c1_bypass(root, conf, args, cwd)
    if bypass:
        stderr.write(
            f"ccnavi: 親子のチケット {bypass} は取り込み済み（C1 の対象）。状態の操作は "
            f"'{settings.script_command(root, 'ccnavi-ticket.sh')}' か "
            f"'{settings.script_command(root, 'ccnavi-review.sh')}' から打ってください。"
            "C1 を通らない書き込みは親のブランチへ送られないので、何も書かずに止めた\n"
        )
        return EXIT_ERROR
    if args.close_early:
        if not args.result:
            stderr.write("ccnavi: --close-early には --reason <理由> と --result <json> が要る\n")
            return EXIT_ERROR
        # ユーザの判断。`--agree` / `--reviewed` と同じく端末を求める。
        if not cli_args._from_terminal(stdin, conf, stderr, "--close-early"):
            return EXIT_ERROR
        history.set_via(history.VIA_TERMINAL)
        code = review_close.close_early(
            stdin, stdout, stderr, root, conf, cwd, args.reason, args.result
        )
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
            code = review_decide.decide_preview(
                stdout, stderr, root, conf, cwd, args.reviewed, args.result
            )
        else:
            code = review_decide.decide_yes(
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
        # 対話の decide の前半（ロックを持ったままユーザを待たないため分ける）。ユーザが端末で選び、
        # 選択とダイジェストを state の置き場に書くだけ。置くのは sh が C1 の中で
        # `--yes <選択> --digest <ダイジェスト>` で打つ。
        if not cli_args._inside(
            cli_args._real(os.path.abspath(args.choose_out)), cli_args._real(conf.state)
        ):
            stderr.write("ccnavi: --choose-out の書き出し先は state の置き場の下だけ\n")
            return EXIT_ERROR
        code = review_decide.choose(
            stdin, stdout, stderr, root, conf, cwd, args.reviewed, args.result, args.choose_out
        )
        return EXIT_OK if code == 0 else EXIT_ERROR
    if args.reviewed is not None:
        history.set_via(history.VIA_TERMINAL)
        _decide_actor(args)
        code = review_decide.reviewed(
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

    kind = words[0] if words else ""
    verb = words[1] if len(words) > 1 else ""
    target = words[2] if len(words) > 2 else ""
    code = 1
    if kind == "ticket" and verb == "status":
        # ticket status [<親>]。読むだけ（サブエージェントにも許す）。
        if len(words) > 3:
            stderr.write("ccnavi: ticket status に渡せる親は 1 つだけ\n")
        else:
            code = status.run(stdout, stderr, root, conf, c1.family_of(target) if target else "")
    elif kind == "ticket" and verb in ("start", "finish", "cancel") and target:
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
            code = review_close.ready(stdout, stderr, root, conf, cwd, args.result)
    else:
        stderr.write(cli_usage.USAGE)
    return EXIT_OK if code == 0 else EXIT_ERROR


def _worktree(
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    args: argparse.Namespace,
    words: list[str],
    cwd: str,
) -> int:
    """`worktree drop <名前>` と `worktree tidy <親> [--phase <N>]`（worktrees.py）。

    sh が状態を書く操作の後に呼ぶ。名前は識別子の形だけを受ける（パスを渡して任意の場所を
    消させない）。
    """
    verb = words[1] if len(words) > 1 else ""
    if verb not in ("drop", "tidy") or len(words) != 3:
        stderr.write("ccnavi: worktree drop <名前> か worktree tidy <親> [--phase <N>]\n")
        return EXIT_ERROR
    target = words[2]
    if not ticket_ids.is_valid_id(target):
        stderr.write(f"ccnavi: worktree {verb} の {target!r} は識別子の形ではない\n")
        return EXIT_ERROR
    if verb == "drop":
        code = worktrees.run_drop(stdout, stderr, root, target, cwd)
    elif not conf.tickets_enabled:
        stderr.write(f"ccnavi: チケット制御が disable（{settings.TICKET_CONTROL_ENV}）\n")
        return EXIT_ERROR
    else:
        code = worktrees.run_tidy(stdout, stderr, root, conf, target, args.phase, cwd)
    return EXIT_OK if code == 0 else EXIT_ERROR


def _c1_bypass(root: str, conf: settings.Settings, args: argparse.Namespace, cwd) -> str:
    """C1 を通らずに、取り込み済みの親子のチケットの状態を書こうとしているなら、
    その親の識別子。無ければ空。

    状態の操作（`ticket start|finish|cancel`、`review requested|confirm|ready`、残った指摘の
    行き先の `--reviewed N --accept-unresolved`）は、C1 の対象の親子のチケットでは sh が
    `--record-tree` を付けて起こす。付いていなければ、sh の引数の読み違いや直打ちで
    C1 を通っていない。
    ユーザの判断の操作（`--reviewed --chat`・`--close-early`）と、
    書かない形（`--preview`・`--choose-out`・`review prepare`）は見ない。
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
    """`ccnavi-sync.sh` が要るパスと名前を 1 行 1 項目（`<鍵> <値>`）で返す。

    sh は jq を使わず、JSON も読まない。置き場のパス（ツリーのルートからの相対）と、
    `.claude/settings.local.json` の `env` に書かれた統合先の名前をここが読んで渡す。
    統合先の名前は環境変数からは読まない（sh が先に環境変数を見る）。
    置き場は固定（`settings.load` の既定）で、env では動かない。`ccnavi-sync.sh` は答えが
    無いとき同じ既定を直に使う。
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
    """decide のマーカーと履歴に入れるアカウントと経路。

    `--actor` があれば履歴の行にアカウントを足し、`--via` があれば経路を差し替える。マーカーの
    `actor`・`via` は `review_decide.apply_decision` がこの起動の値から書く。
    無ければマーカーも履歴も前と同じ。
    """
    if not args.actor:
        return
    history.set_actor(args.actor)
    if args.via:
        history.set_via(args.via)


class _Family:
    """親の識別子の形（`ticket_ids.is_valid_id` と同じ。日本語の字を含む）。

    sh から渡る引数なので、パスとして読まれる文字列を入れない。前は正規表現で持っていたので、
    呼び手が使う `fullmatch` の形を残す。
    """

    @staticmethod
    def fullmatch(text: str) -> bool:
        return ticket_ids.is_valid_id(text)


_FAMILY = _Family()
# リポジトリの取り込み状態を分ける名前（`self` かプロジェクト名）。ASCII の 1 語。
_REPO_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
# `--actor` の形。拡張がホストから読むアカウント名の形（`github.ts` の NAME）と同じ。
_ACTOR = re.compile(r"[A-Za-z0-9_.-]{1,100}")


# `sync check` の答えの頭の行。sh はこれが無ければ「検査を実行できなかった」（古い実行ファイルが
# 知らない副命令を断った、など）と読み、親子のチケットを止めない（検査の error と分ける）。
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

    error が 1 つでもあれば 1。sh は最初の error の中身を親子のチケットの取り込み状態の
    `reason` に書いて `blocked` にする。
    `repo` は取り込み状態の名前（`self` かプロジェクト名）。ネットワークにも git にも触らない。
    """
    for name, value, pattern in (
        ("識別子", family, _FAMILY),
        ("リポジトリ", repo or "self", _REPO_NAME),
    ):
        if not pattern.fullmatch(value) or ".." in value:
            stderr.write(f"ccnavi: sync check の{name} {value!r} は識別子の形ではない\n")
            return 1
    problems = lint.family_check(conf, root, family, repo)
    stdout.write(SYNC_CHECK_HEAD + "\n")
    for p in problems:
        detail = " ".join(p.detail.split())
        stdout.write(f"{p.severity} {detail}\n")
    return 1 if any(p.severity == lint.SEVERITY_ERROR for p in problems) else 0
