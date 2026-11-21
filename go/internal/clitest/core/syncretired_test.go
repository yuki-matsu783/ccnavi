package core

import (
	"path/filepath"
	"testing"

	"ccnavi/internal/harness/run"
)

// `ccnavi sync retired` の答えの形と、置き場のフラグが効かないこと。片付けの記録の置き場は固定で、env でも診断のフラグでも動かない
// （設計 wip/design/i0064-fixed-places.md）。中身は go/internal/tickets の retire の go test が見る。

// sh は受け取った引数を `"$@"` で渡すので、サブコマンドに置き場のフラグを足されても効かない（置き場は固定）。
// 無視したことは標準エラーに出す。
func TestSyncRetiredPlaceFlagsAreIgnoredOutsideDiagnosis(t *testing.T) {
	t.Parallel()
	ws := emptyRoot(t)
	mkdir(t, filepath.Join(ws, ".claude"))
	elsewhere := filepath.Join(ws, "elsewhere")
	writeFile(t, elsewhere, "retire/i0001.json", `{"parent": "i0001", "project": "", "branch": "i0001", "mr": 42, "children": []}`+"\n")
	r := run.Ccnavi(t, []string{
		"--root", ws, "--approved", "x/approved", "--tickets", "x/proposals", "--state", elsewhere, "--log", "", "sync", "retired",
	})
	if r.Code != 0 || r.Stdout != "retired 1\n" {
		t.Errorf("code = %d, stdout = %q, stderr = %q", r.Code, r.Stdout, r.Stderr)
	}
	for _, flag := range []string{"--approved", "--tickets", "--state", "--log"} {
		mustContain(t, "stderr", r.Stderr, "ccnavi: "+flag+" は診断")
	}
}

// 答えの形だけをここで見る。頭の行が先に来て、注記は付かない。
func TestSyncRetiredTheHeadComesFirstWithoutAnyNote(t *testing.T) {
	t.Parallel()
	ws := emptyRoot(t)
	mkdir(t, filepath.Join(ws, ".claude"))
	r := run.Ccnavi(t, []string{"--root", ws, "sync", "retired"})
	if r.Code != 0 || r.Stdout != "retired 1\n" {
		t.Errorf("code = %d, stdout = %q", r.Code, r.Stdout)
	}
}
