"""状態の置き場を守る組み込みのルール、生の git を止める組み込みのルールと、提案を書いた回に渡す
承認の前の確認の文。

ticket から分けた。ticket を読まない。
"""

from __future__ import annotations

import re
from typing import TextIO

from ...infra import hookio, settings
from ...policy import ctxfile, rules, selfguard_shell, texts
from . import ticket_model

STATE_RULE_ID = "builtin:ticket-state"
# 同じ置き場をシェルからの書き込みで守る 1 本。
STATE_SHELL_RULE_ID = STATE_RULE_ID + "-shell"
# 提案を書いた回に、承認を頼む前の確認を伝えるルールの id。
PROPOSE_RULE_ID = "builtin:ticket-propose"
# 生の git を止め、入口を git のラッパー 1 本に寄せるルールの id。ccnavi 自身の約束を守るので、
# ルールファイルからは書けない（予約の接頭辞 `builtin:guard-`）。
RAW_GIT_RULE_ID = "builtin:guard-raw-git"

# `git` がコマンドの位置に立つ形。glob の `*git*` だと誘導先の `ccnavi-git.sh` にもヒットして
# 出口ごと塞がるので、前後の切れ目を見る。
#
# 適用するのは、引用とコメントを外してコマンドの切れ目に `\x00` を置いた読み（shellread）なので、
# コマンドの位置は行頭か `\x00` の直後になる。`env` `sudo` `command` `xargs` `nohup` `time` などの
# 前置きは、読みが外した中のコマンド（`git status`）の層に適用して止める。`grep git README.md` の
# ような引数の `git` には適用しない。読み切れずに生の文字列に適用する回のために、シェルの区切り
# （改行 `;` `&` `|` `(` `{` `` ` `` `!`）の後ろも位置に数え、その直後に続く `then` `do` などの
# 予約語も読み飛ばす（`echo to do git` の `do` は区切りの直後ではないので数えない）。
# パス付き（`/usr/bin/git`・`C:\Git\git.exe`）と `git.exe` も同じ。パスの途中の字に区切りを
# 入れないのは、区切りの字ごとに残りを読み直さないため（区切りの後ろからは、
# その区切りでヒットする）。
# 引用しない `C:\Git\git.exe` はシェルが `\` を外して `C:Gitgit.exe` と読むので、ドライブ名
# （`C:`）で始まり `git` / `git.exe` で終わる語も止める（実際には走らない形だが、止める側に倒す）。
# 共通レイヤーの見本（位置を問わず適用する `raw-git`）は削除した（ADR-0131）。
# その結果、コマンドの位置に無い git は止まらなくなった。
_SEPARATOR = r"[\x00\n;&|({`!]"
_KEYWORD = r"(?:then|do|else|elif|if|while|until|time)\s++"
_PATH_CHAR = r"[^\s\x00\\/;&|({`!]"
RAW_GIT_REGEX = (
    rf"(?:^|{_SEPARATOR})\s*+(?:{_KEYWORD})*+"
    rf"(?:(?:{_PATH_CHAR}*+[\\/])*+|[a-z]:{_PATH_CHAR}*?)git(?:\.exe)?\s"
)


def _place(tickets_rel: str) -> str:
    """置き場のパスにヒットする式。プロジェクトの名前を挟んだ形（`wip/<name>/proposals`）にもヒットする。"""
    parts = [re.escape(p) for p in tickets_rel.split("/") if p]
    optional = r"(?:[^\\/\s\x00]+[\\/])?"
    if len(parts) < 2:
        return optional + r"[\\/]".join(parts)
    return parts[0] + r"[\\/]" + optional + r"[\\/]".join(parts[1:])


def state_dir_regex(tickets_rel: str) -> str:
    """直接の作成・移動を止める置き場にヒットする式。パス用。"""
    return rf"(^|[\\/]){_place(tickets_rel)}[\\/]({'|'.join(ticket_model.GUARDED_STATES)})[\\/]"


