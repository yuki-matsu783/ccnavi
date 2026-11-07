package digest

import "strings"

// URLSafeB64Decode は URL 向けの base64 を読む。足りないパディングを足してから、検めない形（Python の a2b_base64）で読む。
// 表に無い字とパディングは読み飛ばし、終わりで半端な桁が残れば読めない（false）。半端な 2・3 桁は、その後ろの
// パディングで 4 桁にそろえば読める（途中のパディングでは読み終えない）。ASCII でない文字列も読めない。
func URLSafeB64Decode(s string) ([]byte, bool) {
	for i := 0; i < len(s); i++ {
		if s[i] >= 0x80 {
			return nil, false // ASCII でない文字列は読まない
		}
	}
	s = strings.NewReplacer("-", "+", "_", "/").Replace(s)
	s += strings.Repeat("=", (4-len(s)%4)%4)
	var out []byte
	quad, pads := 0, 0
	var left byte
	for i := 0; i < len(s); i++ {
		c := s[i]
		if c == '=' {
			pads++
			continue
		}
		v, ok := b64Value(c)
		if !ok {
			continue
		}
		pads = 0
		switch quad {
		case 0:
			quad, left = 1, v
		case 1:
			quad = 2
			out = append(out, left<<2|v>>4)
			left = v & 0x0f
		case 2:
			quad = 3
			out = append(out, left<<4|v>>2)
			left = v & 0x03
		case 3:
			quad = 0
			out = append(out, left<<6|v)
			left = 0
		}
	}
	return out, quad == 0 || quad >= 2 && quad+pads >= 4
}

func b64Value(c byte) (byte, bool) {
	switch {
	case c >= 'A' && c <= 'Z':
		return c - 'A', true
	case c >= 'a' && c <= 'z':
		return c - 'a' + 26, true
	case c >= '0' && c <= '9':
		return c - '0' + 52, true
	case c == '+':
		return 62, true
	case c == '/':
		return 63, true
	}
	return 0, false
}
