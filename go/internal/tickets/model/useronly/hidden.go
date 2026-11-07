package useronly

import (
	"regexp"
	"slices"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyre"
	"ccnavi/internal/infra/shell"
	"ccnavi/internal/policy/texts"
)

// HiddenUserEntry が見るのは、ユーザがターミナルで打つ入口のうち、引数を読み切れない形。HumanPathForm と
// TicketApprovalRule の script 節は文字として並んだ表記で見るので、引数を変数・置換・glob で組み立てると外れる。
// 次の 2 つは 2 段目（実行ファイルのターミナル要求）が無く、hook が唯一の保護なので、止める操作を組み立てうるなら
// 止める。組み立てられない形（読むだけの形）は通す。
//
//   - `ccnavi-review.sh decide`: `--choices` になりうる語があれば止める。文字どおりの `--preview` があれば通す
//   - 実行ファイルの `--agree` / `--reviewed` と `--yes`、ターミナル要求を切る `--guard-ticket-approval`。展開を
//     含む語より前に診断のオプションか実行ファイルの知らないオプションがあれば通す（承認の経路に進まない）
//
// 展開と glob は、引用されていない部分と二重引用符の中（glob は引用されていない部分）だけで数える。語は
// コマンドの位置から見る。前に置けるのはシェル・busybox・`.`・シェルの選択肢・リダイレクト・代入だけ。
// 引数を外から流し込む形（`xargs`、入口を `<` で読ませる形）は、入口の名前が出た時点で止める。標準入力から
// 読むシェルがあれば、入口の名前が実行される語として現れるだけで止める（流し込む文字列は語として読めない）。

// Layer は実行役のコマンドを外した層 1 枚（shell.Reading の Layers の Runner と、Unwrapped を切った 1 本）。
// `cd` の先から見た層は Runner が shell.Moved。
type Layer struct {
	Runner string
	Text   string
}

const (
	entryExe = "ccnavi"
	// stdinShell は、標準入力から読むシェルへ入口を流し込む形で見つけた語の代わりに返す印。
	stdinShell = "標準入力から読むシェル"
)

var (
	entryExes = []string{entryExe, "ccnavi.exe", "ccnavi.__main__", config.LauncherScript}
	// entryPrefix は入口の前に置けるコマンド。どれも後ろの語をそのまま実行する。
	entryPrefix = []string{"sh", "bash", "dash", "zsh", "ksh", "ash", "busybox", ".", "source", "exec", "command"}
	// toolRunners は後ろの語を実行する Python のツールの実行役。間の選択肢と値は読み分けない。
	toolRunners = []string{"uv", "uvx", "pipx", "poetry", "pipenv"}
	stdinShells = []string{"sh", "bash", "dash", "zsh", "ksh", "ash"}
	feeders     = []string{"xargs", "parallel"}
	// execPrefix は実行される語の前に置けるもの（入口の前置きのほか、後ろの語を実行するコマンド）。
	execPrefix   = slices.Concat(entryPrefix, []string{"env", "nice", "nohup", "time", "sudo", "timeout"})
	namedScripts = []string{config.ReviewWrapper, config.LauncherScript}
	// exeDiagnoses は実行ファイルの診断のオプション。どれも承認の経路より前に分かれて、承認を行わない。
	exeDiagnoses = []string{"--docs", "--lint", "--test", "--test-expectations", "--explain", "--suggest", "--version", "--help", "-h"}
)

