package worktrees

import (
	"cmp"
	"fmt"
	"io"
	"os"
	"strings"
	"time"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/pathmatch"
	"ccnavi/internal/infra/shell"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/tickets/model/place"
)

const (
	timeout = 30 * time.Second
	// stashShown は退避したファイルを名指しする行の上限。
	stashShown = 10
	// stashDir は退避先（ワークスペースルートの一時の置き場の下）。
	stashDir = "worktrees"

	cleanScript = "ccnavi-clean.sh"

	textDropOutside = "NEXT_OPS_DROP_OUTSIDE"
)

// Status は Drop の結果の種類。
type Status string

const (
	Removed   Status = "removed"
	Absent    Status = "absent"
	CwdInside Status = "cwd"
	Dirty     Status = "dirty"
	Refused   Status = "refused"
)

// Dropped は 1 本のワークツリーを消した結果。Detail は消せなかった理由や、退避したファイル。
type Dropped struct {
	Name   string
	Status Status
	Path   string
	Detail string
}

// Removed は消した（もともと無いのも含む）か。
func (d Dropped) Removed() bool { return d.Status == Removed || d.Status == Absent }

// classifierNote は自動モードの分類器に止められたときの道。allow は許可に足す 1 本の例。
func classifierNote(root, allow string) string {
	return "Claude Code の自動モードの分類器に止められたら、迂回せずにユーザに許可を頼む。" +
		"以後も許可するなら、ユーザがホームの ~/.claude/settings.json の permissions.allow に、" +
		"この操作だけに絞った許可（例 `Bash(" + allow + ")`）を追加します" +
		"（分類器はプロジェクトの .claude/settings.json を読まない。" +
		"`" + config.ScriptCommand(root, config.GitWrapper) + " *` のような広い許可は push まで" +
		"分類器を通さなくなるので勧めない）"
}

// retryCommand は外へ出てから打ち直す 1 本。
func retryCommand(root, name string) string {
	return config.ScriptCommand(root, cleanScript) + " --worktree " + name
}

// badName は `.claude/worktrees/` の直下の名前として使えない（空・`.`・`..`・パスの区切り）か。
func badName(name string) bool {
	return name == "" || name == "." || name == ".." || strings.ContainsAny(name, `/\:`)
}

// screen は消してよいかを読むだけで判定する。止める理由（もともと無い Absent も含む）があればそれを、通れば
// nil と持ち主のリポジトリを返す。Drop のゲートで、Blocked も同じものを使う。無視されたファイルは止める理由に
// しない（Drop が退避する）。
func screen(root, name, cwd string) (*Dropped, string) {
	path := worktreePath(root, name)
	refuse := func(why string) (*Dropped, string) {
		return &Dropped{Name: name, Status: Refused, Path: path, Detail: why}, ""
	}
	if badName(name) {
		return refuse("名前にパスは書けない（.claude/worktrees/ の直下の名前）")
	}
	if !ospath.DiskLexists(path) {
		return &Dropped{Name: name, Status: Absent, Path: path}, ""
	}
	if pathmatch.IsLink(path) {
		return refuse("リンクなので、リンクの先は削除しません。")
	}
	if !ospath.IsDir(path) {
		return refuse("ディレクトリでない")
	}
	if inside(cwd, path) {
		return &Dropped{Name: name, Status: CwdInside, Path: path}, ""
	}
	owner, why := Owner(path)
	if owner == "" {
		return refuse(why)
	}
	out, ok := status(path)
	if !ok {
		return refuse("git の状態を読めない。未コミットの変更を確かめられない")
	}
	if ospath.Strip(out) != "" {
		return &Dropped{Name: name, Status: Dirty, Path: path}, ""
	}
	return nil, owner
}

// status は未追跡を含む `git status --porcelain`。
func status(path string) (string, bool) {
	done := git.Run(path, []string{"status", "--porcelain", "--untracked-files=all"}, git.Options{Timeout: timeout, RawPaths: true})
	return done.Out, done.OK()
}

// Blocked は Drop がこのワークツリーを消さない理由。消せる（もともと無いのも含む）なら nil。消す前に断りたい
// 呼び手（retire）が、Drop と同じ判定を使う。
func Blocked(root, name, cwd string) *Dropped {
	found, _ := screen(root, name, cwd)
	if found != nil && !found.Removed() {
		return found
	}
	return nil
}

