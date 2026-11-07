"""テストの準備で、操作記録（`logs/state/ops/<操作ID>.json`）を実行ファイルと同じ形で書く。

実行ファイルの `ops begin` は、判断の中身（`shown`・`choices`・`plan`）を受けない。受け入れの計画を
代役で書く試験は、判断を載せた記録が要るので、ここで書く。
形は go/internal/tickets/model/oplog と同じ。食い違えば、記録を読む `ccnavi ops …` が断って
試験が落ちる。

このファイルは標準ライブラリだけを読む。sh の代役が
`python oprecord.py <state> <計画の出力先> <記録と計画の JSON>` で起こし、記録を作って、
その操作ID を入れた計画を書く（JSON は `{"begin": {begin の引数}, "plan": {計画の欄}}`）。
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from typing import Any

VERSION = 1


def begin(
    state: str,
    kind: str,
    *,
    repo: dict[str, str],
    parent: str = "",
    phase: int = 0,
    mr: int = 0,
    request_head: str = "",
    shown: list[dict[str, Any]] | None = None,
    choices: dict[str, str] | None = None,
    plan: dict[str, Any] | None = None,
    stages: list[tuple[str, str]] | None = None,
) -> str:
    """操作を始める記録を書き、操作ID を返す。stages は（段階ID, 種類）を送る順に並べたもの。"""
    op_id = uuid.uuid4().hex
    now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    record = {
        "version": VERSION,
        "id": op_id,
        "kind": kind,
        "repo": repo,
        "parent": parent,
        "phase": phase,
        "mr": mr,
        "request_head": request_head,
        "shown": shown or [],
        "choices": choices or {},
        "plan": plan or {},
        "applied": {},
        "stages": [
            {
                "id": sid,
                "kind": skind,
                "state": "pending",
                "url": "",
                "account": "",
                "tries": 0,
                "at": "",
            }
            for sid, skind in (stages or [])
        ],
        "created_at": now,
        "updated_at": now,
    }
    directory = os.path.join(state, "ops")
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, f"{op_id}.json"), "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(record, ensure_ascii=False, indent=1) + "\n")
    return op_id


def record_path(state: str, op_id: str) -> str:
    return os.path.join(state, "ops", f"{op_id}.json")


def ids(state: str) -> list[str]:
    """置いてある記録の操作ID（書いた順）。"""
    directory = os.path.join(state, "ops")
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    found = [
        (os.path.getmtime(os.path.join(directory, name)), name[: -len(".json")])
        for name in names
        if name.endswith(".json")
    ]
    return [op_id for _, op_id in sorted(found)]


def load(state: str, op_id: str) -> dict[str, Any] | None:
    """記録を読む。無ければ None。"""
    try:
        with open(record_path(state, op_id), encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def save(state: str, record: dict[str, Any]) -> None:
    """記録を書き戻す（試験の準備で段階や適用の欄を書き換えたあと）。"""
    with open(record_path(state, record["id"]), "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(record, ensure_ascii=False, indent=1) + "\n")


def main(argv: list[str]) -> int:
    state, plan_path, spec_path = argv
    with open(spec_path, encoding="utf-8") as f:
        spec = json.load(f)
    args = spec["begin"]
    op = begin(state, args.pop("kind"), **args)
    with open(plan_path, "w", encoding="utf-8") as f:
        json.dump({**spec["plan"], "op": op}, f)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