// exeOptions は実行ファイルが受けるオプション（短い形を含む）。ここに無いオプションは実行ファイルが使い方の
// 誤りとして断り、何も動かさない。オプションを足したらここにも足す（入口の段のテストが引数の読み手と比べる）。
var exeOptions = []string{
	"--abandon", "--accept-check", "--accept-done", "--accept-plan",
	"--accept-unresolved", "--actor", "--agent-id", "--agree", "--all", "--approved",
	"--body-file", "--check", "--check-file",
	"--choose-out", "--close-early", "--cwd", "--digest", "--docs", "--draft",
	"--expect",
	"--explain", "--feedback", "--fill-phases", "--flow", "--format", "--guard-core-files",
	"--guard-ticket-approval", "--handoff", "--help", "--issue",
	"--json",
	"--keyword", "--layer", "--learnings", "--limit", "--lint", "--log", "--mirror",
	"--mode", "--mr",
	"--name",
	"--no-issue",
	"--no-refresh",
	"--note", "--order",
	"--path",
	"--permission-mode", "--phase", "--phases", "--placed-out", "--plan", "--plan-order",
	"--preview",
	"--project-home", "--project-phases-file", "--project-risk-file", "--project-rules-file",
	"--projects", "--prune", "--reason", "--record-tree", "--record-writes",
	"--restore-if-deny", "--result", "--reverse", "--reviewed", "--risk", "--root", "--rules",
	"--scope", "--show", "--signal", "--since", "--sort", "--state", "--suggest", "--tag",
	"--target",
	"--template", "--templates",
	"--test",
	"--test-expectations",
	"--text", "--ticket-control", "--tickets", "--title", "--to", "--type", "--until",
	"--verify",
	"--version", "--via", "--with-feedback", "--write", "--yes", "-h", "-r",
}

// ExeOptions は exeOptions の写しを返す（呼び手が書き換えても元は変わらない）。
func ExeOptions() []string { return slices.Clone(exeOptions) }

var (
	approvalFlags = names("", groupApproval)
	yesFlags      = names("", groupYes)
	importFlags   = names("", groupHandoff)
	guardFlags    = names("", groupGuard)
	abandonFlags  = names("", groupAbandon)
	choicesFlags  = names(reviewSh, groupChoices)
)

// 展開・置換・glob・find -exec の置き場。どれもシェルか実行役が実行の時点で値を決める。ASCII の記号だけを
// 見るので RE2 で読む。
var (
	openRx     = regexp.MustCompile(`[$` + "`" + `*?\[]|\{\}`)
	openPartRx = regexp.MustCompile(`\$\{[^}]*\}?|\$\([^)]*\)?|\$[A-Za-z0-9_@*#?$!-]*|` + "`[^`]*`?" + `|\[[^\]]*\]?|\{\}|[*?]`)
	// stringSegment は流し込む文字列をコマンドの切れ目と引用符で切る。引用した文字列は 1 本のコマンド行として読む。
	stringSegment = regexp.MustCompile(`[;&|\n()` + "`" + `'"]+`)
)

// literalMarks は単引用符の中と、glob を引用した文字の印。文字どおりの値として読むため、走査の前に置き換える。
var literalMarks = map[rune]rune{'$': '', '`': '', '*': '', '?': '', '[': ''}

const globMarks = "*?["

func unmark(s string) string {
	return strings.Map(func(r rune) rune {
		for ch, mark := range literalMarks {
			if r == mark {
				return ch
			}
		}
		return r
	}, s)
}

// baseName は語の最後の要素を小文字で。
func baseName(word string) string { return pyfmt.Lower(lastPart(word)) }

func isOpen(word string) bool { return openRx.MatchString(word) }

// couldBe は語が実行の時点で flags のどれか（`=` で値を付けた形を含む）になりうるか。
func couldBe(word string, flags []string) bool {
	if !isOpen(word) {
		for _, f := range flags {
			if word == f || strings.HasPrefix(word, f+"=") {
				return true
			}
		}
		return false
	}
	var pattern strings.Builder
	last := 0
	for _, loc := range openPartRx.FindAllStringIndex(word, -1) {
		pattern.WriteString(pyre.Escape(word[last:loc[0]]))
		part := word[loc[0]:loc[1]]
		if part == "?" || strings.HasPrefix(part, "[") {
			pattern.WriteString(".")
		} else {
			pattern.WriteString(".*")
		}
		last = loc[1]
	}
	pattern.WriteString(pyre.Escape(word[last:]))
	re, err := pyre.Compile(pattern.String(), pyre.IgnoreCase|pyre.DotAll)
	if err != nil {
		// 語から組む式は文字を逃がしてあるので読めないことは無い。読めなければなりうる側に倒す。
		return true
	}
	for _, f := range flags {
		if re.FullMatch(f) || re.FullMatch(f+"=x") {
			return true
		}
	}
	return false
}

