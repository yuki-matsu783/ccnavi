package audit

import (
	"slices"
	"sort"
	"strings"

	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/pyjson"
)

// ReadRecords は判定の記録 log とそのローテートした写し（同じ置き場の `<名前>.*<拡張子>`。名前は大文字小文字を
// 区別しない）を名前順に読み、オブジェクトの行を返す。読めない行は飛ばす。recorded は 1 本でも開けたか。
func ReadRecords(log string) (records []*pyjson.Object, recorded bool) {
	if log == "" {
		return nil, false
	}
	base, ext := ospath.Splitext(log)
	dir, stem := ospath.Dirname(base), ospath.Basename(base)
	var paths []string
	if names, err := ospath.DiskListdir(dirOrDot(dir)); err == nil {
		head, tail := pathcase.Fold(stem+"."), pathcase.Fold(ext)
		for _, name := range names {
			folded := pathcase.Fold(name)
			if len(folded) >= len(head)+len(tail) && strings.HasPrefix(folded, head) && strings.HasSuffix(folded, tail) {
				paths = append(paths, ospath.Join(dir, name))
			}
		}
	}
	sort.Strings(paths)
	if !slices.Contains(paths, log) {
		paths = append(paths, log)
	}
	for _, path := range paths {
		text, err := fsio.ReadDiskText(path, fsio.Replace)
		if err != nil {
			continue
		}
		recorded = true
		for _, line := range strings.Split(text, "\n") {
			if v, err := pyjson.Loads(line); err == nil {
				if obj, ok := v.(*pyjson.Object); ok {
					records = append(records, obj)
				}
			}
		}
	}
	return records, recorded
}

func dirOrDot(dir string) string {
	if dir == "" {
		return "."
	}
	return dir
}
