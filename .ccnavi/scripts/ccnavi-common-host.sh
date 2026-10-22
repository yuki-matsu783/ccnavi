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
#                               ccnavi_h_kind（github / gitlab）・ccnavi_h_token_name・ccnavi_h_api_base・
#                               ccnavi_h_class_host を決める（GitHub 系は ccnavi_h_graphql_base も）。
#                               読めなければ 1（URL の形式）・2（ホスト）・3（パス）・4（GHE.com のホストにポートが
#                               付いていて API の URL を組めない。理由は ccnavi_h_parse_why）
#
# ホストの分類（GitHub か GitLab か）は、origin から取り出したホストを小文字にしてポートを除いたもの
# （ccnavi_h_class_host）を、`.` の区切りで照合して決める。scheme の大文字小文字は区別しない。
#   GitHub : github.com、ghe.com とそのサブドメイン（*.ghe.com）
#            REST は github.com が https://api.github.com、GHE.com が https://api.<分類に使うホスト>。
#            GraphQL はその REST の根の /graphql。GHE.com の API の URL の規則は実物では未検証
#   GitLab : 上のどれにも当たらないもの全部（gitlab.com、*.aslead.cloud、セルフホスト、未知のホスト）。
#            接続先は origin の scheme・ホスト・ポートをそのまま保つ（<scheme>://<ホスト[:ポート]>/api/v4）
# Python の review_host.classify と、Chrome 拡張の hostclass.ts が同じ規則で、tests/fixtures/host-kinds.json の
# 同じ表で突き合わせる。
#   ccnavi_host_connect         道具を選ぶ。ccnavi_h_jq・ccnavi_h_cli・ccnavi_h_cli_name・ccnavi_h_curl・
#                               ccnavi_h_token・ccnavi_h_transport（gh / glab / curl）を決める。
#                               jq が無ければ 3、curl はあるがトークンが無ければ 4、curl も無ければ 5
#   ccnavi_host_api <METHOD> <path> [<JSON>]  応答の JSON を標準出力へ。path は ccnavi_h_api_base からの相対
#   ccnavi_host_pages <path>    100 件ずつ最後のページまで読んで 1 つの配列にする。20 ページを超えたら 2
#   ccnavi_host_encoded_path    プロジェクトのパスを URL に入れる表記（GitLab の projects/<ここ>）
#   ccnavi_host_encode <文字列> クエリの値に入れる文字列（ブランチ名など）のパーセントエンコード
#   ccnavi_host_project_id      GitLab のプロジェクトの数の id。読めなければ 1
#
# 失敗したときの文面は呼ぶ側が決める。ccnavi_host_api が失敗したら、
# ccnavi_h_on_fail に名前を入れた関数を `<METHOD> <path> <ホストの返事>` で呼んで 1 を返す。
# ccnavi_h_tmp は gh / glab の標準エラーを一時に受ける置き場（無ければ TMPDIR）、ccnavi_h_max_time は
# curl の 1 回の時間の上限（秒。応答しないホストで止まり続けないように）。

ccnavi_h_on_fail=""
ccnavi_h_tmp=""
ccnavi_h_max_time=120

