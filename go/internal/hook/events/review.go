package events

import (
	"ccnavi/internal/infra/ospath"
	"fmt"
	"io"
	"sort"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/records/stopreview"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/model/ticket"
)

// notCandidateFinal は候補にならなかった理由ごとの、ccnavi が確定する最終分類。ここに無い理由は計画を書かない。
var notCandidateFinal = map[stopreview.Reason]stopreview.Final{
	stopreview.ReasonStopHookActive:     stopreview.FinalSkipped,
	stopreview.ReasonExistingNudge:      stopreview.FinalOutOfScope,
	stopreview.ReasonApprovedInProgress: stopreview.FinalOutOfScope,
	stopreview.ReasonTargetUnresolvable: stopreview.FinalUnchecked,
	stopreview.ReasonCriteriaUnreadable: stopreview.FinalUnchecked,
	stopreview.ReasonDisabled:           stopreview.FinalSkipped,
	stopreview.ReasonNotMainAgent:       stopreview.FinalSkipped,
}

// planStopReview は Stop の AI 判定の計画と試行を、共有キーで 1 件書く（判定役の agent hook が material で取る）。
// agent hook を登録していなければ何もしない。書けなくても Stop は止めない。対象は Stop の機会と、このターンの提案の
// 最終の有効版で、着手済みの親に属さない提案があれば候補にする。
func planStopReview(stderr io.Writer, conf *config.Settings, root string, in hookio.Input, mode config.Mode,
	nudged bool, cont continuation, raw *approval.Raw) {
	if conf.State == "" || in.SessionID == "" || !conf.StopReviewAgent {
		return
	}
	// 処理フロー: 共有キーを導く → 直前の Stop の記録を残す → このターンの提案を候補に集める → 判定の対象か決める → 計画を 1 件書く。
	store := stopreview.Store{State: conf.State, Logs: stopreview.LogsDir(conf.Log)}
	key := stopreview.SharedKey(in.SessionID, in.TranscriptPath)
	if key == "" {
		// `stop-review report` の「共有キーを導けなかった Stop」の件数になる。
		store.LogNoKey(stopreview.SideHook)
		return
	}
	effective := stopreview.Setting(mode, conf.StopReview)
	now := time.Now()
	// Stop が続いた回なのに、前の Stop は促しも差し戻しもしていない。判定役の差し戻しを取りこぼした疑いなので記録して、あとで数えられるようにする。
	if last, ok := store.LastStop(in.SessionID); in.StopHookActive && !cont.ok && ok && !last.Nudged && !last.SentBack {
		store.LogSuspected(in.SessionID, key)
	}
	store.NoteLastStop(in.SessionID, key, nudged, false, now)
	criteria := stopreview.LoadCriteria(root)
	stopreview.Rebind(conf.State, in.SessionID, now)
	since := stopreview.TurnStart(conf.State, in.SessionID)
	ops := stopreview.TurnOps(conf.State, in.SessionID, since)
	bound := lazyBound(conf, root, raw)
	// 着手済み（進行中）のチケットに属する提案は、判定の候補にしない。
	unstarted := func(ident string) bool {
		t, ok := tree.Lookup(bound(), ident)
		return !ok || !t.InProgress()
	}
	var proposals []stopreview.Proposal
	for _, op := range stopreview.FinalVersions(ops) {
		if unstarted(op.Ident) {
			proposals = append(proposals, stopreview.Proposal{Key: stopreview.TargetKey(op), Ident: op.Ident,
				Route: op.Route, Commit: op.Commit, Files: op.Files})
		}
	}
	// 同じ識別子の approval-request の操作があれば（止めた・失敗した理由が分かる）、未送信としては重ねない。
	operated := map[string]bool{}
	for _, op := range ops {
		operated[pyfmt.Casefold(op.Ident)] = true
	}
	writes := stopreview.UnsentWrites(stopreview.TurnWrites(conf.State, in.SessionID, since), ops)
	for _, w := range writes {
		if unstarted(w.Ident) && !operated[pyfmt.Casefold(w.Ident)] {
			proposals = append(proposals, stopreview.Proposal{Key: stopreview.UnsentKey(w.Ident), Ident: w.Ident,
				Route: stopreview.RouteUnsent, Files: []stopreview.File{{Path: w.Path}}})
		}
	}
	ok, why := stopreview.Candidate(stopreview.CandidateInput{
		MainAgent:          in.AgentID == "",
		StopHookActive:     in.StopHookActive,
		AIContinuation:     cont.ok,
		Effective:          effective,
		TicketsEnabled:     conf.TicketsEnabled(),
		Registered:         true,
		ExistingNudge:      nudged,
		ProposalActivity:   len(proposals) > 0,
		ApprovedInProgress: approvedInProgress(conf, root, in.Cwd, raw),
	})
	if ok && criteria == "" {
		ok, why = false, stopreview.ReasonCriteriaUnreadable
	}
	// 対象外の理由のうち、結果を記録に残すもの（known）だけ計画を書く。それ以外の対象外は何も書かずに終わる。
	final, known := notCandidateFinal[why]
	if !ok && !known {
		return
	}
	targets := []string{stopreview.StopTarget}
	var noR2 []string
	for _, p := range proposals {
		targets = append(targets, p.Key)
		if p.Route != stopreview.RouteSent && p.Route != stopreview.RouteUnsent {
			noR2 = append(noR2, p.Key)
		}
	}
	plan := stopreview.Plan{
		IsCandidate:     ok,
		Reason:          why,
		Final:           final,
		Targets:         targets,
		Proposals:       proposals,
		NoR2:            noR2,
		MaterialVersion: stopreview.MaterialVersion(criteria),
		Refs:            stopreview.SessionRefs(conf.State, in.SessionID),
		Tainted:         store.TaintedFlag(in.SessionID),
		Effective:       effective,
		Rejudge:         cont.ok,
		Original:        cont.attemptID,
		ExistingNudge:   nudged,
	}
	if ok {
		plan.Submitted = submitted(conf, in.SessionID, bound())
	}
	store.ExpireOverdue(time.Now())
	if _, err := store.WritePlan(key, plan, time.Now()); err != nil {
		fmt.Fprintf(stderr, "ccnavi: Stop の AI 判定の計画を書けない: %v\n", err)
	}
}

