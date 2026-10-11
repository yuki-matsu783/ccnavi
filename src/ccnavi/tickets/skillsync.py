"""着手の前に、共通の概念スキルをプロジェクトの `skills/` へ写す（設計 11.13）。

概念スキルは、ワークスペースの `.claude/skills/<概念>/` にある、頭の frontmatter に
`concept: true` を持つスキルで、`SKILL.md` と `references/` の下の `.md` が中身になる。
プロジェクトだけを clone したユーザからは見えないので、親チケットの `ticket start` で
親のワークツリーにあるプロジェクトの `skills/<概念>/` へ写す。

共通レイヤーのミラー（`configsync`）と違い、**上書きと追加だけで、消さない**。写し先は
プロジェクト固有の reference と同じディレクトリで、そこにはプロジェクトが育てたファイルがある。
共通側から無くなったファイルが写し先に残っても、プロジェクト固有のものを守る側に倒す。
同じパスのファイルは共通の中身で上書きするので、プロジェクト固有の reference は
共通と違うパスに置く（`docs/claude/skill-concepts.md`）。

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


def sources(root: str) -> tuple[dict[str, bytes], str]:
    """写すもの。プロジェクトの `skills/` からの相対 → 中身。読めなければ理由（何も写さない）。"""
    out: dict[str, bytes] = {}
    base = os.path.join(root, *SOURCE_DIR.split("/"))
    for name in concepts(root):
        top = os.path.join(base, name)
        for parent, dirs, names in os.walk(top):
            dirs.sort()
            for entry in [*dirs, *names]:
                if os.path.islink(os.path.join(parent, entry)):
                    shown = os.path.relpath(os.path.join(parent, entry), root).replace(os.sep, "/")
                    return {}, f"{shown} がシンボリックリンク。写さない"
            for file in sorted(names):
                if not (file == SKILL_FILE or file.endswith(".md")):
                    continue
                path = os.path.join(parent, file)
                raw, why = configsync._read_strict(path)
                if why or raw is None:
                    shown = os.path.relpath(path, root).replace(os.sep, "/")
                    return {}, f"{shown} を読めない ({why or '無い'})"
                rel = os.path.relpath(path, base).replace(os.sep, "/")
                out[rel] = raw
    return out, ""


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
    return out, ""


def apply(changes: list[configsync.Change]) -> str:
    """変更を加える。書けなかった理由を返す。書けたら空文字。"""
    return configsync.apply(changes)


def describe(project: str, changes: list[configsync.Change]) -> list[str]:
    """着手の出力に足す行。"""
    lines = [
        f"共通の概念スキルとプロジェクト {project} の skills/ が違っていたので、"
        "共通の中身で上書きした（プロジェクト固有のファイルは消していない）:"
    ]
    for c in changes:
        lines.append(f"  - {c.rel}（{'上書き' if c.before is not None else '新しく置いた'}）")
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
       `skills/<概念>/<ファイル>.md`（`<概念>` は共通の概念スキル。元リポジトリ、子、
       チケットの無いワークツリー、他のプロジェクトは外さない）
    2. そのパスの途中にシンボリックリンクが無い
    3. 中身が、ワークスペースの `.claude/skills/<概念>/` の同じファイルと同じ（改行は見ない）。
       変更後の中身が無い（消した）ものは通さない。写しは消さないので、消す変更は写しではない

    読めないものは外さない。
    """
    where = tree.tree_of(root, configsync._key(path), conf.projects)
    if where is None or not where.project or where.is_main:
        return False
    rel = configsync._rel(where.root, path)
    parts = rel.split("/")
    if len(parts) < 3 or parts[0] != SKILLS_DIR or parts[1] not in concepts(root):
        return False
    if not parts[-1].endswith(".md") or any(p in ("", ".", "..") for p in parts):
        return False
    if configsync._linked(where.root, path) or not configsync._approved_parent(conf, root, where):
        return False
    source = os.path.join(root, *SOURCE_DIR.split("/"), *parts[1:])
    if configsync._linked(root, source):
        return False
    expected = configsync._read(source)
    now = configsync._read(path) if content is configsync.UNSET else content
    if expected is None or not isinstance(now, bytes):
        return False
    return configsync._same(now, expected)
