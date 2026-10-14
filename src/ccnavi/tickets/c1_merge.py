"""C1 の finish が、子を親のブランチへ合流する前に sh が聞くこと。

子の `finish` は、取り込み済みの親では、親のワークツリーで子のブランチを合流し（`merge --no-ff
--no-commit`）、完了の記録と一緒に 1 つのマージコミットにして push する。git を動かすのは sh
（`ccnavi-common-c1.sh`）で、実行ファイルはネットワークに出ず、コミットもしない。ここが答えるのは
合流してよいかの判定と、衝突したときに誰のワークツリーで解くかの案内だけ。どちらも 1 行 1 項目
（`<鍵> <値>`。sh は jq を使わない）。

- `ccnavi c1 merge <子>`: 合流の前の検査
  - `skip <理由>`: 合流しない（親の finish、作業中でない子など）。sh は `ticket finish` へ進み、
    止めるべきことはそちらが言う
  - `stop <理由>` と続く `hint <案内>`: 何も変えずに止める
  - 合流してよいなら `child`・`tree`・`head`・`base`・`merge yes|no`（子の先頭が親の先頭の祖先なら
    合流するものが無いので `no`）
  - 止めるのは、親のワークツリーに状態の置き場以外の未コミットの変更がある、子のワークツリーが無い・
    子のブランチにいない・未コミットの変更がある、子の仕事の始まりから先頭までの変更に置き場のパスが
    ある、範囲（子・親・フェーズ定義）の外のパスがある、のどれか
- `ccnavi c1 merge-blame <子> <パス>...`: 衝突したパスを、先に親へ合流した別の子の変更から探す。
  `other <子> <パス>` を 1 パス 1 行で（パスの空白はそのまま、改行は `\\n`）。
  見つからなければ何も出さない

git は読み取りだけ。
"""

from __future__ import annotations

import io
import os
from typing import TextIO

from ..infra import gitcmd, gitstate, settings, tree
from ..policy import rules
from . import (
    approval,
    approval_checks,
    c1,
    ops_close,
    phase_scope,
    ticket_model,
    ticket_places,
)

_TIMEOUT = 20.0
_SHOWN = 8


def _git(cwd: str, args: list[str]) -> tuple[bool, str]:
    done = gitcmd.run(cwd, args, _TIMEOUT, raw_paths=True)
    return done.ok, done.out


def _names(listing: str) -> list[str]:
    return [p for p in listing.split("\0") if p]


def _shown(paths: list[str]) -> str:
    more = f" ほか {len(paths) - _SHOWN} 件" if len(paths) > _SHOWN else ""
    return ", ".join(paths[:_SHOWN]) + more


def _one_line(text: str) -> str:
    """1 行 1 項目の値に、改行を混ぜない（`\\n` の 2 文字にする）。"""
    return text.replace("\r", "\\r").replace("\n", "\\n")


def _say(stdout: TextIO, key: str, text: str) -> None:
    stdout.write(f"{key} {' '.join(text.split())}\n")


