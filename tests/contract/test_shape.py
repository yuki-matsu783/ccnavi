"""正規化と比較器の単体。

見る観点:

1. `normalize-cases.json` の対（入力と期待）がすべて一致する。この表は Chrome 拡張の TS も読む
2. 比較器が 4 種類の違い（足りない・余る・型が違う・常在か任意か）を名前つきで出す
3. 許可リストは、理由が空・種類が不正・差が出ていない項目（古い許可）を失敗にする
4. `${名前}` の置き換え
"""

from __future__ import annotations

import unittest

from tests.contract import shape


class NormalizeTest(unittest.TestCase):
    def test_every_case_in_the_table(self):
        for case in shape.load_data("normalize-cases.json"):
            with self.subTest(case["name"]):
                self.assertEqual(case["expect"], shape.normalize(case["input"]))


class CompareTest(unittest.TestCase):
    def diff(self, old, new):
        return shape.compare(shape.normalize(old), shape.normalize(new))

    def test_equal_shapes_have_no_difference(self):
        self.assertEqual([], self.diff({"a": 1, "l": [{"b": "x"}]}, {"a": 2, "l": [{"b": "y"}]}))

    def test_missing(self):
        self.assertEqual([("b", "missing")], self.diff({"a": 1, "b": 1}, {"a": 1}))

    def test_extra(self):
        self.assertEqual([("b", "extra")], self.diff({"a": 1}, {"a": 1, "b": 1}))

    def test_type(self):
        self.assertEqual([("a", "type")], self.diff({"a": 1}, {"a": "1"}))
        self.assertEqual([("a", "type")], self.diff({"a": 1}, {"a": 1.5}))

    def test_presence(self):
        always = [{"a": 1}, {"a": 2}]
        sometimes = [{"a": 1}, {}]
        self.assertEqual([("[].a", "presence")], self.diff(always, sometimes))

    def test_nested_key_is_named_with_its_path(self):
        self.assertEqual(
            [("n[].author.id", "type")],
            self.diff({"n": [{"author": {"id": 1}}]}, {"n": [{"author": {"id": "1"}}]}),
        )

    def test_empty_array_on_one_side_is_not_compared(self):
        self.assertEqual([], self.diff({"l": []}, {"l": [{"a": 1}]}))

    def test_status_and_missing_request(self):
        one = {"status": 200, "shape": shape.normalize({"a": 1})}
        found = shape.compare_all({"x": one, "y": one}, {"x": {**one, "status": 404}, "z": one})
        self.assertEqual(
            {"x": [("$status", "status")], "y": [("$", "missing")], "z": [("$", "extra")]}, found
        )


class AllowTest(unittest.TestCase):
    FOUND = {"mr": [("a", "missing"), ("b", "extra")]}

    def entry(self, **kw):
        return {"request": "mr", "key": "a", "kind": "missing", "reason": "理由", **kw}

    def test_allowed_difference_is_removed_and_the_rest_stays(self):
        left, problems = shape.apply_allow(self.FOUND, [self.entry()])
        self.assertEqual({"mr": [("b", "extra")]}, left)
        self.assertEqual([], problems)

    def test_empty_reason_is_a_problem(self):
        _, problems = shape.apply_allow(self.FOUND, [self.entry(reason=" ")])
        self.assertTrue(any("理由が空" in p for p in problems))

    def test_unknown_kind_is_a_problem(self):
        _, problems = shape.apply_allow({}, [self.entry(kind="other")])
        self.assertTrue(any("違いの種類" in p for p in problems))

    def test_stale_entry_is_a_problem(self):
        _, problems = shape.apply_allow(self.FOUND, [self.entry(key="zzz")])
        self.assertTrue(any("古い許可" in p for p in problems))

    def test_star_is_not_a_wildcard(self):
        left, problems = shape.apply_allow(self.FOUND, [self.entry(key="*")])
        self.assertEqual(self.FOUND, left)
        self.assertTrue(any("古い許可" in p for p in problems))

    def test_the_kind_must_match(self):
        left, _ = shape.apply_allow(self.FOUND, [self.entry(kind="extra")])
        self.assertIn(("a", "missing"), left["mr"])


