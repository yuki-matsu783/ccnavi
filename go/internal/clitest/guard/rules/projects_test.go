package rules

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"sort"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/samples"
)

// 複数のリポジトリの受入テスト。道具を外から呼んで応答だけを見る。
//
// ワークスペース 1 つとプロジェクト 2 つ（app と lib）を一時ディレクトリに作る。ワークスペースは Claude Code を起動した場所で、
// 自分の git を持つ。プロジェクトは `projects/` の直下に clone した別のリポジトリで、それぞれレイヤーの 3 本の置き場
// （`.ccnavi/config/project/`）に rules.yml を持つ。見るのは 5 つ。
//
//  1. パスを持つツールは共通レイヤー + 行き先のプロジェクトのレイヤーで判定される。ワークスペースへの書き込みは共通レイヤー + 自身のレイヤー
//  2. Bash は共通レイヤーと全部のレイヤーの和で判定され、cwd がどこでも同じ
//  3. 読めないレイヤーは空として扱われ、記録がレイヤーの名前を残す。組み込みの既定へは戻らない
//  4. プロジェクトから切ったワークツリーが認識され、元リポジトリとチケットの `project:` が食い違えば止まる
//  5. `projects/` を作らなければ、この機能が入る前と同じに動く
//
// レイヤーの和そのもの（重複の排除、同 id、`--explain`）は config/union が見る。ここが見るのは、置き場とツリーの結び付きが合っていること。

func rule(id, match, glob, message string) payload {
	r := payload{"id": id, "match": match, "glob": glob}
	if message != "" {
		r["message"] = message
	}
	return r
}

var (
	wsRules = payload{
		"version": 1,
		"deny": []payload{
			rule("ws-rm", "Bash", "*rm -rf *", "recursive delete is not run by the agent."),
			rule("guard-approved", "Write|Edit|NotebookEdit", "*/.ccnavi/approved/*", "guard settings. ask the user."),
		},
		"allow": []payload{
			{"id": "ws-src", "match": "Write|Edit", "glob": "*/src/*"},
			{"id": "ws-read", "match": "Read", "regex": "."},
		},
	}
	appRules = payload{
		"version": 1,
		"deny":    []payload{rule("schema", "Write|Edit", "*/schema/*", "schema is not edited by hand. write a migration.")},
		"allow": []payload{
			{"id": "source", "match": "Write|Edit", "glob": "*/src/*"},
			{"id": "npm", "match": "Bash", "regex": `\bnpm test\b`},
		},
	}
	libRules = payload{
		"version": 1,
		"deny":    []payload{rule("raw-psql", "Bash", "*psql*", "psql is not run by the agent.")},
		"allow":   []payload{{"id": "source", "match": "Write|Edit", "glob": "*/src/*"}},
	}
)

const (
	appRulesID  = "projects/app/.ccnavi/config/project/rules.yml:schema"
	libPsqlID   = "projects/lib/.ccnavi/config/project/rules.yml:raw-psql"
	userAgree   = "user"
	approveText = "y\n"
)

// ticketText は提案（チケット）の本文。plan が 1 以上なら親に最小の計画を足す。
func ticketText(name, parent, project string, allow []string, plan int) string {
	lines := []string{"---", "version: 1", "ticket: " + name}
	if parent == "" {
		lines = append(lines, "mr: 1", "issue: 1")
	} else {
		lines = append(lines, "parent: "+parent, "phase: 1")
	}
	if project != "" {
		lines = append(lines, "project: "+project)
	}
	title := "作業"
	if parent != "" {
		title = "子 " + name
	}
	lines = append(lines, "human_review:", "  required: false", "  reason: テスト", "title: "+title, "rationale: |", "  理由")
	if len(allow) > 0 {
		lines = append(lines, "allow:")
		for _, g := range allow {
			lines = append(lines, "  - match: Write|Edit", fmt.Sprintf("    glob: %q", g))
		}
	}
	lines = append(lines, `started_at: ""`, `completed_at: ""`, `base_sha: ""`, "---", "", "本文")
	text := strings.Join(lines, "\n") + "\n"
	if plan > 0 {
		return samples.PlainParent(text, samples.Work(plan))
	}
	return text
}

type projects struct {
	t             *testing.T
	ws, app, lib  string
	approvedPlace string
}

func newProjects(t *testing.T) *projects {
	t.Helper()
	ws := base.TempDir(t)
	mkdirs(t, filepath.Join(ws, ".claude"))
	run.Git(t, ws, "init", "--quiet", "-b", "main")
	write(t, filepath.Join(ws, ".gitignore"), "/projects/\n/.claude/\n")
	write(t, filepath.Join(ws, "src", "keep.py"), "print(1)\n")
	run.Git(t, ws, "add", "-A")
	run.Git(t, ws, "commit", "--quiet", "-m", "init")
	// 共通レイヤーは既定の置き場へ。`--rules` は診断でだけ有効。
	write(t, base.CommonPath(ws, "rules"), jsonText(t, wsRules))
	p := &projects{t: t, ws: ws}
	p.app = p.project("app", appRules)
	p.lib = p.project("lib", libRules)
	// 承認済みチケットは、そのチケットの親のツリーの `.ccnavi/approved/` に置かれる。
	// ここの環境は親のワークツリーを作らないので、提案があったツリーに置かれる。
	p.approvedPlace = filepath.Join(ws, ".ccnavi", "approved")
	return p
}

