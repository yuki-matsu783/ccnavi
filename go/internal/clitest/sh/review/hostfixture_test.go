package review

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"slices"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/hostfixture"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/shkit"
	"ccnavi/internal/infra/pyjson"
)

// ccnavi-review.sh がホストの応答から組む JSON と、マーカーのアカウント（`actor`）の受入テスト。
//
// 見るのは 3 つ。
//
//  1. 録ったホストの応答の見本（extensions/chrome/ccnavi-approval/test/fixtures/host/<github|gitlab>/<場面>/）から sh が組む JSON
//     （`fetch`。`fetched_at` を除く）が、見本の期待値（expected.json）と同じ。拡張の試験（CX-T129）も同じ見本から TS で組んで
//     同じ期待値と比べるので、sh と TS が同じ JSON を組む
//  2. 見本の結論（conclusion.json。変更要求と未解決のスレッド）が、場面の狙いどおり。expected.json から実行ファイルが出す結論が
//     conclusion.json と同じことは go test（go/internal/tickets/model/host の review_host_fixture_test.go）が見る
//  3. `confirm`・`decide` はトークンの持ち主を引いて `--actor` で渡す。引けなければ渡さない（マーカーは前と同じ）。呼び手が
//     `--actor` を渡しても受けない
//
// ホストの API が変わって見本を録り直したら、`go test ./internal/clitest/sh/review -run HostFixture -update-host-fixture` で
// expected.json を、同じ形で上の go test を回して conclusion.json を書き直す
// （手順は extensions/chrome/ccnavi-approval/README.md の「ホストの応答の見本」）。

var timestamp = regexp.MustCompile(`^\d{4}-\d\d-\d\dT`)

// jsonEqual は 2 つの JSON のテキストが、キーの順を問わず同じ値か。
func jsonEqual(t testing.TB, a, b string) bool {
	t.Helper()
	var x, y any
	if err := json.Unmarshal([]byte(a), &x); err != nil {
		t.Fatalf("JSON でない: %v\n%s", err, a)
	}
	if err := json.Unmarshal([]byte(b), &y); err != nil {
		t.Fatalf("JSON でない: %v\n%s", err, b)
	}
	return reflect.DeepEqual(x, y)
}

// checkScenes は、場面の名前の並びが想定どおりで、全部の場面で sh の fetch が expected.json と同じ JSON を組むこと。
func checkScenes(t *testing.T, kind hostfixture.Host, want []string, prepare func(h hostSite, scene string)) {
	t.Helper()
	if got := hostfixture.SceneNames(t, kind); !slices.Equal(got, want) {
		t.Fatalf("場面 = %v, want %v（足した・消したときはここも直す）", got, want)
	}
	for _, scene := range want {
		t.Run(scene, func(t *testing.T) {
			t.Parallel()
			where := hostfixture.SceneDir(kind, scene)
			expected := filepath.Join(where, "expected.json")
			info, err := os.Stat(expected)
			if err != nil {
				t.Fatal(err)
			}
			if !base.CanPassArgument(int(info.Size())) {
				t.Skipf("%d 字の引数を jq に渡せない（引数の上限）", info.Size())
			}
			h := newHostSite(t, kind)
			if prepare != nil {
				prepare(h, scene)
			}
			done := h.review(scene, nil, "fetch")
			if done.Code != 0 {
				t.Fatalf("code = %d\n%s", done.Code, done.Stderr)
			}
			parsed, err := pyjson.Loads(done.Stdout)
			if err != nil {
				t.Fatalf("JSON でない: %v\n%s", err, done.Stdout)
			}
			obj := parsed.(*pyjson.Object)
			if at, _ := obj.Pop("fetched_at"); !timestamp.MatchString(fmt.Sprint(at)) {
				t.Errorf("fetched_at = %v", at)
			}
			copy, err := pyjson.Dumps(obj, pyjson.Options{RawUnicode: true, Indent: 2})
			if err != nil {
				t.Fatal(err)
			}
			if *updateHostFixture {
				shkit.Write(t, expected, copy+"\n")
			}
			if !jsonEqual(t, copy, shkit.Read(t, expected)) {
				t.Errorf("%s の expected.json が古い（-update-host-fixture で書き直す）\nsh が組んだ結果:\n%s", scene, copy)
			}
		})
	}
}

