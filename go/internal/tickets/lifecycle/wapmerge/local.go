package wapmerge

import (
	"os"
	"strings"
	"time"

	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/tickets/lifecycle/refusal"
)

// write-and-push の親のワークツリーへのローカルの書き込みと戻し（途中の操作の確かめ・コミット・合流・比較つきの戻し）。
// push と ls-remote は sh が持つ。ここの git はラッパー（ccnavi-git.sh）を通らない。戻しに使う `restore --source`・
// `update-ref` はエージェントの入口では止める形で、ここは ccnavi 自身が書いたものを戻すため。

// Said は sh に言わせる文の並び（sh が文面の頭を付けて標準エラーへ出す）。
type Said []string

func (s *Said) add(text string) { *s = append(*s, text) }

func localOptions() git.Options { return git.Options{Timeout: git.MergeTimeout, RawPaths: true} }

// busyNames は途中の操作の印（`git rev-parse --git-path` の名前）。上から見て最初に在ったものを答える。
var busyNames = []string{"MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply", "sequencer", "index.lock"}

// Busy は親のワークツリーの途中の操作の名前（無ければ空）。
func Busy(tree string) string {
	for _, name := range busyNames {
		if ospath.DiskExists(gitPath(tree, name)) {
			return name
		}
	}
	return ""
}

// gitPath は `git rev-parse --git-path <名前>` をツリーからのパスにする（読めなければ <ツリー>/.git/<名前>）。
func gitPath(tree, name string) string {
	done := git.Run(tree, []string{"rev-parse", "--git-path", name}, localOptions())
	rel := ospath.Strip(done.Out)
	switch {
	case !done.OK() || rel == "":
		return ospath.Join(tree, ".git", name)
	case ospath.IsAbs(rel) || ospath.HasDrive(rel, true):
		return rel
	}
	return ospath.Join(tree, rel)
}

func revOf(tree, rev string) string {
	done := git.Run(tree, []string{"rev-parse", "-q", "--verify", rev}, localOptions())
	if !done.OK() {
		return ""
	}
	return ospath.Strip(done.Out)
}

// Head は親のワークツリーの先頭（読めなければ空）。
func Head(tree string) string { return revOf(tree, "HEAD") }

// usable は一覧の 1 行がツリーからの相対パスか（空と絶対パスは使わない）。
func usable(rel string) bool {
	return rel != "" && !ospath.IsAbs(rel) && !ospath.HasDrive(rel, true)
}

func tracked(tree, rel string) bool {
	done := git.Run(tree, []string{"ls-files", "--", literal(rel)}, localOptions())
	return done.OK() && ospath.Strip(done.Out) != ""
}

func literal(rel string) string { return ":(literal)" + rel }

func onDisk(tree, rel string) string { return ospath.Join(tree, rel) }

// Committed はコミットの結果。Sha が空で OK なら、変わったものが無くコミットしていない。Refused は git がコミットを
// 断った理由（refusal.Of）。空でなければ呼ぶ側が前置き（何ができなかったか）を付けて言う。
type Committed struct {
	Sha     string
	Refused string
	Said    Said
	OK      bool
}

