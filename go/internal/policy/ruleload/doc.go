// Package ruleload は、この呼び出しに適用するルール集合を決める。組み込みの deny（土台）に共通レイヤーを足し、
// その上にツリーのレイヤー（自身のレイヤーとプロジェクトのレイヤー）を足す。足すだけで、後ろのレイヤーが
// 前を上書きしたり取り消したりはしない。判定そのものはここに無い。
//
// パスを持つツールは、共通レイヤーと行き先の 1 レイヤーを使う。パスを持たないツールは、共通レイヤーと
// 全レイヤーの和を使う（共通 → 自身 → プロジェクトの名前順）。無いレイヤーは空として扱う。不備ではないので、
// 記録にも lint にも出さない。壊れたレイヤーも空として扱い、記録の fallback に名前を残す。
// 共通レイヤー自身が読めないときだけ、組み込みの既定に戻し、レイヤーは足さない。足すと、何が判定に
// 使われているのかをユーザが読めなくなるためである。共通レイヤーもレイヤーのファイルも無ければ、
// 組み込みの既定（Read を通す allow を含む）だけで判定する。
//
// Python からの対応: ruleload。rules_for → RulesFor、load_rules → LoadRules、add_layers → AddLayers、
// layers・layer_for・unreadable → Layers・LayerFor・Unreadable、survey・sum_view・LayerView → Survey・SumView・
// LayerView、layer_files → LayerFiles、stop_rules・deny_ids → StopRules・DenyIDs（集合は map）、relpath → RelPath。
// LAYER_COMMON・LAYER_SELF は config.LayerCommon・LayerSelf を直に使う。read_common・add_base・merge_rules・
// mark_*・prefix_ids は私的にした（上の段が使わないため）。
package ruleload
