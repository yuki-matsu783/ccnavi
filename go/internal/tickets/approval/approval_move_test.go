package approval

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/tickets/testkit"
)

// 別のファイルシステムをまたぐ置き場（rename が EXDEV で通らない）。機械に無ければ飛ばす。
func otherDevice(t *testing.T) string {
	t.Helper()
	const shm = "/dev/shm"
	if runtime.GOOS == "windows" {
		t.Skip("別のファイルシステムの置き場を決められない")
	}
	dir, err := os.MkdirTemp(shm, "ccnavi-move-")
	if err != nil {
		t.Skip("別のファイルシステムの置き場が無い")
	}
	t.Cleanup(func() { os.RemoveAll(dir) })
	probe := testkit.Put(t, filepath.Join(t.TempDir(), "probe"), "x")
	if err := os.Rename(probe, filepath.Join(dir, "probe")); err == nil || !fsio.CrossDevice(err) {
		t.Skip("同じファイルシステムだった")
	}
	return dir
}

// またぐときはコピーして消す（中身を変えず、両方に残さない）。
func TestMoveFileAcrossDevices(t *testing.T) {
	const text = "---\nticket: i0001\n---\nbody\n"
	target := filepath.Join(otherDevice(t), "done", "i0001.md")
	source := testkit.Put(t, filepath.Join(t.TempDir(), "doing", "i0001.md"), text)
	if failed := moveFile(source, target); failed != "" {
		t.Fatal(failed)
	}
	if data, _ := os.ReadFile(target); string(data) != text {
		t.Errorf("中身が変わった: %q", data)
	}
	if _, err := os.Stat(source); err == nil {
		t.Error("元が残った")
	}
}

// コピーできたが元を消せないときは、コピーした側を消して戻す（両方に残すと、以後どの操作も「複数の場所に
// ある」で止まる）。
func TestMoveFileAcrossDevicesRollsBackWhenTheSourceStays(t *testing.T) {
	if os.Geteuid() == 0 {
		t.Skip("root は書けないディレクトリの中も消せる")
	}
	const text = "---\nticket: i0001\n---\nbody\n"
	target := filepath.Join(otherDevice(t), "done", "i0001.md")
	doing := filepath.Join(t.TempDir(), "doing")
	source := testkit.Put(t, filepath.Join(doing, "i0001.md"), text)
	if err := os.Chmod(doing, 0o500); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.Chmod(doing, 0o700) })
	if failed := moveFile(source, target); !strings.Contains(failed, "チケットを動かせない") {
		t.Fatalf("断らない: %q", failed)
	}
	if data, _ := os.ReadFile(source); string(data) != text {
		t.Errorf("元が変わった: %q", data)
	}
	if _, err := os.Stat(target); err == nil {
		t.Error("コピーした側が残った")
	}
}
