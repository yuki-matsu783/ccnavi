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
unset GIT_EXTERNAL_DIFF GIT_CONFIG_PARAMETERS GIT_CONFIG_COUNT GIT_ALTERNATE_OBJECT_DIRECTORIES 2>/dev/null || :

# 拒否の文面で代わりの形を名乗るときの、自分の呼び方。生の git は PreToolUse で
# 止まるので、案内に `git stash push -u` と書くと、案内された先でもう 1 度拒否される。
# 代わりの手段が拒否される案内は、案内が無いのとほとんど同じ。
# $0 は呼ばれたときのパスそのままなので、ワークツリーの中から相対で呼ばれても合う。
SELF="sh $0"
# 取り込みの sh。リモートに合わせる経路はここへ案内する。
SYNC="sh $(dirname "$0")/ccnavi-sync.sh"

# reject <識別子> <文面>。文面は標準エラーへ出す契約。識別子は拒否の種類を表す短い語で、
# 診断ログにだけ残す。文面にはユーザの引数（URL など）が入るので、ログには写さない。
reject() {
	printf 'ccnavi-git: %s\n' "$2" >&2
	# 診断ログ（docs/claude/logging.md）。上の文面は契約として決まっている出力で、診断ログはそれとは別に残すだけ。
	log_info 拒否した -- "sub=${sub:-}" "reason=$1"
	exit 2
}

# 共通部分。ワークスペースルートの探し方と、プロジェクト名の導出はここにある。
. "$(dirname "$0")/ccnavi-common.sh"

# ワークスペースルート。道具と記録の置き場。git のトップとは違うもので、
# モード B（projects/ の下に別リポジトリを clone する形）では一致しない。
# 上へたどって `.ccnavi/scripts/ccnavi-common.sh` を探す（設計 11.8）。
WS=$(ccnavi_workspace) ||
	reject no-workspace "ワークスペースルートが見つかりません（.ccnavi/scripts/ccnavi-common.sh を持つ親を cwd から上へ探しました）。ワークスペースの中で実行するか、CCNAVI_WORKSPACE にワークスペースルートの絶対パスを渡してください。"
# 解いたルートを logger に渡し、書くたびに探し直させない。
ccnavi_log_root="$WS"
# git が返すパス（リンクを解いたもの）と比べるための、リンクを解いたルート。
WS_P=$(ccnavi_phys "$WS")

usage() {
	cat <<'USAGE'
sh .ccnavi/scripts/ccnavi-git.sh <サブコマンド> [引数...]

通すもの:
  読む      status log show diff blame shortlog describe rev-parse rev-list
            ls-files ls-tree merge-base diff-tree cat-file grep
  一覧      branch (-d は可 / -D -m -M -C -f -u は不可)  tag (一覧のみ)
            remote (-v / show / get-url のみ)  worktree (list add prune remove)
            worktree add は行き先の名前とブランチ名を揃える形だけ
                  (add <行き先> -b <行き先の名前> [<起点>]。-B / --detach / -f は不可)
  変える    add  commit (--no-verify は不可)  rm <パス> (-f は不可)
            restore <パス>  (衝突の解決は restore --ours / --theirs -- <パス>)
            checkout / switch (ブランチを移る形だけ。-f・checkout -B・switch -C と -- <パス> は不可。
                  親のワークツリーでは別のブランチへ移れない)
            承認済みチケットの置き場 (.ccnavi/approved/ と wip/proposals/review/) には
                  restore --source / --ours / --theirs と checkout <ref> <パス> を使えない
            stash (list show push pop apply)
            merge (-X ours / -s ours / --no-verify は不可)
            merge-file -p (標準出力に出す形だけ。ファイルへは書かない)
                  両方取り込むときは merge-file -p --union --object-id :2:<パス> :1:<パス> :3:<パス>
                  (現在・祖先・相手の順) で出力し、それを読んで Edit で書く
  通信      fetch  pull  (--force / --prune は不可。取ってくるのは <リモート> <ブランチ> だけで、
                   : や + を含む引数 (refspec・URL) は不可。手元の ref は ccnavi-sync.sh が進める)
            push  (居るブランチを同じ名前で送る形だけ。force / delete / all は不可。
                   main master develop release へ直接は送れない。
                   子チケットのワークツリーからは送れない。親が取り込んでから親のツリーで送る。
                   リモートから消えた (取り込み状態が gone の) 親のブランチへは送れない)

通さないもの (代わりの手段):
  reset clean   sh .ccnavi/scripts/ccnavi-git.sh stash push -u で退避する。消さない
                リモートに合わせるなら sh .ccnavi/scripts/ccnavi-sync.sh <P> (親のブランチ)。
                ほかのブランチは fetch <リモート> <ブランチ> のあと merge <リモート>/<ブランチ>。
                分かれていて進めないならユーザの対応に切り替える (checkout -B で付け替えない)
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

# サブコマンドより前のグローバルオプションは 1 つも受け取らない。
#
# `-c diff.external=<コマンド>` は分類上ただの `git diff` のまま任意コマンドを
# 実行する。危ない設定名 (diff.external / core.pager / core.sshCommand ...) を
# 列挙して拒否するやり方は、漏れた名前が読み取り専用のまま通るので採らない。値を見ずに
# 形で落とす。`-C <パス>` と `--git-dir` も、判定の起点が動くので同じ扱い。
case "$1" in
-*) reject global-option "サブコマンドより前のオプション ($1) は受け取りません。素の形 ($SELF <サブコマンド> ...) で書き直してください。設定の一時上書きが要るなら、その理由をユーザに伝えてください。" ;;
esac

sub="$1"
shift

# 全引数を走査して、どのサブコマンドでも許さない形を落とす。前方一致で拾えば
# 等号形 (--output=out.diff) も一緒に落ちるので、getopt 相当の解析は要らない。
for arg in ${1+"$@"}; do
	case "$arg" in
	-c | --config-env | --config-env=*)
		reject config-override "設定の一時上書き ($arg) は受け取りません。素の形で書き直してください。"
		;;
	--output | --output=* | --upload-pack* | --receive-pack* | --exec-path* | --exec=* | --ext-diff | --textconv)
		reject output-or-exec "$arg は、読むだけのサブコマンドをファイル書き込みや外部コマンド実行に変えます。出力を保存したいなら、このラッパースクリプトが logs/ に全量を残すのでそちらを読んでください。"
		;;
	--git-dir | --git-dir=* | --work-tree | --work-tree=* | --namespace | --namespace=* | -C)
		reject moved-root "$arg は判定の起点を別のツリーへ動かします。対象のツリーの中で実行してください。"
		;;
	--?*)
		# 略した表記（`--outp=x`・`--upload-p=...`）。parse-options を使う副命令は長いオプションの
		# 略を受けるので、止める名前の頭に当たる表記も止める。
		# `--text`（diff の正式な名前）だけは textconv の頭でも通す。
		gl_name="${arg#--}"
		gl_name="${gl_name%%=*}"
		for gl_bad in output upload-pack receive-pack exec-path exec ext-diff textconv git-dir work-tree namespace; do
			case "$gl_bad" in
			"$gl_name"*)
				[ "$gl_name" = text ] && continue
				reject output-or-exec "$arg は、止めているオプション（--${gl_bad}）の略として読まれます。読むだけのサブコマンドをファイル書き込みや外部コマンド実行、判定の起点の移動に変えるので通しません。"
				;;
			esac
		done
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

