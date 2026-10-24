"""ツール実行後チェック。走ったあとのワークツリーを見て、保護領域が変わっていないかを問う。

変わったファイルを守る場所とチケットの範囲に適用する部分は `post_findings`、差し戻しの文は
`post_report` に分けてある。どちらも post を読まない。

## 実行前チェックと何が違うか

見ている対象が違う。実行前はツール呼び出しの引数を見て「これは何をするか」を
言い適用する。ここはワークツリーを見て「何が起きたか」を読む。前者を厳しくしても
後者は要る。引数に現れない書き込み（ビルドの出力先、スクリプトが内部で開く
ファイル、読めなかったシェル構文）は、言い適用する限りいつまでも検出できないから。

止められないことも違う。このイベントにはツール呼び出しを取り消す手段が無く、
返せるのは文だけになる。だから文が「何が変わったか」だけで終わってはいけない。
戻す手順を対象ごとに書く。手順の無い通知は、受け取った側に
戻し方を自分で考えさせることになり、そこで対象が増えたり減ったりする。

## 保護領域をどこから知るか

ルールファイルから知る。`deny` と `ask` のタイプにあって、`match` に書き込み系の
ツールを含むルールは、「この場所はエージェントに好きに書かせない」と
プロジェクトが宣言したものなので、そのままここでの保護領域になる。宣言を
2 か所に分けて書かせない。分ければ必ず食い違い、食い違った側は誰にも
気づかれないまま判定が緩む。

`ask` も保護領域に数えるのは、そこが「ユーザが 1 度見るべき場所」だから。
実行前チェックは引数を見て確認を出すが、シェルやビルドが書いたぶんは
引数に現れないので、誰にも確認が出ないまま通っている。あとから言う先がここしかない。

適用する先は git が返したパスを解いた絶対パス。実行前チェックがファイルのパスを
解いてから適用するのと同じ理由で、表記を変えただけで外せてはいけない。

## 前から在った変更を原因にしない

ワークツリーは、セッションが始まる前から汚れていることがある。他のセッションの
書きかけ、ユーザが直している最中のもの。それを「直前の実行が壊した」として
差し戻すと、エージェントは他人の作業を戻しにいく。だから、セッションの開始の
時点（SessionStart）に在ったものを基準として記録する（`capture_baseline`）。基準が
取れなかったセッションでは、初回の実行後チェックが同じ記録を取る。以降は、基準に
無い新しい汚れだけを原因付きで報告する。記録した側も 1 度は伝えるが、文面を分けて、
戻すなと明示する。
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from typing import TextIO

from ..infra import fsio, gitstate, hookio, pathcase, settings, tree
from ..policy import rules, selfguard
from ..records import audit
from ..tickets import (
    phase_forms,
    ticket_places,
)
from . import post_findings, post_report

# ワークツリーを変えようがないツール。ここを飛ばすぶん git を起こす回数が減る。
# 飛ばしてよいのは「このツールが走った直後に確かめなくても、次に走る
# 書き込みうるツールの直後に同じ変更が見える」から。見落としではなく遅れ。
#
# 名前を並べる側は狭く保つ。MCP のツールは名前を自由に付けられるので、
# 知らない名前は「書けるかもしれない」側に置く。
READ_ONLY_TOOLS = ("Read", "Grep", "Glob", "WebFetch", "WebSearch")

# 判定に至らなかった理由のうち、このチェックだけが出すもの。
REASON_TOOL_CANNOT_WRITE = "tool-cannot-write"
REASON_WORKTREE_UNREADABLE = "worktree-unreadable"
# ターンの始まりを見ていないので、このターンで起きたことを切り出せない。
# 記録に残す。ターンの終わりの報告が何も出さなかった期間を、後から数えられるように。
REASON_NO_TURN_BASELINE = "no-turn-baseline"

# 1 回の報告に載せる件数の上限。ビルドが生成物を数百件置くことがあり、
# 全部を並べると本文が流れて 1 件も読まれない。
REPORT_LIMIT = 12

# ターンの基準（`_save_turn`）を切る件数の上限。セッションが長引いても膨らまないように。
# 報告済みの記録は切らない。切ると、切った先の汚れが次の呼び出しで新しい変更になり、
# 戻されてしまうため（`_save_reported`）。
BASELINE_LIMIT = 500


def check(
    stderr: TextIO,
    enforcing: bool,
    restore: str,
    state_dir: str,
    mine: tuple[str, ...],
    watched: list[Watched],
    scope: post_findings.ScopeGuard | None,
    payload: hookio.Input,
    record: audit.Record,
    places: tuple[str, str] = ("", ""),
    root: str = "",
) -> tuple[str, bool]:
    """実行後の 1 回ぶんを処理し、（モデルに返す文, ワークツリーを戻そうとしたか）を返す。

    返す文が無ければ空文字。2 つ目は、戻しを 1 件でも試みたら真（戻せなかった分を含む）。
    呼び手はこれで、戻しで動いたかもしれないファイル（チケットの置き場など）を読み直すか決める。

    enforcing は、このモードが判定を実際に適用する側かどうか。適用しない側
    （dry-run）では復元も行わない。呼び出しにもワークツリーにも手を出さないことが
    そのモードの約束なので、自分の判断でファイルを動かしては意味がない。

    mine は ccnavi 自身が書く場所。記録と state がそれで、置き場は設定で動くので、
    ルールが守る場所の中を指すこともある。自分の書き込みを自分の違反として
    報告しはじめると、実行後チェックは 1 回目から嘘しか言わなくなる。

    watched は見るツリー。ワークスペースルートと、この呼び出しが触ったツリー。
    ツリーごとに git を起こし、そのツリーのルールで見る。

    places はチケットの置き場（提案と承認済みチケット）。そこに現れた変更のうち、
    ccnavi のサブコマンドが書いたと内容から読めるものを外す（`_script_writes`）。

    処理フロー:
    1. 読み取りだけのツールなら何もせず抜ける（`READ_ONLY_TOOLS`）。
    2. 見るツリーを読み（`_read_all`）、ターンの基準に居ないツリーの HEAD を記録する
       （`_note_new_trees`）。
    3. 報告済みの記録と基準を読み、違反を集める（`_found`）。無ければ基準を作って抜ける。
    4. 違反を新しいもの・報告済みのものに分ける。受け入れたあとに中身が変わったもの
       （`shadowed`）と横で作業する変更の一部は、新しいものとして扱う。
    5. 基準の有無から、前から在ったものを伝える分（`carried`）を分ける。
    6. 戻す設定（ENABLE）なら、`deny` と宣言された場所の新しい変更を戻す（`_restore`）。
       dry-run なら、戻していたはずのものを予行として言う（`would`）。
    7. 記録を保存し、記録の code と detail を残す。
    8. 違反ごとの文（`post_report._violation`・`post_report._preexisting`）を組み、
       件数の上限で切って返す。
    """
    if payload.tool_name in READ_ONLY_TOOLS:
        record.decision, record.reason = audit.SKIP, REASON_TOOL_CANNOT_WRITE
        return "", False

    # 見るツリーを読む。読めなければ何もしない。
    read = _read_all(stderr, watched, record)
    if not read:
        return "", False

    # ターンの基準を持たないツリーを初めて見たら、その場で記録する。ターンの途中で
    # 切られたワークツリーがこれにあたる（`at_prompt` のときには無いので基準が無い）。
    # ここで記録しなくても、ターンの終わりは分岐点（`gitstate.fork_base`）から数える。
    # 記録するのは、触った時点の HEAD をより正確な基準にするため。
    #
    # 触られないまま終わったツリーには、ここでも基準が付かない。分岐点も取れなければ
    # ターンの終わりが「数えていない」と言う（`_uncounted_line`）。
    _note_new_trees(stderr, state_dir, payload.session_id, read)

    reported, baseline, announced = _load_reported(stderr, state_dir, payload.session_id)

    found = _found(read, mine, scope, places, root)
    if not found:
        # 違反が無くても、基準を作る。ここを飛ばすと、綺麗なワークツリーで
        # 始まったセッションはいつまでも「基準なし」のままになり、その後に現れた
        # 汚れが全部「前から在ったもの」になってしまい、誰も差し戻されなくなる。
        # 起動時の基準が在っても、そこに在ったものを伝えたことにするのは、ここで
        # 何も無いことを確かめたあとに限る。
        # 読めなかったツリーが在るなら、その汚れは見えていない。記録を空にすると、
        # 読めるようになったあとに起動時の汚れが新しい変更として戻されるので、書かない。
        if (not baseline or not announced) and len(read) == len(watched):
            _save_reported(stderr, state_dir, payload.session_id, set())
        record.decision, record.enforced = audit.ALLOW, True
        return "", False

    fresh = [f for f in found if f.key() not in reported]
    known = [f for f in found if f.key() in reported]
    # ユーザの発言で受け入れた変更（`at_prompt`）のうえに、そのあと中身が変わったもの。キーは
    # 種類とパスだけなので、受け入れたファイルへの以降の書き込みが全部「すでに知っている変更」に
    # なってしまう。受け入れたときの指紋と違えば、新しい変更として扱う。ただし戻さない。戻す先の
    # コミット済みの内容は、ユーザが受け入れさせた編集より古く、戻すとその編集まで消えるため。
    shadowed: set[str] = set()
    accepted = _load_accepted(state_dir, payload.session_id)
    if accepted:
        for f in known:
            if f.key() in accepted and _fingerprint(f) != accepted[f.key()]:
                shadowed.add(f.key())
        fresh += [f for f in known if f.key() in shadowed]
        known = [f for f in known if f.key() not in shadowed]
        if shadowed:
            # 同じ変更を呼び出しのたびに言わない。言ったぶんは今の指紋を受け入れ済みに直す。
            accepted.update({f.key(): _fingerprint(f) for f in fresh if f.key() in shadowed})
            _save_accepted(stderr, state_dir, payload.session_id, accepted)
    side_roots = _side_roots(read)
    # 横で作業するチェックアウトの変更は戻さないので、キーが報告済みのままだと、同じファイルへの
    # 以降の書き込みが見えなくなる。報告したときの指紋と違えば、新しい変更として言い直す。
    side_seen = _load_accepted(state_dir, payload.session_id)
    side_changed = [
        f
        for f in known
        if _in_side(f, side_roots)
        and _restorable(f)
        and f.key() not in shadowed
        and (side_seen.get(_SIDE_PREFIX + f.key()) or _fingerprint(f)) != _fingerprint(f)
    ]
    if side_changed:
        fresh += side_changed
        known = [f for f in known if f not in side_changed]
    side_marks = {
        _SIDE_PREFIX + f.key(): _fingerprint(f)
        for f in fresh
        if _in_side(f, side_roots) and _restorable(f) and f.key() not in shadowed
    }
    if side_marks and any(side_seen.get(k) != v for k, v in side_marks.items()):
        side_seen.update(side_marks)
        _save_accepted(stderr, state_dir, payload.session_id, side_seen)
    # 基準が無いセッションでは、今そこに在るものは直前の実行の結果ではない。
    # 記録を取るだけにして、原因を付けずに 1 度だけ伝える。
    # 基準が在るなら、起動時に在ったものが、まだ伝えられていない分にヒットする。
    # 同じく 1 度だけ伝える。伝えたあとは、同じものを繰り返さない。
    if not baseline:
        carried, fresh = fresh, []
    elif not announced:
        carried, known = known, []
    else:
        carried = []

    restored: dict[str, str] = {}
    # 戻しを試みたか。戻せなかった 1 件も途中までファイルを動かしたかもしれないので含める。
    tried = False
    # 戻すのは `deny` と宣言された場所だけ（`_restorable`）。報告する対象より狭い。
    # restore には CCNAVI_MODE を掛けたあとの値が来る（modes.effective_setting）ので、
    # ここで enforcing を見る必要はない。掛ける場所を 1 か所にまとめてあるのは、
    # 2 つの設定が別々にモードを解釈して食い違うのを防ぐため。
    # 戻すのはそのツリーの git で。キーはツリー付きにして、別のツリーの同じ相対パスと
    # 取り違えないようにする。
    if restore == selfguard.ENABLE:
        for w, top, _ in read:
            if w.side_by_side:
                continue
            here = [
                f
                for f in fresh
                if _same_tree(f, w.tree.root) and _restorable(f) and f.key() not in shadowed
            ]
            if not here:
                continue
            tried = True
            done = _restore(stderr, top, state_dir, [f.change for f in here])
            restored.update({f.key(): done[f.change.path] for f in here if f.change.path in done})
    # dry-run では戻さない代わりに、戻していたはずだと言う。selfguard の
    # would-restore と同じ扱いにしてある。戻しもせず何も出さないと、報告を読んだ側には
    # 戻しを切った形と見分けが付かない。予行として置いた設定が「戻しは要らない」
    # という結論に読み替えられるし、対象がルールファイル次第で動くこのチェックでは、
    # 何が戻るのかを本番の前に見せることがそのまま安全の余裕になる。
    # 予行で言うのも、本番で戻すのと同じ集合に限る。戻さない 1 件に「本番なら
    # 戻していた」と書くと、設定を enable にしたときに起きることを読み違えさせる。
    would = (
        {
            f.key()
            for f in fresh
            if _restorable(f) and f.key() not in shadowed and not _in_side(f, side_roots)
        }
        if restore == selfguard.DRY_RUN
        else set()
    )

    _save_reported(
        stderr,
        state_dir,
        payload.session_id,
        reported
        # 戻せたものは記録に入れない。同じ場所がもう一度汚れたら、それは
        # すでに知っている変更ではなく新しい出来事なので、もう一度言う。
        | {f.key() for f in fresh if f.key() not in restored}
        | {f.key() for f in carried},
        announced=True,
    )

    record.paths = [f"{f.change.kind} {f.change.path}" for f in fresh]
    record.rules = sorted({rules.record_name(rule) for f in fresh for rule in f.group})
    # 既にある detail を先頭に残す。ルールを読めなかったときのファイル名が
    # そこに入っていて、上書きすると「どのファイルが読めなかったか」が消える。
    notes = [record.detail] if record.detail else []
    mixed = post_findings.codes_note(fresh)
    if mixed:
        notes.append(mixed)
    if carried:
        notes.append(f"preexisting {len(carried)}")
    if known:
        notes.append(f"known {len(known)}")
    side_fresh = [f for f in fresh if _in_side(f, side_roots) and _restorable(f)]
    if side_fresh:
        notes.append(f"side-by-side {len(side_fresh)}")
    if restored:
        notes.append(f"restored {len(restored)}")
    elif would:
        notes.append(f"{selfguard.ACTION_WOULD} {len(would)}")
    record.detail = "; ".join(notes)

    if fresh:
        record.decision, record.enforced = audit.DENY, enforcing
        # 記録の code は 1 つ。件の code が混ざった回は強いほうを載せ、内訳は上の notes に書いた。
        record.code = post_findings.record_code(fresh)
    else:
        # この呼び出しは何も汚していない。記録した変更の報告は状態の通知であって、
        # 直前の実行についての判定ではない。
        record.decision, record.enforced = audit.ALLOW, True
        if carried:
            # 判定は allow のまま、文面で知らせた根拠（POST_PREEXISTING）だけを残す。
            # 文面と記録を同じ材料から作り、記録から「知らせた回」を数えられるように。
            record.code = post_report.CODE_PREEXISTING

    gated = restore in (selfguard.ENABLE, selfguard.DRY_RUN)
    git = settings.git_wrapper_command(root)
    blocks = [
        post_report._violation(
            f,
            payload,
            restored.get(f.key()),
            f.key() in would,
            not_restored=gated and (not _restorable(f) or f.key() in shadowed),
            git=git,
            shadowed=f.key() in shadowed,
            side_by_side=_in_side(f, side_roots) and _restorable(f),
        )
        for f in fresh
    ]
    # 件数の上限は下の `shown` で切る。ここで切ると、落とした件数が「ほか N 件」に入らない。
    blocks += [post_report._preexisting(f) for f in carried]
    if not blocks:
        return "", tried

    shown, dropped = blocks[:REPORT_LIMIT], len(blocks) - REPORT_LIMIT
    if dropped > 0:
        shown.append(
            f"[ccnavi] {dropped} more changed paths in protected areas are not listed here. "
            + (
                f"Run '{git} status' to see the rest before you undo anything."
                if git
                else "Ask the user to check the rest with git status before you undo anything."
            )
        )
    return "\n\n".join(shown), tried


def _note_new_trees(
    stderr: TextIO,
    state_dir: str,
    session: str,
    read: list[tuple[Watched, str, list[gitstate.Change]]],
) -> None:
    """ターンの基準にまだ居ないツリーの HEAD を記録する。居るツリーには触らない。

    書き直すのは、記録に無いツリーが 1 本でもあるときだけ。呼び出しのたびに
    記録を書き直すと、ツールを打つ数だけ書き込みが増える。ターンの基準そのもの
    （`baseline`）は動かさない。あれは「ターンの始まりに何が汚れていたか」で、
    あとから足すと、このターンで現れた汚れを前から在ったことにしてしまう。
    """
    baseline, known, heads = _load_turn(stderr, state_dir, session)
    if not known:
        # ターンの始まりを見ていない。基準そのものが無いので、ここで HEAD だけを
        # 置くと「基準はあるが baseline が空」になってしまう。何もしない。
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
    scope: post_findings.ScopeGuard | None,
    places: tuple[str, str],
) -> tuple[list[post_findings.Finding], list[str]]:
    """このターンでコミットに入った、保護領域の変更。

    見るのは、ターンの始まりに記録した HEAD からの差分（`gitstate.committed`）。
    記録を持たないツリー（ターンの途中で切られた）は、記録済みの他のツリーとの
    分岐点（`gitstate.fork_base`）を基準にする。それも取れないものは飛ばす。

    チケットの置き場は外す。承認はユーザが提案を `.ccnavi/approved/` へ動かして
    コミットする進め方で（`ccnavi-user-push-approved.sh`）、その置き場は `deny` でもある。
    外さないと、ユーザが承認するたびに、その操作が違反としてターンの報告に並ぶ。
    置き場のパスは `places` で受け取る。チケット制御を切ったワークスペースでも
    承認のコミットは在りうるので、`scope` の有無で外れたり外れなかったりさせない。

    2 つめに返すのは、数えなかったツリーの名前。基準が
    無いのは、ターンの途中で切られたツリー（プロンプトのときに無かった）か、
    そのとき HEAD を読めなかったツリーで、分岐点も取れないものがここにヒットする。
    「変わっていない」ではないので、そのことを言わないと、見ていない期間が
    「何も起きなかった」と同じ見た目になる。
    """
    out: list[post_findings.Finding] = []
    uncounted: list[str] = []
    tickets, approved = places
    for w, top, _ in read:
        base = heads.get(top)
        if not base:
            # ターンの途中で切られたツリー。記録済みの他のツリーの HEAD との分岐点を基準にする。
            base = gitstate.fork_base(top, [sha for t, sha in heads.items() if t != top])
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
        # `integrate-done` が積んだ squash だけが変えたパスは外す（git で確かめたものだけ）。
        # 片付けの後なので staged の除外は効かず、正規の取り込みが毎回報告に並ぶため。
        integrated = post_findings.integrated_paths(top, base) if changes else set()
        for finding in post_findings._findings(changes, w.rule_set, mine, w.source, scope, w.tree):
            if finding.change.path in integrated:
                continue
            rel = tree.relative(w.tree, finding.change.full)
            if ticket_places.is_ticket_place(rel, tickets, approved):
                continue
            out.append(finding)
    return out, uncounted


def at_stop(
    stderr: TextIO,
    state_dir: str,
    mine: tuple[str, ...],
    watched: list[Watched],
    scope: post_findings.ScopeGuard | None,
    payload: hookio.Input,
    record: audit.Record,
    places: tuple[str, str] = ("", ""),
    root: str = "",
) -> str:
    """ターンの終わりに、このターンで変わった保護領域をユーザへ報告する。

    呼び出しごとの報告とは宛先も目的も違う。あちらはモデルが読んで次の動きを
    変えるためのもので、こちらはユーザが「このターンで何が変わったか」を 1 度で
    見るためのものになる。

    セッションの記録（`reported`）は見ない。あれは「モデルへ 1 度伝えた」を
    覚えているもので、ユーザはまだ 1 度も見ていないことがある。代わりに見るのは
    ターンの始まりに取った基準で、そこに無いものだけが、このターンで起きたこと。

    戻しもしない。ここで報告するのは、実行後チェックが戻さなかった、あるいは
    戻す設定になっていなかった変更で、どうするかはユーザが決める。

    処理フロー:
    1. ターンの基準と、各ツリーの HEAD を読む（`_load_turn`）。基準が無ければ何も言わずに抜ける。
    2. 見るツリーを読み（`_read_all`）、基準に無い違反を集める（`post_findings._findings`）。
    3. このターンでコミットに入った違反を足す（`_committed_findings`）。数えていないツリーがあれば、
       その旨の 1 行を添える（`_uncounted_line`）。
    4. 違反も数えていない分も無ければ、記録を残して抜ける。
    5. 違反ごとに、戻す手順（`gitstate.undo`）と対象のルールを 1 行にまとめる。
       上限を超える分は件数で言う。
    """
    baseline, known_baseline, heads = _load_turn(stderr, state_dir, payload.session_id)
    if not known_baseline:
        # ターンの始まりを見ていない。ここで今そこに在るものを全部並べると、
        # ユーザの書きかけも他のセッションが置いたものも、このターンの成果として
        # 報告することになる。
        record.decision, record.reason = audit.SKIP, REASON_NO_TURN_BASELINE
        return ""

    read = _read_all(stderr, watched, record)
    if not read:
        return ""

    found = [
        f
        for w, top, changes in read
        for f in post_findings._findings(
            changes, w.rule_set, mine, w.source, scope, w.tree, top, places, root
        )
        if f.key() not in baseline
    ]
    # このターンでコミットに入ったぶんも足す。`git status` はコミットを見せないので、
    # ここを足さないと、保護領域を汚してからコミットした回が「何も起きなかった」と
    # 同じ見た目になる（呼び出しごとの実行後チェックも同じ見落としがあるが、そちらは戻しに関わるので
    # 断面を変えない。ユーザが 1 度で見るのはこの報告）。
    side_roots = _side_roots(read)
    committed, uncounted = _committed_findings(stderr, read, heads, mine, scope, places)
    found += committed
    if not found and not uncounted:
        record.decision, record.enforced = audit.ALLOW, True
        return ""
    git = settings.git_wrapper_command(root)
    if not found:
        # 違反は無いが、数えていないツリーがある。そのことだけを言う。
        record.decision, record.enforced = audit.ALLOW, True
        _add_detail(record, f"uncounted {len(uncounted)}")
        return _uncounted_line(uncounted, git=git)

    record.decision, record.enforced = audit.DENY, True
    record.code = post_findings.record_code(found)
    record.paths = [f"{f.change.kind} {f.change.path}" for f in found]
    record.rules = sorted({rules.record_name(rule) for f in found for rule in f.group})
    # detail は足していく。読めなかったルールファイルの名前（`_read_all`）が
    # 先に入っていることがある。
    _add_detail(record, post_findings.codes_note(found))

    lines = [
        f"[ccnavi] 守ると宣言した場所が、この{phase_forms.TURN_DEFINED}で"
        f" {len(found)} 件変わりました。"
    ]
    for finding in found[:REPORT_LIMIT]:
        rule = finding.group[0]
        where = f"{finding.tree_name}: " if finding.tree_name else ""
        undo = gitstate.undo(finding.change, git)
        how = (
            f"戻すなら {undo}"
            if undo
            else (
                f"{finding.change.path} はすでにコミット済みなので、"
                "取り込む前に中身を確かめ、不要な変更なら取り消してください"
                if finding.change.kind == gitstate.KIND_COMMITTED
                else f"{finding.change.path} を元に戻す操作はユーザに頼んでください"
            )
        )
        kind = gitstate.KIND_LABELS.get(finding.change.kind, finding.change.kind)
        origin = (
            f" {post_report.SIDE_BY_SIDE_LABEL}。"
            if _in_side(finding, side_roots) and _restorable(finding)
            else ""
        )
        lines.append(
            f"  {where}{finding.change.path}（{kind}）"
            f" 対象のルール: {rules.record_name(rule)} /{origin} {how}"
        )
    if len(found) > REPORT_LIMIT:
        all_at = (
            f"全部は {git} status に出ます。"
            if git
            else "全部はユーザに git status で確かめてもらってください。"
        )
        lines.append(f"  ほか {len(found) - REPORT_LIMIT} 件。{all_at}")
    if uncounted:
        lines.append(_uncounted_line(uncounted, define=False, git=git))
        _add_detail(record, f"uncounted {len(uncounted)}")
    return "\n".join(lines)


def _add_detail(record: audit.Record, note: str) -> None:
    """記録の detail に 1 つ足す。

    上書きすると、先に入った断り（読めなかったファイルなど）が消える。
    """
    if note:
        record.detail = f"{record.detail}; {note}" if record.detail else note


def _uncounted_line(uncounted: list[str], *, define: bool = True, git: str | None = None) -> str:
    """コミット済みを数えなかったツリーを言う 1 行。

    数えていないことを言わないと、そのツリーで何も起きなかったのと見分けが付かない。
    `define` は「ターン」の定義をつけるか。同じ報告の前の行でつけていれば外す。
    """
    names = sorted(set(uncounted))
    shown = ", ".join(names[:REPORT_LIMIT])
    trees = f"ワークツリー {shown} "
    if len(names) > 1:
        trees = f"ワークツリー {len(names)} 本（{shown}）"
    turn = phase_forms.TURN_DEFINED if define else "ターン"
    return (
        f"[ccnavi] ccnavi は{turn}ごとに、その間に作られたコミットが保護領域のファイルを"
        "変更していないかを確認します。"
        f"{trees}では、今回のターンでこの確認ができませんでした。"
        "ターン開始時の HEAD が記録されていないか、差分を読み取れなかったためです。"
        "ターンの途中で作り、一度も触らなかったワークツリーでよく起きます。"
        "このツリーでコミットしていなければ対応は不要です。"
        + (
            "コミットした場合は、保護領域のファイルを変更していないか"
            f"`{git} log` で確認してください。"
            if git
            else "コミットした場合は、保護領域のファイルを変更していないか"
            "ユーザに確かめてもらってください。"
        )
    )


@dataclass
class Watched:
    """実行後に見るツリー 1 つと、そこに適用するルール。

    ワークスペースのツリーにはワークスペースのルール、プロジェクトとそのワークツリーには
    そのプロジェクトのルール。実行前チェックと同じ引き方でなければ、実行前に通った
    書き込みが実行後に報告される。
    """

    tree: tree.Tree
    rule_set: rules.RuleSet
    source: str
    # 横で作業する指定が有効なメインのチェックアウト（`infra.directrec`）。ここで新しく見つかった
    # 変更は、ユーザか別のセッションのものかもしれないので、戻さず報告だけにする。
    side_by_side: bool = False


def _read_all(
    stderr: TextIO, watched: list[Watched], record: audit.Record
) -> list[tuple[Watched, str, list[gitstate.Change]]]:
    """見るツリーを順に git に聞く。読めないツリーは記録して飛ばす。

    1 つも読めなければ判定に至らなかったことにする。見えないことを伏せずに言う。
    記録に残せば、実行後チェックが動いていなかった期間を後から数えられる。数えられないと、
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
        stderr.write(f"ccnavi: ワークツリーを読めないので実行後チェックは動かない: {note}\n")
        record.detail = f"{record.detail}; {note}" if record.detail else note
    if not out:
        record.decision, record.reason = audit.SKIP, REASON_WORKTREE_UNREADABLE
    return out


