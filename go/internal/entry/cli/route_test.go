package cli

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/tickets/testkit"
)

// 経路は routeOf の 1 か所で決まる。payload を読まない経路（--lint・--templates を含む）は hook の経路にしない。
func TestRouteOf(t *testing.T) {
	for _, c := range []struct {
		args []string
		want route
	}{
		{nil, routeHook},
		{[]string{"--mode", "dry-run"}, routeHook},
		{[]string{"--version"}, routeVersion},
		{[]string{"--lint"}, routeLint},
		{[]string{"--lint", "--prune"}, routeLint},
		{[]string{"--templates"}, routeTemplates},
		{[]string{"--learnings"}, routeLearnings},
		{[]string{"--docs"}, routeDocs},
		{[]string{"--explain"}, routeDiagnosis},
		{[]string{"--suggest"}, routeDiagnosis},
		{[]string{"--prune"}, routePrune},
		{[]string{"--plan-order", "p", "--json"}, routePlanOrder},
		{[]string{"--agree", "--preview"}, routeAgree},
		{[]string{"ticket", "status"}, routeCommand},
		{[]string{"--reviewed", "1"}, routeCommand},
	} {
		parser := newParser()
		ns, err := parser.Parse(c.args)
		if err != nil {
			t.Fatalf("%v: %v", c.args, err)
		}
		if got := routeOf(opts{ns, parser}); got != c.want {
			t.Errorf("%v: %d（期待 %d）", c.args, got, c.want)
		}
	}
}

// panicOnce は最初の書き込みで panic する書き先。受け皿が書く 2 回目からは書ける。
type panicOnce struct {
	bytes.Buffer
	done bool
}

func (w *panicOnce) Write(p []byte) (int, error) {
	if !w.done {
		w.done = true
		panic("書けない")
	}
	return w.Buffer.Write(p)
}

// hook の経路は、設定を読んだ後・判定の前（フラグを設定に重ねるところ）で落ちても、終了コード 1（Claude Code が
// PreToolUse を通す答え）で終えず、内部エラーの応答で止める。
func TestHookRouteCrashBeforeJudgingFailsClosed(t *testing.T) {
	root := testkit.TempRoot(t)
	testkit.CopyMessages(t, root)
	stderr := &panicOnce{}
	var stdout bytes.Buffer
	// --log は診断の外では捨てて stderr で言う。その最初の書き込みで落とす。
	code := Run(strings.NewReader(""), &stdout, stderr, []string{"--root", root, "--log", "x"})
	if code != config.ExitBlock || !strings.Contains(stderr.String(), "internal-error") {
		t.Errorf("code %d: %s", code, stderr.String())
	}
}

// `ops mark-sent` は sh（ccnavi-review.sh）が打つ 7 語の形で通り、目印で見つけた投稿を送り済みにする。
func TestOpsMarkSentTakesTheShellForm(t *testing.T) {
	state := t.TempDir()
	var out, errOut bytes.Buffer
	words := []string{"begin", "request", "br", "0", "5", "marker", "comment:comment"}
	if code := opsCommand(&out, &errOut, &config.Settings{State: state}, words); code != 0 {
		t.Fatalf("begin: %d %s", code, errOut.String())
	}
	id := strings.TrimSpace(out.String())
	out.Reset()
	words = []string{"mark-sent", "request", "br", "5", "marker", "comment", "http://u"}
	if code := opsCommand(&out, &errOut, &config.Settings{State: state}, words); code != 0 {
		t.Fatalf("mark-sent: %d %s", code, errOut.String())
	}
	data, err := os.ReadFile(filepath.Join(state, "ops", id+".json"))
	if err != nil {
		t.Fatal(err)
	}
	var record struct {
		Stages []struct{ State, URL string }
	}
	if err := json.Unmarshal(data, &record); err != nil {
		t.Fatal(err)
	}
	if len(record.Stages) != 1 || record.Stages[0].State != "sent" || record.Stages[0].URL != "http://u" {
		t.Errorf("記録: %s", data)
	}
}
