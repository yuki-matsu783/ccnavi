package review

import (
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/exe"
	"ccnavi/internal/harness/run"
)

// origin の URL の読み方（sh の `origin`）。実行ファイル側の読み方は go/internal/tickets/model/host が見る。
//
// URL にトークンを埋めた形（`https://oauth2:<token>@host/g/p.git`）は普通にある。ホストにユーザ情報が入り込むと API の URL が
// 不正になり、`origin` の出力にトークンが漏れる。実物の GitLab で実際に起きた穴なので、sh と実行ファイルの両方で固定する。

// originRun は新しい木に origin を足して、ccnavi-review.sh の sub を起こす。
func originRun(t *testing.T, url, sub string) (run.Result, *site) {
	t.Helper()
	s := newMarkerSite(t, "main")
	s.git("remote", "add", "origin", url)
	// 資格情報の伏せ方は実行ファイル（`host mask`）に聞く
	env := map[string]string{"CCNAVI_ENTRY_PATH": exe.ShPath(t), "GITLAB_TOKEN": "x", "GITHUB_TOKEN": "x"}
	return s.real(reviewSh, run.Opts{Env: env}, sub), s
}

func TestOriginDropsAndHidesUserinfo(t *testing.T) {
	t.Parallel()
	// 閉じたポート。glab の疎通の試みがすぐ失敗して curl に切り替わる
	r, _ := originRun(t, "http://oauth2:glpat-secret@127.0.0.1:9/root/p.git", "origin")
	if r.Code != 0 {
		t.Fatalf("code = %d\n%s%s", r.Code, r.Stdout, r.Stderr)
	}
	for _, want := range []string{"host=127.0.0.1:9\n", "path=root/p\n", "api_base=http://127.0.0.1:9/api/v4\n", "origin=http://<伏せた>@127.0.0.1:9/root/p.git"} {
		if !strings.Contains(r.Stdout, want) {
			t.Errorf("stdout に %q が無い:\n%s", want, r.Stdout)
		}
	}
	if strings.Contains(r.Stdout+r.Stderr, "glpat-secret") {
		t.Errorf("トークンが漏れた:\n%s%s", r.Stdout, r.Stderr)
	}
}

// origin の URL から、ホスト・ポート・scheme を欠かさずに API の URL を組むこと。host を `[^/:]+` で切るとポートが欠け、
// 欠けたポートがプロジェクトのパスの先頭に入り込む。scheme を https に決め打ちすると、平文で立てた GitLab
// （`http://localhost:8929`）に届かない。
func TestOriginKeepsThePortAndScheme(t *testing.T) {
	t.Parallel()
	cases := []struct{ url, kind, scheme, host, path string }{
		{"http://localhost:8929/demo/greeter.git", "gitlab", "http", "localhost:8929", "demo/greeter"},
		{"https://gitlab.example.com:8443/g/p.git", "gitlab", "https", "gitlab.example.com:8443", "g/p"},
		{"git@gitlab.example.com:g/p.git", "gitlab", "https", "gitlab.example.com", "g/p"},
		{"https://github.com/o/r.git", "github", "https", "github.com", "o/r"},
	}
	for _, c := range cases {
		t.Run(c.url, func(t *testing.T) {
			t.Parallel()
			r, _ := originRun(t, c.url, "origin")
			if r.Code != 0 {
				t.Fatalf("code = %d\n%s%s", r.Code, r.Stdout, r.Stderr)
			}
			got := map[string]string{}
			for _, line := range lines(r.Stdout) {
				if k, v, ok := strings.Cut(line, "="); ok {
					got[k] = v
				}
			}
			api := c.scheme + "://" + c.host + "/api/v4"
			if c.kind == "github" {
				api = "https://api.github.com"
			}
			want := map[string]string{"kind": c.kind, "scheme": c.scheme, "host": c.host, "path": c.path, "api_base": api}
			for k, v := range want {
				if got[k] != v {
					t.Errorf("%s = %q, want %q", k, got[k], v)
				}
			}
		})
	}
}

// sh の前半（場所と道具の解決）が Windows でも通ること。origin が読めなければ止まる。
func TestAnUnreadableOriginStops(t *testing.T) {
	t.Parallel()
	s := newMarkerSite(t, "main")
	bare := filepath.Join(s.ws, "origin.git")
	run.Git(t, s.ws, "init", "-q", "--bare", bare)
	s.git("remote", "add", "origin", bare)
	env := map[string]string{"CCNAVI_ENTRY_PATH": exe.ShPath(t), "GITLAB_TOKEN": "x", "GITHUB_TOKEN": "x"}
	r := s.real(reviewSh, run.Opts{Env: env}, "fetch")
	if r.Code != 1 || !strings.Contains(r.Stderr, "origin の URL を読めない") {
		t.Errorf("code = %d, stderr = %q", r.Code, r.Stderr)
	}
}
