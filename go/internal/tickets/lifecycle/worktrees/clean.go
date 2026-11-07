package worktrees

import (
	"fmt"
	"io"
	"os"
	"slices"
	"strings"

	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathmatch"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/tickets/lifecycle/internal/pyshim"
)

// always はどこにあっても生成物と言える名前、beside は隣に印のファイルがあるときだけ生成物と見る名前。
var (
	always = map[string]bool{
		"node_modules": true, ".venv": true, "__pycache__": true, ".pytest_cache": true, ".ruff_cache": true,
		".mypy_cache": true, ".tox": true, ".nox": true, ".next": true, ".nuxt": true, ".turbo": true,
		".parcel-cache": true, ".gradle": true,
	}
	beside = map[string][]string{
		"out":    {"package.json"},
		"dist":   {"package.json"},
		".cache": {"package.json"},
		"target": {"Cargo.toml", "pom.xml"},
		"build":  {"build.gradle", "build.gradle.kts"},
	}
)

// cleanShownStatus は未コミットで止めたときに見せる git status の行の上限。
const cleanShownStatus = 10

func worktreePath(root, name string) string { return tree.WorktreePath(root, name) }

// isGenerated はワークツリーのルートからの相対 rel が、生成物（とその中）か。
func isGenerated(top, rel string) bool {
	parts := relParts(rel)
	for i, part := range parts {
		if always[part] {
			return true
		}
		for _, mark := range beside[part] {
			if ospath.IsFile(ospath.Join(top, append(slices.Clone(parts[:i]), mark)...)) {
				return true
			}
		}
	}
	return false
}

// generated は消す生成物（ワークツリーのルートからの相対。区切りは "/"）。リンクはたどらない。
func generated(top string) []string {
	var found []string
	stack := []string{top}
	for len(stack) > 0 {
		here := stack[len(stack)-1]
		stack = stack[:len(stack)-1]
		entries, err := os.ReadDir(here)
		if err != nil {
			continue
		}
		plain := map[string]bool{}
		for _, e := range entries {
			if e.Type().IsRegular() {
				plain[e.Name()] = true
			}
		}
		for _, e := range entries {
			full := ospath.Join(here, e.Name())
			linked := pathmatch.IsLink(full)
			marked := always[e.Name()]
			for _, mark := range beside[e.Name()] {
				marked = marked || plain[mark]
			}
			if marked {
				if linked || e.IsDir() {
					rel, _ := ospath.Relpath(full, top)
					found = append(found, ospath.Slashed(rel))
				}
				continue
			}
			if e.Name() == ".git" || linked {
				continue
			}
			if e.IsDir() {
				stack = append(stack, full)
			}
		}
	}
	slices.Sort(found)
	return found
}

// targets は消す生成物と、読めなかった理由。追跡されている生成物の名前（コミット済みの out/ など）は消さない
// （消すと未コミットの削除になり worktree remove が通らない）。checkTracked が偽なのは git の登録が残って
// いないディレクトリ（聞くと上へ登って別のリポジトリを答える）。
func targets(top string, checkTracked bool) ([]string, string) {
	var out []string
	for _, rel := range generated(top) {
		if checkTracked {
			done := git.Run(top, []string{"ls-files", "-z", "--", ":(literal)" + rel}, git.Options{Timeout: timeout, RawPaths: true})
			if !done.OK() {
				return nil, rel + " が追跡されているかを読めない。何も削除していません。"
			}
			if strings.Trim(done.Out, "\x00") != "" {
				continue
			}
		}
		out = append(out, rel)
	}
	return out, ""
}

// remove は targets を消す。答えは（消せたもの, 消せなかったもの（理由つき））。リンクはリンクだけを消す。
func remove(top string, targets []string) ([]string, []string) {
	var removed, left []string
	for _, rel := range targets {
		full := ospath.Join(top, strings.Split(rel, "/")...)
		var err error
		if pathmatch.IsLink(full) {
			err = os.Remove(full)
		} else {
			err = os.RemoveAll(full)
		}
		if err != nil {
			left = append(left, rel+"（"+pyshim.StrError(err)+"）")
			continue
		}
		removed = append(removed, rel)
	}
	return removed, left
}

