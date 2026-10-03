# ccnavi-common 保護済み sh が共有する部分。単体では動かない。
#
#   . "$(dirname "$0")/ccnavi-common.sh"
#
# 呼ぶ側の `set -eu` の直後に置く。`$0` は呼ばれたときのパスそのままなので、
# `sh .ccnavi/scripts/ccnavi-git.sh` でも `sh ../../../.ccnavi/scripts/ccnavi-git.sh` でも
# 同じディレクトリを指す。**この読み込みにだけ `$0` を使い、ワークスペースルートの
# 決定には使わない**（下の ccnavi_workspace の但し書き）。
#
# ここにあるのは 6 つ。標準出力と終了コードだけを返し、標準エラーには何も書かない。
# 失敗したときの文面は呼ぶ側が決める（reject と fail で書き方が違うため）。
#
#   ccnavi_abs <パス>          相対を絶対に直す
#   ccnavi_workspace           ワークスペースルートの絶対パス
#   ccnavi_bin <ワークスペースルート>  起動する実行ファイルのパス
#   ccnavi_compat_skew <ワークスペースルート> <実行ファイル>  実行ファイルと互換の版が食い違えば直し方を出す
#   ccnavi_project <ディレクトリ>  そこが属するプロジェクトの名前（ワークスペース自身なら空）
#   ccnavi_mask_url <URL>      埋まった資格情報を伏せる
#
# 取り込み状態とロック（ADR-0093 の段階 2b）の関数は、下の「取り込み状態とロック」にまとめてある。
# C1（段階 2d）の関数は、その下の「C1」にまとめてある。
#
# ほかに診断ログの 4 つ（log_debug / log_info / log_warn / log_error）がある。こちらは
# 標準出力にも標準エラーにも何も出さず、`logs/diag/<出どころ>.log` に 1 行足すだけ。
# 決まりは docs/claude/logging.md。

# この sh が頼る実行ファイルの契約の版（互換の版）。実行ファイルの ccnavi/entry/version.py の COMPAT、
# VS Code 拡張の EXTENSION_COMPAT と同じ値に揃える。上げるのは、sh が頼るフラグや出力の形を
# sh を直さないと動かない形に変えたときだけ。`ccnavi --lint` もこの行を読んで比べる。
CCNAVI_COMPAT=3

# 相対パスを絶対に直す。
#
# realpath も readlink -f も使わない。Git Bash・WSL・Linux の 3 つで在ったり
# 無かったり、返すパスの表記も揃わない。cd してから pwd を読むのが一番揃う。
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
	# 在るところまで cd してパスの表記を揃え、残りは文字列としてつなぐ。
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
	# `\` を `/` にそろえ、`.` と `..` を取り除く。
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
	# 先頭に付いた `/` を、元のパスの頭（ドライブ文字か `/`）に直す。
	case "$ccnavi_abs_joined" in
	[A-Za-z]:/*) printf '%s\n' "${ccnavi_abs_out#/}" ;;
	*) printf '%s\n' "$ccnavi_abs_out" ;;
	esac
}

# 実在するディレクトリの、リンクを解いたパス。無ければ受けたパスをそのまま返す。
#
# git の `rev-parse --show-toplevel` はリンクを解いたパスを返すので、ワークスペースルート（cwd から
# 論理のパスで決まる）と比べるときは両辺をこれで揃える。揃えないと、リンクを経た作業場で
# 「.claude/worktrees/ の下か」の比較が外れ、組み込みの保護が当てはまらない。Windows は pwd -W の表記。
ccnavi_phys() {
	[ -n "${1:-}" ] || return 0
	(cd "$1" 2>/dev/null && { pwd -W 2>/dev/null || pwd -P; }) || printf '%s\n' "$1"
}

# ワークスペースルート。hook の登録・実行ファイル・保護済みスクリプトの置き場。
#
# **git に聞かない。** git のトップは git の用途にだけ使う。モード B では
# `cwd` がプロジェクトの中にあると git はプロジェクトを答える。それは git として
# 正しい答えで、ここで欲しいものとは違う（設計 11.8）。
#
# 目印は `.ccnavi/scripts/ccnavi-common.sh`。自分自身なので、無ければそもそも sh が呼べていない。
# ディレクトリの `.ccnavi/scripts/` だけでは足りない。ccnavi ディレクトリの下には配点が呼ぶスクリプトの
# 置き場として同じパスがあり、プロジェクトの中から打つとそのプロジェクトを根と取り違える。
# `.git` は駄目（プロジェクトも持つ）。`.claude/` だけも駄目（Claude Code が作る場合が
# あり、プロジェクト側にできたものに当たる）。
#
# **ワークツリーは候補にせず、最初に当たったものを返す。**
#
# `.ccnavi/scripts/` は git で追跡されているので、どのワークツリーにもコピーがある。
# 単純に「最初に当たったもの」にすると、ワークツリーの中から打ったときワークツリー自身が
# 根になる。ところが `logs/state/`（state の置き場）は追跡外でワークツリーには無く、承認済みチケットも
# ワークスペースルートに置かれたばかりのものはワークツリーに届いていないので、どちらも見つからなくなる。ccnavi が使うもののうち
# git でワークツリーに届くものと届かないものがあり、根は届かないほうに合わせる必要がある。
#
# だから `.claude/worktrees/` の下にあるものは候補にしない。最初に当たった
# 「ワークツリーでない」ディレクトリが根になる。
#
# 最外を取る形にはしない。ワークスペースがユーザのホームの下にあり、そこに
# `~/.ccnavi/scripts/ccnavi-common.sh` が在ると、そちらを選ぶ。近いほうから決める。
#
# `cd` は使わない。`set -e` の下で戻り忘れが問題になる。パスを削って登る。
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
			*/.claude/worktrees/*) ;; # ワークツリーの中のコピー。根ではない
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
# ユーザが端末から打つ場面では settings.json の env が反映されないので、CCNAVI_BIN_PATH が
# 無いのが普通。そのときは ccnavi のリポジトリの組み立て（dist/ccnavi/ccnavi）、次に
# hook と同じ振り分けの sh（.ccnavi/scripts/ccnavi-launcher.sh）を見る。振り分けの sh は
# .ccnavi/bin/ があるときだけ選ぶ。無いのに選ぶと、ソースで動かせる ccnavi のリポジトリでも
# 「実行ファイルが無い」で止まる。
#
# それぞれ `.exe` を付けた名前も見る（Windows の組み立て）。
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
# 食い違えば直し方を含む 1 行を標準出力に出して 1 を返す。呼ぶ側はその行を標準エラーへ書き、止めずに先へ進む。
# 止めるか通すかは実行ファイルと hook が判定する（docs/claude/exe-boundary.md）。
#
# `--version` を知らない古い実行ファイルは互換の版を答えないので、古い版として知らせる。
# 直し方は、ccnavi のリポジトリ（build.py とソースがある）なら組み立て直し、配布先なら配り直し。
ccnavi_compat_skew() {
	ccnavi_cs_out=$("$2" --version </dev/null 2>/dev/null) || ccnavi_cs_out=""
	ccnavi_cs_have=$(printf '%s\n' "$ccnavi_cs_out" | sed -n 's/^compat:[[:space:]]*\([0-9][0-9]*\)[[:space:]]*$/\1/p' | head -n 1)
	if [ -f "$1/build.py" ] && [ -f "$1/ccnavi/__main__.py" ]; then
		ccnavi_cs_fix="build.py を実行して組み立て直してください（uv run --with pyinstaller python build.py）"
	else
		ccnavi_cs_fix="ccnavi のリポジトリで build.py を実行し、scripts/ccnavi-setup.sh <このワークスペース> --force で実行ファイルと sh を配り直してください"
	fi
	if [ -z "$ccnavi_cs_have" ]; then
		printf '実行ファイル %s は --version で互換の版を返しません（古い版です）。%s。\n' "$2" "$ccnavi_cs_fix"
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
# ワークツリーの元リポジトリは `.git` ファイルの `gitdir:` から取る。書式は実際に確かめて確定して
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
	# リンクを解いたパスで揃える（git が返すパスはリンクを解いている）。
	ccnavi_pj_dir=$(ccnavi_phys "$ccnavi_pj_dir")
	ccnavi_pj_ws=$(ccnavi_phys "$ccnavi_pj_ws")

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
		ccnavi_pj_owner_abs=$(ccnavi_phys "$ccnavi_pj_owner")
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
# 書き方の要点が 3 つ。
#   - `[^/]*@` で**最後の `@` まで**消す。解析側（authority の ${##*@}）が最後まで
#     見ているので、伏せる範囲も合わせる。`[^/@]*@` にすると `glpat-A@B` の後半が残る
#   - scheme は大文字も `git+ssh` も拾う
#   - scheme の無い `git@host:path` の形も伏せる
ccnavi_mask_url() {
	printf '%s' "${1:-}" | sed -E \
		-e 's#^([A-Za-z][A-Za-z0-9+.-]*://)[^/]*@#\1<伏せた>@#' \
		-e 's#^[^/:@]*:[^/@]*@#<伏せた>@#'
}

# ---- 取り込み状態とロック（ADR-0093 の 3.6・4.2・4.3。段階 2b）
#
# 取り込み状態は 1 行 1 項目の `<鍵> <値>`（D33）。sh は `sed -n 's/^<鍵> //p'` で読み、jq を使わない。
# 置き場はワークスペースルートの `${CCNAVI_STATE:-logs/state}`（ccnavi-review.sh と同じ読み）。
#
#   sync/<リポジトリ>/families/<P>   親のブランチの取り込み状態（remote branch sha fetched_at state reason）
#   sync/<リポジトリ>/integration/   統合先の取り込み結果（統合先の done/・層・置き場のパスの設定のコピーと head）
#   locks/<リポジトリ>/<P>/          ロック（D32）。中の owner に持ち主を 1 行で書く
#
# <リポジトリ> はワークスペース自身なら `self`、プロジェクトならその名前。

# state の置き場（`logs/state/`）の絶対パス。
ccnavi_state() {
	case "${CCNAVI_STATE:-}" in
	'') printf '%s\n' "$1/logs/state" ;;
	/* | [A-Za-z]:[\\/]*) printf '%s\n' "$CCNAVI_STATE" ;;
	*) printf '%s\n' "$1/$CCNAVI_STATE" ;;
	esac
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

# 親のブランチの取り込み状態のパス。<ワークスペースルート> <リポジトリ> <P>
ccnavi_family_record() {
	printf '%s/sync/%s/families/%s\n' "$(ccnavi_state "$1")" "$2" "$3"
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
ccnavi_record_write() {
	ccnavi_rw_file="$1"
	shift
	mkdir -p "${ccnavi_rw_file%/*}" 2>/dev/null || return 1
	ccnavi_rw_tmp="$ccnavi_rw_file.tmp.$$"
	: >"$ccnavi_rw_tmp" 2>/dev/null || return 1
	while [ "$#" -ge 2 ]; do
		ccnavi_rw_value=$(printf '%s' "$2" | tr '\r\n' '  ')
		printf '%s %s\n' "$1" "$ccnavi_rw_value" >>"$ccnavi_rw_tmp" || {
			rm -f "$ccnavi_rw_tmp"
			return 1
		}
		shift 2
	done
	mv -f "$ccnavi_rw_tmp" "$ccnavi_rw_file" 2>/dev/null || {
		rm -f "$ccnavi_rw_tmp"
		return 1
	}
}

# そのツリーが、その名前の親のブランチのワークツリーか。<ツリー> <名前>
#
# 置き場（承認済みの doing/・done/、提案の todo/・review/）に `ticket: <名前>` の親チケットか提案が
# あれば 0（ADR-0093 の 4.2「SessionStart の早送り」の対象の条件）。子チケット（`parent:` を持つ）は
# 数えない。置き場のパスが絶対パス（リポジトリの外）なら親のブランチとして扱わない（3.1 の 12）。
ccnavi_parent_tree() {
	ccnavi_pt_approved="${CCNAVI_TICKETS_APPROVED:-.ccnavi/approved}"
	ccnavi_pt_proposals="${CCNAVI_TICKETS_PROPOSAL:-wip/proposals}"
	case "$ccnavi_pt_approved$ccnavi_pt_proposals" in
	/* | [A-Za-z]:*) return 1 ;;
	esac
	case "$ccnavi_pt_proposals" in
	/* | [A-Za-z]:*) return 1 ;;
	esac
	ccnavi_pt_approved="${ccnavi_pt_approved%/}"
	ccnavi_pt_proposals="${ccnavi_pt_proposals%/}"
	for ccnavi_pt_file in "$1/$ccnavi_pt_approved/doing/$2.md" "$1/$ccnavi_pt_approved/done/$2.md" \
		"$1/$ccnavi_pt_proposals/todo/$2.md" "$1/$ccnavi_pt_proposals/review/$2.md"; do
		[ -f "$ccnavi_pt_file" ] || continue
		grep -q '^parent:' "$ccnavi_pt_file" 2>/dev/null && continue
		ccnavi_pt_id=$(sed -n 's/^ticket:[[:space:]]*//p' "$ccnavi_pt_file" 2>/dev/null | head -n 1 |
			sed -e 's/[[:space:]]*$//' -e 's/^["'\'']//' -e 's/["'\'']$//')
		[ "$ccnavi_pt_id" = "$2" ] && return 0
	done
	return 1
}

