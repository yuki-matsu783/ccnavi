package prune

import (
	"errors"
	"fmt"
	"io/fs"
	"os"
	"regexp"
	"strings"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/pathmatch"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/records/diaglog"
	"ccnavi/internal/records/stopreview"
	"ccnavi/internal/records/tally"
	"ccnavi/internal/records/turn"
)

// records より上の層（policy・tickets・hook）が書く置き場の名前は、records から import できない（層が逆向きになる）。
// そのため名前をここに写す（持ち主は括弧の中）。records の中の名前は、持ち主のパッケージの定数を読む。
// policy が書く名前（自己防衛のバックアップ・ctxfile の数え）は config の定数を読む。
const (
	stateSent = "sent" // tickets/model/oplog の段階が送り終えた印（oplog.StateSent）
)

const anyName = `(?P<s>.+)`

// named は `^<prefix><セッション><suffix>$` の形。
func named(prefix, suffix string) *regexp.Regexp {
	return regexp.MustCompile(`^` + regexp.QuoteMeta(prefix) + anyName + regexp.QuoteMeta(suffix) + `$`)
}

// 名前からセッションがそのまま読める記録（括弧の中は書く側）。
var sessionOnly = []*regexp.Regexp{
	named("nudged-", fsio.JSONExt),                  // lifecycle の Stop の促し
	named(tally.DenyPrefix, fsio.JSONExt),           // tally の拒否の数え
	named(config.StopPrefix, fsio.JSONExt),          // ctxfile
	named("", ".turn"+fsio.JSONExt),                 // post のターンの基準
	named("", ".accepted"+fsio.JSONExt),             // post
	named(turn.BusyPrefix, fsio.JSONExt),            // turn
	named(turn.TurnPrefix, fsio.JSONExt),            // turn
	named(turn.UserPromptPrefix, fsio.JSONExt),      // turn
	named(stopreview.TurnStartPrefix, fsio.JSONExt), // stopreview
	named(stopreview.RefsPrefix, fsio.JSONExt),      // stopreview
	named(stopreview.TaintPrefix, fsio.JSONExt),     // hook の stopjudge
	named(stopreview.LastStopPrefix, fsio.JSONExt),  // stopreview
	named(config.DirectPrefix, fsio.JSONExt),        // direct_candidates（compact・clear では破棄しない）
	// `<セッション>.reported.json`（post）。Claude Code のセッションは UUID なので、その形に限る。広く読むと
	// 同じ置き場の別のファイル（package.json など）を消す。
	regexp.MustCompile(`^(?P<s>[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})\.reported\.json$`),
}

// ownDate はセッションを名前に持たない Stop の AI 判定の記録と、ミラーの指紋（親の識別子ごと）。それ自身の日付で消す。
var ownDate = regexp.MustCompile(`^(?:` + strings.Join([]string{
	regexp.QuoteMeta(stopreview.PlanPrefix) + `.+`,
	regexp.QuoteMeta(stopreview.AttemptPrefix) + `.+`,
	regexp.QuoteMeta(stopreview.PrePrefix) + `.+`,
	regexp.QuoteMeta(stopreview.OpPrefix) + `.+`,
	`mirror-.+`, // promptsync
}, "|") + `)` + regexp.QuoteMeta(fsio.JSONExt) + `$`)

// sessionAndKey は名前の後ろにセッション以外のキーが `-` で続く記録。セッションにも `-` が入るので、
// 名前だけでは切れ目が決まらない。既に分かっているセッションの表記に当てて決める。
var sessionAndKey = []struct{ prefix, suffix string }{
	{config.OncePrefix, fsio.JSONExt},      // ctxfile
	{tally.StreakPrefix, fsio.JSONExt},     // tally の失敗の連続
	{"subagent-", ".bounced"},              // subagent
	{"subagent-", ".unchecked"},            // subagent
	{"subagent-", ".running"},              // subagent
	{stopreview.WritePrefix, fsio.JSONExt}, // stopreview
	{"subagent-", ".outcome"},              // subagent_outcome
	{"turngate-", fsio.JSONExt},            // turngate（1 件 1 ファイル）
}

// rotation はローテートした 1 本（見せるパス）。
type rotation struct{ from, to string }

// Report は 1 回の後始末で起きたこと。パスはワークスペースルートからの相対（外なら絶対）で、区切りは "/"。
type Report struct {
	rotated  []rotation
	logs     []string
	state    []string
	Problems []string
	// KeptOps は消さずに残した未完了の操作記録の数（消えると送り終えていない判断を復旧できない）。
	KeptOps int
}

// empty は何も動かさなかったか。
func (r *Report) empty() bool { return len(r.rotated) == 0 && len(r.logs) == 0 && len(r.state) == 0 }

// Options は Run の動かし方。
type Options struct {
	// DryRun は何も動かさず、動かすはずのものを返す。
	DryRun bool
	// now は基準の時刻。ゼロ値なら今。
	now time.Time
}

type run struct {
	report *Report
	root   string
	dry    bool
	now    time.Time
}

