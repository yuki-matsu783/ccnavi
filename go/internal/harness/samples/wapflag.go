package samples

import (
	"bufio"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strings"
	"sync/atomic"
)

// sh（write-and-push）を通らずに実行ファイルを直に起こすテストが、状態の操作に付ける印（tests/write_and_push_flag.py の写し）。
//
// origin のある親子のチケットの状態の操作（ticket start|finish|cancel|approval-request、review requested|confirm|ready、
// `--reviewed N --accept-unresolved`）は、write-and-push の sh が --record-writes と --record-tree を付けて起こす。
// 実行ファイルは印の無い呼び出しを断る。sh を通さないテストは、同じ印を付けて呼ぶ。

var wapCount atomic.Int64

var stateOps = [][2]string{
	{"ticket", "start"}, {"ticket", "finish"}, {"ticket", "cancel"},
	{"review", "requested"}, {"review", "confirm"}, {"review", "ready"},
}

// AsWriteAndPush は状態の操作で、root に origin があれば印を付けた引数を返す。そうでなければそのまま返す。
func AsWriteAndPush(root, state, parentTree string, args []string) []string {
	isStateOp := false
	for i := 0; i+1 < len(args); i++ {
		pair := [2]string{args[i], args[i+1]}
		if slices.Contains(stateOps, pair) || (pair == [2]string{"user", "reviewed"} &&
			slices.Contains(args, "--accept-unresolved") && !slices.Contains(args, "--preview")) {
			isStateOp = true
		}
	}
	if !isStateOp || slices.Contains(args, "--record-tree") || !hasOrigin(root) {
		return args
	}
	place := filepath.Join(state, "write-and-push", "self")
	if err := os.MkdirAll(place, 0o755); err != nil {
		panic(err)
	}
	target := filepath.Join(place, fmt.Sprintf("as-write-and-push-%d.writes", wapCount.Add(1)-1))
	return slices.Concat([]string{"--record-writes", target, "--record-tree", parentTree}, args)
}

// hasOrigin は root に origin の URL があるか。テストの木（.git がディレクトリ）は設定ファイルを直に読み、git を起こさない。
// それ以外は git に聞く。
func hasOrigin(root string) bool {
	f, err := os.Open(filepath.Join(root, ".git", "config"))
	if err != nil {
		out, err := exec.Command("git", "-C", root, "config", "--get", "remote.origin.url").Output()
		return err == nil && strings.TrimSpace(string(out)) != ""
	}
	defer f.Close()
	section := ""
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if strings.HasPrefix(line, "[") {
			section = line
			continue
		}
		if key, value, _ := strings.Cut(line, "="); section == `[remote "origin"]` && strings.TrimSpace(key) == "url" {
			return strings.TrimSpace(value) != ""
		}
	}
	return false
}
