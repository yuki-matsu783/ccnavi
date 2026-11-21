package life

import (
	"encoding/json"
	"fmt"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/samples"
	"ccnavi/internal/harness/ticketkit"
)

// セッション開始で示す、却下された要求（REQ-SES-02）。承認済みチケットの要求のうち、ルールの deny や上位（親の範囲・フェーズ定義の上限）に
// 退けられるものを、SessionStart で要求・理由・出所つきで示す。情報源は承認時の記録ではなく、承認済みチケットとルールからの再計算。
//  1. 項がルールの deny にヒットする: 書かれた glob・deny の message・ルールの id が出る（allow と ask の両方）
//  2. 子の項が親の範囲を超える: 出所は「親 <親>」。フェーズ定義の上限を超える: 出所は「フェーズ定義 <名前>」
//  3. 却下が多いときは件数と先頭の数件に絞り、--explain へ誘う
//  4. 却下が無ければ何も足さない
//  5. cwd のツリーに結び付くチケットだけを見る。ワークスペースルートからは件数の 1 行まで
//  6. サブエージェント（agent_id がある）には出さない
//  7. ユーザに見える systemMessage とモデルに渡る additionalContext の両方に出る。終了コードは変わらない
// 却下の glob は、deny のルールの glob と同じ場所を指す書き方で揃える（ルールは */src/secret/*、チケットは src/secret/*）。
// 重なりの判定の細部はここでは固定しない。

const (
	rejectWord  = "却下"
	rejectMsg   = "秘密の置き場は触れません。"
	secretGlob  = "src/secret/*"
	explainFlag = "--explain"
)

// rejectsTree は deny のルール no-secret を足した木。
func rejectsTree(t *testing.T) *ticketkit.Tree {
	t.Helper()
	tr := ticketkit.New(t, ticketkit.Options{Phases: samples.Phases})
	var rules map[string]any
	jsonOf(t, samples.Rules, &rules)
	rules["deny"] = append(rules["deny"].([]any), map[string]any{"id": "no-secret", "match": "Write|Edit", "glob": "*/src/secret/*", "message": rejectMsg})
	raw, err := json.Marshal(rules)
	if err != nil {
		t.Fatal(err)
	}
	ticketkit.Write(t, base.CommonPath(tr.Root, "rules"), string(raw))
	return tr
}

// withAsk は提案の本文に ask の項を足す（started_at の前に差し込む）。
func withAsk(text string, globs ...string) string {
	lines := []string{"ask:"}
	for _, g := range globs {
		lines = append(lines, "  - match: Write|Edit", `    glob: "`+g+`"`)
	}
	return strings.Replace(text, `started_at: ""`, strings.Join(lines, "\n")+"\n"+`started_at: ""`, 1)
}

// session は SessionStart を打って (終了コード, systemMessage, additionalContext) を返す。
func session(tr *ticketkit.Tree, cwd, agent string) (int, string, string) {
	tr.T.Helper()
	if cwd == "" {
		cwd = tr.ParentTree
	}
	payload := map[string]any{"hook_event_name": "SessionStart", "cwd": cwd, "session_id": "s1", "source": "startup"}
	if agent != "" {
		payload["agent_id"] = agent
	}
	r := ticketkit.OK(tr.T, rawCall(tr, payload))
	if strings.TrimSpace(r.Stdout) == "" {
		return r.Code, "", ""
	}
	var out struct {
		Hook struct {
			AdditionalContext string `json:"additionalContext"`
		} `json:"hookSpecificOutput"`
		SystemMessage string `json:"systemMessage"`
	}
	jsonOf(tr.T, r.Stdout, &out)
	return r.Code, out.SystemMessage, out.Hook.AdditionalContext
}

func shown(tr *ticketkit.Tree, cwd string) (string, string) {
	tr.T.Helper()
	_, system, context := session(tr, cwd, "")
	return system, context
}

