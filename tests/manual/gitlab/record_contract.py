"""動いている本物の GitLab に `requests.json` の列を送り、応答の形を `recorded.json` に録る。

契約テストの録る側（仕様は `.agents/docs/gitlab-contract.md`）。手動で回す。自動テストは呼ばない。

    uv run python tests/manual/gitlab/record_contract.py            録って recorded.json に置く
    uv run python tests/manual/gitlab/record_contract.py --dry-run  置かずに標準出力へ（理由も出す）

## 前提

- `docker_gitlab.py status` で GitLab が動いていて、トークンが有効なこと。動いていなければ
  `.agents/docs/gitlab-manual.md` のとおりに起こす（この道具は起動も停止もしない）
- トークンは `docker_gitlab.py` が書く `tokens.env` から読む。同名の環境変数があればそちらを使う

## やること

1. 使い捨てのプロジェクト `p551-<乱数>` を作り、`tests/contract/fakeside.py` の `scene_state()` と
   同じ場面を組み、拡張だけの要求のために `feature` にもう 1 コミットを足す
   （`sha.*`・`blob.sha` を積む）。`merge_status` が確定し `diff_refs` が入るまで待つ
2. `requests.json` の列を順に送る（読み取り → 書き込み）。応答の形を正規化する
3. 2 回やる（2 回目は別のプロジェクト）。`reads` の各キーの形が 2 回で一致しなければ、置かない
4. 置く前の検査（期待のステータス・配列が非空・`reads` のキーが記録にある・機密・イメージの一致）
   に 1 つでも当たれば、理由を出して `recorded.json` を置かない。通れば一時ファイルから `os.replace`
5. 作ったプロジェクトを消す（自分のものだけ。途中で失敗しても消す）。`CCNAVI_PROBE_KEEP=1` で残す
"""

from __future__ import annotations

import importlib.util
import os
import secrets
import sys
import time
import urllib.parse
from datetime import UTC, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
DATA = os.path.join(ROOT, "tests", "contract", "gitlab")
PROJECT_PREFIX = "p551"

sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

import docker_gitlab  # noqa: E402
import gitlab_api  # noqa: E402


def load_module(name: str, path: str):
    """`tests` パッケージ（PATH を変える）を読み込まずに、ファイルのパスから読む。"""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


shape = load_module("contract_shape", os.path.join(ROOT, "tests", "contract", "shape.py"))


class SceneError(RuntimeError):
    pass


