package post

import (
	"fmt"
	"io"
	"slices"
	"strings"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/policy/selfguard"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/records/audit"
	"ccnavi/internal/tickets/model/useronly"
)

const (
	// 判定に至らなかった理由のうち、このチェックだけが出すもの。
	reasonToolCannotWrite audit.Reason = "tool-cannot-write"
	// ReasonWorktreeUnreadable は見るツリーを 1 つも git で読めなかった。
	ReasonWorktreeUnreadable audit.Reason = "worktree-unreadable"
	// reasonNoTurnBaseline はターンの始まりを見ていないので、このターンで起きたことを切り出せない。
	reasonNoTurnBaseline audit.Reason = "no-turn-baseline"

	// ReportLimit は 1 回の報告に載せる件数。ビルドが生成物を数百件置くことがあり、全部を並べると 1 件も読まれない。
	ReportLimit = 12
	// asideDir は戻すときに現れたファイルを退避する置き場（state の下、日時ごと）。
	asideDir = "aside"
	// mainTreeName はターンの報告で、名前を持たないツリー（ワークスペースルート）を呼ぶ名前。
	mainTreeName = "."
)

// Watched は、実行後に見るツリー 1 つと、そこに当てるルール（実行前チェックと同じ引き方）。SideBySide は、横で作業する指定が
// 有効なユーザのチェックアウトを表す。ここで新しく見つかった変更は、ユーザか別のセッションのものかもしれないので、戻さず報告する。
type Watched struct {
	Tree       tree.Tree
	Rules      *rules.RuleSet
	Source     string
	SideBySide bool
}

// Checker は実行後チェックの 1 回ぶんの共通の持ち物。Mine は ccnavi 自身が書く場所（記録と state）で、自分の書き込みを
// 自分の違反にしない。Places は置き場（そこに現れたサブコマンドの書き込みを外す）、Root はワークスペースルート
// （文面ファイルと ready の退避を引く）。Scope はチケット制御が無効なら nil。
type Checker struct {
	Stderr  io.Writer
	State   string
	Mine    []string
	Watched []Watched
	Scope   *ScopeGuard
	Places  Places
	Root    string
}

// readTree は git に聞いた 1 つのツリー。
type readTree struct {
	w       Watched
	top     string
	changes []git.Change
}

// readAll は見るツリーを順に git に聞く。読めないツリーは記録して飛ばす。1 つも読めなければ、判定に至らなかったことにする。
// 見ていなかった期間を後から数えられるようにするため。
func (c *Checker) readAll(record *audit.Record) []readTree {
	var out []readTree
	var failed []string
	for _, w := range c.Watched {
		top := git.TopLevel(w.Tree.Root)
		changes, unreadable := git.Read(top, 0)
		if unreadable != "" {
			if w.Tree.Name != "" {
				failed = append(failed, w.Tree.Name+": "+string(unreadable))
			} else {
				failed = append(failed, string(unreadable))
			}
			continue
		}
		out = append(out, readTree{w, top, changes})
	}
	if len(failed) > 0 {
		note := strings.Join(failed, "; ")
		fmt.Fprintf(c.Stderr, "ccnavi: ワークツリーを読めないので実行後チェックは動かない: %s\n", note)
		addDetail(record, note)
	}
	if len(out) == 0 {
		record.Decision, record.Reason = audit.Skip, ReasonWorktreeUnreadable
	}
	return out
}

func (c *Checker) scanOf(r readTree, withPlaces bool) scan {
	s := scan{changes: r.changes, rules: r.w.Rules, mine: c.Mine, source: r.w.Source, scope: c.Scope, where: r.w.Tree}
	if withPlaces {
		s.top, s.places, s.root = r.top, c.Places, c.Root
	}
	return s
}

// found は読んだツリーの変更のうち、保護領域にヒットするもの。実行後と起動時の基準で同じ式を使う。
func (c *Checker) found(read []readTree) []*finding {
	var out []*finding
	for _, r := range read {
		out = append(out, c.scanOf(r, true).findings()...)
	}
	return out
}

func keys(found []*finding) map[string]bool {
	out := make(map[string]bool, len(found))
	for _, f := range found {
		out[f.key()] = true
	}
	return out
}

