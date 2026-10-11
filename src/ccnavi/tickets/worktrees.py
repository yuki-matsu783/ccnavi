"""ワークツリーの片付け。`ccnavi worktree drop <名前>` と `ccnavi worktree tidy <親>`。

`tidy` は `--phase <N>` でそのフェーズの子に絞れる。

閉じた子と、Draft を外した親のワークツリーを ccnavi が消す。`tidy` が拾うのは閉じた
（`done/` の）子だけで、完了でも取り消しでも消す（`tidy_targets`）。閉じた子の続きは、
新しいチケットで新しいワークツリーを切る。残るのは作業中（`doing/`）の子と、レビュー待ち
（`review/`）の子（指摘を直す場所。`confirm` が `done/` へ動かした後に消える）のもの。
`decide`・`chat`・Chrome のレビュー済みで `done/` へ動いた子のものは、その場では消さず、
次にその親子で `finish` か `cancel` が打たれるまで残る。判断の要らない片付けを、エージェントへの
案内（`ccnavi-clean.sh` → `worktree remove`）で済ませず、ccnavi の側に移す。呼ぶのは sh で、
状態を書く操作が済んでから呼ぶ。

- `confirm` が子を `done/` へ動かした後（`ccnavi-review.sh confirm`）: そのフェーズの子（延期を
  引き受けた分を含む）のワークツリー
- `finish`・`cancel` の後（`ccnavi-ticket.sh`）: その親子の閉じた（`done/` の）子の
  ワークツリーを全部。子の finish・cancel でも、その子に限らず同じ親子の閉じた子を
  まとめて片付ける。親の finish では取りこぼしを拾う備えになる
- `ready` が Draft を外した後（`ccnavi-review.sh ready`）: 親のワークツリー。外から
  `ready --parent <親>` で打てば消える

C1 の中では書いたものが push の失敗で戻ることがあるので、C1 が送り終えてから呼ぶ。

消し方は、生成物（`node_modules` など。`ccnavi-clean.js` と同じ名前）を先に消してから
`git worktree remove`（`--force` なし）。Windows で深い `node_modules` が `worktree remove` を
途中で止めるのを防ぐ。ブランチは消さない（コミットは残り、squash でマージするとローカルの
ブランチは `branch -d` で消えないので、ユーザが消す）。

消さないとき（何も消さずに理由を言う）:

- cwd が消す対象の中にある。消すと呼び手の居場所が無くなり、Windows ではロックにもなる。
  外へ出てから打つ 1 本（`ccnavi-clean.sh --worktree <名前>`）を出す
- 未コミットの変更（未追跡を含む）がある。別のセッションが作業している見込みがあるので、
  変更を名指しする
- git が無視しているファイル（生成物と、直下の `scratchpad/` を除く。.env など）がある。
  `worktree remove` はそれごと消して戻せないので、名指しする
- git のワークツリーとして登録されていない、リンクである、など。任意の場所を消す手段にしない

ネットワークには出ない。git はローカルの操作（`status`・`rev-parse`・`worktree remove`）だけ。
"""

from __future__ import annotations

import os
import shutil
import stat
from dataclasses import dataclass
from typing import TextIO

from ..infra import gitcmd, settings, tree
from . import approval, approval_checks, phase, ticket_ids, ticket_model

TIMEOUT_SECONDS = 30.0

# どこにあっても生成物と言える名前と、隣に package.json があるときだけ生成物と見る名前。
# `ccnavi-clean.js` の ALWAYS・BESIDE_PACKAGE_JSON と揃える。
ALWAYS = frozenset({"node_modules", ".venv", "__pycache__", ".pytest_cache"})
BESIDE_PACKAGE_JSON = frozenset({"out"})

# drop の結果。
REMOVED = "removed"
ABSENT = "absent"
CWD_INSIDE = "cwd"
DIRTY = "dirty"
REFUSED = "refused"
# git に無視されたファイル（生成物を除く）がある。`git worktree remove` は無視されたファイルごと
# 消すので、.env などを知らないうちに消さないよう止める（直下の scratchpad/ の下書きは消えてよい）。
IGNORED = "ignored"

