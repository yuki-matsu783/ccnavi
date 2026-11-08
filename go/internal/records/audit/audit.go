package audit

import (
	"math"
	"strconv"
	"time"
	"unicode/utf8"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/records/redact"
)

// Decision は呼び出しに何が起きたか。
type Decision string

const (
	Allow Decision = "allow"
	Ask   Decision = "ask"
	Deny  Decision = "deny"
	// Skip は判定に至らなかった。必ず Reason が付くので「判定しなかった」と「判定して何も無かった」を分けられる。
	Skip Decision = "skip"
	// Handover は判定を Claude Code の権限モードへ渡した。
	// Ask と分けるのは、ルールが言及していない呼び出しの総量（ルールを足すべきか）と、誰かが実際に判断した回（ガードが効いたか）を別々に数えるため。
	Handover Decision = "handover"
	// Nudge はターンの終わりの促し。Deny の集計（止めた呼び出しの数）に混ぜない。
	Nudge Decision = "nudge"
)

// Reason は判定に至らなかった理由。実行後チェックだけが出す理由は、その段が自分で持つ。
// ルールが読めないことは理由にせず、組み込みの既定で判定して Fallback に残す。
// 拒否にすると、壊れたファイルを直す呼び出しまで止まって、戻れなくなるため。
type Reason string

const (
	ReasonModeDisabled     Reason = "mode-disabled"
	ReasonEventNotChecked  Reason = "event-not-checked"
	ReasonNoSubject        Reason = "no-subject"
	ReasonNothingToRun     Reason = "nothing-to-run" // コメントだけの行など、実行される部分が無い
	ReasonPayloadUnusable  Reason = "payload-unusable"
	ReasonDeadlineExceeded Reason = "deadline-exceeded"
	// ReasonInternalError は ccnavi 自身の失敗。例外の型と場所は Detail に入れる。
	ReasonInternalError Reason = "internal-error"
)

const (
	// recordedSubjectLimit は記録に残すコマンドやパスの上限（字数）。ヒアドキュメントでファイル 1 本の中身を
	// ヒアドキュメントでファイル 1 本の中身を書けるため、1 回の呼び出しで記録が膨らまないようにする。
	recordedSubjectLimit = 1000
	// redactLimit は、伏せる前に切る長さ。上限の手前で始まって上限をまたぐ値も伏せられるだけの余りを持たせる。
	// どれだけ長くても、伏せる手間が実行前チェックの期限に届かないようにするため。
	redactLimit = recordedSubjectLimit * 4
)

// Record は記録の 1 行。欄の意味は記録を読む側（--test・集計）との約束。
type Record struct {
	Mode config.Mode
	// PermissionMode は、呼び出しが来たときの Claude Code の権限モード。ルールが言及しない呼び出しの結末は、これで変わる。
	// 残さないと、同じ Subject に別の結末が並んだ理由を説明できないため残す。
	PermissionMode string
	Event          string
	Tool           string
	Subject        string
	Decision       Decision
	// Enforced は、その判定を適用したか。warn のときは、deny に達しても偽のまま残す。
	// 止めるはずだったものと実際に止めたものの両方に答えるため。
	Enforced bool
	// Code は判定の根拠の種別（拒否の文の先頭と同じ）。
	Code   string
	Reason Reason
	// Degraded はコマンドを読み切れずに生の文字列で判定したときの原因。
	Degraded string
	// Unwrapped は実行役（env・sudo・sh -c）の中のコマンドにヒットしたときのそのコマンド。複数なら NUL でつなぐ。
	Unwrapped string
	// Hinted は additionalContextOnce の案内を渡した回（行には true のときだけ書く）。
	Hinted bool
	// Fallback はルールファイルを読めずに組み込みの既定で判定したこと。
	Fallback string
	// Detail は Reason だけでは言えないこと（読めなかったファイルのパスなど）。切らずに伏せる。
	Detail string
	Rules  []string
	// Quoted は Rules のうち、引用の中から切り出したコマンドにだけヒットしたもの。
	Quoted []string
	// Paths は実行後チェックが保護領域の中に見つけた変更。
	Paths []string
	// Guarded は ccnavi 自身の設定ファイルについてこの 1 回で何をしたか。
	Guarded []string
	Session string
	// Agent はサブエージェントの id（メインなら空）。行には書かない。失敗の連続をエージェントごとに数える。
	Agent string
	// Tree と Project は呼び出しがどのツリーのものと判定されたか（ワークスペースなら Project は空）。
	Tree    string
	Project string
	// Source は判定を下したルールのレイヤー（common・self・プロジェクトの名前）。
	Source string
	// Hits は判定が適用したルールそのもの（Rules の末尾と同じ順）。読むのは診断（--test）だけで行には書かない。
	// 型を決めないのは、記録の段から判定の段（policy）へ依存を張らないため。
	Hits []any
}

