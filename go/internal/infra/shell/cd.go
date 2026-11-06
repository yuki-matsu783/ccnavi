package shell

import (
	"regexp"
	"slices"
	"strings"
	"unicode/utf8"
)

// `cd` の行き先を後ろのコマンドの引数のパスに継ぎ足す。`cd .claude && echo x > settings.json` は
// 表記からディレクトリの名前が消え、保護がヒットしなくなるため。
//
// 継ぎ足した形は適用する先を足すだけで、text は動かさない（コマンドの頭に固定したルールが外れる）。
// 同じ理由で、コマンドの位置の語には継ぎ足さない。行き先が読めなくなったら継ぎ足すのをやめるだけで、
// 縮退はさせない（縮退すると `cd - && rm -f <守られた場所>` のように今止まっている形が通る）。

// Moved は `cd` で移った先から見たコマンドの層に付ける実行役の名前。runners に cd は無いので重ならない。
const Moved = "cd"

// MovedWords は 1 回の読みで継ぎ足す語数の上限。適用する先の総量を抑える。
const MovedWords = 2000

// hereLimit は居場所のパスの長さ（文字数）の上限。`cd a` を数千回並べると整理し直す時間が二乗になり、
// 判定の期限は読み終えたあとに見るので、読みの中で抑える。
const hereLimit = 256

var (
	// pushd は行き先を表記に持たない。
	pushd = newSet("pushd", "popd")
	// runsBuiltin の後ろの `cd` は cd そのもの。
	runsBuiltin = newSet("builtin", "command")
	cdOptions   = newSet("-L", "-P", "-e", "-@")
	// movesTo は中で実行されるコマンドの居場所を移すオプション。まとめ書き（`-iC /tmp`）は読まない。
	movesTo = map[string]set{"env": newSet("-C", "--chdir"), "sudo": newSet("-D", "--chdir")}
	// forked は左がサブシェルになる区切り。そこで移っても右と後ろには影響しない。
	forked = newSet("|", "|&", "&")
)

// expands は実行するときにシェルが中身を決める文字（変数・置換の `$`、グロブ）。
// コマンド名にも使う。`[` だけの test コマンドと `[[` はヒットしない。
var expands = regexp.MustCompile(`[$*?]|\[[^\]]*\]`)

// absolute は絶対パス。Windows のドライブ文字も絶対。
var absolute = regexp.MustCompile(`^(?:[\\/]|[A-Za-z]:[\\/])`)

// where は読みの起点から見た居場所。起点そのものは空文字。known が偽なら読めない。
type where struct {
	path  string
	known bool
}

var origin = where{known: true}

// Placed は外側のコマンド 1 本と、そのコマンドが居る場所。
type Placed struct {
	Words []string
	// Here は `cd` を追った先で、読みの起点から見たパス。起点そのものは空文字。
	Here string
	// Known が偽なら居場所が読めない（Here は空）。
	Known bool
}

type cdTracker struct {
	out    []string
	budget int
	places *[]Placed
}

// resolveCD は `cd` の行き先を継ぎ足したトークン列と、継ぎ足したかどうかを返す。
//
// サブシェル `( )` は出入りで居場所を戻し、`|` `|&` `&` の左はサブシェルなので後ろに残さない。
// `case` の枝の `)` はサブシェルの閉じと同じ表記なので、`case` から `esac` までは読まない（取りこぼす側）。
func resolveCD(tokens []string, places *[]Placed) ([]string, bool) {
	t := &cdTracker{budget: MovedWords, places: places}
	var segment []string
	here := origin
	// start は今のコマンド列（`;` `&&` `||` で区切られる単位）の頭で居た場所。
	start := origin
	var stack []where
	inCase := 0
	for _, token := range tokens {
		if !operators[token] {
			segment = append(segment, token)
			continue
		}
		here, inCase = t.flush(segment, here, inCase)
		segment = nil
		t.out = append(t.out, token)
		if inCase > 0 {
			continue
		}
		switch {
		case forked[token]:
			here = start
		case token == "(":
			stack = append(stack, here)
			start = here
		case token == ")":
			if n := len(stack); n > 0 {
				here = stack[n-1]
				stack = stack[:n-1]
			}
			start = here
		default:
			start = here
		}
	}
	t.flush(segment, here, inCase)
	changed := len(t.out) != len(tokens)
	for i := 0; !changed && i < len(tokens); i++ {
		changed = t.out[i] != tokens[i]
	}
	return t.out, changed
}

