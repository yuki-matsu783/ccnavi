package marks

import (
	"strconv"

	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/tickets/model/place"
)

// Kind はフェーズのマーカーの種類（ファイル名の拡張子）。
type Kind string

const (
	Requested Kind = "requested"
	Reviewed  Kind = "reviewed"
	Skipped   Kind = "skipped"
	// Pending は「終わったと 1 度伝えた」のマーカー。同じ文を呼び出しごとに繰り返さないため。
	Pending Kind = "pending"
)

// Kinds はマーカーの種類の全部（読む順・知らせる順）。
var Kinds = []Kind{Requested, Reviewed, Skipped, Pending}

// Parent は親ごとのマーカー（フェーズの番号に付かないもの）。closed が要るのは、提案（wip/）が統合先に
// 取り込む前に消え、全フェーズが `review: false` の進め方では親を閉じた事実がここにしか残らないから。
type Parent string

const (
	Ready      Parent = "ready"       // Draft を外した。「マージに進んでよい」の合図
	CloseEarly Parent = "close-early" // ユーザが早めに閉じた。残りは別の issue へ
	Closed     Parent = "closed"      // 親を閉じた（`ticket finish <親>`）
	// Ledger は受け入れ台帳。ホストで未解決のまま受け入れた指摘を親ごとに持つ（フェーズを開き直しても残る）。
	Ledger Parent = "accepted"
)

// Record は子ごとの記録の種類（`<子>.<種類>.json`）。
type Record string

const (
	Risk  Record = "risk"  // 実績のリスク {points, level, hits, unmeasured, head, at}
	Judge Record = "judge" // 定性項目の判定 {<項目>: {hit, reason, head, at}}
)

const (
	atKey         = "at"
	saveFailed    = "保存できませんでした ({reason})"
	ledgerVersion = 1
)

// MarkPath はフェーズのマーカーのパス。
func MarkPath(approvedDir, parent string, phase int, kind Kind) string {
	return ospath.Join(approvedDir, place.PhasesDir, parent, strconv.Itoa(phase)+"."+string(kind))
}

// ReadMark はマーカーの中身。無い・読めなければ偽、マップでなければ空のマップ。
func ReadMark(approvedDir, parent string, phase int, kind Kind) (*pyjson.Object, bool) {
	return fsio.ReadObject(MarkPath(approvedDir, parent, phase, kind))
}

// WriteMark はマーカーを書く（at が無ければ今の時刻を足す）。書けなければ理由。
func WriteMark(approvedDir, parent string, phase int, kind Kind, data *pyjson.Object) string {
	return writeJSON(MarkPath(approvedDir, parent, phase, kind), stamped(data), 0)
}

// Marks はこのフェーズに在るマーカー（種類 → 中身）。
func Marks(approvedDir, parent string, phase int) map[Kind]*pyjson.Object {
	found := map[Kind]*pyjson.Object{}
	for _, kind := range Kinds {
		if data, ok := ReadMark(approvedDir, parent, phase, kind); ok {
			found[kind] = data
		}
	}
	return found
}

// ParentPath は親ごとのマーカーのパス。
func ParentPath(approvedDir, parent string, name Parent) string {
	return ospath.Join(approvedDir, place.PhasesDir, parent, string(name)+fsio.JSONExt)
}

// ReadParent は親ごとのマーカーの中身。
func ReadParent(approvedDir, parent string, name Parent) (*pyjson.Object, bool) {
	return fsio.ReadObject(ParentPath(approvedDir, parent, name))
}

// WriteParent は親ごとのマーカーを書く（at が無ければ今の時刻を足す）。書けなければ理由。
func WriteParent(approvedDir, parent string, name Parent, data *pyjson.Object) string {
	return writeJSON(ParentPath(approvedDir, parent, name), stamped(data), 1)
}

// RecordPath は子ごとの記録のパス。
func RecordPath(approvedDir, parent, child string, kind Record) string {
	return ospath.Join(approvedDir, place.PhasesDir, parent, child+"."+string(kind)+fsio.JSONExt)
}

