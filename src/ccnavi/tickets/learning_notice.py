"""SessionStart で、学びの蓄積の「提案の候補」が新しく現れたことをユーザにだけ知らせる。

リポジトリごと（ワークスペース自身と `projects/<名前>/`）に、`ccnavi_integration` の規則で統合先を
決め、その手元の参照が指すコミットの `docs/wiki/` を git のオブジェクトから読む（チェックアウトは
要らない）。参照を決められないか読めないリポジトリは、その回の通知を見送り、通知済みの記録も
変えない。同じ回の `ccnavi-fetch.sh` による取り込みが終わっているかは保証しない
（間に合わなければ次の回に知らせる）。

リポジトリごとに、「候補」の集合を C、そのうち観測元の数が閾値以上のものを R とする。次の 2 つが
どちらも成り立てば、件数・`target` ごとの内訳・依頼の仕方を知らせる。

- 条件: C が `CCNAVI_LEARNING_NOTICE_COUNT` 件以上、または R が 1 件以上
- 変化: 通知済みの記録に無い ID が C にある、または通知済みの記録で閾値未満だった ID が R にある

出したら、通知済みの記録を今の C（ID ごとに R に入っていたか）で置き換える。出さなかったときも、
C に無くなった ID は記録から外す。通知済みの記録は `logs/state/learning-notice/<リポジトリ>.json`。
読めない・書けないときは空とみなす（通知が改めて出ることがある）。

ネットワークには出ない。
"""

from __future__ import annotations

import hashlib
import os
import re
import time

from ..infra import fsio, pathcase, settings, tree
from ..records import wiki, wiki_load
from .lifecycle import stop_integrate as ops_stop_integrate

NOTICE_DIR = "learning-notice"
SELF_NAME = "_self"
_SAFE = re.compile(r"[^A-Za-z0-9._-]")


def state_path(conf: settings.Settings, name: str) -> str:
    # 名前は大文字小文字を区別せずに比べる（pathcase.fold）。ファイル名に使えない字や非 ASCII の字は
    # `_` になるので、衝突しないよう fold した名前の短いハッシュを足す。
    # ワークスペース自身とプロジェクトは別のキーにする
    # （`_self` という名前のプロジェクトと紛れない）。
    key = pathcase.fold(name) if name else SELF_NAME
    digest = hashlib.sha1((f"project:{key}" if name else "self").encode("utf-8")).hexdigest()[:8]
    safe = _SAFE.sub("_", key)[:40]
    return os.path.join(conf.state, NOTICE_DIR, f"{safe}-{digest}.json")


def read_seen(path: str) -> dict[str, bool]:
    """通知済みの記録（パターン名 → 閾値以上だったか）。読めなければ空。"""
    data, error = fsio.read_json(path)
    if error is not None or not isinstance(data, dict):
        return {}
    seen = data.get("patterns")
    if not isinstance(seen, dict):
        return {}
    return {k: v for k, v in seen.items() if isinstance(k, str) and isinstance(v, bool)}


def decide(
    rows: list[wiki.Row], seen: dict[str, bool], threshold: int, notice_count: int
) -> tuple[bool, dict[str, bool]]:
    """通知を出すかと、書き直す通知済みの記録。"""
    current = {r.name: r.reached(threshold) for r in rows}
    reached = [n for n, hit in current.items() if hit]
    due = len(current) >= notice_count or bool(reached)
    changed = any(name not in seen for name in current) or any(
        hit and not seen.get(name, False) for name, hit in current.items()
    )
    if due and changed:
        return True, current
    # 出さないときも、候補でなくなった ID は外す。残す ID の値は、通知したときの値のまま持つ。
    return False, {n: v for n, v in seen.items() if n in current}


def message(label: str, rows: list[wiki.Row], threshold: int) -> str:
    reached = [r for r in rows if r.reached(threshold)]
    parts = []
    for target in wiki.TARGETS:
        n = sum(1 for r in rows if r.target == target)
        if n:
            parts.append(f"{target} {n}")
    where = f"{label} の" if label else ""
    return (
        f"[ccnavi] {where}学びの提案の候補が {len(rows)} 件ある"
        f"（観測元が {threshold} 以上: {len(reached)} 件。内訳: {'、'.join(parts)}）。"
        "提案を出すには、エージェントに「学びの提案を出して」と頼んでください"
        "（手順は .ccnavi/docs/learning-propose.md、一覧は ccnavi --learnings）。"
    )


def notice(conf: settings.Settings, root: str, deadline: float | None = None) -> str:
    """SessionStart でユーザに見せる文。通知が無ければ空。失敗してもセッションの開始は進める。"""
    if not conf.learning_enabled:
        return ""
    try:
        threshold = wiki.sources_threshold(conf.learning_sources)
        count = wiki.notice_count(conf.learning_notice_count)
        out = []
        for main in [tree.main_tree(root), *tree.projects(conf.projects)]:
            if deadline is not None and time.monotonic() >= deadline:
                break
            text = _one(conf, main, threshold, count, deadline)
            if text:
                out.append(text)
        return "\n".join(out)
    except Exception:  # noqa: BLE001 - 案内を足すだけなので、セッションの開始は止めない
        return ""


def _one(
    conf: settings.Settings, main: tree.Tree, threshold: int, count: int, deadline: float | None
) -> str:
    branch = ops_stop_integrate._integration(main.root, deadline)
    if not branch:
        return ""
    sha = wiki_load.commit_of(main.root, f"refs/remotes/origin/{branch}", deadline)
    if not sha:
        return ""
    found = wiki_load.load_commit(main.root, sha, deadline)
    if found is None:
        return ""
    rows = wiki.candidates(found)
    path = state_path(conf, main.project)
    seen = read_seen(path)
    show, record = decide(rows, seen, threshold, count)
    if record != seen or show:
        fsio.write_json_atomic(path, {"patterns": record})
    return message(main.project, rows, threshold) if show else ""
