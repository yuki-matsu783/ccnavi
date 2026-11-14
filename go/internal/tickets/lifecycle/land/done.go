package land

import (
	"strings"

	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/tickets/lifecycle/worktrees"
)

// abort は `integrate --abort <名前>`。一時ワークツリーと ref と記録を消す。作業ワークツリーとユーザのチェックアウトには触らない。
func (r *run) abort(args []string) {
	if len(args) != 1 {
		r.reject("integrate-args", "integrate --abort は名前を 1 つだけ取ります（"+r.SelfQ+" integrate --abort <名前>）。")
	}
	r.knownVersion(r.probe(args[0]))
	r.begin()
	r.log.Info("受け付けた", "sub", "integrate", "action", "abort", "name", r.name)
	r.commonCheck()
	mark := record(r.mark)
	k := mark["k"]
	if k == "" {
		k = r.verify(r.ref + "/k")
	}
	if k != "" && r.ok("cat-file", "-e", k+"^{commit}") && r.isAncestor(k, "HEAD") {
		r.reject("integrate-abort-landed", "K（"+r.short(k)+"）は取り込み先にもう載っています。やめるなら、ユーザが git revert します。片付けは "+r.SelfQ+" integrate-done "+r.name+" で終わらせます。")
	}
	if !ospath.IsFile(r.mark) && !exists(r.tmp) && r.out("for-each-ref", r.ref+"/t", r.ref+"/c", r.ref+"/k") == "" {
		r.reject("integrate-abort-none", r.name+" の取り込みの記録も一時ワークツリーも ref もありません。戻すものはありません。")
	}
	ktree := ""
	if k != "" {
		ktree = r.verify(k + "^{tree}")
	}
	left := !r.removeTmp(orNone(mark["c_tree"], "none"), orNone(ktree, "none"))
	r.dropRefs("t", "c", "k")
	if !r.dropMark() {
		r.reject("integrate-abort-marker", "取り込みの記録（"+r.mark+"）を削除できませんでした。ユーザに伝えてください。")
	}
	r.say("ok  git integrate  %s の取り込みをやめた（作業ワークツリーとユーザのチェックアウトは変えていない）", r.name)
	if left {
		r.tmpNote()
	}
	r.print(texts.Say(r.Root, "NOTE_GIT_INTEGRATE_ABORTED", texts.Values{"self_q": r.SelfQ, "name": r.name}, nil))
	r.log.Info("取り込みをやめた", "name", r.name)
	r.finish(0)
}

// doneArgs は integrate-done の引数（名前と -m のメッセージ）を読む。
func (r *run) doneArgs(args []string) (name, message string, hasMessage bool) {
	for i := 0; i < len(args); i++ {
		arg := args[i]
		switch {
		case arg == "-m" || arg == "--message":
			if i+1 >= len(args) {
				r.reject("integrate-done-args", arg+" にメッセージがありません（"+r.Self+" integrate-done <名前> -m <メッセージ>）。")
			}
			message, hasMessage = args[i+1], true
			i++
		case strings.HasPrefix(arg, "--message="):
			message, hasMessage = strings.TrimPrefix(arg, "--message="), true
		case strings.HasPrefix(arg, "-"):
			r.reject("integrate-done-args", "integrate-done の "+arg+" は使用できません。使用できるのは -m <メッセージ> だけです（"+r.Self+" integrate-done <名前> [-m <メッセージ>]）。")
		case name != "":
			r.reject("integrate-done-args", "integrate-done は名前を 1 つだけ取ります（"+name+" と "+arg+"）。"+r.Self+" integrate-done <名前> [-m <メッセージ>] の形で実行してください。")
		default:
			name = arg
		}
	}
	r.checkName(name)
	if hasMessage && message == "" {
		r.reject("integrate-done-args", "-m のメッセージが空です（"+r.Self+" integrate-done "+name+" -m <メッセージ>）。")
	}
	return name, message, hasMessage
}

// marker は integrate が書いた記録の中身。
type marker struct {
	head, tip, ctree, k, empty, base string
}

