"""判定につける文面と理由コード。

止めた・聞いた・渡した、のそれぞれについて、それだけで読んで成立する 1 件の文を
組む。判定の流れは judge にあり、ここは文面だけを持つ。文面はユーザとモデルが
読む成果物そのもので、判定の順序より直す頻度が高いので、別に置いて読み比べ
られるようにしてある。
"""

from __future__ import annotations

import re

from ..infra import hookio, settings, shellread, shellread_scan, shellread_words
from ..infra.modes import DRY_RUN
from ..policy import rules
from ..tickets import phase, phase_forms
from . import ccnavimd

# 返す理由に載せる理由コード。設計の理由コードの体系から、今のビルドが実際に
# 下せる判定に対応するものだけを借りている。
#
# 体系のコードは「どの検査がその根拠を作ったか」の名前であって、ルール 1 件を
# 指す名前ではない。今のビルドが持つ検査は 1 つ（外から注入したルール集合を
# 正規化済みの対象に適用する）で、適用する先がコマンドかパスかで 2 つに分かれる。
# だからコードはその分かれ方に対応させ、どのルールだったかは出所が名指しする。
# ルール 1 件ごとにコードを持たせれば体系の粒度（ヒアドキュメントなら
# DENY_REDIRECT、DB 破壊なら DENY_DB_DESTRUCTIVE）に届くが、それはルール
# ファイルに欄を 1 つ足すことなので書式の版が上がる。この要件が求めるのは
# 「理由コードを含むこと」までなので、版を上げずに済む側を採る。
CODE_COMMAND = "DENY_COMMAND_PATTERN"  # Bash の実行される部分にヒットした

CODE_PATH = "DENY_PATH"  # ファイルのパスにヒットした

# ルールが ask と書いてある場所にヒットした。「ここは毎回人間が見るべき」という
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
# 読み切れないコマンドの根拠は、宣言された禁止にヒットしたことではなく、
# 対象を確定できなかったこと。こちらは権限モードに委ねない。読めなかった
# という事実は判定の結果に現れず、委ねた先には伝わらないので、言えるのが
# ここしかない。生の文字列が deny にヒットしたときだけは拒否になり、
# そのときも「読めないまま適用した」ことを文面が断る。
CODE_UNCERTAIN = "PARSE_UNCERTAIN"

# 承認されたチケットの作業範囲の外に書こうとした。ルールにヒットしたのではなく、
# 宣言された範囲に入っていないことが根拠なので、コードを分けている。
# 受け取った側の次の一手が違う。ルールなら別の手段を探すことになるが、
# こちらは範囲の中で済ませるか、チケットを書き直して承認を求めることになる。
CODE_TICKET_SCOPE = "DENY_TICKET_SCOPE"

# チケットが `ask` と書いた場所。ルールの `ask` と同じく、ユーザが 1 度見る場所。
CODE_TICKET_ASK = "TICKET_ASK"

# 生の文字列の `>` が上限（selfguard_shell.REDIRECT_LIMIT）を超え、シェルから書き込む形の保護を
# 適用されなかったので止めた。権限モードに依らず止め、確認には回さない。ルールにヒットしたのでは
# ないので、記録のルール名は組み込みの名前（`builtin:` で始まる）の REDIRECT_LIMIT_RULE にする。
CODE_REDIRECT_LIMIT_DENY = "DENY_REDIRECT_LIMIT"
REDIRECT_LIMIT_RULE = "builtin:redirect-limit"

# ワークツリーの元リポジトリと、チケットが承認されたプロジェクトが食い違っている。
CODE_TICKET_PROJECT = "DENY_TICKET_PROJECT_MISMATCH"

# 承認済みチケット自体が信頼できない（親が引けない、置き場と `project:` が違う、など）。
# 範囲の外に書いたのではないので、CODE_TICKET_SCOPE とは分ける。受け取った側の次の一手も
# 違う。範囲外なら範囲の中で済ませる方法があるが、こちらはユーザがチケットを直すまで
# どこにも書けない。
CODE_TICKET_BLOCKED = "DENY_TICKET_BLOCKED"

# 親のワークツリーに、置き場（下書き・提案・ELI5・承認済み）以外を書こうとした。親の範囲の
# 外に書いたのではなく、親は作業の場ではないことが根拠なので、CODE_TICKET_SCOPE とは分ける。
# 次の一手も違う。範囲を広げても通らず、子のチケットを起こしてそのワークツリーで作業する。
CODE_TICKET_PARENT = "DENY_TICKET_PARENT"

