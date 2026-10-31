#!/bin/sh
# ccnavi-user-close-early まだ残っているが、早めに閉じる判断を置く。ユーザがターミナルで打つ。
#
#   sh .ccnavi/scripts/ccnavi-user-close-early.sh --reason <理由> [--no-issue]
#
# ユーザだけが打つ sh は ccnavi-user-*.sh に分けてあり、エージェントからは hook が名前で止める。
#
# 実行ファイル（`ccnavi user close-early`）が残りを見せて y/N を取り、マーカーを置いて下書きを書く。
# ここが残りを issue に書き出し、コメントを投稿する。Draft を外すのは、親が片付けて push したあとの
# ccnavi-review.sh ready（外す経路は 1 本）。未解決の指摘は受け入れる。スレッドに返信を残して解決するのはここで、
# 置く前に済ませる。
# 取り込み済みの親子のチケットなら、早めに閉じたマーカー（ユーザの判断）を承認の push
# （ccnavi-user-push-approved.sh <親>）で送る。ユーザの判断を溜めずにその場で送るためで、送れなければ次の write-and-push が止まり、
# 承認の push の打ち直しを案内する。
#
# 親のワークツリーの中で実行すること。どの親かは cwd から引く。
# 終了コード: 0 成功 / 1 前提の未充足 / 2 引数か環境の誤り

set -eu

usage() {
	cat <<'USAGE'
sh .ccnavi/scripts/ccnavi-user-close-early.sh --reason <理由> [--no-issue]

  まだ残っているが早めに閉じる判断（ユーザがターミナルで実行します）。残りを issue に書き出す（--no-issue なら書き出さない）。
  Draft は親が ccnavi-review.sh ready で外す。

gh / glab があればそれを使う。無ければ curl と GITLAB_TOKEN / GITHUB_TOKEN。jq が必要です。
USAGE
}

. "$(dirname "$0")/ccnavi-common.sh"
review_label=ccnavi-user-close-early
ccnavi_load ccnavi-common-review.sh
ccnavi_load ccnavi-user-lib-review.sh
sub=close-early

case "${1:-}" in
-h | --help | help)
	usage
	exit 0
	;;
esac
# 診断ログに残す引数の数（読む前の数）。
args_n=$#
reason=""
make_issue=1
while [ "$#" -gt 0 ]; do
	case "$1" in
	--reason)
		reason="${2:-}"
		shift 2
		;;
	--no-issue)
		make_issue=0
		shift
		;;
	*) shift ;;
	esac
done
[ -n "$reason" ] || fail close-early-no-reason "--reason <理由> が必要です。" 2

review_place
review_exe || review_no_bin
review_host
review_begin "$args_n"
# 抜けるときに exe に渡した JSON を消し、終わりの 1 行を診断ログに残す。終了コードは変えない。
# rm が失敗しても、`set -e` がその失敗の値で抜けて元の終了コードを上書きしないよう、失敗を無視してから元の値で抜け直す。
trap 'review_exit=$?; ccnavi_wap_end; accept_cleanup "$review_exit"; rm -f "$result" "$result.re" ${accept_plan:+"$accept_plan"} 2>/dev/null || :; log_info 終わった -- "sub=$sub" "exit=$review_exit"; exit "$review_exit"' EXIT
trap 'ccnavi_wap_end; exit 130' INT TERM HUP

review_wap
# 親子のチケットの write-and-push が止まるなら、ホストにも手元にも触る前に止める（返信・解決・issue・コメントの後では止めない）。
ccnavi_wap_family "$family"
case "$ccnavi_wap_target" in
stop)
	ccnavi_wap_refuse
	exit 1
	;;
esac

# ユーザの判断をコミットして push する。取り込み済みの親子のチケットだけ、承認の push を <親> で呼ぶ。それ以外は今のまま何もしない。
carry_human() {
	ccnavi_wap_family "$1"
	case "$ccnavi_wap_target" in
	yes)
		sh "$ccnavi_wap_sh/ccnavi-user-push-approved.sh" "$ccnavi_wap_family_id" || {
			printf '%s: ユーザの判断は置いたが送れなかった。接続を戻してから、sh %s/ccnavi-user-push-approved.sh %s をユーザが再実行してください（送るまで、この親子のチケットの状態の操作は止まります）\n' \
				"$review_label" "$ccnavi_wap_sh" "$ccnavi_wap_family_id" >&2
			return 1
		}
		;;
	esac
	return 0
}

fetch_all >"$result"
# exe はユーザに残りを見せて y/N を取るので、標準出力はターミナルのまま。下書きは state の
# 置き場の決まった名前で拾う（親の識別子。ブランチ名と違うことがある）。
# 未解決の指摘は受け入れる。スレッドに返信を残して解決するのはここで、
# 置く前に済ませる。3 回に分けて打つ。
#   1 回目: 残りを見せて y/N を聞き、y なら計画を書くだけ（--accept-plan）
#   2 回目: 結果を取り直し、y/N の間に未解決の指摘が変わっていないかだけを確かめる（--accept-check）
#   ここで返信と解決をして、結果を取り直す。できなければ止める
#   3 回目: 計画のスレッドが全部ホストで解決済みで、ほかに未解決が無いことを確かめて、聞き直さずに置く（--accept-done）
accept_plan="$state/review-accept-$family.json"
rm -f "$accept_plan"
close_issue_flag=""
[ "$make_issue" -eq 1 ] || close_issue_flag="--no-issue"
# shellcheck disable=SC2086 # close_issue_flag は空か --no-issue
ccnavi --entry sh user close-early --reason "$reason" --result "$result" --accept-plan "$accept_plan" $close_issue_flag || exit $?
refetch
ccnavi --entry sh user close-early --reason "$reason" --result "$result" --accept-check "$accept_plan" || exit $?
accept_execute "$accept_plan"
ccnavi --entry sh user close-early --reason "$reason" --result "$result" --accept-done "$accept_file" || exit $?
issue_draft="$state/review-close-early-issue-$family.md"
noted="$state/review-close-early-note-$family.md"
number=$(printf '%s' "$(cat "$result")" | "$JQ" '.mr.number')
url=$(printf '%s' "$(cat "$result")" | "$JQ" -r '.mr.url')
if [ "$make_issue" -eq 1 ] && [ -f "$issue_draft" ]; then
	issue=$(staged_send close-early "$family" issue issue "$number" "$url" "$issue_draft") || issue=""
	[ -z "$issue" ] && fail issue-create-failed "残りを書き出す issue を作れなかった。下書きは $issue_draft にある。"
	issue_url=$(printf '%s' "$issue" | "$JQ" -r '.url')
	issue_no=$(printf '%s' "$issue" | "$JQ" -r '.number')
	printf '残りを #%s に書き出した（%s）\n' "$issue_no" "$issue_url"
	printf '残りは #%s へ: %s\n' "$issue_no" "$issue_url" >>"$noted"
	rm -f "$issue_draft"
fi
if [ -f "$noted" ]; then
	staged_send close-early "$family" comment note "$number" "$url" "$noted" >/dev/null &&
		rm -f "$noted"
fi
# 取り込み済みの親子のチケットなら、早めに閉じたマーカー（ユーザの判断）を承認の push で送る。
carry_human "$family" || exit 1
printf 'OK: 早めに閉じた（%s）\n' "$url"
