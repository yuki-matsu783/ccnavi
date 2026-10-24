#!/bin/sh
# ccnavi-doctor は、ccnavi を入れる前に、この機械の状況を調べて 1 項目 1 行で報告する。
# 何も書き換えない。通信するのは --online のときだけ。
#
#   sh <配布元>/scripts/ccnavi-doctor.sh [--mode prebuilt|build] [--with-extensions]
#        [--workspace <ws>] [--from <配布元>] [--online] [--no-next]
#
#   --mode <prebuilt|build>  既定は prebuilt（配布元の dist/ にある組み立て済みを入れる）。
#                            build は、ソースで実行ファイルを組み立てて入れる
#   --with-extensions        build のとき、拡張機能（VS Code・Chrome）も組む。node と pnpm を調べる
#   --workspace <ws>         入れる先。既定は現在の作業ディレクトリ
#   --from <配布元>          展開済みのフォルダ（build ではソース）。zip は受けない。
#                            既定はこのスクリプトの 1 つ上
#   --online                 origin と API に届くかを実際に確かめる（通信する）。git を打つので、
#                            そのリポジトリの git の設定（core.sshCommand など）が実行される。
#                            信頼できるリポジトリでだけ指定する。環境変数 GITLAB_TOKEN は、
#                            origin のホストの API にだけ送られる（ccnavi の host 部品と同じ）
#   --no-next                末尾の「案内:」「次:」を出さない（ccnavi-install.sh が呼ぶときに使う）
#
# 出力は `check: <項目> <ok|warn|todo|ng> <コード> <詳細>`。
#   ng   入れられない（ccnavi-install.sh は止まる）
#   todo 入れられるが、使い始める前に解決が要る。代わりの道で解決済みなら todo にしない
#   warn 知らせるだけ
# 判定をすべて終えてから出力する（glab の段階が remote・host-api の結果で変わるため）。
# 項目に特別なコードが無い ok は、コードを ok にする（`check: git ok ok <版>`）。
#
# 終了コード: 0 ng が無い / 1 ng がある / 2 引数の誤り
#
# ccnavi の部品の読み込み: <配布元>/.ccnavi/scripts/ccnavi-common-host.sh を副シェルの中で直接 `.` で
# 読む。共通の入口 ccnavi-common.sh は使わない。入口は部品を `dirname "$0"` で探すので、
# 別のディレクトリのこのスクリプトから読むと部品が見つからず 1 で終わる。host 部品を単体で読むのは
# 「部品は単体では読まない」という既存の決まりの外の使い方になる。host 部品が他の部品の関数に頼るように
# なったら、tests/sh/test_doctor_sh.py の HostPartLoadTest が落ちて気づける。
#
# 通信する確認（git・glab・curl）はすべて時間の上限を付ける。時間を測るのは doctor_timed の 1 つ。
# ccnavi_git_timed（.ccnavi/scripts/ccnavi-common-state.sh）のやり方に倣い、さらに、時間切れと
# INT・TERM のときは ssh や資格情報の取り手などの子孫まで止める。POSIX sh には子孫をたどる手段が無いので、
# ps の出力（pid と ppid）から木を作って kill する。ps が使えない環境では、直接の子だけを止める。

set -eu

DOCTOR_GIT_LIMIT=${CCNAVI_DOCTOR_GIT_LIMIT:-30}
DOCTOR_CURL_LIMIT=${CCNAVI_DOCTOR_CURL_LIMIT:-20}
DOCTOR_GIT_MIN="2.31"

doctor_tmp=""
doctor_seq=0
doctor_pid=""
doctor_dog=""
doctor_os=""
doctor_host_sh=""

usage() {
	printf '%s\n' \
		'sh <配布元>/scripts/ccnavi-doctor.sh [--mode prebuilt|build] [--with-extensions]' \
		'  [--workspace <ws>] [--from <配布元>] [--online] [--no-next]' \
		'' \
		'  --mode <prebuilt|build>  既定は prebuilt' \
		'  --with-extensions        build のとき、拡張機能も組む（node と pnpm を調べる）' \
		'  --workspace <ws>         入れる先。既定は現在の作業ディレクトリ' \
		'  --from <配布元>          展開済みのフォルダ（build ではソース）。zip は受けない' \
		'  --online                 origin と API に届くかを確かめる（通信する）' \
		'  --no-next                末尾の案内と次を出さない' \
		'' \
		'何も書き換えない。出力は check: <項目> <ok|warn|todo|ng> <コード> <詳細>。' \
		'終了コード: 0 ng が無い / 1 ng がある / 2 引数の誤り'
}

die() {
	printf 'ccnavi-doctor: %s\n' "$1" >&2
	exit "${2:-2}"
}

# ---- 小さな部品

# 改行・制御文字を空白にし、前後の空白を削り、200 字で切る。
doctor_clean() {
	printf '%s' "$1" | tr '\r\n\t' '   ' | tr -d '\000-\010\013\014\016-\037' |
		sed -e 's/^ *//' -e 's/ *$//' | cut -c 1-200
}

