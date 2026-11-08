package candidates

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/config/phases"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/testkit"
)

// 承認で親の phases:（計画が使う定義のコピー）を確かめる決まりを、見本の表（tests/fixtures/parent-phases.json）で
// 固定する。errors と warns は、その重さの苦情の文に含まれる語句。空なら、その重さの苦情は 1 つも出ない。
func TestParentPhasesTable(t *testing.T) {
	data, err := os.ReadFile(filepath.Join(testkit.RepoRoot(t), "tests", "fixtures", "parent-phases.json"))
	if err != nil {
		t.Fatal(err)
	}
	var table struct {
		PhasesYML json.RawMessage `json:"phases_yml"`
		Cases     []struct {
			Name   string          `json:"name"`
			Plan   json.RawMessage `json:"plan"`
			Phases json.RawMessage `json:"phases"`
			Errors []string        `json:"errors"`
			Warns  []string        `json:"warns"`
		} `json:"cases"`
	}
	if err := json.Unmarshal(data, &table); err != nil {
		t.Fatal(err)
	}
	// JSON は YAML の流れ形式としてそのまま読める。
	config, problems := phases.Parse("version: 1\nphases: "+string(table.PhasesYML)+"\n", "(phases)")
	if len(problems) != 0 {
		t.Fatalf("phases.yml: %v", problems)
	}
	if len(table.Cases) == 0 {
		t.Fatal("見本が空")
	}
	for _, c := range table.Cases {
		front := "---\nversion: 1\nticket: i0001\n"
		if string(c.Phases) != "null" && len(c.Phases) > 0 {
			front += "phases: " + string(c.Phases) + "\n"
		}
		front += "plan: " + string(c.Plan) + "\ntitle: 親\nallow: [{match: Write|Edit, glob: \"src/*\"}]\n---\n本文\n"
		parent, problems := ticket.Parse(front)
		if parent == nil {
			t.Errorf("%s: 読めない %v", c.Name, problems)
			continue
		}
		found := PlanProblems(parent, config, nil, false)
		for _, want := range []struct {
			severity rules.Severity
			phrases  []string
		}{{rules.Error, c.Errors}, {rules.Warn, c.Warns}} {
			var said []string
			for _, p := range found {
				if p.Severity == want.severity {
					said = append(said, p.Detail)
				}
			}
			if len(want.phrases) == 0 && len(said) != 0 {
				t.Errorf("%s: %s が出た %q", c.Name, want.severity, said)
			}
			for _, phrase := range want.phrases {
				if !strings.Contains(strings.Join(said, "\n"), phrase) {
					t.Errorf("%s: %s に %q が無い %q", c.Name, want.severity, phrase, said)
				}
			}
		}
	}
}
