#!/bin/sh
# ccnavi-user-decide 残った指摘（未解決のスレッド）の対応方針を決めて置く。ユーザが端末で打つ（ボードが押した選択を送る形も、ユーザが打つ）。
#
#   sh .ccnavi/scripts/ccnavi-user-decide.sh <N>                                     端末で 1 件ずつ選ぶ
#   sh .ccnavi/scripts/ccnavi-user-decide.sh <N> --choices <JSON> --digest <ダイジェスト>  ボードでユーザが押した選択を置く
#   sh .ccnavi/scripts/ccnavi-user-decide.sh --resume [<操作ID>]                    止まった判断の続きを送る
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
sh .ccnavi/scripts/ccnavi-user-decide.sh --resume [<操作ID>]

  未解決（Unresolved）の指摘の対応方針を指摘ごとに選んで置く。対応しない・このフェーズで直す・issue に回す。
  ユーザが端末で実行します。--choices と --digest は、ボードでユーザが押した選択を置く形（組で渡す）。
  一覧を見るだけなら sh .ccnavi/scripts/ccnavi-review.sh decide <N> --preview。

  --resume は、途中で止まった判断の続きを送る。保存した判断（操作記録）から、返信・解決・適用・
  issue・コメントの済んでいない段階だけを進める。選び直しはさせず、送り終えた段階は繰り返さない。
  送る前に内容の要約を見せて、端末で y/N を聞く。操作ID を省くと、未完了の判断が 1 件のときそれを選ぶ。
  ワークスペースの中ならどこからでも打てる（親のワークツリーへは自分で移る）。

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
resume_op=""
resuming=""
choices=""
digest=""
if [ "${1:-}" = --resume ]; then
	resuming=1
	shift
	[ "$#" -le 1 ] || fail decide-bad-option "--resume には操作ID を 1 つまで渡せます。" 2
	resume_op="${1:-}"
	[ "$#" -eq 0 ] || shift
	case "$resume_op" in
	'' | [0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]) : ;;
	*) fail decide-bad-op "操作ID は 32 桁の 16 進です。" 2 ;;
	esac
	n=0
else
	n="${1:-}"
	case "$n" in
	'' | *[!0-9]*) fail decide-no-phase "<N>（フェーズ番号）が必要です。" 2 ;;
	esac
	# 下書きの名前は実行ファイルが整数で組む（`01` でも `1`）。揃えないと、書いた下書きを拾えない
	n=$(printf '%s' "$n" | sed 's/^0*\([0-9]\)/\1/')
	shift
fi
while [ "$#" -gt 0 ]; do
	case "$1" in
	--preview)
		fail decide-preview "一覧を見るだけなら ccnavi-review.sh decide <N> --preview を使ってください（何も置きません）。" 2
		;;
	--json) shift ;;
	--choices | --digest)
		[ "$#" -ge 2 ] || fail decide-no-value "$1 には値が必要です。" 2
		if [ "$1" = --choices ]; then choices="$2"; else digest="$2"; fi
		shift 2
		;;
	*) fail decide-bad-option "$1 は受け付けません。" 2 ;;
	esac
done
if [ -n "$choices" ] || [ -n "$digest" ]; then
	[ -n "$choices" ] && [ -n "$digest" ] || fail decide-choices-pair "--choices と --digest は組で渡してください。" 2
fi

