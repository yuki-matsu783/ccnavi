# ccnavi-user-lib-review.sh  ユーザ専用のレビューの sh（ccnavi-user-decide.sh・ccnavi-user-close-early.sh）が共有する、
# ユーザの判断をホストに書く部品。単体では動かない。
#
#   . "$(dirname "$0")/ccnavi-common.sh"
#   review_label=ccnavi-user-decide
#   ccnavi_load ccnavi-common-review.sh
#   ccnavi_load ccnavi-user-lib-review.sh
#
# 置くのは、受け入れた指摘のスレッドへの返信と解決（accept_*）と、issue を作る関数（create_issue）。
# どれもユーザが決めたことをホストに書くので、エージェントも読む共通部（ccnavi-common*.sh）には置かない。
# 共通部に置くと、`sh -c '. …common….sh; 関数'` の形で、ユーザ専用の sh の名前を出さずに呼べてしまう
# （hook は ccnavi-user- で始まる sh を名前で止める。この部品もその名前を持つ）。
#
# 呼ぶ側が決めておくもの: review_label（文面の頭）、result（取ってきた結果の JSON）、state、kind・JQ など
# ccnavi-common-review.sh の review_host が決めるもの。抜けるときに accept_cleanup <終了コード> を呼ぶ。

# 計画（ccnavi --accept-plan が書く）のパスと、ホストに書いたあとに残す計画のパス。置く呼び出しに渡す計画。
accept_plan=""
accept_kept=""
accept_file=""

# ---- 受け入れた指摘のスレッドに返信を残して、解決済みにする。
#
# 実行ファイルが書いた計画（`--accept-plan`。{version, digest, threads: [{id, key, reply}]}）のスレッドを、
# 全部に返信してから、全部を解決する（解決に失敗しても返信が重ならない順）。id は GitHub ならレビューの
# スレッドの node id（GraphQL）か、GraphQL が 403 の環境では先頭コメントの id（数字。REST）、GitLab なら discussion の id。
# GitHub の道は id の形で選ぶ（数字だけなら REST、でなければ GraphQL）。id は取り直した結果にあるものだけを
# 通すので、取った道と書く道は同じになる（1 回の実行の中で道が混ざらない）。REST の返信は、そのスレッドの先頭コメントへの返信。
# 呼ぶのは置く前（マーカーを置く前）。1 件でもできなければ受け入れは未完了として止める。
#
# ホストに書く前に、計画の id が API の経路に使える字で（GitLab は 16 進）、取り直した結果に
# 在ることを確かめる。書いたあとは結果を取り直して置く呼び出しに渡す（実行ファイルが、受け入れた
# スレッドが全部ホストで解決済みで、ほかの未解決が増えていないことを実状態で確かめる）。
# 打ち直すと、返信まで済んで解決できなかったスレッドには返信が重なる。
# 返信の進み具合を操作記録の applied に残す（`reply_try_<id>` は送る前、`reply_<id>` は送れた後）。
# op_id が空（記録の無い形）なら何もしない。
reply_mark() {
	[ -n "${op_id:-}" ] || return 0
	ccnavi ops applied "$op_id" "$1" true 2>/dev/null || :
}

# このスレッドへの返信を送り済みか。<スレッドの id> <取り直した結果のファイル>。送り済みなら 0。
reply_already_sent() {
	[ -n "${op_id:-}" ] || return 1
	ra_rec=$(ccnavi ops show "$op_id" 2>/dev/null) || return 1
	if [ "$(printf '%s' "$ra_rec" | "$JQ" -r --arg k "reply_$1" '.applied[$k] // empty')" = true ]; then
		return 0
	fi
	[ "$(printf '%s' "$ra_rec" | "$JQ" -r --arg k "reply_try_$1" '.applied[$k] // empty')" = true ] || return 1
	ra_last=$("$JQ" -r --arg id "$1" '[.threads[] | select(.id == $id)][0].last_author // empty' "$2" 2>/dev/null)
	# 最後のコメントがスレッドの最初のコメントと別のもの（作成の時刻が違う）のときだけ、返信とみなす。
	# 自分が立てたスレッドの最初のコメントを、自分の返信と取り違えないため（取り違えると返信が欠ける。
	# 時刻が同じ秒に収まる返信は送り直す側に倒れ、二重の返信は出うるが欠落は出さない）。
	ra_later=$("$JQ" -r --arg id "$1" '[.threads[] | select(.id == $id)][0] | if (.last_at // "") != "" and (.last_at // "") != (.created_at // "") then "yes" else "" end' "$2" 2>/dev/null)
	op_account
	if [ -n "$ra_last" ] && [ -n "${op_who:-}" ] && [ "$ra_last" = "$op_who" ] && [ -n "$ra_later" ]; then
		reply_mark "reply_$1"
		return 0
	fi
	return 1
}