# オプションを許可リストで読む。止める名前を並べるだけでは、長いオプションの略とまとめた短いオプションで抜けるため。
#
#   ow_flags="' quiet detach '"   値を取らない長いオプション（前後を空白で挟む）
#   ow_values="' orphan '"         値を取る長いオプション（`--x=v` か次の語）
#   ow_optvals="' track '"         値を `=` でだけ取れる長いオプション（`--x` だけでもよい）
#   ow_sflags="qmt"               値を取らない短いオプションの字
#   ow_svalues="b"                値を取る短いオプションの字（まとめた残りの字か次の語が値）
#   ow_soptvals="u"               値をまとめた残りの字でだけ取れる短いオプションの字
#   opt_walk <コールバック> [<引数>...]
#
# git の parse-options は長いオプションの略（`--force-c` → `--force-create`）を受けるので、止める
# 名前を並べるやり方では略した表記が通ってしまう。ここは一覧に**そのままの表記**である名前だけを通し、ほかの `--` は
# 断る（略した表記も断る）。まとめた短いオプション（`-qbnew`）は 1 字ずつ読み、値を取る字が出たら
# 残りの字をその値として扱う。
#
# コールバックは `<コールバック> opt <-x か --name> <値>`・`pos <語>`・`end`（`--` を見た）で呼ぶ。
# 止める判定はコールバックが持つ（一覧に入れた上で、名前を見て reject する）。
ow_reject() {
	reject option-not-allowed "$sub の $1 は通しません。通すオプションは一覧にあるものだけで、長いオプションは略さずに書きます（略した表記を、git がここで止めているオプションとして読むことがあります）。使いたい形があれば、ユーザに伝えて一覧に足してもらってください。"
}
opt_walk() {
	ow_cb="$1"
	shift
	ow_end=no
	ow_need=""
	for ow_arg in ${1+"$@"}; do
		if [ -n "$ow_need" ]; then
			"$ow_cb" opt "$ow_need" "$ow_arg"
			ow_need=""
			continue
		fi
		if [ "$ow_end" = yes ]; then
			"$ow_cb" pos "$ow_arg"
			continue
		fi
		case "$ow_arg" in
		--)
			ow_end=yes
			"$ow_cb" end
			;;
		--*)
			ow_name="${ow_arg#--}"
			ow_name="${ow_name%%=*}"
			ow_val=""
			ow_eq=no
			case "$ow_arg" in
			*=*)
				ow_val="${ow_arg#*=}"
				ow_eq=yes
				;;
			esac
			case "$ow_values" in
			*" $ow_name "*)
				if [ "$ow_eq" = yes ]; then
					"$ow_cb" opt "--$ow_name" "$ow_val"
				else
					ow_need="--$ow_name"
				fi
				continue
				;;
			esac
			case "$ow_optvals" in
			*" $ow_name "*)
				"$ow_cb" opt "--$ow_name" "$ow_val"
				continue
				;;
			esac
			case "$ow_flags" in
			*" $ow_name "*)
				[ "$ow_eq" = no ] || ow_reject "$ow_arg"
				"$ow_cb" opt "--$ow_name" ""
				continue
				;;
			esac
			ow_reject "$ow_arg"
			;;
		-)
			"$ow_cb" pos "-"
			;;
		-*)
			ow_rest="${ow_arg#-}"
			while [ -n "$ow_rest" ]; do
				ow_ch="${ow_rest%"${ow_rest#?}"}"
				ow_rest="${ow_rest#?}"
				case "$ow_svalues" in
				*"$ow_ch"*)
					if [ -n "$ow_rest" ]; then
						"$ow_cb" opt "-$ow_ch" "$ow_rest"
					else
						ow_need="-$ow_ch"
					fi
					ow_rest=""
					continue
					;;
				esac
				case "$ow_soptvals" in
				*"$ow_ch"*)
					"$ow_cb" opt "-$ow_ch" "$ow_rest"
					ow_rest=""
					continue
					;;
				esac
				case "$ow_sflags" in
				*"$ow_ch"*)
					"$ow_cb" opt "-$ow_ch" ""
					continue
					;;
				esac
				ow_reject "$ow_arg"
			done
			;;
		*)
			"$ow_cb" pos "$ow_arg"
			;;
		esac
	done
	# 値を取るオプションで終わった（値が無い）。git も落とすが、読み違えないよう断る。
	[ -z "$ow_need" ] || ow_reject "$ow_need"
	return 0
}

# 許可リストを空に戻す。副命令ごとに必要なものだけ入れる。
ow_spec() {
	ow_flags=" $1 "
	ow_values=" $2 "
	ow_optvals=" $3 "
	ow_sflags="$4"
	ow_svalues="$5"
	ow_soptvals="${6:-}"
}

