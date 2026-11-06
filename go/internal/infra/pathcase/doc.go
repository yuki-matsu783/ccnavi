// Package pathcase はパスと名前を比べるためのキーを作る（Python の infra.pathcase）。
//
// 大文字小文字だけが違う名前は同じパスとして扱い、両立させない。どの機械でも同じ答えにするため、
// 比べるときは必ずここを通す。返すのは比べるための文字列で、ファイルを開く表記には使わない。
//
// Python からの対応: pathcase.fold → Fold。name.lower().endswith(ext) → HasExt。
package pathcase
