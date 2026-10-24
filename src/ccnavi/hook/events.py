"""hook のイベントごとの手順。1 回の起動で何が起きるかは、ここを上から読めば分かる。

実行前チェックは judge、サブエージェントの始まりと終わりは subagent、実行後
チェックは post に本体があり、ここはイベント名からそれらへ振り分け、記録と応答を
繋ぐだけ。イベントが増えるたびに 1 本の関数が長くならないように、イベント 1 つに
つき関数 1 つ。
"""

from __future__ import annotations

import contextlib
import dataclasses
import functools
import io
import os
import sys
import time
from typing import TextIO

from ..infra import directrec, fsio, hookio, modes, pathcase, settings, shellread, tree
from ..infra.modes import EXIT_BLOCK, EXIT_OK
from ..policy import (
    builtin,
    ctxfile,
    ruleload,
    rules,
    selfguard,
    selfguard_shell,
    selfguard_targets,
)
from ..records import audit, busy, failstreak, prune, repeat, stopops, stopreview, turn
from ..tickets import (
    approval,
    approval_checks,
    branchfind,
    direct_candidates,
    learning_notice,
    ops_stop,
    ops_stop_feedback,
    ops_stop_integrate,
    ops_stop_request,
    overview,
    pending_integrate,
    phase,
    promptsync,
    session_rejects,
    ticket_guard,
)
from . import (
    ccnavimd,
    docsearch,
    judge,
    post,
    post_findings,
    projskills,
    proposalhint,
    stopjudge,
    subagent,
)

# SessionStart で、終わっていない integrate の記録を確かめる時間の上限（秒）。
PENDING_SECONDS = 2.0

# `match: Stop` のルールで止めた回の理由コード。記録の `code` と、止めた文の頭に出る。
CODE_RULE_NUDGE = "NUDGE_STOP_RULE"
# `match: Stop` のルールで止めたとき、ルールの文の前に必ず置く文。ルールの文や
# 指したファイルが差し替わっても、止めた回の扱いがここで決まるように、実行ファイルに持つ。
STOP_PREFACE = (
    "これはタスクの続きではない。ここまでの作業の振り返りだけをし、ほかの作業は始めないでください。"
    "このターンがユーザへの問い・確認で終わっていたなら、振り返りの後にその問いを最後にもう一度書いてください。"
    "振り返って何も無ければ「振り返り: 無し」とだけ答えてください。"
)


def decide(
    stdout: TextIO,
    stderr: TextIO,
    mode: str,
    conf: settings.Settings,
    root: str,
    payload: hookio.Input,
    record: audit.Record,
    deadline: float,
) -> int:
    """イベントごとの処理に振り分ける。戻り値は hook の終了コード（`EXIT_OK`）。

    振り分けだけをここに置く。イベントが増えるたびに 1 本の関数が長くなると、
    どのイベントで何が起きるのかを読むのに全部を読むことになる。

    処理フロー:
    1. 無効モードなら記録だけ残して抜ける。Stop のときは判定役の計画だけ書く
       （`_plan_stop_review`）。
    2. 判定役の呼び出しは、PreToolUse のときだけ `stopjudge.before` に渡す。
       ほかは判定せず、記録に SKIP を残して返す。
    3. PreToolUse は `stopjudge.before` が処理したら抜ける。そうでなければ提案を
       記録する（`_track_proposals`）。
    4. イベントごとに 1 つの関数へ渡す（`judge.decide_before`・`decide_after`・
       `decide_after_failure`・`decide_at_start`・`decide_at_prompt`・`decide_at_stop`・
       `subagent.at_start`・`subagent.at_stop`）。
    5. どれにもヒットしないイベントは、判定をしないものとして記録して抜ける。
    """
    if mode == modes.DISABLE:
        record.decision, record.reason = audit.SKIP, audit.REASON_MODE_DISABLED
        if payload.event == hookio.STOP:
            # 判定役を登録していても待たせない（検査省略の計画を書く）。
            _plan_stop_review(stderr, conf, root, payload, mode, False, None, None)
        return EXIT_OK
    if stopjudge.is_judge(conf, payload):
        # 判定役の呼び出しは、ほかの hook の副作用を動かさない（Stop の AI 判定の判定役）。
        if payload.event == hookio.PRE_TOOL_USE:
            stopjudge.before(stdout, mode, conf, payload, record)
        else:
            record.decision, record.reason = audit.SKIP, "stop-review-judge"
        return EXIT_OK
    if payload.event == hookio.PRE_TOOL_USE and stopjudge.before(
        stdout, mode, conf, payload, record
    ):
        return EXIT_OK
    _track_proposals(conf, payload)
    if payload.event in (
        hookio.PRE_TOOL_USE,
        hookio.POST_TOOL_USE,
        hookio.POST_TOOL_USE_FAILURE,
    ):
        # ターン中の印の更新時刻を新しくする（サブエージェントの呼び出しも、同じターン内）。
        turn.touch(conf.state, payload.session_id)
    if payload.event == hookio.PRE_TOOL_USE:
        return judge.decide_before(stdout, stderr, mode, conf, root, payload, record, deadline)
    if payload.event == hookio.POST_TOOL_USE:
        return decide_after(stdout, stderr, mode, conf, root, payload, record)
    if payload.event == hookio.POST_TOOL_USE_FAILURE:
        return decide_after_failure(stdout, stderr, mode, conf, root, payload, record)
    if payload.event == hookio.SESSION_START:
        return decide_at_start(stdout, stderr, mode, conf, root, payload, record, deadline)
    if payload.event == hookio.USER_PROMPT_SUBMIT:
        return decide_at_prompt(stdout, stderr, mode, conf, root, payload, record)
    if payload.event == hookio.STOP:
        return decide_at_stop(stdout, stderr, conf, root, payload, record, deadline, mode)
    if payload.event == hookio.SUBAGENT_START:
        return subagent.at_start(stdout, stderr, conf, root, payload, record)
    if payload.event == hookio.SUBAGENT_STOP:
        return subagent.at_stop(stdout, stderr, mode, conf, root, payload, record)
    # 判定を持たないイベントは誤りではない。想定していない登録が
    # 作業を止めてはいけない。
    record.decision, record.reason = audit.SKIP, audit.REASON_EVENT_NOT_CHECKED
    return EXIT_OK


def watch_context(
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    record: audit.Record,
    raw: approval.Raw | None = None,
) -> tuple[list[post.Watched], post_findings.ScopeGuard | None]:
    """ターンの区切りでワークツリーを見る 2 つが、共通して使う持ち物。

    保護領域も範囲も、実行前チェックと同じ経路で解く。別に書くと、実行前に
    通った書き込みがターンの終わりに報告される（あるいはその逆）ことになり、
    どちらが本当の宣言なのかを誰も言えなくなる。

    `raw` は承認済みチケットの置き場を呼び手が読んだもの（`scope_guard`）。
    """
    return watched_for(stderr, conf, root, record), scope_guard(conf, root, raw)


