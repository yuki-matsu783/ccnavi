package core

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/run"
)

// 元のプロジェクトが無いワークツリーを --lint と --explain が名指しすることの受入テスト。
// 元のプロジェクトが無いワークツリーとは、`.claude/worktrees/<名前>/.git` が `gitdir:` の行を持つファイルで、元のプロジェクトを
// 見つけられないもの。次の 2 つの形を試す。
//
//   - gitdir が指す先が無い（プロジェクトのディレクトリごと消した）
//   - 先は在るが、そこの `gitdir` ファイルが戻ってこない（相互参照が成り立たない）
//
// 見るのは外から観測できるものだけ（`--lint --json` の苦情の深刻度と文面、`--explain` の節）。
// 中のパスの判定と一覧の作り方は go/internal/infra/tree の go test が見る。

const orphanHeading = "■ 元のプロジェクトが無いワークツリー"

// 先が無い形、相互参照が成り立たない形、正常なワークツリー、ふつうのディレクトリ。
const (
	wtGone   = "gone-target"
	wtBroken = "broken-backref"
	wtNormal = "normal-tree"
	wtPlain  = "plain-dir"
)

func initRepo(t *testing.T, path string) {
	t.Helper()
	mkdir(t, path)
	run.Git(t, path, "init", "--quiet")
	writeFile(t, path, "README.md", "x\n")
	run.Git(t, path, "add", "-A")
	run.Git(t, path, "commit", "--quiet", "-m", "init")
}

// gitdirOf はワークツリーの `.git` ファイルに書かれた gitdir（書かれたとおり）。
func gitdirOf(t *testing.T, worktree string) string {
	t.Helper()
	text := strings.TrimSpace(readFile(t, filepath.Join(worktree, ".git")))
	return strings.TrimSpace(strings.TrimPrefix(text, "gitdir:"))
}

// explainSection は `--explain` の出力から、見出しの下の行（次の空行か次の見出しまで）。見出しが無ければ空。
func explainSection(text string) []string {
	lines := strings.Split(text, "\n")
	for i, l := range lines {
		if l != orphanHeading {
			continue
		}
		var body []string
		for _, x := range lines[i+1:] {
			if strings.TrimSpace(x) == "" || strings.HasPrefix(x, "■") {
				break
			}
			body = append(body, x)
		}
		return body
	}
	return nil
}