// concluded は場面の結論（conclusion.json）。
func concluded(t *testing.T, kind hostfixture.Host, scene string) (changes, unresolved []any) {
	t.Helper()
	var said struct {
		ChangesRequested []any `json:"changes_requested"`
		Unresolved       []any `json:"unresolved"`
	}
	if err := json.Unmarshal([]byte(shkit.Read(t, filepath.Join(hostfixture.SceneDir(kind, scene), "conclusion.json"))), &said); err != nil {
		t.Fatal(err)
	}
	return said.ChangesRequested, said.Unresolved
}

func TestGitHubHostFixtureCopyMatchesEachRecordedScene(t *testing.T) {
	t.Parallel()
	checkScenes(t, hostfixture.GitHub, []string{"changes-requested", "cr-commented", "full-page", "hostile", "paged", "pending", "resolved"}, nil)
}

func TestGitHubHostFixtureScenesSayWhatTheyAreFor(t *testing.T) {
	t.Parallel()
	for _, scene := range []string{"resolved", "full-page"} {
		if c, u := concluded(t, hostfixture.GitHub, scene); len(c)+len(u) != 0 {
			t.Errorf("%s: 変更要求 %d・未解決 %d, want 0・0", scene, len(c), len(u))
		}
	}
	// GitHub では目印で始まるスレッドもユーザのものとして数える（依頼はスレッドにならない）
	c, u := concluded(t, hostfixture.GitHub, "paged")
	if len(c) != 0 || len(u) != 4 || !slices.Contains(u, any("PRRT_kwDOAbCdEs5P2003")) {
		t.Errorf("paged: 変更要求 %v・未解決 %v", c, u)
	}
	for _, scene := range []string{"changes-requested", "cr-commented", "pending"} {
		if c, _ := concluded(t, hostfixture.GitHub, scene); len(c) != 1 {
			t.Errorf("%s: 変更要求 %d, want 1", scene, len(c))
		}
	}
	if _, u := concluded(t, hostfixture.GitHub, "hostile"); len(u) != 8 {
		t.Errorf("hostile: 未解決 %d, want 8", len(u))
	}
}

func TestGitHubConfirmPassesTheTokenOwnerAsTheActor(t *testing.T) {
	t.Parallel()
	h := newHostSite(t, hostfixture.GitHub)
	done := h.review("resolved", nil, "confirm", "--phase", "1")
	if passed := h.stub.passed(); done.Code != 0 || !contains(passed, "confirm") || !contains(passed, "--actor=octo-reviewer") {
		t.Errorf("code = %d, 渡した引数 = %v\n%s%s", done.Code, passed, done.Stdout, done.Stderr)
	}
}

func TestGitHubConfirmOnTheIntegrationBranchSaysToTypeItInTheParentWorktree(t *testing.T) {
	t.Parallel()
	h := newHostSite(t, hostfixture.GitHub)
	h.git("checkout", "-q", "-b", "main")
	setOriginHead(t, h.ws, "main")
	done := h.review("resolved", nil, "confirm", "--phase", "1")
	if done.Code != 2 || !strings.Contains(done.Stderr, "の中で打ってください") || !strings.Contains(done.Stderr, ".claude/worktrees/") ||
		strings.Contains(done.Stderr, "切り直し") || shkit.Exists(h.stub.Args) { // main を親と読んで、親のワークツリーを切り直せとは案内しない
		t.Errorf("code = %d\n%s%s", done.Code, done.Stdout, done.Stderr)
	}
}

