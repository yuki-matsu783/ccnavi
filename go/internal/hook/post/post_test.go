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
	mixed := found(CodeOutOfScope, codeViolation, CodeOutOfScope)
	if recordCode(mixed) != codeViolation || codesNote(mixed) != "codes POST_VIOLATION 1, POST_TICKET_SCOPE 2" {
		t.Errorf("混ざった回: %q %q", recordCode(mixed), codesNote(mixed))
	}
	one := found(CodeOutOfScope, CodeOutOfScope)
	if recordCode(one) != CodeOutOfScope || codesNote(one) != "" {
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
		return scan{changes: changes, rules: guardRules(root), where: tree.Tree{Root: root}, top: root}.findings()
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