# 範囲外で止めたことを記録に残すときのルール名。対応するルールがルールファイルに
# 無いので、組み込みの名前（`builtin:` で始まる）にして、ファイルの中を探しても見つからない
# ことを見た目で示す。
TICKET_RULE = "builtin:ticket-scope"

# 範囲を適用する前に止めた組み込みの判定の、記録のルール名。範囲の外に書いたのではないので
# TICKET_RULE を使い回さない。使い回すと、記録の `rules` で範囲外の件数に混ざり、繰り返しの
# 文面（repeat.note）も「同じ理由（builtin:ticket-scope）」と、止めた理由と違う名前を言う。形は
# フローのロック（flow.LOCK_RULE）・`>` の上限（REDIRECT_LIMIT_RULE）と同じ組み込みの名前。
SUBAGENT_TICKET_OP_RULE = "builtin:subagent-ticket-op"  # DENY_SUBAGENT_TICKET_OP
PHASE_REVIEW_RULE = "builtin:phase-review"  # DENY_PHASE_REVIEW
TICKET_PROJECT_RULE = "builtin:ticket-project"  # DENY_TICKET_PROJECT_MISMATCH
TICKET_BLOCKED_RULE = "builtin:ticket-blocked"  # DENY_TICKET_BLOCKED
TICKET_PARENT_RULE = "builtin:ticket-parent"  # DENY_TICKET_PARENT

