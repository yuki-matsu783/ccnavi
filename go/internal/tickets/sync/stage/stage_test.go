package stage

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/tickets/testkit"
)

// ccnavi-stage.sh の本体（Run）を、使い捨てのワークスペースで確かめる。下書きの置き場所・断るパス・取り込みの手順の出し方。
// sh を通す確認は tests/sh/test_stage_sh.py に数本だけ残す。

const (
	rulesRel = ".ccnavi/config/common/rules.yml"
	hookRel  = ".claude/hooks/lint-go.sh"
)

type fixture struct {
	t     *testing.T
	ws    string
	rules string
	hook  string
}

func newFixture(t *testing.T) *fixture {
	t.Helper()
	ws, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	ws = filepath.Join(ws, "ws")
	f := &fixture{t: t, ws: ws, rules: filepath.Join(ws, rulesRel), hook: filepath.Join(ws, hookRel)}
	testkit.CopyMessages(t, ws)
	testkit.Put(t, f.rules, "version: 1\n")
	testkit.Put(t, f.hook, "echo lint\n")
	return f
}

func (f *fixture) draftRules() string {
	return filepath.Join(f.ws, "wip", "handoff", ".ccnavi", "config", "common", "rules.yml")
}

func (f *fixture) draftHook() string {
	return filepath.Join(f.ws, "wip", "handoff", "handoff-.claude", "hooks", "lint-go.sh")
}

func (f *fixture) read(path string) string {
	data, _ := os.ReadFile(path)
	return string(data)
}

func (f *fixture) exists(path string) bool {
	_, err := os.Lstat(path)
	return err == nil
}

type result struct {
	code           int
	stdout, stderr string
}

// stage は cwd（空ならルート）で Run を呼ぶ。相対パスは、sh と同じく、プロセスの cwd から解かれる。
func (f *fixture) stage(cwd string, args ...string) result {
	f.t.Helper()
	if cwd == "" {
		cwd = f.ws
	}
	f.t.Chdir(cwd)
	var out, errOut bytes.Buffer
	code := Run(&out, &errOut, f.ws, cwd, args)
	return result{code, out.String(), errOut.String()}
}

func (f *fixture) worktree(name, text string) string {
	wt := filepath.Join(f.ws, ".claude", "worktrees", name)
	testkit.Put(f.t, filepath.Join(wt, rulesRel), text)
	return wt
}

func (r result) want(t *testing.T, code int, stdout, stderr []string, not ...string) {
	t.Helper()
	if r.code != code {
		t.Errorf("終了コード %d, want %d\n%s%s", r.code, code, r.stdout, r.stderr)
	}
	for _, w := range stdout {
		if !strings.Contains(r.stdout, w) {
			t.Errorf("標準出力に %q が無い:\n%s", w, r.stdout)
		}
	}
	for _, w := range stderr {
		if !strings.Contains(r.stderr, w) {
			t.Errorf("標準エラーに %q が無い:\n%s", w, r.stderr)
		}
	}
	for _, w := range not {
		if strings.Contains(r.stdout+r.stderr, w) {
			t.Errorf("出力に %q がある:\n%s%s", w, r.stdout, r.stderr)
		}
	}
}

// nexts は「次: 」で始まる行。
func nexts(out string) []string {
	var lines []string
	for _, line := range strings.Split(out, "\n") {
		if strings.HasPrefix(line, "次: ") {
			lines = append(lines, line)
		}
	}
	return lines
}

