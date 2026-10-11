# ccnavi-common-log.sh  ccnavi-common.sh が読む部品。単体では読まない（読み方は ccnavi-common.sh の冒頭）。
#
# 診断ログ（log_debug / log_info / log_warn / log_error）。

# ---- 診断ログ（docs/claude/logging.md）
#
#   log_info <本文の語>... [-- <キー>=<値>...]
#
# 本文の語はスペースでつなぐ。`--` の後ろは 1 つずつ `キー=値` として logfmt で並べる。
# 出る行の形は次のとおり（Python の src/ccnavi/records/diaglog.py、拡張の src/log.ts と同じ）。
#
#   2026-09-27T10:15:03+09:00 INFO  ccnavi-git[4242] 拒否した sub=push reason=unapproved
#
# 置き場はワークスペースルートの `logs/diag/<出どころ>.log`。ルートは呼ぶ側が
# ccnavi_log_root に入れておけばそれを使い、空なら ccnavi_workspace で探す。出どころは
# `$0` の名前から拡張子を除いたもので、CCNAVI_LOG_NAME で上書きできる。出どころに
# `[A-Za-z0-9_-]` 以外の文字があれば書かない（パスの区切りや `..` を名前に入れさせない）。
#
# シンボリックリンクはたどらない。 `logs`・`logs/diag`・書き込み先のファイルのどれかが
# シンボリックリンクなら書かずに捨てる。リンク先へ追記すると、置き場の外のファイル（判定の記録など）を書き換えてしまう。
# ファイルを新しく作るときは umask 077 のサブシェルで作り、持ち主だけが読める 0600 にする。
#
# 標準出力と標準エラーには何も出さず、何があっても 0 を返す。 書けない（置き場が
# 作れない・権限・容量）ときは何も出さずに捨てる。`set -eu` の下で呼んでも、呼ぶ側を止めない。
# 契約として決まっている出力（reject / fail の標準エラー、ok / fail の 1 行目）とは分けてあり、
# そちらは変えない。
#
# 本文と値の中の、URL と scp 形式に埋まった資格情報を `***` に伏せる（ccnavi_log_mask）。
#
# 出さないレベルでは、date を起動せず、文字列も組み立てない（ccnavi_log_on で先に見る）。
# 1 行を書くときに起動する外部コマンドは date と、置き場が無いときの mkdir だけ。ほかに、
# 新しくファイルを作るときの umask のサブシェルと、ccnavi_log_root が空のときに 1 度だけ
# 走る ccnavi_workspace（dirname などを起動する）がある。残りはシェルの展開で済ませる。

ccnavi_log_min=""
ccnavi_log_file=""
ccnavi_log_name=""
# 呼ぶ側が求めたワークスペースルート。入れておくと ccnavi_workspace で探し直さない。
ccnavi_log_root=""
# CR は printf でしか作れない。読み込むときに 1 度だけ作る。
ccnavi_log_cr=$(printf '\r')
# 語の切れ目（空白・タブ・LF・CR）。資格情報を伏せるときに語を切り出すのに使う。
ccnavi_log_space=" 	
$ccnavi_log_cr"

# レベルの閾値。CCNAVI_LOG_LEVEL を 1 度だけ読む。読めない値と空は INFO。
ccnavi_log_on() {
	if [ -z "$ccnavi_log_min" ]; then
		case "${CCNAVI_LOG_LEVEL:-}" in
		[Dd][Ee][Bb][Uu][Gg]) ccnavi_log_min=1 ;;
		[Ww][Aa][Rr][Nn]) ccnavi_log_min=3 ;;
		[Ee][Rr][Rr][Oo][Rr]) ccnavi_log_min=4 ;;
		*) ccnavi_log_min=2 ;;
		esac
	fi
	[ "$1" -ge "$ccnavi_log_min" ]
}

log_debug() {
	ccnavi_log_on 1 || return 0
	ccnavi_log_emit 'DEBUG' "$@" || :
	return 0
}
log_info() {
	ccnavi_log_on 2 || return 0
	ccnavi_log_emit 'INFO ' "$@" || :
	return 0
}
log_warn() {
	ccnavi_log_on 3 || return 0
	ccnavi_log_emit 'WARN ' "$@" || :
	return 0
}
log_error() {
	ccnavi_log_on 4 || return 0
	ccnavi_log_emit 'ERROR' "$@" || :
	return 0
}

# 書き込み先。1 度求めたら覚える。出どころが使えない字を含むか、ワークスペースルートが
# 見つからなければ 1 を返す（捨てる）。
ccnavi_log_target() {
	[ -z "$ccnavi_log_file" ] || return 0
	ccnavi_lt_name="${CCNAVI_LOG_NAME:-}"
	if [ -z "$ccnavi_lt_name" ]; then
		ccnavi_lt_name="${0##*/}"
		ccnavi_lt_name="${ccnavi_lt_name##*\\}"
		ccnavi_lt_name="${ccnavi_lt_name%.*}"
	fi
	[ -n "$ccnavi_lt_name" ] || ccnavi_lt_name=sh
	case "$ccnavi_lt_name" in
	*[!A-Za-z0-9_-]*) return 1 ;;
	esac
	ccnavi_lt_ws="$ccnavi_log_root"
	if [ -z "$ccnavi_lt_ws" ]; then
		ccnavi_lt_ws=$(ccnavi_workspace 2>/dev/null) || return 1
	fi
	[ -n "$ccnavi_lt_ws" ] || return 1
	ccnavi_log_name="$ccnavi_lt_name"
	ccnavi_log_file="$ccnavi_lt_ws/logs/diag/$ccnavi_lt_name.log"
}