func leftMessage(left []string) string {
	if len(left) == 0 {
		return ""
	}
	return "生成物を削除しきれませんでした（" + strings.Join(left, "、") +
		"）。Windows では、読み込まれている DLL（uv の .venv の .pyd）は削除できません。" +
		"使っているプロセスが終わってから再実行します。"
}

// cleanGenerated は生成物を消す。消すものを先に決める（途中で止まって半端に消さない）。消し残しがあれば理由。
func cleanGenerated(top string) string {
	found, why := targets(top, true)
	if why != "" {
		return why
	}
	_, left := remove(top, found)
	return leftMessage(left)
}

// unregistered は git の登録が残っていない（消しきれなかった）ディレクトリか。`.git` が無いか、`.git` の
// ファイルの gitdir の先が消えている。聞くと git は上へ登って別のリポジトリを答えるので、この形のときだけ
// git に聞かずに進める。
func unregistered(top string) bool {
	gitPath := ospath.Join(top, ".git")
	if !ospath.DiskLexists(gitPath) {
		return true
	}
	if !ospath.IsFile(gitPath) {
		return false
	}
	text, err := ospath.ReadDiskText(gitPath)
	if err != nil {
		return false
	}
	first := text
	if i := strings.IndexByte(first, '\n'); i >= 0 {
		first = first[:i]
	}
	first = ospath.Strip(first)
	target, ok := strings.CutPrefix(first, "gitdir:")
	if !ok {
		return false
	}
	target = ospath.Strip(target)
	if !ospath.IsAbs(target) {
		target = ospath.Join(top, target)
	}
	return !ospath.IsDir(target)
}

// RunClean は `worktree clean <名前> [--preview]`。生成物だけを消す（ワークツリーは消さない）。未コミットの変更が
// あれば何も消さない。git の登録が残っていないディレクトリは確かめようが無いので確かめずに進め、git の状態を
// 読めないときは登録なしと見ずに止める。成功で 0、止めたら 1。
func RunClean(stdout, stderr io.Writer, root, name string, preview bool) int {
	top := worktreePath(root, name)
	if badName(name) {
		fmt.Fprint(stderr, "ccnavi-clean: 名前にパスは書けません（.claude/worktrees/ の直下の名前）。\n")
		return 1
	}
	if pathmatch.IsLink(top) || !ospath.IsDir(top) {
		fmt.Fprintf(stderr, "ccnavi-clean: %s がディレクトリでないか、リンクです。\n", top)
		return 1
	}
	registered := !unregistered(top)
	if registered {
		out, ok := status(top)
		if !ok {
			fmt.Fprintf(stderr, "ccnavi-clean: %s の git の状態を読めないので、何も消しません（未コミットの変更を確かめられない）。\n", name)
			return 1
		}
		if ospath.Strip(out) != "" {
			fmt.Fprintf(stderr, "ccnavi-clean: %s に未コミットの変更があるので、何も消しません。別のセッションが作業している可能性が高いです。\n", name)
			lines := ospath.Splitlines(out)
			for _, line := range lines[:min(len(lines), cleanShownStatus)] {
				fmt.Fprintf(stderr, "%s\n", line)
			}
			return 1
		}
	} else {
		fmt.Fprintf(stderr, "ccnavi-clean: %s は git の登録が残っていないので、削除しきれなかったディレクトリとして扱います（未コミットの変更は確かめていません）。\n", name)
	}
	found, why := targets(top, registered)
	if why != "" {
		fmt.Fprintf(stderr, "ccnavi-clean: %s\n", why)
		return 1
	}
	if len(found) == 0 {
		fmt.Fprintf(stdout, "削除するものはありません（%s）\n", top)
		return 0
	}
	if preview {
		for _, rel := range found {
			fmt.Fprintf(stdout, "would remove %s\n", rel)
		}
		return 0
	}
	removed, left := remove(top, found)
	for _, rel := range removed {
		fmt.Fprintf(stdout, "removed %s\n", rel)
	}
	if len(left) > 0 {
		fmt.Fprintf(stderr, "ccnavi-clean: %s\n", leftMessage(left))
		return 1
	}
	return 0
}