# URL に埋まった資格情報とトークンを伏せる。.ccnavi/scripts/ccnavi-common.sh の ccnavi_mask_url と
# 同じ規則（`scheme://` の後ろから最後の `@` まで。scheme の無い `user:pass@host` も）。違いは、
# ここでは文の途中の URL にも適用することと、glab のトークンの形（glpat-…）も伏せること。
doctor_mask() {
	# 最後の awk は、環境の GITLAB_TOKEN・GITHUB_TOKEN の値そのものを伏せる。
	printf '%s' "$1" | sed -E \
		-e 's#([A-Za-z][A-Za-z0-9+.-]*://)[^/ ]*@#\1<伏せた>@#g' \
		-e 's#(^|[ (])[^/: @]*:[^/@ ]*@#\1<伏せた>@#g' \
		-e 's#gl[a-z]{1,6}-[A-Za-z0-9_-]{8,}#<伏せた>#g' \
		-e 's#gh[pousr]_[A-Za-z0-9]{8,}#<伏せた>#g' \
		-e 's#github_pat_[A-Za-z0-9_]+#<伏せた>#g' \
		-e 's#((private|access)_token=)[^& ]+#\1<伏せた>#g' |
		awk '{
			for (k = 1; k <= 2; k++) {
				t = ENVIRON[(k == 1) ? "GITLAB_TOKEN" : "GITHUB_TOKEN"]
				while (length(t) >= 6 && (i = index($0, t)) > 0)
					$0 = substr($0, 1, i - 1) "<伏せた>" substr($0, i + length(t))
			}
			print
		}'
}

# 1 行目だけを、伏せて、きれいにして出す。
doctor_first_line() {
	doctor_clean "$(doctor_mask "$(printf '%s\n' "$1" | sed -n '/[^[:space:]]/{p;q;}')")"
}

# 引数を単引用符で囲んで出す（ccnavi_sh_quote と同じ形）。案内の文に、そのまま打てるパスを載せる。
doctor_quote() {
	dq_rest=$1
	dq_out=""
	while :; do
		case "$dq_rest" in
		*\'*)
			dq_out="${dq_out}${dq_rest%%\'*}'\"'\"'"
			dq_rest=${dq_rest#*\'}
			;;
		*) break ;;
		esac
	done
	printf "'%s%s'" "$dq_out" "$dq_rest"
}

# 絶対パスにする。Git Bash では Windows 形式（pwd -W）にそろえる。
doctor_abs() {
	(cd -- "$1" 2>/dev/null && { pwd -W 2>/dev/null || pwd; })
}

# 項目の結果を控える。出力は最後にまとめて、doctor_print が決まった順で出す。
# <項目> <段階> <コード> [<詳細>]
res() {
	r_name=$(printf '%s' "$1" | tr '-' '_')
	eval "R_${r_name}_level=\$2 R_${r_name}_code=\$3 R_${r_name}_detail=\${4:-} R_${r_name}_set=yes"
}

res_get() {
	# <項目> <level|code|detail|set>。控えが無ければ空
	rg_name=$(printf '%s' "$1" | tr '-' '_')
	eval "printf '%s' \"\${R_${rg_name}_$2:-}\""
}

# ---- 時間の上限と後始末

doctor_table() {
	# pid と ppid を 1 行ずつ。使えなければ 1 を返す。
	if ps -A -o pid= -o ppid= 2>/dev/null; then
		return 0
	fi
	case "$doctor_os" in
	MINGW* | MSYS* | CYGWIN*)
		# MSYS の ps の既定の列は PID PPID PGID WINPID …
		ps 2>/dev/null | awk '$1 ~ /^[0-9]+$/ {print $1, $2}'
		return 0
		;;
	esac
	return 1
}

# $1 とその子孫の pid を、親が先の順で出す。表が取れなければ $1 だけ。
doctor_tree() {
	{ doctor_table || :; } | awk -v root="$1" '
		{ kids[$2] = kids[$2] " " $1 }
		END {
			n = 1; q[1] = root
			for (i = 1; i <= n; i++) {
				print q[i]
				m = split(kids[q[i]], a, " ")
				for (j = 1; j <= m; j++) { n++; q[n] = a[j] }
			}
		}'
}

# $1 とその子孫を止める。$2 が 1 なら、止まらなかった子孫を少し待って KILL する。
doctor_kill_tree() {
	[ -n "${1:-}" ] || return 0
	kt_pids=$(doctor_tree "$1")
	kt_live=""
	for kt_p in $kt_pids; do
		if kill -0 "$kt_p" 2>/dev/null; then
			kt_live="$kt_live $kt_p"
		fi
	done
	[ -n "$kt_live" ] || return 0
	# shellcheck disable=SC2086
	kill -TERM $kt_live 2>/dev/null || :
	[ "${2:-0}" = 1 ] || return 0
	# TERM を無視する子もいる。少し待って、残っているものには根も含めて KILL を送る。
	# 根は呼んだ側がまだ wait していないので、止まっていてもゾンビとして見える。ゾンビへの KILL は無害。
	sleep 1
	for kt_p in $kt_live; do
		if kill -0 "$kt_p" 2>/dev/null; then
			kill -KILL "$kt_p" 2>/dev/null || :
		fi
	done
	return 0
}

doctor_cleanup() {
	trap '' INT TERM
	[ -z "$doctor_pid" ] || doctor_kill_tree "$doctor_pid" 1
	[ -z "$doctor_dog" ] || doctor_kill_tree "$doctor_dog" 0
	[ -z "$doctor_tmp" ] || rm -rf "$doctor_tmp"
	return 0
}

