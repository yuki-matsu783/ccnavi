package core

import (
	"encoding/json"
	"path/filepath"
	"strings"
	"testing"
)

// `--test --json` と `--test-expectations`（試験の JSON）の受入テスト。VS Code 拡張のルール管理画面が読む形を、
// `--test` と同じ判定で組んでいることを確かめる。標準出力と終了コードだけを見る。
//
//  1. `--test --json` が判定・根拠・ヒットしたルール・返る文面を 1 つの JSON で出す
//  2. 判定が対象を取り出せないツールは `known` が偽で出る
//  3. `--test-expectations` が期待する判定をタイプの期待と突き合わせ、食い違いを数える。文字で出すときは食い違いがあれば終了コード 1、JSON では常に 0
//  4. 拡張側のフィクスチャ（extensions/vscode/ccnavi-board/test/fixtures/test.json と expectations.json）と同じ形である

var testJSONRules = map[string]any{
	"version": 1,
	"deny":    []rule{{"id": "git-push", "match": "Bash", "glob": "*git push*", "message": "push はユーザが行う。ラッパに依頼する。"}},
	"ask":     []rule{{"id": "vault", "match": "Read", "glob": "*/vault/*", "message": "秘密の置き場。読む前に確認する。"}},
	"allow":   []rule{{"id": "inspection", "match": "Bash", "regex": `^(ls|cat)\b`, "message": "見るだけ"}},
}

const testJSONExpectations = `deny:
  - tool: Bash
    subject: "cd /repo && git push"
    why: push はユーザが行う
ask:
  - tool: Read
    subject: "/repo/vault/token"
    why: 秘密の置き場
allow:
  - tool: Bash
    subject: "ls -la"
    why: 見るだけ
  - tool: Bash
    subject: "git push --force"
    why: 食い違いの期待する判定。deny にヒットするので allow の期待とずれる
  - tool: Bash
    subject: "# comment only"
    why: 何も走らないので判定に入らない
`

type testJSONEnv struct {
	t                  *testing.T
	dir, root          string
	rules, expectation string
}

func newTestJSONEnv(t *testing.T) *testJSONEnv {
	dir := emptyRoot(t)
	return &testJSONEnv{
		t: t, dir: dir, root: filepath.ToSlash(dir),
		rules:       writeFile(t, dir, "rules.yml", mustJSON(t, testJSONRules)),
		expectation: writeFile(t, dir, "expectations.yml", testJSONExpectations),
	}
}

// ccnavi は見るのをルールだけにする（承認済みチケットと state は外し、記録も残さない）。
func (e *testJSONEnv) ccnavi(rulesPath string, args ...string) (code int, stdout, stderr string) {
	r := ccnavi(e.t, e.root, nil, append([]string{"--rules", rulesPath}, args...)...)
	return r.Code, r.Stdout, r.Stderr
}

func decode(t *testing.T, text string) map[string]any {
	t.Helper()
	var m map[string]any
	if err := json.Unmarshal([]byte(text), &m); err != nil {
		t.Fatalf("JSON でない: %v\n%s", err, text)
	}
	return m
}

// 試験は判定とヒットしたルールの一覧で共通レイヤーを 2 度読み、期待は 1 件ずつ判定する。どちらでも、同じ苦情を 2 度出さない。
func TestTestRuleProblemsAreSaidOnce(t *testing.T) {
	t.Parallel()
	e := newTestJSONEnv(t)
	silent := rule{"id": "silent", "match": "Bash", "glob": "*nothing matches*"}
	body := map[string]any{}
	for k, v := range testJSONRules {
		body[k] = v
	}
	body["deny"] = append(append([]rule{}, testJSONRules["deny"].([]rule)...), silent)
	path := writeFile(t, e.dir, "silent.yml", mustJSON(t, body))
	for _, args := range [][]string{
		{"--test", "Bash", "cd /repo && git push"},
		{"--test", "Bash", "cd /repo && git push", "--json"},
		{"--test-expectations", e.expectation},
		{"--test-expectations", e.expectation, "--json"},
	} {
		_, _, stderr := e.ccnavi(path, args...)
		if n := strings.Count(stderr, "deny:silent"); n != 1 {
			t.Errorf("%v: deny:silent が %d 回: %s", args, n, stderr)
		}
	}
}

func TestTestJSONOneCallCarriesVerdictRulesAndResponse(t *testing.T) {
	t.Parallel()
	e := newTestJSONEnv(t)
	code, stdout, stderr := e.ccnavi(e.rules, "--test", "Bash", "cd /repo && git push", "--json")
	if code != 0 {
		t.Fatalf("code = %d: %s", code, stderr)
	}
	body := decode(t, stdout)
	hit := body["rules"].([]any)[0].(map[string]any)
	if body["version"] != 1.0 || body["known"] != true || body["verdict"] != "deny" || body["code"] != "DENY_COMMAND_PATTERN" ||
		hit["id"] != "rules.yml:git-push" || hit["section"] != "deny" || hit["kind"] != "glob" || hit["source"] != "file" ||
		hit["written"] != "*git push*" || hit["pattern"] == "" {
		t.Errorf("body = %v", body)
	}
	mustContain(t, "response", body["response"].(string), "push はユーザが行う")

	// 文字の出力と同じ判定であること。
	_, plain, _ := e.ccnavi(e.rules, "--test", "Bash", "cd /repo && git push")
	if !strings.HasPrefix(plain, "verdict: deny (DENY_COMMAND_PATTERN)\n") {
		t.Errorf("plain = %q", plain)
	}

	// `--test` は 2 語を取る。直後に `--json` を書いても TOOL として取らない。
	for _, args := range [][]string{{"--test", "--json", "Bash", "cd /repo && git push"}, {"--test", "Bash", "--json", "cd /repo && git push"}} {
		_, out, _ := e.ccnavi(e.rules, args...)
		again := decode(t, out)
		if again["tool"] != "Bash" || again["verdict"] != "deny" {
			t.Errorf("%v: %v", args, again)
		}
	}
	// 判定が対象を取り出せないツールは known が偽で、判定しない。
	code, out, stderr := e.ccnavi(e.rules, "--test", "WebSearch", "ccnavi", "--json")
	unknown := decode(t, out)
	if code != 0 || unknown["known"] != false || unknown["verdict"] != "" || len(unknown["rules"].([]any)) != 0 {
		t.Errorf("code = %d, body = %v, stderr = %s", code, unknown, stderr)
	}

	checkBoardFixture(t, "test.json", stdout, e.root, "rules.0")
}

