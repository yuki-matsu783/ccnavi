"""ワークツリーの片付け。`ccnavi worktree drop <名前>` と `ccnavi worktree tidy <親>`。

`tidy` は `--phase <N>` でそのフェーズの子に絞れる。

閉じた子と、Draft を外した親のワークツリーを ccnavi が消す。`tidy` が拾うのは閉じた
（`done/` の）子だけで、完了でも取り消しでも消す（`tidy_targets`）。閉じた子の続きは、
新しいチケットで新しいワークツリーを切る。残るのは作業中（`doing/`）の子と、レビュー待ち
（`review/`）の子（レビューの間に見返す場所。ここで積んだコミットは親へ合流しないので、
差し戻しは同じフェーズに子を足して直す。`confirm` が `done/` へ動かした後に消える）のもの。
`decide`・Chrome のレビュー済みで `done/` へ動いた子のものは、その場では消さず、
次にその親子で `finish` か `cancel` が打たれるまで残る。判断の要らない片付けを、エージェントへの
案内（`ccnavi-clean.sh` → `worktree remove`）で済ませず、ccnavi の側に移す。呼ぶのは sh で、
状態を書く操作が済んでから呼ぶ。

- `confirm` が子を `done/` へ動かした後（`ccnavi-review.sh confirm`）: そのフェーズの子（延期を
  引き受けた分を含む）のワークツリー
- `finish`・`cancel` の後（`ccnavi-ticket.sh`）: その親子の閉じた（`done/` の）子の
  ワークツリーを全部。子の finish・cancel でも、その子に限らず同じ親子の閉じた子を
  まとめて片付ける。親の finish では取りこぼしを拾う備えになる
- `ready` が Draft を外した後（`ccnavi-review.sh ready`）: 親のワークツリー。外から
  `ready --parent <親>` で打つ（`--parent` なしでは打てない）

write-and-push の中では書いたものが push の失敗で戻ることがあるので、write-and-push
が送り終えてから呼ぶ。

消し方は、生成物（`node_modules` など。生成物の名前）を先に消し、
git が無視しているそれ以外のもの（`.env` など）をワークスペースルートの
`scratchpad/worktrees/<名前>/` へ退避（同じボリュームの rename。中を走査しない。退避先が
git に無視されていないときと、同じパスが退避先にあるときは動かさず止める）してから
`git worktree remove`（`--force` なし）。Windows で深い `node_modules` が `worktree remove` を
途中で止めるのを防ぐ。退避したものは ccnavi が消さない（要らなければユーザが消す）。
親を閉じたときのブランチは
`tickets/lifecycle/retire.py` が片付ける（条件を確かめてから消す）。

消さないとき（何も消さずに理由を言う）:

- cwd が消す対象の中にある。消すと呼び手の居場所が無くなり、Windows ではロックにもなる。
  外へ出てから打つ 1 本（`ccnavi-clean.sh --worktree <名前>`）を出す
- 未コミットの変更（未追跡を含む）がある。別のセッションが作業している見込みがあるので、
  `git status` で確かめてもらう
- git のワークツリーとして登録されていない、リンクである、など。任意の場所を消す手段にしない
- 無視されたファイルを退避できない（使用中・退避先が別のボリュームなど）。理由を伝える。
  退避の途中で止まったときは、退避済みのものが退避先に残る
- `git worktree remove` が断る（ロック・submodule など）。git の拒否文をそのまま伝える。
  生成物はその前に消えているが、作り直せるので失うものは無い。退避したものは退避先に残る

ネットワークには出ない。git はローカルの操作（`status`・`rev-parse`・`worktree remove`）だけ。
"""

from __future__ import annotations

import os
import shlex
import shutil
import stat
from dataclasses import dataclass
from typing import TextIO

from ...infra import gitcmd, pathcase, settings, tree
from ...policy import texts
from ..approval import checks as approval_checks
from ..approval import store as approval
from ..model import ticket as ticket_mod
from ..model import ticket_ids, ticket_model
from ..phase import gates as phase