// restorable は、この 1 件を git から戻してよいかを返す。戻してよいのは deny と宣言された場所だけ。ask は「ユーザが 1 度見る場所」で、
// 確認に「はい」と答えた編集をあとから無かったことにしてしまうため戻さない。範囲外はルールが何も言っていない場所なので戻さない。
// コミット済みは戻す手順を持たない。報告には、戻さない分も出す。
func restorable(f *finding) bool {
	if f.change.Kind == git.KindCommitted {
		return false
	}
	return slices.ContainsFunc(f.group, func(r *rules.Rule) bool { return r.Decision == rules.Deny })
}

func sideRoots(read []readTree) map[string]bool {
	out := map[string]bool{}
	for _, r := range read {
		if r.w.SideBySide {
			out[pathcase.Fold(r.w.Tree.Root)] = true
		}
	}
	return out
}

func sameTree(f *finding, root string) bool {
	return f.treeRoot != "" && pathcase.Fold(f.treeRoot) == pathcase.Fold(root)
}

func inSide(f *finding, roots map[string]bool) bool {
	return f.treeRoot != "" && roots[pathcase.Fold(f.treeRoot)]
}

func addDetail(record *audit.Record, note string) {
	if note == "" {
		return
	}
	if record.Detail != "" {
		record.Detail += "; " + note
	} else {
		record.Detail = note
	}
}

func recordFound(record *audit.Record, found []*finding) {
	record.Paths = nil
	names := map[string]bool{}
	for _, f := range found {
		record.Paths = append(record.Paths, string(f.change.Kind)+" "+f.change.Path)
		for _, rule := range f.group {
			names[rules.RecordName(rule)] = true
		}
	}
	record.Rules = sorted(names)
}

func gitVariant(gitCommand string) texts.Variants {
	if gitCommand != "" {
		return texts.Variants{"what": "git"}
	}
	return texts.Variants{"what": "plain"}
}

func gitWrapper(root string) string {
	command, _ := config.GitWrapperCommand(root)
	return command
}