# コマンドを時間の上限つきで打つ。
#   doctor_timed <秒> <標準出力の書き先> <標準エラーの書き先> <コマンド または 関数>...
# 戻り値はコマンドのもの。時間切れは 124。
#
# - 見張りはバックグラウンドのサブシェルで、sleep をバックグラウンドで起こして wait で待つ。標準入出力は閉じる
#   （つないだままだと、呼ぶ側の `$( )` やパイプが sleep の終わりまで閉じない）
# - 正常に終わったら、見張りとその sleep を木ごと止める
# - 時間切れのときは、見張りがコマンドの木（ssh・資格情報の取り手を含む）を止める
# - INT・TERM を受けたら、EXIT の doctor_cleanup が同じ後始末をする（wait は信号で戻る）
doctor_timed() {
	dt_limit=$1
	dt_out=$2
	dt_err=$3
	shift 3
	doctor_seq=$((doctor_seq + 1))
	dt_mark="$doctor_tmp/timeout.$doctor_seq"
	"$@" >"$dt_out" 2>"$dt_err" </dev/null &
	doctor_pid=$!
	dt_target=$doctor_pid
	(
		exec </dev/null >/dev/null 2>&1
		dt_sleep=""
		trap 'test -z "$dt_sleep" || kill "$dt_sleep"; exit 0' TERM
		sleep "$dt_limit" &
		dt_sleep=$!
		wait "$dt_sleep"
		: >"$dt_mark"
		doctor_kill_tree "$dt_target" 1
	) &
	doctor_dog=$!
	dt_rc=0
	wait "$dt_target" 2>/dev/null || dt_rc=$?
	doctor_kill_tree "$doctor_dog" 0
	{ wait "$doctor_dog"; } 2>/dev/null || :
	doctor_pid=""
	doctor_dog=""
	if [ -f "$dt_mark" ] && [ "$dt_rc" -ge 128 ]; then
		return 124
	fi
	return "$dt_rc"
}

# ---- 見つける

# 候補（改行区切り。`*` を含んでよい）のうち、最初に実在するファイルを出す。空白を含む候補のために
# IFS を空にして展開する。
doctor_find_candidate() {
	printf '%s\n' "$1" | while IFS= read -r fc_line; do
		[ -n "$fc_line" ] || continue
		IFS=
		# shellcheck disable=SC2086
		for fc_path in $fc_line; do
			if [ -f "$fc_path" ]; then
				printf '%s\n' "$fc_path"
				exit 0
			fi
		done
	done
}

doctor_slash() {
	printf '%s' "$1" | tr '\\' '/'
}

# 既定の置き場。確かめられないものは増やさない。
doctor_default_candidates() {
	# <glab|jq>
	dc_tool=$1
	dc_la=$(doctor_slash "${LOCALAPPDATA:-}")
	dc_home=$(doctor_slash "${HOME:-}")
	if [ -n "$dc_la" ]; then
		printf '%s\n' "$dc_la/Microsoft/WinGet/Links/$dc_tool.exe"
	fi
	if [ "$dc_tool" = glab ]; then
		printf '%s\n' "/c/Program Files/glab/glab.exe"
		if [ -n "$dc_la" ]; then
			printf '%s\n' "$dc_la/Microsoft/WinGet/Packages/GLab.GLab*/glab.exe" \
				"$dc_la/Microsoft/WinGet/Packages/GLab.GLab*/bin/glab.exe"
		fi
	fi
	if [ -n "$dc_home" ]; then
		printf '%s\n' "$dc_home/scoop/shims/$dc_tool.exe" "$dc_home/bin/$dc_tool.exe"
	fi
	printf '%s\n' "/opt/homebrew/bin/$dc_tool" "/usr/local/bin/$dc_tool"
	if [ -n "$dc_home" ]; then
		printf '%s\n' "$dc_home/.local/bin/$dc_tool"
	fi
	if [ "$dc_tool" = glab ]; then
		printf '%s\n' "/snap/bin/glab"
	fi
}

# 置き場の一覧。環境変数 CCNAVI_DOCTOR_GLAB_CANDIDATES・CCNAVI_DOCTOR_JQ_CANDIDATES で差し替えられる
# （改行区切り。`C:/…` の `:` と区別するため。空文字なら探さない。未設定なら既定）。
doctor_candidates_of() {
	case "$1" in
	glab)
		if [ "${CCNAVI_DOCTOR_GLAB_CANDIDATES+set}" = set ]; then
			printf '%s\n' "$CCNAVI_DOCTOR_GLAB_CANDIDATES"
			return 0
		fi
		;;
	jq)
		if [ "${CCNAVI_DOCTOR_JQ_CANDIDATES+set}" = set ]; then
			printf '%s\n' "$CCNAVI_DOCTOR_JQ_CANDIDATES"
			return 0
		fi
		;;
	esac
	doctor_default_candidates "$1"
}

# ---- 判定

doctor_machine() {
	dm_s=$(uname -s 2>/dev/null || :)
	dm_m=$(uname -m 2>/dev/null || :)
	case "$dm_s" in
	Linux*) dm_os=linux ;;
	Darwin*) dm_os=darwin ;;
	MINGW* | MSYS* | CYGWIN*) dm_os=windows ;;
	*) dm_os=unknown ;;
	esac
	case "$dm_m" in
	x86_64 | amd64 | AMD64) dm_arch=x86_64 ;;
	arm64 | aarch64 | ARM64) dm_arch=arm64 ;;
	*) dm_arch=unknown ;;
	esac
	machine="$dm_os-$dm_arch"
	# 動く組み立ての名前（優先順）。ccnavi-launcher.sh・ccnavi-setup.sh の runnable_targets と同じ。
	case "$machine" in
	darwin-arm64) runnable="darwin-arm64 darwin-x86_64" ;;
	windows-arm64) runnable="windows-arm64 windows-x86_64" ;;
	*) runnable="$machine" ;;
	esac
	if [ "$dm_os" = unknown ] || [ "$dm_arch" = unknown ]; then
		res machine warn unknown "$dm_s $dm_m"
	else
		res machine ok "$machine"
	fi
	case "$dm_s" in
	MINGW* | MSYS* | CYGWIN*) res shell ok gitbash ;;
	*)
		if [ -r /proc/version ] && grep -qi microsoft /proc/version 2>/dev/null; then
			res shell ok wsl
		else
			res shell ok posix
		fi
		;;
	esac
}

