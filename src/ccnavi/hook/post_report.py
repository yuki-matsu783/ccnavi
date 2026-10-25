"""実行後チェックの差し戻しの文。違反と、前からあった変更の知らせ。

post から分けた。post を読まない。
"""

from __future__ import annotations

from ..infra import gitstate, hookio, settings
from ..policy import rules, selfguard
from . import post_findings

# 前から在った変更には別のコードを使う。同じコードで送ると、受け取った側に
# 「直前の実行が壊したもの」と「元から汚れていたもの」を見分ける方法が無くなる。
CODE_PREEXISTING = "POST_PREEXISTING"

# 対象の文字列を載せるときの長さの上限。
SUBJECT_LIMIT = 200


def _violation(
    finding: post_findings.Finding,
    payload: hookio.Input,
    moved: str | None,
    would_restore: bool = False,
    not_restored: bool = False,
    git: str | None = None,
    shadowed: bool = False,
    side_by_side: bool = False,
) -> str:
    """1 件を、それだけで読んで成立する差し戻しの文に組む。

    実行前の拒否と同じ作りにしてある。何にヒットしたか・どの設定が言っているか・
    直前に何が走ったか・どう戻すか。1 件だけが切り出されて見えても、
    受け取った側がそこから次にすることが分かること。

    would_restore は、戻しが予行のとき。戻す手順はそのまま載せる。今回は
    誰も戻していないので、手順を省くと戻す手立てが 1 つも書かれていない
    報告になる。そのうえで、本番なら ccnavi が戻していたことも書く。

    not_restored は、戻す働きは有効なのに、この 1 件が戻す対象ではない
    とき（`ask` と、承認済みチケットの範囲外。`_restorable`）。手順だけを渡すと、
    受け取ったエージェントには「自分で戻せ」としか読めない。`ask` のルールは
    文面を持てない（lint が禁じる）ので、ここで言わないと誰も言わない。

    shadowed は、ユーザの発言で受け入れた変更のうえに、そのあと中身が変わったもの。戻す先の
    コミット済みの内容は、ユーザの編集より古いので、戻すとその編集まで消える。だから戻さない。
    `not_restored` の理由の文を、これ用に差し替える。

    処理フロー:
    1. 1 行目（理由コードと出所）と、対象・ワークツリー・呼び出しの行を置く。
    2. 横で作業する変更は、動かさなかったことを言う（`SIDE_BY_SIDE_LABEL`）。
    3. 戻していないときは（横で作業する変更を除く）、戻す手順（`gitstate.undo`）と、
       戻さなかった理由、予行の文を足す。
    4. 戻したときは、移したか戻したかの行を足す。
    5. 承認済みの領域（`_in_approved_area`）なら案内を足し、ルールの文（`_messages`）で閉じる。
    """
    change, group = finding.change, finding.group
    lines = [
        f"[ccnavi] {finding.code} ({_source(finding.source, group)})",
        f"path: {change.path} ({change.status.strip()} / {change.kind})",
        f"tree: {finding.tree_name} ({finding.tree_root})" if finding.tree_name else "",
        f"after: {_call(payload)}",
    ]
    if side_by_side and moved is None:
        lines.append(
            f"side-by-side: {SIDE_BY_SIDE_LABEL}。ユーザは、このワークツリーがある間、"
            "このチェックアウトでも作業すると宣言しているので、ccnavi は変更をそのままにしました。"
            "自分で戻さず、何を実行したかを伝えて、誰の変更かをユーザに決めてもらってください。"
        )
    if moved is None:
        if not side_by_side:
            # 横で作業する変更は、誰の変更かをユーザが決める。戻す手順を並べると、上の文と矛盾する。
            undo = gitstate.undo(change, git)
            no_wrapper = "ユーザに頼んでください（git の操作はエージェントには止まります）"
            lines.append(f"undo: {undo or (no_wrapper if git is None else '')}")
        if not_restored:
            lines.append(
                "not-restored: "
                + (
                    "このパスには既にユーザの変更があり、上の呼び出しがさらに変えました。"
                    "コミット済みの内容はユーザの編集より古いので、戻すとその編集まで消えます"
                    if shadowed
                    else (
                        "チケットの作業範囲は、ルールファイルの `deny` ではありません"
                        if finding.code == post_findings.CODE_TICKET_SCOPE
                        else "この場所は `deny` ではなく `ask` と宣言されています"
                    )
                )
                + "。そのため ccnavi は変更をそのままにしました。残すかどうかは人が決めます。"
                "自分で戻さず、何が書いたかを伝えてください。"
            )
        if would_restore:
            lines.append(
                f"{selfguard.ACTION_WOULD}: ccnavi did not touch this path. With "
                f"{settings.RESTORE_IF_DENY_ENV}=enable it would have "
                + (
                    "moved this file aside."
                    if change.kind == gitstate.KIND_NEW
                    else "put it back to its committed content."
                )
            )
    elif moved:
        lines.append(f"restored: ccnavi moved this file to {moved}. It was not deleted.")
    else:
        lines.append("restored: ccnavi put this path back to its committed content.")
    if _in_approved_area(change.path):
        lines.append(APPROVED_HINT)
    lines.append(_messages(group))
    return "\n".join(line for line in lines if line)


