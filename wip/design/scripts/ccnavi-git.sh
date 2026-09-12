#!/bin/sh
# ccnavi-git — 安全な git だけを通し、出力を抑えて結果だけ返すラッパ。
#
# 生の `git` は PreToolUse で拒否し、拒否の文面からここへ誘導する。狙いは 2 つ。
#
#   1. 出力がコンテキストに丸ごと載るのを止める。`git log -p` や `git diff` は
#      入力次第で数千行になる。全量は logs/ に残し、標準出力へは要約と先頭数十行
#      だけを返す。足りなければログを名指しで読ませる
#   2. オプションの穴を入口 1 本で塞ぐ。`permissions.allow` の `Bash(git diff:*)`
#      は前方一致でしかないので、後ろに何を足されても通る。サブコマンドごとに
#      使ってよい形を書けるのは、入口を 1 本にしたここだけ
#
# ホワイトリストに無いものは既定で拒否する。拒否の文面には必ず「代わりに何をするか」を
# 書く。理由だけ返すとエージェントは言い換えて再試行する。
#
# これは事故と浪費を減らすためのもので、敵対的な回避への防御ではない。
# `sh -c` や `python -c` に埋めれば hook の文字列一致は外れる。そこまで塞ぐなら
# permissions.deny か sandbox が要る。
#
# 使い方:  sh .claude/scripts/ccnavi-git.sh <サブコマンド> [引数...]
# 終了コード: 0 成功 / 1 git が失敗 / 2 引数か環境の誤り (拒否を含む)

set -eu

# 標準出力に返す本文の上限。超えたぶんはログにだけ残る。
MAX_LINES="${CCNAVI_GIT_MAX_LINES:-40}"

# 失敗したときに返す末尾の行数。原因はたいてい最後に出る。
FAIL_LINES="${CCNAVI_GIT_FAIL_LINES:-30}"

# 残す記録の本数。放っておくと増え続けるので世代で切る。
KEEP_LOGS="${CCNAVI_GIT_KEEP_LOGS:-50}"

# 対話に落ちる道を全部塞ぐ。Bash ツールの stdin は /dev/null だが、git の
# 資格情報プロンプトは /dev/tty を直接開くので stdin だけでは止まらない。
GIT_TERMINAL_PROMPT=0
GIT_PAGER=cat
PAGER=cat
GIT_EDITOR=true
export GIT_TERMINAL_PROMPT GIT_PAGER PAGER GIT_EDITOR

# 環境変数から設定を差し込む道を閉じる。`-c diff.external=<コマンド>` を引数で
# 弾いても、GIT_CONFIG_COUNT/KEY/VALUE と GIT_EXTERNAL_DIFF で同じことができる。
# 引数だけ見て環境を見ないと、塞いだつもりの穴が横に開いたままになる。
# GIT_CONFIG_KEY_n / VALUE_n は GIT_CONFIG_COUNT が門になっているので、
# 番号を数えて消す必要はない。門を閉じれば全部読まれない。
unset GIT_EXTERNAL_DIFF GIT_CONFIG_PARAMETERS GIT_CONFIG_COUNT GIT_ALTERNATE_OBJECT_DIRECTORIES 2>/dev/null || :

# 拒否の文面で代わりの形を名乗るときの、自分の呼び方。生の git は PreToolUse で
# 止まるので、案内に `git stash push -u` と書くと、案内された先でもう 1 度拒否される。
# 代わりの手段が拒否される案内は、案内が無いのとほとんど同じ。
# $0 は呼ばれたときの綴りそのままなので、worktree の中から相対で呼ばれても合う。
SELF="sh $0"

reject() {
	printf 'ccnavi-git: %s\n' "$1" >&2
	exit 2
}

# 共通部分。ワークスペースルートの探し方と、プロジェクト名の導出はここにある。
. "$(dirname "$0")/ccnavi-common.sh"

# ワークスペースルート。道具と記録の置き場。git のトップとは別物で、
# モード B（projects/ の下に別リポジトリを clone する形）では一致しない。
# 上へ歩いて `.claude/scripts/` を探す（設計 §25.8）。
WS=$(ccnavi_workspace) ||
	reject "ワークスペースルートが見つかりません（.claude/scripts/ を持つ親を cwd から上へ探しました）。ワークスペースの中で実行するか、CCNAVI_WORKSPACE にワークスペースルートの絶対パスを渡してください。"

