package start

import (
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
)

// ccnavi-start.sh の受入テスト（新しく作る経路と事前確認）。土台は startenv_test.go。既存のブランチ・MR を採用する経路と GitLab は
// start_adopt_test.go。
//
// 見るのは次のとおり。
//  1. 候補 0 件で、ブランチ・ワークツリー・初期コミット・push・下書きの MR を作る（--issue・--new-issue）
//  2. --new-issue の似た issue・MR の確認と、意味の確認を待つ止まり方
//  3. ホストを見られないとき（MCP の案内と終了コード 4）と、MCP で確かめた後の打ち直し
//  4. 途中で失敗して打ち直したときに、重複を作らず続きから進むこと
//  5. closed の issue・マージ済みの MR・使えない識別子で、何も作らずに止まること
//  6. --check（事前確認）と書き込みの権限
//  7. 作成者情報が無い機械で、作ったワークツリーを片付けること
//
// 項目ごとの判定（401・404・5xx・push の権限・git の認証・作成者）と、ホストの応答の読み方・識別子の決まりは Go の本体の表が見る。

const addedMR = "必須: 提案の frontmatter に issue: 152 と mr: 7 を書く"

// ---- 1. 候補 0 件で新しく作る

func TestNewIssueCreatesBranchWorktreeCommitPushAndDraftMR(t *testing.T) {
	t.Parallel()
	e := newStart(t, ghURL)
	e.githubNewDefault()
	done := code(t, e.sh("--issue", "152"), 0)
	got := fields(done.Stdout)
	eq(t, got["ブランチ"], branch, "ブランチ")
	eq(t, got["MR"], "https://github.com/acme/widgets/pull/7", "MR")
	eq(t, got["対象リポジトリ"], "acme/widgets（ワークスペース自身）", "対象リポジトリ")
	truth(t, strings.HasSuffix(slash(got["ワークツリー"]), ".claude/worktrees/"+branch), "ワークツリーの末尾: "+got["ワークツリー"])
	truth(t, isDir(e.wt(branch)), "ワークツリーが無い")
	eq(t, git(t, e.wt(branch), "rev-parse", "--abbrev-ref", "HEAD"), branch, "ワークツリーのブランチ")
	eq(t, git(t, e.wt(branch), "log", "-1", "--format=%s"), initSubject, "初期コミット")
	truth(t, contains(e.remoteBranches(), branch), "origin に push していない")
	posts := e.posts()
	if len(posts) != 1 {
		t.Fatalf("POST = %v", posts)
	}
	body := posts[0]
	eq(t, body["head"], any(branch), "head")
	eq(t, body["base"], any("main"), "base")
	eq(t, body["title"], any("Add login"), "title")
	eq(t, body["body"], any("Closes #152"), "body")
	eq(t, body["draft"], any(true), "draft")
	// 提案に MR の番号を書く（必須。チケットの `mr:` で、この MR の番号がチケットに結び付く）
	has(t, done.Stdout, addedMR)
	// 親チケットも提案も無いので、提案を作る次の一手を、埋められる値を埋めて出す
	lines := nexts(done.Stdout)
	if len(lines) != 1 {
		t.Fatalf("次: の行 = %v", lines)
	}
	has(t, lines[0], "ccnavi-launcher.sh ticket new "+branch+" --title 'Add login' ", "--plan quick-work --scope '<触るファイルの glob>' --issue 152 --mr 7")
	has(t, done.Stdout, "直接実装に切り替えず、ユーザに報告して止まる",
		// 切った直後は、親チケットも提案も無いのが正常。失敗に見える「取り込まなかった」を出さない
		"ワークツリーを切った直後なので、取り込むものはありません")
	lacks(t, done.Stdout, "取り込まなかった", "終了コード")
}

