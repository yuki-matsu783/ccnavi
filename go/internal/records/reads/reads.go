package reads

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
	"ccnavi/internal/infra/shell"
	"ccnavi/internal/infra/tree"
)

// Prefix は記録の名前の頭（`reads-<セッション>.json`）。records/prune が同じ名前で消す。
const Prefix = "reads-"

const (
	// maxEntries は 1 セッションの記録の上限。
	maxEntries = 2000
	// maxPerCall は 1 回の呼び出しで足す件数の上限。1 回の大きな出力で記録が埋まらないようにする。
	maxPerCall = 300
	// maxLines は Bash・Grep の出力から読む行数の上限。hook の待ち時間を抑える。
	maxLines = 3000
	// maxFieldsPerLine は、行を空白で切って語ごとにパスを探す行の語数の上限。長い文の語を 1 つずつ確かめない。
	maxFieldsPerLine = 8

	unknownSession = "unknown"
	fieldFilePath  = "file_path"
	fieldNotebook  = "notebook_path"
	fieldCommand   = "command"
	fieldPath      = "path"
	fieldStdout    = "stdout"
	fieldFilenames = "filenames"
	fieldContent   = "content"
	dotGit         = ".git"
)

// Entry は記録の 1 件。Tools はツール名ごとの回数。
type Entry struct {
	Path  string
	Tools map[string]int
	Seen  float64
}

// Count はツールをまたいだ回数。
func (e Entry) Count() int {
	n := 0
	for _, c := range e.Tools {
		n += c
	}
	return n
}

// Item はチケットに載せる 1 件。Rel はツリーのルートからの相対（プロジェクトのツリーなら `<プロジェクト>:` を頭に付ける）。
type Item struct {
	Rel   string
	Tools map[string]int
	Count int
}

// Note は PostToolUse の 1 回で、コンテキストに乗ったファイルを記録に足す。git は呼ばず、保存できなくても何も言わない。
func Note(state, root string, in hookio.Input, now time.Time) {
	if state == "" || in.SessionID == "" || root == "" {
		return
	}
	found := touched(in)
	if len(found) == 0 {
		return
	}
	inside := insideOf(root)
	var paths []string
	seen := map[string]bool{}
	for _, p := range found {
		if len(paths) >= maxPerCall {
			break
		}
		if !inside(p) || underGit(p) || seen[key(p)] {
			continue
		}
		seen[key(p)] = true
		paths = append(paths, p)
	}
	if len(paths) == 0 {
		return
	}
	stamp := fsio.UnixSeconds(now)
	entries := read(state, in.SessionID)
	index := map[string]int{}
	for i, e := range entries {
		index[key(e.Path)] = i
	}
	for _, p := range paths {
		i, ok := index[key(p)]
		if !ok {
			entries = append(entries, Entry{Path: p, Tools: map[string]int{}})
			i = len(entries) - 1
			index[key(p)] = i
		}
		entries[i].Tools[in.ToolName]++
		entries[i].Seen = stamp
	}
	save(state, in.SessionID, entries)
}

// Load はこのセッションの記録（読めなければ空）。
func Load(state, session string) []Entry {
	if state == "" || session == "" {
		return nil
	}
	return read(state, session)
}

// Sessions は、dir の下のファイルを記録しているセッション（最後に見た時刻の古い順）。1 つのチケットを複数のセッションで
// 進めたときに、そのチケットのワークツリーで作業したセッションを集めるのに使う。
func Sessions(state, dir string) []string {
	names, err := fsio.Listdir(state)
	if err != nil {
		return nil
	}
	inside := insideOf(dir)
	type found struct {
		session string
		last    float64
	}
	var out []found
	for _, name := range names {
		if !strings.HasPrefix(name, Prefix) || !strings.HasSuffix(name, fsio.JSONExt) {
			continue
		}
		data, ok := fsio.ReadObject(ospath.Join(state, name))
		if !ok {
			continue
		}
		session, _ := data.Value("session").(string)
		hit, last := false, 0.0
		for _, e := range read(state, session) {
			hit = hit || inside(e.Path)
			last = max(last, e.Seen)
		}
		if hit {
			out = append(out, found{session, last})
		}
	}
	slices.SortStableFunc(out, func(a, b found) int {
		switch {
		case a.last < b.last:
			return -1
		case a.last > b.last:
			return 1
		}
		return strings.Compare(a.session, b.session)
	})
	sessions := make([]string, 0, len(out))
	for _, f := range out {
		sessions = append(sessions, f.session)
	}
	return sessions
}

