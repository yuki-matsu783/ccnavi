#!/bin/sh
# ccnavi-review — レビューの依頼と確認。親（メインエージェント）だけが呼ぶ。
#
#   sh .claude/scripts/ccnavi-review.sh request --phase <N> --body-file <依頼文>
#   sh .claude/scripts/ccnavi-review.sh check   --phase <N>
#   sh .claude/scripts/ccnavi-review.sh note    --body-file <本文>
#   sh .claude/scripts/ccnavi-review.sh accept  <N>          （人が端末で打つ）
#   sh .claude/scripts/ccnavi-review.sh fetch                 （取ってきた写しを見る）
#
# リモート（GitHub / GitLab）を読み書きするのはこのスクリプトで、ccnavi の実行ファイルは
# ネットワークに出ない。実行ファイルが見るのは作業ツリーの中（フェーズ・ブランチ・印）
# だけで、マージリクエストの中身はここが取ってきて JSON で渡す（--result）。
#
#   request: `ccnavi review prepare` が前提を確かめ、依頼の本文とマージリクエストの
#            下書きを書き出す → ここが（無ければ）MR を下書きで作り、依頼を投稿する
#            → `ccnavi review requested` が印を置く
#            人はレビューを MR で行うので、入れ物が無いことで止めない。題から Draft を
#            外してマージするのは人の手に残す。
#   check:   ここがスレッドとレビューを取ってくる → `ccnavi review check` が判定して印を置く
#   accept:  ここが取ってくる → `ccnavi --reviewed N --accept-unresolved` が人に見せて印を置く
#            → 受け入れた一覧をここがコメントに写す
#
# リモートへの道具は、gh / glab があればそれ（認証はツールに任せる）、無ければ curl と
# GITHUB_TOKEN / GITLAB_TOKEN。どちらも無ければ止まる。結果の組み立てには jq が要る。
# 道具は起動時に絶対パスへ解いて固定する。PATH の細工で差し替えられないように。
#
# 親の作業ツリーの中で実行すること。どの親かは cwd から引く。
# 終了コード: 0 成功 / 1 前提の未充足 / 2 引数か環境の誤り

set -eu

usage() {
	cat <<'USAGE'
sh .claude/scripts/ccnavi-review.sh <request|check|note|accept|fetch> [--phase <N>] [--body-file <path>]

  request  --phase <N> --body-file <依頼文>   前提を確かめ、MR が無ければ作り、依頼を投稿して印を置く
  check    --phase <N>                         依頼より後の未解決スレッドが無ければ印を置く
  note     --body-file <本文>                  判断の記録を MR のコメントに写す
  accept   <N>                                 未解決を残したまま進める判断（人が端末で打つ）
  handoff  --body-file <題と本文>              残った指摘を別の issue に切り出し、MR に引き継ぎの note を残す
  ready                                        閉じられて wip を片付け push 済みなら Draft を外す（マージに進んでよいの合図。マージは人が squash で）
  wrapup   --reason <理由> [--no-issue]        まだ残っているが締める判断（人が端末で打つ）。残りを issue に写す。Draft は親が ready で外す
  fetch                                        リモートから取ってきた写し（JSON）を標準出力へ
  origin                                       origin をどう読んだか（ホスト・scheme・API の綴り）

gh / glab があればそれを使う。無ければ curl と GITLAB_TOKEN / GITHUB_TOKEN。jq が要る。
USAGE
}

fail() {
	printf 'ccnavi-review: %s\n' "$1" >&2
	exit "${2:-1}"
}

# 共通部分。ワークスペースルートの探し方と、URL の伏せ字はここにある。
. "$(dirname "$0")/ccnavi-common.sh"

[ "$#" -ge 1 ] || {
	usage
	exit 2
}
sub="$1"
shift
case "$sub" in
request | check | note | accept | handoff | ready | wrapup | fetch | origin) ;;
-h | --help | help)
	usage
	exit 0
	;;
*)
	fail "$sub は通しません。使えるのは request / check / note / accept / handoff / ready / wrapup / fetch / origin です。" 2
	;;
esac

