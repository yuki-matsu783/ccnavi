package union

import (
	"ccnavi/internal/harness/configws"
	"slices"
	"strings"
	"testing"
)

// `.ccnavi/` の組み込み deny とレイヤーの名前を、表記を変えて回避できないことの受入テスト
// （tests/config/test_config_union_holes.py の写し）。
//
// どれも、直さないと組み込み deny が「守っている」と言いながら回避できる形。`.claude/` の側も同じ適用の仕方で守られる。
//   - 大文字小文字: glob と regex で書いたルールと組み込みの保護を、どの機械でも区別せずに適用する。区別が要る regex は `(?-i:...)` で囲む
//   - 区切りが続かないパス: `rm -rf .ccnavi` / `mv .ccnavi .ccnavi.bak` / `rm -rf .claude`
//   - 生の id のコロン: レイヤーの名前をつけた形と見分けが付かないものを error にする
//   - `self` の予約: `projects/Self/` も `self` と同じに扱って数えない
//   - 確認だけ: 既に参照されている `.ccnavi/scripts/` のスクリプトを、表記を変えた形でも区切りの無い形でも、Write / Edit とシェルの両方で書き換えられない
//
// 組み込みの保護の照合が長い入力ですぐ返ることは、Go のテスト（go/internal/policy/selfguard の TestShellRuleDoesNotBlowUp）が確かめる。

const (
	layerCommon = "common"
	layerSelf   = "self"
)

// 予約してある名前（プロジェクトはこの名前を使えない）。
var reservedLayerNames = []string{layerCommon, layerSelf, "builtin"}

// 大文字小文字の的。glob と regex と、区別を取り戻した regex を 1 本ずつ持つ。
// glob も regex も組み込みの保護も、どの機械でも大文字小文字を区別せずにヒットする。機械で変えると、同じルールが Windows では
// ヒットし Linux ではヒットせず、区別しないチケットの範囲とも食い違う。区別が要るときだけ、書いたユーザが `(?-i:...)` で囲む。
var caseRules = configws.Ruleset(
	[]configws.Obj{
		configws.Rule("glob-secret", "Write|Edit", "glob", "*/secret/*", "secret はユーザが置く。"),
		configws.Rule("regex-token", "Write|Edit", "regex", `[\\/]token[\\/]`, "token はユーザが置く。"),
		configws.Rule("regex-exact", "Write|Edit", "regex", `[\\/](?-i:strict)[\\/]`, "strict は書いた表記のとおりに適用する。"),
	},
	[]configws.Obj{configws.Rule("anything-read", "Read", "regex", ".", "")},
)

// 生の id のコロンの的。裸の id にコロンがある。共通レイヤーに置けば lib の定義に見える。
var colonRules = configws.Ruleset(
	[]configws.Obj{configws.Rule("lib:custom", "Write|Edit", "glob", "*/custom/*", "custom はユーザが置く。")},
	[]configws.Obj{configws.Rule("anything-read", "Read", "regex", ".", "")},
)

const colonPhases = `version: 1
phases:
  "lib:build":
    kind: work
    title: ビルド（コロン）
    review: false
    scope: ["src/*"]
`

const colonRisk = `version: 1
factors:
  - {id: "lib:schema", points: 5, glob: "schema/**", message: スキーマに触った}
`

// `self` の予約の的。`projects/Self/` のレイヤーに置く deny。数えないので、ヒットしてはいけない。
var selfProjectRules = configws.Ruleset([]configws.Obj{configws.Rule("kube", "Bash", "glob", "*kubectl*", "kubectl はユーザが打つ。")}, nil)

// 予約名のプロジェクトへの Write の的。ワークスペース自身のレイヤーに置く、広い allow と 1 本の deny。
// 予約名のプロジェクトのレイヤーを名前で引くとこのレイヤーにヒットするので、そのプロジェクトへの Write がここの allow で通り、
// ここの deny で止まる。どちらも起きてはいけない。
var wideOwnRules = configws.Ruleset(
	[]configws.Obj{configws.Rule("generated", "Write|Edit", "glob", "*/generated/*", "generated files are rebuilt, not edited.")},
	[]configws.Obj{configws.Rule("wide", "Write|Edit", "glob", "*", "")},
)

// 予約名のプロジェクトのレイヤーに置く deny。レイヤーとして数えないのでヒットしない。
// ヒットしないことは「緩い」のではない。共通レイヤーだけで判定するので、allow も無い。
var reservedProjectRules = configws.Ruleset([]configws.Obj{configws.Rule("secret", "Write|Edit", "glob", "*/secret/*", "secret はユーザが置く。")}, nil)

