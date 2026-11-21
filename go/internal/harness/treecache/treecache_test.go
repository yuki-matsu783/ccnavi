package treecache

import (
	"os"
	"path/filepath"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"ccnavi/internal/harness/base"
)

type state struct {
	Root  string
	Count int
}

func write(t testing.TB, path, text string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
		t.Fatal(err)
	}
}

func read(t testing.TB, path string) string {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

func exists(path string) bool { _, err := os.Lstat(path); return err == nil }

// 手順は鍵ごとに 1 度だけ走る。2 度目からは、前のテストが木に足した物・書き換えた物・消した物が写しの中身へ戻り、
// 返り値も写しのものが返る。パスは毎回同じ。
func TestSecondUseRestoresTheSavedTreeAtTheSamePathWithoutRunningTheBuild(t *testing.T) {
	var builds atomic.Int32
	build := func(dir string) state {
		builds.Add(1)
		write(t, filepath.Join(dir, "a", "keep.txt"), "keep")
		write(t, filepath.Join(dir, "a", "edit.txt"), "before")
		write(t, filepath.Join(dir, "gone.txt"), "gone")
		write(t, filepath.Join(dir, "exec.sh"), "#!/bin/sh\n")
		if err := os.Chmod(filepath.Join(dir, "exec.sh"), 0o755); err != nil {
			t.Fatal(err)
		}
		return state{Root: filepath.Join(dir, "a"), Count: 7}
	}
	var first string
	for round := range 3 {
		t.Run("", func(t *testing.T) {
			dir, v := Build(t, "restore", build)
			if round == 0 {
				first = dir
			} else if dir != first {
				t.Errorf("パスが変わった: %s → %s", first, dir)
			}
			if v.Count != 7 || v.Root != filepath.Join(dir, "a") {
				t.Errorf("返り値: %+v", v)
			}
			// 前のテストの残骸が消えている
			if exists(filepath.Join(dir, "extra")) || exists(filepath.Join(dir, "a", "new.txt")) {
				t.Error("前のテストが足した物が残っている")
			}
			if got := read(t, filepath.Join(dir, "a", "edit.txt")); got != "before" {
				t.Errorf("書き換えた物が戻らない: %q", got)
			}
			if got := read(t, filepath.Join(dir, "gone.txt")); got != "gone" {
				t.Errorf("消した物が戻らない: %q", got)
			}
			if info, err := os.Stat(filepath.Join(dir, "exec.sh")); err != nil || (!ospathWindows() && info.Mode().Perm()&0o100 == 0) {
				t.Errorf("実行の権限が戻らない: %v %v", info, err)
			}
			// このテストの変更（次の回が戻す）
			write(t, filepath.Join(dir, "extra", "x"), "x")
			write(t, filepath.Join(dir, "a", "new.txt"), "n")
			write(t, filepath.Join(dir, "a", "edit.txt"), "after")
			if err := os.Remove(filepath.Join(dir, "gone.txt")); err != nil {
				t.Fatal(err)
			}
		})
	}
	if builds.Load() != 1 {
		t.Errorf("手順が %d 回走った（1 回のはず）", builds.Load())
	}
}

func TestRestoredFilesKeepTheirModificationTime(t *testing.T) {
	stamp := time.Unix(1_600_000_000, 0)
	build := func(dir string) int {
		write(t, filepath.Join(dir, "f"), "x")
		if err := os.Chtimes(filepath.Join(dir, "f"), stamp, stamp); err != nil {
			t.Fatal(err)
		}
		return 0
	}
	for range 2 {
		t.Run("", func(t *testing.T) {
			dir, _ := Build(t, "mtime", build)
			info, err := os.Stat(filepath.Join(dir, "f"))
			if err != nil || !info.ModTime().Equal(stamp) {
				t.Errorf("更新時刻 %v, err %v", info.ModTime(), err)
			}
			// 触って壊す（次の回は写しの更新時刻に戻る）
			write(t, filepath.Join(dir, "f"), "changed")
		})
	}
}

func TestSymlinksAreRestoredAsLinks(t *testing.T) {
	if !base.CanSymlink() {
		t.Skip("この機械ではリンクが作れない")
	}
	build := func(dir string) int {
		write(t, filepath.Join(dir, "real"), "r")
		if err := os.Symlink("real", filepath.Join(dir, "link")); err != nil {
			t.Fatal(err)
		}
		return 0
	}
	for range 2 {
		t.Run("", func(t *testing.T) {
			dir, _ := Build(t, "links", build)
			if dest, err := os.Readlink(filepath.Join(dir, "link")); err != nil || dest != "real" {
				t.Errorf("リンク先 %q, err %v", dest, err)
			}
			// リンクを実体に置き換える（次の回は戻す）
			if err := os.Remove(filepath.Join(dir, "link")); err != nil {
				t.Fatal(err)
			}
			write(t, filepath.Join(dir, "link"), "file now")
		})
	}
}

func TestDifferentKeysHaveDifferentPaths(t *testing.T) {
	a, _ := Build(t, "key-a", func(dir string) int { return 1 })
	b, _ := Build(t, "key-b", func(dir string) int { return 2 })
	if a == b {
		t.Errorf("鍵が違えばパスも違う: %s", a)
	}
}

// 同じ鍵を使う並行のテストは、取り合わず順に並ぶ（同時に dir の中身を触らない）。
func TestParallelUsersOfOneKeyTakeTurns(t *testing.T) {
	var inside, worst atomic.Int32
	var builds atomic.Int32
	build := func(dir string) int { builds.Add(1); write(t, filepath.Join(dir, "f"), "0"); return 0 }
	var wg sync.WaitGroup
	for i := range 6 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			t.Run("", func(t *testing.T) {
				dir, _ := Build(t, "serial", build)
				now := inside.Add(1)
				if now > worst.Load() {
					worst.Store(now)
				}
				if got := read(t, filepath.Join(dir, "f")); got != "0" {
					t.Errorf("他のテストの書き込みが見える: %q", got)
				}
				write(t, filepath.Join(dir, "f"), string(rune('a'+i)))
				time.Sleep(5 * time.Millisecond)
				inside.Add(-1)
			})
		}()
	}
	wg.Wait()
	if worst.Load() != 1 || builds.Load() != 1 {
		t.Errorf("同時に %d 本が使った・手順は %d 回", worst.Load(), builds.Load())
	}
}

func ospathWindows() bool { return os.PathSeparator == '\\' }
