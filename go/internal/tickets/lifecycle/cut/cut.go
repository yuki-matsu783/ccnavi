package cut

import (
	"crypto/sha1"
	"encoding/hex"
	"regexp"
	"strconv"
	"strings"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/hookio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/reply"
	"ccnavi/internal/infra/shell"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/tickets/lifecycle/branch"
	"ccnavi/internal/tickets/lifecycle/integrate"
	"ccnavi/internal/tickets/model/ids"
)

// 終了コード（ccnavi-worktree.sh の契約）。
const (
	exitUnmet   = 1 // 前提の未充足か worktree add の拒否
	exitUsage   = 2 // 引数か環境の誤り
	exitConfirm = 3 // 確認が要る（何も切らず、ユーザの了承を待つ）
)

// slow は、ユーザのチェックアウトの全体を読む・書く git の期限（status・add・rev-list など）。
const slow = 10 * time.Minute

// Leftover は、親のワークツリーに残っているもの（`tree leftover` と同じ答え）。Stage は doing・review・todo か空。
type Leftover struct {
	Leftover, Proposal bool
	Doing              int
	MR, Stage          string
}

// Remote は sh が取ってきた origin の様子。FetchOK は `fetch origin <取り込み先>` が通ったか、FetchOut はその出力。
// Heads は `ls-remote --heads origin` の応答のまま（fetch が通ったときだけ取る）、HeadsOK はそれが通ったか、HeadsErr は
// 通らなかったときの標準エラー。
type Remote struct {
	FetchOK         bool
	FetchOut        string
	HeadsOK         bool
	Heads, HeadsErr string
}

// Input は Plan・Cut の材料。Args は ccnavi-worktree.sh が受けた引数のまま。Leftover は、ツリー（名前）の親のワークツリーに
// 残っているものを答える（置き場の読み方は sh への答えの側が持つ）。
type Input struct {
	Root, Cwd string
	Conf      *config.Settings
	Args      []string
	Leftover  func(path, name string) Leftover
}

// Plan は、切る前に決まることを全部確かめる。引き取りと --dry-run はここで終わる。切る道に進むなら `fetch <取り込み先>` と
// `top <ツリー>` を返す。
func Plan(in Input) string {
	r := &run{in: in, o: &reply.Lines{}}
	if r.prepare(false) {
		r.o.Val("fetch", r.target)
		r.o.Val("top", r.top)
	}
	return r.o.String()
}

// Cut は、sh が取ってきた origin の様子で、ずれと書きかけを確かめて切る（確認が要れば報告して止める）。
func Cut(in Input, remote Remote) string {
	r := &run{in: in, o: &reply.Lines{}}
	if r.prepare(true) {
		r.cut(remote)
	}
	return r.o.String()
}

// run は 1 回の起動の状態。
type run struct {
	in Input
	o  *reply.Lines

	name, baseArg, accept, wipMode string
	side, dry, adopt               bool

	scripts, dest, top, integ string
	mainTree, target, headSha string
	topBranch, commonDir      string
	c4, c5, c7                bool
	changed, added, staged    int
	others                    string
}

// fail は止める。
func (r *run) fail(id, message string, code int) bool {
	r.o.Fail(id, code, message)
	return false
}

// git は cwd で git を起こし、末尾の改行を落とした標準出力を返す。失敗なら空。
func gitOut(dir string, timeout time.Duration, args ...string) string {
	done := git.Run(dir, args, git.Options{Timeout: timeout})
	if !done.OK() {
		return ""
	}
	return strings.TrimRight(strings.ReplaceAll(done.Out, "\r", ""), "\n")
}

// ok は git が 0 で終わったか。
func ok(dir string, args ...string) bool {
	return git.Run(dir, args, git.Options{}).OK()
}

// phys はリンクを解いたパス（`/` 区切り）。sh の ccnavi_phys と同じ見方。
func phys(p string) string {
	if p == "" {
		return ""
	}
	return ospath.Slashed(ospath.Realpath(p))
}

// samePath はパスを大文字小文字を区別せずに比べる。
func samePath(a, b string) bool { return pathcase.Fold(a) == pathcase.Fold(b) }

// q は sh の 1 語に引用する。
func q(s string) string { return shell.SingleQuote(s) }

// say は文面ファイルの文。
func (r *run) say(id string, kv ...string) string {
	values := texts.Values{}
	for i := 0; i+1 < len(kv); i += 2 {
		values[kv[i]] = kv[i+1]
	}
	return texts.Say(r.in.Root, id, values, nil)
}

