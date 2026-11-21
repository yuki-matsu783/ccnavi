package union

import (
	"ccnavi/internal/harness/configws"
	"encoding/json"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
)

// 設定 3 本の和の受入テスト。rules の合成。
//
// レイヤーは 3 種。共通レイヤー（`.ccnavi/config/common/`）、ワークスペース自身のレイヤー、プロジェクトのレイヤー。
// lib は 3 本とも持ち、app は `.ccnavi/` を持たない（無いレイヤー = 空）。

func p(parts ...string) string { return filepath.Join(parts...) }

// Write / Edit は共通レイヤー + 行き先のレイヤーの和。

func TestEachLayerDenyAppliesToItsOwnFiles(t *testing.T) {
	t.Parallel()
	w := newWS(t, configws.KindBase)
	// id は共通レイヤーなら裸、プロジェクトのレイヤーなら `lib:`、自身のレイヤーなら `self:` が付く。
	// 記録の source はそのレイヤー、project は行き先のプロジェクトで、ワークスペースのファイルなら持たない。
	// cwd は行き先と別の側に置く。レイヤーを選ぶのは行き先で、cwd ではない。
	for _, c := range []struct {
		source, target, cwd, id, message string
		lib                              bool
	}{
		{"common", p(w.Lib, ".env"), w.Root, "credentials", "credentials are not edited", true},
		{"lib", p(w.Lib, "schema", "x.sql"), w.Root, "lib:schema", "write a migration", true},
		{"self", p(w.Root, "generated", "x.py"), w.Lib, "self:generated", "are rebuilt", false},
	} {
		t.Run(c.id, func(t *testing.T) {
			w := w.as(t)
			denied := w.Hook("Write", c.cwd, configws.Obj{"file_path": c.target})
			w.AssertDenied(denied, c.id)
			if !strings.Contains(w.Reason(denied), c.message) {
				t.Errorf("理由に %q が無い: %s", c.message, w.Reason(denied))
			}
			rec := w.LastRecord()
			if !slices.Equal(rec.Hits(), []string{c.id}) {
				t.Errorf("hits = %v", rec.Hits())
			}
			if rec.Str("source") != c.source {
				t.Errorf("source = %q, want %q", rec.Str("source"), c.source)
			}
			if c.lib {
				if rec.Str("project") != "lib" {
					t.Errorf("project = %q", rec.Str("project"))
				}
			} else if _, has := rec["project"]; has {
				t.Errorf("ワークスペースのファイルに project がある: %v", rec["project"])
			}
		})
	}
}

func TestALayerDenyDoesNotReachOtherTrees(t *testing.T) {
	t.Parallel()
	// プロジェクトのレイヤーは他のプロジェクトにもワークスペースにも、自身のレイヤーはプロジェクトにも足さない。
	w := newWS(t, configws.KindBase)
	for _, target := range [][]string{
		{w.App, "schema", "x.sql"},
		{w.Root, "schema", "x.sql"},
		{w.Lib, "generated", "x.py"},
	} {
		path := p(target...)
		rel, _ := filepath.Rel(w.Root, path)
		t.Run(filepath.ToSlash(rel), func(t *testing.T) {
			w := w.as(t)
			w.AssertNotDenied(w.Hook("Write", w.Root, configws.Obj{"file_path": path}))
		})
	}
}

func TestAWorktreeGetsTheLayersOfTheTreeItWasCutFrom(t *testing.T) {
	t.Parallel()
	// ワークスペースから切ったもの（w1）には自身のレイヤー、lib から切ったもの（i0007）には lib のレイヤー。
	// 記録の tree はワークツリーの名前。
	w := newWS(t, configws.KindBase)
	trees := map[string]string{"w1": w.Worktree(w.Root, "w1"), "i0007": w.Worktree(w.Lib, "i0007")}
	for _, c := range []struct {
		tree string
		rel  []string
		id   string
	}{
		{"w1", []string{"generated", "x.py"}, "self:generated"},
		{"i0007", []string{"schema", "x.sql"}, "lib:schema"},
		{"i0007", []string{".env"}, "credentials"},
	} {
		t.Run(c.tree+"/"+c.id, func(t *testing.T) {
			w := w.as(t)
			target := p(append([]string{trees[c.tree]}, c.rel...)...)
			w.AssertDenied(w.Hook("Write", w.Root, configws.Obj{"file_path": target}), c.id)
			if got := w.LastRecord().Str("tree"); got != c.tree {
				t.Errorf("tree = %q, want %q", got, c.tree)
			}
		})
	}
}

