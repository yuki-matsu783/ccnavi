package cli

import (
	"embed"
	"strings"
	"unicode/utf8"
)

// usage/ の各話題の節は `ccnavi --help` の本文そのもの。改行と空白まで出力に出るので、文書としてではなく出力として直す。
//
//go:embed usage/*.txt
var usageFiles embed.FS

// topicRow は一覧に出す 1 行と、その節。一覧と全文はこの並びで出る。
type topicRow struct{ name, how, summary string }

var topicRows = []topicRow{
	{"version", "ccnavi --version", "バージョン・ビルド情報・互換性の情報を出す"},
	{"lint", "ccnavi --lint", "ルール・フェーズ・リスクの設定を検証する"},
	{"test", "ccnavi --test", "ツールを動かさずにルールの判定を試す"},
	{"explain", "ccnavi --explain", "チケットとその置き場を、機械が読める形で一覧する"},
	{"docs", "ccnavi --docs", "md の frontmatter の索引を引く"},
	{"templates", "ccnavi --templates", "作業・レビュー計画のテンプレートを扱う"},
	{"learnings", "ccnavi --learnings", "学びの蓄積のパターンの状態を一覧する"},
	{"logs", "ccnavi-user-prune.sh / --suggest", "判定ログを整理し、ルールの候補を作る"},
	{"agree", "ccnavi-user-agree.sh / --plan-order", "提案の承認と、計画の順序"},
	{"ticket", "ccnavi ticket", "チケットの開始・終了・状態・雛形"},
	{"scripts", "ccnavi review|worktree|sync ほか", "スクリプトが呼ぶサブコマンドと内部オプション"},
}

// 一覧の欄の幅（Python の f"{name:<10} {how:<34}"。幅は符号位置で数える）。
const (
	nameWidth = 10
	howWidth  = 34
	// scriptsTopic はスクリプト用のサブコマンドの語が選ぶ話題。
	scriptsTopic = "scripts"
)

// scriptVerbs は `ccnavi <語> --help` で scripts の節を選ぶ、スクリプト用のサブコマンド。
var scriptVerbs = []string{"review", "worktree", "sync", "tree", "write-and-push", "branches"}

const overviewGuide = `
話題ごとの使い方は、話題のオプションかサブコマンドに --help を付けると出ます
（例: ccnavi --docs --help、ccnavi ticket --help）。ccnavi --help <話題> でも同じです。
全文は ccnavi --help --all で出ます。

`

func usageText(name string) string {
	// 読めないのは埋め込みの組み立ての不備で、テストが本文を比べて見つける。
	b, _ := usageFiles.ReadFile("usage/" + name + ".txt")
	return string(b)
}

// topic は話題の節。
func topic(name string) string { return usageText(name) }

// topicWord は `--help <語>` と `ccnavi <語> --help` の語が選ぶ話題（無ければ空）。
func topicWord(word string) string {
	for _, r := range topicRows {
		if r.name == word {
			return r.name
		}
	}
	for _, v := range scriptVerbs {
		if v == word {
			return scriptsTopic
		}
	}
	return ""
}

func padRight(s string, width int) string {
	if n := utf8.RuneCountInString(s); n < width {
		return s + strings.Repeat(" ", width-n)
	}
	return s
}

// overview は話題の一覧（`--help` だけのとき）。
func overview() string {
	var b strings.Builder
	b.WriteString(usageText("intro"))
	b.WriteString(overviewGuide)
	for i, r := range topicRows {
		if i > 0 {
			b.WriteString("\n")
		}
		b.WriteString("    " + padRight(r.name, nameWidth) + " " + padRight(r.how, howWidth) + " " + r.summary)
	}
	b.WriteString("\n")
	return b.String()
}

// fullUsage は全文（`--help --all`）。一覧のあとに各話題の節を並べる。
func fullUsage() string {
	parts := make([]string, len(topicRows))
	for i, r := range topicRows {
		parts[i] = usageText(r.name)
	}
	return overview() + "\n" + strings.Join(parts, "\n")
}