# 承認済みチケットの置き場（`.ccnavi/approved/`）とレビュー待ち（`wip/proposals/review/`）に
# 当たるパスかを見る。当たれば in_store=yes。
#
# `checkout <ref> <パス>`・`restore --source <ref>` で置き場を過去の中身に戻す形と、
# `restore --ours / --theirs` で置き場の衝突を片側の中身で解く形を止めるために使う。
# 置き場を動かすのはユーザと ccnavi のスクリプトで、エージェントが git で戻すと、承認が
# 無かったことにも、取り下げた承認が戻ったことにもなる。
#
# 比べるのは git のトップからのパス。cwd からの相対（`approved/doing/x.md` を `.ccnavi/` の中で打つ）も
# `..` を取り除いてから比べる。置き場の親（`.ccnavi`・`wip`・`.`）も置き場ごと戻すので当たる。
# `*` `?` `[` と `:` で始まる pathspec は、どこに当たるかをここで決められないので当たるとみなす。
# 大文字小文字はそろえる（Windows と macOS の既定のファイルシステムは区別しない）。
store_hit() {
	in_store=no
	sh_arg=$(printf '%s' "$1" | tr '\\' '/')
	case "$sh_arg" in
	:* | *'*'* | *'?'* | *'['*)
		in_store=yes
		return 0
		;;
	esac
	sh_approved=$(printf '%s' "${CCNAVI_TICKETS_APPROVED:-.ccnavi/approved}" | tr '\\' '/')
	sh_review=$(printf '%s' "${CCNAVI_TICKETS_PROPOSAL:-wip/proposals}/review" | tr '\\' '/')
	case "$sh_arg" in
	/* | [A-Za-z]:/*)
		# 絶対パス。トップのパスは OS で表記が揃わない（`C:/x` と `/c/x`）ので、置き場のパスを
		# 含むかだけを見る。
		sh_path="$sh_arg"
		;;
	*)
		sh_path="$(git rev-parse --show-prefix 2>/dev/null || :)$sh_arg"
		;;
	esac
	# `.` と `..` を取り除く。トップより上に出たら、このリポジトリの外なので当たらない。
	sh_norm=""
	sh_out=no
	sh_ifs="$IFS"
	IFS=/
	set -f
	for sh_part in $sh_path; do
		case "$sh_part" in
		'' | .) ;;
		..)
			case "$sh_norm" in
			'') sh_out=yes ;;
			*/*) sh_norm="${sh_norm%/*}" ;;
			*) sh_norm="" ;;
			esac
			;;
		*) sh_norm="${sh_norm:+$sh_norm/}$sh_part" ;;
		esac
	done
	set +f
	IFS="$sh_ifs"
	[ "$sh_out" = yes ] && return 0
	sh_norm=$(printf '%s' "$sh_norm" | tr '[:upper:]' '[:lower:]')
	for sh_store in "$sh_approved" "$sh_review"; do
		sh_store=$(printf '%s' "$sh_store" | tr '[:upper:]' '[:lower:]')
		sh_store="${sh_store%/}"
		case "$sh_store" in
		/* | [a-z]:/*)
			# 置き場をリポジトリの外に向けた設定。含むかだけを見る。
			case "/$sh_norm/" in
			*"$sh_store"/*) in_store=yes ;;
			esac
			continue
			;;
		esac
		case "$sh_path" in
		/* | [A-Za-z]:/*)
			case "/$sh_norm/" in
			*/"$sh_store"/*) in_store=yes ;;
			esac
			continue
			;;
		esac
		case "$sh_norm" in
		'' | "$sh_store" | "$sh_store"/*) in_store=yes ;;
		esac
		case "$sh_store/" in
		"$sh_norm"/*) in_store=yes ;;
		esac
	done
	return 0
}

case "$sub" in
status | log | show | diff | blame | shortlog | describe | rev-parse | rev-list | ls-files | ls-tree | merge-base | diff-tree | whatchanged | show-ref)
	: # 読むだけ。上のグローバル判定で穴は塞いである
	;;

cat-file)
	# `--textconv`・`--filters` は設定の外部コマンドを走らせ、parse-options なので略も有効。
	# 通すのは中身と型を読む形だけ（許可リスト）。
	ow_spec "batch batch-check batch-all-objects buffer unordered allow-unknown-type" "" "" "etsp" ""
	catfile_cb() { :; }
	opt_walk catfile_cb ${1+"$@"}
	;;

grep)
	# `-O` / `--open-files-in-pager` は当たったファイルを外部コマンドで開く。まとめた短いオプション（`-iO`）と略も見る。
	# 値を取る短いオプション（-e -f -A -B -C -m）より後ろの字は値なので数えない。
	for arg in ${1+"$@"}; do
		case "$arg" in
		--) break ;;
		--?*)
			gr_name="${arg#--}"
			gr_name="${gr_name%%=*}"
			case "open-files-in-pager" in
			"$gr_name"*)
				[ "${#gr_name}" -ge 2 ] &&
					reject grep-pager "$arg は当たったファイルを外部コマンドで開きます。通しません。"
				;;
			esac
			;;
		-?*)
			gr_rest="${arg#-}"
			while [ -n "$gr_rest" ]; do
				gr_ch="${gr_rest%"${gr_rest#?}"}"
				gr_rest="${gr_rest#?}"
				case "$gr_ch" in
				O) reject grep-pager "$arg には -O（--open-files-in-pager）が含まれます。当たったファイルを外部コマンドで開くので通しません。" ;;
				e | f | A | B | C | m) gr_rest="" ;;
				esac
			done
			;;
		esac
	done
	;;

branch)
	# 許可リストで読む（略した長いオプションと、まとめた短いオプションを 1 字ずつ見る）。
	# 消す・改名する・複製する・追跡先を書き換える形は、一覧に入れた上で名前を見て止める。
	ow_spec "list all remotes verbose quiet show-current no-column no-color ignore-case omit-empty no-track delete create-reflog no-create-reflog force move copy unset-upstream edit-description" \
		"sort format points-at set-upstream-to" \
		"contains no-contains merged no-merged color column abbrev track" \
		"arvqdlitDMCfmuc" ""
	branch_cb() {
		[ "$1" = opt ] || return 0
		case "$2" in
		-D)
			reject branch-delete-unmerged "branch -D は未マージのブランチを消します。安全側の削除 ($SELF branch -d <名前>) を試し、それでも消したいならユーザに依頼してください。"
			;;
		-M | -C | -c | --move | --copy)
			reject branch-force-move "branch $2 はブランチを改名するか複製し、同じ名前の既存のブランチを上書きすることがあります。ブランチの名前はワークツリーの名前とチケットの識別子に結び付いていて、変えると着手やレビューのときに名前からチケットを引けなくなります。必要な理由をユーザに伝えてください。"
			;;
		--force | --set-upstream-to | --unset-upstream | --edit-description)
			reject branch-force "branch $2 はブランチを強制的に消すか、設定を書き換えます。安全側の削除 ($SELF branch -d <名前>) を試し、それでも要るならユーザに依頼してください。"
			;;
		-f | -m | -u)
			reject branch-move "branch $2 はブランチを強制的に動かすか、追跡先を書き換えます。必要な理由をユーザに伝えてください。"
			;;
		esac
	}
	opt_walk branch_cb ${1+"$@"}
	;;

tag)
	# 一覧だけ通す。位置の引数（`tag <名前>` はタグを作る）は -l / --list のときの絞り込みだけ。
	# --contains などの値を次の語で取る形は、値ごと読み飛ばす。
	tg_list=no
	tg_words=""
	tg_skip=no
	for arg in ${1+"$@"}; do
		if [ "$tg_skip" = yes ]; then
			tg_skip=no # 直前のオプションの値
			continue
		fi
		case "$arg" in
		-l | --list) tg_list=yes ;;
		--contains | --no-contains | --points-at | --merged | --no-merged) tg_skip=yes ;;
		--contains=* | --no-contains=* | --points-at=* | --merged=* | --no-merged=* | --sort=* | --format=* | -n | -n[0-9]*) ;;
		-*) reject tag-write "tag は一覧だけ通します ($arg は不可)。タグを作る・消すのはユーザに依頼してください。" ;;
		*) tg_words="$arg" ;;
		esac
	done
	if [ -n "$tg_words" ] && [ "$tg_list" = no ]; then
		reject tag-write "tag $tg_words はタグを作ります。tag は一覧だけ通します（絞り込むなら $SELF tag -l <型>）。タグを作るのはユーザに依頼してください。"
	fi
	;;

remote)
	for arg in ${1+"$@"}; do
		case "$arg" in
		-v | --verbose | show | get-url) ;;
		-*) reject remote-option "remote は一覧だけ通します ($arg は不可)。" ;;
		add | set-url | set-head | set-branches | remove | rm | rename | prune | update)
			reject remote-write "remote $arg は取得先・送信先を書き換えます。ユーザに依頼してください。"
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
		# 書き換えずに止める。打ったパスと起きたことが食い違うと、記録を読んだ
		# ユーザが追えなくなる。
		# 行き先は「オプションでない最初の語」。値を取るオプションは値ごと読み飛ばす。
		# 知らないオプションは通さない。通すと行き先を取り違え、検査そのものが
		# 意味を失う。
		#
		# 行き先の名前とブランチ名を揃える。親のブランチ名は親の
		# 識別子で、ワークツリーの名前も同じ。`-B`（既存のブランチの付け替え）・`--detach`（ブランチの
		# 外）・`-f`（他のワークツリーが使うブランチや残った登録の上書き）は、その結び付きを崩すか、
		# 親のブランチを別のコミットへ向け直すので通さない。
		wt_dest=""
		wt_base=""
		wt_new=""
		wt_skip=""
		wt_first=1
		for wt_word in ${1+"$@"}; do
			if [ "$wt_first" -eq 1 ]; then
				wt_first=0 # 先頭の `add` 自身
				continue
			fi
			if [ -n "$wt_skip" ]; then
				# 直前のオプションの値。-b なら新しいブランチ名。
				[ "$wt_skip" = b ] && wt_new="$wt_word"
				wt_skip=""
				continue
			fi
			case "$wt_word" in
			-b) wt_skip=b ;;
			--reason) wt_skip=r ;;
			-B)
				reject worktree-force-branch "worktree add -B は、同じ名前の既存のブランチを別のコミットへ付け替えます。そのブランチにしか無いコミットが外れ、親のブランチなら承認済みチケットの置き場ごと中身が変わります。新しく切るなら $SELF worktree add .claude/worktrees/<名前> -b <名前> <起点>、既にあるブランチを出すなら $SELF worktree add .claude/worktrees/<ブランチ> <ブランチ> です。リモートに合わせるのは $SYNC <P> です。"
				;;
			--detach | -d)
				reject worktree-detach "worktree add $wt_word はブランチの外（detached HEAD）にワークツリーを作ります。ワークツリーはブランチの上に作り、名前をブランチ名に揃えます（$SELF worktree add .claude/worktrees/<名前> -b <名前> <起点>）。"
				;;
			--force | -f)
				reject worktree-force "worktree add $wt_word は、他のワークツリーが出しているブランチや、登録の残った行き先を上書きします。$SELF worktree list で確かめ、要らない登録は $SELF worktree prune で外してから、オプション無しで打ってください。"
				;;
			--checkout | --no-checkout | --lock | \
				--guess-remote | --no-guess-remote | --track | --no-track | --quiet | -q) ;;
			-*)
				reject worktree-option "worktree add の $wt_word は通しません。行き先を取り違えると、プロジェクトの中にワークツリーを作ってしまいます。使いたい形があれば、ユーザに伝えて一覧に足してもらってください。"
				;;
			*)
				if [ -z "$wt_dest" ]; then
					wt_dest="$wt_word"
				elif [ -z "$wt_base" ]; then
					wt_base="$wt_word"
				fi
				;;
			esac
		done
		[ -n "$wt_dest" ] || reject worktree-no-dest "worktree add に行き先がありません。"
		wt_abs=$(ccnavi_abs "$wt_dest") ||
			reject worktree-dest-unresolved "worktree add の行き先 ($wt_dest) を絶対パスに直せません。親のディレクトリが在るか確かめてください。"
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
			# 案内は cwd に合わせたパスで出す。絶対パスだけを出すと、受け取った側が
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
			reject worktree-outside "ワークツリーはワークスペースの .claude/worktrees/ の下に 1 段で置きます（設計 11.2）。$wt_dest は cwd から解くと $wt_abs になり、ワークスペースの外に出ます。$wt_spell と書いてください。"
		fi
		# 行き先の名前 = ブランチ名。-b が無く 2 つ目の語も無ければ、
		# git は行き先の名前でブランチを切るので揃う。
		wt_leaf="${wt_abs##*/}"
		if [ -n "$wt_new" ]; then
			if [ "$wt_new" != "$wt_leaf" ]; then
				reject worktree-name "worktree add の新しいブランチ名（-b ${wt_new}）が行き先の名前（${wt_leaf}）と違います。ワークツリーの名前はブランチ名と同じにします。ccnavi は親チケットのワークツリーを、名前が親チケットの識別子で、同じ名前のブランチをチェックアウトしているものとして探します。親チケットのブランチを識別子と違う名前のワークツリーに出すと、リモートでの承認を取り込む処理（ccnavi-sync.sh と、セッション開始時に ccnavi-fetch.sh が fast-forward で進める処理）がそのワークツリーを見つけられません。親チケットのブランチを一度でも push したか ccnavi-sync.sh で取り込んだことがあると、親と子のチケットの承認・状態の操作（start・finish など）・実行前の判定も止まります。$SELF worktree add .claude/worktrees/$wt_new -b $wt_new <起点> の形にしてください。"
			fi
		elif [ -n "$wt_base" ] && [ "$wt_base" = "$wt_leaf" ] &&
			! git show-ref --verify --quiet "refs/heads/$wt_base" &&
			! git show-ref --verify --quiet "refs/remotes/origin/$wt_base"; then
			# 名前が揃っていても、ブランチでない（タグ・sha）ならブランチの外に作る。
			reject worktree-detach "worktree add $wt_dest $wt_base の $wt_base は手元のブランチでも origin/$wt_base でもないので、ブランチの外（detached HEAD）にワークツリーを作ります。新しく切るなら $SELF worktree add $wt_dest -b $wt_leaf <起点> にしてください。"
		elif [ -n "$wt_base" ] && [ "$wt_base" != "$wt_leaf" ]; then
			reject worktree-name "worktree add $wt_dest $wt_base は、$wt_base を名前の違う行き先（${wt_leaf}）に出すか、ブランチの外（detached HEAD）に作ります。ワークツリーの名前はブランチ名と同じにします。新しく切るなら $SELF worktree add $wt_dest -b $wt_leaf ${wt_base}、既にあるブランチ $wt_base を出すなら行き先を .claude/worktrees/$wt_base にしてください。"
		fi
		;;
	list | prune | remove)
		# 許可リスト。remove の --force（略した --forc も）は止める。
		case "$action" in
		list) ow_spec "porcelain verbose" "expire" "" "vz" "" ;;
		prune) ow_spec "dry-run verbose" "expire" "" "nv" "" ;;
		remove) ow_spec "force" "" "" "f" "" ;;
		esac
		wt_cb() {
			[ "$1" = opt ] || return 0
			case "$2" in
			--force | -f)
				reject worktree-remove-force "worktree remove --force は、未コミットの変更ごとツリーを消します。中の変更を確かめ、要るものを退避してからオプション無しの $SELF worktree remove を使ってください。"
				;;
			esac
		}
		wt_walk() {
			shift
			opt_walk wt_cb ${1+"$@"}
		}
		wt_walk ${1+"$@"}
		;;
	*) reject worktree-action "worktree $action は通しません。使えるのは list / add / prune / remove です。" ;;
	esac
	;;