func TestStartDryRunCreatesNothing(t *testing.T) {
	t.Parallel()
	e := newStart(t, ghURL)
	e.githubNewDefault()
	done := code(t, e.sh("--issue", "152", "--dry-run"), 0)
	has(t, done.Stdout, branch, "Add login")
	e.nothingMade(branch)
	// 次の一手は、--dry-run を外した同じコマンド
	lines := nexts(done.Stdout)
	if len(lines) != 1 {
		t.Fatalf("次: の行 = %v", lines)
	}
	has(t, lines[0], "--dry-run を外して実行する")
	truth(t, strings.HasSuffix(lines[0], "ccnavi-start.sh '--issue' '152'"), lines[0])
}

func TestStartSecondRunContinuesWithoutDuplicates(t *testing.T) {
	t.Parallel()
	e := newStart(t, ghURL)
	e.githubNewDefault()
	code(t, e.sh("--issue", "152"), 0)
	e.route(combine(routes{pulls: ok(arr{prBody(7, branch, "Closes #152")})}, closedPulls(branch)))
	before := len(e.posts())
	done := code(t, e.sh("--issue", "152"), 0)
	// 既存の MR で続ける枝にも同じ案内
	has(t, done.Stdout, addedMR, "次: ワークツリーの中で sh ", "ticket new "+branch+" --title ")
	eq(t, fields(done.Stdout)["ブランチ"], branch, "ブランチ")
	eq(t, fields(done.Stdout)["MR"], "https://github.com/acme/widgets/pull/7", "MR")
	eq(t, len(e.posts()), before, "POST の数")
	n := 0
	for _, b := range e.localBranches() {
		if b == branch {
			n++
		}
	}
	eq(t, n, 1, "手元のブランチの数")
}

// ---- 2. issue がまだ無いとき（--new-issue）

func TestNewIssueCreatesTheIssueThenGoesOnLikeIssue(t *testing.T) {
	t.Parallel()
	e := newStart(t, ghURL)
	e.githubNewIssue(nil, true)
	done := code(t, e.sh("--new-issue", "Add login", "--issue-body", "ログインを足す", "--similar-checked"), 0)
	has(t, done.Stdout, "issue #152 を作った", addedMR)
	eq(t, fields(done.Stdout)["ブランチ"], branch, "ブランチ")
	posts := e.posts()
	if len(posts) != 2 {
		t.Fatalf("POST = %v", posts)
	}
	eq(t, posts[0]["title"], any("Add login"), "issue の title")
	truth(t, strings.HasPrefix(posts[0]["body"].(string), "ログインを足す"), "issue の本文")
	eq(t, posts[1]["body"], any("Closes #152"), "MR の本文")
	eq(t, git(t, e.wt(branch), "log", "-1", "--format=%s"), initSubject, "初期コミット")
}

func TestNewIssueStopsAndAsksWhenASimilarIssueOrMRIsOpen(t *testing.T) {
	t.Parallel()
	e := newStart(t, ghURL)
	e.githubNewIssue(arr{
		obj{"number": 9, "title": "Add login", "html_url": "https://github.com/x/9"},
		obj{"number": 10, "title": "Add Login page", "html_url": "https://github.com/x/10", "pull_request": obj{}},
		obj{"number": 11, "title": "Fix typo in README", "html_url": "u"},
	}, true)
	done := code(t, e.sh("--new-issue", "Add login"), 3)
	has(t, done.Stdout, "issue #9  Add login", "MR !10  Add Login page", "'--new-issue' 'Add login' --similar-checked を打つ")
	lacks(t, done.Stdout, "#11")
	e.nothingMade(branch)
}

// 機械的な確認で似たものが無くても、意味が近いかの確認が済むまで作らない。
func TestNewIssueWaitsForTheSemanticCheckByAHaikuSubagent(t *testing.T) {
	t.Parallel()
	e := newStart(t, ghURL)
	e.githubNewIssue(arr{obj{"number": 11, "title": "Fix typo in README", "html_url": "u"}}, true)
	done := code(t, e.sh("--new-issue", "Add login"), 3)
	has(t, done.Stdout, "issue「Add login」はまだ作っていません", "'--new-issue' 'Add login' --similar-checked を打つ", "直接実装には切り替えない")
	lines := nexts(done.Stdout)
	if len(lines) != 1 {
		t.Fatalf("次: の行 = %v", lines)
	}
	has(t, lines[0], "haiku のサブエージェントに", "ccnavi-start.sh --list-open を実行させ")
	e.nothingMade(branch)
}

