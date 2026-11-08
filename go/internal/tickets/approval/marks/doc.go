// Package marks は承認済みの領域の `phases/<親>/` に置くマーカーと記録を読み書きする。対象は、フェーズのマーカー
// （`<N>.<種類>`。レビューが済むまで止める判定が見る）、親ごとのマーカー（`ready`・`close-early`・`closed` の `.json`）、
// 受け入れ台帳（`accepted.json`）、子ごとの記録（`<子>.risk.json`・`<子>.judge.json`）と、承認済みチケットの書き直し。
// ファイル名と JSON の形（キーの順・indent・ensure_ascii=False・機械の改行）は sh と拡張も読むので、変えない。
package marks
