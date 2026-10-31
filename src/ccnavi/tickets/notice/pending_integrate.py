"""SessionStart で、終わっていない `integrate` の記録を知らせる。

見るのは、ワークスペースルートと各プロジェクトの元ツリーである。`ccnavi-git.sh integrate` がユーザの
チェックアウトのgitディレクトリに置く記録（`ccnavi-integrate/<名前>/marker`）が残っていれば、
別のセッションの取り込みの途中かもしれない。そのため、操作する前にユーザへ確かめるよう知らせる。
この知らせは、ユーザのチェックアウトの変更を操作してよい根拠ではない。

記録が無ければ何も出さない。git も呼ばない。`.git` がディレクトリなら、ファイルの有無で確かめる。
`.git` がファイル（ワークツリーやサブモジュール）のときだけ、期限つきの `rev-parse --git-path` で
場所を引く。

## 版 4（取り込み先へ 1 コミットで載せる形）

markerに `version 4` がある記録は、別の見方をする。K（`k`）を作ってあるか、取り込み先にKが
載っているか（`merge-base --is-ancestor`）を見て、続きの一手（`integrate-done` の打ち直し、
`integrate --abort`、abortしてから打ち直し）を出す。Kの親が記録の `head`、Kの木が `c_tree` と
合わないもの、必須の項目が欠けたものは、壊れた記録として「操作せずにユーザに確かめる」と出す。
自動では再開しない。markerが無くても、残った一時ワークツリー（`logs/direct-tmp/`）・専用のref
（`refs/ccnavi/direct/<名前>/{t,c,k}`）・片付けの途中のdoneの記録を挙げる。
版 4 でないmarkerは、版を知らない壊れた記録として、同じく
「操作せずにユーザに確かめる」と出す。

期限は `time.monotonic` の絶対時刻。過ぎてから git を起こさない。
"""

from __future__ import annotations

import os

from ...infra import directrec, gitcmd, pathcase, settings, tree
from ..lifecycle import stop_integrate as osi

HEAD_LINE = (
    "[ccnavi] 終わっていない integrate の記録があります。別のセッションの取り込みの場合もあるため、"
    "操作する前に、担当と今の状態をユーザに確かめてください。"
    "この案内は、統合先のツリーの変更を操作してよい根拠ではありません。"
)
CUT_LINE = "ほかのツリーは時間切れで確かめていません"
# 記録（途中の跡を含む）がこの件数以上なら、1 件ずつの続きの一手は出さず、名前の一覧にまとめる。
SHORT_FROM = 5


def _records_dir(top: str, deadline: float) -> str:
    """そのツリーの `ccnavi-integrate/`。無ければ空。"""
    dot = os.path.join(top, ".git")
    if os.path.isdir(dot):
        path = os.path.join(dot, osi.RECORD_DIR)
        return path if os.path.isdir(path) else ""
    if not os.path.exists(dot):
        return ""
    path = osi._git_path(top, osi.RECORD_DIR, deadline) or ""
    return path if path and os.path.isdir(path) else ""


def _code(top: str, args: list[str], deadline: float) -> int | None:
    """git の終了コード。起こせなかった・期限不足なら None。"""
    done = gitcmd.run_until(top, args, deadline, gitcmd.TIMEOUT_SECONDS)
    return None if done.failure else done.code


def _unknown_line(root: str, top: str, name: str, version: str) -> str:
    """版 4 でないmarker 1 件の知らせ。壊れた記録として、操作せずユーザに確かめさせる。"""
    git_sh = settings.script_command(root, "ccnavi-git.sh")
    t = settings.shell_path(top)
    cd = f"cd {t} && {git_sh}"
    return (
        f"{name}（取り込み先のツリー {t}）: 記録の版（{version or '無し'}）を知りません。操作せず、"
        f"`{cd} status` の結果をユーザに見せて確かめてください。"
        f"不要なら、ユーザが git reset --merge で統合先の staged を戻し、"
        f"ccnavi-integrate/{name} を手で消します"
    )


