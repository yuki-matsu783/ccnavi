#!/bin/sh
# ccnavi-review — レビューの依頼と確認。親（メインエージェント）だけが呼ぶ。
#
#   sh .ccnavi/scripts/ccnavi-review.sh request --phase <N> --body-file <依頼文>
#   sh .ccnavi/scripts/ccnavi-review.sh confirm --phase <N>
#   sh .ccnavi/scripts/ccnavi-review.sh comment --body-file <本文>
#   sh .ccnavi/scripts/ccnavi-review.sh decide  <N>          （人が端末で打つ）
#   sh .ccnavi/scripts/ccnavi-review.sh fetch                 （取ってきた写しを見る）
#   sh .ccnavi/scripts/ccnavi-review.sh merged                （MR がマージ済みか。ccnavi-sync.sh が使う）
#   sh .ccnavi/scripts/ccnavi-review.sh chat <N>              （人が端末で打つ。chat のフェーズのレビュー済み）
#   sh .ccnavi/scripts/ccnavi-review.sh config-synced <親>    （人が端末で打つ。着手で上書きした設定を見た）
#
# リモート（GitHub / GitLab）を読み書きするのはこのスクリプトで、ccnavi の実行ファイルは
# ネットワークに出ない。実行ファイルが見るのは作業ツリーの中（フェーズ・ブランチ・マーカー）
# だけで、マージリクエストの中身はここが取ってきて JSON で渡す（--result）。
#
#   request: `ccnavi review prepare` が前提を確かめ、依頼の本文とマージリクエストの
#            下書きを書き出す → ここが（無ければ）マージリクエストを下書きで作り、依頼を投稿する
#            → `ccnavi review requested` がマーカーを置く
#            人はレビューをマージリクエストで行うので、入れ物が無いことで止めない。題から Draft を
#            外してマージするのは人の手に残す。
#   confirm: ここがスレッドとレビューを取ってくる → `ccnavi review confirm` が判定してマーカーを置き、
#            レビュー待ち（wip/proposals/review/）の子を .ccnavi/approved/done/ へ動かす。
#            トークンの持ち主（GitHub は login、GitLab は username）を引けたら --actor で渡し、印に残す
#            （ADR-0093 の 8.9。段階 4）。引けなければ渡さず、印は前と同じ
#   decide:  ここが取ってくる → `ccnavi --reviewed N --accept-unresolved` が人に見せ、指摘ごとに
#            対応しない・このフェーズで直す（続きの子チケット）・issue に回すを選ばせる
#            → issue に回す分があればここが issue を作り、決めた内容をコメントに写す
#            ボードは同じ道を --preview（一覧を読む）と --choices --digest（押した選択を置く）で通る
#
# リモートへの道具は、gh / glab があればそれ（認証はツールに任せる）、無ければ curl と
# GITHUB_TOKEN / GITLAB_TOKEN。どちらも無ければ止まる。結果の組み立てには jq が要る。
# 道具は起動時に絶対パスへ解いて固定する。PATH の細工で差し替えられないように。
#
# 取り込み済みの家族（origin があり家族の控えが present。chat だけの家族を除く）では、状態を書く
# request（マーカー）・confirm・decide（--preview を除く）・ready を C1 で回す（ADR-0093 の 4.3。段階 2d）。
# ロック → 途中の操作の確認 → hook の印と跡を先にコミット → 取り込み → 未送信の確かめを済ませてから
# ホストに触り、実行ファイルが書いたパスだけを commit --only して push する。送れなければ戻す。
# 人の判断（chat・config-synced・close-early）は実行ファイルが書いた後、取り込み済みの家族なら
# 運ぶ処理（ccnavi-push-approved.sh <親>）を呼んで送る（D27）。それ以外の家族は今のまま。
#
# 親のワークツリーの中で実行すること。どの親かは cwd から引く。
# 終了コード: 0 成功 / 1 前提の未充足 / 2 引数か環境の誤り

set -eu

usage() {
	cat <<'USAGE'
sh .ccnavi/scripts/ccnavi-review.sh <request|confirm|comment|decide|ready|close-early|chat|config-synced|fetch|origin|merged> [--phase <N>] [--body-file <path>]

  request      --phase <N> --body-file <依頼文>   前提を確かめ、無ければマージリクエストを作り、依頼を投稿してマーカーを置く
  confirm      --phase <N>                        依頼より後の未解決スレッドが無ければマーカーを置く
  comment      --body-file <本文>                 判断の記録をマージリクエストのコメントに写す
  decide       <N> [--preview]                    未解決（Unresolved）の指摘の対応方針を指摘ごとに選ぶ。対応しない・このフェーズで直す・issue に回す（人が端末で打つ。--preview は一覧を JSON で見るだけ）
  ready                                           閉じられて wip を片付け push 済みなら Draft を外す（「マージに進んでよい」の合図。マージは人が squash で）
  close-early  --reason <理由> [--no-issue]       まだ残っているが締める判断（人が端末で打つ）。残りを issue に写す。Draft は親が ready で外す
  chat         <N>                                chat で見るフェーズを人がこのセッションで見終えた（人が端末で打つ。ccnavi --reviewed <N> --chat）
  config-synced <親>                              着手で上書きした設定を人が端末で見た（人が端末で打つ。ccnavi --config-synced <親>）
  fetch                                           リモートから取ってきた写し（JSON）を標準出力へ
  origin                                          origin をどう読んだか（ホスト・scheme・API の綴り）
  merged                                          いまのブランチの MR がマージ済みなら "merged <番号>"、無ければ "none"、確かめられなければ "unknown"（終了コード 3。ccnavi-sync.sh が観測ずれを確かめる）

gh / glab があればそれを使う。無ければ curl と GITLAB_TOKEN / GITHUB_TOKEN。jq が要る。
USAGE
}

