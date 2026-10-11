#!/bin/sh
# ccnavi-worktree 作業用のワークツリーを統合先から切る。fetch・遅れの確認・起点の選択・worktree add を 1 本にする。
#
#   sh .ccnavi/scripts/ccnavi-worktree.sh <名前> [--offline] [--dry-run]
#
# 手で打つと、次の順になる（docs/claude/worktree.md「ワークツリーの作り方」）。
#   1. ccnavi-git.sh fetch origin <統合先>
#   2. ccnavi-git.sh log --oneline <統合先>..origin/<統合先> で遅れを確かめる
#   3. 遅れていれば origin/<統合先> から --no-track で、遅れていなければ <統合先> から切る
#   4. ccnavi-git.sh worktree add .claude/worktrees/<名前> -b <名前> <起点>（<統合先> を省かない。行き先は cwd に
#      合わせて書く）
# このスクリプトがその順に打つ。行き先は <ワークスペースルート>/.claude/worktrees/<名前>、ブランチ名は <名前> で、
# 名前とブランチ名を揃える決まりもここで守る。git は ccnavi-git.sh を通すので、拒否や警告の判定は
# ccnavi-git.sh のままで、ここでは判定を増やさない。手元の統合先は進めない（進めるのはセッション開始時の
# ccnavi-fetch.sh だけ）。ワークツリーが既にあれば、何もせずパスを出す（冪等）。
#
# fetch は origin に通信する。届かないとき（オフライン・認証・Claude remote のコンテナの制限）は、
# ワークツリーを切らずに終了コード 4 で止まり、原因の見方と、次に取れる手を標準出力に出す。
#   - 原因を直せるなら直して、同じコマンドを打ち直す
#   - 直せない・オフラインで続けるなら --offline を付けて打ち直す。fetch は試すが、失敗しても手元の統合先から切る。
#     origin が古いかもしれないので、切ったことをユーザに伝える（取り込むときに遅れが分かる）
#
# --dry-run は何も作らず、切る予定の起点とパスだけを出す（fetch もしない）。
#
# 終了コード: 0 完了 / 1 前提の未充足か worktree add の拒否 / 2 引数か環境の誤り /
#             4 origin に届かず、案内の手が要る（案内を標準出力に出した）

set -eu

EXIT_NET=4

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
sh .ccnavi/scripts/ccnavi-worktree.sh <名前> [--offline] [--dry-run]

  <名前>      ワークツリーの名前。ブランチ名も同じ名前で切る（/ を含められない）。行き先は
              <ワークスペースルート>/.claude/worktrees/<名前>
  --offline   fetch が失敗しても、手元の統合先から切って続ける（origin が古いかもしれない）
  --dry-run   何も作らず、切る予定の起点とパスだけ出す

fetch・遅れの確認・起点の選択・worktree add を順に行う。遅れていれば origin/<統合先> から --no-track で切る。
ワークツリーが既にあれば、パスを出すだけ（冪等）。origin に届かないときは終了コード 4 で止まり、次の手を出す。
終了コード: 0 完了 / 1 前提の未充足か worktree add の拒否 / 2 引数か環境の誤り / 4 origin に届かず案内の手が要る
USAGE
}

name=""
offline=""
dry=""
while [ "$#" -gt 0 ]; do
	case "$1" in
	--offline)
		offline=1
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
		fail unknown-arg "知らない引数です（$1）。" 2
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
top=$(git rev-parse --show-toplevel 2>/dev/null || :)
[ -n "$top" ] ||
	fail not-git "cwd が git のリポジトリの中ではありません。ワークスペースか projects/<名前>/ の中で打ってください。"
scripts="$root/.ccnavi/scripts"
dest="$root/.claude/worktrees/$name"
integ=$(ccnavi_integration "$(ccnavi_phys "$top")" "$root") || integ=""
[ -n "$integ" ] ||
	fail no-integration "統合先の名前を決められません。CCNAVI_INTEGRATION_BRANCH に統合先の名前を渡すか、origin/HEAD を設定してください（git remote set-head origin -a はユーザに頼む）。"
log_info 始めた -- "name=$name" "integration=$integ" "offline=${offline:-no}" "dry=${dry:-no}"

# ccnavi-git.sh 経由の git。成功は何も出さず、失敗は出力を標準エラーへ出して 1 を返す。
cgit() {
	cgit_out=$(sh "$scripts/ccnavi-git.sh" "$@" 2>&1) || {
		printf '%s\n' "$cgit_out" >&2
		return 1
	}
	return 0
}

