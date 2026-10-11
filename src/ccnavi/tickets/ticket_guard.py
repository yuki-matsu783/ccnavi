"""状態の置き場を守る組み込みのルール、生の git を止める組み込みのルールと、提案を書いた回に渡す
承認の前の確認の文。

ticket から分けた。ticket を読まない。
"""

from __future__ import annotations

import os
import re
from typing import TextIO

from ..infra import hookio, settings
from ..policy import ctxfile, rules, selfguard_shell
from . import ticket_model

STATE_RULE_ID = "builtin-ticket-state"
# 同じ置き場をシェルからの書き込みで守る 1 本。
STATE_SHELL_RULE_ID = STATE_RULE_ID + "-shell"
# 提案を書いた回に、承認を頼む前の確認を伝えるルールの id。
PROPOSE_RULE_ID = "builtin-ticket-propose"
# 生の git を止め、入口を git のラッパー 1 本に寄せるルールの id。
RAW_GIT_RULE_ID = "builtin-ticket-raw-git"

# git のラッパーの置き場（ワークスペースルートからの相対）。
# スクリプトはワークスペースにしか無い（`settings.script_command`）ので、ルートの下だけを見る。
GIT_SCRIPT = os.path.join(settings.DEFAULT_PROJECT_HOME, "scripts", "ccnavi-git.sh")

# `git` がコマンドの位置に立つ形。glob の `*git*` だと誘導先の `ccnavi-git.sh` にも当たって
# 出口ごと塞がるので、前後の切れ目を見る。
#
# 当てるのは、引用とコメントを外してコマンドの切れ目に `\x00` を置いた読み（shellread）なので、
# コマンドの位置は行頭か `\x00` の直後になる。`env` `sudo` `command` `xargs` `nohup` `time` などの
# 前置きは、読みが外した中のコマンド（`git status`）の層に当てて止める。`grep git README.md` の
# ような引数の `git` には当てない。読み切れずに生の文字列に当てる回のために、シェルの区切り
# （改行 `;` `&` `|` `(` `{` `` ` `` `!`）と、`then` `do` などの予約語の後ろも位置に数える。
# パス付き（`/usr/bin/git`）と `git.exe` も同じ。パスの途中の字に区切りを入れないのは、区切りの
# 字ごとに残りを読み直さないため（区切りの後ろからは、その区切りで当たる）。
# 共通レイヤーの見本の `raw-git` は位置を問わずに当てる。そちらは緩める向きになるので変えていない。
RAW_GIT_REGEX = (
    r"(?:^|[\x00\n;&|({`!]|\b(?:then|do|else|elif|if|while|until)\s)\s*+"
    r"(?:[^\s\x00\\/;&|({`!]*+[\\/])*+git(?:\.exe)?\s"
)


def _place(tickets_rel: str) -> str:
    """置き場のパスに当たる式。プロジェクトの名前を挟んだ形（`wip/<name>/proposals`）にも当たる。"""
    parts = [re.escape(p) for p in tickets_rel.split("/") if p]
    optional = r"(?:[^\\/\s\x00]+[\\/])?"
    if len(parts) < 2:
        return optional + r"[\\/]".join(parts)
    return parts[0] + r"[\\/]" + optional + r"[\\/]".join(parts[1:])


def state_dir_regex(tickets_rel: str) -> str:
    """直接の作成・移動を止める置き場に当たる式。パス用。"""
    return rf"(^|[\\/]){_place(tickets_rel)}[\\/]({'|'.join(ticket_model.GUARDED_STATES)})[\\/]"


