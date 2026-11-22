package retire

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/tickets/testkit"
)

// 見本（retire.json）が持たない場面の表。確かめられない状態では何も消さないこと、大文字小文字だけ違う名前を同じものと
// 扱うこと、children が配列でない記録からは何も消さないこと。fixture は golden_test.go と同じ。

// noted は fixture に片付けの記録を書いた状態。
type noted struct {
	root, parent, head string
	conf               *config.Settings
	record             string
}

func newNoted(t *testing.T) *noted {
	t.Helper()
	root, head := fixture(t)
	conf := testkit.Settings(t, root)
	parent := testkit.Worktree(root, "i0001")
	if why := Note(conf, root, "i0001", "", parent, 7); why != "" {
		t.Fatal(why)
	}
	return &noted{root, parent, head, conf, filepath.Join(conf.State, "retire", "i0001.json")}
}

func (n *noted) branches(t *testing.T) []string {
	t.Helper()
	return strings.Fields(testkit.Git(t, n.root, "branch", "--format=%(refname:short)"))
}

func (n *noted) exists(path string) bool {
	_, err := os.Lstat(path)
	return err == nil
}

func (n *noted) merged(branch, sha string) (int, string) {
	var o bytes.Buffer
	code := RunMerged(&o, n.conf, n.root, "i0001", "", branch, sha, n.root)
	return code, o.String()
}

func (n *noted) retire(name string) (int, string, string) {
	var o, e bytes.Buffer
	code := RunRetire(&o, &e, n.conf, n.root, name, n.root)
	return code, o.String(), e.String()
}

func (n *noted) branch(name string, confirmed bool) (int, string, string) {
	var o, e bytes.Buffer
	code := RunBranch(&o, &e, n.conf, n.root, name, confirmed)
	return code, o.String(), e.String()
}

// addCancelled は記録に取り消した子を足し、ブランチと退避済みのチケット（`worktree branch` が親を確かめ直す）を作る。
func (n *noted) addCancelled(t *testing.T, name, parent string) {
	t.Helper()
	testkit.Git(t, n.root, "branch", name, "main")
	held := recordMap(t, n.record)
	held["children"] = append(held["children"].([]any), map[string]any{
		"ticket": name, "kind": "cancelled", "merged_sha": "", "project": ""})
	writeRecord(t, n.record, held)
	text := "---\nversion: 1\nticket: " + name + "\nparent: " + parent + "\nphase: 1\ntitle: t\n" +
		"cancelled_at: 2026-10-01T00:00:00+09:00\n---\n"
	testkit.Put(t, filepath.Join(n.root, "logs", "archive", "self", "done", name+".md"), text)
}

func lastLine(text string) string {
	lines := strings.Split(strings.TrimRight(text, "\n"), "\n")
	return lines[len(lines)-1]
}

