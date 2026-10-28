"""サブエージェントの始まりと終わり。

始まりには、cwd のワークツリーに関わる開いている子の一覧を渡す。終わりには、
範囲外の変更が残っていれば 1 回だけ差し戻す。差し戻しを無視して終わったことは
親に言う。どちらも止められないイベントなので、判定はしない。
"""

from __future__ import annotations

import contextlib
import os
import time
from typing import TextIO

from ..catalog import projskills
from ..infra import fsio, hookio, modes, pathcase, settings, tree
from ..infra.modes import EXIT_BLOCK, EXIT_OK
from ..policy import ctxfile, rules
from ..records import audit
from ..tickets import phase_scope
from ..tickets.approval import checks as approval_checks
from ..tickets.approval import store as approval
from ..tickets.flow import graph as flow
from ..tickets.model import ticket_model
from . import (
    agentkind,
    childnote,
    post,
    post_findings,
    reasons,
    subagent_outcome,
)

# サブエージェントには Stop の振り返り（`match: Stop` のルールの文）が届かないので、
# 始まりに 1 行だけ渡す。メインはこの節を集めて振り返りに使う（.ccnavi/docs/skill-review.md）。
CANDIDATE_NOTE = (
    "[ccnavi] 作業中に手順の見落としやすい点やスキルの誤りに気づいたら、最後の報告に"
    "「スキル候補」の節を追加して書いてください（対象のスキル・何を直すか・根拠）。"
    "スキルのファイルは自分では書かない。"
)


def at_start(
    stdout: TextIO,
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    payload: hookio.Input,
    record: audit.Record,
) -> int:
    """サブエージェントが始まったとき。cwd のワークツリーに関わる、開いている子の一覧を渡す。

    止められないイベントなので判定はしない。判定はファイルの行き先で決まるので、
    ここで渡す文は案内でしかない。親のプロンプトに書き忘れがあっても、
    サブエージェントが自分のツリーと範囲を知れるようにする。

    渡すのは cwd で決める。親のワークツリーならその親の開いている子、子のワークツリーなら
    その子自身。ワークスペースルートと、チケットの無いワークツリーからの起動には何も渡さない。
    全部の子を渡すと、別のセッションがワークスペースルートで調査を委譲したときにも無関係な
    子の範囲を案内し、調査役がどこで作業すればよいか分からなくなる（SubagentStop と同じ絞り方）。

    処理フロー:
    1. 判定を ALLOW にし、
       同じワークツリーで動く別のサブエージェントの案内を組む（`_note_running`）。
    2. スキルの目録（`projskills.notice`）と振り返りの候補の 1 行を、案内の前に並べる。
    3. チケット制御が無い・ワークツリーが無い・メインのときは、その文だけを渡して抜ける（`_say`）。
    4. cwd のツリーに対応する親（または子）の開いている子を決め、子ごとに行を組む
       （`childnote.child_block`・`childnote.stage_lines`）。
    5. 手順が並び、子が 1 本に決まらないときは、自分の子のフローだけに従い、
       他の子には従わない旨を 1 行足す。
    6. 子の変更の知らせを足して渡す。
    """
    record.decision, record.enforced = audit.ALLOW, True
    t = tree.tree_of(root, payload.cwd or os.getcwd(), conf.projects)
    # 同じワークツリーで動く別の編集するサブエージェントの案内。ワークスペースルートからの起動
    # （チケットの無いツリー）でも渡すので、子チケットの一覧に至る前の return より先に組む。
    shared = _note_running(stderr, conf, root, payload, t)
    # cwd がプロジェクトの中なら、そのプロジェクトのスキルの目録を頭に置く（本文は要るときに
    # 自分で開く）。
    # 子チケットの一覧が無い起動（チケット制御が無い、チケットの無いツリー）でも渡す。
    skills = projskills.notice(stderr, conf, root, payload, at_start=True)
    # 振り返りの候補は、止められないサブエージェントには報告で返してもらう。
    skills = "\n\n".join(part for part in (skills, shared, CANDIDATE_NOTE) if part)
    if not conf.tickets_enabled:
        return _say(stdout, skills)
    if t is None or t.is_main:
        return _say(stdout, skills)
    # 正とする側（親のツリー）のチケットを読む。着手で書かれる基準点は親のツリーの
    # チケットにだけ入るので、子のツリーに checkout されている版では足りない。
    copies, _ = approval.scan(conf, root)
    index = approval_checks.by_id(copies)
    bound = tree.lookup(index, t.name)
    if bound is None:
        return _say(stdout, skills)
    children = [bound] if bound.is_child else [c for c in copies if c.parent == bound.ticket]
    if not children:
        return _say(stdout, skills)
    preds = childnote.predecessors(conf, root, copies)
    lines = [
        "[ccnavi] 承認済みで開いている子チケット。"
        "書き込みは行き先のワークツリーのチケットで判定される。"
    ]
    # フローの文に使える残り（文字）。子が多くても SubagentStart の文が長くなりすぎないように。
    budget = flow.TOTAL_TEXT_LIMIT
    # 手順を並べるのは、cwd がその子のワークツリーで子が 1 本に決まるときだけ。
    # 親のツリーからの起動（入れ子の孫も）では、どの子の担当かが hook から決められない。
    full = bound.is_child
    listed = False
    changed: list[str] = []
    for t in sorted(children, key=lambda x: x.ticket):
        block = childnote.child_block(conf, root, t, index, preds, budget, full)
        lines.extend(block.lines)
        budget -= sum(len(line) for line in block.brief)
        listed = listed or bool(block.brief)
        if block.notice:
            changed.append(block.notice)
    lines.extend(childnote.stage_lines(conf, root, index, children))
    if listed and not full:
        lines.append(
            "  フロー: 自分の担当の子チケットのフローだけを読んで従ってください。"
            "他の子のフローには従わないでください。担当が分からなければ、読まずにメインに聞いてください"
        )
    lines.extend(childnote.RULE_LINES)
    lines.extend(changed)
    text = "\n".join(lines)
    if skills:
        text = f"{skills}\n\n{text}"
    hookio.write_context(stdout, hookio.SUBAGENT_START, text, system="\n".join(changed))
    return EXIT_OK