# fail <識別子> <文面> [終了コード]。文面は標準エラーへ出す契約。識別子は止めた理由の種類を
# 表す短い語で、診断ログにだけ残す。文面には origin やパスが入るので、ログには写さない。
fail() {
	printf 'ccnavi-review: %s\n' "$2" >&2
	# 診断ログ（docs/claude/logging.md）。上の文面が契約で、こちらは別に残すだけ。
	log_info 止めた -- "sub=${sub:-}" "exit=${3:-1}" "reason=$1"
	exit "${3:-1}"
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
request | confirm | comment | decide | ready | close-early | chat | config-synced | fetch | origin | merged) ;;
-h | --help | help)
	usage
	exit 0
	;;
*)
	fail unknown-sub "$sub は通しません。使えるのは request / confirm / comment / decide / ready / close-early / chat / config-synced / fetch / origin / merged です。" 2
	;;
esac

# ---- 場所。ワークスペースルートと、いまのワークツリー。Windows の Git Bash では pwd -W で綴りを直す。
#
# 根は git に聞かない。モード B では cwd がプロジェクトの中にあると git は
# プロジェクトを答え、写し・マーカー・状態の置き場がプロジェクト側にずれる。
# 道具の置き場は上へ歩いて探す（設計 11.8）。
root=$(ccnavi_workspace) ||
	fail no-workspace "ワークスペースルートが見つかりません（.ccnavi/scripts/ccnavi-common.sh を持つ親を cwd から上へ探しました）。ワークスペースの中で実行するか、CCNAVI_WORKSPACE にワークスペースルートの絶対パスを渡してください。" 2
# 解いたルートを logger に渡し、書くたびに探し直させない。
ccnavi_log_root="$root"
here="$(pwd -W 2>/dev/null || pwd)"
state="$root/${CCNAVI_STATE:-logs/state}"

# ---- 実行ファイル。見つからなければソース（ccnavi のリポジトリ）で動かす。

# 実行ファイルと互換の版が食い違っていれば、最初に呼ぶときに 1 度だけ言う（止めない）。
# 引数の誤りで断る道と、実行ファイルを使わない副命令（origin / fetch / comment）では起こさない。
# `$( )` の中から呼ぶと印が親に残らないので、そう呼ぶ前には親で先に tell_skew を打つ。
told_skew=""
tell_skew() {
	[ -z "$told_skew" ] || return 0
	told_skew=1
	# ソースで動かすとき（bin が無い）は、sh と同じツリーのものを動かすので比べない。
	[ -n "${bin:-}" ] || return 0
	skew=$(ccnavi_compat_skew "$root" "$bin") || printf 'ccnavi-review: %s\n' "$skew" >&2
}

if bin=$(ccnavi_bin "$root"); then
	ccnavi() {
		tell_skew
		"$bin" --root "$root" --cwd "$here" "$@"
	}
elif [ -f "$root/ccnavi/__main__.py" ]; then
	ccnavi() { (cd "$root" && uv run python -m ccnavi --root "$root" --cwd "$here" "$@"); }
elif [ "$sub" != merged ]; then
	# merged は実行ファイルを起こさない（ホストに聞くだけ）ので、無くても進める。
	fail no-bin "ccnavi の実行ファイルが無い（CCNAVI_BIN_PATH・dist/ccnavi/ccnavi・.ccnavi/bin/ のどれにも無い）。build.py で組み立てるか、scripts/ccnavi-setup.sh で配ってください。" 2
fi

# ---- リモート。origin の URL でホストを見分ける。
# 人の判断の入口（chat・config-synced）はホストに触らないので、origin も道具も要らない。
needs_host=yes
case "$sub" in
chat | config-synced) needs_host=no ;;
esac
if [ "$needs_host" = yes ]; then
	origin=$(git remote get-url origin 2>/dev/null || :)
	[ -z "$origin" ] && fail no-origin "origin が無い。レビューはマージリクエストの実物に結ぶので、リモートが要る。"
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
	[ "$rest" = "$origin" ] && fail origin-unreadable "origin の綴りを読めない ($origin_shown)。"
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
	[ -n "$host" ] || fail origin-no-host "origin からホストを読めない ($origin_shown)。"
	tail="${rest#"$host"}"
	while :; do
		case "$tail" in
		[/:]*) tail="${tail#?}" ;;
		*) break ;;
		esac
	done
	path="${tail%.git}"
	path="${path%/}"
	[ -n "$path" ] || fail origin-no-path "origin からプロジェクトのパスを読めない ($origin_shown)。"
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
	[ -z "$JQ" ] && fail no-jq "jq が無い。結果の JSON を組み立てられない。" 2
	# gh / glab は「入っている」だけでは足りない。そのホストで認証されていなければ
	# 通らない（手元に立てた GitLab に glab を繋いでいない、が普通にある）。
	# 1 度だけ疎通を試して、通らなければ curl とトークンに切り替える。
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
			fail no-transport-token "$cli_name が $host で使えず（未導入か未認証）、curl に付ける $token_name も無い。$token_name を置くか、$cli_name を $host に認証してください。" 2
		else
			fail no-transport "$cli_name が $host で使えず、curl も無い。どちらかを用意するか、MCP などでリモートを読める道具でスレッドとレビューを JSON にして、'ccnavi review confirm --result <json>' を人が打つ形にしてください。" 2
		fi
	fi
