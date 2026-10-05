"""hook のイベントごとの手順。1 回の起動で何が起きるかは、ここを上から読めば分かる。

実行前チェックは judge、サブエージェントの始まりと終わりは subagent、実行後
チェックは post に本体があり、ここはイベント名からそれらへ振り分け、記録と応答を
繋ぐだけ。イベントが増えるたびに 1 本の関数が長くならないように、イベント 1 つに
つき関数 1 つ。
"""

from __future__ import annotations

import functools
import io
from typing import TextIO

from ..infra import fsio, hookio, modes, settings, tree
from ..infra.modes import EXIT_BLOCK, EXIT_OK
from ..policy import builtin, ctxfile, ruleload, rules, selfguard, selfguard_targets
from ..records import audit, prune, repeat
from ..tickets import approval, approval_checks, branchfind, configsync, ops, phase
from . import docsearch, judge, post, post_findings, projskills, reasons, subagent

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
    """イベントごとの処理に振り分ける。

    振り分けだけをここに置く。イベントが増えるたびに 1 本の関数が長くなると、
    どのイベントで何が起きるのかを読むのに全部を読むことになる。
    """
    if mode == modes.DISABLE:
        record.decision, record.reason = audit.SKIP, audit.REASON_MODE_DISABLED
        return EXIT_OK
    if payload.event == hookio.PRE_TOOL_USE:
        return judge.decide_before(stdout, stderr, mode, conf, root, payload, record, deadline)
    if payload.event == hookio.POST_TOOL_USE:
        return decide_after(stdout, stderr, mode, conf, root, payload, record)
    if payload.event == hookio.SESSION_START:
        return decide_at_start(stdout, stderr, mode, conf, root, payload, record, deadline)
    if payload.event == hookio.USER_PROMPT_SUBMIT:
        return decide_at_prompt(stdout, stderr, conf, root, payload, record)
    if payload.event == hookio.STOP:
        return decide_at_stop(stdout, stderr, mode, conf, root, payload, record)
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
    """ターンの区切りで作業ツリーを見る 2 つが、共通して使う持ち物。

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
    """実行後に見るツリーと、それぞれに当てるルール（設計 11.7）。

    payload が無ければ全部のツリー（ターンの区切り）。あればワークスペースルートと、
    この呼び出しが触ったツリー（パスを持つツールは行き先、Bash は cwd）。
    ルールの引き方は実行前チェックと同じで、共通レイヤーにそのツリーのレイヤーを足した和。
    別に書くと、実行前に通った書き込みがターンの終わりに報告される。
    """
    ws = tree.main_tree(root)
    if payload is None:
        trees = tree.all_trees(root, conf.projects)
    else:
        trees = [ws]
        where = record.subject if payload.tool_name in ruleload.PATH_TOOLS else payload.cwd
        t = tree.tree_of(root, where, conf.projects)
        if t is not None and t.root != ws.root:
            trees.append(t)
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
            loaded[t.project] = (rule_set, source)
        rule_set, source = loaded[t.project]
        out.append(post.Watched(t, rule_set, source))
    return out


def scope_guard(
    conf: settings.Settings, root: str, raw: approval.Raw | None = None
) -> post_findings.ScopeGuard | None:
    """承認済みチケットを、実行後の側から当てる持ち物。チケット制御が disable なら None。

    `raw` は呼び手が `approval.read_raw` で読んだもの。渡せば置き場を読み直さない。
    """
    if not conf.tickets_enabled:
        return None
    copies, _ = approval.scan(conf, root, raw=raw)
    # 種類の上限はレイヤー（計画を持つ親の `project:`）ごとに、ここで 1 度だけ読む。
    types: dict[str, dict] = {}
    for copy in copies:
        if copy.has_plan and copy.project not in types:
            types[copy.project] = phase.load_types(conf, root, copy.project) or {}
    return post_findings.ScopeGuard(
        root=root,
        copies=approval_checks.by_id(copies),
        projects=conf.projects,
        tickets=conf.tickets,
        approved=conf.approved,
        types=types,
    )


def decide_at_prompt(
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    payload: hookio.Input,
    record: audit.Record,
) -> int:
    """ユーザが何か言ったとき。ターンの基準をここで取る。

    何も返さない。このイベントで返した文はモデルのコンテキストに入るので、
    まだ何も起きていない時点で文を 1 つ足すことになる。ここでやるのは、
    ターンの終わりに「このターンで何が変わったか」を言えるようにする記録だけ。

    承認されたことも伝えない。ボードの承認は拡張が承認の文（`agree_screen.approved_text`）を渡し、
    ほかの経路の承認は、エージェントが `ccnavi-ticket.sh status` で聞く。hook が起点を取って
    増えた承認を数える形は、セッションの開始時の取り込みで届いた承認を起点に含めて取りこぼした。

    例外は 1 つ。依頼文に issue・MR の指定（`#152`・`!5` など）があるとき。着手の前に紐づく
    ブランチを探してユーザに確かめる指示を足す。文を足すだけで、作業は止めない。

    承認済みチケットの置き場は、ここで 1 度だけ読んで範囲（`scope_guard`）に渡す。
    """
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
        functools.partial(configsync.is_synced_write, conf, root),
        root,
    )
    hint = branchfind.prompt_context(conf, root, payload.prompt)
    if hint:
        hookio.write_context(stdout, hookio.USER_PROMPT_SUBMIT, hint)
    return EXIT_OK


def decide_at_stop(
    stdout: TextIO,
    stderr: TextIO,
    mode: str,
    conf: settings.Settings,
    root: str,
    payload: hookio.Input,
    record: audit.Record,
) -> int:
    """ターンが終わったとき。宣言した保護領域の今の状態をユーザへ報告する。

    宛先がユーザなので `systemMessage` で返す。呼び出しごとの報告はモデルへ届く
    経路に載せてあり、そこは既に足りている。足りていないのは、ターンが終わった
    あとにユーザが「結局どこが変わったのか」を 1 度で見る場所のほう。

    報告のためには止めない。このイベントで止めることはターンを続けさせる意味になり、
    報告のために作業を終わらせない形になる。報告は言うだけにする。

    報告はモードを見ない。dry-run でも報告する。ここは呼び出しにも作業ツリーにも手を
    出さず、見えたことを言うだけなので、モードが約束しているものを何ひとつ破らない。
    むしろ dry-run は「まだ何も適用していないが何が起きているか」を見るためのモードなので、
    ここが何も言わないと見る手立てが減る。

    止めるのは 1 つだけ。cwd のワークツリーのチケットが、作業を終えたように見えるのに
    `finish` されていないとき、1 回の連鎖に 1 回だけ止めて `finish` か続ける理由を促す
    （`_finish_nudge`）。こちらはモードを見る。enable でだけ止め、dry-run では
    止めたはずの文を報告に載せる。止めるときも報告は同じ応答の `systemMessage` で返す。

    `finish` を促さなかった回に限り、`match: Stop` のルールが渡す回ならそこで止める
    （`stop_rules_nudge`）。止め方とモードの扱いは `finish` の促しと同じ。

    承認済みチケットの置き場は、ここで 1 度だけ読んで範囲（`scope_guard`）と `finish` の促し
    （`ops.unfinished_at_stop`）の両方に渡す。間の `post.at_stop` は報告するだけで作業ツリーを
    戻さず、`repeat.at_stop` は state を読むだけなので、置き場のファイルは動かない。
    """
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
        functools.partial(configsync.is_synced_write, conf, root),
        root,
    )
    # 同じ理由で繰り返し止めた呼び出し（repeat）。拒否の文面はモデルにしか届かないので、
    # 言い換えで回っているかもしれないことをユーザにも 1 度言う。止めはしない。
    repeated = repeat.at_stop(conf.state, payload.session_id, repeat.threshold(conf.deny_repeat))
    if repeated:
        report = f"{report}\n\n{repeated}" if report else repeated
    nudge = _finish_nudge(stderr, conf, root, payload, record, mode, raw)
    if not nudge:
        # finish の促しで止める回は、ルールの促しを数えもしない。1 回の Stop で
        # 止める理由は 1 つだけにし、数えを進めて届かない回を作らない。
        nudge = stop_rules_nudge(stderr, conf, root, payload, record, mode)
    if nudge and mode == modes.ENABLE:
        hookio.write_stop_block(stdout, nudge, system=report)
        return EXIT_OK
    if nudge:
        # dry-run は止めない。止めたはずのことをユーザへの報告に載せる
        # （実行後チェックと同じ言い方）。
        told = f"[ccnavi dry-run] {modes.ENABLE} would have blocked this stop:\n{nudge}"
        report = f"{report}\n\n{told}" if report else told
    if report:
        hookio.write_system_message(stdout, report)
    return EXIT_OK


def stop_rules_nudge(
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    payload: hookio.Input,
    record: audit.Record,
    mode: str,
) -> str:
    """`match: Stop` の `allow` のルールが、このターンの終わりで止めて渡す文。

    書いたルールが無ければ空で、これまでどおり止めない。何を言うか・何回に 1 度かは
    設定が持ち、ここは当てて数えるだけ（レビューの勧告と同じ分け方）。`every: 10` と書けば
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

    数えは `ctxfile.stop_path` に置き、compact・再開・clear では捨てない。渡す文の頭には
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
            delivered.append(rule.id or f"({rules.ALLOW})")
    if not parts:
        return ""
    record.decision, record.code = audit.NUDGE, CODE_RULE_NUDGE
    record.enforced = mode == modes.ENABLE
    record.rules = [*record.rules, *delivered]
    return f"{CODE_RULE_NUDGE}: {STOP_PREFACE}\n\n" + "\n\n".join(parts)


