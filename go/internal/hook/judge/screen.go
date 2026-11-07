package judge

import (
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/shell"
	"ccnavi/internal/records/audit"
)

// layer は、実行役のコマンド（env・sudo・sh -c など）が中で実行するコマンド 1 つ。cd で移った先から見たパスは
// runner が shell.Moved で持つ。どれも止める側のルールにだけ当てる。
type layer struct {
	runner string
	text   string
	// quoted は引用の中から切り出したコマンドの層か（文の断りに使う）。
	quoted bool
}

// screen はシェルのコマンドを、実際に実行される部分まで絞る。返すのは次の 4 つ。ルールを当てる読み、引用の中で切り出した
// コマンドを除いた読み（文の断りを決めるだけに使う）、中で実行されるコマンドの層、書き直しを求める形。
// ルールはコマンドについて書かれたものなので、git push を引用した文書は push として扱わない。読み切れないコマンドは
// 生の文字列のまま当てる（実行される部分はすべて含まれる）。その事実は record.Degraded に残す。Bash 以外は読まない。
func screen(tool, subject string, record *audit.Record) (string, string, []layer, []shell.Rewrite) {
	if tool != config.ToolBash {
		return subject, subject, nil, nil
	}
	reading := shell.Read(subject)
	var inner []layer
	if reading.Unwrapped != "" {
		for i, text := range strings.Split(reading.Unwrapped, shell.Sep) {
			l := reading.Layers[i]
			inner = append(inner, layer{runner: l.Runner, text: text, quoted: l.Quoted})
		}
	}
	// cd で移った先から見たパスも、同じ層の並びに足す。止める側にしか当たらないので、cd を読み違えても
	// 当たるはずのものが当たらなくなることはない。
	for _, command := range strings.Split(reading.Moved, shell.Sep) {
		if command != "" {
			inner = append(inner, layer{runner: shell.Moved, text: command})
		}
	}
	if reading.Degraded {
		record.Degraded = string(reading.Reason)
		return subject, subject, inner, reading.Rewrites
	}
	return reading.Text, reading.Bare, inner, reading.Rewrites
}

// joinLayers は層のコマンドを sep でつなぐ。
func joinLayers(inner []layer, sep string) string {
	texts := make([]string, len(inner))
	for i, l := range inner {
		texts[i] = l.text
	}
	return strings.Join(texts, sep)
}

// lineLayers は層のコマンドを、コマンドの区切りを改行にして並べる（書き込み先を読む検査に渡す形）。
func lineLayers(inner []layer) []string {
	out := make([]string, len(inner))
	for i, l := range inner {
		out[i] = strings.ReplaceAll(l.text, shell.Sep, "\n")
	}
	return out
}
