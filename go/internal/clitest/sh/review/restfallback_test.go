package review

import (
	"encoding/json"
	"fmt"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/hostfixture"
	"ccnavi/internal/harness/run"
)

// GraphQL が 403 の環境（Claude Code のクラウド）で、GitHub の 4 か所が REST に替わること。
//
// スレッドの取得（`threads`）・Draft 外し（`undraft`）・指摘への返信と解決（`accept_reply`・`accept_resolve`）は、GraphQL を先に
// 試し、使えない 403 のときだけ REST（`/ccr/` の道）に替える。GraphQL が通る環境の動きは変えない。認証の失敗（401）と別の理由の
// 403 は替えない。
//
//   - threads: GraphQL が通れば REST（`ccr/`・`pulls/<N>/comments`）を呼ばない。403 なら REST の応答から従来と同じ形の JSON を組む。
//     返信が重なったスレッドでは、last_* が最後のコメントを指す
//   - confirm: その JSON を実行ファイルに渡す。未解決のスレッドが 1 件ある
//   - undraft: 403 で `ccr/ready_for_review` を呼び、`draft` を読んで返す
//   - decide の返信と解決: 数字の id は REST、node id は GraphQL
//   - 401・別の理由の 403 は REST に落とさず失敗する。REST も失敗したら、拒否される手順（JSON を作らせる）を案内に書かない
//
// ホストは hostfixture の GitHub のスタブ。`FAKE_GITHUB_GRAPHQL` で GraphQL の断り方を選ぶ。

const prURL = "https://github.com/acme/widgets/pull/42"

// REST の代わりの道の見本。スレッドは 2 つ。
//
//	101: 未解決。101（最初の指摘）→ 102（返信）→ 103（指摘した人の再返信）の 3 つ
//	201: 解決済み
var restThreads = []map[string]any{
	{"resolved": false, "outdated": false, "path": "src/app.py", "line": 8, "comment_ids": []int{101, 102, 103}},
	{"resolved": true, "outdated": true, "path": "README.md", "line": nil, "comment_ids": []int{201}},
}

func restComment(id int, login, at, body string, more map[string]any) map[string]any {
	c := map[string]any{
		"id": id, "body": body, "html_url": fmt.Sprintf("%s#discussion_r%d", prURL, id), "created_at": at,
		"user": map[string]any{"login": login}, "path": "src/app.py", "line": 8,
	}
	for k, v := range more {
		c[k] = v
	}
	return c
}

var restComments = []map[string]any{
	restComment(101, "reviewer", "2026-10-01T00:00:00Z", "直してください", nil),
	restComment(102, "author", "2026-10-01T01:00:00Z", "直しました", map[string]any{"in_reply_to_id": 101}),
	restComment(103, "reviewer", "2026-10-01T02:00:00Z", "もう 1 点あります", map[string]any{"in_reply_to_id": 101}),
	restComment(201, "reviewer", "2026-10-01T03:00:00Z", "済み", map[string]any{"path": "README.md", "line": nil}),
	restComment(301, "someone", "2026-10-01T04:00:00Z", "このスレッドとは別の PR のコメント", nil),
}

// 組み上がるスレッドの形（返信が重なった 101 の last_* は最後のコメント）。
var restExpected = []map[string]any{
	{
		"id": "101", "resolved": false, "url": prURL + "#discussion_r101", "path": "src/app.py", "line": 8, "body": "直してください",
		"created_at": "2026-10-01T00:00:00Z", "last_id": "103", "last_at": "2026-10-01T02:00:00Z", "last_author": "reviewer",
	},
	{
		"id": "201", "resolved": true, "url": prURL + "#discussion_r201", "path": "README.md", "line": 0, "body": "済み",
		"created_at": "2026-10-01T03:00:00Z", "last_id": "201", "last_at": "2026-10-01T03:00:00Z", "last_author": "reviewer",
	},
}

// restSite は GitHub の見本の場面 resolved に、REST の見本・要求の記録・書き込みの記録を足したワークスペース。
type restSite struct {
	hostSite
	rest, log, muts string
}

