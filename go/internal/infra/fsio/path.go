package fsio

import (
	"os"
	"os/user"
	"regexp"
	"strings"

	"ccnavi/internal/infra/ospath"
)

// lineSep は os.linesep。
var lineSep = func() string {
	if ospath.Windows {
		return "\r\n"
	}
	return "\n"
}()

// SafeNameLimit は SafeName が既定で切り詰める長さ（文字数）。
const SafeNameLimit = 64

// NoLimit は SafeName で切り詰めない印。
const NoLimit = -1

// FullPath はファイルのパスを、行き着く先が 1 つに決まる絶対パスに直す。空なら空。
//
// 守る対象は名前ではなく場所なので、相対パス・".."・シンボリックリンクで同じ場所を別の名前で
// 書いてもルールを外せないよう、リンクまで解いてから適用する。まだ無いファイルは解けたところまで
// 解いて残りを足す。実行前チェックと実行後チェック（git.Read）は必ずここを通し、同じ場所が
// 2 通りのパスでヒットしないようにする。cwd が空なら今の作業ディレクトリから見る。
func FullPath(path, cwd string) string {
	if path == "" {
		return ""
	}
	return ospath.Realpath(joined(path, cwd))
}

// SpelledPath は FullPath と同じく絶対パスにして ".." を取り除くが、リンクは解かない。
// リンクそのものの名前に適用する検査（解いた先と解く前の両方を見るもの）が使う。
func SpelledPath(path, cwd string) string {
	if path == "" {
		return ""
	}
	return ospath.Abspath(joined(path, cwd))
}

func joined(path, cwd string) string {
	if cwd == "" {
		if wd, err := ospath.Getwd(); err == nil {
			cwd = wd
		}
	}
	return ospath.Join(cwd, expandUser(path))
}

// ParentResolved はディレクトリだけを行き着く先に直し、最後の名前は書かれたまま残す。空なら空。
func ParentResolved(path string) string {
	if path == "" {
		return ""
	}
	return ospath.Join(ospath.Realpath(ospath.Dirname(path)), ospath.Basename(path))
}

// expandUser は os.path.expanduser。"~" と "~名前" で始まるときだけ家のディレクトリに置き換える。
func expandUser(path string) string {
	if !strings.HasPrefix(path, "~") {
		return path
	}
	i := 1
	for i < len(path) && !os.IsPathSeparator(path[i]) {
		i++
	}
	name := path[1:i]
	var home string
	switch {
	case ospath.Windows:
		home = windowsHome(name)
	case name == "":
		home = os.Getenv("HOME")
		if home == "" {
			if u, err := user.Current(); err == nil {
				home = u.HomeDir
			}
		}
	default:
		if u, err := user.Lookup(name); err == nil {
			home = u.HomeDir
		}
	}
	if home == "" {
		return path
	}
	if !ospath.Windows {
		home = strings.TrimRight(home, "/")
		if home+path[i:] == "" {
			return "/"
		}
	}
	return home + path[i:]
}

// windowsHome は ntpath.expanduser の家。USERPROFILE、無ければ HOMEDRIVE と HOMEPATH。
// 別の人の名前なら、自分の家の最後の名前が自分の名前のときだけ、そこをその名前に替える。
func windowsHome(name string) string {
	home, ok := os.LookupEnv("USERPROFILE")
	if !ok {
		homePath, ok := os.LookupEnv("HOMEPATH")
		if !ok {
			return ""
		}
		home = ospath.Join(os.Getenv("HOMEDRIVE"), homePath)
	}
	if name != "" {
		if mine := os.Getenv("USERNAME"); name != mine {
			if mine != ospath.Basename(home) {
				return ""
			}
			home = ospath.Join(ospath.Dirname(home), name)
		}
	}
	return home
}

// JSONExt は JSON の記録の拡張子。
const JSONExt = ".json"

// RecordPath は dir の下の、識別子 id から作る記録のパス（prefix + SafeName(id) + suffix）。
// 識別子は SafeNameLimit で切る。後始末（records/prune）は同じ prefix と suffix で名前から識別子を読む。
func RecordPath(dir, prefix, id, suffix string) string {
	return ospath.Join(dir, prefix+SafeName(id, SafeNameLimit)+suffix)
}

// unsafeChar はファイル名に混ぜられない字。識別子をそのまま名前にするので、区切りが混じった値で
// 別の場所へ書かせない。
var unsafeChar = regexp.MustCompile(`[^A-Za-z0-9._-]`)

// SafeName は識別子から作る置き場の名前。limit 文字で切る（NoLimit なら切らない）。
func SafeName(text string, limit int) string {
	runes := []rune(unsafeChar.ReplaceAllString(text, "_"))
	if limit >= 0 && len(runes) > limit {
		runes = runes[:limit]
	}
	return string(runes)
}

// atomicTemp は途中を見せない書き方が残した一時ファイルの名前（`.<名前>.<一意>.part[<拡張子>]`）。
var atomicTemp = regexp.MustCompile(`^\..+\.[^.]+\.part(\.[^.]*)?$`)

// IsTempName は途中を見せない書き方が異常終了して残した一時ファイルの名前か。先頭が "." でなければ違う。
func IsTempName(name string) bool {
	return atomicTemp.MatchString(name)
}

// TempOrigin は一時ファイルの名前なら先頭の "." を外した名前、違えばそのまま。
// 掃除する側が、本番と同じ「先頭と拡張子」の条件で一時ファイルも拾えるようにする。
func TempOrigin(name string) string {
	if IsTempName(name) {
		return name[1:]
	}
	return name
}