func TestListOpenPrintsOpenIssuesAndMRsAndStopsWhenTheHostIsUnreachable(t *testing.T) {
	t.Parallel()
	e := newStart(t, ghURL)
	e.route(routes{
		"GET " + ghAPI + "/issues?state=open&per_page=100&page=1": ok(arr{
			obj{"number": 9, "title": "Add login", "body": "本文", "html_url": "u"},
			obj{"number": 4, "title": "PR 4", "body": "x", "pull_request": obj{}},
		}),
		pulls: ok(arr{prBody(4, "feature-4-x", "Closes #9")}),
	})
	done := code(t, e.sh("--list-open"), 0)
	has(t, done.Stdout, "issue #9  Add login", "MR !4  PR 4  ブランチ: feature-4-x")
	eq(t, len(e.posts()), 0, "POST の数")
	eq(t, strings.Join(e.localBranches(), ","), "main", "手元のブランチ")
	down := code(t, e.noTokenSh("--list-open"), 4)
	has(t, down.Stdout, "mcp__github__list_issues", "直接実装にも切り替えず、ユーザに報告して止まる")
}

func TestRerunHintAfterTheIssueWasMadeUsesIssueNotNewIssue(t *testing.T) {
	t.Parallel()
	e := newStart(t, ghURL)
	e.githubNewIssue(nil, true)
	e.drop(keysOf(closedPulls(branch))...)
	done := code(t, e.sh("--new-issue", "Add login", "--slug", "add-login", "--similar-checked"), 4)
	has(t, done.Stdout, "以降の打ち直しは --issue 152 で行う", "'--issue' '152' '--slug' 'add-login'")
	_, after, _ := strings.Cut(done.Stdout, "MCP で行う手順")
	lacks(t, after, "'--new-issue'")
	eq(t, len(e.posts()), 1, "POST の数")
}

func TestNewIssueDryRunCreatesNothing(t *testing.T) {
	t.Parallel()
	e := newStart(t, ghURL)
	e.githubNewIssue(nil, true)
	done := code(t, e.sh("--new-issue", "Add login", "--similar-checked", "--dry-run"), 0)
	has(t, done.Stdout, "dry-run: 何も作らない。issue「Add login」を作ってから")
	e.nothingMade(branch)
}

func TestNewIssueUnreachableHostPointsToMCPAndStops(t *testing.T) {
	t.Parallel()
	e := newStart(t, ghURL)
	e.githubNewIssue(nil, true)
	done := code(t, e.noTokenSh("--new-issue", "Add login", "--similar-checked"), 4)
	has(t, done.Stdout, "mcp__github__issue_write", "直接実装にも切り替えず、ユーザに報告して止まる")
	eq(t, len(e.posts()), 0, "POST の数")
}

func TestNewIssueCreateFailureStopsAndAsksTheUser(t *testing.T) {
	t.Parallel()
	e := newStart(t, ghURL)
	e.githubNewIssue(nil, false)
	done := code(t, e.sh("--new-issue", "Add login", "--similar-checked"), 4)
	has(t, done.Stderr, "issue を作れなかった", "ユーザに issue を作ってもらい")
	eq(t, strings.Join(e.localBranches(), ","), "main", "手元のブランチ")
}

// ---- 3. ホストを見られない

func TestGitHubUnreachableAtSearchPrintsMCPGuidanceAndExits4(t *testing.T) {
	t.Parallel()
	e := newStart(t, ghURL)
	done := code(t, e.noTokenSh("--issue", "152"), 4)
	has(t, done.Stdout, "mcp__github__search_pull_requests", "repo:acme/widgets", "--mcp-checked", "何も作成せずに処理を中断しました")
	eq(t, strings.Join(e.localBranches(), ","), "main", "手元のブランチ")
	truth(t, !isDir(filepath.Join(e.ws, ".claude", "worktrees")), "ワークツリーの置き場ができた")
}

