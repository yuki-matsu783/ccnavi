"""親のワークツリーの状態を見て、操作を止める条件（関門）。

見るのは 2 つ。git を呼ぶのはここだけ（例外は `tests/core/test_gate_single_source.py` の一覧）。

- 未コミット: 置き場（承認済みチケットと提案）の外の、追跡中のファイルの変更（`tree_dirty`）
- 未 push: 親のブランチが `origin/<ブランチ>` より進んでいるか（`unpushed`）

どの操作がどちらを見るかは `WATCHES` に宣言する。操作は `check(操作, …)` を呼ぶだけで、
条件を並べない。条件を足したら、見る操作を `WATCHES` に足す。

返すのは `Refusal`（種類・理由の文・次の一手）。表示（ターミナル・ボードの JSON・案内）は
その結果を言い換えるだけにする。置き場の判定は `ticket_places.is_ticket_place` を通す
（大文字小文字を畳む）。
"""

from __future__ import annotations

from dataclasses import dataclass

from ...infra import gitcmd, settings, tree
from ..model import ticket_places
from ..sync import mirror_gap

_TIMEOUT = 20.0
_SHOWN = 8
_SHOWN_UNPUSHED = 3

# 操作。見るものは `WATCHES`。
APPROVAL_REQUEST = "approval-request"
FINISH = "finish"
REVIEW_REQUEST = "review-request"
READY = "ready"

# 拒否の種類。`--verify` の `reason` にもこの値が出る。
DIRTY_PARENT = "dirty_parent"
UNPUSHED_CODE = "unpushed_code"


@dataclass(frozen=True)
class Watch:
    """1 つの操作が見るもの。

    `ignore_places` は、未 push を数えるときに置き場の外のパスだけを数えるかどうか。
    `strict` は、読めないとき（git が答えない・比べる `origin/<ブランチ>` が無い）に止めるかどうか。
    止めない操作は、まだ一度も送っていない親のブランチでも通る。
    """

    wip_removals: bool = False
    unpushed: bool = False
    ignore_places: bool = False
    strict: bool = True


WATCHES: dict[str, Watch] = {
    # 親のブランチを push するので、設計途中のコードまで公開しない。
    APPROVAL_REQUEST: Watch(unpushed=True, ignore_places=True, strict=False),
    # 子の finish の合流前に、親の側を見る。未 push は合流の後の push で届くので見ない。
    FINISH: Watch(),
    # ユーザが見るのはコードなので、未 push は置き場を除いて数える。
    # 置き場の push は別の仕事（`ccnavi-user-push-approved.sh`）。
    REVIEW_REQUEST: Watch(unpushed=True, ignore_places=True),
    # ユーザがリモートを見てマージする。マーカーも届いていないと他の機械へ渡らない。
    # `ready` が消した `wip/` のファイルは write-and-push がコミットするので止めない。
    READY: Watch(wip_removals=True, unpushed=True),
}


@dataclass(frozen=True)
class Refusal:
    """操作を止める理由 1 件。

    `reason` は理由の文、`hints` は次の一手（無いこともある）、`paths` は理由になったパス
    （未 push で置き場を数えないときは sha）。
    """

    kind: str
    reason: str
    hints: tuple[str, ...] = ()
    paths: tuple[str, ...] = ()

    @property
    def line(self) -> str:
        """理由と次の一手を 1 つにつないだ文。"""
        return self.reason + "".join(self.hints)


def in_place(conf: settings.Settings, rel: str) -> bool:
    """ツリーのルートからの相対パスが、ccnavi 自身の置き場（承認済みチケットと提案）の下か。"""
    return ticket_places.is_ticket_place(
        rel,
        conf.tickets or settings.DEFAULT_TICKETS,
        conf.approved or settings.DEFAULT_APPROVED,
    )


