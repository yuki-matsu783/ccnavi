package git

import (
	"strings"
	"time"

	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
)

// Kind は変更の種類。元に戻す手順がこの 3 つ（と戻す手順を持たない Committed）で分かれる。
type Kind string

const (
	KindNew     Kind = "new"     // 無かったものが現れた
	KindChanged Kind = "changed" // 中身が変わった
	KindGone    Kind = "gone"    // 追跡されていたものが消えた
	// KindCommitted はターンの間にコミットに入ったもの。status にはもう出ず、戻すには revert か履歴の
	// 書き換えが要る（どちらもラッパーが拒む）ので、報告のためだけに在る。
	KindCommitted Kind = "committed"
)

// KindLabels はユーザへ見せる表記。記録には Kind の英語のまま残し、報告の文面だけ日本語にする。
var KindLabels = map[Kind]string{
	KindNew:       "新規",
	KindChanged:   "変更",
	KindGone:      "削除",
	KindCommitted: "コミット済み",
}

// StateTimeout はワークツリーを読む git の期限。実行前チェックに張る期限より短い。
// 大きなリポジトリでは status も待たされるが、待たせるくらいなら何も言わない。
const StateTimeout = 2 * time.Second

// writeTimeout は index を書き換える git（restore と rm --cached）の期限。これらは index.lock を必ず取り、
// 期限で殺すと lock が残って以後の add や commit が止まるので、読む側より長く待つ。
// 1 回が hook の期限（10 秒）に収まる長さにする。
const writeTimeout = 5 * time.Second

// Reason は読めなかった理由。記録に入れ、実行後チェックが動いていない期間を後から数えられるようにする。
type Reason string

const (
	reasonNoWorktree Reason = "not-a-git-worktree"
	reasonNoGit      Reason = "git-not-found"
	reasonFailed     Reason = "git-failed"
	reasonTimeout    Reason = "git-timed-out"
)

// 読む git の引数。
var (
	// changesStatus: -z はパスをそのまま NUL 区切りで返す（既定は escape を掛け、解き損ねるとルールが当たらない）。
	// 未追跡は 1 件ずつ挙げる（既定はディレクトリ 1 つにまとめる）。改名は「消えた」と「現れた」の 2 件にし、
	// 1 件が 2 つのパスを持たないようにする。
	changesStatus = Porcelain{NUL: true, Untracked: UntrackedAll, NoRenames: true}
	// committedArgs: このツリーが積んだ未 push のコミットだけを数える（`--first-parent --no-merges` と
	// `--not --remotes`）。統合先を取り込んだマージや fast-forward が持ち込んだコミットを数えないため。
	// --no-renames と --ignore-submodules=none は、改名の移動元と submodule の進みが差分から消えないようにする。
	committedArgs = []string{"log", "--first-parent", "--no-merges", "--format=", "--name-only", "--no-renames",
		"--ignore-submodules=none", "-z"}
)

// Untracked は status の --untracked-files の値。空なら付けない（git の既定）。
type Untracked string

const (
	UntrackedAll Untracked = "all" // 未追跡を 1 件ずつ挙げる
	UntrackedNo  Untracked = "no"  // 未追跡を挙げない
)

// Porcelain は `git status --porcelain` に付けるフラグ。ゼロ値はフラグ無し（行区切り・未追跡は既定・改名を読む）。
type Porcelain struct {
	NUL       bool      // -z（パスを引用せず NUL で区切る）
	Untracked Untracked // --untracked-files=<値>
	NoRenames bool      // --no-renames
	// IgnoreDirtySubmodules は --ignore-submodules=dirty（submodule の中の未コミットを数えない）。
	IgnoreDirtySubmodules bool
	// Paths は `--` の後に渡す pathspec。nil なら付けない（空でも `--` だけは付ける）。
	Paths []string
}

// Args は git に渡す引数。
func (p Porcelain) Args() []string {
	args := []string{"status", "--porcelain"}
	if p.NUL {
		args = append(args, "-z")
	}
	if p.Untracked != "" {
		args = append(args, "--untracked-files="+string(p.Untracked))
	}
	if p.NoRenames {
		args = append(args, "--no-renames")
	}
	if p.IgnoreDirtySubmodules {
		args = append(args, "--ignore-submodules=dirty")
	}
	if p.Paths != nil {
		args = append(append(args, "--"), p.Paths...)
	}
	return args
}

