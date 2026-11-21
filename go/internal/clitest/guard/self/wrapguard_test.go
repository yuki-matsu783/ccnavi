package self

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/exe"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/infra/env"
)

// 保護済みの sh を呼ぶ形のうち、実行前に止める 2 つ（hook/shellguard）と、サブエージェントが親のワークツリーを
// 書き換えないこと、ラッパーで自己防衛の対象を消す形。止める・通すの判定は hook が持ち、sh の検査は 2 つ目の確認にする。
//
// 見本の表（childCases・coreCases）は、ラッパーの sh にも同じものを渡して、sh の検査が 2 つ目として同じく止めることを
// 確かめる（TestShAgrees…）。hook と sh の検査は同じことを別の実装で持つので、食い違いはこの表で見つける。
const (
	codeEnv         = "DENY_SCRIPT_ENV_OVERRIDE"
	codeChildPush   = "DENY_CHILD_PUSH"
	codeCorePath    = "DENY_WRAPPER_CORE_PATH"
	codeParentTree  = "DENY_SUBAGENT_PARENT_TREE"
	childPushRule   = "builtin:child-push"
	corePathRule    = "builtin:guard-wrapper-paths"
	parentTreeRule  = "builtin:subagent-parent-tree"
	wrapperFromTree = "sh ../../../.ccnavi/scripts/ccnavi-git.sh"
)

func gitWrapper(sub string) string { return wrapperFromTree + " " + sub }

// ticketText は frontmatter だけのチケット。parent が空なら親。
func ticketText(name, parent string) string {
	lines := []string{"---", "version: 1", "ticket: " + name}
	if parent != "" {
		lines = append(lines, "parent: "+parent, "phase: 1")
	}
	lines = append(lines, "title: t", "---", "")
	return strings.Join(lines, "\n") + "\n"
}

// wrapEnv はラッパーが引数の形を実行ファイル（`ccnavi git check`）に確かめるための環境。
func wrapEnv(t *testing.T, extra map[string]string) map[string]string {
	t.Helper()
	vars := map[string]string{env.EntryPath: exe.ShPath(t)}
	for k, v := range extra {
		vars[k] = v
	}
	return vars
}

// wrapRoot は .claude と最小の共通ルールを持つ木。
func wrapRoot(t *testing.T, rules any) string {
	t.Helper()
	root := base.TempDir(t)
	mkdirs(t, filepath.Join(root, ".claude"))
	write(t, base.CommonPath(root, "rules"), ruleBlob(t, rules))
	return root
}

// allowWrapper は同じコマンドを通す allow を置いた共通ルール。ルールが先にヒットするなら通ってしまう。
var allowWrapper = payload{
	"version": 1,
	"allow":   []payload{{"id": "wrap", "match": "Bash", "regex": `ccnavi-git\.sh`}},
}

// 環境変数の代入を hook の入口から通す。止める・通す形の表は hook/shellguard の TestCheckEnvTable が見る。
// ここでは、判定の中で「ccnavi 自身を守る設定」で切れないことと、ルールより先に止めることを見る。
func TestEnvAssignmentStopsBeforeAnAllowRule(t *testing.T) {
	t.Parallel()
	const command = "CCNAVI_WORKSPACE=/x sh .ccnavi/scripts/ccnavi-git.sh push"
	root := wrapRoot(t, allowWrapper)
	run1 := func(command string, extra map[string]string) (string, string) {
		body := bash(command, root)
		body["permission_mode"] = "default"
		args := []string{"--log", "", "--state", filepath.Join(root, "state"), "--mode", "enable"}
		r := hook(t, root, args, body, wrapEnv(t, extra))
		if r.Code != 0 {
			t.Fatalf("code = %d: %s", r.Code, r.Stderr)
		}
		return verdict(t, r)
	}
	if decision, reason := run1(command, nil); decision != "deny" || !strings.Contains(reason, codeEnv) || !strings.Contains(reason, "CCNAVI_WORKSPACE") {
		t.Errorf("allow より先に止めるはず: %s %s", decision, reason)
	}
	// allow がヒットして通る呼び出しは何も出さない。
	if decision, reason := run1("sh .ccnavi/scripts/ccnavi-git.sh push", nil); decision != "" || strings.Contains(reason, codeEnv) {
		t.Errorf("代入が無ければルールまで届くはず: %s %s", decision, reason)
	}
	// 環境変数の指定の止めは補助の保護で、自己防衛の設定では切れない。
	if decision, reason := run1(command, map[string]string{env.GuardCoreFiles: "disable"}); decision != "deny" || !strings.Contains(reason, codeEnv) {
		t.Errorf("自己防衛を切っても止めるはず: %s %s", decision, reason)
	}
}

// ---- 子チケットのワークツリーからの push ----

// childCases の各行は（cwd の置き場, 副命令, チケットをどこに置くか, 止まるか）。
// 置き場: parent = 親のワークツリー、child = 子のワークツリー、plain = どちらでもない。
// チケットの置き場: parent-doing = 親のツリーの doing/、root-done = ワークスペースの done/、
// parent-review = 親のツリーの wip/proposals/review/、broken = 親のツリーの doing/ に置いた、YAML として読めないもの。
var childCases = []struct {
	where, sub, placed string
	stops              bool
}{
	{"child", "push", "parent-doing", true},
	{"child", "push", "root-done", true},
	{"child", "push", "parent-review", true},
	{"child", "push", "broken", true},
	{"child", "status", "parent-doing", false},
	{"parent", "push", "parent-doing", false},
	{"plain", "push", "parent-doing", false},
}