func (p *projects) project(name string, rules payload) string {
	p.t.Helper()
	root := filepath.Join(p.ws, "projects", name)
	mkdirs(p.t, root)
	run.Git(p.t, root, "init", "--quiet", "-b", "main")
	write(p.t, base.ConfigPath(root, "rules"), jsonText(p.t, rules))
	write(p.t, filepath.Join(root, "src", "keep.py"), "print(1)\n")
	write(p.t, filepath.Join(root, "schema", "keep.sql"), "-- keep\n")
	run.Git(p.t, root, "add", "-A")
	run.Git(p.t, root, "commit", "--quiet", "-m", "init")
	return root
}

// worktree は owner から切った ws/.claude/worktrees/<name>（あれば使い回す）。
func (p *projects) worktree(owner, name string) string {
	p.t.Helper()
	path := filepath.Join(p.ws, ".claude", "worktrees", name)
	if _, err := os.Stat(path); err != nil {
		run.Git(p.t, owner, "worktree", "add", "--quiet", path, "-b", name)
	}
	return path
}

var childSuffix = regexp.MustCompile(`-\d\d-\d\d$`)

// proposalFile は提案を置くパス。親の提案は親のワークツリー（識別子と同名。無ければ切る）の中。
func (p *projects) proposalFile(owner, name string) string {
	home := p.worktree(owner, childSuffix.ReplaceAllString(name, ""))
	return filepath.Join(home, "wip", "proposals", "todo", name+".md")
}

// approvedPath は承認済みチケットの置き場の下のパス。どのツリーに置かれたかを探す。
func (p *projects) approvedPath(parts ...string) string {
	homes := []string{p.ws, p.lib, p.app}
	trees := filepath.Join(p.ws, ".claude", "worktrees")
	if entries, err := os.ReadDir(trees); err == nil {
		for _, e := range entries {
			homes = append(homes, filepath.Join(trees, e.Name()))
		}
	}
	for _, home := range homes {
		path := filepath.Join(append([]string{home, ".ccnavi", "approved"}, parts...)...)
		if exists(path) {
			return path
		}
	}
	return filepath.Join(append([]string{p.approvedPlace}, parts...)...)
}

// ccnavi は実行ファイルを 1 回起動する。`--projects` は渡さない（診断でだけ有効で、置き場は `--root` の下の既定のまま）。
// 「`projects/` を数えない」は `projects/` を作らないワークスペースで言う。
func (p *projects) ccnavi(stdin string, args ...string) run.Result {
	p.t.Helper()
	prefix := []string{"--root", p.ws, "--guard-core-files", "disable", "--restore-if-deny", "disable", "--guard-ticket-approval", "disable"}
	return run.Ccnavi(p.t, samples.Place(prefix, args), run.Opts{Input: stdin, Dir: base.Root()})
}

// hook は PreToolUse（event を変えれば別のイベント）を 1 回渡す。
func (p *projects) hook(tool, cwd, event string, input map[string]any) run.Result {
	p.t.Helper()
	if input == nil {
		input = map[string]any{}
	}
	body := payload{"hook_event_name": event, "tool_name": tool, "cwd": cwd, "session_id": "s1", "tool_input": input}
	return p.ccnavi(jsonText(p.t, body), "--mode", "enable")
}

func (p *projects) write(cwd, path string) run.Result {
	p.t.Helper()
	return p.hook("Write", cwd, "PreToolUse", map[string]any{"file_path": path})
}

func (p *projects) bash(cwd, command string) run.Result {
	p.t.Helper()
	return p.hook("Bash", cwd, "PreToolUse", map[string]any{"command": command})
}

func (p *projects) record() map[string]any { return lastRecord(p.t, p.ws) }

// agree は `ccnavi user agree` に承認の入力（y）を渡す。
func (p *projects) agree() run.Result {
	p.t.Helper()
	return p.ccnavi(approveText, userAgree, "agree")
}

func (p *projects) mustAgree() run.Result {
	p.t.Helper()
	r := p.agree()
	if r.Code != 0 {
		p.t.Fatalf("承認が通らない: %d\n%s%s", r.Code, r.Stdout, r.Stderr)
	}
	return r
}

// board は `--explain --json` の、チケットごとのプロジェクトと承認待ち。
func (p *projects) board() (projectOf map[string]string, pending []string) {
	p.t.Helper()
	r := p.ccnavi("", "--explain", "--json")
	var out struct {
		Tickets []struct {
			Ticket  string `json:"ticket"`
			Project string `json:"project"`
		} `json:"tickets"`
		Pending []string `json:"pending_approval"`
	}
	if err := json.Unmarshal([]byte(r.Stdout), &out); err != nil {
		p.t.Fatalf("--explain --json が JSON でない: %v\n%s%s", err, r.Stdout, r.Stderr)
	}
	projectOf = map[string]string{}
	for _, tk := range out.Tickets {
		projectOf[tk.Ticket] = tk.Project
	}
	return projectOf, out.Pending
}

