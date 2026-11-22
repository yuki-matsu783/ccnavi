package ident

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"golang.org/x/text/unicode/norm"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/samples"
	"ccnavi/internal/harness/ticketkit"
	"ccnavi/internal/harness/treecache"
)

// 日本語を含む識別子で、承認 → 着手 → 判定 → 終える の主な経路が通ること。
//
// 実際の git リポジトリとワークツリーを使い捨ての置き場に作る。親 `feature-64-統合先の解決` と
// 子 `feature-64-統合先の解決-01-01` を通す。見るのは 5 つ。
//
//  1. 親のワークツリーの上で書いた提案は、既にある自分のブランチと重なると言われない（lint）
//  2. 承認で承認済みチケットが置かれ、着手で基準点が書かれる（ワークツリーの名前とブランチ名が日本語）
//  3. 子のワークツリーへの書き込みが子のチケットで判定される（範囲の外は止まる）
//  4. 子を終えると review/ へ移る。git の出力（status・diff --name-only）の 8 進の引用に惑わされず、
//     日本語のファイル名の承認済みチケットを読み戻せる
//  5. NFD の表記の提案は読めない（error）
const (
	parent = "feature-64-統合先の解決"
	child  = parent + "-01-01"
)

// setup は git のリポジトリと親のワークツリーを組む。2 本のテストは同じ木から始まるので、2 本目からは写しを戻す。
func setup(t *testing.T) (root, parentTree string) {
	root, _ = treecache.Build(t, "ident-family", func(dir string) struct{} {
		ticketkit.Write(t, filepath.Join(dir, ".claude", "keep"), "")
		ticketkit.Git(t, dir, "init", "--quiet", "-b", "main")
		ticketkit.Write(t, filepath.Join(dir, "src", "keep.py"), "print(1)\n")
		ticketkit.Write(t, filepath.Join(dir, ".gitignore"), ".claude/\n")
		ticketkit.Git(t, dir, "add", "-A")
		ticketkit.Git(t, dir, "commit", "--quiet", "-m", "init")
		ticketkit.Write(t, base.CommonPath(dir, "rules"), samples.Rules)
		samples.WritePlainPhases(t, dir)
		ticketkit.Git(t, dir, "worktree", "add", "--quiet", filepath.Join(dir, ".claude", "worktrees", parent), "-b", parent, "main")
		return struct{}{}
	})
	return root, filepath.Join(root, ".claude", "worktrees", parent)
}

func worktree(t testing.TB, root, name, from string) string {
	path := filepath.Join(root, ".claude", "worktrees", name)
	ticketkit.Git(t, root, "worktree", "add", "--quiet", path, "-b", name, from)
	return path
}

func ccnavi(t testing.TB, root, input string, args ...string) run.Result {
	t.Helper()
	prefix := []string{"--root", root, "--guard-core-files", "disable", "--restore-if-deny", "disable", "--guard-ticket-approval", "disable"}
	return run.Ccnavi(t, samples.Place(prefix, args), run.Opts{Input: input, Dir: base.Root()})
}

func hookWrite(t testing.TB, root, cwd, target string) string {
	t.Helper()
	payload, _ := json.Marshal(map[string]any{
		"hook_event_name": "PreToolUse", "tool_name": "Write", "cwd": cwd, "session_id": "s1",
		"tool_input": map[string]any{"file_path": target},
	})
	r := ccnavi(t, root, string(payload), "--mode", "enable")
	if strings.TrimSpace(r.Stdout) == "" {
		return ""
	}
	var out struct {
		Hook struct {
			Reason  string `json:"permissionDecisionReason"`
			Context string `json:"additionalContext"`
		} `json:"hookSpecificOutput"`
	}
	if err := json.Unmarshal([]byte(r.Stdout), &out); err != nil {
		t.Fatalf("hook の出力が JSON でない: %q", r.Stdout)
	}
	if out.Hook.Reason != "" {
		return out.Hook.Reason
	}
	return out.Hook.Context
}

func ok(t testing.TB, r run.Result, what string) {
	t.Helper()
	if r.Code != 0 {
		t.Fatalf("%s: code %d\n%s%s", what, r.Code, r.Stdout, r.Stderr)
	}
}

func propose(t testing.TB, tree, name, text string) {
	ticketkit.Write(t, filepath.Join(tree, "wip", "proposals", "todo", name+".md"), text)
}

