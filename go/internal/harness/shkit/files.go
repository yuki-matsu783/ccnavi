package shkit

import (
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"testing"
)

// Need は tools のどれかが PATH に無ければ、そのテストを飛ばす。
func Need(t testing.TB, tools ...string) {
	t.Helper()
	for _, tool := range tools {
		if _, err := exec.LookPath(tool); err != nil {
			t.Skipf("%s が無い", tool)
		}
	}
}

// Write は path に text を書く（親のディレクトリも作る。改行はそのまま）。path を返す。
func Write(t testing.TB, path, text string) string {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
		t.Fatal(err)
	}
	return path
}

// WriteExec は Write して、実行できるようにする。
func WriteExec(t testing.TB, path, text string) string {
	t.Helper()
	Write(t, path, text)
	if err := os.Chmod(path, 0o755); err != nil {
		t.Fatal(err)
	}
	return path
}

// Read は path の中身。読めなければテストを止める。
func Read(t testing.TB, path string) string {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return string(data)
}

// Mkdir は path（と親）を作り、path を返す。
func Mkdir(t testing.TB, path string) string {
	t.Helper()
	if err := os.MkdirAll(path, 0o755); err != nil {
		t.Fatal(err)
	}
	return path
}

// Remove は path を中身ごと消す（無くてもよい）。
func Remove(t testing.TB, path string) {
	t.Helper()
	if err := os.RemoveAll(path); err != nil {
		t.Fatal(err)
	}
}

// Exists はリンクの先を辿らずに path があるか。
func Exists(path string) bool {
	_, err := os.Lstat(path)
	return err == nil
}

// IsDir は path がディレクトリ（リンクなら先）か。
func IsDir(path string) bool {
	info, err := os.Stat(path)
	return err == nil && info.IsDir()
}

// IsFile は path が通常のファイル（リンクなら先）か。
func IsFile(path string) bool {
	info, err := os.Stat(path)
	return err == nil && info.Mode().IsRegular()
}

// Names は dir の直下の名前（辞書順）。
func Names(t testing.TB, dir string) []string {
	t.Helper()
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	out := make([]string, 0, len(entries))
	for _, e := range entries {
		out = append(out, e.Name())
	}
	sort.Strings(out)
	return out
}

// Slash は sh に渡すパスの区切りを / にそろえる（Git Bash は `\` を食う）。
func Slash(path string) string { return filepath.ToSlash(path) }

// CopyOpts は CopyTree の選び方。
type CopyOpts struct {
	// KeepLinks なら、リンクをリンクのまま写す。既定では、ファイルを指すリンクはリンク先の中身を写し、
	// ディレクトリを指すリンクは空のディレクトリになる（WalkDir はリンクの先へ下りない）。
	KeepLinks bool
	// Skip が true を返す相対パス（`/` 区切り）は写さない（ディレクトリなら下も）。
	Skip func(rel string) bool
}

// CopyTree は src の中身を dst へ写す。権限は保つ。
func CopyTree(t testing.TB, src, dst string, opts ...CopyOpts) {
	t.Helper()
	var o CopyOpts
	if len(opts) > 0 {
		o = opts[0]
	}
	err := filepath.WalkDir(src, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		rel, err := filepath.Rel(src, path)
		if err != nil {
			return err
		}
		if rel == "." {
			return os.MkdirAll(dst, 0o755)
		}
		if o.Skip != nil && o.Skip(filepath.ToSlash(rel)) {
			if d.IsDir() {
				return filepath.SkipDir
			}
			return nil
		}
		target := filepath.Join(dst, rel)
		if d.Type()&fs.ModeSymlink != 0 && o.KeepLinks {
			to, err := os.Readlink(path)
			if err != nil {
				return err
			}
			return os.Symlink(to, target)
		}
		info, err := os.Stat(path)
		if err != nil {
			return err
		}
		if info.IsDir() {
			return os.MkdirAll(target, 0o755)
		}
		data, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		return os.WriteFile(target, data, info.Mode().Perm())
	})
	if err != nil {
		t.Fatal(err)
	}
}
