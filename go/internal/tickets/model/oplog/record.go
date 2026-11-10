package oplog

import (
	"fmt"
	"slices"
	"strings"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/digest"
	"ccnavi/internal/infra/fault"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
)

// version は記録の形の版。読むときに違えば壊れた記録として扱う。
const version = 1

// dirName は state の下の置き場の名前（records/prune も同じ名前を残す側に数える）。
const dirName = config.OpsDir

// idLen は操作ID の桁数（uuid4().hex と同じ 32 桁の 16 進）。
const idLen = 32

// idSeparator は IDFor が部品をつなぐ字（区切りの制御文字 0x1f）。
const idSeparator = "\x1f"

// Kind は操作の種類。
type Kind string

const (
	KindDecide     Kind = "decide"
	kindCloseEarly Kind = "close-early"
	kindRequest    Kind = "request"
	kindComment    Kind = "comment"
	kindReady      Kind = "ready"
)

var kinds = []Kind{KindDecide, kindCloseEarly, kindRequest, kindComment, kindReady}

// State は送信の段階の状態。
type State string

const (
	// StatePending はまだ送っていない（または送ったか確かめていない）。
	StatePending State = "pending"
	// StateSent は送ったと確かめた。戻さない。
	StateSent State = "sent"
	// stateUnknown は送ったか確定できない。自動では送り直さない。
	stateUnknown State = "unknown"
)

var states = []State{StatePending, StateSent, stateUnknown}

// StageKind は段階の種類。Begin は検めない（sh が渡す種類をそのまま残す）。
type StageKind string

const (
	StageReply   StageKind = "reply" // スレッドへの返信・解決
	StageIssue   StageKind = "issue"
	StageComment StageKind = "comment"
	stageMR      StageKind = "mr"
	stagePush    StageKind = "push"
	stageDraft   StageKind = "draft"
)

// Error は記録を読み書きできない、または形が合わない。このパッケージが返すエラーはすべてこの型。
type Error struct{ fault.Detail }

func fail(format string, args ...any) error { return &Error{fault.Plain(fmt.Sprintf(format, args...))} }

const (
	msgBadID        = "操作IDの形が不正: %s"
	msgBadKind      = "知らない操作の種類: %s"
	msgBadState     = "知らない段階の状態: %s"
	msgNotJSON      = "操作記録 %s を読めない（JSON ではない）"
	msgBadShape     = "操作記録 %s の形が合わない"
	msgMissing      = "操作記録 %s が無い"
	msgNoStage      = "操作記録 %s に段階 %s が無い"
	msgSentBack     = "段階 %s は送信済み。状態を戻せない"
	msgCannotSave   = "操作記録を保存できない（%s）"
	msgBusy         = "操作記録を更新中の別のプロセスがある。打ち直してください"
	msgBroken       = "%s: 壊れている（%s）"
	msgSummary      = "%s（%s）%s: 未完了の段階 %s"
	msgSummaryWhere = "%s のフェーズ %d"
)

// Stage は送信の段階 1 つ。
type Stage struct {
	ID      string
	kind    StageKind
	State   State
	URL     string
	account string
	tries   int
	at      string
}

// StageSpec は Begin に渡す段階（送る順に並べる）。
type StageSpec struct {
	ID   string
	Kind StageKind
}

// Record は記録 1 つ。Repo・Choices・Plan・Applied・Shown の中身は呼び手の JSON をそのまま入れる。
type Record struct {
	ID          string
	Kind        Kind
	Repo        *pyjson.Object
	Parent      string
	Phase       int
	MR          int
	requestHead string
	Shown       []any
	Choices     *pyjson.Object
	Plan        *pyjson.Object
	Applied     *pyjson.Object
	Stages      []Stage
	createdAt   string
	updatedAt   string
	// Broken は Pending だけが入れる。読めなかった記録の理由（ID のほかの欄は空）。
	Broken string
}

// Unfinished は送り終えていない段階（Sent でないもの）。
func (r *Record) Unfinished() []Stage {
	var out []Stage
	for _, s := range r.Stages {
		if s.State != StateSent {
			out = append(out, s)
		}
	}
	return out
}

// finished はすべての段階が Sent か。
func (r *Record) finished() bool { return len(r.Unfinished()) == 0 }

// untouched は、どの段階も送っておらず、ローカルにも何も適用していない記録か。
func (r *Record) untouched() bool {
	if r.Applied.Len() > 0 {
		return false
	}
	for _, s := range r.Stages {
		if s.State != StatePending {
			return false
		}
	}
	return true
}

func (r *Record) stage(id string) *Stage {
	for i := range r.Stages {
		if r.Stages[i].ID == id {
			return &r.Stages[i]
		}
	}
	return nil
}