func TestProjectAllowStaysInsideTheProject(t *testing.T) {
	t.Parallel()
	w := newWS(t, configws.KindBase)
	w.AssertNotDenied(w.Hook("Write", w.Root, configws.Obj{"file_path": p(w.Lib, "src", "a.py")}))
	rec := w.LastRecord()
	if rec.Str("decision") != "allow" {
		t.Fatalf("decision = %q: %v", rec.Str("decision"), rec)
	}
	// 順は 共通レイヤー → 自身のレイヤー → プロジェクトのレイヤー。先にヒットするのは共通レイヤーの ws-src。
	// ここを「どちらでも良い」にすると、逆順に並べた実装でも通ってしまう。
	if ids := rec.Ids(); len(ids) == 0 || ids[0] != "ws-src" {
		t.Errorf("先にヒットするのは ws-src: %v", ids)
	}

	w.Hook("Write", w.Root, configws.Obj{"file_path": p(w.Root, "docs", "a.md")})
	rec = w.LastRecord()
	if rec.Str("decision") != "allow" || !slices.Equal(rec.Ids(), []string{"self:docs"}) {
		t.Errorf("自身のレイヤーの docs が allow: %v", rec)
	}
	// lib の docs/ に self:docs はヒットしない。どのルールも言及しない。
	w.Hook("Write", w.Root, configws.Obj{"file_path": p(w.Lib, "docs", "a.md")})
	if slices.Contains(w.LastRecord().Ids(), "self:docs") {
		t.Error("lib の docs/ に self:docs がヒットした")
	}
}

func TestNotebookEditGoesThroughTheSameUnion(t *testing.T) {
	t.Parallel()
	// NotebookEdit も書き込み系。共通レイヤー + 行き先のレイヤーの和で、欄は notebook_path。
	w := newWS(t, configws.KindBase)
	configws.WriteRules(t, w.Lib, configws.With(configws.LibRules, "deny", configws.Plus(configws.DenyOf(configws.LibRules), configws.NotebookRule)))

	denied := w.Hook("NotebookEdit", w.Root, configws.Obj{"notebook_path": p(w.Lib, "notebooks", "a.ipynb")})
	w.AssertDenied(denied, "lib:notebook")
	rec := w.LastRecord()
	if !slices.Equal(rec.Ids(), []string{"lib:notebook"}) || rec.Str("source") != "lib" {
		t.Errorf("record = %v", rec)
	}

	// 共通レイヤーの guard-approved も NotebookEdit を持つ。プロジェクトのツリーでもヒットする。
	common := w.Hook("NotebookEdit", w.Root, configws.Obj{"notebook_path": p(w.Lib, ".ccnavi", "approved", "a.ipynb")})
	w.AssertDenied(common, "guard-approved")
}

func TestAProjectAllowDoesNotLoosenTheCommonAsk(t *testing.T) {
	t.Parallel()
	// 共通レイヤーの ask を、プロジェクトのレイヤーの allow では緩められない。
	w := newWS(t, configws.KindBase)
	configws.Write(t, w.Rules, configws.Body(configws.With(configws.CommonRules, "ask", []configws.Obj{configws.AskVendor})))
	allow := configws.Plus(configws.AllowOf(configws.LibRules), configws.Rule("open-vendor", "Write|Edit", "glob", "*/vendor/*", ""))
	configws.WriteRules(t, w.Lib, configws.With(configws.LibRules, "allow", allow))

	r := w.Hook("Write", w.Root, configws.Obj{"file_path": p(w.Lib, "vendor", "x.py")})
	if w.Decision(r) != "ask" || !strings.Contains(w.Reason(r), "ask-vendor") {
		t.Fatalf("ask-vendor で ask になる: %s%s", r.Stdout, r.Stderr)
	}
	rec := w.LastRecord()
	if ids := rec.Ids(); len(ids) == 0 || ids[0] != "ask-vendor" || rec.Str("source") != "common" {
		t.Errorf("record = %v", rec)
	}
	// ワークスペースのツリーでも同じ。lib の allow はそこには足さない。
	here := w.Hook("Write", w.Root, configws.Obj{"file_path": p(w.Root, "vendor", "x.py")})
	if w.Decision(here) != "ask" {
		t.Errorf("ワークスペースのツリーでも ask: %s", here.Stdout)
	}
}

// パスを持つツールは行き先のレイヤー、持たないツールは全部のレイヤーの和。

var secretsRule = configws.Rule("secrets", "Read|Grep|Glob", "glob", "*/secrets*", "secrets は探さない。")

