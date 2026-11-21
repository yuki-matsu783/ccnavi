# ccnavi-common-lock.sh  ccnavi-common.sh が読む部品。単体では読まない（読み方は ccnavi-common.sh の冒頭）。
#
# 親子のチケットのロック。置き場は ccnavi-common-state.sh の「state の置き場とロック」。

# ロック。<ワークスペースルート> <リポジトリ> <P> <待つ秒>
#
# 0 取れた（入れ子を含む）/ 1 待っても取れなかった / 2 古いロックを強制取得しかけて元に戻せなかった（人の対応に切り替える）。
# 取れたら ccnavi_lock_dir に置き場を入れ、CCNAVI_LOCK_HELD="<リポジトリ>/<P>:<持ち主の情報>" を子に渡す。
# 呼ぶ側は抜けるときに ccnavi_lock_drop を打つ（trap の EXIT・INT・TERM・HUP にも置く）。
# 取れなかったとき（1・2）の文は ccnavi_lock_why が作る。外側のロックを持ったまま別のロックを取るときは、
# ccnavi_lock_take_aside・ccnavi_lock_drop_aside を使う（外側の状態は変えない）。
#
# - `mkdir` の原子性で取る。`flock` は macOS に無い
# - owner は `<ホスト名> <pid> <開始時刻（date +%s）> <持ち主の情報>`、持ち主の情報は `<pid>-<開始時刻>`。
#   書けなければ、取れていないものとして外して 1
# - 古い: ホスト名が同じで pid が数なら pid で見る。`kill -0` が失敗すれば古い。持ち主が生きていれば、10 分を過ぎても
#   強制取得しない（長い操作からロックを取り上げると、二つの操作が同時に書くため。待っても取れなければ失敗にする）。
#   pid を確かめられない（別のホスト・pid が読めない）ときだけ、10 分を過ぎたら時刻で古いとする。
#   owner が読めなければ `find -mmin +10`。同じ機械は同じ環境（WSL だけ、Git Bash だけ）で使う前提で、環境の違いは見ない
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
# 直近の ccnavi_lock_take が見たロックの置き場（取れなかったときの文に使う）。
ccnavi_lock_path=""
ccnavi_lock_take() {
	ccnavi_lk_key="$2/$3"
	ccnavi_lk_dir="$(ccnavi_state "$1")/locks/$2/$3"
	ccnavi_lock_path="$ccnavi_lk_dir"
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
	ccnavi_lk_start=$(date +%s)
	while :; do
		if mkdir "$ccnavi_lk_dir" 2>/dev/null; then
			# 作った直後に覚えておく。owner を書く前に切られても（INT・TERM）、trap の drop が外せるように。
			ccnavi_lock_dir="$ccnavi_lk_dir"
			ccnavi_lock_mark=""
			ccnavi_lk_now=$(date +%s)
			ccnavi_lk_mark="$$-$ccnavi_lk_now"
			# 書く前に持ち主の情報を覚えておく（書いた直後に切られても、drop が自分の owner と分かるように）。
			ccnavi_lock_mark="$ccnavi_lk_mark"
			if ! printf '%s\n' "$ccnavi_lk_host $$ $ccnavi_lk_now $ccnavi_lk_mark" >"$ccnavi_lk_dir/owner" 2>/dev/null; then
				rm -f "$ccnavi_lk_dir/owner" 2>/dev/null || :
				rmdir "$ccnavi_lk_dir" 2>/dev/null || :
				ccnavi_lock_dir=""
				ccnavi_lock_mark=""
				log_warn ロックの持ち主を書けなかった -- "lock=$ccnavi_lk_key"
				return 1
			fi
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
		else
			ccnavi_lk_seen=$(ccnavi_lock_owner "$ccnavi_lk_dir")
			if ccnavi_lock_stale "$ccnavi_lk_dir" "$ccnavi_lk_host" "$ccnavi_lk_seen"; then
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

# そのロックが古いか。<ロック> <自分のホスト名> <読んだ owner の行>
ccnavi_lock_stale() {
	if [ -z "$3" ]; then
		# 取った直後で owner がまだ無いこともある。時刻で見る。
		[ -n "$(find "$1" -maxdepth 0 -mmin +10 2>/dev/null)" ]
		return
	fi
	set -f
	# shellcheck disable=SC2086
	set -- "$1" "$2" $3
	set +f
	# $3 ホスト名 $4 pid $5 開始時刻 $6 持ち主の情報
	case "${5:-}" in
	'' | *[!0-9]*)
		[ -n "$(find "$1" -maxdepth 0 -mmin +10 2>/dev/null)" ]
		return
		;;
	esac
	# 同じホストで pid が数のときだけ pid を見る。持ち主が生きていれば、時刻に関わらず古くない。
	case "${4:-}" in
	'' | *[!0-9]*) ;;
	*)
		if [ "${3:-}" = "$2" ]; then
			kill -0 "$4" 2>/dev/null && return 1
			return 0
		fi
		;;
	esac
	[ "$(($(date +%s) - $5))" -gt 600 ]
}