// unattended は、非対話（CLAUDE_CODE_SESSION_ATTENDED が 0）のときだけ足す、確認に答えられないことと次の行動。
func unattended() string {
	if !hookio.Unattended() {
		return ""
	}
	return "このセッションでは確認に答えられません。ユーザに引き取りの了承を頼み、了承されたら打ち直してもらってください。"
}

// next は最後の行（名前の警告よりも後）に出す次の一手。
func (r *run) next() { r.o.Out(r.say("NEXT_WORKTREE_EDIT", "dest", r.dest)) }

// args は引数を読む。
func (r *run) args() bool {
	argv := r.in.Args
	for i := 0; i < len(argv); i++ {
		a := argv[i]
		value := func(id, missing string) (string, bool) {
			if i+1 >= len(argv) {
				return "", r.fail(id, missing, exitUsage)
			}
			i++
			return argv[i], true
		}
		var good bool
		switch {
		case a == "--offline" || a == "--from-origin":
			return r.fail("retired-arg", a+" は廃止しました。fetch が届かない・手元が origin とずれているときは、引数なしで打つと状態と確認コードを報告するので、ユーザの了承を得てから --accept <確認コード> を付けて打ち直してください。", exitUsage)
		case a == "--adopt":
			r.adopt = true
		case a == "--base":
			if r.baseArg, good = value("base-missing", "--base にブランチ名がありません。"); !good {
				return false
			}
		case strings.HasPrefix(a, "--base="):
			r.baseArg = strings.TrimPrefix(a, "--base=")
		case a == "--accept":
			if r.accept, good = value("accept-missing", "--accept に確認コードがありません。"); !good {
				return false
			}
		case strings.HasPrefix(a, "--accept="):
			r.accept = strings.TrimPrefix(a, "--accept=")
		case a == "--copy-wip" || a == "--no-wip":
			mode := map[string]string{"--copy-wip": "copy", "--no-wip": "no"}[a]
			if r.wipMode != "" && r.wipMode != mode {
				return r.fail("wip-both", "--copy-wip と --no-wip は同時に指定できません。", exitUsage)
			}
			r.wipMode = mode
		case a == "--side-by-side":
			r.side = true
		case a == "--dry-run":
			r.dry = true
		case a == "-h" || a == "--help" || a == "help":
			r.o.Val("help", "yes")
			r.o.Exit(0)
			return false
		case strings.HasPrefix(a, "-"):
			r.o.Val("usage", "yes")
			return r.fail("unknown-arg", "未対応の引数です（"+a+"）。", exitUsage)
		default:
			if r.name != "" {
				return r.fail("two-names", "ワークツリーは 1 回に 1 本だけ指定します（"+r.name+" と "+a+"）。", exitUsage)
			}
			r.name = a
		}
	}
	if r.name == "" {
		r.o.Val("usage", "yes")
		return r.fail("no-name", "ワークツリーの名前を渡してください。", exitUsage)
	}
	if strings.Contains(r.name, "/") {
		return r.fail("bad-name", "名前（"+r.name+"）に / は書けません。ワークツリーは .claude/worktrees/ の下に 1 階層で置きます。", exitUsage)
	}
	// 名前の形・字の種類・NFC・長さは ids の 1 か所で決める（直接作業や子のワークツリーとして使えない理由）。
	for _, line := range ids.IDAnswer(r.name) {
		if why, found := strings.CutPrefix(line, "direct "); found {
			return r.fail("bad-name", "名前（"+r.name+"）は指定できません（"+why+"）。", exitUsage)
		}
	}
	return true
}