// API が失敗して実在を確かめられない間は、--slug・--title があっても無確認で進まない。
func TestTitleUnreadableStopsEvenWithSlugAndTitleUntilMCPChecked(t *testing.T) {
	t.Parallel()
	e := newStart(t, ghURL)
	e.route(routes{pulls: ok(arr{}), "GET " + ghAPI + "/issues/152": reply(500, obj{"message": "boom"})})
	done := code(t, e.sh("--issue", "152", "--slug", "login", "--title", "Add login"), 4)
	has(t, done.Stdout, "実在", "--mcp-checked")
	e.nothingMade("feature-152-login")
}

func TestTitleUnreadableButSlugGivenGoesOnWithAProvisionalTitle(t *testing.T) {
	t.Parallel()
	e := newStart(t, ghURL)
	e.route(combine(routes{
		pulls:                          ok(arr{}),
		"GET " + ghAPI + "/issues/152": reply(500, obj{"message": "boom"}),
		"GET " + ghAPI + "/pulls?state=open&head=acme%3Afeature-152-login": ok(arr{}),
		"POST " + ghAPI + "/pulls": reply(201, obj{"number": 8, "html_url": "https://github.com/acme/widgets/pull/8"}),
	}, closedPulls("feature-152-login")))
	// MCP で実在を確かめた後の再実行（--mcp-checked）なら、仮のタイトルで進む
	code(t, e.sh("--issue", "152", "--slug", "login", "--mcp-checked"), 0)
	eq(t, e.posts()[0]["title"], any("#152 login"), "仮のタイトル")
}

func TestGitHubUnreachableAtCreatePrintsMCPGuidanceThenRerunContinues(t *testing.T) {
	t.Parallel()
	e := newStart(t, ghURL)
	// MR の作成だけ届かない（POST のスタブが無い）。ブランチ・ワークツリー・push は済む
	e.githubNew("Add login", false, branch)
	done := code(t, e.sh("--issue", "152"), 4)
	has(t, done.Stdout, "mcp__github__create_pull_request", "mcp__github__list_pull_requests", "owner: acme", "repo: widgets",
		"head: "+branch, "base: main", "title: Add login", "body: Closes #152", "draft: true", "push 済み", "--mr <作った番号> --branch")
	truth(t, contains(e.remoteBranches(), branch), "origin に push していない")
	truth(t, isDir(e.wt(branch)), "ワークツリーが無い")
	// 打ち直し: MR が無くホストが答えるなら、続きの MR 作成から進む
	e.githubNewDefault()
	again := code(t, e.sh("--issue", "152"), 0)
	eq(t, fields(again.Stdout)["MR"], "https://github.com/acme/widgets/pull/7", "MR")
	eq(t, git(t, e.ws, "rev-list", "--count", "origin/main.."+branch), "1", "初期コミットの数")
	// POST は失敗した 1 回と、打ち直しの 1 回だけ（重複して作っていない）
	eq(t, len(e.requestsOf("POST "+ghAPI+"/pulls")), 2, "MR の POST の数")
}

// requestsOf は記録の中の、line と同じ要求の行。
func (e *startEnv) requestsOf(line string) []string {
	var out []string
	for _, l := range e.requests() {
		if l == line {
			out = append(out, l)
		}
	}
	return out
}

