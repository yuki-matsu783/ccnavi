package ctxfile

import (
	"errors"
	"fmt"
	"io"
	"io/fs"
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

// OnceKeep は「1 度だけ渡す文」の記憶を残す期間。セッションの開始で消えるのが本筋だが、これは開始が来ないまま
// 終わったセッションの分を掃くための上限である。selfguard のバックアップの期間と同じ 3 日だが、別の約束（Python も別の定数）。
const OnceKeep = 3 * 24 * time.Hour

// 記録の名前。
const (
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
	if ospath.IsAbs(rel) || strings.HasPrefix(slashed, "/") || ospath.HasDrive(slashed, false) {
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
		full := ospath.Join(base, strings.ReplaceAll(rel, "/", ospath.Sep))
		if ospath.IsFile(full) {
			return full
		}
	}
	return ""
}

// head は先頭の MaxChars+1 字（UTF-8 として読めないバイトは U+FFFD）。ファイルの先頭だけを読む。
func head(full string) ([]rune, error) {
	text, err := fsio.ReadDiskTextHead(full, MaxChars+1, fsio.Replace)
	return []rune(text), err
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
	return ospath.Rstrip(string(runes[:MaxChars])) +
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
	// once を持つルールも渡さない。既定（偽）は逆に、覚えられないなら毎回渡す。届かない案内は書いていないのと
	// 同じになるためである。真にするのは、案内を渡すとターンの終わりが引き止められる Stop の促しだけ。
	QuietWhenUnsure bool
	// CountsPath は数えの記録の置き場の差し替え。空なら文脈ごとの `once-*.json`。
	CountsPath string
}

// Once はルールファイルの外の組み込みの案内 1 件。ID は数えのキー（rules.ReservedIDPrefix で始める）。
type Once struct {
	ID, Text string
}

// DeliverOnce は組み込みの案内を、ルールの additionalContextOnce と同じ数え（既定の every で、渡す回の最初の 1 回だけ）で
// 渡す。案内を持つ側がルールの入れ物を組み直さずに済むよう、ここで組む。
func DeliverOnce(stderr io.Writer, stateDir string, in hookio.Input, notes ...Once) string {
	if len(notes) == 0 {
		return ""
	}
	carriers := make([]*rules.Rule, len(notes))
	for i, n := range notes {
		carriers[i] = &rules.Rule{ID: n.ID, Every: rules.EveryDefault, AdditionalContextOnce: n.Text}
	}
	said, _ := Deliver(stderr, stateDir, in, carriers, Options{})
	return said
}

// Deliver は、当たったルールがモデルへ渡す案内（1 件ごとに空行で区切る）と、この呼び出しで once の案内を
// 渡したかを返す。渡すのは「渡す回」で、every: N は当たった回数が N の倍数になった回を指す。additionalContext は渡す回の
// たびに、additionalContextOnce は渡す回の最初の 1 回に渡す。数えはルール 1 件につき 1 回（文とファイルで分けない）。
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
	return strings.Join(parts, pyfmt.ParagraphBreak), toldOnce
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
	return strings.Join(parts, pyfmt.ParagraphBreak)
}

// Bases はルールが指すファイルを探すルートを、近い順に返す。行き先がワークツリーならそのワークツリー、
// 次に元のプロジェクト、最後にワークスペースルートの順にする。ワークツリーで直している最中の案内が、そのまま使われるためである。
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

func oncePath(stateDir, session, agentID string) string {
	name := fsio.SafeNameOr(session, unknownSession, fsio.SafeNameLimit) + "-" + fsio.SafeNameOr(agentID, mainAgent, fsio.SafeNameLimit)
	return ospath.Join(stateDir, config.OncePrefix+name+fsio.JSONExt)
}

// StopPath は Stop の促し（ターンの終わり N 回に 1 度）の数えの記録の場所。compact・再開・clear では消さず、
// 起動の Forget と後始末だけが消す。文脈ごとに消すと、compact が N 回より先に来る長いセッションで促しが届かなくなる。
func StopPath(stateDir, session string) string {
	return ospath.Join(stateDir, config.StopPrefix+fsio.SafeNameOr(session, unknownSession, fsio.SafeNameLimit)+fsio.JSONExt)
}

// loadOnce は、この文脈でどのルールが何回当たったかを返す。まだ無ければ空、読めなければ unreadable が真になる。
// 読めない回に「まだ 1 回も当たっていない」として書き戻すと、覚えていた分が消えてしまう。そのため分けている。
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

// ToldRefs は、このセッションで案内した issue・MR の指定（`<種類>:<番号>`）を返す。覚えられない（置き場が無い・
// 読めない）なら ok が偽になり、呼ぶ側は案内する側に倒す。
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

// ToldPeers は、このサブエージェントに同じワークツリーの相手として案内した相手のキーを返す。Deliver の記録とは
// 別のファイルに置く。同じファイルだと、Deliver が書き戻すときに消えてしまうためである。
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

// Forget は、このセッションの数え（渡した回）を全部消し、古いセッションの分も掃く。Stop の促しの数え
// （StopPath）は、起動（startup）のときだけ消す。消せなくても、次の開始でまた試す。
func Forget(stateDir, session string, startup bool) {
	if stateDir == "" || !ospath.IsDir(stateDir) {
		return
	}
	if startup {
		fsio.Remove(StopPath(stateDir, session))
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
		if !strings.HasSuffix(name, fsio.JSONExt) || !strings.HasPrefix(name, config.OncePrefix) {
			continue
		}
		path := ospath.Join(stateDir, entry)
		if strings.HasPrefix(name, mine) {
			fsio.Remove(path)
			continue
		}
		if mtime, _, ok := ospath.Stat(path); ok && mtime.Before(cutoff) {
			fsio.Remove(path)
		}
	}
}
