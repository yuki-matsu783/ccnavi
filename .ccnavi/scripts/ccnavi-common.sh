# ccnavi-common 保護済み sh が共有する部分の入口。単体では動かない。
#
#   . "$(dirname "$0")/ccnavi-common.sh"
#
# 呼ぶ側の `set -eu` の直後に置く。`$0` は呼ばれたときのパスそのままなので、
# `sh .ccnavi/scripts/ccnavi-git.sh` でも `sh ../../../.ccnavi/scripts/ccnavi-git.sh` でも
# 同じディレクトリを指す。この読み込みと、下の部品の読み込みにだけ `$0` を使い、ワークスペースルートの
# 決定には使わない（下の ccnavi_workspace の但し書き）。
#
# ここにあるのは 13 個。`ccnavi_load` 以外は、標準出力と終了コードだけを返し、標準エラーには何も書かない。
# 失敗したときの文面は呼ぶ側が決める（reject と fail で書き方が違うため）。
#
#   ccnavi_abs <パス>          相対を絶対に直す
#   ccnavi_sh_quote <文字列>   単引用符で囲んで、そのまま打てる形にする
#   ccnavi_phys <パス>         リンクを解いたパス（無ければそのまま）
#   ccnavi_workspace           ワークスペースルートの絶対パス
#   ccnavi_bin <ワークスペースルート>  起動する実行ファイルのパス
#   ccnavi_bin_path <振り分けの sh>  PATH の ccnavi（これ自身へのリンクは飛ばす）
#   ccnavi_bin_try <パス>      そのパス（または .exe）が実行できれば返す
#   ccnavi_project <ディレクトリ>  そこが属するプロジェクトの名前（ワークスペース自身なら空）
#   ccnavi_mask_url <URL>      埋まった資格情報を伏せる
#   ccnavi_is_ident <語>       識別子として受けてよい書き方か（終了コードで返す）
#   ccnavi_is_branch <語>      親のブランチ名として受けてよい書き方か（終了コードで返す）
#   ccnavi_branch_to_id <ブランチ>  親のブランチ名から親の識別子を作る（/ を - に置き換える）
#   ccnavi_load <ファイル名>   呼ぶ側が読む部品を読む（無ければ標準エラーに書いて 1 で終わる）
#
# 残りの関数は、同じディレクトリの部品 5 本に分けてあり、このファイルの最後で読む。
# 部品は単体では読まない。呼ぶ側は今までどおりこのファイルだけを読む。
#
#   ccnavi-common-state.sh  state の置き場と統合先（ccnavi_state・ccnavi_integration・ccnavi_parent_tree など）、
#                           親ブランチの push の関門（ccnavi_push_gate）、
#                           タイムアウト監視つきの git（ccnavi_git_timed・ccnavi_git_refusal）
#   ccnavi-common-lock.sh   親子のチケットのロック（ccnavi_lock_take・ccnavi_lock_drop など）
#   ccnavi-common-write-and-push.sh     write-and-push。チケットの状態を書く一連の操作を 1 つにまとめる（ccnavi_wap_*）
#   ccnavi-common-host.sh   ホスト（GitHub / GitLab）への接続（ccnavi_host_*）と、時間を区切って実行する ccnavi_run_timed
#   ccnavi-common-log.sh    診断ログの 4 つ（log_debug / log_info / log_warn / log_error）。標準出力にも
#                           標準エラーにも何も出さず、`logs/diag/<出どころ>.log` に 1 行足すだけ。
#                           決まりは ccnavi のリポジトリの .agents/docs/logging.md
#
# 部品が 1 本でも欠けていれば、標準エラーに配り直しを言って 1 で終わる。
#
# ccnavi-common-review.sh（ホストのマージリクエストを読む側）は、ここでは読まない。ホストの道具が要るので、
# レビューの sh（ccnavi-review.sh・ccnavi-user-decide.sh・ccnavi-user-close-early.sh）がこのファイルの後に自分で読む。
# 共通部（ccnavi-common*.sh）には、読む側とエージェントも使う関数だけを置く。ユーザの判断を書く処理は
# ユーザ専用の sh（ccnavi-user-*.sh）の中だけに置く。