fi
branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || :)

# curl の 1 回の時間の上限（秒）。応答しないホストで止まり続けないように。gh / glab は道具に任せる
api_max_time=120

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
		# 標準エラー（更新の知らせなど）は応答に混ぜない。落ちたときだけ本文と一緒に見せる
		api_err="$state/review-api-err-$$"
		if [ -n "$body" ]; then
			out=$(printf '%s' "$body" | "$CLI" api --hostname "$host" --method "$method" --input - "$rel" 2>"$api_err") || {
				api_failed "$method" "$rel" "$out$(cat "$api_err" 2>/dev/null)"
				rm -f "$api_err"
				return 1
			}
		else
			out=$("$CLI" api --hostname "$host" --method "$method" "$rel" 2>"$api_err") || {
				api_failed "$method" "$rel" "$out$(cat "$api_err" 2>/dev/null)"
				rm -f "$api_err"
				return 1
			}
		fi
		rm -f "$api_err"
		;;
	curl)
		if [ "$kind" = github ]; then
			auth="Authorization: Bearer $token"
		else
			auth="PRIVATE-TOKEN: $token"
		fi
		if [ -n "$body" ]; then
			out=$(printf '%s' "$body" | "$CURL" -fsS --connect-timeout 15 --max-time "$api_max_time" -X "$method" -H "$auth" -H 'Content-Type: application/json' --data-binary @- "$api_base/$rel" 2>&1) || {
				api_failed "$method" "$rel" "$out"
				return 1
			}
		else
			out=$("$CURL" -fsS --connect-timeout 15 --max-time "$api_max_time" -X "$method" -H "$auth" "$api_base/$rel" 2>&1) || {
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
	# GraphQL を塞いでいる実行環境がある（Claude Code のセッションは REST だけ通す）。
	# GitHub ではスレッドの解決状態（threads）と Draft 外し（undraft）が GraphQL でしか
	# 扱えないので、そこだけが 403 で止まる。curl の経路は -f が本文を捨てるため、
	# 画面に残るのは番号だけになり、認証の失敗と見分けが付かない。詰まったその場で
	# 逃げ道を名指しする。綴りは transport を選ぶところの案内と揃える。
	case "$2" in
	graphql)
		printf 'ccnavi-review: %s\n' \
			"GraphQL が塞がれている環境では confirm / fetch（スレッドの解決状態）と ready（Draft 外し）が通りません。MCP などリモートを読める道具でスレッドとレビューを JSON にして 'ccnavi review confirm --phase <N> --result <json>' を打ってください。写しの形は fetch_all と同じ {host, mr, threads, reviews, fetched_at} です。Draft 外しはその道具の側で直接行ってください。" >&2
		;;
	esac
}

# pages <path> — 100 件ずつ最後のページまで読んで 1 つの配列にする。20 ページで打ち切って失敗。
pages() {
	rel="$1"
	page=1
	case "$rel" in
	*\?*) sep='&' ;;
	*) sep='?' ;;
	esac
	all='[]'
	while :; do
		chunk=$(api GET "$rel${sep}per_page=100&page=$page")
		all=$(printf '%s\n%s' "$all" "$chunk" | "$JQ" -s '.[0] + .[1]')
		n=$(printf '%s' "$chunk" | "$JQ" 'length')
		[ "$n" -lt 100 ] && break
		page=$((page + 1))
		[ "$page" -gt 20 ] && fail too-many-pages "$rel が多すぎて読み切れない。"
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
			[ "$page" -gt 20 ] && fail too-many-threads "reviewThreads が多すぎて読み切れない。"
		done
		printf '%s' "$all"
	else
		pages "projects/$(encoded_path)/merge_requests/$mr_number/discussions" |
			"$JQ" --arg u "$mr_url" '[.[] | select((.notes // []) | length > 0) | select(.notes[0].resolvable) | . as $d | .notes[0] as $n |
				{id: ($d.id | tostring), resolved: ([$d.notes[] | select(.resolvable) | .resolved] | all),
				 url: ($u + "#note_" + ($n.id | tostring)), path: ($n.position.new_path // ""), line: ($n.position.new_line // 0),
				 body: ($n.body // ""), created_at: ($n.created_at // ""), author: ($n.author.username // "")}]'
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
			"$JQ" '{url: (.html_url // ""), created_at: (.created_at // ""), author: (.user.login // "")}'
	else
		api POST "projects/$(encoded_path)/merge_requests/$mr_number/notes" "$body_json" |
			"$JQ" --arg u "$mr_url" '{url: ($u + "#note_" + (.id | tostring)), created_at: (.created_at // ""), author: (.author.username // "")}'
	fi
}

# ---- 目印で始まる投稿を探す。{url, created_at} を返す（無ければ空）。<番号> <URL> <目印>

find_posted() {
	case "$3" in
	'<!-- ccnavi:'*) ;;
	*) return 0 ;;
	esac
	if [ "$kind" = github ]; then
		found_all=$(pages "repos/$path/issues/$1/comments") || return 1
		printf '%s' "$found_all" | "$JQ" -c --arg m "$3" \
			'[.[] | select((.body // "") | startswith($m))][0] // empty | {url: (.html_url // ""), created_at: (.created_at // ""), author: (.user.login // "")}'
	else
		found_all=$(pages "projects/$(encoded_path)/merge_requests/$1/notes") || return 1
		printf '%s' "$found_all" | "$JQ" -c --arg m "$3" --arg u "$2" \
			'[.[] | select((.body // "") | startswith($m))][0] // empty | {url: ($u + "#note_" + (.id | tostring)), created_at: (.created_at // ""), author: (.author.username // "")}'
	fi
}

