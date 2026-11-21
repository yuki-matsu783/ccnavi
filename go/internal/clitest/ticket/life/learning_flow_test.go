package life

import (
	"encoding/json"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/samples"
	"ccnavi/internal/harness/ticketkit"
)

// 学びの蓄積の受入（ticket start の案内・ticket finish の検査・consolidate・完成までの 1 周）。

const (
	oneWork = "version: 1\nphases:\n  work:\n    kind: work\n    title: 作業\n    review: false\n    scope: inherit\n"
	// 学びの記録の置き場と、記録の本文。
	child      = "i0001-01-01"
	cons       = "i0001-00-01"
	record     = "wip/handoff/learnings/" + child + ".md"
	entry      = "## 落とし穴\n\n- 種類: 未定\n- 内容: x\n"
	recordNone = "---\nticket: " + child + "\nparent: i0001\nlearnings: none\n---\n\n"
	recordOne  = "---\nticket: " + child + "\nparent: i0001\nlearnings: 1\n---\n\n" + entry
	pattern    = "docs/wiki/patterns/pat.md"
)

var wideScope = []string{"src/*", "wip/*", "docs/*"}

// learnFamily は、work 1 つの定義（extra はその定義に足す行）で親と子を承認し、親を着手する。子のワークツリーは切るが着手はしない。
func learnFamily(t *testing.T, phases string, allow []string) (*ticketkit.Tree, string) {
	t.Helper()
	tr := ticketkit.New(t, ticketkit.Options{Phases: phases})
	tr.Propose("i0001", samples.ParentText("i0001", samples.Plan("work"), samples.ParentOpts{Feedback: []samples.Step{}, Allow: allow, Copy: phases}), "")
	tr.Propose(child, samples.ChildTextReview(child, "i0001", 1, allow, samples.Off), "")
	tr.CommitParent("tickets")
	ticketkit.OK(t, tr.Approve())
	tr.StartParent()
	return tr, tr.Worktree(child, "i0001")
}

// consolidateFamily は、子が記録を書いて閉じ、親へ合流した木。child_finish_ok が false なら子の finish の結果を返す。
func consolidateFamily(t *testing.T, rec string) *ticketkit.Tree {
	t.Helper()
	tr, _ := consolidateFamilyUnclosed(t, rec)
	ticketkit.OK(t, ccn(tr, learnOn, "ticket", "finish", child))
	merge(tr, child)
	return tr
}

func consolidateFamilyUnclosed(t *testing.T, rec string) (*ticketkit.Tree, string) {
	t.Helper()
	tr, tree := learnFamily(t, oneWork, wideScope)
	ticketkit.OK(t, ccn(tr, learnOn, "ticket", "start", child))
	writeIn(t, tree, record, rec)
	tr.Git(tree, "add", "-f", "-A")
	tr.Git(tree, "commit", "--quiet", "-m", "rec")
	return tr, tree
}

func parentFinish(tr *ticketkit.Tree) string {
	tr.T.Helper()
	r := ccn(tr, learnOn, "ticket", "finish", "i0001")
	if r.Code == 0 {
		return ""
	}
	return r.Stderr
}

func consolidate(tr *ticketkit.Tree, env map[string]string) (int, string, string) {
	tr.T.Helper()
	r := ccn(tr, env, "ticket", "consolidate", "i0001")
	return r.Code, r.Stdout, r.Stderr
}

func consFiles(tr *ticketkit.Tree) []string {
	var out []string
	for _, n := range listDir(filepath.Join(tr.Approved, "doing")) {
		if strings.Contains(n, "-00-") {
			out = append(out, n)
		}
	}
	return out
}

func noEntries(rec string) string {
	return strings.Replace(strings.Replace(rec, "learnings: 1", "learnings: none", 1), entry, "", 1)
}