// Check は実行後の 1 回ぶんを処理する。モデルに返す文と、ワークツリーを戻そうとしたかどうか（戻せなかった分を含む）を返す。
// 2 つ目の戻り値は、戻しで動いたかもしれないファイル（チケットの置き場）を呼び手が読み直すかを決めるためのもの。enforcing は、
// このモードが判定を適用する側かを表す。restore は、モードを掛けたあとの戻しの設定（掛ける場所は config.EffectiveSetting の 1 か所）。
//
// 新しい変更だけを原因付きで言い、報告済みのものは繰り返さない。基準が無いセッションでは今そこに在るものを原因を付けずに
// 1 度だけ伝え、基準が在れば起動時に在ったものを 1 度だけ伝える。戻すのは deny と宣言された場所の新しい変更だけで、
// 予行（dry-run）なら戻していたはずのものを言う。
func (c *Checker) Check(enforcing bool, restore config.Mode, in hookio.Input, record *audit.Record) (string, bool) {
	if slices.Contains(config.ReadOnlyTools, in.ToolName) {
		record.Decision, record.Reason = audit.Skip, reasonToolCannotWrite
		return "", false
	}
	read := c.readAll(record)
	if len(read) == 0 {
		return "", false
	}
	// ターンの途中で切られたワークツリーを初めて見たら、触った時点の HEAD をより正確な基準として記録する。
	c.noteNewTrees(in.SessionID, read)
	seen := c.loadReported(in.SessionID)
	found := c.found(read)
	if len(found) == 0 {
		// 違反が無くても基準を作る（綺麗なワークツリーで始まったセッションが「基準なし」のままだと、その後の汚れが全部
		// 前から在ったことになる）。読めなかったツリーが在るなら書かない（その汚れが後で新しい変更として戻される）。
		if (!seen.baseline || !seen.announced) && len(read) == len(c.Watched) {
			c.saveReported(in.SessionID, map[string]bool{}, true)
		}
		record.Decision, record.Enforced = audit.Allow, true
		return "", false
	}
	var fresh, known []*finding
	for _, f := range found {
		if seen.keys[f.key()] {
			known = append(known, f)
		} else {
			fresh = append(fresh, f)
		}
	}
	// ユーザの発言で受け入れた変更のうえに、そのあと中身が変わったもの。キーは種類とパスだけなので、指紋で見分けて
	// 新しい変更として言う。戻さない（戻す先のコミット済みの内容はユーザの編集より前で、その編集まで消える）。
	shadowed := map[string]bool{}
	if accepted := c.loadAccepted(in.SessionID); len(accepted) > 0 {
		var still []*finding
		for _, f := range known {
			if v, ok := accepted[f.key()]; ok && fingerprint(f) != v {
				shadowed[f.key()] = true
				fresh = append(fresh, f)
			} else {
				still = append(still, f)
			}
		}
		known = still
		if len(shadowed) > 0 {
			// 同じ変更を呼び出しのたびに言わない。言ったぶんは今の指紋を受け入れ済みに直す。
			for _, f := range fresh {
				if shadowed[f.key()] {
					accepted[f.key()] = fingerprint(f)
				}
			}
			c.saveAccepted(in.SessionID, accepted)
		}
	}
	side := sideRoots(read)
	// 横で作業するチェックアウトの変更は戻さないので、報告したときの指紋と違えば新しい変更として言い直す。
	sideSeen := c.loadAccepted(in.SessionID)
	var still []*finding
	for _, f := range known {
		if inSide(f, side) && restorable(f) && !shadowed[f.key()] {
			now := fingerprint(f)
			before := sideSeen[sidePrefix+f.key()]
			if before == "" {
				before = now
			}
			if before != now {
				fresh = append(fresh, f)
				continue
			}
		}
		still = append(still, f)
	}
	known = still
	marks := map[string]string{}
	for _, f := range fresh {
		if inSide(f, side) && restorable(f) && !shadowed[f.key()] {
			marks[sidePrefix+f.key()] = fingerprint(f)
		}
	}
	changed := false
	for k, v := range marks {
		if old, ok := sideSeen[k]; !ok || old != v {
			changed = true
		}
		sideSeen[k] = v
	}
	if changed {
		c.saveAccepted(in.SessionID, sideSeen)
	}
	// 基準が無いセッションでは、今そこに在るものは直前の実行の結果ではない。基準が在れば、起動時に在ったものが
	// まだ伝えていない分に当たる。どちらも原因を付けずに 1 度だけ伝える。
	var carried []*finding
	switch {
	case !seen.baseline:
		carried, fresh = fresh, nil
	case !seen.announced:
		carried, known = known, nil
	}

	restored := map[string]string{}
	tried := false
	if restore == config.Enable {
		for _, r := range read {
			if r.w.SideBySide {
				continue
			}
			var here []*finding
			for _, f := range fresh {
				if sameTree(f, r.w.Tree.Root) && restorable(f) && !shadowed[f.key()] {
					here = append(here, f)
				}
			}
			if len(here) == 0 {
				continue
			}
			tried = true
			changes := make([]git.Change, len(here))
			for i, f := range here {
				changes[i] = f.change
			}
			done := c.restore(r.top, changes)
			for _, f := range here {
				if at, ok := done[f.change.Path]; ok {
					restored[f.key()] = at
				}
			}
		}
	}
	// dry-run では戻さない代わりに、戻していたはずだと言う（戻しを切った形と見分けが付かないと、予行が「戻しは要らない」と
	// 読み替えられる）。言うのは本番で戻すのと同じ集合だけ。
	would := map[string]bool{}
	if restore == config.DryRun {
		for _, f := range fresh {
			if restorable(f) && !shadowed[f.key()] && !inSide(f, side) {
				would[f.key()] = true
			}
		}
	}

	// 戻せたものは記録に入れない。同じ場所がもう一度汚れたら、それは新しい出来事なのでもう一度言う。
	next := seen.keys
	for _, f := range fresh {
		if _, ok := restored[f.key()]; !ok {
			next[f.key()] = true
		}
	}
	for _, f := range carried {
		next[f.key()] = true
	}
	c.saveReported(in.SessionID, next, true)

	recordFound(record, fresh)
	if mixed := codesNote(fresh); mixed != "" {
		addDetail(record, mixed)
	}
	if len(carried) > 0 {
		addDetail(record, fmt.Sprintf("preexisting %d", len(carried)))
	}
	if len(known) > 0 {
		addDetail(record, fmt.Sprintf("known %d", len(known)))
	}
	sideFresh := 0
	for _, f := range fresh {
		if inSide(f, side) && restorable(f) {
			sideFresh++
		}
	}
	if sideFresh > 0 {
		addDetail(record, fmt.Sprintf("side-by-side %d", sideFresh))
	}
	switch {
	case len(restored) > 0:
		addDetail(record, fmt.Sprintf("restored %d", len(restored)))
	case len(would) > 0:
		addDetail(record, fmt.Sprintf("%s %d", selfguard.ActionWould, len(would)))
	}
	if len(fresh) > 0 {
		record.Decision, record.Enforced = audit.Deny, enforcing
		record.Code = recordCode(fresh)
	} else {
		// この呼び出しは何も汚していない。記録した変更の報告は状態の通知で、直前の実行についての判定ではない。
		record.Decision, record.Enforced = audit.Allow, true
		if len(carried) > 0 {
			record.Code = codePreexisting
		}
	}

	gated := restore == config.Enable || restore == config.DryRun
	gitCommand := gitWrapper(c.Root)
	var blocks []string
	for _, f := range fresh {
		at, done := restored[f.key()]
		o := outcome{
			restored:    done,
			moved:       at,
			would:       would[f.key()],
			notRestored: gated && (!restorable(f) || shadowed[f.key()]),
			shadowed:    shadowed[f.key()],
			sideBySide:  inSide(f, side) && restorable(f),
		}
		blocks = append(blocks, violation(f, in, o, gitCommand, c.Root))
	}
	for _, f := range carried {
		blocks = append(blocks, preexisting(f, c.Root))
	}
	if len(blocks) == 0 {
		return "", tried
	}
	shown := blocks[:min(len(blocks), ReportLimit)]
	if dropped := len(blocks) - ReportLimit; dropped > 0 {
		shown = append(shown, texts.Say(c.Root, "NOTICE_POST_DROPPED",
			texts.Values{"dropped": fmt.Sprint(dropped), "git": gitCommand}, gitVariant(gitCommand)))
	}
	return strings.Join(shown, "\n\n"), tried
}

