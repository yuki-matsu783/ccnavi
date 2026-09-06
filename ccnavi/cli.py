"""標準入出力とコマンドラインを 1 つの判定に繋ぐ。

run は終了コードを返す。自分で終了しないので、道具ぜんぶを別プロセス無しで
テストから動かせる。
"""

from __future__ import annotations

import argparse
import os
import time
from typing import TextIO

from . import approval, audit, builtin, diagnose, hookio, lint, post, rules, settings, shellread
from . import ticket as ticket_mod

# 1 回の起動に張る期限。呼び手は長く走った hook を打ち切って出力を捨てるので、
# それより先に自前の判定へ着地することが、遅い判定が黙った許可に化けるのを防ぐ。
DEADLINE_SECONDS = 3.0

# 終了コード。
EXIT_OK = 0  # 判定を書いた、あるいは言うことが無かった
EXIT_ERROR = 1  # 使い方の誤り、または読めない設定
EXIT_BLOCK = 2  # 判定を書けなかったので拒否側に倒す

# モードは判定をどう扱うかを決めるだけで、どう判定するかは決めない。
# 判定を続ける 2 つのモードは同じ経路を通るので、dry-run が報告するものが
# enable なら実際に起きたことと一致する。
#
# 名前はガードそのものの状態を言う。判定に deny と ask の 2 つがある以上、
# 名前が「止める」だけを言うと、確認で済む回に嘘をつくことになる。
# 弱いほうから強いほうへ並ぶのは lint の深刻度と同じ。
MODE_DISABLE = "disable"  # 判定しない
MODE_DRY_RUN = "dry-run"  # 判定して報告するが、呼び出しには手を出さない
MODE_ENABLE = "enable"  # 判定を呼び出しに適用する

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

# 判定を権限モードへ渡したことを表す内部の値。区画名（rules.ALLOW など）と
# 同じ変数に入るので、ルールファイルには現れない綴りにしてある。
HANDOVER = "(handover)"

# 返す理由に載せる理由コード。ccnavi.md 付録 B の体系から、今のビルドが実際に
# 下せる判定に対応するものだけを借りている。
#
# 付録 B のコードは「どの検査がその根拠を作ったか」の名前であって、ルール 1 件を
# 指す名前ではない。今のビルドが持つ検査は 1 つ（外から注入したルール集合を
# 正規化済みの対象に当てる）で、当てる先がコマンドかパスかで 2 つに割れる。
# だからコードはその割れ方に対応させ、どのルールだったかは出所が名指しする。
# ルール 1 件ごとにコードを持たせれば付録 B の粒度（ヒアドキュメントなら
# DENY_REDIRECT、DB 破壊なら DENY_DB_DESTRUCTIVE）に届くが、それはルール
# ファイルに欄を 1 つ足すことなので書式の版が上がる。この要件が求めるのは
# 「理由コードを含むこと」までなので、版を上げずに済む側を採る。
CODE_COMMAND = "DENY_COMMAND_PATTERN"  # Bash の実行される部分に当たった
CODE_PATH = "DENY_PATH"  # ファイルのパスに当たった
# ルールが ask と書いてある場所に当たった。「ここは毎回人間が見るべき」という
# 意図的な確認ポイントで、繰り返し出ること自体に値打ちがある。
CODE_RULE_ASK = "RULE_ASK"
# ルールがどこも言及していない。危険の表明ではない。ccnavi はこの呼び出しに
# ついて何も言えず、扱いを Claude Code の権限モードに委ねたことを言うだけ。
# 委ねた先が判断できないモードなら許可としないので、同じコードが渡した回と
# 断った回の両方に付く。どちらだったかは記録の decision 側が持つ。
CODE_UNDECLARED = "UNDECLARED"
# 実行後の監視が出すコードは post.py にある。あちらは判定ではなく、
# すでに起きたことの報告なので、同じ表に混ぜていない。
# 読み切れないコマンドの根拠は、宣言された禁止に当たったことではなく、
# 対象を確定できなかったこと。こちらは権限モードに委ねない。読めなかった
# という事実は判定の結果に現れず、委ねた先には伝わらないので、言えるのが
# ここしかない。生の文字列が deny に当たったときだけは拒否になり、
# そのときも「読めないまま当てた」ことを文面が断る。
CODE_UNCERTAIN = "PARSE_UNCERTAIN"
# 承認されたチケットの作業範囲の外に書こうとした。ルールに当たったのではなく、
# 宣言された範囲に入っていないことが根拠なので、コードを分けている。
# 受け取った側の次の一手が違う。ルールなら別の手段を探すことになるが、
# こちらは範囲の中で済ませるか、チケットを書き直して承認を求めることになる。
CODE_TICKET_SCOPE = "DENY_TICKET_SCOPE"

