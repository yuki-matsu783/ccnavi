# ccnavi-common-write-and-push.sh  ccnavi-common.sh が読む部品。単体では読まない（読み方は ccnavi-common.sh の冒頭）。
#
# write-and-push。親子のチケットの状態を書く一連の操作を 1 つにまとめる。

# ---- write-and-push
#
# 親子のチケットについて、チケットの状態を書く一連の操作を 1 つの操作にまとめる。親子のチケットは
# 親のブランチ 1 本に対応する。ここで対象にするのは、親のワークツリーがあり、origin があり、HEAD の確認に通る
# 親子のチケット。呼ぶ側（ccnavi-ticket.sh・ccnavi-review.sh・ccnavi-user-decide.sh など）は次の順に関数を呼ぶ。
#
#   ccnavi_wap_family <識別子>   親子のチケットと、write-and-push の対象か（ccnavi_wap_target に yes / no / stop）
#   ccnavi_wap_begin             1 ロック 2 途中の操作 3 write-and-push の外の変更の見分けとコミット 4 取り込み 5 未送信の確かめ
#   ccnavi_wap_take_in           4 取り込みと、取り込みの後の確かめ（承認の push も使う）
#   ccnavi_wap_push <先頭>       push と、届いたかの確かめ（ls-remote。承認の push も使う）
#   ccnavi_wap_write <文> -- <実行ファイルの引数>...
#                               6〜8 元の先頭・書く・コミット（実行ファイルの 1 回の起動） 9 push 10 届いたか
#                               11 戻して 1 回だけやり直す
#   ccnavi_wap_merge_plan <子>   子の finish だけ。begin の後、write の前に呼ぶ。子を親のブランチへ合流してよいかを
#                               実行ファイルに聞く（write-and-push merge）。止めるなら 1（何も変えず、ロックも外した）。
#                               合流するなら write の 6〜8 の起動が、書く前に合流する（下の「合流」）
#   ccnavi_wap_end               ロックを外す（trap の EXIT・INT・TERM・HUP にも置く）
#
# 合流（子の finish）。6〜8 の起動が、書く前に親のワークツリーで子の先頭を `merge --no-ff --no-commit` で入れ、
# 合流と状態を 1 つのマージコミットにする。衝突したら merge を取りやめて何も変えずに止める。
# 呼ぶ側が先に決めるもの。
#   ccnavi_wap_root   ワークスペースルート
#   ccnavi_wap_label  文面の頭（ccnavi-ticket など）
#   ccnavi_wap_exe    関数。実行ファイルを `--root <ルート>` つきで起こし、引数を渡す
#                    実行ファイルの探し方がほかの sh と同じでよければ、ccnavi_wap_use_exe <ルート> で決める
#
# 文面はすべて標準エラーへ出す（ボードは標準出力の JSON を読むため）。実行ファイルの標準出力は
# そのまま通す。ccnavi_wap_capture に書き先を入れると、そこへ書く。
#
# 実行ファイルはネットワークに出ない。見分け（`write-and-push family`・`sort`）と、ローカルの書き込みと戻し
# （途中の操作の確かめ・コミット・合流・比較つきの戻し。`write-and-push busy`・`commit`・`undo`・`landed` と
# `--record-commit`）は実行ファイルが持つ。ここ（sh）が持つのは、ロック（sh のプロセスの寿命）、取り込み
# （ccnavi-sync.sh を入れ子のロックで起こす）、push のゲートの呼び出し、push と届いたかの確かめ（ls-remote）。
# どれも外との出入りか段取り。
#
# 環境変数: CCNAVI_LOCK_WAIT（ロックを待つ秒、既定 120）/ CCNAVI_WRITE_AND_PUSH_TIMEOUT（push・ls-remote 1 回の
#   タイムアウトの秒、既定 60）/ CCNAVI_WRITE_AND_PUSH_COMMIT_TIMEOUT（コミット 1 回のタイムアウトの秒、既定 60。
#   実行ファイルが読む）
#
# コミットは `--no-verify` でユーザの hook（pre-commit・commit-msg）を実行しない。コミットするのは状態のファイル
# だけで、コードの検査の対象ではないため。署名はユーザの設定に従うが、
# タイムアウトを付け、pinentry などが尋ねて止まりっぱなしにならないようにする（切れたら失敗）。
#
# 6〜8 の途中で INT・TERM・HUP が来たら、実行ファイルが書き終えてから自分で戻す。push の前に来たら、
# ccnavi_wap_end が実行ファイルに戻させる（write-and-push undo）。強制終了（KILL）で残ったコミットは、
# 次の write-and-push の 5 が「未送信」で止まり、戻し方を言う。

