package request

import (
	"fmt"
	"os"
	"slices"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/pathmatch"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/tickets/lifecycle/treegate"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/sync/handoff"
)

// ready の前に wip/ を片付けるときの文。
const (
	msgNoTree          = "親のワークツリーが無い (%s)"
	msgTrackedUnread   = "`%s/` が追跡されているかを読めない"
	msgUntrackedUnread = "`%s/` に未追跡のファイルがあるかを読めない"
	msgOddTracked      = "`%s/` の追跡済みのファイルのうち、途中のディレクトリがリンクでツリーの外を指すか、名前に改行を含むものがある（%s）。" +
		"削除するとツリーの外のファイルまで削除するおそれがあるので止めた。リンクを元のディレクトリに戻すか、ユーザに確かめてから再実行してください"
	msgUntracked = "`%[1]s/` に未追跡のファイルが %[2]d 件ある（%[3]s）。ready が削除するのは `%[1]s/` の追跡済みのファイルだけ（履歴から戻せるもの）で、" +
		"未追跡のものは戻せないので削除せずに中止しました。必要なものは別の場所へ移し、要らないものはユーザに確かめて削除してから再実行してください"
	msgPendingDrafts = "`%[1]s/` に取り込んでいない保護対象の下書きが %[2]d 件ある（%[3]s）。ready は `%[4]s/` を削除するので中止しました。ユーザに" +
		"`ccnavi-user-handoff.sh --root %[5]s` で取り込んでもらうか、要らない下書きを削除してから再実行してください"
	msgOddRemoved = "途中のディレクトリがリンクでツリーの外を指すか、名前に改行を含むので、何も削除しませんでした（%s）。" +
		"リンクを元のディレクトリに戻すか、ユーザに確かめてから再実行してください"
	msgUnlinkFailed = "%s を削除できません（%s）"
	namedLimit      = 10
)

// WipTracked は途中の作業の置き場（wip）の下で追跡されているファイル（git の表記）。読めなければ ok が偽。
// 大文字小文字を区別せずに見る（区別しない FS で `WIP/` を先に作ると、範囲の判定は通すのに git には `WIP/…` で
// 入る）。名前に `\` を含む 1 ファイル（`wip\x`）も下と見なす。pathspec に頼らず -z で全部を読んで絞る。
func WipTracked(treeRoot string) ([]string, bool) {
	return wipFiles(treeRoot, "ls-files", "-z")
}

func wipUntracked(treeRoot string) ([]string, bool) {
	return wipFiles(treeRoot, "ls-files", "-z", "--others", "--exclude-standard")
}

func wipFiles(treeRoot string, args ...string) ([]string, bool) {
	code, out := git.Output(treeRoot, args, git.Options{Timeout: Timeout})
	if code != 0 {
		return nil, false
	}
	var found []string
	for _, p := range strings.Split(out, "\x00") {
		if p != "" && place.InWip(p) {
			found = append(found, p)
		}
	}
	return found, true
}

// odd は消すとツリーの外へ出るか、名前に改行を含む（sh へ渡す 1 行 1 本の答えを壊す）もの。
func odd(treeRoot string, names []string) []string {
	var out []string
	for _, rel := range names {
		if strings.ContainsAny(rel, "\n\r") || escapes(treeRoot, rel) {
			out = append(out, rel)
		}
	}
	return out
}

// named は名指しの並び（先頭の 10 本。改行を含む名前は repr で見せる）。
func named(names []string) string {
	shown := make([]string, 0, namedLimit)
	for _, r := range names[:min(len(names), namedLimit)] {
		if strings.ContainsAny(r, "\n\r") {
			r = pyfmt.Repr(r)
		}
		shown = append(shown, r)
	}
	return strings.Join(shown, "、")
}

// counted は先頭の 10 本と、残りの件数。
func counted(names []string) string {
	text := strings.Join(names[:min(len(names), namedLimit)], "、")
	if len(names) > namedLimit {
		text += fmt.Sprintf(" ほか %d 件", len(names)-namedLimit)
	}
	return text
}

