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

ルールファイルから知る。`deny` と `ask` のタイプにあって、`match` に書き込み系の
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

import os
import time
from dataclasses import dataclass, field
from typing import TextIO

from . import audit, fsio, gitstate, hookio, phase, phasetypes, rules, selfguard, settings, tree
from . import ticket as ticket_mod

# 保護領域の宣言とみなすツール名。ルールの match にこのどれかが入っていれば、
# そのルールは「この場所に書かせない」を言っている。
WRITE_TOOLS = ("Write", "Edit", "NotebookEdit")

# 作業ツリーを変えようがないツール。ここを飛ばすぶん git を起こす回数が減る。
# 飛ばしてよいのは「このツールが走った直後に確かめなくても、次に走る
# 書き込みうるツールの直後に同じ変更が見える」から。見落としではなく遅れ。
#
# 名前を並べる側は狭く保つ。MCP のツールは名前を自由に付けられるので、
# 知らない名前は「書けるかもしれない」側に置く。
READ_ONLY_TOOLS = ("Read", "Grep", "Glob", "WebFetch", "WebSearch")

# 返す文に載せる理由コード。cli.py の表と同じ体系から借りている。
# 設計 §7.2 が監査に残す事象名がそのまま POST_VIOLATION。
CODE_VIOLATION = "POST_VIOLATION"
# 前から在った変更には別のコードを立てる。同じコードで送ると、受け取った側に
# 「直前の実行が壊したもの」と「元から汚れていたもの」を見分ける手が無くなる。
CODE_PREEXISTING = "POST_PREEXISTING"
# 承認されたチケットの作業範囲の外が変わった。実行前の判定が同じことを
# DENY_TICKET_SCOPE で止めるが、そちらは Write / Edit の引数しか見ない。
# シェルが書いたもの、ビルドの出力、スクリプトが内部で開いたファイルは、
# 引数に現れないのでここでしか捕まらない。
CODE_TICKET_SCOPE = "POST_TICKET_SCOPE"

# 範囲外の変更を咎めているのは、ルールファイルの中のルールではなく承認済みチケット。
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