# ---- --resume: 保存した判断（操作記録）から、止まった判断の続きを送る。
#
# 操作ID を決め、内容の要約を見せて y/N を聞く。親のワークツリーへ移り、保存した選択とダイジェストを
# 使って、以降は選択を置く形（--choices --digest）と同じ道を通る。選び直しはさせない。
# 返信・解決が済んでいれば繰り返さず、適用が済んでいれば投稿の段階だけを進める。
resume_prepare() {
	# review_host（JQ を決める）の前に動くので、jq は自分で探す。
	rp_jq=$(command -v jq || :)
	[ -n "$rp_jq" ] || fail no-jq "jq が見つかりません。" 2
	# 互換のずれは、最初の `ops` が失敗する前に親で言う（`$( )` の中に消さない）
	tell_skew
	if [ -z "$resume_op" ]; then
		rp_list=$(ccnavi ops pending 2>/dev/null | grep ' decide ' | sed 's/ .*//' || :)
		rp_count=$(printf '%s\n' "$rp_list" | grep -c . || :)
		case "$rp_count" in
		0)
			# 操作記録を入れる前の版が残した下書き（issue とコメント）があるかは、親が決まってから見る
			# （review_host のあと。無ければそこで「未完了の判断はありません」と言う）。
			resume_legacy=check
			return 0
			;;
		1) resume_op="$rp_list" ;;
		*)
			printf '%s: 未完了の判断が複数あります。操作ID を渡してください:\n%s\n' "$review_label" "$rp_list" >&2
			exit 2
			;;
		esac
	fi
	rp_json=$(ccnavi ops show "$resume_op" 2>/dev/null) ||
		fail decide-no-op "操作記録 $resume_op がありません。" 1
	[ "$(printf '%s' "$rp_json" | "$rp_jq" -r '.kind')" = decide ] ||
		fail decide-not-decide "操作 $resume_op は decide ではありません。" 1
	rp_parent=$(printf '%s' "$rp_json" | "$rp_jq" -r '.parent')
	n=$(printf '%s' "$rp_json" | "$rp_jq" -r '.phase')
	rp_left=$(printf '%s' "$rp_json" | "$rp_jq" -r '[.stages[] | select(.state != "sent") | .id + "=" + .state] | join(", ")')
	rp_counts=$(printf '%s' "$rp_json" | "$rp_jq" -r '[.choices[]] | group_by(.) | map(.[0] + " " + (length | tostring) + " 件") | join("、")')
	printf '%s: 未完了の判断 %s\n  親 %s のフェーズ %s（MR !%s）\n  選択: %s\n  済んでいない段階: %s\n' \
		"$review_label" "$resume_op" "$rp_parent" "$n" "$(printf '%s' "$rp_json" | "$rp_jq" -r '.mr')" "$rp_counts" "$rp_left"
	printf '続きを送りますか [y/N]: '
	read -r rp_answer || rp_answer=n
	case "$rp_answer" in
	y | Y | yes | YES) ;;
	*) fail decide-not-confirmed "送らずにやめました。" 1 ;;
	esac
	choices=$(printf '%s' "$rp_json" | "$rp_jq" -c '.choices')
	digest=$(printf '%s' "$rp_json" | "$rp_jq" -r '.plan.digest')
	# 今いる場所が、その親のブランチのワークツリーならそのまま。そうでなければ親のワークツリーへ移る。
	rp_here=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || :)
	if [ "$rp_here" != "$rp_parent" ]; then
		rp_dir="$root/.claude/worktrees/$rp_parent"
		[ -d "$rp_dir" ] || fail decide-no-worktree "親のワークツリー $rp_dir がありません。" 1
		cd "$rp_dir" || fail decide-no-worktree "親のワークツリーへ移れません。" 1
	fi
}

review_place
review_exe || review_no_bin
if [ -n "$resuming" ]; then
	resume_prepare
	review_place
	review_exe || review_no_bin
fi
review_host

# 古い形の下書きの送り直し（--resume で未完了の判断が無く、下書きだけが残っているとき）。
# 下書きから issue とコメントを、操作記録の段階（鍵は下書きの名前）に載せて送る。送れた下書きは消す。
resume_legacy_drafts() {
	printf '%s: 操作記録を入れる前の版が残した下書きがあります:\n' "$review_label"
	# 下書きの一覧は改行区切り。パスに空白があっても割れないよう、語の区切りを改行だけにする。
	rl_ifs="$IFS"
	IFS='
'
	set -f
	printf '  %s\n' $resume_legacy
	printf '送りますか [y/N]: '
	read -r rl_answer || rl_answer=n
	case "$rl_answer" in
	y | Y | yes | YES) ;;
	*) fail decide-not-confirmed "送らずにやめました。" 1 ;;
	esac
	fetch_all >"$result"
	rl_number=$("$JQ" '.mr.number' "$result")
	rl_url=$("$JQ" -r '.mr.url' "$result")
	rl_failed=""
	rl_issue_note=""
	for rl_file in $resume_legacy; do
		case "$rl_file" in
		*/review-issue-*)
			if rl_made=$(staged_send decide "$(basename "$rl_file")" issue issue "$rl_number" "$rl_url" "$rl_file"); then
				rl_issue_note=$(printf '%s' "$rl_made" | "$JQ" -r '"\nissue に回した分: #" + ((.number // 0) | tostring) + " " + (.url // "")')
				rm -f "$rl_file"
			else
				rl_failed=1
			fi
			;;
		esac
	done
	for rl_file in $resume_legacy; do
		case "$rl_file" in
		*/review-decide-*)
			if [ -n "$rl_issue_note" ]; then
				printf '%s\n' "$rl_issue_note" >>"$rl_file"
				rl_issue_note=""
			fi
			if staged_send decide "$(basename "$rl_file")" comment note "$rl_number" "$rl_url" "$rl_file" >/dev/null; then
				rm -f "$rl_file"
			else
				rl_failed=1
			fi
			;;
		esac
	done
	set +f
	IFS="$rl_ifs"
	[ -z "$rl_failed" ] || fail decide-legacy-incomplete "送れなかった下書きがあります。確かめてから --resume を打ち直してください。" 1
	printf 'OK: 古い形の下書きを送った\n'
	exit 0
}

