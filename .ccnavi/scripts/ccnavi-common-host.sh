# ccnavi-common-host.sh  ccnavi-common.sh が読む部品。単体では読まない（読み方は ccnavi-common.sh の冒頭）。
#
# ホスト（GitHub / GitLab）への接続。

# ---- ホスト（GitHub / GitLab）への接続（ccnavi-review.sh・ccnavi-branches.sh・ccnavi-start.sh と事前確認が使う）
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
#   GitLab : 上のどれにもヒットしないもの全部（gitlab.com、*.aslead.cloud、セルフホスト、未知のホスト）。
#            接続先は origin の scheme・ホスト・ポートをそのまま保つ（<scheme>://<ホスト[:ポート]>/api/v4）
# 実行ファイルの host.Classify と、Chrome 拡張の hostclass.ts が同じ規則で、tests/fixtures/host-kinds.json の
# 同じ表で突き合わせる。
#   ccnavi_host_connect         道具を選ぶ。ccnavi_h_jq・ccnavi_h_cli・ccnavi_h_cli_name・ccnavi_h_curl・
#                               ccnavi_h_token・ccnavi_h_transport（gh / glab / curl）を決める。
#                               jq が無ければ 3、curl はあるがトークンが無ければ 4、curl も無ければ 5
#   ccnavi_host_api <METHOD> <path> [<JSON>]  応答の JSON を標準出力へ。path は ccnavi_h_api_base からの相対
#   ccnavi_host_pages <path>    100 件ずつ最後のページまで読んで 1 つの配列にする。20 ページを超えたら 2
#   ccnavi_host_encoded_path    プロジェクトのパスを URL に入れる表記（GitLab の projects/<ここ>）
#   ccnavi_host_encode <文字列> クエリの値に入れる文字列（ブランチ名など）のパーセントエンコード
#   ccnavi_host_project_id      GitLab のプロジェクトの数の id。読めなければ 1
#   ccnavi_run_timed <秒> <stdin> <stdout> <stderr> <コマンド>...  時間を区切って実行する（timeout コマンドは使わない。時間切れのときは 124 を返す。stdout に `-` を渡すと、呼ぶ側の標準出力を引き継ぐ）
#   ccnavi_host_fail_status <ホストの返事の文>  curl・gh・glab の失敗の文から HTTP の番号を取り出す
#   ccnavi_host_graphql_blocked <ホストの返事の文>  GraphQL を使えない環境（Claude Code のクラウド）の 403 かを判定する（0 なら該当）
#   ccnavi_host_probe           プロジェクトを 1 回読む。結果は ccnavi_h_probe と ccnavi_h_project_json
#   ccnavi_host_access          書き込みの権限を判定する（0 足りる / 1 足りない / 2 判定できない。判定は実行ファイル）
#   ccnavi_preflight <top>      チケット作業の事前確認（下の説明。判定と文は実行ファイルの ccnavi preflight）
#
# 失敗したときの文面は呼ぶ側が決める。ccnavi_host_api が失敗したら、
# ccnavi_h_on_fail に名前を入れた関数を `<METHOD> <path> <ホストの返事>` で呼んで 1 を返す。
# ccnavi_h_tmp は gh / glab の標準エラーを一時に受ける置き場（無ければ TMPDIR）、ccnavi_h_max_time は
# curl の 1 回の時間の上限（秒。応答しないホストで止まり続けないように）。

ccnavi_h_on_fail=""
ccnavi_h_tmp=""
ccnavi_h_max_time=120