TIMEOUT_SECONDS = 30.0

# どこにあっても生成物と言える名前と、隣に印のファイルがあるときだけ生成物と見る名前。
ALWAYS = frozenset(
    {
        "node_modules",
        ".venv",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".tox",
        ".nox",
        ".next",
        ".nuxt",
        ".turbo",
        ".parcel-cache",
        ".gradle",
    }
)
BESIDE = {
    "out": ("package.json",),
    "dist": ("package.json",),
    ".cache": ("package.json",),
    "target": ("Cargo.toml", "pom.xml"),
    "build": ("build.gradle", "build.gradle.kts"),
}

# drop の結果。
REMOVED = "removed"
ABSENT = "absent"
CWD_INSIDE = "cwd"
DIRTY = "dirty"
REFUSED = "refused"
# 退避したファイルを名指しする行の上限。
_SHOWN = 10


@dataclass
class Dropped:
    """1 本のワークツリーを消した結果。`detail` は消せなかった理由や、無視されたファイル。"""

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
        "以後も許可するなら、ユーザがホームの ~/.claude/settings.json の permissions.allow に、"
        f"この操作だけに絞った許可（例 `Bash({allow})`）を追加します"
        "（分類器はプロジェクトの .claude/settings.json を読まない。"
        f"`{settings.script_command(root, 'ccnavi-git.sh')} *` のような広い許可は push まで"
        "分類器を通さなくなるので勧めない）"
    )


def retry_command(root: str, name: str) -> str:
    """外へ出てから打ち直す 1 本。"""
    return f"{settings.script_command(root, 'ccnavi-clean.sh')} --worktree {name}"


def _screen(root: str, name: str, cwd: str) -> tuple[Dropped | None, str]:
    """消してよいかを判定する（消さずに読むだけ）。

    止める理由があれば（もともと無い場合の ABSENT も含む）`Dropped` を、
    通れば (None, 持ち主のリポジトリ) を返す。`drop` のゲートで、`blocked` も同じものを使う。

    1. 名前にパスの区切りや `.`・`..` が無いかを見る。
    2. 無ければ ABSENT。リンクやジャンクションなら REFUSED（リンクの先は消さない）。
       ディレクトリでなければ REFUSED。
    3. cwd がその中なら CWD_INSIDE。
    4. `_owner` で持ち主のリポジトリを求める。
    5. `git status --porcelain` で未コミットの変更を見る。あれば DIRTY。無視された
       ファイルは止める理由にしない（`drop` が退避する）。
    """
    path = tree.worktree_path(root, name)
    if not name or name in (".", "..") or any(c in name for c in ("/", "\\", ":")):
        refusal = "名前にパスは書けない（.claude/worktrees/ の直下の名前）"
        return Dropped(name, REFUSED, path, refusal), ""
    if not os.path.lexists(path):
        return Dropped(name, ABSENT, path), ""
    if os.path.islink(path) or os.path.isjunction(path):
        return Dropped(name, REFUSED, path, "リンクなので、リンクの先は削除しません。"), ""
    if not os.path.isdir(path):
        return Dropped(name, REFUSED, path, "ディレクトリでない"), ""
    if _inside(cwd, path):
        return Dropped(name, CWD_INSIDE, path), ""
    owner, why = _owner(path)
    if not owner:
        return Dropped(name, REFUSED, path, why), ""
    done = gitcmd.run(
        path,
        ["status", "--porcelain", "--untracked-files=all"],
        TIMEOUT_SECONDS,
        raw_paths=True,
    )
    if not done.ok:
        unread = "git の状態を読めない。未コミットの変更を確かめられない"
        return Dropped(name, REFUSED, path, unread), ""
    if done.out.strip():
        return Dropped(name, DIRTY, path), ""
    return None, owner


