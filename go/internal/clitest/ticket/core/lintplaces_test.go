package core

import (
	"os"
	"path/filepath"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/ticketkit"
)

// 「複数の場所にある」の数え方（リポジトリをまたぐ側）。
//
// 同じ識別子が複数のツリーに在るのは普通の形（同じリポジトリの中の carry-over は state_test.go が見る）。ここで見るのは逆側で、
// リポジトリをまたいだ衝突は免除してはいけないこと。プロジェクトは自分の git を持ち、識別子はユーザが選ぶ短い連番なので、
// プロジェクトが独立に振ればぶつかる。これは同じチケットではなく違うものなので、コミットの遅れでは説明が付かない。ツリーごとの
// 免除をここに適用すると、承認がどちらの実体のものか分からないまま誰も気づかなくなる。
const crossRepoTicket = "---\nversion: 1\nticket: i0001\ntitle: %s\n" +
	"ccnavi_approved: {approved_at: \"2026-01-01T00:00:00Z\", source_tree: \"\", source_path: p}\n" +
	"allow:\n- {glob: \"src/*\", match: \"Write|Edit\"}\n---\n"

type crossRepo struct {
	t        *testing.T
	ws       string
	rules    string
	projects string
}

func newCrossRepo(t *testing.T) *crossRepo {
	t.Helper()
	ws := base.TempDir(t)
	ticketkit.Write(t, filepath.Join(ws, ".claude", "keep"), "")
	ticketkit.Git(t, ws, "init", "--quiet", "-b", "main")
	ticketkit.Write(t, filepath.Join(ws, ".gitignore"), "/projects/\n/.claude/\n")
	ticketkit.Write(t, filepath.Join(ws, "src", "keep.py"), "print(1)\n")
	ticketkit.Git(t, ws, "add", "-A")
	ticketkit.Git(t, ws, "commit", "--quiet", "-m", "init")
	return &crossRepo{t: t, ws: ws, rules: ticketkit.Write(t, filepath.Join(ws, "rules.yml"), `{"version": 1}`), projects: filepath.Join(ws, "projects")}
}

// project はプロジェクトを 1 つ置く。自分の git を持ち、承認済みチケットを 1 枚抱える。
func (c *crossRepo) project(name, state string) string {
	root := filepath.Join(c.projects, name)
	if err := os.MkdirAll(root, 0o755); err != nil {
		c.t.Fatal(err)
	}
	ticketkit.Git(c.t, root, "init", "--quiet", "-b", "main")
	ticketkit.Write(c.t, filepath.Join(root, "src", "keep.py"), "print(1)\n")
	ticketkit.Write(c.t, filepath.Join(root, ".ccnavi", "approved", state, "i0001.md"), sprintf(crossRepoTicket, name+" のチケット"))
	ticketkit.Git(c.t, root, "add", "-A")
	ticketkit.Git(c.t, root, "commit", "--quiet", "-m", "init")
	return root
}

func (c *crossRepo) lint() run.Result {
	return run.Ccnavi(c.t, []string{
		"--root", c.ws, "--rules", c.rules, "--projects", c.projects,
		"--guard-core-files", "disable", "--restore-if-deny", "disable", "--guard-ticket-approval", "disable",
		"--lint", "--mode", "enable",
	})
}

