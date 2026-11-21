package base

import (
	"os"
	"path/filepath"
	"sync"

	"ccnavi/internal/tickets/model/handoffnames"
)

// 保護対象（`rules.yml`・`.ccnavi/scripts/*`・CCNAVI.md）の「できあがりの姿」を引く道具（tests/draft.py の写し）。
//
// 保護対象はエージェントが直接直せず、`wip/handoff/` に下書きを置いて、ユーザが `ccnavi user handoff` で取り込む。
// 取り込む前は、できあがりの姿は下書きにあり、取り込んだ後は本物にある。テストは、どちらの状態でも同じ中身を読めるよう、
// 下書きがあれば下書きを、無ければ本物を読む。

// Final はリポジトリからの相対パス（`/` 区切り）のファイルの、できあがりの姿のパス。
func Final(rel string) string {
	// 下書きの置き場での読み替えは、ccnavi-handoff-names.txt の表（唯一の元）に従う。
	table, err := handoffnames.Default()
	if err != nil {
		panic(err) // 埋め込みの写しは handoffnames のテストが確かめている。壊れたらテストの道具の誤り
	}
	draft := filepath.Join(Root(), "wip", "handoff", filepath.FromSlash(table.ToDraft(rel)))
	if info, err := os.Stat(draft); err == nil && !info.IsDir() {
		return draft
	}
	return filepath.Join(Root(), filepath.FromSlash(rel))
}

var (
	overlayMu   sync.Mutex
	overlayPath string
)

// ScriptsDir は `.ccnavi/scripts/` のできあがりの姿の置き場。
// 下書きが無ければ本物の置き場そのもの。あれば、本物をコピーした一時の置き場に下書きを重ねたもの（Main が消す）。
func ScriptsDir() string {
	real := filepath.Join(Root(), ".ccnavi", "scripts")
	drafts := filepath.Join(Root(), "wip", "handoff", ".ccnavi", "scripts")
	entries, err := os.ReadDir(drafts)
	if err != nil || len(entries) == 0 {
		return real
	}
	overlayMu.Lock()
	defer overlayMu.Unlock()
	if overlayPath != "" {
		return overlayPath
	}
	dir, err := os.MkdirTemp(TempBase(), "ccnavi-scripts-")
	if err != nil {
		panic(err)
	}
	atExit(dir)
	for _, from := range []string{real, drafts} {
		files, err := os.ReadDir(from)
		if err != nil {
			panic(err)
		}
		for _, f := range files {
			if f.IsDir() {
				continue
			}
			data, err := os.ReadFile(filepath.Join(from, f.Name()))
			if err != nil {
				panic(err)
			}
			info, err := f.Info()
			if err != nil {
				panic(err)
			}
			if err := os.WriteFile(filepath.Join(dir, f.Name()), data, info.Mode().Perm()); err != nil {
				panic(err)
			}
		}
	}
	overlayPath = dir
	return dir
}
