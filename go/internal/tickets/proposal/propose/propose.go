package propose

import (
	"fmt"
	"io"
	"slices"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/policy/texts"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/approval/wap"
	"ccnavi/internal/tickets/config/phases"
	"ccnavi/internal/tickets/flow"
	"ccnavi/internal/tickets/model/ids"
	"ccnavi/internal/tickets/model/place"
	"ccnavi/internal/tickets/model/ticket"
)

func same(a, b string) bool { return pathcase.Fold(a) == pathcase.Fold(b) }

// belongs はファイル名の語幹が、この親か、その子の識別子か（大文字小文字を問わない）。
func belongs(stem, parent string) bool {
	if same(stem, parent) {
		return true
	}
	child, ok := ids.ParseChild(stem)
	return ok && same(child.Parent, parent)
}

// candidates は親のワークツリー base の中の、この親子の提案と子のフローの下書き（絶対パス、名前順）。
func candidates(base, ticketsRel, parent string) []string {
	var found []string
	for _, folder := range []struct {
		name     string
		suffixes []string
	}{{string(place.Todo), []string{config.MdExt}}, {flow.FlowsDir, []string{".yml", ".yaml"}}} {
		directory := ospath.Join(base, append(strings.Split(strings.Trim(ticketsRel, "/"), "/"), folder.name)...)
		names, err := fsio.Listdir(directory)
		if err != nil {
			continue
		}
		slices.Sort(names)
		for _, name := range names {
			stem, ext := ospath.Splitext(name)
			if !slices.Contains(folder.suffixes, pathcase.Fold(ext)) || !belongs(stem, parent) {
				continue
			}
			path := ospath.Join(directory, name)
			if ospath.IsFile(path) && !ospath.IsLink(path) {
				found = append(found, path)
			}
		}
	}
	return found
}

// ownProposal は todo/<親>.md か（大文字小文字を問わない）。
func ownProposal(path, parent string) bool {
	stem, ext := ospath.Splitext(ospath.Basename(path))
	return same(ospath.Basename(ospath.Dirname(path)), string(place.Todo)) && same(stem, parent) && same(ext, config.MdExt)
}

// planProblems は計画がある親の提案の after と終端の誤り。
func planProblems(paths []string, parent string) []string {
	var found []string
	for _, path := range paths {
		if !ownProposal(path, parent) {
			continue
		}
		loaded, _ := ticket.Load(path)
		if loaded == nil || (!loaded.HasPlan() && len(loaded.Feedback) == 0) {
			continue
		}
		for _, p := range slices.Concat(phases.AfterProblems(loaded, ""), phases.TerminalProblems(loaded, nil, "")) {
			found = append(found, p.Detail)
		}
	}
	return found
}

// childlessProblems は最初の親の提案で、すぐ始まるステップに子の提案が無い警告（承認の確認と同じ文）。
func childlessProblems(conf *config.Settings, root string, paths []string, parent string) []string {
	i := slices.IndexFunc(paths, func(p string) bool {
		return same(ospath.Basename(p), parent+config.MdExt) && same(ospath.Basename(ospath.Dirname(p)), string(place.Todo))
	})
	if i < 0 {
		return nil
	}
	loaded, _ := ticket.Load(paths[i])
	if loaded == nil || !loaded.HasPlan() {
		return nil
	}
	opened, _ := approval.Scan(conf, root, false, nil)
	closed, _ := approval.Scan(conf, root, true, nil)
	if approval.ByID(slices.Concat(opened, closed))[parent] != nil {
		return nil // 承認済みの親の改版。子はもう承認の側にある
	}
	var numbers []int
	for _, path := range paths {
		stem, _ := ospath.Splitext(ospath.Basename(path))
		if child, ok := ids.ParseChild(stem); ok && strings.HasSuffix(path, config.MdExt) && !slices.Contains(numbers, child.Phase) {
			numbers = append(numbers, child.Phase)
		}
	}
	var out []string
	for _, p := range phases.ChildlessReady(loaded, numbers) {
		out = append(out, p.Detail)
	}
	return out
}

