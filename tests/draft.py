"""保護対象（`rules.yml`・`.ccnavi/scripts/*`・CCNAVI.md）の「できあがりの姿」を引く道具。

保護対象はエージェントが直接直せず、`wip/handoff/` に下書きを置いて、ユーザが `ccnavi user handoff`
で取り込む。取り込む前は、できあがりの姿は下書きにあり、取り込んだ後は本物にある。
テストは、どちらの状態でも同じ中身を読めるよう、下書きがあれば下書きを、無ければ本物を読む。
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile

from . import ROOT


def final(rel: str) -> str:
    """リポジトリからの相対パスのファイルの、できあがりの姿のパス。"""
    draft = os.path.join(ROOT, "wip", "handoff", *rel.split("/"))
    if os.path.isfile(draft):
        return draft
    return os.path.join(ROOT, *rel.split("/"))


_OVERLAY: str | None = None


def scripts_dir() -> str:
    """`.ccnavi/scripts/` のできあがりの姿の置き場。

    下書きが無ければ本物の置き場そのもの。あれば、本物をコピーした一時の置き場に下書きを重ねたもの
    （プロセスの終わりに消す）。
    """
    global _OVERLAY
    real = os.path.join(ROOT, ".ccnavi", "scripts")
    drafts = os.path.join(ROOT, "wip", "handoff", ".ccnavi", "scripts")
    if not os.path.isdir(drafts) or not os.listdir(drafts):
        return real
    if _OVERLAY is None:
        _OVERLAY = tempfile.mkdtemp(prefix="ccnavi-scripts-")
        atexit.register(shutil.rmtree, _OVERLAY, ignore_errors=True)
        for name in os.listdir(real):
            shutil.copy(os.path.join(real, name), _OVERLAY)
        for name in os.listdir(drafts):
            shutil.copy(os.path.join(drafts, name), _OVERLAY)
    return _OVERLAY
