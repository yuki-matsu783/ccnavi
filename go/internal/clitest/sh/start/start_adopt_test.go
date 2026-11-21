package start

import (
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/run"
)

// ccnavi-start.sh の受入テスト（既存のブランチ・MR を採用する経路と GitLab）。土台は startenv_test.go。新しく作る経路と事前確認は
// start_new_test.go。
//
// 見るのは次のとおり。
//  1. 手元の候補が 1 件（MR があればユーザに聞き、--mr で採用し、ワークツリーができた後は聞かずに続ける）
//  2. `/` を含むブランチの MR はブランチを直接チェックアウトする
//  3. 採用するときの確認（識別子・origin の全ブランチでの識別子の重なり・マージ済みの MR）と、確かめられないときの案内
//  4. 毎回 fetch して早送りする（無視されたファイルは上書きしない）
//  5. MR が閉じる issue（GraphQL が断られる環境の本文への切り替え・ページ送り・案内のとおりの打ち直し）
//  6. GitLab の経路

// ---- 1. 手元の候補が 1 件

func TestSingleLocalCandidateGetsAWorktree(t *testing.T) {
	t.Parallel()
	e := newStart(t, ghURL)
	git(t, e.ws, "push", "-q", e.bare, "main:refs/heads/fix-152-other")
	git(t, e.ws, "branch", "fix-152-other", "origin/main")
	e.route(combine(routes{
		pulls:                       ok(arr{prBody(3, "fix-152-other", "Closes #152")}),
		"GET " + ghAPI + "/pulls/3": ok(prBody(3, "fix-152-other", "Closes #152")),
	}, closedPulls("fix-152-other")))
	// このPCにそのブランチのワークツリーが無いので、--issue だけでは聞いて止まる
	asked := code(t, e.sh("--issue", "152"), 3)
	has(t, asked.Stdout, "issue #152 に紐づく MR !3", "--issue 152 --mr 3 を打つ", "直接実装には切り替えない")
	truth(t, !isDir(e.wt("fix-152-other")), "聞く前にワークツリーを切った")
	// ユーザが続けると決めたら、--mr を付けて打ち直す
	done := code(t, e.sh("--issue", "152", "--mr", "3"), 0)
	truth(t, isDir(e.wt("fix-152-other")), "ワークツリーが無い")
	eq(t, len(e.posts()), 0, "POST の数")
	eq(t, fields(done.Stdout)["ブランチ"], "fix-152-other", "ブランチ")
	// ワークツリーができたあとの再開は、聞かずに続けて、ワークツリーを知らせる
	again := code(t, e.sh("--issue", "152"), 0)
	truth(t, strings.HasSuffix(slash(fields(again.Stdout)["ワークツリー"]), "worktrees/fix-152-other"), fields(again.Stdout)["ワークツリー"])
}

// MR の無い候補（名前に番号を含むブランチ）も、親に使えない識別子なら出さずに止める。
func TestACandidateWithoutAnMRIsCheckedToo(t *testing.T) {
	t.Parallel()
	e := newStart(t, ghURL)
	tip := git(t, e.ws, "commit-tree", "-p", "main", "-m", "work", "main^{tree}")
	git(t, e.ws, "branch", "fix-152-other-05", tip)
	e.route(routes{pulls: ok(arr{})})
	done := code(t, e.sh("--issue", "152"), 1)
	has(t, done.Stderr, "末尾")
	truth(t, !isDir(e.wt("fix-152-other-05")), "ワークツリーを切った")
}

// ---- 2. `/` を含むブランチ

