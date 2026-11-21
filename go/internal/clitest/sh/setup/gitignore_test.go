package setup

import (
	"os"
	"strings"
	"testing"
)

// 配った実行ファイルなどを .gitignore に足す。足す行は目印の行で囲んだ 1 つの領域に入る。配布先は git で持ち回るのが普通なので、
// 書かないと次のコミットで実行ファイルがまるごと履歴に入る。入ってしまうと、消すには履歴を書き換えるしかない。

const (
	regionBegin      = "# ---ccnavi begin--- (scripts/ccnavi-setup.sh が管理する範囲)"
	regionEnd        = "# ---ccnavi end---"
	binComment       = "# 実行ファイル"
	indexLine        = "**/ccnavi-index.jsonl" // docsearch は、git が ccnavi-index.jsonl を無視しているときだけ索引を書く
	indexComment     = "# --docs の索引"
	scratchLine      = "/scratchpad" // 一時の置き場（ワークスペースルート直下の scratchpad/）
	scratchComment   = "# 一時の置き場"
	logsLine         = "/logs/" // 実行時の記録（ワークスペースルート直下の logs/）
	logsComment      = "# 実行時の記録"
	worktreesLine    = "/.claude/worktrees/" // ワークツリーの置き場
	worktreesComment = "# ワークツリーの置き場"
)

type entry struct{ comment, line string }

// regionText は領域の全文（begin から end まで）。
func regionText(eol string, entries ...entry) string {
	out := []string{regionBegin}
	for _, e := range entries {
		out = append(out, e.comment, e.line)
	}
	out = append(out, regionEnd)
	return strings.Join(out, eol) + eol
}

func binEntry(machine string) entry { return entry{binComment, "/.ccnavi/bin/" + machine + "/"} }

// fullRegion は何も無い .gitignore に足される領域の全文。
func fullRegion(eol string) string {
	return regionText(eol, binEntry(thisMachine()), entry{indexComment, indexLine}, entry{scratchComment, scratchLine},
		entry{logsComment, logsLine}, entry{worktreesComment, worktreesLine})
}

// gitTarget は配布先（git のリポジトリに見せる）と、配布元と、中身を決めた .gitignore。
func gitTarget(t *testing.T, gitignore *string) (*target, string) {
	t.Helper()
	w := newTarget(t)
	w.makeGit()
	if gitignore != nil {
		w.writeGitignore(*gitignore)
	}
	return w, makeSource(t, srcOpts{})
}

// deployGitignore は .gitignore に text を置いて配り、できあがりの .gitignore を返す。
func deployGitignore(t *testing.T, text string) (string, *target, string, string) {
	t.Helper()
	w, src := gitTarget(t, &text)
	r := w.setup("--deploy", src)
	code(t, r, 0)
	return w.readRaw(), w, src, r.Stdout
}

// 実行ファイルの行は /.ccnavi/bin/<built>/ の 1 行だけ。振り分けの sh は代わりに通る sh と同じく追跡する側に置く（無視すると、clone した先に
// sh が届かず hook が起動しない）。置き場ごと（.ccnavi/ や .ccnavi/bin/）も無視しない（同じ .ccnavi/ の下にある rules.yml まで
// git から消え、そのプロジェクトが何を止めるかが渡らなくなる）。
func TestGitignoreAddsOnlyThePlaceOfTheBuild(t *testing.T) {
	t.Parallel()
	w, src := gitTarget(t, nil)
	r := w.setup("--deploy", src)
	code(t, r, 0)
	written, _ := w.gitignore()
	var lines []string
	for _, line := range strings.Split(written, "\n") {
		if strings.TrimSpace(line) != "" && !strings.HasPrefix(line, "#") {
			lines = append(lines, line)
		}
	}
	want := []string{"/.ccnavi/bin/" + thisMachine() + "/", indexLine, scratchLine, logsLine, worktreesLine}
	eq(t, "足した行", strings.Join(lines, "\n"), strings.Join(want, "\n"))
	for _, wide := range []string{"/.ccnavi/", "/.ccnavi/bin/", "/.ccnavi/config/common/", "/.ccnavi/scripts/", "/" + binPath} {
		notIn(t, written, wide+"\n")
	}
	notIn(t, written, "rules.yml")
	in(t, r.Stdout, ".gitignore に足した")
	for _, line := range []string{indexLine, scratchLine, logsLine, worktreesLine} {
		if !listed(section(r.Stdout, ".gitignore に足した"), line) {
			t.Errorf("足した塊に %s が無い", line)
		}
	}
}

