#!/bin/sh
# ccnavi-ticket チケットの状態を動かす（親（メインエージェント）だけが呼ぶ）。状態を聞く（誰でも呼べる）。
#
#   sh .ccnavi/scripts/ccnavi-ticket.sh status      [<親>]
#   sh .ccnavi/scripts/ccnavi-ticket.sh start       <識別子>
#   sh .ccnavi/scripts/ccnavi-ticket.sh finish      <識別子>
#   sh .ccnavi/scripts/ccnavi-ticket.sh cancel      <識別子> --reason <理由>
#   sh .ccnavi/scripts/ccnavi-ticket.sh record-risk <子> <項目> yes|no --reason <根拠>
#
# record-risk は、実績のリスクの定性項目（risks.yml の `judge:`）の判定を記録する。判断するのは
# サブエージェント、記録するのは親。判定が揃うまで、その子は finish で閉じられない。
#
# status は読むだけ。承認済みチケットの置き場・承認の時刻・着手・未コミットか未 push か・止まっている理由・
# 次の一手を ccnavi が判断して出す。ネットワークには出ない（push 済みかは手元のリモート追跡の ref で見る）。
# サブエージェントも呼べる。状態を動かすコマンドを出す行には「親（メインエージェント）だけが実行する」と書く。
#
# 状態は置き場で表し、チケットは 1 本のファイルが置き場を動く。承認待ちは wip/proposals/todo/、承認済みの作業中は
# .ccnavi/approved/doing/、レビュー待ちは wip/proposals/review/、閉じたものは
# .ccnavi/approved/done/。ユーザが動かす向きは .ccnavi/approved/ へ、エージェントが動かす向きは
# wip/proposals/ へ。エージェントの側を動かすのはこのスクリプトだけで、直接ファイルを
# 作ったり動かしたりするのは ccnavi が止める。
# サブエージェントからの start・finish・cancel・record-risk は ccnavi の hook が止める（判定がサブエージェントの
# 呼び出しを見分ける。実行ファイルとこのスクリプトは呼び手を知らない）。status は止めない。
#
# 本体は ccnavi の `ticket` サブコマンド。ここは実行ファイルを探して渡すだけ。
# 探す順は、環境変数 CCNAVI_BIN_PATH が指すもの → ワークスペースルートの dist/ccnavi/ccnavi →
# ソースツリーの `python -m ccnavi`。
#
# 取り込み済みの親子のチケット（origin があり、親子のチケットの取り込み状態が present。chat だけのものを除く）の
# start・finish・cancel は C1 で回す。Chrome 拡張から見える親子のチケットに未 push の状態を溜めないため、次を 1 操作にする。
# ロック → 途中の操作の確認 → hook のマーカーと状態の履歴を先にコミット →
# 取り込み（ccnavi-sync.sh）→ 未送信の確かめ → 書く → 書いたパスだけ commit --only → push。push が
# 通るまで完了にしない。送れなければ書いたものを戻す。record-risk は C1 にしない（その子の finish がコミットして送る）。
# それ以外の親子のチケットは今のまま（書くだけ。コミットと pushはエージェント）。
#
# finish・cancel が通ったら、その親子の閉じた子のワークツリーを実行ファイルの `worktree tidy <親>` で
# 片付ける（C1 なら送り終えた後）。未コミットの変更があるもの・cwd が中にあるものは消さずに名指しする。
# 片付けが済まなくても finish・cancel の終了コードは変えない。
# 終了コード: 0 成功 / 1 前提の未充足（C1 で止めた・送れなかったを含む） / 2 引数か環境の誤り

set -eu

# 共通部分。ワークスペースルートの探し方はここにある（設計 11.8）。
. "$(dirname "$0")/ccnavi-common.sh"

usage() {
	cat <<'USAGE'
sh .ccnavi/scripts/ccnavi-ticket.sh status [<親>]
sh .ccnavi/scripts/ccnavi-ticket.sh <start|finish|cancel> <識別子> [--reason <理由>]
sh .ccnavi/scripts/ccnavi-ticket.sh record-risk <子> <項目> yes|no --reason <根拠>

  status       承認済みチケットの状態を聞く（読むだけ。サブエージェントも打てる）。親を省けば
               作業中・レビュー待ち・承認待ちのチケットがある親子を全部。ファイルを読んで推測せず、これを打つ

  start        .ccnavi/approved/doing/ の承認済みチケットに着手の時刻と基準点を書く。
               ワークツリー .claude/worktrees/<識別子> が要る。承認済みチケットは置き場を移らない
  finish       doing/ -> wip/proposals/review/（フェーズがレビュー要）か .ccnavi/approved/done/（不要）。
               完了の時刻を書く。子は実績のリスク（差分）を数えて記録する
  cancel       doing/ -> .ccnavi/approved/done/  --reason が要る。未承認の提案（todo/）は消せばよい
  record-risk  定性のリスク項目の判定を記録する（親が打つ。判断はサブエージェント）

  レビュー待ち（review/）から done/ へ動かすのはユーザ（ccnavi-review.sh confirm / decide、
  ccnavi --reviewed）。

  提案の plan に書くフェーズ定義は phases.yml を見る。置き場は共通レイヤーの
  .ccnavi/common/phases.yml、自身のレイヤーの .ccnavi/config/phases.yml、
  プロジェクトは projects/<名前>/.ccnavi/config/phases.yml。どのレイヤーにもなければ
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

# status だけは親を省ける（引数 1 個で通す）。
case "${1:-}" in
status) ;;
*)
	[ "$#" -ge 2 ] || {
		usage
		exit 2
	}
	;;
