package useronly

import (
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/tickets/testkit"
)

// ユーザが打つ入口の sh の作法（.agents/docs/script-output.md の「ユーザ専用 sh の作法」）。

func entryScripts() []string {
	out := make([]string, len(entries))
	for i, e := range entries {
		out[i] = e.script
	}
	return out
}

// 一覧（`ccnavi user`）に載る入口と、配る `ccnavi-user-*.sh`（部品の lib を除く）が一致する。
func TestTheListingMatchesTheUserScripts(t *testing.T) {
	dir, err := os.ReadDir(filepath.Join(testkit.RepoRoot(t), ".ccnavi", "scripts"))
	if err != nil {
		t.Fatal(err)
	}
	have := map[string]bool{}
	for _, e := range dir {
		name := e.Name()
		if strings.HasPrefix(name, UserScriptPrefix) && strings.HasSuffix(name, ".sh") && name != "ccnavi-user-lib-review.sh" {
			have[name] = true
		}
	}
	listing := Listing()
	for _, script := range entryScripts() {
		name := UserScriptPrefix + script + ".sh"
		if !have[name] {
			t.Errorf("一覧にある %s が配る sh に無い", name)
		}
		delete(have, name)
		if !strings.Contains(listing, name+"\n  ") || !strings.Contains(listing, "例: sh .ccnavi/scripts/"+name) {
			t.Errorf("一覧に %s の説明か例が無い", name)
		}
	}
	for name := range have {
		t.Errorf("%s が一覧（entries）に無い", name)
	}
}

// どの入口も `-h` で、標準出力に `使い方: sh .ccnavi/scripts/<名前> …` で始まる usage と、
// `例: sh .ccnavi/scripts/<名前> …` の最後の 1 行を出して 0 で終わる。
func TestEveryUserScriptPrintsUsageWithAnExample(t *testing.T) {
	root := testkit.RepoRoot(t)
	for _, script := range entryScripts() {
		name := UserScriptPrefix + script + ".sh"
		t.Run(name, func(t *testing.T) {
			t.Parallel()
			cmd := exec.Command("sh", filepath.Join(root, ".ccnavi", "scripts", name), "-h")
			cmd.Dir = t.TempDir()
			var stdout, stderr strings.Builder
			cmd.Stdout, cmd.Stderr = &stdout, &stderr
			if err := cmd.Run(); err != nil {
				t.Fatalf("-h が 0 で終わらない: %v\n%s", err, stderr.String())
			}
			lines := strings.Split(strings.TrimRight(stdout.String(), "\n"), "\n")
			if !strings.HasPrefix(lines[0], "使い方: sh .ccnavi/scripts/"+name) {
				t.Errorf("1 行目が使い方で始まらない: %q", lines[0])
			}
			if last := lines[len(lines)-1]; !strings.HasPrefix(last, "例: sh .ccnavi/scripts/"+name) {
				t.Errorf("最後の行が例の 1 行でない: %q", last)
			}
		})
	}
}

// sh 自身が引数の誤りを見つける入口は、終了コード 2 で断る（実行ファイルを起こすだけの包みの handoff・mirror・prune は、
// 実行ファイルの使い方の誤りの終了コードが返る）。
func TestUserScriptsRefuseAnUnknownOptionWith2(t *testing.T) {
	root := testkit.RepoRoot(t)
	for _, script := range []string{"agree", "decide", "close-early", "clean-branch", "push-approved"} {
		name := UserScriptPrefix + script + ".sh"
		t.Run(name, func(t *testing.T) {
			t.Parallel()
			cmd := exec.Command("sh", filepath.Join(root, ".ccnavi", "scripts", name), "--bogus-option")
			cmd.Dir = t.TempDir()
			err := cmd.Run()
			var exit *exec.ExitError
			if !errors.As(err, &exit) || exit.ExitCode() != 2 {
				t.Errorf("終了コードが 2 でない: %v", err)
			}
		})
	}
}