ccnavi_wap_target=""
ccnavi_wap_why=""
ccnavi_wap_family_id=""
# 親のブランチ名。ref・push・ls-remote はこれを使う。
ccnavi_wap_branch=""
ccnavi_wap_repo=""
ccnavi_wap_tree=""
ccnavi_wap_approved=""
ccnavi_wap_review=""
ccnavi_wap_tmp=""
ccnavi_wap_capture=""
ccnavi_wap_tab=$(printf '\t')
# 合流する子の先頭（空なら合流しない）。ccnavi_wap_merge_plan が決める。
ccnavi_wap_merge_sha=""
# 親のブランチが一度も送られていないために止めたら 1。呼び出し元が次の一手を足す（共通関数は事実だけを言う）。
ccnavi_wap_unsent_parent=""

ccnavi_wap_say() {
	printf '%s: %s\n' "$ccnavi_wap_label" "$*" >&2
}

ccnavi_wap_scratch() {
	[ -z "$ccnavi_wap_tmp" ] || return 0
	ccnavi_wap_tmp=$(mktemp -d 2>/dev/null || mktemp -d -t ccnavi-write-and-push) || return 1
}

# ccnavi_wap_exe を決める。<ワークスペースルート>
#
# 実行ファイル（ccnavi_bin）を `--root <ルート>` つきで起こす。無ければ、ccnavi_wap_exe は 1 を返すだけの
# 関数にして 1 を返す（止めるかどうかと文面は呼ぶ側が決める）。見つけた実行ファイルのパスは ccnavi_wap_bin に入れる。
# 各 sh が同じ定義をコピーして持たないように、ここに 1 つだけ置く。
ccnavi_wap_bin=""
ccnavi_wap_exe_root=""
ccnavi_wap_use_exe() {
	ccnavi_wap_exe_root="$1"
	if ccnavi_wap_bin=$(ccnavi_bin "$1"); then
		ccnavi_wap_exe() { "$ccnavi_wap_bin" --root "$ccnavi_wap_exe_root" "$@"; }
		return 0
	fi
	ccnavi_wap_bin=""
	ccnavi_wap_exe() { return 1; }
	return 1
}

# 実行ファイルの write-and-push <名前> を起こし、答えを <出力> に書く（Windows の実行ファイルの CRLF の CR は除く）。
# <名前> <出力> <引数>...。1 行目が `write-and-push 1` でなければ（答えない・古い実行ファイル）、言う文を
# ccnavi_wap_noans に入れて 1。後に続ける文（何も反映していません など）は呼ぶ側が付ける。
ccnavi_wap_noans=""
ccnavi_wap_ask() {
	ccnavi_ak_name="$1"
	ccnavi_ak_out="$2"
	shift 2
	ccnavi_wap_exe write-and-push "$ccnavi_ak_name" "$@" 2>"$ccnavi_wap_tmp/$ccnavi_ak_name-err" </dev/null | tr -d '\r' >"$ccnavi_ak_out" || :
	[ "$(head -n 1 "$ccnavi_ak_out" 2>/dev/null)" = "write-and-push 1" ] && return 0
	ccnavi_wap_noans="実行ファイルが write-and-push $ccnavi_ak_name に答えない（$(head -n 1 "$ccnavi_wap_tmp/$ccnavi_ak_name-err" 2>/dev/null)）"
	return 1
}

# 親子のチケットと、write-and-push の対象か。<識別子>
#
# ccnavi_wap_target: yes（write-and-push で回す）/ no（origin が無い。今の手元の動きのまま）/ stop（止める理由がある）。
# 実行ファイルが答えなかった（古い・失敗した）ときは stop。
ccnavi_wap_family() {
	ccnavi_wap_target=no
	ccnavi_wap_why=""
	ccnavi_wap_family_id=""
	ccnavi_wap_branch=""
	ccnavi_wap_repo=""
	ccnavi_wap_tree=""
	# 子の識別子（`<親>-<2 桁のフェーズ番号>-<2 桁の連番>`）なら、右から 2 要素を剥がして親にする。
	ccnavi_cf_p="$1"
	case "$ccnavi_cf_p" in
	?*-[0-9][0-9]-[0-9][0-9]) ccnavi_cf_p="${ccnavi_cf_p%-[0-9][0-9]-[0-9][0-9]}" ;;
	esac
	ccnavi_wap_scratch || {
		ccnavi_wap_target=stop
		ccnavi_wap_why="一時ディレクトリが作れない"
		return 0
	}
	: >"$ccnavi_wap_tmp/hints"
	if ! ccnavi_wap_ask family "$ccnavi_wap_tmp/family" "$1"; then
		ccnavi_wap_family_id="$ccnavi_cf_p"
		ccnavi_wap_target=stop
		ccnavi_wap_why="$ccnavi_wap_noans。書かずに止める。実行ファイルを新しくしてください"
		return 0
	fi
	ccnavi_cf_ans=$(cat "$ccnavi_wap_tmp/family")
	ccnavi_wap_target=$(ccnavi_val1 target "$ccnavi_cf_ans")
	ccnavi_wap_why=$(ccnavi_val1 why "$ccnavi_cf_ans")
	ccnavi_wap_family_id=$(ccnavi_val1 family "$ccnavi_cf_ans")
	ccnavi_wap_repo=$(ccnavi_val1 repo "$ccnavi_cf_ans")
	ccnavi_wap_tree=$(ccnavi_val1 tree "$ccnavi_cf_ans")
	ccnavi_wap_approved=$(ccnavi_val1 approved "$ccnavi_cf_ans")
	ccnavi_wap_review=$(ccnavi_val1 review "$ccnavi_cf_ans")
	ccnavi_wap_branch=$(ccnavi_val1 branch "$ccnavi_cf_ans")
	ccnavi_val hint "$ccnavi_cf_ans" >"$ccnavi_wap_tmp/hints"
	case "$ccnavi_wap_target" in
	yes)
		if ! ccnavi_branch_ok "$ccnavi_wap_branch" "$ccnavi_wap_family_id"; then
			ccnavi_wap_target=stop
			ccnavi_wap_why="親のブランチ名（親のワークツリーの HEAD）を読めないか、ブランチ名の形でない（${ccnavi_wap_branch:-空}）"
		elif [ -z "$ccnavi_wap_tree" ] || [ ! -d "$ccnavi_wap_tree" ]; then
			ccnavi_wap_target=stop
			ccnavi_wap_why="親のワークツリーが決まらない"
		elif ! git -C "$ccnavi_wap_tree" remote get-url origin >/dev/null 2>&1; then
			ccnavi_wap_target=no
			ccnavi_wap_why="origin がありません（今の手元の動きのまま）"
		fi
		;;
	no | stop) ;;
	*)
		ccnavi_wap_target=stop
		ccnavi_wap_why="実行ファイルの出力（target）を読めない"
		;;
	esac
	log_debug write-and-push の対象を決めた -- "family=$ccnavi_wap_family_id" "target=$ccnavi_wap_target"
	return 0
}