// dropRedirects はリダイレクト（演算子と行き先）を除いた引数。行き先は入口に渡らない。
func dropRedirects(args []string) []string {
	var kept []string
	for k := 0; k < len(args); {
		if width := shell.RedirectWidth(args, k); width > 0 {
			k += width
			continue
		}
		kept = append(kept, args[k])
		k++
	}
	return kept
}

// literalQuoted は単引用符の中の展開と glob、二重引用符の中と `\` の後の glob を、文字どおりの印に置き換える。
// 読み切れない形（置換・ヒアドキュメント・`$'…'`・閉じない引用符）は置き換えずに返す（止める側に倒す）。
func literalQuoted(subject string) string {
	if strings.Contains(subject, "$(") || strings.Contains(subject, "`") || strings.Contains(subject, "<<") || strings.Contains(subject, "$'") {
		return subject
	}
	// 1 字ずつ読み、引用符の状態（quote）に応じて glob や展開の字を「文字どおりの印」に置き換える。
	src := []rune(subject)
	var out []rune
	quote := rune(0)
	for i := 0; i < len(src); i++ {
		ch := src[i]
		switch {
		// 単引用符の中は、すべての字が文字どおり。
		case quote == '\'':
			if ch == '\'' {
				quote = 0
				out = append(out, ch)
			} else if m, ok := literalMarks[ch]; ok {
				out = append(out, m)
			} else {
				out = append(out, ch)
			}
		// `\` の次の字は引用符の外でも文字どおり。
		case ch == '\\' && i+1 < len(src):
			following := src[i+1]
			out = append(out, ch)
			if strings.ContainsRune(globMarks+"$`", following) {
				out = append(out, literalMarks[following])
			} else {
				out = append(out, following)
			}
			i++
		// 二重引用符の中は、展開は効くが glob は効かない。
		case quote == '"':
			if ch == '"' {
				quote = 0
			}
			if strings.ContainsRune(globMarks, ch) {
				out = append(out, literalMarks[ch])
			} else {
				out = append(out, ch)
			}
		default:
			if ch == '\'' || ch == '"' {
				quote = ch
			}
			out = append(out, ch)
		}
	}
	// 引用符が閉じていない。読み違えるより、置き換えずに返して止める側に倒す。
	if quote != 0 {
		return subject
	}
	return string(out)
}

func entryKind(word, binBase string) string {
	base := baseName(word)
	if base == config.ReviewWrapper {
		return base
	}
	if slices.Contains(entryExes, base) || binBase != "" && base == binBase {
		return entryExe
	}
	return ""
}

// atCommandPosition は words[i] がコマンドの位置か。前に置けるのは入口の前置き・選択肢・リダイレクト・代入だけ。
func atCommandPosition(words []string, i int) bool {
	if i > 0 && words[i-1] == "-m" {
		return true
	}
	j := 0
	for j < i {
		if width := shell.RedirectWidth(words, j); width > 0 {
			j += width
			continue
		}
		word := words[j]
		base := baseName(word)
		if slices.Contains(toolRunners, base) {
			return true
		}
		if word == "-o" || word == "+o" {
			j += 2
			continue
		}
		if strings.HasPrefix(word, "-") || strings.HasPrefix(word, "+") || slices.Contains(entryPrefix, base) || shell.IsAssignment(word) {
			j++
			continue
		}
		return false
	}
	return j == i
}

