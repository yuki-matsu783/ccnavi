package failsafe

import (
	"os"
	"path/filepath"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/tickets/testkit"
)

// 1 件は、root からの cwd（"-" ならプロセスの cwd に任せて空を渡す）、ルート（root からの相対。空なら root）、
// ツール、tool_input、通してよいか。
type fsCase struct {
	Cwd   string            `json:"cwd"`
	Root  string            `json:"root"`
	Tool  string            `json:"tool"`
	Input map[string]string `json:"input"`
	Want  bool              `json:"-"`
}

func c(cwd, tool, field, value string, want bool) fsCase {
	return fsCase{Cwd: cwd, Tool: tool, Input: map[string]string{field: value}, Want: want}
}

// credentials は通常の判定の builtin:credentials が止める綴り。判定できないときも通さない。
var credentials = []string{
	".env", ".env.example", ".aws/x", "x.pem", "x.pem.bak", "x.key.json", "secrets/a", "credentials/token", "credentials.json",
}

func cases(root string) []fsCase {
	out := []fsCase{
		// 報告のツールは通し、知らないツール・書き込み・PowerShell は通さない。
		{Tool: config.ToolSubagentHandback, Want: true},
		{Tool: config.ToolStructuredOutput, Want: true},
		{Tool: "UnknownTool"},
		{Tool: ""},
		c("", config.ToolWrite, "file_path", "/tmp/x", false),
		c("", config.ToolAgent, "description", "d", false),
		c("", config.ToolPowerShell, "command", "pwd", false),
		// 読み取りは最低限の deny に当たらなければ通す。
		c("", config.ToolRead, "file_path", "/tmp/notes.md", true),
		c("", config.ToolRead, "file_path", "/home/u/.env", false),
		c("", config.ToolRead, "file_path", "/r/.git/config", false),
		c("", config.ToolRead, "file_path", "/r/logs/state/ops/a.json", false),
		c("", config.ToolGrep, "path", "/home/u/.ssh/id_rsa", false),
		// 解けないパス（NUL を含む）は通さない。
		c("", config.ToolRead, "file_path", "/tmp/a\x00b", false),
		// cwd から解いて見る。
		c(".git", config.ToolRead, "file_path", "config", false),
		c(".git", config.ToolRead, "file_path", "../README.md", true),
		c("", config.ToolRead, "file_path", "docs/../.git/config", false),
		{Cwd: "logs/state/ops", Tool: config.ToolGrep, Input: map[string]string{"pattern": "x", "glob": "*.json"}},
		c("logs/state/ops", config.ToolGlob, "pattern", "*.json", false),
		{Tool: config.ToolGrep, Input: map[string]string{"pattern": "x", "glob": "*.md"}, Want: true},
		// 探す場所を省いた Grep・Glob はプロセスの cwd を探す。
		c("-", config.ToolGrep, "pattern", "x", true),
		c("-", config.ToolGlob, "pattern", "x", true),
		// Glob の pattern は探す先そのもの。
		c("", config.ToolGlob, "pattern", "/home/u/.ssh/*", false),
		c("", config.ToolGlob, "pattern", "../.ssh/*", false),
		c("", config.ToolGlob, "pattern", "**/id_rsa", false),
		c("", config.ToolGlob, "pattern", "docs/*.md", true),
		// シェルは狭い許可の表だけ。
		c(".git", config.ToolBash, "command", "cat config", false),
		c(".git", config.ToolBash, "command", "ls", false),
		c("", config.ToolBash, "command", "ls", true),
		c("", config.ToolBash, "command", "cat docs/../.git/config", false),
		c("", config.ToolBash, "command", "notes/id", false),
		c("", config.ToolBash, "command", "cat notes/id", false),
		c("", config.ToolRead, "file_path", "notes/id", false),
		c("", config.ToolBash, "command", "sh "+filepath.Join(root, ".ccnavi", "scripts", "ccnavi-ticket.sh")+" status", true),
		c("", config.ToolBash, "command", "sh "+filepath.Join(root, ".ccnavi", "scripts", "ccnavi-ticket.sh")+" --help", true),
		c("", config.ToolBash, "command", "sh "+filepath.Join(root, ".ccnavi", "scripts", "ccnavi-ticket.sh")+" start i0001", false),
		c("docs", config.ToolBash, "command", "sh .ccnavi/scripts/ccnavi-ticket.sh status", false),
	}
	for _, cmd := range []struct {
		text string
		want bool
	}{
		{"pwd", true}, {"ls", true}, {"ls docs", true}, {"cat README.md", true}, {"head wip/a.md", true},
		{"sh .ccnavi/scripts/ccnavi-ticket.sh status", true},
		{"sh /w/.ccnavi/scripts/ccnavi-ticket.sh --help", false},
		{"sh /tmp/evil/.ccnavi/scripts/ccnavi-ticket.sh status", false},
		{"sh ../x/.ccnavi/scripts/ccnavi-ticket.sh status", false},
		{"wc --files0-from=.env", false}, {"cat -n README.md", false}, {"cat /dev/zero", false}, {"ls -R", false},
		{"cat .git/config", false}, {"cat .GIT/config", false}, {"cat logs/state/ops/a.json", false},
		{"cat key.pem", false}, {"cat ~/.aws/credentials", false}, {"cat credentials.json", false},
		{"cat secrets/prod.txt", false}, {"less README.md", false}, {"rg --pre cmd x .", false}, {"echo hi", false},
		{"grep x README.md", false}, {"cat a.md > b.md", false}, {"cat $(echo a)", false}, {"cat *.md", false},
		{"ls && rm x", false}, {"cat .env", false}, {"sh .ccnavi/scripts/ccnavi-ticket.sh start i0001", false},
		{"cat src/keyboard.py", true}, {"cat docs/credentials-policy.md", true},
	} {
		out = append(out, c("", config.ToolBash, "command", cmd.text, cmd.want))
	}
	for _, rel := range credentials {
		for _, spelled := range []string{rel, "./" + rel, filepath.Join(root, rel)} {
			out = append(out,
				c("", config.ToolRead, "file_path", spelled, false),
				c("sub", config.ToolRead, "file_path", spelled, false),
				c("", config.ToolGrep, "path", spelled, false),
				c("", config.ToolGrep, "glob", spelled, false),
				c("", config.ToolGlob, "path", spelled, false),
				c("", config.ToolBash, "command", "cat "+spelled, false),
			)
		}
	}
	// ルートより上の祖先の名前（secrets/・.aws/）では止めない。ルートの中の名前では止める。
	for _, parent := range []string{"secrets", ".aws"} {
		base := parent + "/proj"
		for _, k := range []fsCase{
			c(base, config.ToolRead, "file_path", "a.md", true),
			c(base, config.ToolRead, "file_path", filepath.Join(root, base, "a.md"), true),
			c(base, config.ToolGrep, "pattern", "x", true),
			c(base, config.ToolBash, "command", "cat a.md", true),
			c(base, config.ToolBash, "command", "ls", true),
			c(base, config.ToolRead, "file_path", ".git/config", false),
			c(base, config.ToolRead, "file_path", filepath.Join(root, base, ".git", "config"), false),
			c(base, config.ToolBash, "command", "cat .git/config", false),
		} {
			k.Root = base
			out = append(out, k)
		}
	}
	return out
}