# 止めたときの文面（ccnavi_wap_target が stop）。
ccnavi_wap_refuse() {
	ccnavi_wap_say "親子のチケット ${ccnavi_wap_family_id:-?} のチケットの状態を書かずに止めた。${ccnavi_wap_why}"
	while IFS= read -r ccnavi_rf_hint; do
		[ -n "$ccnavi_rf_hint" ] && printf '  %s\n' "$ccnavi_rf_hint" >&2
	done <"$ccnavi_wap_tmp/hints"
	log_info write-and-push で止めた -- "family=$ccnavi_wap_family_id" "reason=write-and-push-stop"
}
# 実行ファイルの答え（ファイル）の `say` の行を、文面の頭を付けて言う。<答えのファイル>
ccnavi_wap_said() {
	ccnavi_val say "$(cat "$1" 2>/dev/null)" | while IFS= read -r ccnavi_sd_line; do
		ccnavi_wap_say "$ccnavi_sd_line"
	done
}

# 途中の操作の名前（無ければ空）。<ツリー>。実行ファイルが答えなければ、止める側に倒して、答えないことを名前の代わりに言う。
ccnavi_wap_busy() {
	if ! ccnavi_wap_ask busy "$ccnavi_wap_tmp/busy" "$1"; then
		printf '%s\n' "$ccnavi_wap_noans"
		return 0
	fi
	ccnavi_val1 busy "$(cat "$ccnavi_wap_tmp/busy")"
	return 0
}

# 1〜5。0 なら書いてよい（ロックを持ったまま返る）。1 なら止めた（ロックは外した）。
ccnavi_wap_begin() {
	ccnavi_cb_wait=$(ccnavi_number_or "${CCNAVI_LOCK_WAIT:-}" 120)
	ccnavi_cb_rc=0
	ccnavi_lock_take "$ccnavi_wap_root" "$ccnavi_wap_repo" "$ccnavi_wap_family_id" "$ccnavi_cb_wait" || ccnavi_cb_rc=$?
	case "$ccnavi_cb_rc" in
	0) ;;
	2)
		ccnavi_wap_say "親子のチケット $ccnavi_wap_family_id の古いロックを強制取得する途中で止まり、元に戻せなかった。ユーザが中身を見て片付ける（$(ccnavi_state "$ccnavi_wap_root")/locks/$ccnavi_wap_repo/${ccnavi_wap_family_id}）"
		return 1
		;;
	*)
		ccnavi_cb_lock="$(ccnavi_state "$ccnavi_wap_root")/locks/$ccnavi_wap_repo/$ccnavi_wap_family_id"
		if ccnavi_lock_long "$ccnavi_cb_lock"; then
			ccnavi_wap_say "親子のチケット $ccnavi_wap_family_id のロックが 10 分を超えて取られたままになっている。持ち主はまだ動いているので強制取得しない。終わるのを待つか、持ち主をユーザが確かめてください。$(ccnavi_lock_describe "$ccnavi_cb_lock")"
		else
			ccnavi_wap_say "親子のチケット $ccnavi_wap_family_id のロックを他の操作が持っている（$(ccnavi_lock_owner "$ccnavi_cb_lock")）。終わってから再実行してください"
		fi
		return 1
		;;
	esac
	ccnavi_cb_busy=$(ccnavi_wap_busy "$ccnavi_wap_tree")
	if [ -n "$ccnavi_cb_busy" ]; then
		ccnavi_wap_say "親のワークツリーに途中の操作（${ccnavi_cb_busy}）がある。済ませるか取りやめてから再実行してください（何も反映していません）"
		ccnavi_lock_drop
		return 1
	fi
	if ! ccnavi_wap_prepare; then
		ccnavi_lock_drop
		return 1
	fi
	return 0
}

