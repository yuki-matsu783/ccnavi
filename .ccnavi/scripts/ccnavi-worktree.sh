#!/bin/sh
# ccnavi-worktree 作業用のワークツリーを統合先から切る。fetch・遅れの確認・起点の選択・worktree add を 1 本にする。
#
# 直接作業（チケットを使わない作業）のワークツリー用で、名前と同じブランチを新しく切る。親子のチケットの
# 親のワークツリーには使わない。親は識別子の名前のブランチを作らず、親のブランチ（MR の元ブランチ）を直接
# チェックアウトする（ccnavi-start.sh --mr <番号> / --issue <番号> が作る）。子のワークツリーは
# ccnavi-ticket.sh start が親のブランチから作る。
#
#   sh .ccnavi/scripts/ccnavi-worktree.sh <名前> [--offline] [--from-origin] [--dry-run]
#
# 手で打つと、次の順になる（.ccnavi/docs/worktree.md「ワークツリーを切る」）。
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
# 手元の統合先が origin/<統合先> と分岐している（先行も遅れもある）ときは、切らずに終了コード 1 で止まり、
# 先に手元の統合先を揃えるよう案内する（そのまま切ると、手元にだけある先行コミットを知らないブランチになり、
# 統合で詰まる）。承知のうえなら --from-origin を付けると、警告つきで origin/<統合先> から切る。
# fetch が失敗して --offline で続けるときは、origin/<統合先> が古いかもしれないので、止めずに警告だけにする。
#
# origin か手元に同じ識別子になるブランチ（同じ名前、`/` を `-` に置き換えると同じになる名前、英字の大文字小文字だけ
# 違う名前）があるときは、切らずに終了コード 1 で止まり、ccnavi-start.sh へ案内する。既にあるブランチ（MR の元ブランチ
# など）は、MR を探して採用する確認をしてから出す必要があり、ここで新しく切ると、同じ識別子のブランチが 2 本になる
# （ワークツリーの置き場が重なる）。origin のブランチ一覧（ls-remote）を取れないときは、確かめずに切らず、終了コード 4 で
# 止まる（--offline なら、手元に残る origin の追跡ブランチで確かめて「リモート未確認」と言って続ける）。
#
# 名前の字の種類・NFC・長さ（64 文字まで）は実行ファイルに聞く（`worktree id`）。sh はマルチバイトの字を見分けない。
# 答えが無ければ「確かめられない」と止まる。
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
sh .ccnavi/scripts/ccnavi-worktree.sh <名前> [--offline] [--from-origin] [--dry-run]

  <名前>      ワークツリーの名前。ブランチ名も同じ名前で切る（/ を含められない）。行き先は
              <ワークスペースルート>/.claude/worktrees/<名前>

直接作業（チケットを使わない作業）用。親子のチケットの親のワークツリーには使わない
（親は ccnavi-start.sh --mr <番号> / --issue <番号> で、親のブランチを直接チェックアウトして作る）。
  --offline   fetch が失敗しても、手元の統合先から切って続ける（origin が古いかもしれない）
  --from-origin
              手元の統合先が origin と分岐していても、警告のうえ origin/<統合先> から切る
              （切ったブランチは手元にだけある先行コミットを知らないので、統合で詰まりうる）
  --dry-run   何も作らず、切る予定の起点とパスだけ出す

fetch・遅れの確認・起点の選択・worktree add を順に行う。遅れていれば origin/<統合先> から --no-track で切る。
ワークツリーが既にあれば、パスを出すだけ（冪等）。origin に届かないときは終了コード 4 で止まり、次の手を出す。
終了コード: 0 完了 / 1 前提の未充足か worktree add の拒否 / 2 引数か環境の誤り / 4 origin に届かず案内の手が必要です
USAGE
}

name=""
offline=""
from_origin=""
dry=""
while [ "$#" -gt 0 ]; do
	case "$1" in
	--offline)
		offline=1
		shift
		;;
	--from-origin)
		from_origin=1
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
	fail id-unchecked "名前（${name}）が使えるか（字の種類・64 文字以内）を確かめられない（ccnavi の実行ファイルが答えない）。build.py でビルドするか、scripts/ccnavi-setup.sh で実行ファイルを配置してから再実行してください。"
[ -z "$ccnavi_id_direct" ] || fail bad-name "名前（${name}）は使えません（${ccnavi_id_direct}）。" 2
top=$(git rev-parse --show-toplevel 2>/dev/null || :)
[ -n "$top" ] ||
	fail not-git "cwd が git のリポジトリの中ではありません。ワークスペースか projects/<名前>/ の中で実行してください。"
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

