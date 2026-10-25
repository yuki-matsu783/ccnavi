"""GitLab API v4 を人間役のトークンで直に呼ぶ部品。import しても何も起こらない。

環境変数を読まない・ファイルや一時ディレクトリを作らない・`sys.exit` しない。接続先は
`Client(base)` の引数、トークンは各メソッドの引数で受ける。`probe_gitlab.py` と、本物の GitLab の
応答を録る道具、受入シナリオの相手の準備が使う。

sh と同じ道具（curl・glab）を使わないのは、片方の不具合がもう片方に隠れないようにするため。

## 公開するもの

    Client(base, *, recorder=None, sleep=time.sleep)
        base は GitLab の根（`http://localhost:8929`）。`/api/v4` は付けない。
        recorder は `recorder(event: dict)` で、呼び出しごとに 1 回呼ばれる（応答を残す用）。
        event のキー: kind（"result" か "retry"）、method、path、payload、status、body、tag、error
        トークンは event に入れない。
    Client.call(method, path, token, payload=None, tag="") -> (status, body)
        path は `/api/v4` より後ろ（`/projects/1`）。接続の失敗は 10 秒おいて 3 回まで試し、
        それでも駄目なら status は 0、body は `{"error": ...}`。HTTP の 4xx / 5xx は例外にしない。
    Client.version(token), Client.user(token)
    Client.create_project(token, path, *, visibility="private") -> (status, body)
    Client.delete_project(token, project_id)
    Client.add_member(token, project_id, user_id, access_level=40)
    Client.create_issue(token, project_id, title, description="")
    Client.list_issues(token, project_id, *, state="opened")
    Client.create_mr(token, project_id, source_branch, target_branch, title)
    Client.find_mr(token, project_id, branch) -> dict | None
    Client.set_reviewers(token, project_id, iid, reviewer_ids)
    Client.notes(token, project_id, iid) -> list[dict]
    Client.create_discussion(token, project_id, iid, body)
    Client.resolve_discussion(token, project_id, iid, discussion_id, resolved=True)
    Client.request_changes(token, project_id, iid)   変更要求。GitLab EE の機能で、CE では 404
    Client.approve(token, project_id, iid)
    Client.reviewers(token, project_id, iid)
    has_marker(notes, marker) -> bool
    first_note_id(discussion) -> id
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable

ATTEMPTS = 3
TIMEOUT_SECONDS = 120
RETRY_WAIT_SECONDS = 10


class Client:
    def __init__(
        self,
        base: str,
        *,
        recorder: Callable[[dict], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.base = base.rstrip("/")
        self.api = self.base + "/api/v4"
        self._recorder = recorder
        self._sleep = sleep

    def _record(self, **event) -> None:
        if self._recorder is not None:
            self._recorder(event)

    def call(
        self, method: str, path: str, token: str, payload: dict | None = None, tag: str = ""
    ) -> tuple[int, object]:
        """起動直後の GitLab は遅いので、120 秒の待ちを 3 回まで試す。"""
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            self.api + path,
            data=data,
            method=method,
            headers={"PRIVATE-TOKEN": token, "Content-Type": "application/json"},
        )
        status, raw = 0, ""
        for attempt in range(ATTEMPTS):
            try:
                with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as res:
                    status, raw = res.status, res.read().decode("utf-8")
                break
            except urllib.error.HTTPError as exc:
                status, raw = exc.code, exc.read().decode("utf-8", "replace")
                break
            except (TimeoutError, OSError) as exc:
                self._record(
                    kind="retry", method=method, path=path, error=str(exc), attempt=attempt + 1
                )
                status, raw = 0, json.dumps({"error": str(exc)})
                self._sleep(RETRY_WAIT_SECONDS)
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            body = raw
        self._record(
            kind="result",
            method=method,
            path=path,
            payload=payload,
            status=status,
            body=body,
            tag=tag,
        )
        return status, body

    def version(self, token: str):
        return self.call("GET", "/version", token, tag="version")

    def user(self, token: str):
        return self.call("GET", "/user", token, tag="user")

    def create_project(self, token: str, path: str, *, visibility: str = "private"):
        return self.call(
            "POST",
            "/projects",
            token,
            {
                "name": path,
                "path": path,
                "visibility": visibility,
                "initialize_with_readme": False,
            },
            tag="project-create",
        )

    def delete_project(self, token: str, project_id: int):
        return self.call("DELETE", f"/projects/{project_id}", token, tag="project-delete")

    def add_member(self, token: str, project_id: int, user_id: int, access_level: int = 40):
        return self.call(
            "POST",
            f"/projects/{project_id}/members",
            token,
            {"user_id": user_id, "access_level": access_level},
            tag="member-add",
        )

    def create_issue(self, token: str, project_id: int, title: str, description: str = ""):
        return self.call(
            "POST",
            f"/projects/{project_id}/issues",
            token,
            {"title": title, "description": description},
            tag="issue-create",
        )

    def list_issues(self, token: str, project_id: int, *, state: str = "opened"):
        return self.call(
            "GET",
            f"/projects/{project_id}/issues?state={state}&order_by=created_at&sort=desc",
            token,
            tag="issues-list",
        )

    def create_mr(
        self, token: str, project_id: int, source_branch: str, target_branch: str, title: str
    ):
        return self.call(
            "POST",
            f"/projects/{project_id}/merge_requests",
            token,
            {"source_branch": source_branch, "target_branch": target_branch, "title": title},
            tag=f"mr-create-{source_branch}",
        )

    def find_mr(self, token: str, project_id: int, branch: str) -> dict | None:
        _, found = self.call(
            "GET",
            f"/projects/{project_id}/merge_requests?state=all&source_branch={branch}",
            token,
            tag=f"mr-list-{branch}",
        )
        return found[0] if isinstance(found, list) and found else None

    def set_reviewers(self, token: str, project_id: int, iid: int, reviewer_ids: list[int]):
        return self.call(
            "PUT",
            f"/projects/{project_id}/merge_requests/{iid}",
            token,
            {"reviewer_ids": reviewer_ids},
            tag="mr-set-reviewer",
        )

    def notes(self, token: str, project_id: int, iid: int) -> list[dict]:
        _, found = self.call(
            "GET",
            f"/projects/{project_id}/merge_requests/{iid}/notes"
            "?per_page=100&sort=asc&order_by=created_at",
            token,
            tag=f"notes-{iid}",
        )
        return found if isinstance(found, list) else []

    def create_discussion(self, token: str, project_id: int, iid: int, body: str, tag: str = ""):
        return self.call(
            "POST",
            f"/projects/{project_id}/merge_requests/{iid}/discussions",
            token,
            {"body": body},
            tag=tag or "discussion-create",
        )

    def resolve_discussion(
        self,
        token: str,
        project_id: int,
        iid: int,
        discussion_id: str,
        resolved: bool = True,
    ):
        return self.call(
            "PUT",
            f"/projects/{project_id}/merge_requests/{iid}/discussions/{discussion_id}",
            token,
            {"resolved": resolved},
            tag="discussion-resolve",
        )

    def request_changes(self, token: str, project_id: int, iid: int):
        return self.call(
            "POST",
            f"/projects/{project_id}/merge_requests/{iid}/request_changes",
            token,
            tag="request-changes",
        )

    def approve(self, token: str, project_id: int, iid: int):
        return self.call(
            "POST", f"/projects/{project_id}/merge_requests/{iid}/approve", token, tag="approve"
        )

    def reviewers(self, token: str, project_id: int, iid: int):
        return self.call(
            "GET",
            f"/projects/{project_id}/merge_requests/{iid}/reviewers",
            token,
            tag="reviewers-after-approve",
        )


def has_marker(notes: list[dict], marker: str) -> bool:
    return any(n.get("body", "").startswith(marker) for n in notes)


def first_note_id(discussion: dict):
    return discussion.get("notes", [{}])[0].get("id", "")
