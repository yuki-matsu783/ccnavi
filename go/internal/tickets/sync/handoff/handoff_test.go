package handoff

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/tickets/model/handoffnames"
	"ccnavi/internal/tickets/testkit"
)

// 表・断る形・書く直前の確かめ直し・起点の木との突き合わせ。Python と同じ答えかは python_test.go \u304c見る。

func TestKinds(t *testing.T) {
	cases := []struct {
		rel  string
		want KindID
	}{
		{".ccnavi/config/common/templates/a.yml", Template},
		{".ccnavi/config/project/templates/a.yml", Template},
		{"projects/lib/.ccnavi/config/project/templates/a.yml", Template},
		{".ccnavi/messages/a.yml", Messages},
		{".ccnavi/config/common/rules.yml", LayerConfig},
		{".ccnavi/config/project/phases.yml", LayerConfig},
		{"projects/lib/.ccnavi/config/project/risks.yml", LayerConfig},
		{".ccnavi/docs/a/b.md", Docs},
		{".ccnavi/CCNAVI.md", Docs},
		{".CCNAVI/Docs/Guide.MD", Docs},
		{".claude/skills/x/SKILL.md", Defense},
		{".claude/skills/x/y/z.md", Defense},
		{".ccnavi/scripts/ccnavi-git.sh", Defense},
		{".ccnavi/scripts/ccnavi-handoff-names.txt", Defense},
		{".claude/hooks/a/b/c.py", Defense},
		{".claude/settings.json", Defense},
		{".claude/settings.local.json", Defense},
		{"learnings/i0001.md", ""},
		{"repro.sh", ""},
		{".ccnavi/bin/ccnavi", ""},
		{".ccnavi/approved/doing/i0001.md", ""},
		{".ccnavi/config/common/rule-expectations.yml", ""},
		{".ccnavi/config/project/templates/sub/a.yml", ""},
		{".ccnavi/config/common/templates/x/templates/a.yml", ""},
		{".ccnavi/config/common/messages/deny.yml", ""},
		{".ccnavi/messages/sub/a.yml", ""},
		{"projects/p/.ccnavi/messages/a.yml", ""},
		{"projects/p/.ccnavi/config/common/rules.yml", ""},
		{".ccnavi/docs/.md", ""},
		{".claude/skills/x/y/z.txt", ""},
		{"src/x.py", ""},
		{".git/config", ""},
		{".gitmodules", ""},
		{"README.md", ""},
	}
	for _, c := range cases {
		got := KindID("")
		if k := KindOf(Key(c.rel)); k != nil {
			got = k.ID
		}
		if got != c.want {
			t.Errorf("%s: %q, want %q", c.rel, got, c.want)
		}
	}
	board, fresh := map[KindID]bool{}, map[KindID]bool{}
	for _, k := range kinds {
		board[k.ID], fresh[k.ID] = k.onBoard, k.creatable
	}
	wantBoard := map[KindID]bool{Template: true, Messages: false, LayerConfig: true, Docs: true, Defense: false}
	wantNew := map[KindID]bool{Template: true, Messages: true, LayerConfig: false, Docs: false, Defense: false}
	for id := range wantBoard {
		if board[id] != wantBoard[id] || fresh[id] != wantNew[id] {
			t.Errorf("%s: board=%v new=%v", id, board[id], fresh[id])
		}
	}
}

func TestNFDFoldsToTheSameKey(t *testing.T) {
	if Key(".ccnavi/docs/\u304b\u3099.md") != Key(".ccnavi/docs/\u304c.md") {
		t.Error("NFD と NFC \u304c同じ形にそろわない")
	}
}

// 表を直したら設計書の表も直す（文書に無い行を表に足さない）。
func TestKindsAgreeWithDesign(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join(testkit.RepoRoot(t), "docs", "21_design", "tickets", "handoff.md"))
	if err != nil {
		t.Fatal(err)
	}
	for _, k := range kinds {
		for _, shown := range k.shown {
			if !strings.Contains(string(raw), "`"+shown+"`") {
				t.Errorf("設計書に `%s` \u304c無い", shown)
			}
		}
	}
}