// flush はコマンド 1 本を足し、次のコマンドが居る場所と `case` の深さを返す。
func (t *cdTracker) flush(segment []string, here where, inCase int) (where, int) {
	if len(segment) > 0 && segment[0] == "case" {
		inCase++
	} else if len(segment) > 0 && segment[0] == "esac" && inCase > 0 {
		inCase--
	}
	if inCase > 0 {
		here = where{}
	}
	if t.places != nil && len(segment) > 0 {
		*t.places = append(*t.places, Placed{slices.Clone(segment), here.path, here.known})
	}
	return t.chdir(segment, here), inCase
}

// chdir はコマンド 1 本を、行き先を継ぎ足した形で足し、次のコマンドが居る場所を返す。
func (t *cdTracker) chdir(segment []string, here where) where {
	// 予約語の後ろはコマンドの先頭（`then rm x`）。`for` `case` `select` の後ろは変数名と値。
	i := 0
	for ; i < len(segment) && reserved[segment[i]]; i++ {
		t.out = append(t.out, segment[i])
		if takesAWord[segment[i]] {
			t.out = append(t.out, segment[i+1:]...)
			return here
		}
	}
	rest := segment[i:]
	if len(rest) == 0 {
		return here
	}
	k := NameIndex(rest)
	// `builtin cd x`・`command cd x` は cd そのもの。`command -v cd` は探すだけ。
	for k+1 < len(rest) && runsBuiltin[Base(rest[k])] && !strings.HasPrefix(rest[k+1], "-") {
		k++
	}
	name := ""
	if k < len(rest) {
		name = Base(rest[k])
	}
	t.out = append(t.out, rest...)
	switch {
	case name == "cd":
		// 行き先の語は書かれたまま残す。移った先は here が持つ。
		at := destination(rest, k)
		if at < 0 {
			return where{}
		}
		return target(here, rest[at])
	case pushd[name]:
		return where{}
	case !here.known || t.budget <= 0 || classify([][]string{rest}) != "":
		// 最後はコードとして実行する形。どの語がパスかが表記に無い。読み全体が縮退する。
		return here
	}
	// 居場所が動いていなくても、`env -C <パス>` はその 1 本だけ動かす。
	at, inside := insideRunner(rest, k, here.path)
	if !inside.known || inside.path == "" {
		return here
	}
	t.budget -= len(rest)
	t.out = t.out[:len(t.out)-len(rest)]
	t.out = append(t.out, withHere(rest, at, inside.path)...)
	return here
}

// insideRunner は実行役の中で実行されるコマンドの位置と、そこでの居場所。位置は RunnerCommand と
// 同じ読み方で出す（名前に継ぎ足すと意味の無い層になる）。`env -C` と `sudo --chdir` は居場所を移す。
func insideRunner(rest []string, k int, here string) (int, where) {
	for depth := 0; k < len(rest) && depth < UnwrapDepth; depth++ {
		name := Base(rest[k])
		if !IsRunner(name) {
			break
		}
		args := rest[k+1:]
		moved := runnerMovesTo(name, args, here)
		if !moved.known {
			return k, moved
		}
		here = moved.path
		inner := RunnerCommand(name, args)
		if len(inner) == 0 {
			break
		}
		k = len(rest) - len(inner)
	}
	return k, where{here, true}
}

// runnerMovesTo は実行役のオプションが移す先。移さないなら今の居場所のまま。
func runnerMovesTo(name string, args []string, here string) where {
	options := movesTo[name]
	if options == nil {
		return where{here, true}
	}
	for i := 0; i < len(args); {
		token := args[i]
		if token == "--" || !strings.HasPrefix(token, "-") || token == "-" {
			break
		}
		width := RunnerOptionWidth(name, token, args[i+1:])
		head, value, _ := strings.Cut(token, "=")
		if options[token] && width == 2 {
			value = args[i+1]
		} else if !(options[head] && value != "") {
			value = ""
		}
		if value != "" {
			return target(where{here, true}, value)
		}
		i += width
	}
	return where{here, true}
}

