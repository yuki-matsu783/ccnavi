package stopreview

import (
	"os"
	"testing"
	"time"
)

// 道は申し送りの最後の route=。書かれていなければ送る前の失敗、送ったのに終了コードが 0 でなければ送ったあとの失敗。
func TestEndResultDecidesTheRoute(t *testing.T) {
	for _, c := range []struct {
		note  string
		rc    int
		route string
	}{
		{"", 0, "failed_before"},
		{"route=stopped\n", 1, "stopped"},
		{"route=sent\ncommit=abc\n", 0, "sent"},
		{"route=sent\ncommit=abc\n", 1, "failed_after"},
		{"route=write_only\nroute=unchanged\n", 0, "unchanged"},
	} {
		state := t.TempDir()
		now := time.Unix(100, 0)
		path, started, ok := BeginResult(state, now)
		if !ok || started != 100 {
			t.Fatal(path, started)
		}
		if err := os.WriteFile(path+noteSuffix, []byte(c.note), 0o644); err != nil {
			t.Fatal(err)
		}
		EndResult(path, "p0001", c.rc, started, time.Unix(105, 0))
		got := readResult(path)
		if got == nil || got.String("route") != c.route || got.String("id") != "p0001" {
			t.Errorf("%q %d: %v", c.note, c.rc, got)
		}
		if _, err := os.Stat(path + noteSuffix); err == nil {
			t.Errorf("%q: 申し送りが残った", c.note)
		}
	}
}