func TestWantedKey(t *testing.T) {
	want := Key(settingsRel)
	for _, arg := range []string{settingsRel, "wip/handoff/handoff-.claude/settings.json", ".CLAUDE/settings.json",
		`.claude\settings.json`, "./.claude//settings.json"} {
		if got, why := wantedKey(arg); got != want || why != "" {
			t.Errorf("%q: (%q, %q)", arg, got, why)
		}
	}
	for _, arg := range []string{"", "/abs", `C:\x`, "../x", ".ccnavi/../../x", "."} {
		if _, why := wantedKey(arg); why == "" {
			t.Errorf("%q を断らない", arg)
		}
	}
}

func workspace(t *testing.T, files map[string]string) (string, *config.Settings) {
	t.Helper()
	clearEnv(t)
	root, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	build(t, root, merged(files))
	conf, _ := config.Load(root)
	return root, conf
}

func entryOf(t *testing.T, plan *Plan, rel string) *Entry {
	t.Helper()
	for _, e := range plan.Entries {
		if e.Rel == rel {
			return e
		}
	}
	t.Fatalf("%s \u304c無い: %+v", rel, plan)
	return nil
}

func symlink(t *testing.T, target, link string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(link), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(target, link); err != nil {
		t.Skip("リンクを作れない:", err)
	}
}

func TestLinksAreRefused(t *testing.T) {
	t.Run("下書き\u304cリンク", func(t *testing.T) {
		root, conf := workspace(t, map[string]string{"outside.md": "x\n"})
		symlink(t, filepath.Join(root, "outside.md"), filepath.Join(root, "wip", "handoff", filepath.FromSlash(docsRel)))
		if e := entryOf(t, Prepare(conf, root, nil), docsRel); e.State() != StateRejected || !strings.Contains(strings.Join(e.Errors, " "), "リンク") {
			t.Errorf("断らない: %q", e.Errors)
		}
	})
	t.Run("置き場の中のリンクのディレクトリは読まない", func(t *testing.T) {
		root, conf := workspace(t, nil)
		outside := t.TempDir()
		build(t, outside, map[string]string{"docs/guide.md": "evil\n"})
		symlink(t, outside, filepath.Join(root, "wip", "handoff", ".ccnavi"))
		plan := Prepare(conf, root, nil)
		if len(plan.Entries) != 0 || len(plan.Ignored) != 1 || plan.Ignored[0] != (Ignored{".ccnavi", WhyLinkedDir}) {
			t.Errorf("entries=%v ignored=%v", plan.Entries, plan.Ignored)
		}
	})
	t.Run("取り込み先\u304cリンク", func(t *testing.T) {
		root, conf := workspace(t, map[string]string{"real.md": "real\n", draftRoot + docsRel: "new\n"})
		target := filepath.Join(root, filepath.FromSlash(docsRel))
		if err := os.Remove(target); err != nil {
			t.Fatal(err)
		}
		symlink(t, filepath.Join(root, "real.md"), target)
		if e := entryOf(t, Prepare(conf, root, nil), docsRel); e.State() != StateRejected {
			t.Errorf("断らない: %q", e.Errors)
		}
	})
	t.Run("置き場\u304cリンク", func(t *testing.T) {
		root, conf := workspace(t, nil)
		symlink(t, t.TempDir(), filepath.Join(root, "wip", "handoff"))
		if plan := Prepare(conf, root, nil); plan.Error == "" || plan.OK() {
			t.Errorf("断らない: %+v", plan)
		}
		if got := Pending(root); len(got) != 1 || got[0] != "wip/handoff/" {
			t.Errorf("Pending = %q", got)
		}
	})
}

