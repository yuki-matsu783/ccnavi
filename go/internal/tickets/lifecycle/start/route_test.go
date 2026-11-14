package start

import (
	"encoding/json"
	"strings"
	"testing"

	"ccnavi/internal/tickets/testkit"
)

// routeRepo は振り分けのための手元のリポジトリ。origin/main と、統合先と同じ位置のブランチ（stale-1）、初期コミットだけの
// ブランチ（feature-2-y）、ほかのコミットを積んだブランチ（feature-3-z）、ワークツリーのあるブランチ（feature-4-w）を持つ。
func routeRepo(t *testing.T) string {
	t.Helper()
	root := testkit.Workspace(t, "feature-4-w")
	testkit.Git(t, root, "update-ref", "refs/remotes/origin/main", "HEAD")
	testkit.Git(t, root, "branch", "stale-1", "main")
	for name, subject := range map[string]string{"feature-2-y": InitSubject("2"), "feature-3-z": "work"} {
		testkit.Git(t, root, "checkout", "-q", "-b", name, "main")
		testkit.Commit(t, root, subject)
	}
	testkit.Git(t, root, "checkout", "-q", "main")
	return root
}

// candidates は ccnavi-branches.sh --json の形の候補。
func candidates(checked bool, cands ...map[string]any) []byte {
	if cands == nil {
		cands = []map[string]any{}
	}
	b, _ := json.Marshal(map[string]any{"host": map[string]any{"checked": checked, "reason": "届かない"}, "candidates": cands})
	return b
}

func cand(branch string, local bool, mrs ...map[string]any) map[string]any {
	if mrs == nil {
		mrs = []map[string]any{}
	}
	return map[string]any{"branch": branch, "local": local, "origin": false, "mrs": mrs}
}

func openMR(n int) map[string]any {
	return map[string]any{"number": n, "url": "https://example/mr", "title": "T", "state": "open", "base": "main", "fork": false}
}

// answer は Route の答えを 項目 → 値 にする。
func answer(out string) map[string]string {
	m := map[string]string{}
	for l := range strings.SplitSeq(strings.TrimSuffix(out, "\n"), "\n") {
		k, v, _ := strings.Cut(l, " ")
		m[k] = v
	}
	return m
}

