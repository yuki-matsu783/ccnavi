// Package rules loads the rule set that drives every decision.
//
// Rules live outside the binary so they can be added without a rebuild. A rule
// carries its own message, which is what keeps the guidance from being
// forgotten: adding a rule forces writing what to do instead.
package rules

import (
	"encoding/json"
	"fmt"
	"os"
	"regexp"
	"strings"
)

// Version is the rule-file format this build understands.
const Version = 1

// Set is a whole rule file.
type Set struct {
	Version int    `json:"version"`
	Rules   []Rule `json:"rules"`
}

// Rule pairs what to look for with what to say when it is found.
type Rule struct {
	// ID names the rule in reports so a broken one can be pointed at.
	ID string `json:"id"`
	// Match lists canonical tool names separated by "|", e.g. "Write|Edit".
	Match string `json:"match"`
	// Pattern is the everyday way to say what to look for: "git push *",
	// "*.pem", "secrets/". See translate for the whole syntax.
	Pattern string `json:"pattern,omitempty"`
	// Regex is the way out when a rule genuinely needs one. Rules that reach
	// for it are the ones worth reviewing hardest, so it is a separate field
	// rather than a spelling of Pattern.
	Regex string `json:"regex,omitempty"`
	// Message says why the call is stopped and what to do instead.
	Message string `json:"message"`

	re *regexp.Regexp
}

// Matches reports whether the rule applies to this tool and subject.
func (r *Rule) Matches(tool, subject string) bool {
	if r.re == nil || subject == "" {
		return false
	}
	for _, want := range strings.Split(r.Match, "|") {
		if strings.TrimSpace(want) == tool {
			return r.re.MatchString(subject)
		}
	}
	return false
}

// Severity separates what breaks the guard from what merely weakens it.
type Severity string

const (
	SeverityError Severity = "error"
	SeverityWarn  Severity = "warn"
)

// Problem is one complaint about a rule file, named so it can be fixed.
type Problem struct {
	Severity Severity
	Rule     string
	Detail   string
}

func (p Problem) String() string {
	where := p.Rule
	if where == "" {
		where = "(file)"
	}
	return fmt.Sprintf("%s: %s: %s", p.Severity, where, p.Detail)
}

// Load reads a rule file and compiles it.
//
// A rule that cannot be understood is dropped and reported by name rather than
// skipped in silence, because a rule that disappears quietly is a hole in the
// guard that nobody notices. Rules that did compile are still returned, so a
// single bad rule does not take the whole guard down with it.
func Load(path string) (*Set, []Problem, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, nil, fmt.Errorf("read rules: %w", err)
	}

	var set Set
	if err := json.Unmarshal(raw, &set); err != nil {
		return nil, nil, fmt.Errorf("parse rules %s: %w", path, err)
	}

	problems := set.compile()
	return &set, problems, nil
}

// compile validates every rule and keeps only the usable ones.
func (s *Set) compile() []Problem {
	var problems []Problem

	if s.Version != Version {
		problems = append(problems, Problem{
			Severity: SeverityError,
			Detail: fmt.Sprintf("rule format version %d is not supported (this build reads %d)",
				s.Version, Version),
		})
	}

	kept := s.Rules[:0]
	for i := range s.Rules {
		r := s.Rules[i]
		name := r.ID
		if name == "" {
			name = fmt.Sprintf("rules[%d]", i)
		}

		switch {
		case r.Message == "":
			problems = append(problems, Problem{SeverityError, name,
				"no message: a rule must say what to do instead"})
			continue
		case r.Match == "":
			problems = append(problems, Problem{SeverityError, name, "no match: rule applies to no tool"})
			continue
		case r.Pattern == "" && r.Regex == "":
			problems = append(problems, Problem{SeverityError, name, "no pattern and no regex"})
			continue
		case r.Pattern != "" && r.Regex != "":
			problems = append(problems, Problem{SeverityError, name,
				"both pattern and regex: pick one so it is clear which one decides"})
			continue
		}

		expr := r.Regex
		if expr == "" {
			expr = translate(r.Pattern)
		}

		re, err := regexp.Compile(expr)
		if err != nil {
			problems = append(problems, Problem{SeverityError, name,
				fmt.Sprintf("does not compile: %v", err)})
			continue
		}
		r.re = re
		kept = append(kept, r)
	}
	s.Rules = kept

	return problems
}