// チケットが無く、API に届かない状態から --mcp-checked で進み、push まで済ませて MR の作成で 4 → MCP で MR を作った想定 →
// 案内のコマンド（--issue M --mr N --branch <ブランチ> --mcp-checked）で 0。
func TestRerunAfterMCPMadeTheMRFollowsTheGuidedCommands(t *testing.T) {
	t.Parallel()
	e := newStart(t, ghURL)
	create := code(t, e.noTokenSh("--issue", "152", "--slug", "login", "--mcp-checked"), 4)
	has(t, create.Stdout, "mcp__github__create_pull_request", "head: feature-152-login",
		// issue の状態は読めない。止めず、確認: の行で未確認と出す
		"確認: issue #152 の状態: 未確認")
	truth(t, contains(e.remoteBranches(), "feature-152-login"), "origin に push していない")
	eq(t, len(e.posts()), 0, "POST の数")
	args := guidedArgs(t, create.Stdout, "打ち直す: ")
	want := []string{"--issue", "152", "--mr", "<作った番号>", "--branch", "feature-152-login", "--mcp-checked"}
	eq(t, strings.Join(args, "\x00"), strings.Join(want, "\x00"), "案内の引数")
	// エージェントが MCP で PR（7 番）を作った。ホストは見られないまま、案内のコマンドを打つ
	for i, a := range args {
		if a == "--mr" {
			args[i+1] = "7"
		}
	}
	done := code(t, e.noTokenSh(args...), 0)
	truth(t, strings.HasSuffix(slash(fields(done.Stdout)["ワークツリー"]), "worktrees/feature-152-login"), fields(done.Stdout)["ワークツリー"])
	has(t, done.Stdout, addedMR, "ホストは見ていない（MCP などで確認済みとして扱った）", "--issue 152 --mr 7")
	eq(t, len(e.posts()), 0, "POST の数")
}

// ---- 5. 何も作らずに止まる

func TestIssueNotFoundOrForbiddenExits2EvenWithSlugAndTitle(t *testing.T) {
	t.Parallel()
	e := newStart(t, ghURL)
	// 404 はホストには届いている。MCP へ回さず、番号を確かめてもらう
	e.route(routes{pulls: ok(arr{})})
	done := code(t, e.sh("--issue", "152"), 2)
	has(t, done.Stderr, "issue #152 が見つからない")
	lacks(t, done.Stdout, "mcp__github__")
	// --slug・--title を付けても、ホストが読めるなら issue の存在を確かめる。作らず、次の手を案内する
	again := code(t, e.sh("--issue", "152", "--slug", "memo", "--title", "メモアプリ"), 2)
	has(t, again.Stderr, "issue #152 が見つからない", "ユーザに作ってもらい")
	e.nothingMade("feature-152-memo")
	// 403 は権限が無いと言う
	e.route(routes{pulls: ok(arr{}), "GET " + ghAPI + "/issues/152": reply(403, obj{"message": "no"})})
	forbidden := code(t, e.sh("--issue", "152"), 2)
	has(t, forbidden.Stderr, "権限")
}

func TestAClosedIssueStopsAndReopenedLetsItThrough(t *testing.T) {
	t.Parallel()
	e := newStart(t, ghURL)
	e.githubNewDefault()
	e.more(routes{"GET " + ghAPI + "/issues/152": ok(obj{"title": "Add login", "state": "closed"})})
	done := code(t, e.sh("--issue", "152"), 3)
	has(t, done.Stdout, "issue #152 は closed")
	// 次の手: は 1 つで、番号つきの選択肢を並べる。次: は出さない
	n := 0
	for _, line := range strings.Split(done.Stdout, "\n") {
		if strings.HasPrefix(line, "次の手:") {
			n++
		}
	}
	eq(t, n, 1, "次の手: の行の数")
	eq(t, len(nexts(done.Stdout)), 0, "次: の行の数")
	// 値が埋まっていて、そのまま打てる（--reopened を付けた打ち直しも）
	has(t, done.Stdout, "ccnavi-start.sh '--issue' '152' --reopened")
	e.nothingMade(branch)
	// --dry-run でも止め、打ち直しの案内に --dry-run は残さない
	dry := code(t, e.sh("--issue", "152", "--dry-run"), 3)
	lacks(t, dry.Stdout, "--dry-run")
	// --reopened で通す
	reopened := code(t, e.sh("--issue", "152", "--reopened"), 0)
	has(t, reopened.Stdout, "--reopened が付いている")
	truth(t, contains(e.remoteBranches(), branch), "origin に push していない")
	eq(t, e.posts()[0]["title"], any("Add login"), "title")
}

