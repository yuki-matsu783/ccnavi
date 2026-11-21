package fakegitlab

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/exe"
	"ccnavi/internal/harness/fakegitlab"
	"ccnavi/internal/harness/run"
)

// reviewSh は fetch が読む sh。共通部（base.CommonSh）と一緒に、使い捨ての木へコピーする。
var reviewSh = []string{"ccnavi-review.sh", "ccnavi-user-decide.sh", "ccnavi-user-lib-review.sh"}

// exeStub は実行ファイルの代わりに置く sh。版の問いに答え、ほかは何もせず 0 で終わる。
const exeStub = `#!/bin/sh
case " $* " in
*" --version --json "*)
  printf '{"compat": COMPAT}\n'
  exit 0 ;;
*" --version "*) printf 'compat: COMPAT\n'; exit 0 ;;
*" write-and-push family "*) printf 'write-and-push 1\nfamily i0001\ntarget no\n'; exit 0 ;;
*" ops "* | *" git integration "* | *" path under "* | *" host "*) exec 'EXE' "$@" ;;
*" accept plan "* | *" accept threads "* | *" accept decided "*) exec 'EXE' "$@" ;;
esac
exit 0
`

func seeded() fakegitlab.State {
	note := map[string]any{
		"id": 101, "body": "ここを直してください", "resolvable": true, "resolved": false, "created_at": "2026-10-25T00:00:00Z",
		"author": map[string]any{"id": 2, "username": "reviewer"}, "position": map[string]any{"new_path": "a.txt", "new_line": 3},
	}
	mr := map[string]any{
		"iid": 1, "state": "opened", "source_branch": "i0001", "target_branch": "main", "title": "i0001",
		"web_url":     "http://127.0.0.1/" + project + "/-/merge_requests/1",
		"discussions": []any{map[string]any{"id": "d101", "individual_note": false, "notes": []any{note}}},
		"reviewers":   []any{}, "notes": []any{}, "created_at": "2026-10-25T00:00:00Z",
	}
	return state(map[string]any{"mrs": []any{mr}})
}

func git(t *testing.T, args ...string) {
	t.Helper()
	cmd := exec.Command("git", args...)
	cmd.Env = run.Environment(run.Opts{})
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("git %v: %v\n%s", args, err, out)
	}
}

// 実コードの ccnavi-review.sh fetch が、glab を通らず curl で模擬の MR とスレッドを読む。
func TestReviewShFetchReadsTheFakeThroughCurl(t *testing.T) {
	t.Parallel()
	for _, tool := range []string{"sh", "git", "jq", "curl"} {
		if _, err := exec.LookPath(tool); err != nil {
			t.Skipf("%s が無い", tool)
		}
	}
	tmp := base.TempDir(t)
	srv := fakegitlab.Start(t, seeded())
	ws := filepath.Join(tmp, "ws")
	scripts := filepath.Join(ws, ".ccnavi", "scripts")
	if err := os.MkdirAll(scripts, 0o755); err != nil {
		t.Fatal(err)
	}
	src := base.ScriptsDir()
	for _, name := range append(append([]string{}, reviewSh...), base.CommonSh(t, src)...) {
		data, err := os.ReadFile(filepath.Join(src, name))
		if err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(scripts, name), data, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	idents := []string{"-C", ws, "-c", "user.name=t", "-c", "user.email=t@example.invalid"}
	git(t, "init", "-q", "-b", "i0001", ws)
	git(t, append(idents, "commit", "-q", "--allow-empty", "-m", "seed")...)
	git(t, append(idents, "remote", "add", "origin", fmt.Sprintf("%s/%s.git", srv.URL, project))...)
	// 前に置く glab は必ず失敗させる。成功すると glab の経路に入ってしまうため。
	bin := filepath.Join(tmp, "bin")
	if err := os.MkdirAll(bin, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(bin, "glab"), []byte("#!/bin/sh\nexit 1\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	stub := strings.NewReplacer("COMPAT", exe.Compat(t), "EXE", exe.ShPath(t)).Replace(exeStub)
	stubPath := filepath.Join(tmp, "exe", "ccnavi")
	if err := os.MkdirAll(filepath.Dir(stubPath), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(stubPath, []byte(stub), 0o755); err != nil {
		t.Fatal(err)
	}

	r := run.Sh(t, filepath.Join(scripts, "ccnavi-review.sh"), []string{"fetch"}, run.Opts{
		Dir: ws,
		Env: map[string]string{
			"GITLAB_TOKEN":      srv.Token,
			"PATH":              bin + string(os.PathListSeparator) + os.Getenv("PATH"),
			"CCNAVI_ENTRY_PATH": stubPath,
		},
	})
	if r.Code != 0 {
		t.Fatalf("code = %d\n%s%s", r.Code, r.Stdout, r.Stderr)
	}
	var got struct {
		Host string `json:"host"`
		MR   struct {
			Number int `json:"number"`
		} `json:"mr"`
		Threads []struct {
			Resolved bool   `json:"resolved"`
			Body     string `json:"body"`
		} `json:"threads"`
	}
	if err := json.Unmarshal([]byte(r.Stdout), &got); err != nil {
		t.Fatalf("JSON でない: %v\n%s", err, r.Stdout)
	}
	if got.Host != "gitlab" || got.MR.Number != 1 || len(got.Threads) != 1 || got.Threads[0].Resolved || !strings.Contains(got.Threads[0].Body, "ここを直してください") {
		t.Errorf("fetch = %+v", got)
	}
	// curl の経路に入ったことは、模擬が受けた要求で分かる
	var project, discussions bool
	for _, line := range srv.Requests() {
		project = project || line == "GET /api/v4/projects/ns%2Fproj"
		discussions = discussions || strings.Contains(line, "/merge_requests/1/discussions")
	}
	if !project || !discussions {
		t.Errorf("requests = %v", srv.Requests())
	}
}