func TestMergedKeepsWhatItCannotCheck(t *testing.T) {
	const unknownSHA = "0123456789abcdef0123456789abcdef01234567"
	for _, c := range []struct {
		name  string
		setup func(t *testing.T, n *noted)
		sha   func(t *testing.T, n *noted) string
		want  []string
		deny  []string // 出力に出てはいけない文
	}{
		{"親に未コミットの変更", func(t *testing.T, n *noted) { testkit.Put(t, filepath.Join(n.parent, "memo.txt"), "書きかけ\n") },
			nil, []string{"未コミットの変更があるので、何も削除しませんでした", "チェックアウトしている"}, nil},
		{"手元に無い先頭は推測しない", nil,
			func(*testing.T, *noted) string { return unknownSHA }, []string{"手元に無く、含まれるか確かめられない"}, nil},
		{"push の無いブランチ", func(t *testing.T, n *noted) {
			testkit.Git(t, n.root, "update-ref", "-d", "refs/remotes/origin/i0001")
		}, nil, []string{"手元に無く、未 push のコミットが無いか確かめられない"}, nil},
		{"未 push のコミットがあるとき、ブランチ自身の先頭を渡しても消さない", func(t *testing.T, n *noted) {
			testkit.Git(t, n.parent, "commit", "-q", "--allow-empty", "-m", "未 push")
		}, func(t *testing.T, n *noted) string {
			return strings.TrimSpace(testkit.Git(t, n.parent, "rev-parse", "HEAD"))
		},
			[]string{"未 push のコミットがある", "origin/i0001"}, nil},
		{"未 push の先頭を持つ親は、push 済みの先頭を渡してもワークツリーを消さない", func(t *testing.T, n *noted) {
			testkit.Git(t, n.parent, "commit", "-q", "--allow-empty", "-m", "未 push")
		}, nil, []string{"未 push のコミットがある", "branch -D i0001", "ワークツリーは、ブランチが片付けの条件を満たさないので消しません"},
			[]string{"ワークツリー i0001 を削除しました"}},
		{"HEAD が detached", func(t *testing.T, n *noted) { testkit.Git(t, n.parent, "checkout", "-q", "--detach") },
			nil, []string{"HEAD が記録のブランチ i0001 を指していない"}, []string{"ブランチ i0001 を削除しました"}},
		{"HEAD が別のブランチ", func(t *testing.T, n *noted) {
			testkit.Git(t, n.parent, "checkout", "-q", "-b", "elsewhere")
			testkit.Git(t, n.parent, "commit", "-q", "--allow-empty", "-m", "別のブランチの成果")
		}, nil, []string{"HEAD が記録のブランチ i0001 を指していない"}, []string{"ブランチ i0001 を削除しました"}},
		{"ブランチの参照が消えた", func(t *testing.T, n *noted) {
			testkit.Git(t, n.root, "update-ref", "-d", "refs/heads/i0001")
		}, nil, []string{"HEAD が記録のブランチ i0001 を指していない"}, []string{"ブランチ i0001 を削除しました"}},
	} {
		t.Run(c.name, func(t *testing.T) {
			n := newNoted(t)
			if c.setup != nil {
				c.setup(t, n)
			}
			sha := n.head
			if c.sha != nil {
				sha = c.sha(t, n)
			}
			code, out := n.merged("i0001", sha)
			if code != 0 || lastLine(out) != "record no" {
				t.Errorf("code=%d\n%s", code, out)
			}
			for _, w := range c.want {
				if !strings.Contains(out, w) {
					t.Errorf("%q が無い\n%s", w, out)
				}
			}
			for _, d := range c.deny {
				if strings.Contains(out, d) {
					t.Errorf("%q が出た\n%s", d, out)
				}
			}
			if !n.exists(n.parent) || !n.exists(n.record) {
				t.Errorf("消してはいけないものが消えた\n%s", out)
			}
			// 確かめられないなら、未 push の成果を持つブランチも消さない（参照が無い場面だけは、消す物が無い）。
			if c.name != "ブランチの参照が消えた" && !slices.Contains(n.branches(t), "i0001") {
				t.Errorf("ブランチ i0001 が消えた\n%s", out)
			}
		})
	}
}

func TestMergedKeepsOnABadSHA(t *testing.T) {
	n := newNoted(t)
	if _, out := n.merged("i0001", "not-a-sha"); lastLine(out) != "record no" || !slices.Contains(n.branches(t), "i0001") {
		t.Errorf("%s", out)
	}
}

func TestRetireKeepsUnverifiableChildren(t *testing.T) {
	for _, c := range []struct {
		name  string
		setup func(t *testing.T, n *noted)
		want  []string
	}{
		{"push 済みの親に含まれない", func(t *testing.T, n *noted) {
			testkit.Git(t, n.root, "update-ref", "refs/remotes/origin/i0001", "main")
		}, []string{"push 済みの親に含まれない"}},
		{"子のワークツリーが汚れている", func(t *testing.T, n *noted) {
			testkit.Put(t, filepath.Join(testkit.Worktree(n.root, "i0001-01-01"), "memo.txt"), "書きかけ\n")
		}, []string{"未コミットの変更があるので、何も削除しませんでした", "チェックアウトしている"}},
	} {
		t.Run(c.name, func(t *testing.T) {
			n := newNoted(t)
			c.setup(t, n)
			code, out, errText := n.retire("i0001")
			if code != 1 {
				t.Errorf("code=%d\n%s%s", code, out, errText)
			}
			// 汚れた子のワークツリーは、未コミットの書きかけごと残る。
			if child := testkit.Worktree(n.root, "i0001-01-01"); c.name == "子のワークツリーが汚れている" &&
				(!n.exists(filepath.Join(child, "memo.txt")) || !strings.Contains(out, "git -C '"+child+"' status")) {
				t.Errorf("汚れた子のワークツリーが消えたか、案内が無い: %s\n%s", child, out)
			}
			for _, w := range c.want {
				if !strings.Contains(out, w) {
					t.Errorf("%q が無い\n%s", w, out)
				}
			}
			if !slices.Contains(n.branches(t), "i0001-01-01") {
				t.Errorf("合流した子のブランチが消えた: %v", n.branches(t))
			}
		})
	}
}

