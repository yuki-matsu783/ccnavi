# ccnavi-common-c1.sh  ccnavi-common.sh が読む部品。単体では読まない（読み方は ccnavi-common.sh の冒頭）。
#
# C1。取り込み済みの親子のチケットの状態を書く一連の操作を 1 つにまとめる。

# ---- C1
#
# 取り込み済みの親子のチケットについて、チケットの状態を書く一連の操作を 1 つの操作にまとめる。親子のチケットは
# 親のブランチ 1 本に対応する。ここで対象にするのは、origin があり取り込み状態が present の親子のチケットのうち、
# chat だけのものを除いたもの。呼ぶ側（ccnavi-ticket.sh・ccnavi-review.sh）は次の順に関数を呼ぶ。
#
#   ccnavi_c1_family <識別子>   親子のチケットと、C1の対象か（ccnavi_c1_target に yes / no / stop）
#   ccnavi_c1_begin             1 ロック 2 途中の操作 3 C1の外の変更の見分けとコミット 4 取り込み 5 未送信の確かめ
#   ccnavi_c1_write <文> -- <実行ファイルの引数>...
#                               6 元の先頭 7 書く 8 コミット 9 push 10 届いたか 11 戻して 1 回だけやり直す
#   ccnavi_c1_end               ロックを外す（trap の EXIT・INT・TERM・HUP にも置く）
#
# 呼ぶ側が先に決めるもの。
#   ccnavi_c1_root   ワークスペースルート
#   ccnavi_c1_label  文面の頭（ccnavi-ticket など）
#   ccnavi_c1_exe    関数。実行ファイルを `--root <ルート>` つきで起こし、引数を渡す
#
# 文面はすべて標準エラーへ出す（ボードは標準出力の JSONを読むため）。実行ファイルの標準出力は
# そのまま通す。ccnavi_c1_capture に書き先を入れると、そこへ書く。
#
# 実行ファイルはネットワークに出ずコミットもしない。見分けと書いたパスの一覧は実行ファイルが
# 出し（`c1 family`・`c1 sort`・`--record-writes`）、取り込み（ccnavi-sync.shを入れ子のロックで起こす）・
# コミット（`commit --only`。ほかのステージ済みの変更を巻き込まない）・push・届いたかの確かめ・戻し（比較つきの update-ref。reset は
# 使わない）はここが持つ。git は ccnavi-git.shを通らずに直に呼ぶ。ccnavi-git.shはエージェントの入口の
# 保護で、ここの戻し（`restore --source` など）には当てない。
#
# 環境変数: CCNAVI_LOCK_WAIT（ロックを待つ秒、既定 120）/ CCNAVI_C1_TIMEOUT（push・ls-remote 1 回の
#   タイムアウトの秒、既定 60）/ CCNAVI_C1_COMMIT_TIMEOUT（コミット 1 回のタイムアウトの秒、既定 60）
#
# コミットは `--no-verify` でユーザの hook（pre-commit・commit-msg）を実行しない。コミットするのは状態のファイル
# だけで、コードの検査の対象ではないため。署名はユーザの設定に従うが、
# タイムアウトを付け、pinentry などが尋ねて止まりっぱなしにならないようにする（切れたら失敗）。
#
# 途中で INT・TERM・HUP が来たら、送る前の自分のコミットを戻す（ccnavi_c1_end）。強制終了（KILL）で
# 残ったコミットは、次の C1の 5 が「未送信」で止まり、戻し方を言う。

ccnavi_c1_target=""
ccnavi_c1_why=""
ccnavi_c1_family_id=""
# 親のブランチ名。ref・push・ls-remote・取り込み状態の `branch` はこれを使う。
ccnavi_c1_branch=""
ccnavi_c1_repo=""
ccnavi_c1_tree=""
ccnavi_c1_approved=""
ccnavi_c1_review=""
ccnavi_c1_tmp=""
ccnavi_c1_capture=""
ccnavi_c1_keep=""
ccnavi_c1_tab=$(printf '\t')

ccnavi_c1_say() {
	printf '%s: %s\n' "$ccnavi_c1_label" "$*" >&2
}

ccnavi_c1_scratch() {
	[ -z "$ccnavi_c1_tmp" ] || return 0
	ccnavi_c1_tmp=$(mktemp -d 2>/dev/null || mktemp -d -t ccnavi-c1) || return 1
}

ccnavi_c1_number() {
	case "${1:-}" in
	'' | *[!0-9]*) printf '%s\n' "$2" ;;
	*) printf '%s\n' "$1" ;;
	esac
}