def _restorable(finding: post_findings.Finding) -> bool:
    """この 1 件を git から戻してよいか。`deny` と宣言された場所だけ。

    報告する対象（`_guarding` の `deny` + `ask` と、チケットの範囲外）より狭くしてある。
    3 つは、宣言が言っていることが違う。

    * `deny` は「書くな」。書かれたものを戻すのは、その宣言のとおりにすること
    * `ask` は「ユーザが 1 度見る場所」。見た結果が「よい」であることもあるので、
      戻すと、ユーザが確認に「はい」と答えた編集をあとから無かったことにする
    * チケットの範囲外は、ルールファイルが何も言っていない場所。戻す根拠が
      ルールに無いうえ、報告しているルール（`TICKET_SCOPE_RULE`）は出所を示すための
      作りもので、`decision` を持たない

    報告は 3 つとも出したままにする。戻さないことと、報告しないことは別（`_guarding`）。
    設定の名前（`CCNAVI_RESTORE_IF_DENY`）が言うとおりの対象がここになる。

    戻さなかった 1 件は記録に入る（`check`）。戻していないのでファイルは汚れたままで、
    `git status` は次の呼び出しでも同じ 1 件を返す。毎回言えば、同じ汚れについて
    同じ文が呼び出しの数だけ積まれる。だから呼び出しごとの報告はセッションで 1 度だけで、
    その後はターンの終わりの報告（`at_stop`）がユーザに見せる。戻した 1 件だけが記録に
    入らないのは、戻したあとに同じ場所が汚れたらそれは新しい出来事だから。
    """
    if finding.change.kind == gitstate.KIND_COMMITTED:
        # コミット済みは戻す手順を持たない（`gitstate.KIND_COMMITTED`）。
        # ここに来るのはターンの終わりの報告だけだが、戻す側に混ざらないよう明示する。
        return False
    return any(rule.decision == rules.DENY for rule in finding.group)