# この sh が頼る実行ファイルの契約の版（互換の版）。実行ファイルの src/ccnavi/entry/version.py の COMPAT、
# VS Code 拡張の EXTENSION_COMPAT と同じ値に揃える。上げるのは、sh が頼るフラグや出力の形を
# sh を直さないと動かない形に変えたときと、データの形（先行関係の置き場など）が
# 変わるとき（上げ方は src/ccnavi/entry/version.py の説明）。`ccnavi --lint` もこの行を読んで比べる。
CCNAVI_COMPAT=20

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

# 引数を単引用符で囲んで出す。中の ' は '"'"' に直す。案内の文に、そのまま打てるパスを載せる。
# <文字列>
ccnavi_sh_quote() {
	ccnavi_q_rest=$1
	ccnavi_q_out=""
	while :; do
		case "$ccnavi_q_rest" in
		*\'*)
			ccnavi_q_out="${ccnavi_q_out}${ccnavi_q_rest%%\'*}'\"'\"'"
			ccnavi_q_rest=${ccnavi_q_rest#*\'}
			;;
		*) break ;;
		esac
	done
	printf "'%s%s'\n" "$ccnavi_q_out" "$ccnavi_q_rest"
}

# 実在するディレクトリの、リンクを解いたパス。無ければ受けたパスをそのまま返す。
#
# git の `rev-parse --show-toplevel` はリンクを解いたパスを返すので、ワークスペースルート（cwd から
# 論理のパスで決まる）と比べるときは両辺をこれで揃える。揃えないと、リンクを経た作業場で
# 「.claude/worktrees/ の下か」の比較が外れ、組み込みの保護が該当しない。Windows は pwd -W の表記。
ccnavi_phys() {
	[ -n "${1:-}" ] || return 0
	(cd "$1" 2>/dev/null && { pwd -W 2>/dev/null || pwd -P; }) || printf '%s\n' "$1"
}

