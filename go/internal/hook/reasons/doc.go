// Package reasons は、判定（止めた・聞いた・渡した）と hook の失敗につける文と、judge が自分で作る根拠（ルール・チケット・
// 読めない形）の理由コードを持つ。判定の流れは judge にあり、ここは文だけを持つ。文はユーザとモデルが読む成果物そのものである。判定の順序より直す頻度が高いので、分けてある。
//
// 1 件の文は、それだけで読んで成立するように組む。1 回の応答に複数の理由が入り、どれが目に入るかが決まらないためである。
// 載せるのは、何にヒットしたか（subject）、どういう筋の根拠か（理由コード）、どの設定が言っているか（出所）。
// 理由コードは「どの検査が根拠を作ったか」の名前であり、ルール 1 件を指す名前ではない。どのルールかは出所が名指しする。
// 組み込みの判定の記録のルール名は `builtin:`（rules.ReservedIDPrefix）で始める。ルールファイルを探しても見つからない
// ことを、見た目で示すため。
//
// 理由コードと組み込みのルール名の置き場の決まり: 検査を持つパッケージが、その検査の出す理由コードとルール名を持つ
// （shellguard・turngate・askcontext・post・launch・hint など。judge と記録の数えはそこから読む）。2 つの検査が同じ名前を
// 出すときは、両方より下の policy/builtin に置く（範囲外の builtin.TicketScopeRuleID）。名前が同じで値の違うコードは作らない。
//
// Python からの対応: hook/reasons。CODE_* → Code*、*_RULE → *Rule（TICKET_RULE は builtin.TicketScopeRuleID）、CODE_REWRITE[form] → RewriteCode、rewrite_rule →
// RewriteRule、code_for → CodeFor、fallen_back → FallenBack、undeclared → Undeclared、reason_for → ReasonFor（引数は Hit。
// 使われていなかった rules_path は外した）、ask_hint → AskHint、subagent_forbidden → SubagentForbidden、builtin_refusal →
// BuiltinRefusal、internal_error → InternalError、internal_error_notice → InternalErrorNotice、payload_unusable →
// PayloadUnusable、deadline_exceeded → DeadlineExceeded（期限は time.Duration）、rewrite → Rewrite。degraded は読めなかった
// 理由（shell.Reason。読めたなら空）、git は文で案内する git の入口の打ち方（ラッパーが無ければ空）。
package reasons
