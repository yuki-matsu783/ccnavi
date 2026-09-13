"""hook のイベントごとの手順。1 回の起動で何が起きるかは、ここを上から読めば分かる。

実行前の判定は judge、サブエージェントの始まりと終わりは subagent、実行後の
監視は post に本体があり、ここはイベント名からそれらへ振り分け、記録と応答を
繋ぐだけ。イベントが増えるたびに 1 本の関数が伸びないように、イベント 1 つに
つき関数 1 つ。
"""

from __future__ import annotations

from typing import TextIO

from . import (
    approval,
    audit,
    builtin,
    ctxfile,
    hookio,
    judge,
    modes,
    phase,
    post,
    reasons,
    ruleload,
    rules,
    selfguard,
    settings,
    subagent,
    tree,
)
from .modes import EXIT_BLOCK, EXIT_OK


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

    振り分けだけをここに置く。イベントが増えるたびに 1 本の関数が伸びると、
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
        return decide_at_start(stdout, stderr, mode, conf, root, payload, record)
    if payload.event == hookio.USER_PROMPT_SUBMIT:
        return decide_at_prompt(stdout, stderr, conf, root, payload, record)
    if payload.event == hookio.STOP:
        return decide_at_stop(stdout, stderr, conf, root, payload, record)
    if payload.event == hookio.SUBAGENT_START:
        return subagent.at_start(stdout, conf, root, payload, record)
    if payload.event == hookio.SUBAGENT_STOP:
        return subagent.at_stop(stdout, stderr, mode, conf, root, payload, record)
    # 判定を持たないイベントは誤りではない。想定していない登録が
    # 作業を止めてはいけない。
    record.decision, record.reason = audit.SKIP, audit.REASON_EVENT_NOT_CHECKED
    return EXIT_OK


def watch_context(
    stderr: TextIO, conf: settings.Settings, root: str, record: audit.Record
) -> tuple[list[post.Watched], post.ScopeGuard | None]:
    """ターンの区切りで作業ツリーを見る 2 つが、共通して使う持ち物。

    保護領域も範囲も、実行前の判定と同じ経路で解く。別に書くと、実行前に
    通った書き込みがターンの終わりに咎められる（あるいはその逆）ことになり、
    どちらが本当の宣言なのかを誰も言えなくなる。
    """
    return watched_for(stderr, conf, root, record), scope_guard(conf, root)


def watched_for(
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    record: audit.Record,
    payload: hookio.Input | None = None,
) -> list[post.Watched]:
    """実行後に見るツリーと、それぞれに当てるルール（設計 §25.7）。

    payload が無ければ全部のツリー（ターンの区切り）。あればワークスペースルートと、
    この呼び出しが触ったツリー（パスを持つツールは行き先、Bash は cwd）。
    ルールの引き方は実行前の判定と同じで、共通層にそのツリーの層を足した和。
    別に書くと、実行前に通った書き込みがターンの終わりに咎められる。
    """
    ws = tree.main_tree(root)
    if payload is None:
        trees = tree.all_trees(root, conf.projects)
    else:
        trees = [ws]
        where = record.subject if payload.tool_name in ruleload.PATH_TOOLS else payload.cwd
        t = tree.tree_of(root, where, conf.projects) if where else None
        if t is not None and t.root != ws.root:
            trees.append(t)
    loaded: dict[str, tuple[rules.RuleSet, str]] = {}
    out = []
    for t in trees:
        if t.project not in loaded:
            rule_set, source = ruleload.load_rules(stderr, conf.rules, record, root)
            if source != builtin.SOURCE:
                ruleload.add_layers(
                    stderr, rule_set, ruleload.layer_for(conf, root, t), root, record
                )
            loaded[t.project] = (rule_set, source)
        rule_set, source = loaded[t.project]
        out.append(post.Watched(t, rule_set, source))
    return out


def scope_guard(conf: settings.Settings, root: str) -> post.ScopeGuard | None:
    """承認済みチケットを、実行後の側から当てる持ち物。チケット制御が disable なら None。"""
    if not conf.tickets_enabled:
        return None
    copies, _ = approval.scan(conf, root)
    return post.ScopeGuard(root=root, copies=approval.by_id(copies), projects=conf.projects)


def decide_at_prompt(
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    payload: hookio.Input,
    record: audit.Record,
) -> int:
    """利用者が何か言ったとき。ターンの基準をここで取る。

    原則として何も返さない。このイベントで返した文はモデルのコンテキストに入るので、
    まだ何も起きていない時点で 1 段積むことになる。ここでやるのは、
    ターンの終わりに「このターンで何が変わったか」を言えるようにする控えだけ。

    例外は、このセッションがまだ知らない承認（人がボードで承認して置かれた承認済みチケット）。
    それは 1 度だけ伝える。伝えないと、人が「承認した」とチャットで打つまで
    モデルは後工程に入れない。
    """
    watched, scope = watch_context(stderr, conf, root, record)
    post.at_prompt(stderr, conf.state, (conf.state, conf.log), watched, scope, payload, record)
    told = approval.news(stderr, conf, root, payload.session_id, payload.agent_id)
    if told:
        hookio.write_context(stdout, hookio.USER_PROMPT_SUBMIT, told)
    return EXIT_OK


