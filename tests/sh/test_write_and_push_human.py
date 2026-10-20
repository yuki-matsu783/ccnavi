"""write-and-push の受入テスト（ユーザの判断の入口と、Chrome のレビュー済みとの突き合わせ）。

共通の道具と全体の説明は `tests/sh/write_and_push_harness.py` にある。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import unittest

from tests import can_pass_argument
from tests.sh.write_and_push_harness import (
    APPROVED,
    CHILD,
    GIT,
    PARENT,
    URL_GIT,
    PhaseOne,
    WriteAndPushHarness,
    executable,
    git,
    write,
)


class WriteAndPushHumanTest(PhaseOne, WriteAndPushHarness):
    """9. ユーザの判断の入口は、取り込み済みの親子のチケットなら承認の push を自動で呼ぶ。

    11. 承認の push。
    """

    plan = ("chores", "design")

    def test_push_approved_without_names_skips_child_trees(self):
        """名前を省いた承認の push: 親は 1 度だけコミットして push し、
        子のワークツリーの置き場はコミットせずに言う。
        """
        rel = f"{APPROVED}/flows/{CHILD}.yml"
        write(os.path.join(self.tree, rel), "steps: []\n")
        child = self.child_tree()
        write(os.path.join(child, APPROVED, "doing", "stray.md"), "x\n")
        result = self.sh("ccnavi-user-push-approved.sh")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("子のワークツリー", result.stderr)
        self.assertEqual(
            result.stdout.count("承認済みチケットを取り込んだうえで"), 1, result.stdout
        )
        self.assertIn(rel, self.committed())
        self.assertEqual(self.remote_sha(CHILD), "")

    def test_push_approved_takes_in_before_sending(self):
        rel = f"{APPROVED}/flows/{CHILD}.yml"
        write(os.path.join(self.tree, rel), "steps: []\n")
        remote = self.remote_commit("src/remote.py", "y = 2\n")
        result = self.sh("ccnavi-user-push-approved.sh", PARENT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(rel, self.committed())
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))
        self.assertTrue(
            git(self.tree, "merge-base", "--is-ancestor", remote, "HEAD").returncode == 0
        )

    def test_push_approved_keeps_the_commit_when_the_push_fails(self):
        rel = f"{APPROVED}/flows/{CHILD}.yml"
        write(os.path.join(self.tree, rel), "steps: []\n")
        executable(os.path.join(self.remote, "hooks", "pre-receive"), "#!/bin/sh\nexit 1\n")
        result = self.sh("ccnavi-user-push-approved.sh", PARENT)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("コミットはローカルに残っています", result.stderr)
        self.assertIn(rel, self.committed())
        # 次の write-and-push は未送信のユーザの判断を見つけて止まり、承認の push
        # を打ち直すよう言う。
        os.remove(os.path.join(self.remote, "hooks", "pre-receive"))
        stopped = self.ticket("start", PARENT)
        self.assertEqual(stopped.returncode, 1, stopped.stdout + stopped.stderr)
        self.assertIn("未送信のユーザの判断", stopped.stderr)
        again = self.sh("ccnavi-user-push-approved.sh", PARENT)
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertEqual(self.ticket("start", PARENT).returncode, 0)

    def test_push_approved_carries_a_removed_draft_with_the_flow(self):
        """取り込んで消えた下書きは、フローと一緒にコミットして push する。書き直された下書きと
        追跡していない下書きには触れない。"""
        drafts = "wip/proposals/flows"
        taken = f"{drafts}/{CHILD}.yml"
        rewritten = f"{drafts}/{PARENT}-01-02.yml"
        untracked = f"{drafts}/{PARENT}-01-03.yml"
        for rel in (taken, rewritten):
            write(os.path.join(self.tree, rel), "nodes: []\n")
        git(self.tree, "add", "--", taken, rewritten)
        git(self.tree, "commit", "-q", "-m", "drafts")
        git(self.tree, "push", "-q", "origin", PARENT)
        flow_rel = f"{APPROVED}/flows/{CHILD}.yml"
        write(os.path.join(self.tree, flow_rel), "nodes: []\n")
        os.remove(os.path.join(self.tree, taken))
        write(os.path.join(self.tree, rewritten), "nodes: [x]\n")
        write(os.path.join(self.tree, untracked), "nodes: []\n")
        result = self.sh("ccnavi-user-push-approved.sh", PARENT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # 同じ中身なので、改名として読まずに 2 つのパスで見る。
        out = git(self.tree, "show", "--name-only", "--no-renames", "--format=", "HEAD").stdout
        self.assertEqual(sorted(out.split()), sorted([flow_rel, taken]))
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))
        left = self.dirty()
        self.assertIn(rewritten, left)
        self.assertIn(untracked, left)
        self.assertNotIn(taken, left)
        self.assertEqual(git(self.tree, "diff", "--cached", "--name-only").stdout, "")

    def test_push_approved_carries_a_removed_template_draft_with_the_flow(self):
        """取り込んで消えたテンプレートの下書きも、フローの下書きと同じ形で運ぶ（write-and-push
        の手順）。"""
        drafts = "wip/proposals/templates"
        taken = f"{drafts}/feature.yml"
        rewritten = f"{drafts}/research.yml"
        for rel in (taken, rewritten):
            write(os.path.join(self.tree, rel), "name: x\n")
        git(self.tree, "add", "--", taken, rewritten)
        git(self.tree, "commit", "-q", "-m", "drafts")
        git(self.tree, "push", "-q", "origin", PARENT)
        flow_rel = f"{APPROVED}/flows/{CHILD}.yml"
        write(os.path.join(self.tree, flow_rel), "nodes: []\n")
        os.remove(os.path.join(self.tree, taken))
        write(os.path.join(self.tree, rewritten), "name: y\n")
        result = self.sh("ccnavi-user-push-approved.sh", PARENT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        out = git(self.tree, "show", "--name-only", "--no-renames", "--format=", "HEAD").stdout
        self.assertEqual(sorted(out.split()), sorted([flow_rel, taken]))
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))
        left = self.dirty()
        self.assertIn(rewritten, left)
        self.assertNotIn(taken, left)

    def test_push_approved_without_names_carries_the_family_through_write_and_push(self):
        rel = f"{APPROVED}/flows/{CHILD}.yml"
        write(os.path.join(self.tree, rel), "steps: []\n")
        self.remote_commit("src/remote.py", "y = 2\n")
        result = self.sh("ccnavi-user-push-approved.sh")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("承認済みチケットを取り込んだうえで", result.stdout)
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))


@unittest.skipIf(shutil.which("jq") is None, "jq が必要です")
class WriteAndPushChromeConfirmTest(PhaseOne, WriteAndPushHarness):
    """Chrome のレビュー済みと手元の confirm の突き合わせ。

    取り込み済みの親子のチケットでは、手元の CLI を直に打つと write-and-push に断られる
    （`--record-tree` が無い）。そこで write-and-push と同じ手順
    （`ccnavi-review.sh request` と `confirm`。GitHub の代役は録った見本）で手元を
    回し、同じ状態から Chrome の入口が出す書くものと、write-and-push
    が親のブランチへ書いて送ったものを比べる。
    違ってよいのは経路（`via`）と時刻（`at`）だけ。アカウント（`actor`）は、手元は sh が
    トークンの持ち主を引いたもの、Chrome は PAT の持ち主で、同じ見本なので同じになる。
    """

    SCENE = "resolved"

    def setUp(self):
        super().setUp()
        from tests.sh import github_host

        self.host = github_host
        shim = os.path.join(self._base, "shim")
        executable(
            os.path.join(shim, "git"),
            URL_GIT.format(url="https://github.com/acme/widgets.git", git=GIT),
        )
        fakes = os.path.join(self._base, "fakes")
        github_host.install(fakes, sys.executable)
        self.path = os.pathsep.join([shim, fakes, os.environ.get("PATH", "")])
        self.finish_phase_one()
        git(self.tree, "merge", "-q", "--no-edit", CHILD)
        git(self.tree, "push", "-q", "origin", PARENT)

    def review(self, *args, scene=None):
        return self.sh(
            "ccnavi-review.sh",
            *args,
            cwd=self.tree,
            PATH=self.path,
            GITHUB_TOKEN="t0k",
            FAKE_GITHUB_SCENE=scene or self.SCENE,
            FAKE_GITHUB_STATE=os.path.join(self._base, "comments.json"),
        )

    def files(self, base, prefixes):
        found = {}
        for prefix in prefixes:
            for current, dirs, names in os.walk(os.path.join(base, *prefix.split("/"))):
                dirs[:] = [d for d in dirs if d != ".git"]
                for name in names:
                    full = os.path.join(current, name)
                    rel = os.path.relpath(full, base).replace(os.sep, "/")
                    found[rel] = self.read(full)
        return found

    def host_compare(self, base, head):
        """GitHub の compare API の答えを、拡張の `compareFiles` と同じ規則で一覧にする。

        GitHub は改名を検出して `filename` と `previous_filename` を返し、拡張は両方を入れる。
        ここでは git の改名の検出（`--find-renames`）で同じ組を作り、同じ規則で並べる
        （TS の関数そのものは拡張の試験 CX-T130・139 が見る）。
        """
        out = git(self.tree, "diff", "--name-status", "--find-renames", f"{base}..{head}").stdout
        files = []
        for line in out.splitlines():
            parts = line.split("\t")
            if parts[0].startswith("R"):
                files += [parts[2], parts[1]]
            else:
                files.append(parts[1])
        return files

    def chrome_request(self, scene=None):
        """拡張が組むのと同じ要求（統合先 main、親 i0001、録った見本、
        依頼の後の変更の一覧）。"""
        from tests.ticketsample import load_chrome

        chrome = load_chrome()
        place = chrome._placement()
        keep = tuple(p + "/" for p in place["integration_paths"])
        main = {
            rel: text
            for rel, text in self.files(self.ws, (".ccnavi", ".claude")).items()
            if rel.startswith(keep) or rel in place["integration_files"]
        }
        mine = self.files(self.tree, place["branch_paths"])
        recorded = json.loads(self.read(os.path.join(self.tree, *self.requested.split("/"))))
        head = self.sha(self.tree, "HEAD")
        files = self.host_compare(recorded["head"], head)
        where = os.path.join(self.host.SCENES, scene or self.SCENE, "expected.json")
        with open(where, encoding="utf-8") as f:
            copy = json.load(f)
        request = {
            "schema": chrome.SCHEMA,
            "op": "confirm",
            "family": PARENT,
            "phase": 1,
            "stamp": "2026-09-29T12:00:00+0900",
            "actor": {"account": "octo-reviewer"},
            "result": copy,
            "compare": {"base": recorded["head"], "head": head, "files": files},
            "snapshot": {
                "integration": {"name": "main", "source": "default", "head": "0" * 40},
                "branches": {
                    "main": {"head": "0" * 40, "files": main},
                    PARENT: {"head": head, "files": mine},
                },
                "absent": [],
            },
        }
        answer = json.loads(chrome.handle(json.dumps(request), os.path.join(self._base, "memfs")))
        self.assertNotIn("error", answer, answer)
        return answer

    @property
    def requested(self):
        return f"{APPROVED}/phases/{PARENT}/1.requested"

    @staticmethod
    def normalized(path, text):
        """経路・時刻を除く（違ってよいもの）。子を動かした経路の欄（settled_by）も経路。"""
        drop = ("via", "at")
        if path.endswith((".reviewed", ".json")):
            return {k: v for k, v in json.loads(text).items() if k not in drop}
        return re.sub(r'^settled_by: "[a-z-]+"\n', "", text, flags=re.M)

    def request(self):
        body = write(os.path.join(self._base, "body.md"), "見てほしい\n")
        html = self.eli5()
        requested = self.review("request", "--phase", "1", "--body-file", body, "--eli5", html)
        self.assertEqual(requested.returncode, 0, requested.stdout + requested.stderr)
        self.assertIn(self.requested, self.committed())
        return body

    def same_refusal(self, scene):
        """Chrome が通さない理由が、write-and-push で回した手元の confirm
        の標準エラーにそのまま出る。"""
        answer = self.chrome_request(scene)
        self.assertIsNone(answer["changes"], scene)
        self.assertTrue(answer["problems"], scene)
        head, remote = self.sha(self.tree, "HEAD"), self.remote_sha()
        local = self.review("confirm", "--phase", "1", scene=scene)
        self.assertNotEqual(local.returncode, 0, scene)
        said = local.stderr.replace(self.ws + os.sep, "").splitlines()
        for line in answer["problems"]:
            self.assertIn(line, said, scene)
        self.assertEqual((self.sha(self.tree, "HEAD"), self.remote_sha()), (head, remote), scene)
        self.assertEqual(self.dirty(), "", scene)
        return answer

    STOPPING = ("paged", "changes-requested", "cr-commented", "pending")

    def test_unresolved_threads_and_change_requests_stop_both_the_same(self):
        self.request()
        # paged の取ってきた状態は約 50,000 字。1 引数の上限が小さい環境（Windows）では取れない。
        scenes = [s for s in self.STOPPING if s != "paged" or can_pass_argument(60_000)]
        said = {s: self.same_refusal(s)["problems"][0] for s in scenes}
        if "paged" in said:
            self.assertIn("未解決のスレッドが 4 件", said["paged"])
        for scene in ("changes-requested", "cr-commented", "pending"):
            self.assertIn("変更要求（Changes requested）が出ています", said[scene], scene)

    def test_chrome_writes_what_write_and_push_writes_and_sends(self):
        body = self.request()

        answer = self.chrome_request()
        self.assertEqual(answer["problems"], [])
        rows = answer["changes"][PARENT]

        # 手元を直に打つと write-and-push に断られる（だから write-and-push の手順で回す）。
        # 文面まで見る
        direct = self.exe("--cwd", self.tree, "review", "confirm", "--phase", "1", "--result", body)
        self.assertNotEqual(direct.returncode, 0)
        self.assertIn(
            f"親子のチケット {PARENT} は取り込み済み（write-and-push の対象）", direct.stderr
        )
        self.assertIn("何も反映せずに中止しました。", direct.stderr)

        before = self.sha(self.tree, "HEAD")
        confirmed = self.review("confirm", "--phase", "1")
        self.assertEqual(confirmed.returncode, 0, confirmed.stdout + confirmed.stderr)
        self.assertEqual(self.remote_sha(), self.sha(self.tree, "HEAD"))
        self.assertEqual(self.dirty(), "")
        sent = git(self.tree, "diff", "--name-only", "--no-renames", f"{before}..HEAD")
        self.assertEqual(sorted(sent.stdout.split()), sorted(r["path"] for r in rows))
        for row in rows:
            full = os.path.join(self.tree, *row["path"].split("/"))
            if row["op"] == "delete":
                self.assertFalse(os.path.exists(full), row["path"])
                continue
            self.assertEqual(
                self.normalized(row["path"], row["content"]),
                self.normalized(row["path"], self.read(full)),
                row["path"],
            )
        remote = json.loads(next(r["content"] for r in rows if r["path"].endswith("1.reviewed")))
        local = json.loads(
            self.read(os.path.join(self.tree, *f"{APPROVED}/phases/{PARENT}/1.reviewed".split("/")))
        )
        self.assertEqual((remote["actor"], remote["via"]), ("octo-reviewer", "chrome"))
        self.assertEqual((local["actor"], local["via"]), ("octo-reviewer", "cli"))
        self.assertEqual(list(remote), list(local))

        # レビュー済みに重ねて打てば、手元も Chrome も同じ文面で止め、何も送らない
        again = self.same_refusal(self.SCENE)
        self.assertEqual(
            again["problems"], ["ccnavi: [CORE_CONFIRM_ALREADY_REVIEWED] フェーズ 1 はレビュー済み"]
        )


if __name__ == "__main__":
    unittest.main()
