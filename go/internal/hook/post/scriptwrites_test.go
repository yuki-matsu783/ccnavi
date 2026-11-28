package post

import (
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/testkit"
)

// scriptWrites は、チケットの置き場の変更のうち ccnavi のサブコマンドが書いたと内容から読めるものを外す。外しすぎると、承認済み
// チケットが宣言する範囲を、引数に現れない書き込みで広げる経路ができる。外さなさすぎると、自分の手順を自分の違反として報告する。
// 実際の git のリポジトリで、コミット済みの版といまの版の組から、外れるパスを表で確かめる。

const (
	swDoing    = ".ccnavi/approved/doing/i0001.md"
	swReview   = "wip/proposals/review/i0001.md"
	swTodo     = "wip/proposals/todo/i0001.md"
	swDone     = ".ccnavi/approved/done/i0001.md"
	swMarker   = ".ccnavi/approved/phases/i0001/1.requested"
	swStartedT = "2026-09-21T00:00:00Z"
)

// swTicket は frontmatter の最後に extra の行を足したチケット。id が違えば別のチケット。
func swTicket(id string, extra ...string) string {
	text := "---\nversion: 1\nticket: " + id + "\nallow:\n  - match: Write|Edit\n    glob: \"src/*\"\n"
	for _, e := range extra {
		text += e + "\n"
	}
	return text + "---\nbody\n"
}

var (
	swBase    = swTicket("i0001")
	swStarted = swTicket("i0001", `started_at: "`+swStartedT+`"`, `base_sha: "1111111111111111"`)
	// 承認が `started_at: ""` の形で欄を置いたもの。
	swPlaced    = swTicket("i0001", `started_at: ""`, `completed_at: ""`, `base_sha: ""`)
	swPlacedRun = swTicket("i0001", `started_at: "2026-09-22T00:00:00Z"`, `completed_at: "2026-09-22T01:00:00Z"`, `base_sha: "1111111111111111"`)
	swFinished  = swTicket("i0001", `completed_at: "2026-09-22T01:00:00Z"`)
	// 着手済み（started_at・base_sha あり）に completed_at だけが足された版。
	swStartedFinished = swTicket("i0001", `started_at: "`+swStartedT+`"`, `base_sha: "1111111111111111"`, `completed_at: "2026-09-22T01:00:00Z"`)
	// 着手済みに mr を足し、同時に基準点も書き換えた版。
	swMRAndRewrittenBase = swTicket("i0001", `started_at: "`+swStartedT+`"`, `base_sha: "2222222222222222"`, "mr: 12")
)

// swMR は mr の欄だけを足したチケット。
func swMR(extra ...string) string { return swTicket("i0001", extra...) }

type swCase struct {
	name      string
	committed map[string]string
	now       map[string]string // 値が "-" なら消す
	want      []string          // 外れるパス
}

func (c swCase) run(t *testing.T) {
	t.Helper()
	root := repo(t)
	// committed が nil なら、コミットの無いリポジトリ（HEAD が無く、コミット済みの版を読めない）。
	if c.committed != nil {
		put(t, root, "README.md", "x\n")
		for p, text := range c.committed {
			put(t, root, p, text)
		}
		testkit.Commit(t, root, "init")
	}
	for p, text := range c.now {
		if text == "-" {
			if err := os.Remove(filepath.Join(root, filepath.FromSlash(p))); err != nil {
				t.Fatal(err)
			}
			continue
		}
		put(t, root, p, text)
	}
	changes, why := git.Read(root, 0)
	if why != "" {
		t.Fatal(why)
	}
	s := scan{changes: changes, rules: &rules.RuleSet{}, where: tree.Tree{Root: root}, top: root,
		places: Places{Tickets: "wip/proposals", Approved: ".ccnavi/approved"}}
	var got []string
	for full, ok := range s.scriptWrites() {
		if ok {
			got = append(got, filepath.ToSlash(strings.TrimPrefix(full, root+string(filepath.Separator))))
		}
	}
	slices.Sort(got)
	want := slices.Clone(c.want)
	slices.Sort(want)
	if !slices.Equal(got, want) {
		t.Errorf("外れたもの = %v、想定 %v", got, want)
	}
}

