package pyfmt

import (
	"strings"
	"unicode"
)

// Lower は Python の str.lower()。strings.ToLower と答えが違う 2 つを合わせる。
// U+0130（İ）は Python では "i̇" の 2 字（Go は "i"）。語末の Σ（Final_Sigma）は Python では ς（Go は σ）。
func Lower(s string) string {
	return strings.ToLower(strings.ReplaceAll(finalSigma(s), "İ", "i̇"))
}

// finalSigma は語末の Σ を ς に替える。CPython の handle_capital_sigma と同じく、Σ の前の大小を問わない字を
// 飛ばした先が大小のある字で、後ろの大小を問わない字を飛ばした先が終わりか大小の無い字のとき語末とみる。
func finalSigma(s string) string {
	if !strings.ContainsRune(s, 'Σ') {
		return s
	}
	runes := []rune(s)
	out := append([]rune(nil), runes...)
	for i, r := range runes {
		if r != 'Σ' {
			continue
		}
		j := i - 1
		for j >= 0 && caseIgnorable(runes[j]) {
			j--
		}
		if j < 0 || !cased(runes[j]) {
			continue
		}
		k := i + 1
		for k < len(runes) && caseIgnorable(runes[k]) {
			k++
		}
		if k == len(runes) || !cased(runes[k]) {
			out[i] = 'ς'
		}
	}
	return string(out)
}

// cased は Unicode の Cased（Lu・Ll・Lt・Other_Lowercase・Other_Uppercase）。
func cased(r rune) bool {
	return unicode.In(r, unicode.Lu, unicode.Ll, unicode.Lt, unicode.Other_Lowercase, unicode.Other_Uppercase)
}

// caseIgnorable は Unicode の Case_Ignorable（Mn・Me・Cf・Lm・Sk と、Word_Break が MidLetter・MidNumLet・Single_Quote の字）。
func caseIgnorable(r rune) bool {
	switch r {
	case '\'', '.', ':', '\u00b7', '\u0387', '\u055f', '\u05f4', '\u2018', '\u2019', '\u2024', '\u2027', '\ufe13', '\ufe52', '\ufe55', '\uff07', '\uff0e', '\uff1a':
		return true
	}
	return unicode.In(r, unicode.Mn, unicode.Me, unicode.Cf, unicode.Lm, unicode.Sk)
}