// 領域の全文を足す。末尾に改行が無いファイルへ足しても最後の行と繋がらず、CRLF のファイルには CRLF で足す。
func TestGitignoreAddsTheWholeRegion(t *testing.T) {
	t.Parallel()
	cases := []struct {
		name string
		init *string
		want string
	}{
		{"ファイルが無い", nil, fullRegion("\n")},
		{"空のファイル", ptr(""), fullRegion("\n")},
		{"空行 1 つあけて足す", ptr("node_modules/\n"), "node_modules/\n\n" + fullRegion("\n")},
		{"末尾に改行が無い", ptr("*.log"), "*.log\n\n" + fullRegion("\n")},
		{"CRLF のファイルには CRLF で", ptr("node_modules/\r\n*.log\r\n"), "node_modules/\r\n*.log\r\n\r\n" + fullRegion("\r\n")},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			w, src := gitTarget(t, c.init)
			code(t, w.setup("--deploy", src), 0)
			eq(t, ".gitignore", w.readRaw(), c.want)
		})
	}
}

// 別の機械の組み立てを配れば、その置き場も足す。領域は 1 つのまま、コメントも重ねない。
func TestGitignoreAddsThePlaceOfEachMachineThatWasDeployed(t *testing.T) {
	t.Parallel()
	w, src := gitTarget(t, nil)
	w.setup("--deploy", src)
	w.setup("--deploy", makeSource(t, srcOpts{Target: anotherMachine}))
	written := w.readRaw()
	eq(t, "この機械の行", strings.Count(written, "/.ccnavi/bin/"+thisMachine()+"/\n"), 1)
	eq(t, "別の機械の行", strings.Count(written, "/.ccnavi/bin/"+anotherMachine+"/\n"), 1)
	eq(t, "begin", strings.Count(written, regionBegin), 1)
	eq(t, "end", strings.Count(written, regionEnd), 1)
	eq(t, "コメント", strings.Count(written, binComment+"\n"), 1)
	if strings.Index(written, "/.ccnavi/bin/"+anotherMachine+"/") > strings.Index(written, regionEnd) {
		t.Error("別の機械の行が領域の外にある")
	}
}

// ccnavi と関係のない行は、順序も含めて変わらない（領域の外も中も）。打ち直しても何も変えず、何も言わない。
func TestGitignoreKeepsTheLinesOfTheUserInOrderAndRunningTwiceChangesNothing(t *testing.T) {
	t.Parallel()
	before := "# mine\nnode_modules/\n\n*.log\n  indented\n!keep.log\nbuild/\n"
	w, src := gitTarget(t, &before)
	w.setup("--deploy", src)
	first := w.readRaw()
	if !strings.HasPrefix(first, before) {
		t.Errorf("ユーザの行が変わった:\n%s", first)
	}
	r := w.setup("--deploy", src)
	code(t, r, 0)
	eq(t, "2 回目の .gitignore", w.readRaw(), first)
	notIn(t, r.Stdout, ".gitignore に足した")
}

