package gitwrap

import (
	"strings"
	"testing"

	"ccnavi/internal/infra/golden"
)

// checkCase は testdata/check.json の 1 件。答えは、ccnavi-git.sh を使い捨てのワークスペースで sh のまま打った結果から
// 取った（拒否の識別子は診断ログから読んだ）。pass は、続きの判定をまだラッパーが持つサブコマンドで、前段で拒否されなかったもの。
type checkCase struct {
	Args    []string `json:"args"`
	Verdict string   `json:"verdict"`
	ID      string   `json:"id"`
	Message string   `json:"message"`
}

func TestCheckMatchesTheWrapper(t *testing.T) {
	names := Names{Self: "sh .ccnavi/scripts/ccnavi-git.sh", Sync: "sh .ccnavi/scripts/ccnavi-sync.sh"}
	for _, c := range golden.Load[[]checkCase](t, "check") {
		verdict, d := Check(names, c.Args)
		got := checkCase{Args: c.Args, Verdict: verdict}
		if d != nil {
			got = checkCase{Args: c.Args, Verdict: "deny", ID: d.ID, Message: d.Message}
		}
		if got.Verdict != c.Verdict || got.ID != c.ID || got.Message != c.Message {
			t.Errorf("%q\nGo:   %s %s %s\n見本: %s %s %s", c.Args, got.Verdict, got.ID, got.Message, c.Verdict, c.ID, c.Message)
		}
	}
}

// 代わりの手段が要る拒否は、ラッパーの形か取り込みの sh の形か、ユーザに頼むことを文に書く（理由だけを返すと、
// エージェントは言い換えて打ち直す）。ユーザに頼む文で終わる拒否（remote-write）も、正しい代わりの手段として数える。
func TestRejectionsNameWhatToDoInstead(t *testing.T) {
	names := Names{Self: "sh .ccnavi/scripts/ccnavi-git.sh", Sync: "sh .ccnavi/scripts/ccnavi-sync.sh"}
	needs := map[string]bool{"grep-pager": true, "remote-option": true, "remote-write": true}
	seen := map[string]bool{}
	for _, c := range golden.Load[[]checkCase](t, "check") {
		_, d := Check(names, c.Args)
		if d == nil || !needs[d.ID] {
			continue
		}
		seen[d.ID] = true
		if !strings.Contains(d.Message, names.Self) && !strings.Contains(d.Message, names.Sync) && !strings.Contains(d.Message, "ユーザに") {
			t.Errorf("%s の文に代わりの手段が無い: %s", d.ID, d.Message)
		}
	}
	for id := range needs {
		if !seen[id] {
			t.Errorf("%s の拒否が見本に無い（確かめられない）", id)
		}
	}
}
