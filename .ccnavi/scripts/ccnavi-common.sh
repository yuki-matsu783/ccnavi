# ccnavi-common — 保護済み sh が共有する部分。単体では動かない。
#
#   . "$(dirname "$0")/ccnavi-common.sh"
#
# 呼ぶ側の `set -eu` の直後に置く。`$0` は呼ばれたときの綴りそのままなので、
# `sh .ccnavi/scripts/ccnavi-git.sh` でも `sh ../../../.ccnavi/scripts/ccnavi-git.sh` でも
# 同じディレクトリを指す。**この読み込みにだけ `$0` を使い、ワークスペースルートの
# 決定には使わない**（下の ccnavi_workspace の但し書き）。
#
# ここにあるのは 6 つ。標準出力と終了コードだけを返し、標準エラーには何も書かない。
# 失敗したときの文面は呼ぶ側が決める（reject と fail で綴りが違うため）。
#
#   ccnavi_abs <パス>          相対を絶対に直す
#   ccnavi_workspace           ワークスペースルートの絶対パス
#   ccnavi_bin <ワークスペースルート>  起動する実行ファイルのパス
#   ccnavi_compat_skew <ワークスペースルート> <実行ファイル>  実行ファイルと互換の版が食い違えば直し方を出す
#   ccnavi_project <ディレクトリ>  そこが属するプロジェクトの名前（ワークスペース自身なら空）
#   ccnavi_mask_url <URL>      埋まった資格情報を伏せる
#
# ほかに診断ログの 4 つ（log_debug / log_info / log_warn / log_error）がある。こちらは
# 標準出力にも標準エラーにも何も出さず、`logs/diag/<出どころ>.log` に 1 行足すだけ。
# 決まりは docs/claude/logging.md。

