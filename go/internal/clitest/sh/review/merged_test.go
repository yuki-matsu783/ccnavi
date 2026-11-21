package review

import (
	"fmt"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/exe"
	"ccnavi/internal/harness/run"
)

// ccnavi-review.sh merged の約束。ccnavi-sync.sh は答えが `none` のときだけ「マージされていない」と読み、
// マージ済みなら `merged <番号> <先頭の sha> <元ブランチ>` を読む。API の失敗・読めない答えは `unknown`（終了コード 3）、
// 道具やトークンが無ければ `none` を出さずに止まる。ホストへの道具（curl）は PATH の先頭に置いたスタブに差し替える。
//
// 応答の読み（マージ済みか・GitLab はこのプロジェクトから出たものだけ・答えの行）は実行ファイル（`ccnavi host merged`）にあり、
// Go の hostreply が表で見る。ここで見るのは sh の仕事（番号の有無で聞く道を選ぶ・GitLab のプロジェクトの id と全ページ・
// 失敗を unknown にする・トークン）。

// mergedRun は curl のスタブ（stubCurl の sh）を PATH の先頭に置いて merged を起こす。token が空ならトークンを渡さない。
func mergedRun(t *testing.T, origin, tokenName, curl, token string, args ...string) run.Result {
	t.Helper()
	s := newSite(t, origin, reviewSh)
	bin := filepath.Join(s.tmp, "bin")
	writeExec(t, filepath.Join(bin, "curl"), curl)
	env := map[string]string{"CCNAVI_ENTRY_PATH": exe.ShPath(t), "PATH": pathWith(bin)}
	if token != "" {
		env[tokenName] = token
	}
	return s.sh(reviewSh, run.Opts{Env: env}, append([]string{"merged"}, args...)...)
}

// curlSays は、常に body を出して code で終わる curl のスタブ。
func curlSays(body string, code int) string {
	return fmt.Sprintf("#!/bin/sh\nprintf '%%s' '%s'\nexit %d\n", body, code)
}

func TestMergedOnGitHub(t *testing.T) {
	t.Parallel()
	const origin = "https://github.com/o/r.git"
	cases := []struct {
		name  string
		curl  string
		token string
		args  []string
		code  int
		out   string // 標準出力（前後の空白を除く）。code が -1 のときは使わない
	}{
		{"マージ済みの MR を先頭の sha つきで言う",
			curlSays(`[{"number": 7, "merged_at": null}, {"number": 9, "merged_at": "x", "head": {"sha": "abc123"}}]`, 0), "t", nil, 0, "merged 9 abc123"},
		{"番号を渡せば、その MR 1 本を聞いて元ブランチも言う",
			curlSays(`{"number": 9, "merged_at": "x", "head": {"sha": "abc123", "ref": "feature/12-x"}}`, 0), "t", []string{"9"}, 0, "merged 9 abc123 feature/12-x"},
		{"番号が数字でなければ断る", curlSays("[]", 0), "t", []string{"9; ls"}, 2, ""},
		{"マージ済みが無ければ none", curlSays("[]", 0), "t", nil, 0, "none"},
		{"API の失敗は unknown", curlSays("", 22), "t", nil, 3, "unknown"},
		{"読めない答えは unknown", curlSays("not json", 0), "t", nil, 3, "unknown"},
		{"トークンが無ければ none を言わずに止まる", curlSays("[]", 0), "", nil, -1, ""},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			r := mergedRun(t, origin, "GITHUB_TOKEN", c.curl, c.token, c.args...)
			got := strings.TrimSpace(r.Stdout)
			switch {
			case c.code == -1: // 0 以外で止まり、none を出さない（トークンが無い）
				if r.Code == 0 || strings.Contains(r.Stdout, "none") {
					t.Errorf("code = %d, stdout = %q", r.Code, r.Stdout)
				}
			case r.Code != c.code || got != c.out:
				t.Errorf("code = %d, stdout = %q, stderr = %q; want %d, %q", r.Code, got, r.Stderr, c.code, c.out)
			}
		})
	}
}

// GitLab の枝。プロジェクトの id を引いて渡し、一覧は全ページを読む。id が引けなければ unknown。

// gitlabCurl は URL ごとに答える curl のスタブ。pages があれば MR の一覧をページの番号で引く。
func gitlabCurl(mrs, project string, projectCode int, pages map[int]string, one string) string {
	var cases strings.Builder
	if one != "" {
		fmt.Fprintf(&cases, "*/merge_requests/9*) printf '%%s' '%s' ;;\n", one)
	}
	if len(pages) > 0 {
		for n := 1; n <= len(pages); n++ {
			fmt.Fprintf(&cases, "*merge_requests*page=%d) printf '%%s' '%s' ;;\n", n, pages[n])
		}
	} else {
		fmt.Fprintf(&cases, "*merge_requests*) printf '%%s' '%s' ;;\n", mrs)
	}
	return "#!/bin/sh\nfor a in \"$@\"; do url=\"$a\"; done\ncase \"$url\" in\n" + cases.String() +
		fmt.Sprintf("*/projects/o%%2Fr) printf '%%s' '%s'; exit %d ;;\n*) exit 22 ;;\nesac\n", project, projectCode)
}

func TestMergedOnGitLab(t *testing.T) {
	t.Parallel()
	const origin = "https://gitlab.com/o/r.git"
	forks := "[" + strings.TrimSuffix(strings.Repeat(`{"iid": 1, "source_project_id": 99}, `, 100), ", ") + "]"
	cases := []struct {
		name string
		curl string
		args []string
		code int
		out  string
	}{
		{"番号を渡せば、元ブランチを言う",
			gitlabCurl("[]", `{"id": 42}`, 0, nil,
				`{"iid": 9, "state": "merged", "source_project_id": 42, "sha": "abc123", "source_branch": "feature/12-x"}`),
			[]string{"9"}, 0, "merged 9 abc123 feature/12-x"},
		// 一覧の道の代表。1 ページ目がフォークで埋まっても、2 ページ目の実物を見落とさない
		{"一覧は全ページを読む",
			gitlabCurl("", `{"id": 42}`, 0, map[int]string{1: forks, 2: `[{"iid": 300, "source_project_id": 42, "sha": "def456"}]`}, ""),
			nil, 0, "merged 300 def456"},
		{"プロジェクトの id が数でなければ unknown",
			gitlabCurl(`[{"iid": 9, "source_project_id": 42}]`, `{"id": "x"}`, 0, nil, ""), nil, 3, "unknown"},
		{"プロジェクトの id が無ければ unknown",
			gitlabCurl(`[{"iid": 9, "source_project_id": 42}]`, `{}`, 0, nil, ""), nil, 3, "unknown"},
		{"プロジェクトを引けなければ unknown",
			gitlabCurl(`[{"iid": 9, "source_project_id": 42}]`, ``, 22, nil, ""), nil, 3, "unknown"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			r := mergedRun(t, origin, "GITLAB_TOKEN", c.curl, "t", c.args...)
			if got := strings.TrimSpace(r.Stdout); r.Code != c.code || got != c.out {
				t.Errorf("code = %d, stdout = %q, stderr = %q; want %d, %q", r.Code, got, r.Stderr, c.code, c.out)
			}
		})
	}
}