const brokenTicket = "---\nparent: i0001\n: : [unclosed\n"

// trees はワークスペースの下の 3 つのワークツリー（git に登録しないディレクトリ。sh の検査は登録を問わないので hook も問わない）。
type trees struct {
	root                 string
	parent, child, plain string
}

func newTrees(t *testing.T, rules any) trees {
	t.Helper()
	root := wrapRoot(t, rules)
	dir := filepath.Join(root, ".claude", "worktrees")
	tr := trees{root: root, parent: filepath.Join(dir, "i0001"), child: filepath.Join(dir, "i0001-01-01"), plain: filepath.Join(dir, "plain")}
	for _, p := range []string{tr.parent, tr.child, tr.plain} {
		mkdirs(t, p)
	}
	return tr
}

func (tr trees) cwd(where string) string {
	return map[string]string{"parent": tr.parent, "child": tr.child, "plain": tr.plain}[where]
}

// placeTicket は子チケットを placed の場所へ置き、そのパスを返す。
func (tr trees) placeTicket(t *testing.T, placed string) string {
	t.Helper()
	child := ticketText("i0001-01-01", "i0001")
	switch placed {
	case "parent-doing":
		return write(t, filepath.Join(tr.parent, ".ccnavi/approved/doing/i0001-01-01.md"), child)
	case "root-done":
		return write(t, filepath.Join(tr.root, ".ccnavi/approved/done/i0001-01-01.md"), child)
	case "parent-review":
		return write(t, filepath.Join(tr.parent, "wip/proposals/review/i0001-01-01.md"), child)
	case "broken":
		return write(t, filepath.Join(tr.parent, ".ccnavi/approved/doing/i0001-01-01.md"), brokenTicket)
	}
	t.Fatalf("置き場 %q を知らない", placed)
	return ""
}

// hookAt は cwd から Bash を 1 回通し、拒否の理由を返す（通した・黙った呼び出しは空）。agent があればサブエージェントの呼び出し。
func (tr trees) hookAt(t *testing.T, body payload, agent string) string {
	t.Helper()
	if agent != "" {
		body["agent_id"] = agent
	}
	_, reason := verdict(t, hook(t, tr.root, []string{"--mode", "enable"}, body, wrapEnv(t, nil)))
	return reason
}

func (tr trees) bashAt(t *testing.T, cwd, command, agent string) string {
	t.Helper()
	return tr.hookAt(t, bash(command, cwd), agent)
}

func TestChildPushIsStoppedFromAChildTree(t *testing.T) {
	t.Parallel()
	for _, c := range childCases {
		t.Run(c.where+"-"+c.sub+"-"+c.placed, func(t *testing.T) {
			t.Parallel()
			tr := newTrees(t, payload{"version": 1})
			tr.placeTicket(t, c.placed)
			reason := tr.bashAt(t, tr.cwd(c.where), gitWrapper(c.sub), "")
			if c.stops {
				if !strings.Contains(reason, codeChildPush) || !strings.Contains(reason, "親（") {
					t.Errorf("止めて親を案内するはず: %s", reason)
				}
			} else if strings.Contains(reason, codeChildPush) {
				t.Errorf("止めないはず: %s", reason)
			}
		})
	}
}

func TestChildPushFollowsTheDirectoryTheCommandRunsIn(t *testing.T) {
	t.Parallel()
	tr := newTrees(t, payload{"version": 1})
	tr.placeTicket(t, "parent-doing")
	push := gitWrapper("push")
	cases := []struct {
		name, cwd, command string
		stops              bool
	}{
		{"親のエージェントが子へ cd して打つ", tr.root, "cd .claude/worktrees/i0001-01-01 && " + push, true},
		{"子から親のツリーへ移ってから打つ形は通す", tr.child, "cd ../i0001 && " + push, false},
		{"関数の中", tr.child, "f() { " + push + "; }; f", true},
		{"読めない cd は止める", tr.root, `cd "$X" && ` + push, true},
	}
	for _, c := range cases {
		reason := tr.bashAt(t, c.cwd, c.command, "")
		if got := strings.Contains(reason, codeChildPush); got != c.stops {
			t.Errorf("%s: 止める=%v のはず: %s", c.name, c.stops, reason)
		}
	}
	// 文面の先頭に、記録の rules と同じ識別子を出す。
	reason := tr.bashAt(t, tr.root, `cd "$X" && `+push, "")
	if want := "[ccnavi] " + codeChildPush + " (組み込みのルール: " + childPushRule + ")"; !strings.Contains(reason, want) {
		t.Errorf("先頭に %q が要る: %s", want, reason)
	}
}

// 論理のパスと実際のパスを混ぜると外れる。cwd が symlink 越しでも止める。
func TestChildPushIsStoppedThroughASymlinkedWorkspace(t *testing.T) {
	t.Parallel()
	if !base.CanSymlink() {
		t.Skip("symlink を作れない")
	}
	tr := newTrees(t, payload{"version": 1})
	tr.placeTicket(t, "parent-doing")
	link := tr.root + "-link"
	if err := os.Symlink(tr.root, link); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.Remove(link) })
	reason := tr.bashAt(t, filepath.Join(link, ".claude", "worktrees", "i0001-01-01"), gitWrapper("push"), "")
	if !strings.Contains(reason, codeChildPush) {
		t.Errorf("止めるはず: %s", reason)
	}
}

