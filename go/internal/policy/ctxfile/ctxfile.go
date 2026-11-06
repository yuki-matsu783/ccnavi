// Package ctxfile は、当たったルールがモデルへ渡す文（additionalContext）を組む。文と、ファイルの本文
// （additionalContextFile）と、1 度だけ渡す文（…Once）の 3 つを並べ、どの回に渡すかを刻む every と、
// その数えの記録（state の `once-*.json`・`stop-*.json`）も持つ。文脈はセッションと、サブエージェントなら
// その 1 回の起動で分ける。
//
// 読むのはワークスペースの中だけ。絶対パスと `..` で上に出るパスは lint が止め、実行時も読まない
// （ルールから任意のファイルをモデルに渡せる形にしない）。本文は固定の上限で切り、切ったことを末尾に書く
// （黙って切ると、モデルは途中で終わる文を全部だと思って読む）。
//
// Python からの対応: deliver と for_rules → Deliver（for_rules は 2 つ目の戻り値を捨てる呼び方）、
// unsure_speaks=False → Options.QuietWhenUnsure、load → Body、bad_path・locate・over_limit・bases・
// stop_path・forget → BadPath・Locate・OverLimit・Bases・StopPath・Forget、told_refs・remember_refs・
// told_peers・remember_peers → ToldRefs・RememberRefs・ToldPeers・RememberPeers（set は map[string]bool）、
// MAX_CHARS → MaxChars、`once-` の頭 → OncePrefix。
package ctxfile

import (
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"slices"
	"strings"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/rules"
)

// MaxChars は 1 回の応答に載せる本文の上限（文字数）。超えた分は載せず、切ったことを言う。
const MaxChars = 4000

// OnceKeep は「1 度だけ渡す文」の記憶を残す期間。セッションの開始で消えるのが本筋で、これは開始が来ないまま
// 終わったセッションの分を掃く上限。
const OnceKeep = 3 * 24 * time.Hour

// 記録の名前。
const (
	OncePrefix = "once-"
	StopPrefix = "stop-"
	// 文脈の名前が無いときの代わり。
	unknownSession = "unknown"
	mainAgent      = "main"
	refsContext    = "refs"
	peersContext   = "peers-"
	givenKey       = "given"
)

// BadPath は欄の値がワークスペースの中を指せない理由。問題なければ空。
func BadPath(rel string) string {
	if rel == "" {
		return ""
	}
	slashed := ospath.Slashed(rel)
	if ospath.IsAbs(rel) || strings.HasPrefix(slashed, "/") || len(slashed) > 1 && slashed[1] == ':' {
		return "絶対パスは指定できない。ルートからの相対パスで書く"
	}
	if slices.Contains(strings.Split(slashed, "/"), "..") {
		return "`..` で上に出るパスは指定できない"
	}
	return ""
}

// Locate は候補のルートを順に見て、最初に在ったファイルの場所。無ければ空。
func Locate(bases []string, rel string) string {
	if rel == "" || BadPath(rel) != "" {
		return ""
	}
	for _, base := range bases {
		full := ospath.Join(base, strings.ReplaceAll(rel, "/", string(os.PathSeparator)))
		if ospath.IsFile(full) {
			return full
		}
	}
	return ""
}

// head は先頭の MaxChars+1 字（UTF-8 として読めないバイトは U+FFFD）。
func head(full string) ([]rune, error) {
	content, err := os.ReadFile(full)
	if err != nil {
		return nil, errors.New(fsio.ErrorText(err))
	}
	text, _ := fsio.Decode(content, fsio.Replace)
	runes := []rune(text)
	return runes[:min(len(runes), MaxChars+1)], nil
}

// Body はファイルの本文。無ければ空。上限を超えたら先頭だけを返し、切ったことを末尾に書く。
func Body(stderr io.Writer, bases []string, rel string) string {
	full := Locate(bases, rel)
	if full == "" {
		return ""
	}
	runes, err := head(full)
	if err != nil {
		fmt.Fprintf(stderr, "ccnavi: %s を読めない: %s\n", rel, err)
		return ""
	}
	if len(runes) <= MaxChars {
		return ospath.Strip(string(runes))
	}
	return strings.TrimRightFunc(string(runes[:MaxChars]), ospath.IsSpace) +
		fmt.Sprintf("\n\n(ccnavi: %s は %d 文字を超えるので先頭だけを載せた。続きはこのファイルを読んでください)", rel, MaxChars)
}

// OverLimit はファイルが上限を超えるか。読めなければ偽（無いのと同じ扱い）。
func OverLimit(full string) bool {
	runes, err := head(full)
	return err == nil && len(runes) > MaxChars
}