# ---- Draft を外す。GitHub は GraphQL でしか外せない。GitLab は題の "Draft: " を落とす。

undraft() {
	mr_number="$1"
	if [ "$kind" = github ]; then
		pr=$(api GET "repos/$path/pulls/$mr_number")
		node=$(printf '%s' "$pr" | "$JQ" -r '.node_id // empty')
		[ -n "$node" ] || fail no-node-id "マージリクエスト #$mr_number の node_id を読めない。"
		# 題の "Draft: " は GitLab の流儀で付けたもの。GitHub は Draft をフラグで持つので、フラグを外すときに題からも落とす。
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
		[ -n "$title" ] || fail no-mr-title "マージリクエスト !$mr_number の題を読めない。"
		stripped=$(printf '%s' "$title" | sed -E 's/^[[:space:]]*(\[?(Draft|WIP)\]?:?[[:space:]]*)+//I')
		if [ "$stripped" = "$title" ]; then
			printf 'false\n'
			return 0
		fi
		# squash も立てる。途中のコミットを既定のブランチに残さない。GitHub は MR ごとに持てない
		# （マージのときに人が選ぶ）ので、コメントに書くだけ。
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

# ---- 決めた行き先を投稿する。exe が控えの置き場に書いた下書きから、issue を作ってコメントを残す。
#
# 置いたあとの投稿なので、ここで失敗しても決めたことは戻さない。失敗は post_warning に言い、
# 下書きは残す（打ち直せば投稿できる）。下書きの名前は exe と揃える（親の識別子 = ブランチ名）。

post_decision() {
	issue_url=""
	post_warning=""
	number=$("$JQ" '.mr.number' "$result")
	url=$("$JQ" -r '.mr.url' "$result")
	issue_draft="$state/review-issue-$branch-$1.md"
	noted="$state/review-decide-$branch-$1.md"
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

# ---- トークンの持ち主（レビュー済みの印の actor。ADR-0093 の 8.9）。引けなければ空で、止めない。
#
# gh / glab はその道具が認証したアカウント、curl はトークンの持ち主。GitHub は GET /user の login、
# GitLab は username。GitHub Actions の GITHUB_TOKEN のように持ち主の無いトークンは 403 で空になる。
# 形（英数字と _ . - の 1〜100 字で、- で始まらない。実行ファイルの --actor と同じ）に合わなければ使わない。
# 印に残すだけなので、長く待たない（account_max_time 秒）。gh / glab の標準エラーは応答に混ぜない。

account_max_time="${CCNAVI_REVIEW_ACCOUNT_TIMEOUT:-15}"

# timed <秒> <書き先> <コマンド>... — 標準出力を書き先へ、標準エラーは捨てる。時間を過ぎたら止めて 1。
timed() {
	timed_limit="$1"
	timed_out="$2"
	shift 2
	"$@" </dev/null >"$timed_out" 2>/dev/null &
	timed_pid=$!
	(
		exec </dev/null >/dev/null 2>&1
		sleep "$timed_limit"
		kill "$timed_pid"
	) &
	timed_dog=$!
	timed_rc=0
	wait "$timed_pid" 2>/dev/null || timed_rc=$?
	kill "$timed_dog" 2>/dev/null || :
	return "$timed_rc"
}

account() {
	if [ "$kind" = github ]; then field=login; else field=username; fi
	account_out="$state/review-account-$$.json"
	case "$transport" in
	gh | glab) timed "$account_max_time" "$account_out" "$CLI" api --hostname "$host" user || : >"$account_out" ;;
	curl)
		api_max_time="$account_max_time"
		api GET user >"$account_out" 2>/dev/null || : >"$account_out"
		api_max_time=120
		;;
	esac
	who=$("$JQ" -r --arg f "$field" '.[$f] // empty' <"$account_out" 2>/dev/null | tr -d '\r') || who=""
	rm -f "$account_out"
	[ -n "$who" ] || return 0
	# 形は C ロケールで見る（ロケールによって [A-Za-z] が英字の外に当たらないように）
	if printf '%s\n' "$who" | LC_ALL=C grep -q '[^A-Za-z0-9_.-]'; then return 0; fi
	case "$who" in
	-*) return 0 ;;
	esac
	[ "${#who}" -le 100 ] || return 0
	printf '%s' "$who"
}

# ---- 写し。exe に渡す JSON。

