package shask

import (
	"fmt"
	"io"
	"strconv"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/tickets/model/ids"
)

// unknown は、置き場を読めずに決められなかった値（名前が識別子でない・ツリーが無い）。
const unknown answer = "unknown"

// pushTrace は、親のブランチ branch を一度は送った形跡があるか。形跡は、手元の `refs/remotes/origin/<ブランチ>`
// （`branch.<ブランチ>.remote` は使わない。origin/main から切っただけの未 push の親でも設定されるため）か、
// .requested・手元の退避・着手済み（started_at）のどれか（fetch.prune で参照が消えても残る）。承認済みというだけでは
// 形跡にしない（直接作業で提案を書き、同名のワークツリーを切って承認し、最初の push を打つ流れが、初回の push として
// 通らなくなる）。着手（ticket start）は push してから完了するので、started_at があれば一度は push している。
// push のゲート（pushGate）、write-and-push の「一度も送っていない親」、ccnavi-sync.sh の「消えた親」が同じ述語を使う。
func pushTrace(path, branch string, f parentFacts) bool {
	if git.Run(path, []string{"show-ref", "--verify", "--quiet", "refs/remotes/origin/" + branch}, git.Options{}).OK() {
		return true
	}
	return f.requested == yes || f.archived == yes || f.started == yes
}

// pushGate は `tree gate <ツリー> <ブランチ> <識別子> <秒>`。親のブランチの push のゲートの判定で、頭に続けて
// 次の一手を `gate <語>` で書く。ls-remote は sh が打つ（ネットワークは sh の仕事）。
//
//	gate pass       そのまま送ってよい（親のワークツリーでない・親でない・送った形跡が無い最初の push）
//	gate stop       確かめられないので送らない。続けて why <理由>
//	gate ls-remote  リモートに在るかを sh が確かめる。続けて、無いときの missing <理由> と、聞けないときの
//	                unreachable <理由>
//
// 処理フロー:
//  1. ツリーが <ワークスペースルート>/.claude/worktrees/<識別子> で、ブランチから作る識別子が同じ（大文字小文字は区別
//     しない）ときだけ見る。ほかのブランチと子の push には掛けない
//  2. 置き場を読み（parentFactsOf）、親でないと分かれば通す。名前が識別子でない・ツリーが無いときは unknown として続ける
//  3. 送った形跡（pushTrace）が無ければ最初の push として通す。ただし承認済みチケットを読めないときは、着手済みかを
//     確かめられないので stop
//  4. 形跡があれば ls-remote を頼む。親のブランチがリモートに無いまま push すると、改名・消し間違い・破棄した親子の
//     チケットの理由を確かめないまま同じ名前で作り直してしまう
func pushGate(stdout, stderr io.Writer, root string, conf *config.Settings, path, branch, ident, secs string) bool {
	if _, err := strconv.Atoi(secs); err != nil || branch == "" {
		fmt.Fprintf(stderr, "ccnavi: tree gate の秒数 %q かブランチ %q が読めません。\n", secs, branch)
		return false
	}
	pass := func() bool { writeLines(stdout, head, "gate pass"); return true }
	if !pathcase.SameReal(path, ospath.Join(tree.WorktreesPath(root), ident)) || !ids.SameID(ids.BranchToID(branch), ident) {
		return pass()
	}
	f := parentFacts{held: unknown, approved: unknown, requested: unknown, archived: unknown, started: unknown}
	if ids.IsValidFamily(ident) && ospath.IsDir(path) {
		f = parentFactsOf(root, conf, path, ident)
	}
	if f.held == no {
		return pass()
	}
	if !pushTrace(path, branch, f) {
		if f.approved == unreadable {
			writeLines(stdout, head, "gate stop",
				"why 親のブランチ "+branch+" の承認済みチケットを読めないため、リモートから消えた親かを確かめられない。チケットの frontmatter を直してから打ち直してください")
			return true
		}
		return pass()
	}
	writeLines(stdout, head, "gate ls-remote",
		"missing 親のブランチ "+branch+" がリモートに無い。改名・消し間違いなら、ユーザに元の名前で戻してもらい（ユーザがターミナルで git push origin "+branch+" を一度打つ）、破棄した親なら親のワークツリーを片付ける。診断は ccnavi-sync.sh "+ident,
		"unreachable 親のブランチ "+branch+" がリモートにあるかを確かめられない（ls-remote が失敗したか、"+secs+" 秒で終わらなかった）。ネットワークと認証を確かめて打ち直してください")
	return true
}