// Commit は一覧のパスだけを `commit --only` でコミットする（ほかのステージ済みの変更を巻き込まない）。新しいファイルは
// 先に add する。ユーザの hook は実行せず（--no-verify。コミットするのは状態のファイルだけ）、署名などで尋ねて
// 止まらないよう timeout で切る。失敗したら、この回が add したパスの索引を元に戻す。
func Commit(tree string, paths []string, message string, timeout time.Duration) Committed {
	var c Committed
	base := Head(tree)
	if base == "" {
		c.Said.add("親のワークツリーの先頭を読めない。コミットしない")
		return c
	}
	// 1. 新しいファイルを add し、コミットするパスを集める。作って消した（git の知らない）パスは外す。
	var added, picked []string
	for _, rel := range paths {
		if !usable(rel) {
			continue
		}
		if ospath.DiskLexists(onDisk(tree, rel)) {
			if !tracked(tree, rel) {
				if done := git.Run(tree, []string{"add", "--", literal(rel)}, localOptions()); !done.OK() {
					c.Said.add(rel + " を add できなかった（" + done.FirstLine() + "）")
					c.Said = append(c.Said, Unstage(tree, added, base)...)
					return c
				}
				added = append(added, rel)
			}
		} else if !tracked(tree, rel) {
			continue
		}
		picked = append(picked, rel)
	}
	if len(picked) == 0 {
		c.OK = true
		return c
	}
	// 2. 変わったものが無ければコミットしない。
	specs := make([]string, len(picked))
	for i, rel := range picked {
		specs[i] = literal(rel)
	}
	status := git.Status(tree, git.Porcelain{Untracked: git.UntrackedAll, Paths: specs}, localOptions())
	if !status.OK() {
		c.Said.add("git の状態を読めない（" + status.FirstLine() + "）。コミットしない")
		c.Said = append(c.Said, Unstage(tree, added, base)...)
		return c
	}
	if ospath.Strip(status.Out) == "" {
		c.OK = true
		return c
	}
	// 3. コミットする。パスは NUL 区切りのファイルで渡す（数が多くても引数の長さに収まるように）。
	done := commitWithSpecs(tree, specs, timeout, "commit", "--quiet", "--only", "--no-verify", "-m", message)
	if !done.OK() {
		c.Refused = refusal.Of(failedOutput(done))
		c.Said = append(c.Said, Unstage(tree, added, base)...)
		return c
	}
	c.Sha = Head(tree)
	c.OK = true
	return c
}

// commitWithSpecs は pathspec を一時ファイルで渡して git を起こす。
func commitWithSpecs(tree string, specs []string, timeout time.Duration, args ...string) git.Done {
	f, err := os.CreateTemp("", "ccnavi-wap-pathspec.*")
	if err != nil {
		return git.Done{Code: 1, Stderr: err.Error()}
	}
	defer os.Remove(f.Name())
	_, err = f.WriteString(strings.Join(specs, "\x00") + "\x00")
	if cerr := f.Close(); err == nil {
		err = cerr
	}
	if err != nil {
		return git.Done{Code: 1, Stderr: err.Error()}
	}
	full := append(append([]string{}, args...), "--pathspec-from-file="+f.Name(), "--pathspec-file-nul")
	return git.Run(tree, full, git.Options{Timeout: timeout, RawPaths: true, Graceful: true, CLocale: true})
}

// failedOutput は git が断った出力（標準エラーと標準出力。期限切れなら期限の文）。
func failedOutput(done git.Done) string {
	text := done.Stderr
	if done.Out != "" {
		text += "\n" + done.Out
	}
	if strings.TrimSpace(text) == "" && done.Failure != nil {
		text = done.Failure.Error()
	}
	return text
}

// Unstage は一覧のパスの索引を rev に合わせる（rev に無ければ索引から外す）。戻せなかったパスを文にして返す。
func Unstage(tree string, paths []string, rev string) Said {
	var said Said
	for _, rel := range paths {
		if rel == "" {
			continue
		}
		if git.Run(tree, []string{"restore", "--staged", "--source=" + rev, "--", literal(rel)}, localOptions()).OK() {
			continue
		}
		if git.Run(tree, []string{"rm", "--cached", "--quiet", "--ignore-unmatch", "--", literal(rel)}, localOptions()).OK() {
			continue
		}
		said.add(rel + " の索引を戻せなかった")
	}
	return said
}

// Restore はワークツリーの中身を戻す範囲。Keep はこの実行の前から未コミットだったもの（record-risk の記録など）で触らない。
// Nothing が真なら何も戻さない（approval-request は利用者の書いた提案を一覧に載せるので、戻すと提案が消える）。
type Restore struct {
	Keep    map[string]bool
	Nothing bool
}

// Written は一覧のパスのワークツリーの中身を rev に戻す（rev に無ければ消す）。戻せなかったパスを文にして返す。
func (r Restore) Written(tree string, paths []string, rev string) Said {
	var said Said
	if r.Nothing {
		return said
	}
	for _, rel := range paths {
		if !usable(rel) || r.Keep[rel] {
			continue
		}
		if git.Run(tree, []string{"cat-file", "-e", rev + ":" + rel}, localOptions()).OK() {
			if !git.Run(tree, []string{"restore", "--worktree", "--source=" + rev, "--", literal(rel)}, localOptions()).OK() {
				said.add(rel + " を書く前の状態に戻せなかった")
			}
			continue
		}
		if err := os.Remove(onDisk(tree, rel)); err != nil && !os.IsNotExist(err) {
			said.add(rel + " を削除できませんでした。")
		}
	}
	return said
}

