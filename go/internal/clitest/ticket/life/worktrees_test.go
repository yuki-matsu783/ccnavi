package life

import (
	"fmt"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/samples"
	"ccnavi/internal/harness/ticketkit"
)

// クローズした子と Draft を外した親のワークツリーの片付け（`ccnavi worktree drop|tidy|remaining`）と、レビューを頼む前後の案内
// （finish の出力・status の次の一手・最後の confirm の知らせ）。

// plainParentText は計画が work だけの親（定義は PlainPhases）。レビューの要否は定義と子の宣言で決まる。
func plainParentText(name string, plan ...string) string {
	if len(plan) == 0 {
		plan = []string{"work"}
	}
	return samples.ParentText(name, samples.Plan(plan...), samples.ParentOpts{Feedback: []samples.Step{}, Allow: []string{"src/*"}, Copy: samples.PlainPhases})
}

// plainTree は PlainPhases の木で、計画 plan の親 i0001 を提案して承認したもの。
func plainTree(t *testing.T, plan ...string) *ticketkit.Tree {
	t.Helper()
	tr := ticketkit.New(t)
	tr.Propose("i0001", plainParentText("i0001", plan...), "")
	tr.CommitParent("tickets")
	ticketkit.OK(t, tr.Approve())
	return tr
}

func approveChild(tr *ticketkit.Tree, name string, review samples.Flag) {
	tr.T.Helper()
	tr.Propose(name, samples.ChildTextReview(name, "i0001", 1, []string{"src/*"}, review), "")
	tr.CommitParent("approval-request " + name)
	ticketkit.OK(tr.T, tr.Approve())
}

// closedChild は子を提案・承認・着手してクローズし、親へ合流する。子のワークツリーと finish の結果を返す。
func closedChild(tr *ticketkit.Tree, name string) (string, run.Result) {
	tr.T.Helper()
	approveChild(tr, name, samples.On)
	tree := tr.RunChild(name, ticketkit.File{Path: "src/" + name + ".py", Text: "x = 1\n"})
	closed := ticketkit.OK(tr.T, tr.Ccnavi("ticket", "finish", name))
	tr.CommitParent("close " + name)
	tr.Merge(name)
	return tree, closed
}

// nextStep は ticket status の子 name の次の操作の欄。
func nextStep(tr *ticketkit.Tree, name string) string {
	tr.T.Helper()
	lines := ticketkit.Lines(ticketkit.OK(tr.T, tr.Ccnavi("ticket", "status", "i0001")).Stdout)
	for i, line := range lines {
		if !strings.HasPrefix(line, "- "+name+" ") {
			continue
		}
		var rest []string
		for _, l := range lines[i+1:] {
			if strings.HasPrefix(l, "- ") {
				break
			}
			rest = append(rest, l)
		}
		return strings.Join(rest, "\n")
	}
	tr.T.Fatalf("status に %s が無い", name)
	return ""
}

func tidy(tr *ticketkit.Tree, cwd string, extra ...string) run.Result {
	tr.T.Helper()
	if cwd == "" {
		cwd = tr.Root
	}
	return tr.Ccnavi(append([]string{"--cwd", cwd, "worktree", "tidy", "i0001"}, extra...)...)
}

func branchExists(tr *ticketkit.Tree, name string) bool {
	tr.T.Helper()
	return strings.TrimSpace(tr.Git(tr.Root, "branch", "--list", name)) == name
}

