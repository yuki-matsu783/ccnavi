package layers

import (
	"encoding/json"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/configws"
	"ccnavi/internal/harness/run"
)

// 共通レイヤーの置き場は `.ccnavi/config/common/` 固定の受入テスト。
//
// 3 つのレイヤーのうち共通レイヤーだけが env（`CCNAVI_RULES` / `CCNAVI_PHASES` / `CCNAVI_RISK`）で動き、自身のレイヤーとプロジェクトの
// レイヤーは `<ccnavi ディレクトリ>/config/project/` 固定、という非対称を無くす。env を渡しても共通レイヤーは既定の置き場のままになる。
//
// フラグ（`--rules` / `--phases` / `--risk`）は残す。ここを一緒に消すと、レイヤーの合成を確かめるテストが自分の一時ディレクトリを指せなくなる
// （configws）。hook は引数を渡さずに起動するので、hook からの判定の入口は固定される。フラグが有効なのは診断の経路
// （`--lint` / `--test` / `--test-expectations` / `--explain`）だけで、hook からの判定と `ticket` / `review` のサブコマンドでは無視して、標準エラーに言う（issue #65）。
//
// 起動は configws.WS.Ccnavi を使わない。あちらはフラグを渡さずに既定の置き場から読ませるので、フラグを足した形を見られない。
// ここは flags で足せる形にしてある。ここが失敗したら、env を読む経路が戻ったということ。

var (
	// 共通レイヤーに置いてあるものとは別の中身。env かフラグがこちらを指したときだけヒットする。
	otherRules  = configws.Obj{"version": 1, "deny": []configws.Obj{{"id": "only-in-the-other-file", "match": "Read", "glob": "*/src/*", "message": "this file is not the common layer."}}}
	otherPhases = "version: 1\nphases:\n  only-here:\n    kind: work\n    title: よそ\n"
	otherRisk   = "version: 1\nfactors:\n  - id: only-here\n    lines_over: 1\n    points: 99\n"
)

// layerKind は共通レイヤーの 3 本。field は `--explain --json` のレイヤーの欄。
type layerKind struct{ name, env, flag, field string }

var layerKinds = []layerKind{
	{"rules", "CCNAVI_RULES", "--rules", "rules"},
	{"phases", "CCNAVI_PHASES", "--phases", "phases_file"},
	{"risk", "CCNAVI_RISK", "--risk", "risk"},
}

// placeWS は共通レイヤーの 3 本をフラグで渡さずに動かす道具。
type placeWS struct {
	*configws.WS
	// other はその種類のよその 1 本、own は既定の置き場。
	other, own map[string]string
}

func newPlaceWS(t *testing.T) *placeWS {
	t.Helper()
	w := &placeWS{WS: configws.NewWS(t, configws.KindBase)}
	elsewhere := filepath.Join(w.Root, "elsewhere")
	w.other = map[string]string{
		"rules":  configws.Write(t, filepath.Join(elsewhere, "rules.yml"), configws.Body(otherRules)),
		"phases": configws.Write(t, filepath.Join(elsewhere, "phases.yml"), otherPhases),
		"risk":   configws.Write(t, filepath.Join(elsewhere, "risks.yml"), otherRisk),
	}
	w.own = map[string]string{"rules": w.Rules, "phases": w.Phases, "risk": w.Risk}
	return w
}

// bare は共通レイヤーの 3 本をフラグで渡さずに 1 回動かす。flags を渡したときだけ、そのフラグを足す。
func (w *placeWS) bare(env map[string]string, guard string, flags []string, stdin string, args ...string) run.Result {
	w.T.Helper()
	prefix := []string{"--root", w.Root, "--guard-core-files", guard, "--restore-if-deny", "disable", "--guard-ticket-approval", "disable"}
	all := append(append(prefix, flags...), args...)
	return run.Ccnavi(w.T, all, run.Opts{Input: stdin, Dir: base.Root(), Env: env})
}

// decide は hook の payload を 1 件渡して判定させる。フラグは既定で 3 本とも渡さない。
func (w *placeWS) decide(tool string, env map[string]string, guard string, flags []string, toolInput configws.Obj) run.Result {
	w.T.Helper()
	payload := configws.Body(configws.Obj{"hook_event_name": "PreToolUse", "tool_name": tool, "cwd": w.Root, "session_id": "s1", "tool_input": toolInput})
	return w.bare(env, guard, flags, payload, "--mode", "enable")
}