# 書き直しを求める形（shellread の FORM_*）ごとの理由コード。ルールにヒットしたのではなく読みの
# 決めごとで止めたので、記録のルール名は TICKET_RULE と同じく組み込みの名前
# （`builtin:` で始まる）にする（rewrite_rule）。
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

    直し方として下書きとユーザの取り込みを挙げるのは、既定の側がシェルからも、Write / Edit からも
    この場所への書き込みを止めているため（builtin:guard-ccnavi-config は読めない間も外れない）。
    止めた先に代わりの方法が無いと、拒否されたあと先へ進めなくなる。
    """
    return (
        "[ccnavi] the rule file at "
        + rules_path
        + " could not be read, so ccnavi is judging with its built-in defaults. "
        "The project's own rules are not in force right now. Neither the shell nor "
        "the Write or Edit tool can write to that file while the defaults are in "
        "force. Put a draft at wip/handoff/.ccnavi/config/common/rules.yml and ask "
        "the user to import it with 'ccnavi-user-handoff.sh' (or to fix the file by hand)."
    )


def undeclared(
    tool: str,
    subject: str,
    source: str,
    degraded: str,
    *,
    verdict: str,
    fell_back: bool = False,
) -> str:
    """どのタイプも言及しなかった呼び出しに返す文。

    危険だとは言わない。言えないから権限モードに委ねている。根拠は設定の穴で
    あって呼び出しの中身ではないので、危険の表明として書くと、受け取った側は
    存在しない危険を探すことになる。

    直し方を書くのは、これが繰り返されるのが穴の側の問題だから。同じ場所で
    何度も止まるなら、答えるべきなのは呼び出しごとの是非ではなく
    「この場所を allow に書くかどうか」になる。

    verdict は `judge.undeclared_verdict` の戻り（ask か deny）。次の一手は、その結末と
    読み切れたかどうか（degraded）の組で決まるので、組ごとの表（`_UNDECLARED_NEXT`）から引く。
    `refused: bool` のように片方だけを渡すと、読めなかった回の分岐が先にヒットし、止めた回にも
    「聞いている、用途を言えば通る」と返していた。聞ける者が居ないモードで用途を言っても通らない。
    表に無い結末（渡した回 `(handover)` など）には文を組まず空を返す。渡した回は文面を返さない
    決まりで、呼び手もその回には呼ばない。

    source は判定の出所（`ruleload.load_rules` が返すもの）。組み込みの既定に退避した回
    （fell_back）は `builtin.SOURCE` になる。読めなかったルールファイルを根拠として名指しし、
    そこへ allow を足せと案内すると、同じ応答の退避の文（`fallen_back`）と食い違う。直し方は
    退避の文が言うので、ここではそれを指すだけにする。
    """
    template = _UNDECLARED_NEXT.get((bool(degraded), verdict))
    if template is None:
        return ""
    lines = [
        f"[ccnavi] {CODE_UNCERTAIN if degraded else CODE_UNDECLARED} (source: {source})",
        f"subject: {_shorten(subject)}",
    ]
    if degraded:
        lines.append(unreadable(degraded, matched=False))
    if fell_back:
        where = "the built-in defaults ccnavi fell back to"
        then = _UNDECLARED_FALLEN_BACK[verdict]
    else:
        where = source
        then = _UNDECLARED_THEN[verdict].format(where=source)
    lines.append(
        template.format(what="command" if tool == "Bash" else "path", where=where, then=then)
    )
    if degraded:
        lines.extend(rewrite_hint(degraded))
    return "\n".join(lines)


# 言及の無い呼び出しの文。キーは（読み切れなかったか, 結末）。結末は
# `judge.undeclared_verdict` が返す ask か deny で、渡した回（handover）は文を返さない。
# 読み切れなかった回は、ルールファイルに何を足しても読めるようにはならないので、
# 次の一手は書き直しになる（{then} を使わない）。
_UNDECLARED_NEXT = {
    (False, rules.ASK): (
        "No rule in {where} mentions this {what}, so ccnavi has no verdict of its own and "
        "leaves the call to Claude Code's permission mode, which in this session asks the user. "
        "This is not a warning about the call itself. {then}"
    ),
    (False, rules.DENY): (
        "No rule in {where} mentions this {what}, and this session has no one to ask: its "
        "permission mode answers on its own or not at all. An undeclared call is not allowed "
        "here. {then}"
    ),
    (True, rules.ASK): (
        "ccnavi は、このコマンドが実際に何を実行するか読めなかったので、判定せずに確認しています。"
        "何のためのコマンドかを伝えるか、読める形に書き直してください。"
    ),
    (True, rules.DENY): (
        "ccnavi は、このコマンドが実際に何を実行するか読めませんでした。このセッションには"
        "確認に答える人がおらず、権限モードが自分で答えるか、そもそも答えないので、実行しません。"
        "何のためのコマンドかを伝えても通りません。読める形に書き直してください。"
        "書き直せないときは、何のために要るかをユーザに伝えてください。"
    ),
}

# 読み切れた回の締め。ルールファイルを読めている回は、穴を塞ぐ先（allow）を名指しする。
_UNDECLARED_THEN = {
    rules.ASK: (
        "If this is ordinary work for this project, say so and ask the user to add it to the "
        "allow section of {where}; that is what stops the same question from coming back."
    ),
    rules.DENY: (
        "Either do the work with something the allow section of {where} already covers, "
        "or tell the user what needs to be added there and let them decide."
    ),
}

# 組み込みの既定に退避した回の締め。読めなかったファイルへ allow を足せとは言わない。
# 直し方は同じ応答の退避の文（`fallen_back`）が言うので、それを指すだけにする。
_UNDECLARED_FALLEN_BACK = {
    rules.ASK: (
        "The project's rule file could not be read (see the note above), so its allow section "
        "is not in force; ask the user to repair that file rather than to add anything to it."
    ),
    rules.DENY: (
        "The project's rule file could not be read (see the note above), so its allow section "
        "is not in force; tell the user that file has to be repaired first."
    ),
}


def reason_for(
    rule: rules.Rule,
    tool: str,
    subject: str,
    rules_path: str,
    degraded: str,
    runner: str,
    inner: str,
    quoted: bool,
    *,
    git: str = "git",
) -> str:
    """ヒットしたルール 1 件を、それだけで読んで成立する理由に組む。

    載せるのは 3 つ。何にヒットしたか（対象）、どういう筋の根拠か（理由コード）、
    それを言っているのはどの設定か（出所）。どれが欠けても、受け取った側は
    自分の呼び出しのどこが引っかかったのかを自分では辿れず、
    文面を信頼するか無視するかの二択になる。

    quoted は、このルールが引用の中から切り出したコマンドにだけヒットしたこと
    （judge が bare に適用し直して決める）。そのときは断りを 1 文足す。

    件ごとに閉じた形にするのは、1 回の応答に複数の理由が入り、そのうちどれが
    ユーザの目に入るかが決まらないため。「上に書いた事情が下の全部に掛かる」形は、
    1 件だけが切り出されて見えた時点で意味を失う。読めなかったという断りが
    件ごとに繰り返されるのはそのための手間で、繰り返しのほうが誤読より害が小さい。

    inner は、ルールにヒットしたのが実行役のコマンド（runner）が中で実行するコマンドだった
    ときの、そのコマンド。元の形でヒットしたときは空。

    git は文面で案内するラッパーの打ち方（settings.git_wrapper_command）。
    ラッパーが無いときは None で、案内の形を書かない（生の git は組み込みで止まる）。

    処理フロー:
    1. 対象を 1 行に縮める（`_shorten`）。
    2. 理由コードと出所を決める。ask は ask のコード、それ以外はツールと読めなさで決まる
       （`code_for`）。組み込みのルールは、出所を組み込みの名前にする。承認の組み込み
       （`TICKET_APPROVAL_RULE_ID`）だけは、理由コードも `CODE_TICKET_APPROVAL` に替える。
    3. 1 行目（理由コードと出所）と対象の行を置く。
    4. 実行役の行（`ran_by`）・読めなかった断り（`unreadable`）・
       ルールの文（`spoken_message`）を順に足す。
    5. 引用の中だけにヒットしたときは、その断り（`inside_quotes`）を最後に足す。
    """
    # 改行を含む対象は 1 行にまとめる。理由の文が対象の中の改行で分断されると、
    # どこまでが対象でどこからが言い分なのかが読めなくなる。
    shown = _shorten(subject)

    # コードはルールが置かれていたタイプから決まる。拒否と確認で同じコードを
    # 返すと、受け取った側は「止まった」のか「聞かれている」のかを文面から
    # 推し量ることになる。
    #
    # 中で実行されるコマンドでヒットしたときは、読めなかった断りを付けない。適用した先は
    # 生の文字列ではなく、読み直したコマンドなので。
    if inner:
        degraded = ""
    code = CODE_RULE_ASK if rule.decision == rules.ASK else code_for(tool, degraded)
    # 出所は記録名（`<ワークスペースからの相対パス>:<書いた id>`）で示す。どのファイルの
    # どのルールかが、名前だけで決まる。判定を試したときの一時ファイルのように
    # ワークスペースの外にあるものは、相対パスが `../` で始まる長い名前になる。
    source = f"rule: {rules.record_name(rule)}"
    if rule.id == phase_forms.TICKET_APPROVAL_RULE_ID:
        # 組み込み。ルールファイルには無いので、そこを探させない。
        code, source = (
            phase_forms.CODE_TICKET_APPROVAL,
            f"組み込みのルール: {rule.id} ({settings.GUARD_TICKET_APPROVAL_ENV})",
        )

    lines = [f"[ccnavi] {code} ({source})", f"subject: {shown}"]
    if inner:
        lines.append(ran_by(runner, inner))
    if degraded:
        lines.append(unreadable(degraded))
        lines.extend(rewrite_hint(degraded))
    lines.append(rule.spoken_message())
    if quoted:
        lines.append(inside_quotes(git))
    return "\n".join(lines)


# 非対話で確認が自動で拒否になるとき、代わりの手を reason に出すルールの一覧です（bare_id → (対象に
# 含まれる語の式, 1 行)）。式が空なら、そのルールにヒットした回に必ず出します。
# ask のルールには message を書けず、additionalContext は自動拒否のときに届かないため、ここに置きます。
#
# direct-worktree-confirm の 1 行は、あとのスクリプト側の拒否を確認が隠さないための説明です。
# `--adopt` は、確認が通ってもスクリプトが「チケットに結び付くワークツリーです」と断るので、
# 「了承されたら打ち直す」だけを言うと、打ち直しても通らない案内になります。
_ASK_ALTERNATIVE = {
    "prefer-webfetch": (
        "",
        "代わりの手: 調べものが一度きりなら WebFetch ツールを、"
        "何度も呼び出すなら .ccnavi/scripts/ の sh にまとめてから実行してください。",
    ),
    "direct-worktree-confirm": (
        r"\s--adopt(?:\s|=|$)",
        "ただし --adopt は、チケットに結び付く名前（承認済みチケット・提案・親のブランチの識別子。"
        "ccnavi-ticket.sh status に出る）のワークツリーには、了承されても通りません。"
        "ccnavi-worktree.sh が断ります。その場合は了承を頼まず、"
        "チケットの作業はマージリクエストで出します（ccnavi-ticket.sh status の次の操作に従う）。"
        "了承を頼むのは、チケットに結び付かない直接作業のワークツリーだけです。",
    ),
}


def ask_hint(bare_id: str, subject: str) -> list[str]:
    """確認（ask）の理由に足す説明です。非対話のときだけ返し、対話のときは空にします。

    非対話のセッションでは確認に答える人がいないため、ask は自動で拒否されます。
    ルール名と対象だけでは、「確認待ち」なのか「止められた」のかが分からないため、
    確認が要ることと次の行動を言います。確認として返す回（ASK）にだけ足します。
    上限で止める拒否など、実際には確認が出ない回に足すと、打ち直しても通らない案内になります。
    """
    if not ccnavimd.unattended():
        return []
    shown = _shorten(subject)
    lines = [
        "この操作はユーザの確認が要りますが、このセッションでは確認に答えられません。"
        f"ユーザに「{shown}」の了承を頼み、了承されたら打ち直してもらってください。"
    ]
    needle, alternative = _ASK_ALTERNATIVE.get(bare_id, ("", ""))
    if alternative and (not needle or re.search(needle, subject, re.IGNORECASE)):
        lines.append(alternative)
    return lines


def ran_by(runner: str, inner: str) -> str:
    """実行役のコマンドが中で実行するコマンドにヒットしたことを言う 1 行。

    元の形（`env rm -f …`）だけを見た読み手には、ルールのどこがヒットしたのかが分からない。
    ルールは `rm` について書かれていて、`env` については何も言っていないので。

    `cd` で移った先から見たパスにヒットしたときも同じで、書いた表記（`rm settings.json`）には
    ヒットしたルールの名前が出てこない。どこへ書こうとしているかをパスで示す。
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