# この sh が頼る実行ファイルの契約の版（互換の版）。実行ファイルの ccnavi/version.py の COMPAT、
# VS Code 拡張の EXTENSION_COMPAT と同じ値に揃える。上げるのは、sh が頼るフラグや出力の形を
# sh を直さないと動かない形に変えたときだけ。`ccnavi --lint` もこの行を読んで比べる。
CCNAVI_COMPAT=1

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
# 正しい答えで、ここで欲しいものとは違う（設計 11.8）。
#
# 目印は `.ccnavi/scripts/ccnavi-common.sh`。自分自身なので、無ければそもそも sh が呼べていない。
# ディレクトリの `.ccnavi/scripts/` だけでは足りない。ccnavi ディレクトリの下には配点が呼ぶスクリプトの
# 置き場として同じ綴りがあり、プロジェクトの中から打つとそのプロジェクトを根と取り違える。
# `.git` は駄目（プロジェクトも持つ）。`.claude/` だけも駄目（Claude Code が作る場合が
# あり、プロジェクト側にできたものに当たる）。
#
# **ワークツリーは飛ばし、最初に当たったものを返す。**
#
# `.ccnavi/scripts/` は git で追跡されているので、どのワークツリーにも写しがある。
# 単純に「最初に当たったもの」にすると、ワークツリーの中から打ったときワークツリー自身が
# 根になる。ところが `logs/state/`（控え）は追跡外でワークツリーには無く、承認済みチケットも
# ワークスペースルートに置かれたばかりのものはワークツリーに届いていないので、どちらも見つからなくなる。道具のうち
# git が運ぶものと運ばないものがあり、根は運ばれないほうに合わせる必要がある。
#
# だから `.claude/worktrees/` の下にあるものは候補にしない。最初に当たった
# 「ワークツリーでない」ディレクトリが根になる。
#
# 最外を取る形にはしない。ワークスペースが利用者のホームの下にあり、そこに
# `~/.ccnavi/scripts/ccnavi-common.sh` が在ると、そちらを掴む。近いほうから決める。
#
# `cd` は使わない。`set -e` の下で戻り忘れが事故になる。パスを削って登る。
ccnavi_workspace() {
	if [ -n "${CCNAVI_WORKSPACE:-}" ]; then
		ccnavi_ws_named=$(ccnavi_abs "$CCNAVI_WORKSPACE") || return 1
		[ -f "$ccnavi_ws_named/.ccnavi/scripts/ccnavi-common.sh" ] || return 1
		printf '%s\n' "$ccnavi_ws_named"
		return 0
	fi
	ccnavi_ws_here=$(ccnavi_abs .) || return 1
	while :; do
		if [ -f "$ccnavi_ws_here/.ccnavi/scripts/ccnavi-common.sh" ]; then
			case "$ccnavi_ws_here" in
			*/.claude/worktrees/*) ;; # ワークツリーの中の写し。根ではない
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

# 起動する実行ファイルのパス。見つからなければ 1 を返す（ソースで動かすかは呼ぶ側が決める）。
#
# 人が端末から打つ場面では settings.json の env が効かないので、CCNAVI_BIN_PATH が
# 無いのが普通。そのときは ccnavi のリポジトリの組み立て（dist/ccnavi/ccnavi）、次に
# hook と同じ振り分けの sh（.ccnavi/scripts/ccnavi-launcher.sh）を見る。振り分けの sh は
# .ccnavi/bin/ があるときだけ選ぶ。無いのに選ぶと、ソースで動かせる ccnavi のリポジトリでも
# 「実行ファイルが無い」で止まる。
#
# それぞれ `.exe` を付けた綴りも見る（Windows の組み立て）。
ccnavi_bin() {
	case "${CCNAVI_BIN_PATH:-}" in
	'')
		ccnavi_bin_try "$1/dist/ccnavi/ccnavi" && return 0
		[ -d "$1/.ccnavi/bin" ] || return 1
		ccnavi_bin_try "$1/.ccnavi/scripts/ccnavi-launcher.sh"
		;;
	/* | [A-Za-z]:*) ccnavi_bin_try "$CCNAVI_BIN_PATH" ;;
	*) ccnavi_bin_try "$1/$CCNAVI_BIN_PATH" ;;
	esac
}

# 実行ファイルの `--version` が言う互換の版と CCNAVI_COMPAT を比べる。揃っていれば何も出さずに 0、
# 食い違えば直し方を含む 1 行を標準出力に出して 1 を返す。呼ぶ側は標準エラーへ書いて先へ進む
# （止めない。止める・通すの判定は実行ファイルと hook が持つ。docs/claude/exe-boundary.md）。
#
# `--version` を知らない古い実行ファイルは、互換の版を答えないので古いとして言う。
# 直し方は、ccnavi のリポジトリ（build.py とソースがある）なら組み立て直し、配布先なら配り直し。
ccnavi_compat_skew() {
	ccnavi_cs_out=$("$2" --version </dev/null 2>/dev/null) || ccnavi_cs_out=""
	ccnavi_cs_have=$(printf '%s\n' "$ccnavi_cs_out" | sed -n 's/^compat:[[:space:]]*\([0-9][0-9]*\)[[:space:]]*$/\1/p' | head -n 1)
	if [ -f "$1/build.py" ] && [ -f "$1/ccnavi/__main__.py" ]; then
		ccnavi_cs_fix="build.py を回して組み立て直してください（uv run --with pyinstaller python build.py）"
	else
		ccnavi_cs_fix="ccnavi のリポジトリで build.py を回し、scripts/ccnavi-setup.sh <このワークスペース> --force で実行ファイルと sh を配り直してください"
	fi
	if [ -z "$ccnavi_cs_have" ]; then
		printf '実行ファイル %s は --version に互換の版を答えません（古い版）。%s。\n' "$2" "$ccnavi_cs_fix"
		return 1
	fi
	[ "$ccnavi_cs_have" = "$CCNAVI_COMPAT" ] && return 0
	printf '実行ファイル %s は互換 %s、sh は互換 %s で食い違っています。%s。\n' "$2" "$ccnavi_cs_have" "$CCNAVI_COMPAT" "$ccnavi_cs_fix"
	return 1
}

ccnavi_bin_try() {
	if [ -x "$1" ]; then
		printf '%s\n' "$1"
	elif [ -x "$1.exe" ]; then
		printf '%s\n' "$1.exe"
	else
		return 1
	fi
}

# そのディレクトリが属するプロジェクトの名前。ワークスペース自身なら空を返す。
#
# 第 2 引数にワークスペースルートを渡す。省くと自分で探す。
#
#   <ws>/projects/<名前>/...          -> <名前>
#   <ws>/.claude/worktrees/<id>/...   -> 元リポジトリがプロジェクトならその名前
#   それ以外                           -> 空
#
# ワークツリーの元リポジトリは `.git` ファイルの `gitdir:` から取る。綴りは実測で確定して
# いる（git 2.39.2、Git Bash と PowerShell の両方）。絶対パス、区切りは `/` のみ、
# ドライブレターは大文字、`gitdir:` の後ろは半角空白 1 個。
#
# **取れなければ空を返す。止めない。** `.git` が読めない、`gitdir:` が無い、
# 元リポジトリが消えている（孤児）のどれでも空。記録の置き場のために作業を止めるのは
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
		# gitdir: <元リポジトリ>/.git/worktrees/<id>
		ccnavi_pj_gitdir=$(sed -n 's/^gitdir:[[:space:]]*//p' "$ccnavi_pj_git" | head -n 1)
		[ -n "$ccnavi_pj_gitdir" ] || return 0
		case "$ccnavi_pj_gitdir" in
		*/.git/worktrees/*) ;;
		*) return 0 ;;
		esac
		ccnavi_pj_owner="${ccnavi_pj_gitdir%/.git/worktrees/*}"
		# 元リポジトリがワークスペースそのものなら、プロジェクトではない。
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

# ---- 診断ログ（docs/claude/logging.md）
#
#   log_info <本文の語>... [-- <キー>=<値>...]
#
# 本文の語はスペースでつなぐ。`--` の後ろは 1 つずつ `キー=値` として logfmt で並べる。
# 出る行（Python の ccnavi/diaglog.py、拡張の src/log.ts と同じ形）:
#
#   2026-09-27T10:15:03+09:00 INFO  ccnavi-git[4242] push を拒否した reason=unapproved
#
# 置き場はワークスペースルートの `logs/diag/<出どころ>.log`。ルートは呼ぶ側が
# ccnavi_log_root に入れておけばそれを使い、空なら ccnavi_workspace で探す。出どころは
# `$0` の名前から拡張子を落としたもので、CCNAVI_LOG_NAME で上書きできる。出どころに
# `[A-Za-z0-9_-]` 以外の字があれば書かない（パスの区切りや `..` を名前に入れさせない）。
#
# **リンクは辿らない。** `logs`・`logs/diag`・書き先のファイルのどれかがシンボリックリンクなら
# 書かずに捨てる。リンクの先へ追記すると、置き場の外のファイル（判定の記録など）を書き換える。
# ファイルを新しく作るときは umask 077 のサブシェルで作り、持ち主だけが読める 0600 にする。
#
# **標準出力と標準エラーには何も出さず、何があっても 0 を返す。** 書けない（置き場が
# 作れない・権限・容量）ときは黙って捨てる。`set -eu` の下で呼んでも、呼ぶ側を止めない。
# 契約の文面（reject / fail の標準エラー、ok / fail の 1 行目）とは別物で、そちらは変えない。
#
# 本文と値の中の、URL と scp 形に埋まった資格情報を `***` に伏せる（ccnavi_log_mask）。
#
# 出さないレベルでは、date を起こさず、文字列も組み立てない（ccnavi_log_on で先に見る）。
# 1 行を書くときに起こす外部コマンドは date と、置き場が無いときの mkdir だけ。ほかに、
# 新しくファイルを作るときの umask のサブシェルと、ccnavi_log_root が空のときに 1 度だけ
# 走る ccnavi_workspace（dirname などを起こす）がある。残りはシェルの展開で済ませる。

ccnavi_log_min=""
ccnavi_log_file=""
ccnavi_log_name=""
# 呼ぶ側が解いたワークスペースルート。入れておくと ccnavi_workspace で探し直さない。
ccnavi_log_root=""
# CR は printf でしか作れない。読み込むときに 1 度だけ作る。
ccnavi_log_cr=$(printf '\r')
# 語の切れ目（空白・タブ・LF・CR）。伏せ字で語を切り出すのに使う。
ccnavi_log_space=" 	
$ccnavi_log_cr"

# レベルの閾値。CCNAVI_LOG_LEVEL を 1 度だけ読む。読めない値と空は INFO。
ccnavi_log_on() {
	if [ -z "$ccnavi_log_min" ]; then
		case "${CCNAVI_LOG_LEVEL:-}" in
		[Dd][Ee][Bb][Uu][Gg]) ccnavi_log_min=1 ;;
		[Ww][Aa][Rr][Nn]) ccnavi_log_min=3 ;;
		[Ee][Rr][Rr][Oo][Rr]) ccnavi_log_min=4 ;;
		*) ccnavi_log_min=2 ;;
		esac
	fi
	[ "$1" -ge "$ccnavi_log_min" ]
}

log_debug() {
	ccnavi_log_on 1 || return 0
	ccnavi_log_emit 'DEBUG' "$@" || :
	return 0
}
log_info() {
	ccnavi_log_on 2 || return 0
	ccnavi_log_emit 'INFO ' "$@" || :
	return 0
}
log_warn() {
	ccnavi_log_on 3 || return 0
	ccnavi_log_emit 'WARN ' "$@" || :
	return 0
}
log_error() {
	ccnavi_log_on 4 || return 0
	ccnavi_log_emit 'ERROR' "$@" || :
	return 0
}

# 書き先。1 度解いたら覚える。出どころが使えない字を含むか、ワークスペースルートが
# 見つからなければ 1 を返す（捨てる）。
ccnavi_log_target() {
	[ -z "$ccnavi_log_file" ] || return 0
	ccnavi_lt_name="${CCNAVI_LOG_NAME:-}"
	if [ -z "$ccnavi_lt_name" ]; then
		ccnavi_lt_name="${0##*/}"
		ccnavi_lt_name="${ccnavi_lt_name##*\\}"
		ccnavi_lt_name="${ccnavi_lt_name%.*}"
	fi
	[ -n "$ccnavi_lt_name" ] || ccnavi_lt_name=sh
	case "$ccnavi_lt_name" in
	*[!A-Za-z0-9_-]*) return 1 ;;
	esac
	ccnavi_lt_ws="$ccnavi_log_root"
	if [ -z "$ccnavi_lt_ws" ]; then
		ccnavi_lt_ws=$(ccnavi_workspace 2>/dev/null) || return 1
	fi
	[ -n "$ccnavi_lt_ws" ] || return 1
	ccnavi_log_name="$ccnavi_lt_name"
	ccnavi_log_file="$ccnavi_lt_ws/logs/diag/$ccnavi_lt_name.log"
}

