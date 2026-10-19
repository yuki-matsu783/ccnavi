#!/bin/sh
# ccnavi-sync 親のブランチをリモートから取り込み、リモートから消えた親のブランチと、片付けの残りを見る。
#
#   sh .ccnavi/scripts/ccnavi-sync.sh [<P>...]
#
# ユーザが打つ（ボードのボタン、「承認した」と言われたエージェント）。セッションの頭の
# ccnavi-fetch.sh は早送りしかしないので、分かれた親子のチケットを取り込むのと、親のブランチが
# リモートから消えたかを確かめるのはここだけ（セッションの頭を待たせず、merge の書きかけも残さないため）。
#
# <P> は親の識別子（= .claude/worktrees/<P>）。省けば、.claude/worktrees/ の下の親のワークツリー
# （親チケットか提案があり、HEAD のブランチ名を変換した識別子がフォルダ名と合うもの）を全部。
#
# 親のブランチ名は親のワークツリーの HEAD から読む。識別子はブランチ名の
# `/` を `-` に置き換えたもの。HEAD から読めない・識別子と合わなければ、その親子のチケットは取り込まずに止める。
#
# 親子のチケットごとの取り込み状態は持たない。判定は親のブランチとチケットの置き場だけで決まる。
# 親のブランチがリモートから消えたときは、ここでは記録を書かず、診断（下の「P がリモートに無い」）を言って
# 終了コード 1 にする。親のブランチへの push は、共通の push の関門（ccnavi_push_gate）が止める。
# 捨てた親子のチケットは、親のワークツリーを片付ければ、この sh の対象から外れ、関門も掛からない。
#
# リポジトリ（ワークスペース自身と、親子のチケットの元のプロジェクト）ごとに 1 回、次の順に行う。
#
#   1. `ls-remote --heads origin` で全ブランチの有無を得る。失敗したら（オフライン・認証）止める
#   2. 統合先の名前を決める。ホストのデフォルトブランチ（`ls-remote --symref origin HEAD`）、
#      読めなければ手元の origin/HEAD、それも無ければ一覧にある main・master。決まらなければ止める
#   3. 統合先を fetch する（取り込みの後の検査が、レイヤーのずれを origin/<統合先> と比べるため）。
#      成功した後、手元の origin/HEAD がホストに聞いた名前と違えば向け直す（fetch の前には向け直さない）
#
# 親子のチケットごとに次を行う（ロックを待って取る）。
#
#   - 途中の操作（merge・cherry-pick・revert・rebase）があれば何もせず止める
#   - P がリモートにある: fetch して、早送りできれば早送り、分かれていれば merge。merge は
#     索引が HEAD と同じときだけで、衝突したら、この sh が始めた merge だけを取りやめて
#     ユーザに回す。続けて、親チケットの mr 欄の MR の番号で
#     MR がマージ済みかを聞き、マージ済みなら下の片付けをする（ホストはマージの後もブランチを残すことがある）
#   - P がリモートに無い: 送った形跡（origin/<P> の参照、承認済みの親チケット、.requested のマーカー、退避。
#     push の関門と同じ述語）が無ければ、一度も送っていない親子のチケットで今のまま。
#     送った形跡があれば、ccnavi-review.sh merged <番号> に聞く。閉じたかの根拠は MR がマージ済みかだけ。答えが得られなければ止める。
#     マージされていないと分かったときは、診断（戻し方）を言って止める
#   - マージ済みと分かったら、手元を片付ける（実行ファイルの `sync tidy`。条件の確認も削除も実行ファイルが行う）。
#     親のワークツリー（未コミットの変更や git が無視しているファイルがあれば残す）と、親の手元のブランチ
#     （手元の先頭が MR の先頭に含まれ、未 push のコミットが無いときだけ）を消し、残ったものは理由と手で消す
#     コマンドを 1 行ずつ言う。何も残らなければ ready の後の片付けの記録（retire/<P>.json）も実行ファイルが消す。
#     MR の先頭の sha はホストが答えた（ccnavi-review.sh merged <番号> の <sha>）。MR の番号は親チケットの
#     mr 欄から引く。番号が無ければ、閉じたか判断できないと 1 行言う
#   - 親のワークツリーを片付けた後（ready の後）の親子のチケットは、実行ファイルの `sync retired`
#     （retire/<P>.json の一覧）から引く。同じく MR がマージ済みかを聞き、マージ済みなら同じ片付けをする。
#     ブランチ名はホストが答えた MR の元ブランチを使う。マージされていなければ片付けない。
#     確かめられなければ推定せずに止める。<P> を渡したときも同じ
#
# 統合先を取ってきた後、親のブランチがリモートにある親子のチケットごとに、実行ファイルの
# `ccnavi sync check <P> <リポジトリ>` で判定し直す（どのチケットを正とするかの検査と、承認済みチケットの
# 判定し直し）。`error ...` の行があれば表示して終了コード 1 にする（状態は書かない）。`warn ...` の行は表示だけ。
# 直してから同じ P でこの sh を打ち直すと、検査し直す。実行ファイルが検査を実行できなかった
# （古い実行ファイルが `sync check` を知らない、など。答えの頭に `check 1` が無い）ときは、警告だけ出す
# （検査の error とは分ける）。
#
# 実行ファイルはネットワークに出ない。ここが git で取ってくる。
# 置き場のパスは実行ファイル（`ccnavi sync paths`）に、チケットの YAML が要る
# 親の状態（親のワークツリーか・承認済みか・依頼のマーカーがあるか・退避にあるか・mr 欄）は
# `ccnavi tree parent` に聞く（sh はチケットを読まない）。
# 実行ファイルが無ければ、ほかの sh と同じく既定のパス（固定）を使う。在るのに
# 答えなかったときは、置き場を取り違えないよう止める。
#
# 環境変数: CCNAVI_LOCK_WAIT（ロックを待つ秒、既定 120）/
#   CCNAVI_SYNC_TIMEOUT（ls-remote・fetch 1 回のタイムアウト監視の秒、既定 60）
# 終了コード: 0 全部取り込んだ（取り込むものが無いを含む） / 1 止めた親子のチケットかリポジトリがある /
#           2 引数か環境の誤り

set -eu

. "$(dirname "$0")/ccnavi-common.sh"