def tree_dirty(
    tree_root: str, conf: settings.Settings, *, allow_wip_removals: bool = False
) -> list[str] | None:
    """置き場の外の未コミットの変更のパス。読めなければ None。

    承認済みチケットとマーカーはこのワークツリーの `.ccnavi/` に置かれ、git が追跡する。
    マーカーはフェーズの終わりに hook が書くので、数えると「マーカーをコミットしろ」と
    言い続けることになる。未追跡は数えない（依頼文をワークツリーに置く使い方が普通にあり、
    未追跡はマージリクエストにも載らない）。

    `-z` で読む。既定の出力は非 ASCII を引用符で囲んで 8 進でエスケープするので、置き場の中の
    日本語のファイルが置き場の外と判定される。`--no-renames` は、改名のときに出る 2 つめの
    パス（移動元）が XY を持たない形で入り込むのを避けるため。

    `allow_wip_removals` なら、`ready` が消した（追跡済みの）`wip/` のファイルは数えない。
    write-and-push がコミットするか、write-and-push の外では sh がコミットと push を求めて止める。
    """
    done = gitcmd.run(
        tree_root,
        ["status", "--porcelain", "-z", "--untracked-files=no", "--no-renames"],
        _TIMEOUT,
        raw_paths=True,
    )
    if not done.ok:
        return None
    paths: list[str] = []
    for entry in done.out.split("\0"):
        if len(entry) <= 3 or entry[2] != " ":
            continue
        path = entry[3:]
        if in_place(conf, path):
            continue
        if allow_wip_removals and "D" in entry[:2] and ticket_places.in_wip(path):
            continue
        paths.append(path)
    return paths


def diff_paths(tree_root: str, ref: str) -> tuple[list[str], str]:
    """`ref..HEAD` で変わったパス（置き場で絞らない）。2 つめは読めなかった理由。

    NUL 区切りで読む理由は `tree_dirty` と同じ。`--no-renames` を付けるのは、改名を 1 行に
    まとめられると移動元のパスが出力に出ず、置き場の外から中へ動かしたファイルが
    「置き場の中だけ」に見えるため。`--ignore-submodules=none` は、`.gitmodules` の
    `ignore = all` で submodule の進みが差分にまったく出なくなるのを防ぐ。

    `-z` が返すパスはもう正規化されているので、こちらでは何も直さない。`\\` を `/` に直すと、
    `.ccnavi\\tickets\\x.py` という名前のファイル 1 個が置き場の中のパスになってしまう。
    """
    if not ref:
        return [], "比べる相手がありません。"
    done = gitcmd.run(
        tree_root,
        ["diff", "--name-only", "--no-renames", "--ignore-submodules=none", "-z", f"{ref}..HEAD"],
        _TIMEOUT,
        raw_paths=True,
    )
    if not done.ok:
        return [], f"{ref[:12]} からの差分を読めない"
    return [path for path in done.out.split("\0") if path], ""


def unpushed(
    tree_root: str, conf: settings.Settings, branch: str, *, ignore_places: bool
) -> list[str] | None:
    """`origin/<branch>` より進んでいる分。進んでいなければ空。読めなければ None。

    `ignore_places` なら、進んでいる分が変えたパスのうち置き場の外のものを返す
    （置き場だけが手元に残っている形は、届いていると数える）。そうでなければ、進んでいる
    コミットの sha を返す。
    """
    done = gitcmd.run(tree_root, ["rev-list", f"origin/{branch}..HEAD"], _TIMEOUT)
    if not done.ok:
        return None
    ahead = done.out.split()
    if not ahead or not ignore_places:
        return ahead
    changed, failed = diff_paths(tree_root, f"origin/{branch}")
    if failed:
        return None
    return [path for path in changed if not in_place(conf, path)]


def check(
    operation: str,
    tree_root: str,
    conf: settings.Settings,
    branch: str | None = None,
    *,
    root: str = "",
    mirror_guidance: bool = True,
) -> list[Refusal]:
    """この操作を止める理由。止めなくてよければ空。

    `branch` は親のブランチ名。無ければワークツリーの今のブランチを読む。`root` はワークスペースの
    ルートで、承認の依頼の文に載せるコマンドのパスに使う。`mirror_guidance` が偽なら、
    ミラーのずれの案内に分けず、ふつうの変更として言う（ワークスペース自身の親の `finish`）。
    """
    watch = WATCHES[operation]
    found: list[Refusal] = []
    paths = tree_dirty(tree_root, conf, allow_wip_removals=watch.wip_removals)
    if paths is None:
        if watch.strict:
            tail = "。何も変えずに止めた" if operation == FINISH else ""
            found.append(Refusal(DIRTY_PARENT, f"親のワークツリーの状態を読めない{tail}"))
    elif paths:
        found.append(
            dirty_refusal(operation, tree_root, conf, paths, root=root, mirror=mirror_guidance)
        )
    if watch.unpushed:
        found += _unpushed_refusal(operation, watch, tree_root, conf, branch)
    return found