// familyInLib は lib の提案として、親 1 本とその子 1 本（フェーズ 1）を書く。
func (p *projects) familyInLib(parent string) {
	p.t.Helper()
	write(p.t, p.proposalFile(p.lib, parent), ticketText(parent, "", "", []string{"src/*"}, 0))
	child := parent + "-01-01"
	write(p.t, p.proposalFile(p.lib, child), ticketText(child, parent, "", []string{"src/a/*"}, 0))
}

func mustDeny(t testing.TB, r run.Result) reply {
	t.Helper()
	out := parse(t, r)
	if out.Decision != "deny" {
		t.Fatalf("deny のはず: %s%s", r.Stdout, r.Stderr)
	}
	return out
}

// 1. パスを持つツールは行き先のプロジェクトのルール

func TestWritesAreJudgedByTheRulesOfTheProjectTheyLandIn(t *testing.T) {
	t.Parallel()
	p := newProjects(t)
	out := mustDeny(t, p.write(p.ws, filepath.Join(p.app, "schema", "x.sql")))
	mustHave(t, out.text(), appRulesID, "write a migration")
	rec := p.record()
	if str(rec, "project") != "app" || str(rec, "tree") != "app" || !reflect.DeepEqual(strs(rec, "rules"), []string{appRulesID}) {
		t.Errorf("記録: %v", rec)
	}

	// lib に schema の deny は無い。app の deny は lib にはヒットしない。
	passed := p.write(p.ws, filepath.Join(p.lib, "schema", "x.sql"))
	if passed.Code != 0 {
		t.Errorf("code = %d: %s", passed.Code, passed.Stderr)
	}
	mustNotHave(t, parse(t, passed).text(), "DENY")
	if str(p.record(), "project") != "lib" {
		t.Errorf("記録: %v", p.record())
	}

	allowed := p.write(p.app, filepath.Join(p.lib, "src", "a.py"))
	if allowed.Code != 0 || str(p.record(), "decision") != "allow" {
		t.Errorf("allow のはず: %d %v", allowed.Code, p.record())
	}
}

func TestWritesIntoTheWorkspaceUseTheWorkspaceRules(t *testing.T) {
	t.Parallel()
	p := newProjects(t)
	if r := p.write(p.app, filepath.Join(p.ws, "src", "a.py")); r.Code != 0 {
		t.Errorf("code = %d: %s", r.Code, r.Stderr)
	}
	rec := p.record()
	if str(rec, "decision") != "allow" || !reflect.DeepEqual(strs(rec, "rules"), []string{".ccnavi/config/common/rules.yml:ws-src"}) {
		t.Errorf("記録: %v", rec)
	}
	if _, has := rec["project"]; has {
		t.Errorf("project が付いている: %v", rec)
	}
	// ワークスペースの schema/ は誰も守っていない。app の deny はヒットしない。
	passed := p.write(p.app, filepath.Join(p.ws, "schema", "x.sql"))
	mustNotHave(t, parse(t, passed).text(), "DENY")
}

// 2. Bash は全部の和

func TestBashIsJudgedByTheUnionRegardlessOfCwd(t *testing.T) {
	t.Parallel()
	p := newProjects(t)
	for _, cwd := range []string{p.ws, p.app, p.lib} {
		mustHave(t, mustDeny(t, p.bash(cwd, "psql -c 'select 1'")).text(), libPsqlID)
		mustHave(t, mustDeny(t, p.bash(cwd, "rm -rf build")).text(), "ws-rm")
	}
	// app だけが許した npm test は lib に居ても通る。和の代償で、意図した挙動。
	if r := p.bash(p.lib, "npm test"); r.Code != 0 {
		t.Errorf("code = %d: %s", r.Code, r.Stderr)
	}
	rec := p.record()
	if str(rec, "decision") != "allow" || str(rec, "project") != "lib" ||
		!reflect.DeepEqual(strs(rec, "rules"), []string{"projects/app/.ccnavi/config/project/rules.yml:npm"}) {
		t.Errorf("記録: %v", rec)
	}
}

// 3. 読めないプロジェクトのルール

func TestUnreadableProjectRulesAreEmptyAndDropOutOfTheUnion(t *testing.T) {
	t.Parallel()
	// 不正なレイヤーは空として扱い、記録がレイヤーの名前を残す。組み込みの既定には戻らない。
	// 共通レイヤーが有るのに戻すと、共通レイヤーの deny が消える側になる。
	p := newProjects(t)
	write(t, base.ConfigPath(p.app, "rules"), "version: 1\ndeny: [\n")
	passed := p.write(p.ws, filepath.Join(p.app, "schema", "x.sql"))
	if passed.Code != 0 {
		t.Errorf("code = %d: %s", passed.Code, passed.Stderr)
	}
	rec := p.record()
	if str(rec, "fallback") != "app" || !strings.Contains(str(rec, "detail"), "app") {
		t.Errorf("fallback / detail: %v", rec)
	}
	mustNotHave(t, parse(t, passed).text(), "built-in defaults")

	// 共通レイヤーの deny は不正なレイヤーの上でもヒットしたまま。
	mustHave(t, mustDeny(t, p.write(p.ws, filepath.Join(p.app, ".ccnavi", "approved", "x"))).text(), "guard-approved")

	mustHave(t, mustDeny(t, p.bash(p.app, "psql")).text(), libPsqlID)
	rec = p.record()
	if str(rec, "fallback") != "app" || !strings.Contains(str(rec, "detail"), "unreadable layer: app") {
		t.Errorf("fallback / detail: %v", rec)
	}
}