# 置き場の変更を見分ける。<出力> [<版>]。答えられなければ 1。
ccnavi_wap_sort() {
	ccnavi_wap_ask sort "$1" "$ccnavi_wap_family_id" ${2:+"$2"} && return 0
	ccnavi_wap_say "$ccnavi_wap_noans。置き場の変更を見分けられなかった。何も反映していません"
	return 1
}

# 見分けで止めるものがあれば言って 1。<見分けの出力> <c の前置き> <c の後書き> <d の前置き> <d の後書き>
ccnavi_wap_stops() {
	ccnavi_cs_ans=$(cat "$1")
	ccnavi_cs_c=$(ccnavi_val c "$ccnavi_cs_ans" | tr '\n' ' ' | sed 's/ *$//')
	ccnavi_cs_d=$(ccnavi_val d "$ccnavi_cs_ans" | tr '\n' ' ' | sed 's/ *$//')
	[ -z "$ccnavi_cs_c$ccnavi_cs_d" ] && return 0
	[ -n "$ccnavi_cs_c" ] && ccnavi_wap_say "$2（${ccnavi_cs_c}）$3"
	[ -n "$ccnavi_cs_d" ] && ccnavi_wap_say "$4（${ccnavi_cs_d}）$5"
	ccnavi_val why "$ccnavi_cs_ans" | sed 's/^/  - /' >&2
	log_info write-and-push で止めた -- "family=$ccnavi_wap_family_id" "reason=write-and-push-unknown-change"
	return 1
}

# 4. 取り込み（ccnavi-sync.sh。ロックは入れ子で渡る）と、取り込みの後の親のワークツリーの確かめ・push のゲート。
# 承認の push（ccnavi-user-push-approved.sh）も使う。0 なら続けてよい。1 なら言って止めた（ロックは呼ぶ側が外す）。
ccnavi_wap_take_in() {
	ccnavi_cp_rc=0
	sh "$ccnavi_wap_sh/ccnavi-sync.sh" "$ccnavi_wap_family_id" </dev/null >"$ccnavi_wap_tmp/sync" 2>&1 || ccnavi_cp_rc=$?
	sed "s/^/  /" "$ccnavi_wap_tmp/sync" >&2
	if [ "$ccnavi_cp_rc" -ne 0 ]; then
		# 3 と 4 の間に hook が書いた（取り込みが書きかけと重なった。sync の終了コード 3）。やり直さずに止める。
		# 再実行すれば 3 でコミットされる。
		if [ "$ccnavi_cp_rc" -eq 3 ]; then
			ccnavi_wap_say "取り込みが書きかけ（変更の見分けの後に hook が書いたもの）と重なった。何も反映していません。再実行してください（書きかけを先にコミットしてから取り込みます）"
			return 1
		fi
		ccnavi_wap_say "取り込めなかった（上の ccnavi-sync.sh の文面）。何も反映していません。接続と、上に出た原因を直してから再実行してください"
		return 1
	fi
	# 取り込みが、マージ済みの親のワークツリーを片付けて 0 で終わることがある。無いツリーには書かずに止める。
	if [ ! -d "$ccnavi_wap_tree" ]; then
		ccnavi_wap_say "親のワークツリーが取り込みの後に無い。親子のチケット ${ccnavi_wap_family_id} は閉じた（マージ済み）か、ワークツリーが無い。何も反映していません"
		log_info write-and-push で止めた -- "family=$ccnavi_wap_family_id" "reason=parent-tree-gone"
		return 1
	fi
	# 親のブランチがリモートから消えていれば、push で作り直さずに止める（push のゲート。3 経路で同じ）。
	ccnavi_cp_gate=0
	ccnavi_push_gate "$ccnavi_wap_tree" "$ccnavi_wap_branch" "$ccnavi_wap_family_id" "$ccnavi_wap_root" \
		"$(ccnavi_number_or "${CCNAVI_WRITE_AND_PUSH_TIMEOUT:-}" 60)" || ccnavi_cp_gate=$?
	if [ "$ccnavi_cp_gate" -ne 0 ]; then
		ccnavi_wap_say "${ccnavi_pg_why}。何も反映していません"
		log_info write-and-push で止めた -- "family=$ccnavi_wap_family_id" "reason=push-gate" "gate=$ccnavi_cp_gate"
		return 1
	fi
	return 0
}