// finish で子を review/ へ動かしただけでは「ユーザのレビューを待つ」と言わない。依頼の前は「レビュー準備中」と親の次の一手（request）を言い、
// 依頼の後は待つと言う。依頼の後に子を足してクローズしたら、またレビュー準備中と言う。status の次の一手も同じ。
func TestFinishAndStatusSayWhoMovesBeforeAndAfterTheRequest(t *testing.T) {
	t.Parallel()
	tr := plainTree(t, "work-mr")
	tree, closed := closedChild(tr, "i0001-01-01")
	// 依頼の前。ユーザには回っていない。
	ticketkit.Has(t, closed.Stdout, "レビュー準備中")
	ticketkit.Has(t, closed.Stdout, "review-request --phase 1")
	ticketkit.Lacks(t, closed.Stdout, "ユーザのレビューを待つ")
	// 誰が done/ へ動かすかは 1 度だけ言う（重ねない）。
	if n := strings.Count(closed.Stdout, "done/ へ動"); n != 1 {
		t.Errorf("done/ へ動 が %d 回: %s", n, closed.Stdout)
	}
	// レビュー待ち（review/）の子のワークツリーは、見返す場所として片付けで残る。
	if r := ticketkit.OK(t, tidy(tr, "")); r.Stdout != "" {
		t.Errorf("tidy の出力 = %q", r.Stdout)
	}
	if !ticketkit.Exists(tree) {
		t.Error("レビュー待ちの子のワークツリーを消した")
	}
	before := nextStep(tr, "i0001-01-01")
	ticketkit.Has(t, before, "次の操作: まだレビュー準備中")
	ticketkit.Has(t, before, "メインエージェントのみ実行可能")
	f := tr.Origin()
	ticketkit.OK(t, tr.Request(f))
	// 依頼の後。ここで初めてユーザを待つ。
	after := nextStep(tr, "i0001-01-01")
	ticketkit.Has(t, after, "依頼済み（レビュー待ち）")
	ticketkit.Has(t, after, "ユーザのレビューを待つ")
	// 依頼の後に同じフェーズへ子を足してクローズした。依頼の記録は外れ、またレビュー準備中になる。
	_, again := closedChild(tr, "i0001-01-02")
	ticketkit.Has(t, again.Stdout, "レビュー準備中")
	if n := strings.Count(again.Stdout, "done/ へ動"); n != 1 {
		t.Errorf("done/ へ動 が %d 回: %s", n, again.Stdout)
	}
	ticketkit.Lacks(t, again.Stdout, "ユーザのレビューを待つ")
	ticketkit.Has(t, nextStep(tr, "i0001-01-02"), "レビュー準備中")
}

func TestTheLastConfirmTellsTheWayToReadyAndTidiesTheChildren(t *testing.T) {
	t.Parallel()
	tr := plainTree(t, "work-mr")
	tree, _ := closedChild(tr, "i0001-01-01")
	f := tr.Origin()
	ticketkit.OK(t, tr.Request(f))
	confirmed := ticketkit.OK(t, tr.Confirm(f))
	for _, w := range []string{"全部のフェーズのレビューが済み", "finish i0001", "ready", "`wip/` の追跡済みのファイル", "ready --parent i0001",
		// push と Draft 解除はレビュー済みの合意の範囲に入ると言い切る。
		"合意した範囲に入る", "確認を取り直さずに進める"} {
		ticketkit.Has(t, confirmed.Stdout, w)
	}
	// confirm の後に sh が打つ片付け。done/ に動いた子のワークツリーを消し、ブランチは残す。
	done := ticketkit.OK(t, tidy(tr, "", "--phase", "1"))
	ticketkit.Has(t, done.Stdout, "ワークツリー i0001-01-01 を削除しました")
	if ticketkit.Exists(tree) {
		t.Error("子のワークツリーが残った")
	}
	if !branchExists(tr, "i0001-01-01") {
		t.Error("子のブランチを消した")
	}
	// もう一度打っても何も起きない。
	if again := ticketkit.OK(t, tidy(tr, "", "--phase", "1")); again.Stdout != "" {
		t.Errorf("打ち直しの出力 = %q", again.Stdout)
	}
}

// 計画の 2 番目のフェーズが残っているなら、最後の confirm の知らせは出ない（子は 1 番目のレビューが済むまで承認できない）。
func TestAConfirmThatIsNotTheLastSaysNothingMore(t *testing.T) {
	t.Parallel()
	tr := plainTree(t, "work-mr", "work-mr")
	closedChild(tr, "i0001-01-01")
	f := tr.Origin()
	ticketkit.OK(t, tr.Request(f))
	tr.Parent("push", "--quiet", "origin", "i0001")
	confirmed := ticketkit.OK(t, tr.Confirm(f))
	ticketkit.Lacks(t, confirmed.Stdout, "全部のフェーズのレビューが済み")
}

