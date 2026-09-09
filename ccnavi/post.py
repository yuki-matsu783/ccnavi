"""ツール実行後の監視。走ったあとの作業ツリーを見て、保護領域が変わっていないかを問う。

## 実行前の判定と何が違うか

見ている対象が違う。実行前はツール呼び出しの引数を見て「これは何をするか」を
言い当てる。ここは作業ツリーを見て「何が起きたか」を読む。前者を厳しくしても
後者は要る。引数に現れない書き込み――ビルドの出力先、スクリプトが内部で開く
ファイル、読めなかったシェル構文――は、言い当てる限りどこまでも抜けるから。

止められないことも違う。このイベントにはツール呼び出しを取り消す手段が無く、
返せるのは文だけになる。だから文が「何が変わったか」だけで終わってはいけない。
戻す手順を対象ごとに書く（REQ-PST-03）。手順の無い通知は、受け取った側に
戻し方を発明させることになり、そこで対象が増えたり減ったりする。

## 保護領域をどこから知るか

ルールファイルから知る。`deny` と `ask` の区画にあって、`match` に書き込み系の
ツールを含むルールは、「この場所はエージェントに好きに書かせない」と
プロジェクトが宣言したものなので、そのままここでの保護領域になる。宣言を
2 か所に分けて書かせない。分ければ必ず食い違い、食い違った側は誰にも
気づかれないまま緩む。

`ask` も保護領域に数えるのは、そこが「人が 1 度見るべき場所」だから。
実行前の判定は引数を見て確認を出すが、シェルやビルドが書いたぶんは
引数に現れないので、誰にも確認が出ないまま通っている。あとから言う先がここしかない。

当てる先は git が返したパスを解いた絶対パス。実行前の判定がファイルのパスを
解いてから当てるのと同じ理由で、綴りを変えただけで外せてはいけない。

## 前から在った変更を原因にしない

作業ツリーは、セッションが始まる前から汚れていることがある。他のセッションの
書きかけ、人が直している最中のもの。それを「直前の実行が壊した」として
差し戻すと、エージェントは他人の作業を戻しにいく。だから初回に見えたものは
その場で控えを取り、以降は新しく現れたものだけを原因付きで報告する。
控えの側も 1 度は伝えるが、文面を分けて、戻すなと明示する。
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import TextIO

from . import audit, gitstate, hookio, rules, selfguard, settings, tree
from . import ticket as ticket_mod

# 保護領域の宣言とみなすツール名。ルールの match にこのどれかが入っていれば、
# そのルールは「この場所に書かせない」を言っている。
WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")

# 作業ツリーを変えようがないツール。ここを飛ばすぶん git を起こす回数が減る。
# 飛ばしてよいのは「このツールが走った直後に確かめなくても、次に走る
# 書き込みうるツールの直後に同じ変更が見える」から。見落としではなく遅れ。
#
# 名前を並べる側は狭く保つ。MCP のツールは名前を自由に付けられるので、
# 知らない名前は「書けるかもしれない」側に置く。
READ_ONLY_TOOLS = ("Read", "Grep", "Glob", "WebFetch", "WebSearch")

# 返す文に載せる理由コード。cli.py の表と同じ体系から借りている。
# 設計 §18.6 が監査に残す事象名がそのまま POST_VIOLATION。
CODE_VIOLATION = "POST_VIOLATION"
# 前から在った変更には別のコードを立てる。同じコードで送ると、受け取った側に
# 「直前の実行が壊したもの」と「元から汚れていたもの」を見分ける手が無くなる。
CODE_PREEXISTING = "POST_PREEXISTING"
# 承認されたチケットの作業範囲の外が変わった。実行前の判定が同じことを
# DENY_TICKET_SCOPE で止めるが、そちらは Write / Edit の引数しか見ない。
# シェルが書いたもの、ビルドの出力、スクリプトが内部で開いたファイルは、
# 引数に現れないのでここでしか捕まらない。
CODE_TICKET_SCOPE = "POST_TICKET_SCOPE"

# 範囲外の変更を咎めているのは、ルールファイルの中のルールではなく承認台帳。
# 出所にこの名前を添えて、ルールファイルを探しても見つからないことを示す。
TICKET_SCOPE_RULE = "(ticket-scope)"

# 判定に至らなかった理由のうち、この面だけが出すもの。
REASON_TOOL_CANNOT_WRITE = "tool-cannot-write"
REASON_WORKTREE_UNREADABLE = "worktree-unreadable"
# ターンの始まりを見ていないので、このターンで起きたことを切り出せない。
# 記録に残す。ターンの終わりの報告が黙っていた期間を、後から数えられるように。
REASON_NO_TURN_BASELINE = "no-turn-baseline"

# 1 回の報告に載せる件数の上限。ビルドが生成物を数百件置くことがあり、
# 全部を並べると本文が流れて 1 件も読まれない。
REPORT_LIMIT = 12

# 控えに残す件数の上限。セッションが長引いても記録が膨らまないように。
SEEN_LIMIT = 500

# 対象の綴りを載せるときの長さの上限。
SUBJECT_LIMIT = 200

# 控えのファイル名に使える文字。セッション識別子はそのまま名前になるので、
# 区切り文字が混じった値でファイルを別の場所へ書かせない。
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def check(
    stderr: TextIO,
    enforcing: bool,
    restore: str,
    state_dir: str,
    mine: tuple[str, ...],
    rule_set: rules.RuleSet,
    source: str,
    scope: ScopeGuard | None,
    payload: hookio.Input,
    record: audit.Record,
    root: str,
) -> str:
    """実行後の 1 回ぶんを処理し、モデルに返す文を返す。返す文が無ければ空文字。

    enforcing は、このモードが判定を実際に適用する側かどうか。適用しない側
    （dry-run）では復元も行わない。呼び出しにも作業ツリーにも手を出さないことが
    そのモードの約束なので、自分の判断でファイルを動かしては意味がない。

    mine は ccnavi 自身が書く場所。記録と控えがそれで、どちらも保護領域の
    中に置かれることがある。実際このリポジトリのルールは `.claude/ccnavi/*`
    を守っていて、記録も控えもそこにある。自分の書き込みを自分の違反として
    報告しはじめると、監視は 1 回目から嘘しか言わなくなる。
    """
    if payload.tool_name in READ_ONLY_TOOLS:
        record.decision, record.reason = audit.SKIP, REASON_TOOL_CANNOT_WRITE
        return ""

    top = gitstate.top_level(root)
    changes, unreadable = gitstate.read(top)
    if unreadable:
        # 見えないことを黙らない。記録に残せば、監視が動いていなかった期間を
        # 後から数えられる。数えられないと、違反が無かったのか見ていなかった
        # のかが同じ見た目になる。
        record.decision, record.reason = audit.SKIP, REASON_WORKTREE_UNREADABLE
        record.detail = unreadable
        stderr.write(f"ccnavi: 作業ツリーを読めないので実行後の監視は動かない: {unreadable}\n")
        return ""

    seen, first_time = _load_seen(stderr, state_dir, payload.session_id)

    found = _findings(changes, rule_set, mine, source, scope)
    if not found:
        # 違反が無くても、初回なら控えを作る。ここを飛ばすと、綺麗な作業ツリーで
        # 始まったセッションはいつまでも「初回」のままになり、その後に現れた
        # 汚れが全部「前から在ったもの」に化けて、誰も差し戻されなくなる。
        if first_time:
            _save_seen(stderr, state_dir, payload.session_id, set())
        record.decision, record.enforced = audit.ALLOW, True
        return ""

    fresh = [f for f in found if f.change.key() not in seen]
    known = [f for f in found if f.change.key() in seen]
    # 初めて見たセッションでは、今そこに在るものは直前の実行の結果ではない。
    # 控えを取るだけにして、原因を付けずに 1 度だけ伝える。
    carried, fresh = (fresh, []) if first_time else ([], fresh)

    restored: dict[str, str] = {}
    # restore には CCNAVI_MODE を掛けたあとの値が来る（cli.effective_setting）ので、
    # ここで enforcing を見る必要はない。掛ける場所を 1 か所に寄せてあるのは、
    # 2 つの設定が別々にモードを解釈して食い違うのを防ぐため。
    if fresh and restore == selfguard.ENABLE:
        restored = _restore(stderr, top, state_dir, [f.change for f in fresh])
    # dry-run では戻さない代わりに、戻していたはずだと言う。selfguard の
    # would-restore と同じ扱いにしてある。黙って何もしないと、報告を読んだ側には
    # 戻しを切った形と見分けが付かない。予行として置いた設定が「戻しは要らない」
    # という結論に読み替えられるし、対象がルールファイル次第で動くこの面では、
    # 何が戻るのかを本番の前に見せることがそのまま安全の余裕になる。
    would_restore = bool(fresh) and restore == selfguard.DRY_RUN

    _save_seen(
        stderr,
        state_dir,
        payload.session_id,
        seen
        # 戻せたものは控えに入れない。同じ場所がもう一度汚れたら、それは
        # すでに知っている変更ではなく新しい出来事なので、もう一度言う。
        | {f.change.key() for f in fresh if f.change.path not in restored}
        | {f.change.key() for f in carried},
    )

    record.paths = [f"{f.change.kind} {f.change.path}" for f in fresh]
    record.rules = sorted({rule.id or "(id 無し)" for f in fresh for rule in f.group})
    # 既にある detail を先頭に残す。ルールを読めなかったときのファイル名が
    # そこに入っていて、上書きすると「どのファイルが読めなかったか」が消える。
    notes = [record.detail] if record.detail else []
    if carried:
        notes.append(f"preexisting {len(carried)}")
    if known:
        notes.append(f"known {len(known)}")
    if restored:
        notes.append(f"restored {len(restored)}")
    elif would_restore:
        notes.append(f"{selfguard.ACTION_WOULD} {len(fresh)}")
    record.detail = "; ".join(notes)

    if fresh:
        record.decision, record.enforced = audit.DENY, enforcing
    else:
        # この呼び出しは何も汚していない。控えの報告は状態の通知であって、
        # 直前の実行についての判定ではない。
        record.decision, record.enforced = audit.ALLOW, True

    blocks = [_violation(f, payload, restored.get(f.change.path), would_restore) for f in fresh]
    blocks += [_preexisting(f) for f in carried[:REPORT_LIMIT]]
    if not blocks:
        return ""

    shown, dropped = blocks[:REPORT_LIMIT], len(blocks) - REPORT_LIMIT
    if dropped > 0:
        shown.append(
            f"[ccnavi] {dropped} more changed paths in protected areas are not listed here. "
            "Run 'git status' to see the rest before you undo anything."
        )
    return "\n\n".join(shown)


def at_stop(
    stderr: TextIO,
    state_dir: str,
    mine: tuple[str, ...],
    rule_set: rules.RuleSet,
    source: str,
    scope: ScopeGuard | None,
    payload: hookio.Input,
    record: audit.Record,
    root: str,
) -> str:
    """ターンの終わりに、このターンで変わった保護領域を人へ報告する。

    呼び出しごとの報告とは宛先も目的も違う。あちらはモデルが読んで次の一手を
    変えるためのもので、こちらは人が「このターンで何が変わったか」を 1 度で
    見るためのものになる。

    セッションの控え（`seen`）は見ない。あれは「モデルへ 1 度伝えた」を
    覚えているもので、人はまだ 1 度も見ていないことがある。代わりに見るのは
    ターンの始まりに取った基準で、そこに無いものだけが、このターンで起きたこと。

    戻しもしない。ここで報告するのは、実行後の監視が戻さなかった、あるいは
    戻す設定になっていなかった変更で、どうするかは人が決める。
    """
    baseline, known_baseline = _load_turn(stderr, state_dir, payload.session_id)
    if not known_baseline:
        # ターンの始まりを見ていない。ここで今そこに在るものを全部並べると、
        # 利用者の書きかけも他のセッションが置いたものも、このターンの成果として
        # 報告することになる。見えていない期間を、見えたことにしない。
        record.decision, record.reason = audit.SKIP, REASON_NO_TURN_BASELINE
        return ""

    top = gitstate.top_level(root)
    changes, unreadable = gitstate.read(top)
    if unreadable:
        record.decision, record.reason = audit.SKIP, REASON_WORKTREE_UNREADABLE
        record.detail = unreadable
        return ""

    found = [
        f
        for f in _findings(changes, rule_set, mine, source, scope)
        if f.change.key() not in baseline
    ]
    if not found:
        record.decision, record.enforced = audit.ALLOW, True
        return ""

    record.decision, record.enforced = audit.DENY, True
    record.paths = [f"{f.change.kind} {f.change.path}" for f in found]
    record.rules = sorted({rule.id or "(id 無し)" for f in found for rule in f.group})

    lines = [f"[ccnavi] 守ると宣言した場所が、このターンで {len(found)} 件変わりました。"]
    for finding in found[:REPORT_LIMIT]:
        rule = finding.group[0]
        lines.append(
            f"  {finding.change.path}（{finding.change.kind}）"
            f" ルール {rule.id or '(id 無し)'} / 戻すなら {gitstate.undo(finding.change)}"
        )
    if len(found) > REPORT_LIMIT:
        lines.append(f"  ほか {len(found) - REPORT_LIMIT} 件。全部は git status に出ます。")
    return "\n".join(lines)


@dataclass
class ScopeGuard:
    """承認された作業範囲を、実行後の側から当てるための持ち物。

    実行前の判定と同じ範囲・同じ当て方を使う。別に書くと、同じ書き込みが
    実行前は通って実行後に咎められる（あるいはその逆）ことになり、
    どちらが本当の範囲なのかを誰も言えなくなる。鍵はファイルの行き先で、
    その行き先の作業ツリーに結び付いた写しの範囲を当てる。
    """

    root: str
    approved: str
    copies: dict[str, ticket_mod.Ticket] = field(default_factory=dict)

    def finding(self, full: str) -> tuple[rules.Rule, str] | None:
        """この変更が範囲の外なら、咎める文面と出所を返す。中なら None。

        チケット自身の提案ファイルは外でも咎めない。次のチケットを提案する道を
        塞ぐと、いちど承認した範囲から永久に出られなくなる。
        """
        t = tree.tree_of(self.root, full)
        if t is None or t.is_main:
            return None
        ticket = self.copies.get(t.name)
        if ticket is None or ticket.is_own_file(full):
            return None
        rel = tree.relative(t, full)
        verdict = ticket.decide(rel)
        parent = self.copies.get(ticket.parent) if ticket.is_child else None
        if parent is not None:
            verdict = ticket_mod.combine(verdict, parent.decide(rel))
        if verdict in (rules.ALLOW, rules.ASK):
            return None
        area = ", ".join(ticket.paths(rules.ALLOW) + ticket.paths(rules.ASK)) or "(empty)"
        rule = rules.Rule(
            id=TICKET_SCOPE_RULE,
            message=(
                f"This path is outside the work area that ticket {ticket.ticket} declares "
                f"({area}) for worktree {t.name}. Send the output inside that area, or, if the "
                "task genuinely needs this path, tell the parent so it can propose a ticket that "
                "covers it and ask the user to run 'ccnavi --approve'."
            ),
        )
        return rule, os.path.join(self.approved, ticket.ticket + ".md")


@dataclass
class Finding:
    """報告する 1 件。何が変わったかと、それを咎めているのが誰かの組。

    咎める側が 2 通りある。ルールファイルが守ると宣言した場所と、承認された
    チケットが作業範囲の外だと言う場所。どちらから来たかを持ち回らないと、
    報告の出所がすべてルールファイルを名乗ることになり、見に行った人が
    そこに無いルールを探すことになる。
    """

    change: gitstate.Change
    group: list[rules.Rule]
    source: str
    code: str


def _findings(
    changes: list[gitstate.Change],
    rule_set: rules.RuleSet,
    mine: tuple[str, ...],
    source: str,
    scope: ScopeGuard | None,
) -> list[Finding]:
    """変更のうち、報告すべきものを返す。

    ccnavi 自身が書く場所は先に落とす。落とさないと、記録を 1 行足すたびに
    自分がその記録を違反として報告し、その報告がまた記録を 1 行増やす。

    ルールに当たった変更は、範囲の外でもルールの側で報告する。ルールのほうが
    強く、範囲を広げても通らないから。ここで範囲の側の文面を返すと、
    「範囲を広げれば済む」と読ませて、済まないことを 1 往復あとに知らせる。
    """
    own = tuple(os.path.realpath(p) for p in mine if p)
    found = []
    for change in changes:
        if any(change.full == p or change.full.startswith(p + os.sep) for p in own):
            continue
        group = [rule for rule in _guarding(rule_set) if _guards_writes(rule, change.full)]
        if group:
            found.append(Finding(change, group, source, CODE_VIOLATION))
            continue
        if scope is None or _allowed(rule_set, change.full):
            continue
        hit = scope.finding(change.full)
        if hit is not None:
            rule, where = hit
            found.append(Finding(change, [rule], where, CODE_TICKET_SCOPE))
    return found


def _guarding(rule_set: rules.RuleSet) -> list[rules.Rule]:
    """保護領域を宣言していると読むルール。`deny` と `ask` の両方。

    `ask` を入れるのは、そこが「人が 1 度見るべき場所」だとプロジェクトが
    言っている場所だから。シェルやビルドが書いたぶんは誰にも確認が出ないまま
    通っているので、あとから言う先がここしかない。

    `allow` は入れない。通してよいと宣言された場所なので、変わっていることは
    報告することではない。ただし `deny` や `ask` と同じ場所に当たる `allow` が
    あっても、強いほうが勝つ。当てる順は実行前の判定と同じ。
    """
    return rule_set.deny + rule_set.ask


def _allowed(rule_set: rules.RuleSet, path: str) -> bool:
    """この場所への書き込みが `allow` で宣言されているか。

    宣言されていればチケットの範囲の外でも咎めない。実行前の判定でルールが
    チケットより強いのと同じ順で、実行後もルールを先に見る。片方だけ順番が
    違うと、実行前に通った書き込みが実行後に差し戻されることになる。
    """
    return any(_guards_writes(rule, path) for rule in rule_set.allow)


def _guards_writes(rule: rules.Rule, path: str) -> bool:
    """このルールがこのパスへの書き込みを禁じているか。

    ルールの当て方は実行前の判定と同じ rule.matches に任せる。ここで別に
    書くと、同じルールが実行前と実行後で違う場所に当たることになり、
    どちらが正しいのかを誰も言えなくなる。
    """
    return any(rule.matches(tool, path) for tool in WRITE_TOOLS)


def _violation(
    finding: Finding, payload: hookio.Input, moved: str | None, would_restore: bool = False
) -> str:
    """1 件を、それだけで読んで成立する差し戻しの文に組む。

    実行前の拒否と同じ作りにしてある。何に当たったか・どの設定が言っているか・
    直前に何が走ったか・どう戻すか。1 件だけが切り出されて見えても、
    受け取った側がそこから次の一手に行けること。

    would_restore は、戻しが予行のとき。戻す手順はそのまま載せる。今回は
    誰も戻していないので、手順を落とすと戻す手立てが 1 つも書かれていない
    報告になる。そのうえで、本番なら ccnavi が戻していたことを添える。
    """
    change, group = finding.change, finding.group
    lines = [
        f"[ccnavi] {finding.code} (source: {_source(finding.source, group)})",
        f"path: {change.path} ({change.status.strip() or change.status} / {change.kind})",
        f"after: {_call(payload)}",
    ]
    if moved is None:
        lines.append(f"undo: {gitstate.undo(change)}")
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


def _preexisting(finding: Finding) -> str:
    """セッションが始まる前から在った変更に添える文。

    戻すなと書く。ここを書き落とすと、受け取った側は違反と同じ形の通知を読んで
    同じ手順を踏み、他のセッションや人の書きかけを消しにいく。
    """
    change, group = finding.change, finding.group
    return "\n".join(
        [
            f"[ccnavi] {CODE_PREEXISTING} (source: {_source(finding.source, group)})",
            f"path: {change.path} ({change.status.strip() or change.status} / {change.kind})",
            "note: this was already in the working tree when ccnavi started watching this "
            "session, so the call that just ran did not cause it. Do not undo it and do not "
            "build on it: say what you found and let the user decide whose change it is.",
            _messages(group),
        ]
    )


def _source(source: str, group: list[rules.Rule]) -> str:
    """どの設定がこの場所を守ると言っているかを名指しする。

    ファイル名だけでは、見に行った人が当たったルールに辿り着けない。
    """
    named = [rule.id for rule in group if rule.id]
    return f"{source}#{','.join(named)}" if named else source


def _messages(group: list[rules.Rule]) -> str:
    """当たったルールの文面。同じパスに複数当たったら全部載せる。

    どれか 1 つを選ぶと、選ばれなかったルールの言い分は誰にも届かない。
    """
    seen, out = set(), []
    for rule in group:
        if rule.message not in seen:
            seen.add(rule.message)
            out.append(rule.message)
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


def _restore(
    stderr: TextIO, top: str, state_dir: str, changes: list[gitstate.Change]
) -> dict[str, str]:
    """戻せたものを {パス: 退避先（戻しただけなら空文字）} で返す。

    戻せなかったものは黙って落とす。復元は報告の代わりではないので、
    戻せなかった 1 件は手順付きの報告として残ればよい。
    """
    aside = os.path.join(state_dir, "aside", time.strftime("%Y%m%d-%H%M%S"))
    done: dict[str, str] = {}
    for change in changes:
        failed = gitstate.restore(top, change, aside)
        if failed:
            stderr.write(f"ccnavi: {change.path} を戻せない: {failed}\n")
            continue
        done[change.path] = aside if change.kind == gitstate.KIND_NEW else ""
    return done


def _seen_path(state_dir: str, session: str) -> str:
    name = _UNSAFE.sub("_", session)[:64] or "unknown"
    return os.path.join(state_dir, f"{name}.json")


def _turn_path(state_dir: str, session: str) -> str:
    """ターンの基準を置く場所。セッションの控えとは別に持つ。

    2 つは寿命が違う。セッションの控えは「モデルへ 1 度伝えた」を覚えていて
    セッションが終わるまで残るが、こちらはターンごとに取り直す。同じファイルに
    まとめると、ターンの区切りでセッションの控えまで消えることになる。
    """
    name = _UNSAFE.sub("_", session)[:64] or "unknown"
    return os.path.join(state_dir, f"{name}.turn.json")


def at_prompt(
    stderr: TextIO,
    state_dir: str,
    mine: tuple[str, ...],
    rule_set: rules.RuleSet,
    source: str,
    scope: ScopeGuard | None,
    payload: hookio.Input,
    record: audit.Record,
    root: str,
) -> None:
    """ターンの始まり。いま保護領域に在る変更を控えて、このターンの基準にする。

    これが無いと、ターンの終わりの報告が「今そこにある変更」しか言えない。
    利用者自身の書きかけも、他のセッションが置いたものも、前のターンで
    片付けなかったものも、全部このターンの成果として並ぶ。何度も同じものを
    見せられた人は、次から読まなくなる。

    基準を取るのはターンが始まる瞬間で、そこが「エージェントがまだ何もして
    いない時点」になる。以降に現れたものだけが、このターンで起きたこと。
    """
    top = gitstate.top_level(root)
    changes, unreadable = gitstate.read(top)
    if unreadable:
        record.decision, record.reason = audit.SKIP, REASON_WORKTREE_UNREADABLE
        record.detail = unreadable
        return

    found = _findings(changes, rule_set, mine, source, scope)
    _save_turn(stderr, state_dir, payload.session_id, {f.change.key() for f in found})
    record.decision, record.enforced = audit.ALLOW, True
    if found:
        record.detail = f"turn baseline {len(found)}"


def _load_turn(stderr: TextIO, state_dir: str, session: str) -> tuple[set[str], bool]:
    """このターンの基準を返す。2 つめの値は、基準を持っているかどうか。

    持っていないなら、ターンの始まりを見ていない。登録されていないか、
    そのイベントで読めなかったか。そこで「全部このターンの成果」として
    報告すると、前から在ったものまで並ぶので、そのときは何も言わない。
    見えていない期間を、見えたことにしない。
    """
    if not state_dir:
        return set(), False
    try:
        with open(_turn_path(state_dir, session), encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return set(), False
    except (OSError, ValueError) as exc:
        stderr.write(f"ccnavi: ターンの基準を読めない: {exc}\n")
        return set(), False
    base = data.get("baseline") if isinstance(data, dict) else None
    if not isinstance(base, list):
        return set(), False
    return {s for s in base if isinstance(s, str)}, True


def _save_turn(stderr: TextIO, state_dir: str, session: str, baseline: set[str]) -> None:
    if not state_dir:
        return
    try:
        os.makedirs(state_dir, exist_ok=True)
        with open(_turn_path(state_dir, session), "w", encoding="utf-8") as f:
            json.dump({"baseline": sorted(baseline)[:SEEN_LIMIT]}, f, ensure_ascii=False)
    except OSError as exc:
        stderr.write(f"ccnavi: ターンの基準を書けない: {exc}\n")


def _load_seen(stderr: TextIO, state_dir: str, session: str) -> tuple[set[str], bool]:
    """すでに報告した変更と、このセッションで初めて見るかどうかを返す。

    読めなければ「初めて」として扱う。控えを失ったときに、前から在った変更を
    直前の実行のせいにするより、もう一度控えを取り直すほうが害が小さい。
    """
    if not state_dir:
        return set(), True
    try:
        with open(_seen_path(state_dir, session), encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return set(), True
    except (OSError, ValueError) as exc:
        stderr.write(f"ccnavi: 実行後の監視の控えを読めない: {exc}\n")
        return set(), True
    seen = data.get("seen") if isinstance(data, dict) else None
    if not isinstance(seen, list):
        return set(), True
    return {s for s in seen if isinstance(s, str)}, False


def _save_seen(stderr: TextIO, state_dir: str, session: str, seen: set[str]) -> None:
    """控えを書く。書けなかったことは報告して捨てる。

    ここでの失敗は監視を止めない。控えが無ければ同じ変更をもう一度報告する
    ことになり、うるさいが、見落とすよりはよい。
    """
    if not state_dir:
        return
    path = _seen_path(state_dir, session)
    try:
        os.makedirs(state_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"seen": sorted(seen)[:SEEN_LIMIT]}, f, ensure_ascii=False)
    except OSError as exc:
        stderr.write(f"ccnavi: 実行後の監視の控えを書けない: {exc}\n")
