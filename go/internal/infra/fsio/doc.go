// Package fsio はファイルの読み書きの入口。記録・マーカー・承認済みチケット・下書きは全部ここを通す。
//
// 読めなければ false か error、書けなければ理由の文（Python の str(OSError) と同じ文）を返し、hook の中で
// 失敗が上へ抜けて判定に達しないまま終わることを防ぐ。理由の文に「書けない」のような言い回しは足さない
// （呼び手の用件）。書いたパスは Recorder が集め（write-and-push のコミットの対象）、Staging の間は
// ディスクに書かずに Op として溜める（承認の plan）。時刻は Stamp だけが読み、FixClock で固定できる。
package fsio
