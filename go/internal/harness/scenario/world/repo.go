package world

import (
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	"ccnavi/internal/harness/scenario/core"
	"ccnavi/internal/harness/scenario/host"
	"ccnavi/internal/harness/scenario/prepare"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/records/diaglog"
)

const (
	// LockName は対象の .git/ の中のロック（git clean で消えず、status も汚さない）。
	LockName = "ccnavi-scenario.lock"
	// LeftName は戻さずに残した回の印（中身は出力の置き場）。
	LeftName = "ccnavi-scenario.left"
	// ccnaviRefPrefix は ccnavi 自身が対象の git に書く専用の ref。
	ccnaviRefPrefix = "refs/ccnavi"
)

// ccnaviStateDirs は ccnavi 自身が対象の git の共通ディレクトリに書く状態（直接作業の記録・取り込みの marker）。
var ccnaviStateDirs = []string{"ccnavi-direct", "ccnavi-integrate", "ccnavi-integrate-done"}

func log(root string) *diaglog.Logger { return diaglog.Get(core.LogName, root) }

func toplevel(path string) string {
	r := Git([]string{"rev-parse", "--show-toplevel"}, hostOpts(path))
	if r.Code != 0 || out(r) == "" {
		return ""
	}
	return ospath.Realpath(out(r))
}

func commonDir(path string) string {
	r := Git([]string{"rev-parse", "--git-common-dir"}, hostOpts(path))
	return pathcase.Fold(ospath.Realpath(ospath.Join(path, out(r))))
}

func origin(top string) string {
	r := Git([]string{"remote", "get-url", "origin"}, hostOpts(top))
	if r.Code != 0 || out(r) == "" {
		return ""
	}
	return pathcase.Fold(strings.TrimSuffix(strings.TrimRight(out(r), "/"), ".git"))
}

func inside(path, top string) bool {
	top = strings.TrimRight(pathcase.Fold(top), string(os.PathSeparator))
	return strings.HasPrefix(pathcase.Fold(path), top+string(os.PathSeparator))
}

const disposableUse = "setup.git・setup.reset・setup.files の追記は、使い捨てのリポジトリにだけ使える"

// RequireDisposableRepo は target が使い捨てのリポジトリだと確かめ、その toplevel（実体）を返す。違えば *core.Refusal。
//
// setup.git・setup.reset・setup.files の追記は、すべてこれを通る。比べるのは toplevel の実体で、ccnavi 本体（root）と
// 同じもの、origin が本体と同じものは断る。許可されるのは allowed（空なら root/projects/test-repo）の実体だけ。
// どちらかに origin が無いときは origin を比べない。
func RequireDisposableRepo(root, target, allowed string) (string, error) {
	if allowed == "" {
		allowed = filepath.Join(root, filepath.FromSlash(core.DisposableRel))
	}
	top := toplevel(target)
	if top == "" {
		return "", &core.Refusal{What: fmt.Sprintf("対象 %s は git のリポジトリではない（または無い）", target),
			Next: core.DisposableRel + " を用意してから打ち直す", Notes: []string{disposableUse}}
	}
	body := ospath.Realpath(root)
	if pathcase.Fold(top) == pathcase.Fold(body) {
		return "", &core.Refusal{What: "対象が ccnavi 本体（ワークスペースルート）になっている: " + top,
			Next: "対象を使い捨てのリポジトリ（" + core.DisposableRel + "）にして打ち直す", Notes: []string{disposableUse}}
	}
	if commonDir(top) == commonDir(body) {
		return "", &core.Refusal{What: "対象が ccnavi 本体と同じリポジトリのワークツリーになっている: " + top,
			Next: "本体とは別のリポジトリを " + core.DisposableRel + " に置いて打ち直す", Notes: []string{disposableUse}}
	}
	if mine, theirs := origin(top), origin(body); mine != "" && mine == theirs {
		return "", &core.Refusal{What: "対象の origin が ccnavi 本体の origin と同じ: " + top,
			Next: "本体とは別のリポジトリを " + core.DisposableRel + " に置いて打ち直す", Notes: []string{disposableUse}}
	}
	if want := ospath.Realpath(allowed); pathcase.Fold(top) != pathcase.Fold(want) {
		return "", &core.Refusal{What: fmt.Sprintf("対象の実体 %s が、許可された %s と違う", top, want),
			Next: "対象を " + core.DisposableRel + " にして打ち直す", Notes: []string{disposableUse}}
	}
	return top, nil
}