const failsafeOracle = `
import json, os, sys
from ccnavi.hook import failsafe
from ccnavi.infra import hookio
c = json.load(sys.stdin)
root = c["root"]
os.chdir(root)
out = []
for k in c["cases"]:
    here = root if k["cwd"] == "" else ("" if k["cwd"] == "-" else os.path.join(root, k["cwd"]))
    payload = hookio.Input(event="PreToolUse", tool_name=k["tool"], tool_input=k["input"] or {}, cwd=here)
    ws = os.path.join(root, k["root"]) if k["root"] else root
    out.append(failsafe.is_safe(payload, ws))
print(json.dumps(out))
`

func workspace(t *testing.T) string {
	t.Helper()
	root := testkit.TempRoot(t)
	for _, dir := range []string{".git", "logs/state/ops", "sub", "docs", "keep/.ssh", "secrets/proj/.git", ".aws/proj/.git"} {
		if err := os.MkdirAll(filepath.Join(root, dir), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	// 守る場所を指す名前の変わる symlink（notes → keep/.ssh）。
	if err := os.Symlink(filepath.Join(root, "keep", ".ssh"), filepath.Join(root, "notes")); err != nil {
		t.Skip("symlink を作れない環境")
	}
	return root
}

func input(k fsCase, root string) hookio.Input {
	in := hookio.Input{Event: hookio.PreToolUse, ToolName: k.Tool, ToolInput: pyjson.NewObject()}
	for key, v := range k.Input {
		in.ToolInput.Set(key, v)
	}
	switch k.Cwd {
	case "":
		in.Cwd = root
	case "-":
		in.Cwd = ""
	default:
		in.Cwd = filepath.Join(root, k.Cwd)
	}
	return in
}

func TestIsSafe(t *testing.T) {
	root := workspace(t)
	all := cases(root)
	got := make([]bool, len(all))
	t.Run("表", func(t *testing.T) {
		t.Chdir(root)
		for i, k := range all {
			ws := root
			if k.Root != "" {
				ws = filepath.Join(root, k.Root)
			}
			got[i] = IsSafe(input(k, root), ws)
			if got[i] != k.Want {
				t.Errorf("IsSafe(%s %v, cwd=%q, root=%q) = %v, want %v", k.Tool, k.Input, k.Cwd, k.Root, got[i], k.Want)
			}
		}
	})
	// Python の答えとも 1 件ずつ合わせる（ルートの場所が毎回変わるので写しは持たない）。
	want := testkit.Answers[[]bool](t, "", failsafeOracle, map[string]any{"root": root, "cases": all})
	for i := range all {
		if got[i] != want[i] {
			t.Errorf("%s %v（cwd=%q）: Go=%v Python=%v", all[i].Tool, all[i].Input, all[i].Cwd, got[i], want[i])
		}
	}
}

// 判定できない間の許可リストのうち、省いた探す場所は、cwd が .git の中なら通さない。
func TestOmittedSearchPlaceInsideGit(t *testing.T) {
	root := workspace(t)
	t.Chdir(filepath.Join(root, ".git"))
	for _, tool := range []string{config.ToolGrep, config.ToolGlob} {
		in := hookio.Input{Event: hookio.PreToolUse, ToolName: tool, ToolInput: pyjson.NewObject("pattern", "x")}
		if IsSafe(in, root) {
			t.Errorf("%s: .git の中の省略を通した", tool)
		}
	}
}
