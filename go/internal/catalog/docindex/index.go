package docindex

import (
	"errors"
	"io/fs"
	"os"
	"slices"
	"sort"
	"strings"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/tree"
)

// mtimeLayout は行の mtime の書式（ローカル時刻、秒まで）。
const mtimeLayout = "2006-01-02T15:04:05"

// Options は Build の選び方。ゼロ値は読み直さず書きもしない（Refresh・Parse を立てて使う）。
type Options struct {
	// Excluded は載せないディレクトリ（ツリーのルートからの相対）、Kept はその中で載せる例外。
	Excluded, Kept []string
	// Refresh が偽なら mtime を見ずに既存の行を使い、何も書かず、.gitignore にも足さない。
	Refresh bool
	// Parse が偽なら、行の無い md も読まない（期限の切れた SessionStart）。
	Parse bool
	// Deadline を過ぎたら md を読むのをやめる（読めた分までは書く）。ゼロ値は期限無し。
	Deadline time.Time
}

// Built は 1 つのツリーの索引を組んだ結果。パスはツリーのルートからの相対。
type Built struct {
	// Rows は concept_id の順。
	Rows []Row
	// IgnoreAdded は無視の行を足した .gitignore。足していなければ空。
	IgnoreAdded string
	// Foreign は ccnavi が書いた形でないので触らなかった ccnavi-index.jsonl。
	Foreign []string
	// Problems は書けなかった・読まなかった理由。索引は組めた分だけ Rows にある。
	Problems []Problem
	// Unignored は、md があるディレクトリの ccnavi-index.jsonl がどれも git に無視されていない（索引の対象外）。
	Unignored bool
	// Indexable は md を持ち、索引の対象になるツリーだった（打ち切りで Rows が空でも引けばヒットする）。
	Indexable bool
	// TimedOut は期限を過ぎて、読み直しの要る md を読み残した。
	TimedOut bool

	// 以下は呼び手が読まない（テストが確かめる）。written・removed は書いた・消した ccnavi-index.jsonl、
	// parsed・reused は読み直した md と使い回した行の数。
	written, removed []string
	parsed, reused   int
}

// Problem は書けなかった・読まなかった 1 つの理由。Rel はその文が指すパス（ツリーのルートからの相対）で、
// 文はパスに続けて読む（`<Rel> <Text>`）。ツリー全体のことでパスが無ければ Rel は空。呼び手は、パスなら
// 頭に置き場を付け、空ならツリーの名前を添える。
type Problem struct{ Rel, Text string }

// String はツリーのルートから見た文。
func (p Problem) String() string {
	if p.Rel == "" {
		return p.Text
	}
	return p.Rel + " " + p.Text
}

// owner は既存の ccnavi-index.jsonl の持ち主。
type owner int

const (
	absent owner = iota
	ours
	foreign
)

// past は差し替えられる（テストが打ち切りを作る）。
var past = func(deadline time.Time) bool { return !deadline.IsZero() && time.Now().After(deadline) }

// Past は期限を過ぎたか。ゼロ値は期限無し。
func Past(deadline time.Time) bool { return past(deadline) }

// left は git に与える時間。期限があれば残りで切る（git.IndexMinTimeout より短くしない）。
func left(deadline time.Time) time.Duration {
	if deadline.IsZero() {
		return git.IndexTimeout
	}
	return max(git.IndexMinTimeout, min(git.IndexTimeout, time.Until(deadline)))
}

// pending は読み直しを待つ md。mtime が空なのは見る前に打ち切ったもの。
type pending struct{ rel, conceptID, mtime string }

// dirState は 1 つのディレクトリの途中の状態。
type dirState struct {
	directory, indexRel, indexPath string
	writable                       bool
	owner                          owner
	oldRows                        map[string]Row
	oldText                        string
	order                          []string // md の concept_id を名前の順に
	rows                           map[string]Row
	pending                        []pending
	// indexTime は既存の索引の更新時刻（無ければゼロ値）。racy は、行の mtime は同じでも、索引を組み始めた秒より
	// 前とは言えないので読み直した md があった（scan）。
	indexTime time.Time
	racy      bool
}