# 3〜5。0 なら続けてよい。
ccnavi_wap_prepare() {
	# 3. write-and-push の外の変更を見分け、(b) を取り込みの前にコミットする。
	ccnavi_wap_sort "$ccnavi_wap_tmp/sort" || return 1
	ccnavi_wap_stops "$ccnavi_wap_tmp/sort" "ユーザの判断が未送信" \
		"。承認の push（sh $ccnavi_wap_sh/ccnavi-user-push-approved.sh ${ccnavi_wap_family_id}）をユーザが実行します。何も反映していません" \
		"置き場に ccnavi が把握していない変更があります" "。ユーザが確かめてください。何も反映していません" || return 1
	ccnavi_val b "$(cat "$ccnavi_wap_tmp/sort")" >"$ccnavi_wap_tmp/b"
	if [ -s "$ccnavi_wap_tmp/b" ]; then
		ccnavi_wap_commit "$ccnavi_wap_tmp/b" "ccnavi: $ccnavi_wap_family_id の hook のマーカーをコミットする" || {
			[ -z "$ccnavi_wap_refused" ] ||
				ccnavi_wap_say "コミットできなかった（制限時間で打ち切った場合を含む）。$ccnavi_wap_refused"
			return 1
		}
	fi
	ccnavi_wap_take_in || return 1
	# 親のブランチが一度も送られていない（ccnavi-start.sh を通さずに手で切った親）。5 の見分けは
	# origin の参照を基点にするので、参照が無いと差分を読めずに内部のエラーで終わる。先に事実を言って止める。
	# 送った形跡があるのに参照が無い親は、上の push のゲートと ccnavi-sync.sh の判定に任せる。
	ccnavi_parent_info "$ccnavi_wap_tree" "$ccnavi_wap_family_id" "$ccnavi_wap_branch" || :
	# 実行ファイルが答えない・値が unknown のときは、断定せず上のゲートに任せる
	if [ "$ccnavi_pi_parent" = yes ] && [ "$ccnavi_pi_approved" != unreadable ] &&
		[ "$ccnavi_pi_approved" != unknown ] && [ "$ccnavi_pi_trace" = no ]; then
		ccnavi_wap_unsent_parent=1
		ccnavi_wap_say "親のブランチ ${ccnavi_wap_branch} はリモートにありません。ccnavi-start.sh を通さずに切った親です。何も反映していません"
		log_info write-and-push で止めた -- "family=$ccnavi_wap_family_id" "reason=parent-never-pushed"
		return 1
	fi
	# 5. 未送信の置き場の変更（(b) 以外）が残っていれば止める。
	ccnavi_wap_sort "$ccnavi_wap_tmp/unsent" "refs/remotes/origin/$ccnavi_wap_branch" || return 1
	ccnavi_wap_stops "$ccnavi_wap_tmp/unsent" "置き場に未送信のユーザの判断のコミットがある" \
		"。承認の push（sh $ccnavi_wap_sh/ccnavi-user-push-approved.sh ${ccnavi_wap_family_id}）をユーザが実行します。何も反映していません" \
		"置き場に ccnavi が把握していない未送信のコミットがあります" \
		"。ユーザが確かめてください。前の状態の操作が送る前に強制終了されて残ったものなら、中身を確かめてから承認の push（sh $ccnavi_wap_sh/ccnavi-user-push-approved.sh ${ccnavi_wap_family_id}）で送るか、ユーザがそのコミットを取り除く。何も反映していません" || return 1
	return 0
}

# 一覧のパスだけをコミットする（実行ファイルの write-and-push commit）。<一覧> <文>
# 変わったものが 1 つも無ければコミットせずに 0（ccnavi_wap_committed は空）。失敗したら 1 で、git が断った
# 理由を ccnavi_wap_refused に入れる（前置きは呼ぶ側が付ける）。索引は実行ファイルが元に戻す。
ccnavi_wap_committed=""
ccnavi_wap_refused=""
ccnavi_wap_commit() {
	ccnavi_wap_committed=""
	ccnavi_wap_refused=""
	if ! ccnavi_wap_ask commit "$ccnavi_wap_tmp/commit" "$ccnavi_wap_tree" "$1" "$2"; then
		ccnavi_wap_say "$ccnavi_wap_noans。コミットしない"
		return 1
	fi
	ccnavi_wap_said "$ccnavi_wap_tmp/commit"
	ccnavi_cc_ans=$(cat "$ccnavi_wap_tmp/commit")
	ccnavi_wap_refused=$(ccnavi_val1 refused "$ccnavi_cc_ans")
	ccnavi_cc_sha=$(ccnavi_val1 commit "$ccnavi_cc_ans")
	[ -n "$ccnavi_cc_sha" ] || return 1
	[ "$ccnavi_cc_sha" = none ] || ccnavi_wap_committed="$ccnavi_cc_sha"
	return 0
}