func gitDir(top string) (string, error) {
	r := Git([]string{"rev-parse", "--absolute-git-dir"}, hostOpts(top))
	if r.Code != 0 {
		return "", fmt.Errorf(".git の場所を取れない: %s: %s", top, strings.TrimSpace(r.Stderr))
	}
	return ospath.Realpath(out(r)), nil
}

// TargetGit は対象で git を実行する（cwd は判定を通した toplevel）。コマンドと結果を診断ログに残す。
// quiet は reset のように何度も呼ぶときだけ true（ループの中で INFO を書かない）。
func TargetGit(root, top string, args []string, quiet bool, env map[string]string) host.GitResult {
	r := Git(args, host.GitOpts{Dir: top, Env: env})
	errLine := ""
	if r.Code != 0 {
		errLine = firstLine(r.Stderr)
	}
	l := log(root)
	logf := l.Info
	if quiet {
		logf = l.Debug
	}
	logf("対象の git を実行した", "cmd", "git "+strings.Join(args, " "), "rc", r.Code, "out_len", len(r.Stdout), "err", errLine)
	return r
}

// RunSetupGit は setup.git のコマンドを、対象の toplevel を cwd にして順に実行する（シェルを介さない）。
// 各コマンドのあと ccnavi 本体の RepoSnapshot が変わっていたら止める。
// via は本物の origin への通信を bare へ向ける -c（host.GitVia）、env はこの git にだけ足す環境（docker-gitlab のトークン）。
func RunSetupGit(root, target string, commands [][]string, allowed string, via []string, env map[string]string) error {
	top, err := RequireDisposableRepo(root, target, allowed)
	if err != nil {
		return err
	}
	before := RepoSnapshot(root)
	for _, argv := range commands {
		if err := core.CheckGitArgv(argv); err != nil {
			return &core.Refusal{What: "setup.git を実行しない: " + err.Error(), Next: "シナリオの setup.git を直す"}
		}
		r := TargetGit(root, top, append(append([]string{}, via...), argv[1:]...), false, env)
		if r.Code != 0 {
			return &core.Refusal{What: fmt.Sprintf("setup.git が失敗した: %s（終了コード %d）", strings.Join(argv, " "), r.Code),
				Next:  "原因を直して打ち直す",
				Notes: []string{"git の出力: " + firstLine(r.Stderr), "対象は後始末で戻す（setup.reset があるとき）"}}
		}
		if RepoSnapshot(root) != before {
			return &core.Refusal{What: "setup.git の実行で ccnavi 本体の HEAD・ブランチ・ワークツリーが変わった: " + strings.Join(argv, " "),
				Next: "ccnavi 本体の状態を手で確かめて戻し、シナリオの setup.git を直す", Notes: []string{"回し続けない"}}
		}
	}
	return nil
}

func gitCommonPath(top string) (string, error) {
	r := Git([]string{"rev-parse", "--path-format=absolute", "--git-common-dir"}, hostOpts(top))
	if r.Code != 0 || out(r) == "" {
		return "", fmt.Errorf("git の共通ディレクトリを取れない: %s: %s", top, strings.TrimSpace(r.Stderr))
	}
	return ospath.Realpath(out(r)), nil
}

func removeAny(path string) error {
	info, err := os.Lstat(path)
	if err != nil {
		return nil
	}
	if info.IsDir() {
		return os.RemoveAll(path)
	}
	return os.Remove(path)
}