// ReadRecord は子ごとの記録の中身。
func ReadRecord(approvedDir, parent, child string, kind Record) (*pyjson.Object, bool) {
	return fsio.ReadObject(RecordPath(approvedDir, parent, child, kind))
}

// WriteRecord は子ごとの記録を書く（時刻は足さない）。書けなければ理由。
func WriteRecord(approvedDir, parent, child string, kind Record, data *pyjson.Object) string {
	return writeJSON(RecordPath(approvedDir, parent, child, kind), data, 1)
}

// ReadLedger は台帳の項目と、読めなかった理由（読めれば空）。無ければ空の台帳。
func ReadLedger(approvedDir, parent string) ([]*pyjson.Object, string) {
	path := ParentPath(approvedDir, parent, Ledger)
	if !fsio.Exists(path) {
		return nil, ""
	}
	bad := "受け入れ台帳 " + path + " を読めない"
	data, ok := fsio.ReadObject(path)
	if !ok {
		return nil, bad
	}
	list, ok := data.Value("entries").([]any)
	if !ok {
		return nil, bad
	}
	entries := make([]*pyjson.Object, 0, len(list))
	for _, e := range list {
		obj, ok := e.(*pyjson.Object)
		if !ok {
			return nil, bad
		}
		entries = append(entries, obj)
	}
	return entries, ""
}

// AddLedger は台帳に項目を足す（同じ key は置き換える。at が空なら今の時刻）。書けなければ理由。
// 読めない台帳には書かない（書くと読めなかった項目が消える）。
func AddLedger(approvedDir, parent string, add []*pyjson.Object) string {
	entries, problem := ReadLedger(approvedDir, parent)
	if problem != "" {
		return problem
	}
	keys := map[string]bool{}
	for _, e := range add {
		keys[ledgerKey(e)] = true
	}
	merged := []any{}
	for _, e := range entries {
		if !keys[ledgerKey(e)] {
			merged = append(merged, e)
		}
	}
	stamp := fsio.Stamp()
	for _, e := range add {
		at := e.Value(atKey)
		if !pyfmt.Truthy(at) {
			at = stamp
		}
		merged = append(merged, e.With(atKey, at))
	}
	return WriteParent(approvedDir, parent, Ledger, pyjson.NewObject("version", ledgerVersion, "entries", merged))
}

// ledgerKey は Python の str(e.get("key"))（無ければ "None"）。
func ledgerKey(e *pyjson.Object) string {
	return pyfmt.Str(e.Value("key"))
}

// WriteTicket は承認済みチケットを書き直す。途中で中断されても前か新しい中身のどちらかを残す（Durable）。
// 改行は機械の改行。書けなければ理由。
func WriteTicket(path, text string) string {
	defer fsio.SetPolicy(func(p *fsio.Policy) { p.Message = saveFailed })()
	if failed := fsio.WriteText(path, text, fsio.NativeNewline, fsio.Durable); failed != "" {
		return "保存できませんでした (" + failed + ")"
	}
	return ""
}

func stamped(data *pyjson.Object) *pyjson.Object {
	out := pyjson.NewObject()
	if data != nil {
		out = data.With()
	}
	if _, ok := out.Get(atKey); !ok {
		out.Set(atKey, fsio.Stamp())
	}
	return out
}

func writeJSON(path string, data *pyjson.Object, indent int) string {
	if data == nil {
		data = pyjson.NewObject()
	}
	text, err := pyjson.Dumps(data, pyjson.Options{RawUnicode: true, Indent: indent})
	if err != nil {
		return "保存できませんでした (" + err.Error() + ")"
	}
	defer fsio.SetPolicy(func(p *fsio.Policy) { p.Message = saveFailed })()
	if failed := fsio.WriteText(path, text, fsio.NativeNewline, fsio.Plain); failed != "" {
		return "保存できませんでした (" + failed + ")"
	}
	return ""
}
