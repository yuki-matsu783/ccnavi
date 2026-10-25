"""見本（`tests/sh/gitlab_host.py` とフィクスチャ）の応答の形を、本物の記録と比べる。

仕様は `docs/42_claude/gitlab-contract.md` の「見本」の節。`tests/sh` は import しない
（`test_layout` が禁じる）ので、`gitlab_host.py` はパスを指して読み込む。

見る観点:

1. 比べる場面・除く場面・要求名が、見本の実体と合っている（一覧に漏れ・実在しない名前が無い）
2. 見本の応答が、`recorded.json` と `reads` の範囲で一致する（差は許可リストの `host_entries`）
3. 空で通らない: 要求ごとに、比べたキーの数が 0 でない
4. 変異: 見本の応答を加工すると、場面名・要求名・キー・違いの種類が出て落ちる
"""

from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
import urllib.parse

from tests.contract import shape

ROOT = os.path.dirname(os.path.dirname(HERE := os.path.dirname(os.path.abspath(__file__))))
HOST_PATH = os.path.join(ROOT, "tests", "sh", "gitlab_host.py")

# 比べる場面。`paged` は `discussions` の 1 ページ目だけを比べる
COMPARED_SCENES = (
    "nested",
    "paged",
    "replies",
    "replies-accepted",
    "requested-changes",
    "resolved",
)
# 異常な値をわざと入れた見本なので、本物の形とは比べない
EXCLUDED_SCENES = ("hostile", "odd-types", "impostor")

# 場面ごとに答えを取る要求（見本が実際に答えるもの）
SCENE_REQUESTS = ("user", "mrs_opened", "discussions", "reviewers")
# 場面に依らない要求。状態ファイルに溜める（場面は 1 つだけ使う）
STATE_REQUESTS = ("notes_post", "notes_get")
# 見本が答えるが比べない要求（理由は文書の表）
EXCLUDED_REQUESTS = ("mrs_merged", "project")
BODY = "contract: host sample"
SOURCE_PROJECT_ID = 42


