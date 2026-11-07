// Package layer は、フェーズ定義（phases）・配点（risk）・設定の検査（check）が同じ規則で引く、レイヤーの置き場を決める。
// レイヤーは、自身（ワークスペース）と、予約名でない各プロジェクトである。
//
// Python からの対応: phasetypes.types_path と risk.definition_path → File、check.layer_home と
// `tree.project_root(conf.projects, project) if project else root` → Home、check.layer_names → Names。
package layer
