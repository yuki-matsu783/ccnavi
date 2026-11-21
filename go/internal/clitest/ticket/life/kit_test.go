package life

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/samples"
	"ccnavi/internal/harness/ticketkit"
)

// 学びの蓄積を有効にする・切る環境（run.Opts.Env の差分）。
var (
	learnOn  = map[string]string{"CCNAVI_LEARNING": "enable"}
	learnOff = map[string]string{"CCNAVI_LEARNING": "disable"}
)

// ccn は env を足して ccnavi を起こす（tr.Ccnavi は環境を渡せない）。
func ccn(tr *ticketkit.Tree, env map[string]string, args ...string) run.Result {
	tr.T.Helper()
	return tr.Call(ticketkit.Call{Env: env}, args...)
}

// merge は子を親のブランチへ合流し、write-and-push の finish の代わりの記録（merged_sha）を足す。
func merge(tr *ticketkit.Tree, child string) {
	tr.T.Helper()
	tr.Parent("merge", "--quiet", "--no-edit", child)
	samples.RecordMerge(tr.T, tr.ParentTree, child)
}

// writeIn は tree の下の相対パス rel（`/` 区切り）に text を書く。
func writeIn(t testing.TB, tree, rel, text string) string {
	t.Helper()
	return ticketkit.Write(t, filepath.Join(tree, filepath.FromSlash(rel)), text)
}

func readIn(t testing.TB, tree, rel string) string {
	t.Helper()
	return ticketkit.Read(t, filepath.Join(tree, filepath.FromSlash(rel)))
}

func existsIn(tree, rel string) bool {
	return ticketkit.Exists(filepath.Join(tree, filepath.FromSlash(rel)))
}

// listDir はディレクトリの名前の並び（無ければ空）。
func listDir(dir string) []string {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil
	}
	var names []string
	for _, e := range entries {
		names = append(names, e.Name())
	}
	return names
}

func containsLine(text, sub string) []string {
	var out []string
	for _, line := range strings.Split(text, "\n") {
		if strings.Contains(line, sub) {
			out = append(out, line)
		}
	}
	return out
}

// mkdirRename は dst の親を作って src を動かす。
func mkdirRename(src, dst string) error {
	if err := os.MkdirAll(filepath.Dir(dst), 0o755); err != nil {
		return err
	}
	return os.Rename(src, dst)
}

// ---- 親子の木（tests/ticket の AuthorityHarness）

const childName = "i0001-01-01"

// authority は、親 i0001 と子 i0001-01-01 を承認して子のワークツリーを作り、着手した木（Cached）。フェーズ定義は samples.Phases。
// 親の計画は research・design。t.Parallel() は呼ぶ前に済ませる。
func authority(t *testing.T) *ticketkit.Tree {
	t.Helper()
	return ticketkit.Cached(t, "authority", ticketkit.Options{Phases: samples.Phases}, func(tr *ticketkit.Tree) {
		tr.PhaseFamily(samples.Plan("research", "design"))
		tr.Propose(childName, samples.ChildText(childName, "i0001", 1, []string{"wip/research/*"}), "")
		tr.CommitParent("approval-request child")
		ticketkit.OK(tr.T, tr.Approve())
		tr.RunChild(childName)
	})
}

// writeTo は tree から rel（`/` 区切り）へ Write するときの hook の結果。
func writeTo(tr *ticketkit.Tree, tree, rel string) run.Result {
	tr.T.Helper()
	return tr.Hook("PreToolUse", "Write", tree, map[string]any{"file_path": filepath.Join(tree, filepath.FromSlash(rel)), "content": "x\n"})
}

// jsonOf は ccnavi の出力の JSON を v に読む。
func jsonOf(t testing.TB, text string, v any) {
	t.Helper()
	if err := json.Unmarshal([]byte(text), v); err != nil {
		t.Fatalf("JSON でない: %v\n%s", err, text)
	}
}

// lintProblems は `--lint --json` の problems。
type problem struct {
	Severity string `json:"severity"`
	Where    string `json:"where"`
	Detail   string `json:"detail"`
}

func lintProblems(tr *ticketkit.Tree) []problem {
	tr.T.Helper()
	var out struct {
		Problems []problem `json:"problems"`
	}
	jsonOf(tr.T, tr.Ccnavi("--lint", "--json").Stdout, &out)
	return out.Problems
}

// gitDir は dir の .git の置き場（ワークツリーなら本体の worktrees/<名前>）。
func gitDir(tr *ticketkit.Tree, dir string) string {
	tr.T.Helper()
	g := strings.TrimSpace(tr.Git(dir, "rev-parse", "--git-dir"))
	if !filepath.IsAbs(g) {
		g = filepath.Join(dir, g)
	}
	return g
}

// startRebase は dir を「rebase の途中」に見せる（git が途中の操作として数える印を置く）。
func startRebase(tr *ticketkit.Tree, dir string) {
	tr.T.Helper()
	g := gitDir(tr, dir)
	head := strings.TrimSpace(tr.Git(dir, "rev-parse", "HEAD"))
	if err := os.MkdirAll(filepath.Join(g, "rebase-merge"), 0o755); err != nil {
		tr.T.Fatal(err)
	}
	ticketkit.Write(tr.T, filepath.Join(g, "HEAD"), head+"\n")
}

// fixtureJSON は結果ファイル（host: fixture）を root に書いて、パスを返す。
func fixtureJSON(tr *ticketkit.Tree) string {
	tr.T.Helper()
	return ticketkit.Write(tr.T, filepath.Join(tr.Root, "fixture.json"), `{"host": "fixture", "mr": {"number": 7, "url": "u/7"}}`)
}

// agreePreview は `--agree --preview --json` の答え。
type preview struct {
	Batch []struct {
		Ticket string `json:"ticket"`
	} `json:"batch"`
	Rejected []struct {
		Problems []string `json:"problems"`
	} `json:"rejected"`
}

func agreePreview(tr *ticketkit.Tree) preview {
	tr.T.Helper()
	var p preview
	jsonOf(tr.T, tr.Ccnavi("--agree", "--preview", "--json").Stdout, &p)
	return p
}

func (p preview) batchHas(name string) bool {
	for _, b := range p.Batch {
		if b.Ticket == name {
			return true
		}
	}
	return false
}

func (p preview) rejectedHas(word string) bool {
	for _, r := range p.Rejected {
		if strings.Contains(strings.Join(r.Problems, " "), word) {
			return true
		}
	}
	return false
}

// baseCommonRules は共通レイヤーのルールのパス。
func baseCommonRules(root string) string { return base.CommonPath(root, "rules") }