doctor_pkg_manager() {
	pm_found=""
	for pm_name in winget scoop choco brew apt-get dnf zypper pacman apk; do
		if command -v "$pm_name" >/dev/null 2>&1; then
			case "$pm_name" in
			apt-get) pm_label=apt ;;
			*) pm_label=$pm_name ;;
			esac
			pm_found="${pm_found:+$pm_found,}$pm_label"
		fi
	done
	if [ -n "$pm_found" ]; then
		res pkg-manager ok "$pm_found"
	else
		res pkg-manager warn none
	fi
}

# 版 $1 が $2（X.Y）以上なら 0
doctor_version_ge() {
	vg_a=$(printf '%s' "$1" | sed -n 's/^\([0-9][0-9]*\)\.\([0-9][0-9]*\).*/\1 \2/p')
	[ -n "$vg_a" ] || return 1
	vg_b=$(printf '%s' "$2" | sed -n 's/^\([0-9][0-9]*\)\.\([0-9][0-9]*\).*/\1 \2/p')
	vg_amaj=${vg_a% *}
	vg_amin=${vg_a#* }
	vg_bmaj=${vg_b% *}
	vg_bmin=${vg_b#* }
	[ "$vg_amaj" -gt "$vg_bmaj" ] && return 0
	[ "$vg_amaj" -lt "$vg_bmaj" ] && return 1
	[ "$vg_amin" -ge "$vg_bmin" ]
}

# コマンドの出力の 1 行目から版（数字とドット）を取る。取れなければ unknown。
doctor_version_of() {
	vo_v=$(printf '%s\n' "$1" | sed -n -e 's/^[^0-9]*\([0-9][0-9]*\(\.[0-9][0-9]*\)*\).*/\1/p' -e 1q)
	printf '%s' "${vo_v:-unknown}"
}

doctor_git() {
	if ! command -v git >/dev/null 2>&1; then
		res git ng not-installed
		have_git=no
		return 0
	fi
	have_git=yes
	g_out=$(git --version 2>/dev/null || :)
	g_ver=$(doctor_version_of "$g_out")
	if doctor_version_ge "$g_ver" "$DOCTOR_GIT_MIN"; then
		res git ok ok "$g_ver"
	else
		res git todo too-old "$g_ver"
	fi
}

doctor_jq() {
	if command -v jq >/dev/null 2>&1; then
		j_out=$(jq --version 2>/dev/null || :)
		res jq ok ok "$(doctor_clean "$j_out")"
		have_jq=yes
		return 0
	fi
	have_jq=no
	j_found=$(doctor_find_candidate "$(doctor_candidates_of jq)")
	if [ -n "$j_found" ]; then
		res jq ng not-on-path "$j_found"
	else
		res jq ng not-installed
	fi
}

# glab の手元の状態（通信しない）。結果を glab_level・glab_code・glab_detail に置く。
doctor_glab_local() {
	glab_level=todo
	glab_code=not-installed
	glab_detail=""
	gl_path=$(command -v glab 2>/dev/null || :)
	if [ -z "$gl_path" ]; then
		gl_found=$(doctor_find_candidate "$(doctor_candidates_of glab)")
		if [ -n "$gl_found" ]; then
			glab_code=not-on-path
			glab_detail=$gl_found
		fi
		return 0
	fi
	# `glab auth git-credential --help` の終了コードでは判定しない。help の出力に載るかを見る。
	gl_rc=0
	doctor_timed "$DOCTOR_GIT_LIMIT" "$doctor_tmp/glab-help.out" "$doctor_tmp/glab-help.err" glab auth --help || gl_rc=$?
	gl_help=$(cat "$doctor_tmp/glab-help.out" "$doctor_tmp/glab-help.err" 2>/dev/null || :)
	gl_vrc=0
	doctor_timed "$DOCTOR_GIT_LIMIT" "$doctor_tmp/glab-ver.out" "$doctor_tmp/glab-ver.err" glab --version || gl_vrc=$?
	gl_ver=$(doctor_version_of "$(cat "$doctor_tmp/glab-ver.out" 2>/dev/null || :)")
	glab_detail=$gl_ver
	if printf '%s\n' "$gl_help" | grep -q 'git-credential'; then
		glab_level=ok
		glab_code=installed
	elif [ "$gl_rc" -eq 124 ]; then
		# 応答しない glab を「古い」とは言わない。
		glab_code=timeout
	elif [ "$gl_rc" -ne 0 ] && [ -z "$(printf '%s' "$gl_help" | tr -d '[:space:]')" ]; then
		glab_code=unavailable
	else
		glab_code=too-old
	fi
}

doctor_curl() {
	if command -v curl >/dev/null 2>&1; then
		have_curl=yes
		c_out=$(curl --version 2>/dev/null | sed -n 1p || :)
		res curl ok ok "$(doctor_version_of "$c_out")"
		return 0
	fi
	have_curl=no
	# glab の道（glab が ok）が使えないときだけ、curl が無いのは todo。
	if [ "$glab_level" = ok ]; then
		res curl warn not-installed
	else
		res curl todo not-installed
	fi
}

doctor_workspace() {
	have_ws_git=no
	# .git の中では --is-inside-work-tree が false を出して 0 で終わる。出力も見る。
	ws_inside=$(git -C "$workspace" rev-parse --is-inside-work-tree 2>/dev/null || :)
	if [ "$have_git" = yes ] && [ "$ws_inside" = true ]; then
		have_ws_git=yes
		res workspace ok ok
	else
		res workspace warn not-git
	fi
}

# origin を読む。<リポジトリ> → 標準出力に `<kind> <scheme|ssh> <host>`。
# 戻り値: 0 読めた / 3 origin が無い / 4 読めない。副シェル（$( ）の中で呼ぶこと。
doctor_origin_info() {
	oi_url=$(git -C "$1" remote get-url origin 2>/dev/null) || return 3
	[ -n "$oi_url" ] || return 3
	. "$doctor_host_sh"
	ccnavi_host_parse "$oi_url" >/dev/null 2>&1 || return 4
	oi_scheme=$ccnavi_h_scheme
	case "$oi_url" in
	git@* | [sS][sS][hH]://* | *+[sS][sS][hH]://*) oi_scheme=ssh ;;
	esac
	printf '%s %s %s\n' "$ccnavi_h_kind" "$oi_scheme" "$ccnavi_h_host"
}

# origin の結果を origin_kind・origin_scheme・origin_host に置く。<リポジトリ>
doctor_origin_read() {
	origin_kind=""
	origin_scheme=""
	origin_host=""
	origin_state=none
	[ "$have_git" = yes ] || return 0
	or_rc=0
	or_info=$(doctor_origin_info "$1") || or_rc=$?
	case "$or_rc" in
	0)
		origin_kind=${or_info%% *}
		or_rest=${or_info#* }
		origin_scheme=${or_rest%% *}
		origin_host=${or_rest#* }
		origin_state=ok
		;;
	3) origin_state=none ;;
	*) origin_state=unknown ;;
	esac
}

doctor_origin() {
	doctor_origin_read "$workspace"
	if [ "$have_ws_git" != yes ]; then
		origin_state=none
		origin_kind=""
	fi
	case "$origin_state" in
	none) res origin warn none ;;
	unknown) res origin warn unknown ;;
	*)
		if [ "$origin_kind" = github ]; then
			res origin warn github "$origin_host"
		elif [ "$origin_scheme" = ssh ]; then
			res origin warn ssh "$origin_host"
		else
			res origin ok "$origin_scheme" "$origin_host"
		fi
		;;
	esac
}