# 1 行を組み立てて足す。$1 はレベル（5 字）、残りは log_* の引数。
ccnavi_log_emit() {
	ccnavi_le_level="$1"
	shift
	ccnavi_log_target || return 0
	ccnavi_le_msg=""
	ccnavi_le_fields=""
	ccnavi_le_in_fields=""
	for ccnavi_le_arg in ${1+"$@"}; do
		if [ -z "$ccnavi_le_in_fields" ]; then
			if [ "$ccnavi_le_arg" = "--" ]; then
				ccnavi_le_in_fields=1
			elif [ -z "$ccnavi_le_msg" ]; then
				ccnavi_le_msg="$ccnavi_le_arg"
			else
				ccnavi_le_msg="$ccnavi_le_msg $ccnavi_le_arg"
			fi
			continue
		fi
		case "$ccnavi_le_arg" in
		*=*)
			ccnavi_le_key="${ccnavi_le_arg%%=*}"
			ccnavi_le_val="${ccnavi_le_arg#*=}"
			;;
		*)
			ccnavi_le_key="$ccnavi_le_arg"
			ccnavi_le_val=""
			;;
		esac
		ccnavi_log_value "$ccnavi_le_val"
		ccnavi_le_fields="$ccnavi_le_fields $ccnavi_le_key=$ccnavi_log_out"
	done
	ccnavi_log_mask "$ccnavi_le_msg"
	ccnavi_log_fold "$ccnavi_log_out"
	ccnavi_le_msg="$ccnavi_log_out"

	# 時刻。date の %z は +0900 なので、+09:00 に直す。
	ccnavi_le_now=$(date '+%Y-%m-%dT%H:%M:%S%z' 2>/dev/null) || ccnavi_le_now=""
	case "$ccnavi_le_now" in
	*[+-][0-9][0-9][0-9][0-9])
		ccnavi_le_zone="${ccnavi_le_now#"${ccnavi_le_now%?????}"}"
		ccnavi_le_now="${ccnavi_le_now%?????}${ccnavi_le_zone%??}:${ccnavi_le_zone#???}"
		;;
	esac

	ccnavi_le_line="$ccnavi_le_now $ccnavi_le_level ${ccnavi_log_name}[$$] $ccnavi_le_msg$ccnavi_le_fields"
	ccnavi_le_dir="${ccnavi_log_file%/*}"
	# リンクは辿らない。logs・logs/diag・書き先のどれかがリンクなら捨てる。
	if [ -L "${ccnavi_le_dir%/*}" ] || [ -L "$ccnavi_le_dir" ] || [ -L "$ccnavi_log_file" ]; then
		return 0
	fi
	if [ ! -d "$ccnavi_le_dir" ]; then
		mkdir -p "$ccnavi_le_dir" >/dev/null 2>&1 || return 0
	fi
	# `>>` は O_APPEND で開く。printf は 1 行を 1 度の write で出す。
	# 無いファイルは umask 077 のサブシェルで作り、0600 にする。在ればサブシェルを起こさない。
	if [ -e "$ccnavi_log_file" ]; then
		{ printf '%s\n' "$ccnavi_le_line" >>"$ccnavi_log_file"; } >/dev/null 2>&1 || :
	else
		(
			umask 077
			printf '%s\n' "$ccnavi_le_line" >>"$ccnavi_log_file"
		) >/dev/null 2>&1 || :
	fi
	return 0
}

