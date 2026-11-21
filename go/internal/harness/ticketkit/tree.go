package ticketkit

import (
	"encoding/json"
	"fmt"
	"hash/fnv"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/samples"
	"ccnavi/internal/harness/treecache"
)

// ParentName は Tree が最初に切る親のワークツリー（と親のブランチ）の名前。
const ParentName = "i0001"

// Options は New の指定。
type Options struct {
	// Phases は共通レイヤーの phases.yml の本文。空なら samples.PlainPhases（TicketText の親が使う最小の定義）。
	Phases string
	// Under があれば、木の根をその下に作る（パスの形を試すとき。既定は base.TempDir）。
	Under string
	// NoMark を立てると、状態の操作に write-and-push の印を付けない（印が無い呼び出しを断る検査）。
	NoMark bool
}

// Tree は git のリポジトリ（main に 1 コミット）と、親のワークツリー i0001 を組んだ使い捨ての木。
//
// ルートに共通レイヤーのルールとフェーズ定義を置き、状態の置き場は Root/logs/state。承認済みチケットとマーカーは
// 親のツリーに置かれ、親のブランチに乗る（Approved）。木はテストごとに作るので、並行に回せる。
type Tree struct {
	T          testing.TB
	Root       string
	State      string
	ParentTree string
	Approved   string
	Phases     string
	mark       bool
}

// New は木を組む。
func New(t testing.TB, opts ...Options) *Tree {
	t.Helper()
	var o Options
	if len(opts) > 0 {
		o = opts[0]
	}
	root := base.TempDir(t)
	if o.Under != "" {
		root = tempUnder(t, o.Under)
	}
	return initTree(t, root, o)
}

// attach は root に組んである木を Tree として扱う（何も起こさない）。
func attach(t testing.TB, root string, o Options) *Tree {
	tr := &Tree{T: t, Root: root, State: filepath.Join(root, "logs", "state"), mark: !o.NoMark, Phases: o.Phases}
	tr.ParentTree = tr.ChildTree(ParentName)
	tr.Approved = filepath.Join(tr.ParentTree, ".ccnavi", "approved")
	return tr
}

// initTree は root に git のリポジトリと親のワークツリーを組む。
func initTree(t testing.TB, root string, o Options) *Tree {
	t.Helper()
	tr := attach(t, root, o)
	Write(t, filepath.Join(root, ".claude", "keep"), "")
	Git(t, root, "init", "--quiet", "-b", "main")
	Write(t, filepath.Join(root, "src", "keep.py"), "print(1)\n")
	Write(t, filepath.Join(root, ".gitignore"), ".claude/\nwip/tmp/\n")
	Git(t, root, "add", "-A")
	Git(t, root, "commit", "--quiet", "-m", "init")
	// 共通レイヤーは既定の置き場に置く（--rules・--phases は診断でだけ有効で、hook の判定には渡せない）。
	Write(t, base.CommonPath(root, "rules"), samples.Rules)
	if o.Phases == "" {
		samples.WritePlainPhases(t, root)
	} else {
		Write(t, base.ConfigPath(root, "phases"), o.Phases)
	}
	tr.Worktree(ParentName, "main")
	return tr
}

// Cached は、組む手順（build）の結果を鍵ごとに取っておき、2 度目からは写しへ戻した木を返す（プロセスを起こさない）。
// 木の根は鍵ごとの固定のパス。同じ鍵を使うテストは順に並ぶ（t.Parallel() は Cached より前に呼ぶ。決まりは treecache の doc）。
// 鍵は組み方を言い、手順が引数で変わるなら引数を鍵に入れる。
func Cached(t testing.TB, key string, o Options, build func(tr *Tree)) *Tree {
	t.Helper()
	dir, _ := treecache.Build(t, key+shardOf(t), func(dir string) struct{} {
		tr := initTree(t, dir, o)
		build(tr)
		return struct{}{}
	})
	return attach(t, dir, o)
}

// shards は同じ組み方の木の写しを何組持つか。同じ鍵を使うテストは順に並ぶので、写しを分けて並行に回す
// （1 組だと、鍵を使うテスト全部の時間が足し算になり、鍵を待つ上限（treecache の lockWait）も超えうる）。
const shards = 4

// shardOf はテスト名から決める写しの組の印（同じテストはいつも同じ組を使う）。
func shardOf(t testing.TB) string {
	h := fnv.New32a()
	_, _ = h.Write([]byte(t.Name()))
	return fmt.Sprintf("#%d", h.Sum32()%shards)
}

