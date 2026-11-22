package core

import (
	"fmt"
	"os"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/samples"
	"ccnavi/internal/harness/ticketkit"
	"ccnavi/internal/infra/yaml"
)

const scaffoldIdent = "feature-9-scaffold-demo"

// 雛形が置く空欄の目印。
var scaffoldPlaceholders = []string{
	"（ここにこの作業をする理由を書く）",
	"（ここにこの作業の目的を書く）",
	"（ここにこの作業の範囲を書く）",
	"（ここにこの作業の対象外を書く）",
	"（ここに完了をどう確かめるかを書く）",
	"（ここに計画の理由を書く）",
}

// 雛形の見出しを決める ticket-sections.yml（このリポジトリの共通レイヤーと同じ見出し）。
const scaffoldSections = `version: 1
parent:
  - name: 目的
    todo: （ここにこの作業の目的を書く）
  - name: 範囲
    todo: （ここにこの作業の範囲を書く）
  - name: 対象外
    todo: （ここにこの作業の対象外を書く）
  - name: 完了の確かめ方
    todo: （ここに完了をどう確かめるかを書く）
  - name: 計画の理由
    todo: （ここに計画の理由を書く）
child:
  - name: 担当範囲
  - name: 親の要求との対応
  - name: 確認の手順
`

// scaffold は `ccnavi ticket new` を試す木。cwd は識別子と同名のワークツリー。
type scaffold struct {
	*ticketkit.Tree
	tree string
	todo string
}

func newScaffold(t *testing.T, phases string) *scaffold {
	t.Helper()
	tr := ticketkit.New(t, ticketkit.Options{Phases: phases})
	ticketkit.Write(t, base.CommonPath(tr.Root, "sections"), scaffoldSections)
	tree := tr.Worktree(scaffoldIdent, "main")
	return &scaffold{Tree: tr, tree: tree, todo: tr.TodoPathIn(tree, scaffoldIdent)}
}

// newTicket は ticket new を打つ。issue と MR は渡さなければ既定を足す（親の提案は issue と MR のセット）。
func (s *scaffold) newTicket(plan string, extra ...string) run.Result {
	args := append([]string{"ticket", "new", scaffoldIdent}, extra...)
	for _, d := range [][2]string{{"--issue", "9"}, {"--mr", "5"}} {
		found := false
		for _, e := range extra {
			found = found || e == d[0]
		}
		if !found {
			args = append(args, d[0], d[1])
		}
	}
	args = append(args, "--title", "雛形の見本")
	if plan != "" {
		args = append(args, "--plan", plan)
	}
	return s.Ccnavi(append(args, "--cwd", s.tree)...)
}

func (s *scaffold) written(t *testing.T) string { return ticketkit.Read(t, s.todo) }

// fillPlaceholders は雛形の空欄の目印を、実際の内容に書き換える（目印が残ると承認の検査が止める）。
func (s *scaffold) fillPlaceholders(t *testing.T) {
	text := s.written(t)
	for _, mark := range scaffoldPlaceholders {
		text = strings.ReplaceAll(text, mark, "書いた内容")
	}
	ticketkit.Write(t, s.todo, text)
}

func (s *scaffold) commit() {
	s.Git(s.tree, "add", "-A")
	s.Git(s.tree, "commit", "--quiet", "-m", "scaffold")
}

// frontOf は本文の frontmatter を読む。
func frontOf(t *testing.T, text string) *yaml.Map {
	t.Helper()
	parts := strings.SplitN(text, "---\n", 3)
	if len(parts) < 3 {
		t.Fatalf("frontmatter が無い:\n%s", text)
	}
	data, err := yaml.Load(parts[1])
	if err != nil {
		t.Fatal(err)
	}
	return data.(*yaml.Map)
}

// 生成物が --lint と承認の確認（--agree --preview --verify）を通り、`design` を含む計画では `wip/design/*` が allow に入る（#316）。
// 提案について言う warn は「承認待ち」と「ワークツリーにチケットが無い」と「子の提案がありません」だけ。
func TestTheScaffoldPassesLintAndTheApprovalCheck(t *testing.T) {
	t.Parallel()
	s := newScaffold(t, samples.Phases)
	ticketkit.OK(t, s.newTicket("design,acceptance,implement"))
	front := frontOf(t, s.written(t))
	if front.Text("ticket") != scaffoldIdent || fmt.Sprint(front.Get("issue")) != "9" || front.Text("title") != "雛形の見本" {
		t.Errorf("frontmatter = %v", front)
	}
	s.fillPlaceholders(t)
	s.commit()
	lint := ticketkit.OK(t, s.Ccnavi("--lint", "--mode", "enable"))
	ticketkit.Has(t, lint.Stdout, "error 0 件")
	for _, line := range ticketkit.Lines(lint.Stdout) {
		if strings.HasPrefix(line, "warn") && strings.Contains(line, scaffoldIdent) &&
			!strings.Contains(line, "承認待ち") && !strings.Contains(line, "チケットがありません") && !strings.Contains(line, "子の提案がありません") {
			t.Errorf("想定外の warn: %s", line)
		}
	}
	ticketkit.OK(t, s.Ccnavi("--agree", "--preview", "--verify"))
}