// Drop は `.claude/worktrees/<名前>` を消す。消さないときは理由を持って返す（何も消さない）。
// 生成物を消し、無視されたそれ以外を退避してから `git worktree remove`。git が断れば（ロック・submodule など）
// その拒否文で Refused（生成物は作り直せるので先に消えていてよい。退避したものは退避先に残る）。
func Drop(root, name, cwd string) Dropped {
	found, owner := screen(root, name, cwd)
	if found != nil {
		return *found
	}
	path := worktreePath(root, name)
	refused := func(why string) Dropped { return Dropped{Name: name, Status: Refused, Path: path, Detail: why} }
	if failed := cleanGenerated(path); failed != "" {
		return refused(failed)
	}
	note, stuck := stashIgnored(root, name, path)
	if stuck != "" {
		return refused(stuck)
	}
	done := git.Run(owner, []string{"worktree", "remove", path}, git.Options{Timeout: timeout})
	if !done.OK() {
		said := firstLine(done)
		if said == "" {
			said = "理由不明"
		}
		detail := "git worktree remove が失敗した（" + said + "）"
		if note != "" {
			detail += "\n" + note
		}
		return refused(detail)
	}
	return Dropped{Name: name, Status: Removed, Path: path, Detail: note}
}

// firstLine は git の拒否文（標準エラー、無ければ起こせなかった理由）の 1 行目。
func firstLine(done git.Done) string {
	said := done.Stderr
	if said == "" && done.Failure != nil {
		said = done.Failure.Error()
	}
	if lines := ospath.Splitlines(ospath.Strip(said)); len(lines) > 0 {
		return lines[0]
	}
	return ""
}

// AfterDropped は Report の消した行の括弧の後ろに足す既定の一言。
const AfterDropped = "。ブランチは残してある"

// Report は Drop の結果を言う。消さなかったときも標準出力へ（呼び手の操作そのものは成功している）。after は
// 消した行の括弧の後ろに足す一言（ブランチをどうするか）。
func Report(stdout io.Writer, root string, d Dropped, after string) {
	switch d.Status {
	case Removed:
		fmt.Fprintf(stdout, "ワークツリー %s を削除しました（%s%s）\n", d.Name, d.Path, after)
		if d.Detail != "" {
			fmt.Fprintf(stdout, "%s\n", d.Detail)
		}
	case Absent:
		fmt.Fprintf(stdout, "ワークツリー %s はありません（削除するものがありません）\n", d.Name)
	case CwdInside:
		retry := retryCommand(root, d.Name)
		fmt.Fprintf(stdout, "cwd がワークツリー %s の中にあるので削除しませんでした。\n%s\n", d.Name,
			texts.Say(root, textDropOutside, texts.Values{"command": retry, "classifier_note": classifierNote(root, retry)}, nil))
	case Dirty:
		fmt.Fprintf(stdout, "ワークツリー %s に未コミットの変更があるので、何も削除しませんでした。"+
			"別のセッションが作業している見込みがあるので、"+
			"ユーザに git -C %s status で確かめてもらう\n", d.Name, shell.Quote(d.Path))
	default:
		fmt.Fprintf(stdout, "ワークツリー %s を削除しなかった: %s\n", d.Name, d.Detail)
	}
}

// RunDrop は `worktree drop <名前>`。消したか、もともと無ければ 0。消さなかったら 1。
func RunDrop(stdout io.Writer, root, name, cwd string) int {
	d := Drop(root, name, cwd)
	Report(stdout, root, d, AfterDropped)
	if d.Removed() {
		return config.ExitOK
	}
	return 1
}

// ignored は git が無視しているもの（生成物を除く）と、読めなかった理由。--directory で全体が無視された
// ディレクトリは 1 行（末尾の "/"）にまとまる。下書きの置き場は追跡されるのでここには現れない。
func ignored(top string) ([]string, string) {
	done := git.Run(top, []string{"ls-files", "-z", "--others", "--ignored", "--exclude-standard", "--directory"},
		git.Options{Timeout: timeout, RawPaths: true})
	if !done.OK() {
		return nil, "git が無視しているファイルを読めない。削除してよいかを確かめられない"
	}
	var out []string
	for _, p := range strings.Split(done.Out, "\x00") {
		if p != "" && !isGenerated(top, p) {
			out = append(out, p)
		}
	}
	return out, ""
}

