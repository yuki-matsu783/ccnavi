package post

// SessionStart の学びの通知の、hook での出し方。見るのは、通知がユーザにだけ見える `systemMessage` であること、2 回目は言わないこと、
// 統合先の手元の参照のコミットから数えること。条件（候補の数か閾値以上）と変化、決められないリポジトリの見送り、リポジトリごとの
// 判定は Go の tickets/notice の learning_test.go が見る。

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/samples"
	"ccnavi/internal/infra/env"
)

// originRepo は origin 付きのリポジトリ（統合先 main の wiki を書いて push できる）。
func originRepo(t *testing.T) string {
	t.Helper()
	tmp := base.TempDir(t)
	root := filepath.Join(tmp, "ws")
	origin := filepath.Join(tmp, "o.git")
	if err := os.MkdirAll(root, 0o755); err != nil {
		t.Fatal(err)
	}
	git(t, root, "init", "--quiet", "-b", "main")
	write(t, filepath.Join(root, "README"), "x\n")
	git(t, root, "add", "-A")
	git(t, root, "commit", "--quiet", "-m", "init")
	if out, err := exec.Command("git", "init", "--quiet", "--bare", origin).CombinedOutput(); err != nil {
		t.Fatalf("%v\n%s", err, out)
	}
	git(t, root, "remote", "add", "origin", origin)
	git(t, root, "push", "--quiet", "origin", "main")
	git(t, root, "fetch", "--quiet", "origin")
	git(t, root, "remote", "set-head", "origin", "main")
	return root
}

// patterns は統合先に wiki のパターンを n 件積んで origin へ送る。
func patterns(t *testing.T, root string, n int) {
	t.Helper()
	for i := range n {
		write(t, filepath.Join(root, "docs", "wiki", "patterns", fmt.Sprintf("p%d.md", i)), samples.Pattern([]string{"s0"}, "docs"))
	}
	git(t, root, "add", "-A")
	git(t, root, "commit", "--quiet", "-m", "wiki")
	git(t, root, "push", "--quiet", "origin", "main")
	git(t, root, "fetch", "--quiet", "origin")
}

// sessionStart は SessionStart の hook を 1 回打ち、応答の JSON を返す。
func sessionStart(t *testing.T, root string) map[string]any {
	t.Helper()
	if err := os.MkdirAll(filepath.Join(root, ".claude"), 0o755); err != nil {
		t.Fatal(err)
	}
	got := fire(t, root, call{Event: "SessionStart", Source: "startup", Cwd: root, Mode: "enable", Guard: "disable", Restore: "disable",
		Env: map[string]string{env.Learning: "enable"}})
	if got.Code != 0 {
		t.Fatal(got.Stderr)
	}
	out := map[string]any{}
	if got.Stdout != "" {
		if err := json.Unmarshal([]byte(got.Stdout), &out); err != nil {
			t.Fatal(err)
		}
	}
	return out
}

func TestTheLearningNoticeIsOnlyForTheUserAndIsSaidOnce(t *testing.T) {
	t.Parallel()
	root := originRepo(t)
	patterns(t, root, 5)

	body := sessionStart(t, root)

	mustHave(t, str(body, "systemMessage"), "学びの提案の候補")
	specific, _ := body["hookSpecificOutput"].(map[string]any)
	mustNotHave(t, str(specific, "additionalContext"), "学びの提案の候補")
	mustNotHave(t, str(sessionStart(t, root), "systemMessage"), "学びの提案の候補")
}

func TestTheLearningNoticeCountsFromTheIntegrationRefEvenOnAnotherBranch(t *testing.T) {
	t.Parallel()
	root := originRepo(t)
	patterns(t, root, 5)
	git(t, root, "checkout", "--quiet", "-b", "other")
	git(t, root, "rm", "-q", "-r", "docs/wiki")
	git(t, root, "commit", "--quiet", "-m", "other")

	mustHave(t, str(sessionStart(t, root), "systemMessage"), "5 件")
}
