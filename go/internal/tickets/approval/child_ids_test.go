package approval

import (
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"ccnavi/internal/tickets/model/ticket"
	"ccnavi/internal/tickets/testkit"
)

// files は .git の外のファイルの中身（続きの子が何も書かなかったことを比べる）。
func files(t *testing.T, root string) map[string]string {
	t.Helper()
	found := map[string]string{}
	err := filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.Name() == ".git" {
			if d.IsDir() {
				return filepath.SkipDir
			}
			return nil
		}
		if !d.IsDir() {
			data, err := os.ReadFile(path)
			if err != nil {
				return err
			}
			found[path] = string(data)
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	return found
}

func parsed(t *testing.T, text string) *ticket.Ticket {
	t.Helper()
	found, problems := ticket.Parse(text)
	if found == nil {
		t.Fatal(problems)
	}
	return found
}

// 続きの子は、組めない識別子や先に在るファイル（読めないものも）の上では、何も書かずに止まる。
func TestFollowupRefusesWithoutWriting(t *testing.T) {
	parent := parsed(t, parentText("i0001", "  - research"))
	child := parsed(t, childText("i0001-02-01", "i0001", "2", "src/*"))
	cases := []struct {
		name      string
		worktree  bool
		phase     int
		put       map[string]string
		wantIdent string
		want      []string
	}{
		{"連番が埋まった", true, 2, map[string]string{".claude/worktrees/i0001/.ccnavi/approved/done/i0001-02-99.md": childText("i0001-02-99", "i0001", "2", "src/*")},
			"", []string{"連番 99 まで埋まっている"}},
		{"3 桁のフェーズ", true, 100, nil, "", []string{"フェーズ 100 は子の識別子に指定できません"}},
		{"読めないファイルが承認済みの置き場に在る", true, 2, map[string]string{".claude/worktrees/i0001/.ccnavi/approved/doing/I0001-02-01.md": "壊れた中身\n"},
			"i0001-02-01", []string{"すでに在る", "I0001-02-01.md"}},
		{"読めないファイルが提案の置き場に在る", true, 2, map[string]string{"wip/proposals/review/I0001-02-01.md": "壊れた中身\n"},
			"i0001-02-01", []string{"すでに在る", "I0001-02-01.md"}},
		{"親のワークツリーが無い", false, 2, nil, "i0001-02-01", []string{"親のワークツリーが決まらない"}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			var root string
			if c.worktree {
				root = testkit.Workspace(t, "i0001")
			} else {
				root = testkit.Workspace(t)
			}
			for rel, text := range c.put {
				testkit.Put(t, filepath.Join(root, filepath.FromSlash(rel)), text)
			}
			conf := testkit.Settings(t, root)
			before := files(t, root)
			ident, why := Followup(conf, root, parent, c.phase, []*ticket.Ticket{child}, []string{"指摘"}, io.Discard, "op-1")
			if ident != c.wantIdent {
				t.Errorf("識別子 = %q", ident)
			}
			for _, w := range c.want {
				if !strings.Contains(why, w) {
					t.Errorf("理由に %q が無い: %q", w, why)
				}
			}
			if after := files(t, root); !reflect.DeepEqual(before, after) {
				t.Errorf("書いた: %v", after)
			}
		})
	}
}

// 空いた番号なら、親のワークツリーの承認済みの置き場に書く。
func TestFollowupWritesAFreeNumber(t *testing.T) {
	root := testkit.Workspace(t, "i0001")
	testkit.Put(t, testkit.Approved(root, "i0001", "done", "i0001-02-01"), childText("i0001-02-01", "i0001", "2", "src/*"))
	conf := testkit.Settings(t, root)
	parent := parsed(t, parentText("i0001", "  - research"))
	child := parsed(t, childText("i0001-02-01", "i0001", "2", "src/*"))
	ident, why := Followup(conf, root, parent, 2, []*ticket.Ticket{child}, []string{"指摘"}, io.Discard, "op-1")
	if ident != "i0001-02-02" || why != "" {
		t.Fatalf("%q %q", ident, why)
	}
	if _, err := os.Stat(testkit.Approved(root, "i0001", "doing", ident)); err != nil {
		t.Error(err)
	}
}