// 予約名のプロジェクトのレイヤーの phases / risk。中身は何でもよく、バックアップと復元の対象に入るかだけを見る。
const reservedProjectPhases = `version: 1
phases:
  reserved:
    kind: work
    title: 予約名のレイヤー
    review: false
    scope: ["src/*"]
`

const reservedProjectRisk = `version: 1
factors:
  - {id: reserved, points: 5, glob: "src/**", message: 予約名のレイヤーで数えた}
`

// 参照されているスクリプトの的。レイヤーの risk が呼ぶスクリプト。これが書き換わると配点が 0 を返せる。
const (
	countScript = "printf '{\"points\": 30, \"message\": \"%s\"}' \"$CCNAVI_TICKET\"\n"
	countRisk   = "version: 1\nfactors:\n  - {id: counted, points: 5, script: .ccnavi/scripts/count.sh, message: 数えた}\n"
)

// 大文字小文字: ルールに書いたパスの扱い。glob も regex も、tickets の範囲と同じく、どの機械でも大文字小文字を区別せずにヒットする。
// 区別すると、`.ccnavi/` を `.Ccnavi/` のパスで作って deny に止められずに通れる。区別が要るときは `(?-i:...)` で囲む。

func TestRulesIgnoreCaseOnEveryMachine(t *testing.T) {
	t.Parallel()
	for _, c := range []struct{ name, id, dir, swapped string }{
		{"glob", "glob-secret", "secret", "SECRET"},
		{"regex", "regex-token", "token", "TOKEN"},
	} {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			w := newWS(t, configws.KindBase)
			configws.Write(t, w.Rules, configws.Body(caseRules))
			// 実体があると、区別しない機械では realpath がパスをディスクの側へ補正してしまい、この問い自体が消える
			// （フラグ無しでもヒットする）。
			if configws.Exists(p(w.Root, c.dir)) {
				t.Fatalf("%s が在る", c.dir)
			}
			w.AssertDenied(w.Hook("Write", w.Root, configws.Obj{"file_path": p(w.Root, c.dir, "x.txt")}), c.id)
			// 区別する機械では別のディレクトリだが、それでも適用する。同じルールが機械によってヒットしたりしなかったりしないことを
			// 優先する。regex が区別すると、Write の経路だけが表記違いで外れ、シェルから書く形は組み込みの保護が止めるので経路で
			// 答えが分かれる。
			w.AssertDenied(w.Hook("Write", w.Root, configws.Obj{"file_path": p(w.Root, c.swapped, "x.txt")}), c.id)
		})
	}
}

func TestARegexCanKeepTheDistinctionWithAnInlineFlag(t *testing.T) {
	t.Parallel()
	// 区別が要る regex は `(?-i:...)` で囲めば書いたとおりにヒットする。
	w := newWS(t, configws.KindBase)
	configws.Write(t, w.Rules, configws.Body(caseRules))
	w.AssertDenied(w.Hook("Write", w.Root, configws.Obj{"file_path": p(w.Root, "strict", "x.txt")}), "regex-exact")
	w.AssertNotDenied(w.Hook("Write", w.Root, configws.Obj{"file_path": p(w.Root, "STRICT", "x.txt")}))
}

func TestTheBuiltinProjectHomeDenyMatchesASwappedSpelling(t *testing.T) {
	t.Parallel()
	// 組み込みの `*/.ccnavi/*` も、どの機械でも大文字小文字を区別せずにヒットする（穴そのもの）。
	// 的は app のレイヤー。雛形の app は `.ccnavi/` を持たないので、`.Ccnavi/` はディスクに無く、realpath がパスを補正しない。
	// 「まだ無いところを表記違いで作る」という、ちょうど回避が成り立つ形になる。
	w := newWS(t, configws.KindGuard)
	if configws.Exists(p(w.App, configws.Home)) {
		t.Fatal("app が .ccnavi を持っている")
	}
	w.AssertDenied(w.GuardedHook("Write", w.Root, configws.Obj{"file_path": configws.LayerPath(w.App, "rules")}), "builtin:guard-project-home")
	// 区別する機械でも止める。組み込みの保護だけが機械でヒットの仕方を変えると、どの機械でも区別しないルールの glob や
	// チケットの範囲と食い違う。
	swapped := p(w.App, ".Ccnavi", "config", "project", "rules.yml")
	w.AssertDenied(w.GuardedHook("Write", w.Root, configws.Obj{"file_path": swapped}), "builtin:guard-project-home")
}