usage() {
	cat <<'USAGE'
sh .claude/scripts/ccnavi-git.sh <サブコマンド> [引数...]

通すもの:
  読む      status log show diff blame shortlog describe rev-parse rev-list
            ls-files ls-tree merge-base diff-tree cat-file grep
  一覧      branch (-d は可 / -D -M -f -u は不可)  tag (一覧のみ)
            remote (-v / show / get-url のみ)  worktree (list add prune remove)
  変える    add  commit (--no-verify は不可)  rm <パス> (-f は不可)
            restore <パス>  (衝突の解決は restore --ours / --theirs -- <パス>)
            checkout / switch (ブランチを移る形だけ。-f と -- <パス> は不可)
            stash (list show push pop apply)
            merge (-X ours / -s ours / --no-verify は不可)
  通信      fetch  pull  (--force / --prune は不可)
            push  (居るブランチを同じ名前で送る形だけ。force / delete / all は不可。
                   main master develop release へ直接は送れない。
                   子チケットの作業ツリーからは送れない。親が合流してから親のツリーで送る)

通さないもの (代わりの手段):
  reset clean   git stash push -u で退避する。消さない
  rebase cherry-pick revert am apply bisect  履歴を書き換えない
  config clone submodule  利用者に依頼する
  -c / --config-env / --git-dir / -C / --output / --upload-pack / --exec-path
                読み取り専用のサブコマンドでも任意コマンドの実行や書き込みに
                化けるので、値を見ずに一律で拒否する

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

# サブコマンドより前のグローバルオプションは 1 つも受け取らない。
#
# `-c diff.external=<コマンド>` は分類上ただの `git diff` のまま任意コマンドを
# 実行する。危ない設定名 (diff.external / core.pager / core.sshCommand ...) を
# 列挙して弾く手は、漏れた名前が読み取り専用のまま通るので採らない。値を見ずに
# 形で落とす。`-C <パス>` と `--git-dir` も、判定の起点が動くので同じ扱い。
case "$1" in
-*) reject "サブコマンドより前のオプション ($1) は受け取りません。素の形 ($SELF <サブコマンド> ...) で書き直してください。設定の一時上書きが要るなら、その理由を利用者に伝えてください。" ;;
esac

sub="$1"
shift

# 全引数を走査して、どのサブコマンドでも許さない形を落とす。前方一致で拾えば
# 等号形 (--output=out.diff) も一緒に落ちるので、getopt 相当の解析は要らない。
for arg in ${1+"$@"}; do
	case "$arg" in
	-c | --config-env | --config-env=*)
		reject "設定の一時上書き ($arg) は受け取りません。素の形で書き直してください。"
		;;
	--output | --output=* | --upload-pack* | --receive-pack* | --exec-path* | --exec=* | --ext-diff | --textconv)
		reject "$arg は、読むだけのサブコマンドをファイル書き込みや外部コマンド実行に変えます。出力を保存したいなら、このラッパが logs/ に全量を残すのでそちらを読んでください。"
		;;
	--git-dir | --git-dir=* | --work-tree | --work-tree=* | --namespace | --namespace=* | -C)
		reject "$arg は判定の起点を別のツリーへ動かします。対象のツリーの中で実行してください。"
		;;
	esac
done

# サブコマンドごとのホワイトリスト。ここに無いものは既定で拒否。
#
# 「読み取り専用」に分類したサブコマンドでも、オプション次第で状態が変わる。
# branch -D / worktree remove --force / tag -d が実例。だから分類だけでは足りず、
# 閉じる向き (通っていたものを止める) の判定をサブコマンドの中に足してある。
has() {
	needle="$1"
	shift
	for a in ${1+"$@"}; do
		[ "$a" = "$needle" ] && return 0
	done
	return 1
}

case "$sub" in
status | log | show | diff | blame | shortlog | describe | rev-parse | rev-list | ls-files | ls-tree | merge-base | diff-tree | cat-file | grep | whatchanged | show-ref)
	: # 読むだけ。上のグローバル判定で穴は塞いである
	;;

