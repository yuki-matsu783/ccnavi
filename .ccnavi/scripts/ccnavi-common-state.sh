# ccnavi-common-state.sh  ccnavi-common.sh が読む部品。単体では読まない（読み方は ccnavi-common.sh の冒頭）。
#
# 取り込み状態と統合先、タイムアウト監視つきの git。ロックの置き場（locks/）も下の見出しで説明している。

# ---- 取り込み状態とロック
#
# 取り込み状態は 1 行 1 項目の `<鍵> <値>`。sh は `sed -n 's/^<鍵> //p'` で読み、jq を使わない
# （JSON とチケットの YAML は実行ファイルが読んで、sh には 1 行で返す）。
# 置き場はワークスペースルートの `logs/state`（固定。ccnavi-review.sh と同じ）。
#
#   sync/<リポジトリ>/families/<P>   親子のチケットの取り込み状態（remote branch sha fetched_at state reason）
#   sync/<リポジトリ>/integration/   統合先の取り込み結果（統合先の done/・レイヤー・置き場のパスの設定のコピーと head）
#   locks/<リポジトリ>/<P>/          ロック。中の owner に持ち主を 1 行で書く
#
# <リポジトリ> はワークスペース自身なら `self`、プロジェクトならその名前。
#
# 統合先の名前を決める ccnavi_integration（と、その最後の手の ccnavi_default_branch）も、
# 統合先の取り込み結果を読むのでここに置く。

# state の置き場（`logs/state/`）の絶対パス。
ccnavi_state() {
	printf '%s\n' "$1/logs/state" # 固定
}

# そのツリーの取り込み状態を分ける名前。<ツリー> <ワークスペースルート>
ccnavi_repo_key() {
	ccnavi_rk_name=$(ccnavi_project "$1" "$2")
	if [ -n "$ccnavi_rk_name" ]; then
		printf '%s\n' "$ccnavi_rk_name"
	else
		printf 'self\n'
	fi
}

# 親子のチケットの取り込み状態のパス。<ワークスペースルート> <リポジトリ> <P>
#
# 鍵は親の識別子（`/` を含まない）。親のブランチ名は取り込み状態の中の `branch` に書く。
ccnavi_family_record() {
	printf '%s/sync/%s/families/%s\n' "$(ccnavi_state "$1")" "$2" "$3"
}

# 親のブランチ名がそのブランチの親子のチケットの取り込み状態。無ければ空。<ワークスペースルート> <リポジトリ> <ブランチ>
#
# 取り込み状態の `branch` の行で探す（`branch` の無い前の取り込み状態は、鍵の識別子をブランチ名として読む）。
# 識別子と違う名前の親のブランチ（`branch:`）でも、ブランチ名から取り込み状態を引ける。書きかけ（`*.tmp.*`）と
# シンボリックリンクは読まない。当たった取り込み状態を 1 行に 1 つずつ全部出す（2 つ以上なら、2 つの親子の
# チケットが同じブランチを宣言している。呼ぶ側は止める）。
ccnavi_family_record_of_branch() {
	ccnavi_fb_dir="$(ccnavi_state "$1")/sync/$2/families"
	[ -d "$ccnavi_fb_dir" ] || return 0
	[ -L "$ccnavi_fb_dir" ] && return 0
	for ccnavi_fb_file in "$ccnavi_fb_dir"/*; do
		[ -f "$ccnavi_fb_file" ] || continue
		[ -L "$ccnavi_fb_file" ] && continue
		case "$ccnavi_fb_file" in
		*.tmp.*) continue ;;
		esac
		ccnavi_fb_branch=$(ccnavi_record_get "$ccnavi_fb_file" branch)
		[ -n "$ccnavi_fb_branch" ] || ccnavi_fb_branch="${ccnavi_fb_file##*/}"
		if [ "$ccnavi_fb_branch" = "$3" ]; then
			printf '%s\n' "$ccnavi_fb_file"
		fi
	done
	return 0
}