def watched_for(
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    record: audit.Record,
    payload: hookio.Input | None = None,
) -> list[post.Watched]:
    """実行後に見るツリーと、それぞれに適用するルール。

    payload が無ければ全部のツリー（ターンの区切り）。あればワークスペースルートと、
    この呼び出しが触ったツリー（パスを持つツールは行き先、Bash は cwd）。
    ルールの引き方は実行前チェックと同じで、共通レイヤーにそのツリーのレイヤーを足した和。
    別に書くと、実行前に通った書き込みがターンの終わりに報告される。

    ガード自身の保護は、実行前と同じ関数から足す。自己防衛の設定（`CCNAVI_GUARD_CORE_FILES`）が
    disable でなければ、hook・settings・scripts・共通の設定 3 本の名前で書くツール用の 4 本
    （`selfguard_shell.add_named_rules`）を、各ツリーのルールの組に足す。共通のルールファイルが
    読めない間も同じ。チケット制御が有効なら、承認済みの置き場の名前で書くツール用の 1 本
    （`ticket_guard.approved_write_rule`）も足す。自己防衛の値には依らない。
    `guard-project-home` 全体は足さない。範囲を広げると、ccnavi ディレクトリの下の正規の
    書き込みが報告に並ぶ。
    """
    ws = tree.main_tree(root)
    # 横で作業する指定が有効なメインのチェックアウトは、触ったツリーによらず毎回見る
    # （エージェントは作業ワークツリーで動いていて、書き換えはそのメインのチェックアウトに起きる）。
    sbs = directrec.side_by_side_roots(root, conf.projects)
    sbs_keys = {pathcase.fold(t.root) for t in sbs}
    if payload is None:
        trees = tree.all_trees(root, conf.projects)
    else:
        trees = [ws]
        where = record.subject if payload.tool_name in ruleload.PATH_TOOLS else payload.cwd
        t = tree.tree_of(root, where, conf.projects)
        if t is not None and t.root != ws.root:
            trees.append(t)
        have = {pathcase.fold(x.root) for x in trees}
        trees += [x for x in sbs if pathcase.fold(x.root) not in have]
    core_on = conf.guard_core_files != selfguard.DISABLE
    loaded: dict[str, tuple[rules.RuleSet, str]] = {}
    out = []
    for t in trees:
        if t.project not in loaded:
            # 共通レイヤーはツリーのレイヤーごとに読み直す（レイヤーを足すと集合が書き換わるため）。
            # 苦情は同じなので
            # 最初の 1 回だけ書く。レイヤーの苦情はツリーごとに違うので、add_layers はそのまま書く。
            said = stderr if not loaded else io.StringIO()
            rule_set, source = ruleload.load_rules(said, conf, record, root)
            if source != builtin.SOURCE:
                ruleload.add_layers(
                    stderr, rule_set, ruleload.layer_for(conf, root, t), root, record
                )
            if core_on:
                selfguard_shell.add_named_rules(rule_set, conf.project_home, root)
            if conf.tickets_enabled:
                rule_set.deny.append(ticket_guard.approved_write_rule(conf.approved, root))
            loaded[t.project] = (rule_set, source)
        rule_set, source = loaded[t.project]
        out.append(post.Watched(t, rule_set, source, pathcase.fold(t.root) in sbs_keys))
    return out


def scope_guard(
    conf: settings.Settings, root: str, raw: approval.Raw | None = None
) -> post_findings.ScopeGuard | None:
    """承認済みチケットを、実行後の側から適用する持ち物。チケット制御が disable なら None。

    `raw` は呼び手が `approval.read_raw` で読んだもの。渡せば置き場を読み直さない。
    """
    if not conf.tickets_enabled:
        return None
    copies, _ = approval.scan(conf, root, raw=raw)
    # 定義の上限は親に固定したコピー（`phases:`）から引く（`phase_scope.type_for`）。
    # `phases.yml` は読まない。
    return post_findings.ScopeGuard(
        root=root,
        copies=approval_checks.by_id(copies),
        projects=conf.projects,
        tickets=conf.tickets,
        approved=conf.approved,
    )


def decide_at_prompt(
    stdout: TextIO,
    stderr: TextIO,
    mode: str,
    conf: settings.Settings,
    root: str,
    payload: hookio.Input,
    record: audit.Record,
) -> int:
    """ユーザが何か言ったとき。ターンの基準をここで取る。

    基準を取るだけの回は何も返さない。このイベントで返した文はモデルのコンテキストに入るので、
    まだ何も起きていない時点で文を足さないためで、返すのは次の 6 種類の文だけ。空でない文は
    空行で区切って 1 つの `additionalContext` にまとめる。

    1. 設定ファイルの変更の知らせ（`guard`）。ユーザの発言ではない回（バックグラウンド作業の
       完了通知など）に、ターンの間に動いた設定ファイルを戻した旨を言う
    2. 共通レイヤーと工程スキルをプロジェクトへコピーした知らせ（`promptsync.sync`）。コピーした
       ファイルのコミットを案内する。ユーザの発言のときだけコピーする
    3. 依頼文の issue・MR の指定への指示（`branchfind.prompt_context`）。着手の前に紐づく
       ブランチを探してユーザに確かめる
    4. 終わったサブエージェントの子の範囲を確かめられなかった印（`subagent.unchecked_scope`）
    5. 終わったサブエージェントの結果の知らせ（`subagent.outcome_notices`）
    6. 手作業で確かめる旨（`_manual_confirm_note`）

    どれも文を足すだけで、作業は止めない。

    承認されたことは伝えない。ボードの承認は拡張が承認の文（`agree_screen.approved_text`）を渡し、
    ほかの経路の承認は、エージェントが `ccnavi-ticket.sh status` で聞く。hook が起点を取って
    増えた承認を数える形は、セッションの開始時の取り込みで届いた承認を起点に含めて取りこぼした。

    承認済みチケットの置き場は、ここで 1 度だけ読んで範囲（`scope_guard`）に渡す。

    ユーザの発言なら、まず共通レイヤーと工程スキルをプロジェクトへコピーする（`promptsync.sync`）。
    コピーするのはツール呼び出しの外のここだけで、コピーした分もユーザの変更と同じに受け入れるので、
    ミラーは実行後チェックとバックアップと復元から特別扱いされない。

    ユーザの発言なら、前のターンの終わりからここまでの変更を、ユーザの手によるものとして
    受け入れる。ccnavi 自身の設定ファイルのバックアップを今の状態に取り直し（`selfguard.accept`）、
    `deny` と宣言された場所の今の変更を報告済みの記録に足す（`post.at_prompt`）。どちらも、
    次のツール呼び出しが、ユーザの編集や削除をその呼び出しの変更として戻さないようにするため。

    ユーザの手によるものと言い切れないときは受け入れない。バックグラウンド作業の完了通知
    （`from_background_task`）も UserPromptSubmit を起こすが、その間に動いたプロセスの変更は
    ユーザの変更と区別できない。前のターンの終わりに、動かしている作業が残っていた
    （`busy`。Stop の payload の `background_tasks`・`session_crons`）ときも、次の発言までの変更は
    その作業が書いたかもしれない。どちらも、これまでどおり次のツール呼び出しの前と同じ照合
    （`selfguard.before`）をここで行い、消えた設定ファイルは戻す。ターンの基準は受け入れの
    有無に関わらず取り直す。
    """
    # ユーザの発言とバックグラウンド作業の完了通知は、どちらもターンを始める。
    turn.begin(conf.state, payload.session_id)
    by_user = not payload.from_background_task() and not busy.is_busy(
        conf.state, payload.session_id
    )
    # 共通レイヤーと工程スキルのコピーを、受け入れの前に書く。書いた分が受け入れの対象になり、
    # 次のツール呼び出しが戻さない。
    mirrored = ""
    if by_user:
        try:
            mirrored = "\n".join(promptsync.sync(conf, root))
        except Exception as exc:  # noqa: BLE001  同期の失敗で、このあとの受け入れまで飛ばさない
            stderr.write(f"ccnavi: ミラーの同期で想定外の失敗: {exc}\n")
    found = (selfguard.accept if by_user else selfguard.before)(
        modes.effective_setting(mode, conf.guard_core_files),
        conf.state,
        payload.session_id,
        root,
        selfguard_targets.targets(
            root, conf.rules, conf.bin, ruleload.layer_files(conf, root), conf.projects
        ),
    )
    guard = ""
    if found:
        record.guarded = [f"{o.target.key}:{o.action}" for o in found]
        if by_user:
            # 受け入れの知らせは、記録の `guarded` と、失敗だけ標準エラーに残す。実行前・実行後の
            # 知らせ（`selfguard.report`）は「設定ファイルが変更された」と言い、「自分で書き換えると
            # 戻る」と結ぶ。受け入れの結果には合わないので、モデルの文脈には足さない。
            for o in found:
                if o.action == selfguard.ACTION_FAILED:
                    stderr.write(f"ccnavi: {o.target.label}: {o.detail}\n")
        else:
            guard = selfguard.report(found)
    raw = approval.read_raw(conf, root) if conf.tickets_enabled else None
    watched, scope = watch_context(stderr, conf, root, record, raw)
    post.at_prompt(
        stderr,
        conf.state,
        (conf.state, conf.log),
        watched,
        scope,
        payload,
        record,
        (conf.tickets, conf.approved),
        root,
        accept=by_user,
    )
    hint = branchfind.prompt_context(
        conf, root, payload.prompt, payload.cwd, payload.session_id, stderr
    )
    # バックグラウンドのサブエージェントが範囲を確かめられないまま終わり、そのあと親が
    # ツールを使わずにターンを終えたなら、印はここまで残っている。次の依頼の前に言う。
    unchecked = subagent.unchecked_scope(conf.state, payload) if conf.tickets_enabled else ""
    outcomes, told = (
        subagent.outcome_notices(conf.state, payload) if conf.tickets_enabled else ("", [])
    )
    manual = _manual_confirm_note(stderr, conf, root, payload, raw)
    hint = "\n\n".join(p for p in (guard, mirrored, hint, unchecked, outcomes, manual) if p)
    if hint:
        hookio.write_context(stdout, hookio.USER_PROMPT_SUBMIT, hint)
    if told:
        # 出力に書き終えてから「通知済み」の印を付ける（書く前に止まったら次の機会に出し直す）。
        subagent.confirm_outcomes(conf.state, told)
    return EXIT_OK