def _finish_nudge(
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    payload: hookio.Input,
    record: audit.Record,
    mode: str,
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

    `raw` は呼び手が同じ hook の中で `approval.read_raw` で読んだ置き場（`decide_at_stop`）。
    """
    if not conf.tickets_enabled or payload.stop_hook_active or payload.agent_id or not conf.state:
        return ""
    found = ops.unfinished_at_stop(root, conf, payload.cwd, raw)
    if found is None or ops.nudged_before(conf.state, payload.session_id, found):
        return ""
    failed = ops.remember_nudge(conf.state, payload.session_id, found)
    if failed:
        stderr.write(f"ccnavi: finish を促した記録を残せないので、促さない: {failed}\n")
        return ""
    record.decision, record.code = audit.NUDGE, ops.CODE_FINISH_NUDGE
    record.enforced = mode == modes.ENABLE
    record.tree = found.ticket.ticket
    return ops.finish_nudge(root, found)


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

    判定は返さない。何も起きていない時点なので、言うことは 2 つだけ。バックアップを
    取れなかったこと（あれば）と、チケット制御が有効なときの作業の進め方。
    後者は起動・再開・compact・clear のどの回にも出す。文脈が新しくなるたびに
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
    # 渡した回の数えはここで捨てる。このイベントは起動だけでなく再開と compact の後にも
    # 来るので、モデルの文脈が新しくなるたびに「1 度だけ渡す文」は改めて届き、`every` の
    # 刻みも 0 から数え直しになる。
    ctxfile.forget(conf.state, payload.session_id, startup=payload.source == "startup")
    record.detail = _prune_at_start(stderr, conf, root, payload.session_id)
    record.decision, record.enforced = audit.ALLOW, True
    texts = []
    if outcomes:
        record.guarded = [f"{o.target.key}:{o.action}" for o in outcomes]
        texts.append(selfguard.report(outcomes))
    if conf.tickets_enabled:
        texts.append(reasons.ways_of_working(conf, root, mode))
    # cwd がプロジェクトの中なら、そのプロジェクトのスキルの目録。
    skills = projskills.notice(stderr, conf, root, payload, at_start=True)
    if skills:
        texts.append(skills)
    # md の frontmatter の索引を差分で新しくし、引き方を案内する（`ccnavi --docs`）。
    # サブエージェントには出さない。壊れても何も出さない
    # （docsearch.at_start が例外を外に出さない）。
    if not payload.agent_id:
        docs = docsearch.at_start(conf, root, deadline)
        if docs:
            texts.append(docs)
    if texts:
        hookio.write_context(stdout, hookio.SESSION_START, "\n\n".join(texts))
    return EXIT_OK


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


def decide_after(
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
    何も止めていないので、遅れは待ち時間にしかならない。作業ツリーを読む側は
    自前の短い時間を持っていて、そこに達したら何も言わずに終わる。
    """
    # 設定ファイルを先に戻す。ルールを読むより前でなければならない。あとから
    # 戻すと、この呼び出しが書き換えたルールファイルをそのまま読んで保護領域を
    # 決めることになり、`deny` を空にされた版で「守るものは無い」と判断する。
    # 保護の根拠を、この呼び出しが触れる前の状態に返してから読む。
    # 書いた先を渡すのは、組み込みの既定を使っている間の修復を戻さないため
    # （selfguard._left_as_repair）。
    # 着手のときに共通レイヤーでプロジェクトのレイヤーを上書きした分は、
    # 内容と上書きの記録で見分けて外す
    # （設計 11.12）。戻す側と、報告する側の両方で同じ答えを使う。
    synced = functools.partial(configsync.is_synced_write, conf, root)
    restore = functools.partial(selfguard.after, written=_written(payload, record), synced=synced)
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
        # チケットの置き場。そこに現れた変更のうち、ccnavi の副命令が書いたと内容から
        # 読めるものを外す。チケット制御を切ったワークスペースでも渡すのは、
        # `_committed_findings` が置き場を外すのと同じ理由。判定の有無で、外れたり
        # 外れなかったりさせない。
        places=(conf.tickets, conf.approved),
        synced=synced,
        # 退避（`ready`）が消した閉じたチケットを見分けるため、`logs/archive/` を読む。
        root=root,
    )
    # 設定ファイルについて言うことは、実行後チェックの報告より前に置く。
    # ガード自身が触られた回は、他の何よりそれが先に読まれてほしい。
    if guard:
        text = f"{guard}\n\n{text}" if text else guard
    # フェーズが終わったばかりなら、ここで 1 度だけ言う。止まるのは次の呼び出しから。
    bounced = ""
    if conf.tickets_enabled:
        # `post.check` が作業ツリーを戻そうとした回は、置き場のファイルも戻っていることが
        # あるので読み直す。それ以外の回は、上で読んだ置き場をそのまま使う。
        parent, raw = phase.parent_at(root, conf, payload.cwd, None if restored else raw)
        said = phase.announce(stderr, root, conf, parent, raw) if parent is not None else ""
        if said:
            text = f"{text}\n\n{said}" if text else said
        # サブエージェントが差し戻しを無視して終わったなら、親にそれを言う。
        # 差し戻しは 1 回きりなので、2 度目の終了は何も言われずに通っている。
        bounced = subagent.ignored_bounce(conf.state, payload)
        if bounced:
            text = f"{text}\n\n{bounced}" if text else bounced
    if not text:
        return EXIT_OK

    # 差し戻すのは、直前の実行が汚したと言える分があるときだけ。セッションが
    # 始まる前から在った変更は言うが差し戻さない。差し戻しは「あなたが直せ」で、
    # 誰が書いたか分からないものにそれを言うと、他人の書きかけを消しにいく。
    pushback = record.decision == audit.DENY

    if pushback and mode == modes.ENABLE:
        # このイベントで差し戻す経路は exit 2 と標準エラーだけ。ツールは
        # すでに走っているので取り消せず、渡せるのは「次に何をするか」になる。
        stderr.write(text + "\n")
        return EXIT_BLOCK

    if pushback:
        # warn は差し戻さない。呼び出しを止めないことがこのモードの約束で、
        # 差し戻しは止めはしないが次の一手を変えさせる。変えさせないまま
        # 数えるためのモードなので、届け先を報告の側にする。
        text = (
            f"[ccnavi dry-run] {modes.ENABLE} would have sent this back as a correction:\n" + text
        )
    # 起動したのがサブエージェント（入れ子）なら、差し戻しを無視した知らせはその子にしか
    # 届かない。ユーザにも見えるよう `systemMessage` に同じ文を載せる。
    # 上の exit 2 の経路では標準出力の JSON が読まれないので、載せられない。
    system = bounced if payload.agent_id else ""
    hookio.write_context(stdout, hookio.POST_TOOL_USE, text, system=system)
    return EXIT_OK
