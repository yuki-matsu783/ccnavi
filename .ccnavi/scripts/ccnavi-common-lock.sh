# ccnavi-common-lock.sh  ccnavi-common.sh が読む部品。単体では読まない（読み方は ccnavi-common.sh の冒頭）。
#
# 親子のチケットのロック。置き場は ccnavi-common-state.sh の「取り込み状態とロック」。

# ロック。<ワークスペースルート> <リポジトリ> <P> <待つ秒>
#
# 0 取れた（入れ子を含む）/ 1 待っても取れなかった / 2 古いロックを強制取得しかけて元に戻せなかった（人の対応に切り替える）。
# 取れたら ccnavi_lock_dir に置き場を入れ、CCNAVI_LOCK_HELD="<リポジトリ>/<P>:<持ち主の情報>" を子に渡す。
# 呼ぶ側は抜けるときに ccnavi_lock_drop を打つ（trap の EXIT・INT・TERM・HUP にも置く）。
#
# - `mkdir` の原子性で取る。`flock` は macOS に無い
# - owner は `<ホスト名> <pid> <開始時刻（date +%s）> <持ち主の情報> <OS>`、持ち主の情報は `<pid>-<開始時刻>`。
#   書けなかった・書いた中身が読み返せないときは取れていないとして手放す
# - 古い: ホスト名と OS（`uname -s`）が同じで、置き場が /mnt/ の下で
#   なければ pid で見る。`kill -0` が落ちれば古く、持ち主が生きていれば 10 分を過ぎても強制取得しない
#   （長い操作からロックを取り上げて二重に書かせない。待ちで取れなければ「長い」と言って落とす）。pid を確かめ
#   られない（別のホスト・別の OS・/mnt/ の下・pid が読めない）ときだけ、10 分を過ぎたら時刻で古い
#   とする（WSL と Git Bash は同じホスト名で pid が通じない）。owner が読めなければ `find -mmin +10`
# - 強制取得の仕方: 強制取得の操作を `<ロック>.steal`（`mkdir`、10 分で古い）で 1 つにし、古いと判断したときに読んだ
#   owner の行と今の owner の行が同じなら `mv` で退避して、退避した中の owner がまだ同じなら消して取り直す。
#   違えば（その間に持ち主が替わった）、元の名前が空いていれば戻して待ちに戻り、空いていなければ 2
# - 入れ子: CCNAVI_LOCK_HELD が同じ親子のチケットを指し、その識別子がロックの owner の識別子と同じなら、取ったものとして
#   進み、外さない（識別子の合わない値は偽物として無視する）
ccnavi_lock_dir=""
ccnavi_lock_mark=""
ccnavi_lock_set_held=""
# 取る前の CCNAVI_LOCK_HELD（入れ子で別のロックを取ったとき、外すときに元へ戻す）。
ccnavi_lock_prev_held=""
ccnavi_lock_prev_set=""
ccnavi_lock_os() {
	uname -s 2>/dev/null | tr ' ' '_' || echo unknown
}
ccnavi_lock_take() {
	ccnavi_lk_key="$2/$3"
	ccnavi_lk_dir="$(ccnavi_state "$1")/locks/$2/$3"
	case "${CCNAVI_LOCK_HELD:-}" in
	"$ccnavi_lk_key":*)
		ccnavi_lk_held="${CCNAVI_LOCK_HELD#"$ccnavi_lk_key":}"
		ccnavi_lk_line=$(ccnavi_lock_owner "$ccnavi_lk_dir")
		ccnavi_lk_fields=$(printf '%s\n' "$ccnavi_lk_line" | awk '{ print $4 }')
		if [ -n "$ccnavi_lk_held" ] && [ "$ccnavi_lk_fields" = "$ccnavi_lk_held" ]; then
			return 0
		fi
		log_warn 入れ子の持ち主の情報がロックの持ち主と合わない -- "lock=$ccnavi_lk_key"
		;;
	esac
	mkdir -p "${ccnavi_lk_dir%/*}" 2>/dev/null || return 1
	ccnavi_lk_host=$(hostname 2>/dev/null || uname -n 2>/dev/null || echo unknown)
	ccnavi_lk_os=$(ccnavi_lock_os)
	ccnavi_lk_start=$(date +%s)
	while :; do
		if mkdir "$ccnavi_lk_dir" 2>/dev/null; then
			# 作った直後に覚えておく。owner を書く前に切られても（INT・TERM）、trap の drop が外せるように。
			ccnavi_lock_dir="$ccnavi_lk_dir"
			ccnavi_lock_mark=""
			ccnavi_lk_now=$(date +%s)
			ccnavi_lk_mark="$$-$ccnavi_lk_now"
			ccnavi_lk_want="$ccnavi_lk_host $$ $ccnavi_lk_now $ccnavi_lk_mark $ccnavi_lk_os"
			# 書く前に持ち主の情報を覚えておく（書いた直後に切られても、drop が自分の owner と分かるように）。
			ccnavi_lock_mark="$ccnavi_lk_mark"
			if printf '%s\n' "$ccnavi_lk_want" >"$ccnavi_lk_dir/owner" 2>/dev/null &&
				[ "$(ccnavi_lock_owner "$ccnavi_lk_dir")" = "$ccnavi_lk_want" ]; then
				ccnavi_lock_dir="$ccnavi_lk_dir"
				ccnavi_lock_mark="$ccnavi_lk_mark"
				if [ -n "${CCNAVI_LOCK_HELD+x}" ]; then
					ccnavi_lock_prev_held="$CCNAVI_LOCK_HELD"
					ccnavi_lock_prev_set=yes
				else
					ccnavi_lock_prev_held=""
					ccnavi_lock_prev_set=""
				fi
				CCNAVI_LOCK_HELD="$ccnavi_lk_key:$ccnavi_lk_mark"
				export CCNAVI_LOCK_HELD
				ccnavi_lock_set_held=yes
				log_debug ロックを取った -- "lock=$ccnavi_lk_key"
				return 0
			fi
			# 取った直後に強制取得された（owner を書けない・別の中身）。取れていないとして待ちに戻る。
			ccnavi_lock_dir=""
			ccnavi_lock_mark=""
			log_warn 取ったロックの持ち主を書けなかった -- "lock=$ccnavi_lk_key"
		else
			ccnavi_lk_seen=$(ccnavi_lock_owner "$ccnavi_lk_dir")
			if ccnavi_lock_stale "$ccnavi_lk_dir" "$ccnavi_lk_host" "$ccnavi_lk_os" "$ccnavi_lk_seen"; then
				ccnavi_lk_rc=0
				ccnavi_lock_steal "$ccnavi_lk_dir" "$ccnavi_lk_seen" || ccnavi_lk_rc=$?
				case "$ccnavi_lk_rc" in
				0) continue ;;
				2) return 2 ;;
				esac
			fi
		fi
		ccnavi_lk_now=$(date +%s)
		[ "$((ccnavi_lk_now - ccnavi_lk_start))" -lt "$4" ] || return 1
		sleep 1
	done
}