def guard_rules(tickets_rel: str, root: str) -> list[rules.Rule]:
    """状態の置き場を守るルール。組み込みで、ルールファイルには書かない。

    通るのは状態を動かすスクリプトだけ。そのスクリプトの呼び出し文字列には
    置き場のパスが現れないので、ここに当たらない。root は文面の sh のパスに使う。
    """
    place = state_dir_regex(tickets_rel)
    message = (
        "チケットの状態は置き場で表します。review/（レビュー待ち）へ動かすのは "
        f"'{settings.script_command(root, 'ccnavi-ticket.sh')} finish <識別子>' だけです。"
        "直接ファイルを作ったり動かしたりしないでください。todo/ への作成と編集は自由です。"
    )
    write_rule = rules.Rule(
        id=STATE_RULE_ID,
        match="|".join(ticket_model.WRITE_TOOLS),
        regex=place,
        message=message,
        decision=rules.DENY,
    )
    # 大文字小文字を区別しない機械では `DONE/` も同じ場所。区別する機械で余分に
    # 当たっても、状態の名前を大文字で書く正当な用事は無い。
    write_rule.compiled = re.compile(place, re.IGNORECASE)
    # シェルの側は前の区切りを求めない。コマンドの引数は空白で区切られていて、
    # 書き込む動詞の式が引数までを覆う。行き先は末尾の `/` が無いパス
    # （`mv x wip/proposals/done`）でも当てる。
    states = "|".join(ticket_model.GUARDED_STATES)
    review = _place(tickets_rel) + rf"[\\/]({states})"
    loose = review + r"([\\/]|\s|$)"
    # コピーする動詞は行き先だけで当てる。置き場から外へコピーする読み向きの cp は止めない。行き先の
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
        message=message,
        decision=rules.DENY,
    )
    shell_rule.compiled = re.compile(shell, re.IGNORECASE)
    return [write_rule, shell_rule]


def raw_git_applies(conf: settings.Settings, root: str) -> bool:
    """生の git を組み込みで止めるか。チケット制御が有効で、誘導先のラッパーがあるときだけ。

    ラッパーが無いワークスペースで止めると、git を打つ道が 1 つも残らない。
    """
    return conf.tickets_enabled and os.path.isfile(os.path.join(root, GIT_SCRIPT))