def _restore(
    stderr: TextIO, top: str, state_dir: str, changes: list[gitstate.Change]
) -> dict[str, str]:
    """戻せたものを {パス: 退避先（戻しただけなら空文字）} で返す。

    戻せなかったものは返す値から除く。復元は報告の代わりではないので、
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


def _reported_path(state_dir: str, session: str) -> str:
    """報告済みの変更の記録。"""
    name = fsio.safe_name(session) or "unknown"
    return os.path.join(state_dir, f"{name}.reported.json")


def _accepted_path(state_dir: str, session: str) -> str:
    """ユーザの発言で受け入れた変更の、受け入れたときの指紋の置き場。

    報告済みの記録（`_reported_path`）とは別に持つ。あちらはキーの集合で、切らずに増やす。
    こちらはキーごとに指紋を持ち、発言のたびに更新する。
    """
    name = fsio.safe_name(session) or "unknown"
    return os.path.join(state_dir, f"{name}.accepted.json")


def _load_accepted(state_dir: str, session: str) -> dict[str, str]:
    """{キー: 指紋}。無い・読めないなら空（受け入れた変更が無いのと同じに読む）。"""
    if not state_dir:
        return {}
    data = fsio.read_dict(_accepted_path(state_dir, session)) or {}
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}


def _save_accepted(stderr: TextIO, state_dir: str, session: str, accepted: dict[str, str]) -> None:
    if not state_dir:
        return
    failed = fsio.write_json_atomic(
        _accepted_path(state_dir, session), dict(sorted(accepted.items()))
    )
    if failed:
        stderr.write(f"ccnavi: 受け入れた変更の記録を保存できませんでした: {failed}\n")


# 横で作業するチェックアウトの変更を報告したときの指紋は、受け入れた変更（`accepted`）と同じ
# ファイルに、この接頭辞を付けたキーで置く。接頭辞があるので、受け入れた変更とは取り違えない。
_SIDE_PREFIX = "side:"


def _side_roots(read: list[tuple[Watched, str, list[gitstate.Change]]]) -> set[str]:
    """横で作業する指定が有効なツリーのルート。名前ではなくパスで持つ（別のツリーに同じ名前がありうる）。"""
    return {pathcase.fold(w.tree.root) for w, _, _ in read if w.side_by_side}


def _same_tree(finding: post_findings.Finding, root: str) -> bool:
    return bool(finding.tree_root) and pathcase.fold(finding.tree_root) == pathcase.fold(root)


def _in_side(finding: post_findings.Finding, roots: set[str]) -> bool:
    return bool(finding.tree_root) and pathcase.fold(finding.tree_root) in roots


def _fingerprint(finding: post_findings.Finding) -> str:
    """変更されたファイルの中身の指紋。消えているか読めないときは `-`。

    受け入れたあとにエージェントが同じファイルを書き換えたかを、キー（種類とパス）とは別に見分ける。
    """
    digest = hashlib.sha256()
    try:
        with open(finding.change.full, "rb") as f:
            while chunk := f.read(1024 * 1024):
                digest.update(chunk)
    except OSError:
        return "-"
    return digest.hexdigest()


def _turn_path(state_dir: str, session: str) -> str:
    """ターンの基準を置く場所。セッションの記録とは別に持つ。

    2 つは寿命が違う。セッションの記録は「モデルへ 1 度伝えた」を覚えていて
    セッションが終わるまで残るが、こちらはターンごとに取り直す。同じファイルに
    まとめると、ターンの区切りでセッションの記録まで消えることになる。
    """
    name = fsio.safe_name(session) or "unknown"
    return os.path.join(state_dir, f"{name}.turn.json")


def at_prompt(
    stderr: TextIO,
    state_dir: str,
    mine: tuple[str, ...],
    watched: list[Watched],
    scope: post_findings.ScopeGuard | None,
    payload: hookio.Input,
    record: audit.Record,
    places: tuple[str, str] = ("", ""),
    root: str = "",
    accept: bool = False,
) -> None:
    """ターンの始まり。いま保護領域に在る変更を記録して、このターンの基準にする。

    これが無いと、ターンの終わりの報告が「今そこにある変更」しか言えない。
    ユーザ自身の書きかけも、他のセッションが置いたものも、前のターンで
    片付けなかったものも、全部このターンの成果として並ぶ。何度も同じものを
    見せられたユーザは、次から読まなくなる。

    基準を取るのはターンが始まるときで、そこが「エージェントがまだ何もして
    いない時点」になる。以降に現れたものだけが、このターンで起きたこと。

    accept は、ユーザの発言で始まるターンのとき真。今そこにある変更を報告済みの記録にも足し、
    次の実行後チェックが「この呼び出しが起こした変更」として戻さないようにする。足さないと、
    セッションの途中でユーザが `deny` の場所を直した分が、次のツール呼び出しで新しい変更に
    見えて、コミット済みの内容へ戻される。記録は起動時の基準（`capture_baseline`）が
    取れているセッションにだけ足す。基準が無いセッションの今の変更は、初回の実行後チェックが
    原因を付けずに伝える。
    """
    read = _read_all(stderr, watched, record)
    if not read:
        # 基準を取れなかった。前のターンの記録が残っていると、そこに入っている
        # HEAD を「このターンの始まり」として読むことになり、前のターンの
        # コミットをこのターンの成果として並べる。基準の側は「言い落とす」ほうに
        # なるのに、HEAD の側だけ「言い過ぎる」ほうになるので、消しておく。
        fsio.remove(_turn_path(state_dir, payload.session_id))
        return

    found = _found(read, mine, scope, places, root)
    # ツリーごとの HEAD も記録する。ターンの終わりに「このターンでコミットに
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
    if accept and found:
        reported, baseline, announced = _load_reported(stderr, state_dir, payload.session_id)
        if baseline:
            _save_reported(
                stderr,
                state_dir,
                payload.session_id,
                reported | {f.key() for f in found},
                announced=announced,
            )
            accepted = _load_accepted(state_dir, payload.session_id)
            accepted.update({f.key(): _fingerprint(f) for f in found})
            _save_accepted(stderr, state_dir, payload.session_id, accepted)


def _load_turn(stderr: TextIO, state_dir: str, session: str) -> tuple[set[str], bool, dict]:
    """このターンの基準を返す。2 つめの値は、基準を持っているかどうか。3 つめは各ツリーの HEAD。

    持っていないなら、ターンの始まりを見ていない。登録されていないか、
    そのイベントで読めなかったか。そこで「全部このターンの成果」として
    報告すると、前から在ったものまで並ぶので、そのときは何も言わない。
    `heads` を持たない記録（破損したもの）も、記録が無いときと同じく基準なしとして扱う。
    """
    if not state_dir:
        return set(), False, {}
    data, failed = fsio.read_json(_turn_path(state_dir, session))
    if failed is not None:
        if not isinstance(failed, FileNotFoundError):
            stderr.write(f"ccnavi: {phase_forms.TURN_DEFINED}の基準を読めない: {failed}\n")
        return set(), False, {}
    base = data.get("baseline") if isinstance(data, dict) else None
    if not isinstance(base, list):
        return set(), False, {}
    heads = data.get("heads")
    if not isinstance(heads, dict):
        return set(), False, {}
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
        {"baseline": sorted(baseline)[:BASELINE_LIMIT], "heads": dict(sorted(heads.items()))},
    )
    if failed:
        stderr.write(f"ccnavi: {phase_forms.TURN_DEFINED}の基準を保存できませんでした: {failed}\n")


def _found(
    read: list[tuple[Watched, str, list[gitstate.Change]]],
    mine: tuple[str, ...],
    scope: post_findings.ScopeGuard | None,
    places: tuple[str, str],
    root: str,
) -> list[post_findings.Finding]:
    """読んだツリーの変更のうち、保護領域にヒットするもの。実行後と起動時の基準で同じ式を使う。"""
    return [
        f
        for w, top, changes in read
        for f in post_findings._findings(
            changes, w.rule_set, mine, w.source, scope, w.tree, top, places, root
        )
    ]


def capture_baseline(
    stderr: TextIO,
    state_dir: str,
    session: str,
    watched: list[Watched],
    scope: post_findings.ScopeGuard | None,
    record: audit.Record,
    mine: tuple[str, ...],
    places: tuple[str, str],
    root: str,
) -> None:
    """セッションの始まり（SessionStart の startup）に在る保護領域の変更を、基準として記録する。

    基準を初回の実行後チェックで取ると、そのとき触ったツリーしか見えない。ここで
    全部のツリーを見ておけば、基準はセッションの始まりの状態になる。
    記録は「まだ伝えていない」で書く。起動時に在った変更は、初回の実行後チェックで
    1 度だけモデルへ伝える（`_load_reported` の `announced`）。

    読めなかったツリーが 1 つでもあれば、基準は書かない。読めなかったツリーの
    汚れを、後から「新しい変更」として差し戻すことになるため。その場合は基準が無い
    扱いのまま、従来どおり初回の実行後チェックが基準を取る。
    """
    if not state_dir:
        return
    read = _read_all(stderr, watched, record)
    if len(read) != len(watched):
        return
    found = _found(read, mine, scope, places, root)
    _save_reported(stderr, state_dir, session, {f.key() for f in found}, announced=False)


def _load_reported(stderr: TextIO, state_dir: str, session: str) -> tuple[set[str], bool, bool]:
    """すでに報告した変更と、基準を持つか、起動時の変更をもう伝えたかを返す。

    基準を持つのは、SessionStart の基準がある場合。持たなければ「初めて」として扱う。
    3 つめが偽なのは、基準は在るが起動時に在った変更をまだ伝えていないとき。

    読めなければ「初めて」として扱う。記録を失ったときに、前から在った変更を
    直前の実行のせいにするより、もう一度記録を取り直すほうが害が小さい。
    """
    if not state_dir:
        return set(), False, True
    data, failed = fsio.read_json(_reported_path(state_dir, session))
    if failed is not None:
        if not isinstance(failed, FileNotFoundError):
            stderr.write(f"ccnavi: 実行後チェックの記録を読めない: {failed}\n")
        return set(), False, True
    items = None
    if isinstance(data, dict):
        items = data.get("reported")
    if not isinstance(items, list):
        return set(), False, True
    reported_set = {s for s in items if isinstance(s, str)}
    if data.get("baseline_captured") is not True:
        return set(), False, True
    return reported_set, True, data.get("announced") is not False


def _save_reported(
    stderr: TextIO,
    state_dir: str,
    session: str,
    reported: set[str],
    announced: bool = True,
) -> None:
    """記録を書く。書けなかったことは報告して破棄する。

    `announced` は、起動時に在った変更をモデルへ伝えたかどうか。書く側は
    起動時の基準（偽）と、実行後チェック（真）の 2 通り。

    ここでの失敗はチェックを止めない。記録が無ければ同じ変更をもう一度報告する
    ことになり、うるさいが、見落とすよりはよい。
    """
    if not state_dir:
        return
    failed = fsio.write_json_atomic(
        _reported_path(state_dir, session),
        {
            # 切らない。切った先に在る汚れは次の呼び出しで「新しい変更」になり、戻されてしまう。
            # 件数は在る汚れの数までで、無制限には増えない。
            "reported": sorted(reported),
            "baseline_captured": True,
            "announced": announced,
        },
    )
    if failed:
        stderr.write(f"ccnavi: 実行後チェックの記録を保存できませんでした: {failed}\n")