def guard_rules(tickets_rel: str, root: str) -> list[rules.Rule]:
    """状態の置き場を守るルール。組み込みで、ルールファイルには書かない。

    通るのは状態を動かすスクリプトだけ。そのスクリプトの呼び出し文字列には
    置き場のパスが現れないので、ここにヒットしない。root は文面の sh のパスに使う。
    """
    place = state_dir_regex(tickets_rel)
    spec = texts.spec("RULE_TICKET_STATE")
    write_rule = rules.Rule(
        id=STATE_RULE_ID,
        match="|".join(ticket_model.WRITE_TOOLS),
        regex=place,
        root=root,
        decision=rules.DENY,
        **spec,
    )
    # `DONE/` も同じ場所として適用する（どの機械でも区別しない）。状態の名前を大文字で書く
    # 正当な用事は無い。
    write_rule.compiled = re.compile(place, re.IGNORECASE)
    # シェルの側は前の区切りを求めない。コマンドの引数は空白で区切られていて、
    # 書き込む動詞の式が引数までを覆う。行き先は末尾の `/` が無いパス
    # （`mv x wip/proposals/done`）でも適用する。
    states = "|".join(ticket_model.GUARDED_STATES)
    review = _place(tickets_rel) + rf"[\\/]({states})"
    loose = review + r"([\\/]|\s|$)"
    # コピーする動詞は行き先だけで適用する。置き場から外へコピーする読み向きの cp は通す。行き先の
    # 読み方（`-t` の値か、選択肢でない最後の引数）は組み込みの保護と同じ部品を使う。
    # ここで別に書くと、
    # `cp -t <置き場> <提案>` のように片方だけが読む書き方が通る。
    # 状態の置き場が入っているディレクトリ（提案の置き場）を行き先にして、元の名前を状態の名前に
    # した形（`cp -r /tmp/review wip/proposals/`）も同じ部品で止める。
    shell = "|".join(
        (
            rf"{selfguard_shell._WRITE_VERBS}{loose}",
            selfguard_shell.copy_destination_regex(selfguard_shell.copy_last_place(review), loose),
            selfguard_shell.holder_regex(selfguard_shell.under(_place(tickets_rel)), states),
        )
    )
    shell_rule = rules.Rule(
        id=STATE_SHELL_RULE_ID,
        match="Bash|PowerShell",
        regex=shell,
        root=root,
        decision=rules.DENY,
        **spec,
    )
    shell_rule.compiled = re.compile(shell, re.IGNORECASE)
    return [write_rule, shell_rule]


def _dir_rules(
    rule_id: str, shell_id: str, place_rel: str, spec: dict, root: str
) -> tuple[rules.Rule, rules.Rule]:
    """置き場のディレクトリ全体への書き込みを止める 2 本（ツールの書き込みとシェルの書き込み）。

    `guard_rules` が状態の名前を絞るのに対し、ここは置き場の下すべてを守る。
    """
    place = _place(place_rel)
    path = rf"(^|[\\/]){place}[\\/]"
    write_rule = rules.Rule(
        id=rule_id,
        match="|".join(ticket_model.WRITE_TOOLS),
        regex=path,
        root=root,
        decision=rules.DENY,
        **spec,
    )
    write_rule.compiled = re.compile(path, re.IGNORECASE)
    loose = place + r"([\\/]|\s|$)"
    # 書き込み動詞の列挙（リダイレクト、mv・rm・tee・dd・sed -i、cp の行き先）に加えて、
    # 置き場の名前を引数に取る作成・変更・展開の道具と、コードを走らせる道具も止める。
    # 置き場の名前が現れるだけでヒットするので、
    # 読み取りの道具（cat・grep・ls・head）は巻き込まない。
    also = (
        r"(^|\x00|[;&|]\s*)(touch|mkdir|chmod|chown|rsync|sponge|unzip|tar|python3?|node|perl|ruby"
        r"|Set-Content|Add-Content|New-Item|Copy-Item|Move-Item|Remove-Item|Out-File)\b"
        rf"[^\x00]*{place}"
    )
    shell = "|".join(
        (
            rf"{selfguard_shell._WRITE_VERBS}{loose}",
            selfguard_shell.copy_destination_regex(selfguard_shell.copy_last_place(place), loose),
            also,
        )
    )
    shell_rule = rules.Rule(
        id=shell_id,
        match="Bash|PowerShell",
        regex=shell,
        root=root,
        decision=rules.DENY,
        **spec,
    )
    shell_rule.compiled = re.compile(shell, re.IGNORECASE)
    return write_rule, shell_rule