def _say(stdout: TextIO, text: str) -> int:
    """子チケットの一覧が無い起動で、渡す文（スキルの目録と候補の 1 行）だけを渡す。"""
    if text:
        hookio.write_context(stdout, hookio.SUBAGENT_START, text)
    return EXIT_OK


def at_stop(
    stdout: TextIO,
    stderr: TextIO,
    mode: str,
    conf: settings.Settings,
    root: str,
    payload: hookio.Input,
    record: audit.Record,
) -> int:
    """サブエージェントが終わろうとしたとき。実行中の記録を外し、範囲外の変更の差し戻しを判定する。

    差し戻した（`EXIT_BLOCK`）ときは、サブエージェントが続けるので記録を残す。それ以外は、
    判定の途中で例外が出ても外す（終わるのは変わらない。残すと促しを期限まで止める）。
    """
    blocked = False
    try:
        rc = _judge_stop(stdout, stderr, mode, conf, root, payload, record)
        blocked = rc == EXIT_BLOCK
        return rc
    finally:
        if not blocked:
            _forget_running(conf, payload)


def _judge_stop(
    stdout: TextIO,
    stderr: TextIO,
    mode: str,
    conf: settings.Settings,
    root: str,
    payload: hookio.Input,
    record: audit.Record,
) -> int:
    """サブエージェントが終わろうとしたとき。範囲外の変更が残っていれば 1 回だけ差し戻す。

    見るのは、cwd が子のワークツリーならその子、親のワークツリーならその親の開いている
    子の全部。親のワークツリーの HEAD との共通の祖先（`gitstate.child_base`）から子の HEAD までの
    コミット済みの差分と、未コミットの両方を見る。
    範囲は実行前チェックと同じく親の範囲と定義の上限で切り詰め、子の範囲の中でも
    上限の外なら、どの上限かをパスの後ろにつける。

    処理フロー:
    1. 判定を ALLOW として始める。チケット制御が無効なら通す。
    2. cwd のツリーから担当の子（または親の開いている子の全部）を決める（`tree.lookup`）。
    3. 子ごとに範囲の外の変更を見る（`phase_scope.scope_findings`）。読めない子は
       `unchecked` に残し、detail と印に書く。
    4. 範囲の外が無ければ、結果を記録して通す（フローの変更の知らせは添える）。
    5. 担当が分からないときは差し戻さず、親に知らせる（`UNATTRIBUTED`）。
    6. ENABLE で初めての差し戻しは、標準エラーに書いて `exit 2`。2 回目以降は通して結果を記録する。
    """
    record.decision, record.enforced = audit.ALLOW, True
    if not conf.tickets_enabled:
        return EXIT_OK
    # 担当の子と、その範囲の外の変更を集める。
    t = tree.tree_of(root, payload.cwd or os.getcwd(), conf.projects)
    copies, _ = approval.scan(conf, root)
    index = approval_checks.by_id(copies)
    targets: list[ticket_model.Ticket] = []
    bound = tree.lookup(index, t.name) if t is not None and not t.is_main else None
    if bound is not None:
        targets = [bound] if bound.is_child else [c for c in copies if c.parent == bound.ticket]
    # 担当の子が決まるのは、cwd が子のワークツリーのとき。親のツリーから起動された体は、
    # どの子の担当か分からないので、全部の子を調べても差し戻さず、親に知らせる。
    attributed = bound is not None and bound.is_child
    findings = []
    unchecked: list[str] = []
    for child in targets:
        outside, unreadable = phase_scope.scope_findings(root, conf, child, index.get(child.parent))
        if unreadable:
            stderr.write(f"ccnavi: {child.ticket} のワークツリーを読めない: {unreadable}\n")
            unchecked.append(child.ticket)
            continue
        for rel, found in outside:
            findings.append((child, rel, found))
    # 相手を見分けるキー。`agent_id` が無い payload では、cwd のワークツリーの名前を使う。
    who = payload.agent_id or (t.name if t is not None else "")
    if unchecked:
        # 範囲を確かめられなかった子がある。通した回と記録で見分けられるよう、範囲外が他に
        # 見つからなければ判定に至らなかった回にし、どの子かを detail に残す。
        # 読めないのは子の作業のせいとは限らず、差し戻しても子には直せないことがある。
        # この出力は親に届かないので、親の `Agent` の PostToolUse で 1 行言うための印を置く。
        record.detail = "unchecked: " + ", ".join(unchecked)
        if not findings:
            record.decision, record.reason = audit.SKIP, post.REASON_WORKTREE_UNREADABLE
            record.enforced = False
        path = _unchecked_path(conf.state, payload.session_id, who)
        _remember(stderr, path, payload.session_id, unchecked)
    # 着手のあとにフローが書き換わっていれば知らせる。行き先を追えないシェルの書き込みは
    # 止められないので、止めずに知らせる。
    changed = [n for n in (flow.changed_notice(conf, root, c) for c in targets) if n]
    if not findings:
        _record_outcome(
            stderr,
            conf,
            payload,
            who,
            bound.ticket if attributed and bound is not None else "",
            subagent_outcome.UNCHECKED
            if unchecked and attributed
            else (
                subagent_outcome.FIXED
                if _bounced(conf.state, payload.session_id, who)
                else subagent_outcome.OK
            ),
        )
        if changed:
            note = "\n".join(changed)
            hookio.write_context(stdout, hookio.SUBAGENT_STOP, note, system=note)
        return EXIT_OK

    record.decision, record.paths = audit.DENY, [f"{c.ticket}:{rel}" for c, rel, _ in findings]
    record.rules, record.code = [reasons.TICKET_RULE], post_findings.CODE_TICKET_SCOPE
    lines = [
        f"[ccnavi] {post_findings.CODE_TICKET_SCOPE}: 子チケットの範囲の外に変更が残っています（"
        f"{len(findings)} 件）。範囲の中へ戻すか、必要なら親に伝えて次のチケットにしてください。"
    ]
    for child, rel, found in findings[: post.REPORT_LIMIT]:
        area = ", ".join(child.paths(rules.ALLOW) + child.paths(rules.ASK)) or "(空)"
        lines.append(f"  {child.ticket}: {rel}{_limit_note(child, found)}  範囲は {area}")
    text = "\n".join(lines + changed)

    already = _bounced(conf.state, payload.session_id, who)
    found_paths = [f"{c.ticket}:{rel}" for c, rel, _ in findings]
    child_name = bound.ticket if attributed and bound is not None else ""
    if not attributed:
        # 担当が分からないので差し戻さない（別の子の変更を、関係ない体に直させない）。
        record.enforced = False
        _record_outcome(
            stderr, conf, payload, who, "", subagent_outcome.UNATTRIBUTED, paths=found_paths
        )
        if changed:
            note = "\n".join(changed)
            hookio.write_context(stdout, hookio.SUBAGENT_STOP, note, system=note)
        return EXIT_OK
    if mode == modes.ENABLE and not already:
        _remember_bounce(stderr, conf.state, payload.session_id, who)
        record.enforced = True
        # 差し戻して続けさせる回なので、結果はまだ確定しない（次の終了で直ったかが決まる）。
        _record_outcome(
            stderr,
            conf,
            payload,
            who,
            child_name,
            subagent_outcome.OUTSIDE,
            bounces=1,
            paths=found_paths,
            final=False,
        )
        stderr.write(text + "\n")
        return EXIT_BLOCK
    record.enforced = False
    _record_outcome(
        stderr,
        conf,
        payload,
        who,
        child_name,
        subagent_outcome.OUTSIDE,
        bounces=1 if already else 0,
        paths=found_paths,
    )
    hookio.write_context(stdout, hookio.SUBAGENT_STOP, text, system="\n".join(changed))
    return EXIT_OK


