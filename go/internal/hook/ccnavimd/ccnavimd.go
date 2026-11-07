package ccnavimd

import (
	"fmt"
	"sort"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyre"
	"ccnavi/internal/infra/shell"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/tickets/model/place"
)

const (
	// file は作業の決まりの md の名前（ccnavi ディレクトリの直下）。
	file = "CCNAVI.md"
	// maxChars は 1 回の応答に載せる本文の上限（符号位置で数える）。常駐の文脈なので毎回これだけ重くなる。
	maxChars = 6000
	// scriptFile は配布先が条件を足す sh（ccnavi ディレクトリの scripts/ の下）。エージェントの Write・Edit は
	// 組み込みの guard-scripts が止めるので、セッションの頭に載る文を条件の側から書き換えられない。
	scriptFile     = "ccnavimd.sh"
	functionPrefix = "ccnavi_cond_"
	// indent は囲みの 1 段ぶんのインデント。載せるときに深さの分だけ取る。
	indent = "  "
	// workspaceRootRule はワークツリーの行を支える deny の id。
	workspaceRootRule = "workspace-root"
	// scriptLoadFailed は sh の読み込みに失敗したときの終了コード。構文の誤りで `.` が 1 を返す sh（macOS の bash）でも、
	// 偽の 1 と取り違えないようにする。
	scriptLoadFailed = "3"
	falseCode        = 1
)

var (
	ifLine       = pyre.MustCompile(`^\s*<!--\s*ccnavi:if\s+(?P<name>\S+)\s*-->\s*$`, 0)
	elseLine     = pyre.MustCompile(`^\s*<!--\s*ccnavi:else\s*-->\s*$`, 0)
	endifLine    = pyre.MustCompile(`^\s*<!--\s*ccnavi:endif\s*-->\s*$`, 0)
	directive    = pyre.MustCompile(`<!--\s*ccnavi:`, 0)
	substitution = pyre.MustCompile(`\{\{\s*(?P<name>\w+)\s*\}\}`, 0)
	// function は sh に書かれた条件の関数。名前は Unicode の単語文字で拾う（形の検査は shell.IsIdentifier がする）。
	function = pyre.MustCompile(`^\s*`+functionPrefix+`(?P<name>\w+)\s*\(\s*\)`, pyre.Multiline)
)

// situation は条件の判定に渡す、そのセッションの状況。
type situation struct {
	conf    *config.Settings
	root    string
	docs    bool // 索引の案内（`--docs`）を出す回か
	denyIDs map[string]bool
	mode    config.Mode
}

// conditions は md の条件の名前と、その真偽。条件を足すときはここに 1 つ足す。
var conditions = map[string]func(s situation) bool{
	"workspace_root":  func(s situation) bool { return s.denyIDs[workspaceRootRule] },
	"git_wrapper":     func(s situation) bool { return config.HasGitWrapper(s.root) },
	"projects":        func(s situation) bool { return len(tree.Projects(s.conf.Projects)) > 0 },
	"tickets":         func(s situation) bool { return s.conf.TicketsEnabled() },
	"docs":            func(s situation) bool { return s.docs },
	"dry_run":         func(s situation) bool { return s.mode == config.DryRun },
	"non_interactive": func(situation) bool { return hookio.Unattended() },
}

// substitutions は `{{名前}}` に使える名前。
var substitutions = []string{
	"worktrees",    // ワークツリーの置き場（`.claude/worktrees`）
	"git_sh",       // git のラッパー（打てる形のコマンド）
	"ticket_sh",    // チケットの sh
	"stage_sh",     // 保護対象を下書きで直すための sh
	"worktree_sh",  // 直接作業のワークツリーを切る sh
	"handoff",      // ユーザに渡す物の置き場（`wip/handoff`）
	"scratch",      // 一時の置き場（ワークスペースルート直下の `scratchpad` の絶対パス）
	"projects",     // プロジェクトの置き場（ワークスペースルートからの相対）
	"tickets_dir",  // 提案の置き場
	"approved_dir", // 承認済みチケットの置き場
	"mode_env",     // CCNAVI_MODE
}

// NameProblems は条件の名前が sh の関数名に使えない形（`-` を含むなど）の行。lint が error で言う。
func NameProblems(text string) []string {
	var found []string
	for i, line := range ospath.Splitlines(text) {
		if name, ok := opened(line); ok && !shell.IsIdentifier(name) {
			found = append(found, fmt.Sprintf(
				"%d 行目: 条件の名前 %s は指定できない（英数字と _ だけ。先頭は数字にしない。`-` は _ にする）", i+1, name))
		}
	}
	return found
}