# 時間を区切って実行する。<秒> <stdin のファイル> <stdout のファイル> <stderr のファイル> <コマンド>...
# バックグラウンドで動かし、上限を過ぎたら止めて 124 を返す。時間切れは印のファイルで判定する。
# <stdout のファイル> に `-` を渡すと、標準出力は呼ぶ側のものを引き継ぐ。ccnavi_git_timed がこの形で使う。
# 印のファイルは `<stdout のファイル>.timeout`。stdout が `-` か /dev/null のときは `<stderr のファイル>.timeout` に置く。
# stderr も /dev/null なら、TMPDIR の一時名に置く（特殊ファイルに `.timeout` を付けないため）。
# 監視の出力は破棄する。つないだままだと、監視の sleep が終わるまで呼ぶ側の `$( )` が閉じないため。
# 変数は ccnavi_tm_ で始める（呼ぶ側の名前と重ねない）。
ccnavi_run_timed() {
	ccnavi_tm_limit="$1"
	ccnavi_tm_in="$2"
	ccnavi_tm_out="$3"
	ccnavi_tm_err="$4"
	shift 4
	case "$ccnavi_tm_out" in
	- | /dev/null) ccnavi_tm_base="$ccnavi_tm_err" ;;
	*) ccnavi_tm_base="$ccnavi_tm_out" ;;
	esac
	case "$ccnavi_tm_base" in
	/dev/null) ccnavi_tm_base="${TMPDIR:-/tmp}/ccnavi-timed-$$" ;;
	esac
	ccnavi_tm_flag="$ccnavi_tm_base.timeout"
	rm -f "$ccnavi_tm_flag" || :
	if [ "$ccnavi_tm_out" = - ]; then
		"$@" <"$ccnavi_tm_in" 2>"$ccnavi_tm_err" &
	else
		"$@" <"$ccnavi_tm_in" >"$ccnavi_tm_out" 2>"$ccnavi_tm_err" &
	fi
	ccnavi_tm_pid=$!
	# sleep はバックグラウンドで起動して wait で待つ。TERM を受けたら sleep も止めて抜ける（sleep を ppid 1 で残さない）。
	(
		exec </dev/null >/dev/null 2>&1
		ccnavi_tm_sleep=""
		ccnavi_tm_stop=""
		# trap は印を立てるだけにし、分かっていれば sleep も止める。抜けるのは本流で、sleep の pid を控えた後に印を見る
		# （sleep を起動した直後に TERM が来ても、sleep を残さないため）。
		trap 'ccnavi_tm_stop=1; test -z "$ccnavi_tm_sleep" || kill "$ccnavi_tm_sleep"' TERM
		sleep "$ccnavi_tm_limit" &
		ccnavi_tm_sleep=$!
		test -z "$ccnavi_tm_stop" || { kill "$ccnavi_tm_sleep"; exit 0; }
		wait "$ccnavi_tm_sleep"
		test -z "$ccnavi_tm_stop" || exit 0
		: >"$ccnavi_tm_flag"
		kill "$ccnavi_tm_pid"
	) &
	ccnavi_tm_dog=$!
	ccnavi_tm_rc=0
	wait "$ccnavi_tm_pid" 2>/dev/null || ccnavi_tm_rc=$?
	kill "$ccnavi_tm_dog" 2>/dev/null || :
	# 見張りの終了を回収して、シェルに「Terminated」の報告を出させない。
	wait "$ccnavi_tm_dog" 2>/dev/null || :
	# 終わった印。呼ぶ側の後始末が動いている最中かどうかを、これで見分ける。
	ccnavi_tm_dog=""
	ccnavi_tm_pid=""
	if [ -e "$ccnavi_tm_flag" ]; then
		rm -f "$ccnavi_tm_flag" || :
		return 124
	fi
	return "$ccnavi_tm_rc"
}