def _manual_confirm_note(
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    payload: hookio.Input,
    raw: approval.Raw | None,
) -> str:
    """ユーザが子を手で done/ に動かして共有していたら、次の発言の前に confirm を案内する。

    Stop の促し（`_request_nudge`）と状態の記録を共有するので、ここで言えば Stop では繰り返さない。
    サブエージェントの中、チケット制御が無いとき、状態の置き場が無いときは何も言わない。
    """
    if not conf.tickets_enabled or payload.agent_id or not conf.state:
        return ""
    found = ops_stop_request.pending_at_stop(root, conf, payload.cwd, raw)
    if (
        found is None
        or found.step != ops_stop_request.STEP_CONFIRM
        or ops_stop_request.nudged_before(conf.state, payload.session_id, found)
    ):
        return ""
    failed = ops_stop_request.remember_nudge(conf.state, payload.session_id, found)
    if failed:
        stderr.write(f"ccnavi: 手動移動の案内を残せないので、案内しない: {failed}\n")
        return ""
    # 発言の内容を読まずに出すので、Stop の促しより弱く言う（許可の要否ではなく状態の知らせ）。
    review_sh = settings.script_command(root, "ccnavi-review.sh")
    return (
        f"[ccnavi] 親 {found.parent.ticket} のフェーズ {found.number} は、ユーザが子 "
        f"{', '.join(found.children)} を手で done/ に動かして共有しています"
        "（レビュー完了確認待ち）。この発言が別の用件なら触れずに進めてよい。"
        f"確定するなら '{review_sh} confirm --phase {found.number}' を実行する。"
        "移動の前に付いた未解決の指摘だけが版つきで受け入れられ、移動の後に付いたものがあれば止まる。"
    )


def decide_at_stop(
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    payload: hookio.Input,
    record: audit.Record,
    deadline: float | None = None,
    mode: str = modes.ENABLE,
) -> int:
    """ターンが終わったとき。宣言した保護領域の今の状態をユーザへ報告する。

    宛先がユーザなので `systemMessage` で返す。呼び出しごとの報告はモデルへ届く
    経路に載せてあり、そこは既に足りている。足りていないのは、ターンが終わった
    あとにユーザが「結局どこが変わったのか」を 1 度で見る場所のほう。

    報告のためには止めない。このイベントで止めることはターンを続けさせる意味になり、
    報告のために作業を終わらせない形になる。報告は言うだけにする。

    dry-run でも報告する。ここは呼び出しにもワークツリーにも手を
    出さず、見えたことを言うだけなので、モードが約束しているものを何ひとつ破らない。
    むしろ dry-run は「まだ何も適用していないが何が起きているか」を見るためのモードなので、
    ここが何も言わないと見る手立てが減る。

    止めるのは 1 つだけ。cwd のワークツリーのチケットが、作業を終えたように見えるのに
    `finish` されていないとき、1 回の連鎖に 1 回だけ止めて `finish` か続ける理由を促す
    （`_finish_nudge`）。dry-run でも止める。Stop でモデルに文を届ける
    経路は止めて `reason` に書くことしか無く、止めないと促しがモデルに届かないため。
    止めるときも報告は同じ応答の `systemMessage` で返す。

    `finish` を促さなかった回に限り、レビューの依頼（`request`）までの道の途中なら止めて、次の一手を
    促す（`_request_nudge`）。承認済みの親に作業中の子が残っているか、子が全部閉じたのに
    依頼のマーカーが無いとき。止め方とモードの扱いは `finish` の促しと同じ。

    どちらも促さなかった回に限り、チケットに結び付かない直接作業が `integrate` まで進んでいなければ
    止めて、打ち方と、一時ワークツリーに展開した取り込み結果をユーザに確かめてもらう手順を促す（`_integrate_nudge`）。
    統合の前提を満たせないときは止めず、理由を報告へ足す。
    止め方とモードの扱いは `finish` の促しと同じ。

    どれも促さなかった回に限り、`match: Stop` のルールが渡す回ならそこで止める
    （`stop_rules_nudge`）。止め方とモードの扱いは `finish` の促しと同じ。

    承認済みチケットの置き場は、ここで 1 度だけ読んで範囲（`scope_guard`）と `finish` の促し
    （`ops_stop.unfinished_at_stop`）の両方に渡す。
    間の `post.at_stop` は報告するだけでワークツリーを
    戻さず、`repeat.at_stop` は state を読むだけなので、置き場のファイルは動かない。
    """
    # 動かしている作業が残っているか。次の発言での受け入れ（`decide_at_prompt`）が読む。
    busy.record(conf.state, payload.session_id, payload.busy)
    raw = approval.read_raw(conf, root) if conf.tickets_enabled else None
    watched, scope = watch_context(stderr, conf, root, record, raw)
    report = post.at_stop(
        stderr,
        conf.state,
        (conf.state, conf.log),
        watched,
        scope,
        payload,
        record,
        (conf.tickets, conf.approved),
        root,
    )
    # 同じ理由で繰り返し止めた呼び出し（repeat）。拒否の文面はモデルにしか届かないので、
    # 言い換えで回っているかもしれないことをユーザにも 1 度言う。
    repeated = repeat.at_stop(conf.state, payload.session_id, repeat.threshold(conf.deny_repeat))
    if repeated:
        report = f"{report}\n\n{repeated}" if report else repeated
    # AI 判定の差し戻しの次の Stop（AI による継続）は、既存の促しを `stop_hook_active` が偽のときと
    # 同じに 1 回だけ動かす。差し戻しのせいで既存の促しが黙るのを防ぐため。
    continuation = _ai_continuation(conf, payload)
    asked = dataclasses.replace(payload, stop_hook_active=False) if continuation else payload
    nudge = _finish_nudge(stderr, conf, root, asked, record, raw)
    if not nudge:
        # finish の促しで止める回は、依頼までの道の促しを出しも数えもしない。
        nudge = _request_nudge(stderr, conf, root, asked, record, raw)
    if not nudge:
        nudge = _feedback_nudge(stderr, conf, root, asked, record, raw)
    if not nudge:
        nudge = _subagent_nudge(conf, payload, record)
    if not nudge:
        # 上の促しで止める回は、直接作業の促しを出しも数えもしない。前提を満たせず促せない回は
        # 止めずに理由だけを報告へ足す。
        nudge, notice = _integrate_nudge(stderr, conf, root, asked, record, deadline)
        if notice:
            report = f"{report}\n\n{notice}" if report else notice
    if not nudge:
        # 上の促しで止める回は、ルールの促しを数えもしない。1 回の Stop で
        # 止める理由は 1 つだけにし、数えを進めて届かない回を作らない。
        nudge = stop_rules_nudge(stderr, conf, root, asked, record)
    # 既存の促しの連鎖を実行したあとで、AI 判定の計画を確定する（回数を進める副作用で
    # 判断が食い違わないように）。
    _plan_stop_review(stderr, conf, root, payload, mode, bool(nudge), continuation, raw)
    if nudge:
        # 止めた Stop はターンの続きなので、ターン中の印は残す（モデルはこのあとも動く）。
        hookio.write_stop_block(stdout, nudge, system=report)
        return EXIT_OK
    # ターンの終わり。ここから次の発言までは、ユーザの編集が戻されない。
    turn.end(conf.state, payload.session_id)
    if report:
        hookio.write_system_message(stdout, report)
    return EXIT_OK


def _track_proposals(conf: settings.Settings, payload: hookio.Input) -> None:
    """`_track_proposals_inner` の受け皿。記録の失敗で、ほかの hook の判定を止めない。"""
    try:
        _track_proposals_inner(conf, payload)
    except Exception as exc:  # 記録は判定の材料でしかない。落ちても作業を止めない
        sys.stderr.write(f"ccnavi: propose の操作実績を記録できない: {type(exc).__name__}: {exc}\n")


