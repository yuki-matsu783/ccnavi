package approval

import (
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/ticket"
)

// TimeSource はどこから引いた承認の時刻か。
type TimeSource string

const (
	fromCommit  TimeSource = "commit"      // doing/ に足したコミットの時刻
	uncommitted TimeSource = "uncommitted" // コミットが無い（手で置いて、まだコミットしていない）
)

// ApprovedTime は承認の時刻。At も Source も空なら分からない。
type ApprovedTime struct {
	At     string
	Source TimeSource
}

// Label は人に見せる 1 語。
func (a ApprovedTime) Label() string {
	if a.Source == uncommitted {
		return "未コミット（手で置いた）"
	}
	if a.At == "" {
		return "不明"
	}
	return a.At
}

// ApprovedTimes は、承認済みチケットごと（Path で引く）の承認の時刻を返す。表示（ボード・--diagnose）だけが読む。判定は git を読まない取り決めなので、hook の判定の経路からは呼ばない。
//
// ツリーごとに 1 回 `git log --no-renames --diff-filter=A` を打ち、doing/<識別子>.md を足したコミットのうち、新しいものの時刻を引く。--no-renames は、GitHub の画面での移動（rename のコミット）も、足したことにするために付ける。コミットが無ければ、doing/ にあるものだけ「未コミット」とする。
func ApprovedTimes(conf *config.Settings, found []*ticket.Ticket) map[string]ApprovedTime {
	out := map[string]ApprovedTime{}
	var order []string
	waiting := map[string][]*ticket.Ticket{}
	for _, t := range found {
		if t.TreeRoot == "" {
			continue
		}
		if _, ok := waiting[t.TreeRoot]; !ok {
			order = append(order, t.TreeRoot)
		}
		waiting[t.TreeRoot] = append(waiting[t.TreeRoot], t)
	}
	for _, treeRoot := range order {
		added, ok := addedTimes(conf, treeRoot)
		for _, t := range waiting[treeRoot] {
			if at, hit := added[t.ID]; hit {
				out[t.Path] = ApprovedTime{at, fromCommit}
			} else if ok && t.State == place.Doing {
				out[t.Path] = ApprovedTime{"", uncommitted}
			}
		}
	}
	return out
}

// addedTimes はツリーの doing/ に足したコミットの時刻（識別子ごとに新しいもの）と、git を読めたか。
func addedTimes(conf *config.Settings, treeRoot string) (map[string]string, bool) {
	doing := ospath.Join(conf.ApprovedDir(treeRoot), string(place.Doing))
	rel, ok := ospath.Relpath(doing, treeRoot)
	rel = ospath.Slashed(rel)
	if !ok || strings.HasPrefix(rel, "..") {
		return map[string]string{}, false
	}
	done := git.Run(treeRoot, []string{
		"log", "--no-renames", "--diff-filter=A", "--relative", "--format=%x00%cI", "--name-only", "--", rel,
	}, git.Options{RawPaths: true})
	if !done.OK() {
		return map[string]string{}, false
	}
	found := map[string]string{}
	when := ""
	for _, line := range ospath.Splitlines(done.Out) {
		if rest, ok := strings.CutPrefix(line, "\x00"); ok {
			when = ospath.Strip(rest)
			continue
		}
		name := ospath.Strip(line)
		ident, ok := strings.CutPrefix(name, rel+"/")
		if !ok || !strings.HasSuffix(ident, config.MdExt) {
			continue
		}
		ident = strings.TrimSuffix(ident, config.MdExt)
		if _, seen := found[ident]; !strings.Contains(ident, "/") && !seen {
			found[ident] = when
		}
	}
	return found, true
}
