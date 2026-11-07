package ospath

import (
	"errors"
	"io/fs"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"
)

// Windows はこの機械が Windows か。OS で分ける場所はこの定数を見る（runtime.GOOS を直に比べない）。
const Windows = runtime.GOOS == "windows"

// Sep は機械の区切り（os.sep）。
const Sep = string(filepath.Separator)

func isSep(c byte) bool { return os.IsPathSeparator(c) }

func isSepRune(r rune) bool { return r < utf8.RuneSelf && isSep(byte(r)) }

// IsAbs は os.path.isabs。
func IsAbs(p string) bool {
	if Windows {
		return filepath.IsAbs(p)
	}
	return strings.HasPrefix(p, "/")
}

// Join は os.path.join。絶対の要素が来ると前を捨て、綴りは正規化しない（join("a", "") は "a/"）。
func Join(base string, elems ...string) string {
	path := base
	for _, e := range elems {
		switch {
		case Windows && filepath.VolumeName(e) != "":
			path = e
		case e != "" && isSep(e[0]):
			if Windows {
				path = filepath.VolumeName(path) + e
			} else {
				path = e
			}
		case path == "" || isSep(path[len(path)-1]):
			path += e
		default:
			path += Sep + e
		}
	}
	return path
}

func splitLast(p string) (vol, head, tail string) {
	vol = filepath.VolumeName(p)
	rest := p[len(vol):]
	i := len(rest)
	for i > 0 && !isSep(rest[i-1]) {
		i--
	}
	return vol, rest[:i], rest[i:]
}

// Dirname は os.path.dirname。末尾の区切りは落とすが、区切りだけの頭はそのまま残す。
func Dirname(p string) string {
	vol, head, _ := splitLast(p)
	if trimmed := strings.TrimRightFunc(head, isSepRune); trimmed != "" {
		head = trimmed
	}
	return vol + head
}

// Basename は os.path.basename（最後の区切りの後ろ。末尾が区切りなら空）。
func Basename(p string) string {
	_, _, tail := splitLast(p)
	return tail
}

// Normpath は os.path.normpath。POSIX では頭のちょうど 2 つの "/" を残す（Clean は 1 つにする）。
func Normpath(p string) string {
	if Windows {
		return filepath.Clean(p)
	}
	if p == "" {
		return "."
	}
	initial := 0
	if strings.HasPrefix(p, "/") {
		initial = 1
		if strings.HasPrefix(p, "//") && !strings.HasPrefix(p, "///") {
			initial = 2
		}
	}
	var comps []string
	for _, c := range strings.Split(p, "/") {
		if c == "" || c == "." {
			continue
		}
		if c != ".." || (initial == 0 && len(comps) == 0) || (len(comps) > 0 && comps[len(comps)-1] == "..") {
			comps = append(comps, c)
		} else if len(comps) > 0 {
			comps = comps[:len(comps)-1]
		}
	}
	out := strings.Repeat("/", initial) + strings.Join(comps, "/")
	if out == "" {
		return "."
	}
	return out
}

// Getwd はカレントディレクトリ。os.Getwd は $PWD がリンクを含んでもそのまま返すが、ここは実体のパスを返す。
func Getwd() (string, error) {
	cwd, err := os.Getwd()
	if err != nil {
		return "", err
	}
	if real, err := filepath.EvalSymlinks(cwd); err == nil {
		return real, nil
	}
	return cwd, nil
}

// Abspath は os.path.abspath。
func Abspath(p string) string {
	if !IsAbs(p) {
		cwd, err := Getwd()
		if err == nil {
			p = Join(cwd, p)
		}
	}
	return Normpath(p)
}

