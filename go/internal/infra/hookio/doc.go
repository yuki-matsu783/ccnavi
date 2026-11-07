// Package hookio は、Claude Code が hook の標準入力に書く payload の解釈と、標準出力に返す応答の
// 組み立てを担う。両方の形を知っているのはここだけにして、上流が変わったときに直す場所を 1 つにする。
// 応答の JSON は pyjson で書く（キーの順や空白を変えないため、バイト列が安定する）。
//
// payload は Decode で読み、SessionStart の source は StartSource で取る。応答は WritePreToolUse で書く。
// 文の頭 "[ccnavi] " と `[ccnavi] CODE (…)`・`(source: …)`・"subject: " の組み立ては Head・Tagged・Cited・SubjectPrefix。
// 無人かどうかの判定は 2 つあり、答えが違う。hook の判定側は Unattended、記録の idle handoff 側は Headless。
// 違いは各関数のコメントに書く。
package hookio