# 実行中のサブエージェントの印。メインの Stop が、委任して待つ回を「レビュー依頼までの道」の
# 促しで止めないために使う（`running`）。
#
# - SubagentStart が `subagent-<セッション>-<agent_id>.running` を置き、SubagentStop が外す。
#   差し戻して続けさせた回は外さない。1 体 1 ファイルなので、並行して始まる・終わる体が
#   同じファイルを書き換えて取り合うことは無い
# - 中身は 1 行目に `session <セッション>`、2 行目に開始時刻（エポック秒）。
#   セッションの識別子は名前に入れるとき字を置き換えるので、別のセッションの名前と前置きが
#   重なりうる。どのセッションの印かは 1 行目の完全一致で決める（`_read_mark` と同じ）。
#   編集するサブエージェントだけが 3 行目以降に `tree <種類> <名前>` と `type <agent_type>` を
#   持つ（同じワークツリーの案内に使う。`_shared_note`）。無い印は案内の相手に数えない
# - `agent_id` が payload に無い始まり・終わりは、キーが決まらないので印を置かず、外しもしない。
#   その体は「実行中」と数えられず、委任して待つ回でも促しが出うる
# - 入れ子の体（孫）も、同じセッションで自分の `agent_id` の印を持つ。1 体でも残っていれば実行中
# - SubagentStop が来ないまま残った印（中断・クラッシュ・ccnavi の内部エラー）は、開始から
#   `RUNNING_MAX_SECONDS` を過ぎたら数えない。さらにセッションの起動（`startup`）と再開
#   （`resume`）で破棄する（`forget_running`）。どちらも新しいプロセスで、前の体は生きていない。
#   compact・clear では破棄しない。同じプロセスで、実行中の体が残りうるため
# - 数える対象はサブエージェントだけ。チームメイトとバックグラウンドのシェルは数えない
RUNNING_SUFFIX = ".running"