// Realpath は path を実体のパスにする。無い名前から先は書かれたまま足し、失敗にしない。
// リンクの輪はそこで解くのをやめる。
// 名前を 1 つずつ左から取り出して、リンクなら中身（行き先の名前）を積み直す方式で解く。
// EvalSymlinks と違い、途中の名前が無くてもそこまでの結果を返すため。
func Realpath(p string) string {
	if Windows {
		return realpathWindows(p)
	}
	// 1. 起点を決める。相対パスはカレントディレクトリから始める。
	rest := reversed(strings.Split(p, "/"))
	count := len(rest)
	path := "/"
	if !strings.HasPrefix(p, "/") {
		cwd, err := Getwd()
		if err != nil {
			return Abspath(p)
		}
		path = cwd
	}
	// rest の中の nil の印は「この名前のリンクを解き終えた」を表す。
	type item struct {
		name string
		mark bool
	}
	stack := make([]item, 0, len(rest))
	for _, r := range rest {
		stack = append(stack, item{name: r})
	}
	// 2. 残りの名前を 1 つずつ取り出す。seen はリンクごとの解けた結果（解いている最中は nil。輪の検出に使う）。
	seen := map[string]*string{}
	for count > 0 {
		top := stack[len(stack)-1]
		stack = stack[:len(stack)-1]
		if top.mark {
			link := stack[len(stack)-1].name
			stack = stack[:len(stack)-1]
			resolved := path
			seen[link] = &resolved
			continue
		}
		count--
		name := top.name
		if name == "" || name == "." {
			continue
		}
		if name == ".." {
			i := strings.LastIndex(path, "/")
			path = path[:i]
			if path == "" {
				path = "/"
			}
			continue
		}
		// 3. リンクでない名前（無い名前も）はそのまま足す。リンクは行き先の名前を積み直して続きで解く。
		newpath := path + "/" + name
		if path == "/" {
			newpath = path + name
		}
		info, err := os.Lstat(newpath)
		if err != nil || info.Mode()&os.ModeSymlink == 0 {
			path = newpath
			continue
		}
		if done, ok := seen[newpath]; ok {
			if done != nil {
				path = *done
			} else {
				path = newpath
			}
			continue
		}
		target, err := os.Readlink(newpath)
		if err != nil {
			path = newpath
			continue
		}
		if strings.HasPrefix(target, "/") {
			path = "/"
		}
		seen[newpath] = nil
		stack = append(stack, item{name: newpath}, item{mark: true})
		parts := reversed(strings.Split(target, "/"))
		for _, t := range parts {
			stack = append(stack, item{name: t})
		}
		count += len(parts)
	}
	return path
}

// realpathWindows は在る部分までを EvalSymlinks で解き、無い残りを足す（ntpath.realpath の近似）。
func realpathWindows(p string) string {
	abs := Abspath(p)
	head, tail := abs, ""
	for {
		if resolved, err := filepath.EvalSymlinks(head); err == nil {
			if tail == "" {
				return resolved
			}
			return Join(resolved, tail)
		}
		parent := filepath.Dir(head)
		if parent == head {
			return abs
		}
		name := filepath.Base(head)
		if tail == "" {
			tail = name
		} else {
			tail = Join(name, tail)
		}
		head = parent
	}
}

func reversed(s []string) []string {
	out := make([]string, len(s))
	for i, v := range s {
		out[len(s)-1-i] = v
	}
	return out
}

// Relpath は os.path.relpath。Windows で別のドライブのときは false。
func Relpath(path, start string) (string, bool) {
	rel, err := filepath.Rel(Abspath(start), Abspath(path))
	return rel, err == nil
}

// RelOr は start からの相対パス。相対にできない（Windows で別のドライブ）ときは path のまま。
func RelOr(path, start string) string {
	if rel, ok := Relpath(path, start); ok {
		return rel
	}
	return path
}

// DiskExists は os.path.exists（リンクは先を見る）。fsio の溜める段は見ない（記録やマーカーは fsio.Exists）。
func DiskExists(p string) bool {
	_, err := os.Stat(p)
	return err == nil
}

// DiskLexists は os.path.lexists。fsio の溜める段は見ない（記録やマーカーは fsio.Lexists）。
func DiskLexists(p string) bool {
	_, err := os.Lstat(p)
	return err == nil
}

// DiskListdir は os.listdir を名前の順にしたもの。fsio の溜める段は見ない（記録の置き場は fsio.Listdir）。
func DiskListdir(dir string) ([]string, error) {
	entries, err := os.ReadDir(dir) // 名前の順に返る
	if err != nil {
		return nil, err
	}
	names := make([]string, len(entries))
	for i, e := range entries {
		names[i] = e.Name()
	}
	return names, nil
}

// Stat は os.stat の更新時刻と大きさ（os.path.getmtime・getsize。リンクは先を見る）。無ければ false。
// fsio の溜める段は見ない。
func Stat(p string) (mtime time.Time, size int64, ok bool) {
	info, err := os.Stat(p)
	if err != nil {
		return time.Time{}, 0, false
	}
	return info.ModTime(), info.Size(), true
}

// IsFile は os.path.isfile。
func IsFile(p string) bool {
	info, err := os.Stat(p)
	return err == nil && info.Mode().IsRegular()
}

// Lstat は os.lstat（リンクは辿らない）。無ければ errors.Is(err, fs.ErrNotExist)（FileNotFoundError）。
func Lstat(p string) (fs.FileInfo, error) { return os.Lstat(p) }

// IsRegular はリンクを辿らずに見て、ふつうのファイルか（stat.S_ISREG(os.lstat(p).st_mode)）。リンク・ディレクトリ・
// 名前付きパイプ・見られないものは偽。
func IsRegular(p string) bool {
	info, err := Lstat(p)
	return err == nil && info.Mode().IsRegular()
}