// 4. プロジェクトから切ったワークツリー

func TestAWorktreeCutFromAProjectIsJudgedByThatProject(t *testing.T) {
	t.Parallel()
	p := newProjects(t)
	tree := p.worktree(p.app, "i0007")
	mustHave(t, mustDeny(t, p.write(p.ws, filepath.Join(tree, "schema", "x.sql"))).text(), appRulesID)
	rec := p.record()
	if str(rec, "tree") != "i0007" || str(rec, "project") != "app" {
		t.Errorf("記録: %v", rec)
	}
}

func TestAWorktreeCutFromTheWrongProjectIsRefusedByTheTicket(t *testing.T) {
	t.Parallel()
	// プロジェクトの提案は、親のワークツリー（そのプロジェクトから切る）の中に置く。置き場がプロジェクトを決めるので、
	// frontmatter の project は書かなくてよい。
	p := newProjects(t)
	p.familyInLib("i0007")
	p.familyInLib("i0008")
	p.mustAgree()

	// 子のワークツリーを別のプロジェクト（app）から切ってしまった
	wrong := p.worktree(p.app, "i0007-01-01")
	out := mustDeny(t, p.write(p.ws, filepath.Join(wrong, "src", "a", "a.py")))
	mustHave(t, out.text(), "DENY_TICKET_PROJECT_MISMATCH", "lib のリポジトリ向け", "app から切られている")
	rec := p.record()
	if str(rec, "code") != "DENY_TICKET_PROJECT_MISMATCH" {
		t.Errorf("code = %v", rec["code"])
	}
	// 範囲の判定ではないので、記録の名前は builtin:ticket-scope を使い回さない。
	if !reflect.DeepEqual(strs(rec, "rules"), []string{"builtin:ticket-project"}) {
		t.Errorf("rules = %v", strs(rec, "rules"))
	}

	right := p.worktree(p.lib, "i0008-01-01")
	allowed := p.write(p.ws, filepath.Join(right, "src", "a", "a.py"))
	if allowed.Code != 0 {
		t.Errorf("code = %d: %s", allowed.Code, allowed.Stderr)
	}
	mustNotHave(t, parse(t, allowed).text(), "DENY")
	mustHave(t, parse(t, p.write(p.ws, filepath.Join(right, "docs", "a.md"))).text(), "DENY_TICKET_SCOPE")
}

func TestAWrongProjectIsRefusedEvenWhenTheWorktreeNameDiffersInCase(t *testing.T) {
	t.Parallel()
	// 表記違いで切ったワークツリーでも（どの機械でも区別しない）取り違えを止める。
	// 範囲の判定は表記の違いを吸収して引く。取り違えの検査だけ厳密に引くと、範囲の中への書き込みは別のプロジェクトのツリーでも通る。
	p := newProjects(t)
	p.familyInLib("i0007")
	p.mustAgree()
	wrong := p.worktree(p.app, "I0007-01-01")
	out := mustDeny(t, p.write(p.ws, filepath.Join(wrong, "src", "a", "a.py")))
	mustHave(t, out.text(), "DENY_TICKET_PROJECT_MISMATCH")
	if str(p.record(), "code") != "DENY_TICKET_PROJECT_MISMATCH" {
		t.Errorf("記録: %v", p.record())
	}
}

func TestProjectSkillsAreWrittenOnlyUnderTheTicketRules(t *testing.T) {
	t.Parallel()
	// skills/ は保護の外のふつうの場所。チケットの範囲の中でだけ書ける。範囲の外とチケットの無いワークツリーでは、
	// 同じツリーのほかのファイルと同じ判定になる。
	p := newProjects(t)
	write(t, p.proposalFile(p.lib, "i0009"), ticketText("i0009", "", "", []string{"skills/*"}, 0))
	write(t, p.proposalFile(p.lib, "i0009-01-01"), ticketText("i0009-01-01", "i0009", "", []string{"skills/deploy/*"}, 0))
	p.mustAgree()
	// 親のワークツリーは、範囲の中でも置き場以外に書けない。
	home := p.worktree(p.lib, "i0009")
	mustHave(t, mustDeny(t, p.write(p.ws, filepath.Join(home, "skills", "deploy", "SKILL.md"))).text(), "DENY_TICKET_PARENT")
	rec := p.record()
	if str(rec, "code") != "DENY_TICKET_PARENT" || !reflect.DeepEqual(strs(rec, "rules"), []string{"builtin:ticket-parent"}) {
		t.Errorf("記録: %v", rec)
	}
	tree := p.worktree(p.lib, "i0009-01-01")
	allowed := parse(t, p.write(p.ws, filepath.Join(tree, "skills", "deploy", "SKILL.md")))
	if allowed.Decision == "deny" {
		t.Errorf("範囲の中は通る: %+v", allowed)
	}
	mustNotHave(t, allowed.text(), "DENY")
	mustHave(t, parse(t, p.write(p.ws, filepath.Join(tree, "skills", "other", "SKILL.md"))).text(), "DENY_TICKET_SCOPE")

	loose := p.worktree(p.lib, "loose")
	for _, rel := range []string{"skills/deploy/SKILL.md", "docs/notes.md"} {
		done := p.write(p.ws, filepath.Join(loose, filepath.FromSlash(rel)))
		if done.Code != 0 || parse(t, done).Decision != "ask" {
			t.Errorf("チケットの無いワークツリーの %s は ask のはず: %d %s", rel, done.Code, done.Stdout)
		}
	}
}