def inside_quotes(git: str | None = None) -> str:
    """引用の中から切り出したコマンドにルールがヒットしたときにつける断り。

    書いた側は、二重引用や引用しないヒアドキュメントの中の `$( )` とバッククォートを、
    文字を書いただけのつもりでいる。実際にはシェルが実行するので止めるのは正しいが、
    文字として渡す方法を知らなければ、同じ形を書き直しては止まる。方法はあるので名指しする。
    git の値を変数に取る形は、ラッパースクリプトで出して読み、値を次に書く 2 つの手順になる。
    git は案内するラッパーの打ち方（settings.git_wrapper_command）。ラッパーが無いときは None。
    """
    return (
        "注: このルールは二重引用符の中、または引用しないヒアドキュメントの中のコマンドに"
        "ヒットしました。そこではシェルが $( ) とバッククォートを実行します。文字として書くだけでは"
        "ありません。文字のまま渡すには、シングルクォートで囲むか、\\$( と \\` と書くか、"
        f"文面をファイルから渡してください（{_commit_file(git)}"
        "glab --description-file <file>、gh なら --body-file <file>）。"
        "git のリビジョンのような値を使うには、先に 1 つのコマンドで値を出力し、"
        "次のコマンドに値を書いてください。"
    )


def unreadable(reason: str, *, matched: bool = True) -> str:
    """コマンドを読めないまま出した 1 件につける断り。

    2 つの違う失敗に同じ文を使わせないために要る。「禁止されたコマンドを実行した」と
    「読めない文字列のどこかにその語がある」では次にやることが違うし、
    後者なのに前者を渡された読み手は、書いた覚えのないコマンドを探しに行く。

    matched は、ルールにヒットした 1 件につけるか。言及の無い回（`undeclared`）はどのルールにも
    ヒットしていないので、「this rule」と言うと指す先が無い。そのときは読めなかったことだけを言い、
    次の一手は呼び手が続けて書く。
    """
    what = {
        shellread_scan.REASON_UNTERMINATED: "引用符かヒアドキュメントが閉じていない",
        shellread_words.REASON_TAKEN_AS_CODE: "文字列をコードとして実行するものに渡している",
        shellread_scan.REASON_UNTERMINATED_SUBST: "$( ) が閉じていない",
        shellread_scan.REASON_ANSI_C: "ANSI-C 引用（$'…'）の中身が何の文字になるか読めない",
    }.get(reason, "読めない形をしている")
    if not matched:
        return (
            f"注: このコマンドは、{what}ため、ccnavi は書かれた生の文字列にしかルールを当てられず、"
            "どのルールもそれに言及していません。"
        )
    return (
        f"注: このコマンドは、{what}ため、このルールは実行される中身ではなく、書かれた生の文字列に"
        "当てました。語を書いただけで実行しないなら、禁止したわけではありません。"
        "語をファイルに移すか、コマンドを分けて打ち直してください。"
    )


