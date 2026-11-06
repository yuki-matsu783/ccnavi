package yaml

import (
	"errors"
	"fmt"
	"strings"
)

// sourceName は文字列から読んだ文書の名前。位置の文に出る（PyYAML と同じ）。
const sourceName = "<unicode string>"

// ErrUnreadable は読めない文書。構文の誤りも、値を組み立てる途中の失敗も、すべてこれで包む。
// 呼び手は errors.Is(err, ErrUnreadable) で「読めない文書」として扱う。
var ErrUnreadable = errors.New("YAML として読めない")

// ErrAlias は LoadRefusingAliases が別名（*名前）を見つけて断ったこと。ErrUnreadable でもある。
var ErrAlias = errors.New("別名")

// 値を組み立てる途中の失敗の文。Python は素の例外（ValueError など）を LoadError に包んでいた。
const (
	msgTooDeep     = "入れ子が深すぎる"
	msgUnbuildable = "値を組み立てられない"
)

// Mark は文書の中の位置。行と桁は 0 始まり（文に出すときは 1 を足す）。
type Mark struct {
	Line, Column int
	buffer       []rune
	pointer      int
}

// snippet は PyYAML の Mark.get_snippet。位置の行を抜き出し、下に ^ を付ける。
func (m Mark) snippet() string {
	const indent, maxLength = 4, 75
	isBreak := func(r rune) bool { return strings.ContainsRune("\x00\r\n\u0085\u2028\u2029", r) }
	head, tail := "", ""
	start := m.pointer
	for start > 0 && !isBreak(m.buffer[start-1]) {
		start--
		if float64(m.pointer-start) > maxLength/2.0-1 {
			head = " ... "
			start += 5
			break
		}
	}
	end := m.pointer
	for end < len(m.buffer) && !isBreak(m.buffer[end]) {
		end++
		if float64(end-m.pointer) > maxLength/2.0-1 {
			tail = " ... "
			end -= 5
			break
		}
	}
	pad := strings.Repeat(" ", indent)
	return pad + head + string(m.buffer[start:end]) + tail + "\n" +
		strings.Repeat(" ", indent+m.pointer-start+len(head)) + "^"
}

func (m Mark) String() string {
	return fmt.Sprintf("  in \"%s\", line %d, column %d:\n%s", sourceName, m.Line+1, m.Column+1, m.snippet())
}

// Error は読めない理由。Error() は PyYAML の str(exc) と同じ文（文脈・位置・問題・位置の順）。
// Problem と ProblemMark は、理由を 1 行にまとめたい呼び手（フローの検査）が使う。
type Error struct {
	Context     string
	ContextMark *Mark
	Problem     string
	ProblemMark *Mark
	text        string // 位置を持たない理由（文字の検査・組み立ての途中の失敗）
	cause       error
}

func (e *Error) Error() string {
	if e.text != "" {
		return e.text
	}
	var lines []string
	if e.Context != "" {
		lines = append(lines, e.Context)
	}
	if e.ContextMark != nil && (e.Problem == "" || e.ProblemMark == nil ||
		e.ContextMark.Line != e.ProblemMark.Line || e.ContextMark.Column != e.ProblemMark.Column) {
		lines = append(lines, e.ContextMark.String())
	}
	if e.Problem != "" {
		lines = append(lines, e.Problem)
	}
	if e.ProblemMark != nil {
		lines = append(lines, e.ProblemMark.String())
	}
	return strings.Join(lines, "\n")
}

// Is は、どの Error も ErrUnreadable として見せる。別名で断ったものは ErrAlias でもある。
func (e *Error) Is(target error) bool {
	return target == ErrUnreadable || (target == ErrAlias && e.cause == ErrAlias)
}

func (e *Error) Unwrap() error { return e.cause }

func markedError(context string, contextMark *Mark, problem string, problemMark *Mark) *Error {
	return &Error{Context: context, ContextMark: contextMark, Problem: problem, ProblemMark: problemMark}
}

func markPtr(m Mark) *Mark { return &m }

// buildError は値を組み立てる途中の失敗。kind は Python の例外の名前で、文に残す。
func buildError(kind, detail string) *Error {
	return &Error{text: fmt.Sprintf("%s (%s: %s)", msgUnbuildable, kind, detail)}
}

func tooDeep() *Error { return &Error{text: msgTooDeep} }
