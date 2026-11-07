package outcome

import (
	"fmt"
	"slices"
	"strconv"
	"strings"
	"time"

	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
)

// Kind はサブエージェントの終わり方。
type Kind string

const (
	// OK は範囲外の変更が無い。
	OK Kind = "ok"
	// Fixed は差し戻した後に直った。
	Fixed Kind = "fixed"
	// Outside は範囲外の変更が残っている。
	Outside Kind = "outside"
	// Unchecked は読めない・内部エラーで確かめられなかった。
	Unchecked Kind = "unchecked"
	// Unattributed は担当の子が分からない（親のツリーから起動された）。
	Unattributed Kind = "unattributed"
)

var kinds = []Kind{OK, Fixed, Outside, Unchecked, Unattributed}

// nudged は Stop が促す終わり方。
var nudged = []Kind{Outside, Unchecked}

const (
	prefix = "subagent-"
	suffix = ".outcome"
	// unknownName は空のセッション・相手の代わりに名前に入れる語。
	unknownName = "unknown"
	// shownPaths は 1 行に並べるパスの数。
	shownPaths = 5
	// noticeCode は担当の子が分からなかった体の知らせの印。
	noticeCode = "SUBAGENT_UNATTRIBUTED"
)

// 記録の JSON の欄。
const (
	keyID       = "id"
	keySession  = "session"
	keyAgentID  = "agent_id"
	keyChild    = "child"
	keyOutcome  = "outcome"
	keyBounces  = "bounces"
	keyPaths    = "paths"
	keyFinal    = "final"
	keyNotified = "notified"
	keyNudged   = "nudged"
	keyAt       = "at"
)

// Entry は書く結果。Provisional は差し戻して続けさせた回（次の SubagentStop で確定する）。
type Entry struct {
	Kind        Kind
	Child       string
	Bounces     int
	Paths       []string
	Provisional bool
}

// Outcome は読んだ結果。
type Outcome struct {
	AgentID  string
	Child    string
	Kind     Kind
	Bounces  int
	Paths    []string
	Final    bool
	Notified bool
	Nudged   bool

	id   string
	at   float64
	file string
}

func path(stateDir, session, who string) string {
	return ospath.Join(stateDir, prefix+fsio.SafeNameOr(session, unknownName, fsio.SafeNameLimit)+"-"+
		fsio.SafeNameOr(who, unknownName, fsio.SafeNameLimit)+suffix)
}

// Write は結果を書く。書けなければ理由（呼び手は言うだけで止まらない）。stateDir が空なら何もしない。
func Write(stateDir, session, who string, e Entry) string {
	if stateDir == "" {
		return ""
	}
	if !slices.Contains(kinds, e.Kind) {
		return "知らない結果: " + string(e.Kind)
	}
	now := time.Now()
	paths := make([]any, len(e.Paths))
	for i, p := range e.Paths {
		paths[i] = p
	}
	data := pyjson.NewObject(
		keyID, session+":"+who+":"+strconv.FormatInt(now.UnixMilli(), 10),
		keySession, session,
		keyAgentID, who,
		keyChild, e.Child,
		keyOutcome, string(e.Kind),
		keyBounces, e.Bounces,
		keyPaths, paths,
		keyFinal, !e.Provisional,
		keyNotified, false,
		keyNudged, false,
		keyAt, fsio.UnixSeconds(now),
	)
	return fsio.WriteJSON(path(stateDir, session, who), data)
}

// read はこのセッションの結果。違うセッション・壊れたもの・型の合わないもの（手で直した記録など）は nil。
func read(file, session string) *Outcome {
	text, ok := fsio.ReadText(file, fsio.Replace)
	if !ok {
		return nil
	}
	value, err := pyjson.Loads(text)
	if err != nil {
		return nil
	}
	data, ok := value.(*pyjson.Object)
	if !ok || !isText(data.Value(keySession), session) {
		return nil
	}
	kind := Kind(str(data.Value(keyOutcome)))
	if !slices.Contains(kinds, kind) {
		return nil
	}
	bounces, ok := intOf(data.Value(keyBounces))
	if !ok {
		return nil
	}
	paths, ok := strs(data.Value(keyPaths))
	if !ok {
		return nil
	}
	at, ok := floatOf(data.Value(keyAt))
	if !ok {
		return nil
	}
	final := true
	if v, has := data.Get(keyFinal); has {
		final = pyfmt.Truthy(v)
	}
	return &Outcome{
		AgentID:  str(data.Value(keyAgentID)),
		Child:    str(data.Value(keyChild)),
		Kind:     kind,
		Bounces:  bounces,
		Paths:    paths,
		Final:    final,
		Notified: pyfmt.Truthy(data.Value(keyNotified)),
		Nudged:   pyfmt.Truthy(data.Value(keyNudged)),
		id:       str(data.Value(keyID)),
		at:       at,
		file:     file,
	}
}

// isText は Python の v == text（文字列のほかは等しくない）。
func isText(v any, text string) bool {
	s, ok := v.(string)
	return ok && s == text
}

// str は Python の str(v or "")。
func str(v any) string {
	if !pyfmt.Truthy(v) {
		return ""
	}
	return pyfmt.Str(v)
}

// intOf は Python の int(v or 0)。
func intOf(v any) (int, bool) {
	if !pyfmt.Truthy(v) {
		return 0, true
	}
	return pyfmt.IntOf(v)
}