# 親子のチケット <識別子> の親のブランチ名として <名前> を受けてよいなら 0。<名前> <識別子>
#
# 識別子と同じ名前は識別子の検査（ccnavi_is_ident）で見る（前からの識別子は ccnavi_is_branch の予約に
# 当たることがある）。違う名前は ccnavi_is_branch で見る。
ccnavi_branch_ok() {
	if [ "$1" = "$2" ]; then
		ccnavi_is_ident "$1"
	else
		ccnavi_is_branch "$1"
	fi
}

# 実行ファイルに聞く。答え（標準出力）を、Windows の実行ファイルの CR を除いて出す。<ワークスペースルート> <引数>...
#
# 実行ファイルが無ければ 1（ソースで動かしている ccnavi のリポジトリでは uv で起こす）。実行ファイルが
# 断った・答えなかったときは空を出して 0。答えの頭の行を確かめるのは呼ぶ側。
ccnavi_ask() {
	ccnavi_ak_root="$1"
	shift
	if ccnavi_ak_bin=$(ccnavi_bin "$ccnavi_ak_root"); then
		ccnavi_ak_out=$("$ccnavi_ak_bin" --root "$ccnavi_ak_root" "$@" 2>/dev/null </dev/null) || ccnavi_ak_out=""
	elif [ -f "$ccnavi_ak_root/src/ccnavi/__main__.py" ] && command -v uv >/dev/null 2>&1; then
		ccnavi_ak_out=$(cd "$ccnavi_ak_root" && uv run --quiet python -m ccnavi --root "$ccnavi_ak_root" "$@" 2>/dev/null </dev/null) ||
			ccnavi_ak_out=""
	else
		return 1
	fi
	printf '%s\n' "$ccnavi_ak_out" | tr -d '\r'
}

# 親子のチケットの親のブランチ名。<ワークスペースルート> <識別子>
#
# 実行ファイルの `c1 family <識別子>` の `branch` の行（承認済みの親チケットの `branch:`、無ければ識別子。
# 提案の `branch:` は使わない）。sh はチケットを読まない（読むのは実行ファイル）。実行ファイルが無ければ 1、
# 在るのに答えない・`branch` の行が無い（`branch_refused`。使えない名前か統合先の名前）・答えが親のブランチ名の
# 形でなければ 2 を返す（呼ぶ側は識別子の外へ動かさずに止める）。ソースで動かしている ccnavi の
# リポジトリでは uv で起こす。
ccnavi_family_branch() {
	if ccnavi_fbr_bin=$(ccnavi_bin "$1"); then
		ccnavi_fbr_out=$("$ccnavi_fbr_bin" --root "$1" c1 family "$2" 2>/dev/null </dev/null) || ccnavi_fbr_out=""
	elif [ -f "$1/src/ccnavi/__main__.py" ] && command -v uv >/dev/null 2>&1; then
		ccnavi_fbr_out=$(cd "$1" && uv run --quiet python -m ccnavi --root "$1" c1 family "$2" 2>/dev/null </dev/null) ||
			ccnavi_fbr_out=""
	else
		return 1
	fi
	ccnavi_fbr_out=$(printf '%s\n' "$ccnavi_fbr_out" | tr -d '\r')
	[ "$(printf '%s\n' "$ccnavi_fbr_out" | head -n 1)" = "c1 1" ] || return 2
	ccnavi_fbr_name=$(printf '%s\n' "$ccnavi_fbr_out" | sed -n 's/^branch //p' | head -n 1)
	ccnavi_branch_ok "$ccnavi_fbr_name" "$2" || return 2
	printf '%s\n' "$ccnavi_fbr_name"
}

# 取り込み状態から 1 項目を読む。無ければ空。<ファイル> <鍵>
ccnavi_record_get() {
	[ -f "$1" ] || return 0
	sed -n "s/^$2 //p" "$1" 2>/dev/null | head -n 1
}

