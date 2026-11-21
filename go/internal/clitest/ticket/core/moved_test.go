package core

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/ticketkit"
	"ccnavi/internal/infra/ospath"
)

// commitMarkers は ccnavi 自身の置き場だけをコミットする（ccnavi-user-push-approved.sh と同じ範囲）。
func commitMarkers(tr *ticketkit.Tree, push bool) {
	tr.Parent("add", "--", ".ccnavi/approved")
	tr.Parent("commit", "--quiet", "-m", "ccnavi: 承認済みチケットを更新", "--", ".ccnavi/approved")
	if push {
		tr.Parent("push", "--quiet", "origin", ticketkit.ParentName)
	}
}

// 置き場の中の日本語のファイルが、置き場の外に見えない。
//
// 回帰: `git status --porcelain` は `-z` が無いと非 ASCII を 8 進にエスケープして引用符で包む。そのまま前置き一致に適用すると、
// 置き場の中のファイルが「ユーザの作業の汚れ」になってしまい、依頼が「未コミットの変更がある」で止まった。
func TestAJapaneseNameInTheStoreIsStillTheStore(t *testing.T) {
	t.Parallel()
	tr, _ := closedFamily(t)
	note := tr.ApprovedPath("doing", "覚え書き")
	ticketkit.Write(t, note, "a\n")
	tr.Parent("add", "--", ".ccnavi/approved")
	tr.Parent("commit", "--quiet", "-m", "置き場に日本語のファイル")
	fx := ticketkit.Fixture{T: t, Path: filepath.Join(tr.Root, "fixture.json")}
	ticketkit.Write(t, note, "b\n")
	ticketkit.OK(t, tr.Request(fx))
}

// moveRow は「何が動いたか × 経路 → 通る・止まる」の 1 行。
type moveRow struct {
	what string
	// afterRequest が真なら依頼のあとのコピーから、偽なら依頼の前から始める。
	afterRequest bool
	move         func(t *testing.T, tr *ticketkit.Tree, fx ticketkit.Fixture)
	way          string
	passes       bool
	needle       string
	then         func(t *testing.T, tr *ticketkit.Tree, fx ticketkit.Fixture)
}

func movedHead(head string) func(t *testing.T, tr *ticketkit.Tree, fx ticketkit.Fixture) {
	return func(t *testing.T, tr *ticketkit.Tree, fx ticketkit.Fixture) {
		// ユーザが見ていないコミットを積み、マーカーの head を sha でない値に書き換える。マーカーは親のブランチに乗って他の機械から
		// 届く。`HEAD` のような「今」を指す値を revision として渡すと `head..HEAD` が空差分になり、見ていないコミットがそのまま通る。
		commitCode(t, tr, "unseen.py", "誰も見ていない変更", true)
		data := ticketkit.ReadJSON(t, tr.RequestedMark())
		data["head"] = head
		raw, _ := json.Marshal(data)
		ticketkit.Write(t, tr.RequestedMark(), string(raw))
	}
}

func withOpenThread(t *testing.T, tr *ticketkit.Tree, fx ticketkit.Fixture) {
	fx.SetThreads(ticketkit.Thread{ID: "t1", URL: "u1", Body: "ここ"})
	commitMarkers(tr, true)
}

