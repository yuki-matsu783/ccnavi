package core

import (
	"bufio"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/harness/run"
)

// 記録と state の後始末（prune）の入口の受入テスト。
// `ccnavi --prune` は `--preview` なら動かさずに並べ、そうでなければターミナルを求める。セッションの開始でも走り、動かした数を記録に残す。
// エージェントが Bash から打つ形は実行前チェックで止まる。ローテート・保持日数・セッションごとの判断は go/internal/records/prune の go test が見る。

const (
	sessionOld = "11111111-aaaa-bbbb-cccc-000000000001"
	sessionMe  = "33333333-aaaa-bbbb-cccc-000000000003"
)

// putAged は path に書き、更新時刻を ageDays 日前にする。
func putAged(t *testing.T, path string, ageDays float64) string {
	t.Helper()
	writeFile(t, filepath.Dir(path), filepath.Base(path), "x")
	at := time.Now().Add(-time.Duration(ageDays * 24 * float64(time.Hour)))
	if err := os.Chtimes(path, at, at); err != nil {
		t.Fatal(err)
	}
	return path
}

type pruneEnv struct {
	t                 *testing.T
	root, logs, state string
}

func newPruneEnv(t *testing.T) *pruneEnv {
	root := emptyRoot(t)
	logs := filepath.Join(root, "logs")
	return &pruneEnv{t, root, logs, mkdir(t, filepath.Join(logs, "state"))}
}

func (e *pruneEnv) env(extra map[string]string) map[string]string {
	env := map[string]string{"CLAUDE_PROJECT_DIR": e.root, "CCNAVI_MODE": "enable", "CCNAVI_TICKET_CONTROL": "disable"}
	for k, v := range extra {
		env[k] = v
	}
	return env
}

func (e *pruneEnv) ccnavi(env map[string]string, input string, args ...string) run.Result {
	return run.Ccnavi(e.t, args, run.Opts{Input: input, Env: e.env(env), Dir: e.root})
}

var pruneApproval = map[string]string{"CCNAVI_GUARD_TICKET_APPROVAL": "disable"}

func TestPruneEntry(t *testing.T) {
	t.Parallel()
	oldLogName := "decisions.20260101-000000.jsonl"
	t.Run("--preview はターミナル無しで並べる", func(t *testing.T) {
		t.Parallel()
		e := newPruneEnv(t)
		old := putAged(t, filepath.Join(e.logs, oldLogName), 30)
		proc := e.ccnavi(nil, "", "--prune", "--preview")
		if proc.Code != 0 || !strings.Contains(proc.Stdout, "削除する  logs/"+oldLogName) || !exists(old) {
			t.Errorf("code = %d, stdout = %q, stderr = %q", proc.Code, proc.Stdout, proc.Stderr)
		}
	})
	t.Run("動かすにはターミナルが要る", func(t *testing.T) {
		t.Parallel()
		e := newPruneEnv(t)
		old := putAged(t, filepath.Join(e.logs, oldLogName), 30)
		proc := e.ccnavi(nil, "", "--entry", "sh", "user", "prune")
		if proc.Code == 0 || !strings.Contains(proc.Stderr, "ccnavi-user-prune.sh はユーザがターミナルから実行する必要があります") || !exists(old) {
			t.Errorf("code = %d, stderr = %q", proc.Code, proc.Stderr)
		}
	})
	t.Run("消して、そう言う", func(t *testing.T) {
		t.Parallel()
		e := newPruneEnv(t)
		old := putAged(t, filepath.Join(e.logs, oldLogName), 30)
		proc := e.ccnavi(pruneApproval, "", "user", "prune")
		if proc.Code != 0 || !strings.Contains(proc.Stdout, "削除した  logs/"+oldLogName) || exists(old) {
			t.Errorf("code = %d, stdout = %q, stderr = %q", proc.Code, proc.Stdout, proc.Stderr)
		}
	})
	t.Run("動かすものが無い", func(t *testing.T) {
		t.Parallel()
		e := newPruneEnv(t)
		proc := e.ccnavi(pruneApproval, "", "user", "prune")
		if proc.Code != 0 || !strings.Contains(proc.Stdout, "動かすものはありません。") {
			t.Errorf("code = %d, stdout = %q, stderr = %q", proc.Code, proc.Stdout, proc.Stderr)
		}
	})
	t.Run("1 日より短い保持は受けず、既定に戻す", func(t *testing.T) {
		t.Parallel()
		// いま書いたばかりの記録が消える形になるので受けない。
		e := newPruneEnv(t)
		fresh := putAged(t, filepath.Join(e.logs, oldLogName), 0.5)
		proc := e.ccnavi(map[string]string{"CCNAVI_GUARD_TICKET_APPROVAL": "disable", "CCNAVI_LOG_KEEP_DAYS": "0.0001"}, "", "user", "prune")
		if proc.Code != 0 || !strings.Contains(proc.Stderr, "下限") || !exists(fresh) {
			t.Errorf("code = %d, stderr = %q", proc.Code, proc.Stderr)
		}
	})
	t.Run("セッションの開始で後始末し、動かした数を記録に残す", func(t *testing.T) {
		t.Parallel()
		e := newPruneEnv(t)
		oldLog := putAged(t, filepath.Join(e.logs, oldLogName), 30)
		oldState := putAged(t, filepath.Join(e.state, sessionOld+".turn.json"), 30)
		mine := putAged(t, filepath.Join(e.state, sessionMe+".turn.json"), 30)
		payload := mustJSON(t, map[string]any{"hook_event_name": "SessionStart", "session_id": sessionMe, "source": "startup"})
		proc := e.ccnavi(nil, payload)
		if proc.Code != 0 || exists(oldLog) || exists(oldState) || !exists(mine) {
			t.Errorf("code = %d, stderr = %q", proc.Code, proc.Stderr)
		}
		f, err := os.Open(filepath.Join(e.logs, "decisions.jsonl"))
		if err != nil {
			t.Fatal(err)
		}
		defer f.Close()
		var rows []map[string]any
		scan := bufio.NewScanner(f)
		for scan.Scan() {
			var row map[string]any
			if err := json.Unmarshal(scan.Bytes(), &row); err != nil {
				t.Fatal(err)
			}
			rows = append(rows, row)
		}
		// 読めなかったツリーの注記（この一時リポジトリは git のワークツリーではない）は残し、後始末の結果はそのあとに続く。
		if len(rows) != 1 || rows[0]["event"] != "SessionStart" || !strings.HasSuffix(str(rows[0], "detail"), "pruned rotated=0 logs=1 state=1") {
			t.Errorf("rows = %v", rows)
		}
	})
}