# 取り込み状態を書き直す。<ファイル> <鍵> <値> [<鍵> <値>...]
#
# 同じディレクトリの一時ファイルに書いてから mv で置き換える（読む側が半端な中身を見ない）。
# 値の改行は空白に置き換える（1 行 1 項目の契約）。書けなければ 1。
# 渡した鍵だけを書き直すが、`mr`（親を載せる MR の番号。ccnavi-start.sh が保存する）は、渡さなくても前の値を引き継ぐ。
ccnavi_record_write() {
	ccnavi_rw_file="$1"
	shift
	mkdir -p "${ccnavi_rw_file%/*}" 2>/dev/null || return 1
	ccnavi_rw_keep=$(ccnavi_record_get "$ccnavi_rw_file" mr)
	ccnavi_rw_tmp="$ccnavi_rw_file.tmp.$$"
	: >"$ccnavi_rw_tmp" 2>/dev/null || return 1
	while [ "$#" -ge 2 ]; do
		[ "$1" != mr ] || ccnavi_rw_keep=""
		ccnavi_rw_value=$(printf '%s' "$2" | tr '\r\n' '  ')
		printf '%s %s\n' "$1" "$ccnavi_rw_value" >>"$ccnavi_rw_tmp" || {
			rm -f "$ccnavi_rw_tmp"
			return 1
		}
		shift 2
	done
	if [ -n "$ccnavi_rw_keep" ]; then
		printf 'mr %s\n' "$ccnavi_rw_keep" >>"$ccnavi_rw_tmp" || {
			rm -f "$ccnavi_rw_tmp"
			return 1
		}
	fi
	mv -f "$ccnavi_rw_tmp" "$ccnavi_rw_file" 2>/dev/null || {
		rm -f "$ccnavi_rw_tmp"
		return 1
	}
}

# 取り込み状態の 1 項目だけを書き足すか書き換える。ほかの項目はそのまま。<ファイル> <鍵> <値>
#
# ファイルが無ければ 1（先に ccnavi_record_write で作る）。書き方は ccnavi_record_write と同じ（一時ファイル → mv）。
ccnavi_record_put() {
	[ -f "$1" ] || return 1
	ccnavi_rp_tmp="$1.tmp.$$"
	ccnavi_rp_value=$(printf '%s' "$3" | tr '\r\n' '  ')
	{
		sed "/^$2 /d" "$1"
		printf '%s %s\n' "$2" "$ccnavi_rp_value"
	} >"$ccnavi_rp_tmp" 2>/dev/null || {
		rm -f "$ccnavi_rp_tmp"
		return 1
	}
	mv -f "$ccnavi_rp_tmp" "$1" 2>/dev/null || {
		rm -f "$ccnavi_rp_tmp"
		return 1
	}
}

# そのリポジトリのデフォルトブランチの名前。分からなければ 1。<リポジトリ>
#
# `origin/HEAD` は clone のときに置かれる。`git init` してから `remote add` した手元や、
# 古い clone には無いので、そのときは `origin/main`・`origin/master` の在る側を使う。
# どちらも無ければ「分からない」。当てずっぽうで別のブランチを宣言しない。
# ネットワークには出ない（手元の ref だけを読む）。
ccnavi_default_branch() {
	ccnavi_db_head=$(git -C "$1" symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null || :)
	case "$ccnavi_db_head" in
	origin/?*)
		printf '%s\n' "${ccnavi_db_head#origin/}"
		return 0
		;;
	esac
	for ccnavi_db_try in main master; do
		if git -C "$1" rev-parse --verify --quiet "refs/remotes/origin/$ccnavi_db_try" >/dev/null 2>&1; then
			printf '%s\n' "$ccnavi_db_try"
			return 0
		fi
	done
	return 1
}

