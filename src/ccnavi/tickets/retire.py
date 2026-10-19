"""親を閉じたときの手元の片付け（ワークツリーとブランチ）。

`ccnavi worktree retire <親>`（`ready` が Draft を外した後）、`ccnavi sync tidy`（MR が
マージされたと分かった後）、`ccnavi worktree branch <名前>`（残したブランチを確かめて
消し直す）が使う。
消すのは、成果が失われないと確かめられたものだけ。確かめられなければ消さずに、理由と
手で消すコマンドを言う。

## ready の後（片付け 1）

子はすべて閉じている。`ready` は閉じた子のチケットを手元の退避へ移すので、先に子の一覧
（識別子・合流したか取り消したか・合流した先頭の sha）を状態の置き場の `retire/<親>.json` に
書く（`note`）。片付けはそれを読む。

- 子のワークツリーをすべて消す（`worktrees.drop` と同じ条件。未コミットの変更や、git が
  無視しているファイル（`.env` など）があれば消さずに理由を言う）
- 子のブランチ（手元）を消す。条件は子の種類で分ける
  - 合流した子: ブランチの先頭が合流した sha（`merged_sha`）と同じで、その sha が push 済みの
    親の先頭（`refs/remotes/origin/<親のブランチ>`）の祖先であること
  - 取り消した子: ユーザに一覧を見せ、ユーザが `!` を付けて `ccnavi-user-clean-branch.sh <名前>
    --confirmed` を打ったときだけ（エージェントからは hook が止める）。確認なしでは消さない。
    消す前に、記録の子が実際にその親の子か（チケットの `parent:`）を確かめ直す。
    親に無いコミットの数を添える
- 親のワークツリーを消す

## MR がマージされたと分かった後（片付け 2）

`ccnavi-sync.sh` がホストに聞いて、マージ済みの MR の番号と先頭の sha を渡す。

- 親のワークツリーが残っていれば消す（同じ条件）
- 親の手元のブランチを消す。条件は、手元の先頭が push 済みの先頭
  （`refs/remotes/origin/<ブランチ>`）と MR の先頭の両方に含まれること。
  MR の先頭は sh が渡す値なので、それだけでは信じない。
  どちらかが手元に無ければ、含まれるか確かめられないので消さない
- 残っていた子のブランチは、MR の先頭を基準に片付け 1 と同じ条件で消す
- すべて片付いたら、親の片付けの記録（`retire/<親>.json`）を消し、`record yes` を言う

ブランチを消す権限はエージェントに開けない（`git branch -D` は組み込みで止まる）。条件の確認は
ここで行い、消すのもここだけ。ネットワークには出ない。git はローカルの操作（`rev-parse`・
`merge-base`・`worktree list`・`branch -D`）だけ。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import TextIO

from ..infra import fsio, gitcmd, pathcase, settings, tree
from . import approval, archive, ticket_ids, worktrees

STATE_DIR = "retire"
KIND_MERGED = "merged"
KIND_CANCELLED = "cancelled"

# 消した・残した・確認が要る、の分類。
DELETED = "deleted"
KEPT = "kept"
ASK = "ask"
NONE = "none"


@dataclass
class Child:
    """片付ける子 1 つ。`kind` は合流したか取り消したか。"""

    ticket: str
    kind: str
    merged_sha: str = ""
    project: str = ""


@dataclass
class Outcome:
    """子のブランチ 1 本の結果。`text` は言うこと（`kept` と `ask` は理由と次の手を含む）。"""

    status: str
    text: str = ""


def _same(a: str, b: str) -> bool:
    """名前を、大文字小文字（と NFC/NFD）を区別せずに比べる。"""
    return pathcase.fold(tree.nfc(a)) == pathcase.fold(tree.nfc(b))


def record_path(conf: settings.Settings, parent: str) -> str:
    """親の片付けの記録の場所。

    大文字小文字だけが違う名前は同じ記録として扱うので、在るときはファイルの実際の表記で返す。
    無いときは与えた名前のまま組む。
    """
    if not conf.state:
        return ""
    base = os.path.join(conf.state, STATE_DIR)
    want = f"{parent}.json"
    try:
        existing = sorted(os.listdir(base))
    except OSError:
        existing = []
    for entry in existing:
        if _same(entry, want):
            return os.path.join(base, entry)
    return os.path.join(base, want)


def _projects_dir(root: str, conf: settings.Settings) -> str:
    if not conf.projects:
        return ""
    return conf.projects if os.path.isabs(conf.projects) else os.path.join(root, conf.projects)


def repo_dir(root: str, conf: settings.Settings, project: str) -> str:
    """そのリポジトリのディレクトリ（ワークスペース自身なら root）。"""
    if not project:
        return root
    return tree.project_root(_projects_dir(root, conf), project) or root


def _closed_children(root: str, conf: settings.Settings, parent: str) -> list[Child]:
    out = []
    for t in worktrees.tidy_targets(root, conf, parent):
        kind = KIND_CANCELLED if t.cancelled_at else KIND_MERGED
        out.append(Child(t.ticket, kind, t.merged_sha or "", t.project))
    return out


def read_record(conf: settings.Settings, parent: str) -> dict:
    path = record_path(conf, parent)
    data, failed = fsio.read_json(path) if path else (None, None)
    return data if failed is None and isinstance(data, dict) else {}


def _held_record(path: str) -> tuple[dict, str]:
    """前の回の記録と、読めなかった理由。無いのは普通なので `({}, "")`。

    あるのに読めない（壊れている・辞書でない）ものは、上書きすると前の回の子の一覧と MR の
    番号を失うので、呼び手は書かずに止める。
    """
    data, failed = fsio.read_json(path)
    if failed is not None:
        if isinstance(failed, FileNotFoundError):
            return {}, ""
        return {}, f"{failed}"
    if not isinstance(data, dict):
        return {}, "JSON の辞書ではない"
    if "children" in data and not isinstance(data["children"], list):
        return {}, "children が配列ではない"
    return data, ""


def _mr_text(value: object) -> str:
    """MR の番号。正の整数だけ数える（無い・形が違うなら空）。"""
    if isinstance(value, bool) or value is None:
        return ""
    text = str(value).strip()
    return text if text.isascii() and text.isdigit() and int(text) > 0 else ""


def _unreadable_closed(conf: settings.Settings, root: str, tree_root: str) -> list[str]:
    """親のワークツリーの閉じた承認済みチケットのうち、読めないもの（理由の文）。

    読めない閉じた子は子の一覧に入らない。そのまま退避へ進むと、ワークツリーとブランチの
    片付けの対象から、知らないうちに外れる。
    """
    if tree_root:
        return [
            text
            for path, text in approval.unreadable_copies(conf, tree_root)
            if approval.DONE_DIR in (path, os.path.basename(os.path.dirname(path)))
            or os.path.basename(path) == approval.DONE_DIR
        ]
    return approval.scan_all(conf, root, closed=True)[1]


def note(
    root: str,
    conf: settings.Settings,
    parent: str,
    project: str,
    tree_root: str,
    mr: object = None,
) -> str:
    """`ready` が子のチケットを退避へ移す前に、片付けの記録を書く。失敗した理由（無ければ空）。

    記録は `retire/<親>.json`（親、リポジトリ、ブランチ、MR の番号、閉じた子の一覧）。
    親のワークツリーを片付けた後に、`sync retired` がこれを列挙元にする。前の回（途中で
    止まった）の一覧には足し、同じ子は今回の値で置き換える。新しい `mr` が空なら前の回の値を保つ。

    書かずに理由を返すのは、記録の置き場が無いとき、前の回の記録が壊れているとき（上書きすると
    子の一覧と MR を失う）、読めない閉じたチケットがあるとき（子の一覧に入らない）。呼び手
    （`ready`）は、理由があれば退避へ進まずに止める。
    """
    path = record_path(conf, parent)
    if not path:
        return "state の置き場が空なので、片付けの記録を書けない"
    held, broken = _held_record(path)
    if broken:
        return (
            f"既存の片付けの記録 {path} を読めない（{broken}）。"
            "上書きしないので、直すか消してから打ち直す"
        )
    unread = _unreadable_closed(conf, root, tree_root)
    if unread:
        return (
            "読めない閉じたチケットがあり、片付ける子の一覧に入らない"
            f"（{'; '.join(unread[:3])}）。直してから打ち直す"
        )
    children = {
        c["ticket"]: c for c in held.get("children") or [] if isinstance(c, dict) and "ticket" in c
    }
    for c in _closed_children(root, conf, parent):
        children[c.ticket] = {
            "ticket": c.ticket,
            "kind": c.kind,
            "merged_sha": c.merged_sha,
            "project": c.project,
        }
    branch = (tree.branch_of(tree_root) if tree_root else "") or held.get("branch", "")
    data: dict = {
        "parent": parent,
        "project": project,
        "branch": branch,
        "children": [children[k] for k in sorted(children)],
    }
    kept = _mr_text(mr) or _mr_text(held.get("mr"))
    if kept:
        data["mr"] = int(kept)
    text = json.dumps(data, ensure_ascii=False, indent=1) + "\n"
    failed = fsio.write_text_atomic(path, text, newline="\n")
    return f"片付けの記録を書けない（{path}: {failed}）" if failed else ""


@dataclass
class Merged:
    """`merge_legacy` の結果。`status` は `moved`（書いた・足した）か `kept`（そのまま）。"""

    status: str = ""
    failed: str = ""


def merge_legacy(
    conf: settings.Settings, parent: str, project: str, branch: str, mr: str
) -> Merged:
    """古い取り込み状態の `mr` と `branch` を `retire/<親>.json` に写す（`sync migrate` が使う）。

    記録が無ければ新しく書く。既にあれば、そちらを尊重して、空の `mr` と `branch` だけを足す
    （足すものが無ければ `kept`）。既存の記録が壊れているときは書かず、理由を返す。
    """
    path = record_path(conf, parent)
    if not path:
        return Merged(failed="state の置き場が空なので、片付けの記録を書けない")
    held, broken = _held_record(path)
    if broken:
        return Merged(failed=f"既存の片付けの記録を読めない（{broken}）。上書きしない")
    present = os.path.lexists(path)
    if present and not _same(str(held.get("parent") or ""), parent):
        return Merged(failed="既存の片付けの記録の parent が親と合わない。上書きしない")
    data = dict(held) if present else {"parent": parent, "project": project, "children": []}
    changed = not present
    if not _mr_text(data.get("mr")) and _mr_text(mr):
        data["mr"] = int(_mr_text(mr))
        changed = True
    if not str(data.get("branch") or "") and branch:
        data["branch"] = branch
        changed = True
    data.setdefault("branch", "")
    if not changed:
        return Merged("kept")
    text = json.dumps(data, ensure_ascii=False, indent=1) + "\n"
    failed = fsio.write_text_atomic(path, text, newline="\n")
    return (
        Merged(failed=f"片付けの記録を書けない（{path}: {failed}）") if failed else Merged("moved")
    )


@dataclass
class Entry:
    """`sync retired` の 1 件。`incomplete` が空でなければ、列挙できない理由（他の欄は空）。"""

    parent: str
    repo: str = ""
    mr: str = ""
    branch: str = ""
    incomplete: str = ""


def entries(conf: settings.Settings) -> list[Entry]:
    """片付けの記録（`retire/*.json`）の列挙。読むだけで、ネットワークにも git にも触れない。

    数えるのは、ファイル名の幹と JSON の `parent` が一致するものだけ（記録を手で置き換えて、
    別の親を指させない）。読めない・`mr` が無い・ブランチが空白を含むものは `incomplete`
    で、列挙できない理由を言う。
    """
    directory = os.path.dirname(record_path(conf, "x")) if conf.state else ""
    try:
        names = sorted(os.listdir(directory)) if directory else []
    except OSError:
        return []
    out: list[Entry] = []
    for name in names:
        if not name.endswith(".json") or ".tmp." in name:
            continue
        stem = name[: -len(".json")]
        path = os.path.join(directory, name)
        if os.path.islink(path) or not os.path.isfile(path) or not ticket_ids.is_valid_id(stem):
            continue
        data, failed = fsio.read_json(path)
        if failed is not None or not isinstance(data, dict):
            reason = f"{failed}" if failed is not None else "JSON の辞書ではない"
            out.append(Entry(stem, incomplete=f"記録を読めない（{reason}）"))
            continue
        if not _same(str(data.get("parent") or ""), stem):
            continue
        if "children" in data and not isinstance(data["children"], list):
            out.append(Entry(stem, incomplete="記録の children が配列ではない"))
            continue
        project = str(data.get("project") or "")
        branch = str(data.get("branch") or "")
        mr = _mr_text(data.get("mr"))
        if not mr:
            out.append(Entry(stem, incomplete="記録に MR の番号（mr）が無い"))
        elif _spaced(project) or _spaced(branch):
            out.append(Entry(stem, incomplete="記録のリポジトリかブランチが空白を含む"))
        else:
            out.append(Entry(stem, tree.repo_key(project), mr, branch))
    return out


def _spaced(text: str) -> bool:
    return any(ch.isspace() for ch in text)


def run_abandon(
    stdout: TextIO, stderr: TextIO, root: str, conf: settings.Settings, parent: str
) -> int:
    """`worktree retire --abandon <親>`。放棄した親（MR をマージせずに閉じた親）の記録を消す。

    記録を消すと、`ccnavi-sync.sh` はその親を片付け待ちとして知らせなくなる。親のワークツリーが
    残っている間は、まだ放棄していないとみなして断る（ワークツリーを片付けてから打つ）。
    ユーザが端末で打つ。
    """
    path = record_path(conf, parent)
    if not path:
        stderr.write("ccnavi: state の置き場が空なので、片付けの記録を消せません\n")
        return 1
    where = tree.worktree_path(root, parent)
    if os.path.lexists(where):
        stderr.write(
            f"ccnavi: 親 {parent} のワークツリー（{where}）が残っているので、記録は消しません。"
            "放棄するなら、先にワークツリーを片付けてください\n"
        )
        return 1
    if not os.path.lexists(path):
        stderr.write(f"ccnavi: 親 {parent} の片付けの記録はありません\n")
        return 1
    failed = fsio.unlink(path)
    if failed:
        stderr.write(f"ccnavi: 片付けの記録を消せませんでした（{path}: {failed}）\n")
        return 1
    stdout.write(f"親 {parent} の片付けの記録を消しました。ブランチは残っています\n")
    return 0


def _children_from(record: dict) -> list[Child]:
    out = []
    children = record.get("children")
    for c in children if isinstance(children, list) else []:
        if (
            isinstance(c, dict)
            and c.get("ticket")
            and c.get("kind") in (KIND_MERGED, KIND_CANCELLED)
        ):
            out.append(
                Child(
                    str(c["ticket"]),
                    str(c["kind"]),
                    str(c.get("merged_sha") or ""),
                    str(c.get("project") or ""),
                )
            )
    return out


# ---- git（ローカルの読み取りと、ブランチの削除だけ）


def _commit(repo: str, rev: str) -> str:
    done = gitcmd.run(repo, ["rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"])
    return done.out.strip() if done.ok else ""


def _tip(repo: str, name: str) -> str:
    return _commit(repo, f"refs/heads/{name}")


def _is_ancestor(repo: str, older: str, newer: str) -> bool:
    return gitcmd.run(repo, ["merge-base", "--is-ancestor", older, newer]).ok


def _checked_out_at(repo: str, name: str) -> str:
    """そのブランチをチェックアウトしているワークツリーのパス。無ければ空。"""
    done = gitcmd.run(repo, ["worktree", "list", "--porcelain"], raw_paths=True)
    if not done.ok:
        return "（ワークツリーの一覧を読めない）"
    for block in done.out.split("\n\n"):
        lines = block.splitlines()
        if f"branch refs/heads/{name}" in lines:
            return lines[0][len("worktree ") :] if lines[0].startswith("worktree ") else "?"
    return ""


def _delete(repo: str, name: str) -> str:
    """`git branch -D`。失敗した理由（無ければ空）。条件の確認は呼び手が済ませてある。"""
    done = gitcmd.run(repo, ["branch", "-D", "--", name])
    if done.ok:
        return ""
    said = (done.err or done.failure or "").strip().splitlines()
    return said[0] if said else "理由不明"


def hand_command(repo: str, name: str) -> str:
    return f"git -C {repo} branch -D {name}"


def _keep(repo: str, name: str, why: str) -> Outcome:
    return Outcome(
        KEPT,
        f"ブランチ {name} は残した: {why}\n"
        f"  確かめたうえで手で消すなら: {hand_command(repo, name)}",
    )


def judge_child(
    repo: str, child: Child, reference: str, confirmed: bool = False, tool: str = ""
) -> Outcome:
    """子のブランチを消してよいか確かめて、よければ消す。

    `reference` は「push 済みの親」を指す版（`refs/remotes/origin/<親のブランチ>`、または
    MR の先頭の sha）。
    `tool` は確認が取れた後に打つコマンド（取り消した子の案内に使う）。
    """
    name = child.ticket
    tip = _tip(repo, name)
    if not tip:
        return Outcome(NONE)
    base = _commit(repo, reference) if reference else ""
    if child.kind == KIND_CANCELLED:
        extra = ""
        if base:
            counted = gitcmd.run(repo, ["rev-list", "--count", f"{base}..{tip}"])
            if counted.ok:
                extra = f"（親に無いコミット {counted.out.strip()} 件）"
        if not confirmed:
            return Outcome(
                ASK,
                f"- {name}{extra}\n  ユーザが打つ: {tool}".rstrip() if tool else f"- {name}{extra}",
            )
        return _drop_branch(repo, name, tip, "取り消した子。ユーザが確認した")
    if not child.merged_sha:
        return _keep(
            repo, name, "合流した先頭の sha（merged_sha）が記録に無く、合流済みか確かめられない"
        )
    merged = _commit(repo, child.merged_sha)
    if not merged:
        return _keep(repo, name, f"合流した先頭 {child.merged_sha[:12]} が手元に無い")
    if tip != merged:
        return _keep(
            repo,
            name,
            f"ブランチの先頭 {tip[:12]} が、合流した先頭 {merged[:12]} と違う"
            "（合流のあとに進んでいる）",
        )
    if not base:
        return _keep(
            repo, name, "push 済みの親の先頭を読めず、合流した先頭が含まれるか確かめられない"
        )
    if not _is_ancestor(repo, merged, base):
        return _keep(repo, name, f"合流した先頭 {merged[:12]} が push 済みの親に含まれない")
    return _drop_branch(repo, name, tip, "合流済み")


def _drop_branch(repo: str, name: str, tip: str, why: str) -> Outcome:
    where = _checked_out_at(repo, name)
    if where:
        return _keep(repo, name, f"ワークツリー（{where}）がチェックアウトしている")
    failed = _delete(repo, name)
    if failed:
        return _keep(repo, name, f"git branch -D が失敗した（{failed}）")
    return Outcome(DELETED, f"ブランチ {name} を削除しました（{why}。先頭 {tip[:12]}）")


# ---- ready の後（片付け 1）


def _drop_worktree(stdout: TextIO, root: str, name: str, cwd: str, note_text: str) -> bool:
    """ワークツリーがあれば消す。消した・もともと無いなら True。"""
    path = tree.worktree_path(root, name)
    if not os.path.lexists(path):
        return True
    d = worktrees.drop(root, name, cwd)
    worktrees.report(stdout, root, d, note_text)
    return d.removed


def _say(stdout: TextIO, outcome: Outcome) -> None:
    if outcome.text:
        stdout.write(outcome.text + "\n")


def _clean_script(root: str) -> str:
    return settings.script_command(root, "ccnavi-user-clean-branch.sh")


def _confirm_command(root: str, name: str) -> str:
    """取り消した子のブランチを消す、ユーザが打つコマンド（`!` は呼び手が付ける）。

    エージェントからは hook が止める。ユーザが確認したことを、ユーザ自身が打つことで担保する。
    """
    return f"{_clean_script(root)} {name} --confirmed"


def _parent_of(root: str, conf: settings.Settings, project: str, ident: str) -> str:
    """そのチケットの `parent:`。承認済みの置き場（作業中・閉じた・レビュー待ち）を先に、無ければ
    手元の退避を見る。見つからなければ空。"""
    raw = approval.read_raw(conf, root)
    found = (
        approval.scan(conf, root, raw=raw)[0]
        + approval.scan(conf, root, closed=True, raw=raw)[0]
        + approval.scan_review(conf, root, raw=raw)[0]
    )
    for t in found:
        if _same(t.ticket, ident):
            return t.parent or ""
    copy = archive.archived_fields(root, project, ident)
    return (copy.parent or "") if copy is not None else ""


def run_retire(
    stdout: TextIO, stderr: TextIO, root: str, conf: settings.Settings, family: str, cwd: str
) -> int:
    """`worktree retire <親>`。子のワークツリーとブランチ、親のワークツリーを片付ける。

    Draft を外した後に呼ぶ。消さなかったものがあれば 1。
    """
    family = worktrees.family_for(root, conf, family)
    held, broken = _held_record(record_path(conf, family)) if conf.state else ({}, "")
    if broken:
        stderr.write(
            f"ccnavi: 親 {family} の片付けの記録を読めない（{broken}）。"
            "子の一覧が分からないので、何も消さない。直すか消してから打ち直す\n"
        )
        return 1
    children = {c.ticket: c for c in _children_from(held)}
    for c in _closed_children(root, conf, family):
        children.setdefault(c.ticket, c)
    parent_path = tree.worktree_path(root, family)
    branch = held.get("branch") or (
        tree.branch_of(parent_path) if os.path.isdir(parent_path) else ""
    )
    reference = f"refs/remotes/origin/{branch}" if branch else ""
    code = 0
    asking: list[Outcome] = []
    for name in sorted(children):
        child = children[name]
        repo = repo_dir(root, conf, child.project)
        owner = worktrees._owner(tree.worktree_path(root, name))[0]
        if owner:
            repo = owner
        if not _drop_worktree(
            stdout, root, name, cwd, "。子のブランチは条件を確かめて別に片付ける"
        ):
            code = 1
        outcome = judge_child(repo, child, reference, tool=f"! {_confirm_command(root, name)}")
        if outcome.status == ASK:
            asking.append(outcome)
            continue
        _say(stdout, outcome)
        if outcome.status == KEPT:
            code = 1
    if asking:
        stdout.write(
            "取り消した子のブランチ（親に取り込まれていないコミットを含みうる）。"
            "この一覧をユーザに見せる。消すのはユーザで、消してよければ次のコマンドを"
            "ユーザが先頭に ! を付けて打つ（エージェントからは打てない）:\n"
        )
        for outcome in asking:
            stdout.write(outcome.text + "\n")
        code = 1
    if not _drop_worktree(
        stdout, root, family, cwd, "。ブランチは MR のマージを確かめてから片付ける"
    ):
        code = 1
    return code


def run_branch(
    stdout: TextIO,
    stderr: TextIO,
    root: str,
    conf: settings.Settings,
    name: str,
    confirmed: bool,
) -> int:
    """`worktree branch <名前> [confirmed]`。片付けで残した子のブランチを、確かめ直して消す。

    ccnavi が片付ける子として記録したブランチだけ。取り消した子は `confirmed`
    （ユーザが確認した）が要る。
    """
    found: tuple[dict, Child] | None = None
    directory = os.path.dirname(record_path(conf, "x")) if conf.state else ""
    try:
        names = sorted(os.listdir(directory)) if directory else []
    except OSError:
        names = []
    for entry in names:
        if not entry.endswith(".json"):
            continue
        parent = entry[: -len(".json")]
        # 壊れた記録は飛ばして続ける（1 本の壊れで、ほかの親の子のブランチ片付けを止めない）。
        record, broken = _held_record(os.path.join(directory, entry))
        if broken or not _same(str(record.get("parent") or ""), parent):
            continue
        for child in _children_from(record):
            if _same(child.ticket, name):
                found = (record, child)
    if found is None:
        stderr.write(
            f"ccnavi: {name} は、親を閉じたときに片付ける子として記録したブランチではない。"
            "任意のブランチは消さない\n"
        )
        return 1
    record, child = found
    name = child.ticket  # 以降は記録の綴りで扱う（ブランチは git が大文字小文字を区別する）
    # 記録は state の置き場のファイルで、書き換えれば消す対象を広げられる。消す前に、記録の子が
    # 実際にこの親の子か（承認済みチケットか退避の `parent:` が記録の親か）を確かめ直す。
    parent = str(record.get("parent") or "")
    actual = _parent_of(root, conf, child.project, name)
    if not parent or not _same(actual, parent):
        stderr.write(
            f"ccnavi: {name} は、記録の親（{parent or '空'}）の子として承認済みチケットにも"
            f"退避にも見つからない（見つかった親: {actual or 'なし'}）。記録を信じて消さない\n"
        )
        return 1
    branch = str(record.get("branch") or "")
    reference = f"refs/remotes/origin/{branch}" if branch else ""
    repo = repo_dir(root, conf, child.project)
    outcome = judge_child(repo, child, reference, confirmed=confirmed)
    if outcome.status == NONE:
        stdout.write(f"ブランチ {name} はありません（消すものがありません）\n")
        return 0
    if outcome.status == ASK:
        stderr.write(
            f"ccnavi: {name} は取り消した子のブランチ。消すのはユーザで、一覧を見て消してよければ"
            f"ユーザが '! {_confirm_command(root, name)}' を打ちます\n"
        )
        return 1
    _say(stdout, outcome)
    return 0 if outcome.status == DELETED else 1


# ---- MR がマージされたと分かった後（片付け 2）


def _parent_head_blocker(parent_path: str, branch: str, has_ref: bool) -> str:
    """親のワークツリーが在るのに、そのままブランチの確認に進めない理由。進めるなら空。

    ワークツリーの HEAD が記録のブランチを指し、そのブランチの ref が手元に在るときだけ、
    ブランチの確認（`_merged_blocker`）が実際の HEAD を見たことになる。
    """
    head = tree.branch_of(parent_path)
    if head is None:
        return (
            "親のワークツリーの HEAD がブランチを指していない（切り離し、または読めない）ので、"
            "成果が失われないか確かめられない"
        )
    if head != branch:
        return (
            f"親のワークツリーの HEAD は {head} で、記録のブランチ {branch} と違う。"
            f"{head} のコミットが push 済みか確かめられない"
        )
    if not has_ref:
        return f"ブランチ {branch} の ref が手元に無いのに親のワークツリーが在り、確かめられない"
    return ""


def _merged_blocker(repo: str, branch: str, tip: str, head_sha: str) -> str:
    """親のブランチを消してはいけない理由。消してよければ空。

    手元の先頭が push 済み（`refs/remotes/origin/<ブランチ>`）に含まれることも確かめる。
    MR の先頭は呼び手（sh）が渡す値で、ブランチ自身の先頭を渡せば必ず「含まれる」になる。
    ネットワークには出ず、手元の remote-tracking ref で見る。無ければ消さない。
    """
    pushed = _commit(repo, f"refs/remotes/origin/{branch}")
    if not pushed:
        return (
            f"push 済みの先頭（origin/{branch}）が手元に無く、未 push のコミットが無いか"
            "確かめられない"
        )
    if not _is_ancestor(repo, tip, pushed):
        return (
            f"手元の先頭 {tip[:12]} が、push 済みの先頭（origin/{branch}）{pushed[:12]} に"
            "含まれない（未 push のコミットがある）"
        )
    merged_head = _commit(repo, head_sha)
    if not merged_head:
        return f"マージされた MR の先頭 {head_sha[:12]} が手元に無く、含まれるか確かめられない"
    if not _is_ancestor(repo, tip, merged_head):
        return (
            f"手元の先頭 {tip[:12]} が、マージされた MR の先頭 {merged_head[:12]} に含まれない"
            "（未 push のコミットがある）"
        )
    return ""


def run_merged(
    stdout: TextIO,
    stderr: TextIO,
    root: str,
    conf: settings.Settings,
    family: str,
    project: str,
    branch: str,
    head_sha: str,
    cwd: str,
) -> int:
    """`sync tidy <親> <リポジトリ> <ブランチ> <sha>`。マージ済みの MR の先頭 sha を基準に片付ける。

    1 行ずつ言う。最後の行は `record yes|no`。親のワークツリーも親のブランチも残っていなければ yes
    （親の片付けの記録 `retire/<親>.json` を消した、または元から無い）。

    順は、ブランチの確認、ワークツリーの削除、ブランチの削除。同じ名前のブランチを作り直して
    使っている親のワークツリー（MR の先頭に含まれない先頭を持つ）を、先に消さないため。
    ブランチが確認を通らなければ、ワークツリーも消さない。
    """
    repo = repo_dir(root, conf, project)
    clean = True
    tip = _tip(repo, branch)
    parent_path = tree.worktree_path(root, family)
    on_disk = os.path.lexists(parent_path)
    # ワークツリーが在るなら、その HEAD が記録のブランチか確かめる。`worktrees.drop` が見るのは
    # 未コミットと ignored だけ。HEAD が push 済みか・MR に含まれるかはここで確かめる。
    blocker = _parent_head_blocker(parent_path, branch, bool(tip)) if on_disk else ""
    if not blocker and tip:
        blocker = _merged_blocker(repo, branch, tip, head_sha)
    if blocker:
        stdout.write(
            f"親 {family} のワークツリーは、ブランチが片付けの条件を満たさないので消しません\n"
        )
        if tip:
            _say(stdout, _keep(repo, branch, blocker))
        else:
            stdout.write(f"親のワークツリーは消しませんでした: {blocker}\n")
        clean = False
    else:
        if not _drop_worktree(
            stdout, root, family, cwd, "。ブランチは MR の先頭に含まれるか確かめて片付ける"
        ):
            clean = False
        if tip:
            outcome = _drop_branch(repo, branch, tip, "MR がマージ済みで、先頭が MR に含まれる")
            _say(stdout, outcome)
            if outcome.status != DELETED:
                clean = False
    path = record_path(conf, family)
    held, broken = _held_record(path) if path else ({}, "")
    if broken:
        # 子の一覧を読めないので、子の片付けは進められず、記録も消さない。
        stdout.write(f"親 {family} の片付けの記録を読めないので、子は片付けません: {broken}\n")
        clean = False
    asking: list[Outcome] = []
    for child in _children_from(held):
        if not _drop_worktree(stdout, root, child.ticket, cwd, "。ブランチは別に片付ける"):
            clean = False
        outcome = judge_child(
            repo_dir(root, conf, child.project),
            child,
            head_sha,
            tool=f"! {_confirm_command(root, child.ticket)}",
        )
        if outcome.status == ASK:
            asking.append(outcome)
        elif outcome.status != NONE:
            _say(stdout, outcome)
            clean = clean and outcome.status == DELETED
    if asking:
        stdout.write(
            "取り消した子のブランチが残っている。一覧をユーザに見せる。消すのはユーザで、"
            "消してよければ次のコマンドをユーザが先頭に ! を付けて打つ:\n"
        )
        for outcome in asking:
            stdout.write(outcome.text + "\n")
    if clean and not asking and path and os.path.lexists(path):
        fsio.unlink(path)
    stdout.write(f"record {'yes' if clean else 'no'}\n")
    return 0
