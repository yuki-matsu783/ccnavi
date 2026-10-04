"""ツール実行後チェック。走ったあとの作業ツリーを見て、保護領域が変わっていないかを問う。

変わったファイルを守る場所とチケットの範囲に当てる部分は `post_findings`、差し戻しの文は
`post_report` に分けてある。どちらも post を読まない。

## 実行前チェックと何が違うか

見ている対象が違う。実行前はツール呼び出しの引数を見て「これは何をするか」を
言い当てる。ここは作業ツリーを見て「何が起きたか」を読む。前者を厳しくしても
後者は要る。引数に現れない書き込み（ビルドの出力先、スクリプトが内部で開く
ファイル、読めなかったシェル構文）は、言い当てる限りいつまでも漏れるから。

止められないことも違う。このイベントにはツール呼び出しを取り消す手段が無く、
返せるのは文だけになる。だから文が「何が変わったか」だけで終わってはいけない。
戻す手順を対象ごとに書く（REQ-PST-03）。手順の無い通知は、受け取った側に
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

当てる先は git が返したパスを解いた絶対パス。実行前チェックがファイルのパスを
解いてから当てるのと同じ理由で、表記を変えただけで外せてはいけない。

## 前から在った変更を原因にしない

作業ツリーは、セッションが始まる前から汚れていることがある。他のセッションの
書きかけ、ユーザが直している最中のもの。それを「直前の実行が壊した」として
差し戻すと、エージェントは他人の作業を戻しにいく。だから初回に見えたものは
その場で記録を取り、以降は新しく現れたものだけを原因付きで報告する。
記録した側も 1 度は伝えるが、文面を分けて、戻すなと明示する。
"""

from __future__ import annotations

import functools
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TextIO

from ..infra import fsio, gitcmd, gitstate, hookio, tree
from ..policy import rules, selfguard
from ..records import audit
from ..tickets import (
    phase_forms,
    ticket_places,
)
from . import post_findings, post_report

# 作業ツリーを変えようがないツール。ここを飛ばすぶん git を起こす回数が減る。
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

