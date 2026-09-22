"""実行前の判定。ツール呼び出しの引数を見て、通す・聞く・止めるを決める。

ここが返すのは判定と、その根拠になった文（文面は reasons が組む）。応答の形に
組むのは hookio、記録に残すのは audit で、どちらも同じ結論から作られる。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TextIO

from . import (
    approval,
    audit,
    builtin,
    ctxfile,
    fsio,
    hookio,
    modes,
    phase,
    reasons,
    ruleload,
    rules,
    selfguard,
    settings,
    shellread,
    tree,
)
from . import ticket as ticket_mod
from .modes import EXIT_OK

# 1 回の起動に張る期限。呼び手は長く走った hook を打ち切って出力を捨てるので、
# それより先に自前の判定へ着地することが、遅い判定が黙った許可に化けるのを防ぐ。
DEADLINE_SECONDS = 3.0

# Claude Code 側の権限モード。ルールがどこも言及しなかった呼び出しの結末が、
# ここで変わる。ccnavi 自身のモード（上の 3 つ）とは別のもので、あちらは
# 「下した判定を適用するか」、こちらは「そもそも誰が判断するか」を決める。
#
# ルールが言及していない呼び出しについて、ccnavi は判定を持たない。持っていない
# 判定を ask として返すと、判断できる相手が居るモードでも必ず人に止まる。だから
# 返さず、Claude Code の権限モードに従う。
# ルールに書いていないものは、ルールに書いていないものとして渡す。
#
# 判断できる相手が居るモードは 4 つ。auto は classifier が読み、残りは Claude Code
# 自身の権限の仕組み（settings.json の permissions と、モードごとの既定）が決める。
# ccnavi がそこに確認を上乗せしても、判断する者が増えるわけではなく、同じ呼び出しで
# 2 度聞かれるだけになる。ここに無い綴りは ask に倒す。名前が 1 つ増えたときに、
# それが素通りではなく確認になるように。
PERMISSION_JUDGED = ("auto", "default", "acceptEdits", "plan")

# 人にも classifier にも確認できないモード。ここで ask を返すと「誰も答えないまま
# 通る」に化けるので、許可としない（REQ-PRE-08）。
PERMISSION_NO_JUDGE = ("dontAsk", "bypassPermissions")

# 判定を権限モードへ渡したことを表す内部の値。タイプ名（rules.ALLOW など）と
# 同じ変数に入るので、ルールファイルには現れない綴りにしてある。
HANDOVER = "(handover)"

# 範囲の中かどうかを問うツール。作業ツリーを書き換えるものだけ。
# Bash は入れていない。コマンド文字列に現れるパスは追えないので、ここで
# 当てると当たったり当たらなかったりする判定になる。シェル経由の書き込みは
# 実行後の監視が作業ツリーの実物を見て捕まえる。
SCOPE_TOOLS = ("Write", "Edit", "NotebookEdit")

# サブエージェントの起動ツール。レビューが済むまで止める対象で、対象の文字列を持たないので
# 見出しだけを subject にする。
AGENT_TOOL = "Agent"

# ツールごとに、ルールを当てる欄。ここに無いツールは対象を持たず、判定に届かない
# まま通る。名前は Claude Code の権限ルール `ToolName(指定子)` から括弧の中を
# 除いたものに揃えてある。WebSearch は指定子を持たないので載せない。並びは画面の
# 候補の順になる。
#
# 対象を差し込む側（試験の diagnose と lint の probe）はこの表を読む。欄の名前を
# subject_of の中にだけ持つと、ツールを足したときにそちらが黙って古くなる。
SUBJECT_FIELDS: dict[str, str] = {
    "Bash": "command",
    "PowerShell": "command",
    "Read": "file_path",
    "Grep": "path",
    "Glob": "path",
    "Edit": "file_path",
    "Write": "file_path",
    "NotebookEdit": "notebook_path",
    "Skill": "skill",
    AGENT_TOOL: "description",
    "WebFetch": "url",
}

# 行き着く先まで解いてから当てるツール（パスを対象にするもの）。行き先の層を選ぶ一覧と
# 同じものを使う。別に持つと、判定はパスに当てるのに層は共通層だけ、という食い違いが起きる。
PATH_TOOLS = ruleload.PATH_TOOLS

# 探す場所を省略できるツール。省略は「いま居る場所」の意味なので、cwd を対象にする。
# 空のままにすると、場所を書かない呼び出しがどのルールにも当たらずに通る。
SEARCH_TOOLS = ("Grep", "Glob")


def guard_setting_files(
    stderr: TextIO,
    mode: str,
    conf: settings.Settings,
    root: str,
    payload: hookio.Input,
    record: audit.Record,
    step: Callable[..., list[selfguard.Outcome]],
) -> str:
    """ccnavi 自身の設定ファイルを控えと突き合わせる 1 回。返す文を作る。

    実行前と実行後で違うのは呼ぶ関数だけなので、そこを引数で受け取る。
    対象の決め方も設定の解き方も 1 か所にまとまり、片方だけが別の対象を
    見ている、という食い違いが起きない。
    """
    setting = modes.effective_setting(mode, conf.guard_core_files)
    outcomes = step(
        stderr,
        setting,
        conf.state,
        payload.session_id,
        root,
        selfguard.targets(
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
    """実行前の判定に達し、何が起きたかを record に書き込む。
    記録と応答が必ず同じ結論から作られるようにするため。"""
    # 控えを取るのは判定より先。ここで取る断面が、この呼び出しが何かを壊した
    # ときに戻る先になる。判定で弾かれた呼び出しでも先に取っておくのは、
    # 弾けなかったものだけが作業ツリーに届くので、届いた側から見れば
    # 「直前」は判定の前だから。
    guard = guard_setting_files(stderr, mode, conf, root, payload, record, selfguard.before)

    if not record.subject:
        record.decision, record.reason = audit.SKIP, audit.REASON_NO_SUBJECT
        if guard:
            hookio.write_context(stdout, hookio.PRE_TOOL_USE, guard)
        return EXIT_OK

    rule_set, source, target = ruleload.rules_for(stderr, conf, root, payload, record)
    if target is None and payload.cwd:
        # Bash には行き先が無い。記録には cwd のツリーを添える。判定には使わない。
        target = tree.tree_of(root, payload.cwd, conf.projects)
    if target is not None:
        record.tree, record.project = target.name, target.project
    # 設定ファイルを守る側が有効なら、そこへシェルから書き込む形を止める
    # ルールを judgment に足す。戻せるだけでは足りないので、同じ場所を
    # 実行前にも止める。既定に落ちているときは足さない。組み込みの既定が
    # 同じ形を既に持っていて、二重に当たると同じ話が 2 度返る。
    if (
        record.fallback != builtin.FALLBACK
        and modes.effective_setting(mode, conf.guard_core_files) != selfguard.DISABLE
    ):
        selfguard.add_rules(
            rule_set, conf.bin, conf.project_home, root, selfguard.common_layer_files(conf)
        )
    # チケットの状態の置き場を守る。動かすのはスクリプトだけで、直接の作成・移動は
    # 誰がやっても止める。チケット制御が効いているときだけ足す。
    if conf.tickets_enabled:
        rule_set.deny.extend(ticket_mod.guard_rules(conf.tickets, root))
        # 人の判断の経路（承認・レビュー済みの受け入れ・状態とレビューの操作）を、
        # 実行ファイルを直接打つ形で通さない。スクリプト 2 本の中身がこれ。
        if conf.guard_ticket_approval != selfguard.DISABLE:
            rule_set.deny.append(phase.ticket_approval_rule(conf.bin, root))

    subject, bare, inner, rewrites = screen(payload.tool_name, record.subject, record)

    if not subject:
        # コマンドは在るが、実行される部分が残らなかった。コメントだけの行が
        # これにあたる。ルールを当てる先が無いので、どのタイプにも当たらず
        # 権限モードへ渡る先になるが、何も走らないものについて誰かの判断を
        # 求める意味は無い。
        record.decision, record.reason = audit.SKIP, audit.REASON_NOTHING_TO_RUN
        if guard:
            hookio.write_context(stdout, hookio.PRE_TOOL_USE, guard)
        return EXIT_OK

    # 組み込みの既定に落ちたときだけ言う。層が壊れて空になったのは組み込みへの
    # 退避ではないので、同じ文面を出すと「既定で判定している」と読み違えられる。
    # そちらは記録の `fallback` に層の名前が残り、`--lint` が error で言う。
    fallback = (
        reasons.fallen_back(record.detail or conf.rules)
        if record.fallback == builtin.FALLBACK
        else ""
    )
    notices = [text for text in (guard, fallback) if text]

    # サブエージェントには、状態を動かすスクリプトもレビューのスクリプトも打たせない。
    # 閉じるのは親だけ（REQ-TKT-10）。ルールより先に見る。ルールが allow と
    # 書いていても、この 2 本はサブエージェントの手には渡さない。
    if (
        conf.tickets_enabled
        and payload.agent_id
        and payload.tool_name in phase.SHELL_TOOLS
        and phase.forbidden(subject, shellread.SEP.join(layer for _, layer, _ in inner))
    ):
        # 禁止は中で実行されるコマンドにも当てる。`env sh …ccnavi-ticket.sh start` を
        # 元の形だけで見ると、先頭の `sh` に固定した形が外れて素通りになる。
        runner, layer = ("", "")
        if not phase.forbidden(subject):
            runner, layer = next((r, x) for r, x, _ in inner if phase.forbidden(x))
        record.code, record.rules = phase.CODE_SUBAGENT, [reasons.TICKET_RULE]
        record.unwrapped = layer
        return refuse(
            stdout,
            mode,
            record,
            rules.DENY,
            notices + [reasons.subagent_forbidden(subject, runner, layer)],
        )

    # HITL ポイント。人間レビュー要のフェーズが終わっていてマーカーが無い間、
    # サブエージェントの起動と、例外の 3 本以外のシェル実行を止める（REQ-TKT-15）。
    # ルールより先に見る。
    if conf.tickets_enabled and payload.tool_name in phase.HELD_TOOLS:
        parent = phase.parent_for_cwd(root, conf, payload.cwd)
        held = phase.held_phase(root, conf, parent.ticket) if parent is not None else None
        exempt = payload.tool_name == "Bash" and phase.exempt(subject, record.degraded)
        if held is not None and not exempt:
            record.code, record.rules = phase.CODE_REVIEW, [reasons.TICKET_RULE]
            reason = phase.hold_reason(held, payload.tool_name, root)
            return refuse(stdout, mode, record, rules.DENY, notices + [reason])

    # 承認済みチケットの索引。ワークツリーへの書き込みでは、プロジェクトの食い違いの点検と
    # チケットの範囲の判定の両方が引く。走査は 1 回で数百ミリ秒かかるので、1 回の判定で
    # 1 度だけ読んで両方に渡す（設計 §9）。ワークスペースルートへの書き込みとシェルでは読まない。
    index = None
    if (
        conf.tickets_enabled
        and target is not None
        and not target.is_main
        and payload.tool_name in SCOPE_TOOLS
    ):
        copies, _ = approval.scan(conf, root)
        index = approval.by_id(copies)

    # ワークツリーの元リポジトリと承認済みチケットの `project:` の食い違いは、ルールより先に見る。
    # 範囲の宣言ではなく取り違えなので、ルールが allow と言っていても通さない。
    if conf.tickets_enabled and target is not None and payload.tool_name in SCOPE_TOOLS:
        mismatch = project_mismatch(conf, root, target, record.subject, index)
        if mismatch:
            record.code, record.rules = reasons.CODE_TICKET_PROJECT, [reasons.TICKET_RULE]
            return refuse(stdout, mode, record, rules.DENY, notices + [mismatch])

    # 書き直しを求める形は、ルールより先に止める（ADR-0046、ADR-0047）。ブレース展開と、実行する
    # ときに決まるコマンド名は、ルールを当てる読みと実行されるものが食い違う。
    # `{git,push,origin,main}` も `c=git; $c push origin main` も、raw-git に当たらないまま push を
    # 実行する。バッククォートとシェルで読みが割れる形は、読み分けると規則が増え、読み違えると
    # 素通りに倒れる。どれも書き直す道が必ずあるので、読み解かずに止めて、形ごとの書き直し方を
    # 1 回で返す。
    if rewrites:
        forms = list(dict.fromkeys(form for form, _ in rewrites))
        record.code = reasons.CODE_REWRITE[forms[0]]
        record.rules = [reasons.rewrite_rule(form) for form in forms]
        parts = [
            reasons.rewrite(record.subject, form, [text for f, text in rewrites if f == form])
            for form in forms
        ]
        return refuse(stdout, mode, record, rules.DENY, notices + parts)

    # 強いタイプから順に見て、最初に当たったところで止める。deny に当たった
    # 呼び出しについて ask のタイプを調べる意味は無いし、調べれば「拒否だが
    # 確認もしろ」という読めない結論に届く道ができる。
    verdict, group = "", []
    # group と同じ並びで、中で実行されるコマンドで当たったときの
    # （実行役のコマンド, そのコマンド, 引用の中から切り出したコマンドの層か）。
    # 元の形で当たったルールは ("", "", False)。
    via: list[tuple[str, str, bool]] = []
    for name in rules.SECTIONS:
        if name == rules.ALLOW and record.degraded:
            # 読み切れなかったコマンドに allow は当てない。当てる先は
            # 実行される部分ではなく生の文字列なので、そこで許可を出すのは
            # 「読めなかった文字列にそう書いてあった」を根拠に通すことになる。
            # deny と ask は当てたままにする。生の文字列に当たりすぎるぶんは
            # 厳しい側へ外れるだけで、そのことは文面が断る。
            break
        # 中で実行されるコマンドは deny と ask にだけ当てる。止める側に足す当て先なので、
        # 層を読み違えても当たるはずのものが当たらないだけで、元の形の判定は消えない。
        # allow に当てると逆になる。`sudo -u me cat /etc/hosts` は元の形では確認に落ちるが、
        # 中の `cat …` が読み取りの allow に当たって通るようになる。
        #
        # 引用の外の層を先に見る。同じルールが引用の中と外の両方に当たるなら、外で当たった
        # ことにする。引用の中の断りは、引用の中にだけ当たったときに付けるものなので。
        layers = sorted(inner, key=lambda x: x[2]) if name != rules.ALLOW else []
        for rule in rule_set.section(name):
            if time.monotonic() > deadline:
                stderr.write("ccnavi: 判定を終える前に期限に達した\n")
                record.decision, record.reason = audit.SKIP, audit.REASON_DEADLINE_EXCEEDED
                return modes.fail_closed(mode)
            if rule.matches(payload.tool_name, subject):
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

    record.rules = [rule.id or f"({verdict})" for rule in group]
    record.unwrapped = shellread.SEP.join(dict.fromkeys(layer for _, layer, _ in via if layer))
    # 判定を下したのは最初に当たったルール（設計 §11.9）。その層を 1 欄で残す。
    # id の前置きからも読めるが、欄にしておくと記録を層で数えられる。ルールファイルの
    # 外から足したルール（組み込みの守り、チケット）は層を持たないので空のまま。
    if group:
        record.source = group[0].source

    # 止めた・聞いたルールのうち、引用の中から切り出したコマンドにだけ当たったもの。
    # 書いた側は文字を書いただけのつもりでいるので、回避策を知らせないと、
    # 同じ形を書き直しては止まる。どこに当たったかは一致位置から推し量らず、
    # 引用の中の中身を除いた読み（bare）に当て直して、当たらなければそうだとする。
    # こうするとルールの書き方（glob / regex、`^` / `(^|\x00)`）に依らない。
    # 中で実行されるコマンドで当たったルールは、bare に当て直せない（bare は元の形の読み）。
    # そちらは、当たった層が引用の中から切り出したコマンドのものかどうかで決める。
    inside = [False] * len(group)
    if verdict in (rules.DENY, rules.ASK):
        inside = [
            quoted if layer else bare != subject and not rule.matches(payload.tool_name, bare)
            for rule, (_, layer, quoted) in zip(group, via, strict=True)
        ]
    record.quoted = [name for name, hit in zip(record.rules, inside, strict=True) if hit]

    # ルールの判定とチケットの判定を合わせ、強い側を採る（設計 §9.5）。同じ強さならルール。
    # ルールの deny はどう書いても最も強いので、そのときはチケットを見ない。
    # チケットは閉じる向きにしか効かない（範囲の外・deny・ask）。人が書いたルールの allow を
    # 作業 1 本のあいだ狭めることはあっても、ルールの deny や ask を開けることは無い。
    # チケットが効くのは人が承認したあとだけで、承認画面が「ルールの allow も範囲の外では
    # 止まる」と言う。
    ticket_reason = ""
    ticket_code = ""
    if verdict != rules.DENY:
        rule_hit = (group[0].id or f"({verdict})", verdict) if group else None
        ticket_decision, text, ticket_notice, code = ticket_verdict(
            conf, root, payload.tool_name, record.subject, index, rule_hit
        )
        if ticket_notice:
            notices.append(ticket_notice)
        if STRENGTH[ticket_decision] > STRENGTH[verdict]:
            verdict, ticket_reason, ticket_code = ticket_decision, text, code
            if ticket_reason:
                # 判定を下したのは層を持たないチケット。狭められたルールの id は後ろに残し、
                # 「ルールは通したのにチケットが止めた」回を記録から数えられるようにする。
                record.rules = [reasons.TICKET_RULE, *record.rules]
                record.source = ""

    # 当たったルールがモデルへ渡す文。通す・聞く・止めるのどれでも、判定とは
    # 別の経路（additionalContext）で届く。
    context = ctxfile.for_rules(
        stderr, conf.state, payload, group, ctxfile.bases(conf, root, target)
    )
    # このセッションがまだ知らない承認（人がボードで承認して置かれた承認済みチケット）は、
    # 判定がどれでも 1 度だけ添える。応答は 1 つの JSON なので、ルールの文と
    # 同じ経路（additionalContext）に合流させる。
    told = approval.news(stderr, conf, root, payload.session_id, payload.agent_id)
    # 提案を書いた回に、承認を頼む前の確認を 1 度だけ伝える文（REQ-APV-14）。判定には
    # 足さない（`ticket_mod.propose_notice` の説明）ので、同じ口から渡す。
    if conf.tickets_enabled:
        told = "\n\n".join(
            p
            for p in (
                told,
                ticket_mod.propose_notice(stderr, conf, root, payload, record.subject),
            )
            if p
        )
    if told:
        context = "\n\n".join(p for p in (told, context) if p)

    if verdict == rules.ALLOW:
        record.decision, record.enforced = audit.ALLOW, True
        if notices or context:
            # 通した回にも言う。ガードが今なにを見ていないのかを黙っていると、
            # 誰も知らないまま作業が進む。呼び出しごとに出るのでうるさいが、
            # うるさいのが正しい。壊れた設定と未承認のチケットはどちらも
            # 短命であるべきで、黙って居座られるより気づかれたほうがよい。
            # ルールの additionalContext も同じ経路で、通す代わりに一言添える。
            hookio.write_context(
                stdout, hookio.PRE_TOOL_USE, "\n\n".join(notices + ([context] if context else []))
            )
        return EXIT_OK

    # どちらの文面を返すかは、判定を決めた側で分ける。ルールが当たっていても、
    # チケットが強かった回はチケットの文面になる（ticket_reason があるのはその回だけ）。
    if verdict == rules.DENY and not ticket_reason:
        # ルールの deny。当たった理由はまとめて 1 回で返す。1 つずつ返すと、エージェントも
        # 1 つずつ直すことになり、そのたびに往復が 1 回増える。
        texts = [
            reasons.reason_for(
                rule, payload.tool_name, subject, source, record.degraded, runner, layer, quoted
            )
            for rule, (runner, layer, _), quoted in zip(group, via, inside, strict=True)
        ]
        # 全部が中で実行されるコマンドで当たったなら、当てた先は読み直したコマンドであって
        # 生の文字列ではない。読み切れなかったことを根拠のコードにしない。
        read_through = all(layer for _, layer, _ in via)
        record.code = reasons.code_for(payload.tool_name, "" if read_through else record.degraded)
        if any(rule.id == phase.TICKET_APPROVAL_RULE_ID for rule in group):
            record.code = phase.CODE_TICKET_APPROVAL
    elif verdict == rules.DENY:
        # チケットが止めた。範囲の外かチケットの deny で、ルールは何も言わないか、
        # allow / ask に当たっている（そのときは文面がルールの id を名指しする）。
        texts = [ticket_reason]
        record.code = ticket_code or reasons.CODE_TICKET_SCOPE
    elif verdict == rules.ASK and not ticket_reason:
        texts = [
            reasons.reason_for(
                rule, payload.tool_name, subject, source, record.degraded, runner, layer, quoted
            )
            for rule, (runner, layer, _), quoted in zip(group, via, inside, strict=True)
        ]
        record.code = reasons.CODE_RULE_ASK
    elif verdict == rules.ASK:
        # チケットが ask と書いた場所。人が 1 度見る場所として宣言されている。
        # ルールは何も言わないか、allow に当たっている。
        texts = [ticket_reason]
        record.code = reasons.CODE_TICKET_ASK
    else:
        # どのタイプも言及しなかった。ccnavi はこの呼び出しの判定を持たない。
        # 結末は Claude Code の権限モードが決める。
        record.code = reasons.CODE_UNCERTAIN if record.degraded else reasons.CODE_UNDECLARED
        verdict = undeclared_verdict(payload.permission_mode, record.degraded, conf.guard_unwatched)
        texts = [
            reasons.undeclared(
                payload.tool_name, subject, conf.rules, record.degraded, verdict == rules.DENY
            )
        ]

    if verdict == HANDOVER:
        # 判定は渡した。ここで残せるのは記録だけで、それがこの分担の要点になる。
        # ルールが言及していない場所は log の handover を数えれば分かり、その数は
        # 人に聞いた回とも、classifier が通した回とも混ざらない。
        #
        # 文面は返さない。呼び出しごとに「ルールが言及していない」と言うと、
        # 渡した先が判断するだけの回に毎度コンテキストを 1 段積むことになる。
        # 穴の在処は記録から読む。
        record.decision, record.enforced = audit.HANDOVER, False
        # 新しい承認だけは、渡す回にも言う。言わないと、その承認を伝える機会が
        # 権限モードに渡す呼び出しの分だけ遅れる。
        if notices or told:
            hookio.write_context(
                stdout, hookio.PRE_TOOL_USE, "\n\n".join(notices + ([told] if told else []))
            )
        return EXIT_OK

    return refuse(stdout, mode, record, verdict, notices + texts, context)


def refuse(
    stdout: TextIO,
    mode: str,
    record: audit.Record,
    verdict: str,
    parts: list[str],
    context: str = "",
) -> int:
    """拒否か確認を返す。dry-run なら返す代わりに、返していたはずだと言う。

    context はルールの `additionalContext`。判定と一緒にモデルへ渡す文で、
    dry-run では判定の代わりの文に続けて出す。止めない回でも文だけは届く形にして、
    enable に切り替えたときに初めて読まれる文を残さない。
    """
    # 空行で割るのは、1 件ずつが閉じた文であることを見た目でも保つため。
    reason = "\n\n".join(parts)
    decision = audit.DENY if verdict == rules.DENY else audit.ASK

    if mode == modes.DRY_RUN:
        record.decision, record.enforced = decision, False
        would = "stopped" if verdict == rules.DENY else "asked the user about"
        text = f"[ccnavi dry-run] {modes.ENABLE} would have {would} this call:\n" + reason
        if context:
            text += "\n\n" + context
        hookio.write_context(stdout, hookio.PRE_TOOL_USE, text)
        return EXIT_OK

    record.decision, record.enforced = decision, True
    hookio.write_verdict(
        stdout, hookio.DENY if verdict == rules.DENY else hookio.ASK, reason, context
    )
    return EXIT_OK


def subject_of(payload: hookio.Input) -> str:
    """このツールでルールを当てる欄を選ぶ。欄は SUBJECT_FIELDS が持つ。"""
    tool = payload.tool_name
    field = SUBJECT_FIELDS.get(tool)
    if field is None:
        return ""
    if tool == AGENT_TOOL:
        # 起動には対象の文字列が無い。レビューが済むまで止める対象なので、見出しを subject に
        # して判定に入れる。
        return payload.field_value(field) or payload.field_value("prompt") or "(agent)"
    value = payload.field_value(field)
    if tool == "NotebookEdit":
        value = value or payload.field_value("file_path")
    if tool in SEARCH_TOOLS:
        value = value or "."
    if tool in PATH_TOOLS:
        return fsio.full_path(value, payload.cwd)
    return value


def screen(
    tool: str, subject: str, record: audit.Record
) -> tuple[str, str, list[tuple[str, str, bool]], list[tuple[str, str]]]:
    """シェルのコマンドを、実際に実行される部分まで絞る。読み切れなかったときは
    record にそう書き残す。

    返すのは 4 つ。1 つめはルールを当てる読み。2 つめはそこから引用の中で切り出した
    コマンドを除いた読み（shellread.Reading.bare）で、文面の断りを決めるためだけに使う。
    シェルでないツールと読み切れなかったコマンドでは、1 つめと 2 つめは同じになる。

    ルールはコマンドについて書かれたものであって、文字列についてではない。
    git push を引用した文書は push ではないし、そこで拒否を返すことは、
    やっていないことをやったと読み手に告げることになる。

    読み切れないコマンドは生の文字列に落とす。生の文字列には実行される部分が
    すべて含まれるので、捕まえるべきものが抜けることはない。その代わり、返す拒否は
    どちらの拒否なのかを名乗る。

    3 つめは、実行役のコマンド（`env` `sudo` `sh -c` など）が中で実行するコマンドの並び。
    1 つずつが（実行役のコマンドの名前, 中で実行されるコマンド, 引用の中から切り出した
    コマンドの層か）。読み切れないコマンドでもトークンに割れる限り返る。Bash 以外は空。
    `cd` で移った先から見た綴り（`shellread.Reading.moved`）も、実行役のコマンドの名前を
    `shellread.MOVED` にしてここに並ぶ。どちらも止める側のルールにだけ当てる。

    4 つめは、書き直しを求める形の（形, 綴り）の並び（shellread.Reading.rewrites）。Bash 以外は空。
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
    # `cd` で移った先から見た綴りも、中で実行されるコマンドと同じ並びに足す（ADR-0069）。
    # 書かれた綴りの当たり方は動かさず、当てる先を足すだけにする。止める側のルールにしか
    # 当たらないので、`cd` を読み違えても、当たるはずのものが当たらなくなることはない。
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
    index: dict[str, ticket_mod.Ticket] | None = None,
) -> str:
    """ワークツリーの元リポジトリと、そこに結び付く承認済みチケットの `project:` が
    違えば、その理由の文。

    範囲の宣言ではなく取り違えなので、ルールより先に見る（REQ-MLT-12）。ルールが
    allow と言っていても通さない。子は親から継ぐ。判定はエージェントの申告を見ない。
    行き先のツリーが誰のものかは、そのツリーの `.git` が指す先で決まっている。

    index は承認済みチケットの索引。呼び手がチケットの範囲の判定と共有して渡す。
    無ければここで読む。
    """
    if t.is_main:
        return ""
    # 読むのは権威のある側（親のツリー）の写し。子のツリーにも checkout されているが、
    # 閉じるのも着手の欄を書くのも親のツリーの側なので、そこを読まないと閉じた
    # チケットの範囲がいつまでも効く。
    if index is None:
        copies, _ = approval.scan(conf, root)
        index = approval.by_id(copies)
    ticket = index.get(t.name)
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
            f"ticket: {ticket.ticket} ({ticket.title}) was approved for project "
            f"'{owner or '(workspace)'}', but worktree {t.name} was cut from "
            f"'{t.project or '(workspace)'}'",
            "Do not write here. The worktree must be created from the repository the ticket "
            "names (projects/<project>), or the ticket must be proposed again for this "
            "repository and approved by the user.",
        ]
    )


