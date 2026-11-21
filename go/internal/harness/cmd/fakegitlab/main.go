// fakegitlab は GitLab API v4 の模擬を手で起こす入口である（tests/manual/gitlab/fake_gitlab.py の写し）。
// `.ccnavi/scripts/ccnavi-review.sh` を実物なしで通すための的で、分かるのは sh が動くことだけ（返す形が実物と同じかは契約テストが見る）。
//
//	cd go && go run ./internal/harness/cmd/fakegitlab <state.json> <port>
//
// 状態は JSON ファイル 1 つ。最初は次の形で置く。
//
//	{"project": "demo/greeter", "seq": 100, "issues": [], "mrs": []}
//
// <port> に 0 を渡すと OS が空きポートを決める。決まったポートは、起動時に標準エラーへ出る 1 行
// `fake-gitlab listening on 127.0.0.1:<port> state=<state.json>` で分かる。受けた要求は標準エラーに
// `fake-gitlab <METHOD> <path>` で 1 行ずつ出る。対象のプロジェクトは origin を `http://127.0.0.1:<port>/demo/greeter.git` にし、
// push だけ手元の bare へ向ける（`git remote set-url --push`）。`GITLAB_TOKEN` に fakegitlab.Token を置くと、sh は curl の経路でここを呼ぶ。
// 時計は fakegitlab.DefaultSkew（3 分）進めてある。SIGINT・SIGTERM で止まる。
package main

import (
	"fmt"
	"io"
	"os"
	"os/signal"
	"strconv"
	"syscall"

	"ccnavi/internal/harness/fakegitlab"
)

func main() {
	if len(os.Args) != 3 {
		fmt.Fprintln(os.Stderr, "使い方: fakegitlab <state.json> <port>（port に 0 を渡すと OS が決める）")
		os.Exit(2)
	}
	stop := make(chan os.Signal, 1)
	signal.Notify(stop, os.Interrupt, syscall.SIGTERM)
	os.Exit(run(os.Args[1], os.Args[2], os.Stderr, stop))
}

// run は模擬を起こし、stop が来るまで待つ。終了コードを返す。
func run(statePath, port string, stderr io.Writer, stop <-chan os.Signal) int {
	n, err := strconv.Atoi(port)
	if err != nil || n < 0 || n > 65535 {
		fmt.Fprintf(stderr, "ポートが数でない: %q\n", port)
		return 2
	}
	srv, err := fakegitlab.New(fakegitlab.Config{StatePath: statePath, Skew: fakegitlab.DefaultSkew, Port: n, Echo: stderr})
	if err != nil {
		fmt.Fprintln(stderr, err)
		return 1
	}
	defer srv.Close()
	fmt.Fprintf(stderr, "fake-gitlab listening on 127.0.0.1:%d state=%s\n", srv.Port(), statePath)
	<-stop
	return 0
}
