#!/bin/sh
# ccnavi-ticket — チケットの状態を動かす。親（メインエージェント）だけが呼ぶ。
#
#   sh .claude/scripts/ccnavi-ticket.sh start  <識別子>
#   sh .claude/scripts/ccnavi-ticket.sh done   <識別子>
#   sh .claude/scripts/ccnavi-ticket.sh cancel <識別子> --reason <理由>
#
# 状態は置き場（wip/tickets/{todo,doing,done,cancelled}/）で表す。動かすのはこの
# スクリプトだけで、直接ファイルを作ったり動かしたりするのは ccnavi が止める。
# サブエージェントからの呼び出しも ccnavi が止める（agent_id が付いていたら拒む）。
#
# 本体は ccnavi の `ticket` サブコマンド。ここは実行ファイルを探して渡すだけ。
# 探す順は、環境変数 CCNAVI_BIN_PATH が指すもの → main の dist/ccnavi/ccnavi →
# ソースツリーの `python -m ccnavi`。
# 終了コード: 0 成功 / 1 前提の未充足 / 2 引数か環境の誤り

set -eu

usage() {
	cat <<'USAGE'
sh .claude/scripts/ccnavi-ticket.sh <start|done|cancel> <識別子> [--reason <理由>]

  start   todo/ -> doing/  作業ツリー .claude/worktrees/<識別子> が要る。着手の時刻と基準点を書く
  done    doing/ -> done/  完了の時刻を書く。写しは次の hook が閉じる
  cancel  todo/ か doing/ -> cancelled/  --reason が要る
USAGE
}

[ "$#" -ge 2 ] || {
	usage
	exit 2
}
case "$1" in
start | done | cancel) ;;
-h | --help | help)
	usage
	exit 0
	;;
*)
	printf 'ccnavi-ticket: %s は通しません。使えるのは start / done / cancel です。\n' "$1" >&2
	exit 2
	;;
esac

# main の根。作業ツリーの中から呼ばれても、写しと設定は main の側にある。
common=$(git rev-parse --git-common-dir 2>/dev/null || :)
[ -z "$common" ] && {
	printf 'ccnavi-ticket: git リポジトリの中で実行してください。\n' >&2
	exit 2
}
case "$common" in
*/.git) root="${common%/.git}" ;;
.git) root="$(pwd)" ;;
*) root="$common" ;;
esac

# 実行ファイル。設定に書かれた綴りを優先し、無ければ既定の置き場、それも無ければソース。
bin="$root/${CCNAVI_BIN_PATH:-dist/ccnavi/ccnavi}"
if [ -x "$bin" ]; then
	exec "$bin" --root "$root" ticket "$@"
elif [ -x "$bin.exe" ]; then
	exec "$bin.exe" --root "$root" ticket "$@"
elif [ -f "$root/ccnavi/__main__.py" ]; then
	cd "$root"
	exec uv run python -m ccnavi --root "$root" ticket "$@"
fi
printf 'ccnavi-ticket: ccnavi の実行ファイルが無い (%s)。build.py で組み立ててください。\n' "$bin" >&2
exit 2