func TestDraftsKeepTheLayoutAndTheNextStep(t *testing.T) {
	f := newFixture(t)
	r := f.stage("", rulesRel, hookRel)
	r.want(t, 0, nil, nil)
	if f.read(f.draftRules()) != "version: 1\n" || f.read(f.draftHook()) != "echo lint\n" || f.read(f.rules) != "version: 1\n" {
		t.Error("同じ階層に写せていない、または元を触った")
	}
	// 次の一手は 1 行で、いま下書きを作ったパスを埋める
	n := nexts(r.stdout)
	if len(n) != 1 || strings.Contains(n[0], "<パス>") || !strings.HasSuffix(n[0], "--commands "+f.rules+" "+f.hook+" を打つ（取り込みの手順が出る）") {
		t.Errorf("次の一手:\n%s", r.stdout)
	}
	// 下書きが既にあるだけでも、次の一手は消えない
	if again := f.stage("", rulesRel); len(nexts(again.stdout)) != 1 {
		t.Errorf("2 回目:\n%s", again.stdout)
	}
	// 相対パス（cwd から）と絶対パス
	f = newFixture(t)
	f.stage(filepath.Join(f.ws, ".ccnavi"), "config/common/rules.yml").want(t, 0, nil, nil)
	f.stage("", f.hook).want(t, 0, nil, nil)
	if !f.exists(f.draftRules()) || !f.exists(f.draftHook()) {
		t.Error("下書きができていない")
	}
}

func TestDraftIsNeverOverwrittenWhenEdited(t *testing.T) {
	f := newFixture(t)
	f.stage("", rulesRel)
	testkit.Put(t, f.draftRules(), "version: 2\n")
	f.stage("", rulesRel).want(t, 0, []string{"上書きしない"}, nil)
	if f.read(f.draftRules()) != "version: 2\n" {
		t.Error("編集した下書きを上書きした")
	}
}

func TestPathsItCannotStageAreRefusedBeforeWritingAnything(t *testing.T) {
	f := newFixture(t)
	outside := filepath.Join(filepath.Dir(f.ws), "outside.txt")
	testkit.Put(t, outside, "x")
	testkit.Put(t, filepath.Join(f.ws, "wip", "handoff", "x.md"), "x")
	testkit.Put(t, filepath.Join(f.ws, "it's.md"), "x")
	for _, c := range []struct{ path, why string }{
		{outside, "の下にありません"},
		{".ccnavi/config/common/nothing.yml", "実在するファイルではありません"},
		{"wip/handoff/x.md", "下書きの置き場の中"},
		{"it's.md", "引用符"},
	} {
		f.stage("", c.path).want(t, 2, nil, []string{c.why})
	}
	// 良い引数のあとに悪い引数があっても、下書きを 1 つも書かない
	f.stage("", rulesRel, ".ccnavi/config/common/nothing.yml").want(t, 2, nil, []string{"実在するファイルではありません"})
	if f.exists(f.draftRules()) || f.exists(f.draftRules()+".orig") {
		t.Error("悪い引数があるのに下書きを書いた")
	}
	// リンクは、ワークスペースの外を指すものも、リンクそのものも断る
	outDir := filepath.Join(filepath.Dir(f.ws), "outdir")
	testkit.Put(t, filepath.Join(outDir, "f"), "secret")
	t.Run("links are refused", func(t *testing.T) {
		if os.Symlink(outDir, filepath.Join(f.ws, "link")) != nil {
			t.Skip("シンボリックリンクを作れない")
		}
		if os.Symlink(f.rules, filepath.Join(f.ws, "rules-link.yml")) != nil {
			t.Skip("シンボリックリンクを作れない")
		}
		f.stage("", "link/f").want(t, 2, nil, []string{"外"})
		f.stage("", "rules-link.yml").want(t, 2, nil, []string{"シンボリックリンク"})
		if f.exists(filepath.Join(f.ws, "wip", "handoff", "link")) {
			t.Error("リンクの先を下書きにした")
		}
	})
	// 引数の誤り
	f.stage("", "--bogus").want(t, 2, nil, nil)
	f.stage("").want(t, 2, nil, nil)
	f.stage("", "--help").want(t, 0, nil, nil)
}

