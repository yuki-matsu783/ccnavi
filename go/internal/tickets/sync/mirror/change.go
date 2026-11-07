package mirror

import (
	"bytes"
	"errors"
	"io/fs"
	"os"
	"path/filepath"
	"slices"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/pathmatch"
)

// Action は変更の種類。値は JSON の action の表記（拡張が読む）。
type Action string

const (
	ActionCreate Action = "create"
	ActionUpdate Action = "update"
	ActionDelete Action = "delete"
)

// tempSuffix は書くときの一時ファイルの名前の末尾。異常終了して残ったものを write-and-push が名前で見分ける。
const tempSuffix = ".ccnavi-sync"

// Change は写し先に加える（加えた）変更 1 本。
type Change struct {
	// Rel は写し先のツリーからの相対（リンクを解いた実体から。"/" 区切り）。
	Rel    string
	target string
	// content は書く中身。remove なら使わない。
	content []byte
	remove  bool
	// before は変える前の中身（戻すときに使う）。existed が偽なら無かった。
	before  []byte
	existed bool
	// sweep は消したあと空になったディレクトリを片付ける置き場（消す変更だけ）。
	sweep string
}

// Action は変更の種類。消すなら delete、元が無ければ create、あれば update。
func (c Change) Action() Action {
	switch {
	case c.remove:
		return ActionDelete
	case !c.existed:
		return ActionCreate
	}
	return ActionUpdate
}

// IsSkill は工程スキルのコピー（`skills/…`）の変更か。それ以外はミラー（`.ccnavi/…`）。
func (c Change) IsSkill() bool { return strings.HasPrefix(c.Rel, config.SkillsDir+"/") }

// 写せない理由の文。
const (
	linkedPath  = " がシンボリックリンクか、その下にある。コピーしない"
	linkedEntry = " がシンボリックリンク。コピーしない"
	missing     = "無い"
)

func unreadable(shown, why string) string { return shown + " を読めない (" + why + ")" }

// updates は wanted（base からの相対 → 中身）のうち、先の中身と違うものを書く変更。名前の順。
func updates(treeRoot, base string, wanted map[string][]byte) ([]Change, string) {
	var out []Change
	for _, name := range sortedKeys(wanted) {
		target := ospath.Join(base, filepath.FromSlash(name))
		if pathmatch.HasLink(treeRoot, target) {
			return nil, relPlain(treeRoot, target) + linkedPath
		}
		before, existed, why := readStrict(target)
		if why != "" {
			return nil, unreadable(relReal(treeRoot, target), why)
		}
		if existed && same(before, wanted[name]) {
			continue
		}
		out = append(out, Change{Rel: relReal(treeRoot, target), target: target, content: wanted[name],
			before: before, existed: existed})
	}
	return out, ""
}

// removals は dir に今ある present（dir からの相対）のうち、gone が真のものを消す変更。名前の順。
func removals(treeRoot, dir string, present []string, gone func(rel string) bool) ([]Change, string) {
	var out []Change
	for _, rel := range present {
		if !gone(rel) {
			continue
		}
		target := ospath.Join(dir, filepath.FromSlash(rel))
		before, existed, why := readStrict(target)
		if why != "" || !existed {
			if why == "" {
				why = missing
			}
			return nil, unreadable(relReal(treeRoot, target), why)
		}
		out = append(out, Change{Rel: relReal(treeRoot, target), target: target, remove: true,
			before: before, existed: true, sweep: dir})
	}
	return out, ""
}

// scan は dir の下の通常のファイル（dir からの相対、"/" 区切り、名前の順）。置き場が無ければ空。
// リンクが 1 本でもあれば、shownFrom からの相対で名指しして止める。読めないディレクトリは飛ばす（os.walk と同じ）。
func scan(dir, shownFrom string) ([]string, string) {
	if !ospath.DiskLexists(dir) {
		return nil, ""
	}
	var found []string
	var walk func(sub string) string
	walk = func(sub string) string {
		entries, err := os.ReadDir(sub)
		if err != nil {
			return ""
		}
		var dirs []string
		for _, e := range entries {
			path := ospath.Join(sub, e.Name())
			if pathmatch.IsLink(path) {
				return relPlain(shownFrom, path) + linkedEntry
			}
			if e.IsDir() {
				dirs = append(dirs, path)
			} else {
				found = append(found, relPlain(dir, path))
			}
		}
		for _, d := range dirs {
			if why := walk(d); why != "" {
				return why
			}
		}
		return ""
	}
	if why := walk(dir); why != "" {
		return nil, why
	}
	slices.Sort(found)
	return found, ""
}