// 承認画面の「この親に固定する定義」は、学びの蓄積を切った定義にだけ `learning: off` を出す。
func TestAgreeScreenShowsLearningOffOnlyForDefinitionsThatTurnedItOff(t *testing.T) {
	t.Parallel()
	off := strings.Replace(samples.Phases, "    agent: explorer\n", "    agent: explorer\n    learning: off\n", 1)
	for _, c := range []struct {
		name, phases string
		wantResearch bool
	}{{"off", off, true}, {"default", samples.Phases, false}} {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			tr := ticketkit.New(t, ticketkit.Options{Phases: c.phases})
			tr.Propose("i0001", samples.ParentText("i0001", samples.Plan("research", "design"), samples.ParentOpts{Copy: c.phases}), "")
			tr.CommitParent("tickets")
			screen := ticketkit.OK(t, tr.Approve()).Stdout
			parts := strings.SplitN(screen, "■ この親に固定するフェーズ定義", 2)
			if len(parts) != 2 {
				t.Fatalf("承認画面に「この親に固定するフェーズ定義」が無い:\n%s", screen)
			}
			fixed := strings.SplitN(parts[1], "■", 2)[0]
			research, design := containsLine(fixed, "調査 / research"), containsLine(fixed, "設計 / design")
			if len(research) == 0 || len(design) == 0 {
				t.Fatalf("定義の行が無い:\n%s", fixed)
			}
			if got := strings.Contains(research[0], "learning: off"); got != c.wantResearch {
				t.Errorf("調査の行の learning: off = %v（%v のはず）: %s", got, c.wantResearch, research[0])
			}
			ticketkit.Lacks(t, design[0], "learning: off")
		})
	}
}

func TestChildStartNoticeAndFinishChecks(t *testing.T) {
	t.Parallel()
	t.Run("start_prints_the_notice_only_when_enabled", func(t *testing.T) {
		t.Parallel()
		for _, c := range []struct {
			env  map[string]string
			want bool
		}{{learnOn, true}, {learnOff, false}} {
			tr, _ := learnFamily(t, oneWork, []string{"src/*", "wip/*"})
			started := ticketkit.OK(t, ccn(tr, c.env, "ticket", "start", child))
			if got := strings.Contains(started.Stdout, "学びの蓄積"); got != c.want {
				t.Errorf("env %v: 案内の有無 = %v", c.env, got)
			}
			if c.want {
				ticketkit.Has(t, started.Stdout, "git commit")
			}
		}
	})
	t.Run("finish_is_refused_without_a_record", func(t *testing.T) {
		t.Parallel()
		tr, _ := learnFamily(t, oneWork, []string{"src/*", "wip/*"})
		ticketkit.OK(t, ccn(tr, learnOn, "ticket", "start", child))
		r := ticketkit.Fails(t, ccn(tr, learnOn, "ticket", "finish", child))
		ticketkit.Has(t, r.Stderr, "気づきの記録")
		ticketkit.Has(t, r.Stderr, "learn")
	})
	// 記録は HEAD にあるものだけが通る（add だけでは通らない）。
	t.Run("finish_passes_only_after_the_record_is_committed", func(t *testing.T) {
		t.Parallel()
		tr, tree := learnFamily(t, oneWork, []string{"src/*", "wip/*"})
		ticketkit.OK(t, ccn(tr, learnOn, "ticket", "start", child))
		writeIn(t, tree, record, recordNone)
		tr.Git(tree, "add", "-f", "-A")
		ticketkit.Fails(t, ccn(tr, learnOn, "ticket", "finish", child))
		tr.Git(tree, "commit", "--quiet", "-m", "rec")
		ticketkit.OK(t, ccn(tr, learnOn, "ticket", "finish", child))
	})
	t.Run("a_phase_with_learning_off_needs_no_record_and_prints_no_notice", func(t *testing.T) {
		t.Parallel()
		tr, _ := learnFamily(t, oneWork+"    learning: off\n", []string{"src/*", "wip/*"})
		started := ticketkit.OK(t, ccn(tr, learnOn, "ticket", "start", child))
		ticketkit.Lacks(t, started.Stdout, "学びの蓄積")
		ticketkit.OK(t, ccn(tr, learnOn, "ticket", "finish", child))
	})
}

