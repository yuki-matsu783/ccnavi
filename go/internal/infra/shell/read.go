package shell

import (
	"errors"
	"strings"

	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
)

// 目印の文字。シェルなら一致がまたげない場所に置き、ルールの `[^\x00]*` が「同じコマンドの中」だけを
// 指せるようにする。実際のコマンドラインはこの文字を含められないので、prepare が入力から取り除いて
// なりすましを防ぐ。
const (
	// Sep はコマンドとコマンドの間。
	Sep = "\x00"
	// WordSep は引用が 2 語を 1 語につないだ場所と、語の中に入った演算子の文字の両側。
	// grep 'git push' が push として読まれず、grep -n "x>" f が `x > f` と同じ形にならない。
	WordSep = "\x01"
)

// Reason は読みを止めた理由。何が止めたかまで名指しして、読み手が直す先を分かるようにする。
type Reason string

const (
	ReasonUnterminated Reason = "unterminated-quote"
	// ReasonUnterminatedSubst は閉じない `$((` と、引用の外で閉じない `$(`。
	ReasonUnterminatedSubst Reason = "unterminated-substitution"
	// ReasonAmbiguousSubst はシェルによって答えが分かれる形と、深すぎる入れ子。
	ReasonAmbiguousSubst Reason = "ambiguous-substitution"
	ReasonBackquote      Reason = "backquote"
	// ReasonAnsiC は `\` を含む `$'…'`。展開しないと中身が決まらない。
	ReasonAnsiC Reason = "ansi-c-escape"
	// ReasonTakenAsCode は文字列をコードとして実行するコマンド（`sh -c`・`eval`・`xargs rm` など）。
	ReasonTakenAsCode Reason = "command-taken-as-code"
	// ReasonExpandedTarget は書き込みのリダイレクト先が変数か置換（`> $X`・`> "$(…)"`）。書く先は実行するまで決まらない。
	ReasonExpandedTarget Reason = "expanded-redirect-target"
)

// Form は書き直しを求める形。書き直す方法が必ずあり、読み分けると規則が増えるか、読み違えると
// 止まらずに通るもの。判定はこれがあればルールより先に止める。
type Form string

const (
	// FormBrace は引用の外のブレース展開（`{git,push}` は bash が git push と実行する）。
	FormBrace Form = "brace-expansion"
	// FormCommandName は実行するときにシェルが決めるコマンド名（`$c push`・`$(echo git) push`・`gi?`）。
	FormCommandName Form = "command-name-expansion"
	FormBackquote   Form = "backquote"
	// FormAmbiguous はシェルで読みが分かれる形と、作業で使わない予約語（coproc・select）。
	FormAmbiguous Form = "ambiguous-form"
)

// Rewrite は書き直しを求める形と、その表記。
type Rewrite struct {
	Form Form
	Text string
}

// Layer は実行役のコマンドを 1 枚外した層の付帯情報。Reading.Unwrapped と同じ順。
type Layer struct {
	// Runner はその層を実行する実行役の名前（`cd` で移った先なら Moved）。
	Runner string
	// Quoted は引用の中から切り出したコマンドの層か。文面の断りに使う。
	Quoted bool
}

// Reading は 1 回の読みの結果。
type Reading struct {
	// Text はシェルが実行しないもの（コメント・ヒアドキュメントの本文・引用のつなぎ目）を除いた形。
	// 置換の中身は外側の後ろに、独立したコマンドとしてつなぐ（置換の位置で挟むと、外側の
	// `find $(pwd) -name x -delete` が 2 本に割れる）。Degraded なら空。
	Text string
	// Degraded は読み切れなかったこと。呼び手は生の文字列に適用し、そうしたことを文面に書く。
	Degraded bool
	Reason   Reason
	// Bare は Text から、引用の中で切り出したコマンドを除いたもの。Text にヒットして Bare に
	// ヒットしなければ、書いた側は文字のつもりでいる。文面で回避策を言うためだけに使う。
	Bare string
	// Unwrapped は実行役のコマンドを 1 枚ずつ外した層を Sep でつないだもの。元の形は含めない。
	// 縮退していてもトークンに割れる限り作る（`sh -c` や `xargs` こそ中を見たい）。
	Unwrapped string
	Layers    []Layer
	// Moved は `cd` で移った先から見たコマンドのうち、表記の変わったものを Sep でつないだもの。
	// 止める側のルール（deny と ask）にだけ適用する。Degraded なら空。
	Moved string
	// Rewrites は書き直しを求める形。書かれた順で重なりは除く。縮退していても見つけたものは並ぶ。
	Rewrites []Rewrite
}