func TestScriptWritesTable(t *testing.T) {
	doing := func(text string) map[string]string { return map[string]string{swDoing: text} }
	for _, c := range []swCase{
		// 外れるもの。サブコマンドが書いたと読める。
		{"着手の欄だけの書き足し", doing(swBase), doing(swTicket("i0001", `started_at: "`+swStartedT+`"`)), []string{swDoing}},
		{"マーカーの設置（どちらの版も範囲を宣言しない）", doing(swBase), map[string]string{swMarker: `{"mr": 1}` + "\n"}, []string{swMarker}},
		{"finish の移動（作業中からレビュー待ち）", doing(swBase), map[string]string{swDoing: "-", swReview: swFinished}, []string{swDoing, swReview}},
		{"着手済みのチケットをクローズして動かす", doing(swStarted), map[string]string{swDoing: "-", swReview: swStartedFinished}, []string{swDoing, swReview}},
		{"空の欄を持つチケットをクローズして動かす", doing(swPlaced), map[string]string{swDoing: "-", swReview: swPlacedRun}, []string{swDoing, swReview}},
		{"レビュー待ちをクローズした置き場へ動かす", doing(swPlaced), map[string]string{swDoing: "-", swDone: swPlacedRun}, []string{swDoing, swDone}},
		// 外れないもの。範囲・基準点・場所の偽造。
		{"範囲を広げる編集", doing(swBase), doing(strings.Replace(swBase, `glob: "src/*"`, `glob: "*"`, 1)), nil},
		{"承認済みチケットを消すだけ", doing(swBase), map[string]string{swDoing: "-"}, nil},
		{"承認待ちへ逃がす移動", doing(swBase), map[string]string{swDoing: "-", swTodo: swBase}, nil},
		{"承認済みチケットを新しく置く", doing(swBase), map[string]string{".ccnavi/approved/doing/i0002.md": swTicket("i0002")}, nil},
		{"承認待ちからレビュー待ちへの移動", map[string]string{swTodo: swBase}, map[string]string{swTodo: "-", swReview: swBase}, nil},
		{"基準点の書き換え", doing(swStarted), doing(strings.Replace(swStarted, "1111111111111111", "2222222222222222", 1)), nil},
		{"着手の時刻の書き換え", doing(swStarted), doing(strings.Replace(swStarted, "2026-09-21", "2026-09-20", 1)), nil},
		{"続きの行に置いた基準点の書き換え",
			doing(swTicket("i0001", "base_sha: |", "  1111111111111111")),
			doing(swTicket("i0001", "base_sha: |", "  2222222222222222")), nil},
		// 同じ内容が 2 つ以上あれば、どれがどれの行き先かを決められない。
		{"同じ内容が 2 つ動くときは移動として外さない",
			map[string]string{swDoing: swBase, ".ccnavi/approved/doing/i0002.md": swBase},
			map[string]string{swDoing: "-", ".ccnavi/approved/doing/i0002.md": "-", swReview: swFinished}, nil},
		{"行き先に同じ内容が 2 つあるときも外さない", doing(swBase),
			map[string]string{swDoing: "-", swReview: swFinished, "wip/proposals/review/i0001-copy.md": swFinished}, nil},
		// コミット済みの版を読めなければ外さない。
		{"コミット済みの版が無いリポジトリのマーカー", nil, map[string]string{swMarker: `{"mr": 1}` + "\n"}, nil},
	} {
		t.Run(c.name, c.run)
	}
}

// review-request が親の承認済みチケットに書き足す `mr:` だけは、空から整数になった変更として外す。
func TestScriptWritesMRFilled(t *testing.T) {
	held := swMR("mr: 12")
	for _, c := range []swCase{
		{"欄が無い版から整数", map[string]string{swDoing: swBase}, map[string]string{swDoing: swMR("mr: 12")}, []string{swDoing}},
		{"値なしの版から整数", map[string]string{swDoing: swMR(`mr: ""`)}, map[string]string{swDoing: swMR("mr: 12")}, []string{swDoing}},
		{"整数が別の整数になる", map[string]string{swDoing: held}, map[string]string{swDoing: swMR("mr: 13")}, nil},
		{"整数が空になる", map[string]string{swDoing: held}, map[string]string{swDoing: swMR(`mr: ""`)}, nil},
		{"整数の欄が消える", map[string]string{swDoing: held}, map[string]string{swDoing: swBase}, nil},
		{"mr を足すと同時に範囲が変わる", map[string]string{swDoing: swBase},
			map[string]string{swDoing: strings.Replace(swMR("mr: 12"), "src/*", "*", 1)}, nil},
		{"mr を足すと同時に本文が変わる", map[string]string{swDoing: swBase},
			map[string]string{swDoing: strings.Replace(swMR("mr: 12"), "body", "body2", 1)}, nil},
		{"mr の足し込みと基準点の書き換えが一緒", map[string]string{swDoing: swStarted},
			map[string]string{swDoing: swMRAndRewrittenBase}, nil},
	} {
		t.Run(c.name, c.run)
	}
	for _, raw := range []string{`"12"`, "12 # c", "[12]", "0", "-1", "x"} {
		t.Run("整数以外の書き方 "+raw, swCase{"", map[string]string{swDoing: swBase}, map[string]string{swDoing: swMR("mr: " + raw)}, nil}.run)
	}
}

// 省いてよい欄は、コミット済みの版がまだ値を持たない欄だけ。
func TestDroppable(t *testing.T) {
	has := func(drop []string, key string) bool { return slices.Contains(drop, key) }
	if drop := droppable(version{}); !has(drop, "started_at") || !has(drop, "base_sha") {
		t.Errorf("版が無ければ全部省く: %v", drop)
	}
	if drop := droppable(version{swPlaced, true}); !has(drop, "started_at") || !has(drop, "completed_at") || !has(drop, "base_sha") {
		t.Errorf("空の欄は値と数えない: %v", drop)
	}
	drop := droppable(version{swStarted, true})
	if has(drop, "started_at") || has(drop, "base_sha") || !has(drop, "completed_at") {
		t.Errorf("値のある欄は残し、無い欄だけ省く: %v", drop)
	}
}

func TestMRFilled(t *testing.T) {
	v := func(text string) version { return version{text, true} }
	for _, c := range []struct {
		name        string
		before, now version
		want        bool
	}{
		{"欄が無い版から整数", v(swBase), v(swMR("mr: 12")), true},
		{"値なしから整数", v(swMR("mr: \"\"")), v(swMR("mr: 12")), true},
		{"整数から別の整数", v(swMR("mr: 12")), v(swMR("mr: 13")), false},
		{"整数以外の書き方", v(swBase), v(swMR(`mr: "12"`)), false},
		{"コミット済みの版が無い", version{}, v(swMR("mr: 12")), false},
		{"いまの版が無い", v(swBase), version{}, false},
		{"コミット済みの版がチケットでない", v("{}\n"), v(swMR("mr: 12")), false},
	} {
		if got := len(mrFilled(c.before, c.now)) > 0; got != c.want {
			t.Errorf("%s: %v", c.name, got)
		}
	}
}
