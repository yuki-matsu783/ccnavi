"""`requests.json` の列を `tests/fake_gitlab.py` に送り、正規化した形を返す。

模擬の側の状態（場面）をここで組む。場面の中身は `docs/42_claude/gitlab-contract.md`。
本物の側は、録る側が同じ場面を `Client` で作る。

    uv run python -m tests.contract.fakeside        recorded.sample.json を作り直す
"""

from __future__ import annotations

import http.client
import json
import os
import sys
import tempfile
import urllib.parse
from collections.abc import Callable

from tests import fake_gitlab
from tests.contract import shape

PROJECT = "ns/proj"
SAMPLE = os.path.join(shape.DATA, "recorded.sample.json")
ISO = "2026-01-02T03:04:05.000Z"


def scene_state() -> dict:
    """場面: open の MR 1 本（スレッド・レビュアー・メモ 2 件）、merged の MR、issue 1 件。"""
    user = {"id": 1, "username": "fake-user"}
    return {
        "project": PROJECT,
        "seq": 100,
        "issues": [
            {
                "iid": 1,
                "title": "scene issue",
                "description": "scene",
                "state": "opened",
                "created_at": ISO,
                "web_url": "http://x/issues/1",
                "notes": [{"id": 90, "body": "scene note", "created_at": ISO}],
            }
        ],
        "mrs": [
            {
                "iid": 1,
                "state": "opened",
                "source_branch": "feature",
                "target_branch": "main",
                "title": "scene mr",
                "description": "Closes #1",
                "web_url": "http://x/merge_requests/1",
                "created_at": ISO,
                "discussions": [
                    {
                        "id": "d50",
                        "individual_note": False,
                        "notes": [
                            {
                                "id": 50,
                                "body": "thread",
                                "resolvable": True,
                                "resolved": False,
                                "created_at": ISO,
                                "author": user,
                            }
                        ],
                    }
                ],
                "reviewers": [
                    {"user": user, "state": "unreviewed", "created_at": ISO, "updated_at": ISO}
                ],
                "notes": [
                    {"id": 60, "body": "one", "created_at": ISO},
                    {"id": 61, "body": "two", "created_at": ISO},
                ],
            },
            {
                "iid": 2,
                "state": "merged",
                "source_branch": "done",
                "target_branch": "main",
                "title": "scene merged",
                "description": "",
                "web_url": "http://x/merge_requests/2",
                "created_at": ISO,
                "merged_at": ISO,
                "discussions": [],
                "reviewers": [],
                "notes": [],
            },
        ],
    }


def scene_context() -> dict[str, str]:
    return {
        "project": urllib.parse.quote(PROJECT, safe=""),
        "mr.iid": "1",
        "mr.branch": "feature",
        "mr.merged_branch": "done",
        "mr.new_branch": "fresh",
        "issue.iid": "1",
        "discussion.id": "d50",
    }


def load_requests() -> list[dict]:
    return shape.load_data("requests.json")["requests"]


def run(requests: list[dict], tamper: Callable[[str, object], object] | None = None) -> dict:
    """`{名前: {"status", "shape"}}`。`tamper(名前, 本文)` は応答の本文を形にする前に書き換える。"""
    context = scene_context()
    out: dict = {}
    with tempfile.TemporaryDirectory() as tmp:
        server = fake_gitlab.start(
            os.path.join(tmp, "state.json"), initial_state=scene_state(), skew=0
        )
        try:
            for req in requests:
                status, body = send(server, req, context)
                for var, key in req.get("save", {}).items():
                    context[var] = body[key]
                if tamper is not None:
                    body = tamper(req["name"], body)
                out[req["name"]] = {"status": status, "shape": shape.normalize(body)}
        finally:
            server.stop()
    return out


def send(server, req: dict, context: dict[str, str]) -> tuple[int, object]:
    path = "/api/v4" + shape.substitute(req["path"], context)
    query = shape.substitute(req.get("query", {}), context)
    if query:
        path += "?" + urllib.parse.urlencode(query)
    headers = {}
    if req["token"] == "main":
        headers["PRIVATE-TOKEN"] = server.token
    raw = None
    if "body" in req:
        raw = json.dumps(shape.substitute(req["body"], context))
        headers["Content-Type"] = "application/json"
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    try:
        conn.request(req["method"], path, body=raw, headers=headers)
        res = conn.getresponse()
        return res.status, json.loads(res.read() or b"null")
    finally:
        conn.close()


def sample_document(shapes: dict) -> dict:
    return {
        "version": 1,
        "source": "sample",
        "gitlab_image": None,
        "recorded_at": None,
        "not_recorded": [n["what"] for n in shape.load_data("allow.json")["not_recorded"]],
        "requests": shapes,
    }


def main() -> int:
    text = json.dumps(sample_document(run(load_requests())), ensure_ascii=False, indent=1)
    with open(SAMPLE, "w", encoding="utf-8", newline="\n") as f:
        f.write(text + "\n")
    sys.stdout.write(f"書いた: {SAMPLE}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