func TestMRWithSlashBranchChecksOutDirectlyWithoutIdentifierBranch(t *testing.T) {
	t.Parallel()
	e := newStart(t, ghURL)
	git(t, e.ws, "push", "-q", e.bare, "main:refs/heads/topic/x")
	e.route(combine(routes{"GET " + ghAPI + "/pulls/5": ok(prOf(5, "topic/x"))}, closedPulls("topic/x")))
	done := code(t, e.sh("--mr", "5"), 0)
	got := fields(done.Stdout)
	eq(t, got["ブランチ"], "topic/x", "ブランチ")
	truth(t, strings.HasSuffix(slash(got["ワークツリー"]), "worktrees/topic-x"), got["ワークツリー"])
	eq(t, git(t, e.wt("topic-x"), "rev-parse", "--abbrev-ref", "HEAD"), "topic/x", "ワークツリーのブランチ")
	// 識別子のブランチ（topic-x）は作らない。switch の回り道もしない
	eq(t, strings.Join(e.localBranches(), ","), "main,topic/x", "手元のブランチ")
	lacks(t, done.Stdout, "switch")
	// MR が閉じる issue を frontmatter に書く案内
	has(t, done.Stdout, "識別子: topic-x", "必須: 提案の frontmatter に issue: 152 と mr: 5 を書く")
}

// ---- 3. 採用するときの確認

func TestAdoptStopsWhenIdentifierIsUnusable(t *testing.T) {
	t.Parallel()
	e := newStart(t, ghURL)
	done := code(t, e.adopt("feature/123-login-05", nil, nil), 1)
	has(t, done.Stderr, "使えない", "末尾")
	truth(t, !isDir(e.wt("feature-123-login-05")), "ワークツリーを切った")
}

// origin の全ブランチで見る（MR の無い別ブランチ、手元に無いブランチも）。
func TestAdoptStopsWhenAnotherBranchHasTheSameIdentifier(t *testing.T) {
	t.Parallel()
	e := newStart(t, ghURL)
	git(t, e.ws, "push", "-q", e.bare, "main:refs/heads/topic-x")
	done := code(t, e.adopt("topic/x", nil, nil), 1)
	has(t, done.Stderr, "識別子 topic-x")
	truth(t, !isDir(e.wt("topic-x")), "ワークツリーを切った")
}

func TestAdoptMergedCheckUnreachablePrintsGuidanceAndMCPCheckedGoesOn(t *testing.T) {
	t.Parallel()
	e := newStart(t, ghURL)
	git(t, e.ws, "push", "-q", e.bare, "main:refs/heads/topic/x")
	e.route(routes{"GET " + ghAPI + "/pulls/5": ok(prOf(5, "topic/x"))})
	done := code(t, e.sh("--mr", "5"), 4)
	has(t, done.Stdout, "マージ済みの MR があるかを確かめられなかった", "state=closed")
	truth(t, !isDir(e.wt("topic-x")), "ワークツリーを切った")
	code(t, e.sh("--mr", "5", "--mcp-checked"), 0)
	truth(t, isDir(e.wt("topic-x")), "ワークツリーが無い")
}

// MR を採用するときは origin の全ブランチで識別子の重なりを確かめるので、git のリモートに届かないと止まる。
func TestUnreachableRemoteStopsWhenTheMRIsAdopted(t *testing.T) {
	t.Parallel()
	e := newStart(t, ghURL)
	tip := git(t, e.ws, "commit-tree", "-p", "main", "-m", "work", "main^{tree}")
	git(t, e.ws, "branch", "fix-152-other", tip)
	e.route(combine(routes{
		pulls:                       ok(arr{prBody(3, "fix-152-other", "Closes #152")}),
		"GET " + ghAPI + "/pulls/3": ok(prBody(3, "fix-152-other", "Closes #152")),
	}, closedPulls("fix-152-other")))
	remove(t, e.bare)
	done := code(t, e.sh("--issue", "152", "--mr", "3"), 1)
	has(t, done.Stderr, "origin のブランチ一覧を取れなかった")
	truth(t, !isDir(e.wt("fix-152-other")), "ワークツリーを切った")
}

// ---- 4. 毎回 fetch して早送りする

// otherClone は origin を別に clone した押し手（topic/x をチェックアウト済み）。
func (e *startEnv) otherClone() string {
	e.t.Helper()
	other := filepath.Join(e.base, "other")
	git(e.t, e.base, "clone", "-q", e.bare, other)
	identity(e.t, other)
	git(e.t, other, "checkout", "-q", "topic/x")
	return other
}

