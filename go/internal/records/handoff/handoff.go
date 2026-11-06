package handoff

import (
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"io"
	"os"
	"strconv"
	"strings"
	"time"

	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
)

const (
	// DefaultTokens は閾値の既定（トークン数）。
	DefaultTokens = 400000
	// Marker は起こすときの指示の先頭に付ける目印。この目印で始まる入力では待機を戻さない。
	// hookio がユーザの発言から除く目印と同じ値にする。
	Marker = "[idle-handoff]"

	// 1 時間のキャッシュの失効の 5 分前と、5 分のキャッシュの失効の 1 分前。
	waitLong  = 55 * time.Minute
	waitShort = 4 * time.Minute
	// maxWait は待ち時間の上限。hook の上限 3600 秒より前に起きる。
	maxWait = 3300 * time.Second
	// keep は状態ファイルを残す長さ。
	keep = 7 * 24 * time.Hour

	ttlShort = 300 * time.Second
	ttlLong  = 3600 * time.Second

	// 末尾から読むトランスクリプトの大きさ。末尾に応答の行が無ければ（巨大なツールの結果が続いたとき）
	// 4 倍ずつ広げ、tailLimit で止める。
	tailBytes = 1024 * 1024
	tailLimit = 16 * 1024 * 1024
	// lookback はキャッシュの保持時間を探して遡る応答の行数の上限。
	lookback = 40

	folderName   = "idle"
	stateSuffix  = ".json"
	noticeSuffix = ".notice"
	tokenBytes   = 8
)

// phase は待機の状態。
type phase string

const (
	waiting phase = "waiting"
	fired   phase = "fired"
	active  phase = "active"
)

// 状態ファイルのキー。
const (
	keyState      = "state"
	keyToken      = "token"
	keyDue        = "due"
	keyBase       = "base"
	keyAt         = "at"
	keyTranscript = "transcript"
	keyWait       = "wait"
	keyTold       = "told"
)

// Usage は直近の応答の時点での会話の大きさと、キャッシュの保持時間（5 分か 1 時間）。
type Usage struct {
	Tokens int
	TTL    time.Duration
}

// Plan は待機の指示。sh が Wait だけ sleep してから Check を呼ぶ。
type Plan struct {
	Wait  time.Duration
	Token string
}

// Threshold は閾値（トークン数）を読む。0 は無効。空・読めない値は既定、負数は 0。
func Threshold(raw string) int {
	text := ospath.Strip(raw)
	if text == "" {
		return DefaultTokens
	}
	value, err := pyfmt.ParseInt(text)
	if err != nil {
		return DefaultTokens
	}
	return max(value, 0)
}

// Unattended は見ている人がいない実行（`claude -p`・SDK）か。待機がプロセスを引き止めるので除く。
func Unattended(getenv func(string) string) bool {
	return getenv(env.ClaudeSessionAttended) == "0" || strings.HasPrefix(getenv(env.ClaudeCodeEntrypoint), "sdk")
}

// UsageOf はトランスクリプトの末尾から、直近の応答の会話の大きさとキャッシュの保持時間を読む。
// サブエージェントの行は別の会話なので除く。大きさが分からなければ nil。
func UsageOf(transcript string) *Usage {
	if transcript == "" {
		return nil
	}
	var rows []*pyjson.Object
	for reach := tailBytes; reach <= tailLimit; reach *= 4 {
		raw, whole, ok := tail(transcript, reach)
		if !ok {
			return nil
		}
		// 中断や API の誤りの行は使用量がすべて 0 で、大きさを測れないので飛ばす。
		rows = rows[:0]
		for _, u := range assistantUsages(raw) {
			if sizeOf(u) > 0 {
				rows = append(rows, u)
			}
		}
		if len(rows) > 0 || whole {
			break
		}
	}
	if len(rows) == 0 {
		return nil
	}
	return &Usage{Tokens: sizeOf(rows[0]), TTL: ttlOf(rows[:min(len(rows), lookback)])}
}

var sizeKeys = []string{"input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"}

func sizeOf(usage *pyjson.Object) int {
	total := 0
	for _, k := range sizeKeys {
		total += positive(usage.Value(k))
	}
	return total
}

// positive は正の整数ならその値、ほかは 0。
func positive(v any) int {
	if n, ok := v.(int); ok && n > 0 {
		return n
	}
	return 0
}

// tail は末尾から reach バイトを読む。返すのは本文と、ファイル全体を読んだか。先頭の欠けた行は捨てる。
func tail(path string, reach int) (string, bool, bool) {
	f, err := os.Open(path)
	if err != nil {
		return "", true, false
	}
	defer f.Close()
	info, err := f.Stat()
	if err != nil {
		return "", true, false
	}
	size := info.Size()
	if _, err := f.Seek(max(size-int64(reach), 0), io.SeekStart); err != nil {
		return "", true, false
	}
	data, err := io.ReadAll(f)
	if err != nil {
		return "", true, false
	}
	text, _ := fsio.Decode(data, fsio.Replace)
	whole := size <= int64(reach)
	if !whole {
		_, text, _ = strings.Cut(text, "\n")
	}
	return text, whole, true
}