def blocked(root: str, name: str, cwd: str) -> Dropped | None:
    """`drop` がこのワークツリーを消さない理由。消せる（もともと無いのも含む）なら None。

    何も変えずに読むだけ。消す前に断りたい呼び手（`retire`）が、`drop` と同じ判定を使う。
    """
    found, _ = _screen(root, name, cwd)
    return found if found is not None and not found.removed else None


def drop(root: str, name: str, cwd: str) -> Dropped:
    """`.claude/worktrees/<名前>` を消す。消さないときは理由を持って返す（何も消さない）。

    処理フロー:
    1. `_screen` で消してよいかを見る。止める理由があれば、それを返す。
    2. `_clean_generated` で生成物を消す。
    3. `_stash_ignored` で、git が無視しているそれ以外のものを退避する。
    4. `git worktree remove` で消す。git が断ったら（ロック・submodule など）、その拒否文で
       REFUSED。
    """
    found, owner = _screen(root, name, cwd)
    if found is not None:
        return found
    path = tree.worktree_path(root, name)
    failed = _clean_generated(path)
    if failed:
        return Dropped(name, REFUSED, path, failed)
    note, stuck = _stash_ignored(root, name, path)
    if stuck:
        return Dropped(name, REFUSED, path, stuck)
    done = gitcmd.run(owner, ["worktree", "remove", path], TIMEOUT_SECONDS)
    if not done.ok:
        said = (done.err or done.failure or "").strip().splitlines()
        return Dropped(
            name,
            REFUSED,
            path,
            f"git worktree remove が失敗した（{said[0] if said else '理由不明'}）"
            + (f"\n{note}" if note else ""),
        )
    return Dropped(name, REMOVED, path, note)


def report(stdout: TextIO, root: str, d: Dropped, after: str = "。ブランチは残してある") -> None:
    """drop の結果を言う。消さなかったときも標準出力へ（呼び手の操作そのものは成功している）。

    `after` は消した行の括弧の後ろに足す一言（ブランチをどうするか）。
    """
    if d.status == REMOVED:
        stdout.write(f"ワークツリー {d.name} を削除しました（{d.path}{after}）\n")
        if d.detail:
            stdout.write(f"{d.detail}\n")
    elif d.status == ABSENT:
        stdout.write(f"ワークツリー {d.name} はありません（削除するものがありません）\n")
    elif d.status == CWD_INSIDE:
        stdout.write(
            f"cwd がワークツリー {d.name} の中にあるので削除しませんでした。\n"
            + texts.say(
                root,
                "NEXT_OPS_DROP_OUTSIDE",
                {
                    "command": retry_command(root, d.name),
                    "classifier_note": classifier_note(root, retry_command(root, d.name)),
                },
            )
            + "\n"
        )
    elif d.status == DIRTY:
        stdout.write(
            f"ワークツリー {d.name} に未コミットの変更があるので、何も削除しませんでした。"
            "別のセッションが作業している見込みがあるので、"
            f"ユーザに git -C {shlex.quote(d.path)} status で確かめてもらう\n"
        )
    else:
        stdout.write(f"ワークツリー {d.name} を削除しなかった: {d.detail}\n")


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
    raw = approval.read_raw(conf, root)
    found = (
        approval.scan(conf, root, raw=raw)[0] + approval.scan(conf, root, closed=True, raw=raw)[0]
    )
    found += approval.scan_review(conf, root, raw=raw)[0]
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


@dataclass
class Left:
    """ready の後に残ったワークツリー 1 本。`why` は残った理由、`check` は消す前に確かめること。"""

    name: str
    path: str
    why: list[str]
    check: list[str]