// Braces はブレース展開の表記。
func (r Reading) Braces() []string {
	var out []string
	for _, rw := range r.Rewrites {
		if rw.Form == FormBrace {
			out = append(out, rw.Text)
		}
	}
	return out
}

// maxDepth は置換の入れ子の上限。上限が無いと 1 本のコマンドで判定の期限を使い切れる。
const maxDepth = 16

// reasonForm は読みを止めた理由のうち、書き直しを求める形になるもの。
var reasonForm = map[Reason]Form{ReasonBackquote: FormBackquote, ReasonAmbiguousSubst: FormAmbiguous}

// ambiguousReserved はコマンドの位置で読みが分かれるか、作業で使わない予約語。
// `coproc NAME cmd` は bash 4 以降が NAME を名前と読み、zsh は実行する。`select` は入力を待つ。
var ambiguousReserved = newSet("coproc", "select")

// runnable は層を作る先のコマンド 1 本。quoted は引用の中から切り出したものか。
type runnable struct {
	words  []string
	quoted bool
}

// Read はコマンド文字列を、シェルが割るとおりに読む。
//
// 完全なシェルパーサではなく、回避しようとする相手に対する境界でもない。普通の作業のコマンドで、
// 語が実行されるのか書かれただけなのかを決める。決められなければ Degraded にして、呼び手が
// 厳しい側（生の文字列との一致）に切り替えられるようにする。
func Read(src string) Reading {
	reading, commands, full, fullOK := readAt(src, 0)
	var reread []Rewrite
	layers := unwrap(commands, &reread)
	rendered := make([]string, len(layers))
	reading.Layers = make([]Layer, len(layers))
	for i, l := range layers {
		rendered[i] = renderCommand(l.words)
		reading.Layers[i] = Layer{l.runner, l.quoted}
	}
	reading.Unwrapped = strings.Join(rendered, Sep)
	// コマンド名は層の先頭も見る（`env $c push` の `$c`）。`eval` と `sh -c` を読み直した層は、
	// 外側が縮退していれば見ない。確認になるので、止めて書き直させると中身がファイルに隠れる。
	names := expandedNames(append(wordsOf(commands), looked(layers, reading.Degraded)...))
	// 表示する名前は置換を全文で書いた形に差し替える。名前が有るかどうかが変わるなら差し替えない。
	if fullOK {
		if shown := expandedNames(append(wordsOf(full), looked(unwrap(full, new([]Rewrite)), reading.Degraded)...)); len(shown) > 0 && len(names) > 0 {
			names = shown
		}
	}
	all := append(append([]Rewrite(nil), reading.Rewrites...), reread...)
	for _, name := range names {
		all = append(all, Rewrite{FormCommandName, name})
	}
	reading.Rewrites = dedupe(all)
	return reading
}

func dedupe(items []Rewrite) []Rewrite {
	seen := map[Rewrite]bool{}
	out := make([]Rewrite, 0, len(items))
	for _, item := range items {
		if !seen[item] {
			seen[item] = true
			out = append(out, item)
		}
	}
	return out
}

func wordsOf(commands []runnable) [][]string {
	out := make([][]string, len(commands))
	for i, c := range commands {
		out[i] = c.words
	}
	return out
}

// PlacedCommands は外側のコマンド 1 本ずつと、`cd` を追ったそのコマンドの居場所。置換の中身は
// 並べない。読み切れない形なら nil（呼び手は Read の Degraded を先に見る）。
//
// rendered は src が Reading の層（Unwrapped・Moved）のとき真。層では引用の中の `<` `>` が WordSep で
// 挟まれて単独の語に見えるので、身代わりの文字に戻して演算子と読ませない。打たれたコマンドには使わない
// （入力の WordSep を戻すと演算子を隠せてしまう）。
func PlacedCommands(src string, rendered bool) []Placed {
	scanned, err := scan(prepare(src, rendered))
	if err != nil {
		return nil
	}
	tokens, err := tokenize(scanned.outer)
	if err != nil {
		return nil
	}
	places := []Placed{}
	resolveCD(tokens, &places)
	return places
}

// prepare は目印の文字を空白に戻し、行継続（改行の前の `\`）を消す。改行をコマンドの区切りに
// 読ませないため、走査より先に消す。単一引用の中の行継続は取りこぼすが、変わるのは引数の文字だけ。
func prepare(src string, rendered bool) string {
	for _, pair := range quotedBack {
		src = strings.ReplaceAll(src, pair[0], " ")
	}
	if rendered {
		for _, pair := range quotedBack {
			src = strings.ReplaceAll(src, WordSep+pair[1]+WordSep, pair[0])
		}
	}
	src = strings.NewReplacer(Sep, " ", WordSep, " ").Replace(src)
	src = strings.ReplaceAll(src, "\\\r\n", "")
	return strings.ReplaceAll(src, "\\\n", "")
}