// Listed はこのセッションの記録を、チケットに載せる形にする。ツリーをまたいだ同じファイルは 1 件にまとめ、回数の多い順
// （同じならパスの順）に並べる。どのツリーにも入らないものは載せない。
func Listed(state, session, root, projects string) []Item {
	var out []Item
	index := map[string]int{}
	for _, e := range Load(state, session) {
		here, ok := tree.TreeOf(root, e.Path, projects)
		if !ok {
			continue
		}
		rel := tree.Relative(here, e.Path)
		if rel == "" || rel == "." || strings.HasPrefix(rel, "..") {
			continue
		}
		if here.Project != "" {
			rel = here.Project + ":" + rel
		}
		i, ok := index[key(rel)]
		if !ok {
			out = append(out, Item{Rel: rel, Tools: map[string]int{}})
			i = len(out) - 1
			index[key(rel)] = i
		}
		for tool, n := range e.Tools {
			out[i].Tools[tool] += n
			out[i].Count += n
		}
	}
	slices.SortStableFunc(out, func(a, b Item) int {
		if a.Count != b.Count {
			return b.Count - a.Count
		}
		return strings.Compare(a.Rel, b.Rel)
	})
	return out
}

// touched は 1 回の呼び出しでコンテキストに乗ったファイルの候補（絶対パス）。Bash の分は実在するファイルだけ。
func touched(in hookio.Input) []string {
	base := in.Cwd
	if base == "" {
		base, _ = ospath.Getwd()
	}
	switch in.ToolName {
	case config.ToolRead, config.ToolWrite, config.ToolEdit, config.ToolMultiEdit, config.ToolNotebookEdit:
		raw := in.Field(fieldFilePath)
		if raw == "" {
			raw = in.Field(fieldNotebook)
		}
		if raw == "" {
			return nil
		}
		return []string{absolute(base, raw)}
	case config.ToolGrep, config.ToolGlob:
		var out []string
		if p := in.Field(fieldPath); p != "" {
			if full := absolute(base, p); ospath.IsFile(full) {
				out = append(out, full)
			}
		}
		if in.ToolResponse != nil {
			names, _ := in.ToolResponse.Value(fieldFilenames).([]any)
			for _, n := range names {
				if s, ok := n.(string); ok && s != "" {
					out = append(out, absolute(base, s))
				}
			}
			out = append(out, fromOutput(base, in.ToolResponse.String(fieldContent))...)
		}
		return out
	case config.ToolBash:
		out := fromCommand(base, in.Field(fieldCommand))
		if in.ToolResponse != nil {
			out = append(out, fromOutput(base, in.ToolResponse.String(fieldStdout))...)
		}
		return out
	}
	return nil
}

// fromCommand はコマンドの語のうち、実在するファイルを指すもの。`cd <ディレクトリ>` のあとの語はその場所から読む。
// 字面の推測なので、変数・置換・ヒアドキュメントの中は追わない。
func fromCommand(base, command string) []string {
	if command == "" {
		return nil
	}
	words, err := shell.Split(command)
	if err != nil {
		words = ospath.Fields(command)
	}
	var out []string
	for i := 0; i < len(words); i++ {
		w := words[i]
		if w == "cd" && i+1 < len(words) {
			if dir := absolute(base, words[i+1]); ospath.IsDir(dir) {
				base = dir
			}
			i++
			continue
		}
		if full, ok := fileOf(base, wordPath(w)); ok {
			out = append(out, full)
		}
	}
	return out
}

// wordPath は語からパスらしい部分を取り出す。`--file=x` の値、`x:12`（grep・sed の位置）の頭。
func wordPath(w string) string {
	if strings.HasPrefix(w, "-") {
		if _, value, ok := strings.Cut(w, "="); ok {
			return value
		}
		return ""
	}
	return w
}