# 未コミットの変更を名指しする行の上限。
_SHOWN = 10


@dataclass
class Dropped:
    """1 本のワークツリーを消した結果。`detail` は名指しする変更や、消せなかった理由。"""

    name: str
    status: str
    path: str
    detail: str = ""

    @property
    def removed(self) -> bool:
        return self.status in (REMOVED, ABSENT)


def classifier_note(root: str, allow: str) -> str:
    """自動モードの分類器に止められたときの道。`allow` は許可に足す 1 本の例。"""
    return (
        "Claude Code の自動モードの分類器に止められたら、迂回せずにユーザに許可を頼む。"
        "続けて許すなら、ユーザがホームの ~/.claude/settings.json の permissions.allow に、"
        f"この操作だけに絞った許可（例 `Bash({allow})`）を足す"
        "（分類器はプロジェクトの .claude/settings.json を読まない。"
        f"`{settings.script_command(root, 'ccnavi-git.sh')} *` のような広い許可は push まで"
        "分類器を通さなくなるので勧めない）"
    )


def retry_command(root: str, name: str) -> str:
    """外へ出てから打ち直す 1 本。"""
    return f"{settings.script_command(root, 'ccnavi-clean.sh')} --worktree {name}"


def drop(root: str, name: str, cwd: str) -> Dropped:
    """`.claude/worktrees/<名前>` を消す。消さないときは理由を持って返す（何も消さない）。"""
    path = tree.worktree_path(root, name)
    if not name or name in (".", "..") or any(c in name for c in ("/", "\\", ":")):
        return Dropped(
            name, REFUSED, path, "名前にパスは書けない（.claude/worktrees/ の直下の名前）"
        )
    if not os.path.lexists(path):
        return Dropped(name, ABSENT, path)
    if os.path.islink(path) or _is_junction(path):
        return Dropped(name, REFUSED, path, "リンクなので、リンクの先は消さない")
    if not os.path.isdir(path):
        return Dropped(name, REFUSED, path, "ディレクトリでない")
    if _inside(cwd, path):
        return Dropped(name, CWD_INSIDE, path)
    owner, why = _owner(path)
    if not owner:
        return Dropped(name, REFUSED, path, why)
    done = gitcmd.run(
        path,
        ["status", "--porcelain", "--untracked-files=all"],
        TIMEOUT_SECONDS,
        raw_paths=True,
    )
    if not done.ok:
        return Dropped(
            name, REFUSED, path, "git の状態を読めない。未コミットの変更を確かめられない"
        )
    changes = [line for line in done.out.splitlines() if line.strip()]
    if changes:
        shown = changes[:_SHOWN] + (
            [f"…ほか {len(changes) - _SHOWN} 件"] if len(changes) > _SHOWN else []
        )
        return Dropped(name, DIRTY, path, "\n".join(shown))
    ignored, why = _ignored(path)
    if why:
        return Dropped(name, REFUSED, path, why)
    if ignored:
        shown = ignored[:_SHOWN] + (
            [f"…ほか {len(ignored) - _SHOWN} 件"] if len(ignored) > _SHOWN else []
        )
        return Dropped(name, IGNORED, path, "\n".join(shown))
    # `git worktree remove` が通らない形は、生成物を消す前に止める（消したあとで通らないと、
    # 生成物だけが消えた半端なツリーが残る）。
    blocked = _remove_blocked(owner, path)
    if blocked:
        return Dropped(name, REFUSED, path, blocked)
    failed = _clean_generated(path)
    if failed:
        return Dropped(name, REFUSED, path, failed)
    done = gitcmd.run(owner, ["worktree", "remove", path], TIMEOUT_SECONDS)
    if not done.ok:
        said = (done.err or done.failure or "").strip().splitlines()
        return Dropped(
            name,
            REFUSED,
            path,
            f"git worktree remove が通らない（{said[0] if said else '理由不明'}）",
        )
    return Dropped(name, REMOVED, path)