// apply は変更を順に書く。書けなかった理由を返し、書けたら空文字。1 本でも書けなければ、した分を戻し、
// 戻せなかったものを名指しする。書けたら、消す変更の置き場と prune の下から空になったディレクトリを片付ける。
func apply(changes []Change, prune ...string) string {
	for i, c := range changes {
		failed := put(c.target, c.content, c.remove)
		if failed == "" {
			continue
		}
		var stuck []string
		for j := i - 1; j >= 0; j-- {
			w := changes[j]
			if put(w.target, w.before, !w.existed) != "" {
				stuck = append(stuck, w.Rel)
			}
		}
		if len(stuck) > 0 {
			return c.Rel + " を保存できませんでした: " + failed + "（戻せなかったもの: " + strings.Join(stuck, ", ") + "）"
		}
		return c.Rel + " を保存できませんでした: " + failed + "（した分は戻した）"
	}
	for _, c := range changes {
		if c.remove && !slices.Contains(prune, c.sweep) {
			prune = append(prune, c.sweep)
		}
	}
	for _, base := range prune {
		pruneEmpty(base)
	}
	return ""
}

// put は 1 本を書く（remove なら消す）。fsio を通す（write-and-push の記録がこの実行で書いたパスに数える）。
func put(path string, content []byte, remove bool) string {
	if remove {
		if !fsio.Lexists(path) {
			return ""
		}
		return fsio.Unlink(path)
	}
	return fsio.ReplaceFile(path, content, tempSuffix)
}

// pruneEmpty は base の下の空のディレクトリを、深い方から消す。base 自身は残す。失敗は飲む。
func pruneEmpty(base string) {
	var dirs []string
	var walk func(dir string)
	walk = func(dir string) {
		entries, err := os.ReadDir(dir)
		if err != nil {
			return
		}
		for _, e := range entries {
			if e.IsDir() {
				path := ospath.Join(dir, e.Name())
				dirs = append(dirs, path)
				walk(path)
			}
		}
	}
	walk(base)
	for _, dir := range slices.Backward(dirs) {
		_ = os.Remove(dir)
	}
}

// readStrict は中身。無ければ existed が偽で why は空、在るのに読めなければ why に OS の文言の理由。
func readStrict(path string) (content []byte, existed bool, why string) {
	data, err := fsio.ReadDiskBytes(path)
	if errors.Is(err, fs.ErrNotExist) {
		return nil, false, ""
	}
	if err != nil {
		return nil, false, err.Error()
	}
	return data, true, ""
}

// same は中身が同じか。改行の違いは見ない（Windows のチェックアウトで書き換わるため）。
func same(a, b []byte) bool {
	crlf, lf := []byte("\r\n"), []byte("\n")
	return bytes.Equal(bytes.ReplaceAll(a, crlf, lf), bytes.ReplaceAll(b, crlf, lf))
}

// relPlain はリンクを解かない相対（ユーザに見せるパス）。
func relPlain(base, path string) string {
	return filepath.ToSlash(ospath.RelOr(path, base))
}

// relReal はリンクを解いた実体どうしの相対（Change.Rel）。
func relReal(base, path string) string { return relPlain(ospath.Realpath(base), ospath.Realpath(path)) }

// inside は target の実体が treeRoot の実体の中か（同じも含む）。
func inside(treeRoot, target string) bool {
	base, real := pathcase.Real(treeRoot), pathcase.Real(target)
	return real == base || strings.HasPrefix(real, base+ospath.Sep)
}

func sortedKeys(m map[string][]byte) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	slices.Sort(keys)
	return keys
}

// describe は同期の出力の、変更ごとの行。
func describe(changes []Change) []string {
	lines := make([]string, 0, len(changes))
	for _, c := range changes {
		state := "上書き"
		switch {
		case c.remove:
			state = "削除した"
		case !c.existed:
			state = "新しく置いた"
		}
		lines = append(lines, "  - "+c.Rel+"（"+state+"）")
	}
	return lines
}
