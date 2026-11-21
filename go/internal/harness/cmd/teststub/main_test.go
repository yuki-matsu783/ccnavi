package main

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/samples"
	"ccnavi/internal/tickets/model/oplog"
)

func TestMain(m *testing.M) { base.Main(m) }

// 知らないサブコマンドは使い方を言って 2 で終わる。名前は表（commands）から並べる。
func TestUnknownSubcommandSaysTheUsage(t *testing.T) {
	t.Parallel()
	cases := [][]string{{}, {"nope"}, {"gitlab-host"}, {"github-host", "nope"}}
	for _, args := range cases {
		var stderr bytes.Buffer
		if code := run(args, strings.NewReader(""), &bytes.Buffer{}, &stderr); code != 2 || !strings.Contains(stderr.String(), "使い方: teststub ") {
			t.Errorf("%v: code = %d, stderr = %q", args, code, stderr.String())
		}
	}
	for name := range commands {
		var stderr bytes.Buffer
		run(nil, strings.NewReader(""), &bytes.Buffer{}, &stderr)
		if !strings.Contains(stderr.String(), name) {
			t.Errorf("使い方に %s が無い: %q", name, stderr.String())
		}
	}
}

// userform は実行ファイルの入口の検べと同じ。通れば何も出さず 0、断れば文を stderr に出して 2（標準出力は使わない）。
func TestUserformThroughTheStub(t *testing.T) {
	t.Parallel()
	cases := []struct {
		args []string
		code int
		msg  string
	}{
		{[]string{"userform", "--entry", "sh", "user", "agree"}, 0, ""},
		{[]string{"userform", "--entry", "web", "user", "agree"}, 2, "--entry に渡せるのは"},
		{[]string{"userform", "--entry", "sh", "ticket", "status"}, 2, "user の入口と一緒に"},
	}
	for _, c := range cases {
		var stdout, stderr bytes.Buffer
		if code := run(c.args, strings.NewReader(""), &stdout, &stderr); code != c.code || stdout.Len() != 0 || !strings.Contains(stderr.String(), c.msg) {
			t.Errorf("%v: code = %d, stdout = %q, stderr = %q", c.args, code, stdout.String(), stderr.String())
		}
	}
}

// oprecord は記録を 1 つ書き、その操作ID を入れた計画を書く。成功は 0 で何も出さない。引数が足りなければ 1 と文。
func TestOprecordThroughTheStub(t *testing.T) {
	t.Parallel()
	dir := base.TempDir(t)
	state, plan, spec := filepath.Join(dir, "state"), filepath.Join(dir, "plan.json"), filepath.Join(dir, "spec.json")
	body := `{"begin": {"kind": "decide", "repo": {"host": "gitlab"}, "parent": "i0001", "phase": 1, "mr": 1, "stages": [["reply", "reply"]]},
		"plan": {"version": 1}}`
	if err := os.WriteFile(spec, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	var stdout, stderr bytes.Buffer
	if code := run([]string{"oprecord", state, plan, spec}, strings.NewReader(""), &stdout, &stderr); code != 0 || stdout.Len() != 0 || stderr.Len() != 0 {
		t.Fatalf("code = %d, stdout = %q, stderr = %q", code, stdout.String(), stderr.String())
	}
	ids := samples.RecordIDs(state)
	if len(ids) != 1 {
		t.Fatalf("記録は 1 つのはず: %v", ids)
	}
	if r, err := oplog.Load(state, ids[0]); err != nil || r == nil || r.Parent != "i0001" {
		t.Errorf("Load: %+v, %v", r, err)
	}
	if raw, err := os.ReadFile(plan); err != nil || string(raw) != `{"version": 1, "op": "`+ids[0]+`"}` {
		t.Errorf("計画 = %q, %v", raw, err)
	}
	stderr.Reset()
	if code := run([]string{"oprecord", state}, strings.NewReader(""), &stdout, &stderr); code != 1 || stderr.Len() == 0 {
		t.Errorf("引数が足りなければ 1 と文: %d %q", code, stderr.String())
	}
}
