#!/bin/sh
# ccnavi-ticket チケットの状態を動かす。親（メインエージェント）だけが呼ぶ。
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
# .ccnavi/approved/done/。ユーザが動かす向きは .ccnavi/approved/ へ、エージェントが動かす向きは
# wip/proposals/ へ。エージェントの側を動かすのはこのスクリプトだけで、直接ファイルを
# 作ったり動かしたりするのは ccnavi が止める。
# サブエージェントからの呼び出しも ccnavi が止める（agent_id が付いていたら拒む）。
#
# 本体は ccnavi の `ticket` サブコマンド。ここは実行ファイルを探して渡すだけ。
# 探す順は、環境変数 CCNAVI_BIN_PATH が指すもの → ワークスペースルートの dist/ccnavi/ccnavi →
# ソースツリーの `python -m ccnavi`。
#
# 取り込み済みの家族（origin があり家族の控えが present。chat だけの家族を除く）の start・finish・cancel は
# C1 で回す（ADR-0093 の 4.3。段階 2d）: ロック → 途中の操作の確認 → hook の印と跡を先にコミット →
# 取り込み（ccnavi-sync.sh）→ 未送信の確かめ → 書く → 書いたパスだけ commit --only → push。push が
# 通るまで完了にしない。送れなければ書いたものを戻す。record-risk は C1 にしない（その子の finish がコミットして送る）。
# それ以外の家族は今のまま（書くだけ。コミットと push はエージェント）。
# 終了コード: 0 成功 / 1 前提の未充足（C1 で止めた・送れなかったを含む） / 2 引数か環境の誤り

set -eu

# 共通部分。ワークスペースルートの探し方はここにある（設計 11.8）。
. "$(dirname "$0")/ccnavi-common.sh"

usage() {
	cat <<'USAGE'
sh .ccnavi/scripts/ccnavi-ticket.sh <start|finish|cancel> <識別子> [--reason <理由>]
sh .ccnavi/scripts/ccnavi-ticket.sh record-risk <子> <項目> yes|no --reason <根拠>

  start        .ccnavi/approved/doing/ の承認済みチケットに着手の時刻と基準点を書く。
               ワークツリー .claude/worktrees/<識別子> が要る。承認済みチケットは置き場を移らない
  finish       doing/ -> wip/proposals/review/（フェーズがレビュー要）か .ccnavi/approved/done/（不要）。
               完了の時刻を書く。子は実績のリスク（差分）を数えて記録する
  cancel       doing/ -> .ccnavi/approved/done/  --reason が要る。未承認の提案（todo/）は消せばよい
  record-risk  定性のリスク項目の判定を記録する（親が打つ。判断はサブエージェント）

  レビュー待ち（review/）から done/ へ動かすのはユーザ（ccnavi-review.sh confirm / decide、
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
	exit 2
}
case "$1" in
start | finish | cancel | record-risk) ;;
*)
	printf 'ccnavi-ticket: %s は通しません。使えるのは start / finish / cancel / record-risk です。\n' "$1" >&2
	exit 2
	;;
esac

# ワークスペースルート。承認済みチケットと設定と実行ファイルはここにある。
#
# git には聞かない。モード B（projects/ の下に別リポジトリを clone する形）では、
# cwd がプロジェクトの中にあると git はプロジェクトを答える。それは git として
# 正しい答えで、ここで欲しいもの（道具の置き場）とは違う（設計 11.8）。
root=$(ccnavi_workspace) || {
	printf 'ccnavi-ticket: ワークスペースルートが見つかりません（.ccnavi/scripts/ccnavi-common.sh を持つ親を cwd から上へ探しました）。ワークスペースの中で実行するか、CCNAVI_WORKSPACE にワークスペースルートの絶対パスを渡してください。\n' >&2
	exit 2
}

# 実行ファイル。見つからなければソース（ccnavi のリポジトリ）で動かす。
if bin=$(ccnavi_bin "$root"); then
	skew=$(ccnavi_compat_skew "$root" "$bin") || printf 'ccnavi-ticket: %s\n' "$skew" >&2
	ccnavi_c1_exe() { "$bin" --root "$root" "$@"; }
elif [ -f "$root/ccnavi/__main__.py" ]; then
	ccnavi_c1_exe() { (cd "$root" && uv run python -m ccnavi --root "$root" "$@"); }
else
	printf 'ccnavi-ticket: ccnavi の実行ファイルが無い（CCNAVI_BIN_PATH・dist/ccnavi/ccnavi・.ccnavi/bin/ のどれにも無い）。build.py で組み立てるか、scripts/ccnavi-setup.sh で配ってください。\n' >&2
	exit 2
fi

# C1（取り込み済みの家族の start・finish・cancel）。
case "$1" in
start | finish | cancel)
	ccnavi_log_root="$root"
	ccnavi_c1_root="$root"
	ccnavi_c1_label=ccnavi-ticket
	ccnavi_c1_sh="$(dirname "$0")"
	trap 'ccnavi_c1_end' EXIT
	trap 'ccnavi_c1_end; exit 130' INT TERM HUP
	# 識別子は実行ファイル（argparse）と同じに読む。`--` と `--reason <値>` を読み飛ばした最初の語
	# （`start -- <親>` で C1 を経ずに通らないようにする。段階 2d のレビュー）。
	c1_id=""
	c1_skip=""
	c1_first=yes
	for c1_arg in "$@"; do
		if [ -n "$c1_first" ]; then
			c1_first=""
			continue
		fi
		if [ -n "$c1_skip" ]; then
			c1_skip=""
			continue
		fi
		case "$c1_arg" in
		--reason) c1_skip=yes ;;
		--* | -?*) ;;
		*) [ -n "$c1_id" ] || c1_id="$c1_arg" ;;
		esac
	done
	if ! ccnavi_is_ident "$c1_id"; then
		printf 'ccnavi-ticket: %s には識別子が要る（%s は識別子の形ではない）。\n' "$1" "${c1_id:-（無い）}" >&2
		exit 2
	fi
	ccnavi_c1_family "$c1_id"
	case "$ccnavi_c1_target" in
	stop)
		ccnavi_c1_refuse
		exit 1
		;;
	yes)
		case "$1" in
		start) c1_words="$c1_id に着手" ;;
		finish) c1_words="$c1_id の作業を終えた" ;;
		*) c1_words="$c1_id を取り消した" ;;
		esac
		ccnavi_c1_begin || exit 1
		c1_rc=0
		ccnavi_c1_write "ccnavi: ${c1_words}" -- ticket "$@" || c1_rc=$?
		ccnavi_c1_end
		exit "$c1_rc"
		;;
	esac
	ccnavi_c1_end
	trap - EXIT INT TERM HUP
	;;
esac

if [ -n "${bin:-}" ]; then
	exec "$bin" --root "$root" ticket "$@"
fi
cd "$root"
exec uv run python -m ccnavi --root "$root" ticket "$@"
