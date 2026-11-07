package learning

import (
	"regexp"
	"slices"
	"sort"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/shell"
	"ccnavi/internal/records/audit"
)

// minRepeated は「繰り返した止め」に入れる回数の下限。
const minRepeated = 2

var (
	assignment = regexp.MustCompile(`(?s)\A[A-Za-z_][A-Za-z0-9_]*=.*\z`)
	plainWord  = regexp.MustCompile(`\A[A-Za-z0-9._+-]+\z`)
	pathSep    = regexp.MustCompile(`[/\\]`)
)

const envCommand = "env"

// commandWord は実行の文字列から、先頭の環境変数の代入と `env` を飛ばした最初の語の名前。
// 記録に残すので、名前の形でないもの（秘密を含みうる）は空にする。
func commandWord(subject string) string {
	tokens, err := shell.Split(subject)
	if err != nil {
		return ""
	}
	i := commandIndex(tokens)
	if i >= len(tokens) {
		return ""
	}
	parts := pathSep.Split(tokens[i], -1)
	word := parts[len(parts)-1]
	if !plainWord.MatchString(word) {
		return ""
	}
	return word
}

// commandIndex は先頭の代入と `env`（とその後の `-` で始まる語）を飛ばした位置。
func commandIndex(tokens []string) int {
	afterEnv := false
	for i, tok := range tokens {
		switch {
		case assignment.MatchString(tok):
		case tok == envCommand:
			afterEnv = true
		case afterEnv && strings.HasPrefix(tok, "-"):
		default:
			return i
		}
	}
	return len(tokens)
}

// digest の件数を数える判定。並びは書き出す順。
var digestKinds = []struct {
	name     string
	decision audit.Decision
}{{"deny", audit.Deny}, {"ask", audit.Ask}, {"nudge", audit.Nudge}, {"handover", audit.Handover}}

// buildDigest はログの記録から、判定の件数と繰り返した止めを作る。treeName が nil でなければその木の記録だけ。
// repeated は、強制された deny のうち（先頭のルート・ツール・コマンドの語）が同じものを回数の多い順に並べ、
// 2 回以上のものだけ入れる。
func buildDigest(records []*pyjson.Object, recorded bool, treeName *string) *pyjson.Object {
	if treeName != nil {
		var kept []*pyjson.Object
		for _, r := range records {
			if v, ok := r.Get("tree"); (ok && v == any(*treeName)) || (!ok && *treeName == "") {
				kept = append(kept, r)
			}
		}
		records = kept
	}
	is := func(r *pyjson.Object, d audit.Decision) bool { return r.Value("decision") == any(string(d)) }
	counts := pyjson.NewObject()
	for _, k := range digestKinds {
		n := 0
		for _, r := range records {
			if is(r, k.decision) {
				n++
			}
		}
		counts.Set(k.name, n)
	}
	type key struct{ rule, tool, word string }
	tally := map[key]int{}
	for _, r := range records {
		if !is(r, audit.Deny) || !pyfmt.Truthy(r.Value("enforced")) {
			continue
		}
		rules, ok := r.Value("rules").([]any)
		if !ok || len(rules) == 0 {
			continue
		}
		rule, ok := rules[0].(string)
		if !ok {
			continue
		}
		tool := ""
		if v := r.Value("tool"); pyfmt.Truthy(v) {
			tool = pyfmt.Str(v)
		}
		word := ""
		if slices.Contains(config.ShellTools, tool) {
			if subject, ok := r.Value("subject").(string); ok {
				word = commandWord(subject)
			}
		}
		tally[key{rule, tool, word}]++
	}
	keys := make([]key, 0, len(tally))
	for k := range tally {
		keys = append(keys, k)
	}
	sort.Slice(keys, func(i, j int) bool {
		a, b := keys[i], keys[j]
		if tally[a] != tally[b] {
			return tally[a] > tally[b]
		}
		if a.rule != b.rule {
			return a.rule < b.rule
		}
		if a.tool != b.tool {
			return a.tool < b.tool
		}
		return a.word < b.word
	})
	repeated := []any{}
	for _, k := range keys {
		if n := tally[k]; n >= minRepeated {
			repeated = append(repeated, pyjson.NewObject("rule", k.rule, "tool", k.tool, "word", k.word, "count", n))
		}
	}
	return pyjson.NewObject("recorded", recorded, "counts", counts, "repeated", repeated)
}
