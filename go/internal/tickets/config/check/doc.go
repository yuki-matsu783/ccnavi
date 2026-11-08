// Package check はレイヤーの設定 3 本（rules・phases・risks）を検査する。`--lint` と user handoff が同じ関数を呼ぶ。
// `--lint` はワークスペースの今の設定を、user handoff は設定の置き場を下書きに差し替えた設定を検査する。そのため、
// 取り込みの検査が通ったのに `--lint` が error を言う、という食い違いは起きない。検査の単位は 1 つのレイヤーの
// 1 つの種類である。レイヤーをまたぐ検査は `--lint` の側に残す。
package check
