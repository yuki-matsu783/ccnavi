#!/bin/sh
# ccnavi-user-decide 残った指摘（未解決のスレッド）の対応方針を決めて置く。ユーザが端末で打つ（ボードが押した選択を送る形も、ユーザが打つ）。
#
#   sh .ccnavi/scripts/ccnavi-user-decide.sh <N>                                     端末で 1 件ずつ選ぶ
#   sh .ccnavi/scripts/ccnavi-user-decide.sh <N> --choices <JSON> --digest <ダイジェスト>  ボードでユーザが押した選択を置く
#
# 前は `ccnavi-review.sh decide <N> [--choices … --digest …]` だった。ユーザだけが打つ sh は ccnavi-user-*.sh に
# 分けてあり、エージェントからは hook が名前で止める。一覧を見るだけの形（`ccnavi-review.sh decide <N> --preview`）は
# エージェントも使うので、ccnavi-review.sh に残してある。
#
# ここが取ってくる → `ccnavi user reviewed N --accept-unresolved` がユーザに見せ、指摘ごとに
# 対応しない・このフェーズで直す（続きの子チケット）・issue に回すを選ばせる
# → 対応しない・issue に回すと選んだ指摘は、置く前にここがスレッドへ返信を残して解決する
#   （`--accept-plan` が返信の本文を書く。ホストに書く前に結果を取り直して指摘が変わっていないことを
#   確かめ、書いたあとにもう一度取り直して、受け入れたスレッドがホストで解決済みのときだけ置く。
#   返信か解決ができなければ、何も置かずに止まる）
# → issue に回す分があればここが issue を作り、決めた内容をコメントに書き出す
#
# 取り込み済みの親子のチケットでは、置く呼び出しを write-and-push で回す（ccnavi-common-write-and-push.sh）。端末で選ぶ形は、選ぶのを
# write-and-push の外で先に済ませる（ロックを持ったままユーザを待たない）。
#
# 親のワークツリーの中で実行すること。どの親かは cwd から引く。
# 終了コード: 0 成功 / 1 前提の未充足（置けなかった） / 2 引数か環境の誤り

set -eu

usage() {
	cat <<'USAGE'
sh .ccnavi/scripts/ccnavi-user-decide.sh <N> [--choices <JSON> --digest <ダイジェスト>]

  未解決（Unresolved）の指摘の対応方針を指摘ごとに選んで置く。対応しない・このフェーズで直す・issue に回す。
  ユーザが端末で実行します。--choices と --digest は、ボードでユーザが押した選択を置く形（組で渡す）。
  一覧を見るだけなら sh .ccnavi/scripts/ccnavi-review.sh decide <N> --preview。

gh / glab があればそれを使う。無ければ curl と GITLAB_TOKEN / GITHUB_TOKEN。jq が必要です。
USAGE
}

. "$(dirname "$0")/ccnavi-common.sh"
review_label=ccnavi-user-decide
ccnavi_load ccnavi-common-review.sh
ccnavi_load ccnavi-user-lib-review.sh
sub=decide

case "${1:-}" in
-h | --help | help)
	usage
	exit 0
	;;
esac
# 診断ログに残す引数の数（読む前の数）。
args_n=$#
n="${1:-}"
case "$n" in
'' | *[!0-9]*) fail decide-no-phase "<N>（フェーズ番号）が必要です。" 2 ;;
esac
# 下書きの名前は実行ファイルが整数で組む（`01` でも `1`）。揃えないと、書いた下書きを拾えない
n=$(printf '%s' "$n" | sed 's/^0*\([0-9]\)/\1/')
shift
choices=""
digest=""
while [ "$#" -gt 0 ]; do
	case "$1" in
	--preview)
		fail decide-preview "一覧を見るだけの --preview は ccnavi-review.sh decide <N> --preview です（何も置かない）。" 2
		;;
	--json) shift ;;
	--choices | --digest)
		[ "$#" -ge 2 ] || fail decide-no-value "$1 には値が必要です。" 2
		if [ "$1" = --choices ]; then choices="$2"; else digest="$2"; fi
		shift 2
		;;
	*) fail decide-bad-option "$1 は受けない。" 2 ;;
	esac
done
if [ -n "$choices" ] || [ -n "$digest" ]; then
	[ -n "$choices" ] && [ -n "$digest" ] || fail decide-choices-pair "--choices と --digest は組で渡してください。" 2
fi

review_place
review_exe || review_no_bin
review_host