# ワークスペースルート。hook の登録・実行ファイル・保護済みスクリプトの置き場。
#
# git に聞かない。 git のトップは git の用途にだけ使う。モード B では
# `cwd` がプロジェクトの中にあると git はプロジェクトを答える。それは git として
# 正しい答えで、ここで欲しいものとは違う。
#
# 目印は `.ccnavi/scripts/ccnavi-common.sh`。自分自身なので、無ければそもそも sh が呼べていない。
# ディレクトリの `.ccnavi/scripts/` だけでは足りない。ccnavi ディレクトリの下には配点が呼ぶスクリプトの
# 置き場として同じパスがあり、プロジェクトの中から打つとそのプロジェクトを根と取り違える。
# `.git` は駄目（プロジェクトも持つ）。`.claude/` だけも駄目（Claude Code が作る場合が
# あり、プロジェクト側にできたものにヒットする）。
#
# ワークツリーでないディレクトリのうち、最初にヒットしたものを返す。
#
# `.ccnavi/scripts/` は git で追跡されているので、どのワークツリーにもコピーがある。
# 単純に「最初にヒットしたもの」にすると、ワークツリーの中から打ったときワークツリー自身が
# 根になる。ところが `logs/state/`（state の置き場）は追跡外でワークツリーには無く、承認済みチケットも
# ワークスペースルートに置かれたばかりのものはワークツリーに届いていないので、どちらも見つからなくなる。ccnavi が使うもののうち
# git でワークツリーに届くものと届かないものがあり、根は届かないほうに合わせる必要がある。
#
# だから `.claude/worktrees/` の下にあるものは候補から外し、最初にヒットした
# 「ワークツリーでない」ディレクトリを根にする。
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
				# プロジェクト（<根>/projects/<名前>/）が自前の .ccnavi/scripts/ のコピーを持つとき、
				# そこで止まると logs/ や state がプロジェクトの中にできる。`projects/` の親に
				# ワークスペースがあれば、そちらを根にする。
				ccnavi_ws_out=$(dirname "$ccnavi_ws_here")
				ccnavi_ws_outer=$(dirname "$ccnavi_ws_out")
				case "$ccnavi_ws_out" in
				*/projects)
					if [ -f "$ccnavi_ws_outer/.ccnavi/scripts/ccnavi-common.sh" ]; then
						case "$ccnavi_ws_outer" in
						*/.claude/worktrees/*) ;;
						*) ccnavi_ws_here="$ccnavi_ws_outer" ;;
						esac
					fi
					;;
				esac
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
# ユーザがターミナルから打つ場面では settings.json の env が反映されないので、CCNAVI_ENTRY_PATH が
# 無いのが普通。そのときは ccnavi のリポジトリの組み立て（dist/ccnavi/ccnavi）、次に
# hook と同じ振り分けの sh（.ccnavi/scripts/ccnavi-launcher.sh）を見る。振り分けの sh は
# .ccnavi/bin/ があるときだけ選ぶ。無いのに選ぶと、ソースで動かせる ccnavi のリポジトリでも
# 「実行ファイルが無い」で止まる。bin/ もソースも無い配布先では、PATH の ccnavi を返す（無ければ 1）。
#
# それぞれ `.exe` を付けた名前も見る（Windows の組み立て）。
ccnavi_bin() {
	case "${CCNAVI_ENTRY_PATH:-}" in
	'')
		ccnavi_bin_try "$1/dist/ccnavi/ccnavi" && return 0
		if [ -d "$1/.ccnavi/bin" ]; then
			ccnavi_bin_try "$1/.ccnavi/scripts/ccnavi-launcher.sh"
		elif [ ! -f "$1/src/ccnavi/__main__.py" ]; then
			# bin/ もソースも無い配布先。PATH の ccnavi があればそれを返す（振り分けの sh と同じ規則）。
			# 無ければ 1 を返すので、呼び手の「実行ファイルが無い」の分岐は今までと同じ。
			ccnavi_bin_path "$1/.ccnavi/scripts/ccnavi-launcher.sh"
		else
			return 1
		fi
		;;
	/* | [A-Za-z]:*) ccnavi_bin_try "$CCNAVI_ENTRY_PATH" ;;
	*) ccnavi_bin_try "$1/$CCNAVI_ENTRY_PATH" ;;
	esac
}

# PATH の ccnavi。振り分けの sh（ccnavi-launcher.sh の ccnavi_launcher_path）と同じ規則:
# 絶対パスの項目だけを見て、通常ファイルで実行できる最初のものを返す。$1 は振り分けの sh のパス。
# これと同じファイル（ccnavi という名前で張ったリンク）は、起動すると自分に戻るので飛ばす。
ccnavi_bin_path() {
	ccnavi_bp_ifs=$IFS
	case $- in *f*) ccnavi_bp_noglob=1 ;; *) ccnavi_bp_noglob=0 ;; esac
	set -f
	IFS=:
	for ccnavi_bp_dir in $PATH; do
		case "$ccnavi_bp_dir" in
		/*) ;;
		*) continue ;;
		esac
		for ccnavi_bp_name in ccnavi ccnavi.exe; do
			ccnavi_bp_file=$ccnavi_bp_dir/$ccnavi_bp_name
			if [ -f "$ccnavi_bp_file" ] && [ -x "$ccnavi_bp_file" ] && ! [ "$ccnavi_bp_file" -ef "${1:-}" ]; then
				IFS=$ccnavi_bp_ifs
				[ "$ccnavi_bp_noglob" = 1 ] || set +f
				printf '%s\n' "$ccnavi_bp_file"
				return 0
			fi
		done
	done
	IFS=$ccnavi_bp_ifs
	[ "$ccnavi_bp_noglob" = 1 ] || set +f
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

	# ワークスペースの下に無ければ、宣言するプロジェクトは無い。
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
		# 直下に .git を持つものだけがプロジェクト。
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
			*/*) return 0 ;; # 2 段以上は数えない
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
	# 末尾の改行が `$( )` で無くならないよう、最後に `/`（上で止めた字なので本文には無い）を足して比べる。
	ccnavi_ii_rest=$(printf '%s/' "$1" | LC_ALL=C tr -d 'A-Za-z0-9._\200-\377-')
	[ "$ccnavi_ii_rest" = / ]
}