func TestExistingLocalBranchIsFastForwardedEveryTime(t *testing.T) {
	t.Parallel()
	e := newStart(t, ghURL)
	git(t, e.ws, "push", "-q", e.bare, "main:refs/heads/topic/x")
	git(t, e.ws, "fetch", "-q", e.bare, "topic/x:refs/heads/topic/x")
	// origin だけ進める
	other := e.otherClone()
	git(t, other, "commit", "-q", "--allow-empty", "-m", "remote work")
	git(t, other, "push", "-q", "origin", "topic/x")
	tip := git(t, other, "rev-parse", "HEAD")
	truth(t, git(t, e.ws, "rev-parse", "topic/x") != tip, "前提: 手元は遅れている")
	e.route(combine(routes{"GET " + ghAPI + "/pulls/5": ok(prOf(5, "topic/x"))}, closedPulls("topic/x")))
	code(t, e.sh("--mr", "5"), 0)
	eq(t, git(t, e.wt("topic-x"), "rev-parse", "HEAD"), tip, "1 回目の HEAD")
	// 2 回目: ワークツリーがあっても取り直して早送りする
	git(t, other, "commit", "-q", "--allow-empty", "-m", "remote work 2")
	git(t, other, "push", "-q", "origin", "topic/x")
	tip = git(t, other, "rev-parse", "HEAD")
	again := code(t, e.sh("--mr", "5"), 0)
	eq(t, git(t, e.wt("topic-x"), "rev-parse", "HEAD"), tip, "2 回目の HEAD")
	has(t, again.Stdout, "早送り")
}

func TestFastForwardDoesNotOverwriteAnIgnoredFile(t *testing.T) {
	t.Parallel()
	e := newStart(t, ghURL)
	git(t, e.ws, "push", "-q", e.bare, "main:refs/heads/topic/x")
	git(t, e.ws, "fetch", "-q", e.bare, "topic/x:refs/heads/topic/x")
	e.route(combine(routes{"GET " + ghAPI + "/pulls/5": ok(prOf(5, "topic/x"))}, closedPulls("topic/x")))
	code(t, e.sh("--mr", "5"), 0)
	wt := e.wt("topic-x")
	before := git(t, wt, "rev-parse", "HEAD")
	other := e.otherClone()
	write(t, filepath.Join(other, "ign.txt"), "remote\n")
	git(t, other, "add", "ign.txt")
	git(t, other, "commit", "-q", "-m", "remote ign")
	git(t, other, "push", "-q", "origin", "topic/x")
	write(t, filepath.Join(gitPath(t, wt, "info"), "exclude"), "ign.txt\n")
	write(t, filepath.Join(wt, "ign.txt"), "手元の ignored\n")
	code(t, e.sh("--mr", "5"), 0)
	eq(t, git(t, wt, "rev-parse", "HEAD"), before, "HEAD")
	eq(t, read(t, filepath.Join(wt, "ign.txt")), "手元の ignored\n", "ignored のファイル")
}

// ---- 5. MR が閉じる issue
// 一覧から issue を決める規則（--issue が含まれるか・0 件・複数・大文字小文字・別のリポジトリ）は Go の Resolve が見る。

func TestMRWithALinkedIssueInAnotherRepositoryReportsIt(t *testing.T) {
	t.Parallel()
	e := newStart(t, ghURL)
	e.mrBranch("fix-152-other", 5, routes{graphql: ok(closing(false, [2]any{3, "other/repo"}))})
	done := code(t, e.sh("--mr", "5"), 0)
	has(t, done.Stdout, "必須: 提案の frontmatter に issue: other/repo#3 と mr: 5 を書く", "--issue other/repo#3 --mr 5")
}

func TestMRThatDoesNotCloseTheIssueStopsBeforeTheWorktree(t *testing.T) {
	t.Parallel()
	e := newStart(t, ghURL)
	e.mrBranch("fix-152-other", 5, routes{graphql: ok(closing(false))})
	done := code(t, e.sh("--issue", "152", "--mr", "5"), 1)
	has(t, done.Stderr, "閉じるリンクを持たない")
	truth(t, !isDir(e.wt("fix-152-other")), "ワークツリーを切った")
}

