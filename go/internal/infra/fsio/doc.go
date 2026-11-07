// Package fsio はファイルの読み書きの入口。記録・マーカー・承認済みチケット・下書きは全部ここを通す。
//
// 読めなければ false か error、書けなければ理由の文（OS のエラーの文）を返し、hook の中で
// 失敗が上へ抜けて判定に達しないまま終わることを防ぐ。理由の文に「書けない」のような言い回しは足さない
// （呼び手の用件）。書いたパスは Recorder が集め（write-and-push のコミットの対象）、Staging の間は
// ディスクに書かずに Op として溜める（承認の plan）。時刻は Stamp だけが読み、FixClock で固定できる。
//
// 書き込みは WriteFile（バイト列）と WriteText（本文）の How で選ぶ。
//   - Plain は素の上書き。
//   - Atomic は一時ファイルに書いてから差し替える。
//   - Durable は fsync し、`.<名前>.<一意>.part` に書いてから差し替える。
//   - New は既にあれば失敗する作成、NewDurable はそれに fsync を足したもの。
//
// そのほかの入口は次のとおり。
//   - JSON は WriteJSON と、書式を選ぶ WriteJSONAs・JSONFormat。読みは ReadObject と ReadJSON（値と error）。
//   - 1 行の追記は AppendLine、差し替えは ReplaceFile、コピーは CopyFile。試しに書く使い捨ての置き場は ScratchDir。
//   - 記録のパスは RecordPath（拡張子は JSONExt）、会話の記録を末尾から広げて読むのは ScanTail。
//   - fsio を通さずディスクだけを読むのは ReadDiskText・ReadDiskBytes（溜める段は見ない）、中身の要約は DiskDigest。
//   - 名前や識別子に入れる時刻の書式は IDStampLayout・NameStampLayout・StampLayout・LocalStampLayout・ISOStampLayout、
//     時刻と Unix 秒の変換は UnixSeconds・FromUnixSeconds。hook の時刻の差し込み口は Now・SetNow。
//   - OS のエラーは wrap で文を保ったまま上へ渡す。StrError は番号の無いエラーで空、CrossDevice は別デバイスへの
//     差し替えの失敗を見分ける。
//   - 他人のファイルを辿らない開き方は NoFollow・nonBlock の 2 つで開いて fstat する OpenNoFollow。
//   - 標準入力の答えは ReadAnswer（y・yes なら ReadYes）。
package fsio