# <スレッドの id> <返信の本文> <MR の番号>
accept_reply() {
	accept_id="$1"
	accept_text="$2"
	if [ "$kind" = github ]; then
		case "$accept_id" in
		"" | *[!0-9]*) ;;
		*)
			accept_body=$("$JQ" -n --arg b "$accept_text" '{body: $b}')
			accept_res=$(api POST "repos/$path/pulls/$3/comments/$accept_id/replies" "$accept_body") || return 1
			[ -n "$(printf '%s' "$accept_res" | "$JQ" -r '.id // empty' 2>/dev/null)" ]
			return
			;;
		esac
		accept_query=$("$JQ" -n --arg id "$accept_id" --arg b "$accept_text" '{
			query: "mutation($id:ID!,$b:String!){addPullRequestReviewThreadReply(input:{pullRequestReviewThreadId:$id,body:$b}){comment{id}}}",
			variables: {id: $id, b: $b}}')
		accept_res=$(api POST graphql "$accept_query") || return 1
		[ -n "$(printf '%s' "$accept_res" | "$JQ" -r '.data.addPullRequestReviewThreadReply.comment.id // empty' 2>/dev/null)" ]
	else
		accept_body=$("$JQ" -n --arg b "$accept_text" '{body: $b}')
		accept_res=$(api POST "projects/$(encoded_path)/merge_requests/$3/discussions/$accept_id/notes" "$accept_body") || return 1
		[ -n "$(printf '%s' "$accept_res" | "$JQ" -r '.id // empty' 2>/dev/null)" ]
	fi
}

accept_resolve() {
	accept_id="$1"
	if [ "$kind" = github ]; then
		case "$accept_id" in
		"" | *[!0-9]*) ;;
		*)
			accept_res=$(api POST "repos/$path/pulls/$2/ccr/comments/$accept_id/resolve" '{}') || return 1
			return 0
			;;
		esac
		accept_query=$("$JQ" -n --arg id "$accept_id" '{
			query: "mutation($id:ID!){resolveReviewThread(input:{threadId:$id}){thread{isResolved}}}",
			variables: {id: $id}}')
		accept_res=$(api POST graphql "$accept_query") || return 1
		[ "$(printf '%s' "$accept_res" | "$JQ" -r '.data.resolveReviewThread.thread.isResolved // empty' 2>/dev/null)" = true ]
	else
		accept_res=$(api PUT "projects/$(encoded_path)/merge_requests/$2/discussions/$accept_id" '{"resolved":true}') || return 1
		[ -n "$(printf '%s' "$accept_res" | "$JQ" -r '.id // empty' 2>/dev/null)" ]
	fi
}

