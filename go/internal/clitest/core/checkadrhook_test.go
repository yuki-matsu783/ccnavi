package core

import (
	"path/filepath"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
)

// `.claude/hooks/check-adr.sh` が、ADR の番号の重なりと置き場の間違いを止める。ADR は `docs/30_adr/<主題>/NNNN-名前.md` に分けて置く。
// 番号は主題をまたぐ通し番号なので、別の主題が使っている番号を採ると重なる。書いた直後に exit 2 で返すことを確かめる。
type adrEnv struct {
	t    *testing.T
	tree string
}

func newADREnv(t *testing.T) *adrEnv {
	e := &adrEnv{t, emptyRoot(t)}
	e.put("rules/0172-a.md")
	e.put("ticket/0171-b.md")
	return e
}

func (e *adrEnv) put(rel string) string {
	return writeFile(e.t, filepath.Join(e.tree, "docs", "30_adr"), rel, "")
}

func (e *adrEnv) hook(path string) run.Result {
	e.t.Helper()
	return run.Sh(e.t, base.Final(".claude/hooks/check-adr.sh"), nil, run.Opts{Input: mustJSON(e.t, map[string]any{"tool_input": map[string]any{"file_path": path}})})
}

func TestCheckADRHook(t *testing.T) {
	t.Parallel()
	t.Run("空いている番号は通る", func(t *testing.T) {
		t.Parallel()
		e := newADREnv(t)
		if r := e.hook(e.put("rules/0173-c.md")); r.Code != 0 {
			t.Errorf("code = %d: %s", r.Code, r.Stderr)
		}
	})
	t.Run("別の主題が使っている番号は止める", func(t *testing.T) {
		t.Parallel()
		e := newADREnv(t)
		r := e.hook(e.put("ticket/0172-d.md"))
		if r.Code != 2 {
			t.Errorf("code = %d", r.Code)
		}
		mustContain(t, "stderr", r.Stderr, "0172-a.md", "0173")
	})
	t.Run("次の番号は先頭の 0 を 8 進に読まない", func(t *testing.T) {
		t.Parallel()
		e := newADREnv(t)
		e.put("base/0179-e.md")
		mustContain(t, "stderr", e.hook(e.put("rules/0172-f.md")).Stderr, "0180")
	})
	t.Run("直下に置いたら止める", func(t *testing.T) {
		t.Parallel()
		e := newADREnv(t)
		top := writeFile(t, filepath.Join(e.tree, "docs", "30_adr"), "0173-top.md", "")
		r := e.hook(top)
		if r.Code != 2 {
			t.Errorf("code = %d", r.Code)
		}
		mustContain(t, "stderr", r.Stderr, "主題のディレクトリ")
	})
	t.Run("ほかのファイルは見ない", func(t *testing.T) {
		t.Parallel()
		e := newADREnv(t)
		for _, path := range []string{"/x/a.py", filepath.Join(e.tree, "docs", "30_adr", "rules", "memo.md")} {
			if r := e.hook(path); r.Code != 0 {
				t.Errorf("%s: code = %d", path, r.Code)
			}
		}
	})
}