func TestTheClaudeSideIsClosedTheSameWay(t *testing.T) {
	t.Parallel()
	// 区切りが続かないパス。ディレクトリごと消す・退かす形。`.claude` は中身のあるディレクトリだが、丸ごと消す経路は止める。
	w := newWS(t, configws.KindGuard)
	for _, command := range []string{"rm -rf .claude", "mv .claude .claude.bak", "cp /tmp/x .claude"} {
		t.Run(command, func(t *testing.T) {
			w := w.as(t)
			w.AssertDenied(w.GuardedHook("Bash", w.Root, configws.Obj{"command": command}), configws.SettingFilesGuard)
		})
	}
}

func TestCopyDestinationOnlyIsGuarded(t *testing.T) {
	t.Parallel()
	// cp / ln / install は行き先だけで適用する。行き先は `-t <dir>` / `--target-directory` の値か、選択肢でない最後の引数。
	// 元の側に出ただけの守る場所は読まれるだけなので止めず、行き先になる形は書き方を変えても止める。
	// 形ごとの表は Go の go/internal/hook/judge/shell_guard_test.go が持つ。ここは hook を通した 1 本だけを残す。
	w := newWS(t, configws.KindGuard)
	read := w.GuardedHook("Bash", w.Root, configws.Obj{"command": "cp .ccnavi/config/common/rules.yml /tmp/x"})
	if strings.Contains(read.Stdout+read.Stderr, configws.SettingFilesGuard) {
		t.Error("読むだけのコピーが止まった")
	}
	w.AssertAllGuarded([]string{
		"cp /tmp/x .ccnavi/config/common/rules.yml",
		"cp -t .ccnavi/config/common /tmp/x",
		"cp --target-directory=.ccnavi/config/common /tmp/x",
	})
}

// 生の id のコロン。レイヤーの名前をつけた形（`lib:custom`）と見分けが付かない。

func TestAColonInAnIdIsNamedByLint(t *testing.T) {
	t.Parallel()
	// 共通レイヤーでもレイヤーでも、フェーズ定義でも risk の項目でも、コロンを含む id は error で名指しする。
	for _, c := range []struct {
		name  string
		setup func(t testing.TB, w *ws)
		layer string
		where string // where に出るべき語（空なら見ない）
	}{
		{"common rules", func(t testing.TB, w *ws) { configws.Write(t, w.Rules, configws.Body(colonRules)) }, "", "lib:custom"},
		{"project rules", func(t testing.TB, w *ws) { configws.WriteRules(t, w.Lib, colonRules) }, "lib", "lib:custom"},
		{"phase type", func(t testing.TB, w *ws) { configws.WritePhases(t, w.Lib, colonPhases) }, "lib", "lib:build"},
		{"risk factor", func(t testing.TB, w *ws) { configws.WriteRisk(t, w.Lib, colonRisk) }, "lib", ""},
	} {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			w := newWS(t, configws.KindBase)
			c.setup(t, w)
			where := ""
			if c.layer != "" {
				where = configws.ProjectWhere(c.layer)
			}
			errs := w.Problems("error", where)
			if c.where != "" && !configws.AnyWhere(errs, c.where) {
				t.Errorf("where に %q を含む error が無い: %+v", c.where, errs)
			}
			w.MustHaveDetail(errs, "`:`")
		})
	}
}

func TestARuleWithAColonInItsIdDoesNotJudge(t *testing.T) {
	t.Parallel()
	// そのルールを除いて名指しする側を採る。何も言わずに適用すると、どのファイルを直すのか決まらない。
	// 1 件の不備でガード全体は止めない（rules.load）ので、他のルールは有効なまま。代償は、その 1 本がヒットしなくなること。
	// `--lint` が error で言うことでそれを補う。
	w := newWS(t, configws.KindBase)
	configws.Write(t, w.Rules, configws.Body(colonRules))
	w.AssertNotDenied(w.Hook("Write", w.Root, configws.Obj{"file_path": p(w.Root, "custom", "x.txt")}))
}

// `self` は予約。`projects/Self/` のような表記違いも同じに扱う。

