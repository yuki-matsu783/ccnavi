package digest

import (
	"crypto/rand"
	"crypto/sha1"
	"crypto/sha256"
	"encoding/hex"
	"hash"
	"io"
)

// Algo は要約の算法。
type Algo int

const (
	SHA1 Algo = iota
	SHA256
)

func (a Algo) new() hash.Hash {
	if a == SHA1 {
		return sha1.New()
	}
	return sha256.New()
}

// Hex は text（UTF-8）の要約の 16 進の頭 n 字。n が 0 以下か要約より長ければ全部。
func Hex(a Algo, text string, n int) string {
	h := a.new()
	h.Write([]byte(text))
	return head(hex.EncodeToString(h.Sum(nil)), n)
}

// HexOf は r の中身を最後まで読んだ要約の 16 進（全部）。読めなければ error。
func HexOf(a Algo, r io.Reader) (string, error) {
	h := a.new()
	if _, err := io.Copy(h, r); err != nil {
		return "", err
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}

func head(s string, n int) string {
	if n <= 0 || n >= len(s) {
		return s
	}
	return s[:n]
}

// UUIDBytes は uuid.uuid4().hex と同じ長さ（32 字）の乱数のバイト数。
const UUIDBytes = 16

// RandomHex は n バイトの乱数の 16 進（2n 字）。crypto/rand.Read は Go 1.24 から失敗を返さず、読めなければ
// プログラムを止める。そのため返すエラーは無く、捨てたエラーで同じ名前を作り続けることも無い。
func RandomHex(n int) string {
	b := make([]byte, n)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}
