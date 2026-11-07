package testkit

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// Clock はコミットの時刻（git の日付の書式）。この時刻で git を走らせると、同じ形を 2 回作ったときに sha も同じに
// なる。testdata の写しに sha が入るので、パッケージごとに決めた時刻は変えない。
type Clock string

// fixed はふつうに使う時刻。
const fixed Clock = "2026-01-05T00:00:00+0000"

// env は git に渡す環境（利用者の設定を読まず、名前と時刻を固定する）。
func (c Clock) env() []string {
	return []string{"GIT_CONFIG_GLOBAL=" + os.DevNull, "GIT_CONFIG_NOSYSTEM=1",
		"GIT_AUTHOR_NAME=t", "GIT_AUTHOR_EMAIL=t@example.com", "GIT_COMMITTER_NAME=t", "GIT_COMMITTER_EMAIL=t@example.com",
		"GIT_AUTHOR_DATE=" + string(c), "GIT_COMMITTER_DATE=" + string(c)}
}

// Git は git を走らせて標準出力を返す。失敗すればテストを落とす。
func (c Clock) Git(t *testing.T, dir string, args ...string) string {
	t.Helper()
	cmd := exec.Command("git", args...)
	cmd.Dir = dir
	cmd.Env = append(os.Environ(), c.env()...)
	out, err := cmd.Output()
	if err != nil {
		var stderr string
		if e, ok := err.(*exec.ExitError); ok {
			stderr = string(e.Stderr)
		}
		t.Fatalf("git %v: %v\n%s%s", args, err, out, stderr)
	}
	return string(out)
}

// commit はそのツリーの変更を全部コミットする（変更が無くてもコミットを作る）。
func (c Clock) commit(t *testing.T, dir, message string) {
	t.Helper()
	c.Git(t, dir, "add", "-A")
	c.Git(t, dir, "commit", "-q", "--allow-empty", "-m", message)
}

// Workspace はワークスペース（main に空のコミット 1 つ）と、main から切った名前 = ブランチのワークツリーを作り、
// 文面ファイルを写す。
func (c Clock) Workspace(t *testing.T, worktrees ...string) string {
	t.Helper()
	root := GitRoot(t)
	c.Git(t, root, "init", "-q", "-b", "main")
	c.Git(t, root, "commit", "-q", "--allow-empty", "-m", "init")
	for _, name := range worktrees {
		AddWorktree(t, root, name, "main")
	}
	CopyMessages(t, root)
	return root
}

// Git は fixed の時刻で git を走らせる。
func Git(t *testing.T, dir string, args ...string) string {
	t.Helper()
	return fixed.Git(t, dir, args...)
}

// GitEnv は fixed の時刻の環境。
func GitEnv() []string { return fixed.env() }

// Commit は fixed の時刻でコミットする。
func Commit(t *testing.T, dir, message string) {
	t.Helper()
	fixed.commit(t, dir, message)
}

// Workspace は fixed の時刻でワークスペースを作る。
func Workspace(t *testing.T, worktrees ...string) string {
	t.Helper()
	return fixed.Workspace(t, worktrees...)
}

// TempRoot はリンクを解いた一時ディレクトリ（macOS の /var → /private/var で比べるパスがずれないように）。
func TempRoot(t *testing.T) string {
	t.Helper()
	root, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	return root
}

// GitRoot は git のある機械での TempRoot。git が無ければテストを飛ばす。
func GitRoot(t *testing.T) string {
	t.Helper()
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("git が無い")
	}
	return TempRoot(t)
}

// Remote は bare のリモート（root/remote.git）を origin に足す。
func Remote(t *testing.T, root string) {
	t.Helper()
	remote := filepath.Join(root, "remote.git")
	Git(t, root, "init", "-q", "--bare", remote)
	Git(t, root, "remote", "add", "origin", remote)
}

// Worktree はワークツリーの場所。
func Worktree(root, name string) string { return filepath.Join(root, ".claude", "worktrees", name) }

// AddWorktree は from から切った名前 = ブランチのワークツリーを作る。
func AddWorktree(t *testing.T, root, name, from string) string {
	t.Helper()
	Git(t, root, "worktree", "add", "-q", "-b", name, filepath.Join(".claude", "worktrees", name), from)
	return Worktree(root, name)
}

// Head はそのツリーの HEAD の sha。
func Head(t *testing.T, dir string) string {
	return strings.TrimSpace(Git(t, dir, "rev-parse", "HEAD"))
}
