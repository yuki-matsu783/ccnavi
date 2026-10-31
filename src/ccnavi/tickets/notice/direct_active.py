"""SessionStart で、作業中の直接作業のワークツリーを知らせる。

compact をまたぐと、会話は先に切ったワークツリーを覚えておらず、同じ作業のために別のワークツリーを
切り直すことがある。そのため、記録（`ccnavi-direct/<名前>`）と作業ワークツリーが残っている直接作業を、
起動・再開・compact・clear のたびに名前とパスで知らせる。読むのはファイルだけで、git は呼ばない。
無ければ何も出さない。切る側の確認は `ccnavi-worktree.sh` の C8 が受け持つ。
"""

from __future__ import annotations

from ...infra import directrec, settings, tree
from ...policy import texts


def notice(conf: settings.Settings, root: str) -> str:
    """作業中の直接作業の知らせ。無い・読めないなら空。例外は出さない。"""
    try:
        found: list[str] = []
        for main in [tree.main_tree(root), *tree.projects(conf.projects)]:
            for name in directrec.active_names(root, main):
                found.append(f"{name}（{settings.shell_path(tree.worktree_path(root, name))}）")
        if not found:
            return ""
        return texts.say(root, "NOTE_WORKTREE_ACTIVE_AT_START", {"worktrees": "、".join(found)})
    except Exception:  # noqa: BLE001  知らせの失敗でセッションの開始を止めない
        return ""
