"""ccnavi 本体（ワークスペース）の事実を読む（段 1）。

HEAD・ブランチの記録、確認に出す git の状態、判定のモード。
"""

import json
import os
import subprocess
from pathlib import Path

GIT_SH = "{root}/.ccnavi/scripts/ccnavi-git.sh"


def repo_snapshot(root):
    """ccnavi 本体の HEAD・ブランチの一覧・ワークツリーの一覧。回す前と後で比べる。"""
    parts = []
    for args in (
        ["rev-parse", "HEAD"],
        ["for-each-ref", "--format=%(refname)", "refs/heads"],
        ["worktree", "list", "--porcelain"],
    ):
        r = subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=True, timeout=120
        )
        parts.append(r.stdout if r.returncode == 0 else f"エラー {r.returncode}: {r.stderr}")
    return "\n".join(parts)


def preflight_facts(root):
    git = GIT_SH.format(root=root)
    lines = []
    for label, cwd in (
        ("ワークスペースルート", root),
        ("projects/test-repo", Path(root) / "projects/test-repo"),
    ):
        if not Path(cwd).is_dir():
            lines.append(f"## {label}: 置き場が無い（{cwd}）\n")
            continue
        for sub in ("status", "worktree list", "branch"):
            r = subprocess.run(
                f"sh {git} {sub}", shell=True, cwd=cwd, capture_output=True, text=True, timeout=120
            )
            lines.append(f"## {label}: git {sub}\n{(r.stdout or r.stderr).strip()}\n")
    return "\n".join(lines)


MODE_ENV = "CCNAVI_MODE"


def mode_check(root):
    """判定のモードが enable かを確かめる。(enable か, 人間に見せる文)。

    dry-run では、hook は拒否を報告するだけで止めない（enforced が false の行になる）。
    シナリオは「止まる」ことを確かめるので、enable 以外では回さない。値は、プロジェクトの
    .claude/settings.json の env と、実行の環境変数を見る。どちらかに enable 以外の値が
    あれば止める。値が無ければ enable（hook の既定と同じ）。
    """
    settings_path = Path(root) / ".claude/settings.json"
    try:
        declared = json.loads(settings_path.read_text(encoding="utf-8")).get("env", {})
    except FileNotFoundError:
        declared = {}
    except (OSError, ValueError) as exc:
        return (
            False,
            f"## 判定のモード: {settings_path} を読めない（{exc}）。確かめられないので回さない",
        )
    sources = [
        (".claude/settings.json の env", declared.get(MODE_ENV)),
        ("実行の環境変数", os.environ.get(MODE_ENV)),
    ]
    lines = ["## 判定のモード（CCNAVI_MODE）"]
    ok = True
    for label, value in sources:
        if value is None:
            lines.append(f"- {label}: 指定なし")
            continue
        lines.append(f"- {label}: {value}")
        if value.strip().lower() not in ("", "enable"):
            ok = False
    if ok:
        lines.append("enable で判定している。回してよい")
    else:
        lines.append(
            "enable ではない。dry-run などでは止まらない判定が混じるので回さない。"
            "モードを enable にしてから打ち直す（設定の変更はユーザが行う）"
        )
    return ok, "\n".join(lines)