# 1 行を組み立てて足す。$1 はレベル（5 字）、残りは log_* の引数。
ccnavi_log_emit() {
	ccnavi_le_level="$1"
	shift
	ccnavi_log_target || return 0
	ccnavi_le_msg=""
	ccnavi_le_fields=""
	ccnavi_le_in_fields=""
	for ccnavi_le_arg in ${1+"$@"}; do
		if [ -z "$ccnavi_le_in_fields" ]; then
			if [ "$ccnavi_le_arg" = "--" ]; then
				ccnavi_le_in_fields=1
			elif [ -z "$ccnavi_le_msg" ]; then
				ccnavi_le_msg="$ccnavi_le_arg"
			else
				ccnavi_le_msg="$ccnavi_le_msg $ccnavi_le_arg"
			fi
			continue
		fi
		case "$ccnavi_le_arg" in
		*=*)
			ccnavi_le_key="${ccnavi_le_arg%%=*}"
			ccnavi_le_val="${ccnavi_le_arg#*=}"
			;;
		*)
			ccnavi_le_key="$ccnavi_le_arg"
			ccnavi_le_val=""
			;;
		esac
		ccnavi_log_value "$ccnavi_le_val"
		ccnavi_le_fields="$ccnavi_le_fields $ccnavi_le_key=$ccnavi_log_out"
	done
	ccnavi_log_mask "$ccnavi_le_msg"
	ccnavi_log_fold "$ccnavi_log_out"
	ccnavi_le_msg="$ccnavi_log_out"

	# 時刻。date の %z は +0900 なので、+09:00 に直す。
	ccnavi_le_now=$(date '+%Y-%m-%dT%H:%M:%S%z' 2>/dev/null) || ccnavi_le_now=""
	case "$ccnavi_le_now" in
	*[+-][0-9][0-9][0-9][0-9])
		ccnavi_le_zone="${ccnavi_le_now#"${ccnavi_le_now%?????}"}"
		ccnavi_le_now="${ccnavi_le_now%?????}${ccnavi_le_zone%??}:${ccnavi_le_zone#???}"
		;;
	esac

	ccnavi_le_line="$ccnavi_le_now $ccnavi_le_level ${ccnavi_log_name}[$$] $ccnavi_le_msg$ccnavi_le_fields"
	ccnavi_le_dir="${ccnavi_log_file%/*}"
	# シンボリックリンクはたどらない。logs・logs/diag・書き込み先のどれかがリンクなら捨てる。
	if [ -L "${ccnavi_le_dir%/*}" ] || [ -L "$ccnavi_le_dir" ] || [ -L "$ccnavi_log_file" ]; then
		return 0
	fi
	if [ ! -d "$ccnavi_le_dir" ]; then
		mkdir -p "$ccnavi_le_dir" >/dev/null 2>&1 || return 0
	fi
	# `>>` は O_APPEND で開く。printf は 1 行を 1 度の write で出す。
	# 無いファイルは umask 077 のサブシェルで作り、0600 にする。在ればサブシェルを起動しない。
	if [ -e "$ccnavi_log_file" ]; then
		{ printf '%s\n' "$ccnavi_le_line" >>"$ccnavi_log_file"; } >/dev/null 2>&1 || :
	else
		(
			umask 077
			printf '%s\n' "$ccnavi_le_line" >>"$ccnavi_log_file"
		) >/dev/null 2>&1 || :
	fi
	return 0
}

# 文字列の中の $2 を、すべて $3 に置き換える。結果は ccnavi_log_out に入る。
# $2 が空なら何も置き換えない（空を探すと、いつまでも当たり続けて抜けない）。
ccnavi_log_replace() {
	ccnavi_log_out="$1"
	[ -n "$2" ] || return 0
	ccnavi_lr_rest="$1"
	ccnavi_log_out=""
	while :; do
		case "$ccnavi_lr_rest" in
		*"$2"*)
			ccnavi_log_out="$ccnavi_log_out${ccnavi_lr_rest%%"$2"*}$3"
			ccnavi_lr_rest="${ccnavi_lr_rest#*"$2"}"
			;;
		*)
			ccnavi_log_out="$ccnavi_log_out$ccnavi_lr_rest"
			return 0
			;;
		esac
	done
}

