"""SessionStart で、終わっていない `integrate` の記録を知らせる。

見るのは、ワークスペースルートと各プロジェクトの元ツリーである。`ccnavi-git.sh integrate` が統合先の
ツリーの git ディレクトリに置く記録（`ccnavi-integrate/<名前>/marker`）が残っていれば、
別のセッションの取り込みの途中かもしれない。そのため、操作する前にユーザへ確かめるよう知らせる。
この知らせは、統合先のツリーの変更を操作してよい根拠ではない。

記録が無ければ何も出さない。git も呼ばない。`.git` がディレクトリなら、ファイルの有無で確かめる。
`.git` がファイル（ワークツリーやサブモジュール）のときだけ、期限つきの `rev-parse --git-path` で
場所を引く。

## 版 4（取り込み先へ 1 コミットで載せる形）

marker に `version 4` がある記録は、別の見方をする。K（`k`）を作ってあるか、取り込み先に K が
載っているか（`merge-base --is-ancestor`）を見て、続きの一手（`integrate-done` の打ち直し、
`integrate --abort`、abort してから打ち直し）を出す。K の親が記録の `head`、K の木が `c_tree` と
合わないもの、必須の項目が欠けたものは、壊れた記録として「操作せずにユーザに確かめる」と出す。
自動では再開しない。marker が無くても、残った一時ワークツリー（`logs/direct-tmp/`）・専用の ref
（`refs/ccnavi/direct/<名前>/{t,c,k}`）・片付けの途中の done の記録を挙げる。

## 旧形式（版の無い marker）

旧形式の記録は git で次の 4 つに分ける（`write-tree` は使わない。index を書くため）。

- A: `state` が `staged`、HEAD が `base` と同じ、`diff-index --cached --quiet <tree>` が 0
- B: `state` が `staged`、HEAD が `base` と違う、`diff --cached --quiet` が 0
- C: `state` が `conflict`
- D: 上のどれでもない。git が 0 と 1 以外で終わった（期限不足を含む）。記録が壊れている

期限は `time.monotonic` の絶対時刻。過ぎてから git を起こさない。
"""

from __future__ import annotations

import os

from ..infra import directrec, gitcmd, settings, tree
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
    """旧形式の記録 1 件の区分（A〜D）。例外は出さない。"""
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


# 版 4 の知らせ ------------------------------------------------------------------------------

_V4_NEED = ("head", "tip", "c_tree")


def _git_out(top: str, args: list[str], deadline: float) -> str | None:
    """git の標準出力（前後の空白を除く）。成功しなければ None。"""
    done = gitcmd.run_until(top, args, deadline, gitcmd.TIMEOUT_SECONDS)
    return done.out.strip() if done.ok else None


def _v4_line(root: str, top: str, name: str, fields: dict[str, str], deadline: float) -> str:
    """版 4 の marker 1 件の知らせ。例外は出さない。"""
    git_sh = settings.script_command(root, "ccnavi-git.sh")
    t = settings.shell_path(top)
    head = f"{name}（取り込み先のツリー {t}）: "
    cd = f"cd {t} && {git_sh}"
    broken = (
        f"{head}記録が壊れているか、今の状態と合いません（{{why}}）。"
        f"操作せず、`{cd} status` の結果をユーザに見せて確かめてください"
    )
    try:
        missing = [k for k in _V4_NEED if not fields.get(k)]
        if missing:
            return broken.format(why=f"{'・'.join(missing)} がありません")
        rec = osi.direct_record(top, name)
        base = (rec or {}).get("base_branch", "")
        k = fields.get("k", "")
        now = _git_out(top, ["rev-parse", "HEAD"], deadline)
        if now is None:
            return broken.format(why="HEAD を読めません")
        if k and k != "-":
            parent = _git_out(top, ["rev-parse", "--verify", f"{k}^"], deadline)
            tree_of = _git_out(top, ["rev-parse", "--verify", f"{k}^{{tree}}"], deadline)
            if parent != fields["head"] or tree_of != fields["c_tree"]:
                return broken.format(why="K の親か木が記録と違います。K を採用しません")
            landed = bool(base) and (
                _code(top, ["merge-base", "--is-ancestor", k, f"refs/heads/{base}"], deadline) == 0
            )
            if landed:
                return (
                    f"{head}K（{k[:9]}）は取り込み先に載っています。片付けが途中で止まっています。"
                    f"`{cd} integrate-done {name}` を打ち直すと、残りを片付けます"
                )
            if now == fields["head"]:
                return (
                    f"{head}K（{k[:9]}）を作ってありますが、取り込み先にはまだ載っていません。"
                    "ユーザの了承があるなら "
                    f"`{cd} integrate-done {name}` を打ち直すと続きから進みます。"
                    f"やめるなら `{cd} integrate --abort {name}`"
                )
            return (
                f"{head}K を作ったあとに取り込み先が動きました。自動では取り込みません。"
                f"ユーザに確かめてから `{cd} integrate --abort {name}` で戻し、"
                f"`{cd} integrate {name}` を打ち直してください"
            )
        if now != fields["head"]:
            return (
                f"{head}確認の途中で取り込み先が動きました（記録の HEAD と違います）。"
                f"ユーザに確かめてから `{cd} integrate --abort {name}` で戻し、"
                f"`{cd} integrate {name}` を打ち直してください"
            )
        return (
            f"{head}取り込み結果を一時ワークツリー（{settings.shell_path(fields.get('tmp', ''))}）"
            "に用意して、確認を待っています。差分と変わるファイルの一覧をユーザに見せ、"
            f"了承を得たら `{cd} integrate-done {name} -m '<メッセージ>'`。"
            f"やめるなら `{cd} integrate --abort {name}`"
        )
    except Exception:  # noqa: BLE001  壊れた記録でセッションの開始を止めない
        return broken.format(why="読めません")


