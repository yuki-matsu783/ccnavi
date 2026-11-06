// Package pyjson は Python の json.dumps と json.loads と同じバイト列・同じ値・同じエラー文を作る。
// hook の応答やボードの JSON は、Python の実行ファイルが出していたものと 1 バイトも違えない約束なので、
// encoding/json ではなくここを通す。オブジェクトは Object（キーの順序を保つ）で受け渡し、整数と
// 小数は区別して読む（1 と 1.0 は書き戻すと違う）。
package pyjson
