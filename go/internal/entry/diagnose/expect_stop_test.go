package diagnose

import (
	"encoding/json"
	"path/filepath"
	"testing"

	"ccnavi/internal/tickets/testkit"
)

// 期待する判定に Stop を置ける。`match: Stop` のルールが (stop) にヒットし、食い違いにも読み飛ばしにもならない。
func TestExpectationsCanCheckStop(t *testing.T) {
	root := rulesWorkspace(t)
	path := testkit.Put(t, filepath.Join(root, "expect-stop.yml"),
		"allow:\n  - tool: Stop\n    subject: (stop)\n    why: 振り返り\n")
	got := runGo(t, root, diagCase{Kind: "expect", Path: path, JSON: true})
	if got.Code != 0 {
		t.Fatalf("終了コード %d: %s", got.Code, got.Stdout)
	}
	var body struct {
		Mismatches   int `json:"mismatches"`
		Skipped      int `json:"skipped"`
		Expectations []struct {
			Rules []struct {
				ID string `json:"id"`
			} `json:"rules"`
		} `json:"expectations"`
	}
	if err := json.Unmarshal([]byte(got.Stdout), &body); err != nil {
		t.Fatal(err)
	}
	if body.Mismatches != 0 || body.Skipped != 0 || len(body.Expectations) != 1 ||
		len(body.Expectations[0].Rules) != 1 || body.Expectations[0].Rules[0].ID != "stop-every" {
		t.Errorf("%s", got.Stdout)
	}
}
