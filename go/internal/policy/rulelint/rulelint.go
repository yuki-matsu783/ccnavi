package rulelint

import (
	"fmt"
	"regexp"
	"slices"
	"strconv"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/policy/ctxfile"
	"ccnavi/internal/policy/rules"
)

// Options は検査の対象の性質。
type Options struct {
	// Home はルールが指すファイル（additionalContextFile・{script:…}）を探す起点。レイヤーのルールなら
	// そのレイヤーの git プロジェクトルート。空ならワークスペースルート、それも無ければルールファイルの隣。
	Home string
	// Layer は共通レイヤーより後ろのレイヤー（共通に足すものなので、deny や allow が空でも穴にならない）。
	Layer bool
	// Project はプロジェクトのレイヤー（ターンの終わりのルールを読まない）。
	Project bool
}

// FileProblems はルールファイルを判定と同じ読み方で読んで検査する。
func FileProblems(path, root string, o Options) []rules.Problem {
	home := o.Home
	if home == "" {
		home = root
	}
	if home == "" {
		home = ospath.Dirname(ospath.Abspath(path))
	}
	rs, loaded, err := rules.Load(path, root)
	if err != nil {
		// block のモードではこれがそのまま全ツールの拒否になり、このファイルを直す呼び出しも止まる。
		return []rules.Problem{{Severity: rules.Error, Rule: "(rules)", Detail: "ルールを読めない: " + err.Error()}}
	}
	problems := slices.Clone(loaded)
	if len(rs.Deny) == 0 && !o.Layer {
		problems = append(problems, rules.Problem{Severity: rules.Error, Rule: "(rules)",
			Detail: "`deny` が空。何も止めないガードは、入れていないのと同じなのに、入っているように見える"})
	}
	if len(rs.Allow) == 0 && !o.Layer {
		// どの呼び出しも ccnavi の判定を受けずに権限モードへ渡る。動いてはいるが、外からは何も言わない
		// ガードと見分けが付かない。
		problems = append(problems, rules.Problem{Severity: rules.Warn, Rule: "(rules)",
			Detail: "`allow` が空。どのルールも言及しない呼び出しは、すべて Claude Code の権限モードに従うことになる"})
	}
	seen := map[string]bool{}
	for _, rule := range rs.All() {
		if seen[rule.ID] {
			problems = append(problems, rules.Problem{Severity: rules.Warn, Rule: rule.ID,
				Detail: "id が重複している。どちらのルールがヒットしたかを記録から辿れない"})
		}
		seen[rule.ID] = true
		problems = append(problems, ruleProblems(rule, rule.ID, home, o.Project)...)
	}
	return problems
}

func warn(name, detail string) rules.Problem {
	return rules.Problem{Severity: rules.Warn, Rule: name, Detail: detail}
}

func fail(name, detail string) rules.Problem {
	return rules.Problem{Severity: rules.Error, Rule: name, Detail: detail}
}

// scriptsDir はルールの文面の sh の置き場（ワークスペースルートからの相対）。
var scriptsDir = []string{config.DefaultProjectHome, config.ScriptsDir}