def plan(stdout: TextIO, conf: settings.Settings, root: str, ident: str) -> int:
    """`ccnavi c1 merge <子>`。合流の前の検査。"""
    stdout.write(c1.HEAD + "\n")
    parent_id = c1.family_of(ident)
    if parent_id == ident:
        _say(stdout, "skip", "親は合流しない")
        return 0
    found = ops_close.find(io.StringIO(), root, conf, ident)
    if found is None or not found.is_child:
        _say(stdout, "skip", "子の承認済みチケットを引けない（finish が理由を言う）")
        return 0
    if found.state != ticket_model.DOING or not found.started_at:
        _say(stdout, "skip", "作業中の着手済みの子ではない（finish が理由を言う）")
        return 0
    parent_tree = tree.worktree_path(root, parent_id)
    child_tree = tree.worktree_path(root, ident)
    problem = _tree_problem(conf, parent_tree, child_tree, ident)
    if problem:
        _stop(stdout, *problem)
        return 0
    # 子の先頭はここで 1 回だけ読む。検査も合流（sh の `git merge <sha>`）もこの sha を使い、
    # 検査のあとに子が積んだコミットは合流に載せない。
    head = gitstate.head(child_tree)
    parent_head = gitstate.head(parent_tree)
    if not (head and parent_head):
        _stop(stdout, f"子 {ident} の先頭を読めない。何も変えずに止めた", [])
        return 0
    copies, _ = approval.scan(conf, root)
    parent = approval_checks.by_id(copies).get(parent_id)
    places, outside, why = phase_scope.merge_findings(root, conf, found, parent, head)
    if why:
        _stop(stdout, f"子 {ident} の変更を調べられない（{why}）。何も変えずに止めた", [])
        return 0
    if places:
        _stop(
            stdout,
            f"子 {ident} のコミットに、チケットの置き場の変更がある（{_shown(places)}）。"
            "置き場はユーザの承認と ccnavi が書くもので、子のコミットでは親へ持ち込めない。"
            "合流しない",
            [
                "置き場の変更を子のコミットから取り除いてください"
                "（ccnavi-git.sh では置き場のパスを戻せないので、できなければユーザに頼む）"
            ],
        )
        return 0
    if outside:
        area = ", ".join(found.paths(rules.ALLOW) + found.paths(rules.ASK)) or "(空)"
        names = [rel + _limit(found, verdict) for rel, verdict in outside]
        _stop(
            stdout,
            f"子 {ident} のコミットに、範囲の外のパスがある（{_shown(names)}）。"
            f"範囲は {area}。合流しない",
            [
                "範囲の中へ戻すか、必要ならユーザに伝えて次のチケットにしてください。"
                "直してコミットしてから finish をやり直す"
            ],
        )
        return 0
    base, why = gitstate.child_base(child_tree, parent_tree, child_head=head)
    if not base:
        _stop(stdout, f"子 {ident} の先頭を読めない（{why}）。何も変えずに止めた", [])
        return 0
    done = gitcmd.run(child_tree, ["merge-base", "--is-ancestor", head, parent_head], _TIMEOUT)
    _say(stdout, "child", ident)
    stdout.write(f"tree {child_tree}\n")
    _say(stdout, "head", head)
    _say(stdout, "base", base)
    _say(stdout, "merge", "no" if done.ok else "yes")
    return 0


def _limit(child: ticket_model.Ticket, verdict: phase_scope.ScopeVerdict) -> str:
    names = {
        phase_scope.LIMIT_PARENT: "親の範囲の外",
        phase_scope.LIMIT_TYPE: "フェーズ定義の範囲の外",
        phase_scope.LIMIT_BLOCKED: "子のチケットを信頼できない",
    }
    note = names.get(verdict.limit, "")
    return f"（{note}）" if note else ""


def _stop(stdout: TextIO, why: str, hints: list[str]) -> None:
    _say(stdout, "stop", why)
    for hint in hints:
        _say(stdout, "hint", hint)


