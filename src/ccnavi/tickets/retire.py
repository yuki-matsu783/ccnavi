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
  - 取り消した子: ユーザに一覧を見せて確認が取れてから（`worktree branch <名前> confirmed`）。
    確認なしでは消さない。親に無いコミットの数を添える
- 親のワークツリーを消す

## MR がマージされたと分かった後（片付け 2）

`ccnavi-sync.sh` がホストに聞いて、マージ済みの MR の番号と先頭の sha を渡す。

- 親のワークツリーが残っていれば消す（同じ条件）
- 親の手元のブランチを消す。条件は、手元の先頭が push 済みの先頭
  （`refs/remotes/origin/<ブランチ>`）と MR の先頭の両方に含まれること。
  MR の先頭は sh が渡す値なので、それだけでは信じない。
  どちらかが手元に無ければ、含まれるか確かめられないので消さない
- 残っていた子のブランチは、MR の先頭を基準に片付け 1 と同じ条件で消す
- すべて片付いたら `record yes` を言う。sh はそれを見て、その親の取り込み記録を消す

ブランチを消す権限はエージェントに開けない（`git branch -D` は組み込みで止まる）。条件の確認は
ここで行い、消すのもここだけ。ネットワークには出ない。git はローカルの操作（`rev-parse`・
`merge-base`・`worktree list`・`branch -D`）だけ。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import TextIO

from ..infra import fsio, gitcmd, settings, tree
from . import worktrees

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


def record_path(conf: settings.Settings, parent: str) -> str:
    return os.path.join(conf.state, STATE_DIR, f"{parent}.json") if conf.state else ""


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


def note(root: str, conf: settings.Settings, parent: str, project: str, tree_root: str) -> str:
    """`ready` が子のチケットを退避へ移す前に、片付ける子の一覧を書く。失敗した理由（無ければ空）。

    前の回（途中で止まった）の一覧には足す。同じ子は今回の値で置き換える。
    """
    path = record_path(conf, parent)
    if not path:
        return "state の置き場が空なので、片付ける子の一覧を書けない"
    held = read_record(conf, parent)
    children = {c["ticket"]: c for c in held.get("children", []) if isinstance(c, dict)}
    for c in _closed_children(root, conf, parent):
        children[c.ticket] = {
            "ticket": c.ticket,
            "kind": c.kind,
            "merged_sha": c.merged_sha,
            "project": c.project,
        }
    branch = (tree.branch_of(tree_root) if tree_root else "") or held.get("branch", "")
    data = {
        "parent": parent,
        "project": project,
        "branch": branch,
        "children": [children[k] for k in sorted(children)],
    }
    text = json.dumps(data, ensure_ascii=False, indent=1) + "\n"
    failed = fsio.write_text(path, text, newline="\n")
    return f"片付ける子の一覧を書けない（{path}: {failed}）" if failed else ""


def _children_from(record: dict) -> list[Child]:
    out = []
    for c in record.get("children", []):
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
                f"- {name}{extra}\n  ユーザが消してよいと言ったら: {tool}".rstrip()
                if tool
                else f"- {name}{extra}",
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
    return settings.script_command(root, "ccnavi-clean.sh")