# <計画のファイル> <取り直した結果のファイル>。成功は 0、できなければ理由を標準エラーへ出して 1。
accept_threads() {
	accept_file="$1"
	accept_rfile="$2"
	accept_mr=$("$JQ" '.mr.number' "$accept_rfile")
	accept_n=$("$JQ" '.threads | length' "$accept_file" 2>/dev/null) || {
		printf '%s: 受け入れの計画を読めない（%s）。何も置いていない。\n' "$review_label" "$accept_file" >&2
		return 1
	}
	# 1. ホストに書く前の確かめ（できなければ、ホストには何も書かない）
	accept_i=0
	while [ "$accept_i" -lt "$accept_n" ]; do
		accept_id=$("$JQ" -r --argjson i "$accept_i" '.threads[$i].id' "$accept_file")
		case "$accept_id" in
		'' | *[!A-Za-z0-9_=-]*)
			printf '%s: 受け入れの計画のスレッドの id が読めない。ホストには何も書いていない。\n' "$review_label" >&2
			return 1
			;;
		esac
		if [ "$kind" != github ]; then
			case "$accept_id" in
			*[!0-9a-f]*)
				printf '%s: 受け入れの計画のスレッドの id が GitLab の discussion の id（16 進）でない。ホストには何も書いていない。\n' "$review_label" >&2
				return 1
				;;
			esac
		fi
		if [ "$("$JQ" --arg id "$accept_id" '[.threads[].id] | index($id) != null' "$accept_rfile")" != true ]; then
			printf '%s: 受け入れるスレッドが取り直した結果に無い。ホストには何も書いていない。\n' "$review_label" >&2
			return 1
		fi
		accept_i=$((accept_i + 1))
	done
	# 2. 全部に返信する
	accept_i=0
	while [ "$accept_i" -lt "$accept_n" ]; do
		accept_id=$("$JQ" -r --argjson i "$accept_i" '.threads[$i].id' "$accept_file")
		accept_key=$("$JQ" -r --argjson i "$accept_i" '.threads[$i].key' "$accept_file")
		accept_text=$("$JQ" -r --argjson i "$accept_i" '.threads[$i].reply' "$accept_file")
		# 操作記録があれば、返信を送り済みのスレッドには重ねない（打ち直し・--resume で二重に返信しない）。
		# 試して結果が分からないスレッド（記録に試した印だけある）は、ホストのスレッドの最後のコメントが
		# 自分のものなら送り済みとみなし、そうでなければ送り直す。
		if reply_already_sent "$accept_id" "$accept_rfile"; then
			accept_kept="$accept_file"
			accept_plan=""
			accept_i=$((accept_i + 1))
			continue
		fi
		# 送る前の印を書けなければ送らない（書けないまま送って落ちると、二重に返信しうる）
		if [ -n "${op_id:-}" ] && ! ccnavi ops applied "$op_id" "reply_try_$accept_id" true 2>/dev/null; then
			printf '%s: 操作記録に返信の印を書けなかった。何も送らずに止めた。\n' "$review_label" >&2
			return 1
		fi
		if ! accept_reply "$accept_id" "$accept_text" "$accept_mr"; then
			printf '%s: 受け入れを完了できない。スレッド %s に返信を残せなかった。何も置いていない（返信が済んだ分は残る。解決はまだ誰もしていない）。\n' "$review_label" "$accept_key" >&2
			return 1
		fi
		reply_mark "reply_$accept_id"
		# 1 件でも書いたら、置けずに終わったとき計画を残す（accept_cleanup）
		accept_kept="$accept_file"
		accept_plan=""
		accept_i=$((accept_i + 1))
	done
	# 3. 全部を解決する
	accept_i=0
	while [ "$accept_i" -lt "$accept_n" ]; do
		accept_id=$("$JQ" -r --argjson i "$accept_i" '.threads[$i].id' "$accept_file")
		accept_key=$("$JQ" -r --argjson i "$accept_i" '.threads[$i].key' "$accept_file")
		if ! accept_resolve "$accept_id" "$accept_mr"; then
			printf '%s: 受け入れを完了できない。スレッド %s を解決できなかった（返信は残した）。何も置いていない（先に解決した分は残る）。\n' "$review_label" "$accept_key" >&2
			return 1
		fi
		accept_i=$((accept_i + 1))
	done
}

# 計画のスレッドに返信と解決をして、結果を取り直す。<計画のファイル>（$result は計画を書いたときの結果）
# 受け入れるスレッドが無ければ何もしない。ホストに書いたあとは、計画を kept に移し、置くのに
# 失敗して終わったときは残す（accept_cleanup）。置く呼び出しには $accept_file を渡す。
accept_execute() {
	accept_file="$1"
	accept_count=$("$JQ" '.threads | length' "$accept_file" 2>/dev/null) || {
		printf '%s: 受け入れの計画を読めない（%s）。何も置いていない。\n' "$review_label" "$accept_file" >&2
		exit 1
	}
	[ "$accept_count" -gt 0 ] || return 0
	accept_threads "$accept_file" "$result" || exit 1
	op_set reply sent
	refetch
}

# 抜けるときの後始末。置けて終わったら計画を消す。ホストに書いたのに置けずに終わったなら残して言う。
accept_cleanup() {
	[ -z "$accept_kept" ] || {
		if [ "$1" -eq 0 ]; then
			rm -f "$accept_kept"
		else
			printf '%s: スレッドへの返信か解決をホストに書いたが、受け入れを置けなかった。計画を残した（%s）。打ち直すと、解決済みのスレッドは指摘の一覧から外れます。issue に回す選択だった分は、この計画を見て手で扱ってください。\n' "$review_label" "$accept_kept" >&2
		fi
	}
}

# ---- issue を作る。下書きの 1 行目が題、3 行目からが本文。{number, url} を返す。