# ---- 通信する確認（--online）

# origin に読みに行く（git ls-remote）。timed の中のバックグラウンドのサブシェルで動く。<リポジトリ>
# 認証は ccnavi_git_auth（glab / gh の資格情報の取り手を足す）。尋ねさせない。
doctor_probe_remote() {
	. "$doctor_host_sh"
	ccnavi_git_auth "$1"
	pr_ssh=${GIT_SSH_COMMAND:-}
	pr_core=$(git -C "$1" config --get core.sshCommand 2>/dev/null || :)
	if [ -z "$pr_ssh" ] && [ -z "${GIT_SSH:-}" ] && [ -z "$pr_core" ]; then
		pr_ssh="ssh -o BatchMode=yes -o ConnectTimeout=15"
	fi
	if [ -n "$pr_ssh" ]; then
		GIT_SSH_COMMAND=$pr_ssh
		export GIT_SSH_COMMAND
	fi
	GIT_TERMINAL_PROMPT=0
	GCM_INTERACTIVE=never
	LC_ALL=C
	export GIT_TERMINAL_PROMPT GCM_INTERACTIVE LC_ALL
	pr_rc=0
	git -C "$1" -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=10 ls-remote --exit-code origin HEAD || pr_rc=$?
	# git が終わった印。終了コード 2（HEAD が無い）が、シェル自身の失敗の 2 と取り違えられないようにする。
	printf 'doctor-rc:%s\n' "$pr_rc"
	return "$pr_rc"
}

# 読めるかを確かめて、<名前>（remote か source-remote）の結果を控える。<名前> <リポジトリ>
# ok は「読める」ことだけを意味する。push の権限は確かめていない。
doctor_check_remote() {
	cr_name=$1
	cr_rc=0
	doctor_timed "$DOCTOR_GIT_LIMIT" "$doctor_tmp/$cr_name.out" "$doctor_tmp/$cr_name.err" doctor_probe_remote "$2" || cr_rc=$?
	case "$cr_rc" in
	0) res "$cr_name" ok readable "読めるだけで、push の権限は確かめていない" ;;
	2)
		if grep -q '^doctor-rc:2$' "$doctor_tmp/$cr_name.out" 2>/dev/null; then
			res "$cr_name" ok readable "リモートに HEAD が無い（空のリポジトリ）。push の権限は確かめていない"
		else
			cr_msg=$(doctor_first_line "$(cat "$doctor_tmp/$cr_name.err" 2>/dev/null || :)")
			res "$cr_name" todo unreachable "${cr_msg:-git ls-remote を実行できなかった}"
		fi
		;;
	124) res "$cr_name" todo timeout "${DOCTOR_GIT_LIMIT} 秒で応答が無かった" ;;
	*)
		cr_msg=$(doctor_first_line "$(cat "$doctor_tmp/$cr_name.err" 2>/dev/null || :)")
		res "$cr_name" todo unreachable "${cr_msg:-git ls-remote が失敗した}"
		;;
	esac
}

# 手段を決める（ccnavi_host_connect）。timed の中で動く。標準出力に `<戻り値> <手段>`。
doctor_probe_connect() {
	. "$doctor_host_sh"
	pc_url=$(git -C "$1" remote get-url origin 2>/dev/null)
	ccnavi_host_parse "$pc_url" >/dev/null 2>&1 || exit 20
	ccnavi_h_tmp=$doctor_tmp
	pc_rc=0
	ccnavi_host_connect || pc_rc=$?
	printf '%s %s\n' "$pc_rc" "${ccnavi_h_transport:-}"
}

