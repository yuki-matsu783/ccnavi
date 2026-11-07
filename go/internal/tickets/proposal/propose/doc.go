// Package propose は提案を親のブランチへ送る入口（`ccnavi ticket approval-request <識別子>`）。エージェントが書いた
// 承認待ちの提案（todo/）と子のフローの下書き（flows/）は、書いただけでは手元にしかないので、親のブランチに載せて
// 別の PC の承認に届ける。実行ファイルは書き換えず、送るパスを「この実行で書いたもの」の一覧に載せるだけで
// （fsio.NoteInput）、ロック・取り込み・commit --only・push は sh が write-and-push の手順で行う。ネットワークにも
// git にも触らない。
//
// 対象は親のワークツリーの中の、識別子の親子の提案（todo/<親>.md・todo/<子>.md・flows/<子>.yml）だけ。計画を持つ
// 親の提案だけ、送る前に after の形と終端を検査し、誤りがあれば何も送らない（承認で落ちる誤りを承認の前に返す）。
// 最初の親の提案で、すぐ始まるステップに子の提案が無ければ警告する（止めない。親を先に承認する進め方も正規）。
//
// Python からの対応: tickets/proposal/propose。run → Run（引数は conf, root の順。gate は呼び手の検査）、FLOWS →
// flow.FlowsDir。
package propose