// ---- tidy（クローズした子のワークツリーの片付け。消さないときは名指しする）

func TestTidyRemovesTheWorktreesOfCancelledChildrenButNotOfDoingOnes(t *testing.T) {
	t.Parallel()
	tr := plainTree(t)
	for _, name := range []string{"i0001-01-01", "i0001-01-02"} {
		tr.Propose(name, samples.ChildText(name, "i0001", 1, []string{"src/*"}), "")
	}
	tr.CommitParent("tickets")
	ticketkit.OK(t, tr.Approve())
	doing := tr.RunChild("i0001-01-01")
	gone := tr.RunChild("i0001-01-02")
	ticketkit.OK(t, tr.Ccnavi("ticket", "cancel", "i0001-01-02", "--reason", "やめた"))
	done := ticketkit.OK(t, tidy(tr, ""))
	// 片付けの候補はクローズした（done/ の）子だけ。作業中の子と取り消した子が並んでいるとき、候補に入るのは取り消した子だけ。
	ticketkit.Has(t, done.Stdout, "ワークツリー i0001-01-02 を削除しました")
	ticketkit.Lacks(t, done.Stdout, "i0001-01-01")
	if !ticketkit.Exists(doing) || ticketkit.Exists(gone) {
		t.Error("ワークツリーの残り方が合わない")
	}
	if !branchExists(tr, "i0001-01-02") {
		t.Error("取り消した子のブランチを消した")
	}
}

func TestAClosedChildWithUncommittedChangesIsKeptWithAStatusHint(t *testing.T) {
	t.Parallel()
	tr := plainTree(t)
	approveChild(tr, "i0001-01-01", samples.Off)
	tree := tr.RunChild("i0001-01-01", ticketkit.File{Path: "src/a.py", Text: "x = 1\n"})
	ticketkit.OK(t, tr.Ccnavi("ticket", "finish", "i0001-01-01"))
	left := writeIn(t, tree, "src/left.py", "y = 2\n")
	r := tidy(tr, "")
	if r.Code != 1 {
		t.Fatalf("code %d\n%s%s", r.Code, r.Stdout, r.Stderr)
	}
	ticketkit.Has(t, r.Stdout, "未コミットの変更があるので、何も削除しませんでした。")
	ticketkit.Has(t, r.Stdout, "status で確かめてもらう")
	if !ticketkit.Exists(left) {
		t.Error("未コミットのファイルが消えた")
	}
	// cwd が中にあれば、未コミットを見る前に止める。
	mustRemove(t, left)
	inside := tidy(tr, filepath.Join(tree, "src"))
	if inside.Code != 1 {
		t.Errorf("code %d", inside.Code)
	}
	ticketkit.Has(t, inside.Stdout, "cwd がワークツリー i0001-01-01 の中にある")
	if !ticketkit.Exists(tree) {
		t.Error("cwd が中にあるのに消した")
	}
	// 外から打てば消える。
	ticketkit.OK(t, tidy(tr, ""))
	if ticketkit.Exists(tree) {
		t.Error("外から打っても消えない")
	}
}