# 6〜8 を実行ファイルの 1 回の起動で行う（一覧つき。合流するなら --record-merge）。<一覧> <文> <引数>...
# 答えは <一覧>.answer に書かれる（標準出力は状態の操作のもの）。
ccnavi_wap_run() {
	ccnavi_cr_list="$1"
	ccnavi_cr_message="$2"
	shift 2
	rm -f "$ccnavi_cr_list" "$ccnavi_cr_list.answer"
	if [ -n "$ccnavi_wap_merge_sha" ]; then
		set -- --record-merge "$ccnavi_wap_merge_sha" "$@"
	fi
	set -- --record-writes "$ccnavi_cr_list" --record-tree "$ccnavi_wap_tree" --record-commit "$ccnavi_cr_message" "$@"
	if [ -n "$ccnavi_wap_capture" ]; then
		ccnavi_wap_exe "$@" >"$ccnavi_wap_capture"
	else
		ccnavi_wap_exe "$@"
	fi
}
# 子の finish の合流の前の検査。<子の識別子>。0 なら続けてよい（合流するなら ccnavi_wap_merge_sha が入る）。
# 1 なら止めた（何も変えていない。ロックは外した）。答えられなければ止める側。
ccnavi_wap_merge_plan() {
	ccnavi_wap_merge_sha=""
	if ! ccnavi_wap_ask merge "$ccnavi_wap_tmp/merge" "$1"; then
		ccnavi_wap_say "$ccnavi_wap_noans。何も反映していません。実行ファイルを新しくしてください"
		ccnavi_lock_drop
		return 1
	fi
	ccnavi_pm_ans=$(cat "$ccnavi_wap_tmp/merge")
	ccnavi_pm_stop=$(ccnavi_val1 stop "$ccnavi_pm_ans")
	if [ -n "$ccnavi_pm_stop" ]; then
		ccnavi_wap_say "$ccnavi_pm_stop"
		ccnavi_val hint "$ccnavi_pm_ans" | sed 's/^/  - /' >&2
		log_info write-and-push で止めた -- "family=$ccnavi_wap_family_id" "reason=write-and-push-merge-stop"
		ccnavi_lock_drop
		return 1
	fi
	if [ "$(ccnavi_val1 merge "$ccnavi_pm_ans")" = yes ]; then
		ccnavi_wap_merge_sha=$(ccnavi_val1 head "$ccnavi_pm_ans")
		[ -n "$ccnavi_wap_merge_sha" ] || {
			ccnavi_wap_say "合流する子の先頭を読めない。何も反映していません"
			ccnavi_lock_drop
			return 1
		}
	fi
	return 0
}

# 親のブランチを push する（--force なし）。失敗したように見えても、ls-remote で <送る先頭> が届いていれば成功。
# <送る先頭>。0 なら届いた。ccnavi_wap_via に push（push が通った）か ls（応答は失ったが届いていた）を入れる。
# 失敗の 1 行目は ccnavi_wap_tmp/push-err にある。
ccnavi_wap_via=""
ccnavi_wap_push() {
	ccnavi_pu_timeout=$(ccnavi_number_or "${CCNAVI_WRITE_AND_PUSH_TIMEOUT:-}" 60)
	ccnavi_wap_via=push
	if ccnavi_git_timed "$ccnavi_pu_timeout" "$ccnavi_wap_tmp/push-err" "$ccnavi_wap_tree" \
		push --quiet origin "refs/heads/$ccnavi_wap_branch:refs/heads/$ccnavi_wap_branch" >/dev/null; then
		return 0
	fi
	ccnavi_wap_via=ls
	ccnavi_git_timed "$ccnavi_pu_timeout" "$ccnavi_wap_tmp/ls-err" "$ccnavi_wap_tree" \
		ls-remote origin "refs/heads/$ccnavi_wap_branch" >"$ccnavi_wap_tmp/ls" &&
		grep -F -x -q -- "$1${ccnavi_wap_tab}refs/heads/$ccnavi_wap_branch" "$ccnavi_wap_tmp/ls"
}