// approveAll は preview の digest を渡して names を承認する。
func approveAll(tr *ticketkit.Tree, names ...string) {
	tr.T.Helper()
	var preview struct{ Digest string }
	jsonOf(tr.T, tr.Ccnavi("--agree", "--preview", "--json").Stdout, &preview)
	done := ticketkit.OK(tr.T, tr.Ccnavi("user", "agree", "--yes", strings.Join(names, ","), "--digest", preview.Digest, "--json"))
	var out struct{ Copies []string }
	jsonOf(tr.T, done.Stdout, &out)
	for _, name := range names {
		found := false
		for _, c := range out.Copies {
			found = found || filepath.Base(c) == name+".md"
		}
		if !found {
			tr.T.Fatalf("%s が承認されていない\n%s", name, done.Stdout)
		}
	}
}

// approvedParent は親を提案して承認し、着手する。
func approvedParent(tr *ticketkit.Tree, allow []string, plan ...string) {
	tr.T.Helper()
	if len(plan) == 0 {
		plan = []string{"research", "design"}
	}
	tr.Propose("i0001", samples.ParentText("i0001", samples.Plan(plan...), samples.ParentOpts{Allow: allow, Copy: tr.Phases}), "")
	tr.CommitParent("tickets")
	approveAll(tr, "i0001")
	tr.StartParent("i0001")
}

// 親の項が deny にヒットする状態。
func rejectByDeny(tr *ticketkit.Tree) { approvedParent(tr, []string{"src/*", "wip/*", secretGlob}) }

func approveChildAt(tr *ticketkit.Tree, allow []string) string {
	tr.T.Helper()
	tr.Propose(childName, samples.ChildTextReview(childName, "i0001", 1, allow, samples.Off), "")
	tr.CommitParent("tickets")
	approveAll(tr, childName)
	tree := tr.Worktree(childName, "i0001")
	ticketkit.OK(tr.T, tr.Ccnavi("ticket", "start", childName))
	return tree
}

func both(t *testing.T, system, context string, check func(text string)) {
	t.Helper()
	for _, text := range []string{system, context} {
		check(text)
	}
}

func TestADenyHitShowsTheRequestTheReasonAndTheSource(t *testing.T) {
	t.Parallel()
	t.Run("allow", func(t *testing.T) {
		t.Parallel()
		tr := rejectsTree(t)
		rejectByDeny(tr)
		system, context := shown(tr, "")
		both(t, system, context, func(text string) {
			for _, w := range []string{rejectWord, secretGlob /* 要求 */, rejectMsg /* 理由 */, "no-secret" /* 出所 */} {
				ticketkit.Has(t, text, w)
			}
		})
	})
	t.Run("ask", func(t *testing.T) {
		t.Parallel()
		tr := rejectsTree(t)
		tr.Propose("i0001", withAsk(samples.ParentText("i0001", samples.Plan("research", "design"), samples.ParentOpts{Copy: tr.Phases}), secretGlob), "")
		tr.CommitParent("tickets")
		approveAll(tr, "i0001")
		tr.StartParent()
		system, context := shown(tr, "")
		both(t, system, context, func(text string) {
			ticketkit.Has(t, text, secretGlob)
			ticketkit.Has(t, text, rejectMsg)
		})
	})
}

func TestAChildItemBeyondTheParentOrThePhaseLimitNamesTheSource(t *testing.T) {
	t.Parallel()
	t.Run("the_parent_range", func(t *testing.T) {
		t.Parallel()
		tr := rejectsTree(t)
		approvedParent(tr, []string{"wip/*"})
		tree := approveChildAt(tr, []string{"docs/*"}) // 親の範囲は wip/*。子が docs/* を求める。
		system, context := shown(tr, tree)
		both(t, system, context, func(text string) {
			ticketkit.Has(t, text, "docs/*")
			ticketkit.Has(t, text, "親 i0001")
		})
	})
	t.Run("the_phase_definition", func(t *testing.T) {
		t.Parallel()
		// research の上限は wip/research/*。親の範囲（src/*）には収まるが、定義を超える。
		tr := rejectsTree(t)
		approvedParent(tr, []string{"src/*", "wip/*"})
		tree := approveChildAt(tr, []string{"src/*"})
		system, context := shown(tr, tree)
		both(t, system, context, func(text string) {
			ticketkit.Has(t, text, "src/*")
			ticketkit.Has(t, text, "フェーズ定義 research")
			// 出所に親は出ない。context に載る作業中の親の一覧（親 i0001 の行）は別の話。
			head, _, _ := strings.Cut(text, "[ccnavi] このPCで作業中の親:")
			ticketkit.Lacks(t, head, "親 i0001")
		})
	})
}

