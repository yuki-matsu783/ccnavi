package rules

import (
	"testing"

	"ccnavi/internal/harness/run"
	"ccnavi/internal/infra/env"
)

// ルールが言及しない呼び出しの結末が、Claude Code の権限モードで変わること。
// 外から道具を動かし、読み返すのは標準出力と記録の 1 行だけ。ccnavi が「判定を返さない」ことは、返した JSON の有無でしか外から見えない。

// どのルールも言及しない呼び出し。allow にも deny にも ask にもヒットしない。
const undeclaredCommand = "docker run --rm alpine"

// modeCase は 1 回の呼び出し。記録を取り合わないよう、呼び出しごとに見本のルールを据えた別の作業場所を使う。
type modeCase struct {
	permission string
	command    string
	mode       string
	unwatched  string // CCNAVI_GUARD_UNWATCHED
}

func (c modeCase) run(t testing.TB) (run.Result, reply, map[string]any) {
	t.Helper()
	if c.command == "" {
		c.command = undeclaredCommand
	}
	if c.mode == "" {
		c.mode = "enable"
	}
	e := map[string]string{env.GuardCoreFiles: "disable"}
	if c.unwatched != "" {
		e[env.GuardUnwatched] = c.unwatched
	}
	ws := withRules(t, "rules-undeclared.yml")
	body := preBash(c.command)
	body["permission_mode"] = c.permission
	r := fire(t, ws, []string{"--mode", c.mode}, body, e)
	return r, parse(t, r), lastRecord(t, ws)
}

func TestAHandoverIsStillRecorded(t *testing.T) {
	t.Parallel()
	// 渡した回も記録に残る。ここが唯一「ルールが薄い場所」の手掛かりになる。
	_, _, rec := modeCase{permission: "auto"}.run(t)
	if str(rec, "decision") != "handover" || str(rec, "code") != "UNDECLARED" || str(rec, "permission_mode") != "auto" || rec["enforced"] != false {
		t.Errorf("記録: %v", rec)
	}
}

func TestEveryModeWithAJudgeGetsNoVerdict(t *testing.T) {
	t.Parallel()
	// 判断できる相手が居るモードには渡す。ccnavi が確認を上乗せしても、判断する者が増えるわけではない。
	for _, mode := range []string{"auto", "default", "acceptEdits", "plan"} {
		t.Run(mode, func(t *testing.T) {
			t.Parallel()
			r, _, rec := modeCase{permission: mode}.run(t)
			if r.Stdout != "" {
				t.Errorf("判定を返している: %s", r.Stdout)
			}
			if str(rec, "decision") != "handover" || str(rec, "code") != "UNDECLARED" {
				t.Errorf("記録: %v", rec)
			}
		})
	}
}

func TestAHandoverIsNotCountedAsAskingTheUser(t *testing.T) {
	t.Parallel()
	_, _, handed := modeCase{permission: "auto"}.run(t)
	_, _, asked := modeCase{permission: "someFutureMode"}.run(t)
	if str(handed, "decision") == str(asked, "decision") {
		t.Errorf("渡した回とユーザに聞いた回が混ざっている: %v %v", handed, asked)
	}
}

func TestBypassPermissionsRefuses(t *testing.T) {
	t.Parallel()
	// 確認できる者が居ないモードでは許可としない。
	_, out, rec := modeCase{permission: "bypassPermissions"}.run(t)
	if out.Decision != "deny" || str(rec, "decision") != "deny" || str(rec, "code") != "UNDECLARED" {
		t.Errorf("deny / UNDECLARED のはず: %+v %v", out, rec)
	}
	// 断りの文面は、言えば通るかもしれない ask とは別の次の一手を示す。
	mustHave(t, out.Reason, "no one to ask")
	mustNotHave(t, out.Reason, "asks the user")
}

func TestTheProjectCanOpenTheUnwatchedGate(t *testing.T) {
	t.Parallel()
	// CCNAVI_GUARD_UNWATCHED=disable にした設定では、ここも渡す側になる。
	for _, mode := range []string{"dontAsk", "bypassPermissions"} {
		t.Run(mode, func(t *testing.T) {
			t.Parallel()
			r, _, rec := modeCase{permission: mode, unwatched: "disable"}.run(t)
			if r.Stdout != "" || str(rec, "decision") != "handover" {
				t.Errorf("渡す側になるはず: %q %v", r.Stdout, rec)
			}
		})
	}
}

func TestAnUnreadCommandIsRefusedEvenWithTheGateOpen(t *testing.T) {
	t.Parallel()
	// 渡す先が「確認しない」と決まっている以上、読めなかったことを言える場所が他に無い。
	_, out, rec := modeCase{permission: "bypassPermissions", command: "bash -c 'docker run --rm alpine'", unwatched: "disable"}.run(t)
	if out.Decision != "deny" || str(rec, "code") != "PARSE_UNCERTAIN" {
		t.Errorf("deny / PARSE_UNCERTAIN のはず: %+v %v", out, rec)
	}
}

