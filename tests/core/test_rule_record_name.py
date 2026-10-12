"""ルールの id は必須。記録名はルールファイルの相対パスと書いた id の形（rules.record_name）。"""

from __future__ import annotations

import os
import tempfile
import unittest

from ccnavi.policy import ruleload, rules


class IdRequiredTest(unittest.TestCase):
    def test_id_が無いルールは_error_で除外される(self):
        raw = {"match": "Bash", "glob": "*rm -rf*", "message": "消さない"}
        found, problems = rules._build(raw, "deny", 0)
        self.assertIsNone(found)
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0].severity, rules.SEVERITY_ERROR)
        self.assertIn("id が無い", problems[0].detail)

    def test_id_が無くても他のルールは残る(self):
        rule_set, problems = rules.parse(
            {
                "version": 1,
                "deny": [
                    {"match": "Bash", "glob": "*rm -rf*", "message": "消さない"},
                    {"id": "git-push", "match": "Bash", "glob": "*git push*", "message": "m"},
                ],
            }
        )
        self.assertEqual([r.bare_id for r in rule_set.deny], ["git-push"])
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0].severity, rules.SEVERITY_ERROR)


class RecordNameTest(unittest.TestCase):
    def test_ルールファイル由来は相対パスと書いた_id(self):
        rule_set, _ = rules.parse(
            {
                "version": 1,
                "deny": [{"id": "raw-git", "match": "Bash", "glob": "*git*", "message": "m"}],
            }
        )
        ruleload.mark_file(rule_set, ".ccnavi/config/common/rules.yml")
        (rule,) = rule_set.deny
        self.assertEqual(rules.record_name(rule), ".ccnavi/config/common/rules.yml:raw-git")

    def test_レイヤーの前置きは記録名に付かない(self):
        rule_set, _ = rules.parse(
            {
                "version": 1,
                "deny": [{"id": "raw-git", "match": "Bash", "glob": "*git*", "message": "m"}],
            }
        )
        ruleload.mark_file(rule_set, "projects/lib/.ccnavi/config/project/rules.yml")
        ruleload.prefix_ids(rule_set, "lib")
        (rule,) = rule_set.deny
        self.assertEqual(rule.id, "lib:raw-git")
        self.assertEqual(
            rules.record_name(rule), "projects/lib/.ccnavi/config/project/rules.yml:raw-git"
        )

    def test_組み込みは_id_そのもの(self):
        rule_set, _ = rules.parse(
            {
                "version": 1,
                "deny": [
                    {
                        "id": "builtin:git-push",
                        "match": "Bash",
                        "glob": "*git push*",
                        "message": "m",
                    }
                ],
            },
            builtin=True,
        )
        (rule,) = rule_set.deny
        self.assertFalse(rule.from_file)
        self.assertEqual(rules.record_name(rule), "builtin:git-push")

    def test_相対パスはワークスペースのルートから_スラッシュ区切り(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(
                root, "projects", "lib", ".ccnavi", "config", "project", "rules.yml"
            )
            self.assertEqual(
                ruleload.relpath(path, root),
                "projects/lib/.ccnavi/config/project/rules.yml",
            )


if __name__ == "__main__":
    unittest.main()