def load_host():
    spec = importlib.util.spec_from_file_location("ccnavi_contract_gitlab_host", HOST_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_json(host, scene: str, name: str):
    with open(os.path.join(host.SCENES, scene, name), encoding="utf-8") as f:
        return json.load(f)


def scene_context(host, scene: str) -> dict[str, str]:
    """`scene.json` と `mrs.json` から作る文脈（`fakeside.scene_context` は模擬用）。"""
    meta = read_json(host, scene, "scene.json")
    mine = [
        m
        for m in read_json(host, scene, "mrs.json")
        if m.get("source_project_id") == SOURCE_PROJECT_ID
    ]
    return {
        "project": urllib.parse.quote(f"{meta['namespace']}/{meta['project']}", safe=""),
        "mr.iid": str(mine[0]["iid"]),
        "mr.branch": meta["branch"],
    }


def requests_by_name(names: tuple[str, ...]) -> list[dict]:
    from tests.contract import fakeside

    by_name = {r["name"]: r for r in fakeside.load_requests()}
    missing = [n for n in names if n not in by_name]
    if missing:
        raise AssertionError(f"requests.json に無い要求: {missing}")
    return [by_name[n] for n in names]


def ask(host, scene: str, req: dict, context: dict[str, str], state: str):
    path = shape.substitute(req["path"], context)
    query = shape.substitute(req.get("query", {}), context)
    if query:
        path += "?" + urllib.parse.urlencode(query)
    body = json.dumps(shape.substitute(req["body"], context)) if "body" in req else ""
    return host.answer(scene, req["method"], path, body, state)


def run(host, tamper=None) -> dict[str, dict]:
    """`{場面: {名前: {"status", "shape"}}}`。`tamper(名前, 本文)` は形にする前に本文を書き換える。

    `notes_post`・`notes_get` は場面に依らないので、比べる場面の最初の 1 つで 1 回だけ打つ。
    """
    scene_reqs = requests_by_name(SCENE_REQUESTS)
    state_reqs = requests_by_name(STATE_REQUESTS)
    out: dict[str, dict] = {}
    with tempfile.TemporaryDirectory() as tmp:
        state = os.path.join(tmp, "state.json")
        for i, scene in enumerate(COMPARED_SCENES):
            context = scene_context(host, scene)
            answers = {}
            for req in scene_reqs + (state_reqs if i == 0 else []):
                status, body = ask(host, scene, req, context, state)
                if tamper is not None:
                    body = tamper(req["name"], body)
                answers[req["name"]] = {"status": status, "shape": shape.normalize(body)}
            out[scene] = answers
    return out


def read_keys() -> dict[str, list[str]]:
    from tests.contract import fakeside

    return shape.reads_of(fakeside.load_requests())


def compared_count(name: str, reads: list[str], recorded: dict, got: dict) -> int:
    """`reads` のうち、見本にも本物の記録にも存在するキーの数。"""
    mine = shape.key_paths(got["shape"])
    real = shape.key_paths(recorded["shape"])
    return len([k for k in reads if k in mine and k in real])


def gather(recorded: dict, ran: dict[str, dict]):
    """場面をまたいで差を集める。(`{名前: [(キー, 種類)]}`, `{(名前, キー, 種類): [場面]}`)。"""
    reads = read_keys()
    found: dict[str, list[tuple[str, str]]] = {}
    where: dict[tuple[str, str, str], list[str]] = {}
    for scene, answers in ran.items():
        wanted = {n: recorded[n] for n in answers}
        diffs = shape.compare_all(wanted, answers, {n: reads[n] for n in answers})
        for name, items in diffs.items():
            for key, kind in items:
                where.setdefault((name, key, kind), []).append(scene)
                if (key, kind) not in found.setdefault(name, []):
                    found[name].append((key, kind))
    return found, where


def counts(recorded: dict, ran: dict[str, dict]) -> dict[tuple[str, str], int]:
    reads = read_keys()
    return {
        (scene, name): compared_count(name, reads[name], recorded[name], got)
        for scene, answers in ran.items()
        for name, got in answers.items()
    }


def problems(recorded: dict, ran: dict[str, dict], entries: list[dict]) -> list[str]:
    """失敗の理由の一覧。空なら一致している。"""
    out: list[str] = []
    requests = requests_by_name(SCENE_REQUESTS + STATE_REQUESTS)
    for scene, answers in ran.items():
        out += [
            f"{scene}: {p}"
            for p in shape.reads_problems([r for r in requests if r["name"] in answers], answers)
        ]
    for (scene, name), n in counts(recorded, ran).items():
        if n == 0:
            out.append(f"{scene}/{name}: 比べたキーが 0 件")
    found, where = gather(recorded, ran)
    left, allow_problems = shape.apply_allow(found, entries)
    out += allow_problems
    for name, items in left.items():
        for key, kind in items:
            out.append(f"{name} {key} {kind}（場面: {', '.join(where[(name, key, kind)])}）")
    return out


class SceneListTest(unittest.TestCase):
    def test_scenes_are_decided_one_by_one(self):
        host = load_host()
        self.assertEqual(set(host.scene_names()), set(COMPARED_SCENES) | set(EXCLUDED_SCENES))

    def test_excluded_scenes_exist_and_compared_scenes_are_not_empty(self):
        host = load_host()
        self.assertTrue(COMPARED_SCENES)
        self.assertLessEqual(set(EXCLUDED_SCENES), set(host.scene_names()))
        self.assertFalse(set(COMPARED_SCENES) & set(EXCLUDED_SCENES))

    def test_requests_are_in_requests_json(self):
        requests_by_name(SCENE_REQUESTS + STATE_REQUESTS + EXCLUDED_REQUESTS)

    def test_an_unknown_request_name_fails(self):
        with self.assertRaises(AssertionError):
            requests_by_name(("no_such_request",))


class HostSampleTest(unittest.TestCase):
    PATH = os.path.join(shape.DATA, "recorded.json")

    def setUp(self):
        if not os.path.exists(self.PATH):
            self.skipTest("recorded.json が無い（gitlab-contract.md）")
        self.recorded = shape.load_data("recorded.json")["requests"]
        self.entries = shape.load_data("allow.json")["host_entries"]
        self.host = load_host()

    def test_the_sample_agrees_with_the_recording_except_for_the_allow_list(self):
        self.assertEqual([], problems(self.recorded, run(self.host), self.entries))

    def test_every_request_in_every_scene_compared_some_keys(self):
        n = counts(self.recorded, run(self.host))
        self.assertTrue(n)
        self.assertEqual({}, {k: v for k, v in n.items() if v == 0})
        self.assertEqual({r for _, r in n}, set(SCENE_REQUESTS + STATE_REQUESTS))

    def test_notes_are_posted_before_they_are_read(self):
        ran = run(self.host)
        first = ran[COMPARED_SCENES[0]]
        self.assertEqual(201, first["notes_post"]["status"])
        self.assertIn("items", first["notes_get"]["shape"])
        for scene in COMPARED_SCENES[1:]:
            self.assertNotIn("notes_post", ran[scene])

    def test_a_key_the_sample_lacks_is_reported_with_the_scene(self):
        def drop_system(name, body):
            if name == "discussions":
                for d in body:
                    for n in d["notes"]:
                        n.pop("system", None)
            return body

        out = problems(self.recorded, run(self.host, drop_system), self.entries)
        hit = [p for p in out if "discussions" in p and "[].notes[].system missing" in p]
        self.assertTrue(hit, out)
        self.assertIn(COMPARED_SCENES[0], hit[0])

    def test_an_empty_discussions_fails_with_zero_keys(self):
        def empty(name, body):
            return [] if name == "discussions" else body

        out = problems(self.recorded, run(self.host, empty), self.entries)
        self.assertTrue([p for p in out if "discussions" in p and "0 件" in p], out)


if __name__ == "__main__":
    unittest.main()
