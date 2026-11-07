package shellguard

import (
	"encoding/json"
	"math/rand/v2"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/golden"
	"ccnavi/internal/infra/shell"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/testkit"
)

// Python の hook/wrapguard・parentwrite・rootwrite と同じワークスペースに同じコマンドを渡し、6 つの検査の答えと
// 止めた文、`ccnavi-git.sh commit` の呼び出しの読みが 1 字も違わないことを見る。ワークスペースの場所は毎回変わる
// ので写しは持たず、Python が無ければ飛ばす（そのときは下の表のテストが要の約束を見る）。

const (
	parentTree = "i0001"
	childTree  = "i0001-01-01"
	plainTree  = "plain"
	gitSh      = "sh ../../../.ccnavi/scripts/ccnavi-git.sh"
	rootGitSh  = "sh .ccnavi/scripts/ccnavi-git.sh"
)

func ticketText(name, parent string) string {
	lines := []string{"---", "version: 1", "ticket: " + name}
	if parent != "" {
		lines = append(lines, "parent: "+parent, "phase: 1")
	}
	return strings.Join(append(lines, "title: t", "---", ""), "\n") + "\n"
}

func worktree(root, name string) string { return filepath.Join(root, ".claude", "worktrees", name) }

// workspace は守る場所・下書きの置き場・親と子のワークツリーを持つワークスペース。ワークツリーは git に登録しない
// （sh の検査と同じく、登録を問わない）。
func workspace(t *testing.T) string {
	t.Helper()
	root := testkit.TempRoot(t)
	for _, rel := range []string{
		".ccnavi/scripts/ccnavi-git.sh", "src/a.py", "projects/p/.ccnavi/config/project/rules.yml",
		".claude/settings.json", "tests/logs/a.txt", ".ccnavi/approved/phases/i0001/1.reviewed",
		"wip/proposals/review/i0002.md", "wip/handoff/a.md", "scratchpad/a.txt", "docs/README.md",
	} {
		testkit.Put(t, filepath.Join(root, rel), "x\n")
	}
	rulesText, err := os.ReadFile(filepath.Join(testkit.RepoRoot(t), ".ccnavi", "config", "common", "rules.yml"))
	if err != nil {
		t.Fatal(err)
	}
	testkit.Put(t, filepath.Join(root, ".ccnavi", "config", "common", "rules.yml"), string(rulesText))
	testkit.CopyMessages(t, root)
	for _, name := range []string{parentTree, childTree, plainTree} {
		if err := os.MkdirAll(filepath.Join(worktree(root, name), "src"), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	approved := filepath.Join(worktree(root, parentTree), ".ccnavi", "approved", "doing")
	testkit.Put(t, filepath.Join(approved, parentTree+".md"), ticketText(parentTree, ""))
	testkit.Put(t, filepath.Join(approved, childTree+".md"), ticketText(childTree, parentTree))
	return root
}

type sgCase struct {
	Cwd     string `json:"cwd"`
	Command string `json:"command"`
}

func sgCases(root string) []sgCase {
	parent, child := worktree(root, parentTree), worktree(root, childTree)
	x := filepath.Join(parent, "src", "x.py")
	var out []sgCase
	add := func(cwd string, commands ...string) {
		for _, c := range commands {
			out = append(out, sgCase{cwd, c})
		}
	}
	// 環境変数の代入。
	add("",
		"CCNAVI_TICKETS_APPROVED=/x sh .ccnavi/scripts/ccnavi-git.sh push",
		"env CCNAVI_PROJECTS=/x sh .ccnavi/scripts/ccnavi-git.sh push",
		"env -u CCNAVI_ENTRY_PATH sh .ccnavi/scripts/ccnavi-ticket.sh finish a",
		"export CCNAVI_WORKSPACE=/x; sh .ccnavi/scripts/ccnavi-git.sh push",
		"CCNAVI_STATE=/x; sh .ccnavi/scripts/ccnavi-review.sh confirm",
		"unset CCNAVI_TICKETS_PROPOSAL && sh .ccnavi/scripts/ccnavi-git.sh push",
		"sudo env CLAUDE_PROJECT_DIR=/x bash .ccnavi/scripts/ccnavi-review.sh confirm",
		"CCNAVI_NEW_THING=1 ../../../.ccnavi/scripts/ccnavi-git.sh status",
		"cd a && CCNAVI_ENTRY_PATH=f sh ../.ccnavi/scripts/ccnavi-user-agree.sh",
		"f() { CCNAVI_TICKETS_APPROVED=/x sh .ccnavi/scripts/ccnavi-git.sh push; }; f",
		"f(){ CCNAVI_X=1 sh .ccnavi/scripts/ccnavi-git.sh push; }; f",
		"function f { CCNAVI_X=1 sh .ccnavi/scripts/ccnavi-git.sh push; }; f",
		"env -i sh .ccnavi/scripts/ccnavi-git.sh push",
		"env --ignore-environment sh .ccnavi/scripts/ccnavi-git.sh push",
		"env -C /tmp CCNAVI_X=1 sh .ccnavi/scripts/ccnavi-git.sh push",
		"for i in 1 2; do sh .ccnavi/scripts/ccnavi-git.sh push; CCNAVI_X=1; done",
		"GIT_DIR=/p/.git GIT_WORK_TREE=/p sh .ccnavi/scripts/ccnavi-git.sh commit -m x",
		"export GIT_WORK_TREE=/p; sh .ccnavi/scripts/ccnavi-git.sh add -A",
		"env GIT_INDEX_FILE=/p/.git/index sh .ccnavi/scripts/ccnavi-git.sh status",
		"GIT_CEILING_DIRECTORIES=/x sh .ccnavi/scripts/ccnavi-git.sh commit -m x",
		"CCNAVI_GIT_MAX_LINES=200 sh .ccnavi/scripts/ccnavi-git.sh log",
		"env CCNAVI_FETCH_TIMEOUT=5 sh .ccnavi/scripts/ccnavi-fetch.sh",
		"GIT_AUTHOR_NAME=a GIT_PAGER=cat sh .ccnavi/scripts/ccnavi-git.sh commit -m x",
		"CCNAVI_ENTRY_PATH=x uv run python -m ccnavi --lint",
		"echo CCNAVI_ENTRY_PATH=x && sh .ccnavi/scripts/ccnavi-git.sh status",
		"sh -c 'CCNAVI_X=1 sh .ccnavi/scripts/ccnavi-git.sh push'",
		"env -vi sh .ccnavi/scripts/ccnavi-git.sh push",
	)
	// ラッパーで守る場所を消す・戻す。
	for _, args := range []string{
		"rm .ccnavi/scripts/ccnavi-git.sh", "rm -r .ccnavi/scripts", "rm --cached .ccnavi/config/common/rules.yml",
		"rm .claude/settings.json", "rm -r projects", "rm *.sh", "rm :/.ccnavi", "rm --pathspec-from-file=list.txt",
		"rm src/a.py", "rm src/*.py", "rm -n .claude/settings.json", "rm .claude/settings.json --dry-run", "rm -r tests/logs",
		"restore --source=HEAD~5 -- .ccnavi", "restore -s HEAD .ccnavi/config/common/rules.yml",
		"restore -sHEAD .claude/settings.json", "restore .ccnavi/config/common/rules.yml --source HEAD",
		"restore --source=HEAD -- ./", "restore --source=HEAD --pathspec-from-file=list.txt",
		"restore --source=HEAD -- src/a.py", "checkout HEAD .ccnavi/config/common/rules.yml", "checkout HEAD src/a.py",
		"restore .ccnavi/config/common/rules.yml", "restore --staged .ccnavi/config/common/rules.yml",
		"restore --ours -- .ccnavi/config/common/rules.yml", "restore --theirs .ccnavi/config/common/rules.yml",
		"checkout main", "checkout -b x main", "add .ccnavi/config/common/rules.yml",
		"restore --sou=HEAD~1 .claude/settings.json", "restore --so HEAD~1 .claude/settings.json",
		"rm --pathspec-from-fi=list.txt", "checkout -- .ccnavi/config/common/rules.yml",
		"rm --dry-run --no-dry-run .claude/settings.json", "rm -n --no-dry .claude/settings.json",
		"rm --no-dry-run --dry-run .claude/settings.json", "rm .ccnavi/approved/phases/i0001/1.reviewed",
		"rm wip/proposals/review/i0002.md", "rm -r wip/proposals/review", "rm -r wip", "rm -r wip/proposals/",
		"rm 'wip/*'", "rm WIP/Proposals/Review/i0002.md", "mv wip/proposals/review/i0002.md wip/x.md",
		"restore --source=HEAD~1 wip/proposals/review", "checkout HEAD~1 wip/proposals/review/i0002.md",
		"rm -r wip/handoff", "rm wip/handoff/a.md", "rm -n wip/proposals/review/i0002.md",
		"restore --staged wip/proposals/review/i0002.md", "add wip/proposals/review/i0002.md",
		"restore --source HEAD~1 wip", "checkout HEAD~1 -- wip", "rm -r docs",
		"rm $(printf .ccnavi/rules.yml)", "restore --source=HEAD -- $P", "checkout $B .ccnavi/config/common/rules.yml",
		"restore --source $R src/a.py", "checkout $B", "restore $P", "${S:-rm} .ccnavi/approved/t/r",
		"$(echo rm) .ccnavi/approved/t/r", "r? .ccnavi/approved/t/r", `commit -m "fix: $X"`, "diff $F", "status",
		"rm " + filepath.Join(root, "wip", "proposals", "review", "i0002.md"),
	} {
		add("", rootGitSh+" "+args)
	}
	add("",
		"cd .ccnavi && sh scripts/ccnavi-git.sh rm scripts/ccnavi-git.sh",
		"cd src && sh ../.ccnavi/scripts/ccnavi-git.sh restore --source=HEAD -- ..",
		"cd src && sh ../.ccnavi/scripts/ccnavi-git.sh rm ../.claude/settings.json",
		"cd src && sh ../.ccnavi/scripts/ccnavi-git.sh rm a.py",
		`cd "$X" && sh /x/.ccnavi/scripts/ccnavi-git.sh rm ../a.py`,
		"cd wip/proposals && sh ../../.ccnavi/scripts/ccnavi-git.sh rm review/i0002.md",
		"cd wip && sh ../.ccnavi/scripts/ccnavi-git.sh rm -r proposals",
		"X=.ccnavi; sh .ccnavi/scripts/ccnavi-git.sh rm -r $X",
		"pushd .ccnavi && sh scripts/ccnavi-git.sh rm rules.yml",
		"S=rm; sh .ccnavi/scripts/ccnavi-git.sh $S .ccnavi/approved/t/r",
		"echo 'sh .ccnavi/scripts/ccnavi-git.sh rm .ccnavi/approved/t/r' | sh",
		"echo 'sh .ccnavi/scripts/ccnavi-git.sh status; rm -rf .ccnavi' | sh",
		"echo 'sh .ccnavi/scripts/ccnavi-git.sh rm wip/proposals/review/i0002.md' | bash -euo pipefail",
		"echo 'sh .ccnavi/scripts/ccnavi-git.sh rm wip/proposals/review/i0002.md' | env -i FOO=1 bash",
		"echo 'sh .ccnavi/scripts/ccnavi-git.sh rm wip/proposals/review/i0002.md' | bash --rcfile x",
		"echo rm .ccnavi/approved/t/r | xargs sh .ccnavi/scripts/ccnavi-git.sh",
		"xargs -a f sh .ccnavi/scripts/ccnavi-git.sh",
		"sh -c 'sh .ccnavi/scripts/ccnavi-git.sh rm x'",
	)
	// 親と子のワークツリーでのラッパーの呼び出し（子の push・親のワークツリーを書き換える形）。
	for _, sub := range []string{"push", "status", "merge i0001-01-01", "commit -m x", "cherry-pick abc", "revert abc", "add a", "merge i0001", "log --oneline", "diff", "integrate-done x"} {
		add(".claude/worktrees/"+parentTree, gitSh+" "+sub)
		add(".claude/worktrees/"+childTree, gitSh+" "+sub)
		add(".claude/worktrees/"+plainTree, gitSh+" "+sub)
	}
	for _, head := range []string{"env -C " + parent, "env --chdir=" + parent, "env --chdir " + parent, "sudo -D " + parent, "sudo --chdir=" + parent, "env -C ../i0001", `env -C "$D"`, "env -iC " + parent} {
		add(".claude/worktrees/"+childTree, head+" "+gitSh+" commit -m x", head+" "+gitSh+" push")
	}
	add(".claude/worktrees/"+childTree,
		"cd ../i0001 && "+gitSh+" commit -m x",
		`cd "$X" && `+gitSh+" commit -m x",
		"echo a | xargs sh .ccnavi/scripts/ccnavi-git.sh commit",
		"sh -c 'sh ../../../.ccnavi/scripts/ccnavi-git.sh commit -m x'",
		gitSh+" $S x",
	)
	add(".claude/worktrees/"+parentTree, "cd ../i0001-01-01 && "+gitSh+" commit -m x", "env -C "+child+" "+gitSh+" push")
	// シェルから親のワークツリーへの書き込み。
	add(".claude/worktrees/"+childTree,
		"echo hi > "+x, "echo hi >> "+x, "cd ../i0001 && echo hi > src/x.py", "cd "+parent+" && printf x >src/x.py",
		"sed -i s/a/b/ "+x, "sed -i.bak -e s/a/b/ "+x, "cp /etc/hosts "+x, "cp -t "+parent+"/src /etc/hosts",
		"mv "+x+" /tmp/x.py", "tee "+x+" < /dev/null", "echo x | tee -a "+x, "touch "+x, "rm -f "+x,
		"dd if=/dev/zero of="+x+" count=1", "env -C "+parent+" touch src/x.py",
		`python3 -c "open('`+x+`','w').write('x')"`, `node -e "require('fs').writeFileSync('`+x+`','x')"`,
		`perl -e 'open F, ">`+x+`"'`, `python3 -c "open('../i0001/src/x.py','w')"`, "cd "+parent+` && python3 -c "open('x','w')"`,
		"sh -c 'echo hi > "+x+"'", `cd "$X" && echo hi > out.txt`,
		"echo hi > src/x.py", "echo hi > "+child+"/src/x.py", "cd ../i0001-01-01 && sed -i s/a/b/ src/x.py",
		"cat "+x+" > src/x.py", "cp "+x+" src/x.py", "make test 2>&1 | tail -3", "ls >/dev/null 2>&1",
		"cd "+parent+" && cat src/x.py 2>&1", "cd "+parent+" && grep -rn a src >/dev/null",
		"echo note > "+parent+"/wip/handoff/n.md", "echo x > "+parent+"/.ccnavi/approved/doing/n.md",
		"curl -o "+x+" https://example.com", "wget -O"+x+" https://example.com", "sort -o "+x+" a",
		"perl -pi -e s/a/b/ "+x, "sed -n p "+x, `python3 -c "print(open('`+x+`').read())"`,
		"echo hi > "+parent+"/src/$N", "echo hi > "+parent+"Other/src/x.py", "echo hi > ../i0001/src/*.py",
		"ln -s a "+x, "install -m 644 a "+x, "truncate -s 0 "+x, "mkdir -p "+parent+"/new",
		"echo hi > `pwd`/x", "echo 'a > b' "+x, "echo x | python3 - "+x,
	)
	// ルート直下への書き込み。
	add("",
		"sed -i 's/a/b/' README.md", "echo x > README.md", "echo x >> docs/notes.md", "printf x | tee README.md",
		"rm -f README.md", "cp a.txt b.txt", "mv a.txt scratchpad/../b.txt", "rm docs/*.md", "echo x > "+root+"/README.md",
		"echo x > wip/handoff/a.md", "sed -i 's/a/b/' scratchpad/a.txt", "tee scratchpad/a.txt < /dev/null",
		"cp a.txt wip/handoff/a.txt", "rm -f scratchpad/*", "echo x > .claude/worktrees/plain/README.md",
		"echo x > "+root+"/scratchpad/a.txt", "echo x > /dev/null", "echo x > /tmp/out.txt", "ls 2>&1 | head", "cat a",
		"cd .claude/worktrees/plain && echo x > README.md", "cd docs && echo x > README.md",
		"cp scratchpad/a .", "cp -t . scratchpad/a", "install --target-directory=. x", "mv scratchpad/a ./",
		`cd "$PWD" && echo x > README.md`, "d=.; cd $d; echo x > README.md",
		"echo x >", "echo x >>", "echo x 2>", "echo x <", "cd docs && echo x >",
		"env sh -c 'echo x > README.md'", "sudo tee README.md",
	)
	add("scratchpad", "cp x ..", "cp x .", "echo x > README.md")
	// 引用符と演算子の乱れた組み合わせ（落ちないこと、答えが Python と同じこと）。
	pieces := []string{"'", `"`, "<", ">", ">>", "2>&1", " ", " ", "a", "b c", "&&", ";", "(", ")", "cd x", "echo", "$(", "`",
		"sh .ccnavi/scripts/ccnavi-git.sh", "rm", ".ccnavi", "../i0001/x", "tee", "env -C ../i0001", "CCNAVI_X=1"}
	rng := rand.New(rand.NewPCG(554, 0))
	for range 300 {
		var b strings.Builder
		for range 1 + rng.IntN(14) {
			b.WriteString(pieces[rng.IntN(len(pieces))])
		}
		add("", b.String())
		add(".claude/worktrees/"+childTree, b.String())
	}
	return out
}

func goAnswer(t *testing.T, root string, set *rules.RuleSet, k sgCase) []any {
	t.Helper()
	conf := testkit.Settings(t, root)
	rel := func(s string) string { return strings.ReplaceAll(s, root, "<root>") }
	cwd := root
	if k.Cwd != "" {
		cwd = filepath.Join(root, k.Cwd)
	}
	r := shell.Read(k.Command)
	var layers []string
	if r.Unwrapped != "" {
		layers = strings.Split(r.Unwrapped, shell.Sep)
	}
	for _, m := range strings.Split(r.Moved, shell.Sep) {
		if m != "" {
			layers = append(layers, m)
		}
	}
	deg := r.Degraded
	script, names := CheckEnv(k.Command, deg)
	name, parent := CheckChildPush(conf, root, cwd, k.Command, deg)
	sub, word, kind := CheckCorePaths(conf, root, cwd, k.Command, deg)
	tsub, tname := CheckParentTreeGit(conf, root, cwd, k.Command, deg)
	pw := CheckParentWrite(conf, root, cwd, k.Command, deg, layers)
	rw := CheckRootWrite(set, cwd, k.Command, layers)
	calls := []any{}
	for _, c := range GitCalls(cwd, k.Command, deg, "commit") {
		var place any
		if c.Known {
			place = rel(c.Place)
		}
		calls = append(calls, []any{c.Args, place})
	}
	said := []any{}
	if len(names) > 0 {
		said = append(said, rel(EnvMessage(root, script, names)))
	} else {
		script = ""
	}
	if name != "" {
		said = append(said, rel(ChildPushMessage(root, name, parent)))
	}
	if sub != "" {
		said = append(said, rel(CorePathMessage(root, sub, word, kind)))
	}
	if tname != "" {
		said = append(said, rel(ParentTreeMessage(root, tsub, tname)))
	}
	pwKind := ""
	if pw.Parent != "" {
		said = append(said, rel(ParentWriteMessage(root, pw.What, pw.Parent, pw.Kind)))
		pwKind = string(pw.Kind)
	}
	ruleID := ""
	if rw.Rule != nil {
		said = append(said, rel(RootWriteMessage(root, rw)))
		ruleID = rw.Rule.ID
	}
	if names == nil {
		names = []string{}
	}
	return []any{
		[]any{script, names}, []any{name, parent}, []any{sub, rel(word), string(kind)}, []any{tsub, tname},
		[]any{rel(pw.What), pw.Parent, pwKind}, []any{rel(rw.target), rel(rw.path), ruleID, rel(rw.origin)},
		calls, said,
	}
}

// TestMatchesPython は表の命令の読みと判定を、Go の答えの見本（Python と答えが揃っていた Go の答えから取った）と比べる。
func TestMatchesPython(t *testing.T) {
	root := workspace(t)
	set, _, err := rules.Load(filepath.Join(root, ".ccnavi", "config", "common", "rules.yml"), root)
	if err != nil {
		t.Fatal(err)
	}
	var seen []json.RawMessage
	for _, k := range sgCases(root) {
		data, err := json.Marshal(goAnswer(t, root, set, k))
		if err != nil {
			t.Fatal(err)
		}
		seen = append(seen, json.RawMessage(strings.ReplaceAll(string(data), root, "<root>")))
	}
	golden.Check(t, "answers", seen)
}

func TestBlocked(t *testing.T) {
	for name, want := range map[string]bool{
		"CCNAVI_GIT_KEEP_LOGS": false, "CCNAVI_GIT_SOMETHING_NEW": true, "PATH": false,
		"GIT_DIR": true, "GIT_SOMETHING_NEW": true, "GIT_AUTHOR_NAME": false, "CLAUDE_PROJECT_DIR": true,
	} {
		if got := blocked(name); got != want {
			t.Errorf("Blocked(%s) = %v, want %v", name, got, want)
		}
	}
}

// 守る場所の表（Python の CORE_CASES）。Python が無くても、止める・通すの約束をここで見る。
func TestCorePathsTable(t *testing.T) {
	root := workspace(t)
	conf := testkit.Settings(t, root)
	for args, stops := range map[string]bool{
		"rm .ccnavi/scripts/ccnavi-git.sh": true, "rm -r projects": true, "rm *.sh": true, "rm :/.ccnavi": true,
		"rm src/a.py": false, "rm src/*.py": false, "rm -n .claude/settings.json": false, "rm -r tests/logs": false,
		"restore --source=HEAD~5 -- .ccnavi": true, "restore -sHEAD .claude/settings.json": true,
		"restore --source=HEAD -- src/a.py": false, "restore .ccnavi/config/common/rules.yml": false,
		"checkout HEAD .ccnavi/config/common/rules.yml": true, "checkout main": false,
		"rm --dry-run --no-dry-run .claude/settings.json": true, "rm --no-dry-run --dry-run .claude/settings.json": false,
		"rm -r wip": true, "rm -r wip/handoff": false, "rm WIP/Proposals/Review/i0002.md": true,
	} {
		sub, _, _ := CheckCorePaths(conf, root, root, rootGitSh+" "+args, false)
		if (sub != "") != stops {
			t.Errorf("%s: 止めた=%v, want %v", args, sub != "", stops)
		}
	}
}

// 役はそのワークツリー自身のツリーのチケットだけで決める。ほかのツリーに `parent:` を持つ同名のファイルを置いても、
// 親のワークツリーの禁止は外れない（回帰）。
func TestTicketRoleIgnoresPlantedFiles(t *testing.T) {
	root := workspace(t)
	conf := testkit.Settings(t, root)
	parent, child := worktree(root, parentTree), worktree(root, childTree)
	testkit.Put(t, filepath.Join(child, "wip", "proposals", "review", parentTree+".md"), "parent: x\n")
	testkit.Put(t, filepath.Join(worktree(root, plainTree), ".ccnavi", "approved", "doing", parentTree+".md"), "parent: x\n")
	if role, _ := TicketRole(conf, root, parentTree); role != RoleParent {
		t.Errorf("親の役 = %q", role)
	}
	// 自身のツリーで、`parent:` の無い同名のチケットと並べても親のまま（曖昧なら止める側）。
	testkit.Put(t, filepath.Join(parent, "wip", "proposals", "review", parentTree+".md"), "parent: x\n")
	if role, _ := TicketRole(conf, root, parentTree); role != RoleParent {
		t.Errorf("曖昧な親の役 = %q", role)
	}
	testkit.Put(t, filepath.Join(child, ".ccnavi", "approved", "doing", childTree+".md"), ticketText(childTree, parentTree))
	if role, p := TicketRole(conf, root, childTree); role != roleChild || p != parentTree {
		t.Errorf("子の役 = %q, %q", role, p)
	}
	if role, _ := TicketRole(conf, root, plainTree); role != RoleParent {
		// plain には parent: x だけの i0001.md を置いたが、名前が違うので plain の役は決まらない。
		if role != "" {
			t.Errorf("plain の役 = %q", role)
		}
	}
	if !isParentTree(conf, root, parentTree) || isParentTree(conf, root, config.DefaultTickets) {
		t.Error("IsParentTree が TicketRole と合わない")
	}
}