// 却下が多いときは件数と先頭の数件に絞り、--explain へ誘う。常駐の文脈なので、却下の段落は短い。
func TestManyRejectsAreCutToACountAndTheFirstFewAndPointToExplain(t *testing.T) {
	t.Parallel()
	tr := rejectsTree(t)
	var globs []string
	for i := 0; i < 12; i++ {
		globs = append(globs, fmt.Sprintf("src/secret/%02d/*", i))
	}
	approvedParent(tr, append([]string{"src/*", "wip/*"}, globs...))
	system, context := shown(tr, "")
	both(t, system, context, func(text string) {
		ticketkit.Has(t, text, "12 件")
		ticketkit.Has(t, text, globs[0])
		ticketkit.Lacks(t, text, globs[len(globs)-1])
		n := 0
		for _, g := range globs {
			if strings.Contains(text, g) {
				n++
			}
		}
		if n > 5 {
			t.Errorf("先頭の数件を超えて %d 件出した:\n%s", n, text)
		}
		ticketkit.Has(t, text, explainFlag)
	})
	var part []string
	for _, p := range strings.Split(context, "\n\n") {
		if strings.Contains(p, rejectWord) {
			part = append(part, p)
		}
	}
	if len(part) > 1 || (len(part) == 1 && len(strings.Split(part[0], "\n")) > 12) {
		t.Errorf("却下の段落が長い:\n%s", context)
	}
}

func TestNoRejectsAddsNothing(t *testing.T) {
	t.Parallel()
	tr := rejectsTree(t)
	approvedParent(tr, []string{"src/*", "wip/*"})
	system, context := shown(tr, "")
	ticketkit.Lacks(t, system, rejectWord)
	ticketkit.Lacks(t, context, rejectWord)
}

func TestRejectsOfOtherTreesAreNotShown(t *testing.T) {
	t.Parallel()
	tr := rejectsTree(t)
	approvedParent(tr, []string{"src/*", "wip/*"})
	other := tr.Worktree("i0002", "main")
	tr.Propose("i0002", samples.ParentText("i0002", samples.Plan("research", "design"), samples.ParentOpts{Allow: []string{"src/*", secretGlob}, Copy: tr.Phases}), other)
	commitIn(tr, other, "approval-request")
	approveAll(tr, "i0002")
	system, context := shown(tr, "") // i0001 のツリー
	ticketkit.Lacks(t, system+context, secretGlob)
	ticketkit.Lacks(t, system+context, rejectWord)
}

func TestTheWorkspaceRootGetsTheCountLineOnly(t *testing.T) {
	t.Parallel()
	tr := rejectsTree(t)
	rejectByDeny(tr)
	system, context := shown(tr, tr.Root)
	both(t, system, context, func(text string) {
		ticketkit.Has(t, text, rejectWord)
		ticketkit.Has(t, text, "1 件")
		ticketkit.Lacks(t, text, secretGlob)
		ticketkit.Lacks(t, text, rejectMsg)
	})
	n := 0
	for _, l := range strings.Split(context, "\n") {
		if strings.Contains(l, rejectWord) {
			n++
		}
	}
	if n != 1 {
		t.Errorf("却下の行が %d 行:\n%s", n, context)
	}
}

func TestASubagentIsNotToldAboutRejects(t *testing.T) {
	t.Parallel()
	tr := rejectsTree(t)
	rejectByDeny(tr)
	code, system, context := session(tr, "", "a1")
	if code != 0 {
		t.Errorf("code %d", code)
	}
	ticketkit.Lacks(t, system+context, rejectWord)
	ticketkit.Lacks(t, system+context, secretGlob)
}

