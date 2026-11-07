package diagnose

import (
	"strings"
	"testing"

	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/yaml"
	"ccnavi/internal/policy/rules"
)

func TestRuleFormOfWhen(t *testing.T) {
	data, err := yaml.Load("version: 1\ndeny:\n  - id: vault\n    match: Write\n    message: m\n    when:\n" +
		"      any:\n        - glob: '*.pem'\n        - all:\n            - glob: '*/secrets/*'\n            - not:\n                glob: '*.md'\n")
	if err != nil {
		t.Fatal(err)
	}
	rs, _ := rules.Parse(data.(*yaml.Map), "/w")
	f := ruleForm(rs.Deny[0])
	if f.Kind != rules.WhenKey {
		t.Fatalf("kind %q", f.Kind)
	}
	// kind が when を言うので、written に `when: ` は重ねない（画面の「when when:」を避ける）
	if !strings.HasPrefix(f.Written, "any(") {
		t.Errorf("written %q", f.Written)
	}
	got, _ := pyjson.Dumps(f.When, pyjson.Options{})
	want := `{"any": [{"glob": "*.pem"}, {"all": [{"glob": "*/secrets/*"}, {"not": {"glob": "*.md"}}]}]}`
	if got != want {
		t.Errorf("when %s", got)
	}
}
