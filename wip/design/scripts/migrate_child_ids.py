"""承認済みの置き場の子チケットの識別子を、旧形式から新しい形へ移す（ユーザが打つ）。

旧形式 `<親>-<2 桁の連番>` を、新しい形 `<親>-<2 桁のフェーズ番号>-<2 桁のフェーズ内の連番>` へ
移す。フェーズ番号は子の frontmatter の `phase:`。同じ親・同じフェーズに複数の子があれば、
フェーズ内の連番は旧い連番の順に 1 から振り直す。

動かすもの（`<ツリー>/.ccnavi/approved/` の下）:

- `doing/` `done/` の子チケットのファイル名と、全チケットの本文
  （`ticket:`・`predecessors:`・本文中の言及）
- `phases/<親>/<子>.*`（`.risk.json` `.judge.json` `.flow.json` など）のファイル名と中身
- `flows/<子>.yml` のファイル名と中身、`events/<子>.ndjson` のファイル名と中身

`ccnavi_approved.source_path`（承認したときに提案が在った場所の記録）は過去の事実なので
書き換えない。チケットの無い記録（子が置き場に無い `phases/<親>/<親>-NN.*`）はフェーズが
分からず新しい名前を決められないので、ユーザの決定どおり消す。
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
import sys

OLD_CHILD = re.compile(r"^(?P<parent>[A-Za-z0-9][A-Za-z0-9._-]*)-(?P<seq>\d{2})$")
NEW_CHILD = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*-\d{2}-\d{2}$")
FRONT_KEY = re.compile(r"^(?P<key>[A-Za-z_]+):\s*(?P<value>.*?)\s*$")
# 記録の名前 `<子>.<種類>`（`.risk.json` など）。
RECORD = re.compile(r"^(?P<ident>[A-Za-z0-9][A-Za-z0-9._-]*?-\d{2})(?P<rest>\.[A-Za-z.]+)$")


def front_fields(path: str) -> dict[str, str]:
    """frontmatter の 1 行の欄（`ticket` `parent` `phase`）だけを読む。"""
    out: dict[str, str] = {}
    with open(path, encoding="utf-8") as f:
        lines = f.read().split("\n")
    if not lines or lines[0].strip() != "---":
        return out
    for line in lines[1:]:
        if line.strip() == "---":
            break
        m = FRONT_KEY.match(line)
        if m and m.group("key") in ("ticket", "parent", "phase"):
            out[m.group("key")] = m.group("value").strip("'\"")
    return out


def plan_mapping(approved: str) -> dict[str, str]:
    """旧い識別子 → 新しい識別子。"""
    found: dict[tuple[str, int], list[tuple[int, str]]] = {}
    for state in ("doing", "done"):
        d = os.path.join(approved, state)
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if not name.endswith(".md"):
                continue
            ident = name[:-3]
            fields = front_fields(os.path.join(d, name))
            parent = fields.get("parent", "")
            if not parent or NEW_CHILD.match(ident):
                continue
            m = OLD_CHILD.match(ident)
            if m is None or m.group("parent") != parent or not fields.get("phase", "").isdigit():
                print(f"読めない子を飛ばした: {state}/{name}", file=sys.stderr)
                continue
            phase = int(fields["phase"])
            found.setdefault((parent, phase), []).append((int(m.group("seq")), ident))
    mapping: dict[str, str] = {}
    for (parent, phase), items in sorted(found.items()):
        for n, (_, ident) in enumerate(sorted(items), start=1):
            mapping[ident] = f"{parent}-{phase:02d}-{n:02d}"
    return mapping


def rewrite(text: str, pattern: re.Pattern, mapping: dict[str, str]) -> str:
    """本文の旧い識別子を置き換える。`source_path:` の行は過去の記録なので残す。"""
    out = []
    for line in text.split("\n"):
        if line.lstrip().startswith("source_path:"):
            out.append(line)
        else:
            out.append(pattern.sub(lambda m: mapping[m.group(0)], line))
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="移す（無ければ対応表を出すだけ）")
    ap.add_argument("--approved", default=os.path.join(".ccnavi", "approved"))
    args = ap.parse_args()
    approved = args.approved
    if not os.path.isdir(approved):
        print(f"置き場が無い: {approved}", file=sys.stderr)
        return 1

    mapping = plan_mapping(approved)
    print("旧 → 新")
    for old, new in sorted(mapping.items()):
        print(f"  {old} → {new}")
    if not mapping:
        print("  （移すものは無い）")
        return 0
    # 前後が識別子の文字でないところだけを当てる（`i0055-02` が `i0055-02-01` の頭に当たらない）。
    alt = "|".join(re.escape(k) for k in sorted(mapping, key=len, reverse=True))
    pattern = re.compile(rf"(?<![A-Za-z0-9._-])(?:{alt})(?![A-Za-z0-9_]|-\d|\.\d)")

    renames: list[tuple[str, str]] = []
    rewrites: list[str] = []
    orphans: list[str] = []
    for dirpath, _dirs, files in os.walk(approved):
        for name in sorted(files):
            path = os.path.join(dirpath, name)
            stem, ext = os.path.splitext(name)
            new_name = name
            if stem in mapping:
                new_name = mapping[stem] + ext
            else:
                rec = RECORD.match(name)
                if rec and rec.group("ident") in mapping:
                    new_name = mapping[rec.group("ident")] + rec.group("rest")
                elif rec and OLD_CHILD.match(rec.group("ident")):
                    orphans.append(path)
            if new_name != name:
                renames.append((path, os.path.join(dirpath, new_name)))
            if name.endswith((".md", ".json", ".yml", ".ndjson")):
                with open(path, encoding="utf-8", newline="") as f:
                    text = f.read()
                if rewrite(text, pattern, mapping) != text:
                    rewrites.append(path)

    print("中身を書き換える:")
    for path in rewrites:
        print(f"  {path}")
    print("名前を変える:")
    for src, dst in renames:
        print(f"  {src} → {dst}")
    if orphans:
        print("消すもの（チケットが無く、フェーズが分からない記録）:")
        for path in orphans:
            print(f"  {path}")
    if not args.apply:
        print("（対応表だけ。移すなら --apply を付けて打ち直す）")
        return 0

    for path in rewrites:
        with open(path, encoding="utf-8", newline="") as f:
            text = f.read()
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(rewrite(text, pattern, mapping))
    for src, dst in renames:
        if os.path.exists(dst):
            print(f"移す先が既にある: {dst}", file=sys.stderr)
            return 1
        os.rename(src, dst)
    emptied: list[str] = []
    for path in orphans:
        os.remove(path)
        parent = os.path.dirname(path)
        if not os.listdir(parent):
            os.rmdir(parent)
            emptied.append(parent)
    print(
        f"移した: 書き換え {len(rewrites)} 件、名前の変更 {len(renames)} 件、"
        f"消した記録 {len(orphans)} 件、消した空のディレクトリ {len(emptied)} 件"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