def _leftover_facts(path: str) -> tuple[list[str], list[str]]:
    """残ったワークツリーが抱えているもの（理由と、確かめること）。何も読めなければ空。

    `drop` が DIRTY で止める条件と同じ。消さずに読むだけ。
    """
    why: list[str] = []
    check: list[str] = []
    done = gitcmd.run(
        path, ["status", "--porcelain", "--untracked-files=all"], TIMEOUT_SECONDS, raw_paths=True
    )
    changes = [line for line in done.out.splitlines() if line.strip()] if done.ok else []
    if changes:
        scratch = [c for c in changes if "wip/handoff/" in c.replace("\\", "/")]
        why.append("未保存の下書きがある（wip/handoff/）" if scratch else "未コミットの変更がある")
        check.append(
            f"`git -C {shlex.quote(path)} status` で中身を見て、必要なものはコミットするか退避する"
        )
    return why, check


def remaining(root: str, conf: settings.Settings, family: str, cwd: str = "") -> list[Left]:
    """ready の最後の `drop <親>` の後に、まだ残っている親子のワークツリー。見るだけで消さない。

    - 名前が親の識別子と違うが、その親の提案か承認済みチケットを持つワークツリー
      （親の作業を別の名前のワークツリーで進めた場合。`drop <親>` は名前で消すので拾えない）
    - 閉じた子のワークツリーで、未保存の下書きがあって `drop` が消さなかったもの
    """
    family = family_for(root, conf, family)
    open_all, _ = approval.scan_all(conf, root)
    closed_all, _ = approval.scan_all(conf, root, closed=True)
    review_all, _ = approval.review_all(conf, root)
    proposals, _ = ticket_mod.scan_all(root, conf.tickets, conf.projects)
    everything = open_all + closed_all + review_all + proposals
    mine = [t for t in everything if (t.parent or t.ticket) == family]
    members = {t.ticket for t in mine}
    holders: dict[str, str] = {}
    for t in mine:
        if t.tree and t.tree not in members:
            holders.setdefault(t.tree, t.tree_root)
    found: list[Left] = []
    for name, top in sorted(holders.items()):
        path = tree.worktree_path(root, name)
        if not os.path.isdir(path) or os.path.islink(path):
            continue
        branch = tree.branch_of(top)
        why = [f"名前が親 {family} と違うので、親の片付けの対象にならなかった"]
        check = [
            f"{('ブランチ ' + branch + ' の') if branch else ''}作業が push 済みで、"
            "ほかのセッションが使っていないことを確かめる"
        ]
        more_why, more_check = _leftover_facts(path)
        found.append(Left(name, path, why + more_why, check + more_check))
    for t in tidy_targets(root, conf, family):
        path = tree.worktree_path(root, t.ticket)
        if t.ticket == family or not os.path.isdir(path) or os.path.islink(path):
            continue
        if _owner(path)[0] == "":
            continue
        why, check = _leftover_facts(path)
        if why:
            found.append(Left(t.ticket, path, why, check))
    return found


def run_remaining(stdout: TextIO, root: str, conf: settings.Settings, family: str, cwd: str) -> int:
    """`worktree remaining <親>`。残りを一覧にする。出すだけで消さない。無ければ何も出さない。"""
    left = remaining(root, conf, family, cwd)
    if not left:
        return 0
    stdout.write(
        "残っているワークツリー（自動では削除していません。不要なら確かめてから削除します）:\n"
    )
    for item in left:
        stdout.write(f"- {item.name}（{item.path}）\n")
        stdout.write(f"  残った理由: {'。'.join(item.why)}\n")
        for line in item.check:
            stdout.write(f"  削除する前に確かめること: {line}\n")
        stdout.write(f"  削除するコマンド: {retry_command(root, item.name)}\n")
    return 0


def _ignored(top: str) -> tuple[list[str], str]:
    """git が無視しているもの（生成物を除く）。2 つめは読めなかった理由。

    `--directory` で、全体が無視されたディレクトリは 1 行（末尾の `/`）にまとめる。生成物
    （生成物の名前。`out`・`target` などは隣に印のファイルがあるときだけ）は
    消してよいので数えない。
    下書きの置き場（`wip/handoff/`）は追跡されるファイルで、gitが無視するものではないので、
    ここには現れない。
    """
    done = gitcmd.run(
        top,
        ["ls-files", "-z", "--others", "--ignored", "--exclude-standard", "--directory"],
        TIMEOUT_SECONDS,
        raw_paths=True,
    )
    if not done.ok:
        return [], "git が無視しているファイルを読めない。削除してよいかを確かめられない"
    return [p for p in done.out.split("\0") if p and not _is_generated(top, p)], ""