func TestTheParentWorktreeTakesOnlyThePlaces(t *testing.T) {
	t.Parallel()
	// 親のワークツリーに書けるのは置き場だけ。実装は子のワークツリーで書く。親の範囲の中でも、置き場の外は止める。
	// 止める理由は範囲の外ではなく、親が作業の場ではないこと（DENY_TICKET_PARENT）。案内は子を起こす手を言う。
	p := newProjects(t)
	p.familyInLib("i0007")
	p.mustAgree()
	home := p.worktree(p.lib, "i0007")
	for _, rel := range []string{"src/a/a.py", "docs/x.md", "wip/design/d.md"} {
		out := mustDeny(t, p.write(p.ws, filepath.Join(home, filepath.FromSlash(rel))))
		mustHave(t, out.text(), "DENY_TICKET_PARENT", "子チケット")
	}
	for _, rel := range []string{"wip/handoff/a.md", "wip/proposals/todo/i0099.md"} {
		mustNotHave(t, parse(t, p.write(p.ws, filepath.Join(home, filepath.FromSlash(rel)))).text(), "DENY_TICKET_PARENT")
	}
	// 子のワークツリーは、いつもどおり子の範囲で判定する。
	child := p.worktree(p.lib, "i0007-01-01")
	mustNotHave(t, parse(t, p.write(p.ws, filepath.Join(child, "src", "a", "a.py"))).text(), "DENY")
}

// 4b. プロジェクトを決めるのは提案を置いた場所

func TestThePlaceDecidesTheProjectForParentAndChildAlike(t *testing.T) {
	t.Parallel()
	p := newProjects(t)
	write(t, p.proposalFile(p.lib, "i0007"), ticketText("i0007", "", "", []string{"src/*"}, 0))
	write(t, p.proposalFile(p.lib, "i0007-01-01"), ticketText("i0007-01-01", "i0007", "", []string{"src/a/*"}, 0))
	want := map[string]string{"i0007": "lib", "i0007-01-01": "lib"}
	// 承認の前から、親も子も同じプロジェクトとしてボードに出る
	found, pending := p.board()
	if !reflect.DeepEqual(found, want) {
		t.Errorf("承認前のプロジェクト: %v", found)
	}
	sort.Strings(pending)
	if !reflect.DeepEqual(pending, []string{"i0007", "i0007-01-01"}) {
		t.Errorf("pending_approval = %v", pending)
	}

	approved := p.mustAgree()
	// 承認の画面は、書き込みが向かうリポジトリをユーザに見せる
	mustHave(t, approved.Stdout, "■ プロジェクト: lib")
	// 承認は `project:` を書き足さない。承認済みチケットのプロジェクトも置き場（ツリー）から決まる
	for _, name := range []string{"i0007", "i0007-01-01"} {
		mustNotHave(t, read(t, p.approvedPath("doing", name+".md")), "project:")
	}
	if found, _ := p.board(); !reflect.DeepEqual(found, want) {
		t.Errorf("承認後のプロジェクト: %v", found)
	}
}

func TestADeclarationThatDisagreesWithThePlaceIsNotApproved(t *testing.T) {
	t.Parallel()
	p := newProjects(t)
	write(t, filepath.Join(p.ws, "wip", "proposals", "todo", "i0007.md"), ticketText("i0007", "", "lib", []string{"src/*"}, 0))
	r := p.agree()
	if r.Code != 1 {
		t.Errorf("終了コード = %d, 1 のはず: %s%s", r.Code, r.Stdout, r.Stderr)
	}
	mustHave(t, r.Stderr, "置き場（ワークスペース）と違う", "wip/proposals/ に置いて")
	if exists(filepath.Join(p.approvedPlace, "doing", "i0007.md")) {
		t.Error("承認済みの置き場に置いてはいけない")
	}
}

func TestAChildPlacedApartFromItsParentIsNotApproved(t *testing.T) {
	t.Parallel()
	p := newProjects(t)
	write(t, p.proposalFile(p.lib, "i0007"), ticketText("i0007", "", "", []string{"src/*"}, 0))
	write(t, filepath.Join(p.app, "wip", "proposals", "todo", "i0007-01-01.md"), ticketText("i0007-01-01", "i0007", "", []string{"src/a/*"}, 0))
	r := p.agree()
	// 承認の対象の一部（子）が失敗したので、通ったぶん（親）を置いてから 4 で終わる（何も置かない失敗の 1 とは分ける）。
	if r.Code != 4 {
		t.Errorf("終了コード = %d, 4 のはず: %s%s", r.Code, r.Stdout, r.Stderr)
	}
	mustHave(t, r.Stderr, "子は親と同じ置き場に置いて")
	if !exists(p.approvedPath("doing", "i0007.md")) {
		t.Error("親は承認済みの置き場に置かれるはず")
	}
	if exists(filepath.Join(p.approvedPlace, "doing", "i0007-01-01.md")) {
		t.Error("子は置かれないはず")
	}
}