// done は `integrate-done <名前> [-m <メッセージ>]`。K を作って取り込み先へ fast-forward し、片付ける。何度打ち直してもよい。
func (r *run) done(args []string) {
	name, message, hasMessage := r.doneArgs(args)
	switch version := r.probe(name); version {
	case "none":
		if !r.hasBranch() && !exists(ospath.Join(r.Root, ".claude", "worktrees", name)) {
			r.say("ok  git integrate-done  %s は片付け済み（ワークツリーもブランチも取り込みの記録もありません）", name)
			r.finish(0)
		}
		r.reject("integrate-done-no-marker", name+" の取り込みの記録がありません。先に "+r.SelfQ+" integrate "+name+" で取り込み結果を用意し、中身を確かめてから実行してください（記録の無いブランチは、取り込んだことを確かめずには消しません）。")
	case "4":
	default:
		r.knownVersion(version)
	}
	r.begin()
	r.log.Info("受け付けた", "sub", "integrate-done", "name", r.name, "message", yesNo(hasMessage))
	r.commonCheck()
	if !r.gitAtLeast(2, 40) {
		r.reject("integrate-old-git", "git が古く（"+r.out("version")+"）、git 2.40 未満では扱えません。git を更新してから再実行してください。")
	}
	m := r.readMarker()
	cur := r.verify("HEAD")
	if m.k != "" {
		// K を作ったあとの再開。
		if !r.ok("cat-file", "-e", m.k+"^{commit}") || !r.isK(m.k, m) {
			r.reject("integrate-done-bad-k", "記録の K（"+m.k+"）の親か木が、記録の head・c_tree と合いません。自動では取り込みません。ユーザに確かめてください。")
		}
		if r.isAncestor(m.k, "HEAD") {
			r.tidy(m)
		}
		if cur != m.head {
			r.reject("integrate-done-moved", "K を作ったあとに "+m.base+" の HEAD が動きました（記録 "+r.short(m.head)+"、今 "+r.short(cur)+"）。"+r.SelfQ+" integrate --abort "+r.name+" で戻してから、"+r.SelfQ+" integrate "+r.name+" を打ち直してください。")
		}
	} else {
		m.k = r.makeK(m, cur, message, hasMessage)
	}
	r.land(m)
}

func yesNo(b bool) string {
	if b {
		return "yes"
	}
	return "no"
}

// readMarker は取り込み先と記録を読み、ユーザのチェックアウトが取り込み先を開いていることを確かめる。足りなければ止める。
func (r *run) readMarker() marker {
	base := record(r.rec)["base_branch"]
	if base == "" {
		base = record(r.doneFile)["base_branch"]
	}
	if base == "" {
		r.reject("integrate-bad-record", "取り込み先を記録から読めません（"+r.rec+"）。壊れた記録は自動では直しません。ユーザに確かめてください。")
	}
	r.onBase(base)
	rec := record(r.mark)
	m := marker{head: rec["head"], tip: rec["tip"], ctree: rec["c_tree"], k: rec["k"], empty: rec["empty"], base: base}
	if m.head == "" || m.tip == "" || m.ctree == "" {
		r.reject("integrate-bad-marker", "取り込みの記録（"+r.mark+"）の中身が足りません。自動では取り込みません。"+r.SelfQ+" integrate --abort "+r.name+" で戻して、打ち直してください。")
	}
	return m
}

