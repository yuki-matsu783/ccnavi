package judge

import (
	"slices"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/policy/rules"
)

// Handover は、判定を Claude Code の権限モードへ渡したことを表す。rules.Decision と同じ変数に入るため、
// ルールファイルには書けない表記にしてある。
const Handover rules.Decision = "(handover)"

// Claude Code の権限モード。ルールが言及しなかった呼び出しの結末が、これで変わる（ccnavi のモードとは別）。
var (
	// PermissionJudged は、判断できる相手（classifier か Claude Code の権限の仕組み）が居るモード。ここに ask を
	// 上乗せしても同じ呼び出しで 2 度聞くだけになるため、渡す。
	PermissionJudged = []string{"auto", "default", "acceptEdits", "plan"}
	// PermissionNoJudge はユーザにも classifier にも確認できないモード。ask は誰も答えないまま通るので、許可としない。
	PermissionNoJudge = []string{"dontAsk", "bypassPermissions"}
)

// UndeclaredVerdict は、どのタイプも言及しなかった呼び出しの結末（Handover・ask・deny）を返す。degraded は、コマンドを
// 読み切れなかった理由（読めたなら空）。読めなかった呼び出しは渡さない。読めなかったことを言える場所が他に無いため。
// 知らないモードは ask にする。モードの名前が増えたとき、確認なしに通らず確認に回るようにするため。
func UndeclaredVerdict(permissionMode, degraded, guardUnwatched string) rules.Decision {
	if slices.Contains(PermissionNoJudge, permissionMode) {
		if degraded != "" || guardUnwatched != string(config.Disable) {
			return rules.Deny
		}
		return Handover
	}
	if degraded == "" && slices.Contains(PermissionJudged, permissionMode) {
		return Handover
	}
	return rules.Ask
}

// strength は判定の強さ。ルールとチケットの判定を合わせるとき、強い側を採る（同じならルール）。
var strength = map[rules.Decision]int{rules.Deny: 3, rules.Ask: 2, rules.Allow: 1, "": 0}
