package self

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
)

// 下書きの置き場（wip/handoff/）を足場にして、守る場所へ書く形を止めること（policy/selfguard の
// builtin:guard-handoff-links・builtin:guard-handoff-ln）。
//
//  1. 名指しのツール: 利用者がルールファイルに書く保護は置き場の中を except の式で外せる。式はファイルを見ないので、
//     守る場所とハードリンクでつながったコピーへの Write も外れる。組み込みの 1 本が、置き場の中のつながった対象だけを
//     止める。ふつうのコピーへの Write は止めない
//  2. シェル: 置き場の中にリンクを作る ln・link・リンクを作る cp を止める。置き場の外のリンクとふつうの cp は止めない
const (
	linksRule = "builtin:guard-handoff-links"
	lnRule    = "builtin:guard-handoff-ln"
)

type linksRepo struct{ root, pad string }

func (l linksRepo) at(rel string) string { return filepath.Join(l.root, filepath.FromSlash(rel)) }

func newLinksRepo(t *testing.T) linksRepo {
	t.Helper()
	root := base.TempDir(t)
	// 共通のルールはテスト用の最小の組にする。ccnavi 自身の保護は組み込みで、ルールファイルに依らない。
	write(t, base.CommonPath(root, "rules"), ruleBlob(t, payload{
		"version": 1,
		"deny":    []payload{{"id": "blocked-fetch", "match": "WebFetch", "glob": "*://blocked.example/*", "message": "x"}},
	}))
	l := linksRepo{root: root}
	write(t, l.at(".claude/hooks/guard.sh"), "#!/bin/sh\n")
	write(t, l.at(".claude/settings.json"), "{}\n")
	l.pad = mkdirs(t, l.at("wip/handoff"))
	return l
}

func (l linksRepo) hook(t *testing.T, tool string, input map[string]any, cwd string) (string, string) {
	t.Helper()
	if cwd == "" {
		cwd = l.root
	}
	r := hook(t, l.root, []string{"--mode", "enable"}, preToolUse(tool, input, cwd), nil)
	return verdict(t, r)
}

func (l linksRepo) writeTo(t *testing.T, rel string) (string, string) {
	t.Helper()
	return l.hook(t, "Write", map[string]any{"file_path": l.at(rel), "content": "x\n"}, "")
}

func TestALinkedCopyInThePlaceIsNotWritten(t *testing.T) {
	t.Parallel()
	l := newLinksRepo(t)
	// 守る場所とつながった写しは、Write でも Edit でも止める。ワークツリーや projects の中の置き場も同じ。
	mustLink(t, l.at(".claude/hooks/guard.sh"), l.at("wip/handoff/.claude/hooks/guard.sh"))
	mustLink(t, l.at(".claude/settings.json"), l.at("wip/handoff/.claude/settings.json"))
	type call struct {
		name, tool string
		input      map[string]any
	}
	settings := l.at("wip/handoff/.claude/settings.json")
	calls := []call{
		{"hook の写しへの Write", "Write", map[string]any{"file_path": l.at("wip/handoff/.claude/hooks/guard.sh"), "content": "x\n"}},
		{"settings の写しへの Write", "Write", map[string]any{"file_path": settings, "content": "{}"}},
		{"settings の写しへの Edit", "Edit", map[string]any{"file_path": settings, "old_string": "{}", "new_string": "{ }"}},
	}
	for _, tree := range []string{".claude/worktrees/a", "projects/lib"} {
		rel := tree + "/wip/handoff/.claude/hooks/guard.sh"
		mustLink(t, l.at(".claude/hooks/guard.sh"), l.at(rel))
		calls = append(calls, call{tree + " の中の置き場", "Write", map[string]any{"file_path": l.at(rel), "content": "x\n"}})
	}
	for _, c := range calls {
		decision, reason := l.hook(t, c.tool, c.input, "")
		if decision != "deny" || !strings.Contains(reason, linksRule) {
			t.Errorf("%s: %s を理由に止めるはず: %s %s", c.name, linksRule, decision, reason)
		}
	}
}