// ---- サブエージェントは親のワークツリーを書き換えない ----

// 居場所はエージェント ID と実行先のツリーで決める。子のワークツリーでのコミットと、子のワークツリーで親のブランチを取り込む
// merge は通す。居場所を追えない形は止める。メインのエージェントには掛けない。
func newSubagentTrees(t *testing.T) trees {
	t.Helper()
	tr := newTrees(t, payload{"version": 1})
	write(t, filepath.Join(tr.parent, ".ccnavi/approved/doing/i0001.md"), ticketText("i0001", ""))
	write(t, filepath.Join(tr.parent, ".ccnavi/approved/doing/i0001-01-01.md"), ticketText("i0001-01-01", "i0001"))
	return tr
}

func denied(reason string) bool { return strings.Contains(reason, codeParentTree) }

func (tr trees) writeAt(t *testing.T, cwd, path, agent string) string {
	t.Helper()
	body := preToolUse("Write", map[string]any{"file_path": path, "content": "x"}, cwd)
	return tr.hookAt(t, body, agent)
}

func TestASubagentAndTheParentTree(t *testing.T) {
	t.Parallel()
	tr := newSubagentTrees(t)
	commit := gitWrapper("commit -m x")
	for _, sub := range []string{"merge i0001-01-01", "commit -m x", "cherry-pick abc", "revert abc", "add a"} {
		reason := tr.bashAt(t, tr.parent, gitWrapper(sub), "a1")
		if !denied(reason) || !strings.Contains(reason, parentTreeRule) || !strings.Contains(reason, "i0001 は親のワークツリー") {
			t.Errorf("親のツリーで %q を止めるはず: %s", sub, reason)
		}
	}
	for _, sub := range []string{"commit -m x", "add a", "merge i0001"} {
		if reason := tr.bashAt(t, tr.child, gitWrapper(sub), "a1"); denied(reason) {
			t.Errorf("自分の子のツリーの %q は通すはず: %s", sub, reason)
		}
	}
	for _, sub := range []string{"status", "log --oneline", "diff"} {
		if reason := tr.bashAt(t, tr.parent, gitWrapper(sub), "a1"); denied(reason) {
			t.Errorf("親のツリーを読む %q は通すはず: %s", sub, reason)
		}
	}
	// cd で親のツリーへ移る形を追う。親から子へ移ってから打つ形は通す。
	if reason := tr.bashAt(t, tr.child, "cd ../i0001 && "+commit, "a1"); !denied(reason) {
		t.Errorf("子から親へ cd して打つ形を止めるはず: %s", reason)
	}
	if reason := tr.bashAt(t, tr.parent, "cd ../i0001-01-01 && "+commit, "a1"); denied(reason) {
		t.Errorf("親から子へ移ってから打つ形は通すはず: %s", reason)
	}
	// メインのエージェントには掛けない。
	if reason := tr.bashAt(t, tr.parent, commit, ""); denied(reason) {
		t.Errorf("メインのエージェントは止めない: %s", reason)
	}
	// push は自分の理由で止まる。
	if reason := tr.bashAt(t, tr.child, gitWrapper("push"), "a1"); !strings.Contains(reason, codeChildPush) {
		t.Errorf("子からの push は %s で止まるはず: %s", codeChildPush, reason)
	}
}

// 役は、そのワークツリー自身のツリーにある同名のチケットだけで決める。ほかのツリーに parent: を持つ同名のファイルを
// 置いても、親のワークツリーの禁止は外れない。
func TestAPlantedChildTicketDoesNotTurnTheParentIntoAChild(t *testing.T) {
	t.Parallel()
	tr := newSubagentTrees(t)
	commit := gitWrapper("commit -m x")
	write(t, filepath.Join(tr.child, "wip/proposals/review/i0001.md"), "parent: x\n")
	write(t, filepath.Join(tr.plain, ".ccnavi/approved/doing/i0001.md"), "parent: x\n")
	if reason := tr.bashAt(t, tr.child, "cd ../i0001 && "+commit, "a1"); !denied(reason) {
		t.Errorf("置いた偽のチケットで外れてはいけない: %s", reason)
	}
	if reason := tr.writeAt(t, tr.child, filepath.Join(tr.parent, "src", "x.py"), "a1"); !denied(reason) {
		t.Errorf("Write も止めるはず: %s", reason)
	}
	// 自身のツリーで、parent: の無い同名のチケットと並べても親のまま（曖昧なら止める側）。
	write(t, filepath.Join(tr.parent, "wip/proposals/review/i0001.md"), "parent: x\n")
	if reason := tr.bashAt(t, tr.child, "cd ../i0001 && "+commit, "a1"); !denied(reason) {
		t.Errorf("曖昧なら止めるはず: %s", reason)
	}
	// 子のワークツリーで子のチケットを持つ形は、子のまま（コミットは通る）。
	write(t, filepath.Join(tr.child, ".ccnavi/approved/doing/i0001-01-01.md"), ticketText("i0001-01-01", "i0001"))
	if reason := tr.bashAt(t, tr.child, commit, "a1"); denied(reason) {
		t.Errorf("子のままならコミットは通すはず: %s", reason)
	}
}