create_issue() {
	draft="$1"
	if [ "$kind" = github ]; then
		payload=$("$JQ" -n --rawfile all "$draft" \
			'($all | split("\n")) as $l | {title: ($l[0]), body: ($l[2:] | join("\n"))}')
		api POST "repos/$path/issues" "$payload" | "$JQ" '{number: .number, url: .html_url}'
	else
		payload=$("$JQ" -n --rawfile all "$draft" \
			'($all | split("\n")) as $l | {title: ($l[0]), description: ($l[2:] | join("\n"))}')
		api POST "projects/$(encoded_path)/issues" "$payload" | "$JQ" '{number: .iid, url: .web_url}'
	fi
}

# ---- issue へのコメント（長い指摘の続き）。{url} を返す。<issue の番号> <issue の URL> <本文のファイル>

issue_note() {
	in_body=$("$JQ" -Rs '{body: .}' <"$3")
	if [ "$kind" = github ]; then
		api POST "repos/$path/issues/$1/comments" "$in_body" |
			"$JQ" 'if ((.html_url // "") == "") then error("no html_url") else {url: .html_url} end'
	else
		api POST "projects/$(encoded_path)/issues/$1/notes" "$in_body" |
			"$JQ" --arg u "$2" 'if .id == null then error("no note id") else {url: ($u + "#note_" + (.id | tostring))} end'
	fi
}

# ---- 操作記録。判断を保存した記録（`ccnavi ops`）に、投稿の段階を記録し、投稿の前に照合する。
#
# 操作ID は受け入れの計画（`--accept-plan` が書く）が持つ。記録が無い形（古い実行ファイルの答え）では
# op_id が空のままで、ここの関数は何もしない（投稿は前の形に戻る）。
# 投稿の本文は記録に持たない。`ccnavi ops post-text` が保存した判断から作り直す。
# 本文の末尾に目印 `<!-- ccnavi:op=<操作ID> stage=<段階ID> -->` を足すので、送ったかどうかを
# あとから目印で照合できる（先頭の `<!-- ccnavi:request …` などの目印は変えない）。

op_id=""
op_who=""
op_who_read=""

op_use_plan() {
	op_id=$("$JQ" -r '.op // empty' "$1" 2>/dev/null || :)
}

op_set() {
	[ -n "$op_id" ] || return 0
	ccnavi ops stage-set "$op_id" "$@" 2>/dev/null ||
		printf '%s: 操作記録に段階 %s を書けなかった。\n' "$review_label" "$1" >&2
}

# <段階ID> <欄>。無ければ空。
op_field() {
	ccnavi ops show "$op_id" 2>/dev/null | "$JQ" -r --arg s "$1" --arg f "$2" '[.stages[]? | select(.id == $s)][0][$f] // empty'
}

op_account() {
	if [ -z "$op_who_read" ]; then
		op_who_read=1
		op_who=$(account || :)
	fi
}