// makeK は、確かめてから一時ワークツリーで K をコミットし（フックを通す）、記録して返す。取り込む差分が無ければ片付けて終える。
func (r *run) makeK(m marker, cur, message string, hasMessage bool) string {
	abortThen := r.SelfQ + " integrate --abort " + r.name + " で戻してから、"
	if cur != m.head {
		r.reject("integrate-done-moved", "確認したあとに "+m.base+" の HEAD が動きました（記録 "+r.short(m.head)+"、今 "+r.short(cur)+"）。"+abortThen+r.SelfQ+" integrate "+r.name+" を打ち直してください。")
	}
	if now := r.verify("refs/heads/" + r.name + "^{commit}"); now != m.tip {
		r.reject("integrate-done-branch-moved", "作業ブランチ "+r.name+" の先頭が確認したとき（"+r.short(m.tip)+"）から動いています（今 "+orNone(shortOr(r, now), "無し")+"）。"+abortThen+r.SelfQ+" integrate "+r.name+" を打ち直してください。")
	}
	if ospath.IsDir(r.wt) {
		if d := r.dirty(r.wt); d != "" {
			r.reject("integrate-done-source-dirty", "ワークツリー .claude/worktrees/"+r.name+" に未コミットの変更があります。コミットしてから "+r.SelfQ+" integrate --abort "+r.name+" と integrate を打ち直してください。変更（先頭 10 件）: "+d)
		}
		if !r.drafts(orNone(r.verify(r.ref+"/s^{commit}"), m.head)) {
			r.reject("integrate-done-draft", r.draftWhy+"。ユーザに cd "+shQuote(r.wt)+" && ccnavi-user-handoff.sh を頼み、取り込んだ変更をコミットしてから、integrate を打ち直してください。")
		}
	}
	if m.empty == "yes" {
		r.tidy(m)
	}
	if !ospath.IsDir(r.tmp) {
		r.reject("integrate-done-no-tmp", "一時ワークツリー（"+r.tmp+"）がありません。"+abortThen+"integrate を打ち直してください。")
	}
	if index, _ := r.git(r.tmp, "write-tree"); index != m.ctree || !r.ok("-C", r.tmp, "diff", "--quiet") {
		r.reject("integrate-done-tmp-differs", "一時ワークツリーの中身が、確認した取り込み結果（C）と違います（検証で追跡ファイルを書き換えていないか）。"+abortThen+"integrate を打ち直してください。")
	}
	if hits := r.guarded(r.names("HEAD^{tree}", m.ctree)); len(hits) > 0 && !r.Terminal {
		r.reject("integrate-done-terminal", "自己防衛の対象（"+strings.Join(hits, " ")+" ）を含む取り込みは、ユーザがターミナルで、エージェントのターンが終わってから打ちます（fast-forward で設定ファイルが書き換わると、ターンの終わりの照合が元に戻してしまうため）。ユーザに "+r.SelfQ+" integrate-done "+r.name+" -m '<メッセージ>' を頼んでください。")
	}
	// K をコミットしたあと、記録する前に中断された打ち直しでは、一時ワークツリーの HEAD が既に K になっている。
	// 親が head、木が c_tree ならそれを採用して記録する（commit し直さない）。
	k, _ := r.git(r.tmp, "rev-parse", "--verify", "HEAD")
	if k == "" || k == m.head || !r.isK(k, m) {
		if !hasMessage {
			r.reject("integrate-done-no-message", "取り込みをコミットするので、メッセージが要ります（"+r.SelfQ+" integrate-done "+r.name+" -m <メッセージ>）。")
		}
		if done := r.gitDone(r.tmp, "", "commit", "-q", "-m", message); !done.OK() {
			r.say("fail  git integrate-done  %s をコミットできなかった（%s）。ユーザのチェックアウトは変えていない", r.name, refusal(said(done.Stderr, done.Out)))
			r.print(texts.Say(r.Root, "NOTE_GIT_DONE_COMMIT_FAILED", texts.Values{"self_q": r.SelfQ, "name": r.name}, nil))
			r.log.Error("コミットできなかった", "name", r.name, "exit", done.Code)
			r.finish(1)
		}
		k, _ = r.git(r.tmp, "rev-parse", "--verify", "HEAD")
		if !r.isK(k, m) {
			// フックが中身を変えた。K は記録せず、参照の無いオブジェクトとして残す。一時ワークツリーを C に戻す。
			r.ok("-C", r.tmp, "reset", "-q", "--hard", m.head)
			r.ok("-C", r.tmp, "read-tree", "--reset", "-u", m.ctree)
			r.say("fail  git integrate-done  %s のコミットのフックが、確認した取り込み結果と違う中身にした。記録せずに止めた（ユーザのチェックアウトは変えていない）", r.name)
			r.print(texts.Say(r.Root, "NOTE_GIT_DONE_HOOK_CHANGED", texts.Values{"self_q": r.SelfQ, "name": r.name}, nil))
			r.log.Error("フックが中身を変えた", "name", r.name)
			r.finish(1)
		}
	}
	r.ok("update-ref", r.ref+"/k", k)
	if !writeRecord(r.mark, "version", "4", "head", m.head, "tip", m.tip, "c_tree", m.ctree, "tmp", r.tmp, "empty", "no", "k", k, "created", now()) {
		r.reject("integrate-marker", "K（"+k+"）の記録（"+r.mark+"）を書けません。ユーザに伝えてください。打ち直せば、一時ワークツリーの K を採用して記録し直します。")
	}
	return k
}

