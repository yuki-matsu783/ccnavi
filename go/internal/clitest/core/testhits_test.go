package core

import (
	"encoding/json"
	"path/filepath"
	"regexp"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
)

// `--test` が見せる根拠と payload が、hook の判定と同じものであることの受入テスト。
//
//  1. ヒットしたルール（rules）は判定が適用したルールそのもので、ルールファイルを別に読み直さない。組み込みは outside で、式は短く切る。
//     レイヤーの和で重複として破棄される側のプロジェクトの定義も、同じ id のルールも位置で引ける
//  2. payload の欄（--cwd・--permission-mode・--agent-id）が判定に渡り、使った payload が出る。知らない権限モードと無いディレクトリは断る。
//     切り替えの環境変数は hook と同じく解いてから判定に渡す
//  3. `--explain` の「どのルールも言及しない呼び出し」が、判定と同じ関数で権限モードごとの結末を出す

var allowAll = map[string]any{"version": 1, "allow": []rule{{"id": "all", "match": "Bash", "glob": "*"}}}

var schemaRules = map[string]any{"version": 1, "deny": []rule{{
	"id": "schema", "match": "Write|Edit", "glob": "*/schema/*", "message": "schema は手で書かない。マイグレーションを書く。",
}}}

type hitsEnv struct {
	t     *testing.T
	root  string
	rules string
}

func newHitsEnv(t *testing.T) *hitsEnv {
	root := filepath.ToSlash(emptyRoot(t))
	return &hitsEnv{t, root, writeFile(t, root, "rules.yml", mustJSON(t, allowAll))}
}

// ccnavi はルールを固定し、ログと state を切って起こす。rules が空なら e.rules。
func (e *hitsEnv) ccnavi(rules string, env map[string]string, args ...string) run.Result {
	e.t.Helper()
	if rules == "" {
		rules = e.rules
	}
	return run.Ccnavi(e.t, append([]string{"--root", e.root, "--rules", rules, "--log", "", "--state", ""}, args...),
		run.Opts{Dir: base.Root(), Env: env})
}

func (e *hitsEnv) body(rules string, env map[string]string, args ...string) map[string]any {
	e.t.Helper()
	done := e.ccnavi(rules, env, append(append([]string{"--test"}, args...), "--json")...)
	if done.Code != 0 {
		e.t.Fatalf("code = %d: %s", done.Code, done.Stderr)
	}
	return decode(e.t, done.Stdout)
}

func firstHit(body map[string]any) map[string]any { return body["rules"].([]any)[0].(map[string]any) }

// 組み込み（設定ファイルの保護・チケットの状態の保護）はルールファイルの外にある根拠で、式は短く切る。
func TestHitsBuiltinGuardsAreOutsideWithAShortForm(t *testing.T) {
	t.Parallel()
	e := newHitsEnv(t)
	mkdir(t, filepath.Join(e.root, ".ccnavi", "config", "common"))
	body := e.body("", nil, "Bash", "echo x > .ccnavi/config/common/rules.yml")
	hit := firstHit(body)
	if body["verdict"] != "deny" || hit["id"] != "builtin:guard-setting-files" || hit["source"] != "outside" || hit["section"] != "deny" {
		t.Errorf("body = %v", body)
	}
	// 組み込みの式は 2 万字を超える。そのまま出さず、切ったことと全体の字数を添える。
	if n := len([]rune(hit["written"].(string))); n >= 300 || !regexp.MustCompile(`…（全 \d+ 字）$`).MatchString(hit["written"].(string)) {
		t.Errorf("written が切れていない (%d 字)", n)
	}
	if n := len([]rune(hit["pattern"].(string))); n >= 300 {
		t.Errorf("pattern が長い (%d 字)", n)
	}
	plain := e.ccnavi("", nil, "--test", "Bash", "echo x > .ccnavi/config/common/rules.yml").Stdout
	mustContain(t, "plain", plain, "deny:builtin:guard-setting-files（ルールファイルの外にある根拠）")
	for _, line := range strings.Split(plain, "\n") {
		if len([]rune(line)) >= 1000 {
			t.Errorf("1000 字以上の行がある: %.80s…", line)
		}
	}
	// チケットの状態の保護も、ルールファイルの外にある根拠。
	body = e.body("", nil, "Write", e.root+"/wip/proposals/review/x.md")
	byID := map[string]map[string]any{}
	for _, h := range body["rules"].([]any) {
		byID[h.(map[string]any)["id"].(string)] = h.(map[string]any)
	}
	if body["verdict"] != "deny" || byID["builtin:ticket-state"]["source"] != "outside" || byID["builtin:ticket-state"]["section"] != "deny" {
		t.Errorf("body = %v", body)
	}
}