# 記録に残す件数の上限。セッションが長引いても記録が膨らまないように。
SEEN_LIMIT = 500


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
    synced: Callable[..., bool] | None = None,
    root: str = "",
) -> tuple[str, bool]:
    """実行後の 1 回ぶんを処理し、（モデルに返す文, 作業ツリーを戻そうとしたか）を返す。

    返す文が無ければ空文字。2 つ目は、戻しを 1 件でも試みたら真（戻せなかった分を含む）。
    呼び手はこれで、戻しで動いたかもしれないファイル（チケットの置き場など）を読み直すか決める。

    enforcing は、このモードが判定を実際に適用する側かどうか。適用しない側
    （dry-run）では復元も行わない。呼び出しにも作業ツリーにも手を出さないことが
    そのモードの約束なので、自分の判断でファイルを動かしては意味がない。

    mine は ccnavi 自身が書く場所。記録と state がそれで、置き場は設定で動くので、
    ルールが守る場所の中を指すこともある。自分の書き込みを自分の違反として
    報告しはじめると、実行後チェックは 1 回目から嘘しか言わなくなる。

    watched は見るツリー。ワークスペースルートと、この呼び出しが触ったツリー
    （設計 11.7）。ツリーごとに git を起こし、そのツリーのルールで見る。

    places はチケットの置き場（提案と承認済みチケット）。そこに現れた変更のうち、
    ccnavi の副命令が書いたと内容から読めるものを外す（`_script_writes`）。
    """
    if payload.tool_name in READ_ONLY_TOOLS:
        record.decision, record.reason = audit.SKIP, REASON_TOOL_CANNOT_WRITE
        return "", False

    read = _read_all(stderr, watched, record)
    if not read:
        return "", False

    # ターンの基準を持たないツリーを初めて見たら、その場で記録する。ターンの途中で
    # 切られたワークツリーがこれにあたる（`at_prompt` のときには無いので基準が無く、
    # 基準が無いツリーのコミットはターンの終わりに数えられない）。ワークツリーを
    # 切ってから手を付けるのがこのリポジトリの手順なので、切ったターンがまるごと
    # 数えられない期間に入っていた。触った時点から先は数えられるようにする。
    #
    # 触られないまま終わったツリーには、ここでも基準が付かない。そちらは
    # ターンの終わりが「数えていない」と言う（`_uncounted_line`）。
    _note_new_trees(stderr, state_dir, payload.session_id, read)

    seen, first_time = _load_seen(stderr, state_dir, payload.session_id)

    found = [
        f
        for w, top, changes in read
        for f in post_findings._findings(
            changes, w.rule_set, mine, w.source, scope, w.tree, top, places, synced, root
        )
    ]
    if not found:
        # 違反が無くても、初回なら記録を作る。ここを飛ばすと、綺麗な作業ツリーで
        # 始まったセッションはいつまでも「初回」のままになり、その後に現れた
        # 汚れが全部「前から在ったもの」になってしまい、誰も差し戻されなくなる。
        if first_time:
            _save_seen(stderr, state_dir, payload.session_id, set())
        record.decision, record.enforced = audit.ALLOW, True
        return "", False

    fresh = [f for f in found if f.key() not in seen]
    known = [f for f in found if f.key() in seen]
    # 初めて見たセッションでは、今そこに在るものは直前の実行の結果ではない。
    # 記録を取るだけにして、原因を付けずに 1 度だけ伝える。
    carried, fresh = (fresh, []) if first_time else ([], fresh)

    restored: dict[str, str] = {}
    # 戻しを試みたか。戻せなかった 1 件も途中までファイルを動かしたかもしれないので含める。
    tried = False
    # 戻すのは `deny` と宣言された場所だけ（`_restorable`）。報告する対象より狭い。
    # restore には CCNAVI_MODE を掛けたあとの値が来る（cli.effective_setting）ので、
    # ここで enforcing を見る必要はない。掛ける場所を 1 か所にまとめてあるのは、
    # 2 つの設定が別々にモードを解釈して食い違うのを防ぐため。
    # 戻すのはそのツリーの git で。鍵はツリー付きにして、別のツリーの同じ相対パスと
    # 取り違えないようにする。
    if restore == selfguard.ENABLE:
        for w, top, _ in read:
            here = [f for f in fresh if f.tree_name == w.tree.name and _restorable(f)]
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
    would = {f.key() for f in fresh if _restorable(f)} if restore == selfguard.DRY_RUN else set()

    _save_seen(
        stderr,
        state_dir,
        payload.session_id,
        seen
        # 戻せたものは記録に入れない。同じ場所がもう一度汚れたら、それは
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
        # この呼び出しは何も汚していない。記録した変更の報告は状態の通知であって、
        # 直前の実行についての判定ではない。
        record.decision, record.enforced = audit.ALLOW, True

    gated = restore in (selfguard.ENABLE, selfguard.DRY_RUN)
    blocks = [
        post_report._violation(
            f,
            payload,
            restored.get(f.key()),
            f.key() in would,
            not_restored=gated and not _restorable(f),
        )
        for f in fresh
    ]
    blocks += [post_report._preexisting(f) for f in carried[:REPORT_LIMIT]]
    if not blocks:
        return "", tried

    shown, dropped = blocks[:REPORT_LIMIT], len(blocks) - REPORT_LIMIT
    if dropped > 0:
        shown.append(
            f"[ccnavi] {dropped} more changed paths in protected areas are not listed here. "
            "Run 'git status' to see the rest before you undo anything."
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
    synced: Callable[..., bool] | None = None,
) -> tuple[list[post_findings.Finding], list[str]]:
    """このターンでコミットに入った、保護領域の変更。

    見るのは、ターンの始まりに記録した HEAD からの差分（`gitstate.committed`）。
    記録を持たないツリー（ターンの途中で現れた、HEAD を読めなかった）は飛ばす。
    数えていない期間を、数えたことにしない。

    チケットの置き場は外す。承認はユーザが提案を `.ccnavi/approved/` へ動かして
    コミットする進め方で（`ccnavi-push-approved.sh`）、その置き場は `deny` でもある。
    外さないと、ユーザが承認するたびに、その操作が違反としてターンの報告に並ぶ。
    置き場のパスは `places` で受け取る。チケット制御を切ったワークスペースでも
    承認のコミットは在りうるので、`scope` の有無で外れたり外れなかったりさせない。

    2 つめに返すのは、数えなかったツリーの名前。**何も出さずに飛ばすことはしない。** 基準が
    無いのは、ターンの途中で切られたツリー（プロンプトのときに無かった）か、
    そのとき HEAD を読めなかったツリーで、どちらも「変わっていない」ではない。
    ワークツリーを切ってから手を付けるのがこのリポジトリの手順なので、切った
    ターンのコミットはここに当たる。そのことを言わないと、見ていない期間が
    「何も起きなかった」と同じ見た目になる。
    """
    out: list[post_findings.Finding] = []
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
        # 着手がコピーした分かどうかは、コミットされた中身で答える。ディスクで答えると、
        # 好きな中身でコミットしてからディスクだけ共通層の中身へ戻す形が、呼び出しごとの
        # チェック・バックアップと復元・ここの 3 つから同時に外れる。
        judged = (
            functools.partial(_committed_synced, synced, top, base, changes) if synced else None
        )
        for finding in post_findings._findings(
            changes, w.rule_set, mine, w.source, scope, w.tree, synced=judged
        ):
            rel = tree.relative(w.tree, finding.change.full)
            if ticket_places.is_ticket_place(rel, tickets, approved):
                continue
            out.append(finding)
    return out, uncounted


def _committed_synced(
    synced: Callable[..., bool],
    top: str,
    base: str,
    changes: list[gitstate.Change],
    full: str,
) -> bool:
    """コミットされた中身で `synced` に答えさせる。読めなければ外さない。

    変更後は HEAD、変更前はターンの始まりの版の中身。どちらもバイト列のまま読む。文字列で
    読むと、UTF-8 でないスクリプトや単独の CR が読み替えられ、コピーした分でも食い違う。
    """
    path = next((c.path for c in changes if c.full == full), "")
    if not path:
        return False
    now, readable = gitcmd.blob(top, "HEAD", path)
    if not readable or now is None:
        return False
    prior, readable = gitcmd.blob(top, base, path)
    if not readable:
        return False
    return synced(os.path.join(top, path.replace("/", os.sep)), now, prior)


def at_stop(
    stderr: TextIO,
    state_dir: str,
    mine: tuple[str, ...],
    watched: list[Watched],
    scope: post_findings.ScopeGuard | None,
    payload: hookio.Input,
    record: audit.Record,
    places: tuple[str, str] = ("", ""),
    synced: Callable[..., bool] | None = None,
    root: str = "",
) -> str:
    """ターンの終わりに、このターンで変わった保護領域をユーザへ報告する。

    呼び出しごとの報告とは宛先も目的も違う。あちらはモデルが読んで次の動きを
    変えるためのもので、こちらはユーザが「このターンで何が変わったか」を 1 度で
    見るためのものになる。

    セッションの記録（`seen`）は見ない。あれは「モデルへ 1 度伝えた」を
    覚えているもので、ユーザはまだ 1 度も見ていないことがある。代わりに見るのは
    ターンの始まりに取った基準で、そこに無いものだけが、このターンで起きたこと。

    戻しもしない。ここで報告するのは、実行後チェックが戻さなかった、あるいは
    戻す設定になっていなかった変更で、どうするかはユーザが決める。
    """
    baseline, known_baseline, heads = _load_turn(stderr, state_dir, payload.session_id)
    if not known_baseline:
        # ターンの始まりを見ていない。ここで今そこに在るものを全部並べると、
        # ユーザの書きかけも他のセッションが置いたものも、このターンの成果として
        # 報告することになる。見えていない期間を、見えたことにしない。
        record.decision, record.reason = audit.SKIP, REASON_NO_TURN_BASELINE
        return ""

    read = _read_all(stderr, watched, record)
    if not read:
        return ""

    found = [
        f
        for w, top, changes in read
        for f in post_findings._findings(
            changes, w.rule_set, mine, w.source, scope, w.tree, top, places, synced, root
        )
        if f.key() not in baseline
    ]
    # このターンでコミットに入ったぶんも足す。`git status` はコミットを見せないので、
    # ここを足さないと、保護領域を汚してからコミットした回が「何も起きなかった」と
    # 同じ見た目になる（呼び出しごとの実行後チェックも同じ見落としがあるが、そちらは戻しに関わるので
    # 断面を変えない。ユーザが 1 度で見るのはこの報告）。
    committed, uncounted = _committed_findings(stderr, read, heads, mine, scope, places, synced)
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

    lines = [
        f"[ccnavi] 守ると宣言した場所が、この{phase_forms.TURN_DEFINED}で"
        f" {len(found)} 件変わりました。"
    ]
    for finding in found[:REPORT_LIMIT]:
        rule = finding.group[0]
        where = f"{finding.tree_name}: " if finding.tree_name else ""
        undo = gitstate.undo(finding.change)
        how = (
            f"戻すなら {undo}"
            if undo
            else (
                f"{finding.change.path} はすでにコミット済みなので、"
                "取り込む前に中身を確かめ、不要な変更なら取り消してください"
            )
        )
        kind = gitstate.KIND_LABELS.get(finding.change.kind, finding.change.kind)
        lines.append(
            f"  {where}{finding.change.path}（{kind}）"
            f" 対象のルール: {rule.id or '(id 無し)'} / {how}"
        )
    if len(found) > REPORT_LIMIT:
        lines.append(f"  ほか {len(found) - REPORT_LIMIT} 件。全部は git status に出ます。")
    if uncounted:
        lines.append(_uncounted_line(uncounted, define=False))
        record.detail = f"uncounted {len(uncounted)}"
    return "\n".join(lines)


def _uncounted_line(uncounted: list[str], *, define: bool = True) -> str:
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
        "コミットした場合は、保護領域のファイルを変更していないか `git log` で確認してください。"
    )


