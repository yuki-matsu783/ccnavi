package judge

import (
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/policy/builtin"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/policy/selfguard"
	"ccnavi/internal/tickets/model/guard"
	"ccnavi/internal/tickets/testkit"
)

// fixtureWorkspace は受入テストのルール（tests/fixtures/rules.yml）だけを置いたワークスペース。
func fixtureWorkspace(t *testing.T, extra string) string {
	t.Helper()
	root := testkit.TempRoot(t)
	data, err := os.ReadFile(filepath.Join(testkit.RepoRoot(t), "tests", "fixtures", "rules.yml"))
	if err != nil {
		t.Fatal(err)
	}
	testkit.Put(t, filepath.Join(root, config.DefaultRules), string(data))
	if extra != "" {
		testkit.Put(t, filepath.Join(root, config.DefaultProjectHome, filepath.FromSlash(config.LayerConfigDir), "rules.yml"), extra)
	}
	testkit.CopyMessages(t, root)
	return root
}

// clock は最初の checks 回は期限の前、それより後は期限の後を返す時計に差し替える。呼ばれた回数を返す関数を返す。
func clock(t *testing.T, checks int, after time.Duration) func() int {
	t.Helper()
	calls := 0
	start := time.Now()
	t.Cleanup(fsio.SetNow(func() time.Time {
		calls++
		if calls <= checks {
			return start
		}
		return start.Add(after)
	}))
	return func() int { return calls }
}

// 期限（REQ-CMN-08）。ルールを 1 本当てるたびに見て、どの回で期限に達しても応答を出さずに止める。
func TestDeadline(t *testing.T) {
	root := fixtureWorkspace(t, "")
	envs := with("CCNAVI_GUARD_CORE_FILES", "disable")
	run := func(command string, mode config.Mode) judgeAnswer {
		e := maps(envs)
		e["CCNAVI_MODE"] = string(mode)
		return runGo(t, root, judgeCase{Payload: call(config.ToolBash, root, map[string]any{"command": command}), Env: e})
	}
	late := 2 * config.HookDeadline.Value()

	for _, command := range []string{"echo hi", "git push origin main", "cat .env"} {
		count := clock(t, 1<<30, 0)
		if a := run(command, config.Enable); strings.Contains(a.Stderr, "deadline-exceeded") {
			t.Fatalf("%s: 期限の前に deadline-exceeded", command)
		}
		checks := count()
		if checks == 0 {
			t.Fatalf("%s: 期限を見ていない", command)
		}
		for hit := range checks {
			clock(t, hit, late)
			a := run(command, config.Enable)
			if a.Code != config.ExitBlock || a.Stdout != "" || !strings.Contains(a.Stderr, "[ccnavi] deadline-exceeded") ||
				a.Record["reason"] != "deadline-exceeded" || a.Record["decision"] != "skip" {
				t.Errorf("%s の %d 回目: code %d stdout %q stderr %q record %v", command, hit, a.Code, a.Stdout, a.Stderr, a.Record)
			}
		}
	}
	// dry-run でも、設定を読まずに安全と分かる読み取りだけを通す。
	clock(t, 0, late)
	if a := run("ls", config.DryRun); a.Code != config.ExitOK || a.Stdout != "" || !strings.Contains(a.Stderr, "deadline-exceeded") {
		t.Errorf("dry-run ls: %+v", a)
	}
	// 比べるのは「過ぎた」か。ちょうどは過ぎていない。
	clock(t, 0, config.HookDeadline.Value())
	if a := run("echo hi", config.Enable); a.Code != config.ExitOK || strings.Contains(a.Stderr, "deadline-exceeded") {
		t.Errorf("期限ちょうど: %+v", a)
	}
}

