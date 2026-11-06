package selfguard

import (
	"crypto/sha1"
	"crypto/sha256"
	"encoding/hex"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/fsio"
	"ccnavi/internal/infra/git"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pathcase"
	"ccnavi/internal/infra/platformtag"
	"ccnavi/internal/infra/tree"
)

// PathKey は PATH の ccnavi の実体の key の頭。パスの印が続く。PATH が変わって指す先が替わったとき、
// 別の実体の参照を新しい実体に書き戻さないためである。
const PathKey = "bin-path-"

// 実行ファイルの key。
const (
	binKey         = "bin"
	binLaunchedKey = "bin-launched"
)

// settingsFiles は守る設定ファイル（root からの相対）。rules は設定で動くのでここに無い。
var settingsFiles = []struct{ key, rel string }{
	{"settings", ospath.Join(".claude", "settings.json")},
	{"settings-local", ospath.Join(".claude", "settings.local.json")},
}

// Target は守る対象 1 つ。
type Target struct {
	// Key はバックアップの名前。対象ごとに固定する（パスから作ると、置き場を動かしただけで直前の断面を見失う）。
	Key string
	// Path は行き着く先まで解いた絶対パス。
	Path string
	// Label は報告に出す root からの相対。
	Label string
	// Heavy は毎回読むには大きすぎる対象（実行ファイル）。開始でバックアップし、照合は大きさと更新時刻で行う。
	Heavy bool
	// Top は git から戻すときに渡すルート。空なら root の側。ワークツリー側の設定とプロジェクトのレイヤーは、
	// それを追跡している自分の git に聞く（ワークスペースの git は持っていない）。
	Top string
	// Copy はワークツリー側の設定であること（今は誰も読まないが、統合で反映される。報告の文が分かれる）。
	Copy bool
}

// place は守る対象の (key, 追跡している git プロジェクトルート, 絶対パス)。
type place struct{ key, home, path string }

// Targets は守る対象を組み立てる。rulesPath と binPath は解決済みのパスで、空ならその対象を持たない。
// layers はレイヤーごとの設定ファイルを渡す。共通レイヤーの rules は rulesPath が渡すので、重ねない。
func Targets(root, rulesPath, binPath string, layers []config.LayerFile, projectsDir string) []Target {
	places := collectPlaces(root, projectsDir, rulesPath, layers)
	var found []Target
	for _, p := range places {
		found = append(found, Target{Key: p.key, Path: p.path, Label: relative(root, p.path), Top: homeTop(root, p.home)})
	}
	if binPath != "" {
		full := ospath.Realpath(binPath)
		found = append(found, Target{Key: binKey, Path: full, Label: relative(root, full), Heavy: true})
		// 振り分けの sh なら、hook が実際に走らせる `../bin/` の実体も守る。
		if launched := platformtag.LaunchedExecutable(binPath); launched != "" {
			real := ospath.Realpath(launched)
			found = append(found, Target{Key: binLaunchedKey, Path: real, Label: relative(root, real), Heavy: true})
		}
		// bin/ に実体が無いと sh は PATH の ccnavi を起動する。bin/ があるときも守る（途中で現れても組が変わらない）。
		onPath := ""
		if platformtag.IsLauncher(binPath) {
			onPath = platformtag.PathExecutable(binPath)
		}
		if onPath != "" {
			real := ospath.Realpath(onPath)
			known := false
			for _, t := range found {
				known = known || t.Path == real
			}
			if !known {
				found = append(found, Target{Key: PathKey + pathMark(real), Path: real, Label: real, Heavy: true})
			}
		}
	}
	return append(found, worktreeCopies(root, projectsDir, places)...)
}

// collectPlaces は設定ファイルと各レイヤーの場所。同じ key が二度来たら後ろを捨てる（同じバックアップを書き換え合う）。
func collectPlaces(root, projectsDir, rulesPath string, layers []config.LayerFile) []place {
	var found []place
	for _, f := range settingsFiles {
		found = append(found, place{f.key, root, ospath.Join(root, f.rel)})
	}
	if rulesPath != "" {
		found = append(found, place{commonRulesKey, root, rulesPath})
	}
	for _, l := range layers {
		if l.Path == "" {
			continue
		}
		found = append(found, place{layerKey(l.Origin, l.Layer, l.Kind), layerHome(root, projectsDir, l.Origin, l.Layer), l.Path})
	}
	seen := map[string]bool{}
	var places []place
	for _, p := range found {
		if seen[p.key] {
			continue
		}
		seen[p.key] = true
		places = append(places, place{p.key, p.home, ospath.Realpath(p.path)})
	}
	return places
}