func TestAProposalInsideAProjectWorktreeIsReadWithoutComplaint(t *testing.T) {
	t.Parallel()
	// 提案はそのツリーの wip/proposals/ に置く。プロジェクトのワークツリーの中も普通の置き場で、承認をプロジェクトの git で
	// 共有するために、そこに置く。置き場はワークツリーの元リポジトリで決まり、承認済みチケットは記録したパスから引くので閉じられる。
	p := newProjects(t)
	tree := p.worktree(p.lib, "i0010")
	// 計画のある親だけが着手できる。定義はプロジェクトのレイヤー（config）に置く。
	samples.WritePlainPhases(t, p.lib)
	write(t, filepath.Join(tree, "wip", "proposals", "todo", "i0010.md"), ticketText("i0010", "", "", []string{"src/*"}, 1))
	approved := p.mustAgree()
	mustNotHave(t, approved.Stderr, "の中に提案がある")
	mustHave(t, approved.Stdout, "■ プロジェクト: lib")

	// 承認で todo/ から動き、着手と完了はそのツリーの置き場の中で動く。
	if exists(filepath.Join(tree, "wip", "proposals", "todo", "i0010.md")) {
		t.Error("承認で todo/ から動くはず")
	}
	if r := p.ccnavi("", "ticket", "start", "i0010"); r.Code != 0 {
		t.Fatalf("着手: %d\n%s%s", r.Code, r.Stdout, r.Stderr)
	}
	// 着手の欄は、そのツリーの置き場の承認済みチケットに書かれる。
	doing := filepath.Join(tree, ".ccnavi", "approved", "doing", "i0010.md")
	if !exists(doing) {
		t.Fatalf("%s が無い", doing)
	}
	mustNotHave(t, read(t, doing), `started_at: ""`)
}

func TestLintNamesATicketPlaceThatIsNotScanned(t *testing.T) {
	t.Parallel()
	// 置き場が走査されないので、提案はどこにも出ない。何も言わないと提案が消えたように見えるので名指しする。
	// 名前が projects/ に在っても、ワークスペースの wip/<名前>/proposals/ は走査されない。正しい置き場（projects/<名前>/wip/proposals/）をつけて名指しする。
	for name, c := range map[string]struct {
		rel, name, place, also string
	}{
		"unknown name": {"wip/gone/proposals/todo/i0009.md", "i0009", "wip/gone/proposals", ""},
		"a known name": {"wip/lib/proposals/todo/i0011.md", "i0011", "wip/lib/proposals", "projects/lib/wip/proposals"},
	} {
		t.Run(name, func(t *testing.T) {
			t.Parallel()
			p := newProjects(t)
			write(t, filepath.Join(p.ws, filepath.FromSlash(c.rel)), ticketText(c.name, "", "", []string{"src/*"}, 0))
			r := p.ccnavi("", "--lint")
			out := r.Stdout + r.Stderr
			mustHave(t, out, c.place, "走査されていない", c.also)
			if found, _ := p.board(); len(found) != 0 {
				t.Errorf("提案はどこにも出ない: %v", found)
			}
		})
	}
}

func TestAProjectTicketMovesInsideItsOwnTree(t *testing.T) {
	t.Parallel()
	p := newProjects(t)
	write(t, p.proposalFile(p.lib, "i0007"), ticketText("i0007", "", "", []string{"src/*"}, 0))
	p.mustAgree()
	// 承認はプロジェクトの todo/ からプロジェクトの doing/ へ動かす。取り消すと done/ へ。置き場はプロジェクトの git が持つ。
	libApproved := filepath.Join(p.worktree(p.lib, "i0007"), ".ccnavi", "approved")
	if exists(p.proposalFile(p.lib, "i0007")) {
		t.Error("承認で提案は動くはず")
	}
	if !exists(filepath.Join(libApproved, "doing", "i0007.md")) {
		t.Error("doing/ に置かれるはず")
	}
	if r := p.ccnavi("", "ticket", "cancel", "i0007", "--reason", "やめる"); r.Code != 0 {
		t.Fatalf("取り消し: %d\n%s%s", r.Code, r.Stdout, r.Stderr)
	}
	if !exists(filepath.Join(libApproved, "done", "i0007.md")) {
		t.Error("done/ へ動くはず")
	}
}