def _stash_ignored(root: str, name: str, top: str) -> tuple[str, str]:
    """生成物でない無視されたもの（`.env` など）を、`<ルート>/scratchpad/worktrees/<名前>/` へ移す。

    (退避したことを言う文, 止まった理由)。何も無ければ ("", "")。移すのは最上位の項目ごとの
    rename で、中身を走査しない（同じボリュームなら一瞬。深い node_modules でも 260 文字の
    制限に掛からない）。退避先に同じパスが既にあれば上書きせず止める。退避先が git に無視されて
    いなければ（.env がコミットされうる）、何も動かさず止める。移せなければ、推測や再試行を
    せず、理由と退避済みのものを言って止める。
    """
    ignored, why = _ignored(top)
    if why:
        return "", why
    if not ignored:
        return "", ""
    dest = os.path.join(root, "scratchpad", "worktrees", name)
    shown_dest = f"scratchpad/worktrees/{name}"
    checked = gitcmd.run(root, ["check-ignore", "-q", "--", shown_dest], TIMEOUT_SECONDS)
    if not checked.ok:
        return "", (
            f"退避先 {shown_dest} が git に無視されていないので、無視されたファイルを退避しません。"
            "ワークスペースルートの .gitignore に `/scratchpad` を足してから再実行します。"
        )
    moved: list[str] = []
    for rel in ignored:
        parts = [p for p in rel.rstrip("/").split("/") if p]
        target = os.path.join(dest, *parts)
        try:
            if os.path.lexists(_long(target)):
                raise FileExistsError(0, "退避先に同じパスがある", target)
            os.makedirs(_long(os.path.dirname(target)), exist_ok=True)
            os.replace(_long(os.path.join(top, *parts)), _long(target))
        except OSError as exc:
            done = f"（退避済み: {', '.join(moved)}）" if moved else ""
            return "", (
                f"無視されたファイル {rel} を {shown_dest} へ退避できませんでした"
                f"（{exc.strerror or exc}）{done}。原因を直してから再実行します。"
            )
        moved.append(rel)
    shown = moved[:_SHOWN] + ([f"…ほか {len(moved) - _SHOWN} 件"] if len(moved) > _SHOWN else [])
    lines = [f"git が無視しているファイルを {shown_dest} へ退避しました（不要ならユーザが消す）:"]
    lines += [f"  {line}" for line in shown]
    return "\n".join(lines), ""


def _is_generated(top: str, rel: str) -> bool:
    """ワークツリーのルートからの相対 `rel` が、生成物（とその中）か。"""
    parts = [p for p in rel.rstrip("/").split("/") if p]
    for i, part in enumerate(parts):
        if part in ALWAYS:
            return True
        if part in BESIDE and any(
            os.path.isfile(os.path.join(top, *parts[:i], mark)) for mark in BESIDE[part]
        ):
            return True
    return False


def _owner(path: str) -> tuple[str, str]:
    """ワークツリーの元リポジトリ（`worktree remove` を打つ場所）。無ければ ("", 理由)。

    `.git` がファイル（リンクされたワークツリー）であることを求める。ディレクトリなら
    それ自身がリポジトリで、ワークツリーとして消す対象ではない。
    """
    if not os.path.isfile(os.path.join(path, ".git")):
        return (
            "",
            "git のワークツリーとして登録されていない（.git のファイルがありません）"
            "ので削除しません。",
        )
    done = gitcmd.run(path, ["rev-parse", "--git-common-dir"], TIMEOUT_SECONDS)
    common = done.out.strip() if done.ok else ""
    if not common:
        return "", "元のリポジトリを読めない（git rev-parse --git-common-dir が失敗した）"
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
    return pathcase.fold(os.path.normpath(resolved))


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
        plain = {e.name for e in entries if _plain_file(e)}
        for e in entries:
            full = os.path.join(here, e.name)
            linked = e.is_symlink() or os.path.isjunction(full)
            if e.name in ALWAYS or any(mark in plain for mark in BESIDE.get(e.name, ())):
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