func TestGrepAndGlobUseTheLayerOfThePlaceTheySearch(t *testing.T) {
	t.Parallel()
	// Grep と Glob は Read と同じく行き先のレイヤー。探す場所を省けば cwd。
	w := newWS(t, configws.KindBase)
	configws.WriteRules(t, w.Lib, configws.With(configws.LibRules, "deny", configws.Plus(configws.DenyOf(configws.LibRules), secretsRule)))
	secrets := p(w.Lib, "secrets")

	w.AssertDenied(w.Hook("Read", w.Root, configws.Obj{"file_path": secrets}), "lib:secrets")
	w.AssertDenied(w.Hook("Grep", w.Root, configws.Obj{"pattern": "token", "path": secrets}), "lib:secrets")
	w.AssertDenied(w.Hook("Glob", w.Root, configws.Obj{"pattern": "*", "path": secrets}), "lib:secrets")
	// 探す場所を省くと cwd。
	w.AssertDenied(w.Hook("Grep", secrets, configws.Obj{"pattern": "token"}), "lib:secrets")
	w.AssertDenied(w.Hook("Glob", secrets, configws.Obj{"pattern": "*"}), "lib:secrets")
	// 行き先のレイヤーだけなので、app の secrets には lib のルールはヒットしない。
	w.AssertNotDenied(w.Hook("Grep", w.Root, configws.Obj{"pattern": "token", "path": p(w.App, "secrets")}))
}

func TestToolsWithoutAPathUseTheUnionOfEveryLayer(t *testing.T) {
	t.Parallel()
	// WebFetch・Skill・Agent・PowerShell は、Bash と同じく全部のレイヤーの和。cwd によらない。
	w := newWS(t, configws.KindBase)
	fetch := configws.Rule("fetch-internal", "WebFetch", "glob", "*internal.example.com*", "社内のページは取りに行かない。")
	configws.WriteRules(t, w.Root, configws.With(configws.OwnRules, "deny", configws.Plus(configws.DenyOf(configws.OwnRules), fetch)))
	deny := configws.Plus(configws.DenyOf(configws.LibRules),
		configws.Rule("release", "Skill", "glob", "release*", "ユーザが回す。"),
		configws.Rule("migrate", "Agent", "glob", "*migrate*", "ユーザが回す。"),
		configws.Rule("ps-psql", "PowerShell", "glob", "*psql*", "ユーザが回す。"),
	)
	configws.WriteRules(t, w.Lib, configws.With(configws.LibRules, "deny", deny))

	for _, cwd := range []string{w.Root, w.Lib, w.App} {
		t.Run(filepath.Base(cwd), func(t *testing.T) {
			w := w.as(t)
			w.AssertDenied(w.Hook("WebFetch", cwd, configws.Obj{"url": "https://internal.example.com/a", "prompt": "read"}), "self:fetch-internal")
			w.AssertDenied(w.Hook("Skill", cwd, configws.Obj{"skill": "release"}), "lib:release")
			w.AssertDenied(w.Hook("Agent", cwd, configws.Obj{"description": "migrate the db", "prompt": "go"}), "lib:migrate")
			w.AssertDenied(w.Hook("PowerShell", cwd, configws.Obj{"command": "psql"}), "lib:ps-psql")
		})
	}
}

func TestALayerAllowReachesGrepAndToolsWithoutAPath(t *testing.T) {
	t.Parallel()
	// レイヤーをツールの種類で選ぶ形の代償。パスを持つツールは行き先のレイヤー、持たないツールは全部のレイヤーの和を足す。
	w := newWS(t, configws.KindBase)
	allow := configws.Plus(configws.AllowOf(configws.LibRules),
		configws.Rule("grep-src", "Grep", "glob", "*/src*", ""),
		configws.Rule("fetch-docs", "WebFetch", "glob", "*docs.example.com*", ""),
	)
	configws.WriteRules(t, w.Lib, configws.With(configws.LibRules, "allow", allow))

	w.Hook("Grep", w.Root, configws.Obj{"pattern": "x", "path": p(w.Lib, "src")})
	rec := w.LastRecord()
	if rec.Str("decision") != "allow" || !slices.Equal(rec.Ids(), []string{"lib:grep-src"}) {
		t.Errorf("Grep: %v", rec)
	}
	// lib のレイヤーの allow は、app に居る WebFetch にもヒットする（パスを持たないツールは全部の和）。
	w.Hook("WebFetch", w.App, configws.Obj{"url": "https://docs.example.com/a", "prompt": "read"})
	rec = w.LastRecord()
	if rec.Str("decision") != "allow" || !slices.Equal(rec.Ids(), []string{"lib:fetch-docs"}) {
		t.Errorf("WebFetch: %v", rec)
	}
}

// `{root}` の置換先はどのレイヤーでもワークスペースルート。