# 印を「実行中」と数える長さの上限（秒）。4 時間。hook の timeout（10 秒）とは関係なく、
# バックグラウンドの体が現実に走る長さ（数分から 1 時間ほど）に十分な余裕を足した値。
# 長すぎる分の害は、
# 取りこぼした印が促しを止める時間が伸びるだけ（促しが 1 回出ないだけで、他は壊れない）。
# 短すぎると、長く走る体を待つ回で誤って止める。迷ったら止めない側に倒す。
RUNNING_MAX_SECONDS = 4 * 3600


def _running_path(state_dir: str, session: str, who: str) -> str:
    """実行中の印の置き場。キーの決め方は差し戻しの記録（_bounce_path）と同じ。"""
    return _bounce_path(state_dir, session, who)[: -len(".bounced")] + RUNNING_SUFFIX


def _note_running(
    stderr: TextIO,
    conf: settings.Settings,
    root: str,
    payload: hookio.Input,
    here: tree.Tree | None,
) -> str:
    """SubagentStart で、このサブエージェントが実行中だと印を置く。置けなくても始まりは止めない。

    チケット制御の有無によらず置く（直接作業の促しも、実行中のサブエージェントが居る間は止める）。返すのは、同じワークツリーで動く別の
    編集するサブエージェントへの案内（無ければ空）。

    印の 3 行目以降は、編集するサブエージェントだけが書く（`tree <種類> <名前>` と
    `type <agent_type>`）。無い印は、案内の相手に数えない。
    同時に起動した相手が書きかけの印を読まないよう、途中の状態が見えない書き方で置く。置いてから
    相手を探すので、同時に起動した 2 つのうち、後から探すほうは先に置かれた印を見つける。
    """
    if not conf.state or not payload.agent_id:
        return ""
    lines = [str(int(time.time()))]
    editing = (
        here is not None
        and agentkind.kind_of([here.root, root], payload.agent_type, payload.tools)
        == agentkind.EDIT
    )
    if editing and here is not None:
        lines += [
            f"{_TREE}{here.kind} {_flat(here.name)}",
            f"{_TYPE}{_flat(payload.agent_type)}",
        ]
    path = _running_path(conf.state, payload.session_id, payload.agent_id)
    failed = fsio.write_text_atomic(path, _mark_text(payload.session_id, lines))
    if failed:
        stderr.write(f"ccnavi: 実行中のサブエージェントの印を保存できませんでした: {failed}\n")
    if not editing or here is None:
        return ""
    return _shared_note(stderr, conf, payload, here)


