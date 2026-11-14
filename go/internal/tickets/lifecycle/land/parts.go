package land

import (
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strings"

	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/policy/ruleload"
	"ccnavi/internal/policy/selfguard"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/tickets/lifecycle/worktrees"
	"ccnavi/internal/tickets/sync/handoff"
)

// removeQuiet は、消せなくても処理を止めない削除（ディレクトリは空のときだけ消える）。
func removeQuiet(path string) { _ = os.Remove(path) }

// tmpUnder は、一時ワークツリーの置き場（logs/direct-tmp/）。
func (r *run) tmpUnder() string {
	return ospath.Join(r.Root, "logs", "direct-tmp")
}

// removeTmp は一時ワークツリーを片付ける。生成物を消したあと、索引の木が許す木（allow）のどれかと同じで、追跡ファイルに
// それ以上の変更が無く、未追跡のファイル（無視されたものを含む）も残っていないときに限り `worktree remove --force` を打つ。
// 満たさなければ削除せずに残し、理由を tmpWhy に入れて偽。
func (r *run) removeTmp(allow ...string) bool {
	r.tmpWhy = ""
	if !exists(r.tmp) {
		// 登録だけが残っているときの後始末。prune は「ディレクトリが無くなった登録」だけを外す（名前では絞れない）。
		r.ok("worktree", "prune")
		return true
	}
	if _, under := pathcase.Under(r.tmp, r.tmpUnder()); !under {
		r.tmpWhy = "場所（" + r.tmp + "）が一時ワークツリーの置き場の外です"
		return false
	}
	if ospath.IsLink(r.tmp) {
		r.tmpWhy = "一時ワークツリー（" + r.tmp + "）がリンクです"
		return false
	}
	if why := worktrees.CleanGenerated(r.tmp); why != "" {
		r.tmpWhy = why
		return false
	}
	index, _ := r.git(r.tmp, "write-tree")
	if index == "" || !slices.Contains(allow, index) {
		r.tmpWhy = "index の木が取り込み結果（C か K）と違います"
		return false
	}
	if _, clean := r.git(r.tmp, "diff", "--quiet"); !clean {
		r.tmpWhy = "追跡ファイルに、取り込み結果以外の変更があります"
		return false
	}
	var rest []string
	for _, args := range [][]string{{"ls-files", "--others", "--exclude-standard"}, {"ls-files", "--others", "--ignored", "--exclude-standard"}} {
		out, _ := r.git(r.tmp, args...)
		lines := ospath.Splitlines(out)
		if len(lines) > 5 {
			lines = lines[:5]
		}
		rest = append(rest, lines...)
	}
	if len(rest) > 0 {
		r.tmpWhy = "未追跡のファイルが残っています（" + strings.Join(rest, " ") + "）"
		return false
	}
	if done := r.gitDone(r.Cwd, "", "worktree", "remove", "--force", r.tmp); !done.OK() {
		first, _, _ := strings.Cut(strings.TrimSpace(done.Stderr+done.Out), "\n")
		r.tmpWhy = "worktree remove が通りませんでした（" + first + "）"
		return false
	}
	return true
}

// tmpNote は一時ワークツリーを消せなかったときの案内。
func (r *run) tmpNote() {
	r.print(texts.Say(r.Root, "NOTE_GIT_TMP_LEFT", texts.Values{"tmp": r.tmp, "why": r.tmpWhy}, nil))
}

// dropRefs は専用の ref を消す。全部を消そうとしてから、消せなかったものがあれば偽を返し、その種類を refsFailed に入れる。
// すでに無い ref は消せたものと同じ。
func (r *run) dropRefs(kinds ...string) bool {
	var failed []string
	for _, kind := range kinds {
		if !r.ok("update-ref", "-d", r.ref+"/"+kind) {
			failed = append(failed, kind)
		}
	}
	r.refsFailed = strings.Join(failed, " ")
	return len(failed) == 0
}

// drafts は下書きの条件を作業ワークツリーについて確かめる。満たさなければ理由を draftWhy に入れて偽。
//
//   - 許可リストにヒットする下書きは、取り込み済み（取り込んでいない下書きが 0 件）
//   - wip/handoff/ に今あるファイルのうち、元（wip/handoff/ を除いたパスの start の版）があって中身が違うものが無い。
//     元の無い下書き（新規のメモ・報告）は通し、draftNotes に集める
//   - 元の無い下書きでも、まだコミットしていないものは通さない（ワークツリーを消すと失われるため）
func (r *run) drafts(start string) bool {
	r.draftWhy, r.draftNotes = "", ""
	root := ospath.Abspath(r.wt)
	foreign, notes, ok := handoff.Survey(root, start)
	if !ospath.IsDir(root) || !ok {
		r.draftWhy = "下書きの取り込み漏れを実行ファイルに確かめられない"
		return false
	}
	if left := handoff.Pending(root); len(left) > 0 {
		r.draftWhy = "取り込んでいない下書きがある（" + strings.Join(left, " ") + " ）"
		return false
	}
	var kept []string
	for _, note := range notes {
		if r.ok("-C", r.wt, "--literal-pathspecs", "ls-files", "--error-unmatch", "--", note) {
			kept = append(kept, note)
		} else {
			foreign = append(foreign, note)
		}
	}
	r.draftNotes = strings.Join(kept, " ")
	if len(foreign) > 0 {
		r.draftWhy = "wip/handoff/ に、元と違う写し、またはまだコミットしていないファイルがある（" + strings.Join(foreign, " ") + "）"
		return false
	}
	return true
}

