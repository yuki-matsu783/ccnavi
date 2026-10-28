#!/bin/sh
# ccnavi-user-mirror 共通レイヤーのミラーと工程スキル（skills/）がずれていないかを見て、プロジェクトへ揃える。ユーザが端末で打つ。
#
#   sh .ccnavi/scripts/ccnavi-user-mirror.sh [<プロジェクト名>…] [--yes --expect <値>]
#
# ユーザだけが打つ sh は ccnavi-user-*.sh に分けてあり、エージェントからは hook が名前で止める。
# 中身は実行ファイルの `user mirror` で、引数はそのまま渡す。ユーザが打つ入口を sh にそろえるための
# 薄い包みで、実行ファイルに入口の印（`--entry sh`）を付けて起こす。印が無い `user` の入口は、
# 実行ファイルが直に打った形として断る。
# 引数なしはずれを見せるだけ。揃えるときは `--yes --expect <値>` を添える。
#
# 終了コード: 実行ファイルの `user mirror` のもの。2 は引数か環境の誤り

set -eu

# 共通部分。ワークスペースルートの探し方と実行ファイルの探し方はここにある。
. "$(dirname "$0")/ccnavi-common.sh"

usage() {
	cat <<'USAGE'
sh .ccnavi/scripts/ccnavi-user-mirror.sh [<プロジェクト名>…] [--yes --expect <値>]

  共通レイヤーのミラーと工程スキル（skills/）がずれていないかを見て、プロジェクトへ揃える。
  実行ファイルの `user mirror` を、入口の印を付けて起こす。ユーザが端末で実行します。
USAGE
}

if [ $# -eq 1 ]; then
	case "$1" in
	-h | --help | help)
		usage
		exit 0
		;;
	esac
fi

# ワークスペースの外で開いた端末からも動くように、cwd から見つからなければ、この sh のある場所からルートを決める。
root=$(ccnavi_workspace) || root=$(ccnavi_abs "$(dirname "$0")/../..") || {
	printf 'ccnavi-user-mirror: ワークスペースルートが見つかりません（.ccnavi/scripts/ccnavi-common.sh を持つ親を cwd から上へ探しました）。ワークスペースの中で実行するか、CCNAVI_WORKSPACE にワークスペースルートの絶対パスを渡してください。\n' >&2
	exit 2
}

# 実行ファイルは、打った人の cwd のまま起こす（取り込み先のツリーと根は、実行ファイルが cwd から決める）。
# ソースで動かすときだけ、uv にワークスペースルートのプロジェクトを教える。
if bin=$(ccnavi_bin "$root"); then
	skew=$(ccnavi_compat_skew "$root" "$bin") || printf 'ccnavi-user-mirror: %s\n' "$skew" >&2
	exec "$bin" --entry sh user mirror "$@"
elif [ -f "$root/src/ccnavi/__main__.py" ] && command -v uv >/dev/null 2>&1; then
	exec uv run --quiet --project "$root" python -m ccnavi --entry sh user mirror "$@"
fi
printf 'ccnavi-user-mirror: ccnavi の実行ファイルが見つかりません（CCNAVI_ENTRY_PATH・dist/ccnavi/ccnavi・.ccnavi/bin/・PATH のどれにもありません）。build.py でビルドするか、scripts/ccnavi-setup.sh で実行ファイルを配置してください。\n' >&2
exit 2
