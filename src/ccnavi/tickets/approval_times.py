"""承認の時刻（表示だけ）。承認済みチケットがいつ承認されたかを、履歴か git から引く。

ボードと `--diagnose` が見せるためだけに読む。判定は履歴も git も読まない取り決めなので、
hook の判定の経路からは呼ばない。approval から分けた。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

from ..infra import gitcmd, settings
from . import history
from . import ticket as ticket_mod

# どこから引いた時刻か。
APPROVED_FROM_HISTORY = "history"  # 状態の履歴の approved（続きの子は raised）
APPROVED_FROM_RECORD = "record"  # 前の版の承認が書いた `ccnavi_approved.approved_at`（古い形）
APPROVED_FROM_COMMIT = "commit"  # doing/ に足したコミットの時刻
APPROVED_UNCOMMITTED = "uncommitted"  # 履歴もコミットも無い（手で置いて、まだコミットしていない）
# git を打たない場（Chrome の Pyodide）では、履歴の無い承認の時刻は分からない（空）。


@dataclass(frozen=True)
class ApprovedTime:
    """承認済みチケットの承認の時刻。`at` が空で `source` も空なら分からない。"""

    at: str = ""
    source: str = ""

    def label(self) -> str:
        """人に見せる 1 語。"""
        if self.source == APPROVED_UNCOMMITTED:
            return "未コミット（手で置いた）"
        return self.at or "不明"


def approved_times(
    conf: settings.Settings, found: list[ticket_mod.Ticket], use_git: bool = True
) -> dict[str, ApprovedTime]:
    """承認済みチケットごと（パスで引く）の承認の時刻。表示（ボード・`--diagnose`）だけが読む。

    次の順で引く。

    1. 状態の履歴 `events/<識別子>.ndjson` の `approved`（続きの子は `raised`）の
       うち新しいものの `at`
    2. 前の版の承認が書いた記録（古い形の `ccnavi_approved.approved_at`）。前の版は承認のときに
       この欄へ時刻を書いていた。履歴を書く前の版の承認は履歴を持たないので、コミットの時刻より先に読む
    3. 1 と 2 で取れないチケットがあるツリーだけ、ツリーごとに 1 回
       `git log --no-renames --diff-filter=A` を打ち、`doing/<識別子>.md` を足した
       コミットのうち新しいものの時刻。`--no-renames` を付けるのは、GitHub の画面での
       移動が rename のコミットになり、付けないと足したことにならないため
    4. どれも無ければ「未コミット（手で置いた）」（`doing/` にあるものだけ。ほかは分からない）

    判定は履歴も git も読まない取り決めなので、hook の判定の経路からは呼ばない。`use_git` が偽
    （Chrome の Pyodide など、git の無い場）なら 3 を飛ばし、1 と 2 で取れなければ分からないとする。
    """
    out: dict[str, ApprovedTime] = {}
    missing: dict[str, list[ticket_mod.Ticket]] = {}
    for t in found:
        where = settings.approved_dir(conf, t.tree_root) if t.tree_root else ""
        at = history_time(where, t.ticket) if where else ""
        if at:
            out[t.path] = ApprovedTime(at, APPROVED_FROM_HISTORY)
        elif t.approved_at:
            out[t.path] = ApprovedTime(t.approved_at, APPROVED_FROM_RECORD)
        elif t.tree_root:
            missing.setdefault(t.tree_root, []).append(t)
    if not use_git or sys.platform == "emscripten":
        return out
    for tree_root, waiting in missing.items():
        added, ok = _added_times(conf, tree_root)
        for t in waiting:
            if t.ticket in added:
                out[t.path] = ApprovedTime(added[t.ticket], APPROVED_FROM_COMMIT)
            elif ok and t.state == ticket_mod.DOING:
                out[t.path] = ApprovedTime("", APPROVED_UNCOMMITTED)
    return out


def history_time(approved_dir: str, ident: str) -> str:
    """履歴の最後の承認の時刻。最後の承認のあとに取り下げがあれば空（次の手段の git に任せる）。

    取り下げたあとに手で動かして承認し直すと、履歴には前の承認の行しか残らない。それを今の
    承認の時刻と言わないため。
    """
    entries, _ = history.read(approved_dir, ident, limit=0)
    kinds = (history.KIND_APPROVED, history.KIND_RAISED)
    for entry in reversed(entries):
        if entry.get("kind") == history.KIND_WITHDRAWN:
            return ""
        if entry.get("kind") in kinds and isinstance(entry.get("at"), str):
            return entry["at"]
    return ""


def _added_times(conf: settings.Settings, tree_root: str) -> tuple[dict[str, str], bool]:
    """ツリーの `doing/` に足したコミットの時刻（識別子ごとに新しいもの）と、git を読めたか。"""
    doing = os.path.join(settings.approved_dir(conf, tree_root), ticket_mod.DOING)
    rel = os.path.relpath(doing, tree_root).replace(os.sep, "/")
    if rel.startswith(".."):
        return {}, False
    done = gitcmd.run(
        tree_root,
        [
            "log",
            "--no-renames",
            "--diff-filter=A",
            "--relative",
            "--format=%x00%cI",
            "--name-only",
            "--",
            rel,
        ],
        raw_paths=True,
    )
    if not done.ok:
        return {}, False
    found: dict[str, str] = {}
    when = ""
    for line in done.out.splitlines():
        if line.startswith("\0"):
            when = line[1:].strip()
            continue
        name = line.strip()
        if not name.startswith(rel + "/") or not name.endswith(".md"):
            continue
        ident = name[len(rel) + 1 : -len(".md")]
        if "/" not in ident and ident not in found:
            found[ident] = when
    return found, True
