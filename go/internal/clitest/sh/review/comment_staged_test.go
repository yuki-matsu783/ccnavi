package review

import (
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"ccnavi/internal/harness/base"
	"ccnavi/internal/harness/run"
	"ccnavi/internal/harness/shkit"
)

// `comment_staged`（ccnavi-common-review.sh）。エージェントの単発の投稿を操作記録に載せて送る。
// `ccnavi`・`comment`・`find_marked`・`account` はスタブに差し替え（答えは 1 行 1 項目）、分岐だけを確かめる。
//
//   - 記録を作れなければ送らず止まる
//   - 送り済みなら送らない
//   - 送るときは本文の末尾に目印を足し、pending → sent を記録する
//   - 前に試した形跡があれば、先に照合して、確定できなければ送らず 1 で止まる

const stagedHarness = `
. "$(dirname "$0")/ccnavi-common.sh"
review_label=t
ccnavi_load ccnavi-common-review.sh
JQ="$JQ_BIN"
state="$STATE"
branch=feature
ccnavi() {
	if [ "$1 $2" = "host sent" ]; then printf 'url %s\n' "$4"; return 0; fi
	[ "$1" = ops ] || return 1
	shift
	printf '%s\n' "$*" >>"$STATE/ops.log"
	case "$1" in
	begin) [ -z "${NO_RECORD:-}" ] || return 1; printf '%s\n' "$OPID" ;;
	get) printf 'stage comment\ncomment.state %s\ncomment.url u0\ncomment.tries %s\n' "$ST" "$TRIES" ;;
	reconcile) printf '%s\n' "$VERDICT" ;;
	*) : ;;
	esac
}
comment() {
	cat "$3" >"$STATE/posted.md"
	[ -z "${POST_FAIL:-}" ] || return 1
	printf 'url https://h/c/1\nauthor me\n'
}
create_issue() {
	cat "$1" >"$STATE/issue.md"
	printf 'url https://h/o/r/issues/5\nnumber 5\n'
}
find_marked() { printf 'yes %s\n' "${FOUND:-}"; }
account() { printf 'me'; }
"${FUNC:-comment_staged}" "$@"
`

var opID = strings.Repeat("a", 32)

// stagedRun は harness.sh に args を渡して起こす。env は既定（記録は pending・試行 0・照合は送る）への差し替え。
func stagedRun(t *testing.T, env map[string]string, args ...string) (res run.Result, dir string) {
	t.Helper()
	jq, err := exec.LookPath("jq")
	if err != nil {
		t.Skip("jq が無い")
	}
	shkit.Need(t, "sh")
	dir = base.TempDir(t)
	src := base.ScriptsDir()
	scripts := filepath.Join(dir, "scripts")
	for _, name := range append(base.CommonSh(t, src), commonRevSh) {
		shkit.Write(t, filepath.Join(scripts, name), shkit.Read(t, filepath.Join(src, name)))
	}
	harness := shkit.Write(t, filepath.Join(scripts, "harness.sh"), stagedHarness)
	shkit.Write(t, filepath.Join(dir, "body.md"), "本文\n")
	all := map[string]string{"JQ_BIN": filepath.ToSlash(jq), "STATE": dir, "OPID": opID, "ST": "pending", "TRIES": "0", "VERDICT": "send "}
	for k, v := range env {
		all[k] = v
	}
	if len(args) == 0 {
		args = []string{"comment", "k", "7", "https://h/mr/7", filepath.Join(dir, "body.md")}
	} else {
		args[len(args)-1] = filepath.Join(dir, "body.md")
	}
	return run.Sh(t, harness, args, run.Opts{Env: all}), dir
}