@dataclass
class Watched:
    """実行後に見るツリー 1 つと、そこに当てるルール（設計 11.7）。

    ワークスペースのツリーにはワークスペースのルール、プロジェクトとそのワークツリーには
    そのプロジェクトのルール。実行前チェックと同じ引き方でなければ、実行前に通った
    書き込みが実行後に報告される。
    """

    tree: tree.Tree
    rule_set: rules.RuleSet
    source: str


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
        stderr.write(f"ccnavi: 作業ツリーを読めないので実行後チェックは動かない: {note}\n")
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

    戻せなかったものは返す値から落とす。復元は報告の代わりではないので、
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
    synced: Callable[..., bool] | None = None,
    root: str = "",
) -> None:
    """ターンの始まり。いま保護領域に在る変更を記録して、このターンの基準にする。

    これが無いと、ターンの終わりの報告が「今そこにある変更」しか言えない。
    ユーザ自身の書きかけも、他のセッションが置いたものも、前のターンで
    片付けなかったものも、全部このターンの成果として並ぶ。何度も同じものを
    見せられたユーザは、次から読まなくなる。

    基準を取るのはターンが始まるときで、そこが「エージェントがまだ何もして
    いない時点」になる。以降に現れたものだけが、このターンで起きたこと。
    """
    read = _read_all(stderr, watched, record)
    if not read:
        # 基準を取れなかった。前のターンの記録が残っていると、そこに入っている
        # HEAD を「このターンの始まり」として読むことになり、前のターンの
        # コミットをこのターンの成果として並べる。基準の側は「言い落とす」ほうに
        # なるのに、HEAD の側だけ「言い過ぎる」ほうになるので、消しておく。
        fsio.remove(_turn_path(state_dir, payload.session_id))
        return

    found = [
        f
        for w, top, changes in read
        for f in post_findings._findings(
            changes, w.rule_set, mine, w.source, scope, w.tree, top, places, synced, root
        )
    ]
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


