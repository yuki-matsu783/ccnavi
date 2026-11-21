package small

import (
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
)

// hostKindsTable は tests/fixtures/host-kinds.json。実行ファイル（host）も同じ表を読む。
type hostKindsTable struct {
	Cases      []hostCase `json:"cases"`
	SHGoCases  []hostCase `json:"sh_go_cases"`
	Unreadable []string   `json:"unreadable"`
}

type hostCase struct {
	Origin  string `json:"origin"`
	Kind    string `json:"kind"`
	Host    string `json:"host"`
	Connect string `json:"connect"`
	Path    string `json:"path"`
	API     string `json:"api"`
	GraphQL string `json:"graphql"` // null は ""
	Problem string `json:"problem"` // 読めない理由（rc が 4 になる件）
}

// hostProbe は ccnavi_host_parse の答えを、`key=value` の行として読む。
const hostProbe = `. "$1"
ccnavi_host_parse "$2"
rc=$?
printf 'rc=%s\nkind=%s\nclass=%s\nconnect=%s\npath=%s\napi=%s\ngraphql=%s\nwhy=%s\n' \
  "$rc" "${ccnavi_h_kind:-}" "${ccnavi_h_class_host:-}" "${ccnavi_h_host:-}" "${ccnavi_h_path:-}" \
  "${ccnavi_h_api_base:-}" "${ccnavi_h_graphql_base:-}" "${ccnavi_h_parse_why:-}"
`

// hostParse は origin を ccnavi_host_parse に通し、出力の `key=value` の並びを返す。
func hostParse(t *testing.T, shell, origin string) map[string]string {
	t.Helper()
	res := runProgram(t, shell, run.Opts{}, "-c", hostProbe, "probe", hostScript(), origin)
	out := map[string]string{}
	for _, line := range strings.Split(res.Stdout, "\n") {
		if k, v, ok := strings.Cut(line, "="); ok {
			out[k] = v
		}
	}
	return out
}

// hostScript は、できあがりの姿の ccnavi-common-host.sh。
func hostScript() string { return filepath.Join(base.ScriptsDir(), "ccnavi-common-host.sh") }

// TestHostKindsMatchTheTable は、origin の分類と接続先（ccnavi_host_parse）が表どおりであること。
// 読めない形は止まる（rc が 0 でない）。
func TestHostKindsMatchTheTable(t *testing.T) {
	t.Parallel()
	shell := firstShell(t)
	var tab hostKindsTable
	loadFixture(t, "host-kinds.json", &tab)
	table := append(append([]hostCase{}, tab.Cases...), tab.SHGoCases...)
	for _, c := range table {
		t.Run(c.Origin, func(t *testing.T) {
			t.Parallel()
			got := hostParse(t, shell, c.Origin)
			if c.Problem != "" {
				if got["rc"] != "4" {
					t.Fatalf("rc = %q, want 4", got["rc"])
				}
				if !strings.Contains(got["why"], c.Problem) {
					t.Errorf("why = %q, want %q を含む", got["why"], c.Problem)
				}
				return
			}
			if got["rc"] != "0" {
				t.Fatalf("rc = %q, want 0 (why %q)", got["rc"], got["why"])
			}
			want := map[string]string{
				"kind": c.Kind, "class": c.Host, "connect": c.Connect,
				"path": c.Path, "api": c.API, "graphql": c.GraphQL,
			}
			for k, w := range want {
				if got[k] != w {
					t.Errorf("%s = %q, want %q", k, got[k], w)
				}
			}
		})
	}
}

// TestUnreadableOriginsStop は、読めない形の origin で止まる（rc が 0 でない）。
func TestUnreadableOriginsStop(t *testing.T) {
	t.Parallel()
	shell := firstShell(t)
	var tab hostKindsTable
	loadFixture(t, "host-kinds.json", &tab)
	for _, origin := range tab.Unreadable {
		t.Run(origin, func(t *testing.T) {
			t.Parallel()
			if rc := hostParse(t, shell, origin)["rc"]; rc == "0" {
				t.Errorf("読めない origin が通った: %q", origin)
			}
		})
	}
}
