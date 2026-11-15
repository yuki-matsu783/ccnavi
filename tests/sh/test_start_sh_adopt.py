"""ccnavi-start.sh の受入テスト（既存のブランチ・MR を採用する経路と GitLab）。

共通の部品と全体の説明は `tests/sh/start_sh_harness.py` にある。新しく作る経路と事前確認は
`tests/sh/test_start_sh.py`。

見るのは次のとおり。

1. 手元の候補が 1 件（MR があればユーザに聞き、--mr で採用し、ワークツリーができた後は
   聞かずに続ける）
2. `/` を含むブランチの MR はブランチを直接チェックアウトする
3. 採用するときの確認（識別子・origin の全ブランチでの識別子の重なり・マージ済みの MR）と、
   確かめられないときの案内
4. 毎回 fetch して早送りする（無視されたファイルは上書きしない）
5. MR が閉じる issue（GraphQL が断られる環境の本文への切り替え・ページ送り・案内のとおりの打ち直し）
6. GitLab の経路
"""

from __future__ import annotations

import os
import shutil
import unittest

from tests.sh.start_sh_harness import (
    BRANCH,
    GITHUB,
    GITLAB,
    GL_API,
    GL_URL,
    GRAPHQL,
    PULLS,
    StartShHarness,
    closed_pulls,
    closing,
    git,
    nexts,
    pr,
    write,
)