// 親の識別子が子の形（-<2 桁>-<2 桁>）で終わっていても、子の識別子から親を読み違えない。
func TestTidyByTheParentIdDoesNotStripItsTail(t *testing.T) {
	t.Parallel()
	parent := "rel-2026-10-06"
	child := parent + "-01-01"
	tr := ticketkit.New(t)
	tr.ParentTree = tr.Worktree(parent, "main")
	tr.Approved = filepath.Join(tr.ParentTree, ".ccnavi", "approved")
	tr.Propose(parent, plainParentText(parent), "")
	tr.CommitParent("tickets")
	ticketkit.OK(t, tr.Approve())
	tr.Propose(child, samples.ChildText(child, parent, 1, []string{"src/*"}), "")
	tr.CommitParent("tickets")
	ticketkit.OK(t, tr.Approve())
	tr.StartParent(parent)
	tree := tr.Worktree(child, parent)
	ticketkit.OK(t, tr.Ccnavi("ticket", "start", child))
	ticketkit.OK(t, tr.Ccnavi("ticket", "cancel", child, "--reason", "やめた"))
	// 親の finish の後と同じ形（親の識別子で頼む）。名前の形で末尾を剥がすと rel-2026 になる。
	done := ticketkit.OK(t, tr.Ccnavi("--cwd", tr.Root, "worktree", "tidy", parent))
	ticketkit.Has(t, done.Stdout, "ワークツリー "+child+" を削除しました")
	if ticketkit.Exists(tree) {
		t.Error("子のワークツリーが残った")
	}
}

// ---- drop（生成物を消してから git worktree remove。消さないときは理由を言う）

func drop(tr *ticketkit.Tree, name, cwd string) run.Result {
	tr.T.Helper()
	if cwd == "" {
		cwd = tr.Root
	}
	return tr.Ccnavi("--cwd", cwd, "worktree", "drop", name)
}

// dropTree は main から i0002 のワークツリーを切り、files（相対パス → 中身。追跡するもの）をコミットする。
func dropTree(tr *ticketkit.Tree, files map[string]string) string {
	tr.T.Helper()
	tree := tr.Worktree("i0002", "main")
	for rel, text := range files {
		writeIn(tr.T, tree, rel, text)
	}
	tr.Git(tree, "add", "-A")
	tr.Git(tree, "commit", "--quiet", "-m", "setup")
	return tree
}

func TestDropRemovesTheWorktreeAfterCleaningTheBuildOutputs(t *testing.T) {
	t.Parallel()
	tr := ticketkit.New(t)
	tree := dropTree(tr, map[string]string{".gitignore": "node_modules/\nout/\ndist/\n.cache/\n.venv/\n__pycache__/\n", "ext/package.json": "{}"})
	for _, rel := range []string{"node_modules/.pnpm/x/index.js", "ext/out/extension.js", "ext/dist/bundle.js", "ext/.cache/c", ".venv/pyvenv.cfg"} {
		writeIn(t, tree, rel, "")
	}
	dropped := ticketkit.OK(t, drop(tr, "i0002", ""))
	ticketkit.Has(t, dropped.Stdout, "ワークツリー i0002 を削除しました")
	ticketkit.Has(t, dropped.Stdout, "ブランチは残してある")
	if ticketkit.Exists(tree) || !branchExists(tr, "i0002") {
		t.Error("ワークツリーとブランチの残り方が合わない")
	}
	ticketkit.Lacks(t, tr.Git(tr.Root, "worktree", "list"), "i0002")
	// 無いものを消すのは成功（打ち直し）。
	ticketkit.Has(t, ticketkit.OK(t, drop(tr, "i0002", "")).Stdout, "ありません")
}