# 文字列の中の $2 を、すべて $3 に置き換える。結果は ccnavi_log_out に入る。
# $2 が空なら何も置き換えない（空を探すと、いつまでも当たり続けて抜けない）。
ccnavi_log_replace() {
	ccnavi_log_out="$1"
	[ -n "$2" ] || return 0
	ccnavi_lr_rest="$1"
	ccnavi_log_out=""
	while :; do
		case "$ccnavi_lr_rest" in
		*"$2"*)
			ccnavi_log_out="$ccnavi_log_out${ccnavi_lr_rest%%"$2"*}$3"
			ccnavi_lr_rest="${ccnavi_lr_rest#*"$2"}"
			;;
		*)
			ccnavi_log_out="$ccnavi_log_out$ccnavi_lr_rest"
			return 0
			;;
		esac
	done
}

# 改行（CR LF・CR・LF）を `\n` の 2 字に畳む。結果は ccnavi_log_out。
ccnavi_log_fold() {
	ccnavi_log_out="$1"
	ccnavi_lf_nl='
'
	case "$ccnavi_log_out" in
	*"$ccnavi_lf_nl"* | *"$ccnavi_log_cr"*) ;;
	*) return 0 ;;
	esac
	ccnavi_log_replace "$ccnavi_log_out" "$ccnavi_log_cr$ccnavi_lf_nl" "$ccnavi_lf_nl"
	ccnavi_log_replace "$ccnavi_log_out" "$ccnavi_log_cr" "$ccnavi_lf_nl"
	ccnavi_log_replace "$ccnavi_log_out" "$ccnavi_lf_nl" '\n'
}