def dirty_refusal(
    operation: str,
    tree_root: str,
    conf: settings.Settings,
    paths: list[str],
    *,
    root: str = "",
    mirror: bool = True,
) -> Refusal:
    """未コミットの変更があるために止めるときの文。`paths` は `tree_dirty` の答え。

    変更がミラーのコピー先（共通レイヤー・工程スキル）にあるなら、作業の変更ではなく、
    共通レイヤーとのずれである。承認の範囲を超えるコミットは促さず、揃える手順（ユーザの操作）を
    案内する。
    """
    if operation == APPROVAL_REQUEST:
        return _approval_dirty(tree_root, paths, root)
    mirrored, others = mirror_gap.split(conf, paths) if mirror else ([], paths)
    lead = "親のワークツリーに未コミットの変更がある"
    stop = operation == FINISH
    if mirrored:
        reason = f"{lead}（{mirror_gap.label(mirrored)}）。{mirror_gap.NOT_WORK}"
        reason += mirror_gap.others_note(others)
    else:
        reason = f"{lead}（{_shown(paths)}）。"
    if stop and others:
        reason += "サブエージェントの作業がコミットされていません。"
    if stop:
        reason += "何も変えずに止めた"
    hints: list[str] = []
    if stop and others:
        restore = (
            "親のワークツリー（メインの作業場）には、状態の置き場以外の変更を残さない。"
            "子のワークツリーへ移してコミットするか、親のワークツリーで "
            "ccnavi-git.sh restore --staged --worktree -- <パス>（1 つずつ名指し）を打って"
            "変更を取り除いてから、finish をやり直す"
        )
        hints.append(restore + ("（ミラーのずれは対象外）" if mirrored else ""))
    if mirrored:
        hints.append(mirror_gap.ASK)
    return Refusal(DIRTY_PARENT, reason, tuple(hints), tuple(paths))


def _approval_dirty(tree_root: str, paths: list[str], root: str) -> Refusal:
    """承認を頼むときと、承認の結果を知らせるときの文。承認のあとは、親のワークツリーへの書き込みが断られる。"""
    name = _name_of(tree_root)
    git_sh = settings.script_command(root, "ccnavi-git.sh") if root else "ccnavi-git.sh"
    restore = (
        f"cd {settings.shell_path(tree_root)} && "
        f"{git_sh} restore --staged --worktree -- {settings.shell_path(paths[0])}"
    )
    reason = (
        f"親 {name} のワークツリーに、提案以外の未コミットの変更がある（{_shown(paths)}）。"
        "承認の後は、親のワークツリーへの書き込みが断られ、この変更があると子の finish も止まる。"
        "自分が書いた変更でなければ、戻す前にユーザへ確かめる（戻すと取り消せない）。"
        "残す変更は、子のワークツリーへ移してコミットする。"
        f"要らない変更は、パスを 1 つずつ名指しして戻す。例: {restore}"
    )
    hint = "親のワークツリーの変更を片付けて、approval-request を打ち直す（何も送っていません）"
    return Refusal(DIRTY_PARENT, reason, (hint,), tuple(paths))


def _unpushed_refusal(
    operation: str, watch: Watch, tree_root: str, conf: settings.Settings, branch: str | None
) -> list[Refusal]:
    name = branch or tree.branch_of(tree_root) or ""
    if not name:
        if not watch.strict:
            return []
        return [Refusal(UNPUSHED_CODE, "親ブランチの名前を読めない")]
    ahead = unpushed(tree_root, conf, name, ignore_places=watch.ignore_places)
    if ahead is None and not watch.strict:
        return []
    if ahead == []:
        return []
    if operation == APPROVAL_REQUEST:
        return [_approval_unpushed(ahead or [])]
    return [
        Refusal(UNPUSHED_CODE, "親ブランチの HEAD が push されていない", (), tuple(ahead or []))
    ]


def _approval_unpushed(paths: list[str]) -> Refusal:
    shown = "、".join(paths[:_SHOWN_UNPUSHED])
    reason = (
        f"親のブランチに未 push のコードの変更が{len(paths)}件ある（{shown}など）。"
        "approval-request は提案だけを送るので、止めた。"
    )
    hint = (
        "コードは先に送るか、手元の変更を整理してから approval-request を打ち直す"
        "（何も送っていません）"
    )
    return Refusal(UNPUSHED_CODE, reason, (hint,), tuple(paths))


def _name_of(tree_root: str) -> str:
    """ワークツリーの名前（親の識別子）。"""
    return tree_root.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


def _shown(paths: list[str]) -> str:
    more = f" ほか {len(paths) - _SHOWN} 件" if len(paths) > _SHOWN else ""
    return ", ".join(paths[:_SHOWN]) + more
