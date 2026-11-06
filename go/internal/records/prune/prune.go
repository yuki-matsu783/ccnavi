package prune

import (
	"errors"
	"fmt"
	"io/fs"
	"os"
	"regexp"
	"sort"
	"strings"
	"time"

	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/pathmatch"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/records/diaglog"
)

// 置き場の名前。records は上の段を読めないので、名前を写して持つ（持ち主は括弧の中）。
const (
	selfguardDir   = "selfguard"      // policy の自己防衛のバックアップ
	selfguardStore = "store"          // 自己防衛の実体（消さない）
	stopOpsResult  = "stopops-result" // stopops の結果
	opsDir         = "ops"            // tickets/model の操作記録
	stateSent      = "sent"           // 操作記録の段階が送り終えた印
)

var (
	stopReviewMaterial = []string{"stop-review", "material"}
	stopReviewEvents   = []string{"stop-review", "events.jsonl"}
)

// stampLayout はローテートした名前の日時。名前の順と時刻の順が合う。
const stampLayout = "20060102-150405"

// pyEnd は Python の re の `$`（最後の改行の手前でも当たる）。
const pyEnd = `\n?$`

// 名前からセッションがそのまま読める記録（括弧の中は書く側）。
var sessionOnly = []*regexp.Regexp{
	regexp.MustCompile(`^nudged-(?P<s>.+)\.json` + pyEnd),     // lifecycle の Stop の促し
	regexp.MustCompile(`^denied-(?P<s>.+)\.json` + pyEnd),     // tally の拒否の数え
	regexp.MustCompile(`^stop-(?P<s>.+)\.json` + pyEnd),       // ctxfile
	regexp.MustCompile(`^(?P<s>.+)\.turn\.json` + pyEnd),      // post のターンの基準
	regexp.MustCompile(`^(?P<s>.+)\.accepted\.json` + pyEnd),  // post
	regexp.MustCompile(`^busy-(?P<s>.+)\.json` + pyEnd),       // busy
	regexp.MustCompile(`^turn-(?P<s>.+)\.json` + pyEnd),       // turn
	regexp.MustCompile(`^userprompt-(?P<s>.+)\.json` + pyEnd), // userprompt
	regexp.MustCompile(`^stopturn-(?P<s>.+)\.json` + pyEnd),   // stopops
	regexp.MustCompile(`^stoprefs-(?P<s>.+)\.json` + pyEnd),   // stopops
	regexp.MustCompile(`^stoptaint-(?P<s>.+)\.json` + pyEnd),  // stopjudge
	regexp.MustCompile(`^stoplast-(?P<s>.+)\.json` + pyEnd),   // stopreview
	regexp.MustCompile(`^direct-(?P<s>.+)\.json` + pyEnd),     // direct_candidates（compact・clear では破棄しない）
	// `<セッション>.reported.json`（post）。Claude Code のセッションは UUID なので、その形に限る。広く読むと
	// 同じ置き場の別のファイル（package.json など）を消す。
	regexp.MustCompile(`^(?P<s>[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})\.reported\.json` + pyEnd),
}

// ownDate はセッションを名前に持たない Stop の AI 判定の記録と、ミラーの指紋（親の識別子ごと）。それ自身の日付で消す。
var ownDate = regexp.MustCompile(`^(?:stopreview-(?:plan|attempt)-.+|stopops-(?:pre|op)-.+|mirror-.+)\.json` + pyEnd)

// sessionAndKey は名前の後ろにセッション以外のキーが `-` で続く記録。セッションにも `-` が入るので、
// 名前だけでは切れ目が決まらない。既に分かっているセッションの表記に当てて決める。
var sessionAndKey = []struct{ prefix, suffix string }{
	{"once-", ".json"},          // ctxfile
	{"failstreak-", ".json"},    // tally の失敗の連続
	{"subagent-", ".bounced"},   // subagent
	{"subagent-", ".unchecked"}, // subagent
	{"subagent-", ".running"},   // subagent
	{"stopwrite-", ".json"},     // stopops
	{"subagent-", ".outcome"},   // subagent_outcome
	{"turngate-", ".json"},      // turngate（1 件 1 ファイル）
}

// diagRotated はローテートした診断ログ（`<出どころ>.<日時>.log`）。
var diagRotated = regexp.MustCompile(`\.\p{Nd}{8}-\p{Nd}{6}(?:-\p{Nd}+)?\.log` + pyEnd)

