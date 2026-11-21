package post

// 判定の期限・失敗や拒否の連続・同じ拒否の繰り返しの、入口から見える振る舞い。

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/harness/exe"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/infra/env"
)

// ---- 判定の期限（REQ-CMN-08） ----

// 判定の期限の既定（秒）。
const deadlineSeconds = 3.0

// lateOnFirstMatch は最初の照合で超える期限（秒）。実時間を待たずに期限切れの経路を見る。
const lateOnFirstMatch = "0.000001"

func deadlineRun(t *testing.T, root, mode, tool string, input map[string]any, deadline string) run.Result {
	t.Helper()
	environ := map[string]string{env.GuardCoreFiles: "disable"}
	if deadline != "" {
		environ[env.HookDeadlineSeconds] = deadline
	}
	return fire(t, root, call{Event: "PreToolUse", Tool: tool, Input: input, Mode: mode, Env: environ})
}

func bashInput(command string) map[string]any { return map[string]any{"command": command} }

// 期限は判定がルールを 1 つ照合するたびに見る。期限に達した回は、標準出力に allow や deny の応答を出さない。
// enable は終了コード 2、dry-run は安全な読み取りだけ 0。標準エラーに期限の文面、記録の `reason` に deadline-exceeded。
// どの照合の回で期限に達しても応答を出さないこと、期限ちょうどは超えていないことは、時計を差し替えられる
// hook/judge の TestDeadline が見る。
func TestPastTheDeadlineTheCallIsStoppedAndSaidSo(t *testing.T) {
	t.Parallel()
	t.Run("enable stops with exit 2 and says so", func(t *testing.T) {
		t.Parallel()
		root := freshWorkspace(t, "rules.yml")
		for _, command := range []string{"echo hi", "git push origin main", "cat .env"} {
			got := deadlineRun(t, root, "enable", "Bash", bashInput(command), lateOnFirstMatch)
			if got.Code != 2 || got.Stdout != "" {
				t.Errorf("%s: code=%d stdout=%q stderr=%s", command, got.Code, got.Stdout, got.Stderr)
			}
			mustHave(t, got.Stderr, "[ccnavi] deadline-exceeded", "期限（", "止めました", "短く分けて")
		}
		row := last(t, root)
		if str(row, "decision") != "skip" || str(row, "reason") != "deadline-exceeded" {
			t.Errorf("record = %v", row)
		}
	})
	t.Run("dry-run lets a safe read through and does not say it stopped", func(t *testing.T) {
		t.Parallel()
		root := freshWorkspace(t, "rules.yml")
		got := deadlineRun(t, root, "dry-run", "Bash", bashInput("ls"), lateOnFirstMatch)
		if got.Code != 0 || got.Stdout != "" {
			t.Errorf("code=%d stdout=%q", got.Code, got.Stdout)
		}
		mustHave(t, got.Stderr, "[ccnavi] deadline-exceeded", "通しました")
		mustNotHave(t, got.Stderr, "止めました")
	})
}

// 期限切れの結論もツールの種類で決まる（書き込みは止め、安全な読み取りは通す）。
func TestTheConclusionPastTheDeadlineFollowsTheToolKind(t *testing.T) {
	t.Parallel()
	cases := []struct {
		tool  string
		input map[string]any
		want  int
	}{
		{"Write", map[string]any{"file_path": "/tmp/x"}, 2},
		{"Edit", map[string]any{"file_path": "/tmp/x"}, 2},
		{"Agent", map[string]any{"description": "d"}, 2},
		{"Read", map[string]any{"file_path": "/tmp/notes.md"}, 0},
		{"Read", map[string]any{"file_path": "/home/u/.env"}, 2},
		{"Grep", map[string]any{"pattern": "x", "path": "/tmp/notes"}, 0},
		{"Grep", map[string]any{"pattern": "x", "path": "/home/u/.env"}, 2},
		{"Glob", map[string]any{"pattern": "/tmp/notes/*.md"}, 0},
		{"PowerShell", bashInput("echo hi"), 2},
		{"Bash", bashInput("ls"), 0},
		{"Bash", bashInput("echo hi"), 2},
		{"Bash", bashInput("cat .env"), 2},
	}
	for _, c := range cases {
		for _, mode := range []string{"enable", "dry-run"} {
			t.Run(c.tool+"/"+mode+"/"+strings.Join(mapValues(c.input), " "), func(t *testing.T) {
				t.Parallel()
				root := freshWorkspace(t, "rules.yml")
				got := deadlineRun(t, root, mode, c.tool, c.input, lateOnFirstMatch)
				if got.Code != c.want || str(last(t, root), "reason") != "deadline-exceeded" {
					t.Errorf("code=%d want %d: %s", got.Code, c.want, got.Stderr)
				}
			})
		}
	}
}