// restore は戻せたものを {パス: 退避先（戻しただけなら空）} で返す。戻せなかった 1 件は手順付きの報告として残る。
func (c *Checker) restore(top string, changes []git.Change) map[string]string {
	aside := ospath.Join(c.State, asideDir, time.Now().Format(fsio.NameStampLayout))
	done := map[string]string{}
	for _, change := range changes {
		if failed := git.Restore(top, change, aside); failed != "" {
			fmt.Fprintf(c.Stderr, "ccnavi: %s を戻せない: %s\n", change.Path, failed)
			continue
		}
		if change.Kind == git.KindNew {
			done[change.Path] = aside
		} else {
			done[change.Path] = ""
		}
	}
	return done
}

// noteNewTrees はターンの基準にまだ居ないツリーの HEAD を記録する。書き直すのは居ないツリーが 1 本でもあるときだけで、
// 基準そのもの（ターンの始まりに何が汚れていたか）は動かさない。
func (c *Checker) noteNewTrees(session string, read []readTree) {
	t := c.loadTurn(session)
	if !t.known {
		// ターンの始まりを見ていない。HEAD だけを置くと「基準はあるが baseline が空」になる。
		return
	}
	added := false
	for _, r := range read {
		if _, ok := t.heads.sha[r.top]; r.top == "" || ok {
			continue
		}
		if sha := git.Head(r.top, git.Options{Timeout: git.StateTimeout}); sha != "" {
			t.heads.put(r.top, sha)
			added = true
		}
	}
	if added {
		c.saveTurn(session, t.baseline, t.heads)
	}
}