// env -C と sudo -D は後ろのコマンドを別のディレクトリで走らせる。剥がした実行役の行き先を居場所に入れないと、
// 子の cwd のまま親へコミットできる。
func TestTheDirectoryOfARunnerIsFollowed(t *testing.T) {
	t.Parallel()
	tr := newSubagentTrees(t)
	commit, push := gitWrapper("commit -m x"), gitWrapper("push")
	for _, head := range []string{
		"env -C " + tr.parent, "env --chdir=" + tr.parent, "env --chdir " + tr.parent,
		"sudo -D " + tr.parent, "sudo --chdir=" + tr.parent, "env -C ../i0001",
	} {
		if reason := tr.bashAt(t, tr.child, head+" "+commit, "a1"); !denied(reason) {
			t.Errorf("%q: 止めるはず: %s", head, reason)
		}
	}
	// 読めない行き先は止める。子のワークツリーへ移る形は通す。
	if reason := tr.bashAt(t, tr.child, `env -C "$D" `+commit, "a1"); !denied(reason) {
		t.Errorf("読めない行き先は止めるはず: %s", reason)
	}
	if reason := tr.bashAt(t, tr.parent, "env -C "+tr.child+" "+commit, "a1"); denied(reason) {
		t.Errorf("子へ移る形は通すはず: %s", reason)
	}
	// 子のワークツリーからの push も、実行役の行き先で見る。読めない行き先は止める。
	if reason := tr.bashAt(t, tr.parent, "env -C "+tr.child+" "+push, "a1"); !strings.Contains(reason, codeChildPush) {
		t.Errorf("子への env -C 越しの push を止めるはず: %s", reason)
	}
	if reason := tr.bashAt(t, tr.parent, `env -C "$D" `+push, "a1"); !strings.Contains(reason, codeChildPush) {
		t.Errorf("読めない行き先の push を止めるはず: %s", reason)
	}
}

func TestAnUntraceablePlaceStops(t *testing.T) {
	t.Parallel()
	tr := newSubagentTrees(t)
	reason := tr.bashAt(t, tr.child, `cd "$X" && `+gitWrapper("commit -m x"), "a1")
	if !denied(reason) || !strings.Contains(reason, "読めない") {
		t.Errorf("居場所を追えない形は止めて、読めないと言うはず: %s", reason)
	}
	if reason := tr.bashAt(t, tr.child, "echo a | xargs sh .ccnavi/scripts/ccnavi-git.sh commit", "a1"); !denied(reason) {
		t.Errorf("xargs 越しも止めるはず: %s", reason)
	}
}

// Bash からの書き込みも、行き先が親のワークツリーなら止める。書き込みの形ごとの止める・通すは hook/shellguard の
// TestParentTreeShellTable が見る。ここでは hook を通した代表だけ。
func TestASubagentCannotWriteIntoTheParentTree(t *testing.T) {
	t.Parallel()
	tr := newSubagentTrees(t)
	x := filepath.Join(tr.parent, "src", "x.py")
	reason := tr.bashAt(t, tr.child, "echo hi > "+x, "a1")
	if !denied(reason) || strings.Contains(reason, "ccnavi-git.sh write") {
		t.Errorf("シェルの書き込みを止めるはず: %s", reason)
	}
	// 居場所の読めない cd の後の相対の書き込みは、どのツリーか決まらないので止める。
	reason = tr.bashAt(t, tr.child, `cd "$X" && echo hi > out.txt`, "a1")
	if !denied(reason) || !strings.Contains(reason, "読めない") {
		t.Errorf("読めない cd の後の書き込みは止めるはず: %s", reason)
	}
	// 子のワークツリーへの書き込みと、メインのエージェントは通す。
	if reason := tr.bashAt(t, tr.child, "cp "+x+" src/x.py", "a1"); denied(reason) {
		t.Errorf("子への書き込みは通すはず: %s", reason)
	}
	if reason := tr.bashAt(t, tr.child, "echo hi > "+x, ""); denied(reason) {
		t.Errorf("メインのエージェントは止めない: %s", reason)
	}
	// Write ツール。文面はファイルの書き込みとして言う（ラッパーのサブコマンドと取り違えない）。
	reason = tr.writeAt(t, tr.child, x, "a1")
	if !denied(reason) || strings.Contains(reason, "ccnavi-git.sh write") || !strings.Contains(reason, "書き込もうとしたため、止めました（Write）") {
		t.Errorf("Write をファイルの書き込みとして止めるはず: %s", reason)
	}
	if reason := tr.writeAt(t, tr.child, filepath.Join(tr.child, "src", "x.py"), "a1"); denied(reason) {
		t.Errorf("自分の子のツリーへの Write は通すはず: %s", reason)
	}
	// 提案・下書きの置き場は、置き場ごとの保護に任せる（フローの下書きを書く経路）。
	for _, rel := range []string{"wip/proposals/flows/x.yml", "wip/handoff/note.md"} {
		if reason := tr.writeAt(t, tr.child, filepath.Join(tr.parent, filepath.FromSlash(rel)), "a1"); denied(reason) {
			t.Errorf("%s は置き場ごとの保護に任せる: %s", rel, reason)
		}
	}
	if reason := tr.writeAt(t, tr.child, x, ""); denied(reason) {
		t.Errorf("メインのエージェントの Write は止めない: %s", reason)
	}
}