class Scene:
    """本物の GitLab に場面を組む。`context` は `${名前}` の置き換え先。
    場面の中身は `tests/contract/fakeside.py` の `scene_state()` と同じ。"""

    def __init__(self, client, token: str, reviewer_id: int):
        self.client, self.token, self.reviewer_id = client, token, reviewer_id
        self.project_id = 0
        self.context: dict[str, str] = {}

    def need(self, label: str, result, ok=(200, 201)):
        status, body = result
        if status not in ok:
            raise SceneError(f"場面の組み立てで失敗: {label} → HTTP {status}")
        return body

    def build(self, name: str) -> None:
        c, t = self.client, self.token
        project = self.need("プロジェクト作成", c.create_project(t, name))
        self.project_id = pid = project["id"]
        self.context["project"] = urllib.parse.quote(project["path_with_namespace"], safe="")
        self.need("レビュアーを member に", c.add_member(t, pid, self.reviewer_id))
        sha = {"main": self.need("最初のコミット", c.first_commit(t, pid, "main"))["id"]}
        for branch in ("feature", "done", "fresh"):
            sha[branch] = self.need(
                f"ブランチ {branch}",
                c.commit_file(t, pid, branch, f"{branch}.txt", branch, start_branch="main"),
            )["id"]
        # 拡張だけの要求（tree・compare・commits）のための 2 つ目のコミット（ディレクトリと履歴）
        sha["feature"] = self.need(
            "feature の 2 つ目のコミット",
            c.commit_file(t, pid, "feature", "docs/note.txt", "note\n"),
        )["id"]
        tree = self.need(
            "feature の tree",
            c.call("GET", f"/projects/{pid}/repository/tree?ref={sha['feature']}", t),
        )
        blob = next(e["id"] for e in tree if e["name"] == "feature.txt")
        issue = self.need("issue", c.create_issue(t, pid, "scene issue", "scene"))
        self.need("issue のメモ", c.create_issue_note(t, pid, issue["iid"], "scene note"))
        mr = self.need(
            "open の MR",
            c.create_mr_described(t, pid, "feature", "main", "scene mr", f"Closes #{issue['iid']}"),
        )
        done = self.need(
            "merged にする MR", c.create_mr_described(t, pid, "done", "main", "scene merged", "")
        )
        self.need("MR の判定待ち", c.wait_mr_checked(t, pid, done["iid"]))
        merged = self.need("MR のマージ", c.merge_mr(t, pid, done["iid"]))
        if merged.get("state") != "merged":
            raise SceneError("MR がマージされなかった（merged にならないまま待ちを終えた）")
        # 直前のマージの直後は 5xx を返すことがあるので、同じ操作（冪等）を数回やり直す
        for attempt in range(4):
            status, _ = c.set_reviewers(t, pid, mr["iid"], [self.reviewer_id])
            if status == 200 or attempt == 3:
                break
            time.sleep(5)
        self.need("レビュアー", (status, None), ok=(200,))
        for body in ("one", "two"):
            self.need("MR のメモ", c.create_mr_note(t, pid, mr["iid"], body))
        thread = self.need("スレッド", c.create_discussion(t, pid, mr["iid"], "thread"))
        # マージのあとで open の MR の判定がやり直されるので、確定してから録る
        status, mr_now = c.wait_mr_checked(t, pid, mr["iid"])
        if (
            status != 200
            or mr_now.get("merge_status") in ("checking", "unchecked", None)
            or not mr_now.get("diff_refs")
        ):
            raise SceneError("open の MR の merge_status / diff_refs が確定しなかった")
        self.wait_related(issue["iid"])
        self.context.update(
            {
                "mr.iid": str(mr["iid"]),
                "mr.branch": "feature",
                "mr.merged_branch": "done",
                "mr.new_branch": "fresh",
                "issue.iid": str(issue["iid"]),
                "discussion.id": thread["id"],
                "sha.main": sha["main"],
                "sha.feature": sha["feature"],
                "blob.sha": blob,
            }
        )

    def wait_related(self, issue_iid: int) -> None:
        """MR から issue への参照は非同期に作られる。`related_merge_requests` に出るまで待つ。"""
        path = f"/projects/{self.project_id}/issues/{issue_iid}/related_merge_requests"
        for _ in range(24):
            status, body = self.client.call("GET", path, self.token)
            if status == 200 and isinstance(body, list) and body:
                return
            time.sleep(5)
        raise SceneError("issue の related_merge_requests が空のまま")

    def delete(self) -> None:
        if self.project_id and os.environ.get("CCNAVI_PROBE_KEEP") != "1":
            status, _ = self.client.delete_project(self.token, self.project_id)
            if not 200 <= status < 300:
                sys.stderr.write(
                    f"プロジェクト {self.project_id} が消せなかった（HTTP {status}）\n"
                )


def send(client, token: str, req: dict, context: dict[str, str]) -> tuple[int, object]:
    path = shape.substitute(req["path"], context)
    query = shape.substitute(req.get("query", {}), context)
    if query:
        path += "?" + urllib.parse.urlencode(query)
    body = shape.substitute(req["body"], context) if "body" in req else None
    return client.call(req["method"], path, token if req["token"] == "main" else "", body)