// 判定と終了コードは変わらない。案内を出しても deny にヒットする書き込みは止まる。
func TestTheVerdictAndTheExitCodeDoNotChange(t *testing.T) {
	t.Parallel()
	tr := rejectsTree(t)
	rejectByDeny(tr)
	code, system, context := session(tr, "", "")
	if code != 0 || system == "" || context == "" {
		t.Errorf("code %d, systemMessage %q, additionalContext %q（両方に出すはず）", code, system, context)
	}
	target := filepath.Join(tr.ParentTree, "src", "secret", "x.py")
	r := tr.Hook("PreToolUse", "Write", tr.ParentTree, map[string]any{"file_path": target})
	ticketkit.Has(t, r.Stdout, "deny")
}

// ---- --explain の全件の節

func explained(tr *ticketkit.Tree, args ...string) string {
	tr.T.Helper()
	return ticketkit.OK(tr.T, tr.Ccnavi(append([]string{explainFlag}, args...)...)).Stdout
}

// sectionOf は節の見出しから次の見出しまでの行（見出しの行を除く）。
func sectionOf(t *testing.T, out string) []string {
	t.Helper()
	head := "■ 上位の設定に却下された要求"
	_, rest, ok := strings.Cut(out, head)
	if !ok {
		t.Fatalf("節が無い:\n%s", out)
	}
	body, _, _ := strings.Cut(rest, "\n■")
	return strings.Split(body, "\n")[1:]
}

func TestExplainListsAllRejects(t *testing.T) {
	t.Parallel()
	tr := rejectsTree(t)
	var globs []string
	for i := 0; i < 12; i++ {
		globs = append(globs, fmt.Sprintf("src/secret/%02d/*", i))
	}
	approvedParent(tr, append([]string{"src/*", "wip/*"}, globs...))
	lines := sectionOf(t, explained(tr))
	var rows []string
	for _, l := range lines {
		if strings.Contains(l, "src/secret/") {
			rows = append(rows, l)
		}
	}
	if len(rows) != 12 {
		t.Fatalf("行が %d 件:\n%s", len(rows), strings.Join(lines, "\n"))
	}
	for _, g := range globs {
		n := 0
		for _, r := range rows {
			if strings.Contains(r, g) {
				n++
			}
		}
		if n != 1 {
			t.Errorf("%s の行が %d 件", g, n)
		}
	}
}

func TestExplainOneRowHasTheRequestTheReasonTheSourceAndANote(t *testing.T) {
	t.Parallel()
	tr := rejectsTree(t)
	rejectByDeny(tr)
	lines := sectionOf(t, explained(tr))
	var rows []string
	for _, l := range lines {
		if strings.Contains(l, secretGlob) {
			rows = append(rows, l)
		}
	}
	if len(rows) != 1 {
		t.Fatalf("行が %d 件:\n%s", len(rows), strings.Join(lines, "\n"))
	}
	for _, w := range []string{"i0001" /* どのチケットか */, rejectMsg /* 理由 */, "no-secret" /* 出所 */} {
		ticketkit.Has(t, rows[0], w)
	}
	// 節の末尾に近似であることを添える。
	ticketkit.Has(t, strings.Join(lines, "\n"), "近似")
}

func TestExplainAlsoNamesTheHigherSource(t *testing.T) {
	t.Parallel()
	tr := rejectsTree(t)
	approvedParent(tr, []string{"wip/*"})
	approveChildAt(tr, []string{"docs/*"})
	text := strings.Join(sectionOf(t, explained(tr)), "\n")
	ticketkit.Has(t, text, "docs/*")
	ticketkit.Has(t, text, "親 i0001")
}

func TestExplainShowsNoSectionWithoutRejectsAndNoneInJSON(t *testing.T) {
	t.Parallel()
	t.Run("none", func(t *testing.T) {
		t.Parallel()
		tr := rejectsTree(t)
		approvedParent(tr, []string{"src/*", "wip/*"})
		ticketkit.Lacks(t, explained(tr), "上位の設定に却下された要求")
	})
	t.Run("json_has_no_field", func(t *testing.T) {
		t.Parallel()
		tr := rejectsTree(t)
		rejectByDeny(tr)
		out := explained(tr, "--json")
		ticketkit.Lacks(t, out, rejectWord)
		ticketkit.Lacks(t, out, rejectMsg)
	})
}