# ロック（D32）。<ワークスペースルート> <リポジトリ> <P> <待つ秒>
#
# 0 取れた（入れ子を含む）/ 1 待っても取れなかった / 2 古いロックを奪いかけて元に戻せなかった（ユーザに回す）。
# 取れたら ccnavi_lock_dir に置き場を入れ、CCNAVI_LOCK_HELD="<リポジトリ>/<P>:<持ち主の情報>" を子に渡す。
# 呼ぶ側は抜けるときに ccnavi_lock_drop を打つ（trap の EXIT・INT・TERM・HUP にも置く）。
#
# - `mkdir` の原子性で取る。`flock` は macOS に無い
# - owner は `<ホスト名> <pid> <開始時刻（date +%s）> <持ち主の情報> <OS>`、持ち主の情報は `<pid>-<開始時刻>`。
#   書けなかった・書いた中身が読み返せないときは取れていないとして手放す
# - 古い（段階 2d のレビューの決定 B）: ホスト名と OS（`uname -s`）が同じで、置き場が /mnt/ の下で
#   なければ pid で見る。`kill -0` が落ちれば古く、持ち主が生きていれば 10 分を過ぎても奪わない
#   （長い操作を奪って二重に書かせない。待ちで取れなければ「長い」と言って落とす）。pid を確かめ
#   られない（別のホスト・別の OS・/mnt/ の下・pid が読めない）ときだけ、10 分を過ぎたら時刻で古い
#   とする（WSL と Git Bash は同じホスト名で pid が通じない）。owner が読めなければ `find -mmin +10`
# - 奪い方: 奪う操作を `<ロック>.steal`（`mkdir`、10 分で古い）で 1 つにし、古いと判断したときに読んだ
#   owner の行と今の owner の行が同じなら `mv` で退避して、退避した中の owner がまだ同じなら消して取り直す。
#   違えば（その間に持ち主が替わった）、元の名前が空いていれば戻して待ちに戻り、空いていなければ 2
# - 入れ子: CCNAVI_LOCK_HELD が同じ親のブランチを指し、その識別子がロックの owner の識別子と同じなら、取ったものとして
#   進み、外さない（識別子の合わない値は偽物として無視する）
ccnavi_lock_dir=""
ccnavi_lock_mark=""
ccnavi_lock_set_held=""
# 取る前の CCNAVI_LOCK_HELD（入れ子で別のロックを取ったとき、外すときに元へ戻す。段階 2d）。
ccnavi_lock_prev_held=""
ccnavi_lock_prev_set=""
ccnavi_lock_os() {
	uname -s 2>/dev/null | tr ' ' '_' || echo unknown
}
ccnavi_lock_take() {
	ccnavi_lk_key="$2/$3"
	ccnavi_lk_dir="$(ccnavi_state "$1")/locks/$2/$3"
	case "${CCNAVI_LOCK_HELD:-}" in
	"$ccnavi_lk_key":*)
		ccnavi_lk_held="${CCNAVI_LOCK_HELD#"$ccnavi_lk_key":}"
		ccnavi_lk_line=$(ccnavi_lock_owner "$ccnavi_lk_dir")
		ccnavi_lk_fields=$(printf '%s\n' "$ccnavi_lk_line" | awk '{ print $4 }')
		if [ -n "$ccnavi_lk_held" ] && [ "$ccnavi_lk_fields" = "$ccnavi_lk_held" ]; then
			return 0
		fi
		log_warn 入れ子の持ち主の情報がロックの持ち主と合わない -- "lock=$ccnavi_lk_key"
		;;
	esac
	mkdir -p "${ccnavi_lk_dir%/*}" 2>/dev/null || return 1
	ccnavi_lk_host=$(hostname 2>/dev/null || uname -n 2>/dev/null || echo unknown)
	ccnavi_lk_os=$(ccnavi_lock_os)
	ccnavi_lk_start=$(date +%s)
	while :; do
		if mkdir "$ccnavi_lk_dir" 2>/dev/null; then
			# 作った直後に覚えておく。owner を書く前に切られても（INT・TERM）、trap の drop が外せるように。
			ccnavi_lock_dir="$ccnavi_lk_dir"
			ccnavi_lock_mark=""
			ccnavi_lk_now=$(date +%s)
			ccnavi_lk_mark="$$-$ccnavi_lk_now"
			ccnavi_lk_want="$ccnavi_lk_host $$ $ccnavi_lk_now $ccnavi_lk_mark $ccnavi_lk_os"
			# 書く前に持ち主の情報を覚えておく（書いた直後に切られても、drop が自分の owner と分かるように）。
			ccnavi_lock_mark="$ccnavi_lk_mark"
			if printf '%s\n' "$ccnavi_lk_want" >"$ccnavi_lk_dir/owner" 2>/dev/null &&
				[ "$(ccnavi_lock_owner "$ccnavi_lk_dir")" = "$ccnavi_lk_want" ]; then
				ccnavi_lock_dir="$ccnavi_lk_dir"
				ccnavi_lock_mark="$ccnavi_lk_mark"
				if [ -n "${CCNAVI_LOCK_HELD+x}" ]; then
					ccnavi_lock_prev_held="$CCNAVI_LOCK_HELD"
					ccnavi_lock_prev_set=yes
				else
					ccnavi_lock_prev_held=""
					ccnavi_lock_prev_set=""
				fi
				CCNAVI_LOCK_HELD="$ccnavi_lk_key:$ccnavi_lk_mark"
				export CCNAVI_LOCK_HELD
				ccnavi_lock_set_held=yes
				log_debug ロックを取った -- "lock=$ccnavi_lk_key"
				return 0
			fi
			# 取った直後に奪われた（owner を書けない・別の中身）。取れていないとして待ちに戻る。
			ccnavi_lock_dir=""
			ccnavi_lock_mark=""
			log_warn 取ったロックの持ち主を書けなかった -- "lock=$ccnavi_lk_key"
		else
			ccnavi_lk_seen=$(ccnavi_lock_owner "$ccnavi_lk_dir")
			if ccnavi_lock_stale "$ccnavi_lk_dir" "$ccnavi_lk_host" "$ccnavi_lk_os" "$ccnavi_lk_seen"; then
				ccnavi_lk_rc=0
				ccnavi_lock_steal "$ccnavi_lk_dir" "$ccnavi_lk_seen" || ccnavi_lk_rc=$?
				case "$ccnavi_lk_rc" in
				0) continue ;;
				2) return 2 ;;
				esac
			fi
		fi
		ccnavi_lk_now=$(date +%s)
		[ "$((ccnavi_lk_now - ccnavi_lk_start))" -lt "$4" ] || return 1
		sleep 1
	done
}