def report(stdout: TextIO, root: str, d: Dropped) -> None:
    """drop の結果を言う。消さなかったときも標準出力へ（呼び手の操作そのものは成功している）。"""
    if d.status == REMOVED:
        stdout.write(f"ワークツリー {d.name} を消した（{d.path}。ブランチは残してある）\n")
    elif d.status == ABSENT:
        stdout.write(f"ワークツリー {d.name} は無い（消すものが無い）\n")
    elif d.status == CWD_INSIDE:
        stdout.write(
            f"cwd がワークツリー {d.name} の中にあるので消さなかった。"
            "外（ワークスペースルートなど。エージェントは ExitWorktree か cd で出る）に出てから、"
            "次を打つ:\n"
            f"  {retry_command(root, d.name)}\n"
            f"  {classifier_note(root, retry_command(root, d.name))}\n"
        )
    elif d.status == DIRTY:
        stdout.write(
            f"ワークツリー {d.name} に未コミットの変更があるので、何も消さなかった"
            "（別のセッションが作業している見込みがある）。ユーザに確かめてもらう:\n"
        )
        for line in d.detail.splitlines():
            stdout.write(f"  {line}\n")
    elif d.status == IGNORED:
        stdout.write(
            f"ワークツリー {d.name} に git が無視しているファイルがあるので、何も消さなかった"
            "（worktree remove は無視されたファイルごと消し、履歴から戻せない）:\n"
        )
        for line in d.detail.splitlines():
            stdout.write(f"  {line}\n")
        stdout.write(
            "  残したいならワークツリーの外へ退避し、要らないなら手で消してから、"
            f"'{retry_command(root, d.name)}' で打ち直す\n"
        )
    else:
        stdout.write(f"ワークツリー {d.name} を消さなかった: {d.detail}\n")


def run_drop(stdout: TextIO, stderr: TextIO, root: str, name: str, cwd: str) -> int:
    """`worktree drop <名前>`。消したか、もともと無ければ 0。消さなかったら 1。"""
    d = drop(root, name, cwd)
    report(stdout, root, d)
    return 0 if d.removed else 1


def tidy_targets(
    root: str, conf: settings.Settings, family: str, phase_no: int | None = None
) -> list[ticket_model.Ticket]:
    """片付けの候補。閉じた（`done/` の。完了も取り消しも）子のうち、`phase_no` があればその
    フェーズ（延期を引き受けた分を含む）の子。作業中・レビュー待ちの子は入らない。"""
    closed, _ = approval.scan(conf, root, closed=True)
    children = approval_checks.children_of(closed, family)
    if phase_no is not None:
        numbers = {phase_no}
        for ph in phase.phases_of(root, conf, family):
            if ph.number == phase_no:
                numbers |= set(ph.covers)
        children = [t for t in children if t.phase in numbers]
    seen: set[str] = set()
    out = []
    for t in sorted(children, key=lambda t: t.ticket):
        if t.ticket in seen:
            continue
        seen.add(t.ticket)
        out.append(t)
    return out


def family_for(root: str, conf: settings.Settings, ident: str) -> str:
    """識別子が属する親子の親。

    チケットが見つかれば、その `parent:`（親なら自身）を採る。親の識別子が子の形
    （`<x>-<2 桁>-<2 桁>`。`rel-2026-10-06` など。lint は warn にとどめる）で終わっていても、
    名前の形から末尾を剥がして別の親と読み違えないため。見つからないときだけ名前の形で割り出す。
    """
    found = approval.scan(conf, root)[0] + approval.scan(conf, root, closed=True)[0]
    found += approval.scan_review(conf, root)[0]
    for t in found:
        if t.ticket == ident:
            return t.parent or t.ticket
    matched = ticket_ids.child_pattern().match(ident)
    return matched.group("parent") if matched else ident


def run_tidy(
    stdout: TextIO,
    stderr: TextIO,
    root: str,
    conf: settings.Settings,
    family: str,
    phase_no: int | None,
    cwd: str,
) -> int:
    """`worktree tidy <親> [--phase <N>]`。閉じた（完了・取り消し）子のワークツリーを消す。

    消さなかったもの（cwd・未コミット・登録が無いなど）が 1 本でもあれば 1。何も無ければ黙って 0。
    """
    code = 0
    family = family_for(root, conf, family)
    for t in tidy_targets(root, conf, family, phase_no):
        path = tree.worktree_path(root, t.ticket)
        if not os.path.lexists(path):
            continue
        d = drop(root, t.ticket, cwd)
        report(stdout, root, d)
        if not d.removed:
            code = 1
    return code


