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
) -> str:
    """1 件を、それだけで読んで成立する差し戻しの文に組む。

    実行前の拒否と同じ作りにしてある。何に当たったか・どの設定が言っているか・
    直前に何が走ったか・どう戻すか。1 件だけが切り出されて見えても、
    受け取った側がそこから次にすることが分かること。

    would_restore は、戻しが予行のとき。戻す手順はそのまま載せる。今回は
    誰も戻していないので、手順を省くと戻す手立てが 1 つも書かれていない
    報告になる。そのうえで、本番なら ccnavi が戻していたことも書く。

    not_restored は、戻す働きは有効なのに、この 1 件が戻す対象ではない
    とき（`ask` と、承認済みチケットの範囲外。`_restorable`）。手順だけを渡すと、
    受け取ったエージェントには「自分で戻せ」としか読めない。`ask` のルールは
    文面を持てない（lint が禁じる）ので、ここで言わないと誰も言わない。
    """
    change, group = finding.change, finding.group
    lines = [
        f"[ccnavi] {finding.code} ({_source(finding.source, group)})",
        f"path: {change.path} ({change.status.strip()} / {change.kind})",
        f"tree: {finding.tree_name} ({finding.tree_root})" if finding.tree_name else "",
        f"after: {_call(payload)}",
    ]
    if moved is None:
        lines.append(f"undo: {gitstate.undo(change)}")
        if not_restored:
            lines.append(
                "not-restored: "
                + (
                    "the ticket's work area is not a rule-file `deny`"
                    if finding.code == post_findings.CODE_TICKET_SCOPE
                    else "this place is declared `ask`, not `deny`"
                )
                + ", so ccnavi left the change as it is. A person decides whether it stays: "
                "say what wrote it instead of undoing it yourself."
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
    lines.append(_messages(group))
    return "\n".join(line for line in lines if line)


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
            "note: this was already in the working tree when ccnavi started watching this "
            "session, so the call that just ran did not cause it. Do not undo it and do not "
            "build on it: say what you found and let the user decide whose change it is.",
            _messages(group),
        ]
    )


def _source(source: str, group: list[rules.Rule]) -> str:
    """どの設定がこの場所を守ると言っているかを名指しする。

    実行前の理由（reasons.reason_for）と同じで、示すのはルールの id。プロジェクトの
    ルールの id にはプロジェクトの名前が付くので、id だけで直しに行く先が決まる。
    id を持たないルールだけ、代わりにファイルを示す。
    """
    named = [rule.id for rule in group if rule.id]
    return f"rule: {','.join(named)}" if named else f"rules: {source}"


def _messages(group: list[rules.Rule]) -> str:
    """当たったルールの文面。同じパスに複数当たったら全部載せる。

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
