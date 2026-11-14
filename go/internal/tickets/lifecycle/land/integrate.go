package land

import (
	"os"
	"strings"

	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/tickets/lifecycle/branch"
)

// integrate は `integrate <名前>`。取り込み結果 C を計算して一時ワークツリーに展開し、差分を出して止まる。
func (r *run) integrate(args []string) {
	if len(args) != 1 {
		r.reject("integrate-args", "integrate は名前を 1 つだけ取ります（"+r.SelfQ+" integrate <名前>。やめるなら "+r.SelfQ+" integrate --abort <名前>）。")
	}
	r.knownVersion(r.probe(args[0]))
	r.begin()
	r.log.Info("受け付けた", "sub", "integrate", "name", r.name)
	if k := record(r.mark)["k"]; ospath.IsFile(r.mark) && k != "" {
		r.reject("integrate-has-k", r.name+" は確定の途中です（K="+r.short(k)+"）。"+r.SelfQ+" integrate-done "+r.name+" -m '<メッセージ>' で続けるか、"+r.SelfQ+" integrate --abort "+r.name+" でやめてください。")
	}
	if !ospath.IsFile(r.rec) {
		// チケットに結び付く名前は --adopt が断る。案内が行き止まりに向かわないよう、先に同じ判定で聞く。
		if state, why := r.bound(); state == "yes" {
			r.reject("integrate-no-record", r.name+" の直接作業の記録がありません（ccnavi-direct/"+r.name+"）。"+r.name+" はチケットに結び付く名前です（"+why+"）。チケットの作業は直接作業に引き取らず、マージリクエストで出します（"+r.TicketQ+" status の次の操作に従う。.ccnavi/docs/worktree-merge.md）。")
		}
		r.reject("integrate-no-record", r.name+" の直接作業の記録がありません（ccnavi-direct/"+r.name+"）。手で切ったワークツリーは、ユーザの確認を経て "+r.WorktreeQ+" "+r.name+" --adopt --base <取り込み先> で引き取ります。")
	}
	r.commonCheck()
	if !r.gitAtLeast(2, 40) {
		r.reject("integrate-old-git", "git が古く（"+r.out("version")+"）、merge-tree --merge-base（git 2.40 から）が利用できません。git を 2.40 以上にしてから再実行してください。")
	}
	rec := record(r.rec)
	base, sRec := rec["base_branch"], rec["snap_sha"]
	if base == "" || sRec == "" {
		r.reject("integrate-bad-record", "直接作業の記録（"+r.rec+"）に base_branch か snap_sha がありません。壊れた記録は自動では直しません。ユーザに確かめてください。")
	}
	r.onBase(base)
	s := r.verify(r.ref + "/s^{commit}")
	if s == "" {
		s = r.verify(sRec + "^{commit}")
	}
	if s == "" {
		r.reject("integrate-no-s", "起点 S（"+sRec+"）のコミットが見つかりません。ユーザに確かめてください。")
	}
	r.baseBound(base)
	if !r.hasBranch() {
		r.reject("integrate-no-branch", "ブランチ "+r.name+" がありません。取り込むのは .claude/worktrees/<名前> のワークツリーと同じ名前のブランチです。")
	}
	if !ospath.IsDir(r.wt) {
		r.reject("integrate-no-worktree", "ワークツリー .claude/worktrees/"+r.name+" がありません。取り込むのは .claude/worktrees/<名前> のワークツリーと同じ名前のブランチです。")
	}
	if b := r.worktreeBranch(); b != "refs/heads/"+r.name {
		r.reject("integrate-worktree-branch", ".claude/worktrees/"+r.name+" は、このリポジトリで "+r.name+" をチェックアウトしているワークツリーではありません（"+orNone(b, "このリポジトリの登録に無い")+"）。")
	}
	// 名前のほうもチケットに結び付かないことを確かめる（取り込み先の判定とは別）。
	switch state, why := r.bound(); state {
	case "no":
	case "yes":
		r.reject("integrate-ticket", r.name+" はチケットに結び付くワークツリーです（"+why+"）。チケットの作業は取り込み先へ直接取り込まず、マージリクエストで出します（.ccnavi/docs/worktree-merge.md）。")
	default:
		r.reject("integrate-ticket", r.name+" がチケットに結び付くかを実行ファイルに確かめられないので、取り込みません。")
	}
	if d := r.dirty(r.wt); d != "" {
		r.reject("integrate-source-dirty", "ワークツリー .claude/worktrees/"+r.name+" に未コミットの変更があります。取り込むのはコミットした分だけなので、ワークツリーの中でコミットしてから再実行してください。変更（先頭 10 件）: "+d)
	}
	if !r.drafts(s) {
		r.reject("integrate-draft", r.draftWhy+"。ユーザに cd "+shQuote(r.wt)+" && ccnavi-user-handoff.sh を頼み、取り込んだ変更を作業ワークツリーでコミットしてください。要らない下書きは消してコミットします。")
	}
	tip := r.verify("refs/heads/" + r.name + "^{commit}")
	if all, _ := r.git(r.Cwd, "ls-tree", "-r", tip); hasLinePrefix(all, "160000 ") {
		r.reject("integrate-submodule", r.name+" は submodule（gitlink）を含むので、直接作業の取り込みの対象外です。")
	}
	head := r.verify("HEAD")
	// 前回の一時ワークツリーと ref を片付ける（残っていれば）。
	if exists(r.tmp) && !r.removeTmp(orNone(r.verify(r.ref+"/c"), "none")) {
		r.reject("integrate-tmp-left", "前の一時ワークツリー "+r.tmp+" を片付けられません（"+r.tmpWhy+"）。中身はユーザのものかもしれないので、黙って消しません。ユーザに確かめてください。")
	}
	r.dropRefs("t", "c", "k")
	c := r.compute(tip, s, rec["base_sha"], base)
	r.lay(head, tip, c, base)
}

