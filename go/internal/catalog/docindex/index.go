package docindex

import (
	"os"
	"slices"
	"sort"
	"strings"
	"time"

	"ccnavi/internal/infra/fsio"
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
	// Written・Removed は書いた・消した ccnavi-index.jsonl。
	Written, Removed []string
	// IgnoreAdded は無視の行を足した .gitignore。足していなければ空。
	IgnoreAdded string
	// Foreign は ccnavi が書いた形でないので触らなかった ccnavi-index.jsonl。
	Foreign []string
	// Parsed・Reused は読み直した md と使い回した行の数。
	Parsed, Reused int
	// Problems は書けなかった・読まなかった理由。索引は組めた分だけ Rows にある。
	Problems []string
	// Unignored は md を持つディレクトリの ccnavi-index.jsonl がどれも無視されていない（索引の対象外）。
	Unignored bool
	// Indexable は md を持ち、索引の対象になるツリーだった（打ち切りで Rows が空でも引けばヒットする）。
	Indexable bool
	// TimedOut は期限を過ぎて、読み直しの要る md を読み残した。
	TimedOut bool
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

// left は git に与える時間。期限があれば残りで切る（minGitTimeout より短くしない）。
func left(deadline time.Time) time.Duration {
	if deadline.IsZero() {
		return gitTimeout
	}
	return max(minGitTimeout, min(gitTimeout, time.Until(deadline)))
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
}

// Build は base（git のワークツリーのルート）の md の索引を組み、無視されている索引を差分で書く。
//
// md を持つディレクトリの索引がどれも無視されていなければ、Refresh のときに限り .gitignore に IgnoreLine を足して
// 確かめ直す。それでも無視されなければ、索引の対象外（Unignored）として行を返さない。一部だけ無視されていない
// ディレクトリと、ccnavi のものでない索引のディレクトリは、行を組むが書かない。
//
// 処理は 3 段。まず全部のディレクトリで既存の索引を読み、使い回せる行を分ける。次に、読み直しの要る md を待つ数が
// 少ないディレクトリから読む。最後にディレクトリごとに書く。読めなかった md は既存の行を残し、次の回に読み直す。
// `.git` が無ければ ErrNotRepository を、git に聞けなければ *GitError を返す。
func Build(base string, o Options) (*Built, error) {
	result := &Built{}
	all, err := listed(base, left(o.Deadline))
	if err != nil {
		return nil, err
	}
	byDir := map[string][]string{}
	for _, rel := range all {
		if Excluded(rel, o.Excluded, o.Kept) {
			continue
		}
		dir, _ := splitDir(rel)
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
	indexRel := map[string]string{}
	rels := make([]string, 0, len(dirs))
	for _, d := range dirs {
		indexRel[d] = joinRel(d, IndexName)
		rels = append(rels, indexRel[d])
	}
	sort.Strings(rels)
	writable, err := Ignored(base, rels, left(o.Deadline))
	if err != nil {
		return nil, err
	}
	if len(writable) == 0 && o.Refresh && addIgnore(base) == added {
		result.IgnoreAdded = gitignoreName
		if writable, err = Ignored(base, rels, left(o.Deadline)); err != nil {
			return nil, err
		}
	}
	if len(writable) == 0 {
		result.Unignored = true
		return result, nil
	}
	result.Indexable = true
	var w *writer
	if o.Refresh {
		if tmpDir, ok := tree.GitDir(base); ok && ospath.IsDir(tmpDir) {
			sweep(tmpDir)
			w = &writer{base: base, tmpDir: tmpDir, deadline: o.Deadline}
		} else {
			result.Problems = append(result.Problems, "git のディレクトリが見つからないので索引を書かない")
		}
	}
	baseReal := ospath.Realpath(base)

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
	if o.Parse && !result.TimedOut {
		parsePending(base, states, o.Deadline, result)
	}
	for _, one := range states {
		settle(one, o.Refresh, w, result)
	}
	sort.SliceStable(result.Rows, func(i, j int) bool { return result.Rows[i].ConceptID() < result.Rows[j].ConceptID() })
	return result, nil
}

// splitDir は rel.rpartition("/") の頭と尾。
func splitDir(rel string) (string, string) {
	if i := strings.LastIndex(rel, "/"); i >= 0 {
		return rel[:i], rel[i+1:]
	}
	return "", rel
}

func joinRel(dir, name string) string {
	if dir == "" {
		return name
	}
	return dir + "/" + name
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
		result.Problems = append(result.Problems, directory+"/ の実体がツリーの外にあるので読まない")
		return nil
	}
	indexPath := full(base, indexRel)
	oldRows, oldText, own := readIndex(indexPath)
	if own == foreign {
		result.Foreign = append(result.Foreign, indexRel)
		result.Problems = append(result.Problems, indexRel+" は ccnavi の索引ではないので書き換えない")
		writable = false
	}
	one := &dirState{directory: directory, indexRel: indexRel, indexPath: indexPath, writable: writable,
		owner: own, oldRows: oldRows, oldText: oldText, rows: map[string]Row{}}
	names = slices.Clone(names)
	sort.Strings(names)
	for _, rel := range names {
		conceptID := strings.TrimSuffix(rel, MdExt)
		one.order = append(one.order, conceptID)
		if result.TimedOut || past(o.Deadline) {
			result.TimedOut = true
			one.pending = append(one.pending, pending{rel, conceptID, ""})
			continue
		}
		old, hasOld := oldRows[conceptID]
		if !o.Refresh && hasOld {
			one.rows[conceptID] = old
			result.Reused++
			continue
		}
		info, err := os.Lstat(full(base, rel))
		if err != nil || !info.Mode().IsRegular() {
			continue // 消したがまだステージしていない、またはふつうのファイルでない
		}
		mtime := info.ModTime().Local().Format(mtimeLayout)
		if hasOld && old.Mtime() == mtime && old.directory() == shownDir(directory) {
			one.rows[conceptID] = old
			result.Reused++
			continue
		}
		one.pending = append(one.pending, pending{rel, conceptID, mtime})
	}
	return one
}

// readIndex は既存の ccnavi-index.jsonl の行（concept_id → 行）と、中身と、持ち主を返す。
// 空か、空でない行がすべて ccnavi の形なら ours。リンク・ふつうでないもの・大きすぎるもの・UTF-8 でないもの・
// 形の違う行を持つものは foreign。
func readIndex(path string) (map[string]Row, string, owner) {
	rows := map[string]Row{}
	info, err := os.Lstat(path)
	if os.IsNotExist(err) {
		return rows, "", absent
	}
	if err != nil || !info.Mode().IsRegular() || info.Size() > indexFileLimit {
		return rows, "", foreign
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return rows, "", foreign
	}
	text, err := fsio.DecodeBytes(data, fsio.Strict)
	if err != nil {
		return rows, "", foreign
	}
	// 割るのは "\n" だけ。値の中の U+2028・U+2029・U+0085 は JSON の文字列に生のまま入る。
	for _, line := range strings.Split(text, "\n") {
		line = strings.TrimSuffix(line, "\r")
		if isBlank(line) {
			continue
		}
		v, err := pyjson.Loads(line)
		if err != nil {
			return map[string]Row{}, text, foreign
		}
		row, ok := validRow(v)
		if !ok {
			return map[string]Row{}, text, foreign
		}
		rows[row.ConceptID()] = row
	}
	return rows, text, ours
}

// parsePending は読み直しの要る md を読む。待つ数の少ないディレクトリから。期限を過ぎたらやめる。
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
				continue
			}
			result.Parsed++
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
	if !refresh || !one.writable || w == nil {
		return
	}
	if len(rows) == 0 {
		// md が全部消えたディレクトリ。ccnavi の索引だけを消す。打ち切りで行が空のときは消さない。
		if one.owner == ours && len(waiting) == 0 {
			if err := os.Remove(one.indexPath); err != nil {
				result.Problems = append(result.Problems, one.indexRel+" を保存できませんでした: "+fsio.ErrorText(err))
				return
			}
			result.Removed = append(result.Removed, one.indexRel)
		}
		return
	}
	var text strings.Builder
	for _, r := range rows {
		text.WriteString(r.Line() + "\n")
	}
	if one.owner != absent && text.String() == one.oldText {
		return
	}
	if err := w.write(one.indexPath, one.indexRel, text.String()); err != nil {
		result.Problems = append(result.Problems, one.indexRel+" を保存できませんでした: "+fsio.ErrorText(err))
		return
	}
	result.Written = append(result.Written, one.indexRel)
}
