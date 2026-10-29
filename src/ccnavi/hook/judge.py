"""実行前チェック。ツール呼び出しの引数を見て、通す・聞く・止めるを決める。

ここが返すのは判定と、その根拠になった文（文面は reasons が組む）。応答の形に
組むのは hookio、記録に残すのは audit で、どちらも同じ結論から作られる。
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable
from typing import TextIO

from ..catalog import docsearch, projskills
from ..infra import fsio, hookio, modes, settings, shellread, tree
from ..infra.modes import AUX, EXIT_OK, GATE, GENERAL
from ..policy import (
    builtin,
    ctxfile,
    ruleload,
    rules,
    selfguard,
    selfguard_shell,
    selfguard_targets,
    subjects,
    texts,
)
from ..records import audit, failstreak, repeat
from ..tickets.approval import checks as approval_checks
from ..tickets.approval import store as approval
from ..tickets.flow import graph as flow
from ..tickets.model import phase_forms, ticket_guard, ticket_model, ticket_places
from ..tickets.phase import gates as phase
from ..tickets.phase import scope as phase_scope
from . import (
    agentlaunch,
    failsafe,
    parentwrite,
    reasons,
    rootwrite,
    turngate,
    wrapguard,
)

# 1 回の起動に張る期限。呼び手は長く走った hook を打ち切って出力を破棄するので、
# それより先に自前の判定を出し終えることが、遅い判定が何も返さないまま許可として扱われて
# しまうのを防ぐ。
DEADLINE_SECONDS = 3.0

# Claude Code 側の権限モード。ルールがどこも言及しなかった呼び出しの結末が、
# ここで変わる。ccnavi 自身のモード（上の 3 つ）とは別のもので、あちらは
# 「下した判定を適用するか」、こちらは「そもそも誰が判断するか」を決める。
#
# ルールが言及していない呼び出しについて、ccnavi は判定を持たない。持っていない
# 判定を ask として返すと、判断できる相手が居るモードでも必ずユーザの確認で止まる。だから
# 返さず、Claude Code の権限モードに従う。
# ルールに書いていないものは、ルールに書いていないものとして渡す。
#
# 判断できる相手が居るモードは 4 つ。auto は classifier が読み、残りは Claude Code
# 自身の権限の仕組み（settings.json の permissions と、モードごとの既定）が決める。
# ccnavi がそこに確認を上乗せしても、判断する者が増えるわけではなく、同じ呼び出しで
# 2 度聞かれるだけになる。ここに無い名前は ask として扱う。名前が 1 つ増えたときに、
# それが確認なしに通るのではなく確認になるように。
PERMISSION_JUDGED = ("auto", "default", "acceptEdits", "plan")

# ユーザにも classifier にも確認できないモード。ここで ask を返すと「誰も答えないまま
# 通る」になってしまうので、許可としない。
PERMISSION_NO_JUDGE = ("dontAsk", "bypassPermissions")

# 判定を権限モードへ渡したことを表す内部の値。タイプ名（rules.ALLOW など）と
# 同じ変数に入るので、ルールファイルには現れない表記にしてある。
HANDOVER = "(handover)"

# dry-run でも効く止め（関門と補助）の拒否文に添える 1 行。
DRY_RUN_HOLDS_NOTE = (
    "[ccnavi] CCNAVI_MODE is dry-run, but this stop still applies: ticket gates and the "
    "protections that keep them from being bypassed are enforced in every mode except disable."
)

# 範囲の中かどうかを問うツール。ワークツリーを書き換えるものだけ。
# Bash は入れていない。コマンド文字列に現れるパスは追えないので、ここで
# 適用するとヒットしたりヒットしなかったりする判定になる。シェル経由の書き込みは
# 実行後チェックがワークツリーの実物を見て拾う。
SCOPE_TOOLS = ("Write", "Edit", "NotebookEdit")

# ルールを適用する欄の表と選び方は `policy.subjects` にある（ルールファイルの検査も同じ表を読む）。
SUBJECT_FIELDS = subjects.SUBJECT_FIELDS
PATH_TOOLS = subjects.PATH_TOOLS
SEARCH_TOOLS = subjects.SEARCH_TOOLS

# シェルから書き込む形を止める組み込みの保護。どれも書き込む動詞の式
# （selfguard_shell._WRITE_VERBS）を持ち、生の文字列では `>` の個数に対して 2 乗で遅くなる。
# ガード自身の設定を守る 1 本は、ルールファイルが読める間は selfguard_shell が足し、読めずに
# 組み込みの既定に戻った間は builtin が持つ。
# チケットの状態の置き場を守る 1 本は、チケット制御が有効なときに足す。
# 生の文字列の `>` が上限を超えたときは、これらを生の文字列に適用しない（decide_before）。
# `.git` の中を守る土台の 1 本（builtin.GIT_DIR_SHELL_RULE_ID）は入れない。リダイレクトの行き先を
# `>` をまたがずに読むので、照合が長さに比例して終わる（selfguard_shell.git_dir_shell_regex）。
SHELL_GUARD_RULE_IDS = (
    selfguard_shell.SHELL_RULE_ID,
    builtin.CONFIG_VIA_BASH_RULE_ID,
    ticket_guard.STATE_SHELL_RULE_ID,
    ticket_guard.APPROVED_SHELL_RULE_ID,
    ticket_guard.OPS_SHELL_RULE_ID,
)


def guard_setting_files(
    mode: str,
    conf: settings.Settings,
    root: str,
    payload: hookio.Input,
    record: audit.Record,
    step: Callable[..., list[selfguard.Outcome]],
) -> str:
    """ccnavi 自身の設定ファイルをバックアップと突き合わせる 1 回。返す文を作る。

    実行前と実行後で違うのは呼ぶ関数だけなので、そこを引数で受け取る。
    対象の決め方も設定の解き方も 1 か所にまとまり、片方だけが別の対象を
    見ている、という食い違いが起きない。
    """
    setting = modes.effective_setting(mode, conf.guard_core_files)
    outcomes = step(
        setting,
        conf.state,
        payload.session_id,
        root,
        selfguard_targets.targets(
            root, conf.rules, conf.bin, ruleload.layer_files(conf, root), conf.projects
        ),
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
    """実行前チェックに達し、何が起きたかを record に書き込む。
    記録と応答が必ず同じ結論から作られるようにするため。

    処理フロー:
    1. 判定より先に設定ファイルのバックアップを取る（`guard_setting_files` に `selfguard.before`）。
       その知らせは `guard` に残し、以降の応答に足す。
    2. 主題（`record.subject`）が無ければ記録して抜ける。
    3. ルールを読み、この呼び出しの行き先のツリーを決める（`ruleload.rules_for`）。
    4. 組み込みの保護をルールに足す（`selfguard_shell`・`ticket_guard`・`phase_forms`）。
    5. 主題を読み直す（`screen`）。実行される部分が残らなければ記録して抜ける。
    6. 組み込みの止めを、順に適用する。ヒットすれば `refuse` で返す。順に、環境変数の指定
       （`wrapguard.check_env`）・自己防衛のパス（`wrapguard.check_core_paths`）・記録の削除
       （`phase_forms.prune_form`）・ユーザの判断の経路（`phase_forms`）・子の push
       （`wrapguard.check_child_push`）・ユーザの発言をまたぐ関門（`turngate`）・サブエージェントの禁止（`phase_forms.forbidden`）・
       親のワークツリーへの書き込み（`wrapguard.check_parent_tree_git`・`parentwrite.check`）・
       レビュー待ちの止め（`phase.held_phase`）・サブエージェントの起動（`agentlaunch.decide`）・
       プロジェクトの食い違い（`project_mismatch`）・着手中の子のロック（`flow_lock`）。
    7. 書き直しを求める形（`rewrites`）は、読み分けずに止めて書き直し方を返す。
    8. ルールを deny・ask・allow の順に適用する（`rules.SECTIONS`）。読み切れない形には
       allow を適用しない。期限を過ぎたら、ツールの種類で決めた結論を返す。
    9. deny にヒットしなかった Bash は、ワークスペースルート直下への書き込みを `rootwrite.check` で
       適用する。ヒットすれば `refuse` で返す。
    10. ヒットを記録し、チケットの判定（`ticket_verdict`）と強さを比べて強い側を採る。
    11. 文面を組み、通す・渡す（`HANDOVER`）・拒否・確認の応答を返す（`refuse`・`_write_pass`）。
    """
    # バックアップを取るのは判定より先。ここで取る断面が、この呼び出しが何かを書き換えて
    # しまったときに戻る先になる。判定で弾かれた呼び出しでも先に取っておくのは、
    # 弾けなかったものだけがワークツリーに届くので、届いた側から見れば
    # 「直前」は判定の前だから。
    guard = guard_setting_files(mode, conf, root, payload, record, selfguard.before)

    if not record.subject:
        record.decision, record.reason = audit.SKIP, audit.REASON_NO_SUBJECT
        if guard:
            hookio.write_context(stdout, hookio.PRE_TOOL_USE, guard)
        return EXIT_OK

    # ルールを読み、この呼び出しの行き先のツリーを決める。
    rule_set, source, target = ruleload.rules_for(stderr, conf, root, payload, record)
    # チケットの範囲を引く行き先。下で cwd のツリーに置き換える前の、対象のパスそのものの
    # ツリー。ワークスペースの外へ書くときに cwd のワークツリーの範囲を適用しないため。
    dest = target
    if target is None and payload.cwd:
        # Bash には行き先が無い。記録には cwd のツリーをつける。判定には使わない。
        target = tree.tree_of(root, payload.cwd, conf.projects)
    if target is not None:
        record.tree, record.project = target.name, target.project
    # 設定ファイルを守る側が有効なら、そこへシェルから書き込む形を止める
    # ルールを judgment に足す。戻せるだけでは足りないので、同じ場所を
    # 実行前にも止める。既定を使っているときは `add_rules` を足さない。組み込みの既定が
    # 同じ形を既に持っていて、二重にヒットすると同じ話が 2 度返る。
    # 名前で書くツールの 4 本（`add_named_rules`）は既定を使っている間も足す。組み込みの既定は
    # 持っていないので、二重にならない。読めない間も共通の設定への Write / Edit は止まる。
    # 組み込みの保護（設定ファイル・チケットの置き場・シェルの形）をルールに足す。
    core_files_on = modes.effective_setting(mode, conf.guard_core_files) != selfguard.DISABLE
    if record.fallback != builtin.FALLBACK and core_files_on:
        selfguard_shell.add_rules(
            rule_set,
            conf.bin,
            conf.project_home,
            root,
            selfguard_shell.common_layer_files(conf),
            (conf.log, conf.state),
            tool=payload.tool_name,
            cwd=payload.cwd,
        )
    if core_files_on:
        # `add_rules` の後に呼ぶ。先頭に挿すので、専用の文面が
        # `guard-project-home` / `guard-common-layer` より先に並ぶ。
        selfguard_shell.add_named_rules(rule_set, conf.project_home, root)
    # チケットの状態の置き場を守る。動かすのはスクリプトだけで、直接の作成・移動は
    # 誰がやっても止める。チケット制御が有効なときだけ足す。
    # 生の git は、チケット制御の有無によらず止める。入口を git のラッパー 1 本に寄せ、
    # チケット制御の可否の一部をラッパーの中に置くため。ルールファイルが読めない間も、
    # 共通レイヤーが無くても足す（`ticket_guard.raw_git_rule`）。
    rule_set.deny.append(ticket_guard.raw_git_rule(root))
    if conf.tickets_enabled:
        rule_set.deny.extend(ticket_guard.guard_rules(conf.tickets, root))
        # 承認済みの置き場と操作記録の置き場。関門なので dry-run でも
        # `CCNAVI_GUARD_CORE_FILES` の値でも外れない。
        rule_set.deny.extend(ticket_guard.gate_rules(conf.approved, root))
        # ユーザの判断の経路（承認・レビュー済みの受け入れ・状態とレビューの操作）を、
        # 実行ファイルを直接打つ形で通さない。スクリプト 2 本の中身がこれ。
        # サブエージェントからは、設定に関わらず止める。実行ファイルを直接呼んで状態を動かさせない。
        if conf.guard_ticket_approval != selfguard.DISABLE or payload.agent_id:
            rule_set.deny.append(phase_forms.ticket_approval_rule(conf.bin, root))

    # 主題を読み直して、実行される部分（subject）と書き直しの形を取る。
    subject, bare, inner, rewrites = screen(payload.tool_name, record.subject, record)

    # 文面の `subject:` には、読み直す前の原文を出す。秘密の形は記録と同じく伏せる。
    # 照合には読み直した後の subject を使う。
    display_subject = audit._redacted(record.subject)

    if not subject:
        # コマンドは在るが、実行される部分が残らなかった。コメントだけの行が
        # これにあたる。ルールを適用する先が無いので、どのタイプにもヒットせず
        # 権限モードへ渡る先になるが、何も走らないものについて誰かの判断を
        # 求める意味は無い。
        record.decision, record.reason = audit.SKIP, audit.REASON_NOTHING_TO_RUN
        if guard:
            hookio.write_context(stdout, hookio.PRE_TOOL_USE, guard)
        return EXIT_OK

    # 組み込みの既定に戻ったときだけ言う。レイヤーが不正で空になったのは組み込みへの
    # 退避ではないので、同じ文面を出すと「既定で判定している」と読み違えられる。
    # そちらは記録の `fallback` にレイヤーの名前が残り、`--lint` が error で言う。
    fallback = (
        reasons.fallen_back(record.detail or conf.rules, root)
        if record.fallback == builtin.FALLBACK
        else ""
    )
    notices = [text for text in (guard, fallback) if text]

    # 保護済みの sh を、sh の検査の材料を変える環境変数と同じコマンド行で呼ぶ形は、ルールより
    # 先に止める。`CCNAVI_WORKSPACE=<子のワークツリー> sh …ccnavi-git.sh push` は、sh の中の
    # 子の push の検査を外す。hook は settings.json の env で起動するので、この代入の影響を
    # 受けない。止める場所を hook に置き、sh の検査は 2 重目にする。読むのは元のコマンド。
    # `cd` を追うには区切りの残った形が要る。
    # 環境変数の指定の止めは補助の保護（AUX）で、`CCNAVI_GUARD_CORE_FILES` では切れない
    # （切れるのは自己防衛だけ）。dry-run でも止める。
    if payload.tool_name == "Bash":
        script, names = wrapguard.check_env(record.subject, record.degraded)
        if names:
            record.code, record.rules = wrapguard.CODE_ENV, [wrapguard.ENV_RULE_ID]
            return refuse(
                stdout,
                mode,
                record,
                rules.DENY,
                notices
                + [
                    reasons.builtin_refusal(
                        wrapguard.CODE_ENV,
                        wrapguard.ENV_RULE_ID,
                        display_subject,
                        wrapguard.env_message(root, script, names),
                    )
                ],
                conf=conf,
                root=root,
                kind=AUX,
            )
        # git のラッパーの `rm`・`restore --source`・`checkout <ref> <パス>` で、自己防衛の対象を
        # 消す・別のコミットの中身に戻す形。シェルの rm と同じ場所を、ラッパー越しにも止める。
        # ラッパー自身が消えても、生の git の組み込みの止めは外れない（いつも足す）。
        sub, word, kind = (
            wrapguard.check_core_paths(conf, root, payload.cwd, record.subject, record.degraded)
            if core_files_on
            else ("", "", "")
        )
        if sub:
            record.code, record.rules = wrapguard.CODE_CORE_PATH, [wrapguard.CORE_PATH_RULE_ID]
            return refuse(
                stdout,
                mode,
                record,
                rules.DENY,
                notices
                + [
                    reasons.builtin_refusal(
                        wrapguard.CODE_CORE_PATH,
                        wrapguard.CORE_PATH_RULE_ID,
                        display_subject,
                        wrapguard.core_path_message(root, sub, word, kind),
                    )
                ],
                conf=conf,
                root=root,
                kind=GENERAL,
            )

    # 記録と state を消す `ccnavi-user-prune.sh`（内部は `--prune`。`--preview` の無い形）は、
    # チケット制御に依らず止める
    # （phase_forms.prune_form）。実行ファイルのターミナル要求は擬似ターミナルを使えば通れる。
    # 記録と state の
    # 置き場をシェルの書き込みから守る組み込み（selfguard）と同じく、ガード自身を守る設定で切れる。
    if payload.tool_name in phase_forms.SHELL_TOOLS and core_files_on:
        found = phase_forms.prune_form(record.subject, conf.bin) or phase_forms.user_prune_form(
            record.subject, conf.bin
        )
        if found:
            record.code = phase_forms.CODE_RECORDS_PRUNE
            record.rules = [phase_forms.RECORDS_PRUNE_RULE_ID]
            return refuse(
                stdout,
                mode,
                record,
                rules.DENY,
                notices
                + [
                    reasons.builtin_refusal(
                        phase_forms.CODE_RECORDS_PRUNE,
                        phase_forms.RECORDS_PRUNE_RULE_ID,
                        display_subject,
                        phase_forms.prune_message(root),
                    )
                ],
                conf=conf,
                root=root,
                kind=GENERAL,
            )

    # ユーザの判断の経路のうち、hook のほかに保護が無い形（ターミナル要求を切る形、
    # ボードの経路の形）は
    # 止める。実行ファイルを呼ぶ表記は追い切れないので、呼び方ではなく文字列の組で見る
    # （phase_forms.human_path_form）。
    if (
        conf.tickets_enabled
        and conf.guard_ticket_approval != selfguard.DISABLE
        and payload.tool_name in phase_forms.SHELL_TOOLS
    ):
        kind, found = phase_forms.human_path_form(record.subject)
        if found:
            record.code = phase_forms.CODE_TICKET_APPROVAL
            record.rules = [phase_forms.TICKET_APPROVAL_RULE_ID]
            return refuse(
                stdout,
                mode,
                record,
                rules.DENY,
                notices
                + [
                    reasons.builtin_refusal(
                        phase_forms.CODE_TICKET_APPROVAL,
                        phase_forms.TICKET_APPROVAL_RULE_ID,
                        display_subject,
                        phase_forms.guard_off_message(root, found)
                        if kind == "guard-off"
                        else phase_forms.user_script_message(root, found)
                        if kind == "user-script"
                        else phase_forms.expanded_option_message(root, found)
                        if kind == "expanded"
                        else phase_forms.board_form_message(root, found),
                    )
                ],
                conf=conf,
                root=root,
                kind=GATE,
            )
        # 2 段目の無いユーザの入口（`ccnavi-review.sh decide`・実行ファイルの `user` の入口と
        # `--yes`）は、引数を変数・置換・glob で組み立てると表記の検査から外れる。読めなければ止める
        # （phase_forms.hidden_user_entry、ADR-0122）。
        entry, found = phase_forms.hidden_user_entry(
            record.subject, [(r, x) for r, x, _ in inner], conf.bin
        )
        if entry:
            record.code = phase_forms.CODE_TICKET_APPROVAL
            record.rules = [phase_forms.TICKET_APPROVAL_RULE_ID]
            return refuse(
                stdout,
                mode,
                record,
                rules.DENY,
                notices
                + [
                    reasons.builtin_refusal(
                        phase_forms.CODE_TICKET_APPROVAL,
                        phase_forms.TICKET_APPROVAL_RULE_ID,
                        display_subject,
                        phase_forms.hidden_entry_message(root, entry, found),
                    )
                ],
                conf=conf,
                root=root,
                kind=GATE,
            )

    # 子チケットのワークツリーからの `ccnavi-git.sh push` は、誰が打っても止める。sh も同じ検査を
    # 持つが、止める場所は hook に置き、sh は 2 重目にする。
    if conf.tickets_enabled and payload.tool_name == "Bash":
        name, parent, _ = wrapguard.check_child_push(
            conf, root, payload.cwd, record.subject, record.degraded
        )
        if name:
            record.code, record.rules = wrapguard.CODE_CHILD_PUSH, [wrapguard.CHILD_PUSH_RULE_ID]
            return refuse(
                stdout,
                mode,
                record,
                rules.DENY,
                notices
                + [
                    reasons.builtin_refusal(
                        wrapguard.CODE_CHILD_PUSH,
                        wrapguard.CHILD_PUSH_RULE_ID,
                        display_subject,
                        wrapguard.child_push_message(root, name, parent),
                    )
                ],
                conf=conf,
                root=root,
                kind=GATE,
            )

    # ユーザの発言をまたぐ関門。「A を打ったら、ユーザの発言が 1 回はさまるまで B を打てない」
    # （`turngate.BUILTIN`）。ルールより先に見る。ルールが allow と書いていても、ユーザの返事を
    # 待つ手順は外さない。B は止め、通した A は記録する（記録は拒否・失敗で消す。`events.decide`、
    # `decide_after_failure`）。サブエージェントの呼び出しも、同じセッションの記録で見る。
    if payload.tool_name in phase_forms.SHELL_TOOLS:
        gate_texts = [subject, *(layer for runner, layer, _ in inner if runner != shellread.MOVED)]
        stop = turngate.check(
            turngate.BUILTIN, gate_texts, conf.state, payload.session_id
        ) or turngate.note(
            turngate.BUILTIN, gate_texts, conf.state, payload.session_id, payload.tool_use_id
        )
        if stop is not None:
            record.code, record.rules = turngate.CODE, [turngate.rule_id(stop.gate)]
            return refuse(
                stdout,
                mode,
                record,
                rules.DENY,
                notices
                + [
                    reasons.builtin_refusal(
                        turngate.CODE,
                        turngate.rule_id(stop.gate),
                        display_subject,
                        turngate.message(stop, root),
                    )
                ],
                conf=conf,
                root=root,
                kind=GATE,
            )

    # サブエージェントには、状態を動かすスクリプトもレビューのスクリプトも打たせない。
    # 閉じるのは親だけ。ルールより先に見る。ルールが allow と
    # 書いていても、この 2 本はサブエージェントの手には渡さない。
    if (
        conf.tickets_enabled
        and payload.agent_id
        and payload.tool_name in phase_forms.SHELL_TOOLS
        and phase_forms.forbidden(subject, shellread.SEP.join(layer for _, layer, _ in inner))
    ):
        # 禁止は中で実行されるコマンドにも適用する。`env sh …ccnavi-ticket.sh start` を
        # 元の形だけで見ると、先頭の `sh` に固定した形が外れて止まらずに通る。
        runner, layer = ("", "")
        if not phase_forms.forbidden(subject):
            runner, layer = next((r, x) for r, x, _ in inner if phase_forms.forbidden(x))
        record.code, record.rules = phase.CODE_SUBAGENT, [reasons.SUBAGENT_TICKET_OP_RULE]
        record.unwrapped = layer
        return refuse(
            stdout,
            mode,
            record,
            rules.DENY,
            notices + [reasons.subagent_forbidden(root, display_subject, runner, layer)],
            conf=conf,
            root=root,
            kind=GATE,
        )

    # サブエージェントは親のワークツリーを書き換えない。合流・コミット・push はメインが finish で
    # 行い、サブエージェントは子のワークツリーで実装してコミットするだけ。居場所（エージェント ID と
    # 実行先のツリー）で見るので、子のワークツリーでのコミットと、子のワークツリーで親のブランチを
    # 取り込む merge は通る。
    if conf.tickets_enabled and payload.agent_id:
        sub, where, message = "", "", ""
        if payload.tool_name == "Bash":
            sub, where = wrapguard.check_parent_tree_git(
                conf, root, payload.cwd, record.subject, record.degraded
            )
            if where:
                message = wrapguard.parent_tree_message(root, sub, where)
            else:
                # シェルからの書き込み（リダイレクト・tee・sed -i・cp / mv の行き先、
                # インタプリタに渡したコード）。読み方と限界は parentwrite に書いてある。
                what, where, kind = parentwrite.check(
                    conf,
                    root,
                    payload.cwd,
                    record.subject,
                    record.degraded,
                    [layer.replace(shellread.SEP, "\n") for _, layer, _ in inner],
                )
                if where:
                    message = wrapguard.parent_write_message(root, what, where, kind)
        elif payload.tool_name in SCOPE_TOOLS:
            # 書き込み先のパスが `.claude/worktrees/<名前>` の中で、その名前が親のチケットか
            # （ワークツリーが git に登録されているかは問わない。sh の検査と同じ広さ）。
            # 提案・下書き・ELI5 の置き場（`wip/proposals/` `wip/handoff/` `wip/eli5/` と承認済みの
            # 領域）は対象にしない。サブエージェントがフローの下書きなどをここへ書く経路で、
            # 置き場ごとの保護（ルールの deny とチケットの保護）が別に掛かっている。
            name = wrapguard.worktree_name(root, record.subject)
            if name and wrapguard.ticket_role(conf, root, name)[0] == wrapguard.ROLE_PARENT:
                base = os.path.join(root, tree.WORKTREES_DIR, name)
                rel = os.path.relpath(os.path.abspath(record.subject), base).replace(os.sep, "/")
                if not ticket_places.is_unscoped(rel, conf.tickets, conf.approved):
                    where = name
                    message = wrapguard.parent_write_message(root, payload.tool_name, name)
        if where:
            record.code = wrapguard.CODE_PARENT_TREE
            record.rules = [wrapguard.PARENT_TREE_RULE_ID]
            return refuse(
                stdout,
                mode,
                record,
                rules.DENY,
                notices
                + [
                    reasons.builtin_refusal(
                        wrapguard.CODE_PARENT_TREE,
                        wrapguard.PARENT_TREE_RULE_ID,
                        display_subject,
                        message,
                    )
                ],
                conf=conf,
                root=root,
                kind=GATE,
            )

    # 承認済みチケットの置き場（`approval.read_raw`）。1 回の判定で読むのは 1 度だけにし、
    # 下のレビュー待ちの止めと範囲の索引で持ち回る。
    # 判定の途中で置き場のファイルを動かす処理は無いので、読み直さない。
    raw: approval.Raw | None = None

    # HITL ポイント。人間レビュー要のフェーズが終わっていてマーカーが無い間、
    # サブエージェントの起動と、例外の 3 本以外のシェル実行を止める。
    # ルールより先に見る。置き場を読むのは cwd がワークツリーかプロジェクトの中のときだけ
    # （`phase.parent_at`）。
    if conf.tickets_enabled and payload.tool_name in phase_forms.HELD_TOOLS:
        parent, raw = phase.parent_at(root, conf, payload.cwd, raw)
        held = phase.held_phase(root, conf, parent.ticket, raw) if parent is not None else None
        exempt = payload.tool_name == "Bash" and phase_forms.exempt(subject, record.degraded)
        if held is not None and not exempt:
            record.code, record.rules = phase.CODE_REVIEW, [reasons.PHASE_REVIEW_RULE]
            reason = phase.hold_reason(held, payload.tool_name, root)
            return refuse(
                stdout,
                mode,
                record,
                rules.DENY,
                notices + [reason],
                conf=conf,
                root=root,
                kind=GATE,
            )

    # サブエージェントの起動。プロンプトの言及から担当を決める。決まらないときの止めは、ここ
    # （レビュー待ちの止めのあと、ルールの照合の前）で返す。決まったときの更新の計画（担当の案内を
    # プロンプトに書き足す）は持ったまま判定を進め、最後の応答で 1 つの JSON に合成する。
    # ルールには書き足す前の入力を適用する。
    launch = agentlaunch.Result()
    if payload.tool_name in modes.AGENT_TOOLS:
        launch = agentlaunch.decide(conf, root, payload, raw)
        if launch.stop is not None:
            record.code, record.rules = launch.stop.code, [launch.stop.rule]
            return refuse(
                stdout,
                mode,
                record,
                rules.DENY,
                notices
                + [
                    reasons.builtin_refusal(
                        launch.stop.code, launch.stop.rule, display_subject, launch.stop.text
                    )
                ],
                conf=conf,
                root=root,
                kind=launch.stop.kind,
            )
    updated = launch.plan.apply(payload.tool_input) if launch.plan else None

    # 承認済みチケットの索引。ワークツリーへの書き込みでは、プロジェクトの食い違いの点検と
    # チケットの範囲の判定の両方が引く。走査は 1 回で数百ミリ秒かかるので、1 回の判定で
    # 1 度だけ読んで両方に渡す。ワークスペースルートへの書き込みとシェルでは読まない。
    # 条件は project_mismatch の早く返る条件と同じ式。ticket_verdict は dest で見るが、dest が
    # None でなければ target は dest そのものなので、先へ進むときはここも同じツリーで読んでいる。
    index = None
    scanned: list[ticket_model.Ticket] | None = None
    if (
        conf.tickets_enabled
        and target is not None
        and not target.is_main
        and payload.tool_name in SCOPE_TOOLS
    ):
        if raw is None:
            raw = approval.read_raw(conf, root)
        scanned, _ = approval.scan(conf, root, raw=raw)
        index = approval_checks.by_id(scanned)

    # ワークツリーの元リポジトリと承認済みチケットの `project:` の食い違いは、ルールより先に見る。
    # 範囲の宣言ではなく取り違えなので、ルールが allow と言っていても通さない。
    if conf.tickets_enabled and target is not None and payload.tool_name in SCOPE_TOOLS:
        mismatch = project_mismatch(conf, root, target, record.subject, index)
        if mismatch:
            record.code, record.rules = (
                reasons.CODE_TICKET_PROJECT,
                [reasons.TICKET_PROJECT_RULE],
            )
            return refuse(
                stdout,
                mode,
                record,
                rules.DENY,
                notices + [mismatch],
                conf=conf,
                root=root,
                kind=GATE,
            )

    # 着手中の子のフローは書き換えさせない。ルールより先に見る。
    # 置き場は承認済みの領域で、エージェントの書き込みは組み込みの保護でも止まる。ロックは
    # その保護を切った設定でも該当し、止めた理由（着手中）を名指しする（厳しくする向きだけ）。
    if conf.tickets_enabled and target is not None and payload.tool_name in SCOPE_TOOLS:
        locked, scanned = flow_lock(conf, root, payload, record.subject, scanned)
        if index is None and scanned is not None:
            index = approval_checks.by_id(scanned)
        if locked:
            record.code, record.rules = flow.CODE_LOCKED, [flow.LOCK_RULE]
            return refuse(
                stdout,
                mode,
                record,
                rules.DENY,
                notices + [locked],
                conf=conf,
                root=root,
                kind=GATE,
            )

    # 書き直しを求める形は、ルールより先に止める（読み分けずに止めて書き直しを案内する）。
    # ブレース展開と、実行するときに決まるコマンド名は、ルールを適用する読みと実行されるものが
    # 食い違う。
    # `{git,push,origin,main}` も `c=git; $c push origin main` も、
    # 生の git にヒットしないまま push を
    # 実行する。バッククォートとシェルで読みが分かれる形は、読み分けると規則が増え、読み違えると
    # 止まらずに通る。どれも書き直す方法が必ずあるので、読み解かずに止めて、形ごとの書き直し方を
    # 1 回で返す。
    if rewrites:
        rewrite_kind = _rewrite_kind(conf, payload, subject, inner, rewrites)
        forms = list(dict.fromkeys(form for form, _ in rewrites))
        record.code = reasons.CODE_REWRITE[forms[0]]
        record.rules = [reasons.rewrite_rule(form) for form in forms]
        parts = [
            reasons.rewrite(
                root,
                record.subject,
                form,
                [text for f, text in rewrites if f == form],
                settings.git_wrapper_command(root),
            )
            for form in forms
        ]
        return refuse(
            stdout,
            mode,
            record,
            rules.DENY,
            notices + parts,
            conf=conf,
            root=root,
            kind=rewrite_kind,
        )

    # 生の文字列（読み切れなかった Bash と、読まない PowerShell）に `>` が多すぎると、シェルから
    # 書き込む形の保護の照合が `>` の個数 × 尾の長さで遅くなる（selfguard_shell.REDIRECT_LIMIT）。
    # 期限を越えると判定に達せず、enable では block（終了コード 2）で止まるだけになり、
    # 何が起きたかも言えない。そのときはその保護（SHELL_GUARD_RULE_IDS）だけを生の文字列に
    # 適用せず、ほかの deny と ask のルールを適用したうえで、権限モードに依らず拒否にし、理由と
    # 書き直し方を返す。期限切れの block と同じく止める側に置き、確認には回さない。照合を
    # 飛ばして通すと、上限の後ろに書いた保護対象への書き込みが素通りする。ルールの deny に
    # ヒットする形は、そのルールの deny のまま返す。allow は適用しない
    # （保護を適用していないので、通す根拠にならない）。中で実行されるコマンドと `cd` の行き先は
    # 読み解けた形なので、保護もそのまま適用する。
    # 外す保護がこのツールにヒットしないなら（既定の組み込みの 1 本は Bash だけ）、上限も掛けない。
    unread = bool(record.degraded) or (
        payload.tool_name in phase_forms.SHELL_TOOLS and payload.tool_name != "Bash"
    )
    capped = (
        unread
        and selfguard_shell.too_many_redirects(subject)
        and any(
            rule.id in SHELL_GUARD_RULE_IDS and rules.tool_matches(rule.match, payload.tool_name)
            for rule in rule_set.deny
        )
    )
    # ルールを deny・ask・allow の順に適用する。最初にヒットした区分で判定が決まる。
    verdict, group = "", []
    # group と同じリストで、中で実行されるコマンドでヒットしたときの
    # （実行役のコマンド, そのコマンド, 引用の中から切り出したコマンドの層か）。
    # 元の形でヒットしたルールは ("", "", False)。
    via: list[tuple[str, str, bool]] = []
    for name in rules.SECTIONS:
        if name == rules.ALLOW and (record.degraded or capped):
            # 読み切れなかったコマンドに allow は適用しない。適用する先は
            # 実行される部分ではなく生の文字列なので、そこで許可を出すのは
            # 「読めなかった文字列にそう書いてあった」を根拠に通すことになる。
            # deny と ask は適用したままにする。生の文字列にヒットしすぎるぶんは
            # 厳しい側へ外れるだけで、そのことは文面が断る。
            break
        # 中で実行されるコマンドは deny と ask にだけ適用する。止める側に足す適用先なので、
        # レイヤーを読み違えてもヒットするはずのものがヒットしないだけで、元の形の判定は消えない。
        # allow に適用すると逆になる。`sudo -u me cat /etc/hosts` は元の形では確認になるが、
        # 中の `cat …` が読み取りの allow にヒットして通るようになる。
        #
        # 引用の外のレイヤーを先に見る。同じルールが引用の中と外の両方にヒットするなら、
        # 外でヒットしたことにする。
        # 引用の中の断りは、引用の中にだけヒットしたときに付けるものなので。
        layers = sorted(inner, key=lambda x: x[2]) if name != rules.ALLOW else []
        for rule in rule_set.section(name):
            if time.monotonic() > deadline:
                # 期限切れの結論もツールの種類で決める。通すのは、設定を読まずに安全と
                # 分かる読み取りだけ。dry-run でも、通さない呼び出しは止めて、そう言う。
                code = modes.fail_closed(
                    mode, payload.tool_name, safe=failsafe.is_safe(payload, root)
                )
                stderr.write(
                    reasons.deadline_exceeded(
                        root,
                        audit.REASON_DEADLINE_EXCEEDED,
                        DEADLINE_SECONDS,
                        passed=code == EXIT_OK,
                    )
                    + "\n"
                )
                record.decision, record.reason = audit.SKIP, audit.REASON_DEADLINE_EXCEEDED
                return code
            raw_skipped = capped and rule.id in SHELL_GUARD_RULE_IDS
            if not raw_skipped and rule.matches(payload.tool_name, subject):
                group.append(rule)
                via.append(("", "", False))
                continue
            for runner, layer, quoted in layers:
                if rule.matches(payload.tool_name, layer):
                    group.append(rule)
                    via.append((runner, layer, quoted))
                    break
        if group:
            verdict = name
            break
    # 土台（base）の deny を落とす前の id。分類（dry-run でも止めるか）は落とす前の組で決める。
    # 層の deny が同時にヒットしても、生の git の補助止めの分類を失わない。
    hit_ids = {rule.id for rule in group}
    if verdict == rules.DENY and any(rule.base for rule in group):
        # 組み込みの deny（土台）は常にヒットするが、レイヤーの deny にもヒットしたなら、
        # そちらだけを言う。拒否は変わらない。同じ拒否を 2 つの文面で返さないため。
        kept = [i for i, rule in enumerate(group) if not rule.base]
        if kept:
            group = [group[i] for i in kept]
            via = [via[i] for i in kept]
    # 保護を適用しなかったぶん、ルールの deny にヒットしていなければ拒否に上げる。権限モードでも
    # CCNAVI_GUARD_UNWATCHED でも分けない（保護の照合を終えていないので、委ねる先が無い）。
    capped = capped and verdict != rules.DENY
    if capped:
        verdict = rules.DENY

    # シェルからワークスペースルート直下へ書く形は、Write / Edit と同じ決まり（ルールファイルの
    # `workspace-root`）に適用して止める。書き込み先を解いた絶対パスで適用するので、
    # `cd` で移った先もたどる。ガード自身の保護など、ほかの deny にヒットした呼び出しは、
    # その文面で止まる。ここでは
    # deny にヒットしなかった呼び出しだけを見る。
    if payload.tool_name == "Bash" and verdict != rules.DENY:
        written, place, root_rule, origin = rootwrite.check(
            rule_set,
            payload.cwd,
            record.subject,
            [layer.replace(shellread.SEP, "\n") for _, layer, _ in inner],
        )
        if written:
            # 記録には、止めた決まり（ルールファイルの `workspace-root`）の名前を残す。`--test` が
            # 式まで見せる際に読む `hits` と、位置・名前を揃える。
            record.code, record.rules = rootwrite.CODE, [rules.record_name(root_rule)]
            record.hits = [root_rule]
            return refuse(
                stdout,
                mode,
                record,
                rules.DENY,
                notices
                + [
                    reasons.builtin_refusal(
                        rootwrite.CODE,
                        rootwrite.RULE_ID,
                        display_subject,
                        rootwrite.message(root, written, place, origin),
                    )
                ],
                conf=conf,
                root=root,
                kind=GENERAL,
            )

    # 記録に、ヒットしたルールと判定の根拠を残す。
    record.rules = [rules.record_name(rule) for rule in group]
    # 適用したルールそのもの。診断（`--test`）が根拠を見せるのに読む。記録には書かない。
    record.hits = list(group)
    record.unwrapped = shellread.SEP.join(dict.fromkeys(layer for _, layer, _ in via if layer))
    # 判定を下したのは最初にヒットしたルール。そのレイヤーを 1 欄で残す。
    # id の前置きからも読めるが、欄にしておくと記録をレイヤーで数えられる。ルールファイルの
    # 外から足したルール（組み込みの保護、チケット）はレイヤーを持たないので空のまま。
    if group:
        record.source = group[0].source

    # 止めた・聞いたルールのうち、引用の中から切り出したコマンドにだけヒットしたもの。
    # 書いた側は文字を書いただけのつもりでいるので、回避策を知らせないと、
    # 同じ形を書き直しては止まる。どこにヒットしたかは一致位置から推し量らず、
    # 引用の中の中身を除いた読み（bare）に適用し直して、ヒットしなければそうだとする。
    # こうするとルールの書き方（glob / regex、`^` / `(^|\x00)`）に依らない。
    # 中で実行されるコマンドでヒットしたルールは、bare に適用し直せない（bare は元の形の読み）。
    # そちらは、ヒットしたレイヤーが引用の中から切り出したコマンドのものかどうかで決める。
    inside = [False] * len(group)
    if verdict in (rules.DENY, rules.ASK):
        inside = [
            quoted if layer else bare != subject and not rule.matches(payload.tool_name, bare)
            for rule, (_, layer, quoted) in zip(group, via, strict=True)
        ]
    record.quoted = [name for name, hit in zip(record.rules, inside, strict=True) if hit]

    # ルールの判定とチケットの判定を合わせ、強い側を採る。同じ強さならルール。
    # ルールの deny はどう書いても最も強いので、そのときはチケットを見ない。
    # チケットが該当するのは閉じる向きだけ（範囲の外・deny・ask）。ユーザが書いたルールの allow を
    # 作業 1 本のあいだ狭めることはあっても、ルールの deny や ask を緩めることは無い。
    # チケットが判定に使われるのはユーザが承認したあとだけで、承認画面が
    # 「ルールの allow も範囲の外では止まる」と言う。
    ticket_reason = ""
    ticket_code = ""
    if verdict != rules.DENY:
        rule_hit = (rules.record_name(group[0]), verdict) if group else None
        ticket_decision, text, ticket_notice, code = ticket_verdict(
            conf, root, payload.tool_name, record.subject, dest, index, rule_hit
        )
        if ticket_notice:
            notices.append(ticket_notice)
        if STRENGTH[ticket_decision] > STRENGTH[verdict]:
            verdict, ticket_reason, ticket_code = ticket_decision, text, code
            if ticket_reason:
                # 判定を下したのはレイヤーを持たないチケット。狭められたルールの id は後ろに残し、
                # 「ルールは通したのにチケットが止めた」回を記録から数えられるようにする。
                # チケット自体が信頼できずに止めた回（DENY_TICKET_BLOCKED）は範囲を適用して
                # いないので、範囲の名前（TICKET_RULE）と分ける。
                decided_by = {
                    reasons.CODE_TICKET_BLOCKED: reasons.TICKET_BLOCKED_RULE,
                    reasons.CODE_TICKET_PARENT: reasons.TICKET_PARENT_RULE,
                }.get(code, reasons.TICKET_RULE)
                record.rules = [decided_by, *record.rules]
                record.source = ""

    # モデルへの文脈（ルールの文・提案の確認・スキルの目録）を組む。
    # ヒットしたルールがモデルへ渡す文。通す・聞く・止めるのどれでも、判定とは
    # 別の経路（additionalContext）で届く。
    context, record.hinted = ctxfile.deliver(
        stderr, conf.state, payload, group, ctxfile.bases(conf, root, target)
    )
    # 提案を書いた回に、承認を頼む前の確認を 1 度だけ伝える文。判定には
    # 足さない（`ticket_guard.propose_notice` の説明）ので、ルールの文と同じ経路
    # （additionalContext）で渡す。応答は 1 つの JSON なので、まとめる。
    told = (
        ticket_guard.propose_notice(stderr, conf, root, payload, record.subject)
        if conf.tickets_enabled
        else ""
    )
    # cwd がプロジェクトの中に入った最初の呼び出しで、そのプロジェクトのスキルの目録を 1 度だけ
    # つける。セッションはワークスペースルートで始まり、あとから cd で入るのがふつう。
    skills = projskills.notice(stderr, conf, root, payload)
    if skills:
        told = "\n\n".join(p for p in (told, skills) if p)
    if told:
        context = "\n\n".join(p for p in (told, context) if p)

    # 通す回。通知と入力の更新だけを、判定を付けずに返す。
    if verdict == rules.ALLOW:
        record.decision, record.enforced = audit.ALLOW, True
        if notices or context or updated is not None:
            # 通した回にも言う。ガードが今なにを見ていないのかを言わないでいると、
            # 誰も知らないまま作業が進む。呼び出しごとに出るのでうるさいが、
            # うるさいのが正しい。不正な設定と未承認のチケットはどちらも
            # 短命であるべきで、気づかないうちに残り続けるより気づかれたほうがよい。
            # ルールの additionalContext も同じ経路で、通す代わりに一言添える。
            # 起動の入力の更新（担当の案内）は判定を付けずに同じ JSON へ載せる。
            _write_pass(stdout, "\n\n".join(notices + ([context] if context else [])), updated)
        return EXIT_OK

    # どちらの文面を返すかは、判定を決めた側で分ける。ルールがヒットしていても、
    # チケットが強かった回はチケットの文面になる（ticket_reason があるのはその回だけ）。
    if verdict == rules.DENY and capped:
        # `>` の上限で止めた。ヒットした ask のルールがあれば、その文も後ろに並べる。
        parts = [
            reasons.builtin_refusal(
                reasons.CODE_REDIRECT_LIMIT_DENY,
                reasons.REDIRECT_LIMIT_RULE,
                display_subject,
                texts.say(
                    root,
                    "DENY_REDIRECT_LIMIT",
                    {
                        "limit": str(selfguard_shell.REDIRECT_LIMIT),
                        "length": str(selfguard_shell.RAW_LENGTH_LIMIT),
                    },
                ),
            ),
            *(
                reasons.reason_for(
                    rule,
                    payload.tool_name,
                    display_subject,
                    source,
                    record.degraded,
                    runner,
                    layer,
                    quoted,
                    root=root,
                    git=settings.git_wrapper_command(root),
                )
                for rule, (runner, layer, _), quoted in zip(group, via, inside, strict=True)
            ),
        ]
        record.code = reasons.CODE_REDIRECT_LIMIT_DENY
        record.rules = [reasons.REDIRECT_LIMIT_RULE, *record.rules]
        # 判定を決めたのはレイヤーを持たない組み込み（上限）。並べた ask のルールのレイヤーを
        # 残すと、ルールファイルのルールが止めたように読める。チケットが止めた回と同じく空にする。
        record.source = ""
    elif verdict == rules.DENY and not ticket_reason:
        # ルールの deny。ヒットした理由はまとめて 1 回で返す。1 つずつ返すと、エージェントも
        # 1 つずつ直すことになり、そのたびに往復が 1 回増える。
        parts = [
            reasons.reason_for(
                rule,
                payload.tool_name,
                display_subject,
                source,
                record.degraded,
                runner,
                layer,
                quoted,
                root=root,
                git=settings.git_wrapper_command(root),
            )
            for rule, (runner, layer, _), quoted in zip(group, via, inside, strict=True)
        ]
        # 全部が中で実行されるコマンドでヒットしたなら、適用した先は読み直したコマンドであって
        # 生の文字列ではない。読み切れなかったことを根拠のコードにしない。
        read_through = all(layer for _, layer, _ in via)
        record.code = reasons.code_for(payload.tool_name, "" if read_through else record.degraded)
        if any(rule.id == phase_forms.TICKET_APPROVAL_RULE_ID for rule in group):
            record.code = phase_forms.CODE_TICKET_APPROVAL
    elif verdict == rules.DENY:
        # チケットが止めた。範囲の外かチケットの deny で、ルールは何も言わないか、
        # allow / ask にヒットしている（そのときは文面がルールの id を名指しする）。
        parts = [ticket_reason]
        record.code = ticket_code or reasons.CODE_TICKET_SCOPE
    elif verdict == rules.ASK and not ticket_reason:
        parts = [
            "\n".join(
                [
                    reasons.reason_for(
                        rule,
                        payload.tool_name,
                        display_subject,
                        source,
                        record.degraded,
                        runner,
                        layer,
                        quoted,
                        root=root,
                        git=settings.git_wrapper_command(root),
                    ),
                    *(
                        reasons.ask_hint(root, rule.bare_id, display_subject)
                        if rule.decision == rules.ASK
                        else ()
                    ),
                ]
            )
            for rule, (runner, layer, _), quoted in zip(group, via, inside, strict=True)
        ]
        record.code = reasons.CODE_RULE_ASK
    elif verdict == rules.ASK:
        # チケットが ask と書いた場所。ユーザが 1 度見る場所として宣言されている。
        # ルールは何も言わないか、allow にヒットしている。
        parts = [ticket_reason]
        record.code = reasons.CODE_TICKET_ASK
    else:
        # どのタイプも言及しなかった。ccnavi はこの呼び出しの判定を持たない。
        # 結末は Claude Code の権限モードが決める。
        record.code = reasons.CODE_UNCERTAIN if record.degraded else reasons.CODE_UNDECLARED
        verdict = undeclared_verdict(payload.permission_mode, record.degraded, conf.guard_unwatched)
        # 渡した回（HANDOVER）は文を返さないので組まない。出所は判定の出所（退避した回は
        # 組み込みの既定）で、読めなかったルールファイルを名指ししない。
        parts = (
            []
            if verdict == HANDOVER
            else [
                reasons.undeclared(
                    payload.tool_name,
                    display_subject,
                    source,
                    record.degraded,
                    verdict=verdict,
                    fell_back=record.fallback == builtin.FALLBACK,
                    root=root,
                )
            ]
        )

    if verdict == HANDOVER:
        # 判定は渡した。ここで残せるのは記録だけで、それがこの分担の要点になる。
        # ルールが言及していない場所は log の handover を数えれば分かり、その数は
        # ユーザに聞いた回とも、classifier が通した回とも区別できる。
        #
        # 文面は返さない。呼び出しごとに「ルールが言及していない」と言うと、
        # 渡した先が判断するだけの回に毎度コンテキストを 1 段積むことになる。
        # ルールが言及していない場所は記録から読む。
        record.decision, record.enforced = audit.HANDOVER, False
        # 1 度だけ渡す文（提案を書いた回の確認、プロジェクトのスキルの目録）は、渡す回にも言う。
        # 言わないと、その文を伝える機会が権限モードに渡す呼び出しの分だけ遅れる。
        if notices or told or updated is not None:
            _write_pass(stdout, "\n\n".join(notices + ([told] if told else [])), updated)
        return EXIT_OK

    # 拒否・確認・渡すの応答を 1 つにまとめて返す。
    return refuse(
        stdout,
        mode,
        record,
        verdict,
        notices + parts,
        context,
        conf=conf,
        root=root,
        kind=final_kind(conf, hit_ids, ticket_reason, capped),
        updated_input=updated,
    )


def _write_pass(stdout: TextIO, text: str, updated: dict | None) -> None:
    """通す・渡す回の応答。文があれば additionalContext、入力の更新があれば updatedInput。

    判定（permissionDecision）は付けない。付けると権限の確認を飛ばす（allow）ことになる。
    """
    if updated is None:
        hookio.write_context(stdout, hookio.PRE_TOOL_USE, text)
        return
    hookio.write_pre_tool_use(stdout, context=text, updated_input=updated)


# 一般の保護とは別に、dry-run でも止める関門のルール。チケット制御が有効なときだけ足されるので、
# ヒットしたならそのまま関門として数える。
GATE_RULE_IDS = (
    ticket_guard.STATE_RULE_ID,
    ticket_guard.STATE_SHELL_RULE_ID,
    ticket_guard.APPROVED_RULE_ID,
    ticket_guard.APPROVED_SHELL_RULE_ID,
    ticket_guard.OPS_RULE_ID,
    ticket_guard.OPS_SHELL_RULE_ID,
    phase_forms.TICKET_APPROVAL_RULE_ID,
)


def final_kind(conf: settings.Settings, ids: set[str], ticket_reason: str, capped: bool) -> str:
    """ルールとチケットの判定を拒否にした回の分類。dry-run でも止めるかを決める。

    ids はヒットしたルールの id（土台を落とす前の組）。関門（GATE）が優先、次が補助（AUX）、
    それ以外は一般。チケット制御が無効なら関門は足されていない。生の git はそのときでも止めるが、
    一般の保護として扱う。

    `>` の数の上限で上げた拒否は、シェルから書き込む形の保護（関門の置き場を守る 2 本を含む）を
    生の文字列に適用していない代わりの拒否。関門の穴にならないよう、チケット制御が有効なら関門にする。
    """
    if capped:
        return GATE if conf.tickets_enabled else GENERAL
    if ticket_reason:
        return GATE if conf.tickets_enabled else GENERAL
    if conf.tickets_enabled and ids & set(GATE_RULE_IDS):
        return GATE
    if ticket_guard.RAW_GIT_RULE_ID in ids:
        return AUX if conf.tickets_enabled else GENERAL
    return GENERAL


# 書き直しを求める形のうち、生の git や ccnavi の入口を隠しうるもの。語として現れる `git` と
# `ccnavi`（パスの一部の `/ccnavi/` は数えない）、保護済みの sh の名前。
_ENTRY_WORD = re.compile(
    r"(?<![\w/.-])(?:git|ccnavi)(?:\.exe)?(?![\w-])|ccnavi-[\w-]+\.sh", re.IGNORECASE
)


def _rewrite_kind(
    conf: settings.Settings,
    payload: hookio.Input,
    subject: str,
    inner: list[tuple[str, str, bool]],
    rewrites: list[tuple[str, str]],
) -> str:
    """書き直しを求める拒否の分類。生の git や ccnavi の入口を隠しうる形は、補助にする。

    実行時に決まるコマンド名（`c=git; $c push`）は、中身を読めないので生の git を隠しうる。
    呼び手がサブエージェントの回、語としての `git` / `ccnavi` / 保護済みの sh の名前が現れる回も
    同じ。ただし文字列の一部に `ccnavi` が入っているだけ（`ls /work/ccnavi/{src,tests}`）では
    補助にしない。チケット制御が無効なら補助は足さない。
    """
    if not conf.tickets_enabled:
        return GENERAL
    if payload.agent_id:
        return AUX
    if any(form == shellread.FORM_COMMAND_NAME for form, _ in rewrites):
        return AUX
    texts = [subject, *(layer for _, layer, _ in inner), *(text for _, text in rewrites)]
    return AUX if any(_ENTRY_WORD.search(text) for text in texts) else GENERAL


def refuse(
    stdout: TextIO,
    mode: str,
    record: audit.Record,
    verdict: str,
    parts: list[str],
    context: str = "",
    conf: settings.Settings | None = None,
    root: str = "",
    *,
    kind: str,
    updated_input: dict | None = None,
) -> int:
    """拒否か確認を返す。dry-run なら返す代わりに、返していたはずだと言う。

    kind は止めている箇所の分類（`modes.GENERAL`・`GATE`・`AUX`）。dry-run で報告だけにするのは
    GENERAL だけで、チケット制御の関門（GATE）と補助の保護（AUX）は dry-run でも止める。
    拒否の文面には「dry-run でもこの止めは効く」と 1 行添える。

    context はルールの `additionalContext`。判定と一緒にモデルへ渡す文で、
    dry-run では判定の代わりの文に続けて出す。止めない回でも文だけは届く形にして、
    enable に切り替えたときに初めて読まれる文を残さない。

    conf があれば、止めた回を数える（repeat）。同じ理由で同じ呼び出しを N 回止めたら、
    文面の末尾に「言い換えずに相談する」一文を足す。判定は変えない。数えるのは実際に
    止めた回（enable の deny）だけで、dry-run と確認は数えない。

    updated_input は起動の入力の更新（担当の案内）。確認（ask）に添えて返す。強制される拒否には
    添えない。dry-run で報告だけにする回は、呼び出しが進むので添える（案内であって、
    緩めることではない）。

    拒否は、失敗の連続（failstreak）にも数える。こちらは dry-run の deny も数える（dry-run は
    判定を変えないので、案内を出してよい）。連続が閾値に達した回は、文面の末尾に資料を引く案内を足す。
    """
    # 空行で区切るのは、1 件ずつが閉じた文であることを見た目でも保つため。
    reason = "\n\n".join(parts)
    decision = audit.DENY if verdict == rules.DENY else audit.ASK
    streak = ""
    if conf is not None and verdict == rules.DENY:
        streak = fail_streak_hint(conf, root, record)

    if mode == modes.DRY_RUN and modes.holds_in_dry_run(kind):
        reason += "\n\n" + DRY_RUN_HOLDS_NOTE
    if mode == modes.DRY_RUN and not modes.holds_in_dry_run(kind):
        record.decision, record.enforced = decision, False
        would = "stopped" if verdict == rules.DENY else "asked the user about"
        text = f"[ccnavi dry-run] {modes.ENABLE} would have {would} this call:\n" + reason
        if streak:
            text += "\n\n" + streak
        if context:
            text += "\n\n" + context
        _write_pass(stdout, text, updated_input)
        return EXIT_OK

    record.decision, record.enforced = decision, True
    if conf is not None and verdict == rules.DENY:
        n = repeat.count(conf.state, record)
        if n >= repeat.threshold(conf.deny_repeat):
            reason += "\n\n" + repeat.note(repeat.rule_of(record), n)
        if streak:
            reason += "\n\n" + streak
    if verdict != rules.DENY and updated_input is not None:
        hookio.write_pre_tool_use(
            stdout,
            decision=hookio.ASK,
            reason=reason,
            context=context,
            updated_input=updated_input,
        )
        return EXIT_OK
    hookio.write_verdict(
        stdout, hookio.DENY if verdict == rules.DENY else hookio.ASK, reason, context
    )
    return EXIT_OK


def fail_streak_hint(conf: settings.Settings, root: str, record: audit.Record) -> str:
    """拒否を失敗の連続に 1 回数え、案内を出す回ならその文を返す。出さない回と数えられない回は空。

    数えの失敗や案内を組む失敗で、判定を止めない。
    """
    n = failstreak.threshold(conf.fail_streak_count)
    try:
        got = failstreak.fail(conf.state, record.session, record.agent, n)
        return docsearch.fail_streak_note(conf, root, got) if got else ""
    except Exception:  # noqa: BLE001  案内の失敗で判定を止めない
        return ""


def subject_of(payload: hookio.Input) -> str:
    """このツールでルールを適用する欄を選ぶ（`policy.subjects.subject_of`）。"""
    return subjects.subject_of(payload)


def screen(
    tool: str, subject: str, record: audit.Record
) -> tuple[str, str, list[tuple[str, str, bool]], list[tuple[str, str]]]:
    """シェルのコマンドを、実際に実行される部分まで絞る。読み切れなかったときは
    record にそう書き残す。

    返すのは 4 つ。1 つめはルールを適用する読み。2 つめはそこから引用の中で切り出した
    コマンドを除いた読み（shellread.Reading.bare）で、文面の断りを決めるためだけに使う。
    シェルでないツールと読み切れなかったコマンドでは、1 つめと 2 つめは同じになる。

    ルールはコマンドについて書かれたものであって、文字列についてではない。
    git push を引用した文書は push ではないし、そこで拒否を返すことは、
    やっていないことをやったと読み手に告げることになる。

    読み切れないコマンドは生の文字列のまま見る。生の文字列には実行される部分が
    すべて含まれるので、捕まえるべきものが抜けることはない。その代わり、返す拒否は
    どちらの拒否なのかを明示する。

    3 つめは、実行役のコマンド（`env` `sudo` `sh -c` など）が中で実行するコマンドのリスト。
    1 つずつが（実行役のコマンドの名前, 中で実行されるコマンド, 引用の中から切り出した
    コマンドの層か）。読み切れないコマンドでもトークンに分けられる限り返る。Bash 以外は空。
    `cd` で移った先から見たパス（`shellread.Reading.moved`）も、実行役のコマンドの名前を
    `shellread.MOVED` にしてここに並ぶ。どちらも止める側のルールにだけ適用する。

    4 つめは、書き直しを求める形の（形, 表記）のリスト（shellread.Reading.rewrites）。
    Bash 以外は空。
    """
    if tool != "Bash":
        return subject, subject, [], []
    reading = shellread.read(subject)
    inner = []
    if reading.unwrapped:
        inner = list(
            zip(
                reading.runners,
                reading.unwrapped.split(shellread.SEP),
                reading.quoted_layers,
                strict=True,
            )
        )
    # `cd` で移った先から見たパスも、中で実行されるコマンドと同じリストに足す。
    # 書かれた表記のヒットの仕方は動かさず、適用する先を足すだけにする。止める側のルールにしか
    # ヒットしないので、`cd` を読み違えても、ヒットするはずのものがヒットしなくなることはない。
    inner += [
        (shellread.MOVED, command, False)
        for command in reading.moved.split(shellread.SEP)
        if command
    ]
    if reading.degraded:
        record.degraded = reading.reason
        return subject, subject, inner, reading.rewrites
    return reading.text, reading.bare, inner, reading.rewrites


def project_mismatch(
    conf: settings.Settings,
    root: str,
    t: tree.Tree,
    full: str,
    index: dict[str, ticket_model.Ticket] | None,
) -> str:
    """ワークツリーの元リポジトリと、そこに結び付く承認済みチケットの `project:` が
    違えば、その理由の文。

    範囲の宣言ではなく取り違えなので、ルールより先に見る。ルールが
    allow と言っていても通さない。子は親から継ぐ。
    行き先のツリーが誰のものかは、そのツリーの `.git` が指す先で決まっている。

    index は承認済みチケットの索引。呼び手がチケットの範囲の判定と共有して渡す。
    ワークツリーの中への書き込みでは必ず読んである。
    """
    if t.is_main:
        return ""
    # 読むのは正とする側（親のツリー）のチケット。子のツリーにも checkout されているが、
    # 閉じるのも着手の欄を書くのも親のツリーの側なので、そこを読まないと閉じた
    # チケットの範囲がいつまでも判定に使われる。
    assert index is not None
    # ticket_verdict と同じ引き方（大文字小文字はどの機械でも区別しない）。ここだけ厳密に引くと、
    # `I0001-01-01` と切ったワークツリーは範囲の判定に掛かりながら、取り違えの検査を素通りする。
    ticket = tree.lookup(index, t.name)
    if ticket is None:
        return ""
    parent = index.get(ticket.parent) if ticket.is_child else None
    owner = parent.project if parent is not None else ticket.project
    if owner == t.project:
        return ""
    source = ticket.path
    return "\n".join(
        [
            f"[ccnavi] {reasons.CODE_TICKET_PROJECT} (source: {source})",
            f"subject: {full}",
            texts.say(
                root,
                "DENY_TICKET_PROJECT_MISMATCH",
                {
                    "ticket": ticket.ticket,
                    "title": ticket.title,
                    "owner": owner or "(workspace)",
                    "cut_from": t.project or "(workspace)",
                    "worktree": t.name,
                },
            ),
        ]
    )


def flow_lock(
    conf: settings.Settings,
    root: str,
    payload: hookio.Input,
    full: str,
    copies: list[ticket_model.Ticket] | None,
) -> tuple[str, list[ticket_model.Ticket] | None]:
    """着手中の子のフローへの書き込みなら、その理由の文。と、読んだ承認済みチケットのリスト。

    適用するのは解いたパス（`full`）と、解く前のパス（payload のパスを cwd から繋いで `..` を
    整えただけのもの）と、ディレクトリだけを解いたパスの 3 通り。フローのファイルかその途中が
    リンクでも、どれかのパスが置き場にヒットすれば止める（止める向きだけ）。承認済みの領域の
    `flows/` の下でなければ承認済みチケットを読まない。リストは識別子でまとめる前のもので、
    どのツリー上のチケットでも着手中なら止める。
    """
    field = SUBJECT_FIELDS.get(payload.tool_name, "")
    value = payload.field_value(field) if field else ""
    if payload.tool_name == "NotebookEdit":
        value = value or payload.field_value("file_path")
    spelled = fsio.spelled_path(value, payload.cwd)
    # 3 通り目は、ディレクトリだけを解いてファイルの名前は残したパス。リンク越しに置き場へ届き、
    # 置き場のファイルがまたリンクのとき、解いた先も解く前も置き場のパスにならない。
    parent = fsio.parent_resolved(spelled)
    for path in dict.fromkeys(p for p in (full, spelled, parent) if p):
        found = flow.locate(conf, root, path)
        if found is None:
            continue
        if copies is None:
            copies, _ = approval.scan(conf, root)
        child = flow.lock_hit(copies, found[1], found[0])
        if child is not None:
            return flow.locked_message(root, conf, child, path), copies
    # ハードリンクはパスに置き場が出ないので `locate` ではヒットしない。書き込み先が在って
    # 名前が 2 つ以上あるときだけ、着手中の子のフローと同じ中身（inode）かを見る。
    target = full or spelled
    if target and flow.hard_linked(target):
        if copies is None:
            copies, _ = approval.scan(conf, root)
        child = flow.inode_hit(conf, root, copies, target)
        if child is not None:
            return flow.locked_message(root, conf, child, target), copies
    return "", copies


# 判定の強さ。ルールの判定とチケットの判定を合わせるとき、強い側を採る。
# 何も言わない（空）がいちばん弱い。同じ強さならルールを採る。
STRENGTH = {rules.DENY: 3, rules.ASK: 2, rules.ALLOW: 1, "": 0}


def ticket_verdict(
    conf: settings.Settings,
    root: str,
    tool: str,
    full: str,
    t: tree.Tree | None,
    index: dict[str, ticket_model.Ticket] | None,
    rule_hit: tuple[str, str] | None = None,
) -> tuple[str, str, str, str]:
    """チケットが承認された範囲について何を言うかを返す。判定と、理由の文と、注記と、理由のコード。

    コードは空のことが多い。記録に残す文面を呼び手が決められないとき（範囲の外ではなく
    チケット自体が信頼できないとき）だけ、ここが返す。

    キーはファイルの行き先。解いた先が `.claude/worktrees/<名前>/` の中なら、その名前と
    同じ識別子の承認済みチケットで判定する。ワークスペースルートの直下ならチケットは無く、ルールだけで判定する。
    呼び出し元の cwd も agent_id も使わない。

    範囲の中は allow、範囲の `ask` は ask、範囲の外とチケットの `deny` は deny。
    チケットが境界を明示している以上、外に出たことは「宣言に反した」になる。
    子は親の範囲とフェーズ定義の上限で切り詰め、厳しい側を採る（phase_scope.scope_verdict）。
    承認は範囲の超過を警告で通すので、超えた分はここで止まる。止めた上限を `limit:` 行で
    名指しする。ルールの判定と比べて強い側を採るのは呼び手。

    注記はいまは付けない（前は、子の番号の定義が `phases.yml` から読めないときに「定義では
    切り詰めていない」と添えていた。定義は親のコピーから引くようになり、コピーに無ければ親も子も
    止まるので、その形は無くなった）。返す形は変えない。

    t は full の行き先のツリー（`ruleload.rules_for` が返したもの。cwd で置き換える前）。
    ここで引き直さない。引き直すと、索引を読むかを決めたときのツリーとファイルシステムの
    変化で食い違う余地が残る。

    index は承認済みチケットの索引。1 回の判定で走査を 1 度にするため、呼び手が
    プロジェクトの食い違いの点検と共有して渡す。t がワークツリーなら、呼び手は同じ t で
    索引を読んである。

    rule_hit は、ルールがヒットしていたときの (ルールの id, タイプ)。チケットがそれより
    厳しい判定を返すときは文面で名指しする。ルールは通しているのに止まった理由が
    読めないと、受け取った側はルールファイルを探しに行って見つけられない。

    チケットの置き場（提案と承認済みチケット）については何も言わない。承認された範囲の外に
    あるのが普通で、そこを deny にすると、いちど承認した範囲から出る方法が無くなる。

    処理フロー:
    1. チケット制御が無い・対象外のツール・ワークツリーでない・チケットが無い・置き場なら、
       何も言わずに空を返す（`tree.lookup`・`ticket_places.is_unscoped`）。
    2. 親のワークツリーなら、書き込みを DENY にする（索引か `wrapguard.ticket_role` のどちらかが
       親と言えば親とする）。
       チケットが `blocked` のときは後段の範囲の判定で止める。
    3. 子なら、範囲の判定を `phase_scope.scope_verdict` で取る。ALLOW ならそのまま通す。
    4. チケットが信頼できない（`blocked`）なら、引っかかった検査を名指しして DENY にする。
    5. ASK と DENY の文面を組む。ルールの判定より強いときは、ルールの id を名指しする。
    6. 止めた上限（フェーズの型・親・範囲の外）ごとに、次にすることを文面に書く。
    """
    if not conf.tickets_enabled or tool not in SCOPE_TOOLS:
        return "", "", "", ""
    if t is None or t.is_main:
        return "", "", "", ""
    assert index is not None
    # 表記の違いはどの機械でも許す。SubagentStart / SubagentStop / 実行後チェックと
    # 同じ引き方。ここだけ厳密に引くと、`I0001-01-01` と切ったワークツリーは案内では
    # 有効と言われながら判定では権限モード任せになる。
    ticket = tree.lookup(index, t.name)
    if ticket is None:
        return "", "", "", ""
    rel = tree.relative(t, full)
    if ticket_places.is_unscoped(rel, conf.tickets, conf.approved):
        return "", "", "", ""
    # 親のワークツリーは作業の場ではない。成果は子のワークツリーで作り、子の finish が親へ合流
    # する。親の範囲は子の上限として働くだけで、親のワークツリーへの直接の書き込みを通す根拠に
    # ならない。置き場（上の `is_unscoped`）だけが書ける。親かどうかは、索引と、ワークツリー自身の
    # チケット（`wrapguard.ticket_role`。サブエージェントの禁止と同じ引き方）のどちらかが親と
    # 言えば親とする。食い違うときは止める側に倒す。
    # チケット自体が信頼できないとき（`blocked`）は、下の範囲の判定が DENY_TICKET_BLOCKED で止める。
    is_parent = not ticket.is_child or (
        wrapguard.ticket_role(conf, root, t.name)[0] == wrapguard.ROLE_PARENT
    )
    if is_parent and not ticket.blocked:
        return (
            rules.DENY,
            "\n".join(
                [
                    f"[ccnavi] {reasons.CODE_TICKET_PARENT} (source: {ticket.path})",
                    f"subject: {full}",
                    f"ticket: {ticket.ticket} ({ticket.title}), worktree {t.name}",
                    texts.say(
                        root,
                        "DENY_TICKET_PARENT",
                        {
                            "ticket": ticket.ticket,
                            "title": ticket.title,
                            "worktree": t.name,
                            "path": full,
                            "worktree_path": t.root,
                        },
                    ),
                ]
            ),
            "",
            reasons.CODE_TICKET_PARENT,
        )
    parent = index.get(ticket.parent) if ticket.is_child else None
    # 定義は親に固定したコピー（`phases:`）から引く。`phases.yml` は開かない（承認のあとに直しても
    # 進行中の親の子の範囲は変わらない）。コピーに定義が無い親は判定の側の検査で止まり、
    # 子も `blocked` で止まる。
    notice = ""
    pt = phase_scope.type_for(ticket, parent)
    found = phase_scope.scope_verdict(ticket, parent, pt, rel)
    if found.verdict == rules.ALLOW:
        return rules.ALLOW, "", notice, ""

    if found.limit == phase_scope.LIMIT_BLOCKED:
        # 範囲の外に書いたのではなく、チケット自体が信頼できない。範囲を見せても
        # 直しようが無いので、代わりに引っかかった検査を名指しする。
        return (
            rules.DENY,
            "\n".join(
                [
                    f"[ccnavi] {reasons.CODE_TICKET_BLOCKED} (source: {ticket.path})",
                    f"subject: {full}",
                    f"ticket: {ticket.ticket} ({ticket.title}), worktree {t.name}",
                    f"problem: {ticket.blocked}",
                    texts.say(
                        root,
                        "DENY_TICKET_BLOCKED",
                        {"ticket": ticket.ticket, "title": ticket.title, "worktree": t.name},
                    ),
                ]
            ),
            notice,
            reasons.CODE_TICKET_BLOCKED,
        )

    area = ", ".join(ticket.paths(rules.ALLOW) + ticket.paths(rules.ASK)) or "(空)"
    source = ticket.path
    head = [
        f"subject: {full}",
        f"ticket: {ticket.ticket} ({ticket.title}), worktree {t.name}",
        f"scope: {area}",
    ]
    decided = rules.ASK if found.verdict == rules.ASK else rules.DENY
    if rule_hit is not None and STRENGTH[decided] > STRENGTH[rule_hit[1]]:
        rule_id, rule_type = rule_hit
        head.append(
            f"rule: {rule_id} ({rule_type}) lets this through, "
            "but the ticket for this worktree narrows it"
        )
    if found.verdict == rules.DENY:
        # 範囲の外ではなく、チケットが deny と書いた項にヒットしたなら、どの項かを名指しする。
        for owner in (ticket, parent):
            entry = owner.entry_for(rel) if owner is not None else None
            if entry is not None and entry.decision == rules.DENY:
                head.append(f"ticket entry: deny {entry.glob or entry.regex}")
                break
    if found.verdict == rules.ASK:
        return (
            rules.ASK,
            "\n".join(
                [
                    f"[ccnavi] {reasons.CODE_TICKET_ASK} (source: {source})",
                    *head,
                    "This path is one the ticket marks `ask`: the user looks at each write here. "
                    "Say what you are changing and why.",
                ]
            ),
            notice,
            "",
        )
    # 上限ごとに次にすることが違う。子の範囲の外なら提案し直し、親や定義の上限の外なら、
    # 範囲を広げても通らない（承認で超過を見せたうえで止めている）。
    case, limit = "area", ""
    if found.limit == phase_scope.LIMIT_TYPE and found.type is not None:
        pt = found.type
        head.append(f"limit: phase type {pt.title} ({pt.id}): {', '.join(pt.scope_globs)}")
        case, limit = "type", f"{pt.title}（{pt.id}）"
    elif found.limit == phase_scope.LIMIT_PARENT and parent is not None:
        parent_area = ", ".join(parent.paths(rules.ALLOW) + parent.paths(rules.ASK)) or "(空)"
        head.append(f"limit: parent {parent.ticket}: {parent_area}")
        case, limit = "parent", parent.ticket
    body = texts.say(
        root,
        "DENY_TICKET_SCOPE",
        {
            "ticket": ticket.ticket,
            "title": ticket.title,
            "worktree": t.name,
            "path": full,
            "limit": limit,
        },
        {"what": case, "why": case, "next": case},
    )
    return (
        rules.DENY,
        "\n".join([f"[ccnavi] {reasons.CODE_TICKET_SCOPE} (source: {source})", *head, body]),
        notice,
        "",
    )


def undeclared_verdict(permission_mode: str, degraded: str, guard_unwatched: str = "") -> str:
    """どのタイプも言及しなかった呼び出しを、権限モードごとにどう扱うか。

    渡すのは「ルールが言及していない」ときだけ。読み切れなかったコマンド
    （degraded）は渡さない。ccnavi が読めなかったという事実は判定の結果に
    現れないので、渡すと「判断材料が足りない」ことが誰にも伝わらないまま
    モードの既定になる。読めなかったことを言えるのはここだけ。

    知らないモードは ask として扱う。名前が 1 つ増えたときに、それが確認なしに通るのではなく
    確認になるように。設定漏れがガードの消失にならない側へ既定を置く。

    確認できる者が居ないモードは、既定では通さない。そこで ask を
    返しても「誰も答えないまま通る」になってしまうため。CCNAVI_GUARD_UNWATCHED を
    disable にしたプロジェクトだけ、ここも渡す側になる。読み切れなかった
    呼び出しは、その設定でも渡さない。渡す先が「確認しない」と決まっている以上、
    読めなかったことを言える場所が他に無い。
    """
    if permission_mode in PERMISSION_NO_JUDGE:
        if degraded or guard_unwatched != selfguard.DISABLE:
            return rules.DENY
        return HANDOVER
    if not degraded and permission_mode in PERMISSION_JUDGED:
        return HANDOVER
    return rules.ASK