// committedFindings はこのターンでコミットに入った保護領域の変更と、数えなかったツリーの名前。基準はターンの始まりの
// HEAD で、記録を持たないツリーは記録済みの他のツリーとの分岐点。チケットの置き場は外す（承認はユーザが提案を動かして
// コミットする進め方なので、外さないと承認のたびに報告に並ぶ）。数えなかったことを言わないと、見ていない期間が
// 「何も起きなかった」と同じ見た目になる。
func (c *Checker) committedFindings(read []readTree, h heads) ([]*finding, []string) {
	var out []*finding
	var uncounted []string
	for _, r := range read {
		name := r.w.Tree.Name
		if name == "" {
			name = mainTreeName
		}
		base := h.sha[r.top]
		if base == "" {
			var others []string
			for _, top := range h.order {
				if top != r.top {
					others = append(others, h.sha[top])
				}
			}
			base = git.ForkBase(r.top, others, 0)
		}
		if base == "" {
			uncounted = append(uncounted, name)
			continue
		}
		changes, unreadable := git.Committed(r.top, base, 0)
		if unreadable != "" {
			fmt.Fprintf(c.Stderr, "ccnavi: コミット済みの差分を読めない（%s）: %s\n", name, unreadable)
			uncounted = append(uncounted, name)
			continue
		}
		// integrate-done が積んだ squash だけが変えたパスは外す（片付けの後なので staged の除外は効かない）。
		var integrated map[string]bool
		if len(changes) > 0 {
			integrated = integratedPaths(r.top, base)
		}
		s := c.scanOf(readTree{r.w, r.top, changes}, false)
		for _, f := range s.findings() {
			if integrated[f.change.Path] || c.Places.holds(tree.Relative(r.w.Tree, f.change.Full)) {
				continue
			}
			out = append(out, f)
		}
	}
	return out, uncounted
}

// AtStop はターンの終わりに、このターンで変わった保護領域をユーザへ報告する。宛先はモデルではなくユーザで、セッションの
// 記録（モデルへ 1 度伝えた）は見ず、ターンの始まりの基準に無いものだけを言う。戻しもしない（どうするかはユーザが決める）。
// ターンの始まりを見ていなければ何も言わない（ユーザの書きかけまでこのターンの成果として並べてしまう）。
func (c *Checker) AtStop(in hookio.Input, record *audit.Record) string {
	t := c.loadTurn(in.SessionID)
	if !t.known {
		record.Decision, record.Reason = audit.Skip, reasonNoTurnBaseline
		return ""
	}
	read := c.readAll(record)
	if len(read) == 0 {
		return ""
	}
	var found []*finding
	for _, f := range c.found(read) {
		if !t.baseline[f.key()] {
			found = append(found, f)
		}
	}
	// `git status` はコミットを見せないので、このターンでコミットに入ったぶんも足す。
	side := sideRoots(read)
	committed, uncounted := c.committedFindings(read, t.heads)
	found = append(found, committed...)
	if len(found) == 0 && len(uncounted) == 0 {
		record.Decision, record.Enforced = audit.Allow, true
		return ""
	}
	gitCommand := gitWrapper(c.Root)
	if len(found) == 0 {
		record.Decision, record.Enforced = audit.Allow, true
		addDetail(record, fmt.Sprintf("uncounted %d", len(uncounted)))
		return c.uncountedLine(uncounted, true, gitCommand)
	}
	record.Decision, record.Enforced = audit.Deny, true
	record.Code = recordCode(found)
	recordFound(record, found)
	addDetail(record, codesNote(found))

	// 見出しの主語は、何が変わったか。1 件ならそのパスと変わり方を示す。
	subject := fmt.Sprintf("保護対象の %d 件", len(found))
	if len(found) == 1 {
		first := found[0].change
		subject = "保護対象の " + first.Path + "（" + kindLabel(first.Kind) + "）"
	}
	lines := []string{"[ccnavi] " + subject + "が、この" + useronly.TurnDefined + "で変わりました。"}
	for _, f := range found[:min(len(found), ReportLimit)] {
		where := ""
		if f.treeName != "" {
			where = f.treeName + ": "
		}
		undo := git.Undo(f.change, gitCommand)
		how := "戻すなら " + undo
		switch {
		case undo != "":
		case f.change.Kind == git.KindCommitted:
			how = f.change.Path + " はすでにコミット済みなので、取り込む前に中身を確かめ、不要な変更なら取り消してください"
		default:
			how = f.change.Path + " を元に戻す操作はユーザに頼んでください"
		}
		origin := ""
		if inSide(f, side) && restorable(f) {
			origin = " " + sideBySideLabel + "。"
		}
		lines = append(lines, "  "+where+f.change.Path+"（"+kindLabel(f.change.Kind)+"） 対象のルール: "+
			rules.RecordName(f.group[0])+" /"+origin+" "+how)
	}
	if len(found) > ReportLimit {
		all := texts.Say(c.Root, "NOTICE_POST_ALL_AT", texts.Values{"git": gitCommand}, gitVariant(gitCommand))
		lines = append(lines, fmt.Sprintf("  ほか %d 件。%s", len(found)-ReportLimit, all))
	}
	if len(uncounted) > 0 {
		lines = append(lines, c.uncountedLine(uncounted, false, gitCommand))
		addDetail(record, fmt.Sprintf("uncounted %d", len(uncounted)))
	}
	return strings.Join(lines, "\n")
}

