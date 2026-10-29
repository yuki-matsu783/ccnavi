#!/bin/sh
# ccnavi-worktree直接作業のワークツリーを、取り込み先のブランチから切る。
#
# 直接作業（チケットを使わない作業）用で、名前と同じブランチを新しく切る。親子のチケットの親のワークツリーには
# 使わない（親はccnavi-start.sh --mr <番号> / --issue <番号> で、親のブランチを直接チェックアウトして作る）。
#
#   sh .ccnavi/scripts/ccnavi-worktree.sh <名前> [--base <ブランチ>] [--accept <確認コード>]
#                                        [--copy-wip | --no-wip] [--side-by-side] [--dry-run]
#   sh .ccnavi/scripts/ccnavi-worktree.sh <名前> --adopt --base <ブランチ> [--accept <確認コード>] [--side-by-side]
#
# 取り込み先は、切った時点でユーザのチェックアウト（`git worktree list` の先頭）が開いているブランチ。
# 起点Sは、書きかけをコピーするとき（--copy-wip）は確認した時点の書きかけを含むスナップショット、それ以外は
# 取り込み先のHEAD。ルートのindexとワークツリーには書かない（スナップショットは一時的なindexで作る）。
# 手元の取り込み先は進めない（進めるのはセッション開始時のccnavi-fetch.shだけ）。originからも切らない。
#
# 次のどれかにヒットするとき、ワークツリーを切らずに終了コード 3 で止まり、状態・勧め・リスク・選択肢・確認コードを
# 出す。報告をユーザに伝え、了承を得てから --base / --acceptなどを付けて打ち直す。
#   C4 cwdのブランチと取り込み先が違う（--base <取り込み先> が要る）
#   C5 取り込み先が統合先（origin/HEAD）でない
#   C6 origin/<取り込み先> より遅れている・進んでいる・分岐している・originに無い・リモート未確認
#   C7 ユーザのチェックアウトに書きかけ（追跡済みの変更・新規ファイル）がある（--copy-wipか --no-wipが要る）
#   C5〜C7 のどれかにヒットするときは --accept <確認コード> も要る。確認コードは今の状態から決まるので、
#   状態が動けば報告からやり直しになる。
# ヒットしないときは確認なしでS = 取り込み先のHEADから切る。--side-by-sideだけはaskルールがユーザの確認を取る。
#
# 先に断るもの: ユーザのチェックアウトがdetached HEAD（C1）、取り込み先がチケットに結び付く・結び付くかを
# 確かめられない（C2。実行ファイルの `tree base-bound`）、gitが 2.40 未満（C3）、
# cwdが親のワークツリーで、親が承認済み・レビュー待ち・着手済みの子がある（終了コード 1。実行ファイルの `tree leftover` の `stage`）。
#
# 手で打つgitではなくccnavi-git.shを通す。fetchはorigin <取り込み先> だけ。
# ワークツリーが既にあれば、何も切らずに、パスと取り込みの記録の有無を出します（冪等）。
#
# origin か手元に同じ識別子になるブランチ（同じ名前、`/` を `-` に置き換えると同じになる名前、英字の大文字小文字だけ
# 違う名前）があるときは、切らずに終了コード 1 で止まり、ccnavi-start.sh へ案内する。既にあるブランチ（MR の元ブランチ
# など）は、MR を探して採用する確認をしてから出す必要があり、ここで新しく切ると、同じ識別子のブランチが 2 本になる
# （ワークツリーの置き場が重なる）。
#
# 名前の字の種類・NFC・長さ（64 文字まで）は実行ファイルに聞く（`worktree id`）。sh はマルチバイトの字を見分けない。
# 答えが無ければ「確かめられない」と止まる。
#
# 記録はgitの共通ディレクトリのccnavi-direct/<名前>（version 4）に書く。Sはrefs/ccnavi/direct/<名前>/sに置く。
# 書きかけをコピーする候補は確認が要るときにrefs/ccnavi/direct/<名前>/pendingに置き、切ったら消す。
#
# --dry-runは何も作らず、調べた結果と予定だけを出す（fetchもしない）。
# --offline / --from-originは廃止（終了コード 2）。--acceptで代わりができる。--adoptは記録の無い既存のワークツリーを引き取る（2 回の呼び出しで確認コードを使う）。
#
# 終了コード: 0 完了 / 1 前提の未充足かworktree addの拒否 / 2 引数か環境の誤り / 3 確認が要る

set -eu

# ユーザのチェックアウトの状態を読むだけの git が、index を書き直さない（確認している間、ルートの index に
# 書かない）。ユーザの git add / commit と index.lock を取り合わない。
GIT_OPTIONAL_LOCKS=0
export GIT_OPTIONAL_LOCKS

EXIT_CONFIRM=3

# fail <識別子> <文面> [終了コード]。文面は標準エラーへ出す契約。識別子は診断ログにだけ残す。
fail() {
	printf 'ccnavi-worktree: %s\n' "$2" >&2
	log_info 止めた -- "exit=${3:-1}" "reason=$1"
	exit "${3:-1}"
}

# 共通部分。ワークスペースルートの探し方と、診断ログはここにある。
. "$(dirname "$0")/ccnavi-common.sh"

usage() {
	cat <<'USAGE'
sh .ccnavi/scripts/ccnavi-worktree.sh <名前> [--base <ブランチ>] [--accept <確認コード>] [--copy-wip | --no-wip] [--side-by-side] [--dry-run]

  <名前>          ワークツリーの名前。ブランチ名も同じ名前で切る（/ を含められない）。行き先は
                  <ワークスペースルート>/.claude/worktrees/<名前>
  --base <ブランチ>
                  確認した取り込み先（ユーザのチェックアウトが開いているブランチ）
  --accept <確認コード>
                  報告したずれ（取り込み先・origin との関係・書きかけ）を、ユーザが了承して進める
  --copy-wip / --no-wip
                  書きかけをコピーして切るか、コピーせず HEAD から切るか（書きかけがあるときはどちらかが要る）
  --side-by-side  このワークツリーが残っている間、ユーザのチェックアウトで見つかった変更を自動で戻さず報告にする
                  （ccnavi の設定ファイルは戻す）
  --adopt         記録の無い既存の直接作業のワークツリー（.claude/worktrees/<名前>）を引き取る。--base <取り込み先> が要る。
                  1 回目は推定した起点・取り込む範囲・差分を見せて確認コードを出し（終了コード 3）、
                  ユーザの了承を得て --accept <確認コード> を付けて打ち直すと記録を書く
  --dry-run       何も作らず、調べた結果と予定だけ出す

直接作業（チケットを使わない作業）用。親子のチケットの親のワークツリーには使わない。
親のワークツリーの中から打てるのは、親の提案が承認前（todo/ にあるだけ）のときだけ。
承認済み・レビュー待ち・着手済みの子があるときは、切る前に終了コード 1 で断る
（親は ccnavi-start.sh --mr <番号> / --issue <番号> で、親のブランチを直接チェックアウトして作る）。
ずれが無ければ確認なしで切る。ずれがあれば切らずに状態・勧め・リスク・確認コードを出して終了コード 3 で止まる。
ワークツリーが既にあれば、パスと取り込みの記録の有無を出すだけです（冪等）。
終了コード: 0 完了 / 1 前提の未充足か worktree add の拒否 / 2 引数か環境の誤り / 3 確認が要る
USAGE
}