def _track_proposals_inner(conf: settings.Settings, payload: hookio.Input) -> None:
    """propose の操作実績とターンの開始を、state に記録する（Stop の AI 判定の材料）。

    agent hook を登録していなければ何もしない。判定にも出力にも影響しない。サブエージェントの
    呼び出しは数えない（propose はサブエージェントから呼べない）。
    """
    if not (conf.stop_review_agent and conf.state and payload.session_id) or payload.agent_id:
        return
    now = time.time()
    if payload.event == hookio.USER_PROMPT_SUBMIT:
        if not payload.from_background_task():
            stopops.start_turn(conf.state, payload.session_id, now)
            labels = [r.label() for r in branchfind.prompt_refs(payload.prompt)]
            stopops.note_refs(conf.state, payload.session_id, labels, now)
    elif payload.event == hookio.PRE_TOOL_USE and payload.tool_name == "Bash":
        command = payload.tool_input.get("command")
        for words, _place in shellread.placed(command) if isinstance(command, str) else []:
            ident = stopops.propose_ident(words)
            if ident:
                stopops.register(conf.state, payload.session_id, payload.tool_use_id, ident, now)
    elif payload.event in (hookio.POST_TOOL_USE, hookio.POST_TOOL_USE_FAILURE):
        stopops.bind(conf.state, payload.tool_use_id, now)
        file_path = payload.tool_input.get("file_path")
        if (
            payload.event == hookio.POST_TOOL_USE
            and payload.tool_name in ("Write", "Edit", "MultiEdit")
            and isinstance(file_path, str)
        ):
            ident = stopops.written_ident(file_path, conf.tickets)
            if ident:
                stopops.note_written(conf.state, payload.session_id, ident, file_path, now)


def _unstarted_ops(
    conf: settings.Settings, root: str, raw: approval.Raw | None, ops: list[dict]
) -> list[dict]:
    """着手済みの親に属する提案を除いた操作。着手済みの親への子の追加は、判定の対象外。"""
    if not ops:
        return []
    copies, _ = approval.scan(conf, root, raw=raw)
    by_id = approval_checks.by_id(copies)
    kept = []
    for op in ops:
        bound = tree.lookup(by_id, op["ident"])
        if bound is None or not bound.in_progress:
            kept.append(op)
    return kept


def _ai_continuation(conf: settings.Settings, payload: hookio.Input) -> dict | None:
    """この Stop が、AI 判定の差し戻しで続いた回なら、その試行を消費して返す。"""
    if not (payload.stop_hook_active and conf.stop_review_agent and conf.state):
        return None
    if payload.agent_id or not payload.session_id:
        return None
    return stopreview.consume_continuation(conf.state, payload.session_id)


def _approved_in_progress(
    conf: settings.Settings, root: str, cwd: str, raw: approval.Raw | None
) -> bool:
    """cwd のワークツリーに結び付いた、着手済みの承認済みチケットがあるか。"""
    here = tree.tree_of(root, cwd or os.getcwd(), conf.projects)
    if here is None or here.is_main:
        return False
    copies, _ = approval.scan(conf, root, raw=raw)
    bound = tree.lookup(approval_checks.by_id(copies), here.name)
    return bound is not None and bound.in_progress


# 候補にならなかった理由ごとの、ccnavi が確定する最終分類。ここに無い理由は計画を書かない。
_NOT_CANDIDATE_FINAL = {
    "stop_hook_active": stopreview.FINAL_SKIPPED,
    "existing_nudge": stopreview.FINAL_OUT_OF_SCOPE,
    "approved_in_progress": stopreview.FINAL_OUT_OF_SCOPE,
    "target_unresolvable": stopreview.FINAL_UNCHECKED,
    "criteria_unreadable": stopreview.FINAL_UNCHECKED,
    "disabled": stopreview.FINAL_SKIPPED,
    "not_main_agent": stopreview.FINAL_SKIPPED,
}


def _submitted(
    conf: settings.Settings, root: str, raw: approval.Raw | None, session: str
) -> list[dict]:
    """このセッションで propose した提案と、今の承認状態。"""
    idents = sorted({op["ident"] for op in stopops.turn_ops(conf.state, session, 0)})
    if not idents:
        return []
    copies, _ = approval.scan(conf, root, raw=raw)
    by_id = approval_checks.by_id(copies)
    rows = []
    for ident in idents:
        bound = tree.lookup(by_id, ident)
        rows.append(
            {
                "ident": ident,
                "approved": bound is not None,
                "in_progress": bool(bound is not None and bound.in_progress),
            }
        )
    return rows


def _plan_stop_review(
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    payload: hookio.Input,
    mode: str,
    nudged: bool,
    continuation: dict | None,
    raw: approval.Raw | None,
) -> None:
    """`_plan_stop_review_inner` の受け皿。

    AI 判定の失敗で、既存の促し（数え終えたもの）が出ずに Stop が終わらないようにする。
    """
    try:
        _plan_stop_review_inner(stderr, conf, root, payload, mode, nudged, continuation, raw)
    except Exception as exc:  # 計画は AI 判定のためだけのもの。落ちても Stop を止めない
        stderr.write(f"ccnavi: Stop の AI 判定の計画を作れない: {type(exc).__name__}: {exc}\n")


def _plan_stop_review_inner(
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    payload: hookio.Input,
    mode: str,
    nudged: bool,
    continuation: dict | None,
    raw: approval.Raw | None,
) -> None:
    """Stop の AI 判定の計画と試行を、共有キーで 1 件書く。

    判定役（agent hook）が `material` で取る。

    agent hook を登録していなければ（`CCNAVI_STOP_REVIEW_AGENT`）何もしない。書けなくても Stop は
    止めない。対象は Stop の機会（R1・R3）と、このターンの提案の最終の有効版（R2・R4）。
    提案の操作実績（`stopops`）で、着手済みの親に属さない提案があれば候補にする。

    処理フロー:
    1. 状態の置き場・セッション・判定役の登録のどれかが無ければ何もしない。
    2. 共有キー（`stopreview.shared_key`）を導く。導けなければ `no_key` を記録して抜ける。
    3. 前回の Stop の記録を見て、再開の疑いを記録し、今回の Stop を覚える（`note_last_stop`）。
    4. このターンの提案の操作（`stopops`）から最終の版と未送信の書き込みを集め、
       着手していない親のものを外す（`_unstarted_ops`）。
    5. 候補かどうかを `stopreview.candidate` で決める。基準が読めなければ候補にしない。
       候補外で、理由が `_NOT_CANDIDATE_FINAL` に無いものは、何も書かずに抜ける。
    6. 対象・提案・材料の版・参照・提出済みを計画にまとめ、`stopreview.write_plan` に書く。
       書けなければ 1 行の知らせにして、Stop は止めない。
    """
    if not (conf.state and payload.session_id and conf.stop_review_agent):
        return
    stopreview.configure_log(conf.state, stopreview.logs_dir(conf.log))
    key = stopreview.shared_key(payload.session_id, payload.transcript_path)
    if not key:
        # 共有キーを導けない Stop は、未検査（キーなし）として数える。
        # `stop-review report` の「共有キーを導けなかった Stop」の件数になる。
        stopreview.log_event(conf.state, "no_key", {"reason": "hook"})
        return
    effective = stopreview.setting(mode, conf.stop_review)
    now = time.time()
    stopreview.configure_log(conf.state, stopreview.logs_dir(conf.log))
    previous = stopreview.last_stop(conf.state, payload.session_id)
    if (
        payload.stop_hook_active
        and continuation is None
        and previous is not None
        and not previous.get("nudged")
        and not previous.get("sent_back")
    ):
        stopreview.log_suspected(conf.state, payload.session_id, key)
    stopreview.note_last_stop(conf.state, payload.session_id, key, nudged, False, now)
    criteria, _source = stopreview.load_criteria(root)
    stopops.rebind(conf.state, payload.session_id, now)
    since = stopops.turn_start(conf.state, payload.session_id)
    finals = stopops.final_versions(stopops.turn_ops(conf.state, payload.session_id, since))
    turn = stopops.turn_ops(conf.state, payload.session_id, since)
    ops = _unstarted_ops(conf, root, raw, list(finals.values()))
    unsent = _unstarted_ops(
        conf,
        root,
        raw,
        stopops.unsent_writes(stopops.turn_writes(conf.state, payload.session_id, since), turn),
    )
    proposals = [
        {
            "key": stopops.target_key(op),
            "ident": op["ident"],
            "route": op["route"],
            "commit": op.get("commit", ""),
            "files": op.get("files", []),
        }
        for op in ops
    ]
    # 同じ識別子の propose の操作があれば（止めた・失敗した理由が分かる）、未送信としては重ねない。
    operated = {op["ident"].casefold() for op in turn}
    unsent = [row for row in unsent if row["ident"].casefold() not in operated]
    proposals += [
        {
            "key": stopops.unsent_key(row["ident"]),
            "ident": row["ident"],
            "route": stopops.UNSENT,
            "commit": "",
            "files": [{"path": row["path"]}],
        }
        for row in unsent
    ]
    ok, why = stopreview.candidate(
        main_agent=not payload.agent_id,
        stop_hook_active=payload.stop_hook_active,
        ai_continuation=continuation is not None,
        already_rejudged=False,
        effective=effective,
        tickets_enabled=conf.tickets_enabled,
        registered=True,
        existing_nudge=nudged,
        target_unresolvable=False,
        proposal_activity=bool(proposals),
        approved_in_progress=_approved_in_progress(conf, root, payload.cwd, raw),
    )
    if ok and not criteria:
        ok, why = False, "criteria_unreadable"
    if not ok and why not in _NOT_CANDIDATE_FINAL:
        return
    plan = {
        "is_candidate": ok,
        "reason": why,
        "final": _NOT_CANDIDATE_FINAL.get(why, ""),
        "targets": [stopreview.STOP_TARGET, *(p["key"] for p in proposals)],
        "proposals": proposals,
        "no_r2": [p["key"] for p in proposals if p["route"] not in (stopops.SENT, stopops.UNSENT)],
        "material_version": stopreview.material_version(criteria),
        "refs": stopops.session_refs(conf.state, payload.session_id),
        "submitted": _submitted(conf, root, raw, payload.session_id) if ok else [],
        "tainted": stopreview.tainted_flag(conf.state, payload.session_id),
        "effective": effective,
        "rejudge": continuation is not None,
        "original": continuation["attempt_id"] if continuation else "",
        "existing_nudge": nudged,
    }
    try:
        stopreview.expire_overdue(conf.state)
        stopreview.write_plan(conf.state, key, plan)
    except OSError as exc:
        stderr.write(f"ccnavi: Stop の AI 判定の計画を書けない: {exc}\n")


