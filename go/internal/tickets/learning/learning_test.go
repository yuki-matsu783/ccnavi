package learning

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/records/wiki"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/testkit"
)

const child = "feat-1-01-01"

// workspace は文面ファイルを写したワークスペースと、学びを有効にした設定。
func workspace(t *testing.T) (string, *config.Settings) {
	t.Helper()
	root := repo(t)
	from := filepath.Join(testkit.RepoRoot(t), ".ccnavi", "messages")
	entries, err := os.ReadDir(from)
	if err != nil {
		t.Fatal(err)
	}
	for _, e := range entries {
		put(t, root, ".ccnavi/messages/"+e.Name(), read(t, filepath.Join(from, e.Name())))
	}
	conf, _ := config.Load(root)
	conf.Learning = "enable"
	return root, conf
}

func childTicket() *ticket.Ticket { return &ticket.Ticket{ID: child, Parent: "feat-1", Phase: 1} }

func childTree(root string) tree.Tree {
	return tree.Tree{Name: child, Root: filepath.Join(root, ".claude", "worktrees", child), Kind: tree.KindWorktree}
}

func TestLearnAppendsAndRecounts(t *testing.T) {
	root, conf := workspace(t)
	here := childTree(root)
	rec := filepath.Join(here.Root, filepath.FromSlash(recordRel(child)))
	for i, note := range []string{"最初の気づき", "2 件目の気づき"} {
		var out, errOut strings.Builder
		if code := learn(&out, &errOut, conf, root, here, childTicket(), Note{Note: note, Target: "skill"}); code != 0 {
			t.Fatalf("%d: %s", code, errOut.String())
		}
		text := read(t, rec)
		if !strings.Contains(text, "learnings: "+string(rune('1'+i))) || headings(text) != i+1 {
			t.Fatalf("%q", text)
		}
	}
}

func TestLearnWithoutNoteKeepsRecordAndWritesDigest(t *testing.T) {
	root, conf := workspace(t)
	here := childTree(root)
	conf.Log = filepath.Join(root, "logs", "decisions.jsonl")
	put(t, root, "logs/decisions.jsonl", `{"decision": "deny", "tree": "`+child+`"}`+"\n")
	existing, err := appendEntry(scaffoldText(child, "feat-1"), entryText("既存の気づき", "docs", "", "t"), child, "feat-1")
	if err != nil {
		t.Fatal(err)
	}
	rec := put(t, here.Root, recordRel(child), existing)
	var out, errOut strings.Builder
	if code := learn(&out, &errOut, conf, root, here, childTicket(), Note{}); code != 0 {
		t.Fatal(errOut.String())
	}
	if read(t, rec) != existing {
		t.Error("既存の記録を書き換えた")
	}
	digest := read(t, filepath.Join(here.Root, filepath.FromSlash(digestRel(child))))
	if !strings.Contains(digest, "\"recorded\": true") || !strings.Contains(digest, "\"deny\": 1") {
		t.Errorf("%s", digest)
	}
	if !strings.Contains(out.String(), "記録: "+recordRel(child)) {
		t.Errorf("%s", out.String())
	}
}

func TestLearnRescaffoldsAnEmptyRecord(t *testing.T) {
	root, conf := workspace(t)
	here := childTree(root)
	rec := put(t, here.Root, recordRel(child), "  \n")
	var out, errOut strings.Builder
	if code := learn(&out, &errOut, conf, root, here, childTicket(), Note{}); code != 0 {
		t.Fatal(errOut.String())
	}
	if !strings.Contains(read(t, rec), "learnings: none") {
		t.Error(read(t, rec))
	}
}

func TestLearnRefuses(t *testing.T) {
	root, conf := workspace(t)
	here := childTree(root)
	sjis := func() { put(t, here.Root, recordRel(child), "\x82\xa0") }
	handwritten := func() { put(t, here.Root, recordRel(child), "手で書いた本文\n") }
	cases := []struct {
		name  string
		setup func()
		bound *ticket.Ticket
		note  Note
		want  string
	}{
		{"UTF-8 でない記録", sjis, childTicket(), Note{Note: "n"}, "書けない（'utf-8' codec"},
		{"frontmatter の無い記録", handwritten, childTicket(), Note{Note: "n"}, "を読み取れません（先頭に learnings:"},
		{"ほかの作業の記録", nil, childTicket(), Note{Ident: "other", Note: "n"}, "他の作業（other）"},
		{"結び付かない作業の雛形", nil, nil, Note{}, "--note だけ指定できる"},
		{"空白だけの本文", nil, nil, Note{Note: "   "}, "--note だけ指定できる"},
		{"知らない信号", nil, nil, Note{Note: "x", Signal: "zzz"}, "--signal は"},
		{"知らない反映先", nil, nil, Note{Note: "x", Target: "zzz"}, "--target は"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			if c.setup != nil {
				c.setup()
			}
			var out, errOut strings.Builder
			if code := learn(&out, &errOut, conf, root, here, c.bound, c.note); code != 1 || !strings.Contains(errOut.String(), c.want) {
				t.Errorf("%d %q", code, errOut.String())
			}
		})
	}
	if _, err := os.Stat(filepath.Join(here.Root, "docs")); err == nil {
		t.Error("止めたのに受信箱を作った")
	}
}