name=""
base_arg=""
accept=""
wip_mode=""
side=""
dry=""
adopt=""
while [ "$#" -gt 0 ]; do
	case "$1" in
	--offline | --from-origin)
		fail retired-arg "$1 は廃止しました。fetch が届かない・手元が origin とずれているときは、引数なしで打つと状態と確認コードを報告するので、ユーザの了承を得てから --accept <確認コード> を付けて打ち直してください。" 2
		;;
	--adopt)
		adopt=1
		shift
		;;
	--base)
		[ "$#" -ge 2 ] || fail base-missing "--base にブランチ名がありません。" 2
		base_arg="$2"
		shift 2
		;;
	--base=*)
		base_arg="${1#--base=}"
		shift
		;;
	--accept)
		[ "$#" -ge 2 ] || fail accept-missing "--accept に確認コードがありません。" 2
		accept="$2"
		shift 2
		;;
	--accept=*)
		accept="${1#--accept=}"
		shift
		;;
	--copy-wip)
		[ "$wip_mode" != no ] || fail wip-both "--copy-wip と --no-wip は同時に指定できません。" 2
		wip_mode=copy
		shift
		;;
	--no-wip)
		[ "$wip_mode" != copy ] || fail wip-both "--copy-wip と --no-wip は同時に指定できません。" 2
		wip_mode=no
		shift
		;;
	--side-by-side)
		side=1
		shift
		;;
	--dry-run)
		dry=1
		shift
		;;
	-h | --help | help)
		usage
		exit 0
		;;
	-*)
		usage >&2
		fail unknown-arg "未対応の引数です（$1）。" 2
		;;
	*)
		[ -z "$name" ] || fail two-names "ワークツリーは 1 回に 1 本だけ指定します（${name} と ${1}）。" 2
		name="$1"
		shift
		;;
	esac
done
if [ -z "$name" ]; then
	usage >&2
	fail no-name "ワークツリーの名前を渡してください。" 2