def stop_rules_nudge(
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    payload: hookio.Input,
    record: audit.Record,
) -> str:
    """`match: Stop` の `allow` のルールが、このターンの終わりで止めて渡す文。

    書いたルールが無ければ空で、これまでどおり止めない。何を言うか・何回に 1 度かは
    設定が持ち、ここは適用して数えるだけ（レビューの勧告と同じ分け方）。`every: 10` と書けば
    「ターンの終わり 10 回に 1 度」止める。

    ルールは共通レイヤーとワークスペース自身のレイヤーからだけ引く（`ruleload.stop_rules`）。プロジェクトのレイヤーは
    外のリポジトリで、そこに書かれた 1 行が cwd に依らずメインのターンの終わりを止められて
    しまうため。本文のファイルもワークスペースルートの版だけを読む（ワークツリーやプロジェクトの
    版はエージェントが書き換えられる）。

    止めない回:

    - `stop_hook_active` が真（Stop の hook が続けさせた連鎖の 2 回目以降）。数えもしない
    - サブエージェント（`agent_id` がある）。候補は報告につけてメインに返す決まり
    - 数えを覚えられない（`--state ""`、記録を読めない・書けない）。覚えられないまま止めると
      ターンの終わりのたびに止まるので、何も言わない側を採る（`finish` の促しと同じ）

    数えは `ctxfile.stop_path` に置き、compact・再開・clear では破棄しない。渡す文の頭には
    `STOP_PREFACE` を必ず付ける。止めた回がタスクの続きと読まれず、ユーザへの問いで終わった
    ターンの問いが消えないように。記録の `rules` には、実際に渡したルールだけを残す。
    """
    if payload.stop_hook_active or payload.agent_id or not conf.state:
        return ""
    group = ruleload.stop_rules(conf, root)
    if not group:
        return ""
    counts = ctxfile.stop_path(conf.state, payload.session_id)
    parts: list[str] = []
    delivered: list[str] = []
    for rule in group:
        text = ctxfile.for_rules(
            stderr, conf.state, payload, [rule], [root], unsure_speaks=False, counts_path=counts
        )
        if text:
            parts.append(text)
            delivered.append(rules.record_name(rule))
    if not parts:
        return ""
    _over_report(record)
    record.decision, record.code = audit.NUDGE, CODE_RULE_NUDGE
    record.enforced = True
    record.rules = delivered
    return f"{CODE_RULE_NUDGE}: {STOP_PREFACE}\n\n" + "\n\n".join(parts)


def _over_report(record: audit.Record) -> None:
    """促しで止める回の記録から、同じ Stop の報告（`post.at_stop`）を detail へ移す。

    1 回の Stop の記録は 1 行で、`decision` と `code` は 1 つしか持てない。止めたのは促しなので
    促しが勝つ（`nudge` と `NUDGE_*`）。上書きするだけだと、同じ Stop で保護領域の変更を
    報告したこと（`deny` と `POST_*`）が記録から消える。`rules` に報告のルールを残したまま
    促しのルールを足すと、どれで止めたかが読めなくなる（`rules` は実際に渡したルールだけ）。
    そこで報告の code と rules を `report <code> <id,…>` の形で detail に足し、
    `rules` を空にしてから促しの側が書く。`paths` は報告の変わったパスのまま残る。
    """
    if record.decision != audit.DENY or not record.code:
        return
    note = f"report {record.code} {','.join(record.rules)}".rstrip()
    record.detail = f"{record.detail}; {note}" if record.detail else note
    record.rules = []


def _finish_nudge(
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    payload: hookio.Input,
    record: audit.Record,
    raw: approval.Raw | None = None,
) -> str:
    """`finish` の打ち忘れを促す文。促さないなら空文字。

    メインエージェントの Stop でだけ呼ぶ（SubagentStop は別の手順）。促すのは、同じセッションで
    同じチケットを同じ HEAD のまま促したことが無いときだけ（記録は state の置き場の
    `nudged-<セッション>.json`）。コミットを足して HEAD が進めば、また促してよい。加えて
    `stop_hook_active` が真なら（Stop の hook が続けさせた結果なら。ほかの hook が止めた分も含む）
    何もしない。state の置き場が無い（`--state ""`）か、記録を書けないときは促さない。
    覚えられないまま止めると、ターンの終わりのたびに止まるので、何も言わない側を採る。
    チケット制御が disable なら何もしない。
    このセッションに実行中のサブエージェントが居る間（`subagent.running`。記録を読めないときも）は
    促さず、「1 回だけ」の記録も進めない。

    `raw` は呼び手が同じ hook の中で `approval.read_raw` で読んだ置き場（`decide_at_stop`）。
    """
    if not conf.tickets_enabled or payload.stop_hook_active or payload.agent_id or not conf.state:
        return ""
    # 実行中のサブエージェントが居る間は、委任して待つ回なので促さない（`_request_nudge` と同じ）。
    # ここで返すので「1 回だけ」の記録は進まず、終わったあとの Stop でまた促せる。防ぐのは誤った
    # 促しだけで、メインが自分から早く finish を打つことは防がない。
    if subagent.running(conf.state, payload.session_id):
        return ""
    found = ops_stop.unfinished_at_stop(root, conf, payload.cwd, raw)
    if found is None or ops_stop.nudged_before(conf.state, payload.session_id, found):
        return ""
    failed = ops_stop.remember_nudge(conf.state, payload.session_id, found)
    if failed:
        stderr.write(f"ccnavi: finish を促した記録を残せないので、促さない: {failed}\n")
        return ""
    _over_report(record)
    record.decision, record.code = audit.NUDGE, ops_stop.CODE_FINISH_NUDGE
    record.enforced = True
    record.tree = found.ticket.ticket
    return ops_stop.finish_nudge(root, found)


