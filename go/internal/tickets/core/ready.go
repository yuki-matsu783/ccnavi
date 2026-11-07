package core

import (
	"io"
	"slices"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/tickets/approval/wap"
	"ccnavi/internal/tickets/lifecycle/treegate"
	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/proposal/agree"
)

// readyProposal は承認の画面が外す理由（提案を読めない・承認の対象にならない）。
const readyProposal agree.VerifyReason = "proposal"

// readiness は承認を頼める状態か。reason が空なら頼める。reason は readyProposal か、親のワークツリーの状態
// （treegate の Kind）。follow は `次:`（1 行。最初の手）と `案内:`（後の手）の行。
type readiness struct {
	reason agree.VerifyReason
	lines  []string
	follow []string
}

func (r readiness) ok() bool { return r.reason == "" }

func (r readiness) text() string {
	var b strings.Builder
	for _, line := range slices.Concat(r.lines, r.follow) {
		b.WriteString(line + "\n")
	}
	return b.String()
}

// follow は次の手の並びを出力の行にする。`次:` は 1 行（最初の手）、後の手は `案内:`。
func follow(steps []string) []string {
	out := make([]string, len(steps))
	for i, step := range steps {
		label := texts.NoteLabel
		if i == 0 {
			label = texts.NextLabel
		}
		out[i] = label + step
	}
	return out
}

// checkReadiness は、送る提案（ids）で承認を頼める状態かを見る。承認の可否を判定するのはここだけである。approval-request（と `--check`）のゲートと、`--agree --preview --verify` が、ここを通る。承認を押したあとで拒否や止まりに気づく往復を避けるため、承認の画面で外す理由と、承認のあとに子の finish を止める親の状態（treegate の ApprovalRequest）を先に伝える。
//
// 見るのは送る提案だけである。ほかの親子の書きかけでは止めない。承認待ちに無い識別子（承認済みなど）も止めない。gathered が nil なら、承認の画面の集計を取り直す。
func checkReadiness(conf *config.Settings, root string, ids []string, gathered *agree.Gathered) readiness {
	mine := map[string]bool{}
	for _, id := range ids {
		mine[id] = true
	}
	if gathered == nil {
		gathered = agree.Gather(io.Discard, conf, root, nil)
	}
	var lines []string
	for _, text := range gathered.Problems {
		for id := range mine {
			if strings.Contains(text, "/"+id+".md") {
				lines = append(lines, linePrefix+text)
				break
			}
		}
	}
	for _, r := range gathered.Rejected {
		if !mine[r.Ticket.ID] {
			continue
		}
		lines = append(lines, linePrefix+r.Ticket.ID+" は承認の対象にならない")
		for _, p := range r.Problems {
			lines = append(lines, "  "+p.String())
		}
	}
	if len(lines) > 0 {
		return readiness{readyProposal, lines, []string{texts.Say(root, "NEXT_APPROVAL_FIX_PROPOSAL", nil, nil)}}
	}
	// 承認の画面の対象に入らない提案（承認済みと同じ中身の改版など）も、親のワークツリーは同じ。対象の組み（Batch）
	// には頼らず、送る提案の識別子から親を引く。
	projects := map[string]bool{}
	for _, t := range gathered.Proposals {
		if t.Project != "" {
			projects[t.ID] = true
		}
	}
	sorted := slices.Sorted(func(yield func(string) bool) {
		for id := range mine {
			if !yield(id) {
				return
			}
		}
	})
	var parents []string
	for _, id := range sorted {
		if p := wap.FamilyOf(id); !slices.Contains(parents, p) {
			parents = append(parents, p)
		}
	}
	var refusals []treegate.Refusal
	for _, parent := range parents {
		if parent == "" || projects[parent] {
			continue
		}
		if path := parentTree(root, parent); path != "" {
			refusals = append(refusals, treegate.Check(treegate.ApprovalRequest, path, conf, treegate.Options{Root: root})...)
		}
	}
	if len(refusals) == 0 {
		return readiness{}
	}
	var steps, reasons []string
	for _, r := range refusals {
		reasons = append(reasons, linePrefix+r.Reason)
		for _, h := range r.Hints {
			if !slices.Contains(steps, h) {
				steps = append(steps, h)
			}
		}
	}
	return readiness{agree.VerifyReason(refusals[0].Kind), reasons, follow(steps)}
}

// ApprovalRequestGate は approval-request が提案を送る前に、承認の画面と同じ検査を通す。通らなければ理由を標準エラーに
// 出して 1（何も送らない）。
func ApprovalRequestGate(stderr io.Writer, conf *config.Settings, root string, ids []string) int {
	ready := checkReadiness(conf, root, ids, nil)
	io.WriteString(stderr, ready.text())
	if ready.ok() {
		return 0
	}
	return 1
}

// parentTree は親のワークツリーのパス。無い・git のワークツリーでないときは空。
func parentTree(root, id string) string {
	path := tree.WorktreePath(root, id)
	if !ospath.IsDir(path) || !tree.IsWorktreeOf(root, path) {
		return ""
	}
	return path
}

// dirtyParent は承認した親のワークツリーに残る、置き場の外の未コミットの変更。
type dirtyParent struct {
	ticket string
	paths  []string
	text   string
}

// dirtyParents は、承認した親のワークツリーにある、提案とチケットの置き場以外の未コミットの変更を数える。承認の前は、親のワークツリーへの書き込みは判定の対象外なので通る。しかし承認のあとに同じ変更を戻そうとすると断られ、子の finish も止まる。止まる前に知らせるため、承認の時点で同じ条件（treegate）を数える。承認は止めない。ワークツリーが無い、または読めないときは黙って数えない。プロジェクトの子は置き場が別なので数えない。
func dirtyParents(conf *config.Settings, root string, tickets []*ticket.Ticket) []dirtyParent {
	seen := map[string]bool{}
	out := []dirtyParent{}
	for _, t := range tickets {
		if t.Project != "" {
			continue
		}
		id := t.ID
		if t.IsChild() {
			id = t.Parent
		}
		key := pathcase.Fold(id)
		if id == "" || seen[key] {
			continue
		}
		seen[key] = true
		path := parentTree(root, id)
		if path == "" {
			continue
		}
		if paths, ok := treegate.Dirty(path, conf, false); ok && len(paths) > 0 {
			refusal := treegate.DirtyRefusal(treegate.ApprovalRequest, path, conf, paths, root, true)
			out = append(out, dirtyParent{id, paths, refusal.Reason})
		}
	}
	return out
}

// dirtyJSON は承認の JSON の dirty_parent（親の識別子と変更のパス）。
func dirtyJSON(found []dirtyParent) []any {
	out := []any{}
	for _, d := range found {
		out = append(out, pyjson.NewObject("ticket", d.ticket, "paths", d.paths))
	}
	return out
}

func sayDirty(stderr io.Writer, found []dirtyParent) {
	for _, d := range found {
		io.WriteString(stderr, linePrefix+d.text+"\n")
	}
}
