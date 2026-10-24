"""着手の前に、共通の工程スキルをプロジェクトの `skills/` へ写す。

工程スキルは、ワークスペースの `.claude/skills/<工程>/` にある、頭の frontmatter に
`process: true` を持つスキルで、`SKILL.md` と `references/common/` の下の `.md` が写す中身になる。
`references/` の最上段は `common/`（共通。写す）とそれ以外（プロジェクト固有。写さない）に分かれる。
プロジェクトだけを clone したユーザからは見えないので、親チケットの `ticket start` で
親のワークツリーにあるプロジェクトの `skills/<工程>/` へ写す。

写すのは `references/common/` だけで、`references/` のそれ以外（プロジェクト固有）には触れない
ので、プロジェクトが育てたファイルは上書きも削除もされない（`.ccnavi/docs/skill-processes.md`）。
`common/` は共通の写しなので、共通側から無くなった `.md` は写し先からも消す（共通レイヤーの
ミラーと同じ）。消えて空になったディレクトリは、`common/` の下から片付ける。`SKILL.md` は
消さない（共通側に必ずある）。

写し先は ccnavi ディレクトリ（`.ccnavi/`）の外なので、保護は関わらない。写し先かその途中が
シンボリックリンクなら止める。上書きは一時ファイルからの置き換えで行い、途中で止まれば
した分を元へ戻す（`configsync.apply`）。
"""

from __future__ import annotations

import os

import yaml

from ..infra import yamlread
from . import configsync, flow

PROCESS_KEY = "process"
SKILL_FILE = "SKILL.md"
# `references/` の最上段で、共通として写すディレクトリ。それ以外は写さず、触れない。
COMMON_REFS = ("references", "common")
# 写し先の、プロジェクトのルートからの相対。目録（`hook.projskills`）の読み先と同じ。
SKILLS_DIR = "skills"
# 共通の置き場の、ワークスペースルートからの相対。
SOURCE_DIR = ".claude/skills"
# frontmatter を探すのは頭のこの長さだけ。
HEAD_LIMIT = 8 * 1024


def processes(root: str) -> list[str]:
    """共通の工程スキルの名前（`.claude/skills/` 直下で `process: true` を持つもの）。名前の順。"""
    base = os.path.join(root, *SOURCE_DIR.split("/"))
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return []
    found: list[str] = []
    for name in names:
        path = os.path.join(base, name, SKILL_FILE)
        if os.path.islink(os.path.join(base, name)) or os.path.islink(path):
            continue
        if not os.path.isfile(path):
            continue
        raw, _ = configsync._read_strict(path)
        if raw is not None and _process_flag(raw):
            found.append(name)
    return found


def _process_flag(raw: bytes) -> bool:
    text = raw[:HEAD_LIMIT].decode("utf-8", errors="replace")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return False
    try:
        end = next(i for i, line in enumerate(lines[1:], 1) if line.strip() == "---")
        data = yamlread.load("\n".join(lines[1:end]), flow._Loader)
    except (StopIteration, yaml.YAMLError):
        return False
    return isinstance(data, dict) and data.get(PROCESS_KEY) is True


def is_shared(parts: list[str]) -> bool:
    """工程スキルのディレクトリからの相対（"/" で割ったもの）が、写す対象の形か。

    `SKILL.md` と、`references/common/` の下の `.md`。
    """
    if parts == [SKILL_FILE]:
        return True
    return (
        len(parts) > len(COMMON_REFS)
        and tuple(parts[: len(COMMON_REFS)]) == COMMON_REFS
        and parts[-1].endswith(".md")
        and not any(p in ("", ".", "..") for p in parts)
    )