APPROVED_RULE_ID = "builtin:ticket-approved"
APPROVED_SHELL_RULE_ID = APPROVED_RULE_ID + "-shell"
OPS_RULE_ID = "builtin:ticket-ops"
OPS_SHELL_RULE_ID = OPS_RULE_ID + "-shell"

# 操作記録の置き場（`logs/state/ops/`）。置き場の位置は設定で動かない（`settings.DEFAULT_STATE`）。
OPS_REL = "logs/state/ops"


def approved_write_rule(approved_rel: str, root: str) -> rules.Rule:
    """承認済みの置き場を名前で書くツールから守る 1 本（`builtin:ticket-approved`）。

    実行後の保護範囲（`events.watched_for`）に足す。シェル版は足さない。実行後はワークツリーの
    変更を見るため。実行前の関門（`gate_rules`）と同じ式・同じ文面。
    """
    return _dir_rules(
        APPROVED_RULE_ID,
        APPROVED_SHELL_RULE_ID,
        approved_rel,
        texts.spec("RULE_TICKET_APPROVED"),
        root,
    )[0]


def gate_rules(approved_rel: str, root: str) -> list[rules.Rule]:
    """承認済みの置き場と操作記録の置き場を守るルール。組み込みで、ルールファイルには書かない。

    承認済みの置き場（承認済みチケット、`doing/`・`done/`、`phases/` のマーカー・子の記録・
    受け入れ台帳、`flows/`）は、動かせるのは ccnavi のスクリプトとユーザの操作だけ。エージェントの
    直接の書き込みは、dry-run でも `CCNAVI_GUARD_CORE_FILES` の値でも止める
    （チケット制御が有効な間）。
    操作記録（判断の保存と送信の段階）も同じ。書き直すと、送り直しの根拠を偽れる。
    """
    return [
        *_dir_rules(
            APPROVED_RULE_ID,
            APPROVED_SHELL_RULE_ID,
            approved_rel,
            texts.spec("RULE_TICKET_APPROVED"),
            root,
        ),
        *_dir_rules(OPS_RULE_ID, OPS_SHELL_RULE_ID, OPS_REL, texts.spec("RULE_TICKET_OPS"), root),
    ]


def raw_git_rule(root: str) -> rules.Rule:
    """生の git を止めるルール。組み込みで、ルールファイルには書かない。いつも足す。

    git のラッパー（`ccnavi-git.sh`）の中には、チケット制御の約束を守る可否がある。
    承認済みチケットの置き場を別のコミットの中身に戻す `restore` と `checkout` を通さない、
    直接作業だけを取り込む `integrate`、子のワークツリーから送らない `push`、
    親のブランチへ移るときに承認済みチケットを運ぶ `checkout` など。どれも、git が
    ラッパーの外から打てれば外れる。そこで生の git は、チケット制御の有無や共通レイヤーの
    有無によらず止める。

    ラッパーが無いワークスペースでも止める。そのときは誘導先が無いので、文面は
    ユーザに頼む形にする（`git` の形を案内すると、それも止まる）。

    土台（`base`）として扱う。ほかのレイヤーの deny にもヒットした回は、
    そちらだけを言う（拒否は変わらない）。
    """
    wrapper = settings.has_git_wrapper(root)
    rule = rules.Rule(
        id=RAW_GIT_RULE_ID,
        match="Bash",
        regex=RAW_GIT_REGEX,
        root=root,
        decision=rules.DENY,
        **texts.spec("RULE_RAW_GIT", wrapper=wrapper, cased=("why", "next")),
    )
    rule.compiled = re.compile(RAW_GIT_REGEX, re.IGNORECASE)
    rule.base = True
    return rule