// layerKey はレイヤーのバックアップの key。名前ではなく種別で決める。`projects/common/` が共通レイヤーと同じ key に
// なると、重なりの排除でその 3 本が保護から外れるためである。予約名のプロジェクトとミラーは置き場の前置きを付ける。
func layerKey(origin config.Origin, layer string, kind config.LayerKind) string {
	switch {
	case origin == config.OriginCommon:
		return string(kind)
	case origin == config.OriginMirror:
		return string(kind) + ":" + config.MirrorKeyHome + layer
	case origin == config.OriginProject && config.IsReservedLayerName(layer):
		return string(kind) + ":" + config.ProjectKeyHome + layer
	}
	return string(kind) + ":" + layer
}

// layerHome はそのレイヤーを追跡している git プロジェクトルート。プロジェクトとミラーはそのプロジェクト、ほかはルート。
func layerHome(root, projectsDir string, origin config.Origin, layer string) string {
	if origin != config.OriginProject && origin != config.OriginMirror {
		return root
	}
	if home := tree.ProjectRoot(projectsDir, layer); home != "" {
		return home
	}
	return root
}

// homeTop は git から戻すときに渡すルート。ワークスペースルートなら空。
func homeTop(root, home string) string {
	if home == "" || ospath.Realpath(home) == ospath.Realpath(root) {
		return ""
	}
	return home
}

// worktreeCopies はワークツリー側の設定を組む。元リポジトリが追跡しているぶんだけを、元リポジトリからの相対で組む
// （相互参照の揃ったワークツリーだけ。元リポジトリの外を指す置き場には、対応するものが無いため）。
func worktreeCopies(root, projectsDir string, places []place) []Target {
	type rel struct{ key, rel string }
	byHome := map[string][]rel{}
	for _, p := range places {
		if r := inside(p.home, p.path); r != "" {
			h := ospath.Realpath(p.home)
			byHome[h] = append(byHome[h], rel{p.key, r})
		}
	}
	var copies []Target
	for _, work := range tree.Worktrees(root, projectsDir) {
		home := root
		if work.Project != "" {
			home = tree.ProjectRoot(projectsDir, work.Project)
		}
		for _, r := range byHome[ospath.Realpath(home)] {
			full := ospath.Realpath(ospath.Join(work.Root, r.rel))
			copies = append(copies, Target{
				Key: copyKey(r.key, work.Name), Path: full, Label: relative(root, full), Top: work.Root, Copy: true,
			})
		}
	}
	return copies
}

// copyKey はワークツリー側の設定のバックアップの名前。使えない字を置き換えた名前に、元の名前の digest を付ける。
// 置き換えだけだと `a/b` と `a_b` が同じ名前になり、別のワークツリーの中身を書き戻してしまうためである。
func copyKey(key, name string) string {
	sum := sha256.Sum256([]byte(name))
	return key + "." + fsio.SafeName(name, 40) + "-" + hex.EncodeToString(sum[:])[:8]
}

// inside は path が root の下なら root からの相対、外なら空。
func inside(root, path string) string {
	if path == "" {
		return ""
	}
	rel := relative(root, ospath.Realpath(path))
	if ospath.IsAbs(rel) || rel == ".." || strings.HasPrefix(rel, ".."+ospath.Sep) {
		return ""
	}
	return rel
}

// gitTop は対象を git から戻すときに渡すワークツリーのルート。
func gitTop(root string, t Target) string {
	if t.Top != "" {
		return t.Top
	}
	return git.TopLevel(root)
}

// relative は base（行き着く先まで解く）からの相対。相対にできない（別のドライブ）なら path のまま。
func relative(base, path string) string {
	if rel, ok := ospath.Relpath(path, ospath.Realpath(base)); ok {
		return rel
	}
	return path
}

// pathMark は PATH の実体の key に付けるパスの印。
func pathMark(real string) string {
	sum := sha1.Sum([]byte(pathcase.Fold(real)))
	return hex.EncodeToString(sum[:])[:8]
}