// prepare は、切る前に決まることを確かめる（Plan と Cut で同じ）。again なら Cut の 2 回目で、名前の警告を出さない。
// 引き取りと --dry-run はここで終える（偽を返す）。
func (r *run) prepare(again bool) bool {
	if !r.args() {
		return false
	}
	root := r.in.Root
	r.top = gitOut(r.in.Cwd, 0, "rev-parse", "--show-toplevel")
	if r.top == "" {
		return r.fail("not-git", "cwd が git のリポジトリの中ではありません。ワークスペースか projects/<名前>/ の中で実行してください。", exitUnmet)
	}
	r.scripts = ospath.Join(root, ".ccnavi", "scripts")
	r.dest = ospath.Join(root, ".claude", "worktrees", r.name)
	if r.integ = integrate.IntegrationName(phys(r.top)); r.integ == "" {
		return r.fail("no-integration", integrate.IntegrationHint(r.top), exitUnmet)
	}
	// 同じ識別子になる別のブランチは、既にあるワークツリー・ブランチの検査より前に見る。大文字小文字を区別しない機械では、
	// 綴り違いの名前のパスが既にあるように見えるため。
	if !r.adopt && !r.sameID(r.localHeads(), "手元") {
		return false
	}
	if !r.adopt && ospath.DiskLexists(r.dest) {
		return r.existing()
	}
	if !r.adopt && ok(r.in.Cwd, "show-ref", "--verify", "--quiet", "refs/heads/"+r.name) {
		return r.fail("branch-exists", "ブランチ "+r.name+" が既にあります（ワークツリーはありません）。既にあるブランチを出すには、2 段階の手順が要ります。1 つ目: sh "+q(ospath.Join(r.scripts, "ccnavi-git.sh"))+" worktree add "+q(".claude/worktrees/"+r.name)+" "+q(r.name)+"。2 つ目: sh "+q(ospath.Join(r.scripts, "ccnavi-worktree.sh"))+" "+q(r.name)+" --adopt --base <統合先> で引き取ります（引き取らないと integrate できません。引き取りにはユーザの確認が要ります）。新しく切るなら別の名前にしてください。"+unattended(), exitUnmet)
	}
	if !r.mainCheckout() || !r.parentTree() || !r.marker() {
		return false
	}
	if r.baseArg != "" && r.baseArg != r.target {
		return r.fail("base-mismatch", "--base "+r.baseArg+" は、今の取り込み先（"+r.target+"。ユーザのチェックアウト "+r.mainTree+"）と違います。取り込み先が切り替わっていれば、ユーザに確かめて、報告からやり直してください（引数なしで打ち直す）。", exitUnmet)
	}
	if r.adopt {
		r.adoptTree()
		return false
	}
	if !r.state() {
		return false
	}
	if r.dry {
		r.dryRun()
		return false
	}
	if !again {
		r.nameNote()
	}
	return true
}

// localHeads は手元のブランチと、手元に残る origin の追跡ブランチの名前。
func (r *run) localHeads() []string {
	var names []string
	names = append(names, lines(gitOut(r.in.Cwd, 0, "for-each-ref", "--format=%(refname:short)", "refs/heads"))...)
	for _, n := range lines(gitOut(r.in.Cwd, 0, "for-each-ref", "--format=%(refname:lstrip=3)", "refs/remotes/origin")) {
		if n != "HEAD" {
			names = append(names, n)
		}
	}
	return names
}

// lines は空行を除いた行。
func lines(text string) []string {
	var out []string
	for _, l := range strings.Split(text, "\n") {
		if l = strings.TrimRight(l, "\r"); l != "" {
			out = append(out, l)
		}
	}
	return out
}

// startHint は、既にあるブランチを ccnavi-start.sh で始める案内の 2 行。
func (r *run) startHint() {
	start := q(ospath.Join(r.scripts, "ccnavi-start.sh"))
	r.o.Out("  sh " + start + " --mr <MR の番号>\n  sh " + start + " --issue <issue の番号>")
}

// otherName は、別の名前で新しく切る案内。
func (r *run) otherName() {
	r.o.Out("別の作業として新しく切るなら、別の名前で再実行する: sh " + q(ospath.Join(r.scripts, "ccnavi-worktree.sh")) + " <別の名前>")
}

// sameID は、names に同じ識別子になる別のブランチ（feature/7-x に対する feature-7-x、英字の大文字小文字だけ違う名前）が
// あれば止める。where は「手元」か「origin」。
func (r *run) sameID(names []string, where string) bool {
	other := ids.IDClash(r.name, names)
	if other == "" {
		return true
	}
	r.o.Out("ワークツリーは切っていない。" + where + " にブランチ " + other + " があり、" + r.name + " と同じ識別子（" + r.name + "。英字の大文字小文字は区別しない）になる。")
	r.o.Out("同じ識別子のブランチが 2 本あると、ワークツリーの置き場が重なる。既にあるブランチは ccnavi-start.sh で始める:")
	r.startHint()
	r.otherName()
	r.o.Val("reason", "same-id")
	r.o.Exit(exitUnmet)
	return false
}

// branchAt は、パス path にあるこのリポジトリのワークツリーが開いているブランチ（refs/heads/…）。無ければ空。
func (r *run) branchAt(path string) string {
	want, at := phys(path), ""
	for _, line := range strings.Split(gitOut(r.in.Cwd, 0, "worktree", "list", "--porcelain"), "\n") {
		if p, found := strings.CutPrefix(line, "worktree "); found {
			at = p
		} else if b, found := strings.CutPrefix(line, "branch "); found && samePath(at, want) {
			return b
		}
	}
	return ""
}