# そのツリーが属するリポジトリの統合先の名前。分からなければ 1。<ツリー> <ワークスペースルート>
#
# 環境変数 CCNAVI_INTEGRATION_BRANCH、無ければ ccnavi-sync.sh が統合先の取り込み結果
# （sync/<リポジトリ>/integration/head の branch）に書いた名前、無ければデフォルトブランチ
# （ccnavi_default_branch）。ccnavi-fetch.sh（ワークツリーの起点を進める）・ccnavi-git.sh（統合先への
# push の拒否）・ccnavi-review.sh（マージリクエストの宛先）が同じ順で読むよう、ここに 1 つだけ置く。
# ワークツリーは元リポジトリの取り込み結果を読む（ccnavi_repo_key）。
ccnavi_integration() {
	if [ -n "${CCNAVI_INTEGRATION_BRANCH:-}" ]; then
		printf '%s\n' "$CCNAVI_INTEGRATION_BRANCH"
		return 0
	fi
	ccnavi_ig_key=$(ccnavi_repo_key "$1" "$2")
	ccnavi_ig_name=$(ccnavi_record_get "$(ccnavi_state "$2")/sync/$ccnavi_ig_key/integration/head" branch)
	if [ -n "$ccnavi_ig_name" ]; then
		printf '%s\n' "$ccnavi_ig_name"
		return 0
	fi
	ccnavi_default_branch "$1"
}