func TestRootInAProjectLayerIsTheWorkspaceRoot(t *testing.T) {
	t.Parallel()
	w := newWS(t, configws.KindBase)
	deny := configws.Plus(configws.DenyOf(configws.LibRules),
		configws.Rule("root-vendor", "Write|Edit", "glob", "{root}/projects/lib/vendor/*", "vendor はワークスペースルートから数えたパスで止める。"))
	configws.WriteRules(t, w.Lib, configws.With(configws.LibRules, "deny", deny))

	w.AssertDenied(w.Hook("Write", w.Root, configws.Obj{"file_path": p(w.Lib, "vendor", "x.py")}), "lib:root-vendor")
	// `{root}` が lib の git プロジェクトルートに置き換わるなら、こちらがヒットするはず。
	w.AssertNotDenied(w.Hook("Write", w.Root, configws.Obj{"file_path": p(w.Lib, "projects", "lib", "vendor", "x.py")}))
}

func TestACopiedRootRuleIsDroppedAsADuplicate(t *testing.T) {
	t.Parallel()
	// 重複の判定は置き換えた後の欄で比べる。`{root}` ごとコピーした定義は破棄する。
	w := newWS(t, configws.KindBase)
	configws.Write(t, w.Rules, configws.Body(configws.With(configws.CommonRules, "deny", configws.Plus(configws.DenyOf(configws.CommonRules), configws.RootRule))))
	configws.WriteRules(t, w.Lib, configws.With(configws.LibRules, "deny", append([]configws.Obj{configws.RootRule}, configws.DenyOf(configws.LibRules)...)))

	w.AssertDenied(w.Hook("Write", w.Root, configws.Obj{"file_path": p(w.Lib, "secret.txt")}), "root-secret")
	rec := w.LastRecord()
	if !slices.Equal(rec.Ids(), []string{"root-secret"}) || rec.Str("source") != "common" {
		t.Errorf("record = %v", rec)
	}
	w.MustHaveDetail(w.Problems("info", configws.ProjectWhere("lib")), "root-secret")
}

// Bash は共通レイヤー + 自身のレイヤー + 全プロジェクトのレイヤー。

func TestBashIsTheUnionOfEveryLayerRegardlessOfCwd(t *testing.T) {
	t.Parallel()
	w := newWS(t, configws.KindBase)
	for _, cwd := range []string{w.Root, w.Lib, w.App} {
		t.Run(filepath.Base(cwd), func(t *testing.T) {
			w := w.as(t)
			w.AssertDenied(w.Hook("Bash", cwd, configws.Obj{"command": "terraform apply -auto-approve"}), "terraform")
			w.AssertDenied(w.Hook("Bash", cwd, configws.Obj{"command": "dropdb prod"}), "self:dropdb")
			w.AssertDenied(w.Hook("Bash", cwd, configws.Obj{"command": "psql -c 'select 1'"}), "lib:raw-psql")
		})
	}
}

func TestBashRecordNamesTheLayerOfTheDecidingRule(t *testing.T) {
	t.Parallel()
	// 記録の source は判定を下したルール（rules の先頭）のレイヤー。
	w := newWS(t, configws.KindBase)
	w.Hook("Bash", w.App, configws.Obj{"command": "dropdb prod"})
	rec := w.LastRecord()
	if ids := rec.Ids(); len(ids) == 0 || ids[0] != "self:dropdb" || rec.Str("source") != "self" {
		t.Errorf("record = %v", rec)
	}
	w.Hook("Bash", w.App, configws.Obj{"command": "psql"})
	if got := w.LastRecord().Str("source"); got != "lib" {
		t.Errorf("source = %q, want lib", got)
	}
}

func TestTwoNonEmptyProjectLayersTakePartInNameOrder(t *testing.T) {
	t.Parallel()
	// 非空のプロジェクトのレイヤーが 2 つでも両方が和に入り、順序は名前順。
	w := newWS(t, configws.KindBase)
	configws.WriteRules(t, w.App, configws.AppRules)
	configws.WriteRules(t, w.Lib, configws.With(configws.LibRules, "deny", configws.Plus(configws.DenyOf(configws.LibRules), configws.LibDeploy)))

	for _, cwd := range []string{w.Root, w.Lib, w.App} {
		t.Run(filepath.Base(cwd), func(t *testing.T) {
			w := w.as(t)
			w.AssertDenied(w.Hook("Bash", cwd, configws.Obj{"command": "kubectl get pods"}), "app:kube")
			w.AssertDenied(w.Hook("Bash", cwd, configws.Obj{"command": "psql -c 'select 1'"}), "lib:raw-psql")
		})
	}
	// 1 本の呼び出しに app と lib の両方がヒットする。先に載るのは名前順で前の app。
	w.AssertDenied(w.Hook("Bash", w.Root, configws.Obj{"command": "sh deploy.sh --now"}), "app:deploy", "lib:deploy-any")
	rec := w.LastRecord()
	if !slices.Equal(rec.Ids(), []string{"app:deploy", "lib:deploy-any"}) || rec.Str("source") != "app" {
		t.Errorf("record = %v", rec)
	}
}