# 失敗した応答を控える（ccnavi_h_on_fail）。<METHOD> <path> <ホストの返事>
doctor_on_fail() {
	printf '%s\n' "$3" >"$doctor_tmp/api.fail"
}

# curl で API を実際に打つ。glab の疎通は phase 1 で済んでいる（glab が通らなかった結果が curl）ので、
# ここでは glab を呼ばせない（同じ glab を 2 度試して 2 度待たないため）。
doctor_probe_api() {
	. "$doctor_host_sh"
	glab() { return 1; }
	pa_url=$(git -C "$1" remote get-url origin 2>/dev/null)
	ccnavi_host_parse "$pa_url" >/dev/null 2>&1 || exit 20
	ccnavi_h_tmp=$doctor_tmp
	ccnavi_h_max_time=$DOCTOR_CURL_LIMIT
	ccnavi_h_on_fail=doctor_on_fail
	pa_rc=0
	ccnavi_host_connect || pa_rc=$?
	if [ "$pa_rc" -ne 0 ] || [ "$ccnavi_h_transport" != curl ]; then
		exit 21
	fi
	pa_body=$(ccnavi_host_api GET "projects/$(ccnavi_host_encoded_path)") || exit 22
	printf '%s' "$pa_body" | "$ccnavi_h_jq" -e 'type == "object"' >/dev/null 2>&1 || exit 23
	return 0
}

doctor_api_class() {
	# curl の失敗の文面（-fsS）から分ける。
	ac_msg=$1
	case "$ac_msg" in
	*"error: 401"* | *"error: 403"*) res host-api todo unauthorized "トークン（GITLAB_TOKEN）が通らなかった" ;;
	*"error: 404"*) res host-api todo not-found "プロジェクトが見つからない（パスか権限）" ;;
	*"curl: (28)"*) res host-api todo timeout "${DOCTOR_CURL_LIMIT} 秒で応答が無かった" ;;
	*"error: "*)
		ac_code=${ac_msg#*error: }
		ac_code=${ac_code%%[!0-9]*}
		res host-api todo unreachable "HTTP ${ac_code:-?}"
		;;
	*)
		ac_line=$(doctor_first_line "$ac_msg")
		res host-api todo unreachable "${ac_line:-API に届かなかった}"
		;;
	esac
}

doctor_check_host_api() {
	ha_rc=0
	doctor_timed "$DOCTOR_GIT_LIMIT" "$doctor_tmp/connect.out" "$doctor_tmp/connect.err" doctor_probe_connect "$workspace" || ha_rc=$?
	if [ "$ha_rc" -eq 124 ]; then
		res host-api todo timeout "手段を決める確認が ${DOCTOR_GIT_LIMIT} 秒で終わらなかった"
		return 0
	fi
	ha_line=$(sed -n 1p "$doctor_tmp/connect.out" 2>/dev/null || :)
	ha_code=${ha_line%% *}
	ha_via=${ha_line#* }
	[ "$ha_code" != "$ha_line" ] || ha_via=""
	if [ "$ha_rc" -ne 0 ] || [ -z "$ha_code" ]; then
		res host-api todo unreachable "origin を読めなかった"
		return 0
	fi
	case "$ha_code" in
	3)
		res host-api todo no-jq "jq が無い"
		return 0
		;;
	4)
		res host-api todo token-missing "glab が通らず、環境変数 GITLAB_TOKEN も無い"
		return 0
		;;
	5)
		res host-api todo no-curl "glab が通らず、curl も無い"
		return 0
		;;
	0) ;;
	*)
		res host-api todo unreachable "手段を決められなかった"
		return 0
		;;
	esac
	if [ "$ha_via" = glab ]; then
		res host-api ok glab
		return 0
	fi
	rm -f "$doctor_tmp/api.fail"
	ha_rc=0
	doctor_timed $((DOCTOR_CURL_LIMIT + 5)) "$doctor_tmp/api.out" "$doctor_tmp/api.err" doctor_probe_api "$workspace" || ha_rc=$?
	case "$ha_rc" in
	0) res host-api ok curl ;;
	124) res host-api todo timeout "${DOCTOR_CURL_LIMIT} 秒で応答が無かった" ;;
	23) res host-api todo unreachable "応答が API のものに見えない" ;;
	*) doctor_api_class "$(cat "$doctor_tmp/api.fail" 2>/dev/null || :)" ;;
	esac
}

# ---- existing

# 設定ファイルの env.CCNAVI_MODE を読む。<ファイル> → 標準出力 `set:<値>` か `unset`。
# 戻り値: 0 / 1 ファイルが無い / 2 JSON として読めない
doctor_settings_mode() {
	[ -f "$1" ] || return 1
	jq empty "$1" >/dev/null 2>&1 || return 2
	jq -r 'if type == "object" and (.env | type) == "object" and (.env.CCNAVI_MODE | type) == "string" then "set:" + .env.CCNAVI_MODE else "unset" end' "$1" | tr -d '\r' | sed -n 1p
}

doctor_existing() {
	if [ "$have_jq" != yes ]; then
		res existing warn unchecked "jq が無いので設定を読めない"
		return 0
	fi
	ex_bad=""
	ex_value=""
	ex_has=no
	for ex_rel in .claude/settings.json .claude/settings.local.json; do
		ex_rc=0
		ex_got=$(doctor_settings_mode "$workspace/$ex_rel") || ex_rc=$?
		case "$ex_rc" in
		1) continue ;;
		2)
			ex_bad="${ex_bad:+$ex_bad,}$ex_rel"
			continue
			;;
		esac
		case "$ex_got" in
		set:*)
			ex_value=${ex_got#set:}
			ex_has=yes
			;;
		esac
	done
	if [ -n "$ex_bad" ]; then
		res existing warn invalid "$ex_bad"
		return 0
	fi
	ex_low=$(printf '%s' "$ex_value" | tr 'A-Z' 'a-z')
	if [ "$ex_has" = no ] || [ -z "$ex_low" ]; then
		res existing ok none
		return 0
	fi
	case "$ex_low" in
	dry-run) res existing ok dry-run ;;
	enable) res existing warn enable ;;
	*) res existing warn other "$(doctor_clean "$ex_value" | cut -c 1-60)" ;;
	esac
}

