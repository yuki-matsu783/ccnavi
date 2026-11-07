package platformtag

import (
	"os"
	"path/filepath"
	"runtime"
	"slices"
	"strings"

	"ccnavi/internal/infra/env"
	"ccnavi/internal/infra/ospath"
	"ccnavi/internal/infra/pyfmt"
)

// systems は組み立ての語として読めるディレクトリ名の OS の側。
var systems = []string{"darwin", "linux", "windows"}

// Systems は systems の写しを返す（呼び手が書き換えても元は変わらない）。
func Systems() []string { return slices.Clone(systems) }

// executableNames は実行ファイルの名前。Windows の組み立てだけが `.exe` を付ける。
var executableNames = []string{"ccnavi", "ccnavi.exe"}

// LauncherName は振り分けの sh の名前。この名前なら実体は `../bin/<語>/` に在り、ほかの名前は
// 実行ファイルそのものとして読む。名前だけで切り替える。
const LauncherName = "ccnavi-launcher.sh"

const binDir = "bin"

// HostTarget はこの機械の `<os>-<arch>`。
func HostTarget() string {
	system := runtime.GOOS
	if ospath.Windows {
		system = "win32"
	}
	return hostTarget(system, runtime.GOARCH)
}

// hostTarget は OS の名前（win32・darwin・linux で始まる名前）と CPU の名前（uname -m の綴りも Go の GOARCH の綴りも）から `<os>-<arch>` の語を作る。
func hostTarget(system, machine string) string {
	name := system
	switch {
	case system == "win32":
		name = "windows"
	case strings.HasPrefix(system, "linux"):
		name = "linux"
	}
	arch := pyfmt.Lower(machine)
	switch arch {
	case "amd64", "x86_64":
		arch = "x86_64"
	case "arm64", "aarch64":
		arch = "arm64"
	case "":
		arch = "unknown"
	}
	return name + "-" + arch
}

// runnableTargets はこの機械で動く組み立ての語を、先に選ぶ順に並べる。arm64 の macOS と Windows は
// x86_64 を変換して動かせるので、自分向けが無いときだけそちらに回す。
func runnableTargets(host string) []string {
	switch host {
	case "darwin-arm64":
		return []string{host, "darwin-x86_64"}
	case "windows-arm64":
		return []string{host, "windows-x86_64"}
	}
	return []string{host}
}

// IsLauncher はこのパスが振り分けの sh か（名前だけで決める）。
func IsLauncher(path string) bool {
	return ospath.Basename(path) == LauncherName
}

// PathExecutable は PATH から見つかる ccnavi の実行ファイル。無ければ空。sh が bin/ に何も
// 見つけなかったときに起動するもの。skip（振り分けの sh）と同じファイルは飛ばす（起動すると自分に戻る）。
func PathExecutable(skip string) string {
	return pathExecutable(os.Getenv(env.Path), skip)
}

// pathExecutable は sh と同じ規則で探す。絶対パスの項目だけを書かれた順に見て（空と相対は cwd で
// 答えが変わるので飛ばす）、実行できる通常ファイルの最初のもの。リンクは解かない。
func pathExecutable(pathEnv, skip string) string {
	for _, place := range strings.Split(pathEnv, string(filepath.ListSeparator)) {
		if place == "" || !ospath.IsAbs(place) {
			continue
		}
		for _, name := range executableNames {
			found := ospath.Join(place, name)
			if !ospath.IsFile(found) || !executable(found) {
				continue
			}
			if skip != "" && sameFile(found, skip) {
				continue
			}
			return found
		}
	}
	return ""
}

func sameFile(a, b string) bool {
	ia, err := os.Stat(a)
	if err != nil {
		return false
	}
	ib, err := os.Stat(b)
	return err == nil && os.SameFile(ia, ib)
}

// LaunchedExecutable は振り分けの sh がこの機械で起動する実行ファイル。無ければ空。
func LaunchedExecutable(launcher string) string {
	return launchedExecutable(launcher, HostTarget())
}

// launchedExecutable は名前が LauncherName のときだけ `../bin/` を探し、隣は見ない（sh が起動しない
// 置き場を返すと、バックアップする実体と走る実体が別になる）。`..` は sh と同じく解かない。
func launchedExecutable(launcher, host string) string {
	if ospath.Basename(launcher) != LauncherName {
		return ""
	}
	places := ospath.Join(ospath.Dirname(ospath.Dirname(launcher)), binDir)
	for _, target := range runnableTargets(host) {
		for _, name := range executableNames {
			found := ospath.Join(places, target, name)
			if ospath.IsFile(found) {
				return found
			}
		}
	}
	return ""
}