fetch_all() {
	mr=$(find_mr)
	[ -z "$mr" ] && fail no-mr "親ブランチ $branch に対応するマージリクエストが $host に無い。"
	number=$(printf '%s' "$mr" | "$JQ" '.number')
	url=$(printf '%s' "$mr" | "$JQ" -r '.url')
	t=$(threads "$number" "$url")
	r=$(reviews "$number" "$url")
	"$JQ" -n --arg host "$kind" --argjson mr "$mr" --argjson threads "$t" --argjson reviews "$r" \
		'{host: $host, mr: $mr, threads: $threads, reviews: $reviews, fetched_at: (now | todate)}'
}

log_info 受け付けた -- "sub=$sub" "args=$#"
log_debug 判定の材料 -- "sub=$sub" "kind=${kind:-}" "host=${host:-}" "branch=$branch" "transport=${transport:-}" "bin=${bin:-}"

mkdir -p "$state"
result="$state/review-result-$$.json"
# 抜けるときに写しを消し、終わりの 1 行を診断ログに残す。終了コードは変えない。
# rm が失敗しても（写しの名前がディレクトリ・権限など）、`set -e` がその失敗の値で抜けて
# 元の終了コードを上書きしないよう、失敗を飲んでから元の値で抜け直す。
trap 'review_exit=$?; ccnavi_c1_end; rm -f "$result" 2>/dev/null || :; log_info 終わった -- "sub=$sub" "exit=$review_exit"; exit "$review_exit"' EXIT
trap 'ccnavi_c1_end; exit 130' INT TERM HUP

# ---- C1（ADR-0093 の 4.3。段階 2d）と人の判断の運び（4.6・D27）
ccnavi_c1_root="$root"
ccnavi_c1_label=ccnavi-review
ccnavi_c1_sh="$(dirname "$0")"
ccnavi_c1_exe() { ccnavi "$@"; }
c1_on=no

# C1 を始める（1〜5）。<識別子>。対象でなければ何もしない（今の手元の動き）。止める理由があれば抜ける。
c1_start() {
	ccnavi_c1_family "$1"
	case "$ccnavi_c1_target" in
	stop)
		ccnavi_c1_refuse
		exit 1
		;;
	yes)
		ccnavi_c1_begin || exit 1
		c1_on=yes
		;;
	esac
	return 0
}

# 状態を書く実行ファイルの 1 回。C1 の中なら書いたパスだけをコミットして送る（6〜11）。
# <文> -- <実行ファイルの引数>...。ccnavi_c1_capture に書き先があれば標準出力をそこへ書く。
c1_ccnavi() {
	c1_words="$1"
	shift
	[ "${1:-}" = "--" ] && shift
	if [ "$c1_on" = yes ]; then
		ccnavi_c1_write "ccnavi: ${c1_words}" -- "$@"
	elif [ -n "$ccnavi_c1_capture" ]; then
		ccnavi "$@" >"$ccnavi_c1_capture"
	else
		ccnavi "$@"
	fi
}

# 人の判断を運ぶ（D27）。取り込み済みの家族だけ、運ぶ処理を <親> で呼ぶ。それ以外は今のまま運ばない。
carry_human() {
	ccnavi_c1_family "$1"
	case "$ccnavi_c1_target" in
	yes)
		sh "$ccnavi_c1_sh/ccnavi-push-approved.sh" "$ccnavi_c1_family_id" || {
			printf 'ccnavi-review: 人の判断は置いたが送れなかった。接続を戻して sh %s/ccnavi-push-approved.sh %s を人が打ち直す（送るまで、この家族の状態の操作は止まる）\n' \
				"$ccnavi_c1_sh" "$ccnavi_c1_family_id" >&2
			return 1
		}
		;;
	stop)
		ccnavi_c1_refuse
		return 1
		;;
	esac
	return 0
}

case "$sub" in
origin)
	# origin をどう読んだか。当たらないときに、どこで読み違えたかを見る出口。
	# URL に埋まった資格情報は伏せる（`ccnavi_mask_url` が作る `origin_shown`）。
	# ここの出力はエージェントの文脈と記録に残る。
	printf 'origin=%s\nkind=%s\nscheme=%s\nhost=%s\npath=%s\napi_base=%s\nbranch=%s\ntransport=%s\n' \
		"$origin_shown" "$kind" "$scheme" "$host" "$path" "$api_base" "$branch" "$transport"
	;;
fetch)
	fetch_all
	printf '\n'
	;;
merged)
	# いまのブランチ（親のブランチ）の MR がマージ済みか（ADR-0093 の 3.6 の 5）。ccnavi-sync.sh が、
	# 親のブランチがリモートから消えて統合先の done/ にも見えないときに、観測ずれかを確かめるために聞く。
	# 読むだけで、何も書かない。答えは 3 つで、呼ぶ側は none のときだけ「マージされていない」と読む。
	#   merged <番号>  終了コード 0
	#   none          終了コード 0（マージ済みの MR が無いと分かった）
	#   unknown       終了コード 3（API が落ちた・答えを読めなかった）。道具やトークンが無ければ、
	#                 上の道具の選び方が 2 で止める（none は出さない）
	if [ "$kind" = github ]; then
		owner="${path%%/*}"
		answer=$(api GET "repos/$path/pulls?state=closed&head=$owner:$branch") || {
			printf 'unknown\n'
			exit 3
		}
		found=$(printf '%s' "$answer" | "$JQ" -r '[.[] | select(.merged_at != null)][0].number // empty') || {
			printf 'unknown\n'
			exit 3
		}
	else
		answer=$(api GET "projects/$(encoded_path)/merge_requests?state=merged&source_branch=$branch") || {
			printf 'unknown\n'
			exit 3
		}
		found=$(printf '%s' "$answer" | "$JQ" -r '.[0].iid // empty') || {
			printf 'unknown\n'
			exit 3
		}
	fi
	if [ -n "$found" ]; then
		printf 'merged %s\n' "$found"
	else
		printf 'none\n'
	fi
	;;
