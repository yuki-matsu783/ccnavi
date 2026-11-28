package core

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/ticketkit"
)

// 識別子を親のブランチ名にできるかを `--lint` が warn で言う（承認も判定も変えない）。
//
//  1. 新規の提案の識別子の形: ブランチ名として安全でない（`..`・`.lock`・`.` で終わる）、統合先や保護されたブランチの名前、
//     `<先頭の語>-<番号>-<slug>` の形でない、番号の重なり、既にあるブランチと同じ名前
//  2. 承認済み・クローズした識別子には 1 を言わない（もう変えられないので、言っても常態になるだけ）
//  3. 大文字小文字だけが違う識別子
//  4. 末尾が `-<2 桁>` の親の識別子（`-<2 桁>-<2 桁>` なら子の形そのもの、`-<2 桁>` だけなら子の識別子の途中と紛れる）
//
// 識別子の形の見本表（ブランチ名・課題からの識別子・通し番号・先頭の語の設定）は Go の ids・ticket・config のテストが見る。
const branchNameRule = "（親のブランチ名の規則）"

// lint は `--lint` を外から呼び、親のブランチ名の規則の行だけを返す。
func (w *idWorkspace) lint(env map[string]string) []string {
	w.t.Helper()
	rules := ticketkit.Write(w.t, filepath.Join(w.ws, "rules.yml"), `{"version": 1}`)
	return lintLines(w.t, w.ws, rules, env)
}

func lintLines(t *testing.T, ws, rules string, env map[string]string) []string {
	t.Helper()
	r := runLint(t, ws, rules, env)
	var lines []string
	for _, line := range ticketkit.Lines(r) {
		if strings.Contains(line, branchNameRule) {
			lines = append(lines, line)
		}
	}
	return lines
}

func TestNewProposalsAreNamedByTheBranchNameRule(t *testing.T) {
	t.Parallel()
	w := newIDWorkspace(t)
	w.propose("i0131")
	w.proposeWithIssue("feature-132-x", 132)
	w.propose("develop")
	w.propose("feature-1-fix..it")
	lines := w.lint(nil)
	joined := strings.Join(lines, "\n")
	for _, line := range lines {
		if !strings.HasPrefix(line, "warn: (ticket): ") {
			t.Errorf("warn の形でない: %s", line)
		}
	}
	for _, want := range []string{"i0131: 親の識別子は", "develop: 識別子が統合先", "feature-1-fix..it: 識別子に `..`", "develop: 親の識別子は"} {
		ticketkit.Has(t, joined, want)
	}
	ticketkit.Lacks(t, joined, "feature-132-x:")
	ticketkit.Lacks(t, joined, "通し番号は")
}

// 言わない形: 日本語の識別子・承認済みとクローズした識別子（もう変えられない）・承認済みの親の改版・1 桁で終わる親・本物の子。
func TestTheBranchNameRuleStaysSilentWhereItShould(t *testing.T) {
	t.Parallel()
	for _, row := range []struct {
		name   string
		set    func(w *idWorkspace)
		filter string
	}{
		{"日本語の識別子", func(w *idWorkspace) { w.propose("feature-64-統合先の解決") }, ""},
		// 既存の i0055 などは issue: を持たない。承認済みの識別子は変えられないので言わない。
		{"承認済みとクローズした識別子", func(w *idWorkspace) { w.place("doing", "i0055", ""); w.place("done", "i0060", "") }, ""},
		{"承認済みの親の改版は新規でない", func(w *idWorkspace) { w.place("doing", "i0055", ""); w.propose("i0055") }, "i0055:"},
		{"1 桁で終わる親", func(w *idWorkspace) { w.place("done", "abc-1", ""); w.place("done", "web-i0012", "") }, ""},
		{"本物の子", func(w *idWorkspace) { w.place("doing", "abc", ""); w.place("doing", "abc-01-01", "abc") }, ""},
	} {
		t.Run(row.name, func(t *testing.T) {
			t.Parallel()
			w := newIDWorkspace(t)
			row.set(w)
			for _, line := range w.lint(nil) {
				if row.filter == "" || strings.Contains(line, row.filter) {
					t.Errorf("言わないはずの warn: %s", line)
				}
			}
		})
	}
}

func TestDuplicateNumbersAreNamed(t *testing.T) {
	t.Parallel()
	w := newIDWorkspace(t)
	w.place("done", "feature-62-z", "")
	w.place("doing", "feature-63-a", "")
	w.propose("feature-63-b")
	w.propose("hotfix-62-c")
	w.propose("feature-64-d")
	joined := strings.Join(w.lint(nil), "\n")
	ticketkit.Has(t, joined, "feature-63-b: 識別子の番号 63 が feature-63-a と重なる")
	ticketkit.Has(t, joined, "hotfix-62-c: 識別子の番号 62 が feature-62-z と重なる")
	ticketkit.Lacks(t, joined, "feature-64-d:")
	ticketkit.Lacks(t, joined, "feature-63-a:")
}

func TestThePrefixListComesFromTheEnvironment(t *testing.T) {
	t.Parallel()
	w := newIDWorkspace(t)
	w.propose("spike-1-x")
	if n := len(w.lint(nil)); n != 1 {
		t.Errorf("既定の先頭の語では 1 件のはず: %d", n)
	}
	if lines := w.lint(map[string]string{"CCNAVI_BRANCH_PREFIXES": "feature,spike"}); len(lines) != 0 {
		t.Errorf("設定した先頭の語は通る: %v", lines)
	}
	lines := w.lint(map[string]string{"CCNAVI_BRANCH_PREFIXES": "spike,release"})
	if len(lines) != 1 {
		t.Fatalf("lines = %v", lines)
	}
	ticketkit.Has(t, lines[0], "CCNAVI_BRANCH_PREFIXES の release は")
}