// 同 id の扱い（重複は後ろを破棄する。同 id で中身が違うときは両方ヒットする）。

func TestIdenticalDefinitionInALaterLayerIsDropped(t *testing.T) {
	t.Parallel()
	// 裸の id と全欄が一致する定義は後ろのレイヤーを破棄する。記録に 1 本、--lint は info。
	w := newWS(t, configws.KindBase)
	configws.WriteRules(t, w.Lib, configws.With(configws.LibRules, "deny", append([]configws.Obj{configws.DenyOf(configws.CommonRules)[0]}, configws.DenyOf(configws.LibRules)...)))

	w.AssertDenied(w.Hook("Write", w.Root, configws.Obj{"file_path": p(w.Lib, ".env")}), "credentials")
	if got := w.HitRules(); !slices.Equal(got, []string{"credentials"}) {
		t.Errorf("hits = %v", got)
	}
	where := configws.ProjectWhere("lib")
	w.MustHaveDetail(w.Problems("info", where), "credentials")
	if configws.AnyDetail(w.Problems("warn", where), "credentials") {
		t.Error("重複を warn で言っている")
	}
}

func TestSameIdWithDifferentContentKeepsBoth(t *testing.T) {
	t.Parallel()
	// 同 id で中身が違う rules は両方ヒットする。記録に両方が並び、--lint は warn。
	w := newWS(t, configws.KindBase)
	differs := append([]configws.Obj{configws.With(configws.DenyOf(configws.CommonRules)[0], "glob", "*/secrets/*")}, configws.DenyOf(configws.LibRules)...)
	configws.WriteRules(t, w.Lib, configws.With(configws.LibRules, "deny", differs))

	w.AssertDenied(w.Hook("Write", w.Root, configws.Obj{"file_path": p(w.Lib, "secrets", ".env")}), "credentials", "lib:credentials")
	got := w.HitRules()
	slices.Sort(got)
	if !slices.Equal(got, []string{"credentials", "lib:credentials"}) {
		t.Errorf("hits = %v", got)
	}
	w.MustHaveDetail(w.Problems("warn", configws.ProjectWhere("lib")), "credentials")
}

func TestBashUnionDropsIdenticalDuplicatesToo(t *testing.T) {
	t.Parallel()
	// 共通レイヤーの `terraform` をコピーした lib の定義は 1 本にまとまる。
	w := newWS(t, configws.KindBase)
	configws.WriteRules(t, w.Lib, configws.With(configws.LibRules, "deny", configws.Plus(configws.DenyOf(configws.LibRules), configws.DenyOf(configws.CommonRules)[1])))

	w.AssertDenied(w.Hook("Bash", w.Lib, configws.Obj{"command": "terraform apply"}), "terraform")
	if ids := w.LastRecord().Ids(); !slices.Equal(ids, []string{"terraform"}) {
		t.Errorf("ids = %v", ids)
	}
}

// レイヤーが無い・不正・共通レイヤー自身が不正、の 3 つ。

func TestMissingLayerIsEmptyWithoutFallback(t *testing.T) {
	t.Parallel()
	// レイヤーのファイルが無い = 空。fallback は付かず、--lint も言わない。
	w := newWS(t, configws.KindBase)
	passed := w.Hook("Write", w.Root, configws.Obj{"file_path": p(w.App, "schema", "x.sql")})
	w.AssertNotDenied(passed)
	if _, has := w.LastRecord()["fallback"]; has {
		t.Error("fallback が付いた")
	}
	if strings.Contains(w.Reason(passed), "built-in defaults") {
		t.Error("組み込みの既定に戻った")
	}
	for _, pr := range w.LintProblems() {
		if strings.HasPrefix(pr.Where, "(projects/app)") {
			t.Errorf("無いレイヤーを --lint が言う: %+v", pr)
		}
	}
}