# そのツリーが、名前の親子のチケットの親のワークツリーか。<ツリー> <名前>
#
# 置き場（承認済みの doing/・done/、提案の todo/・review/）に `ticket: <名前>` の親チケットか提案が
# あれば 0（SessionStart で早送りする対象の条件の 1 つ）。ready が退避した後は、手元の退避の親チケットも見る。
# 子チケット（`parent:` を持つ）は数えない。
#
# sh はチケットを読まない。読むのは実行ファイル（`tree parent <ツリー> <名前>`）で、sh は答えの 1 行を見る。
# 親でなければ 1。ツリーが無ければ聞かずに 1。決められなければ 2（実行ファイルが無い・答えない・古くて
# 副命令を知らない、置き場に名前のファイルが在るのに frontmatter を読めない）。2 をどちらに倒すかは
# 呼ぶ側が決める。止める側の検査は親として扱い、ツリーを動かす側は親でないとして扱う。
# 実行ファイルの --root に渡すワークスペースルートは `.claude/worktrees/<名前>` の 2 つ上。
ccnavi_parent_tree() {
	[ -d "$1" ] || return 1
	case "$1" in
	*/.claude/worktrees/*) ccnavi_pt_ws="${1%/.claude/worktrees/*}" ;;
	*) ccnavi_pt_ws=$(ccnavi_workspace) || return 2 ;;
	esac
	ccnavi_pt_out=$(ccnavi_ask "$ccnavi_pt_ws" tree parent "$1" "$2") || return 2
	[ "$(printf '%s\n' "$ccnavi_pt_out" | head -n 1)" = "tree 1" ] || return 2
	ccnavi_pt_ans=$(printf '%s\n' "$ccnavi_pt_out" | sed -n 's/^parent //p' | head -n 1)
	case "$ccnavi_pt_ans" in
	yes) return 0 ;;
	no) return 1 ;;
	esac
	return 2
}

# ---- タイムアウト監視つきの git（取ってくる操作。ccnavi-fetch.sh と ccnavi-sync.sh が使う）
#
#   ccnavi_git_timed <秒> <標準エラーの書き先> <リポジトリ> <git の引数>...
#
# 認証を尋ねさせず（GIT_TERMINAL_PROMPT=0・GCM_INTERACTIVE=never、ssh は BatchMode）、<秒> で切る。
# ssh の BatchMode は、ユーザが GIT_SSH_COMMAND・GIT_SSH・core.sshCommand を持っていればそちらを尊重する。
# タイムアウト監視の出力は捨てる（つないだままだと、監視の sleep が終わるまで呼ぶ側の `$( )` が閉じない）。
# 戻り値は git のもの（切ったときは 0 でない）。標準出力は捨てないので、呼ぶ側がリダイレクトする。
ccnavi_git_timed() {
	ccnavi_gt_limit="$1"
	ccnavi_gt_err="$2"
	ccnavi_gt_repo="$3"
	shift 3
	ccnavi_gt_ssh="${GIT_SSH_COMMAND:-}"
	if [ -z "$ccnavi_gt_ssh" ] && [ -z "${GIT_SSH:-}" ] &&
		! git -C "$ccnavi_gt_repo" config --get core.sshCommand >/dev/null 2>&1; then
		ccnavi_gt_ssh="ssh -o BatchMode=yes"
	fi
	if [ -n "$ccnavi_gt_ssh" ]; then
		GIT_SSH_COMMAND="$ccnavi_gt_ssh" GIT_TERMINAL_PROMPT=0 GCM_INTERACTIVE=never LC_ALL=C \
			git -C "$ccnavi_gt_repo" -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=10 "$@" \
			</dev/null 2>"$ccnavi_gt_err" &
	else
		GIT_TERMINAL_PROMPT=0 GCM_INTERACTIVE=never LC_ALL=C \
			git -C "$ccnavi_gt_repo" -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=10 "$@" \
			</dev/null 2>"$ccnavi_gt_err" &
	fi
	ccnavi_gt_pid=$!
	# タイムアウト監視の中で標準入出力を先に閉じる（呼ぶ側のパイプを開いたまま残らないように）。
	# sleep は背景で起こして wait で待つ。TERM を受けたら sleep も止めて抜ける（sleep を ppid 1 で残さない）。
	(
		exec </dev/null >/dev/null 2>&1
		ccnavi_gt_sleep=""
		ccnavi_gt_stop=""
		# trap は印を立てて、分かっていれば sleep を止めるだけ。抜けるのは本流で、sleep の pid を控えた後に印を見る
		# （sleep を起こした直後に TERM が来ても、sleep を残さない）。
		trap 'ccnavi_gt_stop=1; test -z "$ccnavi_gt_sleep" || kill "$ccnavi_gt_sleep"' TERM
		sleep "$ccnavi_gt_limit" &
		ccnavi_gt_sleep=$!
		test -z "$ccnavi_gt_stop" || { kill "$ccnavi_gt_sleep"; exit 0; }
		wait "$ccnavi_gt_sleep"
		test -z "$ccnavi_gt_stop" || exit 0
		kill "$ccnavi_gt_pid"
	) &
	ccnavi_gt_dog=$!
	ccnavi_gt_rc=0
	wait "$ccnavi_gt_pid" 2>/dev/null || ccnavi_gt_rc=$?
	kill "$ccnavi_gt_dog" 2>/dev/null || :
	# 見張りの終了を回収して、シェルに「Terminated」の報告を出させない。
	wait "$ccnavi_gt_dog" 2>/dev/null || :
	ccnavi_gt_dog=""
	return "$ccnavi_gt_rc"
}

# git が拒んだ理由を 1 行にする（ccnavi-fetch.sh と ccnavi-sync.sh の文面）。<標準エラーを書いたファイル>
#
# 書きかけとの重なり（「would be overwritten」の後に git が字下げして並べるパス）、索引のロック、
# コミットするユーザの名前が無い、署名の失敗、hook、それ以外は git の 1 行目。理由どおりに言い、
# 重なっていないのに「重なる」とは言わない。
ccnavi_git_refusal() {
	ccnavi_gr_paths=$(awk '/would be overwritten/ { f = 1; next }
		/^[^ \t]/ { f = 0 }
		f && /^[ \t]+[^ \t]/ { sub(/^[ \t]+/, ""); printf "%s%s", sep, $0; sep = " " }' "$1" 2>/dev/null)
	if [ -n "$ccnavi_gr_paths" ]; then
		printf '書きかけの %s と重なる。コミットか退避をしてから再実行してください' "$ccnavi_gr_paths"
	elif grep -q 'index\.lock' "$1" 2>/dev/null; then
		printf '索引のロック（index.lock）が残っている。別の git が動いていないか確かめ、異常終了して残ったものならユーザが削除します。'
	elif grep -Eqi 'tell me who you are|empty ident|user\.email|user\.name' "$1" 2>/dev/null; then
		printf 'コミットするユーザの名前（user.name・user.email）が決まっていない'
	elif grep -Eqi 'gpg|signing' "$1" 2>/dev/null; then
		printf 'コミットの署名に失敗した'
	elif grep -qi 'hook' "$1" 2>/dev/null; then
		printf 'git の hook が止めた（%s）' "$(head -n 1 "$1")"
	else
		printf 'git が拒んだ（%s）' "$(head -n 1 "$1" 2>/dev/null)"
	fi
}
