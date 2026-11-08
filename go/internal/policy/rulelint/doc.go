// Package rulelint は、ルールファイルの中身を検査する。組み上がらないルール、何も止めない match、届かない
// message、広すぎる allow などを名指しする。`--lint`・保護対象の下書きの取り込み・提案（suggest）が呼ぶ。
// 判定と同じ読み（rules.Load）で読む。別の読み方をすると、検査は通ったのに実運用で失敗するため。
package rulelint
