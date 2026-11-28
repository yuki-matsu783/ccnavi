#!/bin/sh
# ccnavi-sync 親のブランチをリモートから取り込み、リモートから消えた親のブランチと、片付けの残りを見る。
#
#   sh .ccnavi/scripts/ccnavi-sync.sh [<P>...]
#
# マージ後の片付けは、ユーザがボードから起こすか、エージェントが打つ（「承認した」と言われたエージェント）。セッションの頭の
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
# 終了コード 1 にする。親のブランチへの push は、共通の push のゲート（ccnavi_push_gate）が止める。
# 破棄した親子のチケットは、親のワークツリーを片付ければ、この sh の対象から外れ、ゲートも掛からない。
#
# リポジトリ（ワークスペースと、親子のチケットの元のプロジェクト）ごとに 1 回、次の順に行う。
#
#   1. `ls-remote --heads origin` で全ブランチの有無を得る。失敗したら（オフライン・認証）止める
#   2. 統合先の名前を決める。ホストのデフォルトブランチ（`ls-remote --symref origin HEAD`）、
#      読めなければ手元の origin/HEAD、それも無ければ一覧にある main・master。決まらなければ止める
#   3. 統合先を fetch する（取り込みの後の検査が、設定のずれを origin/<統合先> と比べるため）。
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
#     push のゲートと同じ述語）が無ければ、一度も送っていない親子のチケットで今のまま。
#     送った形跡があれば、ccnavi-review.sh merged <番号> に聞く。クローズしたかの根拠は MR がマージ済みかだけ。答えが得られなければ止める。
#     マージされていないと分かったときは、診断（戻し方）を言って止める
#   - マージ済みと分かったら、手元を片付ける（`sync tidy` と同じ片付け。条件の確認も削除も実行ファイルが行う）。
#     親のワークツリー（未コミットの変更があれば残す。git が無視しているファイルは scratchpad/worktrees/<名前>/ へ退避する）と、親の手元のブランチ
#     （手元の先頭が MR の先頭に含まれ、未 push のコミットが無いときだけ）を消し、残ったものは理由と手で消す
#     コマンドを 1 行ずつ言う。何も残らなければ ready の後の片付けの記録（retire/<P>.json）も実行ファイルが消す。
#     MR の先頭の sha はホストが答えた（ccnavi-review.sh merged <番号> の <sha>）。MR の番号は親チケットの
#     mr 欄から引く。番号が無ければ、クローズしたか判断できないと 1 行言う
#   - 親のワークツリーを片付けた後（ready の後）の親子のチケットは、片付けの記録
#     （retire/<P>.json）から引く。同じく MR がマージ済みかを聞き、マージ済みなら同じ片付けをする。
#     ブランチ名はホストが答えた MR の元ブランチを使う。マージされていなければ片付けない。
#     確かめられなければ推定せずに止める。<P> を渡したときも同じ
#
# 統合先を取ってきた後、親のブランチがリモートにある親子のチケットごとに判定し直す（どのチケットを正とするかの
# 検査と、承認済みチケットの判定し直し）。error があれば言って終了コード 1 にする（状態は書かない）。warn は表示だけ。
# 直してから同じ P でこの sh を打ち直すと、検査し直す。
#
# 分担: 判定（親子のチケットの選び方・統合先の決め方・リモートにあるか・クローズしたか）、ローカルの git の書き込み
# （早送り・merge と取りやめ・origin/HEAD の向け直し・片付け）、出力の文は実行ファイル（`ccnavi sync <段取り>`。
# go/internal/entry/catchup）が持つ。この sh に残すのは外との出入り（ls-remote・fetch・ccnavi-review.sh merged）と、
# ロック・trap（プロセスの寿命）と、それらを並べる段取りだけ。実行ファイルが答えの out の行を出せと言い、failed で
# 止めたものに数えさせ（終了コードは、答えを並べたファイルから実行ファイルの sync end が決める）、next の語
# （fetch・ask・skip・go）で次の外との出入りを決める。merge の取りやめは実行ファイルの
# 1 回の起動の中で済み（信号を受けたときも）、trap は子を待ってロックを外すだけ。実行ファイルが無い・答えないときは、
# 組むか置くかを言って止める。
#
# 環境変数: CCNAVI_LOCK_WAIT（ロックを待つ秒、既定 120）/
#   CCNAVI_SYNC_TIMEOUT（ls-remote・fetch 1 回のタイムアウト監視の秒、既定 60）
# 終了コード: 0 全部取り込んだ（取り込むものが無いを含む） / 1 止めた親子のチケットかリポジトリがある /
#           2 引数か環境の誤り（実行ファイルが無い・答えないを含む） /
#           3 止めたもののうち、取り込み（早送りか merge）が書きかけと重なって止まったものがある（ほかの理由で
#             止めたものがあっても 3。write-and-push はこれで「書きかけを先にコミットしてから取り込み直す」と言い分ける） /
#           4 止めたのは、渡した <P> のワークツリーに親チケットも提案も無いものだけ（ワークツリーを切った直後。
#             ccnavi-start.sh はこれで「取り込むものはまだ無い」と言い分ける）