// Undo は自分のコミット commit を比較つきで base に戻す（reset は使わない）。先頭が commit でなくなっていれば戻さない。
// 戻すパスは base からこのコミットまでの変更（合流したコードを含む）と、書いた一覧 written。1 行 1 パスで扱うので、
// 改行を含むパス・大文字小文字だけが違うパス・submodule の追加と削除は、合流の前の検査（Plan）が止める。
func (r Restore) Undo(tree, branch, commit, base string, written []string) (Said, bool) {
	var said Said
	if now := Head(tree); now != commit {
		said.add("先頭が自分のコミット（" + commit + "）でなくなっていた（" + now + "）。戻さずに止めた。ユーザが確かめてください")
		return said, false
	}
	diff := git.Run(tree, []string{"diff", "--name-only", "--no-renames", "-z", base, commit}, localOptions())
	inCommit := names(diff.Out)
	if done := git.Run(tree, []string{"update-ref", "refs/heads/" + branch, base, commit}, localOptions()); !done.OK() {
		said.add("コミットを戻せなかった（" + done.FirstLine() + "）。ユーザが確かめてください")
		return said, false
	}
	said = append(said, Unstage(tree, inCommit, base)...)
	said = append(said, r.Written(tree, inCommit, base)...)
	said = append(said, r.Written(tree, written, base)...)
	return said, true
}

// Branch は親のワークツリーが居るブランチの名前（`refs/heads/` を除く。ブランチの上に居なければ空）。
func Branch(tree string) string {
	done := git.Run(tree, []string{"symbolic-ref", "-q", "HEAD"}, localOptions())
	ref, ok := strings.CutPrefix(ospath.Strip(done.Out), "refs/heads/")
	if !done.OK() || !ok {
		return ""
	}
	return ref
}

// Remote は手元の origin の参照 `refs/remotes/origin/<ブランチ>` の先（無ければ空）。
func Remote(tree, branch string) string { return revOf(tree, "refs/remotes/origin/"+branch) }

// Blobs は rev の、一覧のパスの中身の名前を `<blob> <パス>` で並べる（rev に無いパスは外す）。
func Blobs(tree, rev string, paths []string) []string {
	var out []string
	for _, rel := range paths {
		if sha := revOf(tree, rev+":"+rel); sha != "" {
			out = append(out, sha+" "+rel)
		}
	}
	return out
}

// Unsent はまだ送れていないパス。手元の origin の参照があればそこから先頭までの差、無ければ直近のコミットの分。
func Unsent(tree, branch string) []string {
	args := []string{"show", "--name-only", "--format=", "-z", "HEAD"}
	if Remote(tree, branch) != "" {
		args = []string{"diff", "--name-only", "-z", "refs/remotes/origin/" + branch, "HEAD"}
	}
	return names(git.Run(tree, args, localOptions()).Out)
}

// Contains は commit が先頭の祖先（先頭に含まれる）か。
func Contains(tree, commit string) bool {
	return commit != "" && git.IsAncestor(tree, commit, "HEAD", localOptions()).OK()
}

// Merging は合流（子の finish）の途中の形。Begin が作り、Commit か Abort で終える。
type Merging struct {
	Tree, Base, Sha, Child, Branch string
	// Active は `merge --no-commit` が索引とワークツリーに合流を入れたか（入れるものが無ければ偽）。
	Active bool
}