// Rotation はローテートした 1 本（見せるパス）。
type Rotation struct{ From, To string }

// Report は 1 回の後始末で起きたこと。パスはワークスペースルートからの相対（外なら絶対）で、区切りは "/"。
type Report struct {
	Rotated  []Rotation
	Logs     []string
	State    []string
	Problems []string
	// KeptOps は消さずに残した未完了の操作記録の数（消えると送り終えていない判断を復旧できない）。
	KeptOps int
}

// Empty は何も動かさなかったか。
func (r *Report) Empty() bool { return len(r.Rotated) == 0 && len(r.Logs) == 0 && len(r.State) == 0 }

// Options は Run の動かし方。
type Options struct {
	// DryRun は何も動かさず、動かすはずのものを返す。
	DryRun bool
	// Now は基準の時刻。ゼロ値なら今。
	Now time.Time
}

type run struct {
	report *Report
	root   string
	dry    bool
	now    time.Time
}

// Run はローテートと削除を 1 度ずつ行う。途中の失敗は Problems に積んで先へ進む。
func Run(root, logPath, stateDir, session string, o Options) *Report {
	r := &run{report: &Report{}, root: root, dry: o.DryRun, now: o.Now}
	if r.now.IsZero() {
		r.now = time.Now()
	}
	rotate, logDays, stateDays := r.limit(rotateMB), r.limit(logKeepDays), r.limit(stateKeepDays)
	if logPath != "" {
		if rotate > 0 {
			r.rotate(logPath, bytesOf(rotate))
		}
		if logDays > 0 {
			r.pruneLogs(logPath, cutoff(r.now, days(logDays)))
		}
	}
	r.pruneDiag(rotate, logDays)
	if stateDir != "" && stateDays > 0 {
		before := cutoff(r.now, days(stateDays))
		r.pruneState(stateDir, session, before)
		r.pruneDirectory(ospath.Join(stateDir, stopOpsResult), before)
	}
	keep := r.limit(stopReviewKeepDays)
	if logPath != "" {
		// 試行のイベントも決定の記録と同じ形でローテートする。保持が 0 のときは消さないだけ。
		logDir := ospath.Dirname(logPath)
		events := ospath.Join(logDir, stopReviewEvents...)
		if rotate > 0 && r.linkless(ospath.Dirname(events)) {
			r.rotate(events, bytesOf(rotate))
		}
		if keep > 0 {
			before := cutoff(r.now, days(keep))
			r.pruneDirectory(ospath.Join(logDir, stopReviewMaterial...), before)
			if r.linkless(ospath.Dirname(events)) {
				r.pruneLogs(events, before)
			}
		}
	}
	r.report.KeptOps = countOps(stateDir)
	return r.report
}

func (r *run) limit(l limit) float64 {
	value, problem := l.read()
	if problem != "" {
		r.report.Problems = append(r.report.Problems, problem)
	}
	return value
}

func (r *run) problem(format string, args ...any) {
	r.report.Problems = append(r.report.Problems, fmt.Sprintf(format, args...))
}

// Summary は記録（audit の detail）に残す 1 行。何も動かなければ空。
func Summary(r *Report) string {
	if r.Empty() {
		return ""
	}
	return fmt.Sprintf("pruned rotated=%d logs=%d state=%d", len(r.Rotated), len(r.Logs), len(r.State))
}

// Lines はユーザに見せる行。
func Lines(r *Report, dryRun bool) []string {
	rotate, remove := "ローテートした", "削除した"
	if dryRun {
		rotate, remove = "ローテートする", "削除する"
	}
	var out []string
	for _, x := range r.Rotated {
		out = append(out, rotate+"  "+x.From+" → "+x.To)
	}
	for _, path := range append(append([]string(nil), r.Logs...), r.State...) {
		out = append(out, remove+"  "+path)
	}
	if r.KeptOps > 0 {
		out = append(out, fmt.Sprintf("残す  未完了の操作記録 %d 件（%s/。消さない）", r.KeptOps, opsDir))
	}
	if len(out) == 0 {
		out = append(out, "動かすものはありません。")
	}
	return out
}