func TestAnUndecidedProjectFamilyStopsTheHookAndTheState(t *testing.T) {
	t.Parallel()
	// プロジェクトの親子のチケットも、親のワークツリーが決まらなければ止まる。親のワークツリーはプロジェクトのリポジトリから切る。
	p := newProjects(t)
	write(t, p.proposalFile(p.lib, "i0007"), ticketText("i0007", "", "", []string{"src/*"}, 0))
	p.mustAgree()
	tree := p.worktree(p.lib, "i0007")
	run.Git(t, tree, "add", "-A")
	run.Git(t, tree, "commit", "--quiet", "--allow-empty", "-m", "approve")
	// 親のワークツリーに書けるのは置き場だけ（実装は子のワークツリーで書く）。
	before := p.write(p.ws, filepath.Join(tree, "wip", "handoff", "a.py"))
	if parse(t, before).Decision == "deny" {
		t.Errorf("置き場には書ける: %s", before.Stdout)
	}
	// 親のワークツリーの HEAD がブランチ名と合わなくなれば、決まらない。
	run.Git(t, tree, "checkout", "--quiet", "-b", "elsewhere")
	// 親のワークツリーは範囲の中でも置き場以外に書けない。決まらなくなっても止まる側は同じ。
	mustDeny(t, p.write(p.ws, filepath.Join(tree, "src", "a.py")))
	cancelled := p.ccnavi("", "ticket", "cancel", "i0007", "--reason", "やめる")
	if cancelled.Code == 0 {
		t.Error("決まらないチケットは取り消せない")
	}
	mustHave(t, cancelled.Stderr, "合わない")
}

// 5. 実行後チェックはツリーごと

func TestPostMonitoringReadsTheProjectTreeTheCallTouched(t *testing.T) {
	t.Parallel()
	p := newProjects(t)
	if r := p.hook("", p.ws, "UserPromptSubmit", nil); r.Code != 0 {
		t.Errorf("code = %d: %s", r.Code, r.Stderr)
	}
	// 初回の実行後は記録を取るだけ。そのあとで app の schema/ をシェルが汚す。
	post := map[string]any{"command": "python gen.py"}
	if r := p.hook("Bash", p.app, "PostToolUse", post); r.Code != 0 {
		t.Errorf("code = %d: %s", r.Code, r.Stderr)
	}
	write(t, filepath.Join(p.app, "schema", "x.sql"), "dirty\n")

	// ワークスペースに居る Bash の後では app のツリーを見ない。
	elsewhere := p.hook("Bash", p.ws, "PostToolUse", post)
	if elsewhere.Code != 0 {
		t.Errorf("code = %d: %s", elsewhere.Code, elsewhere.Stderr)
	}
	mustNotHave(t, parse(t, elsewhere).text(), "POST_VIOLATION")

	// app に居る Bash の後では、app のルールで app のツリーを見る。
	after := p.hook("Bash", p.app, "PostToolUse", post)
	if after.Code != 2 {
		t.Fatalf("終了コード = %d, 2 のはず: %s%s", after.Code, after.Stdout, after.Stderr)
	}
	mustHave(t, after.Stderr, "POST_VIOLATION", appRulesID, "tree: app")
	rec := p.record()
	if str(rec, "project") != "app" || !reflect.DeepEqual(strs(rec, "rules"), []string{appRulesID}) {
		t.Errorf("記録: %v", rec)
	}

	// ターンの終わりは全部のツリーを見て、ツリーの名前をつけてユーザに言う。
	stopped := p.hook("", p.ws, "Stop", nil)
	if stopped.Code != 0 {
		t.Errorf("code = %d: %s", stopped.Code, stopped.Stderr)
	}
	mustHave(t, parse(t, stopped).System, "app: schema/x.sql")
}

// 6. --lint がプロジェクトまわりの設定の誤りを言う

func TestLintNamesProjectConfigProblems(t *testing.T) {
	t.Parallel()
	p := newProjects(t)
	write(t, base.ConfigPath(p.app, "rules"), "version: 1\ndeny: [\n")
	mkdirs(t, filepath.Join(p.lib, ".claude"))
	// `projects/` を無視しない。追跡はしない: ここで `git add -A` すると、入れ子のリポジトリが `projects/app` の名前で索引に載り、
	// 「追跡されている」の側の知らせに切り替わって、「無視されていない」を確かめられなくなる。
	write(t, filepath.Join(p.ws, ".gitignore"), "/.claude/\n")

	r := p.ccnavi("", "--lint")
	out := r.Stdout + r.Stderr
	mustHave(t, out, "projects/ がワークスペースの git で無視されていない", "(projects/app)")
	// `.claude/` を持つだけのプロジェクトには苦情を出さない
	mustNotHave(t, out, "(projects/lib)")
	// scratchpad の無視行は求めない（実行前チェックの話）
	mustNotHave(t, out, "(scratch")

	// `--explain` はレイヤーごとに並べる。読めないレイヤーはその位置で言う。
	explained := p.ccnavi("", "--explain")
	mustHave(t, explained.Stdout, "■ rules app", "■ rules lib", "読めない")
}

// 7. projects/ を数えない設定では前と同じ

