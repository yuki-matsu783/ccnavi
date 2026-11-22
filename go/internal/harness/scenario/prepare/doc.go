// Package prepare は、受入シナリオを回す前の準備と後片付けである。
// setup のファイルを作る・追記する・戻す、回す前の検査、setup.host の相手の用意と step ごとの記録を扱う。
//
// シナリオの読み込み（schema）・使い捨てのリポジトリの扱い（repo）は実行器の芯の持ち主のもので、ここは読まない。
// 要るものは入力の型（Setup・Files）と差し込み口（Hooks）で受ける。State は state.json のうち、ここと actors が触る欄だけで、
// 実行器の状態の型が埋め込む（欄は JSON で平らに並ぶ）。Refusal は回す前の拒否（理由・案内:・次:）で、実行器の持つ同じ型へ寄せてよい。
package prepare