// 置き場（.ccnavi/approved）だけが動いた形と、置き場の外が動いた形を分ける。
//
//   - request: 置き場だけが手元に残っている形では止まらない。push が失敗してもコミットが残るので、そこで止めると、まだ 1 度も
//     依頼していないフェーズまで止まる。置き場の外が 1 つでも手元に残っていれば、push を求める
//   - request（依頼済み）: 置き場だけが動いた形では出し直させない（check が止まらないので、出し直しても依頼のコメントが増えるだけ）
//   - check: 置き場だけが動いた形（push 済みでも手元だけでも）では止まらない。ユーザがレビューで見るものは変わっていない。
//     置き場の外が動いていれば、置き場に見せかけたもの（改名・置き場に見える名前・sha でない head）も含めて止まる
//   - ユーザがターミナルで打つ経路（--reviewed）も check と同じ基準で見る
//
// 行ごとに、前置き（親子の承認・着手、フェーズの終わり、origin への push、依頼）から木を作り直す。行の間で状態は持ち越されない。
// sha でない head の形（`@`・`-` で始まる値・空）は Go の表（TestIsSHA・TestMovedDetail）が見る。
func TestWhatMovedDecidesWhetherRequestAndCheckPass(t *testing.T) {
	t.Parallel()
	const moved = "HEAD が動いている"
	rows := []moveRow{
		{"依頼の前: 置き場だけ・未 push", false, func(t *testing.T, tr *ticketkit.Tree, fx ticketkit.Fixture) {
			// ccnavi-user-push-approved.sh は push が失敗してもコミットを残す。ユーザの承認が失敗した形。
			ticketkit.Write(t, tr.ApprovedPath("doing", "unrelated"), "承認が落ちた形\n")
			commitMarkers(tr, false)
		}, "request", true, "", nil},
		{"依頼の前: 置き場の外・未 push", false, func(t *testing.T, tr *ticketkit.Tree, fx ticketkit.Fixture) {
			commitCode(t, tr, "later.py", "送っていない変更", false)
		}, "request", false, "push されていない", nil},
		{"置き場だけ・push 済み", true, func(t *testing.T, tr *ticketkit.Tree, fx ticketkit.Fixture) {
			// 「依頼 → マーカー → コミット」の順のせいで、依頼の直後に HEAD が必ず 1 つ進む。
			recorded := ticketkit.ReadJSON(t, tr.RequestedMark())["head"]
			commitMarkers(tr, true)
			if strings.TrimSpace(tr.Parent("rev-parse", "HEAD")) == recorded {
				t.Error("HEAD が進んでいない")
			}
		}, "check", true, "", func(t *testing.T, tr *ticketkit.Tree, fx ticketkit.Fixture) {
			if !ticketkit.Exists(tr.PhaseMark("1.reviewed")) {
				t.Error("レビュー済みにならない")
			}
		}},
		{"置き場だけ・未 push", true, func(t *testing.T, tr *ticketkit.Tree, fx ticketkit.Fixture) {
			commitMarkers(tr, false)
		}, "check", true, "", nil},
		{"置き場だけ・push 済み", true, func(t *testing.T, tr *ticketkit.Tree, fx ticketkit.Fixture) {
			commitMarkers(tr, true)
		}, "request", false, "依頼済み", func(t *testing.T, tr *ticketkit.Tree, fx ticketkit.Fixture) {
			if n := len(fx.Comments()); n != 1 {
				t.Errorf("依頼のコメントが %d 件（1 件のはず）", n)
			}
		}},
		{"置き場の外・push 済み", true, func(t *testing.T, tr *ticketkit.Tree, fx ticketkit.Fixture) {
			commitCode(t, tr, "later.py", "マーカーと一緒に", true)
		}, "check", false, moved, nil},
		{"置き場の外・head='HEAD'", true, movedHead("HEAD"), "check", false, "", nil},
		{"置き場の外・head='HEAD' で check が止まった後", true, func(t *testing.T, tr *ticketkit.Tree, fx ticketkit.Fixture) {
			// 偽の head で check が止まった後にも、出し直しの経路は残す（残さないと打つ手が無い）。
			movedHead("HEAD")(t, tr, fx)
			ticketkit.Fails(t, tr.Confirm(fx))
		}, "request", true, "", nil},
		{"置き場の外から中へ改名", true, func(t *testing.T, tr *ticketkit.Tree, fx ticketkit.Fixture) {
			// 改名を 1 行にまとめられると移動元が差分から消え、レビューされたコードからファイルが消えたことを検出できない。
			tr.Parent("mv", "src/keep.py", ".ccnavi/approved/keep.py")
			tr.Parent("commit", "--quiet", "-m", "置き場へ移す")
			tr.Parent("push", "--quiet", "origin", ticketkit.ParentName)
		}, "check", false, moved, nil},
		{"置き場に見える名前", true, func(t *testing.T, tr *ticketkit.Tree, fx ticketkit.Fixture) {
			// `.ccnavi\tickets\x.py` はルート直下のファイル 1 個で、置き場の中ではない。git が `-z` で返すパスを直しにかかると、
			// ここが除外の側に入る。
			if ospath.Windows {
				t.Skip("名前に \\ を含むファイルを作れない機械")
			}
			ticketkit.Write(t, filepath.Join(tr.ParentTree, `.ccnavi\tickets\x.py`), "x\n")
			tr.Parent("add", "-A")
			tr.Parent("commit", "--quiet", "-m", "置き場に見える名前")
			tr.Parent("push", "--quiet", "origin", ticketkit.ParentName)
		}, "check", false, "", nil},
		{"置き場だけ・未解決の指摘あり", true, withOpenThread, "reviewed", true, "", nil},
		{"ユーザが受け入れた後に置き場の外", true, func(t *testing.T, tr *ticketkit.Tree, fx ticketkit.Fixture) {
			// ユーザの経路で一度受け入れた後に、見ていない変更を積んで受け入れをやり直す。
			withOpenThread(t, tr, fx)
			ticketkit.OK(t, tr.Reviewed(fx, "k\n"))
			commitCode(t, tr, "unseen.py", "誰も見ていない変更", true)
			if err := os.Remove(tr.PhaseMark("1.reviewed")); err != nil {
				t.Fatal(err)
			}
		}, "reviewed", false, moved, nil},
	}
	for _, row := range rows {
		t.Run(row.what+"・"+row.way, func(t *testing.T) {
			t.Parallel()
			tr, fx := closedFamily(t)
			if row.afterRequest {
				ticketkit.OK(t, tr.Request(fx))
			}
			row.move(t, tr, fx)
			way := map[string]func() (int, string, string){
				"request": func() (int, string, string) { x := tr.Request(fx); return x.Code, x.Stdout, x.Stderr },
				"check":   func() (int, string, string) { x := tr.Confirm(fx); return x.Code, x.Stdout, x.Stderr },
				"reviewed": func() (int, string, string) {
					x := tr.Reviewed(fx, "k\n")
					return x.Code, x.Stdout, x.Stderr
				},
			}[row.way]
			code, stdout, stderr := way()
			if row.passes && code != 0 {
				t.Errorf("通るはずが終了コード %d\n%s%s", code, stdout, stderr)
			}
			if !row.passes && code == 0 {
				t.Errorf("止まるはずが通った\n%s%s", stdout, stderr)
			}
			if row.needle != "" {
				ticketkit.Has(t, stderr, row.needle)
			}
			if row.then != nil {
				row.then(t, tr, fx)
			}
		})
	}
}