func ruleProblems(rule *rules.Rule, name, home string, project bool) []rules.Problem {
	var problems []rules.Problem
	for _, tool := range inert(rule.Match) {
		problems = append(problems, warn(name, "match の "+tool+" には適用する対象がありません。何も止まらない"))
	}
	problems = append(problems, stopProblems(rule, name, project)...)

	if rule.Message != "" && rule.Decision != rules.Deny {
		// ask の文面は確認の画面にしか出ず、allow の文面はどこにも出ない。書いたユーザはモデルに届くと
		// 思って書くので、届かない欄を残さない。
		where := "どこにも届かない"
		if rule.Decision == rules.Ask {
			where = "ユーザの確認ダイアログにしか出ない"
		}
		problems = append(problems, fail(name, string(rule.Decision)+" に message がある。"+where+
			"ので、モデルに渡す文は additionalContext に書き、message は削除してください"))
	}

	texts := []struct{ field, text string }{
		{"message", rule.Message},
		{"additionalContext", rule.AdditionalContext},
		{"additionalContextOnce", rule.AdditionalContextOnce},
	}
	// 文面の sh は {root} から書く。相対の `.ccnavi/scripts/...` は cwd がプロジェクトの中だと見つからない。
	for _, t := range texts {
		if rules.HasRelativeScripts(t.text) {
			problems = append(problems, warn(name, t.field+" の `.ccnavi/scripts/` に `{root}` が付いていない。cwd が"+
				"プロジェクトの中だと相対パスの sh が見つからない。"+
				"`{root}/.ccnavi/scripts/...` と書いてください"))
		}
	}
	// {script:<名前>} の形が不正なら置き換わらず、スクリプトが無ければ置き換わっても実行できない。
	for _, t := range texts {
		for _, hit := range rules.ScriptPlaceholder.FindAllStringSubmatch(t.text, -1) {
			whole, script := hit[0], hit[1]
			var why string
			switch {
			case !rules.IsScriptName(script):
				why = "`" + whole + "` の名前が不正（使える字は英数字と `.` `_` `-`）。置き換わらない"
			case !ospath.IsFile(ospath.Join(home, append(slices.Clone(scriptsDir), script)...)):
				why = "`" + whole + "` の `.ccnavi/scripts/" + script + "` が無い。置き換わっても実行できない"
			default:
				continue
			}
			problems = append(problems, warn(name, t.field+" の "+why))
		}
	}
	// ルールが指すファイルは、ルートの中を指していて、いま在って、上限に収まるか。無いのは warn
	// （作るまで何も足さないだけで、判定は変わらない）。
	for _, f := range []struct{ key, rel string }{
		{"additionalContextFile", rule.AdditionalContextFile},
		{"additionalContextOnceFile", rule.AdditionalContextOnceFile},
	} {
		if f.rel == "" {
			continue
		}
		if why := ctxfile.BadPath(f.rel); why != "" {
			problems = append(problems, fail(name, f.key+" の "+f.rel+": "+why))
			continue
		}
		full := ctxfile.Locate([]string{home}, f.rel)
		switch {
		case full == "":
			problems = append(problems, warn(name, f.key+" の "+f.rel+" がありません。ワークツリーにもルートにも無ければ何も追加しません。"))
		case ctxfile.OverLimit(full):
			problems = append(problems, warn(name, fmt.Sprintf("%s の %s は %d 文字を超える。先頭だけを渡し、切り詰めたことを末尾に追記します。", f.key, f.rel, ctxfile.MaxChars)))
		}
	}

	problems = append(problems, everyProblems(rule, name)...)
	problems = append(problems, exceptProblems(rule, name)...)
	problems = append(problems, whenProblems(rule, name)...)
	problems = append(problems, catchAllDenyProblems(rule, name)...)

	// once の文と every > 1 の文は毎回は積まれないので、広さの報告から外す。読めない every は 1 として扱われるため、
	// この式は読めない every も拾う。
	if (rule.AdditionalContext != "" || rule.AdditionalContextFile != "") && rule.Decision == rules.Allow && rule.Every <= 1 {
		if why := broad(rule); why != "" {
			problems = append(problems, warn(name, "広い allow に additionalContext がある（"+why+"）。"+
				"ヒットするたびに同じ文がコンテキストに積まれる。狭いルールに分けて書いてください"))
		}
	}
	return problems
}

func wants(match string) []string {
	var out []string
	for _, w := range strings.Split(match, "|") {
		out = append(out, ospath.Strip(w))
	}
	return out
}

