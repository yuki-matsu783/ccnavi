"""実行前の判定。ツール呼び出しの引数を見て、通す・聞く・止めるを決める。

ここが返すのは判定と、その根拠になった文（文面は reasons が組む）。応答の形に
組むのは hookio、記録に残すのは audit で、どちらも同じ結論から作られる。
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import TextIO

from . import (
    approval,
    audit,
    ctxfile,
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
# 判定を ask として返すと、判断できるモードでも必ず人に止まり、auto モードが
# 実質効かなくなる。だから返さず、Claude Code の権限モードに従う。
# ルールに書いていないものは、ルールに書いていないものとして渡す。
PERMISSION_JUDGED = ("auto",)

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

# サブエージェントの起動ツール。ゲートが止める対象で、対象の文字列を持たないので
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

# 行き着く先まで解いてから当てるツール（パスを対象にするもの）。
PATH_TOOLS = ("Read", "Grep", "Glob", "Edit", "Write", "NotebookEdit")

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
        selfguard.targets(root, conf.rules, conf.bin, ruleload.project_rules_files(conf)),
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
        not record.fallback
        and modes.effective_setting(mode, conf.guard_core_files) != selfguard.DISABLE
    ):
        selfguard.add_rules(
            rule_set, conf.bin, selfguard.project_rules_clause(conf.projects, conf.project_rules)
        )
    # チケットの状態の置き場を守る。動かすのはスクリプトだけで、直接の作成・移動は
    # 誰がやっても止める。チケット制御が効いているときだけ足す。
    if conf.tickets_enabled:
        rule_set.deny.extend(ticket_mod.guard_rules(conf.tickets))
        # 人の判断の経路（承認・レビュー済みの受け入れ・状態とレビューの操作）を、
        # 実行ファイルを直接打つ形で通さない。スクリプト 2 本の中身がこれ。
        if conf.guard_ticket_approval != selfguard.DISABLE:
            rule_set.deny.append(phase.ticket_approval_rule(conf.bin))

    subject = screen(payload.tool_name, record.subject, record)

    if not subject:
        # コマンドは在るが、実行される部分が残らなかった。コメントだけの行が
        # これにあたる。ルールを当てる先が無いので、どのタイプにも当たらず
        # 権限モードへ渡る先になるが、何も走らないものについて誰かの判断を
        # 求める意味は無い。
        record.decision, record.reason = audit.SKIP, audit.REASON_NOTHING_TO_RUN
        if guard:
            hookio.write_context(stdout, hookio.PRE_TOOL_USE, guard)
        return EXIT_OK

    fallback = reasons.fallen_back(record.detail or conf.rules) if record.fallback else ""
    notices = [text for text in (guard, fallback) if text]

    # サブエージェントには、状態を動かすスクリプトもレビューのスクリプトも打たせない。
    # 閉じるのは親だけ（REQ-TKT-10）。ルールより先に見る。ルールが allow と
    # 書いていても、この 2 本はサブエージェントの手には渡さない。
    if (
        conf.tickets_enabled
        and payload.agent_id
        and payload.tool_name in phase.SHELL_TOOLS
        and phase.forbidden(subject)
    ):
        record.code, record.rules = phase.CODE_SUBAGENT, [reasons.TICKET_RULE]
        return refuse(
            stdout, mode, record, rules.DENY, notices + [reasons.subagent_forbidden(subject)]
        )

    # ゲート。人間レビュー要のフェーズが終わっていて印が無い間、サブエージェントの
    # 起動と、例外の 3 本以外のシェル実行を止める（REQ-TKT-15）。ルールより先に見る。
    if conf.tickets_enabled and payload.tool_name in phase.GATED_TOOLS:
        parent = phase.parent_for_cwd(root, conf, payload.cwd)
        closed = phase.gate(root, conf, parent.ticket) if parent is not None else None
        exempt = payload.tool_name == "Bash" and phase.exempt(subject, record.degraded)
        if closed is not None and not exempt:
            record.code, record.rules = phase.CODE_GATE, [reasons.TICKET_RULE]
            reason = phase.gate_reason(closed, payload.tool_name)
            return refuse(stdout, mode, record, rules.DENY, notices + [reason])

    # 作業ツリーの切り元と承認済みチケットの `project:` の食い違いは、ルールより先に見る。
    # 範囲の宣言ではなく配線の誤りなので、ルールが allow と言っていても通さない。
    if conf.tickets_enabled and target is not None and payload.tool_name in SCOPE_TOOLS:
        mismatch = project_mismatch(conf, root, target, record.subject)
        if mismatch:
            record.code, record.rules = reasons.CODE_TICKET_PROJECT, [reasons.TICKET_RULE]
            return refuse(stdout, mode, record, rules.DENY, notices + [mismatch])

    # 強いタイプから順に見て、最初に当たったところで止める。deny に当たった
    # 呼び出しについて ask のタイプを調べる意味は無いし、調べれば「拒否だが
    # 確認もしろ」という読めない結論に届く道ができる。
    verdict, group = "", []
    for name in rules.SECTIONS:
        if name == rules.ALLOW and record.degraded:
            # 読み切れなかったコマンドに allow は当てない。当てる先は
            # 実行される部分ではなく生の文字列なので、そこで許可を出すのは
            # 「読めなかった文字列にそう書いてあった」を根拠に通すことになる。
            # deny と ask は当てたままにする。生の文字列に当たりすぎるぶんは
            # 厳しい側へ外れるだけで、そのことは文面が断る。
            break
        for rule in rule_set.section(name):
            if time.monotonic() > deadline:
                stderr.write("ccnavi: 判定を終える前に期限に達した\n")
                record.decision, record.reason = audit.SKIP, audit.REASON_DEADLINE_EXCEEDED
                return modes.fail_closed(mode)
            if rule.matches(payload.tool_name, subject):
                group.append(rule)
        if group:
            verdict = name
            break

    record.rules = [rule.id or f"({verdict})" for rule in group]

    # チケットはルールが何も言わなかったときだけ見る。ルールのほうが強い。
    # 順番を逆にすると、ルールが許した場所をチケットが閉じられることになり、
    # 人が書いた宣言よりエージェントが書いた宣言のほうが強くなる。
    ticket_reason = ""
    if not verdict:
        verdict, ticket_reason = ticket_verdict(conf, root, payload.tool_name, record.subject)
        if ticket_reason:
            record.rules = [reasons.TICKET_RULE]

    # 当たったルールがモデルへ渡す文。通す・聞く・止めるのどれでも、判定とは
    # 別の経路（additionalContext）で届く。
    context = ctxfile.for_rules(
        stderr, conf.state, payload, group, ctxfile.bases(conf, root, target)
    )
    # このセッションがまだ知らない承認（人がボードで承認して置かれた承認済みチケット）は、
    # 判定がどれでも 1 度だけ添える。応答は 1 つの JSON なので、ルールの文と
    # 同じ経路（additionalContext）に合流させる。
    told = approval.news(stderr, conf, payload.session_id, payload.agent_id)
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

    if verdict == rules.DENY and group:
        # 当たった理由はまとめて 1 回で返す。1 つずつ返すと、エージェントも
        # 1 つずつ直すことになり、そのたびに往復が 1 回増える。
        texts = [
            reasons.reason_for(rule, payload.tool_name, subject, source, record.degraded)
            for rule in group
        ]
        record.code = reasons.code_for(payload.tool_name, record.degraded)
        if any(rule.id == phase.TICKET_APPROVAL_RULE_ID for rule in group):
            record.code = phase.CODE_TICKET_APPROVAL
    elif verdict == rules.DENY:
        # チケットの範囲の外。ルールが 1 件も当たっていないので group は空。
        texts = [ticket_reason]
        record.code = reasons.CODE_TICKET_SCOPE
    elif verdict == rules.ASK and group:
        texts = [
            reasons.reason_for(rule, payload.tool_name, subject, source, record.degraded)
            for rule in group
        ]
        record.code = reasons.CODE_RULE_ASK
    elif verdict == rules.ASK:
        # チケットが ask と書いた場所。人が 1 度見る場所として宣言されている。
        texts = [ticket_reason]
        record.code = reasons.CODE_TICKET_ASK
    else:
        # どのタイプも言及しなかった。ccnavi はこの呼び出しの判定を持たない。
        # 結末は Claude Code の権限モードが決める。
        record.code = reasons.CODE_UNCERTAIN if record.degraded else reasons.CODE_UNDECLARED
        verdict = undeclared_verdict(payload.permission_mode, record.degraded)
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
        # 起動には対象の文字列が無い。ゲートが止める対象なので、見出しを subject に
        # して判定に入れる。
        return payload.field_value(field) or payload.field_value("prompt") or "(agent)"
    value = payload.field_value(field)
    if tool == "NotebookEdit":
        value = value or payload.field_value("file_path")
    if tool in SEARCH_TOOLS:
        value = value or "."
    if tool in PATH_TOOLS:
        return full_path(value, payload.cwd)
    return value


def full_path(path: str, cwd: str) -> str:
    """ファイルのパスを、行き着く先が 1 つに決まる綴りに直す。

    来たままの文字列に当てると、同じ場所を別の綴りで書くだけでルールを外せる。
    相対パスは呼び出し側の作業ディレクトリ次第で意味が変わるし、`..` を挟めば
    `secrets/` を通らない綴りで `secrets/` の中に届く。シンボリックリンクなら
    名前を 1 つ増やすだけで済む。守る対象は名前ではなく場所なので、
    場所まで解いてから当てる。

    解けなかったときも、絶対パスにして `..` を畳むところまではやる。
    まだ存在しないファイルへの書き込みがこれにあたる。
    """
    if not path:
        return ""
    base = cwd or os.getcwd()
    joined = os.path.join(base, os.path.expanduser(path))
    try:
        return os.path.realpath(joined)
    except OSError:
        return os.path.normpath(os.path.abspath(joined))


def screen(tool: str, subject: str, record: audit.Record) -> str:
    """シェルのコマンドを、実際に実行される部分まで絞る。読み切れなかったときは
    record にそう書き残す。

    ルールはコマンドについて書かれたものであって、文字列についてではない。
    git push を引用した文書は push ではないし、そこで拒否を返すことは、
    やっていないことをやったと読み手に告げることになる。

    読み切れないコマンドは生の文字列に落とす。これは以前の挙動そのものなので、
    今まで捕まえていたものが抜けることはない。変わるのは、返す拒否が
    どちらの拒否なのかを名乗らなければならない点。
    """
    if tool != "Bash":
        return subject
    reading = shellread.read(subject)
    if reading.degraded:
        record.degraded = reading.reason
        return subject
    return reading.text


def project_mismatch(conf: settings.Settings, root: str, t: tree.Tree, full: str) -> str:
    """作業ツリーの切り元と、そこに結び付く承認済みチケットの `project:` が違えば、その理由の文。

    範囲の宣言ではなく配線の誤りなので、ルールより先に見る（REQ-MLT-12）。ルールが
    allow と言っていても通さない。子は親から継ぐ。判定はエージェントの申告を見ない。
    行き先のツリーが誰のものかは、そのツリーの `.git` が指す先で決まっている。
    """
    if t.is_main:
        return ""
    copies, _ = approval.copies(conf.approved)
    index = approval.by_id(copies)
    ticket = index.get(t.name)
    if ticket is None:
        return ""
    parent = index.get(ticket.parent) if ticket.is_child else None
    owner = parent.project if parent is not None else ticket.project
    if owner == t.project:
        return ""
    source = approval.copy_path(conf.approved, t.name)
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


def ticket_verdict(
    conf: settings.Settings,
    root: str,
    tool: str,
    full: str,
) -> tuple[str, str]:
    """チケットが承認された範囲について何を言うかを返す。判定と、その理由の文。

    鍵はファイルの行き先。解いた先が `.claude/worktrees/<名前>/` の中なら、その名前と
    同じ識別子の承認済みチケットで判定する。main の直下ならチケットは無く、ルールだけで判定する。
    呼び出し元の cwd も agent_id も使わない（REQ-TKT-01）。

    範囲の中は allow。ルールが何も言っていない場所で、チケットだけが
    「ここで作業する」と宣言しているので、そこは聞かずに通す。範囲の外は deny。
    チケットが境界を明示している以上、外に出たことは「宣言に反した」になる。
    子は親と合わせて厳しい側が勝つ。

    チケット自身の提案ファイルについては何も言わない。承認された範囲の外に
    あるのが普通で、そこを deny にすると、いちど承認した範囲から出る道が無くなる。
    """
    if not conf.tickets_enabled or tool not in SCOPE_TOOLS or not full:
        return "", ""
    t = tree.tree_of(root, full, conf.projects)
    if t is None or t.is_main:
        return "", ""
    copies, _ = approval.copies(conf.approved)
    index = approval.by_id(copies)
    # 区別しない機械では綴りの違いを許す。SubagentStart / SubagentStop / 実行後の監視と
    # 同じ引き方。ここだけ厳密に引くと、`I0001-01` と切った作業ツリーは案内では
    # 「効いている」と言われながら判定では権限モード任せに落ちる（敵対的レビューで実測）。
    ticket = tree.lookup(index, t.name)
    if ticket is None or ticket.is_own_file(full):
        return "", ""
    rel = tree.relative(t, full)
    verdict = ticket.decide(rel)
    parent = index.get(ticket.parent) if ticket.is_child else None
    if parent is not None:
        verdict = ticket_mod.combine(verdict, parent.decide(rel))
    if verdict == rules.ALLOW:
        return rules.ALLOW, ""

    area = ", ".join(ticket.paths(rules.ALLOW) + ticket.paths(rules.ASK)) or "(空)"
    source = approval.copy_path(conf.approved, t.name)
    head = [
        f"subject: {full}",
        f"ticket: {ticket.ticket} ({ticket.title}), approved {ticket.approved_at}, "
        f"worktree {t.name}",
        f"scope: {area}",
    ]
    if verdict == rules.ASK:
        return rules.ASK, "\n".join(
            [
                f"[ccnavi] {reasons.CODE_TICKET_ASK} (source: {source})",
                *head,
                "This path is one the ticket marks `ask`: the user looks at each write here. "
                "Say what you are changing and why.",
            ]
        )
    return rules.DENY, "\n".join(
        [
            f"[ccnavi] {reasons.CODE_TICKET_SCOPE} (source: {source})",
            *head,
            "This path is outside the work area the ticket for this worktree declares. Do the "
            "work inside that area, or, if the task genuinely needs this path, tell the parent "
            "so it can propose a ticket that covers it and ask the user to approve it (from the "
            "ccnavi board in VS Code, or 'ccnavi --approve' in a terminal). "
            "Editing a proposal alone changes nothing.",
        ]
    )


def undeclared_verdict(permission_mode: str, degraded: str) -> str:
    """どのタイプも言及しなかった呼び出しを、権限モードごとにどう扱うか。

    渡すのは「ルールが言及していない」ときだけ。読み切れなかったコマンド
    （degraded）は渡さない。ccnavi が読めなかったという事実は判定の結果に
    現れないので、渡すと「判断材料が足りない」ことが誰にも伝わらないまま
    モードの既定に落ちる。読めなかったことを言えるのはここだけ。

    知らないモードは ask に倒す。名前が 1 つ増えたときに、それが素通りではなく
    確認になるように。設定漏れがガードの消失にならない側へ既定を置く。
    """
    if permission_mode in PERMISSION_NO_JUDGE:
        return rules.DENY
    if not degraded and permission_mode in PERMISSION_JUDGED:
        return HANDOVER
    return rules.ASK