// Log は記録をファイルに追記する。パスが空なら書かない（診断の実行が痕跡を残さないように）。
type Log struct {
	path  string
	start time.Time
}

// NewLog は path に書く Log。経過時間はここから測る。
func NewLog(path string) *Log { return &Log{path: path, start: time.Now()} }

// Write は 1 件を追記する。時刻と経過時間はここで埋める。失敗は返すが、呼び手は判定を変えない。
// 記録の 1 行を失うほうが、帳簿の都合で実行の可否が決まるより害が小さいため。
func (l *Log) Write(r *Record) error {
	if l.path == "" {
		return nil
	}
	line, err := pyjson.Dumps(l.object(r), pyjson.Options{RawUnicode: true})
	if err != nil {
		return err
	}
	if directory := ospath.Dirname(l.path); directory != "" {
		if err := fsio.MakeDirs(directory); err != nil {
			return err
		}
	}
	// 同じイベントの hook は同時に走るので、1 行を追記モードの 1 回の書き込みで出す（2 件が混ざらない）。
	// 行末は機械の改行になる（Windows では CRLF）。読む側は行ごとに JSON として読むので、どちらでも通る。
	return fsio.AppendLine(l.path, line, 0o600)
}

// object は 1 行の欄。空の欄は省く（Decision と Enforced は空でも入れる）。
func (l *Log) object(r *Record) *pyjson.Object {
	// 切る前に伏せる（上限で値の途中が切れると形が崩れて伏せられない）。伏せる前にも redactLimit で切る。
	subject := limited(redact.Redact(pyfmt.Head(r.Subject, redactLimit)), runeLen(r.Subject))
	unwrapped := limited(redact.Redact(pyfmt.Head(r.Unwrapped, redactLimit)), runeLen(r.Unwrapped))
	detail := redact.Redact(r.Detail)
	elapsed := float64(time.Since(l.start)) / float64(time.Millisecond)

	out := pyjson.NewObject("ts", fsio.ISOFormat(time.Now()), "mode", string(r.Mode))
	putText(out, "permission_mode", r.PermissionMode)
	putText(out, "event", r.Event)
	putText(out, "tool", r.Tool)
	putText(out, "subject", subject)
	out.Set("decision", string(r.Decision))
	out.Set("enforced", r.Enforced)
	putText(out, "code", r.Code)
	putText(out, "reason", string(r.Reason))
	putText(out, "degraded", r.Degraded)
	putText(out, "unwrapped", unwrapped)
	putText(out, "fallback", r.Fallback)
	putText(out, "detail", detail)
	putText(out, "tree", r.Tree)
	putText(out, "project", r.Project)
	putText(out, "source", r.Source)
	if r.Hinted {
		out.Set("hinted", true)
	}
	putList(out, "rules", r.Rules)
	putList(out, "quoted", r.Quoted)
	putList(out, "paths", r.Paths)
	putList(out, "guarded", r.Guarded)
	putText(out, "session", r.Session)
	out.Set("ms", math.Round(elapsed*1000)/1000)
	return out
}

func putText(o *pyjson.Object, key, value string) {
	if value != "" {
		o.Set(key, value)
	}
}

func putList(o *pyjson.Object, key string, values []string) {
	if len(values) > 0 {
		o.Set(key, values)
	}
}

// limited は上限で切り、切ったら残りの長さを付ける。whole は伏せる前に切る前の長さ。
func limited(text string, whole int) string {
	rest := max(runeLen(text), whole) - recordedSubjectLimit
	if rest <= 0 {
		return text
	}
	return pyfmt.Head(text, recordedSubjectLimit) + "…(+" + strconv.Itoa(rest) + ")"
}

func runeLen(s string) int { return utf8.RuneCountInString(s) }