confirm)
	# 印に残すアカウントはここがホストに聞く。呼び手が渡したもの（--actor）は受けない
	for a in "$@"; do
		case "$a" in
		--actor | --actor=*) fail confirm-actor "confirm は --actor を受けない。印のアカウントはトークンの持ち主をホストに聞いて入れる。" 2 ;;
		esac
	done
	# 印に残すアカウント。ロックを取る前に引く（引けなくても止めない。前と同じ印になる）
	actor=$(account || :)
	log_debug 印のアカウント -- "actor=${actor:+set}"
	[ -z "$actor" ] || set -- "$@" "--actor=$actor"
	c1_start "$branch"
	fetch_all >"$result"
	c1_ccnavi "$branch のレビュー済みを置いた" -- review confirm "$@" --result "$result"
	;;
request)
	# 段 0: 取り込み済みの家族なら C1 の前半（ロック・取り込み）を先に済ませる。
	c1_start "$branch"
	# 段 1: 前提。exe が依頼の本文と、マージリクエストの下書きを書き出す。
	tell_skew
	prepared=$(ccnavi review prepare "$@") || exit $?
	file=$(printf '%s\n' "$prepared" | sed -n 1p)
	draft=$(printf '%s\n' "$prepared" | sed -n 2p)
	# 段 1.5: 入れ物が無ければ作る。人はレビューをここで行うので、
	# 「見る場所が無い」で止めない。統合するのは人なので下書きで作る。
	mr=$(find_mr)
	if [ -z "$mr" ]; then
		[ -n "$draft" ] && [ -f "$draft" ] || fail no-draft "マージリクエストの下書きを読めない ($draft)。"
		mr=$(create_mr "$draft") || fail mr-create-failed "マージリクエストを作れなかった。ホストの返事は上に出ている。"
		[ -z "$mr" ] && fail mr-create-empty "マージリクエストを作れなかった。"
		printf 'マージリクエストを作った: %s\n' "$(printf '%s' "$mr" | "$JQ" -r '.url')"
	fi
	number=$(printf '%s' "$mr" | "$JQ" '.number')
	url=$(printf '%s' "$mr" | "$JQ" -r '.url')
	# 段 2: 投稿。同じ目印（親・フェーズ・鍵）の依頼が既にあれば投稿し直さない（打ち直しや C1 の
	# やり直しで依頼を二重にしない。段階 2d のレビューの決定 D）。
	request_marker=$(head -n 1 "$file" | tr -d '\r')
	posted=$(find_posted "$number" "$url" "$request_marker") ||
		fail request-posted-unknown "投稿済みの依頼を確かめられなかった（ホストの返事は上に出ている）。二重に投稿しないよう止めた。"
	if [ -n "$posted" ]; then
		printf '同じ依頼は投稿済み（%s）。投稿し直さない\n' "$(printf '%s' "$posted" | "$JQ" -r '.url')"
	else
		posted=$(comment "$number" "$url" "$file")
	fi
	"$JQ" -n --arg host "$kind" --argjson mr "$mr" --argjson posted "$posted" \
		'{host: $host, mr: $mr} + $posted' >"$result"
	# 段 3: マーカー。
	c1_ccnavi "$branch のレビューを依頼した" -- review requested "$@" --result "$result"
	;;
comment)
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
	[ -n "$body" ] && [ -f "$body" ] || fail no-body-file "comment には --body-file <本文> が要る。" 2
	mr=$(find_mr)
	[ -z "$mr" ] && fail no-mr "親ブランチ $branch に対応するマージリクエストが $host に無い。"
	number=$(printf '%s' "$mr" | "$JQ" '.number')
	url=$(printf '%s' "$mr" | "$JQ" -r '.url')
	noted="$state/review-comment-$$.md"
	{
		printf '<!-- ccnavi:comment -->\n'
		cat "$body"
	} >"$noted"
	posted=$(comment "$number" "$url" "$noted")
	rm -f "$noted"
	printf 'OK: 記録した（%s）\n' "$(printf '%s' "$posted" | "$JQ" -r '.url')"
	;;