// 領域があって一部の行が足りないとき、end の直前にだけ足す。領域の中のユーザの行は残る。
func TestGitignoreAddsOnlyTheMissingLinesJustBeforeTheEnd(t *testing.T) {
	t.Parallel()
	before := "node_modules/\n\n" + regionText("\n", binEntry(thisMachine()), entry{"# my own", "my-cache/"}, entry{indexComment, indexLine}) + "tail-line\n"
	w, src := gitTarget(t, &before)
	code(t, w.setup("--deploy", src), 0)
	want := "node_modules/\n\n" + regionText("\n", binEntry(thisMachine()), entry{"# my own", "my-cache/"}, entry{indexComment, indexLine},
		entry{scratchComment, scratchLine}, entry{logsComment, logsLine}, entry{worktreesComment, worktreesLine}) + "tail-line\n"
	eq(t, ".gitignore", w.readRaw(), want)
}

// 領域の中にコメントだけがあって行が無い状態から、行だけ足す。コメントは重ねない。
func TestGitignoreDoesNotRepeatACommentThatIsAlreadyInTheRegion(t *testing.T) {
	t.Parallel()
	text := regionText("\n", binEntry(thisMachine()), entry{indexComment, indexLine}, entry{scratchComment, scratchLine}, entry{logsComment, logsLine}) +
		""
	text = strings.Replace(text, regionEnd+"\n", worktreesComment+"\n"+regionEnd+"\n", 1)
	w, src := gitTarget(t, &text)
	w.setup("--deploy", src)
	written := w.readRaw()
	eq(t, "コメント", strings.Count(written, worktreesComment+"\n"), 1)
	eq(t, "行", strings.Count(written, worktreesLine+"\n"), 1)
}

// CRLF の領域に足すときも CRLF で足す。ファイルの実行ビットを保つ。
func TestGitignoreAddsToACrlfRegionWithCrlfAndKeepsTheMode(t *testing.T) {
	t.Parallel()
	t.Run("CRLF の領域", func(t *testing.T) {
		t.Parallel()
		before := "a\r\n" + regionText("\r\n", binEntry(thisMachine()))
		w, src := gitTarget(t, &before)
		w.setup("--deploy", src)
		written := w.readRaw()
		if strings.Contains(strings.ReplaceAll(written, "\r\n", ""), "\n") {
			t.Error("CRLF でない改行が混ざった")
		}
		if !strings.HasSuffix(written, regionEnd+"\r\n") {
			t.Error("end で終わっていない")
		}
		in(t, written, worktreesLine+"\r\n")
	})
	t.Run("実行ビット", func(t *testing.T) {
		t.Parallel()
		notWindows(t)
		before := regionText("\n", binEntry(thisMachine()))
		w, src := gitTarget(t, &before)
		chmod(t, w.path(".gitignore"), 0o755)
		w.setup("--deploy", src)
		info, _ := os.Stat(w.path(".gitignore"))
		eq(t, "mode", info.Mode().Perm(), os.FileMode(0o755))
		in(t, w.readRaw(), worktreesLine)
	})
}

// 領域を使い始める前の古い見出しブロックは、そのまま残す。領域へ移さない。
func TestGitignoreLeavesAnOldHeaderBlockAlone(t *testing.T) {
	t.Parallel()
	old := "node_modules/\n\n# ccnavi が配る実行ファイル（scripts/ccnavi-setup.sh）\n/.ccnavi/bin/" + thisMachine() +
		"/\n\n# ccnavi の一時の置き場（scripts/ccnavi-setup.sh）\n/scratchpad\n"
	written, _, _, _ := deployGitignore(t, old)
	if !strings.HasPrefix(written, old) {
		t.Fatalf("見出しブロックが変わった:\n%s", written)
	}
	eq(t, "足した領域", written[len(old):], "\n"+regionText("\n", entry{indexComment, indexLine}, entry{logsComment, logsLine}, entry{worktreesComment, worktreesLine}))
}

