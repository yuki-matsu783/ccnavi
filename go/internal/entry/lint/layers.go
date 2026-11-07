package lint

import (
	"bytes"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/config/check"
	"ccnavi/internal/tickets/lifecycle/integrate"
	"ccnavi/internal/tickets/model/archive"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/standing"
	"ccnavi/internal/tickets/model/ticket"
)

// 設定のレイヤーをまたぐ検査。1 つのレイヤーの 1 本ずつの検査は tickets/config/check にある。

// worktreesWhere はワークツリーの苦情の出どころ（`(.claude/worktrees/<名前>)`）。
func worktreesWhere(name string) string {
	return "(" + ospath.Slashed(tree.WorktreesDir) + "/" + name + ")"
}

func projectHome(conf *config.Settings) string {
	if conf.ProjectHome != "" {
		return conf.ProjectHome
	}
	return config.DefaultProjectHome
}

// worktreeLayers はワークツリーの ccnavi ディレクトリに、元リポジトリに無いファイルがあるか（warn）。判定が読むのは
// 元リポジトリの版だけなので、足したファイルは統合されるまで使われない。同じパスが両方に在れば編集で、git の差分が
// 拾う。承認済みの領域は数えない（承認済みチケットは親のワークツリーの版が読まれるので、言えば誤った案内になる）。
func worktreeLayers(conf *config.Settings, root string) []rules.Problem {
	var out []rules.Problem
	home := filepath.FromSlash(projectHome(conf))
	for _, work := range tree.Worktrees(root, conf.Projects) {
		origin := root
		if work.Project != "" {
			origin = tree.ProjectRoot(conf.Projects, work.Project)
		}
		approved := pathcase.Fold(ospath.Normpath(conf.ApprovedDir(work.Root)))
		for _, rel := range filesUnder(ospath.Join(work.Root, home)) {
			if ospath.DiskExists(ospath.Join(origin, home, filepath.FromSlash(rel))) {
				continue
			}
			// プロジェクトのワークツリーの共通レイヤーは、発言のときの同期がミラーしたもの。ワークスペースの中では読まれない。
			if work.Project != "" && strings.HasPrefix(rel, config.CommonDir+"/") {
				continue
			}
			where := pathcase.Fold(ospath.Normpath(ospath.Join(work.Root, home, filepath.FromSlash(rel))))
			if strings.HasPrefix(where, approved+ospath.Sep) {
				continue
			}
			out = append(out, rules.Problem{Severity: rules.Warn, Rule: worktreesWhere(work.Name), Detail: conf.ProjectHome + "/" + rel +
				" はワークツリーにしかない。判定が読むのは元リポジトリの版なので、このファイルは統合されるまで有効にならない"})
		}
	}
	return out
}

// filesUnder は base の下のファイルを、base からの相対（"/" 区切り）で文字列の順に並べる。os.walk と同じく
// ディレクトリへのリンクは辿らず、ファイルにも数えない。読めないディレクトリは飛ばす。
func filesUnder(base string) []string {
	var found []string
	var walk func(dir, rel string)
	walk = func(dir, rel string) {
		entries, err := os.ReadDir(dir)
		if err != nil {
			return
		}
		for _, e := range entries {
			path, name := ospath.Join(dir, e.Name()), rel+e.Name()
			switch {
			case e.IsDir():
				walk(path, name+"/")
			case e.Type()&os.ModeSymlink != 0 && ospath.IsDir(path):
			default:
				found = append(found, name)
			}
		}
	}
	walk(base, "")
	sort.Strings(found)
	return found
}

// syncProblems は親のワークツリーの置き場の検査と、レイヤーのずれ。origin/<統合先> が手元に無く比べられなかった
// リポジトリは info 1 行にまとめる。
func syncProblems(conf *config.Settings, root string) []rules.Problem {
	out := parentTreesOffBranch(conf, root)
	var unchecked []string
	for _, project := range layerRepos(conf) {
		found, checked := drift(conf, root, project)
		out = append(out, found...)
		if !checked {
			unchecked = append(unchecked, tree.RepoKey(project))
		}
	}
	if len(unchecked) > 0 {
		out = append(out, rules.Problem{Severity: rules.Info, Rule: whereLayers, Detail: "統合先のブランチ（origin/<統合先>）が手元に無く、" +
			"レイヤーのずれを確かめていない（" + strings.Join(unchecked, ", ") + "）。取得すると確かめる"})
	}
	return out
}

// parentTreesOffBranch は名前が親の識別子なのに、HEAD のブランチ名を変換した識別子が名前と合わない親のワークツリー。
func parentTreesOffBranch(conf *config.Settings, root string) []rules.Problem {
	var out []rules.Problem
	for _, work := range tree.Worktrees(root, conf.Projects) {
		if !holdsParent(conf, work, root) {
			continue
		}
		branch, ok := tree.BranchOf(work.Root)
		if ok && standing.HeadBranch(work.Root, work.Name) != "" {
			continue
		}
		on := branch
		if !ok {
			on = "（ブランチの外）"
		}
		out = append(out, rules.Problem{Severity: rules.Warn, Rule: worktreesWhere(work.Name), Detail: "親 " + work.Name +
			" のワークツリーが " + on + " の上にある。親のブランチ名の `/` を `-` に置き換えたものがフォルダ名（" + work.Name +
			"）になる。取り込み（ccnavi-sync.sh）と正とする側の検査は、そのブランチだけを見る。" +
			"親が閉じるのを待ってから、ブランチを切り替えてください"})
	}
	return out
}