set -eu

. "$(dirname "$0")/ccnavi-common.sh"

usage() {
	cat <<'USAGE'
sh .ccnavi/scripts/ccnavi-sync.sh [<P>...]

  親チケット <P>（識別子。省けば .claude/worktrees/ の下の親のワークツリー全部）の親のブランチ
  （親のワークツリーの HEAD のブランチ。/ を - に置き換えたものが <P>）をリモートから取り込む。
  分かれていれば merge し、衝突したら取りやめてユーザに回す。
  取り込んだ後、親子のチケットを判定し直し、止める理由があれば言って終了コード 1 にする。
  リモートから消えた親のブランチは、MR がマージ済みかを見て「クローズした」か「消えた」かを決める。
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
	printf 'ccnavi-sync: --forget は廃止しました。破棄した親子のチケットは、親のワークツリーを片付ければこの sh の対象から外れます（push のゲートも掛からない）。ready の後に放棄した親の記録は、ユーザが ccnavi worktree retire --abandon <P> で消します。\n' >&2
	exit 2
	;;
-*)
	printf 'ccnavi-sync: %s は受けません。\n' "$1" >&2
	usage >&2
	exit 2
	;;
esac

root=$(ccnavi_workspace) || ccnavi_no_workspace ccnavi-sync
ccnavi_log_root="$root"
here_sh="$(dirname "$0")"
# 打った場所。手元の片付けが、自分の居るワークツリーを消さないために渡す。
sync_cwd="$(pwd -W 2>/dev/null || pwd)"

lock_wait=$(ccnavi_number_or "${CCNAVI_LOCK_WAIT:-}" 120)
timeout=$(ccnavi_number_or "${CCNAVI_SYNC_TIMEOUT:-}" 60)

scratch=$(mktemp -d 2>/dev/null || mktemp -d -t ccnavi-sync) || {
	printf 'ccnavi-sync: 一時ディレクトリが作れません。\n' >&2
	exit 2
}
: >"$scratch/answers"

# 後始末（プロセスの寿命）。ロックを外し、一時ディレクトリを消す。merge の途中で切られたときの取りやめは、
# merge を始めた実行ファイルが持つ（sh は子が終わるのを待ってからここに来る）。
# dash は EXIT の trap を INT・TERM・HUP で走らせないので、そちらにも置く。2 度走っても害は無い。
cleaned=""
cleanup() {
	[ -z "$cleaned" ] || return 0
	cleaned=yes
	ccnavi_lock_drop
	rm -rf "$scratch"
}
trap 'cleanup' EXIT
trap 'cleanup; exit 130' INT TERM HUP

log_info 受け付けた -- "args=$#"

bin=$(ccnavi_bin "$root") || ccnavi_no_bin ccnavi-sync