branch)
	# 短いオプションは束ねられる (-rd は -r -d と同じ) ので、1 文字ずつ見る。
	# 見るのはダッシュ 1 個で始まる語だけ。長いオプションまで 1 文字で見ると、
	# `--contains=feature/dev` のような値の中の f と d に当たって誤検知する。
	for arg in ${1+"$@"}; do
		case "$arg" in
		--force | --delete=* | --move | --move=* | --set-upstream-to | --set-upstream-to=* | --edit-description)
			reject "$arg はブランチを強制的に消すか、設定を書き換えます。安全側の削除 ($SELF branch -d <名前>) を試し、それでも要るなら利用者に依頼してください。"
			;;
		--*) ;;
		-*)
			case "$arg" in
			*D*)
				reject "$arg は未マージのブランチを消します。安全側の削除 ($SELF branch -d <名前>) を試し、それでも消したいなら利用者に依頼してください。"
				;;
			*f* | *m* | *u*)
				reject "$arg はブランチを強制的に動かすか、追跡先を書き換えます。必要な理由を利用者に伝えてください。"
				;;
			esac
			;;
		esac
	done
	;;

tag)
	for arg in ${1+"$@"}; do
		case "$arg" in
		-l | --list | --contains | --contains=* | --points-at | --points-at=* | --merged | --no-merged | --sort=* | --format=* | -n | -n[0-9]*) ;;
		-*) reject "tag は一覧だけ通します ($arg は不可)。タグを作る・消すのは利用者に依頼してください。" ;;
		esac
	done
	;;

remote)
	for arg in ${1+"$@"}; do
		case "$arg" in
		-v | --verbose | show | get-url) ;;
		-*) reject "remote は一覧だけ通します ($arg は不可)。" ;;
		add | set-url | set-head | set-branches | remove | rm | rename | prune | update)
			reject "remote $arg は取得先・送信先を書き換えます。利用者に依頼してください。"
			;;
		esac
	done
	;;

worktree)
	action="${1:-list}"
	case "$action" in
	add)
		# 行き先を確かめる。git は cwd 基準で解くので、プロジェクトの中で
		# `.claude/worktrees/x` と打つと projects/<名前>/.claude/worktrees/x が
		# できる。プロジェクトに .claude/ ができて --lint が error になり、
		# tree_of の探す場所からも外れる（設計 4.1）。
		#
		# 書き換えずに止める。打った綴りと起きたことがずれると、記録を読んだ
		# 人が追えなくなる。
		# 行き先は「オプションでない最初の語」。値を取るオプションは値ごと飛ばす。
		# 知らないオプションは通さない。通すと行き先を取り違え、検査そのものが
		# 意味を失う（2026-09-12 の決定）。
		wt_dest=""
		wt_skip=0
		wt_first=1
		for wt_word in ${1+"$@"}; do
			if [ "$wt_first" -eq 1 ]; then
				wt_first=0 # 先頭の `add` 自身
				continue
			fi
			if [ "$wt_skip" -eq 1 ]; then
				wt_skip=0 # 直前のオプションの値
				continue
			fi
			case "$wt_word" in
			-b | -B | --reason)
				wt_skip=1
				;;
			--detach | -d | --force | -f | --checkout | --no-checkout | --lock | \
				--guess-remote | --no-guess-remote | --track | --no-track | --quiet | -q) ;;
			-*)
				reject "worktree add の $wt_word は通しません。行き先を取り違えると、プロジェクトの中に作業ツリーを作ってしまいます。使いたい形があれば、利用者に伝えて一覧に足してもらってください。"
				;;
			*)
				if [ -z "$wt_dest" ]; then
					wt_dest="$wt_word"
				fi
				;;
			esac
		done
		[ -n "$wt_dest" ] || reject "worktree add に行き先がありません。"
		wt_abs=$(ccnavi_abs "$wt_dest") ||
			reject "worktree add の行き先 ($wt_dest) を絶対パスに直せません。親のディレクトリが在るか確かめてください。"
		wt_ok=no
		case "$wt_abs" in
		"$WS"/.claude/worktrees/*)
			wt_rest="${wt_abs#"$WS"/.claude/worktrees/}"
			case "$wt_rest" in
			*/*) ;; # 2 段以上は置かない
			'') ;;
			*) wt_ok=yes ;;
			esac
			;;
		esac
		if [ "$wt_ok" = no ]; then
			# 案内は cwd に合わせた綴りで出す。絶対パスだけを出すと、受け取った側が
			# そのまま打てはするが、次に別の場所から打つときに応用が効かない。
			wt_name=$(basename "$wt_dest")
			wt_here=$(ccnavi_abs .)
			wt_spell="$WS/.claude/worktrees/$wt_name"
			case "$wt_here" in
			"$WS")
				wt_spell=".claude/worktrees/$wt_name"
				;;
			"$WS"/*)
				# ワークスペースまで何段上がるかを数えて `../` を並べる。
				wt_rel="${wt_here#"$WS"/}"
				wt_up=""
				while [ -n "$wt_rel" ]; do
					wt_up="../$wt_up"
					case "$wt_rel" in
					*/*) wt_rel="${wt_rel#*/}" ;;
					*) wt_rel="" ;;
					esac
				done
				wt_spell="$wt_up.claude/worktrees/$wt_name"
				;;
			esac
			reject "作業ツリーはワークスペースの .claude/worktrees/ の下に 1 段で置きます（設計 §25.2）。$wt_dest は cwd から解くと $wt_abs になり、ワークスペースの外に出ます。$wt_spell と書いてください。"
		fi
		;;
	list | prune) ;;
	remove)
		if has --force ${1+"$@"} || has -f ${1+"$@"}; then
			reject "worktree remove --force は、未コミットの変更ごとツリーを消します。中の変更を確かめ、要るものを退避してからオプション無しの $SELF worktree remove を使ってください。"
		fi
		;;
	*) reject "worktree $action は通しません。使えるのは list / add / prune / remove です。" ;;
	esac
	;;