func TestABrokenLayerIsEmptyAndNamedInTheRecord(t *testing.T) {
	t.Parallel()
	// 不正なレイヤーは空 + 記録の fallback にレイヤーの名前。組み込みには戻らず、共通レイヤーの deny はヒットしたまま。
	// Bash の和からも外れ、記録がそれを言う。--lint は error。
	w := newWS(t, configws.KindBase)
	configws.Write(t, configws.LayerPath(w.Lib, "rules"), configws.Broken)

	passed := w.Hook("Write", w.Root, configws.Obj{"file_path": p(w.Lib, "schema", "x.sql")})
	w.AssertNotDenied(passed)
	rec := w.LastRecord()
	if rec.Str("fallback") != "lib" {
		t.Errorf("fallback = %q, want lib", rec.Str("fallback"))
	}
	if strings.Contains(w.Reason(passed), "built-in defaults") {
		t.Error("組み込みの既定に戻った")
	}

	w.AssertDenied(w.Hook("Write", w.Root, configws.Obj{"file_path": p(w.Lib, ".env")}), "credentials")
	if got := w.LastRecord().Str("fallback"); got != "lib" {
		t.Errorf("共通レイヤーの deny でも fallback は lib: %q", got)
	}

	w.AssertNotDenied(w.Hook("Bash", w.Lib, configws.Obj{"command": "psql"}))
	w.AssertDenied(w.Hook("Bash", w.Lib, configws.Obj{"command": "dropdb prod"}), "self:dropdb")
	if detail, _ := w.LastRecord()["detail"].(string); !strings.Contains(detail, "lib") {
		t.Errorf("記録の detail がレイヤーを言わない: %q", detail)
	}

	if !configws.AnyWhere(w.Problems("error", ""), "(projects/lib)") {
		t.Error("不正なレイヤーを --lint が error で言わない")
	}
}

func TestABrokenCommonLayerFallsBackToBuiltin(t *testing.T) {
	t.Parallel()
	// 共通レイヤー自身が読めないときは組み込みの既定。レイヤーは足さない。
	w := newWS(t, configws.KindBase)
	configws.Write(t, w.Rules, configws.Broken)

	passed := w.Hook("Write", w.Root, configws.Obj{"file_path": p(w.Lib, "schema", "x.sql")})
	w.AssertNotDenied(passed)
	if got := w.LastRecord().Str("fallback"); got != "builtin:rules" {
		t.Errorf("fallback = %q", got)
	}
	reason := w.Reason(passed)
	if !strings.Contains(reason, "built-in defaults") || strings.Contains(reason, "lib:schema") {
		t.Errorf("reason = %q", reason)
	}
}

// ルールの苦情は、1 回の起動で 1 度だけ出す。hook はツリーのレイヤーごとに共通レイヤーを読み直し、`--lint` はレイヤーのファイルを
// 2 つの経路で読む。読むたびに言うと、同じ苦情がレイヤーやツリーの数だけ並ぶ。

var silentRule = configws.Rule("silent", "Bash", "glob", "*nothing matches*", "")

func TestHooksSayACommonLayerProblemOnce(t *testing.T) {
	t.Parallel()
	w := newWS(t, configws.KindBase)
	configws.Write(t, w.Rules, configws.Body(configws.Ruleset([]configws.Obj{silentRule}, nil)))
	for _, event := range []string{"UserPromptSubmit", "Stop"} {
		t.Run(event, func(t *testing.T) {
			w := w.as(t)
			done := w.HookAs(event, "disable", "", w.Root, nil)
			if n := strings.Count(done.Stderr, "deny:silent"); n != 1 {
				t.Errorf("苦情が %d 回: %s", n, done.Stderr)
			}
		})
	}
}

func TestLintCountsALayerProblemOnce(t *testing.T) {
	t.Parallel()
	w := newWS(t, configws.KindBase)
	configws.WriteRules(t, w.Lib, configws.Ruleset([]configws.Obj{silentRule}, nil))

	seen := map[[2]string]int{}
	total := 0
	for _, pr := range w.LintProblems() {
		if strings.HasPrefix(pr.Where, configws.ProjectWhere("lib")) && strings.Contains(pr.Where, "silent") {
			seen[[2]string{pr.Where, pr.Detail}]++
			total++
		}
	}
	if total == 0 {
		t.Fatal("レイヤーの苦情が出ていない")
	}
	if total != len(seen) {
		t.Errorf("同じ苦情が重なっている: %v", seen)
	}
}

// 実行後チェックも「共通レイヤー + そのツリーのレイヤー」の和。行き先のレイヤー 1 本のままの実装では、共通レイヤーの deny の場所が
// 保護領域に数えられない。プロジェクトのツリー（共通レイヤー）と、ワークスペースのワークツリー（自身のレイヤー）の両方で見る。