def decide_at_stop(
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    payload: hookio.Input,
    record: audit.Record,
) -> int:
    """ターンが終わったとき。宣言した保護領域の今の状態を人へ報告する。

    宛先が人なので `systemMessage` で返す。呼び出しごとの報告はモデルへ届く
    経路に載せてあり、そこは既に足りている。足りていないのは、ターンが終わった
    あとに人が「結局どこが変わったのか」を 1 度で見る場所のほう。

    exit 2 は使わない。このイベントでの exit 2 はターンを続けさせる意味になり、
    報告のために作業を終わらせない形になる。何も止めずに、言うだけにする。

    モードを見ない。dry-run でも disable でも報告する。ここは呼び出しにも
    作業ツリーにも手を出さず、見えたことを言うだけなので、モードが約束している
    ものを何ひとつ破らない。むしろ dry-run は「まだ何も適用していないが何が
    起きているか」を見るためのモードなので、ここが黙ると見る手立てが減る。
    """
    watched, scope = watch_context(stderr, conf, root, record)
    text = post.at_stop(stderr, conf.state, (conf.state, conf.log), watched, scope, payload, record)
    if text:
        hookio.write_system_message(stdout, text)
    return EXIT_OK


def decide_at_start(
    stdout: TextIO,
    stderr: TextIO,
    mode: str,
    conf: settings.Settings,
    root: str,
    payload: hookio.Input,
    record: audit.Record,
) -> int:
    """セッションが始まったとき。大きい対象の控えをここで 1 度だけ取る。

    ここで取るのは実行ファイルで、ツール呼び出しのたびに写すには大きすぎる。
    このイベントは 1 セッションに 1 回しか来ないので、重い仕事を置く先になる。

    判定は返さない。何も起きていない時点なので、言うことは 2 つだけ。控えを
    取れなかったこと（あれば）と、チケット制御が効いているときの作業の進め方。
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
        selfguard.targets(
            root, conf.rules, conf.bin, ruleload.layer_files(conf, root), conf.projects
        ),
    )
    # 「1 度だけ渡す文」の記憶はここで捨てる。このイベントは起動だけでなく再開と
    # compact の後にも来るので、モデルの文脈が新しくなるたびに文も改めて届く。
    ctxfile.forget(conf.state, payload.session_id)
    # 承認の控えは捨てない。控えが無ければ、いまの承認済みチケットを「知っているもの」として
    # 書く。それより後に置かれた承認済みチケットだけが、次の hook で「新しい承認」になる。
    approval.baseline(stderr, conf, root, payload.session_id, payload.agent_id)
    record.decision, record.enforced = audit.ALLOW, True
    texts = []
    if outcomes:
        record.guarded = [f"{o.target.key}:{o.action}" for o in outcomes]
        texts.append(selfguard.report(outcomes))
    if conf.tickets_enabled:
        texts.append(reasons.ways_of_working(conf, root, mode))
    if texts:
        hookio.write_context(stdout, hookio.SESSION_START, "\n\n".join(texts))
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
    """実行後の監視を 1 回動かし、言うことがあれば返す。

    期限を渡していない。実行前の期限は、遅い判定が黙った許可に化けるのを
    防ぐためのもので、止められるイベントでしか意味を持たない。ここは
    何も止めていないので、遅れは待ち時間にしかならない。作業ツリーを読む側は
    自前の短い時間を持っていて、そこに達したら何も言わずに終わる。
    """
    # 設定ファイルを先に戻す。ルールを読むより前でなければならない。あとから
    # 戻すと、この呼び出しが書き換えたルールファイルをそのまま読んで保護領域を
    # 決めることになり、`deny` を空にされた版で「守るものは無い」と判断する。
    # 守りの根拠を、この呼び出しが触れる前の状態に返してから読む。
    guard = judge.guard_setting_files(stderr, mode, conf, root, payload, record, selfguard.after)

    watched = watched_for(stderr, conf, root, record, payload)
    if len(watched) > 1:
        record.tree, record.project = watched[-1].tree.name, watched[-1].tree.project
    # 既定に落ちたことをこのイベントでは言わない。実行前の判定が呼び出しごとに
    # 言っているので、同じターンで 2 度届く。届く数が増えると、どちらも
    # 読まれなくなる。記録には fallback が残る。
    # 提案の状態を承認済みチケットへ写す。閉じた子の承認済みチケットはここで closed/ へ動く。
    # 範囲は実行前の判定と同じ経路で解く。
    if conf.tickets_enabled:
        phase.sync(stderr, root, conf)
    scope = scope_guard(conf, root)

    text = post.check(
        stderr,
        enforcing=mode == modes.ENABLE,
        restore=modes.effective_setting(mode, conf.restore_if_deny),
        state_dir=conf.state,
        mine=(conf.state, conf.log),
        watched=watched,
        scope=scope,
        payload=payload,
        record=record,
    )
    # 設定ファイルについて言うことは、実行後の監視の報告より前に置く。
    # ガード自身が触られた回は、他の何よりそれが先に読まれてほしい。
    if guard:
        text = f"{guard}\n\n{text}" if text else guard
    # フェーズが終わったばかりなら、ここで 1 度だけ言う。ゲートは次の呼び出しから。
    if conf.tickets_enabled:
        parent = phase.parent_for_cwd(root, conf, payload.cwd)
        said = phase.announce(stderr, root, conf, parent) if parent is not None else ""
        if said:
            text = f"{text}\n\n{said}" if text else said
        # サブエージェントが差し戻しを無視して終わったなら、親にそれを言う。
        # 差し戻しは 1 回きりなので、2 度目の終了は黙って通っている。
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
    hookio.write_context(stdout, hookio.POST_TOOL_USE, text)
    return EXIT_OK