// git で持っていない配布先に .gitignore を置いても、誰も読まない。--no-deploy は settings.json だけを書く。
func TestGitignoreIsNotWrittenWithoutAGitRepositoryOrWithoutDeploying(t *testing.T) {
	t.Parallel()
	w := newTarget(t)
	code(t, w.setup("--deploy", makeSource(t, srcOpts{})), 0)
	if _, ok := w.gitignore(); ok {
		t.Error("git でない配布先に .gitignore を置いた")
	}
	w2 := newTarget(t)
	w2.makeGit()
	w2.setup("--no-deploy")
	if _, ok := w2.gitignore(); ok {
		t.Error("--no-deploy が .gitignore を置いた")
	}
}

// --check は書かない。配布が済んでいても、.gitignore が欠けていれば揃っていない（--check の結果で先へ進むかを決める手順がそこを通すと、
// 次のコミットで実行ファイルが履歴に入る）。ワークツリーの行が欠けていることも名指しする。
func TestGitignoreCheckNamesTheMissingLinesWithoutWriting(t *testing.T) {
	t.Parallel()
	t.Run("配る前", func(t *testing.T) {
		t.Parallel()
		w, src := gitTarget(t, nil)
		r := w.setup("--deploy", src, "--check")
		code(t, r, 1)
		in(t, r.Stdout, ".gitignore に足す")
		if _, ok := w.gitignore(); ok {
			t.Error("--check が書いた")
		}
	})
	t.Run("配布のあとで行が欠ける", func(t *testing.T) {
		t.Parallel()
		w, src := gitTarget(t, nil)
		w.setup("--deploy", src)
		w.writeGitignore("node_modules/\n")
		r := w.setup("--deploy", src, "--check")
		code(t, r, 1)
		in(t, r.Stdout, ".gitignore に足す")
	})
	t.Run("配ったあとなら通る", func(t *testing.T) {
		t.Parallel()
		w, src := gitTarget(t, nil)
		w.setup("--deploy", src)
		code(t, w.setup("--deploy", src, "--check"), 0)
	})
	t.Run("ワークツリーの行だけが欠ける", func(t *testing.T) {
		t.Parallel()
		before := "/.ccnavi/bin/" + thisMachine() + "/\n" + indexLine + "\n" + scratchLine + "\n" + logsLine + "\n"
		w, src := gitTarget(t, &before)
		w.setup("--deploy", src) // 欠けた行が足される
		w.writeGitignore(before)
		r := w.setup("--deploy", src, "--check")
		code(t, r, 1)
		if !listed(section(r.Stdout, ".gitignore に足す"), worktreesLine) {
			t.Errorf("ワークツリーの行を名指ししていない:\n%s", r.Stdout)
		}
		eq(t, ".gitignore", w.readRaw(), before)
	})
}

// ---- 領域の壊れ方と、同等の綴り・否定の行の扱い。

// worktrees・scratchpad・logs の同等の綴りは、領域の外にあっても足さない。否定する行があれば足さず、そのことを言う。
func TestGitignoreAnEquivalentLineOutsideTheRegionIsNotAdded(t *testing.T) {
	t.Parallel()
	cases := []struct {
		name, comment, word string
		existing            []string
	}{
		{"worktrees", worktreesComment, ".claude/worktrees", []string{"/.claude/worktrees/", "/.claude/worktrees", ".claude/worktrees/", ".claude/worktrees"}},
		{"scratchpad", scratchComment, "scratchpad", []string{scratchLine, "/scratchpad/", "scratchpad/", "scratchpad"}},
		{"logs", logsComment, "logs", []string{logsLine, "/logs", "logs/", "logs"}},
	}
	for _, c := range cases {
		for _, existing := range c.existing {
			t.Run(c.name+" "+existing, func(t *testing.T) {
				t.Parallel()
				written, _, _, _ := deployGitignore(t, "node_modules/\n"+existing+"\n")
				notIn(t, written, c.comment)
				eq(t, c.word+" の行数", strings.Count(written, c.word), 1)
			})
		}
	}
}