_TREE = "tree "
_TYPE = "type "


def _shared_note(
    stderr: TextIO, conf: settings.Settings, payload: hookio.Input, here: tree.Tree
) -> str:
    """同じワークツリーで実行中の、別の編集するサブエージェントへの案内。まだ案内していない相手がいなければ空。

    相手は同じセッションの印のうち、自分のキーではなく、ワークツリーが同じ（名前は大文字小文字を
    区別せず比べる）で、開始から `RUNNING_MAX_SECONDS` を過ぎていないもの。同じ相手への案内を
    1 回に抑えるため、案内した相手のキーを覚える（再開で `SubagentStart` がまた走っても、
    同じ相手には出さない）。覚えられないときは案内する側に倒す。読めない印は数えない。
    """
    # 同じセッションの印から、同じワークツリーで動いている別の相手を集める。
    marks = _session_marks(conf.state, payload.session_id, RUNNING_SUFFIX)
    if not marks:
        return ""
    me = fsio.safe_name(payload.agent_id) or "unknown"
    want = f"{here.kind} {pathcase.fold(here.name)}"
    now = time.time()
    peers: dict[str, str] = {}
    for key, path in marks:
        if key == me:
            continue
        text = fsio.read_text(path, errors="replace")
        lines = text.splitlines() if text is not None else []
        if not lines or lines[0] != _MARK_SESSION + _one_line(payload.session_id):
            continue
        started = _started_at(lines)
        if started is None or now - started > RUNNING_MAX_SECONDS:
            continue
        tree_line = next((x[len(_TREE) :] for x in lines[2:] if x.startswith(_TREE)), None)
        if tree_line is None:
            continue
        kind, _, name = tree_line.partition(" ")
        if f"{kind} {pathcase.fold(name)}" != want:
            continue
        peers[key] = next((x[len(_TYPE) :] for x in lines[2:] if x.startswith(_TYPE)), "")
    if not peers:
        return ""
    told = ctxfile.told_peers(stderr, conf.state, payload.session_id, payload.agent_id)
    fresh = {k: v for k, v in peers.items() if told is None or k not in told}
    if not fresh:
        return ""
    ctxfile.remember_peers(
        stderr, conf.state, payload.session_id, payload.agent_id, (told or set()) | set(fresh)
    )
    counts: dict[str, int] = {}
    for kind_name in fresh.values():
        label = kind_name or "種類不明"
        counts[label] = counts.get(label, 0) + 1
    who = "、".join(f"{label} {n} 件" for label, n in sorted(counts.items()))
    where = here.name or "ワークスペースルート"
    return (
        f"[ccnavi] 起動時の作業ディレクトリと同じワークツリー（{where}）で、別の編集する"
        f"サブエージェントが動いています（{who}）。並行して編集するときは、別のワークツリーを切る"
        "決まりです（.ccnavi/docs/decisions.md）。同じワークツリーで続けるなら、触るファイルの"
        "持ち主を分け、他のサブエージェントの未コミットの変更には触れないでください。"
        "別のワークツリーで作業している場合や、親から委任された子の場合は、対応は不要です。"
    )


def _forget_running(conf: settings.Settings, payload: hookio.Input) -> None:
    """SubagentStop で、この体の実行中の印を外す。無くても、外せなくても何も出さない。"""
    if conf.state and payload.agent_id:
        fsio.remove(_running_path(conf.state, payload.session_id, payload.agent_id))


def forget_running(state_dir: str, session: str) -> None:
    """このセッションの実行中の印を全部破棄する。起動（`startup`）と再開（`resume`）の後始末。"""
    if not state_dir:
        return
    for _, path in _session_marks(state_dir, session, RUNNING_SUFFIX) or []:
        # 名前の前置きが重なる別のセッションの印は消さない（`running` と同じく 1 行目で確かめる）。
        # 1 行目を読めない印（壊れている・空）は、期限と prune に任せず、ここで破棄する。
        text = fsio.read_text(path, errors="replace")
        lines = text.splitlines() if text is not None else []
        if lines and lines[0] != _MARK_SESSION + _one_line(session):
            continue
        fsio.remove(path)


def running(state_dir: str, session: str, now: float | None = None) -> bool:
    """このセッションに、実行中のサブエージェントが居るか。読めないときも真（促さない側）。

    印が壊れていて開始時刻を読めないときは、ファイルの更新時刻で期限を見る。
    置き場が無いなら偽、あっても一覧できないときは真。印を読めないまま促すと、委任して待つ回のたびに止めてしまう。
    """
    if not state_dir:
        return False
    marks = _session_marks(state_dir, session, RUNNING_SUFFIX)
    if marks is None:
        return True
    when = time.time() if now is None else now
    for _, path in marks:
        text = fsio.read_text(path, errors="replace")
        lines = text.splitlines() if text is not None else []
        if text is not None and lines and lines[0] != _MARK_SESSION + _one_line(session):
            continue
        started = _started_at(lines)
        if started is None:
            try:
                started = os.path.getmtime(path)
            except OSError:
                return True
        if when - started <= RUNNING_MAX_SECONDS:
            return True
    return False


