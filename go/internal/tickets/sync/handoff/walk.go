package handoff

import (
	"cmp"
	"errors"
	"io/fs"
	"os"
	"sort"
	"strings"
	"syscall"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathmatch"
	"ccnavi/internal/tickets/model/handoffnames"
	"ccnavi/internal/tickets/model/place"
)

// names は下書きの置き場で読み替える階層名の表。埋め込みの表が読めないのはビルドの誤りで、handoffnames の
// テストが落とす。そのときは読み替えない表で動く。テストは表を差し替える。
var names = defaultNames()

func defaultNames() *handoffnames.Table {
	if t, err := handoffnames.Default(); err == nil {
		return t
	}
	return &handoffnames.Table{}
}

// Why は対象外にした理由。拡張の「取り込み待ち」の画面に名前と並べて出す。
type Why string

const (
	WhyNotAllowed Why = "not-allowed"
	WhyLinkedDir  Why = "linked-dir"
)

const (
	msgPlaceLinked  = place.Handoff + "/ がリンクか、ディレクトリではないので読めない"
	msgNotDirOnWay  = "取り込み先の途中（%s）がディレクトリではない"
	msgPlaceUnread  = "取り込み先の置き場を読めない（%s）"
	msgSpellingTwin = "取り込み先に、大文字小文字か NFC/NFD だけが違う名前が並んでいる（%s）"
)

// Found は置き場で見つけた下書き 1 本（まだ読んでいない）。Rel と Key は読み替えを戻した取り込み先のパス、
// Path は下書きの絶対パス、Drel は置き場の中の相対パス（置いてある綴りのまま）。
type Found struct {
	Rel, Key, Path, Drel string
}

// Ignored は対象外にした下書き（置き場からの相対パス）とその理由。
type Ignored struct {
	Path string
	Why  Why
}

func handoffDir(treeRoot string) string {
	return ospath.Join(treeRoot, config.WipRoot, config.HandoffName)
}

// Walk は置き場の下書き（ふつうのファイルとリンク）、対象外にしたもの、置き場そのものの問題を返す。
// リンクのディレクトリの中へは降りない（その中の下書きは取り込まない）。書き込みの途中で残った一時ファイルは数えない。
func Walk(treeRoot string) ([]Found, []Ignored, string) {
	base := handoffDir(treeRoot)
	if !ospath.DiskLexists(base) {
		return nil, nil, ""
	}
	if pathmatch.HasLink(treeRoot, base) || !ospath.IsDir(base) {
		return nil, nil, msgPlaceLinked
	}
	var found []Found
	var ignored []Ignored
	walkDir(base, func(dir string, files, dirs []string) {
		for _, n := range files {
			if fsio.IsTempName(n) {
				continue
			}
			full := ospath.Join(dir, n)
			drel := relSlash(full, base)
			rel := names.FromDraft(drel)
			key := Key(rel)
			if KindOf(key) == nil {
				ignored = append(ignored, Ignored{drel, WhyNotAllowed})
				continue
			}
			found = append(found, Found{rel, key, full, drel})
		}
		for _, n := range dirs {
			if full := ospath.Join(dir, n); ospath.IsLink(full) {
				ignored = append(ignored, Ignored{relSlash(full, base), WhyLinkedDir})
			}
		}
	})
	sort.SliceStable(found, func(i, j int) bool {
		if found[i].Key != found[j].Key {
			return found[i].Key < found[j].Key
		}
		return found[i].Rel < found[j].Rel
	})
	return found, ignored, ""
}

// walkDir は os.walk(top, followlinks=False) と同じ順（名前順、上から）で、ディレクトリごとにファイルと
// ディレクトリ（リンクの先がディレクトリのものを含む）の名前を渡す。リンクのディレクトリへは降りない。
// 読めないディレクトリは飛ばす。
func walkDir(top string, visit func(dir string, files, dirs []string)) {
	listed, err := os.ReadDir(top)
	if err != nil {
		return
	}
	var files, dirs []string
	for _, e := range listed {
		if isDir(ospath.Join(top, e.Name()), e) {
			dirs = append(dirs, e.Name())
		} else {
			files = append(files, e.Name())
		}
	}
	visit(top, files, dirs)
	for _, n := range dirs {
		if full := ospath.Join(top, n); !ospath.IsLink(full) {
			walkDir(full, visit)
		}
	}
}

// isDir は os.DirEntry.is_dir()（リンクは先を見る）。
func isDir(full string, e fs.DirEntry) bool {
	if e.Type()&fs.ModeSymlink != 0 {
		return ospath.IsDir(full)
	}
	return e.IsDir()
}

func relSlash(full, base string) string {
	rel, _ := ospath.Relpath(full, base)
	return ospath.Slashed(rel)
}

// Spelled は取り込み先の絶対パスを、在るディレクトリとファイルの実際の表記で組む。(パス, 断る理由)。
// 大文字小文字・NFC/NFD だけが違う名前は同じものとして扱い、同じ形にそろう名前が 2 つ並んでいれば断る。
func Spelled(treeRoot, rel string) (string, string) {
	current := treeRoot
	parts := strings.Split(rel, "/")
	for i, part := range parts {
		listed, err := os.ReadDir(current)
		if err != nil {
			switch {
			case errors.Is(err, fs.ErrNotExist):
				return ospath.Join(current, parts[i:]...), ""
			case errors.Is(err, syscall.ENOTDIR) || ospath.DiskExists(current) && !ospath.IsDir(current):
				where := current
				if i > 0 {
					where = parts[i-1]
				}
				return "", sprintf(msgNotDirOnWay, line(where))
			}
			return "", sprintf(msgPlaceUnread, line(cmp.Or(fsio.StrError(err), fsio.ErrorText(err))))
		}
		want := FoldPart(part)
		var hits []string
		for _, e := range listed {
			if FoldPart(e.Name()) == want {
				hits = append(hits, e.Name())
			}
		}
		sort.Strings(hits)
		if len(hits) > 1 {
			return "", sprintf(msgSpellingTwin, strings.Join(hits, ", "))
		}
		if len(hits) == 0 {
			return ospath.Join(current, parts[i:]...), ""
		}
		current = ospath.Join(current, hits[0])
	}
	return current, ""
}
