"""承認の時刻（表示だけ）。承認済みチケットがいつ承認されたかを、git から引く。

ボードと `--diagnose` が見せるためだけに読む。判定は git も読まない取り決めなので、
hook の判定の経路からは呼ばない。approval から分けた。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

from ..infra import gitcmd, settings
from .model import ticket_model

# どこから引いた時刻か。
APPROVED_FROM_COMMIT = "commit"  # doing/ に足したコミットの時刻
APPROVED_UNCOMMITTED = "uncommitted"  # コミットが無い（手で置いて、まだコミットしていない）
# git を打たない場（Chrome の Pyodide）では、承認の時刻は分からない（空）。


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
    conf: settings.Settings, found: list[ticket_model.Ticket], use_git: bool = True
) -> dict[str, ApprovedTime]:
    """承認済みチケットごと（パスで引く）の承認の時刻。表示（ボード・`--diagnose`）だけが読む。

    ツリーごとに 1 回 `git log --no-renames --diff-filter=A` を打ち、`doing/<識別子>.md` を足した
    コミットのうち新しいものの時刻を引く。`--no-renames` を付けるのは、GitHub の画面での
    移動が rename のコミットになり、付けないと足したことにならないため。
    コミットが無ければ「未コミット（手で置いた）」（`doing/` にあるものだけ。ほかは分からない）。

    判定は git を読まない取り決めなので、hook の判定の経路からは呼ばない。`use_git` が偽
    （Chrome の Pyodide など、git の無い場）なら分からないとする。
    """
    out: dict[str, ApprovedTime] = {}
    missing: dict[str, list[ticket_model.Ticket]] = {}
    for t in found:
        if t.tree_root:
            missing.setdefault(t.tree_root, []).append(t)
    if not use_git or sys.platform == "emscripten":
        return out
    for tree_root, waiting in missing.items():
        added, ok = _added_times(conf, tree_root)
        for t in waiting:
            if t.ticket in added:
                out[t.path] = ApprovedTime(added[t.ticket], APPROVED_FROM_COMMIT)
            elif ok and t.state == ticket_model.DOING:
                out[t.path] = ApprovedTime("", APPROVED_UNCOMMITTED)
    return out


def _added_times(conf: settings.Settings, tree_root: str) -> tuple[dict[str, str], bool]:
    """ツリーの `doing/` に足したコミットの時刻（識別子ごとに新しいもの）と、git を読めたか。"""
    doing = os.path.join(settings.approved_dir(conf, tree_root), ticket_model.DOING)
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