func TestAMergedMRGateComesBeforeTheClosedIssueStop(t *testing.T) {
	t.Parallel()
	e := newStart(t, ghURL)
	merged := prOf(2, branch)
	merged["merged_at"], merged["state"] = "2026-01-01T00:00:00Z", "closed"
	e.githubNewDefault()
	e.more(combine(routes{"GET " + ghAPI + "/issues/152": ok(obj{"title": "Add login", "state": "closed"})}, closedPulls(branch, merged)))
	done := code(t, e.sh("--issue", "152"), 1)
	has(t, done.Stderr, "マージ済み MR（!2）")
	lacks(t, done.Stdout, "は closed")
	e.nothingMade(branch)
}

// タイトルが長くて slug が切れるときは、何も作らずに止め、短い --slug での打ち直しを案内する。--slug を付ければ、同じ issue で進める。
func TestALongTitleStopsAndAsksForAShortSlug(t *testing.T) {
	t.Parallel()
	e := newStart(t, ghURL)
	e.githubNew("テキスト整形CLI（main.sh）を README の仕様どおりに実装する", true, "feature-152-text-formatter-cli")
	done := code(t, e.sh("--issue", "152"), 2)
	has(t, done.Stderr, "タイトルが長く")
	lines := nexts(done.Stderr)
	if len(lines) != 1 {
		t.Fatalf("次: の行 = %v", lines)
	}
	has(t, lines[0], "--issue", "--slug <語>")
	e.nothingMade("feature-152-text-formatter-cli")
	again := code(t, e.sh("--issue", "152", "--slug", "text-formatter-cli", "--dry-run"), 0)
	has(t, again.Stdout, "feature-152-text-formatter-cli")
}

// --slug で作る識別子が親に使えなければ（ここでは末尾が -2 桁）、何も作らずに止める。
func TestAnUnusableIdentifierStopsBeforeAnythingIsMade(t *testing.T) {
	t.Parallel()
	e := newStart(t, ghURL)
	br := "feature-152-login-12"
	e.githubNew("Add login", true, br)
	done := code(t, e.sh("--issue", "152", "--slug", "login-12"), 2)
	has(t, done.Stderr, "末尾")
	e.nothingMade(br)
}

func TestARepoThatDiffersFromCwdStopsBeforeAskingTheHost(t *testing.T) {
	t.Parallel()
	e := newStart(t, ghURL)
	proj := filepath.Join(e.ws, "projects", "tour")
	mkdir(t, proj)
	git(t, proj, "init", "-q", "-b", "main")
	git(t, proj, "remote", "add", "origin", "https://github.com/Acme/Tour.git")
	done := code(t, e.sh("--issue", "2", "--repo", "acme/tour"), 1)
	has(t, done.Stderr, "/projects/tour' && sh '", "/.ccnavi/scripts/ccnavi-start.sh' '--issue' '2'")
	eq(t, len(e.requests()), 0, "ホストへの要求の数")
	eq(t, strings.Join(e.localBranches(), ","), "main", "手元のブランチ")
	// 同じリポジトリ（大文字小文字違い）なら止めない
	same := e.sh("--issue", "152", "--repo", "ACME/Widgets", "--dry-run")
	lacks(t, same.Stderr, "が違います")
	remove(t, e.stub.Log)
	// projects/ の中から、ワークスペース自身のリポジトリを頼むと、ルートへの移動を案内する
	up := code(t, e.run(opt{dir: proj}, "--issue", "152", "--repo", "acme/widgets"), 1)
	has(t, up.Stderr, "ワークスペースルートに移動")
	// projects/ に無いリポジトリは、移動先を案内せず止める
	gone := code(t, e.sh("--issue", "2", "--repo", "acme/none"), 1)
	has(t, gone.Stderr, "projects/ に見つかりません")
	eq(t, len(e.requests()), 0, "ホストへの要求の数")
}

// 日本語を生でクエリに入れるとホストが HTML のエラーを返す。エンコードして渡す。
func TestHostEncodePercentEncodesJapaneseBranchNames(t *testing.T) {
	t.Parallel()
	need(t, "sh", "jq")
	script := filepath.Join(base.ScriptsDir(), "ccnavi-common-host.sh")
	r := run.Command(t, "sh", []string{"-c", `. "$1"; ccnavi_h_jq=$(command -v jq); ccnavi_host_encode "$2"`, "sh", slash(script), "acme:feature-418-状態/jsonl"})
	code(t, r, 0)
	eq(t, strings.TrimSpace(r.Stdout), "acme%3Afeature-418-%E7%8A%B6%E6%85%8B%2Fjsonl", "エンコード")
}

