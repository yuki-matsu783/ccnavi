package rules

import (
	"encoding/json"
	"fmt"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/infra/yaml"
)

// 記録からルールの候補を起こすこと（`ccnavi --suggest`、issue #149 の 4）。記録を手で書いたワークスペースに対して
// 道具を動かし、出た候補を読む。候補は `--lint` と `--test-expectations` と同じ検査を通ったものだけで、allow は出さない。

const suggestRules = `version: 1
deny:
  - id: git-push
    match: Bash
    glob: "*git push*"
    message: push しません。ユーザに頼んでください。
allow:
  - id: listing
    match: Bash
    regex: '\bls\b'
`

const gitPushRule = ".ccnavi/config/common/rules.yml:git-push"

type suggestWS struct{ root string }

func newSuggestWS(t testing.TB) suggestWS {
	t.Helper()
	root := base.TempDir(t)
	write(t, base.CommonPath(root, "rules"), suggestRules)
	mkdirs(t, filepath.Join(root, "logs"))
	return suggestWS{root}
}

func logLine(fields map[string]any) string {
	row := map[string]any{"ts": "2026-09-01T00:00:00+00:00", "mode": "enable"}
	for k, v := range fields {
		row[k] = v
	}
	b, _ := json.Marshal(row)
	return string(b)
}

func handover(tool, subject string) string {
	return logLine(map[string]any{"event": "PreToolUse", "tool": tool, "subject": subject, "decision": "handover", "enforced": false, "code": "UNDECLARED"})
}

func denied(subject, rule string) string {
	return logLine(map[string]any{
		"event": "PreToolUse", "tool": "Bash", "subject": subject, "decision": "deny", "enforced": true,
		"code": "DENY_COMMAND_PATTERN", "rules": []string{rule},
	})
}

func (w suggestWS) put(t testing.TB, name string, lines ...string) {
	t.Helper()
	write(t, filepath.Join(w.root, "logs", name), strings.Join(lines, "\n")+"\n")
}

func (w suggestWS) suggest(t testing.TB, extra ...string) run.Result {
	t.Helper()
	return run.Ccnavi(t, append([]string{"--root", w.root, "--suggest"}, extra...), run.Opts{Dir: w.root})
}

// body は `--suggest --json` の出力。
func (w suggestWS) body(t testing.TB) map[string]any {
	t.Helper()
	r := w.suggest(t, "--json")
	if r.Code != 0 {
		t.Fatalf("code = %d: %s", r.Code, r.Stderr)
	}
	var out map[string]any
	if err := json.Unmarshal([]byte(r.Stdout), &out); err != nil {
		t.Fatalf("JSON でない: %v\n%s", err, r.Stdout)
	}
	return out
}

func candidates(body map[string]any) []map[string]any {
	var out []map[string]any
	list, _ := body["candidates"].([]any)
	for _, c := range list {
		m, _ := c.(map[string]any)
		out = append(out, m)
	}
	return out
}

func sub(m map[string]any, key string) map[string]any {
	v, _ := m[key].(map[string]any)
	return v
}