def _request_nudge(
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    payload: hookio.Input,
    record: audit.Record,
    raw: approval.Raw | None = None,
) -> str:
    """レビューの依頼までの道の途中で終えようとしたとき、次の一手を促す文。促さないなら空文字。

    最後の発言は読まない。「進めてよいですか」で終わった回も、そうでない回も、状態だけで決める。
    文は `STOP_PREFACE` と同じ考え方で、決まった道の途中でユーザの許可は要らないこと、
    本当にユーザの判断が要る問いならその問いを最後に書き直して終えることを言う。

    止めない回と止め方は `_finish_nudge` と同じ（`stop_hook_active`・サブエージェント・
    state の置き場・チケット制御を見る。`dry-run` でも止める）。
    加えて、このセッションに実行中のサブエージェントが居る間（`subagent.running`。記録を読めない
    ときも）は止めず、「1 回だけ」の記録も進めない。終わったあとの Stop でまた促せる。
    同じ状態（親・フェーズ・残っている子・HEAD）では 1 回だけで、記録は
    `nudged-<セッション>.json`。進めばまた止める。判定は `ops_stop_request.pending_at_stop`。
    """
    if not conf.tickets_enabled or payload.stop_hook_active or payload.agent_id or not conf.state:
        return ""
    # 実行中のサブエージェントが居る間は、委任して待つ回なので止めない。記録を読めないときも
    # 止めない。ここで返すので「1 回だけ」の記録は進まず、終わったあとの Stop でまた促せる。
    if subagent.running(conf.state, payload.session_id):
        return ""
    found = ops_stop_request.pending_at_stop(root, conf, payload.cwd, raw)
    if found is None or ops_stop_request.nudged_before(conf.state, payload.session_id, found):
        return ""
    failed = ops_stop_request.remember_nudge(conf.state, payload.session_id, found)
    if failed:
        stderr.write(f"ccnavi: 依頼までの道を促した記録を残せないので、促さない: {failed}\n")
        return ""
    record.decision, record.code = audit.NUDGE, ops_stop_request.CODE_REQUEST_NUDGE
    record.enforced = True
    record.tree = found.parent.ticket
    return ops_stop_request.request_nudge(root, found)


def _feedback_nudge(
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    payload: hookio.Input,
    record: audit.Record,
    raw: approval.Raw | None = None,
) -> str:
    """フィードバック計画待ちのまま終えようとしたとき、次の一手を促す文。促さないなら空文字。

    止めない回と止め方は `_request_nudge` と同じ（`stop_hook_active`・サブエージェント・
    state の置き場・実行中のサブエージェントを見る。`dry-run` でも止める）。同じ状態（親・HEAD・
    改版を提案済みか）では 1 回だけで、記録は `nudged-<セッション>.json` の `feedback:` で
    始まるキー。判定は `ops_stop_feedback.waiting_at_stop`。
    """
    if not conf.tickets_enabled or payload.stop_hook_active or payload.agent_id or not conf.state:
        return ""
    if subagent.running(conf.state, payload.session_id):
        return ""
    found = ops_stop_feedback.waiting_at_stop(root, conf, payload.cwd, raw)
    if found is None or ops_stop_feedback.nudged_before(conf.state, payload.session_id, found):
        return ""
    failed = ops_stop_feedback.remember_nudge(conf.state, payload.session_id, found)
    if failed:
        stderr.write(f"ccnavi: フィードバック計画を促した記録を残せないので、促さない: {failed}\n")
        return ""
    record.decision, record.code = audit.NUDGE, ops_stop_feedback.CODE_FEEDBACK_NUDGE
    record.enforced = True
    record.tree = found.parent.ticket
    return ops_stop_feedback.feedback_nudge(root, found)


def _subagent_nudge(conf: settings.Settings, payload: hookio.Input, record: audit.Record) -> str:
    """終わったサブエージェントが範囲外の変更を残した・範囲を確かめられなかったとき、Stop で促す文。

    促すのは `outside` と `unchecked` だけ（`unattributed` と正常な終了では促さない）。同じ結果では
    1 回だけ。実行中のサブエージェントが居る間と、サブエージェントの中では止めない。
    """
    if not conf.tickets_enabled or payload.stop_hook_active or payload.agent_id or not conf.state:
        return ""
    if subagent.running(conf.state, payload.session_id):
        return ""
    text = subagent.stop_nudge(conf.state, payload.session_id)
    if text:
        record.decision, record.code = audit.NUDGE, "NUDGE_SUBAGENT_OUTCOME"
        record.enforced = True
    return text


def _integrate_nudge(
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    payload: hookio.Input,
    record: audit.Record,
    deadline: float | None = None,
) -> tuple[str, str]:
    """直接作業が `integrate` まで進んでいないとき、打ち方を促す文と、促せない理由。

    返すのは (止める文, 報告に足す理由)。どちらも空なら何もしない。hook 自身は `integrate`
    を打たない。判定は `ops_stop_integrate.direct_at_stop`。理由は統合の前提（統合先のツリーが
    綺麗なこと）を満たせないときで、止めずに報告へ足す。

    止めない回と止め方は `_request_nudge` と同じ（`stop_hook_active`・サブエージェント・
    state の置き場を見て、モードは見ない。実行中のサブエージェントが居る間も止めず、
    「1 回だけ」の記録も進めない）。チケット制御の有無は見ない（直接作業はチケットに結び付かない）。
    同じ状態では促しも理由も 1 回だけで、記録は `nudged-<セッション>.json` の `integrate:` で
    始まるキー。`deadline`（`time.monotonic` の絶対時刻）を過ぎてから git を起こさない。
    """
    if payload.stop_hook_active or payload.agent_id or not conf.state:
        return "", ""
    if subagent.running(conf.state, payload.session_id):
        return "", ""
    found = ops_stop_integrate.direct_at_stop(root, conf, payload.cwd, payload.session_id, deadline)
    if found is None or ops_stop_integrate.nudged_before(conf.state, payload.session_id, found):
        return "", ""
    failed = ops_stop_integrate.remember_nudge(conf.state, payload.session_id, found)
    if failed:
        stderr.write(f"ccnavi: integrate を促した記録を残せないので、促さない: {failed}\n")
        return "", ""
    if found.blocked:
        return "", ops_stop_integrate.blocked_notice(found)
    _over_report(record)
    record.decision, record.code = audit.NUDGE, ops_stop_integrate.CODE_INTEGRATE_NUDGE
    record.enforced = True
    record.tree = found.name
    return ops_stop_integrate.integrate_nudge(root, found), ""