// 言及の無い呼び出しの文は、結末（ask / deny）と読み切れたかの組で次の一手を変える。

const unreadable = `eval "$X"`

func TestAnUnreadStopDoesNotPromiseThatSayingThePurposeHelps(t *testing.T) {
	t.Parallel()
	for _, mode := range []string{"dontAsk", "bypassPermissions"} {
		t.Run(mode, func(t *testing.T) {
			t.Parallel()
			_, out, rec := modeCase{permission: mode, command: unreadable}.run(t)
			if out.Decision != "deny" || str(rec, "code") != "PARSE_UNCERTAIN" {
				t.Fatalf("deny / PARSE_UNCERTAIN のはず: %+v %v", out, rec)
			}
			mustHave(t, out.Reason, "通りません", "読める形に書き直してください")
			mustNotHave(t, out.Reason, "確認しています")
		})
	}
}

func TestAnUnreadAskAsksForThePurpose(t *testing.T) {
	t.Parallel()
	_, out, _ := modeCase{permission: "default", command: unreadable}.run(t)
	if out.Decision != "ask" {
		t.Errorf("ask のはず: %+v", out)
	}
	mustHave(t, out.Reason, "確認しています")
	mustNotHave(t, out.Reason, "通りません")
}

func TestTheTextDoesNotSayThisRuleWhenNoRuleHit(t *testing.T) {
	t.Parallel()
	for _, mode := range []string{"default", "bypassPermissions"} {
		t.Run(mode, func(t *testing.T) {
			t.Parallel()
			_, out, _ := modeCase{permission: mode, command: unreadable}.run(t)
			mustHave(t, out.Reason, "どのルールもそれに言及していません")
			mustNotHave(t, out.Reason, "このルールは")
		})
	}
}

func TestAReadableUndeclaredCallShowsTheWayToAddAnAllow(t *testing.T) {
	t.Parallel()
	// 知らない権限モードは ask になる（default などの判断できるモードは渡す）。
	_, out, _ := modeCase{permission: ""}.run(t)
	if out.Decision != "ask" {
		t.Errorf("ask のはず: %+v", out)
	}
	mustHave(t, out.Reason, "asks the user", "allow section of")
}

func TestAHandoverBuildsNoTextAndOnlyRecords(t *testing.T) {
	t.Parallel()
	// HANDOVER の回に文の表を引いて落ちない。default で言及の無い読めた呼び出しは渡す。
	r, _, rec := modeCase{permission: "default"}.run(t)
	if r.Stdout != "" || str(rec, "decision") != "handover" || str(rec, "code") != "UNDECLARED" {
		t.Errorf("渡して記録だけ残すはず: %q %v", r.Stdout, rec)
	}
}

// 権限モードが判定に使われるのは、ルールが何も言わなかったときだけ。

func TestDenyHoldsInEveryMode(t *testing.T) {
	t.Parallel()
	for _, mode := range []string{"auto", "default", "bypassPermissions", "dontAsk"} {
		t.Run(mode, func(t *testing.T) {
			t.Parallel()
			if _, out, _ := (modeCase{permission: mode, command: "git push origin main"}).run(t); out.Decision != "deny" {
				t.Errorf("deny のはず: %+v", out)
			}
		})
	}
}

func TestAnExplicitAskIsNeverHandedOver(t *testing.T) {
	t.Parallel()
	// ルールが ask と書いた場所は、auto でもユーザに出す。意図した確認ポイント（fixture の release）。
	_, out, rec := modeCase{permission: "auto", command: "make release"}.run(t)
	if out.Decision != "ask" || str(rec, "code") != "RULE_ASK" {
		t.Errorf("ask / RULE_ASK のはず: %+v %v", out, rec)
	}
}

func TestAllowHoldsInEveryMode(t *testing.T) {
	t.Parallel()
	for _, mode := range []string{"auto", "bypassPermissions"} {
		t.Run(mode, func(t *testing.T) {
			t.Parallel()
			r, _, rec := modeCase{permission: mode, command: "ls -la"}.run(t)
			if r.Stdout != "" || str(rec, "decision") != "allow" {
				t.Errorf("allow のはず: %q %v", r.Stdout, rec)
			}
		})
	}
}

func TestDryRunSaysNothingAboutAHandover(t *testing.T) {
	t.Parallel()
	// enable が何もしない回は、dry-run も何も言わない。報告が実物と一致する。
	r, _, rec := modeCase{permission: "auto", mode: "dry-run"}.run(t)
	if r.Stdout != "" || str(rec, "decision") != "handover" {
		t.Errorf("何も言わないはず: %q %v", r.Stdout, rec)
	}
}

func TestDryRunStillReportsWhatEnableWouldRefuse(t *testing.T) {
	t.Parallel()
	r, _, _ := modeCase{permission: "bypassPermissions", mode: "dry-run"}.run(t)
	mustHave(t, r.Stdout, "would have stopped")
}