class StartShAdoptTest(StartShHarness):
    # ---- 1. 手元の候補が 1 件

    def test_single_local_candidate_gets_a_worktree(self):
        git(self.ws, "push", "-q", self.bare, "main:refs/heads/fix-152-other")
        git(self.ws, "branch", "fix-152-other", "origin/main")
        self.route(
            {
                PULLS: [200, [pr(3, "fix-152-other", body="Closes #152")]],
                f"GET {GITHUB}/pulls/3": [200, pr(3, "fix-152-other", body="Closes #152")],
                **closed_pulls("fix-152-other"),
            }
        )
        # このPCにそのブランチのワークツリーが無いので、--issue だけでは聞いて止まる
        asked = self.run_sh("--issue", "152")
        self.assertEqual(asked.returncode, 3, asked.stdout + asked.stderr)
        self.assertIn("issue #152 に紐づく MR !3", asked.stdout)
        self.assertIn("--issue 152 --mr 3 を打つ", asked.stdout)
        self.assertIn("直接実装には切り替えない", asked.stdout)
        self.assertFalse(os.path.isdir(self.wt("fix-152-other")))
        # ユーザが続けると決めたら、--mr を付けて打ち直す
        done = self.run_sh("--issue", "152", "--mr", "3")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertTrue(os.path.isdir(self.wt("fix-152-other")))
        self.assertEqual(self.posts(), [])
        self.assertEqual(self.fields(done.stdout)["ブランチ"], "fix-152-other")
        # ワークツリーができたあとの再開は、聞かずに続けて、ワークツリーを知らせる
        again = self.run_sh("--issue", "152")
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertTrue(
            self.fields(again.stdout)["ワークツリー"].endswith("worktrees/fix-152-other")
        )

    def test_a_candidate_without_mr_is_checked_too(self):
        """MR の無い候補（名前に番号を含むブランチ）も、親に使えない識別子なら出さずに止める。"""
        tip = git(self.ws, "commit-tree", "-p", "main", "-m", "work", "main^{tree}")
        git(self.ws, "branch", "fix-152-other-05", tip)
        self.route({PULLS: [200, []]})
        done = self.run_sh("--issue", "152")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("末尾", done.stderr)
        self.assertFalse(os.path.isdir(self.wt("fix-152-other-05")))

    # ---- 2. `/` を含むブランチ

    def test_mr_with_slash_branch_checks_out_directly_without_identifier_branch(self):
        git(self.ws, "push", "-q", self.bare, "main:refs/heads/topic/x")
        self.route({f"GET {GITHUB}/pulls/5": [200, pr(5, "topic/x")], **closed_pulls("topic/x")})
        done = self.run_sh("--mr", "5")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        got = self.fields(done.stdout)
        self.assertEqual(got["ブランチ"], "topic/x")
        self.assertTrue(got["ワークツリー"].endswith("worktrees/topic-x"))
        self.assertEqual(git(self.wt("topic-x"), "rev-parse", "--abbrev-ref", "HEAD"), "topic/x")
        # 識別子のブランチ（topic-x）は作らない。switch の回り道もしない
        self.assertEqual(sorted(self.local_branches()), ["main", "topic/x"])
        self.assertNotIn("switch", done.stdout)
        self.assertIn("識別子: topic-x", done.stdout)
        # MR が閉じる issue を frontmatter に書く案内
        self.assertIn("必須: 提案の frontmatter に issue: 152 と mr: 5 を書く", done.stdout)

    # ---- 3. 採用するときの確認

    def test_adopt_stops_when_identifier_is_unusable(self):
        done = self.adopt("feature/123-login-05")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("使えない", done.stderr)
        self.assertIn("末尾", done.stderr)
        self.assertFalse(os.path.isdir(self.wt("feature-123-login-05")))

    def test_adopt_stops_when_another_branch_has_the_same_identifier(self):
        # origin の全ブランチで見る（MR の無い別ブランチ、手元に無いブランチも）
        git(self.ws, "push", "-q", self.bare, "main:refs/heads/topic-x")
        done = self.adopt("topic/x")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("識別子 topic-x", done.stderr)
        self.assertFalse(os.path.isdir(self.wt("topic-x")))

    def test_adopt_merged_check_unreachable_prints_guidance_and_mcp_checked_goes_on(self):
        git(self.ws, "push", "-q", self.bare, "main:refs/heads/topic/x")
        self.route({f"GET {GITHUB}/pulls/5": [200, pr(5, "topic/x")]})
        done = self.run_sh("--mr", "5")
        self.assertEqual(done.returncode, 4, done.stdout + done.stderr)
        self.assertIn("マージ済みの MR があるかを確かめられなかった", done.stdout)
        self.assertIn("state=closed", done.stdout)
        self.assertFalse(os.path.isdir(self.wt("topic-x")))
        again = self.run_sh("--mr", "5", "--mcp-checked")
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertTrue(os.path.isdir(self.wt("topic-x")))

    def test_unreachable_remote_stops_when_the_mr_is_adopted(self):
        # MR を採用するときは origin の全ブランチで
        # 識別子の重なりを確かめるので、git のリモートに届かないと止まる
        tip = git(self.ws, "commit-tree", "-p", "main", "-m", "work", "main^{tree}")
        git(self.ws, "branch", "fix-152-other", tip)
        self.route(
            {
                PULLS: [200, [pr(3, "fix-152-other", body="Closes #152")]],
                f"GET {GITHUB}/pulls/3": [200, pr(3, "fix-152-other", body="Closes #152")],
                **closed_pulls("fix-152-other"),
            }
        )
        shutil.rmtree(self.bare)
        done = self.run_sh("--issue", "152", "--mr", "3")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("origin のブランチ一覧を取れなかった", done.stderr)
        self.assertFalse(os.path.isdir(self.wt("fix-152-other")))

    # ---- 4. 毎回 fetch して早送りする

    def other_clone(self):
        """origin を別に clone した押し手（topic/x をチェックアウト済み）。"""
        other = os.path.join(self._tmp.name, "other")
        git(self._tmp.name, "clone", "-q", self.bare, other)
        for key, value in (("user.email", "t@example.invalid"), ("user.name", "t")):
            git(other, "config", key, value)
        git(other, "checkout", "-q", "topic/x")
        return other

    def test_existing_local_branch_is_fast_forwarded_every_time(self):
        git(self.ws, "push", "-q", self.bare, "main:refs/heads/topic/x")
        git(self.ws, "fetch", "-q", self.bare, "topic/x:refs/heads/topic/x")
        # origin だけ進める
        other = self.other_clone()
        git(other, "commit", "-q", "--allow-empty", "-m", "remote work")
        git(other, "push", "-q", "origin", "topic/x")
        tip = git(other, "rev-parse", "HEAD")
        self.assertNotEqual(git(self.ws, "rev-parse", "topic/x"), tip)
        self.route({f"GET {GITHUB}/pulls/5": [200, pr(5, "topic/x")], **closed_pulls("topic/x")})
        done = self.run_sh("--mr", "5")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(git(self.wt("topic-x"), "rev-parse", "HEAD"), tip)
        # 2 回目: ワークツリーがあっても取り直して早送りする
        git(other, "commit", "-q", "--allow-empty", "-m", "remote work 2")
        git(other, "push", "-q", "origin", "topic/x")
        tip = git(other, "rev-parse", "HEAD")
        again = self.run_sh("--mr", "5")
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertEqual(git(self.wt("topic-x"), "rev-parse", "HEAD"), tip)
        self.assertIn("早送り", again.stdout)

    def test_fast_forward_does_not_overwrite_an_ignored_file(self):
        git(self.ws, "push", "-q", self.bare, "main:refs/heads/topic/x")
        git(self.ws, "fetch", "-q", self.bare, "topic/x:refs/heads/topic/x")
        self.route({f"GET {GITHUB}/pulls/5": [200, pr(5, "topic/x")], **closed_pulls("topic/x")})
        first = self.run_sh("--mr", "5")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        wt = self.wt("topic-x")
        before = git(wt, "rev-parse", "HEAD")
        other = self.other_clone()
        write(os.path.join(other, "ign.txt"), "remote\n")
        git(other, "add", "ign.txt")
        git(other, "commit", "-q", "-m", "remote ign")
        git(other, "push", "-q", "origin", "topic/x")
        common = git(wt, "rev-parse", "--git-common-dir")
        common = common if os.path.isabs(common) else os.path.join(wt, common)
        write(os.path.join(common, "info", "exclude"), "ign.txt\n")
        write(os.path.join(wt, "ign.txt"), "手元の ignored\n")
        again = self.run_sh("--mr", "5")
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertEqual(before, git(wt, "rev-parse", "HEAD"))
        with open(os.path.join(wt, "ign.txt"), encoding="utf-8") as f:
            self.assertEqual("手元の ignored\n", f.read())

    # ---- 5. MR が閉じる issue
    # 一覧から issue を決める規則（--issue が含まれるか・0 件・複数・大文字小文字・
    # 別のリポジトリ）は Go の Resolve が見る

    def test_mr_with_a_linked_issue_in_another_repository_reports_it(self):
        self.mr_branch(**{GRAPHQL: [200, closing([(3, "other/repo")])]})
        done = self.run_sh("--mr", "5")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn(
            "必須: 提案の frontmatter に issue: other/repo#3 と mr: 5 を書く", done.stdout
        )
        self.assertIn("--issue other/repo#3 --mr 5", done.stdout)

    def test_mr_that_does_not_close_the_issue_stops_before_the_worktree(self):
        self.mr_branch(**{GRAPHQL: [200, closing([])]})
        done = self.run_sh("--issue", "152", "--mr", "5")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("閉じるリンクを持たない", done.stderr)
        self.assertFalse(os.path.isdir(self.wt("fix-152-other")))

    def test_linked_issues_needing_a_sixth_page_stop_with_exit_4(self):
        self.mr_branch(**{GRAPHQL: [200, closing([(12, "acme/widgets")], more=True)]})
        done = self.run_sh("--mr", "5")
        self.assertEqual(done.returncode, 4, done.stdout + done.stderr)
        self.assertIn("mcp__github__pull_request_read", done.stdout)
        self.assertIn("--mr 5 --issue <issue の番号> --branch", done.stdout)
        self.assertFalse(os.path.isdir(self.wt("fix-152-other")))

    def test_a_blocked_graphql_falls_back_to_the_mr_body_only_on_403(self):
        def blocked(status):
            self.mr_branch(
                **{
                    GRAPHQL: [status, {"message": "refused"}],
                    f"GET {GITHUB}/pulls/5": [
                        200,
                        pr(5, "fix-152-other", body="機能を足す。\n\nCloses #12"),
                    ],
                }
            )
            return self.run_sh("--mr", "5")

        # 401 は認証の失敗なので、本文に切り替えない
        denied = blocked(401)
        self.assertEqual(denied.returncode, 4, denied.stdout + denied.stderr)
        self.assertNotIn("issue: 12", denied.stdout)
        # GraphQL を使えない環境（403）では、MR の本文の Closes を読む
        done = blocked(403)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("issue: 12 と mr: 5", done.stdout)

    def test_the_guided_rerun_after_an_unreadable_closing_link_succeeds(self):
        # 閉じる issue だけ読めない（GraphQL が errors）。案内の 3 引数で打ち直せば確定する
        self.mr_branch(
            **{
                GRAPHQL: [200, {"errors": [{"message": "boom"}], "data": None}],
                f"GET {GITHUB}/issues/152": [200, {"title": "Add login"}],
            }
        )
        first = self.run_sh("--mr", "5")
        self.assertEqual(first.returncode, 4, first.stdout + first.stderr)
        self.assertIn("mcp__github__issue_read", first.stdout)
        # 理由が空（「?」）にならない
        self.assertIn("紐づく issue を読めなかった", first.stdout)
        again = self.run_sh(
            "--mr", "5", "--issue", "152", "--branch", "fix-152-other", "--mcp-checked"
        )  # fmt: skip
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertIn("必須: 提案の frontmatter に issue: 152 と mr: 5 を書く", again.stdout)
        self.assertTrue(os.path.isdir(self.wt("fix-152-other")))

    def test_the_guided_rerun_does_not_pass_an_issue_that_does_not_exist(self):
        self.mr_branch(**{GRAPHQL: [200, {"errors": [{"message": "boom"}], "data": None}]})
        done = self.run_sh(
            "--mr", "5", "--issue", "999", "--branch", "fix-152-other", "--mcp-checked"
        )  # fmt: skip
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("issue #999 が見つからない", done.stderr)
        self.assertFalse(os.path.isdir(self.wt("fix-152-other")))


