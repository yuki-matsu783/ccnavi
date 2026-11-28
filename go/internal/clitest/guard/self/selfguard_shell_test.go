package self

import (
	"strings"
	"testing"

	"ccnavi/internal/infra/env"
)

// ---- 戻す前に止める ----

// RULES にこの場所を守るルールは 1 件も無い。それでも止まるのが要点で、止める側もルールファイルの外に置いてあることを確かめている。
func TestShellWritesToTheSettingFilesStopWithoutAnyRule(t *testing.T) {
	t.Parallel()
	s := newSG(t)
	r := s.run("PreToolUse", call{Command: "echo x > .ccnavi/config/common/rules.yml"})
	mustHave(t, r.Stdout, "deny", settingFilesRule)
}

// 場所の名前が出たかどうかでは止めない。ここは読むほうが普通の場所で、名前で止めると、いちばんガードを直したいときに
// いちばん強く止まる。
func TestReadingTheSettingFilesPasses(t *testing.T) {
	t.Parallel()
	s := newSG(t)
	for _, command := range []string{
		"cd .ccnavi/config/common && cat rules.yml",
		"cat .ccnavi/config/common/rules.yml",
	} {
		mustNot(t, s.run("PreToolUse", call{Command: command}).Stdout, "deny")
	}
}

func TestDisableAddsNoStoppingSideEither(t *testing.T) {
	t.Parallel()
	s := newSG(t)
	r := s.run("PreToolUse", call{Setting: "disable", Command: "echo x > .ccnavi/config/common/rules.yml"})
	mustNot(t, r.Stdout, settingFilesRule)
	// 記録と state の置き場の名指しも止めない。
	r = s.run("PreToolUse", call{Setting: "disable", Tool: "Write", Path: s.at("logs/state/denied-x.json")})
	mustNot(t, r.Stdout, recordsRule)
}

// 消すのはセッションの開始とターミナルから打つ `ccnavi --prune` だけ（prune）。ラッパー sh の記録（git-*.log）は消しても
// 判定に影響しない。logs/ を丸ごと守ると片付けまで止まる。
func TestRotatedRecordsAreStoppedByShellWritesButWrapperLogsAreNot(t *testing.T) {
	t.Parallel()
	s := newSG(t)
	for _, command := range []string{
		"rm logs/decisions.20260927-120000.jsonl",
		"rm logs/decisions.*.jsonl",
		"echo x > logs/decisions.20260927-120000-2.jsonl",
		"cd logs && rm decisions.20260927-120000.jsonl",
	} {
		mustHave(t, s.run("PreToolUse", call{Command: command}).Stdout, settingFilesRule)
	}
	mustNot(t, s.run("PreToolUse", call{Command: "rm logs/git-20260913-000000-1.log"}).Stdout, settingFilesRule)
}

// シェルの側の文面は「Write / Edit でも拒否される場所」と言う。名指しのツールで書けると、拒否の数え（denied-<セッション>.json）や
// 記録を書き換えて自分の足跡を消せる。ツールと場所ごとの止める・通すは hook/judge の TestSelfGuardRecordsByNamedTools が見る。
func TestRecordsAreStoppedByNamedToolsToo(t *testing.T) {
	t.Parallel()
	s := newSG(t)
	mustHave(t, s.run("PreToolUse", call{Tool: "Write", Path: s.log}).Stdout, "deny", recordsRule)
}

