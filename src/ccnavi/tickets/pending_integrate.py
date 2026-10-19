"""SessionStart で、終わっていない `integrate` の記録を知らせる。

見るのは、ワークスペースルートと各プロジェクトの元ツリーである。`ccnavi-git.sh integrate` が統合先の
ツリーの git ディレクトリに置く記録（`ccnavi-integrate/<名前>/marker`）が残っていれば、
別のセッションの取り込みの途中かもしれない。そのため、操作する前にユーザへ確かめるよう知らせる。
この知らせは、統合先のツリーの変更を操作してよい根拠ではない。

記録が無ければ何も出さない。git も呼ばない。`.git` がディレクトリなら、ファイルの有無で確かめる。
`.git` がファイル（ワークツリーやサブモジュール）のときだけ、期限つきの `rev-parse --git-path` で
場所を引く。

記録は git で次の 4 つに分ける（`write-tree` は使わない。index を書くため）。

- A: `state` が `staged`、HEAD が `base` と同じ、`diff-index --cached --quiet <tree>` が 0
- B: `state` が `staged`、HEAD が `base` と違う、`diff --cached --quiet` が 0
- C: `state` が `conflict`
- D: 上のどれでもない。git が 0 と 1 以外で終わった（期限不足を含む）。記録が壊れている

期限は `time.monotonic` の絶対時刻。過ぎてから git を起こさない。
"""

from __future__ import annotations

import os

from ..infra import gitcmd, settings, tree
from . import ops_stop_integrate as osi

HEAD_LINE = (
    "[ccnavi] 終わっていない integrate の記録があります。別のセッションの取り込みの場合もあるため、"
    "操作する前に、担当と今の状態をユーザに確かめてください。"
    "この案内は、統合先のツリーの変更を操作してよい根拠ではありません。"
)
CUT_LINE = "ほかのツリーは時間切れで確かめていません"

A, B, C, D = "A", "B", "C", "D"


def _records_dir(top: str, deadline: float) -> str:
    """そのツリーの `ccnavi-integrate/`。無ければ空。"""
    dot = os.path.join(top, ".git")
    if os.path.isdir(dot):
        path = os.path.join(dot, osi._RECORD_DIR)
        return path if os.path.isdir(path) else ""
    if not os.path.exists(dot):
        return ""
    path = osi._git_path(top, osi._RECORD_DIR, deadline) or ""
    return path if path and os.path.isdir(path) else ""


def _code(top: str, args: list[str], deadline: float) -> int | None:
    """git の終了コード。起こせなかった・期限不足なら None。"""
    done = gitcmd.run_until(top, args, deadline, gitcmd.TIMEOUT_SECONDS)
    return None if done.failure else done.code


def classify(top: str, name: str, marker: str, deadline: float) -> str:
    """記録 1 件の区分（A〜D）。例外は出さない。"""
    try:
        rec = osi._read_record(marker)
        state = rec.get("state", "")
        if state == "conflict":
            return C
        if state != "staged":
            return D
        base, want = rec.get("base", ""), rec.get("tree", "")
        done = gitcmd.run_until(top, ["rev-parse", "HEAD"], deadline, gitcmd.TIMEOUT_SECONDS)
        head = done.out.strip() if done.ok else ""
        if not head or not base:
            return D
        if head == base:
            if not want:
                return D
            return (
                A if _code(top, ["diff-index", "--cached", "--quiet", want], deadline) == 0 else D
            )
        return B if _code(top, ["diff", "--cached", "--quiet"], deadline) == 0 else D
    except Exception:  # noqa: BLE001  壊れた記録でセッションの開始を止めない
        return D


def _line(root: str, top: str, name: str, kind: str) -> str:
    git_sh = settings.script_command(root, "ccnavi-git.sh")
    t = settings.shell_path(top)
    head = f"{name}（統合先のツリー {t}）: "
    cd = f"cd {t} && {git_sh}"
    if kind == A:
        return (
            f"{head}HEAD と staged の内容は、取り込みの記録と一致しています"
            "（未ステージ・未追跡の変更と、元のブランチの状態は見ていません）。"
            f"`{cd} diff --staged` の結果をユーザに見せ、了承を得たら "
            f"`{cd} integrate-done {name} -m '<メッセージ>'`。"
            f"やめるなら `{cd} integrate --abort {name}`"
        )
    if kind == B:
        return (
            f"{head}HEAD が記録から進み、staged はありません。"
            "取り込みをコミット済みで、片付けだけが残っている可能性がありますが、"
            "確かめてはいません（別のコミットや reset でも同じ状態になります）。"
            f"`{cd} log --oneline -5` の結果をユーザに見せ、"
            f"取り込みのコミットがあると了承を得たら、`{cd} integrate-done {name}`"
            "（-m なし。中身が統合先に入りきっていなければ、何も消さずに理由が出ます）。"
            f"違うなら、`{cd} status` を見せて判断を仰いでください"
        )
    if kind == C:
        return (
            f"{head}取り込みが途中で止まっています（衝突か、下書きを外せなかった）。"
            f"ユーザに確かめてから `{cd} integrate --abort {name}` で戻してください。"
            "戻せたら、ワークツリーに未コミットの変更が無いか確かめ、"
            f"`{cd} integrate {name}` を打ち直します。また止まったら、その出力の案内に従います。"
            f"--abort が断られたら、`{cd} status` の結果をユーザに見せて判断を仰いでください"
        )
    return (
        f"{head}記録と今の状態が合わないか、読めません。操作せず、"
        f"`{cd} status` の結果をユーザに見せて判断を仰いでください"
    )


def pending_notice(root: str, conf: settings.Settings, deadline: float) -> str:
    """終わっていない `integrate` の知らせ。無ければ空。例外は出さない。"""
    try:
        return _notice(root, conf, deadline)
    except Exception:  # noqa: BLE001  知らせの失敗でセッションの開始を止めない
        return ""


def _notice(root: str, conf: settings.Settings, deadline: float) -> str:
    tops = [root] + [t.root for t in tree.projects(conf.projects)]
    lines: list[str] = []
    cut = False
    for top in tops:
        if osi._expired(deadline):
            cut = True
            break
        marks = _records_dir(top, deadline)
        if not marks:
            continue
        try:
            names = sorted(os.listdir(marks))
        except OSError:
            continue
        for name in names:
            if not os.path.isdir(os.path.join(marks, name)):
                continue
            # marker が無い（保存に失敗した）ものは、読めない記録として区分 D に回る。
            marker = os.path.join(marks, name, "marker")
            lines.append(_line(root, top, name, classify(top, name, marker, deadline)))
    if cut:
        lines.append(CUT_LINE)
    if not lines:
        return ""
    return "\n".join([HEAD_LINE, *lines])
