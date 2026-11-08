package subagent

import (
	"fmt"
	"io"
	"maps"
	"slices"
	"strconv"
	"strings"
	"unicode/utf8"

	"ccnavi/internal/catalog/skills"
	"ccnavi/internal/hook/agentkind"
	"ccnavi/internal/hook/childnote"
	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/ctxfile"
	"ccnavi/internal/records/audit"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/flow"
	"ccnavi/internal/tickets/model/ticket"
)

// 始まりに渡す文。
const (
	// candidateNote: サブエージェントには Stop の振り返り（`match: Stop` のルールの文）が届かないので、始まりに 1 行だけ
	// 渡す。メインはこの節を集めて振り返りに使う（.ccnavi/docs/skill-review.md）。
	candidateNote = hookio.Head + "作業中に手順の見落としやすい点やスキルの誤りに気づいたら、最後の報告に" +
		"「スキル候補」の節を追加して書いてください（対象のスキル・何を直すか・根拠）。" +
		"スキルのファイルは自分では書かない。"
	childrenHead = hookio.Head + "承認済みで開いている子チケット。" +
		"書き込みは行き先のワークツリーのチケットで判定される。"
	ownFlowOnly = "  フロー: 自分の担当の子チケットのフローだけを読んで従ってください。" +
		"他の子のフローには従わないでください。担当が分からなければ、読まずにメインに聞いてください"
	sharedNote = hookio.Head + "起動時の作業ディレクトリと同じワークツリー（%s）で、別の編集する" +
		"サブエージェントが動いています（%s）。並行して編集するときは、別のワークツリーを切る" +
		"決まりです（.ccnavi/docs/decisions.md）。同じワークツリーで続けるなら、触るファイルの" +
		"持ち主を分け、他のサブエージェントの未コミットの変更には触れないでください。" +
		"別のワークツリーで作業している場合や、親から委任された子の場合は、対応は不要です。"
	unknownType = "種類不明"
	rootLabel   = "ワークスペースルート"
	runningFail = "ccnavi: 実行中のサブエージェントの印を保存できませんでした: %s\n"
)

// cwdTree は呼び出しの cwd（無ければ手元の作業ディレクトリ）のツリー。
func cwdTree(conf *config.Settings, root string, in hookio.Input) (tree.Tree, bool) {
	cwd := in.Cwd
	if cwd == "" {
		cwd, _ = ospath.Getwd()
	}
	return tree.TreeOf(root, cwd, conf.Projects)
}

// AtStart は SubagentStart。cwd のワークツリーに関わる開いている子の案内を渡す。親のワークツリーならその親の開いている子、
// 子のワークツリーならその子自身。ワークスペースルートとチケットの無いワークツリーには子の案内を渡さない（全部の子を
// 渡すと、別のセッションがルートで委譲した調査役にも無関係な範囲を案内してしまう）。判定ではなく案内で、終了コードは
// いつも ExitOK。error は応答を書けなかったとき。
func AtStart(stdout, stderr io.Writer, conf *config.Settings, root string, in hookio.Input, record *audit.Record) (int, error) {
	record.Decision, record.Enforced = audit.Allow, true
	here, found := cwdTree(conf, root, in)
	// 同じワークツリーの別の編集するサブエージェントの案内は、チケットの無いツリーでも渡すので先に組む。
	shared := noteRunning(stderr, conf, root, in, here, found)
	lead := pyfmt.JoinNonEmpty(pyfmt.ParagraphBreak, skills.Notice(stderr, conf, root, in, true), shared, candidateNote)
	if !conf.TicketsEnabled() || !found || here.IsMain() {
		return say(stdout, lead)
	}
	// 着手で書かれる基準点は親のツリーのチケットにだけ入るので、正とする側（親のツリー）を読む。
	copies, _ := approval.Scan(conf, root, false, nil)
	index := approval.ByID(copies)
	bound, ok := tree.Lookup(index, here.Name)
	if !ok {
		return say(stdout, lead)
	}
	children := childrenOf(bound, copies)
	if len(children) == 0 {
		return say(stdout, lead)
	}
	preds := approval.PredecessorPool(conf, root)
	lines := []string{childrenHead}
	budget := flow.TotalTextLimit
	// 手順を並べるのは子が 1 本に決まるときだけ。親のツリーからの起動では、どの子の担当かが hook から決められない。
	full := bound.IsChild()
	listed := false
	var changed []string
	for _, child := range slices.SortedFunc(slices.Values(children), byID) {
		block := childnote.ChildBlock(conf, root, child, index, preds, budget, full)
		lines = append(lines, block.Lines...)
		for _, line := range block.Brief {
			budget -= utf8.RuneCountInString(line)
		}
		listed = listed || len(block.Brief) > 0
		if block.Notice != "" {
			changed = append(changed, block.Notice)
		}
	}
	lines = append(lines, childnote.StageLines(conf, root, index, children)...)
	if listed && !full {
		lines = append(lines, ownFlowOnly)
	}
	lines = append(lines, childnote.RuleLines()...)
	lines = append(lines, changed...)
	text := pyfmt.JoinNonEmpty(pyfmt.ParagraphBreak, lead, strings.Join(lines, "\n"))
	if err := hookio.WriteContext(stdout, hookio.SubagentStart, text, strings.Join(changed, "\n")); err != nil {
		return config.ExitOK, fmt.Errorf("SubagentStart の応答: %w", err)
	}
	return config.ExitOK, nil
}