// cwd がワークツリー（.claude/worktrees/<名前>）の中なら、下書きはそのワークツリーの wip/handoff/ に置く。
func TestDraftsInsideAWorktreeGoToThatWorktree(t *testing.T) {
	f := newFixture(t)
	wt := f.worktree("scratch-1-x", "version: wt\n")
	draft := filepath.Join(wt, "wip", "handoff", ".ccnavi", "config", "common", "rules.yml")
	t.Run("the draft starts from the worktree's version and the commands point at it", func(t *testing.T) {
		os.MkdirAll(filepath.Join(wt, "src"), 0o755)
		f.stage(filepath.Join(wt, "src"), f.rules).want(t, 0, nil, nil)
		if f.read(draft) != "version: wt\n" || f.exists(f.draftRules()) || f.read(f.rules) != "version: 1\n" {
			t.Error("下書きの置き場か中身が違う")
		}
		testkit.Put(t, draft, "version: edited\n")
		r := f.stage(wt, "--commands", f.rules)
		r.want(t, 0, []string{"--root '" + wt + "' '" + rulesRel + "'"}, nil, "mv ")
		// 下書きの置き場の中（ワークツリー側）は元として受けない
		f.stage(wt, draft).want(t, 2, nil, []string{"下書きの置き場の中"})
	})
	t.Run("every way of naming the file gives the same draft", func(t *testing.T) {
		for _, arg := range []string{rulesRel, filepath.Join(wt, rulesRel), f.rules} {
			if err := os.RemoveAll(filepath.Join(wt, "wip")); err != nil {
				t.Fatal(err)
			}
			f.stage(wt, arg).want(t, 0, nil, nil)
			entries, _ := os.ReadDir(filepath.Join(wt, "wip", "handoff"))
			if f.read(draft) != "version: wt\n" || len(entries) != 1 || entries[0].Name() != ".ccnavi" {
				t.Errorf("%s: 同じ下書きにならない（深い階層に置いた）", arg)
			}
		}
	})
	t.Run("a relative path gets an absolute next step", func(t *testing.T) {
		os.RemoveAll(filepath.Join(wt, "wip"))
		r := f.stage(wt, rulesRel)
		r.want(t, 0, []string{"--commands " + filepath.Join(wt, rulesRel)}, nil)
	})
	t.Run("a file only the root has is refused", func(t *testing.T) {
		os.RemoveAll(filepath.Join(wt, "wip"))
		f.stage(wt, f.hook).want(t, 2, nil, []string{filepath.Join(wt, hookRel), "実在するファイルではありません"})
		if f.exists(filepath.Join(wt, "wip")) {
			t.Error("断ったのに置き場を作った")
		}
	})
	t.Run("from the root another worktree's file is refused", func(t *testing.T) {
		inner := filepath.Join(wt, rulesRel)
		f.stage("", inner).want(t, 2, nil, []string{"別のワークツリー"})
		f.stage("", filepath.Join(f.ws, ".CLAUDE", "worktrees", filepath.Base(wt), rulesRel)).want(t, 2, nil, []string{"別のワークツリー"})
		if f.exists(filepath.Join(f.ws, "wip")) {
			t.Error("断ったのにルートに置き場を作った")
		}
	})
	t.Run("a linked handoff directory is refused", func(t *testing.T) {
		g := newFixture(t)
		wt := filepath.Join(g.ws, ".claude", "worktrees", "scratch-1-x")
		outside := filepath.Join(filepath.Dir(g.ws), "outside")
		os.MkdirAll(filepath.Join(wt, "wip"), 0o755)
		os.MkdirAll(outside, 0o755)
		if os.Symlink(outside, filepath.Join(wt, "wip", "handoff")) != nil {
			t.Skip("シンボリックリンクを作れない")
		}
		g.stage(wt, g.rules).want(t, 2, nil, []string{"シンボリックリンク"})
		if entries, _ := os.ReadDir(outside); len(entries) != 0 {
			t.Error("リンクの先へ書いた")
		}
	})
}

