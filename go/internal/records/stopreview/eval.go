package stopreview

import (
	"fmt"
	"slices"
	"strings"
	"time"

	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
)

// 評価の集計。採用を判断する材料を、基準ごとの単位（R1・R3 は Stop の機会、R2・R4 は提案の版）で偏りなく出す。
// 独立した評価（labels.jsonl。1 行 1 件 `{"attempt_id", "criterion", "target", "truth": "flaw"|"ok"}`）は人か
// シナリオの期待結果が書く。見逃し率・誤差し戻し率・不要介入率はこれが無いと出せない。

// rate は割合。分母が 0 なら出せない。
type rate struct {
	value float64
	known bool
}

func ratio(numerator, denominator int) rate {
	if denominator == 0 {
		return rate{}
	}
	return rate{float64(numerator) / float64(denominator), true}
}

func (r rate) String() string {
	if !r.known {
		return "不明"
	}
	return fmt.Sprintf("%.1f%%", r.value*100)
}

// counts は試行の分類ごとの件数。分類は最初に当たった 1 つを数える（期限切れは未検査にも数える）。
// 拒否と汚染は分類とは別に数える。
type counts struct {
	ccnaviOutOfScope, aiOutOfScope, skipped, expired, rejected, planMissing, tainted int
	checkedOK, flawed, unchecked, inProgress                                         int
}

type fix struct{ sentBack, resolved, unresolved, inconclusive, notRejudged int }

type latency struct {
	n           int
	median, max float64
}

type criterionSummary struct {
	name, unit                         string
	flagged, labeled                   int
	missRate, falseBlockRate, needless rate
}

type summary struct {
	attempts   int
	counts     counts
	suspected  int
	fixRate    rate
	fix        fix
	latency    *latency
	noKey      int
	timeouts   int
	coverage   rate
	byCriteria []criterionSummary
}

// Report は評価の集計を人が読む形で返す。読むだけではなく、集計の前に ExpireOverdue で期限切れの試行を
// 確定して書く。
func Report(state, logDir string) string {
	return render(summarize(state, logDir), unlinkedTotal(state))
}

func jsonl(path string) []*pyjson.Object {
	if !ospath.IsFile(path) {
		return nil
	}
	text, _ := fsio.ReadText(path, fsio.Replace)
	var rows []*pyjson.Object
	for _, line := range ospath.Splitlines(text) {
		if v, err := pyjson.Loads(line); err == nil {
			if row, ok := v.(*pyjson.Object); ok {
				rows = append(rows, row)
			}
		}
	}
	return rows
}

// allEvents は試行のイベント。ローテートした `events.<日時>.jsonl` も読む。
func allEvents(logDir string) []*pyjson.Object {
	current := logPath(logDir, EventsFile)
	dir := ospath.Dirname(current)
	paths := []string{current}
	for _, name := range names(dir) {
		if strings.HasPrefix(name, "events.") && strings.HasSuffix(name, ".jsonl") && name != "events.jsonl" {
			paths = append(paths, ospath.Join(dir, name))
		}
	}
	var rows []*pyjson.Object
	for _, p := range paths {
		rows = append(rows, jsonl(p)...)
	}
	return rows
}

// waitOf は計画の作成から判定の完了まで（秒）。完了した試行だけ。
func waitOf(attempts []*pyjson.Object) *latency {
	var waits []float64
	for _, a := range attempts {
		doneAt, ok1 := pyjson.Number(a.Value(keyDoneAt), pyjson.BoolsAsNumbers)
		created, ok2 := pyjson.Number(a.Value(keyCreated), pyjson.BoolsAsNumbers)
		if ok1 && ok2 {
			waits = append(waits, doneAt-created)
		}
	}
	if len(waits) == 0 {
		return nil
	}
	slices.Sort(waits)
	return &latency{n: len(waits), median: waits[len(waits)/2], max: waits[len(waits)-1]}
}

func countEvents(events []*pyjson.Object, name event) int {
	n := 0
	for _, e := range events {
		if e.Value("event") == string(name) {
			n++
		}
	}
	return n
}

