package layer

import (
	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/tree"
)

// Name は設定の名前。空の project はワークスペースの設定（self）。
func Name(project string) string {
	if project == "" {
		return config.LayerSelf
	}
	return project
}

// Project は名前を project の値に戻す。ワークスペースの設定は空。
func Project(name string) string {
	if name == config.LayerSelf {
		return ""
	}
	return name
}

// Home はその設定の git プロジェクトルート。空の project はワークスペースルート。見つからなければ空。
func Home(conf *config.Settings, root, project string) string {
	if project == "" {
		return root
	}
	return tree.ProjectRoot(conf.Projects, project)
}

// File はそのプロジェクトの設定のファイル（kind の種類）。予約名（common / self）のプロジェクトは
// 設定として数えないので空（名前で引くとワークスペースの設定として合成され、設定を書ける側が
// 設定を選べてしまう）。
func File(conf *config.Settings, root, project string, kind config.LayerKind) string {
	if config.IsReservedLayerName(project) {
		return ""
	}
	home := Home(conf, root, project)
	if home == "" {
		return ""
	}
	return conf.LayerPath(home, kind, Name(project))
}

// Names はワークスペースの設定と、予約名でないプロジェクトの設定の名前。
func Names(conf *config.Settings) []string {
	names := []string{config.LayerSelf}
	for _, p := range tree.Projects(conf.Projects) {
		if !config.IsReservedLayerName(p.Name) {
			names = append(names, p.Name)
		}
	}
	return names
}