// 統合の子（maintenance: learning-consolidate）と、親の finish の学びの検査。
func TestParentFinishAndConsolidateGuardTheRecords(t *testing.T) {
	t.Parallel()
	t.Run("finish_stops_on_an_unprocessed_entry_and_points_to_consolidate", func(t *testing.T) {
		t.Parallel()
		tr := consolidateFamily(t, recordOne)
		refused := parentFinish(tr)
		if refused == "" {
			t.Fatal("親の finish が通った")
		}
		for _, want := range []string{"落とし穴", record, "consolidate", "- 処理: 反映 <パターン名>", "- 処理: 対象外 <理由>", "注釈は付けない", "チケット ID"} {
			ticketkit.Has(t, refused, want)
		}
	})
	t.Run("all_none_passes_without_consolidation", func(t *testing.T) {
		t.Parallel()
		tr := consolidateFamily(t, noEntries(recordOne))
		if msg := parentFinish(tr); msg != "" {
			t.Fatal(msg)
		}
	})
	t.Run("uncommitted_learning_files_stop_the_parent", func(t *testing.T) {
		t.Parallel()
		tr := consolidateFamily(t, noEntries(recordOne))
		writeIn(t, tr.ParentTree, "docs/wiki/patterns/p.md", "x\n")
		msg := parentFinish(tr)
		ticketkit.Has(t, msg, "docs/wiki/patterns/p.md")
		ticketkit.Has(t, msg, "コミット")
	})
	t.Run("a_file_in_the_inbox_stops_the_parent", func(t *testing.T) {
		t.Parallel()
		tr := consolidateFamily(t, noEntries(recordOne))
		writeIn(t, tr.ParentTree, "docs/wiki/inbox/feat-1.md", "---\nlearnings: 1\n---\n\n## a\n")
		tr.CommitParent("inbox")
		ticketkit.Has(t, parentFinish(tr), "docs/wiki/inbox/feat-1.md")
	})
	// 子の finish も、親の finish と同じ検査で形式の誤りを断る。
	t.Run("a_broken_record_stops_the_child_finish", func(t *testing.T) {
		t.Parallel()
		tr, _ := consolidateFamilyUnclosed(t, strings.Replace(recordOne, "learnings: 1", "learnings: 5", 1))
		r := ticketkit.Fails(t, ccn(tr, learnOn, "ticket", "finish", child))
		ticketkit.Has(t, r.Stderr, record)
		ticketkit.Has(t, r.Stderr, "合わない")
	})
	// 合流のあとに記録が壊れたら、親の finish と consolidate が止まる。
	t.Run("a_record_broken_after_the_merge_stops_both_finish_and_consolidate", func(t *testing.T) {
		t.Parallel()
		tr := consolidateFamily(t, recordOne)
		writeIn(t, tr.ParentTree, record, strings.Replace(recordOne, "learnings: 1", "learnings: 5", 1))
		tr.Parent("add", "-f", record)
		tr.CommitParent("break")
		_, _, consErr := consolidate(tr, learnOn)
		for _, msg := range []string{parentFinish(tr), consErr} {
			ticketkit.Has(t, msg, record)
			ticketkit.Has(t, msg, "合わない")
		}
	})
}