// childrenOf は担当の子。子のツリーならその子、親のツリーなら親の開いている子の全部。
func childrenOf(bound *ticket.Ticket, copies []*ticket.Ticket) []*ticket.Ticket {
	if bound.IsChild() {
		return []*ticket.Ticket{bound}
	}
	var out []*ticket.Ticket
	for _, c := range copies {
		if c.Parent == bound.ID {
			out = append(out, c)
		}
	}
	return out
}

func byID(a, b *ticket.Ticket) int { return strings.Compare(a.ID, b.ID) }

// say は子の案内の無い起動で、頭の文（スキルの目録と候補の 1 行）だけを渡す。
func say(stdout io.Writer, text string) (int, error) {
	if text == "" {
		return config.ExitOK, nil
	}
	if err := hookio.WriteContext(stdout, hookio.SubagentStart, text, ""); err != nil {
		return config.ExitOK, fmt.Errorf("SubagentStart の応答: %w", err)
	}
	return config.ExitOK, nil
}

// noteRunning は実行中の印を置き、同じワークツリーで動く別の編集するサブエージェントへの案内を返す（無ければ空）。
// チケット制御の有無によらず置く（直接作業の促しも、実行中の体が居る間は止める）。置けなくても始まりは止めない。
// 相手が書きかけを読まないよう途中を見せない書き方で置き、置いてから相手を探す（同時に起動した 2 つのうち、後から
// 探すほうが先の印を見つける）。ツリーと種類は編集するサブエージェントだけが書き、書かない印は相手に数えない。
func noteRunning(stderr io.Writer, conf *config.Settings, root string, in hookio.Input, here tree.Tree, found bool) string {
	if conf.State == "" || in.AgentID == "" {
		return ""
	}
	lines := []string{strconv.FormatInt(fsio.Now().Unix(), 10)}
	editing := found && agentkind.Of([]string{here.Root, root}, in.AgentType, in.Tools) == agentkind.Edit
	if editing {
		lines = append(lines, treeHead+string(here.Kind)+" "+flat(here.Name), typeHead+flat(in.AgentType))
	}
	file := markPath(conf.State, in.SessionID, in.AgentID, runningSuffix)
	if failed := fsio.WriteText(file, markText(in.SessionID, lines), fsio.NativeNewline, fsio.Atomic); failed != "" {
		fmt.Fprintf(stderr, runningFail, failed)
	}
	if !editing {
		return ""
	}
	return sharedTreeNote(stderr, conf, in, here)
}

// sharedTreeNote は同じワークツリーで実行中の、まだ案内していない別の編集するサブエージェントの案内。相手は同じ
// セッションの、自分でない、ツリーが同じ（名前は大文字小文字を区別しない）、期限内の印。案内した相手は覚えて、再開で
// SubagentStart がまた走っても重ねない。覚えられないときは案内する側に倒す。
func sharedTreeNote(stderr io.Writer, conf *config.Settings, in hookio.Input, here tree.Tree) string {
	marks, _ := sessionMarks(conf.State, in.SessionID, runningSuffix)
	if len(marks) == 0 {
		return ""
	}
	me := fsio.SafeNameOr(in.AgentID, unknownName, fsio.SafeNameLimit)
	want := string(here.Kind) + " " + pathcase.Fold(here.Name)
	when := fsio.UnixSeconds(fsio.Now())
	// 同じセッション・同じツリーで、期限内の別のサブエージェントの印を集める（peers は 名前 → エージェントの種類、order は見つけた順）。
	peers := map[string]string{}
	var order []string
	for _, m := range marks {
		if m.who == me {
			continue
		}
		lines, _ := markLines(m.file)
		if len(lines) == 0 || lines[0] != sessionLine(in.SessionID) {
			continue
		}
		started, ok := startedAt(lines)
		// 期限切れの印は、終了の印を残せずに終わったもの。古い印で案内を出し続けないよう外す。
		if !ok || when-started > runningMax() {
			continue
		}
		place, ok := field(lines, treeHead)
		if !ok {
			continue
		}
		kind, name, _ := strings.Cut(place, " ")
		if kind+" "+pathcase.Fold(name) != want {
			continue
		}
		agentType, _ := field(lines, typeHead)
		peers[m.who] = agentType
		order = append(order, m.who)
	}
	if len(peers) == 0 {
		return ""
	}
	// 案内済みの相手を除く。覚えている記録が読めない（known が偽）ときは全員に案内する側に倒す。
	told, known := ctxfile.ToldPeers(stderr, conf.State, in.SessionID, in.AgentID)
	remember := map[string]bool{}
	for k := range told {
		remember[k] = true
	}
	counts := map[string]int{}
	fresh := 0
	for _, who := range order {
		if known && told[who] {
			continue
		}
		fresh++
		remember[who] = true
		label := peers[who]
		if label == "" {
			label = unknownType
		}
		counts[label]++
	}
	if fresh == 0 {
		return ""
	}
	ctxfile.RememberPeers(stderr, conf.State, in.SessionID, in.AgentID, remember)
	var who []string
	for _, label := range slices.Sorted(maps.Keys(counts)) {
		who = append(who, label+" "+strconv.Itoa(counts[label])+" 件")
	}
	where := here.Name
	if where == "" {
		where = rootLabel
	}
	return fmt.Sprintf(sharedNote, where, strings.Join(who, "、"))
}

// field は印の 3 行目以降で head で始まる最初の行の残り。
func field(lines []string, head string) (string, bool) {
	for _, line := range lines[min(2, len(lines)):] {
		if rest, ok := strings.CutPrefix(line, head); ok {
			return rest, true
		}
	}
	return "", false
}