decide)
	# 残った指摘の行き先を、人が指摘ごとに決める。形は 3 つ。
	#   decide <N>                                   端末で 1 件ずつ選ぶ（人が打つ）
	#   decide <N> --preview                         見せる一覧を JSON で返す。何も置かない（ボードが読む）
	#   decide <N> --choices <JSON> --digest <指紋>  ボードで人が押した選択を置く
	# 最後の形は、エージェントが打つと組み込みの deny（builtin-guard-ticket-approval）が止める。
	n="${1:-}"
	case "$n" in
	'' | *[!0-9]*) fail decide-no-phase "decide には <N>（フェーズ番号）が要る。" 2 ;;
	esac
	# 下書きの名前は実行ファイルが整数で組む（`01` でも `1`）。揃えないと、書いた下書きを拾えない
	n=$(printf '%s' "$n" | sed 's/^0*\([0-9]\)/\1/')
	shift
	preview=0
	choices=""
	digest=""
	while [ "$#" -gt 0 ]; do
		case "$1" in
		--preview)
			preview=1
			shift
			;;
		--json) shift ;;
		--choices | --digest)
			[ "$#" -ge 2 ] || fail decide-no-value "decide の $1 には値が要る。" 2
			if [ "$1" = --choices ]; then choices="$2"; else digest="$2"; fi
			shift 2
			;;
		*) fail decide-bad-option "decide は $1 を受けない。" 2 ;;
		esac
	done
	if [ "$preview" -eq 1 ] && [ -n "$choices$digest" ]; then
		fail decide-preview-conflict "decide の --preview と --choices / --digest は一緒に使えない。" 2
	fi
	if [ -n "$choices" ] || [ -n "$digest" ]; then
		[ -n "$choices" ] && [ -n "$digest" ] || fail decide-choices-pair "decide の --choices と --digest は組で渡す。" 2
	fi
	# 印に残すアカウント（ADR-0093 の 8.9。段階 5）。confirm と同じくトークンの持ち主をホストに聞き、
	# ロックを取る前に引く。引けなければ渡さず、印も跡も前と同じ。見るだけの --preview は引かない。
	# 経路は、ボードの押した選択（--choices）なら board、端末で選ぶ形なら terminal。
	decide_who=""
	if [ "$preview" -eq 0 ]; then
		decide_actor=$(account || :)
		log_debug 印のアカウント -- "actor=${decide_actor:+set}"
		if [ -n "$decide_actor" ]; then
			if [ -n "$choices" ]; then decide_via=board; else decide_via=terminal; fi
			decide_who="--actor=$decide_actor --via=$decide_via"
		fi
	fi
	# 見るだけの --preview は何も書かないので C1 にしない。端末で選ぶ形は、選ぶのを C1 の外で先に
	# 済ませる（ロックを持ったまま人を待たない。段階 2d のレビューの決定 A）ので、ここでは始めない。
	[ "$preview" -eq 1 ] || [ -z "$choices" ] || c1_start "$branch"
	fetch_all >"$result"
	if [ "$preview" -eq 1 ]; then
		ccnavi --reviewed "$n" --accept-unresolved --preview --json --result "$result"
		exit $?
	fi
	if [ -n "$choices" ]; then
		# 失敗の答え（見せた指摘と今の指摘が違う、など）も JSON で返すので、先に出してから終わる。
		tell_skew
		ccnavi_c1_capture="$state/review-decide-$$.out"
		decide_rc=0
		# shellcheck disable=SC2086 # decide_who は空か --actor=<形を確かめた名前> --via=<語> の 2 語
		c1_ccnavi "$branch のフェーズ $n の指摘の行き先を決めた" -- \
			--reviewed "$n" --accept-unresolved --yes "$choices" --digest "$digest" --json --result "$result" $decide_who ||
			decide_rc=$?
		out=$(cat "$ccnavi_c1_capture" 2>/dev/null || :)
		rm -f "$ccnavi_c1_capture"
		ccnavi_c1_capture=""
		[ "$decide_rc" -eq 0 ] || {
			printf '%s\n' "$out"
			exit 1
		}
		post_decision "$n"
		printf '%s' "$out" | "$JQ" -c --arg u "$issue_url" --arg w "$post_warning" '. + {issue_url: $u, warning: $w}'
		exit 0
	fi
	ccnavi_c1_family "$branch"
	case "$ccnavi_c1_target" in
	stop)
		ccnavi_c1_refuse
		exit 1
		;;
	yes)
		# 1. 人が端末で選ぶ（何も置かない。選択と指紋を控えの置き場に書くだけ）。
		chosen="$state/review-choose-$$.json"
		ccnavi --reviewed "$n" --accept-unresolved --choose-out "$chosen" --result "$result" || {
			rm -f "$chosen"
			exit 1
		}
		choices=$("$JQ" -c '.choices' "$chosen") && digest=$("$JQ" -r '.digest' "$chosen") || {
			rm -f "$chosen"
			fail decide-choose-unreadable "選んだものを読めない（${chosen}）。" 1
		}
		rm -f "$chosen"
		# 2. C1 の中で、選んだものを置く（`--yes`。見せた指摘と今の指摘の指紋が同じときだけ）。
		ccnavi_c1_begin || exit 1
		c1_on=yes
		ccnavi_c1_capture="$state/review-decide-$$.out"
		decide_rc=0
		# shellcheck disable=SC2086 # decide_who は空か --actor=<形を確かめた名前> --via=<語> の 2 語
		c1_ccnavi "$branch のフェーズ $n の指摘の行き先を決めた" -- \
			--reviewed "$n" --accept-unresolved --yes "$choices" --digest "$digest" --json --result "$result" $decide_who ||
			decide_rc=$?
		out=$(cat "$ccnavi_c1_capture" 2>/dev/null || :)
		rm -f "$ccnavi_c1_capture"
		ccnavi_c1_capture=""
		if [ "$decide_rc" -ne 0 ]; then
			[ -z "$out" ] || printf '%s\n' "$out"
			exit 1
		fi
		printf 'OK: フェーズ %s の指摘の行き先を置いて送った（%s）\n' "$n" "$out"
		;;
	*)
		# shellcheck disable=SC2086 # decide_who は空か --actor=<形を確かめた名前> --via=<語> の 2 語
		ccnavi --reviewed "$n" --accept-unresolved --result "$result" $decide_who
		;;
	esac
	post_decision "$n"
	[ -n "$issue_url" ] && printf 'issue に回した: %s\n' "$issue_url"
	[ -n "$post_warning" ] && printf 'ccnavi-review: %s\n' "$post_warning" >&2
	:
	;;