ccnavi_host_parse() {
	ccnavi_hp_origin="$1"
	ccnavi_h_parse_why=""
	# scheme は origin から取る（大文字小文字は区別しない）。https に決め打ちすると、社内や手元で
	# 平文で立てた GitLab（`http://localhost:8929` のような形）に当たらない。ssh の形式には
	# scheme が無いので、そこだけ https にする。
	# host にはポートを残す（GitLab の接続先に使う）。省くと `:8929` のような立て方がすべて当たらなくなり、しかも
	# 省かれたポートがプロジェクトのパスの先頭に入り込む（`8929/demo/greeter`）。分類にはポートを除いた
	# ホスト（ccnavi_h_class_host）を使う。
	case "$ccnavi_hp_origin" in
	*://*)
		ccnavi_hp_scheme=$(printf '%s' "${ccnavi_hp_origin%%://*}" | tr 'A-Z' 'a-z')
		case "$ccnavi_hp_scheme" in
		*[/@:]*) ;;
		*) ccnavi_hp_origin="$ccnavi_hp_scheme://${ccnavi_hp_origin#*://}" ;;
		esac
		;;
	esac
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
	# そうでなければパスの先頭なので除く。IPv6 は `[::1]` の形で、`]` までがホスト（後ろに数字のポートが続けばそれも）。
	case "$ccnavi_hp_rest" in
	'['*']'*)
		ccnavi_hp_v6="${ccnavi_hp_rest%%]*}]"
		ccnavi_hp_after="${ccnavi_hp_rest#"$ccnavi_hp_v6"}"
		ccnavi_h_host="$ccnavi_hp_v6"
		case "$ccnavi_hp_after" in
		:[0-9]*) ccnavi_h_host="$ccnavi_hp_v6${ccnavi_hp_after%%[!:0-9]*}" ;;
		esac
		;;
	*)
		case "$ccnavi_h_host" in
		*:*)
			case "${ccnavi_h_host##*:}" in
			'' | *[!0-9]*) ccnavi_h_host="${ccnavi_h_host%%:*}" ;;
			esac
			;;
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
	ccnavi_h_path="${ccnavi_hp_tail%/}"
	ccnavi_h_path="${ccnavi_h_path%.git}"
	ccnavi_h_path="${ccnavi_h_path%/}"
	[ -n "$ccnavi_h_path" ] || return 3
	# 分類に使うホスト: 小文字にして、数字だけのポートを除く。照合は `.` の区切りで行う
	# （`evilgithub.com` や `github.com.evil` は GitHub にならない）。
	ccnavi_hp_port=""
	ccnavi_h_class_host=$(printf '%s' "$ccnavi_h_host" | tr 'A-Z' 'a-z')
	case "$ccnavi_h_class_host" in
	*:*)
		case "${ccnavi_h_class_host##*:}" in
		'' | *[!0-9]*) ;;
		*)
			ccnavi_hp_port="${ccnavi_h_class_host##*:}"
			ccnavi_h_class_host="${ccnavi_h_class_host%:*}"
			;;
		esac
		;;
	esac
	ccnavi_h_graphql_base=""
	case "$ccnavi_h_class_host" in
	github.com)
		ccnavi_h_kind=github
		ccnavi_h_token_name=GITHUB_TOKEN
		ccnavi_h_host="$ccnavi_h_class_host"
		ccnavi_h_api_base="https://api.github.com"
		ccnavi_h_graphql_base="https://api.github.com/graphql"
		;;
	ghe.com | ?*.ghe.com)
		ccnavi_h_kind=github
		ccnavi_h_token_name=GITHUB_TOKEN
		if [ -n "$ccnavi_hp_port" ]; then
			ccnavi_h_parse_why="GHE.com のホスト ${ccnavi_h_class_host} にポート :${ccnavi_hp_port} が付いているので、API の URL（https://api.${ccnavi_h_class_host}）を組めない"
			return 4
		fi
		ccnavi_h_host="$ccnavi_h_class_host"
		ccnavi_h_api_base="https://api.$ccnavi_h_class_host"
		ccnavi_h_graphql_base="$ccnavi_h_api_base/graphql"
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
		if [ "$ccnavi_hg_page" -gt 20 ]; then
			# 打ち切った。呼び手が ccnavi_hg_partial を立てていれば、読めた分を出す
			# （送信の照合は、途中までの一覧でも目印を探し、0 件だけは確定にしない）。
			[ -z "${ccnavi_hg_partial:-}" ] || printf '%s' "$ccnavi_hg_all"
			return 2
		fi
	done
	printf '%s' "$ccnavi_hg_all"
}

ccnavi_host_encoded_path() {
	printf '%s' "$ccnavi_h_path" | "$ccnavi_h_jq" -Rr '@uri'
}

# クエリの値に入れる文字列（ブランチ名など）をパーセントエンコードして出す。
# 日本語を生で URL に入れると、ホストが HTML のエラーを返して JSON として読めない。
ccnavi_host_encode() {
	printf '%s' "$1" | "$ccnavi_h_jq" -Rr '@uri' | tr -d '\r'
}

ccnavi_host_project_id() {
	ccnavi_hi_out=$(ccnavi_host_api GET "projects/$(ccnavi_host_encoded_path)") || return 1
	ccnavi_hi_val=$(printf '%s' "$ccnavi_hi_out" | "$ccnavi_h_jq" -r '.id // empty') || return 1
	case "$ccnavi_hi_val" in
	'' | *[!0-9]*) return 1 ;;
	esac
	printf '%s' "$ccnavi_hi_val"
}