def _ignored(top: str) -> tuple[list[str], str]:
    """git が無視しているもの（生成物を除く）。2 つめは読めなかった理由。

    `--directory` で、全体が無視されたディレクトリは 1 行（末尾の `/`）にまとめる。生成物
    （`ccnavi-clean.js` と同じ名前。`out` は package.json の隣だけ）は消してよいので数えない。
    ワークツリーの直下の `scratchpad/`（下書きと使い捨ての置き場。docs/claude/scratchpad.md）も、
    ワークツリーごと消えてよい置き場なので数えない。直下でないもの（`docs/scratchpad/`）と、
    大文字小文字の違うもの（`SCRATCHPAD/`）は数える。
    """
    done = gitcmd.run(
        top,
        ["ls-files", "-z", "--others", "--ignored", "--exclude-standard", "--directory"],
        TIMEOUT_SECONDS,
        raw_paths=True,
    )
    if not done.ok:
        return [], "git が無視しているファイルを読めない。消してよいかを確かめられない"
    return [
        p for p in done.out.split("\0") if p and not _is_generated(top, p) and not _in_scratchpad(p)
    ], ""


# ワークツリーの直下の下書きの置き場。中身はワークツリーごと消えてよい。
SCRATCHPAD = "scratchpad"


def _in_scratchpad(rel: str) -> bool:
    """ワークツリーのルートからの相対 `rel` が、直下の `scratchpad/` の下か。

    大文字小文字は区別する（scratchpad.md の照らし合わせの外し方と同じ）。
    """
    return rel == SCRATCHPAD + "/" or rel.startswith(SCRATCHPAD + "/")


def _is_generated(top: str, rel: str) -> bool:
    """ワークツリーのルートからの相対 `rel` が、生成物（とその中）か。"""
    parts = [p for p in rel.rstrip("/").split("/") if p]
    for i, part in enumerate(parts):
        if part in ALWAYS:
            return True
        if part in BESIDE_PACKAGE_JSON and os.path.isfile(
            os.path.join(top, *parts[:i], "package.json")
        ):
            return True
    return False


def _remove_blocked(owner: str, path: str) -> str:
    """`git worktree remove`（--force なし）が通らないと分かっている理由。無ければ空。

    ロックされたワークツリーと、submodule を持つワークツリー（git が消さない）。
    """
    done = gitcmd.run(owner, ["worktree", "list", "--porcelain"], TIMEOUT_SECONDS, raw_paths=True)
    if not done.ok:
        return "git のワークツリーの一覧を読めない。消せるかを確かめられない"
    want = _canonical(path)
    for block in done.out.split("\n\n"):
        lines = block.splitlines()
        if not lines or not lines[0].startswith("worktree "):
            continue
        if _canonical(lines[0][len("worktree ") :]) != want:
            continue
        if any(line == "locked" or line.startswith("locked ") for line in lines[1:]):
            return "ロックされている（git worktree lock）。ロックした人に確かめ、外してから打ち直す"
    if os.path.lexists(os.path.join(path, ".gitmodules")):
        return "submodule を持つワークツリーは git worktree remove（--force なし）で消せない"
    return ""


def _owner(path: str) -> tuple[str, str]:
    """ワークツリーの元リポジトリ（`worktree remove` を打つ場所）。無ければ ("", 理由)。

    `.git` がファイル（リンクされたワークツリー）であることを求める。ディレクトリなら
    それ自身がリポジトリで、ワークツリーとして消す対象ではない。
    """
    if not os.path.isfile(os.path.join(path, ".git")):
        return "", "git のワークツリーとして登録されていない（.git のファイルが無い）ので消さない"
    done = gitcmd.run(path, ["rev-parse", "--git-common-dir"], TIMEOUT_SECONDS)
    common = done.out.strip() if done.ok else ""
    if not common:
        return "", "元のリポジトリを読めない（git rev-parse --git-common-dir が通らない）"
    if not os.path.isabs(common):
        common = os.path.join(path, common)
    common = os.path.normpath(common)
    if os.path.basename(common) != ".git":
        return "", f"元のリポジトリの形が読めない（{common}）"
    return os.path.dirname(common), ""