// Object は記録を、ファイルに書く形（キーの順序も同じ）の JSON にする。`ops show` はこれを出す。
func (r *Record) Object() *pyjson.Object {
	stages := make([]any, 0, len(r.Stages))
	for _, s := range r.Stages {
		stages = append(stages, pyjson.NewObject(
			"id", s.ID, "kind", string(s.kind), "state", string(s.State),
			"url", s.URL, "account", s.account, "tries", s.tries, "at", s.at,
		))
	}
	shown := r.Shown
	if shown == nil {
		shown = []any{}
	}
	return pyjson.NewObject(
		"version", version,
		"id", r.ID,
		"kind", string(r.Kind),
		"repo", orEmpty(r.Repo),
		"parent", r.Parent,
		"phase", r.Phase,
		"mr", r.MR,
		"request_head", r.requestHead,
		"shown", shown,
		"choices", orEmpty(r.Choices),
		"plan", orEmpty(r.Plan),
		"applied", orEmpty(r.Applied),
		"stages", stages,
		"created_at", r.createdAt,
		"updated_at", r.updatedAt,
	)
}

// Summary は未完了の記録を 1 行で言う（status・ボード・Stop が出す）。
func Summary(r *Record) string {
	if r.Broken != "" {
		return fmt.Sprintf(msgBroken, r.ID, r.Broken)
	}
	var left []string
	for _, s := range r.Unfinished() {
		left = append(left, s.ID+"="+string(s.State))
	}
	where := ospath.Strip(fmt.Sprintf(msgSummaryWhere, r.Parent, r.Phase))
	return fmt.Sprintf(msgSummary, r.Kind, where, r.ID, strings.Join(left, ", "))
}

func orEmpty(o *pyjson.Object) *pyjson.Object {
	if o == nil {
		return pyjson.NewObject()
	}
	return o
}

// validID は操作ID の形か。ID はパスの一部になるので、形を決めて外を断る。
func validID(id string) bool {
	if len(id) != idLen {
		return false
	}
	for _, c := range id {
		if !strings.ContainsRune("0123456789abcdef", c) {
			return false
		}
	}
	return true
}

func checkID(id string) error {
	if !validID(id) {
		return fail(msgBadID, pyfmt.Quote(id))
	}
	return nil
}

// IDFor は内容から決まる操作ID。エージェントの操作（review-request・comment・ready）は同じ操作を
// 打ち直して復旧するので、操作のキーから ID を決めて、打ち直しが同じ記録に当たるようにする。
func IDFor(parts ...string) string {
	return digest.Hex(digest.SHA256, strings.Join(parts, idSeparator), idLen)
}

func newID() string { return digest.RandomHex(digest.UUIDBytes) }

func now() string { return time.Now().Format(fsio.StampLayout) }

func dir(state string) string { return ospath.Join(state, dirName) }

func recordPath(state, id string) string { return ospath.Join(dir(state), id+fsio.JSONExt) }

// fromObject は読んだ JSON を記録にする。version・id・stages に加えて、
// 段階とマップの欄の型も確かめる（合わなければ形が合わない記録として止める）。
func fromObject(id string, o *pyjson.Object) (*Record, error) {
	bad := fail(msgBadShape, id)
	if !pyfmt.EqualsInt(o.Value("version"), version) {
		return nil, bad
	}
	if got, ok := o.Value("id").(string); !ok || got != id {
		return nil, bad
	}
	rawStages, ok := o.Value("stages").([]any)
	if !ok {
		return nil, bad
	}
	r := &Record{
		ID:          id,
		Kind:        Kind(o.String("kind")),
		Parent:      o.String("parent"),
		requestHead: o.String("request_head"),
		createdAt:   o.String("created_at"),
		updatedAt:   o.String("updated_at"),
	}
	r.Phase, _ = pyfmt.IntOf(o.Value("phase"))
	r.MR, _ = pyfmt.IntOf(o.Value("mr"))
	for _, field := range []struct {
		key string
		to  **pyjson.Object
	}{{"repo", &r.Repo}, {"choices", &r.Choices}, {"plan", &r.Plan}, {"applied", &r.Applied}} {
		v, has := o.Get(field.key)
		obj, isObj := v.(*pyjson.Object)
		if has && !isObj {
			return nil, bad
		}
		*field.to = orEmpty(obj)
	}
	if v, has := o.Get("shown"); has {
		if r.Shown, ok = v.([]any); !ok {
			return nil, bad
		}
	}
	for _, raw := range rawStages {
		s, ok := raw.(*pyjson.Object)
		if !ok {
			return nil, bad
		}
		tries, _ := pyfmt.IntOf(s.Value("tries"))
		r.Stages = append(r.Stages, Stage{
			ID: s.String("id"), kind: StageKind(s.String("kind")), State: State(s.String("state")),
			URL: s.String("url"), account: s.String("account"), tries: tries, at: s.String("at"),
		})
	}
	return r, nil
}

func newStage(spec StageSpec) Stage {
	return Stage{ID: spec.ID, kind: spec.Kind, State: StatePending}
}

func knownKind(k Kind) bool   { return slices.Contains(kinds, k) }
func knownState(s State) bool { return slices.Contains(states, s) }
