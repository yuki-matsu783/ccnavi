// Package turngate はターン分離のゲートを持つ。「A を打ったら、ユーザの発言が 1 回はさまるまで B を打てない」を
// 止める。エージェントが自分の判断で止まる以外に守られない手順（差分をユーザに見せて返事を待つ、など）を、「A の
// あとにユーザの発言が 1 回あった」という機械で確かめられる事実に置き換える。止めるのは B の側だけ。
//
// 宣言 1 件（Gate）が 1 組を表し、宣言の表を渡して判定する（特定の組に依存しない）。条件（Cond）の読み方はルールと
// 同じ（大文字小文字を区別しない、先読み・後方参照は使えない）で、shell.Sep で分けたコマンド 1 つずつに当てる。
// キーは regex の名前つきグループ `key` で、A と B の両方が持てば同じキーの組だけを対象にする。値に解けないキー
// （`$name`）はどのキーとも一致する（読めない書き方でゲートを外せないように）。宣言の置き場は組み込みの表（Builtin）
// だけで、配布先のルールファイルからは足せない。AskIfAttended を立てた宣言は、付き添いのあるセッションでは発言を
// 待たず B を確認に出す（同じコマンドに A と B を並べた呼び出しだけは止める）。
//
// 記録は state の `turngate-<セッション>-…json` に 1 件 1 ファイルで残す（並行する呼び出しが書き換え合わない）。
// 残すのは実行前（Note）。実行後に残すと、同じメッセージで並べた A と B のうち B が A の記録の前に通る。代わりに、A が
// 最終的に拒否されたときと失敗したときに、その呼び出しの記録を消す（ForgetCall）。記録は B が通ったときと、
// セッションの開始・再開（Clear）で消す。壊れた記録・読めない置き場は閉じる側（B を止める）に倒し、文面が次の一手を
// 言う。壊れた記録は次のユーザの発言（DropBroken）が消すので詰まらない。
//
// Python からの対応: hook/turngate を移した。Cond・Gate・Stop は同名、BUILTIN・INTEGRATE_GATE・REVIEW_CONFIRM_GATE →
// Builtin・IntegrateGate・ReviewConfirmGate、check（same_command_only は引数）・note・asks・split_by_attendance・
// forget_call・clear・drop_broken・validate・rule_id・message・ask_message → Check・Note・Asks・SplitByAttendance・
// ForgetCall・Clear・DropBroken・Validate・RuleID・Message・AskMessage、止めた理由の定数 → Reason、CODE・RULE_PREFIX・
// PREFIX・SUFFIX → Code・RulePrefix・Prefix・Suffix。条件が組めないときの ValueError は ErrBadGate を包んだ error。
// hits は私的にした。
package turngate