# ---- 配布元（prebuilt）

doctor_package() {
	pk_target_file="$from/dist/ccnavi.target"
	if [ ! -f "$pk_target_file" ]; then
		res package ng missing "$from/dist/ccnavi.target が無い"
		return 0
	fi
	pk_target=$(sed -n 1p "$pk_target_file" | tr -d '\r')
	pk_ok=no
	for pk_t in $runnable; do
		if [ "$pk_t" = "$pk_target" ]; then
			pk_ok=yes
		fi
	done
	if [ "$pk_ok" != yes ]; then
		res package ng wrong-target "$(doctor_clean "$pk_target")"
		return 0
	fi
	pk_exe=""
	for pk_name in ccnavi ccnavi.exe; do
		if [ -f "$from/dist/ccnavi/$pk_name" ]; then
			pk_exe="$from/dist/ccnavi/$pk_name"
			break
		fi
	done
	if [ -z "$pk_exe" ]; then
		res package ng no-executable "$from/dist/ccnavi/ に ccnavi も ccnavi.exe も無い"
		return 0
	fi
	# macOS: ダウンロードした zip を展開すると隔離の印が付くことがある。読むだけで外さない。
	if command -v xattr >/dev/null 2>&1 && xattr -p com.apple.quarantine "$pk_exe" >/dev/null 2>&1; then
		res package warn quarantined "install に --clear-quarantine が要る"
		return 0
	fi
	res package ok ok "$pk_target"
}

# ---- 配布元（build）

doctor_build_tools() {
	if command -v uv >/dev/null 2>&1; then
		res uv ok ok "$(doctor_version_of "$(uv --version 2>/dev/null || :)")"
	else
		res uv ng not-installed
	fi
	if [ -f "$from/build.py" ]; then
		res source ok ok "$from"
	else
		res source ng missing "$from/build.py が無い"
	fi
	if [ "$online" = yes ]; then
		doctor_source_remote
	fi
	nd_missing=""
	command -v node >/dev/null 2>&1 || nd_missing="node"
	command -v pnpm >/dev/null 2>&1 || nd_missing="${nd_missing:+$nd_missing,}pnpm"
	if [ -z "$nd_missing" ]; then
		res node ok ok "$(doctor_version_of "$(node --version 2>/dev/null || :)")"
	elif [ "$with_extensions" = yes ]; then
		res node ng not-installed "$nd_missing"
	else
		res node warn not-installed "${nd_missing}（--with-extensions のときに要る）"
	fi
}

doctor_source_remote() {
	if [ "$have_git" != yes ] || [ ! -f "$from/build.py" ]; then
		res source-remote warn skipped no-source
		return 0
	fi
	sr_state=$origin_state
	doctor_origin_read "$from"
	if [ "$origin_state" = ok ]; then
		doctor_check_remote source-remote "$from"
	elif [ "$origin_state" = none ]; then
		res source-remote warn skipped no-origin
	else
		res source-remote warn skipped unknown-origin
	fi
	origin_state=$sr_state
}

doctor_claude() {
	if command -v claude >/dev/null 2>&1; then
		res claude ok ok
	else
		res claude warn not-installed
	fi
}

# ---- 出力

doctor_print() {
	for pr_item in machine shell pkg-manager git jq curl workspace origin glab remote host-api existing package uv source source-remote node claude; do
		[ "$(res_get "$pr_item" set)" = yes ] || continue
		pr_level=$(res_get "$pr_item" level)
		pr_code=$(res_get "$pr_item" code)
		pr_detail=$(res_get "$pr_item" detail)
		pr_detail=$(doctor_clean "$pr_detail")
		if [ -n "$pr_detail" ]; then
			printf 'check: %s %s %s %s\n' "$pr_item" "$pr_level" "$pr_code" "$pr_detail"
		else
			printf 'check: %s %s %s\n' "$pr_item" "$pr_level" "$pr_code"
		fi
	done
}

doctor_next() {
	# 数える
	nx_ng=no
	nx_todos=""
	for nx_item in machine shell pkg-manager git jq curl workspace origin glab remote host-api existing package uv source source-remote node claude; do
		[ "$(res_get "$nx_item" set)" = yes ] || continue
		case "$(res_get "$nx_item" level)" in
		ng) nx_ng=yes ;;
		todo) nx_todos="$nx_todos $nx_item" ;;
		esac
	done
	for nx_item in $nx_todos; do
		nx_d=$(doctor_clean "$(res_get "$nx_item" detail)")
		printf '案内: %s が todo（%s）: %s\n' "$nx_item" "$(res_get "$nx_item" code)" "${nx_d:-詳細なし}"
	done
	self=$(doctor_quote "$from/scripts/ccnavi-doctor.sh")
	if [ "$nx_ng" = yes ]; then
		nx_cmd="sh $self --workspace $(doctor_quote "$workspace") --from $(doctor_quote "$from")"
		[ "$mode" = prebuilt ] || nx_cmd="$nx_cmd --mode $mode"
		[ "$with_extensions" != yes ] || nx_cmd="$nx_cmd --with-extensions"
		[ "$online" != yes ] || nx_cmd="$nx_cmd --online"
		printf '次: ng の項目をユーザに直してもらって返事を待ち、doctor を打ち直す（%s）\n' "$nx_cmd"
		return 0
	fi
	nx_inst="sh $(doctor_quote "$from/scripts/ccnavi-install.sh") $(doctor_quote "$workspace")"
	if [ "$mode" = build ]; then
		nx_inst="$nx_inst --build --source $(doctor_quote "$from")"
		[ "$with_extensions" != yes ] || nx_inst="$nx_inst --with-extensions"
	else
		nx_inst="$nx_inst --from $(doctor_quote "$from")"
	fi
	printf '次: %s --check\n' "$nx_inst"
}