# ロックの持ち主（owner の 1 行）。読めなければ空。
ccnavi_lock_owner() {
	head -n 1 "$1/owner" 2>/dev/null || :
}

# 10 進の数に揃える（先頭の 0 を落とす。`$(( ))` が 8 進に読まないように）。数でなければ空。
ccnavi_lock_num() {
	case "${1:-}" in
	'' | *[!0-9]*) return 0 ;;
	esac
	ccnavi_ln_v="${1#"${1%%[!0]*}"}"
	printf '%s\n' "${ccnavi_ln_v:-0}"
}

# そのロックが古いか。<ロック> <自分のホスト名> <自分の OS> <読んだ owner の行>
ccnavi_lock_stale() {
	if [ -z "$4" ]; then
		# 取った直後で owner がまだ無いこともある。時刻で見る。
		[ -n "$(find "$1" -maxdepth 0 -mmin +10 2>/dev/null)" ]
		return
	fi
	set -f
	# shellcheck disable=SC2086
	set -- "$1" "$2" "$3" $4
	set +f
	# $4 ホスト名 $5 pid $6 開始時刻 $7 持ち主の情報 $8 OS
	ccnavi_ls_started=$(ccnavi_lock_num "${6:-}")
	if [ -z "$ccnavi_ls_started" ]; then
		[ -n "$(find "$1" -maxdepth 0 -mmin +10 2>/dev/null)" ]
		return
	fi
	ccnavi_ls_now=$(date +%s)
	ccnavi_ls_old=no
	[ "$((ccnavi_ls_now - ccnavi_ls_started))" -gt 600 ] && ccnavi_ls_old=yes
	# 同じ機械の同じ OS のときだけ pid を見る。/mnt/ の下（WSL から Windows の置き場）は時刻だけ。
	ccnavi_ls_pid=$(ccnavi_lock_num "${5:-}")
	ccnavi_ls_local=yes
	case "$1" in
	/mnt/*) ccnavi_ls_local=no ;;
	esac
	{ [ "${4:-}" = "$2" ] && [ "${8:-}" = "$3" ] && [ -n "$ccnavi_ls_pid" ]; } || ccnavi_ls_local=no
	if [ "$ccnavi_ls_local" = yes ]; then
		# 持ち主が生きていれば、時刻に関わらず古くない。
		kill -0 "$ccnavi_ls_pid" 2>/dev/null && return 1
		return 0
	fi
	[ "$ccnavi_ls_old" = yes ]
}

# そのロックが 10 分を超えて持たれているか（持ち主が生きていて奪わないときの文面に使う）。<ロック>
ccnavi_lock_long() {
	ccnavi_ll_started=$(ccnavi_lock_num "$(ccnavi_lock_owner "$1" | awk '{ print $3 }')")
	[ -n "$ccnavi_ll_started" ] || return 1
	[ "$(($(date +%s) - ccnavi_ll_started))" -gt 600 ]
}

# 長いロックの持ち主と止め方を 1 文で出す（文面だけ。判定には使わない）。<ロック>
# 開始時刻は GNU の `date -d @N` か BSD の `date -r N` で読める形にし、落ちれば数のまま。
ccnavi_lock_describe() {
	ccnavi_lds_line=$(ccnavi_lock_owner "$1")
	ccnavi_lds_host=$(printf '%s\n' "$ccnavi_lds_line" | awk '{ print $1 }')
	ccnavi_lds_pid=$(ccnavi_lock_num "$(printf '%s\n' "$ccnavi_lds_line" | awk '{ print $2 }')")
	ccnavi_lds_started=$(ccnavi_lock_num "$(printf '%s\n' "$ccnavi_lds_line" | awk '{ print $3 }')")
	ccnavi_lds_at=""
	if [ -n "$ccnavi_lds_started" ]; then
		# BSD の `date -d` は別の意味なので、GNU（`--version` が通る）のときだけ `-d` を使う。
		if date --version >/dev/null 2>&1; then
			ccnavi_lds_at=$(date -d "@$ccnavi_lds_started" '+%Y-%m-%d %H:%M:%S' 2>/dev/null) || ccnavi_lds_at=""
		else
			ccnavi_lds_at=$(date -r "$ccnavi_lds_started" '+%Y-%m-%d %H:%M:%S' 2>/dev/null) || ccnavi_lds_at=""
		fi
		[ -n "$ccnavi_lds_at" ] || ccnavi_lds_at="$ccnavi_lds_started"
	fi
	printf '持ち主は pid %s・ホスト %s・開始 %s。そのプロセスが固まっているなら、ユーザに終了させてもらってから打ち直してください（持ち主のプロセスが終われば、ロックは次の実行が片付ける）\n' \
		"${ccnavi_lds_pid:-?}" "${ccnavi_lds_host:-?}" "${ccnavi_lds_at:-?}"
}

# 古いロックを奪う。<ロック> <古いと判断したときに読んだ owner の行>
# 0 奪えた（取り直す）/ 1 奪わなかった（待ちに戻る）/ 2 退けたものを戻せなかった
ccnavi_lock_steal() {
	ccnavi_st_gate="$1.steal"
	if ! mkdir "$ccnavi_st_gate" 2>/dev/null; then
		# 別の誰かが奪っている最中。10 分を過ぎた門は落ちた奪い手の残りなので外す。
		if [ -n "$(find "$ccnavi_st_gate" -maxdepth 0 -mmin +10 2>/dev/null)" ]; then
			rmdir "$ccnavi_st_gate" 2>/dev/null || :
		fi
		return 1
	fi
	ccnavi_st_rc=1
	ccnavi_st_aside="$1.stale.$$"
	ccnavi_st_now=$(ccnavi_lock_owner "$1")
	if [ "$ccnavi_st_now" = "$2" ] && { [ -n "$2" ] || [ -n "$(find "$1" -maxdepth 0 -mmin +10 2>/dev/null)" ]; } &&
		mv "$1" "$ccnavi_st_aside" 2>/dev/null; then
		if [ "$(ccnavi_lock_owner "$ccnavi_st_aside")" = "$2" ]; then
			rm -rf "$ccnavi_st_aside" 2>/dev/null || :
			log_info 古いロックを奪った -- "lock=$1"
			ccnavi_st_rc=0
		elif [ ! -e "$1" ] && mv "$ccnavi_st_aside" "$1" 2>/dev/null; then
			ccnavi_st_rc=1
		else
			log_warn 奪いかけたロックを戻せなかった -- "lock=$1"
			ccnavi_st_rc=2
		fi
	fi
	rmdir "$ccnavi_st_gate" 2>/dev/null || :
	return "$ccnavi_st_rc"
}

# 自分が取ったロックを外す。入れ子で取ったもの（ccnavi_lock_dir が空）は外さない。
# owner の持ち主の情報が自分のものでなければ（奪われた後）触らない。自分が渡した CCNAVI_LOCK_HELD も消す。
ccnavi_lock_drop() {
	if [ -n "$ccnavi_lock_dir" ]; then
		ccnavi_ld_line=$(ccnavi_lock_owner "$ccnavi_lock_dir")
		ccnavi_ld_mark=$(printf '%s\n' "$ccnavi_ld_line" | awk '{ print $4 }')
		if [ -n "$ccnavi_lock_mark" ] && [ "$ccnavi_ld_mark" = "$ccnavi_lock_mark" ]; then
			rm -rf "$ccnavi_lock_dir" 2>/dev/null || :
		elif [ -z "$ccnavi_ld_line" ]; then
			# 作ったが owner を書く前に切られた。中身が空のままなら自分の作ったものなので外す。
			rmdir "$ccnavi_lock_dir" 2>/dev/null || rm -f "$ccnavi_lock_dir/owner" 2>/dev/null || :
			rmdir "$ccnavi_lock_dir" 2>/dev/null || :
		fi
		ccnavi_lock_dir=""
	fi
	if [ -n "$ccnavi_lock_set_held" ]; then
		# 入れ子の中で別のロック（統合先の取り込み結果のロックなど）を取ったときは、外側から渡された値に戻す。
		# 消したままにすると、外側（C1）が持つ親のブランチのロックを、この sh の後の段が入れ子と読めない。
		if [ -n "$ccnavi_lock_prev_set" ]; then
			CCNAVI_LOCK_HELD="$ccnavi_lock_prev_held"
			export CCNAVI_LOCK_HELD
		else
			unset CCNAVI_LOCK_HELD
		fi
		ccnavi_lock_set_held=""
		ccnavi_lock_prev_held=""
		ccnavi_lock_prev_set=""
	fi
	return 0
}

# ---- 見張りつきの git（取ってくる操作。ccnavi-fetch.sh と ccnavi-sync.sh が使う）
#
#   ccnavi_git_timed <秒> <標準エラーの書き先> <リポジトリ> <git の引数>...
#
# 認証を尋ねさせず（GIT_TERMINAL_PROMPT=0・GCM_INTERACTIVE=never、ssh は BatchMode）、<秒> で切る。
# ssh の BatchMode は、ユーザが GIT_SSH_COMMAND・GIT_SSH・core.sshCommand を持っていればそちらを尊重する。
# 見張りの出力は捨てる（つないだままだと、見張りの sleep が終わるまで呼ぶ側の `$( )` が閉じない）。
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
	# 見張りの中で標準入出力を先に閉じる（呼ぶ側のパイプを開いたまま残らないように）。
	(
		exec </dev/null >/dev/null 2>&1
		sleep "$ccnavi_gt_limit"
		kill "$ccnavi_gt_pid"
	) &
	ccnavi_gt_dog=$!
	ccnavi_gt_rc=0
	wait "$ccnavi_gt_pid" 2>/dev/null || ccnavi_gt_rc=$?
	kill "$ccnavi_gt_dog" 2>/dev/null || :
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
		printf '書きかけの %s と重なる。コミットか退避をしてから打ち直してください' "$ccnavi_gr_paths"
	elif grep -q 'index\.lock' "$1" 2>/dev/null; then
		printf '索引のロック（index.lock）が残っている。別の git が動いていないか確かめ、落ちた残りならユーザが消す'
	elif grep -qi 'tell me who you are\|empty ident\|user\.email\|user\.name' "$1" 2>/dev/null; then
		printf 'コミットするユーザの名前（user.name・user.email）が決まっていない'
	elif grep -qi 'gpg\|signing' "$1" 2>/dev/null; then
		printf 'コミットの署名に失敗した'
	elif grep -qi 'hook' "$1" 2>/dev/null; then
		printf 'git の hook が止めた（%s）' "$(head -n 1 "$1")"
	else
		printf 'git が拒んだ（%s）' "$(head -n 1 "$1" 2>/dev/null)"
	fi
}

# ---- C1（ADR-0093 の 4.3・4.4。段階 2d）
#
# 取り込み済みの親のブランチについて、チケットの状態を書く一連の操作を 1 つの操作にまとめる。親のブランチ 1 本に、
# 親チケットと子チケットのまとまりが 1 つ対応する。ここで対象にするのは、origin があり取り込み状態が present の
# 親のブランチのうち、chat だけの親のブランチを除いたもの（D11）。呼ぶ側（ccnavi-ticket.sh・ccnavi-review.sh）は次の順に関数を呼ぶ。
#
#   ccnavi_c1_family <識別子>   親のブランチと、C1 の対象か（ccnavi_c1_target に yes / no / stop）
#   ccnavi_c1_begin             1 ロック 2 途中の操作 3 C1 の外の変更の見分けとコミット 4 取り込み 5 未送信の確かめ
#   ccnavi_c1_write <文> -- <実行ファイルの引数>...
#                               6 元の先頭 7 書く 8 コミット 9 push 10 届いたか 11 戻して 1 回だけやり直す
#   ccnavi_c1_end               ロックを外す（trap の EXIT・INT・TERM・HUP にも置く）
#
# 呼ぶ側が先に決めるもの。
#   ccnavi_c1_root   ワークスペースルート
#   ccnavi_c1_label  文面の頭（ccnavi-ticket など）
#   ccnavi_c1_exe    関数。実行ファイルを `--root <ルート>` つきで起こし、引数を渡す
#
# 文面はすべて標準エラーへ出す（ボードは標準出力の JSON を読むため）。実行ファイルの標準出力は
# そのまま通す。ccnavi_c1_capture に書き先を入れると、そこへ書く。
#
# 実行ファイルはネットワークに出ずコミットもしない（D17）。見分けと書いたパスの一覧は実行ファイルが
# 出し（`c1 family`・`c1 sort`・`--record-writes`）、取り込み（ccnavi-sync.sh を入れ子のロックで起こす）・
# コミット（`commit --only`。D35）・push・届いたかの確かめ・戻し（比較つきの update-ref。reset は
# 使わない）はここが持つ。git はエージェントの入口の ccnavi-git.sh を通らずに直に呼ぶ（4.3 の戻し）。
#
# 環境変数: CCNAVI_LOCK_WAIT（ロックを待つ秒、既定 120）/ CCNAVI_C1_TIMEOUT（push・ls-remote 1 回の
#   見張りの秒、既定 60）/ CCNAVI_C1_COMMIT_TIMEOUT（コミット 1 回の見張りの秒、既定 60）
#
# コミットは `--no-verify` でユーザの hook（pre-commit・commit-msg）を実行しない。コミットするのは状態のファイル
# だけで、コードの検査の対象ではないため（段階 2d のレビューの決定 C）。署名はユーザの設定に従うが、
# 見張りの時間を付け、pinentry などが尋ねて止まりっぱなしにならないようにする（切れたら失敗）。
#
# 途中で INT・TERM・HUP が来たら、送る前の自分のコミットを戻す（ccnavi_c1_end）。強制終了（KILL）で
# 残ったコミットは、次の C1 の 5 が「未送信」で止まり、戻し方を言う。

ccnavi_c1_target=""
ccnavi_c1_why=""
ccnavi_c1_family_id=""
ccnavi_c1_repo=""
ccnavi_c1_tree=""
ccnavi_c1_approved=""
ccnavi_c1_review=""
ccnavi_c1_tmp=""
ccnavi_c1_capture=""
ccnavi_c1_keep=""
ccnavi_c1_tab=$(printf '\t')

ccnavi_c1_say() {
	printf '%s: %s\n' "$ccnavi_c1_label" "$*" >&2
}

ccnavi_c1_scratch() {
	[ -z "$ccnavi_c1_tmp" ] || return 0
	ccnavi_c1_tmp=$(mktemp -d 2>/dev/null || mktemp -d -t ccnavi-c1) || return 1
}

ccnavi_c1_number() {
	case "${1:-}" in
	'' | *[!0-9]*) printf '%s\n' "$2" ;;
	*) printf '%s\n' "$1" ;;
	esac
}

# 親のブランチと、C1 の対象か。<識別子>
#
# ccnavi_c1_target: yes（C1 で回す）/ no（今の手元の動きのまま）/ stop（取り込み済みだが止める理由がある）。
# 実行ファイルが答えなかった（古い・落ちた）ときは、取り込み状態があれば stop、無ければ no。
ccnavi_c1_family() {
	ccnavi_c1_target=no
	ccnavi_c1_why=""
	ccnavi_c1_family_id=""
	ccnavi_c1_repo=""
	ccnavi_c1_tree=""
	# 取り込み状態が 1 つも無ければ、実行ファイルに聞かずに対象外（D11。一時ディレクトリも要らない）。
	ccnavi_cf_state=$(ccnavi_state "$ccnavi_c1_root")
	ccnavi_cf_p="$1"
	case "$ccnavi_cf_p" in
	*-[0-9][0-9]) ccnavi_cf_p="${ccnavi_cf_p%-[0-9][0-9]}" ;;
	esac
	ccnavi_cf_any=no
	for ccnavi_cf_rec in "$ccnavi_cf_state"/sync/*/families/"$ccnavi_cf_p"; do
		if [ -e "$ccnavi_cf_rec" ] || [ -L "$ccnavi_cf_rec" ]; then
			ccnavi_cf_any=yes
			break
		fi
	done
	if [ "$ccnavi_cf_any" = no ]; then
		ccnavi_c1_family_id="$ccnavi_cf_p"
		ccnavi_c1_why="親のブランチの取り込み状態が無い（取り込み済みでない。今の手元の動きのまま）"
		return 0
	fi
	ccnavi_c1_scratch || {
		ccnavi_c1_target=stop
		ccnavi_c1_why="一時ディレクトリが作れない"
		return 0
	}
	: >"$ccnavi_c1_tmp/hints"
	# Windows の実行ファイルの CRLF を落として読む（1 行 1 項目の値の末尾に CR を残さない）。
	ccnavi_c1_exe c1 family "$1" 2>"$ccnavi_c1_tmp/family-err" </dev/null | tr -d '\r' >"$ccnavi_c1_tmp/family" || :
	if [ "$(head -n 1 "$ccnavi_c1_tmp/family" 2>/dev/null)" != "c1 1" ]; then
		ccnavi_c1_family_id="$ccnavi_cf_p"
		ccnavi_c1_target=stop
		ccnavi_c1_why="実行ファイルが C1 の問い合わせ（c1 family）に応答しない（$(head -n 1 "$ccnavi_c1_tmp/family-err" 2>/dev/null)）。親のブランチの取り込み状態があるので、書かずに止める。実行ファイルを新しくしてください"
		return 0
	fi
	ccnavi_c1_target=$(sed -n 's/^target //p' "$ccnavi_c1_tmp/family" | head -n 1)
	ccnavi_c1_why=$(sed -n 's/^why //p' "$ccnavi_c1_tmp/family" | head -n 1)
	ccnavi_c1_family_id=$(sed -n 's/^family //p' "$ccnavi_c1_tmp/family" | head -n 1)
	ccnavi_c1_repo=$(sed -n 's/^repo //p' "$ccnavi_c1_tmp/family" | head -n 1)
	ccnavi_c1_tree=$(sed -n 's/^tree //p' "$ccnavi_c1_tmp/family" | head -n 1)
	ccnavi_c1_approved=$(sed -n 's/^approved //p' "$ccnavi_c1_tmp/family" | head -n 1)
	ccnavi_c1_review=$(sed -n 's/^review //p' "$ccnavi_c1_tmp/family" | head -n 1)
	sed -n 's/^hint //p' "$ccnavi_c1_tmp/family" >"$ccnavi_c1_tmp/hints"
	case "$ccnavi_c1_target" in
	yes)
		if [ -z "$ccnavi_c1_tree" ] || [ ! -d "$ccnavi_c1_tree" ]; then
			ccnavi_c1_target=stop
			ccnavi_c1_why="親のワークツリーが決まらない"
		elif ! git -C "$ccnavi_c1_tree" remote get-url origin >/dev/null 2>&1; then
			ccnavi_c1_target=no
			ccnavi_c1_why="origin が無い（今の手元の動きのまま）"
		fi
		;;
	no | stop) ;;
	*)
		ccnavi_c1_target=stop
		ccnavi_c1_why="実行ファイルの出力（target）を読めない"
		;;
	esac
	log_debug C1 の対象を決めた -- "family=$ccnavi_c1_family_id" "target=$ccnavi_c1_target"
	return 0
}

