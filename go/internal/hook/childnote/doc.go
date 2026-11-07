// Package childnote は子チケットの担当の案内の行を組む。サブエージェントの始まり（SubagentStart）と、起動の
// PreToolUse がプロンプトに書き足す案内（launch）が同じ行を使う。どちらも判定ではなく案内なので、組めない部分
// （局面など）は黙って省き、案内全体を失敗させない。
package childnote