# ---- git の認証（gh / glab の資格情報を git に使わせる）
#
#   ccnavi_git_auth <リポジトリ>  origin が http(s) のとき、そのホストの資格情報の取り手に
#                                 `gh auth git-credential`（GitHub 系）か `glab auth git-credential`
#                                 （それ以外）を足す。git を打つ前に呼ぶ（以降の git に効く）。
#
# gh / glab で認証済みでも、git の push・fetch は別に認証が要る（`could not read Username` で止まる）。
# ログイン済みのホストだけに足すので、未認証・未導入のホストでは何も足されず、
# git は元の設定の取り手（ccnavi が足すのは末尾なので、先にあるものが優先）で進む。
# 足し方は環境変数（GIT_CONFIG_COUNT 系）。.gitconfig や origin の URL は書き換えず、この呼び出しの子プロセスにだけ効く。
# 道具は絶対パスへ解く（PATH の細工で差し替えられないように。ccnavi_host_connect と同じ）。
ccnavi_ga_seen=""
ccnavi_ga_base=""
ccnavi_ga_had=""

# 自分が足した分を外す。元の環境が GIT_CONFIG_COUNT を持たなかったなら、変数ごと戻す。
ccnavi_git_auth_reset() {
	[ -n "$ccnavi_ga_base" ] || return 0
	if [ "$ccnavi_ga_base" = 0 ] && [ -z "$ccnavi_ga_had" ]; then
		unset GIT_CONFIG_COUNT
	else
		GIT_CONFIG_COUNT="$ccnavi_ga_base"
		export GIT_CONFIG_COUNT
	fi
}

ccnavi_git_auth() {
	ccnavi_ga_url=$(git -C "${1:-.}" remote get-url origin 2>/dev/null || :)
	[ "$ccnavi_ga_url" = "$ccnavi_ga_seen" ] && return 0
	# origin が変わったら、前に足した分を外す（http(s) でなければ、そのまま何も足さない）。
	ccnavi_git_auth_reset
	case "$ccnavi_ga_url" in
	[hH][tT][tT][pP]://* | [hH][tT][tT][pP][sS]://*) ;;
	*) return 0 ;;
	esac
	# 親の環境が GIT_CONFIG_COUNT を持っていればその続きに足す。origin が変わったら、自分が足した分を外してやり直す。
	if [ -z "$ccnavi_ga_base" ]; then
		ccnavi_ga_had="${GIT_CONFIG_COUNT+set}"
		ccnavi_ga_base="${GIT_CONFIG_COUNT:-0}"
	fi
	case "$ccnavi_ga_base" in '' | *[!0-9]*) return 0 ;; esac
	ccnavi_ga_seen="$ccnavi_ga_url"
	# ホストの見分けは ccnavi_host_parse に任せる。呼び手の ccnavi_h_* を壊さないよう、副シェルで読む。
	ccnavi_ga_info=$(ccnavi_host_parse "$ccnavi_ga_url" >/dev/null 2>&1 && printf '%s %s %s' "$ccnavi_h_scheme" "$ccnavi_h_host" "$ccnavi_h_kind") || { ccnavi_git_auth_reset; return 0; }
	ccnavi_ga_scheme="${ccnavi_ga_info%% *}"
	ccnavi_ga_rest="${ccnavi_ga_info#* }"
	ccnavi_ga_host="${ccnavi_ga_rest% *}"
	ccnavi_ga_kind="${ccnavi_ga_rest##* }"
	if [ "$ccnavi_ga_kind" = github ]; then
		ccnavi_ga_name=gh
	else
		ccnavi_ga_name=glab
	fi
	ccnavi_ga_cli=$(command -v "$ccnavi_ga_name" 2>/dev/null || :)
	# 絶対パスで、式に効く文字（" $ ` \）を含まないものだけ使う。相対パスだと、リポジトリの中の gh が helper として動く。
	case "$ccnavi_ga_cli" in
	*[\"\$\`\\]*) ccnavi_git_auth_reset; return 0 ;;
	[A-Za-z]:[/\\]* | /*) ;;
	*) ccnavi_git_auth_reset; return 0 ;;
	esac
	# 値は変数のまま eval に渡す（ホスト名や道具のパスを式に埋めない。origin の文字列がコマンドとして走らないように）。
	ccnavi_ga_key="credential.$ccnavi_ga_scheme://$ccnavi_ga_host.helper"
	ccnavi_ga_val="!\"$ccnavi_ga_cli\" auth git-credential"
	eval "GIT_CONFIG_KEY_$ccnavi_ga_base=\$ccnavi_ga_key GIT_CONFIG_VALUE_$ccnavi_ga_base=\$ccnavi_ga_val"
	eval "export GIT_CONFIG_KEY_$ccnavi_ga_base GIT_CONFIG_VALUE_$ccnavi_ga_base"
	GIT_CONFIG_COUNT=$((ccnavi_ga_base + 1))
	export GIT_CONFIG_COUNT
	return 0
}