func kindLabel(k git.Kind) string {
	if label, ok := git.KindLabels[k]; ok {
		return label
	}
	return string(k)
}

// uncountedLine はコミット済みを数えなかったツリーを言う 1 行。define は「ターン」の定義をつけるか（同じ報告の前の行で
// つけていれば外す）。
func (c *Checker) uncountedLine(uncounted []string, define bool, gitCommand string) string {
	set := map[string]bool{}
	for _, n := range uncounted {
		set[n] = true
	}
	names := sorted(set)
	shown := strings.Join(names[:min(len(names), ReportLimit)], ", ")
	trees := "ワークツリー " + shown
	if len(names) > 1 {
		trees = fmt.Sprintf("ワークツリー %d 本（%s）", len(names), shown)
	}
	turnWord := "ターン"
	if define {
		turnWord = useronly.TurnDefined
	}
	return texts.Say(c.Root, "NOTICE_POST_UNCOUNTED", texts.Values{"turn": turnWord, "trees": trees, "git": gitCommand}, gitVariant(gitCommand))
}

// AtPrompt はターンの始まりに、いま保護領域に在る変更を記録してこのターンの基準にする。accept はユーザの発言で始まる
// ターンのとき真で、今そこにある変更を報告済みにも足し、次の実行後チェックが戻さないようにする（足さないと、ユーザが
// セッションの途中で deny の場所を直した分が戻される）。足すのは起動時の基準が取れているセッションだけ。
func (c *Checker) AtPrompt(in hookio.Input, record *audit.Record, accept bool) {
	read := c.readAll(record)
	if len(read) == 0 {
		// 基準を取れなかった。前のターンの記録が残ると、その HEAD をこのターンの始まりとして読み、前のターンのコミットを
		// このターンの成果として並べるので消す。
		fsio.Remove(statePath(c.State, in.SessionID, turnSuffix))
		return
	}
	found := c.found(read)
	h := heads{sha: map[string]string{}}
	for _, r := range read {
		if r.top == "" {
			continue
		}
		if sha := git.Head(r.top, git.Options{Timeout: git.StateTimeout}); sha != "" {
			h.put(r.top, sha)
		}
	}
	c.saveTurn(in.SessionID, keys(found), h)
	record.Decision, record.Enforced = audit.Allow, true
	if len(found) > 0 {
		record.Detail = fmt.Sprintf("turn baseline %d", len(found))
	}
	if !accept || len(found) == 0 {
		return
	}
	seen := c.loadReported(in.SessionID)
	if !seen.baseline {
		return
	}
	next := seen.keys
	for k := range keys(found) {
		next[k] = true
	}
	c.saveReported(in.SessionID, next, seen.announced)
	accepted := c.loadAccepted(in.SessionID)
	for _, f := range found {
		accepted[f.key()] = fingerprint(f)
	}
	c.saveAccepted(in.SessionID, accepted)
}

// CaptureBaseline はセッションの始まり（SessionStart の startup）に在る保護領域の変更を、基準として「まだ伝えていない」で
// 記録する。読めなかったツリーが 1 つでもあれば書かない（その汚れを後で新しい変更として差し戻すことになる）。
func (c *Checker) CaptureBaseline(session string, record *audit.Record) {
	if c.State == "" {
		return
	}
	read := c.readAll(record)
	if len(read) != len(c.Watched) {
		return
	}
	c.saveReported(session, keys(c.found(read)), false)
}
