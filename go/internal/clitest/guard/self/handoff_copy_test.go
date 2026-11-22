package self

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
)

// 守る場所のファイルを、下書きの置き場（wip/handoff/）へ cp で写す形も止めること。
//
// 守る場所を直したいときは `ccnavi-stage.sh <元のパス>` で置き場へ写し、写しを直す（.ccnavi/docs/scratchpad.md）。
// シェルから書き込む形の保護は、cp の行き先の最後の語に守る名前があれば止めるので、
// `cp .ccnavi/scripts/ccnavi-git.sh wip/handoff/ccnavi-git.sh` も止まる。止めた文が ccnavi-stage.sh を案内することを見る。

const settingFilesRule = "builtin:guard-setting-files"

// handoffRepo は rules.yml・ccnavi-git.sh・wip/handoff/ を持つ git の木。
type handoffRepo struct {
	repo, pad string
}

func newHandoffRepo(t *testing.T) handoffRepo {
	t.Helper()
	repo := base.TempDir(t)
	initRepo(t, repo)
	write(t, base.CommonPath(repo, "rules"), ruleBlob(t, payload{
		"version": 1,
		"allow":   []payload{{"id": "anything-else", "match": "Bash|Read|Write|Edit", "regex": "."}},
	}))
	write(t, filepath.Join(repo, ".ccnavi", "scripts", "ccnavi-git.sh"), "#!/bin/sh\n")
	pad := mkdirs(t, filepath.Join(repo, "wip", "handoff"))
	return handoffRepo{repo, pad}
}

// decide は Bash の命令を hook に通した標準出力。cwd が空なら木の根。
func (h handoffRepo) decide(t *testing.T, command, cwd string) string {
	t.Helper()
	if cwd == "" {
		cwd = h.repo
	}
	r := hook(t, h.repo, []string{"--mode", "enable", "--guard-core-files", "enable"}, bash(command, cwd), nil)
	return r.Stdout
}

func (h handoffRepo) hits(said string) bool { return strings.Contains(said, settingFilesRule) }

func TestCopyingIntoTheScratchpadIsStoppedAndPointsToTheStageScript(t *testing.T) {
	t.Parallel()
	h := newHandoffRepo(t)
	for _, command := range []string{
		"cp .ccnavi/scripts/ccnavi-git.sh wip/handoff/ccnavi-git.sh",
		"cp " + h.repo + "/.ccnavi/scripts/ccnavi-git.sh " + h.pad + "/ccnavi-git.sh",
		"cp -f -- .ccnavi/scripts/ccnavi-git.sh wip/handoff/ccnavi-git.sh",
		"install .ccnavi/scripts/ccnavi-git.sh wip/handoff/ccnavi-git.sh",
	} {
		said := h.decide(t, command, "")
		if !h.hits(said) || !strings.Contains(said, "ccnavi-stage.sh") || !strings.Contains(said, "<元のパス>") {
			t.Errorf("%q: 止めて ccnavi-stage.sh を案内するはず: %s", command, said)
		}
	}
}

func TestTheGuardItselfStillStopsWrites(t *testing.T) {
	t.Parallel()
	h := newHandoffRepo(t)
	for _, command := range []string{
		"echo x > .ccnavi/config/common/rules.yml",
		"sed -i s/a/b/ .ccnavi/config/common/rules.yml",
		"cp /tmp/x .ccnavi/config/common/rules.yml",
	} {
		if said := h.decide(t, command, ""); !h.hits(said) {
			t.Errorf("%q: 止めるはず: %s", command, said)
		}
	}
}

// 置き場の中の写しの削除は通す。守る場所そのものと、置き場の外へ出る形は止める。
const wrapperGit = "sh .ccnavi/scripts/ccnavi-git.sh "

func newDeleteRepo(t *testing.T) handoffRepo {
	t.Helper()
	h := newHandoffRepo(t)
	write(t, filepath.Join(h.pad, ".ccnavi", "docs", "x.md"), "x\n")
	write(t, filepath.Join(h.repo, ".ccnavi", "docs", "x.md"), "x\n")
	return h
}

func (h handoffRepo) stopped(t *testing.T, command, cwd string) bool {
	t.Helper()
	return strings.Contains(h.decide(t, command, cwd), `"deny"`)
}

// forms は素の rm（絶対パス）とラッパーの rm（相対パス）の、同じ対象を消す 2 つの形。
func (h handoffRepo) forms(rel string) []string {
	return []string{"rm " + filepath.Join(h.repo, filepath.FromSlash(rel)), wrapperGit + "rm " + rel}
}

