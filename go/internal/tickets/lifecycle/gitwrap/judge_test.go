package gitwrap

import (
	"strings"
	"testing"
)

// 引数だけで決まる Judge の答え（git の状態を読まずに決まる形）。リポジトリでない場所で判定するので、
// 統合先のユーザのチェックアウトでも親のワークツリーでもない。止める識別子と、文が言うべきこと（has）を見る。
// 状態で決まる形（push・統合先の merge と pull・親のワークツリー・サブディレクトリからのパス）は、
// tests/sh/test_gitwrap.py がワークスペースを組んで確かめる。
func TestJudgeByTheArguments(t *testing.T) {
	const store = ".ccnavi/approved/doing/i0001.md"
	const review = "wip/proposals/review/i0001-01-01.md"
	sync := []string{"ccnavi-sync.sh", "別のコミットへ移します"}
	spell := []string{"略さずに"}
	wrapper := []string{"ccnavi-git.sh"}
	for _, c := range []struct {
		args string
		id   string // 通すなら空
		has  []string
	}{
		// 点検を飛ばす形と、書きかけを破棄する形
		{"checkout -- tracked.txt", "checkout-path", wrapper},
		{"checkout -f main", "checkout-force", wrapper},
		{"checkout -fq topic", "checkout-force", nil},
		{"switch --discard-changes main", "checkout-discard", nil},
		{"pull --force", "fetch-force", wrapper},
		{"worktree remove --force p", "worktree-remove-force", nil},
		{"worktree remove -ff x", "worktree-remove-force", nil},
		{"restore .", "restore-whole-tree", nil},
		{"restore", "restore-no-path", wrapper},
		{"merge -X ours other", "merge-discard-side", nil},
		{"merge -Xtheirs other", "merge-discard-side", nil},
		{"merge -s ours other", "merge-discard-side", nil},
		{"merge -sours topic", "merge-discard-side", nil},
		{"merge --strategy-option theirs topic", "merge-discard-side", nil},
		{"merge --no-verify other", "merge-no-verify", nil},
		// rm は通すが、-f・ツリー全体・パスの無い形は止める
		{"rm tracked.txt", "", nil},
		{"rm", "rm-no-path", nil},
		{"rm -f tracked.txt", "rm-force", nil},
		{"rm --force tracked.txt", "rm-force", nil},
		{"rm -rf .", "rm-force", nil},
		{"rm -rf tracked.txt", "rm-force", nil},
		{"rm .", "rm-whole-tree", nil},
		// 既存のブランチを別のコミットへ移す形
		{"checkout -B b upstream", "checkout-force-branch", sync},
		{"checkout -qB b upstream", "checkout-force-branch", sync},
		{"switch --force-create b upstream", "checkout-force-branch", sync},
		{"switch --force-create=b upstream", "checkout-force-branch", sync},
		{"switch -C b upstream", "moved-root", nil},
		{"checkout -b fresh HEAD", "", nil},
		{"checkout -qbnew2 main", "", nil},
		{"switch -cnew3", "", nil},
		{"switch --create fresh2 HEAD", "", nil},
		// 許可リストに無い形・略した長いオプション
		{"switch --force-c topic HEAD", "option-not-allowed", spell},
		{"switch --force-cr=topic HEAD", "option-not-allowed", spell},
		{"switch --discard-ch topic", "option-not-allowed", spell},
		{"switch --for topic", "option-not-allowed", spell},
		{"checkout --for topic", "option-not-allowed", spell},
		{"checkout --unknown-option", "option-not-allowed", spell},
		{"pull --rebas origin main", "option-not-allowed", spell},
		{"pull -Xours origin main", "option-not-allowed", spell},
		{"fetch --prun origin", "option-not-allowed", spell},
		{"merge --strategy-o=ours topic", "option-not-allowed", spell},
		{"merge --no-verif topic", "option-not-allowed", spell},
		{"rm --forc tracked.txt", "option-not-allowed", spell},
		{"worktree remove --forc x", "option-not-allowed", spell},
		{"restore --sour=HEAD~1 " + store, "option-not-allowed", spell},
		// fetch・pull は、リモート名とブランチ名だけ（refspec・URL は通さない）
		{"fetch origin +refs/heads/b:refs/heads/b", "fetch-refspec", []string{"ccnavi-git.sh fetch <リモート> <ブランチ>", "ccnavi-sync.sh <ブランチ> が進めます"}},
		{"fetch origin b:b", "fetch-refspec", nil},
		{"fetch origin b:refs/heads/other", "fetch-refspec", nil},
		{"fetch origin +b", "fetch-refspec", nil},
		{"fetch origin :refs/heads/b", "fetch-refspec", nil},
		{"fetch --refmap=+refs/heads/*:refs/remotes/o/* origin", "option-not-allowed", nil},
		{"fetch /tmp/origin.git:x", "fetch-refspec", nil},
		{"fetch https://example.invalid/x.git b", "fetch-refspec", nil},
		{"pull origin b:b", "fetch-refspec", nil},
		{"pull origin +b", "fetch-refspec", nil},
		{"fetch --depth=1 origin +main:main", "fetch-refspec", nil},
		{"fetch --prune origin", "fetch-force", nil},
		{"fetch -p origin", "fetch-force", nil},
		{"fetch --force origin", "fetch-force", nil},
		{"pull --rebase origin b", "pull-rebase", nil},
		{"pull --rebase=merges origin b", "pull-rebase", nil},
		{"pull -r origin main", "pull-rebase", nil},
		// 履歴を書き換えない足した形は通す
		{"fetch --depth=1 origin b", "", nil},
		{"fetch --depth 1 origin b", "", nil},
		{"fetch --deepen=1 origin b", "", nil},
		{"fetch --shallow-since=2000-01-01 origin b", "", nil},
		{"fetch --unshallow origin b", "", nil},
		{"fetch --show-forced-updates origin b", "", nil},
		{"fetch --no-show-forced-updates origin b", "", nil},
		{"fetch --no-write-fetch-head origin b", "", nil},
		{"fetch --write-fetch-head origin b", "", nil},
		{"pull --depth=1 origin b", "", nil},
		{"pull --autostash origin b", "", nil},
		{"pull --no-autostash origin b", "", nil},
		{"merge --autostash origin/b", "", nil},
		{"merge --no-autostash origin/b", "", nil},
		// merge-file は結果を標準出力に出す形だけ
		{"merge-file c b o", "merge-file-no-stdout", nil},
		{"merge-file --union c b o", "merge-file-no-stdout", nil},
		{"merge-file -L -p c b o", "merge-file-no-stdout", nil},
		{"merge-file --union -- -p b o", "merge-file-no-stdout", nil},
		{"merge-file --union --object-id :2:t :1:t :3:t", "merge-file-no-stdout", nil},
		{"merge-file -p --no-stdout c b o", "merge-file-option", nil},
		{"merge-file --std c b o", "merge-file-option", nil},
		{"merge-file -pq c b o", "merge-file-option", nil},
		{"merge-file -p --obj c b o", "merge-file-option", nil},
		{"merge-file -p --no-object-id c b o", "merge-file-option", nil},
		{"merge-file -p --union -L ours -L base -L theirs c b o", "", nil},
		// 承認済みチケットの置き場を別のコミットの中身に戻す・衝突を片側にそろえる形
		{"checkout HEAD~1 " + store, "checkout-store", nil},
		{"checkout HEAD~1 " + review, "checkout-store", nil},
		{"checkout HEAD~1 tracked.txt " + store, "checkout-store", nil},
		{"checkout HEAD~1 .ccnavi", "checkout-store", nil},
		{"checkout HEAD~1 .ccnavi/approved/", "checkout-store", nil},
		{"checkout HEAD~1 wip", "checkout-store", nil},
		{"checkout HEAD~1 x/../" + store, "checkout-store", nil},
		{"checkout HEAD~1 .CCNAVI/Approved/doing/i0001.md", "checkout-store", nil},
		{`checkout HEAD~1 .ccnavi\approved\doing\i0001.md`, "checkout-store", nil},
		{"checkout HEAD~1 .ccnavi/approved/*", "checkout-store", nil},
		{"checkout HEAD~1 :(glob).ccnavi/**", "checkout-store", nil},
		{"checkout -p HEAD~1 " + store, "option-not-allowed", nil},
		{"checkout HEAD~1 --pathspec-from-file=list.txt", "checkout-store", nil},
		{"checkout HEAD~1 tracked.txt", "", nil},
		{"restore --source HEAD~1 " + store, "restore-store-source", nil},
		{"restore --source=HEAD~1 -- " + store, "restore-store-source", nil},
		{"restore -s HEAD~1 " + store, "restore-store-source", nil},
		{"restore -sHEAD~1 " + store, "restore-store-source", nil},
		{"restore -Ss HEAD~1 " + store, "restore-store-source", nil},
		{"restore -Ws HEAD~1 " + store, "restore-store-source", nil},
		{"restore -SWs HEAD~1 -- " + review, "restore-store-source", nil},
		{"restore --source HEAD~1 .ccnavi", "restore-store-source", nil},
		{"restore --source HEAD~1 --pathspec-from-file=list.txt", "restore-store-source", nil},
		{"restore --source HEAD~1 wip/proposals/review/*", "restore-store-source", nil},
		{"restore --ours -- " + store, "restore-store-side", []string{"merge --abort", "ccnavi-git.sh"}},
		{"restore --theirs " + review, "restore-store-side", []string{"merge --abort", "ccnavi-git.sh"}},
		{"restore --source HEAD~1 tracked.txt", "", nil},
		{"restore --source HEAD~1 .ccnavi-other/x.md", "", nil},
		{"restore " + store, "", nil},
		{"restore --staged " + store, "", nil},
		// worktree add は行き先の名前とブランチ名を揃える形だけ
		{"worktree add", "worktree-no-dest", []string{"ccnavi-git.sh worktree add .claude/worktrees/<名前> -b <名前>"}},
		{"worktree add .claude/worktrees/a -b b", "worktree-name", []string{"-b b", "worktree add .claude/worktrees/b -b b",
			"そのブランチをチェックアウトしているものとして探します", "/ を - に置き換えたもの"}},
		{"worktree add .claude/worktrees/a -b b HEAD", "worktree-name", nil},
		{"worktree add -B a .claude/worktrees/a", "worktree-force-branch", nil},
		{"worktree add --detach .claude/worktrees/a HEAD", "worktree-detach", nil},
		{"worktree add -d .claude/worktrees/a HEAD", "worktree-detach", nil},
		{"worktree add -f .claude/worktrees/a -b a", "worktree-force", nil},
		{"worktree add --force .claude/worktrees/a -b a", "worktree-force", nil},
		{"worktree add .claude/worktrees/a HEAD", "worktree-name", nil},
	} {
		dir := t.TempDir()
		e := &Env{Names: Names{Self: "sh .ccnavi/scripts/ccnavi-git.sh", Sync: "sh .ccnavi/scripts/ccnavi-sync.sh"},
			Root: dir, Cwd: dir, ParentTree: func(string, string) int { return 1 }}
		a := Judge(e, strings.Fields(c.args))
		got := ""
		if a.Deny != nil {
			got = a.Deny.ID
		}
		if got != c.id {
			t.Errorf("%s: %q, want %q", c.args, got, c.id)
			continue
		}
		if a.Deny == nil {
			continue
		}
		for _, h := range c.has {
			if !strings.Contains(a.Deny.Message, h) {
				t.Errorf("%s: 文に %q が無い: %s", c.args, h, a.Deny.Message)
			}
		}
		// 代わりに示す形はラッパーの形にする（生の git は hook が止めるので、案内された先でもう一度止まる）。
		rest := strings.ReplaceAll(a.Deny.Message, e.Self, "")
		for _, raw := range []string{"git stash", "git restore", "git branch", "git worktree", "ADR"} {
			if strings.Contains(rest, raw) {
				t.Errorf("%s: 文が %s を言っている: %s", c.args, raw, a.Deny.Message)
			}
		}
	}
}