func summarize(state, logDir string) summary {
	Store{State: state}.ExpireOverdue(time.Now())
	// 再判定の試行は元の試行の修正率にだけ使う。Stop の機会を二重に数えない。
	var attempts []*pyjson.Object
	for _, a := range records(state, AttemptPrefix) {
		if !pyfmt.Truthy(a.Value(keyOriginal)) {
			attempts = append(attempts, a)
		}
	}
	events := allEvents(logDir)
	labels := jsonl(logPath(logDir, labelsFile))

	var c counts
	var f fix
	for _, a := range attempts {
		final, judge, st := a.Value(keyFinal), a.Value(keyJudge), stateOf(a)
		sent := pyfmt.Truthy(a.Value(keySentAt))
		switch {
		case sent:
			c.flawed++ // 差し戻した試行は、期限で閉じても最終分類（不備検出）で数える
		case st == planWait:
			c.planMissing++
		case final == string(FinalOutOfScope):
			if judge == string(judgeAI) {
				c.aiOutOfScope++
			} else {
				c.ccnaviOutOfScope++
			}
		case final == string(FinalSkipped):
			c.skipped++
		case st == expired:
			c.expired++
			c.unchecked++
		case final == string(finalOK):
			c.checkedOK++
		case final == string(finalFlawed):
			c.flawed++
		case final == string(FinalUnchecked):
			c.unchecked++
		default:
			c.inProgress++ // 開始待ち・実行中（まだ最終分類が無い）
		}
		if a.Value(keyAccepted) == false {
			c.rejected++
		}
		if pyfmt.Truthy(a.Value(keyTainted)) {
			c.tainted++
		}
		if sent {
			f.sentBack++
			switch a.Value(keyRejudge) {
			case string(verdictResolved):
				f.resolved++
			case string(verdictUnresolved):
				f.unresolved++
			case string(verdictInconclusive):
				f.inconclusive++
			default:
				f.notRejudged++
			}
		}
	}
	judged := c.checkedOK + c.flawed + c.unchecked
	return summary{
		attempts:   len(attempts),
		counts:     c,
		suspected:  countEvents(events, eventSuspected),
		fixRate:    ratio(f.resolved, f.sentBack),
		fix:        f,
		latency:    waitOf(attempts),
		noKey:      countEvents(events, eventNoKey),
		timeouts:   countEvents(events, eventTimeout),
		coverage:   ratio(c.checkedOK+c.flawed, judged),
		byCriteria: byCriterion(attempts, labels),
	}
}

// detection は単位ごとの検出の結果。結果の行が無い単位（未検査・AI の誤った対象外・期限切れ）は検出できなかったとする。
type detection string

const (
	detectedFlaw detection = "flawed"
	detectedNone detection = "clear"
	notDetected  detection = "missed"
)

// truth は独立した評価の答え。
type truth string

const (
	truthFlaw truth = "flaw"
	truthOK   truth = "ok"
)

type labelKey struct {
	attempt   string
	criterion criterionID
	target    string
}

// units は (試行, 基準, 対象) → 検出の結果。対象は大文字小文字を区別せずに比べる。
func units(attempts []*pyjson.Object) map[labelKey]detection {
	out := map[labelKey]detection{}
	for _, a := range attempts {
		rows, _ := a.Value(keyResults).([]any)
		for _, r := range rows {
			row, ok := r.(*pyjson.Object)
			if !ok {
				continue
			}
			name, ok1 := row.Value("criterion").(string)
			target, ok2 := row.Value("target").(string)
			result, ok3 := row.Value("result").(string)
			if !ok1 || !ok2 || !ok3 {
				continue
			}
			criterion := criterionID(name)
			found := detectedNone
			if a.Value(keyAccepted) != false && slices.Contains(flawCriteria, criterion) && outcome(result) == fail {
				found = detectedFlaw
			}
			out[labelKey{a.String(keyAttemptID), criterion, pathcase.Fold(target)}] = found
		}
	}
	return out
}

type label struct {
	labelKey
	flaw bool
}