# 判定の強さ。ルールの判定とチケットの判定を合わせるとき、強い側を採る（設計 §1）。
# 何も言わない（空）がいちばん弱い。同じ強さならルールを採る。
STRENGTH = {rules.DENY: 3, rules.ASK: 2, rules.ALLOW: 1, "": 0}


def ticket_verdict(
    conf: settings.Settings,
    root: str,
    tool: str,
    full: str,
    index: dict[str, ticket_mod.Ticket] | None = None,
    rule_hit: tuple[str, str] | None = None,
) -> tuple[str, str, str, str]:
    """チケットが承認された範囲について何を言うかを返す。判定と、理由の文と、注記と、理由のコード。

    コードは空のことが多い。記録に残す綴りを呼び手が決められないとき（範囲の外ではなく
    チケット自体が信じられないとき、ADR-0058）だけ、ここが名乗る。

    鍵はファイルの行き先。解いた先が `.claude/worktrees/<名前>/` の中なら、その名前と
    同じ識別子の承認済みチケットで判定する。ワークスペースルートの直下ならチケットは無く、ルールだけで判定する。
    呼び出し元の cwd も agent_id も使わない（REQ-TKT-01）。

    範囲の中は allow、範囲の `ask` は ask、範囲の外とチケットの `deny` は deny。
    チケットが境界を明示している以上、外に出たことは「宣言に反した」になる。
    子は親の範囲とフェーズの種類の上限で切り詰め、厳しい側が勝つ（phase.scope_verdict）。
    承認は範囲の超過を警告で通すので、超えた分はここで止まる。止めた上限を `limit:` 行で
    名指しする。ルールの判定と比べて強い側を採るのは呼び手。

    注記は、親が計画を持つのに子の番号の種類が読めないときの 1 文。そのときは種類では
    切り詰めない（親の範囲では切り詰める）ので、効いていない上限があることを判定に添える。

    index は承認済みチケットの索引。1 回の判定で走査を 1 度にするため、呼び手が
    プロジェクトの食い違いの点検と共有して渡す。無ければここで読む。

    rule_hit は、ルールが当たっていたときの (ルールの id, タイプ)。チケットがそれより
    厳しい判定を返すときは文面で名指しする。ルールは通しているのに止まった理由が
    読めないと、受け取った側はルールファイルを探しに行って見つけられない。

    チケットの置き場（提案と承認済みチケット）については何も言わない。承認された範囲の外に
    あるのが普通で、そこを deny にすると、いちど承認した範囲から出る道が無くなる。
    """
    if not conf.tickets_enabled or tool not in SCOPE_TOOLS or not full:
        return "", "", "", ""
    t = tree.tree_of(root, full, conf.projects)
    if t is None or t.is_main:
        return "", "", "", ""
    if index is None:
        copies, _ = approval.scan(conf, root)
        index = approval.by_id(copies)
    # 区別しない機械では綴りの違いを許す。SubagentStart / SubagentStop / 実行後の監視と
    # 同じ引き方。ここだけ厳密に引くと、`I0001-01` と切ったワークツリーは案内では
    # 「効いている」と言われながら判定では権限モード任せに落ちる。
    ticket = tree.lookup(index, t.name)
    if ticket is None:
        return "", "", "", ""
    rel = tree.relative(t, full)
    if ticket_mod.is_unscoped(rel, conf.tickets, conf.approved):
        return "", "", "", ""
    parent = index.get(ticket.parent) if ticket.is_child else None
    # 種類を読むのは、親が計画を持ち子の番号が計画に在るときだけ。番号だけの親では
    # phases.yml を開かない。
    pt, notice = None, ""
    if parent is not None and phase.plan_item(ticket, parent) is not None:
        types = phase.load_types(conf, root, parent.project)
        pt = phase.type_for(conf, root, ticket, parent, types or {})
        missing = phase.unread_type(conf, root, ticket, parent, types)
        if missing:
            notice = (
                f"[ccnavi] {ticket.ticket} のフェーズ {ticket.phase} の種類 `{missing}` が"
                "読めないので、種類の上限では切り詰めていない（親 "
                f"{parent.ticket} の範囲では切り詰めている）。phases.yml が壊れているか、"
                "種類が消えている。利用者に伝えて直してもらう（'ccnavi --lint' が箇所を言う）。"
            )
    found = phase.scope_verdict(ticket, parent, pt, rel)
    if found.verdict == rules.ALLOW:
        return rules.ALLOW, "", notice, ""

    if found.limit == phase.LIMIT_BLOCKED:
        # 範囲の外に書いたのではなく、チケット自体が信じられない。範囲を見せても
        # 直しようが無いので、代わりに引っかかった検査を名指しする（ADR-0058）。
        return (
            rules.DENY,
            "\n".join(
                [
                    f"[ccnavi] {reasons.CODE_TICKET_BLOCKED} (source: {ticket.path})",
                    f"subject: {full}",
                    f"ticket: {ticket.ticket} ({ticket.title}), worktree {t.name}",
                    f"problem: {ticket.blocked}",
                    "The approved ticket for this worktree does not hold together, so its work "
                    "area is not in effect and every write here is blocked. Nothing you write "
                    "can fix this: the user has to repair the approved ticket or where it sits. "
                    "Tell them the problem line above and ask them to run 'ccnavi --lint', "
                    "which names every ticket in this state.",
                ]
            ),
            notice,
            reasons.CODE_TICKET_BLOCKED,
        )

    area = ", ".join(ticket.paths(rules.ALLOW) + ticket.paths(rules.ASK)) or "(空)"
    source = ticket.path
    head = [
        f"subject: {full}",
        f"ticket: {ticket.ticket} ({ticket.title}), approved {ticket.approved_at}, "
        f"worktree {t.name}",
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
        # 範囲の外ではなく、チケットが deny と書いた項に当たったなら、どの項かを名指しする。
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
    # 上限ごとに次の一手が違う。子の範囲の外なら提案し直し、親や種類の上限の外なら、
    # 範囲を広げても通らない（承認で超過を見せたうえで止めている）。
    if found.limit == phase.LIMIT_TYPE and found.type is not None:
        pt = found.type
        head.append(f"limit: phase type {pt.title} ({pt.id}): {', '.join(pt.scope_globs)}")
        body = (
            "This path is inside the ticket's work area but outside what phase type "
            f"{pt.title} ({pt.id}) allows. The ticket was approved with that overflow shown as "
            "a warning; writes there stay blocked. Do the work in a later phase whose type "
            "covers this path, or ask the user to change phases.yml."
        )
    elif found.limit == phase.LIMIT_PARENT and parent is not None:
        parent_area = ", ".join(parent.paths(rules.ALLOW) + parent.paths(rules.ASK)) or "(空)"
        head.append(f"limit: parent {parent.ticket}: {parent_area}")
        body = (
            "This path is inside the ticket's work area but outside its parent "
            f"{parent.ticket}. The ticket was approved with that overflow shown as a warning; "
            "writes there stay blocked. Do the work inside the parent's area, or, if the task "
            "genuinely needs this path, tell the parent so it can propose a ticket whose parent "
            "covers it and ask the user to approve it."
        )
    else:
        body = (
            "This path is outside the work area the ticket for this worktree declares. Do the "
            "work inside that area, or, if the task genuinely needs this path, tell the parent "
            "so it can propose a ticket that covers it and ask the user to approve it (from the "
            "ccnavi board in VS Code, or 'ccnavi --approve' in a terminal). "
            "Editing a proposal alone changes nothing."
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
    モードの既定に落ちる。読めなかったことを言えるのはここだけ（REQ-PRE-04）。

    知らないモードは ask に倒す。名前が 1 つ増えたときに、それが素通りではなく
    確認になるように。設定漏れがガードの消失にならない側へ既定を置く。

    確認できる者が居ないモードは、既定では通さない（REQ-PRE-08）。そこで ask を
    返しても「誰も答えないまま通る」に化けるため。CCNAVI_GUARD_UNWATCHED を
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
