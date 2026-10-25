"""要求の列・模擬（`tests/fake_gitlab.py`）・記録の比べる側。

これが証明するのは、比較器と要求の列が動くことだけ。`recorded.sample.json` は
模擬の応答から作ったので、サンプルとの一致は「模擬が本物に近い」ことを示さない。
本物の記録 `recorded.json` ができたら、同じ比べ方で本物との差が出る。

見る観点:

1. 要求の列の書式（名前が一意・トークン・`used_by`・置き換えの名前が場面にある）
2. サンプルが模擬の応答と一致し、いまの模擬から作り直しても変わらない
3. 変異: 1 変異 1 種類の違いが、期待したキーと種類で出る（サンプル側から削る・模擬側に足す）
4. 機密の検査: サンプルと要求の列に禁止文字列が無く、混ぜると落ちる
5. `recorded.json` があれば、許可リストを当てて一致する
"""

from __future__ import annotations

import copy
import json
import os
import unittest

from tests.contract import fakeside, shape

NAMES = {"project", "mr.iid", "mr.branch", "mr.merged_branch", "mr.new_branch", "issue.iid"}
SAVED = {"user.id", "discussion.id"}


def load_sample() -> dict:
    return shape.load_data("recorded.sample.json")


def sample_requests() -> dict:
    return load_sample()["requests"]


def set_shape(doc: dict, request: str, mutate) -> dict:
    out = copy.deepcopy(doc)
    mutate(out[request]["shape"])
    return out


class RequestsTest(unittest.TestCase):
    def test_format(self):
        requests = fakeside.load_requests()
        names = [r["name"] for r in requests]
        self.assertEqual(len(names), len(set(names)), "名前が重複している")
        self.assertGreaterEqual(len(requests), 23)
        context = set(fakeside.scene_context()) | SAVED
        for r in requests:
            with self.subTest(r["name"]):
                self.assertIn(r["method"], ("GET", "POST", "PUT"))
                self.assertIn(r["token"], ("main", "none"))
                self.assertTrue(r["used_by"])
                self.assertLessEqual(set(r["used_by"]), {"sh", "chrome"})
                text = json.dumps(r)
                self.assertFalse(
                    set(shape.PLACEHOLDER.findall(text)) - context, "場面に無い置き換えの名前"
                )
                self.assertNotIn("/projects/ns", text, "実体の名前を入れない（${project} を使う）")

    def test_writes_come_after_reads(self):
        methods = [r["method"] for r in fakeside.load_requests()]
        first_write = next(i for i, m in enumerate(methods) if m != "GET")
        self.assertEqual({"GET"}, set(methods[:first_write]))
        self.assertNotIn("GET", methods[first_write:])


class SampleTest(unittest.TestCase):
    def test_sample_agrees_with_the_fake(self):
        found = shape.compare_all(sample_requests(), fakeside.run(fakeside.load_requests()))
        self.assertEqual({}, found)

    def test_sample_is_regenerated_without_change(self):
        fresh = fakeside.sample_document(fakeside.run(fakeside.load_requests()))
        self.assertEqual(
            load_sample(),
            fresh,
            "模擬を変えたら `uv run python -m tests.contract.fakeside` でサンプルを作り直す",
        )

    def test_sample_says_what_it_is(self):
        doc = load_sample()
        self.assertEqual("sample", doc["source"])
        self.assertIn("headers", doc["not_recorded"])

    def test_every_request_has_a_recorded_answer(self):
        names = {r["name"] for r in fakeside.load_requests()}
        self.assertEqual(names, set(sample_requests()))