func TestReservedSelfIsNamedByLintAndNotCounted(t *testing.T) {
	t.Parallel()
	w := newWS(t, configws.KindBase)
	w.Project("Self", selfProjectRules, "", "")

	// `projects/Self/` も error で名指しする。
	w.MustHaveDetail(w.Problems("error", configws.ProjectWhere("Self")), "self")
	// 数えないので、そのレイヤーの deny は Bash の和に入らない。
	w.AssertNotDenied(w.Hook("Bash", w.Root, configws.Obj{"command": "kubectl get pods"}))
	// 普通の名前のレイヤーは和に入る。「そもそも和が使われていない」ではないことを見る。
	w.AssertDenied(w.Hook("Bash", w.Root, configws.Obj{"command": "psql -c 'select 1'"}), "lib:raw-psql")
}

// 予約名のプロジェクトへの Write が、ワークスペース自身のレイヤーで判定される穴。
//
// `layers` は `projects/self/` を数えないのに、`layer_for` は `target.project or LAYER_SELF` をレイヤーの名前で引いていた。
// `target.project` が `"self"` ならレイヤーの名前と一致するので、ワークスペース自身のレイヤーが返る。そのプロジェクトへの
// Write / Edit が、プロジェクト自身の deny を一度も読まずに、ワークスペースのレイヤーのルールで判定される。
//
// 穴が再現する形に組む。ワークスペース自身のレイヤーに広い allow（`self:wide`）と deny（`self:generated`）を置き、
// プロジェクトのレイヤーに deny（`secret`）を置く。レイヤーの名前で引くと `self:wide` が採られて通り、`self:generated` で止まる。
// 予約名のプロジェクトはレイヤー無しなので共通レイヤーだけで判定し、どちらも記録に現れない。

func reservedWS(t testing.TB) *ws {
	w := newWS(t, configws.KindBase)
	configws.WriteRules(t, w.Root, wideOwnRules)
	return w
}

func TestAWriteIntoAReservedProjectIsNotJudgedByTheWorkspaceLayer(t *testing.T) {
	t.Parallel()
	for _, name := range []string{layerSelf, "Self", layerCommon} {
		t.Run(name, func(t *testing.T) {
			t.Parallel()
			w := reservedWS(t)
			project := w.Project(name, reservedProjectRules, "", "")
			// 前提の確認。広い allow は実在して、ワークスペースのツリーにはヒットする。これが無いと、あとの「ヒットしない」が
			// 「そもそもルールが有効になっていない」と区別できない。
			w.AssertNotDenied(w.Hook("Write", w.Root, configws.Obj{"file_path": p(w.Root, "secret", "x.txt")}))
			rec := w.LastRecord()
			if rec.Str("decision") != "allow" || !slices.Contains(rec.Ids(), "self:wide") {
				t.Fatalf("広い allow が生きていない: %v", rec)
			}

			result := w.Hook("Write", w.Root, configws.Obj{"file_path": p(project, "secret", "x.txt")})
			rec = w.LastRecord()
			// 穴の本体。レイヤーの名前で引くと、ここに `self:wide` が入り、decision が allow になる。
			if slices.Contains(rec.Ids(), "self:wide") || rec.Str("decision") == "allow" {
				t.Errorf("ワークスペースのレイヤーで判定された: %v", rec)
			}
			// プロジェクトのレイヤーも足さない（レイヤー無し）。共通レイヤーに `*/secret/*` は無いので deny でもない。
			w.AssertNotDenied(result)
			if slices.Contains(rec.Names(), name+":secret") {
				t.Errorf("予約名のレイヤーの deny がヒットした: %v", rec)
			}
		})
	}
}

func TestTheWorkspaceLayerDenyDoesNotReachAReservedProject(t *testing.T) {
	t.Parallel()
	// レイヤーを借りないので、ワークスペースのレイヤーの deny もヒットしない。
	w := reservedWS(t)
	project := w.Project(layerSelf, reservedProjectRules, "", "")
	// 前提。同じパスはワークスペースのツリーでは止まる。
	w.AssertDenied(w.Hook("Write", w.Root, configws.Obj{"file_path": p(w.Root, "generated", "x.py")}), "self:generated")
	// レイヤーの名前で引くと、ここも `self:generated` で止まる。レイヤー無しなら共通レイヤーだけ。
	w.AssertNotDenied(w.Hook("Write", w.Root, configws.Obj{"file_path": p(project, "generated", "x.py")}))
	if slices.Contains(w.LastRecord().Ids(), "self:generated") {
		t.Error("self:generated がヒットした")
	}
}

