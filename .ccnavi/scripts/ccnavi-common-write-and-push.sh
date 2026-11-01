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
#   ccnavi_wap_write <文> -- <実行ファイルの引数>...
#                               6 元の先頭 7 書く 8 コミット 9 push 10 届いたか 11 戻して 1 回だけやり直す
#   ccnavi_wap_merge_plan <子>   子の finish だけ。begin の後、write の前に呼ぶ。子を親のブランチへ合流してよいかを
#                               実行ファイルに聞く（write-and-push merge）。止めるなら 1（何も変えず、ロックも外した）。
#                               合流するなら write が 7 の前に合流する（下の「合流」）
#   ccnavi_wap_end               ロックを外す（trap の EXIT・INT・TERM・HUP にも置く）
#
# 合流（子の finish）。write の 7 の前に、親のワークツリーで子の先頭を `merge --no-ff --no-commit` で入れる。
# 衝突したら merge を取りやめて何も変えずに止める。入れたら 7（完了の記録と子の merged_sha）を書き、8 は
# 合流と状態を 1 つのマージコミットにする（commit --only は合流の途中では使えないので、書いたパスを add して commit）。
# 9 以降（push・届いたかの確かめ・戻し）は同じ。戻す範囲は「元の先頭から最終の先頭まで」で、
# `git diff --name-only <元の先頭> <コミット>` のパスの索引とワークツリーを戻す。コミットの前に失敗・中断したときは
# merge --abort と書いたものの復元で、合流ごと元に戻す。
# 呼ぶ側が先に決めるもの。
#   ccnavi_wap_root   ワークスペースルート
#   ccnavi_wap_label  文面の頭（ccnavi-ticket など）
#   ccnavi_wap_exe    関数。実行ファイルを `--root <ルート>` つきで起こし、引数を渡す
#                    実行ファイルの探し方がほかの sh と同じでよければ、ccnavi_wap_use_exe <ルート> で決める
#
# 文面はすべて標準エラーへ出す（ボードは標準出力の JSON を読むため）。実行ファイルの標準出力は
# そのまま通す。ccnavi_wap_capture に書き先を入れると、そこへ書く。
#
# 実行ファイルはネットワークに出ずコミットもしない。見分けと書いたパスの一覧は実行ファイルが
# 出し（`write-and-push family`・`write-and-push sort`・`--record-writes`）、取り込み（ccnavi-sync.sh を入れ子のロックで起こす）・
# コミット（`commit --only`。ほかのステージ済みの変更を巻き込まない）・push・届いたかの確かめ・戻し（比較つきの update-ref。reset は
# 使わない）はここが持つ。git は ccnavi-git.sh を通らずに直に呼ぶ。ccnavi-git.sh はエージェントの入口の
# 保護で、ここの戻し（`restore --source` など）には適用しない。
#
# 環境変数: CCNAVI_LOCK_WAIT（ロックを待つ秒、既定 120）/ CCNAVI_WRITE_AND_PUSH_TIMEOUT（push・ls-remote 1 回の
#   タイムアウトの秒、既定 60）/ CCNAVI_WRITE_AND_PUSH_COMMIT_TIMEOUT（コミット 1 回のタイムアウトの秒、既定 60）
#
# コミットは `--no-verify` でユーザの hook（pre-commit・commit-msg）を実行しない。コミットするのは状態のファイル
# だけで、コードの検査の対象ではないため。署名はユーザの設定に従うが、
# タイムアウトを付け、pinentry などが尋ねて止まりっぱなしにならないようにする（切れたら失敗）。
#
# 途中で INT・TERM・HUP が来たら、送る前の自分のコミットを戻す（ccnavi_wap_end）。強制終了（KILL）で
# 残ったコミットは、次の write-and-push の 5 が「未送信」で止まり、戻し方を言う。

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
ccnavi_wap_keep=""
ccnavi_wap_tab=$(printf '\t')
# 合流する子の先頭（空なら合流しない）と子の識別子。ccnavi_wap_merge_plan が決める。
ccnavi_wap_merge_sha=""
ccnavi_wap_merge_child=""
# 合流の途中（merge --no-commit のあと、コミットか取りやめまで）か、その元の先頭と書いたパスの一覧。
ccnavi_wap_merging=""
ccnavi_wap_merging_h0=""
ccnavi_wap_merging_list=""
# 親のブランチが一度も送られていないために止めたら 1。呼び出し元が次の一手を足す（共通関数は事実だけを言う）。
ccnavi_wap_unsent_parent=""

ccnavi_wap_say() {
	printf '%s: %s\n' "$ccnavi_wap_label" "$*" >&2
}

ccnavi_wap_scratch() {
	[ -z "$ccnavi_wap_tmp" ] || return 0
	ccnavi_wap_tmp=$(mktemp -d 2>/dev/null || mktemp -d -t ccnavi-write-and-push) || return 1
}

ccnavi_wap_number() {
	case "${1:-}" in
	'' | *[!0-9]*) printf '%s\n' "$2" ;;
	*) printf '%s\n' "$1" ;;
	esac
}