main() {
	mode=prebuilt
	with_extensions=no
	workspace=""
	from=""
	online=no
	no_next=no
	while [ "$#" -gt 0 ]; do
		case "$1" in
		--mode)
			[ "$#" -ge 2 ] || die "--mode に値がありません。"
			mode=$2
			shift 2
			;;
		--with-extensions)
			with_extensions=yes
			shift
			;;
		--workspace)
			[ "$#" -ge 2 ] || die "--workspace に値がありません。"
			workspace=$2
			shift 2
			;;
		--from)
			[ "$#" -ge 2 ] || die "--from に値がありません。"
			from=$2
			shift 2
			;;
		--online)
			online=yes
			shift
			;;
		--no-next)
			no_next=yes
			shift
			;;
		-h | --help | help)
			usage
			return 0
			;;
		*)
			die "$1 は使えない引数です。使えるものは --help で確認できます。"
			;;
		esac
	done
	case "$mode" in
	prebuilt | build) ;;
	*) die "--mode に使えるのは prebuilt か build です。" ;;
	esac
	# 時間の上限は、数字（小数も可）だけ。それ以外は sleep と算術を壊す。
	case "$DOCTOR_GIT_LIMIT$DOCTOR_CURL_LIMIT" in
	*[!0-9.]*) die "CCNAVI_DOCTOR_GIT_LIMIT と CCNAVI_DOCTOR_CURL_LIMIT は数字で指定してください。" ;;
	esac
	case "$DOCTOR_CURL_LIMIT" in
	*.*) die "CCNAVI_DOCTOR_CURL_LIMIT は整数で指定してください。" ;;
	esac
	if [ -z "$workspace" ]; then
		workspace=.
	fi
	[ -d "$workspace" ] || die "$workspace というディレクトリがありません。"
	workspace=$(doctor_abs "$workspace")
	if [ -z "$from" ]; then
		from=$(dirname "$0")/..
	fi
	if [ -f "$from" ]; then
		die "--from にファイルが渡されました。zip は受けません。展開してから、展開したフォルダを渡してください。"
	fi
	[ -d "$from" ] || die "$from というディレクトリがありません。"
	from=$(doctor_abs "$from")
	doctor_host_sh="$from/.ccnavi/scripts/ccnavi-common-host.sh"
	[ -f "$doctor_host_sh" ] || die "$from は ccnavi の配布元に見えません（.ccnavi/scripts/ccnavi-common-host.sh が無い）。"
	if [ "$with_extensions" = yes ] && [ "$mode" != build ]; then
		die "--with-extensions は --mode build のときだけ使えます。"
	fi

	doctor_os=$(uname -s 2>/dev/null || :)
	doctor_tmp=${TMPDIR:-/tmp}/ccnavi-doctor.$$
	doctor_try=0
	while ! (umask 077 && mkdir "$doctor_tmp") 2>/dev/null; do
		doctor_try=$((doctor_try + 1))
		[ "$doctor_try" -lt 20 ] || die "作業用のディレクトリを作れません。" 1
		doctor_tmp=${TMPDIR:-/tmp}/ccnavi-doctor.$$.$doctor_try
	done
	trap doctor_cleanup EXIT
	trap 'exit 130' INT
	trap 'exit 143' TERM

	doctor_machine
	doctor_pkg_manager
	doctor_git
	doctor_jq
	doctor_glab_local
	doctor_curl
	doctor_workspace
	doctor_origin
	if [ "$mode" = prebuilt ]; then
		doctor_package
	fi
	if [ "$online" = yes ]; then
		if [ "$have_ws_git" != yes ]; then
			res remote warn skipped not-git
		elif [ "$origin_state" = none ]; then
			res remote warn skipped no-origin
		elif [ "$origin_state" = unknown ]; then
			res remote warn skipped unknown-origin
		else
			doctor_check_remote remote "$workspace"
			if [ "$origin_kind" = gitlab ]; then
				doctor_check_host_api
			fi
		fi
	fi
	doctor_existing
	if [ "$mode" = build ]; then
		doctor_build_tools
	fi
	doctor_claude

	# 代わりの道（glab 無しで origin と API に届いた）で、glab の段階を下げる。コードはそのまま。
	if [ "$glab_level" != ok ] && [ "$(res_get remote level)" = ok ] && [ "$(res_get host-api level)" = ok ]; then
		glab_note="optional: glab 無しで origin と API に届いた"
		if [ -n "$glab_detail" ]; then
			glab_note="$glab_note ($glab_detail)"
		fi
		res glab warn "$glab_code" "$glab_note"
	else
		res glab "$glab_level" "$glab_code" "$glab_detail"
	fi

	doctor_print
	if [ "$no_next" != yes ]; then
		doctor_next
	fi
	for ng_item in machine shell pkg-manager git jq curl workspace origin glab remote host-api existing package uv source source-remote node claude; do
		if [ "$(res_get "$ng_item" level)" = ng ]; then
			return 1
		fi
	done
	return 0
}

main "$@"; exit $?