ccnavi_host_parse() {
	ccnavi_hp_origin="$1"
	ccnavi_h_parse_why=""
	ccnavi_h_project_json=""
	# scheme は origin から取る（大文字小文字は区別しない）。https に決め打ちすると、社内や手元で
	# 平文で立てた GitLab（`http://localhost:8929` のような形）にヒットしない。ssh の形式には
	# scheme が無いので、そこだけ https にする。
	# host にはポートを残す（GitLab の接続先に使う）。省くと `:8929` のような立て方がすべてヒットしなくなり、しかも
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

# gh / glab の疎通確認（上限は ccnavi_h_max_time）。通れば 0
ccnavi_host_cli_ping() {
	ccnavi_cp_base="${ccnavi_h_tmp:-${TMPDIR:-/tmp}}/ccnavi-host-ping-$$"
	ccnavi_cp_rc=0
	ccnavi_run_timed "$ccnavi_h_max_time" /dev/null "$ccnavi_cp_base.out" "$ccnavi_cp_base.err" \
		"$ccnavi_h_cli" api --hostname "$ccnavi_h_host" "$1" || ccnavi_cp_rc=$?
	rm -f "$ccnavi_cp_base.out" "$ccnavi_cp_base.err" || :
	return "$ccnavi_cp_rc"
}

# PATH の絶対パスのエントリだけから実行可能な道具を探し、見つかれば絶対パスを標準出力に出す（無ければ終了コード 1）。
# `command -v` は使わない。macOS の sh は PATH の相対エントリ（例 bin）を絶対パスに直して返すため、
# リポジトリの中の gh などが選ばれてしまう。空のエントリ（cwd を指す）と相対のエントリは飛ばす。
# IFS は変えず、パラメータ展開（${変数%%:*}）で : 区切りに割る。
# " $ ` \ を含む候補は、式に埋め込むと危険なので飛ばして次を探す。
ccnavi_find_tool() {
	ccnavi_ft_rest="${PATH-}:"
	while [ -n "$ccnavi_ft_rest" ]; do
		ccnavi_ft_dir="${ccnavi_ft_rest%%:*}"
		ccnavi_ft_rest="${ccnavi_ft_rest#*:}"
		case "$ccnavi_ft_dir" in
		/*) ;;
		*) continue ;;
		esac
		for ccnavi_ft_cand in "$ccnavi_ft_dir/$1" "$ccnavi_ft_dir/$1.exe"; do
			if [ -f "$ccnavi_ft_cand" ] && [ -x "$ccnavi_ft_cand" ]; then
				case "$ccnavi_ft_cand" in
				*[\"\$\`\\]*) continue ;;
				esac
				printf '%s\n' "$ccnavi_ft_cand"
				return 0
			fi
		done
	done
	return 1
}

ccnavi_host_connect() {
	ccnavi_h_jq=$(ccnavi_find_tool jq || :)
	[ -n "$ccnavi_h_jq" ] || return 3
	# gh / glab は「入っている」だけでは足りない。そのホストで認証されていなければ
	# 通らない（手元に立てた GitLab に glab を繋いでいない、が普通にある）。
	# 1 度だけ疎通を試して、通らなければ curl とトークンに切り替える。
	ccnavi_h_transport=""
	ccnavi_h_curl=""
	ccnavi_h_token=""
	if [ "$ccnavi_h_kind" = github ]; then
		ccnavi_h_cli_name=gh
		ccnavi_h_cli=$(ccnavi_find_tool gh || :)
		if [ -n "$ccnavi_h_cli" ] && ccnavi_host_cli_ping "repos/$ccnavi_h_path"; then
			ccnavi_h_transport=gh
		fi
	else
		ccnavi_h_cli_name=glab
		ccnavi_h_cli=$(ccnavi_find_tool glab || :)
		if [ -n "$ccnavi_h_cli" ] && ccnavi_host_cli_ping "projects/$(ccnavi_host_encoded_path)"; then
			ccnavi_h_transport=glab
		fi
	fi
	[ -n "$ccnavi_h_transport" ] && return 0
	ccnavi_h_curl=$(ccnavi_find_tool curl || :)
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
		# 標準エラー（更新の知らせなど）は応答に混ぜない。失敗したときだけ本文と一緒に渡す。
		# 本文を送るときは Content-Type を付ける（付けないと、GitLab は HTTP 415 で拒む）
		# 上限（ccnavi_h_max_time）で切る。本文は一時ファイル経由で stdin に渡す
		ccnavi_ha_base="${ccnavi_h_tmp:-${TMPDIR:-/tmp}}/ccnavi-host-$$"
		ccnavi_ha_err="$ccnavi_ha_base.err"
		ccnavi_ha_rc=0
		if [ -n "$ccnavi_ha_body" ]; then
			printf '%s' "$ccnavi_ha_body" >"$ccnavi_ha_base.in"
			ccnavi_run_timed "$ccnavi_h_max_time" "$ccnavi_ha_base.in" "$ccnavi_ha_base.out" "$ccnavi_ha_err" \
				"$ccnavi_h_cli" api --hostname "$ccnavi_h_host" --method "$ccnavi_ha_method" --header 'Content-Type: application/json' --input - "$ccnavi_ha_rel" || ccnavi_ha_rc=$?
		else
			ccnavi_run_timed "$ccnavi_h_max_time" /dev/null "$ccnavi_ha_base.out" "$ccnavi_ha_err" \
				"$ccnavi_h_cli" api --hostname "$ccnavi_h_host" --method "$ccnavi_ha_method" "$ccnavi_ha_rel" || ccnavi_ha_rc=$?
		fi
		ccnavi_ha_out=$(cat "$ccnavi_ha_base.out" 2>/dev/null || :)
		if [ "$ccnavi_ha_rc" -ne 0 ]; then
			ccnavi_host_failed "$ccnavi_ha_out$(cat "$ccnavi_ha_err" 2>/dev/null)"
			rm -f "$ccnavi_ha_base.in" "$ccnavi_ha_base.out" "$ccnavi_ha_err" || :
			return 1
		fi
		rm -f "$ccnavi_ha_base.in" "$ccnavi_ha_base.out" "$ccnavi_ha_err" || :
		;;
	curl)
		if [ "$ccnavi_h_kind" = github ]; then
			ccnavi_ha_auth="Authorization: Bearer $ccnavi_h_token"
		else
			ccnavi_ha_auth="PRIVATE-TOKEN: $ccnavi_h_token"
		fi
		# curl 自身の --max-time に任せず、gh / glab と同じ見張りでも切る（1 秒遅らせ、curl の言葉を先に取る）
		ccnavi_ha_base="${ccnavi_h_tmp:-${TMPDIR:-/tmp}}/ccnavi-host-$$"
		ccnavi_ha_err="$ccnavi_ha_base.err"
		ccnavi_ha_rc=0
		if [ -n "$ccnavi_ha_body" ]; then
			printf '%s' "$ccnavi_ha_body" >"$ccnavi_ha_base.in"
			ccnavi_run_timed "$((ccnavi_h_max_time + 1))" "$ccnavi_ha_base.in" "$ccnavi_ha_base.out" "$ccnavi_ha_err" \
				"$ccnavi_h_curl" -fsS --connect-timeout 15 --max-time "$ccnavi_h_max_time" -X "$ccnavi_ha_method" -H "$ccnavi_ha_auth" -H 'Content-Type: application/json' --data-binary @- "$ccnavi_h_api_base/$ccnavi_ha_rel" || ccnavi_ha_rc=$?
		else
			ccnavi_run_timed "$((ccnavi_h_max_time + 1))" /dev/null "$ccnavi_ha_base.out" "$ccnavi_ha_err" \
				"$ccnavi_h_curl" -fsS --connect-timeout 15 --max-time "$ccnavi_h_max_time" -X "$ccnavi_ha_method" -H "$ccnavi_ha_auth" "$ccnavi_h_api_base/$ccnavi_ha_rel" || ccnavi_ha_rc=$?
		fi
		ccnavi_ha_out=$(cat "$ccnavi_ha_base.out" 2>/dev/null || :)
		if [ "$ccnavi_ha_rc" -ne 0 ]; then
			ccnavi_host_failed "$ccnavi_ha_out$(cat "$ccnavi_ha_err" 2>/dev/null)"
			rm -f "$ccnavi_ha_base.in" "$ccnavi_ha_base.out" "$ccnavi_ha_err" || :
			return 1
		fi
		rm -f "$ccnavi_ha_base.in" "$ccnavi_ha_base.out" "$ccnavi_ha_err" || :
		;;
	*) return 1 ;;
	esac
	printf '%s' "$ccnavi_ha_out"
}

ccnavi_host_failed() {
	[ -n "$ccnavi_h_on_fail" ] || return 0
	"$ccnavi_h_on_fail" "$ccnavi_ha_method" "$ccnavi_ha_rel" "$1"
}

# 失敗の返事（本文と標準エラー）から HTTP の番号を取り出す。取れなければ何も出さない。
# 本文の中の数字に引かれないよう、まず形の決まった書き方（curl の returned error・gh の (HTTP nnn)・glab の行）だけを全体から探し、
# 無ければ最後の行（gh / glab の標準エラー）だけを緩い式で読む。
ccnavi_host_fail_status() {
	ccnavi_hs_val=$(printf '%s' "$1" | sed -n -E -e 's/.*returned error: ([0-9]{3}).*/\1/p' -e 's/.*\(HTTP ([0-9]{3})\).*/\1/p' -e 's/^glab: HTTP ([0-9]{3})$/\1/p' -e 's/^[^0-9]*([0-9]{3}) (Not Found|Forbidden|Unauthorized|Gone).*/\1/p' | head -n 1)
	if [ -z "$ccnavi_hs_val" ]; then
		ccnavi_hs_val=$(printf '%s\n' "$1" | sed '/^$/d' | tail -n 1 | sed -n -E -e 's/.*HTTP ([0-9]{3}).*/\1/p' -e 's/^[^0-9]*([0-9]{3}) [A-Z][a-z]+.*/\1/p' | head -n 1)
	fi
	printf '%s' "$ccnavi_hs_val"
}

# GraphQL を使えない実行環境（Claude Code のクラウド）で出る 403 かを判定する。失敗の返事（本文と標準エラー）を渡し、そうなら 0 を返す。
# gh は本文を返すので、「GitHub GraphQL is not available」の文言で見分ける（別の理由の 403・401 は 1）。
# curl は -f が本文を捨て、403 の番号だけが残る。番号だけの 403 は、認証の失敗と見分けが付かないので 0 とする。
# 呼び手は REST の道（/ccr/）を試し、それも失敗したら元の GraphQL の失敗を言う（通常の GitHub では /ccr/ は 404 になるだけ）。
ccnavi_host_graphql_blocked() {
	case "$1" in
	*"GraphQL is not available"*) return 0 ;;
	esac
	[ "$(ccnavi_host_fail_status "$1")" = 403 ] || return 1
	case "$1" in
	*"returned error: 403"*) return 0 ;;
	esac
	return 1
}