func TestParentOptionIsRefusedExceptForReady(t *testing.T) {
	t.Parallel()
	h := newHostSite(t, hostfixture.GitHub)
	for _, sub := range [][]string{{"confirm", "--phase", "1"}, {"review-request", "--phase", "1"}, {"comment"}} {
		for _, parent := range [][]string{{"--parent", "feature-1"}, {"--parent=feature-1"}} {
			done := h.review("resolved", nil, append(slices.Clone(sub), parent...)...)
			if done.Code != 2 || !strings.Contains(done.Stderr, "--parent は ready だけです") {
				t.Errorf("%v %v: code = %d\n%s%s", sub, parent, done.Code, done.Stdout, done.Stderr)
			}
		}
	}
	if shkit.Exists(h.stub.Args) {
		t.Errorf("実行ファイルを起こした: %v", h.stub.passed())
	}
}

func TestUsageListsParentOnlyOnReady(t *testing.T) {
	t.Parallel()
	h := newHostSite(t, hostfixture.GitHub)
	done := h.review("resolved", nil, "--help")
	var found []string
	for _, l := range lines(done.Stdout) { // 並べて案内する別の sh（ccnavi-user-*.sh）の行は数えない
		if strings.Contains(l, "--parent") && !strings.Contains(l, "ccnavi-user-") {
			found = append(found, l)
		}
	}
	if len(found) != 1 || !strings.Contains(found[0], "ready") {
		t.Errorf("--parent を案内する行 = %v", found)
	}
}

func TestGitHubConfirmPassesNoActorWhenTheOwnerIsUnknown(t *testing.T) {
	t.Parallel()
	h := newHostSite(t, hostfixture.GitHub)
	done := h.review("resolved", map[string]string{"FAKE_GITHUB_NO_USER": "1"}, "confirm", "--phase", "1")
	passed := h.stub.passed()
	// 引けなかったことで止めず、ホストの失敗の文面も出さない
	if done.Code != 0 || !contains(passed, "confirm") || anyHasPrefix(passed, "--actor") || strings.Contains(done.Stderr, "失敗した") {
		t.Errorf("code = %d, 渡した引数 = %v\n%s%s", done.Code, passed, done.Stdout, done.Stderr)
	}
}

// 名前の形（ほかの形・改行コードの扱い）は Go の hostreply が見る。ここでは、形のおかしい名前を渡さないことだけを見る。
func TestAMalformedOwnerIsNotPassed(t *testing.T) {
	t.Parallel()
	h := newHostSite(t, hostfixture.GitHub)
	done := h.review(h.sceneWith("resolved", "a b;rm -rf /"), nil, "confirm", "--phase", "1")
	passed := h.stub.passed()
	if done.Code != 0 || !contains(passed, "confirm") || anyHasPrefix(passed, "--actor") {
		t.Errorf("code = %d, 渡した引数 = %v\n%s%s", done.Code, passed, done.Stdout, done.Stderr)
	}
}

// ghBin は gh が認証済みの形（疎通が通る）の置き場。curl は置かない（gh の経路だけを通す）。
func (h hostSite) ghBin() string {
	h.t.Helper()
	dir := filepath.Join(h.tmp, "ghbin")
	hostfixture.InstallGh(h.t, dir)
	return pathWith(dir)
}

func TestGitHubThroughGhTheOwnerIsPassedAndItsStderrIsNotMixedIn(t *testing.T) {
	t.Parallel()
	h := newHostSite(t, hostfixture.GitHub)
	done := h.review("resolved", map[string]string{"PATH": h.ghBin()}, "confirm", "--phase", "1")
	if passed := h.stub.passed(); done.Code != 0 || !contains(passed, "--actor=octo-reviewer") {
		t.Errorf("code = %d, 渡した引数 = %v\n%s%s", done.Code, passed, done.Stdout, done.Stderr)
	}
}

func TestAnAccountLookupThatIsTooSlowIsCutOffAndNoActorIsPassed(t *testing.T) {
	t.Parallel()
	h := newHostSite(t, hostfixture.GitHub)
	done := h.review("resolved", map[string]string{"PATH": h.ghBin(), "FAKE_GH_SLOW_USER": "5", "CCNAVI_REVIEW_ACCOUNT_TIMEOUT": "1"}, "confirm", "--phase", "1")
	if passed := h.stub.passed(); done.Code != 0 || !contains(passed, "confirm") || anyHasPrefix(passed, "--actor") {
		t.Errorf("code = %d, 渡した引数 = %v\n%s%s", done.Code, passed, done.Stdout, done.Stderr)
	}
}