func newRestSite(t *testing.T) restSite {
	t.Helper()
	h := newHostSite(t, hostfixture.GitHub)
	r := restSite{hostSite: h, rest: filepath.Join(h.tmp, "rest.json"), log: filepath.Join(h.tmp, "calls.log"), muts: filepath.Join(h.tmp, "mutations.jsonl")}
	r.setRest(restComments)
	return r
}

func (r restSite) setRest(comments []map[string]any) {
	r.t.Helper()
	write(r.t, r.rest, mustJSON(map[string]any{"review_threads": restThreads, "comments": comments, "body": "", "draft": true}))
}

// env は GraphQL の断り方（空なら通る）と、道（curl か gh）を決める。
func (r restSite) env(graphql, transport string, extra map[string]string) map[string]string {
	env := map[string]string{
		"FAKE_GITHUB_REST": r.rest, "FAKE_GITHUB_LOG": r.log, "FAKE_GITHUB_MUTATIONS": r.muts,
		"FAKE_GITHUB_DRAFT_STATE": filepath.Join(r.tmp, "draft.json"),
	}
	if graphql != "" {
		env["FAKE_GITHUB_GRAPHQL"] = graphql
	}
	if transport == "gh" {
		env["PATH"] = r.ghBin()
	}
	for k, v := range extra {
		env[k] = v
	}
	return env
}

func (r restSite) fetch(graphql, transport string, extra map[string]string, args ...string) run.Result {
	r.t.Helper()
	return r.review("resolved", r.env(graphql, transport, extra), args...)
}

// driver は ccnavi-review.sh と同じ支度をして、関数を直接呼ぶ（undraft・accept_*）。
func (r restSite) driver(body, graphql, transport string, extra map[string]string) run.Result {
	r.t.Helper()
	script := strings.Join([]string{
		"set -eu",
		". .ccnavi/scripts/ccnavi-common.sh",
		"review_label=ccnavi-test",
		"ccnavi_load ccnavi-common-review.sh",
		"ccnavi_load ccnavi-user-lib-review.sh",
		"sub=test",
		"review_place",
		"review_exe || review_no_bin",
		"review_host",
		`mkdir -p "$state"`,
		`eval "$(sed -n '/^undraft_ask() {/,/^}/p;/^undraft() {/,/^}/p' .ccnavi/scripts/ccnavi-review.sh)"`,
		body,
	}, "\n")
	// dirname $0 で共通部を探すので、配った sh と同じ場所に置いて打つ
	write(r.t, filepath.Join(r.scripts, "driver-test.sh"), script+"\n")
	return r.sh("driver-test.sh", run.Opts{Env: r.env2(graphql, transport, extra)})
}

// env2 は driver 用（review を通らないので、場面・トークン・実行ファイルの代わりの sh も足す）。
func (r restSite) env2(graphql, transport string, extra map[string]string) map[string]string {
	return r.hostSite.env("resolved", r.env(graphql, transport, extra))
}

func (r restSite) calls() []string { return lines(readIf(r.log)) }

func (r restSite) mutations() []map[string]any { return mutations(r.t, r.muts) }

// restCalls は REST の代わりの道（/ccr/）を呼んだ要求。
func (r restSite) restCalls() []string {
	var out []string
	for _, c := range r.calls() {
		if strings.Contains(c, "/ccr/") {
			out = append(out, c)
		}
	}
	return out
}

func TestWhenGraphQLWorksRestIsNotCalled(t *testing.T) {
	t.Parallel()
	r := newRestSite(t)
	done := r.fetch("", "curl", nil, "fetch")
	if done.Code != 0 {
		t.Fatalf("code = %d\n%s", done.Code, done.Stderr)
	}
	calls := r.calls()
	if !contains(calls, "POST /graphql") {
		t.Errorf("GraphQL を呼んでいない: %v", calls)
	}
	for _, c := range calls {
		if strings.Contains(c, "/ccr/") || (strings.Contains(c, "/comments") && !strings.Contains(c, "issues")) {
			t.Errorf("REST を呼んだ: %s", c)
		}
	}
}

