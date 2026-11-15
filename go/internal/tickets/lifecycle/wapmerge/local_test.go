package wapmerge

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/tickets/testkit"
)

// ローカルの書き込みと戻し（コミット・合流・比較つきの戻し）を、一時のリポジトリで確かめる。push と ls-remote は sh。

func localRepo(t *testing.T) string {
	t.Helper()
	for _, kv := range testkit.GitEnv() {
		k, v, _ := strings.Cut(kv, "=")
		t.Setenv(k, v)
	}
	root := testkit.Workspace(t)
	testkit.Put(t, filepath.Join(root, "s", "old.json"), "old\n")
	testkit.Commit(t, root, "base")
	return root
}

func read(t *testing.T, path string) string {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		return "<無い>"
	}
	return string(data)
}

func TestCommitAndUndo(t *testing.T) {
	for _, c := range []struct {
		name    string
		restore Restore
		// 戻した後の s/new.json の中身（Keep・Nothing なら残る）
		left string
	}{
		{"書いたものを戻す", Restore{}, "<無い>"},
		{"keep は戻さない", Restore{Keep: map[string]bool{"s/new.json": true}}, "new\n"},
		{"Nothing はワークツリーを戻さない", Restore{Nothing: true}, "new\n"},
	} {
		t.Run(c.name, func(t *testing.T) {
			root := localRepo(t)
			base := Head(root)
			testkit.Put(t, filepath.Join(root, "s", "new.json"), "new\n")
			testkit.Put(t, filepath.Join(root, "s", "old.json"), "changed\n")
			testkit.Put(t, filepath.Join(root, "other.txt"), "他人の書きかけ\n")
			written := []string{"s/new.json", "s/old.json", "s/gone.json"}
			got := Commit(root, written, "ccnavi: x", time.Minute)
			if !got.OK || got.Sha == "" || len(got.Said) > 0 {
				t.Fatalf("コミットできない: %+v", got)
			}
			paths := strings.Fields(testkit.Git(t, root, "show", "--name-only", "--format=", "HEAD"))
			if strings.Join(paths, " ") != "s/new.json s/old.json" {
				t.Fatalf("コミットしたパス %v", paths)
			}
			// 2 回目は変わったものが無いのでコミットしない。
			if again := Commit(root, written, "ccnavi: x", time.Minute); !again.OK || again.Sha != "" {
				t.Fatalf("変わっていないのにコミットした: %+v", again)
			}
			branch := Branch(root)
			said, ok := c.restore.Undo(root, branch, got.Sha, base, written)
			if !ok || len(said) > 0 || Head(root) != base {
				t.Fatalf("戻せない: %v %v", said, ok)
			}
			if left := read(t, filepath.Join(root, "s", "new.json")); left != c.left {
				t.Errorf("s/new.json = %q, want %q", left, c.left)
			}
			if read(t, filepath.Join(root, "other.txt")) != "他人の書きかけ\n" {
				t.Error("一覧に無いファイルを戻した")
			}
			// 先頭が自分のコミットでなければ戻さない。
			if _, ok := c.restore.Undo(root, branch, got.Sha, base, written); ok {
				t.Error("先頭が動いたのに戻した")
			}
		})
	}
}

func TestBeginConflictLeavesTree(t *testing.T) {
	root := localRepo(t)
	base := Head(root)
	testkit.Git(t, root, "checkout", "-q", "-b", "child")
	testkit.Put(t, filepath.Join(root, "s", "old.json"), "child\n")
	testkit.Commit(t, root, "child")
	child := Head(root)
	testkit.Git(t, root, "checkout", "-q", "main")
	testkit.Put(t, filepath.Join(root, "s", "old.json"), "parent\n")
	testkit.Commit(t, root, "parent")
	parent := Head(root)
	m, said, ok := Begin(root, parent, child, "i0001-01-01", "main")
	if ok || m.Active || len(said) != 2 || !strings.Contains(said[0], "衝突する（s/old.json）") {
		t.Fatalf("衝突で止まらない: %v %v", said, ok)
	}
	if Head(root) != parent || Busy(root) != "" {
		t.Fatal("取りやめた後に元に戻っていない")
	}
	// 子の先頭が親に入っていれば、合流するものは無い。
	if m, _, ok := Begin(root, parent, base, "i0001-01-01", "main"); !ok || m.Active {
		t.Fatal("入っている子を合流しようとした")
	}
}