# ロックの持ち主（owner の 1 行）。読めなければ空。
ccnavi_lock_owner() {
	head -n 1 "$1/owner" 2>/dev/null || :
}

# 10 進の数に揃える（先頭の 0 を落とす。`$(( ))` が 8 進に読まないように）。数でなければ空。
ccnavi_lock_num() {
	case "${1:-}" in
	'' | *[!0-9]*) return 0 ;;
	esac
	ccnavi_ln_v="${1#"${1%%[!0]*}"}"
	printf '%s\n' "${ccnavi_ln_v:-0}"
}

# そのロックが古いか。<ロック> <自分のホスト名> <自分の OS> <読んだ owner の行>
ccnavi_lock_stale() {
	if [ -z "$4" ]; then
		# 取った直後で owner がまだ無いこともある。時刻で見る。
		[ -n "$(find "$1" -maxdepth 0 -mmin +10 2>/dev/null)" ]
		return
	fi
	set -f
	# shellcheck disable=SC2086
	set -- "$1" "$2" "$3" $4
	set +f
	# $4 ホスト名 $5 pid $6 開始時刻 $7 持ち主の情報 $8 OS
	ccnavi_ls_started=$(ccnavi_lock_num "${6:-}")
	if [ -z "$ccnavi_ls_started" ]; then
		[ -n "$(find "$1" -maxdepth 0 -mmin +10 2>/dev/null)" ]
		return
	fi
	ccnavi_ls_now=$(date +%s)
	ccnavi_ls_old=no
	[ "$((ccnavi_ls_now - ccnavi_ls_started))" -gt 600 ] && ccnavi_ls_old=yes
	# 同じ機械の同じ OS のときだけ pid を見る。/mnt/ の下（WSL から Windows の置き場）は時刻だけ。
	ccnavi_ls_pid=$(ccnavi_lock_num "${5:-}")
	ccnavi_ls_local=yes
	case "$1" in
	/mnt/*) ccnavi_ls_local=no ;;
	esac
	{ [ "${4:-}" = "$2" ] && [ "${8:-}" = "$3" ] && [ -n "$ccnavi_ls_pid" ]; } || ccnavi_ls_local=no
	if [ "$ccnavi_ls_local" = yes ]; then
		# 持ち主が生きていれば、時刻に関わらず古くない。
		kill -0 "$ccnavi_ls_pid" 2>/dev/null && return 1
		return 0
	fi
	[ "$ccnavi_ls_old" = yes ]
}

# そのロックが 10 分を超えて持たれているか（持ち主が生きていて強制取得しないときの文面に使う）。<ロック>
ccnavi_lock_long() {
	ccnavi_ll_started=$(ccnavi_lock_num "$(ccnavi_lock_owner "$1" | awk '{ print $3 }')")
	[ -n "$ccnavi_ll_started" ] || return 1
	[ "$(($(date +%s) - ccnavi_ll_started))" -gt 600 ]
}

# 長いロックの持ち主と止め方を 1 文で出す（文面だけ。判定には使わない）。<ロック>
# 開始時刻は GNU の `date -d @N` か BSD の `date -r N` で読める形にし、落ちれば数のまま。
ccnavi_lock_describe() {
	ccnavi_lds_line=$(ccnavi_lock_owner "$1")
	ccnavi_lds_host=$(printf '%s\n' "$ccnavi_lds_line" | awk '{ print $1 }')
	ccnavi_lds_pid=$(ccnavi_lock_num "$(printf '%s\n' "$ccnavi_lds_line" | awk '{ print $2 }')")
	ccnavi_lds_started=$(ccnavi_lock_num "$(printf '%s\n' "$ccnavi_lds_line" | awk '{ print $3 }')")
	ccnavi_lds_at=""
	if [ -n "$ccnavi_lds_started" ]; then
		# BSD の `date -d` は別の意味なので、GNU（`--version` が通る）のときだけ `-d` を使う。
		if date --version >/dev/null 2>&1; then
			ccnavi_lds_at=$(date -d "@$ccnavi_lds_started" '+%Y-%m-%d %H:%M:%S' 2>/dev/null) || ccnavi_lds_at=""
		else
			ccnavi_lds_at=$(date -r "$ccnavi_lds_started" '+%Y-%m-%d %H:%M:%S' 2>/dev/null) || ccnavi_lds_at=""
		fi
		[ -n "$ccnavi_lds_at" ] || ccnavi_lds_at="$ccnavi_lds_started"
	fi
	printf '持ち主は pid %s・ホスト %s・開始 %s。そのプロセスが固まっているなら、ユーザに終了させてもらってから打ち直してください（持ち主のプロセスが終われば、ロックは次の実行が片付ける）\n' \
		"${ccnavi_lds_pid:-?}" "${ccnavi_lds_host:-?}" "${ccnavi_lds_at:-?}"
}

# 古いロックを強制取得する。<ロック> <古いと判断したときに読んだ owner の行>
# 0 強制取得できた（取り直す）/ 1 強制取得しなかった（待ちに戻る）/ 2 退けたものを戻せなかった
ccnavi_lock_steal() {
	ccnavi_st_gate="$1.steal"
	if ! mkdir "$ccnavi_st_gate" 2>/dev/null; then
		# 別の誰かが強制取得している最中。10 分を過ぎた取得用のロック（.steal）は、途中で落ちた取得の残りなので外す。
		if [ -n "$(find "$ccnavi_st_gate" -maxdepth 0 -mmin +10 2>/dev/null)" ]; then
			rmdir "$ccnavi_st_gate" 2>/dev/null || :
		fi
		return 1
	fi
	ccnavi_st_rc=1
	ccnavi_st_aside="$1.stale.$$"
	ccnavi_st_now=$(ccnavi_lock_owner "$1")
	if [ "$ccnavi_st_now" = "$2" ] && { [ -n "$2" ] || [ -n "$(find "$1" -maxdepth 0 -mmin +10 2>/dev/null)" ]; } &&
		mv "$1" "$ccnavi_st_aside" 2>/dev/null; then
		if [ "$(ccnavi_lock_owner "$ccnavi_st_aside")" = "$2" ]; then
			rm -rf "$ccnavi_st_aside" 2>/dev/null || :
			log_info 古いロックを強制取得した -- "lock=$1"
			ccnavi_st_rc=0
		elif [ ! -e "$1" ] && mv "$ccnavi_st_aside" "$1" 2>/dev/null; then
			ccnavi_st_rc=1
		else
			log_warn 強制取得しかけたロックを戻せなかった -- "lock=$1"
			ccnavi_st_rc=2
		fi
	fi
	rmdir "$ccnavi_st_gate" 2>/dev/null || :
	return "$ccnavi_st_rc"
}

# 自分が取ったロックを外す。入れ子で取ったもの（ccnavi_lock_dir が空）は外さない。
# owner の持ち主の情報が自分のものでなければ（強制取得された後）触らない。自分が渡した CCNAVI_LOCK_HELD も消す。
ccnavi_lock_drop() {
	if [ -n "$ccnavi_lock_dir" ]; then
		ccnavi_ld_line=$(ccnavi_lock_owner "$ccnavi_lock_dir")
		ccnavi_ld_mark=$(printf '%s\n' "$ccnavi_ld_line" | awk '{ print $4 }')
		if [ -n "$ccnavi_lock_mark" ] && [ "$ccnavi_ld_mark" = "$ccnavi_lock_mark" ]; then
			rm -rf "$ccnavi_lock_dir" 2>/dev/null || :
		elif [ -z "$ccnavi_ld_line" ]; then
			# 作ったが owner を書く前に切られた。中身が空のままなら自分の作ったものなので外す。
			rmdir "$ccnavi_lock_dir" 2>/dev/null || rm -f "$ccnavi_lock_dir/owner" 2>/dev/null || :
			rmdir "$ccnavi_lock_dir" 2>/dev/null || :
		fi
		ccnavi_lock_dir=""
	fi
	if [ -n "$ccnavi_lock_set_held" ]; then
		# 入れ子の中で別のロック（統合先の取り込み結果のロックなど）を取ったときは、外側から渡された値に戻す。
		# 消したままにすると、外側（C1）が持つ親子のチケットのロックを、この sh の後の段が入れ子と読めない。
		if [ -n "$ccnavi_lock_prev_set" ]; then
			CCNAVI_LOCK_HELD="$ccnavi_lock_prev_held"
			export CCNAVI_LOCK_HELD
		else
			unset CCNAVI_LOCK_HELD
		fi
		ccnavi_lock_set_held=""
		ccnavi_lock_prev_held=""
		ccnavi_lock_prev_set=""
	fi
	return 0
}
