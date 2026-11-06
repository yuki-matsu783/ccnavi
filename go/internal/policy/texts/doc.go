// Package texts は文面ファイル（`.ccnavi/messages/*.yml`。拒否・促し・次の一手の文）の読み込み・検査・
// 組み立て。条件（いつ出すか）はルールとコードと sh に残り、文（何を出すか）は文面ファイルが持つ。
// 呼ぶ側は識別子と差し込む値を渡し、組み立てた文を受け取る。止める・止めないの判定には関わらない。
//
// Say は失敗しない。文面ファイルが無い・壊れている・識別子や値が合わないときは、種類ごとの短い既定の文
// （Fallback）を返す。判定は呼ぶ側が決めたままで、壊れた文面ファイルが防御を消さない。
// 1 件を引くときは、識別子の字を含むファイルだけを YAML として読む（hook は 1 回ごとに別のプロセスなので）。
// 形式と欄の表は docs/21_design/messages.md の「文面ファイル」。
//
// Python からの対応: say → Say（値は Values、場合は Variants）、spec → Spec（SpecOptions）、spoken → Spoken、
// load → Load（Catalog）、find → Find、render → Render、check_text → CheckText、fallback → Fallback、
// lines_of → LinesOf、ID.fullmatch → IsID、LABELS["next"] → NextLabel、NOTE_LABEL → NoteLabel、
// SUBJECT_LIMIT → SubjectLimit、TextError → *Error、dirs → Dir。上の段が使わない fields_for・kind_of_id・
// scan・fill は私的にし、呼び手の無い code_of_id は移さなかった。読んだ結果を覚えておく _CACHE・_FILES は
// 持たない（実行ファイルは呼び出しごとに別のプロセス）。
package texts
