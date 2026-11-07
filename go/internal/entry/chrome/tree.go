package chrome

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"sync"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/pyre"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/tickets/core"
	"ccnavi/internal/tickets/model/ids"
	"ccnavi/internal/tickets/model/ticket"
)

const headPrefix = "ref: refs/heads/"

// build は統合先をワークスペースルートに、親子のチケットをワークツリーに置いた仮のツリーを組む。プロジェクトの
// リポジトリは、ワークスペースの統合先をワークスペースルートに、プロジェクトの統合先（done/ とプロジェクトの
// レイヤー）を projects/<名前>/ に置き、親子のチケットをそのプロジェクトのワークツリーにする。HEAD はブランチの
// 名前を読む先（承認の記録の source_tree はブランチ名）。
func (s *snapshot) build(root string, families []string) error {
	// 前の要求の仮のツリーを消して、組み直す。
	os.RemoveAll(root)
	if err := os.MkdirAll(ospath.Join(root, tree.GitWorktrees), 0o755); err != nil {
		return err
	}
	// keep はブランチの中身のうち、dirs の下と files に当たるパスだけを base の下に書く。
	// owner は、ワークツリーの登録（.git/worktrees）を置くリポジトリのルート。
	owner := root
	keep := func(b *branch, base string, dirs, files []string) error {
		for _, path := range b.paths {
			if hasDirPrefix(path, dirs) || containsPath(files, path) {
				if err := write(base, path, b.files[path]); err != nil {
					return err
				}
			}
		}
		return nil
	}
	// 1. ワークスペースルートとプロジェクトに、統合先の中身（done/ とレイヤー）を置く。
	integ := s.branch(s.integration)
	stamped := []string{settingsFile, config.CompatFile}
	if s.project == "" {
		if err := head(ospath.Join(root, tree.DotGit), s.integration); err != nil {
			return err
		}
		if err := keep(integ, root, sortedSet(doneDir, commonLayer, ownLayer, messagesDir), stamped); err != nil {
			return err
		}
	} else {
		if err := head(ospath.Join(root, tree.DotGit), s.wsName); err != nil {
			return err
		}
		if err := keep(s.workspace, root, sortedSet(commonLayer, ownLayer, messagesDir), stamped); err != nil {
			return err
		}
		owner = ospath.Join(root, config.DefaultProjects, s.project)
		if err := os.MkdirAll(ospath.Join(owner, tree.GitWorktrees), 0o755); err != nil {
			return err
		}
		if err := head(ospath.Join(owner, tree.DotGit), s.integration); err != nil {
			return err
		}
		// プロジェクトのレイヤーは統合先の .ccnavi/config/project/ をそのまま写す。共通レイヤーはワークスペースの
		// 統合先のものだけで、プロジェクトの .ccnavi/config/common/（同期が配るミラー）は読まない。ミラーを共通として
		// 読むと、ずれている間ワークスペースと違う判定になる。P の上のレイヤーも読まない（P の上で書き換えて承認や
		// レビューを外せないように）。
		if err := keep(integ, owner, []string{doneDir, ownLayer}, nil); err != nil {
			return err
		}
	}
	// 2. 親子のチケットのブランチごとに、ワークツリーを作る。ブランチの中身を全部書き、HEAD と登録をそろえる
	// （ワークツリーかどうかの判定が .git ファイルと登録の gitdir の往復を見るため）。
	for _, name := range families {
		if !s.has(name) || name == s.integration {
			continue
		}
		ident, err := s.treeIdent(name)
		if err != nil {
			return err
		}
		if err := validFamilyName(name, ident); err != nil {
			return err
		}
		work := ospath.Join(root, filepath.FromSlash(worktreesDir), ident)
		registry := ospath.Join(owner, tree.GitWorktrees, ident)
		if err := os.MkdirAll(work, 0o755); err != nil {
			return err
		}
		if err := os.MkdirAll(registry, 0o755); err != nil {
			return err
		}
		gitfile := ospath.Join(work, tree.DotGit)
		if err := os.WriteFile(gitfile, []byte("gitdir: "+registry+"\n"), 0o644); err != nil {
			return err
		}
		if err := os.WriteFile(ospath.Join(registry, "gitdir"), []byte(gitfile+"\n"), 0o644); err != nil {
			return err
		}
		if err := head(registry, name); err != nil {
			return err
		}
		b := s.branch(name)
		for _, path := range b.paths {
			if err := write(work, path, b.files[path]); err != nil {
				return err
			}
		}
	}
	return nil
}