# 範囲外で止めたことを記録に残すときのルール名。対応するルールがルールファイルに
# 無いので、括弧付きにして、ファイルの中を探しても見つからないことを見た目で示す。
TICKET_RULE = "(ticket-scope)"

# 範囲の中かどうかを問うツール。作業ツリーを書き換えるものだけ。
# Bash は入れていない。コマンド文字列に現れるパスは追えないので、ここで
# 当てると当たったり当たらなかったりする判定になる。シェル経由の書き込みは
# 実行後の監視が作業ツリーの実物を見て捕まえる。
SCOPE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")

# 理由に載せる対象の長さの上限。対象はエージェントが今書いたものなので、
# ここでは同じものを指せれば足りる。ヒアドキュメントは 1 ファイル分を運べるので、
# 全文を載せると理由の本体が下へ流れて読まれなくなる。
SUBJECT_LIMIT = 200

USAGE = """ccnavi guards agent tool calls and guides the agent to a safer alternative.

It is a hook command, not something to run by hand: it reads one hook payload
as JSON on stdin and writes its response to stdout.

Register it on PreToolUse to judge calls before they run, and on PostToolUse to
watch the working tree for protected files that changed anyway. Exercise it with

    echo '{"hook_event_name":"PreToolUse","tool_name":"Bash",
           "tool_input":{"command":"git push"}}' | ccnavi

To check the rules file and the settings without making a decision, run

    ccnavi --lint

It reads no payload, reports anything that could disable the guard as an error
or a warning, and exits non-zero when it reports an error.

To review the current ticket and approve the work area it declares, run

    ccnavi --approve

It shows what the ticket makes writable, how that differs from the last approved
ticket, and a risk score, then appends the approval to the ledger. Only the
ledger is consulted when judging calls, so editing the ticket never widens the
area on its own.
"""