# 6〜11。<文> -- <実行ファイルの引数>...。終了コードは実行ファイルのもの（write-and-push で失敗したら 1）。
ccnavi_wap_write() {
	ccnavi_cw_message="$1"
	shift
	[ "${1:-}" = "--" ] && shift
	ccnavi_cw_dir="$ccnavi_wap_root/logs/state/write-and-push/$ccnavi_wap_repo"
	mkdir -p "$ccnavi_cw_dir" 2>/dev/null || :
	ccnavi_cw_list="$ccnavi_cw_dir/$ccnavi_wap_family_id.$$.writes"
	ccnavi_cw_answer="$ccnavi_cw_list.answer"
	ccnavi_cw_try=0
	while :; do
		ccnavi_cw_try=$((ccnavi_cw_try + 1))
		# 6〜8. 元の先頭・合流・書く・コミット（実行ファイルの 1 回の起動。失敗したら、その中で合流ごと戻す）。
		# 送る前に切られたら戻せるように、起こす前に答えのファイルを覚える（ccnavi_wap_end）。sh の trap は前景の子
		# （実行ファイル）が終わるのを待ってから動くので、起動の後に立てると、書き終えたコミットを戻し損ねる。
		# 答えにコミットが無ければ（失敗・送るものが無い・none）、ccnavi_wap_end は何もしない。
		ccnavi_wap_inflight="$ccnavi_cw_answer"
		ccnavi_cw_rc=0
		ccnavi_wap_run "$ccnavi_cw_list" "$ccnavi_cw_message" "$@" || ccnavi_cw_rc=$?
		if [ "$(head -n 1 "$ccnavi_cw_answer" 2>/dev/null)" != "write-and-push 1" ]; then
			ccnavi_wap_inflight=""
			ccnavi_wap_say "実行ファイルが write-and-push の答え（${ccnavi_cw_answer}）を書かなかった。書いたものとコミットが分からない。親のワークツリー（${ccnavi_wap_tree}）をユーザが確かめてください"
			rm -f "$ccnavi_cw_list"
			[ "$ccnavi_cw_rc" -ne 0 ] || ccnavi_cw_rc=1
			return "$ccnavi_cw_rc"
		fi
		ccnavi_wap_said "$ccnavi_cw_answer"
		if [ "$ccnavi_cw_rc" -ne 0 ]; then
			ccnavi_wap_inflight=""
			rm -f "$ccnavi_cw_list" "$ccnavi_cw_answer"
			return "$ccnavi_cw_rc"
		fi
		ccnavi_cw_ans=$(cat "$ccnavi_cw_answer")
		if [ "$(ccnavi_val1 push "$ccnavi_cw_ans")" = no ]; then
			ccnavi_wap_inflight=""
			rm -f "$ccnavi_cw_list" "$ccnavi_cw_answer"
			ccnavi_wap_note "route=unchanged"
			return 0 # 書いたものが無く、送るものも無い
		fi
		ccnavi_cw_c=$(ccnavi_val1 commit "$ccnavi_cw_ans")
		ccnavi_cw_head=$(ccnavi_val1 head "$ccnavi_cw_ans")
		[ "$ccnavi_cw_c" != none ] || ccnavi_wap_inflight=""
		# 9. push（--force なし）。10. 失敗したように見えても、届いていれば成功。
		if ccnavi_wap_push "$ccnavi_cw_head"; then
			[ "$ccnavi_wap_via" = push ] ||
				ccnavi_wap_say "push の応答を受け取れなかったが、リモートには届いていた（${ccnavi_cw_head}）"
			ccnavi_wap_inflight=""
			ccnavi_wap_sent "$ccnavi_cw_head" "$ccnavi_cw_answer"
			rm -f "$ccnavi_cw_list" "$ccnavi_cw_answer"
			return 0
		fi
		ccnavi_wap_say "push が通らなかった（$(head -n 1 "$ccnavi_wap_tmp/push-err" 2>/dev/null)）"
		log_info write-and-push で push が通らなかった -- "family=$ccnavi_wap_family_id" "try=$ccnavi_cw_try" "reason=write-and-push-push-rejected"
		# 11. 戻す。先頭が自分のコミットのときだけ（実行ファイルが比較つきで戻す）。
		if [ "$ccnavi_cw_c" != none ]; then
			ccnavi_wap_inflight=""
			if ! ccnavi_wap_undo "$ccnavi_cw_answer"; then
				rm -f "$ccnavi_cw_list" "$ccnavi_cw_answer"
				return 1
			fi
		fi
		rm -f "$ccnavi_cw_list"
		if [ "$ccnavi_cw_try" -ge 2 ]; then
			rm -f "$ccnavi_cw_answer"
			ccnavi_wap_say "2 回目も送れなかった。書いたものは戻した。オンラインで sh $ccnavi_wap_sh/ccnavi-sync.sh $ccnavi_wap_family_id を実行して取り込んでから、再実行してください"
			log_error write-and-push で送れなかった -- "family=$ccnavi_wap_family_id" "reason=write-and-push-push"
			return 1
		fi
		ccnavi_wap_say "書いたものを戻した。取り込みからもう 1 回だけやり直す"
		ccnavi_wap_prepare || {
			rm -f "$ccnavi_cw_answer"
			return 1
		}
		# 届いていたのに確かめ（ls-remote）も失敗していた回は、取り込みで自分のコミットが戻ってくる。
		# そのときは書き直さず、届いていたとして終える。
		if [ "$ccnavi_cw_c" != none ]; then
			ccnavi_cw_landed=$(ccnavi_val1 "landed yes" "$(ccnavi_wap_exe write-and-push landed "$ccnavi_cw_answer" </dev/null 2>/dev/null)")
			if [ -n "$ccnavi_cw_landed" ]; then
				rm -f "$ccnavi_cw_answer"
				ccnavi_wap_say "取り込み直すと、前の回のコミット（$(printf '%.12s' "$ccnavi_cw_c")）がリモートに届いていた。書き直さずに終える"
				ccnavi_wap_sent "$ccnavi_cw_landed" ""
				return 0
			fi
		fi
		rm -f "$ccnavi_cw_answer"
	done
}