# 失敗の返事の本文を、画面に出せる形（1 行・制御文字なし・先頭 200 文字・トークンは伏せる）にして出す。
# 文字数は jq で数える（cut -c は環境によってバイトで切り、日本語の途中で壊れる）。
ccnavi_host_fail_body() {
	printf '%s' "$1" | "$ccnavi_h_jq" -Rjs --arg t "${ccnavi_h_token:-}" '(if $t == "" then . else split($t) | join("***") end) | gsub("[\u0000-\u001f\u007f]+"; " ") | .[0:200]'
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
		ccnavi_hg_n=$(printf '%s' "$ccnavi_hg_chunk" | "$ccnavi_h_jq" 'if type == "array" then length else error("not an array") end' 2>/dev/null) || {
			ccnavi_host_failed "$ccnavi_hg_chunk"
			return 1
		}
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
	# ccnavi_host_probe が読んでいれば、その応答を使う（問い合わせを繰り返さない）
	if [ -n "${ccnavi_h_project_json:-}" ]; then
		ccnavi_hi_out="$ccnavi_h_project_json"
	else
		ccnavi_hi_out=$(ccnavi_host_api GET "projects/$(ccnavi_host_encoded_path)") || return 1
	fi
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
	ccnavi_ga_cli=$(ccnavi_find_tool "$ccnavi_ga_name" || :)
	# ccnavi_find_tool は、式の中で特別な意味を持つ文字（" $ ` \）を含まない絶対パスだけを返す。相対パスだと、リポジトリの中の gh が helper として動いてしまう。
	[ -n "$ccnavi_ga_cli" ] || { ccnavi_git_auth_reset; return 0; }
	# 値は変数のまま eval に渡す（ホスト名や道具のパスを式に埋めない。origin の文字列がコマンドとして走らないように）。
	ccnavi_ga_key="credential.$ccnavi_ga_scheme://$ccnavi_ga_host.helper"
	ccnavi_ga_val="!\"$ccnavi_ga_cli\" auth git-credential"
	eval "GIT_CONFIG_KEY_$ccnavi_ga_base=\$ccnavi_ga_key GIT_CONFIG_VALUE_$ccnavi_ga_base=\$ccnavi_ga_val"
	eval "export GIT_CONFIG_KEY_$ccnavi_ga_base GIT_CONFIG_VALUE_$ccnavi_ga_base"
	GIT_CONFIG_COUNT=$((ccnavi_ga_base + 1))
	export GIT_CONFIG_COUNT
	return 0
}

