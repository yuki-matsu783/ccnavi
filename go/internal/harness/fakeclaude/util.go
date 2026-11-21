package fakeclaude

import (
	"bytes"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"strings"

	"ccnavi/internal/infra/ospath"
)

// uuid4 は 8-4-4-4-12 の乱数の ID。
func uuid4() string {
	b := make([]byte, 16)
	_, _ = rand.Read(b)
	b[6] = b[6]&0x0f | 0x40
	b[8] = b[8]&0x3f | 0x80
	h := hex.EncodeToString(b)
	return h[:8] + "-" + h[8:12] + "-" + h[12:16] + "-" + h[16:20] + "-" + h[20:]
}

// hex24 は 24 桁の 16 進（`toolu_` の後ろ）。
func hex24() string { return strings.ReplaceAll(uuid4(), "-", "")[:24] }

// hex17 は 17 桁の 16 進（サブエージェントの ID）。
func hex17() string { return strings.ReplaceAll(uuid4(), "-", "")[:17] }

// marshal は JSON の 1 行。HTML の記号（< > &）も日本語もそのまま出す（Python の ensure_ascii=False と同じ）。
func marshal(v any) string {
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	_ = enc.Encode(v)
	return strings.TrimSuffix(buf.String(), "\n")
}

// decode は数を json.Number のまま読む（整数と小数の違いを形に残すため）。
func decode(text string) (any, error) {
	dec := json.NewDecoder(strings.NewReader(text))
	dec.UseNumber()
	var v any
	if err := dec.Decode(&v); err != nil {
		return nil, err
	}
	return v, nil
}

type object = map[string]any

func asObject(v any) object { o, _ := v.(map[string]any); return o }
func asList(v any) []any    { l, _ := v.([]any); return l }
func asString(v any) string { s, _ := v.(string); return s }

// environ は `NAME=value` の並びの読み書き。Windows は名前の大文字小文字を区別しない。
type environ []string

func sameName(a, b string) bool {
	if ospath.Windows {
		return strings.EqualFold(a, b)
	}
	return a == b
}

func (e environ) get(name string) (string, bool) {
	for _, kv := range e {
		if n, v, _ := strings.Cut(kv, "="); sameName(n, name) {
			return v, true
		}
	}
	return "", false
}

func (e environ) set(name, value string) environ {
	out := e.without(name)
	return append(out, name+"="+value)
}

func (e environ) without(name string) environ {
	out := make(environ, 0, len(e)+1)
	for _, kv := range e {
		if n, _, _ := strings.Cut(kv, "="); !sameName(n, name) {
			out = append(out, kv)
		}
	}
	return out
}