// assistantUsages は新しい順に、サブエージェントでない応答の usage を返す。
func assistantUsages(raw string) []*pyjson.Object {
	lines := ospath.Splitlines(raw)
	var rows []*pyjson.Object
	for i := len(lines) - 1; i >= 0; i-- {
		line := ospath.Strip(lines[i])
		if line == "" {
			continue
		}
		data, err := pyjson.Loads(line)
		obj, ok := data.(*pyjson.Object)
		if err != nil || !ok || obj.Value("type") != "assistant" || pyfmt.Truthy(obj.Value("isSidechain")) {
			continue
		}
		message, _ := obj.Value("message").(*pyjson.Object)
		if usage, ok := message.Value("usage").(*pyjson.Object); ok {
			rows = append(rows, usage)
		}
	}
	return rows
}

// ttlOf は直近でキャッシュを書き込んだ応答の内訳で保持時間を決める。5 分側に書き込みがあれば 5 分、
// 無ければ 1 時間。どの応答にも書き込みが無ければ 1 時間とみなす。
func ttlOf(rows []*pyjson.Object) time.Duration {
	for _, usage := range rows {
		made, ok := usage.Value("cache_creation").(*pyjson.Object)
		if !ok {
			continue
		}
		short := positive(made.Value("ephemeral_5m_input_tokens"))
		long := positive(made.Value("ephemeral_1h_input_tokens"))
		if short > 0 {
			return ttlShort
		}
		if long > 0 {
			return ttlLong
		}
	}
	return ttlLong
}

// waitFor は放置してから起こすまでの待ち時間。上書き（秒）が正の整数ならそれ（maxWait まで）。
func waitFor(ttl time.Duration, override string) time.Duration {
	text := ospath.Strip(override)
	if text != "" && strings.Trim(text, "0123456789") == "" {
		n, err := strconv.Atoi(text)
		if err != nil {
			return maxWait // 桁あふれは上限より大きい
		}
		if n > 0 {
			return min(time.Duration(n)*time.Second, maxWait)
		}
	}
	if ttl <= ttlShort {
		return waitShort
	}
	return waitLong
}

// ---- 状態

func folder(state string) string { return ospath.Join(state, folderName) }

func statePath(state, session, suffix string) string {
	return ospath.Join(folder(state), fsio.SafeName(session, fsio.SafeNameLimit)+suffix)
}

func read(state, session string) *pyjson.Object {
	obj, ok := fsio.ReadObject(statePath(state, session, stateSuffix))
	if !ok {
		return pyjson.NewObject()
	}
	return obj
}

func write(state, session string, data *pyjson.Object) bool {
	if fsio.MakeDirs(folder(state)) != nil {
		return false
	}
	return fsio.WriteJSON(statePath(state, session, stateSuffix), data) == ""
}

// base は基準。会話が基準より小さくなっていたら 0 に戻っているものとして読む。
func base(data *pyjson.Object, size int) int {
	b := positive(data.Value(keyBase))
	if size >= b {
		return b
	}
	return 0
}

// due はこの大きさが促す対象か。
func due(data *pyjson.Object, size, limit int) bool {
	return limit > 0 && size >= base(data, size)+limit
}

// sessionOK は状態ファイルの名前にそのまま使える識別子か。
func sessionOK(session string) bool {
	return session != "" && fsio.SafeName(session, fsio.SafeNameLimit) == session
}

// Start はターンの終わりに待機を始める。始めないなら false。見ている人がいない実行・サブエージェントは
// 呼ぶ前に除く。状態は waiting と印（token）にし、基準は持ち越す。
func Start(state, session string, usage *Usage, limit int, waitOverride, transcript string) (Plan, bool) {
	if usage == nil || !sessionOK(session) {
		return Plan{}, false
	}
	data := read(state, session)
	if data.Value(keyState) == string(fired) || !due(data, usage.Tokens, limit) {
		return Plan{}, false
	}
	token, err := newToken()
	if err != nil {
		return Plan{}, false
	}
	fresh := pyjson.NewObject(
		keyState, string(waiting),
		keyToken, token,
		keyDue, usage.Tokens,
		keyBase, base(data, usage.Tokens),
		keyAt, int(time.Now().Unix()),
		keyTranscript, transcript,
	)
	if !write(state, session, fresh) {
		return Plan{}, false
	}
	return Plan{Wait: waitFor(usage.TTL, waitOverride), Token: token}, true
}

func newToken() (string, error) {
	b := make([]byte, tokenBytes)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return hex.EncodeToString(b), nil
}