func (w *placeWS) target() string { return filepath.Join(w.Root, "src", "a.py") }

// common は `--explain --json` の共通レイヤーの 1 件。
func (w *placeWS) common(env map[string]string, flags []string) map[string]any {
	w.T.Helper()
	r := w.bare(env, "disable", flags, "", "--explain", "--json", "Read", w.target())
	var out struct {
		Layers []map[string]any `json:"layers"`
	}
	if err := json.Unmarshal([]byte(r.Stdout), &out); err != nil {
		w.T.Fatalf("--explain --json が読めない: %v\n%s\n%s", err, r.Stdout, r.Stderr)
	}
	var found []map[string]any
	for _, l := range out.Layers {
		if l["name"] == "common" {
			found = append(found, l)
		}
	}
	if len(found) != 1 {
		w.T.Fatalf("共通レイヤーは 1 件のはず: %v", out.Layers)
	}
	return found[0]
}

func pathOf(layer map[string]any, field string) string {
	m, _ := layer[field].(map[string]any)
	s, _ := m["path"].(string)
	return s
}

func (w *placeWS) reason(r run.Result) string {
	if strings.TrimSpace(r.Stdout) == "" {
		return ""
	}
	var out struct {
		HookSpecificOutput struct {
			Reason string `json:"permissionDecisionReason"`
		} `json:"hookSpecificOutput"`
	}
	if err := json.Unmarshal([]byte(r.Stdout), &out); err != nil {
		w.T.Fatalf("%v\n%s", err, r.Stdout)
	}
	return out.HookSpecificOutput.Reason
}

func (w *placeWS) allThree() map[string]string {
	env := map[string]string{}
	for _, k := range layerKinds {
		env[k.env] = w.other[k.name]
	}
	return env
}

// env は共通レイヤーを動かさない。1 本ずつ渡しても、3 本まとめて渡しても、共通レイヤーは `.ccnavi/config/common/` のまま。
// env が指したファイルの deny はヒットしない（置き場だけでなく判定でも確かめる）。
func TestEnvDoesNotMoveTheCommonLayer(t *testing.T) {
	t.Parallel()
	for _, k := range layerKinds {
		t.Run(k.env, func(t *testing.T) {
			t.Parallel()
			w := newPlaceWS(t)
			layer := w.common(map[string]string{k.env: w.other[k.name]}, nil)
			if got := pathOf(layer, k.field); !run.SamePath(got, w.own[k.name]) {
				t.Errorf("%s = %s, want %s", k.field, got, w.own[k.name])
			}
		})
	}
	t.Run("all three", func(t *testing.T) {
		t.Parallel()
		w := newPlaceWS(t)
		layer := w.common(w.allThree(), nil)
		for _, k := range layerKinds {
			if got := pathOf(layer, k.field); !run.SamePath(got, w.own[k.name]) {
				t.Errorf("%s = %s, want %s", k.field, got, w.own[k.name])
			}
		}
	})
	t.Run("the rules the env names are not applied", func(t *testing.T) {
		t.Parallel()
		w := newPlaceWS(t)
		r := w.decide("Read", map[string]string{"CCNAVI_RULES": w.other["rules"]}, "disable", nil, configws.Obj{"file_path": w.target()})
		if strings.Contains(w.reason(r), "only-in-the-other-file") {
			t.Errorf("env が指したルールがヒットした: %s", r.Stdout)
		}
	})
}

// 診断のためのフラグは残る（消しすぎを見つけるための確認）。`--rules` / `--phases` / `--risk` は、診断（`--explain`）ではそれぞれの 1 本を動かし、
// env を渡してもフラグのほうを採る。
func TestFlagsStillMoveTheCommonLayerInDiagnostics(t *testing.T) {
	t.Parallel()
	for _, k := range layerKinds {
		t.Run(k.flag, func(t *testing.T) {
			t.Parallel()
			w := newPlaceWS(t)
			layer := w.common(nil, []string{k.flag, w.other[k.name]})
			if got := pathOf(layer, k.field); !run.SamePath(got, w.other[k.name]) {
				t.Errorf("%s = %s, want %s", k.field, got, w.other[k.name])
			}
		})
	}
	t.Run("the flag wins over the env", func(t *testing.T) {
		t.Parallel()
		w := newPlaceWS(t)
		env := map[string]string{"CCNAVI_RULES": filepath.Join(w.Root, "elsewhere", "nowhere.yml")}
		layer := w.common(env, []string{"--rules", w.other["rules"]})
		if got := pathOf(layer, "rules"); !run.SamePath(got, w.other["rules"]) {
			t.Errorf("rules = %s, want %s", got, w.other["rules"])
		}
	})
}