// Build は base（git のワークツリーのルート）の md の索引を組み、無視されている索引を差分で書く。
//
// md を持つディレクトリの索引がどれも無視されていなければ、Refresh のときに限り .gitignore に IgnoreLine を足して
// 確かめ直す。それでも無視されなければ、索引の対象外（Unignored）として行を返さない。一部だけ無視されていない
// ディレクトリと、ccnavi のものでない索引のディレクトリは、行を組むが書かない。
//
// 処理は 3 段階。まず全部のディレクトリで既存の索引を読み、使い回せる行を分ける。次に、読み直しの要る md を待つ数が
// 少ないディレクトリから読む。最後にディレクトリごとに書く。読めなかった md は既存の行を残し、次の回に読み直す。
// `.git` が無ければ ErrNotRepository を、git に聞けなければ理由のエラーを返す。エラーのときの結果は nil だが、
// .gitignore に足したあとの確かめ直しに失敗したときだけ、IgnoreAdded を載せた結果も返す。
func Build(base string, o Options) (*Built, error) {
	started := time.Now()
	result := &Built{}
	all, err := listed(base, left(o.Deadline))
	if err != nil {
		return nil, err
	}
	// git が挙げた md を、除外の指定を外してディレクトリごとに分ける。
	byDir := map[string][]string{}
	for _, rel := range all {
		if Excluded(rel, o.Excluded, o.Kept) {
			continue
		}
		dir := dirOf(rel)
		byDir[dir] = append(byDir[dir], rel)
	}
	if len(byDir) == 0 {
		return result, nil
	}
	dirs := make([]string, 0, len(byDir))
	for d := range byDir {
		dirs = append(dirs, d)
	}
	sort.Strings(dirs)
	// ディレクトリごとの索引ファイルのパスを決め、どれが git に無視されているか（＝書いてよいか）を 1 回の問い合わせで調べる。
	indexRel := map[string]string{}
	rels := make([]string, 0, len(dirs))
	for _, d := range dirs {
		indexRel[d] = JoinRel(d, IndexName)
		rels = append(rels, indexRel[d])
	}
	sort.Strings(rels)
	writable, err := Ignored(base, rels, left(o.Deadline))
	if err != nil {
		return nil, err
	}
	// 無視する行は Refresh のときだけ足す。読むだけの呼び出しでユーザの .gitignore を書き換えない。足した結果は git に聞き直して確かめる。
	if len(writable) == 0 && o.Refresh && addIgnore(base) == added {
		result.IgnoreAdded = gitignoreName
		// 確かめ直しに失敗しても、足したことは呼び手が知らせられるように結果を返す。
		if writable, err = Ignored(base, rels, left(o.Deadline)); err != nil {
			return result, err
		}
	}
	if len(writable) == 0 {
		result.Unignored = true
		return result, nil
	}
	result.Indexable = true
	var w *writer
	// 書く場合の下ごしらえ。一時ファイルは git のディレクトリに作る（ワークツリーの git status に出ないため）。見つからなければ書かずに続ける。
	if o.Refresh {
		if tmpDir, ok := tree.GitDir(base); ok && ospath.IsDir(tmpDir) {
			sweep(tmpDir)
			w = &writer{base: base, tmpDir: tmpDir, deadline: o.Deadline, started: started}
		} else {
			result.Problems = append(result.Problems, Problem{Text: "git のディレクトリが見つからないので索引を書かない"})
		}
	}
	baseReal := ospath.Realpath(base)

	// 1 段階目: 既存の索引を読んで、使い回す行と読み直す md に分ける。
	var states []*dirState
	for _, d := range dirs {
		if past(o.Deadline) {
			result.TimedOut = true
			break
		}
		if one := scan(base, baseReal, d, byDir[d], indexRel[d], writable[indexRel[d]] && w != nil, o, result); one != nil {
			states = append(states, one)
		}
	}
	// 2 段階目: 読み直しの要る md を読む。3 段階目: ディレクトリごとに行を決めて書く。
	if o.Parse && !result.TimedOut {
		parsePending(base, states, o.Deadline, result)
	}
	for _, one := range states {
		settle(one, o.Refresh, w, result)
	}
	sort.SliceStable(result.Rows, func(i, j int) bool { return result.Rows[i].ConceptID() < result.Rows[j].ConceptID() })
	return result, nil
}

