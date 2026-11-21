package ticketkit

import (
	"path/filepath"
	"strings"

	"ccnavi/internal/harness/samples"
)

// PhaseFamily は、フェーズ定義の見本（samples.Phases）を持つ木（Options{Phases: samples.Phases}）で、親を提案して承認する。
// 承認するのは親だけ（子は RunChild が提案・承認する）。親の本文は samples.ParentText（plan が使う定義のコピーを持つ）。
func (tr *Tree) PhaseFamily(plan []samples.Step) {
	tr.T.Helper()
	tr.Propose(ParentName, samples.ParentText(ParentName, plan, samples.ParentOpts{Copy: tr.Phases}), "")
	tr.CommitParent("tickets")
	OK(tr.T, tr.Approve())
}

// RunChild は親を着手し、子のワークツリーを作って着手し、ファイル（パス → 中身）を置いてコミットする。子のツリーを返す。
func (tr *Tree) RunChild(name string, files ...File) string {
	tr.T.Helper()
	tr.StartParent()
	tree := tr.Worktree(name, ParentName)
	OK(tr.T, tr.Ccnavi("ticket", "start", name))
	for _, f := range files {
		Write(tr.T, filepath.Join(tree, filepath.FromSlash(f.Path)), f.Text)
	}
	Git(tr.T, tree, "add", "-A")
	if len(files) > 0 {
		Git(tr.T, tree, "commit", "--quiet", "-m", name)
	}
	return tree
}

// File は RunChild が子のツリーに置くファイル。
type File struct{ Path, Text string }

// PushOrigin は origin（bare）を作って親のブランチを push する。
func (tr *Tree) PushOrigin() {
	tr.T.Helper()
	bare := filepath.Join(tr.Root, "origin.git")
	Git(tr.T, tr.Root, "init", "--quiet", "--bare", bare)
	Git(tr.T, tr.Root, "remote", "add", "origin", bare)
	tr.Parent("push", "--quiet", "origin", ParentName)
}

// Replace は path のファイルの中身の old を new に置き換えて書き戻す（最初の 1 件）。
func (tr *Tree) Replace(path, old, new string) {
	tr.T.Helper()
	Write(tr.T, path, strings.Replace(Read(tr.T, path), old, new, 1))
}