// hasLinePrefix は、どれかの行が prefix で始まるか。
func hasLinePrefix(text, prefix string) bool {
	for _, line := range strings.Split(text, "\n") {
		if strings.HasPrefix(line, prefix) {
			return true
		}
	}
	return false
}

// shQuote は単引用符で囲む（中の ' は '"'"'）。
func shQuote(s string) string {
	return "'" + strings.ReplaceAll(s, "'", `'"'"'`) + "'"
}

// baseBound は取り込み先がチケットに結び付かないことを確かめる（結び付かないと分かったときだけ通す）。
func (r *run) baseBound(base string) {
	bound, why := branch.BaseBound(r.Conf, r.Root, r.top, base)
	switch bound {
	case "no":
	case "yes":
		r.reject("integrate-ticket", "取り込み先 "+base+" はチケットに結び付いています（"+why+"）。チケットの作業は取り込み先へ直接取り込まず、マージリクエストで出します。")
	default:
		r.reject("integrate-ticket", "取り込み先 "+base+" がチケットに結び付くかを決められないので、取り込みません。")
	}
}

// compute は T と C を計算し、C（取り込み結果の木）を返す。衝突したら衝突したパスを出して止める。
//
// 基準は、ふつう起点 S。作業ブランチが取り込み先を取り込んでいる（先頭と HEAD の共通祖先 M が、記録した確認時点の
// HEAD（base_sha）より新しい）ときは M にする。S のままだと、取り込み先側と同じ箇所を直した解決結果が「両側が変更」と
// 読まれ、衝突を解いても integrate が収束しない。
func (r *run) compute(tip, s, s0, base string) string {
	bs, snap, snap0 := s, "", ""
	if mb := r.out("merge-base", "HEAD", tip); mb != "" && s0 != "" && mb != s0 && r.isAncestor(s0, mb) {
		bs = mb
		if s != s0 {
			snap, snap0 = s, s0
		}
	}
	t := r.makeT(bs, tip, snap, snap0)
	if t == "" {
		r.reject("integrate-t", "作業ブランチの先頭の木（T）を作れませんでした。何も変えていません。")
	}
	tc := r.out("-c", "user.name=ccnavi", "-c", "user.email=ccnavi@localhost", "commit-tree", t, "-p", bs, "-m", "ccnavi: T")
	if tc == "" {
		r.reject("integrate-t", "T のコミットを作れませんでした。何も変えていません。")
	}
	done := r.gitDone(r.Cwd, "", "merge-tree", "--write-tree", "--name-only", "--merge-base="+bs, "HEAD", tc)
	lines := ospath.Splitlines(done.Out)
	if done.Failure == nil && done.Code == 1 {
		r.say("fail  git integrate  %s の取り込みが衝突した（ユーザのチェックアウトは変えていない）", r.name)
		r.say("衝突したパス:")
		var paths []string
		for _, line := range lines[min(1, len(lines)):] {
			if line == "" {
				break
			}
			paths = append(paths, line)
		}
		r.list(paths)
		r.print(texts.Say(r.Root, "NOTE_GIT_INTEGRATE_CONFLICT", texts.Values{"wt": r.wt, "base": base, "name": r.name, "self_q": r.SelfQ}, nil))
		r.log.Error("取り込みが衝突した", "name", r.name)
		r.finish(1)
	}
	if !done.OK() || len(lines) == 0 {
		r.say("fail  git integrate  %s の取り込み結果を計算できなかった（ユーザのチェックアウトは変えていない）", r.name)
		r.finish(1)
	}
	c := lines[0]
	if !r.ok("update-ref", r.ref+"/t", tc) || !r.ok("update-ref", r.ref+"/c", c) {
		r.reject("integrate-ref", "専用の ref（"+r.ref+"/t・c）を置けません。何も変えていません。")
	}
	return c
}

