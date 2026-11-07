package stopreview

import (
	"slices"
	"strings"
	"time"

	"ccnavi/internal/infra/digest"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
)

// 置き場の名前。後始末（prune）が同じ名前で読むので、変えるときは揃えて直す。
const (
	// ResultDir は ccnavi-ticket.sh が approval-request の結果を書くディレクトリ（state の下）。
	ResultDir = "stopops-result"
	// MaterialDir は判定に渡した材料を伏せ字にして残すディレクトリ（ログの置き場の下）。
	MaterialDir = "stop-review/material"
	// EventsFile は試行のイベントの記録（ログの置き場の下）。
	EventsFile = "stop-review/events.jsonl"
	labelsFile = "stop-review/labels.jsonl"

	// セッションを名前に持たない記録の名前の先頭。後ろはキーの要約・試行 ID・呼び出し。後始末はそれ自身の日付で消す。
	PlanPrefix    = "stopreview-plan-"
	AttemptPrefix = "stopreview-attempt-"
	PrePrefix     = "stopops-pre-"
	OpPrefix      = "stopops-op-"
	// セッションごとの記録の名前の先頭。後ろはセッション（stopwrite- はセッションと "-" と識別子）。
	TurnStartPrefix = "stopturn-"
	RefsPrefix      = "stoprefs-"
	WritePrefix     = "stopwrite-"
	LastStopPrefix  = "stoplast-"
	TaintPrefix     = "stoptaint-"
	resultSuffix    = ".kv"
)

// logPath は "a/b" の形の名前を、機械の区切りで dir に足す。
func logPath(dir, name string) string {
	return ospath.Join(dir, strings.Split(name, "/")...)
}

func safe(text string) string { return fsio.SafeName(text, fsio.SafeNameLimit) }

func sessionFile(state, prefix, session string) string {
	return fsio.RecordPath(state, prefix, session, fsio.JSONExt)
}

// names はディレクトリの中の名前を並べて返す。無ければ空（結果ファイルの置き場は最初の approval-request まで無い）。
func names(dir string) []string {
	if dir == "" {
		return nil
	}
	found, err := fsio.Listdir(dir)
	if err != nil {
		return nil
	}
	slices.Sort(found)
	return found
}

// records は dir の中の prefix で始まる JSON の記録を、名前の順に読む。読めない・空のものは飛ばす。
func records(dir, prefix string) []*pyjson.Object {
	var out []*pyjson.Object
	for _, name := range names(dir) {
		if !strings.HasPrefix(name, prefix) || !strings.HasSuffix(name, fsio.JSONExt) {
			continue
		}
		if obj, ok := fsio.ReadObject(ospath.Join(dir, name)); ok && obj.Len() > 0 {
			out = append(out, obj)
		}
	}
	return out
}

// readObject は記録を読む。読めなければ nil。
func readObject(path string) *pyjson.Object {
	obj, ok := fsio.ReadObject(path)
	if !ok {
		return nil
	}
	return obj
}

// keyLength は名前と版に使う要約の 16 進の字数。
const keyLength = 16

func shortKey(text string) string { return digest.Hex(digest.SHA1, text, keyLength) }

// moment は記録の UNIX 秒を時刻として読む（真偽値も数として読む）。数でなければ fallback。
func moment(v any, fallback time.Time) time.Time {
	if n, ok := pyjson.Number(v, pyjson.BoolsAsNumbers); ok {
		return fsio.FromUnixSeconds(n)
	}
	return fallback
}

// strs は JSON の列から文字列だけを取り出す。
func strs(v any) []string {
	items, _ := v.([]any)
	var out []string
	for _, item := range items {
		if s, ok := item.(string); ok {
			out = append(out, s)
		}
	}
	return out
}

// clip は先頭から limit 文字（符号位置）で切る。切ったら true。
func clip(text string, limit int) (string, bool) {
	head := pyfmt.Head(text, limit)
	return head, len(head) < len(text)
}