# ---- 事前確認（チケット作業の入口と、子チケットの作業から始める操作が使う）
#
# ホストの API と git のリモートが使える状態かを確かめる。ここの関数はユーザ向けの文面を出さず、
# 結果・勧める対処と、実行ファイルが組み立てた行と `次:` の行を変数で返す。出すかどうかは呼ぶ側が決める。

# ccnavi_host_connect の後に呼ぶ。経路によらずプロジェクトを 1 回読む（GitHub は repos/<パス>、GitLab は projects/<パス>）。
#   ccnavi_h_project_json  応答の JSON
#   ccnavi_h_probe         ok / auth（401・403）/ notfound（404）/ network（通信の失敗・時間切れ・5xx）/ badjson
ccnavi_h_project_json=""
ccnavi_h_probe=""
ccnavi_hq_text=""
ccnavi_hq_prev=""
ccnavi_probe_failed() {
	ccnavi_hq_text="$3"
	[ -z "$ccnavi_hq_prev" ] || "$ccnavi_hq_prev" "$@"
}
ccnavi_host_probe() {
	ccnavi_h_project_json=""
	ccnavi_h_probe=""
	ccnavi_hq_text=""
	if [ "$ccnavi_h_kind" = github ]; then
		ccnavi_hq_rel="repos/$ccnavi_h_path"
	else
		ccnavi_hq_rel="projects/$(ccnavi_host_encoded_path)"
	fi
	ccnavi_hq_file="${ccnavi_h_tmp:-${TMPDIR:-/tmp}}/ccnavi-probe-$$"
	ccnavi_hq_prev="$ccnavi_h_on_fail"
	ccnavi_h_on_fail=ccnavi_probe_failed
	ccnavi_hq_rc=0
	ccnavi_host_api GET "$ccnavi_hq_rel" >"$ccnavi_hq_file" || ccnavi_hq_rc=$?
	ccnavi_h_on_fail="$ccnavi_hq_prev"
	if [ "$ccnavi_hq_rc" -ne 0 ]; then
		rm -f "$ccnavi_hq_file" || :
		case "$(ccnavi_host_fail_status "$ccnavi_hq_text")" in
		401 | 403) ccnavi_h_probe=auth ;;
		404) ccnavi_h_probe=notfound ;;
		*) ccnavi_h_probe=network ;;
		esac
		return 0
	fi
	ccnavi_h_project_json=$(cat "$ccnavi_hq_file")
	rm -f "$ccnavi_hq_file" || :
	if printf '%s' "$ccnavi_h_project_json" | "$ccnavi_h_jq" -e 'type == "object"' >/dev/null 2>&1; then
		ccnavi_h_probe=ok
	else
		ccnavi_h_project_json=""
		ccnavi_h_probe=badjson
	fi
	return 0
}