# ---- 実行ファイルに聞く（段取り）
#
# ask_sync <段取り> <引数>...: 答えを $answer に入れる。頭の行（sync 1）が無ければ、置き場や判定を取り違えないよう止める。
# 信号で切られた（130）なら、そのまま抜ける（trap が後始末する）。
ask_sync() {
	as_rc=0
	"$bin" --root "$root" --cwd "$sync_cwd" sync "$@" </dev/null >"$scratch/answer-raw" 2>"$scratch/answer-err" || as_rc=$?
	[ "$as_rc" -ne 130 ] || exit 130
	# Windows の実行ファイルの CRLF の CR を除く（1 行 1 項目の値の末尾に CR を残さない）。
	answer=$(tr -d '\r' <"$scratch/answer-raw")
	if [ "$(printf '%s\n' "$answer" | head -n 1)" != "sync 1" ]; then
		printf 'ccnavi-sync: 実行ファイル（%s）が sync %s の答えを返さなかった（%s）。止めた。実行ファイルを組む（sh scripts/ccnavi-build.sh）か置いてから打ち直してください。\n' \
			"$bin" "$1" "$(head -n 1 "$scratch/answer-err" 2>/dev/null)" >&2
		exit 2
	fi
}

# 答えの out・err の行を出し、答えを終了コードの決め手（sync end）に渡すファイルへ足す。次の一手（next の語）を
# $next に入れる。
said() {
	ccnavi_val out "$answer"
	ccnavi_val err "$answer" >&2
	printf '%s\n' "$answer" >>"$scratch/answers"
	next=$(ccnavi_val1 next "$answer")
}

# ---- 外との出入り

# fetch 1 本。単一ブランチの clone でも origin/<ブランチ> が進むよう、行き先を書いて取る
# （sh の中の git。ccnavi-git.sh の入口の refspec の拒否とは別の話）。
# 終了コードを $fetched に、失敗した理由（git の標準エラー）を $scratch/err に残す。<リポジトリ> <ブランチ>
fetch_one() {
	fetched=0
	ccnavi_git_timed "$timeout" "$scratch/err" "$1" \
		fetch --quiet --no-tags origin "+refs/heads/$2:refs/remotes/origin/$2" >/dev/null || fetched=$?
}

# 親の MR がマージ済みかをホストに聞く（ccnavi-review.sh merged <番号>）。答えを $scratch/merged に、終了コードを
# $asked に入れる。<リポジトリかツリーのディレクトリ> <MR の番号>
ask_merged() {
	asked=0
	(cd "$1" && sh "$here_sh/ccnavi-review.sh" merged "$2" 2>/dev/null </dev/null) >"$scratch/merged" || asked=$?
}

# クローズしたかを聞く次の一手（next ask <MR>）なら聞いて、片付けまでを実行ファイルに任せる。
# <場面（present・absent・retired）> <P> <リポジトリの名前> <ブランチ> <聞くディレクトリ> <一覧> [<記録の MR>]
ask_and_tidy() {
	case "$next" in
	'ask '*) ;;
	*) return 0 ;;
	esac
	ask_merged "$5" "${next#ask }"
	ask_sync merged "$here_sh" "$1" "$2" "$3" "$4" "${7:-${next#ask }}" "$asked" "$scratch/merged" "$6"
	said
}

# ロックを待って取る（プロセスの寿命）。取れなければ理由を言って 1。<リポジトリの名前> <P>
take_lock() {
	lock_rc=0
	ccnavi_lock_take "$root" "$1" "$2" "$lock_wait" || lock_rc=$?
	[ "$lock_rc" -ne 0 ] || return 0
	ccnavi_lock_why "$lock_rc" "$2"
	printf 'failed\n' >>"$scratch/answers"
	return 1
}

# ---- 取り込む親子のチケットとリポジトリを選ぶ

ask_sync plan "$here_sh" ${1+"$@"}
said
if exit_code=$(ccnavi_val1 exit "$answer") && [ -n "$exit_code" ]; then
	exit "$exit_code"
fi
# repo と skip の行は語ごと並びのまま残す（下の while が先頭の語で分ける）
printf '%s\n' "$answer" | grep -E '^(repo|skip) ' >"$scratch/repos" || :
ccnavi_val family "$answer" >"$scratch/families"
ccnavi_val retired "$answer" >"$scratch/retired"

