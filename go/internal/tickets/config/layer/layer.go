package layer

import (
	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/tree"
)

// Name はレイヤーの名前。空の project はワークスペース自身のレイヤー（self）。
func Name(project string) string {
	if project == "" {
		return config.LayerSelf
	}
	return project
}

// Project は名前を project の値に戻す。自身のレイヤーは空。
func Project(name string) string {
	if name == config.LayerSelf {
		return ""
	}
	return name
}

// Home はそのレイヤーの git プロジェクトルート。空の project はワークスペースルート。見つからなければ空。
func Home(conf *config.Settings, root, project string) string {
	if project == "" {
		return root
	}
	return tree.ProjectRoot(conf.Projects, project)
}

// File はそのプロジェクトのレイヤーの設定ファイル（kind の種類）。予約名（common / self）のプロジェクトは
// レイヤーとして数えないので空（名前で引くとワークスペース自身のレイヤーとして合成され、設定を書ける側が
// レイヤーを選べてしまう）。
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

// Names は自身のレイヤーと、予約名でないプロジェクトのレイヤーの名前。
func Names(conf *config.Settings) []string {
	names := []string{config.LayerSelf}
	for _, p := range tree.Projects(conf.Projects) {
		if !config.IsReservedLayerName(p.Name) {
			names = append(names, p.Name)
		}
	}
	return names
}