// second は t を秒に切り捨てる（行の mtime と同じ細かさで比べる）。
func second(t time.Time) time.Time { return t.Truncate(time.Second) }

// dirOf は rel の最後の "/" より前（ルートの直下なら空）。
func dirOf(rel string) string {
	if i := strings.LastIndex(rel, "/"); i >= 0 {
		return rel[:i]
	}
	return ""
}

// JoinRel は "/" 区切りの dir の下の rel。dir が空（ルートの直下・接頭辞の無いツリー）なら rel のまま。
func JoinRel(dir, rel string) string {
	if dir == "" {
		return rel
	}
	return dir + "/" + rel
}

func shownDir(dir string) string {
	if dir == "" {
		return rootDirectory
	}
	return dir
}

func full(base, rel string) string {
	if rel == "" {
		return base
	}
	return ospath.Join(base, strings.Split(rel, "/")...)
}

// scan は既存の索引を読み、使い回せる行と読み直しの要る md に分ける。
// 実体がツリーの外にあるディレクトリ（ジャンクションやリンク越し）は読まない。
func scan(base, baseReal, directory string, names []string, indexRel string, writable bool, o Options, result *Built) *dirState {
	if _, ok := tree.PartsUnder(baseReal, ospath.Realpath(full(base, directory))); !ok {
		result.Problems = append(result.Problems, Problem{directory + "/", "の実体がツリーの外にあるので読まない"})
		return nil
	}
	indexPath := full(base, indexRel)
	oldRows, oldText, own, indexTime := readIndex(indexPath)
	if own == foreign {
		result.Foreign = append(result.Foreign, indexRel)
		result.Problems = append(result.Problems, Problem{indexRel, "は ccnavi の索引ではないので書き換えない"})
		writable = false
	}
	one := &dirState{directory: directory, indexRel: indexRel, indexPath: indexPath, writable: writable,
		owner: own, oldRows: oldRows, oldText: oldText, rows: map[string]Row{}, indexTime: indexTime}
	names = slices.Clone(names)
	sort.Strings(names)
	for _, rel := range names {
		conceptID := rel[:len(rel)-len(config.MdExt)] // 索引に載るのは MdExt で終わる文書だけ（綴りは問わない）
		one.order = append(one.order, conceptID)
		if result.TimedOut || past(o.Deadline) {
			result.TimedOut = true
			one.pending = append(one.pending, pending{rel, conceptID, ""})
			continue
		}
		// Refresh でなければ、既存の行をそのまま信じる（mtime の確認も省いて速く返す）。
		old, hasOld := oldRows[conceptID]
		if !o.Refresh && hasOld {
			one.rows[conceptID] = old
			result.reused++
			continue
		}
		info, err := ospath.Lstat(full(base, rel))
		if err != nil || !info.Mode().IsRegular() {
			continue // 消したがまだステージしていない、またはふつうのファイルでない
		}
		mtime := info.ModTime().Local().Format(mtimeLayout)
		if hasOld && old.Mtime() == mtime && old.directory() == shownDir(directory) {
			// mtime が秒までしか無いので、索引を書いた同じ秒の中の更新は見分けられない。索引より前の秒のものだけ使い回し、同じ秒以降のものは読み直す（racy）。
			if second(info.ModTime()).Before(second(one.indexTime)) {
				one.rows[conceptID] = old
				result.reused++
				continue
			}
			one.racy = true
		}
		one.pending = append(one.pending, pending{rel, conceptID, mtime})
	}
	return one
}

// readIndex は既存の ccnavi-index.jsonl の行（concept_id → 行）と、中身と、持ち主と、ours なら更新時刻を返す。
// 空か、空でない行がすべて ccnavi の形なら ours。リンク・ふつうでないもの・大きすぎるもの・UTF-8 でないもの・
// 形の違う行が含まれるものは foreign。
func readIndex(path string) (map[string]Row, string, owner, time.Time) {
	rows := map[string]Row{}
	info, err := ospath.Lstat(path)
	if errors.Is(err, fs.ErrNotExist) {
		return rows, "", absent, time.Time{}
	}
	if err != nil || !info.Mode().IsRegular() || info.Size() > indexFileLimit {
		return rows, "", foreign, time.Time{}
	}
	data, err := fsio.ReadDiskBytes(path)
	if err != nil {
		return rows, "", foreign, time.Time{}
	}
	text, err := fsio.DecodeBytes(data, fsio.Strict)
	if err != nil {
		return rows, "", foreign, time.Time{}
	}
	// 割るのは "\n" だけ。値の中の U+2028・U+2029・U+0085 は JSON の文字列に生のまま入る。
	for _, line := range strings.Split(text, "\n") {
		line = strings.TrimSuffix(line, "\r")
		if isBlank(line) {
			continue
		}
		v, err := pyjson.Loads(line)
		if err != nil {
			return map[string]Row{}, text, foreign, time.Time{}
		}
		row, ok := validRow(v)
		if !ok {
			return map[string]Row{}, text, foreign, time.Time{}
		}
		rows[row.ConceptID()] = row
	}
	return rows, text, ours, info.ModTime()
}

