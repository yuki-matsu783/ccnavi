package version

import (
	"fmt"
	"io"
	"slices"
	"strings"

	"ccnavi/internal/infra/config"
	"ccnavi/internal/infra/pyjson"
	"ccnavi/internal/policy/rules"
	"ccnavi/internal/tickets/config/phases"
	"ccnavi/internal/tickets/config/risk"
	"ccnavi/internal/tickets/model/ticket"
)

// Version は ccnavi の版。pyproject.toml の version と揃える（テストが見る）。
const Version = "0.1.0"

// Compat は実行ファイルと sh・拡張の契約の版。呼ぶ側が頼っているフラグや出力の形を、呼ぶ側を直さないと動かない形に
// 変えたとき、データの形（承認済みの置き場の並び・先行関係の読み方など）が変わったとき、sh が新しいサブコマンドを
// 呼ぶようになったときに 1 上げる。
// 20: sh が `ccnavi --check ticket start|finish|cancel|consolidate` を呼ぶ。decide の JSON から判断欄を消し、`review` を
// 真偽値にして `deferred` を足した。ボード JSON の欄を必須にした。
const Compat = 20

// Schema は `--version --json` の形の版。欄を足すだけなら上げない。
const Schema = 1

// Unknown は組み立ての元のコミットが分からないときの表記。
const Unknown = "unknown"

// commit は組み立ての元のコミット。組み立てが次のように埋める（git が無いなどで分からなければ Unknown を埋める）。
//
//	go build -ldflags "-X ccnavi/internal/entry/version.commit=<sha>[-dirty]" ./cmd/ccnavi
//
// 空なら埋めていない（go run・go test で動いている）。
var commit string

// built は組み立てた実行ファイルとして動いているか（commit を埋めたか）。
func built() bool { return commit != "" }

func commitOrUnknown() string {
	if commit == "" {
		return Unknown
	}
	return commit
}

// formats は読む設定とチケットの書式の版（レイヤーのファイルの `version:` と比べるもの）。キーの名前の順。
func formats() *pyjson.Object {
	return pyjson.NewObject(
		"phases", phases.Version,
		"risks", risk.Version,
		"rules", rules.Version,
		"ticket", ticket.Version,
	)
}

func describe() *pyjson.Object {
	return pyjson.NewObject(
		"schema", Schema,
		"version", Version,
		"commit", commitOrUnknown(),
		"built", built(),
		"compat", Compat,
		"formats", formats(),
	)
}

// Report は版を書く（`ccnavi --version [--json]`）。ユーザ向けの形も 1 行 1 項目の `<名前>: <値>` にして、sh が sed で
// 読めるようにする。
func Report(stdout io.Writer, asJSON bool) int {
	if asJSON {
		body, err := pyjson.Dumps(describe(), pyjson.Options{Indent: 1})
		if err != nil {
			// 中身はこのパッケージの定数だけなので届かない。
			return config.ExitError
		}
		fmt.Fprint(stdout, body+"\n")
		return config.ExitOK
	}
	how, yes := "ソースで動いている", "no"
	if built() {
		how, yes = "組み立てた実行ファイル", "yes"
	}
	f := formats()
	names := f.Keys()
	slices.Sort(names)
	pairs := make([]string, 0, len(names))
	for _, name := range names {
		v, _ := f.Get(name)
		pairs = append(pairs, fmt.Sprintf("%s=%v", name, v))
	}
	fmt.Fprintf(stdout, "ccnavi %s\n", Version)
	fmt.Fprintf(stdout, "commit: %s\n", commitOrUnknown())
	fmt.Fprintf(stdout, "built: %s (%s)\n", yes, how)
	fmt.Fprintf(stdout, "compat: %d\n", Compat)
	fmt.Fprintf(stdout, "schema: %d\n", Schema)
	fmt.Fprintf(stdout, "formats: %s\n", strings.Join(pairs, " "))
	return config.ExitOK
}