# ccnavi_h_project_json を受けて、書き込みの権限を判定する（判定は実行ファイルの `ccnavi preflight access`。sh は応答を取る）。
#   戻り値  0 足りる / 1 足りない / 2 判定できない。理由は ccnavi_h_access_why
# 「足りる」は「リポジトリのロール上は書き込める」の意味に限る。トークンの scope・ブランチの保護・
# API と git が別の資格情報で動いていることは確かめていない。
ccnavi_h_access_why=""
ccnavi_host_access() {
	ccnavi_h_access_why=""
	ccnavi_hc_root="${root:-$(ccnavi_workspace 2>/dev/null || printf .)}"
	ccnavi_hc_dir="${ccnavi_h_tmp:-${TMPDIR:-/tmp}}"
	ccnavi_hc_project="$ccnavi_hc_dir/ccnavi-access-project-$$"
	ccnavi_hc_file="$ccnavi_hc_dir/ccnavi-access-$$"
	printf '%s' "$ccnavi_h_project_json" >"$ccnavi_hc_project"
	ccnavi_hc_prev="$ccnavi_h_on_fail"
	ccnavi_h_on_fail=""
	set -- "$ccnavi_h_kind" "$ccnavi_hc_project"
	# GitLab: 自分の id → プロジェクトの member の access_level。読めなければ、プロジェクトの permissions で判定する
	if [ "$ccnavi_h_kind" = gitlab ] && ccnavi_host_api GET user >"$ccnavi_hc_file" 2>/dev/null; then
		ccnavi_hc_uid=$(ccnavi_ask "$ccnavi_hc_root" preflight uid "$ccnavi_hc_file" | sed -n 's/^uid //p')
		if [ -n "$ccnavi_hc_uid" ] &&
			ccnavi_host_api GET "projects/$(ccnavi_host_encoded_path)/members/all/$ccnavi_hc_uid" >"$ccnavi_hc_file" 2>/dev/null; then
			set -- "$@" "$ccnavi_hc_file"
		fi
	fi
	ccnavi_h_on_fail="$ccnavi_hc_prev"
	ccnavi_hc_out=$(ccnavi_ask "$ccnavi_hc_root" preflight access "$@") || ccnavi_hc_out=""
	rm -f "$ccnavi_hc_project" "$ccnavi_hc_file" || :
	ccnavi_h_access_why=$(printf '%s\n' "$ccnavi_hc_out" | sed -n 's/^why //p')
	case "$(printf '%s\n' "$ccnavi_hc_out" | sed -n 's/^access //p')" in
	ok) return 0 ;;
	ng) return 1 ;;
	esac
	[ -n "$ccnavi_h_access_why" ] || ccnavi_h_access_why="実行ファイルに確かめられない"
	return 2
}

