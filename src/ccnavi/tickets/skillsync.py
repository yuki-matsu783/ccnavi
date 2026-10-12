"""着手の前に、共通の概念スキルをプロジェクトの `skills/` へ写す。

概念スキルは、ワークスペースの `.claude/skills/<概念>/` にある、頭の frontmatter に
`concept: true` を持つスキルで、`SKILL.md` と `references/common/` の下の `.md` が写す中身になる。
`references/` の最上段は `common/`（共通。写す）とそれ以外（プロジェクト固有。写さない）に分かれる。
プロジェクトだけを clone したユーザからは見えないので、親チケットの `ticket start` で
親のワークツリーにあるプロジェクトの `skills/<概念>/` へ写す。

写すのは `references/common/` だけで、`references/` のそれ以外（プロジェクト固有）には触れない
ので、プロジェクトが育てたファイルは上書きも削除もされない（`.ccnavi/docs/skill-concepts.md`）。
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

from ..infra import settings, tree, yamlread
from . import configsync, flow

CONCEPT_KEY = "concept"
SKILL_FILE = "SKILL.md"
# `references/` の最上段で、共通として写すディレクトリ。それ以外は写さず、触れない。
COMMON_REFS = ("references", "common")
# 写し先の、プロジェクトのルートからの相対。目録（`hook.projskills`）の読み先と同じ。
SKILLS_DIR = "skills"
# 共通の置き場の、ワークスペースルートからの相対。
SOURCE_DIR = ".claude/skills"
# frontmatter を探すのは頭のこの長さだけ。
HEAD_LIMIT = 8 * 1024


def concepts(root: str) -> list[str]:
    """共通の概念スキルの名前（`.claude/skills/` 直下で `concept: true` を持つもの）。名前の順。"""
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
        if raw is not None and _concept_flag(raw):
            found.append(name)
    return found


def _concept_flag(raw: bytes) -> bool:
    text = raw[:HEAD_LIMIT].decode("utf-8", errors="replace")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return False
    try:
        end = next(i for i, line in enumerate(lines[1:], 1) if line.strip() == "---")
        data = yamlread.load("\n".join(lines[1:end]), flow._Loader)
    except (StopIteration, yaml.YAMLError):
        return False
    return isinstance(data, dict) and data.get(CONCEPT_KEY) is True


def is_shared(parts: list[str]) -> bool:
    """概念スキルのディレクトリからの相対（"/" で割ったもの）が、写す対象の形か。

    `SKILL.md` と、`references/common/` の下の `.md`。`references/` のそれ以外は写さない。
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
    for name in concepts(root):
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
    for name in concepts(root):
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
        f"共通の概念スキルとプロジェクト {project} の skills/ が違っていたので、"
        "共通の中身に揃えた（references/project/ などプロジェクト固有のファイルには触れていない）:"
    ]
    for c in changes:
        state = "消した" if c.deletes else ("上書き" if c.before is not None else "新しく置いた")
        lines.append(f"  - {c.rel}（{state}）")
    return lines


def is_synced_write(
    conf: settings.Settings,
    root: str,
    path: str,
    content: bytes | None | object = configsync.UNSET,
) -> bool:
    """その変更が、親の着手がミラーか概念スキルの写しとして配った書き込みだと読めるか。

    `configsync.is_synced_write`（共通レイヤーのミラー）に、概念スキルの写しを足した答え。
    C1 の置き場の検査と、実行後チェック、バックアップの復元が使う。
    """
    if configsync.is_synced_write(conf, root, path, content):
        return True
    return _is_copied_skill(conf, root, path, content)


def _is_copied_skill(
    conf: settings.Settings, root: str, path: str, content: bytes | None | object
) -> bool:
    """概念スキルの写しとして読めるか。次が全部揃ったときだけ。

    1. 置き場が、プロジェクトから切った承認済みの親チケットのワークツリーの
       `skills/<概念>/SKILL.md` か `skills/<概念>/references/common/` の下の `.md`
       （`<概念>` は共通の概念スキル。元リポジトリ、子、チケットの無いワークツリー、
       他のプロジェクトは外さない）
    2. そのパスの途中にシンボリックリンクが無い
    3. 中身が、ワークスペースの `.claude/skills/<概念>/` の同じファイルと同じ（改行は見ない）。
       消す変更（変更後の中身が無い）は、共通側にそのファイルが無いときだけ通す。共通側に
       あるファイルを消す変更と、`SKILL.md` を消す変更は通さない

    読めないものは外さない。
    """
    where = tree.tree_of(root, configsync._key(path), conf.projects)
    if where is None or not where.project or where.is_main:
        return False
    rel = configsync._rel(where.root, path)
    parts = rel.split("/")
    if len(parts) < 3 or parts[0] != SKILLS_DIR or parts[1] not in concepts(root):
        return False
    if not is_shared(parts[2:]):
        return False
    if configsync._linked(where.root, path) or not configsync._approved_parent(conf, root, where):
        return False
    source = os.path.join(root, *SOURCE_DIR.split("/"), *parts[1:])
    if configsync._linked(root, source):
        return False
    now = configsync._read(path) if content is configsync.UNSET else content
    if now is None:
        # 消す変更。共通側から無くなったファイルの片付けだけを通す（リンクの先を見て答えない）。
        return parts[2:] != [SKILL_FILE] and not os.path.lexists(source)
    expected = configsync._read(source)
    if expected is None or not isinstance(now, bytes):
        return False
    return configsync._same(now, expected)