if [ -e "$dest" ]; then
	printf 'ワークツリーは既にある: %s\n' "$dest"
	log_info 既にある -- "name=$name"
	exit 0
fi
if git show-ref --verify --quiet "refs/heads/$name"; then
	fail branch-exists "ブランチ ${name} が既にあります（ワークツリーは無い）。既にあるブランチを出すなら ccnavi-git.sh worktree add .claude/worktrees/${name} ${name}、新しく切るなら別の名前にしてください。"
fi

# ---- 起点を決める。遅れていれば origin/<統合先>、遅れていなければ <統合先>。
# 遅れの数は fetch の後で数える。dry-run は fetch しないので、手元の ref のままの数で予定を出す。
choose_base() {
	base="$integ"
	track=""
	behind=0
	if git show-ref --verify --quiet "refs/remotes/origin/$integ" &&
		git show-ref --verify --quiet "refs/heads/$integ"; then
		behind=$(git rev-list --count "$integ..origin/$integ" 2>/dev/null || echo 0)
		if [ "$behind" -ne 0 ]; then
			base="origin/$integ"
			track="--no-track"
		fi
	elif git show-ref --verify --quiet "refs/remotes/origin/$integ"; then
		base="origin/$integ"
		track="--no-track"
	elif ! git show-ref --verify --quiet "refs/heads/$integ"; then
		fail no-base "統合先 ${integ} が手元にも origin にもありません。統合先の名前を確かめてください。"
	fi
}

if [ -n "$dry" ]; then
	choose_base
	printf 'dry-run: 何も作らない。作る予定は次のとおり。\n'
	printf 'ブランチ: %s（起点 %s%s）\n' "$name" "$base" "${track:+、$track}"
	printf 'ワークツリー: %s\n' "$dest"
	[ "$behind" -eq 0 ] || printf '案内: 手元の %s は origin/%s より %s 件遅れている（fetch 前の数）\n' "$integ" "$integ" "$behind"
	exit 0
fi

# ---- fetch。origin に通信するので、届かないときの手を案内する。
fetch_note=""
if ! cgit fetch origin "$integ"; then
	log_info fetch-failed -- "integration=$integ" "offline=${offline:-no}"
	if [ -z "$offline" ]; then
		printf 'ワークツリーは切っていない。origin から統合先 %s を取れなかった（上の出力に原因が出ている。ネットワーク・認証・origin の設定のどれか）。\n' "$integ"
		printf '次の手:\n'
		printf '  1. 原因を直せるなら直して、同じコマンドを打ち直す: sh %s/ccnavi-worktree.sh %s\n' "$scripts" "$name"
		printf '     認証や origin の設定が要るときは、ユーザに頼む（ccnavi は git config を通さない）\n'
		printf '  2. 直せない・オフラインで続けるなら --offline を付けて打ち直す: sh %s/ccnavi-worktree.sh %s --offline\n' "$scripts" "$name"
		printf '     手元の %s から切る。origin が古いかもしれないので、切ったことをユーザに伝える\n' "$integ"
		exit "$EXIT_NET"
	fi
	fetch_note="origin から取れなかったため、手元の ${integ} から切った（オフラインの可能性。origin/${integ} が古いかもしれない）。つながったら ccnavi-git.sh fetch origin ${integ} で確かめ、遅れていれば取り込む前に統合先を合わせる。ユーザにも伝える。"
fi

choose_base

# ---- worktree add。起点は省かない。拒否の文面は ccnavi-git.sh のもの（行き先・起点の直し方つき）をそのまま見せる。
if [ -n "$track" ]; then
	cgit worktree add "$dest" -b "$name" "$track" "$base" ||
		fail worktree-add-refused "ccnavi-git.sh worktree add が通りませんでした（上の出力の案内に従ってください）。"
else
	cgit worktree add "$dest" -b "$name" "$base" ||
		fail worktree-add-refused "ccnavi-git.sh worktree add が通りませんでした（上の出力の案内に従ってください）。"
fi

printf 'ワークツリーを切った: %s\n' "$dest"
printf 'ブランチ: %s（起点 %s%s）\n' "$name" "$base" "${track:+、$track}"
[ "$behind" -eq 0 ] || printf '案内: 手元の %s は origin/%s より %s 件遅れていたので origin/%s から切った（手元の %s は進めていない）\n' "$integ" "$integ" "$behind" "$integ" "$integ"
[ -z "$fetch_note" ] || printf '案内: %s\n' "$fetch_note"
log_info 完了 -- "name=$name" "base=$base"
