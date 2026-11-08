package notice

import (
	"cmp"
	"fmt"
	"slices"
	"strconv"
	"strings"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/tickets/lifecycle/integrate"
)

// directLimit は直接作業の一覧に載せる本数の上限。超えた分は本数だけ言う。
const directLimit = 10

// unknown は取れなかった項目の言い方。
const unknown = "不明"

type directFound struct {
	mtime time.Time
	here  tree.Tree
	rec   *integrate.Record
	owner string
}

// DirectWorktrees は直接作業のワークツリーの一覧（事実だけ）。無ければ空。チケット作業のワークツリーは Parents が
// 言うので入れない（Stop の促しと同じ integrate.DirectCheck で外す）。並びは直接作業の記録の更新時刻の新しい順。
// レビュー済みかは分からないので続きの一手は言わず、別のセッションの作業かもしれないことを添える。
func DirectWorktrees(conf *config.Settings, root string, deadline time.Time) string {
	var found []directFound
	for _, here := range tree.Worktrees(root, conf.Projects) {
		owner := tree.ProjectRoot(conf.Projects, here.Project)
		if owner == "" {
			owner = root
		}
		rec := integrate.DirectRecord(owner, here.Name)
		if rec == nil {
			continue
		}
		if reason, _ := integrate.DirectCheck(conf, root, here, deadline); reason != "" {
			continue
		}
		mtime, _, ok := ospath.Stat(ospath.Join(rec.Common, tree.DirectDir, here.Name))
		if !ok {
			continue
		}
		found = append(found, directFound{mtime, here, rec, owner})
	}
	if len(found) == 0 {
		return ""
	}
	slices.SortStableFunc(found, func(a, b directFound) int { return b.mtime.Compare(a.mtime) })
	lines := []string{texts.Say(root, "NOTICE_DIRECT_WORKTREES_HEAD", nil, nil)}
	for _, f := range found[:min(len(found), directLimit)] {
		lines = append(lines, directLine(f, deadline))
	}
	if rest := len(found) - directLimit; rest > 0 {
		values := texts.Values{"rest": strconv.Itoa(rest), "total": strconv.Itoa(len(found)), "shown": strconv.Itoa(directLimit)}
		lines = append(lines, "  "+texts.Say(root, "NOTICE_DIRECT_WORKTREES_MORE", values, nil))
	}
	lines = append(lines, texts.Say(root, "NOTICE_DIRECT_WORKTREES_TAIL", nil, nil))
	return strings.Join(lines, "\n")
}

// directLine はワークツリー 1 本の行。取れなかった項目は「不明」と言う。
func directLine(f directFound, deadline time.Time) string {
	dirty, known := git.Dirty(f.here.Root, git.Options{Deadline: deadline})
	var dirtyOf *bool
	dirtyText := unknown
	if known {
		dirtyOf = &dirty
		dirtyText = "なし"
		if dirty {
			dirtyText = "あり"
		}
	}
	aheadText := unknown
	if snap := f.rec.Fields["snap_sha"]; snap != "" {
		if ahead, ok := integrate.AheadSince(f.here.Root, snap, deadline); ok {
			aheadText = strconv.Itoa(ahead) + " コミット"
		}
	}
	base := cmp.Or(f.rec.Fields["base_branch"], unknown)
	record := integrateRecord(f, dirtyOf, deadline)
	return fmt.Sprintf("  - %s（取り込み先 %s）: 未コミット %s、起点から %s、integrate の記録 %s",
		f.here.Name, base, dirtyText, aheadText, record)
}

// integrateRecord は integrate の記録の有無。片付け済みかは integrate.HasRecord と同じ判定。
func integrateRecord(f directFound, dirty *bool, deadline time.Time) string {
	if ospath.DiskLexists(ospath.Join(f.rec.Common, integrate.RecordDir, f.here.Name)) {
		return "あり（取り込みの途中）"
	}
	head := git.Head(f.here.Root, git.Options{Deadline: deadline})
	recorded, known := integrate.HasRecord(f.owner, f.here.Name, head, dirty, deadline)
	switch {
	case !known:
		return unknown
	case recorded:
		return "あり（片付け済み）"
	}
	return "なし"
}
