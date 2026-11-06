package internal

// internal/ の下の import の向きを守る。段の小さいパッケージは、段の大きいパッケージを読まない。
// 循環は Go のコンパイラが止めるので、ここでは向きだけを見る。
//
// 段は 2 つの表で決める。topTiers は internal/ 直下の並び。subTiers は直下の中をさらに分けるときの並びで、
// 同じ親の下の兄弟は、段が小さいものだけを読める（段が同じ兄弟は互いに読めない）。subTiers に親が
// 無ければ、その中の兄弟どうしの向きは見ない。新しいパッケージを足すときは、先にこの表を直す。

import (
	"go/parser"
	"go/token"
	"io/fs"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
)

const modulePrefix = "ccnavi/internal/"

var topTiers = map[string]int{
	"infra":   0,
	"records": 1,
	"policy":  2,
	"tickets": 3,
	"catalog": 4,
	"hook":    5,
	"entry":   6,
}

var subTiers = map[string]map[string]int{
	"infra": {
		// 0: 何も読まない土台（環境変数の名前・Python の表記・os.path の写しと OS の分岐）
		"env":    0,
		"pyfmt":  0,
		"ospath": 0,
		// 1: 土台だけを読む
		"pathcase":    1,
		"pyjson":      1,
		"platformtag": 1,
		"yaml":        1,
		"pyre":        1, // Python の re の写し（RE2 で書けない式を Python と同じ答えで読む）
		// 2: ファイル・設定・入出力・照合
		"fsio":      2,
		"config":    2,
		"hookio":    2,
		"pathmatch": 2,
		"shell":     2,
		"tree":      2,
		// 3: fsio の上に立つ
		"git": 3,
	},
	"records": {
		// 0: records のほかを読まない（伏せ字・学びの wiki）
		"redact": 0,
		"wiki":   0,
		// 0: セッションごとのターンの状態と、放置したセッションの引き継ぎ
		"turn":    0,
		"handoff": 0,
		// 1: 伏せ字を通して書く（判定の記録・診断ログ）
		"audit":   1,
		"diaglog": 1,
		// 1: 伏せ字を通して材料を残す（Stop の AI 判定の計画・試行・操作実績・集計）
		"stopreview": 1,
		// 2: 判定の記録の上に立つ（拒否と失敗の数え）
		"tally": 2,
		// 3: 記録の名前を持ち主のパッケージから読んで消す（後始末）
		"prune": 3,
	},
	"policy": {
		// 1: ルールの型・読み込み・照合と、ルールを当てる文字列
		"rules": 1,
		// 2: ルールの上に立つ（文面ファイル・ルールの文脈ファイル）
		"texts":   2,
		"ctxfile": 2,
		// 3: ルールファイルの検査（文脈ファイルの置き場を見る）
		"rulelint": 3,
		// 3: 自己防衛（守る場所への書き込みを止める組み込みのルールと、守る対象のバックアップと戻し）
		"selfguard": 3,
		// 4: 組み込みのルール（selfguard の守る形を読む）
		"builtin": 4,
		// 5: 適用するルール集合を決める（組み込みとレイヤーを重ねる）
		"ruleload": 5,
	},
	"tickets": {
		"model":     0,
		"config":    1,
		"flow":      1,
		"approval":  2,
		"phase":     3,
		"learning":  4,
		"proposal":  4,
		"sync":      5,
		"lifecycle": 6,
		"notice":    7,
		"review":    7,
		"core":      8,
	},
}

// importsOf は internal/ の下の各パッケージ（internal/ からの相対パス）と、それが読む internal/ のパッケージを返す。
func importsOf(t *testing.T) map[string][]string {
	t.Helper()
	out := map[string][]string{}
	err := filepath.WalkDir(".", func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.IsDir() || !strings.HasSuffix(path, ".go") || strings.HasSuffix(path, "_test.go") {
			return nil
		}
		f, err := parser.ParseFile(token.NewFileSet(), path, nil, parser.ImportsOnly)
		if err != nil {
			return err
		}
		pkg := filepath.ToSlash(filepath.Dir(path))
		for _, spec := range f.Imports {
			p, _ := strconv.Unquote(spec.Path.Value)
			if rest, ok := strings.CutPrefix(p, modulePrefix); ok {
				out[pkg] = append(out[pkg], rest)
			}
		}
		if _, ok := out[pkg]; !ok {
			out[pkg] = nil
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	return out
}

// violation は from が to を読んではいけない理由を返す。読んでよければ空。
func violation(from, to string) string {
	fs, ts := strings.Split(from, "/"), strings.Split(to, "/")
	if fs[0] != ts[0] {
		if topTiers[ts[0]] >= topTiers[fs[0]] {
			return "上か同じ段の " + ts[0] + " を読んでいる"
		}
		return ""
	}
	tiers, ok := subTiers[fs[0]]
	if !ok || len(fs) < 2 || len(ts) < 2 || fs[1] == ts[1] {
		return ""
	}
	if tiers[ts[1]] >= tiers[fs[1]] {
		return "兄弟で上か同じ段の " + ts[0] + "/" + ts[1] + " を読んでいる"
	}
	return ""
}

func TestLayers(t *testing.T) {
	if _, err := os.Stat("infra"); err != nil {
		t.Fatal(err)
	}
	for pkg, deps := range importsOf(t) {
		top := strings.Split(pkg, "/")[0]
		if _, ok := topTiers[top]; !ok {
			t.Errorf("%s: 段の表（topTiers）に無い", pkg)
			continue
		}
		if tiers, ok := subTiers[top]; ok {
			if parts := strings.Split(pkg, "/"); len(parts) >= 2 {
				if _, ok := tiers[parts[1]]; !ok {
					t.Errorf("%s: 段の表（subTiers[%s]）に無い", pkg, top)
				}
			}
		}
		for _, dep := range deps {
			if why := violation(pkg, dep); why != "" {
				t.Errorf("%s → %s: %s", pkg, dep, why)
			}
		}
	}
}

func TestViolation(t *testing.T) {
	cases := []struct {
		from, to string
		bad      bool
	}{
		{"hook", "infra/git", false},
		{"infra/git", "hook", true},
		{"policy", "records", false},
		{"records", "policy", true},
		{"tickets/core", "tickets/model", false},
		{"tickets/model", "tickets/core", true},
		{"tickets/notice", "tickets/review", true},
		{"infra/git", "infra/fsio", false},
		{"infra/fsio", "infra/git", true},
		{"infra/pathcase", "infra/ospath", false},
		{"infra/ospath", "infra/pathcase", true},
		{"infra/tree", "infra/fsio", true},
	}
	for _, c := range cases {
		if got := violation(c.from, c.to) != ""; got != c.bad {
			t.Errorf("violation(%q, %q) bad=%v, want %v", c.from, c.to, got, c.bad)
		}
	}
}
