"""下書きの階層名の読み替えの見本（tests/ticket と tests/sh が同じ表を使う）。

表（`.ccnavi/scripts/ccnavi-handoff-names.txt`）に名前を足したときは、`SAMPLES` に 1 行足す。
"""

from __future__ import annotations

# (元のパス, 下書きの置き場の中のパス)。`ccnavi-stage.sh` も同じ見本を通る。
SAMPLES = [
    (".claude/settings.json", "handoff-.claude/settings.json"),
    (".claude/settings.local.json", "handoff-.claude/settings.local.json"),
    (".claude/hooks/lint-py.sh", "handoff-.claude/hooks/lint-py.sh"),
    (".claude/skills/x/SKILL.md", "handoff-.claude/skills/x/SKILL.md"),
    ("projects/web/.claude/settings.json", "projects/web/handoff-.claude/settings.json"),
    ("a/.claude/b/.claude/c.json", "a/handoff-.claude/b/handoff-.claude/c.json"),
    ("日本語/.claude/x.md", "日本語/handoff-.claude/x.md"),
    # 大文字小文字は区別しない
    (".CLAUDE/settings.json", "handoff-.claude/settings.json"),
    (".Claude/hooks/x.sh", "handoff-.claude/hooks/x.sh"),
    # 要素が名前そのものではないものは、読み替えない
    (".ccnavi/docs/guide.md", ".ccnavi/docs/guide.md"),
    ("docs/claude/x.md", "docs/claude/x.md"),
    ("my.claude/x.md", "my.claude/x.md"),
    (".claude-old/x.md", ".claude-old/x.md"),
    ("claude/x.md", "claude/x.md"),
    ("x/.claude", "x/handoff-.claude"),
]

# 下書きのパス → 元のパス（戻す向き。綴りは表の元の階層名にそろう）。
REVERSE_SAMPLES = [
    ("handoff-.claude/settings.json", ".claude/settings.json"),
    ("projects/web/handoff-.claude/hooks/x.sh", "projects/web/.claude/hooks/x.sh"),
    ("Handoff-.Claude/settings.json", ".claude/settings.json"),
    ("HANDOFF-.CLAUDE/a/handoff-.claude/b", ".claude/a/.claude/b"),
    (".ccnavi/docs/guide.md", ".ccnavi/docs/guide.md"),
    ("handoff-.claude-old/x", "handoff-.claude-old/x"),
]

# 予約名（下書きでの階層名）を含む元のパス。stage が断る。
RESERVED = [
    "handoff-.claude/x.json",
    "a/Handoff-.CLAUDE/x.json",
    ".claude/handoff-.claude/x.json",
]