stash)
	action="${1:-push}"
	case "$action" in
	list | show | push | save | pop | apply | -*) ;;
	drop | clear)
		reject "stash $action は退避した変更を捨てます。中身を $SELF stash show -p で確かめ、要らないと判断した理由を利用者に伝えてください。"
		;;
	*) reject "stash $action は通しません。使えるのは list / show / push / pop / apply です。" ;;
	esac
	;;

add)
	: # 索引を変えるだけ。作業ツリーは壊れない
	;;

rm)
	# 消す側だが、消えるのは git が中身を持っているファイルだけ。git rm は
	# 索引や HEAD と食い違うファイルを既定で拒む。`rm -rf` の代わりとして
	# rules.yml が名指しで勧める経路なので、勧めた先が通らない形にはしない。
	#
	# 通さないのは -f。それを付けると、コミットしていない変更ごと消える。
	# git が守っている線がそこなので、こちらで引く線も同じ場所にする。
	# -r は通す。付けても、中の 1 つでも書きかけがあれば git が止める。
	[ "$#" -eq 0 ] && reject "rm は消すファイルを名指ししてください ($SELF rm <パス>)。"
	for arg in ${1+"$@"}; do
		case "$arg" in
		. | :/ | "*" | ":/*" | "./")
			reject "rm にツリー全体 ($arg) を渡すと、追跡されているファイルがまとめて消えます。消すものを 1 つずつ名指ししてください。"
			;;
		--force)
			reject "$arg はコミットしていない変更ごと消します。付けずに実行し、git が止めたなら、その中身を確かめてから利用者に伝えてください。"
			;;
		--*) ;;
		-*)
			case "$arg" in
			*f*)
				reject "$arg には -f (--force) が含まれます。コミットしていない変更ごと消すので通しません。付けずに実行してください。"
				;;
			esac
			;;
		esac
	done
	;;

restore)
	# 作業中の変更を捨てる側。rules.yml が reset --hard の代わりに名指しで勧める
	# 経路でもあるので、対象を 1 つずつ名指しさせる形だけ通す。
	#
	# --ours / --theirs もここを通る。衝突したパスにしか効かない（普段は
	# エラーになる）ので、マージの最中だけ意味を持つ。ガード自身の設定が
	# 衝突したときに解く道はここしかない。ルールファイルに衝突マーカーが
	# 入っていると YAML として読めず、判定は組み込みの既定に落ちているが、
	# 既定もこの形は止めない（ccnavi/builtin.py）。
	[ "$#" -eq 0 ] && reject "restore は戻すファイルを名指ししてください ($SELF restore <パス>)。"
	for arg in ${1+"$@"}; do
		case "$arg" in
		. | :/ | "*" | ":/*" | "./")
			reject "restore にツリー全体 ($arg) を渡すと、作業中の変更が黙って消えます。戻したいファイルを 1 つずつ名指ししてください。"
			;;
		esac
	done
	;;