// 繰り返しを含む利用者の式を数百 KB のコマンドに当てても落ちず、照合を打ち切ったルールは止める側に倒す
// （deny は当たり、allow は通す根拠にしない）。Python は打ち切らないので、ここは Python と突き合わせない。
func TestDeepUserRegexFallsToTheStoppingSide(t *testing.T) {
	const deep = `version: 1
deny:
  - id: deep-deny
    match: WebFetch
    regex: '(?:a|b)*c'
    message: deep
`
	const deepAllow = `version: 1
allow:
  - id: deep-allow
    match: WebFetch
    regex: '^https://x/(?:a|b)*$'
`
	// シェルのコマンドは長さの上限（CommandLengthLimit）で照合の前に止まるので、長い対象は WebFetch の URL で渡す。
	long := "https://x/" + strings.Repeat("ab", 100_000)
	clock(t, 1<<30, 0) // 期限で止まる経路と分けるため、時計を止めておく
	for _, tc := range []struct {
		rules, decision string
	}{
		{deep, "deny"},
		{deepAllow, "allow"},
	} {
		root := fixtureWorkspace(t, tc.rules)
		a := runGo(t, root, judgeCase{Payload: call(config.ToolWebFetch, root, map[string]any{"url": long}),
			Env: with("CCNAVI_GUARD_CORE_FILES", "disable")})
		if a.Error != "" {
			t.Fatalf("%s: %s", tc.decision, a.Error)
		}
		names, _ := a.Record["rules"].([]any)
		switch tc.decision {
		case "deny":
			if a.Record["decision"] != "deny" || !slices.Contains(names, any(".ccnavi/config/project/rules.yml:deep-deny")) {
				t.Errorf("deny の式を打ち切ったら止める: %v", a.Record)
			}
		case "allow":
			if slices.Contains(names, any(".ccnavi/config/project/rules.yml:deep-allow")) {
				t.Errorf("allow の式を打ち切ったら通す根拠にしない: %v", a.Record)
			}
		}
	}
}

// `>` の上限で外す保護の id は、実際に足すルールの id（名前が変わると上限が効かず、2 乗の照合に戻る）。
func TestShellGuardRuleIDsAreRealRules(t *testing.T) {
	root := testkit.TempRoot(t)
	rs := &rules.RuleSet{Version: rules.Version}
	selfguard.AddRules(rs, selfguard.Options{Root: root, Tool: config.ToolBash})
	ids := map[string]bool{}
	for _, r := range rs.Deny {
		ids[r.ID] = true
	}
	base, _ := builtin.Load(root, &config.Settings{})
	for _, r := range base.Deny {
		ids[r.ID] = true
	}
	for _, build := range []func() ([]*rules.Rule, error){
		func() ([]*rules.Rule, error) { return guard.GuardRules(config.DefaultTickets, root) },
		func() ([]*rules.Rule, error) { return guard.GateRules(config.DefaultApproved, root) },
	} {
		list, err := build()
		if err != nil {
			t.Fatal(err)
		}
		for _, r := range list {
			ids[r.ID] = true
		}
	}
	for _, id := range shellGuardRuleIDs {
		if !ids[id] {
			t.Errorf("%s は足すルールに無い", id)
		}
	}
}

func TestUndeclaredVerdict(t *testing.T) {
	for _, tc := range []struct {
		mode, degraded, unwatched string
		want                      rules.Decision
	}{
		{"default", "", "", Handover},
		{"auto", "", "", Handover},
		{"plan", "x", "", rules.Ask},
		{"dontAsk", "", "", rules.Deny},
		{"bypassPermissions", "", "disable", Handover},
		{"bypassPermissions", "x", "disable", rules.Deny},
		// 知らないモードは確認にする（名前が増えたとき、確認なしに通さない）。
		{"newMode", "", "", rules.Ask},
		{"", "", "", rules.Ask},
	} {
		if got := UndeclaredVerdict(tc.mode, tc.degraded, tc.unwatched); got != tc.want {
			t.Errorf("%q %q %q: %s, want %s", tc.mode, tc.degraded, tc.unwatched, got, tc.want)
		}
	}
}

func maps(m map[string]string) map[string]string {
	out := make(map[string]string, len(m))
	for k, v := range m {
		out[k] = v
	}
	return out
}