func TestStartHelp(t *testing.T) {
	t.Parallel()
	e := newStart(t, ghURL)
	has(t, code(t, e.sh("--help"), 0).Stdout, "終了コード")
}

// ---- 6. 事前確認（--check）と権限
// 項目ごとの判定（401・404・5xx・push の権限・git の認証・作成者）は Go の preflight の表が見る。

func TestCheckAllOKCreatesNothingAndTellsToTryNewIssue(t *testing.T) {
	t.Parallel()
	e := newStart(t, ghURL)
	done := code(t, e.check(opt{}), 0)
	truth(t, strings.HasPrefix(done.Stdout, "対象リポジトリ: acme/widgets"), done.Stdout)
	got := labels(done.Stdout)
	for _, k := range []string{"tools", "origin", "integration", "api", "access", "git", "ident"} {
		eq(t, got[k], "OK", "確認の "+k)
	}
	lines := nexts(done.Stdout)
	if len(lines) != 1 {
		t.Fatalf("次: の行 = %v", lines)
	}
	has(t, lines[0], "--new-issue <タイトル> を打つ")
	has(t, done.Stdout, "作れなかった")
	eq(t, strings.Join(e.localBranches(), ","), "main", "手元のブランチ")
	eq(t, strings.Join(e.remoteBranches(), ","), "main", "origin のブランチ")
	truth(t, !isDir(filepath.Join(e.ws, ".claude", "worktrees")), "ワークツリーの置き場ができた")
	lacks(t, out(done), dummyToken)
}

func TestCheckWithoutAGitAuthorIsNGAndAsksForTheConfig(t *testing.T) {
	t.Parallel()
	e := newStart(t, ghURL)
	done := code(t, e.check(opt{env: e.noIdentity()}), 1)
	got := labels(done.Stdout)
	eq(t, got["ident"], "NG", "ident")
	eq(t, got["git"], "OK", "git")
	has(t, done.Stdout, "git config user.name", "不備があり")
	lacks(t, done.Stdout, "--new-issue")
}

func TestCheckWithoutATokenIsUnverifiedAndStillChecksGit(t *testing.T) {
	t.Parallel()
	e := newStart(t, ghURL)
	done := code(t, e.check(opt{noToken: true}), 4)
	got := labels(done.Stdout)
	eq(t, got["api"]+"/"+got["access"]+"/"+got["git"], "未確認/省略/OK", "api・access・git")
	has(t, done.Stdout, "GITHUB_TOKEN")
}

func TestCheckLocalPathOriginIsNG(t *testing.T) {
	t.Parallel()
	e := newStart(t, ghURL)
	git(t, e.ws, "remote", "set-url", "origin", e.bare)
	done := code(t, e.check(opt{}), 1)
	got := labels(done.Stdout)
	eq(t, got["origin"], "NG", "origin")
	eq(t, got["api"], "省略", "api")
}

// ホストの応答が返らなくても、上限で切って「未確認」にする。curl も gh も同じ。
func TestCheckTimesOutOnAHangingHostTool(t *testing.T) {
	t.Parallel()
	for name, hang := range map[string]func(e *startEnv) opt{
		"curl": func(e *startEnv) opt {
			return opt{env: map[string]string{"FAKE_SLEEP": "30", "CCNAVI_PREFLIGHT_TIMEOUT": "2"}}
		},
		"gh": func(e *startEnv) opt {
			writeExec(e.t, filepath.Join(e.bin, "gh"), "#!/bin/sh\nsleep 30\n")
			return opt{noToken: true, env: map[string]string{"CCNAVI_PREFLIGHT_TIMEOUT": "2"}}
		},
	} {
		t.Run(name, func(t *testing.T) {
			t.Parallel()
			e := newStart(t, ghURL)
			done := code(t, e.check(hang(e)), 4)
			eq(t, labels(done.Stdout)["api"], "未確認", "api")
		})
	}
}