# 読めなかった理由ごとの書き換え例。書き換え先は読める形で、ルールには触れない。
_REWRITE = {
    shellread_words.REASON_TAKEN_AS_CODE: (
        "書き換え: Grep ツールを使うか、`grep -rn <語> <パス>` と書く。"
        "ファイルの行数などが要るなら、`wc -l <パス>` のようにファイル名を直接並べて 1 回で呼ぶ。",
        "`xargs` と `find -exec` は、後ろのコマンドが次のどれかのときだけ読める形として扱う: "
        + " ".join(sorted(shellread_words._READONLY_RUNNEE)),
    ),
    shellread_scan.REASON_ANSI_C: (
        "書き換え: タブは `grep -P '\\t'` のように書く。"
        "または `printf '\\t'` の出力をファイルに書いて、そのファイルを渡す。",
    ),
    shellread_scan.REASON_UNTERMINATED: (
        "書き換え: 複数行の文字列やヒアドキュメントは、Write ツールでファイルに書き、"
        "そのファイルを渡す。引用符は閉じて書く。",
    ),
}


def rewrite_hint(reason: str) -> list[str]:
    """読めなかった理由ごとの書き換え例の行。例の無い理由は空。

    非対話のセッションでは確認に答える人がいないので、書き直すほかないことを 1 行足す。
    """
    lines = list(_REWRITE.get(reason, ()))
    if lines and ccnavimd.unattended():
        lines.append("このセッションでは確認に答えられないので、上の形に書き直してください。")
    return lines


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