# 段階 1 つを送る。<段階ID> <種類 issue|note|issue-note> <対象の番号> <対象の URL>
# 送れたら（または送り済みなら）URL を標準出力に出して 0。送れなかった・確定できないときは 1。
# 前に試した形跡（試した回数が 1 以上、または unknown）があれば、先にホストの一覧を目印で照合する。
post_staged() {
	ps_stage="$1"
	ps_kind="$2"
	ps_target="$3"
	ps_url="$4"
	ps_state=$(op_field "$ps_stage" state)
	if [ "$ps_state" = sent ]; then
		op_field "$ps_stage" url
		return 0
	fi
	ps_tries=$(op_field "$ps_stage" tries)
	if [ "${ps_tries:-0}" -gt 0 ] || [ "$ps_state" = unknown ]; then
		op_account
		ps_found=$(find_marked "$ps_kind" "$op_id" "$ps_stage" "$ps_target" "$op_who" "$ps_url")
		ps_complete="${ps_found%% *}"
		ps_urls="${ps_found#* }"
		ps_verdict=$(ccnavi ops reconcile "$op_id" "$ps_stage" "$ps_urls" "$ps_complete" "$op_who" 2>/dev/null) || ps_verdict="stop 照合できなかった"
		case "$ps_verdict" in
		sent*)
			op_set "$ps_stage" sent "$ps_urls" "$op_who"
			printf '%s' "$ps_urls"
			return 0
			;;
		send*) : ;;
		unknown*)
			op_set "$ps_stage" unknown
			printf '%s: %s を送ったか確定できない（%s）。自動では送り直さない。ホストで目印 ccnavi:op=%s stage=%s を探して確かめてください。\n' \
				"$review_label" "$ps_stage" "${ps_verdict#unknown }" "$op_id" "$ps_stage" >&2
			return 1
			;;
		*)
			printf '%s: %s の送信を照合できない（%s）。止めた。ホストで目印 ccnavi:op=%s stage=%s を探して確かめてください。\n' \
				"$review_label" "$ps_stage" "${ps_verdict#stop }" "$op_id" "$ps_stage" >&2
			return 1
			;;
		esac
	fi
	ps_text="$state/review-post-$$-$ps_stage.md"
	ps_body="$ps_text.send"
	ccnavi ops post-text "$op_id" "$ps_stage" "$ps_text" || {
		rm -f "$ps_text"
		return 1
	}
	{
		cat "$ps_text"
		printf '\n<!-- ccnavi:op=%s stage=%s -->\n' "$op_id" "$ps_stage"
	} >"$ps_body"
	op_account
	if ! ccnavi ops stage-set "$op_id" "$ps_stage" pending "" "$op_who" 2>/dev/null; then
		printf '%s: 操作記録に段階 %s を書けなかった。送らずに止めた。\n' "$review_label" "$ps_stage" >&2
		rm -f "$ps_text" "$ps_body"
		return 1
	fi
	ps_posted=""
	case "$ps_kind" in
	issue) ps_posted=$(create_issue "$ps_body" | "$JQ" -r '.url // empty' 2>/dev/null || :) ;;
	note) ps_posted=$(comment "$ps_target" "$ps_url" "$ps_body" | "$JQ" -r '.url // empty' 2>/dev/null || :) ;;
	issue-note) ps_posted=$(issue_note "$ps_target" "$ps_url" "$ps_body" | "$JQ" -r '.url // empty' 2>/dev/null || :) ;;
	esac
	rm -f "$ps_text" "$ps_body"
	if [ -z "$ps_posted" ]; then
		op_set "$ps_stage" unknown
		return 1
	fi
	op_account
	op_set "$ps_stage" sent "$ps_posted" "$op_who"
	printf '%s' "$ps_posted"
}

# 投稿の段階をすべて進める（issue → 続きのコメント → 決めた内容のコメント）。
# 置いたあとの投稿なので、失敗しても決めたことは戻さない。post_warning に言い、記録を残す。
#
# 操作 ID のロック（`locks/ops/<操作ID>`。staged_send と同じ置き場・待ち方）を、段階の進行全体で持つ。
# 2 つの --resume・打ち直しが同じ段階を同時に「未送信」と見て、二重に送るのを防ぐ。
# --resume は返信・解決の前から持つ（decide.sh が op_lock_take を打つ）。すでに同じロックを持っていれば（入れ子）何もしない。
# ロックの取得はグローバル変数 1 組を使うので、write-and-push などほかのロックの状態は退避して戻す。
op_lk_dir=""
op_lk_mark=""
op_lk_prev=""
op_lk_prev_set=""

# 取れたら（入れ子・ロックが使えない構成を含む）0、取れなければ 1（post_warning に言う）。<操作ID>
# 入れ子の判定は ccnavi_lock_take が持ち主の情報を照合して行う（偽の CCNAVI_LOCK_HELD では進まない）。
# root が無い・ロックの部品が無い構成では、取らずに進む（staged_send と同じ。root は絶対パスで返るので通常は通らない）。
# 取ったかどうかは op_lk_dir で分かる（入れ子・取らずに進んだときは空のまま）。
op_lock_take() {
	[ -z "$op_lk_dir" ] || return 0
	[ -n "${root:-}" ] && command -v ccnavi_lock_take >/dev/null 2>&1 || return 0
	ol_sv_dir="$ccnavi_lock_dir"
	ol_sv_mark="$ccnavi_lock_mark"
	ol_sv_set="$ccnavi_lock_set_held"
	ol_sv_prev="$ccnavi_lock_prev_held"
	ol_sv_prev_set="$ccnavi_lock_prev_set"
	# 入れ子で何も取らなかったとき、ほかのロック（write-and-push）の置き場を取ったものと読まないよう、先に空にする。
	ccnavi_lock_dir=""
	ol_rc=0
	ccnavi_lock_take "$root" ops "$1" 30 || ol_rc=$?
	if [ "$ol_rc" -eq 0 ] && [ -n "$ccnavi_lock_dir" ]; then
		op_lk_dir="$ccnavi_lock_dir"
		op_lk_mark="$ccnavi_lock_mark"
		op_lk_prev="$ccnavi_lock_prev_held"
		op_lk_prev_set="$ccnavi_lock_prev_set"
	fi
	ccnavi_lock_dir="$ol_sv_dir"
	ccnavi_lock_mark="$ol_sv_mark"
	ccnavi_lock_set_held="$ol_sv_set"
	ccnavi_lock_prev_held="$ol_sv_prev"
	ccnavi_lock_prev_set="$ol_sv_prev_set"
	[ "$ol_rc" -eq 0 ] && return 0
	if [ "$ol_rc" -eq 2 ]; then
		post_warning="操作 $1 のロックが古いまま残っていて、片付けられなかった。何も送らず止めた。ロックの置き場（locks/ops/$1）をユーザが見て片付けてから 'sh .ccnavi/scripts/ccnavi-user-decide.sh --resume $1' で続きを送れる"
	else
		post_warning="同じ判断の投稿が別のプロセスで実行中（操作 $1）。待っても取れなかったので何も送らず止めた。終わってから 'sh .ccnavi/scripts/ccnavi-user-decide.sh --resume $1' で続きを送れる"
	fi
	return 1
}