// Claude Code の権限ルールの名前（括弧の中を除いたもの）で試せる。PowerShell はコマンド、Grep / Glob は探す場所のパス、
// Skill はスキル名、WebFetch は URL が対象。欄の名前を取り違えると known でも対象が空になり、ルールにヒットしないまま通る。
func TestTestToolsNamedLikePermissionRulesAreJudged(t *testing.T) {
	t.Parallel()
	e := newTestJSONEnv(t)
	path := writeFile(t, e.dir, "named.yml", mustJSON(t, map[string]any{
		"version": 1,
		"deny": []rule{
			{"id": "ps", "match": "PowerShell", "glob": "*Remove-Item*", "message": "削除しない"},
			{"id": "search", "match": "Grep|Glob", "glob": "*/vault/*", "message": "秘密の置き場は探さない"},
			{"id": "skill", "match": "Skill", "glob": "deploy*", "message": "配布はユーザが行う"},
			{"id": "fetch", "match": "WebFetch", "glob": "*://example.com/*", "message": "外"},
		},
		"allow": []rule{{"id": "rest", "match": "Bash", "glob": "*"}},
	}))
	for _, c := range [][3]string{
		{"PowerShell", "Remove-Item -Recurse x", "ps"},
		{"Grep", "/repo/vault/keys", "search"},
		{"Glob", "/repo/vault/tokens", "search"},
		{"Skill", "deploy prod", "skill"},
		{"WebFetch", "https://example.com/a", "fetch"},
	} {
		code, out, stderr := e.ccnavi(path, "--test", c[0], c[1], "--json")
		body := decode(t, out)
		hits := body["rules"].([]any)
		if code != 0 || body["known"] != true || body["verdict"] != "deny" || len(hits) != 1 || hits[0].(map[string]any)["id"] != "named.yml:"+c[2] {
			t.Errorf("%s: code = %d, body = %v, stderr = %s", c[0], code, body, stderr)
		}
	}
}

func TestTestExpectationsCountMismatchesAndSkips(t *testing.T) {
	t.Parallel()
	e := newTestJSONEnv(t)
	code, stdout, stderr := e.ccnavi(e.rules, "--test-expectations", e.expectation, "--json")
	if code != 0 {
		t.Fatalf("code = %d: %s", code, stderr)
	}
	body := decode(t, stdout)
	counts := body["counts"].(map[string]any)
	want := map[string][2]float64{"deny": {1, 1}, "ask": {1, 1}, "allow": {2, 3}}
	for section, w := range want {
		c := counts[section].(map[string]any)
		if c["ok"] != w[0] || c["total"] != w[1] {
			t.Errorf("counts[%s] = %v, want %v", section, c, w)
		}
	}
	if body["version"] != 1.0 || body["mismatches"] != 1.0 || body["skipped"] != 1.0 {
		t.Errorf("body = %v", body)
	}
	bySubject := map[string]map[string]any{}
	for _, x := range body["expectations"].([]any) {
		m := x.(map[string]any)
		bySubject[m["subject"].(string)] = m
	}
	wrong := bySubject["git push --force"]
	if wrong["ok"] != false || wrong["expected"] != "allow" || wrong["verdict"] != "deny" ||
		wrong["rules"].([]any)[0].(map[string]any)["id"] != "rules.yml:git-push" {
		t.Errorf("wrong = %v", wrong)
	}
	if skipped := bySubject["# comment only"]; skipped["ok"] != true || skipped["skipped"] != true {
		t.Errorf("skipped = %v", skipped)
	}
	// `/repo` は走らせた場所に読み替わる。
	if got := bySubject["/repo/vault/token"]["resolved_subject"]; got != e.root+"/vault/token" {
		t.Errorf("resolved_subject = %v", got)
	}

	// 文字で出すときは、食い違いがあれば終了コード 1。
	code, plain, _ := e.ccnavi(e.rules, "--test-expectations", e.expectation)
	if code != 1 {
		t.Errorf("code = %d: %s", code, plain)
	}
	mustContain(t, "plain", plain, "食い違い: allow のはずが deny", "食い違い 1 件、判定に入らなかったもの 1 件")

	checkBoardFixture(t, "expectations.json", stdout, e.root, "expectations.0", "expectations.0.rules.0")
}

func TestTestUnreadableExpectationsFailLoudly(t *testing.T) {
	t.Parallel()
	e := newTestJSONEnv(t)
	code, _, stderr := e.ccnavi(e.rules, "--test-expectations", filepath.Join(e.dir, "missing.yml"))
	if code != 1 || !strings.Contains(stderr, "期待する判定を読めない") {
		t.Errorf("code = %d, stderr = %s", code, stderr)
	}
}
