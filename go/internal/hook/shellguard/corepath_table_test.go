package shellguard

import (
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/infra/shell"
	"ccnavi/internal/tickets/testkit"
)

// ラッパーで守る場所を消す・別のコミットの中身に戻す形の、止める・通すの表（コマンド全体で見る。居場所の移動・
// 読み切れない形・レビュー待ちの置き場を含む）。受入テスト（tests/guard/test_wrapguard.py の CorePathTest）が hook を
// 1 件ずつ起こして見ていた表で、sh の写しが同じく止めることは受入テストの CorePathShAgreesTest が見る。
func TestCorePathsCommandTable(t *testing.T) {
	root := workspace(t)
	conf := testkit.Settings(t, root)
	review := filepath.Join(root, "wip", "proposals", "review", "i0002.md")
	g := rootGitSh + " "
	cases := []struct {
		command string
		stops   bool
	}{
		// 消す・別のコミットの中身に戻す形（受入テストの CORE_CASES と同じ表）。何も消さない形・索引に戻す形・衝突の解決は通す。
		{g + "rm .ccnavi/scripts/ccnavi-git.sh", true}, {g + "rm -r .ccnavi/scripts", true},
		{g + "rm --cached .ccnavi/config/common/rules.yml", true}, {g + "rm .claude/settings.json", true},
		{g + "rm -r projects", true}, {g + "rm *.sh", true}, {g + "rm :/.ccnavi", true}, {g + "rm --pathspec-from-file=list.txt", true},
		{g + "rm src/a.py", false}, {g + "rm src/*.py", false}, {g + "rm -n .claude/settings.json", false},
		{g + "rm .claude/settings.json --dry-run", false}, {g + "rm -r tests/logs", false},
		{g + "restore --source=HEAD~5 -- .ccnavi", true}, {g + "restore -s HEAD .ccnavi/config/common/rules.yml", true},
		{g + "restore -sHEAD .claude/settings.json", true}, {g + "restore .ccnavi/config/common/rules.yml --source HEAD", true},
		{g + "restore --source=HEAD -- ./", true}, {g + "restore --source=HEAD --pathspec-from-file=list.txt", true},
		{g + "restore --source=HEAD -- src/a.py", false}, {g + "checkout HEAD .ccnavi/config/common/rules.yml", true},
		{g + "checkout HEAD src/a.py", false}, {g + "restore .ccnavi/config/common/rules.yml", false},
		{g + "restore --staged .ccnavi/config/common/rules.yml", false}, {g + "restore --ours -- .ccnavi/config/common/rules.yml", false},
		{g + "restore --theirs .ccnavi/config/common/rules.yml", false}, {g + "checkout main", false}, {g + "checkout -b x main", false},
		{g + "add .ccnavi/config/common/rules.yml", false},
		// 居場所から解いたパスで見る。居場所が読めず、上へ出る語はヒットする先を決められない。
		{"cd .ccnavi && sh scripts/ccnavi-git.sh rm scripts/ccnavi-git.sh", true},
		{"cd src && sh ../.ccnavi/scripts/ccnavi-git.sh restore --source=HEAD -- ..", true},
		{"cd src && sh ../.ccnavi/scripts/ccnavi-git.sh rm ../.claude/settings.json", true},
		{"cd src && sh ../.ccnavi/scripts/ccnavi-git.sh rm a.py", false},
		{`cd "$X" && sh /x/.ccnavi/scripts/ccnavi-git.sh rm ../a.py`, true},
		// レビュー待ちの置き場は、親を丸ごと消す形もヒットする。下書きの置き場と何も消さない形は通す。
		{g + "rm wip/proposals/review/i0002.md", true}, {g + "rm -r wip/proposals/review", true},
		{g + "rm --cached wip/proposals/review/i0002.md", true}, {g + "rm -r wip", true}, {g + "rm -r wip/proposals/", true},
		{g + "rm 'wip/*'", true}, {g + "rm WIP/Proposals/Review/i0002.md", true},
		{g + "mv wip/proposals/review/i0002.md wip/x.md", true}, {g + "restore --source=HEAD~1 wip/proposals/review", true},
		{g + "checkout HEAD~1 wip/proposals/review/i0002.md", true},
		{"cd wip/proposals && sh ../../.ccnavi/scripts/ccnavi-git.sh rm review/i0002.md", true},
		{"cd wip && sh ../.ccnavi/scripts/ccnavi-git.sh rm -r proposals", true}, {g + "rm " + review, true},
		{g + "rm -r wip/handoff", false}, {g + "rm wip/handoff/a.md", false}, {g + "rm -n wip/proposals/review/i0002.md", false},
		{g + "restore --staged wip/proposals/review/i0002.md", false}, {g + "add wip/proposals/review/i0002.md", false},
		// 対象を読み切れない形は止める。起点だけを変数で渡す形と、索引に戻す形は対象が読めるので通す。
		{"X=.ccnavi; " + g + "rm -r $X", true}, {g + "rm $(printf .ccnavi/rules.yml)", true},
		{"pushd .ccnavi && sh scripts/ccnavi-git.sh rm rules.yml", true}, {`cd "$D" && sh /x/.ccnavi/scripts/ccnavi-git.sh rm a.py`, true},
		{g + "restore --source=HEAD -- $P", true}, {g + "checkout $B .ccnavi/config/common/rules.yml", true},
		{g + "restore --source $R src/a.py", false}, {g + "checkout $B", false}, {g + "restore $P", false},
		// サブコマンドが文字どおりの語でない（変数・置換・glob）。
		{"S=rm; " + g + "$S .ccnavi/approved/t/r", true}, {g + "${S:-rm} .ccnavi/approved/t/r", true},
		{g + "$(echo rm) .ccnavi/approved/t/r", true}, {g + "r? .ccnavi/approved/t/r", true},
		// 標準入力から読むシェルに流す文字列の中の呼び出し（前に選択肢を置いたシェル、env・command 越しも）。
		{"echo '" + g + "rm .ccnavi/approved/t/r' | sh", true}, {"echo '" + g + "status; rm -rf .ccnavi' | sh", true},
		{"echo '" + g + "rm wip/proposals/review/i0002.md' | bash --norc", true},
		{"echo '" + g + "rm wip/proposals/review/i0002.md' | bash --posix", true},
		{"echo '" + g + "rm wip/proposals/review/i0002.md' | bash --noprofile", true},
		{"echo '" + g + "rm wip/proposals/review/i0002.md' | bash --rcfile x", true},
		{"echo '" + g + "rm wip/proposals/review/i0002.md' | bash -o pipefail", true},
		{"echo '" + g + "rm wip/proposals/review/i0002.md' | bash -O extglob", true},
		{"echo '" + g + "rm wip/proposals/review/i0002.md' | bash -euo pipefail", true},
		{"echo '" + g + "rm wip/proposals/review/i0002.md' | env sh", true},
		{"echo '" + g + "rm wip/proposals/review/i0002.md' | env -i FOO=1 bash", true},
		{"echo '" + g + "rm wip/proposals/review/i0002.md' | command sh", true},
		// 走査が読み切れず、ラッパーの名前の後ろに文字どおりのサブコマンドが無い。直接の呼び出しは通す。
		{"echo rm .ccnavi/approved/t/r | xargs " + g, true}, {"xargs -a f " + g, true},
		{g + `commit -m "fix: $X"`, false}, {g + "diff $F", false}, {g + "status", false},
		// sh が別の理由で断る形と、sh が通してユーザに任せる形も、hook は止める。
		{g + "restore --sou=HEAD~1 .claude/settings.json", true}, {g + "restore --so HEAD~1 .claude/settings.json", true},
		{g + "rm --pathspec-from-fi=list.txt", true}, {g + "checkout -- .ccnavi/config/common/rules.yml", true},
		{g + "rm -n --no-dry .claude/settings.json", true}, {g + "rm .ccnavi/approved/phases/i0001/1.reviewed", true},
		{g + "rm " + filepath.Join(root, ".ccnavi", "scripts", "ccnavi-git.sh"), true},
	}
	for _, c := range cases {
		sub, _, _ := CheckCorePaths(conf, root, root, c.command, shell.Read(c.command).Degraded)
		if (sub != "") != c.stops {
			t.Errorf("%q: 止めた=%v, want %v", c.command, sub != "", c.stops)
		}
	}
}

