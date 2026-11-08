package askcontext

import (
	"slices"
	"strings"
	"time"
	"unicode/utf8"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/pyre"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/policy/texts"
)

const (
	Code   = "DENY_ASK_NEEDS_CONTEXT"
	RuleID = rules.ReservedIDPrefix + "ask-context"
)

// section は直前の発言に要る節。name は文面（deny.yml）が言う名前で、words はその節として数える見出しの言い方。
type section struct {
	name  string
	words []string
}

var sections = []section{
	{"前提・背景", []string{"前提・背景", "背景", "前提"}},
	{"想定結果", []string{"想定結果", "選んだ時の結果"}},
}

const (
	// tailBytes は会話記録の末尾から読む長さ。現在のステップの発言はこの中に収まる。
	tailBytes    = 512 * 1024
	pollInterval = 100 * time.Millisecond
)

// heading は行頭の見出し。`## 背景`・`### 1. 背景`・`**背景**`・`【背景】`・`背景:` が当たり、`背景を説明すると…` は外れる。
func heading(words []string) *pyre.Regexp {
	sorted := slices.Clone(words)
	// 長い言い方を先に試す（長さの降順。同じ長さは元の並びを保つ）。短い語が先に当たって、長い語の途中で切れるのを防ぐ。
	slices.SortStableFunc(sorted, func(a, b string) int {
		return utf8.RuneCountInString(b) - utf8.RuneCountInString(a)
	})
	escaped := make([]string, len(sorted))
	for i, w := range sorted {
		escaped[i] = pyre.Escape(w)
	}
	return pyre.MustCompile(`^[ \t　#>*\-・【\[]*(?:\d+[.)．][ \t　]*)?(?:`+strings.Join(escaped, "|")+`)[*】\]]*(?:[:：]|[ \t\r　]*$)`, pyre.Multiline)
}

var patterns = func() []*pyre.Regexp {
	out := make([]*pyre.Regexp, len(sections))
	for i, s := range sections {
		out[i] = heading(s.words)
	}
	return out
}()

// tailLines は会話記録の末尾の行。途中から読んだときの先頭の切れた行は除く。区切りは改行だけ（splitlines は U+2028
// などでも割り、JSON の行を壊す）。
func tailLines(path string) ([]string, error) {
	var lines []string
	err := fsio.ScanTail(path, tailBytes, tailBytes, 2, func(tail []byte, _ bool) bool {
		text, _ := fsio.DecodeBytes(tail, fsio.Replace)
		lines = strings.Split(text, "\n")
		return true
	})
	return lines, err
}

func isToolResult(row *pyjson.Object) bool {
	message, _ := row.Value("message").(*pyjson.Object)
	content, ok := message.Value("content").([]any)
	if !ok {
		return false
	}
	for _, block := range content {
		if b, ok := block.(*pyjson.Object); ok && b.Value("type") == "tool_result" {
			return true
		}
	}
	return false
}

// stepText は最後のユーザの発言より後ろの、アシスタントの text を連ねたものと、書き込み済みかどうか。
//
// 書き込み済みは、この呼び出し自身の tool_use の行が記録にあること。text の行はその行より前に書かれる。区切るのは
// ユーザの発言（プロンプト）だけで、ツールの結果の行は区切らない（見出しを書いた発言のあとに別のツールを呼んで確認を
// 出しても、同じ発言の中の見出しを数える）。サイドチェーン（サブエージェント）とメタの行は読み飛ばす。
func stepText(path, toolUseID string) (string, bool, error) {
	lines, err := tailLines(path)
	if err != nil {
		return "", false, err
	}
	var found []string
	written := false
	// 記録を末尾から遡り、最後のユーザの発言まで（その発言より後）のアシスタントの text を集める。
	for i := len(lines) - 1; i >= 0; i-- {
		data, err := pyjson.Loads(lines[i])
		if err != nil {
			continue
		}
		row, ok := data.(*pyjson.Object)
		if !ok || pyfmt.Truthy(row.Value("isSidechain")) || pyfmt.Truthy(row.Value("isMeta")) {
			continue
		}
		kind := row.Value("type")
		if kind == "user" {
			// ツールの結果は user の行で書かれるが、ユーザの発言ではない。区切りにしない。
			if isToolResult(row) {
				continue
			}
			break
		}
		if kind != "assistant" {
			continue
		}
		message, _ := row.Value("message").(*pyjson.Object)
		content, ok := message.Value("content").([]any)
		if !ok {
			continue
		}
		for _, block := range content {
			b, ok := block.(*pyjson.Object)
			if !ok {
				continue
			}
			switch b.Value("type") {
			case "tool_use":
				if b.Value("id") == toolUseID {
					written = true
				}
			case "text":
				if text, ok := b.Value("text").(string); ok {
					found = append(found, text)
				}
			}
		}
	}
	return strings.Join(found, "\n"), written, nil
}

// absentSections は text に見出しが無い節の名前。
func absentSections(text string) []string {
	var out []string
	for i, s := range sections {
		if !patterns[i].Search(text) {
			out = append(out, s.name)
		}
	}
	return out
}

// missing は直前の発言に無い節。判定できないときは空（検査しない）。自分の行があって見出しが無ければすぐ返す。
// 自分の行がまだ無いあいだは until まで読み直す。
func missing(path, toolUseID string, until time.Time) []string {
	if path == "" || toolUseID == "" {
		return nil
	}
	for {
		text, written, err := stepText(path, toolUseID)
		if err != nil {
			return nil
		}
		absent := absentSections(text)
		if len(absent) == 0 {
			return nil
		}
		if written {
			return absent
		}
		if !time.Now().Before(until) {
			return nil
		}
		time.Sleep(pollInterval)
	}
}

// StopText は確認の前に止める文。直前の発言に節が揃っているか、判定できなければ空。
//
// 待つ上限は hook の判定の期限（HookDeadline）に縛らない。期限はルール照合を打ち切るためのもので、この待ちはその
// 前後の短い読み取りだけだから。上限は hook の timeout（settings.json の `timeout`、既定 10 秒）より短くする。
func StopText(root, transcriptPath, toolUseID string) string {
	absent := missing(transcriptPath, toolUseID, time.Now().Add(config.AskContextWait.Value()))
	if len(absent) == 0 {
		return ""
	}
	quoted := make([]string, len(absent))
	for i, name := range absent {
		quoted[i] = "「" + name + "」"
	}
	return texts.Say(root, Code, texts.Values{"missing": strings.Join(quoted, "・")}, nil)
}
