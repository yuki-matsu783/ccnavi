"""要求の列・模擬（`tests/fake_gitlab.py`）・記録の比べる側。

`recorded.sample.json` は模擬の応答から作ったので、サンプルとの一致は「模擬が本物に近い」ことを
示さない（比較器と要求の列が動くことの確認）。模擬が本物と合っていることを示すのは、
本物から録った `recorded.json` との一致（`RecordedTest`）。

見る観点:

1. 要求の列の書式（名前が一意・トークン・`used_by`・`expect_status`・`reads`・置き換えの名前）
2. サンプルが模擬の応答と一致し、いまの模擬から作り直しても変わらない。`reads` のキーが全部ある
3. 変異: 1 変異 1 種類の違いが、期待したキーと種類で出る（サンプル側から削る・模擬側に足す）。
   `reads` の扱い（読むキーは必ず比べる・読まない本物だけのキーは比べない・模擬だけのキーは出す）
4. 機密の検査: サンプルと要求の列に禁止文字列が無く、混ぜると落ちる
5. 置くか置かないかの分岐（ステータス違い・空の配列・禁止文字列・イメージ違い・`reads` の欠け）と、
   置き換えの関数。Docker は使わない
6. `recorded.json` があれば、`reads` で絞って許可リストを当てて一致する
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest

from tests.contract import fakeside, shape

NAMES = {"project", "mr.iid", "mr.branch", "mr.merged_branch", "mr.new_branch", "issue.iid"}
SAVED = {"user.id", "discussion.id"}
# 拡張だけの要求が使う名前。本物の側の Scene.build と TS の mock() が積む（fake は持たない）
CHROME_SCENE = {"sha.main", "sha.feature", "blob.sha"}
# 2xx 以外を期待する要求（拒否の形を録る）
ERROR_STATUS = {"error_401": 401, "error_404": 404, "branch_404": 404, "branch_exists": 400}


def load_sample() -> dict:
    return shape.load_data("recorded.sample.json")


def sample_requests() -> dict:
    return load_sample()["requests"]


def reads() -> dict[str, list[str]]:
    return shape.reads_of(fakeside.load_requests())


def set_shape(doc: dict, request: str, mutate) -> dict:
    out = copy.deepcopy(doc)
    mutate(out[request]["shape"])
    return out


class RequestsTest(unittest.TestCase):
    def test_format(self):
        requests = fakeside.load_all_requests()
        names = [r["name"] for r in requests]
        self.assertEqual(len(names), len(set(names)), "名前が重複している")
        self.assertGreaterEqual(len(requests), 23)
        context = set(fakeside.scene_context()) | SAVED | CHROME_SCENE
        for r in requests:
            if "sh" in r["used_by"]:
                self.assertFalse(
                    set(shape.PLACEHOLDER.findall(json.dumps(r))) & CHROME_SCENE,
                    f"{r['name']}: fake に無い名前を sh の要求で使えない",
                )
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

    def test_expected_status(self):
        for r in fakeside.load_all_requests():
            with self.subTest(r["name"]):
                want = ERROR_STATUS.get(r["name"])
                if want is None:
                    want = 201 if r["method"] == "POST" else 200
                self.assertEqual(want, r["expect_status"])

    def test_reads_are_listed_and_not_in_dropped_keys(self):
        for r in fakeside.load_all_requests():
            with self.subTest(r["name"]):
                self.assertIsInstance(r["reads"], list)
                self.assertTrue(r["reads"], "読むキーが無い要求は reads を空にせず、理由を書く")
                self.assertEqual(len(r["reads"]), len(set(r["reads"])))
                for key in r["reads"]:
                    last = key.replace("[]", ".").split(".")[-1]
                    self.assertNotIn(last, shape.DROPPED_KEYS, "形から落とす欄は読むキーにできない")

    def test_non_empty_requests_are_requests(self):
        names = {r["name"] for r in fakeside.load_all_requests()}
        self.assertLessEqual(set(shape.NON_EMPTY), names)

    def test_fake_requests_are_the_ones_used_by_sh(self):
        all_requests = fakeside.load_all_requests()
        self.assertEqual(
            [r["name"] for r in all_requests if "sh" in r["used_by"]],
            [r["name"] for r in fakeside.load_requests()],
        )
        self.assertTrue(any("sh" not in r["used_by"] for r in all_requests))

    def test_writes_come_after_reads(self):
        methods = [r["method"] for r in fakeside.load_all_requests()]
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

    def test_every_read_key_is_in_the_sample(self):
        self.assertEqual([], shape.reads_problems(fakeside.load_requests(), sample_requests()))


class MutationTest(unittest.TestCase):
    """1 変異 1 種類。期待したキーと種類が、ちょうど 1 件だけ出る。"""

    def run_with(self, tamper, recorded=None, read_keys=None):
        actual = fakeside.run(fakeside.load_requests(), tamper)
        return shape.compare_all(recorded or sample_requests(), actual, read_keys or reads())

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

    def test_sample_gains_a_read_key(self):
        doc = set_shape(
            sample_requests(), "mr_get", lambda s: s["keys"].update(more={"t": ["string"]})
        )
        read_keys = {**reads(), "mr_get": [*reads()["mr_get"], "more"]}
        self.assertEqual({"mr_get": [("more", "missing")]}, self.run_with(None, doc, read_keys))

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


class ReadsMutationTest(unittest.TestCase):
    """`reads` の三つの扱い（読むキーは比べる・読まない本物だけは比べない・模擬だけは出す）。"""

    def run_with(self, doc, read_keys=None):
        actual = fakeside.run(fakeside.load_requests())
        return shape.compare_all(doc, actual, read_keys or reads())

    def test_a_read_key_that_the_fake_lacks_is_reported(self):
        doc = set_shape(
            sample_requests(), "mr_get", lambda s: s["keys"].update(more={"t": ["string"]})
        )
        read_keys = {**reads(), "mr_get": [*reads()["mr_get"], "more"]}
        self.assertEqual({"mr_get": [("more", "missing")]}, self.run_with(doc, read_keys))

    def test_a_real_only_key_that_is_not_read_is_not_compared(self):
        doc = set_shape(
            sample_requests(), "mr_get", lambda s: s["keys"].update(more={"t": ["string"]})
        )
        self.assertEqual({}, self.run_with(doc))

    def test_a_real_only_key_is_reported_when_reads_is_not_used(self):
        doc = set_shape(
            sample_requests(), "mr_get", lambda s: s["keys"].update(more={"t": ["string"]})
        )
        found = shape.compare_all(doc, fakeside.run(fakeside.load_requests()))
        self.assertEqual({"mr_get": [("more", "missing")]}, found)

    def test_a_fake_only_key_is_reported_even_if_it_is_not_read(self):
        doc = set_shape(sample_requests(), "mr_get", lambda s: s["keys"].pop("web_url", None))
        doc = set_shape(doc, "mr_get", lambda s: s["keys"].pop("created_at"))
        self.assertEqual({"mr_get": [("created_at", "extra")]}, self.run_with(doc))

    def test_a_read_key_missing_from_the_recording_is_a_problem(self):
        doc = set_shape(sample_requests(), "mr_get", lambda s: s["keys"].pop("title"))
        self.assertEqual(
            ["mr_get: reads の title が記録に無い"],
            shape.reads_problems(fakeside.load_requests(), doc),
        )

    def test_a_read_key_below_a_missing_parent_is_a_problem(self):
        doc = set_shape(sample_requests(), "reviewers", lambda s: s["items"]["keys"].pop("user"))
        problems = shape.reads_problems(fakeside.load_requests(), doc)
        self.assertEqual(["reviewers: reads の [].user.id が記録に無い"], problems)


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
            "p551-": "p551-abcd1234",
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


class PlacementTest(unittest.TestCase):
    """録った結果を置くか置かないか。本物の代わりに模擬の応答を使い、Docker は使わない。"""

    IMAGE = "gitlab/gitlab-ce@sha256:test"

    def setUp(self):
        self.requests = fakeside.load_requests()
        self.bodies: dict = {}

        def capture(name, body):
            self.bodies[name] = body
            return body

        shapes = fakeside.run(self.requests, capture)
        self.answers = {name: (shapes[name]["status"], self.bodies[name]) for name in shapes}
        self.document = {
            "version": 1,
            "source": "gitlab",
            "gitlab_image": self.IMAGE,
            "gitlab_version": "0.0.0",
            "recorded_at": "2026-01-01",
            "not_recorded": [],
            "requests": shapes,
        }

    def problems(self, answers=None, document=None, image=None):
        return shape.placement_problems(
            self.requests,
            answers or self.answers,
            document or self.document,
            running_image=self.IMAGE if image is None else image,
            forbidden=shape.load_data("forbidden.json"),
        )

    def test_a_good_recording_is_placed(self):
        self.assertEqual([], self.problems())

    def test_every_non_empty_request_has_a_non_empty_array_in_the_scene(self):
        for name in shape.NON_EMPTY:
            with self.subTest(name):
                self.assertTrue(self.answers[name][1])

    def test_a_status_that_differs_from_the_expectation_is_not_placed(self):
        for name, wrong in (
            ("error_401", 200),
            ("error_404", 200),
            ("mr_get", 404),
            ("mr_create", 200),
        ):
            with self.subTest(name):
                answers = {**self.answers, name: (wrong, self.answers[name][1])}
                got = self.problems(answers)
                self.assertEqual(1, len(got))
                self.assertIn(name, got[0])

    def test_an_empty_array_is_not_placed(self):
        for name in shape.NON_EMPTY:
            with self.subTest(name):
                answers = {**self.answers, name: (200, [])}
                got = self.problems(answers)
                self.assertTrue(any(name in p and "空" in p for p in got), got)

    def test_a_forbidden_string_is_not_placed(self):
        document = copy.deepcopy(self.document)
        document["requests"]["user"]["shape"]["keys"]["root/p551-ab12cd34"] = {"t": ["string"]}
        got = self.problems(document=document)
        self.assertTrue(any("禁止文字列" in p and "p551-" in p for p in got), got)

    def test_another_image_is_not_placed(self):
        got = self.problems(image="gitlab/gitlab-ce@sha256:other")
        self.assertEqual(1, len(got))
        self.assertIn("イメージ", got[0])
        self.assertEqual(1, len(self.problems(image="")))

    def test_a_read_key_missing_from_the_recording_is_not_placed(self):
        document = copy.deepcopy(self.document)
        del document["requests"]["mr_get"]["shape"]["keys"]["title"]
        self.assertEqual(["mr_get: reads の title が記録に無い"], self.problems(document=document))

    def test_write_atomic_replaces_the_file_and_leaves_no_temporary_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "recorded.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write("old\n")
            shape.write_atomic(path, "new\n")
            with open(path, encoding="utf-8") as f:
                self.assertEqual("new\n", f.read())
            self.assertEqual(["recorded.json"], os.listdir(tmp))

    def test_write_atomic_keeps_the_old_file_when_the_write_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "recorded.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write("old\n")
            with self.assertRaises(TypeError):
                shape.write_atomic(path, 123)  # type: ignore[arg-type]
            with open(path, encoding="utf-8") as f:
                self.assertEqual("old\n", f.read())
            self.assertEqual(["recorded.json"], os.listdir(tmp))


class RecordedTest(unittest.TestCase):
    PATH = os.path.join(shape.DATA, "recorded.json")

    def load(self) -> dict:
        if not os.path.exists(self.PATH):
            self.skipTest(
                "recorded.json が無い（本物の GitLab で録るのは手動。gitlab-contract.md）"
            )
        return shape.load_data("recorded.json")

    def test_the_header_says_it_was_recorded_from_gitlab(self):
        doc = self.load()
        self.assertEqual(1, doc["version"])
        self.assertEqual("gitlab", doc["source"])
        self.assertTrue(doc["gitlab_image"].startswith("gitlab/gitlab-ce@sha256:"))
        self.assertRegex(doc["gitlab_version"], r"^\d+\.\d+\.\d+")
        self.assertRegex(doc["recorded_at"], r"^\d{4}-\d\d-\d\d$")
        wanted = [n["what"] for n in shape.load_data("allow.json")["not_recorded"]]
        self.assertEqual(wanted, doc["not_recorded"])
        self.assertTrue(doc["not_recorded"])

    def test_every_request_is_recorded_with_only_status_and_shape(self):
        doc = self.load()
        self.assertEqual({r["name"] for r in fakeside.load_all_requests()}, set(doc["requests"]))
        for name, entry in doc["requests"].items():
            with self.subTest(name):
                self.assertEqual({"status", "shape"}, set(entry))

    def test_the_recorded_status_is_the_expected_one(self):
        doc = self.load()
        for r in fakeside.load_all_requests():
            with self.subTest(r["name"]):
                self.assertEqual(r["expect_status"], doc["requests"][r["name"]]["status"])

    def test_every_read_key_is_in_the_recording(self):
        doc = self.load()
        self.assertEqual([], shape.reads_problems(fakeside.load_all_requests(), doc["requests"]))

    def test_the_arrays_that_must_not_be_empty_have_elements(self):
        doc = self.load()
        for name in shape.NON_EMPTY:
            with self.subTest(name):
                self.assertIn("items", doc["requests"][name]["shape"])

    def test_recorded_json_agrees_except_for_the_allow_list(self):
        recorded = self.load()
        # 本物の記録のうち、fake が答える（sh の）要求だけを比べる。拡張だけの要求は TS 側で比べる
        sh_names = {r["name"] for r in fakeside.load_requests()}
        found = shape.compare_all(
            {n: v for n, v in recorded["requests"].items() if n in sh_names},
            fakeside.run(fakeside.load_requests()),
            reads(),
        )
        left, problems = shape.apply_allow(found, shape.load_data("allow.json")["entries"])
        self.assertEqual([], problems)
        self.assertEqual({}, left)

    def test_no_forbidden_string_in_the_recording(self):
        with open(self.PATH, encoding="utf-8") as f:
            text = f.read()
        self.assertEqual([], shape.scan_forbidden(text, shape.load_data("forbidden.json")))


if __name__ == "__main__":
    unittest.main()
