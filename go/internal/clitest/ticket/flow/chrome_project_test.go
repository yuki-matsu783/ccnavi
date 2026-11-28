package flow

// Chrome の入口のプロジェクトのリポジトリと「始める」の受入テスト。
//
//  1. プロジェクトのリポジトリ（手元で projects/<名前> に clone されるもの）の親子のチケットを、手元と同じ形の仮のツリーで判定し、
//     承認で書くもの（Changes）はその親子のチケットの親のブランチだけ
//  2. プロジェクトの設定は、プロジェクトの統合先の .ccnavi/config/project/ をそのまま使う。共通設定はワークスペースの
//     統合先の .ccnavi/config/common/ だけで、プロジェクトの .ccnavi/config/common/（同期が配るミラー）は読まない
//  3. 「始める」: issue の番号から識別子（feature-12-<slug>、プロジェクトなら feature-12-web-<slug>）を決め、統合先の done/ にある・
//     同じ名前のブランチがある・開いた親子のチケットに同じ識別子がある・互換の版が違う、のどれでも始められない
//  4. プロジェクト名が予約の名前（common・self）や識別子の形でなければ受けない
import (
	"encoding/json"
	"maps"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/exe"
	"ccnavi/internal/harness/samples"
	"ccnavi/internal/harness/ticketkit"
)

// mirrorRules はミラー（プロジェクトの .ccnavi/config/common/）。ワークスペースの中では共通設定として読まれない。
const mirrorRules = `{"version": 1, "deny": [{"id": "mirror-only", "match": "Bash", "glob": "*x*"}]}` + "\n"

var projectActor = map[string]any{"account": "lab-approver", "version": "9.9.9"}

func workspaceSnapshot(compat string, extra map[string]string) map[string]any {
	files := map[string]any{
		".claude/settings.json":           `{"env": {"CCNAVI_TICKET_CONTROL": "enable"}}` + "\n",
		".ccnavi/config/common/rules.yml": `{"version": 1, "deny": []}` + "\n",
		samples.CompatFile:                "#!/bin/sh\nCCNAVI_COMPAT=" + compat + "\n",
	}
	for k, v := range extra {
		files[k] = v
	}
	return map[string]any{"integration": map[string]any{"name": "main", "source": "default", "head": zeroHead}, "files": files, "binary": []any{}, "links": []any{}}
}

// projectIntegration はプロジェクトの統合先。フェーズ定義は、ワークスペースの共通設定と、親の project: が指すプロジェクトの config の和。
func projectIntegration(extra map[string]string) map[string]any {
	files := map[string]any{
		".ccnavi/config/project/rules.yml":  `{"version": 1, "deny": []}` + "\n",
		".ccnavi/config/project/phases.yml": samples.Phases,
	}
	for k, v := range extra {
		files[k] = v
	}
	return map[string]any{"head": zeroHead, "files": files, "binary": []any{}}
}

func familyFiles(ident string) map[string]any {
	return map[string]any{
		"wip/proposals/todo/" + ident + ".md":       samples.ParentText(ident, samples.Plan("research"), samples.ParentOpts{Issue: 12}),
		"wip/proposals/todo/" + ident + "-01-01.md": samples.ChildTextReview(ident+"-01-01", ident, 1, []string{"wip/research/*"}, samples.Off),
		// 親のブランチの上の設定は読まない（置き場の外なので拡張はそもそも読まない。
		// 親のブランチの上で書き換えて承認やレビューを不要にさせない）
	}
}

type chromeRaw struct{ t *testing.T }

func (c chromeRaw) handle(request map[string]any, memfs string) map[string]any {
	c.t.Helper()
	raw, err := json.Marshal(request)
	if err != nil {
		c.t.Fatal(err)
	}
	return ticketkit.ParseJSON(c.t, samples.ChromeHandle(c.t, exe.ChromePath(c.t), string(raw), memfs))
}

type projectAsk struct {
	family   string
	branches map[string]any
	project  string
	ws       map[string]any
	extra    map[string]any
}