func mapValues(m map[string]any) []string {
	var out []string
	for _, v := range m {
		if s, ok := v.(string); ok {
			out = append(out, s)
		}
	}
	return out
}

// 実時間の代表入力は、期限の 1/3 に収まる大きさにして、上限は緩く取る（遅い機械で揺れないよう期限そのものにする）。
// 時間を測るので、並行に回さない（ほかのテストと取り合わない）。
func TestRepresentativeLargeInputsFinishWellWithinTheDeadline(t *testing.T) {
	exe.Path(t) // 実行ファイルの組み立てを測りに入れない
	root := freshWorkspace(t, "rules.yml")
	for name, command := range map[string]string{
		"a long command":     "echo " + strings.Repeat("a", 3000),
		"many substitutions": "echo " + strings.Repeat("$(:) ", 500),
	} {
		t.Run(name, func(t *testing.T) {
			start := time.Now()
			got := deadlineRun(t, root, "enable", "Bash", bashInput(command), "")
			took := time.Since(start).Seconds()
			mustNotHave(t, got.Stderr, "deadline-exceeded")
			if took >= deadlineSeconds {
				t.Errorf("%.2f 秒かかった（上限 %.0f 秒）", took, deadlineSeconds)
			}
		})
	}
}

// ---- 失敗や拒否が続いたとき、資料を引く案内を出す（issue #270） ----

const (
	streakNote  = "失敗か拒否が"
	streakFirst = "失敗したか、ccnavi に拒否されました"
	docsFlag    = "--docs"
)

// streak は rules-undeclared.yml のルールを据えた作業場所と、そこへ 1 回ずつ渡す道具。
// コアファイルのバックアップと復元は切る（試したいのは数えだけ）。
type streak struct {
	root string
	env  map[string]string
}

func newStreak(t *testing.T, extra map[string]string) *streak {
	environ := map[string]string{env.GuardCoreFiles: "disable"}
	for k, v := range extra {
		environ[k] = v
	}
	return &streak{freshWorkspace(t, "rules-undeclared.yml"), environ}
}

func (s *streak) send(t *testing.T, c call) run.Result {
	t.Helper()
	if c.Mode == "" {
		c.Mode = "enable"
	}
	c.Env = s.env
	if c.Extra == nil {
		c.Extra = map[string]any{}
	}
	c.Extra["permission_mode"] = "auto"
	c.Extra["cwd"] = s.root
	return fire(t, s.root, c)
}

func (s *streak) fail(t *testing.T, session string, interrupt bool) run.Result {
	return s.send(t, call{Event: "PostToolUseFailure", Session: session, Input: bashInput("false"),
		Extra: map[string]any{"error": "Exit code 1", "is_interrupt": interrupt}})
}

func (s *streak) deny(t *testing.T, mode string) run.Result {
	return s.send(t, call{Event: "PreToolUse", Mode: mode, Input: bashInput("git push origin main")})
}

// text は応答の文（無ければ空）。
func text(t *testing.T, r run.Result) string {
	t.Helper()
	if strings.TrimSpace(r.Stdout) == "" {
		return ""
	}
	out, _ := hookOutput(t, r)["hookSpecificOutput"].(map[string]any)
	if s := str(out, "permissionDecisionReason"); s != "" {
		return s
	}
	return str(out, "additionalContext")
}

// 連続が閾値（既定 3）の倍数になった回に案内が付く。判定そのもの（deny）は変わらない。
func TestTheStreakNoteComesEveryThresholdFailures(t *testing.T) {
	t.Parallel()
	s := newStreak(t, nil)
	var said []string
	for i := range 7 {
		got := s.fail(t, "", false)
		if got.Code != 0 {
			t.Fatalf("code=%d %s", got.Code, got.Stderr)
		}
		if i == 0 {
			out, _ := hookOutput(t, got)["hookSpecificOutput"].(map[string]any)
			if str(out, "hookEventName") != "PostToolUseFailure" {
				t.Errorf("hookSpecificOutput = %v", out)
			}
		}
		said = append(said, text(t, got))
	}
	mustHave(t, said[0], "迂回", "ユーザに相談", streakFirst)
	mustNotHave(t, said[0], streakNote)
	mustHave(t, said[2], "迂回", "ユーザに相談", streakNote, "3 回続いて", docsFlag)
	mustHave(t, said[5], "6 回続いて")
	for _, i := range []int{1, 3, 4, 6} {
		if said[i] != "" {
			t.Errorf("%d 回目は何も言わない: %q", i+1, said[i])
		}
	}
}