func TestConsolidate(t *testing.T) {
	t.Parallel()
	t.Run("creates_one_open_child_and_is_idempotent", func(t *testing.T) {
		t.Parallel()
		tr := consolidateFamily(t, recordOne)
		code, out, errText := consolidate(tr, learnOn)
		if code != 0 {
			t.Fatal(out + errText)
		}
		ticketkit.Has(t, out, cons)
		ticketkit.Has(t, out, "- 処理: 反映 <パターン名>")
		if got := consFiles(tr); len(got) != 1 || got[0] != cons+".md" {
			t.Fatalf("統合の子 = %v", got)
		}
		text := ticketkit.Read(t, tr.ApprovedPath("doing", cons))
		ticketkit.Has(t, text, "maintenance: learning-consolidate")
		ticketkit.Has(t, text, "phase: 0")
		code, out, errText = consolidate(tr, learnOn)
		if code != 0 {
			t.Fatal(out + errText)
		}
		ticketkit.Has(t, out, cons)
		if got := consFiles(tr); len(got) != 1 {
			t.Errorf("打ち直しで二重にできた: %v", got)
		}
	})
	t.Run("is_refused_when_there_is_nothing_to_process", func(t *testing.T) {
		t.Parallel()
		tr := consolidateFamily(t, noEntries(recordOne))
		code, _, errText := consolidate(tr, learnOn)
		if code == 0 {
			t.Fatal("断るはず")
		}
		ticketkit.Has(t, errText, "処理すべき")
		if got := consFiles(tr); len(got) != 0 {
			t.Errorf("統合の子を作った: %v", got)
		}
	})
	t.Run("does_nothing_when_disabled", func(t *testing.T) {
		t.Parallel()
		tr := consolidateFamily(t, recordOne)
		code, _, errText := consolidate(tr, learnOff)
		if code == 0 {
			t.Fatal("断るはず")
		}
		ticketkit.Has(t, errText, "disable")
		if got := consFiles(tr); len(got) != 0 {
			t.Errorf("統合の子を作った: %v", got)
		}
	})
	t.Run("requires_the_other_finish_checks", func(t *testing.T) {
		t.Parallel()
		tr := consolidateFamily(t, recordOne)
		tr.Propose("i0001-01-02", samples.ChildTextReview("i0001-01-02", "i0001", 1, []string{"src/*"}, samples.Off), "")
		tr.CommitParent("open child")
		tr.Approve()
		code, _, errText := consolidate(tr, learnOn)
		if code == 0 {
			t.Fatal("断るはず")
		}
		ticketkit.Has(t, errText, "開いている子")
	})
	t.Run("a_second_consolidation_advances_the_sequence", func(t *testing.T) {
		t.Parallel()
		tr := consolidateFamily(t, recordOne)
		if code, out, errText := consolidate(tr, learnOn); code != 0 {
			t.Fatal(out + errText)
		}
		if err := mkdirRename(tr.ApprovedPath("doing", cons), tr.ApprovedPath("done", cons)); err != nil {
			t.Fatal(err)
		}
		tr.CommitParent("closed")
		code, out, errText := consolidate(tr, learnOn)
		if code != 0 {
			t.Fatal(out + errText)
		}
		ticketkit.Has(t, out, "i0001-00-02")
	})
	t.Run("full_cycle", func(t *testing.T) {
		t.Parallel()
		tr := consolidateFamily(t, recordOne)
		if code, out, errText := consolidate(tr, learnOn); code != 0 {
			t.Fatal(out + errText)
		}
		tr.CommitParent("consolidate child")
		tree := tr.Worktree(cons, "i0001")
		started := ticketkit.OK(t, ccn(tr, learnOn, "ticket", "start", cons))
		ticketkit.Lacks(t, started.Stdout, "学びの蓄積")
		text := readIn(t, tree, record)
		writeIn(t, tree, record, strings.TrimRight(text, "\n")+"\n- 処理: 対象外 環境が原因\n")
		tr.Git(tree, "add", "-f", "-A")
		tr.Git(tree, "commit", "--quiet", "-m", "process")
		// 記録を求めず、リスクも測らず、レビューなしで done/ へ移る。
		closed := ticketkit.OK(t, ccn(tr, learnOn, "ticket", "finish", cons))
		if !ticketkit.Exists(tr.ApprovedPath("done", cons)) || ticketkit.Exists(tr.ApprovedPath("doing", cons)) {
			t.Error("統合の子が done/ へ移っていない")
		}
		ticketkit.Lacks(t, closed.Stdout, "リスク")
		merge(tr, cons)
		if msg := parentFinish(tr); msg != "" {
			t.Fatal(msg)
		}
	})
}