func (c chromeRaw) project(op string, a projectAsk, tmp string) map[string]any {
	if a.family == "" {
		a.family = "web-i0012"
	}
	if a.project == "" {
		a.project = "web"
	}
	if a.ws == nil {
		a.ws = workspaceSnapshot(exe.Compat(c.t), nil)
	}
	if a.branches == nil {
		a.branches = map[string]any{"trunk": projectIntegration(nil), a.family: map[string]any{"head": zeroHead, "files": familyFiles(a.family)}}
	}
	snapshot := map[string]any{
		"integration": map[string]any{"name": "trunk", "source": "setting", "head": zeroHead},
		"branches":    a.branches, "absent": []any{}, "project": a.project, "workspace": a.ws,
	}
	request := map[string]any{"schema": samples.ChromeSchema, "op": op, "family": a.family, "stamp": chromeStamp, "settings": jv(a.ws, "files", ".claude/settings.json"), "snapshot": snapshot}
	maps.Copy(request, a.extra)
	return c.handle(request, filepath.Join(tmp, "memfs"))
}

func batchIDs(board map[string]any) []string {
	var ids []string
	for _, e := range jl(board, "batch") {
		ids = append(ids, js(e, "ticket"))
	}
	return ids
}

func TestTheBoardOfAProjectFamilyListsItsProposals(t *testing.T) {
	t.Parallel()
	tmp := base.TempDir(t)
	board := chromeRaw{t}.project("board", projectAsk{}, tmp)
	if _, bad := board["error"]; bad {
		t.Fatalf("error: %v", board["error"])
	}
	if got := batchIDs(board); !reflect.DeepEqual(got, []string{"web-i0012", "web-i0012-01-01"}) || !jb(board, "write", "allowed") || len(jl(board, "rejected")) != 0 {
		t.Errorf("board = %v", board)
	}
	// 画面の本文はプロジェクトの置き場を名指しする（仮のツリーのパスは出さない）。
	ticketkit.Lacks(t, js(board, "text"), filepath.Join(tmp, "memfs"))
}

func TestApprovingAProjectFamilyWritesOnlyItsBranch(t *testing.T) {
	t.Parallel()
	tmp := base.TempDir(t)
	c := chromeRaw{t}
	board := c.project("board", projectAsk{}, tmp)
	shown := map[string]any{"ids": batchIDs(board), "digest": board["digest"]}
	body := c.project("plan", projectAsk{extra: map[string]any{"only": board["only"], "shown": shown, "actor": projectActor}}, tmp)
	if _, bad := body["error"]; bad || !isNull(body, "mismatch") {
		t.Fatalf("body = %v", body)
	}
	changes := jm(body, "changes")
	if len(changes) != 1 || changes["web-i0012"] == nil {
		t.Fatalf("changes のブランチ = %v", changes)
	}
	paths := map[string]map[string]any{}
	for _, r := range jl(changes, "web-i0012") {
		paths[js(r, "path")] = jm(r)
	}
	if js(paths["wip/proposals/todo/web-i0012.md"], "op") != "delete" {
		t.Errorf("提案が消えない: %v", paths)
	}
	// 承認は提案の中身を変えない。
	ticketkit.Lacks(t, js(paths[".ccnavi/approved/doing/web-i0012.md"], "content"), "ccnavi_approved")
}

// フェーズ定義は共通設定（ワークスペースの統合先）と、親の project: が指す config の和。ミラーは足さない。
func TestTheProjectFamilyIsJudgedByTheProjectPhasesAndIgnoresTheMirror(t *testing.T) {
	t.Parallel()
	tmp := base.TempDir(t)
	c := chromeRaw{t}
	trunk := projectIntegration(map[string]string{".ccnavi/config/common/phases.yml": samples.Phases, ".ccnavi/config/common/rules.yml": mirrorRules})
	family := map[string]any{"head": zeroHead, "files": familyFiles("web-i0012")}
	board := c.project("board", projectAsk{branches: map[string]any{"trunk": trunk, "web-i0012": family}}, tmp)
	if _, bad := board["error"]; bad || !reflect.DeepEqual(batchIDs(board), []string{"web-i0012", "web-i0012-01-01"}) {
		t.Fatalf("board = %v", board)
	}
	// プロジェクトの config に定義が無ければ、ミラーの定義があっても計画の定義が見つからない。
	bareFiles := map[string]any{}
	for k, v := range jm(trunk, "files") {
		if !strings.Contains(k, "config/project/phases.yml") {
			bareFiles[k] = v
		}
	}
	bare := map[string]any{"trunk": map[string]any{"head": zeroHead, "files": bareFiles, "binary": []any{}}, "web-i0012": family}
	if got := batchIDs(c.project("board", projectAsk{branches: bare}, tmp)); reflect.DeepEqual(got, []string{"web-i0012", "web-i0012-01-01"}) {
		t.Error("ミラーの定義で通った")
	}
	// ワークスペースの共通設定に定義があれば、プロジェクトの config に無くてもその定義が効く。
	common := workspaceSnapshot(exe.Compat(t), map[string]string{".ccnavi/config/common/phases.yml": samples.Phases})
	board = c.project("board", projectAsk{branches: bare, ws: common}, tmp)
	if _, bad := board["error"]; bad || !reflect.DeepEqual(batchIDs(board), []string{"web-i0012", "web-i0012-01-01"}) {
		t.Errorf("board = %v", board)
	}
}