// cleanLabels は独立した評価を整える。形の違う行・不正な truth を捨て、同じ単位の重複を除く。
func cleanLabels(rows []*pyjson.Object) []label {
	seen := map[labelKey]bool{}
	var kept []label
	for _, row := range rows {
		attempt, ok1 := row.Value("attempt_id").(string)
		criterion, ok2 := row.Value("criterion").(string)
		target, ok3 := row.Value("target").(string)
		answer, ok4 := row.Value("truth").(string)
		if !ok1 || !ok2 || !ok3 || !ok4 || (truth(answer) != truthFlaw && truth(answer) != truthOK) {
			continue
		}
		key := labelKey{attempt, criterionID(criterion), pathcase.Fold(target)}
		if seen[key] {
			continue
		}
		seen[key] = true
		kept = append(kept, label{key, truth(answer) == truthFlaw})
	}
	return kept
}

func byCriterion(attempts, labelRows []*pyjson.Object) []criterionSummary {
	found := units(attempts)
	labels := cleanLabels(labelRows)
	var out []criterionSummary
	for _, criterion := range criteria {
		flagged := 0
		for key, result := range found {
			if key.criterion == criterion && result == detectedFlaw {
				flagged++
			}
		}
		labeled, flawTotal, missed, okTotal, okFlagged, labeledFlagged := 0, 0, 0, 0, 0, 0
		for _, l := range labels {
			if l.criterion != criterion {
				continue
			}
			labeled++
			result, ok := found[l.labelKey]
			if !ok {
				result = notDetected
			}
			if result == detectedFlaw {
				labeledFlagged++
			}
			if l.flaw {
				flawTotal++
				if result != detectedFlaw {
					missed++
				}
			} else {
				okTotal++
				if result == detectedFlaw {
					okFlagged++
				}
			}
		}
		unit := "proposal_version"
		if slices.Contains(stopUnit, criterion) {
			unit = "stop"
		}
		out = append(out, criterionSummary{
			name: string(criterion), unit: unit, flagged: flagged, labeled: labeled,
			missRate: ratio(missed, flawTotal), falseBlockRate: ratio(okFlagged, labeledFlagged), needless: ratio(okFlagged, okTotal),
		})
	}
	return out
}

func render(s summary, unlinked int) string {
	c := s.counts
	lines := []string{
		fmt.Sprintf("試行 %d 件。検査カバー率 %s。修正率 %s（差し戻し %d 件、解消 %d、未解消 %d、判定不能 %d、再判定なし %d）",
			s.attempts, s.coverage, s.fixRate, s.fix.sentBack, s.fix.resolved, s.fix.unresolved, s.fix.inconclusive, s.fix.notRejudged),
		"基準ごと（Stop の機会: R1・R3 / 提案の版: R2・R4）:",
	}
	for _, row := range s.byCriteria {
		lines = append(lines, fmt.Sprintf("  %s（%s）: 不備検出 %d 件、評価 %d 件、見逃し率 %s、誤差し戻し率 %s、不要介入率 %s",
			row.name, row.unit, row.flagged, row.labeled, row.missRate, row.falseBlockRate, row.needless))
	}
	lines = append(lines, fmt.Sprintf("分けて数えるもの: ccnavi の対象外 %d、AI の対象外 %d、検査省略 %d、計画なし %d、"+
		"期限切れ %d、出力不正 %d、未連携 %d、記録外の停止の疑い %d、汚染 %d",
		c.ccnaviOutOfScope, c.aiOutOfScope, c.skipped, c.planMissing, c.expired, c.rejected, unlinked, s.suspected, c.tainted))
	shown := "不明"
	if w := s.latency; w != nil {
		shown = fmt.Sprintf("中央値 %.1f 秒、最大 %.1f 秒（%d 件）", w.median, w.max, w.n)
	}
	lines = append(lines,
		"待ち時間（計画の作成から判定の完了まで）: "+shown,
		"費用: 不明（hook からは取れない。claude -p の total_cost_usd で測る）",
		fmt.Sprintf("共有キーを導けなかった Stop %d 件、判定役が計画を待ち切れなかった %d 件", s.noKey, s.timeouts),
	)
	return strings.Join(lines, "\n")
}

// unlinkedTotal は未連携の approval-request 呼び出しの数（全セッション）。
func unlinkedTotal(state string) int {
	n := 0
	for _, pre := range records(state, PrePrefix) {
		if pre.Value("bound") == unlinked {
			n++
		}
	}
	return n
}
