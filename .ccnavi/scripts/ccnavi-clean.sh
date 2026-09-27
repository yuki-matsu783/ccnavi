#!/bin/sh
# ccnavi-clean — ワークツリー 1 本の生成物を消す。`git worktree remove` の前に打つ。
#
#   sh .ccnavi/scripts/ccnavi-clean.sh <名前>
#   sh .ccnavi/scripts/ccnavi-clean.sh <名前> --dry-run
#
# Windows では、pnpm の node_modules が深すぎる（260 文字を超える）ことと、uv の
# .venv が掴まれていることで、`git worktree remove` が途中で止まり、消しきれなかったディレクトリが残る。
# 先に生成物だけを消しておく。
#
# <名前> は .claude/worktrees/ の直下のディレクトリの名前。パスは受け付けない。
# `rm -rf` の代わりに任意の場所を消す道具にしないためで、ccnavi の recursive-delete の
# 趣旨（消す対象を名指しする）に合わせてある。
#
# 消すのは、作り直せば戻る決まった名前のディレクトリだけ。何を消すかと消し方は
# ccnavi-clean.js にある。node が無ければ、同じものを sh で消す（clean_with_sh）。
# sh の rm は Windows の深い node_modules で粘りきれないことがあり、そのときは
# 消し残しとして 1 で返る。
#
# ワークツリーに未コミットの変更があれば、何も消さずに止める。別のセッションが
# そこで作業している見込みが高い。git に登録の残っていない、消しきれなかったディレクトリ（.git が無い、
# または .git が指す先が消えている）は確かめようが無いので、確かめずに進める。
# 消すのは生成物だけなので、書きかけは残る。
#
# 終了コード: 0 成功 / 1 前提の未充足か消し残し / 2 引数か環境の誤り

set -eu

# 共通部分。ワークスペースルートの探し方はここにある（設計 11.8）。
. "$(dirname "$0")/ccnavi-common.sh"

# fail <識別子> <文面> <終了コード>。文面は標準エラーへ出す契約。識別子は止めた理由の種類を
# 表す短い語で、診断ログ（docs/claude/logging.md）にだけ残す。文面には名前やパスが入るので、
# ログには写さない。
fail() {
	printf 'ccnavi-clean: %s\n' "$2" >&2
	log_info 止めた -- "exit=$3" "reason=$1"
	exit "$3"
}

usage() {
	cat <<'USAGE'
sh .ccnavi/scripts/ccnavi-clean.sh <名前> [--dry-run]

  <名前>     .claude/worktrees/ の直下の名前。パスは書けない
  --dry-run  消すものを並べるだけで、消さない

消すもの: node_modules / .venv / __pycache__ / .pytest_cache と、package.json の隣の out
未コミットの変更があるワークツリーでは、何も消さずに止まる
USAGE
}

name=""
dry=""
for arg in "$@"; do
	case "$arg" in
	-h | --help | help)
		usage
		exit 0
		;;
	--dry-run)
		dry=1
		;;
	-*)
		fail unknown-option "$arg は通しません。使えるのは --dry-run だけです。" 2
		;;
	*)
		if [ -n "$name" ]; then
			fail two-names "ワークツリーは 1 回に 1 本だけ指定します（$name と ${arg}）。" 2
		fi
		name="$arg"
		;;
	esac
done

[ -n "$name" ] || {
	usage
	log_info 止めた -- "exit=2" "reason=missing-name"
	exit 2
}