func TestTheMainPathWithAJapaneseIdentifier(t *testing.T) {
	root, parentTree := setup(t)
	approved := filepath.Join(parentTree, ".ccnavi", "approved")
	propose(t, parentTree, parent, ticketkit.TicketText(parent, ticketkit.TicketOpts{Allow: []string{"src/*", "wip/*"}}))
	propose(t, parentTree, child, ticketkit.TicketText(child, ticketkit.TicketOpts{Parent: parent, Phase: 1, Allow: []string{"src/a/*"}}))
	ticketkit.Git(t, parentTree, "add", "-A")
	ticketkit.Git(t, parentTree, "commit", "--quiet", "-m", "tickets")

	// 1. 自分のワークツリーの上の提案は、自分のブランチと重なると言われない。形の warn も無い
	lint := ccnavi(t, root, "", "--lint", "--mode", "enable")
	if strings.Contains(lint.Stdout, "（親のブランチ名の規則）") || strings.Contains(lint.Stdout+lint.Stderr, "識別子に使えない文字") {
		t.Errorf("lint が日本語の識別子を咎めた:\n%s%s", lint.Stdout, lint.Stderr)
	}

	// 2. 承認と着手
	ok(t, ccnavi(t, root, "y\n", "user", "agree"), "user agree")
	if _, err := os.Stat(filepath.Join(approved, "doing", parent+".md")); err != nil {
		t.Fatalf("承認済みチケットが置かれない: %v", err)
	}
	ticketkit.Git(t, parentTree, "add", "-A")
	ticketkit.Git(t, parentTree, "commit", "--quiet", "-m", "approve")
	ok(t, ccnavi(t, root, "", "ticket", "start", parent), "start parent")
	childTree := worktree(t, root, child, parent)
	ok(t, ccnavi(t, root, "", "ticket", "start", child), "start child")
	ticketkit.Git(t, parentTree, "add", "-A")
	ticketkit.Git(t, parentTree, "commit", "--quiet", "-m", "start")
	doing, err := os.ReadFile(filepath.Join(approved, "doing", child+".md"))
	if err != nil {
		t.Fatal(err)
	}
	if head := strings.TrimSpace(ticketkit.Git(t, childTree, "rev-parse", "HEAD")); !strings.Contains(string(doing), head) {
		t.Errorf("基準点が書かれない: %s", doing)
	}

	// git の既定の出力は日本語のパスを 8 進で引用する（ccnavi は -z か quotePath=false で読む）
	if quoted := ticketkit.Git(t, parentTree, "log", "--name-only", "--format=", "-1", "HEAD~1"); !strings.Contains(quoted, `\`) {
		t.Errorf("git の出力が引用されていない（前提が崩れた）: %q", quoted)
	}

	// 3. 子のツリーへの書き込みは子のチケットで判定される
	if inside := hookWrite(t, root, parentTree, filepath.Join(childTree, "src", "a", "x.py")); strings.Contains(inside, "DENY_TICKET_SCOPE") {
		t.Errorf("範囲の中が止まった: %s", inside)
	}
	outside := hookWrite(t, root, parentTree, filepath.Join(childTree, "src", "b", "x.py"))
	if !strings.Contains(outside, "DENY_TICKET_SCOPE") || !strings.Contains(outside, child) {
		t.Errorf("範囲の外が子のチケットで止まらない: %s", outside)
	}

	// 4. 子を終える
	ticketkit.Write(t, filepath.Join(childTree, "src", "a", "x.py"), "print(2)\n")
	ticketkit.Git(t, childTree, "add", "-A")
	ticketkit.Git(t, childTree, "commit", "--quiet", "-m", "work")
	ok(t, ccnavi(t, root, "", "ticket", "finish", child), "finish child")
	if _, err := os.Stat(filepath.Join(parentTree, "wip", "proposals", "review", child+".md")); err != nil {
		t.Errorf("review/ へ移らない: %v", err)
	}
	lint = ccnavi(t, root, "", "--lint", "--mode", "enable")
	if strings.Contains(lint.Stdout+lint.Stderr, "識別子に使えない文字") {
		t.Errorf("終えたあとの lint が日本語の識別子を咎めた:\n%s%s", lint.Stdout, lint.Stderr)
	}
}

func TestAnNFDSpellingIsRefused(t *testing.T) {
	root, parentTree := setup(t)
	nfd := norm.NFD.String("feature-65-ガイド")
	propose(t, parentTree, nfd, ticketkit.TicketText(nfd, ticketkit.TicketOpts{Allow: []string{"src/*"}}))
	lint := ccnavi(t, root, "", "--lint", "--mode", "enable")
	if !strings.Contains(lint.Stdout+lint.Stderr, "NFC でない") {
		t.Errorf("NFD の表記を断らない:\n%s%s", lint.Stdout, lint.Stderr)
	}
}