func TestRoute(t *testing.T) {
	root := routeRepo(t)
	issue := func(n string) RouteArgs {
		return RouteArgs{What: "issue", Number: n, HasIssue: true, Integration: "main"}
	}
	mcp := RouteArgs{What: "issue", Number: "1", HasIssue: true, HasMR: true, MCPChecked: true, Integration: "main"}
	withMR := func(mod func(map[string]any)) map[string]any { m := openMR(5); mod(m); return m }
	for _, c := range []struct {
		name string
		cand []byte
		args RouteArgs
		want map[string]string
	}{
		{"ホストを見られず 3 つそろえば MCP で続ける", candidates(false), func() RouteArgs { a := mcp; a.Branch = "feature-3-z"; return a }(),
			map[string]string{"route": "mcp", "branch": "feature-3-z", "problem": ""}},
		{"MCP の --branch が保護されたブランチ", candidates(false), func() RouteArgs { a := mcp; a.Branch = "main"; return a }(),
			map[string]string{"route": "mcp", "problem": "protected"}},
		{"MCP の --branch が手元にも origin にも無い", candidates(false), func() RouteArgs { a := mcp; a.Branch = "feature-9-x"; return a }(),
			map[string]string{"route": "mcp", "problem": "branch-missing"}},
		{"候補が複数", candidates(true, cand("a-1", true), cand("b-1", true)), issue("1"), map[string]string{"route": "multi", "total": "2"}},
		{"ホストを見られず候補がある", candidates(false, cand("feature-3-z", true)), issue("1"),
			map[string]string{"route": "unchecked", "branch": "feature-3-z", "checked": "false", "reason": "届かない"}},
		{"ホストを見られず --mr で来た", candidates(false), RouteArgs{What: "mr", Number: "5", HasMR: true, Integration: "main"},
			map[string]string{"route": "unchecked", "branch": ""}},
		{"--mr で候補が 0 件", candidates(true), RouteArgs{What: "mr", Number: "5", HasMR: true, Integration: "main"}, map[string]string{"route": "mr-not-found"}},
		{"候補が 0 件でホストを見られない", candidates(false), issue("1"), map[string]string{"route": "none-unchecked"}},
		{"候補が 0 件で --branch がある", candidates(true), func() RouteArgs { a := issue("1"); a.Branch = "x-1"; return a }(), map[string]string{"route": "none-branch"}},
		{"候補が 0 件なら新しく作る", candidates(true), issue("1"), map[string]string{"route": "create"}},
		{"候補のブランチ名が保護されている", candidates(true, cand("main", true)), issue("1"), map[string]string{"route": "bad-candidate"}},
		{"提案だけで実体が無い", candidates(true, cand("feature-1-x", false)), issue("1"), map[string]string{"route": "proposal-only"}},
		{"--branch が候補と違う", candidates(true, cand("feature-3-z", true)), func() RouteArgs { a := issue("3"); a.Branch = "other-3"; return a }(),
			map[string]string{"route": "branch-mismatch"}},
		{"MR があるのにワークツリーが無い", candidates(true, cand("feature-3-z", true, openMR(5))), issue("3"),
			map[string]string{"route": "mr-exists", "mr": "5", "url": "https://example/mr", "title": "T", "mrs": "1"}},
		{"ワークツリーがあれば MR を採用する", candidates(true, cand("feature-4-w", true, openMR(5))), issue("4"),
			map[string]string{"route": "adopt", "problem": "", "mr-state": "open"}},
		{"--mr で来ればワークツリーが無くても採用する", candidates(true, cand("feature-3-z", true, openMR(5))), RouteArgs{What: "mr", Number: "5", HasMR: true, Integration: "main"},
			map[string]string{"route": "adopt", "problem": ""}},
		{"閉じた MR は採用しない", candidates(true, cand("feature-4-w", true, withMR(func(m map[string]any) { m["state"] = "closed" }))), issue("4"),
			map[string]string{"route": "adopt", "problem": "not-open closed"}},
		{"フォークの MR は採用しない", candidates(true, cand("feature-4-w", true, withMR(func(m map[string]any) { m["fork"] = true }))), issue("4"),
			map[string]string{"route": "adopt", "problem": "fork"}},
		{"マージ先が統合先でない MR は採用しない", candidates(true, cand("feature-4-w", true, withMR(func(m map[string]any) { m["base"] = "develop" }))), issue("4"),
			map[string]string{"route": "adopt", "problem": "target develop", "mr-base": "develop"}},
		{"閉じていてフォークなら状態を先に言う", candidates(true, cand("feature-4-w", true, withMR(func(m map[string]any) { m["state"] = "merged"; m["fork"] = true }))), issue("4"),
			map[string]string{"problem": "not-open merged"}},
		{"採用する MR のブランチが統合先そのもの", candidates(true, cand("feature-4-w", true, withMR(func(m map[string]any) { m["base"] = "feature-4-w" }))),
			func() RouteArgs { a := issue("4"); a.Integration = "feature-4-w"; return a }(), map[string]string{"route": "adopt", "problem": "protected"}},
		{"統合先より進んでいないブランチは古い", candidates(true, cand("stale-1", true)), issue("1"), map[string]string{"route": "nomr", "after": "stale"}},
		{"初期コミットだけなら作りかけ", candidates(true, cand("feature-2-y", true)), issue("2"), map[string]string{"route": "nomr", "after": "resume"}},
		{"作りかけでも --mr で来たら続けない", candidates(true, cand("feature-2-y", true)), RouteArgs{What: "mr", Number: "2", HasMR: true, Integration: "main"},
			map[string]string{"after": "no-mr"}},
		{"初期コミットの番号が違えば作りかけでない", candidates(true, cand("feature-2-y", true)), issue("9"), map[string]string{"after": "no-mr"}},
		{"ほかのコミットがあれば MR の無い候補", candidates(true, cand("feature-3-z", true)), issue("3"), map[string]string{"after": "no-mr"}},
		{"名前に / を含めば作りかけと見ない", candidates(true, cand("topic/x", true)), issue("1"), map[string]string{"after": "no-mr"}},
	} {
		t.Run(c.name, func(t *testing.T) {
			out, code := Route(root, c.cand, c.args)
			if code != ok {
				t.Fatalf("終了コード %d\n%s", code, out)
			}
			got := answer(out)
			for k, v := range c.want {
				if got[k] != v {
					t.Errorf("%s: %q（期待 %q）\n%s", k, got[k], v, out)
				}
			}
		})
	}
}

func TestRouteRefusesAnUnreadableCandidateList(t *testing.T) {
	for _, data := range []string{"not json", `{"host": 1, "candidates": []}`, `{"host": {}, "candidates": {}}`} {
		if out, code := Route(t.TempDir(), []byte(data), RouteArgs{What: "issue"}); code != unreadable || out != "" {
			t.Errorf("%s: %q（%d）", data, out, code)
		}
	}
}