def _started_at(lines: list[str]) -> float | None:
    """印の開始時刻。読めなければ None。"""
    try:
        return float(lines[1])
    except (IndexError, ValueError):
        return None


def _flat(text: str) -> str:
    """`splitlines` が行の区切りとみなす文字（改行、垂直タブ、改ページなど）を空白に替える。

    印は `splitlines` で読むので、区切りが残ると 1 行の値が途中で切れ、相手を見つけ損ねる。
    """
    return " ".join(text.splitlines())


def _one_line(session: str) -> str:
    return session.replace("\r", " ").replace("\n", " ")


def _session_marks(state_dir: str, session: str, suffix: str) -> list[tuple[str, str]] | None:
    """このセッションの名前の前置きにヒットする印。相手のキーとパスの組。一覧できなければ None。

    名前は前置きで候補を絞るだけで、セッションは読むときに 1 行目で確かめる。
    """
    head = os.path.basename(_bounce_path(state_dir, session, "x"))[: -len("x.bounced")]
    try:
        names = sorted(os.listdir(state_dir))
    except FileNotFoundError:
        # 置き場がまだ無い。印を置いた者が居ない。
        return []
    except OSError:
        return None
    return [
        (name[len(head) : -len(suffix)], os.path.join(state_dir, name))
        for name in names
        if name.startswith(head) and name.endswith(suffix) and len(name) > len(head) + len(suffix)
    ]


def _limit_note(child: ticket_model.Ticket, found: phase_scope.ScopeVerdict) -> str:
    """子の範囲の中なのに外とされたパスにつける、止めた上限の名指し。子の範囲の外なら空。

    つけないと、承認で見た範囲の中を書いたのに差し戻された理由が読めず、範囲の中へ
    戻せと言われても戻し先が分からない。
    """
    if found.limit == phase_scope.LIMIT_BLOCKED:
        return f"（チケットを信頼できない: {child.blocked}）"
    if found.limit == phase_scope.LIMIT_TYPE and found.type is not None:
        return f"（定義 {found.type.title} の上限の外）"
    if found.limit == phase_scope.LIMIT_PARENT:
        return f"（親 {child.parent} の範囲の外）"
    return ""


def _record_outcome(
    stderr: TextIO,
    conf: settings.Settings,
    payload: hookio.Input,
    who: str,
    child: str,
    outcome: str,
    bounces: int = 0,
    paths: list[str] | None = None,
    final: bool = True,
) -> None:
    """終了結果を残す。残せなくても止めない（言うだけ）。"""
    if not conf.state or not who:
        return
    try:
        failed = subagent_outcome.write(
            conf.state, payload.session_id, who, outcome, child, bounces, paths, final
        )
    except (OSError, ValueError) as exc:
        failed = str(exc)
    if failed:
        stderr.write(f"ccnavi: サブエージェントの終了結果を保存できませんでした: {failed}\n")


def outcome_notices(state_dir: str, payload: hookio.Input) -> tuple[str, list]:
    """親に言う終了結果の知らせ（担当の子が分からなかった体）と、書き終えたあとに印を付ける結果。

    読むのは親（`agent_id` の無いメインのセッション）だけ。入れ子の体の中で読むと、親に届かない。
    """
    if not state_dir or payload.agent_id:
        return "", []
    return subagent_outcome.notices(state_dir, payload.session_id)


def confirm_outcomes(state_dir: str, done: list) -> None:
    """知らせを書き終えた結果に「通知済み」の印を付ける。付けられなくても、次の機会に出し直すだけ。"""
    if done:
        subagent_outcome.confirm(state_dir, done)


def stop_lines(state_dir: str, session: str) -> list[str]:
    """Stop で促す結果の 1 行ずつ（`subagent_outcome.stop_lines`）。"""
    return subagent_outcome.stop_lines(state_dir, session)