def check(
    stderr: TextIO,
    enforcing: bool,
    restore: str,
    state_dir: str,
    mine: tuple[str, ...],
    watched: list[Watched],
    scope: ScopeGuard | None,
    payload: hookio.Input,
    record: audit.Record,
    places: tuple[str, str] = ("", ""),
) -> str:
    """実行後の 1 回ぶんを処理し、モデルに返す文を返す。返す文が無ければ空文字。

    enforcing は、このモードが判定を実際に適用する側かどうか。適用しない側
    （dry-run）では復元も行わない。呼び出しにも作業ツリーにも手を出さないことが
    そのモードの約束なので、自分の判断でファイルを動かしては意味がない。

    mine は ccnavi 自身が書く場所。記録と控えがそれで、置き場は設定で動くので、
    ルールが守る場所の中を指すこともある。自分の書き込みを自分の違反として
    報告しはじめると、監視は 1 回目から嘘しか言わなくなる。

    watched は見るツリー。ワークスペースルートと、この呼び出しが触ったツリー
    （設計 §11.7）。ツリーごとに git を起こし、そのツリーのルールで見る。

    places はチケットの置き場（提案と承認済みチケット）。そこに現れた変更のうち、
    ccnavi の副命令が書いたと内容から読めるものを外す（`_script_writes`）。
    """
    if payload.tool_name in READ_ONLY_TOOLS:
        record.decision, record.reason = audit.SKIP, REASON_TOOL_CANNOT_WRITE
        return ""

    read = _read_all(stderr, watched, record)
    if not read:
        return ""

    # ターンの基準を持たないツリーを初めて見たら、その場で控える。ターンの途中で
    # 切られたワークツリーがこれにあたる（`at_prompt` のときには無いので基準が無く、
    # 基準が無いツリーのコミットはターンの終わりに数えられない）。ワークツリーを
    # 切ってから手を付けるのがこのリポジトリの手順なので、切ったターンがまるごと
    # その穴に入っていた。触った時点から先は数えられるようにする。
    #
    # 触られないまま終わったツリーには、ここでも基準が付かない。そちらは
    # ターンの終わりが「数えていない」と言う（`_uncounted_line`）。
    _note_new_trees(stderr, state_dir, payload.session_id, read)

    seen, first_time = _load_seen(stderr, state_dir, payload.session_id)

    found = [
        f
        for w, top, changes in read
        for f in _findings(changes, w.rule_set, mine, w.source, scope, w.tree, top, places)
    ]
    if not found:
        # 違反が無くても、初回なら控えを作る。ここを飛ばすと、綺麗な作業ツリーで
        # 始まったセッションはいつまでも「初回」のままになり、その後に現れた
        # 汚れが全部「前から在ったもの」に化けて、誰も差し戻されなくなる。
        if first_time:
            _save_seen(stderr, state_dir, payload.session_id, set())
        record.decision, record.enforced = audit.ALLOW, True
        return ""

    fresh = [f for f in found if f.key() not in seen]
    known = [f for f in found if f.key() in seen]
    # 初めて見たセッションでは、今そこに在るものは直前の実行の結果ではない。
    # 控えを取るだけにして、原因を付けずに 1 度だけ伝える。
    carried, fresh = (fresh, []) if first_time else ([], fresh)

    restored: dict[str, str] = {}
    # 戻すのは `deny` と宣言された場所だけ（`_restorable`）。報告する対象より狭い。
    # restore には CCNAVI_MODE を掛けたあとの値が来る（cli.effective_setting）ので、
    # ここで enforcing を見る必要はない。掛ける場所を 1 か所に寄せてあるのは、
    # 2 つの設定が別々にモードを解釈して食い違うのを防ぐため。
    # 戻すのはそのツリーの git で。鍵はツリー付きにして、別のツリーの同じ相対パスと
    # 混ざらないようにする。
    if restore == selfguard.ENABLE:
        for w, top, _ in read:
            here = [f for f in fresh if f.tree_name == w.tree.name and _restorable(f)]
            if not here:
                continue
            done = _restore(stderr, top, state_dir, [f.change for f in here])
            restored.update({f.key(): done[f.change.path] for f in here if f.change.path in done})
    # dry-run では戻さない代わりに、戻していたはずだと言う。selfguard の
    # would-restore と同じ扱いにしてある。黙って何もしないと、報告を読んだ側には
    # 戻しを切った形と見分けが付かない。予行として置いた設定が「戻しは要らない」
    # という結論に読み替えられるし、対象がルールファイル次第で動くこの面では、
    # 何が戻るのかを本番の前に見せることがそのまま安全の余裕になる。
    # 予行で言うのも、本番で戻すのと同じ集合に限る。戻さない 1 件に「本番なら
    # 戻していた」と書くと、設定を enable にしたときに起きることを読み違えさせる。
    would = {f.key() for f in fresh if _restorable(f)} if restore == selfguard.DRY_RUN else set()

    _save_seen(
        stderr,
        state_dir,
        payload.session_id,
        seen
        # 戻せたものは控えに入れない。同じ場所がもう一度汚れたら、それは
        # すでに知っている変更ではなく新しい出来事なので、もう一度言う。
        | {f.key() for f in fresh if f.key() not in restored}
        | {f.key() for f in carried},
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
    elif would:
        notes.append(f"{selfguard.ACTION_WOULD} {len(would)}")
    record.detail = "; ".join(notes)

    if fresh:
        record.decision, record.enforced = audit.DENY, enforcing
    else:
        # この呼び出しは何も汚していない。控えの報告は状態の通知であって、
        # 直前の実行についての判定ではない。
        record.decision, record.enforced = audit.ALLOW, True

    gated = restore in (selfguard.ENABLE, selfguard.DRY_RUN)
    blocks = [
        _violation(
            f,
            payload,
            restored.get(f.key()),
            f.key() in would,
            not_restored=gated and not _restorable(f),
        )
        for f in fresh
    ]
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


def _note_new_trees(
    stderr: TextIO,
    state_dir: str,
    session: str,
    read: list[tuple[Watched, str, list[gitstate.Change]]],
) -> None:
    """ターンの基準にまだ居ないツリーの HEAD を控える。居るツリーには触らない。

    書き直すのは、控えに無いツリーが 1 本でもあるときだけ。呼び出しのたびに
    控えを書き直すと、ツールを打つ数だけ書き込みが増える。ターンの基準そのもの
    （`baseline`）は動かさない。あれは「ターンの始まりに何が汚れていたか」で、
    あとから足すと、このターンで現れた汚れを前から在ったことにしてしまう。
    """
    baseline, known, heads = _load_turn(stderr, state_dir, session)
    if not known:
        # ターンの始まりを見ていない。基準そのものが無いので、ここで HEAD だけを
        # 置くと「基準はあるが baseline が空」に化ける。何もしない。
        return
    fresh_heads = {}
    for _, top, _ in read:
        if not top or top in heads:
            continue
        sha = gitstate.head(top)
        if sha:
            fresh_heads[top] = sha
    if fresh_heads:
        _save_turn(stderr, state_dir, session, baseline, {**heads, **fresh_heads})


def _committed_findings(
    stderr: TextIO,
    read: list[tuple[Watched, str, list[gitstate.Change]]],
    heads: dict[str, str],
    mine: tuple[str, ...],
    scope: ScopeGuard | None,
    places: tuple[str, str],
) -> tuple[list[Finding], list[str]]:
    """このターンでコミットに入った、保護領域の変更。

    見るのは、ターンの始まりに控えた HEAD からの差分（`gitstate.committed`）。
    控えを持たないツリー（ターンの途中で現れた、HEAD を読めなかった）は飛ばす。
    数えていない期間を、数えたことにしない。

    チケットの置き場は外す。承認は人が提案を `.ccnavi/approved/` へ動かして
    コミットする運びで（`ccnavi-push-approved.sh`）、その置き場は `deny` でもある。
    外さないと、人が承認するたびに、その操作が違反としてターンの報告に並ぶ。
    置き場の綴りは `places` で受け取る。チケット制御を切ったワークスペースでも
    承認のコミットは在りうるので、`scope` の有無で外れたり外れなかったりさせない。

    2 つめに返すのは、数えなかったツリーの名前。**黙って飛ばさない。** 基準が
    無いのは、ターンの途中で切られたツリー（プロンプトのときに無かった）か、
    そのとき HEAD を読めなかったツリーで、どちらも「変わっていない」ではない。
    ワークツリーを切ってから手を付けるのがこのリポジトリの手順なので、切った
    ターンのコミットはここに落ちる。落ちたことを言わないと、見ていない期間が
    「何も起きなかった」と同じ見た目になる。
    """
    out: list[Finding] = []
    uncounted: list[str] = []
    tickets, approved = places
    for w, top, _ in read:
        base = heads.get(top)
        if not base:
            uncounted.append(w.tree.name or ".")
            continue
        changes, unreadable = gitstate.committed(top, base)
        if unreadable:
            stderr.write(
                f"ccnavi: コミット済みの差分を読めない（{w.tree.name or '.'}）: {unreadable}\n"
            )
            uncounted.append(w.tree.name or ".")
            continue
        for finding in _findings(changes, w.rule_set, mine, w.source, scope, w.tree):
            rel = tree.relative(w.tree, finding.change.full)
            if ticket_mod.is_ticket_place(rel, tickets, approved):
                continue
            out.append(finding)
    return out, uncounted


def at_stop(
    stderr: TextIO,
    state_dir: str,
    mine: tuple[str, ...],
    watched: list[Watched],
    scope: ScopeGuard | None,
    payload: hookio.Input,
    record: audit.Record,
    places: tuple[str, str] = ("", ""),
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
    baseline, known_baseline, heads = _load_turn(stderr, state_dir, payload.session_id)
    if not known_baseline:
        # ターンの始まりを見ていない。ここで今そこに在るものを全部並べると、
        # 利用者の書きかけも他のセッションが置いたものも、このターンの成果として
        # 報告することになる。見えていない期間を、見えたことにしない。
        record.decision, record.reason = audit.SKIP, REASON_NO_TURN_BASELINE
        return ""

    read = _read_all(stderr, watched, record)
    if not read:
        return ""

    found = [
        f
        for w, top, changes in read
        for f in _findings(changes, w.rule_set, mine, w.source, scope, w.tree, top, places)
        if f.key() not in baseline
    ]
    # このターンでコミットに入ったぶんも足す。`git status` はコミットを見せないので、
    # ここを足さないと、保護領域を汚してからコミットした回が「何も起きなかった」と
    # 同じ見た目になる（呼び出しごとの監視も同じ穴を持つが、そちらは戻しに関わるので
    # 断面を変えない。人が 1 度で見るのはこの報告）。
    committed, uncounted = _committed_findings(stderr, read, heads, mine, scope, places)
    found += committed
    if not found and not uncounted:
        record.decision, record.enforced = audit.ALLOW, True
        return ""
    if not found:
        # 違反は無いが、数えていないツリーがある。そのことだけを言う。
        record.decision, record.enforced = audit.ALLOW, True
        record.detail = f"uncounted {len(uncounted)}"
        return _uncounted_line(uncounted)

    record.decision, record.enforced = audit.DENY, True
    record.paths = [f"{f.change.kind} {f.change.path}" for f in found]
    record.rules = sorted({rule.id or "(id 無し)" for f in found for rule in f.group})

    lines = [f"[ccnavi] 守ると宣言した場所が、このターンで {len(found)} 件変わりました。"]
    for finding in found[:REPORT_LIMIT]:
        rule = finding.group[0]
        where = f"{finding.tree_name}: " if finding.tree_name else ""
        undo = gitstate.undo(finding.change)
        how = (
            f"戻すなら {undo}"
            if undo
            else "コミットに入っている（履歴は書き換えない。取り込む前に確かめる）"
        )
        lines.append(
            f"  {where}{finding.change.path}（{finding.change.kind}）"
            f" ルール {rule.id or '(id 無し)'} / {how}"
        )
    if len(found) > REPORT_LIMIT:
        lines.append(f"  ほか {len(found) - REPORT_LIMIT} 件。全部は git status に出ます。")
    if uncounted:
        lines.append(_uncounted_line(uncounted))
        record.detail = f"uncounted {len(uncounted)}"
    return "\n".join(lines)


def _uncounted_line(uncounted: list[str]) -> str:
    """コミット済みを数えなかったツリーを言う 1 行。

    数えていないことを黙ると、そのツリーで何も起きなかったのと見分けが付かない。
    """
    return (
        f"[ccnavi] コミットに入ったぶんを数えていないツリーが {len(uncounted)} 本あります"
        f"（{', '.join(sorted(set(uncounted))[:REPORT_LIMIT])}）。"
        "ターンの始まりに基準（HEAD）を取れていません。ターンの途中で切ったワークツリーが"
        "これにあたります。そのツリーのコミットは 'git log' で確かめてください。"
    )


@dataclass
class ScopeGuard:
    """承認された作業範囲を、実行後の側から当てるための持ち物。

    実行前の判定と同じ範囲・同じ当て方を使う。別に書くと、同じ書き込みが
    実行前は通って実行後に咎められる（あるいはその逆）ことになり、
    どちらが本当の範囲なのかを誰も言えなくなる。鍵はファイルの行き先で、
    その行き先のワークツリーに結び付いた承認済みチケットの範囲を当てる。
    """

    root: str
    copies: dict[str, ticket_mod.Ticket] = field(default_factory=dict)
    # プロジェクトの置き場。ワークツリーの元リポジトリをプロジェクトまで広げる（設計 §11.3）。
    projects: str = ""
    # チケットの置き場（ツリーのルートからの相対）。提案と承認済みチケット。
    tickets: str = ""
    approved: str = ""
    # フェーズの種類。親の `project:` の層ごとに、作るときに 1 度だけ読んだもの。
    # 変更 1 件ごとに phases.yml を開かない。読めない層は空。
    types: dict[str, dict[str, phasetypes.PhaseType]] = field(default_factory=dict)

    def finding(self, full: str) -> tuple[rules.Rule, str] | None:
        """この変更が範囲の外なら、咎める文面と出所を返す。中なら None。

        範囲は実行前の判定と同じく、親の範囲と種類の上限で切り詰める（phase.scope_verdict）。
        チケットの置き場は外でも咎めない。次のチケットを提案する道を塞ぐと、
        いちど承認した範囲から永久に出られなくなる。外し方は実行前の判定と同じ関数。
        """
        t = tree.tree_of(self.root, full, self.projects)
        if t is None or t.is_main:
            return None
        ticket = tree.lookup(self.copies, t.name)
        if ticket is None:
            return None
        rel = tree.relative(t, full)
        # 外すのはチケットの置き場だけ。下書きの置き場（`scratchpad/`）はここでは外さない。
        # 見ているのは `git status`（`--ignored` を付けない）が挙げた変更なので、
        # 追跡から外れている `scratchpad/` はそもそもこの経路に現れない。現れたということは
        # そのツリーの git が `scratchpad/` を追跡しているということで、外してよい根拠
        # （追跡されないので統合先へ乗らない）が崩れている。そこは黙らせずに言う。
        if ticket_mod.is_ticket_place(rel, self.tickets, self.approved):
            return None
        parent = self.copies.get(ticket.parent) if ticket.is_child else None
        item = phase.plan_item(ticket, parent)
        pt = (
            self.types.get(parent.project, {}).get(item.type)
            if item is not None and parent is not None
            else None
        )
        found = phase.scope_verdict(ticket, parent, pt, rel)
        if not found.outside:
            return None
        area = ", ".join(ticket.paths(rules.ALLOW) + ticket.paths(rules.ASK)) or "(empty)"
        inside = (
            f"This path is inside the work area that ticket {ticket.ticket} declares ({area}) "
            f"for worktree {t.name}, but "
        )
        overflow = (
            "The ticket was approved with that overflow shown as a warning; writes there stay "
            "blocked. "
        )
        if found.limit == phase.LIMIT_BLOCKED:
            # 範囲の外に出たのではなく、チケット自体が信じられない（ADR-0058）。範囲を
            # 見せても直しようが無いので、引っかかった検査を名指しする。
            message = (
                f"The approved ticket {ticket.ticket} for worktree {t.name} does not hold "
                f"together ({ticket.blocked}), so its work area is not in effect and every "
                "change here is reported. Nothing you write can fix this: the user has to "
                "repair the approved ticket or where it sits. Tell them the problem above and "
                "ask them to run 'ccnavi --lint', which names every ticket in this state."
            )
        elif found.limit == phase.LIMIT_TYPE and found.type is not None:
            message = (
                inside + f"outside what phase type {found.type.title} ({found.type.id}) allows "
                f"({', '.join(found.type.scope_globs)}). "
                + overflow
                + "Send the output to a path that type covers, do the work in a later phase "
                "whose type covers this path, or ask the user to change phases.yml."
            )
        elif found.limit == phase.LIMIT_PARENT and parent is not None:
            parent_area = (
                ", ".join(parent.paths(rules.ALLOW) + parent.paths(rules.ASK)) or "(empty)"
            )
            message = (
                inside
                + f"outside its parent {parent.ticket} ({parent_area}). "
                + overflow
                + "Send the output inside the parent's area, or, if the task genuinely needs "
                "this path, tell the parent so it can propose a ticket whose parent covers it "
                "and ask the user to run 'ccnavi --approve'."
            )
        else:
            message = (
                f"This path is outside the work area that ticket {ticket.ticket} declares "
                f"({area}) for worktree {t.name}. Send the output inside that area, or, if the "
                "task genuinely needs this path, tell the parent so it can propose a ticket that "
                "covers it and ask the user to run 'ccnavi --approve'."
            )
        rule = rules.Rule(id=TICKET_SCOPE_RULE, message=message)
        # 出所はその写し自身の場所。写しはツリーごとに在るので、1 か所には畳めない。
        return rule, ticket.path


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
    # どのツリーで見つけたか。ワークスペースルートなら空。控えの鍵と報告に使う。
    tree_name: str = ""
    tree_root: str = ""

    def key(self) -> str:
        """控えの鍵。ツリーが違えば同じ相対パスでも別の変更。"""
        return f"{self.tree_name}|{self.change.key()}" if self.tree_name else self.change.key()


@dataclass
class Watched:
    """実行後に見るツリー 1 つと、そこに当てるルール（設計 §11.7）。

    ワークスペースのツリーにはワークスペースのルール、プロジェクトとそのワークツリーには
    そのプロジェクトのルール。実行前の判定と同じ引き方でなければ、実行前に通った
    書き込みが実行後に咎められる。
    """

    tree: tree.Tree
    rule_set: rules.RuleSet
    source: str


def _read_all(
    stderr: TextIO, watched: list[Watched], record: audit.Record
) -> list[tuple[Watched, str, list[gitstate.Change]]]:
    """見るツリーを順に git に聞く。読めないツリーは記録して飛ばす。

    1 つも読めなければ判定に至らなかったことにする。見えないことを黙らない。
    記録に残せば、監視が動いていなかった期間を後から数えられる。数えられないと、
    違反が無かったのか見ていなかったのかが同じ見た目になる。
    """
    out, failed = [], []
    for w in watched:
        top = gitstate.top_level(w.tree.root)
        changes, unreadable = gitstate.read(top)
        if unreadable:
            failed.append(f"{w.tree.name}: {unreadable}" if w.tree.name else unreadable)
            continue
        out.append((w, top, changes))
    if failed:
        note = "; ".join(failed)
        stderr.write(f"ccnavi: 作業ツリーを読めないので実行後の監視は動かない: {note}\n")
        record.detail = f"{record.detail}; {note}" if record.detail else note
    if not out:
        record.decision, record.reason = audit.SKIP, REASON_WORKTREE_UNREADABLE
    return out


def _findings(
    changes: list[gitstate.Change],
    rule_set: rules.RuleSet,
    mine: tuple[str, ...],
    source: str,
    scope: ScopeGuard | None,
    where: tree.Tree | None = None,
    top: str = "",
    places: tuple[str, str] = ("", ""),
) -> list[Finding]:
    """変更のうち、報告すべきものを返す。

    ccnavi 自身が書く場所は先に落とす。落とさないと、記録を 1 行足すたびに
    自分がその記録を違反として報告し、その報告がまた記録を 1 行増やす。

    チケットの置き場に現れた変更も、ccnavi の副命令が書いたと内容から読めるぶんだけ
    落とす（`_script_writes`）。落とさないと、`ticket start` が着手の時刻を書くたび、
    `review request` がマーカーを置くたびに、ccnavi 自身の書き込みが
    「エージェントによる書き換え」として報告され、戻す設定では戻されて手順が進まない。

    ルールの deny と ask に当たった変更は、範囲の外でもルールの側で報告する。
    範囲を広げても済まない場所だから。ここで範囲の側の文面を返すと、
    「範囲を広げれば済む」と読ませて、済まないことを 1 往復あとに知らせる。

    ルールの allow に当たる変更も、チケットの範囲は当てる。実行前の判定がルールと
    チケットの厳しい側を採るのと同じ（設計 §5）。allow を理由に飛ばすと、実行前に
    止まる書き込みがシェルから入ったときに誰も言わない。
    """
    own = tuple(os.path.realpath(p) for p in mine if p)
    name = where.name if where is not None else ""
    tree_root = where.root if where is not None else ""
    script = _script_writes(changes, places, top, where)
    found = []
    for change in changes:
        if any(change.full == p or change.full.startswith(p + os.sep) for p in own):
            continue
        if change.full in script:
            continue
        group = [rule for rule in _guarding(rule_set) if _guards_writes(rule, change.full)]
        if group:
            found.append(Finding(change, group, source, CODE_VIOLATION, name, tree_root))
            continue
        if scope is None:
            continue
        hit = scope.finding(change.full)
        if hit is not None:
            rule, place = hit
            found.append(Finding(change, [rule], place, CODE_TICKET_SCOPE, name, tree_root))
    return found


def _script_writes(
    changes: list[gitstate.Change],
    places: tuple[str, str],
    top: str,
    where: tree.Tree | None,
) -> set[str]:
    """チケットの置き場の変更のうち、ccnavi の副命令が書いたと読めるものの実パス。

    `ticket start` / `done` / `cancel` と `review request` / `check` / `ready` は、
    承認済みチケットとマーカーを書く。書いた先は `deny` と宣言された場所なので、外さないと
    自分の手順を自分で違反として報告し、戻す設定では自分で戻す。

    **誰が書いたかは記録せず、何が変わったかで答える。** 台帳はワークスペース側にあって
    git に入らないので、承認とマーカーが親のブランチに乗って届いた先（別の機械の clone）では
    1 件も残っていない。台帳で見ると、その機械でだけ報告が出る。内容で見れば同じ答えになる。

    外すのは 3 つ。

    * どちらの版も範囲を宣言していないもの（マーカー、`.risk.json`、閉じの記録）
    * スクリプトだけが書く欄（`ticket.SCRIPT_FIELDS`）以外が 1 文字も変わっていないチケット
    * `done` と `cancel` の移動。同じ姿のチケットが `doing/` から消えて、レビュー待ちか
      閉じた置き場に現れた組。片側だけなら外さない

    外さないもの——範囲や親やフェーズや本文が変わったチケット、新しく現れた承認済み
    チケット、消えただけのチケット——は今までどおり報告する。承認済みチケットの
    frontmatter はそのワークツリーの作業範囲そのもので、ここが黙ると、引数に現れない
    書き込み（シェル、ビルド、スクリプトが内部で開くファイル）で自分の範囲を広げる道ができる。

    読めなかったものは外さない。git を起こせない、期限に達した、ファイルを読めない——
    どれも「変わっていない」ではない。倒す向きを間違えると、git を数秒止めるだけで
    除外が通る。
    """
    tickets_rel, approved_rel = places
    if where is None or not top or not (tickets_rel or approved_rel):
        return set()
    here = [
        (change, rel)
        for change in changes
        for rel in [tree.relative(where, change.full)]
        if ticket_mod.is_ticket_place(rel, tickets_rel, approved_rel)
    ]
    if not here:
        return set()

    out: set[str] = set()
    # 消えた側と現れた側。組になったときだけ外す（`done` と `cancel` の移動）。
    gone: list[tuple[gitstate.Change, str, tuple[str, ...]]] = []
    arrived: list[tuple[gitstate.Change, str]] = []
    for change, rel in here:
        before, readable = gitstate.committed_text(top, change.path)
        if not readable:
            # 読めないものは外さない。読めないことは「変わっていない」ではない。
            continue
        now = fsio.read_text(change.full, errors="replace")
        # 落としてよいのは、コミット済みの版がまだ持っていない欄だけ。副命令はどれも
        # 1 度しか書かないので、既に値がある欄が変わったのなら副命令の仕業ではない
        # （`ticket.script_fields_set`）。
        drop = _droppable(before)
        if _shape(before, drop) == _shape(now, drop):
            # 同じ姿。どちらも範囲を宣言していない（マーカー・記録）か、
            # まだ無かったスクリプトの欄が足されただけか。
            out.add(change.full)
        elif now is None or _shape(now, drop) is None:
            if before is not None and ticket_mod.leaves_open_state(rel, tickets_rel, approved_rel):
                gone.append((change, before, drop))
        elif _shape(before, drop) is None and ticket_mod.lands_in_finished_state(
            rel, tickets_rel, approved_rel
        ):
            arrived.append((change, now))
    for change, before, drop in gone:
        # 行き先の姿は、消えた側の落とす欄で見る。`done` が足す `completed_at` は
        # 消えた側がまだ持っていないので落ち、着手の時刻と基準点は両側に残る。
        shape = _shape(before, drop)
        landed = [c for c, now in arrived if _shape(now, drop) == shape]
        leaving = [c for c, other, _ in gone if _shape(other, drop) == shape]
        # **組は 1 対 1 のときだけ外す。** どちらかの側に同じ姿が 2 つ以上あると、
        # どれがどれの行き先なのかを内容からは決められない。正規の移動 1 件に、
        # 同じ姿のチケットのただの削除や、行き先に直接置いた偽物が相乗りする。
        # 同じ姿ということは識別子まで同じということなので、揃うのは普通の手順では
        # 起きない。曖昧なら全部報告する側に倒す。
        if len(landed) == 1 and len(leaving) == 1:
            out.add(change.full)
            out.add(landed[0].full)
    return out


def _droppable(before: str | None) -> tuple[str, ...]:
    """姿から落としてよいスクリプトの欄。コミット済みの版がまだ持っていない欄だけ。"""
    held = ticket_mod.script_fields_set(before) if before is not None else ()
    return tuple(f for f in ticket_mod.SCRIPT_FIELDS if f not in held)


def _shape(text: str | None, drop: tuple[str, ...]) -> str | None:
    """その版の姿。無い・読めない・チケットでないなら None。"""
    return ticket_mod.script_shape(text, drop) if text is not None else None


def _guarding(rule_set: rules.RuleSet) -> list[rules.Rule]:
    """保護領域を宣言していると読むルール。`deny` と `ask` の両方。

    `ask` を入れるのは、そこが「人が 1 度見るべき場所」だとプロジェクトが
    言っている場所だから。シェルやビルドが書いたぶんは誰にも確認が出ないまま
    通っているので、あとから言う先がここしかない。

    `allow` は入れない。ルールとしては通してよいと宣言された場所なので、ルールの側から
    報告することではない。チケットの範囲は `_findings` が別に当てる。`deny` や `ask` と
    同じ場所に当たる `allow` があっても、強いほうが勝つ。当てる順は実行前の判定と同じ。
    """
    return rule_set.deny + rule_set.ask


def _restorable(finding: Finding) -> bool:
    """この 1 件を git から戻してよいか。`deny` と宣言された場所だけ。

    報告する対象（`_guarding` の `deny` + `ask` と、チケットの範囲外）より狭くしてある。
    3 つは、宣言が言っていることが違う。

    * `deny` は「書くな」。書かれたものを戻すのは、その宣言のとおりにすること
    * `ask` は「人が 1 度見る場所」。見た結果が「よい」であることもあるので、
      戻すと、人が確認に「はい」と答えた編集をあとから無かったことにする
    * チケットの範囲外は、ルールファイルが何も言っていない場所。戻す根拠が
      ルールに無いうえ、咎めているルール（`TICKET_SCOPE_RULE`）は出所を示すための
      作りもので、`decision` を持たない

    報告は 3 つとも出したままにする。戻さないことと、黙ることは別（`_guarding`）。
    設定の名前（`CCNAVI_RESTORE_IF_DENY`）が言うとおりの対象がここになる。

    戻さなかった 1 件は控えに入る（`check`）。戻していないのでファイルは汚れたままで、
    `git status` は次の呼び出しでも同じ 1 件を返す。毎回言えば、同じ汚れについて
    同じ文が呼び出しの数だけ積まれる。だから呼び出しごとの報告はセッションで 1 度だけで、
    その後はターンの終わりの報告（`at_stop`）が人に見せる。戻した 1 件だけが控えに
    入らないのは、戻したあとに同じ場所が汚れたらそれは新しい出来事だから。
    """
    if finding.change.kind == gitstate.KIND_COMMITTED:
        # コミット済みは戻す手順を持たない（`gitstate.KIND_COMMITTED`）。
        # ここに来るのはターンの終わりの報告だけだが、戻す側に混ざらないよう明示する。
        return False
    return any(rule.decision == rules.DENY for rule in finding.group)


def _guards_writes(rule: rules.Rule, path: str) -> bool:
    """このルールがこのパスへの書き込みを禁じているか。

    ルールの当て方は実行前の判定と同じ rule.matches に任せる。ここで別に
    書くと、同じルールが実行前と実行後で違う場所に当たることになり、
    どちらが正しいのかを誰も言えなくなる。
    """
    return any(rule.matches(tool, path) for tool in WRITE_TOOLS)


def _violation(
    finding: Finding,
    payload: hookio.Input,
    moved: str | None,
    would_restore: bool = False,
    not_restored: bool = False,
) -> str:
    """1 件を、それだけで読んで成立する差し戻しの文に組む。

    実行前の拒否と同じ作りにしてある。何に当たったか・どの設定が言っているか・
    直前に何が走ったか・どう戻すか。1 件だけが切り出されて見えても、
    受け取った側がそこから次の一手に行けること。

    would_restore は、戻しが予行のとき。戻す手順はそのまま載せる。今回は
    誰も戻していないので、手順を落とすと戻す手立てが 1 つも書かれていない
    報告になる。そのうえで、本番なら ccnavi が戻していたことを添える。

    not_restored は、戻す働きは効いているのに、この 1 件が戻す対象ではない
    とき（`ask` と、承認済みチケットの範囲外。`_restorable`）。手順だけを渡すと、
    受け取ったエージェントには「自分で戻せ」としか読めない。`ask` のルールは
    文面を持てない（lint が禁じる）ので、ここで言わないと誰も言わない。
    """
    change, group = finding.change, finding.group
    lines = [
        f"[ccnavi] {finding.code} ({_source(finding.source, group)})",
        f"path: {change.path} ({change.status.strip() or change.status} / {change.kind})",
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
                    if finding.code == CODE_TICKET_SCOPE
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


def _preexisting(finding: Finding) -> str:
    """セッションが始まる前から在った変更に添える文。

    戻すなと書く。ここを書き落とすと、受け取った側は違反と同じ形の通知を読んで
    同じ手順を踏み、他のセッションや人の書きかけを消しにいく。
    """
    change, group = finding.change, finding.group
    return "\n".join(
        [
            f"[ccnavi] {CODE_PREEXISTING} ({_source(finding.source, group)})",
            f"path: {change.path} ({change.status.strip() or change.status} / {change.kind})",
            "note: this was already in the working tree when ccnavi started watching this "
            "session, so the call that just ran did not cause it. Do not undo it and do not "
            "build on it: say what you found and let the user decide whose change it is.",
            _messages(group),
        ]
    )


def _source(source: str, group: list[rules.Rule]) -> str:
    """どの設定がこの場所を守ると言っているかを名指しする。

    実行前の理由（cli.reason_for）と同じで、名乗るのはルールの id。プロジェクトの
    ルールの id にはプロジェクトの名前が付くので、id だけで直しに行く先が決まる。
    id を持たないルールだけ、代わりにファイルを名乗る。
    """
    named = [rule.id for rule in group if rule.id]
    return f"rule: {','.join(named)}" if named else f"rules: {source}"


def _messages(group: list[rules.Rule]) -> str:
    """当たったルールの文面。同じパスに複数当たったら全部載せる。

    どれか 1 つを選ぶと、選ばれなかったルールの言い分は誰にも届かない。
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
    name = fsio.safe_name(session) or "unknown"
    return os.path.join(state_dir, f"{name}.json")


def _turn_path(state_dir: str, session: str) -> str:
    """ターンの基準を置く場所。セッションの控えとは別に持つ。

    2 つは寿命が違う。セッションの控えは「モデルへ 1 度伝えた」を覚えていて
    セッションが終わるまで残るが、こちらはターンごとに取り直す。同じファイルに
    まとめると、ターンの区切りでセッションの控えまで消えることになる。
    """
    name = fsio.safe_name(session) or "unknown"
    return os.path.join(state_dir, f"{name}.turn.json")


def at_prompt(
    stderr: TextIO,
    state_dir: str,
    mine: tuple[str, ...],
    watched: list[Watched],
    scope: ScopeGuard | None,
    payload: hookio.Input,
    record: audit.Record,
    places: tuple[str, str] = ("", ""),
) -> None:
    """ターンの始まり。いま保護領域に在る変更を控えて、このターンの基準にする。

    これが無いと、ターンの終わりの報告が「今そこにある変更」しか言えない。
    利用者自身の書きかけも、他のセッションが置いたものも、前のターンで
    片付けなかったものも、全部このターンの成果として並ぶ。何度も同じものを
    見せられた人は、次から読まなくなる。

    基準を取るのはターンが始まる瞬間で、そこが「エージェントがまだ何もして
    いない時点」になる。以降に現れたものだけが、このターンで起きたこと。
    """
    read = _read_all(stderr, watched, record)
    if not read:
        # 基準を取れなかった。前のターンの控えが残っていると、そこに入っている
        # HEAD を「このターンの始まり」として読むことになり、前のターンの
        # コミットをこのターンの成果として並べる。基準の側は「言い落とす」に
        # 倒れるのに、HEAD の側だけ「言い過ぎる」に倒れるので、消しておく。
        fsio.remove(_turn_path(state_dir, payload.session_id))
        return

    found = [
        f
        for w, top, changes in read
        for f in _findings(changes, w.rule_set, mine, w.source, scope, w.tree, top, places)
    ]
    # ツリーごとの HEAD も控える。ターンの終わりに「このターンでコミットに
    # 入ったもの」を数える基準になる（`git status` はコミットを見せない）。
    heads = {top: gitstate.head(top) for _, top, _ in read if top}
    _save_turn(
        stderr,
        state_dir,
        payload.session_id,
        {f.key() for f in found},
        {top: sha for top, sha in heads.items() if sha},
    )
    record.decision, record.enforced = audit.ALLOW, True
    if found:
        record.detail = f"turn baseline {len(found)}"


def _load_turn(stderr: TextIO, state_dir: str, session: str) -> tuple[set[str], bool, dict]:
    """このターンの基準を返す。2 つめの値は、基準を持っているかどうか。3 つめは各ツリーの HEAD。

    持っていないなら、ターンの始まりを見ていない。登録されていないか、
    そのイベントで読めなかったか。そこで「全部このターンの成果」として
    報告すると、前から在ったものまで並ぶので、そのときは何も言わない。
    見えていない期間を、見えたことにしない。
    """
    if not state_dir:
        return set(), False, {}
    data, failed = fsio.read_json(_turn_path(state_dir, session))
    if failed is not None:
        if not isinstance(failed, FileNotFoundError):
            stderr.write(f"ccnavi: ターンの基準を読めない: {failed}\n")
        return set(), False, {}
    base = data.get("baseline") if isinstance(data, dict) else None
    if not isinstance(base, list):
        return set(), False, {}
    heads = data.get("heads") if isinstance(data, dict) else None
    if not isinstance(heads, dict):
        # 古い控え（HEAD を持たない版）でも基準としては読める。コミットのぶんだけ
        # 言えないが、ターンの始まりを見ていないことにするより害が小さい。
        heads = {}
    return (
        {s for s in base if isinstance(s, str)},
        True,
        {k: v for k, v in heads.items() if isinstance(k, str) and isinstance(v, str)},
    )


def _save_turn(
    stderr: TextIO, state_dir: str, session: str, baseline: set[str], heads: dict[str, str]
) -> None:
    if not state_dir:
        return
    failed = fsio.write_json_atomic(
        _turn_path(state_dir, session),
        {"baseline": sorted(baseline)[:SEEN_LIMIT], "heads": dict(sorted(heads.items()))},
    )
    if failed:
        stderr.write(f"ccnavi: ターンの基準を書けない: {failed}\n")


def _load_seen(stderr: TextIO, state_dir: str, session: str) -> tuple[set[str], bool]:
    """すでに報告した変更と、このセッションで初めて見るかどうかを返す。

    読めなければ「初めて」として扱う。控えを失ったときに、前から在った変更を
    直前の実行のせいにするより、もう一度控えを取り直すほうが害が小さい。
    """
    if not state_dir:
        return set(), True
    data, failed = fsio.read_json(_seen_path(state_dir, session))
    if failed is not None:
        if not isinstance(failed, FileNotFoundError):
            stderr.write(f"ccnavi: 実行後の監視の控えを読めない: {failed}\n")
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
    failed = fsio.write_json_atomic(
        _seen_path(state_dir, session), {"seen": sorted(seen)[:SEEN_LIMIT]}
    )
    if failed:
        stderr.write(f"ccnavi: 実行後の監視の控えを書けない: {failed}\n")