// firstAfterWrapperOptions はラッパーが先に付ける値つきのオプションを読み飛ばした後の最初の語。
func firstAfterWrapperOptions(args []string) string {
	at := 0
	for at < len(args) {
		word := args[at]
		head, _, hasValue := strings.Cut(word, "=")
		if slices.Contains(wrapperOptions, word) {
			at += 2
		} else if hasValue && slices.Contains(wrapperOptions, head) {
			at++
		} else {
			break
		}
	}
	if at < len(args) {
		return args[at]
	}
	return ""
}

// hiddenArgs は入口の引数のうち、止める理由になる語。無ければ空。文字として並んだ `--choices` と `--digest` の
// 組み合わせは HumanPathForm が先に止める。
func hiddenArgs(kind string, args []string) string {
	args = dropRedirects(args)
	if kind == config.ReviewWrapper {
		first := ""
		if len(args) > 0 {
			first = args[0]
		}
		deciding := isOpen(first) && couldBe(first, []string{"decide"})
		for _, a := range args {
			deciding = deciding || pyfmt.Lower(a) == "decide"
		}
		if !deciding || slices.Contains(args, optPreview) || !slices.ContainsFunc(args, isOpen) {
			return ""
		}
		for _, a := range args {
			if isOpen(a) && couldBe(a, choicesFlags) {
				return a
			}
		}
		return ""
	}
	if !slices.ContainsFunc(args, isOpen) {
		return ""
	}
	if first := firstAfterWrapperOptions(args); first != "" && isOpen(first) && couldBe(first, []string{userWord}) {
		return first
	}
	// 展開を含む語より前の、文字どおりのオプション（`--` の後はオプションではない）。
	given := map[string]bool{}
	for _, a := range args {
		if isOpen(a) || a == "--" {
			break
		}
		if strings.HasPrefix(a, "-") && a != "-" {
			head, _, _ := strings.Cut(a, "=")
			given[head] = true
		}
	}
	for f := range given {
		if slices.Contains(exeDiagnoses, f) || !slices.Contains(exeOptions, f) {
			return ""
		}
	}
	if given["--agree"] && given[optPreview] {
		return ""
	}
	var opened []string
	for _, a := range args {
		if isOpen(a) {
			opened = append(opened, a)
		}
	}
	for _, a := range opened {
		if couldBe(a, guardFlags) {
			return a
		}
	}
	if abandon := abandonWord(args, opened); abandon != "" {
		return abandon
	}
	// 下書きの取り込み（`--handoff` と `--yes`）も、承認と同じく組み合わせを読めない形にして隠せる。
	acting := slices.Concat(approvalFlags, importFlags)
	if slices.ContainsFunc(args, func(a string) bool { return couldBe(a, acting) }) &&
		slices.ContainsFunc(args, func(a string) bool { return couldBe(a, yesFlags) }) {
		all := slices.Concat(acting, yesFlags)
		for _, a := range opened {
			if couldBe(a, all) {
				return a
			}
		}
		// 展開を含む語がどれにもなりえず、文字どおりの語だけで揃った形（`--agree --yes [a]`）。
		// 揃えた文字どおりの語で止める側に倒す。
		for _, a := range args {
			if couldBe(a, all) {
				return a
			}
		}
	}
	return ""
}

// abandonWord は `worktree retire --abandon`（放棄した親の記録を消す操作）を組み立てうる語。文字どおりの
// `worktree retire` は cliForms が止める。ここはサブコマンドか `--abandon` を変数・glob で組み立てて外す形。
func abandonWord(args, opened []string) string {
	at := slices.Index(args, "worktree")
	if at < 0 {
		return ""
	}
	for _, a := range opened {
		if couldBe(a, abandonFlags) {
			return a
		}
	}
	if verb := at + 1; verb < len(args) && isOpen(args[verb]) && couldBe(args[verb], []string{"retire"}) {
		return args[verb]
	}
	return ""
}