func TestAProjectWhoseNameIsNotReservedStillWorks(t *testing.T) {
	t.Parallel()
	// 対照: 予約名でない `lib` は今までどおり自分のレイヤーで判定される。
	w := reservedWS(t)
	w.AssertDenied(w.Hook("Write", w.Root, configws.Obj{"file_path": p(w.Lib, "schema", "x.sql")}), "lib:schema")
	// 広い allow は行き先の 1 レイヤーにしか足さないので、lib にはヒットしない。
	if slices.Contains(w.LastRecord().Ids(), "self:wide") {
		t.Error("lib に self:wide がヒットした")
	}
	w.AssertDenied(w.Hook("Bash", w.Root, configws.Obj{"command": "psql -c 'select 1'"}), "lib:raw-psql")
}

func TestLintNamesEveryReservedName(t *testing.T) {
	t.Parallel()
	// `projects/common/`・`projects/self/`・`projects/builtin/` を error で名指しする。
	// `builtin` は組み込みの id（`builtin:<名前>`）と取り違えないよう拒否する。
	w := newWS(t, configws.KindBase)
	for _, name := range reservedLayerNames {
		w.Project(name, reservedProjectRules, "", "")
	}
	for _, name := range reservedLayerNames {
		t.Run(name, func(t *testing.T) {
			w := w.as(t)
			errs := w.Problems("error", configws.ProjectWhere(name))
			w.MustHaveDetail(errs, "予約")
			// 文面から「どちらの表記が予約か」が読めること。
			named := slices.ContainsFunc(errs, func(pr configws.Problem) bool {
				return strings.Contains(pr.Detail, "`"+layerCommon+"`") && strings.Contains(pr.Detail, "`"+layerSelf+"`")
			})
			if !named {
				t.Errorf("common と self の両方を言う文が無い: %+v", errs)
			}
		})
	}
}

func TestATicketCannotNameAReservedLayerName(t *testing.T) {
	t.Parallel()
	// `project: self` / `project: common` のチケットは `--agree` で通らない。
	w := newWS(t, configws.KindBase)
	for _, name := range reservedLayerNames {
		w.Project(name, reservedProjectRules, "", "")
	}
	// 前提。同じ本文で `project: lib` なら通る。止まる理由が予約名であることを固定する。
	w.Propose("i0001", configws.TicketText("i0001", configws.TicketOpts{Project: "lib", Plan: []string{"design"}, Allow: []string{"src/*"}}), "lib")
	w.mustApprove()

	for i, name := range reservedLayerNames {
		number := "i000" + configws.Itoa(i+2)
		t.Run(name, func(t *testing.T) {
			w := w.as(t)
			proposal := w.Propose(number, configws.TicketText(number, configws.TicketOpts{Project: name, Plan: []string{"design"}, Allow: []string{"src/*"}}), name)
			refused := w.Approve()
			if refused.Code == 0 || !strings.Contains(refused.Stderr, "予約") {
				t.Errorf("予約名のチケットが通った: code %d\n%s%s", refused.Code, refused.Stdout, refused.Stderr)
			}
			if configws.Exists(w.ApprovedCopy(number)) {
				t.Error("拒んだのに承認済みチケットが置かれた")
			}
			// 次の 1 件に影響しないように片付ける。
			if err := configws.RemoveFile(proposal); err != nil {
				t.Fatal(err)
			}
		})
	}
}

// `projects/common/` のレイヤーの 3 本がバックアップと復元の対象から外れる穴。
//
// `_layer_key` が `layer == LAYER_COMMON` の文字列比較で key を決めていた。`projects/common/` があると、そのレイヤーの key が
// `rules` / `phases` / `risk` になって共通レイヤーの key と完全に一致し、`_places` の重複の排除で先に積んだ共通レイヤーだけが
// 残る。プロジェクトが名前を 1 つ選ぶだけで、その 3 本が守られなくなる。
// key が共通レイヤーと重なると、この 2 つが失敗する（戻らず不正な中身が残る）。

