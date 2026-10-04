"""状態の置き場を守る組み込みのルールと、提案を書いた回に渡す承認の前の確認の文。

ticket から分けた。ticket を読まない。
"""

from __future__ import annotations

import re
from typing import TextIO

from ..infra import hookio, settings
from ..policy import ctxfile, rules, selfguard
from . import ticket_model

STATE_RULE_ID = "builtin-ticket-state"
# 提案を書いた回に、承認を頼む前の確認を伝えるルールの id。
PROPOSE_RULE_ID = "builtin-ticket-propose"


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
            rf"{selfguard._WRITE_VERBS}{loose}",
            selfguard.copy_destination_regex(selfguard.copy_last_place(review), loose),
            selfguard.holder_regex(selfguard.under(_place(tickets_rel)), states),
        )
    )
    shell_rule = rules.Rule(
        id=STATE_RULE_ID + "-shell",
        match="Bash|PowerShell",
        regex=shell,
        message=message,
        decision=rules.DENY,
    )
    shell_rule.compiled = re.compile(shell, re.IGNORECASE)
    return [write_rule, shell_rule]


def propose_notice(
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    payload: hookio.Input,
    subject: str,
) -> str:
    """提案を書いた回に、承認を頼む前の確認を 1 度だけ伝える文（REQ-APV-14）。空なら渡さない。

    承認できない提案のままユーザに承認を頼むと、落ちたことを知るのが端末に座ったユーザになり、
    往復が 1 回増える。確かめる手立て（`--agree --preview --verify`）は在るので、
    書いた直後に、要る場所で言う。

    **判定には足さない。** これは文であって判定ではないので、ルールの表（`rule_set`）には
    入れず、ルールの文と同じ経路（PreToolUse の `additionalContext`）で渡す。表に allow を
    1 本足す形は採らない。次の 3 つを一緒に引き受けることになるため。

    - `todo/` が「ccnavi が言及する場所」になり、どのタイプも言及しないときの扱い
      （`judge.undeclared_verdict`）を通らなくなる。確認できる者が居ないモードの deny も、
      知らない表記のモードを ask として扱う既定も、そこだけ外れる（REQ-PRE-08）
    - 判定は強いタイプから見て最初に当たった段で決まるので、**提案の置き場に `deny` か
      `ask` を書いているワークスペースには文が届かない。** 承認の流れをいちばん
      気にしているところにだけ届かない、という向きになる
    - 組み込みで持つのは、ガード自身を守るものと取り返しの付かない操作だけ（設計 P4）。
      助言はそのどちらでもない

    渡るのは書き込みの**前**（PreToolUse）で、止まった回にも届く。文面を「書きました」と
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
    """承認待ちの置き場に当たる式。**ワークスペースの中に限る。**

    `guard_rules` は前を問わない形（`(^|[\\/])`）で当てるが、あれは deny なので、余分に
    当たるぶんは止めすぎる側へ外れるだけ。文を渡す側を同じ形で当てると、ワークスペースの
    外に `wip/proposals/todo/` という構成のディレクトリを作っただけの場所でも
    「提案を書いた」と読む。
    ツリー（ワークツリー・プロジェクト）はどれもワークスペースルートの下（設計 11)
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
            "書いた提案の frontmatter が壊れていても（承認待ちに並ばないので）気づけません。"
        ),
    )