def decide_at_start(
    stdout: TextIO,
    stderr: TextIO,
    mode: str,
    conf: settings.Settings,
    root: str,
    payload: hookio.Input,
    record: audit.Record,
    deadline: float | None = None,
) -> int:
    """セッションが始まったとき。大きい対象のバックアップをここで 1 度だけ取る。

    deadline は hook の判定の期限（`judge.DEADLINE_SECONDS`）。md の索引を新しくするのは
    その残りまでに収める（hook の timeout を超えない）。

    ここで取るのは実行ファイルで、ツール呼び出しのたびにコピーするには大きすぎる。
    このイベントは 1 セッションに 1 回しか来ないので、重い仕事を置く先になる。

    判定は返さない。何も起きていない時点なので、言うことは 3 つだけ。バックアップを
    取れなかったこと（あれば）と、チケット制御が有効なときの作業の進め方と、ccnavi が
    前提にしている作業の決まり。
    後の 2 つは起動・再開・compact・clear のどの回にも出す。文脈が新しくなるたびに
    改めて届かないと、compact のあとのモデルは進め方を知らないまま続ける。
    サブエージェントには出さない（SubagentStart は別の手順で、チケットを起こす
    立場にない）。
    """
    setting = modes.effective_setting(mode, conf.guard_core_files)
    outcomes = selfguard.at_start(
        setting,
        conf.state,
        payload.session_id,
        root,
        selfguard_targets.targets(
            root, conf.rules, conf.bin, ruleload.layer_files(conf, root), conf.projects
        ),
    )
    # 渡した回の数えはここで破棄する。このイベントは起動だけでなく再開と compact の後にも
    # 来るので、モデルの文脈が新しくなるたびに「1 度だけ渡す文」は改めて届き、`every` の
    # 刻みも 0 から数え直しになる。
    ctxfile.forget(conf.state, payload.session_id, startup=payload.source == "startup")
    # 実行中のサブエージェントの印は、起動（startup）と再開（resume）で破棄する。どちらも新しい
    # プロセスで、前のプロセスのサブエージェントは生きていない。compact と clear は同じ
    # プロセスなので、実行中の体が残りうる。Stop の数え（上）は起動だけで破棄する。
    if payload.source in ("startup", "resume"):
        subagent.forget_running(conf.state, payload.session_id)
        busy.clear(conf.state, payload.session_id)
        turn.clear(conf.state, payload.session_id)
    if payload.source == "startup":
        _reported_baseline(stderr, conf, root, payload, record)
    # 基準を取った時の注記（読めなかったツリーなど）を消さないよう、足す。
    pruned = _prune_at_start(stderr, conf, root, payload.session_id)
    record.detail = "; ".join(part for part in (record.detail, pruned) if part)
    record.decision, record.enforced = audit.ALLOW, True
    texts = []
    if outcomes:
        record.guarded = [f"{o.target.key}:{o.action}" for o in outcomes]
        texts.append(selfguard.report(outcomes))
    # 承認済みチケットの要求のうち、上位（ルールの deny・親の範囲・フェーズ定義の上限）に
    # 却下されて効かないもの（REQ-SES-02）。モデルにもユーザにも出す。サブエージェントには
    # 出さない。索引（docsearch）より先に組むのは、こちらが短い予算で済み、索引に期限の
    # 残りを渡すため。
    rejects = ""
    if conf.tickets_enabled and not payload.agent_id:
        rejects = session_rejects.notice(conf, root, payload.cwd, deadline)
    # 終わっていない integrate の記録（別のセッションの取り込みの途中かもしれない）。モデルにも
    # ユーザにも出す。チケット制御は見ない。サブエージェントには出さない。期限は hook の期限と
    # 開始から 2 秒の小さいほうで、索引（docsearch）より先に組んで残りを渡す。
    pending = ""
    if not payload.agent_id:
        limit = time.monotonic() + PENDING_SECONDS
        pending = pending_integrate.pending_notice(
            root, conf, limit if deadline is None else min(deadline, limit)
        )
    # md の frontmatter の索引を差分で新しくし、引き方を案内する（`ccnavi --docs`）。
    # サブエージェントには出さない。失敗しても何も出さない
    # （docsearch.at_start が例外を外に出さない）。先に組むのは、作業の決まりの
    # 「詳しくは --docs で引く」を、索引の案内を出す回にだけ添えるため。
    docs = "" if payload.agent_id else docsearch.at_start(conf, root, deadline)
    # ccnavi が前提にしている作業の決まり（考え方・ワークツリー・git の入口・下書きの置き場・
    # チケットの進め方など）。文面は .ccnavi/CCNAVI.md にあり、ここは条件と差し込みを解いて渡す。
    # チケット制御の有無によらず出す。サブエージェントには出さない。
    if not payload.agent_id:
        guide = ccnavimd.session_text(
            conf,
            root,
            docs.startswith(docsearch.GUIDE_HEAD),
            ruleload.deny_ids(conf, root),
            mode,
        )
        if guide:
            texts.append(guide)
        # このPCで作業中の親を 1 行ずつ（親・ブランチ・MR・次の子・未送信）。
        # 読めなくても開始は止めない。
        parents = _parents_at_start(stderr, conf, root)
        if parents:
            texts.append(parents)
    if rejects:
        texts.append(rejects)
    if pending:
        texts.append(pending)
    # 学びの蓄積の提案の候補が新しく現れたら、ユーザにだけ知らせる（モデルの文脈には入れない）。
    learning = "" if payload.agent_id else learning_notice.notice(conf, root, deadline)
    # cwd がプロジェクトの中なら、そのプロジェクトのスキルの目録。
    skills = projskills.notice(stderr, conf, root, payload, at_start=True)
    if skills:
        texts.append(skills)
    if docs:
        texts.append(docs)
    if texts or learning:
        hookio.write_context(
            stdout,
            hookio.SESSION_START,
            "\n\n".join(texts),
            system="\n\n".join(p for p in (rejects, pending, learning) if p),
        )
    return EXIT_OK


def _parents_at_start(stderr: TextIO, conf: settings.Settings, root: str) -> str:
    """セッション開始で渡す、このPCで作業中の親の一覧。無い・読めないなら空。"""
    try:
        return overview.session_text(conf, root)
    except Exception as exc:  # noqa: BLE001 - 一覧の失敗でセッションの開始を止めない
        stderr.write(f"ccnavi: 作業中の親の一覧を作れなかった: {exc}\n")
        return ""


def _reported_baseline(
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    payload: hookio.Input,
    record: audit.Record,
) -> None:
    """起動時に在る保護領域の変更を、実行後チェックの基準として記録する。

    全部のツリーを見る（`watched_for` の payload 無し）。実行後チェックと同じ式で数えるので、
    あとで同じ変更が見つかっても、新しい変更には数えない。
    """
    if not conf.state:
        return
    raw = approval.read_raw(conf, root) if conf.tickets_enabled else None
    post.capture_baseline(
        stderr,
        conf.state,
        payload.session_id,
        watched_for(stderr, conf, root, record, None),
        scope_guard(conf, root, raw),
        record,
        mine=(conf.state, conf.log),
        places=(conf.tickets, conf.approved),
        root=root,
    )


def _prune_at_start(stderr: TextIO, conf: settings.Settings, root: str, session: str) -> str:
    """記録のローテートと、古い記録・終わったセッションの記録の削除（prune）。

    ここに置くのは、セッションに 1 度しか来ない場所だから。実行前チェックに置くと、呼び出しの
    たびに置き場を数えることになる。何が起きても開始は止めない。失敗は標準エラーに出し、
    動かしたものの数は記録の `detail` に残す（消したことも記録に残る）。
    """
    try:
        report = prune.run(root, conf.log, conf.state, session)
    except Exception as exc:  # noqa: BLE001 - 後始末の失敗でセッションの開始を止めない
        stderr.write(f"ccnavi: 記録と state の後始末に失敗した: {exc}\n")
        return ""
    for problem in report.problems:
        stderr.write(f"ccnavi: {problem}\n")
    return prune.summary(report)


def _written(payload: hookio.Input, record: audit.Record) -> str:
    """この呼び出しが名指しのツールで書いた先の、解決済みのパス。書かないツールなら空。"""
    if payload.tool_name not in selfguard.REPAIR_TOOLS:
        return ""
    return fsio.full_path(record.subject, payload.cwd)


def decide_after_failure(
    stdout: TextIO,
    stderr: TextIO,
    mode: str,
    conf: settings.Settings,
    root: str,
    payload: hookio.Input,
    record: audit.Record,
) -> int:
    """ツール自体が失敗した。ワークツリーを見て戻し、失敗の連続（failstreak）に数える。

    失敗した呼び出しも、失敗する前に設定ファイルや保護領域を変えていることがある
    （`rm …; false`、途中で落ちたビルドなど）。ここで実行後チェックを通さないと、その変更は
    次の呼び出しの前までそのまま残り、次のユーザの発言で「ユーザの手による変更」として
    受け入れられる（`decide_at_prompt`）。成功した呼び出しと同じ照合をここでも行う。中断
    （is_interrupt）も、中断までに書かれたぶんは残るので同じ。

    止められるイベントではないので、返せるのは文だけ。照合の結果は `additionalContext` で渡す。
    連続は閾値に達した回に資料を引く案内を返す。中断はユーザが止めたもので、ツールの誤りでは
    ないので数えない。数えの失敗や案内を組む失敗で、ツールの結果は変わらない。
    """
    record.decision, record.reason = audit.SKIP, audit.REASON_EVENT_NOT_CHECKED
    if payload.is_interrupt and not payload.agent_id:
        # ユーザの中断。Stop は来ないことがあるので、ここでターンの終わりにする。
        # 下の照合は中断までに書かれたぶんを戻すので、印を先に消してよい（消す前でも結果は同じ）。
        turn.end(conf.state, payload.session_id)
    after = functools.partial(selfguard.after, written=_written(payload, record))
    texts = [judge.guard_setting_files(mode, conf, root, payload, record, after)]
    raw = approval.read_raw(conf, root) if conf.tickets_enabled else None
    text, _ = post.check(
        stderr,
        enforcing=mode == modes.ENABLE,
        restore=modes.effective_setting(mode, conf.restore_if_deny),
        state_dir=conf.state,
        mine=(conf.state, conf.log),
        watched=watched_for(stderr, conf, root, record, payload),
        scope=scope_guard(conf, root, raw),
        payload=payload,
        record=record,
        places=(conf.tickets, conf.approved),
        root=root,
    )
    texts.append(text)
    if not payload.is_interrupt:
        n = failstreak.threshold(conf.fail_streak_count)
        try:
            got = failstreak.fail(conf.state, payload.session_id, payload.agent_id, n)
            if got:
                texts.append(docsearch.fail_streak_note(conf, root, got))
        except Exception:  # noqa: BLE001  案内の失敗でツールの結果を変えない
            pass
    said = "\n\n".join(p for p in texts if p)
    if said:
        hookio.write_context(stdout, hookio.POST_TOOL_USE_FAILURE, said)
    return EXIT_OK


