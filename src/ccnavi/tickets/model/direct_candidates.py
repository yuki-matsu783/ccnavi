"""Stop で `integrate` を促すための、直接作業の「候補」の記録。

直接作業のワークツリー（`.claude/worktrees/<名前>`）に Write・Edit・NotebookEdit で書き込んだとき、
そのツリーを、セッションごとの記録に加える。
Stop は cwd のツリーに加えて、ここで加えられた候補も調べる。
cwd がワークスペースルートのままでも、サブエージェントが書いたツリーでも、促しが届く。

候補は、促す対象を絞るための材料であり、担当の証明ではない。
登録では git を呼ばず、ファイルシステムだけを使う。
候補を確かめるのは Stop（`lifecycle.stop_integrate.direct_at_stop`）である。

置き場は `logs/state/direct-<セッション>.json`。形は
`{"session": "<ID>", "entries": [{"name", "path", "seen", "checked"}]}`。

- `path` は実パス。`seen` は最後に書き込み先として見た時刻（壁時計の秒）、`checked` は Stop で最後に
  調べた時刻（0 は未調査）。
- 上限は 50 件で、`seen` の古いものから破棄する。
- 読むときは、`session` が完全に一致するものだけを使い、名前が `integrable_name` を通らない項目と、
  `path` が `<root>/.claude/worktrees/<名前>` と一致しない項目は破棄する。読めなければ空。
- 保存できなくても何も言わない（登録は補助）。並行して書かれて 1 件落ちることがあるが、補わない。
"""

from __future__ import annotations

import os
import time

from ...infra import fsio, hookio, pathcase, settings, tree

# 候補にする書き込みのツール。
TOOLS = ("Write", "Edit", "NotebookEdit")
# 記録の上限。
MAX_ENTRIES = 50
# 同じ候補が末尾にあり、これ以内に見たばかりなら書き直さない（秒）。
REFRESH_SECONDS = 60


def integrable_name(name: str) -> bool:
    """`integrate` が受ける名前か。`ccnavi-git.sh` の `ig_name` と同じ検査。

    統合先に使う名前（main・master・develop・release・release-*）と、
    `ccnavi_is_ident` を通らない形は、
    `integrate` が断る。
    そのため、促さない。
    """
    if name in ("main", "master", "develop", "release") or name.startswith("release-"):
        return False
    if not name or name[0] in "-." or ".." in name or "/" in name or "\\" in name:
        return False
    if not (name[0].isascii() and name[0].isalnum()):
        return False
    return all(c.isalnum() and c.isascii() or c in "._-" or ord(c) >= 0x80 for c in name)


def path_of(state_dir: str, session: str) -> str:
    where = fsio.safe_name(session) or "unknown"
    return os.path.join(state_dir, f"direct-{where}.json")


def _same(a: str, b: str) -> bool:
    # リンクを解いて比べる。`tree` は解いたパスを返すが、hook のルートは解いていないことがある。
    def norm(path: str) -> str:
        return pathcase.fold(tree.nfc(os.path.realpath(path)))

    return norm(a) == norm(b)


def _read(state_dir: str, session: str) -> list[dict]:
    """記録の項目（形の検査だけ済んだもの）。別のセッションの記録・読めない記録は空。"""
    data = fsio.read_dict(path_of(state_dir, session))
    if not data or data.get("session") != session:
        return []
    raw = data.get("entries")
    out: list[dict] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        name, path = item.get("name"), item.get("path")
        seen, checked = item.get("seen"), item.get("checked", 0)
        if not (isinstance(name, str) and isinstance(path, str)):
            continue
        if isinstance(seen, bool) or not isinstance(seen, (int, float)):
            continue
        if isinstance(checked, bool) or not isinstance(checked, (int, float)):
            continue
        out.append({"name": name, "path": path, "seen": seen, "checked": checked})
    return out


def load(conf: settings.Settings, root: str, session: str) -> list[dict]:
    """このセッションの候補。名前か実パスが合わない項目は破棄する。"""
    if not conf.state or not session:
        return []
    kept = []
    for item in _read(conf.state, session):
        if not integrable_name(item["name"]):
            continue
        if not _same(item["path"], tree.worktree_path(root, item["name"])):
            continue
        kept.append(item)
    return kept


def _save(state_dir: str, session: str, entries: list[dict]) -> str:
    ordered = sorted(entries, key=lambda e: e["seen"])[-MAX_ENTRIES:]
    return fsio.write_json_atomic(
        path_of(state_dir, session), {"session": session, "entries": ordered}
    )


def _target(payload: hookio.Input) -> str:
    raw = payload.field_value("file_path") or payload.field_value("notebook_path")
    if not raw:
        return ""
    if not os.path.isabs(raw):
        raw = os.path.join(payload.cwd or os.getcwd(), raw)
    return os.path.normpath(raw)


def touch(
    conf: settings.Settings, root: str, payload: hookio.Input, now: float | None = None
) -> None:
    """成功した書き込みの先が直接作業のワークツリーなら、候補に加える。git は呼ばない。

    メインにもサブエージェントにも効く（サブエージェントも親のセッションの候補になる）。
    保存できなければ何も言わない。
    """
    if payload.tool_name not in TOOLS or not conf.state or not payload.session_id:
        return
    full = _target(payload)
    if not full:
        return
    here = tree.tree_of(root, full, conf.projects)
    if here is None or here.is_main:
        return
    if not _same(here.root, tree.worktree_path(root, here.name)):
        return
    if not integrable_name(here.name):
        return
    stamp = time.time() if now is None else now
    entries = _read(conf.state, payload.session_id)
    for i, item in enumerate(entries):
        if _same(item["name"], here.name):
            if i == len(entries) - 1 and stamp - item["seen"] <= REFRESH_SECONDS:
                return
            del entries[i]
            break
    entries.append({"name": here.name, "path": here.root, "seen": stamp, "checked": 0})
    _save(conf.state, payload.session_id, entries)


def update(
    conf: settings.Settings,
    session: str,
    checked: dict[str, float],
    dropped: set[str],
) -> None:
    """Stop が調べた候補の `checked` を進め、外す候補を除いて保存する。書けなくても何も言わない。"""
    if not conf.state or not session or not (checked or dropped):
        return
    entries = _read(conf.state, session)
    keep = []
    for item in entries:
        if item["name"] in dropped:
            continue
        if item["name"] in checked:
            item["checked"] = checked[item["name"]]
        keep.append(item)
    _save(conf.state, session, keep)