// isK は、k の親が記録の head で、木が記録の c_tree か。
func (r *run) isK(k string, m marker) bool {
	return r.verify(k+"^") == m.head && r.verify(k+"^{tree}") == m.ctree
}

// shortOr は sha が空でなければ短い表記。
func shortOr(r *run, sha string) string {
	if sha == "" {
		return ""
	}
	return r.short(sha)
}

// said は git の標準エラーと標準出力を 1 つにした文（空の側は落とす）。
func said(parts ...string) string {
	var kept []string
	for _, p := range parts {
		if p = strings.TrimRight(p, "\n"); p != "" {
			kept = append(kept, p)
		}
	}
	return strings.Join(kept, "\n")
}

// land は、ユーザのチェックアウトで K を fast-forward で取り込み、片付ける。
func (r *run) land(m marker) {
	if done := r.gitDone(r.Cwd, "", "merge", "--ff-only", "--no-overwrite-ignore", m.k); !done.OK() {
		r.say("fail  git integrate-done  %s を取り込めなかった（%s）。ユーザのチェックアウトは変えていない", r.name, refusal(said(done.Stderr, done.Out)))
		r.print(texts.Say(r.Root, "NOTE_GIT_DONE_FF_FAILED", texts.Values{"self_q": r.SelfQ, "name": r.name, "short": r.short(m.k)}, nil))
		r.log.Error("取り込めなかった", "name", r.name, "exit", done.Code)
		r.finish(1)
	}
	r.did = "取り込みました（K=" + r.short(m.k) + "）。"
	r.say("ok  git integrate-done  %s を %s に取り込みました（K=%s）", r.name, m.base, r.short(m.k))
	r.say("変わったファイル:")
	r.list(r.names(m.k+"^", m.k))
	r.tidy(m)
}

// stop は片付けを途中で止める（済んだこと・残したものを言う）。
func (r *run) stop(reason, id string) {
	r.say("fail  git integrate-done  %s の片付けを途中で止めた", r.name)
	r.say("理由: %s", reason)
	if r.did != "" {
		r.say("済んだこと: %s", r.did)
	}
	var kept []string
	if exists(r.wt) {
		kept = append(kept, "ワークツリー .claude/worktrees/"+r.name)
	}
	if exists(r.tmp) {
		kept = append(kept, "一時ワークツリー")
	}
	if r.hasBranch() {
		kept = append(kept, "ブランチ "+r.name)
	}
	if ospath.IsFile(r.mark) {
		kept = append(kept, "取り込みの記録")
	}
	if len(kept) > 0 {
		r.say("残したもの: %s", strings.Join(kept, "、"))
	}
	r.print(texts.Say(r.Root, "NOTE_GIT_DONE_STOPPED", texts.Values{"self_q": r.SelfQ, "name": r.name}, nil))
	r.log.Info("片付けを止めた", "name", r.name, "reason", id)
	r.finish(1)
}