// withHere はコマンド 1 本の引数に居場所を継ぎ足す。継ぎ足すのは、リダイレクトの行き先（名前の前に
// 書いたものも）と、中で実行されるコマンドの名前より後ろの語。`>&` `<&` の後ろは fd の番号なので除く。
func withHere(rest []string, inner int, here string) []string {
	out := make([]string, 0, len(rest))
	for j := 0; j < len(rest); {
		width := RedirectWidth(rest, j)
		if width == 0 {
			if j > inner {
				out = append(out, under(here, rest[j]))
			} else {
				out = append(out, rest[j])
			}
			j++
			continue
		}
		end := min(j+width, len(rest))
		last := j + width - 1
		for m := j; m < end; m++ {
			if m == last && !strings.HasSuffix(rest[last-1], "&") {
				out = append(out, under(here, rest[m]))
			} else {
				out = append(out, rest[m])
			}
		}
		j = end
	}
	return out
}

// destination は `cd` の行き先の語の位置。読み切れなければ -1（引数なし、`cd -`、`cd old new`、
// 一覧に無いオプション、先頭の `~`、変数・置換・グロブ）。
func destination(rest []string, k int) int {
	var words []int
	for j := k + 1; j < len(rest); {
		if width := RedirectWidth(rest, j); width > 0 {
			j += width
			continue
		}
		words = append(words, j)
		j++
	}
	i := 0
	for i < len(words) && cdOptions[rest[words[i]]] {
		i++
	}
	if i < len(words) && rest[words[i]] == "--" {
		i++
	}
	words = words[i:]
	if len(words) != 1 {
		return -1
	}
	word := rest[words[0]]
	if word == "" || strings.HasPrefix(word, "-") || strings.HasPrefix(word, "~") || expands.MatchString(word) {
		return -1
	}
	return words[0]
}

// target は移る先を、読みの起点から見たパスにする。
func target(here where, word string) where {
	if expands.MatchString(word) || strings.HasPrefix(word, "~") {
		return where{}
	}
	if absolute.MatchString(word) {
		return capped(normal(word))
	}
	if !here.known {
		return where{}
	}
	if here.path != "" {
		word = here.path + "/" + word
	}
	return capped(normal(word))
}

func capped(path string) where {
	if utf8.RuneCountInString(path) > hereLimit {
		return where{}
	}
	return where{path, true}
}

// under はいま居る場所から見たパスを、読みの起点から見たパスに直す。絶対パスと `-` で始まる語は
// そのまま。`dd of=x` のように値にパスを取る語は値だけ継ぎ足す。
func under(here, word string) string {
	if here == "" || word == "" || absolute.MatchString(word) || strings.HasPrefix(word, "-") {
		return word
	}
	if IsAssignment(word) {
		head, value, _ := strings.Cut(word, "=")
		if value == "" || strings.HasPrefix(value, "-") || absolute.MatchString(value) {
			return word
		}
		return head + "=" + normal(here+"/"+value)
	}
	return normal(here + "/" + word)
}

// normal は `.` と `..` を整理する。区切りはどちらも受けて `/` で返す。先頭の区切り（絶対パス）と
// 末尾の区切り（ディレクトリと書いた形。保護の当たり方が変わる）は残す。起点より上に出る `..` も残す。
func normal(path string) string {
	var parts []string
	for _, part := range strings.FieldsFunc(path, func(r rune) bool { return r == '/' || r == '\\' }) {
		switch {
		case part == ".":
		case part == ".." && len(parts) > 0 && parts[len(parts)-1] != "..":
			parts = parts[:len(parts)-1]
		default:
			parts = append(parts, part)
		}
	}
	isSep := func(b byte) bool { return b == '/' || b == '\\' }
	lead, tail := "", ""
	if path != "" && isSep(path[0]) {
		lead = "/"
	}
	if path != "" && isSep(path[len(path)-1]) && len(parts) > 0 {
		tail = "/"
	}
	joined := lead + strings.Join(parts, "/")
	if joined == "" {
		joined = "."
	}
	return joined + tail
}