func TestGitignoreANegationIsNotUndone(t *testing.T) {
	t.Parallel()
	cases := []struct {
		name, comment, line, said string
		negations                 []string
	}{
		{"worktrees", worktreesComment, worktreesLine, ".claude/worktrees を否定する行があり",
			[]string{"!/.claude/worktrees", "!.claude/worktrees/", "!/.claude/worktrees/", "!.claude/worktrees/keep", "!/.claude/worktrees/keep/"}},
		{"scratchpad", scratchComment, scratchLine, "scratchpad を否定する行があり",
			[]string{"!/scratchpad/", "!scratchpad/", "!/scratchpad", "!scratchpad", "!scratchpad/keep.md", "!/scratchpad/keep/"}},
		{"logs", logsComment, logsLine, "logs を否定する行があり",
			[]string{"!/logs/", "!logs/", "!/logs", "!logs", "!/logs/diag", "!logs/diag/"}},
	}
	for _, c := range cases {
		for _, negation := range c.negations {
			t.Run(c.name+" "+negation, func(t *testing.T) {
				t.Parallel()
				written, _, _, stdout := deployGitignore(t, negation+"\n")
				notIn(t, written, "\n"+c.line+"\n", c.comment)
				in(t, stdout, c.said)
				if c.name == "worktrees" {
					in(t, stdout, negation)
				}
			})
		}
	}
}

// 似ているだけの行は同等とみなさない（足す）。
func TestGitignoreALookAlikeLineDoesNotCount(t *testing.T) {
	t.Parallel()
	cases := []struct{ comment, line, other string }{
		{worktreesComment, worktreesLine, "wip/.claude/worktrees/"},
		{worktreesComment, worktreesLine, ".claude/worktrees-old/"},
		{worktreesComment, worktreesLine, "/.claude/worktrees/x"},
		{scratchComment, scratchLine, "wip/scratchpad/"},
		{scratchComment, scratchLine, "scratchpadx/"},
		{scratchComment, scratchLine, "/scratchpad/x"},
	}
	for _, c := range cases {
		t.Run(c.other, func(t *testing.T) {
			t.Parallel()
			written, _, _, _ := deployGitignore(t, c.other+"\n")
			in(t, written, c.comment+"\n"+c.line+"\n")
		})
	}
}

// 壊れた領域は触らず、行番号つきで知らせる。--check は 1 を返す。
func TestGitignoreABrokenRegionIsLeftAloneAndNamed(t *testing.T) {
	t.Parallel()
	broken := map[string]string{
		"end が無い":   "a\n" + regionBegin + "\n" + binComment + "\n/x/\n",
		"順序が逆":      "a\n" + regionEnd + "\n" + regionBegin + "\n",
		"begin が複数": regionBegin + "\n" + regionEnd + "\n" + regionBegin + "\n",
		"begin が無い": "a\n" + regionEnd + "\n",
	}
	for label, text := range broken {
		t.Run(label, func(t *testing.T) {
			t.Parallel()
			w, src := gitTarget(t, nil)
			w.setup("--deploy", src)
			w.writeGitignore(text)
			checked := w.setup("--deploy", src, "--check")
			code(t, checked, 1)
			in(t, checked.Stdout, "壊れています")
			eq(t, ".gitignore", w.readRaw(), text)
			r := w.setup("--deploy", src)
			code(t, r, 0)
			in(t, r.Stdout, "壊れています", "行目")
			eq(t, "触ったあとの .gitignore", w.readRaw(), text)
		})
	}
	t.Run("行番号を言う", func(t *testing.T) {
		t.Parallel()
		w, src := gitTarget(t, nil)
		w.setup("--deploy", src)
		w.writeGitignore("a\nb\n" + regionBegin + "\nx\n" + regionBegin + "\n")
		r := w.setup("--deploy", src, "--check")
		in(t, r.Stdout, "begin: 3 5行目", "end: なし行目")
	})
}