func TestLinkedIssuesNeedingASixthPageStopWithExit4(t *testing.T) {
	t.Parallel()
	e := newStart(t, ghURL)
	e.mrBranch("fix-152-other", 5, routes{graphql: ok(closing(true, [2]any{12, "acme/widgets"}))})
	done := code(t, e.sh("--mr", "5"), 4)
	has(t, done.Stdout, "mcp__github__pull_request_read", "--mr 5 --issue <issue の番号> --branch")
	truth(t, !isDir(e.wt("fix-152-other")), "ワークツリーを切った")
}

// GraphQL を使えない環境（403）では MR の本文の Closes を読む。401 は認証の失敗なので、本文に切り替えない。
func TestABlockedGraphQLFallsBackToTheMRBodyOnlyOn403(t *testing.T) {
	t.Parallel()
	e := newStart(t, ghURL)
	blocked := func(status int) run.Result {
		e.mrBranch("fix-152-other", 5, routes{
			graphql:                     reply(status, obj{"message": "refused"}),
			"GET " + ghAPI + "/pulls/5": ok(prBody(5, "fix-152-other", "機能を足す。\n\nCloses #12")),
		})
		return e.sh("--mr", "5")
	}
	denied := code(t, blocked(401), 4)
	lacks(t, denied.Stdout, "issue: 12")
	done := code(t, blocked(403), 0)
	has(t, done.Stdout, "issue: 12 と mr: 5")
}

// 閉じる issue だけ読めない（GraphQL が errors）。案内の 3 引数で打ち直せば確定する。
func TestTheGuidedRerunAfterAnUnreadableClosingLinkSucceeds(t *testing.T) {
	t.Parallel()
	e := newStart(t, ghURL)
	e.mrBranch("fix-152-other", 5, routes{
		graphql:                        ok(obj{"errors": arr{obj{"message": "boom"}}, "data": nil}),
		"GET " + ghAPI + "/issues/152": ok(obj{"title": "Add login"}),
	})
	first := code(t, e.sh("--mr", "5"), 4)
	has(t, first.Stdout, "mcp__github__issue_read",
		// 理由が空（「?」）にならない
		"紐づく issue を読めなかった")
	again := code(t, e.sh("--mr", "5", "--issue", "152", "--branch", "fix-152-other", "--mcp-checked"), 0)
	has(t, again.Stdout, "必須: 提案の frontmatter に issue: 152 と mr: 5 を書く")
	truth(t, isDir(e.wt("fix-152-other")), "ワークツリーが無い")
}

func TestTheGuidedRerunDoesNotPassAnIssueThatDoesNotExist(t *testing.T) {
	t.Parallel()
	e := newStart(t, ghURL)
	e.mrBranch("fix-152-other", 5, routes{graphql: ok(obj{"errors": arr{obj{"message": "boom"}}, "data": nil})})
	done := code(t, e.sh("--mr", "5", "--issue", "999", "--branch", "fix-152-other", "--mcp-checked"), 1)
	has(t, done.Stderr, "issue #999 が見つからない")
	truth(t, !isDir(e.wt("fix-152-other")), "ワークツリーを切った")
}

// ---- 6. GitLab

func TestGitLabNewIssueCreatesDraftMR(t *testing.T) {
	t.Parallel()
	e := newStart(t, glURL)
	e.gitlabRoutes(true)
	done := code(t, e.sh("--issue", "152"), 0)
	eq(t, fields(done.Stdout)["MR"], "https://gitlab.com/acme/widgets/-/merge_requests/9", "MR")
	posts := e.posts()
	if len(posts) != 1 {
		t.Fatalf("POST = %v", posts)
	}
	eq(t, posts[0]["source_branch"], any(branch), "source_branch")
	eq(t, posts[0]["target_branch"], any("main"), "target_branch")
	eq(t, posts[0]["title"], any("Draft: Add login"), "title")
	eq(t, posts[0]["description"], any("Closes #152"), "description")
	truth(t, contains(e.remoteBranches(), branch), "origin に push していない")
}

