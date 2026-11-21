package core

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"os/exec"
	"runtime"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/exe"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/infra/env"
)

// 実行ファイルの入口のスモークテスト。標準入出力が UTF-8 で通ること、終了コードがプロセスの終了コードになることを、
// 素の環境で 1 回ずつ見る。

// spawn は固定のルール（tests/fixtures/rules.yml）で、`--mode enable` の hook として payload を渡す。
// コアファイルのバックアップと復元は切る。リポジトリ自身をワークスペースルートにして動くので、切らないと、
// ワークツリーで消した設定ファイルが `logs/state` のバックアップから書き戻される。
func spawn(t *testing.T, payload string) run.Result {
	t.Helper()
	return run.Ccnavi(t,
		[]string{"--root", base.FixtureWorkspace(t, "rules.yml"), "--mode", "enable"},
		run.Opts{
			Input:      payload,
			Dir:        base.Root(),
			Env:        run.CleanEnv(map[string]string{env.GuardCoreFiles: "disable"}),
			NoMessages: true,
		})
}

func bashPayload(t *testing.T, command string) string {
	t.Helper()
	b, err := json.Marshal(map[string]any{
		"hook_event_name": "PreToolUse",
		"tool_name":       "Bash",
		"tool_input":      map[string]any{"command": command},
	})
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

// 判定は UTF-8 の JSON で返り、終了コードは 0。判定の理由は対象を名指しするので、日本語を含む対象がそのまま返ることで、
// 入口の文字コードの切り替えが効いていることも分かる。
func TestVerdictComesBackAsUTF8JSONWithExitZero(t *testing.T) {
	t.Parallel()
	cases := []struct{ name, command, reasonHas string }{
		{"deny", "git push origin main", "DENY_COMMAND_PATTERN"},
		{"japanese", "git push origin 統合先", "git push origin 統合先"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			r := spawn(t, bashPayload(t, c.command))
			if r.Code != 0 {
				t.Fatalf("code = %d, stderr = %s", r.Code, r.Stderr)
			}
			var out struct {
				HookSpecificOutput struct {
					PermissionDecision       string
					PermissionDecisionReason string
				}
			}
			if err := json.Unmarshal([]byte(r.Stdout), &out); err != nil {
				t.Fatal(err)
			}
			got := out.HookSpecificOutput
			if got.PermissionDecision != "deny" || !strings.Contains(got.PermissionDecisionReason, c.reasonHas) {
				t.Errorf("verdict = %+v, want deny with %q", got, c.reasonHas)
			}
		})
	}
}

func TestNoPayloadFailsWithANonzeroExit(t *testing.T) {
	t.Parallel()
	r := spawn(t, "")
	if r.Code == 0 || !strings.Contains(r.Stderr, "hook") {
		t.Errorf("code = %d, stderr = %q", r.Code, r.Stderr)
	}
}

// 引数なしでターミナルから打つと、payload を待たずに使い方を出して 1 で終える。端末は `script` で与える
// （Linux は `script -qec`、macOS は `script -q /dev/null`）。無い環境と Windows は飛ばす。
func TestATerminalGetsTheUsageWithoutWaitingForAPayload(t *testing.T) {
	t.Parallel()
	script, err := exec.LookPath("script")
	if err != nil || runtime.GOOS == "windows" {
		t.Skip("疑似ターミナルを与える script が無い")
	}
	exePath := exe.Path(t)
	root := base.FixtureWorkspace(t, "rules.yml")
	args := []string{"-qec", shellQuote(exePath) + " --root " + shellQuote(root), "/dev/null"}
	if runtime.GOOS == "darwin" {
		args = []string{"-q", "/dev/null", exePath, "--root", root}
	}
	ctx, cancel := context.WithTimeout(context.Background(), base.ExecTimeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, script, args...)
	cmd.Dir = base.Root()
	for k, v := range run.CleanEnv(nil) {
		cmd.Env = append(cmd.Env, k+"="+v)
	}
	var out bytes.Buffer
	cmd.Stdout, cmd.Stderr = &out, &out
	err = cmd.Run()
	var exit *exec.ExitError
	if ctx.Err() != nil {
		t.Fatal("ターミナルの入力を待ち続けた")
	}
	if !errors.As(err, &exit) || exit.ExitCode() != 1 {
		t.Errorf("err = %v, want exit 1\n%s", err, out.String())
	}
	if !strings.Contains(out.String(), "hook") {
		t.Errorf("使い方が出ていない: %q", out.String())
	}
}

func shellQuote(s string) string { return "'" + strings.ReplaceAll(s, "'", `'"'"'`) + "'" }