# ---- 場所。ワークスペースルートと、いまの作業ツリー。Windows の Git Bash では pwd -W で綴りを直す。
#
# 根は git に聞かない。モード B では cwd がプロジェクトの中にあると git は
# プロジェクトを答え、写し・印・状態の置き場がプロジェクト側にずれる。
# 道具の置き場は上へ歩いて探す（設計 §25.8）。
root=$(ccnavi_workspace) ||
	fail "ワークスペースルートが見つかりません（.claude/scripts/ を持つ親を cwd から上へ探しました）。ワークスペースの中で実行するか、CCNAVI_WORKSPACE にワークスペースルートの絶対パスを渡してください。" 2
here="$(pwd -W 2>/dev/null || pwd)"
state="$root/${CCNAVI_STATE:-.claude/ccnavi/state}"

# ---- 実行ファイル。設定に書かれた綴りを優先し、無ければ既定の置き場、それも無ければソース。

case "${CCNAVI_BIN_PATH:-}" in
/* | [A-Za-z]:*) bin="$CCNAVI_BIN_PATH" ;;
*) bin="$root/${CCNAVI_BIN_PATH:-dist/ccnavi/ccnavi}" ;;
esac
if [ -x "$bin" ]; then
	ccnavi() { "$bin" --root "$root" --cwd "$here" "$@"; }
elif [ -x "$bin.exe" ]; then
	ccnavi() { "$bin.exe" --root "$root" --cwd "$here" "$@"; }
elif [ -f "$root/ccnavi/__main__.py" ]; then
	ccnavi() { (cd "$root" && uv run python -m ccnavi --root "$root" --cwd "$here" "$@"); }
else
	fail "ccnavi の実行ファイルが無い ($bin)。build.py で組み立ててください。" 2
fi

# ---- リモート。origin の URL でホストを見分ける。

origin=$(git remote get-url origin 2>/dev/null || :)
[ -z "$origin" ] && fail "origin が無い。レビューはマージリクエストの実物に結ぶので、リモートが要る。"
# 伏せた綴りを、読む前に 1 度だけ作る。以降、文面に使うのはこれだけ。
# 生の $origin を文面に入れる綴りを 1 つも残さないことで、次に fail を足す人が
# 素通りできないようにする。URL に資格情報を埋める使い方は普通にあり、
# 出力はエージェントの文脈にも記録にも残る。
origin_shown=$(ccnavi_mask_url "$origin")
# scheme は origin から取る。https に決め打ちすると、社内や手元で平文で立てた
# GitLab（`http://localhost:8929` のような形）に当たらない。ssh の綴りには
# scheme が無いので、そこだけ https にする。
# host には**ポートを残す**。落とすと `:8929` のような立て方が全滅し、しかも
# 落ちたポートがプロジェクトのパスの先頭に混ざる（`8929/demo/greeter`）。
case "$origin" in
http://*) scheme=http ;;
*) scheme=https ;;
esac
rest=$(printf '%s' "$origin" | sed -E 's#^(https?://|git@|ssh://git@)##')
[ "$rest" = "$origin" ] && fail "origin の綴りを読めない ($origin_shown)。"
# `user:token@host` の形はユーザ情報を落とす。URL にトークンを埋める使い方は普通にあり、
# 落とさないと host にトークンが混ざり、API の綴りにも `origin` の出力にも漏れる（実測）。
# 認証は gh / glab か GITLAB_TOKEN / GITHUB_TOKEN で行い、URL 側の資格情報は使わない。
authority="${rest%%/*}"
case "$authority" in
*@*) rest="${authority##*@}${rest#"$authority"}" ;;
esac
host="${rest%%/*}"
# ssh の `git@host:group/proj` は `:` の後ろがパス。数字だけならポート、
# そうでなければパスの先頭なので落とす。
case "$host" in
*:*)
	case "${host##*:}" in
	'' | *[!0-9]*) host="${host%%:*}" ;;
	esac
	;;
esac
[ -n "$host" ] || fail "origin からホストを読めない ($origin_shown)。"
tail="${rest#"$host"}"
while :; do
	case "$tail" in
	[/:]*) tail="${tail#?}" ;;
	*) break ;;
	esac
done
path="${tail%.git}"
path="${path%/}"
[ -n "$path" ] || fail "origin からプロジェクトのパスを読めない ($origin_shown)。"
case "$host" in
github.com | github.com:*)
	kind=github
	token_name=GITHUB_TOKEN
	api_base="https://api.github.com"
	;;
*)
	kind=gitlab
	token_name=GITLAB_TOKEN
	api_base="$scheme://$host/api/v4"
	;;
esac
branch=$(git rev-parse --abbrev-ref HEAD)

# ---- 道具。絶対パスに解いて固定する。

JQ=$(command -v jq 2>/dev/null || :)
[ -z "$JQ" ] && fail "jq が無い。結果の JSON を組み立てられない。" 2
# gh / glab は「入っている」だけでは足りない。そのホストで認証されていなければ
# 通らない（手元に立てた GitLab に glab を繋いでいない、が普通にある）。
# 1 度だけ疎通を試して、通らなければ curl とトークンへ落ちる。
transport=""
if [ "$kind" = github ]; then
	CLI=$(command -v gh 2>/dev/null || :)
	if [ -n "$CLI" ] && "$CLI" api --hostname "$host" "repos/$path" >/dev/null 2>&1; then
		transport=gh
	fi
else
	CLI=$(command -v glab 2>/dev/null || :)
	if [ -n "$CLI" ] && "$CLI" api --hostname "$host" "projects/$(printf '%s' "$path" | "$JQ" -Rr '@uri')" >/dev/null 2>&1; then
		transport=glab
	fi
fi
if [ -z "$transport" ]; then
	CURL=$(command -v curl 2>/dev/null || :)
	eval "token=\${$token_name:-}"
	if [ "$kind" = github ]; then cli_name=gh; else cli_name=glab; fi
	if [ -n "$CURL" ] && [ -n "$token" ]; then
		transport=curl
	elif [ -n "$CURL" ]; then
		fail "$cli_name が $host で使えず（未導入か未認証）、curl に付ける $token_name も無い。$token_name を置くか、$cli_name を $host に認証してください。" 2
	else
		fail "$cli_name が $host で使えず、curl も無い。どちらかを用意するか、MCP などでリモートを読める道具でスレッドとレビューを JSON にして、'ccnavi review check --result <json>' を人が打つ形にしてください。" 2
	fi
fi

# api <METHOD> <path> [<JSON body>] — レスポンスの JSON を標準出力へ。path は api_base からの相対。
#
# 失敗したら標準出力には何も出さず、ホストが返した本文ごと標準エラーへ出して 1 を返す。
# gh と glab は 4xx でも本文を標準出力へ書くので、そのまま流すと `{"message":"Not Found"}` が
# jq に渡り、`{number: null}` の形で「マージリクエストができた」ことになる（実測）。
api() {
	method="$1"
	rel="$2"
	body="${3:-}"
	case "$transport" in
	gh | glab)
		if [ -n "$body" ]; then
			out=$(printf '%s' "$body" | "$CLI" api --hostname "$host" --method "$method" --input - "$rel" 2>&1) || {
				api_failed "$method" "$rel" "$out"
				return 1
			}
		else
			out=$("$CLI" api --hostname "$host" --method "$method" "$rel" 2>&1) || {
				api_failed "$method" "$rel" "$out"
				return 1
			}
		fi
		;;
	curl)
		if [ "$kind" = github ]; then
			auth="Authorization: Bearer $token"
		else
			auth="PRIVATE-TOKEN: $token"
		fi
		if [ -n "$body" ]; then
			out=$(printf '%s' "$body" | "$CURL" -fsS -X "$method" -H "$auth" -H 'Content-Type: application/json' --data-binary @- "$api_base/$rel" 2>&1) || {
				api_failed "$method" "$rel" "$out"
				return 1
			}
		else
			out=$("$CURL" -fsS -X "$method" -H "$auth" "$api_base/$rel" 2>&1) || {
				api_failed "$method" "$rel" "$out"
				return 1
			}
		fi
		;;
	esac
	printf '%s' "$out"
}

api_failed() {
	printf 'ccnavi-review: %s への %s %s が失敗した:\n%s\n' "$host" "$1" "$2" "$3" >&2
}

# pages <path> — 100 件ずつ最後のページまで読んで 1 つの配列にする。20 ページで打ち切って失敗。
pages() {
	rel="$1"
	page=1
	sep=$(case "$rel" in *\?*) echo '&' ;; *) echo '?' ;; esac)
	all='[]'
	while :; do
		chunk=$(api GET "$rel${sep}per_page=100&page=$page")
		all=$(printf '%s\n%s' "$all" "$chunk" | "$JQ" -s '.[0] + .[1]')
		n=$(printf '%s' "$chunk" | "$JQ" 'length')
		[ "$n" -lt 100 ] && break
		page=$((page + 1))
		[ "$page" -gt 20 ] && fail "$rel が多すぎて読み切れない。"
	done
	printf '%s' "$all"
}

encoded_path() {
	printf '%s' "$path" | "$JQ" -Rr '@uri'
}

# ---- マージリクエスト。無ければ空。

find_mr() {
	if [ "$kind" = github ]; then
		owner="${path%%/*}"
		api GET "repos/$path/pulls?state=open&head=$owner:$branch" |
			"$JQ" '.[0] // empty | {number: .number, url: .html_url}'
	else
		api GET "projects/$(encoded_path)/merge_requests?state=opened&source_branch=$branch" |
			"$JQ" '.[0] // empty | {number: .iid, url: .web_url}'
	fi
}

# ---- マージリクエストを作る。下書きの 1 行目が題、3 行目からが本文。

default_branch() {
	# 統合先。clone が置いた origin/HEAD を見る。無ければ main。
	head=$(git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null || :)
	head="${head#origin/}"
	[ -n "$head" ] && printf '%s' "$head" || printf 'main'
}

create_mr() {
	draft="$1"
	target=$(default_branch)
	if [ "$kind" = github ]; then
		payload=$("$JQ" -n --arg h "$branch" --arg b "$target" --rawfile all "$draft" \
			'($all | split("\n")) as $l |
			 {head: $h, base: $b, title: ($l[0]), body: ($l[2:] | join("\n")), draft: true}')
		api POST "repos/$path/pulls" "$payload" |
			"$JQ" '{number: .number, url: .html_url}'
	else
		payload=$("$JQ" -n --arg s "$branch" --arg b "$target" --rawfile all "$draft" \
			'($all | split("\n")) as $l |
			 {source_branch: $s, target_branch: $b, title: ($l[0]),
			  description: ($l[2:] | join("\n"))}')
		api POST "projects/$(encoded_path)/merge_requests" "$payload" |
			"$JQ" '{number: .iid, url: .web_url}'
	fi
}

# ---- スレッド。GitHub は GraphQL でしか解決状態を読めない。GitLab は discussions。

threads() {
	mr_number="$1"
	mr_url="$2"
	if [ "$kind" = github ]; then
		owner="${path%%/*}"
		repo="${path#*/}"
		cursor=null
		all='[]'
		page=0
		while :; do
			query=$("$JQ" -n --arg o "$owner" --arg r "$repo" --argjson n "$mr_number" --argjson c "$cursor" '{
				query: "query($o:String!,$r:String!,$n:Int!,$c:String){repository(owner:$o,name:$r){pullRequest(number:$n){reviewThreads(first:100,after:$c){pageInfo{hasNextPage endCursor} nodes{id isResolved comments(first:1){nodes{url path line body createdAt}}}}}}}",
				variables: {o: $o, r: $r, n: $n, c: $c}}')
			res=$(api POST graphql "$query")
			chunk=$(printf '%s' "$res" | "$JQ" '[.data.repository.pullRequest.reviewThreads.nodes[] | . as $t | (.comments.nodes[0] // {}) as $c |
				{id: $t.id, resolved: $t.isResolved, url: ($c.url // ""), path: ($c.path // ""), line: ($c.line // 0), body: ($c.body // ""), created_at: ($c.createdAt // "")}]')
			all=$(printf '%s\n%s' "$all" "$chunk" | "$JQ" -s '.[0] + .[1]')
			more=$(printf '%s' "$res" | "$JQ" -r '.data.repository.pullRequest.reviewThreads.pageInfo.hasNextPage')
			[ "$more" = true ] || break
			cursor=$(printf '%s' "$res" | "$JQ" '.data.repository.pullRequest.reviewThreads.pageInfo.endCursor')
			page=$((page + 1))
			[ "$page" -gt 20 ] && fail "reviewThreads が多すぎて読み切れない。"
		done
		printf '%s' "$all"
	else
		pages "projects/$(encoded_path)/merge_requests/$mr_number/discussions" |
			"$JQ" --arg u "$mr_url" '[.[] | select((.notes // []) | length > 0) | select(.notes[0].resolvable) | . as $d | .notes[0] as $n |
				{id: ($d.id | tostring), resolved: ([$d.notes[] | select(.resolvable) | .resolved] | all),
				 url: ($u + "#note_" + ($n.id | tostring)), path: ($n.position.new_path // ""), line: ($n.position.new_line // 0),
				 body: ($n.body // ""), created_at: ($n.created_at // "")}]'
	fi
}

# ---- レビュー。変更要求の状態を CHANGES_REQUESTED に寄せる。

reviews() {
	mr_number="$1"
	mr_url="$2"
	if [ "$kind" = github ]; then
		pages "repos/$path/pulls/$mr_number/reviews" |
			"$JQ" '[.[] | {state: (.state // ""), url: (.html_url // ""), submitted_at: (.submitted_at // ""), author: ((.user.id // .user.login // "") | tostring)}]'
	else
		pages "projects/$(encoded_path)/merge_requests/$mr_number/reviewers" |
			"$JQ" --arg u "$mr_url" '[.[] | {state: (if .state == "requested_changes" then "CHANGES_REQUESTED" else ((.state // "") | ascii_upcase) end),
				url: $u, submitted_at: (.updated_at // .created_at // ""), author: ((.user.id // .user.username // "") | tostring)}]'
	fi
}

# ---- 投稿。{url, created_at} を返す。

comment() {
	mr_number="$1"
	mr_url="$2"
	body_json=$("$JQ" -Rs '{body: .}' <"$3")
	if [ "$kind" = github ]; then
		api POST "repos/$path/issues/$mr_number/comments" "$body_json" |
			"$JQ" '{url: (.html_url // ""), created_at: (.created_at // "")}'
	else
		api POST "projects/$(encoded_path)/merge_requests/$mr_number/notes" "$body_json" |
			"$JQ" --arg u "$mr_url" '{url: ($u + "#note_" + (.id | tostring)), created_at: (.created_at // "")}'
	fi
}

# ---- Draft を外す。GitHub は GraphQL でしか外せない。GitLab は題の "Draft: " を落とす。

undraft() {
	mr_number="$1"
	if [ "$kind" = github ]; then
		pr=$(api GET "repos/$path/pulls/$mr_number")
		node=$(printf '%s' "$pr" | "$JQ" -r '.node_id // empty')
		[ -n "$node" ] || fail "マージリクエスト #$mr_number の node_id を読めない。"
		# 題の "Draft: " は GitLab の流儀で付けたもの。GitHub は旗で持つので、旗を下ろすときに題からも落とす。
		title=$(printf '%s' "$pr" | "$JQ" -r '.title // empty')
		stripped=$(printf '%s' "$title" | sed -E 's/^[[:space:]]*(\[?(Draft|WIP)\]?:?[[:space:]]*)+//I')
		if [ -n "$stripped" ] && [ "$stripped" != "$title" ]; then
			api PATCH "repos/$path/pulls/$mr_number" "$("$JQ" -n --arg t "$stripped" '{title: $t}')" >/dev/null
		fi
		query=$("$JQ" -n --arg id "$node" '{
			query: "mutation($id:ID!){markPullRequestReadyForReview(input:{pullRequestId:$id}){pullRequest{number isDraft}}}",
			variables: {id: $id}}')
		api POST graphql "$query" | "$JQ" -r '.data.markPullRequestReadyForReview.pullRequest.isDraft'
	else
		title=$(api GET "projects/$(encoded_path)/merge_requests/$mr_number" | "$JQ" -r '.title // empty')
		[ -n "$title" ] || fail "マージリクエスト !$mr_number の題を読めない。"
		stripped=$(printf '%s' "$title" | sed -E 's/^[[:space:]]*(\[?(Draft|WIP)\]?:?[[:space:]]*)+//I')
		if [ "$stripped" = "$title" ]; then
			printf 'false\n'
			return 0
		fi
		# squash も立てる。途中のコミットを既定のブランチに残さない。GitHub は MR ごとに持てない
		# （マージのときに人が選ぶ）ので、note に書くだけ。
		payload=$("$JQ" -n --arg t "$stripped" '{title: $t, squash: true}')
		api PUT "projects/$(encoded_path)/merge_requests/$mr_number" "$payload" | "$JQ" -r '.draft // .work_in_progress // false'
	fi
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

# ---- 写し。exe に渡す JSON。

fetch_all() {
	mr=$(find_mr)
	[ -z "$mr" ] && fail "親ブランチ $branch に対応するマージリクエストが $host に無い。"
	number=$(printf '%s' "$mr" | "$JQ" '.number')
	url=$(printf '%s' "$mr" | "$JQ" -r '.url')
	t=$(threads "$number" "$url")
	r=$(reviews "$number" "$url")
	"$JQ" -n --arg host "$kind" --argjson mr "$mr" --argjson threads "$t" --argjson reviews "$r" \
		'{host: $host, mr: $mr, threads: $threads, reviews: $reviews, fetched_at: (now | todate)}'
}

mkdir -p "$state"
result="$state/review-result-$$.json"
trap 'rm -f "$result"' EXIT

case "$sub" in
origin)
	# origin をどう読んだか。当たらないときに、どこで読み違えたかを見る出口。
	# URL に埋まった資格情報は伏せる。ここの出力はエージェントの文脈と記録に残る。
	shown=$(printf '%s' "$origin" | sed -E 's#^([a-z]+://)[^/@]+@#\1<伏せた>@#')
	printf 'origin=%s\nkind=%s\nscheme=%s\nhost=%s\npath=%s\napi_base=%s\nbranch=%s\ntransport=%s\n' \
		"$origin_shown" "$kind" "$scheme" "$host" "$path" "$api_base" "$branch" "$transport"
	;;
fetch)
	fetch_all
	printf '\n'
	;;
check)
	fetch_all >"$result"
	ccnavi review check "$@" --result "$result"
	;;
request)
	# 段 1: 前提。exe が依頼の本文と、マージリクエストの下書きを書き出す。
	prepared=$(ccnavi review prepare "$@") || exit $?
	file=$(printf '%s\n' "$prepared" | sed -n 1p)
	draft=$(printf '%s\n' "$prepared" | sed -n 2p)
	# 段 1.5: 入れ物が無ければ作る。人はレビューをここで行うので、
	# 「見る場所が無い」で止めない。統合するのは人なので下書きで作る。
	mr=$(find_mr)
	if [ -z "$mr" ]; then
		[ -n "$draft" ] && [ -f "$draft" ] || fail "マージリクエストの下書きを読めない ($draft)。"
		mr=$(create_mr "$draft") || fail "マージリクエストを作れなかった。ホストの返事は上に出ている。"
		[ -z "$mr" ] && fail "マージリクエストを作れなかった。"
		printf 'マージリクエストを作った: %s\n' "$(printf '%s' "$mr" | "$JQ" -r '.url')"
	fi
	number=$(printf '%s' "$mr" | "$JQ" '.number')
	url=$(printf '%s' "$mr" | "$JQ" -r '.url')
	# 段 2: 投稿。
	posted=$(comment "$number" "$url" "$file")
	"$JQ" -n --arg host "$kind" --argjson mr "$mr" --argjson posted "$posted" \
		'{host: $host, mr: $mr} + $posted' >"$result"
	# 段 3: 印。
	ccnavi review requested "$@" --result "$result"
	;;
note)
	body=""
	while [ "$#" -gt 0 ]; do
		case "$1" in
		--body-file)
			body="${2:-}"
			shift 2
			;;
		*) shift ;;
		esac
	done
	[ -n "$body" ] && [ -f "$body" ] || fail "note には --body-file <本文> が要る。" 2
	mr=$(find_mr)
	[ -z "$mr" ] && fail "親ブランチ $branch に対応するマージリクエストが $host に無い。"
	number=$(printf '%s' "$mr" | "$JQ" '.number')
	url=$(printf '%s' "$mr" | "$JQ" -r '.url')
	noted="$state/review-note-$$.md"
	{
		printf '<!-- ccnavi:note -->\n'
		cat "$body"
	} >"$noted"
	posted=$(comment "$number" "$url" "$noted")
	rm -f "$noted"
	printf 'OK: 記録した（%s）\n' "$(printf '%s' "$posted" | "$JQ" -r '.url')"
	;;
