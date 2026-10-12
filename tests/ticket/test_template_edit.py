"""テンプレートと下書きの保存・改名・移動・削除（`template_edit`）の単体テスト。

下見（prepare）は何も書かず、commit は expect が主語のハッシュと一致したときだけ書く。
取り込みなどの準備は `tests.ticket.test_template_drafts.Drafts` の道具を使う。
"""

from __future__ import annotations

import os
import unittest

from ccnavi.tickets import plan_templates, template_drafts, template_edit
from tests.ticket.test_template_drafts import FLOW_IMPL, Drafts, slurp


class Edit(Drafts):
    def setUp(self):
        super().setUp()
        self.done_parent(
            ["research", "design", "implement"],
            feedback=["implement-feedback"],
            flows={"i0001-03-01": FLOW_IMPL},
        )
        self.assertEqual(self.draft().returncode, 0)
        self.sha = self.generalize()

    def project_file(self, name="i0001"):
        return os.path.join(self.root, ".ccnavi", "config", "project", "templates", name + ".yml")

    def to_common(self):
        """下書きを共通へ取り込む（CLI の取り込みで、共通の取り込み先を作る）。"""
        result = self.import_("i0001", "--to", "common", "--yes", "--expect", self.sha)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    # 1. save
    def test_save_writes_the_new_body_only_when_expect_matches(self):
        old = self.read_draft()
        new = old.replace("進める".encode(), "進める（改訂）".encode())
        plan = template_edit.prepare(
            self.conf,
            self.root,
            template_edit.OP_SAVE,
            "i0001",
            on=template_edit.ON_DRAFT,
            body=new,
        )
        self.assertTrue(plan.ok, plan.errors)
        self.assertTrue(plan.diff)
        self.assertEqual(self.read_draft(), old)

        wrong = template_edit.commit(plan, "0" * 64)
        self.assertFalse(wrong.ok)
        self.assertFalse(wrong.written)
        self.assertEqual(self.read_draft(), old)

        done = template_edit.commit(plan, plan.sha)
        self.assertTrue(done.ok, done.errors)
        self.assertTrue(done.written)
        self.assertEqual(self.read_draft(), new)
        self.assertEqual(done.new_sha, plan_templates.digest(new))

    # 2. save with a changed name
    def test_save_refuses_a_body_that_changes_the_name(self):
        body = self.read_draft().replace(b"name: i0001", b"name: other")
        plan = template_edit.prepare(
            self.conf,
            self.root,
            template_edit.OP_SAVE,
            "i0001",
            on=template_edit.ON_DRAFT,
            body=body,
        )
        self.assertFalse(plan.ok)
        self.assertTrue(any("name を変える" in e for e in plan.errors), plan.errors)
        self.assertEqual(template_edit.commit(plan, self.sha).ok, False)

    # 3. delete-draft
    def test_delete_draft_removes_the_draft(self):
        plan = template_edit.prepare(
            self.conf,
            self.root,
            template_edit.OP_DELETE_DRAFT,
            "i0001",
            on=template_edit.ON_DRAFT,
        )
        self.assertTrue(plan.ok, plan.errors)
        result = template_edit.commit(plan, self.sha)
        self.assertTrue(result.ok, result.errors)
        self.assertTrue(result.removed)
        self.assertFalse(os.path.lexists(self.draft_path()))

    # 4. rename (draft)
    def test_rename_draft_moves_it_to_the_new_name(self):
        plan = template_edit.prepare(
            self.conf,
            self.root,
            template_edit.OP_RENAME,
            "i0001",
            on=template_edit.ON_DRAFT,
            new_name="i0002",
        )
        self.assertTrue(plan.ok, plan.errors)
        result = template_edit.commit(plan, self.sha)
        self.assertTrue(result.ok, result.errors)
        self.assertFalse(os.path.lexists(self.draft_path("i0001")))
        self.assertTrue(os.path.exists(self.draft_path("i0002")))
        template, notes = plan_templates.parse_template(self.read_draft("i0002"), "i0002")
        self.assertIsNotNone(template, notes)
        self.assertEqual(template.name, "i0002")

    # 5. move (common -> project)
    def test_move_common_to_project_places_it_and_removes_the_source(self):
        self.to_common()
        common = self.common_file()
        sha = plan_templates.digest(slurp(common))
        plan = template_edit.prepare(
            self.conf,
            self.root,
            template_edit.OP_MOVE,
            "i0001",
            on=template_edit.ON_COMMON,
            to=template_drafts.TO_PROJECT,
        )
        self.assertTrue(plan.ok, plan.errors)
        self.assertEqual(plan.sha, sha)
        result = template_edit.commit(plan, sha)
        self.assertTrue(result.ok, result.errors)
        self.assertTrue(os.path.exists(self.project_file()))
        self.assertFalse(os.path.lexists(common))

    # 6. delete (common)
    def test_delete_removes_an_adopted_common_template(self):
        self.to_common()
        common = self.common_file()
        sha = plan_templates.digest(slurp(common))
        plan = template_edit.prepare(
            self.conf,
            self.root,
            template_edit.OP_DELETE,
            "i0001",
            on=template_edit.ON_COMMON,
        )
        self.assertTrue(plan.ok, plan.errors)
        result = template_edit.commit(plan, sha)
        self.assertTrue(result.ok, result.errors)
        self.assertTrue(result.removed)
        self.assertFalse(os.path.lexists(common))

    # 7. save on a common template
    def test_save_on_an_adopted_common_template_is_refused(self):
        self.to_common()
        plan = template_edit.prepare(
            self.conf,
            self.root,
            template_edit.OP_SAVE,
            "i0001",
            on=template_edit.ON_COMMON,
            body=slurp(self.common_file()),
        )
        self.assertFalse(plan.ok)
        self.assertTrue(any("保存できるのは下書きだけ" in e for e in plan.errors), plan.errors)

    # 8. move の衝突は、移動元自身を数えず、ほかの層の衝突は残す
    def test_move_to_common_keeps_a_collision_in_another_layer(self):
        text = self.read_draft().decode("utf-8")
        imported = self.import_("i0001", "--to", "project", "--yes", "--expect", self.sha)
        self.assertEqual(imported.returncode, 0, imported.stdout + imported.stderr)
        lib = os.path.join(self.root, "projects", "lib")
        os.makedirs(os.path.join(lib, ".git"))
        other = os.path.join(lib, ".ccnavi", "config", "project", "templates", "i0001.yml")
        os.makedirs(os.path.dirname(other))
        with open(other, "wb") as f:
            f.write(text.replace("機能を設計から実装まで進める", "別の説明").encode("utf-8"))
        plan = template_edit.prepare(
            self.conf,
            self.root,
            template_edit.OP_MOVE,
            "i0001",
            on=template_edit.ON_PROJECT,
            layer="self",
            to=template_drafts.TO_COMMON,
        )
        self.assertFalse(plan.ok)
        self.assertTrue(any("lib" in e for e in plan.errors), plan.errors)
        self.assertFalse(any(self.project_file() in e for e in plan.errors), plan.errors)

    # 9. 改名で .yml の付いた名前は作らない
    def test_rename_to_a_name_with_the_yml_suffix_is_refused(self):
        plan = template_edit.prepare(
            self.conf,
            self.root,
            template_edit.OP_RENAME,
            "i0001",
            on=template_edit.ON_DRAFT,
            new_name="zz.yml",
        )
        self.assertFalse(plan.ok)
        self.assertTrue(any("zz.yml" in e and ".yml" in e for e in plan.errors), plan.errors)
        self.assertFalse(os.path.lexists(self.draft_path("zz.yml")))


if __name__ == "__main__":
    unittest.main()