def sources(root: str) -> tuple[dict[str, bytes], str]:
    """写すもの。プロジェクトの `skills/` からの相対 → 中身。読めなければ理由（何も写さない）。"""
    out: dict[str, bytes] = {}
    base = os.path.join(root, *SOURCE_DIR.split("/"))
    for name in processes(root):
        top = os.path.join(base, name)
        files = [os.path.join(top, SKILL_FILE)]
        shared = os.path.join(top, *COMMON_REFS)
        # `references` や `common` そのものがリンクなら、外を指して写せてしまう。
        for chain in (os.path.join(top, COMMON_REFS[0]), shared):
            if os.path.islink(chain):
                return {}, f"{_shown(root, chain)} がシンボリックリンク。写さない"
        for parent, dirs, names in os.walk(shared):
            dirs.sort()
            for entry in [*dirs, *names]:
                if os.path.islink(os.path.join(parent, entry)):
                    linked = _shown(root, os.path.join(parent, entry))
                    return {}, f"{linked} がシンボリックリンク。写さない"
            files.extend(os.path.join(parent, f) for f in sorted(names))
        for path in files:
            if not is_shared(os.path.relpath(path, top).split(os.sep)):
                continue
            raw, why = configsync._read_strict(path)
            if why or raw is None:
                return {}, f"{_shown(root, path)} を読めない ({why or '無い'})"
            out[os.path.relpath(path, base).replace(os.sep, "/")] = raw
    return out, ""


def _shown(root: str, path: str) -> str:
    return os.path.relpath(path, root).replace(os.sep, "/")


def plan(root: str, tree_root: str) -> tuple[list[configsync.Change], str]:
    """プロジェクトの `skills/` に加える変更の一覧。写せない理由があれば、それを返す。"""
    wanted, why = sources(root)
    if why:
        return [], why
    out: list[configsync.Change] = []
    base = os.path.join(tree_root, *SKILLS_DIR.split("/"))
    for rel in sorted(wanted):
        target = os.path.join(base, rel.replace("/", os.sep))
        if configsync._linked(tree_root, target):
            shown = configsync._rel_plain(tree_root, target)
            return [], f"{shown} がシンボリックリンクか、その下にある。写さない"
        before, why = configsync._read_strict(target)
        if why:
            return [], f"{configsync._rel(tree_root, target)} を読めない ({why})"
        if before is not None and configsync._same(before, wanted[rel]):
            continue
        out.append(
            configsync.Change(configsync._rel(tree_root, target), target, wanted[rel], before)
        )
    stale, why = _stale(root, tree_root, base, wanted)
    if why:
        return [], why
    return out + stale, ""


def _stale(
    root: str, tree_root: str, base: str, wanted: dict[str, bytes]
) -> tuple[list[configsync.Change], str]:
    """写し先の `common/` にあって、共通側に無くなった `.md`（消す変更）。"""
    out: list[configsync.Change] = []
    for name in processes(root):
        common = os.path.join(base, name, *COMMON_REFS)
        if not os.path.lexists(common):
            continue
        if configsync._linked(tree_root, common):
            shown = configsync._rel_plain(tree_root, common)
            return [], f"{shown} がシンボリックリンクか、その下にある。写さない"
        present, why = configsync._scan(common)
        if why:
            return [], why
        for rel in sorted(present):
            key = "/".join([name, *COMMON_REFS, rel])
            if key in wanted or not is_shared([*COMMON_REFS, *rel.split("/")]):
                continue
            target = os.path.join(common, rel.replace("/", os.sep))
            before, why = configsync._read_strict(target)
            if why or before is None:
                return [], f"{configsync._rel(tree_root, target)} を読めない ({why or '無い'})"
            out.append(configsync.Change(configsync._rel(tree_root, target), target, None, before))
    return out, ""


def apply(changes: list[configsync.Change]) -> str:
    """変更を加える。書けなかった理由を返す。書けたら空文字。

    消して空になったディレクトリは、その `common/` の下から片付ける。
    """
    failed = configsync.apply(changes)
    if failed:
        return failed
    for dirname in {_common_of(c.target) for c in changes if c.deletes}:
        if dirname:
            configsync._prune_empty(dirname)
    return ""


def _common_of(target: str) -> str:
    """その写し先のファイルが属する `references/common/` のディレクトリ。無ければ空。"""
    parts = target.split(os.sep)
    for i in range(len(parts) - 1):
        if tuple(parts[i : i + len(COMMON_REFS)]) == COMMON_REFS:
            return os.sep.join(parts[: i + len(COMMON_REFS)])
    return ""


def describe(project: str, changes: list[configsync.Change]) -> list[str]:
    """着手の出力に足す行。"""
    lines = [
        f"共通の工程スキルとプロジェクト {project} の skills/ が違っていたので、共通の中身に揃えた:"
    ]
    for c in changes:
        state = "削除した" if c.deletes else ("上書き" if c.before is not None else "新しく置いた")
        lines.append(f"  - {c.rel}（{state}）")
    return lines
