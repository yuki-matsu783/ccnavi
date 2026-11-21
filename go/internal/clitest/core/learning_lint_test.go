package core

import (
	"strings"
	"testing"

	"ccnavi/internal/harness/run"
)

// CCNAVI_LEARNING の書き誤りを `ccnavi --lint` が warn で言うか。
func TestLintWarnsAMistypedLearningSwitch(t *testing.T) {
	t.Parallel()
	cases := []struct {
		name  string
		value *string // nil は未設定
		warns bool
	}{
		{"書き誤りの値は言う", ptr("on"), true},
		// 空白つきの値は実行時に disable として動くので、見逃さない。
		{"空白つきの値は言う", ptr(" enable"), true},
		{"enable は言わない", ptr("enable"), false},
		{"未設定は言わない", nil, false},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			ws := emptyRoot(t)
			writeFile(t, ws, ".claude/.keep", "")
			git(t, ws, "init", "--quiet", "-b", "main")
			o := run.Opts{Dir: ws}
			if c.value == nil {
				o.Unset = []string{"CCNAVI_LEARNING"}
			} else {
				o.Env = map[string]string{"CCNAVI_LEARNING": *c.value}
			}
			r := run.Ccnavi(t, []string{"--root", ws, "--lint"}, o)
			out := r.Stdout + r.Stderr
			if has := strings.Contains(out, "CCNAVI_LEARNING"); has != c.warns {
				t.Errorf("CCNAVI_LEARNING を言った = %v, want %v: %s", has, c.warns, out)
			}
		})
	}
}

func ptr(s string) *string { return &s }
