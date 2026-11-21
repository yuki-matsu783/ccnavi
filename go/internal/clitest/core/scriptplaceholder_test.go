package core

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
)

// できあがりの rules.yml・CCNAVI.md・docs が、直接作業のワークツリーを ccnavi-worktree.sh で切る案内を書くこと。
// 保護対象（rules.yml・CCNAVI.md）は、下書きがあれば下書きの、無ければ本物の「できあがりの姿」を読む（base.Final）。
// `{script:<名前>}` の差し込みと案内のコマンドの引用は go/internal/policy/rules・go/internal/infra/config の go test が見る。

func oneLine(s any) string { text, _ := s.(string); return strings.Join(strings.Fields(text), " ") }

func TestFinalFilesPointAtTheWorktreeScriptAndTheDocs(t *testing.T) {
	t.Parallel()
	rules := loadYAML(t, base.Final(".ccnavi/config/common/rules.yml"))

	text := oneLine(ruleByID(t, rules, "workspace-root")["message"])
	mustContain(t, "workspace-root の文面", text,
		"{script:ccnavi-worktree.sh} <名前>",
		"worktree-merge.md「直接作業のワークツリーを取り込む」",
		"worktree.md「直接作業のワークツリーの名前」",
		"cd projects/<プロジェクト名> && {script:ccnavi-worktree.sh} <名前>")
	mustNotContain(t, "workspace-root の文面", text, "ff-only")

	hint := oneLine(ruleByID(t, rules, "ccnavi-git-worktree-add")["additionalContextOnce"])
	mustContain(t, "worktree add の案内", hint, "{script:ccnavi-worktree.sh} <名前>")
	mustNotContain(t, "worktree add の案内（手の worktree add に誘わない）", hint, "--no-track", "ff-only")
}

func TestNoFFOnlyCuttingProcedureIsLeftInTheDocsOrRules(t *testing.T) {
	t.Parallel()
	paths := []string{base.Final(".ccnavi/config/common/rules.yml")}
	docs := filepath.Join(base.Root(), ".ccnavi", "docs")
	entries, err := os.ReadDir(docs)
	if err != nil {
		t.Fatal(err)
	}
	for _, e := range entries {
		if strings.HasSuffix(e.Name(), ".md") {
			paths = append(paths, filepath.Join(docs, e.Name()))
		}
	}
	for _, p := range paths {
		mustNotContain(t, filepath.Base(p), readFile(t, p), "merge --ff-only")
	}
}

func TestTestCommandSpeaksTheQuotedScriptAndTheDocs(t *testing.T) {
	t.Parallel()
	ws := filepath.Join(emptyRoot(t), "a b")
	writeFile(t, ws, base.CommonRelpath("rules"), readFile(t, base.Final(".ccnavi/config/common/rules.yml")))
	mkdir(t, filepath.Join(ws, "src"))
	done := run.Ccnavi(t, []string{"--root", ws, "--test", "Write", filepath.ToSlash(ws) + "/src/x.py", "--json"}, run.Opts{Dir: ws})
	response := str(decode(t, done.Stdout), "response")
	// 空白を含むルートは "..." で引用する（go/internal/infra/config の go test も見る）。
	mustContain(t, "response", response, `sh "`+filepath.ToSlash(ws)+`/.ccnavi/scripts/ccnavi-worktree.sh"`, "worktree-merge.md")
	mustNotContain(t, "response", response, "ff-only")
}

// CCNAVI.md の該当行は 1 行の差し替えで、元の案内より 40 字以上は長くならない。
func TestCCNaviMDLineIsAOneLineReplacement(t *testing.T) {
	t.Parallel()
	text := readFile(t, base.Final(".ccnavi/CCNAVI.md"))
	var line string
	for _, l := range strings.Split(text, "\n") {
		if strings.Contains(l, "{{worktree_sh}}") {
			line = l
			break
		}
	}
	const old = "  - 編集する前にワークツリー（{{worktrees}}/<名前>）を切り、その中で編集する。" +
		"切り方は ccnavi-git.sh の --help（worktree add）。直接作業の名前は <先頭の語>-<番号>-<slug>（.ccnavi/docs/worktree.md）。"
	if line == "" || len([]rune(line))-len([]rune(old)) >= 40 {
		t.Errorf("該当行 = %q", line)
	}
	mustNotContain(t, "CCNAVI.md", text, "--help（worktree add）")
	if n := strings.Count(text, "{{worktree_sh}}"); n != 1 {
		t.Errorf("{{worktree_sh}} が %d 個", n)
	}
}
