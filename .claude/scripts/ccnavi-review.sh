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
#   request: `ccnavi review prepare` が前提を確かめて本文を書き出す → ここが投稿する
#            → `ccnavi review requested` が印を置く
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

  request  --phase <N> --body-file <依頼文>   前提を確かめて依頼を投稿し、依頼の印を置く
  check    --phase <N>                         依頼より後の未解決スレッドが無ければ印を置く
  note     --body-file <本文>                  判断の記録を MR のコメントに写す
  accept   <N>                                 未解決を残したまま進める判断（人が端末で打つ）
  fetch                                        リモートから取ってきた写し（JSON）を標準出力へ

gh / glab があればそれを使う。無ければ curl と GITLAB_TOKEN / GITHUB_TOKEN。jq が要る。
USAGE
}

fail() {
	printf 'ccnavi-review: %s\n' "$1" >&2
	exit "${2:-1}"
}

[ "$#" -ge 1 ] || {
	usage
	exit 2
}
sub="$1"
shift
case "$sub" in
request | check | note | accept | fetch) ;;
-h | --help | help)
	usage
	exit 0
	;;
*)
	fail "$sub は通しません。使えるのは request / check / note / accept / fetch です。" 2
	;;
esac

# ---- 場所。main の根と、いまの作業ツリー。Windows の Git Bash では pwd -W で綴りを直す。

common=$(git rev-parse --git-common-dir 2>/dev/null || :)
[ -z "$common" ] && fail "git リポジトリの中で実行してください。" 2
case "$common" in
*/.git) root="${common%/.git}" ;;
.git) root="$(pwd -W 2>/dev/null || pwd)" ;;
*) root="$common" ;;
esac
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
host=$(printf '%s' "$origin" | sed -E 's#^(https?://|git@|ssh://git@)([^/:]+).*#\2#')
path=$(printf '%s' "$origin" | sed -E 's#^(https?://|git@|ssh://git@)[^/:]+[/:]+##; s#\.git$##; s#/$##')
[ "$host" = "$origin" ] && fail "origin の綴りを読めない ($origin)。"
if [ "$host" = "github.com" ]; then
	kind=github
	token_name=GITHUB_TOKEN
	api_base="https://api.github.com"
else
	kind=gitlab
	token_name=GITLAB_TOKEN
	api_base="https://$host/api/v4"
fi
branch=$(git rev-parse --abbrev-ref HEAD)

# ---- 道具。絶対パスに解いて固定する。

JQ=$(command -v jq 2>/dev/null || :)
[ -z "$JQ" ] && fail "jq が無い。結果の JSON を組み立てられない。" 2
transport=""
if [ "$kind" = github ]; then
	CLI=$(command -v gh 2>/dev/null || :)
	[ -n "$CLI" ] && transport=gh
else
	CLI=$(command -v glab 2>/dev/null || :)
	[ -n "$CLI" ] && transport=glab
fi
if [ -z "$transport" ]; then
	CURL=$(command -v curl 2>/dev/null || :)
	eval "token=\${$token_name:-}"
	if [ -n "$CURL" ] && [ -n "$token" ]; then
		transport=curl
	elif [ -n "$CURL" ]; then
		fail "$([ "$kind" = github ] && echo gh || echo glab) が無く、curl に付ける $token_name も無い。$token_name を置くか、gh / glab を入れてください。" 2
	else
		fail "$([ "$kind" = github ] && echo gh || echo glab) も curl も無い。どちらかを入れるか、MCP などでリモートを読める道具を用意して、その結果を JSON にしてから 'ccnavi review check --result <json>' を人が打つ形にしてください。" 2
	fi
fi

# api <METHOD> <path> [<JSON body>] — レスポンスの JSON を標準出力へ。path は api_base からの相対。
api() {
	method="$1"
	rel="$2"
	body="${3:-}"
	case "$transport" in
	gh | glab)
		if [ -n "$body" ]; then
			printf '%s' "$body" | "$CLI" api --method "$method" --input - "$rel"
		else
			"$CLI" api --method "$method" "$rel"
		fi
		;;
	curl)
		if [ "$kind" = github ]; then
			auth="Authorization: Bearer $token"
		else
			auth="PRIVATE-TOKEN: $token"
		fi
		if [ -n "$body" ]; then
			printf '%s' "$body" | "$CURL" -fsS -X "$method" -H "$auth" -H 'Content-Type: application/json' --data-binary @- "$api_base/$rel"
		else
			"$CURL" -fsS -X "$method" -H "$auth" "$api_base/$rel"
		fi
		;;
	esac
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
fetch)
	fetch_all
	printf '\n'
	;;
check)
	fetch_all >"$result"
	ccnavi review check "$@" --result "$result"
	;;
request)
	# 段 1: 前提。exe が本文を書き出す。
	file=$(ccnavi review prepare "$@") || exit $?
	mr=$(find_mr)
	[ -z "$mr" ] && fail "親ブランチ $branch に対応するマージリクエストが $host に無い。先に MR を作ること。"
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
esac