def _load_turn(stderr: TextIO, state_dir: str, session: str) -> tuple[set[str], bool, dict]:
    """このターンの基準を返す。2 つめの値は、基準を持っているかどうか。3 つめは各ツリーの HEAD。

    持っていないなら、ターンの始まりを見ていない。登録されていないか、
    そのイベントで読めなかったか。そこで「全部このターンの成果」として
    報告すると、前から在ったものまで並ぶので、そのときは何も言わない。
    見えていない期間を、見えたことにしない。`heads` を持たない記録（壊れたものや
    HEAD を記録する前の版）も、記録が無いときと同じく基準なしとして扱う。
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
        {"baseline": sorted(baseline)[:SEEN_LIMIT], "heads": dict(sorted(heads.items()))},
    )
    if failed:
        stderr.write(f"ccnavi: {phase_forms.TURN_DEFINED}の基準を書けない: {failed}\n")


def _load_seen(stderr: TextIO, state_dir: str, session: str) -> tuple[set[str], bool]:
    """すでに報告した変更と、このセッションで初めて見るかどうかを返す。

    読めなければ「初めて」として扱う。記録を失ったときに、前から在った変更を
    直前の実行のせいにするより、もう一度記録を取り直すほうが害が小さい。
    """
    if not state_dir:
        return set(), True
    data, failed = fsio.read_json(_seen_path(state_dir, session))
    if failed is not None:
        if not isinstance(failed, FileNotFoundError):
            stderr.write(f"ccnavi: 実行後チェックの記録を読めない: {failed}\n")
        return set(), True
    seen = data.get("seen") if isinstance(data, dict) else None
    if not isinstance(seen, list):
        return set(), True
    return {s for s in seen if isinstance(s, str)}, False


def _save_seen(stderr: TextIO, state_dir: str, session: str, seen: set[str]) -> None:
    """記録を書く。書けなかったことは報告して捨てる。

    ここでの失敗はチェックを止めない。記録が無ければ同じ変更をもう一度報告する
    ことになり、うるさいが、見落とすよりはよい。
    """
    if not state_dir:
        return
    failed = fsio.write_json_atomic(
        _seen_path(state_dir, session), {"seen": sorted(seen)[:SEEN_LIMIT]}
    )
    if failed:
        stderr.write(f"ccnavi: 実行後チェックの記録を書けない: {failed}\n")