class StartShGitLabTest(StartShHarness):
    origin_url = GL_URL

    def test_gitlab_new_issue_creates_draft_mr(self):
        self.gitlab_routes()
        done = self.run_sh("--issue", "152")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(
            self.fields(done.stdout)["MR"], "https://gitlab.com/acme/widgets/-/merge_requests/9"
        )
        (body,) = self.posts()
        self.assertEqual(body["source_branch"], BRANCH)
        self.assertEqual(body["target_branch"], "main")
        self.assertEqual(body["title"], "Draft: Add login")
        self.assertEqual(body["description"], "Closes #152")
        self.assertIn(BRANCH, self.remote_branches())

    def test_gitlab_unreachable_at_create_has_manual_steps_and_no_mcp(self):
        self.gitlab_routes(created=False)
        done = self.run_sh("--issue", "152")
        self.assertEqual(done.returncode, 4, done.stdout + done.stderr)
        self.assertIn("GitLab の API に届かない（HTTP 404）", done.stdout)
        self.assertIn(f"ブランチ: {BRANCH}（push 済み）", done.stdout)
        self.assertNotIn("mcp__github__", done.stdout)
        self.gitlab_routes()
        again = self.run_sh("--issue", "152")
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertEqual(len(self.posts()), 2)

    def install_glab(self):
        """認証済みのglabのスタブ。本文付きでContent-Typeが無いと、GitLabと同じくHTTP 415を返す。
        それ以外はcurlのスタブ（ルート表）へ回す"""
        text = f"""#!/bin/sh
method=GET; ct=""; input=""; path=""
while [ $# -gt 0 ]; do
  case "$1" in
  api) ;;
  --hostname) shift ;;
  --method) method="$2"; shift ;;
  --header) case "$2" in 'Content-Type: application/json') ct=1 ;; esac; shift ;;
  --input) input=1; shift ;;
  *) path="$1" ;;
  esac
  shift
done
if [ -n "$input" ] && [ -z "$ct" ]; then
  cat >/dev/null
  echo '{{"error":"The provided content-type '"''"' is not supported."}}'
  echo 'glab: HTTP 415' >&2
  exit 1
fi
if [ -n "$input" ]; then
  exec '{self.bin}/curl' -X "$method" --data-binary @- "https://gitlab.com/api/v4/$path"
fi
exec '{self.bin}/curl' -X "$method" "https://gitlab.com/api/v4/$path"
"""
        os.chmod(write(os.path.join(self.bin, "glab"), text), 0o755)

    def test_gitlab_glab_sends_content_type_for_create(self):
        self.install_glab()
        self.gitlab_routes()
        done = self.run_sh("--issue", "152", token=False)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(
            self.fields(done.stdout)["MR"], "https://gitlab.com/acme/widgets/-/merge_requests/9"
        )
        (body,) = self.posts()
        self.assertEqual(body["title"], "Draft: Add login")

    def test_gitlab_unreachable_asks_the_user_for_auth_and_reruns(self):
        """GitLab は代行の手段がない。ユーザに認証を頼み、同じ引数で打ち直す。

        --mcp-checked は勧めない。
        """
        done = self.run_sh("--issue", "152", token=False)
        self.assertEqual(done.returncode, 4, done.stdout + done.stderr)
        self.assertIn("GitLab の API に届かない（glab が", done.stdout)
        self.assertIn("利用できず", done.stdout)
        (line,) = nexts(done.stdout)
        self.assertIn("ユーザに glab の gitlab.com への認証か GITLAB_TOKEN の用意を頼み", line)
        self.assertIn("ccnavi-start.sh '--issue' '152'。", line)
        self.assertIn("直接実装に切り替えず、ユーザに報告して止まる", line)
        self.assertIn("なし（ブランチもワークツリーも作っていない）", done.stdout)
        self.assertNotIn("--mcp-checked", done.stdout)
        self.assertNotIn("ブラウザ", done.stdout)
        self.assertNotIn("mcp__github__", done.stdout)

    def test_gitlab_new_issue_unreachable_asks_the_user(self):
        done = self.run_sh("--new-issue", "Add login", "--similar-checked", token=False)
        self.assertEqual(done.returncode, 4, done.stdout + done.stderr)
        self.assertIn(
            "次: --new-issue で作れなかったので、"
            "ユーザに、ブラウザで issue を作って番号を伝えるよう頼んで",
            done.stdout,
        )
        self.assertIn("glab を gitlab.com に認証する", done.stdout)
        self.assertIn("直接実装には切り替えない", done.stdout)
        self.assertNotIn("mcp__github__", done.stdout)

    def test_gitlab_new_issue_stops_when_a_similar_issue_or_mr_is_open(self):
        self.route(
            {
                f"GET {GITLAB}/issues?state=opened&per_page=100&page=1": [
                    200,
                    [{"iid": 9, "title": "Add login", "web_url": "https://gitlab.com/x/9"}],
                ],
                f"GET {GITLAB}/merge_requests?state=opened&per_page=100&page=1": [
                    200,
                    [
                        {
                            "iid": 4,
                            "title": "Draft: Add login page",
                            "web_url": "https://gitlab.com/x/4",
                        }
                    ],
                ],
            }
        )
        done = self.run_sh("--new-issue", "Add login")
        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        self.assertIn("issue #9  Add login", done.stdout)
        self.assertIn("MR !4  Draft: Add login page", done.stdout)
        self.assertEqual(self.posts(), [])

    def test_gitlab_check_access_levels(self):
        # 権限の読み方（member・プロジェクトの permissions）は Go の preflight が見る。
        # sh は user と member を取ってくる
        no_user = {
            f"GET {GL_API}/user": [404, {"message": "x"}],
            f"GET {GITLAB}": [200, {"id": 42}],
        }
        for routes, code, shown in (
            ({f"GET {GITLAB}/members/all/5": [200, {"access_level": 20}]}, 1, "NG"),
            (no_user, 0, "注意"),
        ):
            with self.subTest(shown=shown):
                done = self.check(routes)
                self.assertEqual(done.returncode, code, done.stdout + done.stderr)
                self.assertEqual(self.labels(done.stdout)["access"], shown)
        self.assertIn("権限を判定できない", done.stdout)

    def test_gitlab_check_without_a_token_suggests_glab_login(self):
        done = self.check(token=False)
        self.assertEqual(done.returncode, 4, done.stdout + done.stderr)
        got = self.labels(done.stdout)
        self.assertEqual((got["api"], got["access"], got["git"]), ("未確認", "省略", "OK"))
        self.assertIn("glab auth login --hostname gitlab.com", done.stdout)

    def test_gitlab_adopt_checks_the_merged_mr(self):
        git(self.ws, "push", "-q", self.bare, "main:refs/heads/topic/x")
        merged_url = (
            f"GET {GITLAB}/merge_requests?state=merged&source_branch=topic%2Fx&per_page=100&page=1"
        )
        base = {f"GET {GITLAB}": [200, {"id": 42}], merged_url: [200, []]}
        self.route(
            {
                **base,
                merged_url: [200, [self.gitlab_mr(iid=3, state="merged")]],
                f"GET {GITLAB}/merge_requests/9": [200, self.gitlab_mr()],
            }
        )
        done = self.run_sh("--mr", "9")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("マージ済み MR（!3）", done.stderr)
        self.assertFalse(os.path.isdir(self.wt("topic-x")))
        self.route({**base, f"GET {GITLAB}/merge_requests/9": [200, self.gitlab_mr()]})
        done = self.run_sh("--mr", "9")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(git(self.wt("topic-x"), "rev-parse", "--abbrev-ref", "HEAD"), "topic/x")

    def test_gitlab_fork_mr_is_refused(self):
        # フォークかどうかは、sh が取ったプロジェクトの id と MR の source_project_id で見る
        self.route(
            {
                f"GET {GITLAB}": [200, {"id": 42}],
                f"GET {GITLAB}/merge_requests/9": [200, self.gitlab_mr(source_project_id=77)],
            }
        )
        done = self.run_sh("--mr", "9")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("フォーク", done.stderr)


if __name__ == "__main__":
    unittest.main()