func TestTheLayersOfReservedNamesAreRestoredWithTheCommonLayer(t *testing.T) {
	t.Parallel()
	// `projects/common/` の 3 本・`projects/self/` の 2 本・共通レイヤーの phases / risk・ワークスペース自身の risk が、
	// 同じ 1 回のバックアップと復元で全部戻る（key が衝突しない）。`projects/self/` は、ワークスペース自身のレイヤーとは別に守る。
	w := newWS(t, configws.KindGuard)
	reserved := w.Project(layerCommon, reservedProjectRules, reservedProjectPhases, reservedProjectRisk)
	project := w.Project(layerSelf, reservedProjectRules, reservedProjectPhases, "")
	configws.Write(t, configws.LayerPath(w.Root, "risk"), configws.CommonRiskText)
	var paths []string
	for _, kind := range []string{"rules", "phases", "risk"} {
		paths = append(paths, configws.LayerPath(reserved, kind))
	}
	paths = append(paths, configws.LayerPath(project, "rules"), configws.LayerPath(project, "phases"), configws.LayerPath(w.Root, "risk"), w.Phases, w.Risk)
	w.BreakAllAndRestore(paths)
}

func TestTheRestoreAsksTheProjectGit(t *testing.T) {
	t.Parallel()
	// 戻す先を聞く相手はそのプロジェクトの git。バックアップが無くても戻る。
	w := newWS(t, configws.KindGuard)
	reserved := w.Project(layerCommon, reservedProjectRules, reservedProjectPhases, reservedProjectRisk)
	path := configws.LayerPath(reserved, "rules")
	expected := configws.Read(t, path)
	if err := configws.RemoveFile(path); err != nil {
		t.Fatal(err)
	}
	result := w.RunHook("PreToolUse")
	if !configws.Exists(path) || configws.Read(t, path) != expected {
		t.Errorf("git から戻らない\n%s", configws.Said(result, ""))
	}
}

// 既に参照されている `.ccnavi/scripts/` のスクリプトを書き換える・消す経路。
// コア（バックアップと復元）には入れない。止まることだけを確かめる（大文字小文字と、区切りが続かないパスを直した結果）。

func scriptTamperWS(t testing.TB) (w *ws, script string) {
	w = newWS(t, configws.KindGuard)
	script = configws.Write(t, p(w.Lib, configws.Home, "scripts", "count.sh"), countScript)
	configws.WriteRisk(t, w.Lib, countRisk)
	configws.Git(t, w.Lib, "add", "-A")
	configws.Git(t, w.Lib, "commit", "--quiet", "-m", "script")
	return w, script
}

func TestNamedToolsCannotRewriteAReferencedScript(t *testing.T) {
	t.Parallel()
	w, script := scriptTamperWS(t)
	// 前提の確認。この `script:` は `--lint` に報告されない（参照が生きている）。
	for _, pr := range w.Problems("error", configws.ProjectWhere("lib")) {
		if strings.Contains(pr.Detail, "count.sh") {
			t.Fatalf("参照が生きていない: %+v", pr)
		}
	}
	// Write / Edit は、正しいパスでも表記を変えた形でも止まる。
	for _, tool := range []string{"Write", "Edit"} {
		w.AssertDenied(w.GuardedHook(tool, w.Root, configws.Obj{"file_path": script}), "builtin:guard-project-home")
	}
	// どの機械でも止める。区別しない機械では、このパスで書けば実際のファイルが書き換わる。区別する機械では別のファイルだが、
	// 組み込みの保護のヒットの仕方を機械で変えない。補正が無い形（ccnavi ディレクトリがまだ無いところを表記違いで作る）は
	// TestTheBuiltinProjectHomeDenyMatchesASwappedSpelling。
	swapped := p(w.Lib, ".Ccnavi", "scripts", "count.sh")
	w.AssertDenied(w.GuardedHook("Write", w.Root, configws.Obj{"file_path": swapped}), "builtin:guard-project-home")
}

func TestTheShellCannotRewriteOrDeleteAReferencedScript(t *testing.T) {
	t.Parallel()
	// シェルも同じ。ccnavi ディレクトリごと消す形も、表記を変えた形も止まる。
	w, _ := scriptTamperWS(t)
	for _, command := range []string{
		"rm -rf projects/lib/.ccnavi/scripts/count.sh",
		"echo x > projects/lib/.ccnavi/scripts/count.sh",
		"rm -rf projects/lib/.ccnavi",
		"mv projects/lib/.ccnavi /tmp/keep",
		// シェルの側も、場所のパスはどの機械でも区別せずに適用する。
		"rm -rf projects/lib/.Ccnavi/scripts/count.sh",
	} {
		t.Run(command, func(t *testing.T) {
			w := w.as(t)
			w.AssertDenied(w.GuardedHook("Bash", w.Root, configws.Obj{"command": command}), configws.SettingFilesGuard)
		})
	}
}