// handoffDir は下書きの置き場（木の中のパスの頭）。
const handoffDir = "wip/handoff/"

// restorePath は、一時の索引 index の path を base の版に戻す（base に無ければ索引から外す）。
func (r *run) restorePath(index, base, path string) bool {
	entry, _ := r.git(r.Cwd, "--literal-pathspecs", "ls-tree", base, "--", path)
	if fields := strings.Fields(entry); len(fields) >= 3 {
		return r.gitDone(r.Cwd, index, "update-index", "--add", "--cacheinfo", fields[0]+","+fields[2]+","+path).OK()
	}
	return r.gitDone(r.Cwd, index, "update-index", "--force-remove", "--", path).OK()
}

// treeEntry は木 rev の path の「モード sha」。無ければ空。
func (r *run) treeEntry(rev, path string) string {
	entry, _ := r.git(r.Cwd, "--literal-pathspecs", "ls-tree", rev, "--", path)
	if fields := strings.Fields(entry); len(fields) >= 3 {
		return fields[0] + " " + fields[2]
	}
	return ""
}

// makeT は、作業ブランチの先頭（tip）の木から wip/handoff/ の差分を base に戻した木 T を作る。snap と snap0 を渡すと
// （作業ブランチが取り込み先を取り込んでいて、base が S ではなく共通祖先のとき）、コピーした書きかけ（snap0..snap の差分）
// のうち、エージェントが snap から変えていないパスも base の版に戻す（コピーした書きかけそのものは成果に含めない）。
func (r *run) makeT(base, tip, snap, snap0 string) string {
	dir, err := os.MkdirTemp("", "ccnavi-idx")
	if err != nil {
		return ""
	}
	defer os.RemoveAll(dir)
	index := filepath.Join(dir, "index")
	if !r.gitDone(r.Cwd, index, "read-tree", tip).OK() {
		return ""
	}
	for _, p := range r.names(base, tip, "--", handoffDir) {
		if strings.HasPrefix(p, handoffDir) && !r.restorePath(index, base, p) {
			return ""
		}
	}
	if snap != "" && snap0 != "" {
		for _, p := range r.names(snap0, snap) {
			if strings.HasPrefix(p, handoffDir) || r.treeEntry(tip, p) != r.treeEntry(snap, p) {
				continue
			}
			if !r.restorePath(index, base, p) {
				return ""
			}
		}
	}
	done := r.gitDone(r.Cwd, index, "write-tree")
	if !done.OK() {
		return ""
	}
	return strings.TrimSpace(done.Out)
}

// guarded は、パスの並び（トップからの相対）のうち自己防衛の対象に当たるもの。
func (r *run) guarded(rels []string) (hits []string) {
	guarded := map[string]bool{}
	for _, t := range selfguard.Targets(r.Root, r.Conf.Rules, r.Conf.Bin, ruleload.LayerFiles(r.Conf, r.Root), r.Conf.Projects) {
		guarded[pathcase.Fold(ospath.Realpath(t.Path))] = true
	}
	for _, rel := range rels {
		if rel != "" && guarded[pathcase.Fold(ospath.Realpath(ospath.Join(r.top, filepath.FromSlash(rel))))] {
			hits = append(hits, rel)
		}
	}
	return hits
}

// overwritten は、git が「書きかけを上書きする」と断ったときに並べるパスの行。
var overwritten = regexp.MustCompile(`would be overwritten`)

// refusal は、コミットや fast-forward を git が断った理由の 1 文（sh の ccnavi_git_refusal と同じ読み方）。
func refusal(out string) string {
	lines := ospath.Splitlines(out)
	first := ""
	if len(lines) > 0 {
		first = lines[0]
	}
	var paths []string
	in := false
	for _, line := range lines {
		switch {
		case overwritten.MatchString(line):
			in = true
			continue
		case line != "" && line[0] != ' ' && line[0] != '\t':
			in = false
		case in && strings.TrimSpace(line) != "":
			paths = append(paths, strings.TrimLeft(line, " \t"))
		}
	}
	low := strings.ToLower(out)
	switch {
	case len(paths) > 0:
		return "書きかけの " + strings.Join(paths, " ") + " と重なる。コミットか退避をしてから再実行してください"
	case strings.Contains(out, "index.lock"):
		return "索引のロック（index.lock）が残っている。別の git が動いていないか確かめ、異常終了して残ったものならユーザが削除します。"
	case strings.Contains(low, "tell me who you are") || strings.Contains(low, "empty ident") ||
		strings.Contains(low, "user.email") || strings.Contains(low, "user.name"):
		return "コミットするユーザの名前（user.name・user.email）が決まっていない"
	case strings.Contains(low, "gpg") || strings.Contains(low, "signing"):
		return "コミットの署名に失敗した"
	case strings.Contains(low, "hook"):
		return "git の hook が止めた（" + first + "）"
	}
	return "git が拒んだ（" + first + "）"
}