def _inside(cwd: str, path: str) -> bool:
    if not cwd:
        return False
    here = _canonical(cwd)
    top = _canonical(path)
    return here == top or here.startswith(top + os.sep)


def _canonical(path: str) -> str:
    try:
        resolved = os.path.realpath(path)
    except OSError:
        resolved = os.path.abspath(path)
    return os.path.normcase(os.path.normpath(resolved))


def _is_junction(path: str) -> bool:
    check = getattr(os.path, "isjunction", None)
    return bool(check and check(path))


def _long(path: str) -> str:
    """Windows では 260 文字を超えるパスを扱える表記にする。ほかはそのまま。"""
    if os.name != "nt":
        return path
    full = os.path.abspath(path)
    if full.startswith("\\\\?\\"):
        return full
    if full.startswith("\\\\"):
        return "\\\\?\\UNC\\" + full[2:]
    return "\\\\?\\" + full


def generated(top: str) -> list[str]:
    """消す生成物（ワークツリーのルートからの相対。区切りは "/"）。リンクはたどらない。"""
    found: list[str] = []
    stack = [top]
    while stack:
        here = stack.pop()
        try:
            with os.scandir(here) as it:
                entries = list(it)
        except OSError:
            continue
        has_package = any(e.name == "package.json" and _plain_file(e) for e in entries)
        for e in entries:
            full = os.path.join(here, e.name)
            linked = e.is_symlink() or _is_junction(full)
            if e.name in ALWAYS or (has_package and e.name in BESIDE_PACKAGE_JSON):
                if linked or e.is_dir(follow_symlinks=False):
                    found.append(os.path.relpath(full, top).replace(os.sep, "/"))
                continue
            if e.name == ".git" or linked:
                continue
            if e.is_dir(follow_symlinks=False):
                stack.append(full)
    return sorted(found)


def _plain_file(e: os.DirEntry) -> bool:
    try:
        return e.is_file(follow_symlinks=False)
    except OSError:
        return False


def _clean_generated(top: str) -> str:
    """生成物を消す。消し残しがあれば理由。リンクはリンクそのものだけを消す。"""
    # 消すものを先に決める（途中で止まって半端に消さない）。生成物の名前でも、追跡されている
    # もの（コミット済みの out/ など）は消さない。消すと未コミットの削除になり、worktree remove が
    # 通らない。追跡済みのものは worktree remove が消す。
    targets = []
    for rel in generated(top):
        tracked = gitcmd.run(
            top, ["ls-files", "-z", "--", f":(literal){rel}"], TIMEOUT_SECONDS, raw_paths=True
        )
        if not tracked.ok:
            return f"{rel} が追跡されているかを読めない。何も消していない"
        if not tracked.out.strip("\0"):
            targets.append(rel)
    left = []
    for rel in targets:
        full = os.path.join(top, *rel.split("/"))
        try:
            if os.path.islink(full):
                os.unlink(full)
            elif _is_junction(full):
                os.rmdir(full)
            else:
                shutil.rmtree(_long(full), onexc=_writable_and_retry)
        except OSError as exc:
            left.append(f"{rel}（{exc.strerror or exc}）")
    if left:
        return (
            "生成物を消しきれなかった（"
            + "、".join(left)
            + "）。Windows では、読み込まれている DLL（uv の .venv の .pyd）は消せない。"
            "使っているプロセスが終わってから打ち直す"
        )
    return ""


def _writable_and_retry(func, path, _exc) -> None:
    """読み取り専用のファイル（Windows）を書けるようにしてやり直す。駄目なら投げる。"""
    os.chmod(path, stat.S_IWRITE)
    func(path)
