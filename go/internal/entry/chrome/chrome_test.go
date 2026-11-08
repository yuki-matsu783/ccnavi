package chrome

import (
	"errors"
	"strconv"
	"strings"
	"testing"

	"ccnavi/internal/entry/version"
	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyjson"
)

func TestHandleRefusesWithoutRunning(t *testing.T) {
	for _, tc := range []struct{ request, want string }{
		{`not json`, "要求を JSON として読めません"},
		{`{"schema": 2, "op": "placement"}`, "要求の形式の版が一致しません"},
		{`{"schema": 1, "op": "nope"}`, "未対応の操作です：'nope'"},
		{`{"schema": 1, "op": "board"}`, "snapshot がありません"},
	} {
		got := Handle(tc.request, t.TempDir())
		if !strings.Contains(got, tc.want) || !strings.HasPrefix(got, `{"schema": 1, "error": `) {
			t.Errorf("%s: %s", tc.request, got)
		}
	}
}

// ホストから来たパスは、根から始まるもの・区切りが `\` のもの・`.`・`..`・空の段を含むものを断る。
func TestHostPathsThatLeaveTheTreeAreRefused(t *testing.T) {
	if err := checkRel("wip/proposals/todo/i0001.md"); err != nil {
		t.Fatal(err)
	}
	for _, path := range []string{"", "/a", "~/a", "C:/a", `\a`, `a\b`, "a/../b", "a/./b", "a//b", "a/"} {
		var r *refusal
		if err := checkRel(path); !errors.As(err, &r) {
			t.Errorf("%q: %v", path, err)
		}
	}
}

// 仮のツリーのパスは、パスの頭（行の頭か、空白・引用符・括弧・区切りの直後）に在るときだけ畳む。
func TestRelativeFoldsOnlyAtTheHeadOfAPath(t *testing.T) {
	root := ospath.Join(ospath.Sep, "ws")
	sep := ospath.Sep
	for _, tc := range []struct{ in, want string }{
		{"wip/ws/todo/i0001.md", "wip/ws/todo/i0001.md"},
		{"読めない: " + root + sep + ".claude" + sep + "worktrees" + sep + "i0001" + sep + "wip/ws/x.md", "読めない: i0001:wip/ws/x.md"},
		{"'" + root + sep + "a' と a/ws/b", "'a' と a/ws/b"},
	} {
		if got := relative(root, tc.in); got != tc.want {
			t.Errorf("%q: %q", tc.in, got)
		}
	}
}

func TestWritesOnlyToTheParentBranch(t *testing.T) {
	for _, tc := range []struct {
		changes *pyjson.Object
		ok      bool
	}{
		{pyjson.NewObject("i0001", []any{}), true},
		{pyjson.NewObject("i0001", []any{}, "i0003", []any{}), false},
		{pyjson.NewObject("main", []any{}), false},
	} {
		err := oneBranch(pyjson.NewObject("changes", tc.changes), "i0001")
		if (err == nil) != tc.ok {
			t.Errorf("%v: %v", tc.changes.Keys(), err)
		}
	}
}

func TestReservedNamesAndTheIntegrationAreNotWritten(t *testing.T) {
	compat := "#!/bin/sh\nCCNAVI_COMPAT=" + strconv.Itoa(version.Compat) + "\n"
	s := &snapshot{integration: "trunk", branches: map[string]*branch{
		"trunk": {paths: []string{config.CompatFile}, files: map[string]string{config.CompatFile: compat}},
	}}
	if why := writeRefusal(s, "i0001"); why != "" {
		t.Fatal(why)
	}
	for _, name := range []string{"main", "Develop", "release-1", "trunk", "TRUNK"} {
		if why := writeRefusal(s, name); !strings.Contains(why, "予約された名前か統合先の名前") {
			t.Errorf("%s: %q", name, why)
		}
	}
	s.branches["trunk"].files[config.CompatFile] = "CCNAVI_COMPAT=" + strconv.Itoa(version.Compat+1) + "\n"
	if why := writeRefusal(s, "i0001"); !strings.Contains(why, "拡張を更新してください") {
		t.Errorf("互換の版が違う: %q", why)
	}
}