// 引用に空白を含む形。引用の中の空白は語の中の切れ目で、コマンドの区切りとは別の目印で渡る。動詞と行き先の間に引用の空白が
// あっても同じコマンドの中なら届くこと、引用の中の空白の連続・空白に隣り合う演算子の文字（目印が 2 つ続く形）でも
// リダイレクトの行き先に届くこと、`>` を長く並べても後ろの書き込みを止めることを見る。
func TestWritesWithQuotedSpacesAreStopped(t *testing.T) {
	t.Parallel()
	s := newSG(t)
	rules := strings.ReplaceAll(s.rules, `\`, "/") // 引用の外に置くので区切りは `/`
	long := "echo x " + strings.Repeat(">", 500) + `"` + strings.Repeat("a ", 500) + "\"\necho x > .ccnavi/config/common/rules.yml"
	for _, command := range []string{
		`sed -i "s/a b/c/" ` + rules,
		`tee "a b" ` + rules,
		`cp "a b" ` + rules,
		`echo x > "projects/New folder (2)/.ccnavi/config/common/rules.yml"`,
		`echo x > "projects/has  space/.ccnavi/config/project/rules.yml"`,
		`echo x > "projects/R & D/.ccnavi/config/project/rules.yml"`,
		`echo x > "a; b/.ccnavi/config/common/rules.yml"`,
		long,
	} {
		r := s.run("PreToolUse", call{Command: command})
		if !strings.Contains(r.Stdout, "deny") || !strings.Contains(r.Stdout, settingFilesRule) {
			t.Errorf("%q: 止めるはず: %s", command, r.Stdout)
		}
	}
}

// ---- 生の文字列の `>` の個数の上限（redirectLimit）。時間ではなく個数で確かめる ----

var permissionModes = []string{"default", "acceptEdits", "plan", "dontAsk", "bypassPermissions"}

func manyRedirects(n int) string { return strings.Repeat(">a", n) }

// assertLimitDenied は上限で止めた答えの形を見て、理由を返す。上限を超えたときは、権限モードに依らず止める。確認には回さない。
func (s *sg) assertLimitDenied(r call, event string) string {
	s.t.Helper()
	decision, reason := verdict(s.t, s.run(event, r))
	if decision != "deny" {
		s.t.Fatalf("上限で止めるはず: %q %s", decision, reason)
	}
	// 既存の拒否と同じ見出し（コード・subject・本文）で、書き直し方まで言う。
	if !strings.HasPrefix(reason, "[ccnavi] DENY_REDIRECT_LIMIT (組み込みのルール: builtin:redirect-limit)\nsubject: ") {
		s.t.Errorf("見出し: %s", reason)
	}
	mustHave(s.t, reason, "'>' が 50 個を超えて", "止めました", "Write / Edit")
	mustNot(s.t, reason, "確認に答える人", "REDIRECT_LIMIT_ASK")
	rec := s.lastRecord()
	rules := strs(rec["rules"])
	if rec["decision"] != "deny" || rec["code"] != "DENY_REDIRECT_LIMIT" || len(rules) == 0 || rules[0] != "builtin:redirect-limit" {
		s.t.Errorf("記録: %v", rec)
	}
	return reason
}

// 上限で外すのは保護の照合だけ。ルールファイルの deny は生の文字列に適用し続ける。どの権限モードでも同じ。
func TestOverTheLimitARuleDenyStillApplies(t *testing.T) {
	t.Parallel()
	s := newSG(t)
	command := `git push; echo "` + manyRedirects(redirectLimit+1)
	for _, mode := range permissionModes {
		decision, reason := verdict(t, s.run("PreToolUse", call{PermissionMode: mode, Command: command}))
		if decision != "deny" || !strings.Contains(reason, "rule: .ccnavi/config/common/rules.yml:push") || strings.Contains(reason, "REDIRECT_LIMIT") {
			t.Errorf("%s: ルールの deny がそのまま出るはず: %s %s", mode, decision, reason)
		}
	}
}

// 読み解けた形には上限を掛けない。
func TestAReadableFormIsNotLimited(t *testing.T) {
	t.Parallel()
	s := newSG(t)
	many := manyRedirects(redirectLimit + 10)
	protected := "echo " + many + "; echo x > .ccnavi/config/common/rules.yml"
	harmless := "echo " + many
	for _, command := range []string{protected, harmless} {
		if degraded, _ := s.reading(command); degraded {
			t.Errorf("読み解けるはず: %s", command)
		}
	}
	decision, reason := verdict(t, s.run("PreToolUse", call{Command: protected}))
	if decision != "deny" || !strings.Contains(reason, settingFilesRule) {
		t.Errorf("保護の止めが先に当たるはず: %s %s", decision, reason)
	}
	r := s.run("PreToolUse", call{Command: harmless})
	mustNot(t, r.Stdout, "REDIRECT_LIMIT", "deny")
}

// 確認できるモードでも確認には回さない（保護を適用していない呼び出しなので止める側に置く）。権限モードごとの答えと上限ちょうどの
// 扱いは hook/judge の TestSelfGuardRedirectLimitInEveryMode が見る。ここでは文面を確認できるモードの代表で見る。
// 言及の無い呼び出しを委ねる設定（CCNAVI_GUARD_UNWATCHED=disable）でも、保護の照合を終えていないので委ねない。
func TestOverTheLimitIsStoppedInAnyModeAndTheTextSaysHowToRewrite(t *testing.T) {
	t.Parallel()
	s := newSG(t)
	command := "echo " + manyRedirects(redirectLimit+1) + `; echo x > .ccnavi/config/common/rules.yml; echo "`
	if degraded, _ := s.reading(command); !degraded {
		t.Fatal("読み切れない形のはず")
	}
	s.assertLimitDenied(call{PermissionMode: "default", Command: command}, "PreToolUse")

	unwatched := `echo "` + manyRedirects(redirectLimit+1)
	for _, mode := range permissionModes {
		s.assertLimitDenied(call{PermissionMode: mode, Env: map[string]string{env.GuardUnwatched: "disable"}, Command: unwatched}, "PreToolUse")
	}
}

// PowerShell は読まずに生の文字列に適用する。チケットの置き場を守る 1 本がヒットする。上限で止めても allow のルールは理由に
// 並べない（拒否の見出しは 1 つだけ。最初の失敗に付く資料の案内は数えない）。
func TestPowerShellRawStringIsLimitedToo(t *testing.T) {
	t.Parallel()
	s := newSG(t)
	command := `Write-Output "` + strings.Repeat(">", redirectLimit+1) + `"`
	s.assertLimitDenied(call{Tool: "PowerShell", Command: command}, "PreToolUse")

	write(t, s.rules, ruleBlob(t, func() payload {
		r := selfRules()
		r["allow"] = []payload{{"id": "anything-else", "match": "Bash|PowerShell|Read|Write|Edit", "regex": "."}}
		return r
	}()))
	reason := s.assertLimitDenied(call{Tool: "PowerShell", PermissionMode: "default", Command: command}, "PreToolUse")
	mustNot(t, reason, "anything-else")
	if n := strings.Count(reason, "[ccnavi] DENY_"); n != 1 {
		t.Errorf("拒否の見出しは 1 つだけのはず: %d\n%s", n, reason)
	}
	if rules := strs(s.lastRecord()["rules"]); len(rules) != 1 || rules[0] != "builtin:redirect-limit" {
		t.Errorf("記録の rules = %v", rules)
	}
}

// 保護を切り、チケット制御も切ると、シェルから書き込む形の保護は 1 本も無い。適用しない保護が無いので、上限で止める理由も無い。
// 既定の組み込みに戻っても、その 1 本は Bash にしかヒットしない。PowerShell には掛けない。
func TestNoLimitWhenNoProtectionIsLeftOut(t *testing.T) {
	t.Parallel()
	s := newSG(t)
	off := map[string]string{env.TicketControl: "disable"}
	bashCmd := `echo "` + manyRedirects(redirectLimit+1)
	mustNot(t, s.run("PreToolUse", call{Setting: "disable", Env: off, Command: bashCmd}).Stdout, "REDIRECT_LIMIT")

	write(t, s.rules, "version: [broken")
	power := `Write-Output "` + strings.Repeat(">", redirectLimit+1) + `"`
	mustNot(t, s.run("PreToolUse", call{Tool: "PowerShell", Env: off, Command: power}).Stdout, "REDIRECT_LIMIT")
	mustHave(t, s.run("PreToolUse", call{Env: off, Command: bashCmd}).Stdout, "DENY_REDIRECT_LIMIT")
}

// ask のルールにもヒットした回。判定を決めたのは上限なので、ask のルールの設定を残さない（組み込みの拒否と同じく出所を持たない）。
func TestTheRecordOfALimitDenyHasNoSource(t *testing.T) {
	t.Parallel()
	s := newSG(t)
	write(t, s.rules, ruleBlob(t, withRules(payload{"ask": []payload{{"id": "askme", "match": "Bash", "regex": "askme", "message": "ask the user."}}})))
	s.run("PreToolUse", call{Command: "echo x > .ccnavi/config/common/rules.yml"})
	builtin := s.lastRecord()
	if rules := strs(builtin["rules"]); len(rules) != 1 || rules[0] != settingFilesRule {
		t.Fatalf("組み込みの拒否の rules = %v", rules)
	}

	s.assertLimitDenied(call{Command: `askme; echo "` + manyRedirects(redirectLimit+1)}, "PreToolUse")

	rec := s.lastRecord()
	rules := strs(rec["rules"])
	if len(rules) != 2 || rules[0] != "builtin:redirect-limit" || rules[1] != ".ccnavi/config/common/rules.yml:askme" {
		t.Errorf("rules = %v", rules)
	}
	if rec["source"] != builtin["source"] || (rec["source"] != nil && rec["source"] != "") {
		t.Errorf("出所を持たないはず: %v / %v", rec["source"], builtin["source"])
	}
}

// 上限で外すのは生の文字列への照合だけで、中で実行されるコマンド（設定）には保護をそのまま適用する。`grep -n "<<"` で縮退させる。
// この縮退は読みを止めても実行役の中身（設定）を作るので、生の文字列の経路に入りつつ、`sudo` が実行する `tee` が設定に出る。
func TestOverTheLimitTheCommandsRunInsideAreStillProtected(t *testing.T) {
	t.Parallel()
	s := newSG(t)
	command := `grep -n "<<" f; sudo tee .ccnavi/config/common/rules.yml </dev/null; echo` + strings.Repeat(" >a", redirectLimit+1)
	degraded, unwrapped := s.reading(command)
	if !degraded || !strings.Contains(unwrapped, "tee .ccnavi/config/common/rules.yml") {
		t.Fatalf("degraded=%v unwrapped=%q", degraded, unwrapped)
	}

	decision, reason := verdict(t, s.run("PreToolUse", call{Command: command}))

	if decision != "deny" || !strings.Contains(reason, settingFilesRule) || strings.Contains(reason, "REDIRECT_LIMIT") {
		t.Errorf("保護が先に止めるはず: %s %s", decision, reason)
	}
	if rules := strs(s.lastRecord()["rules"]); len(rules) != 1 || rules[0] != settingFilesRule {
		t.Errorf("rules = %v", rules)
	}
}
