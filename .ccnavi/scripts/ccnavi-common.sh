# ccnavi-common 保護済み sh が共有する部分の入口。単体では動かない。
#
#   . "$(dirname "$0")/ccnavi-common.sh"
#
# 呼ぶ側の `set -eu` の直後に置く。`$0` は呼ばれたときのパスそのままなので、
# `sh .ccnavi/scripts/ccnavi-git.sh` でも `sh ../../../.ccnavi/scripts/ccnavi-git.sh` でも
# 同じディレクトリを指す。この読み込みと、下の部品の読み込みにだけ `$0` を使い、ワークスペースルートの
# 決定には使わない（下の ccnavi_workspace の但し書き）。
#
# ここにあるのは 8 つ。標準出力と終了コードだけを返し、標準エラーには何も書かない。
# 失敗したときの文面は呼ぶ側が決める（reject と fail で書き方が違うため）。
#
#   ccnavi_abs <パス>          相対を絶対に直す
#   ccnavi_workspace           ワークスペースルートの絶対パス
#   ccnavi_bin <ワークスペースルート>  起動する実行ファイルのパス
#   ccnavi_compat_skew <ワークスペースルート> <実行ファイル>  実行ファイルと互換の版が食い違えば直し方を出す
#   ccnavi_project <ディレクトリ>  そこが属するプロジェクトの名前（ワークスペース自身なら空）
#   ccnavi_mask_url <URL>      埋まった資格情報を伏せる
#   ccnavi_is_ident <語>       識別子として受けてよい書き方か（終了コードで返す）
#   ccnavi_is_branch <語>      親のブランチ名として受けてよい書き方か（終了コードで返す）
#
# 残りの関数は、同じディレクトリの部品 5 本に分けてあり、このファイルの最後で読む。
# 部品は単体では読まない。呼ぶ側は今までどおりこのファイルだけを読む。
#
#   ccnavi-common-state.sh  取り込み状態と統合先（ccnavi_state・ccnavi_integration・ccnavi_parent_tree など）と、
#                           タイムアウト監視つきの git（ccnavi_git_timed・ccnavi_git_refusal）
#   ccnavi-common-lock.sh   親子のチケットのロック（ccnavi_lock_take・ccnavi_lock_drop など）
#   ccnavi-common-c1.sh     C1。チケットの状態を書く一連の操作を 1 つにまとめる（ccnavi_c1_*）
#   ccnavi-common-host.sh   ホスト（GitHub / GitLab）への接続（ccnavi_host_*）
#   ccnavi-common-log.sh    診断ログの 4 つ（log_debug / log_info / log_warn / log_error）。標準出力にも
#                           標準エラーにも何も出さず、`logs/diag/<出どころ>.log` に 1 行足すだけ。
#                           決まりは docs/claude/logging.md
#
# 部品が 1 本でも欠けていれば、標準エラーに配り直しを言って 1 で終わる。

# この sh が頼る実行ファイルの契約の版（互換の版）。実行ファイルの src/ccnavi/entry/version.py の COMPAT、
# VS Code 拡張の EXTENSION_COMPAT と同じ値に揃える。上げるのは、sh が頼るフラグや出力の形を
# shを直さないと動かない形に変えたときと、データの形（待ち方の置き場・取り下げの条件など）が
# 変わるとき（上げ方は src/ccnavi/entry/version.py の説明）。`ccnavi --lint` もこの行を読んで比べる。
CCNAVI_COMPAT=8

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
# gitの `rev-parse --show-toplevel` はリンクを解いたパスを返すので、ワークスペースルート（cwdから
# 論理のパスで決まる）と比べるときは両辺をこれで揃える。揃えないと、リンクを経た作業場で
# 「.claude/worktrees/ の下か」の比較が外れ、組み込みの保護が当てはまらない。Windows は pwd -W の表記。
ccnavi_phys() {
	[ -n "${1:-}" ] || return 0
	(cd "$1" 2>/dev/null && { pwd -W 2>/dev/null || pwd -P; }) || printf '%s\n' "$1"
}

