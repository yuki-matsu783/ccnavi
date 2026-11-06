package directcand

import (
	"slices"
	"strings"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/tree"
)

// MaxEntries は記録の上限。
const MaxEntries = 50

// refreshWindow は、同じ候補が末尾にあってこれ以内に見たばかりなら書き直さない長さ。
const refreshWindow = 60 * time.Second

const (
	recordPrefix   = config.DirectPrefix
	unknownSession = "unknown"
	fieldFilePath  = "file_path"
	fieldNotebook  = "notebook_path"
)

// integrate が統合先に使う名前（受けない）。
var reservedNames = []string{"main", "master", "develop", "release"}

const releasePrefix = "release-"

// Entry は候補 1 つ。
type Entry struct {
	Name string
	Path string
	// Seen は最後に書き込み先として見た時刻、Checked は Stop で最後に調べた時刻（0 は未調査）。UNIX 秒。
	Seen    float64
	Checked float64
}

// row は記録の 1 項目。seen・checked の数は読んだ型（整数か小数か）のまま書き戻す。
type row struct {
	name, path    string
	seen, checked any
}

func (r row) entry() Entry {
	seen, _ := pyjson.Number(r.seen, pyjson.BoolsRejected)
	checked, _ := pyjson.Number(r.checked, pyjson.BoolsRejected)
	return Entry{r.name, r.path, seen, checked}
}

// IntegrableName は `integrate` が受ける名前か（ccnavi-git.sh の ig_name と同じ検査）。統合先の名前と、
// ccnavi_is_ident を通らない形は integrate が断るので、促さない。
func IntegrableName(name string) bool {
	if slices.Contains(reservedNames, name) || strings.HasPrefix(name, releasePrefix) {
		return false
	}
	if name == "" || strings.ContainsAny(name[:1], "-.") || strings.Contains(name, "..") || strings.ContainsAny(name, `/\`) {
		return false
	}
	if !asciiAlnum(rune(name[0])) {
		return false
	}
	for _, c := range name {
		if !asciiAlnum(c) && !strings.ContainsRune("._-", c) && c < 0x80 {
			return false
		}
	}
	return true
}

func asciiAlnum(c rune) bool {
	return c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9'
}

func pathOf(stateDir, session string) string {
	return fsio.RecordPath(stateDir, recordPrefix, fsio.SafeNameOr(session, unknownSession, fsio.SafeNameLimit), fsio.JSONExt)
}

// samePlace はリンクを解いて比べる。tree は解いたパスを返すが、hook のルートは解いていないことがある。
func samePlace(a, b string) bool { return key(ospath.Realpath(a)) == key(ospath.Realpath(b)) }

func key(s string) string { return pathcase.Fold(pyfmt.NFC(s)) }

// read は記録の項目（形の検査だけ済んだもの）。別のセッションの記録・読めない記録は空。
func read(stateDir, session string) []row {
	data, ok := fsio.ReadObject(pathOf(stateDir, session))
	if !ok || data.Value("session") != session {
		return nil
	}
	items, _ := data.Value("entries").([]any)
	var out []row
	for _, raw := range items {
		item, ok := raw.(*pyjson.Object)
		if !ok {
			continue
		}
		name, okName := item.Value("name").(string)
		path, okPath := item.Value("path").(string)
		seen := item.Value("seen")
		checked, has := item.Get("checked")
		if !has {
			checked = 0
		}
		if !okName || !okPath || !isNumber(seen) || !isNumber(checked) {
			continue
		}
		out = append(out, row{name, path, seen, checked})
	}
	return out
}

func isNumber(v any) bool {
	_, ok := pyjson.Number(v, pyjson.BoolsRejected)
	return ok
}

func save(stateDir, session string, rows []row) {
	slices.SortStableFunc(rows, func(a, b row) int {
		x, y := a.entry().Seen, b.entry().Seen
		switch {
		case x < y:
			return -1
		case x > y:
			return 1
		}
		return 0
	})
	if len(rows) > MaxEntries {
		rows = rows[len(rows)-MaxEntries:]
	}
	entries := make([]any, 0, len(rows))
	for _, r := range rows {
		entries = append(entries, pyjson.NewObject("name", r.name, "path", r.path, "seen", r.seen, "checked", r.checked))
	}
	fsio.WriteJSON(pathOf(stateDir, session), pyjson.NewObject("session", session, "entries", entries))
}

// Load はこのセッションの候補。名前か実パスが合わない項目は捨てる。
func Load(conf *config.Settings, root, session string) []Entry {
	if conf.State == "" || session == "" {
		return nil
	}
	var kept []Entry
	for _, r := range read(conf.State, session) {
		if IntegrableName(r.name) && samePlace(r.path, tree.WorktreePath(root, r.name)) {
			kept = append(kept, r.entry())
		}
	}
	return kept
}

func target(in hookio.Input) string {
	raw := in.Field(fieldFilePath)
	if raw == "" {
		raw = in.Field(fieldNotebook)
	}
	if raw == "" {
		return ""
	}
	if !ospath.IsAbs(raw) {
		base := in.Cwd
		if base == "" {
			base, _ = ospath.Getwd()
		}
		raw = ospath.Join(base, raw)
	}
	return ospath.Normpath(raw)
}

// Touch は、成功した書き込みの先が直接作業のワークツリーなら候補に加える。メインにもサブエージェントにも効く
// （サブエージェントも親のセッションの候補になる）。git は呼ばず、保存できなくても何も言わない。
func Touch(conf *config.Settings, root string, in hookio.Input, now time.Time) {
	if !slices.Contains(config.WriteTools, in.ToolName) || conf.State == "" || in.SessionID == "" {
		return
	}
	full := target(in)
	if full == "" {
		return
	}
	here, ok := tree.TreeOf(root, full, conf.Projects)
	if !ok || here.IsMain() || !samePlace(here.Root, tree.WorktreePath(root, here.Name)) || !IntegrableName(here.Name) {
		return
	}
	stamp := fsio.UnixSeconds(now)
	rows := read(conf.State, in.SessionID)
	for i, r := range rows {
		if key(r.name) != key(here.Name) {
			continue
		}
		if i == len(rows)-1 && stamp-r.entry().Seen <= refreshWindow.Seconds() {
			return
		}
		rows = slices.Delete(rows, i, i+1)
		break
	}
	rows = append(rows, row{here.Name, here.Root, stamp, 0})
	save(conf.State, in.SessionID, rows)
}

// Update は Stop が調べた候補の Checked を進め、dropped の候補を除いて保存する。書けなくても何も言わない。
func Update(conf *config.Settings, session string, checked map[string]float64, dropped map[string]bool) {
	if conf.State == "" || session == "" || (len(checked) == 0 && len(dropped) == 0) {
		return
	}
	var keep []row
	for _, r := range read(conf.State, session) {
		if dropped[r.name] {
			continue
		}
		if at, ok := checked[r.name]; ok {
			r.checked = at
		}
		keep = append(keep, r)
	}
	save(conf.State, session, keep)
}