// lay は C を一時ワークツリーに展開し、記録を書き、差分と次の一手を出す。取り込む差分が無ければ記録だけ書いて終える。
func (r *run) lay(head, tip, c, base string) {
	headTree := r.verify("HEAD^{tree}")
	if c == headTree {
		if !writeRecord(r.mark, "version", "4", "head", head, "tip", tip, "c_tree", c, "tmp", "", "empty", "yes", "created", now()) {
			r.reject("integrate-marker", "取り込みの記録（"+r.mark+"）を書けません。ユーザに伝えてください。")
		}
		r.say("ok  git integrate  %s に取り込む差分は無かった（作業ブランチの中身は %s に入っている）", r.name, base)
		r.print(texts.Say(r.Root, "NEXT_GIT_INTEGRATE_EMPTY", texts.Values{"name": r.name}, nil))
		r.log.Info("取り込む差分が無かった", "name", r.name)
		r.finish(0)
	}
	changed := r.names(headTree, c)
	// 無視されたファイルとの重なり（案内。最終的な防御は --no-overwrite-ignore）。
	var overlap []string
	for _, p := range r.names("--diff-filter=A", headTree, c) {
		if exists(ospath.Join(r.top, p)) && r.ok("-C", r.top, "check-ignore", "-q", "--", p) {
			overlap = append(overlap, p)
		}
	}
	hits := r.guarded(changed)
	// 一時ワークツリーに C を展開する。
	if err := os.MkdirAll(ospath.Dirname(r.tmp), 0o755); err != nil {
		r.reject("integrate-tmp", "一時ワークツリーの置き場（"+ospath.Dirname(r.tmp)+"）を作れません。書き込める場所か確かめ、ユーザに伝えてください。")
	}
	if done := r.gitDone(r.Cwd, "", "worktree", "add", "--detach", r.tmp, head); !done.OK() {
		said := ospath.Splitlines(strings.TrimRight(done.Out+done.Stderr, "\n"))
		last := ""
		if len(said) > 0 {
			last = said[len(said)-1]
		}
		r.reject("integrate-tmp", "一時ワークツリー "+r.tmp+" を作れませんでした（"+last+"）。ユーザのチェックアウトは変えていません。原因を確かめ、ユーザに伝えてください。")
	}
	if !r.ok("-C", r.tmp, "read-tree", "--reset", "-u", c) {
		r.ok("worktree", "remove", "--force", r.tmp)
		r.reject("integrate-tmp", "一時ワークツリーに取り込み結果を展開できませんでした。ユーザのチェックアウトは変えていません。ユーザに伝えてください。")
	}
	if !writeRecord(r.mark, "version", "4", "head", head, "tip", tip, "c_tree", c, "tmp", r.tmp, "empty", "no", "created", now()) {
		r.removeTmp(c)
		r.reject("integrate-marker", "取り込みの記録（"+r.mark+"）を書けません。ユーザに伝えてください。")
	}
	r.say("ok  git integrate  %s の取り込み結果を一時ワークツリーに用意した（%d ファイル。ユーザのチェックアウトは変えていない）", r.name, len(changed))
	stat, _ := r.git(r.Cwd, "diff", "--stat", headTree, c)
	for i, line := range ospath.Splitlines(stat) {
		if i >= r.MaxLines {
			break
		}
		r.say("%s", line)
	}
	r.say("変わるファイル:")
	r.list(changed)
	r.print(texts.Say(r.Root, "NOTE_GIT_INTEGRATE_SAVE_EDITS", nil, nil))
	r.print(texts.Say(r.Root, "NOTE_GIT_INTEGRATE_VERIFY", texts.Values{"tmp": r.tmp}, nil))
	if len(overlap) > 0 {
		r.print(texts.Say(r.Root, "NOTE_GIT_INTEGRATE_IGNORED_OVERLAP", texts.Values{"paths": strings.Join(overlap, " ")}, nil))
	}
	if r.draftNotes != "" {
		r.print(texts.Say(r.Root, "NOTE_GIT_INTEGRATE_DRAFTS", texts.Values{"notes": r.draftNotes}, nil))
	}
	if len(hits) > 0 {
		r.print(texts.Say(r.Root, "NOTE_GIT_INTEGRATE_GUARDED", texts.Values{"paths": strings.Join(hits, " ") + " "}, nil))
	}
	if r.originCount(base, "HEAD..refs/remotes/origin/"+base) > 0 {
		r.print(texts.Say(r.Root, "NOTE_GIT_INTEGRATE_ORIGIN_BEHIND", texts.Values{"base": base, "top": r.top}, nil))
	}
	r.print(texts.Say(r.Root, "NEXT_GIT_INTEGRATE_REVIEW", texts.Values{"head": head, "result": c, "name": r.name}, nil))
	r.log.Info("取り込み結果を用意した", "name", r.name, "files", len(changed))
	r.finish(0)
}