// --commands は、元と違う下書きに、取り込みの手順（ccnavi-user-handoff.sh）を出す。mv は出さない。
func TestCommandsPrintTheHandoffSteps(t *testing.T) {
	handoff := func(f *fixture) string { return "sh '" + f.ws + "/.ccnavi/scripts/ccnavi-user-handoff.sh'" }
	t.Run("an edited draft of every kind gets the handoff line, and an unedited one is reported", func(t *testing.T) {
		f := newFixture(t)
		f.stage("", rulesRel, hookRel)
		testkit.Put(t, f.draftHook(), "echo lint2\n")
		r := f.stage("", "--commands", rulesRel, hookRel)
		r.want(t, 1, []string{handoff(f) + " '" + hookRel + "'\n", "--yes --expect <上のハッシュ>"}, []string{"まだ編集していない"}, "mv ")
		if len(nexts(r.stdout)) != 0 {
			t.Error("揃っていないのに次の一手を出した")
		}
		if f.read(f.hook) != "echo lint\n" {
			t.Error("差し替えはユーザの手。スクリプトは動かさない")
		}
	})
	t.Run("all edited succeeds with one next step", func(t *testing.T) {
		f := newFixture(t)
		f.stage("", hookRel)
		testkit.Put(t, f.draftHook(), "echo lint2\n")
		r := f.stage("", "--commands", hookRel)
		r.want(t, 0, []string{handoff(f) + " '" + hookRel + "'\n"}, nil, "mv ")
		if n := nexts(r.stdout); len(n) != 1 || !strings.Contains(n[0], "取り込みの手順をユーザに案内してターンを終える") {
			t.Errorf("次の一手:\n%s", r.stdout)
		}
	})
	t.Run("the handoff line is printed after the original changed", func(t *testing.T) {
		// mv のように黙って上書きする案内は出さない。取り込みの --expect が、取り込みの時に止める。
		f := newFixture(t)
		f.stage("", hookRel)
		testkit.Put(t, f.draftHook(), "echo lint2\n")
		testkit.Put(t, f.hook, "echo someone-else\n")
		f.stage("", "--commands", hookRel).want(t, 0, []string{handoff(f) + " '" + hookRel + "' --yes"}, nil, "mv ")
		if f.read(f.hook) != "echo someone-else\n" {
			t.Error("元を動かした")
		}
	})
	t.Run("a kind outside the table is refused", func(t *testing.T) {
		f := newFixture(t)
		testkit.Put(t, filepath.Join(f.ws, "notes.txt"), "a\n")
		f.stage("", "notes.txt")
		testkit.Put(t, filepath.Join(f.ws, "wip", "handoff", "notes.txt"), "b\n")
		f.stage("", "--commands", "notes.txt").want(t, 1, nil, []string{"取り込みの対象外"}, "mv ", "ccnavi-user-handoff.sh")
	})
	t.Run("importable kinds point to the user handoff", func(t *testing.T) {
		f := newFixture(t)
		rel := ".ccnavi/config/common/templates/quickfix.yml"
		testkit.Put(t, filepath.Join(f.ws, rel), "a: 1\n")
		f.stage("", rel)
		testkit.Put(t, filepath.Join(f.ws, "wip", "handoff", rel), "a: 2\n")
		r := f.stage("", "--commands", rel)
		r.want(t, 0, []string{handoff(f) + " '" + rel + "'\n", "--yes --expect <上のハッシュ>"}, nil, "mv ")
		if n := nexts(r.stdout); len(n) != 1 || !strings.Contains(n[0], "取り込みの手順をユーザに案内してターンを終える") {
			t.Errorf("次の一手:\n%s", r.stdout)
		}
	})
	t.Run("mixed kinds join in one handoff line", func(t *testing.T) {
		f := newFixture(t)
		f.stage("", rulesRel, hookRel)
		testkit.Put(t, f.draftRules(), "version: 2\n")
		testkit.Put(t, f.draftHook(), "echo lint2\n")
		r := f.stage("", "--commands", rulesRel, hookRel)
		r.want(t, 0, []string{handoff(f) + " '" + rulesRel + "' '" + hookRel + "'\n"}, nil, "mv ")
		if len(nexts(r.stdout)) != 1 {
			t.Errorf("次の一手:\n%s", r.stdout)
		}
	})
	t.Run("without a draft or an edit prints nothing", func(t *testing.T) {
		f := newFixture(t)
		f.stage("", "--commands", rulesRel).want(t, 1, nil, []string{"下書きがまだ無い"}, "mv ")
	})
}