// lazyBound は承認済みチケットを識別子で引く表を、初めて要るときに 1 度だけ作る。
func lazyBound(conf *config.Settings, root string, raw *approval.Raw) func() map[string]*ticket.Ticket {
	var byID map[string]*ticket.Ticket
	read := false
	return func() map[string]*ticket.Ticket {
		if !read {
			copies, _ := approval.Scan(conf, root, false, raw)
			byID, read = approval.ByID(copies), true
		}
		return byID
	}
}

// submitted は、このセッションで approval-request した提案（セッションの最初から）と、今の承認状態。
func submitted(conf *config.Settings, session string, byID map[string]*ticket.Ticket) []stopreview.Submitted {
	seen := map[string]bool{}
	var idents []string
	for _, op := range stopreview.TurnOps(conf.State, session, time.Unix(0, 0)) {
		if !seen[op.Ident] {
			seen[op.Ident] = true
			idents = append(idents, op.Ident)
		}
	}
	sort.Strings(idents)
	rows := make([]stopreview.Submitted, 0, len(idents))
	for _, ident := range idents {
		t, ok := tree.Lookup(byID, ident)
		rows = append(rows, stopreview.Submitted{Ident: ident, Approved: ok, InProgress: ok && t.InProgress()})
	}
	return rows
}

// approvedInProgress は cwd のワークツリーに結び付いた、着手済みの承認済みチケットがあるか。
func approvedInProgress(conf *config.Settings, root, cwd string, raw *approval.Raw) bool {
	if cwd == "" {
		cwd, _ = ospath.Getwd()
	}
	here, ok := tree.TreeOf(root, cwd, conf.Projects)
	if !ok || here.IsMain() {
		return false
	}
	copies, _ := approval.Scan(conf, root, false, raw)
	t, found := tree.Lookup(approval.ByID(copies), here.Name)
	return found && t.InProgress()
}
