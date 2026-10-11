# ccnavi-common-host.sh  ccnavi-common.sh が読む部品。単体では読まない（読み方は ccnavi-common.sh の冒頭）。
#
# ホスト（GitHub / GitLab）への接続。

# ---- ホスト（GitHub / GitLab）への接続（ccnavi-review.sh と ccnavi-branches.sh が使う）
#
# origin の URL でホストを見分け、gh / glab（認証は道具に任せる）か curl とトークン
# （GITHUB_TOKEN / GITLAB_TOKEN）でホストの API を読み書きする。結果の組み立てには jq が要る。
# 道具は ccnavi_host_connect で絶対パスへ解いて固定する。PATH の細工で差し替えられないように。
#
#   ccnavi_host_parse <origin>  origin を読み、ccnavi_h_scheme・ccnavi_h_host・ccnavi_h_path・
#                               ccnavi_h_kind（github / gitlab）・ccnavi_h_token_name・ccnavi_h_api_base を決める。
#                               読めなければ 1（URL の形式）・2（ホスト）・3（パス）
#   ccnavi_host_connect         道具を選ぶ。ccnavi_h_jq・ccnavi_h_cli・ccnavi_h_cli_name・ccnavi_h_curl・
#                               ccnavi_h_token・ccnavi_h_transport（gh / glab / curl）を決める。
#                               jq が無ければ 3、curl はあるがトークンが無ければ 4、curl も無ければ 5
#   ccnavi_host_api <METHOD> <path> [<JSON>]  応答の JSON を標準出力へ。path は ccnavi_h_api_base からの相対
#   ccnavi_host_pages <path>    100 件ずつ最後のページまで読んで 1 つの配列にする。20 ページを超えたら 2
#   ccnavi_host_encoded_path    プロジェクトのパスを URL に入れる表記（GitLab の projects/<ここ>）
#   ccnavi_host_project_id      GitLab のプロジェクトの数の id。読めなければ 1
#
# 失敗したときの文面は呼ぶ側が決める（ここは標準エラーに何も書かない）。ccnavi_host_api が失敗したら、
# ccnavi_h_on_fail に名前を入れた関数を `<METHOD> <path> <ホストの返事>` で呼んで 1 を返す。
# ccnavi_h_tmp は gh / glab の標準エラーを一時に受ける置き場（無ければ TMPDIR）、ccnavi_h_max_time は
# curl の 1 回の時間の上限（秒。応答しないホストで止まり続けないように。gh / glab は道具に任せる）。

ccnavi_h_on_fail=""
ccnavi_h_tmp=""
ccnavi_h_max_time=120

ccnavi_host_parse() {
	ccnavi_hp_origin="$1"
	# scheme は origin から取る。https に決め打ちすると、社内や手元で平文で立てた
	# GitLab（`http://localhost:8929` のような形）に当たらない。ssh の形式には
	# scheme が無いので、そこだけ https にする。
	# host にはポートを残す。省くと `:8929` のような立て方がすべて当たらなくなり、しかも
	# 省かれたポートがプロジェクトのパスの先頭に入り込む（`8929/demo/greeter`）。
	case "$ccnavi_hp_origin" in
	http://*) ccnavi_h_scheme=http ;;
	*) ccnavi_h_scheme=https ;;
	esac
	ccnavi_hp_rest=$(printf '%s' "$ccnavi_hp_origin" | sed -E 's#^(https?://|git@|ssh://git@)##')
	[ "$ccnavi_hp_rest" = "$ccnavi_hp_origin" ] && return 1
	# `user:token@host` の形はユーザ情報を除く。URL にトークンを埋める使い方は普通にあり、
	# 除かないと host にトークンが入り込み、API の URL にも `origin` の出力にも漏れる（実際に確かめた）。
	# 認証は gh / glab か GITLAB_TOKEN / GITHUB_TOKEN で行い、URL 側の資格情報は使わない。
	ccnavi_hp_authority="${ccnavi_hp_rest%%/*}"
	case "$ccnavi_hp_authority" in
	*@*) ccnavi_hp_rest="${ccnavi_hp_authority##*@}${ccnavi_hp_rest#"$ccnavi_hp_authority"}" ;;
	esac
	ccnavi_h_host="${ccnavi_hp_rest%%/*}"
	# ssh の `git@host:group/proj` は `:` の後ろがパス。数字だけならポート、
	# そうでなければパスの先頭なので除く。
	case "$ccnavi_h_host" in
	*:*)
		case "${ccnavi_h_host##*:}" in
		'' | *[!0-9]*) ccnavi_h_host="${ccnavi_h_host%%:*}" ;;
		esac
		;;
	esac
	[ -n "$ccnavi_h_host" ] || return 2
	ccnavi_hp_tail="${ccnavi_hp_rest#"$ccnavi_h_host"}"
	while :; do
		case "$ccnavi_hp_tail" in
		[/:]*) ccnavi_hp_tail="${ccnavi_hp_tail#?}" ;;
		*) break ;;
		esac
	done
	ccnavi_h_path="${ccnavi_hp_tail%.git}"
	ccnavi_h_path="${ccnavi_h_path%/}"
	[ -n "$ccnavi_h_path" ] || return 3
	case "$ccnavi_h_host" in
	github.com | github.com:*)
		ccnavi_h_kind=github
		ccnavi_h_token_name=GITHUB_TOKEN
		ccnavi_h_api_base="https://api.github.com"
		;;
	*)
		ccnavi_h_kind=gitlab
		ccnavi_h_token_name=GITLAB_TOKEN
		ccnavi_h_api_base="$ccnavi_h_scheme://$ccnavi_h_host/api/v4"
		;;
	esac
	return 0
}