usage() {
	cat <<'USAGE'
sh .ccnavi/scripts/ccnavi-sync.sh [<P>...]

  親チケット <P>（識別子。省けば .claude/worktrees/ の下の親のワークツリー全部）の親のブランチ
  （親のワークツリーの HEAD のブランチ。/ を - に置き換えたものが <P>）をリモートから取り込む。
  分かれていれば merge し、衝突したら取りやめてユーザに回す。
  取り込んだ後、親子のチケットを判定し直し、止める理由があれば言って終了コード 1 にする。
  リモートから消えた親のブランチは、MR がマージ済みかを見て「閉じた」か「消えた」かを決める。
  MR がマージ済みなら、手元の親のブランチとワークツリー、片付けの記録を片付ける
  （未 push のコミットや未コミットの変更があれば消さずに理由を言う）。

  統合先: ホストのデフォルトブランチ。読めなければ手元の origin/HEAD、それも無ければ main・master
USAGE
}

case "${1:-}" in
-h | --help | help)
	usage
	exit 0
	;;
--forget)
	printf 'ccnavi-sync: --forget は廃止しました。捨てた親子のチケットは、親のワークツリーを片付ければこの sh の対象から外れます（push の関門も掛からない）。ready の後に放棄した親の記録は、ユーザが ccnavi worktree retire --abandon <P> で消します。\n' >&2
	exit 2
	;;
-*)
	printf 'ccnavi-sync: %s は受けません。\n' "$1" >&2
	usage >&2
	exit 2
	;;
esac

for want in ${1+"$@"}; do
	if ! ccnavi_is_ident "$want"; then
		printf 'ccnavi-sync: %s は親の識別子の形ではありません（親のブランチ名ではなく識別子を渡します）。\n' "$want" >&2
		exit 2
	fi
done

root=$(ccnavi_workspace) || {
	printf 'ccnavi-sync: ワークスペースルートが見つかりません（.ccnavi/scripts/ccnavi-common.sh を持つ親を cwd から上へ探しました）。ワークスペースの中で実行するか、CCNAVI_WORKSPACE にワークスペースルートの絶対パスを渡してください。\n' >&2
	exit 2
}
ccnavi_log_root="$root"
state=$(ccnavi_state "$root")
here_sh="$(dirname "$0")"
# 打った場所。手元の片付けが、自分の居るワークツリーを消さないために渡す。
sync_cwd="$(pwd -W 2>/dev/null || pwd)"

number_or() {
	case "${1:-}" in
	'' | *[!0-9]*) printf '%s\n' "$2" ;;
	*) printf '%s\n' "$1" ;;
	esac
}
lock_wait=$(number_or "${CCNAVI_LOCK_WAIT:-}" 120)
timeout=$(number_or "${CCNAVI_SYNC_TIMEOUT:-}" 60)

scratch=$(mktemp -d 2>/dev/null || mktemp -d -t ccnavi-sync) || {
	printf 'ccnavi-sync: 一時ディレクトリが作れません。\n' >&2
	exit 2
}
: >"$scratch/failed"
tab=$(printf '\t')

# 後始末。この sh が始めた merge の途中（目印の変数 sync_merging）で切られたら取りやめ、ロックを外す。
# dash は EXIT の trap を INT・TERM・HUP で走らせないので、そちらにも置く。2 度走っても害は無い。
sync_merging=""
cleaned=""
cleanup() {
	[ -z "$cleaned" ] || return 0
	cleaned=yes
	if [ -n "$sync_merging" ] && [ -e "$(git_path "$sync_merging" MERGE_HEAD)" ]; then
		git -C "$sync_merging" merge --abort >/dev/null 2>&1 || :
	fi
	ccnavi_lock_drop
	rm -rf "$scratch"
}
trap 'cleanup' EXIT
trap 'cleanup; exit 130' INT TERM HUP

log_info 受け付けた -- "args=$#"