func TestOddFilesAreRefused(t *testing.T) {
	t.Run("ハードリンク", func(t *testing.T) {
		root, conf := workspace(t, map[string]string{"other.md": "x\n"})
		draft := filepath.Join(root, "wip", "handoff", filepath.FromSlash(docsRel))
		if err := os.MkdirAll(filepath.Dir(draft), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.Link(filepath.Join(root, "other.md"), draft); err != nil {
			t.Skip("ハードリンクを作れない")
		}
		if e := entryOf(t, Prepare(conf, root, nil), docsRel); !strings.Contains(strings.Join(e.Errors, " "), "ハードリンク") {
			t.Errorf("断らない: %q", e.Errors)
		}
	})
	t.Run("名前付きパイプ", func(t *testing.T) {
		if ospath.Windows {
			t.Skip("名前付きパイプを作れない")
		}
		root, conf := workspace(t, nil)
		draft := filepath.Join(root, "wip", "handoff", filepath.FromSlash(docsRel))
		if err := os.MkdirAll(filepath.Dir(draft), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := mkfifo(draft); err != nil {
			t.Skip("名前付きパイプを作れない")
		}
		if e := entryOf(t, Prepare(conf, root, nil), docsRel); e.State() != StateRejected {
			t.Errorf("断らない: %q", e.Errors)
		}
	})
	t.Run(".git で始まる要素", func(t *testing.T) {
		rel := ".ccnavi/docs/.github/x.md"
		root, conf := workspace(t, map[string]string{rel: "a\n", draftRoot + rel: "b\n"})
		if e := entryOf(t, Prepare(conf, root, nil), rel); !strings.Contains(strings.Join(e.Errors, " "), ".git") {
			t.Errorf("断らない: %q", e.Errors)
		}
	})
	t.Run("同じ取り込み先に落ちる 2 本", func(t *testing.T) {
		root, conf := workspace(t, map[string]string{draftRoot + docsRel: "a\n"})
		other := filepath.Join(root, "wip", "handoff", ".ccnavi", "docs", "GUIDE.md")
		if _, err := os.Stat(other); err == nil {
			t.Skip("大文字小文字だけ違う名前を並べられない")
		}
		build(t, root, map[string]string{draftRoot + ".ccnavi/docs/GUIDE.md": "b\n"})
		plan := Prepare(conf, root, nil)
		if len(plan.Entries) != 2 {
			t.Fatalf("entries=%d", len(plan.Entries))
		}
		for _, e := range plan.Entries {
			if !strings.Contains(strings.Join(e.Errors, " "), "複数") {
				t.Errorf("%s を断らない: %q", e.Rel, e.Errors)
			}
		}
	})
}

func TestShellScriptsAreChecked(t *testing.T) {
	if _, err := exec.LookPath("sh"); err != nil {
		t.Skip("sh \u304c無い")
	}
	root, conf := workspace(t, map[string]string{
		draftRoot + scriptRel:                    "#!/bin/sh\nif then\n",
		".claude/hooks/a.sh":                     "echo old\n",
		draftRoot + "handoff-.claude/hooks/a.sh": "#!/bin/sh\necho new\n",
	})
	plan := Prepare(conf, root, nil)
	if e := entryOf(t, plan, scriptRel); len(e.Errors) != 1 || !strings.HasPrefix(e.Errors[0], "sh -n \u304c文法の誤りを言った（") {
		t.Errorf("文法の誤りを言わない: %q", e.Errors)
	}
	if e := entryOf(t, plan, ".claude/hooks/a.sh"); len(e.Errors) != 0 {
		t.Errorf("正しい sh を断った: %q", e.Errors)
	}
}

// 見せたあとに取り込み先\u304c変わる・途中にリンク\u304c置かれると、何も書かない。
func TestTargetIsCheckedAgainJustBeforeWriting(t *testing.T) {
	root, conf := workspace(t, map[string]string{draftRoot + docsRel: "new\n"})
	plan := Prepare(conf, root, nil)
	target := filepath.Join(root, filepath.FromSlash(docsRel))
	if err := os.WriteFile(target, []byte("moved\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if done := Apply(plan, plan.Expect(), false); done.OK || !strings.Contains(done.Error, "見せたあとに取り込み先\u304c変わった") {
		t.Errorf("書いた: %+v", done)
	}
	if raw, _ := os.ReadFile(target); string(raw) != "moved\n" {
		t.Errorf("取り込み先 = %q", raw)
	}

	root, conf = workspace(t, map[string]string{draftRoot + docsRel: "new\n"})
	plan = Prepare(conf, root, nil)
	docs := filepath.Join(root, ".ccnavi", "docs")
	aside := filepath.Join(root, "aside")
	if err := os.Rename(docs, aside); err != nil {
		t.Fatal(err)
	}
	symlink(t, aside, docs)
	if done := Apply(plan, plan.Expect(), false); done.OK || !strings.Contains(done.Error, "リンクに変わった") {
		t.Errorf("書いた: %+v", done)
	}
}

// 途中で失敗したら、書いたものを書く前のバイト列へ戻す。
func TestFailureInTheMiddlePutsBack(t *testing.T) {
	root, conf := workspace(t, map[string]string{
		draftRoot + ".ccnavi/CCNAVI.md": "new top\n", ".ccnavi/CCNAVI.md": "old top\n",
		draftRoot + docsRel: "new\n",
	})
	plan := Prepare(conf, root, nil)
	// 並び順で後に書く方（docs/guide.md）の下書きを、見せたあとに書き換える。
	if err := os.WriteFile(filepath.Join(root, "wip", "handoff", filepath.FromSlash(docsRel)), []byte("other\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	done := Apply(plan, plan.Expect(), false)
	if done.OK || !strings.Contains(done.Error, "書いたものは元に戻しました") {
		t.Fatalf("%+v", done)
	}
	if raw, _ := os.ReadFile(filepath.Join(root, ".ccnavi", "CCNAVI.md")); string(raw) != "old top\n" {
		t.Errorf("戻していない: %q", raw)
	}
}

// 表に仮の 1 行を足しても、同じ往復\u304c通る。
func TestARowAddedToTheNamesTableWorksTheSame(t *testing.T) {
	saved := names
	t.Cleanup(func() { names = saved })
	names = names.With(handoffnames.Pair{Original: ".ccnavi", Draft: "handoff-.ccnavi"})
	root, conf := workspace(t, map[string]string{"wip/handoff/handoff-.ccnavi/docs/guide.md": "new\n"})
	plan := Prepare(conf, root, nil)
	if len(plan.Entries) != 1 || plan.Entries[0].Rel != docsRel || !plan.OK() {
		t.Fatalf("%+v", plan)
	}
	if done := Apply(plan, plan.Expect(), false); !done.OK || done.RemovedDrafts[0] != "wip/handoff/handoff-.ccnavi/docs/guide.md" {
		t.Fatalf("%+v", done)
	}
	if raw, _ := os.ReadFile(filepath.Join(root, filepath.FromSlash(docsRel))); string(raw) != "new\n" {
		t.Errorf("取り込み先 = %q", raw)
	}
}

func TestSurvey(t *testing.T) {
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("git \u304c無い")
	}
	root, _ := workspace(t, map[string]string{
		draftRoot + "same.md": "s\n",
	})
	testkit.Git(t, root, "init", "-q", "-b", "main")
	testkit.Git(t, root, "add", "-A")
	testkit.Git(t, root, "commit", "-q", "-m", "init")
	build(t, root, map[string]string{
		draftRoot + docsRel:                         "# 手引き\n\n古い文\n",
		draftRoot + scriptRel:                       "#!/bin/sh\necho changed\n",
		draftRoot + "handoff-.claude/settings.json": "{\"hooks\": {}}\n",
		draftRoot + "notes/memo.md":                 "memo\n",
	})
	foreign, notes, ok := Survey(root, "HEAD")
	if !ok {
		t.Fatal("起点を読めない")
	}
	if strings.Join(foreign, ",") != "wip/handoff/.ccnavi/scripts/ccnavi-x.sh" {
		t.Errorf("foreign = %q", foreign)
	}
	if strings.Join(notes, ",") != "wip/handoff/notes/memo.md" {
		t.Errorf("notes = %q", notes)
	}
	if _, _, ok := Survey(root, "no-such-rev"); ok {
		t.Error("読めない起点を読めたと言う")
	}
}
