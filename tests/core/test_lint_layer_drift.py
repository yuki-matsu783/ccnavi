"""レイヤーのずれの warn（`lint_layers.layer_drift`）。

手元のレイヤーを、取得済みの `refs/remotes/origin/<統合先>` の同じパスと `git show` で比べる。
違えば warn を出す。`origin/<統合先>` が手元に無ければ `layer_drift` は黙り、
`--lint`（`lint_layers._sync`）は比べられなかったことを info 1 行で言う。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest

from ccnavi.entry import lint_layers
from ccnavi.infra import settings
from ccnavi.policy.rules import SEVERITY_INFO, SEVERITY_WARN

COMMON = ".ccnavi/config/common/rules.yml"
LAYER = ".ccnavi/config/project/rules.yml"


def git(cwd, *args):
    done = subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return done.stdout


def write(path, text, newline="\n"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline=newline) as f:
        f.write(text)


def repo(path, branch="main"):
    """git のリポジトリと、そこから push した origin。origin/<branch> が手元に取得済みになる。"""
    os.makedirs(path, exist_ok=True)
    git(path, "init", "-q", "-b", branch)
    write(os.path.join(path, COMMON), "version: 1\nrules: []\n")
    write(os.path.join(path, LAYER), "version: 1\nrules: []\n")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "init")
    bare = path + ".origin.git"
    git(os.path.dirname(path), "init", "-q", "--bare", bare)
    git(path, "remote", "add", "origin", bare)
    git(path, "push", "-q", "origin", branch)


class LayerDriftTest(unittest.TestCase):
    def setUp(self):
        self.ws = os.path.realpath(tempfile.mkdtemp(prefix="ccnavi-drift-"))
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)
        repo(os.path.join(self.ws, "ws"))
        self.root = os.path.join(self.ws, "ws")
        write(os.path.join(self.root, ".claude", "settings.json"), "{}\n")
        self.conf, _ = settings.load(self.root)

    def drift(self, project=""):
        return lint_layers.layer_drift(self.conf, self.root, project)

    def test_same_layers_say_nothing(self):
        self.assertEqual([], self.drift())

    def test_a_changed_layer_is_a_warn_that_names_the_path_and_the_branch(self):
        write(os.path.join(self.root, LAYER), "version: 1\nrules: [x]\n")
        (found,) = self.drift()
        self.assertEqual(SEVERITY_WARN, found.severity)
        self.assertEqual("(self)", found.rule)
        self.assertIn(LAYER, found.detail)
        self.assertIn("origin/main", found.detail)
        self.assertIn("中身が違う", found.detail)
        # 手元の origin/main が古いだけのこともあるので、先に取得する案内を添える。
        self.assertIn(
            "手元の origin/main が古いだけなら、先に取得（fetch）してから見直す", found.detail
        )

    def test_a_changed_common_layer_is_told_for_the_workspace_itself(self):
        write(os.path.join(self.root, COMMON), "version: 1\nrules: [x]\n")
        (found,) = self.drift()
        self.assertIn(COMMON, found.detail)

    def test_a_file_only_here_or_only_there_is_told_which(self):
        os.remove(os.path.join(self.root, LAYER))
        (gone,) = self.drift()
        self.assertIn("作業ツリーにありません", gone.detail)
        write(os.path.join(self.root, LAYER), "version: 1\n")
        git(self.root, "rm", "-q", "--cached", LAYER)
        git(self.root, "commit", "-q", "-m", "drop")
        git(self.root, "push", "-q", "origin", "main")
        (extra,) = self.drift()
        self.assertIn("統合先にありません", extra.detail)

    def test_line_endings_alone_are_not_a_difference(self):
        write(os.path.join(self.root, LAYER), "version: 1\r\nrules: []\r\n", newline="")
        self.assertEqual([], self.drift())

    def test_the_integration_branch_follows_origin_head_only(self):
        git(self.root, "branch", "develop")
        write(os.path.join(self.root, LAYER), "version: 1\nrules: [dev]\n")
        git(self.root, "checkout", "-q", "develop")
        git(self.root, "commit", "-qam", "dev")
        git(self.root, "push", "-q", "origin", "develop")
        git(self.root, "checkout", "-q", "main")
        write(os.path.join(self.root, LAYER), "version: 1\nrules: [dev]\n")
        (found,) = self.drift()
        self.assertIn("origin/main", found.detail)
        git(self.root, "remote", "set-head", "origin", "develop")
        self.assertEqual([], self.drift())

    def test_no_origin_branch_is_silent(self):
        git(self.root, "update-ref", "-d", "refs/remotes/origin/main")
        write(os.path.join(self.root, LAYER), "version: 1\nrules: [x]\n")
        self.assertEqual([], self.drift())

    def test_a_project_is_compared_with_its_own_origin(self):
        repo(os.path.join(self.root, "projects", "app"))
        self.conf, _ = settings.load(self.root)
        write(os.path.join(self.root, "projects", "app", LAYER), "version: 1\nrules: [x]\n")
        self.assertEqual([], self.drift())
        (found,) = self.drift("app")
        self.assertEqual("(projects/app)", found.rule)
        # プロジェクトでは共通レイヤーは比べない（ワークスペースのものを読むため）。
        write(os.path.join(self.root, "projects", "app", COMMON), "version: 1\nrules: [x]\n")
        self.assertEqual(1, len(self.drift("app")))

    def test_lint_says_one_info_for_the_repositories_it_could_not_compare(self):
        repo(os.path.join(self.root, "projects", "app"))
        self.conf, _ = settings.load(self.root)
        git(self.root, "update-ref", "-d", "refs/remotes/origin/main")
        found = [p for p in lint_layers._sync(self.conf, self.root) if p.severity == SEVERITY_INFO]
        self.assertEqual(1, len(found), found)
        self.assertIn("self", found[0].detail)
        self.assertNotIn("app", found[0].detail)

    def test_a_repository_without_an_origin_is_not_told_to_fetch(self):
        git(self.root, "remote", "remove", "origin")
        self.assertEqual([], lint_layers._sync(self.conf, self.root))

    def test_lint_adds_the_warn_to_the_same_report(self):
        write(os.path.join(self.root, LAYER), "version: 1\nrules: [x]\n")
        warns = [p for p in lint_layers._sync(self.conf, self.root) if p.severity == SEVERITY_WARN]
        self.assertEqual(1, len(warns))


if __name__ == "__main__":
    unittest.main()
