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

// LogFiles は判定の記録 log とそのローテートした写し（同じ置き場の `<名前>.*<拡張子>`。名前は大文字小文字を区別しない）
// のうち、在るファイルを名前順に並べる。log 自身は最後。
func LogFiles(log string) []string {
	if log == "" {
		return nil
	}
	base, ext := ospath.Splitext(log)
	dir, stem := ospath.Dirname(base), ospath.Basename(base)
	var paths []string
	if names, err := ospath.DiskListdir(dirOrDot(dir)); err == nil {
		head, tail := pathcase.Fold(stem+"."), pathcase.Fold(ext)
		for _, name := range names {
			folded := pathcase.Fold(name)
			path := ospath.Join(dir, name)
			if len(folded) >= len(head)+len(tail) && strings.HasPrefix(folded, head) && strings.HasSuffix(folded, tail) &&
				ospath.IsFile(path) {
				paths = append(paths, path)
			}
		}
	}
	sort.Strings(paths)
	if !slices.Contains(paths, log) && ospath.IsFile(log) {
		paths = append(paths, log)
	}
	return paths
}

// ReadRecords は LogFiles の記録を順に読み、オブジェクトの行を返す。読めない行は飛ばす。recorded は 1 本でも開けたか。
func ReadRecords(log string) (records []*pyjson.Object, recorded bool) {
	for _, path := range LogFiles(log) {
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
