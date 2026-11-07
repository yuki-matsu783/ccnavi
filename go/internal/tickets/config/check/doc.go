// Package check はレイヤーの設定 3 本（rules・phases・risks）を検査する。`--lint` と user handoff が同じ関数を呼ぶ。
// `--lint` はワークスペースの今の設定を、user handoff は設定の置き場を下書きに差し替えた設定を検査する。そのため、
// 取り込みの検査が通ったのに `--lint` が error を言う、という食い違いは起きない。検査の単位は 1 つのレイヤーの
// 1 つの種類である。レイヤーをまたぐ検査は `--lint` の側に残す。
//
// Python からの対応: tickets/config/check。layer_where・common_rules・common_phases・common_risk・layer_rules・
// layer_configs・file_problems・unmatched_scopes → LayerWhere・CommonRules・CommonPhases・CommonRisk・LayerRules・
// LayerConfigs・FileProblems・UnmatchedScopes（only・kind の None → 空）、layer_home・layer_names → layer.Home・
// layer.Names。
package check
