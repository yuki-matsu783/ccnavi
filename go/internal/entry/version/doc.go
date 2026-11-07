// Package version は、実行ファイルが自分について言うこと（`ccnavi --version [--json]`）を持つ。言うのは、
// 版・組み立ての元のコミット・組み立てた実行ファイルかどうか・互換の版（Compat）・読む書式の版である。
// sh は `compat: <数>` の行を sed で読み、配布の組み立て（scripts/ccnavi-package.sh）はコミットを読む。
// 設定もワークスペースも読まず、git にもネットワークにも出ない。
//
// Compat は実行ファイルと、それを呼ぶ側（.ccnavi/scripts/ の sh と VS Code 拡張）との契約の版である。
// 上げ方は Compat のコメントにある。上げたら、sh の CCNAVI_COMPAT（ccnavi-common.sh）と拡張の EXTENSION_COMPAT
// （version.ts）も揃える。
//
// 組み立ての元のコミットは、組み立てのときに -ldflags で commit に埋める（埋め方は commit のコメントにある）。
// 埋めていなければ go run・go test で動いているとみなし、built は偽、コミットは Unknown と言う。
// 実行ファイルは git に聞かない。ソースで動いているときにワークツリーの HEAD を言うと、
// 組み立てた版と取り違えるからである。
//
// Python からの対応は次のとおり。
//   - entry/version を移した。VERSION・COMPAT・SCHEMA・UNKNOWN → Version・Compat・Schema・Unknown。
//   - report → Report、describe → describe（私的）、commit()・built() → 埋めた commit の読み（私的）、
//     formats() → 私的。
//   - ccnavi_buildinfo の部品（BUILDINFO_MODULE）は、-ldflags の commit に置き換えた。
package version