accept)
	n="${1:-}"
	[ -n "$n" ] || fail "accept には <N>（フェーズ番号）が要る。" 2
	fetch_all >"$result"
	ccnavi --reviewed "$n" --accept-unresolved --result "$result"
	# 受け入れた一覧が書き出されていれば、コメントに写す。
	for f in "$state"/review-accept-*-"$n".md; do
		[ -f "$f" ] || continue
		number=$(printf '%s' "$(cat "$result")" | "$JQ" '.mr.number')
		url=$(printf '%s' "$(cat "$result")" | "$JQ" -r '.mr.url')
		comment "$number" "$url" "$f" >/dev/null && rm -f "$f"
	done
	;;
handoff)
	# 残った指摘を別の issue に切り出す。exe が段階を確かめて下書きを書き、ここが作る。
	body=""
	while [ "$#" -gt 0 ]; do
		case "$1" in
		--body-file)
			body="${2:-}"
			shift 2
			;;
		*) shift ;;
		esac
	done
	[ -n "$body" ] && [ -f "$body" ] || fail "handoff には --body-file <題と本文>（1 行目が題）が要る。" 2
	fetch_all >"$result"
	draft=$(ccnavi review handoff --body-file "$body" --result "$result") || exit $?
	issue=$(create_issue "$draft")
	[ -z "$issue" ] && fail "issue を作れなかった。"
	issue_url=$(printf '%s' "$issue" | "$JQ" -r '.url')
	issue_no=$(printf '%s' "$issue" | "$JQ" -r '.number')
	number=$(printf '%s' "$(cat "$result")" | "$JQ" '.mr.number')
	url=$(printf '%s' "$(cat "$result")" | "$JQ" -r '.mr.url')
	noted="$state/review-handoff-note-$$.md"
	{
		printf '<!-- ccnavi:handoff -->\n'
		printf '残った指摘を #%s へ引き継ぐ: %s\n' "$issue_no" "$issue_url"
	} >"$noted"
	comment "$number" "$url" "$noted" >/dev/null
	rm -f "$noted"
	printf 'OK: #%s に引き継いだ（%s）。残りは利用者が accept で受け入れて閉じる\n' "$issue_no" "$issue_url"
	;;
