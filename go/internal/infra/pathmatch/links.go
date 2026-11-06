package pathmatch

import (
	"os"
	"runtime"
	"strings"

	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
)

// 調べきれないとき（パスが解けない・root の外に出る）は、HasLink と Escapes は拒否する側にする。
// Scan は lstat の失敗を Missing として返し、呼び手が決める。IsLink は失敗を偽にする（os.path.islink と同じ）。

// LinkState は Scan の答え。
type LinkState int

const (
	// Clean は道にリンクが無い。
	Clean LinkState = iota
	// Link は道にリンクがある。
	Link
	// Missing は途中の名前を lstat できなかった。
	Missing
)

// isJunction は Windows の junction か。Go は junction を ModeIrregular で返す。テストで差し替える。
var isJunction = func(path string) bool {
	if runtime.GOOS != "windows" {
		return false
	}
	info, err := os.Lstat(path)
	return err == nil && info.Mode()&os.ModeIrregular != 0
}

// IsLink はシンボリックリンクか junction か。失敗したときは偽。
func IsLink(path string) bool {
	info, err := os.Lstat(path)
	if err == nil && info.Mode()&os.ModeSymlink != 0 {
		return true
	}
	return isJunction(path)
}

// unresolvable は Python の realpath が ValueError を投げる綴り（NUL を含む）。
func unresolvable(paths ...string) bool {
	for _, p := range paths {
		if strings.ContainsRune(p, 0) {
			return true
		}
	}
	return false
}

// HasLink は root の下の path までの道（path 自身を含む）にリンクがあるか。
//
// path から上へさかのぼり、root に着いたら止める。root 自身と root より上のリンク（macOS の /tmp）は数えない。
// root と違う綴り（/tmp と /private/tmp）の path も、実体が root に着けば止まる。root へのリンクを通った
// 綴りはそのリンクがあるので真。root に着けないときは真。
func HasLink(root, path string) bool {
	if unresolvable(root, path) {
		return true
	}
	base := pathcase.Fold(ospath.Realpath(root))
	top := pathcase.Fold(ospath.Abspath(root))
	inside := strings.TrimRight(top, ospath.Sep) + ospath.Sep
	current := ospath.Abspath(path)
	for pathcase.Fold(current) != top {
		if IsLink(current) {
			return true
		}
		// root の下の綴りなら root まで 1 段ずつ調べる（途中のリンクが root の中へ戻っても見逃さない）。
		outside := !strings.HasPrefix(pathcase.Fold(current), inside)
		if outside && pathcase.Fold(ospath.Realpath(current)) == base {
			return false
		}
		parent := ospath.Dirname(current)
		if parent == current {
			return true
		}
		current = parent
	}
	return false
}

// Scan は root から relParts を下へ順に lstat する。Link なら最初に見つかったリンクのパスを添える。
// includeLast が偽なら最後の名前は調べない（リンクだけを消す削除なら、最後がリンクでも構わない）。
func Scan(root string, relParts []string, includeLast bool) (LinkState, string) {
	parts := relParts
	if !includeLast && len(parts) > 0 {
		parts = parts[:len(parts)-1]
	}
	current := root
	for _, part := range parts {
		current = ospath.Join(current, part)
		info, err := os.Lstat(current)
		if err != nil {
			return Missing, ""
		}
		if info.Mode()&os.ModeSymlink != 0 || isJunction(current) {
			return Link, current
		}
	}
	return Clean, ""
}

// FirstLink は root から relParts を下へたどり、最初に見つかったリンクのパス。無ければ ok が偽。
func FirstLink(root string, relParts []string, includeLast bool) (string, bool) {
	state, path := Scan(root, relParts, includeLast)
	return path, state == Link
}

// Escapes は realpath で解くと root の外に出るか。root 自身は外ではない。解けないときは真。
func Escapes(root, path string) bool {
	if unresolvable(root, path) {
		return true
	}
	top := pathcase.Fold(ospath.Realpath(root))
	real := pathcase.Fold(ospath.Realpath(path))
	return real != top && !strings.HasPrefix(real, top+ospath.Sep)
}
