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

// このリポジトリの実際のルール（.ccnavi/config/common/rules.yml）と組み込みのルールで、コマンドと書き込みの行き先が
// どう判定されるか。testdata/repo_rules.json の 1 行が 1 件で、判定（allow・ask・deny。ルールが何も当たらず判定を
// 持たないものは handover）とヒットしたルールの名前（書いた id。組み込みは builtin: つき）を固定する。
// rules.yml を変えたら、この表のどの行が変わったかを見て、変えた狙いの行だけが変わったことを確かめて直す。
// subject の `/repo` はワークスペースのルートに置き換える。entry があれば、その実行ファイル（CCNAVI_ENTRY_PATH）から
// 起動した形で判定する（承認のルールは実行ファイルのパスから当てる）。

type repoRuleRow struct {
	Case    string   `json:"case"`
	Tool    string   `json:"tool"`
	Subject string   `json:"subject"`
	Entry   string   `json:"entry"`
	Verdict string   `json:"verdict"`
	Rules   []string `json:"rules"`
	Code    string   `json:"code"`
}

// repoRulesWorkspace は共通設定のルールにリポジトリの rules.yml だけを置いたワークスペース。ほかの設定と
// 承認済みチケットは置かない。
func repoRulesWorkspace(t *testing.T) string {
	t.Helper()
	repo := testkit.RepoRoot(t)
	root := testkit.TempRoot(t)
	data, err := os.ReadFile(filepath.Join(repo, ".ccnavi", "config", "common", "rules.yml"))
	if err != nil {
		t.Fatal(err)
	}
	testkit.Put(t, filepath.Join(root, config.DefaultRules), string(data))
	copyDir(t, filepath.Join(repo, ".ccnavi", "scripts"), filepath.Join(root, ".ccnavi", "scripts"))
	testkit.CopyMessages(t, root)
	return root
}

// writtenIDs は記録のルール名（`<相対パス>:<書いた id>`）を書いた id にそろえる。組み込みの名前はそのまま。
func writtenIDs(rules []any) []string {
	out := []string{}
	for _, r := range rules {
		name, _ := r.(string)
		if i := strings.LastIndex(name, ":"); i >= 0 && !strings.HasPrefix(name, "builtin:") {
			name = name[i+1:]
		}
		out = append(out, name)
	}
	return out
}

func TestRepoRulesTable(t *testing.T) {
	rows := golden.Load[[]repoRuleRow](t, "repo_rules")
	root := repoRulesWorkspace(t)
	slashed := filepath.ToSlash(root)
	for _, r := range rows {
		env := baseEnv
		if r.Entry != "" {
			env = with("CCNAVI_ENTRY_PATH", filepath.Join(root, filepath.FromSlash(r.Entry)))
		}
		field := "command"
		if r.Tool != config.ToolBash {
			field = "file_path"
		}
		subject := strings.ReplaceAll(r.Subject, "/repo", slashed)
		a := runGo(t, root, judgeCase{Payload: call(r.Tool, root, map[string]any{field: subject}), Env: env})
		rules, _ := a.Record["rules"].([]any)
		got := writtenIDs(rules)
		decision, _ := a.Record["decision"].(string)
		code, _ := a.Record["code"].(string)
		if decision != r.Verdict || !slices.Equal(got, r.Rules) || (r.Code != "" && code != r.Code) {
			t.Errorf("%s: %s %q (entry=%v): %s %v %s、想定 %s %v %s", r.Case, r.Tool, r.Subject, r.Entry != "",
				decision, got, code, r.Verdict, r.Rules, r.Code)
		}
	}
}
