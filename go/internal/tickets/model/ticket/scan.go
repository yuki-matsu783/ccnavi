package ticket

import (
	"path/filepath"
	"strings"

	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/model/place"
)

// Scan はワークスペース・プロジェクト・全ワークツリーの提案（todo/ と review/）を集め、同じ識別子は正とする
// ツリーの側だけを残す。approved はまとめる前の承認済みチケットの全部で、渡せば承認済みの識別子の提案は
// 承認済みチケットで決めた正とするツリーの側だけを残す（Dedupe）。
func Scan(root, ticketsRel, projectsDir string, approved []*Ticket) ([]*Ticket, []rules.Problem) {
	found, problems := ScanAll(root, ticketsRel, projectsDir)
	return Dedupe(found, approved), problems
}

// ScanAll はワークスペースルートと全ワークツリーの提案を、重複をまとめずに集める（ボードが「どのツリーに
// あるか」を見せるのに使う）。置き場がプロジェクトを決める（frontmatter の `project:` は照合に使うだけ）。
// raw に project を差し込まない（承認は提案のバイト列をそのまま動かす）。
func ScanAll(root, ticketsRel, projectsDir string) ([]*Ticket, []rules.Problem) {
	var found []*Ticket
	var problems []rules.Problem
	trees := append([]tree.Tree{tree.MainTree(root)}, tree.Projects(projectsDir)...)
	trees = append(trees, tree.Worktrees(root, projectsDir)...)
	for _, t := range trees {
		base := ospath.Join(t.Root, filepath.FromSlash(ticketsRel))
		for _, state := range place.States {
			directory := ospath.Join(base, string(state))
			names, err := ospath.DiskListdir(directory)
			if err != nil {
				continue
			}
			// 大文字小文字を区別しない機械では DONE/ が done/ として開ける。置き場が状態なので、表記まで同じものだけ読む。
			if ospath.Basename(ospath.Realpath(directory)) != string(state) {
				problems = append(problems, problem(rules.Warn, string(state),
					ospath.RelOr(directory, root)+" の表記が状態の名前 `"+string(state)+"` と違うので読まない"))
				continue
			}
			for _, name := range names {
				stem, ok := strings.CutSuffix(name, ".md")
				if !ok {
					continue
				}
				path := ospath.Join(directory, name)
				ticket, complaints := Load(path)
				for i := range complaints {
					complaints[i].Rule += " (" + ospath.RelOr(path, root) + ")"
				}
				problems = append(problems, complaints...)
				if ticket == nil {
					continue
				}
				if ticket.ID != stem {
					problems = append(problems, problem(rules.Error, ticket.ID,
						"ファイル名 "+name+" と識別子 "+ticket.ID+" が一致しない"))
					continue
				}
				ticket.State, ticket.Tree, ticket.TreeRoot = state, t.Name, t.Root
				ticket.Project = t.Project
				found = append(found, ticket)
			}
		}
	}
	return found, problems
}

// SubsetProblems は子が親の部分集合でない項を warn で名指しする（判定が親の範囲で切り詰める）。glob 同士の包含は
// 一般には決められないので、子の項の字義どおりの前置を親に当てる。前置が親の allow か ask に入っていれば中。
// ワイルドカードがあれば前置に 1 文字足したパスを当てる（末尾の "/" を除くと、親の `src/b/*` が `src/b` に
// 当たらず正当な子を「超えている」と読む）。
func SubsetProblems(child, parent *Ticket) []rules.Problem {
	var out []rules.Problem
	for i := range child.Entries {
		e := &child.Entries[i]
		if e.Decision == rules.Deny {
			continue
		}
		if e.Regex != "" {
			out = append(out, problem(rules.Warn, child.ID, RegexOverflowDetail(e.Regex)))
			continue
		}
		probe := e.Glob
		if prefix := e.Prefix(); prefix != e.Glob {
			probe = prefix + "x"
		}
		if d := parent.Decide(probe); d != rules.Allow && d != rules.Ask {
			out = append(out, problem(rules.Warn, child.ID, "`"+e.Glob+"` は親 "+parent.ID+" の範囲を超えている"))
		}
	}
	return out
}
