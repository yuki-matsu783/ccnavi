# ccnavi-common — 保護済み sh 3 本が共有する部分。単体では動かない。
#
#   . "$(dirname "$0")/ccnavi-common.sh"
#
# 呼ぶ側の `set -eu` の直後に置く。`$0` は呼ばれたときの綴りそのままなので、
# `sh .claude/scripts/ccnavi-git.sh` でも `sh ../../scripts/ccnavi-git.sh` でも
# 同じディレクトリを指す。**この読み込みにだけ `$0` を使い、ワークスペースルートの
# 決定には使わない**（下の ccnavi_workspace の但し書き）。
#
# ここにあるのは 4 つ。標準出力と終了コードだけを返し、標準エラーには何も書かない。
# 失敗したときの文面は呼ぶ側が決める（reject と fail で綴りが違うため）。
#
#   ccnavi_abs <パス>          相対を絶対に直す
#   ccnavi_workspace           ワークスペースルートの絶対パス
#   ccnavi_project <ディレクトリ>  そこが属するプロジェクトの名前（ワークスペース自身なら空）
#   ccnavi_mask_url <URL>      埋まった資格情報を伏せる

# 相対パスを絶対に直す。
#
# realpath も readlink -f も使わない。Git Bash・WSL・Linux の 3 つで在ったり
# 無かったり、綴りも揃わない。cd してから pwd を読むのが一番揃う。
# サブシェルの中で cd するので、呼ぶ側の cwd は動かない。
ccnavi_abs() {
	case "$1" in
	'')
		return 1
		;;
	esac
	if [ -d "$1" ]; then
		(cd "$1" 2>/dev/null && pwd -W 2>/dev/null || pwd) || return 1
		return 0
	fi
	# 在るところまで cd して綴りを揃え、残りは文字で継ぐ。
	ccnavi_abs_dir=$(dirname "$1")
	ccnavi_abs_base=$(basename "$1")
	if ccnavi_abs_head=$(cd "$ccnavi_abs_dir" 2>/dev/null && { pwd -W 2>/dev/null || pwd; }); then
		case "$ccnavi_abs_head" in
		*/) printf '%s%s\n' "$ccnavi_abs_head" "$ccnavi_abs_base" ;;
		*) printf '%s/%s\n' "$ccnavi_abs_head" "$ccnavi_abs_base" ;;
		esac
		return 0
	fi
	# 途中のディレクトリがまだ無い（これから作る行き先）。文字だけで組み立てる。
	# `worktree add` の行き先はまさにこれで、在ることを前提にすると検査ができない。
	case "$1" in
	/* | [A-Za-z]:[\\/]*) ccnavi_abs_joined="$1" ;;
	*) ccnavi_abs_joined="$(pwd -W 2>/dev/null || pwd)/$1" ;;
	esac
	# `\` を `/` に寄せ、`.` と `..` を畳む。
	ccnavi_abs_joined=$(printf '%s' "$ccnavi_abs_joined" | tr '\\' '/')
	ccnavi_abs_out=""
	ccnavi_abs_rest="$ccnavi_abs_joined"
	while [ -n "$ccnavi_abs_rest" ]; do
		case "$ccnavi_abs_rest" in
		*/*) ccnavi_abs_part="${ccnavi_abs_rest%%/*}" ;;
		*) ccnavi_abs_part="$ccnavi_abs_rest" ;;
		esac
		case "$ccnavi_abs_rest" in
		*/*) ccnavi_abs_rest="${ccnavi_abs_rest#*/}" ;;
		*) ccnavi_abs_rest="" ;;
		esac
		case "$ccnavi_abs_part" in
		'' | .) continue ;;
		..)
			case "$ccnavi_abs_out" in
			*/*) ccnavi_abs_out="${ccnavi_abs_out%/*}" ;;
			esac
			;;
		*) ccnavi_abs_out="$ccnavi_abs_out/$ccnavi_abs_part" ;;
		esac
	done
	# 先頭に付いた `/` を、元の綴りの頭（ドライブ文字か `/`）に直す。
	case "$ccnavi_abs_joined" in
	[A-Za-z]:/*) printf '%s\n' "${ccnavi_abs_out#/}" ;;
	*) printf '%s\n' "$ccnavi_abs_out" ;;
	esac
}

# ワークスペースルート。道具（hook の登録・実行ファイル・保護済みスクリプト）の置き場。
#
# **git に聞かない。** git のトップは git の用途にだけ使う。モード B では
# `cwd` がプロジェクトの中にあると git はプロジェクトを答える。それは git として
# 正しい答えで、ここで欲しいものとは違う（設計 §25.8）。
#
# 印は `.claude/scripts/`。自分自身の置き場なので、無ければそもそも sh が呼べていない。
# `.git` は駄目（プロジェクトも持つ）。`.claude/` だけも駄目（Claude Code が作る場合が
# あり、プロジェクト側にできたものに当たる）。
#
# **作業ツリーは飛ばし、最初に当たったものを返す。**
#
# `.claude/scripts/` は git で追跡されているので、どの作業ツリーにも写しがある。
# 単純に「最初に当たったもの」にすると、作業ツリーの中から打ったとき作業ツリー自身が
# 根になる。ところが `.claude/ccnavi/tickets/`（承認済みチケット）と `state/` は
# 追跡外で作業ツリーには無いので、チケットも印も見つからなくなる。道具のうち
# git が運ぶものと運ばないものがあり、根は運ばれないほうに合わせる必要がある。
#
# だから `.claude/worktrees/` の下にあるものは候補にしない。最初に当たった
# 「作業ツリーでない」ディレクトリが根になる。
#
# 最外を取る形にはしない。ワークスペースが利用者のホームの下にあり、そこに
# `~/.claude/scripts/` が在ると、そちらを掴む。近いほうから決める。
#
# `cd` は使わない。`set -e` の下で戻り忘れが事故になる。パスを削って登る。
ccnavi_workspace() {
	if [ -n "${CCNAVI_WORKSPACE:-}" ]; then
		ccnavi_ws_named=$(ccnavi_abs "$CCNAVI_WORKSPACE") || return 1
		[ -d "$ccnavi_ws_named/.claude/scripts" ] || return 1
		printf '%s\n' "$ccnavi_ws_named"
		return 0
	fi
	ccnavi_ws_here=$(ccnavi_abs .) || return 1
	while :; do
		if [ -d "$ccnavi_ws_here/.claude/scripts" ]; then
			case "$ccnavi_ws_here" in
			*/.claude/worktrees/*) ;; # 作業ツリーの中の写し。根ではない
			*)
				printf '%s\n' "$ccnavi_ws_here"
				return 0
				;;
			esac
		fi
		ccnavi_ws_up=$(dirname "$ccnavi_ws_here")
		[ "$ccnavi_ws_up" = "$ccnavi_ws_here" ] && return 1
		ccnavi_ws_here="$ccnavi_ws_up"
	done
}

# そのディレクトリが属するプロジェクトの名前。ワークスペース自身なら空を返す。
#
# 第 2 引数にワークスペースルートを渡す。省くと自分で探す。
#
#   <ws>/projects/<名前>/...          -> <名前>
#   <ws>/.claude/worktrees/<id>/...   -> 切り元のプロジェクトの名前
#   それ以外                           -> 空
#
# 作業ツリーの切り元は `.git` ファイルの `gitdir:` から取る。綴りは実測で確定して
# いる（git 2.39.2、Git Bash と PowerShell の両方）。絶対パス、区切りは `/` のみ、
# ドライブレターは大文字、`gitdir:` の後ろは半角空白 1 個。
#
# **取れなければ空を返す。止めない。** `.git` が読めない、`gitdir:` が無い、
# 切り元が消えている（孤児）のどれでも空。記録の置き場のために作業を止めるのは
# 釣り合わない。
ccnavi_project() {
	ccnavi_pj_dir=$(ccnavi_abs "${1:-.}") || return 0
	if [ -n "${2:-}" ]; then
		ccnavi_pj_ws="$2"
	else
		ccnavi_pj_ws=$(ccnavi_workspace) || return 0
	fi
	ccnavi_pj_places="${CCNAVI_PROJECTS:-projects}"

	# ワークスペースの下に無ければ、名乗るプロジェクトは無い。
	case "$ccnavi_pj_dir" in
	"$ccnavi_pj_ws" | "$ccnavi_pj_ws"/*) ;;
	*) return 0 ;;
	esac
	ccnavi_pj_rel="${ccnavi_pj_dir#"$ccnavi_pj_ws"}"
	ccnavi_pj_rel="${ccnavi_pj_rel#/}"

	case "$ccnavi_pj_rel" in
	"$ccnavi_pj_places"/*)
		ccnavi_pj_name="${ccnavi_pj_rel#"$ccnavi_pj_places"/}"
		ccnavi_pj_name="${ccnavi_pj_name%%/*}"
		# 直下に .git を持つものだけがプロジェクト（REQ-MLT-01）。
		if [ -e "$ccnavi_pj_ws/$ccnavi_pj_places/$ccnavi_pj_name/.git" ]; then
			printf '%s\n' "$ccnavi_pj_name"
		fi
		return 0
		;;
	.claude/worktrees/*)
		ccnavi_pj_id="${ccnavi_pj_rel#.claude/worktrees/}"
		ccnavi_pj_id="${ccnavi_pj_id%%/*}"
		ccnavi_pj_git="$ccnavi_pj_ws/.claude/worktrees/$ccnavi_pj_id/.git"
		[ -f "$ccnavi_pj_git" ] || return 0
		# gitdir: <切り元>/.git/worktrees/<id>
		ccnavi_pj_gitdir=$(sed -n 's/^gitdir:[[:space:]]*//p' "$ccnavi_pj_git" | head -n 1)
		[ -n "$ccnavi_pj_gitdir" ] || return 0
		case "$ccnavi_pj_gitdir" in
		*/.git/worktrees/*) ;;
		*) return 0 ;;
		esac
		ccnavi_pj_owner="${ccnavi_pj_gitdir%/.git/worktrees/*}"
		# 切り元がワークスペースそのものなら、プロジェクトではない。
		ccnavi_pj_owner_abs=$(ccnavi_abs "$ccnavi_pj_owner" 2>/dev/null) || return 0
		case "$ccnavi_pj_owner_abs" in
		"$ccnavi_pj_ws") return 0 ;;
		esac
		ccnavi_pj_orel="${ccnavi_pj_owner_abs#"$ccnavi_pj_ws"}"
		ccnavi_pj_orel="${ccnavi_pj_orel#/}"
		case "$ccnavi_pj_orel" in
		"$ccnavi_pj_places"/*)
			ccnavi_pj_name="${ccnavi_pj_orel#"$ccnavi_pj_places"/}"
			case "$ccnavi_pj_name" in
			*/*) return 0 ;; # 2 段以上は数えない（REQ-MLT-01）
			esac
			printf '%s\n' "$ccnavi_pj_name"
			;;
		esac
		return 0
		;;
	esac
	return 0
}

# URL に埋まった資格情報を伏せる。
#
# `user:token@host` の形はよくある。出力にも記録にも残すと、そこから漏れる。
#
# 綴りの要点が 3 つ。
#   - `[^/]*@` で**最後の `@` まで**消す。解析側（authority の ${##*@}）が最後まで
#     見ているので、伏せ字も合わせる。`[^/@]*@` にすると `glpat-A@B` の後半が残る
#   - scheme は大文字も `git+ssh` も拾う
#   - scheme の無い `git@host:path` の形も伏せる
ccnavi_mask_url() {
	printf '%s' "${1:-}" | sed -E \
		-e 's#^([A-Za-z][A-Za-z0-9+.-]*://)[^/]*@#\1<伏せた>@#' \
		-e 's#^[^/:@]*:[^/@]*@#<伏せた>@#'
}