# 版 4 の知らせ ------------------------------------------------------------------------------

_V4_NEED = ("head", "tip", "c_tree")


def _git_out(top: str, args: list[str], deadline: float) -> str | None:
    """gitの標準出力（前後の空白を除く）。成功しなければNone。"""
    done = gitcmd.run_until(top, args, deadline, gitcmd.TIMEOUT_SECONDS)
    return done.out.strip() if done.ok else None


def _v4_line(root: str, top: str, name: str, fields: dict[str, str], deadline: float) -> str:
    """版 4 の marker 1 件の知らせ。例外は出さない。

    処理フロー:
    1. 記録に要る欄（`_V4_NEED`）が揃っていなければ、壊れた扱いの文を返す。
    2. `HEAD` を読む。読めなければ壊れた扱い。
    3. K（作った取り込みの先頭）があれば、その親と木が記録と同じかを見る。違えば採用しない。
       K が取り込み先に載っていれば片付けの途中、HEAD が記録と同じなら了承待ち、
       違えば取り込み先が動いた旨を返す。
    4. K が無ければ、HEAD が記録と同じなら一時ワークツリーで確認を待つ文、違えば動いた旨を返す。
    5. 途中で例外が出たら、壊れた扱いの文を返す。
    """
    git_sh = settings.script_command(root, "ccnavi-git.sh")
    t = settings.shell_path(top)
    head = f"{name}（取り込み先のツリー {t}）: "
    cd = f"cd {t} && {git_sh}"

    def broken(why: str) -> str:
        return (
            f"{head}記録が壊れているか、今の状態と合いません（{why}）。"
            f"操作せず、`{cd} status` の結果をユーザに見せて確かめてください"
        )

    try:
        missing = [k for k in _V4_NEED if not fields.get(k)]
        if missing:
            return broken(f"{'・'.join(missing)} がありません")
        rec = osi.direct_record(top, name)
        base = (rec or {}).get("base_branch", "")
        k = fields.get("k", "")
        now = _git_out(top, ["rev-parse", "HEAD"], deadline)
        if now is None:
            return broken("HEAD を読めません")
        if k and k != "-":
            parent = _git_out(top, ["rev-parse", "--verify", f"{k}^"], deadline)
            tree_of = _git_out(top, ["rev-parse", "--verify", f"{k}^{{tree}}"], deadline)
            if parent != fields["head"] or tree_of != fields["c_tree"]:
                return broken("K の親か木が記録と違います。K を採用しません")
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
            "確定はユーザの了承（対話では確認の許可、非対話では返事を待って次の発言）を得ていれば、"
            f"`{cd} integrate-done {name} -m '<メッセージ>'` を打ちます。"
            f"やめるなら `{cd} integrate --abort {name}`"
        )
    except Exception:  # noqa: BLE001  壊れた記録でセッションの開始を止めない
        return broken("読めません")


def repo_key(root: str, top: str) -> str:
    """一時ワークツリーの置き場の名前（`logs/direct-tmp/<repo>/`）。ワークスペース自身は `self`。"""
    if pathcase.fold(os.path.realpath(top)) == pathcase.fold(os.path.realpath(root)):
        return "self"
    return os.path.basename(os.path.normpath(top))