stash)
	action="${1:-push}"
	case "$action" in
	list | show | push | save | pop | apply | -*) ;;
	drop | clear)
		reject stash-drop "stash $action は退避した変更を捨てます。中身を $SELF stash show -p で確かめ、要らないと判断した理由をユーザに伝えてください。"
		;;
	*) reject stash-action "stash $action は通しません。使えるのは list / show / push / pop / apply です。" ;;
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
	# 通さないのは -f（略した --forc と、まとめた -rf も）。それを付けると、コミットしていない変更ごと消える。
	# git が守っている線がそこなので、こちらで引く線も同じ場所にする。
	# -r は通す。付けても、中の 1 つでも書きかけがあれば git が止める。
	[ "$#" -eq 0 ] && reject rm-no-path "rm は消すファイルを名指ししてください ($SELF rm <パス>)。"
	ow_spec "cached quiet dry-run ignore-unmatch sparse pathspec-file-nul force" "pathspec-from-file" "" "rqnf" ""
	rm_cb() {
		case "$1:${2:-}" in
		opt:--force | opt:-f)
			reject rm-force "rm の $2 (--force) はコミットしていない変更ごと消します。付けずに実行し、git が止めたなら、その中身を確かめてからユーザに伝えてください。"
			;;
		pos:. | pos::/ | "pos:*" | "pos::/*" | pos:./)
			reject rm-whole-tree "rm にツリー全体 ($2) を渡すと、追跡されているファイルがまとめて消えます。消すものを 1 つずつ名指ししてください。"
			;;
		esac
	}
	opt_walk rm_cb ${1+"$@"}
	;;

restore)
	# 作業中の変更を捨てる側。rules.yml が reset --hard の代わりに名指しで勧める
	# 経路でもあるので、対象を 1 つずつ名指しさせる形だけ通す。
	#
	# --ours / --theirs もここを通る。衝突したパスにしか使えない（普段は
	# エラーになる）ので、マージの最中だけ意味を持つ。ガード自身の設定が
	# 衝突したときに解く方法はここしかない。ルールファイルに衝突マーカーが
	# 入っていると YAML として読めず、判定は組み込みの既定を使っているが、
	# 既定もこの形は止めない（ccnavi/policy/builtin.py）。
	#
	# 別のコミットの中身で戻す形（--source）と、衝突を片側の中身で解く形（--ours / --theirs）は、
	# 承認済みチケットの置き場に当たるパスには使わせない。
	# 置き場を過去の中身に戻すと、承認が無かったことにも、消えたマーカーが戻ったことにもなる。
	# 置き場の衝突は、どちらの承認を採るかをユーザが決める。オプションは許可リストで読む。
	[ "$#" -eq 0 ] && reject restore-no-path "restore は戻すファイルを名指ししてください ($SELF restore <パス>)。"
	rs_source=no
	rs_side=no
	rs_paths=""
	ow_spec "staged worktree ours theirs merge quiet progress no-progress overlay no-overlay ignore-unmerged ignore-skip-worktree-bits pathspec-file-nul recurse-submodules no-recurse-submodules" \
		"source conflict pathspec-from-file" "" "SWqm" "s"
	restore_cb() {
		case "$1" in
		opt)
			case "$2" in
			--source | -s) rs_source=yes ;;
			--ours | --theirs) rs_side=yes ;;
			--pathspec-from-file) rs_paths="--pathspec-from-file" ;;
			esac
			;;
		pos)
			case "$2" in
			. | :/ | "*" | ":/*" | "./")
				reject restore-whole-tree "restore にツリー全体 ($2) を渡すと、作業中の変更が気づかないうちに消えます。戻したいファイルを 1 つずつ名指ししてください。"
				;;
			esac
			store_hit "$2"
			[ "$in_store" = yes ] && rs_paths="$2"
			;;
		esac
		return 0
	}
	opt_walk restore_cb ${1+"$@"}
	if [ -n "$rs_paths" ]; then
		if [ "$rs_side" = yes ]; then
			reject restore-store-side "restore --ours / --theirs で承認済みチケットの置き場に当たるパス ($rs_paths) の衝突を片側に寄せる形は通しません。置き場の衝突は、どちらの承認を採るかをユーザが決めます。$SELF merge --abort で取り込みをやめ、衝突したパスをユーザに伝えてください。"
		fi
		if [ "$rs_source" = yes ]; then
			reject restore-store-source "restore --source で承認済みチケットの置き場に当たるパス ($rs_paths) を別のコミットの中身に戻す形は通しません。置き場を動かすのはユーザと ccnavi のスクリプトです。置き場の中身が食い違っているなら、ユーザに伝えてください（* ? [ や : で始まる指定は、置き場に当たるかを確かめられないので同じく通しません。ファイルを 1 つずつ名指ししてください）。"
		fi
	fi
	;;