// 大小を区別しないファイルシステムでは同じ sh が走る。表記を変えても外れない。
func TestTheNameOfTheWrapperIsReadWithoutCase(t *testing.T) {
	t.Parallel()
	tr := newSubagentTrees(t)
	scripts := filepath.Join(tr.root, ".ccnavi", "scripts")
	if reason := tr.bashAt(t, tr.parent, "sh "+filepath.Join(scripts, "CCNAVI-GIT.SH")+" commit -m x", "a1"); !denied(reason) {
		t.Errorf("大文字のラッパー名でも止めるはず: %s", reason)
	}
	if reason := tr.bashAt(t, tr.child, "sh "+filepath.Join(scripts, "ccnavi-git.SH")+" push", "a1"); !strings.Contains(reason, codeChildPush) {
		t.Errorf("混ぜた表記でも push を止めるはず: %s", reason)
	}
	if reason := tr.bashAt(t, tr.child, "sh "+filepath.Join(scripts, "CCNAVI-TICKET.SH")+" finish i0001-01-01", "a1"); !strings.Contains(reason, "DENY_SUBAGENT_TICKET_OP") {
		t.Errorf("ticket の sh の大文字表記でも止めるはず: %s", reason)
	}
}

// ---- ラッパーの sh にも同じ見本を渡す（2 つ目として同じく止める） ----

func needGit(t *testing.T) {
	t.Helper()
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("git が無い")
	}
}

// copyWrapper はラッパーと共通部を、できあがりの姿（下書きがあれば下書き）から root の .ccnavi/scripts/ へコピーする。
// 入口と部品の版が混ざらないように、共通部は 1 か所からまとめてコピーする。
func copyWrapper(t *testing.T, root string) string {
	t.Helper()
	scripts := base.ScriptsDir()
	dest := mkdirs(t, filepath.Join(root, ".ccnavi", "scripts"))
	for _, name := range append([]string{"ccnavi-git.sh"}, base.CommonSh(t, scripts)...) {
		write(t, filepath.Join(dest, name), read(t, filepath.Join(scripts, name)))
	}
	return filepath.Join(dest, "ccnavi-git.sh")
}

// runWrapper はラッパーの sh を cwd から 1 回起こす。PWD は起動時の論理の居場所（ターミナルから symlink 越しに cd した形）。
func runWrapper(t *testing.T, script, cwd string, args ...string) run.Result {
	t.Helper()
	return run.Sh(t, script, args, run.Opts{Dir: cwd, Env: wrapEnv(t, map[string]string{"PWD": cwd})})
}

func TestShAgreesOnChildPush(t *testing.T) {
	t.Parallel()
	needGit(t)
	root := base.TempDir(t)
	script := copyWrapper(t, root)
	initRepo(t, root)
	run.Git(t, root, "checkout", "-q", "-b", "main")
	write(t, filepath.Join(root, ".gitignore"), ".claude/\nlogs/\n")
	run.Git(t, root, "add", "-A")
	run.Git(t, root, "commit", "--quiet", "-m", "init")
	tr := trees{root: root}
	for _, name := range []string{"i0001", "i0001-01-01", "plain"} {
		path := filepath.Join(root, ".claude", "worktrees", name)
		run.Git(t, root, "worktree", "add", "--quiet", path, "-b", name, "main")
		switch name {
		case "i0001":
			tr.parent = path
		case "i0001-01-01":
			tr.child = path
		default:
			tr.plain = path
		}
	}
	for _, c := range childCases {
		if c.sub != "push" {
			continue
		}
		path := tr.placeTicket(t, c.placed)
		result := runWrapper(t, script, tr.cwd(c.where), "push", "--dry-run")
		if said := strings.Contains(result.Stderr, "子チケットのワークツリー"); said != c.stops {
			t.Errorf("%s/%s: sh は止める=%v のはず: %s", c.where, c.placed, c.stops, result.Stderr)
		}
		if err := os.Remove(path); err != nil {
			t.Fatal(err)
		}
	}
	// ワークスペースを symlink 越しに開いたとき（macOS の /tmp → /private/tmp など）。sh は論理のパスでワークスペースを、
	// git の実際のパスでツリーを作るので、混ぜると外れる。両辺をリンクを解いたパスで比べないと、リンクを経た作業場で保護が外れる。
	if base.CanSymlink() {
		link := root + "-link"
		if err := os.Symlink(root, link); err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { _ = os.Remove(link) })
		tr.placeTicket(t, "parent-doing")
		result := runWrapper(t, script, filepath.Join(link, ".claude", "worktrees", "i0001-01-01"), "push", "--dry-run")
		if !strings.Contains(result.Stderr, "子チケットのワークツリー") {
			t.Errorf("symlink 越しでも止めるはず: %s", result.Stderr)
		}
	}
}