// Run は `ticket approval-request <識別子>`。送る提案を一覧に載せ、載せたものを標準出力に 1 行ずつ出す。gate は
// 送る提案の識別子（todo/ の .md）を受け取り、承認の画面と同じ検査を通す（通らなければ 0 以外。検査そのものは
// 上の段が持つ）。check は検査だけをして、何も一覧に載せない。
func Run(stdout, stderr io.Writer, conf *config.Settings, root, ident string, check bool, gate func([]string) int) int {
	// 1. 送る先（親のワークツリー）を決める。決まらなければ止める。送る先が無い設計（verdict No）のときは、止めず案内だけ出す。
	parent := wap.FamilyOf(ident)
	verdict, why, st := wap.TargetOf(conf, root, parent)
	if verdict == wap.Stop || st.Home == nil {
		if why == "" {
			why = "親のワークツリーが決まらない"
		}
		fmt.Fprintf(stderr, "ccnavi: 提案を送れません（%s）。\n", why)
		return 1
	}
	if verdict == wap.No {
		fmt.Fprintf(stdout, "案内: %s には送る先がありません（%s）。提案は手元に書かれたままです。\n", parent, why)
		return 0
	}
	base := st.Home.Root
	ticketsRel := conf.Tickets
	if ticketsRel == "" {
		ticketsRel = config.DefaultTickets
	}
	// 2. 送る提案（親と子の .md、フローの下書き）を集める。
	paths := candidates(base, ticketsRel, parent)
	if len(paths) == 0 {
		fmt.Fprintf(stderr, "ccnavi: 送る提案がありません（親のワークツリー %s の %s/%s/%s.md など）。"+
			"提案は識別子と同名のワークツリーの中に書いてください。\n", base, strings.Trim(ticketsRel, "/"), place.Todo, parent)
		return 1
	}
	// 3. 計画の誤りは、何も送らずに止める。子の無いフェーズは注意だけ出して続ける。
	if wrong := planProblems(paths, parent); len(wrong) > 0 {
		fmt.Fprintf(stderr, "ccnavi: %s の提案の計画に誤りがあるので送りません（何も送っていません）。\n", parent)
		for _, detail := range wrong {
			fmt.Fprintf(stderr, "  %s\n", detail)
		}
		fmt.Fprintln(stderr, texts.Say(root, "NEXT_APPROVAL_REQUEST_PLAN_WRONG", texts.Values{"parent": parent}, nil))
		return 1
	}
	for _, detail := range childlessProblems(conf, root, paths, parent) {
		fmt.Fprintf(stderr, "ccnavi: 注意: %s\n", detail)
	}
	// 4. 呼び手の検査（承認の画面と同じもの）。通らなければ送らない。todo/ の .md だけが対象。
	if gate != nil {
		var sending []string
		for _, p := range paths {
			if strings.HasSuffix(p, config.MdExt) && ospath.Basename(ospath.Dirname(p)) == string(place.Todo) {
				stem, _ := ospath.Splitext(ospath.Basename(p))
				sending = append(sending, stem)
			}
		}
		if gate(sending) != 0 {
			return 1
		}
	}
	// 5. --check ならここまで。送るなら、読んだ入力として記録し、送る提案を 1 行ずつ出す。
	if check {
		fmt.Fprintln(stdout, "確認: ok（approval-request を打てる状態。提案は送っていません）")
		fmt.Fprintln(stdout, texts.Say(root, "NEXT_APPROVAL_REQUEST_CHECKED", texts.Values{"parent": parent}, nil))
		return 0
	}
	for _, path := range paths {
		fsio.NoteInput(path)
		rel, _ := ospath.Relpath(path, base)
		fmt.Fprintf(stdout, "提案: %s\n", ospath.Slashed(rel))
	}
	return 0
}
