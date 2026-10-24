"""親チケットの見本に `phases:`（計画が使うフェーズ定義の写し）を差し込む道具。

計画を持つ親は、計画が使う定義だけを `phases.yml` から写した `phases:` を持つ。
見本の親を書くテストが、それぞれ定義を手で写さずに済むように
ここに置く。写すのは、計画（`plan:` / `feedback:`）のステップが使う定義だけ。前の版の順序の欄
（`order` `after` `overlap` `requires`）は写さない。

実行ファイルの `--plan-order <親> --fill-phases` と同じことをテストの側で行う。実行ファイルの
関数を呼ばないのは、見本が実装の書き方に引きずられないようにするため。
"""

from __future__ import annotations

import yaml


def definitions(phases_yml: str) -> dict:
    """`phases.yml` の本文から、定義の id → 欄の辞書。"""
    try:
        data = yaml.safe_load(phases_yml) or {}
    except yaml.YAMLError:
        data = {}
    if not isinstance(data, dict) or not isinstance(data.get("phases"), dict):
        return {}
    out = {}
    for ident, body in (data.get("phases") or {}).items():
        if isinstance(body, dict):
            out[str(ident)] = dict(body)
    return out


def combined(*phases_ymls: str) -> str:
    """共通レイヤーとレイヤーの `phases.yml` を足した本文。同じ id は前のものを残す。

    判定は共通レイヤー + 親の `project:` が指すレイヤーの和を読むので、写しもその和から取る。
    """
    merged: dict = {}
    for text in phases_ymls:
        for ident, body in definitions(text).items():
            merged.setdefault(ident, body)
    if not merged:
        return ""
    return yaml.safe_dump(
        {"version": 1, "phases": merged},
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )


def used_types(front: dict) -> list[str]:
    """frontmatter の計画のステップが使う定義の名前（出てきた順、重なりなし）。"""
    names: list[str] = []
    for key in ("plan", "feedback"):
        for item in front.get(key) or []:
            name = item.get("type") if isinstance(item, dict) else item
            if isinstance(name, str) and name.strip() and name.strip() not in names:
                names.append(name.strip())
    return names


def block(phases_yml: str, names: list[str]) -> str:
    """`phases:` の行（末尾の改行つき）。`phases.yml` に無い名前は写さない。"""
    defs = definitions(phases_yml)
    picked = {name: defs[name] for name in names if name in defs}
    return yaml.safe_dump(
        {"phases": picked}, allow_unicode=True, sort_keys=False, default_flow_style=False
    )


def insert(text: str, phases_yml: str) -> str:
    """親チケットの本文の `plan:` の行の前に、計画が使う定義の `phases:` を差し込む。

    `plan:` が無い（子や計画の無い親）、もう `phases:` がある本文は、そのまま返す。
    """
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return text
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        return text
    front = yaml.safe_load("\n".join(lines[1:end])) or {}
    if not isinstance(front, dict) or "plan" not in front or "phases" in front:
        return text
    at = next(i for i in range(1, end) if lines[i].startswith("plan:"))
    added = block(phases_yml, used_types(front)).rstrip("\n").split("\n")
    return "\n".join(lines[:at] + added + lines[at:])