# 取れなかったときの文を 1 つ出す。<ccnavi_lock_take の戻り値> <ロックの名前（「親子のチケット P」など）>
ccnavi_lock_why() {
	case "$1" in
	2)
		printf '%s の古いロック（%s）を強制取得する途中で止まり、元に戻せなかった。ユーザが中身を見て片付ける。\n' "$2" "$ccnavi_lock_path"
		;;
	*)
		printf '%s のロックを他の操作が持っている（%s、owner: %s）。終わってから再実行してください。持ち主のプロセスが固まっているなら、ユーザに終了してもらう。\n' \
			"$2" "$ccnavi_lock_path" "$(ccnavi_lock_owner "$ccnavi_lock_path")"
		;;
	esac
}

# 外側のロック（ccnavi_lock_take で取った状態）を持ったまま、別のロックを取る・外す。外側の状態は変えない。
# 第 1 引数 <頭> は変数名の頭（例 op_lk）。取ったロックの状態は <頭>_dir・_mark・_set・_prev_held・_prev_set に入る。
# take_aside <頭> <ccnavi_lock_take の引数>: 戻り値は ccnavi_lock_take と同じ。入れ子で何も取らなかったときは <頭>_dir が空
# drop_aside <頭>: <頭>_dir が空なら何もしない
ccnavi_lock_stash() {
	eval "${1}_dir=\$ccnavi_lock_dir ${1}_mark=\$ccnavi_lock_mark ${1}_set=\$ccnavi_lock_set_held ${1}_prev_held=\$ccnavi_lock_prev_held ${1}_prev_set=\$ccnavi_lock_prev_set"
}
ccnavi_lock_unstash() {
	eval "ccnavi_lock_dir=\$${1}_dir ccnavi_lock_mark=\$${1}_mark ccnavi_lock_set_held=\$${1}_set ccnavi_lock_prev_held=\$${1}_prev_held ccnavi_lock_prev_set=\$${1}_prev_set"
}
ccnavi_lock_take_aside() {
	ccnavi_ta_name="$1"
	shift
	ccnavi_lock_stash ccnavi_ta
	# 入れ子で何も取らなかったとき、外側の置き場を取ったものと読まないよう、先に空にする。
	ccnavi_lock_dir=""
	ccnavi_ta_rc=0
	ccnavi_lock_take "$@" || ccnavi_ta_rc=$?
	ccnavi_lock_stash "$ccnavi_ta_name"
	ccnavi_lock_unstash ccnavi_ta
	return "$ccnavi_ta_rc"
}
ccnavi_lock_drop_aside() {
	eval "[ -n \"\$${1}_dir\" ]" || return 0
	ccnavi_lock_stash ccnavi_ta
	ccnavi_lock_unstash "$1"
	ccnavi_lock_drop
	ccnavi_lock_unstash ccnavi_ta
	eval "${1}_dir="
}

# 古いロックを強制取得する。<ロック> <古いと判断したときに読んだ owner の行>
# 0 強制取得できた（取り直す）/ 1 強制取得しなかった（待ちに戻る）/ 2 退けたものを戻せなかった
ccnavi_lock_steal() {
	ccnavi_st_gate="$1.steal"
	if ! mkdir "$ccnavi_st_gate" 2>/dev/null; then
		# 別の誰かが強制取得している最中。10 分を過ぎた取得用のロック（.steal）は、途中で失敗した取得の残りなので外す。
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
		# 入れ子の中で別のロック（親のワークツリーを使う別の操作のロックなど）を取ったときは、外側から渡された値に戻す。
		# 消したままにすると、外側（write-and-push）が持つ親子のチケットのロックを、この sh の後の処理が入れ子と読めない。
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