// readAt は読みと、層を作る先のコマンド（外側が先、切り出した中身が後ろ）を返す。読み切れない形でも、
// トークンに割れる限りコマンドを返す。3 つめは外側だけを置換の全文で書いた同じリストで、作れれば fullOK。
func readAt(src string, depth int) (Reading, []runnable, []runnable, bool) {
	if depth > maxDepth {
		return stopped(&unreadable{ReasonAmbiguousSubst, formTooDeep}), nil, nil, false
	}
	scanned, err := scan(prepare(src, false))
	if err != nil {
		var u *unreadable
		errors.As(err, &u)
		return stopped(u), nil, nil, false
	}
	var rewrites []Rewrite
	for _, brace := range scanned.braces {
		rewrites = append(rewrites, Rewrite{FormBrace, brace})
	}
	tokens, err := tokenize(scanned.outer)
	if err != nil {
		// 走査は通ったのに shlex が閉じないと読む形（`$'…\'…'`）。
		return Reading{Degraded: true, Reason: ReasonUnterminated, Rewrites: rewrites}, nil, nil, false
	}
	movedTokens, moved := resolveCD(tokens, nil)
	commands := splitCommands(tokens)
	// `for select in` と `case coproc in` の語は変数名と値で、予約語ではない。
	var previous []string
	for _, command := range commands {
		if len(command) == 1 && ambiguousReserved[command[0]] && !(len(previous) == 1 && takesAWord[previous[0]]) {
			rewrites = append(rewrites, Rewrite{FormAmbiguous, command[0]})
		}
		previous = command
	}
	// 層を作る先は、読みを止める理由があっても集める。
	var runnables []runnable
	for _, command := range withTime(commands) {
		runnables = append(runnables, runnable{command, false})
	}
	type inner struct {
		reading Reading
		quoted  bool
	}
	var inners []inner
	for _, f := range scanned.found {
		r, innerCommands, _, _ := readAt(f.body, depth+1)
		inners = append(inners, inner{r, f.quoted})
		rewrites = append(rewrites, r.Rewrites...)
		for _, c := range innerCommands {
			runnables = append(runnables, runnable{c.words, f.quoted || c.quoted})
		}
	}
	full, fullOK := fullRunnables(scanned.full, commands, runnables)
	degraded := func(reason Reason) (Reading, []runnable, []runnable, bool) {
		return Reading{Degraded: true, Reason: reason, Rewrites: rewrites}, runnables, full, fullOK
	}

	heredocs := 0
	for _, t := range tokens {
		if t == "<<" || t == "<<-" {
			heredocs++
		}
	}
	if heredocs > scanned.heads {
		// 走査がヒアドキュメントと読まなかった `<<`（`grep -n "<<" f`）。演算子と区別が付かないので、
		// 閉じない本文として縮退する（許容している誤検知）。
		return degraded(ReasonUnterminated)
	}
	if why := classify(commands); why != "" {
		return degraded(why)
	}
	if expandedTarget(commands) {
		return degraded(ReasonExpandedTarget)
	}

	texts := []string{render(commands)}
	bares := []string{texts[0]}
	// 移った先は中身からも集める。中身の中で `cd` した先は中身に及ぶ（`echo "$(cd .claude && rm x)"`）。
	var moveds []string
	if moved {
		moveds = append(moveds, render(movedLayers(commands, movedTokens)))
	} else {
		moveds = append(moveds, "")
	}
	for _, in := range inners {
		if in.reading.Degraded {
			// 中身が 1 つでも読めなければ全体を読めないとする。
			return degraded(in.reading.Reason)
		}
		texts = append(texts, in.reading.Text)
		moveds = append(moveds, in.reading.Moved)
		if !in.quoted {
			bares = append(bares, in.reading.Bare)
		}
	}
	return Reading{
		Text:     pyfmt.JoinNonEmpty(Sep, texts...),
		Bare:     pyfmt.JoinNonEmpty(Sep, bares...),
		Moved:    pyfmt.JoinNonEmpty(Sep, moveds...),
		Rewrites: rewrites,
	}, runnables, full, fullOK
}

