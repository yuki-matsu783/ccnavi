#!/bin/sh
# ccnavi-build は、ccnavi の実行ファイルを Go で組み立て、dist/ccnavi/ に置き、hook が起動する
# .ccnavi/bin/<os>-<arch>/ へコピーする。ccnavi のリポジトリ（ソース）で打つ。
#
#   sh scripts/ccnavi-build.sh [--target <os>-<arch>]
#
#   --target <os>-<arch>  組み立てる機械。既定はこの機械。linux・darwin・windows と x86_64・arm64 の組
#                         （例 windows-x86_64）。Go は別の機械向けにもそのまま組める
#
# 語は .ccnavi/scripts/ccnavi-launcher.sh・scripts/ccnavi-setup.sh の host_target・
# go/internal/infra/platformtag と揃える。どれかだけ変えると、配った場所と探す場所が食い違う。
#
# 組み立ての元のコミットを実行ファイルに埋める（`ccnavi --version` の commit）。git に聞くのはここだけで、
# 実行ファイルは聞かない。未コミットの変更があれば -dirty を付け、git が無い・リポジトリでないときは unknown。
#
# 置く順: build/stage/ccnavi/ に組む → dist/ccnavi/ と入れ替える → dist/ccnavi.target を書く →
# .ccnavi/bin/<target>.new にコピーしてから .ccnavi/bin/<target>/ と入れ替える。
# dist/ccnavi.target は dist/ccnavi/ の版の目印なので、dist/ の入れ替えが通るまで書き換えない。
# 振り分けの sh は .ccnavi/bin/ を探すので、コピーするまで hook は新しい実行ファイルを起動しない。
#
# 終了コード: 0 成功 / 1 置き場への入れ替えかコピーに失敗 / 2 引数の誤り・前提が足りない / ほか go build の終了コード

set -eu

die() {
	printf 'ccnavi-build: %s\n' "$1" >&2
	exit "${2:-2}"
}

# 組み上がった <新> を <置き場> と入れ替える。
# Windows は走っている実行ファイルを上書きできないが、名前の変更はできる。置き場を退避してから新しいほうを
# 移し、退避した側を消す。hook がちょうど走っている瞬間に重なることがあるので、数回やり直す。
# やり直しきれなかったら、退避した側を置き場に戻して 1 を返す。
swap() {
	sw_new=$1
	sw_live=$2
	sw_old="$sw_live.old"
	mkdir -p "$(dirname "$sw_live")" || return 1
	rm -rf "$sw_old" && [ ! -e "$sw_old" ] || return 1
	sw_try=1
	while :; do
		if { [ ! -e "$sw_live" ] || mv "$sw_live" "$sw_old"; } && mv "$sw_new" "$sw_live"; then
			break
		fi
		if [ "$sw_try" -ge 5 ]; then
			if [ -e "$sw_old" ] && [ ! -e "$sw_live" ]; then
				mv "$sw_old" "$sw_live" || :
			fi
			return 1
		fi
		sw_try=$((sw_try + 1))
		sleep 1
	done
	rm -rf "$sw_old"
}

# 組み立ての元のコミット。
source_commit() {
	sc_head=$(git -C "$1" rev-parse HEAD 2>/dev/null) || sc_head=""
	if [ -z "$sc_head" ]; then
		printf 'unknown\n'
		return 0
	fi
	sc_changed=$(git -C "$1" status --porcelain 2>/dev/null) || {
		printf 'unknown\n'
		return 0
	}
	if [ -n "$sc_changed" ]; then
		printf '%s-dirty\n' "$sc_head"
	else
		printf '%s\n' "$sc_head"
	fi
}

main() {
	target=""
	while [ "$#" -gt 0 ]; do
		case "$1" in
		--target)
			[ "$#" -ge 2 ] || die "--target に値がありません。"
			target=$2
			shift 2
			;;
		-h | --help | help)
			printf '%s\n' \
				'sh scripts/ccnavi-build.sh [--target <os>-<arch>]' \
				'' \
				'  --target <os>-<arch>  組み立てる機械（既定はこの機械。例 windows-x86_64）' \
				'終了コード: 0 成功 / 1 置き場への入れ替えかコピーに失敗 / 2 引数の誤り・前提が足りない'
			return 0
			;;
		*) die "$1 は指定できない引数です。指定できるものは --help で確認できます。" ;;
		esac
	done

	root=$(cd "$(dirname "$0")/.." && pwd)
	[ -f "$root/go/cmd/ccnavi/main.go" ] || die "$root は ccnavi のソースに見えません（go/cmd/ccnavi/main.go が無い）。"
	command -v go >/dev/null 2>&1 || die "go が必要です（https://go.dev/dl/ から入れてください）。"

	if [ -z "$target" ]; then
		host_os=$(cd "$root/go" && go env GOHOSTOS | tr -d '\r') || die "go env が失敗しました。"
		host_arch=$(cd "$root/go" && go env GOHOSTARCH | tr -d '\r') || die "go env が失敗しました。"
		[ "$host_arch" != amd64 ] || host_arch=x86_64
		target="$host_os-$host_arch"
	fi
	case "$target" in
	linux-* | darwin-* | windows-*) goos=${target%%-*} ;;
	*) die "--target の OS が読めません（${target}）。linux・darwin・windows のどれかです。" ;;
	esac
	case "$target" in
	*-x86_64) goarch=amd64 ;;
	*-arm64) goarch=arm64 ;;
	*) die "--target の CPU が読めません（${target}）。x86_64・arm64 のどちらかです。" ;;
	esac
	name=ccnavi
	[ "$goos" != windows ] || name=ccnavi.exe

	commit=$(source_commit "$root")
	stage="$root/build/stage/ccnavi"
	rm -rf "$stage"
	mkdir -p "$stage" || die "$stage を作れません。" 1
	# CGO を切ると、別の機械向けにも C の道具なしで組め、配布先の libc の版にも縛られない。
	(cd "$root/go" && CGO_ENABLED=0 GOOS=$goos GOARCH=$goarch go build -trimpath \
		-ldflags "-X ccnavi/internal/entry/version.commit=$commit" -o "$stage/$name" ./cmd/ccnavi) || exit $?

	if ! swap "$stage" "$root/dist/ccnavi"; then
		printf 'ccnavi-build: dist/ccnavi/ へ置き換えられなかった\n' >&2
		printf '前の版は dist/ccnavi/ か dist/ccnavi.old/ にある\n' >&2
		printf '組み立てた版は %s にある\n' "$stage" >&2
		return 1
	fi
	printf '%s\n' "$target" >"$root/dist/ccnavi.target"
	printf 'built %s (%s, %s)\n' "$root/dist/ccnavi/$name" "$target" "$commit"

	live="$root/.ccnavi/bin/$target"
	rm -rf "$live.new"
	if ! { mkdir -p "$live.new" && cp -p "$root/dist/ccnavi/$name" "$live.new/$name" && swap "$live.new" "$live"; }; then
		rm -rf "$live.new"
		printf 'ccnavi-build: .ccnavi/bin/%s へコピーできなかった\n' "$target" >&2
		printf 'dist/ は新しい。.ccnavi/bin/%s/ は前のまま\n' "$target" >&2
		return 1
	fi
	printf 'installed %s\n' "$live"
	return 0
}

main "$@"; exit $?
