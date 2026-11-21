"""Chrome の入口と手元の CLI を比べるテストの土台（CoreHarness・STAMP）。

テストはここに持たない。承認・Chrome の入口との突き合わせ・置き場・記録層のテストは
Go（go/internal/clitest/ticket/phases/chrome_test.go）へ移した。ほかの Python のテストが
継いで使うので、土台だけを残す（それらが Go へ移ったら、このファイルごと消す）。
"""

from __future__ import annotations

import json
import os

from tests.ticket.test_phases import PhaseHarness
from tests.ticketsample import child_text, load_chrome

STAMP = "2026-09-29T12:00:00+0900"
PLACES = (".ccnavi", "wip")


def _place_of(chrome):
    """入口（`handle` の `op: placement`）が答える置き場。`_placement` の引数の形に依らない。"""
    request = {"schema": chrome.SCHEMA, "op": "placement"}
    answer = json.loads(chrome.handle(json.dumps(request), os.devnull))
    if "error" in answer:
        raise AssertionError(f"置き場を読めない: {answer['error']}")
    return answer["placement"]


def _text(raw: bytes) -> str:
    """ファイルの中身を本文にする。Windows が書く改行（CRLF）は LF に揃える。"""
    return raw.decode().replace("\r\n", "\n")


def _files(tree_root, prefixes=PLACES):
    """ツリーの置き場のファイル（相対パス → バイト列）。"""
    found = {}
    for prefix in prefixes:
        base = os.path.join(tree_root, prefix)
        for current, dirs, names in os.walk(base):
            dirs[:] = [d for d in dirs if d != ".git"]
            for name in names:
                full = os.path.join(current, name)
                rel = os.path.relpath(full, tree_root).replace(os.sep, "/")
                with open(full, "rb") as f:
                    found[rel] = f.read()
    return found


class CoreHarness(PhaseHarness):
    """親のワークツリー（i0001）を持つリポジトリで、コアを直に呼ぶ道具。"""

    def trees(self):
        """ブランチの名前 → ツリーのルート。ワークスペースルートは main。"""
        found = {"main": self.root}
        base = os.path.join(self.root, ".claude", "worktrees")
        for name in sorted(os.listdir(base)):
            found[name] = os.path.join(base, name)
        return found

    def disk(self):
        return {name: _files(path) for name, path in self.trees().items()}

    def diff(self, before, after):
        """2 つの `disk` の違いを、Changes の `per_branch` と同じ形（中身は本文）で。"""
        out = {}
        for name in sorted(set(before) | set(after)):
            old, new = before.get(name, {}), after.get(name, {})
            rows = []
            for path in sorted(set(old) | set(new)):
                if path in old and path not in new:
                    rows.append({"op": "delete", "path": path})
                elif path not in old:
                    rows.append({"op": "create", "path": path, "content": _text(new[path])})
                elif old[path] != new[path]:
                    rows.append({"op": "update", "path": path, "content": _text(new[path])})
            if rows:
                out[name] = rows
        return out

    def reviewed_phase_one(self):
        """フェーズ 1（MR で見る設計）の子を閉じて合流し、レビューを依頼したところまで進める。"""
        self.family(plan=["design"])
        self.propose("i0001-01-01", child_text("i0001-01-01", "i0001", 1, ["wip/design/*"]))
        self.commit_parent()
        self.assertEqual(self.approve().returncode, 0)
        self.run_child("i0001-01-01", [("wip/design/plan.md", "d\n")])
        self.assertEqual(self.close_child("i0001-01-01").returncode, 0)
        self.commit_parent("close 01")
        self.merge("i0001-01-01")
        fixture = self.remote()
        self.assertEqual(self.request(fixture, 1).returncode, 0)
        return fixture

    # ---- Chrome の要求

    def chrome_request(self, op, family, heads=None, **extra):
        """手元のツリーから、拡張が組むのと同じ形の要求を作る（統合先は main）。

        ブランチの先頭は既定で `0` を並べたもの。
        `heads` で名前ごとに実際の先頭を渡せる（レビュー済みは
        依頼時の先頭と比べるので、親のブランチの実際の先頭が要る）。
        """
        chrome = load_chrome()
        place = _place_of(chrome)
        branches = {}
        for name, path in self.trees().items():
            if name == "main":
                wanted = tuple(p + "/" for p in place["integration_paths"])
                files = {
                    rel: body.decode()
                    for rel, body in _files(path, (".ccnavi", ".claude")).items()
                    if rel.startswith(wanted) or rel in place["integration_files"]
                }
            else:
                wanted = tuple(p + "/" for p in place["branch_paths"])
                files = {
                    rel: body.decode()
                    for rel, body in _files(path).items()
                    if rel.startswith(wanted)
                }
            head = (heads or {}).get(name, "0" * 40)
            branches[name] = {"head": head, "files": dict(sorted(files.items()))}
        request = {
            "schema": chrome.SCHEMA,
            "op": op,
            "family": family,
            "stamp": STAMP,
            "snapshot": {
                "integration": {"name": "main", "source": "setting", "head": "0" * 40},
                "branches": branches,
                "absent": [],
            },
            **extra,
        }
        return request

    def ask_chrome(self, request):
        root = os.path.join(self.root, "memfs")
        answer = json.loads(load_chrome().handle(json.dumps(request), root))
        self.assertNotIn("error", answer, answer)
        return answer