func keysOf(m map[string]any) []string {
	var out []string
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

// yamlKeys は候補の yaml を読んで、その下 key の子のキー。
func yamlKeys(t testing.TB, text, key string) []string {
	t.Helper()
	doc, err := yaml.Load(text)
	if err != nil {
		t.Fatalf("yaml が読めない: %v\n%s", err, text)
	}
	m, _ := doc.(*yaml.Map)
	keys, _ := m.Map(key).StringKeys()
	return keys
}

func TestARepeatedHandoverBecomesAnAskRuleAndRotatedLogsCount(t *testing.T) {
	t.Parallel()
	w := newSuggestWS(t)
	var current, rotated []string
	for i := 0; i < 3; i++ {
		current = append(current, handover("Bash", fmt.Sprintf("npm install p%d", i%2)))
		rotated = append(rotated, handover("Bash", "npm install p0"))
	}
	w.put(t, "decisions.jsonl", current...)
	w.put(t, "decisions.20260901-000000.jsonl", rotated...)
	body := w.body(t)
	if logs, _ := body["logs"].([]any); len(logs) != 2 {
		t.Errorf("logs = %v", body["logs"])
	}
	got := candidates(body)
	if len(got) != 1 {
		t.Fatalf("候補は 1 件のはず: %v", got)
	}
	c := got[0]
	if c["kind"] != "rule" || c["section"] != "ask" || c["count"] != float64(6) {
		t.Errorf("kind / section / count: %v", c)
	}
	if sub(c, "rule")["id"] != "suggest-npm-install" {
		t.Errorf("rule = %v", c["rule"])
	}
	if keys := yamlKeys(t, c["yaml"].(string), "expectations"); !reflect.DeepEqual(keys, []string{"ask"}) {
		t.Errorf("expectations のキー = %v", keys)
	}
}

func TestAFewHandoversAreNotCandidates(t *testing.T) {
	t.Parallel()
	w := newSuggestWS(t)
	var lines []string
	for i := 0; i < 4; i++ {
		lines = append(lines, handover("Bash", "docker run x"))
	}
	w.put(t, "decisions.jsonl", lines...)
	if got := candidates(w.body(t)); len(got) != 0 {
		t.Errorf("候補にしない: %v", got)
	}
}

func TestPathsAreWrittenFromTheRoot(t *testing.T) {
	t.Parallel()
	// パスのツールはディレクトリの下を `{root}` から書き、期待する判定は `/repo` で書く。
	w := newSuggestWS(t)
	var lines []string
	for i := 0; i < 5; i++ {
		lines = append(lines, handover("Write", fmt.Sprintf("%s/docs/n%d.md", w.root, i)))
	}
	w.put(t, "decisions.jsonl", lines...)
	got := candidates(w.body(t))
	if len(got) != 1 {
		t.Fatalf("候補は 1 件のはず: %v", got)
	}
	if sub(got[0], "rule")["glob"] != "{root}/docs/*" {
		t.Errorf("glob = %v", sub(got[0], "rule"))
	}
	exps, _ := got[0]["expectations"].([]any)
	if len(exps) == 0 {
		t.Fatal("expectations が空")
	}
	for _, e := range exps {
		if s, _ := e.(map[string]any)["subject"].(string); !strings.HasPrefix(s, "/repo/docs/") {
			t.Errorf("subject = %q", s)
		}
	}
}

func TestCandidatesThatFailTheExpectationsAreDropped(t *testing.T) {
	t.Parallel()
	// いまのルールで ask にならない形（deny にヒットする）は、検証で除いて数だけ言う。
	w := newSuggestWS(t)
	var lines []string
	for i := 0; i < 5; i++ {
		lines = append(lines, handover("Bash", "git push --force"))
	}
	w.put(t, "decisions.jsonl", lines...)
	body := w.body(t)
	if got := candidates(body); len(got) != 0 || body["dropped"] != float64(1) {
		t.Errorf("候補なし・dropped=1 のはず: %v", body)
	}
}

func TestRepeatedDeniesPointAtTheMessage(t *testing.T) {
	t.Parallel()
	// 同じ呼び出しを繰り返し止めたルールは、文面を見直す候補になる。
	w := newSuggestWS(t)
	w.put(t, "decisions.jsonl",
		denied("git push origin main", gitPushRule),
		denied("git  push 'origin' main", gitPushRule),
		denied(`git push "origin" main`, gitPushRule),
		denied("git push origin x", gitPushRule),
	)
	got := candidates(w.body(t))
	if len(got) != 1 {
		t.Fatalf("候補は 1 件のはず: %v", got)
	}
	c := got[0]
	if c["kind"] != "message" || c["section"] != "deny" || c["id"] != "git-push" {
		t.Errorf("kind / section / id: %v", c)
	}
	if sub(c, "rule")["message"] != "push しません。ユーザに頼んでください。" {
		t.Errorf("message = %v", sub(c, "rule"))
	}
	exps, _ := c["expectations"].([]any)
	if len(exps) != 1 || exps[0].(map[string]any)["subject"] != "git push origin main" {
		t.Errorf("expectations = %v", exps)
	}
}

func TestDeniesFromOutsideTheRulesAreNotCandidates(t *testing.T) {
	t.Parallel()
	// ルールファイルに無い根拠の拒否は、直す文面が無いので候補にしない。
	w := newSuggestWS(t)
	w.put(t, "decisions.jsonl", denied("rm x", "builtin:guard-x"), denied("rm x", "builtin:guard-x"), denied("rm x", "builtin:guard-x"))
	body := w.body(t)
	if got := candidates(body); len(got) != 0 || body["dropped"] != float64(1) {
		t.Errorf("候補なし・dropped=1 のはず: %v", body)
	}
}

func TestNoCandidateIsAnAllow(t *testing.T) {
	t.Parallel()
	w := newSuggestWS(t)
	var lines []string
	for i := 0; i < 5; i++ {
		lines = append(lines, handover("Bash", "npm ci"))
	}
	for i := 0; i < 3; i++ {
		lines = append(lines, denied("git push", gitPushRule))
	}
	w.put(t, "decisions.jsonl", lines...)
	got := candidates(w.body(t))
	if len(got) != 2 {
		t.Fatalf("候補は 2 件のはず: %v", got)
	}
	for _, c := range got {
		if c["section"] != "deny" && c["section"] != "ask" {
			t.Errorf("section = %v", c["section"])
		}
		for _, k := range yamlKeys(t, c["yaml"].(string), "rules") {
			if k == "allow" {
				t.Errorf("allow を出している: %v", c["yaml"])
			}
		}
	}
}

func TestTheTextFormIsYamlDocuments(t *testing.T) {
	t.Parallel()
	// 文字で出すときは、候補ごとに YAML の文書を 1 つずつ並べる。
	w := newSuggestWS(t)
	var lines []string
	for i := 0; i < 5; i++ {
		lines = append(lines, handover("Bash", "npm ci"))
	}
	w.put(t, "decisions.jsonl", lines...)
	r := w.suggest(t)
	if r.Code != 0 {
		t.Fatalf("code = %d: %s", r.Code, r.Stderr)
	}
	// 先頭は # の説明。最初の `---` の次から、次の `---` までが 1 件目の文書。
	_, afterMark, _ := strings.Cut(r.Stdout, "\n---\n")
	first, _, _ := strings.Cut(afterMark, "\n---")
	doc, err := yaml.Load(first)
	if err != nil {
		t.Fatalf("最初の文書が読めない: %v\n%s", err, r.Stdout)
	}
	asks, _ := doc.(*yaml.Map).Map("rules").List("ask")
	if len(asks) == 0 || asks[0].(*yaml.Map).Text("id") != "suggest-npm-ci" {
		t.Errorf("rules.ask[0].id = suggest-npm-ci のはず:\n%s", r.Stdout)
	}
}

func TestTheExtensionFixtureHasTheSameKeys(t *testing.T) {
	t.Parallel()
	// VS Code 拡張が読む例（test/fixtures/suggest.json）は、いまの出力と同じキーを持つ。形を変えたらこの例も書き直す。
	w := newSuggestWS(t)
	var lines []string
	for i := 0; i < 5; i++ {
		lines = append(lines, handover("Bash", "npm ci"))
	}
	for i := 0; i < 3; i++ {
		lines = append(lines, denied("git push", gitPushRule))
	}
	w.put(t, "decisions.jsonl", lines...)
	body := w.body(t)
	var example map[string]any
	path := filepath.Join(base.Root(), "extensions", "vscode", "ccnavi-board", "test", "fixtures", "suggest.json")
	if err := json.Unmarshal([]byte(read(t, path)), &example); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(keysOf(body), keysOf(example)) || body["version"] != example["version"] {
		t.Errorf("キー: %v / %v, version: %v / %v", keysOf(body), keysOf(example), body["version"], example["version"])
	}
	got, want := candidates(body), candidates(example)
	if len(got) != len(want) {
		t.Fatalf("候補の数: %d / %d", len(got), len(want))
	}
	for i := range got {
		if !reflect.DeepEqual(keysOf(got[i]), keysOf(want[i])) {
			t.Errorf("候補 %d のキー: %v / %v", i, keysOf(got[i]), keysOf(want[i]))
		}
		ge, _ := got[i]["expectations"].([]any)
		we, _ := want[i]["expectations"].([]any)
		if len(ge) == 0 || len(we) == 0 || !reflect.DeepEqual(keysOf(ge[0].(map[string]any)), keysOf(we[0].(map[string]any))) {
			t.Errorf("候補 %d の expectations のキー: %v / %v", i, ge, we)
		}
	}
}

func TestWithoutALogItSaysSoAndExitsZero(t *testing.T) {
	t.Parallel()
	r := newSuggestWS(t).suggest(t)
	if r.Code != 0 {
		t.Errorf("code = %d", r.Code)
	}
	mustHave(t, r.Stdout, "記録がありません")
}