def run(stdin: TextIO, stdout: TextIO, stderr: TextIO, argv: list[str]) -> int:
    """1 回の起動を処理する。"""
    parser = argparse.ArgumentParser(prog="ccnavi", add_help=False)
    parser.add_argument("--root", default=None)
    parser.add_argument("--mode", default="")
    parser.add_argument("--rules", default="")
    parser.add_argument("--log", default=None)
    parser.add_argument("--state", default=None)
    parser.add_argument("--restore", default="")
    parser.add_argument("--lint", action="store_true")
    parser.add_argument("--approve", action="store_true")
    parser.add_argument("--test", nargs=2, metavar=("TOOL", "SUBJECT"), default=None)
    parser.add_argument("--explain", action="store_true")
    parser.add_argument("--ticket", default="")
    parser.add_argument("--ledger", default=None)
    parser.add_argument("-h", "--help", action="store_true")
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return EXIT_ERROR
    if args.help:
        stderr.write(USAGE)
        return EXIT_ERROR

    root = args.root if args.root is not None else default_root()
    conf, problems = settings.load(root)

    # フラグは何よりも強い。診断のための実行が、プロジェクト全体で共有している
    # ファイルに触らずに別の場所を指せるように。
    # 空文字も受ける。「記録しない」「控えを持たない」を言えないと、診断の
    # ための 1 回が、走っているセッションの記録と控えに必ず混ざる。
    if args.log is not None:
        conf.log = args.log
    if args.rules:
        conf.rules = args.rules
    if args.state is not None:
        conf.state = args.state
    if args.ticket:
        conf.ticket = args.ticket
    if args.ledger is not None:
        conf.ledger = args.ledger

    # 検証だけを行う経路。payload を読まないので、判定に入る前にここで分かれる。
    # 苦情の扱いが逆になるのが分ける理由で、判定にとっては読み飛ばした設定の
    # 報告でしかないものが、検証にとっては結論そのものになる。
    if args.lint:
        return lint.report(stdout, root, conf, problems, args.mode, args.restore)

    # 診断の経路。どちらも payload を読まず、判定を実行にも記録にも繋げない。
    # 人が端末から叩いて「このルールは何に当たるのか」を確かめるための場所で、
    # 判定そのものは実運用と同じ関数を通る（REQ-DIA-03）。
    if args.test is not None:
        return diagnose.test(stdout, stderr, conf, root, args.test[0], args.test[1])
    if args.explain:
        return diagnose.explain(stdout, stderr, conf, root)

    # 承認の経路。人が端末から叩くもので、payload を読まないのでここで分かれる。
    # 判定を 1 度も通らないのも分ける理由で、承認はツール呼び出しについての
    # 判断ではなく、これから効く範囲についての合意になる。
    if args.approve:
        if not conf.ledger:
            stderr.write("ccnavi: 承認台帳の置き場が空。--ledger で指す先が要る\n")
            return EXIT_ERROR
        rule_set, _ = load_rules(stderr, conf.rules, audit.Record())
        approved = approval.approve(stdin, stdout, stderr, conf.ticket, conf.ledger, rule_set, root)
        return EXIT_OK if approved == 0 else EXIT_ERROR

    for problem in problems:
        stderr.write(f"ccnavi: {problem}\n")

    mode = resolve_mode(stderr, args.mode, conf)
    conf.restore = post.resolve_restore(stderr, args.restore, conf.restore)
    log = audit.Log(conf.log)
    deadline = time.monotonic() + DEADLINE_SECONDS

    try:
        payload = hookio.decode(stdin)
    except hookio.NoPayload:
        # hook ではなく端末の前の人。何も判定していないので何も記録しない。
        stderr.write(USAGE)
        return EXIT_ERROR
    except hookio.Unusable as exc:
        stderr.write(f"ccnavi: {exc}\n")
        _record(
            stderr,
            log,
            audit.Record(
                mode=mode,
                decision=audit.SKIP,
                reason=audit.REASON_PAYLOAD_UNUSABLE,
            ),
        )
        return fail_closed(mode)

    record = audit.Record(
        mode=mode,
        permission_mode=payload.permission_mode,
        event=payload.event,
        tool=payload.tool_name,
        subject=subject_of(payload),
        session=payload.session_id,
    )

    code = decide(stdout, stderr, mode, conf, root, payload, record, deadline)
    _record(stderr, log, record)
    return code


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
    if mode == MODE_DISABLE:
        record.decision, record.reason = audit.SKIP, audit.REASON_MODE_DISABLED
        return EXIT_OK
    if payload.event == hookio.PRE_TOOL_USE:
        return decide_before(stdout, stderr, mode, conf, root, payload, record, deadline)
    if payload.event == hookio.POST_TOOL_USE:
        return decide_after(stdout, stderr, mode, conf, root, payload, record)
    # 判定を持たないイベントは誤りではない。想定していない登録が
    # 作業を止めてはいけない。
    record.decision, record.reason = audit.SKIP, audit.REASON_EVENT_NOT_CHECKED
    return EXIT_OK


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
    if not record.subject:
        record.decision, record.reason = audit.SKIP, audit.REASON_NO_SUBJECT
        return EXIT_OK

    rule_set, source = load_rules(stderr, conf.rules, record)
    subject = screen(payload.tool_name, record.subject, record)

    if not subject:
        # コマンドは在るが、実行される部分が残らなかった。コメントだけの行が
        # これにあたる。ルールを当てる先が無いので、どの区画にも当たらず
        # 権限モードへ渡る先になるが、何も走らないものについて誰かの判断を
        # 求める意味は無い。
        record.decision, record.reason = audit.SKIP, audit.REASON_NOTHING_TO_RUN
        return EXIT_OK

    # 強い区画から順に見て、最初に当たったところで止める。deny に当たった
    # 呼び出しについて ask の区画を調べる意味は無いし、調べれば「拒否だが
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
                return fail_closed(mode)
            if rule.matches(payload.tool_name, subject):
                group.append(rule)
        if group:
            verdict = name
            break

    record.rules = [rule.id or f"({verdict})" for rule in group]

    # チケットはルールが何も言わなかったときだけ見る。ルールのほうが強い。
    # 順番を逆にすると、ルールが許した場所をチケットが閉じられることになり、
    # 人が書いた宣言よりエージェントが書いた宣言のほうが強くなる。
    approved, notice = ticket_scope(conf)
    ticket_reason = ""
    if not verdict:
        verdict, ticket_reason = ticket_verdict(
            conf, root, approved, payload.tool_name, record.subject
        )
        if ticket_reason:
            record.rules = [TICKET_RULE]

    # 通知は件ごとではなく先頭に 1 回。件ごとの断りはその件の中で閉じるが、
    # 通知は応答全体に掛かる事情で、繰り返すと理由の本体が下へ流れて読まれなくなる。
    notices = [
        text for text in (fallen_back(conf.rules) if record.fallback else "", notice) if text
    ]

    if verdict == rules.ALLOW:
        record.decision, record.enforced = audit.ALLOW, True
        if notices:
            # 通した回にも言う。ガードが今なにを見ていないのかを黙っていると、
            # 誰も知らないまま作業が進む。呼び出しごとに出るのでうるさいが、
            # うるさいのが正しい。壊れた設定と未承認のチケットはどちらも
            # 短命であるべきで、黙って居座られるより気づかれたほうがよい。
            hookio.write_context(stdout, hookio.PRE_TOOL_USE, "\n\n".join(notices))
        return EXIT_OK

    if verdict == rules.DENY and group:
        # 当たった理由はまとめて 1 回で返す。1 つずつ返すと、エージェントも
        # 1 つずつ直すことになり、そのたびに往復が 1 回増える。
        reasons = [
            reason_for(rule, payload.tool_name, subject, source, record.degraded) for rule in group
        ]
        record.code = code_for(payload.tool_name, record.degraded)
    elif verdict == rules.DENY:
        # チケットの範囲の外。ルールが 1 件も当たっていないので group は空。
        reasons = [ticket_reason]
        record.code = CODE_TICKET_SCOPE
    elif verdict == rules.ASK:
        reasons = [
            reason_for(rule, payload.tool_name, subject, source, record.degraded) for rule in group
        ]
        record.code = CODE_RULE_ASK
    else:
        # どの区画も言及しなかった。ccnavi はこの呼び出しの判定を持たない。
        # 結末は Claude Code の権限モードが決める。
        record.code = CODE_UNCERTAIN if record.degraded else CODE_UNDECLARED
        verdict = undeclared_verdict(payload.permission_mode, record.degraded)
        reasons = [
            undeclared(
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
        if notices:
            hookio.write_context(stdout, hookio.PRE_TOOL_USE, "\n\n".join(notices))
        return EXIT_OK

    # 空行で割るのは、1 件ずつが閉じた文であることを見た目でも保つため。
    reason = "\n\n".join(notices + reasons)
    decision = audit.DENY if verdict == rules.DENY else audit.ASK

    if mode == MODE_DRY_RUN:
        record.decision, record.enforced = decision, False
        would = "stopped" if verdict == rules.DENY else "asked the user about"
        hookio.write_context(
            stdout,
            hookio.PRE_TOOL_USE,
            f"[ccnavi dry-run] {MODE_ENABLE} would have {would} this call:\n" + reason,
        )
        return EXIT_OK

    record.decision, record.enforced = decision, True
    hookio.write_verdict(stdout, hookio.DENY if verdict == rules.DENY else hookio.ASK, reason)
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
    rule_set, source = load_rules(stderr, conf.rules, record)
    # 既定に落ちたことをこのイベントでは言わない。実行前の判定が呼び出しごとに
    # 言っているので、同じターンで 2 度届く。届く数が増えると、どちらも
    # 読まれなくなる。記録には fallback が残る。
    # 範囲は実行前の判定と同じ経路で解く。断りの文はここでは出さない。
    # 実行前が呼び出しごとに同じことを言っているので、同じターンで 2 度届く。
    # 届く数が増えると、どちらも読まれなくなる。
    approved, _ = ticket_scope(conf)
    scope = (
        post.ScopeGuard(
            root=root,
            ledger=conf.ledger,
            ticket=conf.ticket,
            name=approved.ticket,
            write=approved.write,
        )
        if approved is not None
        else None
    )

    text = post.check(
        stderr,
        enforcing=mode == MODE_ENABLE,
        restore=conf.restore,
        state_dir=conf.state,
        mine=(conf.state, conf.log),
        rule_set=rule_set,
        source=source,
        scope=scope,
        payload=payload,
        record=record,
        root=root,
    )
    if not text:
        return EXIT_OK

    # 差し戻すのは、直前の実行が汚したと言える分があるときだけ。セッションが
    # 始まる前から在った変更は言うが差し戻さない。差し戻しは「あなたが直せ」で、
    # 誰が書いたか分からないものにそれを言うと、他人の書きかけを消しにいく。
    pushback = record.decision == audit.DENY

    if pushback and mode == MODE_ENABLE:
        # このイベントで差し戻す経路は exit 2 と標準エラーだけ。ツールは
        # すでに走っているので取り消せず、渡せるのは「次に何をするか」になる。
        stderr.write(text + "\n")
        return EXIT_BLOCK

    if pushback:
        # warn は差し戻さない。呼び出しを止めないことがこのモードの約束で、
        # 差し戻しは止めはしないが次の一手を変えさせる。変えさせないまま
        # 数えるためのモードなので、届け先を報告の側にする。
        text = f"[ccnavi dry-run] {MODE_ENABLE} would have sent this back as a correction:\n" + text
    hookio.write_context(stdout, hookio.POST_TOOL_USE, text)
    return EXIT_OK


def load_rules(stderr: TextIO, rules_path: str, record: audit.Record) -> tuple[rules.RuleSet, str]:
    """ルール集合と、それがどこから来たかを返す。

    読めなければ組み込みの既定に落ちる。「設定が読めない」は「判断できない」
    ではなく「設定が壊れている」。拒否側へ倒すと、壊れたファイルを直すための
    呼び出しまで止まって回復できなくなる。既定モードが block なので、
    ファイルを置く前に hook を登録しただけでセッションが死ぬ（REQ-PRE-06）。

    出所は、いま当てているルールがどこから来たか。既定に落ちているなら
    読めなかったファイルではない。そのファイルを名乗ると、見に行った人が
    当たったルールを見つけられない。
    """
    try:
        rule_set, problems = rules.load(rules_path)
    except (OSError, ValueError) as exc:
        stderr.write(f"ccnavi: ルールを読めない: {exc}\n")
        rule_set, problems = builtin.load()
        record.fallback = builtin.FALLBACK
        record.detail = rules_path
    for problem in problems:
        stderr.write(f"ccnavi: {problem}\n")
    return rule_set, builtin.SOURCE if record.fallback else rules_path


def fail_closed(mode: str) -> int:
    """「判定に達せなかった」ときの終了コード。

    enable は呼び出しを止める。達せなかった判定が許可に化けてはならないから。
    dry-run は通す。呼び出しに手を出さないことがそのモードの約束なので、
    自分の失敗で作業を止めるようでは意味がない。
    """
    return EXIT_OK if mode == MODE_DRY_RUN else EXIT_BLOCK


def _record(stderr: TextIO, log: audit.Log, record: audit.Record) -> None:
    """1 行を書く。書けなかったことは報告して捨てる。
    それがツールの実行可否を変えられてはいけない。"""
    try:
        log.write(record)
    except OSError as exc:
        stderr.write(f"ccnavi: 記録を書けない: {exc}\n")


def subject_of(payload: hookio.Input) -> str:
    """このツールでルールを当てる欄を選ぶ。"""
    if payload.tool_name == "Bash":
        return payload.field_value("command")
    if payload.tool_name in ("Read", "Write", "Edit", "MultiEdit", "NotebookEdit"):
        return full_path(payload.field_value("file_path"), payload.cwd)
    return ""


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


def fallen_back(rules_path: str) -> str:
    """ルールファイルを読めずに組み込みの既定へ落ちたことを伝える文。

    通した回にも返す。ここを黙ると、ガードが立っているように見えて実際には
    プロジェクトのルールを 1 件も見ていない、という状態が続く。それは
    ガードが止まっていることより悪い。止まっていれば誰かが気づくから。

    直し方に Write / Edit を名指しするのは、既定の側がシェルからこの場所への
    書き込みを止めているため。止めた先に道が無いと、拒否は行き止まりになる。
    """
    return (
        "[ccnavi] the rule file at "
        + rules_path
        + " could not be read, so ccnavi is judging with its built-in defaults. "
        "The project's own rules are not in force right now. Repair that file with "
        "the Write or Edit tool; while the defaults are in force the shell cannot "
        "write to it, so that the path that breaks it and the path that fixes it "
        "are not the same one."
    )


def ticket_scope(conf: settings.Settings) -> tuple[approval.Approval | None, str]:
    """いま効いている作業範囲と、呼び出しごとに言うべき断りを返す。

    判定に使うのは台帳に載った範囲だけ。作業ツリーのチケットは、承認を求める
    ための提案でしかなく、書き換えても効いている範囲は変わらない。変わらない
    かわりに食い違いが起きるので、食い違いはここで見つけて毎回言う。

    台帳が読めないときと未承認のときは、範囲の制限を掛けない。掛けようにも
    「どこが範囲か」を知らないし、全部拒否へ倒すと、チケットを直す手も
    台帳を直す手も同時に止まる。倒れた先に回復の道を残すのは、ルールを
    読めないときに組み込みの既定へ落ちるのと同じ判断（REQ-PRE-06）。
    """
    if not conf.ledger:
        return None, ""

    approved, unreadable = approval.current(conf.ledger)
    if unreadable:
        return None, (
            f"[ccnavi] {unreadable} ({conf.ledger}). "
            "No ticket scope is in force right now. Ask the user to repair or remove that file."
        )

    proposed, _ = ticket_mod.load(conf.ticket) if conf.ticket else (None, [])

    if approved is None:
        if proposed is None:
            return None, ""
        return None, (
            f"[ccnavi] the ticket {proposed.ticket} at {conf.ticket} has not been approved, "
            "so no ticket scope is in force. Ask the user to run 'ccnavi --approve'. "
            "Until they do, nothing is limiting which files you may write."
        )

    if proposed is None:
        return approved, (
            f"[ccnavi] no readable ticket at {conf.ticket}, but the approved scope of "
            f"{approved.ticket} is still in force: {', '.join(approved.write)}. "
            "Ask the user if you believe this ticket is finished."
        )

    if proposed.digest() != approved.digest:
        return approved, (
            f"[ccnavi] the ticket at {conf.ticket} no longer matches what was approved "
            f"({approved.ticket}, approved {approved.approved_at}). Judgement still uses the "
            f"approved scope: {', '.join(approved.write)}. Editing the ticket does not widen it; "
            "ask the user to run 'ccnavi --approve' to approve the new one."
        )

    return approved, ""


def ticket_verdict(
    conf: settings.Settings,
    root: str,
    approved: approval.Approval | None,
    tool: str,
    full: str,
) -> tuple[str, str]:
    """チケットが承認された範囲について何を言うかを返す。判定と、その理由の文。

    範囲の中は allow。ルールが何も言っていない場所で、チケットだけが
    「ここで作業する」と宣言しているので、そこは聞かずに通す。これが無いと、
    宣言した作業範囲の中まで権限モード任せに落ちることになり、
    範囲を宣言する意味が「ccnavi が何も言わない場所が増えるだけ」になる。

    範囲の外は deny。チケットが境界を明示している以上、外に出たことは
    「誰も言及していない」ではなく「宣言に反した」になる。

    チケットのファイル自身については何も言わない。承認された範囲の外に
    あるのが普通で、そこを deny にすると、いちど承認した範囲から出る道が
    無くなる。allow にもしないのは、範囲外への書き込みであることに変わりは
    ないから。ルールが言及していない扱いになるので、そこから先は
    Claude Code の権限モードが決める。
    """
    if approved is None or tool not in SCOPE_TOOLS or not full:
        return "", ""
    if conf.ticket and os.path.normpath(full) == os.path.realpath(conf.ticket):
        return "", ""
    if ticket_mod.inside(root, approved.write, full):
        return rules.ALLOW, ""
    scope = ", ".join(f"{p}/" for p in approved.write) or "(空)"
    return rules.DENY, "\n".join(
        [
            f"[ccnavi] {CODE_TICKET_SCOPE} (source: {conf.ledger})",
            f"subject: {full}",
            f"ticket: {approved.ticket} ({approved.title}), approved {approved.approved_at}",
            f"scope: {scope}",
            "This path is outside the work area the current ticket declares. Do the work inside "
            "that area, or, if the task genuinely needs this path, edit the ticket to add it and "
            "ask the user to run 'ccnavi --approve'. Editing the ticket alone changes nothing.",
        ]
    )


def code_for(tool: str, degraded: str) -> str:
    """拒否の根拠コード。対象がコマンドかパスかで割れる。"""
    if degraded:
        return CODE_UNCERTAIN
    return CODE_COMMAND if tool == "Bash" else CODE_PATH


def undeclared_verdict(permission_mode: str, degraded: str) -> str:
    """どの区画も言及しなかった呼び出しを、権限モードごとにどう扱うか。

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


def undeclared(tool: str, subject: str, rules_path: str, degraded: str, refused: bool) -> str:
    """どの区画も言及しなかった呼び出しに返す文。

    危険だとは言わない。言えないから権限モードに委ねている。根拠は設定の穴で
    あって呼び出しの中身ではないので、危険の表明として書くと、受け取った側は
    存在しない危険を探すことになる。

    直し方を書くのは、これが繰り返されるのが穴の側の問題だから。同じ場所で
    何度も止まるなら、答えるべきなのは呼び出しごとの是非ではなく
    「この場所を allow に書くかどうか」になる。

    refused は、確認できる者が居ないモードで許可としなかったことを示す。
    そのときは同じ根拠でも受け取った側の次の一手が違う。人に聞けるなら
    「言えば通るかもしれない」だが、聞けないなら「言っても通らない」ので、
    先に設定を直すか、人が居るセッションでやり直すしかない。
    """
    shown = " ".join(subject.split())
    if len(shown) > SUBJECT_LIMIT:
        shown = shown[:SUBJECT_LIMIT] + f"…(+{len(subject) - SUBJECT_LIMIT})"

    lines = [
        f"[ccnavi] {CODE_UNCERTAIN if degraded else CODE_UNDECLARED} (source: {rules_path})",
        f"subject: {shown}",
    ]
    if degraded:
        lines.append(unreadable(degraded))
        lines.append(
            "ccnavi is asking rather than deciding because it could not tell what this call "
            "would actually do. Say what the command is for, or rewrite it in a form that can "
            "be read: no heredoc, no string handed to something that runs it."
        )
    elif refused:
        lines.append(
            f"No rule in {rules_path} mentions this {'command' if tool == 'Bash' else 'path'}, "
            "and this session has no one to ask: its permission mode answers on its own or not "
            "at all. An undeclared call is not allowed here. Either do the work with something "
            f"the allow section of {rules_path} already covers, or tell the user what needs to "
            "be added there and let them decide."
        )
    else:
        lines.append(
            f"No rule in {rules_path} mentions this {'command' if tool == 'Bash' else 'path'}, "
            "so ccnavi has no verdict of its own and leaves the call to Claude Code's permission "
            "mode, which in this session asks the user. This is not a warning about the call "
            "itself. If this is ordinary work for this project, say so and ask the user to add "
            f"it to the allow section of {rules_path}; that is what stops the same question from "
            "coming back."
        )
    return "\n".join(lines)


def reason_for(rule: rules.Rule, tool: str, subject: str, rules_path: str, degraded: str) -> str:
    """当たったルール 1 件を、それだけで読んで成立する理由に組む。

    載せるのは 3 つ。何に当たったか（対象）、どういう筋の根拠か（理由コード）、
    それを言っているのはどの設定か（出所）。どれが欠けても、受け取った側は
    自分の呼び出しのどこが引っかかったのかを自分では辿れず、
    文面を信じるか無視するかの二択になる。

    件ごとに閉じた形にするのは、1 回の応答に複数の理由が入り、そのうちどれが
    利用者の目に入るかが決まらないため。「上に書いた事情が下の全部に掛かる」形は、
    1 件だけが切り出されて見えた瞬間に意味を失う。読めなかったという断りが
    件ごとに繰り返されるのはその代金で、繰り返しのほうが誤読より安い。
    """
    shown = subject
    if len(shown) > SUBJECT_LIMIT:
        shown = shown[:SUBJECT_LIMIT] + f"…(+{len(subject) - SUBJECT_LIMIT})"
    # 改行を含む対象は 1 行に畳む。理由の骨格が対象の中身で割られると、
    # どこまでが対象でどこからが言い分なのかが読めなくなる。
    shown = " ".join(shown.split())

    # コードはルールが置かれていた区画から決まる。拒否と確認で同じコードを
    # 返すと、受け取った側は「止まった」のか「聞かれている」のかを文面から
    # 推し量ることになる。
    code = CODE_RULE_ASK if rule.decision == rules.ASK else code_for(tool, degraded)
    # 出所は「どのファイルのどのルール」まで。ファイル名だけでは、同じ名前の
    # ルールファイルが複数ある構成で直しに行く先が決まらない。
    source = f"{rules_path}#{rule.id}" if rule.id else rules_path

    lines = [f"[ccnavi] {code} (source: {source})", f"subject: {shown}"]
    if degraded:
        lines.append(unreadable(degraded))
    lines.append(rule.message)
    return "\n".join(lines)


def unreadable(reason: str) -> str:
    """コマンドを読めないまま出した 1 件に添える断り。

    2 つの違う失敗に同じ文を使わせないために要る。「禁止されたコマンドを実行した」と
    「読めない文字列のどこかにその語がある」では次にやることが違うし、
    後者なのに前者を渡された読み手は、書いた覚えのないコマンドを探しに行く。
    """
    what = {
        shellread.REASON_UNTERMINATED: ("a quote or heredoc in this command never closes"),
        shellread.REASON_TAKEN_AS_CODE: (
            "this command hands a string to something that runs it as code"
        ),
    }.get(reason, "this command could not be read")
    return (
        "note: " + what + ", so this rule was matched against the raw text of the "
        "command instead of against what runs. If these words are only being "
        "written down and not run, nothing was deliberately forbidden: move the "
        "text into a file, or split the command up, and try again."
    )


def resolve_mode(stderr: TextIO, flag_value: str, conf: settings.Settings) -> str:
    """設定された値をモードにする。

    設定ファイルが決められるのは warn と block だけで、その編集は次のツール
    呼び出しから効く。disable を言えるのは環境だけ。設定ファイルは作業ツリーの中に
    あってエージェントが書けるので、そこに書かれた disable を効かせると、
    見張られている側が見張りを止められてしまう。

    フラグは両方より強い。テストが、たまたま走った環境に依存せずモードを
    固定できるように。

    どこにも値が無ければ enable。設定の欠落が、ガードの欠落にならないように。
    """
    source, value = settings.MODE_ENV, conf.mode
    if flag_value:
        source, value = "--mode", flag_value

    normalized = value.lower()

    if normalized == MODE_DRY_RUN:
        return MODE_DRY_RUN

    if normalized == MODE_DISABLE:
        # disable の経路は 1 本だけ。セッションを起動した人の環境から来て、
        # かつ作業ツリーの中の何もそれを求めていないとき。設定ファイルもフラグも
        # エージェントが書ける場所から来るし、そこでの編集は次のツール呼び出しから
        # 効くので、どちらの off を認めても、見張られている側が見張りを
        # 止められることになる。
        from_file = conf.mode_declared_in_file.lower()
        from_env = conf.mode_from_environment.lower()
        if not flag_value and from_env == MODE_DISABLE and from_file != MODE_DISABLE:
            return MODE_DISABLE
        stderr.write(
            f"ccnavi: {source}={MODE_DISABLE} is ignored because it comes from inside "
            f"the project; start the session with {settings.MODE_ENV}={MODE_DISABLE} "
            "in the environment instead\n"
        )
        return MODE_ENABLE

    if normalized in ("", MODE_ENABLE):
        return MODE_ENABLE

    # 解釈できない値も最も強いモードに着地するが、それを言うことに意味がある。
    # 名前を変えた設定や打ち間違いが、黙っていると意図した選択に見えてしまい、
    # 誰にも見えない理由でガードが締まることになる。
    stderr.write(
        f"ccnavi: {source}={value!r} is not a mode; using {MODE_ENABLE}. "
        f"Valid modes are {MODE_DISABLE}, {MODE_DRY_RUN} and {MODE_ENABLE}\n"
    )
    return MODE_ENABLE


def default_root() -> str:
    """プロジェクト根、つまり .claude を持つディレクトリを見つける。

    ここでは作業ディレクトリそのものに頼ってはいけない。hook は自分が走る
    ディレクトリを選べないから。代わりに上へ辿るので、プロジェクトの中の
    どこから起動しても同じ根に行き着くし、思わぬ場所で起動された hook でも
    プロジェクトの設定を読める。
    """
    # Claude Code はこれを渡してくるが、あることに依存してはいけない。
    from_env = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if from_env:
        return from_env
    found = _find_project_root()
    return found if found else "."


def _find_project_root() -> str:
    """作業ディレクトリからファイルシステムの根まで上って .claude を探す。
    バージョン管理が自分の根を見つけるのと同じやり方。"""
    try:
        directory = os.getcwd()
    except OSError:
        return ""
    while True:
        if os.path.isdir(os.path.join(directory, ".claude")):
            return directory
        parent = os.path.dirname(directory)
        if parent == directory:
            return ""
        directory = parent