# 親子のチケットと、C1の対象か。<識別子>
#
# ccnavi_c1_target: yes（C1 で回す）/ no（今の手元の動きのまま）/ stop（取り込み済みだが止める理由がある）。
# 実行ファイルが答えなかった（古い・落ちた）ときは、取り込み状態があれば stop、無ければ no。
ccnavi_c1_family() {
	ccnavi_c1_target=no
	ccnavi_c1_why=""
	ccnavi_c1_family_id=""
	ccnavi_c1_branch=""
	ccnavi_c1_repo=""
	ccnavi_c1_tree=""
	# 取り込み状態が 1 つも無ければ、実行ファイルに聞かずに対象外（一時ディレクトリも要らない）。
	ccnavi_cf_state=$(ccnavi_state "$ccnavi_c1_root")
	# 子の識別子（`<親>-<2 桁のフェーズ番号>-<2 桁の連番>`）なら、右から 2 段を剥がして親にする。
	ccnavi_cf_p="$1"
	case "$ccnavi_cf_p" in
	?*-[0-9][0-9]-[0-9][0-9]) ccnavi_cf_p="${ccnavi_cf_p%-[0-9][0-9]-[0-9][0-9]}" ;;
	esac
	ccnavi_cf_any=no
	for ccnavi_cf_rec in "$ccnavi_cf_state"/sync/*/families/"$ccnavi_cf_p"; do
		if [ -e "$ccnavi_cf_rec" ] || [ -L "$ccnavi_cf_rec" ]; then
			ccnavi_cf_any=yes
			break
		fi
	done
	if [ "$ccnavi_cf_any" = no ]; then
		ccnavi_c1_family_id="$ccnavi_cf_p"
		ccnavi_c1_why="親子のチケットの取り込み状態が無い（取り込み済みでない。今の手元の動きのまま）"
		return 0
	fi
	ccnavi_c1_scratch || {
		ccnavi_c1_target=stop
		ccnavi_c1_why="一時ディレクトリが作れない"
		return 0
	}
	: >"$ccnavi_c1_tmp/hints"
	# Windows の実行ファイルの CRLF を落として読む（1 行 1 項目の値の末尾に CR を残さない）。
	ccnavi_c1_exe c1 family "$1" 2>"$ccnavi_c1_tmp/family-err" </dev/null | tr -d '\r' >"$ccnavi_c1_tmp/family" || :
	if [ "$(head -n 1 "$ccnavi_c1_tmp/family" 2>/dev/null)" != "c1 1" ]; then
		ccnavi_c1_family_id="$ccnavi_cf_p"
		ccnavi_c1_target=stop
		ccnavi_c1_why="実行ファイルが C1の問い合わせ（c1 family）に応答しない（$(head -n 1 "$ccnavi_c1_tmp/family-err" 2>/dev/null)）。親子のチケットの取り込み状態があるので、書かずに止める。実行ファイルを新しくしてください"
		return 0
	fi
	ccnavi_c1_target=$(sed -n 's/^target //p' "$ccnavi_c1_tmp/family" | head -n 1)
	ccnavi_c1_why=$(sed -n 's/^why //p' "$ccnavi_c1_tmp/family" | head -n 1)
	ccnavi_c1_family_id=$(sed -n 's/^family //p' "$ccnavi_c1_tmp/family" | head -n 1)
	ccnavi_c1_repo=$(sed -n 's/^repo //p' "$ccnavi_c1_tmp/family" | head -n 1)
	ccnavi_c1_tree=$(sed -n 's/^tree //p' "$ccnavi_c1_tmp/family" | head -n 1)
	ccnavi_c1_approved=$(sed -n 's/^approved //p' "$ccnavi_c1_tmp/family" | head -n 1)
	ccnavi_c1_review=$(sed -n 's/^review //p' "$ccnavi_c1_tmp/family" | head -n 1)
	ccnavi_c1_branch=$(sed -n 's/^branch //p' "$ccnavi_c1_tmp/family" | head -n 1)
	sed -n 's/^hint //p' "$ccnavi_c1_tmp/family" >"$ccnavi_c1_tmp/hints"
	case "$ccnavi_c1_target" in
	yes)
		if ! ccnavi_branch_ok "$ccnavi_c1_branch" "$ccnavi_c1_family_id"; then
			ccnavi_c1_target=stop
			ccnavi_c1_why="実行ファイルが親のブランチ名（branch）を答えないか、ブランチ名の形でない（${ccnavi_c1_branch:-空}）。実行ファイルを新しくしてください"
		elif [ -z "$ccnavi_c1_tree" ] || [ ! -d "$ccnavi_c1_tree" ]; then
			ccnavi_c1_target=stop
			ccnavi_c1_why="親のワークツリーが決まらない"
		elif ! git -C "$ccnavi_c1_tree" remote get-url origin >/dev/null 2>&1; then
			ccnavi_c1_target=no
			ccnavi_c1_why="origin が無い（今の手元の動きのまま）"
		fi
		;;
	no | stop) ;;
	*)
		ccnavi_c1_target=stop
		ccnavi_c1_why="実行ファイルの出力（target）を読めない"
		;;
	esac
	log_debug C1の対象を決めた -- "family=$ccnavi_c1_family_id" "target=$ccnavi_c1_target"
	return 0
}

# 止めたときの文面（ccnavi_c1_target が stop）。
ccnavi_c1_refuse() {
	ccnavi_c1_say "親子のチケット ${ccnavi_c1_family_id:-?} のチケットの状態を書かずに止めた。${ccnavi_c1_why}"
	while IFS= read -r ccnavi_rf_hint; do
		[ -n "$ccnavi_rf_hint" ] && printf '  %s\n' "$ccnavi_rf_hint" >&2
	done <"$ccnavi_c1_tmp/hints"
	log_info C1 で止めた -- "family=$ccnavi_c1_family_id" "reason=c1-stop"
}

# `git rev-parse --git-path <名前>` をツリーからのパスにする。<ツリー> <名前>
ccnavi_c1_git_path() {
	ccnavi_gp_rel=$(git -C "$1" rev-parse --git-path "$2" 2>/dev/null || :)
	case "$ccnavi_gp_rel" in
	'') printf '%s\n' "$1/.git/$2" ;;
	/* | [A-Za-z]:*) printf '%s\n' "$ccnavi_gp_rel" ;;
	*) printf '%s\n' "$1/$ccnavi_gp_rel" ;;
	esac
}

# 途中の操作の名前（無ければ空）。<ツリー>
ccnavi_c1_busy() {
	for ccnavi_cb_name in MERGE_HEAD CHERRY_PICK_HEAD REVERT_HEAD rebase-merge rebase-apply sequencer index.lock; do
		if [ -e "$(ccnavi_c1_git_path "$1" "$ccnavi_cb_name")" ]; then
			printf '%s\n' "$ccnavi_cb_name"
			return 0
		fi
	done
	return 0
}

# 1〜5。0 なら書いてよい（ロックを持ったまま返る）。1 なら止めた（ロックは外した）。
ccnavi_c1_begin() {
	ccnavi_cb_wait=$(ccnavi_c1_number "${CCNAVI_LOCK_WAIT:-}" 120)
	ccnavi_cb_rc=0
	ccnavi_lock_take "$ccnavi_c1_root" "$ccnavi_c1_repo" "$ccnavi_c1_family_id" "$ccnavi_cb_wait" || ccnavi_cb_rc=$?
	case "$ccnavi_cb_rc" in
	0) ;;
	2)
		ccnavi_c1_say "親子のチケット $ccnavi_c1_family_id の古いロックを強制取得する途中で止まり、元に戻せなかった。ユーザが中身を見て片付ける（$(ccnavi_state "$ccnavi_c1_root")/locks/$ccnavi_c1_repo/${ccnavi_c1_family_id}）"
		return 1
		;;
	*)
		ccnavi_cb_lock="$(ccnavi_state "$ccnavi_c1_root")/locks/$ccnavi_c1_repo/$ccnavi_c1_family_id"
		if ccnavi_lock_long "$ccnavi_cb_lock"; then
			ccnavi_c1_say "親子のチケット $ccnavi_c1_family_id のロックが 10 分を超えて取られたままになっている。持ち主はまだ動いているので強制取得しない。終わるのを待つか、持ち主をユーザが確かめてください。$(ccnavi_lock_describe "$ccnavi_cb_lock")"
		else
			ccnavi_c1_say "親子のチケット $ccnavi_c1_family_id のロックを他の操作が持っている（$(ccnavi_lock_owner "$ccnavi_cb_lock")）。終わってから打ち直してください"
		fi
		return 1
		;;
	esac
	ccnavi_cb_busy=$(ccnavi_c1_busy "$ccnavi_c1_tree")
	if [ -n "$ccnavi_cb_busy" ]; then
		ccnavi_c1_say "親のワークツリーに途中の操作（${ccnavi_cb_busy}）がある。済ませるか取りやめてから打ち直してください（何も書いていない）"
		ccnavi_lock_drop
		return 1
	fi
	if ! ccnavi_c1_prepare; then
		ccnavi_lock_drop
		return 1
	fi
	return 0
}

# 置き場の変更を見分ける。<出力> [<版>]。答えられなければ 1。
ccnavi_c1_sort() {
	ccnavi_c1_exe c1 sort "$ccnavi_c1_family_id" ${2:+"$2"} 2>"$ccnavi_c1_tmp/sort-err" </dev/null | tr -d '\r' >"$1" || :
	if [ "$(head -n 1 "$1" 2>/dev/null)" != "c1 1" ]; then
		ccnavi_c1_say "置き場の変更を見分けられなかった（$(head -n 1 "$ccnavi_c1_tmp/sort-err" 2>/dev/null)）。何も書いていない"
		return 1
	fi
	return 0
}

# 見分けで止めるものがあれば言って 1。<見分けの出力> <c の前置き> <c の後書き> <d の前置き> <d の後書き>
ccnavi_c1_stops() {
	ccnavi_cs_c=$(sed -n 's/^c //p' "$1" | tr '\n' ' ' | sed 's/ *$//')
	ccnavi_cs_d=$(sed -n 's/^d //p' "$1" | tr '\n' ' ' | sed 's/ *$//')
	[ -z "$ccnavi_cs_c$ccnavi_cs_d" ] && return 0
	[ -n "$ccnavi_cs_c" ] && ccnavi_c1_say "$2（${ccnavi_cs_c}）$3"
	[ -n "$ccnavi_cs_d" ] && ccnavi_c1_say "$4（${ccnavi_cs_d}）$5"
	sed -n 's/^why /  - /p' "$1" >&2
	log_info C1 で止めた -- "family=$ccnavi_c1_family_id" "reason=c1-unknown-change"
	return 1
}

# 3〜5。0 なら続けてよい。
ccnavi_c1_prepare() {
	ccnavi_cp_try=0
	while :; do
		ccnavi_cp_try=$((ccnavi_cp_try + 1))
		# 3. C1の外の変更を見分け、(b) を取り込みの前にコミットする。
		ccnavi_c1_sort "$ccnavi_c1_tmp/sort" || return 1
		ccnavi_c1_stops "$ccnavi_c1_tmp/sort" "ユーザの判断が未送信" \
			"。承認の push（sh $ccnavi_c1_sh/ccnavi-push-approved.sh ${ccnavi_c1_family_id}）をユーザが打つ。何も書いていない" \
			"置き場に ccnaviの知らない変更がある" "。ユーザが確かめてください。何も書いていない" || return 1
		sed -n 's/^keep //p' "$ccnavi_c1_tmp/sort" >"$ccnavi_c1_tmp/keep"
		sed -n 's/^b //p' "$ccnavi_c1_tmp/sort" >"$ccnavi_c1_tmp/b"
		if [ -s "$ccnavi_c1_tmp/b" ]; then
			ccnavi_c1_commit "$ccnavi_c1_tmp/b" "ccnavi: $ccnavi_c1_family_id の hookのマーカーと状態の履歴をコミットする" || return 1
		fi
		# 4. 取り込み（ccnavi-sync.sh。ロックは入れ子で渡る）。統合先の取り込み結果も同じ回で書く。
		ccnavi_cp_rc=0
		sh "$ccnavi_c1_sh/ccnavi-sync.sh" "$ccnavi_c1_family_id" </dev/null >"$ccnavi_c1_tmp/sync" 2>&1 || ccnavi_cp_rc=$?
		sed "s/^/  /" "$ccnavi_c1_tmp/sync" >&2
		if [ "$ccnavi_cp_rc" -ne 0 ]; then
			# 3 と 4 の間に hook が書いた（mergeが書きかけと重なった）なら、3 から 1 回だけやり直す。
			if [ "$ccnavi_cp_try" -eq 1 ] && grep -q '書きかけの' "$ccnavi_c1_tmp/sync" 2>/dev/null; then
				ccnavi_c1_say "取り込みが書きかけと重なった。変更の見分けからもう 1 回だけやり直す"
				continue
			fi
			ccnavi_c1_say "取り込めなかった（上の ccnavi-sync.sh の文面）。何も書いていない。接続と、上に出た原因を直してから打ち直してください"
			return 1
		fi
		ccnavi_cp_record=$(ccnavi_family_record "$ccnavi_c1_root" "$ccnavi_c1_repo" "$ccnavi_c1_family_id")
		ccnavi_cp_state=$(ccnavi_record_get "$ccnavi_cp_record" state)
		if [ "$ccnavi_cp_state" != present ]; then
			ccnavi_c1_say "取り込みの後、親子のチケットの取り込み状態が ${ccnavi_cp_state:-（無い）} になった。何も書いていない（上の ccnavi-sync.sh の文面）"
			return 1
		fi
		# 5. 未送信の置き場の変更（(b) 以外）が残っていれば止める（REQ-APV-11 の補足）。
		ccnavi_c1_sort "$ccnavi_c1_tmp/unsent" "refs/remotes/origin/$ccnavi_c1_branch" || return 1
		ccnavi_c1_stops "$ccnavi_c1_tmp/unsent" "置き場に未送信のユーザの判断のコミットがある" \
			"。承認の push（sh $ccnavi_c1_sh/ccnavi-push-approved.sh ${ccnavi_c1_family_id}）をユーザが打つ。何も書いていない" \
			"置き場に ccnaviの知らない未送信のコミットがある" \
			"。ユーザが確かめてください。前の状態の操作が送る前に強制終了されて残ったものなら、中身を確かめてから承認の push（sh $ccnavi_c1_sh/ccnavi-push-approved.sh ${ccnavi_c1_family_id}）で送るか、ユーザがそのコミットを取り除く。何も書いていない" || return 1
		return 0
	done
}

# 一覧のパスだけをコミットする。<一覧> <文>。新しいファイルは先に add する。
# 変わったものが 1 つも無ければコミットせずに 0（ccnavi_c1_committed は空）。
# 落ちたら、この実行が add したパスを索引から外して 1（索引を元に戻す）。
ccnavi_c1_committed=""
ccnavi_c1_commit() {
	ccnavi_c1_committed=""
	: >"$ccnavi_c1_tmp/paths"
	: >"$ccnavi_c1_tmp/added"
	ccnavi_cc_base=$(git -C "$ccnavi_c1_tree" rev-parse --verify HEAD 2>/dev/null) || {
		ccnavi_c1_say "親のワークツリーの先頭を読めない。コミットしない"
		return 1
	}
	while IFS= read -r ccnavi_cc_path; do
		[ -n "$ccnavi_cc_path" ] || continue
		case "$ccnavi_cc_path" in
		/* | [A-Za-z]:*) continue ;;
		esac
		if [ -e "$ccnavi_c1_tree/$ccnavi_cc_path" ] || [ -L "$ccnavi_c1_tree/$ccnavi_cc_path" ]; then
			if [ -z "$(git -C "$ccnavi_c1_tree" ls-files -- ":(literal)$ccnavi_cc_path" 2>/dev/null)" ]; then
				if ! git -C "$ccnavi_c1_tree" add -- ":(literal)$ccnavi_cc_path" 2>"$ccnavi_c1_tmp/err"; then
					ccnavi_c1_say "$ccnavi_cc_path を addできなかった（$(head -n 1 "$ccnavi_c1_tmp/err")）"
					ccnavi_c1_unstage "$ccnavi_c1_tmp/added" "$ccnavi_cc_base"
					return 1
				fi
				printf '%s\n' "$ccnavi_cc_path" >>"$ccnavi_c1_tmp/added"
			fi
		elif [ -z "$(git -C "$ccnavi_c1_tree" ls-files -- ":(literal)$ccnavi_cc_path" 2>/dev/null)" ]; then
			continue # 作って消した（gitの知らない）パス
		fi
		printf '%s\n' "$ccnavi_cc_path" >>"$ccnavi_c1_tmp/paths"
	done <"$1"
	[ -s "$ccnavi_c1_tmp/paths" ] || return 0
	sed 's/^/:(literal)/' "$ccnavi_c1_tmp/paths" | tr '\n' '\000' >"$ccnavi_c1_tmp/pathspec"
	if ! xargs -0 git -C "$ccnavi_c1_tree" status --porcelain --untracked-files=all -- \
		<"$ccnavi_c1_tmp/pathspec" >"$ccnavi_c1_tmp/status" 2>"$ccnavi_c1_tmp/err"; then
		ccnavi_c1_say "gitの状態を読めない（$(head -n 1 "$ccnavi_c1_tmp/err")）。コミットしない"
		ccnavi_c1_unstage "$ccnavi_c1_tmp/added" "$ccnavi_cc_base"
		return 1
	fi
	[ -s "$ccnavi_c1_tmp/status" ] || return 0
	# ユーザの hook は実行しない。署名などで尋ねて止まらないよう、タイムアウトで切る。
	if ! ccnavi_git_timed "$(ccnavi_c1_number "${CCNAVI_C1_COMMIT_TIMEOUT:-}" 60)" "$ccnavi_c1_tmp/err" "$ccnavi_c1_tree" \
		commit --quiet --only --no-verify -m "$2" \
		--pathspec-from-file="$ccnavi_c1_tmp/pathspec" --pathspec-file-nul >"$ccnavi_c1_tmp/out"; then
		cat "$ccnavi_c1_tmp/out" >>"$ccnavi_c1_tmp/err"
		ccnavi_c1_say "コミットできなかった（制限時間で打ち切った場合を含む）。$(ccnavi_git_refusal "$ccnavi_c1_tmp/err")"
		ccnavi_c1_unstage "$ccnavi_c1_tmp/added" "$ccnavi_cc_base"
		return 1
	fi
	ccnavi_c1_committed=$(git -C "$ccnavi_c1_tree" rev-parse HEAD)
	return 0
}

# 一覧のパスの索引を <版> に合わせる（<版> に無ければ索引から外す）。<一覧> <版>
ccnavi_c1_unstage() {
	while IFS= read -r ccnavi_cn_path; do
		[ -n "$ccnavi_cn_path" ] || continue
		git -C "$ccnavi_c1_tree" restore --staged --source="$2" -- ":(literal)$ccnavi_cn_path" 2>/dev/null ||
			git -C "$ccnavi_c1_tree" rm --cached --quiet --ignore-unmatch -- ":(literal)$ccnavi_cn_path" >/dev/null 2>&1 ||
			ccnavi_c1_say "$ccnavi_cn_path の索引を戻せなかった"
	done <"$1"
}

# 書いた一覧のうち、この実行で書いたパスの作業ツリーの中身を <版> に戻す（無ければ消す）。<一覧> <版>
# record-risk の記録など、この実行の前から未コミットだったもの（keep）は触らない。
ccnavi_c1_restore_written() {
	while IFS= read -r ccnavi_cr_path; do
		[ -n "$ccnavi_cr_path" ] || continue
		case "$ccnavi_cr_path" in
		/* | [A-Za-z]:*) continue ;;
		esac
		grep -F -x -q -- "$ccnavi_cr_path" "$ccnavi_c1_tmp/keep" 2>/dev/null && continue
		if git -C "$ccnavi_c1_tree" cat-file -e "$2:$ccnavi_cr_path" 2>/dev/null; then
			git -C "$ccnavi_c1_tree" restore --worktree --source="$2" -- ":(literal)$ccnavi_cr_path" 2>/dev/null ||
				ccnavi_c1_say "$ccnavi_cr_path を書く前の状態に戻せなかった"
		else
			rm -f "$ccnavi_c1_tree/$ccnavi_cr_path" 2>/dev/null ||
				ccnavi_c1_say "$ccnavi_cr_path を消せなかった"
		fi
	done <"$1"
}

# 実行ファイルを 1 回（一覧つき）。<一覧> <引数>...
ccnavi_c1_run() {
	ccnavi_cr_list="$1"
	shift
	rm -f "$ccnavi_cr_list"
	if [ -n "$ccnavi_c1_capture" ]; then
		ccnavi_c1_exe --record-writes "$ccnavi_cr_list" --record-tree "$ccnavi_c1_tree" "$@" >"$ccnavi_c1_capture"
	else
		ccnavi_c1_exe --record-writes "$ccnavi_cr_list" --record-tree "$ccnavi_c1_tree" "$@"
	fi
}

# 6〜11。<文> -- <実行ファイルの引数>...。終了コードは実行ファイルのもの（C1 で落ちたら 1）。
ccnavi_c1_write() {
	ccnavi_cw_message="$1"
	shift
	[ "${1:-}" = "--" ] && shift
	ccnavi_cw_dir="$ccnavi_c1_root/logs/state/c1/$ccnavi_c1_repo"
	mkdir -p "$ccnavi_cw_dir" 2>/dev/null || :
	ccnavi_cw_list="$ccnavi_cw_dir/$ccnavi_c1_family_id.$$.writes"
	ccnavi_cw_timeout=$(ccnavi_c1_number "${CCNAVI_C1_TIMEOUT:-}" 60)
	ccnavi_cw_try=0
	while :; do
		ccnavi_cw_try=$((ccnavi_cw_try + 1))
		# 6. 元の先頭。
		ccnavi_cw_h0=$(git -C "$ccnavi_c1_tree" rev-parse --verify HEAD 2>/dev/null) || {
			ccnavi_c1_say "親のワークツリーの先頭を読めない。何も書いていない"
			return 1
		}
		# 7. 書く。
		ccnavi_cw_rc=0
		ccnavi_c1_run "$ccnavi_cw_list" "$@" || ccnavi_cw_rc=$?
		if [ ! -f "$ccnavi_cw_list" ]; then
			ccnavi_c1_say "実行ファイルが書いたパスの一覧を出さなかった。書いたものが分からないのでコミットしない。親のワークツリー（${ccnavi_c1_tree}）をユーザが確かめてください"
			return 1
		fi
		if [ "$ccnavi_cw_rc" -ne 0 ]; then
			# 落ちた回は、ここまでに書いたものを戻す（コミットしない）。
			ccnavi_c1_restore_written "$ccnavi_cw_list" "$ccnavi_cw_h0"
			rm -f "$ccnavi_cw_list"
			return "$ccnavi_cw_rc"
		fi
		# 8. コミット。
		if ! ccnavi_c1_commit "$ccnavi_cw_list" "$ccnavi_cw_message"; then
			ccnavi_c1_restore_written "$ccnavi_cw_list" "$ccnavi_cw_h0"
			rm -f "$ccnavi_cw_list"
			return 1
		fi
		ccnavi_cw_c="$ccnavi_c1_committed"
		# 送る前に切られたら戻せるように覚えておく（ccnavi_c1_end）。
		if [ -n "$ccnavi_cw_c" ]; then
			ccnavi_c1_inflight="$ccnavi_cw_c"
			ccnavi_c1_inflight_h0="$ccnavi_cw_h0"
			ccnavi_c1_inflight_list="$ccnavi_cw_list"
		fi
		if [ -z "$ccnavi_cw_c" ] && [ "$(git -C "$ccnavi_c1_tree" rev-parse HEAD)" = "$(git -C "$ccnavi_c1_tree" rev-parse --verify -q "refs/remotes/origin/$ccnavi_c1_branch" 2>/dev/null)" ]; then
			rm -f "$ccnavi_cw_list"
			return 0 # 書いたものが無く、送るものも無い
		fi
		ccnavi_cw_head=$(git -C "$ccnavi_c1_tree" rev-parse HEAD)
		# 9. push（--force なし）。
		if ccnavi_git_timed "$ccnavi_cw_timeout" "$ccnavi_c1_tmp/push-err" "$ccnavi_c1_tree" \
			push --quiet origin "refs/heads/$ccnavi_c1_branch:refs/heads/$ccnavi_c1_branch" >/dev/null; then
			ccnavi_c1_inflight=""
			ccnavi_c1_sent "$ccnavi_cw_head"
			rm -f "$ccnavi_cw_list"
			return 0
		fi
		# 10. 落ちたように見えても、届いていれば成功。
		if ccnavi_git_timed "$ccnavi_cw_timeout" "$ccnavi_c1_tmp/ls-err" "$ccnavi_c1_tree" \
			ls-remote origin "refs/heads/$ccnavi_c1_branch" >"$ccnavi_c1_tmp/ls" &&
			grep -F -x -q -- "$ccnavi_cw_head${ccnavi_c1_tab}refs/heads/$ccnavi_c1_branch" "$ccnavi_c1_tmp/ls"; then
			ccnavi_c1_say "push の応答を受け取れなかったが、リモートには届いていた（${ccnavi_cw_head}）"
			ccnavi_c1_inflight=""
			ccnavi_c1_sent "$ccnavi_cw_head"
			rm -f "$ccnavi_cw_list"
			return 0
		fi
		ccnavi_c1_say "push が通らなかった（$(head -n 1 "$ccnavi_c1_tmp/push-err" 2>/dev/null)）"
		# 11. 戻す。先頭が自分のコミットのときだけ。
		if [ -n "$ccnavi_cw_c" ]; then
			ccnavi_c1_inflight=""
			if ! ccnavi_c1_undo "$ccnavi_cw_c" "$ccnavi_cw_h0" "$ccnavi_cw_list"; then
				rm -f "$ccnavi_cw_list"
				return 1
			fi
		fi
		rm -f "$ccnavi_cw_list"
		if [ "$ccnavi_cw_try" -ge 2 ]; then
			ccnavi_c1_say "2 回目も送れなかった。書いたものは戻した。オンラインで sh $ccnavi_c1_sh/ccnavi-sync.sh $ccnavi_c1_family_id を打って取り込んでから、打ち直してください"
			log_info C1 で送れなかった -- "family=$ccnavi_c1_family_id" "reason=c1-push"
			return 1
		fi
		ccnavi_c1_say "書いたものを戻した。取り込みからもう 1 回だけやり直す"
		ccnavi_c1_prepare || return 1
		# 届いていたのに確かめ（ls-remote）も落ちていた回は、取り込みで自分のコミットが戻ってくる。
		# そのときは書き直さず、届いていたとして終える。
		if [ -n "$ccnavi_cw_c" ] &&
			git -C "$ccnavi_c1_tree" merge-base --is-ancestor "$ccnavi_cw_c" HEAD 2>/dev/null; then
			ccnavi_c1_say "取り込み直すと、前の回のコミット（$(printf '%.12s' "$ccnavi_cw_c")）がリモートに届いていた。書き直さずに終える"
			ccnavi_c1_sent "$(git -C "$ccnavi_c1_tree" rev-parse HEAD)"
			return 0
		fi
	done
}

# 比較つきで戻す。<自分のコミット> <元の先頭> <一覧>
ccnavi_c1_undo() {
	ccnavi_cu_now=$(git -C "$ccnavi_c1_tree" rev-parse HEAD 2>/dev/null || :)
	if [ "$ccnavi_cu_now" != "$1" ]; then
		ccnavi_c1_say "先頭が自分のコミット（$1）でなくなっていた（${ccnavi_cu_now}）。戻さずに止めた。ユーザが確かめてください"
		return 1
	fi
	git -C "$ccnavi_c1_tree" diff-tree --no-commit-id --name-only -r -z "$1" 2>/dev/null |
		tr '\000' '\n' >"$ccnavi_c1_tmp/in-commit"
	if ! git -C "$ccnavi_c1_tree" update-ref "refs/heads/$ccnavi_c1_branch" "$2" "$1" 2>"$ccnavi_c1_tmp/err"; then
		ccnavi_c1_say "コミットを戻せなかった（$(head -n 1 "$ccnavi_c1_tmp/err")）。ユーザが確かめてください"
		return 1
	fi
	ccnavi_c1_unstage "$ccnavi_c1_tmp/in-commit" "$2"
	ccnavi_c1_inflight=""
	ccnavi_c1_restore_written "$3" "$2"
	log_info C1 で書いたものを戻した -- "family=$ccnavi_c1_family_id"
	return 0
}

# 送れた。親子のチケットの取り込み状態の sha を書き換える（state はそのまま present）。<送った先頭>
ccnavi_c1_sent() {
	ccnavi_cn_record=$(ccnavi_family_record "$ccnavi_c1_root" "$ccnavi_c1_repo" "$ccnavi_c1_family_id")
	if [ "$(ccnavi_record_get "$ccnavi_cn_record" state)" = present ]; then
		ccnavi_record_write "$ccnavi_cn_record" remote origin branch "$ccnavi_c1_branch" sha "$1" \
			fetched_at "$(ccnavi_record_get "$ccnavi_cn_record" fetched_at)" state present reason "" ||
			ccnavi_c1_say "親子のチケットの取り込み状態（${ccnavi_cn_record}）を書けなかった"
	fi
	ccnavi_c1_say "親子のチケット $ccnavi_c1_family_id のチケットの状態を送った（$(printf '%.12s' "$1")）"
	log_info C1 で送った -- "family=$ccnavi_c1_family_id"
}

# 後始末。ロックを外し、一時ディレクトリを消す。2 度呼んでも害は無い。
ccnavi_c1_inflight=""
ccnavi_c1_inflight_h0=""
ccnavi_c1_inflight_list=""
ccnavi_c1_end() {
	# タイムアウト監視の途中で切られたら、監視も止める（後で別のプロセスを kill しないように）。
	if [ -n "${ccnavi_gt_dog:-}" ]; then
		kill "$ccnavi_gt_dog" 2>/dev/null || :
		ccnavi_gt_dog=""
	fi
	if [ -n "$ccnavi_c1_inflight" ] && [ -n "$ccnavi_c1_tmp" ]; then
		# 送る前に切られた。自分のコミットを比較つきで戻す（先頭が動いていれば戻さない）。
		ccnavi_ce_c="$ccnavi_c1_inflight"
		ccnavi_c1_inflight=""
		ccnavi_c1_say "送る前に止められた。書いたものを戻す"
		ccnavi_c1_undo "$ccnavi_ce_c" "$ccnavi_c1_inflight_h0" "$ccnavi_c1_inflight_list" || :
		rm -f "$ccnavi_c1_inflight_list"
	fi
	ccnavi_lock_drop
	if [ -n "$ccnavi_c1_tmp" ]; then
		rm -rf "$ccnavi_c1_tmp"
		ccnavi_c1_tmp=""
	fi
	return 0
}