// markedSuffix は、層の語が外側のコマンドの後ろ側と同じなら、単引用の印の残った外側の語を返す。実行役を外した
// 層（`sh <入口> '$X'` の `<入口> $X`）は引用を外して組み直してあるので、単引用の中の `$X` も展開に見えるため。
// `sh -c '…'` の文字列を読み直した層は並びが合わないので、そのまま（止める側）。
func markedSuffix(words []string, placed [][]string) []string {
	for _, outer := range placed {
		if len(outer) <= len(words) {
			continue
		}
		tail := outer[len(outer)-len(words):]
		same := true
		for i, w := range tail {
			if unmark(w) != words[i] {
				same = false
				break
			}
		}
		if same {
			return tail
		}
	}
	return words
}

type entryCommand struct {
	runner string
	words  []string
}

// entryCommands は入口を探すコマンドの並びと、コマンドの位置を見るか。読めなければ生の文字列の語で、
// 位置は見ない（止める側に倒す）。
func entryCommands(subject string, inner []Layer) ([]entryCommand, bool) {
	var placed [][]string
	for _, p := range shell.PlacedCommands(literalQuoted(subject), false) {
		placed = append(placed, p.Words)
	}
	if len(placed) > 0 {
		var out []entryCommand
		for _, words := range placed {
			out = append(out, entryCommand{"", words})
		}
		for _, layer := range inner {
			if layer.Text == "" {
				continue
			}
			var words []string
			for _, w := range strings.Split(layer.Text, " ") {
				if w != "" {
					words = append(words, strings.ReplaceAll(w, shell.WordSep, " "))
				}
			}
			out = append(out, entryCommand{layer.Runner, markedSuffix(words, placed)})
		}
		return out, true
	}
	text := strings.Map(func(r rune) rune {
		if r == '\'' || r == '"' || r == '\\' {
			return -1
		}
		return r
	}, flat(subject))
	if onlyReaders(text) {
		return nil, false
	}
	var out []entryCommand
	for _, segment := range segmentSep.Split(text, -1) {
		out = append(out, entryCommand{"", ospath.Fields(segment)})
	}
	return out, false
}

// HiddenUserEntry は、ユーザがターミナルで打つ入口に読み切れない引数を渡す形があれば、入口と見つけた語を返す。
// 無ければ空の 2 つ。inner は実行役のコマンドを外した層（`sh -c '…'` の中や `xargs` が実行するコマンド）。
// binPath は実行ファイルのパス（名前を変えた実体も入口と読む）。
func HiddenUserEntry(subject string, inner []Layer, binPath string) (string, string) {
	entry, word := hiddenEntry(subject, inner, binPath)
	return entry, unmark(word)
}

func hiddenEntry(subject string, inner []Layer, binPath string) (string, string) {
	binBase := ""
	if binPath != "" {
		binBase = baseName(binPath)
	}
	found, positional := entryCommands(subject, inner)
	readsStdin := false
	named := ""
	for _, c := range found {
		words := c.words
		readsStdin = readsStdin || positional && shell.ReadsStdin(words)
		for i, word := range words {
			kind := entryKind(word, binBase)
			if kind == "" {
				continue
			}
			// 標準入力から読むシェルと並べて数える名前。sh の入口はファイルとして渡すだけで中身が流れ込む。
			// 実行ファイルの名前は実行される語として立つときだけ数える（`cd /home/user/ccnavi` は数えない）。
			if kind != entryExe || baseName(word) == config.LauncherScript || !positional || executedEntry(words[:i+1], binBase) != "" {
				if named == "" {
					named = kind
				}
			}
			if i > 0 && strings.HasSuffix(words[i-1], "<") {
				return kind, "< " + word
			}
			if positional && !atCommandPosition(words, i) {
				continue
			}
			feeder := ""
			if slices.Contains(feeders, c.runner) {
				feeder = c.runner
			}
			if !positional {
				for _, w := range words[:i] {
					if slices.Contains(feeders, baseName(w)) {
						feeder = w
						break
					}
				}
			}
			if feeder != "" {
				return kind, feeder
			}
			if hidden := hiddenArgs(kind, words[i+1:]); hidden != "" {
				return kind, hidden
			}
		}
	}
	if readsStdin && named == "" {
		named = namedInText(subject, binBase)
	}
	if readsStdin && named != "" {
		return named, stdinShell
	}
	return "", ""
}