def _leftovers(root: str, top: str, reported: set[str], deadline: float) -> list[tuple[str, str]]:
    """markerの無い名前で残っている、取り込みの途中の跡（一時ワークツリー・ref・doneの記録）。
    （名前, 知らせの 1 行）の組を名前の順に返す。

    作業中の記録（`ccnavi-direct`）・`s`・`pending` のref・作業ワークツリーは、普通の状態なので
    挙げない。
    """
    common = directrec.common_dir(top)
    if common is None:
        return []
    # 名前ごとに、残っている跡を集める（一時ワークツリー・専用の ref・書きかけの候補）
    names: dict[str, list[str]] = {}
    tmp_base = os.path.join(root, "logs", "direct-tmp", repo_key(root, top))
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
    # done の記録の残り（作業ワークツリー・ブランチ・直接作業の記録）
    done_dir = os.path.join(common, osi.DONE_DIR)
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
    # 報告していない名前だけを、名前の順に 1 行ずつ言う
    git_sh = settings.script_command(root, "ccnavi-git.sh")
    t = settings.shell_path(top)
    lines: list[tuple[str, str]] = []
    for name in sorted(names):
        if name in reported:
            continue
        what = "、".join(names[name])
        lines.append(
            (
                name,
                f"{name}（取り込み先のツリー {t}）: 取り込みの途中の跡が残っています: {what}。"
                f"done の記録があれば `cd {t} && {git_sh} integrate-done {name}` を打ち直すと"
                "片付けます。"
                "記録が無いものは、操作せずにユーザに確かめてください（消してよいかは持ち主が決めます）",
            )
        )
    return lines


def pending_notice(root: str, conf: settings.Settings, deadline: float) -> str:
    """終わっていない `integrate` の知らせ。無ければ空。例外は出さない。"""
    try:
        return _notice(root, conf, deadline)
    except Exception:  # noqa: BLE001  知らせの失敗でセッションの開始を止めない
        return ""


def _notice(root: str, conf: settings.Settings, deadline: float) -> str:
    """取り込みの途中の跡を、見出しの後ろに並べる。無ければ空。例外は呼び元が受ける。

    処理フロー:
    1. 本体と各プロジェクトのツリーを順に見る。期限が過ぎたら打ち切り、`CUT_LINE` を足す。
    2. 記録の置き場の名前ごとに、版 4 の記録なら `_v4_line`、それ以外は `_unknown_line` で言う。
    3. 記録の無い跡は `_leftovers` で言う。
    4. 行が無ければ空を返す。記録が `SHORT_FROM` 件未満なら、`HEAD_LINE` の後ろに1件1行で返す。
       `SHORT_FROM` 件以上なら、1 件ずつの行を出さず、名前を全件 1 行に並べ、確かめ方を添える。
    """
    tops = [root] + [t.root for t in tree.projects(conf.projects)]
    lines: list[tuple[str, str]] = []
    found: list[str] = []  # 知らせる行があったツリー
    cut = False
    for top in tops:
        before = len(lines)
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
            # marker が無いものや版 4 でないものは、版を知らない記録として知らせる。
            marker = os.path.join(marks, name, "marker")
            reported.add(name)
            fields = osi._read_record(marker)
            if fields.get("version") == directrec.VERSION:
                lines.append((name, _v4_line(root, top, name, fields, deadline)))
                continue
            lines.append((name, _unknown_line(root, top, name, fields.get("version", ""))))
        if not osi._expired(deadline):
            lines.extend(_leftovers(root, top, reported, deadline))
        if len(lines) > before:
            found.append(top)
    if not lines and not cut:
        return ""
    tail = [CUT_LINE] if cut else []
    if len(lines) >= SHORT_FROM:
        return "\n".join([HEAD_LINE, _summary_line(root, [n for n, _ in lines], found), *tail])
    return "\n".join([HEAD_LINE, *(line for _, line in lines), *tail])


def _summary_line(root: str, names: list[str], trees: list[str]) -> str:
    """記録が多いときの 1 行。名前は全件並べる（確認が要る件を見落とさないため）。

    同じ名前が別のツリーにもあり得るので、記録のあるツリーも並べる。
    """
    git_sh = settings.script_command(root, "ccnavi-git.sh")
    cds = "、".join(f"`cd {settings.shell_path(t)} && {git_sh} status`" for t in trees)
    return f"{'、'.join(names)}。詳しくは {cds}（操作せずユーザに見せて確かめる）"