def raw_git_rule(root: str) -> rules.Rule:
    """生の git を止めるルール。組み込みで、ルールファイルには書かない。

    git のラッパー（`ccnavi-git.sh`）の中には、チケット制御の約束を守る可否がある。
    承認済みチケットの置き場を別のコミットの中身に戻す `restore` と `checkout` を通さない、
    直接作業だけを取り込む `integrate`、子のワークツリーから送らない `push`、
    親のブランチへ移るときに承認済みチケットを運ぶ `checkout` など。どれも、git が
    ラッパーの外から打てれば外れる。生の git を止めるのが共通レイヤーのルールだけだと、
    共通レイヤーは置かなくてよいので、ccnavi 自身の約束がワークスペースの設定しだいで消える。
    そこで止める側を組み込みに持つ。

    土台（`base`）として扱う。共通レイヤーの `raw-git` にも当たったときは、そちらだけを言う。
    拒否は変わらず、同じ拒否を 2 つの文面で返さない。

    共通レイヤーが読めずに組み込みの既定に戻っている間も足す（judge がチケット制御の
    組み込みと同じ条件で足す）。ラッパーはルールファイルを読まずに動き、衝突の解決に要る
    `add`・`restore --ours` / `--theirs`・`merge-file -p` を通すので、直す道は残る。
    外すと、ルールファイルが読めない間だけチケット制御の可否がラッパーの外から外れる。
    """
    rule = rules.Rule(
        id=RAW_GIT_RULE_ID,
        match="Bash",
        regex=RAW_GIT_REGEX,
        message=(
            "生の git は実行しません。"
            f"'{settings.script_command(root, 'ccnavi-git.sh')} <サブコマンド> ...'"
            " を使ってください。通る形は同じスクリプトの --help にあります。"
            "チケット制御の可否（承認済みチケットの置き場を戻さない・取り込み・push の条件）は"
            "このスクリプトが確かめるので、生の git では外れます。"
        ),
        decision=rules.DENY,
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

    承認できない提案のままユーザに承認を頼むと、通らなかったことを知るのが端末に座ったユーザになり、
    往復が 1 回増える。確かめる手立て（`--agree --preview --verify`）は在るので、
    書いた直後に、要る場所で言う。

    判定には足さない。 これは文であって判定ではないので、ルールの表（`rule_set`）には
    入れず、ルールの文と同じ経路（PreToolUse の `additionalContext`）で渡す。表に allow を
    1 本足す形は採らない。次の 3 つを一緒に引き受けることになるため。

    - `todo/` が「ccnavi が言及する場所」になり、どのタイプも言及しないときの扱い
      （`judge.undeclared_verdict`）を通らなくなる。確認できる者が居ないモードの deny も、
      知らない表記のモードを ask として扱う既定も、そこだけ外れる
    - 判定は強いタイプから見て最初に当たった段で決まるので、提案の置き場に `deny` か
      `ask` を書いているワークスペースには文が届かない。 承認の流れをいちばん
      気にしているところにだけ届かない、という向きになる
    - 組み込みで持つのは、ガード自身を守るものと取り返しの付かない操作だけ。
      助言はそのどちらでもない

    渡るのは書き込みの前（PreToolUse）で、止まった回にも届く。文面を「書きました」と
    過去形にしないのはそのため。書けたかどうかは、この文を渡す時点では決まっていない。

    1 つの文脈（セッション、サブエージェントなら 1 回の起動）で最初の 1 回だけ渡る。
    数えは `ctxfile` の記録を、ルールと同じ形で使う（この 1 本は表に入れないので、
    判定には一切現れない）。2 本目からは何も渡さない。提案を 1 本書くたびに同じ文を積むと、
    長いセッションではそれだけでコンテキストを使ってしまう。
    """
    if payload.tool_name not in ticket_model.WRITE_TOOLS or not subject:
        return ""
    if not _propose_place(conf.tickets, root).search(subject):
        return ""
    return ctxfile.for_rules(stderr, conf.state, payload, [_propose_rule(conf.tickets, conf.bin)])


def _propose_place(tickets_rel: str, root: str) -> re.Pattern:
    """承認待ちの置き場に当たる式。ワークスペースの中に限る。

    `guard_rules` は前を問わない形（`(^|[\\/])`）で当てるが、あれは deny なので、余分に
    当たるぶんは止めすぎる側へ外れるだけ。文を渡す側を同じ形で当てると、ワークスペースの
    外に `wip/proposals/todo/` という構成のディレクトリを作っただけの場所でも
    「提案を書いた」と読む。
    ツリー（ワークツリー・プロジェクト）はどれもワークスペースルートの下
    なので、ルートの下に限れば正しい置き場は全部入り、外は入らない。

    大文字小文字は区別しない機械では `TODO/` も同じ場所。`guard_rules` と揃える。
    """
    place = (
        rf"^{rules.root_pattern(root)}[\\/][^\x00]*{_place(tickets_rel)}"
        rf"[\\/]{ticket_model.TODO}[\\/]"
    )
    return re.compile(place, re.IGNORECASE)


def _propose_rule(tickets_rel: str, bin_path: str) -> rules.Rule:
    """文と、数えの鍵になる id を持つ入れ物。表には入れない（`propose_notice` だけが持つ）。"""
    return rules.Rule(
        id=PROPOSE_RULE_ID,
        additional_context_once=(
            f"チケットの提案を書こうとしています（{tickets_rel}/todo/ は承認待ちの置き場で、"
            "ここに書いただけでは判定には効きません）。"
            "ユーザに承認を依頼する前に、"
            f"'{settings.bin_command(bin_path)} --agree --preview --verify <識別子>' で"
            "承認できる状態かを確かめてください。承認済みチケットは置きません。"
            "終了コードが答えです。0 なら依頼してよく、3 なら承認の対象に入らない理由が出ます"
            "（親が承認されていない、計画に無いフェーズ、順序、承認待ちに無い識別子）。"
            "直してから依頼してください。1 は打ち方か設定の誤りで、提案の問題ではありません。"
            "**識別子には、いま書いたものを渡してください。** 省くと承認待ち全部が対象になり、"
            "書いた提案の frontmatter の形が正しくなくても（承認待ちに並ばないので）気づけません。"
        ),
    )
