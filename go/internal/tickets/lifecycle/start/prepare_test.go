package start

import (
	"strings"
	"testing"
)

// fetch の結果の読み分け。取れた・origin に無い・リモート未確認（手元にある）と、手元にも無くて止める道。
func TestFetched(t *testing.T) {
	root := routeRepo(t)
	gone := "fatal: couldn't find remote ref feature-3-z\n"
	for _, c := range []struct {
		branch, out string
		code        int
		want        string // 答えの先頭の行
	}{
		{"feature-3-z", "", 0, "remote checked"},
		{"feature-3-z", gone, 1, "remote gone"},
		{"feature-3-z", "fatal: unable to access\nmore\n", 1, "remote unchecked"},
		{"feature-9-n", "fatal: unable to access\nmore\n", 1, "fail fetch-failed 1"},
	} {
		out, _ := Fetched(root, root, c.branch, c.code, c.out)
		if first, _, _ := strings.Cut(out, "\n"); first != c.want {
			t.Errorf("%s %q: %q, want %q", c.branch, c.out, out, c.want)
		}
		if c.want == "fail fetch-failed 1" && !strings.Contains(out, "（fatal: unable to access）") {
			t.Errorf("理由に git の 1 行目が無い: %q", out)
		}
	}
}

// push を飛ばすのは、先頭が手元の origin/<ブランチ> と同じときだけ。統合先から切るとき origin/<統合先> が無ければ止める。
func TestPreparePushPlanAndOriginIntegration(t *testing.T) {
	root := routeRepo(t)
	out, _ := Prepare(root, root, PrepareArgs{Branch: "main", Integration: "main"})
	if answer(out)["push"] != "skip" {
		t.Errorf("origin/main と同じ先頭で push を飛ばさない: %q", out)
	}
	out, _ = Prepare(root, root, PrepareArgs{Branch: "feature-3-z", Integration: "main"})
	if answer(out)["push"] != "needed" {
		t.Errorf("origin に無いブランチで push を飛ばした: %q", out)
	}
	out, _ = Prepare(root, root, PrepareArgs{Branch: "feature-5-n", From: "origin/trunk", Integration: "trunk"})
	if first, _, _ := strings.Cut(out, "\n"); first != "fail no-origin-integration 1" {
		t.Errorf("origin/trunk が無いのに止めない: %q", out)
	}
}