// git が無視している .env などは worktree remove が黙って消すので、消さずに scratchpad/worktrees/<名前>/ へ退避する。生成物
// （node_modules・Cargo.toml の隣の target/ など）は消す。印の無い out/ は退避する。ワークツリー直下の scratchpad/ も特別扱いしない。
func TestDropMovesIgnoredFilesToTheScratchpadBeforeRemoving(t *testing.T) {
	t.Parallel()
	for _, c := range []struct {
		name      string
		tracked   map[string]string
		ignored   map[string]string
		kept      map[string]string
		gone      []string
		stdoutHas []string
		stdoutNot []string
	}{
		{"env_file_kept_and_node_modules_dropped",
			map[string]string{".gitignore": ".env\nnode_modules/\n"},
			map[string]string{"node_modules/x/index.js": "", ".env": "TOKEN=x\n"},
			map[string]string{".env": "TOKEN=x\n"}, []string{"node_modules"},
			[]string{"scratchpad/worktrees/i0002 へ退避しました", ".env"}, []string{"node_modules"}},
		{"cargo_target_dropped_and_out_kept",
			map[string]string{".gitignore": "target/\nout/\n", "data-schema/Cargo.toml": "[package]\n"},
			map[string]string{"data-schema/target/debug/x": "", "data-schema/out/key_experiment.md": "記録\n"},
			map[string]string{"data-schema/out/key_experiment.md": "記録\n"}, []string{"data-schema/target"}, nil, nil},
		{"a_scratchpad_dir_in_the_worktree_is_like_any_ignored_file",
			map[string]string{".gitignore": "scratchpad/\n"},
			map[string]string{"scratchpad/memo.md": "メモ\n"},
			map[string]string{"scratchpad/memo.md": "メモ\n"}, nil, nil, nil},
	} {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			tr := ticketkit.New(t)
			// 退避先（ワークスペースルートの scratchpad/）を git に無視させる。
			ticketkit.Write(t, filepath.Join(tr.Root, ".gitignore"), "/scratchpad\n")
			tree := dropTree(tr, c.tracked)
			for rel, text := range c.ignored {
				writeIn(t, tree, rel, text)
			}
			dropped := ticketkit.OK(t, drop(tr, "i0002", ""))
			for _, w := range c.stdoutHas {
				ticketkit.Has(t, dropped.Stdout, w)
			}
			for _, w := range c.stdoutNot {
				ticketkit.Lacks(t, dropped.Stdout, w)
			}
			if ticketkit.Exists(tree) {
				t.Error("ワークツリーが残った")
			}
			stash := filepath.Join(tr.Root, "scratchpad", "worktrees", "i0002")
			for rel, text := range c.kept {
				if got := readIn(t, stash, rel); got != text {
					t.Errorf("退避した %s = %q", rel, got)
				}
			}
			for _, rel := range c.gone {
				if existsIn(stash, rel) {
					t.Errorf("生成物 %s を退避した", rel)
				}
			}
		})
	}
}

// 退避先が git に無視されていないと .env がコミットされうるので、何も動かさずに止める。
func TestDropKeepsEverythingWhenTheStashDirIsNotIgnored(t *testing.T) {
	t.Parallel()
	tr := ticketkit.New(t)
	tree := dropTree(tr, map[string]string{".gitignore": ".env\n"})
	env := writeIn(t, tree, ".env", "TOKEN=x\n")
	dropped := drop(tr, "i0002", "")
	if dropped.Code != 1 {
		t.Fatalf("code %d\n%s%s", dropped.Code, dropped.Stdout, dropped.Stderr)
	}
	ticketkit.Has(t, dropped.Stdout, "/scratchpad")
	if !ticketkit.Exists(env) || ticketkit.Exists(filepath.Join(tr.Root, "scratchpad")) {
		t.Error("何も動かさないはずが動いた")
	}
}

// 退避先に同じパスがあれば上書きせず止める（前の退避を失わない）。
func TestDropDoesNotOverwriteAnExistingStash(t *testing.T) {
	t.Parallel()
	tr := ticketkit.New(t)
	ticketkit.Write(t, filepath.Join(tr.Root, ".gitignore"), "/scratchpad\n")
	tree := dropTree(tr, map[string]string{".gitignore": ".env\n"})
	env := writeIn(t, tree, ".env", "NEW\n")
	old := ticketkit.Write(t, filepath.Join(tr.Root, "scratchpad", "worktrees", "i0002", ".env"), "OLD\n")
	dropped := drop(tr, "i0002", "")
	if dropped.Code != 1 {
		t.Fatalf("code %d\n%s%s", dropped.Code, dropped.Stdout, dropped.Stderr)
	}
	ticketkit.Has(t, dropped.Stdout, "退避できませんでした")
	if ticketkit.Read(t, old) != "OLD\n" || !ticketkit.Exists(env) {
		t.Error("前の退避か元のファイルを失った")
	}
}