// 子のワークツリーで、git の置き場を親に向ける変数を置いて呼んでも、ラッパーは居場所のツリー（子）を操作する。
// 変数が効くと、hook と sh の居場所の検査が子を見ている間に親のワークツリーへコミットできる。
func TestShIgnoresGitLocationVariables(t *testing.T) {
	t.Parallel()
	needGit(t)
	root := base.TempDir(t)
	script := copyWrapper(t, root)
	initRepo(t, root)
	run.Git(t, root, "checkout", "-q", "-b", "main")
	write(t, filepath.Join(root, ".gitignore"), ".claude/\nlogs/\n")
	run.Git(t, root, "add", "-A")
	run.Git(t, root, "commit", "--quiet", "-m", "init")
	parent := filepath.Join(root, ".claude", "worktrees", "i0001")
	child := filepath.Join(root, ".claude", "worktrees", "i0001-01-01")
	run.Git(t, root, "worktree", "add", "--quiet", parent, "-b", "i0001", "main")
	run.Git(t, root, "worktree", "add", "--quiet", child, "-b", "i0001-01-01", "main")
	write(t, filepath.Join(parent, "only-in-parent.txt"), "x\n")
	result := run.Sh(t, script, []string{"status", "--short"}, run.Opts{Dir: child, Env: wrapEnv(t, map[string]string{
		"PWD":           child,
		"GIT_DIR":       filepath.Join(root, ".git", "worktrees", "i0001"),
		"GIT_WORK_TREE": parent,
	})})
	if strings.Contains(result.Stdout+result.Stderr, "only-in-parent.txt") {
		t.Errorf("git の置き場を向ける変数は効かないはず: %s%s", result.Stdout, result.Stderr)
	}
}

// ---- ラッパーで自己防衛の対象を消す・別のコミットの中身に戻す形 ----

// coreCases の各行は（ラッパーに渡す引数, 止まるか）。居場所はワークスペースルート。ワークスペースには
// .ccnavi/scripts/ccnavi-git.sh・.ccnavi/config/common/rules.yml・src/a.py・projects/p/.ccnavi/config/project/rules.yml・
// .claude/settings.json がある（coreFiles）。
var coreCases = []struct {
	args  string
	stops bool
}{
	// 消す。ラッパー自身を消すと、生の git の組み込みの止め（ラッパーがあるときだけ）も外れる。
	{"rm .ccnavi/scripts/ccnavi-git.sh", true},
	{"rm -r .ccnavi/scripts", true},
	{"rm --cached .ccnavi/config/common/rules.yml", true},
	{"rm .claude/settings.json", true},
	{"rm -r projects", true},
	{"rm *.sh", true},
	{"rm :/.ccnavi", true},
	{"rm --pathspec-from-file=list.txt", true},
	{"rm src/a.py", false},
	{"rm src/*.py", false},
	// 何も消さない形と、ワークスペースの記録の置き場ではない logs。
	{"rm -n .claude/settings.json", false},
	{"rm .claude/settings.json --dry-run", false},
	{"rm -r tests/logs", false},
	// 別のコミットの中身に戻す。
	{"restore --source=HEAD~5 -- .ccnavi", true},
	{"restore -s HEAD .ccnavi/config/common/rules.yml", true},
	{"restore -sHEAD .claude/settings.json", true},
	{"restore .ccnavi/config/common/rules.yml --source HEAD", true},
	{"restore --source=HEAD -- ./", true},
	{"restore --source=HEAD --pathspec-from-file=list.txt", true},
	{"restore --source=HEAD -- src/a.py", false},
	{"checkout HEAD .ccnavi/config/common/rules.yml", true},
	{"checkout HEAD src/a.py", false},
	// 索引の中身に戻す形と、衝突の解決は通す。
	{"restore .ccnavi/config/common/rules.yml", false},
	{"restore --staged .ccnavi/config/common/rules.yml", false},
	{"restore --ours -- .ccnavi/config/common/rules.yml", false},
	{"restore --theirs .ccnavi/config/common/rules.yml", false},
	{"checkout main", false},
	{"checkout -b x main", false},
	{"add .ccnavi/config/common/rules.yml", false},
}

var coreFiles = []string{
	".ccnavi/scripts/ccnavi-git.sh",
	".ccnavi/config/common/rules.yml",
	"src/a.py",
	"projects/p/.ccnavi/config/project/rules.yml",
	".claude/settings.json",
	"tests/logs/a.txt",
	".ccnavi/approved/phases/i0001/1.reviewed",
	"wip/proposals/review/i0002.md",
	"wip/handoff/a.md",
}

type coreRepo struct{ root string }

func newCoreRepo(t *testing.T) coreRepo {
	t.Helper()
	root := base.TempDir(t)
	for _, name := range coreFiles {
		write(t, filepath.Join(root, filepath.FromSlash(name)), "x\n")
	}
	write(t, base.CommonPath(root, "rules"), ruleBlob(t, allowWrapper))
	return coreRepo{root}
}

func (c coreRepo) hook(t *testing.T, command string, extra map[string]string) (string, string) {
	t.Helper()
	body := bash(command, c.root)
	body["permission_mode"] = "default"
	args := []string{"--log", "", "--state", filepath.Join(c.root, "state"), "--mode", "enable"}
	r := hook(t, c.root, args, body, wrapEnv(t, extra))
	if r.Code != 0 {
		t.Fatalf("code = %d: %s", r.Code, r.Stderr)
	}
	return verdict(t, r)
}

func (c coreRepo) wrapper(t *testing.T, args string, extra map[string]string) (string, string) {
	t.Helper()
	return c.hook(t, "sh .ccnavi/scripts/ccnavi-git.sh "+args, extra)
}