func TestWithoutAProjectsDirEverythingIsJudgedByTheWorkspaceRules(t *testing.T) {
	t.Parallel()
	// 「数えない」を言う口は、`projects/` を作らないこと（置き場は固定）。消さずに `projects/` の外へ動かす
	// （Windows は .git の中の読み取り専用を消せない）。動かした先の app は入れ子の git のまま。
	// `projects/` の外なら、その中でもワークスペースのルールで判定される。
	p := newProjects(t)
	moved := filepath.Join(p.ws, "moved-away")
	if err := os.Rename(filepath.Join(p.ws, "projects"), moved); err != nil {
		t.Fatal(err)
	}
	app := filepath.Join(moved, "app")
	passed := p.write(p.ws, filepath.Join(app, "schema", "x.sql"))
	if passed.Code != 0 {
		t.Errorf("code = %d: %s", passed.Code, passed.Stderr)
	}
	mustNotHave(t, parse(t, passed).text(), "DENY")
	if _, has := p.record()["project"]; has {
		t.Errorf("project が付いている: %v", p.record())
	}
	passed = p.bash(app, "psql")
	if passed.Code != 0 {
		t.Errorf("code = %d: %s", passed.Code, passed.Stderr)
	}
	mustNotHave(t, parse(t, passed).text(), "DENY")
}

func TestProjectRulesFileSwapsOneProjectsRulesForDiagnosticsOnly(t *testing.T) {
	t.Parallel()
	// VS Code 拡張が、編集中の lib のルールを保存せずに試す形。lib の deny に `*/docs/*` を足した一時ファイルを渡すと、
	// lib への書き込みはそれで判定される。
	p := newProjects(t)
	edited := payload{"version": 1, "allow": libRules["allow"], "deny": append(libRules["deny"].([]payload), rule("docs", "Write|Edit", "*/docs/*", "docs are generated."))}
	tmp := write(t, filepath.Join(p.ws, "tmp", "lib-rules.yml"), jsonText(t, edited))
	target := filepath.Join(p.lib, "docs", "a.md")
	swap := "lib=" + tmp

	tried := p.ccnavi("", "--test", "Write", target, "--json", "--project-rules-file", swap)
	if tried.Code != 0 {
		t.Fatalf("code = %d: %s", tried.Code, tried.Stderr)
	}
	var body verdictBody
	if err := json.Unmarshal([]byte(tried.Stdout), &body); err != nil {
		t.Fatalf("JSON でない: %v\n%s", err, tried.Stdout)
	}
	if body.s("verdict") != "deny" {
		t.Fatalf("deny のはず: %s", tried.Stdout)
	}
	hit, _ := body["rules"].([]any)[0].(map[string]any)
	// ヒットしたルールはプロジェクトの名前付きで引け、翻訳後の式まで見える。
	if hit["id"] != "tmp/lib-rules.yml:docs" || hit["source"] != "file" || hit["written"] != "*/docs/*" {
		t.Errorf("hit = %v", hit)
	}

	// app は差し替えていないので、app への同じ書き込みは前のまま通る。
	other := p.ccnavi("", "--test", "Write", filepath.Join(p.app, "docs", "a.md"), "--json", "--project-rules-file", swap)
	var otherBody verdictBody
	if err := json.Unmarshal([]byte(other.Stdout), &otherBody); err != nil || otherBody.s("verdict") == "deny" {
		t.Errorf("app は通るはず: %v %s", err, other.Stdout)
	}

	// --lint --json も差し替えた側を読む。不正な一時ファイルは lib の error として出る。
	broken := write(t, filepath.Join(p.ws, "tmp", "broken.yml"), "version: 1\ndeny: [\n")
	linted := p.ccnavi("", "--lint", "--json", "--project-rules-file", "lib="+broken)
	var report struct {
		Projects []string `json:"projects"`
		Problems []struct {
			Severity string `json:"severity"`
			Where    string `json:"where"`
		} `json:"problems"`
	}
	if err := json.Unmarshal([]byte(linted.Stdout), &report); err != nil {
		t.Fatalf("JSON でない: %v\n%s", err, linted.Stdout)
	}
	if !reflect.DeepEqual(report.Projects, []string{"app", "lib"}) {
		t.Errorf("projects = %v", report.Projects)
	}
	var lib, app bool
	for _, pr := range report.Problems {
		if pr.Severity == "error" {
			lib = lib || strings.HasPrefix(pr.Where, "(projects/lib)")
			app = app || strings.HasPrefix(pr.Where, "(projects/app)")
		}
	}
	if !lib || app {
		t.Errorf("lib の error だけが出るはず: %s", linted.Stdout)
	}

	// hook からの判定は差し替えを見ない。保存していないルールが判定に使われる経路を持たない。
	body2 := payload{"hook_event_name": "PreToolUse", "tool_name": "Write", "cwd": p.ws, "session_id": "s1", "tool_input": payload{"file_path": target}}
	ignored := p.ccnavi(jsonText(t, body2), "--mode", "enable", "--project-rules-file", swap)
	if ignored.Code != 0 || parse(t, ignored).Decision == "deny" {
		t.Errorf("差し替えを見ずに通るはず: %d %s", ignored.Code, ignored.Stdout)
	}
	mustHave(t, ignored.Stderr, "診断")

	// 形が違えば止まる。
	malformed := p.ccnavi("", "--lint", "--json", "--project-rules-file", "lib")
	if malformed.Code != 1 {
		t.Errorf("終了コード = %d, 1 のはず", malformed.Code)
	}
	mustHave(t, malformed.Stderr, "<名前>=<パス>")
}
