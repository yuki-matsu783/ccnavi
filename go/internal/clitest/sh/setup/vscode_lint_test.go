package setup

import (
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

// VS Code へ渡す設定（docs/50_guide/install.md「VS Code からワークツリーを見る」と対）。ccnavi は作業を .claude/worktrees/ の中でさせる。
// この 1 行が無いと、エディタからはワークスペースルートのブランチしか見えないまま作業が進む。

func vscodeText(w *target) string { return read(w.t, w.vscodePath()) }

func TestVscodeCreatesTheFileWhenItIsNotThere(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	code(t, w.setup(), 0)
	if got := w.vscode(); !reflect.DeepEqual(got, jsonObj{"git.detectWorktrees": true, "git.repositoryScanMaxDepth": float64(2)}) {
		t.Errorf(".vscode/settings.json = %v", got)
	}
}

// VS Code の他の設定は残す。バックアップも 1 つ取る。打ち直しても何も変えない（バックアップも取り直さない）。
func TestVscodeKeepsOtherSettingsAndRunningTwiceChangesNothing(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	writeJSON(t, w.vscodePath(), jsonObj{"editor.tabSize": 2})
	code(t, w.setup(), 0)
	landed := w.vscode()
	eq(t, "editor.tabSize", landed["editor.tabSize"], any(float64(2)))
	eq(t, "git.detectWorktrees", landed["git.detectWorktrees"], any(true))
	eq(t, "git.repositoryScanMaxDepth", landed["git.repositoryScanMaxDepth"], any(float64(2)))
	if !exists(w.vscodePath() + ".bak") {
		t.Error("バックアップを取っていない")
	}

	w2 := newTarget(t)
	w2.setup()
	before := vscodeText(w2)
	code(t, w2.setup(), 0)
	eq(t, "打ち直したあと", vscodeText(w2), before)
	if exists(w2.vscodePath() + ".bak") {
		t.Error("打ち直しでバックアップを取った")
	}
}

// `false` と書いたユーザの判断を消さない。並べて見せるだけ（--check では揃っていない）。
func TestVscodeKeepsAValueThePersonWrote(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	writeJSON(t, w.vscodePath(), jsonObj{"git.detectWorktrees": false})
	r := w.setup()
	code(t, r, 0)
	eq(t, "git.detectWorktrees", w.vscode()["git.detectWorktrees"], any(false))
	in(t, r.Stdout, "git.detectWorktrees")
	code(t, w.setup("--check"), 1)
}

// VS Code の設定ファイルはコメントを書ける（JSONC）。jq は読めない。ここで止まると、ccnavi と関係のない書き方のせいで .claude/settings.json まで
// 書けなくなる。触らずにユーザへ渡して、残りは進める。
func TestVscodeDoesNotTouchAFileItCannotRead(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	jsonc := "{\n  // worktree は見せない\n  \"git.detectWorktrees\": false\n}\n"
	write(t, w.vscodePath(), jsonc)
	r := w.setup()
	code(t, r, 0)
	eq(t, ".vscode/settings.json", vscodeText(w), jsonc)
	in(t, r.Stdout, ".vscode/settings.json", "--no-vscode")
	if _, ok := w.envOf()["CCNAVI_ENTRY_PATH"]; !ok {
		t.Error("肝心の登録が済んでいない")
	}
}

func TestVscodeNoVscodeLeavesItAlone(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	code(t, w.setup("--no-vscode"), 0)
	if exists(w.vscodePath()) {
		t.Error("--no-vscode が書いた")
	}
	code(t, w.setup("--check", "--no-vscode"), 0)
}

func TestVscodeCheckIsNotSettledWhenTheLineIsMissing(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	w.setup("--no-vscode")
	r := w.setup("--check")
	code(t, r, 1)
	in(t, r.Stdout, "git.detectWorktrees")
	if exists(w.vscodePath()) {
		t.Error("--check が書いた")
	}
}

// ---- 入れ終わりに見せる `--lint`

// 入れ終わりに、配った振り分けの sh を通して `--lint` を 1 回起こし、結果をそのまま見せる。`projects/` の索引の知らせなどの判定と文面は
// 実行ファイルだけが持ち、導入スクリプトは自分で判定しない。止めず、終了コードも変えない。`--check` と、振り分けの sh がまだ無いときは
// 起こさず、確かめ方の 1 行だけを出す。

const (
	lintHeading = "sh " + binPath + " --lint の結果:"
	lintLater   = "案内: 導入したあとに、ワークスペースルートで sh " + binPath + " --lint を実行して"
)

// sourceWithExe は配る実行ファイルを、body の sh にした配布元。
func sourceWithExe(t *testing.T, body string) string {
	t.Helper()
	src := makeSource(t, srcOpts{})
	write(t, filepath.Join(src, "dist", "ccnavi", "ccnavi"), body)
	return src
}

func lintCalls(t *testing.T, log string) []string {
	t.Helper()
	if !exists(log) {
		return nil
	}
	return strings.Split(strings.TrimRight(read(t, log), "\n"), "\n")
}

func TestLintRunsOnceAtTheEndAndShowsItAsIsAndCheckDoesNotRunIt(t *testing.T) {
	t.Parallel()
	notWindows(t) // スタブの実行ファイルは sh で書く
	t.Run("入れ終わりに 1 回起こす。終了コードは変えない", func(t *testing.T) {
		t.Parallel()
		w := newTarget(t)
		log := w.path("lint-calls.txt")
		// 終了コードを 3 にして、導入スクリプトの終了コードが変わらないことも見る。
		src := sourceWithExe(t, "#!/bin/sh\nprintf '%s\\n' \"$*\" >>'"+log+"'\nprintf 'LINT-OUT\\n'\nexit 3\n")
		r := w.setup("--deploy", src, "--mode", "enable")
		code(t, r, 0)
		if !strings.HasSuffix(r.Stdout, lintHeading+"\nLINT-OUT\n") {
			t.Errorf("出力の終わり:\n%s", r.Stdout)
		}
		if got := lintCalls(t, log); !reflect.DeepEqual(got, []string{"--root " + w.dir + " --lint"}) {
			t.Errorf("lint の呼び出し = %v", got)
		}
	})
	t.Run("--check は起こさず、確かめ方の 1 行だけを出す", func(t *testing.T) {
		t.Parallel()
		w := newTarget(t)
		log := w.path("lint-calls.txt")
		src := sourceWithExe(t, "#!/bin/sh\nprintf '%s\\n' \"$*\" >>'"+log+"'\n")
		w.setup("--deploy", src, "--mode", "enable")
		before := lintCalls(t, log)
		checked := w.setup("--deploy", src, "--mode", "enable", "--check")
		in(t, checked.Stdout, lintLater)
		notIn(t, checked.Stdout, lintHeading)
		if !reflect.DeepEqual(lintCalls(t, log), before) {
			t.Error("--check が lint を起こした")
		}
	})
}

func TestLintWithoutTheLauncherItOnlySaysHowToCheck(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	r := w.setup("--no-deploy", "--mode", "enable")
	code(t, r, 0)
	in(t, r.Stdout, lintLater)
	notIn(t, r.Stdout, lintHeading)
}