ready)
	# 親を閉じられる状態なら Draft を外す。exe が条件を確かめて印と note の下書きを置き、
	# ここが外して note を投稿する。マージは人。
	fetch_all >"$result"
	noted=$(ccnavi review ready --result "$result") || exit $?
	number=$(printf '%s' "$(cat "$result")" | "$JQ" '.mr.number')
	url=$(printf '%s' "$(cat "$result")" | "$JQ" -r '.mr.url')
	still=$(undraft "$number")
	[ "$still" = "false" ] || fail "Draft を外せなかった（$url）。ホストの返事は上に出ている。"
	if [ -n "$noted" ] && [ -f "$noted" ]; then
		comment "$number" "$url" "$noted" >/dev/null && rm -f "$noted"
	fi
	printf 'OK: Draft を外した（%s）。マージは利用者が行う\n' "$url"
	;;
wrapup)
	# 人が端末で打つ。exe が残りを見せて y/N を取り、印を置いて下書きを書く。
	# ここが残りを issue に写し、note を投稿する。Draft を外すのは、親が片付けて
	# push したあとの ready（外す道は 1 本）。
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
	[ -n "$reason" ] || fail "wrapup には --reason <理由> が要る。" 2
	fetch_all >"$result"
	# exe は人に残りを見せて y/N を取るので、標準出力は端末のまま。下書きは控えの
	# 置き場の決まった名前で拾う（親の識別子 = ブランチ名）。
	ccnavi review wrapup --reason "$reason" --result "$result" || exit $?
	issue_draft="$state/review-wrapup-issue-$branch.md"
	noted="$state/review-wrapup-note-$branch.md"
	number=$(printf '%s' "$(cat "$result")" | "$JQ" '.mr.number')
	url=$(printf '%s' "$(cat "$result")" | "$JQ" -r '.mr.url')
	if [ "$make_issue" -eq 1 ] && [ -f "$issue_draft" ]; then
		issue=$(create_issue "$issue_draft")
		[ -z "$issue" ] && fail "残りを写す issue を作れなかった。下書きは $issue_draft にある。"
		issue_url=$(printf '%s' "$issue" | "$JQ" -r '.url')
		issue_no=$(printf '%s' "$issue" | "$JQ" -r '.number')
		printf '残りを #%s に写した（%s）\n' "$issue_no" "$issue_url"
		printf '残りは #%s へ: %s\n' "$issue_no" "$issue_url" >>"$noted"
		rm -f "$issue_draft"
	fi
	if [ -f "$noted" ]; then
		comment "$number" "$url" "$noted" >/dev/null && rm -f "$noted"
	fi
	printf 'OK: 締めた（%s）。あとは親に、閉じて片付けて push し、ready を打たせる。マージは利用者が行う\n' "$url"
	;;
esac