// FamilyTree は Family まで組んだ木（Cached）。
func FamilyTree(t testing.TB, o FamilyOpts) *Tree {
	t.Helper()
	return Cached(t, fmt.Sprintf("family %+v", o), Options{}, func(tr *Tree) { tr.Family(o) })
}

// ClosedPhaseTree は Family のフェーズ 1 を閉じるまで組んだ木（Cached）。
func ClosedPhaseTree(t testing.TB, o FamilyOpts) *Tree {
	t.Helper()
	return Cached(t, fmt.Sprintf("closed-phase %+v", o), Options{}, func(tr *Tree) { tr.Family(o); tr.ClosePhase() })
}

// ClosedTree は Family のフェーズ 1 を閉じ、origin と合流・push した親のブランチ（と結果ファイル）まで組んだ木（Cached）。
func ClosedTree(t testing.TB) (*Tree, Fixture) {
	t.Helper()
	tr := Cached(t, "closed", Options{}, func(tr *Tree) { tr.Family(FamilyOpts{}); tr.ClosePhase(); tr.Remote(nil) })
	return tr, tr.Fixture()
}

// RequestedTree は ClosedTree に依頼（request）を通した木（Cached）。
func RequestedTree(t testing.TB) (*Tree, Fixture) {
	t.Helper()
	tr := Cached(t, "requested", Options{}, func(tr *Tree) {
		tr.Family(FamilyOpts{})
		tr.ClosePhase()
		OK(tr.T, tr.Request(tr.Remote(nil)))
	})
	return tr, tr.Fixture()
}

// Worktree は root の下の .claude/worktrees/<name> に、from から切ったブランチ name のワークツリーを作る。
func (tr *Tree) Worktree(name, from string) string {
	tr.T.Helper()
	path := tr.ChildTree(name)
	Git(tr.T, tr.Root, "worktree", "add", "--quiet", path, "-b", name, from)
	return path
}

// ChildTree はワークツリー name の置き場所（作らない）。
func (tr *Tree) ChildTree(name string) string {
	return filepath.Join(tr.Root, ".claude", "worktrees", name)
}

// Git は親のワークツリーでなく、指定の dir で git を起こす。
func (tr *Tree) Git(dir string, args ...string) string {
	tr.T.Helper()
	return Git(tr.T, dir, args...)
}

// Parent は親のワークツリーで git を起こす。
func (tr *Tree) Parent(args ...string) string {
	tr.T.Helper()
	return Git(tr.T, tr.ParentTree, args...)
}

// Call は ccnavi の 1 回の呼び出しの入力。
type Call struct {
	Stdin string
	// Env は子の環境への差分（run.Opts.Env）。
	Env map[string]string
}

// Ccnavi は --root と保護の切り替えを付けて ccnavi を起こす。保護（コアファイルの守り・拒否したときの巻き戻し・
// ユーザの判断の経路のターミナル要求）は切る。テストはターミナルを持たない。経路そのものの検査は個別に enable を渡す。
func (tr *Tree) Ccnavi(args ...string) run.Result {
	tr.T.Helper()
	return tr.Call(Call{}, args...)
}

// Call は Ccnavi に標準入力と環境を渡す形。
func (tr *Tree) Call(c Call, args ...string) run.Result {
	tr.T.Helper()
	if tr.mark {
		args = samples.AsWriteAndPush(tr.Root, tr.State, tr.ParentTree, args)
	}
	prefix := []string{"--root", tr.Root, "--guard-core-files", "disable", "--restore-if-deny", "disable", "--guard-ticket-approval", "disable"}
	return run.Ccnavi(tr.T, samples.Place(prefix, args), run.Opts{Input: c.Stdin, Env: c.Env, Dir: base.Root()})
}

// HookOpts は Hook の任意の指定。
type HookOpts struct {
	// Mode は既定で enable。
	Mode string
	// AgentID があればサブエージェントからの呼び出し。
	AgentID string
	// GuardTicketApproval があれば --guard-ticket-approval に渡す（テストの既定は disable）。
	GuardTicketApproval string
	// Session は既定で s1。
	Session string
	// PermissionMode は Claude Code 側の権限モード（どのルールも言及しない呼び出しの結末が変わる）。
	PermissionMode string
	// Extra は payload の最上位に足す欄（PostToolUse の tool_response など）。
	Extra map[string]any
}