// stopProblems は `match: Stop` のルール（ターンの終わり N 回に 1 度止めて文を渡す）の書き方。どれも判定は変えない。
func stopProblems(rule *rules.Rule, name string, project bool) []rules.Problem {
	var listed []string
	for _, w := range wants(rule.Match) {
		if w != "" {
			listed = append(listed, w)
		}
	}
	if !slices.Contains(listed, rules.StopMatch) {
		return nil
	}
	var others []string
	for _, w := range listed {
		if w != rules.StopMatch {
			others = append(others, w)
		}
	}
	if rule.Decision != rules.Allow {
		rest := ""
		if len(others) > 0 {
			rest = "（" + strings.Join(others, "|") + " の部分はふつうに判定される）"
		}
		return []rules.Problem{warn(name, string(rule.Decision)+" の match に "+rules.StopMatch+" がある。ターンの終わりに見るのは "+
			"allow のルールだけなので、"+rules.StopMatch+" の部分は何も起きない"+rest+"。"+
			rules.StopMatch+" は allow の別のルールに分けて置いてください")}
	}
	if project {
		return []rules.Problem{warn(name, "プロジェクトのレイヤーの "+rules.StopMatch+" のルールは使われない。"+
			"ターンの終わりには共通レイヤーと自身のレイヤーしか読まない"+
			"（外のリポジトリの 1 行でメインのターンを止めさせないため）")}
	}
	var problems []rules.Problem
	if rule.Built() && !rule.Hits(rules.StopSubject) {
		problems = append(problems, warn(name, rules.StopMatch+" のルールは呼び出しの文字列ではなく、"+
			"固定の "+rules.StopSubject+" に適用する。"+
			"この glob / regex / when はそれにヒットしないので何も起きない。"+
			`glob: "*" と書いてください`))
	}
	if rule.Every < 2 {
		problems = append(problems, warn(name, rules.StopMatch+" のルールに every がありません（1 として扱う）ので使われない。"+
			"使えばターンが終わるたびに止めることになるので、"+
			"every: 10 のように間隔を空けてください"))
	}
	return problems
}

// anythingProbes は「何にでも当たる」を見る文字列（空と、パスにもコマンドにも出ない制御文字）。
var anythingProbes = []string{"", "\x02"}

// nonemptyProbes は「空でない文字列のどれにでも当たる」を見る文字列。共通の字を持たない 3 つ（判定は空の対象を
// 見ないので deny の本体はこれで見る。英字を入れるのは、記号だけに当たる式を何にでも当たると言わないため）。
var nonemptyProbes = []string{"\x02", "~", "a"}

type searcher interface{ Search(string) bool }

func hitsAll(re searcher, probes []string) bool {
	for _, p := range probes {
		if !re.Search(p) {
			return false
		}
	}
	return true
}

func shellTools(match string) []string {
	tools := wants(match)
	var shells []string
	for _, t := range config.ShellTools {
		if slices.Contains(tools, t) {
			shells = append(shells, t)
		}
	}
	return shells
}

// exceptProblems は except の意味の側（書式の誤りは読み込みが言う）。
func exceptProblems(rule *rules.Rule, name string) []rules.Problem {
	if len(rule.Excepts) == 0 {
		return nil
	}
	var problems []rules.Problem
	loose := false
	for i, e := range rule.Excepts {
		if e.Compiled != nil && hitsAll(e.Compiled, anythingProbes) {
			problems = append(problems, warn(name, fmt.Sprintf("except[%d] は何にでもヒットする。このルールはどの呼び出しにもヒットしない", i)))
		}
		if e.Compiled != nil && !boundToCommand(e.Compiled.Pattern()) {
			loose = true
		}
	}
	shells := shellTools(rule.Match)
	if len(shells) > 0 && (rule.Decision == rules.Deny || rule.Decision == rules.Ask) && loose {
		problems = append(problems, warn(name, strings.Join(shells, "|")+" の except はコマンド列の全体にヒットする。コマンドのどこかに"+
			"除く語を追加するだけでこのルールが外れるので、except は語の位置まで狭く書くか、"+
			"ルールを分けてください。語の位置まで狭くとは、コマンド名から始めて"+
			"`[^\\x00]*` でつなぎ、コマンドの切れ目を越えない形にすることです"))
	}
	return problems
}

var (
	// commandHead はコマンド名（頭の語）。先頭の `^` か `(^|\x00)` は付けてもよい。
	commandHead = regexp.MustCompile(`^(?:\(\^\|\\x00\)|\^)?(?:[A-Za-z0-9_\-/]|\\[.\-/b]|\\s[+*]?)+$`)
	// reachesBoundary は頭のあとに置くとコマンドの切れ目まで届く書き方（任意の 1 字・否定の文字クラス・\S \W \D）。
	reachesBoundary = regexp.MustCompile(`\\[SWD]|\.|\[\^`)
	// escapedChar は字そのもの・空白・語の境目を指すエスケープ（上の検査の前に除く）。
	escapedChar = regexp.MustCompile(`\\[^SWD]`)
)

// commandSpan はコマンドの中だけを読む繋ぎ。
const commandSpan = `[^\x00]`