merge)
	# 早送り以外も通す。docs/claude/worktree.md の手順は、main が先に進んだ状態から
	# ブランチへ main を取り込む形を必ず通る。そこを --ff-only に絞ると、
	# 枝分かれした時点でブランチが永久に統合されない。衝突の解消はメインの仕事で、
	# 解こうとする操作をラッパースクリプトが止めてしまうと、先に進む方法が無くなる。
	#
	# 止めるのは、衝突をユーザが見ないまま片側を捨てる形だけ。`-X ours` と `-s ours` は
	# もう一方の変更を気づかないうちに落とす。並行して動いている他セッションの書きかけが
	# そこに入っていることがあり、落ちたことは差分にも記録にも残らない。
	# オプションは許可リストで読む（略した --strategy-o=ours・まとめた -sours も同じに読む）。
	ow_spec "ff no-ff ff-only edit no-edit commit no-commit stat no-stat no-log squash no-squash quiet verbose progress no-progress abort continue quit signoff no-signoff allow-unrelated-histories summary no-summary verify no-verify autostash no-autostash" \
		"message file strategy strategy-option" "log" "qvne" "mFsX"
	merge_cb() {
		[ "$1" = opt ] || return 0
		case "$2:${3:-}" in
		--strategy:ours | --strategy:theirs | -s:ours | -s:theirs | --strategy-option:ours | --strategy-option:theirs | -X:ours | -X:theirs)
			reject merge-discard-side "$2 $3 を使うと、衝突の片側が気づかないうちに捨てられます。他セッションの書きかけが入っていても差分に残りません。衝突は 1 つずつ中身を見て解いてください。"
			;;
		--no-verify:*)
			reject merge-no-verify "$2 はマージ前の検査を飛ばします。検査が落ちるなら、落ちた理由を直してください。"
			;;
		esac
	}
	opt_walk merge_cb ${1+"$@"}
	;;

merge-file)
	# 衝突を両方取り込む形 (--union) を作るための方法。通すのは結果を標準出力に出す形
	# (-p / --stdout) だけ。付けないと git は 1 つめのファイルを直に書き換える。
	# ファイルを書く手段は Edit / Write にそろえる。そちらなら hook が行き先を見られる。
	#
	# オプションは知っているものだけ通す。git は長いオプションの略記 (--std) も
	# 否定の形 (--no-stdout) も受け取るので、知らない表記を通すと -p を付けたつもりで
	# 書き込みに戻る。-L と --marker-size は値を次の語で取る。`-L -p` の -p は
	# ラベルで、-p を付けたことにならない (git はファイルを書く)。だから値ごと読み飛ばす。
	# `--` の後ろはファイル名。そこに -p があっても数えない。
	# まとめた短いオプション (-pq) は分けて書かせる。1 文字ずつ読むと -L の値を取り違える。
	#
	# --object-id は通す。衝突の最中なら :2:<パス> :1:<パス> :3:<パス> を直に渡せて、
	# 一時ファイルが要らない。-p が無いと結果をオブジェクトとしてリポジトリに書くが、
	# -p は必須なのでその形はここに来ない。
	mf_stdout=no
	mf_skip=no
	mf_end=no
	for arg in ${1+"$@"}; do
		if [ "$mf_skip" = yes ]; then
			mf_skip=no # 直前のオプションの値
			continue
		fi
		[ "$mf_end" = yes ] && continue
		case "$arg" in
		--) mf_end=yes ;;
		-p | --stdout) mf_stdout=yes ;;
		-L | --marker-size | --diff-algorithm) mf_skip=yes ;;
		--union | --ours | --theirs | --diff3 | --zdiff3 | --object-id | -q | --quiet | \
			--marker-size=* | --diff-algorithm=* | -L?*) ;;
		-*)
			reject merge-file-option "merge-file の $arg は通しません。通すのは -p (--stdout) と --union / --ours / --theirs / --diff3 / --zdiff3 / --object-id / -L <ラベル> / --marker-size=<数> / -q だけで、短いオプションは 1 つずつ分けて書きます。"
			;;
		esac
	done
	if [ "$mf_stdout" = no ]; then
		reject merge-file-no-stdout "merge-file は -p (--stdout) を付けた形だけ通します。付けないと 1 つめのファイルを直に書き換えます。衝突の最中なら $SELF merge-file -p --union --object-id :2:<パス> :1:<パス> :3:<パス> で出力し、その結果を Edit で書いてください。"
	fi
	;;

commit)
	# 通す。中身の点検は /commit スキルと ask ルールの側でやる。
	# ここで見るのは、点検そのものを行わない形（--no-verify・-n）と、直前のコミットを書き換える
	# --amend。オプションは許可リストで読む（略した --no-verif・まとめた -an も同じに読む）。
	ow_spec "all quiet verbose signoff no-signoff only include allow-empty allow-empty-message dry-run short porcelain long branch null status no-status reset-author edit no-edit verify no-verify pathspec-file-nul amend" \
		"message file author date cleanup trailer pathspec-from-file template fixup squash" "untracked-files" \
		"aqvsoiezn" "mFt" "u"
	commit_cb() {
		[ "$1" = opt ] || return 0
		case "$2" in
		--no-verify | -n)
			reject commit-no-verify "commit の $2 (--no-verify) はコミット前の検査を飛ばします。検査が落ちるなら、落ちた理由を直してください。"
			;;
		--amend)
			reject commit-amend "commit --amend は直前のコミットを書き換えます。直すなら新しいコミットを積んでください。"
			;;
		esac
	}
	opt_walk commit_cb ${1+"$@"}
	;;

