#!/bin/sh
# ccnavi-git 安全な git だけを通し、出力を抑えて結果だけ返すラッパースクリプト。
#
# 生の `git` は PreToolUse で拒否し、拒否の文面からここへ誘導する。狙いは 2 つ。
#
#   1. 出力がコンテキストに丸ごと載るのを止める。`git log -p` や `git diff` は
#      入力次第で数千行になる。全量は logs/ に残し、標準出力へは要約と先頭数十行
#      だけを返す。足りなければログを名指しで読ませる
#   2. 入口を 1 本にして、オプションの穴を防ぐ。`permissions.allow` の `Bash(git diff:*)`
#      は前方一致でしかないので、後ろに何を足されても通る。サブコマンドごとに
#      使ってよい形を書けるのは、入口を 1 本にしたここだけ
#
# ホワイトリストに無いものは既定で拒否する。拒否の文面には必ず「代わりに何をするか」を
# 書く。理由だけ返すとエージェントは言い換えて再試行する。
#
# これは失敗と浪費を減らすためのもので、敵対的な回避への防御ではない。
# `sh -c` や `python -c` に埋めれば hook の文字列一致は外れる。そこまで防ぐなら
# permissions.deny か sandbox が要る。
#
# 使い方:  sh .ccnavi/scripts/ccnavi-git.sh <サブコマンド> [引数...]
# 終了コード: 0 成功 / 1 git が失敗 / 2 引数か環境の誤り (拒否を含む)

set -eu

# 標準出力に返す本文の上限。超えたぶんはログにだけ残る。
MAX_LINES="${CCNAVI_GIT_MAX_LINES:-40}"

# 失敗したときに返す末尾の行数。原因はたいてい最後に出る。
FAIL_LINES="${CCNAVI_GIT_FAIL_LINES:-30}"

# 残す記録の本数。放っておくと増え続けるので世代で切る。
KEEP_LOGS="${CCNAVI_GIT_KEEP_LOGS:-50}"

# 対話になる経路を全部止める。Bash ツールの stdin は /dev/null だが、git の
# 資格情報プロンプトは /dev/tty を直接開くので stdin だけでは止まらない。
GIT_TERMINAL_PROMPT=0
GIT_PAGER=cat
PAGER=cat
GIT_EDITOR=true
export GIT_TERMINAL_PROMPT GIT_PAGER PAGER GIT_EDITOR

# 環境変数から設定を差し込む経路を閉じる。`-c diff.external=<コマンド>` を引数で
# 拒否しても、GIT_CONFIG_COUNT/KEY/VALUE と GIT_EXTERNAL_DIFF で同じことができる。
# 引数だけ見て環境を見ないと、防いだつもりの穴が別の経路で開いたままになる。
# GIT_CONFIG_KEY_n / VALUE_n は GIT_CONFIG_COUNT が無ければ読まれないので、
# 番号を数えて消す必要はない。GIT_CONFIG_COUNT を消せば全部読まれない。
# 例外は、このあと実行直前に ccnavi_git_auth が足す gh / glab の credential helper だけ（利用者の環境変数は引き継がない）。
unset GIT_EXTERNAL_DIFF GIT_CONFIG_PARAMETERS GIT_CONFIG_COUNT GIT_ALTERNATE_OBJECT_DIRECTORIES 2>/dev/null || true
# git が操作するリポジトリとワークツリーを、居場所（cwd）でなく環境変数で決める経路も閉じる。
# `GIT_DIR=<親>/.git GIT_WORK_TREE=<親> sh ccnavi-git.sh commit` を子のワークツリーで打つと、
# 居場所の検査（hook と、下の子のワークツリーの検査）は子を見るのに、git は親へコミットする。
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_COMMON_DIR GIT_OBJECT_DIRECTORY GIT_NAMESPACE \
	GIT_CEILING_DIRECTORIES GIT_DISCOVERY_ACROSS_FILESYSTEM 2>/dev/null || true

# 拒否の文面で代わりの形を示すときの、自分の呼び方。生の git は PreToolUse で
# 止まるので、案内に `git stash push -u` と書くと、案内された先でもう 1 度拒否される。
# 代わりの手段が拒否される案内は、案内が無いのとほとんど同じ。
# $0 は呼ばれたときのパスそのままなので、ワークツリーの中から相対で呼ばれても合う。
SELF="sh $0"
# 取り込みの sh。リモートに合わせる経路はここへ案内する。
SYNC="sh $(dirname "$0")/ccnavi-sync.sh"
# 親のワークツリーを作る入口。既にあるブランチを親にする経路はここへ案内する。
START="sh $(dirname "$0")/ccnavi-start.sh"

# reject <識別子> <文面>。文面は標準エラーへ出す契約。識別子は拒否の種類を表す短い語で、
# 診断ログにだけ残す。文面にはユーザの引数（URL など）が入るので、ログにはコピーしない。
reject() {
	printf 'ccnavi-git: %s\n' "$2" >&2
	# 診断ログ。上の文面は契約として決まっている出力で、診断ログはそれとは別に残すだけ。
	log_info 拒否した -- "sub=${sub:-}" "reason=$1"
	exit 2
}

# exe_reject <識別子> <文面>。実行ファイル（`ccnavi git check`）が拒否したとき。識別子は実行ファイルが決めるので、
# reject と違って文字のまま書けない。出し方と記録の仕方は reject と同じ。
exe_reject() {
	printf 'ccnavi-git: %s\n' "$2" >&2
	log_info 拒否した -- "sub=${sub:-}" "reason=$1"
	exit 2
}

# 共通部分。ワークスペースルートの探し方と、プロジェクト名の導出はここにある。
. "$(dirname "$0")/ccnavi-common.sh"

# 案内の文専用の、引用した自分の呼び方。パスに空白や ' があっても、そのまま打てる。
# `SELF` は今までの動きのまま変えない。
SELF_Q="sh $(ccnavi_sh_quote "$0")"
WT_SH_Q="sh $(ccnavi_sh_quote "$(dirname "$0")/ccnavi-worktree.sh")"
TICKET_SH_Q="sh $(ccnavi_sh_quote "$(dirname "$0")/ccnavi-ticket.sh")"

# ワークスペースルート。道具と記録の置き場。git のトップとは違うもので、
# モード B（projects/ の下に別リポジトリを clone する形）では一致しない。
# 上へたどって `.ccnavi/scripts/ccnavi-common.sh` を探す（保護済みスクリプトはワークスペースルートの .ccnavi/scripts/ に置くため）。
WS=$(ccnavi_workspace) ||
	reject no-workspace "ワークスペースルートが見つかりません（.ccnavi/scripts/ccnavi-common.sh を持つ親を cwd から上へ探しました）。ワークスペースの中で実行するか、CCNAVI_WORKSPACE にワークスペースルートの絶対パスを渡してください。"
# 解いたルートを logger に渡し、書くたびに探し直させない。
ccnavi_log_root="$WS"
# git が返すパス（リンクを解いたもの）と比べるための、リンクを解いたルート。
WS_P=$(ccnavi_phys "$WS")

usage() {
	cat <<'USAGE'
sh .ccnavi/scripts/ccnavi-git.sh <サブコマンド> [引数...]

通すもの
  読む      status log show diff blame shortlog describe rev-parse rev-list
            ls-files ls-tree merge-base diff-tree cat-file grep
  一覧      branch (-d は可 / -D -m -M -C -f -u は不可)  tag (一覧のみ。作成はユーザに依頼)
            remote (-v / show / get-url のみ)  worktree (list add prune remove)
            worktree add は行き先の名前 = ブランチ名の / を - に置き換えたもの、の形だけ
                  (add <行き先> -b <ブランチ> [<起点>] か add <行き先> <既にあるブランチ>。
                  -B / --detach / -f は不可。既にあるブランチを出す形は、識別子が親に使えるもの
                  （58 文字以内・末尾が -2 桁でない。子の識別子の形は 64 文字まで）だけ)
  変える    add  commit (--no-verify は不可)  rm <パス> (-f は不可)
            restore <パス>  (衝突の解決は restore --ours / --theirs -- <パス>)
            checkout / switch (ブランチを移る形だけ。-f・checkout -B・switch -C と -- <パス> は不可。
                  親のワークツリーでは別のブランチへ移れない)
            承認済みチケットの置き場 (.ccnavi/approved/ と wip/proposals/review/) には
                  restore --source / --ours / --theirs と checkout <ref> <パス> を利用できない
            ccnavi 自身を守る場所 (.ccnavi/ .claude/settings*.json .claude/hooks/ logs/ の記録と state
                  ccnavi-git.sh) には rm と restore --source と checkout <ref> <パス> を利用できない
                  (衝突の解決の restore --ours / --theirs は利用できる。変更が必要ならユーザに依頼する)
            stash (list show push pop apply)
            merge (-X ours / -s ours / --no-verify は不可。統合先のユーザのチェックアウトでは --abort / --continue / --quit 以外は不可)
            merge-file -p (標準出力に出す形だけ。ファイルへは書かない)
                  両方取り込むときは merge-file -p --union --object-id :2:<パス> :1:<パス> :3:<パス>
                  (現在・祖先・相手の順) で出力し、それを読んで Edit で書く
  通信      fetch  pull  (--force / --prune は不可。取ってくるのは <リモート> <ブランチ> だけで、
                   : や + を含む引数(refspec・URL)は不可。手元の ref は ccnavi-sync.sh が進める。
                   統合先のユーザのチェックアウトの pull は、引数なしと現在のブランチと同じ名前
                   (pull [--ff-only] <リモート> <統合先>) だけ。別のブランチは merge と同じく不可)
            push  (居るブランチを同じ名前で送る形だけ。force / delete / all は不可。
                   main master develop release と統合先 (そのリポジトリの origin/HEAD、無ければ
                   origin/main・origin/master) へ直接は送れない。
                   子チケットのワークツリーからは送れない。親が取り込んでから親のツリーで送る。
                   リモートから消えた親のブランチへは送れない。親のワークツリーは push の前に
                   リモートに在るかを確かめる。破棄した親ならワークツリーを片付ける)
  取り込み  直接作業(チケットを起こさない)のワークツリーを、切ったときの取り込み先のブランチへ 1 コミットで載せる。
            取り込み先をチェックアウトしているユーザのチェックアウトで実行します。チケットの作業は対象外(マージリクエストで出す)
            integrate <名前>          取り込み結果を計算して一時ワークツリー(logs/direct-tmp/)に展開し、差分と変わるファイルを
                                      出して止まる。ユーザのチェックアウトの index とワークツリーには書かない。
                                      wip/handoff/ の下書きは取り込まない
            integrate --abort <名前>  取り込みをやめる(一時ワークツリーと記録を消す。作業ワークツリーには触らない)
            integrate-done <名前> -m <メッセージ>
                                      一時ワークツリーで 1 コミットにし(フックを通す)、ユーザのチェックアウトで
                                      merge --ff-only --no-overwrite-ignore する。そのあとワークツリー・ブランチ・
                                      記録を片付ける。何度再実行してもよい

通さないもの(代わりの手段)
  reset clean   sh .ccnavi/scripts/ccnavi-git.sh stash push -u で退避する。削除しない
                リモートに合わせるなら sh .ccnavi/scripts/ccnavi-sync.sh <P> (親のブランチ)。
                ほかのブランチは fetch <リモート> <ブランチ> のあと merge <リモート>/<ブランチ>。
                分かれていて進めないならユーザの対応に切り替える (checkout -B でブランチを移さない)
  rebase cherry-pick revert am apply bisect  履歴を書き換えない
  config clone submodule  ユーザに依頼する
  -c / --config-env / --git-dir / -C / --output / --upload-pack / --exec-path
                読み取り専用のサブコマンドでも任意コマンドの実行や書き込みに
                なってしまうので、値を見ずに一律で拒否する

オプション: branch checkout switch fetch pull merge commit rm restore cat-file worktree は
            許可リストで読む。長いオプションは略さずに書く (略した表記は通らない)。
            短いオプションはまとめて書いてよく、値を取る字 (-b など) の後ろは値として読む

出力: 成功なら要約と先頭 40 行、失敗なら末尾 30 行。全量は logs/ に残る。
環境変数: CCNAVI_GIT_MAX_LINES / CCNAVI_GIT_FAIL_LINES / CCNAVI_GIT_KEEP_LOGS
USAGE
}