// holdsParent はそのツリーに `ticket: <ツリーの名前>` の親の承認済みチケットか提案があるか（sh の ccnavi_parent_tree と
// 同じ見方）。ready の後は親が手元の退避へ移るので、そこに親が在っても親のワークツリーとして扱う。
func holdsParent(conf *config.Settings, work tree.Tree, root string) bool {
	approved := conf.ApprovedDir(work.Root)
	proposals := ospath.Join(work.Root, filepath.FromSlash(conf.Tickets))
	for _, path := range []string{
		approval.CopyPath(approved, work.Name),
		approval.ClosedPath(approved, work.Name),
		ospath.Join(proposals, string(place.Todo), work.Name+".md"),
		ospath.Join(proposals, string(place.Review), work.Name+".md"),
	} {
		if !ospath.IsFile(path) {
			continue
		}
		if t, _ := ticket.Load(path); t != nil && t.ID == work.Name && t.Parent == "" {
			return true
		}
	}
	if root == "" {
		return false
	}
	held, ok := archive.ArchivedFields(root, work.Project, work.Name)
	return ok && held.Ticket == work.Name && held.Parent == ""
}

// layerRepos はレイヤーを比べるリポジトリ（ワークスペース自身の空と、プロジェクト）。
func layerRepos(conf *config.Settings) []string {
	out := []string{""}
	for _, p := range tree.Projects(conf.Projects) {
		out = append(out, p.Name)
	}
	return out
}

// layerDrift は手元のレイヤーが origin/<統合先> のレイヤー（同じパス）と違う、という warn。origin/<統合先> が手元に
// 無ければ黙る。project が空ならワークスペース自身。
func layerDrift(conf *config.Settings, root, project string) []rules.Problem {
	found, _ := drift(conf, root, project)
	return found
}

// drift は layerDrift の本体。2 つめは済んだか（origin を持つのに origin/<統合先> が手元に無いときだけ偽。origin を
// 持たないリポジトリは比べる相手が無いので済んだ扱い）。手元の判定は元リポジトリのチェックアウトのレイヤーを読み、
// 統合先へは切り替えない。読むのは取得済みの refs/remotes/origin/<統合先> だけで、ネットワークには出ない。
func drift(conf *config.Settings, root, project string) ([]rules.Problem, bool) {
	home := root
	if project != "" {
		home = tree.ProjectRoot(conf.Projects, project)
	}
	if home == "" || !ospath.IsDir(home) {
		return nil, true
	}
	branch := integrate.IntegrationName(home)
	ref := ""
	if branch != "" {
		ref = "refs/remotes/origin/" + branch
	}
	if ref == "" || !git.Run(home, []string{"rev-parse", "--verify", "--quiet", ref + "^{commit}"}, git.Options{}).OK() {
		// origin が無いリポジトリ（git でない、リモートを持たない）は、取得で解けないので言わない。
		return nil, !git.Run(home, []string{"remote", "get-url", "origin"}, git.Options{}).OK()
	}
	homeRel := strings.Trim(ospath.Slashed(projectHome(conf)), "/")
	var rels []string
	if project == "" {
		for _, kind := range config.LayerKinds {
			rels = append(rels, config.DefaultProjectHome+"/"+config.CommonDir+"/"+config.LayerFileNames[kind])
		}
	}
	for _, kind := range config.LayerKinds {
		rels = append(rels, homeRel+"/"+config.LayerConfigDir+"/"+config.LayerFileNames[kind])
	}
	layer := project
	if layer == "" {
		layer = config.LayerSelf
	}
	where := check.LayerWhere(layer)
	var out []rules.Problem
	for _, rel := range rels {
		remote, read := git.Blob(home, ref, rel, git.Options{})
		if !read {
			continue
		}
		local, err := os.ReadFile(ospath.Join(home, filepath.FromSlash(rel)))
		if err != nil {
			local = nil
		}
		if sameText(local, remote) {
			continue
		}
		how := "中身が違う"
		switch {
		case local == nil:
			how = "ワークツリーにありません。"
		case remote == nil:
			how = "統合先にありません。"
		}
		out = append(out, rules.Problem{Severity: rules.Warn, Rule: where, Detail: "ワークツリーの " + rel + " が統合先（origin/" + branch +
			"）と違う（" + how + "）。統合先に取り込まれるまで、他の機械と Chrome の判定には使われない。" +
			"手元の origin/" + branch + " が古いだけなら、先に取得（fetch）してから見直す"})
	}
	return out, true
}

// sameText は改行の違い（CRLF と LF）を除いて同じか。片方が無ければ、両方とも無いときだけ同じ。
func sameText(a, b []byte) bool {
	if a == nil || b == nil {
		return a == nil && b == nil
	}
	crlf, lf := []byte("\r\n"), []byte("\n")
	return bytes.Equal(bytes.ReplaceAll(a, crlf, lf), bytes.ReplaceAll(b, crlf, lf))
}
