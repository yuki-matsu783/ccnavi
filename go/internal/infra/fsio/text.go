package fsio

import (
	"ccnavi/internal/infra/ospath"

	"fmt"
	"strings"
)

// Undecodable は UTF-8 として読めないバイトの扱い。
type Undecodable int

const (
	// Strict は読めないものとして失敗にする（errors="strict"）。
	Strict Undecodable = iota
	// Replace は読めない並びを 1 つずつ U+FFFD にする（errors="replace"）。
	Replace
)

// DecodeError は UTF-8 として読めないバイト（文は Python の UnicodeDecodeError と同じ形）。
type DecodeError struct {
	Start, end int // 読めなかったバイトの範囲 [Start, end)
	byte       byte
	reason     string
}

func (e *DecodeError) Error() string {
	if e.end-e.Start == 1 {
		return fmt.Sprintf("'utf-8' codec can't decode byte 0x%02x in position %d: %s", e.byte, e.Start, e.reason)
	}
	return fmt.Sprintf("'utf-8' codec can't decode bytes in position %d-%d: %s", e.Start, e.end-1, e.reason)
}

// Decode は open(path, encoding="utf-8") が読むのと同じ本文を返す。改行の "\r\n" と "\r" は "\n" にそろう。
func Decode(content []byte, bad Undecodable) (string, error) {
	text, err := DecodeBytes(content, bad)
	if err != nil {
		return "", err
	}
	return ospath.UnifyNewlines(text), nil
}

// DecodeBytes は content.decode("utf-8", errors=bad)。Decode と違い、改行は読み替えない。
//
// 読めない並びの区切り方は CPython の UTF-8 の復号と同じにする（Replace で置く U+FFFD の数が変わるため）。
// Go の utf8 は 1 バイトずつ区切るので使わない。
func DecodeBytes(content []byte, bad Undecodable) (string, error) {
	var b strings.Builder
	b.Grow(len(content))
	for i := 0; i < len(content); {
		end, reason := utf8Sequence(content, i)
		if reason == "" {
			b.Write(content[i:end])
		} else if bad == Replace {
			b.WriteRune('�')
		} else {
			return "", &DecodeError{Start: i, end: end, byte: content[i], reason: reason}
		}
		i = end
	}
	return b.String(), nil
}

// utf8Sequence は i から始まる 1 文字の終わりと、読めなければその理由。
// 読めないときの終わりは、CPython が 1 つの誤りとして飛ばす範囲の終わり。
func utf8Sequence(b []byte, i int) (int, string) {
	c := b[i]
	if c < 0x80 {
		return i + 1, ""
	}
	need, lo, hi := 0, byte(0x80), byte(0xbf)
	switch {
	case c >= 0xc2 && c <= 0xdf:
		need = 1
	case c == 0xe0:
		need, lo = 2, 0xa0
	case c == 0xed:
		need, hi = 2, 0x9f
	case c >= 0xe1 && c <= 0xef:
		need = 2
	case c == 0xf0:
		need, lo = 3, 0x90
	case c == 0xf4:
		need, hi = 3, 0x8f
	case c >= 0xf1 && c <= 0xf3:
		need = 3
	default:
		return i + 1, "invalid start byte"
	}
	j := i + 1
	for k := 0; k < need; k++ {
		if j >= len(b) {
			return len(b), "unexpected end of data"
		}
		l, h := byte(0x80), byte(0xbf)
		if k == 0 {
			l, h = lo, hi
		}
		if b[j] < l || b[j] > h {
			return j, "invalid continuation byte"
		}
		j++
	}
	return j, ""
}

// Newline は本文を書くときの改行。
type Newline string

const (
	// NativeNewline は機械の改行（newline=None。Windows は CRLF）。ゼロ値。
	NativeNewline Newline = ""
	// LF は書き換えない（newline="" と newline="\n"）。
	LF Newline = "\n"
	// crlf は "\n" を "\r\n" にする。
	crlf Newline = "\r\n"
	// cr は "\n" を "\r" にする。
	cr Newline = "\r"
)

// onDisk は open(path, "w", encoding="utf-8", newline=nl) がディスクに書くバイト列。
func (nl Newline) onDisk(text string) []byte {
	if nl == NativeNewline {
		return []byte(strings.ReplaceAll(text, "\n", lineSep))
	}
	return nl.staged(text)
}

// staged は溜める段階に置く中身。NativeNewline は LF のまま残し、機械に依らず同じバイト列にする
// （Chrome のコミットと手元の plan が同じ答えになるように）。
func (nl Newline) staged(text string) []byte {
	if nl == crlf || nl == cr {
		text = strings.ReplaceAll(text, "\n", string(nl))
	}
	return []byte(text)
}