// 生成物の名前でも追跡されているもの（package.json の隣のコミット済みの out/ など）は消さない。消すと worktree remove が通らず、
// 破損したツリーが残る。
func TestDropDoesNotCleanTrackedBuildOutputNames(t *testing.T) {
	t.Parallel()
	tr := ticketkit.New(t)
	tree := dropTree(tr, map[string]string{"ext/package.json": "{}", "ext/out/kept.js": "x\n"})
	ticketkit.OK(t, drop(tr, "i0002", ""))
	if ticketkit.Exists(tree) {
		t.Error("ワークツリーが残った")
	}
	ticketkit.Lacks(t, tr.Git(tr.Root, "worktree", "list"), "i0002")
}

// git worktree remove が断る（ロックされた）ワークツリーは残り、git の拒否文を伝える。生成物は先に消えるが、作り直せるので失うものは無い。
func TestALockedWorktreeIsKeptWithTheRefusalOfGit(t *testing.T) {
	t.Parallel()
	tr := ticketkit.New(t)
	tree := dropTree(tr, map[string]string{".gitignore": "node_modules/\n"})
	writeIn(t, tree, "node_modules/x.js", "")
	tr.Git(tr.Root, "worktree", "lock", tree)
	dropped := drop(tr, "i0002", "")
	if dropped.Code != 1 {
		t.Fatalf("code %d\n%s%s", dropped.Code, dropped.Stdout, dropped.Stderr)
	}
	ticketkit.Has(t, dropped.Stdout, "git worktree remove が失敗した")
	ticketkit.Has(t, dropped.Stdout, "locked")
	if !existsIn(tree, ".gitignore") {
		t.Error("ロックされたワークツリーが壊れた")
	}
	ticketkit.Has(t, tr.Git(tr.Root, "worktree", "list"), "i0002")
}

func TestDropCutsShortAListOfManyMovedFiles(t *testing.T) {
	t.Parallel()
	tr := ticketkit.New(t)
	ticketkit.Write(t, filepath.Join(tr.Root, ".gitignore"), "/scratchpad\n")
	tree := dropTree(tr, map[string]string{".gitignore": "*.log\n"})
	for i := 0; i < 15; i++ {
		writeIn(t, tree, fmt.Sprintf("a%02d.log", i), "")
	}
	ticketkit.Has(t, ticketkit.OK(t, drop(tr, "i0002", "")).Stdout, "…ほか 5 件")
}

func TestDropKeepsTheWorktreeWhenTheCwdIsInsideOrItHasUntrackedFiles(t *testing.T) {
	t.Parallel()
	t.Run("cwd_inside", func(t *testing.T) {
		t.Parallel()
		tr := ticketkit.New(t)
		tree := tr.Worktree("i0002", "main")
		dropped := drop(tr, "i0002", tree)
		if dropped.Code != 1 {
			t.Fatalf("code %d", dropped.Code)
		}
		ticketkit.Has(t, dropped.Stdout, "cwd がワークツリー i0002 の中にあるので削除しませんでした")
		ticketkit.Has(t, dropped.Stdout, "ccnavi-clean.sh --worktree i0002")
		// 分類器に止められたときの道（ユーザに頼む・ホームの設定に絞った許可を足す）も言う。
		ticketkit.Has(t, dropped.Stdout, "~/.claude/settings.json")
		ticketkit.Has(t, dropped.Stdout, "ccnavi-git.sh *")
		if !ticketkit.Exists(tree) {
			t.Error("消えた")
		}
	})
	t.Run("untracked_files", func(t *testing.T) {
		t.Parallel()
		tr := ticketkit.New(t)
		tree := tr.Worktree("i0002", "main")
		draft := writeIn(t, tree, "draft.txt", "書きかけ\n")
		dropped := drop(tr, "i0002", "")
		if dropped.Code != 1 {
			t.Fatalf("code %d", dropped.Code)
		}
		ticketkit.Has(t, dropped.Stdout, "git -C "+tree+" status")
		if !ticketkit.Exists(draft) {
			t.Error("書きかけが消えた")
		}
	})
}

