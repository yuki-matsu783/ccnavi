"""sh（write-and-push）を通らずに実行ファイルを直に起こすテストが、状態の操作に付ける印。

origin のある親子のチケットの状態の操作（`ticket start|finish|cancel|approval-request`、
`review requested|confirm|ready`、
`--reviewed N --accept-unresolved`）は、write-and-push の sh が `--record-writes` と
`--record-tree` を付けて起こす。実行ファイルは印の無い呼び出しを断る。sh を通さないテストは、
同じ印を付けて呼ぶ。
"""

from __future__ import annotations

import itertools
import os
import subprocess

_COUNT = itertools.count()


def as_write_and_push(root: str, state: str, parent_tree: str, args) -> tuple:
    """状態の操作で、root に origin があれば印を付けた引数を返す。そうでなければそのまま。"""
    pairs = set(zip(args, args[1:], strict=False))
    is_state_op = any(
        pair in pairs
        for pair in (
            ("ticket", "start"),
            ("ticket", "finish"),
            ("ticket", "cancel"),
            ("review", "requested"),
            ("review", "confirm"),
            ("review", "ready"),
        )
    ) or (
        ("user", "reviewed") in pairs and "--accept-unresolved" in args and "--preview" not in args
    )
    if not is_state_op or "--record-tree" in args:
        return tuple(args)
    origin = subprocess.run(
        ["git", "-C", root, "config", "--get", "remote.origin.url"],
        capture_output=True,
        text=True,
    )
    if origin.returncode != 0 or not origin.stdout.strip():
        return tuple(args)
    place = os.path.join(state, "write-and-push", "self")
    os.makedirs(place, exist_ok=True)
    target = os.path.join(place, f"as-write-and-push-{next(_COUNT)}.writes")
    return ("--record-writes", target, "--record-tree", parent_tree, *args)
