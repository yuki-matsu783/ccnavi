package tally

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"sort"
	"strings"

	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/records/audit"
)

// 止められたエージェントは言い回しを少し変えて打ち直すことがある。同じルールが同じ呼び出しを止めた
// 回数を数え、N 回目から拒否の文面に「言い換えずに相談する」一文を足し、ターンの終わりにも報告する。
//
// 数えるキーは（ルールの id, 均した対象のハッシュ）。均すのは引用符を除くことと空白をまとめることだけで、
// 大文字小文字は分けたまま（パスもコマンドも大小を区別する環境がある）。記録にはハッシュだけを置く
// （認証情報を残さないため）。

// DefaultDenyThreshold は N の既定（CCNAVI_DENY_REPEAT）。
const DefaultDenyThreshold = 3

// minDenyThreshold より小さい N は既定に戻す。1 は 1 回目の拒否が案内する代わりの方法を試す前に止まる。
const minDenyThreshold = 2

const (
	denyPrefix = "denied-"
	jsonExt    = ".json"
	// keyHashLen はキーに使うハッシュの長さ（16 進の字数）。
	keyHashLen = 16
	// noRule は理由の名前が無いときの名前。
	noRule = "(deny)"
)

// DenyThreshold は書かれた N。読めない値と 2 未満は既定。
func DenyThreshold(written string) int {
	if n, ok := pyInt(written); ok && n >= minDenyThreshold {
		return n
	}
	return DefaultDenyThreshold
}

// Normalize は同じ呼び出しとして数えるための均し（引用符を除き、空白をまとめる）。
func Normalize(subject string) string {
	subject = strings.Map(func(r rune) rune {
		if r == '\'' || r == '"' || r == '`' {
			return -1
		}
		return r
	}, subject)
	return strings.Join(strings.FieldsFunc(subject, ospath.IsSpace), " ")
}

// RuleOf は止めた理由の名前。判定を下したルールの id、無ければ根拠の種別。
func RuleOf(r *audit.Record) string {
	if len(r.Rules) > 0 {
		return r.Rules[0]
	}
	if r.Code != "" {
		return r.Code
	}
	return noRule
}

func keyOf(rule, subject string) string {
	digest := sha256.Sum256([]byte(Normalize(subject)))
	return rule + " " + hex.EncodeToString(digest[:])[:keyHashLen]
}

func denyPath(stateDir, session string) string {
	return ospath.Join(stateDir, denyPrefix+fsio.SafeName(session, fsio.SafeNameLimit)+jsonExt)
}

// CountDeny はこの拒否を数え、同じキーのこれまでの回数（今回を含む）を返す。数えられなければ 0。
// state の置き場が無い（--test）ときも、セッションが無いときも数えない（別のセッションの拒否と混ぜて、
// 打ち直していない相手に「言い換えるな」と言わないため）。
func CountDeny(stateDir string, r *audit.Record) int {
	if stateDir == "" || r.Subject == "" || r.Session == "" {
		return 0
	}
	path := denyPath(stateDir, r.Session)
	data, ok := fsio.ReadObject(path)
	if !ok {
		data = pyjson.NewObject()
	}
	rule := RuleOf(r)
	key := keyOf(rule, r.Subject)
	entry, isObject := data.Value(key).(*pyjson.Object)
	if !isObject {
		entry = pyjson.NewObject("rule", rule, "tool", r.Tool, "count", 0, "told", 0)
	}
	count, ok := recordedInt(entry.Value("count"))
	if !ok {
		return 0
	}
	count++
	entry.Set("count", count)
	data.Set(key, entry)
	if fsio.WriteJSON(path, data) != "" {
		return 0
	}
	return count
}

// DenyNote は N 回目から拒否の文面の末尾に足す一文。
func DenyNote(rule string, n int) string {
	return fmt.Sprintf("[ccnavi] 同じ理由（%s）でこの呼び出しを %d 回止めました。", rule, n) +
		"言い換えて再実行せず、何をしたいのかと止まった理由をユーザに伝えて相談してください。"
}

const denyReportHead = "[ccnavi] このセッションで、同じ理由で同じ呼び出しを繰り返し止めました。" +
	"言い換えて同じ呼び出しを繰り返していないか、ユーザの判断が必要な場面ではないかを確かめてください。"

// DenyReport はターンの終わりにユーザへ言うこと。N 回に達したキーのうち、まだ言っていない回数のもの。
// 言った回数は記録に残す。書けなければ言わない（覚えられないまま言うと、ターンの終わりごとに同じ行が並ぶ）。
func DenyReport(stateDir, session string, n int) string {
	if stateDir == "" || session == "" {
		return ""
	}
	path := denyPath(stateDir, session)
	data, ok := fsio.ReadObject(path)
	if !ok || data.Len() == 0 {
		return ""
	}
	var lines []string
	for _, key := range data.Keys() {
		entry, isObject := data.Value(key).(*pyjson.Object)
		if !isObject {
			continue
		}
		got, ok := recordedInt(entry.Value("count"))
		if !ok {
			return ""
		}
		if got < n {
			continue
		}
		told, ok := recordedInt(entry.Value("told"))
		if !ok {
			return ""
		}
		if got <= told {
			continue
		}
		lines = append(lines, fmt.Sprintf("  %s（%s）: %d 回", orText(entry.Value("rule"), noRule), orText(entry.Value("tool"), "?"), got))
		entry.Set("told", got)
	}
	if len(lines) == 0 || fsio.WriteJSON(path, data) != "" {
		return ""
	}
	sort.Strings(lines)
	return strings.Join(append([]string{denyReportHead}, lines...), "\n")
}

// orText は Python の f"{v or fallback}"。
func orText(v any, fallback string) string {
	if !pyfmt.Truthy(v) {
		return fallback
	}
	return pyfmt.Str(v)
}