// ResetRepo は対象を base へ戻す。順序: ワークツリーを消す → prune → ccnavi の記録・marker・ref を消す →
// logs/archive/<プロジェクト>/ を消す → main を base に合わせて切り替える → main 以外のローカルブランチを消す →
// タグを消す → stash を空にする → reset --hard → clean -fd。
// ccnavi の状態は、ワークツリーを消して prune したあとに消す。記録は一時ワークツリーやブランチを指すので、
// ワークツリーが消せずに止まった回で、記録だけ先に消さない。clean に -x は付けない（.gitignore 済みを消さない）。
// refs/remotes は触らない。
func ResetRepo(root, target, base, allowed string) error {
	top, err := RequireDisposableRepo(root, target, allowed)
	if err != nil {
		return err
	}
	var fail error
	run := func(args ...string) string {
		if fail != nil {
			return ""
		}
		r := TargetGit(root, top, args, true, nil)
		if r.Code != 0 {
			log(root).Error("対象を戻せなかった", "target", top, "base", base, "cmd", strings.Join(args[:min(3, len(args))], " "))
			fail = &core.Refusal{What: fmt.Sprintf("対象を戻す途中で失敗した: git %s（終了コード %d）", strings.Join(args[:min(3, len(args))], " "), r.Code),
				Next: fmt.Sprintf("%s を手で %s に戻す", top, base), Notes: []string{"git の出力: " + firstLine(r.Stderr)}}
			return ""
		}
		return r.Stdout
	}
	run("cat-file", "-e", base+"^{commit}")
	var paths []string
	for _, line := range strings.Split(run("worktree", "list", "--porcelain"), "\n") {
		if p, ok := strings.CutPrefix(strings.TrimRight(line, "\r"), "worktree "); ok {
			paths = append(paths, p)
		}
	}
	keep := map[string]bool{pathcase.Fold(ospath.Realpath(top)): true}
	if len(paths) > 0 {
		keep[pathcase.Fold(ospath.Realpath(paths[0]))] = true
	}
	for _, p := range paths {
		if !keep[pathcase.Fold(ospath.Realpath(p))] {
			run("worktree", "remove", "--force", p)
		}
	}
	run("worktree", "prune")
	if fail != nil {
		return fail
	}
	if err := clearCcnaviState(top, run); err != nil {
		return err
	}
	if err := removeAny(filepath.Join(root, "logs", "archive", filepath.Base(top))); err != nil {
		return err
	}
	run("checkout", "-f", "-B", "main", base)
	for _, name := range strings.Fields(run("for-each-ref", "--format=%(refname:short)", "refs/heads")) {
		if name != "main" {
			run("branch", "-D", name)
		}
	}
	tags := strings.Fields(run("tag", "-l"))
	for i := 0; i < len(tags); i += 100 {
		run(append([]string{"tag", "-d"}, tags[i:min(i+100, len(tags))]...)...)
	}
	run("stash", "clear")
	run("reset", "--hard", base)
	run("clean", "-fd")
	if fail != nil {
		return fail
	}
	log(root).Info("対象を base に戻した", "target", top, "base", base)
	return nil
}

// clearCcnaviState は ccnavi 自身が対象の git に書く状態（直接作業の記録・取り込みの marker・専用の ref）を消す。
// top は RequireDisposableRepo を通したものだけ。消すのは共通ディレクトリ直下の名指しの 3 つと refs/ccnavi の下だけで、
// logs/ など .gitignore 済みのものは触らない。
func clearCcnaviState(top string, run func(args ...string) string) error {
	common, err := gitCommonPath(top)
	if err != nil {
		return err
	}
	for _, name := range ccnaviStateDirs {
		if err := removeAny(filepath.Join(common, name)); err != nil {
			return err
		}
	}
	for _, ref := range strings.Fields(run("for-each-ref", "--format=%(refname)", ccnaviRefPrefix)) {
		run("update-ref", "-d", ref)
	}
	return nil
}

// AcquireTarget は対象を排他で使い始める。ロックのパスを返す。使えなければ *core.Refusal。
//
// ロックは対象の .git/ の中（O_CREAT|O_EXCL。中身は PID）。あれば断る（PID は文に出すだけ。使っている回が無いなら
// ファイルを消すよう言う）。戻さずに残した回の印があれば、その回を続けるとき（resuming で出力の置き場が同じ）以外は断る。
func AcquireTarget(top, outDir string, resuming bool) (string, error) {
	dir, err := gitDir(top)
	if err != nil {
		return "", err
	}
	left := filepath.Join(dir, LeftName)
	if data, err := os.ReadFile(left); err == nil {
		who := strings.TrimSpace(string(data))
		same := who != "" && pathcase.Fold(ospath.Realpath(who)) == pathcase.Fold(ospath.Realpath(outDir))
		if !(resuming && same) {
			return "", &core.Refusal{What: "前の回が対象を戻さずに残している: " + who,
				Next:  fmt.Sprintf("続けるなら `%s next %s`、やめるなら `%s restore %s`", core.Runner, who, core.Runner, who),
				Notes: []string{"戻せずに残した回も同じ。restore で戻す"}}
		}
	}
	lock := filepath.Join(dir, LockName)
	f, err := os.OpenFile(lock, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o600)
	if err != nil {
		if !os.IsExist(err) {
			return "", err
		}
		pid := "読めない"
		if data, rerr := os.ReadFile(lock); rerr == nil {
			if n, perr := strconv.Atoi(strings.TrimSpace(string(data))); perr == nil && n > 0 {
				pid = strconv.Itoa(n)
			}
		}
		return "", &core.Refusal{What: fmt.Sprintf("同じ対象を別の回が使っている（PID %s）: %s", pid, lock),
			Next: "その回が終わってから打ち直す", Notes: []string{"使っている回が無いことを確かめてから、ロックのファイル " + lock + " を消す"}}
	}
	_, werr := f.WriteString(strconv.Itoa(os.Getpid()))
	if cerr := f.Close(); werr == nil {
		werr = cerr
	}
	return lock, werr
}

