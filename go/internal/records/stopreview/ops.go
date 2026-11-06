package stopreview

import (
	"slices"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"

	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
)

// approval-request の操作実績。実行前（PreToolUse）に呼び出しを登録し（Register）、ccnavi-ticket.sh が出口で
// どの経路を通っても書く結果ファイルを、実行後に tool_use_id・識別子・時刻の範囲で結び付ける（Bind）。
// 標準出力の識別行は、出力が切られると消えるので頼らない。バックグラウンドの実行などで結べなかったものは
// Stop で結び直し（Rebind）、それでも結べなければ未連携にする。

// Route は approval-request の経路。
type Route string

const (
	RouteSent         Route = "sent"          // 送った
	RouteUnchanged    Route = "unchanged"     // 変更なし
	RouteWriteOnly    Route = "write_only"    // 書くだけ（origin なし）
	RouteStopped      Route = "stopped"       // 止めた
	RouteFailedBefore Route = "failed_before" // 送る前に失敗（親の状態で拒否した回を含む）
	RouteFailedAfter  Route = "failed_after"  // push 後の後処理で失敗
	// RouteUnsent は todo/ へ書いたまま送っていない提案（操作実績ではなく、書いた記録から決める）。
	RouteUnsent Route = "unsent"
)

var routes = []Route{RouteSent, RouteUnchanged, RouteWriteOnly, RouteStopped, RouteFailedBefore, RouteFailedAfter}

// effective は提案の最終の有効版になる経路。変更なしは、前に送った版がそのまま有効のままになる。
func (r Route) effective() bool { return r == RouteSent || r == RouteUnchanged }

const (
	unlinked = "unlinked"
	// slack は時計の粗さ（sh は秒単位）と実行の前後の隙間（秒）。
	slack = 2.0
	// unlinkAfter は結び直しても見つからないまま、未連携にするまでの秒数。
	unlinkAfter = 30.0
	// refsKept は issue・MR の指定を、新しく足す前に残す数。
	refsKept = 19

	ticketScript   = "ccnavi-ticket.sh"
	approvalVerb   = "approval-request"
	checkFlag      = "--check"
	reasonFlag     = "--reason"
	proposalPrefix = "proposal:"
	versionLength  = 12
)

var shells = []string{"sh", "bash", "zsh", "dash", "ksh"}

var wrappers = []string{"env", "nohup", "time", "timeout", "sudo", "uv", "run", "nice", "command", "exec", "stdbuf"}

// File は送った版に含まれたファイル。SHA は書いた記録から作った未送信の版では空。
type File struct {
	SHA  string
	Path string
}

// Op は approval-request の 1 回の操作実績。時刻は UNIX 秒。
type Op struct {
	Ident     string
	Route     Route
	Commit    string
	Files     []File
	ToolUseID string
	Started   float64
	Ended     float64
}