def builtin_refusal(code: str, rule_id: str, subject: str, text: str) -> str:
    """組み込みの判定で止めた文。

    rule_id は記録の `rules` に残す名前と同じもの。ルールファイルには無いので、
    `組み込みのルール:` と断ってファイルの中を探させない。文面と記録が同じ識別子を持てば、
    受け取った文から記録の行を引ける。ルールファイルのルールで止めた文（reason_for）と
    同じ並びにしてある。
    """
    shown = _shorten(subject)
    return "\n".join([f"[ccnavi] {code} (組み込みのルール: {rule_id})", f"subject: {shown}", text])


def internal_error(code: str, event: str, kind: str, log_path: str, *, passed: bool = False) -> str:
    """ccnavi 自身の例外で判定や検査を終えられなかったときに、モデルへ返す文。

    呼び出しの中身が原因ではないので、書き直し方は言えない。言えるのは、同じ呼び出しを
    繰り返しても結果は変わらないことと、直せるのはユーザだということ。診断ログの場所を
    名指しするのは、ユーザが例外の型と場所をそこで読めるようにするため。
    """
    if event == hookio.POST_TOOL_USE:
        what = (
            "ccnavi の内部の不具合で、この呼び出しが何を変えたかを確かめられませんでした。"
            "実行後の保護対象の検査は行われていません。"
        )
    elif passed:
        what = (
            "ccnavi の内部の不具合で、この呼び出しを判定できませんでした。"
            "この呼び出しは、ルールなしで確かめられる読み取りだけなので、通しました。"
        )
    else:
        what = "ccnavi の内部の不具合で判定できなかったため、この呼び出しを止めました。"
    return "\n".join(
        [
            f"[ccnavi] {code}",
            f"{what}原因は呼び出しではなく ccnavi にあります（{kind}）。詳細は診断ログ "
            f"{log_path} にあります。同じ呼び出しを打ち直して回避しないでください。"
            "ユーザに、ここで ccnavi が失敗したことと診断ログの場所を伝え、続け方を任せてください。",
        ]
    )


def internal_error_notice(code: str, event: str, kind: str, log_path: str) -> str:
    """ccnavi 自身の例外で、ツールの呼び出しを持たないイベントの処理を終えられなかったときに、
    ユーザへ見せる文。止めていないことと、診断ログの場所を言う。"""
    return (
        f"[ccnavi] {code}: {event} の処理中に ccnavi の内部の不具合が起き（{kind}）、"
        f"そこでの処理を飛ばしました。止めたものはありません。詳細は診断ログ {log_path} にあります。"
    )


# 判定に達しなかった回（payload 不正・期限切れ）に、直す側として名指しする先。
_CHECK_SETUP = (
    "the ccnavi hook registration in .claude/settings.json and whether this ccnavi build "
    "matches the Claude Code version"
)

