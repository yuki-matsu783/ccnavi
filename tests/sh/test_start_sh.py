"""ccnavi-start.sh の受入テスト（新しく作る経路と事前確認）。

共通の部品と全体の説明は `tests/sh/start_sh_harness.py` にある。既存のブランチ・MR を採用する経路と
GitLab は `tests/sh/test_start_sh_adopt.py`。

見るのは次のとおり。

1. 候補 0 件で、ブランチ・ワークツリー・初期コミット・push・下書きの MR を作る
   （--issue・--new-issue）
2. --new-issue の似た issue・MR の確認と、意味の確認を待つ止まり方
3. ホストを見られないとき（MCP の案内と終了コード 4）と、MCP で確かめた後の打ち直し
4. 途中で失敗して打ち直したときに、重複を作らず続きから進むこと
5. closed の issue・マージ済みの MR・使えない識別子で、何も作らずに止まること
6. --check（事前確認）と書き込みの権限
7. 作成者情報が無い機械で、作ったワークツリーを片付けること
"""

from __future__ import annotations

import os
import subprocess
import unittest

from tests.sh.start_sh_harness import (
    BRANCH,
    DUMMY_TOKEN,
    GITHUB,
    INIT_SUBJECT,
    PULLS,
    ROOT,
    SHELL,
    StartShHarness,
    closed_pulls,
    git,
    nexts,
    pr,
    write,
)


