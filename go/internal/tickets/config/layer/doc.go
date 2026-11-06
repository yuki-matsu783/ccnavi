// Package layer は、フェーズ定義（phases）と配点（risk）と設定の検査（check）が同じ規則で引く、レイヤーの
// 置き場を決める。レイヤーは自身（ワークスペース）と、予約名でない各プロジェクト。
//
// Python からの対応: phasetypes.types_path と risk.definition_path → File、check.layer_home と
// `tree.project_root(conf.projects, project) if project else root` → Home、check.layer_names → Names。
package layer