# 親のブランチ名として受けてよい書き方なら 0。<語>
#
# 実行ファイルの `ticket_ids.branch_problem` の複製（ASCII で見分けられる分だけ）。write-and-push の対象決めなど、呼ぶたびに
# 実行ファイルを起こせない所で使う。名前を受け入れる入口（ccnavi-start.sh・
# ccnavi-worktree.sh・ccnavi-git.sh の worktree add）は実行ファイルに聞く（`ccnavi_id_ask`）。規則を変えるときは両方を直す。
#
# 親のブランチ名は識別子の字に階層の区切りの `/` を足したもの（`feature/123-login`）。実行ファイル
# （`ticket_ids.branch_problem`）が字と形を確かめたものを受け取る側の 2 段目の確認で、パスや ref で意味を持つ
# 書き方を止める: 空、先頭の `-` `.` `/`、末尾の `/` `.`、`..`、`//`、`/.`（`.` で始まる階層）、`.lock` で終わる
# 階層、`\`、ASCII の英数字と `.` `_` `-` `/` 以外の ASCII の字（空白・制御文字・記号）、git の ref の名前
# （refs/・origin/ など）や保護されたブランチの名前を先頭の階層に持つもの、HEAD の階層を持つもの。
ccnavi_is_branch() {
	case "$1" in
	'' | -* | .* | /* | */ | *. | *..* | *//* | */.* | *.lock | *.lock/* | *\\*) return 1 ;;
	[ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789]*) ;;
	*) return 1 ;;
	esac
	# 末尾の改行が `$( )` で無くならないよう、最後に `\`（上で止めた字なので本文には無い）を足して比べる。
	ccnavi_ib_rest=$(printf '%s\\' "$1" | LC_ALL=C tr -d 'A-Za-z0-9._/\200-\377-')
	[ "$ccnavi_ib_rest" = '\' ] || return 1
	# git の ref の名前・リモートの名前・保護されたブランチの名前を先頭の階層に持つもの、`HEAD`・`*_HEAD` の
	# 階層を持つもの（`ticket_ids.branch_problem` と同じ。大文字小文字は区別しない）。
	ccnavi_ib_low=$(printf '%s' "$1" | LC_ALL=C tr 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' 'abcdefghijklmnopqrstuvwxyz')
	case "$ccnavi_ib_low" in
	refs | refs/* | heads | heads/* | remotes | remotes/* | tags | tags/* | origin | origin/* | upstream | upstream/*) return 1 ;;
	main | main/* | master | master/* | develop | develop/* | release | release/* | release-*) return 1 ;;
	head | head/* | */head | */head/* | *_head | *_head/*) return 1 ;;
	esac
	return 0
}

# 親のブランチ名から親の識別子を作る。`/` を `-` に置き換えるだけ。<ブランチ>
#
# ワークツリーは `.claude/worktrees/<識別子>` に、識別子のブランチを作らず親のブランチを直接チェックアウトする。
# Python（`ticket_ids.branch_to_id`）・TS（拡張の `branchToId`）と同じ変換で、`tests/fixtures/branch-to-id.json`
# の同じ表で一致を確かめる。
ccnavi_branch_to_id() {
	printf '%s\n' "$1" | tr '/' '-'
}

# ---- 部品を読む
#
# 置き場は呼んだ sh（`$0`）のディレクトリ。呼ぶ側がこのファイルを探すのと同じ
# `dirname "$0"` で求め、このファイルを読んだのと同じ場所から読む。
# 環境変数からは受け取らない。置き場を外から差し替えられると、保護していない場所の sh を
# 保護済み sh の中で走らせられるため。
ccnavi_lib_dir=$(dirname "$0")

# 部品のうち、ここでは読まず呼ぶ側が読むもの（ccnavi-common-review.sh など）を読む。<ファイル名>
# 置き場は上の部品と同じ（呼んだ sh のディレクトリ）。無ければ配り直しを言って 1 で終わる
# （読めないまま `.` に進むと、シェルの生のエラーだけが出る）。
ccnavi_load() {
	[ -f "$ccnavi_lib_dir/$1" ] || {
		printf 'ccnavi: 共通部の部品 %s が見つかりません。scripts/ccnavi-setup.sh --force で配り直してください\n' "$ccnavi_lib_dir/$1" >&2
		exit 1
	}
	. "$ccnavi_lib_dir/$1"
}

for ccnavi_lib_part in state lock write-and-push host log; do
	[ -f "$ccnavi_lib_dir/ccnavi-common-$ccnavi_lib_part.sh" ] || {
		printf 'ccnavi: 共通部の部品 %s が見つかりません。scripts/ccnavi-setup.sh --force で配り直してください\n' "$ccnavi_lib_dir/ccnavi-common-$ccnavi_lib_part.sh" >&2
		exit 1
	}
	. "$ccnavi_lib_dir/ccnavi-common-$ccnavi_lib_part.sh"
done
