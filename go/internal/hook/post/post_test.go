package post

import (
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/pyre"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/records/prune"
	"ccnavi/internal/tickets/testkit"
)

func TestRecordCode(t *testing.T) {
	found := func(codes ...string) []*finding {
		var out []*finding
		for i, c := range codes {
			out = append(out, &finding{change: git.Change{Path: "f" + string(rune('0'+i))}, code: c})
		}
		return out
	}
	mixed := found(CodeTicketScope, codeViolation, CodeTicketScope)
	if recordCode(mixed) != codeViolation || codesNote(mixed) != "codes POST_VIOLATION 1, POST_TICKET_SCOPE 2" {
		t.Errorf("混ざった回: %q %q", recordCode(mixed), codesNote(mixed))
	}
	one := found(CodeTicketScope, CodeTicketScope)
	if recordCode(one) != CodeTicketScope || codesNote(one) != "" {
		t.Errorf("1 種類: %q %q", recordCode(one), codesNote(one))
	}
	if recordCode(nil) != "" || codesNote(nil) != "" {
		t.Error("件が無い")
	}
}

func put(t *testing.T, root, rel, text string) {
	t.Helper()
	testkit.Put(t, filepath.Join(root, filepath.FromSlash(rel)), text)
}

func repo(t *testing.T) string {
	t.Helper()
	root := testkit.GitRoot(t)
	testkit.Git(t, root, "init", "-q", "-b", "main")
	return root
}

func mergeTree(t *testing.T, root string) {
	t.Helper()
	if !git.Run(root, []string{"merge-tree", "--write-tree", "HEAD", "HEAD"}, git.Options{}).OK() {
		t.Skip("git merge-tree --write-tree（git 2.38 以降）が無い")
	}
}

func TestSameButHandoff(t *testing.T) {
	root := repo(t)
	put(t, root, "a.txt", "a\n")
	put(t, root, "wip/handoff/draft.txt", "draft\n")
	put(t, root, "wip/proposals/todo/p.md", "p\n")
	testkit.Commit(t, root, "init")
	left := strings.TrimSpace(testkit.Git(t, root, "write-tree"))
	testkit.Git(t, root, "rm", "-q", "-f", "--", "wip/handoff/draft.txt")
	onlyHandoff := strings.TrimSpace(testkit.Git(t, root, "write-tree"))
	testkit.Git(t, root, "rm", "-q", "-f", "--", "wip/proposals/todo/p.md")
	alsoProposal := strings.TrimSpace(testkit.Git(t, root, "write-tree"))
	if !sameButHandoff(root, left, onlyHandoff) || sameButHandoff(root, left, alsoProposal) ||
		sameButHandoff(root, left, strings.Repeat("0", 40)) || sameButHandoff(root, left, "") {
		t.Error("下書きの置き場だけを外していない")
	}
}

func guardRules(root string) *rules.RuleSet {
	rule := &rules.Rule{ID: "guarded", BareID: "guarded", Match: "Write|Edit", Decision: rules.Deny, Root: root,
		Compiled: pyre.MustCompile(`/guarded/`, 0)}
	return &rules.RuleSet{Deny: []*rules.Rule{rule}}
}

// integrate が統合先に staged で置いた変更も、保護領域にヒットすれば通常の変更と同じに報告する。
func TestStagedChangesInAGuardedPlaceAreReported(t *testing.T) {
	root := repo(t)
	put(t, root, "a.txt", "a\n")
	put(t, root, "guarded/g.txt", "g\n")
	testkit.Commit(t, root, "init")
	found := func() []*finding {
		changes, why := git.Read(root, 0)
		if why != "" {
			t.Fatal(why)
		}
		return scan{changes: changes, rules: guardRules(root), source: "rules.yml", where: tree.Tree{Root: root}, top: root}.findings()
	}
	put(t, root, "a.txt", "a2\n")
	testkit.Git(t, root, "add", ".")
	if got := found(); len(got) != 0 {
		t.Errorf("保護領域の外を報告した: %v", got)
	}
	put(t, root, "guarded/g.txt", "g2\n")
	put(t, root, "guarded/new.txt", "new\n")
	testkit.Git(t, root, "add", ".")
	var paths []string
	for _, f := range found() {
		if !f.change.Staged || f.group[0].ID != "guarded" {
			t.Errorf("%+v", f)
		}
		paths = append(paths, f.change.Path)
	}
	slices.Sort(paths)
	if !slices.Equal(paths, []string{"guarded/g.txt", "guarded/new.txt"}) {
		t.Errorf("%v", paths)
	}
}

func writeDone(t *testing.T, root, name string, fields [][2]string) {
	t.Helper()
	var b strings.Builder
	for _, f := range fields {
		b.WriteString(f[0] + " " + f[1] + "\n")
	}
	put(t, root, ".git/"+integrateDoneDir+"/"+name, b.String())
}

func with(base [][2]string, over ...[2]string) [][2]string {
	out := slices.Clone(base)
	for _, o := range over {
		for i := range out {
			if out[i][0] == o[0] {
				out[i][1] = o[1]
			}
		}
	}
	return out
}