def run_retire(
    stdout: TextIO, stderr: TextIO, root: str, conf: settings.Settings, family: str, cwd: str
) -> int:
    """`worktree retire <親>`。子のワークツリーとブランチ、親のワークツリーを片付ける。

    Draft を外した後に呼ぶ。消さなかったものがあれば 1。
    """
    family = worktrees.family_for(root, conf, family)
    held = read_record(conf, family)
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
        outcome = judge_child(
            repo, child, reference, tool=f"{_clean_script(root)} --branch {name} --confirmed"
        )
        if outcome.status == ASK:
            asking.append(outcome)
            continue
        _say(stdout, outcome)
        if outcome.status == KEPT:
            code = 1
    if asking:
        stdout.write(
            "取り消した子のブランチ（親に取り込まれていないコミットを含みうる）。"
            "この一覧をユーザに見せて、消してよいか確かめてから消す:\n"
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
        record = read_record(conf, entry[: -len(".json")])
        for child in _children_from(record):
            if child.ticket == name:
                found = (record, child)
    if found is None:
        stderr.write(
            f"ccnavi: {name} は、親を閉じたときに片付ける子として記録したブランチではない。"
            "任意のブランチは消さない\n"
        )
        return 1
    record, child = found
    branch = str(record.get("branch") or "")
    reference = f"refs/remotes/origin/{branch}" if branch else ""
    repo = repo_dir(root, conf, child.project)
    outcome = judge_child(repo, child, reference, confirmed=confirmed)
    if outcome.status == NONE:
        stdout.write(f"ブランチ {name} はありません（消すものがありません）\n")
        return 0
    if outcome.status == ASK:
        stderr.write(
            f"ccnavi: {name} は取り消した子のブランチ。ユーザに消してよいか確かめてから、"
            f"'{_clean_script(root)} --branch {name} --confirmed' を実行します\n"
        )
        return 1
    _say(stdout, outcome)
    return 0 if outcome.status == DELETED else 1


# ---- MR がマージされたと分かった後（片付け 2）


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
    （sh がその親の取り込み記録を消す）。
    """
    repo = repo_dir(root, conf, project)
    clean = True
    if not _drop_worktree(
        stdout, root, family, cwd, "。ブランチは MR の先頭に含まれるか確かめて片付ける"
    ):
        clean = False
    tip = _tip(repo, branch)
    if tip:
        merged_head = _commit(repo, head_sha)
        # 手元の先頭が push 済み（`refs/remotes/origin/<ブランチ>`）に含まれることも確かめる。
        # MR の先頭は呼び手（sh）が渡す値で、ブランチ自身の先頭を渡せば必ず「含まれる」になる。
        # ネットワークには出ず、手元の remote-tracking ref で見る。無ければ消さない。
        pushed = _commit(repo, f"refs/remotes/origin/{branch}")
        if not pushed:
            outcome = _keep(
                repo,
                branch,
                f"push 済みの先頭（origin/{branch}）が手元に無く、未 push のコミットが無いか"
                "確かめられない",
            )
        elif not _is_ancestor(repo, tip, pushed):
            outcome = _keep(
                repo,
                branch,
                f"手元の先頭 {tip[:12]} が、push 済みの先頭（origin/{branch}）{pushed[:12]} に"
                "含まれない（未 push のコミットがある）",
            )
        elif not merged_head:
            outcome = _keep(
                repo,
                branch,
                f"マージされた MR の先頭 {head_sha[:12]} が手元に無く、含まれるか確かめられない",
            )
        elif not _is_ancestor(repo, tip, merged_head):
            outcome = _keep(
                repo,
                branch,
                f"手元の先頭 {tip[:12]} が、マージされた MR の先頭 {merged_head[:12]} に含まれない"
                "（未 push のコミットがある）",
            )
        else:
            outcome = _drop_branch(repo, branch, tip, "MR がマージ済みで、先頭が MR に含まれる")
        _say(stdout, outcome)
        if outcome.status != DELETED:
            clean = False
    held = read_record(conf, family)
    asking: list[Outcome] = []
    for child in _children_from(held):
        if not _drop_worktree(stdout, root, child.ticket, cwd, "。ブランチは別に片付ける"):
            clean = False
        outcome = judge_child(
            repo_dir(root, conf, child.project),
            child,
            head_sha,
            tool=f"{_clean_script(root)} --branch {child.ticket} --confirmed",
        )
        if outcome.status == ASK:
            asking.append(outcome)
        elif outcome.status != NONE:
            _say(stdout, outcome)
            clean = clean and outcome.status == DELETED
    if asking:
        stdout.write("取り消した子のブランチが残っている。ユーザに確かめてから消す:\n")
        for outcome in asking:
            stdout.write(outcome.text + "\n")
    if clean and not asking:
        path = record_path(conf, family)
        if path and os.path.lexists(path):
            fsio.unlink(path)
    stdout.write(f"record {'yes' if clean else 'no'}\n")
    return 0