func TestGitLabUnreachableAtCreateHasManualStepsAndNoMCP(t *testing.T) {
	t.Parallel()
	e := newStart(t, glURL)
	e.gitlabRoutes(false)
	done := code(t, e.sh("--issue", "152"), 4)
	has(t, done.Stdout, "GitLab の API に届かない（HTTP 404）", "ブランチ: "+branch+"（push 済み）")
	lacks(t, done.Stdout, "mcp__github__")
	e.gitlabRoutes(true)
	code(t, e.sh("--issue", "152"), 0)
	eq(t, len(e.requestsOf("POST "+glProj+"/merge_requests")), 2, "MR の POST の数")
}

// installGlab は認証済みの glab のスタブ。本文付きで Content-Type が無いと、GitLab と同じく HTTP 415 を返す。
// それ以外は curl のスタブ（ルート表）へ回す。
func (e *startEnv) installGlab() {
	e.t.Helper()
	text := `#!/bin/sh
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
  echo '{"error":"The provided content-type '"''"' is not supported."}'
  echo 'glab: HTTP 415' >&2
  exit 1
fi
if [ -n "$input" ]; then
  exec '%BIN%/curl' -X "$method" --data-binary @- "https://gitlab.com/api/v4/$path"
fi
exec '%BIN%/curl' -X "$method" "https://gitlab.com/api/v4/$path"
`
	writeExec(e.t, filepath.Join(e.bin, "glab"), strings.ReplaceAll(text, "%BIN%", slash(e.bin)))
}

func TestGitLabGlabSendsContentTypeForCreate(t *testing.T) {
	t.Parallel()
	e := newStart(t, glURL)
	e.installGlab()
	e.gitlabRoutes(true)
	done := code(t, e.noTokenSh("--issue", "152"), 0)
	eq(t, fields(done.Stdout)["MR"], "https://gitlab.com/acme/widgets/-/merge_requests/9", "MR")
	posts := e.posts()
	if len(posts) != 1 {
		t.Fatalf("POST = %v", posts)
	}
	eq(t, posts[0]["title"], any("Draft: Add login"), "title")
}

// GitLab は代行の手段がない。ユーザに認証を頼み、同じ引数で打ち直す。--mcp-checked は勧めない。
func TestGitLabUnreachableAsksTheUserForAuthAndReruns(t *testing.T) {
	t.Parallel()
	e := newStart(t, glURL)
	done := code(t, e.noTokenSh("--issue", "152"), 4)
	has(t, done.Stdout, "GitLab の API に届かない（glab が", "利用できず", "なし（ブランチもワークツリーも作っていない）")
	lines := nexts(done.Stdout)
	if len(lines) != 1 {
		t.Fatalf("次: の行 = %v", lines)
	}
	has(t, lines[0], "ユーザに glab の gitlab.com への認証か GITLAB_TOKEN の用意を頼み", "ccnavi-start.sh '--issue' '152'。", "直接実装に切り替えず、ユーザに報告して止まる")
	lacks(t, done.Stdout, "--mcp-checked", "ブラウザ", "mcp__github__")
}

func TestGitLabNewIssueUnreachableAsksTheUser(t *testing.T) {
	t.Parallel()
	e := newStart(t, glURL)
	done := code(t, e.noTokenSh("--new-issue", "Add login", "--similar-checked"), 4)
	has(t, done.Stdout, "次: --new-issue で作れなかったので、ユーザに、ブラウザで issue を作って番号を伝えるよう頼んで",
		"glab を gitlab.com に認証する", "直接実装には切り替えない")
	lacks(t, done.Stdout, "mcp__github__")
}