// サブエージェントが子のワークツリーからシェルで親のワークツリーへ書く形の、止める・通すの表。受入テスト
// （tests/guard/test_wrapguard.py の SubagentParentTreeTest）が hook を 1 件ずつ起こして見ていた表。
// テストの名前に「Write」を入れない。一時置き場のパスに入り、コードの中の書き込みを疑う語として読まれる。
func TestParentTreeShellTable(t *testing.T) {
	root := workspace(t)
	conf := testkit.Settings(t, root)
	parent, child := worktree(root, parentTree), worktree(root, childTree)
	x := filepath.Join(parent, "src", "x.py")
	stops := []string{
		"echo hi > " + x, "echo hi >> " + x, "cd ../i0001 && echo hi > src/x.py", "cd " + parent + " && printf x >src/x.py",
		"sed -i s/a/b/ " + x, "sed -i.bak -e s/a/b/ " + x, "cp /etc/hosts " + x, "cp -t " + parent + "/src /etc/hosts",
		"mv " + x + " /tmp/x.py", "tee " + x + " < /dev/null", "echo x | tee -a " + x, "touch " + x, "rm -f " + x,
		"dd if=/dev/zero of=" + x + " count=1", "env -C " + parent + " touch src/x.py",
		`python3 -c "open('` + x + `','w').write('x')"`, `node -e "require('fs').writeFileSync('` + x + `','x')"`,
		`perl -e 'open F, ">` + x + `"'`, `python3 -c "open('../i0001/src/x.py','w')"`, "cd " + parent + ` && python3 -c "open('x','w')"`,
		"sh -c 'echo hi > " + x + "'", `cd "$X" && echo hi > out.txt`,
	}
	passes := []string{
		"echo hi > src/x.py", "echo hi > " + child + "/src/x.py", "cd ../i0001-01-01 && sed -i s/a/b/ src/x.py",
		"cat " + x + " > src/x.py", "cp " + x + " src/x.py", "make test 2>&1 | tail -3", "ls >/dev/null 2>&1",
		"cd " + parent + " && cat src/x.py 2>&1", "cd " + parent + " && grep -rn a src >/dev/null",
		"echo note > " + parent + "/wip/handoff/n.md", "cp /tmp/f.yml " + parent + "/wip/proposals/flows/x.yml",
		`python3 -c "print(open('` + x + `').read())"`, `python3 -c "open('out.txt','w').write('x')"`, "diff " + x + " src/x.py > /tmp/d.txt",
	}
	check := func(command string, want bool) {
		r := shell.Read(command)
		var layers []string
		if r.Unwrapped != "" {
			layers = strings.Split(r.Unwrapped, shell.Sep)
		}
		for _, m := range strings.Split(r.Moved, shell.Sep) {
			if m != "" {
				layers = append(layers, m)
			}
		}
		if got := CheckParentWrite(conf, root, child, command, r.Degraded, layers).Parent != ""; got != want {
			t.Errorf("%q: 止めた=%v, want %v", command, got, want)
		}
	}
	for _, c := range stops {
		check(c, true)
	}
	for _, c := range passes {
		check(c, false)
	}
}