# payload を読めずに止めた（enable の exit 2）とき、イベントごとに何が起きたかと次の一手。
# exit 2 の意味と標準エラーの宛先はイベントで違う。PreToolUse は呼び出しを止めてモデルへ、
# PostToolUse は走った後でモデルへ、UserPromptSubmit はユーザの発言を止めてユーザへ、Stop と
# SubagentStop は終わりを止めてモデルへ届く。ほかのイベントは止まらず、ユーザにだけ見える。
_PAYLOAD_STOPPED = {
    hookio.PRE_TOOL_USE: (
        "so it could not judge this call and stopped it. Rewriting the call will not help. "
        f"Tell the user, so they can check {_CHECK_SETUP}."
    ),
    hookio.POST_TOOL_USE: (
        "so it could not check what this call changed. The call has already run, and this is "
        "not about what you wrote. Do not retry it. "
        f"Tell the user, so they can check {_CHECK_SETUP}."
    ),
    hookio.USER_PROMPT_SUBMIT: (
        "so it held this message back before Claude saw it. Sending it again will not help "
        f"until someone checks {_CHECK_SETUP}."
    ),
    hookio.STOP: (
        "so it could not check the end of this turn and kept it from ending. This is not "
        "about your work. Do not go on with the task: tell the user, so they can check "
        f"{_CHECK_SETUP}, and end the turn."
    ),
    hookio.SUBAGENT_STOP: (
        "so it could not check this subagent's work and kept it from ending. This is not "
        "about your work. Do not go on with the task: put this in your final report, so the "
        f"user can check {_CHECK_SETUP}, and end."
    ),
}


def payload_unusable(code: str, problem: str, *, event: str = "", mode: str = "") -> str:
    """hook の入力（payload）を読めなかったときの文。

    ccnavi は何の呼び出しかも分からないので、enable では止める（`modes.fail_closed`）。呼び出しを
    書き直しても入力は変わらず、直せるのは hook の登録か ccnavi・Claude Code の版を合わせる
    ユーザだけ。

    文はイベントとモードに合わせる。exit 2 が何を止め、標準エラーが誰に届くかはイベントで違い、
    UserPromptSubmit で「この呼び出しを止めた」と言うと、ユーザが自分の発言についての文として
    読めない。ツールの呼び出しを持たないイベントは、dry-run なら exit 0 で通すので「止めた」と
    言わない（PreToolUse とイベントすら読めなかった回は dry-run でも止める）。event は
    イベント名すら読めなかった回は空で、そのときは何を止めたかを言わない。
    """
    head = f"[ccnavi] {code}"
    said = f"ccnavi could not read the hook input from Claude Code ({problem}), "
    # ツールの呼び出しを判定するイベント（と、イベントすら読めなかった回）は dry-run でも止める
    # （`modes.fail_closed`）。ほかのイベントは、dry-run なら通す。
    if mode == DRY_RUN and event not in ("", hookio.PRE_TOOL_USE):
        return "\n".join(
            [
                head,
                said + "so it could not check this "
                f"{event or 'hook'} event. CCNAVI_MODE is dry-run, so nothing was stopped; under "
                f"enable it would have been. The user needs to check {_CHECK_SETUP}.",
            ]
        )
    stopped = _PAYLOAD_STOPPED.get(event)
    if stopped is None:
        stopped = (
            f"so it could not check this {event or 'hook'} event and stopped with exit code 2. "
            "This is not about anything that was written, and repeating it will not help. "
            f"The user needs to check {_CHECK_SETUP}."
        )
    return "\n".join([head, said + stopped])


def deadline_exceeded(code: str, seconds: float, *, passed: bool = False) -> str:
    """判定が期限に届かなかったときの文。

    照合が長引くのは、コマンドの文字列が長いか、`>` のような照合を伸ばす形が多いとき。
    短く分ければ通る見込みがある。同じ形で続けて止まるなら、ルールの側の問題なので
    ユーザに伝えさせる。

    期限を見るのは実行前チェック（PreToolUse）だけなので、宛先はツール呼び出しを書いたモデル。
    dry-run は、設定を読まずに安全と分かる読み取りだけ exit 0 で通すので、その回は「止めた」と
    言わず、enable なら止めていたと言う。それ以外の呼び出しは dry-run でも止める。
    """
    if passed:
        what = (
            "This call is a plain read that ccnavi can check without its rules, so it went through."
        )
    else:
        what = "so it stopped it."
    return "\n".join(
        [
            f"[ccnavi] {code}",
            f"ccnavi ran out of time ({seconds:g} s) before it finished judging this call"
            + (". " if passed else ", ")
            + what
            + " Long commands and long inline text make matching slow: split the command "
            "into smaller ones, or write long text to a file with the Write tool and pass the "
            "file. If a short command keeps hitting this, tell the user.",
        ]
    )


