"""学びの蓄積の wiki（`docs/wiki/`）を、作業ツリーか git のオブジェクトから読む。

- `load_tree` は作業ツリーのファイルを読む（`--learnings`・`--lint`）
- `load_commit` は 1 つのコミットの `docs/wiki/` を、チェックアウトせずに git のオブジェクトから読む
  （SessionStart の通知）。`ls-tree` で一覧を取り、`cat-file blob` で中身を読む

どちらも、パターンと採否の記録の読み方は `wiki.build` に任せる。シンボリックリンクと、大きすぎる
ファイルは読まず、壊れたファイルとして名指しする。ネットワークには出ない。
"""

from __future__ import annotations

import os
import time

from ..infra import fsio, gitcmd
from . import wiki

# 1 本の上限（バイト）。パターンも採否の記録も、これを超えない。
FILE_LIMIT = 256 * 1024
REGULAR_MODES = ("100644", "100755")


def _stem(file: str) -> str:
    return file[:-3] if file.lower().endswith(".md") else ""


def load_tree(tree_root: str) -> wiki.Wiki:
    """作業ツリーの `docs/wiki/` を読む。無ければ `present` が False の空の `Wiki`。"""
    texts: dict[str, dict[str, str]] = {"pattern": {}, "impact": {}}
    broken: list[wiki.Broken] = []
    present = False
    for kind, rel in (("pattern", wiki.PATTERNS_DIR), ("impact", wiki.IMPACT_DIR)):
        directory = os.path.join(tree_root, *rel.split("/"))
        try:
            names = sorted(os.listdir(directory))
        except OSError:
            continue
        present = True
        for file in names:
            name = _stem(file)
            if not name:
                continue
            path = os.path.join(directory, file)
            where = f"{rel}/{file}"
            if os.path.islink(path):
                broken.append(wiki.Broken(where, kind, "シンボリックリンクは読まない"))
            elif not os.path.isfile(path):
                continue
            elif os.path.getsize(path) > FILE_LIMIT:
                broken.append(wiki.Broken(where, kind, "大きすぎて読まない"))
            else:
                text = fsio.read_text(path, errors="replace")
                if text is None:
                    broken.append(wiki.Broken(where, kind, "読めない"))
                else:
                    texts[kind][name] = text
    found = wiki.build(texts["pattern"], texts["impact"], present)
    found.broken.extend(broken)
    found.broken.sort(key=lambda b: b.file)
    return found


def commit_of(repo: str, ref: str, deadline: float | None = None) -> str:
    """参照が指すコミットの sha。決められなければ空。"""
    args = ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"]
    done = _run(repo, args, deadline)
    return done.out.strip() if done.ok else ""


def _run(repo: str, args: list[str], deadline: float | None) -> gitcmd.Done:
    if deadline is None:
        return gitcmd.run(repo, args)
    return gitcmd.run_until(repo, args, deadline)


def load_commit(repo: str, rev: str, deadline: float | None = None) -> wiki.Wiki | None:
    """コミット `rev` の `docs/wiki/` を読む。読めなければ None。

    `docs/wiki/` がそのコミットに無いのは読めた結果で、`present` が False の空の `Wiki` を返す。
    期限を過ぎたとき、git が失敗したとき、blob が読めないときは None
    （一部だけ読んだ答えは返さない）。
    """
    listed = _run(
        repo,
        [
            "ls-tree",
            "-z",
            "-l",
            rev,
            f"{wiki.PATTERNS_DIR}/",
            f"{wiki.IMPACT_DIR}/",
        ],
        deadline,
    )
    if not listed.ok:
        return None
    texts: dict[str, dict[str, str]] = {"pattern": {}, "impact": {}}
    broken: list[wiki.Broken] = []
    present = False
    for entry in listed.out.split("\0"):
        if not entry:
            continue
        meta, _, path = entry.partition("\t")
        fields = meta.split()
        directory, _, file = path.rpartition("/")
        kind = {wiki.PATTERNS_DIR: "pattern", wiki.IMPACT_DIR: "impact"}.get(directory, "")
        if len(fields) != 4 or not kind:
            continue
        present = True
        name = _stem(file)
        if not name:
            continue
        mode, type_, sha, size = fields
        if type_ != "blob" or mode not in REGULAR_MODES:
            broken.append(wiki.Broken(path, kind, "ふつうのファイルでない"))
        elif not size.isdigit() or int(size) > FILE_LIMIT:
            broken.append(wiki.Broken(path, kind, "大きすぎて読まない"))
        else:
            blob = _run(repo, ["cat-file", "blob", sha], deadline)
            if not blob.ok:
                return None
            texts[kind][name] = blob.out
    found = wiki.build(texts["pattern"], texts["impact"], present)
    found.broken.extend(broken)
    found.broken.sort(key=lambda b: b.file)
    return found


def expired(deadline: float | None) -> bool:
    """期限（`time.monotonic` の絶対時刻）を過ぎたか。"""
    return deadline is not None and time.monotonic() >= deadline
