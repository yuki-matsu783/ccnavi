"""GitLab API v4 を人間役のトークンで直に呼ぶ部品。import しても何も起こらない。

環境変数を読まない・ファイルや一時ディレクトリを作らない・`sys.exit` しない。接続先は
`Client(base)` の引数、トークンは各メソッドの引数で受ける。`probe_gitlab.py` と、本物の GitLab の
応答を記録する道具。受入シナリオの相手の準備と、step の終わりの相手側の状態の読み取り
（`tests/scenarios/hostsnap.py`）が使う。

sh と同じ道具（curl・glab）を使わないのは、片方の不具合がもう片方に隠れないようにするため。

## 公開するもの

    Client(base, *, recorder=None, sleep=time.sleep)
        base は GitLab の根（`http://127.0.0.1:8929`）。`/api/v4` は付けない。
        recorder は `recorder(event: dict)` で、呼び出しごとに 1 回呼ばれる（応答を残す用）。
        event のキー: kind（"result" か "retry"）、method、path、payload、status、body、tag、error
        トークンは event に入れない。
    Client.call(method, path, token, payload=None, tag="") -> (status, body)
        path は `/api/v4` より後ろ（`/projects/1`）。接続の失敗は 10 秒おいて 3 回まで試し、
        それでも失敗するなら status は 0、body は `{"error": ...}`。
        HTTP の 4xx / 5xx は例外にしない。
    Client.version(token), Client.user(token)
    Client.create_project(token, path, *, visibility="private") -> (status, body)
    Client.delete_project(token, project_id)
    Client.add_member(token, project_id, user_id, access_level=40)
    Client.create_issue(token, project_id, title, description="")
    Client.list_issues(token, project_id, *, state="opened", per_page=None) -> (status, body)
        per_page を渡さないと GitLab の既定の 20 件になる。
    Client.create_mr(token, project_id, source_branch, target_branch, title)
    Client.find_mr(token, project_id, branch) -> dict | None
    Client.list_mrs(token, project_id, *, state="all") -> (status, body)
    Client.set_reviewers(token, project_id, iid, reviewer_ids)
    Client.notes(token, project_id, iid) -> list[dict]
    Client.create_discussion(token, project_id, iid, body)
    Client.resolve_discussion(token, project_id, iid, discussion_id, resolved=True)
    Client.discussions(token, project_id, iid) -> list[dict]   スレッドの一覧（notes を持つ）
    Client.list_discussions(token, project_id, iid) -> (status, body)
        `discussions` と同じ取得だが、status も返す（読めなかったことと空を区別したいとき）
    Client.tree(token, project_id, ref, path="", recursive=False) -> list[str]   ブランチの一覧
    Client.request_changes(token, project_id, iid)   変更要求。GitLab EE の機能で、CE では 404
    Client.approve(token, project_id, iid)
    Client.reviewers(token, project_id, iid)
    Client.first_commit(token, project_id, branch="main")           空のリポジトリの最初のコミット
    Client.commit_file(token, project_id, branch, file_path, content, *, start_branch="")
        start_branch から branch を切って、ファイル 1 つを足す（差分のあるブランチを作る）
    Client.create_mr_described(token, project_id, source, target, title, description)
    Client.get_mr(token, project_id, iid)
    Client.merge_mr(token, project_id, iid, *, attempts=12)   マージして merged になるまで待つ
    Client.create_issue_note(token, project_id, iid, body)
    Client.create_mr_note(token, project_id, iid, body)
    Client.wait_mr_checked(token, project_id, iid, *, attempts=24)
        merge_status が確定し diff_refs が入るまで待つ
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
        """起動直後の GitLab は応答が遅いので、1 回の待ちは 120 秒まで。
        GET は失敗したら 3 回まで試す。書き込みは送り直すと二重に作られるので 1 回で止める。"""
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
                if method != "GET":
                    status, raw = 0, json.dumps({"error": str(exc)})
                    break
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

    def list_issues(
        self, token: str, project_id: int, *, state: str = "opened", per_page: int | None = None
    ):
        query = f"state={state}&order_by=created_at&sort=desc"
        if per_page is not None:
            query += f"&per_page={per_page}"
        return self.call("GET", f"/projects/{project_id}/issues?{query}", token, tag="issues-list")

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

    def get_branch(self, token: str, project_id: int, branch: str):
        return self.call(
            "GET",
            f"/projects/{project_id}/repository/branches/{branch}",
            token,
            tag=f"branch-{branch}",
        )

    def find_mr(self, token: str, project_id: int, branch: str) -> dict | None:
        _, found = self.call(
            "GET",
            f"/projects/{project_id}/merge_requests?state=all&source_branch={branch}",
            token,
            tag=f"mr-list-{branch}",
        )
        return found[0] if isinstance(found, list) and found else None

    def list_mrs(self, token: str, project_id: int, *, state: str = "all"):
        return self.call(
            "GET",
            f"/projects/{project_id}/merge_requests?state={state}&per_page=100",
            token,
            tag="mrs-list",
        )

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

    def list_discussions(self, token: str, project_id: int, iid: int):
        return self.call(
            "GET",
            f"/projects/{project_id}/merge_requests/{iid}/discussions?per_page=100",
            token,
            tag=f"discussions-{iid}",
        )

    def discussions(self, token: str, project_id: int, iid: int) -> list[dict]:
        _, found = self.list_discussions(token, project_id, iid)
        return found if isinstance(found, list) else []

    def tree(
        self, token: str, project_id: int, ref: str, path: str = "", recursive: bool = False
    ) -> list[str]:
        """ブランチの一覧（ファイルとディレクトリの名前。path を渡すとその下）。
        無いブランチ・読めないときは空。100 件まで。"""
        query = f"ref={ref}&per_page=100" + ("&recursive=true" if recursive else "")
        found = self.call(
            "GET",
            f"/projects/{project_id}/repository/tree?{query}" + (f"&path={path}" if path else ""),
            token,
            tag=f"tree-{ref}",
        )[1]
        if not isinstance(found, list):
            return []
        return [item.get("path", "") for item in found]

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

    # ---- 契約テストの録り（record_contract.py）が場面を組むために足したもの。既存は変えていない

    def first_commit(self, token: str, project_id: int, branch: str = "main"):
        """空のリポジトリに最初のコミットを作り、`branch` を作る。"""
        return self.commit_file(token, project_id, branch, "README.md", "contract\n")

    def commit_file(
        self,
        token: str,
        project_id: int,
        branch: str,
        file_path: str,
        content: str,
        *,
        start_branch: str = "",
    ):
        """ファイル 1 つを足すコミット。`start_branch` があればそこから `branch` を切る。"""
        payload = {
            "branch": branch,
            "commit_message": f"contract {branch}",
            "actions": [{"action": "create", "file_path": file_path, "content": content}],
        }
        if start_branch:
            payload["start_branch"] = start_branch
        return self.call(
            "POST",
            f"/projects/{project_id}/repository/commits",
            token,
            payload,
            tag=f"commit-{branch}",
        )

    def create_mr_described(
        self,
        token: str,
        project_id: int,
        source_branch: str,
        target_branch: str,
        title: str,
        description: str,
    ):
        return self.call(
            "POST",
            f"/projects/{project_id}/merge_requests",
            token,
            {
                "source_branch": source_branch,
                "target_branch": target_branch,
                "title": title,
                "description": description,
            },
            tag=f"mr-create-{source_branch}",
        )

    def get_mr(self, token: str, project_id: int, iid: int):
        return self.call(
            "GET", f"/projects/{project_id}/merge_requests/{iid}", token, tag=f"mr-get-{iid}"
        )

    def merge_mr(self, token: str, project_id: int, iid: int, *, attempts: int = 12):
        """MR をマージし、`merged` になるまで待つ。mergeability の判定は非同期で、できていないうちは
        405 / 406 が返るので、5 秒おいて `attempts` 回まで試す。(status, body) は最後の MR。"""
        status: int = 0
        for _ in range(attempts):
            status, _body = self.call(
                "PUT", f"/projects/{project_id}/merge_requests/{iid}/merge", token, {}, tag="merge"
            )
            if status == 200:
                break
            self._sleep(5)
        if status != 200:
            return status, _body
        for _ in range(attempts):
            status, body = self.get_mr(token, project_id, iid)
            if status == 200 and isinstance(body, dict) and body.get("state") == "merged":
                return status, body
            self._sleep(2)
        return status, body

    def create_issue_note(self, token: str, project_id: int, iid: int, body: str):
        return self.call(
            "POST",
            f"/projects/{project_id}/issues/{iid}/notes",
            token,
            {"body": body},
            tag="issue-note",
        )

    def create_mr_note(self, token: str, project_id: int, iid: int, body: str):
        return self.call(
            "POST",
            f"/projects/{project_id}/merge_requests/{iid}/notes",
            token,
            {"body": body},
            tag="mr-note",
        )

    def wait_mr_checked(self, token: str, project_id: int, iid: int, *, attempts: int = 24):
        """`merge_status` が `checking` / `unchecked` でなくなり、`diff_refs` が入るまで待つ。
        着かなければ最後の (status, body) を返す。判定は呼び出し側が `body` を見て行う。"""
        status, body = 0, {}
        for _ in range(attempts):
            status, body = self.get_mr(token, project_id, iid)
            if (
                status == 200
                and isinstance(body, dict)
                and body.get("merge_status") not in ("checking", "unchecked", None)
                and body.get("diff_refs")
            ):
                break
            self._sleep(5)
        return status, body


def has_marker(notes: list[dict], marker: str) -> bool:
    return any(n.get("body", "").startswith(marker) for n in notes)


def first_note_id(discussion: dict):
    return discussion.get("notes", [{}])[0].get("id", "")