// Options は Deliver の選び方。
type Options struct {
	// Bases はファイルを探すルート（近い順）。
	Bases []string
	// QuietWhenUnsure は、覚えられない回（記録の置き場が無い・読めない・書けない）に、刻みを持つルールも
	// once を持つルールも渡さない。既定（偽）は逆に、覚えられないなら毎回渡す（届かない案内は書いていないのと
	// 同じなので）。真にするのは、案内を渡すとターンの終わりが引き止められる Stop の促し。
	QuietWhenUnsure bool
	// CountsPath は数えの記録の置き場の差し替え。空なら文脈ごとの `once-*.json`。
	CountsPath string
}

// Deliver は当たったルールがモデルへ渡す案内（1 件ごとに空行で区切る）と、この呼び出しで once の案内を
// 渡したか。渡すのは「渡す回」で、every: N は当たった回数が N の倍数になった回。additionalContext は渡す回の
// たび、additionalContextOnce は渡す回の最初の 1 回。数えはルール 1 件で 1 回（文とファイルで分けない）。
// 記録の置き場が無い（stateDir が空）なら刻まず、once の案内も毎回渡す。
func Deliver(stderr io.Writer, stateDir string, in hookio.Input, group []*rules.Rule, o Options) (string, bool) {
	if o.QuietWhenUnsure && stateDir == "" {
		return "", false
	}
	path := o.CountsPath
	if path == "" && stateDir != "" {
		path = oncePath(stateDir, in.SessionID, in.AgentID)
	}
	var parts []string
	toldOnce := false
	var counted map[string]int
	consulted, unreadable := false, false
	for _, rule := range group {
		hasOnce := rule.AdditionalContextOnce != "" || rule.AdditionalContextOnceFile != ""
		delivering, first := true, true
		// 数えを残すのは刻みを持つルールと once を持つルールだけ（ほかはどのみち毎回渡す）。
		if stateDir != "" && (rule.Every > 1 || hasOnce) {
			if !consulted {
				counted, unreadable = loadOnce(stderr, path)
				consulted = true
			}
			if unreadable && o.QuietWhenUnsure {
				continue
			}
			if !unreadable {
				// 渡す回は刻みの倍数になった回。その最初は刻みの回そのものなので、once を渡したかを別に覚えない。
				hits := counted[rule.ID] + 1
				counted[rule.ID] = hits
				delivering, first = hits%rule.Every == 0, hits == rule.Every
			}
		}
		if delivering {
			if text := withFile(stderr, o.Bases, rules.FillText(rule.AdditionalContext, rule.Root), rule.AdditionalContextFile); text != "" {
				parts = append(parts, text)
			}
		}
		if hasOnce && first {
			if once := withFile(stderr, o.Bases, rules.FillText(rule.AdditionalContextOnce, rule.Root), rule.AdditionalContextOnceFile); once != "" {
				parts = append(parts, once)
				toldOnce = true
			}
		}
	}
	// 読めなかった記録は書き戻さない（読めなかっただけの記録を空の中身で置き換えない）。
	if consulted && !unreadable {
		if !saveOnce(stderr, path, counted) && o.QuietWhenUnsure {
			// 書けなかった。次の回も同じ数えから始まり、同じ回に止め続けうる。
			return "", false
		}
	}
	return strings.Join(parts, "\n\n"), toldOnce
}

func withFile(stderr io.Writer, bases []string, text, rel string) string {
	var parts []string
	if text != "" {
		parts = append(parts, text)
	}
	if rel != "" {
		if body := Body(stderr, bases, rel); body != "" {
			parts = append(parts, body)
		}
	}
	return strings.Join(parts, "\n\n")
}

// Bases はルールが指すファイルを探すルート（近い順）。行き先がワークツリーならそのワークツリー、
// 元のプロジェクト、最後にワークスペースルート（ワークツリーで直している最中の案内がそのまま使われる）。
func Bases(conf *config.Settings, root string, target *tree.Tree) []string {
	var bases []string
	if target != nil && !target.IsMain() {
		bases = append(bases, target.Root)
	}
	if target != nil && target.Project != "" {
		bases = append(bases, tree.ProjectRoot(conf.Projects, target.Project))
	}
	return append(bases, root)
}

func safeOr(text, fallback string) string {
	if s := fsio.SafeName(text, fsio.SafeNameLimit); s != "" {
		return s
	}
	return fallback
}

func oncePath(stateDir, session, agentID string) string {
	return ospath.Join(stateDir, OncePrefix+safeOr(session, unknownSession)+"-"+safeOr(agentID, mainAgent)+fsio.JSONExt)
}

// StopPath は Stop の促し（ターンの終わり N 回に 1 度）の数えの記録。compact・再開・clear では消さず、
// 起動の Forget と後始末だけが消す（文脈ごとに消すと、compact が N 回より先に来る長いセッションで届かない）。
func StopPath(stateDir, session string) string {
	return ospath.Join(stateDir, StopPrefix+safeOr(session, unknownSession)+fsio.JSONExt)
}

