// Package childnote は子チケットの担当の案内の行を組む。サブエージェントの始まり（SubagentStart）と、起動の
// PreToolUse がプロンプトに書き足す案内（launch）が同じ行を使う。どちらも判定ではなく案内なので、組めない部分
// （局面など）は黙って省き、案内全体を失敗させない。
//
// Python からの対応: hook/childnote。RULE_LINES → RuleLines、Block（lines・brief・notice → Lines・Brief・Notice）、
// child_block → ChildBlock（brief_room は呼び手が budget を絞って渡す）、stage_lines → StageLines。predecessors は
// 呼び手の読み方ごとに approval.PredecessorPool（読む）と launch の中（読んだ一覧から組む）に分けた。
package childnote