[ "$#" -eq 0 ] && {
	usage
	exit 2
}

case "$1" in
-h | --help | help)
	usage
	exit 0
	;;
esac

# 引数の形を通すかは実行ファイルが決める（`ccnavi git check`）。決めるのは、サブコマンドより前のオプション
# （`-c diff.external=<コマンド>`・`-C <パス>`・`--git-dir` など。値を見ずに形で断る）、どのサブコマンドでも許さない形
# （設定の一時上書き・ファイル書き込みや外部コマンド実行に変えるオプション・判定の起点を動かすオプションと、その略）、
# 引数だけで決まるサブコマンドの判定（読むだけの一覧・cat-file・grep・branch・tag・remote・stash・add・commit・
# 通さないサブコマンド）。答えは allow（通す）・pass（続きはこのあとの case が見る）・deny <識別子> と拒否の文。
# git の引数は標準入力に NUL 区切りで渡す（ccnavi の引数として渡すと、`-m` などを ccnavi のオプションとして読むため）。
# 診断ログの sub。サブコマンドより前のオプションで止めるときは空のまま。
case "$1" in
-*) ;;
*) sub="$1" ;;
esac
gc_bin=$(ccnavi_bin "$WS") ||
	reject no-exe "ccnavi の実行ファイルが見つからないため、git の引数の形を確かめられません。sh .ccnavi/scripts/ccnavi-launcher.sh --version で実行ファイルを確かめ、無ければユーザに伝えてください。"
gc_out=$(printf '%s\0' "$@" | "$gc_bin" --root "$WS" git check "$SELF" "$SYNC" "$START" "$SELF_Q" "$WT_SH_Q" 2>/dev/null) || gc_out=""
gc_out=$(printf '%s\n' "$gc_out" | tr -d '\r')
# 答えの行を読む。allow に続けて、git の引数の前に足す語（prepend）・出力の後ろに出す警告（warn）・push の前に確かめる
# 親のブランチ（gate-top・gate-branch）・統合先のユーザのチェックアウトの統合先（target）が付くことがある。
gc_line() { printf '%s\n' "$gc_out" | sed -n "s/^$1 //p" | head -n 1; }
case "$(printf '%s\n' "$gc_out" | head -n 1)" in
allow | pass) ;;
deny\ *)
	gc_id=$(printf '%s\n' "$gc_out" | sed -n '1s/^deny //p')
	exe_reject "$gc_id" "$(printf '%s\n' "$gc_out" | sed -n '2,$p')"
	;;
*) reject no-exe "ccnavi の実行ファイルが git の引数の形に答えませんでした。sh .ccnavi/scripts/ccnavi-launcher.sh --version で実行ファイルを確かめ、直せなければユーザに伝えてください。" ;;
esac
gc_warn=$(gc_line warn)
gc_gate_top=$(gc_line gate-top)
gc_gate_branch=$(gc_line gate-branch)
gc_target=$(gc_line target)
gc_prepend=$(gc_line prepend)

sub="$1"
shift
[ -z "$gc_prepend" ] || set -- "$gc_prepend" ${1+"$@"}
[ "$sub" != worktree ] || action="${1:-list}"

# 共通の git ディレクトリ（fetch が失敗した印の置き場。印は worktree add の起点の確認が読む）。
wt_fresh_dir() {
	wt_fd=$(git rev-parse --git-common-dir 2>/dev/null) || return 1
	(cd "$wt_fd" 2>/dev/null && pwd)
}

# ---- 直接作業の取り込み（integrate / integrate --abort / integrate-done）
#
# チケットを起こさない直接作業のワークツリー（.claude/worktrees/<名前>。ブランチも同じ名前）を、
# 切った時点でユーザのチェックアウトが開いていたブランチ（取り込み先）へ 1 コミットで載せる。
# 現在の形（版 4）は、下のd4_ 系の関数が受け持つ。確認の間はユーザのチェックアウトのindexとワークツリーに
# 書かず、一時ワークツリーで作ったコミットを `merge --ff-only --no-overwrite-ignore` で載せる。
# チケットの作業は今までどおりマージリクエストで出すので、チケットに結び付くワークツリーは断る。
#
# markerの版が4でないものは、版を知らない壊れた記録として「版を知りません」で止める。

# 名前を確かめる。<名前>
ig_name() {
	[ -n "${1:-}" ] ||
		reject integrate-no-name "$sub には取り込むワークツリーの名前を渡してください（$SELF $sub <名前>。.claude/worktrees/<名前> の名前）。"
	ccnavi_is_ident "$1" ||
		reject integrate-name "$sub の名前（$1）は .claude/worktrees/ の直下の名前の形ではありません。パスではなく名前だけを渡してください（$SELF $sub <名前>）。"
	case "$1" in
	main | master | develop | release | release-*)
		reject integrate-name "$1 は統合先に使う名前です。取り込むのは .claude/worktrees/<名前> の作業用のブランチです（$SELF $sub <名前>）。"
		;;
	esac
}

# 名前から、記録とワークツリーのパスを決める。<名前>
ig_set() {
	ig_n="$1"
	ig_mark="$ig_gdir/$1/marker"
	ig_wt="$WS/.claude/worktrees/$1"
}

# 記録を消す。消せなければ 1。
ig_drop_mark() {
	rm -f "$ig_mark" 2>/dev/null || :
	rmdir "$ig_gdir/$ig_n" 2>/dev/null || :
	rmdir "$ig_gdir" 2>/dev/null || :
	[ ! -e "$ig_mark" ]
}

# .claude/worktrees/<名前> がこのリポジトリのワークツリーなら、チェックアウトしている ref（refs/heads/...）を出す。
ig_wt_branch() {
	ig_want=$(ccnavi_phys "$ig_wt")
	git worktree list --porcelain 2>/dev/null |
		awk '/^worktree / { p = substr($0, 10) } /^branch / { print p "\t" substr($0, 8) }' |
		while IFS='	' read -r ig_p ig_br; do
			[ -n "$ig_p" ] || continue
			if [ "$(ccnavi_phys "$ig_p")" = "$ig_want" ]; then
				printf '%s\n' "$ig_br"
				break
			fi
		done
}

ig_short() {
	git rev-parse --short "$1" 2>/dev/null || printf '%s\n' "$1"
}

# integrate-doneの引数を読む。ig_n・ig_msg・ig_has_msgを決める。
ig_done_args() {
	ig_n=""
	ig_msg=""
	ig_has_msg=no
	ig_note=""
	ig_did=""
	while [ "$#" -gt 0 ]; do
		case "$1" in
		-m | --message)
			[ "$#" -ge 2 ] ||
				reject integrate-done-args "$1 にメッセージがありません（$SELF integrate-done <名前> -m <メッセージ>）。"
			ig_msg="$2"
			ig_has_msg=yes
			shift 2
			continue
			;;
		--message=*)
			ig_msg="${1#--message=}"
			ig_has_msg=yes
			;;
		-*)
			reject integrate-done-args "integrate-done の $1 は使用できません。使用できるのは -m <メッセージ> だけです（$SELF integrate-done <名前> [-m <メッセージ>]）。"
			;;
		*)
			[ -z "$ig_n" ] ||
				reject integrate-done-args "integrate-done は名前を 1 つだけ取ります（$ig_n と $1）。$SELF integrate-done <名前> [-m <メッセージ>] の形で実行してください。"
			ig_n="$1"
			;;
		esac
		shift
	done
	ig_name "$ig_n"
	if [ "$ig_has_msg" = yes ] && [ -z "$ig_msg" ]; then
		reject integrate-done-args "-m のメッセージが空です（$SELF integrate-done $ig_n -m <メッセージ>）。"
	fi
}

# ---- 直接作業の取り込み（取り込み先へ 1 コミットで載せる形。記録はversion 4）
#
# 切ったときの記録（ccnavi-worktree.shがgitの共通ディレクトリのccnavi-direct/<名前> に書く）から、
# 取り込み先のブランチと起点Sを読む。確認している間、ユーザのチェックアウトのindexとワークツリーには書かない。
#
#   integrate <名前>      作業ブランチの先頭の木からwip/handoff/ の差分をSに戻してTとし、
#                         merge-tree --write-tree --merge-base=S HEAD TでC（取り込み結果の木）を計算する。
#                         Cを一時ワークツリー（logs/direct-tmp/<repo>/<名前>）に展開し、markerと専用のref
#                         （refs/ccnavi/direct/<名前>/{t,c}）に残して止まる
#   integrate-done <名前> 一時ワークツリーでCを 1 コミットKにし（フックを通す）、Kの親と木を確かめてから、
#                         ユーザのチェックアウトでmerge --ff-only --no-overwrite-ignore Kを打つ。
#                         そのあとワークツリー・一時ワークツリー・ブランチ・ref・記録を片付ける。何度打ち直してもよい
#   integrate --abort     一時ワークツリーとrefとmarkerを消す。作業ワークツリーとユーザのチェックアウトには触らない
#
# markerは <ユーザのチェックアウトのgitディレクトリ>/ccnavi-integrate/<名前>/marker。

d4_lower() {
	printf '%s' "$1" | LC_ALL=C tr 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' 'abcdefghijklmnopqrstuvwxyz'
}

# 2 つのパスが、大文字小文字を区別せず同じなら 0。<パス> <パス>
d4_same_path() {
	[ "$(d4_lower "$1")" = "$(d4_lower "$2")" ]
}

# gitが <major>.<minor> 以上なら 0。<major> <minor>
d4_git_ge() {
	d4_ver=$(git version 2>/dev/null | sed -n 's/^git version \([0-9][0-9]*\)\.\([0-9][0-9]*\).*/\1 \2/p')
	[ -n "$d4_ver" ] || return 1
	d4_maj="${d4_ver% *}"
	d4_min="${d4_ver#* }"
	[ "$d4_maj" -gt "$1" ] || { [ "$d4_maj" -eq "$1" ] && [ "$d4_min" -ge "$2" ]; }
}