fi
case "$name" in
*/*) fail bad-name "名前（${name}）に / は書けません。ワークツリーは .claude/worktrees/ の下に 1 段で置きます。" 2 ;;
esac
ccnavi_is_branch "$name" || fail bad-name "名前（${name}）はブランチ名に使えない書き方です。" 2

root=$(ccnavi_workspace) ||
	fail no-workspace "ワークスペースルートが見つかりません（.ccnavi/scripts/ccnavi-common.sh を持つ親を cwd から上へ探しました）。ワークスペースの中で実行するか、CCNAVI_WORKSPACE にワークスペースルートの絶対パスを渡してください。" 2
ccnavi_log_root="$root"
# 名前の字の種類・NFC・長さは実行ファイルに聞く（sh の検査は ASCII の外のバイトを通す）
ccnavi_id_ask "$root" "$name" ||
	fail id-unchecked "名前（${name}）を指定できるか（字の種類・64 文字以内）を確かめられない（ccnavi の実行ファイルが答えない）。build.py でビルドするか、scripts/ccnavi-setup.sh で実行ファイルを配置してから再実行してください。"
[ -z "$ccnavi_id_direct" ] || fail bad-name "名前（${name}）は指定できません（${ccnavi_id_direct}）。" 2
top=$(git rev-parse --show-toplevel 2>/dev/null || :)
[ -n "$top" ] ||
	fail not-git "cwd が git のリポジトリの中ではありません。ワークスペースか projects/<名前>/ の中で実行してください。"
scripts="$root/.ccnavi/scripts"
dest="$root/.claude/worktrees/$name"
integ=$(ccnavi_integration "$(ccnavi_phys "$top")") || integ=""
[ -n "$integ" ] ||
	fail no-integration "$(ccnavi_integration_hint "$top")"
log_info 始めた -- "name=$name" "integration=$integ" "dry=${dry:-no}"

# ccnavi-git.sh 経由の git。成功は何も出さず、失敗は出力を標準エラーへ出して 1 を返す。
cgit() {
	cgit_out=$(sh "$scripts/ccnavi-git.sh" "$@" 2>&1) || {
		printf '%s\n' "$cgit_out" >&2
		return 1
	}
	return 0
}

# 非対話（CLAUDE_CODE_SESSION_ATTENDED が 0。実行ファイルの ccnavimd.unattended と同じ見方）のときだけ、
# 確認に答えられないことと次の行動を足します。対話のときは何も出しません。
unattended_line() {
	[ "${CLAUDE_CODE_SESSION_ATTENDED:-}" = 0 ] || return 0
	printf '%s' "このセッションでは確認に答えられません。ユーザに引き取りの了承を頼み、了承されたら打ち直してもらってください。"
}

# 最後の行（名前の警告よりも後）に出す次の一手。コマンドは 1 行で完結する形にする。
print_next() {
	ccnavi_text "$root" NEXT_WORKTREE_EDIT "dest=$dest"
}

# 同じ識別子になる別のブランチ（feature/7-x に対する feature-7-x、英字の大文字小文字だけ違う名前）があれば止める。
# <一覧のファイル> <どこ（手元・origin）>
# dest-exists・既にあるワークツリーの分岐・branch-exists の検査より前に置く。大文字小文字を区別しない機械では、綴り違いの名前のパスが既にあるように見えるため。
heads_list="$root/logs/state/worktree-heads-$$"
cand_index="$root/logs/state/worktree-index-$$"
mkdir -p "$root/logs/state" 2>/dev/null || :
trap 'rm -f "$heads_list" "$heads_list.names" "$cand_index"' EXIT
same_id_guard() {
	sg_other=$(ccnavi_id_clash "$name" "$1") || return 0
	log_info 同じ識別子のブランチ -- "name=$name" "where=$2"
	printf 'ワークツリーは切っていない。%s にブランチ %s があり、%s と同じ識別子（%s。英字の大文字小文字は区別しない）になる。\n' "$2" "$sg_other" "$name" "$name"
	printf '同じ識別子のブランチが 2 本あると、ワークツリーの置き場が重なる。既にあるブランチは ccnavi-start.sh で始める:\n'
	printf '  sh %s --mr <MR の番号>\n  sh %s --issue <issue の番号>\n' "$(ccnavi_sh_quote "$scripts/ccnavi-start.sh")" "$(ccnavi_sh_quote "$scripts/ccnavi-start.sh")"
	printf '別の作業として新しく切るなら、別の名前で再実行する: sh %s <別の名前>\n' "$(ccnavi_sh_quote "$scripts/ccnavi-worktree.sh")"
	exit 1
}
{
	git for-each-ref --format='%(refname:short)' refs/heads 2>/dev/null || :
	git for-each-ref --format='%(refname:lstrip=3)' refs/remotes/origin 2>/dev/null | grep -v -x 'HEAD' || :
} >"$heads_list"
[ -n "$adopt" ] || same_id_guard "$heads_list" 手元

if [ -z "$adopt" ] && { [ -e "$dest" ] || [ -L "$dest" ]; }; then
	# 在るのは、このリポジトリのワークツリーで、同じ名前のブランチを開いているときだけ成功にする。
	have=$(git worktree list --porcelain 2>/dev/null | awk -v want="$(ccnavi_phys "$dest")" '
		/^worktree / { p = substr($0, 10) }
		/^branch / { if (p == want) print substr($0, 8) }')
	[ "$have" = "refs/heads/$name" ] ||
		fail dest-exists "${dest} は既にありますが、ブランチ ${name} を開いているこのリポジトリのワークツリーではありません（${have:-ワークツリーの登録が無い}）。中身を確かめ、不要ならユーザに確かめてから片付け、別の名前で再実行してください。"
	printf 'ワークツリーは既にある: %s\n' "$dest"
	# 取り込みの記録（--adopt が読む場所）があるかを確かめます。無いと integrate に進めません。
	ex_common=$(git -C "$top" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || :)
	if [ -n "$ex_common" ] && [ -e "$(ccnavi_phys "$ex_common")/ccnavi-direct/$name" ]; then
		printf '案内: 取り込みの記録は、ある（ccnavi-direct/%s）。integrate に進めます\n' "$name"
		ex_record=yes
	else
		ex_name_q=$(ccnavi_sh_quote "$name")
		ex_unattended=$(unattended_line)
		ccnavi_text "$root" NOTE_WORKTREE_RECORD_MISSING "name=$name" "name_q=$ex_name_q" "unattended=$ex_unattended"
		ex_record=no
	fi
	log_info 既にある -- "name=$name" "record=$ex_record"
	print_next
	exit 0
fi
if [ -z "$adopt" ] && git show-ref --verify --quiet "refs/heads/$name"; then
	fail branch-exists "ブランチ ${name} が既にあります（ワークツリーはありません）。既にあるブランチを出すには、2 段の手順が要ります。1 段目: sh $(ccnavi_sh_quote "$scripts/ccnavi-git.sh") worktree add $(ccnavi_sh_quote ".claude/worktrees/${name}") $(ccnavi_sh_quote "$name")。2 段目: sh $(ccnavi_sh_quote "$scripts/ccnavi-worktree.sh") $(ccnavi_sh_quote "$name") --adopt --base <統合先> で引き取ります（引き取らないと integrate できません。引き取りにはユーザの確認が要ります）。新しく切るなら別の名前にしてください。$(unattended_line)"
fi

# ---- C1〜C3。先に断るもの。
# 取り込み先は、ユーザのチェックアウト（git worktree listの先頭）が開いているブランチ。
main_tree=$(git -C "$top" worktree list --porcelain 2>/dev/null | sed -n '1s/^worktree //p' | tr -d '\r')
[ -n "$main_tree" ] ||
	fail no-main-tree "このリポジトリのユーザのチェックアウトを git worktree list から読めません。"
main_tree=$(ccnavi_phys "$main_tree")
target=$(git -C "$main_tree" symbolic-ref --quiet --short HEAD 2>/dev/null || :)
[ -n "$target" ] ||
	fail detached "ユーザのチェックアウト（${main_tree}）がブランチを開いていません（detached HEAD）。取り込み先を決められないので切りません。ユーザにブランチへ戻してもらってから再実行してください。"
head_sha=$(git -C "$main_tree" rev-parse HEAD 2>/dev/null || :)
[ -n "$head_sha" ] ||
	fail no-head "ユーザのチェックアウト（${main_tree}）の HEAD を読めません。"

git_ver=$(git version 2>/dev/null | sed -n 's/^git version \([0-9][0-9]*\)\.\([0-9][0-9]*\).*/\1 \2/p')
git_major=${git_ver% *}
git_minor=${git_ver#* }
if [ -z "$git_ver" ] || { [ "$git_major" -lt 2 ] || { [ "$git_major" -eq 2 ] && [ "$git_minor" -lt 40 ]; }; }; then
	fail git-old "git が 2.40 未満（$(git version 2>/dev/null)）です。取り込みに使う merge-tree --merge-base が無いので切りません。git を 2.40 以上に更新してから再実行してください。"
fi

bound_out=$(ccnavi_ask "$root" tree base-bound "$main_tree" "$target") || bound_out=""
if [ "$(printf '%s\n' "$bound_out" | head -n 1)" != "tree 1" ]; then
	fail base-bound-unknown "取り込み先 ${target} がチケットに結び付くかを、実行ファイルに確かめられません。build.py でビルドするか、scripts/ccnavi-setup.sh で実行ファイルを配置してから再実行してください。"
fi
case "$(printf '%s\n' "$bound_out" | sed -n 's/^bound //p' | head -n 1)" in
no) ;;
yes)
	fail base-bound "取り込み先 ${target} はチケットに結び付く（$(printf '%s\n' "$bound_out" | sed -n 's/^why //p' | head -n 1)）ので、直接作業では切りません。チケットの作業は ccnavi-ticket.sh で進めます（.ccnavi/docs/worktree.md）。"
	;;
*)
	fail base-bound-unknown "取り込み先 ${target} がチケットに結び付くかを決められません（置き場の読み取りに失敗）。ユーザに確かめてください。"
	;;
esac