// Problems は md の書き方の誤り（`ccnavi --lint`）。条件の名前（組み込みか、scripts＝sh に書かれた関数の名前）、
// 差し込みの名前、対の取れていない囲みを見る。名前の形は NameProblems が言う。
func Problems(text string, scripts map[string]bool) []string {
	var found []string
	// 行を上から読み、囲み（if〜endif）の入れ子を elses の積み方で追う。開く行で積み、endif で降ろす。
	var elses []bool // 囲みごとに、else を置いたか
	for i, line := range ospath.Splitlines(text) {
		number := i + 1
		if name, ok := opened(line); ok {
			elses = append(elses, false)
			if _, builtin := conditions[name]; shell.IsIdentifier(name) && !builtin && !scripts[name] {
				found = append(found, fmt.Sprintf("%d 行目: 条件 %s は指定できない（組み込みの条件でも %s の %s でもない）",
					number, name, scriptFile, functionPrefix+name))
			}
		} else if elseLine.Match(line) {
			switch {
			case len(elses) == 0:
				found = append(found, fmt.Sprintf("%d 行目: ccnavi:else に対応する ccnavi:if がありません。", number))
			case elses[len(elses)-1]:
				found = append(found, fmt.Sprintf("%d 行目: 1 つの ccnavi:if に ccnavi:else は 1 つだけ", number))
			default:
				elses[len(elses)-1] = true
			}
		} else if endifLine.Match(line) {
			if len(elses) == 0 {
				found = append(found, fmt.Sprintf("%d 行目: ccnavi:endif に対応する ccnavi:if がありません。", number))
			} else {
				elses = elses[:len(elses)-1]
			}
		} else if directive.Search(line) {
			found = append(found, fmt.Sprintf("%d 行目: ccnavi: の囲みは 1 行に 1 つ、単独で書く", number))
		}
		// 差し込みは囲みの種類に関わらず、どの行でも調べる。
		for _, m := range substitution.FindAll(line) {
			name, _ := m.Named("name")
			if !known(name) {
				found = append(found, fmt.Sprintf("%d 行目: 差し込み {{%s}} は指定できない", number, name))
			}
		}
	}
	if len(elses) > 0 {
		found = append(found, "ccnavi:if に対応する ccnavi:endif がありません。")
	}
	return found
}

func opened(line string) (string, bool) {
	m, ok := ifLine.Find(line)
	if !ok {
		return "", false
	}
	name, _ := m.Named("name")
	return name, true
}

func known(name string) bool {
	for _, s := range substitutions {
		if s == name {
			return true
		}
	}
	return false
}

// dedent は囲みの深さの分だけ、行頭のインデント（indent 1 つにつき 1 段）を取る。
func dedent(line string, depth int) string {
	for range depth {
		rest, ok := strings.CutPrefix(line, indent)
		if !ok {
			break
		}
		line = rest
	}
	return line
}

// render は条件に合う行だけを残し、差し込みを解く。空行は続けて 1 つにまとめ、前後の空行は取る。
// flags に無い条件は成り立たないものとし、知らない差し込みはそのまま残す（書き間違いが見える形で届く）。
// else はいちばん内側の囲みの真偽を反転する。外側が成り立たなければ、反転しても載らない。
func render(text string, flags map[string]bool, values map[string]string) string {
	var out []string
	// stack は開いている囲みごとの真偽。全部が真の行だけ残す。
	var stack []bool
	for _, line := range ospath.Splitlines(text) {
		if name, ok := opened(line); ok {
			stack = append(stack, flags[name])
			continue
		}
		if elseLine.Match(line) {
			if len(stack) > 0 {
				stack[len(stack)-1] = !stack[len(stack)-1]
			}
			continue
		}
		if endifLine.Match(line) {
			if len(stack) > 0 {
				stack = stack[:len(stack)-1]
			}
			continue
		}
		// 開いている囲みのどれかが偽なら、この行は載せない。
		if !all(stack) {
			continue
		}
		line = dedent(line, len(stack))
		out = append(out, substitution.Sub(line, func(m pyre.Found) string {
			name, _ := m.Named("name")
			if v, ok := values[name]; ok {
				return v
			}
			return m.Text()
		}))
	}
	var lines []string
	for _, line := range out {
		// 囲みを外した跡に空行が並ばないよう、続く空行は 1 つにし、先頭の空行は捨てる。
		if ospath.Strip(line) == "" && (len(lines) == 0 || ospath.Strip(lines[len(lines)-1]) == "") {
			continue
		}
		lines = append(lines, ospath.Rstrip(line))
	}
	for len(lines) > 0 && ospath.Strip(lines[len(lines)-1]) == "" {
		lines = lines[:len(lines)-1]
	}
	return strings.Join(lines, "\n")
}

func all(stack []bool) bool {
	for _, b := range stack {
		if !b {
			return false
		}
	}
	return true
}

// home は ccnavi ディレクトリの名前（既定は `.ccnavi`）。
func home(conf *config.Settings) string {
	h := conf.ProjectHome
	if h == "" {
		h = config.DefaultProjectHome
	}
	return strings.Trim(h, "/")
}

// Path は作業の決まりの md の絶対パス。
func Path(conf *config.Settings, root string) string {
	return ospath.Join(root, home(conf), file)
}