# 一時的なコミット（T）を作る。ユーザの設定とは関係なく、名前を固定する。
d4_commit_tree() {
	git -c user.name=ccnavi -c user.email=ccnavi@localhost commit-tree "$@"
}

# markerの置き場だけを調べる。ig_n・d4_pv（4 / none / それ以外は知らない版）を決める。<名前>
d4_probe() {
	ig_name "$1"
	d4_top0=$(git rev-parse --show-toplevel 2>/dev/null || :)
	[ -n "$d4_top0" ] ||
		reject not-a-repo "git リポジトリの中で実行してください（取り込み先のブランチをチェックアウトしているユーザのチェックアウトで $SELF_Q $sub ... を実行します）。"
	d4_g0=$(git rev-parse --git-path ccnavi-integrate 2>/dev/null || :)
	[ -n "$d4_g0" ] || reject integrate-no-gitdir "git ディレクトリの場所が読めません。ユーザに伝えてください。"
	case "$d4_g0" in
	/* | [A-Za-z]:*) ;;
	*) d4_g0="$(pwd)/$d4_g0" ;;
	esac
	d4_pv=none
	if [ -f "$d4_g0/$1/marker" ]; then
		d4_pv=$(ccnavi_record_get "$d4_g0/$1/marker" version)
		[ -n "$d4_pv" ] || d4_pv=無し
	fi
}

# 版を知らない記録で止まる。ccnavi が読めない記録なので、エージェントは触らず、ユーザが統合先を確かめて戻す。<名前>
d4_unknown_marker() {
	reject integrate-marker "取り込みの記録（${d4_g0}/${1}/marker）の版（${d4_pv}）を知りません。版 4 でない記録は ccnavi が読めません。ユーザが git status で統合先の staged を確かめ、不要なら git reset --merge で戻してから、${d4_g0}/${1} を手で消してください。エージェントは操作しません。"
}

# 引数のパス（git のトップ）が、ユーザのチェックアウト（git worktree list の先頭）か調べる。
#
# 先頭の場所は d4_main に残す（見つからなければ空）。d4_begin（integrate 系）と merge) が同じ判定を使う。
user_checkout_is() {
	d4_main=$(git worktree list --porcelain 2>/dev/null | sed -n '1s/^worktree //p')
	[ -n "$d4_main" ] && [ "$(ccnavi_phys "$1")" = "$(ccnavi_phys "$d4_main")" ]
}

# 共通部。名前・場所・記録の置き場を決める（ロックは取らない）。<名前>
#
# ユーザのチェックアウト（git worktree listの先頭）で打たれていることを求める。markerは
# ユーザのチェックアウトのgitディレクトリに置く。
d4_begin() {
	ig_name "$1"
	# ユーザのチェックアウトを読むだけの git が index を書き直さない（確認している間、ルートの index に書かない）。
	GIT_OPTIONAL_LOCKS=0
	export GIT_OPTIONAL_LOCKS
	ig_top=$(git rev-parse --show-toplevel 2>/dev/null || :)
	[ -n "$ig_top" ] ||
		reject not-a-repo "git リポジトリの中で実行してください（取り込み先のブランチをチェックアウトしているユーザのチェックアウトで $SELF_Q $sub ... を実行します）。"
	d4_common=$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null || :)
	[ -n "$d4_common" ] || reject integrate-no-gitdir "git の共通ディレクトリの場所が読めません。ユーザに伝えてください。"
	d4_common=$(ccnavi_phys "$d4_common")
	if ! user_checkout_is "$ig_top"; then
		reject integrate-not-main "$sub はユーザのチェックアウト（${d4_main:-見つかりません}）でだけ実行します。ここ（${ig_top}）は違います。cd してから、同じコマンドを再実行してください。"
	fi
	ig_gdir=$(git rev-parse --git-path ccnavi-integrate 2>/dev/null || :)
	case "$ig_gdir" in
	/* | [A-Za-z]:*) ;;
	*) ig_gdir="$(pwd)/$ig_gdir" ;;
	esac
	ig_set "$1"
	d4_repo=$(ccnavi_project "$ig_top" "$WS")
	[ -n "$d4_repo" ] || d4_repo=self
	d4_rec="$d4_common/ccnavi-direct/$ig_n"
	d4_done_file="${ig_gdir}-done/$ig_n"
	d4_ref="refs/ccnavi/direct/$ig_n"
	d4_tmp="$WS/logs/direct-tmp/$d4_repo/$ig_n"
	ig_head=$(git symbolic-ref --quiet --short HEAD 2>/dev/null || :)
}

# ロックを取る（キーはlogs/state/locks/<repo>/.integrate）。取れなければ断る。
d4_lock() {
	ccnavi_lock_take "$WS" "$d4_repo" .integrate 0 ||
		reject integrate-locked "別の integrate か integrate-done が動いています（ロック: logs/state/locks/${d4_repo}/.integrate）。終わってから再実行してください。"
	trap 'ccnavi_lock_drop' EXIT
	trap 'exit 130' INT
	trap 'exit 143' TERM
	trap 'exit 129' HUP
}

# 共通の前提チェック。実行した場所・ユーザのチェックアウト・作業ワークツリー・一時ワークツリーのgitの共通
# ディレクトリが、記録のcommon_dirと同じ実パスであること（存在するものについてのみ）。
d4_common_check() {
	d4_cc_want=$(ccnavi_record_get "$d4_rec" common_dir)
	[ -n "$d4_cc_want" ] || d4_cc_want=$(ccnavi_record_get "$d4_done_file" common_dir)
	[ -n "$d4_cc_want" ] || return 0
	d4_cc_want=$(ccnavi_phys "$d4_cc_want")
	for d4_cc_dir in "$ig_top" "$d4_main" "$ig_wt" "$d4_tmp"; do
		[ -d "$d4_cc_dir" ] || continue
		d4_cc_got=$(git -C "$d4_cc_dir" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || :)
		[ -n "$d4_cc_got" ] || continue
		if ! d4_same_path "$(ccnavi_phys "$d4_cc_got")" "$d4_cc_want"; then
			reject integrate-common-dir "$d4_cc_dir の git の共通ディレクトリ（$(ccnavi_phys "$d4_cc_got")）が、記録の common_dir（${d4_cc_want}）と違います。別のリポジトリの記録かもしれないので、何も変えずに止めます。ユーザに確かめてください。"
		fi
	done
}

# 取り込み先を開いていることを確かめる。<取り込み先>
d4_on_base() {
	[ "$ig_head" = "$1" ] ||
		reject integrate-not-base "ユーザのチェックアウトが取り込み先（${1}）を開いていません（今は ${ig_head:-ブランチの外（detached HEAD）}）。ブランチの切り替えはユーザに戻してもらってください（エージェントは checkout しない）。戻ったら同じコマンドを再実行します。"
}

# 取り込み先がorigin/<取り込み先> より進んでいれば 0（手元のrefだけで比べる）。<取り込み先>
d4_origin_behind() {
	git rev-parse --verify --quiet "refs/remotes/origin/$1^{commit}" >/dev/null 2>&1 || return 1
	d4_ob=$(git rev-list --count "HEAD..refs/remotes/origin/$1" 2>/dev/null || :)
	[ -n "$d4_ob" ] && [ "$d4_ob" -gt 0 ]
}

d4_origin_ahead() {
	git rev-parse --verify --quiet "refs/remotes/origin/$1^{commit}" >/dev/null 2>&1 || return 1
	d4_oa=$(git rev-list --count "refs/remotes/origin/$1..HEAD" 2>/dev/null || :)
	[ -n "$d4_oa" ] && [ "$d4_oa" -gt 0 ]
}

# 一時ワークツリーを片付ける（7 章）。<許すindexの木> [<許すindexの木>]
#
# 生成物（node_modules・.venv・__pycache__・.pytest_cache・.ruff_cache・package.jsonの隣のout）を消し、
# indexの木が許す木のどれかと同じで、追跡ファイルにそれ以上の変更が無く、未追跡のファイル（ignoredを含む）
# が残っていないときに限り、worktree remove --forceを打つ。満たさなければ削除せずに残し、理由をd4_tmp_whyに入れて 1。
d4_tmp_why=""
d4_tmp_remove() {
	d4_tmp_why=""
	if [ ! -e "$d4_tmp" ] && [ ! -L "$d4_tmp" ]; then
		# 登録だけが残っているときの後始末。prune は git の定義で「ディレクトリが無くなった登録」だけを
		# 外す（名前では絞れない）。ほかの名前の登録でも、ディレクトリが無ければ守るものは無い。
		git worktree prune 2>/dev/null || :
		return 0
	fi
	case "$d4_tmp" in
	"$WS"/logs/direct-tmp/*) ;;
	*)
		d4_tmp_why="場所（${d4_tmp}）が一時ワークツリーの置き場の外です"
		return 1
		;;
	esac
	if [ -L "$d4_tmp" ]; then
		d4_tmp_why="一時ワークツリー（${d4_tmp}）がリンクです"
		return 1
	fi
	find "$d4_tmp" \( -name node_modules -o -name .venv -o -name __pycache__ -o -name .pytest_cache -o -name .ruff_cache \) -type d -prune -exec rm -rf {} + 2>/dev/null || :
	find "$d4_tmp" -name package.json -type f -not -path '*/node_modules/*' 2>/dev/null | while IFS= read -r d4_pj; do
		d4_pd=$(dirname "$d4_pj")
		[ ! -d "$d4_pd/out" ] || rm -rf "$d4_pd/out" 2>/dev/null || :
	done
	d4_tr_idx=$(git -C "$d4_tmp" write-tree 2>/dev/null || :)
	if [ -z "$d4_tr_idx" ] || { [ "$d4_tr_idx" != "$1" ] && [ "$d4_tr_idx" != "${2:-}" ]; }; then
		d4_tmp_why="index の木が取り込み結果（C か K）と違います"
		return 1
	fi
	if ! git -C "$d4_tmp" diff --quiet 2>/dev/null; then
		d4_tmp_why="追跡ファイルに、取り込み結果以外の変更があります"
		return 1
	fi
	d4_tr_rest=$(
		git -C "$d4_tmp" ls-files --others --exclude-standard 2>/dev/null | head -n 5
		git -C "$d4_tmp" ls-files --others --ignored --exclude-standard 2>/dev/null | head -n 5
	)
	if [ -n "$d4_tr_rest" ]; then
		d4_tmp_why="未追跡のファイルが残っています（$(printf '%s' "$d4_tr_rest" | tr '\n' ' ')）"
		return 1
	fi
	if ! d4_tr_out=$(git worktree remove --force "$d4_tmp" 2>&1); then
		d4_tmp_why="worktree remove が通りませんでした（$(printf '%s' "$d4_tr_out" | head -n 1)）"
		return 1
	fi
	return 0
}

# 一時ワークツリーを消せなかったときの案内。
d4_tmp_note() {
	ccnavi_text "$WS" NOTE_GIT_TMP_LEFT "tmp=$d4_tmp" "why=$d4_tmp_why"
}

# 専用のrefを消す。<種類>...
# 全部にヒットしてから、消せなかったものがあれば 1 を返し、その種類を d4_rd_failed に入れる。
# すでに無い ref は消せたものと同じ（update-ref -d は 0）。準備と abort は戻り値を見ない（|| :）。
d4_rd_failed=""
d4_refs_drop() {
	d4_rd_failed=""
	for d4_rd in "$@"; do
		git update-ref -d "$d4_ref/$d4_rd" 2>/dev/null || d4_rd_failed="${d4_rd_failed:+$d4_rd_failed }$d4_rd"
	done
	[ -z "$d4_rd_failed" ]
}

# 下書きの条件（6.2）を作業ワークツリーについて確かめる。満たさなければ理由をd4_draft_whyに入れて 1。<S>
#
# - 許可リストにヒットする下書きは、取り込み済み（実行ファイルのtree handoff-pendingが 0 件）
# - wip/handoff/ に今あるファイルのうち、元（wip/handoff/ を除いたパスの S の版）があって中身が違うものが無い
#   （消えた下書きは数えない）。元の無い下書き（新規のメモ・報告）は通し、d4_draft_notes に集める。
#   元との突き合わせは実行ファイルが行う（大文字小文字と NFC/NFD の違いは同じパスとして扱う）
# - 元の無い下書きでも、まだコミットしていないものは通さない（ワークツリーを消すと失われるため）
d4_draft_why=""
d4_draft_notes=""
d4_draft_check() {
	d4_draft_why=""
	d4_draft_notes=""
	d4_dp_out=$(ccnavi_ask "$WS" tree handoff-pending "$ig_wt" "$1") || d4_dp_out=""
	if [ "$(printf '%s\n' "$d4_dp_out" | head -n 1)" != "tree 1" ]; then
		d4_draft_why="下書きの取り込み漏れを実行ファイルに確かめられない"
		return 1
	fi
	d4_dp_n=$(printf '%s\n' "$d4_dp_out" | sed -n 's/^pending //p' | head -n 1)
	if [ "${d4_dp_n:-1}" != 0 ]; then
		d4_draft_why="取り込んでいない下書きがある（$(printf '%s\n' "$d4_dp_out" | sed -n 's/^path //p' | tr '\n' ' ')）"
		return 1
	fi
	d4_dr=$(printf '%s\n' "$d4_dp_out" | sed -n 's/^foreign //p')
	d4_dn=$(printf '%s\n' "$d4_dp_out" | sed -n 's/^note //p')
	while IFS= read -r d4_f; do
		[ -n "$d4_f" ] || continue
		if GIT_LITERAL_PATHSPECS=1 git -C "$ig_wt" ls-files --error-unmatch -- "$d4_f" >/dev/null 2>&1; then
			d4_draft_notes="${d4_draft_notes:+$d4_draft_notes }$d4_f"
		else
			d4_dr="${d4_dr:+$d4_dr
}$d4_f"
		fi
	done <<EOF
$d4_dn
EOF
	if [ -n "$d4_dr" ]; then
		d4_draft_why="wip/handoff/ に、元と違う写し、またはまだコミットしていないファイルがある（$(printf '%s\n' "$d4_dr" | tr '\n' ' ' | sed 's/ $//')）"
		return 1
	fi
	return 0
}

# 作業ブランチの先頭の木から、wip/handoff/ の差分を<基準>に戻した木Tを作る。d4_t_treeに入れる。
# <基準> <先頭> [<S> <S0>]
#
# <S> <S0> を渡すと（作業ブランチが取り込み先を取り込んでいて、基準が S ではなく共通祖先のとき）、
# コピーした書きかけ（S0..S の差分）のうち、エージェントが S から変えていないパスも<基準>の版に戻す。
# コピーした書きかけそのものは成果に含めない。
#
# パスは -z で読む（引用符・バックスラッシュ・タブを含む名前を、引用符つきで受け取らない）。
# 改行を含む名前だけは扱えない。
d4_restore_path() {
	# <index> <基準> <パス>
	d4_ent=$(GIT_LITERAL_PATHSPECS=1 git ls-tree "$2" -- "$3" 2>/dev/null || :)
	if [ -n "$d4_ent" ]; then
		d4_mode=$(printf '%s\n' "$d4_ent" | awk '{ print $1 }')
		d4_sha=$(printf '%s\n' "$d4_ent" | awk '{ print $3 }')
		GIT_INDEX_FILE="$1" git update-index --add --cacheinfo "$d4_mode,$d4_sha,$3" 2>/dev/null
	else
		GIT_INDEX_FILE="$1" git update-index --force-remove -- "$3" 2>/dev/null
	fi
}

d4_make_t() {
	d4_idx=$(mktemp 2>/dev/null || mktemp -t ccnavi-idx) || return 1
	rm -f "$d4_idx"
	if ! GIT_INDEX_FILE="$d4_idx" git read-tree "$2" 2>/dev/null; then
		rm -f "$d4_idx"
		return 1
	fi
	d4_hp=$(git diff-tree -r -z --no-renames --name-only "$1" "$2" -- wip/handoff/ 2>/dev/null | tr '\0' '\n' || :)
	while IFS= read -r d4_p; do
		[ -n "$d4_p" ] || continue
		case "$d4_p" in
		wip/handoff/*) ;;
		*) continue ;;
		esac
		d4_restore_path "$d4_idx" "$1" "$d4_p" || {
			rm -f "$d4_idx"
			return 1
		}
	done <<EOF
$d4_hp
EOF
	if [ -n "${3:-}" ] && [ -n "${4:-}" ]; then
		d4_wp=$(git diff-tree -r -z --no-renames --name-only "$4" "$3" 2>/dev/null | tr '\0' '\n' || :)
		while IFS= read -r d4_p; do
			[ -n "$d4_p" ] || continue
			case "$d4_p" in
			wip/handoff/*) continue ;;
			esac
			d4_tb=$(GIT_LITERAL_PATHSPECS=1 git ls-tree "$2" -- "$d4_p" 2>/dev/null | awk '{ print $1 " " $3 }')
			d4_sb=$(GIT_LITERAL_PATHSPECS=1 git ls-tree "$3" -- "$d4_p" 2>/dev/null | awk '{ print $1 " " $3 }')
			[ "$d4_tb" = "$d4_sb" ] || continue
			d4_restore_path "$d4_idx" "$1" "$d4_p" || {
				rm -f "$d4_idx"
				return 1
			}
		done <<EOF
$d4_wp
EOF
	fi
	d4_t_tree=$(GIT_INDEX_FILE="$d4_idx" git write-tree 2>/dev/null || :)
	rm -f "$d4_idx"
	[ -n "$d4_t_tree" ]
}

# 変わるパスの一覧（1 行 1 本）。<git diff-tree の追加引数>... <木> <木>
d4_names() {
	git diff-tree -r -z --no-renames --name-only "$@" 2>/dev/null | tr '\0' '\n'
}

# パスの一覧（1 行 1 本）が自己防衛の対象を含むか、実行ファイルに聞く。d4_guard（yes / no / unknown）と
# d4_guard_pathsに入れる。聞けなければunknown（ターミナルからの確定を求める側に倒す）。<パスの一覧>
d4_guard_ask() {
	d4_guard=unknown
	d4_guard_paths=""
	[ -n "$1" ] || {
		d4_guard=no
		return 0
	}
	set --
	while IFS= read -r d4_gp; do
		[ -n "$d4_gp" ] || continue
		set -- "$@" "$d4_gp"
	done <<EOF
$d4_changed
EOF
	[ "$#" -gt 0 ] || {
		d4_guard=no
		return 0
	}
	d4_ga=$(ccnavi_ask "$WS" tree guarded "$ig_top" "$@") || d4_ga=""
	[ "$(printf '%s\n' "$d4_ga" | head -n 1)" = "tree 1" ] || return 0
	d4_guard=$(printf '%s\n' "$d4_ga" | sed -n 's/^guarded //p' | head -n 1)
	d4_guard_paths=$(printf '%s\n' "$d4_ga" | sed -n 's/^path //p' | tr '\n' ' ')
	case "$d4_guard" in
	yes | no) ;;
	*) d4_guard=unknown ;;
	esac
}

# integrate <名前>
ig_integrate() {
	[ "$#" -eq 1 ] ||
		reject integrate-args "integrate は名前を 1 つだけ取ります（$SELF_Q integrate <名前>。やめるなら $SELF_Q integrate --abort <名前>）。"
	d4_probe "$1"
	case "$d4_pv" in
	4 | none) ;;
	*) d4_unknown_marker "$1" ;;
	esac
	d4_begin "$1"
	log_info 受け付けた -- "sub=integrate" "name=$ig_n"
	d4_lock
	if [ -f "$ig_mark" ]; then
		d4_k0=$(ccnavi_record_get "$ig_mark" k)
		if [ -n "$d4_k0" ]; then
			reject integrate-has-k "$ig_n は確定の途中です（K=$(ig_short "$d4_k0")）。$SELF_Q integrate-done $ig_n -m '<メッセージ>' で続けるか、$SELF_Q integrate --abort $ig_n でやめてください。"
		fi
	fi
	if [ ! -f "$d4_rec" ]; then
		# チケットに結び付く名前は --adopt が断る。案内が行き止まりに向かわないよう、先に同じ判定
		# （ccnavi_tree_bound。--adopt と共通）で聞く。
		ccnavi_tree_bound "$WS" "$ig_wt" "$ig_n" || :
		if [ "$ccnavi_tb_state" = yes ]; then
			reject integrate-no-record "$ig_n の直接作業の記録がありません（ccnavi-direct/${ig_n}）。$ig_n はチケットに結び付く名前です（${ccnavi_tb_why}）。チケットの作業は直接作業に引き取らず、マージリクエストで出します（$TICKET_SH_Q status の次の操作に従う。.ccnavi/docs/worktree-merge.md）。"
		fi
		reject integrate-no-record "$ig_n の直接作業の記録がありません（ccnavi-direct/${ig_n}）。手で切ったワークツリーは、ユーザの確認を経て $WT_SH_Q $ig_n --adopt --base <取り込み先> で引き取ります。"
	fi
	d4_common_check
	d4_git_ge 2 40 ||
		reject integrate-old-git "git が古く（$(git version 2>/dev/null)）、merge-tree --merge-base（git 2.40 から）が利用できません。git を 2.40 以上にしてから再実行してください。"
	d4_base=$(ccnavi_record_get "$d4_rec" base_branch)
	d4_s_rec=$(ccnavi_record_get "$d4_rec" snap_sha)
	[ -n "$d4_base" ] && [ -n "$d4_s_rec" ] ||
		reject integrate-bad-record "直接作業の記録（${d4_rec}）に base_branch か snap_sha がありません。壊れた記録は自動では直しません。ユーザに確かめてください。"
	d4_on_base "$d4_base"
	d4_s=$(git rev-parse --verify --quiet "$d4_ref/s^{commit}" 2>/dev/null || :)
	[ -n "$d4_s" ] || d4_s=$(git rev-parse --verify --quiet "${d4_s_rec}^{commit}" 2>/dev/null || :)
	[ -n "$d4_s" ] ||
		reject integrate-no-s "起点 S（${d4_s_rec}）のコミットが見つかりません。ユーザに確かめてください。"
	# 取り込み先の判定（3.1）。no以外は断る。
	d4_bb=$(ccnavi_ask "$WS" tree base-bound "$ig_top" "$d4_base") || d4_bb=""
	if [ "$(printf '%s\n' "$d4_bb" | head -n 1)" != "tree 1" ]; then
		reject integrate-ticket "取り込み先 ${d4_base} がチケットに結び付くかを実行ファイルに確かめられないので、取り込みません。"
	fi
	case "$(printf '%s\n' "$d4_bb" | sed -n 's/^bound //p' | head -n 1)" in
	no) ;;
	yes)
		reject integrate-ticket "取り込み先 ${d4_base} はチケットに結び付いています（$(printf '%s\n' "$d4_bb" | sed -n 's/^why //p' | head -n 1)）。チケットの作業は取り込み先へ直接取り込まず、マージリクエストで出します。"
		;;
	*) reject integrate-ticket "取り込み先 ${d4_base} がチケットに結び付くかを決められないので、取り込みません。" ;;
	esac
	git show-ref --verify --quiet "refs/heads/$ig_n" ||
		reject integrate-no-branch "ブランチ $ig_n がありません。取り込むのは .claude/worktrees/<名前> のワークツリーと同じ名前のブランチです。"
	[ -d "$ig_wt" ] ||
		reject integrate-no-worktree "ワークツリー .claude/worktrees/$ig_n がありません。取り込むのは .claude/worktrees/<名前> のワークツリーと同じ名前のブランチです。"
	ig_b=$(ig_wt_branch)
	[ "$ig_b" = "refs/heads/$ig_n" ] ||
		reject integrate-worktree-branch ".claude/worktrees/$ig_n は、このリポジトリで $ig_n をチェックアウトしているワークツリーではありません（${ig_b:-このリポジトリの登録に無い}）。"
	# 名前のほうもチケットに結び付かないことを確かめる（取り込み先の判定とは別）。
	ccnavi_tree_bound "$WS" "$ig_wt" "$ig_n" || :
	case "$ccnavi_tb_state" in
	no) ;;
	yes)
		reject integrate-ticket "$ig_n はチケットに結び付くワークツリーです（${ccnavi_tb_why}）。チケットの作業は取り込み先へ直接取り込まず、マージリクエストで出します（.ccnavi/docs/worktree-merge.md）。"
		;;
	undecided) reject integrate-ticket "$ig_n がチケットに結び付くかを決められないので、取り込みません。" ;;
	*) reject integrate-ticket "$ig_n がチケットに結び付くかを実行ファイルに確かめられないので、取り込みません。" ;;
	esac
	ig_wdirty=$(git -C "$ig_wt" status --porcelain 2>/dev/null | head -n 10)
	if [ -n "$ig_wdirty" ]; then
		reject integrate-source-dirty "ワークツリー .claude/worktrees/$ig_n に未コミットの変更があります。取り込むのはコミットした分だけなので、ワークツリーの中でコミットしてから再実行してください。変更（先頭 10 件）: $(printf '%s' "$ig_wdirty" | tr '\n' ' ')"
	fi
	if ! d4_draft_check "$d4_s"; then
		reject integrate-draft "${d4_draft_why}。ユーザに cd $(ccnavi_sh_quote "$ig_wt") && ccnavi-user-handoff.sh を頼み、取り込んだ変更を作業ワークツリーでコミットしてください。要らない下書きは消してコミットします。"
	fi
	d4_tip=$(git rev-parse --verify "refs/heads/$ig_n^{commit}")
	if git ls-tree -r "$d4_tip" 2>/dev/null | grep -q '^160000 '; then
		reject integrate-submodule "$ig_n は submodule（gitlink）を含むので、直接作業の取り込みの対象外です。"
	fi
	d4_head=$(git rev-parse --verify HEAD)
	# 前回の一時ワークツリーとrefを片付ける（残っていれば）。
	d4_old_c=$(git rev-parse --verify --quiet "$d4_ref/c" 2>/dev/null || :)
	if [ -e "$d4_tmp" ] || [ -L "$d4_tmp" ]; then
		if ! d4_tmp_remove "${d4_old_c:-none}" "none"; then
			reject integrate-tmp-left "前の一時ワークツリー ${d4_tmp} を片付けられません（${d4_tmp_why}）。中身はユーザのものかもしれないので、黙って消しません。ユーザに確かめてください。"
		fi
	fi
	d4_refs_drop t c k || :
	# TとCを計算する。
	# 基準は、ふつう起点 S。作業ブランチが取り込み先を取り込んでいる（先頭と HEAD の共通祖先 M が、記録した
	# 確認時点の HEAD より新しい）ときは M にする。S のままだと、取り込み先側と同じ箇所を直した解決結果が
	# 「両側が変更」と読まれ、衝突を解いても integrate が収束しない。
	d4_s0=$(ccnavi_record_get "$d4_rec" base_sha)
	d4_mb=$(git merge-base HEAD "$d4_tip" 2>/dev/null || :)
	d4_bs="$d4_s"
	d4_wipsnap=""
	if [ -n "$d4_mb" ] && [ -n "$d4_s0" ] && [ "$d4_mb" != "$d4_s0" ] &&
		git merge-base --is-ancestor "$d4_s0" "$d4_mb" 2>/dev/null; then
		d4_bs="$d4_mb"
		[ "$d4_s" = "$d4_s0" ] || d4_wipsnap="$d4_s $d4_s0"
	fi
	# shellcheck disable=SC2086
	d4_make_t "$d4_bs" "$d4_tip" $d4_wipsnap ||
		reject integrate-t "作業ブランチの先頭の木（T）を作れませんでした。何も変えていません。"
	d4_t_commit=$(d4_commit_tree "$d4_t_tree" -p "$d4_bs" -m "ccnavi: T" 2>/dev/null || :)
	[ -n "$d4_t_commit" ] || reject integrate-t "T のコミットを作れませんでした。何も変えていません。"
	d4_mt_rc=0
	d4_mt=$(git merge-tree --write-tree --name-only --merge-base="$d4_bs" HEAD "$d4_t_commit" 2>/dev/null) || d4_mt_rc=$?
	if [ "$d4_mt_rc" -eq 1 ]; then
		printf 'fail  git integrate  %s の取り込みが衝突した（ユーザのチェックアウトは変えていない）\n' "$ig_n"
		printf '衝突したパス:\n'
		printf '%s\n' "$d4_mt" | sed -n '2,/^$/p' | grep -v '^$' | head -n "$MAX_LINES" | sed 's/^/  /'
		ccnavi_text "$WS" NOTE_GIT_INTEGRATE_CONFLICT "wt=$ig_wt" "base=$d4_base" "name=$ig_n" "self_q=$SELF_Q"
		log_error 取り込みが衝突した -- "name=$ig_n"
		exit 1
	fi
	if [ "$d4_mt_rc" -ne 0 ]; then
		printf 'fail  git integrate  %s の取り込み結果を計算できなかった（ユーザのチェックアウトは変えていない）\n' "$ig_n"
		exit 1
	fi
	d4_c=$(printf '%s\n' "$d4_mt" | head -n 1)
	git update-ref "$d4_ref/t" "$d4_t_commit" 2>/dev/null &&
		git update-ref "$d4_ref/c" "$d4_c" 2>/dev/null ||
		reject integrate-ref "専用の ref（${d4_ref}/t・c）を置けません。何も変えていません。"
	d4_head_tree=$(git rev-parse --verify "HEAD^{tree}")
	if [ "$d4_c" = "$d4_head_tree" ]; then
		ccnavi_record_write "$ig_mark" version 4 head "$d4_head" tip "$d4_tip" c_tree "$d4_c" tmp "" empty yes \
			created "$(date +%s)" ||
			reject integrate-marker "取り込みの記録（${ig_mark}）を書けません。ユーザに伝えてください。"
		printf 'ok  git integrate  %s に取り込む差分は無かった（作業ブランチの中身は %s に入っている）\n' "$ig_n" "$d4_base"
		ccnavi_text "$WS" NEXT_GIT_INTEGRATE_EMPTY "name=$ig_n"
		log_info 取り込む差分が無かった -- "name=$ig_n"
		exit 0
	fi
	d4_changed=$(d4_names "$d4_head_tree" "$d4_c" || :)
	d4_count=$(printf '%s\n' "$d4_changed" | awk 'NF { n++ } END { print n + 0 }')
	# ignoredのファイルとの重なり（案内。最終的な防御は --no-overwrite-ignore）。
	d4_added=$(d4_names --diff-filter=A "$d4_head_tree" "$d4_c" || :)
	d4_overlap=""
	while IFS= read -r d4_ap; do
		[ -n "$d4_ap" ] || continue
		if { [ -e "$ig_top/$d4_ap" ] || [ -L "$ig_top/$d4_ap" ]; } && git -C "$ig_top" check-ignore -q -- "$d4_ap" 2>/dev/null; then
			d4_overlap="$d4_overlap $d4_ap"
		fi
	done <<EOF
$d4_added
EOF
	d4_guard_ask "$d4_changed"
	# 一時ワークツリーにCを展開する。
	mkdir -p "${d4_tmp%/*}" 2>/dev/null || reject integrate-tmp "一時ワークツリーの置き場（${d4_tmp%/*}）を作れません。書き込める場所か確かめ、ユーザに伝えてください。"
	if ! d4_wa=$(git worktree add --detach "$d4_tmp" "$d4_head" 2>&1); then
		d4_wa=$(printf '%s' "$d4_wa" | tail -n 1)
		reject integrate-tmp "一時ワークツリー ${d4_tmp} を作れませんでした（${d4_wa}）。ユーザのチェックアウトは変えていません。原因を確かめ、ユーザに伝えてください。"
	fi
	if ! git -C "$d4_tmp" read-tree --reset -u "$d4_c" >/dev/null 2>&1; then
		git worktree remove --force "$d4_tmp" >/dev/null 2>&1 || :
		reject integrate-tmp "一時ワークツリーに取り込み結果を展開できませんでした。ユーザのチェックアウトは変えていません。ユーザに伝えてください。"
	fi
	ccnavi_record_write "$ig_mark" version 4 head "$d4_head" tip "$d4_tip" c_tree "$d4_c" tmp "$d4_tmp" empty no \
		created "$(date +%s)" || {
		d4_tmp_remove "$d4_c" "none" || :
		reject integrate-marker "取り込みの記録（${ig_mark}）を書けません。ユーザに伝えてください。"
	}
	printf 'ok  git integrate  %s の取り込み結果を一時ワークツリーに用意した（%d ファイル。ユーザのチェックアウトは変えていない）\n' "$ig_n" "$d4_count"
	git diff --stat "$d4_head_tree" "$d4_c" 2>/dev/null | head -n "$MAX_LINES"
	printf '変わるファイル:\n'
	printf '%s\n' "$d4_changed" | head -n "$MAX_LINES" | sed 's/^/  /'
	ccnavi_text "$WS" NOTE_GIT_INTEGRATE_SAVE_EDITS
	ccnavi_text "$WS" NOTE_GIT_INTEGRATE_VERIFY "tmp=$d4_tmp"
	[ -z "$d4_overlap" ] ||
		ccnavi_text "$WS" NOTE_GIT_INTEGRATE_IGNORED_OVERLAP "paths=${d4_overlap# }"
	[ -z "$d4_draft_notes" ] ||
		ccnavi_text "$WS" NOTE_GIT_INTEGRATE_DRAFTS "notes=$d4_draft_notes"
	if [ "$d4_guard" != no ]; then
		ccnavi_text "$WS" NOTE_GIT_INTEGRATE_GUARDED "paths=${d4_guard_paths:-対象を確かめられなかった}"
	fi
	if d4_origin_behind "$d4_base"; then
		ccnavi_text "$WS" NOTE_GIT_INTEGRATE_ORIGIN_BEHIND "base=$d4_base" "top=$ig_top"
	fi
	ccnavi_text "$WS" NEXT_GIT_INTEGRATE_REVIEW "head=$d4_head" "result=$d4_c" "name=$ig_n"
	log_info 取り込み結果を用意した -- "name=$ig_n" "files=$d4_count"
	exit 0
}

# integrate --abort <名前>
ig_abort() {
	[ "$#" -eq 1 ] ||
		reject integrate-args "integrate --abort は名前を 1 つだけ取ります（$SELF_Q integrate --abort <名前>）。"
	d4_probe "$1"
	case "$d4_pv" in
	4 | none) ;;
	*) d4_unknown_marker "$1" ;;
	esac
	d4_begin "$1"
	log_info 受け付けた -- "sub=integrate" "action=abort" "name=$ig_n"
	d4_lock
	d4_common_check
	d4_m_k=$(ccnavi_record_get "$ig_mark" k)
	[ -n "$d4_m_k" ] || d4_m_k=$(git rev-parse --verify --quiet "$d4_ref/k" 2>/dev/null || :)
	if [ -n "$d4_m_k" ] && git cat-file -e "${d4_m_k}^{commit}" 2>/dev/null &&
		git merge-base --is-ancestor "$d4_m_k" HEAD 2>/dev/null; then
		reject integrate-abort-landed "K（$(ig_short "$d4_m_k")）は取り込み先にもう載っています。やめるなら、ユーザが git revert します。片付けは $SELF_Q integrate-done $ig_n で終わらせます。"
	fi
	if [ ! -f "$ig_mark" ] && [ ! -e "$d4_tmp" ] && [ ! -L "$d4_tmp" ] &&
		[ -z "$(git for-each-ref "$d4_ref/t" "$d4_ref/c" "$d4_ref/k" 2>/dev/null)" ]; then
		reject integrate-abort-none "$ig_n の取り込みの記録も一時ワークツリーも ref もありません。戻すものはありません。"
	fi
	d4_a_c=$(ccnavi_record_get "$ig_mark" c_tree)
	d4_a_ktree=""
	[ -z "$d4_m_k" ] || d4_a_ktree=$(git rev-parse --verify --quiet "${d4_m_k}^{tree}" 2>/dev/null || :)
	d4_a_left=no
	if ! d4_tmp_remove "${d4_a_c:-none}" "${d4_a_ktree:-none}"; then
		d4_a_left=yes
	fi
	d4_refs_drop t c k || :
	ig_drop_mark ||
		reject integrate-abort-marker "取り込みの記録（${ig_mark}）を削除できませんでした。ユーザに伝えてください。"
	printf 'ok  git integrate  %s の取り込みをやめた（作業ワークツリーとユーザのチェックアウトは変えていない）\n' "$ig_n"
	[ "$d4_a_left" = no ] || d4_tmp_note
	ccnavi_text "$WS" NOTE_GIT_INTEGRATE_ABORTED "self_q=$SELF_Q" "name=$ig_n"
	log_info 取り込みをやめた -- "name=$ig_n"
	exit 0
}

# 片付けを途中で止める（v4）。<理由> <識別子>
d4_stop() {
	printf 'fail  git integrate-done  %s の片付けを途中で止めた\n' "$ig_n"
	printf '理由: %s\n' "$1"
	[ -z "$ig_did" ] || printf '済んだこと: %s\n' "$ig_did"
	d4_kept=""
	{ [ -e "$ig_wt" ] || [ -L "$ig_wt" ]; } && d4_kept="ワークツリー .claude/worktrees/$ig_n"
	{ [ -e "$d4_tmp" ] || [ -L "$d4_tmp" ]; } && d4_kept="${d4_kept:+${d4_kept}、}一時ワークツリー"
	git show-ref --verify --quiet "refs/heads/$ig_n" && d4_kept="${d4_kept:+${d4_kept}、}ブランチ $ig_n"
	[ -f "$ig_mark" ] && d4_kept="${d4_kept:+${d4_kept}、}取り込みの記録"
	[ -z "$d4_kept" ] || printf '残したもの: %s\n' "$d4_kept"
	ccnavi_text "$WS" NOTE_GIT_DONE_STOPPED "self_q=$SELF_Q" "name=$ig_n"
	log_info 片付けを止めた -- "name=$ig_n" "reason=${2:-}"
	exit 1
}

# 片付け（6.1 の 7）。順番は固定: ① doneの記録 → ② 作業ワークツリー → ③ 一時ワークツリー → ④ ブランチ →
# ⑤ 専用のrefと直接作業の記録 → ⑥ marker。
d4_finish() {
	d4_ddir="${ig_gdir}-done"
	mkdir -p "$d4_ddir" 2>/dev/null || :
	d4_ts=$(git rev-parse --verify --quiet "$d4_ref/t" 2>/dev/null || :)
	d4_ss=$(git rev-parse --verify --quiet "$d4_ref/s" 2>/dev/null || :)
	if ! ccnavi_record_write "$d4_done_file" version 4 k "${d4_m_k:--}" s "${d4_ss:--}" t "${d4_ts:--}" c_tree "$d4_m_ctree" \
		head "$d4_m_head" base_branch "$d4_base" common_dir "$d4_common" created "$(date +%s)"; then
		log_info 取り込みのコピーを残せなかった -- "name=$ig_n"
	fi
	# ブランチの先頭が動いていたら、作業ワークツリー（②）を消す前に止める。消してからでは、積まれた
	# コミットを作業ワークツリーから確かめられなくなる。
	if git show-ref --verify --quiet "refs/heads/$ig_n"; then
		d4_bpre=$(git rev-parse --verify "refs/heads/$ig_n" 2>/dev/null || :)
		if [ "$d4_bpre" != "$d4_m_tip" ]; then
			d4_stop "ブランチ $ig_n の先頭が取り込んだとき（$(ig_short "$d4_m_tip")）から動いている（今は $(ig_short "$d4_bpre")）。後から積んだコミットは取り込まれていないので、何も削除していません" branch-moved
		fi
	fi
	# ② 作業ワークツリー
	if [ -e "$ig_wt" ] || [ -L "$ig_wt" ]; then
		ig_wb=$(ig_wt_branch)
		if [ "$ig_wb" != "refs/heads/$ig_n" ]; then
			d4_stop "ワークツリー .claude/worktrees/$ig_n が、いまブランチ $ig_n を開いていない（${ig_wb:-detached HEAD かこのリポジトリの登録に無い}）。ここで積んだコミットは、削除すると戻せないので、何も削除していません。ワークツリーの中で $ig_n に戻すか、積んだコミットの扱いをユーザに確かめる" worktree-branch
		fi
		d4_wd=$(git -C "$ig_wt" status --porcelain 2>/dev/null | head -n 10)
		if [ -n "$d4_wd" ]; then
			d4_stop "ワークツリー .claude/worktrees/$ig_n に未コミットの変更があるので、削除していません。変更（先頭 10 件）: $(printf '%s' "$d4_wd" | tr '\n' ' ')" worktree-dirty
		fi
		if ! d4_draft_check "${d4_ss:-$d4_m_head}"; then
			d4_stop "${d4_draft_why}。ワークツリーは削除していません" worktree-draft
		fi
		ig_cl_rc=0
		ig_cl=$(sh "$(dirname "$0")/ccnavi-clean.sh" --worktree "$ig_n" 2>&1) || ig_cl_rc=$?
		if [ "$ig_cl_rc" -ne 0 ]; then
			d4_stop "ワークツリー .claude/worktrees/$ig_n を削除できませんでした。ccnavi-clean.sh --worktree の出力: $ig_cl" worktree
		fi
		ig_did="${ig_did}ワークツリー .claude/worktrees/$ig_n を削除しました。"
	fi
	# ③ 一時ワークツリー
	d4_ktree=""
	[ -z "$d4_m_k" ] || d4_ktree=$(git rev-parse --verify --quiet "${d4_m_k}^{tree}" 2>/dev/null || :)
	if ! d4_tmp_remove "$d4_m_ctree" "${d4_ktree:-none}"; then
		d4_stop "一時ワークツリー ${d4_tmp} を片付けられない（${d4_tmp_why}）。中身はユーザのものかもしれないので、黙って消さない" tmp
	fi
	ig_did="${ig_did}一時ワークツリーを片付けました。"
	# ④ ブランチ（記録した先頭のままのときだけ消す）
	if git show-ref --verify --quiet "refs/heads/$ig_n"; then
		d4_bnow=$(git rev-parse --verify "refs/heads/$ig_n" 2>/dev/null || :)
		if [ "$d4_bnow" != "$d4_m_tip" ]; then
			d4_stop "ブランチ $ig_n の先頭が取り込んだとき（$(ig_short "$d4_m_tip")）から動いている（今は $(ig_short "$d4_bnow")）。後から積んだコミットは取り込まれていないので、ブランチを削除していません" branch-moved
		fi
		if ! d4_bd=$(git update-ref -d "refs/heads/$ig_n" "$d4_m_tip" 2>&1); then
			d4_stop "ブランチ $ig_n を削除できませんでした（$(printf '%s' "$d4_bd" | head -n 1)）。ワークツリーの登録が残っているなら $SELF_Q worktree prune で外してから再実行します" branch
		fi
		ig_did="${ig_did}ブランチ $ig_n を削除しました（先頭は $(ig_short "$d4_m_tip")）。"
	fi
	# ⑤ 専用のrefと直接作業の記録
	d4_refs_drop t c k s pending ||
		d4_stop "専用の ref（${d4_ref}/ の ${d4_rd_failed}）を削除できませんでした。直接作業の記録と取り込みの記録は残しています" refs
	rm -f "$d4_rec" 2>/dev/null || :
	rmdir "$d4_common/ccnavi-direct" 2>/dev/null || :
	# ⑥ marker
	ig_drop_mark ||
		d4_stop "取り込みの記録（${ig_mark}）を削除できませんでした。" marker
	printf 'ok  git integrate-done  %s を片付けた。%s\n' "$ig_n" "${ig_did:-片付けるものは記録だけだった}"
	[ -z "${ig_note:-}" ] || printf '案内: %s\n' "$ig_note"
	log_info 取り込みを片付けた -- "name=$ig_n"
	# 8: pushはユーザが打つ。
	if [ -n "$d4_m_k" ] && d4_origin_ahead "$d4_base"; then
		ccnavi_text "$WS" NEXT_GIT_PUSH_USER "base=$d4_base" "top=$ig_top"
	fi
	exit 0
}

# integrate-done <名前> [-m <メッセージ>]
ig_done() {
	ig_done_args "$@"
	d4_probe "$ig_n"
	case "$d4_pv" in
	none)
		d4_nb=no
		git show-ref --verify --quiet "refs/heads/$ig_n" && d4_nb=yes
		if [ "$d4_nb" = no ] && [ ! -e "$WS/.claude/worktrees/$ig_n" ] && [ ! -L "$WS/.claude/worktrees/$ig_n" ]; then
			printf 'ok  git integrate-done  %s は片付け済み（ワークツリーもブランチも取り込みの記録もありません）\n' "$ig_n"
			exit 0
		fi
		reject integrate-done-no-marker "$ig_n の取り込みの記録がありません。先に $SELF_Q integrate $ig_n で取り込み結果を用意し、中身を確かめてから実行してください（記録の無いブランチは、取り込んだことを確かめずには消しません）。"
		;;
	4) ;;
	*) d4_unknown_marker "$ig_n" ;;
	esac
	d4_begin "$ig_n"
	log_info 受け付けた -- "sub=integrate-done" "name=$ig_n" "message=$ig_has_msg"
	d4_lock
	d4_common_check
	d4_git_ge 2 40 ||
		reject integrate-old-git "git が古く（$(git version 2>/dev/null)）、git 2.40 未満では扱えません。git を更新してから再実行してください。"
	d4_base=$(ccnavi_record_get "$d4_rec" base_branch)
	[ -n "$d4_base" ] || d4_base=$(ccnavi_record_get "$d4_done_file" base_branch)
	[ -n "$d4_base" ] ||
		reject integrate-bad-record "取り込み先を記録から読めません（${d4_rec}）。壊れた記録は自動では直しません。ユーザに確かめてください。"
	d4_on_base "$d4_base"
	d4_m_head=$(ccnavi_record_get "$ig_mark" head)
	d4_m_tip=$(ccnavi_record_get "$ig_mark" tip)
	d4_m_ctree=$(ccnavi_record_get "$ig_mark" c_tree)
	d4_m_k=$(ccnavi_record_get "$ig_mark" k)
	d4_m_empty=$(ccnavi_record_get "$ig_mark" empty)
	[ -n "$d4_m_head" ] && [ -n "$d4_m_tip" ] && [ -n "$d4_m_ctree" ] ||
		reject integrate-bad-marker "取り込みの記録（${ig_mark}）の中身が足りません。自動では取り込みません。$SELF_Q integrate --abort $ig_n で戻して、打ち直してください。"
	d4_cur=$(git rev-parse --verify HEAD)
	if [ -n "$d4_m_k" ]; then
		# 2: Kを作ったあとの再開。
		if ! git cat-file -e "${d4_m_k}^{commit}" 2>/dev/null ||
			[ "$(git rev-parse --verify "${d4_m_k}^" 2>/dev/null || :)" != "$d4_m_head" ] ||
			[ "$(git rev-parse --verify "${d4_m_k}^{tree}" 2>/dev/null || :)" != "$d4_m_ctree" ]; then
			reject integrate-done-bad-k "記録の K（${d4_m_k}）の親か木が、記録の head・c_tree と合いません。自動では取り込みません。ユーザに確かめてください。"
		fi
		if git merge-base --is-ancestor "$d4_m_k" HEAD 2>/dev/null; then
			d4_finish
		fi
		if [ "$d4_cur" != "$d4_m_head" ]; then
			reject integrate-done-moved "K を作ったあとに ${d4_base} の HEAD が動きました（記録 $(ig_short "$d4_m_head")、今 $(ig_short "$d4_cur")）。$SELF_Q integrate --abort $ig_n で戻してから、$SELF_Q integrate $ig_n を打ち直してください。"
		fi
	else
		# 3: Kが無い場合の前提。
		if [ "$d4_cur" != "$d4_m_head" ]; then
			reject integrate-done-moved "確認したあとに ${d4_base} の HEAD が動きました（記録 $(ig_short "$d4_m_head")、今 $(ig_short "$d4_cur")）。$SELF_Q integrate --abort $ig_n で戻してから、$SELF_Q integrate $ig_n を打ち直してください。"
		fi
		d4_tip_now=$(git rev-parse --verify --quiet "refs/heads/$ig_n^{commit}" 2>/dev/null || :)
		if [ "$d4_tip_now" != "$d4_m_tip" ]; then
			reject integrate-done-branch-moved "作業ブランチ $ig_n の先頭が確認したとき（$(ig_short "$d4_m_tip")）から動いています（今 ${d4_tip_now:+$(ig_short "$d4_tip_now")}${d4_tip_now:-無し}）。$SELF_Q integrate --abort $ig_n で戻してから、$SELF_Q integrate $ig_n を打ち直してください。"
		fi
		d4_wdirty=$(git -C "$ig_wt" status --porcelain 2>/dev/null | head -n 10)
		if [ -d "$ig_wt" ] && [ -n "$d4_wdirty" ]; then
			reject integrate-done-source-dirty "ワークツリー .claude/worktrees/$ig_n に未コミットの変更があります。コミットしてから $SELF_Q integrate --abort $ig_n と integrate を打ち直してください。変更（先頭 10 件）: $(printf '%s' "$d4_wdirty" | tr '\n' ' ')"
		fi
		d4_ss0=$(git rev-parse --verify --quiet "$d4_ref/s^{commit}" 2>/dev/null || :)
		if [ -d "$ig_wt" ] && ! d4_draft_check "${d4_ss0:-$d4_m_head}"; then
			reject integrate-done-draft "${d4_draft_why}。ユーザに cd $(ccnavi_sh_quote "$ig_wt") && ccnavi-user-handoff.sh を頼み、取り込んだ変更をコミットしてから、integrate を打ち直してください。"
		fi
		if [ "$d4_m_empty" = yes ]; then
			d4_finish
		fi
		if [ ! -d "$d4_tmp" ]; then
			reject integrate-done-no-tmp "一時ワークツリー（${d4_tmp}）がありません。$SELF_Q integrate --abort $ig_n で戻してから、integrate を打ち直してください。"
		fi
		if [ "$(git -C "$d4_tmp" write-tree 2>/dev/null || :)" != "$d4_m_ctree" ] || ! git -C "$d4_tmp" diff --quiet 2>/dev/null; then
			reject integrate-done-tmp-differs "一時ワークツリーの中身が、確認した取り込み結果（C）と違います（検証で追跡ファイルを書き換えていないか）。$SELF_Q integrate --abort $ig_n で戻してから、integrate を打ち直してください。"
		fi
		d4_changed=$(d4_names "HEAD^{tree}" "$d4_m_ctree" || :)
		d4_guard_ask "$d4_changed"
		if [ "$d4_guard" != no ] && [ ! -t 0 ]; then
			reject integrate-done-terminal "自己防衛の対象（${d4_guard_paths:-対象を確かめられなかった}）を含む取り込みは、ユーザがターミナルで、エージェントのターンが終わってから打ちます（fast-forward で設定ファイルが書き換わると、ターンの終わりの照合が元に戻してしまうため）。ユーザに $SELF_Q integrate-done $ig_n -m '<メッセージ>' を頼んでください。"
		fi
		# K を commit したあと、記録する前に落ちた打ち直しは、一時ワークツリーの HEAD が既に K になっている。
		# 親が head、木が c_tree ならそれを採用して記録する（commit し直さない）。
		d4_newk=$(git -C "$d4_tmp" rev-parse --verify HEAD 2>/dev/null || :)
		if [ -n "$d4_newk" ] && [ "$d4_newk" != "$d4_m_head" ] &&
			[ "$(git rev-parse --verify "${d4_newk}^" 2>/dev/null || :)" = "$d4_m_head" ] &&
			[ "$(git rev-parse --verify "${d4_newk}^{tree}" 2>/dev/null || :)" = "$d4_m_ctree" ]; then
			d4_reuse=yes
		else
			d4_reuse=no
		fi
		if [ "$d4_reuse" = no ]; then
			[ "$ig_has_msg" = yes ] ||
				reject integrate-done-no-message "取り込みをコミットするので、メッセージが要ります（$SELF_Q integrate-done $ig_n -m <メッセージ>）。"
			# 4: 一時ワークツリーでKをコミットする（フックを通す）。
			d4_out=$(mktemp 2>/dev/null || mktemp -t ccnavi-integrate) ||
				reject integrate-tmp-file "一時ファイルが作れないので、コミットしていません。TMPDIR に書けるかを確かめてください。"
			d4_crc=0
			git -C "$d4_tmp" commit -q -m "$ig_msg" >"$d4_out" 2>&1 || d4_crc=$?
			if [ "$d4_crc" -ne 0 ]; then
				d4_why=$(ccnavi_git_refusal "$d4_out")
				rm -f "$d4_out"
				printf 'fail  git integrate-done  %s をコミットできなかった（%s）。ユーザのチェックアウトは変えていない\n' "$ig_n" "$d4_why"
				ccnavi_text "$WS" NOTE_GIT_DONE_COMMIT_FAILED "self_q=$SELF_Q" "name=$ig_n"
				log_error コミットできなかった -- "name=$ig_n" "exit=$d4_crc"
				exit 1
			fi
			rm -f "$d4_out"
			d4_newk=$(git -C "$d4_tmp" rev-parse --verify HEAD)
			if [ "$(git rev-parse --verify "${d4_newk}^" 2>/dev/null || :)" != "$d4_m_head" ] ||
				[ "$(git rev-parse --verify "${d4_newk}^{tree}" 2>/dev/null || :)" != "$d4_m_ctree" ]; then
				# フックが中身を変えた。Kは記録せず、参照の無いオブジェクトとして残す。一時ワークツリーをCに戻す。
				git -C "$d4_tmp" reset -q --hard "$d4_m_head" >/dev/null 2>&1 || :
				git -C "$d4_tmp" read-tree --reset -u "$d4_m_ctree" >/dev/null 2>&1 || :
				printf 'fail  git integrate-done  %s のコミットのフックが、確認した取り込み結果と違う中身にした。記録せずに止めた（ユーザのチェックアウトは変えていない）\n' "$ig_n"
				ccnavi_text "$WS" NOTE_GIT_DONE_HOOK_CHANGED "self_q=$SELF_Q" "name=$ig_n"
				log_error フックが中身を変えた -- "name=$ig_n"
				exit 1
			fi
		fi
		git update-ref "$d4_ref/k" "$d4_newk" 2>/dev/null || :
		ccnavi_record_write "$ig_mark" version 4 head "$d4_m_head" tip "$d4_m_tip" c_tree "$d4_m_ctree" tmp "$d4_tmp" \
			empty no k "$d4_newk" created "$(date +%s)" ||
			reject integrate-marker "K（${d4_newk}）の記録（${ig_mark}）を書けません。ユーザに伝えてください。打ち直せば、一時ワークツリーの K を採用して記録し直します。"
		d4_m_k="$d4_newk"
	fi
	# 5: ユーザのチェックアウトでfast-forward。
	d4_ffout=$(mktemp 2>/dev/null || mktemp -t ccnavi-integrate) ||
		reject integrate-tmp-file "一時ファイルが作れないので、取り込んでいません。"
	d4_ffrc=0
	git merge --ff-only --no-overwrite-ignore "$d4_m_k" >"$d4_ffout" 2>&1 || d4_ffrc=$?
	if [ "$d4_ffrc" -ne 0 ]; then
		d4_why=$(ccnavi_git_refusal "$d4_ffout")
		rm -f "$d4_ffout"
		printf 'fail  git integrate-done  %s を取り込めなかった（%s）。ユーザのチェックアウトは変えていない\n' "$ig_n" "$d4_why"
		ccnavi_text "$WS" NOTE_GIT_DONE_FF_FAILED "self_q=$SELF_Q" "name=$ig_n" "short=$(ig_short "$d4_m_k")"
		log_error 取り込めなかった -- "name=$ig_n" "exit=$d4_ffrc"
		exit 1
	fi
	rm -f "$d4_ffout"
	# 6
	ig_did="取り込みました（K=$(ig_short "$d4_m_k")）。"
	printf 'ok  git integrate-done  %s を %s に取り込みました（K=%s）\n' "$ig_n" "$d4_base" "$(ig_short "$d4_m_k")"
	printf '変わったファイル:\n'
	d4_names "${d4_m_k}^" "$d4_m_k" | head -n "$MAX_LINES" | sed 's/^/  /'
	d4_finish
}

case "$sub" in
integrate)
	# 直接作業の取り込み（版 4。d4_ 系）。
	if [ "${1:-}" = --abort ]; then
		shift
		ig_abort ${1+"$@"}
	fi
	ig_integrate ${1+"$@"}
	;;
integrate-done)
	ig_done ${1+"$@"}
	;;
*)
	: # ほかのサブコマンドは、上の `ccnavi git check` が通した
	;;
esac

# リモートから消えた親のブランチへは送らない（push のゲート。write-and-push・ccnavi-user-push-approved.sh と同じ）。
# 普通の push で作り直すと、消えた理由（改名・消し間違い・破棄した親子のチケット）を確かめないまま親子のチケットが
# 動き出す。ゲートを掛けるか（送り先が origin で、ワークツリーから送る）は実行ファイルが決め、ls-remote はここで打つ。
if [ -n "$gc_gate_top" ]; then
	push_gate_rc=0
	# 秒数は write-and-push・ccnavi-user-push-approved.sh と同じ既定（60 秒。CCNAVI_WRITE_AND_PUSH_TIMEOUT で変えられる）。
	case "${CCNAVI_WRITE_AND_PUSH_TIMEOUT:-}" in
	'' | *[!0-9]*) push_gate_secs=60 ;;
	*) push_gate_secs="$CCNAVI_WRITE_AND_PUSH_TIMEOUT" ;;
	esac
	ccnavi_push_gate "$gc_gate_top" "$gc_gate_branch" "${gc_gate_top##*/}" "$WS_P" "$push_gate_secs" || push_gate_rc=$?
	case "$push_gate_rc" in
	0) ;;
	1) reject push-parent-missing "${ccnavi_pg_why}。エージェントは戻せない（生の git は利用できない）ので、ユーザに伝えて止まってください。" ;;
	*) reject push-parent-unverified "${ccnavi_pg_why}。直せなければユーザに伝えて止まってください（生の git で押し通さない）。" ;;
	esac