merge)
	# 早送り以外も通す。CLAUDE.md の worktree 手順は、main が先に進んだ状態から
	# ブランチへ main を取り込む形を必ず通る。そこを --ff-only に絞ると、
	# 枝分かれした時点でブランチが永久に統合されない。衝突の解消はメインの仕事で、
	# 解こうとする手をラッパが止めてしまっては、止めた先に進む道が無くなる。
	#
	# 止めるのは、衝突を人が見ないまま片側を捨てる形だけ。`-X ours` と `-s ours` は
	# もう一方の変更を黙って落とす。並行して動いている他セッションの書きかけが
	# そこに入っていることがあり、落ちたことは差分にも記録にも残らない。
	prev=""
	for arg in ${1+"$@"}; do
		case "$prev" in
		-X | -s | --strategy | --strategy-option)
			case "$arg" in
			ours | theirs)
				reject "$prev $arg は衝突した側を黙って捨てます。他セッションの書きかけが入っていても差分に残りません。衝突は 1 つずつ中身を見て解いてください。"
				;;
			esac
			;;
		esac
		case "$arg" in
		-Xours | -Xtheirs | --strategy-option=ours | --strategy-option=theirs | -sours | --strategy=ours)
			reject "$arg は衝突した側を黙って捨てます。他セッションの書きかけが入っていても差分に残りません。衝突は 1 つずつ中身を見て解いてください。"
			;;
		--no-verify)
			reject "$arg はマージ前の検査を飛ばします。検査が落ちるなら、落ちた理由を直してください。"
			;;
		esac
		prev="$arg"
	done
	;;

commit)
	# 通す。中身の点検は /commit スキルと ask ルールの側でやる。
	# ここで見るのは、点検そのものを飛ばす形だけ。
	for arg in ${1+"$@"}; do
		case "$arg" in
		--no-verify)
			reject "$arg はコミット前の検査を飛ばします。検査が落ちるなら、落ちた理由を直してください。"
			;;
		--*) ;;
		-*)
			case "$arg" in
			*n*)
				reject "$arg には -n (--no-verify) が含まれます。コミット前の検査は飛ばさず、落ちた理由を直してください。"
				;;
			esac
			;;
		esac
	done
	;;

checkout | switch)
	# ブランチを移る形は通す。作業ツリーの中身を捨てる形だけ止める。
	# `git checkout -- .` は、書きかけを何も言わずに消す。取り返せない。
	for arg in ${1+"$@"}; do
		case "$arg" in
		--force | --discard-changes | --ours | --theirs)
			reject "$arg は作業中の変更を捨てます。退避は $SELF stash push -u です。"
			;;
		--)
			reject "$sub にパスを渡す形は、そのファイルの書きかけを消します。戻したいファイルがあるなら $SELF restore <パス> を名指しで使ってください。"
			;;
		. | :/)
			reject "$sub にツリー全体 ($arg) を渡すと、作業中の変更が黙って消えます。$SELF restore <パス> を名指しで使ってください。"
			;;
		--*) ;;
		-*)
			case "$arg" in
			*f*)
				reject "$arg には -f (--force) が含まれます。作業中の変更を捨てるので通しません。退避は $SELF stash push -u です。"
				;;
			esac
			;;
		esac
	done
	;;

fetch | pull)
	# 外と通信する。資格情報の入力待ちは GIT_TERMINAL_PROMPT=0 で即失敗に倒れる。
	for arg in ${1+"$@"}; do
		case "$arg" in
		-f | --force | --prune | --unshallow)
			reject "$arg は手元の参照を書き換えます。オプション無しの $SELF $sub で足ります。"
			;;
		esac
	done
	;;