// 返信が重なったスレッドの last_* は最後のコメント。diff_hunk を含むコメントが多い MR でも、引数の上限（128KB）に当たらない
// （別のスレッドのコメントを多く混ぜて渡す）。
func TestWhenGraphQLIsBlockedRestBuildsTheSameShape(t *testing.T) {
	t.Parallel()
	for _, transport := range []string{"curl", "gh"} {
		t.Run(transport, func(t *testing.T) {
			t.Parallel()
			r := newRestSite(t)
			big := append([]map[string]any{}, restComments...)
			for i := range 120 {
				big = append(big, restComment(1000+i, "reviewer", "2026-10-02T00:00:00Z", strings.Repeat("x", 500), map[string]any{"diff_hunk": strings.Repeat("y", 2000)}))
			}
			r.setRest(big)
			done := r.fetch("blocked", transport, nil, "fetch")
			if done.Code != 0 {
				t.Fatalf("code = %d\n%s", done.Code, done.Stderr)
			}
			got := decode(t, done.Stdout)
			if !jsonEqual(t, mustJSON(got["threads"]), mustJSON(restExpected)) {
				t.Errorf("threads = %s", mustJSON(got["threads"]))
			}
			if got["mr"].(map[string]any)["number"] != json.Number("42") {
				t.Errorf("mr = %v", got["mr"])
			}
			// GraphQL を先に試し、そのあとで REST
			calls := r.calls()
			first, second := indexOf(calls, "POST /graphql"), indexOf(calls, "GET /repos/acme/widgets/pulls/42/ccr/review_threads")
			if first < 0 || second < first || !contains(calls, "GET /repos/acme/widgets/pulls/42/comments?per_page=100&page=1") {
				t.Errorf("要求の順 = %v", calls)
			}
			// 失敗の文面（GraphQL の 403）は出さない
			if strings.Contains(done.Stderr, "が失敗した") {
				t.Errorf("失敗の文面が出た: %s", done.Stderr)
			}
		})
	}
}

func indexOf(list []string, want string) int {
	for i, s := range list {
		if s == want {
			return i
		}
	}
	return -1
}

// 偽の GitHub は配列でない答えを空の配列に直してしまうので、この試験は REST の代わりの道（ccr/review_threads）だけ、
// PATH の先頭の curl が配列でない答えを返す。
func TestAResponseThatIsNotAnArrayStopsWithTheHint(t *testing.T) {
	t.Parallel()
	r := newRestSite(t)
	catcher := filepath.Join(r.tmp, "bin-object")
	writeExec(t, filepath.Join(catcher, "curl"), "#!/bin/sh\n"+
		"case \" $* \" in *ccr/review_threads*) printf '%s' '{\"threads\": []}'; exit 0 ;; esac\n"+
		"exec "+shq(filepath.Join(r.bin, "curl"))+" \"$@\"\n")
	done := r.fetch("blocked", "curl", map[string]string{"PATH": pathWith(catcher, r.bin)}, "fetch")
	if done.Code == 0 || !strings.Contains(done.Stderr, "配列でない") || !strings.Contains(done.Stderr, "REST の代わりの道") {
		t.Errorf("code = %d\n%s%s", done.Code, done.Stdout, done.Stderr)
	}
}

// confirm は組んだ JSON を実行ファイルに渡す。解決済みの読み方は上の形の試験（201）が見る。未解決はレビュアーの 101 の 1 件。
func TestConfirmHandsTheThreadsToTheExecutable(t *testing.T) {
	t.Parallel()
	r := newRestSite(t)
	done := r.fetch("blocked", "curl", nil, "confirm", "--phase", "1")
	if done.Code != 0 {
		t.Fatalf("code = %d\n%s%s", done.Code, done.Stdout, done.Stderr)
	}
	// 数え方（機構自身の投稿を除くなど）は go test（host の Unresolved）が見る。ここのスレッドは全部レビュアーのもの
	var copied struct{ Threads []struct{ ID, Resolved any } }
	var unresolved []any
	if err := json.Unmarshal([]byte(read(t, r.stub.Result)), &copied); err != nil {
		t.Fatal(err)
	}
	for _, th := range copied.Threads {
		if th.Resolved == false {
			unresolved = append(unresolved, th.ID)
		}
	}
	if fmt.Sprint(unresolved) != "[101]" {
		t.Errorf("未解決 = %v, want [101]", unresolved)
	}
}