# decide: 選択を置く前に、受け入れる指摘（対応しない・issue に回す）へ返信を残して解決する。
# <選択の JSON> <ダイジェスト>。計画の段（置く側の事前検査とダイジェストの照合を含む。何も置かない）
# が通れば、返信と解決をして結果を取り直す。失敗したら（ダイジェストの不一致を含む）答えを出して抜ける。
accept_before_place() {
	accept_plan="$state/review-accept-$family-$n.json"
	rm -f "$accept_plan"
	if [ -n "$resume_op" ]; then
		# 保存した判断から計画を書き直す。返信・解決が済んでいれば繰り返さず、結果を取り直して置く側へ渡す。
		ccnavi ops resume-plan "$resume_op" "$accept_plan" || exit 1
		op_use_plan "$accept_plan"
		if [ "$(op_field reply state)" = sent ] || [ "$("$JQ" '.threads | length' "$accept_plan")" -eq 0 ]; then
			accept_file="$accept_plan"
			refetch
			return 0
		fi
		accept_execute "$accept_plan"
		return 0
	fi
	accept_rc=0
	accept_out=$(ccnavi user reviewed "$n" --accept-unresolved --yes "$1" --digest "$2" --json --result "$result" --accept-plan "$accept_plan") ||
		accept_rc=$?
	if [ "$accept_rc" -ne 0 ]; then
		[ -z "$accept_out" ] || printf '%s\n' "$accept_out"
		exit 1
	fi
	op_use_plan "$accept_plan"
	accept_execute "$accept_plan"
}

# ---- 決めた行き先を投稿する。exe が state の置き場に書いた下書きから、issue を作ってコメントを残す。
#
# 置いたあとの投稿なので、ここで失敗しても決めたことは戻さない。失敗は post_warning に言い、
# 下書きは残す（打ち直せば投稿できる）。下書きの名前は exe と揃える（親の識別子。ブランチ名と違うことがある）。

post_decision() {
	if [ -n "$op_id" ]; then
		post_decision_staged
		return 0
	fi
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

# --resume の終わりに言うこと。
resume_report() {
	printf 'OK: 判断（操作 %s）の続きを送った\n' "$resume_op"
	[ -z "$issue_url" ] || printf 'issue に回した: %s\n' "$issue_url"
	[ -z "$post_warning" ] || printf '%s: %s\n' "$review_label" "$post_warning" >&2
	return 0
}

review_begin "$args_n"
# 抜けるときに exe に渡した JSON を消し、終わりの 1 行を診断ログに残す。終了コードは変えない。
# rm が失敗しても、`set -e` がその失敗の値で抜けて元の終了コードを上書きしないよう、失敗を無視してから元の値で抜け直す。
trap 'review_exit=$?; ccnavi_wap_end; accept_cleanup "$review_exit"; rm -f "$result" "$result.re" ${accept_plan:+"$accept_plan"} 2>/dev/null || :; log_info 終わった -- "sub=$sub" "exit=$review_exit"; exit "$review_exit"' EXIT
trap 'ccnavi_wap_end; exit 130' INT TERM HUP

review_wap

if [ -n "${resume_legacy:-}" ]; then
	# 名前が `<親>-<フェーズの数字>.md` のものだけ（`i0001` の glob が親 `i0001-x` の下書きに当たらないように）
	resume_legacy=""
	for rl_cand in "$state"/review-issue-"$family"-[0-9]*.md "$state"/review-decide-"$family"-[0-9]*.md; do
		[ -f "$rl_cand" ] || continue
		case "${rl_cand##*/}" in
		review-issue-"$family"-*[!0-9.md]* | review-decide-"$family"-*[!0-9.md]*) continue ;;
		esac
		resume_legacy="${resume_legacy:+$resume_legacy
}$rl_cand"
	done
	if [ -z "$resume_legacy" ]; then
		printf '%s: 未完了の判断はありません。\n' "$review_label"
		exit 0
	fi
	resume_legacy_drafts
fi

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
# 適用（ローカルへの反映）が write-and-push で戻された・失敗したとき、記録の「適用済み」を外す。
# 外さないと、--resume が存在しない適用を前提に、実在しない子を名指しして投稿してしまう。
unapply_local() {
	ua_op=$("$JQ" -r '.op // empty' "$accept_file" 2>/dev/null || :)
	[ -n "$ua_op" ] || ua_op="${resume_op:-}"
	[ -z "$ua_op" ] || ccnavi ops applied "$ua_op" local false 2>/dev/null || :
}
[ -z "$choices" ] || wap_start "$family"
fetch_all >"$result"
if [ -n "$choices" ]; then
	# 失敗の答え（見せた指摘と今の指摘が違う、など）も JSON で返すので、先に出してから終わる。
	tell_skew
	# 適用まで済んでいる判断の続きは、投稿の段階だけを進める。
	if [ -n "$resume_op" ] && [ "$(ccnavi ops show "$resume_op" | "$JQ" -r '.applied.local // empty')" = true ]; then
		op_id="$resume_op"
		post_decision_staged
		resume_report
		exit 0
	fi
	# 受け入れる指摘（対応しない・このフェーズで直す・issue に回す）のうち、受け入れる分には、置く前に返信を残して解決する。
	# できなければここで止める。
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
		unapply_local
		printf '%s\n' "$out"
		exit 1
	}
	post_decision "$n"
	if [ -n "$resuming" ]; then
		resume_report
		exit 0
	fi
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
	fail decide-choose-unreadable "選んだものを読めません（${chosen}）。" 1
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
		unapply_local
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
		unapply_local
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
