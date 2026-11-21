package life

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/ticketkit"
)

// `ticket learn`（学びの蓄積の CLI）の置き場。リポジトリ 1 つ（親のワークツリーは持たない）。
type learnWS struct {
	t    *testing.T
	root string
}

func newLearnWS(t *testing.T) *learnWS {
	t.Helper()
	root := base.TempDir(t)
	if err := os.MkdirAll(filepath.Join(root, ".claude"), 0o755); err != nil {
		t.Fatal(err)
	}
	run.Git(t, root, "init", "--quiet", "-b", "main")
	return &learnWS{t: t, root: root}
}

// learn は `ticket learn <words>` を dir で起こす。学びの蓄積は既定で有効にする（disable を渡すと切る）。
func (w *learnWS) learn(dir, mode string, words ...string) run.Result {
	w.t.Helper()
	if mode == "" {
		mode = "enable"
	}
	args := append([]string{"--root", w.root, "ticket", "learn"}, words...)
	return run.Ccnavi(w.t, args, run.Opts{Dir: dir, Env: map[string]string{"CCNAVI_LEARNING": mode}})
}

// direct はチケットに結び付かない直接作業のワークツリーを切る。
func (w *learnWS) direct() string {
	w.t.Helper()
	run.Git(w.t, w.root, "commit", "--allow-empty", "-q", "-m", "init")
	wt := filepath.Join(w.root, ".claude", "worktrees", "direct-1-x")
	run.Git(w.t, w.root, "worktree", "add", "-q", wt, "-b", "direct-1-x")
	return wt
}

func exists(path string) bool { return ticketkit.Exists(path) }

func inboxNames(t *testing.T, wt string) []string {
	t.Helper()
	entries, err := os.ReadDir(filepath.Join(wt, "docs", "wiki", "inbox"))
	if err != nil {
		t.Fatal(err)
	}
	var names []string
	for _, e := range entries {
		names = append(names, e.Name())
	}
	return names
}

func TestLearnNoteInADirectWorktreeGoesToTheInbox(t *testing.T) {
	t.Parallel()
	w := newLearnWS(t)
	wt := w.direct()
	ticketkit.OK(t, w.learn(wt, "", "--note", "テストの気づき\n2 行目", "--target", "skill"))
	names := inboxNames(t, wt)
	if len(names) != 1 || !strings.HasPrefix(names[0], "direct-1-x-") || !strings.HasSuffix(names[0], ".md") {
		t.Fatalf("受信箱の名前 = %v", names)
	}
	text := ticketkit.Read(t, filepath.Join(wt, "docs", "wiki", "inbox", names[0]))
	for _, want := range []string{"type: learning-wiki", "- 種類: skill", "- 信号: 訂正", "テストの気づき", "  2 行目"} {
		ticketkit.Has(t, text, want)
	}
	// 直接作業の受信箱は docs/wiki/inbox。ルートの wip/handoff には書かない。
	if exists(filepath.Join(w.root, "wip", "handoff", "inbox")) {
		t.Error("ルートの wip/handoff/inbox に書いた")
	}
}

func TestLearnNotesInTheSameWorktreeDoNotCollide(t *testing.T) {
	t.Parallel()
	w := newLearnWS(t)
	wt := w.direct()
	markers := []string{"気づきA", "気づきB", "気づきC"}
	for _, m := range markers {
		ticketkit.OK(t, w.learn(wt, "", "--note", m))
	}
	names := inboxNames(t, wt)
	if len(names) != 3 {
		t.Fatalf("受信箱の件数 = %v", names)
	}
	seen := map[string]bool{}
	for _, n := range names {
		text := ticketkit.Read(t, filepath.Join(wt, "docs", "wiki", "inbox", n))
		var hits []string
		for _, m := range markers {
			if strings.Contains(text, m) {
				hits = append(hits, m)
			}
		}
		if len(hits) != 1 {
			t.Errorf("%s の中の気づき = %v（1 件のはず）", n, hits)
		}
		seen[strings.Join(hits, "")] = true
	}
	if len(seen) != 3 {
		t.Errorf("気づきが重なった: %v", seen)
	}
}

// 断る形。何も作らない（docs・wip を残さない）ことまで見る。
func TestLearnRefusals(t *testing.T) {
	t.Parallel()
	for _, c := range []struct {
		name string
		// where は learn を起こす場所（"root"・"project"・"direct"）、mode は CCNAVI_LEARNING。
		where, mode string
		words       []string
		want        string
		// untouched は作られてはならない場所（起こす場所からの相対）。
		untouched string
	}{
		{"root_note", "root", "", []string{"--note", "x"}, "ワークツリーを切", "wip"},
		{"root_without_note", "root", "", nil, "", "wip"},
		{"project_root_note", "project", "", []string{"--note", "n"}, "ワークツリーを切", "wip"},
		{"disabled", "direct", "disable", []string{"--note", "x"}, "CCNAVI_LEARNING", "docs"},
		{"bad_target", "direct", "", []string{"--note", "x", "--target", "zzz"}, "--target", "docs"},
		{"bad_signal", "direct", "", []string{"--note", "x", "--signal", "zzz"}, "--signal", "docs"},
		{"blank_note", "direct", "", []string{"--note", ""}, "", "docs"},
		{"spaces_note", "direct", "", []string{"--note", "   "}, "", "docs"},
	} {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			w := newLearnWS(t)
			dir := w.root
			switch c.where {
			case "project":
				dir = filepath.Join(w.root, "projects", "foo")
				if err := os.MkdirAll(dir, 0o755); err != nil {
					t.Fatal(err)
				}
				run.Git(t, dir, "init", "--quiet", "-b", "main")
			case "direct":
				dir = w.direct()
			}
			r := ticketkit.Fails(t, w.learn(dir, c.mode, c.words...))
			if c.want != "" {
				ticketkit.Has(t, r.Stderr, c.want)
			}
			if exists(filepath.Join(dir, c.untouched)) {
				t.Errorf("%s を作った", c.untouched)
			}
			// 起点のルートにも何も残さない。
			if exists(filepath.Join(w.root, "wip")) {
				t.Error("ルートに wip を作った")
			}
		})
	}
}

func TestLearnRedactsASecretInTheNote(t *testing.T) {
	t.Parallel()
	w := newLearnWS(t)
	wt := w.direct()
	secret := "ghp_" + "0123456789abcdefghijklmnopqrstuvwxyz"
	ticketkit.OK(t, w.learn(wt, "", "--note", "接続に使った "+secret+" を忘れない"))
	names := inboxNames(t, wt)
	if len(names) != 1 {
		t.Fatalf("受信箱 = %v", names)
	}
	ticketkit.Lacks(t, ticketkit.Read(t, filepath.Join(wt, "docs", "wiki", "inbox", names[0])), secret)
}

// 書き込み先がリンクで外を指していても、外には書かない。
func TestLearnRefusesASymlinkedDestination(t *testing.T) {
	t.Parallel()
	if !base.CanSymlink() {
		t.Skip("リンクを作れない機械")
	}
	w := newLearnWS(t)
	outside := base.TempDir(t)
	if err := os.MkdirAll(filepath.Join(w.root, "wip"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(outside, filepath.Join(w.root, "wip", "handoff")); err != nil {
		t.Fatal(err)
	}
	ticketkit.Fails(t, w.learn(w.root, "", "--note", "x"))
	if entries, _ := os.ReadDir(outside); len(entries) != 0 {
		t.Errorf("リンクの先に書いた: %v", entries)
	}
}