func TestTheStreakNoteFollowsTheEnvironmentAndTheTool(t *testing.T) {
	t.Parallel()
	t.Run("threshold", func(t *testing.T) {
		t.Parallel()
		s := newStreak(t, map[string]string{env.FailStreakCount: "2"})
		mustHave(t, text(t, s.fail(t, "", false)), streakFirst)
		mustHave(t, text(t, s.fail(t, "", false)), streakNote)
	})
	t.Run("an Artifact failure names the workspace scratchpad", func(t *testing.T) {
		t.Parallel()
		s := newStreak(t, nil)
		got := text(t, s.send(t, call{Event: "PostToolUseFailure", Tool: "Artifact", Input: map[string]any{"file_path": "/tmp/x.html"},
			Extra: map[string]any{"error": "failed", "is_interrupt": false}}))
		mustHave(t, got, "Artifact ツールが失敗しました", slash(filepath.Join(s.root, "scratchpad")))
	})
	t.Run("an Artifact interrupt says nothing about the scratchpad", func(t *testing.T) {
		t.Parallel()
		s := newStreak(t, nil)
		got := text(t, s.send(t, call{Event: "PostToolUseFailure", Tool: "Artifact", Input: map[string]any{"file_path": "/tmp/x.html"},
			Extra: map[string]any{"error": "interrupted", "is_interrupt": true}}))
		mustNotHave(t, got, "scratchpad")
	})
	t.Run("another tool does not mention Artifact", func(t *testing.T) {
		t.Parallel()
		s := newStreak(t, nil)
		mustNotHave(t, text(t, s.fail(t, "", false)), "Artifact")
	})
}

// 拒否も数える。案内は拒否の文面の末尾に付き、判定は deny のまま。失敗と拒否は 1 本の連続。
func TestDeniesAreCountedAndKeepTheirVerdict(t *testing.T) {
	t.Parallel()
	t.Run("enable", func(t *testing.T) {
		t.Parallel()
		s := newStreak(t, nil)
		for i := range 3 {
			got := s.deny(t, "")
			out, _ := hookOutput(t, got)["hookSpecificOutput"].(map[string]any)
			if str(out, "permissionDecision") != "deny" {
				t.Fatalf("判定は deny のまま: %v", out)
			}
			reason := str(out, "permissionDecisionReason")
			if (i == 2) != strings.Contains(reason, streakNote) {
				t.Errorf("%d 回目の案内: %s", i+1, reason)
			}
			if i == 2 {
				mustHave(t, reason, docsFlag)
			}
		}
	})
	t.Run("dry-run counts too", func(t *testing.T) {
		t.Parallel()
		s := newStreak(t, nil)
		var said []string
		for range 3 {
			said = append(said, text(t, s.deny(t, "dry-run")))
		}
		mustNotHave(t, said[0]+said[1], streakNote)
		mustHave(t, said[2], streakNote, "dry-run")
	})
	t.Run("failures and denies share one streak", func(t *testing.T) {
		t.Parallel()
		s := newStreak(t, nil)
		s.fail(t, "", false)
		s.deny(t, "")
		mustHave(t, text(t, s.fail(t, "", false)), streakNote)
	})
}

func TestOnlyFailuresAndDeniesOfTheSameSessionAreCounted(t *testing.T) {
	t.Parallel()
	t.Run("a call that is not denied", func(t *testing.T) {
		t.Parallel()
		s := newStreak(t, nil)
		for range 4 {
			got := s.send(t, call{Event: "PreToolUse", Input: bashInput("ls")})
			mustNotHave(t, got.Stdout, streakNote)
		}
		got := text(t, s.fail(t, "", false)) // 数えていないので、最初の 1 回の案内はこの失敗で初めて出る
		mustHave(t, got, streakFirst)
		mustNotHave(t, got, streakNote)
	})
	t.Run("an interrupt", func(t *testing.T) {
		t.Parallel()
		s := newStreak(t, nil)
		for range 4 {
			if got := text(t, s.fail(t, "", true)); got != "" {
				t.Errorf("中断は数えない: %q", got)
			}
		}
		names, _ := os.ReadDir(filepath.Join(s.root, "logs", "state"))
		for _, n := range names {
			if strings.HasPrefix(n.Name(), "failstreak-") {
				t.Errorf("記録を作らない: %s", n.Name())
			}
		}
	})
	t.Run("sessions are counted apart", func(t *testing.T) {
		t.Parallel()
		s := newStreak(t, nil)
		s.fail(t, "a", false)
		s.fail(t, "b", false)
		s.fail(t, "a", false)
		if got := text(t, s.fail(t, "b", false)); got != "" {
			t.Errorf("b の 2 回目は何も言わない: %q", got)
		}
		mustHave(t, text(t, s.fail(t, "a", false)), streakNote)
	})
}

// ---- 同じ理由で同じ呼び出しを繰り返し止めたとき、文面とユーザへの報告で名指しする（issue #149 の 3） ----

