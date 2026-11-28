package useronly

import (
	"fmt"
	"strings"
)

// entryInfo はユーザが打つ入口の sh 1 本の、一覧に出す説明と打つ例。
type entryInfo struct {
	script string
	// summary は 1 行の説明。
	summary string
	// example は打つ例の引数（`sh .ccnavi/scripts/ccnavi-user-<script>.sh` の後ろ）。
	example string
}

// entries はユーザが打つ入口の sh の一覧（`ccnavi user` が引数なしで出す）。`ccnavi-user-lib-review.sh` は部品で、
// 単独では打たない。順は、承認から始まる普段の流れの順。
var entries = []entryInfo{
	{"agree", "承認待ちのチケットを承認し、承認済みチケットをコミットして push する", "--preview"},
	{"decide", "残った指摘の対応方針を決めて置く", "3 --preview"},
	{"close-early", "残りがあっても、早めにクローズする判断を置く", "--parent i0002 --reason '残りは次のフェーズで扱う'"},
	{"push-approved", "承認済みチケットの置き場をコミットして push する（承認の後始末。打ち直しにも使う）", ""},
	{"handoff", "wip/handoff/ の下書きを、保護対象の本来の場所へ取り込む", "--preview"},
	{"mirror", "共通設定のミラーと工程スキルのずれを、プロジェクトへ揃える", "--preview"},
	{"prune", "判定ログと state を整理する", "--preview"},
	{"clean-branch", "親をクローズしたときに残した子のブランチを消す", "i0002-01-03"},
}

// Listing は `ccnavi user`（引数なし）が出す入口の一覧。名前・1 行の説明・打つ例を並べる。
func Listing() string {
	var b strings.Builder
	b.WriteString("ユーザだけが打つ入口です（エージェントからは打てません）。ターミナルで次の sh を打ちます。\n\n")
	for _, e := range entries {
		name := UserScriptPrefix + e.script + ".sh"
		fmt.Fprintf(&b, "%s\n  %s\n  例: %s\n\n", name, e.summary, strings.TrimSpace("sh .ccnavi/scripts/"+name+" "+e.example))
	}
	b.WriteString("書く操作は、実行ファイルが内容を見せて y/N を聞きます。--preview は何も書かずに内容だけ出します。\n")
	b.WriteString("使い方は、各 sh に -h を付けて打ちます。\n")
	return b.String()
}

// IsBareEntry は argv（`--entry` を外した後）が、操作の無い `user` だけか。
func IsBareEntry(argv []string) bool {
	at := 0
	for at < len(argv) && isWrapperOption(argv[at]) {
		if strings.Contains(argv[at], "=") {
			at++
		} else {
			at += 2
		}
	}
	return at == len(argv)-1 && asciiLower(argv[at]) == userWord
}