def _commit_file(git: str | None) -> str:
    """ファイルから本文を渡す入口の案内。ラッパーがあるときだけ `git commit -F` を名指しする。"""
    return f"{git} commit -F <file>, " if git else ""


def rewrite_rule(form: str) -> str:
    """書き直しを求める形で止めたときに、記録の `rules` に残す名前。"""
    return f"builtin:{form}"


def rewrite(subject: str, form: str, found: list[str], git: str | None = None) -> str:
    """書き直しを求める形を止めた文。形ごとに 1 件。

    ルールにヒットしたのではないので、禁止された操作をしたとは言わない。止めたのは読みの
    決めごとで、書き直す方法は必ずある。方法を名指ししないと、同じ形を書き直しては止まる。
    git は案内するラッパーの打ち方（settings.git_wrapper_command）。無いときは None で、
    文面の `{commit}` は書かない。
    """
    shown = _shorten(subject)
    listed = ", ".join(f"`{_one_line(text)}`" for text in found[:_REWRITES_SHOWN])
    if len(found) > _REWRITES_SHOWN:
        listed += f" (+{len(found) - _REWRITES_SHOWN})"
    what, how = _REWRITE_TEXT[form]
    how = how.replace("{commit}", f", {git} commit -F <ファイル>" if git else "")
    return "\n".join(
        [f"[ccnavi] {CODE_REWRITE[form]}", f"subject: {shown}", f"{what}: {listed}", how]
    )


# 止めた文に並べる表記の数。1 つ直せば残りも同じ直し方になる。
_REWRITES_SHOWN = 5

# 形ごとの（見つけたものの呼び名, 書き直し方）。
_REWRITE_TEXT = {
    shellread.FORM_BRACE: (
        "引用の外の波括弧展開",
        "引用の外では、シェルがコマンドを実行する前に波括弧を複数の語に展開します。"
        "そのため、ccnavi が読んだ語と実際に実行される語が一致しません。"
        "bash と zsh で展開の結果が異なるので、ccnavi は結果を推測せず、呼び出しを止めます。"
        "代入や case のパターン、[[ ]] のように展開されない箇所でも止まります。"
        "語を書き出してください。"
        "`--exclude-dir={a,b}` は `--exclude-dir=a --exclude-dir=b`、"
        "`cp f{,.bak}` は `cp f f.bak`、`{1..3}` は `1 2 3` と書きます。"
        "波括弧を文字として使うなら、シングルクォートで囲んでください（'{a,b}'）。",
    ),
    shellread.FORM_COMMAND_NAME: (
        "実行時に決まるコマンド名",
        "シェルは、このコマンド名を実行するときに変数・コマンド置換・グロブから決めます。"
        "どのプログラムが走るかが表記から分からないので、どのルールも適用されません。"
        "そのため、呼び出しを止めます。"
        "コマンド名を直接書いてください。"
        "`c=ls; $c -la` は `ls -la`、"
        '`"$(command -v python3)" x.py` は `python3 x.py`、'
        "`/usr/bin/l? -la` は `/usr/bin/ls -la` と書きます。"
        "sh や source に渡すスクリプトも、`sh $SCRIPT` ではなくパスを直接書いてください。",
    ),
    shellread.FORM_BACKQUOTE: (
        "バッククォート",
        "バッククォートの中身は、二重引用符の中や引用なしの here-doc でもシェルが実行します。"
        "ccnavi はバッククォートを読まないので、呼び出しを止めます。"
        "コマンド置換には `$( )` を使ってください。"
        "バッククォートを文字として残すなら（Issue や PR 本文の Markdown など）、"
        "シングルクォートで囲むか、1 つずつ \\` とエスケープするか、ファイルから渡してください"
        "（`glab --description-file <ファイル>`、gh なら `--body-file`{commit}）。",
    ),
    shellread.FORM_AMBIGUOUS: (
        "読みが分かれる形",
        "bash と zsh で読みが分かれる形か、入れ子が深すぎる形なので、"
        "実行される内容を確定できません。"
        "そのため、呼び出しを止めます。"
        "素直な形に書き直してください。"
        "`$( )` の中の case は if と elif にしてください。"
        "`$((cmd) | x)` は算術式と読まれるので、`$( (cmd) | x )` と空白を入れてください。"
        "内側のコマンドを先に実行し、その値を次のコマンドに書いてください。"
        "入れ子の `$( )` を深くしないでください。"
        "coproc の代わりに `&` を、select の代わりに for を使ってください。",
    ),
}
