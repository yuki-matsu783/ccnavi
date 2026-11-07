package request

import (
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/tickets/model/handoffnames"

	"ccnavi/internal/tickets/testkit"
)

func repo(t *testing.T) string {
	t.Helper()
	root := testkit.GitRoot(t)
	testkit.Git(t, root, "init", "-q", "-b", "main")
	return root
}

func commitAll(t *testing.T, dir string) {
	t.Helper()
	testkit.Commit(t, dir, "x")
}

// 追跡済みの wip/design/x.html の途中のディレクトリがリンク（ツリーの外）に置き換わっていても、リンクの先は消さない。
// ready の前提の時点で名指しして止め、RemoveWip も何も消さない。
func TestRemoveWipDoesNotFollowALinkOut(t *testing.T) {
	root := repo(t)
	rel := "wip/design/x.html"
	testkit.Put(t, filepath.Join(root, filepath.FromSlash(rel)), "<p>x</p>\n")
	commitAll(t, root)
	outside := filepath.Join(t.TempDir(), "outside")
	testkit.Put(t, filepath.Join(outside, "x.html"), "大事\n")
	if err := os.RemoveAll(filepath.Join(root, "wip", "design")); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(outside, filepath.Join(root, "wip", "design")); err != nil {
		t.Skip("リンクが作れない")
	}
	problems := MergeProblems(defaultConf(t), root, root)
	if len(problems) == 0 || !strings.Contains(problems[0], rel) || !strings.Contains(problems[0], "リンク") {
		t.Errorf("前提: %q", problems)
	}
	removed, _, failed := RemoveWip(root)
	if removed != nil || !strings.Contains(failed, rel) || !strings.Contains(failed, "リンク") {
		t.Errorf("%q %q", removed, failed)
	}
	if _, err := os.Stat(filepath.Join(outside, "x.html")); err != nil {
		t.Error("外のファイルが消えた")
	}
	// 最後の名前がリンクなのは構わない（消すのはリンクだけ）。途中がリンクなら外へ出る。
	if !escapes(root, "wip/design/x") || escapes(root, "wip/design") || escapes(root, "a/b/f.txt") {
		t.Error("escapes")
	}
}

// ready は `WIP/design/…` と `wip\design\…`（名前に `\` を含む 1 ファイル）も片付ける（区別しない FS では範囲の
// 除外が `WIP/` を通しうる）。消したあとの空のディレクトリも消す。
func TestRemoveWipCatchesAnyCaseAndABackslashedName(t *testing.T) {
	if ospath.Windows {
		t.Skip("大文字違いの並存と名前の \\ は Windows では作れない")
	}
	for _, rel := range []string{"WIP/design/phase-1.html", `wip\design\phase-1.html`, "wip/a/b/c.txt"} {
		root := repo(t)
		testkit.Put(t, filepath.Join(root, "keep.txt"), "k\n")
		testkit.Put(t, filepath.Join(root, filepath.FromSlash(rel)), "<p>x</p>\n")
		commitAll(t, root)
		if got, ok := wipTracked(root); !ok || !reflect.DeepEqual(got, []string{rel}) {
			t.Errorf("%s: 追跡 %q", rel, got)
		}
		removed, head, failed := RemoveWip(root)
		if !reflect.DeepEqual(removed, []string{rel}) || failed != "" || len(head) != 40 {
			t.Errorf("%s: %q %q %q", rel, removed, head, failed)
		}
		top, _, _ := strings.Cut(rel, "/")
		if ospath.DiskLexists(filepath.Join(root, top)) {
			t.Errorf("%s: 空のディレクトリが残った", rel)
		}
	}
}

// 取り込んでいない保護対象の下書きは ready の前に名指しして止める。未追跡の下書きは未追跡の側で 1 度だけ言う。
func TestMergeProblemsNameDraftsOnce(t *testing.T) {
	const docs = ".ccnavi/docs/guide.md"
	names, err := handoffnames.Default()
	if err != nil {
		t.Fatal(err)
	}
	draft := func(root string) string {
		return filepath.Join(root, "wip", "handoff", filepath.FromSlash(names.ToDraft(docs)))
	}
	root := repo(t)
	testkit.Put(t, filepath.Join(root, filepath.FromSlash(docs)), "古い文\n")
	testkit.Put(t, draft(root), "new\n")
	commitAll(t, root)
	said := 0
	for _, p := range MergeProblems(defaultConf(t), root, root) {
		if strings.Contains(p, "取り込んでいない保護対象の下書き") {
			said++
			if !strings.Contains(p, "ccnavi-user-handoff.sh") || !strings.Contains(p, "wip/handoff/"+docs) {
				t.Errorf("%q", p)
			}
		}
	}
	if said != 1 {
		t.Errorf("%d 回", said)
	}
	root = repo(t)
	testkit.Put(t, filepath.Join(root, filepath.FromSlash(docs)), "古い文\n")
	testkit.Put(t, draft(root), "new\n")
	joined := strings.Join(MergeProblems(defaultConf(t), root, root), "\n")
	if !strings.Contains(joined, "未追跡") || strings.Contains(joined, "取り込んでいない保護対象の下書き") {
		t.Errorf("%q", joined)
	}
}