// 診断の外では、フラグも共通レイヤーを動かさない（issue #65）。`--project-rules-file` / `--project-phases-file` と揃える。
// 上の TestFlagsStillMoveTheCommonLayerInDiagnostics と対で読む。片方だけだと、フラグを消してもそのまま通してもテストは通り続ける。
func TestFlagsAreDiagnosisOnly(t *testing.T) {
	t.Parallel()
	t.Run("each of the three flags is dropped and says so", func(t *testing.T) {
		t.Parallel()
		for _, k := range layerKinds {
			w := newPlaceWS(t)
			r := w.decide("Read", nil, "disable", []string{k.flag, w.other[k.name]}, configws.Obj{"file_path": w.target()})
			if !strings.Contains(r.Stderr, k.flag+" は診断") {
				t.Errorf("%s: 無視したことを言わない: %q", k.flag, r.Stderr)
			}
			// 判定は渡したファイルの deny を読まない。
			if strings.Contains(w.reason(r), "only-in-the-other-file") {
				t.Errorf("%s: 渡したファイルの deny がヒットした: %s", k.flag, r.Stdout)
			}
		}
	})
	t.Run("the wording is the one the project file flags use", func(t *testing.T) {
		t.Parallel()
		w := newPlaceWS(t)
		input := configws.Obj{"file_path": w.target()}
		one := w.decide("Read", nil, "disable", []string{"--rules", w.other["rules"]}, input)
		other := w.decide("Read", nil, "disable", []string{"--project-rules-file", "lib=" + w.other["rules"]}, input)
		if a, b := strings.ReplaceAll(one.Stderr, "--rules", "FLAG"), strings.ReplaceAll(other.Stderr, "--project-rules-file", "FLAG"); a != b {
			t.Errorf("文面が違う:\n%q\n%q", one.Stderr, other.Stderr)
		}
	})
	// `--risk ""` は「渡した」と数える（空は「配点を持たない」の意味だった）。`--rules ""` は渡していないのと同じ
	// （`--rules` の既定は空文字なので、その 1 本だけ毎回言うことになるのを避ける）。
	t.Run("an empty flag", func(t *testing.T) {
		t.Parallel()
		w := newPlaceWS(t)
		input := configws.Obj{"file_path": w.target()}
		if r := w.decide("Read", nil, "disable", []string{"--risk", ""}, input); !strings.Contains(r.Stderr, "--risk は診断") {
			t.Errorf("空の --risk を言わない: %q", r.Stderr)
		}
		if r := w.decide("Read", nil, "disable", []string{"--rules", ""}, input); strings.Contains(r.Stderr, "--rules") {
			t.Errorf("空の --rules を言った: %q", r.Stderr)
		}
	})
	t.Run("the flags still work for the diagnosis that judges", func(t *testing.T) {
		t.Parallel()
		w := newPlaceWS(t)
		done := w.bare(nil, "disable", []string{"--rules", w.other["rules"]}, "", "--test", "Read", w.target(), "--json")
		var body struct {
			Rules []struct {
				ID string `json:"id"`
			} `json:"rules"`
		}
		if err := json.Unmarshal([]byte(done.Stdout), &body); err != nil {
			t.Fatalf("%v\n%s", err, done.Stdout)
		}
		if len(body.Rules) != 1 || body.Rules[0].ID != "elsewhere/rules.yml:only-in-the-other-file" {
			t.Errorf("rules = %+v", body.Rules)
		}
	})
}

// env がよそを指していても、`.ccnavi/config/common/` の 3 本は組み込みで止まる。守る対象は既定の置き場から組み立てる。
// env を読む経路が戻ると、`builtin:guard-common-layer` が env の指すよそへ移り、ここが失敗する。
func TestTheDefaultPlaceStaysGuardedWhileTheEnvNamesAnother(t *testing.T) {
	t.Parallel()
	for _, k := range layerKinds {
		t.Run(k.name, func(t *testing.T) {
			t.Parallel()
			w := newPlaceWS(t)
			r := w.decide("Write", w.allThree(), "enable", nil, configws.Obj{"file_path": w.own[k.name]})
			if !strings.Contains(w.reason(r), "builtin:guard-common-layer") {
				t.Errorf("止まらない: %s", r.Stdout)
			}
		})
	}
}