// floatOf は Python の float(v or 0)。
func floatOf(v any) (float64, bool) {
	if !pyfmt.Truthy(v) {
		return 0, true
	}
	if s, ok := v.(string); ok {
		f, err := pyfmt.ParseFloat(s)
		return f, err == nil
	}
	return pyjson.Number(v, pyjson.BoolsAsNumbers)
}

// strs は Python の [str(p) for p in v or []]。文字列は 1 字ずつ、マップはキーを並べる。
func strs(v any) ([]string, bool) {
	if !pyfmt.Truthy(v) {
		return []string{}, true
	}
	var out []string
	switch x := v.(type) {
	case []any:
		for _, p := range x {
			out = append(out, pyfmt.Str(p))
		}
	case string:
		for _, r := range x {
			out = append(out, string(r))
		}
	case *pyjson.Object:
		out = x.Keys()
	default:
		return nil, false
	}
	return out, true
}

// Of はこの相手の結果。無ければ nil。
func Of(stateDir, session, who string) *Outcome {
	if stateDir == "" || who == "" {
		return nil
	}
	return read(path(stateDir, session, who), session)
}

// all はこのセッションの確定した結果（古い順）。
func all(stateDir, session string) []*Outcome {
	if stateDir == "" {
		return nil
	}
	names, err := ospath.DiskListdir(stateDir)
	if err != nil {
		return nil
	}
	var found []*Outcome
	for _, name := range names {
		if !strings.HasPrefix(name, prefix) || !strings.HasSuffix(name, suffix) {
			continue
		}
		if one := read(ospath.Join(stateDir, name), session); one != nil && one.Final {
			found = append(found, one)
		}
	}
	slices.SortStableFunc(found, func(a, b *Outcome) int {
		switch {
		case a.at < b.at:
			return -1
		case a.at > b.at:
			return 1
		}
		return 0
	})
	return found
}

// mark は結果に印（notified・nudged）を付ける。保存できなければ理由。読み直したら別の結果（新しい終了で書き直された）
// なら付けずに空を返す。
func mark(one *Outcome, field string) string {
	data, ok := fsio.ReadObject(one.file)
	if !ok || !isText(data.Value(keyID), one.id) {
		return ""
	}
	data.Set(field, true)
	return fsio.WriteJSON(one.file, data)
}

// line は親に言う 1 行。
func line(one *Outcome) string {
	who := one.Child
	if who == "" {
		who = "（担当の子が分からない）"
	}
	shown := strings.Join(one.Paths[:min(len(one.Paths), shownPaths)], "、")
	if len(one.Paths) > shownPaths {
		shown += fmt.Sprintf(" ほか %d 件", len(one.Paths)-shownPaths)
	}
	switch one.Kind {
	case Outside:
		if one.Bounces == 0 {
			return "サブエージェント " + one.AgentID + " は、子 " + who + " の範囲外の変更を残したまま終わっている" +
				"（差し戻していない。dry-run か、差し戻しの対象外）: " + shown
		}
		return fmt.Sprintf("サブエージェント %s は、子 %s の範囲外の変更を差し戻された（%d 回）後も残したまま終わっている: %s",
			one.AgentID, who, one.Bounces, shown)
	case Unchecked:
		target := "子"
		if one.Child != "" {
			target = "子 " + one.Child
		}
		return "サブエージェント " + one.AgentID + " の終わりに、" + target + "の範囲を確かめられていない" +
			"（ワークツリーを読めないか、ccnavi の内部エラー）"
	}
	return "サブエージェント " + one.AgentID + " は親のツリーから起動されたため、担当の子が分からない。" +
		"範囲外の変更が見つかっているが差し戻していない: " + shown + "。" +
		"どの子に残っているかを確かめる（最終的には finish の範囲検査が止める）"
}

// Notices は、まだ言っていない確定した Unattributed の結果を、親に言う文にする。Outside と Unchecked は別の経路
// （差し戻しを無視した知らせと、確かめられなかった知らせ）が届ける。書き終えたら、返した結果を Confirm に渡す。
func Notices(stateDir, session string) (string, []*Outcome) {
	var todo []*Outcome
	var said []string
	for _, o := range all(stateDir, session) {
		if o.Kind == Unattributed && !o.Notified {
			todo = append(todo, o)
			said = append(said, "[ccnavi] "+noticeCode+": "+line(o))
		}
	}
	return strings.Join(said, "\n\n"), todo
}

// Confirm は書き終えた知らせに「通知済み」の印を付ける。付けられなければ理由（次の機会に出し直すだけ）。
func Confirm(done []*Outcome) string {
	failed := ""
	for _, one := range done {
		if why := mark(one, keyNotified); why != "" {
			failed = why
		}
	}
	return failed
}

// MarkNudged は、この相手の確定した結果を「促した」ことにする（別の経路で同じ内容を親に言ったとき）。
func MarkNudged(stateDir, session, who string) {
	if one := Of(stateDir, session, who); one != nil && one.Final && !one.Nudged {
		mark(one, keyNudged)
	}
}

// StopLines は Stop で促す結果（Outside・Unchecked で、まだ促していないもの）の 1 行ずつ。印を付けられたものだけを
// 返す（付けないと毎回止めてしまう）。前後の文は呼び手が文面ファイルから引く（NUDGE_SUBAGENT_OUTCOME）。
func StopLines(stateDir, session string) []string {
	var out []string
	for _, o := range all(stateDir, session) {
		if !slices.Contains(nudged, o.Kind) || o.Nudged {
			continue
		}
		if mark(o, keyNudged) == "" {
			out = append(out, "  - "+line(o))
		}
	}
	return out
}