def _targets(top: str, check_tracked: bool = True) -> tuple[list[str], str]:
    """消す生成物（ワークツリーのルートからの相対）。2 つめは読めなかった理由。

    生成物の名前でも、追跡されているもの（コミット済みの out/ など）は消さない。消すと未コミットの
    削除になり、worktree remove が通らない。追跡済みのものは worktree remove が消す。
    `check_tracked` が偽なのは、git の登録が残っていないディレクトリ（聞くと上へ登って別の
    リポジトリを答える）。
    """
    targets = []
    for rel in generated(top):
        if check_tracked:
            tracked = gitcmd.run(
                top, ["ls-files", "-z", "--", f":(literal){rel}"], TIMEOUT_SECONDS, raw_paths=True
            )
            if not tracked.ok:
                return [], f"{rel} が追跡されているかを読めない。何も削除していません。"
            if tracked.out.strip("\0"):
                continue
        targets.append(rel)
    return targets, ""


def _remove(top: str, targets: list[str]) -> tuple[list[str], list[str]]:
    """`targets` を消す。(消せたもの, 消せなかったもの（理由つき）)。リンクはリンクだけを消す。"""
    removed: list[str] = []
    left: list[str] = []
    for rel in targets:
        full = os.path.join(top, *rel.split("/"))
        try:
            if os.path.islink(full):
                os.unlink(full)
            elif os.path.isjunction(full):
                os.rmdir(full)
            else:
                shutil.rmtree(_long(full), onexc=_writable_and_retry)
            removed.append(rel)
        except OSError as exc:
            left.append(f"{rel}（{exc.strerror or exc}）")
    return removed, left


def _left_message(left: list[str]) -> str:
    if not left:
        return ""
    return (
        "生成物を削除しきれませんでした（"
        + "、".join(left)
        + "）。Windows では、読み込まれている DLL（uv の .venv の .pyd）は削除できません。"
        "使っているプロセスが終わってから再実行します。"
    )


def _clean_generated(top: str) -> str:
    """生成物を消す。消し残しがあれば理由。"""
    # 消すものを先に決める（途中で止まって半端に消さない）。
    targets, why = _targets(top)
    if why:
        return why
    return _left_message(_remove(top, targets)[1])


def run_clean(stdout: TextIO, stderr: TextIO, root: str, name: str, preview: bool) -> int:
    """`worktree clean <名前> [--preview]`。生成物だけを消す（ワークツリーは消さない）。

    `git worktree remove` の前に打つ（`ccnavi-clean.sh <名前>`）。未コミットの変更があれば何も
    消さない（別のセッションが作業している見込みが高い）。git の登録が残っていないディレクトリ
    （消しきれなかったもの）は、確かめようが無いので確かめずに進める。成功で 0、止めたら 1。
    """
    top = tree.worktree_path(root, name)
    if not name or name in (".", "..") or any(c in name for c in ("/", "\\", ":")):
        stderr.write("ccnavi-clean: 名前にパスは書けません（.claude/worktrees/ の直下の名前）。\n")
        return 1
    if os.path.islink(top) or os.path.isjunction(top) or not os.path.isdir(top):
        stderr.write(f"ccnavi-clean: {top} がディレクトリでないか、リンクです。\n")
        return 1
    registered = os.path.lexists(os.path.join(top, ".git"))
    if registered:
        done = gitcmd.run(
            top, ["status", "--porcelain", "--untracked-files=all"], TIMEOUT_SECONDS, raw_paths=True
        )
        if done.ok:
            if done.out.strip():
                stderr.write(
                    f"ccnavi-clean: {name} に未コミットの変更があるので、何も消しません。"
                    "別のセッションが作業している可能性が高いです。\n"
                )
                for line in done.out.splitlines()[:10]:
                    stderr.write(f"{line}\n")
                return 1
        else:
            registered = False
            stderr.write(
                f"ccnavi-clean: {name} は git の登録が残っていないので、削除しきれなかった"
                "ディレクトリとして扱います（未コミットの変更は確かめていません）。\n"
            )
    targets, why = _targets(top, registered)
    if why:
        stderr.write(f"ccnavi-clean: {why}\n")
        return 1
    if not targets:
        stdout.write(f"削除するものはありません（{top}）\n")
        return 0
    if preview:
        for rel in targets:
            stdout.write(f"would remove {rel}\n")
        return 0
    removed, left = _remove(top, targets)
    for rel in removed:
        stdout.write(f"removed {rel}\n")
    if left:
        stderr.write(f"ccnavi-clean: {_left_message(left)}\n")
        return 1
    return 0