func TestAnExistingBranchIsNamed(t *testing.T) {
	t.Parallel()
	w := newIDWorkspace(t)
	w.commitEmpty()
	ticketkit.Git(t, w.ws, "branch", "feature-5-統合")
	w.propose("feature-5-統合")
	w.propose("feature-6-new")
	lines := w.lint(nil)
	if len(lines) != 1 {
		t.Fatalf("lines = %v", lines)
	}
	ticketkit.Has(t, lines[0], "feature-5-統合: 親のブランチ名と同じブランチ（feature-5-統合）が既にある")
}

// repoWithOrigin は origin を持ち、`origin/HEAD` が branch を指すリポジトリを path に作る。
func repoWithOrigin(t *testing.T, path, branch string) {
	t.Helper()
	if err := os.MkdirAll(path, 0o755); err != nil {
		t.Fatal(err)
	}
	bare := filepath.Join(base.TempDir(t), "origin.git")
	ticketkit.Git(t, path, "init", "--quiet", "-b", branch)
	ticketkit.Git(t, path, "commit", "--quiet", "--allow-empty", "-m", "x")
	ticketkit.Git(t, filepath.Dir(bare), "init", "--quiet", "--bare", bare)
	ticketkit.Git(t, path, "remote", "add", "origin", bare)
	ticketkit.Git(t, path, "push", "--quiet", "origin", branch)
	ticketkit.Git(t, path, "remote", "set-head", "origin", branch)
}

// 統合先はそのリポジトリの origin/HEAD だけで決まる。ワークスペースが main でも、projects/app（trunk）向けのチケットは trunk を予約する。
func TestEachRepositoryReservesItsOwnDefaultBranch(t *testing.T) {
	t.Parallel()
	t.Run("プロジェクトごと", func(t *testing.T) {
		t.Parallel()
		ws := base.TempDir(t)
		repoWithOrigin(t, ws, "main")
		repoWithOrigin(t, filepath.Join(ws, "projects", "app"), "trunk")
		rules := ticketkit.Write(t, filepath.Join(ws, "rules.yml"), `{"version": 1}`)
		ticketkit.Write(t, filepath.Join(ws, "projects", "app", "wip", "proposals", "todo", "trunk.md"), idTicketText("trunk", "", 0, false))
		ticketkit.Write(t, filepath.Join(ws, "wip", "proposals", "todo", "Trunk-ws.md"), idTicketText("Trunk-ws", "", 0, false))
		var lines []string
		for _, line := range lintLines(t, ws, rules, nil) {
			if strings.Contains(line, "統合先") {
				lines = append(lines, line)
			}
		}
		if len(lines) != 1 {
			t.Fatalf("lines = %v", lines)
		}
		ticketkit.Has(t, lines[0], "trunk: 識別子が統合先の名前（trunk）")
	})
	t.Run("ワークスペース", func(t *testing.T) {
		t.Parallel()
		ws := base.TempDir(t)
		repoWithOrigin(t, ws, "trunk")
		rules := ticketkit.Write(t, filepath.Join(ws, "rules.yml"), `{"version": 1}`)
		ticketkit.Write(t, filepath.Join(ws, "wip", "proposals", "todo", "trunk.md"), idTicketText("trunk", "", 0, false))
		var lines []string
		for _, line := range lintLines(t, ws, rules, nil) {
			if strings.Contains(line, "統合先") {
				lines = append(lines, line)
			}
		}
		if len(lines) != 1 {
			t.Errorf("lines = %v", lines)
		}
	})
}

func TestIDsThatDifferOnlyInCase(t *testing.T) {
	t.Parallel()
	w := newIDWorkspace(t)
	w.place("done", "login", "")
	w.proposeWithIssue("Login", 0) // 形の warn も出るが、ここでは大文字小文字だけを見る
	if !strings.Contains(strings.Join(w.lint(nil), "\n"), "Login と login は大文字小文字だけが違う") {
		t.Error("大文字小文字だけが違う識別子を名指ししない")
	}
}

// 末尾が `-<2 桁>` の親の識別子。`-<2 桁>-<2 桁>` は子の形そのもので、子として扱われるので変える。`-<2 桁>` だけなら、右から 2 階層を
// 剥がすので親の割り出しは誤らないが、別の親の子の途中（`<親>-<フェーズ>`）と紛れる。古い形の子と同じ綴りなので warn にする。
func TestAParentShapedLikeAChildOrEndingInTwoDigits(t *testing.T) {
	t.Parallel()
	for _, row := range []struct {
		ident   string
		wants   []string
		unwants []string
	}{
		{"abc-01-02", []string{"abc-01-02 は親なのに、識別子が子の形", "abc の子として扱われる", "末尾を `-<2 桁>` にしない"}, nil},
		{"abc-01", []string{"abc-01 は親なのに、識別子の末尾が `-<2 桁>`", "子の識別子の途中"}, []string{"の子として扱われる"}},
	} {
		t.Run(row.ident, func(t *testing.T) {
			t.Parallel()
			w := newIDWorkspace(t)
			w.place("done", row.ident, "")
			lines := w.lint(nil)
			if len(lines) != 1 {
				t.Fatalf("lines = %v", lines)
			}
			for _, want := range row.wants {
				ticketkit.Has(t, lines[0], want)
			}
			for _, not := range row.unwants {
				ticketkit.Lacks(t, lines[0], not)
			}
		})
	}
}
