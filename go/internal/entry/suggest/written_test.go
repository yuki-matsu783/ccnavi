package suggest

import (
	"testing"

	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/yaml"
	"ccnavi/internal/infra/yamljson"
	"ccnavi/internal/policy/rules"
)

func buildRule(t *testing.T, raw any) *rules.Rule {
	t.Helper()
	m := &yaml.Map{}
	m.Put("version", 1)
	m.Put("deny", []any{raw})
	rs, problems := rules.Parse(m, "/w")
	for _, p := range problems {
		if p.Severity == rules.Error {
			t.Fatalf("組み上がらない: %v", p)
		}
	}
	return rs.Deny[0]
}

// 提案が書き戻す形（writtenRule）は、読み直すと同じ定義になる（when と except が残る）。
func TestWrittenRuleRoundTrips(t *testing.T) {
	for _, text := range []string{
		"id: root\nmatch: Write\nglob: '{root}/*'\nexcept:\n  - glob: '*.md'\n  - regex: '\\.txt$'\nmessage: m\n",
		"id: vault\nmatch: Write\nmessage: m\nwhen:\n  any:\n    - glob: '*.pem'\n    - not:\n        glob: '*.md'\n" +
			"except:\n  - glob: '*/fixtures/*'\nadditionalContext: c\nevery: 2\n",
	} {
		data, err := yaml.Load(text)
		if err != nil {
			t.Fatal(err)
		}
		rule := buildRule(t, data)
		written := writtenRule(rule)
		again := buildRule(t, yamljson.FromJSON(written))
		if again.Key() != rule.Key() {
			t.Errorf("読み直すと違う定義になる: %s", dump(written))
		}
		if rule.When != nil {
			if _, ok := written.Get("glob"); ok {
				t.Errorf("when のルールに glob が付く: %s", dump(written))
			}
		}
	}
}

func dump(o *pyjson.Object) string {
	s, _ := pyjson.Dumps(o, pyjson.Options{})
	return s
}
