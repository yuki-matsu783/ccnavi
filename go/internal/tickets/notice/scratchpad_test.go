package notice

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/infra/env"
	"ccnavi/internal/tickets/testkit"
)

func putSize(t *testing.T, root, rel string, size int) string {
	t.Helper()
	return testkit.Put(t, filepath.Join(root, "scratchpad", filepath.FromSlash(rel)), strings.Repeat("x", size))
}

func TestScratchBytes(t *testing.T) {
	root := t.TempDir()
	if got := scratchBytes(root, time.Time{}); got != 0 {
		t.Errorf("置き場が無ければ 0: %d", got)
	}
	target := putSize(t, root, "a.bin", 100)
	putSize(t, root, "sub/b.bin", 200)
	if got := scratchBytes(root, time.Time{}); got != 300 {
		t.Errorf("入れ子のファイルも数える: %d", got)
	}
	if got := scratchBytes(root, time.Now().Add(-time.Second)); got != 0 {
		t.Errorf("期限を過ぎたら走査しない: %d", got)
	}
	if os.Symlink(target, filepath.Join(root, "scratchpad", "link.bin")) != nil ||
		os.Symlink(root, filepath.Join(root, "scratchpad", "dirlink")) != nil {
		t.Skip("symlink 不可")
	}
	if got := scratchBytes(root, time.Time{}); got != 300 {
		t.Errorf("リンクは辿らず数えない: %d", got)
	}
}

func TestWarnMB(t *testing.T) {
	for _, c := range []struct {
		written string
		want    float64
	}{{"", defaultWarnMB}, {"abc", defaultWarnMB}, {"-1", defaultWarnMB}, {"inf", defaultWarnMB},
		{"nan", defaultWarnMB}, {"0", 0}, {"2.5", 2.5}, {" 3 ", 3}} {
		t.Setenv(env.ScratchpadWarnMB, c.written)
		if got := warnMB(); got != c.want {
			t.Errorf("%q: %v（期待 %v）", c.written, got, c.want)
		}
	}
}

const scratchOracle = `
import json, os, sys
from unittest import mock
from ccnavi.tickets.notice import scratchpad
c = json.load(sys.stdin)
out = {}
for limit in c["limits"]:
    with mock.patch.dict(os.environ, {scratchpad.WARN_MB_ENV: limit}):
        out[limit] = scratchpad.session_text(c["root"]).replace(c["root"], "<root>")
json.dump(out, sys.stdout, ensure_ascii=False)
`

func TestScratchpadMatchesPython(t *testing.T) {
	root := workspace(t)
	putSize(t, root, "big.bin", 2*mb+mb/2)
	limits := []string{"1", "1.5", "2.5", "3", "0", "0.000001"}
	want := testkit.Answers[map[string]any](t, "scratchpad", scratchOracle, map[string]any{"root": root, "limits": limits})
	got := map[string]any{}
	for _, limit := range limits {
		t.Setenv(env.ScratchpadWarnMB, limit)
		got[limit] = testkit.Rel(root, Scratchpad(root, time.Time{}))
	}
	testkit.SameJSON(t, got, want)
}