// fold はパスを比べるキー。区切りは "/" にそろえ、大文字小文字は区別しない。
func fold(path string) string {
	return strings.ReplaceAll(pathcase.Fold(path), `\`, "/")
}

// runs はスクリプトの前に並ぶ語が、実行の前置き（シェル・VAR=値・env・timeout・オプション・数）だけか。
func runs(before []string) bool {
	for _, word := range before {
		name := ospath.Basename(fold(word))
		switch {
		case slices.Contains(shells, name), slices.Contains(wrappers, name), strings.HasPrefix(word, "-"),
			digits(strings.TrimLeft(word, "-")):
			continue
		}
		if head, _, ok := strings.Cut(word, "="); ok && !strings.Contains(head, "/") {
			continue
		}
		if digits(strings.Replace(word, ".", "", 1)) {
			continue
		}
		return false
	}
	return true
}

// digits は Python の str.isdigit。
func digits(s string) bool {
	if s == "" {
		return false
	}
	for _, r := range s {
		if !unicode.IsDigit(r) {
			return false
		}
	}
	return true
}

// ProposeIdent はコマンドの語から、実行される `ccnavi-ticket.sh approval-request <識別子>` の識別子を取る。
// 無ければ空。識別子は `--` と `--reason <値>` を読み飛ばした最初の語（sh の ticket_id と同じ読み方）。
// `--check` は確かめるだけで提案を送らないので、操作実績に載せない。
func ProposeIdent(words []string) string {
	for at, word := range words {
		if ospath.Basename(fold(word)) != ticketScript || !runs(words[:at]) {
			continue // `echo …` や `grep …` の引数の中は数えない
		}
		rest := words[at+1:]
		if len(rest) == 0 || rest[0] != approvalVerb {
			continue
		}
		if slices.Contains(rest[1:], checkFlag) {
			return ""
		}
		skip := false
		for _, arg := range rest[1:] {
			switch {
			case skip:
				skip = false
			case arg == reasonFlag:
				skip = true
			case strings.HasPrefix(arg, "-") && len(arg) > 1:
			default:
				return arg
			}
		}
		return ""
	}
	return ""
}

// call は 1 つの Bash に approval-request が複数あっても、それぞれ別の呼び出しとして扱うためのキー。
func call(toolUseID, ident string) string { return toolUseID + "~" + pathcase.Fold(ident) }

func prePath(state, toolUseID, ident string) string {
	return ospath.Join(state, prePrefix+safe(call(toolUseID, ident))+jsonSuffix)
}

func opPath(state, toolUseID, ident string) string {
	return ospath.Join(state, opPrefix+safe(call(toolUseID, ident))+jsonSuffix)
}

// StartTurn は通知でない発言のとき、ターンの開始時刻を書く。
func StartTurn(state, session string, now time.Time) {
	if state != "" && session != "" {
		fsio.WriteJSON(sessionFile(state, TurnStartPrefix, session), pyjson.NewObject("t", fsio.UnixSeconds(now)))
	}
}

// TurnStart はターンの開始時刻。無ければ UNIX 秒の 0（セッションの最初から数える）。
func TurnStart(state, session string) time.Time {
	if state == "" || session == "" {
		return time.Unix(0, 0)
	}
	return fsio.FromUnixSeconds(number(readObject(sessionFile(state, TurnStartPrefix, session)).Value("t"), 0))
}

// Register は実行前に、approval-request の呼び出しを登録する。
func Register(state, session, toolUseID, ident string, now time.Time) {
	if state == "" || session == "" || toolUseID == "" || ident == "" {
		return
	}
	fsio.WriteJSON(prePath(state, toolUseID, ident), pyjson.NewObject(
		"tool_use_id", toolUseID, "session", session, "ident", ident, "t0", fsio.UnixSeconds(now), "bound", ""))
}

// readResult は結果ファイル（key=value の行）を読む。読めない・経路が分からない・識別子が無ければ nil。
func readResult(path string) *pyjson.Object {
	text, ok := fsio.ReadText(path, fsio.Replace)
	if !ok {
		return nil
	}
	result := pyjson.NewObject("files", nil)
	files := []any{}
	for _, line := range ospath.Splitlines(text) {
		key, value, ok := strings.Cut(line, "=")
		if !ok {
			continue
		}
		if key == "file" {
			if sha, name, _ := strings.Cut(value, " "); sha != "" && name != "" {
				files = append(files, pyjson.NewObject("sha", sha, "path", name))
			}
			continue
		}
		result.Set(key, value)
	}
	result.Set("files", files)
	if !slices.Contains(routes, Route(result.String("route"))) || result.String("id") == "" {
		return nil
	}
	for _, key := range []string{"started", "ended", "rc"} {
		value := 0.0
		if text, ok := result.Value(key).(string); ok {
			if parsed, err := pyfmt.ParseFloat(text); err == nil {
				value = parsed
			}
		}
		result.Set(key, value)
	}
	result.Set("file", path)
	return result
}

func results(state string) []*pyjson.Object {
	dir := ospath.Join(state, ResultDir)
	var out []*pyjson.Object
	for _, name := range names(dir) {
		if strings.HasSuffix(name, resultSuffix) {
			if r := readResult(ospath.Join(dir, name)); r != nil {
				out = append(out, r)
			}
		}
	}
	return out
}

func boundFiles(state string) map[string]bool {
	bound := map[string]bool{}
	for _, op := range records(state, opPrefix) {
		if f := op.String("file"); f != "" {
			bound[f] = true
		}
	}
	return bound
}

// Bind は実行後に、結果ファイルを tool_use_id の呼び出しへ結び付ける。最後に結べた操作を返す。
// 1 つの Bash に approval-request が複数あれば、登録は識別子ごとにあり、それぞれ結ぶ。
func Bind(state, toolUseID string, now time.Time) (Op, bool) {
	var last Op
	found := false
	if state == "" || toolUseID == "" {
		return last, false
	}
	for _, pre := range records(state, prePrefix) {
		if pre.Value("tool_use_id") != toolUseID {
			continue
		}
		if op, ok := bindOne(state, pre, fsio.UnixSeconds(now)); ok {
			last, found = op, true
		}
	}
	return last, found
}

// bindOne は 1 件の登録に結果ファイルを結ぶ。識別子が合い、開始が登録の時刻より後で、終了が今より前
// （どちらも slack を許す）、まだどの呼び出しにも結ばれていないもののうち、終了の最も新しいもの。
func bindOne(state string, pre *pyjson.Object, now float64) (Op, bool) {
	if pyfmt.Truthy(pre.Value("bound")) {
		return Op{}, false
	}
	taken := boundFiles(state)
	lower, upper := number(pre.Value("t0"), 0)-slack, now+slack
	var best *pyjson.Object
	for _, r := range results(state) {
		if r.Value("id") != pre.Value("ident") || taken[r.String("file")] ||
			number(r.Value("started"), 0) < lower || number(r.Value("ended"), 0) > upper {
			continue
		}
		if best == nil || number(r.Value("ended"), 0) > number(best.Value("ended"), 0) {
			best = r
		}
	}
	if best == nil {
		return Op{}, false
	}
	toolUseID, ident := pre.String("tool_use_id"), pre.String("ident")
	op := best.With("tool_use_id", toolUseID, "session", pre.Value("session"), "ident", ident)
	fsio.WriteJSON(opPath(state, toolUseID, ident), op)
	pre.Set("bound", best.String("file"))
	fsio.WriteJSON(prePath(state, toolUseID, ident), pre)
	return parseOp(op), true
}

// Rebind は結び付いていない登録を、時刻の範囲で結び直す。見つからないまま古くなったものは未連携にする。
func Rebind(state, session string, now time.Time) {
	when := fsio.UnixSeconds(now)
	for _, name := range names(state) {
		if !strings.HasPrefix(name, prePrefix) || !strings.HasSuffix(name, jsonSuffix) {
			continue
		}
		path := ospath.Join(state, name)
		pre := readObject(path)
		if pre.Len() == 0 || pre.Value("session") != session || pyfmt.Truthy(pre.Value("bound")) {
			continue
		}
		if _, ok := bindOne(state, pre, when); ok {
			continue
		}
		if when-number(pre.Value("t0"), 0) > unlinkAfter {
			pre.Set("bound", unlinked)
			fsio.WriteJSON(path, pre)
		}
	}
}

func parseOp(obj *pyjson.Object) Op {
	return Op{
		Ident:     obj.String("ident"),
		Route:     Route(obj.String("route")),
		Commit:    obj.String("commit"),
		Files:     parseFiles(obj.Value("files")),
		ToolUseID: obj.String("tool_use_id"),
		Started:   number(obj.Value("started"), 0),
		Ended:     number(obj.Value("ended"), 0),
	}
}

func parseFiles(v any) []File {
	items, _ := v.([]any)
	var out []File
	for _, item := range items {
		if x, ok := item.(*pyjson.Object); ok {
			out = append(out, File{SHA: x.String("sha"), Path: pyfmt.Str(orEmpty(x.Value("path")))})
		}
	}
	return out
}

func orEmpty(v any) any {
	if v == nil {
		return ""
	}
	return v
}

func filesList(files []File) []any {
	out := make([]any, 0, len(files))
	for _, f := range files {
		if f.SHA != "" {
			out = append(out, pyjson.NewObject("sha", f.SHA, "path", f.Path))
		} else {
			out = append(out, pyjson.NewObject("path", f.Path))
		}
	}
	return out
}

// TurnOps はこのターン（since 以降に始まった呼び出し）の操作実績。終了の古い順。
func TurnOps(state, session string, since time.Time) []Op {
	floor := fsio.UnixSeconds(since) - 1
	var ops []Op
	for _, obj := range records(state, opPrefix) {
		if obj.Value("session") == session && number(obj.Value("started"), 0) >= floor {
			ops = append(ops, parseOp(obj))
		}
	}
	slices.SortStableFunc(ops, func(a, b Op) int {
		if a.Ended != b.Ended {
			if a.Ended < b.Ended {
				return -1
			}
			return 1
		}
		return strings.Compare(a.ToolUseID, b.ToolUseID)
	})
	return ops
}

// FinalVersions は提案ごとの最終の有効版を、識別子が最初に出た順に返す。有効なのは送った版で、変更なしは
// 直前の有効版のままなので版を動かさない。有効な版が 1 つも無い提案は、最後の操作（送れなかった理由）を返す。
func FinalVersions(ops []Op) []Op {
	var order []string
	latest := map[string]Op{}
	for _, op := range ops {
		prev, seen := latest[op.Ident]
		if op.Route == RouteUnchanged && seen && prev.Route.effective() {
			continue
		}
		if op.Route.effective() || !seen || !prev.Route.effective() {
			if !seen {
				order = append(order, op.Ident)
			}
			latest[op.Ident] = op
		}
	}
	out := make([]Op, 0, len(order))
	for _, ident := range order {
		out = append(out, latest[ident])
	}
	return out
}

// TargetKey は判定の対象キー。識別子と版（送った先頭、無ければ経路）から決める。
func TargetKey(op Op) string {
	version := op.Commit
	if version == "" {
		version = string(op.Route)
	}
	version, _ = clip(version, versionLength)
	return proposalPrefix + op.Ident + ":" + version
}

// ---- 未送信の提案

// Write は提案を todo/ へ書いた（Write・Edit）記録。At は UNIX 秒。
type Write struct {
	Ident string
	Path  string
	At    float64
}

// WrittenIdent は書き込んだパスが `<提案の置き場>/todo/<識別子>.md` なら識別子、違えば空。
// 大文字小文字は区別しない。置き場は設定の相対パス（既定 wip/proposals）。
func WrittenIdent(filePath, ticketsDir string) string {
	path := fold(filePath)
	marker := "/" + strings.Trim(fold(ticketsDir), "/") + "/todo/"
	at := strings.LastIndex(path, marker)
	original := []rune(strings.ReplaceAll(filePath, `\`, "/"))
	if at < 0 || utf8.RuneCountInString(path) != len(original) {
		return ""
	}
	start := utf8.RuneCountInString(path[:at]) + utf8.RuneCountInString(marker)
	name := path[at+len(marker):]
	if strings.Contains(name, "/") || !strings.HasSuffix(name, ".md") || utf8.RuneCountInString(name) <= 3 {
		return ""
	}
	return string(original[start : len(original)-3])
}

func writePath(state, session, ident string) string {
	return ospath.Join(state, WritePrefix+safe(session)+"-"+safe(pathcase.Fold(ident))+jsonSuffix)
}

// NoteWritten は提案を todo/ へ書いたことを、書いた時刻つきで残す。
func NoteWritten(state, session, ident, path string, now time.Time) {
	if state != "" && session != "" && ident != "" {
		fsio.WriteJSON(writePath(state, session, ident), pyjson.NewObject(
			"session", session, "ident", ident, "path", path, "t", fsio.UnixSeconds(now)))
	}
}

// TurnWrites はこのターンに書いた提案。書いた時刻の古い順。
func TurnWrites(state, session string, since time.Time) []Write {
	floor := fsio.UnixSeconds(since)
	var found []Write
	for _, obj := range records(state, WritePrefix+safe(session)+"-") {
		at := number(obj.Value("t"), 0)
		if at >= floor {
			found = append(found, Write{Ident: obj.String("ident"), Path: obj.String("path"), At: at})
		}
	}
	slices.SortStableFunc(found, func(a, b Write) int {
		if a.At != b.At {
			if a.At < b.At {
				return -1
			}
			return 1
		}
		return strings.Compare(a.Ident, b.Ident)
	})
	return found
}

// UnsentWrites は書いたのに、書いた後に送った操作が無い提案。送ったかは、送った操作の files に
// `todo/<識別子>.md` があり、その終了が書いた時刻より後（slack を許す）かで決める。親の approval-request は
// 子の提案も一緒に送るので、識別子では決めない。
func UnsentWrites(writes []Write, ops []Op) []Write {
	sent := map[string]float64{}
	for _, op := range ops {
		if op.Route != RouteSent {
			continue
		}
		for _, f := range op.Files {
			tail := fold(f.Path)
			if i := strings.LastIndex(tail, "/todo/"); i >= 0 {
				tail = tail[i+len("/todo/"):]
			}
			if prev, ok := sent[tail]; !ok || op.Ended > prev {
				sent[tail] = max(op.Ended, 0)
			}
		}
	}
	var unsent []Write
	for _, w := range writes {
		at, ok := sent[fold(w.Ident+".md")]
		if !ok {
			at = -1
		}
		if at < w.At-slack {
			unsent = append(unsent, w)
		}
	}
	return unsent
}

// UnsentKey は未送信の提案の対象キー。
func UnsentKey(ident string) string { return proposalPrefix + ident + ":" + string(RouteUnsent) }

// ---- issue・MR の指定

// NoteRefs は依頼に issue・MR の指定があれば、ラベルを時刻つきで足す（セッションの間は残す）。
func NoteRefs(state, session string, labels []string, now time.Time) {
	if state == "" || session == "" || len(labels) == 0 {
		return
	}
	path := sessionFile(state, RefsPrefix, session)
	row := readObject(path)
	prior, _ := row.Value("refs").([]any)
	var kept []any
	for _, r := range prior {
		obj, _ := r.(*pyjson.Object)
		if label, ok := obj.Value("label").(string); ok && slices.Contains(labels, label) {
			continue
		}
		kept = append(kept, r)
	}
	kept = kept[max(len(kept)-refsKept, 0):]
	for _, label := range labels {
		kept = append(kept, pyjson.NewObject("label", label, "t", fsio.UnixSeconds(now)))
	}
	fsio.WriteJSON(path, pyjson.NewObject("refs", kept))
}

// SessionRefs はこのセッションで指定された issue・MR のラベル（古い順）。
func SessionRefs(state, session string) []string {
	if state == "" || session == "" {
		return nil
	}
	refs, _ := readObject(sessionFile(state, RefsPrefix, session)).Value("refs").([]any)
	var out []string
	for _, r := range refs {
		obj, _ := r.(*pyjson.Object)
		if label := obj.Value("label"); pyfmt.Truthy(label) {
			out = append(out, pyfmt.Str(label))
		}
	}
	return out
}
