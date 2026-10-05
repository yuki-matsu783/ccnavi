"""判定につける文面と理由コード。

止めた・聞いた・渡した、のそれぞれについて、それだけで読んで成立する 1 件の文を
組む。判定の流れは judge にあり、ここは文面だけを持つ。文面はユーザとモデルが
読む成果物そのもので、判定の順序より直す頻度が高いので、別に置いて読み比べ
られるようにしてある。
"""

from __future__ import annotations

import os

from ..infra import settings, shellread, shellread_scan, shellread_words, tree
from ..infra.modes import DRY_RUN
from ..policy import rules
from ..tickets import phase, phase_forms, ticket_places

# 返す理由に載せる理由コード。設計 付録 B の体系から、今のビルドが実際に
# 下せる判定に対応するものだけを借りている。
#
# 付録 B のコードは「どの検査がその根拠を作ったか」の名前であって、ルール 1 件を
# 指す名前ではない。今のビルドが持つ検査は 1 つ（外から注入したルール集合を
# 正規化済みの対象に当てる）で、当てる先がコマンドかパスかで 2 つに分かれる。
# だからコードはその分かれ方に対応させ、どのルールだったかは出所が名指しする。
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

# 実行後チェックが出すコードは post_findings.py（CODE_VIOLATION・CODE_TICKET_SCOPE）と
# post_report.py（CODE_PREEXISTING）にある。あちらは判定ではなく、
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

# チケットが `ask` と書いた場所。ルールの `ask` と同じく、ユーザが 1 度見る場所。
CODE_TICKET_ASK = "TICKET_ASK"

# 生の文字列の `>` が上限（selfguard_shell.REDIRECT_LIMIT）を超え、シェルから書き込む形の保護を
# 当てられなかったので止めた。権限モードに依らず止め、確認には回さない。ルールに当たったのでは
# ないので、記録のルール名は括弧付きの REDIRECT_LIMIT_RULE にする。
CODE_REDIRECT_LIMIT_DENY = "DENY_REDIRECT_LIMIT"
REDIRECT_LIMIT_RULE = "(redirect-limit)"

# ワークツリーの元リポジトリと、チケットが承認されたプロジェクトが食い違っている。
CODE_TICKET_PROJECT = "DENY_TICKET_PROJECT_MISMATCH"

# 承認済みチケット自体が信頼できない（親が引けない、置き場と `project:` が違う、など）。
# 範囲の外に書いたのではないので、CODE_TICKET_SCOPE とは分ける。受け取った側の次の一手も
# 違う。範囲外なら範囲の中で済ませる方法があるが、こちらはユーザがチケットを直すまで
# どこにも書けない。
CODE_TICKET_BLOCKED = "DENY_TICKET_BLOCKED"

# 範囲外で止めたことを記録に残すときのルール名。対応するルールがルールファイルに
# 無いので、括弧付きにして、ファイルの中を探しても見つからないことを見た目で示す。
TICKET_RULE = "(ticket-scope)"

# 書き直しを求める形（shellread の FORM_*）ごとの理由コード。ルールに当たったのではなく読みの
# 決めごとで止めたので、記録のルール名は TICKET_RULE と同じく括弧付きの形の名前にする
# （rewrite_rule）。
CODE_REWRITE = {
    shellread.FORM_BRACE: "DENY_BRACE_EXPANSION",
    shellread.FORM_COMMAND_NAME: "DENY_COMMAND_NAME_EXPANSION",
    shellread.FORM_BACKQUOTE: "DENY_BACKQUOTE",
    shellread.FORM_AMBIGUOUS: "DENY_AMBIGUOUS_FORM",
}

# 理由に載せる対象の長さの上限。対象はエージェントが今書いたものなので、
# ここでは同じものを指せれば足りる。ヒアドキュメントには 1 ファイル分を丸ごと書けるので、
# 全文を載せると理由の本体が下へ流れて読まれなくなる。
SUBJECT_LIMIT = 200