func TestInsufficientRoleStopsBeforeAnythingIsMade(t *testing.T) {
	t.Parallel()
	e := newStart(t, ghURL)
	e.githubNewDefault()
	e.more(routes{"GET " + ghAPI: ok(obj{"permissions": obj{"push": false}})})
	done := code(t, e.sh("--issue", "152"), 1)
	has(t, done.Stderr, "書き込みの権限が足りない")
	e.nothingMade(branch)
}

// 作りかけ（初期コミットだけ・MR が無い）の再開も、ワークツリーを切る前に止める。
func TestInsufficientRoleStopsAResumedStartBeforeTheWorktree(t *testing.T) {
	t.Parallel()
	e := newStart(t, ghURL)
	e.initialBranch(branch)
	e.route(combine(routes{pulls: ok(arr{}), "GET " + ghAPI: ok(obj{"permissions": obj{"push": false}})}, closedPulls(branch)))
	done := code(t, e.sh("--issue", "152"), 1)
	has(t, done.Stderr, "書き込みの権限が足りない")
	truth(t, !isDir(e.wt(branch)), "ワークツリーを切った")
	eq(t, len(e.posts()), 0, "POST の数")
}

func TestAResumedStartChecksTheIssueBeforeTheWorktree(t *testing.T) {
	t.Parallel()
	e := newStart(t, ghURL)
	e.initialBranch(branch)
	// issue 152 は 404（既定の routes に無い）。作りかけでも、実在しない issue の MR は作らない
	e.route(combine(routes{pulls: ok(arr{})}, closedPulls(branch)))
	done := code(t, e.sh("--issue", "152"), 2)
	has(t, done.Stderr, "issue #152 が見つからない")
	truth(t, !isDir(e.wt(branch)), "ワークツリーを切った")
	eq(t, len(e.posts()), 0, "POST の数")
}

// ---- 7. 作成者情報が無い機械

func TestAMissingAuthorStopsWithGuidanceAndRemovesTheNewWorktree(t *testing.T) {
	t.Parallel()
	e := newStart(t, ghURL)
	e.githubNewDefault()
	done := e.run(opt{env: e.noIdentity()}, "--issue", "152")
	truth(t, done.Code != 0, "終了コードが 0: "+out(done))
	has(t, done.Stderr, "作成者情報")
	lacks(t, out(done), "許可リスト")
	has(t, done.Stdout, "git config user.name")
	truth(t, !isDir(e.wt(branch)), "ワークツリーが残った")
	truth(t, !contains(e.localBranches(), branch), "ブランチが残った")
	eq(t, len(e.posts()), 0, "POST の数")
}

// 手元の統合先が origin より遅れていると、切ったばかりの空のブランチでも branch -d が通らない。
func TestABranchThatCannotBeDeletedIsReportedAsTheOnlyLeftover(t *testing.T) {
	t.Parallel()
	e := newStart(t, ghURL)
	tip := git(t, e.ws, "commit-tree", "-p", "main", "-m", "ahead", "main^{tree}")
	git(t, e.ws, "push", "-q", e.bare, tip+":refs/heads/main")
	e.githubNewDefault()
	done := e.run(opt{env: e.noIdentity()}, "--issue", "152")
	truth(t, done.Code != 0, "終了コードが 0: "+out(done))
	truth(t, !isDir(e.wt(branch)), "ワークツリーが残った")
	truth(t, contains(e.localBranches(), branch), "ブランチが消えた")
	has(t, done.Stdout, "ブランチ "+branch+" が残っている")
	lacks(t, done.Stdout, "worktree remove")
}

// 続き（初期コミットが既にある）はコミットしないので、作成者が無くても通り、残る。
func TestAResumedStartKeepsItsWorktreeWithoutAnAuthor(t *testing.T) {
	t.Parallel()
	e := newStart(t, ghURL)
	e.initialBranch(branch)
	e.githubNewDefault()
	code(t, e.run(opt{env: e.noIdentity()}, "--issue", "152"), 0)
	truth(t, isDir(e.wt(branch)), "ワークツリーが無い")
}
