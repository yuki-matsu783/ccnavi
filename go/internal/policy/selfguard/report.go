package selfguard

import (
	"strings"

	"ccnavi/internal/policy/texts"
)

// Report はモデルに返す文。何が起きたかと、次に何をすべきか。結果が無ければ空。
func Report(root string, outcomes []Outcome) string {
	if len(outcomes) == 0 {
		return ""
	}
	lines := []string{"[ccnavi] ccnavi 自身の設定ファイルが変更されました。" +
		"ここはルールファイルの外で守っている場所で、判定と hook の登録はこのファイルで決まります。"}
	var copied, commonRules, rebased, other bool
	for _, o := range outcomes {
		lines = append(lines, "  "+o.Target.Label+": "+string(o.Action)+" — "+o.Detail)
		copied = copied || o.Target.Copy
		commonRules = commonRules || (o.Target.Key == commonRulesKey && !o.Target.Copy && o.Action != ActionRebased)
		rebased = rebased || o.Action == ActionRebased
		other = other || o.Action != ActionRebased
	}
	if copied {
		// ワークツリー側の設定は今の判定を変えないので、時間差で反映される経路だと言わないと同じ操作が繰り返される。
		lines = append(lines, texts.Say(root, "NOTICE_SELFGUARD__COPY", nil, nil))
	}
	if commonRules {
		// 壊れたルールファイルを直した書き込みも戻る。直す道（下書きとユーザの取り込み）を言う。
		lines = append(lines, texts.Say(root, "NOTICE_SELFGUARD__COMMON_RULES", texts.Values{"draft": commonRulesDraft}, nil))
	}
	if rebased {
		lines = append(lines, texts.Say(root, "NOTICE_SELFGUARD__REBASED", nil, nil))
	}
	if other {
		lines = append(lines, texts.Say(root, "NOTICE_SELFGUARD__ASK_USER", nil, nil))
	}
	return strings.Join(lines, "\n")
}