// Status は `git status --porcelain` を p のフラグで起こす。
func Status(cwd string, p Porcelain, o Options) Done {
	return runGit(cwd, p.Args(), o)
}

// Change はワークツリーの変更 1 件。
type Change struct {
	Kind Kind
	// Path は git が返したパス（ワークツリーのルートからの相対、区切りは "/"）。見せる側と git に渡す側が使う。
	Path string
	// Full は行き着く先まで解いた絶対パス。ルールはこちらに適用する（表記を変えただけで外せないように）。
	Full string
	// Status は git の 2 文字（索引側とワークツリー側）。
	Status string
	Staged bool
}

// Key は同じ変更を同じものとして数える名前。種類も含める。変えられたファイルが次に消されたら、
// それは新しい出来事なのでもう一度言う。
func (c Change) Key() string {
	return string(c.Kind) + " " + c.Path
}

// TopLevel は root から上へ辿ってワークツリーのルートを探す。見つからなければ空文字。
// git を起こさずに探す（git の無い環境でもリポジトリでない場所でも、プロセスを 1 つ無駄に起こさない）。
// .git は worktree と submodule の中ではファイル。
func TopLevel(root string) string {
	directory := ospath.Abspath(root)
	for {
		if ospath.DiskExists(ospath.Join(directory, ".git")) {
			return directory
		}
		parent := ospath.Dirname(directory)
		if parent == directory {
			return ""
		}
		directory = parent
	}
}

// Read はワークツリーの未コミットの変更。読めなければ理由。timeout が 0 なら StateTimeout。止める手段を持たないので、
// 読めなかったときにできるのは理由を記録することだけ。
func Read(top string, timeout time.Duration) ([]Change, Reason) {
	if top == "" {
		return nil, reasonNoWorktree
	}
	done := Status(top, changesStatus, stateOptions(timeout))
	if reason := readFailure(done); reason != "" {
		return nil, reason
	}
	var changes []Change
	for _, entry := range strings.Split(done.Out, "\x00") {
		if c, ok := parseStatus(top, entry); ok {
			changes = append(changes, c)
		}
	}
	return changes, ""
}

// Committed は base..HEAD でこのツリーが積んだ未 push のコミットに入ったパス。読めなければ理由。
//
// 未コミットしか見ない Read の穴をターンの終わりに埋める（保護領域を汚してからコミットすると status から消える）。
// reflog は読まない。残る穴は docs/21_design/post-check.md。
func Committed(top, base string, timeout time.Duration) ([]Change, Reason) {
	if top == "" {
		return nil, reasonNoWorktree
	}
	if base == "" {
		return nil, ""
	}
	args := append(append([]string(nil), committedArgs...), base+"..HEAD", "--not", "--remotes")
	done := runGit(top, args, stateOptions(timeout))
	if reason := readFailure(done); reason != "" {
		return nil, reason
	}
	// 同じパスが複数のコミットに現れるのでまとめる。報告は「何が変わったか」で、順序は git が返した順。
	seen := map[string]bool{}
	var changes []Change
	for _, path := range strings.Split(done.Out, "\x00") {
		if path == "" || seen[path] {
			continue
		}
		seen[path] = true
		changes = append(changes, Change{Kind: KindCommitted, Path: path, Full: fsio.FullPath(path, top), Staged: true})
	}
	return changes, ""
}

func readFailure(done Done) Reason {
	switch {
	case done.Missing():
		return reasonNoGit
	case done.TimedOut():
		return reasonTimeout
	case !done.OK():
		return reasonFailed
	}
	return ""
}

// parseStatus は "XY path"（X は索引側、Y はワークツリー側）を読む。
func parseStatus(top, entry string) (Change, bool) {
	runes := []rune(entry)
	if len(runes) < 4 || runes[2] != ' ' {
		return Change{}, false
	}
	status, path := string(runes[:2]), string(runes[3:])
	index, worktree := runes[0], runes[1]
	c := Change{Path: path, Full: fsio.FullPath(path, top), Status: status, Staged: index != ' '}
	switch {
	case status == "??":
		c.Kind, c.Staged = KindNew, false
	case index == 'A':
		c.Kind, c.Staged = KindNew, true
	case index == 'D' || worktree == 'D':
		c.Kind = KindGone
	default:
		c.Kind = KindChanged
	}
	return c, true
}
