package diaglog

import (
	"testing"

	"ccnavi/internal/infra/env"
)

// sh（ccnavi-common-log.sh）と拡張（log.ts）と同じ入力から、同じ行を出す（時刻と pid を除く）。
// 入力と答えは tests/sh/test_diaglog_sh.py の CASES・EXPECTED と同じ。
func TestSameLinesAsTheShAndTheExtension(t *testing.T) {
	root := setup(t)
	t.Setenv(env.LogLevel, "DEBUG")
	log := Get("probe", root)
	log.Info("push を拒否した", "reason", "unapproved", "ticket", "T-12")
	log.Warn("値の逃がし方", "v", "a b", "q", `say "hi"\x`, "eq", "k=v", "tab", "a\tb")
	log.Error("改行を\n含む本文", "n", "l1\nl2\r\nl3\rl4", "empty", "", "bs", `a\b`)
	log.Info("値の無い行")
	log.Debug("判定の材料", "sub", "push", "path", "/tmp/x y/z")
	log.Info("clone https://u:p@h/x を読んだ",
		"url", "https://user:glpat-SECRET@gitlab.example/x.git",
		"scp", "oauth2:tok@gitlab.example:org/r.git",
		"plain", "git@github.com:org/r.git",
		"said", "push to ssh://git@h:22/p failed")
	want := [][3]string{
		{"INFO ", "probe", "push を拒否した reason=unapproved ticket=T-12"},
		{"WARN ", "probe", "値の逃がし方 v=\"a b\" q=\"say \\\"hi\\\"\\\\x\" eq=\"k=v\" tab=\"a\tb\""},
		{"ERROR", "probe", `改行を\n含む本文 n="l1\nl2\nl3\nl4" empty= bs=a\b`},
		{"INFO ", "probe", "値の無い行"},
		{"DEBUG", "probe", `判定の材料 sub=push path="/tmp/x y/z"`},
		{"INFO ", "probe", "clone https://***@h/x を読んだ url=https://***@gitlab.example/x.git" +
			" scp=***@gitlab.example:org/r.git plain=git@github.com:org/r.git" +
			` said="push to ssh://***@h:22/p failed"`},
	}
	got := lines(t, root)
	if len(got) != len(want) {
		t.Fatalf("lines = %q", got)
	}
	for i, line := range got {
		m := head.FindStringSubmatch(line)
		if m == nil {
			t.Errorf("頭の形が違う: %q", line)
			continue
		}
		if have := [3]string{m[2], m[3], line[len(m[0]):]}; have != want[i] {
			t.Errorf("%d 行目 = %q, want %q", i, have, want[i])
		}
	}
}