// Hook は hook の入口に payload を渡す。input は tool_input。
func (tr *Tree) Hook(event, tool, cwd string, input map[string]any, opts ...HookOpts) run.Result {
	tr.T.Helper()
	var o HookOpts
	if len(opts) > 0 {
		o = opts[0]
	}
	if o.Mode == "" {
		o.Mode = "enable"
	}
	if o.Session == "" {
		o.Session = "s1"
	}
	if input == nil {
		input = map[string]any{}
	}
	payload := map[string]any{"hook_event_name": event, "tool_name": tool, "cwd": cwd, "session_id": o.Session, "tool_input": input}
	if o.AgentID != "" {
		payload["agent_id"] = o.AgentID
	}
	if o.PermissionMode != "" {
		payload["permission_mode"] = o.PermissionMode
	}
	for k, v := range o.Extra {
		payload[k] = v
	}
	raw, err := json.Marshal(payload)
	if err != nil {
		tr.T.Fatal(err)
	}
	args := []string{"--mode", o.Mode}
	if o.GuardTicketApproval != "" {
		args = append(args, "--guard-ticket-approval", o.GuardTicketApproval)
	}
	return tr.Call(Call{Stdin: string(raw)}, args...)
}

// Judge は PreToolUse の判定の理由の文（無ければ追加の文脈。出力が空なら空）。
func (tr *Tree) Judge(tool, cwd string, input map[string]any, opts ...HookOpts) string {
	tr.T.Helper()
	return Reason(tr.T, tr.Hook("PreToolUse", tool, cwd, input, opts...))
}

// WriteReason は cwd から file へ Write するときの判定の理由。
func (tr *Tree) WriteReason(cwd, file string) string {
	tr.T.Helper()
	return tr.Judge("Write", cwd, map[string]any{"file_path": file})
}

// Bash は cwd で command を打つときの判定の理由。
func (tr *Tree) Bash(cwd, command string) string {
	tr.T.Helper()
	return tr.Judge("Bash", cwd, map[string]any{"command": command})
}

// Spawn は cwd からサブエージェントを起こすときの判定の理由。
func (tr *Tree) Spawn(cwd, description string) string {
	tr.T.Helper()
	return tr.Judge("Agent", cwd, map[string]any{"description": description})
}

// LastRules は判定の記録（logs/decisions.jsonl）の最後の 1 行の rules。
func (tr *Tree) LastRules() []string {
	tr.T.Helper()
	lines := Lines(Read(tr.T, filepath.Join(tr.Root, "logs", "decisions.jsonl")))
	var last struct {
		Rules []string `json:"rules"`
	}
	if err := json.Unmarshal([]byte(lines[len(lines)-1]), &last); err != nil {
		tr.T.Fatal(err)
	}
	return last.Rules
}

// Propose は提案を書く。tree が空なら親 i0001 のワークツリー。別の親の提案は、識別子と同じ名前のワークツリーに書く
// （別名のツリーの提案は承認が止まる）。
func (tr *Tree) Propose(name, text, tree string) string {
	tr.T.Helper()
	if tree == "" {
		tree = tr.ParentTree
	}
	return Write(tr.T, filepath.Join(tree, "wip", "proposals", "todo", name+".md"), text)
}

// ProposeTicket は TicketText の提案を親のワークツリーに書く。
func (tr *Tree) ProposeTicket(name string, o TicketOpts) string {
	tr.T.Helper()
	return tr.Propose(name, TicketText(name, o), "")
}

// TodoPath は親のワークツリーの提案 name の場所。
func (tr *Tree) TodoPath(name string) string {
	return filepath.Join(tr.ParentTree, "wip", "proposals", "todo", name+".md")
}

// TodoPathIn はワークツリー tree の提案 name の場所。
func (tr *Tree) TodoPathIn(tree, name string) string {
	return filepath.Join(tree, "wip", "proposals", "todo", name+".md")
}

// ApprovedPath は承認済みチケットの場所（state は doing・done）。
func (tr *Tree) ApprovedPath(state, name string) string {
	return filepath.Join(tr.Approved, state, name+".md")
}

// CommitParent は親のワークツリーの変更を全部コミットする（空でもよい）。
func (tr *Tree) CommitParent(message string) {
	tr.T.Helper()
	tr.Parent("add", "-A")
	tr.Parent("commit", "--quiet", "--allow-empty", "-m", message)
}

// Agree は `user agree <ids…>` に answer を渡す。
func (tr *Tree) Agree(answer string, ids ...string) run.Result {
	tr.T.Helper()
	return tr.Call(Call{Stdin: answer + "\n"}, append([]string{"user", "agree"}, ids...)...)
}

// Approve は承認して、承認済みチケットを親のブランチに乗せる。本番で ccnavi-user-agree.sh がやることを同じ順でたどる
// （承認済みチケットは親のツリーに置かれ、コミットして初めて子のワークツリーへ渡る）。
func (tr *Tree) Approve() run.Result {
	tr.T.Helper()
	r := tr.Agree("y")
	if Exists(tr.Approved) {
		tr.CommitParent("approve")
	}
	return r
}