func TestCommentStagedSendsWithAMarkerAndRecordsTheStages(t *testing.T) {
	t.Parallel()
	done, dir := stagedRun(t, nil)
	if done.Code != 0 || !strings.Contains(done.Stdout, "https://h/c/1") {
		t.Fatalf("code = %d\n%s%s", done.Code, done.Stdout, done.Stderr)
	}
	if got, want := readIf(filepath.Join(dir, "posted.md")), "本文\n\n<!-- ccnavi:op="+opID+" stage=comment -->\n"; got != want {
		t.Errorf("投稿 = %q, want %q", got, want)
	}
	log := readIf(filepath.Join(dir, "ops.log"))
	for _, want := range []string{"stage-set " + opID + " comment pending", "comment sent https://h/c/1 me"} {
		if !strings.Contains(log, want) {
			t.Errorf("操作記録の呼び出しに %q が無い:\n%s", want, log)
		}
	}
}

func TestCommentStagedCreatesACloseEarlyIssueWithAMarker(t *testing.T) {
	t.Parallel()
	done, dir := stagedRun(t, map[string]string{"FUNC": "staged_send"}, "close-early", "fam", "issue", "issue", "7", "u", "body")
	if done.Code != 0 || !strings.Contains(done.Stdout, "issues/5") {
		t.Fatalf("code = %d\n%s%s", done.Code, done.Stdout, done.Stderr)
	}
	if !strings.Contains(readIf(filepath.Join(dir, "issue.md")), "stage=issue -->") {
		t.Errorf("issue の本文に段階の目印が無い: %q", readIf(filepath.Join(dir, "issue.md")))
	}
	if log := readIf(filepath.Join(dir, "ops.log")); !strings.Contains(log, "issue sent https://h/o/r/issues/5") {
		t.Errorf("ops.log = %q", log)
	}
}

func TestCommentStagedDoesNotPostWhenItCannotBeSure(t *testing.T) {
	t.Parallel()
	cases := []struct {
		name       string
		env        map[string]string
		code       int
		posted     bool
		out, err   string // 標準出力・標準エラーに含まれる語
		opsLogWord string
	}{
		{name: "記録を作れなければ送らずに止まる", env: map[string]string{"NO_RECORD": "1"}, code: 1, err: "操作記録を作れなかった"},
		{name: "送り済みなら送り直さない", env: map[string]string{"ST": "sent"}, out: "u0"},
		{name: "前に試した形跡は先に照合し、ホストに在れば送らない", env: map[string]string{"TRIES": "1", "VERDICT": "sent", "FOUND": "https://h/c/9"}, out: "https://h/c/9"},
		{name: "送ったか確定できなければ送らずに止まる", env: map[string]string{"ST": "unknown", "VERDICT": "unknown 作成直後"}, code: 1, opsLogWord: "comment unknown"},
		{name: "照合できなければ記録を書かず、送らずに止まる", env: map[string]string{"TRIES": "1", "VERDICT": "stop 一覧を読めない"}, code: 1, err: "照合できない"},
		{name: "投稿が失敗したら unknown と記録して止まる", env: map[string]string{"POST_FAIL": "1"}, code: 1, posted: true, opsLogWord: "comment unknown"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			done, dir := stagedRun(t, c.env)
			if done.Code != c.code || !strings.Contains(done.Stdout, c.out) || !strings.Contains(done.Stderr, c.err) {
				t.Errorf("code = %d, stdout = %q, stderr = %q", done.Code, done.Stdout, done.Stderr)
			}
			if posted := shkit.Exists(filepath.Join(dir, "posted.md")); posted != c.posted {
				t.Errorf("投稿した = %v, want %v", posted, c.posted)
			}
			if strings.Contains(c.name, "記録を書かず") {
				if log := readIf(filepath.Join(dir, "ops.log")); strings.Contains(log, "stage-set") {
					t.Errorf("記録を書かないはずが、ops.log = %q", log)
				}
				return
			}
			if !strings.Contains(readIf(filepath.Join(dir, "ops.log")), c.opsLogWord) {
				t.Errorf("ops.log = %q に %q が無い", readIf(filepath.Join(dir, "ops.log")), c.opsLogWord)
			}
		})
	}
}