# 親のワークツリーから直接作業へ切り替えられるのは、承認依頼の時点（親の提案が todo/ にあり、承認前）だけ。
# 承認済み・レビュー待ち・着手済みの子があると、切り替えても取り下げられないものが残るので、切る前に断る。
# 断るのは cwd が親のワークツリーのときだけ（ワークスペースのルートや子のワークツリーから打てば掛からない。意図して残した抜け道）。
# 実行ファイルに聞けない・古くて stage が無いときは通す。--adopt は対象外（親のワークツリーの引き取りは adopt-ticket が断る）。
if [ -z "$adopt" ]; then
	pb_name=$(basename "$top")
	pb_out=$(ccnavi_ask "$root" tree leftover "$top" "$pb_name") || pb_out=""
	if [ "$(printf '%s\n' "$pb_out" | head -n 1)" = "tree 1" ]; then
		pb_stage=$(printf '%s\n' "$pb_out" | sed -n 's/^stage //p' | head -n 1)
		pb_doing=$(printf '%s\n' "$pb_out" | sed -n 's/^doing //p' | head -n 1)
		case "$pb_doing" in
		'' | *[!0-9]*) pb_doing=0 ;;
		esac
		pb_why=""
		case "$pb_stage" in
		doing) pb_why="親 ${pb_name} は承認済みで、着手済みの子・MR が取り下げられずに残る" ;;
		review) pb_why="親 ${pb_name} は完了済み（レビュー待ち）で、MR が取り下げられずに残る" ;;
		*) [ "$pb_doing" -eq 0 ] || pb_why="親 ${pb_name} に着手済みの子が ${pb_doing} 件あり、取り下げられずに残る" ;;
		esac
		if [ -n "$pb_why" ]; then
			log_info 親のワークツリーから切り替えない -- "parent=$pb_name" "stage=$pb_stage" "doing=$pb_doing"
			printf 'ccnavi-worktree: ワークツリーは切っていない。親のワークツリー（%s）から直接作業へは切り替えられない。%s。\n' "$pb_name" "$pb_why" >&2
			printf '親のワークツリーから切り替えられるのは、承認依頼の時点（親の提案が todo/ にあり、承認前）だけ。\n' >&2
			if [ "$pb_stage" = doing ]; then
				ccnavi_text "$root" NOTE_WORKTREE_PARENT_CLOSE >&2
			fi
			[ "$pb_doing" -eq 0 ] || ccnavi_text "$root" NOTE_WORKTREE_PARENT_CHILDREN "count=$pb_doing" >&2
			ccnavi_text "$root" NEXT_WORKTREE_PARENT_ACTIVE >&2
			exit 1
		fi
	fi
fi