// fromOutput は出力の行から、実在するファイルを指すもの。行全体（find・ls -1）、最初の `:` の前（grep -n・rg）、
// 語の少ない行の各語を試す。
func fromOutput(base, text string) []string {
	if text == "" {
		return nil
	}
	lines := ospath.Splitlines(text)
	if len(lines) > maxLines {
		lines = lines[:maxLines]
	}
	var out []string
	for _, line := range lines {
		line = ospath.Strip(line)
		if line == "" {
			continue
		}
		candidates := []string{line}
		if head, _, ok := strings.Cut(line, ":"); ok {
			candidates = append(candidates, head)
		}
		if words := ospath.Fields(line); len(words) > 1 && len(words) <= maxFieldsPerLine {
			candidates = append(candidates, words...)
		}
		for _, c := range candidates {
			if full, ok := fileOf(base, c); ok {
				out = append(out, full)
				break
			}
		}
	}
	return out
}

// fileOf は語がファイルを指すなら絶対パス。語の端の引用符・句読点は外す。ディレクトリは載せない。
func fileOf(base, word string) (string, bool) {
	word = strings.Trim(word, "'\"`,;()<>[]{}")
	word = strings.TrimSuffix(word, ":")
	if word == "" || word == "." || word == ".." || strings.ContainsAny(word, "*?$|&\n") {
		return "", false
	}
	full := absolute(base, word)
	if !ospath.IsFile(full) {
		return "", false
	}
	return full, true
}

func absolute(base, raw string) string {
	if !ospath.IsAbs(raw) {
		raw = ospath.Join(base, raw)
	}
	return ospath.Normpath(raw)
}

// insideOf はワークスペースルートの中かを答える関数。hook のルートはリンクを解いていないことがあるので、解いた形とも比べる。
func insideOf(root string) func(string) bool {
	var bases []string
	for _, r := range []string{ospath.Normpath(root), ospath.Realpath(root)} {
		if b := key(r); !slices.Contains(bases, b) {
			bases = append(bases, b)
		}
	}
	return func(full string) bool {
		k := key(full)
		for _, b := range bases {
			if strings.HasPrefix(k, strings.TrimRight(b, ospath.Sep)+ospath.Sep) {
				return true
			}
		}
		return false
	}
}

func underGit(full string) bool {
	return slices.ContainsFunc(strings.Split(ospath.Slashed(full), "/"), func(part string) bool {
		return pathcase.Fold(part) == dotGit
	})
}

func key(s string) string { return pathcase.Fold(pyfmt.NFC(s)) }

func pathOf(state, session string) string {
	return fsio.RecordPath(state, Prefix, fsio.SafeNameOr(session, unknownSession, fsio.SafeNameLimit), fsio.JSONExt)
}

// read は記録の項目（形の検査だけ済んだもの）。別のセッションの記録・読めない記録は空。
func read(state, session string) []Entry {
	data, ok := fsio.ReadObject(pathOf(state, session))
	if !ok || data.Value("session") != session {
		return nil
	}
	items, _ := data.Value("entries").([]any)
	var out []Entry
	for _, raw := range items {
		item, ok := raw.(*pyjson.Object)
		if !ok {
			continue
		}
		path, okPath := item.Value("path").(string)
		seen, okSeen := pyjson.Number(item.Value("seen"), pyjson.BoolsRejected)
		counts, okTools := item.Value("tools").(*pyjson.Object)
		if !okPath || path == "" || !okSeen || !okTools {
			continue
		}
		tools := map[string]int{}
		for _, name := range counts.Keys() {
			if n := pyjson.PositiveInt(counts.Value(name)); n > 0 {
				tools[name] = n
			}
		}
		out = append(out, Entry{Path: path, Tools: tools, Seen: seen})
	}
	return out
}

func save(state, session string, entries []Entry) {
	slices.SortStableFunc(entries, func(a, b Entry) int {
		switch {
		case a.Seen < b.Seen:
			return -1
		case a.Seen > b.Seen:
			return 1
		}
		return 0
	})
	if len(entries) > maxEntries {
		entries = entries[len(entries)-maxEntries:]
	}
	rows := make([]any, 0, len(entries))
	for _, e := range entries {
		names := make([]string, 0, len(e.Tools))
		for name := range e.Tools {
			names = append(names, name)
		}
		slices.Sort(names)
		counts := pyjson.NewObject()
		for _, name := range names {
			counts.Set(name, e.Tools[name])
		}
		rows = append(rows, pyjson.NewObject("path", e.Path, "tools", counts, "seen", e.Seen))
	}
	fsio.WriteJSON(pathOf(state, session), pyjson.NewObject("session", session, "entries", rows))
}
