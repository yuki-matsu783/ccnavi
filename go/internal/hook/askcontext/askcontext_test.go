package askcontext

import (
	"encoding/json"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/infra/env"
	"ccnavi/internal/tickets/testkit"
)

const both = "## 前提・背景\nファイルを消す必要がある。\n## 想定結果\n許可すると消える。断ると残る。"

const toolUseID = "toolu_test"

func names() []string {
	out := make([]string, len(sections))
	for i, s := range sections {
		out[i] = s.name
	}
	return out
}

func TestHeadings(t *testing.T) {
	for _, text := range []string{
		"## 背景", "**背景**", "【前提・背景】", "前提:", "- 背景：理由", "  背景", "### 1. 前提・背景", "　背景", "## 背景\r",
	} {
		if slices.Contains(absentSections(text+"\n想定結果:"), "前提・背景") {
			t.Errorf("%q が見出しに当たらない", text)
		}
	}
	for _, text := range []string{"## 想定結果", "選んだ時の結果:"} {
		if slices.Contains(absentSections("背景:\n"+text), "想定結果") {
			t.Errorf("%q が見出しに当たらない", text)
		}
	}
	// 本文の中の語と別の見出しは当たらない。
	for _, text := range []string{"背景を説明すると、こうです", "この背景は大きい", "## 結果", "選んだ結果", "前提を置くと"} {
		if got := absentSections(text); !slices.Equal(got, names()) {
			t.Errorf("%q: %v", text, got)
		}
	}
	if got := absentSections("## 背景\nx"); !slices.Equal(got, []string{"想定結果"}) {
		t.Errorf("無い節だけ: %v", got)
	}
}

func assistant(text string, extra ...any) map[string]any {
	row := map[string]any{"type": "assistant", "message": map[string]any{"content": []any{map[string]any{"type": "text", "text": text}}}}
	for i := 0; i+1 < len(extra); i += 2 {
		row[extra[i].(string)] = extra[i+1]
	}
	return row
}

func prompt(text string) map[string]any {
	return map[string]any{"type": "user", "message": map[string]any{"content": text}}
}

// call はこの呼び出し自身の tool_use の行。あれば、直前の発言は書き込み済み。
func call() map[string]any {
	block := map[string]any{"type": "tool_use", "id": toolUseID, "name": "X", "input": map[string]any{}}
	return map[string]any{"type": "assistant", "message": map[string]any{"content": []any{block}}}
}

func toolResult() map[string]any {
	return map[string]any{"type": "user", "message": map[string]any{"content": []any{map[string]any{"type": "tool_result", "content": "ok"}}}}
}

func transcript(t *testing.T, rows ...map[string]any) string {
	t.Helper()
	var b strings.Builder
	for _, row := range rows {
		data, err := json.Marshal(row)
		if err != nil {
			t.Fatal(err)
		}
		b.Write(data)
		b.WriteString("\n")
	}
	return testkit.Put(t, filepath.Join(t.TempDir(), "transcript.jsonl"), b.String())
}

func TestStopText(t *testing.T) {
	t.Setenv(env.AskContextWaitSeconds, "0.3")
	root := t.TempDir()
	testkit.CopyMessages(t, root)
	for _, c := range []struct {
		name  string
		rows  []map[string]any
		stops bool
	}{
		{"節が無ければ止まる", []map[string]any{prompt("お願いします"), assistant("消してよいですか？"), call()}, true},
		{"節が揃えば通る", []map[string]any{prompt("お願いします"), assistant(both), call()}, false},
		{"2 つの発言に分かれていても揃えば通る", []map[string]any{prompt("p"), assistant("## 背景\n理由"), assistant("## 選んだ時の結果\n消える"), call()}, false},
		{"見出しのあとに別のツールを呼んでも同じ発言の中なら数える", []map[string]any{prompt("p"), assistant(both), toolResult(), assistant("消してよいですか？"), call()}, false},
		{"ツールの結果をはさんでもユーザの発言より前は数えない", []map[string]any{assistant(both), prompt("どうぞ"), toolResult(), assistant("消してよいですか？"), call()}, true},
		{"U+2028 で行を割らない", []map[string]any{prompt("p"), assistant(strings.ReplaceAll(both, "消す", "消す ")), call()}, false},
		{"ユーザの発言より前の発言は数えない", []map[string]any{assistant(both), prompt("どうぞ"), call()}, true},
		{"サイドチェーンの発言は数えない", []map[string]any{prompt("p"), assistant(both, "isSidechain", true), call()}, true},
		{"自分の行がまだ無く、待っても現れなければ通す", []map[string]any{prompt("p"), assistant("消してよいですか？")}, false},
	} {
		got := StopText(root, transcript(t, c.rows...), toolUseID)
		if (got != "") != c.stops {
			t.Errorf("%s: %q", c.name, got)
		}
	}
	text := StopText(root, transcript(t, prompt("p"), assistant("消してよいですか？"), call()), toolUseID)
	if !strings.Contains(text, "「前提・背景」・「想定結果」") {
		t.Errorf("無い節の名前: %s", text)
	}
	if text := StopText(root, transcript(t, prompt("p"), assistant("## 背景\n理由"), call()), toolUseID); !strings.Contains(text, "「想定結果」の節") || strings.Contains(text, "「前提・背景」の節") {
		t.Errorf("片方だけ: %s", text)
	}
	// 判定できないときは検査しない。
	for name, got := range map[string]string{
		"記録のパスが無い":        StopText(root, "", toolUseID),
		"記録が読めない":         StopText(root, filepath.Join(root, "missing.jsonl"), toolUseID),
		"tool_use_id が無い": StopText(root, transcript(t, prompt("p"), assistant("x"), call()), ""),
	} {
		if got != "" {
			t.Errorf("%s: %q", name, got)
		}
	}
}

func TestWaitsForTheLateText(t *testing.T) {
	t.Setenv(env.AskContextWaitSeconds, "2")
	path := transcript(t, prompt("p"))
	go func() {
		time.Sleep(150 * time.Millisecond)
		f, err := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0)
		if err != nil {
			return
		}
		defer f.Close()
		for _, row := range []map[string]any{assistant(both), call()} {
			data, _ := json.Marshal(row)
			f.Write(append(data, '\n'))
		}
	}()
	if got := missing(path, toolUseID, time.Now().Add(2*time.Second)); got != nil {
		t.Errorf("遅れて書かれた発言を待たなかった: %v", got)
	}
	// 自分の行があって見出しが無ければ、待たずに止める。
	start := time.Now()
	stop := transcript(t, prompt("p"), assistant("x"), call())
	if got := missing(stop, toolUseID, time.Now().Add(2*time.Second)); len(got) != 2 || time.Since(start) > time.Second {
		t.Errorf("待った・止めなかった: %v %v", got, time.Since(start))
	}
}
