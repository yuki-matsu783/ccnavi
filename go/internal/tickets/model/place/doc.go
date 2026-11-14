// Package place は、チケットの置き場の名前と、パスの文字列だけで決まる置き場の見分けを置く。ここに集めるのは、
// 状態の名前（置き場のディレクトリ名そのもの）、範囲を適用しない置き場（提案・承認済みチケット・下書き）、
// 途中の作業の置き場、退避と承認済みの領域の下の名前である。ファイルも git も読まない。
//
// 下の層（records/prune・policy/selfguard・records/stopreview）も名前で見る置き場（archiveName・Handoff・WipRoot・Todo）
// がある。下の層は tickets を読めないので、名前を infra/config に置く。ここの定数は、それを指す。
package place