const repeatNote = "言い換えて再実行せず"

func (s *streak) push(t *testing.T, command string, extra map[string]string) (decision, reason string) {
	t.Helper()
	c := call{Event: "PreToolUse", Mode: "enable", Input: bashInput(command), Extra: map[string]any{"permission_mode": "auto"}}
	c.Env = map[string]string{env.GuardCoreFiles: "disable"}
	for k, v := range extra {
		c.Env[k] = v
	}
	out, _ := hookOutput(t, fire(t, s.root, c))["hookSpecificOutput"].(map[string]any)
	return str(out, "permissionDecision"), str(out, "permissionDecisionReason")
}

// N 回目から拒否の文面の末尾に「言い換えずに相談する」一文が足される。判定そのものは deny のまま。
func TestTheNthDenyOfTheSameCallAsksToConsult(t *testing.T) {
	t.Parallel()
	t.Run("the third, counting a re-typed call with other quoting as the same call", func(t *testing.T) {
		t.Parallel()
		s := newStreak(t, nil)
		var withNote []bool
		for _, command := range []string{"git push origin main", "git  push 'origin' main", `git push "origin" main`} {
			decision, reason := s.push(t, command, nil)
			if decision != "deny" {
				t.Fatalf("decision = %q", decision)
			}
			withNote = append(withNote, strings.Contains(reason, repeatNote))
		}
		if withNote[0] || withNote[1] || !withNote[2] {
			t.Errorf("note = %v", withNote)
		}
		_, reason := s.push(t, "git push origin main", nil)
		mustHave(t, reason, "4 回止めました")
	})
	t.Run("the threshold follows the environment", func(t *testing.T) {
		t.Parallel()
		s := newStreak(t, nil)
		extra := map[string]string{env.DenyRepeat: "2"}
		s.push(t, "git push origin main", extra)
		_, reason := s.push(t, "git push origin main", extra)
		mustHave(t, reason, repeatNote)
	})
	t.Run("other targets are counted apart", func(t *testing.T) {
		t.Parallel()
		s := newStreak(t, nil)
		for _, target := range []string{"a", "b", "c"} {
			_, reason := s.push(t, "git push origin "+target, nil)
			mustNotHave(t, reason, repeatNote)
		}
	})
}

func TestDryRunDoesNotCountDeniesAndAnUnwritableStateStillDenies(t *testing.T) {
	t.Parallel()
	t.Run("dry-run", func(t *testing.T) {
		t.Parallel()
		s := newStreak(t, nil)
		for range 3 {
			got := fire(t, s.root, call{Event: "PreToolUse", Mode: "dry-run", Input: bashInput("rm -rf /tmp/some-dir"),
				Env: map[string]string{env.GuardCoreFiles: "disable"}, Extra: map[string]any{"permission_mode": "auto"}})
			mustNotHave(t, got.Stdout, repeatNote)
		}
		names, _ := os.ReadDir(filepath.Join(s.root, "logs", "state"))
		for _, n := range names {
			if strings.HasPrefix(n.Name(), "denied-") {
				t.Errorf("止めていないので記録も作らない: %s", n.Name())
			}
		}
	})
	t.Run("a state place that cannot be written", func(t *testing.T) {
		t.Parallel()
		// 記録を書けなくても拒否は拒否のまま（一文が付かないだけ）。state の置き場をファイルで塞ぐ。
		s := newStreak(t, nil)
		write(t, filepath.Join(s.root, "logs", "state"), "x")
		for range 3 {
			decision, reason := s.push(t, "git push origin main", nil)
			if decision != "deny" || strings.Contains(reason, repeatNote) {
				t.Errorf("decision=%q reason=%s", decision, reason)
			}
		}
	})
}

// ターンの終わりにユーザへ 1 度言う。同じ回数のまま次のターンでは繰り返さない。
func TestTheTurnEndSaysARepeatedDenyOnce(t *testing.T) {
	t.Parallel()
	s := newStreak(t, nil)
	for range 3 {
		s.push(t, "git push origin main", nil)
	}
	stop := func() run.Result {
		return fire(t, s.root, call{Event: "Stop", Mode: "enable", Env: map[string]string{env.GuardCoreFiles: "disable"},
			Extra: map[string]any{"permission_mode": "auto"}})
	}
	first := stop()
	if first.Code != 0 {
		t.Fatalf("code=%d %s", first.Code, first.Stderr)
	}
	var out struct{ SystemMessage string }
	if err := json.Unmarshal([]byte(first.Stdout), &out); err != nil {
		t.Fatal(err)
	}
	mustHave(t, out.SystemMessage, "git-push（Bash）: 3 回")
	mustNotHave(t, stop().Stdout, "git-push")
}