esac
case "$1" in
status | start | finish | cancel | record-risk) ;;
*)
	printf 'ccnavi-ticket: %s は通しません。使えるのは status / start / finish / cancel / record-risk です。\n' "$1" >&2
	exit 2
	;;
esac

# ワークスペースルート。承認済みチケットと設定と実行ファイルはここにある。
#
# git には聞かない。モード B（projects/ の下に別リポジトリを clone する形）では、
# cwd がプロジェクトの中にあると git はプロジェクトを答える。それは git として
# 正しい答えで、ここで欲しいもの（道具の置き場）とは違う（設計 11.8）。
root=$(ccnavi_workspace) || {
	printf 'ccnavi-ticket: ワークスペースルートが見つかりません（.ccnavi/scripts/ccnavi-common.shを持つ親を cwdから上へ探しました）。ワークスペースの中で実行するか、CCNAVI_WORKSPACE にワークスペースルートの絶対パスを渡してください。\n' >&2
	exit 2
}

# 実行ファイル。見つからなければソース（ccnavi のリポジトリ）で動かす。
if bin=$(ccnavi_bin "$root"); then
	skew=$(ccnavi_compat_skew "$root" "$bin") || printf 'ccnavi-ticket: %s\n' "$skew" >&2
	ccnavi_c1_exe() { "$bin" --root "$root" "$@"; }
elif [ -f "$root/src/ccnavi/__main__.py" ]; then
	ccnavi_c1_exe() { (cd "$root" && uv run python -m ccnavi --root "$root" "$@"); }
else
	printf 'ccnavi-ticket: ccnavi の実行ファイルが無い（CCNAVI_BIN_PATH・dist/ccnavi/ccnavi・.ccnavi/bin/ のどれにも無い）。build.py で組み立てるか、scripts/ccnavi-setup.sh で配ってください。\n' >&2
	exit 2
fi

here="$(pwd -W 2>/dev/null || pwd)"

# 識別子を実行ファイル（argparse）と同じに読む。`--` と `--reason <値>` を読み飛ばした最初の語。
# 副命令（1 つ目の語）は読まない。
ticket_id() {
	ti_id=""
	ti_skip=""
	ti_first=yes
	for ti_arg in "$@"; do
		if [ -n "$ti_first" ]; then
			ti_first=""
			continue
		fi
		if [ -n "$ti_skip" ]; then
			ti_skip=""
			continue
		fi
		case "$ti_arg" in
		--reason) ti_skip=yes ;;
		--* | -?*) ;;
		*) [ -n "$ti_id" ] || ti_id="$ti_arg" ;;
		esac
	done
	printf '%s' "$ti_id"
}

# finish・cancel が通った後に、その親子の閉じた（done/ の）子のワークツリーを全部片付ける。ワークツリーが
# あるのは作業中の子だけにする。子の finish・cancel でも、その子に限らず同じ親子の閉じた子をまとめて消す
# （親の finish では取りこぼしを拾う安全網）。レビュー待ち（review/）の子のものは残す（指摘を直す場所。
# confirm が done/ へ動かした後に消える）。消せなかったもの（cwd が中・未コミット）は名指しされるだけで、
# finish・cancel は成功のまま。
tidy_after() {
	case "$1" in
	finish | cancel) ;;
	*) return 0 ;;
	esac
	ta_id=$(ticket_id "$@")
	ccnavi_is_ident "$ta_id" || return 0
	# 子の識別子のまま渡す。親へ割り出すのは実行ファイル（チケットの `parent:` を読む）。名前の形で
	# 末尾の `-<2 桁>-<2 桁>` を剥がすと、その形で終わる親の識別子（rel-2026-10-06 など）を読み違える。
	ccnavi_c1_exe --cwd "$here" worktree tidy "$ta_id" || :
}

# C1（取り込み済みの親子のチケットの start・finish・cancel）。
case "$1" in
start | finish | cancel)
	ccnavi_log_root="$root"
	ccnavi_c1_root="$root"
	ccnavi_c1_label=ccnavi-ticket
	ccnavi_c1_sh="$(dirname "$0")"
	trap 'ccnavi_c1_end' EXIT
	trap 'ccnavi_c1_end; exit 130' INT TERM HUP
	# 識別子は実行ファイル（argparse）と同じに読む（ticket_id。`start -- <親>` で C1 を経ずに通らないようにする）。
	c1_id=$(ticket_id "$@")
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
		trap - EXIT INT TERM HUP
		[ "$c1_rc" -ne 0 ] || tidy_after "$@"
		exit "$c1_rc"
		;;
	esac
	ccnavi_c1_end
	trap - EXIT INT TERM HUP
	;;
esac

rc=0
ccnavi_c1_exe ticket "$@" || rc=$?
[ "$rc" -ne 0 ] || tidy_after "$@"
exit "$rc"