// MergeProblems はマージに進む前にワークツリーの側で満たしていること（ready の前提）。root は文面の sh のパスに使う。
// 未コミットが無いこと、push 済みであること（ユーザがマージで見るのはリモートの HEAD）。wip/ の追跡済みの
// ファイルは ready が消してコミットに含めるので止めない。未追跡のファイルと、取り込んでいない保護対象の
// 下書きは消すと戻せないので、名指しして止める。
func MergeProblems(conf *config.Settings, root, treeRoot string) []string {
	if !ospath.IsDir(treeRoot) {
		return []string{fmt.Sprintf(msgNoTree, treeRoot)}
	}
	wip := place.WipRoot
	var problems []string
	if tracked, ok := WipTracked(treeRoot); !ok {
		problems = append(problems, fmt.Sprintf(msgTrackedUnread, wip))
	} else if bad := odd(treeRoot, tracked); len(bad) > 0 {
		// 退避より前に止める（RemoveWip も同じ確かめをするが、そこでは退避が済んでいる）。
		problems = append(problems, fmt.Sprintf(msgOddTracked, wip, named(bad)))
	}
	untracked, ok := wipUntracked(treeRoot)
	if !ok {
		problems = append(problems, fmt.Sprintf(msgUntrackedUnread, wip))
	} else if len(untracked) > 0 {
		problems = append(problems, fmt.Sprintf(msgUntracked, wip, len(untracked), counted(untracked)))
	}
	told := map[string]bool{}
	for _, p := range untracked {
		told[pathcase.Fold(p)] = true
	}
	var waiting []string
	for _, p := range handoff.Pending(treeRoot) {
		if !told[pathcase.Fold(p)] {
			waiting = append(waiting, p)
		}
	}
	if len(waiting) > 0 {
		problems = append(problems, fmt.Sprintf(msgPendingDrafts, place.Handoff, len(waiting), counted(waiting), wip, shlexQuote(treeRoot)))
	}
	for _, r := range treegate.Check(treegate.Ready, treeRoot, conf, treegate.Options{}) {
		problems = append(problems, r.Line())
	}
	return problems
}

// RemoveWip は wip の下の追跡済みのファイルを消す（ready が退避と同じ流れで使う）。返すのは消したパス、消す前の
// HEAD（消したものを履歴から戻すときの版）、止まった理由。消すのは fsio を通すので write-and-push がコミットする。
// 1 本でもツリーの外へ出るものがあれば何も消さない（途中のディレクトリがリンクに置き換わっていると、リンクの
// 先を消す）。空になったディレクトリは下から消す（git は空のディレクトリを持たない）。
func RemoveWip(treeRoot string) ([]string, string, string) {
	names, ok := WipTracked(treeRoot)
	if !ok {
		return nil, "", fmt.Sprintf(msgTrackedUnread, place.WipRoot)
	}
	if len(names) == 0 {
		return nil, "", ""
	}
	if bad := odd(treeRoot, names); len(bad) > 0 {
		return nil, "", fmt.Sprintf(msgOddRemoved, named(bad))
	}
	head := ""
	if code, out := git.Output(treeRoot, []string{"rev-parse", "HEAD"}, git.Options{Timeout: Timeout}); code == 0 {
		head = strings.TrimSpace(out)
	}
	var removed []string
	for _, rel := range names {
		full := ospath.Join(treeRoot, strings.Split(rel, "/")...)
		if !ospath.DiskLexists(full) {
			// ワークツリーから先に消えていた（削除が未コミット）。消したものとして write-and-push のコミットに含める。
			fsio.NoteRemoved(full)
			removed = append(removed, rel)
			continue
		}
		if failed := fsio.Unlink(full); failed != "" {
			return removed, head, fmt.Sprintf(msgUnlinkFailed, rel, failed)
		}
		removed = append(removed, rel)
	}
	tops := map[string]bool{}
	for _, p := range names {
		top, _, _ := strings.Cut(p, "/")
		tops[top] = true
	}
	ordered := make([]string, 0, len(tops))
	for top := range tops {
		ordered = append(ordered, top)
	}
	slices.Sort(ordered)
	for _, top := range ordered {
		pruneEmpty(ospath.Join(treeRoot, top))
	}
	return removed, head, ""
}

// escapes は rel（git の表記）の途中のディレクトリがリンクか、親ディレクトリを解くとツリーの外か。最後の名前が
// リンクなのは構わない（消すのはリンクだけ）。
func escapes(treeRoot, rel string) bool {
	parts := strings.Split(rel, "/")
	if _, linked := pathmatch.FirstLink(treeRoot, parts, false); linked {
		return true
	}
	return pathmatch.Escapes(treeRoot, ospath.Dirname(ospath.Join(treeRoot, parts...)))
}

// pruneEmpty は空になったディレクトリを下から消す。リンクはたどらない。
func pruneEmpty(directory string) {
	if ospath.IsLink(directory) || !ospath.IsDir(directory) {
		return
	}
	var dirs []string
	var walk func(string)
	walk = func(dir string) {
		dirs = append(dirs, dir)
		entries, err := os.ReadDir(dir)
		if err != nil {
			return
		}
		for _, e := range entries {
			if e.IsDir() {
				walk(ospath.Join(dir, e.Name()))
			}
		}
	}
	walk(directory)
	// 深いものから（Python は os.walk の答えをパスの長さの降順に並べる）。
	slices.SortStableFunc(dirs, func(a, b string) int { return len(b) - len(a) })
	for _, d := range dirs {
		_ = os.Remove(d)
	}
}

// shlexQuote は Python の shlex.quote（案内に載せるパス）。
func shlexQuote(s string) string {
	if s == "" {
		return "''"
	}
	for i := 0; i < len(s); i++ {
		c := s[i]
		safe := c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || strings.IndexByte("_@%+=:,./-", c) >= 0
		if !safe {
			return "'" + strings.ReplaceAll(s, "'", `'"'"'`) + "'"
		}
	}
	return s
}