func TestDropRefusesWhatIsNotAWorktreeAndNamesThatArePaths(t *testing.T) {
	t.Parallel()
	tr := ticketkit.New(t)
	keep := writeIn(t, filepath.Join(tr.Root, ".claude", "worktrees", "plain"), "node_modules/keep.js", "")
	dropped := drop(tr, "plain", "")
	if dropped.Code != 1 {
		t.Fatalf("code %d", dropped.Code)
	}
	ticketkit.Has(t, dropped.Stdout, "登録されていない")
	if !ticketkit.Exists(keep) {
		t.Error("登録されていないディレクトリの中身を消した")
	}
	for _, name := range []string{"..", "a/b", `a\b`} {
		if r := drop(tr, name, ""); r.Code == 0 {
			t.Errorf("%q を通した", name)
		}
	}
	if !ticketkit.Exists(filepath.Join(tr.Root, ".claude", "worktrees")) {
		t.Error("worktrees の置き場が消えた")
	}
}

// ---- remaining（ready の後に残った親子のワークツリーを、消さずに一覧にする）

func remaining(tr *ticketkit.Tree) run.Result {
	tr.T.Helper()
	return tr.Ccnavi("--cwd", tr.Root, "worktree", "remaining", "i0001")
}

func TestRemainingSaysNothingWhenNothingIsLeft(t *testing.T) {
	t.Parallel()
	tr := plainTree(t)
	if r := ticketkit.OK(t, remaining(tr)); r.Stdout != "" {
		t.Errorf("出力 = %q", r.Stdout)
	}
}

func TestRemainingListsAWorktreeNamedDifferentlyThatHoldsTheParentsTickets(t *testing.T) {
	t.Parallel()
	tr := plainTree(t)
	tree := tr.Worktree("work-a", "i0001")
	memo := writeIn(t, tree, "wip/handoff/memo.md", "メモ\n")
	listed := ticketkit.OK(t, remaining(tr))
	for _, w := range []string{"- work-a（", "名前が親 i0001 と違う", "未保存の下書きがある（wip/handoff/）", "削除する前に確かめること", "ccnavi-clean.sh --worktree work-a"} {
		ticketkit.Has(t, listed.Stdout, w)
	}
	// 出すだけで消さない。
	if !ticketkit.Exists(memo) {
		t.Error("消した")
	}
}

func TestRemainingListsAClosedChildKeptForAnUnsavedDraftAndNotACleanOne(t *testing.T) {
	t.Parallel()
	tr := plainTree(t)
	for _, name := range []string{"i0001-01-01", "i0001-01-02"} {
		tr.Propose(name, samples.ChildTextReview(name, "i0001", 1, []string{"src/*"}, samples.Off), "")
	}
	tr.CommitParent("tickets")
	ticketkit.OK(t, tr.Approve())
	dirty := tr.RunChild("i0001-01-01", ticketkit.File{Path: "src/a.py", Text: "x = 1\n"})
	clean := tr.RunChild("i0001-01-02", ticketkit.File{Path: "src/b.py", Text: "y = 1\n"})
	for _, name := range []string{"i0001-01-01", "i0001-01-02"} {
		ticketkit.OK(t, tr.Ccnavi("ticket", "finish", name))
	}
	memo := writeIn(t, dirty, "wip/handoff/memo.md", "メモ\n")
	listed := ticketkit.OK(t, remaining(tr))
	for _, w := range []string{"- i0001-01-01（", "未保存の下書きがある（wip/handoff/）", "ccnavi-clean.sh --worktree i0001-01-01"} {
		ticketkit.Has(t, listed.Stdout, w)
	}
	ticketkit.Lacks(t, listed.Stdout, "i0001-01-02")
	if !ticketkit.Exists(clean) || !ticketkit.Exists(memo) {
		t.Error("消した")
	}
}