// stashIgnored は生成物でない無視されたもの（.env など）を `<ルート>/scratchpad/worktrees/<名前>/` へ移す。
// 答えは（退避したことを言う文, 止まった理由）。移すのは最上位の項目ごとの rename で、中身を走査しない。
// 退避先に同じパスがあれば上書きせず止める。退避先が git に無視されていなければ（.env がコミットされうる）、
// 何も動かさず止める。移せなければ、再試行せず、理由と退避済みのものを言って止める。
func stashIgnored(root, name, top string) (string, string) {
	found, why := ignored(top)
	if why != "" {
		return "", why
	}
	if len(found) == 0 {
		return "", ""
	}
	dest := ospath.Join(root, place.ScratchRoot, stashDir, name)
	shownDest := place.ScratchRoot + "/" + stashDir + "/" + name
	checked := git.Run(root, []string{"check-ignore", "-q", "--", shownDest}, git.Options{Timeout: timeout, PlainPathspecs: true})
	if !checked.OK() {
		return "", "退避先 " + shownDest + " が git に無視されていないので、無視されたファイルを退避しません。" +
			"ワークスペースルートの .gitignore に `/scratchpad` を足してから再実行します。"
	}
	var moved []string
	for _, rel := range found {
		parts := relParts(rel)
		target := ospath.Join(dest, parts...)
		if err := moveAside(ospath.Join(top, parts...), target); err != "" {
			done := ""
			if len(moved) > 0 {
				done = "（退避済み: " + strings.Join(moved, ", ") + "）"
			}
			return "", "無視されたファイル " + rel + " を " + shownDest + " へ退避できませんでした（" + err + "）" + done +
				"。原因を直してから再実行します。"
		}
		moved = append(moved, rel)
	}
	shownMoved := moved[:min(len(moved), stashShown)]
	if len(moved) > stashShown {
		shownMoved = append(shownMoved, fmt.Sprintf("…ほか %d 件", len(moved)-stashShown))
	}
	lines := []string{"git が無視しているファイルを " + shownDest + " へ退避しました（不要ならユーザが消す）:"}
	for _, line := range shownMoved {
		lines = append(lines, "  "+line)
	}
	return strings.Join(lines, "\n"), ""
}

// moveAside は source を target へ rename する。失敗した理由（Python の strerror）か空。
func moveAside(source, target string) string {
	if ospath.DiskLexists(target) {
		return "退避先に同じパスがある"
	}
	if err := os.MkdirAll(ospath.Dirname(target), 0o777); err != nil {
		return cmp.Or(fsio.StrError(err), fsio.ErrorText(err))
	}
	if err := os.Rename(source, target); err != nil {
		return cmp.Or(fsio.StrError(err), fsio.ErrorText(err))
	}
	return ""
}

// relParts は git が出した相対パス（区切りは "/"、ディレクトリは末尾に "/"）の各段。
func relParts(rel string) []string {
	var parts []string
	for _, p := range strings.Split(strings.TrimRight(rel, "/"), "/") {
		if p != "" {
			parts = append(parts, p)
		}
	}
	return parts
}

// Owner はワークツリーの元リポジトリ（`worktree remove` を打つ場所）。無ければ空と理由。`.git` がファイル
// （リンクされたワークツリー）であることを求める。ディレクトリならそれ自身がリポジトリで、消す対象ではない。
func Owner(path string) (string, string) {
	if !ospath.IsFile(ospath.Join(path, ".git")) {
		return "", "git のワークツリーとして登録されていない（.git のファイルがありません）ので削除しません。"
	}
	done := git.Run(path, []string{"rev-parse", "--git-common-dir"}, git.Options{Timeout: timeout})
	common := ""
	if done.OK() {
		common = ospath.Strip(done.Out)
	}
	if common == "" {
		return "", "元のリポジトリを読めない（git rev-parse --git-common-dir が失敗した）"
	}
	if !ospath.IsAbs(common) {
		common = ospath.Join(path, common)
	}
	common = ospath.Normpath(common)
	if ospath.Basename(common) != ".git" {
		return "", "元のリポジトリの形が読めない（" + common + "）"
	}
	return ospath.Dirname(common), ""
}

func inside(cwd, path string) bool {
	if cwd == "" {
		return false
	}
	here, top := canonical(cwd), canonical(path)
	return here == top || strings.HasPrefix(here, top+ospath.Sep)
}

func canonical(path string) string {
	return pathcase.Fold(ospath.Normpath(ospath.Realpath(path)))
}
