package git

import (
	"encoding/json"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/exe"
	"ccnavi/internal/harness/run"
)

// find_marked（ccnavi-common-review.sh）。目印で自分の投稿を探す、送信の照合の読み取り。
// ホストの一覧はスタブ（ccnavi_host_pages を差し替える）で返し、実行ファイル（`ccnavi host marked`）の絞り込みを
// GitHub と GitLab の両方で通しで確かめる。
//   - 目印の一致だけを数える（別の段階・別の操作ID・目印の無い投稿は数えない）
//   - アカウントが分かれば、その人の投稿だけを数える
//   - GitHub の issue の一覧には PR も入るので除く
//   - 一覧を上限で打ち切ったら no、読めなければ error

const findMarkedHarness = `
. "$(dirname "$0")/ccnavi-common.sh"
review_label=t
ccnavi_load ccnavi-common-review.sh
JQ="$JQ_BIN"
state="$STATE"
ccnavi() { "$CCNAVI_EXE" "$@"; }
kind="$KIND"
path="o/r"
encoded_path() { printf 'o%%2Fr'; }
ccnavi_host_encode() { printf '%s' "$1"; }
ccnavi_host_pages() {
	printf '%s' "$(cat "$LIST")"
	return "${LIST_RC:-0}"
}
find_marked "$@"
`

const markedOp = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

func marker(stage, op string) string {
	return "本文\n<!-- ccnavi:op=" + op + " stage=" + stage + " -->\n"
}

type obj = map[string]any

func TestFindMarked(t *testing.T) {
	t.Parallel()
	need(t, "sh", "jq")
	jq, _ := exec.LookPath("jq")
	otherOp := strings.Repeat("b", 32)
	mark := func(stage string) string { return marker(stage, markedOp) }
	ghIssues := []obj{
		{"html_url": "https://h/o/r/issues/5", "body": mark("issue"), "user": obj{"login": "me"}},
		{"html_url": "https://h/o/r/issues/6", "body": mark("note"), "user": obj{"login": "me"}},
		{"html_url": "https://h/o/r/issues/7", "body": marker("issue", otherOp), "user": obj{"login": "me"}},
		{"html_url": "https://h/o/r/issues/8", "body": mark("issue"), "user": obj{"login": "other"}},
		{"html_url": "https://h/o/r/pull/9", "body": mark("issue"), "user": obj{"login": "me"}, "pull_request": obj{}},
		{"html_url": "https://h/o/r/issues/10", "body": "目印なし", "user": obj{"login": "me"}},
	}
	ghComments := []obj{
		{"html_url": "https://h/c/1", "body": mark("note"), "user": obj{"login": "me"}},
		{"html_url": "https://h/c/2", "body": mark("note"), "user": obj{"login": "other"}},
	}
	glIssues := []obj{
		{"web_url": "https://g/i/1", "description": mark("issue"), "author": obj{"username": "me"}},
		{"web_url": "https://g/i/2", "description": mark("issue"), "author": obj{"username": "x"}},
	}
	glNotes := []obj{{"id": 31, "body": mark("note"), "description": "", "author": obj{"username": "me"}}}
	one := ghComments[:1]
	for _, tc := range []struct {
		name  string
		kind  string
		items []obj
		args  []string
		rc    int
		want  string
	}{
		{"GitHub の issue は目印と作成者が合うものだけ数え、PR を除く", "github", ghIssues, []string{"issue", markedOp, "issue", "7", "me", "https://h/o/r/pull/7"}, 0, "yes https://h/o/r/issues/5"},
		{"GitHub のコメントはアカウントで絞る", "github", ghComments, []string{"note", markedOp, "note", "7", "me", ""}, 0, "yes https://h/c/1"},
		{"アカウントが分からなければ目印だけで数える", "github", ghComments, []string{"note", markedOp, "note", "7", "", ""}, 0, "yes https://h/c/1,https://h/c/2"},
		{"GitLab の issue は description を読む", "gitlab", glIssues, []string{"issue", markedOp, "issue", "7", "me", "https://g/mr/7"}, 0, "yes https://g/i/1"},
		{"GitLab の MR のメモは body を読み MR の URL に note を付ける", "gitlab", glNotes, []string{"note", markedOp, "note", "7", "me", "https://g/mr/7"}, 0, "yes https://g/mr/7#note_31"},
		{"GitLab の issue のメモは issue の URL に note を付ける", "gitlab", glNotes, []string{"issue-note", markedOp, "note", "3", "me", "https://g/i/3"}, 0, "yes https://g/i/3#note_31"},
		{"全部読んで見つからなければ URL なしの yes", "gitlab", []obj{}, []string{"note", markedOp, "note", "7", "me", "u"}, 0, "yes"},
		{"上限で打ち切った一覧は no で、見つけた分を返す", "github", one, []string{"note", markedOp, "note", "7", "me", ""}, 2, "no https://h/c/1"},
		{"読めない一覧は error", "github", []obj{}, []string{"note", markedOp, "note", "7", "me", ""}, 1, "error"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			dir := base.TempDir(t)
			scripts := filepath.Join(dir, "scripts")
			copyInto(t, scripts, "ccnavi-common-review.sh")
			harness := write(t, filepath.Join(scripts, "harness.sh"), findMarkedHarness)
			data, err := json.Marshal(tc.items)
			if err != nil {
				t.Fatal(err)
			}
			listing := write(t, filepath.Join(dir, "list.json"), string(data))
			r := run.Sh(t, harness, tc.args, run.Opts{Env: map[string]string{
				"JQ_BIN": jq, "STATE": dir, "CCNAVI_EXE": exe.Path(t), "KIND": tc.kind, "LIST": listing, "LIST_RC": strconv.Itoa(tc.rc),
			}})
			if r.Code != 0 {
				t.Fatalf("code = %d: %s", r.Code, r.Stderr)
			}
			if got := strings.TrimSpace(r.Stdout); got != tc.want {
				t.Errorf("find_marked = %q, want %q", got, tc.want)
			}
		})
	}
}
