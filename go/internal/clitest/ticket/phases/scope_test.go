package phases

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/samples"
)

// 範囲の上限（設計 wip/design/approve-carry.md 3・4、6.1〜6.2）と、サブコマンドに足した診断のオプション
// （tests/ticket/test_phases.py の後半）。
// 承認は範囲の超過を拒まず「編集対象としているが」として見せる。判定は子 → 親 → 定義の厳しい側で切り詰め、外へ出した上限を
// `limit:` 行で名指しする。親の範囲は既定で src/*・wip/*・tests/*、フェーズ 1 の定義 research は wip/research/*。

// scopedChild は ask と regex の項を足せる子の提案。regex は allow の項として書く。
func scopedChild(name string, phase int, allow, ask, regex []string) string {
	lines := []string{"---", "version: 1", "ticket: " + name, "parent: " + parentName, "phase: " + itoa(phase),
		"human_review:", "  required: true", "  reason: t", "title: 子 " + name, "rationale: r"}
	if len(allow) > 0 || len(regex) > 0 {
		lines = append(lines, "allow:")
		for _, g := range allow {
			lines = append(lines, "  - match: Write|Edit", `    glob: "`+g+`"`)
		}
		for _, r := range regex {
			lines = append(lines, "  - match: Write|Edit", "    regex: '"+r+"'")
		}
	}
	if len(ask) > 0 {
		lines = append(lines, "ask:")
		for _, g := range ask {
			lines = append(lines, "  - match: Write|Edit", `    glob: "`+g+`"`)
		}
	}
	lines = append(lines, `started_at: ""`, `completed_at: ""`, `base_sha: ""`, "---", "", "本文")
	return strings.Join(lines, "\n") + "\n"
}

// phasesWithoutResearch は定義 research を消した phases.yml（親の計画の 1 番目の定義が引けなくなる）。
func phasesWithoutResearch() string {
	head, _, _ := strings.Cut(samples.Phases, "  research:")
	_, tail, _ := strings.Cut(samples.Phases, "  design:")
	return head + "  design:" + tail
}

// phasesInherit は scope を inherit にした作業の定義だけを持つ phases.yml。
const phasesInherit = `
version: 1
phases:
  open:
    kind: work
    title: 自由
    review: false
    scope: inherit
`

func TestAParentWithoutAPlanIsApprovedButCannotStartEvenWithChildren(t *testing.T) {
	t.Parallel()
	w := newWS(t)
	w.propose(parentName, noPlanParent(parentName, "src/*", "wip/*"))
	w.propose("i0001-01-01", samples.ChildText("i0001-01-01", parentName, 1, []string{"src/a/*"}))
	w.commitParent()
	if r := w.approve(); r.Code != 0 || !w.isApproved("i0001-01-01") {
		t.Fatalf("承認: code %d\n%s", r.Code, r.Stderr)
	}
	started := w.ccnavi("ticket", "start", parentName)
	if started.Code != 1 {
		t.Errorf("code = %d\n%s", started.Code, started.Stdout)
	}
	contains(t, "stderr", started.Stderr, "に plan が無いので着手できない", "子や子の提案があっても同じ", "phases.yml", ".ccnavi/config/project/phases.yml", "work を 1 つ", "書くのはユーザ")
	// 着手の欄は書かれない
	contains(t, "承認済みチケット", read(t, w.doing(parentName)), `started_at: ""`)
	// 子の着手は親が着手済みであることが条件のまま。親が止まれば子も着手できない。
	w.worktree("i0001-01-01", parentName)
	if child := w.ccnavi("ticket", "start", "i0001-01-01"); child.Code != 1 {
		t.Errorf("親が止まっているのに子が着手できた: code %d\n%s", child.Code, child.Stdout)
	}
}

func TestNoPhasesFileAnywhereStopsAParentWithoutAPlan(t *testing.T) {
	t.Parallel()
	// どのレイヤーにも phases.yml が無いと plan を持てず、計画の無い親は着手が止まる。
	w := newWS(t)
	remove(t, w.phases)
	w.propose(parentName, noPlanParent(parentName, "src/*", "wip/*"))
	w.commitParent()
	w.mustApprove()
	started := w.ccnavi("ticket", "start", parentName)
	if started.Code != 1 {
		t.Errorf("code = %d\n%s", started.Code, started.Stdout)
	}
	contains(t, "stderr", started.Stderr, "phases.yml に最小のフェーズ定義", "書くのはユーザ")
}

