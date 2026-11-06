package diaglog

import (
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"

	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/records/redact"
)

// Dir はワークスペースルートからの置き場。
var Dir = filepath.Join("logs", "diag")

const fileExt = ".log"

// Level は出力のレベル。CCNAVI_LOG_LEVEL で閾値を決める。
type Level int

const (
	Debug Level = 10
	Info  Level = 20
	Warn  Level = 30
	Error Level = 40
)

// labels は行に書くレベル（5 字の左寄せ）。
var labels = map[Level]string{Debug: "DEBUG", Info: "INFO ", Warn: "WARN ", Error: "ERROR"}

var byWord = map[string]Level{"DEBUG": Debug, "INFO": Info, "WARN": Warn, "ERROR": Error}

// sourceName は出どころに使える名前。ほかの字（区切り・`.`・空白）を含む名前では書かない。
// 置き換えると別の出どころの名前と重なるので、置き換えはしない。
var sourceName = regexp.MustCompile(`^[A-Za-z0-9_-]+$`)

// stampLayout は現地時刻と時差を秒まで（BSD の date がミリ秒を出せないので 3 つの logger で揃える）。
const stampLayout = "2006-01-02T15:04:05-07:00"

// threshold は CCNAVI_LOG_LEVEL。空と読めない値は Info。
func threshold() Level {
	if level, ok := byWord[strings.ToUpper(os.Getenv(env.LogLevel))]; ok {
		return level
	}
	return Info
}

// Logger は 1 つの出どころの書き手。Get で作る。
type Logger struct {
	name  string
	root  string
	level Level
}

// Get は出どころ name の書き手。root はワークスペースルートで、空なら CLAUDE_PROJECT_DIR。
// どちらも無ければ何も書かない（置き場を当て推量で決めない）。
func Get(name, root string) *Logger {
	return &Logger{name: name, root: root, level: threshold()}
}

// Enabled は level の行を出すか。重い材料はこの条件の中で組み立てる。
func (l *Logger) Enabled(level Level) bool { return level >= l.level }

// Debug・Info・Warn・Error の kv は key, value, key, value… と並べる（値は渡した順に書く）。
// 出さないレベルでは時刻も行も作らない。

func (l *Logger) Debug(msg string, kv ...any) { l.log(Debug, msg, kv) }
func (l *Logger) Info(msg string, kv ...any)  { l.log(Info, msg, kv) }
func (l *Logger) Warn(msg string, kv ...any)  { l.log(Warn, msg, kv) }
func (l *Logger) Error(msg string, kv ...any) { l.log(Error, msg, kv) }

func (l *Logger) log(level Level, msg string, kv []any) {
	if !l.Enabled(level) {
		return
	}
	root := l.root
	if root == "" {
		root = os.Getenv(env.ClaudeProjectDir)
	}
	if root == "" || !sourceName.MatchString(l.name) {
		return
	}
	var tail strings.Builder
	for i := 0; i < len(kv); i += 2 {
		key := pyfmt.Str(kv[i])
		var value any
		if i+1 < len(kv) {
			value = kv[i+1]
		}
		tail.WriteString(" " + key + "=" + quote(scrub(text(value))))
	}
	line := stamp(time.Now()) + " " + labels[level] + " " + l.name + "[" + strconv.Itoa(os.Getpid()) + "] " +
		fold(scrub(msg)) + tail.String()
	appendLine(ospath.Join(root, Dir, l.name+fileExt), line)
}

func stamp(now time.Time) string { return now.Format(stampLayout) }

// text は値を文字にする。真偽は true / false、nil は空（拡張の String と揃える）。
func text(value any) string {
	switch v := value.(type) {
	case nil:
		return ""
	case bool:
		if v {
			return "true"
		}
		return "false"
	}
	return pyfmt.Str(value)
}

func scrub(value string) string { return redact.Redact(MaskUserinfo(value)) }

// fold は改行（CR LF・CR・LF）を `\n` の 2 字にする。
func fold(value string) string {
	value = strings.ReplaceAll(value, "\r\n", "\n")
	value = strings.ReplaceAll(value, "\r", "\n")
	return strings.ReplaceAll(value, "\n", `\n`)
}

// quote は logfmt の値。空白・タブ・`"`・`=`・改行を含めば囲み、`\` と `"` の前に `\` を付ける。
func quote(value string) string {
	if !strings.ContainsAny(value, " \t\"=\n\r") {
		return value
	}
	escaped := strings.ReplaceAll(value, `\`, `\\`)
	escaped = strings.ReplaceAll(escaped, `"`, `\"`)
	return `"` + fold(escaped) + `"`
}

// appendLine は 1 行を追記モードで 1 度に書く（並行するプロセスの行と混ざらない）。
// logs・logs/diag・書き込み先のどれかがリンクなら書かない。リンク先へ追記すると置き場の外のファイル
// （logs/decisions.jsonl など）を書き換えるため。新しいファイルは 0600。
func appendLine(path, line string) {
	directory := ospath.Dirname(path)
	for _, place := range []string{ospath.Dirname(directory), directory} {
		if isLink(place) {
			return
		}
	}
	if noFollow == 0 && isLink(path) {
		return
	}
	flags := os.O_WRONLY | os.O_APPEND | os.O_CREATE | noFollow
	f, err := os.OpenFile(path, flags, 0o600)
	if os.IsNotExist(err) {
		if os.MkdirAll(directory, 0o777) != nil {
			return
		}
		f, err = os.OpenFile(path, flags, 0o600)
	}
	if err != nil {
		return
	}
	defer f.Close()
	_, _ = f.Write([]byte(line + "\n"))
}

func isLink(path string) bool {
	info, err := os.Lstat(path)
	return err == nil && info.Mode()&os.ModeSymlink != 0
}