# ccnavi_wap_exe を決める。<ワークスペースルート>
#
# 実行ファイル（ccnavi_bin）があればそれを、無ければソース（ccnavi のリポジトリの src/ と uv）を
# `--root <ルート>` つきで起こす。どちらも無ければ、ccnavi_wap_exe は 1 を返すだけの関数にして 1 を返す
# （止めるかどうかと文面は呼ぶ側が決める）。見つけた実行ファイルのパスは ccnavi_wap_bin に入れる
# （ソースで動かすときは空）。
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
	if [ -f "$1/src/ccnavi/__main__.py" ] && command -v uv >/dev/null 2>&1; then
		# ソースで動かすときは、ワークスペースルートで uv を起こす（uv はプロジェクトを cwd から探す）。
		# サブシェルで cd するので、呼ぶ側の cwd は動かない。
		ccnavi_wap_exe() { (cd "$ccnavi_wap_exe_root" && uv run --quiet python -m ccnavi --root "$ccnavi_wap_exe_root" "$@"); }
		return 0
	fi
	ccnavi_wap_exe() { return 1; }
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
	# 子の識別子（`<親>-<2 桁のフェーズ番号>-<2 桁の連番>`）なら、右から 2 段を剥がして親にする。
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
	# Windows の実行ファイルの CRLF の CR を除いて読む（1 行 1 項目の値の末尾に CR を残さない）。
	ccnavi_wap_exe write-and-push family "$1" 2>"$ccnavi_wap_tmp/family-err" </dev/null | tr -d '\r' >"$ccnavi_wap_tmp/family" || :
	if [ "$(head -n 1 "$ccnavi_wap_tmp/family" 2>/dev/null)" != "write-and-push 1" ]; then
		ccnavi_wap_family_id="$ccnavi_cf_p"
		ccnavi_wap_target=stop
		ccnavi_wap_why="実行ファイルが write-and-push の問い合わせ（write-and-push family）に応答しない（$(head -n 1 "$ccnavi_wap_tmp/family-err" 2>/dev/null)）。書かずに止める。実行ファイルを新しくしてください"
		return 0
	fi
	ccnavi_wap_target=$(sed -n 's/^target //p' "$ccnavi_wap_tmp/family" | head -n 1)
	ccnavi_wap_why=$(sed -n 's/^why //p' "$ccnavi_wap_tmp/family" | head -n 1)
	ccnavi_wap_family_id=$(sed -n 's/^family //p' "$ccnavi_wap_tmp/family" | head -n 1)
	ccnavi_wap_repo=$(sed -n 's/^repo //p' "$ccnavi_wap_tmp/family" | head -n 1)
	ccnavi_wap_tree=$(sed -n 's/^tree //p' "$ccnavi_wap_tmp/family" | head -n 1)
	ccnavi_wap_approved=$(sed -n 's/^approved //p' "$ccnavi_wap_tmp/family" | head -n 1)
	ccnavi_wap_review=$(sed -n 's/^review //p' "$ccnavi_wap_tmp/family" | head -n 1)
	ccnavi_wap_branch=$(sed -n 's/^branch //p' "$ccnavi_wap_tmp/family" | head -n 1)
	sed -n 's/^hint //p' "$ccnavi_wap_tmp/family" >"$ccnavi_wap_tmp/hints"
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

