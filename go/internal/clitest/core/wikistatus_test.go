package core

import (
	"path/filepath"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/samples"
)

// `ccnavi --learnings` の受入テスト。パターンの状態の決め方と wiki の読み方は go/internal/records/wiki の go test が見る。

func learningsRoot(t *testing.T) string {
	root := emptyRoot(t)
	mkdir(t, filepath.Join(root, ".claude"))
	writeFile(t, root, "docs/wiki/patterns/p1.md", samples.Pattern([]string{"a", "b", "c"}, "rule"))
	writeFile(t, root, "docs/wiki/patterns/p2.md", samples.Pattern([]string{"a"}, "skill"))
	writeFile(t, root, "docs/wiki/patterns/broken.md", "x\n")
	writeFile(t, root, "docs/wiki/impact/w.md", samples.Impact("w", "承認待ち", []samples.Count{{Name: "p2", N: 1}}, "", samples.DefaultTarget))
	return root
}

// learnings は `--learnings` を起こす。CCNAVI_LEARNING の既定の切り（run の既定）は外す。
func learnings(t *testing.T, root string, env map[string]string, args ...string) run.Result {
	t.Helper()
	return run.Ccnavi(t, append([]string{"--root", root, "--learnings"}, args...), run.Opts{Dir: base.Root(), Env: env, Unset: []string{"CCNAVI_LEARNING"}})
}

func TestLearningsJSON(t *testing.T) {
	t.Parallel()
	root := learningsRoot(t)
	done := learnings(t, root, nil, "--json")
	if done.Code != 0 {
		t.Fatalf("code = %d: %s", done.Code, done.Stderr)
	}
	data := decode(t, done.Stdout)
	rows := map[string]map[string]any{}
	for _, r := range data["patterns"].([]any) {
		m := r.(map[string]any)
		rows[m["name"].(string)] = m
	}
	var pending, broken []any
	for _, p := range data["pending"].([]any) {
		pending = append(pending, p.(map[string]any)["proposal"])
	}
	for _, b := range data["broken"].([]any) {
		broken = append(broken, b.(map[string]any)["file"])
	}
	byTarget := data["by_target"].(map[string]any)["rule"].(map[string]any)
	if data["threshold"] != 3.0 || data["candidates"] != 1.0 || data["reached"] != 1.0 ||
		rows["p1"]["status"] != "候補" || rows["p1"]["reached"] != true || rows["p2"]["status"] != "提案中" ||
		len(pending) != 1 || pending[0] != "w" || len(broken) != 1 || broken[0] != "docs/wiki/patterns/broken.md" ||
		byTarget["candidates"] != 1.0 {
		t.Errorf("data = %v", data)
	}
}

func TestLearningsThresholdFromEnv(t *testing.T) {
	t.Parallel()
	root := learningsRoot(t)
	for value, want := range map[string]float64{"1": 1, "zero": 3} {
		data := decode(t, learnings(t, root, map[string]string{"CCNAVI_LEARNING_SOURCES": value}, "--json").Stdout)
		if data["threshold"] != want {
			t.Errorf("CCNAVI_LEARNING_SOURCES=%s: threshold = %v, want %v", value, data["threshold"], want)
		}
	}
}

func TestLearningsTextNamesTheBrokenFile(t *testing.T) {
	t.Parallel()
	done := learnings(t, learningsRoot(t), nil)
	mustContain(t, "stdout", done.Stdout, "broken.md", "承認待ちの提案")
}

func TestLearningsFailsWithOtherRoutesAndWithAMissingRoot(t *testing.T) {
	t.Parallel()
	root := learningsRoot(t)
	if done := run.Ccnavi(t, []string{"--root", root, "--learnings", "--lint"}, run.Opts{Dir: base.Root()}); done.Code == 0 {
		t.Error("--lint と組み合わせられた")
	}
	if done := run.Ccnavi(t, []string{"--root", filepath.Join(root, "nope"), "--learnings"}, run.Opts{Dir: base.Root()}); done.Code == 0 {
		t.Error("無いルートが通った")
	}
}
