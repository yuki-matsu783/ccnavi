// Package ospath は Python の os.path と str の、答えが Go の標準と食い違う部分だけを同じ答えで返す。
//
// filepath.Join・filepath.Dir は途中で Clean するが、Python の join・dirname は綴りを変えない。
// filepath.EvalSymlinks は無いパスで失敗するが、os.path.realpath は解けたところまで解いて残りを足す。
// 比べるキーや記録に残すパスがこの差で変わらないよう、移した側はここを通す。OS の分岐は Windows を見る。
//
// Disk の付く読み（DiskExists・DiskLexists・ReadDiskText）はディスクだけを見る。記録・マーカー・承認済み
// チケットは、溜めている書き込みも見る fsio の Exists・Lexists・ReadText で読む。
//
// Python からの対応: os.path.exists・lexists → DiskExists・DiskLexists、open(...).read() → ReadDiskText、
// fsio.slashed → Slashed、str.strip()・str.rstrip() → Strip・Rstrip、os.path.splitext → Splitext、os.path.islink → IsLink、sorted(os.listdir) → DiskListdir、
// os.path.getmtime・getsize（os.stat）→ Stat、
// os.stat(...).st_nlink → HardLinks、os.lstat(...).st_ino・st_ctime_ns → Identity、ドライブの頭（`C:`）の判定 → HasDrive、
// str.splitlines(keepends=True) → SplitlinesKeep、`try: os.path.relpath(p, start) except ValueError: p` → RelOr。
package ospath