// 認証の失敗（401）と、別の理由の 403（gh の Resource not accessible）は REST に落とさず失敗する。
func TestAnAuthFailureOrAnotherForbiddenDoesNotFallBackToRest(t *testing.T) {
	t.Parallel()
	cases := []struct{ graphql, transport, stderr string }{
		{"unauthorized", "curl", "が失敗した"},
		{"unauthorized", "gh", "が失敗した"},
		{"forbidden", "gh", "Resource not accessible"},
	}
	for _, c := range cases {
		t.Run(c.graphql+"/"+c.transport, func(t *testing.T) {
			t.Parallel()
			r := newRestSite(t)
			done := r.fetch(c.graphql, c.transport, nil, "fetch")
			if done.Code == 0 || !strings.Contains(done.Stderr, c.stderr) || len(r.restCalls()) != 0 {
				t.Errorf("code = %d, REST の呼び出し = %v\n%s", done.Code, r.restCalls(), done.Stderr)
			}
		})
	}
}

// GraphQL は 403、/ccr/ は 404。案内に、拒否される手順（JSON を作らせる）を書かず、次の一手（止まってユーザに頼む）を言い、
// 元の GraphQL の返事を見せる（curl の経路では本文が捨てられ、番号だけが残る）。
func TestWhenRestFailsTooTheHintDoesNotAskForAJSON(t *testing.T) {
	t.Parallel()
	for transport, reply := range map[string]string{"curl": "403", "gh": "GraphQL is not available"} {
		t.Run(transport, func(t *testing.T) {
			t.Parallel()
			r := newRestSite(t)
			done := r.fetch("blocked", transport, map[string]string{"FAKE_GITHUB_NO_CCR": "1"}, "fetch")
			if done.Code == 0 {
				t.Fatalf("成功した\n%s", done.Stdout)
			}
			for _, want := range []string{"REST の代わりの道", "が失敗した", "GraphQL の返事:", reply, "ユーザに", "止まって"} {
				if !strings.Contains(done.Stderr, want) {
					t.Errorf("案内に %q が無い:\n%s", want, done.Stderr)
				}
			}
			for _, word := range []string{"--result", "JSON", "MCP"} {
				if strings.Contains(done.Stderr, word) {
					t.Errorf("案内に %q がある:\n%s", word, done.Stderr)
				}
			}
		})
	}
}

func TestUndraftWhenBlockedCallsReadyForReviewAndReturnsFalse(t *testing.T) {
	t.Parallel()
	for _, transport := range []string{"curl", "gh"} {
		t.Run(transport, func(t *testing.T) {
			t.Parallel()
			r := newRestSite(t)
			done := r.driver("undraft 42", "blocked", transport, nil)
			var rest []string
			for _, m := range r.mutations() {
				rest = append(rest, fmt.Sprint(m["rest"]))
			}
			// タイトルの Draft: 除去の PATCH は今のまま
			if done.Code != 0 || strings.TrimSpace(done.Stdout) != "false" || !contains(rest, "POST pulls/42/ccr/ready_for_review") ||
				!contains(r.calls(), "PATCH /repos/acme/widgets/pulls/42") {
				t.Errorf("code = %d, stdout = %q, 書き込み = %v\n%s", done.Code, done.Stdout, rest, done.Stderr)
			}
		})
	}
}

func TestUndraftWhenGraphQLWorksDoesNotCallRest(t *testing.T) {
	t.Parallel()
	r := newRestSite(t)
	done := r.driver("undraft 42", "", "curl", nil)
	ready := false
	for _, m := range r.mutations() {
		ready = ready || strings.Contains(fmt.Sprint(m["query"]), "markPullRequestReadyForReview")
	}
	if done.Code != 0 || strings.TrimSpace(done.Stdout) != "false" || len(r.restCalls()) != 0 || !ready {
		t.Errorf("code = %d, stdout = %q, REST = %v, GraphQL で外した = %v\n%s", done.Code, done.Stdout, r.restCalls(), ready, done.Stderr)
	}
}