# ワークスペースルート。hook の登録・実行ファイル・保護済みスクリプトの置き場。
#
# git に聞かない。 gitのトップは gitの用途にだけ使う。モード B では
# `cwd` がプロジェクトの中にあると git はプロジェクトを答える。それは git として
# 正しい答えで、ここで欲しいものとは違う（設計 11.8）。
#
# 目印は `.ccnavi/scripts/ccnavi-common.sh`。自分自身なので、無ければそもそも sh が呼べていない。
# ディレクトリの `.ccnavi/scripts/` だけでは足りない。ccnavi ディレクトリの下には配点が呼ぶスクリプトの
# 置き場として同じパスがあり、プロジェクトの中から打つとそのプロジェクトを根と取り違える。
# `.git` は駄目（プロジェクトも持つ）。`.claude/` だけも駄目（Claude Code が作る場合が
# あり、プロジェクト側にできたものに当たる）。
#
# ワークツリーは候補にせず、最初に当たったものを返す。
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
# hook と同じ振り分けの sh（.ccnavi/scripts/ccnavi-launcher.sh）を見る。振り分けの shは
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
	if [ -f "$1/build.py" ] && [ -f "$1/src/ccnavi/__main__.py" ]; then
		ccnavi_cs_fix="build.py を実行して組み立て直してください（uv run --with pyinstaller python build.py）"
	else
		ccnavi_cs_fix="ccnavi のリポジトリで build.py を実行し、scripts/ccnavi-setup.sh <このワークスペース> --force で実行ファイルとshを配り直してください"
	fi
	if [ -z "$ccnavi_cs_have" ]; then
		printf '実行ファイル %s は --version で互換の版を返しません（古い版です）。%s。\n' "$2" "$ccnavi_cs_fix"
		return 1
	fi
	[ "$ccnavi_cs_have" = "$CCNAVI_COMPAT" ] && return 0
	printf '実行ファイル %s は互換 %s、shは互換 %s で食い違っています。%s。\n' "$2" "$ccnavi_cs_have" "$CCNAVI_COMPAT" "$ccnavi_cs_fix"
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
# 取れなければ空を返す。止めない。 `.git` が読めない、`gitdir:` が無い、
# 元リポジトリが消えている（孤児）のどれでも空。記録の置き場のために作業を止めるのは
# 釣り合わない。
ccnavi_project() {
	ccnavi_pj_dir=$(ccnavi_abs "${1:-.}") || return 0
	if [ -n "${2:-}" ]; then
		ccnavi_pj_ws="$2"
	else
		ccnavi_pj_ws=$(ccnavi_workspace) || return 0
	fi
	ccnavi_pj_places=projects # 固定
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
#   - `[^/]*@` で最後の `@` まで消す。解析側（authority の ${##*@}）が最後まで
#     見ているので、伏せる範囲も合わせる。`[^/@]*@` にすると `glpat-A@B` の後半が残る
#   - scheme は大文字も `git+ssh` も拾う
#   - scheme の無い `git@host:path` の形も伏せる
ccnavi_mask_url() {
	printf '%s' "${1:-}" | sed -E \
		-e 's#^([A-Za-z][A-Za-z0-9+.-]*://)[^/]*@#\1<伏せた>@#' \
		-e 's#^[^/:@]*:[^/@]*@#<伏せた>@#'
}

# 親や子の識別子として受けてよい書き方なら 0。<語>
#
# 識別子は ASCII の英数字と `.` `_` `-` に、日本語の字（ひらがな・カタカナ・漢字）を足した形。
# `LC_ALL=C` の `case` の文字クラスはマルチバイトの字を 1 字として扱えず、ロケールによって
# 範囲（`[a-z]`）の読み方も変わるので、ここでは字の種類を細かく見ない。止めるのは、パスや
# シェルで意味を持つ書き方だけ: 空、先頭の `-` と `.`、`..`、`/`、`\`、ASCII の英数字と `.` `_` `-`
# 以外の ASCII の字（空白・制御文字・`$` `;` `*` などの記号）。ASCII の外のバイトは通し、
# 字の種類（全角記号や NFD を止める）は実行ファイル（`ticket_ids.id_problem`）が確かめる。
ccnavi_is_ident() {
	case "$1" in
	'' | -* | .* | *..* | */* | *\\*) return 1 ;;
	[ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789]*) ;;
	*) return 1 ;;
	esac
	# 末尾の改行が `$( )` で落ちないよう、最後に `/`（上で止めた字なので本文には無い）を足して比べる。
	ccnavi_ii_rest=$(printf '%s/' "$1" | LC_ALL=C tr -d 'A-Za-z0-9._\200-\377-')
	[ "$ccnavi_ii_rest" = / ]
}

# 親のブランチ名として受けてよい書き方なら 0。<語>
#
# 親のブランチ名は識別子の字に階層の区切りの `/` を足したもの（`feature/123-login`）。実行ファイル
# （`ticket_ids.branch_problem`）が字と形を確かめたものを受け取る側の 2 段目の確認で、パスや ref で意味を持つ
# 書き方を止める: 空、先頭の `-` `.` `/`、末尾の `/` `.`、`..`、`//`、`/.`（`.` で始まる階層）、`.lock` で終わる
# 階層、`\`、ASCII の英数字と `.` `_` `-` `/` 以外の ASCII の字（空白・制御文字・記号）、gitの ref の名前
# （refs/・origin/ など）や保護されたブランチの名前を先頭の階層に持つもの、HEAD の階層を持つもの。
ccnavi_is_branch() {
	case "$1" in
	'' | -* | .* | /* | */ | *. | *..* | *//* | */.* | *.lock | *.lock/* | *\\*) return 1 ;;
	[ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789]*) ;;
	*) return 1 ;;
	esac
	# 末尾の改行が `$( )` で落ちないよう、最後に `\`（上で止めた字なので本文には無い）を足して比べる。
	ccnavi_ib_rest=$(printf '%s\\' "$1" | LC_ALL=C tr -d 'A-Za-z0-9._/\200-\377-')
	[ "$ccnavi_ib_rest" = '\' ] || return 1
	# gitの ref の名前・リモートの名前・保護されたブランチの名前を先頭の階層に持つもの、`HEAD`・`*_HEAD` の
	# 階層を持つもの（`ticket_ids.branch_problem` と同じ。大文字小文字は区別しない）。
	ccnavi_ib_low=$(printf '%s' "$1" | LC_ALL=C tr 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' 'abcdefghijklmnopqrstuvwxyz')
	case "$ccnavi_ib_low" in
	refs | refs/* | heads | heads/* | remotes | remotes/* | tags | tags/* | origin | origin/* | upstream | upstream/*) return 1 ;;
	main | main/* | master | master/* | develop | develop/* | release | release/* | release-*) return 1 ;;
	head | head/* | */head | */head/* | *_head | *_head/*) return 1 ;;
	esac
	return 0
}

# ---- 部品を読む
#
# 置き場は呼んだ sh（`$0`）のディレクトリ。呼ぶ側がこのファイルを探すのと同じ
# `dirname "$0"` で求め、このファイルを読んだのと同じ場所から読む。
# 環境変数からは受け取らない。置き場を外から差し替えられると、保護していない場所の shを
# 保護済み sh の中で走らせられるため。
ccnavi_lib_dir=$(dirname "$0")
for ccnavi_lib_part in state lock c1 host log; do
	[ -f "$ccnavi_lib_dir/ccnavi-common-$ccnavi_lib_part.sh" ] || {
		printf 'ccnavi: 共通部の部品 %s が見つかりません。scripts/ccnavi-setup.sh --force で配り直してください\n' "$ccnavi_lib_dir/ccnavi-common-$ccnavi_lib_part.sh" >&2
		exit 1
	}
	. "$ccnavi_lib_dir/ccnavi-common-$ccnavi_lib_part.sh"
done