// tidy は片付ける。順番は固定: ① done の記録 → ② 作業ワークツリー → ③ 一時ワークツリー → ④ ブランチ →
// ⑤ 専用の ref と直接作業の記録 → ⑥ 記録（marker）。
func (r *run) tidy(m marker) {
	ts, ss := r.verify(r.ref+"/t"), r.verify(r.ref+"/s")
	if !writeRecord(r.doneFile, "version", "4", "k", orNone(m.k, "-"), "s", orNone(ss, "-"), "t", orNone(ts, "-"), "c_tree", m.ctree,
		"head", m.head, "base_branch", m.base, "common_dir", r.common, "created", now()) {
		r.log.Info("取り込みのコピーを残せなかった", "name", r.name)
	}
	moved := func(what string) {
		if cur := r.verify("refs/heads/" + r.name); r.hasBranch() && cur != m.tip {
			r.stop("ブランチ "+r.name+" の先頭が取り込んだとき（"+r.short(m.tip)+"）から動いている（今は "+r.short(cur)+"）。後から積んだコミットは取り込まれていないので、"+what, "branch-moved")
		}
	}
	// ブランチの先頭が動いていたら、作業ワークツリー（②）を消す前に止める。消してからでは、積まれたコミットを作業
	// ワークツリーから確かめられなくなる。
	moved("何も削除していません")
	// ② 作業ワークツリー
	if exists(r.wt) {
		if b := r.worktreeBranch(); b != "refs/heads/"+r.name {
			r.stop("ワークツリー .claude/worktrees/"+r.name+" が、いまブランチ "+r.name+" を開いていない（"+orNone(b, "detached HEAD かこのリポジトリの登録に無い")+"）。ここで積んだコミットは、削除すると戻せないので、何も削除していません。ワークツリーの中で "+r.name+" に戻すか、積んだコミットの扱いをユーザに確かめる", "worktree-branch")
		}
		if d := r.dirty(r.wt); d != "" {
			r.stop("ワークツリー .claude/worktrees/"+r.name+" に未コミットの変更があるので、削除していません。変更（先頭 10 件）: "+d, "worktree-dirty")
		}
		if !r.drafts(orNone(ss, m.head)) {
			r.stop(r.draftWhy+"。ワークツリーは削除していません", "worktree-draft")
		}
		if d := worktrees.Drop(r.Root, r.name, r.Cwd); !d.Gone() {
			var b strings.Builder
			worktrees.Report(&b, r.Root, d, "")
			r.stop("ワークツリー .claude/worktrees/"+r.name+" を削除できませんでした。削除の出力: "+strings.TrimRight(b.String(), "\n"), "worktree")
		}
		r.did += "ワークツリー .claude/worktrees/" + r.name + " を削除しました。"
	}
	// ③ 一時ワークツリー
	ktree := ""
	if m.k != "" {
		ktree = r.verify(m.k + "^{tree}")
	}
	if !r.removeTmp(m.ctree, orNone(ktree, "none")) {
		r.stop("一時ワークツリー "+r.tmp+" を片付けられない（"+r.tmpWhy+"）。中身はユーザのものかもしれないので、黙って消さない", "tmp")
	}
	r.did += "一時ワークツリーを片付けました。"
	// ④ ブランチ（記録した先頭のままのときだけ消す）
	if r.hasBranch() {
		moved("ブランチを削除していません")
		if done := r.gitDone(r.Cwd, "", "update-ref", "-d", "refs/heads/"+r.name, m.tip); !done.OK() {
			first, _, _ := strings.Cut(said(done.Stderr, done.Out), "\n")
			r.stop("ブランチ "+r.name+" を削除できませんでした（"+first+"）。ワークツリーの登録が残っているなら "+r.SelfQ+" worktree prune で外してから再実行します", "branch")
		}
		r.did += "ブランチ " + r.name + " を削除しました（先頭は " + r.short(m.tip) + "）。"
	}
	// ⑤ 専用の ref と直接作業の記録
	if !r.dropRefs("t", "c", "k", "s", "pending") {
		r.stop("専用の ref（"+r.ref+"/ の "+r.refsFailed+"）を削除できませんでした。直接作業の記録と取り込みの記録は残しています", "refs")
	}
	removeQuiet(r.rec)
	removeQuiet(ospath.Dirname(r.rec))
	// ⑥ 記録
	if !r.dropMark() {
		r.stop("取り込みの記録（"+r.mark+"）を削除できませんでした。", "marker")
	}
	r.say("ok  git integrate-done  %s を片付けた。%s", r.name, orNone(r.did, "片付けるものは記録だけだった"))
	r.log.Info("取り込みを片付けた", "name", r.name)
	// push はユーザが打つ。
	if m.k != "" && r.originCount(m.base, "refs/remotes/origin/"+m.base+"..HEAD") > 0 {
		r.print(texts.Say(r.Root, "NEXT_GIT_PUSH_USER", texts.Values{"base": m.base, "top": r.top}, nil))
	}
	r.finish(0)
}