// submodule の進みを、置き場の中だけの変更と数えない。
//
// `.gitmodules` は追跡されるファイルで、そこに `ignore = all` があると `git diff` は gitlink の変更を 1 行も出さない。
// 設定は他の機械から届く。
func TestCheckRefusesASubmoduleThatMoved(t *testing.T) {
	t.Parallel()
	tr := ticketkit.ClosedPhaseTree(t, ticketkit.FamilyOpts{})
	sub := newSubmoduleSource(t, tr)
	if _, err := ticketkit.TryGit(tr.ParentTree, "-c", "protocol.file.allow=always", "submodule", "add", "--quiet", sub, "sub"); err != nil {
		t.Skipf("submodule を作れない機械（%v）", err)
	}
	appendLine(t, filepath.Join(tr.ParentTree, ".gitmodules"), "\tignore = all\n")
	tr.CommitParent("submodule を足す")
	fx := tr.Remote(nil)
	ticketkit.OK(t, tr.Request(fx))
	ticketkit.Write(t, filepath.Join(sub, "f.txt"), "b\n")
	ticketkit.Git(t, sub, "commit", "--quiet", "-am", "s2")
	tr.Git(filepath.Join(tr.ParentTree, "sub"), "pull", "--quiet", "origin", "main")
	tr.Parent("commit", "--quiet", "-am", "submodule を進める")
	tr.Parent("push", "--quiet", "origin", ticketkit.ParentName)
	check := ticketkit.Fails(t, tr.Confirm(fx))
	ticketkit.Has(t, check.Stderr, "HEAD が動いている")
}

// newSubmoduleSource は submodule の元になるリポジトリを tr.Root/sub に作る。
func newSubmoduleSource(t *testing.T, tr *ticketkit.Tree) string {
	t.Helper()
	sub := filepath.Join(tr.Root, "sub")
	if err := os.MkdirAll(sub, 0o755); err != nil {
		t.Fatal(err)
	}
	ticketkit.Git(t, sub, "init", "--quiet", "-b", "main", ".")
	ticketkit.Write(t, filepath.Join(sub, "f.txt"), "a\n")
	ticketkit.Git(t, sub, "add", "-A")
	ticketkit.Git(t, sub, "commit", "--quiet", "-m", "s1")
	return sub
}

func appendLine(t *testing.T, path, text string) {
	t.Helper()
	f, err := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	if _, err := f.WriteString(text); err != nil {
		t.Fatal(err)
	}
}