func pathSet(m map[string]bool) []string {
	out := sorted(m)
	if out == nil {
		out = []string{}
	}
	return out
}

func head(t *testing.T, root, rev string) string {
	return strings.TrimSpace(testkit.Git(t, root, "rev-parse", rev))
}

// integrate-done が積んだ squash を外す条件。記録は .git の中でエージェントにも書けるので、偽の記録では外れないこと。
func TestIntegratedSquash(t *testing.T) {
	zero := strings.Repeat("0", 40)
	type fixture struct{ root, turn, tip, base, tree, squash string }
	setup := func(t *testing.T) fixture {
		root := repo(t)
		mergeTree(t, root)
		put(t, root, "a.txt", "a\n")
		put(t, root, "gone.txt", "gone\n")
		testkit.Commit(t, root, "init")
		f := fixture{root: root, turn: head(t, root, "HEAD")}
		testkit.Git(t, root, "switch", "-q", "-c", "feat")
		put(t, root, "a.txt", "a2\n")
		put(t, root, "new.txt", "new\n")
		put(t, root, "wip/handoff/draft.txt", "draft\n")
		testkit.Git(t, root, "rm", "-q", "gone.txt")
		testkit.Commit(t, root, "work")
		f.tip = head(t, root, "HEAD")
		testkit.Git(t, root, "switch", "-q", "main")
		f.base = head(t, root, "HEAD")
		testkit.Git(t, root, "merge", "-q", "--squash", "feat")
		testkit.Git(t, root, "rm", "-q", "-f", "--", "wip/handoff/draft.txt")
		f.tree = strings.TrimSpace(testkit.Git(t, root, "write-tree"))
		testkit.Git(t, root, "commit", "-q", "-m", "squash")
		f.squash = head(t, root, "HEAD")
		testkit.Git(t, root, "branch", "-q", "-D", "feat")
		return f
	}
	record := func(f fixture) [][2]string {
		return [][2]string{{"state", "staged"}, {"branch", "feat"}, {"branch_sha", f.tip}, {"base", f.base}, {"tree", f.tree}}
	}
	all := []string{"a.txt", "gone.txt", "new.txt"}
	cases := []struct {
		name    string
		prepare func(t *testing.T, f fixture)
		base    func(f fixture) string
		want    []string
	}{
		{"squash を外す", func(t *testing.T, f fixture) { writeDone(t, f.root, "feat", record(f)) }, nil, all},
		{"記録が無い", func(*testing.T, fixture) {}, nil, []string{}},
		{"基準が無い", func(t *testing.T, f fixture) { writeDone(t, f.root, "feat", record(f)) }, func(fixture) string { return "" }, []string{}},
		{"squash の後の直接のコミットは外さない", func(t *testing.T, f fixture) {
			writeDone(t, f.root, "feat", record(f))
			put(t, f.root, "a.txt", "edited directly\n")
			put(t, f.root, "evil.txt", "x\n")
			testkit.Commit(t, f.root, "direct")
		}, nil, []string{"gone.txt", "new.txt"}},
		{"直接のコミット自身を書いた偽の記録", func(t *testing.T, f fixture) {
			put(t, f.root, "evil.txt", "x\n")
			testkit.Commit(t, f.root, "direct")
			writeDone(t, f.root, "feat", with(record(f), [2]string{"base", f.squash},
				[2]string{"tree", head(t, f.root, "HEAD^{tree}")}, [2]string{"branch_sha", head(t, f.root, "HEAD")}))
		}, nil, []string{}},
		{"関係の無いブランチの偽の記録", func(t *testing.T, f fixture) {
			put(t, f.root, "evil.txt", "x\n")
			testkit.Commit(t, f.root, "direct")
			writeDone(t, f.root, "feat", with(record(f), [2]string{"base", f.squash}, [2]string{"tree", head(t, f.root, "HEAD^{tree}")}))
		}, nil, []string{}},
		{"偽のツリー", func(t *testing.T, f fixture) { writeDone(t, f.root, "feat", with(record(f), [2]string{"tree", zero})) }, nil, []string{}},
		{"偽の基準", func(t *testing.T, f fixture) { writeDone(t, f.root, "feat", with(record(f), [2]string{"base", zero})) }, nil, []string{}},
		{"知らない branch_sha", func(t *testing.T, f fixture) {
			writeDone(t, f.root, "feat", with(record(f), [2]string{"branch_sha", zero}))
		}, nil, []string{}},
		{"tip を持つブランチが残っている", func(t *testing.T, f fixture) {
			testkit.Git(t, f.root, "branch", "-q", "feat", f.tip)
			writeDone(t, f.root, "feat", record(f))
		}, nil, []string{}},
		{"記録の名前とブランチが違う", func(t *testing.T, f fixture) {
			writeDone(t, f.root, "feat", with(record(f), [2]string{"branch", "other"}))
		}, nil, []string{}},
		{"チケットに結び付く名前", func(t *testing.T, f fixture) {
			writeDone(t, f.root, "feat", record(f))
			put(t, f.root, ".ccnavi/approved/doing/feat.md", "---\nticket: feat\n---\n")
		}, nil, []string{}},
		{"origin に同じ名前がある", func(t *testing.T, f fixture) {
			writeDone(t, f.root, "feat", record(f))
			testkit.Git(t, f.root, "update-ref", "refs/remotes/origin/feat", "HEAD")
		}, nil, []string{}},
		{"ターンの前の squash は数えない", func(t *testing.T, f fixture) { writeDone(t, f.root, "feat", record(f)) },
			func(f fixture) string { return f.squash }, []string{}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			f := setup(t)
			c.prepare(t, f)
			base := f.turn
			if c.base != nil {
				base = c.base(f)
			}
			if got := pathSet(integratedPaths(f.root, base)); !slices.Equal(got, c.want) {
				t.Errorf("%v, want %v", got, c.want)
			}
		})
	}
}