def _tree_problem(
    conf: settings.Settings, parent_tree: str, child_tree: str, ident: str
) -> tuple[str, list[str]] | None:
    """親と子のワークツリーが合流できる形か。できなければ（止める理由, 案内）。"""
    ok, out = _git(
        parent_tree, ["status", "--porcelain", "-z", "--untracked-files=no", "--no-renames"]
    )
    if not ok:
        return "親のワークツリーの状態を読めない。何も変えずに止めた", []
    dirty = [
        entry[3:]
        for entry in _names(out)
        if len(entry) > 3
        and not ticket_places.is_ticket_place(entry[3:], conf.tickets, conf.approved)
    ]
    if dirty:
        return (
            f"親のワークツリーに未コミットの変更がある（{_shown(dirty)}）。"
            "サブエージェントの作業がコミットされていません。何も変えずに止めた",
            [
                "親のワークツリー（メインの作業場）には、状態の置き場以外の変更を残さない。"
                "子のワークツリーへ移してコミットするか、取り除いてから finish をやり直す"
            ],
        )
    if not os.path.isdir(child_tree):
        return (
            f"子 {ident} のワークツリー（{child_tree}）が無い。何も変えずに止めた",
            ["ワークツリーを作り直して、子のブランチの作業を確かめてください"],
        )
    ok, common_parent = _git(parent_tree, ["rev-parse", "--git-common-dir"])
    ok_child, common_child = _git(child_tree, ["rev-parse", "--git-common-dir"])
    if not (ok and ok_child) or not _same(
        os.path.join(parent_tree, common_parent.strip()),
        os.path.join(child_tree, common_child.strip()),
    ):
        return (
            f"子 {ident} のワークツリーが親のワークツリーと同じリポジトリのものでない。"
            "何も変えずに止めた",
            [],
        )
    ok, ref = _git(child_tree, ["symbolic-ref", "-q", "HEAD"])
    if not ok or ref.strip() != f"refs/heads/{ident}":
        return (
            f"子 {ident} のワークツリーが子のブランチ {ident} にいない"
            f"（{ref.strip() or '分離した HEAD'}）。何も変えずに止めた",
            [f"子のワークツリーで子のブランチ {ident} に切り替えてから finish をやり直す"],
        )
    ok, out = _git(
        child_tree, ["status", "--porcelain", "-z", "--untracked-files=all", "--no-renames"]
    )
    if not ok:
        return f"子 {ident} のワークツリーの状態を読めない。何も変えずに止めた", []
    unsaved = [entry[3:] for entry in _names(out) if len(entry) > 3]
    if unsaved:
        return (
            f"子 {ident} のワークツリーに未コミットの変更がある（{_shown(unsaved)}）。"
            "サブエージェントの作業がコミットされていません。何も変えずに止めた",
            ["サブエージェントにコミットさせるか、不要な変更を取り除いてから finish をやり直す"],
        )
    return None


def _same(a: str, b: str) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def blame(stdout: TextIO, conf: settings.Settings, root: str, ident: str, paths: list[str]) -> int:
    """`ccnavi c1 merge-blame <子> <パス>...`。衝突したパスを持ち込んだ、先に合流した別の子。"""
    stdout.write(c1.HEAD + "\n")
    parent_id = c1.family_of(ident)
    parent_tree = tree.worktree_path(root, parent_id)
    child_tree = tree.worktree_path(root, ident)
    parent_head = gitstate.head(parent_tree)
    child_head = gitstate.head(child_tree)
    if not parent_head or not child_head or not paths:
        return 0
    copies, _ = approval.scan(conf, root)
    closed, _ = approval.scan(conf, root, closed=True)
    review, _ = approval.scan_review(conf, root)
    wanted = set(paths)
    ok, listing = _git(
        parent_tree, ["rev-list", "--parents", "--merges", parent_head, f"^{child_head}"]
    )
    merges = [line.split() for line in listing.splitlines()] if ok else []
    for other in copies + review + closed:
        if other.parent != parent_id or other.ticket == ident or not other.merged_sha:
            continue
        # その子の finish が作ったマージコミット（2 つ目の親が合流した子の先頭）の変更。
        for commit in merges:
            if len(commit) != 3 or commit[2] != other.merged_sha:
                continue
            ok, out = _git(
                parent_tree,
                ["diff", "--name-only", "--no-renames", "-z", commit[1], commit[0]],
            )
            # 1 行に 1 パス。パスに空白があっても、sh は子の識別子（空白を含まない）の
            # 後ろを丸ごと読む。
            for rel in sorted(wanted & set(_names(out))) if ok else []:
                stdout.write(f"other {other.ticket} {_one_line(rel)}\n")
            break
    return 0