// エージェントが Bash から `ccnavi --prune` を打つ形は、実行前チェックで止まること。実行ファイルのターミナル要求は、擬似ターミナル
// （`script -qc`）でも、チケット制御を切ったワークスペースでターミナル要求を切る変数を並べても抜けられる。チケット制御に依らず止める。
func TestPruneIsDeniedBeforeToolUseHoweverItIsCalled(t *testing.T) {
	t.Parallel()
	e := newPruneEnv(t)
	preToolUse := func(command, tool string, env map[string]string) string {
		payload := mustJSON(t, map[string]any{"hook_event_name": "PreToolUse", "session_id": "s1", "tool_name": tool, "tool_input": map[string]any{"command": command}})
		return e.ccnavi(env, payload).Stdout
	}
	for _, command := range []string{
		"ccnavi --prune",
		"script -qc 'ccnavi --prune' /dev/null",
		"CCNAVI_GUARD_TICKET_APPROVAL=disable CCNAVI_LOG_KEEP_DAYS=0 ccnavi --prune",
		"uv run python -m ccnavi --prune",
		".ccnavi/bin/linux-x64/ccnavi --json --prune",
		`bash -c "ccnavi --prune" x --preview`,
		`bash -c "ccnavi --prune" --preview`,
		"ccnavi --prune --preview; ccnavi --prune",
		`ccnavi --pr""une`,
		"ccnavi --prune --reason=--preview",
		"ccnavi --prune | tail -3",
	} {
		out := preToolUse(command, "Bash", nil)
		if !strings.Contains(out, "DENY_RECORDS_PRUNE") || !strings.Contains(out, `"deny"`) {
			t.Errorf("%q が止まらない: %s", command, out)
		}
	}
	if out := preToolUse(`& C:\tools\ccnavi.exe --prune`, "PowerShell", nil); !strings.Contains(out, "DENY_RECORDS_PRUNE") {
		t.Errorf("PowerShell の形が止まらない: %s", out)
	}
	for _, command := range []string{
		"ccnavi --prune --preview",
		"ccnavi --preview --prune",
		`bash -c "ccnavi --prune --preview"`,
		"git fetch --prune",
		"sh .ccnavi/scripts/ccnavi-git.sh fetch origin --prune",
		"echo ccnavi --prune",
		"git remote prune origin",
	} {
		if out := preToolUse(command, "Bash", nil); strings.Contains(out, "DENY_RECORDS_PRUNE") {
			t.Errorf("%q が止まった: %s", command, out)
		}
	}
	if out := preToolUse("ccnavi --prune", "Bash", map[string]string{"CCNAVI_GUARD_CORE_FILES": "disable"}); strings.Contains(out, "DENY_RECORDS_PRUNE") {
		t.Errorf("CCNAVI_GUARD_CORE_FILES=disable で切れない: %s", out)
	}
}