# 事前確認（ccnavi-start.sh --check と ccnavi-ticket.sh が共有する）。手元で決まる項目（道具・origin・統合先・作成者）と、
# 項目ごとの結果・勧める対処・行と次の一手は、実行ファイル（`ccnavi preflight plan`・`check`）が決める。sh が行うのは、
# ホストへの接続・プロジェクトの取得・権限の問い合わせ・ls-remote（ネットワーク）と、時間の枠だけ。
#
#   ccnavi_preflight <リポジトリの top>
#     ccnavi_pf_result       ok / ng / unverified
#     ccnavi_pf_<項目>       項目は tools origin integration api access git ident（ok / ng / unverified / skipped）
#     ccnavi_pf_fix          勧める対処の種類（無ければ空）
#     ccnavi_pf_auth_source  認証の出どころの種類（cli / token / none。値は入れない）
#     ccnavi_pf_access_unknown  権限を判定できなかった（ok として続ける）なら yes
#     ccnavi_pf_continue     確かめきれなくても続けてよい（ホストの API だけを確かめられず、git は通る）なら yes
# 1 回の問い合わせは 15 秒、git の確認は 30 秒（どちらも CCNAVI_PREFLIGHT_TIMEOUT で変える。テスト用）、全体は 60 秒まで。
# 全体が 60 秒を過ぎたら残りは skipped。キャッシュはしない。毎回確かめる。
ccnavi_pf_late() {
	[ $(($(date +%s) - ccnavi_pf_t0)) -lt "${CCNAVI_PREFLIGHT_TOTAL:-60}" ] && return 1
	return 0
}

