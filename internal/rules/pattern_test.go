package rules

import (
	"regexp"
	"testing"
)

// The friendly syntax only earns its place if what it does is obvious from the
// pattern alone. These cases are the ones a rule author would guess at.
func TestPatternMatches(t *testing.T) {
	cases := []struct {
		pattern string
		hit     []string
		miss    []string
	}{
		{
			pattern: "git push",
			hit: []string{
				"git push",
				"git push origin main",
				"git   push",
				"/usr/bin/git push",
				"cd repo && git push",
			},
			miss: []string{
				"git pushed",
				"git commit -m push",
				"legit push",
			},
		},
		{
			// Trailing " *" reads as "with anything after", and a bare command
			// has to keep matching or the rule would miss the plainest case.
			pattern: "sed *",
			hit:     []string{"sed", "sed -i s/a/b/ f.txt"},
			miss:    []string{"sedate", "used"},
		},
		{
			// A pattern is searched for anywhere in the subject, so this also
			// catches a copy like "key.pem.bak". For a guard that is the right
			// way to be wrong: the copy holds the same key.
			pattern: "*.pem",
			hit:     []string{"/etc/ssl/key.pem", "key.pem", "key.pem.bak"},
			miss:    []string{"pem"},
		},
		{
			pattern: ".env",
			hit:     []string{"/app/.env", "/app/.env.local", ".env"},
			miss:    []string{"/app/.environment", "env"},
		},
		{
			// A rule written with forward slashes has to hold against the
			// backslashes a Windows tool call carries.
			pattern: "secrets/",
			hit: []string{
				"app/secrets/db.yml",
				`C:\repo\secrets\db.yml`,
				"secrets/",
			},
			miss: []string{"mysecrets/db.yml", "secrets"},
		},
		{
			pattern: ".claude/ccnavi/*",
			hit: []string{
				`C:\Users\me\repo\.claude\ccnavi\rules.json`,
				"/home/me/repo/.claude/ccnavi/rules.json",
			},
			miss: []string{`C:\Users\me\repo\.claude\settings.json`},
		},
		{
			pattern: "rm -rf ?",
			hit:     []string{"rm -rf x"},
			miss:    []string{"rm -rf"},
		},
	}

	for _, c := range cases {
		t.Run(c.pattern, func(t *testing.T) {
			re, err := regexp.Compile(translate(c.pattern))
			if err != nil {
				t.Fatalf("%q translated to something that does not compile: %v", c.pattern, err)
			}
			for _, s := range c.hit {
				if !re.MatchString(s) {
					t.Errorf("%q should match %q (as %s)", c.pattern, s, re)
				}
			}
			for _, s := range c.miss {
				if re.MatchString(s) {
					t.Errorf("%q should not match %q (as %s)", c.pattern, s, re)
				}
			}
		})
	}
}
