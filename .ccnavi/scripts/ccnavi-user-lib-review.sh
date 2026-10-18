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
# スレッドの node id（GraphQL）、GitLab なら discussion の id。
# 呼ぶのは置く前（マーカーを置く前）。1 件でもできなければ受け入れは未完了として止める。
#
# ホストに書く前に、計画の id が API の経路に使える字で（GitLab は 16 進）、取り直した結果に
# 在ることを確かめる。書いたあとは結果を取り直して置く呼び出しに渡す（実行ファイルが、受け入れた
# スレッドが全部ホストで解決済みで、ほかの未解決が増えていないことを実状態で確かめる）。
# 打ち直すと、返信まで済んで解決できなかったスレッドには返信が重なる。
# <スレッドの id> <返信の本文> <MR の番号>
accept_reply() {
	accept_id="$1"
	accept_text="$2"
	if [ "$kind" = github ]; then
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
		if ! accept_reply "$accept_id" "$accept_text" "$accept_mr"; then
			printf '%s: 受け入れを完了できない。スレッド %s に返信を残せなかった。何も置いていない（返信が済んだ分は残る。解決はまだ誰もしていない）。\n' "$review_label" "$accept_key" >&2
			return 1
		fi
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