class StartShTest(StartShHarness):
    # ---- 1. 候補 0 件で新しく作る

    def test_new_issue_creates_branch_worktree_commit_push_and_draft_mr(self):
        self.github_new()
        done = self.run_sh("--issue", "152")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        got = self.fields(done.stdout)
        self.assertEqual(got["ブランチ"], BRANCH)
        self.assertEqual(got["MR"], "https://github.com/acme/widgets/pull/7")
        self.assertEqual(got["対象リポジトリ"], "acme/widgets（ワークスペース自身）")
        self.assertTrue(got["ワークツリー"].endswith(f".claude/worktrees/{BRANCH}"), got)
        self.assertTrue(os.path.isdir(self.wt(BRANCH)))
        self.assertEqual(git(self.wt(BRANCH), "rev-parse", "--abbrev-ref", "HEAD"), BRANCH)
        self.assertEqual(git(self.wt(BRANCH), "log", "-1", "--format=%s"), INIT_SUBJECT)
        self.assertIn(BRANCH, self.remote_branches())
        (body,) = self.posts()
        self.assertEqual(body["head"], BRANCH)
        self.assertEqual(body["base"], "main")
        self.assertEqual(body["title"], "Add login")
        self.assertEqual(body["body"], "Closes #152")
        self.assertTrue(body["draft"])
        # 提案に MR の番号を書く（必須。チケットの `mr:` で、この MR の番号がチケットに結び付く）
        self.assertIn("必須: 提案の frontmatter に issue: 152 と mr: 7 を書く", done.stdout)
        # 親チケットも提案も無いので、提案を作る次の一手を、埋められる値を埋めて出す
        (line,) = nexts(done.stdout)
        self.assertIn(f"ccnavi-launcher.sh ticket new {BRANCH} --title 'Add login' ", line)
        self.assertIn("--plan quick-work --scope '<触るファイルの glob>' --issue 152 --mr 7", line)
        self.assertIn("直接実装に切り替えず、ユーザに報告して止まる", done.stdout)
        # 切った直後は、親チケットも提案も無いのが正常。失敗に見える「取り込まなかった」を出さない
        self.assertIn("ワークツリーを切った直後なので、取り込むものはありません", done.stdout)
        self.assertNotIn("取り込まなかった", done.stdout)
        self.assertNotIn("終了コード", done.stdout)

    def test_dry_run_creates_nothing(self):
        self.github_new()
        done = self.run_sh("--issue", "152", "--dry-run")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn(BRANCH, done.stdout)
        self.assertIn("Add login", done.stdout)
        self.nothing_made()
        # 次の一手は、--dry-run を外した同じコマンド
        (line,) = nexts(done.stdout)
        self.assertIn("--dry-run を外して実行する", line)
        self.assertTrue(line.endswith("ccnavi-start.sh '--issue' '152'"), line)

    def test_second_run_continues_without_duplicates(self):
        self.github_new()
        self.assertEqual(self.run_sh("--issue", "152").returncode, 0)
        self.route({PULLS: [200, [pr(7, BRANCH, body="Closes #152")]], **closed_pulls(BRANCH)})
        before = self.posts()
        done = self.run_sh("--issue", "152")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        # 既存の MR で続ける枝にも同じ案内
        self.assertIn("必須: 提案の frontmatter に issue: 152 と mr: 7 を書く", done.stdout)
        self.assertIn("次: ワークツリーの中で sh ", done.stdout)
        self.assertIn(f"ticket new {BRANCH} --title ", done.stdout)
        self.assertEqual(self.fields(done.stdout)["ブランチ"], BRANCH)
        self.assertEqual(self.fields(done.stdout)["MR"], "https://github.com/acme/widgets/pull/7")
        self.assertEqual(self.posts(), before)
        self.assertEqual(self.local_branches().count(BRANCH), 1)

    # ---- 2. issue がまだ無いとき（--new-issue）

    def test_new_issue_creates_the_issue_then_goes_on_like_issue(self):
        self.github_new_issue()
        done = self.run_sh(
            "--new-issue", "Add login", "--issue-body", "ログインを足す", "--similar-checked"
        )
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("issue #152 を作った", done.stdout)
        self.assertEqual(self.fields(done.stdout)["ブランチ"], BRANCH)
        issue, mr = self.posts()
        self.assertEqual(issue["title"], "Add login")
        self.assertTrue(issue["body"].startswith("ログインを足す"))
        self.assertEqual(mr["body"], "Closes #152")
        self.assertIn("必須: 提案の frontmatter に issue: 152 と mr: 7 を書く", done.stdout)
        self.assertEqual(git(self.wt(BRANCH), "log", "-1", "--format=%s"), INIT_SUBJECT)

    def test_new_issue_stops_and_asks_when_a_similar_issue_or_mr_is_open(self):
        self.github_new_issue(
            found=[
                {"number": 9, "title": "Add login", "html_url": "https://github.com/x/9"},
                {
                    "number": 10,
                    "title": "Add Login page",
                    "html_url": "https://github.com/x/10",
                    "pull_request": {},
                },
                {"number": 11, "title": "Fix typo in README", "html_url": "u"},
            ]
        )
        done = self.run_sh("--new-issue", "Add login")
        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        self.assertIn("issue #9  Add login", done.stdout)
        self.assertIn("MR !10  Add Login page", done.stdout)
        self.assertNotIn("#11", done.stdout)
        self.assertIn("'--new-issue' 'Add login' --similar-checked を打つ", done.stdout)
        self.nothing_made()

    def test_new_issue_waits_for_the_semantic_check_by_a_haiku_subagent(self):
        """機械的な確認で似たものが無くても、意味が近いかの確認が済むまで作らない。"""
        self.github_new_issue(
            found=[{"number": 11, "title": "Fix typo in README", "html_url": "u"}]
        )
        done = self.run_sh("--new-issue", "Add login")
        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        self.assertIn("issue「Add login」はまだ作っていません", done.stdout)
        (line,) = nexts(done.stdout)
        self.assertIn("haiku のサブエージェントに", line)
        self.assertIn("ccnavi-start.sh --list-open を実行させ", line)
        self.assertIn("'--new-issue' 'Add login' --similar-checked を打つ", done.stdout)
        self.assertIn("直接実装には切り替えない", done.stdout)
        self.nothing_made()

    def test_list_open_prints_open_issues_and_mrs_and_stops_when_the_host_is_unreachable(self):
        self.route(
            {
                f"GET {GITHUB}/issues?state=open&per_page=100&page=1": [
                    200,
                    [
                        {"number": 9, "title": "Add login", "body": "本文", "html_url": "u"},
                        {"number": 4, "title": "PR 4", "body": "x", "pull_request": {}},
                    ],
                ],
                PULLS: [200, [pr(4, "feature-4-x", body="Closes #9")]],
            }
        )
        done = self.run_sh("--list-open")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("issue #9  Add login", done.stdout)
        self.assertIn("MR !4  PR 4  ブランチ: feature-4-x", done.stdout)
        self.assertEqual(self.posts(), [])
        self.assertEqual(self.local_branches(), ["main"])
        down = self.run_sh("--list-open", token=False)
        self.assertEqual(down.returncode, 4, down.stdout + down.stderr)
        self.assertIn("mcp__github__list_issues", down.stdout)
        self.assertIn("直接実装にも切り替えず、ユーザに報告して止まる", down.stdout)

    def test_rerun_hint_after_the_issue_was_made_uses_issue_not_new_issue(self):
        self.github_new_issue()
        current = self.current_routes()
        for key in closed_pulls(BRANCH):
            current.pop(key, None)
        self.route(current)
        done = self.run_sh("--new-issue", "Add login", "--slug", "add-login", "--similar-checked")
        self.assertEqual(done.returncode, 4, done.stdout + done.stderr)
        self.assertIn("以降の打ち直しは --issue 152 で行う", done.stdout)
        self.assertIn("'--issue' '152' '--slug' 'add-login'", done.stdout)
        self.assertNotIn("'--new-issue'", done.stdout.split("MCP で行う手順")[-1])
        self.assertEqual(len(self.posts()), 1)

    def test_new_issue_dry_run_creates_nothing(self):
        self.github_new_issue()
        done = self.run_sh("--new-issue", "Add login", "--similar-checked", "--dry-run")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("dry-run: 何も作らない。issue「Add login」を作ってから", done.stdout)
        self.nothing_made()

    def test_new_issue_unreachable_host_points_to_mcp_and_stops(self):
        self.github_new_issue()
        done = self.run_sh("--new-issue", "Add login", "--similar-checked", token=False)
        self.assertEqual(done.returncode, 4, done.stdout + done.stderr)
        self.assertIn("mcp__github__issue_write", done.stdout)
        self.assertIn("直接実装にも切り替えず、ユーザに報告して止まる", done.stdout)
        self.assertEqual(self.posts(), [])

    def test_new_issue_create_failure_stops_and_asks_the_user(self):
        self.github_new_issue(created=False)
        done = self.run_sh("--new-issue", "Add login", "--similar-checked")
        self.assertEqual(done.returncode, 4, done.stdout + done.stderr)
        self.assertIn("issue を作れなかった", done.stderr)
        self.assertIn("ユーザに issue を作ってもらい", done.stderr)
        self.assertEqual(self.local_branches(), ["main"])

    # ---- 3. ホストを見られない

    def test_github_unreachable_at_search_prints_mcp_guidance_and_exits_4(self):
        done = self.run_sh("--issue", "152", token=False)
        self.assertEqual(done.returncode, 4, done.stdout + done.stderr)
        for word in (
            "mcp__github__search_pull_requests",
            "repo:acme/widgets",
            "--mcp-checked",
            "何も作成せずに処理を中断しました",
        ):
            self.assertIn(word, done.stdout)
        self.assertEqual(self.local_branches(), ["main"])
        self.assertFalse(os.path.isdir(os.path.join(self.ws, ".claude", "worktrees")))

    def test_title_unreadable_stops_even_with_slug_and_title_until_mcp_checked(self):
        # API が失敗して実在を確かめられない間は、--slug・--title があっても無確認で進まない
        self.route({PULLS: [200, []], f"GET {GITHUB}/issues/152": [500, {"message": "boom"}]})
        done = self.run_sh("--issue", "152", "--slug", "login", "--title", "Add login")
        self.assertEqual(done.returncode, 4, done.stdout + done.stderr)
        self.assertIn("実在", done.stdout)
        self.assertIn("--mcp-checked", done.stdout)
        self.nothing_made("feature-152-login")

    def test_title_unreadable_but_slug_given_goes_on_with_a_provisional_title(self):
        self.route(
            {
                PULLS: [200, []],
                f"GET {GITHUB}/issues/152": [500, {"message": "boom"}],
                f"GET {GITHUB}/pulls?state=open&head=acme%3Afeature-152-login": [200, []],
                **closed_pulls("feature-152-login"),
                f"POST {GITHUB}/pulls": [
                    201,
                    {"number": 8, "html_url": "https://github.com/acme/widgets/pull/8"},
                ],
            }
        )
        # MCP で実在を確かめた後の再実行（--mcp-checked）なら、仮のタイトルで進む
        done = self.run_sh("--issue", "152", "--slug", "login", "--mcp-checked")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.posts()[0]["title"], "#152 login")

    def test_github_unreachable_at_create_prints_mcp_guidance_then_rerun_continues(self):
        # MR の作成だけ届かない（POST のスタブが無い）。ブランチ・ワークツリー・push は済む
        self.github_new(created=False)
        done = self.run_sh("--issue", "152")
        self.assertEqual(done.returncode, 4, done.stdout + done.stderr)
        for word in (
            "mcp__github__create_pull_request",
            "mcp__github__list_pull_requests",
            "owner: acme",
            "repo: widgets",
            f"head: {BRANCH}",
            "base: main",
            "title: Add login",
            "body: Closes #152",
            "draft: true",
            "push 済み",
            "--mr <作った番号> --branch",
        ):
            self.assertIn(word, done.stdout)
        self.assertIn(BRANCH, self.remote_branches())
        self.assertTrue(os.path.isdir(self.wt(BRANCH)))
        # 打ち直し: MR が無くホストが答えるなら、続きの MR 作成から進む
        self.github_new()
        again = self.run_sh("--issue", "152")
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertEqual(self.fields(again.stdout)["MR"], "https://github.com/acme/widgets/pull/7")
        self.assertEqual(git(self.ws, "rev-list", "--count", f"origin/main..{BRANCH}"), "1")
        # POST は失敗した 1 回と、打ち直しの 1 回だけ（重複して作っていない）
        self.assertEqual(len(self.posts()), 2)

    def test_rerun_after_mcp_made_the_mr_follows_the_guided_commands(self):
        # チケットが無く、API に届かない状態から: --mcp-checked で進み、push まで済ませて
        # MR の作成で 4 → MCP で MR を作った想定 → 案内のコマンド
        # （--issue M --mr N --branch <ブランチ> --mcp-checked）で 0
        create = self.run_sh("--issue", "152", "--slug", "login", "--mcp-checked", token=False)
        self.assertEqual(create.returncode, 4, create.stdout + create.stderr)
        self.assertIn("mcp__github__create_pull_request", create.stdout)
        self.assertIn("head: feature-152-login", create.stdout)
        # issue の状態は読めない。止めず、確認: の行で未確認と出す
        self.assertIn("確認: issue #152 の状態: 未確認", create.stdout)
        self.assertIn("feature-152-login", self.remote_branches())
        self.assertEqual(self.posts(), [])
        cmd, args = self.guided_args(create.stdout, "打ち直す: ")
        self.assertEqual(
            args,
            ["--issue", "152", "--mr", "<作った番号>", "--branch", "feature-152-login"]
            + ["--mcp-checked"],
        )
        # エージェントが MCP で PR（7 番）を作った。ホストは見られないまま、案内のコマンドを打つ
        args[args.index("--mr") + 1] = "7"
        done = self.run_sh(*args, token=False)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertTrue(
            self.fields(done.stdout)["ワークツリー"].endswith("worktrees/feature-152-login")
        )
        self.assertIn("必須: 提案の frontmatter に issue: 152 と mr: 7 を書く", done.stdout)
        self.assertIn("ホストは見ていない（MCP などで確認済みとして扱った）", done.stdout)
        self.assertIn("--issue 152 --mr 7", done.stdout)
        self.assertEqual(self.posts(), [])

    # ---- 5. 何も作らずに止まる

    def test_issue_not_found_or_forbidden_exits_2_even_with_slug_and_title(self):
        # 404 はホストには届いている。MCP へ回さず、番号を確かめてもらう
        self.route({PULLS: [200, []]})
        done = self.run_sh("--issue", "152")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("issue #152 が見つからない", done.stderr)
        self.assertNotIn("mcp__github__", done.stdout)
        # --slug・--title を付けても、ホストが読めるなら issue の存在を確かめる。
        # 作らず、次の手を案内する
        again = self.run_sh("--issue", "152", "--slug", "memo", "--title", "メモアプリ")
        self.assertEqual(again.returncode, 2, again.stdout + again.stderr)
        self.assertIn("issue #152 が見つからない", again.stderr)
        self.assertIn("ユーザに作ってもらい", again.stderr)
        self.nothing_made("feature-152-memo")
        # 403 は権限が無いと言う
        self.route({PULLS: [200, []], f"GET {GITHUB}/issues/152": [403, {"message": "no"}]})
        forbidden = self.run_sh("--issue", "152")
        self.assertEqual(forbidden.returncode, 2, forbidden.stdout + forbidden.stderr)
        self.assertIn("権限", forbidden.stderr)

    def test_a_closed_issue_stops_and_reopened_lets_it_through(self):
        self.github_new()
        routes = self.current_routes()
        routes[f"GET {GITHUB}/issues/152"] = [200, {"title": "Add login", "state": "closed"}]
        self.route(routes)
        done = self.run_sh("--issue", "152")
        self.assertEqual(done.returncode, 3, done.stdout + done.stderr)
        self.assertIn("issue #152 は closed", done.stdout)
        # 次の手: は 1 つで、番号つきの選択肢を並べる。次: は出さない
        self.assertEqual(
            len([line for line in done.stdout.splitlines() if line.startswith("次の手:")]), 1
        )
        self.assertEqual(nexts(done.stdout), [])
        # 値が埋まっていて、そのまま打てる（--reopened を付けた打ち直しも）
        self.assertIn("ccnavi-start.sh '--issue' '152' --reopened", done.stdout)
        self.nothing_made()
        # --dry-run でも止め、打ち直しの案内に --dry-run は残さない
        dry = self.run_sh("--issue", "152", "--dry-run")
        self.assertEqual(dry.returncode, 3, dry.stdout + dry.stderr)
        self.assertNotIn("--dry-run", dry.stdout)
        # --reopened で通す
        reopened = self.run_sh("--issue", "152", "--reopened")
        self.assertEqual(reopened.returncode, 0, reopened.stdout + reopened.stderr)
        self.assertIn("--reopened が付いている", reopened.stdout)
        self.assertIn(BRANCH, self.remote_branches())
        self.assertEqual(self.posts()[0]["title"], "Add login")

    def test_a_merged_mr_gate_comes_before_the_closed_issue_stop(self):
        merged = {**pr(2, BRANCH), "merged_at": "2026-01-01T00:00:00Z", "state": "closed"}
        self.github_new()
        routes = self.current_routes()
        routes[f"GET {GITHUB}/issues/152"] = [200, {"title": "Add login", "state": "closed"}]
        self.route({**routes, **closed_pulls(BRANCH, [merged])})
        done = self.run_sh("--issue", "152")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("マージ済み MR（!2）", done.stderr)
        self.assertNotIn("は closed", done.stdout)
        self.nothing_made()

    def test_a_long_title_stops_and_asks_for_a_short_slug(self):
        """タイトルが長くて slug が切れるときは、何も作らずに止め、
        短い --slug での打ち直しを案内する。--slug を付ければ、同じ issue で進める。
        """
        title = "テキスト整形CLI（main.sh）を README の仕様どおりに実装する"
        self.github_new(title=title, branch="feature-152-text-formatter-cli")
        done = self.run_sh("--issue", "152")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("タイトルが長く", done.stderr)
        (line,) = nexts(done.stderr)
        self.assertIn("--issue", line)
        self.assertIn("--slug <語>", line)
        self.nothing_made("feature-152-text-formatter-cli")
        done = self.run_sh("--issue", "152", "--slug", "text-formatter-cli", "--dry-run")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("feature-152-text-formatter-cli", done.stdout)

    def test_an_unusable_identifier_stops_before_anything_is_made(self):
        """--slug で作る識別子が親に使えなければ（ここでは末尾が -2 桁）、何も作らずに止める。"""
        branch = "feature-152-login-12"
        self.github_new(branch=branch)
        done = self.run_sh("--issue", "152", "--slug", "login-12")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("末尾", done.stderr)
        self.nothing_made(branch)

    def test_a_repo_that_differs_from_cwd_stops_before_asking_the_host(self):
        proj = os.path.join(self.ws, "projects", "tour")
        os.makedirs(proj)
        git(proj, "init", "-q", "-b", "main")
        git(proj, "remote", "add", "origin", "https://github.com/Acme/Tour.git")
        done = self.run_sh("--issue", "2", "--repo", "acme/tour")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("/projects/tour' && sh '", done.stderr)
        self.assertIn("/.ccnavi/scripts/ccnavi-start.sh' '--issue' '2'", done.stderr)
        self.assertEqual(self.requests(), [])
        self.assertEqual(self.local_branches(), ["main"])
        # 同じリポジトリ（大文字小文字違い）なら止めない
        same = self.run_sh("--issue", "152", "--repo", "ACME/Widgets", "--dry-run")
        self.assertNotIn("が違います", same.stderr)
        os.remove(self.log)
        # projects/ の中から、ワークスペース自身のリポジトリを頼むと、ルートへの移動を案内する
        up = self.run_sh("--issue", "152", "--repo", "acme/widgets", cwd=proj)
        self.assertEqual(up.returncode, 1, up.stdout + up.stderr)
        self.assertIn("ワークスペースルートに移動", up.stderr)
        # projects/ に無いリポジトリは、移動先を案内せず止める
        gone = self.run_sh("--issue", "2", "--repo", "acme/none")
        self.assertEqual(gone.returncode, 1, gone.stdout + gone.stderr)
        self.assertIn("projects/ に見つかりません", gone.stderr)
        self.assertEqual(self.requests(), [])

    def test_host_encode_percent_encodes_japanese_branch_names(self):
        # 日本語を生でクエリに入れるとホストが HTML のエラーを返す。エンコードして渡す
        script = os.path.join(ROOT, ".ccnavi", "scripts", "ccnavi-common-host.sh")
        done = subprocess.run(
            [
                SHELL,
                "-c",
                '. "$1"; ccnavi_h_jq=$(command -v jq); ccnavi_host_encode "$2"',
                "sh",
                script,
                "acme:feature-418-状態/jsonl",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.strip(), "acme%3Afeature-418-%E7%8A%B6%E6%85%8B%2Fjsonl")

    def test_help(self):
        done = self.run_sh("--help")
        self.assertEqual(done.returncode, 0)
        self.assertIn("終了コード", done.stdout)

    # ---- 6. 事前確認（--check）と権限
    # 項目ごとの判定（401・404・5xx・push の権限・git の認証・作成者）は Go の preflight の表が見る

    def test_check_all_ok_creates_nothing_and_tells_to_try_new_issue(self):
        done = self.check()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertTrue(done.stdout.startswith("対象リポジトリ: acme/widgets"), done.stdout)
        self.assertEqual(
            self.labels(done.stdout),
            {k: "OK" for k in ("tools", "origin", "integration", "api", "access", "git", "ident")},
        )
        (line,) = nexts(done.stdout)
        self.assertIn("--new-issue <タイトル> を打つ", line)
        self.assertIn("作れなかった", done.stdout)
        self.assertEqual(self.local_branches(), ["main"])
        self.assertEqual(self.remote_branches(), ["main"])
        self.assertFalse(os.path.isdir(os.path.join(self.ws, ".claude", "worktrees")))
        self.assertNotIn(DUMMY_TOKEN, done.stdout + done.stderr)

    def test_check_without_a_git_author_is_ng_and_asks_for_the_config(self):
        done = self.check(**self.no_identity())
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(self.labels(done.stdout)["ident"], "NG")
        self.assertEqual(self.labels(done.stdout)["git"], "OK")
        self.assertIn("git config user.name", done.stdout)
        self.assertIn("不備があり", done.stdout)
        self.assertNotIn("--new-issue", done.stdout)

    def test_check_without_a_token_is_unverified_and_still_checks_git(self):
        done = self.check(token=False)
        self.assertEqual(done.returncode, 4, done.stdout + done.stderr)
        got = self.labels(done.stdout)
        self.assertEqual((got["api"], got["access"], got["git"]), ("未確認", "省略", "OK"))
        self.assertIn("GITHUB_TOKEN", done.stdout)

    def test_check_local_path_origin_is_ng(self):
        git(self.ws, "remote", "set-url", "origin", self.bare)
        done = self.check()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(self.labels(done.stdout)["origin"], "NG")
        self.assertEqual(self.labels(done.stdout)["api"], "省略")

    def test_check_times_out_on_a_hanging_curl(self):
        done = self.check(token=True, FAKE_SLEEP="30", CCNAVI_PREFLIGHT_TIMEOUT="2")
        self.assertEqual(done.returncode, 4, done.stdout + done.stderr)
        self.assertEqual(self.labels(done.stdout)["api"], "未確認")

    def test_check_times_out_on_a_hanging_gh(self):
        write(os.path.join(self.bin, "gh"), "#!/bin/sh\nsleep 30\n")
        os.chmod(os.path.join(self.bin, "gh"), 0o755)
        done = self.check(token=False, CCNAVI_PREFLIGHT_TIMEOUT="2")
        self.assertEqual(done.returncode, 4, done.stdout + done.stderr)
        self.assertEqual(self.labels(done.stdout)["api"], "未確認")

    def test_insufficient_role_stops_before_anything_is_made(self):
        self.github_new()
        self.route(
            {
                **self.current_routes(),
                f"GET {GITHUB}": [200, {"permissions": {"push": False}}],
            }
        )
        done = self.run_sh("--issue", "152")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("書き込みの権限が足りない", done.stderr)
        self.nothing_made()

    def test_insufficient_role_stops_a_resumed_start_before_the_worktree(self):
        # 作りかけ（初期コミットだけ・MR が無い）の再開も、ワークツリーを切る前に止める
        self.initial_branch()
        self.route(
            {
                PULLS: [200, []],
                f"GET {GITHUB}": [200, {"permissions": {"push": False}}],
                **closed_pulls(BRANCH),
            }
        )
        done = self.run_sh("--issue", "152")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("書き込みの権限が足りない", done.stderr)
        self.assertFalse(os.path.isdir(self.wt(BRANCH)))
        self.assertEqual(self.posts(), [])

    def test_a_resumed_start_checks_the_issue_before_the_worktree(self):
        self.initial_branch()
        # issue 152 は 404（既定の routes に無い）。作りかけでも、実在しない issue の MR は作らない
        self.route({PULLS: [200, []], **closed_pulls(BRANCH)})
        done = self.run_sh("--issue", "152")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("issue #152 が見つからない", done.stderr)
        self.assertFalse(os.path.isdir(self.wt(BRANCH)))
        self.assertEqual(self.posts(), [])

    # ---- 7. 作成者情報が無い機械

    def test_a_missing_author_stops_with_guidance_and_removes_the_new_worktree(self):
        self.github_new()
        done = self.run_sh("--issue", "152", **self.no_identity())
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("作成者情報", done.stderr)
        self.assertNotIn("許可リスト", done.stdout + done.stderr)
        self.assertIn("git config user.name", done.stdout)
        self.assertFalse(os.path.isdir(self.wt(BRANCH)))
        self.assertNotIn(BRANCH, self.local_branches())
        self.assertEqual(self.posts(), [])

    def test_a_branch_that_cannot_be_deleted_is_reported_as_the_only_leftover(self):
        # 手元の統合先が origin より遅れていると、
        # 切ったばかりの空のブランチでも branch -d が通らない
        tip = git(self.ws, "commit-tree", "-p", "main", "-m", "ahead", "main^{tree}")
        git(self.ws, "push", "-q", self.bare, f"{tip}:refs/heads/main")
        self.github_new()
        done = self.run_sh("--issue", "152", **self.no_identity())
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertFalse(os.path.isdir(self.wt(BRANCH)))
        self.assertIn(BRANCH, self.local_branches())
        self.assertIn("ブランチ " + BRANCH + " が残っている", done.stdout)
        self.assertNotIn("worktree remove", done.stdout)

    def test_a_resumed_start_keeps_its_worktree_without_an_author(self):
        # 続き（初期コミットが既にある）はコミットしないので、作成者が無くても通り、残る
        self.initial_branch()
        self.github_new()
        done = self.run_sh("--issue", "152", **self.no_identity())
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertTrue(os.path.isdir(self.wt(BRANCH)))


if __name__ == "__main__":
    unittest.main()
