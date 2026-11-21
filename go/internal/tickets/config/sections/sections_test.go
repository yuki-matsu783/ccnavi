package sections

import (
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/model/form"
	"ccnavi/internal/tickets/testkit"
)

func TestParse(t *testing.T) {
	f, notes := Parse("version: 1\nparent:\n  - name: 目的\n    todo: （目的）\n  - name: ' 範囲 '\nchild: []\n", "x")
	if len(notes) != 0 {
		t.Fatalf("苦情 %v", notes)
	}
	got := f.over(form.Sections{Child: []form.Heading{{Name: "残らない"}}})
	// 空の並び（child: []）も置き換える（その種類の見出しを見ない）。
	want := form.Sections{Parent: []form.Heading{{Name: "目的", Todo: "（目的）"}, {Name: "範囲"}}}
	if !reflect.DeepEqual(got.Parent, want.Parent) || len(got.Child) != 0 {
		t.Errorf("読んだ並び %#v", got)
	}
}

func TestParseProblems(t *testing.T) {
	cases := []struct{ name, text, word string }{
		{"YAML でない", "parent: [\n", "YAML として読めない"},
		{"最上位がマップでない", "- a\n", "最上位がマップではない"},
		{"版が違う", "version: 2\n", "版 2 は扱えない"},
		{"版が無い", "parent: []\n", "版 None は扱えない"},
		{"知らない最上位のキー", "version: 1\nparents: []\n", "知らないキー `parents`"},
		{"並びでない", "version: 1\nparent: {name: 目的}\n", "`parent` が並び（リスト）ではない"},
		{"項がマップでない", "version: 1\nchild: [目的]\n", "項がマップではない"},
		{"name が無い", "version: 1\nparent:\n  - todo: x\n", "`name` が無いか空か"},
		{"name が空", "version: 1\nparent:\n  - name: ' '\n", "`name` が無いか空か"},
		{"name が文字列でない", "version: 1\nparent:\n  - name: 1\n", "`name` が無いか空か"},
		{"todo が文字列でない", "version: 1\nparent:\n  - {name: a, todo: [x]}\n", "`todo` が文字列ではない"},
		{"項の知らないキー", "version: 1\nparent:\n  - {name: a, title: b}\n", "知らないキー `title`"},
		{"重なる名前は大文字小文字を区別しない", "version: 1\nparent:\n  - name: Goal\n  - name: goal\n", "見出し `goal` が重なる"},
	}
	for _, c := range cases {
		f, notes := Parse(c.text, "x")
		if f != nil {
			t.Errorf("%s: 壊れているのに読んだ", c.name)
		}
		if len(notes) == 0 || notes[0].Severity != rules.Error || !strings.Contains(notes[0].Detail, c.word) {
			t.Errorf("%s: 苦情 %v", c.name, notes)
		}
	}
}

func TestLayer(t *testing.T) {
	root := t.TempDir()
	conf := &config.Settings{Sections: filepath.Join(root, ".ccnavi", "config", "common", "ticket-sections.yml")}
	layerPath := filepath.Join(root, ".ccnavi", "config", "project", "ticket-sections.yml")
	// どちらにも無ければ見出しを見ない。
	if got, notes := Layer(conf, root, ""); !reflect.DeepEqual(got, form.Sections{}) || notes != nil {
		t.Errorf("無いのに %v %v", got, notes)
	}
	testkit.Put(t, conf.Sections, "version: 1\nparent:\n  - name: 目的\nchild:\n  - name: 担当範囲\n")
	common := form.Sections{Parent: []form.Heading{{Name: "目的"}}, Child: []form.Heading{{Name: "担当範囲"}}}
	if got := For(conf, root, ""); !reflect.DeepEqual(got, common) {
		t.Errorf("共通だけ %v", got)
	}
	// レイヤーにある種類だけ置き換える。
	testkit.Put(t, layerPath, "version: 1\nparent:\n  - name: Goal\n")
	if got := For(conf, root, ""); !reflect.DeepEqual(got, form.Sections{Parent: []form.Heading{{Name: "Goal"}}, Child: common.Child}) {
		t.Errorf("重ねた %v", got)
	}
	// 判定の基準は共通レイヤーだけを見る。
	if got := Common(conf); !reflect.DeepEqual(got, common) {
		t.Errorf("共通 %v", got)
	}
	// 壊れたレイヤーは使わず、そのレイヤーの苦情を返す。
	testkit.Put(t, layerPath, "version: 1\nparent: x\n")
	got, notes := Layer(conf, root, "")
	if !reflect.DeepEqual(got, common) || len(notes) != 1 {
		t.Errorf("壊れたレイヤー %v %v", got, notes)
	}
	// 予約名のプロジェクトはレイヤーとして数えない。
	if got, notes := Layer(conf, root, config.LayerCommon); !reflect.DeepEqual(got, common) || notes != nil {
		t.Errorf("予約名 %v %v", got, notes)
	}
	// 共通レイヤーが壊れていれば、共通レイヤーの見出しは使わない（苦情は CommonFile が返す）。
	testkit.Put(t, conf.Sections, "\xff")
	testkit.Put(t, layerPath, "version: 1\nchild:\n  - name: 担当範囲\n")
	if got, notes := Layer(conf, root, ""); !reflect.DeepEqual(got, form.Sections{Child: common.Child}) || len(notes) != 0 {
		t.Errorf("壊れた共通 %v %v", got, notes)
	}
	if _, notes := CommonFile(conf); len(notes) != 1 || !strings.Contains(notes[0].Detail, "を読めない") {
		t.Errorf("読めない共通 %v", notes)
	}
}