func TestPostMonitoringIsTheUnionOfTheCommonAndTheTreeLayers(t *testing.T) {
	t.Parallel()
	for _, c := range []struct {
		name    string
		tree    func(w *ws) string
		dirty   map[string]string // ツリーからの相対パス -> 中身
		wants   []string          // 標準エラーに出るべき語
		recTree string            // 記録の tree（空なら見ない）
	}{
		{"project tree with the common layer", func(w *ws) string { return w.Lib },
			map[string]string{".env": "SECRET=1\n"}, []string{"POST_VIOLATION", "credentials", ".env"}, ""},
		{"project tree with its own layer", func(w *ws) string { return w.Lib },
			map[string]string{"schema/keep.sql": "-- dirty\n", ".env": "SECRET=1\n"},
			[]string{"projects/lib/.ccnavi/config/project/rules.yml:schema", "credentials", "tree: lib"}, ""},
		{"workspace worktree with the own layer", func(w *ws) string { return w.Worktree(w.Root, "w1") },
			map[string]string{"generated/keep.py": "dirty\n"},
			[]string{"POST_VIOLATION", ".ccnavi/config/project/rules.yml:generated", "generated/keep.py"}, "w1"},
		{"workspace worktree with the common layer", func(w *ws) string { return w.Worktree(w.Root, "w2") },
			map[string]string{".env": "SECRET=1\n"}, []string{"credentials"}, ""},
	} {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			w := newWS(t, configws.KindBase)
			tree := c.tree(w)
			// ターンを起こし、そのツリーの記録を取らせる。初回の実行後は記録するだけ。
			started := w.HookAs("UserPromptSubmit", "disable", "", w.Root, nil)
			w.MustOK(started, "UserPromptSubmit")
			w.MustOK(w.Post("Bash", tree, configws.Obj{"command": "python gen.py"}), "最初の PostToolUse")
			for rel, text := range c.dirty {
				configws.Write(t, p(tree, filepath.FromSlash(rel)), text)
			}
			after := w.Post("Bash", tree, configws.Obj{"command": "python gen.py"})
			if after.Code != 2 {
				t.Fatalf("code = %d, want 2\n%s%s", after.Code, after.Stdout, after.Stderr)
			}
			for _, want := range c.wants {
				if !strings.Contains(after.Stderr, want) {
					t.Errorf("標準エラーに %q が無い: %s", want, after.Stderr)
				}
			}
			if c.recTree != "" {
				if got := w.LastRecord().Str("tree"); got != c.recTree {
					t.Errorf("tree = %q, want %q", got, c.recTree)
				}
			}
		})
	}
}

// 置き場と環境変数。

func TestAProjectNamedSelfIsNotCounted(t *testing.T) {
	t.Parallel()
	// `projects/self/` は `self:id` と区別できないので数えず、--lint が error。
	w := newWS(t, configws.KindBase)
	w.Project("self", configws.Ruleset([]configws.Obj{configws.Rule("dump", "Bash", "glob", "*mysqldump*", "no.")}, nil), "", "")

	w.AssertNotDenied(w.Hook("Bash", w.Root, configws.Obj{"command": "mysqldump db"}))
	w.MustHaveDetail(w.Problems("error", configws.ProjectWhere("self")), "self")
}

func TestAWorkspaceWithoutProjectsOrOwnLayerIsUnchanged(t *testing.T) {
	t.Parallel()
	// `projects/` が無く自身のレイヤーも無ければ、共通レイヤーだけで判定し記録する。
	w := newWS(t, configws.KindBase)
	// 自身のレイヤーだけを消す。共通レイヤーも同じ ccnavi ディレクトリの下（`.ccnavi/config/common/`）にある。
	if err := os.RemoveAll(p(w.Root, configws.Home, "config", "project")); err != nil {
		t.Fatal(err)
	}
	// `projects/` を作らないワークスペースにする（置き場は固定で、数えない口は無い）。消さずに外へ動かす。
	// Windows は .git の中の読み取り専用のファイルを消せない。
	if err := os.Rename(w.Projects, p(w.Root, "moved-away")); err != nil {
		t.Fatal(err)
	}

	w.AssertNotDenied(w.Hook("Write", w.Root, configws.Obj{"file_path": p(w.Root, "src", "a.py")}))
	rec := w.LastRecord()
	if rec.Str("decision") != "allow" || !slices.Equal(rec.Ids(), []string{"ws-src"}) {
		t.Errorf("record = %v", rec)
	}
	for _, key := range []string{"fallback", "project"} {
		if _, has := rec[key]; has {
			t.Errorf("%s が付いた: %v", key, rec[key])
		}
	}

	w.AssertDenied(w.Hook("Write", w.Root, configws.Obj{"file_path": p(w.Root, ".env")}), "credentials")
	if got := w.HitRules(); !slices.Equal(got, []string{"credentials"}) {
		t.Errorf("hits = %v", got)
	}
	w.AssertNotDenied(w.Hook("Bash", w.Root, configws.Obj{"command": "psql"}))
}