def _repo_key(root: str, top: str) -> str:
    """一時ワークツリーの置き場の名前（`logs/direct-tmp/<repo>/`）。ワークスペース自身は `self`。"""
    if os.path.normcase(os.path.realpath(top)) == os.path.normcase(os.path.realpath(root)):
        return "self"
    return os.path.basename(os.path.normpath(top))


def _leftovers(root: str, top: str, reported: set[str], deadline: float) -> list[str]:
    """marker の無い名前で残っている、取り込みの途中の跡（一時ワークツリー・ref・done の記録）。

    作業中の記録（`ccnavi-direct`）・`s`・`pending` の ref・作業ワークツリーは、普通の状態なので
    挙げない。
    """
    common = directrec.common_dir(top)
    if common is None:
        return []
    names: dict[str, list[str]] = {}
    tmp_base = os.path.join(root, "logs", "direct-tmp", _repo_key(root, top))
    try:
        for entry in sorted(os.listdir(tmp_base)):
            names.setdefault(entry, []).append(
                f"一時ワークツリー（{settings.shell_path(os.path.join(tmp_base, entry))}）"
            )
    except OSError:
        pass
    listed = _git_out(top, ["for-each-ref", "--format=%(refname)", "refs/ccnavi/direct"], deadline)
    for ref in (listed or "").splitlines():
        parts = ref.split("/")
        # refs/ccnavi/direct/<名前>/<種類>
        if len(parts) >= 5 and parts[4] in ("t", "c", "k"):
            names.setdefault(parts[3], []).append(f"専用の ref（{ref}）")
        elif len(parts) >= 5 and parts[4] == "pending":
            direct = os.path.join(common, directrec.DIRECT_DIR, parts[3])
            if not os.path.isfile(direct):
                names.setdefault(parts[3], []).append(f"使われなかった書きかけの候補（{ref}）")
    done_dir = os.path.join(common, osi._DONE_DIR)
    try:
        for entry in sorted(os.listdir(done_dir)):
            fields = osi._read_record(os.path.join(done_dir, entry))
            if fields.get("version") != directrec.VERSION:
                continue
            remains = []
            if os.path.isdir(tree.worktree_path(root, entry)):
                remains.append("作業ワークツリー")
            branch = _git_out(
                top, ["show-ref", "--verify", "--quiet", f"refs/heads/{entry}"], deadline
            )
            if branch is not None:
                remains.append("ブランチ")
            if os.path.isfile(os.path.join(common, directrec.DIRECT_DIR, entry)):
                remains.append("直接作業の記録")
            if remains:
                names.setdefault(entry, []).append(
                    f"片付けの途中の done の記録（残り: {'・'.join(remains)}）"
                )
    except OSError:
        pass
    git_sh = settings.script_command(root, "ccnavi-git.sh")
    t = settings.shell_path(top)
    lines = []
    for name in sorted(names):
        if name in reported:
            continue
        what = "、".join(names[name])
        lines.append(
            f"{name}（取り込み先のツリー {t}）: 取り込みの途中の跡が残っています: {what}。"
            f"done の記録があれば `cd {t} && {git_sh} integrate-done {name}` を打ち直すと"
            "片付けます。"
            "記録が無いものは、操作せずにユーザに確かめてください（消してよいかは持ち主が決めます）"
        )
    return lines


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
        reported: set[str] = set()
        marks = _records_dir(top, deadline)
        names: list[str] = []
        if marks:
            try:
                names = sorted(os.listdir(marks))
            except OSError:
                names = []
        for name in names:
            if not os.path.isdir(os.path.join(marks, name)):
                continue
            # marker が無い（保存に失敗した）ものは、読めない記録として区分 D に回る。
            marker = os.path.join(marks, name, "marker")
            reported.add(name)
            fields = osi._read_record(marker)
            if fields.get("version") == directrec.VERSION:
                lines.append(_v4_line(root, top, name, fields, deadline))
                continue
            lines.append(_line(root, top, name, classify(top, name, marker, deadline)))
        if not osi._expired(deadline):
            lines.extend(_leftovers(root, top, reported, deadline))
    if cut:
        lines.append(CUT_LINE)
    if not lines:
        return ""
    return "\n".join([HEAD_LINE, *lines])