// commonOf はツリーの git の共通ディレクトリ（リンクを解いたもの）。読めなければ空。
func commonOf(dir string) string {
	return phys(gitOut(dir, 0, "rev-parse", "--path-format=absolute", "--git-common-dir"))
}

// existing は、行き先が既にあるとき。同じ名前のブランチを開いたこのリポジトリのワークツリーなら、パスと取り込みの記録の
// 有無を出して成功にする（冪等）。
func (r *run) existing() bool {
	have := r.branchAt(r.dest)
	if have != "refs/heads/"+r.name {
		if have == "" {
			have = "ワークツリーの登録が無い"
		}
		return r.fail("dest-exists", r.dest+" は既にありますが、ブランチ "+r.name+" を開いているこのリポジトリのワークツリーではありません（"+have+"）。中身を確かめ、不要ならユーザに確かめてから片付け、別の名前で再実行してください。", exitUnmet)
	}
	r.o.Out("ワークツリーは既にある: " + r.dest)
	// 取り込みの記録（--adopt が読む場所）があるか。無いと integrate に進めない。
	if common := commonOf(r.top); common != "" && ospath.DiskLexists(ospath.Join(common, tree.DirectDir, r.name)) {
		r.o.Out("案内: 取り込みの記録は、ある（ccnavi-direct/" + r.name + "）。integrate に進めます")
	} else {
		r.o.Out(r.say("NOTE_WORKTREE_RECORD_MISSING", "name", r.name, "name_q", q(r.name), "unattended", unattended()))
	}
	r.next()
	r.o.Exit(0)
	return false
}

// gitVersion は `git version` の版（大・小）。読めなければ偽。
var gitVersion = regexp.MustCompile(`^git version ([0-9]+)\.([0-9]+)`)

// mainCheckout は C1〜C3: ユーザのチェックアウト（git worktree list の先頭）がブランチを開いていること、git が 2.40 以上で
// あること、取り込み先がチケットに結び付かないこと。
func (r *run) mainCheckout() bool {
	first, _, _ := strings.Cut(gitOut(r.top, 0, "worktree", "list", "--porcelain"), "\n")
	main, found := strings.CutPrefix(first, "worktree ")
	if !found || main == "" {
		return r.fail("no-main-tree", "このリポジトリのユーザのチェックアウトを git worktree list から読めません。", exitUnmet)
	}
	r.mainTree = phys(main)
	if r.target = gitOut(r.mainTree, 0, "symbolic-ref", "--quiet", "--short", "HEAD"); r.target == "" {
		return r.fail("detached", "ユーザのチェックアウト（"+r.mainTree+"）がブランチを開いていません（detached HEAD）。取り込み先を決められないので切りません。ユーザにブランチへ戻してもらってから再実行してください。", exitUnmet)
	}
	if r.headSha = gitOut(r.mainTree, 0, "rev-parse", "HEAD"); r.headSha == "" {
		return r.fail("no-head", "ユーザのチェックアウト（"+r.mainTree+"）の HEAD を読めません。", exitUnmet)
	}
	version := gitOut(r.in.Cwd, 0, "version")
	m := gitVersion.FindStringSubmatch(version)
	major, minor := 0, 0
	if m != nil {
		major, _ = strconv.Atoi(m[1])
		minor, _ = strconv.Atoi(m[2])
	}
	if m == nil || major < 2 || major == 2 && minor < 40 {
		return r.fail("git-old", "git が 2.40 未満（"+version+"）です。取り込みに使う merge-tree --merge-base が無いので切りません。git を 2.40 以上に更新してから再実行してください。", exitUnmet)
	}
	switch bound, why := branch.BaseBound(r.in.Conf, r.in.Root, r.mainTree, r.target); string(bound) {
	case "no":
	case "yes":
		return r.fail("base-bound", "取り込み先 "+r.target+" はチケットに結び付く（"+why+"）ので、直接作業では切りません。チケットの作業は ccnavi-ticket.sh で進めます（.ccnavi/docs/worktree.md）。", exitUnmet)
	default:
		return r.fail("base-bound-unknown", "取り込み先 "+r.target+" がチケットに結び付くかを決められません（置き場の読み取りに失敗）。ユーザに確かめてください。", exitUnmet)
	}
	return true
}

