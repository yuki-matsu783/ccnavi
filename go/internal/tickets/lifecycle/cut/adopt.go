package cut

import (
	"ccnavi/internal/policy/texts"
	"strings"

	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/tickets/lifecycle/integrate"
)

// makeDirs は親から順にディレクトリを作る。
func makeDirs(dir string) error { return fsio.MakeDirs(dir) }

// prefixed は各行の頭に 2 字の空白を付けた、先頭 50 行。
func prefixed(text string) string {
	all := lines(text)
	if len(all) > 50 {
		all = all[:50]
	}
	for i := range all {
		all[i] = "  " + all[i]
	}
	return strings.Join(all, "\n")
}

// adoptTree は、記録の無い既存の直接作業のワークツリーを version 4 の記録に引き取る。1 回目は推定した起点 S・取り込む
// 範囲・差分の stat を見せて確認コードを出し、終了コード 3 で止まる。2 回目は --accept <確認コード> が一致したときだけ、
// 記録（snap_wip no・confirm adopt）と S の ref を書く。
func (r *run) adoptTree() {
	if r.baseArg == "" {
		r.fail("adopt-no-base", "--adopt には --base <取り込み先> が要ります（今のユーザのチェックアウトのブランチは "+r.target+"）。ユーザに取り込み先を確かめてから付けてください。", exitUsage)
		return
	}
	if r.wipMode != "" {
		r.fail("adopt-wip", "--adopt では --copy-wip / --no-wip は指定できません（書きかけはコピーしない。起点は merge-base から決める）。", exitUsage)
		return
	}
	common := commonOf(r.top)
	if common == "" {
		r.fail("no-common-dir", "git の共通ディレクトリを読めません。", exitUnmet)
		return
	}
	if ospath.DiskLexists(ospath.Join(common, tree.DirectDir, r.name)) {
		r.fail("adopt-recorded", r.name+" には直接作業の記録が既にあります（"+ospath.Join(common, tree.DirectDir, r.name)+"）。引き取りは記録の無いワークツリーだけです。", exitUnmet)
		return
	}
	if !ospath.IsDir(r.dest) || ospath.IsLink(r.dest) {
		r.fail("adopt-no-worktree", "ワークツリー "+r.dest+" がありません。引き取るのは .claude/worktrees/"+r.name+" の既存のワークツリーです。", exitUnmet)
		return
	}
	if dc := commonOf(r.dest); dc == "" || !samePath(dc, common) {
		r.fail("adopt-common-dir", "ワークツリー "+r.dest+" の git の共通ディレクトリが、実行した場所のリポジトリと違います。別のリポジトリのワークツリーは引き取りません。", exitUnmet)
		return
	}
	if have := r.branchAt(r.dest); have != "refs/heads/"+r.name {
		if have == "" {
			have = "ワークツリーの登録が無い"
		}
		r.fail("adopt-branch", r.dest+" は、ブランチ "+r.name+" を開いているこのリポジトリのワークツリーではありません（"+have+"）。", exitUnmet)
		return
	}
	tip := gitOut(r.in.Cwd, 0, "rev-parse", "refs/heads/"+r.name)
	if tip == "" {
		r.fail("adopt-no-branch", "ブランチ "+r.name+" を読めません。", exitUnmet)
		return
	}
	// 取り込み先の判定（base-bound）とは別に、名前のほうもチケットに結び付かないことを確かめる。チケットに結び付く
	// ワークツリーは、MR を経ずに取り込み先へ載せる直接作業に変えない。
	if why := integrate.BoundReason(r.in.Conf, r.in.Root, integrate.TreeAt(r.in.Conf, r.in.Root, r.dest, r.name)); why != "" {
		r.fail("adopt-ticket", r.name+" はチケットに結び付くワークツリーです（"+why+"）。チケットの作業は直接作業に引き取らず、マージリクエストで出します。", exitUnmet)
		return
	}
	bases := lines(gitOut(r.in.Cwd, 0, "merge-base", "--all", r.name, r.target))
	switch {
	case len(bases) == 0:
		r.fail("adopt-no-base-commit", r.name+" と "+r.target+" の共通の祖先（merge-base）がありません。起点を決められないので引き取りません。", exitUnmet)
		return
	case len(bases) > 1:
		listed := ""
		for _, b := range bases {
			listed += b[:min(8, len(b))] + " "
		}
		r.fail("adopt-many-bases", r.name+" と "+r.target+" の merge-base が複数あります（"+listed+"）。起点を 1 つに決められないので引き取りません。ワークツリーの中で ccnavi-git.sh merge "+r.target+" を打ってから、やり直してください。", exitUnmet)
		return
	}
	s := bases[0]
	sShort := short(r.in.Cwd, s)
	adoptCode := code("adopt|repo=" + common + "|name=" + r.name + "|target=" + r.target + "|head=" + r.headSha + "|s=" + s + "|tip=" + tip)
	report := func(stale bool) {
		r.o.Out("引き取りの確認が要る: 記録はまだ書いていない。")
		if stale {
			r.o.Out(texts.Say(r.in.Root, "NOTE_WORKTREE_ADOPT_CODE_STALE", nil, nil))
		}
		r.o.Out("状態:")
		r.o.Out("  取り込み先 " + r.target + " (" + r.mainTree + ")")
		r.o.Out("  ワークツリー " + r.dest + "（ブランチ " + r.name + "。先頭 " + short(r.in.Cwd, tip) + "）")
		r.o.Out("  推定した起点 S: " + sShort + "（" + r.name + " と " + r.target + " の merge-base）")
		r.o.Out("取り込むコミットの範囲（" + sShort + ".." + r.name + "）:")
		r.o.Out(prefixed(gitOut(r.in.Cwd, slow, "log", "--oneline", s+".."+tip)))
		r.o.Out("取り込みの差分:")
		r.o.Out(prefixed(gitOut(r.in.Cwd, slow, "diff", "--stat", s, tip)))
		r.o.Out("了承の文面: この範囲全体をエージェントの成果として扱う。範囲に他人のコミットや、取り込み先に入れたくない変更が混ざっていないかを、ユーザに確かめる")
		r.o.Out(texts.Say(r.in.Root, "NEXT_WORKTREE_ADOPT", texts.Values{"name": r.name, "target": r.target, "code": adoptCode}, nil))
		r.o.Out(texts.Say(r.in.Root, "NOTE_WORKTREE_SIDE_BY_SIDE", nil, nil))
	}
	switch {
	case r.dry:
		r.o.Out("dry-run: 何も書かない。引き取りの予定は次のとおり。")
		report(false)
		r.o.Exit(0)
		return
	case r.accept == "" || r.accept != adoptCode:
		report(r.accept != "")
		r.o.Val("reason", "adopt-confirm")
		r.o.Exit(exitConfirm)
		return
	}
	path, written := r.writeRecord(common, "base_branch", r.target, "base_sha", r.headSha, "snap_sha", s, "snap_wip", "no",
		"side_by_side", r.sideWord(), "main_tree", r.mainTree, "common_dir", common, "confirm", "adopt")
	if !written {
		return
	}
	if !ok(r.in.Cwd, "update-ref", "refs/ccnavi/direct/"+r.name+"/s", s) {
		fsio.Remove(path)
		r.fail("ref-failed", "S の ref（refs/ccnavi/direct/"+r.name+"/s）を置けません。", exitUnmet)
		return
	}
	r.o.Out("ワークツリーを引き取った: " + r.dest)
	r.o.Out("ブランチ: " + r.name + "（起点 " + sShort + "）")
	r.o.Out("取り込み先: " + r.target + "（" + r.mainTree + "）")
	r.o.Out("起点: " + sShort + "（" + r.name + " と " + r.target + " の merge-base。範囲全体を成果として扱う）")
	r.sideLine()
	r.next()
	r.o.Exit(0)
}