func TestWorktreeWithoutProject(t *testing.T) {
	t.Parallel()
	base := emptyRoot(t)
	root := filepath.Join(base, "ws")
	projects := filepath.Join(root, "projects")
	initRepo(t, root)
	wt := filepath.Join(root, ".claude", "worktrees")

	// 先が無い形: プロジェクトから切って、プロジェクトのディレクトリごと消す。
	goneProject := filepath.Join(projects, "gone")
	initRepo(t, goneProject)
	gonePath := filepath.Join(wt, wtGone)
	run.Git(t, goneProject, "worktree", "add", "--quiet", gonePath, "-b", wtGone)
	goneGitdir := gitdirOf(t, gonePath)
	if err := os.RemoveAll(goneProject); err != nil {
		t.Fatal(err)
	}

	// 相互参照が成り立たない形: プロジェクトは在るが、戻りの `gitdir` ファイルが別を指す。
	brokenProject := filepath.Join(projects, "broken")
	initRepo(t, brokenProject)
	brokenPath := filepath.Join(wt, wtBroken)
	run.Git(t, brokenProject, "worktree", "add", "--quiet", brokenPath, "-b", wtBroken)
	brokenGitdir := gitdirOf(t, brokenPath)
	writeFile(t, brokenGitdir, "gitdir", filepath.Join(base, "elsewhere", ".git")+"\n")

	// 正常なワークツリーと、ふつうのディレクトリ。
	run.Git(t, root, "worktree", "add", "--quiet", filepath.Join(wt, wtNormal), "-b", wtNormal)
	mkdir(t, filepath.Join(wt, wtPlain, "src"))

	// 直接作業の記録の検査（`(direct/…)`）は、元のプロジェクトの有無とは別の話なので数えない。
	mentions := func(name string) []problem {
		r := ccnavi(t, root, nil, "--lint", "--json", "--mode", "enable")
		var body struct{ Problems []problem }
		if err := jsonUnmarshal(r.Stdout, &body); err != nil {
			t.Fatal(err)
		}
		var found []problem
		for _, p := range body.Problems {
			if strings.Contains(p.Where+p.Detail, name) && !strings.HasPrefix(p.Where, "(direct/") {
				found = append(found, p)
			}
		}
		return found
	}
	orphans := []struct{ name, gitdir string }{{wtGone, goneGitdir}, {wtBroken, brokenGitdir}}

	t.Run("lint は先が無い・相互参照が成り立たないワークツリーを名指しして warn を 1 件出す", func(t *testing.T) {
		for _, o := range orphans {
			if found := mentions(o.name); len(found) != 1 || found[0].Severity != "warn" {
				t.Errorf("%s: %v", o.name, found)
			}
		}
	})
	t.Run("lint の文面に元の gitdir と影響と対処が入る", func(t *testing.T) {
		for _, o := range orphans {
			found := mentions(o.name)
			if len(found) != 1 {
				t.Fatalf("%s: %v", o.name, found)
			}
			detail := found[0].Where + found[0].Detail
			mustContain(t, o.name, detail, o.gitdir, "元のプロジェクトがありません", "deny が効いていない", "ワークスペースルートとして判定", "ワークツリーを削除するか、プロジェクトを戻")
			mustNotContain(t, o.name, detail, "孤児")
		}
	})
	t.Run("lint は正常なワークツリーとふつうのディレクトリを名指ししない", func(t *testing.T) {
		if got := mentions(wtNormal); len(got) != 0 {
			t.Errorf("normal: %v", got)
		}
		if got := mentions(wtPlain); len(got) != 0 {
			t.Errorf("plain: %v", got)
		}
	})
	t.Run("explain は見出し 1 つの節で同じ内容を 1 件 1 行で出す", func(t *testing.T) {
		r := ccnavi(t, root, nil, "--explain", "--mode", "enable")
		if r.Code != 0 {
			t.Fatalf("code = %d: %s", r.Code, r.Stderr)
		}
		if n := strings.Count("\n"+r.Stdout, "\n"+orphanHeading+"\n"); n != 1 {
			t.Errorf("見出しが %d 個", n)
		}
		lines := explainSection(r.Stdout)
		if len(lines) != 2 {
			t.Fatalf("lines = %v", lines)
		}
		for _, o := range orphans {
			var mine []string
			for _, x := range lines {
				if strings.Contains(x, o.name) {
					mine = append(mine, x)
				}
			}
			if len(mine) != 1 {
				t.Fatalf("%s の行 = %v", o.name, mine)
			}
			mustContain(t, o.name, mine[0], o.gitdir, "deny が効いていない", "ワークツリーを削除するか、プロジェクトを戻")
			mustNotContain(t, o.name, mine[0], "孤児")
		}
		for _, x := range lines {
			mustNotContain(t, "節", x, wtNormal, wtPlain)
		}
	})
	t.Run("explain --json に欄は足さない", func(t *testing.T) {
		r := ccnavi(t, root, nil, "--explain", "--json", "--mode", "enable")
		mustNotContain(t, "stdout", r.Stdout, wtGone, "元のプロジェクトがありません")
	})
	t.Run("該当が無ければ lint も explain も何も出さない", func(t *testing.T) {
		root := filepath.Join(emptyRoot(t), "ws")
		initRepo(t, root)
		run.Git(t, root, "worktree", "add", "--quiet", filepath.Join(root, ".claude", "worktrees", "ok"))
		mkdir(t, filepath.Join(root, ".claude", "worktrees", "plain"))
		lintOut := ccnavi(t, root, nil, "--lint", "--json", "--mode", "enable").Stdout
		explained := ccnavi(t, root, nil, "--explain", "--mode", "enable").Stdout
		mustNotContain(t, "lint", lintOut, "元のプロジェクトがありません")
		mustNotContain(t, "explain", explained, orphanHeading)
	})
}