// parentTree は、cwd が親のワークツリーで、親が承認済み・レビュー待ち・着手済みの子があるときに断る。親のワークツリーから
// 直接作業へ切り替えられるのは、承認依頼の時点（親の提案が todo/ にあり、承認前）だけ。切り替えても取り下げられないものが
// 残るため。断るのは cwd が親のワークツリーのときだけ（ワークスペースのルートや子のワークツリーから打てば掛からない。
// 意図して残した抜け道）。--adopt は対象外（親のワークツリーの引き取りは adopt-ticket が断る）。
func (r *run) parentTree() bool {
	if r.adopt {
		return true
	}
	name := ospath.Basename(r.top)
	f := r.in.Leftover(r.top, name)
	why := ""
	switch f.Stage {
	case "doing":
		why = "親 " + name + " は承認済みで、着手済みの子・MR が取り下げられずに残る"
	case "review":
		why = "親 " + name + " は完了済み（レビュー待ち）で、MR が取り下げられずに残る"
	default:
		if f.Doing != 0 {
			why = "親 " + name + " に着手済みの子が " + strconv.Itoa(f.Doing) + " 件あり、取り下げられずに残る"
		}
	}
	if why == "" {
		return true
	}
	r.o.Err("ccnavi-worktree: ワークツリーは切っていない。親のワークツリー（" + name + "）から直接作業へは切り替えられない。" + why + "。")
	r.o.Err("親のワークツリーから切り替えられるのは、承認依頼の時点（親の提案が todo/ にあり、承認前）だけ。")
	if f.Stage == "doing" {
		r.o.Err(r.say("NOTE_WORKTREE_PARENT_CLOSE"))
	}
	if f.Doing != 0 {
		r.o.Err(r.say("NOTE_WORKTREE_PARENT_CHILDREN", "count", strconv.Itoa(f.Doing)))
	}
	r.o.Err(r.say("NEXT_WORKTREE_PARENT_ACTIVE"))
	r.o.Val("reason", "parent-active")
	r.o.Exit(exitUnmet)
	return false
}

// marker は、同じ名前の終わっていない取り込みの記録が残っていれば断る。前の作業の K や一時ワークツリーを新しい作業と
// 取り違え、新しい記録と ref を片付けてしまうため。
func (r *run) marker() bool {
	marks, found := integrate.GitPath(r.mainTree, integrate.RecordDir, time.Time{})
	if !found || !ospath.IsFile(ospath.Join(marks, r.name, "marker")) {
		return true
	}
	return r.fail("marker-left", r.name+" の取り込みの記録（"+ospath.Join(marks, r.name, "marker")+"）が残っています。前の取り込みが終わっていません。ccnavi-git.sh integrate-done "+r.name+" で終わらせるか、integrate --abort "+r.name+" でやめてから、切り直してください。", exitUnmet)
}

// state は C4・C5・C7 と、共通ディレクトリ・ほかの直接作業を調べる。
func (r *run) state() bool {
	r.topBranch = gitOut(r.top, 0, "symbolic-ref", "--quiet", "--short", "HEAD")
	r.c4 = r.topBranch != r.target
	r.c5 = r.target != r.integ
	// .claude/worktrees/ はワークツリーの置き場で、ccnavi 自身が作る。ユーザの書きかけではないので数えない
	// （.gitignore に行が無い配布先では、最初のワークツリーを切ったあと未追跡のまま残る）。数えないのはこの置き場だけ。
	for _, line := range strings.Split(gitOut(r.mainTree, slow, "status", "--porcelain", "--untracked-files=all"), "\n") {
		if len(line) < 3 || inWorktrees(line[3:]) {
			continue
		}
		switch {
		case strings.HasPrefix(line, "??"):
			r.added++
		case !strings.ContainsRune("?! ", rune(line[0])) || line[0] == ' ' && !strings.ContainsRune("?! ", rune(line[1])):
			r.changed++
		}
	}
	for _, path := range lines(gitOut(r.mainTree, slow, "diff", "--cached", "--name-only")) {
		if !inWorktrees(path) {
			r.staged++
		}
	}
	r.c7 = r.changed != 0 || r.added != 0
	if r.commonDir = commonOf(r.top); r.commonDir == "" {
		return r.fail("no-common-dir", "git の共通ディレクトリを読めません。", exitUnmet)
	}
	// 別の名前で、記録があり、作業ワークツリーも残っている直接作業。並行作業は普通にあるので止めない。compact で会話が
	// 忘れた続きを別に切り直さないよう、切った後に案内するだけ。
	names, _ := ospath.DiskListdir(ospath.Join(r.commonDir, tree.DirectDir))
	var others []string
	for _, o := range names {
		if ospath.IsFile(ospath.Join(r.commonDir, tree.DirectDir, o)) && o != r.name &&
			ospath.IsDir(ospath.Join(r.in.Root, ".claude", "worktrees", o)) {
			others = append(others, o)
		}
	}
	r.others = strings.Join(others, " ")
	return true
}