fi

# 外部 diff ドライバは設定にも書けるので、読む系では毎回無効にして呼ぶ。
case "$sub" in
diff | show | log | whatchanged) set -- --no-ext-diff ${1+"$@"} ;;
esac

root=$(git rev-parse --show-toplevel 2>/dev/null || :)
[ -z "$root" ] && reject not-a-repo "git リポジトリの中で実行してください。"
log_info 受け付けた -- "sub=$sub" "args=$#"
log_debug 判定の材料 -- "sub=$sub" "action=${action:-}" "top=$root" "cwd=$PWD" "workspace=$WS"

# 記録はワークスペースの下にまとめる（git のトップとワークスペースルートは別で、モード B では異なるため）。git のトップに書くと、
# モード B ではプロジェクトのリポジトリの中に出る。ワークスペースの .gitignore の
# /logs/ はワークスペースルート起点なので該当せず、public のリポジトリに運用の痕跡が入る。
logproject=$(ccnavi_project "$(pwd)" "$WS")
if [ -n "$logproject" ]; then
	logdir="$WS/logs/$logproject"
else
	logdir="$WS/logs"
fi
mkdir -p "$logdir"
logfile="$logdir/git-$(date '+%Y%m%d-%H%M%S')-$$.log"

# 全量を必ず残す。成功でも書く。破棄すると「あのとき何が出ていたか」を後から
# 確かめられない。logs/ は .gitignore に入っていて、コミット対象にならない。
{
	printf '# %s  cwd=%s\n' "$(date '+%Y-%m-%dT%H:%M:%S')" "$(pwd)"
	printf '$ git %s' "$sub"
	for arg in ${1+"$@"}; do printf ' %s' "$arg"; done
	printf '\n--- 出力 ---\n'
} >"$logfile"

