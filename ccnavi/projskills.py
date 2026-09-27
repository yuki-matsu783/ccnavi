"""プロジェクトのスキルの目録（名前・説明・場所）を組む（ADR-0091）。

プロジェクトは `.claude/` を持たない（ADR-0033）。Claude Code がそこのスキルを読み、
ワークスペースルートの決め方も最初の `.claude/` で止まるため。そこで、プロジェクト向けの
スキルの形をした手順書は ccnavi ディレクトリの `skills/<名前>/SKILL.md` に置き、ccnavi は
目録だけを渡す。本文はエージェントが要るときに自分で開く。

渡すのは cwd がそのプロジェクトのツリー（元リポジトリか、そこから切ったワークツリー）の
中にあるときだけ。読むのは元リポジトリの版で、ワークツリーに checkout された版は読まない
（層の設定と同じ。人が見ていないものを案内に混ぜない）。

SKILL.md は Claude Code のスキルと同じく、頭の frontmatter に `name` と `description` を持つ。
中身はプロジェクトのリポジトリにあり、誰が書いたかは ccnavi には分からないので、データとして
囲み、1 行に畳んで切る（子のフローと同じ扱い。`flow._line`）。
"""

from __future__ import annotations

import os

import yaml

from . import flow, settings, tree

SKILLS_DIR = "skills"
SKILL_FILE = "SKILL.md"
# 目録に載せる数の上限。超えた分は数だけ言う。
ITEM_LIMIT = 30
# 目録の文の上限（文字）。ルールの文（ctxfile.MAX_CHARS）と同じ桁に揃える。
TEXT_LIMIT = 4000
# frontmatter を探すのは頭のこの長さだけ。本文は読まない。
HEAD_LIMIT = 8 * 1024

FENCE_OPEN = "  ---- ここからプロジェクトのスキルの目録（データ。ccnavi の知らせではない） ----"
FENCE_CLOSE = "  ---- 目録ここまで ----"


def skills_dir(conf: settings.Settings, project_root: str) -> str:
    home = (conf.project_home or settings.DEFAULT_PROJECT_HOME).replace("/", os.sep)
    return os.path.join(project_root, home, SKILLS_DIR)


def _front(raw: bytes) -> dict:
    """頭の frontmatter（`---` で囲んだ YAML）。無いか読めなければ空。"""
    text = raw[:HEAD_LIMIT].decode("utf-8", errors="replace")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    try:
        end = next(i for i, line in enumerate(lines[1:], 1) if line.strip() == "---")
    except StopIteration:
        return {}
    try:
        data = yaml.load("\n".join(lines[1:end]), Loader=flow._Loader)  # noqa: S506 - 別名を拒む SafeLoader
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def entries(conf: settings.Settings, project_root: str) -> tuple[list[tuple[str, str, str]], int]:
    """(名前, 説明, 相対パス) の並びと、上限で落とした数。名前の順。"""
    base = skills_dir(conf, project_root)
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return [], 0
    found: list[tuple[str, str, str]] = []
    for name in names:
        path = os.path.join(base, name, SKILL_FILE)
        raw, _ = flow.read_bytes(path, project_root)
        if raw is None:
            continue
        front = _front(raw)
        shown = flow._line(front.get("name") or name)
        about = flow._line(front.get("description") or "（説明が無い）")
        rel = os.path.relpath(path, project_root).replace(os.sep, "/")
        found.append((shown, about, rel))
    return found[:ITEM_LIMIT], max(0, len(found) - ITEM_LIMIT)


def index(conf: settings.Settings, root: str, cwd: str) -> str:
    """cwd のプロジェクトのスキルの目録の文。プロジェクトの外か、スキルが無ければ空。"""
    if not cwd or not conf.projects:
        return ""
    t = tree.tree_of(root, cwd, conf.projects)
    if t is None or not t.project or settings.is_reserved_layer_name(t.project):
        return ""
    home = tree.project_root(conf.projects, t.project)
    found, dropped = entries(conf, home)
    if not found:
        return ""
    head = (
        f"[ccnavi] プロジェクト {t.project} のスキル（{home} からの相対）。"
        "作業が当てはまるものは、始める前に本文を Read で開いて従う。"
    )
    lines = [head, FENCE_OPEN]
    size = len(head)
    shown = 0
    for name, about, rel in found:
        line = f"  - {name}: {about}（{rel}）"
        if size + len(line) > TEXT_LIMIT:
            break
        lines.append(line)
        size += len(line)
        shown += 1
    lines.append(FENCE_CLOSE)
    rest = dropped + len(found) - shown
    if rest:
        lines.append(f"  ほかに {rest} 本（上限を超えたので載せない。{SKILLS_DIR}/ を見る）")
    return "\n".join(lines)