case "$name" in
. | .. | */* | *\\* | *:*)
	fail path-in-name "名前にパスは書けません（${name}）。.claude/worktrees/ の直下の名前だけを渡してください。" 2
	;;
esac

root=$(ccnavi_workspace) ||
	fail no-workspace "ワークスペースルートが見つかりません（.ccnavi/scripts/ccnavi-common.sh を持つ親を cwd から上へ探しました）。ワークスペースの中で実行するか、CCNAVI_WORKSPACE にワークスペースルートの絶対パスを渡してください。" 2
# 解いたルートを logger に渡し、書くたびに探し直させない。
ccnavi_log_root="$root"
log_info 受け付けた -- "tree=$name" "dry_run=${dry:-0}"

target="$root/.claude/worktrees/$name"

# ワークツリーの置き場そのものがリンクなら、消す先が置き場の外にある。たどらない。
if [ -L "$target" ]; then
	fail linked-tree "$target はリンクです。リンクの先は消しません。" 2
fi
[ -d "$target" ] ||
	fail no-tree "$target がありません。" 2

# 未コミットの変更。`.git` が無いときは git に聞かない。聞くと上へ登って
# ワークスペースのリポジトリを答えてしまう。
if [ -e "$target/.git" ]; then
	if changes=$(git -C "$target" status --porcelain 2>/dev/null); then
		if [ -n "$changes" ]; then
			printf 'ccnavi-clean: %s に未コミットの変更があるので、何も消しません。別のセッションが作業している見込みが高いです。\n' "$name" >&2
			printf '%s\n' "$changes" | head -n 10 >&2
			log_info 止めた -- "exit=1" "reason=uncommitted"
			exit 1
		fi
		log_debug 判定の材料 -- "tree=$name" "git=clean"
	else
		printf 'ccnavi-clean: %s は git の登録が残っていない、消しきれなかったディレクトリとして扱います（未コミットの変更は確かめていません）。\n' "$name" >&2
		log_debug 判定の材料 -- "tree=$name" "git=unregistered"
	fi
else
	log_debug 判定の材料 -- "tree=$name" "git=none"
fi

# node が無いときの消し方。ccnavi-clean.js と同じものを探し、同じ文面で報告する。
#
# 探すのは find、消すのは rm -rf。find は既定でリンクをたどらない。rm -rf はリンクを
# 末尾の / 無しで渡せば、リンクそのものだけを消して先は残す。
#
#   clean_with_sh <ワークツリーの絶対パス>
#
# 終了コード: 0 成功 / 1 消し残しか、名前が読めずに止めた
clean_with_sh() {
	cw_top="$1"
	cw_nl='
'
	# find の出力は行で読む。名前に改行があると行の切れ目を取り違え、名前の途中から
	# 始まる行がワークツリーの外（`../` で始まる綴り）を指しかねない。1 つでもあれば
	# 何も消さない。消す対象の中（node_modules など）は丸ごと消すので見ない。
	cw_odd=$(cd "$cw_top" && {
		find . -name .git -prune \
			-o \( -name node_modules -o -name .venv -o -name __pycache__ -o -name .pytest_cache \) -prune \
			-o -name "*${cw_nl}*" -print 2>/dev/null || :
	}) || {
		printf 'ccnavi-clean: %s に入れません。\n' "$cw_top" >&2
		log_info 止めた -- "reason=cannot-enter"
		return 1
	}
	if [ -n "$cw_odd" ]; then
		printf 'ccnavi-clean: 名前に改行を含むものがあるので、sh では何も消しません。node を入れて打ち直してください。\n' >&2
		log_info 止めた -- "reason=newline-in-name"
		return 1
	fi

	# 決まった名前はそこで降りるのをやめる（-prune）。out は package.json の隣かどうかを
	# 後で見るので、ここでは降りる。読めないディレクトリは探さない（find の文句は捨てる）。
	cw_found=$(cd "$cw_top" && {
		find . -name .git -prune \
			-o \( -name node_modules -o -name .venv -o -name __pycache__ -o -name .pytest_cache \) \
			\( -type d -o -type l \) -print -prune \
			-o -name out \( -type d -o -type l \) -print 2>/dev/null || :
	} | LC_ALL=C sort)

	# 並べ替え済みなので、消す out は必ずその中身より先に来る。消す out の下にあるものは
	# out ごと消えるので、並べない。
	cw_targets=""
	cw_outs=""
	while IFS= read -r cw_rel; do
		[ -n "$cw_rel" ] || continue
		case "$cw_rel" in
		./*) cw_rel="${cw_rel#./}" ;;
		*)
			printf 'ccnavi-clean: find の出力が読めません（%s）。何も消しません。\n' "$cw_rel" >&2
			log_info 止めた -- "reason=unreadable-find"
			return 1
			;;
		esac
		cw_up="$cw_rel"
		cw_inside=""
		while :; do
			case "$cw_up" in
			*/*) cw_up="${cw_up%/*}" ;;
			*) break ;;
			esac
			case "$cw_nl$cw_outs" in
			*"$cw_nl$cw_up$cw_nl"*)
				cw_inside=1
				break
				;;
			esac
		done
		[ -z "$cw_inside" ] || continue
		case "$cw_rel" in
		out | */out)
			case "$cw_rel" in
			*/out) cw_dir="${cw_rel%/out}" ;;
			*) cw_dir=. ;;
			esac
			# JS と同じく、リンクの package.json は数えない。
			if [ ! -f "$cw_top/$cw_dir/package.json" ] || [ -L "$cw_top/$cw_dir/package.json" ]; then
				continue
			fi
			cw_outs="$cw_outs$cw_rel$cw_nl"
			;;
		esac
		cw_targets="$cw_targets$cw_rel$cw_nl"
	done <<EOF
$cw_found
EOF

	if [ -z "$cw_targets" ]; then
		printf '消すものはありません（%s）\n' "$cw_top"
		return 0
	fi

	cw_failed=""
	while IFS= read -r cw_rel; do
		[ -n "$cw_rel" ] || continue
		if [ -n "$dry" ]; then
			printf 'would remove %s\n' "$cw_rel"
			continue
		fi
		# 掴みがすぐ離れることがあるので、3 回まで試す（JS の maxRetries に合わせる）。
		cw_tries=0
		cw_err=""
		while :; do
			cw_err=$(rm -rf "$cw_top/$cw_rel" 2>&1 >/dev/null) || :
			[ -e "$cw_top/$cw_rel" ] || [ -L "$cw_top/$cw_rel" ] || break
			cw_tries=$((cw_tries + 1))
			[ "$cw_tries" -lt 3 ] || break
			sleep 1
		done
		if [ -e "$cw_top/$cw_rel" ] || [ -L "$cw_top/$cw_rel" ]; then
			cw_failed=1
			cw_err=$(printf '%s\n' "$cw_err" | head -n 1)
			printf '消せなかった: %s (%s)\n' "$cw_rel" "${cw_err:-残った}" >&2
		else
			printf 'removed %s\n' "$cw_rel"
		fi
	done <<EOF
$cw_targets
EOF

	if [ -n "$cw_failed" ]; then
		log_info 消し残した -- "reason=leftover"
		printf 'ccnavi-clean: 消し残しがあります。Windows では、読み込まれている DLL（uv の .venv の .pyd）はどのワークツリーからも消せません。テストが終わるのを待って打ち直すか、ディレクトリごと mv で .claude/worktrees/ の外へ出してください（HANDOVER.md の「ワークツリーが消せない」）。\n' >&2
		return 1
	fi
	return 0
}

# exec で置き換えずに子として走らせ、終了コードをそのまま返す。置き換えると最終の結果
# （終了コード）を診断ログに残せない。標準出力・標準エラー・終了コードは同じ。
clean_exit=0
if command -v node >/dev/null 2>&1; then
	log_debug 判定の材料 -- "tree=$name" "via=node"
	node "$(dirname "$0")/ccnavi-clean.js" "$target" ${dry:+--dry-run} || clean_exit=$?
	log_info 終わった -- "tree=$name" "via=node" "exit=$clean_exit"
	exit "$clean_exit"
fi

printf 'ccnavi-clean: node が見つからないので、sh で消します。\n' >&2
# clean_with_sh は `set -e` の効いたまま呼ぶ（`||` を付けると中で -e が外れる）。結果は
# 抜けるときに残す。
trap 'clean_exit=$?; log_info 終わった -- "tree=$name" "via=sh" "exit=$clean_exit"; exit "$clean_exit"' EXIT
clean_with_sh "$target"
