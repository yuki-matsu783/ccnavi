package chrome

import (
	"fmt"
	"path/filepath"
	"slices"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/tickets/model/ids"
	"ccnavi/internal/tickets/model/place"
)

// familyLimit は参照の閉包で辿る親子のチケットの上限。1 組あたり tree 1 回と blob をまとめて読む 1 回なので、
// 承認 1 回の読み取りが 40 回ほどに収まる。超えたら「決まらない」で止め、手元での確かめを案内する。
const familyLimit = 16

// 統合先から読むもの（置き場のパスの設定に依らないもの）。
var (
	worktreesDir = filepath.ToSlash(tree.WorktreesDir)
	commonLayer  = config.DefaultProjectHome + "/" + config.CommonDir
	ownLayer     = config.DefaultProjectHome + "/" + config.LayerConfigDir
	settingsFile = filepath.ToSlash(config.SharedClaudeSettings)
	doneDir      = config.DefaultApproved + "/" + string(place.Done)
)

func sortedSet(items ...string) []string {
	var out []string
	for _, s := range items {
		if !slices.Contains(out, s) {
			out = append(out, s)
		}
	}
	slices.Sort(out)
	return out
}

// opPlacement は置き場のパス。既定に固定し、統合先の `.claude/settings.json` の env も手元の env も読まない。
// 統合先の承認済みは閉じたもの（done/）だけを読む。作業中のものは P を正とし、前の統合先から切った P でも
// 閉じた識別子の使い直しを見つけるため、done/ は常に読む。
func opPlacement(*pyjson.Object, string) (*pyjson.Object, error) {
	return pyjson.NewObject("placement", pyjson.NewObject(
		"tickets", config.DefaultTickets,
		"approved", config.DefaultApproved,
		"integration_paths", sortedSet(doneDir, commonLayer, ownLayer),
		"integration_files", []string{settingsFile, config.CompatFile},
		"branch_paths", []string{config.DefaultApproved, config.DefaultTickets},
		"workspace_paths", sortedSet(commonLayer, ownLayer),
		"workspace_files", []string{settingsFile, config.CompatFile},
		"project_paths", sortedSet(doneDir, ownLayer),
		"layer_dir", ownLayer,
	)), nil
}

// opFamilies は候補のブランチのうち、親のブランチ（その置き場に、ブランチ名を変換した識別子の親の提案か承認済みの
// チケットがある）であるもの。閉じた親（done/）しか無いブランチは数えない。同じ識別子の親チケットを持つブランチが
// 2 本以上あるときは conflict に止める理由を入れる。
func opFamilies(req *pyjson.Object, _ string) (*pyjson.Object, error) {
	s, err := readSnapshot(req)
	if err != nil {
		return nil, err
	}
	closed := s.closed()
	candidates, ok := req.Value("candidates").([]any)
	if !ok {
		return nil, refused("candidates がありません")
	}
	out := []any{}
	for _, v := range candidates {
		name, ok := v.(string)
		if !ok || name == s.integration {
			continue
		}
		parents := s.claimers(name)
		if len(parents) == 0 {
			continue
		}
		first := parents[0]
		found := idents(parents)
		conflict := ""
		if len(found) > 1 {
			conflict = fmt.Sprintf("ブランチ %s を親ブランチとして宣言する親チケットが 2 つ以上あります（%s）。親子チケットを特定できません",
				name, strings.Join(found, ", "))
		} else {
			conflict = s.rivalProblem(name, first.t.ID)
		}
		out = append(out, pyjson.NewObject(
			"name", name, "family", first.t.ID, "title", first.t.Title, "state", string(first.state),
			"closed", slices.Contains(closed, first.t.ID), "conflict", conflict,
		))
	}
	return pyjson.NewObject("families", out), nil
}

// closure は親子のチケット family の判定に要る、親子のチケットの閉包。先行を多段で辿る（1 段だけでは手元と答えが
// 変わる）。提案と承認済みのチケットの predecessors を辿り、統合先の done/ にあるもので止める。まだ読んでいない
// 親子のチケットは need で返し、拡張が読んでから呼び直す。
type closure struct {
	families  []string
	idents    *pyjson.Object
	rivals    []string
	ambiguous []string
	need      []string
	absent    []string
	over      bool
}

func (c *closure) ident(name string) string { s, _ := c.idents.Value(name).(string); return s }

func (c *closure) message() string {
	if !c.over {
		return ""
	}
	return fmt.Sprintf("先行チケットをたどると、%d 組を超える親子チケットに広がりました（%s）。"+
		"Chrome では判定できません。計画を分けて先行チケットを減らすか、手元で `--agree --preview --verify` を実行して確認してください。",
		familyLimit, strings.Join(c.families, ", "))
}

func (c *closure) json() *pyjson.Object {
	need := c.need
	if c.over {
		need = nil
	}
	return pyjson.NewObject(
		"families", c.families, "idents", c.idents, "rivals", list(c.rivals), "ambiguous", list(c.ambiguous),
		"need", list(need), "absent", list(c.absent), "over_limit", c.over, "message", c.message(),
	)
}