func TestASecondCwdIsRefusedAndOneOfEachGoesThrough(t *testing.T) {
	t.Parallel()
	// sh が計算して渡すパス（--root・--cwd）は 2 度渡せない（issue #65）。`ccnavi-review.sh` は
	// `"$bin" --root "$root" --cwd "$here" "$@"` の形で呼ぶ。どちらも「いまどこで動いているか」で、エージェントが宣言するものではない。
	// 後ろに 1 本足すと後勝ちで読まれるので、2 本目が在ること自体を断る。
	w := newWS(t)
	refused := w.ccnavi("--cwd", w.parentTree, "--cwd", w.root, "--explain", "--json")
	if refused.Code == 0 || !strings.Contains(refused.Stderr, "--cwd は 1 度しか渡せない") {
		t.Errorf("2 本目の --cwd を断らない: code %d\n%s", refused.Code, refused.Stderr)
	}
	// 1 本ずつなら通る。断る側だけを見ると、全部断る実装でもテストが通ってしまう。
	passed := w.mustOK(w.ccnavi("--cwd", w.parentTree, "--explain", "--json"), "--explain --json")
	if len(jsonOf(t, passed.Stdout)) == 0 {
		t.Error("答えが空")
	}
}

func TestAPhasesFlagOnTicketFinishDoesNotDropTheReview(t *testing.T) {
	t.Parallel()
	// `ticket` のサブコマンドに `--phases` を足しても、定義は共通レイヤーのまま（issue #65）。`ccnavi-ticket.sh` が引数を
	// そのまま渡すので、この形はエージェントが Bash で打てる。通していた頃は、`review: true` の定義を `review: false` と
	// 宣言するファイルに差し替えて、レビュー待ちを飛ばせた。
	w := newWS(t)
	loose := write(t, filepath.Join(w.root, "loose.yml"), "version: 1\nphases:\n  design:\n    kind: work\n    title: 設計\n    review: false\n    scope: [\"wip/design/*\"]\n")
	w.family(planOf("design"))
	w.propose("i0001-01-01", childReview("i0001-01-01", 1, samples.Off, "wip/design/*"))
	w.commitParent()
	w.mustApprove()
	w.runChild("i0001-01-01", file{"wip/design/plan.md", "d\n"})
	closed := w.mustOK(w.ccnavi("ticket", "finish", "i0001-01-01", "--phases", loose), "finish")
	contains(t, "stderr", closed.Stderr, "--phases は診断") // 無視したことを言う
	// `review: true` のまま。閉じた先はレビュー待ちで、done/ ではない。
	if !exists(filepath.Join(w.parentTree, "wip", "proposals", "review", "i0001-01-01.md")) {
		t.Error("レビュー待ちへ動いていない（渡した定義で判定された）")
	}
	if exists(filepath.Join(w.approved, "done", "i0001-01-01.md")) {
		t.Error("レビューを飛ばして閉じた")
	}
}

// approvedChild は親を承認し、子を提案して承認し、子のワークツリーを作って着手する。ワークツリーを返す。
func (w *ws) approvedChild(text string, plan []samples.Step) string {
	w.t.Helper()
	w.family(plan)
	w.propose("i0001-01-01", text)
	w.commitParent("approval-request child")
	w.mustApprove()
	if !w.isApproved("i0001-01-01") {
		w.t.Fatal("子の承認済みチケットが置かれない")
	}
	return w.runChild("i0001-01-01")
}

func (w *ws) writeTo(tree, rel, mode string) run.Result {
	w.t.Helper()
	return w.hookWith(hookOpts{Mode: mode}, "PreToolUse", "Write", tree, input{"file_path": filepath.Join(tree, filepath.FromSlash(rel)), "content": "x\n"})
}

func TestARegexChildIsApprovedAndShownAsStoppedByTheJudge(t *testing.T) {
	t.Parallel()
	w := newWS(t)
	w.family(planOf("research", "design"))
	w.propose("i0001-01-01", scopedChild("i0001-01-01", 1, nil, nil, []string{"^(wip/research|src/a|docs)/"}))
	w.commitParent("approval-request child")
	approved := w.mustApprove()
	if !w.isApproved("i0001-01-01") {
		t.Fatal("regex の子が承認されない")
	}
	contains(t, "承認画面", approved.Stdout, "編集対象としているが")
	_, after, _ := strings.Cut(approved.Stdout, "編集対象としているが")
	contains(t, "承認画面", after, "regex")
	lacks(t, "stderr", approved.Stderr, "承認の対象にしない")
}

