#!/bin/sh
# ccnavi-ticket — チケットの状態を動かす。親（メインエージェント）だけが呼ぶ。
#
#   sh .ccnavi/scripts/ccnavi-ticket.sh start       <識別子>
#   sh .ccnavi/scripts/ccnavi-ticket.sh finish      <識別子>
#   sh .ccnavi/scripts/ccnavi-ticket.sh cancel      <識別子> --reason <理由>
#   sh .ccnavi/scripts/ccnavi-ticket.sh record-risk <子> <項目> yes|no --reason <根拠>
#
# record-risk は、実績のリスクの定性項目（risks.yml の `judge:`）の判定を記録する。判断するのは
# サブエージェント、記録するのは親。判定が揃うまで、その子は finish で閉じられない。
#
# 状態は置き場で表す（ADR-0055）。承認待ちは wip/proposals/todo/、承認済みの作業中は
# .ccnavi/approved/doing/、レビュー待ちは wip/proposals/review/、閉じたものは
# .ccnavi/approved/done/。人が動かす向きは .ccnavi/approved/ へ、エージェントが動かす向きは
# wip/proposals/ へ。エージェントの側を動かすのはこのスクリプトだけで、直接ファイルを
# 作ったり動かしたりするのは ccnavi が止める。
# サブエージェントからの呼び出しも ccnavi が止める（agent_id が付いていたら拒む）。
#
# 本体は ccnavi の `ticket` サブコマンド。ここは実行ファイルを探して渡すだけ。
# 探す順は、環境変数 CCNAVI_BIN_PATH が指すもの → ワークスペースルートの dist/ccnavi/ccnavi →
# ソースツリーの `python -m ccnavi`。
# 終了コード: 0 成功 / 1 前提の未充足 / 2 引数か環境の誤り

set -eu

# 共通部分。ワークスペースルートの探し方はここにある（設計 11.8）。
. "$(dirname "$0")/ccnavi-common.sh"

# fail <識別子> <文面> <終了コード>。文面は標準エラーへ出す契約。識別子は止めた理由の種類を
# 表す短い語で、診断ログ（docs/claude/logging.md）にだけ残す。文面には引数やパスが入るので、
# ログには写さない。
fail() {
	printf 'ccnavi-ticket: %s\n' "$2" >&2
	log_info 止めた -- "sub=${sub:-}" "exit=$3" "reason=$1"
	exit "$3"
}

usage() {
	cat <<'USAGE'
sh .ccnavi/scripts/ccnavi-ticket.sh <start|finish|cancel> <識別子> [--reason <理由>]
sh .ccnavi/scripts/ccnavi-ticket.sh record-risk <子> <項目> yes|no --reason <根拠>

  start        .ccnavi/approved/doing/ の承認済みチケットに着手の時刻と基準点を書く。
               ワークツリー .claude/worktrees/<識別子> が要る。置き場は動かない
  finish       doing/ -> wip/proposals/review/（フェーズがレビュー要）か .ccnavi/approved/done/（不要）。
               完了の時刻を書く。子は実績のリスク（差分）を数えて記録する
  cancel       doing/ -> .ccnavi/approved/done/  --reason が要る。未承認の提案（todo/）は消せばよい
  record-risk  定性のリスク項目の判定を記録する（親が打つ。判断はサブエージェント）

  レビュー待ち（review/）から done/ へ動かすのは人（ccnavi-review.sh confirm / decide、
  ccnavi --reviewed）。

  提案の plan に書くフェーズの種類は phases.yml を見る。置き場は共通層の
  .ccnavi/common/phases.yml、自身の層の .ccnavi/config/phases.yml、
  プロジェクトは projects/<名前>/.ccnavi/config/phases.yml。どの層にも無ければ
  フェーズは番号だけになる
USAGE
}

# help は引数の数を数える前に見る。`--help` だけで打たれたときに、使い方を出しながら
# 「引数の誤り」の終了コードを返さないため（ccnavi-review.sh と揃える）。
case "${1:-}" in
-h | --help | help)
	usage
	exit 0
	;;
esac

[ "$#" -ge 2 ] || {
	usage
	log_info 止めた -- "exit=2" "reason=missing-args"
	exit 2
}
case "$1" in
start | finish | cancel | record-risk) sub="$1" ;;
*)
	fail unknown-sub "$1 は通しません。使えるのは start / finish / cancel / record-risk です。" 2
	;;
esac

# ワークスペースルート。承認済みチケットと設定と実行ファイルはここにある。
#
# git には聞かない。モード B（projects/ の下に別リポジトリを clone する形）では、
# cwd がプロジェクトの中にあると git はプロジェクトを答える。それは git として
# 正しい答えで、ここで欲しいもの（道具の置き場）とは違う（設計 11.8）。
root=$(ccnavi_workspace) ||
	fail no-workspace "ワークスペースルートが見つかりません（.ccnavi/scripts/ccnavi-common.sh を持つ親を cwd から上へ探しました）。ワークスペースの中で実行するか、CCNAVI_WORKSPACE にワークスペースルートの絶対パスを渡してください。" 2
# 解いたルートを logger に渡し、書くたびに探し直させない。
ccnavi_log_root="$root"
log_info 受け付けた -- "sub=$sub" "args=$#"

# 実行ファイル。見つからなければソース（ccnavi のリポジトリ）で動かす。
#
# exec で置き換えずに子として走らせ、終了コードをそのまま返す。置き換えると
# 最終の結果（終了コード）を診断ログに残せない。標準出力・標準エラー・終了コードは同じ。
ticket_exit=0
if bin=$(ccnavi_bin "$root"); then
	skew=$(ccnavi_compat_skew "$root" "$bin") || {
		printf 'ccnavi-ticket: %s\n' "$skew" >&2
		log_warn 互換の版が食い違う -- "reason=compat-skew"
	}
	log_debug 判定の材料 -- "sub=$sub" "via=bin" "workspace=$root"
	"$bin" --root "$root" ticket "$@" || ticket_exit=$?
elif [ -f "$root/ccnavi/__main__.py" ]; then
	cd "$root"
	log_debug 判定の材料 -- "sub=$sub" "via=source" "workspace=$root"
	uv run python -m ccnavi --root "$root" ticket "$@" || ticket_exit=$?
else
	fail no-bin "ccnavi の実行ファイルが無い（CCNAVI_BIN_PATH・dist/ccnavi/ccnavi・.ccnavi/bin/ のどれにも無い）。build.py で組み立てるか、scripts/ccnavi-setup.sh で配ってください。" 2
fi
log_info 終わった -- "sub=$sub" "exit=$ticket_exit"
exit "$ticket_exit"
