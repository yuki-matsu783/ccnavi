"""古い取り込み状態（`sync/`）の最小の読み取りと削除。

`ccnavi sync migrate` だけが使う使い捨てのモジュール。

取り込み状態は廃止した。このモジュールの役割は、移行の副命令が、古い記録から
`retire/<親>.json` に写すもの（ブランチ・MR の番号）を拾い、古い記録を消すことだけ。
移行が行き渡ったら、このモジュールごと削除する。

    <state の置き場>/sync/<リポジトリ>/families/<P>
        親子のチケットの取り込み状態（1 行 1 項目 `<鍵> <値>`）
    <state の置き場>/sync/<リポジトリ>/integration/
        統合先の取り込み結果のコピー（消すだけ）

`<リポジトリ>` はワークスペース自身なら `self`、プロジェクトならその名前。
途中にシンボリックリンクがあるものは辿らず、読まない・消さない（`skipped` に理由を入れる）。
"""

from __future__ import annotations

import os
import shutil
import stat
from dataclasses import dataclass

SYNC_DIR = "sync"
FAMILIES_DIR = "families"
INTEGRATION_DIR = "integration"
# 古い記録 1 つの大きさの上限。超えたら切らずに読まない。
_RECORD_LIMIT = 64 * 1024


@dataclass(frozen=True)
class LegacyFamily:
    """古い記録 1 つ。

    `skipped` が空でなければ読まず・消さない理由（リンクなど。`branch` `mr` は空）。
    `branch` は記録に書いた親のブランチ名（前の記録は識別子と同じ名前を書いている）。無ければ空。
    `mr` は記録の MR の番号（数字だけ）。無い・読めない値は空。
    """

    repo: str
    name: str
    path: str
    branch: str = ""
    mr: str = ""
    skipped: str = ""


def read_families(state_dir: str) -> list[LegacyFamily]:
    """古い記録の一覧（リポジトリ名、親の識別子の順）。書きかけ（`*.tmp.*`）は数えない。

    `sync/` が無ければ空。読めない記録は `skipped` に理由を入れて並べる。
    """
    if not state_dir:
        return []
    base = os.path.join(state_dir, SYNC_DIR)
    out: list[LegacyFamily] = []
    if _linked(base) is not False:
        return out
    for repo in _names(base):
        fam_dir = os.path.join(base, repo, FAMILIES_DIR)
        links = (_linked(os.path.join(base, repo)), _linked(fam_dir))
        if True in links:
            out.append(LegacyFamily(repo, "", fam_dir, skipped="途中にシンボリックリンクがある"))
            continue
        if None in links:
            continue
        for name in _names(fam_dir):
            if ".tmp." in name:
                continue
            out.append(_read(repo, name, os.path.join(fam_dir, name)))
    return out


def remove_family(state_dir: str, repo: str, name: str) -> bool:
    """古い記録 1 つを消す。消したら True。リンク・ふつうのファイルでないものは消さず False。"""
    path = os.path.join(state_dir, SYNC_DIR, repo, FAMILIES_DIR, name)
    if not _is_plain_file(path) or _above_linked(state_dir, repo):
        return False
    try:
        os.unlink(path)
    except OSError:
        return False
    _prune_empty(state_dir, repo)
    return True


def integration_repos(state_dir: str) -> list[str]:
    """統合先の取り込み結果のコピーが残っているリポジトリの名前。

    `integration/` のほか、書きかけの `integration.tmp.*` `integration.old.*` も数える。
    """
    if not state_dir:
        return []
    base = os.path.join(state_dir, SYNC_DIR)
    if _linked(base) is not False:
        return []
    out = []
    for repo in _names(base):
        if _linked(os.path.join(base, repo)) is True:
            continue
        if _integration_entries(base, repo):
            out.append(repo)
    return out


def remove_integration(state_dir: str, repo: str) -> tuple[bool, str]:
    """統合先の取り込み結果のコピーを消す。`(消した, 消さなかった理由)`。

    無ければ `(False, "")`。`sync/<リポジトリ>` や `integration` がリンクなら消さず、理由を返す。
    中のリンクは辿らず、リンクそのものを消す。
    """
    base = os.path.join(state_dir, SYNC_DIR)
    if _above_linked(state_dir, repo):
        return False, "途中にシンボリックリンクがある"
    entries = _integration_entries(base, repo)
    if not entries:
        return False, ""
    removed = False
    for name in entries:
        path = os.path.join(base, repo, name)
        if _linked(path) is True:
            return removed, f"{name} がシンボリックリンク"
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.unlink(path)
        except OSError as exc:
            return removed, f"{name} を消せない（{exc.strerror or type(exc).__name__}）"
        removed = True
    _prune_empty(state_dir, repo)
    return removed, ""


# ---- 下請け


def _read(repo: str, name: str, path: str) -> LegacyFamily:
    try:
        mode = os.lstat(path).st_mode
    except OSError as exc:
        return LegacyFamily(repo, name, path, skipped=f"読めない（{exc.strerror}）")
    if stat.S_ISLNK(mode):
        return LegacyFamily(repo, name, path, skipped="シンボリックリンク")
    if not stat.S_ISREG(mode):
        return LegacyFamily(repo, name, path, skipped="ふつうのファイルでない")
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as f:
            raw = f.read(_RECORD_LIMIT + 1)
        if len(raw) > _RECORD_LIMIT:
            return LegacyFamily(repo, name, path, skipped="大きすぎる")
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return LegacyFamily(repo, name, path, skipped=f"読めない（{type(exc).__name__}）")
    record: dict[str, str] = {}
    for line in text.splitlines():
        key, _, value = line.partition(" ")
        if key and key not in record:
            record[key] = value.strip()
    mr = record.get("mr", "")
    return LegacyFamily(
        repo,
        name,
        path,
        branch=record.get("branch", ""),
        mr=mr if mr.isascii() and mr.isdigit() else "",
    )


def _names(directory: str) -> list[str]:
    try:
        return sorted(os.listdir(directory))
    except OSError:
        return []


def _linked(path: str) -> bool | None:
    """リンクなら True、リンクでなければ False、無ければ None。"""
    try:
        return stat.S_ISLNK(os.lstat(path).st_mode)
    except OSError:
        return None


def _above_linked(state_dir: str, repo: str) -> bool:
    base = os.path.join(state_dir, SYNC_DIR)
    return _linked(base) is True or _linked(os.path.join(base, repo)) is True


def _is_plain_file(path: str) -> bool:
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except OSError:
        return False


def _integration_entries(base: str, repo: str) -> list[str]:
    prefixes = (INTEGRATION_DIR + ".tmp.", INTEGRATION_DIR + ".old.")
    return [
        n
        for n in _names(os.path.join(base, repo))
        if n == INTEGRATION_DIR or n.startswith(prefixes)
    ]


def _prune_empty(state_dir: str, repo: str) -> None:
    """空になった `families/` `sync/<リポジトリ>/` `sync/` を片付ける（空でなければ何もしない）。"""
    base = os.path.join(state_dir, SYNC_DIR)
    for directory in (
        os.path.join(base, repo, FAMILIES_DIR),
        os.path.join(base, repo),
        base,
    ):
        linked = _linked(directory)
        if linked is None:
            continue
        if linked:
            return
        try:
            os.rmdir(directory)
        except OSError:
            return