checkout | switch)
	# ブランチを移る形は通す。作業ツリーの中身を捨てる形だけ止める。
	# `git checkout -- .` は、書きかけを何も言わずに消す。取り返せない。
	#
	# オプションは許可リストで読む。略した長い
	# オプション（`--force-c`・`--det`・`--orph=`）は断り、まとめた短いオプションは 1 字ずつ読んで、
	# 値を取る字（checkout の b・B、switch の c・C）の後ろは値として扱う（`-qbnew` は -q -b new）。
	#
	# 既存のブランチを別のコミットへ付け替える形（checkout -B / switch -C・--force-create）は通さない。
	# 親のブランチを付け替えると、承認済みチケットの置き場ごと別の中身になる。
	#
	# `checkout <ref> <パス>` は `--` が無くてもパスを別のコミットの中身に戻す。承認済みチケットの
	# 置き場に当たるパスは通さない。オプションでない最初の語が行き先か起点で、2 つ目からがパス。
	if [ "$sub" = checkout ]; then
		ow_spec "quiet progress no-progress detach track no-track guess no-guess merge overlay no-overlay recurse-submodules no-recurse-submodules ignore-skip-worktree-bits force ours theirs" \
			"orphan conflict pathspec-from-file" "track recurse-submodules" "qmtlf" "bB"
	else
		ow_spec "quiet progress no-progress detach track no-track guess no-guess merge recurse-submodules no-recurse-submodules force discard-changes" \
			"create orphan conflict force-create" "track recurse-submodules" "qmtdf" "cC"
	fi
	co_new=""
	co_detach=no
	co_words=0
	co_one=""
	checkout_cb() {
		case "$1" in
		end)
			reject checkout-path "$sub にパスを渡す形は、そのファイルの書きかけを消します。戻したいファイルがあるなら $SELF restore <パス> を名指しで使ってください。"
			;;
		pos)
			case "$2" in
			. | :/)
				reject checkout-whole-tree "$sub にツリー全体 ($2) を渡すと、作業中の変更が気づかないうちに消えます。$SELF restore <パス> を名指しで使ってください。"
				;;
			esac
			co_words=$((co_words + 1))
			if [ "$co_words" -eq 1 ]; then
				co_one="$2"
				return 0
			fi
			store_hit "$2"
			if [ "$in_store" = yes ]; then
				reject checkout-store "$sub <ref> <パス> で承認済みチケットの置き場に当たるパス ($2) を別のコミットの中身に戻す形は通しません。置き場を動かすのはユーザと ccnavi のスクリプトです。置き場の中身が食い違っているなら、ユーザに伝えてください（* ? [ や : で始まる指定は、置き場に当たるかを確かめられないので同じく通しません）。"
			fi
			;;
		opt)
			case "$sub:$2" in
			*:--force | *:-f)
				reject checkout-force "$sub の $2 (--force) は作業中の変更を捨てるので通しません。退避は $SELF stash push -u です。"
				;;
			switch:--discard-changes | checkout:--ours | checkout:--theirs)
				reject checkout-discard "$2 は作業中の変更を捨てます。退避は $SELF stash push -u です。"
				;;
			checkout:-B | switch:-C | switch:--force-create)
				reject checkout-force-branch "$sub $2 は、同じ名前の既存のブランチを別のコミットへ付け替えます。そのブランチにしか無いコミットが気づかないうちに外れ、親のブランチなら承認済みチケットの置き場ごと中身が変わります。リモートに合わせるなら、親のブランチは $SYNC <P>、ほかのブランチは $SELF merge <リモート>/<ブランチ> です。分かれていて進めないならユーザに伝えてください。新しいブランチは -b（switch は --create）で切ります。"
				;;
			checkout:--pathspec-from-file)
				reject checkout-store "$sub の $2 は、どのパスを戻すかをここで読めないので通しません。戻したいファイルがあるなら $SELF restore <パス> を名指しで使ってください。"
				;;
			checkout:-b | checkout:--orphan | switch:--create | switch:-c | switch:--orphan)
				co_new="${3:-}"
				;;
			*:--detach | switch:-d)
				co_detach=yes
				;;
			esac
			;;
		esac
		return 0
	}
	opt_walk checkout_cb ${1+"$@"}
	# 親のワークツリー（.claude/worktrees/<P> で、親チケットか提案があるもの）では、許す形
	# （語が無い・自分のブランチ・HEAD・checkout <ref> <パス>）のほかは通さない。
	# 親チケットか提案があるかは ccnavi_parent_tree が見る。ccnavi-sync.sh・ccnavi-fetch.sh・
	# syncstate.home_tree はそれに加えて、ツリーの名前が識別子で、HEAD が同じ名前のブランチを指すことを求める。
	# 別のブランチに移ると、ccnavi-sync.sh とセッション開始時の ccnavi-fetch.sh は、リモートでの承認を
	# このツリーへ取り込まなくなる。取り込み状態がある親（親のブランチを一度でも origin へ push したか、
	# ccnavi-sync.sh で取り込んだ親）では、実行ファイル（syncstate.standing）が親のワークツリーを決められず、
	# 親と子のチケットの承認・状態の操作・実行前の判定を止める。
	co_top=$(ccnavi_phys "$(git rev-parse --show-toplevel 2>/dev/null || :)")
	case "$co_top" in
	"$WS_P"/.claude/worktrees/*)
		co_name="${co_top#"$WS_P"/.claude/worktrees/}"
		co_name="${co_name%%/*}"
		if ccnavi_parent_tree "$co_top" "$co_name"; then
			co_to=""
			if [ -n "$co_new" ]; then
				co_to="$co_new"
			elif [ "$co_detach" = yes ]; then
				co_to="（ブランチの外）"
			elif [ "$co_words" -eq 1 ] || { [ "$co_words" -ge 2 ] && [ "$sub" = switch ]; }; then
				co_to="$co_one"
			fi
			case "$co_to" in
			'' | "$co_name" | HEAD) ;;
			*)
				reject parent-worktree-switch "親のワークツリー（.claude/worktrees/${co_name}）では別のブランチ（${co_to}）へ移れません。ccnavi は親チケットのワークツリーを、名前が親チケットの識別子で、同じ名前のブランチをチェックアウトしているものとして探します。別のブランチに移ると、リモートでの承認を取り込む処理（ccnavi-sync.sh と、セッション開始時に ccnavi-fetch.sh が fast-forward で進める処理）がこのワークツリーを飛ばします。親チケットのブランチを一度でも push したか ccnavi-sync.sh で取り込んだことがあると、親と子のチケットの承認・状態の操作（start・finish など）・実行前の判定も止まります。別の作業は別のワークツリーを切ってください（$SELF worktree add .claude/worktrees/<名前> -b <名前> <起点>）。"
				;;
			esac
		fi
		;;
	esac
	;;

fetch | pull)
	# 外と通信する。資格情報の入力待ちは GIT_TERMINAL_PROMPT=0 で即失敗になる。
	# オプションは許可リストで読む（略した --prun・--rebas も断る）。
	if [ "$sub" = fetch ]; then
		ow_spec "quiet verbose progress no-progress tags no-tags all dry-run no-recurse-submodules force prune prune-tags unshallow show-forced-updates no-show-forced-updates write-fetch-head no-write-fetch-head" \
			"jobs depth deepen shallow-since" "" "qvntfpP" "j"
	else
		ow_spec "quiet verbose ff ff-only no-ff no-rebase stat no-stat no-edit edit commit no-commit progress no-progress tags no-tags force prune unshallow autostash no-autostash" \
			"depth deepen shallow-since" "rebase" "qvnfpr" ""
	fi
	fetch_cb() {
		case "$1" in
		opt)
			case "$2" in
			-f | --force | --prune | -p | -P | --prune-tags)
				reject fetch-force "$2 は手元の参照を書き換えます。オプション無しの $SELF $sub で足ります。"
				;;
			--rebase | -r)
				reject pull-rebase "pull の $2 は手元のコミットを取ってきた側へ付け直し、履歴を書き換えます。取り込むのは $SELF pull（merge）か、親のブランチなら $SYNC <P> です。"
				;;
			esac
			;;
		pos)
			# refspec（`+refs/heads/x:refs/heads/y`・`x:y`）は、取ってきたものを手元の
			# ブランチへ直に書く。`+` は早送りでない書き換えも通す。取ってくるのはブランチ名だけにする。
			# URL も `:` を含むので同じく止まる。取得先は設定済みのリモート名で書く。
			case "$2" in
			*:* | *+*)
				reject fetch-refspec "$2 は取ってきたものを手元の参照へ直に書く形（refspec）か URL です。取ってくるのはブランチ名だけで、$SELF $sub <リモート> <ブランチ> の形で書いてください。手元の ref は $SYNC <ブランチ> が進めます（親のワークツリー以外のブランチは、取ってきた後の $SELF merge <リモート>/<ブランチ>）。"
				;;
			esac
			;;
		esac
		return 0
	}
	opt_walk fetch_cb ${1+"$@"}
	;;

push)
	# 自分が居るブランチを、同じ名前でそのまま送る形だけを通す。レビューは
	# マージリクエストの実物に結ぶので、そこまではエージェントが自分で進められたほうがよい。
	#
	# 通さないのは「戻せなくなる形」と「ユーザの判断を経ない形」の 2 つ。
	# 履歴を書き換える force、消す delete、まとめて送る all/mirror/tags、
	# 別の名前へ送る refspec（`HEAD:main` が書ける）、そして統合先そのものへの直接の push。
	# 統合はユーザがマージリクエストで行う。
	push_branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || :)
	if [ -z "$push_branch" ] || [ "$push_branch" = "HEAD" ]; then
		reject detached-head "いまブランチの上に居ません（detached HEAD）。送る先が決まらないので通しません。"
	fi
	# 子チケットのワークツリーからは送らない。レビューはマージリクエストの実物に結び、
	# その実物は親ブランチに 1 本だけある。子の成果は親が手元で合流してから、親の
	# ツリーで親が送る。子が自分のブランチをリモートへ置くと、レビューの外に
	# あるブランチができ、ユーザが見た HEAD と合流した HEAD が食い違うことになる。
	# 見分けるのは承認済みチケット（各ツリーの `.ccnavi/approved/{doing,done}/<名前>.md` と
	# レビュー待ちの `wip/proposals/review/<名前>.md`）に `parent:` があるかだけ。
	# 承認済みチケットの無いツリー（チケットを使わないブランチ）は通す。
	# ワークツリーはワークスペースの .claude/worktrees/ の下にある。元リポジトリが
	# プロジェクトでも置き場はワークスペース（設計 11.2）なので、git の
	# --git-common-dir から導くと、モード B ではプロジェクトである元リポジトリを指して
	# 条件が一致せず、承認済みチケットの検査が丸ごと行われない。ガードが「有効な
	# つもりで有効になっていない」形になるので、ワークスペースルートを基準にする。
	push_top=$(ccnavi_phys "$(git rev-parse --show-toplevel 2>/dev/null || :)")
	push_root="$WS_P"
	log_debug push の判定の材料 -- "branch=$push_branch" "top=$push_top" "workspace=$WS"
	if [ -n "$push_root" ]; then
		case "$push_top" in
		"$push_root"/.claude/worktrees/*)
			push_name="${push_top#"$push_root"/.claude/worktrees/}"
			push_name="${push_name%%/*}"
			# 子の承認済みチケットを置くのは、ふつう親のワークツリー（approval.home_dir）。
			# ワークスペースルートだけを見ると、親のツリーに置かれた子を見落として通してしまう。
			# ccnavi が承認済みチケットを探すのと同じツリー（ワークスペースルート・projects/ の下・
			# .claude/worktrees/ の下。approval.trees）を全部見る。識別子は重ならないので、
			# どこで見つかってもこのツリーの子のもの。
			push_projects="${CCNAVI_PROJECTS:-projects}"
			case "$push_projects" in
			/* | [A-Za-z]:*) ;;
			*) push_projects="$push_root/$push_projects" ;;
			esac
			for push_tree in "$push_root" "$push_projects"/* "$push_root"/.claude/worktrees/*; do
				[ -d "$push_tree" ] || continue
				case "${CCNAVI_TICKETS_APPROVED:-}" in
				/* | [A-Za-z]:*) push_copies="$CCNAVI_TICKETS_APPROVED" ;;
				# 既定は ccnavi の既定（settings.py の DEFAULT_APPROVED）と揃える。食い違うと、
				# env を書いていないワークスペースで、この検査が気づかないうちに行われなくなる。
				*) push_copies="$push_tree/${CCNAVI_TICKETS_APPROVED:-.ccnavi/approved}" ;;
				esac
				case "${CCNAVI_TICKETS_PROPOSAL:-}" in
				/* | [A-Za-z]:*) push_proposals="$CCNAVI_TICKETS_PROPOSAL" ;;
				*) push_proposals="$push_tree/${CCNAVI_TICKETS_PROPOSAL:-wip/proposals}" ;;
				esac
				# レビュー待ち（review/）と閉じた承認済みチケット（done/）も見る。子を閉じたあと、親が
				# 取り込んで片付けるまでの間もそのツリーは子のもので、送ってよくなるわけではない。
				for push_copy in "$push_copies/doing/$push_name.md" "$push_copies/done/$push_name.md" \
					"$push_proposals/review/$push_name.md"; do
					if [ -f "$push_copy" ] && grep -q '^parent:' "$push_copy"; then
						push_parent=$(sed -n 's/^parent:[[:space:]]*//p' "$push_copy" | head -n 1)
						reject push-child-worktree "$push_name は子チケットのワークツリーです。子のブランチはリモートへ送りません。親（${push_parent}）が子の成果を取り込んでから、親のワークツリー (.claude/worktrees/$push_parent) で送ります。子は作業を終えたら結果を報告して終わってください。"
					fi
				done
			done
			;;
		esac
	fi
	case "$push_branch" in
	main | master | develop | release | release/*)
		reject push-integration-branch "$push_branch は統合先です。統合はユーザがマージリクエストで行うので、ここへ直接は送りません。作業用のブランチから送ってください。"
		;;
	esac
	# リモートから消えた親のブランチ（取り込み状態が gone）へは送らない。
	# 普通の push で作り直すと、消えた理由（改名・消し間違い・捨てた親子のチケット）を確かめないまま親子のチケットが
	# 動き出す。毎回の ls-remote はせず、取り込み状態を読むだけにする。ユーザが戻した後は ccnavi-sync.sh が
	# present に書き直して、この拒否が解ける。
	push_key=$(ccnavi_repo_key "${push_top:-.}" "$WS")
	push_record=$(ccnavi_family_record "$WS" "$push_key" "$push_branch")
	if [ "$(ccnavi_record_get "$push_record" state)" = gone ]; then
		reject push-gone-family "$push_branch はリモートから消えた親のブランチです（取り込み状態が gone）。普通の push で作り直すと、消えた理由を確かめないまま親子のチケットが動き出すので通しません。改名や消し間違いならユーザに元の名前で戻してもらい（戻し方は $SYNC $push_branch が出します）、戻した後に $SYNC $push_branch を打ち直すと送れます。親子のチケットを捨てたなら親のワークツリーを片付け、ユーザが $SYNC --forget $push_branch で取り込み状態を消します（エージェントは打ちません）。"
	fi
	push_seen_remote=""
	for arg in ${1+"$@"}; do
		case "$arg" in
		-u | --set-upstream | --porcelain | --quiet | -q | --verbose | -v) ;;
		--no-verify)
			reject push-no-verify "$arg は送る前の検査を飛ばします。検査が落ちるなら原因を直してください。"
			;;
		--force-with-lease | --force-with-lease=* | --force-if-includes | -f | --force)
			reject push-force "$arg はリモートの履歴を書き換えます。送り直したい理由をユーザに伝えてください。"
			;;
		-d | --delete)
			reject push-delete "$arg はリモートのブランチを消します。ユーザに依頼してください。"
			;;
		--all | --mirror | --tags | --follow-tags | --prune | --atomic)
			reject push-all "$arg は今のブランチ以外も動かします。通すのは、居るブランチをそのまま送る形だけです。"
			;;
		-*)
			reject push-option "push で $arg は通しません。通すのは 'push [-u] [<リモート>] [$push_branch]' の形だけです。"
			;;
		*:*)
			reject push-refspec "$arg は送り先を直に書く形（refspec や URL）です。設定済みのリモート名だけを使い、居るブランチをそのままの名前で送ってください。"
			;;
		*)
			if [ -z "$push_seen_remote" ]; then
				push_seen_remote="$arg"
			elif [ "$arg" != "$push_branch" ] && [ "$arg" != "HEAD" ]; then
				reject push-other-branch "$arg は今居るブランチ（${push_branch}）ではありません。他のブランチは、そこへ移ってから送ってください。"
			fi
			;;
		esac
	done
	;;
reset)
	reject reset "reset は作業中の変更やコミットを消します。退避は $SELF stash push -u、戻すのは $SELF restore <パス> です。リモートに合わせたいなら、親のブランチは $SYNC <P> を打ってください（早送りか merge で取り込み、衝突したら取りやめます）。ほかのブランチは $SELF fetch <リモート> <ブランチ> のあと $SELF merge <リモート>/<ブランチ> です。分かれていて進めない（squash マージの後で fast-forward できない、など）なら、ブランチを付け替えずにユーザに伝えてください。"
	;;
clean)
	reject clean "$sub は作業中の変更を消します。退避は $SELF stash push -u、戻すのは $SELF restore <パス> です。"
	;;
rebase | cherry-pick | revert | am | apply | bisect | filter-branch | replace | update-ref | symbolic-ref | reflog | gc | notes)
	reject history-rewrite "$sub は履歴か参照を書き換えます。通しません。必要な理由をユーザに伝えてください。"
	;;
config)
	reject config "config は設定を読み書きします。値には資格情報が混ざるので通しません。必要な値はユーザに尋ねてください。"
	;;
clone | submodule | lfs)
	reject import "$sub は外から中身を持ち込みます。通しません。ユーザに依頼してください。"
	;;
*)
	reject not-allowed "$sub は許可リストにありません。使える形は sh .ccnavi/scripts/ccnavi-git.sh --help で確認してください。"
	;;
esac

# push が通ったら、親のブランチなら取り込み状態を作る。最初の push からその親子のチケットを C1 の
# 対象に入れ、次の取り込みまで Chrome 拡張からだけ見える間を作らない。
#
# 送った先が親のブランチ（.claude/worktrees/<P> で、ディレクトリ名 = ブランチ名、親チケットか提案が
# ある）で、送り先が origin のときだけ。取り込み状態があれば（present）sha を書き直すだけで、closed・
# blocked は触らない。取り込み状態が無く、置き場に未コミットの変更があれば作らずに言う（未送信の状態を
# 持ち込まないため）。取り込み状態があると SessionStart の早送りと ccnavi-sync.sh の消えたかの確かめの対象になる。
push_record_family() {
	# 送り先は git と同じ順で解く: 引数のリモート → branch.<b>.pushRemote → remote.pushDefault →
	# branch.<b>.remote → origin。origin 以外へ送ったなら取り込み状態を作らない（取り込み状態は origin の P を見る）。
	pr_remote="${push_seen_remote:-}"
	if [ -z "$pr_remote" ]; then
		pr_remote=$(git config --get "branch.$push_branch.pushRemote" 2>/dev/null ||
			git config --get remote.pushDefault 2>/dev/null ||
			git config --get "branch.$push_branch.remote" 2>/dev/null || echo origin)
	fi
	[ "$pr_remote" = origin ] || return 0
	case "$push_top" in
	"$WS_P"/.claude/worktrees/*) ;;
	*) return 0 ;;
	esac
	[ "${push_top##*/}" = "$push_branch" ] || return 0
	ccnavi_parent_tree "$push_top" "$push_branch" || return 0
	pr_sha=$(git rev-parse --verify --quiet HEAD 2>/dev/null || :)
	[ -n "$pr_sha" ] || return 0
	pr_state=$(ccnavi_record_get "$push_record" state)
	case "$pr_state" in
	present)
		ccnavi_record_write "$push_record" remote origin branch "$push_branch" sha "$pr_sha" \
			fetched_at "$(ccnavi_record_get "$push_record" fetched_at)" state present \
			reason "$(ccnavi_record_get "$push_record" reason)" || :
		return 0
		;;
	'') ;;
	*) return 0 ;;
	esac
	pr_approved="${CCNAVI_TICKETS_APPROVED:-.ccnavi/approved}"
	pr_proposals="${CCNAVI_TICKETS_PROPOSAL:-wip/proposals}"
	pr_dirty=$(git -C "$push_top" status --porcelain --untracked-files=all -- \
		"${pr_approved%/}" "${pr_proposals%/}/review" 2>/dev/null | cut -c4- | tr '\n' ' ' | sed 's/ *$//')
	if [ -n "$pr_dirty" ]; then
		printf '案内: 置き場に未コミットの状態がある（%s）。コミットして push し直すと、この親子のチケットは取り込み（%s %s）の対象になる\n' \
			"$pr_dirty" "$SYNC" "$push_branch"
		return 0
	fi
	if ccnavi_record_write "$push_record" remote origin branch "$push_branch" sha "$pr_sha" \
		fetched_at "$(date +%s)" state present reason ""; then
		log_info 親子のチケットの取り込み状態を作った -- "branch=$push_branch" "repo=$push_key"
		printf '案内: %s の取り込み状態を作った。以後、リモートでの承認は %s %s で取り込む\n' \
			"$push_branch" "$SYNC" "$push_branch"
	fi
	return 0
}

