// Package pathcase はパスと名前を比べるためのキーを作る。
//
// 大文字小文字だけが違う名前は同じパスとして扱い、両立させない。どの機械でも同じ答えにするため、
// 比べるときは必ずここを通す。返すのは比べるための文字列で、ファイルを開く表記には使わない。
//
// パスでない語の比べ（区切りを変えない casefold）は pyfmt.Casefold で、Fold もそれで組む。
// Fold が基本で、拡張子の判定は HasExt、リンクを辿った実体の比べは Real と SameReal、表記だけの比べは SameNorm。
package pathcase