# 止めたときの文面（ccnavi_c1_target が stop）。
ccnavi_c1_refuse() {
	ccnavi_c1_say "親のブランチ ${ccnavi_c1_family_id:-?} のチケットの状態を書かずに止めた。${ccnavi_c1_why}"
	while IFS= read -r ccnavi_rf_hint; do
		[ -n "$ccnavi_rf_hint" ] && printf '  %s\n' "$ccnavi_rf_hint" >&2
	done <"$ccnavi_c1_tmp/hints"
	log_info C1 で止めた -- "family=$ccnavi_c1_family_id" "reason=c1-stop"
}

# `git rev-parse --git-path <名前>` をツリーからのパスにする。<ツリー> <名前>
ccnavi_c1_git_path() {
	ccnavi_gp_rel=$(git -C "$1" rev-parse --git-path "$2" 2>/dev/null || :)
	case "$ccnavi_gp_rel" in
	'') printf '%s\n' "$1/.git/$2" ;;
	/* | [A-Za-z]:*) printf '%s\n' "$ccnavi_gp_rel" ;;
	*) printf '%s\n' "$1/$ccnavi_gp_rel" ;;
	esac
}

# 途中の操作の名前（無ければ空）。<ツリー>
ccnavi_c1_busy() {
	for ccnavi_cb_name in MERGE_HEAD CHERRY_PICK_HEAD REVERT_HEAD rebase-merge rebase-apply sequencer index.lock; do
		if [ -e "$(ccnavi_c1_git_path "$1" "$ccnavi_cb_name")" ]; then
			printf '%s\n' "$ccnavi_cb_name"
			return 0
		fi
	done
	return 0
}

# 1〜5。0 なら書いてよい（ロックを持ったまま返る）。1 なら止めた（ロックは外した）。
ccnavi_c1_begin() {
	ccnavi_cb_wait=$(ccnavi_c1_number "${CCNAVI_LOCK_WAIT:-}" 120)
	ccnavi_cb_rc=0
	ccnavi_lock_take "$ccnavi_c1_root" "$ccnavi_c1_repo" "$ccnavi_c1_family_id" "$ccnavi_cb_wait" || ccnavi_cb_rc=$?
	case "$ccnavi_cb_rc" in
	0) ;;
	2)
		ccnavi_c1_say "親のブランチ $ccnavi_c1_family_id の古いロックを奪う途中で止まり、元に戻せなかった。ユーザが中身を見て片付ける（$(ccnavi_state "$ccnavi_c1_root")/locks/$ccnavi_c1_repo/${ccnavi_c1_family_id}）"
		return 1
		;;
	*)
		ccnavi_cb_lock="$(ccnavi_state "$ccnavi_c1_root")/locks/$ccnavi_c1_repo/$ccnavi_c1_family_id"
		if ccnavi_lock_long "$ccnavi_cb_lock"; then
			ccnavi_c1_say "親のブランチ $ccnavi_c1_family_id のロックが 10 分を超えて取られたままになっている。持ち主はまだ動いているので奪わない。終わるのを待つか、持ち主をユーザが確かめてください。$(ccnavi_lock_describe "$ccnavi_cb_lock")"
		else
			ccnavi_c1_say "親のブランチ $ccnavi_c1_family_id のロックを他の操作が持っている（$(ccnavi_lock_owner "$ccnavi_cb_lock")）。終わってから打ち直してください"
		fi
		return 1
		;;
	esac
	ccnavi_cb_busy=$(ccnavi_c1_busy "$ccnavi_c1_tree")
	if [ -n "$ccnavi_cb_busy" ]; then
		ccnavi_c1_say "親のワークツリーに途中の操作（${ccnavi_cb_busy}）がある。済ませるか取りやめてから打ち直してください（何も書いていない）"
		ccnavi_lock_drop
		return 1
	fi
	if ! ccnavi_c1_prepare; then
		ccnavi_lock_drop
		return 1
	fi
	return 0
}

# 置き場の変更を見分ける。<出力> [<版>]。答えられなければ 1。
ccnavi_c1_sort() {
	ccnavi_c1_exe c1 sort "$ccnavi_c1_family_id" ${2:+"$2"} 2>"$ccnavi_c1_tmp/sort-err" </dev/null | tr -d '\r' >"$1" || :
	if [ "$(head -n 1 "$1" 2>/dev/null)" != "c1 1" ]; then
		ccnavi_c1_say "置き場の変更を見分けられなかった（$(head -n 1 "$ccnavi_c1_tmp/sort-err" 2>/dev/null)）。何も書いていない"
		return 1
	fi
	return 0
}

# 見分けで止めるものがあれば言って 1。<見分けの出力> <c の前置き> <c の後書き> <d の前置き> <d の後書き>
ccnavi_c1_stops() {
	ccnavi_cs_c=$(sed -n 's/^c //p' "$1" | tr '\n' ' ' | sed 's/ *$//')
	ccnavi_cs_d=$(sed -n 's/^d //p' "$1" | tr '\n' ' ' | sed 's/ *$//')
	[ -z "$ccnavi_cs_c$ccnavi_cs_d" ] && return 0
	[ -n "$ccnavi_cs_c" ] && ccnavi_c1_say "$2（${ccnavi_cs_c}）$3"
	[ -n "$ccnavi_cs_d" ] && ccnavi_c1_say "$4（${ccnavi_cs_d}）$5"
	sed -n 's/^why /  - /p' "$1" >&2
	log_info C1 で止めた -- "family=$ccnavi_c1_family_id" "reason=c1-unknown-change"
	return 1
}

# 3〜5。0 なら続けてよい。
ccnavi_c1_prepare() {
	ccnavi_cp_try=0
	while :; do
		ccnavi_cp_try=$((ccnavi_cp_try + 1))
		# 3. C1 の外の変更を見分け、(b) を取り込みの前にコミットする。
		ccnavi_c1_sort "$ccnavi_c1_tmp/sort" || return 1
		ccnavi_c1_stops "$ccnavi_c1_tmp/sort" "ユーザの判断が未送信" \
			"。運ぶ処理（sh $ccnavi_c1_sh/ccnavi-push-approved.sh ${ccnavi_c1_family_id}）をユーザが打つ。何も書いていない" \
			"置き場に ccnavi の知らない変更がある" "。ユーザが確かめてください。何も書いていない" || return 1
		sed -n 's/^keep //p' "$ccnavi_c1_tmp/sort" >"$ccnavi_c1_tmp/keep"
		sed -n 's/^b //p' "$ccnavi_c1_tmp/sort" >"$ccnavi_c1_tmp/b"
		if [ -s "$ccnavi_c1_tmp/b" ]; then
			ccnavi_c1_commit "$ccnavi_c1_tmp/b" "ccnavi: $ccnavi_c1_family_id の hook のマーカーと状態の履歴を運ぶ" || return 1
		fi
		# 4. 取り込み（ccnavi-sync.sh。ロックは入れ子で渡る）。統合先の取り込み結果も同じ回で書く。
		ccnavi_cp_rc=0
		sh "$ccnavi_c1_sh/ccnavi-sync.sh" "$ccnavi_c1_family_id" </dev/null >"$ccnavi_c1_tmp/sync" 2>&1 || ccnavi_cp_rc=$?
		sed "s/^/  /" "$ccnavi_c1_tmp/sync" >&2
		if [ "$ccnavi_cp_rc" -ne 0 ]; then
			# 3 と 4 の間に hook が書いた（merge が書きかけと重なった）なら、3 から 1 回だけやり直す。
			if [ "$ccnavi_cp_try" -eq 1 ] && grep -q '書きかけの' "$ccnavi_c1_tmp/sync" 2>/dev/null; then
				ccnavi_c1_say "取り込みが書きかけと重なった。変更の見分けからもう 1 回だけやり直す"
				continue
			fi
			ccnavi_c1_say "取り込めなかった（上の ccnavi-sync.sh の文面）。何も書いていない。接続と、上に出た原因を直してから打ち直してください"
			return 1
		fi
		ccnavi_cp_record=$(ccnavi_family_record "$ccnavi_c1_root" "$ccnavi_c1_repo" "$ccnavi_c1_family_id")
		ccnavi_cp_state=$(ccnavi_record_get "$ccnavi_cp_record" state)
		if [ "$ccnavi_cp_state" != present ]; then
			ccnavi_c1_say "取り込みの後、親のブランチの取り込み状態が ${ccnavi_cp_state:-（無い）} になった。何も書いていない（上の ccnavi-sync.sh の文面）"
			return 1
		fi
		# 5. 未送信の置き場の変更（(b) 以外）が残っていれば止める（REQ-APV-11 の補足）。
		ccnavi_c1_sort "$ccnavi_c1_tmp/unsent" "refs/remotes/origin/$ccnavi_c1_family_id" || return 1
		ccnavi_c1_stops "$ccnavi_c1_tmp/unsent" "置き場に未送信のユーザの判断のコミットがある" \
			"。運ぶ処理（sh $ccnavi_c1_sh/ccnavi-push-approved.sh ${ccnavi_c1_family_id}）をユーザが打つ。何も書いていない" \
			"置き場に ccnavi の知らない未送信のコミットがある" \
			"。ユーザが確かめてください。前の状態の操作が送る前に強制終了されて残ったものなら、中身を確かめてから運ぶ処理（sh $ccnavi_c1_sh/ccnavi-push-approved.sh ${ccnavi_c1_family_id}）で送るか、ユーザがそのコミットを取り除く。何も書いていない" || return 1
		return 0
	done
}

# 一覧のパスだけをコミットする（D35）。<一覧> <文>。新しいファイルは先に add する。
# 変わったものが 1 つも無ければコミットせずに 0（ccnavi_c1_committed は空）。
# 落ちたら、この実行が add したパスを索引から外して 1（索引を元に戻す）。
ccnavi_c1_committed=""
ccnavi_c1_commit() {
	ccnavi_c1_committed=""
	: >"$ccnavi_c1_tmp/paths"
	: >"$ccnavi_c1_tmp/added"
	ccnavi_cc_base=$(git -C "$ccnavi_c1_tree" rev-parse --verify HEAD 2>/dev/null) || {
		ccnavi_c1_say "親のワークツリーの先頭を読めない。コミットしない"
		return 1
	}
	while IFS= read -r ccnavi_cc_path; do
		[ -n "$ccnavi_cc_path" ] || continue
		case "$ccnavi_cc_path" in
		/* | [A-Za-z]:*) continue ;;
		esac
		if [ -e "$ccnavi_c1_tree/$ccnavi_cc_path" ] || [ -L "$ccnavi_c1_tree/$ccnavi_cc_path" ]; then
			if [ -z "$(git -C "$ccnavi_c1_tree" ls-files -- ":(literal)$ccnavi_cc_path" 2>/dev/null)" ]; then
				if ! git -C "$ccnavi_c1_tree" add -- ":(literal)$ccnavi_cc_path" 2>"$ccnavi_c1_tmp/err"; then
					ccnavi_c1_say "$ccnavi_cc_path を add できなかった（$(head -n 1 "$ccnavi_c1_tmp/err")）"
					ccnavi_c1_unstage "$ccnavi_c1_tmp/added" "$ccnavi_cc_base"
					return 1
				fi
				printf '%s\n' "$ccnavi_cc_path" >>"$ccnavi_c1_tmp/added"
			fi
		elif [ -z "$(git -C "$ccnavi_c1_tree" ls-files -- ":(literal)$ccnavi_cc_path" 2>/dev/null)" ]; then
			continue # 作って消した（git の知らない）パス
		fi
		printf '%s\n' "$ccnavi_cc_path" >>"$ccnavi_c1_tmp/paths"
	done <"$1"
	[ -s "$ccnavi_c1_tmp/paths" ] || return 0
	sed 's/^/:(literal)/' "$ccnavi_c1_tmp/paths" | tr '\n' '\000' >"$ccnavi_c1_tmp/pathspec"
	if ! xargs -0 git -C "$ccnavi_c1_tree" status --porcelain --untracked-files=all -- \
		<"$ccnavi_c1_tmp/pathspec" >"$ccnavi_c1_tmp/status" 2>"$ccnavi_c1_tmp/err"; then
		ccnavi_c1_say "git の状態を読めない（$(head -n 1 "$ccnavi_c1_tmp/err")）。コミットしない"
		ccnavi_c1_unstage "$ccnavi_c1_tmp/added" "$ccnavi_cc_base"
		return 1
	fi
	[ -s "$ccnavi_c1_tmp/status" ] || return 0
	# ユーザの hook は実行しない。署名などで尋ねて止まらないよう、見張りの時間で切る（決定 C）。
	if ! ccnavi_git_timed "$(ccnavi_c1_number "${CCNAVI_C1_COMMIT_TIMEOUT:-}" 60)" "$ccnavi_c1_tmp/err" "$ccnavi_c1_tree" \
		commit --quiet --only --no-verify -m "$2" \
		--pathspec-from-file="$ccnavi_c1_tmp/pathspec" --pathspec-file-nul >"$ccnavi_c1_tmp/out"; then
		cat "$ccnavi_c1_tmp/out" >>"$ccnavi_c1_tmp/err"
		ccnavi_c1_say "コミットできなかった（制限時間で打ち切った場合を含む）。$(ccnavi_git_refusal "$ccnavi_c1_tmp/err")"
		ccnavi_c1_unstage "$ccnavi_c1_tmp/added" "$ccnavi_cc_base"
		return 1
	fi
	ccnavi_c1_committed=$(git -C "$ccnavi_c1_tree" rev-parse HEAD)
	return 0
}

# 一覧のパスの索引を <版> に合わせる（<版> に無ければ索引から外す）。<一覧> <版>
ccnavi_c1_unstage() {
	while IFS= read -r ccnavi_cn_path; do
		[ -n "$ccnavi_cn_path" ] || continue
		git -C "$ccnavi_c1_tree" restore --staged --source="$2" -- ":(literal)$ccnavi_cn_path" 2>/dev/null ||
			git -C "$ccnavi_c1_tree" rm --cached --quiet --ignore-unmatch -- ":(literal)$ccnavi_cn_path" >/dev/null 2>&1 ||
			ccnavi_c1_say "$ccnavi_cn_path の索引を戻せなかった"
	done <"$1"
}

# 書いた一覧のうち、この実行で書いたパスの作業ツリーの中身を <版> に戻す（無ければ消す）。<一覧> <版>
# record-risk の記録など、この実行の前から未コミットだったもの（keep）は触らない。
ccnavi_c1_restore_written() {
	while IFS= read -r ccnavi_cr_path; do
		[ -n "$ccnavi_cr_path" ] || continue
		case "$ccnavi_cr_path" in
		/* | [A-Za-z]:*) continue ;;
		esac
		grep -F -x -q -- "$ccnavi_cr_path" "$ccnavi_c1_tmp/keep" 2>/dev/null && continue
		if git -C "$ccnavi_c1_tree" cat-file -e "$2:$ccnavi_cr_path" 2>/dev/null; then
			git -C "$ccnavi_c1_tree" restore --worktree --source="$2" -- ":(literal)$ccnavi_cr_path" 2>/dev/null ||
				ccnavi_c1_say "$ccnavi_cr_path を書く前の状態に戻せなかった"
		else
			rm -f "$ccnavi_c1_tree/$ccnavi_cr_path" 2>/dev/null ||
				ccnavi_c1_say "$ccnavi_cr_path を消せなかった"
		fi
	done <"$1"
}

# 実行ファイルを 1 回（一覧つき）。<一覧> <引数>...
ccnavi_c1_run() {
	ccnavi_cr_list="$1"
	shift
	rm -f "$ccnavi_cr_list"
	if [ -n "$ccnavi_c1_capture" ]; then
		ccnavi_c1_exe --record-writes "$ccnavi_cr_list" --record-tree "$ccnavi_c1_tree" "$@" >"$ccnavi_c1_capture"
	else
		ccnavi_c1_exe --record-writes "$ccnavi_cr_list" --record-tree "$ccnavi_c1_tree" "$@"
	fi
}

# 6〜11。<文> -- <実行ファイルの引数>...。終了コードは実行ファイルのもの（C1 で落ちたら 1）。
ccnavi_c1_write() {
	ccnavi_cw_message="$1"
	shift
	[ "${1:-}" = "--" ] && shift
	ccnavi_cw_dir="$ccnavi_c1_root/logs/state/c1/$ccnavi_c1_repo"
	mkdir -p "$ccnavi_cw_dir" 2>/dev/null || :
	ccnavi_cw_list="$ccnavi_cw_dir/$ccnavi_c1_family_id.$$.writes"
	ccnavi_cw_timeout=$(ccnavi_c1_number "${CCNAVI_C1_TIMEOUT:-}" 60)
	ccnavi_cw_try=0
	while :; do
		ccnavi_cw_try=$((ccnavi_cw_try + 1))
		# 6. 元の先頭。
		ccnavi_cw_h0=$(git -C "$ccnavi_c1_tree" rev-parse --verify HEAD 2>/dev/null) || {
			ccnavi_c1_say "親のワークツリーの先頭を読めない。何も書いていない"
			return 1
		}
		# 7. 書く。
		ccnavi_cw_rc=0
		ccnavi_c1_run "$ccnavi_cw_list" "$@" || ccnavi_cw_rc=$?
		if [ ! -f "$ccnavi_cw_list" ]; then
			ccnavi_c1_say "実行ファイルが書いたパスの一覧を出さなかった。書いたものが分からないのでコミットしない。親のワークツリー（${ccnavi_c1_tree}）をユーザが確かめてください"
			return 1
		fi
		if [ "$ccnavi_cw_rc" -ne 0 ]; then
			# 落ちた回は、ここまでに書いたものを戻す（コミットしない）。
			ccnavi_c1_restore_written "$ccnavi_cw_list" "$ccnavi_cw_h0"
			rm -f "$ccnavi_cw_list"
			return "$ccnavi_cw_rc"
		fi
		# 8. コミット。
		if ! ccnavi_c1_commit "$ccnavi_cw_list" "$ccnavi_cw_message"; then
			ccnavi_c1_restore_written "$ccnavi_cw_list" "$ccnavi_cw_h0"
			rm -f "$ccnavi_cw_list"
			return 1
		fi
		ccnavi_cw_c="$ccnavi_c1_committed"
		# 送る前に切られたら戻せるように覚えておく（ccnavi_c1_end）。
		if [ -n "$ccnavi_cw_c" ]; then
			ccnavi_c1_inflight="$ccnavi_cw_c"
			ccnavi_c1_inflight_h0="$ccnavi_cw_h0"
			ccnavi_c1_inflight_list="$ccnavi_cw_list"
		fi
		if [ -z "$ccnavi_cw_c" ] && [ "$(git -C "$ccnavi_c1_tree" rev-parse HEAD)" = "$(git -C "$ccnavi_c1_tree" rev-parse --verify -q "refs/remotes/origin/$ccnavi_c1_family_id" 2>/dev/null)" ]; then
			rm -f "$ccnavi_cw_list"
			return 0 # 書いたものが無く、送るものも無い
		fi
		ccnavi_cw_head=$(git -C "$ccnavi_c1_tree" rev-parse HEAD)
		# 9. push（--force なし）。
		if ccnavi_git_timed "$ccnavi_cw_timeout" "$ccnavi_c1_tmp/push-err" "$ccnavi_c1_tree" \
			push --quiet origin "refs/heads/$ccnavi_c1_family_id:refs/heads/$ccnavi_c1_family_id" >/dev/null; then
			ccnavi_c1_inflight=""
			ccnavi_c1_sent "$ccnavi_cw_head"
			rm -f "$ccnavi_cw_list"
			return 0
		fi
		# 10. 落ちたように見えても、届いていれば成功。
		if ccnavi_git_timed "$ccnavi_cw_timeout" "$ccnavi_c1_tmp/ls-err" "$ccnavi_c1_tree" \
			ls-remote origin "refs/heads/$ccnavi_c1_family_id" >"$ccnavi_c1_tmp/ls" &&
			grep -F -x -q -- "$ccnavi_cw_head${ccnavi_c1_tab}refs/heads/$ccnavi_c1_family_id" "$ccnavi_c1_tmp/ls"; then
			ccnavi_c1_say "push の応答は落ちたが、リモートには届いていた（${ccnavi_cw_head}）"
			ccnavi_c1_inflight=""
			ccnavi_c1_sent "$ccnavi_cw_head"
			rm -f "$ccnavi_cw_list"
			return 0
		fi
		ccnavi_c1_say "push が通らなかった（$(head -n 1 "$ccnavi_c1_tmp/push-err" 2>/dev/null)）"
		# 11. 戻す。先頭が自分のコミットのときだけ。
		if [ -n "$ccnavi_cw_c" ]; then
			ccnavi_c1_inflight=""
			if ! ccnavi_c1_undo "$ccnavi_cw_c" "$ccnavi_cw_h0" "$ccnavi_cw_list"; then
				rm -f "$ccnavi_cw_list"
				return 1
			fi
		fi
		rm -f "$ccnavi_cw_list"
		if [ "$ccnavi_cw_try" -ge 2 ]; then
			ccnavi_c1_say "2 回目も送れなかった。書いたものは戻した。オンラインで sh $ccnavi_c1_sh/ccnavi-sync.sh $ccnavi_c1_family_id を打って取り込んでから、打ち直してください"
			log_info C1 で送れなかった -- "family=$ccnavi_c1_family_id" "reason=c1-push"
			return 1
		fi
		ccnavi_c1_say "書いたものを戻した。取り込みからもう 1 回だけやり直す"
		ccnavi_c1_prepare || return 1
		# 届いていたのに確かめ（ls-remote）も落ちていた回は、取り込みで自分のコミットが戻ってくる。
		# そのときは書き直さず、届いていたとして終える。
		if [ -n "$ccnavi_cw_c" ] &&
			git -C "$ccnavi_c1_tree" merge-base --is-ancestor "$ccnavi_cw_c" HEAD 2>/dev/null; then
			ccnavi_c1_say "取り込み直すと、前の回のコミット（$(printf '%.12s' "$ccnavi_cw_c")）がリモートに届いていた。書き直さずに終える"
			ccnavi_c1_sent "$(git -C "$ccnavi_c1_tree" rev-parse HEAD)"
			return 0
		fi
	done
}

# 比較つきで戻す。<自分のコミット> <元の先頭> <一覧>
ccnavi_c1_undo() {
	ccnavi_cu_now=$(git -C "$ccnavi_c1_tree" rev-parse HEAD 2>/dev/null || :)
	if [ "$ccnavi_cu_now" != "$1" ]; then
		ccnavi_c1_say "先頭が自分のコミット（$1）でなくなっていた（${ccnavi_cu_now}）。戻さずに止めた。ユーザが確かめてください"
		return 1
	fi
	git -C "$ccnavi_c1_tree" diff-tree --no-commit-id --name-only -r -z "$1" 2>/dev/null |
		tr '\000' '\n' >"$ccnavi_c1_tmp/in-commit"
	if ! git -C "$ccnavi_c1_tree" update-ref "refs/heads/$ccnavi_c1_family_id" "$2" "$1" 2>"$ccnavi_c1_tmp/err"; then
		ccnavi_c1_say "コミットを戻せなかった（$(head -n 1 "$ccnavi_c1_tmp/err")）。ユーザが確かめてください"
		return 1
	fi
	ccnavi_c1_unstage "$ccnavi_c1_tmp/in-commit" "$2"
	ccnavi_c1_inflight=""
	ccnavi_c1_restore_written "$3" "$2"
	log_info C1 で書いたものを戻した -- "family=$ccnavi_c1_family_id"
	return 0
}

# 送れた。親のブランチの取り込み状態の sha を書き換える（state はそのまま present）。<送った先頭>
ccnavi_c1_sent() {
	ccnavi_cn_record=$(ccnavi_family_record "$ccnavi_c1_root" "$ccnavi_c1_repo" "$ccnavi_c1_family_id")
	if [ "$(ccnavi_record_get "$ccnavi_cn_record" state)" = present ]; then
		ccnavi_record_write "$ccnavi_cn_record" remote origin branch "$ccnavi_c1_family_id" sha "$1" \
			fetched_at "$(ccnavi_record_get "$ccnavi_cn_record" fetched_at)" state present reason "" ||
			ccnavi_c1_say "親のブランチの取り込み状態（${ccnavi_cn_record}）を書けなかった"
	fi
	ccnavi_c1_say "親のブランチ $ccnavi_c1_family_id のチケットの状態を送った（$(printf '%.12s' "$1")）"
	log_info C1 で送った -- "family=$ccnavi_c1_family_id"
}

# 後始末。ロックを外し、一時ディレクトリを消す。2 度呼んでも害は無い。
ccnavi_c1_inflight=""
ccnavi_c1_inflight_h0=""
ccnavi_c1_inflight_list=""
ccnavi_c1_end() {
	# 見張りの途中で切られたら、見張りも止める（後で別のプロセスを kill しないように）。
	if [ -n "${ccnavi_gt_dog:-}" ]; then
		kill "$ccnavi_gt_dog" 2>/dev/null || :
		ccnavi_gt_dog=""
	fi
	if [ -n "$ccnavi_c1_inflight" ] && [ -n "$ccnavi_c1_tmp" ]; then
		# 送る前に切られた。自分のコミットを比較つきで戻す（先頭が動いていれば戻さない）。
		ccnavi_ce_c="$ccnavi_c1_inflight"
		ccnavi_c1_inflight=""
		ccnavi_c1_say "送る前に止められた。書いたものを戻す"
		ccnavi_c1_undo "$ccnavi_ce_c" "$ccnavi_c1_inflight_h0" "$ccnavi_c1_inflight_list" || :
		rm -f "$ccnavi_c1_inflight_list"
	fi
	ccnavi_lock_drop
	if [ -n "$ccnavi_c1_tmp" ]; then
		rm -rf "$ccnavi_c1_tmp"
		ccnavi_c1_tmp=""
	fi
	return 0
}

# ---- 診断ログ（docs/claude/logging.md）
#
#   log_info <本文の語>... [-- <キー>=<値>...]
#
# 本文の語はスペースでつなぐ。`--` の後ろは 1 つずつ `キー=値` として logfmt で並べる。
# 出る行の形は次のとおり（Python の ccnavi/records/diaglog.py、拡張の src/log.ts と同じ）。
#
#   2026-09-27T10:15:03+09:00 INFO  ccnavi-git[4242] 拒否した sub=push reason=unapproved
#
# 置き場はワークスペースルートの `logs/diag/<出どころ>.log`。ルートは呼ぶ側が
# ccnavi_log_root に入れておけばそれを使い、空なら ccnavi_workspace で探す。出どころは
# `$0` の名前から拡張子を除いたもので、CCNAVI_LOG_NAME で上書きできる。出どころに
# `[A-Za-z0-9_-]` 以外の文字があれば書かない（パスの区切りや `..` を名前に入れさせない）。
#
# **シンボリックリンクはたどらない。** `logs`・`logs/diag`・書き込み先のファイルのどれかが
# シンボリックリンクなら書かずに捨てる。リンク先へ追記すると、置き場の外のファイル（判定の記録など）を書き換えてしまう。
# ファイルを新しく作るときは umask 077 のサブシェルで作り、持ち主だけが読める 0600 にする。
#
# **標準出力と標準エラーには何も出さず、何があっても 0 を返す。** 書けない（置き場が
# 作れない・権限・容量）ときは何も出さずに捨てる。`set -eu` の下で呼んでも、呼ぶ側を止めない。
# 契約として決まっている出力（reject / fail の標準エラー、ok / fail の 1 行目）とは分けてあり、
# そちらは変えない。
#
# 本文と値の中の、URL と scp 形式に埋まった資格情報を `***` に伏せる（ccnavi_log_mask）。
#
# 出さないレベルでは、date を起動せず、文字列も組み立てない（ccnavi_log_on で先に見る）。
# 1 行を書くときに起動する外部コマンドは date と、置き場が無いときの mkdir だけ。ほかに、
# 新しくファイルを作るときの umask のサブシェルと、ccnavi_log_root が空のときに 1 度だけ
# 走る ccnavi_workspace（dirname などを起動する）がある。残りはシェルの展開で済ませる。

ccnavi_log_min=""
ccnavi_log_file=""
ccnavi_log_name=""
# 呼ぶ側が求めたワークスペースルート。入れておくと ccnavi_workspace で探し直さない。
ccnavi_log_root=""
# CR は printf でしか作れない。読み込むときに 1 度だけ作る。
ccnavi_log_cr=$(printf '\r')
# 語の切れ目（空白・タブ・LF・CR）。資格情報を伏せるときに語を切り出すのに使う。
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

# 書き込み先。1 度求めたら覚える。出どころが使えない字を含むか、ワークスペースルートが
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
	# シンボリックリンクはたどらない。logs・logs/diag・書き込み先のどれかがリンクなら捨てる。
	if [ -L "${ccnavi_le_dir%/*}" ] || [ -L "$ccnavi_le_dir" ] || [ -L "$ccnavi_log_file" ]; then
		return 0
	fi
	if [ ! -d "$ccnavi_le_dir" ]; then
		mkdir -p "$ccnavi_le_dir" >/dev/null 2>&1 || return 0
	fi
	# `>>` は O_APPEND で開く。printf は 1 行を 1 度の write で出す。
	# 無いファイルは umask 077 のサブシェルで作り、0600 にする。在ればサブシェルを起動しない。
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

# 改行（CR LF・CR・LF）を `\n` の 2 字に置き換える。結果は ccnavi_log_out。
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
# `"` で囲んで `\` と `"` をエスケープする。結果は ccnavi_log_out。
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
# 同じ規則で伏せ、3 つの結果は 1 文字も違わない（tests/sh/test_diaglog_sh.py）。結果は ccnavi_log_out。
#
# 空白・タブ・LF・CR で切った語ごとに見る。
#   - `://` を含む語: `://` の後ろから次の `/` までを authority とし、`@` があれば最後の `@` より
#     前を `***` にする（`https://user:tok@host/x` → `https://***@host/x`）
#   - `://` を含まない語: 最初の `/` より前に `@` があり、最後の `@` より前に `:` があれば、そこを
#     `***` にする（scp 形式 `user:tok@host:path` → `***@host:path`）。`git@host:path` は伏せない
# `@` の無い文字列は語に切らずにそのまま返す（ほとんどの行はこれで済む）。
# ユーザ向けの ccnavi_mask_url（`<伏せた>@host`）とは表記が違う。そちらは契約として決まっている出力なので変えない。
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
