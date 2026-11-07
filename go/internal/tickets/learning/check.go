package learning

import (
	"io"
	"strconv"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/config/phases"
	"ccnavi/internal/tickets/model/ticket"
)

const ()

// phaseOff はこの子のフェーズ定義（親に固定したコピー）が learning: off か。
func phaseOff(conf *config.Settings, root string, found *ticket.Ticket) bool {
	number, ok := found.ChildPhase()
	if !found.IsChild() || !ok {
		return false
	}
	copies, _ := approval.Scan(conf, root, false, nil)
	parent := approval.ByID(copies)[found.Parent]
	if parent == nil || !parent.HasPlan() {
		return false
	}
	item := parent.ItemAt(number)
	if item == nil {
		return false
	}
	pt := phases.TypesOf(parent).Get(item.Type)
	return pt != nil && pt.Learning == phases.LearningOff
}

// Applies は学びの蓄積がこの子に効くか。全体が有効で、統合でない子で、フェーズ定義が off でない。
func Applies(conf *config.Settings, root string, found *ticket.Ticket) bool {
	return conf.LearningEnabled() && found.IsChild() && !found.IsConsolidate() && !phaseOff(conf, root, found)
}

// headText は HEAD の通常のファイル rel の中身。HEAD に無い・リンクである・読めないときは ok が偽。
func headText(treeRoot, rel string) (string, bool) {
	code, out := git.Output(treeRoot, []string{"ls-tree", "HEAD", "--", rel}, git.Options{})
	if code != 0 || ospath.Strip(out) == "" {
		return "", false
	}
	parts := strings.FieldsFunc(out, ospath.IsSpace)
	if len(parts) < 2 || !regularModes[parts[0]] || parts[1] != "blob" {
		return "", false
	}
	code, out = git.Output(treeRoot, []string{"show", "HEAD:" + rel}, git.Options{})
	if code != 0 {
		return "", false
	}
	return out, true
}

// RecordBlocked は子の finish の学びの検査。止めたなら理由を stderr に書いて真。
// 親の finish と同じ読み取りの検査と整合の検査を子の HEAD の記録に当てる。親で初めて断ると、閉じた子へ戻って直す手間が出る。
func RecordBlocked(stderr io.Writer, conf *config.Settings, root string, found *ticket.Ticket) bool {
	if !Applies(conf, root, found) {
		return false
	}
	rel := RecordRel(found.ID)
	text, ok := headText(tree.WorktreePath(root, found.ID), rel)
	if !ok || ospath.Strip(text) == "" {
		write(stderr, "ccnavi: "+found.ID+" の気づきの記録（"+rel+"）が、子のブランチの HEAD に無いか、"+
			"空か、リンクになっている（コミットが必要です）。"+
			"'"+config.ScriptCommand(root, config.TicketWrapper)+" learn' を実行して記録を書き、"+
			"git add と git commit をしてから閉じてください"+
			"（気づきが無ければ learnings: none のままでよい）\n")
		return true
	}
	rec, why := parse(found.ID, text)
	if rec == nil {
		write(stderr, "ccnavi: 気づきの記録 "+rel+" を読み取れない: "+why+"\n")
	} else if why = consistencyProblem(rec); why != "" {
		write(stderr, "ccnavi: 気づきの記録 "+rel+" が整合の検査にヒットする: "+why+"\n")
	} else {
		return false
	}
	write(stderr, "  記録を直してコミットしてから閉じてください"+
		"（`learnings:` は `none` か件数の数値、気づきは `## ` 見出しの節に書く）\n")
	return true
}

// ParentBlocked は親の finish の学びの検査。止めたなら理由を stderr に書いて真。学び以外の検査を通したあと、
// 統合を済ませた後も毎回、親の今の HEAD で見る（未コミットの変更・直せない記録・処理すべきもの）。
func ParentBlocked(stderr io.Writer, conf *config.Settings, root string, found *ticket.Ticket) bool {
	if found.IsChild() || !conf.LearningEnabled() {
		return false
	}
	parentTree := tree.WorktreePath(root, found.ID)
	if !ospath.IsDir(parentTree) {
		return false
	}
	ticketSh := config.ScriptCommand(root, config.TicketWrapper)
	if dirty := Uncommitted(parentTree); len(dirty) > 0 {
		write(stderr, "ccnavi: "+found.ID+" の学びの置き場に未コミットか未追跡の変更がある: "+
			strings.Join(dirty, ", ")+"。コミットしてから '"+ticketSh+" finish "+found.ID+"' を打ち直してください\n")
		return true
	}
	pending := Find(parentTree)
	if pending.Broken() {
		WriteBroken(stderr, pending)
		return true
	}
	if len(pending.Items) == 0 {
		return false
	}
	write(stderr, "ccnavi: "+found.ID+" には、判定されていない気づきがある（"+strconv.Itoa(len(pending.Items))+" 件）:\n")
	for _, item := range pending.Items {
		write(stderr, "  - "+item.Describe()+"\n")
	}
	write(stderr, "  '"+ticketSh+" consolidate "+found.ID+"' で統合の子を作り、その子を処理して閉じたあと、"+
		"'"+ticketSh+" finish "+found.ID+"' を打ち直してください\n")
	write(stderr, texts.Say(root, "NOTE_LEARN_PROCESS_FORM", nil, nil)+"\n")
	return true
}

// WriteBroken は直せない記録を名指しする。統合では直せない。
func WriteBroken(stderr io.Writer, found *Pending) {
	for _, p := range found.Unreadable {
		write(stderr, "ccnavi: 気づきの記録 "+p.Path+" を読み取れない: "+p.Why+"\n")
	}
	for _, p := range found.Inconsistent {
		write(stderr, "ccnavi: 気づきの記録 "+p.Path+" が整合の検査にヒットする: "+p.Why+"\n")
	}
	write(stderr, "  統合では直せない。記録を直して（訂正や追加は新しい節を足す。処理済みの節の本文は"+
		"書き換えない）コミットしてから打ち直してください\n")
}

// write は標準エラーへの 1 回の書き込み。書けなくても続ける（Python の sys.stderr.write と同じく結果を見ない）。
func write(w io.Writer, text string) { _, _ = io.WriteString(w, text) }
