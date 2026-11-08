package shellguard

import (
	"testing"

	"ccnavi/internal/infra/shell"
)

// sh の検査の材料を変える環境変数を、同じコマンド行で置いて保護済みの sh を呼ぶ形（tests/guard/test_wrapguard.py の
// EnvTest から移した）。止める・通すの約束をここで見る。
func TestCheckEnvTable(t *testing.T) {
	for command, stops := range map[string]bool{
		"CCNAVI_TICKETS_APPROVED=/x sh .ccnavi/scripts/ccnavi-git.sh push":             true,
		"env CCNAVI_PROJECTS=/x sh .ccnavi/scripts/ccnavi-git.sh push":                 true,
		"env -u CCNAVI_ENTRY_PATH sh .ccnavi/scripts/ccnavi-ticket.sh finish a":        true,
		"export CCNAVI_WORKSPACE=/x; sh .ccnavi/scripts/ccnavi-git.sh push":            true,
		"CCNAVI_STATE=/x; sh .ccnavi/scripts/ccnavi-review.sh confirm":                 true,
		"unset CCNAVI_TICKETS_PROPOSAL && sh .ccnavi/scripts/ccnavi-git.sh push":       true,
		"sudo env CLAUDE_PROJECT_DIR=/x bash .ccnavi/scripts/ccnavi-review.sh confirm": true,
		"CCNAVI_NEW_THING=1 ../../../.ccnavi/scripts/ccnavi-git.sh status":             true,
		"cd a && CCNAVI_ENTRY_PATH=f sh ../.ccnavi/scripts/ccnavi-user-agree.sh":       true,
		// 関数の定義の本体。頭（`f()`・`function f`）を名前と読み違えると見落とす。
		"f() { CCNAVI_TICKETS_APPROVED=/x sh .ccnavi/scripts/ccnavi-git.sh push; }; f": true,
		"f(){ CCNAVI_X=1 sh .ccnavi/scripts/ccnavi-git.sh push; }; f":                  true,
		"function f { CCNAVI_X=1 sh .ccnavi/scripts/ccnavi-git.sh push; }; f":          true,
		// 環境を丸ごと空にする。settings.json の env が書き出した置き場も消える。
		"env -i sh .ccnavi/scripts/ccnavi-git.sh push":                   true,
		"env --ignore-environment sh .ccnavi/scripts/ccnavi-git.sh push": true,
		// 値を取るオプションの後ろの代入と、ループの中で次の回に届く代入。
		"env -C /tmp CCNAVI_X=1 sh .ccnavi/scripts/ccnavi-git.sh push":             true,
		"for i in 1 2; do sh .ccnavi/scripts/ccnavi-git.sh push; CCNAVI_X=1; done": true,
		// git が操作するリポジトリとワークツリーを変える変数。子の居場所で親へコミットできる。
		"GIT_DIR=/p/.git GIT_WORK_TREE=/p sh .ccnavi/scripts/ccnavi-git.sh commit -m x": true,
		"export GIT_WORK_TREE=/p; sh .ccnavi/scripts/ccnavi-git.sh add -A":              true,
		"env GIT_INDEX_FILE=/p/.git/index sh .ccnavi/scripts/ccnavi-git.sh status":      true,
		"GIT_COMMON_DIR=/p/.git sh .ccnavi/scripts/ccnavi-git.sh commit -m x":           true,
		"GIT_CEILING_DIRECTORIES=/x sh .ccnavi/scripts/ccnavi-git.sh commit -m x":       true,
		"sh .ccnavi/scripts/ccnavi-git.sh push":                                         false,
		"CCNAVI_GIT_MAX_LINES=200 sh .ccnavi/scripts/ccnavi-git.sh log":                 false,
		"env CCNAVI_FETCH_TIMEOUT=5 sh .ccnavi/scripts/ccnavi-fetch.sh":                 false,
		// 作者の名前と出力の形だけを変える git の変数は置ける。
		"GIT_AUTHOR_NAME=a GIT_PAGER=cat sh .ccnavi/scripts/ccnavi-git.sh commit -m x": false,
		// 保護済みの sh を呼ばないなら、ここの話ではない。
		"CCNAVI_ENTRY_PATH=x ccnavi --lint":                                   false,
		"echo CCNAVI_ENTRY_PATH=x && sh .ccnavi/scripts/ccnavi-git.sh status": false,
		// 読み切れない形はここでは見ない。allow がヒットせずに確認になる。
		"sh -c 'CCNAVI_X=1 sh .ccnavi/scripts/ccnavi-git.sh push'": false,
	} {
		_, names := CheckEnv(command, shell.Read(command).Degraded)
		if (len(names) > 0) != stops {
			t.Errorf("%q: 止めた=%v（%v）, want %v", command, len(names) > 0, names, stops)
		}
	}
}