// boundToCommand は except の式が、頭のコマンド名から同じコマンドの中だけを読む形か（簡易な判定）。
func boundToCommand(pattern string) bool {
	head, rest, found := strings.Cut(pattern, commandSpan)
	if !found || !commandHead.MatchString(head) {
		return false
	}
	rest = escapedChar.ReplaceAllString(strings.ReplaceAll(rest, commandSpan, ""), "")
	return !reachesBoundary.MatchString(rest)
}

// estimate は組み合わせ条件のノードを書き方だけから見積もった値。
type estimate int

const (
	unknown estimate = iota
	always
	never
)

// constant はノードがどの文字列にも当たる（always）・どれにも当たらない（never）と言えるか。
func constant(node *rules.Condition) estimate {
	if node.IsLeaf() {
		if node.Compiled != nil && hitsAll(node.Compiled, anythingProbes) {
			return always
		}
		return unknown
	}
	values := make([]estimate, len(node.Children))
	for i, c := range node.Children {
		values[i] = constant(c)
	}
	all := func(v estimate) bool {
		for _, x := range values {
			if x != v {
				return false
			}
		}
		return true
	}
	switch node.Kind {
	case rules.WhenNot:
		switch values[0] {
		case always:
			return never
		case never:
			return always
		}
		return unknown
	case rules.WhenAll:
		if slices.Contains(values, never) {
			return never
		}
		if all(always) {
			return always
		}
		return unknown
	}
	if slices.Contains(values, always) {
		return always
	}
	if all(never) {
		return never
	}
	return unknown
}

// catchAllDenyProblems は glob・regex 1 つの deny の本体が何にでも当たる形（match のツールを丸ごと止める）。
// 丸ごと止めると決めた書き方は `glob: "*"` ちょうどで書く。
func catchAllDenyProblems(rule *rules.Rule, name string) []rules.Problem {
	if rule.Decision != rules.Deny || rule.When != nil || rule.DegradedMessage != "" {
		return nil
	}
	if rule.Glob == "*" && rule.Regex == "" {
		return nil
	}
	if rule.Compiled == nil || !hitsAll(rule.Compiled, nonemptyProbes) {
		return nil
	}
	kind := "regex"
	if rule.Glob != "" {
		kind = "glob"
	}
	what := "match のツールを丸ごと止める"
	if len(rule.Excepts) > 0 {
		what = "except で除いたもの以外は、match のツールの呼び出しをすべて止める"
	}
	return []rules.Problem{warn(name, "本体の "+kind+" は何にでもヒットする。"+what+"。ツールごと止めるつもりなら "+
		"`glob: \"*\"` と書き、そうでなければ適用する語を書いてください")}
}

// whenProblems は when の意味の側（書式の誤りは読み込みが言う）。
func whenProblems(rule *rules.Rule, name string) []rules.Problem {
	tree := rule.When
	if tree == nil {
		return nil
	}
	var problems []rules.Problem
	// 何にでも当たる・何にも当たらないノードを外側から見て言う。外側が中立のときだけ中を見る。
	var look func(node *rules.Condition)
	look = func(node *rules.Condition) {
		value := constant(node)
		if value == unknown {
			for _, c := range node.Children {
				look(c)
			}
			return
		}
		where := node.Where
		if !node.IsLeaf() {
			where = node.Where + " の " + string(node.Kind)
		}
		said, whole := "何にでもヒットする", "。このルールはどの呼び出しにもヒットする"
		if value == never {
			said, whole = "何にもヒットしない", "。このルールはどの呼び出しにもヒットしない"
		}
		if node != tree {
			whole = "。条件を絞っていない"
		}
		problems = append(problems, warn(name, where+" は"+said+whole))
	}
	look(tree)

	// 同じ個別条件が肯定と否定で並ぶ all・any（同じ演算子の入れ子は 1 つに開いて見る）。
	var operands func(node *rules.Condition) []*rules.Condition
	operands = func(node *rules.Condition) []*rules.Condition {
		var out []*rules.Condition
		for _, c := range node.Children {
			if c.Kind == node.Kind {
				out = append(out, operands(c)...)
			} else {
				out = append(out, c)
			}
		}
		return out
	}
	hasNot := false
	for _, node := range tree.Walk() {
		if node.Kind == rules.WhenNot {
			hasNot = true
		}
		if node.Kind != rules.WhenAll && node.Kind != rules.WhenAny {
			continue
		}
		plain, negated := map[string]bool{}, map[string]bool{}
		for _, c := range operands(node) {
			if c.Kind == rules.WhenNot {
				negated[c.Children[0].Key()] = true
			} else {
				plain[c.Key()] = true
			}
		}
		clash := false
		for k := range plain {
			if negated[k] {
				clash = true
			}
		}
		if !clash {
			continue
		}
		result := "何にでもヒットする"
		if node.Kind == rules.WhenAll {
			result = "何にもヒットしない"
		}
		problems = append(problems, warn(name, node.Where+" の "+string(node.Kind)+" に、同じ個別条件が肯定と否定で並んでいる。"+
			"互いに打ち消し合うので、この "+string(node.Kind)+" は"+result))
	}
	shells := shellTools(rule.Match)
	if len(shells) > 0 && hasNot && (rule.Decision == rules.Deny || rule.Decision == rules.Ask) {
		problems = append(problems, warn(name, strings.Join(shells, "|")+" の when の not はコマンド列の全体にヒットする。"+
			"コマンドのどこかに"+
			"除く語を追加するだけでこのルールが外れるので、not の下は語の位置まで狭く書くか、"+
			"ルールを分けてください"))
	}
	return problems
}