func TestRunRefusesOutsideAWorktreeAndWhenDisabled(t *testing.T) {
	root, conf := workspace(t)
	var out, errOut strings.Builder
	if code := Run(&out, &errOut, conf, root, root, Note{Note: "n"}); code != 1 || !strings.Contains(errOut.String(), "ワークツリーを切") {
		t.Errorf("%d %q", code, errOut.String())
	}
	if _, err := os.Stat(filepath.Join(root, "wip")); err == nil {
		t.Error("ルートに書いた")
	}
	conf.Learning = "disable"
	errOut.Reset()
	if code := Run(&out, &errOut, conf, root, root, Note{Note: "n"}); code != 1 || !strings.Contains(errOut.String(), env.Learning) {
		t.Errorf("%d %q", code, errOut.String())
	}
}

func TestInboxNamesDoNotCollideAndSecretsAreMasked(t *testing.T) {
	root, conf := workspace(t)
	here := tree.Tree{Name: "direct-1-x", Root: filepath.Join(root, ".claude", "worktrees", "direct-1-x"), Kind: tree.KindWorktree}
	secret := "ghp_" + "0123456789abcdefghijklmnopqrstuvwxyz"
	for _, note := range []string{"気づきA", "気づきB", "気づきC " + secret} {
		var out, errOut strings.Builder
		if code := learn(&out, &errOut, conf, root, here, nil, Note{Note: note}); code != 0 {
			t.Fatal(errOut.String())
		}
	}
	inbox := filepath.Join(here.Root, filepath.FromSlash(wiki.InboxDir))
	names, err := os.ReadDir(inbox)
	if err != nil || len(names) != 3 {
		t.Fatalf("%v %v", names, err)
	}
	var all string
	for _, e := range names {
		if !strings.HasPrefix(e.Name(), "direct-1-x-") {
			t.Error(e.Name())
		}
		all += read(t, filepath.Join(inbox, e.Name()))
	}
	for _, marker := range []string{"気づきA", "気づきB", "気づきC", "type: learning-wiki", "- 信号: 訂正"} {
		if !strings.Contains(all, marker) {
			t.Error(marker)
		}
	}
	if strings.Contains(all, secret) {
		t.Error("秘密を伏せていない")
	}
}

