"""承認済みの置き場の閉じた子チケットの識別子を、旧形式から新しい形へ移す（ユーザが打つ）。

旧形式 `<親>-<2 桁の連番>` を、新しい形 `<親>-<2 桁のフェーズ番号>-<2 桁のフェーズ内の連番>` へ
移す。フェーズ番号は子の frontmatter の `phase:`（ccnavi と同じ読み方で読む）。

## 範囲は閉じた子（`done/`）だけ

作業中（`doing/`）の旧形式の子、提案の置き場（`wip/proposals/`）の旧形式の提案、旧形式の子と同じ
名前のワークツリーかブランチがあれば、何も書かずに止まる。作業中の子はワークツリーとブランチと
一緒に動かす必要があり、このスクリプトはそれを扱わない。

## 旧形式か新しい形かは `parent:` で決める

識別子が `<parent>-NN-NN` なら移行済み、`<parent>-NN` なら旧形式。形だけで見ると、親 `a-05` の
旧形式の子 `a-05-03` を移行済みと取り違える。どちらにも当たらない子や、`phase:` が読めない子
（0〜99 の整数でない）があれば、名指しして止まる。

## 採番

同じ親・同じフェーズの子を、旧い連番の順に並べ、そのフェーズに既にある新しい形の子の最大の連番の
続きから振る。

## 動かすもの（`<ツリー>/.ccnavi/approved/` の下）

- `done/` の旧形式の子のファイル名。`doing/` と `done/` の全チケットの本文（`ticket:`・
  `predecessors:`・本文中の言及）
- `phases/<親>/<子>.risk.json` `.judge.json` のファイル名と中身
- `events/<子>.ndjson` のファイル名と中身

次は書き換えない。

- `ccnavi_approved.source_path`（承認したときに提案が在った場所の記録）。過去の事実なので
- 別の記録が中身の指紋を持つもの（`flows/<子>.yml` と、その指紋 `phases/<親>/<子>.flow.json`）。
  名前や中身を変えると指紋と食い違うので、在れば止まって名指しする
- 大文字小文字だけが違う綴り（`I0055-02` など）。ccnavi が書く識別子は書いたままの綴りで、
  本文の大文字の綴りは人が書いた文章の一部なので、機械的には変えない。見つかれば一覧に出す

## 消すもの

`phases/<親>/<親>-NN.risk.json` / `.judge.json` のうち、対応する旧形式の子のチケットが無いもの
（フェーズが分からず新しい名前を決められない。ユーザの決定で消す）。チケット本体（`.md`）は消さない。
消して空になった `phases/<親>/` も消す。

使い方（ツリーのルートで）:

    uv run python wip/design/scripts/migrate_child_ids.py          # 対応表だけ（何も変えない）
    uv run python wip/design/scripts/migrate_child_ids.py --apply  # 移す

移したあとは `git add -A .ccnavi/approved` でコミットする。名前の変更は git が rename として拾う。
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

sys.path.insert(0, os.getcwd())
from ccnavi.tickets import ticket as ticket_mod  # noqa: E402

TWO = r"\d{2}"
# 記録の名前 `<識別子>.<種類>.json` のうち、名前を変えてよいもの（中身の指紋を持たないもの）。
RENAMED_RECORDS = (".risk.json", ".judge.json")
# 中身の指紋に結び付いた記録。在れば止まる。
HASHED_RECORDS = (".flow.json",)


class Stop(Exception):
    """書く前に見つけた、移せない理由。"""


def read_front(path: str) -> dict | None:
    with open(path, encoding="utf-8", newline="") as f:
        text = f.read()
    front, _body, _problems = ticket_mod._frontmatter(text)
    return front if isinstance(front, dict) else None


def kind_of(ident: str, parent: str) -> str:
    """`parent:` から見た識別子の形。`new` / `old` / ``（どちらでもない）。"""
    if not parent or not ident.startswith(parent + "-"):
        return ""
    rest = ident[len(parent) + 1 :]
    if re.fullmatch(rf"{TWO}-{TWO}", rest):
        return "new"
    if re.fullmatch(TWO, rest):
        return "old"
    return ""


def phase_of(front: dict) -> int | None:
    phase = front.get("phase")
    if isinstance(phase, bool) or not isinstance(phase, int):
        return None
    if not 0 <= phase <= ticket_mod.MAX_CHILD_NUMBER:
        return None
    return phase


def tickets_in(approved: str, state: str) -> list[tuple[str, str]]:
    d = os.path.join(approved, state)
    if not os.path.isdir(d):
        return []
    return [(n[:-3], os.path.join(d, n)) for n in sorted(os.listdir(d)) if n.endswith(".md")]


def plan(tree: str, approved: str) -> tuple[dict[str, str], list[str], list[str]]:
    """対応表（旧 → 新）、消すもの、止まる理由。"""
    stops: list[str] = []
    old_children: dict[tuple[str, int], list[tuple[int, str]]] = {}
    used: dict[tuple[str, int], int] = {}
    all_old: set[str] = set()
    every: set[str] = set()
    for state in ("doing", "done"):
        for ident, path in tickets_in(approved, state):
            every.add(ident)
            front = read_front(path)
            if front is None:
                stops.append(f"読めないチケット: {path}")
                continue
            parent = str(front.get("parent") or "").strip()
            if not parent:
                continue
            kind = kind_of(ident, parent)
            phase = phase_of(front)
            if not kind or phase is None:
                stops.append(
                    f"子の識別子か `phase:` が読めない: {path}"
                    f"（parent: {parent}、phase: {front.get('phase')!r}）"
                )
                continue
            if kind == "new":
                key = (parent, phase)
                used[key] = max(used.get(key, 0), int(ident[-2:]))
                continue
            all_old.add(ident)
            if state == "doing":
                stops.append(f"作業中の旧形式の子がある（範囲は閉じた子だけ）: {path}")
                continue
            old_children.setdefault((parent, phase), []).append((int(ident[-2:]), ident))

    mapping: dict[str, str] = {}
    for (parent, phase), items in sorted(old_children.items()):
        start = used.get((parent, phase), 0)
        for n, (_, ident) in enumerate(sorted(items), start=start + 1):
            if n > ticket_mod.MAX_CHILD_NUMBER:
                stops.append(f"{parent} のフェーズ {phase} の連番が 2 桁を超える: {ident}")
                continue
            mapping[ident] = ticket_mod.child_id(parent, phase, n)

    # チケットの無い旧形式の記録（消すもの）
    removals: list[str] = []
    phases_dir = os.path.join(approved, "phases")
    if os.path.isdir(phases_dir):
        for parent in sorted(os.listdir(phases_dir)):
            d = os.path.join(phases_dir, parent)
            if not os.path.isdir(d):
                continue
            for name in sorted(os.listdir(d)):
                for ext in RENAMED_RECORDS:
                    if not name.endswith(ext):
                        continue
                    ident = name[: -len(ext)]
                    if kind_of(ident, parent) != "old" or ident in all_old:
                        continue
                    if ident in every:
                        # 子でないチケット（`-NN` で終わる親など）と同じ名前。どちらの記録か決めない
                        stops.append(
                            f"子でないチケットと同じ名前の記録がある: {os.path.join(d, name)}"
                        )
                        continue
                    removals.append(os.path.join(d, name))

    # 書く前に調べること
    for old, new in mapping.items():
        parent = new[:-6]
        for target in (
            os.path.join(approved, "done", new + ".md"),
            os.path.join(approved, "doing", new + ".md"),
            *(os.path.join(phases_dir, parent, new + ext) for ext in RENAMED_RECORDS),
            os.path.join(approved, "events", new + ".ndjson"),
        ):
            if os.path.exists(target):
                stops.append(f"移す先がすでにある: {target}")
        for hashed in (
            os.path.join(approved, "flows", old + ".yml"),
            *(os.path.join(phases_dir, parent, old + ext) for ext in HASHED_RECORDS),
        ):
            if os.path.exists(hashed):
                stops.append(f"中身の指紋に結び付いた記録があるので動かせない: {hashed}")
    proposals = os.path.join(tree, "wip", "proposals")
    if os.path.isdir(proposals):
        for current, _dirs, names in os.walk(proposals):
            for name in names:
                stem = name.rsplit(".", 1)[0] if name.endswith((".md", ".yml")) else ""
                if stem in all_old:
                    stops.append(f"提案の置き場に旧形式の子がある: {os.path.join(current, name)}")
    worktrees = os.path.join(tree, ".claude", "worktrees")
    for old in sorted(all_old):
        if os.path.exists(os.path.join(worktrees, old)):
            stops.append(
                f"旧形式の子と同じ名前のワークツリーがある: {os.path.join(worktrees, old)}"
            )
    branches = git_branches(tree)
    for old in sorted(all_old):
        if old in branches:
            stops.append(f"旧形式の子と同じ名前のブランチがある: {old}")
    return mapping, removals, stops


def git_branches(tree: str) -> set[str]:
    """手元とリモートの追跡ブランチの名前（`origin/` などを外した名前）。git が無ければ空。"""
    try:
        out = subprocess.run(
            ["git", "for-each-ref", "--format=%(refname)", "refs/heads", "refs/remotes"],
            cwd=tree,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return set()
    names: set[str] = set()
    for ref in out.split():
        if ref.startswith("refs/heads/"):
            names.add(ref[len("refs/heads/") :])
        elif ref.startswith("refs/remotes/"):
            names.add(ref[len("refs/remotes/") :].split("/", 1)[-1])
    return names


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="移す（無ければ対応表を出すだけ）")
    ap.add_argument("--tree", default=".", help="ツリーのルート（既定はいまのディレクトリ）")
    args = ap.parse_args()
    tree = os.path.abspath(args.tree)
    approved = os.path.join(tree, ".ccnavi", "approved")
    if not os.path.isdir(approved):
        print(f"置き場が無い: {approved}", file=sys.stderr)
        return 1

    mapping, removals, stops = plan(tree, approved)
    print("旧 → 新")
    for old, new in sorted(mapping.items()):
        print(f"  {old} → {new}")
    if not mapping:
        print("  （移すものは無い）")
    print("消すもの（チケットが無く、フェーズが分からない記録）:")
    for path in removals:
        print(f"  {path}")
    if not removals:
        print("  （無い）")

    # 前後が識別子の文字でないところだけを当てる（`i0055-02` が `i0055-02-01` の頭に当たらない）。
    pattern = None
    if mapping:
        alt = "|".join(re.escape(k) for k in sorted(mapping, key=len, reverse=True))
        pattern = re.compile(rf"(?<![A-Za-z0-9._-])(?:{alt})(?![A-Za-z0-9_]|-\d|\.\d)")
        loose = re.compile(rf"(?<![A-Za-z0-9._-])(?:{alt})(?![A-Za-z0-9_]|-\d|\.\d)", re.I)

    renames: list[tuple[str, str]] = []
    rewrites: list[str] = []
    case_only: list[str] = []
    if pattern is not None:
        for dirpath, _dirs, files in os.walk(approved):
            for name in sorted(files):
                path = os.path.join(dirpath, name)
                if path in removals:
                    continue
                for ext in (".md", *RENAMED_RECORDS, ".ndjson"):
                    if name.endswith(ext) and name[: -len(ext)] in mapping:
                        new_name = mapping[name[: -len(ext)]] + ext
                        renames.append((path, os.path.join(dirpath, new_name)))
                        break
                if not name.endswith((".md", ".json", ".ndjson")):
                    continue
                with open(path, encoding="utf-8", newline="") as f:
                    text = f.read()
                if rewrite(text, pattern, mapping) != text:
                    rewrites.append(path)
                for m in loose.finditer(text):
                    if m.group(0) not in mapping:
                        case_only.append(f"{path}: {m.group(0)}")

    print("中身を書き換える:")
    for path in rewrites:
        print(f"  {path}")
    print("名前を変える:")
    for src, dst in renames:
        print(f"  {src} → {dst}")
    if case_only:
        print("大文字小文字だけが違う綴り（置き換えない）:")
        for line in case_only:
            print(f"  {line}")
    if stops:
        print("移せない（何も書かずに止まる）:", file=sys.stderr)
        for reason in stops:
            print(f"  {reason}", file=sys.stderr)
        return 2
    if not args.apply:
        print("（対応表だけ。移すなら --apply を付けて打ち直す）")
        return 0

    for path in rewrites:
        with open(path, encoding="utf-8", newline="") as f:
            text = f.read()
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(rewrite(text, pattern, mapping))
    for src, dst in renames:
        os.rename(src, dst)
    emptied: list[str] = []
    for path in removals:
        os.remove(path)
        parent = os.path.dirname(path)
        if not os.listdir(parent):
            os.rmdir(parent)
            emptied.append(parent)
    print(
        f"移した: 書き換え {len(rewrites)} 件、名前の変更 {len(renames)} 件、"
        f"消した記録 {len(removals)} 件、消した空のディレクトリ {len(emptied)} 件"
    )
    return 0


def rewrite(text: str, pattern: re.Pattern, mapping: dict[str, str]) -> str:
    """本文の旧い識別子を置き換える。`source_path:` の行は過去の記録なので残す。"""
    out = []
    for line in text.split("\n"):
        if line.lstrip().startswith("source_path:"):
            out.append(line)
        else:
            out.append(pattern.sub(lambda m: mapping[m.group(0)], line))
    return "\n".join(out)


if __name__ == "__main__":
    sys.exit(main())