// Begin は親のワークツリーに子の先頭 sha を入れる（コミットしない）。子の先頭がすでに親に入っていれば何もしない。
// 衝突などで入れられなければ merge を取りやめ、理由を言って偽を返す（何も変えていない）。
func Begin(tree, base, sha, child, branch string) (*Merging, Said, bool) {
	m := &Merging{Tree: tree, Base: base, Sha: sha, Child: child, Branch: branch}
	var said Said
	if git.IsAncestor(tree, sha, "HEAD", localOptions()).OK() {
		return m, said, true
	}
	done := git.Run(tree, []string{"merge", "--no-ff", "--no-commit", "--quiet", sha}, git.Options{Timeout: git.MergeTimeout, RawPaths: true, LazyFetch: true, CLocale: true})
	if done.OK() {
		m.Active = true
		return m, said, true
	}
	conflicts := git.Run(tree, []string{"diff", "--name-only", "-z", "--diff-filter=U"}, localOptions())
	var listed []string
	for _, p := range names(conflicts.Out) {
		// 改行を含むパスは表示のために ? に置き換える。
		listed = append(listed, strings.NewReplacer("\n", "?", "\r", "?").Replace(p))
	}
	git.Run(tree, []string{"merge", "--abort"}, localOptions())
	if Head(tree) != base || Busy(tree) != "" {
		said.add("合流を取りやめても元に戻らなかった。親のワークツリー（" + tree + "）をユーザが確かめてください")
		return m, said, false
	}
	if len(listed) > 0 {
		said.add("子 " + child + " を親のブランチ " + branch + " へ合流すると衝突する（" + strings.Join(listed, ", ") + "）。" +
			"合流を取りやめて、何も変えずに止めた。並行していた子とぶつかったときも、解くのは後から finish した側（この子）です")
		said.add("  子のワークツリー（.claude/worktrees/" + child + "）で親のブランチ " + branch +
			" を merge して衝突を解き、コミットしてから finish をやり直してください")
	} else {
		said.add("子 " + child + " を合流できなかった（" + refusal.Of(failedOutput(done)) + "）。何も変えずに止めた")
	}
	return m, said, false
}

// Abort は合流の途中なら取りやめ、書いたものを戻す。合流の途中でなければ何もしない。
func (m *Merging) Abort(r Restore, written []string) (Said, bool) {
	var said Said
	if m == nil || !m.Active {
		return said, true
	}
	m.Active = false
	switch {
	case revOf(m.Tree, "MERGE_HEAD") != "":
		// 先に merge --abort（索引と合流で入れたものを HEAD に戻す）。書いた状態のファイルは、その後に戻す
		// （先に戻すと、索引に足した分との差が「未 add の変更」に見えて、abort が残してしまう）。
		if done := git.Run(m.Tree, []string{"merge", "--abort"}, localOptions()); !done.OK() {
			said.add("合流を取りやめられなかった（" + done.FirstLine() + "）。親のワークツリー（" + m.Tree + "）をユーザが確かめてください")
			return said, false
		}
	case Head(m.Tree) != m.Base:
		said.add("合流の途中の形でなくなっていた。戻さずに止めた。親のワークツリー（" + m.Tree + "）をユーザが確かめてください")
		return said, false
	case !git.Run(m.Tree, []string{"diff", "--cached", "--quiet"}, localOptions()).OK():
		// merge が MERGE_HEAD を書く前に止まった。索引に残ったものは誰のものか分からないので触らない。
		said.add("合流が途中で切られ、索引に変更が残っている。戻さずに止めた。親のワークツリー（" + m.Tree + "）をユーザが確かめてください")
		return said, false
	}
	return append(said, r.Written(m.Tree, written, m.Base)...), true
}

// Commit は合流の途中のコミット。書いたパスを索引に足して、合流と状態を 1 つのマージコミットにする
// （commit --only は合流の途中では使えない）。失敗したら呼ぶ側が Abort で戻す。
func (m *Merging) Commit(written []string, message string, timeout time.Duration) Committed {
	var c Committed
	for _, rel := range written {
		if !usable(rel) {
			continue
		}
		if ospath.DiskLexists(onDisk(m.Tree, rel)) {
			if done := git.Run(m.Tree, []string{"add", "-A", "--", literal(rel)}, localOptions()); !done.OK() {
				c.Said.add(rel + " を add できなかった（" + done.FirstLine() + "）")
				return c
			}
			continue
		}
		git.Run(m.Tree, []string{"rm", "--cached", "--quiet", "--ignore-unmatch", "--", literal(rel)}, localOptions())
	}
	done := git.Run(m.Tree, []string{"commit", "--quiet", "--no-verify", "-m", message}, git.Options{Timeout: timeout, RawPaths: true, Graceful: true, CLocale: true})
	if !done.OK() {
		c.Refused = refusal.Of(failedOutput(done))
		return c
	}
	m.Active = false
	c.Sha = Head(m.Tree)
	c.OK = true
	return c
}