# 改行（CR LF・CR・LF）を `\n` の 2 字に置き換える。結果は ccnavi_log_out。
ccnavi_log_fold() {
	ccnavi_log_out="$1"
	ccnavi_lf_nl='
'
	case "$ccnavi_log_out" in
	*"$ccnavi_lf_nl"* | *"$ccnavi_log_cr"*) ;;
	*) return 0 ;;
	esac
	ccnavi_log_replace "$ccnavi_log_out" "$ccnavi_log_cr$ccnavi_lf_nl" "$ccnavi_lf_nl"
	ccnavi_log_replace "$ccnavi_log_out" "$ccnavi_log_cr" "$ccnavi_lf_nl"
	ccnavi_log_replace "$ccnavi_log_out" "$ccnavi_lf_nl" '\n'
}

# logfmt の値。資格情報を伏せ、空白・タブ・`"`・`=`・改行を含めば
# `"` で囲んで `\` と `"` をエスケープする。結果は ccnavi_log_out。
ccnavi_log_value() {
	ccnavi_log_mask "$1"
	ccnavi_lv_v="$ccnavi_log_out"
	ccnavi_lv_tab='	'
	ccnavi_lv_nl='
'
	case "$ccnavi_lv_v" in
	*' '* | *"$ccnavi_lv_tab"* | *'"'* | *'='* | *"$ccnavi_lv_nl"* | *"$ccnavi_log_cr"*) ;;
	*) return 0 ;;
	esac
	ccnavi_log_replace "$ccnavi_lv_v" '\' '\\'
	ccnavi_log_replace "$ccnavi_log_out" '"' '\"'
	ccnavi_log_fold "$ccnavi_log_out"
	ccnavi_log_out="\"$ccnavi_log_out\""
}

# 埋まった資格情報を `***` に伏せる。Python の diaglog.mask_userinfo と拡張の maskUserinfo と
# 同じ規則で伏せ、3 つの結果は 1 文字も違わない（tests/sh/test_diaglog_sh.py）。結果は ccnavi_log_out。
#
# 空白・タブ・LF・CR で切った語ごとに見る。
#   - `://` を含む語: `://` の後ろから次の `/` までを authority とし、`@` があれば最後の `@` より
#     前を `***` にする（`https://user:tok@host/x` → `https://***@host/x`）
#   - `://` を含まない語: 最初の `/` より前に `@` があり、最後の `@` より前に `:` があれば、そこを
#     `***` にする（scp 形式 `user:tok@host:path` → `***@host:path`）。`git@host:path` は伏せない
# `@` の無い文字列は語に切らずにそのまま返す（ほとんどの行はこれで済む）。
# ユーザ向けの ccnavi_mask_url（`<伏せた>@host`）とは表記が違う。そちらは契約として決まっている出力なので変えない。
ccnavi_log_mask() {
	ccnavi_log_out="$1"
	case "$1" in
	*@*) ;;
	*) return 0 ;;
	esac
	ccnavi_lm_rest="$1"
	ccnavi_log_out=""
	while [ -n "$ccnavi_lm_rest" ]; do
		ccnavi_lm_word="${ccnavi_lm_rest%%[$ccnavi_log_space]*}"
		if [ -z "$ccnavi_lm_word" ]; then
			# 頭が切れ目の字（1 バイト）。そのまま写す。
			ccnavi_lm_tail="${ccnavi_lm_rest#?}"
			ccnavi_log_out="$ccnavi_log_out${ccnavi_lm_rest%"$ccnavi_lm_tail"}"
			ccnavi_lm_rest="$ccnavi_lm_tail"
			continue
		fi
		ccnavi_lm_rest="${ccnavi_lm_rest#"$ccnavi_lm_word"}"
		ccnavi_log_mask_word "$ccnavi_lm_word"
		ccnavi_log_out="$ccnavi_log_out$ccnavi_lw_out"
	done
}

# 語 1 つを伏せる（ccnavi_log_mask の読み）。結果は ccnavi_lw_out。
ccnavi_log_mask_word() {
	ccnavi_lw_out="$1"
	case "$1" in
	*@*) ;;
	*) return 0 ;;
	esac
	case "$1" in
	*://*)
		ccnavi_lw_rest="$1"
		ccnavi_lw_out=""
		while :; do
			case "$ccnavi_lw_rest" in
			*://*) ;;
			*) break ;;
			esac
			ccnavi_lw_out="$ccnavi_lw_out${ccnavi_lw_rest%%://*}://"
			ccnavi_lw_rest="${ccnavi_lw_rest#*://}"
			ccnavi_lw_auth="${ccnavi_lw_rest%%/*}"
			case "$ccnavi_lw_auth" in
			*@*)
				ccnavi_lw_out="$ccnavi_lw_out***@${ccnavi_lw_auth##*@}"
				ccnavi_lw_rest="${ccnavi_lw_rest#"$ccnavi_lw_auth"}"
				;;
			esac
		done
		ccnavi_lw_out="$ccnavi_lw_out$ccnavi_lw_rest"
		;;
	*)
		ccnavi_lw_auth="${1%%/*}"
		case "$ccnavi_lw_auth" in
		*@*) ;;
		*) return 0 ;;
		esac
		ccnavi_lw_user="${ccnavi_lw_auth%@*}"
		case "$ccnavi_lw_user" in
		*:*) ccnavi_lw_out="***@${1#"$ccnavi_lw_user"@}" ;;
		esac
		;;
	esac
}