// ambiguousProblem は閉包の先行の親子のチケットになるブランチが 2 本以上あれば、止める理由。
func (c *closure) ambiguousProblem() string {
	if len(c.ambiguous) == 0 {
		return ""
	}
	return "先行の親子のチケット（" + strings.Join(c.ambiguous, ", ") + "）になるブランチが 1 本ではありません。" +
		"どれを正とするか決まらないので、この親子のチケットは判定しません"
}

func (s *snapshot) closure(family string, hints map[string]string) (*closure, error) {
	closed := s.closed()
	owners := s.identBranches()
	c := &closure{families: []string{family}, idents: pyjson.NewObject()}
	if s.has(family) {
		mine, err := s.familyIdent(family)
		if err != nil {
			return nil, err
		}
		c.idents.Set(family, mine)
		if mine != family {
			c.rivals = append(c.rivals, mine)
			if !s.has(mine) && !s.isAbsent(mine) {
				c.need = append(c.need, mine)
			}
		}
	}
	queue := []string{family}
	for len(queue) > 0 {
		name := queue[0]
		queue = queue[1:]
		if !s.has(name) {
			if !s.isAbsent(name) {
				c.need = append(c.need, name)
			}
			continue
		}
		for _, f := range s.ticketsIn(s.branch(name), nil) {
			for _, ident := range f.t.Predecessors {
				fam := familyOf(ident)
				if slices.Contains(closed, ident) || slices.Contains(closed, fam) {
					continue
				}
				owned := owners[fam]
				if len(owned) > 1 {
					// 先行の親子のチケットになるブランチが 2 本以上。判定する親子のチケットと同じく決まらない。
					if !slices.Contains(c.ambiguous, fam) {
						c.ambiguous = append(c.ambiguous, fam)
					}
					continue
				}
				next := fam
				if len(owned) == 1 {
					next = owned[0]
				} else if hinted, ok := hints[fam]; ok {
					next = hinted
				}
				if slices.Contains(c.families, next) {
					continue
				}
				c.families = append(c.families, next)
				c.idents.Set(next, fam)
				queue = append(queue, next)
			}
		}
	}
	c.over = len(c.families) > familyLimit
	for _, name := range c.families {
		if s.isAbsent(name) && !slices.Contains(c.absent, name) {
			c.absent = append(c.absent, name)
		}
	}
	slices.Sort(c.absent)
	return c, nil
}

func opClosure(req *pyjson.Object, _ string) (*pyjson.Object, error) {
	s, err := readSnapshot(req)
	if err != nil {
		return nil, err
	}
	family, _ := text(req, "family")
	if family == "" {
		return nil, refused("family がありません")
	}
	hints, err := hintsOf(req, s)
	if err != nil {
		return nil, err
	}
	c, err := s.closure(family, hints)
	if err != nil {
		return nil, err
	}
	return c.json(), nil
}

// unreadable は判定の入力に本文を読めなかったファイルがあれば、止める理由。バイナリとシンボリックリンクは読まない
// （リンクの中身は指す先のパスの文字列で、手元でリンクを辿って読む中身とは違う）。判定がそれらを読むかは分からない
// ので、無いとも空とも読ませず「決まらない」で止める。書く操作があるので、言うだけにせず止める。
func (s *snapshot) unreadable(c *closure) string {
	names := uniqueNames(append(append([]string{s.integration}, c.families...), c.rivals...))
	var found []string
	add := func(prefix string, b *branch) {
		if b == nil {
			return
		}
		for _, kind := range []struct {
			paths []any
			what  string
		}{{b.binary, "バイナリ"}, {b.links, "シンボリックリンク"}} {
			for _, p := range kind.paths {
				found = append(found, prefix+":"+pyfmt.Str(p)+"（"+kind.what+"）")
			}
		}
	}
	for _, name := range names {
		add(name, s.branch(name))
	}
	if s.project != "" {
		add("(ワークスペース) "+s.wsName, s.workspace)
	}
	if len(found) == 0 {
		return ""
	}
	return "判定の入力に、本文を取得できないファイルがあります（" + strings.Join(found, ", ") + "）。" +
		"この親子チケットは判定できません。手元で `--agree --preview --verify` を実行して確認してください。"
}

// uniqueNames は並びを保ったまま重なりを除く（Python の dict.fromkeys）。
func uniqueNames(names []string) []string {
	var out []string
	for _, n := range names {
		if !slices.Contains(out, n) {
			out = append(out, n)
		}
	}
	return out
}

// validFamilyName は仮のツリーに組めるブランチか（識別子の形と、ブランチ名として使えるか）。
func validFamilyName(name, ident string) error {
	if !ids.IsValidID(ident) {
		return refusedf("親子のチケットの識別子が識別子の形ではありません：%s", pyfmt.Repr(ident))
	}
	if name != ident {
		if why := ids.BranchProblem(name); why != "" {
			return refusedf("親のブランチ名に指定できません：%s（%s）", pyfmt.Repr(name), why)
		}
	}
	return nil
}