def ignored_bounce(state_dir: str, payload: hookio.Input) -> str:
    """終わったサブエージェントが差し戻しを受けていたなら、その旨。マーカーは消す。

    言うのは、終了結果が `outside`（直らないまま終わった）のときだけ。差し戻しの後に直った
    （`fixed`）なら言わない。結果の記録が無い体（記録を入れる前の終了）は今までどおり言う。
    """
    if payload.tool_name not in modes.AGENT_TOOLS:
        return ""
    agent_id = str(payload.tool_response.get("agentId") or "")
    if not agent_id or not _bounced(state_dir, payload.session_id, agent_id):
        return ""
    fsio.remove(_bounce_path(state_dir, payload.session_id, agent_id))
    found = subagent_outcome.of(state_dir, payload.session_id, agent_id)
    if found is not None and found.outcome in (
        subagent_outcome.OK,
        subagent_outcome.FIXED,
        subagent_outcome.UNCHECKED,
    ):
        # 直った、または範囲を確かめられなかった（後者は unchecked_scope が言う。断定しない）
        return ""
    return (
        f"[ccnavi] {post_findings.CODE_TICKET_SCOPE}: サブエージェント {agent_id} は範囲外の変更を"
        "差し戻されたまま終わっています。合流する前に、その子のワークツリーの範囲外の"
        "変更を確かめてください。"
    )


def unchecked_scope(state_dir: str, payload: hookio.Input) -> str:
    """終わったサブエージェントの子の範囲を、SubagentStop で確かめられなかったなら、その旨。

    印は読んだら消す。読むのは次の 2 か所。

    - 親の `Agent` の PostToolUse。そのサブエージェント（`agentId`）の印を読む
    - 親（payload に `agent_id` が無いメインのセッション）のどのツールの PostToolUse と
      UserPromptSubmit。このセッションに残った印をぜんぶ読む。バックグラウンドで動かした
      サブエージェントは、`Agent` の PostToolUse が SubagentStop より先に来るので、
      `Agent` の PostToolUse だけで読むと印が残ったまま知らせが落ちる。サブエージェントの
      中の呼び出しでは読まない。その子が親でない印を消すと、親に届かなくなるため
    """
    if not state_dir:
        return ""
    paths: list[tuple[str, str]] = []
    if payload.tool_name in modes.AGENT_TOOLS:
        agent_id = str(payload.tool_response.get("agentId") or "")
        if agent_id:
            paths.append((agent_id, _unchecked_path(state_dir, payload.session_id, agent_id)))
    if not payload.agent_id:
        paths.extend(_session_unchecked(state_dir, payload.session_id))
    said: list[str] = []
    seen: set[str] = set()
    for who, path in paths:
        if path in seen:
            continue
        seen.add(path)
        found = _read_mark(path, payload.session_id)
        if found is None:
            continue
        fsio.remove(path)
        # 同じことを Stop でもう一度促さない
        subagent_outcome.mark_nudged(state_dir, payload.session_id, who)
        tickets = ", ".join(found)
        if not tickets:
            # SubagentStop の処理が ccnavi の内部エラーで終わった回。どの子を見るはずだったかも
            # 分からない。
            said.append(
                f"[ccnavi] {post.REASON_WORKTREE_UNREADABLE}: サブエージェント {who} の終わりに"
                "ccnavi が内部エラーで止まり、子チケットの範囲外の変更を確かめていません。"
                "合流する前に、その子のワークツリーを確かめてください。"
            )
            continue
        said.append(
            f"[ccnavi] {post.REASON_WORKTREE_UNREADABLE}: サブエージェント {who} の終わりに"
            f"子チケット {tickets} のワークツリーを読めず、範囲外の変更を確かめていません。"
            "合流する前に、そのワークツリーを確かめてください。"
        )
    return "\n\n".join(said)


def _session_unchecked(state_dir: str, session: str) -> list[tuple[str, str]]:
    """このセッションに残った、範囲を確かめられなかった印。相手のキーとパスの組。

    ファイルの名前は前置きで候補を絞るだけ。セッションの識別子は名前に入れるときに字を
    置き換えるうえ、`-` で区切るので別のセッションの名前も前置きにヒットしうる。どのセッションの
    印かは、読むときに中身の 1 行目（`_read_mark`）と完全一致で比べる。
    """
    head = os.path.basename(_unchecked_path(state_dir, session, "x"))[: -len("x.unchecked")]
    try:
        names = sorted(os.listdir(state_dir))
    except OSError:
        return []
    return [
        (name[len(head) : -len(".unchecked")], os.path.join(state_dir, name))
        for name in names
        if name.startswith(head) and name.endswith(".unchecked") and len(name) > len(head) + 10
    ]