func TestDeletingAndWritingInThePlace(t *testing.T) {
	t.Parallel()
	h := newDeleteRepo(t)
	copyRel := "wip/handoff/.ccnavi/docs/x.md"
	absolute := h.pad + "/.ccnavi/docs/x.md"
	cases := []struct {
		name     string
		cwd      string
		commands []string
		stops    bool
	}{
		{"置き場の中の写しの削除は通す", "", []string{
			"rm " + absolute,
			"rm -f -- " + absolute,
			"rm -r " + h.pad + "/.ccnavi",
			"rm " + h.repo + "/WIP/Handoff/.ccnavi/docs/x.md",
			"rm " + absolute + " && echo done",
			wrapperGit + "rm " + copyRel,
			wrapperGit + "rm -f " + copyRel,
			wrapperGit + "rm WIP/Handoff/.ccnavi/docs/x.md",
			wrapperGit + "rm " + absolute,
		}, false},
		// 素の rm は絶対パスだけ外す（sh -c や find -execdir の中の cd が見えない）。
		{"相対パスの素の rm は止める", "", []string{
			"rm " + copyRel,
			"sh -c 'cd /tmp && rm " + copyRel + "'",
			"find /tmp -maxdepth 0 -execdir rm " + copyRel + " ;",
		}, true},
		{"本物のファイルの削除は止める", "", []string{
			"rm .ccnavi/docs/x.md",
			"rm " + absolute + " .ccnavi/docs/x.md",
			"rm " + absolute + " && rm .ccnavi/docs/x.md",
			"rm " + absolute + " > .ccnavi/docs/x.md",
			"cd .ccnavi && rm docs/x.md",
			wrapperGit + "rm .ccnavi/docs/x.md",
			wrapperGit + "rm " + copyRel + " .ccnavi/docs/x.md",
			wrapperGit + "rm " + copyRel + " && " + wrapperGit + "rm .ccnavi/docs/x.md",
		}, true},
		{"ホームや展開される語は置き場の中でも止める", h.pad, []string{
			"rm ~/.claude/settings.json",
			"rm ~/.ccnavi/docs/x.md",
			"rm ~root/.ccnavi/docs/x.md",
			wrapperGit + "rm ~/.claude/settings.json",
			wrapperGit + "rm ~/.ccnavi/docs/x.md",
			wrapperGit + "rm ~root/.ccnavi/docs/x.md",
		}, true},
	}
	for _, c := range cases {
		for _, command := range c.commands {
			if got := h.stopped(t, command, c.cwd); got != c.stops {
				t.Errorf("%s: %q: 止める=%v のはず", c.name, command, c.stops)
			}
		}
	}
	// 置き場へ書く形は、守る場所ごと止める。
	for _, command := range []string{
		"cp .ccnavi/docs/x.md wip/handoff/.ccnavi/docs/x.md",
		"mv /tmp/a wip/handoff/.ccnavi/docs/x.md",
		"echo x > wip/handoff/.ccnavi/docs/x.md",
	} {
		if said := h.decide(t, command, ""); !h.hits(said) {
			t.Errorf("置き場への書き込み %q を止めるはず: %s", command, said)
		}
	}
}

func TestAGlobOrAPlaceNotRightUnderTheTreeIsStopped(t *testing.T) {
	t.Parallel()
	h := newDeleteRepo(t)
	for _, rel := range []string{
		"wip/handoff/.ccnavi/docs/*",
		"wip/handoff/.ccnavi/docs/x.m?",
		"docs/wip/handoff/.ccnavi/docs/x.md",
		"wip/handoff/../.ccnavi/docs/x.md",
		"wip/handoff/../../.ccnavi/docs/x.md",
		"wip/handoffx/.ccnavi/docs/x.md",
	} {
		for _, command := range h.forms(rel) {
			if !h.stopped(t, command, "") {
				t.Errorf("%q を止めるはず", command)
			}
		}
	}
}

func TestALinkedCopyIsStopped(t *testing.T) {
	t.Parallel()
	h := newDeleteRepo(t)
	real := filepath.Join(h.repo, ".ccnavi", "docs", "x.md")
	hard := filepath.Join(h.pad, ".ccnavi", "docs", "hard.md")
	mustLink(t, real, hard)
	for _, command := range h.forms("wip/handoff/.ccnavi/docs/hard.md") {
		if !h.stopped(t, command, "") {
			t.Errorf("ハードリンクの写し %q を止めるはず", command)
		}
	}
	if !base.CanSymlink() {
		t.Skip("シンボリックリンクを作れない機械")
	}
	_ = os.Remove(hard)
	if err := os.RemoveAll(filepath.Join(h.pad, ".ccnavi")); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(filepath.Join(h.repo, ".ccnavi"), filepath.Join(h.pad, ".ccnavi")); err != nil {
		t.Fatal(err)
	}
	for _, command := range h.forms("wip/handoff/.ccnavi/docs/x.md") {
		if !h.stopped(t, command, "") {
			t.Errorf("リンクの写し %q を止めるはず", command)
		}
	}
}