// `--explain` はレイヤーごとに全件。

func TestExplainListsRulesPerLayerAndThePhaseAndRiskTables(t *testing.T) {
	t.Parallel()
	// rules を共通レイヤー・自身のレイヤー・各プロジェクトの順に、phases と risk の表を足す。
	w := newWS(t, configws.KindBase)
	r := w.Ccnavi("--explain")
	w.MustOK(r, "--explain")
	out := r.Stdout
	order := []string{"■ rules 共通レイヤー", "■ rules ワークスペースのレイヤー", "■ rules lib", "■ phases", "■ risk"}
	last := -1
	for _, heading := range order[:3] {
		at := strings.Index(out, heading)
		if at < 0 {
			t.Errorf("見出し %q が無い", heading)
		} else if at < last {
			t.Errorf("見出し %q の順が違う", heading)
		}
		last = at
	}
	for _, heading := range order[3:] {
		if !strings.Contains(out, heading) {
			t.Errorf("見出し %q が無い", heading)
		}
	}
	// phases と risk は id と出どころのレイヤー。
	for _, want := range []string{"self:generated", "lib:schema", "build", "big-diff", "schema"} {
		if !strings.Contains(out, want) {
			t.Errorf("--explain に %q が無い", want)
		}
	}
}

func TestExplainShowsABrokenLayerAsUnreadableAndEmpty(t *testing.T) {
	t.Parallel()
	w := newWS(t, configws.KindBase)
	configws.Write(t, configws.LayerPath(w.Lib, "rules"), configws.Broken)
	out := w.Ccnavi("--explain").Stdout
	if !strings.Contains(out, "■ rules lib") || !strings.Contains(out, "読めない") {
		t.Errorf("読めないレイヤーを言わない: %s", out)
	}
}

func TestExplainJSONCarriesEveryLayer(t *testing.T) {
	t.Parallel()
	// `--explain --json` にレイヤーごとの rules 全件と phases / risk の定義と出どころが出る。
	// 形は docs/11_requirements/json-shapes.md「ボードの JSON」にある。ここでは `layers` のリストに
	// name（common / self / プロジェクト名）と rules / phases / risk が在ることまでを固定する。
	w := newWS(t, configws.KindBase)
	r := w.Ccnavi("--explain", "--json")
	var body struct {
		Layers []struct {
			Name  string `json:"name"`
			Rules struct {
				Deny []struct {
					ID string `json:"id"`
				} `json:"deny"`
			} `json:"rules"`
			Phases []struct {
				ID string `json:"id"`
			} `json:"phases"`
			Risk struct {
				Factors []struct {
					ID string `json:"id"`
				} `json:"factors"`
			} `json:"risk"`
		} `json:"layers"`
	}
	if err := json.Unmarshal([]byte(r.Stdout), &body); err != nil {
		t.Fatalf("%v\n%s", err, r.Stdout)
	}
	type layer = struct {
		deny, phases, factors []string
	}
	layers := map[string]layer{}
	for _, l := range body.Layers {
		var x layer
		for _, d := range l.Rules.Deny {
			x.deny = append(x.deny, d.ID)
		}
		for _, d := range l.Phases {
			x.phases = append(x.phases, d.ID)
		}
		for _, d := range l.Risk.Factors {
			x.factors = append(x.factors, d.ID)
		}
		layers[l.Name] = x
	}
	if got := configws.SortedKeys(layers); !slices.Equal(got, []string{"app", "common", "lib", "self"}) {
		t.Fatalf("layers = %v", got)
	}
	checks := []struct {
		layer string
		got   []string
		want  string
	}{
		{"common", layers["common"].deny, "credentials"},
		{"self", layers["self"].deny, "self:generated"},
		{"lib", layers["lib"].deny, "lib:schema"},
		{"lib", layers["lib"].phases, "build"},
		{"common", layers["common"].factors, "big-diff"},
		{"lib", layers["lib"].factors, "schema"},
	}
	for _, c := range checks {
		if !slices.Contains(c.got, c.want) {
			t.Errorf("%s に %q が無い: %v", c.layer, c.want, c.got)
		}
	}
	if len(layers["app"].deny) != 0 {
		t.Errorf("app の deny は空のはず: %v", layers["app"].deny)
	}
	// この土台の共通レイヤーは phases.yml を持たないので、共通レイヤーの定義は空。
	if len(layers["common"].phases) != 0 {
		t.Errorf("common の phases は空のはず: %v", layers["common"].phases)
	}
}