# logfmt の値。資格情報を伏せ、空白・タブ・`"`・`=`・改行を含めば
# `"` で囲んで `\` と `"` を逃がす。結果は ccnavi_log_out。
ccnavi_log_value() {
	ccnavi_log_mask "$1"
	ccnavi_lv_v="$ccnavi_log_out"
	ccnavi_lv_tab='	'
	ccnavi_lv_nl='
'
	case "$ccnavi_lv_v" in
	*' '* | *"$ccnavi_lv_tab"* | *'"'* | *'='* | *"$ccnavi_lv_nl"* | *"$ccnavi_log_cr"*) ;;
	*) return 0 ;;
	esac
	ccnavi_log_replace "$ccnavi_lv_v" '\' '\\'
	ccnavi_log_replace "$ccnavi_log_out" '"' '\"'
	ccnavi_log_fold "$ccnavi_log_out"
	ccnavi_log_out="\"$ccnavi_log_out\""
}

# 埋まった資格情報を `***` に伏せる。Python の diaglog.mask_userinfo と拡張の maskUserinfo と
# 同じ読みで、3 つが 1 字まで同じ結果を出す（tests/sh/test_diaglog_sh.py）。結果は ccnavi_log_out。
#
# 空白・タブ・LF・CR で切った語ごとに見る。
#   - `://` を含む語: `://` の後ろから次の `/` までを authority とし、`@` があれば最後の `@` より
#     前を `***` にする（`https://user:tok@host/x` → `https://***@host/x`）
#   - `://` を含まない語: 最初の `/` より前に `@` があり、最後の `@` より前に `:` があれば、そこを
#     `***` にする（scp 形 `user:tok@host:path` → `***@host:path`）。`git@host:path` は伏せない
# `@` の無い文字列は語に切らずにそのまま返す（ほとんどの行はこれで済む）。
# 利用者向けの ccnavi_mask_url（`<伏せた>@host`）とは綴りが違う。そちらは契約の文面なので変えない。
ccnavi_log_mask() {
	ccnavi_log_out="$1"
	case "$1" in
	*@*) ;;
	*) return 0 ;;
	esac
	ccnavi_lm_rest="$1"
	ccnavi_log_out=""
	while [ -n "$ccnavi_lm_rest" ]; do
		ccnavi_lm_word="${ccnavi_lm_rest%%[$ccnavi_log_space]*}"
		if [ -z "$ccnavi_lm_word" ]; then
			# 頭が切れ目の字（1 バイト）。そのまま写す。
			ccnavi_lm_tail="${ccnavi_lm_rest#?}"
			ccnavi_log_out="$ccnavi_log_out${ccnavi_lm_rest%"$ccnavi_lm_tail"}"
			ccnavi_lm_rest="$ccnavi_lm_tail"
			continue
		fi
		ccnavi_lm_rest="${ccnavi_lm_rest#"$ccnavi_lm_word"}"
		ccnavi_log_mask_word "$ccnavi_lm_word"
		ccnavi_log_out="$ccnavi_log_out$ccnavi_lw_out"
	done
}

