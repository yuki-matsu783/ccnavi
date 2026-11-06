package useronly

import (
	"slices"
	"strings"

	"golang.org/x/text/cases"
	"golang.org/x/text/unicode/norm"

	"ccnavi/internal/infra/pyfmt"
)

// EntryError は `user` の入口の使い方の誤り（操作が無い・知らない操作・印の誤り）。文はそのまま利用者に見せる。
type EntryError struct{ msg string }

func (e *EntryError) Error() string { return e.msg }

func entryError(msg string) error { return &EntryError{msg} }

func isWrapperOption(word string) bool {
	if slices.Contains(wrapperOptions, word) {
		return true
	}
	head, _, ok := strings.Cut(word, "=")
	return ok && slices.Contains(wrapperOptions, head)
}

// asciiLower はコマンドの語を比べる形（ASCII の大文字だけを小文字にする）。casefold を使うと `ſ`（U+017F）が
// `s` に、`ﬀ` が `ff` になり、hook の式と実行ファイルの読みの片方だけが入口と読む綴りができる。ここで比べるのは
// パスではなくコマンドの語（argparse と同じく ASCII の綴り）なので、pathcase.Fold の決まりの外にある。
func asciiLower(word string) string {
	return strings.Map(func(r rune) rune {
		if 'A' <= r && r <= 'Z' {
			return r + 32
		}
		return r
	}, word)
}

var folder = cases.Fold()

// loose は互換の読み（NFKC と casefold）。ASCII でない綴りが入口の語に化けるかを見るためだけに使う。
func loose(word string) string { return folder.String(norm.NFKC.String(word)) }

func isASCII(s string) bool {
	for i := 0; i < len(s); i++ {
		if s[i] >= 0x80 {
			return false
		}
	}
	return true
}

// TakeEntry は先頭のラッパーのオプションから `--entry <値>` を外し、argv と値（無ければ空）を返す。
// ほかの位置の `--entry` は外さない（実行ファイルが知らないオプションとして断る）。
func TakeEntry(argv []string) ([]string, string, error) {
	var kept []string
	marker := ""
	at := 0
	for at < len(argv) && isWrapperOption(argv[at]) {
		word := argv[at]
		head, value, hasValue := strings.Cut(word, "=")
		if head != entryOption {
			step := 2
			if hasValue {
				step = 1
			}
			kept = append(kept, argv[at:min(at+step, len(argv))]...)
			at += step
			continue
		}
		if marker != "" {
			return nil, "", entryError(entryOption + " は 1 度しか渡せない")
		}
		switch {
		case hasValue:
			marker, at = value, at+1
		case at+1 < len(argv):
			marker, at = argv[at+1], at+2
		default:
			return nil, "", entryError(entryOption + " には値が要る")
		}
		if !slices.Contains(entryValues, marker) {
			return nil, "", entryError(entryOption + " に渡せるのは " + strings.Join(entryValues, " か "))
		}
	}
	if marker != "" && !(at < len(argv) && asciiLower(argv[at]) == userWord) {
		return nil, "", entryError(entryOption + " は user の入口と一緒に渡す")
	}
	out := slices.Clone(kept)
	if out == nil {
		out = []string{}
	}
	if at < len(argv) {
		out = append(out, argv[at:]...)
	}
	return out, marker, nil
}

// DirectRefusal は印の無い `user <操作>`（直に打った形）の断り文。script は打つ sh の呼び方。
func DirectRefusal(verb Verb, script string) string {
	return "`ccnavi user " + strings.Join(verb.Words, " ") + "` は、実行ファイルを直に打つ入口ではありません。" +
		"ユーザが `" + script + "` を打ちます（実行ファイルが行わない後始末は、その sh が行います）。"
}

// Rewrite は `user <操作> …` を内部の形に読み替える。`user` で始まらなければ argv と nil を返す。
// `user` はラッパーのオプションを読み飛ばした後の最初の語で、ASCII の大文字小文字は区別しない。先頭の `--` は
// 読み飛ばさない。`user` か操作の位置の ASCII でない語が、互換の読みで入口の語になるなら断る（`uſer`・`handoﬀ`）。
func Rewrite(argv []string) ([]string, *Verb, error) {
	at := 0
	for at < len(argv) && isWrapperOption(argv[at]) {
		if strings.Contains(argv[at], "=") {
			at++
		} else {
			at += 2
		}
	}
	if at >= len(argv) {
		return argv, nil, nil
	}
	if asciiLower(argv[at]) != userWord {
		if !isASCII(argv[at]) && loose(argv[at]) == userWord {
			return nil, nil, entryError("user の入口は ASCII の綴りで打つ（" + pyfmt.Repr(argv[at]) + " は受け付けない）")
		}
		return argv, nil, nil
	}
	var words []string
	for _, w := range argv[at+1 : min(at+3, len(argv))] {
		words = append(words, asciiLower(w))
	}
	if len(words) > 0 && !isASCII(words[0]) {
		return nil, nil, entryError("user の操作は ASCII の綴りで打つ（" + pyfmt.Repr(words[0]) + " は受け付けない）")
	}
	for i := range verbs {
		verb := &verbs[i]
		if len(words) < len(verb.Words) || !slices.Equal(words[:len(verb.Words)], verb.Words) {
			continue
		}
		rest := argv[at+1+len(verb.Words):]
		internal := slices.Clone(verb.Internal)
		if verb.Value {
			if len(rest) == 0 || strings.HasPrefix(rest[0], "-") {
				return nil, nil, entryError("user " + strings.Join(verb.Words, " ") + " には値が要る")
			}
			internal = append(internal, rest[0])
			rest = rest[1:]
		}
		out := slices.Concat(argv[:at], internal, rest)
		v := *verb
		return out, &v, nil
	}
	shown := make([]string, len(verbs))
	for i, v := range verbs {
		shown[i] = strings.Join(v.Words, " ")
	}
	return nil, nil, entryError("user の操作は " + strings.Join(shown, " / ") + " のどれか")
}

// Refused は `user` の外でユーザ専用のオプションが読まれたときの断り文。問題が無ければ空。
// given は、実行ファイルが読んだ結果でそのオプションが立っているか（値が空・偽・無しなら偽）。argv の文字列を
// 探すと、`--keyword=--agree` のような他のオプションの値まで断るので、読んだ結果で見る。
func Refused(given func(option string) bool) string {
	// `--yes` はどの操作にも付くので最後に見る。
	var order []option
	for _, f := range flags {
		if f.gated && f.name != optYes {
			order = append(order, f)
		}
	}
	for _, f := range flags {
		if f.gated && f.name == optYes {
			order = append(order, f)
		}
	}
	for _, f := range order {
		if !given(f.name) || f.unlessPreview && given(optPreview) {
			continue
		}
		for _, v := range verbs {
			if v.Internal[len(v.Internal)-1] != f.name {
				continue
			}
			text := f.name + " はユーザ専用です。ユーザが `" + UserScriptPrefix + v.Script + ".sh` で打ちます。"
			if f.unlessPreview {
				text += "見るだけなら " + f.name + " --preview を使います。"
			}
			return text
		}
		return f.name + " はユーザ専用です（ユーザが `" + UserScriptPrefix + "*.sh` で打ちます）。"
	}
	return ""
}