def record_once(client, token: str, reviewer_id: int, requests: list[dict]):
    """プロジェクトを 1 つ作って列を送る。(応答 {名前: (status, body)}, 形 {名前: {...}})。"""
    scene = Scene(client, token, reviewer_id)
    answers: dict[str, tuple[int, object]] = {}
    shapes: dict[str, dict] = {}
    try:
        scene.build(f"{PROJECT_PREFIX}-{secrets.token_hex(4)}")
        for req in requests:
            code, body = send(client, token, req, scene.context)
            for var, key in req.get("save", {}).items():
                scene.context[var] = body[key]
            answers[req["name"]] = (code, body)
            shapes[req["name"]] = {"status": code, "shape": shape.normalize(body)}
    finally:
        scene.delete()
    return answers, shapes


def reads_differences(requests: list[dict], first: dict, second: dict) -> list[str]:
    """`reads` の各キーの型が、2 回の記録で一致するか。一致しないキーの一覧。"""
    out: list[str] = []
    for req in requests:
        for key in req.get("reads", []):
            a = shape.node_at(first[req["name"]]["shape"], key)
            b = shape.node_at(second[req["name"]]["shape"], key)
            ta, tb = (a or {}).get("t"), (b or {}).get("t")
            if ta != tb:
                out.append(f"{req['name']}: {key}  1 回目 {ta} / 2 回目 {tb}")
    return out


def running_image() -> str:
    done = docker_gitlab.run(
        ["docker", "inspect", "--format", "{{.Config.Image}}", docker_gitlab.CONTAINER]
    )
    return done.stdout.strip() if done.returncode == 0 else ""


def record(argv: list[str]) -> int:
    import json

    dry = "--dry-run" in argv
    values = docker_gitlab.read_env_file(docker_gitlab.tokens_path())
    for key in docker_gitlab.TOKEN_OUTPUT_KEYS:
        values[key] = os.environ.get(key) or values.get(key, "")
    base = os.environ.get("CCNAVI_PROBE_GITLAB") or docker_gitlab.BASE
    token = values.get("GITLAB_TOKEN", "")
    if not token or not values.get("CCNAVI_PROBE_REVIEWER_ID"):
        sys.stderr.write("トークンが無い。docker_gitlab.py status で確かめる\n")
        return 2
    client = gitlab_api.Client(base)
    status, version = client.version(token)
    if status != 200 or not isinstance(version, dict):
        sys.stderr.write(f"GitLab に認証できない（HTTP {status}）。何も作らず終える\n")
        return 2
    requests = shape.load_data("requests.json")["requests"]  # 全要求（sh と拡張）
    reviewer_id = int(values["CCNAVI_PROBE_REVIEWER_ID"])
    first_answers, first_shapes = record_once(client, token, reviewer_id, requests)
    _, second_shapes = record_once(client, token, reviewer_id, requests)
    problems = [
        f"2 回の記録で reads の形が違う: {d}"
        for d in reads_differences(requests, first_shapes, second_shapes)
    ]
    document = {
        "version": 1,
        "source": "gitlab",
        "gitlab_image": docker_gitlab.GITLAB_IMAGE,
        "gitlab_version": version.get("version"),
        "recorded_at": datetime.now(UTC).strftime("%Y-%m-%d"),
        "not_recorded": [n["what"] for n in shape.load_data("allow.json")["not_recorded"]],
        "requests": first_shapes,
    }
    problems += shape.placement_problems(
        requests,
        first_answers,
        document,
        running_image=running_image(),
        forbidden=shape.load_data("forbidden.json"),
    )
    if problems:
        sys.stderr.write("recorded.json は置かない:\n  " + "\n  ".join(problems) + "\n")
        return 1
    text = json.dumps(document, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    if dry:
        sys.stdout.write(text)
        return 0
    path = os.path.join(DATA, "recorded.json")
    shape.write_atomic(path, text)
    sys.stdout.write(f"置いた: {path}（{len(first_shapes)} 本。2 回の記録で reads の形が一致）\n")
    return 0


if __name__ == "__main__":
    sys.exit(record(sys.argv[1:]))