# 取ったロックを外す（取っていなければ何もしない）。
op_lock_release() {
	[ -n "$op_lk_dir" ] || return 0
	ol_sv_dir="$ccnavi_lock_dir"
	ol_sv_mark="$ccnavi_lock_mark"
	ol_sv_set="$ccnavi_lock_set_held"
	ol_sv_prev="$ccnavi_lock_prev_held"
	ol_sv_prev_set="$ccnavi_lock_prev_set"
	ccnavi_lock_dir="$op_lk_dir"
	ccnavi_lock_mark="$op_lk_mark"
	ccnavi_lock_set_held=yes
	ccnavi_lock_prev_held="$op_lk_prev"
	ccnavi_lock_prev_set="$op_lk_prev_set"
	ccnavi_lock_drop
	ccnavi_lock_dir="$ol_sv_dir"
	ccnavi_lock_mark="$ol_sv_mark"
	ccnavi_lock_set_held="$ol_sv_set"
	ccnavi_lock_prev_held="$ol_sv_prev"
	ccnavi_lock_prev_set="$ol_sv_prev_set"
	op_lk_dir=""
	op_lk_mark=""
}

post_decision_staged() {
	issue_url=""
	post_warning=""
	# すでに持っている（--resume が先に取った）ときは、ここで取らず外さない。外すのは自分が取ったときだけ。
	pl_had="$op_lk_dir"
	op_lock_take "$op_id" || return 1
	post_decision_staged_locked
	pl_rc=$?
	[ -n "$pl_had" ] || op_lock_release
	return "$pl_rc"
}

post_decision_staged_locked() {
	issue_url=""
	post_warning=""
	pd_mr=$("$JQ" '.mr.number' "$result")
	pd_mr_url=$("$JQ" -r '.mr.url' "$result")
	pd_ids=$(ccnavi ops show "$op_id" 2>/dev/null | "$JQ" -r '.stages[].id') || pd_ids=""
	pd_issue_no=""
	pd_failed=""
	for pd_stage in $pd_ids; do
		case "$pd_stage" in
		reply) continue ;;
		issue)
			if issue_url=$(post_staged issue issue "$pd_mr" "$pd_mr_url"); then
				pd_issue_no="${issue_url##*/}"
			else
				issue_url=""
				pd_failed="$pd_failed issue"
			fi
			;;
		issue-part-*)
			[ -n "$pd_issue_no" ] || {
				pd_failed="$pd_failed $pd_stage"
				continue
			}
			post_staged "$pd_stage" issue-note "$pd_issue_no" "$issue_url" >/dev/null || pd_failed="$pd_failed $pd_stage"
			;;
		note)
			post_staged note note "$pd_mr" "$pd_mr_url" >/dev/null || pd_failed="$pd_failed note"
			;;
		esac
	done
	if [ -n "$pd_failed" ]; then
		post_warning="引き継ぎ未完了（送れなかった段階:${pd_failed}）。ユーザが 'sh .ccnavi/scripts/ccnavi-user-decide.sh --resume $op_id' で続きを送れる"
	else
		ccnavi ops done "$op_id" >/dev/null 2>&1 || :
	fi
}