ccnavi_host_connect() {
	ccnavi_h_jq=$(command -v jq 2>/dev/null || :)
	[ -n "$ccnavi_h_jq" ] || return 3
	# gh / glab は「入っている」だけでは足りない。そのホストで認証されていなければ
	# 通らない（手元に立てた GitLab に glab を繋いでいない、が普通にある）。
	# 1 度だけ疎通を試して、通らなければ curl とトークンに切り替える。
	ccnavi_h_transport=""
	ccnavi_h_curl=""
	ccnavi_h_token=""
	if [ "$ccnavi_h_kind" = github ]; then
		ccnavi_h_cli_name=gh
		ccnavi_h_cli=$(command -v gh 2>/dev/null || :)
		if [ -n "$ccnavi_h_cli" ] && "$ccnavi_h_cli" api --hostname "$ccnavi_h_host" "repos/$ccnavi_h_path" >/dev/null 2>&1; then
			ccnavi_h_transport=gh
		fi
	else
		ccnavi_h_cli_name=glab
		ccnavi_h_cli=$(command -v glab 2>/dev/null || :)
		if [ -n "$ccnavi_h_cli" ] && "$ccnavi_h_cli" api --hostname "$ccnavi_h_host" "projects/$(ccnavi_host_encoded_path)" >/dev/null 2>&1; then
			ccnavi_h_transport=glab
		fi
	fi
	[ -n "$ccnavi_h_transport" ] && return 0
	ccnavi_h_curl=$(command -v curl 2>/dev/null || :)
	eval "ccnavi_h_token=\${$ccnavi_h_token_name:-}"
	if [ -n "$ccnavi_h_curl" ] && [ -n "$ccnavi_h_token" ]; then
		ccnavi_h_transport=curl
		return 0
	fi
	[ -n "$ccnavi_h_curl" ] && return 4
	return 5
}

