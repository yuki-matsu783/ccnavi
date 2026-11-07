package turngate

import (
	"errors"
	"fmt"
	"io/fs"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"ccnavi/internal/infra/digest"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/pyre"
	"ccnavi/internal/infra/shell"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/records/turn"
)

const (
	// Code は拒否コード。宣言ごとには分けない。どの宣言で止めたかは記録の rules（RuleID）と文面が言う。
	Code       = "DENY_NEEDS_USER_TURN"
	RulePrefix = "builtin:turn-gate:"
	// Prefix と Suffix は記録のファイル名の頭と尻。records/prune は Prefix で始まる名前をセッションの記録と読む。
	Prefix = "turngate-"
	Suffix = fsio.JSONExt
)

// keyGroup はキーの名前つきグループ。wildcard は値に解けないキーで、どのキーとも一致する。
const (
	keyGroup = "key"
	wildcard = "*"
)

var (
	safeKey = regexp.MustCompile(`^[A-Za-z0-9._-]+$`)
	gateID  = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]*$`)
)

// 名前に入れる長さの上限（字）と、文面に載せるキーの長さの上限（識別子の長さの上限と同じ）。
const (
	sessionLimit = 64
	partLimit    = 40
	digestLen    = 10
	shownLimit   = 64
)

// stateMessage は記録を読み書きできないときの文の識別子。
const stateMessage = "DENY_NEEDS_USER_TURN__STATE"

// ErrBadGate は宣言を組み立てられない（条件・キーの有無の食い違い・id）。
var ErrBadGate = errors.New("ターン分離のゲートの宣言が不正")

func badGate(format string, args ...any) error {
	return fmt.Errorf("%w: %s", ErrBadGate, fmt.Sprintf(format, args...))
}

// Cond は A か B の条件。Regex か Glob のどちらか 1 つ。But は regex の並びで、同じコマンドにどれかがあればこの条件に
// 当たらない。
type Cond struct {
	Regex string
	Glob  string
	But   []string
}

// Gate は宣言 1 件。「First を打ったら、ユーザの発言が 1 回はさまるまで Second を打てない」。
//
// 文は文面ファイルの Message（識別子）が持つ。4 つの段のうち、段 1（what）と次の一手（next）は same_command・
// no_user_turn で場合分けし、FirstName・SecondName・宣言の id とキー（where）・key を埋める。別の組を足すときは、
// 文面ファイルにも 1 件足す。
type Gate struct {
	ID         string
	First      Cond
	Second     Cond
	FirstName  string
	SecondName string
	Message    string
	// KeyName は次の一手の `{key}` に、キーが解けなかったとき入れる仮の値。
	KeyName string
	// AskIfAttended は付き添いのあるセッションで、発言を待つ代わりに B を確認（ask）に出す。
	AskIfAttended bool
	// AskMessage は確認の文の識別子。AskIfAttended のときだけ使う。
	AskMessage string
}

// Reason は止めた理由。
type Reason string

const (
	SameCommand Reason = "same-command"
	NoUserTurn  Reason = "no-user-turn"
	Unreadable  Reason = "unreadable"
	Broken      Reason = "broken"
	Unwritable  Reason = "unwritable"
	Ask         Reason = "ask"
)

// Stop は B を止める結論。Key は文面に出す B のキー（キーなしの宣言・そのまま打てない形では空）。
type Stop struct {
	Gate   *Gate
	Reason Reason
	Key    string
	Detail string
}

// RuleID は記録の rules に残す名前。
func RuleID(g *Gate) string { return RulePrefix + g.ID }

// defaultKeyName は KeyName を書かない宣言の仮の値。
const defaultKeyName = "<キー>"

// IntegrateGate は、差分をユーザに見せて返事を待つ手順書の約束を機械にする。キーは `<名前>`。`integrate-done` は
// `-m <メッセージ>` を名前の前後どちらにも置けるので、メッセージの引数を読み飛ばしてから名前を取る。`--abort` は
// 先頭が `-` なので名前として取れず、A にならない。
var IntegrateGate = &Gate{
	ID:    "integrate-user-turn",
	First: Cond{Regex: `ccnavi-git\.sh\s+integrate\s+(?P<key>[^-\s\x00\x01][^\s\x00\x01]*)`},
	Second: Cond{Regex: `ccnavi-git\.sh\s+integrate-done\s+` +
		`(?:(?:-m|--message)\s+[^\s\x00]+\s+|--message=[^\s\x00]+\s+)*` +
		`(?P<key>[^-\s\x00\x01][^\s\x00\x01]*)`},
	FirstName:     "ccnavi-git.sh integrate",
	SecondName:    "ccnavi-git.sh integrate-done",
	Message:       "DENY_NEEDS_USER_TURN__INTEGRATE",
	KeyName:       "<名前>",
	AskIfAttended: true,
	AskMessage:    "NOTICE_ASK_USER_CONFIRM__INTEGRATE",
}

// reviewCheck は `--check`（打つだけで何も変えない確認）。A にも B にもならない。
const reviewCheck = `(?:^|\s)--check(?:\s|$)`

// ReviewConfirmGate は、レビューを頼んだ（`review-request`）直後に同じターンで確定（`confirm`）して、ユーザのレビューを
// 飛ばせないようにする。`confirm` は依頼済みの未解決スレッドの有無しか見ないので、人が何も見ていなくても通る。付き添いの
// あるセッションでも確認には出さず、「レビューを終えた」というユーザの発言を待つ。キーは付けない（`--phase` の書き方を
// 読まずに済み、どのフェーズの依頼のあとでも発言を待つ。待つ間は別の依頼を受けない決まりなので困らない）。
var ReviewConfirmGate = &Gate{
	ID:         "review-confirm-user-turn",
	First:      Cond{Regex: `ccnavi-review\.sh\s+review-request(?:\s|$)`, But: []string{reviewCheck}},
	Second:     Cond{Regex: `ccnavi-review\.sh\s+confirm(?:\s|$)`, But: []string{reviewCheck}},
	FirstName:  "ccnavi-review.sh review-request",
	SecondName: "ccnavi-review.sh confirm",
	Message:    "DENY_NEEDS_USER_TURN__REVIEW_CONFIRM",
	KeyName:    "<N>",
}

// Builtin は組み込みの宣言の表。
var Builtin = []*Gate{IntegrateGate, ReviewConfirmGate}

type compiledCond struct {
	pattern *pyre.Regexp
	buts    []*pyre.Regexp
	keyed   bool
}

func compile(c Cond, label string) (compiledCond, error) {
	if (c.Regex != "") == (c.Glob != "") {
		return compiledCond{}, badGate("%s: regex か glob のどちらか 1 つを書いてください", label)
	}
	pattern, problem := rules.CompileCondition(c.Glob, c.Regex, label)
	if pattern == nil {
		return compiledCond{}, badGate("%s: %s", label, problem)
	}
	out := compiledCond{pattern: pattern}
	_, out.keyed = pattern.Group(keyGroup)
	for _, text := range c.But {
		got, problem := rules.CompileCondition("", text, label+" の but")
		if got == nil {
			return compiledCond{}, badGate("%s の but: %s", label, problem)
		}
		out.buts = append(out.buts, got)
	}
	return out, nil
}

func firstLabel(g *Gate) string  { return g.ID + " の first" }
func secondLabel(g *Gate) string { return g.ID + " の second" }

// Validate は宣言を組み立てられるか。
func Validate(g *Gate) error {
	if !gateID.MatchString(g.ID) {
		return badGate("%s: id は英数字・`.`・`_`・`-` で書いてください", g.ID)
	}
	first, err := compile(g.First, firstLabel(g))
	if err != nil {
		return err
	}
	second, err := compile(g.Second, secondLabel(g))
	if err != nil {
		return err
	}
	if first.keyed != second.keyed {
		return badGate("%s: キー（名前つきグループ %s）は first と second の両方に書くか、両方に書かないかのどちらかです", g.ID, keyGroup)
	}
	return nil
}

// hit は条件に当たった箇所の、比べるキーと書かれたキー。キーのない条件ではどちらも空。
type hit struct{ key, raw string }

func normalKey(raw string) string {
	if safeKey.MatchString(raw) {
		return pyfmt.Casefold(raw)
	}
	return wildcard
}

func sameKey(a, b string) bool { return a == wildcard || b == wildcard || a == b }

// hits は条件に当たった箇所（重なりは除く）。commands のそれぞれを shell.Sep で分けたコマンドごとに見る。
func hits(c Cond, label string, commands []string) ([]hit, error) {
	cc, err := compile(c, label)
	if err != nil {
		return nil, err
	}
	var found []hit
	add := func(h hit) {
		if !slices.Contains(found, h) {
			found = append(found, h)
		}
	}
	for _, text := range commands {
		for _, command := range strings.Split(text, shell.Sep) {
			if command == "" || slices.ContainsFunc(cc.buts, func(b *pyre.Regexp) bool { return b.Search(command) }) {
				continue
			}
			for _, m := range cc.pattern.FindAll(command) {
				if !cc.keyed {
					add(hit{})
					continue
				}
				raw, _ := m.Named(keyGroup)
				add(hit{normalKey(raw), raw})
			}
		}
	}
	return found, nil
}

// shown は文面に載せるキー。そのまま打てる形（解ける書き方で、長すぎない）のときだけ。
func shown(raw string) string {
	if len([]rune(raw)) <= shownLimit && safeKey.MatchString(raw) {
		return raw
	}
	return ""
}

type entry struct {
	gate, key string
	at        float64
	path      string
}

func safe(text string, limit int) string { return fsio.SafeName(text, limit) }

func entryName(session, gate, key, call string) string {
	sum := digest.Hex(digest.SHA1, gate+"\x00"+key, digestLen)
	return Prefix + safe(session, sessionLimit) + "-" + safe(gate, partLimit) + "-" + sum + "-" + safe(call, partLimit) + Suffix
}

// sessionNames はこのセッションのものらしい記録のファイル名（名前の順）と、置き場を読めなかった理由。
// 置き場がまだ無ければ空で、理由も空。
func sessionNames(state, session string) ([]string, string) {
	names, err := ospath.DiskListdir(state)
	if err != nil {
		if errors.Is(err, fs.ErrNotExist) {
			return nil, ""
		}
		return nil, fsio.ErrorText(err)
	}
	head := Prefix + safe(session, sessionLimit) + "-"
	var out []string
	for _, n := range names {
		if strings.HasPrefix(n, head) && strings.HasSuffix(n, Suffix) {
			out = append(out, n)
		}
	}
	slices.Sort(out)
	return out, ""
}

// entries はこのセッションの記録、読めない記録のパス、置き場を読めなかった理由。
func entries(state, session string) ([]entry, []string, string) {
	names, failed := sessionNames(state, session)
	var found []entry
	var broken []string
	for _, name := range names {
		path := ospath.Join(state, name)
		data, err := fsio.ReadJSON(path)
		if obj, ok := data.(*pyjson.Object); err == nil && ok {
			if obj.Value("session") != session {
				continue
			}
			at, isNumber := pyjson.Number(obj.Value("at"), pyjson.BoolsRejected)
			gate, isGate := obj.Value("gate").(string)
			key, isKey := obj.Value("key").(string)
			if isNumber && isGate && isKey {
				found = append(found, entry{gate, key, at, path})
				continue
			}
		}
		if ospath.DiskLexists(path) { // 読む間に消えた（B が通って消した）ものは数えない
			broken = append(broken, path)
		}
	}
	return found, broken, failed
}

// DropBroken は読めない記録を消す。ユーザの発言で呼ぶ（記録の A は、この発言より前にある）。
func DropBroken(state, session string) {
	if state == "" || session == "" {
		return
	}
	_, broken, _ := entries(state, session)
	for _, path := range broken {
		fsio.Remove(path)
	}
}

// Check は B を打とうとしているコマンドを止めるか。止めるなら理由、通すなら nil。commands は判定が当てるコマンドの
// 文字列（読み直したコマンドと、実行役が中で実行するコマンド）。通すときは一致した記録を消す（次の A からまた数える）。
//
// 同じコマンドに同じキーの A もあれば止める（まだ記録が無い）。記録の置き場を読めない・読めない記録があれば止める。
// 同じ宣言・同じキーの記録のどれかより後にユーザの発言が無ければ止める（発言の記録が無い・読めなければ「まだ無い」）。
// sameCommandOnly が真なら同じコマンドに A と B が並ぶかだけを見て、記録は読まない・消さない（付き添いのある
// セッションで B を確認に出す宣言に使う）。
func Check(gates []*Gate, commands []string, state, session string, sameCommandOnly bool) (*Stop, error) {
	if state == "" || session == "" {
		return nil, nil
	}
	for _, g := range gates {
		bHits, err := hits(g.Second, secondLabel(g), commands)
		if err != nil {
			return nil, err
		}
		if len(bHits) == 0 {
			continue
		}
		aHits, err := hits(g.First, firstLabel(g), commands)
		if err != nil {
			return nil, err
		}
		for _, b := range bHits {
			if slices.ContainsFunc(aHits, func(a hit) bool { return sameKey(b.key, a.key) }) {
				return &Stop{g, SameCommand, shown(b.raw), ""}, nil
			}
		}
		if sameCommandOnly {
			continue
		}
		all, broken, failed := entries(state, session)
		if failed != "" {
			return &Stop{g, Unreadable, shown(bHits[0].raw), failed}, nil
		}
		if len(broken) > 0 {
			return &Stop{g, Broken, shown(bHits[0].raw), broken[0]}, nil
		}
		type match struct {
			e   entry
			raw string
		}
		var matched []match
		for _, e := range all {
			if e.gate != g.ID {
				continue
			}
			for _, b := range bHits {
				if sameKey(b.key, e.key) {
					matched = append(matched, match{e, b.raw})
				}
			}
		}
		if len(matched) == 0 {
			continue
		}
		spoken, ok := turn.LastPrompt(state, session)
		for _, m := range matched {
			if !ok || !spoken.After(fsio.FromUnixSeconds(m.e.at)) {
				return &Stop{g, NoUserTurn, shown(m.raw), ""}, nil
			}
		}
		for _, m := range matched {
			fsio.Remove(m.e.path)
		}
	}
	return nil, nil
}

// SplitByAttendance は宣言を、発言を待つゲートで見るものと確認（ask）に出すものに分ける。確認に出すのは、付き添いの
// あるセッションで AskIfAttended を立てた宣言だけ。
func SplitByAttendance(gates []*Gate, attended bool) (waited, asked []*Gate) {
	for _, g := range gates {
		if attended && g.AskIfAttended {
			asked = append(asked, g)
		} else {
			waited = append(waited, g)
		}
	}
	return waited, asked
}

// Asks は B を打とうとしているコマンドが確認に出す宣言に当たれば、その宣言とキー（Reason は Ask）。
func Asks(gates []*Gate, commands []string) (*Stop, error) {
	for _, g := range gates {
		found, err := hits(g.Second, secondLabel(g), commands)
		if err != nil {
			return nil, err
		}
		if len(found) > 0 {
			return &Stop{g, Ask, shown(found[0].raw), ""}, nil
		}
	}
	return nil, nil
}

// Note は A を許した呼び出しを記録する。書けなければ A を止める結論を返す。call は呼び出しの識別子（tool_use_id）。
// 空なら時刻から作る（失敗した呼び出しの記録を消せなくなるだけで、止める側の働きは変わらない）。now のゼロ値は今。
func Note(gates []*Gate, commands []string, state, session, call string, now time.Time) (*Stop, error) {
	if state == "" || session == "" {
		return nil, nil
	}
	if now.IsZero() {
		now = time.Now()
	}
	token := call
	if token == "" {
		token = "t" + strconv.FormatInt(time.Now().UnixNano(), 10)
	}
	for _, g := range gates {
		found, err := hits(g.First, firstLabel(g), commands)
		if err != nil {
			return nil, err
		}
		for _, h := range found {
			if err := fsio.MakeDirs(state); err != nil {
				return &Stop{g, Unwritable, shown(h.raw), fsio.ErrorText(err)}, nil
			}
			record := pyjson.NewObject("session", session, "gate", g.ID, "key", h.key, "at", fsio.UnixSeconds(now), "call", call)
			if failed := fsio.WriteJSON(ospath.Join(state, entryName(session, g.ID, h.key, token)), record); failed != "" {
				return &Stop{g, Unwritable, shown(h.raw), failed}, nil
			}
		}
	}
	return nil, nil
}

// ForgetCall はその呼び出しが残した記録を消す。A が拒否された・失敗したときに呼ぶ。識別子が空なら何もしない
// （別の呼び出しの記録を消さないため）。
func ForgetCall(state, session, call string) {
	if state == "" || session == "" || call == "" {
		return
	}
	names, _ := sessionNames(state, session)
	tail := "-" + safe(call, partLimit) + Suffix
	for _, name := range names {
		if strings.HasSuffix(name, tail) {
			fsio.Remove(ospath.Join(state, name))
		}
	}
}

// Clear はこのセッションの記録をすべて消す。起動と再開で呼ぶ。
func Clear(state, session string) {
	if state == "" || session == "" {
		return
	}
	names, _ := sessionNames(state, session)
	for _, name := range names {
		fsio.Remove(ospath.Join(state, name))
	}
}
