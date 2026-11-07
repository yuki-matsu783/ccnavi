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

// New は塊を順に書いて要約する口。書き終えたら HexSum で 16 進にする。
func New(a Algo) hash.Hash {
	if a == SHA1 {
		return sha1.New()
	}
	return sha256.New()
}

// HexSum は h に書いたものの要約の 16 進（全部）。
func HexSum(h hash.Hash) string {
	return hex.EncodeToString(h.Sum(nil))
}

// Hex は text（UTF-8）の要約の 16 進の頭 n 字。n が 0 以下か要約より長ければ全部。
func Hex(a Algo, text string, n int) string {
	return HexBytes(a, []byte(text), n)
}

// HexBytes はバイト列の要約の 16 進の頭 n 字。n の扱いは Hex と同じ。
func HexBytes(a Algo, b []byte, n int) string {
	h := New(a)
	h.Write(b)
	return head(HexSum(h), n)
}

// HexOf は r の中身を最後まで読んだ要約の 16 進（全部）。読めなければ error。
func HexOf(a Algo, r io.Reader) (string, error) {
	h := New(a)
	if _, err := io.Copy(h, r); err != nil {
		return "", err
	}
	return HexSum(h), nil
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