func TestAWriteBeyondThePhaseTypeIsStoppedOnEveryPath(t *testing.T) {
	t.Parallel()
	// 定義の上限の外は、Write の判定・dry-run・phases.yml の状態がどうでも・Bash の実行後チェック・SubagentStop のどれでも、
	// 上限の名指しつきで止まる。承認のあとの判定は親に固定したコピー（`phases:`）の定義で切り詰める。phases.yml から定義を消しても、
	// 壊しても（UTF-8 として読めなくても）、消しても、判定は phases.yml を読まないので上限は変わらず、例外で終わらない。
	w := newWS(t)
	tree := w.approvedChild(childText("i0001-01-01", 1, "wip/research/*", "src/a/*", "docs/*"), planOf("research", "design"))
	inside := w.writeTo(tree, "wip/research/note.md", "")
	if w.decision(inside) == "deny" {
		t.Errorf("定義の中が止まった: %s", w.reason(inside))
	}
	lacks(t, "reason", w.reason(inside), "DENY_TICKET_SCOPE")
	denied := w.writeTo(tree, "src/a/x.py", "")
	if denied.Code != 0 || w.decision(denied) != "deny" {
		t.Fatalf("定義の外が止まらない: code %d\n%s", denied.Code, denied.Stdout)
	}
	contains(t, "reason", w.reason(denied), "DENY_TICKET_SCOPE", "limit: phase type 調査 (research): wip/research/*")
	// dry-run でもゲートなので止まり、上限を名指しする。
	dry := w.writeTo(tree, "src/a/x.py", "dry-run")
	if dry.Code != 0 || w.decision(dry) != "deny" {
		t.Fatalf("dry-run が止まらない: code %d\n%s", dry.Code, dry.Stdout)
	}
	contains(t, "dry-run の理由", w.reason(dry), "still applies", "DENY_TICKET_SCOPE", "limit: phase type")
	// phases.yml をどう変えても、上限は変わらない。
	undecodable := strings.ReplaceAll(samples.Phases, "調査", "\xff\xfe\x80")
	cases := []struct {
		label   string
		content *string
	}{
		{"定義を消した", ptr(phasesWithoutResearch())},
		{"phases.yml が不正", ptr("version: 1\nphases: [\n")},
		{"phases.yml が UTF-8 でない", &undecodable},
		{"phases.yml が無い", nil},
	}
	for _, c := range cases {
		if c.content == nil {
			remove(t, w.phases)
		} else if err := os.WriteFile(w.phases, []byte(*c.content), 0o644); err != nil {
			t.Fatal(err)
		}
		beyondType := w.writeTo(tree, "src/a/x.py", "")
		w.assertAnswered(c.label, beyondType)
		if w.decision(beyondType) != "deny" {
			t.Errorf("%s: 定義の外が止まらない: %s", c.label, beyondType.Stdout)
		}
		contains(t, c.label+" の理由", w.reason(beyondType), "limit: phase type 調査 (research)")
		lacks(t, c.label+" の理由", w.reason(beyondType), "切り詰めていない")
		in := w.writeTo(tree, "wip/research/x.md", "")
		w.assertAnswered(c.label, in)
		if w.decision(in) == "deny" {
			t.Errorf("%s: 定義の中が止まった: %s", c.label, w.reason(in))
		}
		lacks(t, c.label+" の理由", w.reason(in), "読めない")
		beyondParent := w.writeTo(tree, "docs/x.md", "")
		if w.decision(beyondParent) != "deny" {
			t.Errorf("%s: 親の外が止まらない: %s", c.label, beyondParent.Stdout)
		}
		contains(t, c.label+" の理由", w.reason(beyondParent), "DENY_TICKET_SCOPE", "limit: parent i0001")
		for _, command := range []string{"ls", "echo x > src/a/x.py", "sh ../../../.ccnavi/scripts/ccnavi-ticket.sh finish i0001-01-01"} {
			w.assertAnswered(c.label, w.hook("PreToolUse", "Bash", tree, input{"command": command}))
		}
	}
	// 実行後チェック: Bash が定義の上限の外に書くと POST_TICKET_SCOPE。
	w.hook("UserPromptSubmit", "", tree, nil)
	first := w.hook("PostToolUse", "Bash", tree, input{"command": "python gen.py"})
	if first.Code != 0 {
		t.Errorf("書いていないのに実行後チェックが落ちた: code %d\n%s%s", first.Code, first.Stdout, first.Stderr)
	}
	// 定義の中への書き込みは言わない。
	write(t, filepath.Join(tree, "wip", "research", "gen.md"), "ok\n")
	quiet := w.hook("PostToolUse", "Bash", tree, input{"command": "python gen.py"})
	if quiet.Code != 0 {
		t.Errorf("code %d\n%s%s", quiet.Code, quiet.Stdout, quiet.Stderr)
	}
	lacks(t, "実行後チェック", quiet.Stdout+quiet.Stderr, "POST_TICKET_SCOPE")
	// 子の範囲の中だが定義の外。
	write(t, filepath.Join(tree, "src", "a", "gen.py"), "x\n")
	after := w.hook("PostToolUse", "Bash", tree, input{"command": "python gen.py"})
	if after.Code != 2 {
		t.Errorf("定義の外へ書いたのに止まらない: code %d\n%s%s", after.Code, after.Stdout, after.Stderr)
	}
	contains(t, "stderr", slash(after.Stderr), "POST_TICKET_SCOPE", "src/a/gen.py", "調査")
	// SubagentStop: 定義の上限の外の変更で差し戻し、上限の名指しが付く。
	write(t, filepath.Join(tree, "wip", "research", "ok.md"), "ok\n")
	write(t, filepath.Join(tree, "src", "a", "stray.py"), "x\n")
	w.git(tree, "add", "-A")
	w.git(tree, "commit", "--quiet", "-m", "stray")
	stop := w.hookWith(hookOpts{AgentID: "sub-1"}, "SubagentStop", "", tree, nil)
	if stop.Code != 2 {
		t.Errorf("SubagentStop が差し戻さない: code %d\n%s%s", stop.Code, stop.Stdout, stop.Stderr)
	}
	contains(t, "stderr", stop.Stderr, "POST_TICKET_SCOPE", "src/a/stray.py", "（定義 調査 の上限の外）")
	lacks(t, "stderr", stop.Stderr, "wip/research/ok.md")
}

