package start

import (
	"strings"
	"testing"

	"ccnavi/internal/infra/golden"
	"ccnavi/internal/tickets/testkit"
)

// guidanceCase は testdata/guidance.json の 1 件。答えは、ccnavi-start.sh の stop_host を sh のまま走らせた出力から取った
// （ワークスペースルートは <root> に置き換えてある）。
type guidanceCase struct {
	Name string            `json:"name"`
	Args map[string]string `json:"args"`
	Out  string            `json:"out"`
	Code int               `json:"code"`
}

func guidanceArgs(m map[string]string) GuidanceArgs {
	return GuidanceArgs{Stage: m["stage"], Why: m["why"], Kind: m["kind"], Path: m["path"], Host: m["host"], What: m["what"],
		Number: m["number"], Issue: m["issue"], MR: m["mr"], Branch: m["branch"], StopBranch: m["stop-branch"],
		StopMR: m["stop-mr"], StopTarget: m["stop-target"], StopTitle: m["stop-title"], StopWT: m["stop-wt"],
		Orig: m["orig"], Total: m["total"]}
}

func TestGuidanceMatchesTheShAnswers(t *testing.T) {
	root := testkit.RepoRoot(t)
	for _, c := range golden.Load[[]guidanceCase](t, "guidance") {
		t.Run(c.Name, func(t *testing.T) {
			out, code := Guidance(root, guidanceArgs(c.Args))
			out = strings.ReplaceAll(out, root, "<root>")
			// sh の stop_host は案内を出したあと終了コード 4 で止まる。Guidance は答えを返すだけ（0）。
			if out != c.Out || code != ok || c.Code != 4 {
				t.Errorf("Go: %q（%d）\n見本: %q（%d）", out, code, c.Out, c.Code)
			}
		})
	}
}

func TestGuidanceIsUnreadableWithoutTheMessageFiles(t *testing.T) {
	if out, code := Guidance(t.TempDir(), GuidanceArgs{Stage: "mcp-args", Kind: "github"}); code != unreadable || out != "" {
		t.Errorf("%q（%d）", out, code)
	}
}