// namedInText は、コマンド行の文字列の中で入口の名前が実行される語として現れれば、その入口。流し込む文字列は
// 語として読めないので、コマンドの切れ目と引用符で切り、それぞれの頭の語を見る。
func namedInText(subject, binBase string) string {
	for _, segment := range stringSegment.Split(strings.ReplaceAll(subject, shell.Sep, "\n"), -1) {
		if found := executedEntry(ospath.Fields(segment), binBase); found != "" {
			return found
		}
	}
	return ""
}

// executedEntry は語の並びの頭（代入・実行役・シェル・`env` などの前置きを飛ばした先）が入口なら、その入口。
// シェルの `-s` / `-c` の後ろは引数か文字列で、実行される語ではない。
func executedEntry(words []string, binBase string) string {
	// 頭から前置き（代入・実行役・インタプリタ・オプション）を読み飛ばし、実際に実行される最初の語まで進む。
	shellSeen := false
	k := 0
	for ; k < len(words); k++ {
		word := words[k]
		base := baseName(word)
		if shell.IsAssignment(word) {
			continue
		}
		if slices.Contains(toolRunners, base) || base == "run" && k > 0 && slices.Contains(toolRunners, baseName(words[k-1])) {
			continue
		}
		if slices.Contains(stdinShells, base) {
			shellSeen = true
			continue
		}
		if slices.Contains(execPrefix, base) || base == "-m" || strings.HasPrefix(base, "python") || base == "py" || base == "py.exe" {
			continue
		}
		if strings.HasPrefix(word, "-") || strings.HasPrefix(word, "+") {
			if shellSeen && !strings.HasPrefix(word, "--") && strings.ContainsAny(word[1:], "sc") {
				return ""
			}
			continue
		}
		break
	}
	// 前置きだけで語が尽きた。実行される語は無い。
	if k >= len(words) {
		return ""
	}
	// 実行される語が入口の名前か。シェルを経由した（`sh x`）後ろの語は、入口として実行されるとは限らないので実行ファイルとは見ない。
	base := baseName(words[k])
	if slices.Contains(namedScripts, base) {
		if base == config.LauncherScript {
			return entryExe
		}
		return base
	}
	if !shellSeen && (slices.Contains(entryExes, base) || binBase != "" && base == binBase) {
		return entryExe
	}
	return ""
}

const (
	textHiddenStdin = "DENY_TICKET_APPROVAL_CLI__HIDDEN_STDIN"
	textHiddenFeed  = "DENY_TICKET_APPROVAL_CLI__HIDDEN_FEED"
	textHiddenEntry = "DENY_TICKET_APPROVAL_CLI__HIDDEN_ENTRY"
)

// HiddenEntryMessage は HiddenUserEntry が見つけた形で止めた文。
func HiddenEntryMessage(root, entry, found string) string {
	values := texts.Values{"entry": entry, valueFound: found}
	if found == stdinShell {
		return texts.Say(root, textHiddenStdin, texts.Values{"entry": entry}, nil)
	}
	if slices.Contains(feeders, found) || strings.HasPrefix(found, "< ") {
		return texts.Say(root, textHiddenFeed, values, nil)
	}
	kind := "other"
	switch entry {
	case config.ReviewWrapper:
		kind = "review"
	case entryExe:
		kind = "exe"
	}
	return texts.Say(root, textHiddenEntry, values, texts.Variants{"what": kind, "next": kind})
}