# ネットワークに出る git には、gh / glab の資格情報を使わせる（認証済みなら git の認証も通る）。
case "$sub" in
push | fetch | pull | ls-remote) ccnavi_git_auth . ;;
esac

status=0
git --no-pager "$sub" ${1+"$@"} >>"$logfile" 2>&1 || status=$?

# fetch を試して失敗した印（worktree add の起点の確認が、オフラインで詰まらないように使う）。
# 成功すれば消す。FETCH_HEAD の更新時刻は成功した fetch でしか進まない。
if [ "$sub" = fetch ]; then
	fetch_common=$(wt_fresh_dir) || fetch_common=""
	if [ -n "$fetch_common" ]; then
		if [ "$status" -eq 0 ]; then
			rm -f "$fetch_common/ccnavi-fetch-failed"
		else
			: >"$fetch_common/ccnavi-fetch-failed" 2>/dev/null || :
		fi
	fi
fi

# 記録のパス。エージェントがそのまま sed -n で開ける形で返す。
#
# 基準はワークスペースルート。モード B ではエージェントの cwd がプロジェクトの中に
# あるので、git のトップからの相対を返すと届かない。ワークスペースの中に居るときは
# 短い相対、そうでなければ絶対を返す（git のトップとワークスペースルートは別だから）。
case "$logfile" in
"$WS"/*) logrel="${logfile#"$WS"/}" ;;
*) logrel="$logfile" ;;
esac
# cwd から相対で開けないなら、絶対パスをそのまま返す。2 つ並べない。
# 並べると、受け取った側がどちらを開くか迷い、パスの切り出しも要る。
if [ ! -f "$logrel" ]; then
	logrel="$logfile"
fi

# 本文は目印の次の行から。目印と同じ行が出力に含まれても、awk は最初の 1 件で
# f を立て、それより後ろだけを出すので取り違えない。
body() { awk 'f; /^--- 出力 ---$/ { f = 1 }' "$logfile"; }

lines=$(body | awk 'END { print NR + 0 }')

# 要約は「次の判断に使う数値」だけにする。本文をもう 1 度読ませないためのもので、
# 本文の代わりではない。
summary=$(body | awk -v cmd="$sub" '
	/^diff --git /			{ files++ }
	/^\+/ && !/^\+\+\+/		{ plus++ }
	/^-/ && !/^---/			{ minus++ }
	/^commit [0-9a-f]/		{ commits++ }
	/^[ MADRCU?!][ MADRCU?!] /	{ entries++ }
	/^\t/				{ entries++ }
					{ n++ }
	END {
		if ((cmd == "diff" || cmd == "show") && files > 0)
			printf "%d ファイル +%d -%d", files, plus, minus
		else if ((cmd == "log" || cmd == "shortlog") && commits > 0)
			printf "%d コミット", commits
		else if (cmd == "status")
			printf "変更 %d 件", entries + 0
		else
			printf "%d 行", n + 0
	}')

if [ "$status" -eq 0 ]; then
	printf 'ok  git %s  %s  log=%s\n' "$sub" "$summary" "$logrel"
	body | head -n "$MAX_LINES"
	if [ "$lines" -gt "$MAX_LINES" ]; then
		printf '... 残り %d 行は %s にある（全文は Read ツールでこのファイルを読む）\n' "$((lines - MAX_LINES))" "$logrel"
	fi
	if [ -n "$gc_warn" ]; then
		printf '警告: %s\n' "$gc_warn"
	fi
else
	printf 'fail  git %s  exit=%d  log=%s\n' "$sub" "$status" "$logrel"
	body | tail -n "$FAIL_LINES"
	# Windows では、プロセスの cwd になっているディレクトリは使用中になる。Bash ツールの cwd は呼び出しを
	# またいで残る親のシェルのものなので、ワークツリーの中へ cd したまま remove すると、git が
	# 中身を消したあと最後のディレクトリで Permission denied になり、空のディレクトリが残る。
	# サブシェルの中で cd してから打っても防げず、ここで pwd を見ても親の cwd は分からないので、
	# 起きたときに直し方を言う。
	if [ "$sub" = worktree ] && [ "${action:-}" = remove ] && body | grep -q 'Permission denied'; then
		rm_path_q='<パス>'
		[ -z "${2:-}" ] || rm_path_q=$(ccnavi_sh_quote "$2")
		ccnavi_text "$WS" NOTE_GIT_WORKTREE_REMOVE_CWD "ws=$WS" "self_q=$SELF_Q" "path_q=$rm_path_q"
	fi
	# 統合先のユーザのチェックアウトの pull が衝突したとき（未解決の索引がある）だけ、解かずにユーザへ頼む案内を足す。
	# ワークツリーの中の衝突は、そこで解く普段の作業なので足さない。
	if [ -n "$gc_target" ] && [ -n "$(git ls-files -u 2>/dev/null)" ]; then
		ccnavi_text "$WS" NOTE_GIT_PULL_CONFLICT_ON_TARGET "integ=$gc_target"
	fi
fi

# 世代で切る。新しい順に並べ、上限より後ろを消す。
ls -1t "$logdir"/git-*.log 2>/dev/null | awk -v keep="$KEEP_LOGS" 'NR > keep' |
	while IFS= read -r old; do rm -f "$old"; done

log_info 終わった -- "sub=$sub" "exit=$status" "lines=$lines" "log=$logrel"

[ "$status" -eq 0 ] || exit 1
exit 0