// ReleaseTarget は対象を放す。戻せなかったときは文面を say に出し、その文面を返す（戻せたら空）。
//
// hold: 人間の操作で止めた回。base へ戻さず、ロックだけ放して残し印を書く（続きは next）。
// それ以外: base へ戻し、ロックと残し印を放す。戻せなければ残し印を書き、次の回を断らせる。
func ReleaseTarget(st *core.State, root string, hold bool, say func(string)) string {
	tgt := st.Target
	if tgt == nil || st.Released {
		return ""
	}
	var failure error
	dir, err := gitDir(tgt.Path)
	if err != nil {
		failure = err
	} else {
		left := filepath.Join(dir, LeftName)
		if !hold && tgt.Base != "" {
			failure = ResetRepo(root, tgt.Path, tgt.Base, "")
		}
		switch {
		case hold || failure != nil:
			outPath := st.Out
			if outPath != "" {
				outPath = ospath.Realpath(outPath)
			}
			_ = os.WriteFile(left, []byte(outPath), 0o644)
		default:
			_ = os.Remove(left)
		}
		_ = os.Remove(filepath.Join(dir, LockName))
	}
	if hold {
		st.Held = true
		return ""
	}
	if failure != nil {
		msg := (&core.Refusal{
			What:  fmt.Sprintf("対象 %s を %s に戻せなかった。手作業で戻す必要がある", tgt.Path, tgt.Base),
			Next:  fmt.Sprintf("%s を手で %s に戻し、`%s restore %s` で印を外す", tgt.Path, tgt.Base, core.Runner, st.Out),
			Notes: []string{"原因: " + firstLine(failure.Error()), "戻すまで、次の回は始められない"},
		}).Error()
		say(msg)
		return msg
	}
	st.Released = true
	return ""
}

// ResolveAppend は追記の対象（ワークスペースルートからの相対）を実体にして返す。toplevel の中にある、
// git 管理下のファイルだけ。.. ・絶対パス・外へ出るリンクは断る。
func ResolveAppend(rel, root, top string) (string, error) {
	slashed := strings.ReplaceAll(rel, "\\", "/")
	if filepath.IsAbs(rel) || ospath.IsAbs(rel) || containsDotDot(slashed) {
		return "", &core.Refusal{What: "追記のパスは、ワークスペースルートからの相対パスで .. を含まない形にする: " + rel, Next: "setup.files のパスを直す"}
	}
	real := ospath.Realpath(filepath.Join(root, filepath.FromSlash(slashed)))
	if !inside(real, top) {
		return "", &core.Refusal{What: fmt.Sprintf("追記のパスが対象 %s の外にある（外へ出るリンクを含む）: %s", top, rel),
			Next: "setup.files のパスを、対象の中のファイルにする"}
	}
	if info, err := os.Stat(real); err != nil || !info.Mode().IsRegular() {
		return "", &core.Refusal{What: "追記の対象のファイルが無い: " + rel,
			Next: "追記は対象にすでにあるファイルにだけ使える。パスを直すか、作って消す形（文字列）にする"}
	}
	relInTop, err := filepath.Rel(top, real)
	if err != nil {
		return "", err
	}
	r := Git([]string{"--literal-pathspecs", "ls-files", "--error-unmatch", "--", relInTop}, hostOpts(top))
	if r.Code != 0 {
		return "", &core.Refusal{What: "追記の対象が git 管理下にない: " + rel, Next: "追記は git 管理下のファイルにだけ使える。パスを直す"}
	}
	return real, nil
}

func containsDotDot(slashed string) bool {
	for _, part := range strings.Split(slashed, "/") {
		if part == ".." {
			return true
		}
	}
	return false
}

// CommitExists は対象に base がコミットとしてあるか。
func CommitExists(top, base string) bool {
	return Git([]string{"cat-file", "-e", base + "^{commit}"}, hostOpts(top)).Code == 0
}

// Hooks は prepare に渡す差し込み口（追記の対象の解決・コミットの有無）。
func Hooks() prepare.Hooks {
	return prepare.Hooks{ResolveAppend: ResolveAppend, CommitExists: CommitExists}
}

// CheckBeforeRun は回す前（確認文より前）に確かめられる setup の誤りを返す。何も変えない。
func CheckBeforeRun(scn *core.Scenario, root, top string, m *host.Manager) error {
	return prepare.CheckBeforeRun(Hooks(), scn.Setup.Files, scn.Setup.ResetBase, scn.HostKind(), m, root, top)
}
