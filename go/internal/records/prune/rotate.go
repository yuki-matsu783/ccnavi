package prune

import (
	"errors"
	"io/fs"
	"os"
	"regexp"
	"strconv"
	"strings"
	"time"

	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/records/diaglog"
)

func rotatedName(logPath string, now time.Time, n int) string {
	stem, ext := ospath.Splitext(ospath.Basename(logPath))
	suffix := ""
	if n > 1 {
		suffix = "-" + strconv.Itoa(n)
	}
	return ospath.Join(ospath.Dirname(logPath), stem+"."+now.Local().Format(fsio.NameStampLayout)+suffix+ext)
}

// previewName は dry-run で見せるローテート先。同じ名前があれば -2 から足す。
func previewName(logPath string, now time.Time) string {
	for n := 1; ; n++ {
		if candidate := rotatedName(logPath, now, n); !ospath.DiskExists(candidate) {
			return candidate
		}
	}
}

// reserve はローテート先を空のファイルで押さえて、その名前を返す。名前を決めてから変えるまでの間に、
// 同じ秒に始まった別のセッションが同じ名前を選ぶと、後から変えた側が先の記録を上書きするため、
// 排他で作れた名前だけを自分のものにする。作ったファイルは名前を変えるときに置き換わる。
func reserve(logPath string, now time.Time) (string, error) {
	for n := 1; ; n++ {
		candidate := rotatedName(logPath, now, n)
		f, err := os.OpenFile(candidate, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o600)
		if errors.Is(err, fs.ErrExist) {
			continue
		}
		if err != nil {
			return "", err
		}
		f.Close()
		return candidate, nil
	}
}

// rotatedPattern はローテートした名前（`<stem>.<日時>[-<n>]<ext>`）。stem は正規表現の断片で、ext はそのまま比べる。
func rotatedPattern(stem, ext string) *regexp.Regexp {
	return regexp.MustCompile("^" + stem + `\.\p{Nd}{8}-\p{Nd}{6}(?:-\p{Nd}+)?` + regexp.QuoteMeta(ext) + `$`)
}

// diagRotated はローテートした診断ログ（`<出どころ>.<日時>.log`）。出どころは問わない。
var diagRotated = rotatedPattern(`.*`, diaglog.FileExt)

// rotate は大きさが上限を超えていたら名前を変える。変えた（dry-run なら変える）とき真。
// 名前を変えるだけでコピーしない。同じ時に追記している hook は開いたハンドルのままローテート先へ書き、
// 次の起動からは新しい記録を作って書くので、どちらの行も失われない。Windows で開かれていて変えられなければ、
// 次の開始でまた試す。
func (r *run) rotate(logPath string, limit int64) bool {
	_, n, ok := ospath.Stat(logPath)
	if !ok || n <= limit {
		return false
	}
	if r.dry {
		r.report.rotated = append(r.report.rotated, rotation{r.shown(logPath), r.shown(previewName(logPath, r.now))})
		return true
	}
	// 先にローテート先を押さえ、押さえてから名前を変える。
	target, err := reserve(logPath, r.now)
	if err == nil {
		// 同じ時に始まった別のセッションが先に変えていれば、ここには小さい新しい記録がある。
		if _, n, ok := ospath.Stat(logPath); !ok || n <= limit {
			drop(target)
			return false
		}
		err = os.Rename(logPath, target)
	}
	if err != nil {
		drop(target)
		if !errors.Is(err, fs.ErrNotExist) {
			r.problem("%s をローテートできない: %s", r.shown(logPath), fsio.ErrorText(err))
		}
		return false
	}
	r.report.rotated = append(r.report.rotated, rotation{r.shown(logPath), r.shown(target)})
	return true
}

// drop は押さえたまま使わなかったローテート先を消す。
func drop(reserved string) {
	if reserved != "" {
		_ = os.Remove(reserved)
	}
}

// pruneLogs はローテートした記録のうち、最後に書かれてから保持日数を過ぎたものを消す。
func (r *run) pruneLogs(logPath string, before time.Time) {
	directory := ospath.Dirname(logPath)
	if directory == "" {
		directory = "."
	}
	stem, ext := ospath.Splitext(ospath.Basename(logPath))
	pattern := rotatedPattern(regexp.QuoteMeta(stem), ext)
	names, err := ospath.DiskListdir(directory)
	if err != nil {
		return
	}
	for _, name := range names {
		if !pattern.MatchString(name) {
			continue
		}
		path := ospath.Join(directory, name)
		if t, _, ok := ospath.Stat(path); !ok || !ospath.IsFile(path) || !t.Before(before) {
			continue
		}
		if r.remove(path) {
			r.report.logs = append(r.report.logs, r.shown(path))
		}
	}
}

// pruneDiag は診断ログの後始末。しきい値は記録と同じ。出どころごとの 1 本が上限を超えていればローテートし、
// ローテートした分も含めて保持日数を過ぎた *.log を消す。この回にローテートした 1 本はこの回には消さない
// （dry-run でも同じにして、本番と同じ結果を示す）。置き場（logs か logs/diag）がリンクなら辿らない。
func (r *run) pruneDiag(rotateMB, logDays float64) {
	directory := r.diagDir()
	for _, place := range []string{ospath.Dirname(directory), directory} {
		if ospath.IsLink(place) {
			r.problem("%s はリンクなので辿らない（診断ログを削除しません）", r.shown(place))
			return
		}
	}
	names, err := ospath.DiskListdir(directory)
	if err != nil {
		return
	}
	before := cutoff(r.now, days(logDays))
	for _, name := range names {
		// 拡張子は大文字小文字を区別して比べる（diaglog が書く名前は小文字だけ）。
		if !strings.HasSuffix(name, diaglog.FileExt) {
			continue
		}
		path := ospath.Join(directory, name)
		if ospath.IsLink(path) || !ospath.IsFile(path) {
			continue
		}
		// ローテート済みの名前（日時が付いたもの）は、さらにローテートせず保持日数だけで見る。
		if rotateMB > 0 && !diagRotated.MatchString(name) && r.rotate(path, bytesOf(rotateMB)) {
			continue
		}
		if logDays <= 0 {
			continue
		}
		if t, _, ok := ospath.Stat(path); !ok || !t.Before(before) {
			continue
		}
		if r.remove(path) {
			r.report.logs = append(r.report.logs, r.shown(path))
		}
	}
}