func TestUndraftWhenRestFailsTooSaysSoWithoutAskingForAJSON(t *testing.T) {
	t.Parallel()
	for _, transport := range []string{"curl", "gh"} {
		t.Run(transport, func(t *testing.T) {
			t.Parallel()
			r := newRestSite(t)
			done := r.driver("undraft 42", "blocked", transport, map[string]string{"FAKE_GITHUB_NO_CCR": "1"})
			if done.Code == 0 || strings.TrimSpace(done.Stdout) != "" || !strings.Contains(done.Stderr, "REST の代わりの道") || !strings.Contains(done.Stderr, "Draft の解除") {
				t.Errorf("code = %d, stdout = %q\n%s", done.Code, done.Stdout, done.Stderr)
			}
			for _, word := range []string{"--result", "JSON", "MCP"} {
				if strings.Contains(done.Stderr, word) {
					t.Errorf("案内に %q がある:\n%s", word, done.Stderr)
				}
			}
		})
	}
}

func TestUndraftAuthFailureFailsWithoutRest(t *testing.T) {
	t.Parallel()
	r := newRestSite(t)
	done := r.driver("undraft 42", "unauthorized", "curl", nil)
	if done.Code == 0 || len(r.restCalls()) != 0 {
		t.Errorf("code = %d, REST = %v\n%s", done.Code, r.restCalls(), done.Stderr)
	}
}

// accept は ids を全部受け入れる計画で、ユーザ専用の decide を起こす。
func (r restSite) accept(graphql string, ids ...string) run.Result {
	r.t.Helper()
	plan := keepPlan(r.t, r.tmp, r.state(), "github", prURL, 42, ids...)
	return r.sh(userDecideSh, run.Opts{Env: r.env2(graphql, "curl", map[string]string{"STUB_PLAN": plan})}, "1", "--choices", "{}", "--digest", "d")
}

func TestDecideUsesRestRepliesAndResolveForCommentIDs(t *testing.T) {
	t.Parallel()
	r := newRestSite(t)
	done := r.accept("blocked", "101")
	sent := r.mutations()
	if done.Code != 0 || len(sent) != 2 {
		t.Fatalf("code = %d, 書き込み = %v\n%s%s", done.Code, sent, done.Stdout, done.Stderr)
	}
	body, _ := sent[0]["body"].(map[string]any)
	if sent[0]["rest"] != "POST pulls/42/comments/101/replies" || sent[1]["rest"] != "POST pulls/42/ccr/comments/101/resolve" ||
		!strings.HasPrefix(fmt.Sprint(body["body"]), "<!-- ccnavi:accept -->") {
		t.Errorf("書き込み = %v", sent)
	}
	// GraphQL の mutation は打たない
	for _, m := range sent {
		if _, ok := m["query"]; ok {
			t.Errorf("GraphQL の mutation を打った: %v", m)
		}
	}
}

// GraphQL が通る環境で取った node id は、GraphQL の mutation で書く（REST は呼ばない）。
func TestDecideKeepsUsingGraphQLForNodeIDs(t *testing.T) {
	t.Parallel()
	r := newRestSite(t)
	done := r.accept("", nodeA)
	sent := r.mutations()
	if done.Code != 0 || len(sent) != 2 {
		t.Fatalf("code = %d, 書き込み = %v\n%s%s", done.Code, sent, done.Stdout, done.Stderr)
	}
	for _, m := range sent {
		if _, ok := m["query"]; !ok {
			t.Errorf("GraphQL でない書き込み: %v", m)
		}
	}
	for _, c := range r.calls() {
		if strings.Contains(c, "/ccr/") || strings.Contains(c, "/replies") {
			t.Errorf("REST を呼んだ: %s", c)
		}
	}
}

// 403 のときも、node id を REST の経路に使わない（id の形が道を決める。混ぜない）。
func TestDecideWithANodeIDStaysOnGraphQLEvenWhenBlocked(t *testing.T) {
	t.Parallel()
	r := newRestSite(t)
	done := r.accept("blocked", nodeA)
	for _, m := range r.mutations() {
		if _, ok := m["rest"]; ok {
			t.Errorf("REST の書き込み: %v", m)
		}
	}
	if done.Code != 1 {
		t.Errorf("code = %d\n%s%s", done.Code, done.Stdout, done.Stderr)
	}
}
