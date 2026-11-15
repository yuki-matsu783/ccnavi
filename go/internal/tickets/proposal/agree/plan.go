package agree

import (
	"fmt"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/approval/marks"
	"ccnavi/internal/tickets/config/phases"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/phase"
	"ccnavi/internal/tickets/proposal/candidates"
)

// Applied は承認済みチケットを置いた結果。置いたものは戻さない（戻す途中でまた失敗する）。どこで止まって何が
// 置かれたかをそのまま返し、拡張がユーザに伝える（承認の JSON の partial）。
type Applied struct {
	Code      int
	Placed    []string
	StoppedAt string
	Reason    string
}

// Stop は承認の書き込みを途中で止めた識別子と理由。
type Stop struct {
	Ticket string
	Reason string
}

// Planned は承認の書き込みを並べたもの。Stopped は途中で止まったとき（nil なら止まらなかった）。止まったときも、
// そこまでに並べた分は書く（置いたものは戻さない）。
type Planned struct {
	Stage   *fsio.Stage
	Stopped *Stop
}

// PlanBatch は承認の書き込みを、ディスクに書かずに並べる（書くのは上の層の Writer(FS)。Chrome は同じ結果を
// 1 コミットにする）。時刻は stamp に固定する。承認の対象の順に、改版は承認済みチケットを書き換え、新規は
// 承認済みチケットを置く。照合のあとで提案ファイルが差し替えられていたら、1 件も並べずに止める（一部だけ
// 承認しないため）。親のワークツリーが決まらなければ error（*approval.NoParentError）。
func PlanBatch(conf *config.Settings, root string, batch []*candidates.Candidate, stamp string) (*Planned, error) {
	stage, done, err := fsio.Staging()
	if err != nil {
		return nil, err
	}
	defer done()
	restore, err := fsio.FixClock(stamp)
	if err != nil {
		return nil, err
	}
	defer restore()
	stopped, err := applySteps(stage, conf, root, batch)
	if err != nil {
		return nil, err
	}
	return &Planned{stage, stopped}, nil
}

func applySteps(stage *fsio.Stage, conf *config.Settings, root string, batch []*candidates.Candidate) (*Stop, error) {
	for _, c := range batch {
		if failed := approval.ChangedSinceRead(c.Ticket); failed != "" {
			return &Stop{c.Ticket.ID, failed}, nil
		}
	}
	for _, c := range batch {
		stop, err := applyOne(stage, conf, root, c)
		if stop != nil || err != nil {
			return stop, err
		}
	}
	stage.AddLine(fmt.Sprintf("\n承認した。%s/%s/ へ動かした。", conf.Approved, place.Doing), 0, fsio.StreamOut)
	return nil, nil
}

// applyOne は 1 件の書き込みを並べる。書けなかったときの扱いは fsio の Policy で添える（溜める段階では書き込みが
// 失敗しないので、扱いは Writer(FS) が書くときに当てる）。
func applyOne(stage *fsio.Stage, conf *config.Settings, root string, c *candidates.Candidate) (*Stop, error) {
	t := c.Ticket
	defer fsio.SetPolicy(func(p *fsio.Policy) {
		p.OnFail, p.Ticket, p.Message, p.Prefix, p.Undo, p.Restore, p.Places, p.Group =
			fsio.FailStop, t.ID, "{reason}", "", nil, nil, "", 0
	})()
	where, err := approval.HomeDir(conf, root, t.ID, t.Parent, t.Project)
	if err != nil {
		return nil, err
	}
	if c.IsRevision() {
		restore := fsio.SetPolicy(func(p *fsio.Policy) { p.Places = t.ID })
		failed := reviseCopy(where, c.Current, t)
		restore()
		if failed != "" {
			return &Stop{t.ID, failed}, nil
		}
		const removing = "改版の提案を todo/ から削除できない ({reason})"
		restore = fsio.SetPolicy(func(p *fsio.Policy) { p.OnFail, p.Message = fsio.FailWarn, removing })
		failed = fsio.Unlink(t.Path)
		restore()
		if failed != "" {
			stage.AddLine("ccnavi: "+t.ID+": "+strings.ReplaceAll(removing, "{reason}", failed), 0, fsio.StreamErr)
		}
		what := ""
		switch {
		case c.PlansFeedback():
			what = "フィードバック計画"
		case !approval.SamePlan(t, c.Current, phases.Plan):
			what = "全体計画"
		case !approval.SamePhases(t, c.Current):
			what = "固定する定義（phases:）"
		}
		if !approval.SameScope(t, c.Current) {
			if what != "" {
				what += "・範囲"
			} else {
				what = "範囲"
			}
		}
		stage.AddLine("  "+t.ID+" の"+what+"を改版した", 0, fsio.StreamOut)
		return nil, nil
	}
	if failed := approval.Admit(where, t); failed != "" {
		return &Stop{t.ID, failed}, nil
	}
	// 終わったフェーズに子を足したら、そのフェーズのマーカーは消す（「その時点の子が全部見られた」以上の意味を
	// 持たない）。消すのは置けたあと（先に消すと、書けずに終わったときに子は増えていないのに済んだレビューが
	// 巻き戻る）。Writer(FS) は並べた順に書き、止まったら先を書かないので、この順が保たれる。
	if t.IsChild() {
		parentDir, err := approval.HomeDir(conf, root, t.Parent, "", t.Project)
		if err != nil {
			return nil, err
		}
		parent, number := t.Parent, t.Phase
		marks.Clear(parentDir, parent, number, func(kinds []marks.Kind) []string {
			names := make([]string, len(kinds))
			for i, k := range kinds {
				names[i] = string(k)
			}
			lines := []string{fmt.Sprintf("  %s のフェーズ %d のマーカー（%s）を削除しました。"+
				"全部閉じたらレビューをもう一度頼むことになる", parent, number, strings.Join(names, ", "))}
			return append(lines, phase.ReopenImpact(conf, root, parent, number)...)
		})
	}
	return nil, nil
}