def _writable_and_retry(func, path, _exc) -> None:
    """読み取り専用のファイル（Windows）を書けるようにしてやり直す。駄目なら投げる。"""
    os.chmod(path, stat.S_IWRITE)
    func(path)


# 承認した子のワークツリーの状態（`child_worktrees` の `state`）。拡張は `create` だけを実行する。
CHILD_CREATE = "create"  # 親のワークツリーが在り、子のワークツリーはまだ無い。承認の後に作る
CHILD_EXISTS = "exists"  # 子のワークツリーが既に在る。何もしない
CHILD_NO_PARENT = "no-parent"  # 親のワークツリーが無い（親を先に用意する）。作れない
CHILD_PROJECT = "project"  # プロジェクトの子。置き場の決まりが別なので、承認の後には作らない


def child_worktrees(root: str, tickets: list[ticket_mod.Ticket]) -> list[dict[str, str]]:
    """承認した子それぞれの、ワークツリーの計画（承認のJSON `worktrees` に載せる）。

    `create` の子は、拡張が `ccnavi-git.sh worktree add <path> -b <子> <base>` で作る。
    base は親のワークツリーが今チェックアウトしているブランチ。親の `branch:` を読まずに済み、
    親が `feature/123-login` のように `/` を含むときも、そのブランチから切れる。
    ワークツリーの作成は git の局所の操作だけで、ネットワークには出ない（exe-boundary）。
    """
    out = []
    for t in tickets:
        if not t.is_child:
            continue
        entry = {
            "ticket": t.ticket,
            "parent": t.parent,
            "path": _rel(t.ticket),
            "branch": t.ticket,
            "base": "",
            "state": "",
        }
        if t.project:
            entry["state"] = CHILD_PROJECT
        elif os.path.exists(tree.worktree_path(root, t.ticket)):
            entry["state"] = CHILD_EXISTS
        else:
            base = _checked_out(root, tree.worktree_path(root, t.parent))
            if base:
                entry["base"] = base
                entry["state"] = CHILD_CREATE
            else:
                entry["state"] = CHILD_NO_PARENT
        out.append(entry)
    return out


def _rel(name: str) -> str:
    """ワークスペースルートからの相対のパス。区切りは `/`（文面と JSON で揃える）。"""
    return (tree.WORKTREES_DIR + os.sep + name).replace(os.sep, "/")


def _checked_out(root: str, path: str) -> str:
    """親のワークツリー（`path`）が今チェックアウトしているブランチ名。無い・読めないなら空。"""
    if not os.path.isdir(path) or not tree.is_worktree_of(root, path):
        return ""
    rc, out = gitcmd.output(path, ["rev-parse", "--abbrev-ref", "HEAD"], TIMEOUT_SECONDS)
    name = out.strip()
    return "" if rc != 0 or name in ("", "HEAD") else name
