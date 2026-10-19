"""チケットとレビューのサブコマンド（`ticket` / `review` / `write_and_push` など）を
ops・review へ渡す。

`sync` の問い合わせ（`sync paths` / `sync check` / `sync retired` / `sync migrate`）もここで答える。

cli から分けた。cli を読まない。
"""

from __future__ import annotations

import argparse
import json
import os
import re
from typing import TextIO

from ..infra import fsio, gitcmd, settings, tree
from ..infra.modes import EXIT_ERROR, EXIT_OK
from ..tickets import (
    core,
    learning,
    legacy_sync,
    next_id,
    oplog,
    ops,
    phase,
    propose,
    retire,
    review,
    review_close,
    review_decide,
    scaffold,
    ticket_ids,
    user_only,
    via,
    worktrees,
    write_and_push,
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

    `ticket start|finish|cancel|propose <識別子>`、`ticket new <識別子> ...`（親の提案の雛形）、
    `review prepare|requested|confirm`、それに
    ユーザが打つ `--reviewed` と `--close-early`。
    リモートから取得した結果は `--result <json>` で受け取る。
    """
    cwd = args.cwd or os.getcwd()
    words = list(args.command)
    if words[:1] == ["worktree"]:
        # ワークツリーの片付けはチケット制御が無くても打てる（ccnavi-clean.sh --worktree の中身）。
        # tidy はチケットを読むので、チケット制御が要る。
        return _worktree(stdout, stderr, conf, root, args, words, cwd)
    if words[:2] == ["ticket", "next-id"]:
        # 番号を答えるだけ（読むだけ）。チケット制御が無くても使える。
        # 0 は成功、1 は識別子にできない形、
        # 2 は数えきれなかった。
        code = next_id.run(stdout, stderr, root, conf, words[2:])
        return EXIT_OK if code == 0 else (2 if code == 2 else EXIT_ERROR)
    if not conf.tickets_enabled:
        stderr.write(
            f"ccnavi: チケット制御が無効になっています（{settings.TICKET_CONTROL_ENV}）。\n"
        )
        return EXIT_ERROR
    bypass = _write_and_push_bypass(root, conf, args, cwd)
    if bypass:
        stderr.write(
            f"ccnavi: 親子のチケット {bypass} は取り込み済み（write-and-push の対象）。"
            "状態の操作は "
            f"'{settings.script_command(root, 'ccnavi-ticket.sh')}' か "
            f"'{settings.script_command(root, 'ccnavi-review.sh')}' から実行してください。"
            "write-and-push を通らない書き込みは親のブランチへ送られないので、"
            "何も反映せずに中止しました。\n"
        )
        return EXIT_ERROR
    if args.close_early:
        if not args.result:
            stderr.write(
                "ccnavi: --close-early を実行するには、"
                "--reason <理由> と --result <json> を指定してください。\n"
            )
            return EXIT_ERROR
        # ユーザの判断。`--agree` / `--reviewed` と同じく端末を求める。
        if not cli_args._from_terminal(stdin, conf, stderr, user_only.terminal("close-early")):
            return EXIT_ERROR
        via.set_via(via.VIA_TERMINAL)
        for flag, path in (
            ("--accept-plan", args.accept_plan),
            ("--accept-done", args.accept_done),
            ("--accept-check", args.accept_check),
        ):
            if path and not _in_state(conf, path):
                stderr.write(
                    f"ccnavi: {flag} には、状態保存ディレクトリ配下のパスを指定してください。\n"
                )
                return EXIT_ERROR
        code = review_close.close_early(
            stdin,
            stdout,
            stderr,
            root,
            conf,
            cwd,
            args.reason,
            args.result,
            plan_out=args.accept_plan,
            plan_done=args.accept_done,
            plan_check=args.accept_check,
            no_issue=args.no_issue,
        )
        return EXIT_OK if code == 0 else EXIT_ERROR
    if args.reviewed is not None and args.accept_unresolved and (args.preview or args.yes):
        if args.preview and args.yes:
            stderr.write("ccnavi: --preview と --yes は同時に指定できません。\n")
            return EXIT_ERROR
        if not args.json or not args.result:
            stderr.write(
                "ccnavi: ボードから実行する場合は、--json と --result <json> を指定してください。\n"
            )
            return EXIT_ERROR
        for flag, path in (
            ("--accept-plan", args.accept_plan),
            ("--accept-done", args.accept_done),
        ):
            if path and not _in_state(conf, path):
                msg = f"ccnavi: {flag} には、状態保存ディレクトリ配下のパスを指定してください。\n"
                stderr.write(msg)
                return EXIT_ERROR
        via.set_via(via.VIA_BOARD)
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
                args.accept_plan,
                args.accept_done,
            )
        return EXIT_OK if code == 0 else EXIT_ERROR
    if args.reviewed is not None and args.choose_out:
        # 対話の decide の前半（ロックを持ったままユーザを待たないため分ける）。ユーザが端末で選び、
        # 選択とダイジェストを state の置き場に書くだけ。置くのは sh が write-and-push の中で
        # `--yes <選択> --digest <ダイジェスト>` で打つ。
        if not cli_args._inside(
            cli_args._real(os.path.abspath(args.choose_out)), cli_args._real(conf.state)
        ):
            stderr.write(
                "ccnavi: --choose-out の出力先には、"
                "状態保存ディレクトリ配下のパスを指定してください。\n"
            )
            return EXIT_ERROR
        code = review_decide.choose(
            stdin, stdout, stderr, root, conf, cwd, args.reviewed, args.result, args.choose_out
        )
        return EXIT_OK if code == 0 else EXIT_ERROR
    if args.reviewed is not None:
        via.set_via(via.VIA_TERMINAL)
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
        )
        return EXIT_OK if code == 0 else EXIT_ERROR

    kind = words[0] if words else ""
    verb = words[1] if len(words) > 1 else ""
    target = words[2] if len(words) > 2 else ""
    code = 1
    if kind == "ticket" and verb == "status":
        # ticket status [<親>]。読むだけ（サブエージェントにも許す）。
        if len(words) > 3:
            stderr.write("ccnavi: ticket status に指定できる親チケットは1件のみです。\n")
        else:
            code = status.run(
                stdout, stderr, root, conf, write_and_push.family_of(target) if target else ""
            )
    elif kind == "ticket" and verb == "propose" and target:
        # ticket propose <識別子>。親子の提案（todo/ と flows/）を、書いたパスの一覧に載せるだけ。
        # コミットと push は sh（ccnavi-ticket.sh propose）が write-and-push の手順で行う。
        code = propose.run(stdout, stderr, root, conf, target)
    elif kind == "ticket" and verb in ("start", "finish", "cancel") and target:
        if verb == "start":
            code = ops.start(stdout, stderr, root, conf, target)
        elif verb == "finish":
            code = ops.finish(stdout, stderr, root, conf, target)
        else:
            code = ops.cancel(stdout, stderr, root, conf, target, args.reason)
    elif kind == "ticket" and verb == "learn" and len(words) <= 3:
        # ticket learn [<識別子>] [--note <本文> [--target <種類>] [--signal <信号>]]。
        # 気づきの記録の雛形と要約、または気づき 1 件の追記（直接作業では受信箱）。
        where = cwd[-1] if isinstance(cwd, list) and cwd else cwd
        code = learning.run(
            stdout, stderr, root, conf, where, target, args.note, args.target, args.signal
        )
    elif kind == "ticket" and verb == "new":
        # ticket new <識別子> --title <題> (--plan <型,型,...> |
        # --template <名前> [--layer <名前>] [--with-feedback]) [--issue N]。親の提案の雛形を作る。
        if len(words) != 3:
            stderr.write(
                "ccnavi: ticket new <識別子> --title <題> --plan <型,型,...>"
                " [--scope <glob>] [--issue N] [--mr N]"
                " か --template <名前> [--layer <名前>] [--with-feedback]\n"
            )
        else:
            where = cwd[-1] if isinstance(cwd, list) and cwd else cwd
            code = scaffold.run(
                stdout,
                stderr,
                conf,
                root,
                where,
                target,
                args.title,
                args.plan,
                args.issue,
                args.template,
                args.layer,
                args.with_feedback,
                args.scope,
                args.mr,
            )
    elif kind == "ticket" and verb == "record-risk":
        # ticket record-risk <子> <項目> yes|no --reason <根拠>
        if len(words) < 5:
            stderr.write(
                "ccnavi: ticket record-risk には <子> <項目> yes|no と --reason <根拠>"
                " を指定してください。\n"
            )
        else:
            code = ops.record_risk(
                stdout, stderr, root, conf, words[2], words[3], words[4], args.reason
            )
    elif kind == "review" and verb == "prepare":
        if args.phase is None or not args.body_file:
            stderr.write(
                "ccnavi: review prepare には --phase <N> と --body-file <path> を指定してください。"
                "\n"
            )
        else:
            code = review.prepare(stdout, stderr, root, conf, cwd, args.phase, args.body_file)
    elif kind == "review" and verb in ("requested", "confirm"):
        if args.phase is None or not args.result:
            stderr.write(
                f"ccnavi: review {verb} には --phase <N> と --result <json> を指定してください。\n"
            )
        elif verb == "requested":
            code = review.requested(stdout, stderr, root, conf, cwd, args.phase, args.result)
        else:
            code = core.confirm_local(
                stdout, stderr, root, conf, cwd, args.phase, args.result, args.actor
            )
    elif kind == "review" and verb == "ready":
        if not args.result:
            stderr.write("ccnavi: review ready には --result <json> を指定してください。\n")
        else:
            code = review_close.ready(stdout, stderr, root, conf, cwd, args.result)
    else:
        word = cli_usage.TOPIC_WORDS.get(kind)
        stderr.write(cli_usage.topic(word) if word else cli_usage.OVERVIEW)
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
    if args.abandon is not None:
        # 放棄した親の片付けの記録を消す。ユーザが端末で打つ（hook がエージェントからは止める）。
        if words != ["worktree", "retire"] or not ticket_ids.is_valid_id(args.abandon):
            stderr.write(
                "ccnavi: worktree retire --abandon <親> の形で指定してください"
                "（親は有効な識別子）。\n"
            )
            return EXIT_ERROR
        code = retire.run_abandon(stdout, stderr, root, conf, args.abandon)
        return EXIT_OK if code == 0 else EXIT_ERROR
    if verb == "name" and len(words) == 3:
        # 直接作業の名前の形を言う（warn の 1 行）。sh の作成の後に呼ぶ。
        text = ticket_ids.direct_name_warning(words[2], conf.branch_prefixes, root)
        if text:
            stdout.write(f"warn: {text}\n")
        return EXIT_OK
    if verb == "id" and len(words) == 3:
        # ブランチ名から識別子を作り、使えるかを言う（字の種類・NFC・長さはここで判定する）。
        for line in ticket_ids.id_answer(words[2]):
            stdout.write(line + "\n")
        return EXIT_OK
    if verb == "issue-id" and len(words) in (4, 5):
        # issue から新しい親のブランチ名（= 識別子）を作る。Chrome の「始める」と同じ関数。
        # worktree issue-id <番号> <先頭の語> [<プロジェクト>] --title <題>
        try:
            ident = ticket_ids.issue_identifier(
                int(words[2]) if words[2].isdigit() else 0,
                args.title,
                words[4] if len(words) == 5 else "",
                words[3],
            )
        except ValueError as exc:
            stderr.write(f"ccnavi: {exc}\n")
            return EXIT_ERROR
        stdout.write(f"id {ident}\n")
        return EXIT_OK
    if verb == "branch" and len(words) in (3, 4) and words[3:] in ([], ["confirmed"]):
        # 親を閉じたときに片付ける子として記録したブランチを、条件を確かめ直して消す。
        if not ticket_ids.is_valid_id(words[2]):
            stderr.write(
                f"ccnavi: worktree branch の {words[2]!r} は有効な識別子の形式ではありません。\n"
            )
            return EXIT_ERROR
        code = retire.run_branch(stdout, stderr, root, conf, words[2], len(words) == 4)
        return EXIT_OK if code == 0 else EXIT_ERROR
    if verb not in ("drop", "tidy", "remaining", "retire") or len(words) != 3:
        stderr.write(
            "ccnavi: worktree drop <名前> か worktree tidy <親> [--phase <N>]"
            " か worktree remaining <親> か worktree retire <親>"
            " か worktree retire --abandon <親>"
            " か worktree branch <名前> [confirmed]"
            " か worktree name <名前> か worktree id <ブランチ>"
            " か worktree issue-id <番号> <先頭の語> [<プロジェクト>] --title <題>\n"
        )
        return EXIT_ERROR
    target = words[2]
    if not ticket_ids.is_valid_id(target):
        stderr.write(
            f"ccnavi: worktree {verb} の {target!r} は有効な識別子の形式ではありません。\n"
        )
        return EXIT_ERROR
    if verb == "drop":
        code = worktrees.run_drop(stdout, stderr, root, target, cwd)
    elif not conf.tickets_enabled:
        stderr.write(
            f"ccnavi: チケット制御が無効になっています（{settings.TICKET_CONTROL_ENV}）。\n"
        )
        return EXIT_ERROR
    elif verb == "remaining":
        code = worktrees.run_remaining(stdout, root, conf, target, cwd)
    elif verb == "retire":
        code = retire.run_retire(stdout, stderr, root, conf, target, cwd)
    else:
        code = worktrees.run_tidy(stdout, stderr, root, conf, target, args.phase, cwd)
    return EXIT_OK if code == 0 else EXIT_ERROR


def _write_and_push_bypass(
    root: str, conf: settings.Settings, args: argparse.Namespace, cwd
) -> str:
    """write-and-push を通らずに、取り込み済みの親子のチケットの状態を書こうとしているなら、
    その親の識別子。無ければ空。

    状態の操作（`ticket start|finish|cancel|propose`、`review requested|confirm|ready`、残った指摘の
    行き先の `--reviewed N --accept-unresolved`）は、write-and-push の対象の親子のチケットでは sh が
    `--record-tree` を付けて起こす。付いていなければ、sh の引数の読み違いや直打ちで
    write-and-push を通っていない。
    ユーザの判断の操作（`--close-early`）と、
    書かない形（`--preview`・`--choose-out`・`--accept-plan`・`review prepare`）は見ない。
    """
    if args.record_tree:
        return ""
    words = list(args.command)
    family_id = ""
    if (
        len(words) >= 3
        and words[0] == "ticket"
        and words[1]
        in (
            "start",
            "finish",
            "cancel",
            "propose",
        )
    ):
        family_id = write_and_push.family_of(words[2])
    elif (
        len(words) >= 2 and words[0] == "review" and words[1] in ("requested", "confirm", "ready")
    ) or (
        args.reviewed is not None
        and args.accept_unresolved
        and not args.preview
        and not args.choose_out
        and not args.accept_plan
    ):
        where = cwd[-1] if isinstance(cwd, list) and cwd else cwd
        parent = phase.parent_for_cwd(root, conf, where) if where else None
        if parent is not None:
            family_id = parent.parent or parent.ticket
    if not family_id or not _FAMILY.fullmatch(family_id):
        return ""
    verdict, _, _ = write_and_push.target(conf, root, family_id)
    return family_id if verdict == write_and_push.TARGET_YES else ""


def sync_paths(stdout: TextIO, root: str, conf: settings.Settings) -> int:
    """`ccnavi-sync.sh` が要るパスと名前を 1 行 1 項目（`<鍵> <値>`）で返す。

    sh は jq を使わず、JSON も読まない。置き場のパス（ツリーのルートからの相対）をここが渡す。
    統合先の名前は返さない（sh が各リポジトリの `origin/HEAD` で決める）。
    置き場は固定（`settings.load` の既定）で、env では動かない。`ccnavi-sync.sh` は答えが
    無いとき同じ既定を直に使う。
    ネットワークにも git にも触らない。
    """
    lines = (
        ("approved", fsio.slashed(conf.approved).strip("/")),
        ("proposals", fsio.slashed(conf.tickets).strip("/")),
        ("home", fsio.slashed(conf.project_home or settings.DEFAULT_PROJECT_HOME).strip("/")),
    )
    for key, value in lines:
        stdout.write(f"{key} {value}\n")
    return 0


def ops_command(stdout: TextIO, stderr: TextIO, conf: settings.Settings, words: list[str]) -> int:
    """`ccnavi ops <副命令>`。操作記録（`logs/state/ops/`）を読み書きする。

    sh が送信の段階を記録するために呼ぶ。

    - `pending`: 未完了の記録を 1 行ずつ（`<操作ID> <種類> <未完了の段階>`）。無ければ何も出さない
    - `show <操作ID>`: 記録の JSON
    - `stage-set <操作ID> <段階ID> <状態> [URL] [アカウント]`: 段階の状態を書く
    - `applied <操作ID> <鍵> <値>`: ローカルに適用したものを残す（値は true / false / 文字列）
    - `reconcile <操作ID> <段階ID> <URL,…> <yes|no|error> [アカウント]`: 送信の照合。結論を
      `sent|send|unknown|stop` で出す（`yes` は一覧を最後まで読めた、`no` は打ち切った）
    - `begin <種類> <親> <フェーズ> <MR> <鍵> <段階ID:種類>…`: 鍵から決めた操作ID で記録を作る
      （エージェントの request・comment・ready が、打ち直しで同じ記録に当たるため）。ID を出す
    - `post-text <操作ID> <段階ID> <ファイル>`: 段階で投稿する本文を、保存した判断から作り直して書く
    - `resume-plan <操作ID> <ファイル>`: 保存した判断から受け入れの計画を書き直す（`--resume`）
    - `done <操作ID>`: すべての段階が sent なら記録を消す。消したら `done`、残るなら `open` を出す
    - `retire <親>`: 親の記録のうち、送り終わったものを消す（親を閉じる片付け。ready が呼ぶ）

    書く副命令（`stage-set`・`done`）は、エージェントが偽の送信済みを書けないよう、
    実行ファイルを直に呼ぶ形を hook が止める（`user_only.SUBS`）。
    ネットワークにも git にも触れない。
    """
    if not conf.state:
        stderr.write("ccnavi: state の置き場が空。操作記録を扱えない\n")
        return EXIT_ERROR
    try:
        if words == ["pending"]:
            for record in oplog.pending(conf.state):
                if record.get("broken"):
                    stdout.write(f"{record['id']} broken\n")
                    continue
                left = ",".join(s["id"] for s in oplog.unfinished(record))
                stdout.write(f"{record['id']} {record['kind']} {left}\n")
            return EXIT_OK
        if len(words) == 2 and words[0] == "show":
            record = oplog.load(conf.state, words[1])
            if record is None:
                stderr.write(f"ccnavi: 操作記録 {words[1]} が無い\n")
                return EXIT_ERROR
            stdout.write(json.dumps(record, ensure_ascii=False) + "\n")
            return EXIT_OK
        if len(words) in (4, 5, 6) and words[0] == "stage-set":
            oplog.stage_set(
                conf.state,
                words[1],
                words[2],
                words[3],
                words[4] if len(words) > 4 else "",
                words[5] if len(words) > 5 else "",
            )
            return EXIT_OK
        if len(words) == 4 and words[0] == "applied":
            oplog.applied_set(conf.state, words[1], words[2], _applied_value(words[3]))
            return EXIT_OK
        if len(words) in (5, 6) and words[0] == "reconcile":
            record = oplog.load(conf.state, words[1])
            if record is None:
                stderr.write(f"ccnavi: 操作記録 {words[1]} が無い\n")
                return EXIT_ERROR
            found = [u for u in words[3].split(",") if u]
            complete = {"yes": True, "no": False}.get(words[4])
            verdict, why = oplog.reconcile(
                record, words[2], found, complete, words[5] if len(words) > 5 else ""
            )
            stdout.write(f"{verdict} {why}".rstrip() + "\n")
            return EXIT_OK
        if len(words) >= 7 and words[0] == "begin":
            # begin <種類> <親> <フェーズ> <MR> <鍵> <段階ID:種類>…
            # 鍵から決めた ID で作る（既にあれば作らない）
            kind, parent, phase, mr, key = words[1:6]
            if key.startswith("@"):
                # 鍵の中身をファイルから取る（本文そのものを鍵にして、衝突を避ける）
                key = fsio.read_text(key[1:]) or key
            stages = []
            for spec in words[6:]:
                sid, _, skind = spec.partition(":")
                stages.append((sid, skind or oplog.STAGE_COMMENT))
            op_id = oplog.begin(
                conf.state,
                kind,
                repo={},
                parent=parent,
                phase=int(phase) if phase.isdigit() else 0,
                mr=int(mr) if mr.isdigit() else 0,
                stages=stages,
                op_id=oplog.id_for(kind, parent, phase, mr, key),
            )
            stdout.write(op_id + "\n")
            return EXIT_OK
        if len(words) == 4 and words[0] == "post-text":
            record = oplog.load(conf.state, words[1])
            if record is None:
                stderr.write(f"ccnavi: 操作記録 {words[1]} が無い\n")
                return EXIT_ERROR
            if record.get("kind") != oplog.KIND_DECIDE or not record.get("applied", {}).get(
                "local"
            ):
                stderr.write("ccnavi: この記録は、まだ適用が済んでいない（投稿の本文は作れない）\n")
                return EXIT_ERROR
            text = review_decide.post_text(record, words[2])
            if text is None:
                stderr.write(f"ccnavi: 段階 {words[2]} の本文は作れない\n")
                return EXIT_ERROR
            if not _in_state(conf, words[3]):
                stderr.write("ccnavi: 書き先は state の置き場の下に限る\n")
                return EXIT_ERROR
            failed = fsio.write_text(words[3], text, newline="\n")
            if failed:
                stderr.write(f"ccnavi: 本文を {words[3]} に書けない（{failed}）\n")
                return EXIT_ERROR
            return EXIT_OK
        if len(words) == 3 and words[0] == "resume-plan":
            record = oplog.load(conf.state, words[1])
            if record is None:
                stderr.write(f"ccnavi: 操作記録 {words[1]} が無い\n")
                return EXIT_ERROR
            if not _in_state(conf, words[2]):
                stderr.write("ccnavi: 書き先は state の置き場の下に限る\n")
                return EXIT_ERROR
            return EXIT_OK if review_decide.resume_plan(record, words[2]) == 0 else EXIT_ERROR
        if len(words) == 2 and words[0] == "retire":
            for op_id in oplog.retire_parent(conf.state, words[1]):
                stdout.write(op_id + "\n")
            return EXIT_OK
        if len(words) == 2 and words[0] == "done":
            stdout.write("done\n" if oplog.complete_if_done(conf.state, words[1]) else "open\n")
            return EXIT_OK
    except oplog.OpError as exc:
        stderr.write(f"ccnavi: {exc}\n")
        return EXIT_ERROR
    stderr.write(
        "ccnavi: ops の使い方: pending / show <操作ID> / "
        "stage-set <操作ID> <段階ID> <pending|sent|unknown> [URL] [アカウント] / "
        "begin <種類> <親> <フェーズ> <MR> <鍵> <段階ID:種類>… / applied <操作ID> <鍵> <値> / "
        "reconcile <操作ID> <段階ID> <URL,…> <yes|no|error> [アカウント] / "
        "done <操作ID> / retire <親>\n"
    )
    return EXIT_ERROR


def _applied_value(text: str) -> object:
    return {"true": True, "false": False}.get(text, text)


SYNC_RETIRED_HEAD = "retired 1"
SYNC_MIGRATE_HEAD = "migrate 1"


def _one_line(text: str) -> str:
    return " ".join(text.split())


def sync_retired(stdout: TextIO, conf: settings.Settings) -> int:
    """`ccnavi-sync.sh` が、親を閉じた後の片付けの列挙元を聞く。頭に `retired 1`、続けて 1 行 1 件。

    ```
    retired <親> <self|プロジェクト名> <MR の番号|-> <ブランチ|->
    incomplete <親> <理由>
    ```

    列挙元は `state/retire/<親>.json`（`ready` が書く）。読めない・`mr` が無いものは `incomplete`
    で、sh は一覧の案内だけを出し、自動では片付けない。ネットワークにも git にも触れない。
    """
    stdout.write(SYNC_RETIRED_HEAD + "\n")
    for e in retire.entries(conf):
        if e.incomplete:
            stdout.write(f"incomplete {e.parent} {_one_line(e.incomplete)}\n")
        else:
            stdout.write(f"retired {e.parent} {e.repo} {e.mr or '-'} {e.branch or '-'}\n")
    return 0


def sync_migrate(stdout: TextIO, conf: settings.Settings, root: str) -> int:
    """古い取り込み状態（`sync/<repo>/families/<親>`・`integration/`）の 1 回限りの移行。冪等。

    ```
    migrate 1
    moved <親> <repo>          retire/<親>.json を新規に書いた、または不足分（mr・branch）を足した
    kept <親> <repo>           retire/<親>.json が既にあり、そちらを尊重した
    dropped <親> <repo> <理由>  親のワークツリーが在る、または mr も branch も無い。古い記録を消した
    skipped <親> <repo> <理由>  リンクなど。消さない
    removed integration <repo> sync/<repo>/integration/ を消した
    ```

    親のワークツリーが無い古い記録だけを `retire/<親>.json` に移す。取り込みの状態（gone や
    blocked など）は捨てる。リンクは辿らない。ネットワークにも git にも触れない。
    """
    stdout.write(SYNC_MIGRATE_HEAD + "\n")
    if not conf.state:
        return 0
    for fam in legacy_sync.read_families(conf.state):
        _migrate_one(stdout, conf, root, fam)
    for repo in legacy_sync.integration_repos(conf.state):
        if not _REPO_NAME.fullmatch(repo):
            stdout.write(f"skipped integration {_one_line(repo)} リポジトリの名前の形が違う\n")
            continue
        removed, why = legacy_sync.remove_integration(conf.state, repo)
        if why:
            stdout.write(f"skipped integration {repo} {_one_line(why)}\n")
        elif removed:
            stdout.write(f"removed integration {repo}\n")
    return 0


def _migrate_one(
    stdout: TextIO, conf: settings.Settings, root: str, fam: legacy_sync.LegacyFamily
) -> None:
    name = fam.name or "-"
    repo = fam.repo if _REPO_NAME.fullmatch(fam.repo) else "-"
    if fam.skipped or not fam.name:
        stdout.write(f"skipped {name} {repo} {_one_line(fam.skipped or '名前が空')}\n")
        return
    if repo == "-" or not _FAMILY.fullmatch(fam.name) or ".." in fam.name:
        stdout.write(f"skipped {name} {repo} 名前の形が違う\n")
        return
    if os.path.lexists(tree.worktree_path(root, fam.name)):
        gone = legacy_sync.remove_family(conf.state, fam.repo, fam.name)
        _migrate_dropped(stdout, fam, gone, "親のワークツリーが在る")
        return
    if not fam.mr and not fam.branch:
        gone = legacy_sync.remove_family(conf.state, fam.repo, fam.name)
        _migrate_dropped(stdout, fam, gone, "mr も branch も無い")
        return
    result = retire.merge_legacy(conf, fam.name, tree.project_of_key(fam.repo), fam.branch, fam.mr)
    if result.failed:
        stdout.write(f"skipped {fam.name} {fam.repo} {_one_line(result.failed)}\n")
        return
    stdout.write(f"{result.status} {fam.name} {fam.repo}\n")
    if not legacy_sync.remove_family(conf.state, fam.repo, fam.name):
        stdout.write(f"skipped {fam.name} {fam.repo} 古い記録を消せない\n")


def _migrate_dropped(
    stdout: TextIO, fam: legacy_sync.LegacyFamily, removed: bool, why: str
) -> None:
    if removed:
        stdout.write(f"dropped {fam.name} {fam.repo} {why}\n")
    else:
        stdout.write(f"skipped {fam.name} {fam.repo} {why}。古い記録を消せない\n")


def _in_state(conf: settings.Settings, path: str) -> bool:
    """出力先が state の置き場の下か（`--choose-out` と同じ確かめ方）。"""
    return cli_args._inside(cli_args._real(os.path.abspath(path)), cli_args._real(conf.state))


def _decide_writes(args) -> bool:
    """書く形の decide か（`--reviewed N --accept-unresolved` の、見るだけでないもの）。"""
    return (
        args.reviewed is not None
        and args.accept_unresolved
        and not args.preview
        and not args.choose_out
        and not args.accept_plan
    )


def _decide_actor(args) -> None:
    """decide のマーカーに入れるアカウントと経路。

    `--actor` があればアカウントを足し、`--via` があれば経路を差し替える。マーカーの
    `actor`・`via` は `review_decide.apply_decision` がこの起動の値から書く。
    無ければマーカーは前と同じ。
    """
    if not args.actor:
        return
    via.set_actor(args.actor)
    if args.via:
        via.set_via(args.via)


class _Family:
    """親の識別子の形（`ticket_ids.is_valid_id` と同じ。日本語の字を含む）。

    sh から渡る引数なので、パスとして読まれる文字列を入れない。前は正規表現で持っていたので、
    呼び手が使う `fullmatch` の形を残す。
    """

    @staticmethod
    def fullmatch(text: str) -> bool:
        return ticket_ids.is_valid_id(text)


_FAMILY = _Family()
# リポジトリの名前（`self` かプロジェクト名）。ASCII の 1 語。
_REPO_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
# `--actor` の形。拡張がホストから読むアカウント名の形（`github.ts` の NAME）と同じ。
_ACTOR = re.compile(r"[A-Za-z0-9_.-]{1,100}")


# `sync check` の答えの頭の行。sh はこれが無ければ「検査を実行できなかった」（古い実行ファイルが
# 知らないサブコマンドを断った、など）と読み、親子のチケットを止めない（検査の error と分ける）。
SYNC_CHECK_HEAD = "check 1"
_SHA = re.compile(r"[0-9a-f]{7,64}")
_GIT_TIMEOUT = 20.0


def sync_tidy(
    stdout: TextIO,
    stderr: TextIO,
    root: str,
    conf: settings.Settings,
    cwd: str,
    family: str,
    repo: str,
    branch: str,
    head_sha: str,
) -> int:
    """`ccnavi-sync.sh` が、親の MR がマージ済みと分かった後に打つ片付け。

    親のワークツリーと手元のブランチ、残っていた子のブランチを、条件を確かめて消す。
    `head_sha` はマージ済みの MR の先頭（sh がホストから取ってきた）。1 行ずつ言い、最後が
    `record yes|no`。ネットワークには出ない（`retire.run_merged`）。
    """
    for name, value, pattern in (
        ("識別子", family, _FAMILY),
        ("リポジトリ", repo, _REPO_NAME),
        ("sha", head_sha, _SHA),
    ):
        if not pattern.fullmatch(value) or ".." in value:
            stderr.write(f"ccnavi: sync tidy の{name} {value!r} は有効な形式ではありません。\n")
            return 1
    checked = gitcmd.run(root, ["check-ref-format", "--branch", branch], _GIT_TIMEOUT)
    if not branch or branch.startswith("-") or not checked.ok:
        stderr.write(f"ccnavi: sync tidy のブランチ {branch!r} はブランチ名の形ではない\n")
        return 1
    if ticket_ids.branch_to_id(branch) != family:
        # 親のブランチだけを消す。別のブランチを渡して消させない。
        stderr.write(
            f"ccnavi: sync tidy のブランチ {branch!r} は親 {family} のブランチではない"
            f"（ブランチから作る識別子は {ticket_ids.branch_to_id(branch)!r}）\n"
        )
        return 1
    project = "" if repo == "self" else repo
    return retire.run_merged(stdout, stderr, root, conf, family, project, branch, head_sha, cwd)


def sync_check(
    stdout: TextIO,
    stderr: TextIO,
    root: str,
    conf: settings.Settings,
    family: str,
    repo: str | None = None,
) -> int:
    """`ccnavi-sync.sh` が取り込みの後に打つ検査。頭に `check 1`、続けて 1 行 1 件。

    行は `error <detail>` か `warn <detail>`。error が 1 つでもあれば 1（sh が表示して失敗にする）。
    warn は表示するだけで、レイヤーのずれ（手元と取得済みの `origin/<統合先>`）を含む。
    `repo` は `self` かプロジェクト名。ネットワークには出ない（git はローカルの参照を読むだけ）。
    """
    for name, value, pattern in (
        ("識別子", family, _FAMILY),
        ("リポジトリ", repo or "self", _REPO_NAME),
    ):
        if not pattern.fullmatch(value) or ".." in value:
            stderr.write(
                f"ccnavi: sync check の{name} {value!r} は有効な識別子の形式ではありません。\n"
            )
            return 1
    problems = lint.family_check(conf, root, family, repo)
    stdout.write(SYNC_CHECK_HEAD + "\n")
    for p in problems:
        detail = " ".join(p.detail.split())
        stdout.write(f"{p.severity} {detail}\n")
    return 1 if any(p.severity == lint.SEVERITY_ERROR for p in problems) else 0