def propose_notice(
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    payload: hookio.Input,
    subject: str,
) -> str:
    """提案を書いた回に、承認を頼む前の確認を 1 度だけ伝える文。空なら渡さない。

    承認できない提案のままユーザに承認を頼むと、通らなかったことを知るのがターミナルに座ったユーザになり、
    往復が 1 回増える。確かめる手立て（`--agree --preview --verify`）は在るので、
    書いた直後に、要る場所で言う。

    判定には足さない。 これは文であって判定ではないので、ルールの表（`rule_set`）には
    入れず、ルールの文と同じ経路（PreToolUse の `additionalContext`）で渡す。表に allow を
    1 本足す形は採らない。次の 3 つを一緒に引き受けることになるため。

    - `todo/` が「ccnavi が言及する場所」になり、どのタイプも言及しないときの扱い
      （`judge.undeclared_verdict`）を通らなくなる。確認できる者が居ないモードの deny も、
      知らない表記のモードを ask として扱う既定も、そこだけ外れる
    - 判定は強いタイプから見て最初にヒットした段で決まるので、提案の置き場に `deny` か
      `ask` を書いているワークスペースには文が届かない。 承認の流れをいちばん
      気にしているところにだけ届かない、という向きになる
    - 組み込みで持つのは、ガード自身を守るものと取り返しの付かない操作だけ。
      助言はそのどちらでもない

    渡るのは書き込みの前（PreToolUse）で、止まった回にも届く。文面を「書きました」と
    過去形にしないのはそのため。書けたかどうかは、この文を渡す時点では決まっていない。

    1 つの文脈（セッション、サブエージェントなら 1 回の起動）で最初の 1 回だけ渡る。
    数えは `ctxfile` の記録を、ルールと同じ形で使う（この 1 本は表に入れないので、
    判定には一切現れない）。提案を 1 本書くたびに同じ文を積むと、
    長いセッションではそれだけでコンテキストを使ってしまう。
    """
    if payload.tool_name not in ticket_model.WRITE_TOOLS or not subject:
        return ""
    if not _propose_place(conf.tickets, root).search(subject):
        return ""
    return ctxfile.for_rules(stderr, conf.state, payload, [_propose_rule(conf.tickets, conf.bin)])


def _propose_place(tickets_rel: str, root: str) -> re.Pattern:
    """承認待ちの置き場にヒットする式。ワークスペースの中に限る。

    `guard_rules` は前を問わない形（`(^|[\\/])`）で適用するが、あれは deny なので、余分に
    ヒットするぶんは止めすぎる側へ外れるだけ。文を渡す側を同じ形で適用すると、ワークスペースの
    外に `wip/proposals/todo/` という構成のディレクトリを作っただけの場所でも
    「提案を書いた」と読む。
    ツリー（ワークツリー・プロジェクト）はどれもワークスペースルートの下
    なので、ルートの下に限れば正しい置き場は全部入り、外は入らない。

    `TODO/` も同じ場所（大文字小文字はどの機械でも区別しない）。`guard_rules` と揃える。
    """
    place = (
        rf"^{rules.root_pattern(root)}[\\/][^\x00]*{_place(tickets_rel)}"
        rf"[\\/]{ticket_model.TODO}[\\/]"
    )
    return re.compile(place, re.IGNORECASE)


def _propose_rule(tickets_rel: str, bin_path: str) -> rules.Rule:
    """文と、数えのキーになる id を持つ入れ物。表には入れない（`propose_notice` だけが持つ）。"""
    return rules.Rule(
        id=PROPOSE_RULE_ID,
        additional_context_once=(
            f"チケットの提案を書こうとしています（{tickets_rel}/todo/ は承認待ちの置き場で、"
            "ここに書いただけでは判定の対象になりません）。"
            "ユーザに承認を依頼する前に、"
            "'sh .ccnavi/scripts/ccnavi-ticket.sh approval-request <親の識別子> --check' で"
            "承認できる状態かを確かめてください。承認済みチケットは置きません。"
            "終了コードが答えです。0 なら前提は通っています"
            "（approval-request（--check なし）で送ります）。"
            "1 なら理由が出ます（承認の対象に入らない、親のワークツリーに未コミットの変更がある、"
            "ホストや git のリモートが使えない、など。何も送っていません）。"
            "直してから打ち直してください。"
            "**識別子には、いま書いたものを渡してください。** "
            "省くと識別子が無いと言われて止まります。"
        ),
    )