push)
	# 自分が居るブランチを、同じ名前でそのまま送る形だけを通す。レビューは
	# マージリクエストの実物に結ぶので、そこまではエージェントが自分で運べたほうがよい。
	#
	# 通さないのは「戻せなくなる形」と「人の判断を飛び越す形」の 2 つ。
	# 履歴を書き換える force、消す delete、まとめて送る all/mirror/tags、
	# 別の綴りへ送る refspec（`HEAD:main` が書ける）、そして統合先そのものへの直接の push。
	# 統合は人がマージリクエストで行う。
	push_branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || :)
	if [ -z "$push_branch" ] || [ "$push_branch" = "HEAD" ]; then
		reject "いまブランチの上に居ません（detached HEAD）。送る先が決まらないので通しません。"
	fi
	# 子チケットの作業ツリーからは送らない。レビューはマージリクエストの実物に結び、
	# その実物は親ブランチに 1 本だけある。子の成果は親が手元で合流してから、親の
	# ツリーで親が送る。子が自分のブランチをリモートへ置くと、レビューの外に
	# ある枝ができ、人が見た HEAD と合流した HEAD が食い違う道になる。
	# 見分けるのは承認済みの写し（main の `.claude/ccnavi/tickets/<名前>.md`）に
	# `parent:` があるかだけ。写しの無いツリー（チケットを使わないブランチ）は通す。
	# 作業ツリーはワークスペースの .claude/worktrees/ の下にある。切り元が
	# プロジェクトでも置き場はワークスペース（設計 §25.2）なので、git の
	# --git-common-dir から導くと、モード B では切り元のプロジェクトを指して
	# 条件が一致せず、承認済みの写しの検査が丸ごと飛ぶ。ガードが「効いている
	# つもりで効いていない」形になるので、ワークスペースルートを基準にする。
	push_top=$(git rev-parse --show-toplevel 2>/dev/null || :)
	push_root="$WS"
	if [ -n "$push_root" ]; then
		case "$push_top" in
		"$push_root"/.claude/worktrees/*)
			push_name="${push_top#"$push_root"/.claude/worktrees/}"
			push_name="${push_name%%/*}"
			case "${CCNAVI_APPROVED:-}" in
			/* | [A-Za-z]:*) push_copies="$CCNAVI_APPROVED" ;;
			*) push_copies="$push_root/${CCNAVI_APPROVED:-.claude/ccnavi/tickets}" ;;
			esac
			# 閉じた写し（closed/）も見る。子を閉じたあと、親が合流して片付けるまでの間も
			# そのツリーは子のもので、送ってよくなるわけではない。
			for push_copy in "$push_copies/$push_name.md" "$push_copies/closed/$push_name.md"; do
				if [ -f "$push_copy" ] && grep -q '^parent:' "$push_copy"; then
					push_parent=$(sed -n 's/^parent:[[:space:]]*//p' "$push_copy" | head -n 1)
					reject "$push_name は子チケットの作業ツリーです。子のブランチはリモートへ送りません。親（$push_parent）が子の成果を合流してから、親の作業ツリー (.claude/worktrees/$push_parent) で送ります。子は作業を終えたら結果を報告して終わってください。"
				fi
			done
			;;
		esac
	fi
	case "$push_branch" in
	main | master | develop | release | release/*)
		reject "$push_branch は統合先です。統合は利用者がマージリクエストで行うので、ここへ直接は送りません。作業用のブランチから送ってください。"
		;;
	esac
	push_seen_remote=""
	for arg in ${1+"$@"}; do
		case "$arg" in
		-u | --set-upstream | --porcelain | --quiet | -q | --verbose | -v) ;;
		--no-verify)
			reject "$arg は送る前の検査を飛ばします。検査が落ちるなら原因を直してください。"
			;;
		--force-with-lease | --force-with-lease=* | --force-if-includes | -f | --force)
			reject "$arg はリモートの履歴を書き換えます。送り直したい理由を利用者に伝えてください。"
			;;
		-d | --delete)
			reject "$arg はリモートのブランチを消します。利用者に依頼してください。"
			;;
		--all | --mirror | --tags | --follow-tags | --prune | --atomic)
			reject "$arg は今のブランチ以外も動かします。通すのは、居るブランチをそのまま送る形だけです。"
			;;
		-*)
			reject "push で $arg は通しません。通すのは 'push [-u] [<リモート>] [$push_branch]' の形だけです。"
			;;
		*:*)
			reject "$arg は送り先を直に書く形（refspec や URL）です。設定済みのリモート名だけを使い、居るブランチをそのままの名前で送ってください。"
			;;
		*)
			if [ -z "$push_seen_remote" ]; then
				push_seen_remote="$arg"
			elif [ "$arg" != "$push_branch" ] && [ "$arg" != "HEAD" ]; then
				reject "$arg は今居るブランチ（$push_branch）ではありません。他のブランチは、そこへ移ってから送ってください。"
			fi
			;;
		esac
	done
	;;
reset | clean)
	reject "$sub は作業中の変更を消します。退避は $SELF stash push -u、戻すのは $SELF restore <パス> です。"
	;;
rebase | cherry-pick | revert | am | apply | bisect | filter-branch | replace | update-ref | symbolic-ref | reflog | gc | notes)
	reject "$sub は履歴か参照を書き換えます。通しません。必要な理由を利用者に伝えてください。"
	;;
config)
	reject "config は設定を読み書きします。値には資格情報が混ざるので通しません。必要な値は利用者に尋ねてください。"
	;;
clone | submodule | lfs)
	reject "$sub は外から中身を持ち込みます。通しません。利用者に依頼してください。"
	;;
*)
	reject "$sub はホワイトリストにありません。使える形は sh .claude/scripts/ccnavi-git.sh --help で確認してください。"
	;;
esac

# 外部 diff ドライバは設定にも書けるので、読む系では毎回無効にして呼ぶ。
case "$sub" in
diff | show | log | whatchanged) set -- --no-ext-diff ${1+"$@"} ;;
esac

root=$(git rev-parse --show-toplevel 2>/dev/null || :)
[ -z "$root" ] && reject "git リポジトリの中で実行してください。"

# 記録はワークスペースの下に寄せる（REQ-MLT-14、設計 4.1）。git のトップに書くと、
# モード B ではプロジェクトのリポジトリの中に出る。ワークスペースの .gitignore の
# /logs/ はワークスペースルート起点なので効かず、public のリポジトリに運用の痕跡が入る。
logproject=$(ccnavi_project "$(pwd)" "$WS")
if [ -n "$logproject" ]; then
	logdir="$WS/logs/$logproject"
else
	logdir="$WS/logs"
fi
mkdir -p "$logdir"
logfile="$logdir/git-$(date '+%Y%m%d-%H%M%S')-$$.log"

# 全量を必ず残す。成功でも書く。捨てると「あのとき何が出ていたか」を後から
# 確かめられない。logs/ は .gitignore に入っていて、コミット対象にならない。
{
	printf '# %s  cwd=%s\n' "$(date '+%Y-%m-%dT%H:%M:%S')" "$(pwd)"
	printf '$ git %s' "$sub"
	for arg in ${1+"$@"}; do printf ' %s' "$arg"; done
	printf '\n--- 出力 ---\n'
} >"$logfile"

status=0
git --no-pager "$sub" ${1+"$@"} >>"$logfile" 2>&1 || status=$?

# 記録の綴り。エージェントがそのまま sed -n で開ける形で返す。
#
# 基準はワークスペースルート。モード B ではエージェントの cwd がプロジェクトの中に
# あるので、git のトップからの相対を返すと届かない。ワークスペースの中に居るときは
# 短い相対、そうでなければ絶対を返す（設計 4.1）。
case "$logfile" in
"$WS"/*) logrel="${logfile#"$WS"/}" ;;
*) logrel="$logfile" ;;
esac
# cwd から相対で開けないなら、絶対の綴りをそのまま返す。2 つ並べない。
# 並べると、受け取った側がどちらを開くか迷い、綴りの切り出しも要る。
if [ ! -f "$logrel" ]; then
	logrel="$logfile"
fi

# 本文は目印の次の行から。目印と同じ行が出力に含まれても、awk は最初の 1 件で
# 立ち上がるので取り違えない。
body() { awk 'f; /^--- 出力 ---$/ { f = 1 }' "$logfile"; }

lines=$(body | awk 'END { print NR + 0 }')

# 要約は「次の判断に効く数値」だけにする。本文をもう 1 度読ませないためのもので、
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
		printf '... 残り %d 行は %s にある\n' "$((lines - MAX_LINES))" "$logrel"
	fi
else
	printf 'fail  git %s  exit=%d  log=%s\n' "$sub" "$status" "$logrel"
	body | tail -n "$FAIL_LINES"
fi

# 世代で切る。新しい順に並べ、上限より後ろを消す。
ls -1t "$logdir"/git-*.log 2>/dev/null | awk -v keep="$KEEP_LOGS" 'NR > keep' |
	while IFS= read -r old; do rm -f "$old"; done

[ "$status" -eq 0 ] || exit 1
exit 0
