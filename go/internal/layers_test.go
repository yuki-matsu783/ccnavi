package internal

// internal/ の下の import の向きを守る。段の小さいパッケージは、段の大きいパッケージを読まない。
// 循環は Go のコンパイラが止めるので、ここでは向きだけを見る。
//
// 段は 2 つの表で決める。topTiers は internal/ 直下の並び。subTiers は親（"tickets" や "tickets/config"）の下を
// さらに分けるときの並びで、同じ親の下の兄弟（とその下）は、段が小さい兄弟だけを読める（段が同じ兄弟は互いに
// 読めない）。subTiers に親が無ければ、その中の兄弟どうしの向きは見ない。親がその下を読むのは見ない。
// 新しいパッケージを足すときは、先にこの表を直す。

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
		"env":     0,
		"pyfmt":   0,
		"ospath":  0,
		"digest":  0, // 16 進の要約と乱数（記録・バックアップの名前）
		"difflib": 0, // Python の difflib の写し（見せる差分）
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
		"yamljson":  2, // YAML の値と JSON の値の写し替え（yaml と pyjson を読む）
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
		// テストの道具（testkit）。一番上に置き、本体のコードから読めないようにする。
		"testkit": 9,
	},
	// tickets の各パッケージの中。並びはいまの依存のとおり。
	"tickets/approval": {"marks": 0, "wap": 1},
	"tickets/config":   {"layer": 0, "phases": 1, "risk": 1, "check": 2},
	"tickets/lifecycle": {
		"closing": 0, "plan": 0, "treegate": 0, "worktrees": 0,
		"stop": 1, "consolidate": 1, "retire": 1, "wapmerge": 1, "ops": 1,
		"integrate": 2,
		"branch":    3,
	},
	"tickets/model": {
		"ids": 0, "place": 0, "directcand": 0, "handoffnames": 0, "host": 0, "oplog": 0,
		"useronly": 0, "via": 0,
		"ticket": 1, "standing": 1, "guard": 1,
		"archive": 2, "fields": 2, "form": 2,
	},
	"tickets/proposal": {"internal": 0, "candidates": 0, "propose": 0, "templates": 1, "planedit": 1, "agree": 2, "scaffold": 2},
	"tickets/review":   {"request": 0, "closeout": 1, "decide": 1},
	"tickets/sync":     {"handoff": 0, "mirror": 0, "drafts": 1},
	// 目録。1 つのツリーの索引（docindex）の上に、ツリーを合わせて引く docs が立つ。
	"catalog": {"docindex": 0, "skills": 0, "docs": 1},
	// hook の中。
	"hook": {
		// サブエージェントの種類・子の担当の案内の行・終わり方の記録と、その上に立つ起動の担当の決定
		"agentkind": 0, "childnote": 0, "outcome": 0,
		"launch": 1,
		// 判定に達せないときの通し方・確認の前の発言の検査・ターン分離のゲート・シェルの書き込みの組み込みの検査
		"failsafe": 0, "askcontext": 0, "turngate": 0, "shellguard": 0,
		// PostToolUse の案内（ラッパーの呼び出しの読みを shellguard から借りる）
		"hint": 1,
		// 作業の決まり（CCNAVI.md）と付き添いの有無・Stop の判定役の見分け・実行後チェック
		"ccnavimd": 0, "stopjudge": 0, "post": 0,
		// 判定の文と理由コード（付き添いの有無を ccnavimd から読む）
		"reasons": 1,
		// PreToolUse の判定の本体（上の部品を順に当てる）
		"judge": 2,
		// サブエージェントの始まりと終わり（子の案内・終わり方の記録・実行後チェックの定数を読む）
		"subagent": 2,
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

// violation は from が to を読んではいけない理由を返す。読んでよければ空。分かれる所（最初に違う名前）の親の表で比べる。
func violation(from, to string) string {
	fs, ts := strings.Split(from, "/"), strings.Split(to, "/")
	if fs[0] != ts[0] {
		if topTiers[ts[0]] >= topTiers[fs[0]] {
			return "上か同じ段の " + ts[0] + " を読んでいる"
		}
		return ""
	}
	d := 1
	for d < len(fs) && d < len(ts) && fs[d] == ts[d] {
		d++
	}
	if d == len(fs) || d == len(ts) {
		return "" // 親と子のあいだ
	}
	tiers, ok := subTiers[strings.Join(fs[:d], "/")]
	if !ok {
		return ""
	}
	if tiers[ts[d]] >= tiers[fs[d]] {
		return "兄弟で上か同じ段の " + strings.Join(ts[:d+1], "/") + " を読んでいる"
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
		parts := strings.Split(pkg, "/")
		for d := 1; d < len(parts); d++ {
			parent := strings.Join(parts[:d], "/")
			if tiers, ok := subTiers[parent]; ok {
				if _, ok := tiers[parts[d]]; !ok {
					t.Errorf("%s: 段の表（subTiers[%s]）に無い", pkg, parent)
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
		{"tickets/config/check", "tickets/config/risk", false},
		{"tickets/config/risk", "tickets/config/phases", true},
		{"tickets/review/closeout", "tickets/review/request", false},
		{"tickets/review/request", "tickets/review/decide", true},
		{"tickets/lifecycle/integrate/x", "tickets/lifecycle/stop", false},
		{"tickets/approval", "tickets/approval/marks", false},
		{"tickets/lifecycle/stop", "tickets/model", false},
		{"tickets/core", "tickets/testkit", true},
	}
	for _, c := range cases {
		if got := violation(c.from, c.to) != ""; got != c.bad {
			t.Errorf("violation(%q, %q) bad=%v, want %v", c.from, c.to, got, c.bad)
		}
	}
}
