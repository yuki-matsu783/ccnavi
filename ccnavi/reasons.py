"""判定に添える文面と理由コード。

止めた・聞いた・渡した、のそれぞれについて、それだけで読んで成立する 1 件の文を
組む。判定の流れは judge にあり、ここは文面だけを持つ。文面は利用者とモデルが
読む成果物そのもので、判定の順序より直す頻度が高いので、別に置いて読み比べ
られるようにしてある。
"""

from __future__ import annotations

import os

from . import phase, rules, settings, shellread
from .modes import DRY_RUN

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

# チケットが `ask` と書いた場所。ルールの `ask` と同じく、人が 1 度見る場所。
CODE_TICKET_ASK = "TICKET_ASK"

# 作業ツリーの切り元と、チケットが承認されたプロジェクトが食い違っている。
CODE_TICKET_PROJECT = "DENY_TICKET_PROJECT_MISMATCH"

# 範囲外で止めたことを記録に残すときのルール名。対応するルールがルールファイルに
# 無いので、括弧付きにして、ファイルの中を探しても見つからないことを見た目で示す。
TICKET_RULE = "(ticket-scope)"

# 理由に載せる対象の長さの上限。対象はエージェントが今書いたものなので、
# ここでは同じものを指せれば足りる。ヒアドキュメントは 1 ファイル分を運べるので、
# 全文を載せると理由の本体が下へ流れて読まれなくなる。
SUBJECT_LIMIT = 200


def code_for(tool: str, degraded: str) -> str:
    """拒否の根拠コード。対象がコマンドかパスかで割れる。"""
    if degraded:
        return CODE_UNCERTAIN
    return CODE_COMMAND if tool == "Bash" else CODE_PATH


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


def undeclared(tool: str, subject: str, rules_path: str, degraded: str, refused: bool) -> str:
    """どのタイプも言及しなかった呼び出しに返す文。

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

    # コードはルールが置かれていたタイプから決まる。拒否と確認で同じコードを
    # 返すと、受け取った側は「止まった」のか「聞かれている」のかを文面から
    # 推し量ることになる。
    code = CODE_RULE_ASK if rule.decision == rules.ASK else code_for(tool, degraded)
    # 出所はルールの id で名乗る。プロジェクトのルールの id には `lib:git-push` の形で
    # プロジェクトの名前が付く（REQ-MLT-07）ので、id だけでどのファイルを見に行けばよいかが
    # 決まる。パスまで載せると、判定を試したときの一時ファイルのような読む値の無い綴りが
    # そのまま毎回モデルに届く。id を持たないルールだけ、代わりにファイルを名乗る。
    source = f"rule: {rule.id}" if rule.id else f"rules: {rules_path}"
    if rule.id == phase.TICKET_APPROVAL_RULE_ID:
        # 組み込み。ルールファイルには無いので、そこを探させない。
        code, source = (
            phase.CODE_TICKET_APPROVAL,
            f"builtin rule: {rule.id} ({settings.GUARD_TICKET_APPROVAL_ENV})",
        )

    lines = [f"[ccnavi] {code} ({source})", f"subject: {shown}"]
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


def subagent_forbidden(subject: str) -> str:
    shown = " ".join(subject.split())[:SUBJECT_LIMIT]
    return "\n".join(
        [
            f"[ccnavi] {phase.CODE_SUBAGENT}",
            f"subject: {shown}",
            "チケットの状態を動かす操作、レビューの依頼・確認、リモートへの push は、"
            "親（メインエージェント）だけが行います。サブエージェントは自分のチケットの"
            "範囲で作業を終えたら、コミットまでして結果を報告して終わってください。"
            "合流と push と閉じるのは親の仕事です。",
        ]
    )


def ways_of_working(conf: settings.Settings, root: str, mode: str) -> str:
    """セッションの頭で渡す、直接作業とチケット作業の使い分け。

    チケット制御が効いているワークスペースで、モデルが「この作業にチケットは要るか」を
    自分で決められるようにする。判定はこの線引きを担保しない。チケットの無い作業ツリーと
    main 直下は全体ルールだけで判定されるので、直接作業はそのまま通る。

    phases.yml と risk.yml は解決後のパスで示す。無ければその括弧を省く。人が既定と
    違う場所に置いていれば、そちらの綴りが出る。
    """
    phases = _relative_or_omit(conf.phases, root)
    risk = _relative_or_omit(conf.risk, root)
    lines = [
        "[ccnavi] このワークスペースはチケット制御を使っている。作業の進め方は 2 つ。",
        "- 直接作業: 調査や小さな修正（触るファイルが少ない、振る舞いが変わらない、",
        "  人のレビューが要らない）は、チケットを起こさずそのまま進める。判定は全体ルールだけ。",
        "- チケット作業: 大きな修正（設計に触れる、複数の段階になる、人のレビューが要る）は、",
        f"  {conf.tickets}/ に提案を書いて承認を受け、フェーズ{phases}と",
        f"  リスクの配点{risk}に従って issue と MR を作りながら進める。",
        "  操作は sh .claude/scripts/ccnavi-ticket.sh と ccnavi-review.sh を通す。",
        "どちらで進めるか迷ったら、利用者に聞く。",
    ]
    if mode == DRY_RUN:
        lines.append(f"（現状: {settings.MODE_ENV}={DRY_RUN}。deny判定でも止めずに言うだけ）")
    return "\n".join(lines)


def approved(tickets, revisions: set[str]) -> str:
    """チケットが承認されたことをモデルに伝える文。

    `--approve --yes` の `prompt`（拡張が Claude Code に渡す）と、hook が次の
    UserPromptSubmit / PreToolUse で渡す `additionalContext` の両方がここから出る。
    2 か所で文を持つと、人が貼った文と hook が渡した文が食い違う。

    tickets は承認済みチケット（`ticket` `title` `parent` `phase` `is_child` を持つもの）。
    revisions は親の改版だった識別子。
    """
    lines = [
        "[ccnavi] チケットが承認され、承認済みチケットが置かれた。"
        "この範囲は次のツール呼び出しから効く。"
    ]
    for t in tickets:
        if t.ticket in revisions:
            where = "親の改版。計画が新しくなった"
        elif t.is_child:
            where = f"親 {t.parent}、フェーズ {t.phase}"
        else:
            where = "親"
        title = f": {t.title}" if t.title else ""
        lines.append(f"- {t.ticket}{title}（{where}）")
    lines.append(
        "後工程を進める。子は作業ツリー .claude/worktrees/<識別子> を親のブランチから切り、"
        "'sh .claude/scripts/ccnavi-ticket.sh start <識別子>' で着手する。"
    )
    return "\n".join(lines)


def _relative_or_omit(path: str, root: str) -> str:
    """案内に載せる設定ファイルの綴り。ワークスペースルートからの相対で括弧に入れる。
    無ければ空文字で、呼び手が括弧ごと省ける。"""
    if not path or not os.path.exists(path):
        return ""
    shown = path
    if os.path.isabs(path):
        try:
            shown = os.path.relpath(path, root)
        except ValueError:
            shown = path
    return f"（{shown.replace(os.sep, '/')}）"