// StartParent は親を着手する。済んでいれば何もしない（子の着手は親が着手済みであることを前提にする）。
func (tr *Tree) StartParent(name ...string) {
	tr.T.Helper()
	id := ParentName
	if len(name) > 0 {
		id = name[0]
	}
	if r := tr.Ccnavi("ticket", "start", id); r.Code != 0 && !strings.Contains(r.Stderr, "着手済み") {
		tr.T.Fatalf("親の着手に失敗:\n%s%s", r.Stdout, r.Stderr)
	}
}

// FamilyOpts は Family の指定。
type FamilyOpts struct {
	// Review は 2 つの子の human_review。nil なら {true, false}。
	Review []bool
	// Plan は親の計画。nil なら work 1 つ。
	Plan []string
	// Allow は親の範囲（nil なら src/*・wip/*）、ChildAllow は 1 つ目の子の範囲（nil なら src/a/*）。
	Allow, ChildAllow []string
}

// Family は親 1 本と子 2 本（i0001-01-01・i0001-01-02）をフェーズ 1 で提案し、承認して、
// 子のワークツリーを作って着手する。親も着手する。
func (tr *Tree) Family(o FamilyOpts) {
	tr.T.Helper()
	review := o.Review
	if review == nil {
		review = []bool{true, false}
	}
	allow, childAllow := o.Allow, o.ChildAllow
	if allow == nil {
		allow = []string{"src/*", "wip/*"}
	}
	if childAllow == nil {
		childAllow = []string{"src/a/*"}
	}
	tr.ProposeTicket("i0001", TicketOpts{Allow: allow, Plan: o.Plan})
	tr.ProposeTicket("i0001-01-01", TicketOpts{Parent: "i0001", Phase: 1, Allow: childAllow, NoReview: !review[0]})
	tr.ProposeTicket("i0001-01-02", TicketOpts{Parent: "i0001", Phase: 1, Allow: []string{"src/b/*"}, NoReview: !review[1]})
	tr.CommitParent("tickets")
	OK(tr.T, tr.Approve())
	tr.StartParent()
	for _, child := range []string{"i0001-01-01", "i0001-01-02"} {
		tr.Worktree(child, "i0001")
		OK(tr.T, tr.Ccnavi("ticket", "start", child))
	}
	// 状態の移動は親がコミットする。
	tr.CommitParent("start")
}

// ClosePhase は Family の子 2 本を閉じて、親がコミットする（フェーズ 1 が終わる）。
func (tr *Tree) ClosePhase() {
	tr.T.Helper()
	for _, child := range []string{"i0001-01-01", "i0001-01-02"} {
		OK(tr.T, tr.Ccnavi("ticket", "finish", child))
	}
	tr.CommitParent("done")
}

// CloseParentByHand は、親のワークツリーの承認済みチケットを doing/ から done/ へ手で動かす
// （道具は開いた子がある親を閉じさせないので、置き場を手で動かしたときにしか作れない形）。
func (tr *Tree) CloseParentByHand(name string) {
	tr.T.Helper()
	if err := os.MkdirAll(filepath.Join(tr.Approved, "done"), 0o755); err != nil {
		tr.T.Fatal(err)
	}
	if err := os.Rename(tr.ApprovedPath("doing", name), tr.ApprovedPath("done", name)); err != nil {
		tr.T.Fatal(err)
	}
	tr.CommitParent("close " + name)
}

// HandMove は提案を承認済みの置き場へ手で動かす（GitHub の画面でやることと同じ）。text が空なら中身はそのまま。
// ccnavi_approved は足さない（ターミナルもボードも無いユーザには足す手段が無い）。
func (tr *Tree) HandMove(name, text string) {
	tr.T.Helper()
	source := tr.TodoPath(name)
	if text == "" {
		text = Read(tr.T, source)
	}
	if err := os.Remove(source); err != nil {
		tr.T.Fatal(err)
	}
	Write(tr.T, tr.ApprovedPath("doing", name), text)
	tr.Parent("add", "-A")
	tr.Parent("commit", "--quiet", "-m", "move "+name)
}

// tempUnder は dir の下に使い捨ての置き場を作り、実パスで返す（テストの終わりに消す）。
func tempUnder(t testing.TB, dir string) string {
	t.Helper()
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	made, err := os.MkdirTemp(dir, "cc-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.RemoveAll(made) })
	real, err := filepath.EvalSymlinks(made)
	if err != nil {
		t.Fatal(err)
	}
	return real
}