func TestAReservedOrMalformedProjectNameIsRefused(t *testing.T) {
	t.Parallel()
	tmp := base.TempDir(t)
	for _, name := range []string{"common", "Self", "../x", "a b"} {
		if _, bad := (chromeRaw{t}).project("board", projectAsk{project: name}, tmp)["error"]; !bad {
			t.Errorf("%q を受けた", name)
		}
	}
}

func TestAProjectRepositoryNeedsTheWorkspace(t *testing.T) {
	t.Parallel()
	request := map[string]any{"schema": samples.ChromeSchema, "op": "board", "family": "web-i0012", "snapshot": map[string]any{
		"integration": map[string]any{"name": "trunk", "source": "setting", "head": zeroHead},
		"branches":    map[string]any{"trunk": projectIntegration(nil)}, "absent": []any{}, "project": "web",
	}}
	body := chromeRaw{t}.handle(request, filepath.Join(base.TempDir(t), "m"))
	ticketkit.Has(t, js(body, "error"), "ワークスペースの統合先")
}

func TestADifferentCompatInTheWorkspaceRefusesWriting(t *testing.T) {
	t.Parallel()
	board := chromeRaw{t}.project("board", projectAsk{ws: workspaceSnapshot(compatBump(t), nil)}, base.TempDir(t))
	if jb(board, "write", "allowed") {
		t.Error("互換が違うのに書ける")
	}
	ticketkit.Has(t, js(board, "write", "reason"), "拡張を更新してください。")
}

// ---- 「始める」。識別子は issue_identifier の 1 つだけから決める（手元の CLI と拡張の WASM が同じ関数を使う）。

type startOpts struct {
	issue   any
	project string
	branch  map[string]any
	taken   []string
	compat  string
	done    []string
	title   any
	prefix  any
}

func startBody(t *testing.T, o startOpts) map[string]any {
	t.Helper()
	if o.compat == "" {
		o.compat = exe.Compat(t)
	}
	if o.title == nil {
		o.title = "Login form"
	}
	integ := map[string]any{".claude/settings.json": "{}\n", samples.CompatFile: "#!/bin/sh\nCCNAVI_COMPAT=" + o.compat + "\n"}
	for _, d := range o.done {
		integ[".ccnavi/approved/done/"+d+".md"] = samples.ParentText(d, samples.Plan("research"), samples.ParentOpts{})
	}
	branches := map[string]any{"main": map[string]any{"head": zeroHead, "files": integ}}
	maps.Copy(branches, o.branch)
	snapshot := map[string]any{"integration": map[string]any{"name": "main", "source": "default", "head": zeroHead}, "branches": branches, "absent": []any{}}
	if o.project != "" {
		snapshot["project"] = o.project
		snapshot["workspace"] = workspaceSnapshot(o.compat, nil)
	}
	taken := make([]any, len(o.taken))
	for i, s := range o.taken {
		taken[i] = s
	}
	request := map[string]any{"schema": samples.ChromeSchema, "op": "start", "snapshot": snapshot, "issue": o.issue, "title": o.title, "taken": taken}
	if o.prefix != nil {
		request["prefix"] = o.prefix
	}
	return chromeRaw{t}.handle(request, filepath.Join(base.TempDir(t), "m"))
}

