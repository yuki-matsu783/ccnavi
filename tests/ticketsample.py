"""チケットの見本（ルール・フェーズ定義・親と子の提案の本文）と、Chrome の入口を読む道具。

`tests/ticket/` と `tests/sh/` の両方が同じ見本で親と子を組み立てる。テストモジュールどうしで
import し合うと、片方のグループだけを回したときにも他方のモジュールが読み込まれ、グループの
境目が崩れる。共有するものはグループに属さないここに置く。
"""

from __future__ import annotations

import importlib.util
import os

from tests import ROOT, config_path, phasecopy

RULES = {
    "version": 1,
    "deny": [
        {
            "id": "guard-approved",
            "match": "Write|Edit|NotebookEdit",
            "glob": "*/.ccnavi/*",
            # 守る場所の写しを置く wip/handoff/ の中は、本物のルールと同じく除く
            "except": [{"regex": r"[\\/]wip[\\/]handoff[\\/]"}],
            "message": "ガードの設定と承認済みチケットです。ユーザに依頼してください。",
        }
    ],
}


PHASES = """
version: 1
phases:
  research:
    kind: work
    title: 調査
    review: none
    scope: ["wip/research/*"]
    deliverables: ["wip/research/summary.md"]
    agent: explorer
    when: 既存の振る舞いが分からないとき
  design:
    kind: work
    title: 設計
    review: mr
    scope: ["wip/design/*"]
  acceptance:
    kind: work
    title: 受入テスト作成
    review: mr
    scope: ["tests/*"]
  implement:
    kind: work
    title: 実装とテスト
    review: mr
    scope: ["src/*", "tests/*"]
  implement-feedback:
    kind: feedback
    title: 実装フィードバック対応
    review: mr
    scope: inherit
  chores:
    kind: work
    title: 片付け
    review: mr
    scope: ["src/*"]
  chores-feedback:
    kind: feedback
    title: 片付けフィードバック対応
    review: mr
    scope: inherit
"""


def plan_item_lines(items, start=1) -> list[str]:
    """計画の項を YAML の行にする。`start` はその計画の最初の番号（フィードバック計画は続き）。

    項は定義の名前、`(名前, review)`、`{"type": …, "review": …, "after": […]}` のどれか。
    `after` を書かない項は、すぐ前の項を待つ（一直線）。最初の項は何も待たない。
    並行にしたい見本は辞書で `after` を明記する（`[]` なら何も待たない）。
    """
    lines = []
    for i, item in enumerate(items):
        if isinstance(item, dict):
            kind, review = item["type"], item.get("review", "")
            after = item.get("after")
        elif isinstance(item, tuple):
            kind, review, after = item[0], item[1], None
        else:
            kind, review, after = item, "", None
        if after is None:
            after = [start + i - 1] if i > 0 else []
        fields = [f"type: {kind}"]
        if review:
            fields.append(f"review: {review}")
        if after:
            fields.append(f"after: [{', '.join(str(n) for n in after)}]")
        lines.append(f"  - {kind}" if len(fields) == 1 else f"  - {{{', '.join(fields)}}}")
    return lines


def parent_text(
    name, plan, feedback=None, allow=("src/*", "wip/*", "tests/*"), issue=None, copy=PHASES
):
    """親の提案の本文。

    `copy` は計画が使う定義を写す元の `phases.yml`（None なら `phases:` を書かない）。
    計画を持つ親は、計画が使う定義の写し（`phases:`）を持つ。`phases.yml` を書き換えるテストは、
    同じ本文を `copy` に渡す。
    """
    text = _parent_text(name, plan, feedback, allow, issue)
    return text if copy is None else phasecopy.insert(text, copy)


def _parent_text(name, plan, feedback, allow, issue):
    lines = ["---", "version: 1", f"ticket: {name}", "mr: 1"]
    lines.append(f"issue: {1 if issue is None else issue}")
    lines.append("plan:")
    lines += plan_item_lines(plan)
    if feedback is not None:
        if feedback:
            lines.append("feedback:")
            lines += plan_item_lines(feedback, start=len(plan) + 1)
        else:
            lines.append("feedback: []")
    lines += ["human_review:", "  required: true", "  reason: t", "title: 親", "rationale: r"]
    lines.append("allow:")
    for g in allow:
        lines += ["  - match: Write|Edit", f'    glob: "{g}"']
    lines += ['started_at: ""', 'completed_at: ""', 'base_sha: ""', "---", ""]
    # 親の必須の見出し（形式検査が見る）。中身は短い一言にして、目印は残さない。
    for heading in ("目的", "範囲", "対象外", "完了の確かめ方", "計画の理由"):
        lines += [f"## {heading}", "", "本文", ""]
    return "\n".join(lines)