// 別々のプロジェクトが同じ識別子を使っていたら、状態が揃っていても「同じもののコピー」と見ずに報告する。衝突した片方が
// ワークツリーの側（親のワークツリーにだけ在る合流前の形）にあっても、1 つにまとめて何も言わない形にしない。
func TestTheSameIDInTwoProjectsIsAnError(t *testing.T) {
	t.Parallel()
	t.Run("状態が同じでも別のもの", func(t *testing.T) {
		t.Parallel()
		c := newCrossRepo(t)
		c.project("app", "done")
		c.project("lib", "done")
		r := ticketkit.Fails(t, c.lint())
		ticketkit.Has(t, r.Stdout, "i0001 が複数のリポジトリにある")
		ticketkit.Has(t, r.Stdout, "app:done")
		ticketkit.Has(t, r.Stdout, "lib:done")
	})
	t.Run("片方がワークツリーの側にある", func(t *testing.T) {
		t.Parallel()
		c := newCrossRepo(t)
		c.project("app", "doing")
		lib := c.project("lib", "doing")
		// lib の側は、親のワークツリーにだけ在る形にする（合流前）。ワークツリーの名前は識別子と同じなので、
		// ここで「正とするツリー」の規則を適用すると、もう片方のプロジェクトの実体が気づかないうちに消える。
		if err := os.Remove(filepath.Join(lib, ".ccnavi", "approved", "doing", "i0001.md")); err != nil {
			t.Fatal(err)
		}
		ticketkit.Git(t, lib, "add", "-A")
		ticketkit.Git(t, lib, "commit", "--quiet", "-m", "not merged yet")
		ticketkit.Git(t, lib, "worktree", "add", "--quiet", filepath.Join(c.ws, ".claude", "worktrees", "i0001"), "-b", "i0001", "HEAD~1")
		r := ticketkit.Fails(t, c.lint())
		ticketkit.Has(t, r.Stdout, "i0001 が複数のリポジトリにある")
		ticketkit.Has(t, r.Stdout, "app:doing")
		ticketkit.Has(t, r.Stdout, "lib/i0001:doing")
	})
}

// 1 つのプロジェクトに 1 枚在るだけの、いちばん普通の形は error にならない。
func TestOneProjectAloneIsNotAnError(t *testing.T) {
	t.Parallel()
	c := newCrossRepo(t)
	c.project("app", "done")
	r := c.lint()
	ticketkit.Lacks(t, r.Stdout, "複数のリポジトリにある")
	ticketkit.Lacks(t, r.Stdout, "複数の場所にある")
}

// `projects/` の置き場がワークスペースのソースとぶつかったときの `--lint --json` の通し。ワークスペースの git が `projects/` の
// 下のファイルを追跡していると、名前を逃がす手段が無い（置き場は固定）。`(projects)` の warn を 1 件だけ出す。プロジェクトが 0 件でも、
// `.gitignore` に入っていても出す（追跡が先に決まる）。VS Code 拡張（projects-render.ts）がこの句で始まる苦情を見分けてバナーに出す。
// 置き場の苦情の場面の表（追跡・載せ忘れ・無視の有無・置き場の形・変な `--projects`）は Go の見本
// （go/internal/entry/lint/testdata/projects*.json）と places_test.go の表が見る。
func TestATrackedFileUnderProjectsIsNamedEvenWhenIgnored(t *testing.T) {
	t.Parallel()
	const lead = "`projects/` はワークスペースの git が追跡している"
	ws := base.TempDir(t)
	ticketkit.Write(t, filepath.Join(ws, ".claude", "keep"), "")
	ticketkit.Git(t, ws, "init", "--quiet", "-b", "main")
	ticketkit.Write(t, base.CommonPath(ws, "rules"), `{"version": 1}`)
	ticketkit.Write(t, filepath.Join(ws, "src", "keep.py"), "print(1)\n")
	ticketkit.Write(t, filepath.Join(ws, ".gitignore"), "/.claude/\n")
	ticketkit.Write(t, filepath.Join(ws, "projects", "foo.txt"), "ワークスペース自身のソース\n")
	// `-f`: `.gitignore` に入れた後でも追跡させる（追跡は無視の設定より先に決まる）。
	ticketkit.Git(t, ws, "add", "-f", "--", ".gitignore", "src/keep.py", "projects/foo.txt")
	ticketkit.Git(t, ws, "commit", "--quiet", "-m", "init")
	about := func() []map[string]any {
		r := run.Ccnavi(t, []string{"--root", ws, "--lint", "--json", "--mode", "enable"})
		var found []map[string]any
		for _, p := range ticketkit.ParseJSON(t, r.Stdout)["problems"].([]any) {
			if m := p.(map[string]any); m["where"] == "(projects)" {
				found = append(found, m)
			}
		}
		return found
	}
	for _, ignore := range []string{"", "/projects/\n"} {
		ticketkit.Write(t, filepath.Join(ws, ".gitignore"), "/.claude/\n"+ignore)
		found := about()
		if len(found) != 1 {
			t.Fatalf("(projects) の苦情は 1 件のはず: %v", found)
		}
		if detail, _ := found[0]["detail"].(string); len(detail) < len(lead) || detail[:len(lead)] != lead {
			t.Errorf("detail = %q", detail)
		}
	}
}