# `git rev-parse --git-path <名前>` をツリーからのパスにする。<ツリー> <名前>
ccnavi_wap_git_path() {
	ccnavi_gp_rel=$(git -C "$1" rev-parse --git-path "$2" 2>/dev/null || :)
	case "$ccnavi_gp_rel" in
	'') printf '%s\n' "$1/.git/$2" ;;
	/* | [A-Za-z]:*) printf '%s\n' "$ccnavi_gp_rel" ;;
	*) printf '%s\n' "$1/$ccnavi_gp_rel" ;;
	esac
}

# 途中の操作の名前（無ければ空）。<ツリー>
ccnavi_wap_busy() {
	for ccnavi_cb_name in MERGE_HEAD CHERRY_PICK_HEAD REVERT_HEAD rebase-merge rebase-apply sequencer index.lock; do
		if [ -e "$(ccnavi_wap_git_path "$1" "$ccnavi_cb_name")" ]; then
			printf '%s\n' "$ccnavi_cb_name"
			return 0
		fi
	done
	return 0
}

# 1〜5。0 なら書いてよい（ロックを持ったまま返る）。1 なら止めた（ロックは外した）。
ccnavi_wap_begin() {
	ccnavi_cb_wait=$(ccnavi_wap_number "${CCNAVI_LOCK_WAIT:-}" 120)
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
	ccnavi_wap_exe write-and-push sort "$ccnavi_wap_family_id" ${2:+"$2"} 2>"$ccnavi_wap_tmp/sort-err" </dev/null | tr -d '\r' >"$1" || :
	if [ "$(head -n 1 "$1" 2>/dev/null)" != "write-and-push 1" ]; then
		ccnavi_wap_say "置き場の変更を見分けられなかった（$(head -n 1 "$ccnavi_wap_tmp/sort-err" 2>/dev/null)）。何も反映していません"
		return 1
	fi
	return 0
}

# 見分けで止めるものがあれば言って 1。<見分けの出力> <c の前置き> <c の後書き> <d の前置き> <d の後書き>
ccnavi_wap_stops() {
	ccnavi_cs_c=$(sed -n 's/^c //p' "$1" | tr '\n' ' ' | sed 's/ *$//')
	ccnavi_cs_d=$(sed -n 's/^d //p' "$1" | tr '\n' ' ' | sed 's/ *$//')
	[ -z "$ccnavi_cs_c$ccnavi_cs_d" ] && return 0
	[ -n "$ccnavi_cs_c" ] && ccnavi_wap_say "$2（${ccnavi_cs_c}）$3"
	[ -n "$ccnavi_cs_d" ] && ccnavi_wap_say "$4（${ccnavi_cs_d}）$5"
	sed -n 's/^why /  - /p' "$1" >&2
	log_info write-and-push で止めた -- "family=$ccnavi_wap_family_id" "reason=write-and-push-unknown-change"
	return 1
}

# 3〜5。0 なら続けてよい。
ccnavi_wap_prepare() {
	# 3. write-and-push の外の変更を見分け、(b) を取り込みの前にコミットする。
	ccnavi_wap_sort "$ccnavi_wap_tmp/sort" || return 1
	ccnavi_wap_stops "$ccnavi_wap_tmp/sort" "ユーザの判断が未送信" \
		"。承認の push（sh $ccnavi_wap_sh/ccnavi-user-push-approved.sh ${ccnavi_wap_family_id}）をユーザが実行します。何も反映していません" \
		"置き場に ccnavi が把握していない変更があります" "。ユーザが確かめてください。何も反映していません" || return 1
	sed -n 's/^keep //p' "$ccnavi_wap_tmp/sort" >"$ccnavi_wap_tmp/keep"
	sed -n 's/^b //p' "$ccnavi_wap_tmp/sort" >"$ccnavi_wap_tmp/b"
	if [ -s "$ccnavi_wap_tmp/b" ]; then
		ccnavi_wap_commit "$ccnavi_wap_tmp/b" "ccnavi: $ccnavi_wap_family_id の hook のマーカーをコミットする" || return 1
	fi
	# 4. 取り込み（ccnavi-sync.sh。ロックは入れ子で渡る）。
	ccnavi_cp_rc=0
	sh "$ccnavi_wap_sh/ccnavi-sync.sh" "$ccnavi_wap_family_id" </dev/null >"$ccnavi_wap_tmp/sync" 2>&1 || ccnavi_cp_rc=$?
	sed "s/^/  /" "$ccnavi_wap_tmp/sync" >&2
	if [ "$ccnavi_cp_rc" -ne 0 ]; then
		# 3 と 4 の間に hook が書いた（merge が書きかけと重なった）。やり直さずに止める。再実行すれば 3 でコミットされる。
		if grep -q '書きかけの' "$ccnavi_wap_tmp/sync" 2>/dev/null; then
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
	# 親のブランチがリモートから消えていれば、push で作り直さずに止める（push の関門。3 経路で同じ）。
	ccnavi_cp_gate=0
	ccnavi_push_gate "$ccnavi_wap_tree" "$ccnavi_wap_branch" "$ccnavi_wap_family_id" "$ccnavi_wap_root" \
		"$(ccnavi_wap_number "${CCNAVI_WRITE_AND_PUSH_TIMEOUT:-}" 60)" || ccnavi_cp_gate=$?
	if [ "$ccnavi_cp_gate" -ne 0 ]; then
		ccnavi_wap_say "${ccnavi_pg_why}。何も反映していません"
		log_info write-and-push で止めた -- "family=$ccnavi_wap_family_id" "reason=push-gate" "gate=$ccnavi_cp_gate"
		return 1
	fi
	# 親のブランチが一度も送られていない（ccnavi-start.sh を通さずに手で切った親）。5 の見分けは
	# origin の参照を基点にするので、参照が無いと差分を読めずに内部のエラーで終わる。先に事実を言って止める。
	# 送った形跡があるのに参照が無い親は、上の push の関門と ccnavi-sync.sh の判定に任せる。
	ccnavi_parent_info "$ccnavi_wap_tree" "$ccnavi_wap_family_id" || :
	# 実行ファイルが答えない・古くて値が unknown のときは、断定せず従来の関門に任せる
	if [ "$ccnavi_pi_parent" = yes ] && [ "$ccnavi_pi_approved" != unreadable ] &&
		[ "$ccnavi_pi_approved" != unknown ] && [ "$ccnavi_pi_requested" != unknown ] &&
		[ "$ccnavi_pi_archived" != unknown ] && [ "$ccnavi_pi_started" != unknown ] &&
		! ccnavi_parent_trace "$ccnavi_wap_tree" "$ccnavi_wap_branch"; then
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

# 一覧のパスだけをコミットする。<一覧> <文>。新しいファイルは先に add する。
# 変わったものが 1 つも無ければコミットせずに 0（ccnavi_wap_committed は空）。
# 失敗したら、この実行が add したパスを索引から外して 1（索引を元に戻す）。
ccnavi_wap_committed=""
ccnavi_wap_commit() {
	ccnavi_wap_committed=""
	: >"$ccnavi_wap_tmp/paths"
	: >"$ccnavi_wap_tmp/added"
	ccnavi_cc_base=$(git -C "$ccnavi_wap_tree" rev-parse --verify HEAD 2>/dev/null) || {
		ccnavi_wap_say "親のワークツリーの先頭を読めない。コミットしない"
		return 1
	}
	while IFS= read -r ccnavi_cc_path; do
		[ -n "$ccnavi_cc_path" ] || continue
		case "$ccnavi_cc_path" in
		/* | [A-Za-z]:*) continue ;;
		esac
		if [ -e "$ccnavi_wap_tree/$ccnavi_cc_path" ] || [ -L "$ccnavi_wap_tree/$ccnavi_cc_path" ]; then
			if [ -z "$(git -C "$ccnavi_wap_tree" ls-files -- ":(literal)$ccnavi_cc_path" 2>/dev/null)" ]; then
				if ! git -C "$ccnavi_wap_tree" add -- ":(literal)$ccnavi_cc_path" 2>"$ccnavi_wap_tmp/err"; then
					ccnavi_wap_say "$ccnavi_cc_path を add できなかった（$(head -n 1 "$ccnavi_wap_tmp/err")）"
					ccnavi_wap_unstage "$ccnavi_wap_tmp/added" "$ccnavi_cc_base"
					return 1
				fi
				printf '%s\n' "$ccnavi_cc_path" >>"$ccnavi_wap_tmp/added"
			fi
		elif [ -z "$(git -C "$ccnavi_wap_tree" ls-files -- ":(literal)$ccnavi_cc_path" 2>/dev/null)" ]; then
			continue # 作って削除しました（git の知らない）パス
		fi
		printf '%s\n' "$ccnavi_cc_path" >>"$ccnavi_wap_tmp/paths"
	done <"$1"
	[ -s "$ccnavi_wap_tmp/paths" ] || return 0
	sed 's/^/:(literal)/' "$ccnavi_wap_tmp/paths" | tr '\n' '\000' >"$ccnavi_wap_tmp/pathspec"
	if ! xargs -0 git -C "$ccnavi_wap_tree" status --porcelain --untracked-files=all -- \
		<"$ccnavi_wap_tmp/pathspec" >"$ccnavi_wap_tmp/status" 2>"$ccnavi_wap_tmp/err"; then
		ccnavi_wap_say "git の状態を読めない（$(head -n 1 "$ccnavi_wap_tmp/err")）。コミットしない"
		ccnavi_wap_unstage "$ccnavi_wap_tmp/added" "$ccnavi_cc_base"
		return 1
	fi
	[ -s "$ccnavi_wap_tmp/status" ] || return 0
	# ユーザの hook は実行しない。署名などで尋ねて止まらないよう、タイムアウトで切る。
	if ! ccnavi_git_timed "$(ccnavi_wap_number "${CCNAVI_WRITE_AND_PUSH_COMMIT_TIMEOUT:-}" 60)" "$ccnavi_wap_tmp/err" "$ccnavi_wap_tree" \
		commit --quiet --only --no-verify -m "$2" \
		--pathspec-from-file="$ccnavi_wap_tmp/pathspec" --pathspec-file-nul >"$ccnavi_wap_tmp/out"; then
		cat "$ccnavi_wap_tmp/out" >>"$ccnavi_wap_tmp/err"
		ccnavi_wap_say "コミットできなかった（制限時間で打ち切った場合を含む）。$(ccnavi_git_refusal "$ccnavi_wap_tmp/err")"
		ccnavi_wap_unstage "$ccnavi_wap_tmp/added" "$ccnavi_cc_base"
		return 1
	fi
	ccnavi_wap_committed=$(git -C "$ccnavi_wap_tree" rev-parse HEAD)
	return 0
}

# 一覧のパスの索引を <版> に合わせる（<版> に無ければ索引から外す）。<一覧> <版>
ccnavi_wap_unstage() {
	while IFS= read -r ccnavi_cn_path; do
		[ -n "$ccnavi_cn_path" ] || continue
		git -C "$ccnavi_wap_tree" restore --staged --source="$2" -- ":(literal)$ccnavi_cn_path" 2>/dev/null ||
			git -C "$ccnavi_wap_tree" rm --cached --quiet --ignore-unmatch -- ":(literal)$ccnavi_cn_path" >/dev/null 2>&1 ||
			ccnavi_wap_say "$ccnavi_cn_path の索引を戻せなかった"
	done <"$1"
}

# 書いた一覧のうち、この実行で書いたパスのワークツリーの中身を <版> に戻す（無ければ消す）。<一覧> <版>
# record-risk の記録など、この実行の前から未コミットだったもの（keep）は触らない。
# ccnavi_wap_keep_worktree が空でなければ何も戻さない。呼ぶ側が書いたものではなく、利用者の書いた入力
# （approval-request の提案）を一覧に載せている操作で使う。戻すと、送れなかったときに提案そのものが消える。
ccnavi_wap_keep_worktree=""
ccnavi_wap_restore_written() {
	[ -z "$ccnavi_wap_keep_worktree" ] || return 0
	while IFS= read -r ccnavi_cr_path; do
		[ -n "$ccnavi_cr_path" ] || continue
		case "$ccnavi_cr_path" in
		/* | [A-Za-z]:*) continue ;;
		esac
		grep -F -x -q -- "$ccnavi_cr_path" "$ccnavi_wap_tmp/keep" 2>/dev/null && continue
		if git -C "$ccnavi_wap_tree" cat-file -e "$2:$ccnavi_cr_path" 2>/dev/null; then
			git -C "$ccnavi_wap_tree" restore --worktree --source="$2" -- ":(literal)$ccnavi_cr_path" 2>/dev/null ||
				ccnavi_wap_say "$ccnavi_cr_path を書く前の状態に戻せなかった"
		else
			rm -f "$ccnavi_wap_tree/$ccnavi_cr_path" 2>/dev/null ||
				ccnavi_wap_say "$ccnavi_cr_path を削除できませんでした。"
		fi
	done <"$1"
}

# 実行ファイルを 1 回（一覧つき）。<一覧> <引数>...
ccnavi_wap_run() {
	ccnavi_cr_list="$1"
	shift
	rm -f "$ccnavi_cr_list"
	if [ -n "$ccnavi_wap_capture" ]; then
		ccnavi_wap_exe --record-writes "$ccnavi_cr_list" --record-tree "$ccnavi_wap_tree" "$@" >"$ccnavi_wap_capture"
	else
		ccnavi_wap_exe --record-writes "$ccnavi_cr_list" --record-tree "$ccnavi_wap_tree" "$@"
	fi
}

# 子の finish の合流の前の検査。<子の識別子>。0 なら続けてよい（合流するなら ccnavi_wap_merge_sha が入る）。
# 1 なら止めた（何も変えていない。ロックは外した）。答えられなければ止める側。
ccnavi_wap_merge_plan() {
	ccnavi_wap_merge_sha=""
	ccnavi_wap_merge_child=""
	ccnavi_wap_exe write-and-push merge "$1" 2>"$ccnavi_wap_tmp/merge-err" </dev/null | tr -d '\r' >"$ccnavi_wap_tmp/merge" || :
	if [ "$(head -n 1 "$ccnavi_wap_tmp/merge" 2>/dev/null)" != "write-and-push 1" ]; then
		ccnavi_wap_say "合流の前の検査（write-and-push merge）に実行ファイルが応答しない（$(head -n 1 "$ccnavi_wap_tmp/merge-err" 2>/dev/null)）。何も反映していません。実行ファイルを新しくしてください"
		ccnavi_lock_drop
		return 1
	fi
	ccnavi_pm_stop=$(sed -n 's/^stop //p' "$ccnavi_wap_tmp/merge" | head -n 1)
	if [ -n "$ccnavi_pm_stop" ]; then
		ccnavi_wap_say "$ccnavi_pm_stop"
		sed -n 's/^hint /  - /p' "$ccnavi_wap_tmp/merge" >&2
		log_info write-and-push で止めた -- "family=$ccnavi_wap_family_id" "reason=write-and-push-merge-stop"
		ccnavi_lock_drop
		return 1
	fi
	if [ "$(sed -n 's/^merge //p' "$ccnavi_wap_tmp/merge" | head -n 1)" = yes ]; then
		ccnavi_wap_merge_child="$1"
		ccnavi_wap_merge_sha=$(sed -n 's/^head //p' "$ccnavi_wap_tmp/merge" | head -n 1)
		[ -n "$ccnavi_wap_merge_sha" ] || {
			ccnavi_wap_say "合流する子の先頭を読めない。何も反映していません"
			ccnavi_lock_drop
			return 1
		}
	fi
	return 0
}

# 親のワークツリーに子の先頭を入れる（コミットしない）。<元の先頭> <書いたパスの一覧>。0 で入れた（か、入れるものが無い）。
# 1 は何も変えずに止めた（衝突など。merge は取りやめた）。
ccnavi_wap_merge_begin() {
	[ -n "$ccnavi_wap_merge_sha" ] || return 0
	# 取り込みで、子の先頭がすでに親に入っていれば、入れるものは無い。
	if git -C "$ccnavi_wap_tree" merge-base --is-ancestor "$ccnavi_wap_merge_sha" HEAD 2>/dev/null; then
		return 0
	fi
	ccnavi_mb_rc=0
	# merge の前に立てる。merge の最中にシグナルが来ても、ccnavi_wap_end が取りやめられるように。
	ccnavi_wap_merging_h0="$1"
	ccnavi_wap_merging_list="$2"
	ccnavi_wap_merging=yes
	GIT_MERGE_AUTOEDIT=no git -C "$ccnavi_wap_tree" merge --no-ff --no-commit --quiet "$ccnavi_wap_merge_sha" \
		</dev/null >"$ccnavi_wap_tmp/merge-out" 2>"$ccnavi_wap_tmp/merge-git-err" || ccnavi_mb_rc=$?
	if [ "$ccnavi_mb_rc" -eq 0 ]; then
		return 0
	fi
	cat "$ccnavi_wap_tmp/merge-out" >>"$ccnavi_wap_tmp/merge-git-err" 2>/dev/null || :
	# 衝突したパスは引用符で囲ませず（core.quotePath=false）NUL 区切りで取り、1 行 1 パスの一覧にする
	# （空白・日本語のパスを割らない。改行を含むパスは表示のために ? に置き換わる）。
	git -C "$ccnavi_wap_tree" -c core.quotePath=false diff --name-only -z --diff-filter=U 2>/dev/null |
		tr '\n\000' '?\n' >"$ccnavi_wap_tmp/conflicts" || :
	ccnavi_mb_conflicts=$(awk 'length($0) > 0 { if (n++) printf ", "; printf "%s", $0 }' "$ccnavi_wap_tmp/conflicts" 2>/dev/null)
	git -C "$ccnavi_wap_tree" merge --abort >/dev/null 2>&1 || :
	ccnavi_wap_merging=""
	if [ "$(git -C "$ccnavi_wap_tree" rev-parse HEAD 2>/dev/null)" != "$1" ] || [ -n "$(ccnavi_wap_busy "$ccnavi_wap_tree")" ]; then
		ccnavi_wap_say "合流を取りやめても元に戻らなかった。親のワークツリー（${ccnavi_wap_tree}）をユーザが確かめてください"
		return 1
	fi
	if [ -n "$ccnavi_mb_conflicts" ]; then
		ccnavi_wap_say "子 ${ccnavi_wap_merge_child} を親のブランチ ${ccnavi_wap_branch} へ合流すると衝突する（${ccnavi_mb_conflicts}）。合流を取りやめて、何も変えずに止めた。並行していた子とぶつかったときも、解くのは後から finish した側（この子）です"
		ccnavi_wap_say "  子のワークツリー（.claude/worktrees/${ccnavi_wap_merge_child}）で親のブランチ ${ccnavi_wap_branch} を merge して衝突を解き、コミットしてから finish をやり直してください"
	else
		ccnavi_wap_say "子 ${ccnavi_wap_merge_child} を合流できなかった（$(ccnavi_git_refusal "$ccnavi_wap_tmp/merge-git-err")）。何も変えずに止めた"
	fi
	log_info write-and-push で止めた -- "family=$ccnavi_wap_family_id" "reason=write-and-push-merge-conflict"
	return 1
}

# 合流の途中なら取りやめ、書いたものを元に戻す。<元の先頭> <書いたパスの一覧>。0 で元に戻った（か、合流の途中でない）。
ccnavi_wap_unmerge() {
	[ -n "$ccnavi_wap_merging" ] || return 0
	ccnavi_wap_merging=""
	if git -C "$ccnavi_wap_tree" rev-parse -q --verify MERGE_HEAD >/dev/null 2>&1; then
		# 先に merge --abort（索引と合流で入れたものを HEAD に戻す）。書いた状態のファイルは、その後に戻す
		# （先に戻すと、索引に足した分との差が「未 add の変更」に見えて、abort が残してしまう）。
		if ! git -C "$ccnavi_wap_tree" merge --abort >/dev/null 2>"$ccnavi_wap_tmp/err"; then
			ccnavi_wap_say "合流を取りやめられなかった（$(head -n 1 "$ccnavi_wap_tmp/err" 2>/dev/null)）。親のワークツリー（${ccnavi_wap_tree}）をユーザが確かめてください"
			return 1
		fi
	elif [ "$(git -C "$ccnavi_wap_tree" rev-parse HEAD 2>/dev/null)" != "$1" ]; then
		# 合流の記録（MERGE_HEAD）が無く、先頭も元のままでない。コミットまで済んだ形は呼ぶ側が見る。
		ccnavi_wap_say "合流の途中の形でなくなっていた。戻さずに止めた。親のワークツリー（${ccnavi_wap_tree}）をユーザが確かめてください"
		return 1
	elif ! git -C "$ccnavi_wap_tree" diff --cached --quiet 2>/dev/null; then
		# merge が始まる前か、MERGE_HEAD を書く前に切られた。索引に残ったものは誰のものか分からないので触らない。
		ccnavi_wap_say "合流が途中で切られ、索引に変更が残っている。戻さずに止めた。親のワークツリー（${ccnavi_wap_tree}）をユーザが確かめてください"
		return 1
	fi
	# MERGE_HEAD が無く先頭が元のままで、索引にも変更が無い: merge は何も残していない。
	[ ! -f "$2" ] || ccnavi_wap_restore_written "$2" "$1"
	log_info write-and-push で合流を取りやめた -- "family=$ccnavi_wap_family_id"
	return 0
}

# 合流の途中のコミット。書いたパスを索引に足して、合流と状態を 1 つのマージコミットにする。<一覧> <文>
# 失敗したら 1（呼ぶ側が ccnavi_wap_unmerge で戻す）。
ccnavi_wap_commit_merge() {
	ccnavi_wap_committed=""
	while IFS= read -r ccnavi_cm_path; do
		[ -n "$ccnavi_cm_path" ] || continue
		case "$ccnavi_cm_path" in
		/* | [A-Za-z]:*) continue ;;
		esac
		if [ -e "$ccnavi_wap_tree/$ccnavi_cm_path" ] || [ -L "$ccnavi_wap_tree/$ccnavi_cm_path" ]; then
			git -C "$ccnavi_wap_tree" add -A -- ":(literal)$ccnavi_cm_path" 2>"$ccnavi_wap_tmp/err" || {
				ccnavi_wap_say "$ccnavi_cm_path を add できなかった（$(head -n 1 "$ccnavi_wap_tmp/err")）"
				return 1
			}
		else
			git -C "$ccnavi_wap_tree" rm --cached --quiet --ignore-unmatch -- ":(literal)$ccnavi_cm_path" >/dev/null 2>&1 || :
		fi
	done <"$1"
	if ! ccnavi_git_timed "$(ccnavi_wap_number "${CCNAVI_WRITE_AND_PUSH_COMMIT_TIMEOUT:-}" 60)" "$ccnavi_wap_tmp/err" "$ccnavi_wap_tree" \
		commit --quiet --no-verify -m "$2" >"$ccnavi_wap_tmp/out"; then
		cat "$ccnavi_wap_tmp/out" >>"$ccnavi_wap_tmp/err"
		ccnavi_wap_say "合流のコミットができなかった（制限時間で打ち切った場合を含む）。$(ccnavi_git_refusal "$ccnavi_wap_tmp/err")"
		return 1
	fi
	ccnavi_wap_committed=$(git -C "$ccnavi_wap_tree" rev-parse HEAD)
	# 送る前に切られたら戻せるように、合流の途中の印を外す前に覚える（ccnavi_wap_end）。
	ccnavi_wap_inflight_h0="$ccnavi_wap_merging_h0"
	ccnavi_wap_inflight_list="$ccnavi_wap_merging_list"
	ccnavi_wap_inflight="$ccnavi_wap_committed"
	ccnavi_wap_merging=""
	return 0
}

# 6〜11。<文> -- <実行ファイルの引数>...。終了コードは実行ファイルのもの（write-and-push で失敗したら 1）。
ccnavi_wap_write() {
	ccnavi_cw_message="$1"
	shift
	[ "${1:-}" = "--" ] && shift
	ccnavi_cw_dir="$ccnavi_wap_root/logs/state/write-and-push/$ccnavi_wap_repo"
	mkdir -p "$ccnavi_cw_dir" 2>/dev/null || :
	ccnavi_cw_list="$ccnavi_cw_dir/$ccnavi_wap_family_id.$$.writes"
	ccnavi_cw_timeout=$(ccnavi_wap_number "${CCNAVI_WRITE_AND_PUSH_TIMEOUT:-}" 60)
	ccnavi_cw_try=0
	while :; do
		ccnavi_cw_try=$((ccnavi_cw_try + 1))
		# 6. 元の先頭。
		ccnavi_cw_h0=$(git -C "$ccnavi_wap_tree" rev-parse --verify HEAD 2>/dev/null) || {
			ccnavi_wap_say "親のワークツリーの先頭を読めない。何も反映していません"
			return 1
		}
		# 6b. 合流（子の finish）。親のワークツリーに子の先頭を入れる（コミットしない）。
		ccnavi_wap_merge_begin "$ccnavi_cw_h0" "$ccnavi_cw_list" || return 1
		# 7. 書く。
		ccnavi_cw_rc=0
		ccnavi_wap_run "$ccnavi_cw_list" "$@" || ccnavi_cw_rc=$?
		if [ ! -f "$ccnavi_cw_list" ]; then
			ccnavi_wap_unmerge "$ccnavi_cw_h0" "$ccnavi_cw_list" || :
			ccnavi_wap_say "実行ファイルが書いたパスの一覧を出さなかった。書いたものが分からないのでコミットしない。親のワークツリー（${ccnavi_wap_tree}）をユーザが確かめてください"
			return 1
		fi
		if [ "$ccnavi_cw_rc" -ne 0 ]; then
			# 失敗した回は、ここまでに書いたもの（合流を含む）を戻す（コミットしない）。
			ccnavi_wap_unmerge "$ccnavi_cw_h0" "$ccnavi_cw_list" || :
			ccnavi_wap_restore_written "$ccnavi_cw_list" "$ccnavi_cw_h0"
			rm -f "$ccnavi_cw_list"
			return "$ccnavi_cw_rc"
		fi
		# 8. コミット（合流の途中なら、合流と状態を 1 つのマージコミットにする）。
		if [ -n "$ccnavi_wap_merging" ]; then
			ccnavi_cw_ok=0
			ccnavi_wap_commit_merge "$ccnavi_cw_list" "$ccnavi_cw_message" || ccnavi_cw_ok=1
		else
			ccnavi_cw_ok=0
			ccnavi_wap_commit "$ccnavi_cw_list" "$ccnavi_cw_message" || ccnavi_cw_ok=1
		fi
		if [ "$ccnavi_cw_ok" -ne 0 ]; then
			ccnavi_wap_unmerge "$ccnavi_cw_h0" "$ccnavi_cw_list" || :
			ccnavi_wap_restore_written "$ccnavi_cw_list" "$ccnavi_cw_h0"
			rm -f "$ccnavi_cw_list"
			return 1
		fi
		ccnavi_cw_c="$ccnavi_wap_committed"
		# 送る前に切られたら戻せるように覚えておく（ccnavi_wap_end）。
		if [ -n "$ccnavi_cw_c" ]; then
			ccnavi_wap_inflight="$ccnavi_cw_c"
			ccnavi_wap_inflight_h0="$ccnavi_cw_h0"
			ccnavi_wap_inflight_list="$ccnavi_cw_list"
		fi
		if [ -z "$ccnavi_cw_c" ] && [ "$(git -C "$ccnavi_wap_tree" rev-parse HEAD)" = "$(git -C "$ccnavi_wap_tree" rev-parse --verify -q "refs/remotes/origin/$ccnavi_wap_branch" 2>/dev/null)" ]; then
			rm -f "$ccnavi_cw_list"
			ccnavi_wap_note "route=unchanged"
			return 0 # 書いたものが無く、送るものも無い
		fi
		ccnavi_cw_head=$(git -C "$ccnavi_wap_tree" rev-parse HEAD)
		# 9. push（--force なし）。
		if ccnavi_git_timed "$ccnavi_cw_timeout" "$ccnavi_wap_tmp/push-err" "$ccnavi_wap_tree" \
			push --quiet origin "refs/heads/$ccnavi_wap_branch:refs/heads/$ccnavi_wap_branch" >/dev/null; then
			ccnavi_wap_inflight=""
			ccnavi_wap_sent "$ccnavi_cw_head"
			rm -f "$ccnavi_cw_list"
			return 0
		fi
		# 10. 失敗したように見えても、届いていれば成功。
		if ccnavi_git_timed "$ccnavi_cw_timeout" "$ccnavi_wap_tmp/ls-err" "$ccnavi_wap_tree" \
			ls-remote origin "refs/heads/$ccnavi_wap_branch" >"$ccnavi_wap_tmp/ls" &&
			grep -F -x -q -- "$ccnavi_cw_head${ccnavi_wap_tab}refs/heads/$ccnavi_wap_branch" "$ccnavi_wap_tmp/ls"; then
			ccnavi_wap_say "push の応答を受け取れなかったが、リモートには届いていた（${ccnavi_cw_head}）"
			ccnavi_wap_inflight=""
			ccnavi_wap_sent "$ccnavi_cw_head"
			rm -f "$ccnavi_cw_list"
			return 0
		fi
		ccnavi_wap_say "push が通らなかった（$(head -n 1 "$ccnavi_wap_tmp/push-err" 2>/dev/null)）"
		log_info write-and-push で push が通らなかった -- "family=$ccnavi_wap_family_id" "try=$ccnavi_cw_try" "reason=write-and-push-push-rejected"
		# 11. 戻す。先頭が自分のコミットのときだけ。
		if [ -n "$ccnavi_cw_c" ]; then
			ccnavi_wap_inflight=""
			if ! ccnavi_wap_undo "$ccnavi_cw_c" "$ccnavi_cw_h0" "$ccnavi_cw_list"; then
				rm -f "$ccnavi_cw_list"
				return 1
			fi
		fi
		rm -f "$ccnavi_cw_list"
		if [ "$ccnavi_cw_try" -ge 2 ]; then
			ccnavi_wap_say "2 回目も送れなかった。書いたものは戻した。オンラインで sh $ccnavi_wap_sh/ccnavi-sync.sh $ccnavi_wap_family_id を実行して取り込んでから、再実行してください"
			log_error write-and-push で送れなかった -- "family=$ccnavi_wap_family_id" "reason=write-and-push-push"
			return 1
		fi
		ccnavi_wap_say "書いたものを戻した。取り込みからもう 1 回だけやり直す"
		ccnavi_wap_prepare || return 1
		# 届いていたのに確かめ（ls-remote）も失敗していた回は、取り込みで自分のコミットが戻ってくる。
		# そのときは書き直さず、届いていたとして終える。
		if [ -n "$ccnavi_cw_c" ] &&
			git -C "$ccnavi_wap_tree" merge-base --is-ancestor "$ccnavi_cw_c" HEAD 2>/dev/null; then
			ccnavi_wap_say "取り込み直すと、前の回のコミット（$(printf '%.12s' "$ccnavi_cw_c")）がリモートに届いていた。書き直さずに終える"
			ccnavi_wap_sent "$(git -C "$ccnavi_wap_tree" rev-parse HEAD)"
			return 0
		fi
	done
}

# 比較つきで戻す。<自分のコミット> <元の先頭> <一覧>
ccnavi_wap_undo() {
	ccnavi_cu_now=$(git -C "$ccnavi_wap_tree" rev-parse HEAD 2>/dev/null || :)
	if [ "$ccnavi_cu_now" != "$1" ]; then
		ccnavi_wap_say "先頭が自分のコミット（$1）でなくなっていた（${ccnavi_cu_now}）。戻さずに止めた。ユーザが確かめてください"
		return 1
	fi
	# 戻すパスは、元の先頭からこのコミットまでの変更（合流したコードを含む）。1 行 1 パスで扱うので、
	# 改行を含むパス・大文字小文字だけが違うパス・submodule の追加と削除は、合流の前の検査（write-and-push merge）が止める。
	git -C "$ccnavi_wap_tree" diff --name-only --no-renames -z "$2" "$1" 2>/dev/null |
		tr '\000' '\n' >"$ccnavi_wap_tmp/in-commit"
	if ! git -C "$ccnavi_wap_tree" update-ref "refs/heads/$ccnavi_wap_branch" "$2" "$1" 2>"$ccnavi_wap_tmp/err"; then
		ccnavi_wap_say "コミットを戻せなかった（$(head -n 1 "$ccnavi_wap_tmp/err")）。ユーザが確かめてください"
		return 1
	fi
	ccnavi_wap_unstage "$ccnavi_wap_tmp/in-commit" "$2"
	ccnavi_wap_inflight=""
	ccnavi_wap_restore_written "$ccnavi_wap_tmp/in-commit" "$2"
	[ ! -f "$3" ] || ccnavi_wap_restore_written "$3" "$2"
	log_info write-and-push で書いたものを戻した -- "family=$ccnavi_wap_family_id"
	return 0
}

# approval-request の結果ファイル（ccnavi-ticket.sh が書く）への申し送り。CCNAVI_PROPOSE_NOTE が無ければ何もしない。
# 書けなくても approval-request は止めない。
ccnavi_wap_note() {
	[ -z "${CCNAVI_PROPOSE_NOTE:-}" ] || printf '%s\n' "$1" >>"$CCNAVI_PROPOSE_NOTE" 2>/dev/null || :
}

# 送れた。<送った先頭>
ccnavi_wap_sent() {
	ccnavi_wap_note "route=sent"
	ccnavi_wap_note "commit=$1"
	if [ -n "${CCNAVI_PROPOSE_NOTE:-}" ] && [ -f "${ccnavi_cw_list:-}" ]; then
		while IFS= read -r ccnavi_ws_path; do
			[ -n "$ccnavi_ws_path" ] || continue
			ccnavi_ws_sha=$(git -C "$ccnavi_wap_tree" rev-parse --verify -q "$1:$ccnavi_ws_path" 2>/dev/null || :)
			[ -z "$ccnavi_ws_sha" ] || ccnavi_wap_note "file=$ccnavi_ws_sha $ccnavi_ws_path"
		done <"$ccnavi_cw_list"
	fi
	ccnavi_wap_say "親子のチケット $ccnavi_wap_family_id のチケットの状態を送った（$(printf '%.12s' "$1")）"
	log_info write-and-push で送った -- "family=$ccnavi_wap_family_id"
}

# 後始末。ロックを外し、一時ディレクトリを消す。2 度呼んでも害は無い。
ccnavi_wap_inflight=""
ccnavi_wap_inflight_h0=""
ccnavi_wap_inflight_list=""
ccnavi_wap_end() {
	# タイムアウト監視（ccnavi_run_timed）の途中で切られたら、監視も止める。後で別のプロセスを kill しないようにするため。
	if [ -n "${ccnavi_tm_dog:-}" ]; then
		kill "$ccnavi_tm_dog" 2>/dev/null || :
		wait "$ccnavi_tm_dog" 2>/dev/null || :
		ccnavi_tm_dog=""
		# 監視していた git（コミット・push）も止めて終わりを待つ。動いている途中のツリーを見て戻さない。
		if [ -n "${ccnavi_tm_pid:-}" ]; then
			kill "$ccnavi_tm_pid" 2>/dev/null || :
			wait "$ccnavi_tm_pid" 2>/dev/null || :
		fi
	fi
	if [ -n "$ccnavi_wap_inflight" ] && [ -n "$ccnavi_wap_tmp" ]; then
		# 送る前に切られた。自分のコミットを比較つきで戻す（先頭が動いていれば戻さない）。
		ccnavi_ce_c="$ccnavi_wap_inflight"
		ccnavi_wap_inflight=""
		ccnavi_wap_merging=""
		ccnavi_wap_say "送る前に止められた。書いたものを戻す"
		ccnavi_wap_undo "$ccnavi_ce_c" "$ccnavi_wap_inflight_h0" "$ccnavi_wap_inflight_list" || :
		rm -f "$ccnavi_wap_inflight_list"
	fi
	if [ -n "$ccnavi_wap_merging" ] && [ -n "$ccnavi_wap_tmp" ]; then
		ccnavi_ce_head=$(git -C "$ccnavi_wap_tree" rev-parse -q --verify HEAD 2>/dev/null || :)
		if ! git -C "$ccnavi_wap_tree" rev-parse -q --verify MERGE_HEAD >/dev/null 2>&1 &&
			[ -n "$ccnavi_wap_merge_sha" ] &&
			[ "$(git -C "$ccnavi_wap_tree" rev-parse -q --verify "HEAD^1" 2>/dev/null)" = "$ccnavi_wap_merging_h0" ] &&
			[ "$(git -C "$ccnavi_wap_tree" rev-parse -q --verify "HEAD^2" 2>/dev/null)" = "$ccnavi_wap_merge_sha" ]; then
			# 合流のコミットまで済んで、送る前の印を立てる前に切られた。そのコミットを比較つきで戻す。
			ccnavi_wap_merging=""
			ccnavi_wap_say "送る前に止められた。合流のコミットを戻す"
			ccnavi_wap_undo "$ccnavi_ce_head" "$ccnavi_wap_merging_h0" "$ccnavi_wap_merging_list" || :
			rm -f "$ccnavi_wap_merging_list"
		else
			# 合流の途中で切られた。合流を取りやめ、書いたものを戻す。
			ccnavi_wap_say "合流の途中で止められた。合流を取りやめて、書いたものを戻す"
			ccnavi_wap_unmerge "$ccnavi_wap_merging_h0" "$ccnavi_wap_merging_list" || :
		fi
	fi
	ccnavi_lock_drop
	if [ -n "$ccnavi_wap_tmp" ]; then
		rm -rf "$ccnavi_wap_tmp"
		ccnavi_wap_tmp=""
	fi
	return 0
}
