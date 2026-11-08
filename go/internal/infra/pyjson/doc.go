// Package pyjson は Python の json.dumps と json.loads と同じバイト列・同じ値・同じエラー文を作る。
// hook の応答やボードの JSON は、1 バイトも変えない約束なので、encoding/json ではなくここを通す。
// オブジェクトは Object（キーの順序を保つ）で受け渡し、整数と小数は区別して読む（1 と 1.0 は書き戻すと違う）。
//
// Object には取り出しと更新の補助がある（With・Pop）。数の読みは Number、正の整数の確かめは PositiveInt。
package pyjson