func scriptPath(conf *config.Settings, root string) string {
	return ospath.Join(root, home(conf), config.ScriptsDir, scriptFile)
}

// ScriptNames は ccnavimd.sh に関数として書かれている条件の名前（組み込みと同じ名前は除く）。読めなければ空。
func ScriptNames(conf *config.Settings, root string) map[string]bool {
	names := map[string]bool{}
	text, err := ospath.ReadDiskText(scriptPath(conf, root))
	if err != nil {
		return names
	}
	for _, m := range function.FindAll(text) {
		name, _ := m.Named("name")
		if _, builtin := conditions[name]; !builtin {
			names[name] = true
		}
	}
	return names
}

// runScript は条件の関数を走らせる。0 が真、1 が偽。判定できなければ（走らせられない・時間切れ・0 と 1 以外）真。
// 黙って落とすと、載るはずの決まりが誰にも気づかれずに届かなくなるので、載せる側に倒す。
// 状況は環境変数で渡し、cwd はワークスペースルートにする。
func runScript(conf *config.Settings, root, name string, s situation) bool {
	docs := "0"
	if s.docs {
		docs = "1"
	}
	ids := make([]string, 0, len(s.denyIDs))
	for id := range s.denyIDs {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	vars := []string{
		env.Root + "=" + root,
		env.Docs + "=" + docs,
		env.DenyIDs + "=" + strings.Join(ids, " "),
		env.Mode + "=" + string(s.mode),
	}
	path := strings.ReplaceAll(scriptPath(conf, root), `\`, "/")
	args := []string{"-c", `. "$1" || exit ` + scriptLoadFailed + `; "$2"`, shell.Program, path, functionPrefix + name}
	ran, err := shell.Script(args, root, vars, shell.ConditionTimeout)
	if err != nil || ran.TimedOut {
		return true
	}
	return ran.Code != falseCode
}

// scriptFlags は md が使っている、組み込みに無い条件のうち成り立つもの。使っていない sh の関数は走らせない。
// 関数が無い名前は判定できないので成り立つものにし、囲んだ行をそのまま出す（誤りは lint が言う）。
func scriptFlags(conf *config.Settings, root, raw string, s situation) map[string]bool {
	available := ScriptNames(conf, root)
	out := map[string]bool{}
	tried := map[string]bool{}
	for _, line := range ospath.Splitlines(raw) {
		name, ok := opened(line)
		if !ok || tried[name] {
			continue
		}
		tried[name] = true
		if _, builtin := conditions[name]; builtin {
			continue
		}
		if !available[name] || runScript(conf, root, name, s) {
			out[name] = true
		}
	}
	return out
}

func valuesFor(conf *config.Settings, root string) map[string]string {
	projects := ""
	if conf.Projects != "" {
		projects = strings.ReplaceAll(ospath.RelOr(conf.Projects, root), `\`, "/")
	}
	return map[string]string{
		"worktrees":    strings.ReplaceAll(tree.WorktreesDir, `\`, "/"),
		"git_sh":       config.ScriptCommand(root, config.GitWrapper),
		"ticket_sh":    config.ScriptCommand(root, config.TicketWrapper),
		"stage_sh":     config.ScriptCommand(root, config.StageWrapper),
		"worktree_sh":  config.ScriptCommand(root, config.WorktreeWrapper),
		"handoff":      place.Handoff,
		"scratch":      place.ScratchDir(root),
		"projects":     projects,
		"tickets_dir":  conf.Tickets,
		"approved_dir": conf.Approved,
		"mode_env":     env.Mode,
	}
}

// SessionText はセッションの頭で渡す作業の決まり。md が無い・読めないときは、無いことだけを 1 行で言う
// （黙ると、決まりが渡っていないことに誰も気づけない）。長すぎるときは切り、切ったことを末尾に言う。
// docs は索引の案内を出す回か、denyIDs はワークスペースのルールの deny の id。
func SessionText(conf *config.Settings, root string, docs bool, denyIDs map[string]bool, mode config.Mode) string {
	shown := home(conf) + "/" + file
	raw, err := ospath.ReadDiskText(Path(conf, root))
	if err != nil {
		return hookio.Head + shown + " が無いか読めないので、作業の決まりを渡せない。導入スクリプト（ccnavi-setup.sh）で配る。"
	}
	s := situation{conf: conf, root: root, docs: docs, denyIDs: denyIDs, mode: mode}
	flags := map[string]bool{}
	for name, check := range conditions {
		if check(s) {
			flags[name] = true
		}
	}
	for name := range scriptFlags(conf, root, raw, s) {
		flags[name] = true
	}
	text := render(raw, flags, valuesFor(conf, root))
	if len([]rune(text)) > maxChars {
		text = ospath.Rstrip(pyfmt.Head(text, maxChars)) +
			fmt.Sprintf("\n（長すぎるので %d 文字で切った。続きは %s を読む）", maxChars, shown)
	}
	return text
}