class ReadsTest(unittest.TestCase):
    """`reads` で比べるキーを絞る。読むキーは必ず比べ、本物にしか無い読まないキーは比べない。"""

    def found(self, real, fake, reads):
        recorded = {"x": {"status": 200, "shape": shape.normalize(real)}}
        actual = {"x": {"status": 200, "shape": shape.normalize(fake)}}
        return shape.compare_all(recorded, actual, {"x": reads} if reads is not None else None)

    def test_a_read_key_the_fake_lacks_is_reported(self):
        self.assertEqual({"x": [("a", "missing")]}, self.found({"a": 1, "b": 1}, {"b": 1}, ["a"]))

    def test_a_read_key_of_another_type_is_reported(self):
        self.assertEqual({"x": [("a", "type")]}, self.found({"a": 1}, {"a": "1"}, ["a"]))

    def test_a_real_only_key_that_is_not_read_is_dropped(self):
        self.assertEqual({}, self.found({"a": 1, "b": 1}, {"a": 1}, ["a"]))

    def test_a_fake_only_key_is_reported_even_if_not_read(self):
        self.assertEqual({"x": [("b", "extra")]}, self.found({"a": 1}, {"a": 1, "b": 1}, ["a"]))

    def test_without_reads_everything_is_compared(self):
        self.assertEqual({"x": [("b", "missing")]}, self.found({"a": 1, "b": 1}, {"a": 1}, None))

    def test_a_read_key_below_a_missing_parent_is_reported(self):
        self.assertEqual({"x": [("o", "missing")]}, self.found({"o": {"q": 1}}, {}, ["o.q"]))

    def test_a_read_array_does_not_cover_the_keys_of_its_elements(self):
        real, fake = {"l": [{"q": 1}]}, {"l": [{"z": 1}]}
        want = {"x": [("l[].z", "extra")]}
        self.assertEqual(want, self.found(real, fake, ["l"]))
        both = {"x": [("l[].q", "missing"), ("l[].z", "extra")]}
        self.assertEqual(both, self.found(real, fake, ["l[].q"]))

    def test_the_status_and_a_missing_request_are_always_reported(self):
        recorded = {"x": {"status": 200, "shape": shape.normalize({"a": 1})}}
        actual = {"x": {"status": 404, "shape": shape.normalize({"a": 1})}}
        self.assertEqual(
            {"x": [("$status", "status")]}, shape.compare_all(recorded, actual, {"x": []})
        )
        self.assertEqual({"x": [("$", "missing")]}, shape.compare_all(recorded, {}, {"x": []}))

    def test_key_paths_and_node_at(self):
        node = shape.normalize({"a": {"b": 1}, "l": [{"c": "x"}]})
        self.assertEqual({"a", "a.b", "l", "l[]", "l[].c"}, shape.key_paths(node))
        self.assertEqual(["integer"], shape.node_at(node, "a.b")["t"])
        self.assertEqual(["string"], shape.node_at(node, "l[].c")["t"])
        self.assertIsNone(shape.node_at(node, "a.zzz"))
        root_list = shape.normalize([{"id": 1}])
        self.assertEqual({"[]", "[].id"}, shape.key_paths(root_list))
        self.assertEqual(["integer"], shape.node_at(root_list, "[].id")["t"])

    def test_reads_must_exist_in_the_recording(self):
        requests = [{"name": "x", "reads": ["a", "l[].c", "gone"]}]
        doc = {"x": {"status": 200, "shape": shape.normalize({"a": 1, "l": [{"c": 1}]})}}
        self.assertEqual(["x: reads の gone が記録に無い"], shape.reads_problems(requests, doc))
        self.assertEqual(["x: 記録に無い要求"], shape.reads_problems(requests, {}))


class SubstituteTest(unittest.TestCase):
    def test_replaces_in_nested_values(self):
        got = shape.substitute(
            {"p": "/a/${project}/${mr.iid}", "l": ["x${mr.iid}"], "n": 1},
            {"project": "P", "mr.iid": "3"},
        )
        self.assertEqual({"p": "/a/P/3", "l": ["x3"], "n": 1}, got)

    def test_unknown_name_fails(self):
        with self.assertRaises(KeyError):
            shape.substitute("${nope}", {})


if __name__ == "__main__":
    unittest.main()