func TestRefusesALinkedDestination(t *testing.T) {
	root, conf := workspace(t)
	here := childTree(root)
	outside := t.TempDir()
	if err := os.MkdirAll(filepath.Join(here.Root, "wip"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(outside, filepath.Join(here.Root, "wip", "handoff")); err != nil {
		t.Skip("シンボリックリンクを作れない")
	}
	var out, errOut strings.Builder
	if code := learn(&out, &errOut, conf, root, here, childTicket(), Note{Note: "x"}); code != 1 || !strings.Contains(errOut.String(), "シンボリックリンク") {
		t.Errorf("%d %q", code, errOut.String())
	}
	if names, _ := os.ReadDir(outside); len(names) != 0 {
		t.Error("リンクの先に書いた")
	}
}

func TestUnsafePath(t *testing.T) {
	base, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	root := filepath.Join(base, "root")
	put(t, root, "real/file.txt", "x")
	put(t, root, "plain/sub/file.txt", "x")
	links := map[string]string{
		filepath.Join(root, "dirlink"):       filepath.Join(root, "real"),
		filepath.Join(root, "outer"):         t.TempDir(),
		filepath.Join(root, "real/filelink"): "file.txt",
		filepath.Join(root, "selfdir"):       root,
		filepath.Join(base, "rootlink"):      root,
	}
	for link, target := range links {
		if err := os.Symlink(target, link); err != nil {
			t.Skip("シンボリックリンクを作れない")
		}
	}
	cases := []struct {
		root, rel, want string
	}{
		{root, "plain/sub/file.txt", ""},
		{root, "dirlink/file.txt", filepath.Join(root, "dirlink")},
		{root, "real/filelink", filepath.Join(root, "real", "filelink")},
		{root, "selfdir/plain", filepath.Join(root, "selfdir")},
		{root, "outer/x", filepath.Join(root, "outer")},
		{root, "", ""},
		{root, "plain/nope/x", ""},
		{filepath.Join(base, "rootlink"), "plain/sub/file.txt", ""},
	}
	for _, c := range cases {
		if got := unsafePath(c.root, c.rel); got != c.want {
			t.Errorf("%s %s: %q, want %q", c.root, c.rel, got, c.want)
		}
	}
}

func TestRecordBlocked(t *testing.T) {
	root, conf := workspace(t)
	wt := filepath.Join(root, ".claude", "worktrees", child)
	if err := os.MkdirAll(wt, 0o755); err != nil {
		t.Fatal(err)
	}
	testkit.Git(t, wt, "init", "-q", "-b", "main")
	found := childTicket()
	blocked := func() (bool, string) {
		var errOut strings.Builder
		return RecordBlocked(&errOut, conf, root, found), errOut.String()
	}
	if ok, msg := blocked(); !ok || !strings.Contains(msg, "learn") {
		t.Errorf("記録が無いのに通した: %q", msg)
	}
	put(t, wt, recordRel(child), scaffoldText(child, "feat-1"))
	if ok, _ := blocked(); !ok {
		t.Error("追跡前に通した")
	}
	testkit.Git(t, wt, "add", "-f", recordRel(child))
	if ok, _ := blocked(); !ok {
		t.Error("コミット前に通した")
	}
	commit(t, wt)
	if ok, msg := blocked(); ok {
		t.Errorf("コミットした記録を止めた: %q", msg)
	}
	for _, body := range []string{"---\nticket: x\nlearnings:\n  - 気づき\n---\n", "---\nticket: x\nlearnings: 2\n---\n\n## 一つだけ\n"} {
		put(t, wt, recordRel(child), body)
		commit(t, wt)
		if ok, msg := blocked(); !ok || !strings.Contains(msg, recordRel(child)) {
			t.Errorf("%q を通した: %q", body, msg)
		}
	}
	// ディレクトリは記録ではない。
	if err := os.Remove(filepath.Join(wt, filepath.FromSlash(recordRel(child)))); err != nil {
		t.Fatal(err)
	}
	put(t, wt, recordRel(child)+"/a", "x\n")
	commit(t, wt)
	if ok, _ := blocked(); !ok {
		t.Error("ディレクトリを記録とした")
	}
	conf.Learning = "disable"
	if ok, _ := blocked(); ok {
		t.Error("無効なのに止めた")
	}
}

func TestParentBlocked(t *testing.T) {
	root, conf := workspace(t)
	parent := &ticket.Ticket{ID: "feat-1"}
	wt := filepath.Join(root, ".claude", "worktrees", "feat-1")
	blocked := func() (bool, string) {
		var errOut strings.Builder
		return ParentBlocked(&errOut, conf, root, parent), errOut.String()
	}
	if ok, _ := blocked(); ok {
		t.Error("ワークツリーが無いのに止めた")
	}
	if err := os.MkdirAll(wt, 0o755); err != nil {
		t.Fatal(err)
	}
	testkit.Git(t, wt, "init", "-q", "-b", "main")
	put(t, wt, "README", "x\n")
	put(t, wt, recordRel("feat-1-01-01"), rec("none"))
	commit(t, wt)
	if ok, msg := blocked(); ok {
		t.Errorf("処理するものが無いのに止めた: %q", msg)
	}
	put(t, wt, wiki.InboxDir+"/a.md", "x\n")
	if ok, msg := blocked(); !ok || !strings.Contains(msg, "未コミット") {
		t.Errorf("%q", msg)
	}
	commit(t, wt)
	if ok, msg := blocked(); !ok || !strings.Contains(msg, "consolidate feat-1") || !strings.Contains(msg, "（1 件）") {
		t.Errorf("%q", msg)
	}
	put(t, wt, recordRel("bad"), "壊れた\n")
	commit(t, wt)
	if ok, msg := blocked(); !ok || !strings.Contains(msg, "統合では直せない") {
		t.Errorf("%q", msg)
	}
	if ok, _ := ParentBlocked(&strings.Builder{}, conf, root, childTicket()), ""; ok {
		t.Error("子を親として見た")
	}
}
