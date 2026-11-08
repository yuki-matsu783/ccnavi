package core

import (
	"bytes"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/tickets/model/via"
	"ccnavi/internal/tickets/testkit"
)

// editingAnswers はターミナルの答え。最初の答えを返す直前に edit を呼ぶ（ユーザが画面を見ている間に提案が
// 変わった形）。答えが尽きたら空（やめる）。
type editingAnswers struct {
	answers []string
	edit    func()
	asked   int
}

func (a *editingAnswers) Read(p []byte) (int, error) {
	if a.asked >= len(a.answers) {
		return 0, io.EOF
	}
	if a.asked == 0 {
		a.edit()
	}
	n := copy(p, a.answers[a.asked]+"\n")
	a.asked++
	return n, nil
}

// 確認を待つ間に提案が変わったら、見せていない中身を承認せず、最新を見せ直して確認を取り直す。
func TestTerminalAgreeShowsAgainWhenAProposalChanged(t *testing.T) {
	defer via.Session(via.CLI, "")()
	todo := func(root, id string) string {
		return filepath.Join(testkit.Worktree(root, "i0001"), "wip", "proposals", "todo", id+".md")
	}
	rewrite := func(path string, change func([]byte) []byte) func() {
		return func() {
			data, err := os.ReadFile(path)
			if err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(path, change(data), 0o644); err != nil {
				t.Fatal(err)
			}
		}
	}
	copied := func(root, id string) bool {
		_, err := os.Stat(testkit.Approved(root, "i0001", "doing", id))
		return err == nil
	}
	cases := []struct {
		name    string
		edit    func(root string) func()
		answers []string
		only    []string
		code    int
		asked   int
		shown   bool
		placed  []string
		absent  []string
	}{
		{"改行を変えて、2 回目もはい", func(root string) func() {
			return rewrite(todo(root, "i0001-01-01"), func(b []byte) []byte { return bytes.ReplaceAll(b, []byte("\n"), []byte("\r\n")) })
		}, []string{"y", "y"}, nil, 0, 2, true, []string{"i0001", "i0001-01-01"}, nil},
		{"BOM を足して、2 回目はいいえ", func(root string) func() {
			return rewrite(todo(root, "i0001-01-01"), func(b []byte) []byte { return append([]byte("\xef\xbb\xbf"), b...) })
		}, []string{"y", "n"}, nil, 1, 2, true, nil, []string{"i0001", "i0001-01-01"}},
		{"変わらない", func(string) func() { return func() {} }, []string{"y"}, nil, 0, 1, false, []string{"i0001", "i0001-01-01"}, nil},
		{"指名した提案が消えて別の提案が増えた", func(root string) func() {
			return func() {
				if err := os.Remove(todo(root, "i0001-01-01")); err != nil {
					t.Fatal(err)
				}
				testkit.Put(t, todo(root, "i0001-01-02"), childText("i0001-01-02", "i0001"))
			}
		}, []string{"y", "y"}, []string{"i0001", "i0001-01-01"}, 1, 1, true, nil, []string{"i0001", "i0001-01-01", "i0001-01-02"}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			root := approveWorkspace(t)
			conf, _ := config.Load(root)
			answers := &editingAnswers{answers: c.answers, edit: c.edit(root)}
			var out, errs bytes.Buffer
			code, err := Approve(answers, &out, &errs, conf, root, c.only, "")
			if err != nil {
				t.Fatal(err)
			}
			said := out.String() + errs.String()
			if code != c.code || answers.asked != c.asked || strings.Contains(said, updatedNotice) != c.shown {
				t.Fatalf("終了コード %d・確認 %d 回:\n%s", code, answers.asked, said)
			}
			for _, id := range c.placed {
				if !copied(root, id) {
					t.Errorf("%s を承認しない", id)
				}
			}
			for _, id := range c.absent {
				if copied(root, id) {
					t.Errorf("%s を承認した", id)
				}
			}
		})
	}
}

// 見せ直したあとに承認するのは、表示し直した最新のバイト列。
func TestTerminalAgreeApprovesTheBytesShownAgain(t *testing.T) {
	defer via.Session(via.CLI, "")()
	root := approveWorkspace(t)
	conf, _ := config.Load(root)
	child := filepath.Join(testkit.Worktree(root, "i0001"), "wip", "proposals", "todo", "i0001-01-01.md")
	answers := &editingAnswers{answers: []string{"y", "y"}, edit: func() {
		testkit.Put(t, child, strings.ReplaceAll(childText("i0001-01-01", "i0001"), "\n", "\r\n"))
	}}
	var out, errs bytes.Buffer
	if code, err := Approve(answers, &out, &errs, conf, root, nil, ""); code != 0 || err != nil {
		t.Fatalf("%d %v\n%s%s", code, err, out.String(), errs.String())
	}
	if n := strings.Count(out.String(), "この 2 件を承認する場合は y"); n != 2 {
		t.Errorf("一覧を見せた回数 %d", n)
	}
	data, err := os.ReadFile(testkit.Approved(root, "i0001", "doing", "i0001-01-01"))
	if err != nil || !bytes.Contains(data, []byte("\r\n")) {
		t.Errorf("最新のバイト列を承認しない: %q %v", data, err)
	}
}