// 完成の条件を 1 本で通す。子が記録を書く → 親の finish が止まる → consolidate → 統合の子が処理を書いて閉じる → 親が閉じる →
// 統合先に入った後、候補が条件を満たすと通知が出る → 提案を延期 → 観測元が増えると再び通知が出る。
func TestLearningCycle(t *testing.T) {
	t.Parallel()
	tr := consolidateFamilyUnclosedWithPattern(t)
	ticketkit.OK(t, ccn(tr, learnOn, "ticket", "finish", child))
	merge(tr, child)

	// 1. 子が記録を書いて閉じ、親の finish が止まる。
	ticketkit.Has(t, parentFinish(tr), "consolidate")
	// 2. consolidate が統合の子を作る。
	if code, out, errText := consolidate(tr, learnOn); code != 0 {
		t.Fatal(out + errText)
	}
	tr.CommitParent("consolidate child")
	// 3. 統合の子が、パターンに観測元を足し、記録に処理の行を書いて閉じる。
	ctree := tr.Worktree(cons, "i0001")
	ticketkit.OK(t, ccn(tr, learnOn, "ticket", "start", cons))
	writeIn(t, ctree, pattern, samples.Pattern([]string{"x1", "x2", child}, "docs"))
	text := readIn(t, ctree, record)
	writeIn(t, ctree, record, strings.TrimRight(text, "\n")+"\n- 処理: 反映 pat\n")
	tr.Git(ctree, "add", "-f", "-A")
	tr.Git(ctree, "commit", "--quiet", "-m", "process")
	ticketkit.OK(t, ccn(tr, learnOn, "ticket", "finish", cons))
	merge(tr, cons)
	// 4. 親が閉じる。
	if msg := parentFinish(tr); msg != "" {
		t.Fatal(msg)
	}
	// 5. 統合先に入った後、観測元が閾値に届いたので通知が出る（1 回だけ）。origin はここで足す（足すと、状態の操作は
	// write-and-push の対象になるため）。
	tr.Git(tr.Root, "merge", "--quiet", "--no-edit", "i0001")
	bare := filepath.Join(tr.Root, "origin.git")
	tr.Git(tr.Root, "init", "--quiet", "--bare", bare)
	tr.Git(tr.Root, "remote", "add", "origin", bare)
	tr.Git(tr.Root, "push", "--quiet", "origin", "main")
	tr.Git(tr.Root, "fetch", "--quiet", "origin")
	tr.Git(tr.Root, "remote", "set-head", "origin", "main")
	const noticeText = "学びの提案の候補が 1 件"
	ticketkit.Has(t, notice(tr), noticeText)
	ticketkit.Lacks(t, notice(tr), "学びの提案の候補")
	// 6. 提案を延期（その時点の観測元の数 3 を書く）。通知は出ない。
	writeIn(t, tr.Root, "docs/wiki/impact/p1.md", samples.Impact("p1", "延期", []samples.Count{{Name: "pat", N: 3}}, "2026-10-21T10:00:00+09:00", "skill"))
	publish(tr, "defer")
	ticketkit.Lacks(t, notice(tr), "学びの提案の候補")
	// 7. 観測元が増えると候補に戻り、再び通知が出る。
	writeIn(t, tr.Root, pattern, samples.Pattern([]string{"x1", "x2", child, "y1"}, "docs"))
	publish(tr, "one more source")
	ticketkit.Has(t, notice(tr), noticeText)
}

// consolidateFamilyUnclosedWithPattern は、統合先 main に 2 つの観測元があるパターンを置いてから、子が記録を書いたところまで組む。
func consolidateFamilyUnclosedWithPattern(t *testing.T) *ticketkit.Tree {
	t.Helper()
	tr := ticketkit.New(t, ticketkit.Options{Phases: oneWork})
	writeIn(t, tr.Root, pattern, samples.Pattern([]string{"x1", "x2"}, "docs"))
	tr.Git(tr.Root, "add", "-A")
	tr.Git(tr.Root, "commit", "--quiet", "-m", "pattern")
	tr.Parent("merge", "--quiet", "--no-edit", "main")
	tr.Propose("i0001", samples.ParentText("i0001", samples.Plan("work"), samples.ParentOpts{Feedback: []samples.Step{}, Allow: wideScope, Copy: oneWork}), "")
	tr.Propose(child, samples.ChildTextReview(child, "i0001", 1, wideScope, samples.Off), "")
	tr.CommitParent("tickets")
	ticketkit.OK(t, tr.Approve())
	tr.StartParent()
	tree := tr.Worktree(child, "i0001")
	ticketkit.OK(t, ccn(tr, learnOn, "ticket", "start", child))
	writeIn(t, tree, record, recordOne)
	tr.Git(tree, "add", "-f", "-A")
	tr.Git(tree, "commit", "--quiet", "-m", "rec")
	return tr
}

// notice はセッション開始の hook の出力（学びの提案の候補の知らせはユーザ向けの文に載る）。
func notice(tr *ticketkit.Tree) string {
	tr.T.Helper()
	payload, err := json.Marshal(map[string]any{"hook_event_name": "SessionStart", "cwd": tr.Root, "session_id": "s1", "source": "startup"})
	if err != nil {
		tr.T.Fatal(err)
	}
	return ticketkit.OK(tr.T, tr.Call(ticketkit.Call{Stdin: string(payload), Env: learnOn})).Stdout
}

func publish(tr *ticketkit.Tree, message string) {
	tr.T.Helper()
	tr.Git(tr.Root, "add", "-A")
	tr.Git(tr.Root, "commit", "--quiet", "-m", message)
	tr.Git(tr.Root, "push", "--quiet", "origin", "main")
	tr.Git(tr.Root, "fetch", "--quiet", "origin")
}
