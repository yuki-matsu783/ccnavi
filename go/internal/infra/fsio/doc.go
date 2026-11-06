// Package fsio はファイルの読み書きの入口。記録・マーカー・承認済みチケット・下書きは全部ここを通す。
//
// 読めなければ false か error、書けなければ理由の文（Python の str(OSError) と同じ文）を返し、hook の中で
// 失敗が上へ抜けて判定に達しないまま終わることを防ぐ。理由の文に「書けない」のような言い回しは足さない
// （呼び手の用件）。書いたパスは Recorder が集め（write-and-push のコミットの対象）、Staging の間は
// ディスクに書かずに Op として溜める（承認の plan）。時刻は Stamp だけが読み、FixClock で固定できる。
//
// Python からの対応: 書き込みの 7 種類は WriteFile（バイト列）・WriteText（本文）の How で選ぶ。
// write_text → WriteText(…, Plain)、write_text_atomic → Atomic、write_text_durable → Durable、
// write_bytes → WriteFile(…, Plain)、write_bytes_atomic → Atomic、write_new → New、
// write_new_durable → NewDurable。write_json_atomic → WriteJSON、replace_bytes → ReplaceFile、
// read_dict → ReadObject、read_json の (値, 例外) → ReadJSON の (値, error)、clock → FixClock、
// recording → StartRecording と Recorder.Stop、policy → SetPolicy、slashed → ospath.Slashed。
// open(path, "a") の 1 行の追記 → AppendLine、time.time() の秒との変換 → UnixSeconds・FromUnixSeconds。
package fsio