// 承認の確認は、空欄の目印が残っていれば止め（終了コード 3）、見出しが崩れていれば warn だけで通す。--lint も同じ warn を言う。
func TestTheApprovalCheckStopsOnPlaceholdersAndWarnsOnAMissingHeading(t *testing.T) {
	t.Parallel()
	s := newScaffold(t, samples.Phases)
	ticketkit.OK(t, s.newTicket("research,design", "--mr", "5"))
	s.commit()
	verify := s.Ccnavi("--agree", "--preview", "--verify")
	if verify.Code != 3 {
		t.Errorf("終了コード %d（3 のはず）\n%s", verify.Code, ticketkit.Out(verify))
	}
	ticketkit.Has(t, ticketkit.Out(verify), "空欄の目印が残っている")
	// 目印を埋め、見出しを 1 つ崩すと、承認の確認は warn だけで通る
	s.fillPlaceholders(t)
	ticketkit.Write(t, s.todo, strings.Replace(s.written(t), "## 範囲\n", "### 範囲\n", 1))
	s.commit()
	verify = ticketkit.OK(t, s.Ccnavi("--agree", "--preview", "--verify"))
	ticketkit.Has(t, ticketkit.Out(verify), "見出しが欠けている")
	ticketkit.Has(t, ticketkit.Out(verify), "## 範囲")
	lint := ticketkit.OK(t, s.Ccnavi("--lint", "--mode", "enable"))
	ticketkit.Has(t, lint.Stdout, "見出しが欠けている")
}

// 別のリポジトリの課題（owner/repo#N）が書け、2 つのフェーズが共有する範囲（acceptance の tests/* と implement の tests/*）は
// 1 つにまとめる。計画は前のステップが各ステップに先行する一直線。
func TestAnIssueOfAnotherRepositoryAndAScopeSharedByTwoPhases(t *testing.T) {
	t.Parallel()
	s := newScaffold(t, samples.Phases)
	ticketkit.OK(t, s.newTicket("acceptance,implement,design", "--issue", "other/repo#12"))
	front := frontOf(t, s.written(t))
	if front.Text("issue") != "other/repo#12" {
		t.Errorf("issue = %v", front.Get("issue"))
	}
	allow, _ := front.List("allow")
	var globs []string
	for _, e := range allow {
		entry := e.(*yaml.Map)
		globs = append(globs, entry.Text("glob"))
		if entry.Text("match") != "Write|Edit" {
			t.Errorf("match = %v", entry.Get("match"))
		}
	}
	if strings.Join(globs, ",") != "tests/*,src/*,wip/design/*" {
		t.Errorf("allow = %v", globs)
	}
	plan, _ := front.List("plan")
	if len(plan) != 3 || plan[0] != "acceptance" {
		t.Fatalf("plan = %v", plan)
	}
	for i, want := range []struct{ typ, after string }{{"implement", "[1]"}, {"design", "[2]"}} {
		step := plan[i+1].(*yaml.Map)
		after, _ := step.List("after")
		if step.Text("type") != want.typ || fmt.Sprint(after) != want.after {
			t.Errorf("plan[%d] = %v", i+1, step)
		}
	}
}

func TestWithoutPhasesYmlTheScaffoldStopsAndSaysWhy(t *testing.T) {
	t.Parallel()
	s := newScaffold(t, samples.Phases)
	if err := os.Remove(base.ConfigPath(s.Root, "phases")); err != nil {
		t.Fatal(err)
	}
	r := ticketkit.Fails(t, s.newTicket("research,design"))
	ticketkit.Has(t, r.Stderr, "phases.yml")
	ticketkit.Has(t, r.Stderr, "9.7")
	if ticketkit.Exists(s.todo) {
		t.Error("提案を書いた")
	}
}

// `--scope`: inherit の型（quick-work）だけの計画でも、今回の範囲を渡して雛形を作れる。定義の範囲の上限を超える項は、作ったときと
// 承認の確認の両方で「承認しても書けない」と言う。上限の中・quick-work・パスを 1 つに決められない glob（前置が空か、
// ワイルドカードが後ろに続く）は言わない。
func TestScopeBeyondThePhaseLimitIsSaidAtCreationAndByVerify(t *testing.T) {
	t.Parallel()
	quickWork := "\n  quick-work:\n    kind: work\n    title: 小さい作業\n    review: true\n    scope: inherit\n"
	phases := strings.TrimRight(samples.Phases, "\n") + quickWork
	t.Run("上限を超える範囲は作成時と承認の確認で言う", func(t *testing.T) {
		t.Parallel()
		s := newScaffold(t, phases)
		r := ticketkit.OK(t, s.newTicket("implement", "--scope", "docs/**", "--mr", "5"))
		ticketkit.Has(t, r.Stdout, "承認しても書けない: `docs/**`")
		ticketkit.Has(t, r.Stdout, "quick-work")
		ticketkit.Has(t, r.Stdout, "案内: 承認しても書けない")
		ticketkit.Lacks(t, r.Stdout, "`src/*` は計画")
		s.fillPlaceholders(t)
		s.Git(s.tree, "add", "-A")
		s.Git(s.tree, "commit", "-qm", "proposal")
		verify := s.Ccnavi("--agree", "--preview", "--verify", "--cwd", s.tree)
		ticketkit.Has(t, ticketkit.Out(verify), "承認しても書けない: `docs/**`")
	})
	t.Run("上限の中・quick-work・決められない glob は言わない", func(t *testing.T) {
		t.Parallel()
		s := newScaffold(t, phases)
		for _, row := range []struct{ plan, glob string }{
			{"implement", "src/a.py"},
			{"quick-work", "docs/**"},
			{"implement", "**/*.py"},
			{"implement", "*.md"},
			{"implement", "src/**/*.py"},
		} {
			_ = os.Remove(s.todo)
			r := s.newTicket(row.plan, "--scope", row.glob)
			ticketkit.Lacks(t, r.Stdout, "承認しても書けない")
		}
	})
}
