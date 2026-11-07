// Package closing は、チケットを 1 つに引き、状態を動かしてよいかを検査する。検査は、着手・終了・取り消しの前に行う。
// 引くときは置き場をすべて読み、2 つ以上残っていれば止める。取り込み済みの親子では、親のワークツリーが決まらない
// チケットや、親のワークツリーの外にしか無いチケットも動かさない。親を閉じてよいか（CloseProblems）は、
// `review ready` と Stop の促しも同じ条件で読む。ゲートはここの 1 か所にある。
//
// Python からの対応: tickets/lifecycle/close を移した。find → Find、absent → Absent、
// misplaced_home → MisplacedHome、family_stopped → FamilyStopped、parent_not_started → ParentNotStarted、
// predecessors_unmet → PredecessorsUnmet、parent_still_busy → ParentStillBusy、close_problems → CloseProblems、
// phase_type → PhaseType、deliverables_in_wip → DeliverablesInWip、deliverables_missing → DeliverablesMissing、
// unmerged_fix → UnmergedFix。TIMEOUT_SECONDS は git.DefaultTimeout。
package closing