// 止める・通すの表（coreCases・居場所の移動・読み切れない形・レビュー待ちの置き場）は hook/shellguard の
// TestCorePathsCommandTable が見る。ここでは、allow のルールより先に止めることと、止めない形がルールまで届くことを見る。
func TestCorePathStopsBeforeAnAllowRule(t *testing.T) {
	t.Parallel()
	c := newCoreRepo(t)
	if decision, reason := c.wrapper(t, "rm .claude/settings.json", nil); decision != "deny" || !strings.Contains(reason, codeCorePath) {
		t.Errorf("止めるはず: %s %s", decision, reason)
	}
	if decision, reason := c.wrapper(t, "rm src/a.py", nil); decision != "" {
		t.Errorf("止めない形はルールまで届くはず: %s %s", decision, reason)
	}
	// --no-dry-run の後に --dry-run を書けば、何も消さない形として通す。
	if decision, reason := c.wrapper(t, "rm --no-dry-run --dry-run .claude/settings.json", nil); decision != "" {
		t.Errorf("最後の --dry-run が勝つはず: %s %s", decision, reason)
	}
	// 自己防衛の設定で切れる。
	if _, reason := c.wrapper(t, "rm .ccnavi/scripts/ccnavi-git.sh", map[string]string{env.GuardCoreFiles: "disable"}); strings.Contains(reason, codeCorePath) {
		t.Errorf("自己防衛を切れば止めない: %s", reason)
	}
	// 索引の中身に戻すだけなら、パスをファイルから読む形もここでは止めない。
	if _, reason := c.wrapper(t, "restore --pathspec-from-file=list.txt", nil); strings.Contains(reason, codeCorePath) {
		t.Errorf("索引へ戻すだけの restore は止めない: %s", reason)
	}
}

// 拒否の文面: 識別子の見出し、代わりの手段（ユーザに依頼）と、通る衝突の解決を実パスで名指しする。
func TestCorePathRefusalNamesTheWayOut(t *testing.T) {
	t.Parallel()
	c := newCoreRepo(t)
	_, reason := c.wrapper(t, "rm .ccnavi/scripts/ccnavi-git.sh", nil)
	real, err := filepath.EvalSymlinks(c.root)
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{
		"[ccnavi] " + codeCorePath + " (組み込みのルール: " + corePathRule + ")",
		"ユーザに依頼",
		filepath.ToSlash(real) + "/.ccnavi/scripts/ccnavi-git.sh",
		"restore --ours",
	} {
		if !strings.Contains(reason, want) {
			t.Errorf("拒否の文に %q が無い: %s", want, reason)
		}
	}
}

// sh が別の理由で断る形と、sh が通してユーザに任せる形も、hook はエージェントから止める。形ごとの止めは Go の表が見る。
// ここでは文面の違いを見る。
func TestCorePathHookOnlyForms(t *testing.T) {
	t.Parallel()
	c := newCoreRepo(t)
	for _, f := range []struct{ args, text string }{
		// 索引の中身に戻す checkout（sh は restore を案内して断る）。文面も restore を案内する。
		{"checkout -- .ccnavi/config/common/rules.yml", "restore <パス>"},
		// レビュー済みのマーカーを消すのはユーザがターミナルで打つ手順（sh は通す）。
		{"rm .ccnavi/approved/phases/i0001/1.reviewed", "ユーザに依頼"},
	} {
		decision, reason := c.wrapper(t, f.args, nil)
		if decision != "deny" || !strings.Contains(reason, codeCorePath) || !strings.Contains(reason, f.text) {
			t.Errorf("%q: %s と %q を言って止めるはず: %s %s", f.args, codeCorePath, f.text, decision, reason)
		}
	}
	// 文字として渡す道の案内は、ラッパーのあるワークスペースでは生の git を勧めない。
	decision, reason := c.hook(t, "echo `date`", nil)
	if decision != "deny" || !strings.Contains(reason, "ccnavi-git.sh commit -F <ファイル>") || strings.Contains(reason, " git commit -F") {
		t.Errorf("ラッパーの commit -F を案内するはず: %s %s", decision, reason)
	}
	// 対象を読み切れない形は、共通ルールの allow に落とさずに止める。
	decision, reason = c.hook(t, "echo 'sh .ccnavi/scripts/ccnavi-git.sh rm .ccnavi/approved/t/r' | sh", nil)
	if decision != "deny" || !strings.Contains(reason, codeCorePath) {
		t.Errorf("読み切れない対象は止めるはず: %s %s", decision, reason)
	}
}

