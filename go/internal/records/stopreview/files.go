package stopreview

import (
	"crypto/sha1"
	"encoding/hex"
	"math"
	"slices"
	"strings"
	"time"
	"unicode"

	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
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

	planPrefix    = "stopreview-plan-"
	attemptPrefix = "stopreview-attempt-"
	prePrefix     = "stopops-pre-"
	opPrefix      = "stopops-op-"
	// セッションごとの記録の名前の先頭。後ろはセッション（stopwrite- はセッションと識別子）。
	TurnStartPrefix = "stopturn-"
	RefsPrefix      = "stoprefs-"
	WritePrefix     = "stopwrite-"
	LastStopPrefix  = "stoplast-"
	TaintPrefix     = "stoptaint-"
	jsonSuffix      = ".json"
	resultSuffix    = ".kv"
)

// logPath は "a/b" の形の名前を、機械の区切りで dir に足す（Python の os.path.join と同じ綴り）。
func logPath(dir, name string) string {
	return ospath.Join(dir, strings.Split(name, "/")...)
}

func safe(text string) string { return fsio.SafeName(text, fsio.SafeNameLimit) }

func sessionFile(state, prefix, session string) string {
	return ospath.Join(state, prefix+safe(session)+jsonSuffix)
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
		if !strings.HasPrefix(name, prefix) || !strings.HasSuffix(name, jsonSuffix) {
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

func digest(text string) string {
	sum := sha1.Sum([]byte(text))
	return hex.EncodeToString(sum[:])[:16]
}

// number は JSON の数を秒などの値として読む。数でなければ fallback。
func number(v any, fallback float64) float64 {
	switch x := v.(type) {
	case int:
		return float64(x)
	case float64:
		return x
	case bool: // Python の float(True)
		if x {
			return 1
		}
		return 0
	}
	return fallback
}

// seconds は記録に書く UNIX 秒（Python の time.time()）。
func seconds(t time.Time) float64 { return float64(t.UnixNano()) / 1e9 }

func fromSeconds(f float64) time.Time {
	whole, frac := math.Modf(f)
	return time.Unix(int64(whole), int64(math.Round(frac*1e9)))
}

// with は data に kv を重ねた写し（Python の dict(data, **kv)）。
func with(data *pyjson.Object, kv ...any) *pyjson.Object {
	out := pyjson.NewObject()
	for k, v := range data.All() {
		out.Set(k.(string), v)
	}
	for i := 0; i+1 < len(kv); i += 2 {
		out.Set(kv[i].(string), kv[i+1])
	}
	return out
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

// isWord は Python の re の Unicode の \w（文字・数字・"_"）。
func isWord(r rune) bool {
	return r == '_' || unicode.IsLetter(r) || unicode.IsNumber(r)
}

// clip は先頭から limit 文字（符号位置）で切る。切ったら true。
func clip(text string, limit int) (string, bool) {
	runes := []rune(text)
	if len(runes) <= limit {
		return text, false
	}
	return string(runes[:limit]), true
}