// everyProblems は every の値と、刻んだ先に渡すものがあるかを検査する。読めない値は error にする。判定は毎回渡すので、
// 書いたユーザは刻んだつもりで毎回渡ることになってしまうためである。
func everyProblems(rule *rules.Rule, name string) []rules.Problem {
	if rule.EveryWritten == nil {
		return nil
	}
	if rules.ReadableEvery(rule.EveryWritten) == 0 {
		return []rules.Problem{fail(name, "every の "+pyfmt.Repr(rule.EveryWritten)+" は間隔として読めない。"+
			"1 以上の整数で書いてください。このままだと間隔を空けず、毎回渡る")}
	}
	if rule.AdditionalContext == "" && rule.AdditionalContextFile == "" && rule.AdditionalContextOnce == "" && rule.AdditionalContextOnceFile == "" {
		return []rules.Problem{warn(name, "every があるのに渡すものが無いので、間隔を空けても何も渡らない。"+
			"additionalContext（または additionalContextOnce・…File）を書くか、"+
			"every を削除してください")}
	}
	return nil
}

// broadAlternatives は allow のルールを広いと言う、正規表現の選択肢の数。
const broadAlternatives = 3

// broad は allow のルールが広いと言える理由（何にでも当たる・選択肢が broadAlternatives 以上）。無ければ空。
func broad(rule *rules.Rule) string {
	if rule.When != nil && rule.Compiled == nil {
		// 組み合わせ条件は 1 つの文字列で試すと、`not: {regex: '^rm'}` のような形を取り違える。
		if constant(rule.When) == always {
			return "何にでもヒットする"
		}
	} else if rule.Built() && rule.BodyHits("x") {
		return "何にでもヒットする"
	}
	if alternatives(rule.Regex) >= broadAlternatives {
		return "選択肢が " + strconv.Itoa(broadAlternatives) + " つ以上"
	}
	return ""
}

// alternatives は正規表現の選択肢の数。文字クラスの中と、逃がした `|` は数えない。
func alternatives(regex string) int {
	if regex == "" {
		return 0
	}
	count, inClass, escaped := 1, false, false
	for _, ch := range regex {
		switch {
		case escaped:
			escaped = false
		case ch == '\\':
			escaped = true
		case inClass:
			inClass = ch != ']'
		case ch == '[':
			inClass = true
		case ch == '|':
			count++
		}
	}
	return count
}

// inert は match に並んだツール名のうち、判定が対象を取り出せないものを返す。一覧を写さず、rules.SubjectOf を
// 実際に呼んで確かめる。判定がツールを増やしたときに、こちらが気づかないうちに古くならないようにするためである。
func inert(match string) []string {
	probe := pyjson.NewObject()
	for _, f := range rules.SubjectFields {
		probe.Set(f.Field, "x")
	}
	var out []string
	for _, tool := range wants(match) {
		if tool == "" || tool == rules.StopMatch {
			continue
		}
		if rules.SubjectOf(hookio.Input{ToolName: tool, ToolInput: probe}) == "" {
			out = append(out, tool)
		}
	}
	return out
}