# decide: 選択を置く前に、受け入れる指摘（対応しない・issue に回す）へ返信を残して解決する。
# <選択の JSON> <ダイジェスト>。計画の段（置く側の事前検査とダイジェストの照合を含む。何も置かない）
# が通れば、返信と解決をして結果を取り直す。失敗したら（ダイジェストの不一致を含む）答えを出して抜ける。
accept_before_place() {
	accept_plan="$state/review-accept-$family-$n.json"
	rm -f "$accept_plan"
	accept_rc=0
	accept_out=$(ccnavi user reviewed "$n" --accept-unresolved --yes "$1" --digest "$2" --json --result "$result" --accept-plan "$accept_plan") ||
		accept_rc=$?
	if [ "$accept_rc" -ne 0 ]; then
		[ -z "$accept_out" ] || printf '%s\n' "$accept_out"
		exit 1
	fi
	accept_execute "$accept_plan"
}

# ---- 決めた行き先を投稿する。exe が state の置き場に書いた下書きから、issue を作ってコメントを残す。
#
# 置いたあとの投稿なので、ここで失敗しても決めたことは戻さない。失敗は post_warning に言い、
# 下書きは残す（打ち直せば投稿できる）。下書きの名前は exe と揃える（親の識別子。ブランチ名と違うことがある）。

post_decision() {
	issue_url=""
	post_warning=""
	number=$("$JQ" '.mr.number' "$result")
	url=$("$JQ" -r '.mr.url' "$result")
	issue_draft="$state/review-issue-$family-$1.md"
	noted="$state/review-decide-$family-$1.md"
	if [ -f "$issue_draft" ]; then
		issue=$(create_issue "$issue_draft" || :)
		if [ -n "$issue" ]; then
			issue_url=$(printf '%s' "$issue" | "$JQ" -r '.url')
			issue_no=$(printf '%s' "$issue" | "$JQ" -r '.number')
			[ -f "$noted" ] && printf '\nissue に回した分: #%s %s\n' "$issue_no" "$issue_url" >>"$noted"
			rm -f "$issue_draft"
		else
			post_warning="issue を作れなかった。下書きは $issue_draft にある"
		fi
	fi
	if [ -f "$noted" ]; then
		# comment は api と jq のパイプで、終了コードは jq のもの。投稿の失敗は URL が空で見分ける
		posted=$(comment "$number" "$url" "$noted" || :)
		if [ -n "$(printf '%s' "$posted" | "$JQ" -r '.url // empty' 2>/dev/null)" ]; then
			rm -f "$noted"
		else
			post_warning="${post_warning:+${post_warning}。}マージリクエストにコメントを残せなかった。下書きは $noted にある"
		fi
	fi
}

review_begin "$args_n"
# 抜けるときに exe に渡した JSON を消し、終わりの 1 行を診断ログに残す。終了コードは変えない。
# rm が失敗しても、`set -e` がその失敗の値で抜けて元の終了コードを上書きしないよう、失敗を無視してから元の値で抜け直す。
trap 'review_exit=$?; ccnavi_wap_end; accept_cleanup "$review_exit"; rm -f "$result" "$result.re" ${accept_plan:+"$accept_plan"} 2>/dev/null || :; log_info 終わった -- "sub=$sub" "exit=$review_exit"; exit "$review_exit"' EXIT
trap 'ccnavi_wap_end; exit 130' INT TERM HUP

review_wap

# マーカーに残すアカウント。confirm と同じくトークンの持ち主をホストに聞き、
# ロックを取る前に引く。引けなければ渡さず、マーカーは前と同じ。
# 経路は、ボードの押した選択（--choices）なら board、端末で選ぶ形なら terminal。
decide_who=""
if exe_knows --actor && exe_knows --via; then
	decide_actor=$(account || :)
	log_debug マーカーのアカウント -- "actor=${decide_actor:+set}"
	if [ -n "$decide_actor" ]; then
		if [ -n "$choices" ]; then decide_via=board; else decide_via=terminal; fi
		decide_who="--actor=$decide_actor --via=$decide_via"
	fi
