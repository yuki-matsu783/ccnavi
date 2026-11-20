package ops

import (
	"fmt"
	"os"
	"slices"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/tree"
	"ccnavi/internal/records/reads"
	"ccnavi/internal/tickets/approval"
	"ccnavi/internal/tickets/model/ticket"
)

// readFilesListed はチケットに載せる件数の上限。残りは件数だけ書く。
const readFilesListed = 100

// noteReadFiles は、チケットのワークツリーで作業したセッションと finish を打ったセッションのコンテキストに乗った
// ファイルを、チケットの本文の最後にセッションごとに書く。記録が無い・書けないときは何も言わずに飛ばす（記録は補助で、
// 閉じる操作を止めない）。
func noteReadFiles(conf *config.Settings, root string, found *ticket.Ticket) {
	sessions := reads.Sessions(conf.State, tree.WorktreePath(root, found.ID))
	if current := os.Getenv(env.ClaudeSessionID); current != "" && !slices.Contains(sessions, current) {
		sessions = append(sessions, current)
	}
	for _, session := range sessions {
		items := readFileLines(reads.Listed(conf.State, session, root, conf.Projects))
		approval.NoteReadFiles(found.Path, session, items)
	}
}

// readFileLines は記録を節の項目の行にする（バッククォートで囲んだパスと、ツールごとの回数 `Read 3・Bash 1`）。上限を超えたぶんは件数の 1 行にまとめる。
func readFileLines(listed []reads.Item) []string {
	var out []string
	for i, item := range listed {
		if i == readFilesListed {
			out = append(out, fmt.Sprintf("ほか %d 件", len(listed)-readFilesListed))
			break
		}
		out = append(out, "`"+item.Rel+"`（"+toolCounts(item.Tools)+"）")
	}
	return out
}

// toolCounts はツールごとの回数を、多い順（同じなら名前の順）に `Read 3・Bash 1` の形で並べる。
func toolCounts(tools map[string]int) string {
	names := make([]string, 0, len(tools))
	for name := range tools {
		names = append(names, name)
	}
	slices.SortFunc(names, func(a, b string) int {
		if tools[a] != tools[b] {
			return tools[b] - tools[a]
		}
		return strings.Compare(a, b)
	})
	parts := make([]string, 0, len(names))
	for _, name := range names {
		parts = append(parts, fmt.Sprintf("%s %d", name, tools[name]))
	}
	return strings.Join(parts, "・")
}