// hasDirPrefix は path が dirs のどれかの下にあるか。
func hasDirPrefix(path string, dirs []string) bool {
	for _, d := range dirs {
		if strings.HasPrefix(path, d+"/") {
			return true
		}
	}
	return false
}

// containsPath は path が paths に含まれるか。
func containsPath(paths []string, path string) bool {
	for _, p := range paths {
		if p == path {
			return true
		}
	}
	return false
}

// head は gitdir の HEAD に、ブランチ名を読む先を書く。
func head(gitdir, branch string) error {
	return os.WriteFile(ospath.Join(gitdir, "HEAD"), []byte(headPrefix+branch+"\n"), 0o644)
}

// write は rel を base の下に書く。rel が base の外へ出る形は、書く前に断る。
func write(base, rel, text string) error {
	if err := checkRel(rel); err != nil {
		return err
	}
	full := ospath.Join(base, filepath.FromSlash(rel))
	if err := os.MkdirAll(ospath.Dirname(full), 0o755); err != nil {
		return err
	}
	return os.WriteFile(full, []byte(text), 0o644)
}

// preview は仮のツリーで `--agree --preview --json [<絞り>...]` を出す。答えは終了コード・本文（出せなければ nil）・
// 標準エラー。
func preview(root string, only []string) (int, *pyjson.Object, string) {
	conf, _ := config.Load(root)
	var out, errOut bytes.Buffer
	code := config.ExitOK
	if core.Preview(&out, &errOut, conf, root, true, only) != 0 {
		code = config.ExitError
	}
	if code != config.ExitOK {
		return code, nil, errOut.String()
	}
	// core.Preview が書いた JSON を読み戻して、拡張へ返すオブジェクトにする。
	value, err := pyjson.Loads(out.String())
	body, ok := value.(*pyjson.Object)
	if err != nil || !ok {
		return code, nil, errOut.String()
	}
	return code, body, errOut.String()
}

// entry は一覧の 1 行に、ブランチの上の相対パスと本文（Markdown）を添える。
func entry(root string, e *pyjson.Object) *pyjson.Object {
	path, _ := e.Value("path").(string)
	body := ""
	if path != "" {
		if t, _ := ticket.Load(path); t != nil {
			body = t.Body
		}
	}
	out := pyjson.NewObject()
	for _, k := range e.Keys() {
		if k != "path" {
			out.Set(k, e.Value(k))
		}
	}
	out.Set("path", relative(root, path))
	out.Set("body", body)
	return out
}

// beforeRoot は仮のツリーのパスの前に来てよい字（行の頭・空白・引用符・括弧・区切り）。パスの途中の同じ表記
// （`wip/ws/x`）は畳まない。
const beforeRoot = `(?<![^\s'"` + "`" + `(（「:：,、=])`

// relativeForms は root ごとにコンパイルした式の置き場。relative は答えの文面ごとに呼ぶので、使い回す。
var relativeForms sync.Map // root → [2]*pyre.Regexp

func relativeRes(root string) [2]*pyre.Regexp {
	if v, ok := relativeForms.Load(root); ok {
		return v.([2]*pyre.Regexp)
	}
	base := pyre.Escape(ospath.Join(root, filepath.FromSlash(worktreesDir)) + ospath.Sep)
	res := [2]*pyre.Regexp{
		pyre.MustCompile(beforeRoot+base+`(?P<id>[A-Za-z0-9][`+ids.IDChars+`]*)`+pyre.Escape(ospath.Sep), 0),
		pyre.MustCompile(beforeRoot+pyre.Escape(root+ospath.Sep), 0),
	}
	relativeForms.Store(root, res)
	return res
}

// relative は文面の中の仮のツリーのパスを畳む。`<root>/.claude/worktrees/<P>/` を `<P>:` に、`<root>/` を空に
// する。書くもの（Changes）のパスは既にツリーからの相対なので、ここを通さない。
func relative(root, text string) string {
	res := relativeRes(root)
	out := res[0].Sub(text, func(f pyre.Found) string {
		id, _ := f.Named("id")
		return id + ":"
	})
	out = res[1].Sub(out, func(pyre.Found) string { return "" })
	if ospath.Sep != "/" {
		out = strings.ReplaceAll(out, ospath.Sep, "/")
	}
	return out
}

// relativeAll は relative を行ごとに当てる。
func relativeAll(root string, lines []string) []string {
	out := make([]string, len(lines))
	for i, line := range lines {
		out[i] = relative(root, line)
	}
	return out
}