# 横で作業する指定が有効なチェックアウトで見つかった変更につける呼び名（`directrec`）。
SIDE_BY_SIDE_LABEL = "出所不明の変更（ユーザか別のセッションの可能性）"

# 承認済みの置き場の報告につける 1 行。判定は変えない。ccnavi-ticket.sh / ccnavi-review.sh が
# 前に書いた未コミットの変更は、後のどのコマンドの後でも同じ形で報告され、
# 受け取った側が自分の違反と読んで undo を打つ（打つと手順が進まなくなる）。
APPROVED_HINT = (
    "note: ここは承認済みの置き場です。このセッションで先に ccnavi-ticket.sh か ccnavi-review.sh を"
    "打っていた（start・finish・confirm など）なら、書いたのはおそらく ccnavi 自身で、"
    "after: の呼び出しではありません。undo: は打たないでください。"
    "何が書いたか分からないときだけ、ユーザに伝えてください。"
)


def _in_approved_area(path: str) -> bool:
    """報告のパスが承認済みの置き場（`.ccnavi/approved/`）の下か。"""
    return f"/{path.replace(chr(92), '/').lower()}".find("/.ccnavi/approved/") >= 0


def _preexisting(finding: post_findings.Finding) -> str:
    """セッションが始まる前から在った変更につける文。

    戻すなと書く。ここを書き落とすと、受け取った側は違反と同じ形の通知を読んで
    同じ手順を踏み、他のセッションやユーザの書きかけを消しにいく。
    """
    change, group = finding.change, finding.group
    return "\n".join(
        [
            f"[ccnavi] {CODE_PREEXISTING} ({_source(finding.source, group)})",
            f"path: {change.path} ({change.status.strip()} / {change.kind})",
            "note: セッションの開始前から作業ツリーにあった変更です。"
            "直前の操作が起こしたものではありません。"
            "戻さず、これを土台にもせず、見つけたことをユーザに伝えて判断を任せてください。",
            _messages(group),
        ]
    )


def _source(source: str, group: list[rules.Rule]) -> str:
    """どの設定がこの場所を守ると言っているかを名指しする。

    実行前の理由（reasons.reason_for）と同じで、示すのは記録名（`<相対パス>:<書いた id>`）。
    どのファイルのどのルールかが名前だけで決まるので、直しに行く先が分かる。
    """
    named = [rules.record_name(rule) for rule in group]
    return f"rule: {','.join(named)}"


def _messages(group: list[rules.Rule]) -> str:
    """ヒットしたルールの文面。同じパスに複数ヒットしたら全部載せる。

    どれか 1 つを選ぶと、選ばれなかったルールの文面は誰にも伝わらない。
    """
    seen, out = set(), []
    for rule in group:
        said = rule.spoken_message()
        if said not in seen:
            seen.add(said)
            out.append(said)
    return "\n".join(out)


def _call(payload: hookio.Input) -> str:
    """直前に走った呼び出しを 1 行で。"""
    subject = payload.tool_input.get("command") or payload.tool_input.get("file_path") or ""
    if not isinstance(subject, str) or not subject:
        return payload.tool_name or "(unknown tool)"
    shown = " ".join(subject.split())
    if len(shown) > SUBJECT_LIMIT:
        shown = shown[:SUBJECT_LIMIT] + "…"
    return f"{payload.tool_name}({shown})"