def child_text(name, parent, phase, allow, review=True):
    lines = [
        "---",
        "version: 1",
        f"ticket: {name}",
        f"parent: {parent}",
        f"phase: {phase}",
        "human_review:",
        f"  required: {'true' if review else 'false'}",
        "  reason: t",
        f"title: 子 {name}",
        "rationale: r",
        "allow:",
    ]
    for g in allow:
        lines += ["  - match: Write|Edit", f'    glob: "{g}"']
    lines += ['started_at: ""', 'completed_at: ""', 'base_sha: ""', "---", "", "本文"]
    return "\n".join(lines) + "\n"


# Chrome 拡張（承認の画面）の置き場。Python の入口 `py/ccnavi_chrome.py` を持つ。
CHROME = os.path.join(ROOT, "extensions", "chrome", "ccnavi-approval")


def load_chrome():
    """Chrome の入口（`ccnavi_chrome.py`）をモジュールとして読み込む。"""
    spec = importlib.util.spec_from_file_location(
        "ccnavi_chrome", os.path.join(CHROME, "py", "ccnavi_chrome.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# `plan` を持たない親は `ticket start` が止まるので、計画の中身を問わないテストの親は
# この最小の計画（`work` の項を並べただけ。項は互いに待たない）を持つ。
PLAIN_PHASES = """
version: 1
phases:
  work:
    kind: work
    title: 作業
    review: none
    scope: inherit
  work-mr:
    kind: work
    title: MR作業
    review: mr
    scope: inherit
"""


def plain_plan_lines(plan=1) -> list[str]:
    """`plan:` の行。`plan` は `work` の項の数か定義名の並び。最後の項だけが前の全部を待つ。"""
    names = ["work"] * plan if isinstance(plan, int) else list(plan)
    # 終端は 1 つ（最後の項がほかの全部を待つ）にしないと承認が通らない。
    last = len(names)
    return ["plan:"] + [
        f"  - {{type: {n}, after: [{', '.join(str(k) for k in range(1, last))}]}}"
        if i == last
        else f"  - {{type: {n}, after: []}}"
        for i, n in enumerate(names, 1)
    ]


def plain_parent(text: str, plan=1) -> str:
    """計画を持たない親の本文に、最小の計画（`work` を `plan` 個、または定義名の並び）と写しを足す。

    `plan` が 0 か空なら、子（`parent:` を持つ）と、すでに `plan:` がある本文と同じくそのまま返す。
    """
    if not plan or "\nparent:" in text or "\nplan:" in text:
        return text
    lines = text.split("\n")
    at = next(i for i, line in enumerate(lines) if line.startswith("human_review:"))
    lines[at:at] = plain_plan_lines(plan)
    return phasecopy.insert("\n".join(lines), PLAIN_PHASES)


def write_plain_phases(root: str) -> str:
    """`PLAIN_PHASES` をそのルートの config の `phases.yml` に書く（親の承認が写しを確かめる）。"""
    path = config_path(root, "phases")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(PLAIN_PHASES)
    return path


def record_merge(parent_tree: str, child: str) -> None:
    """子を親のブランチへ合流したあとの記録（`merged_sha`）を、write-and-push の finish
    の代わりに書く。

    本物の finish は、親のワークツリーで子を合流するのと同じコミットで、子の承認済みチケットに
    合流した子の先頭を書く。sh（write-and-push）を通らずに実行ファイルを直に起こすテストは、合流を
    `git merge`
    で済ませたあと、これで同じ記録を足してコミットする。
    """
    import subprocess

    from ccnavi.tickets import ticket_fields

    def git(*args: str) -> str:
        done = subprocess.run(
            ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
            cwd=parent_tree,
            capture_output=True,
            text=True,
            check=True,
        )
        return done.stdout.strip()

    sha = git("rev-parse", child)
    for rel in (
        f".ccnavi/approved/done/{child}.md",
        f".ccnavi/approved/doing/{child}.md",
        f"wip/proposals/review/{child}.md",
    ):
        path = os.path.join(parent_tree, *rel.split("/"))
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as f:
            text = f.read()
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(ticket_fields.set_fields(text, {"merged_sha": sha}))
        git("add", "--", rel)
        staged = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=parent_tree)
        if staged.returncode != 0:
            git("commit", "--quiet", "-m", f"merged_sha {child}")
        return