def decide_after(
    stdout: TextIO,
    stderr: TextIO,
    mode: str,
    conf: settings.Settings,
    root: str,
    payload: hookio.Input,
    record: audit.Record,
) -> int:
    """実行後チェックを 1 回動かし、言うことがあれば返す。そのあと、直接作業の候補を登録する。

    候補の登録（`direct_candidates.touch`）は git を呼ばず、失敗しても何も言わない。
    実行後チェックが差し戻して終わる回でも登録する（書き込みは起きている）。
    """
    try:
        return _decide_after(stdout, stderr, mode, conf, root, payload, record)
    finally:
        with contextlib.suppress(Exception):  # 登録の失敗でツールの結果を変えない
            direct_candidates.touch(conf, root, payload)


def _decide_after(
    stdout: TextIO,
    stderr: TextIO,
    mode: str,
    conf: settings.Settings,
    root: str,
    payload: hookio.Input,
    record: audit.Record,
) -> int:
    """実行後チェックを 1 回動かし、言うことがあれば返す。

    期限を渡していない。実行前の期限は、遅い判定が気づかないうちに許可と同じ扱いになるのを
    防ぐためのもので、止められるイベントでしか意味を持たない。ここは
    何も止めていないので、遅れは待ち時間にしかならない。ワークツリーを読む側は
    自前の短い時間を持っていて、そこに達したら何も言わずに終わる。

    処理フロー:
    1. 失敗の連続を 0 に戻す（`failstreak.reset`）。
    2. 設定ファイルの保護を、ルールを読む前に戻す
       （`judge.guard_setting_files`・`selfguard.after`）。
    3. 見るワークツリーを決め（`watched_for`）、
    実行後チェック（`post.check`）で報告と差し戻しを得る。
    4. 差し戻しの有無によらず、フェーズの知らせ（`phase.announce`）とサブエージェントの知らせ
       （`subagent.ignored_bounce`・`subagent.unchecked_scope`・`subagent.outcome_notices`）を足す。
    5. 差し戻しの対象は、直前の実行が汚したと言える DENY だけ。ENABLE なら標準エラーに書いて
       `exit 2` で返す。dry-run では差し戻さず、標準出力の文脈に dry-run の文を添えて返す。
    6. 最後に、告げた知らせを確定する（`subagent.confirm_outcomes`）。
    """
    # 成功が入ったので、失敗の連続（failstreak）を 0 に戻す。
    failstreak.reset(
        conf.state,
        payload.session_id,
        payload.agent_id,
        failstreak.threshold(conf.fail_streak_count),
    )
    # 設定ファイルを先に戻す。ルールを読むより前でなければならない。あとから
    # 戻すと、この呼び出しが書き換えたルールファイルをそのまま読んで保護領域を
    # 決めることになり、`deny` を空にされた版で「守るものは無い」と判断する。
    # 保護の根拠を、この呼び出しが触れる前の状態に返してから読む。
    # 書いた先を渡すのは、組み込みの既定を使っている間の修復を戻さないため
    # （selfguard._left_as_repair）。
    restore = functools.partial(selfguard.after, written=_written(payload, record))
    guard = judge.guard_setting_files(mode, conf, root, payload, record, restore)

    watched = watched_for(stderr, conf, root, record, payload)
    if len(watched) > 1:
        record.tree, record.project = watched[-1].tree.name, watched[-1].tree.project
    # 既定に戻ったことをこのイベントでは言わない。実行前チェックが呼び出しごとに
    # 言っているので、同じターンで 2 度届く。届く数が増えると、どちらも
    # 読まれなくなる。記録には fallback が残る。
    # 範囲は実行前チェックと同じ経路で解く。状態は置き場そのもので、コピーする段は無い。
    # 置き場はここで 1 度だけ読み、下のフェーズの知らせ（`phase.announce`）にも渡す。
    restore_setting = modes.effective_setting(mode, conf.restore_if_deny)
    raw = approval.read_raw(conf, root) if conf.tickets_enabled else None
    scope = scope_guard(conf, root, raw)

    text, restored = post.check(
        stderr,
        enforcing=mode == modes.ENABLE,
        restore=restore_setting,
        state_dir=conf.state,
        mine=(conf.state, conf.log),
        watched=watched,
        scope=scope,
        payload=payload,
        record=record,
        # チケットの置き場。そこに現れた変更のうち、ccnavi のサブコマンドが書いたと内容から
        # 読めるものを外す。チケット制御を切ったワークスペースでも渡すのは、
        # `_committed_findings` が置き場を外すのと同じ理由。判定の有無で、外れたり
        # 外れなかったりさせない。
        places=(conf.tickets, conf.approved),
        # 退避（`ready`）が消した閉じたチケットを見分けるため、`logs/archive/` を読む。
        root=root,
    )
    # 設定ファイルについて言うことは、実行後チェックの報告より前に置く。
    # ガード自身が触られた回は、他の何よりそれが先に読まれてほしい。
    if guard:
        text = f"{guard}\n\n{text}" if text else guard
    # フェーズが終わったばかりなら、ここで 1 度だけ言う。止まるのは次の呼び出しから。
    bounced = ""
    told: list = []
    if conf.tickets_enabled:
        # `post.check` がワークツリーを戻そうとした回は、置き場のファイルも戻っていることが
        # あるので読み直す。それ以外の回は、上で読んだ置き場をそのまま使う。
        parent, raw = phase.parent_at(root, conf, payload.cwd, None if restored else raw)
        said = phase.announce(stderr, root, conf, parent, raw) if parent is not None else ""
        if said:
            text = f"{text}\n\n{said}" if text else said
        # サブエージェントが差し戻しを無視して終わったなら、親にそれを言う。
        # 差し戻しは 1 回きりなので、2 度目の終了は何も言われずに通っている。
        bounced = subagent.ignored_bounce(conf.state, payload)
        # 範囲を確かめられないまま終わったことも、同じ経路で親に言う。
        unchecked = subagent.unchecked_scope(conf.state, payload)
        outcomes, told = subagent.outcome_notices(conf.state, payload)
        bounced = "\n\n".join(p for p in (bounced, unchecked, outcomes) if p)
        if bounced:
            text = f"{text}\n\n{bounced}" if text else bounced
    # frontmatter の無い md を新しく作ったなら、書いた時点で 1 度だけ書き方を案内する。
    # 判定には加えない（差し戻しの有無は上の実行後チェックだけで決まる）。
    hint = docsearch.missing_frontmatter(conf, root, payload)
    # 親の提案を手で書いたときの書式の誤りも、書いた時点で 1 度だけ案内する。
    hint = "\n\n".join(p for p in (hint, proposalhint.hint(conf, root, payload)) if p)
    if not text and not hint:
        return EXIT_OK

    # 差し戻すのは、直前の実行が汚したと言える分があるときだけ。セッションが
    # 始まる前から在った変更は言うが差し戻さない。差し戻しは「あなたが直せ」で、
    # 誰が書いたか分からないものにそれを言うと、他人の書きかけを消しにいく。
    pushback = record.decision == audit.DENY

    if pushback and mode == modes.ENABLE:
        # このイベントで差し戻す経路は exit 2 と標準エラーだけ。ツールは
        # すでに走っているので取り消せず、渡せるのは「次に何をするか」になる。
        stderr.write("\n\n".join(p for p in (text, hint) if p) + "\n")
        subagent.confirm_outcomes(conf.state, told)
        return EXIT_BLOCK

    if pushback:
        # warn は差し戻さない。呼び出しを止めないことがこのモードの約束で、
        # 差し戻しは止めはしないが次の一手を変えさせる。変えさせないまま
        # 数えるためのモードなので、届け先を報告の側にする。
        text = (
            f"[ccnavi dry-run] {modes.ENABLE} would have sent this back as a correction:\n" + text
        )
    text = "\n\n".join(p for p in (text, hint) if p)
    # 起動したのがサブエージェント（入れ子）なら、差し戻しを無視した知らせはその子にしか
    # 届かない。ユーザにも見えるよう `systemMessage` に同じ文を載せる。
    # 上の exit 2 の経路では標準出力の JSON が読まれないので、載せられない。
    system = bounced if payload.agent_id else ""
    hookio.write_context(stdout, hookio.POST_TOOL_USE, text, system=system)
    subagent.confirm_outcomes(conf.state, told)
    return EXIT_OK
