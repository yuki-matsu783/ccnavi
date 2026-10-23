#!/bin/sh
# ccnavi-user-clean-branch 親を閉じたときに残した子の手元のブランチを、条件を確かめ直して消す。ユーザが端末で打つ。
#
#   sh .ccnavi/scripts/ccnavi-user-clean-branch.sh <子の識別子> [--confirmed]
#
# 前は `ccnavi-clean.sh --branch <子の識別子> [--confirmed]` だった。ユーザだけが打つ sh は ccnavi-user-*.sh に
# 分けてあり、エージェントからは hook が名前で止める（エージェントが --confirmed を単なる語として添えても、
# 取り消した子のブランチを確認なしで消せないようにする）。
#
# 中身は実行ファイルの `worktree branch <名前> [confirmed]`。親を閉じたときに ccnavi が片付ける子として記録した
# 手元のブランチだけを消す。合流した子は、ブランチの先頭が合流した先頭と同じで、その先頭が push 済みの親に
# 含まれるときだけ消える。取り消した子は --confirmed（ユーザが消してよいと言ったこと）が要る。記録に無いブランチと、
# チケットの parent: で記録の親の子と確かめられないブランチは消さない。条件の確認も削除も実行ファイルが行う。
# `git branch -D` を直に打たせないための入口。
#
# <子の識別子> は .claude/worktrees/ の直下の名前と同じ形。パスは受け付けない。
#
# 終了コード: 実行ファイルの `worktree branch` のもの（0 成功 / 1 前提の未充足）。2 引数か環境の誤り

set -eu

# 共通部分。ワークスペースルートの探し方はここにある。
. "$(dirname "$0")/ccnavi-common.sh"

usage() {
	cat <<'USAGE'
sh .ccnavi/scripts/ccnavi-user-clean-branch.sh <子の識別子> [--confirmed]

  親を閉じたときに片付ける子として記録した手元のブランチを、条件を確かめて削除します。
  取り消した子は --confirmed（ユーザが確認した）が要る。ユーザが端末で実行します。
USAGE
}

name=""
confirmed=""
for arg in "$@"; do
	case "$arg" in
	-h | --help | help)
		usage
		exit 0
		;;
	--confirmed)
		confirmed=1
		;;
	-*)
		printf 'ccnavi-user-clean-branch: %s は受け付けません。使えるのは --confirmed だけです。\n' "$arg" >&2
		exit 2
		;;
	*)
		if [ -n "$name" ]; then
			printf 'ccnavi-user-clean-branch: ブランチは 1 回に 1 本だけ指定します（%s と %s）。\n' "$name" "$arg" >&2
			exit 2
		fi
		name="$arg"
		;;
	esac
done

[ -n "$name" ] || {
	usage >&2
	exit 2
}

case "$name" in
. | .. | */* | *\\* | *:*)
	printf 'ccnavi-user-clean-branch: 名前にパスは書けません（%s）。子の識別子だけを渡してください。\n' "$name" >&2
	exit 2
	;;
esac

root=$(ccnavi_workspace) || {
	printf 'ccnavi-user-clean-branch: ワークスペースルートが見つかりません（.ccnavi/scripts/ccnavi-common.sh を持つ親を cwd から上へ探しました）。ワークスペースの中で実行するか、CCNAVI_WORKSPACE にワークスペースルートの絶対パスを渡してください。\n' >&2
	exit 2
}

here="$(pwd -W 2>/dev/null || pwd)"
if bin=$(ccnavi_bin "$root"); then
	exec "$bin" --root "$root" --cwd "$here" worktree branch "$name" ${confirmed:+confirmed}
elif [ -f "$root/src/ccnavi/__main__.py" ]; then
	cd "$root"
	exec uv run python -m ccnavi --root "$root" --cwd "$here" worktree branch "$name" ${confirmed:+confirmed}
fi
printf 'ccnavi-user-clean-branch: ccnavi の実行ファイルが見つかりません（CCNAVI_BIN_PATH・dist/ccnavi/ccnavi・.ccnavi/bin/・PATH のどれにもありません）。build.py でビルドするか、scripts/ccnavi-setup.sh で実行ファイルを配置してください。\n' >&2
exit 2