func TestHitsFallbackDefaultsAreOutside(t *testing.T) {
	t.Parallel()
	e := newHitsEnv(t)
	broken := writeFile(t, e.root, "broken.yml", "deny: [\n")
	body := e.body(broken, nil, "Bash", "git push")
	hits := body["rules"].([]any)
	if body["verdict"] != "deny" || body["fallback"] == "" || len(hits) == 0 {
		t.Fatalf("body = %v", body)
	}
	for _, h := range hits {
		if h.(map[string]any)["source"] != "outside" {
			t.Errorf("hit = %v", h)
		}
	}
	// 期待の食い違いの行は、ルールファイルのルールだけを「ヒットしたルール」と言う。
	expectations := writeFile(t, e.root, "expectations.yml", "allow:\n  - tool: Bash\n    subject: \"git push\"\n    why: 食い違い\n")
	plain := e.ccnavi(broken, nil, "--test-expectations", expectations)
	if plain.Code != 1 {
		t.Errorf("code = %d: %s", plain.Code, plain.Stdout)
	}
	mustNotContain(t, "plain", plain.Stdout, "ヒットしたルール")
}

// id は必須。記録の名前はルールファイルの相対パスと書いた id の形になる。
func TestHitsRuleHitIsNamedByFileAndID(t *testing.T) {
	t.Parallel()
	e := newHitsEnv(t)
	rules := writeFile(t, e.root, "noid.yml", mustJSON(t, map[string]any{"version": 1, "deny": []rule{
		{"id": "rm-build", "match": "Bash", "glob": "*rm -rf*", "message": "削除しない"},
	}}))
	body := e.body(rules, nil, "Bash", "rm -rf build")
	hit := firstHit(body)
	if body["verdict"] != "deny" || hit["id"] != "noid.yml:rm-build" || hit["source"] != "file" || hit["section"] != "deny" ||
		hit["kind"] != "glob" || hit["written"] != "*rm -rf*" {
		t.Errorf("body = %v", body)
	}
}

// レイヤーの和では後ろのプロジェクトの同じ定義が重複として破棄される。判定は行き先のレイヤーで適用するので、
// 破棄された側のプロジェクトにもヒットする。そのルールが見えること。
func TestHitsSameDefinitionInTwoProjectsShowsTheOneThatHit(t *testing.T) {
	t.Parallel()
	root := filepath.ToSlash(emptyRoot(t))
	git(t, root, "init", "--quiet", "-b", "main")
	writeFile(t, root, ".gitignore", "/projects/\n")
	writeFile(t, root, base.CommonRelpath("rules"), `{"version": 1}`)
	for _, name := range []string{"app", "lib"} {
		project := filepath.Join(root, "projects", name)
		git(t, mkdir(t, project), "init", "--quiet", "-b", "main")
		writeFile(t, project, ".ccnavi/config/project/rules.yml", mustJSON(t, schemaRules))
	}
	for _, name := range []string{"app", "lib"} {
		done := run.Ccnavi(t, []string{"--root", root, "--log", "", "--state", "", "--test", "Write", root + "/projects/" + name + "/schema/a.sql", "--json"},
			run.Opts{Dir: base.Root()})
		body := decode(t, done.Stdout)
		hit := firstHit(body)
		if body["verdict"] != "deny" || hit["id"] != "projects/"+name+"/.ccnavi/config/project/rules.yml:schema" ||
			hit["source"] != "file" || hit["section"] != "deny" || hit["written"] != "*/schema/*" {
			t.Errorf("%s: body = %v, stderr = %s", name, body, done.Stderr)
		}
	}
}

// payload の欄が判定に渡り、使った payload が出る。指定した欄だけが JSON を変える。
func TestHitsPayloadIsShownAndOnlyGivenKeysChangeTheJSON(t *testing.T) {
	t.Parallel()
	e := newHitsEnv(t)
	if _, has := e.body("", nil, "Bash", "unknowncmd")["payload"]; has {
		t.Error("指定が無いのに payload が出た")
	}
	plain := e.ccnavi("", nil, "--test", "Bash", "unknowncmd").Stdout
	mustContain(t, "plain", plain, "payload: cwd="+e.root+"  permission_mode=(無し)  agent_id=(無し)")

	sub := filepath.Join(e.root, "sub")
	mkdir(t, sub)
	body := e.body("", nil, "Bash", "unknowncmd", "--cwd", sub, "--permission-mode", "default", "--agent-id", "a1")
	got, _ := json.Marshal(body["payload"])
	want, _ := json.Marshal(map[string]string{"cwd": sub, "permission_mode": "default", "agent_id": "a1"})
	if string(got) != string(want) {
		t.Errorf("payload = %s, want %s", got, want)
	}
}

func TestHitsPermissionModeReachesTheJudgment(t *testing.T) {
	t.Parallel()
	e := newHitsEnv(t)
	rules := writeFile(t, e.root, "empty.yml", `{"version": 1}`)
	cases := []struct {
		extra   []string
		env     map[string]string
		verdict string
	}{
		{nil, nil, "ask"},
		{[]string{"--permission-mode", "default"}, nil, "handover"},
		{[]string{"--permission-mode", "dontAsk"}, nil, "deny"},
		// 切り替えの環境変数は、hook と同じく空白と大文字を落として読んでから判定に渡す。
		{[]string{"--permission-mode", "dontAsk"}, map[string]string{"CCNAVI_GUARD_UNWATCHED": " Disable"}, "handover"},
	}
	for _, c := range cases {
		body := e.body(rules, c.env, append([]string{"Bash", "unknowncmd"}, c.extra...)...)
		if body["verdict"] != c.verdict || body["code"] != "UNDECLARED" {
			t.Errorf("%v %v: verdict = %v, code = %v", c.extra, c.env, body["verdict"], body["code"])
		}
	}
}

