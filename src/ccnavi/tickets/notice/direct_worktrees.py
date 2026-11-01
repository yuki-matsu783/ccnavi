"""SessionStart で、直接作業のワークツリーの一覧を事実として言う。

直接作業は、チケットに結び付かない作業で、`ccnavi-worktree.sh` が直接作業の記録
（`ccnavi-direct/<名前>`）を置いたワークツリーを指す。チケット作業のワークツリー（親・子）は、
親の一覧（`overview`）が言うので、ここには入れない（`osi.direct_check` が、Stop の促しと同じ
判定で外す）。

1 本ごとに言うのは、未コミットの変更の有無、記録の起点 S からのコミットの数
（Stop の促しと同じ数え方）、`integrate` の記録の有無の 3 つだけ。
記録の有無は Stop の促しと同じ判定（`osi.has_record`）で、名前を使い直した作業は「なし」と言う。
「どこまで完了したか」の印は作らない。レビュー済みかどうかは分からないので、続きの一手は言わず、
別のセッションの作業かもしれないことを添える。

並びは新しい順（直接作業の記録の更新時刻）。載せるのは `LIMIT` 本までで、超えた分は
本数だけ言う。
読むのは手元のファイルと git の ref だけで、ネットワークには出ない。期限
（`time.monotonic` の絶対時刻）を過ぎてから git を起こさない。
"""

from __future__ import annotations

import os

from ...infra import directrec, settings, tree
from ...policy import texts
from ..lifecycle import stop as ops_stop
from ..lifecycle import stop_integrate as osi

# 一覧に載せる本数の上限。
LIMIT = 10
UNKNOWN = "不明"


def session_text(root: str, conf: settings.Settings, deadline: float) -> str:
    """直接作業のワークツリーの一覧。無ければ空。例外は出さない。"""
    try:
        return _text(root, conf, deadline)
    except Exception:  # noqa: BLE001  知らせの失敗でセッションの開始を止めない
        return ""


def _text(root: str, conf: settings.Settings, deadline: float) -> str:
    """処理フロー:
    1. ワークツリーのうち、直接作業の記録があり、チケットに結び付かないものを集める。
    2. 記録の更新時刻の新しい順に並べ、`LIMIT` 本まで 1 行ずつ言う。
    3. 超えた分は本数だけ言い、最後に別のセッションの作業の可能性を添える。
    """
    found: list[tuple[float, tree.Tree, dict[str, str], str]] = []
    for here in tree.worktrees(root, conf.projects):
        owner = tree.project_root(conf.projects, here.project) or root
        rec = osi.direct_record(owner, here.name)
        if rec is None:
            continue
        if osi.direct_check(root, conf, here, deadline)[0]:
            continue
        stamp = os.path.join(rec["_common"], directrec.DIRECT_DIR, here.name)
        try:
            found.append((os.path.getmtime(stamp), here, rec, owner))
        except OSError:
            continue
    if not found:
        return ""
    found.sort(key=lambda item: item[0], reverse=True)
    lines = [texts.say(root, "NOTICE_DIRECT_WORKTREES_HEAD")]
    lines += [_line(here, rec, owner, deadline) for _, here, rec, owner in found[:LIMIT]]
    rest = len(found) - LIMIT
    if rest > 0:
        values = {"rest": str(rest), "total": str(len(found)), "shown": str(LIMIT)}
        lines.append("  " + texts.say(root, "NOTICE_DIRECT_WORKTREES_MORE", values))
    lines.append(texts.say(root, "NOTICE_DIRECT_WORKTREES_TAIL"))
    return "\n".join(lines)


def _line(here: tree.Tree, rec: dict[str, str], owner: str, deadline: float) -> str:
    """ワークツリー 1 本の行。取れなかった項目は「不明」と言う。"""
    dirty = osi._dirty(here.root, deadline)
    snap = rec.get("snap_sha", "")
    ahead = osi.ahead_since(here.root, snap, deadline) if snap else None
    dirty_text = UNKNOWN if dirty is None else ("あり" if dirty else "なし")
    ahead_text = UNKNOWN if ahead is None else f"{ahead} コミット"
    base = rec.get("base_branch", "") or UNKNOWN
    record = _integrate_record(rec["_common"], owner, here, dirty, deadline)
    return (
        f"  - {here.name}（取り込み先 {base}）: 未コミット {dirty_text}、"
        f"起点から {ahead_text}、integrate の記録 {record}"
    )


def _integrate_record(
    common: str, owner: str, here: tree.Tree, dirty: bool | None, deadline: float
) -> str:
    """`integrate` の記録の有無。取り込みの途中か、片付け済みか、なしか。

    片付け済みは `osi.has_record` と同じ判定（コピーの `branch_sha` が今の HEAD と同じで、
    未コミットの変更が無い）。判定できなければ「不明」。
    """
    if os.path.lexists(os.path.join(common, osi.RECORD_DIR, here.name)):
        return "あり（取り込みの途中）"
    head = ops_stop.worktree_head_sha(here.root, deadline)
    found = osi.has_record(owner, here.name, head or "", dirty, deadline)
    if found is None:
        return UNKNOWN
    return "あり（片付け済み）" if found else "なし"
