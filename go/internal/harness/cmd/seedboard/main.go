// seedboard は、実画面の確認用に検証用コピーへチケットの見本を置く（親 2・子 4）。使ったら消す。
package main

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	"ccnavi/internal/harness/samples"
)

const (
	famA = "feature-101-ログイン画面"
	famB = "fix-102-日本語の文字化け"
)

var root string

func git(cwd string, args ...string) {
	full := append([]string{"-c", "user.name=t", "-c", "user.email=t@t"}, args...)
	cmd := exec.Command("git", full...)
	cmd.Dir = cwd
	if out, err := cmd.CombinedOutput(); err != nil {
		fmt.Fprintf(os.Stderr, "git %v: %v\n%s", args, err, out)
		os.Exit(1)
	}
}

// put はチケットを親のワークツリーに置く。
func put(rel, text string) {
	fam := famB
	if strings.Contains(rel, famA) {
		fam = famA
	}
	path := filepath.Join(root, ".claude", "worktrees", fam, rel)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		panic(err)
	}
	if err := os.WriteFile(path, []byte(text), 0o644); err != nil {
		panic(err)
	}
}

func parent(name, title string, plan []string, issue int, phases string) string {
	text := samples.ParentText(name, samples.Plan(plan...), samples.ParentOpts{Issue: issue, Copy: phases})
	text = strings.Replace(text, "title: 親", "title: "+title, 1)
	return strings.Replace(text, "reason: t", "reason: 画面の見た目が変わるため", 1)
}

func child(name, par string, phase int, title string, scope []string, review samples.Flag) string {
	text := samples.ChildTextReview(name, par, phase, scope, review)
	return strings.Replace(text, "title: 子 "+name, "title: "+title, 1)
}

func main() {
	if len(os.Args) != 2 {
		fmt.Fprintln(os.Stderr, "使い方: seedboard <検証用コピー>")
		os.Exit(2)
	}
	root = os.Args[1]
	data, err := os.ReadFile(filepath.Join(root, ".ccnavi/config/common/phases.yml"))
	if err != nil {
		panic(err)
	}
	phases := string(data)
	for _, fam := range []string{famA, famB} { // 親ごとにブランチとワークツリー（無いと承認も着手もできない）
		git(root, "branch", fam)
		git(root, "worktree", "add", ".claude/worktrees/"+fam, fam)
	}
	started := `started_at: "2026-10-20T09:00:00+09:00"`
	done := `completed_at: "2026-10-20T11:00:00+09:00"`
	empty := func(s string, repl ...string) string {
		s = strings.Replace(s, `started_at: ""`, repl[0], 1)
		if len(repl) > 1 {
			s = strings.Replace(s, `completed_at: ""`, repl[1], 1)
		}
		return s
	}
	a := parent(famA, "ログイン画面を追加する", []string{"research", "design", "implement"}, 101, phases)
	put(".ccnavi/approved/doing/"+famA+".md", empty(a, started))
	c1 := child(famA+"-01-01", famA, 1, "ログイン方式の調査", []string{"wip/research/*"}, samples.Off)
	put(".ccnavi/approved/done/"+famA+"-01-01.md", empty(c1, started, done))
	c2 := child(famA+"-02-01", famA, 2, "ログイン画面の設計", []string{"wip/design/*"}, samples.On)
	put(".ccnavi/approved/doing/"+famA+"-02-01.md", empty(c2, started))
	put("wip/proposals/todo/"+famA+"-03-01.md",
		child(famA+"-03-01", famA, 3, "ログイン画面の実装", []string{"src/*", "tests/*"}, samples.On))
	put("wip/proposals/todo/"+famB+".md",
		parent(famB, "CSV 出力の日本語が文字化けする不具合を直す", []string{"quick-work"}, 102, phases))
	put("wip/proposals/todo/"+famB+"-01-01.md",
		child(famB+"-01-01", famB, 1, "文字コードを UTF-8（BOM 付き）にそろえる", []string{"src/*", "tests/*"}, samples.On))
	for _, fam := range []string{famA, famB} {
		wt := filepath.Join(root, ".claude", "worktrees", fam)
		git(wt, "add", "-A")
		git(wt, "commit", "-qm", "チケット: "+fam)
	}
	// 設計の子は着手済み（子のワークツリーを切る）
	git(root, "branch", famA+"-02-01", famA)
	git(root, "worktree", "add", ".claude/worktrees/"+famA+"-02-01", famA+"-02-01")
}