// parsePending は読み直しの要る md を読む。待つ数の少ないディレクトリから。期限を過ぎたらやめる。
// 読めなかった md は既存の行を残す（mtime が違うので次の回に読み直す）。
func parsePending(base string, states []*dirState, deadline time.Time, result *Built) {
	ordered := slices.Clone(states)
	sort.SliceStable(ordered, func(i, j int) bool {
		a, b := ordered[i], ordered[j]
		if len(a.pending) != len(b.pending) {
			return len(a.pending) < len(b.pending)
		}
		return a.directory < b.directory
	})
	for _, one := range ordered {
		for at, p := range one.pending {
			if p.mtime == "" {
				continue
			}
			if past(deadline) {
				result.TimedOut = true
				one.pending = one.pending[at:]
				return
			}
			raw, ok := readHead(full(base, p.rel))
			if !ok {
				if old, had := one.oldRows[p.conceptID]; had {
					one.rows[p.conceptID] = old
				}
				continue
			}
			result.parsed++
			one.rows[p.conceptID] = newRow(p.conceptID, shownDir(one.directory), FrontMatter(raw), p.mtime)
		}
		kept := one.pending[:0]
		for _, p := range one.pending {
			if p.mtime == "" {
				kept = append(kept, p)
			}
		}
		one.pending = kept
	}
}

// settle は 1 つのディレクトリの行を決め、書けるなら書く（打ち切ったディレクトリも読めた分まで）。
func settle(one *dirState, refresh bool, w *writer, result *Built) {
	waiting := map[string]bool{}
	for _, p := range one.pending {
		waiting[p.conceptID] = true
	}
	var rows []Row
	for _, conceptID := range one.order {
		row, ok := one.rows[conceptID]
		if !ok && waiting[conceptID] {
			row, ok = one.oldRows[conceptID]
		}
		if ok {
			rows = append(rows, row)
		}
	}
	result.Rows = append(result.Rows, rows...)
	// 読むだけ・書けない（無視されていない／他人の索引）ときは、行を返すだけで書かない。
	if !refresh || !one.writable || w == nil {
		return
	}
	if len(rows) == 0 {
		// md が全部消えたディレクトリ。ccnavi の索引だけを消す。打ち切りで行が空のときは消さない。
		if one.owner == ours && len(waiting) == 0 {
			if err := os.Remove(one.indexPath); err != nil {
				result.Problems = append(result.Problems, Problem{one.indexRel, "を保存できませんでした: " + fsio.ErrorText(err)})
				return
			}
			result.removed = append(result.removed, one.indexRel)
		}
		return
	}
	var text strings.Builder
	for _, r := range rows {
		text.WriteString(r.Line() + "\n")
	}
	if one.owner != absent && text.String() == one.oldText && !one.racy {
		return
	}
	if err := w.write(one.indexPath, one.indexRel, text.String()); err != nil {
		result.Problems = append(result.Problems, Problem{one.indexRel, "を保存できませんでした: " + fsio.ErrorText(err)})
		return
	}
	// 行の mtime は秒までなので、読んだあと同じ秒の中で書き換えられた md は mtime の文字列が変わらない。そこで
	// 索引の更新時刻を組み始めた時刻に戻し、使い回すのはそれより前の秒に更新された md の行だけにする（scan）。
	// 戻せなくても索引は正しいので、知らせない。
	_ = os.Chtimes(one.indexPath, w.started, w.started)
	result.written = append(result.written, one.indexRel)
}