def code_for(tool: str, degraded: str) -> str:
    """拒否の根拠コード。対象がコマンドかパスかで分かれる。"""
    if degraded:
        return CODE_UNCERTAIN
    return CODE_COMMAND if tool == "Bash" else CODE_PATH


def fallen_back(rules_path: str) -> str:
    """ルールファイルを読めずに組み込みの既定に戻ったことを伝える文。

    通した回にも返す。ここで何も言わないと、ガードが働いているように見えて実際には
    プロジェクトのルールを 1 件も見ていない、という状態が続く。それは
    ガードが止まっていることより悪い。止まっていれば誰かが気づくから。

    直し方に Write / Edit を名指しするのは、既定の側がシェルからこの場所への
    書き込みを止めているため。止めた先に代わりの方法が無いと、拒否されたあと先へ進めなくなる。
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
    そのときは同じ根拠でも受け取った側の次の一手が違う。ユーザに聞けるなら
    「言えば通るかもしれない」だが、聞けないなら「言っても通らない」ので、
    先に設定を直すか、ユーザが居るセッションでやり直すしかない。
    """
    shown = _shorten(subject)

    lines = [
        f"[ccnavi] {CODE_UNCERTAIN if degraded else CODE_UNDECLARED} (source: {rules_path})",
        f"subject: {shown}",
    ]
    if degraded:
        lines.append(unreadable(degraded))
        lines.append(
            "ccnavi is asking rather than deciding because it could not tell what this call "
            "would actually do. Say what the command is for, or rewrite it in a form that can "
            "be read: no heredoc, no string handed to something that runs it, no case inside "
            "$( )."
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


def reason_for(
    rule: rules.Rule,
    tool: str,
    subject: str,
    rules_path: str,
    degraded: str,
    runner: str,
    inner: str,
    quoted: bool,
) -> str:
    """当たったルール 1 件を、それだけで読んで成立する理由に組む。

    載せるのは 3 つ。何に当たったか（対象）、どういう筋の根拠か（理由コード）、
    それを言っているのはどの設定か（出所）。どれが欠けても、受け取った側は
    自分の呼び出しのどこが引っかかったのかを自分では辿れず、
    文面を信頼するか無視するかの二択になる。

    quoted は、このルールが引用の中から切り出したコマンドにだけ当たったこと
    （judge が bare に当て直して決める）。そのときは断りを 1 文足す。

    件ごとに閉じた形にするのは、1 回の応答に複数の理由が入り、そのうちどれが
    ユーザの目に入るかが決まらないため。「上に書いた事情が下の全部に掛かる」形は、
    1 件だけが切り出されて見えた時点で意味を失う。読めなかったという断りが
    件ごとに繰り返されるのはそのための手間で、繰り返しのほうが誤読より害が小さい。

    inner は、ルールに当たったのが実行役のコマンド（runner）が中で実行するコマンドだった
    ときの、そのコマンド。元の形で当たったときは空。
    """
    # 改行を含む対象は 1 行にまとめる。理由の文が対象の中の改行で分断されると、
    # どこまでが対象でどこからが言い分なのかが読めなくなる。
    shown = _shorten(subject)

    # コードはルールが置かれていたタイプから決まる。拒否と確認で同じコードを
    # 返すと、受け取った側は「止まった」のか「聞かれている」のかを文面から
    # 推し量ることになる。
    #
    # 中で実行されるコマンドで当たったときは、読めなかった断りを付けない。当てた先は
    # 生の文字列ではなく、読み直したコマンドなので。
    if inner:
        degraded = ""
    code = CODE_RULE_ASK if rule.decision == rules.ASK else code_for(tool, degraded)
    # 出所はルールの id で示す。プロジェクトのルールの id には `lib:git-push` の形で
    # プロジェクトの名前が付く（REQ-MLT-07）ので、id だけでどのファイルを見に行けばよいかが
    # 決まる。パスまで載せると、判定を試したときの一時ファイルのような読む値の無いパスが
    # そのまま毎回モデルに届く。id を持たないルールだけ、代わりにファイルを示す。
    source = f"rule: {rule.id}" if rule.id else f"rules: {rules_path}"
    if rule.id == phase_forms.TICKET_APPROVAL_RULE_ID:
        # 組み込み。ルールファイルには無いので、そこを探させない。
        code, source = (
            phase_forms.CODE_TICKET_APPROVAL,
            f"builtin rule: {rule.id} ({settings.GUARD_TICKET_APPROVAL_ENV})",
        )

    lines = [f"[ccnavi] {code} ({source})", f"subject: {shown}"]
    if inner:
        lines.append(ran_by(runner, inner))
    if degraded:
        lines.append(unreadable(degraded))
    lines.append(rule.spoken_message())
    if quoted:
        lines.append(inside_quotes())
    return "\n".join(lines)


def ran_by(runner: str, inner: str) -> str:
    """実行役のコマンドが中で実行するコマンドに当たったことを言う 1 行。

    元の形（`env rm -f …`）だけを見た読み手には、ルールのどこが当たったのかが分からない。
    ルールは `rm` について書かれていて、`env` については何も言っていないので。

    `cd` で移った先から見たパスに当たったときも同じで、書いた表記（`rm settings.json`）には
    当たったルールの名前が出てこない。どこへ書こうとしているかをパスで示す。
    """
    if runner == shellread.MOVED:
        return (
            f"`cd` で移った先から見ると `{_one_line(inner)}` になり、"
            "ルールはこの形にヒットしました。"
        )
    return f"ルールは `{_one_line(runner)}` が実行する `{_one_line(inner)}` にヒットしました。"


def _one_line(text: str) -> str:
    """コマンドを文面に載せる形にする。目印を空白に戻し、1 行にまとめて上限で切る。"""
    return _shorten(text.replace(shellread.SEP, " ").replace(shellread.WORD_SEP, " "))


def _shorten(text: str) -> str:
    """文面に載せる対象を 1 行にまとめ、上限で切って切った字数をつける。

    順は「まとめる → 切る → まとめた後の長さで残りを数える」。まとめる前の長さで数えると、
    改行や空白の続きまで残りに入り、見えていない字数を多く言う。
    """
    shown = " ".join(text.split())
    if len(shown) > SUBJECT_LIMIT:
        shown = shown[:SUBJECT_LIMIT] + f"…(+{len(shown) - SUBJECT_LIMIT})"
    return shown


def inside_quotes() -> str:
    """引用の中から切り出したコマンドにルールが当たったときにつける断り。

    書いた側は、二重引用や引用しないヒアドキュメントの中の `$( )` とバッククォートを、
    文字を書いただけのつもりでいる。実際にはシェルが実行するので止めるのは正しいが、
    文字として渡す方法を知らなければ、同じ形を書き直しては止まる。方法はあるので名指しする。
    git の値を変数に取る形は、ラッパースクリプトで出して読み、値を次に書く 2 つの手順になる。
    """
    return (
        "note: this rule matched a command inside double quotes or an unquoted heredoc. The "
        "shell runs $( ) and backquotes there; they are not only written down. To keep them as "
        "text, use single quotes, escape them as \\$( and \\`, or pass the text from a file "
        "(git commit -F <file>, gh --body-file <file>). To use a value such as a git revision, "
        "print it with one command first and write the value into the next."
    )


def unreadable(reason: str) -> str:
    """コマンドを読めないまま出した 1 件につける断り。

    2 つの違う失敗に同じ文を使わせないために要る。「禁止されたコマンドを実行した」と
    「読めない文字列のどこかにその語がある」では次にやることが違うし、
    後者なのに前者を渡された読み手は、書いた覚えのないコマンドを探しに行く。
    """
    what = {
        shellread_scan.REASON_UNTERMINATED: ("a quote or heredoc in this command never closes"),
        shellread_words.REASON_TAKEN_AS_CODE: (
            "this command hands a string to something that runs it as code"
        ),
        shellread_scan.REASON_UNTERMINATED_SUBST: "a $( ) in this command never closes",
    }.get(reason, "this command could not be read")
    return (
        "note: " + what + ", so this rule was matched against the raw text of the "
        "command instead of against what runs. If these words are only being "
        "written down and not run, nothing was deliberately forbidden: move the "
        "text into a file, or split the command up, and try again."
    )


def subagent_forbidden(subject: str, runner: str, inner: str) -> str:
    """サブエージェントに許さない操作を止めた文。inner は reason_for と同じ。"""
    shown = _shorten(subject)
    return "\n".join(
        [
            f"[ccnavi] {phase.CODE_SUBAGENT}",
            f"subject: {shown}",
            *([ran_by(runner, inner)] if inner else []),
            "チケットの状態を動かす操作、レビューの依頼・確認、リモートへの push は、"
            "親（メインエージェント）だけが行います。サブエージェントは自分のチケットの"
            "範囲で作業を終えたら、コミットまで済ませ、結果を報告して終えてください。"
            "合流、push、チケットを閉じることは親の仕事です。",
        ]
    )


def builtin_refusal(code: str, subject: str, text: str) -> str:
    """組み込みの判定で止めた文。ルールに当たったのではないので、ルールの id は出さない。"""
    shown = _shorten(subject)
    return "\n".join([f"[ccnavi] {code}", f"subject: {shown}", text])


def rewrite_rule(form: str) -> str:
    """書き直しを求める形で止めたときに、記録の `rules` に残す名前。"""
    return f"({form})"


def rewrite(subject: str, form: str, found: list[str]) -> str:
    """書き直しを求める形を止めた文。形ごとに 1 件。

    ルールに当たったのではないので、禁止された操作をしたとは言わない。止めたのは読みの
    決めごとで、書き直す方法は必ずある。方法を名指ししないと、同じ形を書き直しては止まる。
    """
    shown = _shorten(subject)
    listed = ", ".join(f"`{_one_line(text)}`" for text in found[:_REWRITES_SHOWN])
    if len(found) > _REWRITES_SHOWN:
        listed += f" (+{len(found) - _REWRITES_SHOWN})"
    what, how = _REWRITE_TEXT[form]
    return "\n".join(
        [f"[ccnavi] {CODE_REWRITE[form]}", f"subject: {shown}", f"{what}: {listed}", how]
    )


# 止めた文に並べる表記の数。1 つ直せば残りも同じ直し方になる。
_REWRITES_SHOWN = 5

# 形ごとの（見つけたものの呼び名, 書き直し方）。
_REWRITE_TEXT = {
    shellread.FORM_BRACE: (
        "brace expansion outside quotes",
        "Outside quotes the shell expands these into several words before the command runs, "
        "so the words ccnavi reads are not the words that would run. bash and zsh expand them "
        "differently, so ccnavi does not guess the result; it stops the call, also in the few "
        "places where no shell expands them (an assignment, a case pattern, [[ ]]). Write the "
        "words out instead: "
        "`--exclude-dir={a,b}` becomes `--exclude-dir=a --exclude-dir=b`, `cp f{,.bak}` "
        "becomes `cp f f.bak`, `{1..3}` becomes `1 2 3`. If the braces are meant as text, "
        "put them in single quotes: '{a,b}'.",
    ),
    shellread.FORM_COMMAND_NAME: (
        "command name the shell works out when it runs",
        "The shell decides this command name only when it runs (from a variable, $( ), or a "
        "glob), so ccnavi cannot tell which program would run, and no rule can match it; it "
        "stops the call. Write the command name out: `c=git; $c status` becomes `git status`, "
        '`"$(command -v python3)" x.py` becomes `python3 x.py`, `/usr/bin/gi? status` becomes '
        "`/usr/bin/git status`. The same goes for a script handed to sh or source: write its "
        "path, not `sh $SCRIPT`.",
    ),
    shellread.FORM_BACKQUOTE: (
        "backquote",
        "The shell runs what is between backquotes, also inside double quotes and unquoted "
        "heredocs. ccnavi does not read backquotes; it stops the call. For a command "
        "substitution, write $( ) instead. To keep backquotes as text (Markdown in an issue or "
        "PR body, for example), put the text in single quotes, escape each one as \\`, or pass "
        "the text from a file (gh --body-file <file>, git commit -F <file>).",
    ),
    shellread.FORM_AMBIGUOUS: (
        "form that shells read differently",
        "bash and zsh read this form differently, or it nests too deep, so ccnavi cannot tell "
        "what would run; it stops the call. Write it in a plain form: if/elif instead of case "
        "inside $( ); a space in $( (cmd) | x ) so it is not read as arithmetic; run the inner "
        "command first and write its value into the next command instead of nesting $( ) "
        "deeply; & instead of coproc; a for loop instead of select.",
    ),
}


def ways_of_working(conf: settings.Settings, root: str, mode: str) -> str:
    """セッションの頭で渡す、直接作業とチケット作業の使い分け。

    チケット制御が有効なワークスペースで、モデルが「この作業にチケットは要るか」を
    自分で決められるようにする。判定はこの線引きを保証しない。チケットの無いワークツリーと
    ワークスペースルート直下は全体ルールだけで判定されるので、直接作業はそのまま通る。

    言うのは線引きと入口だけにする。この文はセッションの開始（起動・再開・compact・clear）
    のたびに届くので、後から必要な場所で改めて届くものを頭では言わない。名指しするのは、
    レビューの sh のパスがフェーズの終わりに来たとき（`phase.py`）と `ready` の手順（`ops.py`）、
    ユーザがどこで見るか（`review` の `mr` / `chat`）がそのフェーズを止めるとき（`phase.py`）、
    フェーズ定義の在りかが `ccnavi-ticket.sh` の使い方（`--help`）、リスクの配点の書き方が
    承認のときの検査（`agree_candidates.py`）、後工程の進め方が承認済みチケットが置かれたとき
    （`agree_screen.approved_text`）。

    dry-run の注記は「止まらない」だけで終えない。止まらないことだけを伝えると、通った
    ことが許可の証拠として読まれる。案内に従うところまでを 1 行に入れる。
    """
    ticket_sh = settings.script_command(root, "ccnavi-ticket.sh")
    lines = [
        "[ccnavi] このワークスペースはチケット制御を使っている。作業の進め方は 2 つ。",
        "- 直接作業（調査・小さな修正）: チケットを起こさずそのまま進める。判定は全体ルールだけ。",
        "- チケット作業（設計に触れる・複数のフェーズに分かれる・ユーザのレビューが要る）: "
        f"{conf.tickets}/todo/ に提案を書いて承認を受ける。"
        f"承認されると提案は {conf.approved}/doing/ へ動く。"
        f"以後の操作は {ticket_sh} を通す（使い方は --help）。",
        "どちらで進めるか迷ったら、ユーザに聞いてください。",
    ]
    if mode == DRY_RUN:
        lines.append(
            f"（現状: {settings.MODE_ENV}={DRY_RUN}。deny にヒットしても止まらない。"
            "通ったことを許可と読まず、表示された案内に次からは従う）"
        )
    return "\n".join(lines)


# 作業の決まりのうち、ワークツリーと git の行を支える deny の id（書かれたままの表記）。
# この id のルールがワークスペースに無ければ、その行は出さない。
RULE_MAIN_TREE = "main-tree"
RULE_RAW_GIT = "raw-git"


def conventions(
    conf: settings.Settings,
    root: str,
    docs: bool = False,
    deny_ids: set[str] | None = None,
) -> str:
    """セッションの頭で渡す、ccnavi の動作が前提にしている作業の決まり（REQ-SES-06）。

    ワークスペースの CLAUDE.md に書いていた決まりのうち、ccnavi の判定・置き場・入口に
    依存するものをここに寄せる。ccnavi を入れたワークスペースなら、どこでも同じ文が届く。

    各項目は 1 行の要点に留め、詳しいことは ccnavi に聞く形にする。この文は起動・再開・
    compact・clear のたびに届く常駐の文脈なので、長くすると毎回それだけ重くなる。聞く先は
    新しく作らず、今ある入口を名指しする。git の通る形は `ccnavi-git.sh --help`、止めた
    理由と代わりの手段は拒否の文面、承認済みチケットの状態は `ccnavi-ticket.sh status`、
    それ以外の詳しい決まりは `--docs`（ワークスペースの文書の frontmatter の索引）。

    - ワークツリーの行は、ワークスペースのルール（共通レイヤーと自身のレイヤー）に
      `main-tree` の deny があるときだけ、git の行は `raw-git` の deny があるときだけ出す。
      決まりを支えるのはそのルールで、ルールが無いワークスペースでは文と判定が食い違う。
      ルールの有無は `deny_ids`（判定と同じ読み方）で見る
    - `projects` の行は、プロジェクトの置き場にプロジェクトがあるときだけ出す
    - `status` の行は、チケット制御が有効なときだけ出す
    - `--docs` の行は、索引の案内を出す回（`docs` が真）だけ出す。
      文書が無いワークスペースで引き方だけを案内しても、何も当たらない
    """
    declared = deny_ids if deny_ids is not None else set()
    lines = ["[ccnavi] ccnavi が前提にしている作業の決まり。"]
    if RULE_MAIN_TREE in declared:
        worktrees = tree.WORKTREES_DIR.replace("\\", "/")
        lines.append(
            f"- 編集する前にワークツリー（{worktrees}/<名前>）を切り、その中で編集する。"
            "切り方は ccnavi-git.sh の --help（worktree add）。"
        )
    if RULE_RAW_GIT in declared:
        git_sh = settings.script_command(root, "ccnavi-git.sh")
        lines.append(
            f"- git は直接呼ばず {git_sh} を通す。止められたら迂回せず、出力の案内に従う。"
        )
    lines.append(
        f"- 下書きと使い捨てのファイルはワークツリーの {ticket_places.SCRATCH}/ に置く"
        "（追跡されず、範囲外の変更としても報告されない）。"
    )
    if tree.projects(conf.projects):
        home = os.path.relpath(conf.projects, root).replace("\\", "/")
        lines.append(
            f"- {home}/<名前>/ を直すときは、そこへ cd してから作業する"
            "（git の操作は cwd のリポジトリに向く）。"
        )
    lines += [
        "- 実装・調査・テストはサブエージェントにバックグラウンドで任せる。"
        "メインはユーザとの相談・判断・報告を受け持つ。",
        "- 判定が緩む・ユーザとのやり取りの形が変わる・元に戻せない・影響範囲を読み切れない変更は、"
        "実装する前にユーザと合意する。",
    ]
    if conf.tickets_enabled:
        ticket_sh = settings.script_command(root, "ccnavi-ticket.sh")
        lines.append(
            "- 承認済みチケットの状態は、ファイルを読んで推測せず"
            f" {ticket_sh} status [<親>] で確かめる。"
        )
    if docs:
        lines.append(
            "詳しい決まりは下の案内の --docs で、--keyword <語> を付けて引いてください"
            "（例: ワークツリー、下書き、サブエージェント、相談）。"
        )
    return "\n".join(lines)
