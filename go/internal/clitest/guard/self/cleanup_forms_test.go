package self

import (
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
)

// ユーザだけが打つ実行ファイルの形を、hook の入口から確かめる（tickets/model/useronly）。
//
//  1. 状態の操作（ticket finish など）は、副命令の前に `--` を挟んでも止まる。挟んだ形に --record-tree を足すと、
//     実行ファイルの「write-and-push を通っていない」検査も外れる
//  2. 親をクローズしたときの片付け（sync tidy・worktree retire・worktree branch・ccnavi-user-clean-branch.sh）は、
//     メインのエージェントもサブエージェントも直には打てない。呼ぶのは sh の中（hook を通らない）か、
//     ユーザが ! を付けて打つときだけ
//
// 止める形ごとの表は useronly の単体テストが持つので、ここでは hook を通した判定と文面を見る。
const codeTicketApproval = "DENY_TICKET_APPROVAL_CLI"

func cleanupHook(t *testing.T, root, command, agent string) (string, string) {
	t.Helper()
	body := bash(command, root)
	if agent != "" {
		body["agent_id"] = agent
	}
	return verdict(t, hook(t, root, []string{"--mode", "enable"}, body, nil))
}

func newCleanupRoot(t *testing.T) string {
	t.Helper()
	root := base.TempDir(t)
	mkdirs(t, root+"/.claude")
	write(t, base.CommonPath(root, "rules"), `{"version": 1}`)
	return root
}

func TestDashesBeforeTheVerbAreReadThrough(t *testing.T) {
	t.Parallel()
	root := newCleanupRoot(t)
	for _, command := range []string{
		"ccnavi --record-writes /a --record-tree /b ticket -- finish i0001-01-01",
		"ccnavi ticket -- cancel i0001-01-01",
		"ccnavi review -- ready --result /tmp/r.json",
		"ccnavi ticket --  start i0001",
	} {
		for _, agent := range []string{"", "a1"} {
			decision, reason := cleanupHook(t, root, command, agent)
			if decision != "deny" || !strings.Contains(reason, codeTicketApproval) {
				t.Errorf("%q (agent=%q): 止めるはず: %s %s", command, agent, decision, reason)
			}
		}
	}
	if _, reason := cleanupHook(t, root, "ccnavi ticket -- status", ""); strings.Contains(reason, codeTicketApproval) {
		t.Errorf("読むだけの ticket -- status は止めない: %s", reason)
	}
}

func TestTheAgentsCannotTypeTheCleanup(t *testing.T) {
	t.Parallel()
	root := newCleanupRoot(t)
	for _, command := range []string{
		"ccnavi sync tidy i0001 self i0001 0123456789abcdef",
		"ccnavi --cwd /x sync -- tidy i0001 self i0001 0123456789abcdef",
		".ccnavi/bin/ccnavi worktree branch i0001-01-01",
		"ccnavi --root /x worktree branch i0001-01-01 confirmed",
		"ccnavi worktree retire i0001",
		"sh .ccnavi/scripts/ccnavi-user-clean-branch.sh i0001-01-01 --confirmed",
		"sh .ccnavi/scripts/ccnavi-user-clean-branch.sh i0001-01-01",
		"bash -x .ccnavi/scripts/ccnavi-user-clean-branch.sh --confirmed i0001-01-01",
		"cd /x && sh .ccnavi/scripts/ccnavi-user-clean-branch.sh i0001-01-01 --confirmed",
	} {
		for _, agent := range []string{"", "a1"} {
			decision, reason := cleanupHook(t, root, command, agent)
			if decision != "deny" {
				t.Errorf("%q (agent=%q): 止めるはず: %s", command, agent, reason)
				continue
			}
			if strings.Contains(command, "ccnavi-user-") {
				// ユーザ専用の sh は名前で止まる（案内は名前の説明）。
				if !strings.Contains(reason, "ccnavi-user-clean-branch.sh") {
					t.Errorf("%q: 名前を案内するはず: %s", command, reason)
				}
				continue
			}
			if !strings.Contains(reason, codeTicketApproval) || !strings.Contains(reason, "! を付けて") {
				t.Errorf("%q: %s と「! を付けて」の案内が要る: %s", command, codeTicketApproval, reason)
			}
		}
	}
}

func TestTheScriptsThatCallTheCleanupAreNotStoppedByIt(t *testing.T) {
	t.Parallel()
	root := newCleanupRoot(t)
	// sh の中から実行ファイルを呼ぶ分は hook を通らない。sh を打つ形はこの止めにヒットしない。
	// 生成物とワークツリーの片付け（ブランチは消さない）と、片付けを中で呼ぶ sh。
	for _, command := range []string{
		"sh .ccnavi/scripts/ccnavi-clean.sh --worktree i0001-01-01",
		"sh .ccnavi/scripts/ccnavi-clean.sh i0001-01-01 --dry-run",
		"sh .ccnavi/scripts/ccnavi-sync.sh i0001",
		"sh .ccnavi/scripts/ccnavi-sync.sh",
		"ccnavi worktree drop i0001-01-01",
		"ccnavi worktree remaining i0001",
	} {
		if _, reason := cleanupHook(t, root, command, ""); strings.Contains(reason, codeTicketApproval) {
			t.Errorf("%q は止めない: %s", command, reason)
		}
	}
}
