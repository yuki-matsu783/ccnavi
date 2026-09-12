#!/bin/sh
# ccnavi-ticket — チケットの状態を動かす。親（メインエージェント）だけが呼ぶ。
#
#   sh .claude/scripts/ccnavi-ticket.sh start  <識別子>
#   sh .claude/scripts/ccnavi-ticket.sh done   <識別子>
#   sh .claude/scripts/ccnavi-ticket.sh cancel <識別子> --reason <理由>
#   sh .claude/scripts/ccnavi-ticket.sh judge  <子> <項目> yes|no --reason <根拠>
#
# judge は、実績のリスクの定性項目（risk.yml の `judge:`）の判定を記録する。判断するのは
# サブエージェント、記録するのは親。判定が揃うまで、その子は done で閉じられない。
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

# 共通部分。ワークスペースルートの探し方はここにある（設計 §25.8）。
. "$(dirname "$0")/ccnavi-common.sh"

usage() {
	cat <<'USAGE'
sh .claude/scripts/ccnavi-ticket.sh <start|done|cancel> <識別子> [--reason <理由>]
sh .claude/scripts/ccnavi-ticket.sh judge <子> <項目> yes|no --reason <根拠>

  start   todo/ -> doing/  作業ツリー .claude/worktrees/<識別子> が要る。着手の時刻と基準点を書く
  done    doing/ -> done/  完了の時刻を書く。子は実績のリスク（差分）を数えて記録する
  cancel  todo/ か doing/ -> cancelled/  --reason が要る
  judge   定性のリスク項目の判定を記録する（親が打つ。判断はサブエージェント）
USAGE
}

[ "$#" -ge 2 ] || {
	usage
	exit 2
}
case "$1" in
start | done | cancel | judge) ;;
-h | --help | help)
	usage
	exit 0
	;;
*)
	printf 'ccnavi-ticket: %s は通しません。使えるのは start / done / cancel / judge です。\n' "$1" >&2
	exit 2
	;;
esac

# ワークスペースルート。承認済みチケットと設定と実行ファイルはここにある。
#
# git には聞かない。モード B（projects/ の下に別リポジトリを clone する形）では、
# cwd がプロジェクトの中にあると git はプロジェクトを答える。それは git として
# 正しい答えで、ここで欲しいもの（道具の置き場）とは違う（設計 §25.8）。
root=$(ccnavi_workspace) || {
	printf 'ccnavi-ticket: ワークスペースルートが見つかりません（.claude/scripts/ を持つ親を cwd から上へ探しました）。ワークスペースの中で実行するか、CCNAVI_WORKSPACE にワークスペースルートの絶対パスを渡してください。\n' >&2
	exit 2
}

# 実行ファイル。設定に書かれた綴りを優先し、無ければ既定の置き場、それも無ければソース。
case "${CCNAVI_BIN_PATH:-}" in
/* | [A-Za-z]:*) bin="$CCNAVI_BIN_PATH" ;;
*) bin="$root/${CCNAVI_BIN_PATH:-dist/ccnavi/ccnavi}" ;;
esac
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