func hasProblem(body map[string]any, word string) bool {
	for _, p := range jl(body, "problems") {
		if strings.Contains(p.(string), word) {
			return true
		}
	}
	return false
}

// feature-<番号>-<slug>。slug は issue のタイトルから作る。
func TestTheIdentifierComesFromTheIssue(t *testing.T) {
	t.Parallel()
	want := map[string]any{"identifier": "feature-12-login-form", "integration": "main", "problems": []any{}, "schema": 1.0}
	if got := startBody(t, startOpts{issue: 12}); !reflect.DeepEqual(got, want) {
		t.Errorf("got %v, want %v", got, want)
	}
	for _, c := range []struct {
		name string
		o    startOpts
		want string
	}{
		{"empty title", startOpts{issue: 12345, title: ""}, "feature-12345-issue"},
		{"japanese title", startOpts{issue: 64, title: "統合先の解決"}, "feature-64-統合先の解決"},
		{"project", startOpts{issue: 12, project: "web"}, "feature-12-web-login-form"},
		{"prefix", startOpts{issue: 7, prefix: "hotfix", title: "ログイン"}, "hotfix-7-ログイン"},
		// 敵対的なタイトルは安全な名前になる。
		{"hostile title", startOpts{issue: 9, title: "$(rm -rf /); ../../x `id` \\ \n 'q'"}, "feature-9-rm-rf-x-id-q"},
	} {
		body := startBody(t, c.o)
		if js(body, "identifier") != c.want || len(jl(body, "problems")) != 0 {
			t.Errorf("%s: %v, want %s", c.name, body, c.want)
		}
	}
}

func TestBadTitlesPrefixesAndNumbersAreRefused(t *testing.T) {
	t.Parallel()
	cases := []startOpts{
		{issue: 12, title: []any{"x"}}, {issue: 12, title: strings.Repeat("x", 1001)},
		{issue: 12, prefix: "release"}, {issue: 12, prefix: "Main"}, {issue: 12, prefix: "a-b"}, {issue: 12, prefix: 3},
		{issue: 0}, {issue: -3}, {issue: true}, {issue: "12"}, {issue: nil},
	}
	for _, c := range cases {
		if _, bad := startBody(t, c)["error"]; !bad {
			t.Errorf("%+v を受けた", c)
		}
	}
}

func TestAClosedIdentifierIsRefused(t *testing.T) {
	t.Parallel()
	body := startBody(t, startOpts{issue: 55, done: []string{"feature-55-login-form"}})
	if !hasProblem(body, "done/ でクローズ済み") {
		t.Errorf("body = %v", body)
	}
	problems := jl(body, "problems")
	ticketkit.Has(t, problems[len(problems)-1].(string), "フォールバック")
	if !hasProblem(startBody(t, startOpts{issue: 55, done: []string{"Feature-55-Login-Form"}}), "done/ でクローズ済み") {
		t.Error("大文字小文字違いで閉じた名前を通した")
	}
	// 前の形（i0055）で閉じていても、新しい名前とは重ならない。
	if got := jl(startBody(t, startOpts{issue: 55, done: []string{"i0055"}}), "problems"); len(got) != 0 {
		t.Errorf("problems = %v", got)
	}
}

func TestAnExistingBranchOrOpenFamilyIsRefused(t *testing.T) {
	t.Parallel()
	if !hasProblem(startBody(t, startOpts{issue: 12, taken: []string{"FEATURE-12-login-form"}}), "同じ名前のブランチ") {
		t.Error("同じ名前のブランチを通した")
	}
	name := "feature-12-login-form"
	other := map[string]any{"topic": map[string]any{"head": zeroHead, "files": map[string]any{
		"wip/proposals/todo/" + name + "-01-01.md": samples.ChildText(name+"-01-01", name, 1, []string{"src/*"}),
	}}}
	if !hasProblem(startBody(t, startOpts{issue: 12, branch: other}), "未クローズの親子チケット topic") {
		t.Error("開いた親子を通した")
	}
}

func TestStartRefusesADifferentCompat(t *testing.T) {
	t.Parallel()
	if !hasProblem(startBody(t, startOpts{issue: 12, compat: compatBump(t)}), "互換") {
		t.Error("互換が違うのに通した")
	}
}