func TestASymlinkedCopyIsNotWritten(t *testing.T) {
	t.Parallel()
	if !base.CanSymlink() {
		t.Skip("シンボリックリンクを作れない")
	}
	l := newLinksRepo(t)
	mkdirs(t, l.at("wip/handoff/.claude/hooks"))
	if err := os.Symlink(l.at(".claude/hooks/guard.sh"), l.at("wip/handoff/.claude/hooks/s.sh")); err != nil {
		t.Fatal(err)
	}
	if decision, reason := l.writeTo(t, "wip/handoff/.claude/hooks/s.sh"); decision != "deny" {
		t.Errorf("止めるはず: %s %s", decision, reason)
	}
}

func TestAPlainCopyAndANewDraftAreWritten(t *testing.T) {
	t.Parallel()
	l := newLinksRepo(t)
	write(t, l.at("wip/handoff/.claude/hooks/guard.sh"), "#!/bin/sh\n")
	for _, rel := range []string{
		"wip/handoff/.claude/hooks/guard.sh",
		"wip/handoff/.claude/hooks/new.sh",
		"wip/handoff/.claude/settings.json",
		"wip/handoff/.ccnavi/config/common/rules.yml",
		"wip/handoff/.ccnavi/docs/guide.md",
	} {
		if decision, reason := l.writeTo(t, rel); decision == "deny" {
			t.Errorf("%s は書けるはず: %s", rel, reason)
		}
	}
}

func TestAHardLinkOutsideThePlaceIsLeftToTheRules(t *testing.T) {
	t.Parallel()
	l := newLinksRepo(t)
	write(t, l.at("scratchpad/a.txt"), "a\n")
	mustLink(t, l.at("scratchpad/a.txt"), l.at("scratchpad/b.txt"))
	if _, reason := l.writeTo(t, "scratchpad/b.txt"); strings.Contains(reason, linksRule) {
		t.Errorf("置き場の外は %s の対象でない: %s", linksRule, reason)
	}
}

func TestMakingALinkInThePlaceIsStopped(t *testing.T) {
	t.Parallel()
	l := newLinksRepo(t)
	mkdirs(t, l.at("wip/handoff/.claude/hooks"))
	for _, c := range []struct{ command, cwd string }{
		{"ln -s ../../.claude/hooks/guard.sh wip/handoff/.claude/hooks/x.sh", ""},
		{"ln .claude/hooks/guard.sh wip/handoff/.claude/hooks/x.sh", ""},
		{"ln -sf /etc/passwd wip/handoff/p", ""},
		{"cd wip/handoff && ln -s /etc/passwd p", ""},
		{"/usr/bin/ln -s /etc/passwd wip/handoff/p", ""},
		{"link /etc/hosts wip/handoff/h", ""},
		{"cp -l .claude/hooks/guard.sh wip/handoff/x.sh", ""},
		{"cp --symbolic-link /etc/hosts wip/handoff/h", ""},
		{"cp -rs .claude wip/handoff/.claude", ""},
		{"ln -s /etc/passwd p", l.pad},
	} {
		decision, reason := l.hook(t, "Bash", map[string]any{"command": c.command}, c.cwd)
		if decision != "deny" || !strings.Contains(reason, lnRule) {
			t.Errorf("%q (cwd=%q): %s を理由に止めるはず: %s %s", c.command, c.cwd, lnRule, decision, reason)
		}
	}
}

func TestLinksElsewhereAndPlainCopiesAreNotStoppedByTheLinkRule(t *testing.T) {
	t.Parallel()
	l := newLinksRepo(t)
	for _, command := range []string{
		"ln -s a b",
		"cp .ccnavi/docs/a.md wip/handoff/.ccnavi/docs/a.md",
		"cp -p README.md wip/handoff/README.md",
		"grep -rn handoff docs",
	} {
		if _, reason := l.hook(t, "Bash", map[string]any{"command": command}, ""); strings.Contains(reason, lnRule) {
			t.Errorf("%q は %s で止めない: %s", command, lnRule, reason)
		}
	}
}