if [ -e "$dest" ] || [ -L "$dest" ]; then
	# 在るのは、このリポジトリのワークツリーで、同じ名前のブランチを開いているときだけ成功にする。
	have=$(git worktree list --porcelain 2>/dev/null | awk -v want="$(ccnavi_phys "$dest")" '
		/^worktree / { p = substr($0, 10) }
		/^branch / { if (p == want) print substr($0, 8) }')
	[ "$have" = "refs/heads/$name" ] ||
		fail dest-exists "${dest} は既にありますが、ブランチ ${name} を開いているこのリポジトリのワークツリーではありません（${have:-ワークツリーの登録が無い}）。中身を確かめ、不要ならユーザに確かめてから片付け、別の名前で再実行してください。"
	printf 'ワークツリーは既にある: %s\n' "$dest"
	log_info 既にある -- "name=$name"
	exit 0
fi
if git show-ref --verify --quiet "refs/heads/$name"; then
	fail branch-exists "ブランチ ${name} が既にあります（ワークツリーはありません）。既にあるブランチを出すなら ccnavi-git.sh worktree add .claude/worktrees/${name} ${name}、新しく切るなら別の名前にしてください。"
fi

# 同じ識別子になる別のブランチ（feature/7-x に対する feature-7-x、英字の大文字小文字だけ違う名前）があれば止める。
# <一覧のファイル> <どこ（手元・origin）>
heads_list="$root/logs/state/worktree-heads-$$"
mkdir -p "$root/logs/state" 2>/dev/null || :
trap 'rm -f "$heads_list"' EXIT
same_id_guard() {
	sg_other=$(ccnavi_id_clash "$name" "$1") || return 0
	log_info 同じ識別子のブランチ -- "name=$name" "where=$2"
	printf 'ワークツリーは切っていない。%s にブランチ %s があり、%s と同じ識別子（%s。英字の大文字小文字は区別しない）になる。\n' "$2" "$sg_other" "$name" "$name"
	printf '同じ識別子のブランチが 2 本あると、ワークツリーの置き場が重なる。既にあるブランチは ccnavi-start.sh で始める:\n'
	printf '  sh %s/ccnavi-start.sh --mr <MR の番号>\n  sh %s/ccnavi-start.sh --issue <issue の番号>\n' "$scripts" "$scripts"
	printf '別の作業として新しく切るなら、別の名前で再実行する: sh %s/ccnavi-worktree.sh <別の名前>\n' "$scripts"
	exit 1
}
{
	git for-each-ref --format='%(refname:short)' refs/heads 2>/dev/null || :
	git for-each-ref --format='%(refname:lstrip=3)' refs/remotes/origin 2>/dev/null | grep -v -x 'HEAD' || :
} >"$heads_list"
same_id_guard "$heads_list" 手元

# ---- 起点を決める。遅れていれば origin/<統合先>、遅れていなければ <統合先>。
# 遅れの数は fetch の後で数える。dry-run は fetch しないので、手元の ref のままの数で予定を出す。
choose_base() {
	base="$integ"
	track=""
	behind=0
	ahead=0
	if git show-ref --verify --quiet "refs/remotes/origin/$integ" &&
		git show-ref --verify --quiet "refs/heads/$integ"; then
		behind=$(git rev-list --count "$integ..origin/$integ" 2>/dev/null || echo 0)
		ahead=$(git rev-list --count "origin/$integ..$integ" 2>/dev/null || echo 0)
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

# 手元の統合先が origin と分岐している（先行も遅れもある）。そのまま切ると、手元にだけある先行コミットを知らない
# ブランチになり、統合で詰まる。--from-origin が無ければ切らずに止まる。dry-run は止まらず、止まることを案内するだけ。fetch が失敗した（--offline）ときは古い ref の判定なので止めない。
diverged_guard() {
	[ "$ahead" -ne 0 ] && [ "$behind" -ne 0 ] || return 0
	if [ -n "$fetch_note" ]; then
		diverged_note="手元の ${integ} は origin/${integ} と分岐しているように見える（先行 ${ahead} 件・遅れ ${behind} 件）が、fetch が失敗していて origin/${integ} が古いかもしれないので止めなかった。つながったら ccnavi-git.sh fetch origin ${integ} で確かめ、本当に分岐していれば手元の ${integ} を揃えるようユーザに頼む。"
		return 0
	fi
	if [ -n "$dry" ]; then
		if [ -n "$from_origin" ]; then
			diverged_note="手元の ${integ} は origin/${integ} と分岐している（先行 ${ahead} 件・遅れ ${behind} 件。fetch 前の数）。実行すると --from-origin で origin/${integ} から切る。手元にだけある先行 ${ahead} 件を、そのブランチは知らず、統合（integrate）で詰まる。"
		else
			diverged_note="手元の ${integ} は origin/${integ} と分岐している（先行 ${ahead} 件・遅れ ${behind} 件。fetch 前の数）。実行すると切らずに終了コード 1 で止まる。承知のうえなら --from-origin を付けると origin/${integ} から切る。"
		fi
		return 0
	fi
	if [ -n "$from_origin" ]; then
		diverged_note="手元の ${integ} は origin/${integ} と分岐している（先行 ${ahead} 件・遅れ ${behind} 件）が、--from-origin で origin/${integ} から切った。手元にだけある先行 ${ahead} 件を、このブランチは知らない。統合（integrate）で詰まるので、先に手元の ${integ} を origin/${integ} に揃えるようユーザに頼む。詰まって取り込みの印（.git/ccnavi-integrate/<名前>/marker）が残ったときも、ユーザが手元の ${integ} を揃えてから再実行します。"
		return 0
	fi
	log_info 分岐で止めた -- "integration=$integ" "ahead=$ahead" "behind=$behind"
	printf 'ワークツリーは切っていない。手元の %s が origin/%s と分岐している（先行 %s 件・遅れ %s 件）。\n' "$integ" "$integ" "$ahead" "$behind"
	printf 'このまま origin/%s から切ると、手元にだけある先行 %s 件を知らないブランチになり、統合（integrate）で詰まる。\n' "$integ" "$ahead"
	printf '次の手:\n'
	printf '  1. 先に手元の %s を origin/%s に揃える。ccnavi は手元の統合先を進めないので、ユーザに頼む（先行分を push するか、別のブランチに退避してから揃える）\n' "$integ" "$integ"
	printf '  2. 揃えたら同じコマンドを再実行する: sh %s/ccnavi-worktree.sh %s\n' "$scripts" "$name"
	printf '  3. 先行分が不要・承知のうえなら --from-origin を付けて再実行します（警告つきで origin/%s から切る）\n' "$integ"
	exit 1
}
diverged_note=""
fetch_note=""

if [ -n "$dry" ]; then
	choose_base
	diverged_guard
	printf 'dry-run: 何も作らない。作る予定は次のとおり。\n'
	printf 'ブランチ: %s（起点 %s%s）\n' "$name" "$base" "${track:+、$track}"
	printf 'ワークツリー: %s\n' "$dest"
	[ "$behind" -eq 0 ] || printf '案内: 手元の %s は origin/%s より %s 件遅れている（fetch 前の数）\n' "$integ" "$integ" "$behind"
	[ -z "$diverged_note" ] || printf '案内: %s\n' "$diverged_note"
	exit 0
fi

# ---- fetch。origin に通信するので、届かないときの手を案内する。
fetch_note=""
if ! cgit fetch origin "$integ"; then
	log_info fetch-failed -- "integration=$integ" "offline=${offline:-no}"
	if [ -z "$offline" ]; then
		printf 'ワークツリーは切っていない。origin から統合先 %s を取れなかった（上の出力に原因が出ている。ネットワーク・認証・origin の設定のどれか）。\n' "$integ"
		case "$cgit_out" in
		*"couldn't find remote ref"*)
			printf '注意: origin に統合先 %s がありません（名前の誤りか、統合先の設定の誤り）。--offline で切ると古い手元の統合先から切るので、先に名前を確かめる（CCNAVI_INTEGRATION_BRANCH）。\n' "$integ"
			;;
		esac
		printf '次の手:\n'
		printf '  1. 原因を直せるなら直して、同じコマンドを再実行する: sh %s/ccnavi-worktree.sh %s\n' "$scripts" "$name"
		printf '     認証や origin の設定が必要なときは、ユーザに頼む（ccnavi は git config を通さない）\n'
		printf '  2. 直せない・オフラインで続けるなら --offline を付けて再実行する: sh %s/ccnavi-worktree.sh %s --offline\n' "$scripts" "$name"
		printf '     手元の %s から切る。origin が古いかもしれないので、切ったことをユーザに伝える\n' "$integ"
		exit "$EXIT_NET"
	fi
	fetch_note="origin から取れなかったため、手元の ${integ} から切った（オフラインの可能性。origin/${integ} が古いかもしれない）。つながったら ccnavi-git.sh fetch origin ${integ} で確かめ、遅れていれば取り込む前に統合先を合わせる。ユーザにも伝える。"
fi

choose_base
diverged_guard

# ---- origin に同じ名前・同じ識別子になるブランチがあれば止める（fetch が通ったときだけ見る。届かないときは見られない）。
remote_note=""
if [ -z "$fetch_note" ]; then
	remote_err="$root/logs/state/worktree-ls-remote-err-$$"
	remote_rc=0
	ccnavi_git_timed "${CCNAVI_WORKTREE_GIT_TIMEOUT:-30}" "$remote_err" "$top" ls-remote --heads origin >"$heads_list" || remote_rc=$?
	remote_why=$(head -n 1 "$remote_err" 2>/dev/null || :)
	rm -f "$remote_err"
	remote_out=""
	if [ "$remote_rc" -eq 0 ]; then
		sed -n 's#^[0-9a-fA-F]\{1,\}[[:space:]]\{1,\}refs/heads/##p' "$heads_list" | tr -d '\r' >"$heads_list.names"
		mv "$heads_list.names" "$heads_list"
		grep -F -x -q -- "$name" "$heads_list" && remote_out=yes
	fi
	if [ "$remote_rc" -ne 0 ]; then
		log_info ls-remote できず -- "name=$name"
		if [ -z "$offline" ]; then
			printf 'ワークツリーは切っていない。origin のブランチ一覧を取れなかった（%s）。リモート未確認のままでは、同じ識別子になるブランチが origin に無いか確かめられない。\n' "${remote_why:-理由不明}"
			printf '次の手:\n'
			printf '  1. 原因を直せるなら直して、同じコマンドを再実行する: sh %s/ccnavi-worktree.sh %s\n' "$scripts" "$name"
			printf '  2. 直せない・オフラインで続けるなら --offline を付けて再実行する: sh %s/ccnavi-worktree.sh %s --offline\n' "$scripts" "$name"
			printf '     手元に残る origin の追跡ブランチでだけ確かめて切る。切ったことと、リモート未確認であることをユーザに伝える\n'
			exit "$EXIT_NET"
		fi
		remote_note="リモート未確認。origin のブランチ一覧を取れなかった（${remote_why:-理由不明}）ので、同じ識別子のブランチは手元に残る origin の追跡ブランチでだけ確かめた。"
	elif [ -n "$remote_out" ]; then
		log_info リモートに同名のブランチ -- "name=$name"
		printf 'ワークツリーは切っていない。origin にブランチ %s が既にある（MR の元ブランチかもしれない）。\n' "$name"
		printf '既にあるブランチは、MR を探して採用してよいか確かめてから出す。ccnavi-start.sh で始める:\n'
		printf '  sh %s/ccnavi-start.sh --mr <MR の番号>\n  sh %s/ccnavi-start.sh --issue <issue の番号>\n' "$scripts" "$scripts"
		printf '別の作業として新しく切るなら、別の名前で再実行する: sh %s/ccnavi-worktree.sh <別の名前>\n' "$scripts"
		exit 1
	else
		same_id_guard "$heads_list" origin
	fi
else
	# origin に届かない（--offline）。手元に残る origin の追跡ブランチに同じ名前があれば、origin にあるものとして止める
	if git show-ref --verify --quiet "refs/remotes/origin/$name"; then
		printf 'ワークツリーは切っていない。origin にブランチ %s がある（手元に残る追跡ブランチで分かった。MR の元ブランチかもしれない）。\n' "$name"
		printf '既にあるブランチは、MR を探して採用してよいか確かめてから出す。ccnavi-start.sh で始める:\n'
		printf '  sh %s/ccnavi-start.sh --mr <MR の番号>\n  sh %s/ccnavi-start.sh --issue <issue の番号>\n' "$scripts" "$scripts"
		exit 1
	fi
	remote_note="リモート未確認。origin に届かなかったので、同じ識別子のブランチは手元に残る origin の追跡ブランチでだけ確かめた。"
fi

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
[ "$behind" -eq 0 ] || [ -n "$diverged_note" ] || printf '案内: 手元の %s は origin/%s より %s 件遅れていたので origin/%s から切った（手元の %s は進めていない）\n' "$integ" "$integ" "$behind" "$integ" "$integ"
[ -z "$fetch_note" ] || printf '案内: %s\n' "$fetch_note"
[ -z "$diverged_note" ] || printf '案内: %s\n' "$diverged_note"
[ -z "$remote_note" ] || printf '案内: %s\n' "$remote_note"
# 直接作業の名前の形は実行ファイルに聞く（sh は形を判定しない）。取れなければ案内は出さない。拒否はしない。
name_note=""
if name_bin=$(ccnavi_bin "$root"); then
	name_note=$("$name_bin" --root "$root" worktree name "$name" 2>/dev/null </dev/null) || name_note=""
fi
[ -z "$name_note" ] || printf '%s\n' "$name_note"
log_info 完了 -- "name=$name" "base=$base"