// 版 4 の integrate-done が fast-forward で載せた K を外す条件。
func TestIntegratedDirect(t *testing.T) {
	zero := strings.Repeat("0", 40)
	type fixture struct{ root, turn, k, cTree string }
	setup := func(t *testing.T) fixture {
		root := repo(t)
		put(t, root, "a.txt", "a\n")
		testkit.Commit(t, root, "init")
		f := fixture{root: root, turn: head(t, root, "HEAD")}
		put(t, root, "a.txt", "a2\n")
		put(t, root, "new.txt", "new\n")
		testkit.Commit(t, root, "K")
		f.k, f.cTree = head(t, root, "HEAD"), head(t, root, "HEAD^{tree}")
		return f
	}
	record := func(f fixture) [][2]string {
		return [][2]string{{"version", "4"}, {"k", f.k}, {"head", f.turn}, {"c_tree", f.cTree}, {"base_branch", "main"}}
	}
	cases := []struct {
		name    string
		prepare func(t *testing.T, f fixture)
		want    []string
	}{
		{"K を外す", func(t *testing.T, f fixture) { writeDone(t, f.root, "feat", record(f)) }, []string{"a.txt", "new.txt"}},
		{"k が空の印でも親と木で合わせる", func(t *testing.T, f fixture) { writeDone(t, f.root, "feat", with(record(f), [2]string{"k", "-"})) }, []string{"a.txt", "new.txt"}},
		{"記録が無い", func(*testing.T, fixture) {}, []string{}},
		{"親が違う", func(t *testing.T, f fixture) { writeDone(t, f.root, "feat", with(record(f), [2]string{"head", zero})) }, []string{}},
		{"木が違う", func(t *testing.T, f fixture) {
			writeDone(t, f.root, "feat", with(record(f), [2]string{"c_tree", zero}))
		}, []string{}},
		{"別のコミットを K に書いた", func(t *testing.T, f fixture) {
			writeDone(t, f.root, "feat", with(record(f), [2]string{"k", strings.Repeat("1", 40)}))
		}, []string{}},
		{"チケットに結び付く名前", func(t *testing.T, f fixture) {
			put(t, f.root, ".ccnavi/approved/doing/feat.md", "---\n---\n")
			writeDone(t, f.root, "feat", record(f))
		}, []string{}},
		{"あとのユーザのコミットが同じパスを触る", func(t *testing.T, f fixture) {
			writeDone(t, f.root, "feat", record(f))
			put(t, f.root, "a.txt", "a3\n")
			testkit.Commit(t, f.root, "user")
		}, []string{"new.txt"}},
		{"版の違う記録は版 4 と読まない", func(t *testing.T, f fixture) {
			writeDone(t, f.root, "feat", with(record(f), [2]string{"version", "3"}))
		}, []string{}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			f := setup(t)
			c.prepare(t, f)
			if got := pathSet(integratedPaths(f.root, f.turn)); !slices.Equal(got, c.want) {
				t.Errorf("%v, want %v", got, c.want)
			}
		})
	}
}

// records/prune は post の記録の名前を写して持つ（prune は上の段を読めない）。古い記録が消えることで突き合わせる。
func TestPruneRemovesTheRecords(t *testing.T) {
	root := testkit.TempRoot(t)
	state := filepath.Join(root, config.DefaultState)
	session := "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"
	old := time.Now().Add(-400 * 24 * time.Hour)
	var paths []string
	for _, suffix := range []string{reportedSuffix, acceptedSuffix, turnSuffix} {
		p := statePath(state, session, suffix)
		put(t, root, strings.TrimPrefix(p, root+string(filepath.Separator)), "{}")
		if err := os.Chtimes(p, old, old); err != nil {
			t.Fatal(err)
		}
		paths = append(paths, p)
	}
	prune.Run(root, filepath.Join(root, config.DefaultLog), state, "other-session", prune.Options{})
	for _, p := range paths {
		if _, err := os.Stat(p); err == nil {
			t.Errorf("%s が消えていない（prune の名前の写しが post と食い違う）", filepath.Base(p))
		}
	}
}