# 同じ名前の終わっていない取り込み（marker）が残っていれば切らない。前の作業の K や一時ワークツリーを
# 新しい作業と取り違え、新しい記録と ref を片付けてしまうため。
mk_dir=$(git -C "$main_tree" rev-parse --git-path ccnavi-integrate 2>/dev/null || :)
case "$mk_dir" in
/* | [A-Za-z]:*) ;;
*) mk_dir="$main_tree/$mk_dir" ;;
esac
if [ -n "$mk_dir" ] && [ -f "$mk_dir/$name/marker" ]; then
	fail marker-left "${name} の取り込みの記録（${mk_dir}/${name}/marker）が残っています。前の取り込みが終わっていません。ccnavi-git.sh integrate-done ${name} で終わらせるか、integrate --abort ${name} でやめてから、切り直してください。"
fi

if [ -n "$base_arg" ] && [ "$base_arg" != "$target" ]; then
	fail base-mismatch "--base ${base_arg} は、今の取り込み先（${target}。ユーザのチェックアウト ${main_tree}）と違います。取り込み先が切り替わっていれば、ユーザに確かめて、報告からやり直してください（引数なしで打ち直す）。"
fi

# ---- 引き取り（--adopt）。記録の無い既存の直接作業のワークツリーをv4 の形式に引き取る。
# 1 回目は推定したS・取り込む範囲・差分のstatを見せて確認コードを出し、終了コード 3 で止まる。
# 2 回目は --accept <確認コード> が一致したときだけ、記録（snap_wip no・confirm adopt）とsのrefを書く。
if [ -n "$adopt" ]; then
	[ -n "$base_arg" ] ||
		fail adopt-no-base "--adopt には --base <取り込み先> が要ります（今のユーザのチェックアウトのブランチは ${target}）。ユーザに取り込み先を確かめてから付けてください。" 2
	[ -z "$wip_mode" ] ||
		fail adopt-wip "--adopt では --copy-wip / --no-wip は指定できません（書きかけはコピーしない。起点は merge-base から決める）。" 2
	ad_common=$(git -C "$top" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || :)
	[ -n "$ad_common" ] || fail no-common-dir "git の共通ディレクトリを読めません。"
	ad_common=$(ccnavi_phys "$ad_common")
	[ ! -e "$ad_common/ccnavi-direct/$name" ] ||
		fail adopt-recorded "${name} には直接作業の記録が既にあります（${ad_common}/ccnavi-direct/${name}）。引き取りは記録の無いワークツリーだけです。"
	[ -d "$dest" ] && [ ! -L "$dest" ] ||
		fail adopt-no-worktree "ワークツリー ${dest} がありません。引き取るのは .claude/worktrees/${name} の既存のワークツリーです。"
	ad_dest_common=$(git -C "$dest" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || :)
	[ -n "$ad_dest_common" ] && [ "$(ccnavi_phys "$ad_dest_common")" = "$ad_common" ] ||
		fail adopt-common-dir "ワークツリー ${dest} の git の共通ディレクトリが、実行した場所のリポジトリと違います。別のリポジトリのワークツリーは引き取りません。"
	ad_have=$(git worktree list --porcelain 2>/dev/null | awk -v want="$(ccnavi_phys "$dest")" '
		/^worktree / { p = substr($0, 10) }
		/^branch / { if (p == want) print substr($0, 8) }')
	[ "$ad_have" = "refs/heads/$name" ] ||
		fail adopt-branch "${dest} は、ブランチ ${name} を開いているこのリポジトリのワークツリーではありません（${ad_have:-ワークツリーの登録が無い}）。"
	ad_tip=$(git rev-parse "refs/heads/$name" 2>/dev/null || :)
	[ -n "$ad_tip" ] || fail adopt-no-branch "ブランチ ${name} を読めません。"
	# 取り込み先の判定（上の base-bound）とは別に、名前のほうもチケットに結び付かないことを確かめる。
	# チケットに結び付くワークツリーは、MR を経ずに取り込み先へ載せる直接作業に変えない。
	ccnavi_tree_bound "$root" "$dest" "$name" || :
	case "$ccnavi_tb_state" in
	no) ;;
	yes)
		fail adopt-ticket "${name} はチケットに結び付くワークツリーです（${ccnavi_tb_why}）。チケットの作業は直接作業に引き取らず、マージリクエストで出します。"
		;;
	undecided) fail adopt-ticket "${name} がチケットに結び付くかを決められないので、引き取りません。" ;;
	*) fail adopt-ticket "${name} がチケットに結び付くかを、実行ファイルに確かめられません。引き取りません。" ;;
	esac
	ad_bases=$(git merge-base --all "$name" "$target" 2>/dev/null || :)
	ad_count=$(printf '%s\n' "$ad_bases" | grep -c . || :)
	if [ "$ad_count" -eq 0 ]; then
		fail adopt-no-base-commit "${name} と ${target} の共通の祖先（merge-base）がありません。起点を決められないので引き取りません。"
	fi
	if [ "$ad_count" -gt 1 ]; then
		fail adopt-many-bases "${name} と ${target} の merge-base が複数あります（$(printf '%s\n' "$ad_bases" | cut -c1-8 | tr '\n' ' ')）。起点を 1 つに決められないので引き取りません。ワークツリーの中で ccnavi-git.sh merge ${target} を打ってから、やり直してください。"
	fi
	ad_s="$ad_bases"
	ad_s_short=$(git rev-parse --short "$ad_s" 2>/dev/null || printf '%s' "$ad_s")
	ad_input="adopt|repo=$ad_common|name=$name|target=$target|head=$head_sha|s=$ad_s|tip=$ad_tip"
	ad_code=$(printf '%s' "$ad_input" | git hash-object --stdin | cut -c1-8)
	ad_report() {
		printf '引き取りの確認が要る: 記録はまだ書いていない。\n'
		[ "$1" != code ] || ccnavi_text "$root" NOTE_WORKTREE_ADOPT_CODE_STALE
		printf '状態:\n'
		printf '  取り込み先 %s (%s)\n' "$target" "$main_tree"
		printf '  ワークツリー %s（ブランチ %s。先頭 %s）\n' "$dest" "$name" "$(git rev-parse --short "$ad_tip")"
		printf '  推定した起点 S: %s（%s と %s の merge-base）\n' "$ad_s_short" "$name" "$target"
		printf '取り込むコミットの範囲（%s..%s）:\n' "$ad_s_short" "$name"
		git log --oneline "$ad_s..$ad_tip" 2>/dev/null | head -n 50 | sed 's/^/  /'
		printf '取り込みの差分:\n'
		git diff --stat "$ad_s" "$ad_tip" 2>/dev/null | head -n 50 | sed 's/^/  /'
		printf '了承の文面: この範囲全体をエージェントの成果として扱う。範囲に他人のコミットや、取り込み先に入れたくない変更が混ざっていないかを、ユーザに確かめる\n'
		ccnavi_text "$root" NEXT_WORKTREE_ADOPT "name=$name" "target=$target" "code=$ad_code"
		ccnavi_text "$root" NOTE_WORKTREE_SIDE_BY_SIDE
	}
	log_info 引き取りを調べた -- "name=$name" "target=$target" "s=$ad_s" "tip=$ad_tip"
	if [ -n "$dry" ]; then
		printf 'dry-run: 何も書かない。引き取りの予定は次のとおり。\n'
		ad_report first
		exit 0
	fi
	if [ -z "$accept" ]; then
		ad_report first
		exit "$EXIT_CONFIRM"
	fi
	if [ "$accept" != "$ad_code" ]; then
		ad_report code
		exit "$EXIT_CONFIRM"
	fi
	ad_rec_dir="$ad_common/ccnavi-direct"
	ad_rec="$ad_rec_dir/$name"
	ad_tmp="$ad_rec_dir/.$name.$$"
	ad_side=no
	[ -z "$side" ] || ad_side=yes
	mkdir -p "$ad_rec_dir" 2>/dev/null || fail record-failed "記録の置き場（${ad_rec_dir}）を作れません。"
	{
		printf 'version 4\n'
		printf 'base_branch %s\n' "$target"
		printf 'base_sha %s\n' "$head_sha"
		printf 'snap_sha %s\n' "$ad_s"
		printf 'snap_wip no\n'
		printf 'side_by_side %s\n' "$ad_side"
		printf 'main_tree %s\n' "$main_tree"
		printf 'common_dir %s\n' "$ad_common"
		printf 'confirm adopt\n'
		printf 'created %s\n' "$(date +%s)"
	} >"$ad_tmp" 2>/dev/null && mv -f "$ad_tmp" "$ad_rec" ||
		{
			rm -f "$ad_tmp"
			fail record-failed "記録（${ad_rec}）を書けません。"
		}
	git update-ref "refs/ccnavi/direct/$name/s" "$ad_s" 2>/dev/null || {
		rm -f "$ad_rec"
		fail ref-failed "S の ref（refs/ccnavi/direct/${name}/s）を置けません。"
	}
	printf 'ワークツリーを引き取った: %s\n' "$dest"
	printf 'ブランチ: %s（起点 %s）\n' "$name" "$ad_s_short"
	printf '取り込み先: %s（%s）\n' "$target" "$main_tree"
	printf '起点: %s（%s と %s の merge-base。範囲全体を成果として扱う）\n' "$ad_s_short" "$name" "$target"
	if [ "$ad_side" = yes ]; then
		printf '横で作業する: はい（%s で見つかった変更は自動で戻さず報告する。ccnavi の設定ファイルは戻す）\n' "$main_tree"
	else
		printf '横で作業する: いいえ\n'
	fi
	log_info 引き取った -- "name=$name" "target=$target" "snap=$ad_s" "side=$ad_side"
	print_next
	exit 0
fi

# ---- 状態を調べる（C4〜C8）。
top_branch=$(git -C "$top" symbolic-ref --quiet --short HEAD 2>/dev/null || :)
c4=no
[ "$top_branch" = "$target" ] || c4=yes
c5=no
[ "$target" = "$integ" ] || c5=yes

# .claude/worktrees/ はワークツリーの置き場で、ccnavi 自身が作る。ユーザの書きかけではないので数えない。
# .gitignore に行が無い配布先では、最初のワークツリーを切ったあと未追跡のまま残る。
# 数えないのはこの置き場だけ（wip/・scratchpad/・logs/ などは今のまま）。パスが "…" で囲まれる場合も除く。
status_out=$(git -C "$main_tree" status --porcelain --untracked-files=all 2>/dev/null | grep -v -E '^.. "?\.claude/worktrees/' || :)
wip_changed=$(printf '%s\n' "$status_out" | grep -c -E '^([^?! ]| [^?! ])' || :)
wip_new=$(printf '%s\n' "$status_out" | grep -c '^??' || :)
wip_staged=$(git -C "$main_tree" diff --cached --name-only 2>/dev/null | grep -v -E '^"?\.claude/worktrees/' | grep -c . || :)
c7=no
[ "$wip_changed" -eq 0 ] && [ "$wip_new" -eq 0 ] || c7=yes

# 書きかけを含むスナップショット。ユーザのindexとワークツリーには触れない（一時的なindexで作る）。
# 日付と名前を固定するので、木と親が同じなら同じshaになる（確認コードに入れて、確認したあとの編集を検出する）。
make_candidate() {
	rm -f "$cand_index"
	GIT_INDEX_FILE="$cand_index" git -C "$main_tree" read-tree "$head_sha" 2>/dev/null || return 1
	# 除外指定つきの add は、除く先が .gitignore 済みだと終了コード 1 になる。素の add のあとで index から除く。
	GIT_INDEX_FILE="$cand_index" git -C "$main_tree" add -A >/dev/null 2>&1 || return 1
	# 他のワークツリー（.claude/worktrees/ の下）は、埋め込みリポジトリとして木に入るので取り込まない。
	GIT_INDEX_FILE="$cand_index" git -C "$main_tree" rm -r --cached -q --ignore-unmatch -- .claude/worktrees >/dev/null 2>&1 || return 1
	mc_tree=$(GIT_INDEX_FILE="$cand_index" git -C "$main_tree" write-tree 2>/dev/null) || return 1
	GIT_AUTHOR_NAME=ccnavi GIT_AUTHOR_EMAIL=ccnavi@localhost GIT_AUTHOR_DATE="1970-01-01T00:00:00+0000" \
		GIT_COMMITTER_NAME=ccnavi GIT_COMMITTER_EMAIL=ccnavi@localhost GIT_COMMITTER_DATE="1970-01-01T00:00:00+0000" \
		git -C "$main_tree" -c commit.gpgsign=false commit-tree "$mc_tree" -p "$head_sha" \
		-m "ccnavi: 切った時点の書きかけ" 2>/dev/null
}

common_dir=$(git -C "$top" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || :)
[ -n "$common_dir" ] || fail no-common-dir "git の共通ディレクトリを読めません。"
common_dir=$(ccnavi_phys "$common_dir")
ref_pending="refs/ccnavi/direct/$name/pending"
ref_s="refs/ccnavi/direct/$name/s"

# 直接作業の名前の形は実行ファイルに聞く。取れなければ案内は出さない。
# 名前が形に合わないことは、切る前に（--dry-run でも）言う。
print_name_note() {
	name_note=""
	if name_bin=$(ccnavi_bin "$root"); then
		name_note=$("$name_bin" --root "$root" worktree name "$name" 2>/dev/null </dev/null) || name_note=""
	fi
	[ -z "$name_note" ] || printf '%s\n' "$name_note"
}

if [ -n "$dry" ]; then
	printf 'dry-run: 何も作らない。調べた結果と予定は次のとおり（fetch はしていないので、origin との関係は見ていない）。\n'
	printf '取り込み先: %s（%s。リモートのデフォルトは %s）\n' "$target" "$main_tree" "$integ"
	[ "$c4" = no ] || printf '状態: cwd のブランチ（%s）が取り込み先と違う。実行するには --base %s が要る\n' "${top_branch:-ブランチの外}" "$target"
	[ "$c5" = no ] || printf '状態: 取り込み先がデフォルトブランチ（%s）でない。実行するには --accept が要る\n' "$integ"
	[ "$c7" = no ] || printf '状態: 書きかけ 変更 %s・新規 %s（staged %s）。実行するには --accept と、--copy-wip か --no-wip が要る\n' "$wip_changed" "$wip_new" "$wip_staged"
	printf 'ブランチ: %s（起点 %s）\n' "$name" "$(git -C "$main_tree" rev-parse --short "$head_sha")"
	printf 'ワークツリー: %s\n' "$dest"
	print_name_note
	exit 0
fi
print_name_note

# ---- fetch。取り込み先だけ。届かなくても止めず、リモート未確認として確認を求める。
remote_state=even
remote_ahead=0
remote_behind=0
origin_sha=""
remote_why=""
fetch_ok=yes
if ! cgit fetch origin "$target"; then
	fetch_ok=no
	log_info fetch-failed -- "target=$target"
	case "$cgit_out" in
	*"couldn't find remote ref"*) remote_state=absent ;;
	*)
		remote_state=unverified
		remote_why=$(printf '%s\n' "$cgit_out" | head -n 1)
		;;
	esac
fi
if [ "$fetch_ok" = yes ]; then
	if git show-ref --verify --quiet "refs/remotes/origin/$target"; then
		origin_sha=$(git rev-parse "refs/remotes/origin/$target" 2>/dev/null || :)
		remote_behind=$(git rev-list --count "$head_sha..$origin_sha" 2>/dev/null || echo 0)
		remote_ahead=$(git rev-list --count "$origin_sha..$head_sha" 2>/dev/null || echo 0)
		if [ "$remote_ahead" -ne 0 ] && [ "$remote_behind" -ne 0 ]; then
			remote_state=diverged
		elif [ "$remote_behind" -ne 0 ]; then
			remote_state=behind
		elif [ "$remote_ahead" -ne 0 ]; then
			remote_state=ahead
		fi
	else
		remote_state=absent
	fi
fi

# ---- origin に同じ名前・同じ識別子になるブランチがあれば止める（fetch が通ったときだけ見る）。
if [ "$fetch_ok" = yes ]; then
	remote_err="$root/logs/state/worktree-ls-remote-err-$$"
	remote_rc=0
	ccnavi_git_timed "${CCNAVI_WORKTREE_GIT_TIMEOUT:-30}" "$remote_err" "$top" ls-remote --heads origin >"$heads_list" || remote_rc=$?
	ls_why=$(head -n 1 "$remote_err" 2>/dev/null || :)
	rm -f "$remote_err"
	if [ "$remote_rc" -ne 0 ]; then
		log_info ls-remote できず -- "name=$name"
		remote_state=unverified
		remote_why="origin のブランチ一覧を取れなかった（${ls_why:-理由不明}）"
	else
		sed -n 's#^[0-9a-fA-F]\{1,\}[[:space:]]\{1,\}refs/heads/##p' "$heads_list" | tr -d '\r' >"$heads_list.names"
		mv "$heads_list.names" "$heads_list"
		if grep -F -x -q -- "$name" "$heads_list"; then
			log_info リモートに同名のブランチ -- "name=$name"
			printf 'ワークツリーは切っていない。origin にブランチ %s が既にある（MR の元ブランチかもしれない）。\n' "$name"
			printf '既にあるブランチは、MR を探して採用してよいか確かめてから出す。ccnavi-start.sh で始める:\n'
			printf '  sh %s --mr <MR の番号>\n  sh %s --issue <issue の番号>\n' "$(ccnavi_sh_quote "$scripts/ccnavi-start.sh")" "$(ccnavi_sh_quote "$scripts/ccnavi-start.sh")"
			printf '別の作業として新しく切るなら、別の名前で再実行する: sh %s <別の名前>\n' "$(ccnavi_sh_quote "$scripts/ccnavi-worktree.sh")"
			exit 1
		fi
		same_id_guard "$heads_list" origin
	fi
fi
if [ "$remote_state" = unverified ] && git show-ref --verify --quiet "refs/remotes/origin/$name"; then
	printf 'ワークツリーは切っていない。origin にブランチ %s がある（手元に残る追跡ブランチで分かった。MR の元ブランチかもしれない）。\n' "$name"
	printf '既にあるブランチは、MR を探して採用してよいか確かめてから出す。ccnavi-start.sh で始める:\n'
	printf '  sh %s --mr <MR の番号>\n  sh %s --issue <issue の番号>\n' "$(ccnavi_sh_quote "$scripts/ccnavi-start.sh")" "$(ccnavi_sh_quote "$scripts/ccnavi-start.sh")"
	exit 1
fi
c6=no
[ "$remote_state" = even ] || c6=yes

# ---- 書きかけの候補と確認コード。
cand=""
if [ "$c7" = yes ]; then
	cand=$(make_candidate) ||
		fail snapshot-failed "書きかけのスナップショットを作れませんでした（${main_tree}）。ユーザに確かめてください。"
fi
code_input="repo=$common_dir|name=$name|target=$target|head=$head_sha|origin=${origin_sha:-$remote_state}|rs=$remote_state|default=$integ|cwd=${top_branch:--}|cand=${cand:--}"
code=$(printf '%s' "$code_input" | git hash-object --stdin | cut -c1-8)
need_accept=no
{ [ "$c5" = yes ] || [ "$c6" = yes ] || [ "$c7" = yes ]; } && need_accept=yes

# 確認が要るときの報告。切らずに終了コード 3 で止まる。
report_and_stop() {
	rs_why="$1"
	[ -z "$cand" ] || git update-ref "$ref_pending" "$cand" 2>/dev/null || :
	log_info 確認が要る -- "name=$name" "target=$target" "c4=$c4" "c5=$c5" "c6=$c6" "c7=$c7" "why=$rs_why"
	printf '確認が要る: ワークツリーはまだ切っていない。\n'
	[ "$rs_why" = "code" ] && ccnavi_text "$root" NOTE_WORKTREE_CODE_STALE
	printf '状態:\n'
	printf '  取り込み先 %s (%s)。リモートのデフォルトは %s\n' "$target" "$main_tree" "$integ"
	[ "$c4" = no ] || printf '  cwd のブランチは %s で、取り込み先と違う\n' "${top_branch:-ブランチの外}"
	case "$remote_state" in
	behind) printf '  origin/%s より %s 件遅れている\n' "$target" "$remote_behind" ;;
	ahead) printf '  origin/%s より %s 件進んでいる（まだ送っていない）\n' "$target" "$remote_ahead" ;;
	diverged) printf '  origin/%s と分岐している（先行 %s 件・遅れ %s 件）\n' "$target" "$remote_ahead" "$remote_behind" ;;
	absent) printf '  origin に %s が無い\n' "$target" ;;
	unverified) printf '  リモート未確認（%s）\n' "${remote_why:-origin に届かない}" ;;
	esac
	[ "$c5" = no ] || printf '  取り込み先がデフォルトブランチ（%s）でない\n' "$integ"
	[ "$c7" = no ] || printf '  書きかけ: 変更 %s・新規 %s（staged %s。コピーする場合、staged かどうかは区別されない）\n' "$wip_changed" "$wip_new" "$wip_staged"
	[ "$wip_staged" -eq 0 ] || [ "$c7" = yes ] || printf '  staged %s（情報）\n' "$wip_staged"
	rs_main_q=$(ccnavi_sh_quote "$main_tree")
	rs_target_q=$(ccnavi_sh_quote "$target")
	case "$remote_state" in
	behind)
		printf '勧め: 先にローカルをリモートに合わせる\n'
		printf '  cd %s && git pull --ff-only origin %s   （書きかけは先にコミットか git stash push -u）\n' "$rs_main_q" "$rs_target_q"
		;;
	diverged)
		printf '勧め: 先にローカルをリモートに合わせる（合わせ方はユーザが選ぶ）\n'
		printf '  cd %s && git pull --rebase origin %s\n' "$rs_main_q" "$rs_target_q"
		printf '  cd %s && git pull --no-rebase origin %s\n' "$rs_main_q" "$rs_target_q"
		;;
	ahead) printf '勧め: 先に push するか、push せずに進めるかをユーザが決める（cd %s && git push origin %s）\n' "$rs_main_q" "$rs_target_q" ;;
	absent) printf '勧め: origin に %s を push するか、このまま進めるかをユーザが決める\n' "$target" ;;
	unverified) printf '勧め: origin に届いてから打ち直すと、リモートとの関係を確かめられる\n' ;;
	esac
	rs_risks=""
	case "$remote_state" in
	behind | diverged) rs_risks="${rs_risks}  - 古いコードの上で作業する。push の前に合わせ直しが要り、そのとき衝突しうる
" ;;
	ahead) rs_risks="${rs_risks}  - ${target} にだけある先行コミットの上で作業する。push する前に成果だけを送ることはできない
" ;;
	absent) rs_risks="${rs_risks}  - origin に無いブランチの上で作業する。成果は手元の ${target} にだけ載る
" ;;
	unverified) rs_risks="${rs_risks}  - リモートの最新を確かめないまま作業する。あとで合わせ直しが要りうる
" ;;
	esac
	[ "$c5" = no ] || rs_risks="${rs_risks}  - 最後は ${target} に 1 コミットで載る。${integ} に届くのは ${target} の MR がマージされたとき
"
	[ "$c4" = no ] || rs_risks="${rs_risks}  - cwd のブランチ（${top_branch:-ブランチの外}）ではなく、ユーザのチェックアウトの ${target} へ取り込む
"
	if [ -n "$rs_risks" ]; then
		printf '合わせない場合のリスク:\n'
		printf '%s' "$rs_risks"
	fi
	printf '選択肢:\n'
	if [ "$c7" = yes ]; then
		printf '  書きかけの扱い\n'
		printf '    A. コピーしない (--no-wip) : 書きかけはルートだけに残り、エージェントは HEAD から作業する\n'
		printf '    B. コピーする (--copy-wip) : いま確認した時点の書きかけをコピーする。このあとの編集はコピーされない。\n'
		printf '       コピーした書きかけそのものは成果に含めない。それに依存する変更は、取り込み結果だけでは動かないことがある\n'
	fi
	printf '  横で作業するか (--side-by-side)\n'
	printf '    このワークツリーが残っている間は、%s で見つかった変更（エージェントが Bash で書き換えた分も含む）を\n' "$main_tree"
	printf '    自動で戻さず、報告にする。ccnavi の設定ファイルは戻す\n'
	rs_args=""
	[ "$c4" = no ] || rs_args="$rs_args --base $rs_target_q"
	[ "$need_accept" = no ] || rs_args="$rs_args --accept $code"
	[ "$c7" = no ] || rs_args="$rs_args --no-wip"
	# 先頭の空白は文面の側が持つ（値の前後の空白は落ちる）。
	ccnavi_text "$root" NEXT_WORKTREE_CONFIRM "name=$name" "args_q=${rs_args# }"
	ccnavi_text "$root" NOTE_WORKTREE_WAIT_FOR_OK
	exit "$EXIT_CONFIRM"
}

if [ "$c7" = yes ] && [ -z "$wip_mode" ]; then
	report_and_stop wip
fi
if [ "$c4" = yes ] && [ -z "$base_arg" ]; then
	report_and_stop base
fi
if [ "$need_accept" = yes ]; then
	[ -n "$accept" ] || report_and_stop accept
	[ "$accept" = "$code" ] || report_and_stop code
fi

# ---- Sを決める。
if [ "$c7" = yes ] && [ "$wip_mode" = copy ]; then
	s_sha="$cand"
	s_wip=yes
else
	s_sha="$head_sha"
	s_wip=no
fi
s_short=$(git rev-parse --short "$s_sha" 2>/dev/null || printf '%s' "$s_sha")

# ---- 記録を書き、refを置いて切る。
side_word=no
[ -z "$side" ] || side_word=yes
confirm_word=-
[ "$need_accept" = no ] || confirm_word="$code"
rec_dir="$common_dir/ccnavi-direct"
rec_file="$rec_dir/$name"
rec_tmp="$rec_dir/.$name.$$"
mkdir -p "$rec_dir" 2>/dev/null || fail record-failed "記録の置き場（${rec_dir}）を作れません。"
{
	printf 'version 4\n'
	printf 'base_branch %s\n' "$target"
	printf 'base_sha %s\n' "$head_sha"
	printf 'snap_sha %s\n' "$s_sha"
	printf 'snap_wip %s\n' "$s_wip"
	printf 'side_by_side %s\n' "$side_word"
	printf 'main_tree %s\n' "$main_tree"
	printf 'common_dir %s\n' "$common_dir"
	printf 'confirm %s\n' "$confirm_word"
	printf 'created %s\n' "$(date +%s)"
} >"$rec_tmp" 2>/dev/null && mv -f "$rec_tmp" "$rec_file" ||
	{
		rm -f "$rec_tmp"
		fail record-failed "記録（${rec_file}）を書けません。"
	}

undo_cut() {
	rm -f "$rec_file"
	git update-ref -d "$ref_s" 2>/dev/null || :
}
git update-ref "$ref_s" "$s_sha" 2>/dev/null || {
	undo_cut
	fail ref-failed "S の ref（${ref_s}）を置けません。"
}
git update-ref -d "$ref_pending" 2>/dev/null || :
cgit worktree add "$dest" -b "$name" --no-track "$s_sha" || {
	undo_cut
	fail worktree-add-refused "ccnavi-git.sh worktree add が通りませんでした（上の出力の案内に従ってください）。記録と ref は消しました。"
}

printf 'ワークツリーを切った: %s\n' "$dest"
printf 'ブランチ: %s（起点 %s）\n' "$name" "$s_short"
printf '取り込み先: %s（%s）\n' "$target" "$main_tree"
if [ "$s_wip" = yes ]; then
	printf '起点: %s（確認した時点の書きかけを含む。確認したあとの編集はコピーされない）\n' "$s_short"
else
	printf '起点: %s（%s の HEAD。書きかけは含まない）\n' "$s_short" "$target"
fi
if [ "$side_word" = yes ]; then
	printf '横で作業する: はい（%s で見つかった変更は自動で戻さず報告する。ccnavi の設定ファイルは戻す）\n' "$main_tree"
else
	printf '横で作業する: いいえ\n'
fi
if [ "$c6" = yes ]; then
	case "$remote_state" in
	behind | diverged)
		ccnavi_text "$root" NOTE_WORKTREE_REMOTE_BEHIND "target=$target" "behind=$remote_behind" "main_tree=$main_tree" "branch=$target"
		;;
	ahead) printf '案内: 手元の %s は origin/%s より %s 件進んでいる（まだ送っていない）\n' "$target" "$target" "$remote_ahead" ;;
	absent) printf '案内: origin に %s が無い。成果は手元の %s に載る\n' "$target" "$target" ;;
	unverified) ccnavi_text "$root" NOTE_WORKTREE_REMOTE_UNVERIFIED "target=$target" ;;
	esac
fi
# ワークスペースのルートで実行され、projects/ の下にリポジトリがあるときは、projects/ の変更なら
# cd してから実行し直すよう案内する。断らない。ワークスペース自身の作業もルートから実行するのが正しく、
# どのプロジェクトの話かは sh には分からない。単体 clone（projects/ が無い）と、プロジェクトの中で
# 実行したときは出さない。
top_cmp=$(ccnavi_phys "$top" | tr '[:upper:]' '[:lower:]')
root_cmp=$(ccnavi_phys "$root" | tr '[:upper:]' '[:lower:]')
if [ "$top_cmp" = "$root_cmp" ]; then
	for pj_git in "$root"/projects/*/.git; do
		if [ -e "$pj_git" ]; then
			ccnavi_text "$root" NOTE_WORKTREE_PROJECTS_HINT
			break
		fi
	done