class MutationTest(unittest.TestCase):
    """1 変異 1 種類。期待したキーと種類が、ちょうど 1 件だけ出る。"""

    def run_with(self, tamper, recorded=None):
        actual = fakeside.run(fakeside.load_requests(), tamper)
        return shape.compare_all(recorded or sample_requests(), actual)

    def test_fake_loses_a_key(self):
        def tamper(name, body):
            if name == "mr_get":
                body = {k: v for k, v in body.items() if k != "title"}
            return body

        self.assertEqual({"mr_get": [("title", "missing")]}, self.run_with(tamper))

    def test_fake_gains_a_key(self):
        def tamper(name, body):
            if name == "mr_get":
                body = {**body, "extra_key": 1}
            return body

        self.assertEqual({"mr_get": [("extra_key", "extra")]}, self.run_with(tamper))

    def test_fake_changes_a_type(self):
        def tamper(name, body):
            if name == "mr_get":
                body = {**body, "iid": str(body["iid"])}
            return body

        self.assertEqual({"mr_get": [("iid", "type")]}, self.run_with(tamper))

    def test_fake_drops_a_key_from_one_element_only(self):
        def tamper(name, body):
            if name == "notes_get":
                body = [{k: v for k, v in body[0].items() if k != "body"}, *body[1:]]
            return body

        self.assertEqual({"notes_get": [("[].body", "presence")]}, self.run_with(tamper))

    def test_sample_loses_a_key(self):
        doc = set_shape(sample_requests(), "mr_get", lambda s: s["keys"].pop("title"))
        self.assertEqual({"mr_get": [("title", "extra")]}, self.run_with(None, doc))

    def test_sample_gains_a_key(self):
        doc = set_shape(
            sample_requests(), "mr_get", lambda s: s["keys"].update(more={"t": ["string"]})
        )
        self.assertEqual({"mr_get": [("more", "missing")]}, self.run_with(None, doc))

    def test_sample_has_another_type(self):
        doc = set_shape(
            sample_requests(), "mr_get", lambda s: s["keys"].update(iid={"t": ["string"]})
        )
        self.assertEqual({"mr_get": [("iid", "type")]}, self.run_with(None, doc))

    def test_sample_says_optional(self):
        doc = set_shape(sample_requests(), "notes_get", lambda s: s["items"].update(opt=["body"]))
        self.assertEqual({"notes_get": [("[].body", "presence")]}, self.run_with(None, doc))

    def test_status_differs(self):
        doc = copy.deepcopy(sample_requests())
        doc["error_404"]["status"] = 403
        self.assertEqual({"error_404": [("$status", "status")]}, self.run_with(None, doc))


class SecretsTest(unittest.TestCase):
    FILES = ("recorded.sample.json", "requests.json")

    def texts(self) -> dict[str, str]:
        found = {}
        for name in sorted(os.listdir(shape.DATA)):
            if name.startswith("recorded") or name == "requests.json":
                with open(os.path.join(shape.DATA, name), encoding="utf-8") as f:
                    found[name] = f.read()
        return found

    def test_no_forbidden_string_in_the_recordings_and_requests(self):
        forbidden = shape.load_data("forbidden.json")
        self.assertTrue(set(self.FILES) <= set(self.texts()))
        for name, text in self.texts().items():
            with self.subTest(name):
                self.assertEqual([], shape.scan_forbidden(text, forbidden))

    def test_each_forbidden_string_is_caught_when_mixed_into_the_sample(self):
        forbidden = shape.load_data("forbidden.json")
        samples = {
            "glpat-": "glpat-abcdef",
            "gldt-": "gldt-abcdef",
            "glptt-": "glptt-abcdef",
            "glrt-": "glrt-abcdef",
            "PRIVATE-TOKEN": "PRIVATE-TOKEN: x",
            "Bearer": "Bearer abc",
            "メールの形": "a.b@example.com",
            "root": "root",
            "ccnavi-reviewer": "ccnavi-reviewer",
        }
        self.assertEqual({f["name"] for f in forbidden}, set(samples))
        base = self.texts()["recorded.sample.json"]
        for name, word in samples.items():
            with self.subTest(name):
                mixed = base.replace('"version": 1', f'"version": 1, "leak": "{word}"', 1)
                self.assertNotEqual(base, mixed)
                self.assertEqual([name], shape.scan_forbidden(mixed, forbidden))

    def test_word_boundary_spares_keys_such_as_root_ref(self):
        forbidden = shape.load_data("forbidden.json")
        self.assertEqual([], shape.scan_forbidden('{"root_ref": 1, "groot": 2}', forbidden))


class RecordedTest(unittest.TestCase):
    def test_recorded_json_agrees_except_for_the_allow_list(self):
        path = os.path.join(shape.DATA, "recorded.json")
        if not os.path.exists(path):
            self.skipTest(
                "recorded.json が無い（本物の GitLab で録るのは手動。gitlab-contract.md）"
            )
        recorded = shape.load_data("recorded.json")
        self.assertNotEqual("sample", recorded.get("source"))
        found = shape.compare_all(recorded["requests"], fakeside.run(fakeside.load_requests()))
        left, problems = shape.apply_allow(found, shape.load_data("allow.json")["entries"])
        self.assertEqual([], problems)
        self.assertEqual({}, left)


if __name__ == "__main__":
    unittest.main()
