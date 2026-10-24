#!/bin/sh
# ccnavi-user-handoff wip/handoff/ の下書きを、保護対象の本来の場所へ取り込む。ユーザが端末で打つ。
#
#   sh .ccnavi/scripts/ccnavi-user-handoff.sh [--root <ツリー>] [<相対パス>…] [--yes --expect <ハッシュ>]
#
# ユーザだけが打つ sh は ccnavi-user-*.sh に分けてあり、エージェントからは hook が名前で止める。
# 中身は実行ファイルの `user handoff` で、引数はそのまま渡す。ユーザが打つ入口を sh にそろえるための
# 薄い包みで、実行ファイルに入口の印（`--entry sh`）を付けて起こす。印が無い `user` の入口は、
# 実行ファイルが直に打った形として断る。
# 引数なしは取り込み待ちの一覧を見せるだけ。取り込むときは `--yes --expect <ハッシュ>` を添える。
#
# 終了コード: 実行ファイルの `user handoff` のもの。2 は引数か環境の誤り

set -eu

# 共通部分。ワークスペースルートの探し方と実行ファイルの探し方はここにある。
. "$(dirname "$0")/ccnavi-common.sh"

usage() {
	cat <<'USAGE'
sh .ccnavi/scripts/ccnavi-user-handoff.sh [--root <ツリー>] [<相対パス>…] [--yes --expect <ハッシュ>]

  wip/handoff/ の下書きを、保護対象の本来の場所へ取り込む。
  実行ファイルの `user handoff` を、入口の印つきで起こす。ユーザが端末で実行します。
USAGE
}

# 先頭の --root <ツリー> は、下書き（wip/handoff/）のあるツリーを指す（ボードが出す取り込みのコマンドが付ける）。
# 実行ファイルへは、入口の印より前に渡す。
exe_root=""
if [ "${1:-}" = "--root" ] && [ $# -ge 2 ]; then
	exe_root="$2"
	shift 2
fi

if [ $# -eq 1 ]; then
	case "$1" in
	-h | --help | help)
		usage
		exit 0
		;;
	esac
fi

# 端末をワークスペースの外で開いていても届くように、cwd から見つからなければ、この sh のある場所から決める。
root=$(ccnavi_workspace) || root=$(ccnavi_abs "$(dirname "$0")/../..") || {
	printf 'ccnavi-user-handoff: ワークスペースルートが見つかりません（.ccnavi/scripts/ccnavi-common.sh を持つ親を cwd から上へ探しました）。ワークスペースの中で実行するか、CCNAVI_WORKSPACE にワークスペースルートの絶対パスを渡してください。\n' >&2
	exit 2
}

# 実行ファイルは、打った人の cwd のまま起こす（取り込み先のツリーと根は、実行ファイルが cwd から決める）。
# ソースで動かすときだけ、uv にワークスペースルートのプロジェクトを教える。
if bin=$(ccnavi_bin "$root"); then
	exec "$bin" ${exe_root:+--root "$exe_root"} --entry sh user handoff "$@"
elif [ -f "$root/src/ccnavi/__main__.py" ] && command -v uv >/dev/null 2>&1; then
	exec uv run --quiet --project "$root" python -m ccnavi ${exe_root:+--root "$exe_root"} --entry sh user handoff "$@"
fi
printf 'ccnavi-user-handoff: ccnavi の実行ファイルが見つかりません（CCNAVI_ENTRY_PATH・dist/ccnavi/ccnavi・.ccnavi/bin/・PATH のどれにもありません）。build.py でビルドするか、scripts/ccnavi-setup.sh で実行ファイルを配置してください。\n' >&2
exit 2