fi
# 親のワークツリーから切り替えたとき（承認前の提案だけ。それ以外は切る前に断った）、承認待ちの提案・MR は取り下げられずに残る。
# 何が残っているかは実行ファイルに聞く（sh はチケットの置き場を読まない）。聞けないときは何も出さない。
lo_name=$(basename "$top")
lo_out=$(ccnavi_ask "$root" tree leftover "$top" "$lo_name") || lo_out=""
lo_get() { printf '%s\n' "$lo_out" | sed -n "s/^$1 //p" | head -n 1; }
if [ "$(printf '%s\n' "$lo_out" | head -n 1)" = "tree 1" ] && [ "$(lo_get leftover)" = yes ]; then
	ccnavi_text "$root" NOTE_WORKTREE_LEFTOVER "parent=$lo_name"
	[ "$(lo_get proposal)" != yes ] || ccnavi_text "$root" NOTE_WORKTREE_LEFTOVER_PROPOSAL "parent=$lo_name"
	[ "$(lo_get mr)" = - ] || ccnavi_text "$root" NOTE_WORKTREE_LEFTOVER_MR "mr=#$(lo_get mr)"
fi
print_next
log_info 完了 -- "name=$name" "target=$target" "snap=$s_sha" "wip=$s_wip" "side=$side_word"