# 外部 diff ドライバは設定にも書けるので、読む系では毎回無効にして呼ぶ。
case "$sub" in
diff | show | log | whatchanged) set -- --no-ext-diff ${1+"$@"} ;;
esac

root=$(git rev-parse --show-toplevel 2>/dev/null || :)
[ -z "$root" ] && reject not-a-repo "git リポジトリの中で実行してください。"
log_info 受け付けた -- "sub=$sub" "args=$#"
log_debug 判定の材料 -- "sub=$sub" "action=${action:-}" "top=$root" "cwd=$PWD" "workspace=$WS"

# 記録はワークスペースの下にまとめる（REQ-MLT-14、設計 4.1）。git のトップに書くと、
# モード B ではプロジェクトのリポジトリの中に出る。ワークスペースの .gitignore の
# /logs/ はワークスペースルート起点なので当てはまらず、public のリポジトリに運用の痕跡が入る。
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

# 記録のパス。エージェントがそのまま sed -n で開ける形で返す。
#
# 基準はワークスペースルート。モード B ではエージェントの cwd がプロジェクトの中に
# あるので、git のトップからの相対を返すと届かない。ワークスペースの中に居るときは
# 短い相対、そうでなければ絶対を返す（設計 4.1）。
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
		printf '... 残り %d 行は %s にある\n' "$((lines - MAX_LINES))" "$logrel"
	fi
	if [ "$sub" = push ]; then
		push_record_family
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
		printf '案内: ワークツリーのディレクトリを消せませんでした。Windows では、シェルの cwd がその中にあると消せません（Bash ツールの cwd は呼び出しをまたいで残り、サブシェルの中の cd では動きません）。cwd をワークスペースルートに戻す cd を単独で打ち（cd %s）、%s worktree list で登録が外れたかを確かめてください。外れていて空のディレクトリだけが残っていれば rmdir %s で消し、登録が残っていれば同じ remove を打ち直してください。中にファイルが残っているなら消さずにユーザに報告してください。\n' "$WS" "$SELF" "${2:-<パス>}"
	fi
fi

# 世代で切る。新しい順に並べ、上限より後ろを消す。
ls -1t "$logdir"/git-*.log 2>/dev/null | awk -v keep="$KEEP_LOGS" 'NR > keep' |
	while IFS= read -r old; do rm -f "$old"; done

log_info 終わった -- "sub=$sub" "exit=$status" "lines=$lines" "log=$logrel"

[ "$status" -eq 0 ] || exit 1
exit 0
