package handoff

import "testing"

// 取り込めるか（種類があるか）。ccnavi-stage.sh の --commands（stage パッケージ）が、この答えで取り込みの手順を出すかを決める。
func TestImportableAgreesWithTheStageSh(t *testing.T) {
	cases := map[string]bool{
		".ccnavi/config/common/templates/a.yml":             true,
		".ccnavi/messages/a.yml":                            true,
		".ccnavi/messages/sub/a.yml":                        false,
		"projects/p/.ccnavi/messages/a.yml":                 false,
		".ccnavi/config/common/messages/deny.yml":           false,
		".ccnavi/config/project/templates/a.yml":            true,
		".ccnavi/config/project/templates/sub/a.yml":        false,
		".ccnavi/config/common/templates/x/templates/a.yml": false,
		".ccnavi/docs/.md":                                  false,
		"projects/p/.ccnavi/config/project/templates/a.yml": true,
		".ccnavi/config/common/rules.yml":                   true,
		".ccnavi/config/project/risks.yml":                  true,
		"projects/p/.ccnavi/config/project/phases.yml":      true,
		"projects/p/.ccnavi/config/common/rules.yml":        false,
		".ccnavi/docs/a/b.md":                               true,
		".ccnavi/CCNAVI.md":                                 true,
		".ccnavi/scripts/ccnavi-stage.sh":                   true,
		".claude/hooks/lint-go.sh":                          true,
		".claude/settings.json":                             true,
		".claude/settings.local.json":                       true,
		".claude/hooks/a/b/c.py":                            true,
		".ccnavi/scripts/ccnavi-handoff-names.txt":          true,
		".ccnavi/scripts/sub/x.sh":                          true,
		".claude/skills/x/SKILL.md":                         true,
		".claude/skills/x/y/z.md":                           true,
		".claude/skills/x/y/z.txt":                          false,
		"README.md":                                         false,
	}
	for rel, want := range cases {
		if got := KindOf(Key(rel)) != nil; got != want {
			t.Errorf("%s: %v, want %v", rel, got, want)
		}
	}
}