func TestThroughGlabTheUsernameIsPassed(t *testing.T) {
	t.Parallel()
	h := newHostSite(t, hostfixture.GitHub)
	glab := shkit.WriteExec(t, filepath.Join(h.tmp, "glabbin", "glab"), `#!/bin/sh
echo 'glab: 雑音' >&2
for a in "$@"; do last="$a"; done
case "$last" in
user) printf '{"username": "lab-reviewer"}' ;;
*merge_requests\?*)
  printf '[{"iid": 7, "web_url": "u", "source_project_id": 1}]' ;;
*/discussions*|*/reviewers*) printf '[]' ;;
projects/*) printf '{"id": 1}' ;;
*) printf '{}' ;;
esac
`)
	h.git("remote", "set-url", "origin", "https://gitlab.example/g/p.git")
	done := h.review("resolved", map[string]string{"PATH": pathWith(filepath.Dir(glab))}, "confirm", "--phase", "1")
	if passed := h.stub.passed(); done.Code != 0 || !contains(passed, "--actor=lab-reviewer") {
		t.Errorf("code = %d, 渡した引数 = %v\n%s%s", done.Code, passed, done.Stdout, done.Stderr)
	}
}

// decide のマーカーにも actor と via（ボードは board、ターミナルは terminal）を渡す。
func TestDecidePassesTheTokenOwnerAndTheWay(t *testing.T) {
	t.Parallel()
	for via, args := range map[string][]string{"board": {"decide", "1", "--choices", "{}", "--digest", "d"}, "terminal": {"decide", "1"}} {
		t.Run(via, func(t *testing.T) {
			t.Parallel()
			h := newHostSite(t, hostfixture.GitHub)
			done := h.review("resolved", nil, args...)
			passed := h.stub.passed()
			at := slices.Index(passed, "user")
			// 書く形は実行ファイルのユーザ専用の入口（`user reviewed <N>`）で呼ぶ
			if done.Code != 0 || !contains(passed, "--accept-unresolved") || at < 0 || passed[at+1] != "reviewed" || contains(passed, "--reviewed") ||
				!contains(passed, "--actor=octo-reviewer") || !contains(passed, "--via="+via) {
				t.Errorf("code = %d, 渡した引数 = %v\n%s%s", done.Code, passed, done.Stdout, done.Stderr)
			}
		})
	}
}

func TestDecidePassesNoActorWithoutAnOwnerOrForAPreview(t *testing.T) {
	t.Parallel()
	cases := map[string]struct {
		args  []string
		extra map[string]string
	}{
		"持ち主を引けない": {[]string{"decide", "1", "--choices", "{}", "--digest", "d"}, map[string]string{"FAKE_GITHUB_NO_USER": "1"}},
		"preview":  {[]string{"decide", "1", "--preview"}, nil},
	}
	for name, c := range cases {
		t.Run(name, func(t *testing.T) {
			t.Parallel()
			h := newHostSite(t, hostfixture.GitHub)
			done := h.review("resolved", c.extra, c.args...)
			passed := h.stub.passed()
			if done.Code != 0 || !contains(passed, "--accept-unresolved") || anyHasPrefix(passed, "--actor", "--via") {
				t.Errorf("code = %d, 渡した引数 = %v\n%s%s", done.Code, passed, done.Stdout, done.Stderr)
			}
		})
	}
}

const (
	nodeA = "PRRT_kwDOAbCdEs5AAAAB"
	nodeB = "PRRT_kwDOAbCdEs5AAAAC"
)

// mutations は偽の GitHub が受けた書き込み（1 行 1 件の JSON）。
func mutations(t testing.TB, path string) []map[string]any {
	t.Helper()
	var out []map[string]any
	for _, l := range lines(readIf(path)) {
		if strings.TrimSpace(l) != "" {
			out = append(out, decode(t, l))
		}
	}
	return out
}

func (h hostSite) mutationsPath() string { return filepath.Join(h.tmp, "mutations.jsonl") }

