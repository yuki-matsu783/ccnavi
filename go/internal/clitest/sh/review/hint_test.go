package review

import (
	"strings"
	"testing"

	"ccnavi/internal/harness/exe"
	"ccnavi/internal/harness/run"
)

// GraphQL が制限されている環境で REST にも失敗したときの案内（REST の代わりの道の案内そのものは restfallback_test.go の
// TestWhenRestFailsTooTheHintDoesNotAskForAJSON が見る）。案内の出る条件を広げないための確認。
//
//   - 案内は、ユーザに JSON を作らせて `ccnavi review confirm --result` を打たせる手順を書かない。その手順は write-and-push の検査で
//     止まる（`--record-tree` が付かないため）。書くと、拒否される手順を案内してしまう
//   - GraphQL 以外の失敗では案内を出さない（閉じたポートへ実際に打つ）
//   - 道具が無い失敗の文面も、JSON を作らせる手順を書かない
//
// 実際の GitHub の GraphQL を失敗させる環境は作れない。`remote_kind` はホスト名で github と読むため、127.0.0.1 に立てた偽物は gitlab として
// 読まれる。GraphQL の 403 は hostfixture の GitHub のスタブで作る。

// refusedProcedure は案内に書かない語（実行ファイルに JSON を直接渡す手順は、ユーザが打っても止まる）。
var refusedProcedure = []string{"ccnavi review confirm", "--result", "MCP", "fetch_all"}

func TestAHostFailureThatIsNotGraphQLDoesNotPrintTheHint(t *testing.T) {
	t.Parallel()
	s := newMarkerSite(t, "main")
	// 閉じたポート。最初の API 呼び出し（マージリクエストを探す GET）で失敗する
	s.git("remote", "add", "origin", "http://127.0.0.1:9/root/p.git")
	done := s.real(reviewSh, run.Opts{Env: map[string]string{"CCNAVI_ENTRY_PATH": exe.ShPath(t), "GITLAB_TOKEN": "x", "GITHUB_TOKEN": "x"}}, "fetch")
	if done.Code == 0 || !strings.Contains(done.Stderr, "が失敗した") || strings.Contains(done.Stderr, "MCP") {
		t.Errorf("code = %d\n%s%s", done.Code, done.Stdout, done.Stderr)
	}
}

func TestTheNoTransportMessageDoesNotAskForAJSONEither(t *testing.T) {
	t.Parallel()
	s := newMarkerSite(t, "main")
	s.git("remote", "add", "origin", "https://gitlab.com/o/r.git")
	path := pathWithout(t, s.tmp, "curl", "gh", "glab")
	done := s.real(reviewSh, run.Opts{Env: map[string]string{"CCNAVI_ENTRY_PATH": exe.ShPath(t), "PATH": path}}, "fetch")
	if done.Code != 2 || !strings.Contains(done.Stderr, "curl もありません") {
		t.Fatalf("code = %d\n%s%s", done.Code, done.Stdout, done.Stderr)
	}
	for _, word := range refusedProcedure {
		if strings.Contains(done.Stderr, word) {
			t.Errorf("案内に %q がある:\n%s", word, done.Stderr)
		}
	}
}