// IsLink は os.path.islink。
func IsLink(p string) bool {
	info, err := os.Lstat(p)
	return err == nil && info.Mode()&os.ModeSymlink != 0
}

// IsDir は os.path.isdir。
func IsDir(p string) bool {
	info, err := os.Stat(p)
	return err == nil && info.IsDir()
}

// IsSpace は 1 文字が空白か（Python の str.isspace と同じ範囲）。Go の unicode.IsSpace に無い U+001C〜U+001F も空白に数える。
func IsSpace(r rune) bool {
	return unicode.IsSpace(r) || (r >= 0x1c && r <= 0x1f)
}

// Fields は s.split()（引数なし）。IsSpace の並びで区切り、空の語は返さない。
func Fields(s string) []string { return strings.FieldsFunc(s, IsSpace) }

// Squeeze は " ".join(s.split())。空白の並び（改行を含む）を 1 つの空白にし、前後の空白を除く。
func Squeeze(s string) string {
	return strings.Join(Fields(s), " ")
}

// Strip は引数なしの str.strip()。
func Strip(s string) string {
	return strings.TrimFunc(s, IsSpace)
}

// Rstrip は引数なしの str.rstrip()。
func Rstrip(s string) string {
	return strings.TrimRightFunc(s, IsSpace)
}

// errNotUTF8 は中身が UTF-8 として読めないこと（ReadDiskText が返す）。
var errNotUTF8 = errors.New("UTF-8 として読めない")

// ReadDiskText はディスクの本文を UTF-8 として読む。UTF-8 でなければ errNotUTF8。改行の "\r\n" と "\r" は "\n" にそろえる（テキストモード）。
// fsio の溜める段は見ない（記録やマーカーは fsio.ReadText）。
func ReadDiskText(path string) (string, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	if !utf8.Valid(data) {
		return "", errNotUTF8
	}
	return UnifyNewlines(string(data)), nil
}

// Slashed は区切りを "/" に揃える（どの機械でも `\` を "/" にする）。ルールと範囲の glob は "/" で書かれている。
// 機械の区切りだけを "/" にするなら filepath.ToSlash を使う（POSIX の `\` は名前の一部）。
func Slashed(path string) string {
	return strings.ReplaceAll(path, `\`, "/")
}

// HasDrive は p の 2 字目が ":" か（字は符号位置で数える）。letter なら、
// 1 字目が ASCII の英字のときだけ（Windows のドライブ `C:`）。
func HasDrive(p string, letter bool) bool {
	head, size := utf8.DecodeRuneInString(p)
	if size == 0 || !strings.HasPrefix(p[size:], ":") {
		return false
	}
	return !letter || head < utf8.RuneSelf && unicode.IsLetter(head)
}

// Splitlines は行に分ける。区切りは Python の str.splitlines と同じ集合（\r\n・\v・\f・U+001C〜U+001E・U+0085・U+2028・U+2029）で、区切り自体は残さない。
func Splitlines(s string) []string { return splitLines(s, false) }

// SplitlinesKeep は Splitlines と同じ区切りで分け、区切りを行の末尾に残す（つなげると元に戻る）。
func SplitlinesKeep(s string) []string { return splitLines(s, true) }

func splitLines(s string, keep bool) []string {
	var lines []string
	start := 0
	for i := 0; i < len(s); {
		r, size := utf8.DecodeRuneInString(s[i:])
		switch r {
		case '\n', '\v', '\f', 0x1c, 0x1d, 0x1e, 0x85, 0x2028, 0x2029, '\r':
			if r == '\r' && i+1 < len(s) && s[i+1] == '\n' {
				size = 2
			}
			end := i
			if keep {
				end = i + size
			}
			lines = append(lines, s[start:end])
			start = i + size
		}
		i += size
	}
	if start < len(s) {
		lines = append(lines, s[start:])
	}
	return lines
}

// UnifyNewlines はテキストの読みと同じく、"\r\n" と "\r" を "\n" にする。
func UnifyNewlines(text string) string {
	return strings.ReplaceAll(strings.ReplaceAll(text, "\r\n", "\n"), "\r", "\n")
}

// Splitext は os.path.splitext。拡張子は最後の名前の最後の "." から。名前の頭に並ぶ "." は拡張子の始まりと
// 数えない（".bashrc" は拡張子を持たない）。
func Splitext(p string) (root, ext string) {
	sep := -1
	for i := len(p) - 1; i >= 0; i-- {
		if isSep(p[i]) {
			sep = i
			break
		}
	}
	dot := strings.LastIndexByte(p, '.')
	if dot > sep && strings.Trim(p[sep+1:dot], ".") != "" {
		return p[:dot], p[dot:]
	}
	return p, ""
}