// decideAccepting は、plan を計画にして decide（ボードの形）を起こす。書き込みは mutationsPath に残る。
func (h hostSite) decideAccepting(plan string, extra map[string]string) run.Result {
	h.t.Helper()
	env := map[string]string{"STUB_PLAN": plan, "FAKE_GITHUB_MUTATIONS": h.mutationsPath()}
	for k, v := range extra {
		env[k] = v
	}
	return h.review("resolved", env, "decide", "1", "--choices", "{}", "--digest", "d")
}

// GitHub は GraphQL の mutation。node id を変数で渡し、全部に返信してから解決する。
func TestDecideRepliesThenResolvesWithTheNodeIDThroughGraphQL(t *testing.T) {
	t.Parallel()
	h := newHostSite(t, hostfixture.GitHub)
	done := h.decideAccepting(h.plan(nodeA, nodeB), nil)
	if done.Code != 0 {
		t.Fatalf("code = %d\n%s%s", done.Code, done.Stdout, done.Stderr)
	}
	sent := mutations(t, h.mutationsPath())
	if len(sent) != 4 {
		t.Fatalf("書き込み %d 件, want 4: %v", len(sent), sent)
	}
	for i, node := range []string{nodeA, nodeB} {
		reply, resolve := sent[i], sent[2+i]
		query, vars := fmt.Sprint(reply["query"]), reply["variables"].(map[string]any)
		if !strings.Contains(query, "addPullRequestReviewThreadReply") || !strings.Contains(query, "pullRequestReviewThreadId:$id") ||
			vars["id"] != node || !strings.HasPrefix(fmt.Sprint(vars["b"]), "<!-- ccnavi:accept -->") {
			t.Errorf("返信 %d = %v", i, reply)
		}
		if !strings.Contains(fmt.Sprint(resolve["query"]), "resolveReviewThread(input:{threadId:$id})") ||
			!reflect.DeepEqual(resolve["variables"], map[string]any{"id": node}) {
			t.Errorf("解決 %d = %v", i, resolve) // 返信が先、解決はそのあと
		}
	}
	// 置く呼び出しは計画を読む
	if !contains(h.stub.passed(), "--accept-done") {
		t.Errorf("置く呼び出しが無い: %v", h.stub.passed())
	}
}

// GraphQL は 200 で errors を返す。それを失敗と見て、置く呼び出しを打たない。
func TestDecideStopsWhenTheReplyOrResolveMutationIsRefused(t *testing.T) {
	t.Parallel()
	for which, shown := range map[string]string{"reply": "返信を残せなかった", "resolve": "解決できなかった"} {
		t.Run(which, func(t *testing.T) {
			t.Parallel()
			h := newHostSite(t, hostfixture.GitHub)
			stopped := h.decideAccepting(h.plan(nodeA), map[string]string{"FAKE_GITHUB_FAIL_MUTATION": which})
			if stopped.Code != 1 || !strings.Contains(stopped.Stderr, shown) || contains(h.stub.passed(), "--accept-done") {
				t.Errorf("code = %d, 渡した引数 = %v\n%s%s", stopped.Code, h.stub.passed(), stopped.Stdout, stopped.Stderr)
			}
		})
	}
}

func TestDecideAndConfirmDoNotTakeAnActorFromTheCaller(t *testing.T) {
	t.Parallel()
	h := newHostSite(t, hostfixture.GitHub)
	if done := h.review("resolved", nil, "decide", "1", "--actor=someone"); done.Code != 2 {
		t.Errorf("decide: code = %d\n%s%s", done.Code, done.Stdout, done.Stderr)
	}
	for _, given := range [][]string{{"--actor", "someone"}, {"--actor=someone"}} {
		done := h.review("resolved", nil, append([]string{"confirm", "--phase", "1"}, given...)...)
		if done.Code != 2 || !strings.Contains(done.Stderr, "--actor を受けない") {
			t.Errorf("confirm %v: code = %d\n%s%s", given, done.Code, done.Stdout, done.Stderr)
		}
	}
	if shkit.Exists(h.stub.Args) {
		t.Errorf("実行ファイルを起こした: %v", h.stub.passed())
	}
}