# `git rev-parse --git-path <名前>` をツリーからのパスにする（ワークツリーでは git ディレクトリが別）。
git_path() {
	gp_rel=$(git -C "$1" rev-parse --git-path "$2" 2>/dev/null || :)
	case "$gp_rel" in
	'') printf '%s\n' "$1/.git/$2" ;;
	/* | [A-Za-z]:*) printf '%s\n' "$gp_rel" ;;
	*) printf '%s\n' "$1/$gp_rel" ;;
	esac
}

# ---- 置き場のパス。実行ファイルに聞く（設定ファイルは sh で読まない）。

sync_info=""
info_from=""
if bin=$(ccnavi_bin "$root"); then
	info_from="$bin"
	sync_info=$("$bin" --root "$root" sync paths 2>"$scratch/info" </dev/null) || info_from="failed:$bin"
elif [ -f "$root/src/ccnavi/__main__.py" ] && command -v uv >/dev/null 2>&1; then
	info_from="uv run python -m ccnavi"
	sync_info=$(cd "$root" && uv run --quiet python -m ccnavi --root "$root" sync paths 2>"$scratch/info" </dev/null) ||
		info_from="failed:uv run python -m ccnavi"
fi
# Windows の実行ファイルの CRLF の CR を除く（1 行 1 項目の値の末尾に CR を残さない）。
sync_info=$(printf '%s\n' "$sync_info" | tr -d '\r')
case "$info_from" in
failed:*)
	printf 'ccnavi-sync: 実行ファイル（%s）が置き場のパスを返さなかった（%s）。置き場を取り違えないよう止めた。\n' \
		"${info_from#failed:}" "$(head -n 1 "$scratch/info" 2>/dev/null)" >&2
	exit 2
	;;
esac
info() {
	printf '%s\n' "$sync_info" | sed -n "s/^$1 //p" | head -n 1
}

# 実行ファイルを起こす。`sync paths` を答えたのと同じもの。<引数>...
run_ccnavi() {
	case "$info_from" in
	'uv run python -m ccnavi')
		(cd "$root" && uv run --quiet python -m ccnavi --root "$root" "$@" </dev/null)
		;;
	*)
		"$info_from" --root "$root" "$@" </dev/null
		;;
	esac
}

# 親子のチケットの親のブランチ名。<P>
#
# 親のワークツリー（.claude/worktrees/<P>）の HEAD から読む（ccnavi_head_branch）。HEAD のブランチ名を
# 変換（/ を - に置き換え）した識別子が <P> と一致するときだけ出す。ワークツリーが無い・ブランチの外なら 1、
# 一致しない・使えない名前なら 2（呼ぶ側はその親子のチケットを取り込まずに止めたと言う）。
family_branch() {
	ccnavi_head_branch "$root/.claude/worktrees/$1" "$1"
}
projects=projects # 固定
projects="${projects%/}"

# ---- 統合先の名前

# ls-remote の一覧に refs/heads/<名前> があるか。前方一致で取り違えないよう、2 列目を
# grep -F -x で完全一致に比べる（i0131 と i0131-x）。<一覧> <名前>
has_head() {
	cut -f 2 "$1" | grep -F -x -q -- "refs/heads/$2"
}

# ホストのデフォルトブランチ。`ls-remote --symref origin HEAD` が指すもの（一覧にあるもの）、
# 読めなければ手元の origin/HEAD、無ければ一覧にある main・master。どれも無ければ 1。<リポジトリ> <一覧>
default_branch() {
	db_head=""
	db_host=""
	rm -f "$scratch/db-host"
	if ccnavi_git_timed "$timeout" "$scratch/err-symref" "$1" ls-remote --symref origin HEAD >"$scratch/symref"; then
		db_head=$(sed -n "s|^ref: refs/heads/\\(.*\\)${tab}HEAD\$|\\1|p" "$scratch/symref" | head -n 1)
		[ -z "$db_head" ] || db_host=yes
	fi
	if [ -z "$db_head" ]; then
		db_head=$(git -C "$1" symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null || :)
		db_head="${db_head#origin/}"
	fi
	# 先頭が - の名前は、後ろの git の引数に渡るとオプションに読まれる。使わず、main・master に回す。
	case "$db_head" in
	-*)
		db_head=""
		db_host=""
		;;
	esac
	# ホストの答えが手元の origin/HEAD と綴りの大文字小文字だけ違うなら、同じ名前として手元の綴りを使う。
	# 別の綴りで取ると、大文字小文字を区別しない FS で origin/<名前> が衝突する。
	if [ -n "$db_host" ]; then
		db_local=$(git -C "$1" symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null || :)
		db_local="${db_local#origin/}"
		db_local_f=$(printf '%s' "$db_local" | tr '[:upper:]' '[:lower:]')
		db_head_f=$(printf '%s' "$db_head" | tr '[:upper:]' '[:lower:]')
		if [ -n "$db_local" ] && [ "$db_local_f" = "$db_head_f" ]; then
			printf '%s\n' "$db_local"
			return 0
		fi
	fi
	if [ -n "$db_head" ] && has_head "$2" "$db_head"; then
		# ホストに聞いて得た名前か。`$( )` の中で呼ぶので、変数ではなくファイルで呼び元へ渡す。
		[ -z "$db_host" ] || : >"$scratch/db-host"
		printf '%s\n' "$db_head"
		return 0
	fi
	for db_try in main master; do
		if has_head "$2" "$db_try"; then
			printf '%s\n' "$db_try"
			return 0
		fi
	done
	return 1
}

# fetch 1 本。単一ブランチの clone でも origin/<ブランチ> が進むよう、行き先を書いて取る
# （sh の中の git。ccnavi-git.sh の入口の refspec の拒否とは別の話）。
# 失敗した理由（git の標準エラー）は $scratch/err に残る。<リポジトリ> <ブランチ>
fetch_one() {
	ccnavi_git_timed "$timeout" "$scratch/err" "$1" \
		fetch --quiet --no-tags origin "+refs/heads/$2:refs/remotes/origin/$2" >/dev/null
}

fail_note() {
	printf 'fail\n' >>"$scratch/failed"
}

# 途中の操作（merge・cherry-pick・revert・sequencer・rebase）の名前。無ければ空。<ツリー>
busy_state() {
	for bs_name in MERGE_HEAD CHERRY_PICK_HEAD REVERT_HEAD sequencer rebase-merge rebase-apply; do
		if [ -e "$(git_path "$1" "$bs_name")" ]; then
			printf '%s\n' "$bs_name"
			return 0
		fi
	done
	return 0
}

# 親のブランチ名が決まらなかった親子のチケットの文面。<P> <family_branch の終了コード>
branch_refused() {
	if [ "$2" = 1 ]; then
		printf '%s: 親のワークツリー（.claude/worktrees/%s）の HEAD からブランチ名を読めない（ブランチの外にいる）。取り込まずに止めた。ブランチ名の / を - に置き換えたものが %s になるブランチに戻してから再実行してください\n' "$1" "$1" "$1"
	else
		printf '%s: 親のワークツリー（.claude/worktrees/%s）の HEAD のブランチ（%s）の / を - に置き換えたものが %s と合わない。取り込まずに止めた。ブランチ名の / を - に置き換えたものが %s になるブランチに戻してから再実行してください\n' \
			"$1" "$1" "$(git -C "$root/.claude/worktrees/$1" symbolic-ref --quiet --short HEAD 2>/dev/null || :)" "$1" "$1"
	fi
}

# ---- 親子のチケットを集める。<P><タブ><ツリー><タブ><リポジトリの名前><タブ><親のブランチ名> を 1 行ずつ。

: >"$scratch/families"
: >"$scratch/repos"
repo_dir_of() {
	# <リポジトリの名前> → そのリポジトリのディレクトリ
	if [ "$1" = self ]; then
		printf '%s\n' "$root"
	else
		case "$projects" in
		/* | [A-Za-z]:*) printf '%s/%s\n' "$projects" "$1" ;;
		*) printf '%s/%s/%s\n' "$root" "$projects" "$1" ;;
		esac
	fi
}
has_family_line() {
	awk -F "$tab" -v p="$1" '$1 == p { found = 1 } END { exit found ? 0 : 1 }' "$scratch/families"
}
repo_has_families() {
	awk -F "$tab" -v k="$1" '$3 == k { found = 1 } END { exit found ? 0 : 1 }' "$scratch/families"
}
add_family() {
	has_family_line "$1" && return 0
	af_key=$(ccnavi_repo_key "$2" "$root")
	printf '%s\t%s\t%s\t%s\n' "$1" "$2" "$af_key" "$3" >>"$scratch/families"
	grep -F -x -q -- "$af_key" "$scratch/repos" 2>/dev/null || printf '%s\n' "$af_key" >>"$scratch/repos"
}

# 親のワークツリーを片付けた後の親子のチケット（ready が書いた retire/<P>.json）。実行ファイルの
# `sync retired` から引く。retired_all は全部、retired は今回の対象に選んだもの。
# どちらも <P><タブ><リポジトリの名前><タブ><MR の番号><タブ><ブランチ>（空は -）。
# retired_bad は列挙できなかったもの（<P> <理由>）。
: >"$scratch/retired-all"
: >"$scratch/retired"
: >"$scratch/retired-bad"
load_retired() {
	[ -n "$info_from" ] || return 0
	run_ccnavi sync retired >"$scratch/retired-raw" 2>/dev/null || :
	tr -d '\r' <"$scratch/retired-raw" >"$scratch/retired-out"
	if [ "$(head -n 1 "$scratch/retired-out")" != "retired 1" ]; then
		printf '案内: 実行ファイルが片付けの記録の一覧（sync retired）に答えなかった（実行ファイルが古い可能性）。ready の後の親の片付けは列挙できない。実行ファイルを新しくして再実行してください\n'
		return 0
	fi
	awk 'BEGIN { OFS = "\t" } $1 == "retired" && NF >= 5 { print $2, $3, $4, $5 }' "$scratch/retired-out" >"$scratch/retired-all"
	sed -n 's/^incomplete //p' "$scratch/retired-out" >"$scratch/retired-bad"
}
retired_loaded=""
ensure_retired() {
	[ -z "$retired_loaded" ] || return 0
	retired_loaded=yes
	load_retired
}
# <P> が片付けの記録を持つなら対象に選んで 0。
add_retired() {
	ensure_retired
	ar_line=$(awk -F "$tab" -v p="$1" '$1 == p { print; exit }' "$scratch/retired-all")
	[ -n "$ar_line" ] || return 1
	if ! awk -F "$tab" -v p="$1" '$1 == p { found = 1 } END { exit found ? 0 : 1 }' "$scratch/retired"; then
		printf '%s\n' "$ar_line" >>"$scratch/retired"
		ar_key=$(printf '%s\n' "$ar_line" | cut -f 2)
		grep -F -x -q -- "$ar_key" "$scratch/repos" 2>/dev/null || printf '%s\n' "$ar_key" >>"$scratch/repos"
	fi
	return 0
}
# <P> の片付けの記録が列挙できないものなら、1 行言って 0。
say_retired_bad() {
	ensure_retired
	sb_line=$(awk -v p="$1" '$1 == p { print; exit }' "$scratch/retired-bad")
	[ -n "$sb_line" ] || return 1
	printf '%s: ready の後の片付けの記録（retire/%s.json）が使えない（%s）。閉じたか判断できないので手元は片付けない。MR がマージ済みならブランチとログを手で片付け、捨てた親ならユーザが sh %s/ccnavi-launcher.sh worktree retire --abandon %s で記録を消してください\n' \
		"$1" "$1" "${sb_line#"$1" }" "$here_sh" "$1"
	return 0
}

if [ "$#" -gt 0 ]; then
	for want in "$@"; do
		has_family_line "$want" && continue
		tree="$root/.claude/worktrees/$want"
		if [ ! -e "$tree" ] && [ ! -L "$tree" ]; then
			if add_retired "$want"; then
				continue
			fi
			if say_retired_bad "$want"; then
				continue
			fi
		fi
		if [ ! -d "$tree" ] || [ -L "$tree" ]; then
			printf '%s: 親のワークツリー（.claude/worktrees/%s）がありません。切り直してから再実行してください（sh %s/ccnavi-git.sh fetch origin <ブランチ> のあと worktree add .claude/worktrees/%s <ブランチ>。ブランチ名の / を - に置き換えたものが %s になる）\n' \
				"$want" "$want" "$here_sh" "$want" "$want"
			fail_note
			continue
		fi
		fb_rc=0
		want_branch=$(family_branch "$want") || fb_rc=$?
		if [ "$fb_rc" -ne 0 ]; then
			branch_refused "$want" "$fb_rc"
			fail_note
			continue
		fi
		pt_rc=0
		ccnavi_parent_tree "$tree" "$want" || pt_rc=$?
		case "$pt_rc" in
		1)
			printf '%s: .claude/worktrees/%s に親チケットも提案もありません。親のワークツリーではないので取り込まない\n' "$want" "$want"
			fail_note
			continue
			;;
		2)
			printf '%s: .claude/worktrees/%s に親チケットか提案があるかを実行ファイルに確かめられなかった（実行ファイルが無い・古い、またはチケットの frontmatter を読めない）。取り込まない\n' "$want" "$want"
			fail_note
			continue
			;;
		esac
		add_family "$want" "$tree" "$want_branch"
	done
else
	printf 'self\n' >"$scratch/repos"
	case "$projects" in
	/* | [A-Za-z]:*) project_root="$projects" ;;
	*) project_root="$root/$projects" ;;
	esac
	for dir in "$project_root"/*; do
		[ -e "$dir/.git" ] || continue
		[ -L "$dir" ] && continue
		printf '%s\n' "$(basename "$dir")" >>"$scratch/repos"
	done
	for tree in "$root/.claude/worktrees"/*; do
		[ -d "$tree" ] || continue
		[ -L "$tree" ] && continue
		name=$(basename "$tree")
		branch=$(git -C "$tree" rev-parse --abbrev-ref HEAD 2>/dev/null || :)
		[ -n "$branch" ] && [ "$branch" != HEAD ] || continue
		ccnavi_parent_tree "$tree" "$name" || continue
		# HEAD のブランチ名を変換した識別子がフォルダ名と合うものだけ。
		name_branch=$(family_branch "$name") || continue
		[ "$branch" = "$name_branch" ] || continue
		add_family "$name" "$tree" "$name_branch"
	done
	ensure_retired
	while IFS="$tab" read -r ar_p ar_rest; do
		[ -n "$ar_p" ] || continue
		has_family_line "$ar_p" && continue
		[ -e "$root/.claude/worktrees/$ar_p" ] || [ -L "$root/.claude/worktrees/$ar_p" ] && continue
		add_retired "$ar_p" || :
	done <"$scratch/retired-all"
	while IFS= read -r bad_line; do
		bad_p="${bad_line%% *}"
		[ -n "$bad_p" ] || continue
		[ -e "$root/.claude/worktrees/$bad_p" ] || [ -L "$root/.claude/worktrees/$bad_p" ] && continue
		say_retired_bad "$bad_p" || :
	done <"$scratch/retired-bad"
fi

# ---- 親子のチケット 1 つ。<P> <ツリー> <リポジトリの名前> <リポジトリ> <統合先> <一覧> <親のブランチ名>
#
# ロックは識別子 P、ref・fetch・ls-remote の一覧は親のブランチ名 B。

sync_family() {
	P="$1"
	tree="$2"
	key="$3"
	repo="$4"
	integ="$5"
	heads="$6"
	# 親のブランチ名は、呼び手が親のワークツリーの HEAD から読んだもの。
	B="${7:-}"
	if [ -z "$B" ]; then
		printf '%s: 親のブランチ名を親のワークツリーの HEAD から読めなかった。取り込まない\n' "$P"
		fail_note
		return 0
	fi

	branch=$(git -C "$tree" rev-parse --abbrev-ref HEAD 2>/dev/null || :)
	if [ "$branch" != "$B" ]; then
		printf '%s: 親のワークツリーが %s の上に居る。親のブランチ %s に戻してから再実行してください\n' "$P" "${branch:-（ブランチの外）}" "$B"
		fail_note
		return 0
	fi
	# set -e の下なので、戻り値は || で受ける（そのまま打つと、取れなかった時点で sh ごと抜ける）。
	lock_rc=0
	ccnavi_lock_take "$root" "$key" "$P" "$lock_wait" || lock_rc=$?
	case "$lock_rc" in
	0) ;;
	2)
		printf '%s: 古いロック（%s/locks/%s/%s）を強制取得する途中で止まり、元に戻せなかった。ユーザが中身を見て片付ける\n' "$P" "$state" "$key" "$P"
		fail_note
		return 0
		;;
	*)
		printf '%s: 他の操作がロックを持っている（%s）。終わってから再実行してください\n' "$P" \
			"$(ccnavi_lock_owner "$state/locks/$key/$P")"
		fail_note
		return 0
		;;
	esac

	# 親チケットの mr 欄と、送った形跡（着手済み・依頼・退避）を実行ファイルに聞く。
	ccnavi_parent_info "$tree" "$P" || :
	fam_mr="$ccnavi_pi_mr"

	busy=$(busy_state "$tree")
	if [ -n "$busy" ]; then
		printf '%s: 親のワークツリーに途中の操作（%s）がある。済ませるか取りやめてから再実行してください（この sh は変更しない）\n' "$P" "$busy"
		fail_note
	elif has_head "$heads" "$B"; then
		sync_present
		merged_present
	else
		sync_absent
	fi
	ccnavi_lock_drop
	return 0
}

# P がリモートにある。取ってきて、早送りか merge。
sync_present() {
	if ! fetch_one "$tree" "$B"; then
		printf '%s: 親のブランチ %s を取ってこられなかった（%s）\n' "$P" "$B" "$(head -n 1 "$scratch/err")"
		fail_note
		return 0
	fi
	remote_sha=$(git -C "$tree" rev-parse --verify --quiet "refs/remotes/origin/$B^{commit}" 2>/dev/null || :)
	local_sha=$(git -C "$tree" rev-parse --verify --quiet "HEAD^{commit}" 2>/dev/null || :)
	if [ -z "$remote_sha" ] || [ -z "$local_sha" ]; then
		printf '%s: 先頭を読めなかった\n' "$P"
		fail_note
		return 0
	fi
	if [ "$remote_sha" = "$local_sha" ]; then
		printf '%s: リモートと同じ\n' "$P"
	elif git -C "$tree" merge-base --is-ancestor "$remote_sha" "$local_sha" 2>/dev/null; then
		ahead=$(git -C "$tree" rev-list --count "$remote_sha..$local_sha" 2>/dev/null || echo '?')
		printf '%s: 手元が %s 件先に進んでいる（まだ送っていない）。取り込むものはありません。\n' "$P" "$ahead"
	elif git -C "$tree" merge-base --is-ancestor "$local_sha" "$remote_sha" 2>/dev/null; then
		behind=$(git -C "$tree" rev-list --count "$local_sha..$remote_sha" 2>/dev/null || echo '?')
		if LC_ALL=C git -C "$tree" merge --ff-only --quiet "$remote_sha" </dev/null >/dev/null 2>"$scratch/err"; then
			printf '%s: リモートの %s 件を早送りで取り込んだ\n' "$P" "$behind"
		else
			printf '%s: 早送りできなかった。%s\n' "$P" "$(ccnavi_git_refusal "$scratch/err")"
			fail_note
		fi
	else
		# 分かれている。非 ff の merge は、重ならないステージ済みの変更があっても拒む（git 2.43）ので、
		# 先に見て言う。
		if ! git -C "$tree" diff --cached --quiet 2>/dev/null; then
			printf '%s: リモートと分かれていて merge が必要ですが、ステージ済みの変更がある。コミットするか sh %s/ccnavi-git.sh restore --staged <パス> で外してから再実行してください\n' \
				"$P" "$here_sh"
			fail_note
		else
			# 途中の操作が無いのは確かめてあるので、この後の MERGE_HEAD はこの merge のもの。
			sync_merging="$tree"
			if LC_ALL=C git -C "$tree" merge --no-edit --quiet -m "ccnavi: origin/$B を取り込む" "$remote_sha" \
				</dev/null >"$scratch/out" 2>"$scratch/err"; then
				printf '%s: リモートと分かれていたので merge で取り込んだ\n' "$P"
			else
				conflicted=$(git -C "$tree" diff --name-only --diff-filter=U 2>/dev/null | tr '\n' ' ' | sed 's/ *$//')
				if [ -e "$(git_path "$tree" MERGE_HEAD)" ]; then
					git -C "$tree" merge --abort >/dev/null 2>&1 || :
				fi
				if [ -n "$conflicted" ]; then
					printf '%s: リモートと分かれていて merge が衝突した（%s）。取り込みをやめた（merge --abort）。どちらを採るかはユーザが決める\n' \
						"$P" "$conflicted"
				else
					cat "$scratch/out" >>"$scratch/err"
					printf '%s: リモートと分かれていて merge できなかった。%s\n' "$P" "$(ccnavi_git_refusal "$scratch/err")"
				fi
				fail_note
			fi
			sync_merging=""
		fi
	fi
	return 0
}

# P がリモートにあっても、MR がマージ済みなら閉じている。ホストはマージの後もブランチを残すことがあり
# （GitHub の既定）、ready が親のワークツリーを片付けられなかった（未コミットの変更など）ときは、
# ブランチが残る限り片付けの機会が無くなる。親チケットの mr 欄の MR の番号で聞き、マージ済みなら片付ける。
# 番号が無ければ推定せず 1 行言う。マージされていなければ何も言わない。確かめられなければ 1 行言うが、
# 取り込みそのものは済んでいるので失敗にはしない（次の取り込みでまた聞く）。
merged_present() {
	merged=$(ask_merged "$tree" "$fam_mr")
	merged_rc="${merged%%:*}"
	merged="${merged#*:}"
	case "$merged_rc:$merged" in
	nomr:*) no_mr_note "（親のブランチはリモートにある）" ;;
	*:'merged '*)
		split_merged "${merged#merged }"
		if [ -n "$merged_branch" ] && [ "$merged_branch" != "$B" ]; then
			printf '%s: マージリクエスト（%s）の元ブランチ %s が親のブランチ %s と違う。手元は片付けない（親チケットの mr 欄を確かめてください）\n' \
				"$P" "$merged_mr" "$merged_branch" "$B"
			fail_note
			return 0
		fi
		printf '%s: 親のブランチ %s はリモートに残っているが、マージリクエスト（%s）はマージ済み（クローズ済みの親子のチケット）。手元を片付ける\n' \
			"$P" "$B" "$merged_mr"
		tidy_merged "$merged_sha"
		;;
	0:none) ;;
	*)
		printf '%s: マージリクエストがマージ済みかを確かめられなかった（ccnavi-review.sh merged が結果を返さなかった）。閉じていれば次の取り込みで片付ける\n' "$P"
		;;
	esac
	return 0
}

# 親の MR がマージ済みかをホストに聞く（ccnavi-review.sh merged <番号>）。<リポジトリかツリーのディレクトリ> <MR の番号>
#
# 聞くのは親チケットの mr 欄（ワークツリーの後は retire/<P>.json）の MR の番号だけ。番号が無ければ聞かずに
# nomr: を返す（ディレクトリのブランチから MR を探すと、HEAD が別のブランチなら別の MR を拾うため）。
# 答えは「終了コード:1 行目」で、1 行目は merged <番号> <先頭の sha> <元ブランチ> / none /
# それ以外（道具・トークンが無い、API が失敗した）。
ask_merged() {
	am_mr="${2:-}"
	case "$am_mr" in
	'' | *[!0-9]*)
		printf 'nomr:\n'
		return 0
		;;
	esac
	am_rc=0
	am_out=$(cd "$1" && sh "$here_sh/ccnavi-review.sh" merged "$am_mr" 2>/dev/null </dev/null) || am_rc=$?
	printf '%s:%s\n' "$am_rc" "$(printf '%s\n' "$am_out" | head -n 1)"
}

# ask_merged の merged の行を分ける。<merged の後ろ（<番号> <先頭の sha> <元ブランチ>）>
# merged_mr・merged_sha・merged_branch に入れる。無い欄は空。
split_merged() {
	merged_mr="${1%% *}"
	merged_sha=""
	merged_branch=""
	case "$1" in
	*' '*)
		sm_rest="${1#* }"
		merged_sha="${sm_rest%% *}"
		case "$sm_rest" in
		*' '*) merged_branch="${sm_rest#* }" ;;
		esac
		;;
	esac
	merged_branch="${merged_branch%% *}"
}

# MR の番号が無いときの 1 行。<添える語>
no_mr_note() {
	printf '%s: 親チケットの mr 欄に MR の番号が無いので、閉じたか（MR がマージ済みか）判断できない%s\n' "$P" "$1"
	if [ -z "$ccnavi_pi_hasmr" ]; then
		printf '案内: %s: 実行ファイルの答えに mr の行が無かった（実行ファイルが古い可能性）。親チケットに mr 欄があるなら、実行ファイルを新しくして再実行してください\n' "$P"
	fi
}

# MR がマージ済みと分かった後の手元の片付け。<MR の先頭の sha>
#
# 親のワークツリーと手元のブランチは実行ファイル（`sync tidy`）が消す。手元の先頭が MR の先頭に含まれ
# （未 push のコミットが無い）、未コミットの変更が無いときだけ。消さなかったものは理由と手で消すコマンドを
# 1 行ずつ言う。片付けの記録（retire/<P>.json）は、実行ファイルが「残ったものは無い」（record yes）ときに消す。
# sha が分からない・実行ファイルが無いときは、含まれるか確かめられないので何も消さない。
tidy_merged() {
	if [ -z "$1" ]; then
		printf '%s: マージリクエストの先頭の sha をホストから取れなかったので、手元のブランチは片付けない（含まれるか確かめられない）\n' "$P"
		return 0
	fi
	if [ -z "$info_from" ]; then
		printf '%s: 実行ファイルが無いので、手元の片付けはしなかった\n' "$P"
		return 0
	fi
	tm_rc=0
	run_ccnavi --cwd "$sync_cwd" sync tidy "$P" "$key" "$B" "$1" >"$scratch/tidy-raw" 2>"$scratch/tidy-err" || tm_rc=$?
	tr -d '\r' <"$scratch/tidy-raw" >"$scratch/tidy"
	if [ "$tm_rc" -ne 0 ]; then
		printf '%s: 手元の片付けを実行できなかった（%s）。実行ファイルを新しくして再実行してください\n' "$P" "$(head -n 1 "$scratch/tidy-err" 2>/dev/null)"
		fail_note
		return 0
	fi
	sed -e '/^record /d' -e 's/^/  /' "$scratch/tidy"
	case "$(tail -n 1 "$scratch/tidy")" in
	'record yes')
		printf '%s: 片付けた\n' "$P"
		log_info マージ済みの親を片付けた -- "family=$P" "repo=$key"
		;;
	'record no')
		printf '%s: 消さなかったものがある（理由は上。直してから再実行すれば片付く）\n' "$P"
		;;
	esac
}

# P がリモートに無い。閉じたか、消えたか。
#
# 送った形跡は push の関門と同じ述語（ccnavi_parent_trace）。形跡が無ければ、一度も送っていない親子のチケット。
sync_absent() {
	trace=no
	if ccnavi_parent_trace "$tree" "$B"; then
		trace=yes
	fi
	if [ "$trace" = no ]; then
		if [ "$ccnavi_pi_approved" = unreadable ]; then
			printf '%s: 承認済みの親チケットを読めないので、送った親子のチケットかを確かめられない（チケットの frontmatter を直してから再実行してください）\n' "$P"
			fail_note
			return 0
		fi
		printf '%s: リモートにありません（まだ送っていない親子のチケット）。今の手元の動きのまま\n' "$P"
		return 0
	fi
	# ccnavi-review.sh の道具（gh / glab / curl とトークン）で、MR がマージ済みかを先に聞く。答えは
	# merged <番号> / none（マージされた MR が無い）/ それ以外（道具・トークンが無い、API が失敗した）。
	merged=$(ask_merged "$tree" "$fam_mr")
	merged_rc="${merged%%:*}"
	merged="${merged#*:}"
	case "$merged_rc:$merged" in
	nomr:*)
		no_mr_note "。止めた（親チケットの mr 欄に MR の番号を書く。ユーザが確かめてください）"
		fail_note
		return 0
		;;
	*:'merged '*)
		split_merged "${merged#merged }"
		if [ -n "$merged_branch" ] && [ "$merged_branch" != "$B" ]; then
			printf '%s: マージリクエスト（%s）の元ブランチ %s が親のブランチ %s と違う。手元は片付けずに止めた（親チケットの mr 欄を確かめてください）\n' \
				"$P" "$merged_mr" "$merged_branch" "$B"
			fail_note
			return 0
		fi
		printf '%s: リモートから消えたが、マージリクエスト（%s）はマージ済み（クローズ済みの親子のチケット）。手元を片付ける\n' \
			"$P" "$merged_mr"
		tidy_merged "$merged_sha"
		return 0
		;;
	0:none) ;;
	*)
		# マージ済みかを確かめられないときは、統合先の done/ や手元の退避（logs/archive/）からは推定せずに止める。
		printf '%s: 親のブランチがリモートに無い。マージリクエストがマージ済みかを確かめられなかった（ccnavi-review.sh merged が結果を返さなかった。gh・glab か、curl とトークンが必要です）。止めた。確かめられる道具を用意して再実行するか、ユーザが確かめてください\n' "$P"
		fail_note
		return 0
		;;
	esac
	printf '%s: 親のブランチ %s がリモートにありません。マージリクエストもマージされていないので、改名・消し間違いか捨てた親子のチケットかを決められない。このブランチへの push は止まる（push の関門）\n' "$P" "$B"
	printf '  戻し方 1（改名・消し間違い）: 元の名前 %s でブランチを作り直す。ユーザが端末で、手元の親のワークツリーから git push origin %s を一度打つ。GitHub ならマージリクエストの画面の「Restore branch」、GitLab ならマージリクエストの refs/merge-requests/<番号>/head から %s を作ってもよい。戻したら sh %s/ccnavi-sync.sh %s を再実行します。\n' \
		"$B" "$B" "$B" "$here_sh" "$P"
	printf '  戻し方 2（親子のチケットを捨てた）: 親のワークツリーを片付ける（sh %s/ccnavi-git.sh worktree remove .claude/worktrees/%s）。片付ければこの sh の対象から外れ、push の関門も掛からない。ready の後にマージせず閉じた親の記録は、ユーザが sh %s/ccnavi-launcher.sh worktree retire --abandon %s で消す（エージェントは実行しません）\n' \
		"$here_sh" "$P" "$here_sh" "$P"
	fail_note
	return 0
}

# ---- 親のワークツリーを片付けた後の親子のチケット（ready が書いた片付けの記録だけが残っている）
#
# `ready` が親のワークツリーを片付けた後は、片付けの記録（retire/<P>.json）と手元のブランチだけが残る。
# 記録の MR の番号で MR がマージ済みかを聞き（ホストは、マージ後にブランチを消さないこともある）、
# マージ済みなら手元を片付ける。マージされていなければ何もしない（待っている）。
# 確かめられなければ、推定せずに止める。
#
# ブランチ名は、ワークツリーが無く HEAD も無いので、ホストが答えた MR の元ブランチを使う。
# 記録にブランチがあれば、元ブランチと合うことを確かめる。
# <P> <リポジトリの名前> <リポジトリ> <一覧> <MR の番号> <記録のブランチ>
sync_retired() {
	P="$1"
	key="$2"
	repo="$3"
	heads="$4"
	sr_mr="$5"
	sr_branch="$6"
	B=""
	case "$sr_mr" in
	'' | *[!0-9]*)
		printf '%s: 片付けの記録に MR の番号が無く、閉じたか判断できない。手元は片付けない\n' "$P"
		return 0
		;;
	esac
	lock_rc=0
	ccnavi_lock_take "$root" "$key" "$P" "$lock_wait" || lock_rc=$?
	if [ "$lock_rc" -ne 0 ]; then
		printf '%s: 他の操作がロックを持っている。終わってから再実行してください\n' "$P"
		fail_note
		return 0
	fi
	merged=$(ask_merged "$repo" "$sr_mr")
	merged_rc="${merged%%:*}"
	merged="${merged#*:}"
	case "$merged_rc:$merged" in
	*:'merged '*)
		split_merged "${merged#merged }"
		B="$merged_branch"
		if [ -z "$B" ]; then
			printf '%s: マージリクエスト（%s）はマージ済みだが、元ブランチの名前をホストから取れなかった。手元は片付けない\n' "$P" "$merged_mr"
			fail_note
		elif [ "$sr_branch" != "-" ] && [ -n "$sr_branch" ] && [ "$sr_branch" != "$B" ]; then
			printf '%s: マージリクエスト（%s）の元ブランチ %s が、片付けの記録のブランチ %s と違う。手元は片付けない（記録の MR の番号を確かめてください）\n' \
				"$P" "$merged_mr" "$B" "$sr_branch"
			fail_note
		else
			if has_head "$heads" "$B"; then
				sr_where="リモートに残っている"
			else
				sr_where="リモートから消えた"
			fi
			printf '%s: ワークツリーは片付け済み。親のブランチ %s は%s。マージリクエスト（%s）はマージ済み。手元を片付ける\n' \
				"$P" "$B" "$sr_where" "$merged_mr"
			tree="$repo"
			tidy_merged "$merged_sha"
		fi
		;;
	0:none)
		printf '%s: ワークツリーは片付け済み。マージリクエスト（%s）はまだマージされていないので片付けない。捨てた親なら、ユーザが sh %s/ccnavi-launcher.sh worktree retire --abandon %s で記録を消す\n' \
			"$P" "$sr_mr" "$here_sh" "$P"
		;;
	*)
		printf '%s: ワークツリーは片付け済みだが、マージリクエスト（%s）がマージ済みかを確かめられなかった（ccnavi-review.sh merged が結果を返さなかった。gh・glab か、curl とトークンが必要です）。手元は片付けずに止めた\n' "$P" "$sr_mr"
		fail_note
		;;
	esac
	# ロックは自分が取ったものだけを外す（持ち主を確かめる。入れ子で外側が持つロックは外さない）。
	ccnavi_lock_drop
	return 0
}

# ---- 取り込みの後の検査

# 親のブランチがリモートにある親子のチケットを判定し直す。error があれば言って終了コード 1 にする。
# <P> <リポジトリの名前>
check_family() {
	# 手元の片付けで親のワークツリーを消した親子のチケットは、判定し直す先が無い。
	[ -d "$root/.claude/worktrees/$1" ] || return 0
	if [ -z "$info_from" ]; then
		printf '%s: 実行ファイルが無いので、取り込みの後の検査（判定し直し）はしなかった\n' "$1"
		return 0
	fi
	cf_rc=0
	run_ccnavi sync check "$1" "$2" >"$scratch/check-raw" 2>"$scratch/check-err" || cf_rc=$?
	tr -d '\r' <"$scratch/check-raw" >"$scratch/check"
	if [ "$(head -n 1 "$scratch/check" 2>/dev/null)" != "check 1" ]; then
		# 検査を実行できなかった（古い実行ファイルがサブコマンドを知らない、失敗した）。検査の error ではない
		# ので親子のチケットは止めない。この検査が入る前と同じ動き。
		printf '%s: 注意: 取り込みの後の検査を実行できなかった（%s）。実行ファイルを新しくして再実行してください\n' \
			"$1" "$(head -n 1 "$scratch/check-err" 2>/dev/null)"
		log_warn 取り込みの後の検査を実行できなかった -- "family=$1" "rc=$cf_rc"
		return 0
	fi
	sed -n 's/^warn /  注意: /p' "$scratch/check"
	cf_errors=$(sed -n 's/^error /  - /p' "$scratch/check")
	if [ -n "$cf_errors" ] || [ "$cf_rc" -ne 0 ]; then
		printf '%s: 取り込みの後の検査で止める理由がある。直してから、sh %s/ccnavi-sync.sh %s を再実行してください（検査し直す）\n' "$1" "$here_sh" "$1"
		if [ -n "$cf_errors" ]; then
			printf '%s\n' "$cf_errors"
		else
			printf '  - 検査が失敗した（%s）\n' "$(head -n 1 "$scratch/check-err" 2>/dev/null)"
		fi
		fail_note
	fi
	return 0
}

# ---- リポジトリごと

while IFS= read -r key <&4; do
	[ -n "$key" ] || continue
	repo=$(repo_dir_of "$key")
	has_family=no
	repo_has_families "$key" && has_family=yes
	# 親子のチケットの無いリポジトリ（引数を省いた回のプロジェクトなど）で失敗しても、名指しして続け、1 にしない。
	repo_fail() {
		[ "$has_family" = yes ] && fail_note
		return 0
	}
	if ! git -C "$repo" remote get-url origin >/dev/null 2>&1; then
		# origin の無いリポジトリは取り込みの対象外（今の手元の動きのまま）。
		if [ "$has_family" = yes ]; then
			printf '%s: origin がありません。取り込みの対象外（今の手元の動きのまま）\n' "$key"
		fi
		continue
	fi
	label=""
	[ "$key" = self ] || label="$key: "
	heads="$scratch/heads-$key"
	if ! ccnavi_git_timed "$timeout" "$scratch/err" "$repo" ls-remote --heads origin >"$heads"; then
		printf '%sリモートのブランチの一覧を取ってこられなかった（オフラインか、認証の失敗。%s）。止める\n' "$label" \
			"$(head -n 1 "$scratch/err")"
		repo_fail
		continue
	fi
	if ! integ=$(default_branch "$repo" "$heads"); then
		printf '%s %s 取り込みを止めた\n' "$label" "$(ccnavi_integration_hint "$repo")"
		repo_fail
		continue
	fi
	printf '%s 統合先: %s\n' "$label" "$integ"
	if ! fetch_one "$repo" "$integ"; then
		printf '%s 統合先 %s を取ってこられなかった（%s）。止める\n' "$label" "$integ" "$(head -n 1 "$scratch/err")"
		repo_fail
		continue
	fi
	# ホストに聞いた名前と手元の origin/HEAD が違えば、fetch が成功した今、向け直す。
	if [ -e "$scratch/db-host" ]; then
		old_head=$(git -C "$repo" symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null || :)
		old_head="${old_head#origin/}"
		if [ "$old_head" != "$integ" ] &&
			git -C "$repo" symbolic-ref refs/remotes/origin/HEAD "refs/remotes/origin/$integ" 2>/dev/null; then
			printf '%s 統合先を %s から %s に合わせた（ホストの既定ブランチ）\n' "$label" "${old_head:-未設定}" "$integ"
		fi
	fi
	awk -F "$tab" -v k="$key" '$3 == k' "$scratch/families" >"$scratch/these"
	# 読む先は fd 3。中で起こす git が標準入力を読んでも、親子のチケットの一覧を読み取ってしまわない。
	while IFS="$tab" read -r fam_p fam_tree fam_key fam_branch <&3; do
		[ -n "$fam_p" ] || continue
		sync_family "$fam_p" "$fam_tree" "$fam_key" "$repo" "$integ" "$heads" "$fam_branch"
	done 3<"$scratch/these"
	awk -F "$tab" -v k="$key" '$2 == k' "$scratch/retired" >"$scratch/these-retired"
	while IFS="$tab" read -r ret_p ret_key ret_mr ret_branch <&3; do
		[ -n "$ret_p" ] || continue
		sync_retired "$ret_p" "$ret_key" "$repo" "$heads" "$ret_mr" "$ret_branch"
	done 3<"$scratch/these-retired"
	while IFS="$tab" read -r fam_p fam_tree fam_key fam_branch <&3; do
		[ -n "$fam_p" ] || continue
		has_head "$heads" "$fam_branch" || continue
		check_family "$fam_p" "$fam_key"
	done 3<"$scratch/these"
done 4<"$scratch/repos"

if [ -s "$scratch/failed" ]; then
	log_info 終わった -- "exit=1"
	exit 1
fi
log_info 終わった -- "exit=0"
exit 0