ready)
	# 親を閉じられる状態なら Draft を外す。exe が条件を確かめてマーカーとコメントの下書きを置き、
	# ここが外してコメントを投稿する。マージは人。
	c1_start "$branch"
	fetch_all >"$result"
	tell_skew
	ccnavi_c1_capture="$state/review-ready-$$.out"
	ready_rc=0
	c1_ccnavi "$branch の Draft を外す印を置いた" -- review ready --result "$result" || ready_rc=$?
	noted=$(cat "$ccnavi_c1_capture" 2>/dev/null || :)
	rm -f "$ccnavi_c1_capture"
	ccnavi_c1_capture=""
	[ "$ready_rc" -eq 0 ] || exit "$ready_rc"
	number=$(printf '%s' "$(cat "$result")" | "$JQ" '.mr.number')
	url=$(printf '%s' "$(cat "$result")" | "$JQ" -r '.mr.url')
	still=$(undraft "$number")
	[ "$still" = "false" ] || fail undraft-failed "Draft を外せなかった（${url}）。ホストの返事は上に出ている。"
	if [ -n "$noted" ] && [ -f "$noted" ]; then
		comment "$number" "$url" "$noted" >/dev/null && rm -f "$noted"
	fi
	printf 'OK: Draft を外した（%s）。マージは利用者が行う\n' "$url"
	;;
close-early)
	# 人が端末で打つ。exe が残りを見せて y/N を取り、マーカーを置いて下書きを書く。
	# ここが残りを issue に写し、コメントを投稿する。Draft を外すのは、親が片付けて
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
	[ -n "$reason" ] || fail close-early-no-reason "close-early には --reason <理由> が要る。" 2
	fetch_all >"$result"
	# exe は人に残りを見せて y/N を取るので、標準出力は端末のまま。下書きは控えの
	# 置き場の決まった名前で拾う（親の識別子 = ブランチ名）。
	ccnavi --close-early --reason "$reason" --result "$result" || exit $?
	issue_draft="$state/review-close-early-issue-$branch.md"
	noted="$state/review-close-early-note-$branch.md"
	number=$(printf '%s' "$(cat "$result")" | "$JQ" '.mr.number')
	url=$(printf '%s' "$(cat "$result")" | "$JQ" -r '.mr.url')
	if [ "$make_issue" -eq 1 ] && [ -f "$issue_draft" ]; then
		issue=$(create_issue "$issue_draft")
		[ -z "$issue" ] && fail issue-create-failed "残りを写す issue を作れなかった。下書きは $issue_draft にある。"
		issue_url=$(printf '%s' "$issue" | "$JQ" -r '.url')
		issue_no=$(printf '%s' "$issue" | "$JQ" -r '.number')
		printf '残りを #%s に写した（%s）\n' "$issue_no" "$issue_url"
		printf '残りは #%s へ: %s\n' "$issue_no" "$issue_url" >>"$noted"
		rm -f "$issue_draft"
	fi
	if [ -f "$noted" ]; then
		comment "$number" "$url" "$noted" >/dev/null && rm -f "$noted"
	fi
	# 取り込み済みの家族なら、締めの印（人の判断）を運ぶ処理で送る（D27）。
	carry_human "$branch" || exit 1
	printf 'OK: 締めた（%s）。あとは親に、閉じて片付けて push し、ready を打たせる。マージは利用者が行う\n' "$url"
	;;
chat)
	# 人が端末で打つ。chat で見るフェーズを、このセッションで見終えたと置く（ccnavi --reviewed <N> --chat）。
	# 取り込み済みの家族なら、置いた後に運ぶ処理で送る（D27）。
	n="${1:-}"
	case "$n" in
	'' | *[!0-9]*) fail chat-no-phase "chat には <N>（フェーズ番号）が要る。" 2 ;;
	esac
	[ "$#" -eq 1 ] || fail chat-bad-args "chat は <N> だけを取る。" 2
	ccnavi --reviewed "$n" --chat || exit $?
	carry_human "$branch" || exit 1
	;;
config-synced)
	# 人が端末で打つ。着手で上書きした設定を見たと残す（ccnavi --config-synced <親>）。
	# 取り込み済みの家族なら、置いた後に運ぶ処理で送る（D27）。
	parent="${1:-}"
	case "$parent" in
	'' | -* | *..* | */* | *[!A-Za-z0-9._-]*) fail config-synced-no-parent "config-synced には <親>（親の識別子）が要る。" 2 ;;
	esac
	[ "$#" -eq 1 ] || fail config-synced-bad-args "config-synced は <親> だけを取る。" 2
	ccnavi --config-synced "$parent" || exit $?
	carry_human "$parent" || exit 1
	;;
esac