def unchecked_after_error(
    stderr: TextIO, conf: settings.Settings, root: str, payload: hookio.Input
) -> None:
    """SubagentStop の処理が ccnavi の内部エラーで終わったとき、範囲を確かめられなかった印を置く。

    内部エラーの知らせ（`systemMessage`）はユーザにしか見えず、親には届かない。読めない子が
    あった回と同じ印を置いて、親の PostToolUse か UserPromptSubmit で言わせる。どの子を
    見るはずだったかは分からないので、中身は空にする。ここでの失敗は破棄する（受け皿の中で
    例外を重ねない）。
    """
    if not conf.state:
        return
    with contextlib.suppress(Exception):
        if not conf.tickets_enabled:
            return
        who = payload.agent_id
        if not who:
            t = tree.tree_of(root, payload.cwd or os.getcwd(), conf.projects)
            who = t.name if t is not None else ""
        path = _unchecked_path(conf.state, payload.session_id, who)
        if os.path.exists(path):
            # 例外の前に読めない子を書き終えていれば、そちらのほうが詳しい。
            return
        failed = fsio.write_text(path, _mark_text(payload.session_id, []))
        if failed:
            stderr.write(f"ccnavi: 範囲を確かめられなかった印を保存できませんでした: {failed}\n")
        existing = subagent_outcome.of(conf.state, payload.session_id, who)
        if existing is None or not existing.final:
            # 先に書いた結果（outside・unattributed）を、内部エラーで上書きしない
            _record_outcome(stderr, conf, payload, who, "", subagent_outcome.UNCHECKED)


def _unchecked_path(state_dir: str, session: str, who: str) -> str:
    """範囲を確かめられなかった印の置き場。キーの決め方は差し戻しの記録（_bounce_path）と同じ。"""
    return _bounce_path(state_dir, session, who)[: -len(".bounced")] + ".unchecked"


_MARK_SESSION = "session "


def _mark_text(session: str, tickets: list[str]) -> str:
    """印の中身。1 行目にセッションの識別子をそのまま書き、続けて子の識別子を 1 行に 1 つ。

    子の識別子が無い（中身がセッションの行だけの）印は、SubagentStop が内部エラーで終わった回。
    """
    one_line = session.replace("\r", " ").replace("\n", " ")
    return "".join(f"{line}\n" for line in [_MARK_SESSION + one_line, *tickets])


def _read_mark(path: str, session: str) -> list[str] | None:
    """このセッションの印なら子の識別子のリスト（内部エラーの回は空）。違う・無いなら None。"""
    text = fsio.read_text(path, errors="replace")
    if text is None:
        return None
    lines = text.splitlines()
    one_line = session.replace("\r", " ").replace("\n", " ")
    if not lines or lines[0] != _MARK_SESSION + one_line:
        return None
    return [line for line in lines[1:] if line]


def _remember(stderr: TextIO, path: str, session: str, tickets: list[str]) -> None:
    if not os.path.dirname(path):
        return
    failed = fsio.write_text(path, _mark_text(session, tickets))
    if failed:
        stderr.write(f"ccnavi: 範囲を確かめられなかった印を保存できませんでした: {failed}\n")


def _bounce_path(state_dir: str, session: str, who: str) -> str:
    """差し戻しの記録の置き場。セッションと、その中で相手を見分けるキーで分ける。

    セッションをキーに入れるのは、state の置き場がワークスペースに 1 つしか無いから。
    入れないと、別のセッションが置いた記録を読んで、一度も差し戻していない相手を
    「差し戻し済み」として通す。記録が消えるのは、親の PostToolUse が `agentId` を
    持って通ったときだけなので、残った 1 つは次の日のセッションでも有効なままになる。

    `who` は `agent_id`。持たない payload では、そのワークツリーの名前を使う。
    1 つの名前（`unknown`）に全員をまとめると、最初の 1 体が差し戻されたあと、
    同じ置き場を見る他のサブエージェントが誰も差し戻されなくなる。しかも
    `agent_id` を持たない相手の記録は `ignored_bounce` が消せないので、消えない。
    ツリーの名前なら、少なくとも別の子で作業する相手は巻き込まない。
    """
    where = fsio.safe_name(session) or "unknown"
    safe = fsio.safe_name(who) or "unknown"
    return os.path.join(state_dir, f"subagent-{where}-{safe}.bounced")


def _bounced(state_dir: str, session: str, who: str) -> bool:
    return bool(state_dir) and os.path.exists(_bounce_path(state_dir, session, who))


def _remember_bounce(stderr: TextIO, state_dir: str, session: str, who: str) -> None:
    if not state_dir:
        return
    failed = fsio.write_text(_bounce_path(state_dir, session, who), fsio.stamp())
    if failed:
        stderr.write(f"ccnavi: 差し戻しの回数を保存できませんでした: {failed}\n")
