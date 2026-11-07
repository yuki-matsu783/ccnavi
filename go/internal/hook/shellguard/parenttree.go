package shellguard

import (
	"slices"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/pyfmt"
	"ccnavi/internal/infra/pyre"
	"ccnavi/internal/infra/shell"
	"ccnavi/internal/policy/texts"
)

// parentWrites はサブエージェントが親のワークツリーで打てないラッパーのサブコマンド。親の中身・索引・ブランチの
// 先頭を動かすもの。履歴を書き換えるもの（cherry-pick・revert・rebase など）はラッパーも通さないが、ここでも名指し
// して止める（ラッパーの許可が広がっても、親のワークツリーには届かない）。読むもの（status・log・diff）は止めない。
var parentWrites = []string{
	"add", "rm", "mv", "restore", "checkout", "switch", "stash", "merge", "commit", "pull",
	"cherry-pick", "revert", "rebase", "am", "apply", "reset", "clean", "integrate", "integrate-done",
}

// openSub はサブコマンドの語に現れてはいけない文字（展開・置換・glob）。
const openSub = "$`*?["

// 走査が読み切れなかったときに、生の文字列でラッパーの名前の後ろのサブコマンドを読む。
var (
	degradedName = pyre.MustCompile(`ccnavi-git\.sh['"]?`, pyre.IgnoreCase)
	degradedSub  = pyre.MustCompile(`[ \t]+(?P<sub>[A-Za-z][\w-]*)(?=[\s'";&|)]|$)`, 0)
)

// degradedSubs は読み切れないコマンド行の、ラッパーの名前ごとの後ろのサブコマンド（小文字）。名前の直後に
// 文字どおりのサブコマンドが無ければ空文字。
func degradedSubs(subject string) []string {
	var out []string
	for _, name := range degradedName.FindAll(subject) {
		if sub, ok := degradedSub.MatchIn(subject, name.End, len([]rune(subject))); ok {
			word, _ := sub.Named("sub")
			out = append(out, pyfmt.Lower(word))
		} else {
			out = append(out, "")
		}
	}
	return out
}

// CheckParentTreeGit は、サブエージェントの `ccnavi-git.sh` が親のワークツリーを書き換える形なら、サブコマンドと
// 親のワークツリーの名前。
//
// 居場所は cwd から `cd` と実行役の行き先を追った先なので、子のワークツリーでのコミットと、子のワークツリーで親の
// ブランチを取り込む merge は通る。居場所を追えない形・読み切れないコマンド行で書き換えるサブコマンドを打つ形は、
// どのツリーに当たるかが決まらないので止める（名前は `?`）。
func CheckParentTreeGit(conf *config.Settings, root, cwd, subject string, degraded bool) (sub, name string) {
	if !mentionsGitScript(subject) {
		return "", ""
	}
	if degraded {
		for _, sub := range degradedSubs(subject) {
			if sub == "" {
				return unknownParent, unknownParent
			}
			if slices.Contains(parentWrites, sub) {
				return sub, unknownParent
			}
		}
		return "", ""
	}
	for _, p := range shell.PlacedCommands(subject, false) {
		call, ok := findScriptCall(p.Words)
		if !ok || !isGitScript(call.script) || len(call.args) == 0 {
			continue
		}
		first := call.args[0]
		if strings.ContainsAny(first, openSub) {
			return first, unknownParent
		}
		if !slices.Contains(parentWrites, pyfmt.Lower(first)) {
			continue
		}
		where, known := "", false
		if cwd != "" {
			where, known = callPlace(cwd, p, call.chdirs)
		}
		if !known {
			return first, unknownParent
		}
		if name := worktreeName(root, where); name != "" && isParentTree(conf, root, name) {
			return first, name
		}
	}
	return "", ""
}

// ParentTreeMessage は、サブエージェントが `ccnavi-git.sh` で親のワークツリーを書き換えるのを止めた文。
func ParentTreeMessage(root, sub, name string) string {
	if name == unknownParent {
		return texts.Say(root, "DENY_SUBAGENT_PARENT_TREE__GIT_UNREAD", texts.Values{"sub": sub}, nil)
	}
	return texts.Say(root, "DENY_SUBAGENT_PARENT_TREE__GIT", texts.Values{"sub": sub, "name": name}, nil)
}

// ParentWriteMessage は、サブエージェントが親のワークツリーへファイルを書き込むのを止めた文。what は書き込みの形
// （ツールの名前か、シェルの書き込み先・コードの文字列）。
func ParentWriteMessage(root, what, name string, kind WriteKind) string {
	if name == unknownParent {
		return texts.Say(root, "DENY_SUBAGENT_PARENT_TREE__WRITE_UNREAD", texts.Values{"what": what}, nil)
	}
	id := "DENY_SUBAGENT_PARENT_TREE__WRITE"
	if kind == kindCode {
		id = "DENY_SUBAGENT_PARENT_TREE__WRITE_CODE"
	}
	return texts.Say(root, id, texts.Values{"name": name, "what": what}, nil)
}
