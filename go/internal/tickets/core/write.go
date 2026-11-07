package core

import (
	"fmt"
	"io"
	"strings"

	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/tickets/proposal/agree"
)

// write は並べた書き込みをディスクに書く（Writer(FS)）。失敗の扱いは並べたときに添えた Policy で決まる。
//
//   - FailStop: Undo を消し Restore を戻し、`ccnavi: <識別子>: <理由>` を言って止める。置いたものは戻さない
//   - FailWarn: 同じ形で言って続ける
//   - FailLine: 行を出し、同じ組の残り（続く書き込みと行）を飛ばす
//   - FailQuiet: 何も出さずに続ける
//
// 並べる段で止まっていたら（Stopped）、並べた分を書いてから同じ形で言って止める。Call には同じ組で書けた Tag を
// 渡す（マーカーを消した行は、実際に消せた種類で書く）。
func write(stdout, stderr io.Writer, planned *agree.Planned) agree.Applied {
	placed := []string{}
	skipped := map[int]bool{}
	done := map[int]map[string]bool{}
	for _, item := range planned.Stage.Items {
		group := item.ItemGroup()
		if group != 0 && skipped[group] {
			continue
		}
		switch x := item.(type) {
		case fsio.Line:
			out := stdout
			if x.Stream == fsio.StreamErr {
				out = stderr
			}
			fmt.Fprintln(out, x.Text)
		case fsio.Call:
			for _, line := range x.Run(done[x.Group]) {
				fmt.Fprintln(stdout, line)
			}
		case *fsio.Op:
			rule := x.Policy
			failed := x.Apply()
			if failed == "" {
				if rule.Places != "" {
					placed = append(placed, rule.Places)
				}
				if rule.Tag != "" {
					if done[rule.Group] == nil {
						done[rule.Group] = map[string]bool{}
					}
					done[rule.Group][rule.Tag] = true
				}
				continue
			}
			message := rule.FailureText(failed)
			switch rule.OnFail {
			case fsio.FailStop:
				for _, path := range rule.Undo {
					fsio.Remove(path)
				}
				fsio.PutBack(rule.Restore)
				fmt.Fprintln(stderr, head(rule.Ticket)+message)
				return agree.Applied{Code: 1, Placed: placed, StoppedAt: rule.Ticket, Reason: message}
			case fsio.FailWarn:
				fmt.Fprintln(stderr, head(rule.Ticket)+message)
			case fsio.FailLine:
				fmt.Fprintf(stdout, "  %s\n", message)
				skipped[rule.Group] = true
			}
		}
	}
	if s := planned.Stopped; s != nil {
		fmt.Fprintln(stderr, head(s.Ticket)+s.Reason)
		return agree.Applied{Code: 1, Placed: placed, StoppedAt: s.Ticket, Reason: s.Reason}
	}
	return agree.Applied{Code: 0, Placed: placed}
}

func head(ticket string) string {
	if ticket == "" {
		return "ccnavi: "
	}
	return "ccnavi: " + ticket + ": "
}

// writeRecorded は write に加えて、実際に書けた・消せたパスをワークスペースルートからの相対（"/" 区切り）で返す。
// 承認済みチケット・マーカーと、承認で消した提案が入る。`ccnavi-user-push-approved.sh --path` はこの一覧だけを
// コミットして push する（同じ置き場のほかの変更を巻き込まない）。
func writeRecorded(stdout, stderr io.Writer, root string, planned *agree.Planned) (agree.Applied, []string) {
	recorder := fsio.StartRecording()
	applied := write(stdout, stderr, planned)
	return applied, writtenPaths(root, recorder.Stop())
}

// writtenPaths は書いたパス（絶対パス）をワークスペースルートからの相対にする。ルートの外は除く。
func writtenPaths(root string, written []string) []string {
	base := ospath.Realpath(root)
	out := []string{}
	for _, path := range written {
		rel, ok := ospath.Relpath(fsio.ParentResolved(path), base)
		if !ok || rel == ".." || strings.HasPrefix(rel, ".."+ospath.Sep) || ospath.IsAbs(rel) {
			continue
		}
		out = append(out, strings.ReplaceAll(rel, ospath.Sep, "/"))
	}
	return out
}
