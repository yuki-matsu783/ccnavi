// Package ospath は、パスと文字列の扱いのうち、Go の標準の答えが Python の os.path・str と食い違う部分を
// Python と同じ答えで返す。比べるキーや記録に残すパスは、この差で変わると別の機械で別の値になる。
//
// filepath.Join・filepath.Dir は途中で Clean するが、ここの Join・Dirname は綴りを変えない。
// filepath.EvalSymlinks は無いパスで失敗するが、ここの realpath は解けたところまで解いて残りを足す。
// 比べるキーや記録に残すパスがこの差で変わらないよう、呼ぶ側はここを通す。OS の分岐は Windows を見る。
//
// Disk の付く読み（DiskExists・DiskLexists・ReadDiskText）はディスクだけを見る。記録・マーカー・承認済み
// チケットは、溜めている書き込みも見る fsio の Exists・Lexists・ReadText で読む。
package ospath