// レビュー待ちの置き場（wip/proposals/review/）は、承認済みチケットの置き場と同じくエージェントのラッパーの呼び出しを止める。
// 親を閉じた後の案内（rm -r wip）は、レビュー待ちが空なら通す。通すのは rm だけで、過去のコミットに戻すと提案が戻りうる。
func TestCorePathReviewPlace(t *testing.T) {
	t.Parallel()
	c := newCoreRepo(t)
	decision, reason := c.wrapper(t, "rm -r wip", nil)
	if decision != "deny" || !strings.Contains(reason, codeCorePath) || !strings.Contains(reason, "レビュー待ち") || !strings.Contains(reason, "ユーザに依頼") {
		t.Errorf("レビュー待ちがあれば止めるはず: %s %s", decision, reason)
	}
	if err := os.Remove(filepath.Join(c.root, "wip", "proposals", "review", "i0002.md")); err != nil {
		t.Fatal(err)
	}
	for _, args := range []string{"rm -r wip", "rm -r wip/proposals"} {
		if decision, reason := c.wrapper(t, args, nil); decision != "" {
			t.Errorf("%q: 空なら通すはず: %s %s", args, decision, reason)
		}
	}
	for _, args := range []string{
		"restore --source HEAD~1 wip",
		"restore -s HEAD~1 wip/proposals",
		"checkout HEAD~1 -- wip",
		"checkout HEAD~1 wip/proposals",
	} {
		if decision, reason := c.wrapper(t, args, nil); decision != "deny" || !strings.Contains(reason, "レビュー待ち") {
			t.Errorf("%q: 過去の中身に戻す形は止めるはず: %s %s", args, decision, reason)
		}
	}
	if decision, reason := c.wrapper(t, "restore --staged wip", nil); decision != "" {
		t.Errorf("索引へ戻すだけなら通すはず: %s %s", decision, reason)
	}
	if err := os.RemoveAll(filepath.Join(c.root, "wip", "proposals")); err != nil {
		t.Fatal(err)
	}
	if decision, reason := c.wrapper(t, "rm -r wip", nil); decision != "" {
		t.Errorf("提案の置き場が無ければ通すはず: %s %s", decision, reason)
	}
}

// 同じ見本を sh にも渡し、2 つ目として同じく止めることを確かめる。
type coreSh struct {
	t      *testing.T
	root   string
	script string
}

func newCoreSh(t *testing.T) coreSh {
	t.Helper()
	needGit(t)
	root := base.TempDir(t)
	script := copyWrapper(t, root)
	for _, name := range coreFiles {
		if !exists(filepath.Join(root, filepath.FromSlash(name))) {
			write(t, filepath.Join(root, filepath.FromSlash(name)), "x\n")
		}
	}
	write(t, filepath.Join(root, ".gitignore"), "/logs/\n")
	initRepo(t, root)
	run.Git(t, root, "checkout", "-q", "-b", "main")
	run.Git(t, root, "add", "-A")
	run.Git(t, root, "commit", "-qm", "i")
	return coreSh{t, root, script}
}

// run はラッパーを打つ。消した形は試すたびに戻す（--dry-run は自己防衛の検査を通るので、本当に打つ）。
// sh が断った形（終了コード 2）は git を打つ前に止まるので、戻すものが無い。
func (c coreSh) run(args string) run.Result {
	c.t.Helper()
	r := runWrapper(c.t, c.script, c.root, strings.Fields(args)...)
	if r.Code != 2 {
		run.Git(c.t, c.root, "reset", "-q", "--hard")
	}
	return r
}

func TestShAgreesOnCorePaths(t *testing.T) {
	t.Parallel()
	c := newCoreSh(t)
	for _, k := range coreCases {
		r := c.run(k.args)
		if !k.stops {
			if r.Code != 0 {
				t.Errorf("%q: sh は通すはず: %d %s", k.args, r.Code, r.Stderr)
			}
			continue
		}
		// ツリー全体（./）と承認済みチケットの置き場を含む指定は、sh の前からある拒否（restore-whole-tree・
		// restore-store-source）が先にヒットする。
		said := false
		for _, s := range []string{"ccnavi 自身を守る場所", "ツリー全体", "承認済みチケットの置き場"} {
			said = said || strings.Contains(r.Stderr, s)
		}
		if r.Code != 2 || !said {
			t.Errorf("%q: sh は断るはず: %d %s", k.args, r.Code, r.Stderr)
		}
	}
}

// ユーザの手順は sh が通す: レビュー済みのマーカーを消す。レビュー待ちの置き場の rm は止めない（ユーザがターミナルで打つ手順を
// 塞がない）。過去の中身に戻す形は置き場の検査（ccnavi git check の StoreHit）が止める。
// 記録の置き場はワークスペースルートの logs だけで、ほかの階層の logs は守る場所ではない。
func TestShLeavesTheUsersStepsAndGuardsTheWorkspaceRecords(t *testing.T) {
	t.Parallel()
	c := newCoreSh(t)
	for _, args := range []string{
		"rm .ccnavi/approved/phases/i0001/1.reviewed",
		"rm wip/proposals/review/i0002.md",
		"rm -r wip",
		"rm -r tests/logs",
	} {
		if r := c.run(args); r.Code != 0 {
			t.Errorf("%q: sh は通すはず: %d %s", args, r.Code, r.Stderr)
		}
	}
	for _, args := range []string{
		"restore --source=HEAD wip/proposals/review/i0002.md",
		"checkout HEAD wip/proposals/review/i0002.md",
	} {
		if r := c.run(args); r.Code != 2 {
			t.Errorf("%q: sh は過去の中身に戻す形を断るはず: %d %s", args, r.Code, r.Stderr)
		}
	}
	r := c.run("rm -r logs/state")
	if r.Code != 2 || !strings.Contains(r.Stderr, "ccnavi 自身を守る場所") {
		t.Errorf("ワークスペースの logs は守るはず: %d %s", r.Code, r.Stderr)
	}
}