func ptr[T any](v T) *T { return &v }

// assertAnswered は hook が判定の答えとして読める形で返したこと。
func (w *ws) assertAnswered(label string, r run.Result) {
	w.t.Helper()
	if r.Code != 0 && r.Code != 2 {
		w.t.Errorf("%s: code %d\n%s%s", label, r.Code, r.Stdout, r.Stderr)
	}
	if strings.Contains(r.Stderr, "panic:") {
		w.t.Errorf("%s: panic: %s", label, r.Stderr)
	}
	if strings.TrimSpace(r.Stdout) != "" && !json.Valid([]byte(r.Stdout)) {
		w.t.Errorf("%s: JSON でない出力: %q", label, r.Stdout)
	}
}

func TestAskInsideThePhaseTypeStaysAskAndOutsideItStops(t *testing.T) {
	t.Parallel()
	// 定義の上限の中で子が `ask` と書いた場所は TICKET_ASK のまま。外なら止まる（定義は allow か外しか言わない）。
	w := newWS(t)
	tree := w.approvedChild(scopedChild("i0001-01-01", 1, []string{"wip/research/a/*"}, []string{"wip/research/b/*", "src/b/*"}, nil), planOf("research", "design"))
	asked := w.writeTo(tree, "wip/research/b/x.md", "")
	if w.decision(asked) != "ask" {
		t.Errorf("decision = %q\n%s", w.decision(asked), asked.Stdout)
	}
	contains(t, "reason", w.reason(asked), "TICKET_ASK")
	beyond := w.writeTo(tree, "src/b/x.py", "")
	if w.decision(beyond) != "deny" {
		t.Errorf("定義の外の ask が止まらない: %s", beyond.Stdout)
	}
	contains(t, "reason", w.reason(beyond), "limit: phase type")
}

func TestInheritScopeDoesNotCutByTheType(t *testing.T) {
	t.Parallel()
	w := newWS(t)
	w.setPhases(phasesInherit)
	tree := w.approvedChild(childText("i0001-01-01", 1, "src/a/*"), planOf("open"))
	r := w.writeTo(tree, "src/a/x.py", "")
	if w.decision(r) == "deny" {
		t.Errorf("inherit の定義で切り詰めた: %s", r.Stdout)
	}
	lacks(t, "reason", w.reason(r), "DENY_TICKET_SCOPE", "limit:")
}
