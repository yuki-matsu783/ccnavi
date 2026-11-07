package shellguard

import (
	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/shell"
	"ccnavi/internal/policy/texts"
)

const pushSub = "push"

// CheckChildPush は、子チケットのワークツリーからの `ccnavi-git.sh push` なら、ツリーの名前と親。
//
// 子の成果は、親が合流してから親のツリーで送る。sh も同じ検査を持つが、sh の検査は環境変数の代入で外れる。また、承認済み
// チケットの探し方を sh の中に写して持つため、写しが食い違うと気づかないうちに外れてしまう。そこで止める場所は hook に置き、
// sh の検査は 2 重目として残す。居場所を追えなくなった先で push を打つ形は、どのツリーから送るのかが決まらないので止める
// （名前も親も `?`）。
func CheckChildPush(conf *config.Settings, root, cwd, subject string, degraded bool) (name, parent string) {
	if degraded || cwd == "" {
		return "", ""
	}
	for _, p := range shell.PlacedCommands(subject, false) {
		call, ok := findScriptCall(p.Words)
		if !ok || !isGitScript(call.script) || len(call.args) == 0 || call.args[0] != pushSub {
			continue
		}
		where, known := callPlace(cwd, p, call.chdirs)
		if !known {
			return unknownParent, unknownParent
		}
		name := worktreeName(root, where)
		if name == "" {
			continue
		}
		if parent := childParent(conf, root, name); parent != "" {
			return name, parent
		}
	}
	return "", ""
}

// ChildPushMessage は子のワークツリーからの push を止めた文。sh の文面と揃える。
func ChildPushMessage(root, name, parent string) string {
	if name == unknownParent {
		return texts.Say(root, "DENY_CHILD_PUSH__UNREAD", texts.Values{}, nil)
	}
	return texts.Say(root, "DENY_CHILD_PUSH__CHILD", texts.Values{"name": name, "parent": parent}, nil)
}
