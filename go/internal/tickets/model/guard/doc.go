// Package guard はチケットの置き場を守る組み込みのルールと、生の git を止める組み込みのルールと、提案を書いた回に
// 渡す承認の前の確認の文を持つ。どれもルールファイルには書かず、判定が組み込みとして足す。
//
// Python からの対応: ticket_guard を移した。guard_rules → GuardRules、approved_write_rule → ApprovedWriteRule、
// gate_rules → GateRules、raw_git_rule → RawGitRule、propose_notice → ProposeNotice、state_dir_regex →
// stateDirRegex、*_RULE_ID → *RuleID、RAW_GIT_REGEX → rawGitRegex。Python は式を re.compile で直に組み、失敗は
// 例外で上がっていた。ここは error で返す。
package guard