// inWorktrees は、status・diff の出すパス（"…" で囲まれることがある）が .claude/worktrees/ の下か。
func inWorktrees(path string) bool {
	return strings.HasPrefix(strings.TrimPrefix(path, `"`), ".claude/worktrees/")
}

// nameNote は直接作業の名前の形が合わないときの警告。切る前に（--dry-run でも）言う。
func (r *run) nameNote() {
	if text := ids.DirectNameWarning(r.name, r.in.Conf.BranchPrefixes, r.in.Root); text != "" {
		r.o.Out("warn: " + text)
	}
}

// short は版の短い名前。読めなければそのまま。
func short(dir, rev string) string {
	if s := gitOut(dir, 0, "rev-parse", "--short", rev); s != "" {
		return s
	}
	return rev
}

// cwdBranch は cwd のブランチの言い方。
func (r *run) cwdBranch() string {
	if r.topBranch == "" {
		return "ブランチの外"
	}
	return r.topBranch
}

// dryRun は何も作らず、調べた結果と予定だけ出す（fetch もしない）。
func (r *run) dryRun() {
	r.o.Out("dry-run: 何も作らない。調べた結果と予定は次のとおり（fetch はしていないので、origin との関係は見ていない）。")
	r.o.Out("取り込み先: " + r.target + "（" + r.mainTree + "。リモートのデフォルトは " + r.integ + "）")
	if r.c4 {
		r.o.Out("状態: cwd のブランチ（" + r.cwdBranch() + "）が取り込み先と違う。実行するには --base " + r.target + " が要る")
	}
	if r.c5 {
		r.o.Out("状態: 取り込み先がデフォルトブランチ（" + r.integ + "）でない。実行するには --accept が要る")
	}
	if r.c7 {
		r.o.Out("状態: 書きかけ 変更 " + strconv.Itoa(r.changed) + "・新規 " + strconv.Itoa(r.added) + "（staged " + strconv.Itoa(r.staged) + "）。実行するには --accept と、--copy-wip か --no-wip が要る")
	}
	if r.others != "" {
		r.o.Out("案内: 作業中の直接作業のワークツリーが他にある（" + r.others + "）。切ったあとに知らせる")
	}
	r.o.Out("ブランチ: " + r.name + "（起点 " + short(r.mainTree, r.headSha) + "）")
	r.o.Out("ワークツリー: " + r.dest)
	r.nameNote()
	r.o.Exit(0)
}

// code は確認コード（材料の要約の先頭 8 字）。状態が動けば変わる。
func code(input string) string {
	sum := sha1.Sum([]byte("blob " + strconv.Itoa(len(input)) + "\x00" + input))
	return hex.EncodeToString(sum[:])[:8]
}

// writeRecord は直接作業の記録（version 4）を書く。kv は版と created の間の項目。
func (r *run) writeRecord(common string, kv ...string) (string, bool) {
	dir := ospath.Join(common, tree.DirectDir)
	path := ospath.Join(dir, r.name)
	if err := makeDirs(dir); err != nil {
		return path, r.fail("record-failed", "記録の置き場（"+dir+"）を作れません。", exitUnmet)
	}
	all := append([]string{"version", tree.DirectVersion}, kv...)
	all = append(all, "created", strconv.FormatInt(time.Now().Unix(), 10))
	if !integrate.WriteRecord(path, all...) {
		return path, r.fail("record-failed", "記録（"+path+"）を書けません。", exitUnmet)
	}
	return path, true
}

// sideWord は --side-by-side の記録の値。
func (r *run) sideWord() string {
	if r.side {
		return "yes"
	}
	return "no"
}

// sideLine は切った・引き取った後の「横で作業する」の行。
func (r *run) sideLine() {
	if r.side {
		r.o.Out("横で作業する: はい（" + r.mainTree + " で見つかった変更は自動で戻さず報告する。ccnavi の設定ファイルは戻す）")
	} else {
		r.o.Out("横で作業する: いいえ")
	}
}
