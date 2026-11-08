package oplog

import (
	"cmp"
	"slices"
	"strings"

	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
)

// allIDs は置いてある記録の操作ID（更新の古い順）。
func allIDs(state string) []string {
	names, err := fsio.Listdir(dir(state))
	if err != nil {
		return nil
	}
	type found struct {
		mtime int64
		id    string
	}
	var rows []found
	for _, name := range names {
		id, ok := strings.CutSuffix(name, fsio.JSONExt)
		if !ok || !validID(id) {
			continue
		}
		if mtime, _, ok := ospath.Stat(ospath.Join(dir(state), name)); ok {
			rows = append(rows, found{mtime.UnixNano(), id})
		}
	}
	slices.SortFunc(rows, func(a, b found) int {
		return cmp.Or(cmp.Compare(a.mtime, b.mtime), strings.Compare(a.id, b.id))
	})
	out := make([]string, len(rows))
	for i, r := range rows {
		out[i] = r.id
	}
	return out
}

// removeWhere は読める記録のうち drop が真のものを消し、消した操作ID を返す。読めない記録は消さない。
func removeWhere(state string, drop func(id string, r *Record) bool) []string {
	var gone []string
	for _, id := range allIDs(state) {
		r, err := Load(state, id)
		if err != nil || r == nil || !drop(id, r) {
			continue
		}
		remove(state, id)
		gone = append(gone, id)
	}
	return gone
}

// RetireParent は親の記録のうち、すべての段階が送り終わったものを消し、消した操作ID を返す
// （親を閉じる片付け。ready が呼ぶ）。
func RetireParent(state, parent string) []string {
	if parent == "" {
		// 親が空だと、親の無い記録を全部消してしまう
		return nil
	}
	return removeWhere(state, func(_ string, r *Record) bool {
		return r.Parent == parent && r.finished()
	})
}

// DiscardUntouched は同じ対象（種類・親・フェーズ）の、何も起きていない記録を keep を除いて消し、消した
// 操作ID を返す。判断をやり直したときに、使われない記録が未完了として残り続けないようにする。
func DiscardUntouched(state string, kind Kind, parent string, phase int, keep string) []string {
	return removeWhere(state, func(id string, r *Record) bool {
		return id != keep && r.Kind == kind && r.Parent == parent && r.Phase == phase && r.untouched()
	})
}

// Pending は未完了の記録。読めない記録は、Broken に理由を入れて返す（隠さない）。
func Pending(state string) []*Record {
	var out []*Record
	for _, id := range allIDs(state) {
		r, err := Load(state, id)
		if err != nil {
			out = append(out, &Record{ID: id, Broken: err.Error()})
			continue
		}
		if r != nil && !r.finished() {
			out = append(out, r)
		}
	}
	return out
}

// CompleteIfDone は、すべての段階が Sent なら記録を消して真を返す。記録が無いときも真。
func CompleteIfDone(state, id string) (bool, error) {
	r, err := Load(state, id)
	if err != nil {
		return false, err
	}
	if r == nil {
		return true, nil
	}
	if r.finished() {
		remove(state, id)
		return true, nil
	}
	return false, nil
}