// fullRunnables は外側のコマンドを全文の形で組み直したリスト。語の数が揃わなければ作らない
// （そのときは placeholder の名前で見る）。
func fullRunnables(full string, commands [][]string, runnables []runnable) ([]runnable, bool) {
	tokens, err := tokenize(full)
	if err != nil {
		return nil, false
	}
	outer := splitCommands(tokens)
	if len(outer) != len(commands) {
		return nil, false
	}
	var out []runnable
	for _, command := range withTime(outer) {
		out = append(out, runnable{command, false})
	}
	return append(out, runnables[len(commands):]...), true
}

func stopped(e *unreadable) Reading {
	r := Reading{Degraded: true, Reason: e.reason}
	if form, ok := reasonForm[e.reason]; ok && e.form != "" {
		r.Rewrites = []Rewrite{{form, e.form}}
	}
	return r
}

// splitCommands は区切りでトークン列を切る。コマンドの位置の予約語はそれだけで 1 本にして、後ろを
// コマンドの先頭として読ませる（`then find . -delete`）。省かないのは `! grep x f` を `grep x f` と
// 読んで allow に適用しないため。
func splitCommands(tokens []string) [][]string {
	var commands [][]string
	var current []string
	for _, token := range tokens {
		if operators[token] {
			if len(current) > 0 {
				commands = append(commands, current)
				current = nil
			}
			continue
		}
		if len(current) == 0 && reserved[token] {
			commands = append(commands, []string{token})
			continue
		}
		current = append(current, token)
	}
	if len(current) > 0 {
		commands = append(commands, current)
	}
	return commands
}

// movedLayers は移った先から見たコマンドのうち表記の変わったものと、その中の層。層まで並べるのは、
// 止める側のルールの多くが名前を頭に固定して書かれているから（`sudo rm .claude/settings.json` には
// `(^|\x00)rm` がヒットしない）。語の数が対応しなければ何も返さない。
func movedLayers(commands [][]string, movedTokens []string) [][]string {
	resolved := splitCommands(movedTokens)
	if len(commands) != len(resolved) {
		return nil
	}
	var moved [][]string
	var runnables []runnable
	for i, after := range resolved {
		if !equalWords(commands[i], after) {
			moved = append(moved, after)
			runnables = append(runnables, runnable{after, false})
		}
	}
	for _, l := range unwrap(runnables, new([]Rewrite)) {
		moved = append(moved, l.words)
	}
	return moved
}

func equalWords(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

// withTime は 1 本に切った予約語の `time` を後ろのコマンドとつなぎ直す（`time -f %e rm x` の rm を
// 層の先頭に出すため）。後ろも残すので、つなぎ違えても層が増えるだけで止める側になる。
func withTime(commands [][]string) [][]string {
	out := make([][]string, 0, len(commands))
	for k, command := range commands {
		if len(command) == 1 && command[0] == "time" && k+1 < len(commands) {
			out = append(out, append([]string{"time"}, commands[k+1]...))
		} else {
			out = append(out, command)
		}
	}
	return out
}

// expandedNames はコマンドの位置にある、実行するときにシェルが決める語を並べる。
// `/usr/bin/gi?` はパスを除いた層にも `gi?` で現れるので、同じものを 2 度並べない。
func expandedNames(commands [][]string) []string {
	var found []string
	seen := map[string]bool{}
	var previous []string
	for _, command := range commands {
		name := ""
		if i := NameIndex(command); i < len(command) {
			name = command[i]
		}
		if len(previous) == 1 && takesAWord[previous[0]] {
			name = ""
		}
		if name != "" && expands.MatchString(name) && !seen[name] {
			found = append(found, name)
			seen[name] = true
			seen[Base(name)] = true
		}
		previous = command
	}
	return found
}

func render(commands [][]string) string {
	parts := make([]string, 0, len(commands))
	for _, command := range commands {
		if len(command) > 0 {
			parts = append(parts, renderCommand(command))
		}
	}
	return strings.Join(parts, Sep)
}

func renderCommand(command []string) string {
	parts := make([]string, len(command))
	for i, token := range command {
		parts[i] = renderWord(token)
	}
	return strings.Join(parts, " ")
}

// renderWord は語の中の空白（引用が置いたもの）を WordSep にし、演算子の文字を WordSep で挟む。
// 演算子のトークンはそのまま。
func renderWord(token string) string {
	if isOperator(token) {
		return token
	}
	var b strings.Builder
	for _, c := range token {
		switch {
		case ospath.IsSpace(c):
			b.WriteString(WordSep)
		case string(c) == quotedGT:
			b.WriteString(WordSep + ">" + WordSep)
		case string(c) == quotedLT:
			b.WriteString(WordSep + "<" + WordSep)
		case isPunct(c):
			b.WriteString(WordSep + string(c) + WordSep)
		default:
			b.WriteRune(c)
		}
	}
	return b.String()
}