func TestGitLabNewIssueStopsWhenASimilarIssueOrMRIsOpen(t *testing.T) {
	t.Parallel()
	e := newStart(t, glURL)
	e.route(routes{
		"GET " + glProj + "/issues?state=opened&per_page=100&page=1": ok(arr{obj{"iid": 9, "title": "Add login", "web_url": "https://gitlab.com/x/9"}}),
		"GET " + glProj + "/merge_requests?state=opened&per_page=100&page=1": ok(arr{
			obj{"iid": 4, "title": "Draft: Add login page", "web_url": "https://gitlab.com/x/4"},
		}),
	})
	done := code(t, e.sh("--new-issue", "Add login"), 3)
	has(t, done.Stdout, "issue #9  Add login", "MR !4  Draft: Add login page")
	eq(t, len(e.posts()), 0, "POST の数")
}

// 権限の読み方（member・プロジェクトの permissions）は Go の preflight が見る。sh は user と member を取ってくる。
func TestGitLabCheckAccessLevels(t *testing.T) {
	t.Parallel()
	for name, c := range map[string]struct {
		routes routes
		code   int
		shown  string
	}{
		"member の権限が足りない": {routes{"GET " + glProj + "/members/all/5": ok(obj{"access_level": 20})}, 1, "NG"},
		"user を読めない": {routes{
			"GET " + glAPI + "/user": reply(404, obj{"message": "x"}),
			"GET " + glProj:          ok(obj{"id": 42}),
		}, 0, "注意"},
	} {
		t.Run(name, func(t *testing.T) {
			t.Parallel()
			e := newStart(t, glURL)
			done := code(t, e.check(opt{}, c.routes), c.code)
			eq(t, labels(done.Stdout)["access"], c.shown, "access")
			if c.shown == "注意" {
				has(t, done.Stdout, "権限を判定できない")
			}
		})
	}
}

func TestGitLabCheckWithoutATokenSuggestsGlabLogin(t *testing.T) {
	t.Parallel()
	e := newStart(t, glURL)
	done := code(t, e.check(opt{noToken: true}), 4)
	got := labels(done.Stdout)
	eq(t, got["api"]+"/"+got["access"]+"/"+got["git"], "未確認/省略/OK", "api・access・git")
	has(t, done.Stdout, "glab auth login --hostname gitlab.com")
}

func TestGitLabAdoptChecksTheMergedMR(t *testing.T) {
	t.Parallel()
	e := newStart(t, glURL)
	git(t, e.ws, "push", "-q", e.bare, "main:refs/heads/topic/x")
	mergedURL := "GET " + glProj + "/merge_requests?state=merged&source_branch=topic%2Fx&per_page=100&page=1"
	baseRoutes := routes{"GET " + glProj: ok(obj{"id": 42}), mergedURL: ok(arr{})}
	e.route(combine(baseRoutes, routes{
		mergedURL:                             ok(arr{gitlabMR(obj{"iid": 3, "state": "merged"})}),
		"GET " + glProj + "/merge_requests/9": ok(gitlabMR(nil)),
	}))
	done := code(t, e.sh("--mr", "9"), 1)
	has(t, done.Stderr, "マージ済み MR（!3）")
	truth(t, !isDir(e.wt("topic-x")), "ワークツリーを切った")
	e.route(combine(baseRoutes, routes{"GET " + glProj + "/merge_requests/9": ok(gitlabMR(nil))}))
	code(t, e.sh("--mr", "9"), 0)
	eq(t, git(t, e.wt("topic-x"), "rev-parse", "--abbrev-ref", "HEAD"), "topic/x", "ワークツリーのブランチ")
}

// フォークかどうかは、sh が取ったプロジェクトの id と MR の source_project_id で見る。
func TestGitLabForkMRIsRefused(t *testing.T) {
	t.Parallel()
	e := newStart(t, glURL)
	e.route(routes{
		"GET " + glProj:                       ok(obj{"id": 42}),
		"GET " + glProj + "/merge_requests/9": ok(gitlabMR(obj{"source_project_id": 77})),
	})
	done := code(t, e.sh("--mr", "9"), 1)
	has(t, done.Stderr, "フォーク")
}