// linkless は root の下の path までの道にリンクが 1 つも無いか。path が root の外なら偽（書かれたパスのまま比べる）。
func (r *run) linkless(path string) bool {
	top := pathcase.Fold(ospath.Abspath(r.root))
	here := pathcase.Fold(ospath.Abspath(path))
	if here != top && !strings.HasPrefix(here, strings.TrimRight(top, ospath.Sep)+ospath.Sep) {
		return false
	}
	return !pathmatch.HasLink(r.root, path)
}

// shown は見せるパス。ワークスペースの中なら相対、外なら絶対。区切りは "/"。
func (r *run) shown(path string) string {
	full := ospath.Abspath(path)
	rel, ok := ospath.Relpath(full, ospath.Abspath(r.root))
	if !ok || rel == ".." || strings.HasPrefix(rel, ".."+ospath.Sep) {
		return ospath.Slashed(full)
	}
	return ospath.Slashed(rel)
}

func isLink(path string) bool {
	info, err := os.Lstat(path)
	return err == nil && info.Mode()&os.ModeSymlink != 0
}

func sortedNames(directory string) ([]string, bool) {
	entries, err := os.ReadDir(directory)
	if err != nil {
		return nil, false
	}
	names := make([]string, len(entries))
	for i, e := range entries {
		names[i] = e.Name()
	}
	sort.Strings(names)
	return names, true
}

func mtime(path string) (time.Time, bool) {
	info, err := os.Stat(path)
	if err != nil {
		return time.Time{}, false
	}
	return info.ModTime(), true
}

// touched は最後に書かれた時刻。ディレクトリなら直下の中身も見る（名前が増えないと親の時刻は変わらない）。
func touched(path string) time.Time {
	var newest time.Time
	entries := []string{path}
	if ospath.IsDir(path) {
		if names, err := os.ReadDir(path); err == nil {
			for _, n := range names {
				entries = append(entries, ospath.Join(path, n.Name()))
			}
		}
	}
	for _, e := range entries {
		if t, ok := mtime(e); ok && t.After(newest) {
			newest = t
		}
	}
	return newest
}

// remove は 1 つを消す。消した（dry-run なら消すはずの）とき真。
func (r *run) remove(path string) bool {
	if r.dry {
		return true
	}
	info, err := os.Lstat(path)
	if errors.Is(err, fs.ErrNotExist) {
		// 同じ時に始まった別のセッションが先に消した。
		return false
	}
	if err == nil && info.IsDir() {
		err = os.RemoveAll(path)
	} else {
		err = os.Remove(path)
	}
	if errors.Is(err, fs.ErrNotExist) {
		return false
	}
	if err != nil {
		r.problem("%s を削除できない: %s", r.shown(path), fsio.ErrorText(err))
		return false
	}
	return true
}

// pruneDirectory は 1 つのディレクトリの直下のファイルを、それ自身の日付で消す。道にリンクがあれば何もしない。
func (r *run) pruneDirectory(directory string, before time.Time) {
	if !r.linkless(directory) || !ospath.IsDir(directory) {
		return
	}
	names, ok := sortedNames(directory)
	if !ok {
		return
	}
	for _, name := range names {
		path := ospath.Join(directory, name)
		if isLink(path) || !ospath.IsFile(path) || !touched(path).Before(before) {
			continue
		}
		if r.remove(path) {
			r.report.State = append(r.report.State, r.shown(path))
		}
	}
}

// countOps は未完了の操作記録の数。数えるだけで触らない。読めない記録も数える（壊れた印として残す）。
func countOps(stateDir string) int {
	if stateDir == "" {
		return 0
	}
	entries, err := os.ReadDir(ospath.Join(stateDir, opsDir))
	if err != nil {
		return 0
	}
	n := 0
	for _, e := range entries {
		if !strings.HasSuffix(e.Name(), ".json") {
			continue
		}
		data, ok := fsio.ReadObject(ospath.Join(stateDir, opsDir, e.Name()))
		if !ok || !allSent(data) {
			n++
		}
	}
	return n
}

// allSent は段階がすべて送り終えたか（終わった記録はその操作が自分で消す）。
func allSent(data *pyjson.Object) bool {
	stages, ok := data.Value("stages").([]any)
	if !ok {
		return false
	}
	for _, s := range stages {
		stage, ok := s.(*pyjson.Object)
		if !ok || stage.Value("state") != stateSent {
			return false
		}
	}
	return true
}

// diag は診断ログの置き場。
func (r *run) diagDir() string { return ospath.Join(r.root, diaglog.Dir) }
