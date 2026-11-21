package samples

import (
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"strings"
	"testing"
	"time"
)

// Chrome は Chrome 拡張（承認の画面）の入口。拡張の WASM と同じ要求を、手元向けの入口（go/cmd/ccnavi-chrome）に 1 件ずつ渡す。
//
// 置き場の名前は拡張と同じ値をテストの側に持つ（go/internal/entry/chrome と食い違えば、置き場を比べるテストが落ちる）。
const (
	// ChromeSchema は要求と答えの形の版（拡張の CCNAVI_SCHEMA）。
	ChromeSchema = 1
	// CompatFile・SettingsFile・CommonLayer は仮のツリーの中の置き場。
	CompatFile   = ".ccnavi/scripts/ccnavi-common.sh"
	SettingsFile = ".claude/settings.json"
	CommonLayer  = ".ccnavi/config/common"
)

// ChromeHandle は要求の JSON 1 件に答える。root は仮のツリーを組む場所（入口が中身を作り直す）。chromeExe は exe.ChromePath(t)（samples は exe を読めない層なので、呼び手が渡す）。
func ChromeHandle(t testing.TB, chromeExe, request, root string) string {
	t.Helper()
	if _, err := os.Stat(root); err != nil {
		if err := os.MkdirAll(root, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	ctx, cancel := context.WithTimeout(context.Background(), 120*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, chromeExe, root)
	cmd.Stdin = strings.NewReader(strings.ReplaceAll(request, "\n", " ") + "\n")
	var stdout, stderr strings.Builder
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	cmd.WaitDelay = 5 * time.Second
	if err := cmd.Run(); err != nil {
		t.Fatalf("ccnavi-chrome が失敗しました: %v: %s", err, stderr.String())
	}
	return strings.TrimSpace(stdout.String())
}

// ChromePlacement は置き場の答え（op: placement）。
func ChromePlacement(t testing.TB, chromeExe string) map[string]any {
	t.Helper()
	request, _ := json.Marshal(map[string]any{"schema": ChromeSchema, "op": "placement"})
	var answer struct {
		Placement map[string]any `json:"placement"`
	}
	if err := json.Unmarshal([]byte(ChromeHandle(t, chromeExe, string(request), os.DevNull)), &answer); err != nil {
		t.Fatal(err)
	}
	return answer.Placement
}