// Check は待機のあとで呼ぶ。起こすべきなら指示の文を返し、状態を fired にして基準を進める。
// 状態が waiting でない・印が違う（その間にユーザが打った、あとの待機に置き換わった）なら false。
// limit が正で待機の始めに会話の場所を覚えていれば大きさを読み直し、入力なしに小さくなっていたら
// （/compact など）起こさない。
func Check(state, session, token string, wait time.Duration, limit int) (string, bool) {
	if !sessionOK(session) {
		return "", false
	}
	data := read(state, session)
	if data.Value(keyState) != string(waiting) || data.Value(keyToken) != token {
		return "", false
	}
	if transcript := data.Value(keyTranscript); limit > 0 && pyfmt.Truthy(transcript) {
		path, _ := transcript.(string)
		usage := UsageOf(path)
		if usage == nil || !due(data, usage.Tokens, limit) {
			return "", false
		}
	}
	next := data.With(keyState, string(fired), keyBase, positive(data.Value(keyDue)), keyWait, int(wait/time.Second))
	next.Pop(keyToken)
	next.Pop(keyTold)
	if !write(state, session, next) {
		return "", false
	}
	return fmt.Sprintf(wakeText, Marker, minutes(wait)), true
}

// Reset はユーザの入力で待機をやめる。基準は残す。起こす指示（目印付き）とバックグラウンド作業の
// 完了通知はユーザの入力ではないので戻さない。
func Reset(state, session, prompt string, background bool) {
	if !sessionOK(session) || background || strings.HasPrefix(strings.TrimLeftFunc(prompt, ospath.IsSpace), Marker) {
		return
	}
	data := read(state, session)
	if data.Len() == 0 || data.Value(keyState) == string(active) {
		return
	}
	data.Pop(keyToken)
	data.Set(keyState, string(active))
	write(state, session, data)
}

// Notice はユーザへの知らせ。出すなら文を返し、知らせ済みの印を置く。
// 引き継ぎを書かせた後（fired）は、書かせたことを 1 回だけ知らせる（印は状態ファイルの told。次に
// 書かせるとき Check が消す）。それ以外は、会話が大きく育ったときの予告を 1 セッションに 1 回出す。
func Notice(state, session string, usage *Usage, limit int, waitOverride string) (string, bool) {
	if !sessionOK(session) {
		return "", false
	}
	data := read(state, session)
	if data.Value(keyState) == string(fired) {
		if pyfmt.Truthy(data.Value(keyTold)) {
			return "", false
		}
		if !write(state, session, data.With(keyTold, true)) {
			return "", false
		}
		return fmt.Sprintf(doneText, minutes(time.Duration(positive(data.Value(keyWait)))*time.Second)), true
	}
	if usage == nil || !due(data, usage.Tokens, limit) {
		return "", false
	}
	mark := statePath(state, session, noticeSuffix)
	if ospath.DiskExists(mark) {
		return "", false
	}
	if fsio.MakeDirs(folder(state)) != nil {
		return "", false
	}
	if fsio.WriteText(mark, "1\n", fsio.NativeNewline, fsio.Atomic) != "" {
		return "", false
	}
	return fmt.Sprintf(noticeText, grouped(usage.Tokens), int(usage.TTL/time.Minute),
		minutes(waitFor(usage.TTL, waitOverride)), env.HandoffSuggestTokens), true
}

// minutes は文に出す分。1 分未満でも 1 と書く。
func minutes(d time.Duration) int { return max(int(d/time.Minute), 1) }

// grouped は Python の f"{n:,}"。
func grouped(n int) string {
	digits := strconv.Itoa(n)
	sign := ""
	if strings.HasPrefix(digits, "-") {
		sign, digits = "-", digits[1:]
	}
	var b strings.Builder
	for i, c := range digits {
		if i > 0 && (len(digits)-i)%3 == 0 {
			b.WriteByte(',')
		}
		b.WriteRune(c)
	}
	return sign + b.String()
}

// Prune は古い状態ファイルを消す。知らせ済みの印は書き換わらないので、対の状態ファイルが在る間は残す。
func Prune(state string, now time.Time) {
	dir := folder(state)
	entries, err := os.ReadDir(dir)
	if err != nil {
		return
	}
	cutoff := now.Add(-keep)
	for _, entry := range entries {
		name := entry.Name()
		file := ospath.Join(dir, name)
		if stem, ok := strings.CutSuffix(name, noticeSuffix); ok && ospath.DiskExists(ospath.Join(dir, stem+stateSuffix)) {
			continue
		}
		info, err := os.Stat(file)
		if err != nil || !info.ModTime().Before(cutoff) {
			continue
		}
		fsio.Remove(file)
		if stem, ok := strings.CutSuffix(name, stateSuffix); ok {
			fsio.Remove(ospath.Join(dir, stem+noticeSuffix))
		}
	}
}