# 自分のコミットを比較つきで戻す（実行ファイルの write-and-push undo）。<6〜8 の答えのファイル>。戻せたら 0。
ccnavi_wap_undo() {
	if ! ccnavi_wap_ask undo "$ccnavi_wap_tmp/undo" "$1"; then
		ccnavi_wap_say "$ccnavi_wap_noans。コミットを戻せなかった。ユーザが確かめてください"
		return 1
	fi
	ccnavi_wap_said "$ccnavi_wap_tmp/undo"
	[ "$(ccnavi_val1 undone "$(cat "$ccnavi_wap_tmp/undo")")" = yes ] || return 1
	log_info write-and-push で書いたものを戻した -- "family=$ccnavi_wap_family_id"
	return 0
}
# approval-request の結果ファイル（ccnavi-ticket.sh が書く）への申し送り。CCNAVI_PROPOSE_NOTE が無ければ何もしない。
# 書けなくても approval-request は止めない。
ccnavi_wap_note() {
	[ -z "${CCNAVI_PROPOSE_NOTE:-}" ] || printf '%s\n' "$1" >>"$CCNAVI_PROPOSE_NOTE" 2>/dev/null || :
}

# 送れた。<送った先頭> <6〜8 の答えのファイル（無ければ空）>
ccnavi_wap_sent() {
	ccnavi_wap_note "route=sent"
	ccnavi_wap_note "commit=$1"
	if [ -n "${CCNAVI_PROPOSE_NOTE:-}" ] && [ -n "$2" ]; then
		ccnavi_val file "$(cat "$2" 2>/dev/null)" | sed 's/^/file=/' >>"$CCNAVI_PROPOSE_NOTE" 2>/dev/null || :
	fi
	ccnavi_wap_say "親子のチケット $ccnavi_wap_family_id のチケットの状態を送った（$(printf '%.12s' "$1")）"
	log_info write-and-push で送った -- "family=$ccnavi_wap_family_id"
}

# 後始末。ロックを外し、一時ディレクトリを消す。2 度呼んでも害は無い。
ccnavi_wap_inflight=""
ccnavi_wap_end() {
	# タイムアウト監視（ccnavi_run_timed）の途中で切られたら、監視していた git（push）を止めて終わりを待ち、監視も
	# 止める。動いている途中のツリーを見て戻さないため、後で監視が別のプロセスを kill しないようにするため。
	ccnavi_run_timed_end
	if [ -n "$ccnavi_wap_inflight" ] && [ -n "$ccnavi_wap_tmp" ]; then
		ccnavi_ce_answer="$ccnavi_wap_inflight"
		ccnavi_wap_inflight=""
		ccnavi_ce_c=$(ccnavi_val1 commit "$(cat "$ccnavi_ce_answer" 2>/dev/null)")
		if [ -n "$ccnavi_ce_c" ] && [ "$ccnavi_ce_c" != none ]; then
			# 送る前に切られた。自分のコミットを実行ファイルに比較つきで戻させる（先頭が動いていれば戻さない）。
			ccnavi_wap_say "送る前に止められた。書いたものを戻す"
			ccnavi_wap_undo "$ccnavi_ce_answer" || :
		fi
		rm -f "${ccnavi_ce_answer%.answer}" "$ccnavi_ce_answer"
	fi
	ccnavi_lock_drop
	if [ -n "$ccnavi_wap_tmp" ]; then
		rm -rf "$ccnavi_wap_tmp"
		ccnavi_wap_tmp=""
	fi
	return 0
}

# ---- 置き場の変更のパスの読み（実行ファイルを起こさずに動く ccnavi-user-push-approved.sh が使う写し）
#
# 実行ファイル（wap の changedPaths の git.PorcelainPaths と、書きかけの一時ファイルの temp）と同じ読み方。一致は
# tests/fixtures/approved-carry.json で確かめる。

# `git status --porcelain -z` の出力（標準入力）から、パスを 1 行ずつ。改名を読まない（--no-renames）形だけを受ける。
ccnavi_porcelain_paths() {
	tr '\000' '\n' | sed -n 's/^...//p'
}

# 標準入力のパスのうち、書きかけの一時ファイル（fsio の `.<名前>.<一意>.part*`、フローの保存の `flows/.*.tmp`、
# 設定の同期（tickets/sync/mirror）の `*.ccnavi-sync`）でないものを 1 行ずつ。
ccnavi_drop_temp() {
	grep -v -E '(^|/)\.[^/]*\.part(\.[^/]*)?$|(^|/)flows/\.[^/]*\.tmp$|\.ccnavi-sync$' || :
}