# ---- リポジトリごと
#
# 段取り: ls-remote → 統合先を決める → 統合先を fetch → 親子のチケットごとに（ロック → fetch → 取り込み → クローズしたかを
# 聞く → 片付け）→ 片付けた後の親子のチケット → 取り込みの後の検査。読む先は fd 3・4。中で起こす git が標準入力を
# 読んでも、一覧を読み取ってしまわない。

while IFS= read -r line <&4; do
	case "$line" in
	'skip '*)
		printf '%s\n' "${line#skip }"
		continue
		;;
	esac
	# repo <リポジトリの名前> <yes|no（親子のチケットがあるか）> <ディレクトリ>。ディレクトリは空白を含みうるので最後に置く。
	line="${line#repo }"
	key="${line%% *}"
	line="${line#* }"
	has="${line%% *}"
	dir="${line#* }"
	heads="$scratch/heads-$key"
	: >"$scratch/symref"
	ls_rc=0
	ccnavi_git_timed "$timeout" "$scratch/err" "$dir" ls-remote --heads origin >"$heads" || ls_rc=$?
	if [ "$ls_rc" -eq 0 ]; then
		ccnavi_git_timed "$timeout" "$scratch/err-symref" "$dir" ls-remote --symref origin HEAD >"$scratch/symref" ||
			: >"$scratch/symref"
	fi
	ask_sync integ "$here_sh" "$key" "$has" "$ls_rc" "$heads" "$scratch/err" "$scratch/symref"
	said
	case "$next" in
	'fetch '*) ;;
	*) continue ;;
	esac
	integ="${next#fetch }"
	host="${integ#* }"
	integ="${integ% *}"
	fetch_one "$dir" "$integ"
	ask_sync fetched "$here_sh" "$key" "$has" "$integ" "$host" "$fetched" "$scratch/err"
	said
	[ "$next" = go ] || continue

	# family <P> <リポジトリの名前> <ブランチ> <親のワークツリー>。ワークツリーは空白を含みうるので最後（read の残り）。
	while read -r fam_p fam_key fam_branch fam_tree <&3; do
		[ "$fam_key" = "$key" ] || continue
		take_lock "$key" "$fam_p" || continue
		ask_sync family "$here_sh" "$fam_p" "$key" "$fam_branch" "$heads"
		said
		case "$next" in
		'fetch '*)
			fetch_one "$fam_tree" "$fam_branch"
			ask_sync pull "$here_sh" "$fam_p" "$key" "$fam_branch" "$fetched" "$scratch/err"
			said
			ask_and_tidy present "$fam_p" "$key" "$fam_branch" "$fam_tree" "$heads"
			;;
		*) ask_and_tidy absent "$fam_p" "$key" "$fam_branch" "$fam_tree" "$heads" ;;
		esac
		ccnavi_lock_drop
	done 3<"$scratch/families"

	while read -r ret_p ret_key ret_mr ret_branch <&3; do
		[ "$ret_key" = "$key" ] || continue
		# ロックは自分が取ったものだけを外す（持ち主を確かめる。入れ子で外側が持つロックは外さない）。
		if take_lock "$key" "$ret_p"; then
			next="ask $ret_mr"
			ask_and_tidy retired "$ret_p" "$key" "$ret_branch" "$dir" "$heads" "$ret_mr"
			ccnavi_lock_drop
		fi
	done 3<"$scratch/retired"

	while read -r fam_p fam_key fam_branch _ <&3; do
		[ "$fam_key" = "$key" ] || continue
		ask_sync recheck "$here_sh" "$fam_p" "$key" "$fam_branch" "$heads"
		said
	done 3<"$scratch/families"
done 4<"$scratch/repos"

ask_sync end "$here_sh" "$scratch/answers"
exit_code=$(ccnavi_val1 exit "$answer")
log_info 終わった -- "exit=$exit_code"
exit "$exit_code"