# 語 1 つを伏せる（ccnavi_log_mask の読み）。結果は ccnavi_lw_out。
ccnavi_log_mask_word() {
	ccnavi_lw_out="$1"
	case "$1" in
	*@*) ;;
	*) return 0 ;;
	esac
	case "$1" in
	*://*)
		ccnavi_lw_rest="$1"
		ccnavi_lw_out=""
		while :; do
			case "$ccnavi_lw_rest" in
			*://*) ;;
			*) break ;;
			esac
			ccnavi_lw_out="$ccnavi_lw_out${ccnavi_lw_rest%%://*}://"
			ccnavi_lw_rest="${ccnavi_lw_rest#*://}"
			ccnavi_lw_auth="${ccnavi_lw_rest%%/*}"
			case "$ccnavi_lw_auth" in
			*@*)
				ccnavi_lw_out="$ccnavi_lw_out***@${ccnavi_lw_auth##*@}"
				ccnavi_lw_rest="${ccnavi_lw_rest#"$ccnavi_lw_auth"}"
				;;
			esac
		done
		ccnavi_lw_out="$ccnavi_lw_out$ccnavi_lw_rest"
		;;
	*)
		ccnavi_lw_auth="${1%%/*}"
		case "$ccnavi_lw_auth" in
		*@*) ;;
		*) return 0 ;;
		esac
		ccnavi_lw_user="${ccnavi_lw_auth%@*}"
		case "$ccnavi_lw_user" in
		*:*) ccnavi_lw_out="***@${1#"$ccnavi_lw_user"@}" ;;
		esac
		;;
	esac
}