func TestBranchRefusesAChildOfAnotherParent(t *testing.T) {
	n := newNoted(t)
	n.addCancelled(t, "i0002-01-01", "i0002")
	n.addCancelled(t, "i0001-01-09", "i0001")
	testkit.Put(t, filepath.Join(n.conf.State, "retire", "i0009.json"), "{broken")
	testkit.Put(t, filepath.Join(n.conf.State, "retire", "i0000.json"), `{"parent": "i0000", "children": null}`)
	code, _, errText := n.branch("i0002-01-01", true)
	if code != 1 || !strings.Contains(errText, "記録を信じて消さない") {
		t.Errorf("code=%d %s", code, errText)
	}
	if !slices.Contains(n.branches(t), "i0002-01-01") {
		t.Error("別の親の子のブランチが消えた")
	}
	// 壊れた別の親の記録があっても、この親の子の片付けは止まらない（取り消した子は確認があれば消す）。
	code, out, errText := n.branch("i0001-01-09", true)
	if code != 0 || !strings.Contains(out, "ブランチ i0001-01-09 を削除しました") {
		t.Errorf("code=%d %s%s", code, out, errText)
	}
}

func TestNamesThatDifferOnlyInCaseMeanTheSame(t *testing.T) {
	n := newNoted(t)
	// ワークツリーが残っている間は、別の綴りでも記録を消さない。
	var o, e bytes.Buffer
	if code := RunAbandon(&o, &e, n.conf, n.root, "I0001"); code != 1 || !n.exists(n.record) {
		t.Errorf("code=%d %s%s", code, o.String(), e.String())
	}
	// 別の綴りの記録のファイルも、親が合えば列挙する。
	upper := filepath.Join(filepath.Dir(n.record), "I0001.json")
	if err := os.Rename(n.record, upper); err != nil {
		t.Fatal(err)
	}
	got := Entries(n.conf)
	if len(got) != 1 || got[0] != (Entry{Parent: "I0001", Repo: "self", MR: "7", Branch: "i0001"}) {
		t.Errorf("%+v", got)
	}
	if err := os.Rename(upper, n.record); err != nil {
		t.Fatal(err)
	}
	// 子のブランチは、大文字小文字だけ違う名前でも見つかる（取り消した子は確認つき）。
	n.addCancelled(t, "i0001-01-09", "i0001")
	if code, out, errText := n.branch("I0001-01-09", true); code != 0 || !strings.Contains(out, "ブランチ i0001-01-09 を削除しました") {
		t.Errorf("code=%d %s%s", code, out, errText)
	}
	// 記録の parent が大文字小文字だけ違っても、この親の記録として子のブランチを見つける。
	n.addCancelled(t, "i0001-01-08", "i0001")
	held := recordMap(t, n.record)
	held["parent"] = "I0001"
	writeRecord(t, n.record, held)
	if code, out, errText := n.branch("i0001-01-08", true); code != 0 || !strings.Contains(out, "ブランチ i0001-01-08 を削除しました") {
		t.Errorf("code=%d %s%s", code, out, errText)
	}
	held["parent"] = "i0001"
	writeRecord(t, n.record, held)
	// 親のワークツリーを片付けた後は、別の綴りでも同じ記録を放棄できる。
	if code, _, errText := n.retire("I0001"); code != 1 || n.exists(n.parent) {
		t.Errorf("code=%d parent=%v %s", code, n.exists(n.parent), errText)
	}
	o.Reset()
	e.Reset()
	if code := RunAbandon(&o, &e, n.conf, n.root, "I0001"); code != 0 || n.exists(n.record) {
		t.Errorf("code=%d %s%s", code, o.String(), e.String())
	}
}

// children が配列でない記録は壊れた記録で、何も消さない（壊れ方ごとの場面は持たない）。
func TestBrokenChildrenRemoveNothing(t *testing.T) {
	n := newNoted(t)
	held := recordMap(t, n.record)
	held["children"] = 5
	writeRecord(t, n.record, held)
	code, _, errText := n.retire("i0001")
	if code != 1 || !strings.Contains(errText, "children が配列ではない") {
		t.Errorf("code=%d %s", code, errText)
	}
	for _, name := range []string{"i0001", "i0001-01-01"} {
		if !n.exists(testkit.Worktree(n.root, name)) {
			t.Errorf("%s のワークツリーが消えた", name)
		}
	}
	code, out := n.merged("i0001", n.head)
	if code != 0 || lastLine(out) != "record no" || !n.exists(n.record) || !strings.Contains(out, "片付けの記録を読めない") {
		t.Errorf("code=%d\n%s", code, out)
	}
}

func recordMap(t *testing.T, path string) map[string]any {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var out map[string]any
	if err := json.Unmarshal(data, &out); err != nil {
		t.Fatal(err)
	}
	return out
}

func writeRecord(t *testing.T, path string, held map[string]any) {
	t.Helper()
	data, err := json.Marshal(held)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, data, 0o644); err != nil {
		t.Fatal(err)
	}
}