// Run はローテートと削除を 1 度ずつ行う。途中の失敗は Problems に積んで先へ進む。
//
// 処理フロー:
//  1. しきい値を読む（読めない値は既定で動き、Problems に出す）。
//  2. 判定の記録と診断ログをローテートし、保持日数を過ぎたものを消す。
//  3. state をセッション単位で、結果ファイルを日付単位で消す。
//  4. Stop の AI 判定の記録（イベント・材料）を、別の保持日数で消す。
//  5. 消さずに残した未完了の操作記録を数える。
func Run(root, logPath, stateDir, session string, o Options) *Report {
	r := &run{report: &Report{}, root: root, dry: o.DryRun, now: o.now}
	if r.now.IsZero() {
		r.now = time.Now()
	}
	// 0 はその段階を止める。読めない値は limit が既定に直して Problems に積む。
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
		r.pruneDirectory(ospath.Join(stateDir, stopreview.ResultDir), before)
	}
	keep := r.limit(stopReviewKeepDays)
	if logPath != "" {
		// 試行のイベントも決定の記録と同じ形でローテートする。保持が 0 のときは消さないだけ。
		// 置き場の道にリンクがあれば、リンクの先を触らないよう何もしない（linkless）。
		logDir := ospath.Dirname(logPath)
		events := slashedJoin(logDir, stopreview.EventsFile)
		if rotate > 0 && r.linkless(ospath.Dirname(events)) {
			r.rotate(events, bytesOf(rotate))
		}
		if keep > 0 {
			before := cutoff(r.now, days(keep))
			r.pruneDirectory(slashedJoin(logDir, stopreview.MaterialDir), before)
			if r.linkless(ospath.Dirname(events)) {
				r.pruneLogs(events, before)
			}
		}
	}
	r.report.KeptOps = countOps(stateDir)
	return r.report
}

// slashedJoin は "a/b" の形の名前を、機械の区切りで dir に足す。
func slashedJoin(dir, name string) string {
	return ospath.Join(dir, strings.Split(name, "/")...)
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
	if r.empty() {
		return ""
	}
	return fmt.Sprintf("pruned rotated=%d logs=%d state=%d", len(r.rotated), len(r.logs), len(r.state))
}

// Lines はユーザに見せる行。
func Lines(r *Report, dryRun bool) []string {
	rotate, remove := "ローテートした", "削除した"
	if dryRun {
		rotate, remove = "ローテートする", "削除する"
	}
	var out []string
	for _, x := range r.rotated {
		out = append(out, rotate+"  "+x.from+" → "+x.to)
	}
	for _, path := range append(append([]string(nil), r.logs...), r.state...) {
		out = append(out, remove+"  "+path)
	}
	if r.KeptOps > 0 {
		out = append(out, fmt.Sprintf("残す  未完了の操作記録 %d 件（%s/。消さない）", r.KeptOps, config.OpsDir))
	}
	if len(out) == 0 {
		out = append(out, "動かすものはありません。")
	}
	return out
}

// linkless は root の下の path までの道にリンクが 1 つも無いか。path が root の外なら偽（書かれたパスのまま比べる）。
// リンクを辿って消すと、ccnavi の置き場の外のファイルを消すことになるため、消す前にこれで確かめる。
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

// touched は最後に書かれた時刻。ディレクトリなら直下の中身も見る（名前が増えないと親の時刻は変わらない）。
func touched(path string) time.Time {
	var newest time.Time
	entries := []string{path}
	if ospath.IsDir(path) {
		if names, err := ospath.DiskListdir(path); err == nil {
			for _, n := range names {
				entries = append(entries, ospath.Join(path, n))
			}
		}
	}
	for _, e := range entries {
		if t, _, ok := ospath.Stat(e); ok && t.After(newest) {
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
		// 同じ時に始まった別のセッションが先に消した（失敗として報告しない）。
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
	names, err := ospath.DiskListdir(directory)
	if err != nil {
		return
	}
	for _, name := range names {
		path := ospath.Join(directory, name)
		if ospath.IsLink(path) || !ospath.IsFile(path) || !touched(path).Before(before) {
			continue
		}
		if r.remove(path) {
			r.report.state = append(r.report.state, r.shown(path))
		}
	}
}

// countOps は未完了の操作記録の数。数えるだけで触らない。読めない記録も数える（壊れた印として残す）。
func countOps(stateDir string) int {
	if stateDir == "" {
		return 0
	}
	names, err := ospath.DiskListdir(ospath.Join(stateDir, config.OpsDir))
	if err != nil {
		return 0
	}
	n := 0
	for _, name := range names {
		// 拡張子は大文字小文字を区別して比べる（ccnavi が書く名前は小文字だけ）。
		if !strings.HasSuffix(name, fsio.JSONExt) {
			continue
		}
		data, ok := fsio.ReadObject(ospath.Join(stateDir, config.OpsDir, name))
		if !ok || !allSent(data) {
			n++
		}
	}
	return n
}

// allSent は段階がすべて送り終えたか（終わった記録はその操作が自分で消す）。段階が無い・形が違う記録は未完了として数える。
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

// diagDir は診断ログの置き場。
func (r *run) diagDir() string { return diaglog.DirOf(r.root) }
