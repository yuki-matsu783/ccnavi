package judge

import (
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/golden"
	"ccnavi/internal/tickets/testkit"
)

// 名指しの保護（hook 本体・設定・スクリプト・ccnavi の設定の 4 本）と、その土台の組み込み（プロジェクトの置き場・共通設定・
// 承認済みチケット）が、どの木（ワークスペース・ワークツリー・プロジェクト）のどの場所への書き込みを止めるか。
// testdata/named_guards.json の 1 行が 1 件で、止めるか・ヒットしたルールの並びを固定する。並びも約束で、専用の名前が一般の名前より
// 先に出る。共通のルールが読めない・無い間も 4 本は外れず、自己防衛を切ると 4 本と土台が外れる。
// 入力の {ROOT} はワークスペースのルートに置き換える。state は共通のルールファイルの状態（normal・broken・absent）。

type namedRow struct {
	Case    string         `json:"case"`
	State   string         `json:"state"`
	Tool    string         `json:"tool"`
	Input   map[string]any `json:"input"`
	Deny    bool           `json:"deny"`
	Rules   []string       `json:"rules"`
	Guard   string         `json:"guard"`
	Mode    string         `json:"mode"`
	Tickets string         `json:"tickets"`
}

// namedWorkspace は共通のルールファイルを state の状態にしたワークスペース。
func namedWorkspace(t *testing.T, state string) string {
	t.Helper()
	root := minimalWorkspace(t)
	rules := filepath.Join(root, config.DefaultRules)
	switch state {
	case "broken":
		testkit.Put(t, rules, "deny:\n  - id: [unclosed\n<<<<<<< HEAD\n")
	case "absent":
		if err := os.Remove(rules); err != nil {
			t.Fatal(err)
		}
	}
	return root
}

func TestNamedGuardsTable(t *testing.T) {
	roots := map[string]string{}
	for _, r := range golden.Load[[]namedRow](t, "named_guards") {
		root, ok := roots[r.State]
		if !ok {
			root = namedWorkspace(t, r.State)
			roots[r.State] = root
		}
		input := map[string]any{}
		for k, v := range r.Input {
			if s, ok := v.(string); ok {
				v = strings.ReplaceAll(s, rootMark, filepath.ToSlash(root))
			}
			input[k] = v
		}
		env := map[string]string{"CCNAVI_GUARD_CORE_FILES": "enable"}
		if r.Guard != "" {
			env["CCNAVI_GUARD_CORE_FILES"] = r.Guard
		}
		if r.Mode != "" {
			env["CCNAVI_MODE"] = r.Mode
		}
		if r.Tickets != "" {
			env["CCNAVI_TICKET_CONTROL"] = r.Tickets
		}
		a := runGo(t, root, judgeCase{Payload: call(r.Tool, root, input, "permission_mode", ""), Env: with(flat(env)...)})
		rules, _ := a.Record["rules"].([]any)
		got := []string{}
		for _, x := range rules {
			got = append(got, x.(string))
		}
		want := r.Rules
		if want == nil {
			want = []string{}
		}
		if denied := a.Record["decision"] == "deny"; denied != r.Deny || !slices.Equal(got, want) {
			t.Errorf("%s: %s %v (%s guard=%q mode=%q tickets=%q): deny=%v %v、想定 deny=%v %v", r.Case, r.Tool, r.Input, r.State,
				r.Guard, r.Mode, r.Tickets, denied, got, r.Deny, want)
		}
	}
}

func flat(m map[string]string) []string {
	var out []string
	for k, v := range m {
		out = append(out, k, v)
	}
	return out
}