ccnavi_preflight() {
	ccnavi_pf_top="$1"
	ccnavi_pf_root="${root:-$(ccnavi_workspace 2>/dev/null || printf .)}"
	ccnavi_pf_each="${CCNAVI_PREFLIGHT_TIMEOUT:-15}"
	ccnavi_pf_git_each="${CCNAVI_PREFLIGHT_TIMEOUT:-30}"
	ccnavi_pf_t0=$(date +%s)
	ccnavi_pf_dir="${TMPDIR:-/tmp}/ccnavi-pf-$$"
	mkdir -p "$ccnavi_pf_dir" 2>/dev/null || ccnavi_pf_dir="${TMPDIR:-/tmp}"
	# origin の URL をホストのものとして読めるか（接続に要るので sh が読む）
	ccnavi_pf_parse=ng
	ccnavi_pf_url=$(git -C "$ccnavi_pf_top" remote get-url origin 2>/dev/null || :)
	if [ -n "$ccnavi_pf_url" ] && ccnavi_host_parse "$ccnavi_pf_url"; then
		ccnavi_pf_parse=ok
	fi
	set -- "top=$ccnavi_pf_top" "parse=$ccnavi_pf_parse" "parse-why=${ccnavi_h_parse_why:-}" \
		"masked=$(ccnavi_mask_url "$ccnavi_pf_url")" "kind=${ccnavi_h_kind:-}" "host=${ccnavi_h_host:-}" "path=${ccnavi_h_path:-}"
	ccnavi_pf_plan=$(ccnavi_ask "$ccnavi_pf_root" preflight plan "$@") || ccnavi_pf_plan=""
	# ホストの API（接続とプロジェクトの取得）と権限
	if [ "$(printf '%s\n' "$ccnavi_pf_plan" | sed -n 's/^api //p')" = yes ] && ! ccnavi_pf_late; then
		ccnavi_h_tmp="$ccnavi_pf_dir"
		ccnavi_h_on_fail=""
		ccnavi_h_max_time="$ccnavi_pf_each"
		ccnavi_pf_crc=0
		ccnavi_host_connect || ccnavi_pf_crc=$?
		set -- "$@" "connect=$ccnavi_pf_crc" "transport=${ccnavi_h_transport:-}" "cli=${ccnavi_h_cli_name:-}" \
			"token=${ccnavi_h_token_name:-}"
		if [ "$ccnavi_pf_crc" -eq 0 ]; then
			ccnavi_host_probe
			set -- "$@" "probe=$ccnavi_h_probe"
			if [ "$ccnavi_h_probe" = ok ] && ! ccnavi_pf_late; then
				ccnavi_pf_arc=0
				ccnavi_host_access || ccnavi_pf_arc=$?
				case "$ccnavi_pf_arc" in
				0) set -- "$@" access=ok ;;
				1) set -- "$@" access=ng "access-why=$ccnavi_h_access_why" ;;
				*) set -- "$@" access=unknown "access-why=$ccnavi_h_access_why" ;;
				esac
			fi
		fi
	fi
	# リモートの git（API の結果によらず行う）
	if [ "$(printf '%s\n' "$ccnavi_pf_plan" | sed -n 's/^git //p')" = yes ] && ! ccnavi_pf_late; then
		ccnavi_pf_err="$ccnavi_pf_dir/git-err"
		ccnavi_git_auth "$ccnavi_pf_top"
		ccnavi_pf_grc=0
		ccnavi_git_timed "$ccnavi_pf_git_each" "$ccnavi_pf_err" "$ccnavi_pf_top" ls-remote --heads origin >/dev/null || ccnavi_pf_grc=$?
		set -- "$@" "git=$ccnavi_pf_grc" "git-err=$(tr -d '\r' <"$ccnavi_pf_err" 2>/dev/null | head -n 3 | tr '\n' ' ')"
	fi
	[ "$ccnavi_pf_dir" = "${TMPDIR:-/tmp}" ] || rm -rf "$ccnavi_pf_dir" || :
	ccnavi_pf_out=$(ccnavi_ask "$ccnavi_pf_root" preflight check "$@") || ccnavi_pf_out=""
	ccnavi_pf_result=$(printf '%s\n' "$ccnavi_pf_out" | sed -n 's/^result //p')
	[ -n "$ccnavi_pf_result" ] || ccnavi_pf_result=unverified
	for ccnavi_pf_k in tools origin integration api access git ident; do
		ccnavi_pf_v=$(printf '%s\n' "$ccnavi_pf_out" | sed -n "s/^item $ccnavi_pf_k //p")
		eval "ccnavi_pf_$ccnavi_pf_k=\${ccnavi_pf_v:-skipped}"
	done
	ccnavi_pf_fix=$(printf '%s\n' "$ccnavi_pf_out" | sed -n 's/^fix //p')
	ccnavi_pf_auth_source=$(printf '%s\n' "$ccnavi_pf_out" | sed -n 's/^auth //p')
	ccnavi_pf_access_unknown=$(printf '%s\n' "$ccnavi_pf_out" | sed -n 's/^access-unknown //p')
	ccnavi_pf_continue=$(printf '%s\n' "$ccnavi_pf_out" | sed -n 's/^continue //p')
	return 0
}

# ---- 事前確認の文面（ccnavi-start.sh --check と ccnavi-ticket.sh が共有する。変数に入れるだけで、何も出さない）
#
#   ccnavi_pf_line <項目>  1 行の文面を ccnavi_pf_line_text に入れる（`<項目>: OK|NG|未確認|省略|注意 (<理由>)`）
#   ccnavi_pf_next         ccnavi_pf_fix から `次:` の行を ccnavi_pf_next_text に入れる（無ければ空）。文は実行ファイル
#                          （`ccnavi preflight next`）が組み立てる
ccnavi_pf_line() {
	ccnavi_pf_line_text=$(printf '%s\n' "${ccnavi_pf_out:-}" | sed -n "s/^line $1 //p")
	[ -n "$ccnavi_pf_line_text" ] || ccnavi_pf_line_text="$1: 省略"
}

ccnavi_pf_next() {
	ccnavi_pf_next_text=$(ccnavi_ask "${root:-.}" preflight next "fix=${ccnavi_pf_fix:-}" "top=${ccnavi_pf_top:-.}" \
		"kind=${ccnavi_h_kind:-}" "host=${ccnavi_h_host:-}" "path=${ccnavi_h_path:-}" | sed -n 's/^next //p')
}