fi
# 端末で選ぶ形は、選ぶのを write-and-push の外で先に済ませる（ロックを持ったままユーザを待たない）ので、ここでは始めない。
[ -z "$choices" ] || wap_start "$family"
fetch_all >"$result"
if [ -n "$choices" ]; then
	# 失敗の答え（見せた指摘と今の指摘が違う、など）も JSON で返すので、先に出してから終わる。
	tell_skew
	# 受け入れる指摘（対応しない・issue に回す）には、置く前に返信を残して解決する。できなければここで止める。
	accept_before_place "$choices" "$digest"
	ccnavi_wap_capture="$state/review-decide-$$.out"
	decide_rc=0
	# shellcheck disable=SC2086 # decide_who は空か --actor=<形を確かめた名前> --via=<語> の 2 語
	wap_ccnavi "$branch のフェーズ $n の指摘の行き先を決めた" -- \
		user reviewed "$n" --accept-unresolved --yes "$choices" --digest "$digest" --json --result "$result" --accept-done "$accept_file" $decide_who ||
		decide_rc=$?
	out=$(cat "$ccnavi_wap_capture" 2>/dev/null || :)
	rm -f "$ccnavi_wap_capture"
	ccnavi_wap_capture=""
	[ "$decide_rc" -eq 0 ] || {
		printf '%s\n' "$out"
		exit 1
	}
	post_decision "$n"
	printf '%s' "$out" | "$JQ" -c --arg u "$issue_url" --arg w "$post_warning" '. + {issue_url: $u, warning: $w}'
	exit 0
fi
ccnavi_wap_family "$family"
case "$ccnavi_wap_target" in
stop)
	ccnavi_wap_refuse
	exit 1
	;;
esac
# 1. ユーザが端末で選ぶ（何も置かない。選択とダイジェストを state の置き場に書くだけ）。
chosen="$state/review-choose-$$.json"
ccnavi user reviewed "$n" --accept-unresolved --choose-out "$chosen" --result "$result" || {
	rm -f "$chosen"
	exit 1
}
choices=$("$JQ" -c '.choices' "$chosen") && digest=$("$JQ" -r '.digest' "$chosen") || {
	rm -f "$chosen"
	fail decide-choose-unreadable "選んだものを読めない（${chosen}）。" 1
}
rm -f "$chosen"
# 2. ホストに触る前に、ローカルで失敗しうること（write-and-push のロック・ツリーの確認）を済ませ、結果を取り直す。
#    選んでいる間に指摘が変われば、取り直した結果とのダイジェストの照合で止まる。
case "$ccnavi_wap_target" in
yes)
	ccnavi_wap_begin || exit 1
	wap_on=yes
	;;
esac
refetch
# 3. 受け入れる指摘（対応しない・issue に回す）に返信を残して解決する。できなければ、何も置かずに止める。
accept_before_place "$choices" "$digest"
# 4. 選んだものを置く（`--yes`。取り直した結果で、見せた指摘とダイジェストが同じときだけ）。
case "$ccnavi_wap_target" in
yes)
	ccnavi_wap_capture="$state/review-decide-$$.out"
	decide_rc=0
	# shellcheck disable=SC2086 # decide_who は空か --actor=<形を確かめた名前> --via=<語> の 2 語
	wap_ccnavi "$branch のフェーズ $n の指摘の行き先を決めた" -- \
		user reviewed "$n" --accept-unresolved --yes "$choices" --digest "$digest" --json --result "$result" --accept-done "$accept_file" $decide_who ||
		decide_rc=$?
	out=$(cat "$ccnavi_wap_capture" 2>/dev/null || :)
	rm -f "$ccnavi_wap_capture"
	ccnavi_wap_capture=""
	if [ "$decide_rc" -ne 0 ]; then
		[ -z "$out" ] || printf '%s\n' "$out"
		exit 1
	fi
	printf 'OK: フェーズ %s の指摘の行き先を置いて送った（%s）\n' "$n" "$out"
	;;
*)
	decide_rc=0
	# shellcheck disable=SC2086 # decide_who は空か --actor=<形を確かめた名前> --via=<語> の 2 語
	out=$(ccnavi user reviewed "$n" --accept-unresolved --yes "$choices" --digest "$digest" --json --result "$result" --accept-done "$accept_file" $decide_who) ||
		decide_rc=$?
	if [ "$decide_rc" -ne 0 ]; then
		[ -z "$out" ] || printf '%s\n' "$out"
		exit 1
	fi
	printf 'OK: フェーズ %s の指摘の行き先を置いた（%s）\n' "$n" "$out"
	;;
esac
post_decision "$n"
[ -n "$issue_url" ] && printf 'issue に回した: %s\n' "$issue_url"
[ -n "$post_warning" ] && printf '%s: %s\n' "$review_label" "$post_warning" >&2
:
