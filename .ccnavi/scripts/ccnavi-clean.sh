#!/bin/sh
# ccnavi-clean — 作業ツリー 1 本の生成物を消す。`git worktree remove` の前に打つ。
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
# 作業ツリーに未コミットの変更があれば、何も消さずに止める。別のセッションが
# そこで作業している見込みが高い。git に登録の残っていない、消しきれなかったディレクトリ（.git が無い、
# または .git が指す先が消えている）は確かめようが無いので、確かめずに進める。
# 消すのは生成物だけなので、書きかけは残る。
#
# 終了コード: 0 成功 / 1 前提の未充足か消し残し / 2 引数か環境の誤り

set -eu

# 共通部分。ワークスペースルートの探し方はここにある（設計 §25.8）。
. "$(dirname "$0")/ccnavi-common.sh"

usage() {
	cat <<'USAGE'
sh .ccnavi/scripts/ccnavi-clean.sh <名前> [--dry-run]

  <名前>     .claude/worktrees/ の直下の名前。パスは書けない
  --dry-run  消すものを並べるだけで、消さない

消すもの: node_modules / .venv / __pycache__ / .pytest_cache と、package.json の隣の out
未コミットの変更がある作業ツリーでは、何も消さずに止まる
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
		printf 'ccnavi-clean: %s は通しません。使えるのは --dry-run だけです。\n' "$arg" >&2
		exit 2
		;;
	*)
		if [ -n "$name" ]; then
			printf 'ccnavi-clean: 作業ツリーは 1 回に 1 本だけ指定します（%s と %s）。\n' "$name" "$arg" >&2
			exit 2
		fi
		name="$arg"
		;;
	esac
done

[ -n "$name" ] || {
	usage
	exit 2
}

case "$name" in
. | .. | */* | *\\* | *:*)
	printf 'ccnavi-clean: 名前にパスは書けません（%s）。.claude/worktrees/ の直下の名前だけを渡してください。\n' "$name" >&2
	exit 2
	;;
esac

root=$(ccnavi_workspace) || {
	printf 'ccnavi-clean: ワークスペースルートが見つかりません（.ccnavi/scripts/ccnavi-common.sh を持つ親を cwd から上へ探しました）。ワークスペースの中で実行するか、CCNAVI_WORKSPACE にワークスペースルートの絶対パスを渡してください。\n' >&2
	exit 2
}

target="$root/.claude/worktrees/$name"

# 作業ツリーの置き場そのものがリンクなら、消す先が置き場の外にある。たどらない。
if [ -L "$target" ]; then
	printf 'ccnavi-clean: %s はリンクです。リンクの先は消しません。\n' "$target" >&2
	exit 2
fi
[ -d "$target" ] || {
	printf 'ccnavi-clean: %s がありません。\n' "$target" >&2
	exit 2
}

# 未コミットの変更。`.git` が無いときは git に聞かない。聞くと上へ登って
# ワークスペースのリポジトリを答えてしまう。
if [ -e "$target/.git" ]; then
	if changes=$(git -C "$target" status --porcelain 2>/dev/null); then
		if [ -n "$changes" ]; then
			printf 'ccnavi-clean: %s に未コミットの変更があるので、何も消しません。別のセッションが作業している見込みが高いです。\n' "$name" >&2
			printf '%s\n' "$changes" | head -n 10 >&2
			exit 1
		fi
	else
		printf 'ccnavi-clean: %s は git の登録が残っていない、消しきれなかったディレクトリとして扱います（未コミットの変更は確かめていません）。\n' "$name" >&2
	fi
fi

# node が無いときの消し方。ccnavi-clean.js と同じものを探し、同じ文面で報告する。
#
# 探すのは find、消すのは rm -rf。find は既定でリンクをたどらない。rm -rf はリンクを
# 末尾の / 無しで渡せば、リンクそのものだけを消して先は残す。
#
#   clean_with_sh <作業ツリーの絶対パス>
#
# 終了コード: 0 成功 / 1 消し残しか、名前が読めずに止めた
clean_with_sh() {
	cw_top="$1"
	cw_nl='
'
	# find の出力は行で読む。名前に改行があると行の切れ目を取り違え、名前の途中から
	# 始まる行が作業ツリーの外（`../` で始まる綴り）を指しかねない。1 つでもあれば
	# 何も消さない。消す対象の中（node_modules など）は丸ごと消すので見ない。
	cw_odd=$(cd "$cw_top" && {
		find . -name .git -prune \
			-o \( -name node_modules -o -name .venv -o -name __pycache__ -o -name .pytest_cache \) -prune \
			-o -name "*${cw_nl}*" -print 2>/dev/null || :
	}) || {
		printf 'ccnavi-clean: %s に入れません。\n' "$cw_top" >&2
		return 1
	}
	if [ -n "$cw_odd" ]; then
		printf 'ccnavi-clean: 名前に改行を含むものがあるので、sh では何も消しません。node を入れて打ち直してください。\n' >&2
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
		printf 'ccnavi-clean: 消し残しがあります。Windows では、読み込まれている DLL（uv の .venv の .pyd）はどの作業ツリーからも消せません。テストが終わるのを待って打ち直すか、ディレクトリごと mv で .claude/worktrees/ の外へ出してください（HANDOVER.md の「作業ツリーが消せない」）。\n' >&2
		return 1
	fi
	return 0
}

if command -v node >/dev/null 2>&1; then
	exec node "$(dirname "$0")/ccnavi-clean.js" "$target" ${dry:+--dry-run}
fi

printf 'ccnavi-clean: node が見つからないので、sh で消します。\n' >&2
clean_with_sh "$target"