// loadOnce はこの文脈で、どのルールが何回当たったか。まだ無ければ空、読めなければ unreadable が真。
// 分けるのは、読めなかった回に「まだ 1 回も当たっていない」として書き戻し、覚えていた分を消さないため。
func loadOnce(stderr io.Writer, path string) (map[string]int, bool) {
	data, err := fsio.ReadJSON(path)
	if err != nil {
		if !errors.Is(err, fs.ErrNotExist) {
			fmt.Fprintf(stderr, "ccnavi: 渡した回数の記録を読めない: %s\n", err)
			return nil, true
		}
		return map[string]int{}, false
	}
	out := map[string]int{}
	obj, _ := data.(*pyjson.Object)
	given, _ := obj.Value(givenKey).(*pyjson.Object)
	for k, v := range given.All() {
		key, ok := k.(string)
		if !ok {
			continue
		}
		switch v.(type) {
		case int, bool:
			n, _ := pyfmt.IntOf(v)
			out[key] = n
		}
	}
	return out, false
}

// saveOnce は記録を書く（取り合いになる記録なので、途中を見せない書き方で置く）。書けたか。
func saveOnce(stderr io.Writer, path string, given map[string]int) bool {
	keys := make([]string, 0, len(given))
	for k := range given {
		keys = append(keys, k)
	}
	slices.Sort(keys)
	inner := pyjson.NewObject()
	for _, k := range keys {
		inner.Set(k, given[k])
	}
	if failed := fsio.WriteJSON(path, pyjson.NewObject(givenKey, inner)); failed != "" {
		fmt.Fprintf(stderr, "ccnavi: 渡した回数の記録を保存できませんでした: %s\n", failed)
		return false
	}
	return true
}

func toldIn(stderr io.Writer, path string) (map[string]bool, bool) {
	given, unreadable := loadOnce(stderr, path)
	if unreadable {
		return nil, false
	}
	out := map[string]bool{}
	for k := range given {
		out[k] = true
	}
	return out, true
}

func remember(stderr io.Writer, path string, told map[string]bool) bool {
	given := map[string]int{}
	for k := range told {
		given[k] = 1
	}
	return saveOnce(stderr, path, given)
}

// ToldRefs はこのセッションで案内した issue・MR の指定（`<種類>:<番号>`）。覚えられない（置き場が無い・
// 読めない）なら ok が偽で、呼ぶ側は案内する側に倒す。
func ToldRefs(stderr io.Writer, stateDir, session string) (map[string]bool, bool) {
	if stateDir == "" || session == "" {
		return nil, false
	}
	return toldIn(stderr, oncePath(stateDir, session, refsContext))
}

// RememberRefs は案内した指定を残す。書けたか。
func RememberRefs(stderr io.Writer, stateDir, session string, told map[string]bool) bool {
	if stateDir == "" || session == "" {
		return false
	}
	return remember(stderr, oncePath(stateDir, session, refsContext), told)
}

// ToldPeers はこのサブエージェントに、同じワークツリーの相手として案内した相手のキー。Deliver の記録とは
// 別のファイルに置く（同じファイルだと、Deliver が書き戻すときに消える）。
func ToldPeers(stderr io.Writer, stateDir, session, agentID string) (map[string]bool, bool) {
	if stateDir == "" || session == "" || agentID == "" {
		return nil, false
	}
	return toldIn(stderr, oncePath(stateDir, session, peersContext+agentID))
}

// RememberPeers は案内した相手を残す。書けたか。
func RememberPeers(stderr io.Writer, stateDir, session, agentID string, told map[string]bool) bool {
	if stateDir == "" || session == "" || agentID == "" {
		return false
	}
	return remember(stderr, oncePath(stateDir, session, peersContext+agentID), told)
}

// Forget はこのセッションの数え（渡した回）を全部消し、古いセッションの分も掃く。Stop の促しの数え
// （StopPath）は起動（startup）のときだけ消す。消せなくても次の開始でまた試す。
func Forget(stateDir, session string, startup bool) {
	if stateDir == "" || !ospath.IsDir(stateDir) {
		return
	}
	if startup {
		_ = os.Remove(StopPath(stateDir, session))
	}
	own := ospath.Basename(oncePath(stateDir, session, ""))
	mine := own[:strings.LastIndex(own, "-")+1]
	cutoff := time.Now().Add(-OnceKeep)
	names, err := ospath.DiskListdir(stateDir)
	if err != nil {
		return
	}
	for _, entry := range names {
		// 書きかけで残った一時ファイル（`.once-….part.json`）も、先頭の `.` を外して同じ条件で掃く。
		name := fsio.TempOrigin(entry)
		if !strings.HasSuffix(name, fsio.JSONExt) || !strings.HasPrefix(name, OncePrefix) {
			continue
		}
		path := ospath.Join(stateDir, entry)
		if strings.HasPrefix(name, mine) {
			_ = os.Remove(path)
			continue
		}
		if mtime, _, ok := ospath.Stat(path); ok && mtime.Before(cutoff) {
			_ = os.Remove(path)
		}
	}
}