# 失敗したら標準出力には何も出さず、ホストが返した本文ごと ccnavi_h_on_fail に渡して 1 を返す。
# gh と glab は 4xx でも本文を標準出力へ書くので、そのまま流すと `{"message":"Not Found"}` が
# jq に渡り、`{number: null}` の形で「マージリクエストができた」ことになる（実際に確かめた）。
ccnavi_host_api() {
	ccnavi_ha_method="$1"
	ccnavi_ha_rel="$2"
	ccnavi_ha_body="${3:-}"
	case "$ccnavi_h_transport" in
	gh | glab)
		# 標準エラー（更新の知らせなど）は応答に混ぜない。失敗したときだけ本文と一緒に渡す
		ccnavi_ha_err="${ccnavi_h_tmp:-${TMPDIR:-/tmp}}/ccnavi-host-err-$$"
		if [ -n "$ccnavi_ha_body" ]; then
			ccnavi_ha_out=$(printf '%s' "$ccnavi_ha_body" | "$ccnavi_h_cli" api --hostname "$ccnavi_h_host" --method "$ccnavi_ha_method" --input - "$ccnavi_ha_rel" 2>"$ccnavi_ha_err") || {
				ccnavi_host_failed "$ccnavi_ha_out$(cat "$ccnavi_ha_err" 2>/dev/null)"
				rm -f "$ccnavi_ha_err"
				return 1
			}
		else
			ccnavi_ha_out=$("$ccnavi_h_cli" api --hostname "$ccnavi_h_host" --method "$ccnavi_ha_method" "$ccnavi_ha_rel" 2>"$ccnavi_ha_err") || {
				ccnavi_host_failed "$ccnavi_ha_out$(cat "$ccnavi_ha_err" 2>/dev/null)"
				rm -f "$ccnavi_ha_err"
				return 1
			}
		fi
		rm -f "$ccnavi_ha_err"
		;;
	curl)
		if [ "$ccnavi_h_kind" = github ]; then
			ccnavi_ha_auth="Authorization: Bearer $ccnavi_h_token"
		else
			ccnavi_ha_auth="PRIVATE-TOKEN: $ccnavi_h_token"
		fi
		if [ -n "$ccnavi_ha_body" ]; then
			ccnavi_ha_out=$(printf '%s' "$ccnavi_ha_body" | "$ccnavi_h_curl" -fsS --connect-timeout 15 --max-time "$ccnavi_h_max_time" -X "$ccnavi_ha_method" -H "$ccnavi_ha_auth" -H 'Content-Type: application/json' --data-binary @- "$ccnavi_h_api_base/$ccnavi_ha_rel" 2>&1) || {
				ccnavi_host_failed "$ccnavi_ha_out"
				return 1
			}
		else
			ccnavi_ha_out=$("$ccnavi_h_curl" -fsS --connect-timeout 15 --max-time "$ccnavi_h_max_time" -X "$ccnavi_ha_method" -H "$ccnavi_ha_auth" "$ccnavi_h_api_base/$ccnavi_ha_rel" 2>&1) || {
				ccnavi_host_failed "$ccnavi_ha_out"
				return 1
			}
		fi
		;;
	*) return 1 ;;
	esac
	printf '%s' "$ccnavi_ha_out"
}

ccnavi_host_failed() {
	[ -n "$ccnavi_h_on_fail" ] || return 0
	"$ccnavi_h_on_fail" "$ccnavi_ha_method" "$ccnavi_ha_rel" "$1"
}

ccnavi_host_pages() {
	ccnavi_hg_rel="$1"
	ccnavi_hg_page=1
	case "$ccnavi_hg_rel" in
	*\?*) ccnavi_hg_sep='&' ;;
	*) ccnavi_hg_sep='?' ;;
	esac
	ccnavi_hg_all='[]'
	while :; do
		# 呼び手が `|| ...` で受けると set -e が有効にならないので、失敗したらここで 1 を返す（配列でない答えも）
		ccnavi_hg_chunk=$(ccnavi_host_api GET "$ccnavi_hg_rel${ccnavi_hg_sep}per_page=100&page=$ccnavi_hg_page") || return 1
		ccnavi_hg_n=$(printf '%s' "$ccnavi_hg_chunk" | "$ccnavi_h_jq" 'if type == "array" then length else error("not an array") end' 2>/dev/null) || return 1
		ccnavi_hg_all=$(printf '%s\n%s' "$ccnavi_hg_all" "$ccnavi_hg_chunk" | "$ccnavi_h_jq" -s '.[0] + .[1]')
		[ "$ccnavi_hg_n" -lt 100 ] && break
		ccnavi_hg_page=$((ccnavi_hg_page + 1))
		[ "$ccnavi_hg_page" -gt 20 ] && return 2
	done
	printf '%s' "$ccnavi_hg_all"
}

ccnavi_host_encoded_path() {
	printf '%s' "$ccnavi_h_path" | "$ccnavi_h_jq" -Rr '@uri'
}

ccnavi_host_project_id() {
	ccnavi_hi_out=$(ccnavi_host_api GET "projects/$(ccnavi_host_encoded_path)") || return 1
	ccnavi_hi_val=$(printf '%s' "$ccnavi_hi_out" | "$ccnavi_h_jq" -r '.id // empty') || return 1
	case "$ccnavi_hi_val" in
	'' | *[!0-9]*) return 1 ;;
	esac
	printf '%s' "$ccnavi_hi_val"
}