func TestHitsAgentIDReachesTheJudgment(t *testing.T) {
	t.Parallel()
	e := newHitsEnv(t)
	command := "sh .ccnavi/scripts/ccnavi-ticket.sh start x"
	if v := e.body("", nil, "Bash", command)["verdict"]; v != "allow" {
		t.Errorf("verdict = %v", v)
	}
	body := e.body("", nil, "Bash", command, "--agent-id", "a1")
	if body["verdict"] != "deny" || body["code"] != "DENY_SUBAGENT_TICKET_OP" {
		t.Errorf("body = %v", body)
	}
}

// 知らない権限モード・無いディレクトリ・TOOL と SUBJECT の間のオプション・余った語は、黙って破棄せずに断る。
// payload の欄は --test でだけ指定できる。
func TestHitsBadArgumentsAreRefused(t *testing.T) {
	t.Parallel()
	e := newHitsEnv(t)
	missing := filepath.Join(e.root, "missing")
	refusals := []struct {
		argv []string
		said string
	}{
		{[]string{"--test", "Bash", "ls", "--permission-mode", "dontask"}, "権限モードの名前ではない"},
		{[]string{"--test", "Bash", "ls", "--cwd", missing}, "ディレクトリではない"},
		{[]string{"--test", "Bash", "--permission-mode", "auto", "ls"}, "オプション（--permission-mode）"},
		{[]string{"--test", "--agent-id", "a1", "ls"}, "オプション（--agent-id）"},
		{[]string{"--test", "Bash", "ls", "extra"}, "余った語: extra"},
	}
	for _, route := range []string{"--explain", "--lint", "--docs"} {
		for _, flag := range []string{"--permission-mode", "--agent-id"} {
			refusals = append(refusals, struct {
				argv []string
				said string
			}{[]string{route, flag, "default"}, flag + " は --test でだけ指定できる"})
		}
	}
	for _, c := range refusals {
		done := e.ccnavi("", nil, c.argv...)
		if done.Code != 1 || done.Stdout != "" || !strings.Contains(done.Stderr, c.said) {
			t.Errorf("%v: code = %d, stdout = %q, stderr = %q", c.argv, done.Code, done.Stdout, done.Stderr)
		}
	}
	// 出力の形（--json）が付いても同じ。
	if done := e.ccnavi("", nil, "--test", "Bash", "ls", "--cwd", missing, "--json"); done.Code != 1 || done.Stdout != "" {
		t.Errorf("code = %d, stdout = %q", done.Code, done.Stdout)
	}
	if done := e.ccnavi("", nil, "--test", "Bash", "ls", "--permission-mode", "auto"); done.Code != 0 {
		t.Errorf("code = %d: %s", done.Code, done.Stderr)
	}
}

// --explain の「どのルールにも一致しないツール呼び出し」の表は、判定と同じ関数で権限モードごとの結末を出す。
func TestHitsExplainFollowsTheUndeclaredVerdict(t *testing.T) {
	t.Parallel()
	e := newHitsEnv(t)
	explained := func(env map[string]string) []string {
		done := e.ccnavi("", env, "--explain")
		if done.Code != 0 {
			t.Fatalf("code = %d: %s", done.Code, done.Stderr)
		}
		lines := strings.Split(done.Stdout, "\n")
		for i, l := range lines {
			if l == "■ どのルールにも一致しないツール呼び出し" {
				return lines[i:min(i+10, len(lines))]
			}
		}
		t.Fatalf("表が無い: %s", done.Stdout)
		return nil
	}
	row := func(lines []string, mode string) string {
		for _, l := range lines {
			if f := strings.Fields(l); len(f) > 0 && f[0] == mode {
				return l
			}
		}
		t.Fatalf("%s の行が無い: %v", mode, lines)
		return ""
	}
	lines := explained(nil)
	mustContain(t, "default", row(lines, "default"), "読めた: 権限モードに委ねる / 読み切れない: ccnavi が確認を出す")
	mustContain(t, "auto", row(lines, "auto"), "（classifier が判断）")
	mustContain(t, "dontAsk", row(lines, "dontAsk"), "読めた: ccnavi が止める / 読み切れない: ccnavi が止める")
	mustContain(t, "不明", row(lines, "(不明・無し)"), "読めた: ccnavi が確認を出す")

	// 書いたままの表記（空白・大文字）でも、hook と同じく disable と読む。読み切れない呼び出しは、それでも止める。
	lines = explained(map[string]string{"CCNAVI_GUARD_UNWATCHED": " Disable"})
	mustContain(t, "bypassPermissions", row(lines, "bypassPermissions"), "読めた: 権限モードに委ねる / 読み切れない: ccnavi が止める")
	mustContain(t, "表", strings.Join(lines, "\n"), "CCNAVI_GUARD_UNWATCHED=disable")
}