// 目印の行末の空白と CR は取り除いて比べる。
func TestGitignoreARegionWithTrailingSpacesAndCrStillCounts(t *testing.T) {
	t.Parallel()
	text := regionBegin + "  \r\n" + binComment + "\r\n/.ccnavi/bin/" + thisMachine() + "/\r\n" + regionEnd + "\t\r\n"
	w, src := gitTarget(t, nil)
	w.setup("--deploy", src)
	w.writeGitignore(text)
	r := w.setup("--deploy", src)
	code(t, r, 0)
	notIn(t, r.Stdout, "壊れています")
	written := w.readRaw()
	eq(t, "begin", strings.Count(written, "ccnavi begin"), 1)
	in(t, written, worktreesLine)
}

// ccnavi-index.jsonl を否定する行があっても、実行ファイルと同じく末尾に足す（--check でも足すと言う）。
func TestGitignoreAnIndexNegationDoesNotStopTheIndexLine(t *testing.T) {
	t.Parallel()
	for _, negation := range []string{"!**/ccnavi-index.jsonl", "!ccnavi-index.jsonl", "!docs/ccnavi-index.jsonl", "!*.jsonl"} {
		t.Run(negation, func(t *testing.T) {
			t.Parallel()
			before := "*.log\n" + negation + "\n/.ccnavi/bin/" + thisMachine() + "/\n" + scratchLine + "\n" + logsLine + "\n" + worktreesLine + "\n"
			w, src := gitTarget(t, nil)
			w.setup("--deploy", src)
			w.writeGitignore(before)
			checked := w.setup("--deploy", src, "--check")
			notIn(t, checked.Stdout, "ユーザが索引を追跡しているので")
			if !listed(section(checked.Stdout, ".gitignore に足す"), indexLine) {
				t.Errorf("索引の行を足すと言っていない:\n%s", checked.Stdout)
			}
			eq(t, "--check のあとの .gitignore", w.readRaw(), before)
			r := w.setup("--deploy", src)
			code(t, r, 0)
			written := w.readRaw()
			if !strings.HasPrefix(written, before) {
				t.Errorf("元の行が変わった:\n%s", written)
			}
			n := 0
			for _, line := range strings.Split(written, "\n") {
				if line == indexLine {
					n++
				}
			}
			eq(t, "索引の行", n, 1)
			notIn(t, r.Stdout, "まだ無視していません")
		})
	}
}

// 索引の同等の綴りは足さない。
func TestGitignoreAnIndexEquivalentIsNotAdded(t *testing.T) {
	t.Parallel()
	for _, existing := range []string{indexLine, "ccnavi-index.jsonl"} {
		t.Run(existing, func(t *testing.T) {
			t.Parallel()
			written, _, _, _ := deployGitignore(t, "node_modules/\n"+existing+"\n")
			if !strings.HasPrefix(written, "node_modules/\n"+existing+"\n") {
				t.Errorf("元の行が変わった:\n%s", written)
			}
			eq(t, "索引の行数", strings.Count(written, "ccnavi-index.jsonl"), 1)
			notIn(t, written, indexComment)
		})
	}
}

// CRLF の .gitignore や行末に空白のある行も、同じ行として数える。
func TestGitignoreComparesLinesWithoutCrAndTrailingSpaces(t *testing.T) {
	t.Parallel()
	for _, line := range []string{indexLine + "\r", indexLine + "  ", "ccnavi-index.jsonl\t"} {
		t.Run(strings.NewReplacer("\r", "CR", "\t", "TAB", " ", "_").Replace(line), func(t *testing.T) {
			t.Parallel()
			before := "node_modules/\r\n" + line + "\n/.ccnavi/bin/" + thisMachine() + "/\r\n" + scratchLine + "\r\n" + logsLine + "\r\n" + worktreesLine + "\r\n"
			written, _, _, _ := deployGitignore(t, before)
			eq(t, ".gitignore", written, before)
		})
	}
}
