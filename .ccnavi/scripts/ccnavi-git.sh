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

# 共通部分。ワークスペースルートの探し方と、プロジェクト名の導出はここにある。
. "$(dirname "$0")/ccnavi-common.sh"

# 案内の文専用の、引用した自分の呼び方。パスに空白や ' があっても、そのまま打てる。
# `SELF` は今までの動きのまま変えない。
SELF_Q="sh $(ccnavi_sh_quote "$0")"
WT_SH_Q="sh $(ccnavi_sh_quote "$(dirname "$0")/ccnavi-worktree.sh")"

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
  一覧      branch (-d は可 / -D -m -M -C -f -u は不可)  tag (一覧のみ)
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
            merge (-X ours / -s ours / --no-verify は不可。統合先のユーザのチェックアウトでは --abort / --continue / --quit 以外すべて不可)
            merge-file -p (標準出力に出す形だけ。ファイルへは書かない)
                  両方取り込むときは merge-file -p --union --object-id :2:<パス> :1:<パス> :3:<パス>
                  (現在・祖先・相手の順) で出力し、それを読んで Edit で書く
  通信      fetch  pull  (--force / --prune は不可。取ってくるのは <リモート> <ブランチ> だけで、
                   : や + を含む引数(refspec・URL)は不可。手元の ref は ccnavi-sync.sh が進める)
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

# サブコマンドより前のグローバルオプションは 1 つも受け取らない。
#
# `-c diff.external=<コマンド>` は分類上ただの `git diff` のまま任意コマンドを
# 実行する。危ない設定名 (diff.external / core.pager / core.sshCommand ...) を
# 列挙して拒否するやり方は、列挙から抜けた名前が読み取り専用のまま通るので採らない。値を見ずに
# 形を拒否する。`-C <パス>` と `--git-dir` も、判定の起点が動くので同じ扱い。
case "$1" in
-*) reject global-option "サブコマンドより前のオプション ($1) は受け取りません。$SELF <サブコマンド> ... の形で書き直してください。設定の一時上書きが必要なら、その理由をユーザに伝えてください。" ;;
esac

sub="$1"
shift

# 全引数を走査して、どのサブコマンドでも許さない形を拒否する。前方一致で拾えば
# 等号形 (--output=out.diff) も一緒に拒否されるので、getopt 相当の解析は要らない。
for arg in ${1+"$@"}; do
	case "$arg" in
	-c | --config-env | --config-env=*)
		reject config-override "設定の一時上書き ($arg) は受け取りません。設定の上書きを付けない形で書き直してください。"
		;;
	--output | --output=* | --upload-pack* | --receive-pack* | --exec-path* | --exec=* | --ext-diff | --textconv)
		reject output-or-exec "$arg は、読むだけのサブコマンドをファイル書き込みや外部コマンド実行に変えます。出力を保存したいなら、このラッパースクリプトが logs/ に全量を残すのでそちらを読んでください。"
		;;
	--git-dir | --git-dir=* | --work-tree | --work-tree=* | --namespace | --namespace=* | -C)
		reject moved-root "$arg は判定の起点を別のツリーへ動かします。対象のツリーの中で実行してください。"
		;;
	--?*)
		# 略した表記（`--outp=x`・`--upload-p=...`）。parse-options を使うサブコマンドは長いオプションの
		# 略を受けるので、止める名前の頭にヒットする表記も止める。
		# `--text`（diff の正式な名前）だけは textconv の頭でも通す。
		gl_name="${arg#--}"
		gl_name="${gl_name%%=*}"
		for gl_bad in output upload-pack receive-pack exec-path exec ext-diff textconv git-dir work-tree namespace; do
			case "$gl_bad" in
			"$gl_name"*)
				[ "$gl_name" = text ] && continue
				reject output-or-exec "$arg は、止めているオプション（--${gl_bad}）の略として読まれます。読むだけのサブコマンドをファイル書き込みや外部コマンド実行、判定の起点の移動に変えるので使用できません。"
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
# 名前を並べるやり方では略した表記が通ってしまう。ここは一覧にそのままの表記である名前だけを通し、ほかの `--` は
# 断る（略した表記も断る）。まとめた短いオプション（`-qbnew`）は 1 字ずつ読み、値を取る字が出たら
# 残りの字をその値として扱う。
#
# コールバックは `<コールバック> opt <-x か --name> <値>`・`pos <語>`・`end`（`--` を見た）で呼ぶ。
# 止める判定はコールバックが持つ（一覧に入れた上で、名前を見て reject する）。
ow_reject() {
	reject option-not-allowed "$sub の $1 は使用できません。使用できるオプションは一覧にあるものだけで、長いオプションは略さずに書きます（略した表記を、git がここで止めているオプションとして読むことがあります）。使いたい形があれば、ユーザに伝えて一覧に追加してもらってください。"
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
	# 値を取るオプションで終わった（値が無い）。git もエラーにするが、読み違えないよう断る。
	[ -z "$ow_need" ] || ow_reject "$ow_need"
	return 0
}

# 許可リストを空に戻す。サブコマンドごとに必要なものだけ入れる。
ow_spec() {
	ow_flags=" $1 "
	ow_values=" $2 "
	ow_optvals=" $3 "
	ow_sflags="$4"
	ow_svalues="$5"
	ow_soptvals="${6:-}"
}

# 承認済みチケットの置き場（`.ccnavi/approved/`）とレビュー待ち（`wip/proposals/review/`）に
# ヒットするパスかを見る。ヒットすれば in_store=yes。
#
# `checkout <ref> <パス>`・`restore --source <ref>` で置き場を過去の中身に戻す形と、
# `restore --ours / --theirs` で置き場の衝突を片側の中身で解く形を止めるために使う。
# 置き場を動かすのはユーザと ccnavi のスクリプトで、エージェントが git で戻すと、承認が
# 無かったことにも、承認が戻ったことにもなる。
#
# 比べるのは git のトップからのパス。cwd からの相対（`approved/doing/x.md` を `.ccnavi/` の中で打つ）も
# `..` を取り除いてから比べる。置き場の親（`.ccnavi`・`wip`・`.`）も置き場ごと戻すのでヒットする。
# `*` `?` `[` と `:` で始まる pathspec は、どこにヒットするかをここで決められないのでヒットするとみなす。
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
	sh_approved=.ccnavi/approved   # 固定
	sh_review=wip/proposals/review # 固定
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
	# `.` と `..` を取り除く。トップより上に出たら、このリポジトリの外なのでヒットしない。
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

# ccnavi 自身を守る場所（自己防衛の対象）にヒットするパスかを見る。ヒットすれば in_core=yes。
#
# `rm`・`restore --source`・`checkout <ref> <パス>` で、hook の登録・設定・保護済みのスクリプト・
# 実行ファイル・記録を消す・別のコミットの中身に戻す形を止めるために使う。シェルの rm と Write / Edit は
# hook の自己防衛が止める場所で、このスクリプト自身を消すと生の git の止めも外れる。止めるのは hook が先
# （DENY_WRAPPER_CORE_PATH）で、ここは 2 重目。見るのは既定の置き場の名前だけ（動かした置き場は hook が見る）。
#
# ヒットするとみなすのは、`.ccnavi/` の下・`.claude/settings*.json`・`.claude/hooks/`・ワークスペースルートの
# `logs/` の記録と state と退避・`ccnavi-git.sh`、それらを中に含むディレクトリ（ツリーの頂上と、`.ccnavi`・
# `.claude`・`.git` を直に、または 1 つ下に持つディレクトリ）。`:` で始まる pathspec と、グロブの前に固定の
# ディレクトリが無い指定は、どこにヒットするかを決められないのでヒットするとみなす。大文字小文字はそろえる。
#
# レビュー済みのマーカーを消す
# `rm` はユーザがターミナルで打つ手順で、過去の中身に戻す形は store_hit が止める。エージェントの呼び出しは
# hook が止める（DENY_WRAPPER_CORE_PATH）。
core_hit() {
	in_core=no
	ch_arg=$(printf '%s' "$1" | tr '\\' '/')
	case "$ch_arg" in
	:*)
		in_core=yes
		return 0
		;;
	*'*'* | *'?'* | *'['*)
		# グロブは / もまたぐので、グロブの前の固定のディレクトリより下のどこにでもヒットする。
		ch_arg=$(printf '%s' "$ch_arg" | sed 's/[][*?].*//')
		case "$ch_arg" in
		*/*) ch_arg="${ch_arg%/*}" ;;
		*) ch_arg="" ;;
		esac
		case "$ch_arg" in
		'' | . | ./ | /)
			in_core=yes
			return 0
			;;
		esac
		;;
	esac
	ch_top=$(git rev-parse --show-toplevel 2>/dev/null || :)
	case "$ch_arg" in
	/* | [A-Za-z]:/*) ch_path="$ch_arg" ;;
	*) ch_path="${ch_top:+$ch_top/}$(git rev-parse --show-prefix 2>/dev/null || :)$ch_arg" ;;
	esac
	# `.` と `..` を取り除く。
	ch_norm=""
	ch_ifs="$IFS"
	IFS=/
	set -f
	for ch_part in $ch_path; do
		case "$ch_part" in
		'' | .) ;;
		..)
			case "$ch_norm" in
			*/*) ch_norm="${ch_norm%/*}" ;;
			*) ch_norm="" ;;
			esac
			;;
		*) ch_norm="$ch_norm/$ch_part" ;;
		esac
	done
	set +f
	IFS="$ch_ifs"
	ch_low=$(printf '%s' "$ch_norm/" | tr '[:upper:]' '[:lower:]')
	ch_ws=$(printf '%s' "/${WS_P#/}" | tr '[:upper:]' '[:lower:]')
	case "$ch_low" in
	*/.ccnavi/approved/*) return 0 ;;
	*/.ccnavi/* | */.claude/ | */.claude/hooks/* | */.claude/settings*.json/* | */ccnavi-git.sh/* | \
		"$ch_ws"/logs/ | "$ch_ws"/logs/state/* | "$ch_ws"/logs/archive/* | "$ch_ws"/logs/decisions*.jsonl/*)
		in_core=yes
		return 0
		;;
	esac
	case "/${ch_top#/}" in
	"$ch_norm" | "$ch_norm"/*)
		# ツリーの頂上か、その上。
		in_core=yes
		return 0
		;;
	esac
	# Windows の `C:/x` は上で `/C:/x` になる。ファイルシステムに聞くときは頭の / を外す。
	ch_fs="$ch_norm"
	case "$ch_fs" in
	/[A-Za-z]:/*) ch_fs="${ch_fs#/}" ;;
	esac
	[ -d "$ch_fs" ] || return 0
	for ch_dir in "$ch_fs" "$ch_fs"/*/ "$ch_fs"/.*/; do
		ch_dir="${ch_dir%/}"
		case "$ch_dir" in
		*/. | */..) continue ;;
		esac
		[ -d "$ch_dir" ] || continue
		for ch_mark in .ccnavi .claude .git; do
			if [ -e "$ch_dir/$ch_mark" ]; then
				in_core=yes
				return 0
			fi
		done
	done
	return 0
}
core_reject() {
	reject core-path "$sub で ccnavi 自身を守る場所 ($1) を削除するか、別のコミットの中身に戻す形は使用できません。hook の登録・ルールとフェーズの設定・保護済みのスクリプト・実行ファイル・判定の記録があり、削除したり古い中身に戻したりすると ccnavi の止めが外れます。変更が必要なら、何をなぜ変えたいのかを伝えてユーザに依頼してください。衝突の解決の $SELF restore --ours / --theirs -- <パス> と、索引だけを戻す $SELF restore --staged <パス> は通ります（*?[ や : で始まる指定・--pathspec-from-file は、守る場所にヒットするかを確かめられないので使用できません。ファイルを 1 つずつ名指ししてください）。"
}

# ---- worktree add の起点が古くないか（P14。hook は git もネットワークも起こさない決まりなので、判定はこの sh の中）
#
# 新しいブランチを統合先（名前そのもの、または origin/<統合先>）から切るとき、次を確かめる。
#   (i)  最後の fetch が 10 分より前、または FETCH_HEAD が無い（共通の git ディレクトリの FETCH_HEAD の更新時刻）
#   (ii) 起点が手元の統合先で、origin/<統合先> より遅れている（origin/<統合先> が起点なら対象外）
# 満たさなければ拒否して手順を案内する。ここで fetch は打たない（副作用が増えるため）。
# fetch を試して失敗した記録（このスクリプトの fetch が失敗したときに共通の git ディレクトリへ置く印）が
# 10 分以内にあるときは、(i) は止めずに警告だけにする（オフラインで詰まないため）。
# 起点を省いたときは今のブランチ（HEAD）が起点になるので、それが統合先なら同じ判定をする。
# 既にあるブランチを出す形は新しく切らないので対象外。
wt_fresh_dir() {
	wt_fd=$(git rev-parse --git-common-dir 2>/dev/null) || return 1
	(cd "$wt_fd" 2>/dev/null && pwd)
}
wt_fresh_warn=""
wt_fresh() {
	if [ -n "$wt_new" ]; then
		:
	elif [ -z "$wt_base" ] && ! git show-ref --verify --quiet "refs/heads/$wt_leaf"; then
		:
	else
		return 0
	fi
	wt_f_top=$(ccnavi_phys "$(git rev-parse --show-toplevel 2>/dev/null || :)")
	[ -n "$wt_f_top" ] || return 0
	wt_f_integ=$(ccnavi_integration "$wt_f_top") || return 0
	[ -n "$wt_f_integ" ] || return 0
	wt_f_from="$wt_base"
	case "$wt_f_from" in
	'' | HEAD) wt_f_from=$(git symbolic-ref --quiet --short HEAD 2>/dev/null || :) ;;
	esac
	wt_f_origin=no
	case "$wt_f_from" in
	"$wt_f_integ") ;;
	"origin/$wt_f_integ") wt_f_origin=yes ;;
	*) return 0 ;;
	esac
	git show-ref --verify --quiet "refs/remotes/origin/$wt_f_integ" || return 0
	wt_f_dir=$(wt_fresh_dir) || return 0

	wt_f_behind=0
	wt_f_ahead=0
	if [ "$wt_f_origin" = no ]; then
		git show-ref --verify --quiet "refs/heads/$wt_f_integ" || return 0
		wt_f_behind=$(git rev-list --count "$wt_f_integ..origin/$wt_f_integ" 2>/dev/null || echo 0)
		wt_f_ahead=$(git rev-list --count "origin/$wt_f_integ..$wt_f_integ" 2>/dev/null || echo 0)
	fi

	# 失敗した fetch も FETCH_HEAD を触る（開いて空にする）ので、失敗の印を先に見る。
	wt_f_stale=no
	wt_f_offline=no
	if [ -f "$wt_f_dir/ccnavi-fetch-failed" ] &&
		[ -z "$(find "$wt_f_dir/ccnavi-fetch-failed" -mmin +10 2>/dev/null)" ]; then
		wt_f_offline=yes
		wt_f_stale=yes
	elif [ ! -f "$wt_f_dir/FETCH_HEAD" ]; then
		wt_f_stale=yes
	elif [ -n "$(find "$wt_f_dir/FETCH_HEAD" -mmin +10 2>/dev/null)" ]; then
		wt_f_stale=yes
	fi

	if [ "$wt_f_behind" -ne 0 ] && [ "$wt_f_ahead" -ne 0 ]; then
		reject worktree-base-diverged "ワークツリーの基点 $wt_f_integ が origin/$wt_f_integ と分岐しています（先行 ${wt_f_ahead} 件・遅れ ${wt_f_behind} 件）。ワークツリーの基点は merge では進められません。基点を origin/${wt_f_integ} にして切ると、手元にだけある先行 ${wt_f_ahead} 件を知らないブランチになり、統合で詰まります。ユーザに頼んで、先行分を push するか別のブランチに退避してから、手元の ${wt_f_integ} を origin/${wt_f_integ} に揃えてください。揃えたら再実行します。"
	fi
	if [ "$wt_f_behind" -ne 0 ]; then
		wt_q_integ=$(ccnavi_sh_quote "$wt_f_integ")
		wt_q_leaf=$(ccnavi_sh_quote "$wt_leaf")
		wt_q_dest=$(ccnavi_sh_quote "$wt_dest")
		wt_q_origin=$(ccnavi_sh_quote "origin/$wt_f_integ")
		reject worktree-base-behind "ワークツリーの基点 $wt_f_integ が origin/$wt_f_integ より ${wt_f_behind} 件遅れています。古い統合先から切ると、取り込み済みの変更を知らないブランチになります。直接作業なら \`$WT_SH_Q $wt_q_leaf\` で切り直すと、起点を自動で選びます。手で切るなら \`$SELF_Q fetch origin $wt_q_integ\` のあと、\`$SELF_Q worktree add $wt_q_dest -b $wt_q_leaf --no-track $wt_q_origin\` を実行してください（手元の ${wt_f_integ} は進めない）。"
	fi
	if [ "$wt_f_stale" = yes ] && [ "$wt_f_offline" = no ]; then
		wt_q_integ=$(ccnavi_sh_quote "$wt_f_integ")
		wt_q_leaf=$(ccnavi_sh_quote "$wt_leaf")
		wt_q_dest=$(ccnavi_sh_quote "$wt_dest")
		wt_q_origin=$(ccnavi_sh_quote "origin/$wt_f_integ")
		wt_q_range=$(ccnavi_sh_quote "${wt_f_integ}..origin/${wt_f_integ}")
		reject worktree-fetch-stale "ワークツリーの基点 $wt_f_from の元になる origin/${wt_f_integ} が古いかもしれません（最後の fetch が 10 分より前、または fetch の記録がありません）。まず \`$SELF_Q fetch origin $wt_q_integ\` を実行し、\`$SELF_Q log --oneline $wt_q_range\` で遅れを確かめてから、再実行してください。遅れていれば、直接作業なら \`$WT_SH_Q $wt_q_leaf\` で切り直します。手で切るなら \`$SELF_Q worktree add $wt_q_dest -b $wt_q_leaf --no-track $wt_q_origin\` を実行してください（手元の ${wt_f_integ} は進めない）。"
	fi
	if [ "$wt_f_stale" = yes ]; then
		wt_fresh_warn="fetch を試して失敗した記録があるため、origin/${wt_f_integ} が古いかもしれないまま ${wt_f_from} から切りました（オフラインの可能性）。つながったら $SELF fetch origin ${wt_f_integ} で遅れを確かめてください。"
	fi
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

# そのトップが、ユーザのチェックアウト（git worktree list の先頭）か。<git のトップ>
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
	printf '案内: 一時ワークツリー %s を残した（%s）。中身はユーザのものかもしれないので、黙って消さない。ユーザに確かめてください。\n' "$d4_tmp" "$d4_tmp_why"
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
	[ -f "$d4_rec" ] ||
		reject integrate-no-record "$ig_n の直接作業の記録がありません（ccnavi-direct/${ig_n}）。更新前に切ったワークツリーや、手で切ったワークツリーは、ユーザの確認を経て $WT_SH_Q $ig_n --adopt --base <取り込み先> で引き取ります。"
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
	d4_nb=$(ccnavi_ask "$WS" tree bound "$ig_wt" "$ig_n") || d4_nb=""
	if [ "$(printf '%s\n' "$d4_nb" | head -n 1)" != "tree 1" ]; then
		reject integrate-ticket "$ig_n がチケットに結び付くかを実行ファイルに確かめられないので、取り込みません。"
	fi
	case "$(printf '%s\n' "$d4_nb" | sed -n 's/^bound //p' | head -n 1)" in
	no) ;;
	yes)
		reject integrate-ticket "$ig_n はチケットに結び付くワークツリーです（$(printf '%s\n' "$d4_nb" | sed -n 's/^why //p' | head -n 1)）。チケットの作業は取り込み先へ直接取り込まず、マージリクエストで出します（.ccnavi/docs/worktree-merge.md）。"
		;;
	*) reject integrate-ticket "$ig_n がチケットに結び付くかを決められないので、取り込みません。" ;;
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
		printf '案内: ワークツリー（%s）の中で %s merge %s を実行して衝突を解いてコミットしてから、%s integrate %s を再実行します。\n' \
			"$ig_wt" "$SELF_Q" "$d4_base" "$SELF_Q" "$ig_n"
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
		printf '次: %s integrate-done %s で片付けだけを行う\n' "$SELF_Q" "$ig_n"
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
	printf '案内: このファイルをエディタで開いて保存していない編集があれば、integrate-done の前に保存してもらう\n'
	printf '案内: 取り込み結果は一時ワークツリーで検証できる: cd %s && <テストやビルド>。ここは編集しない。プロジェクトのレイヤーのルールが通常どおりヒットする場所ではない\n' "$(ccnavi_sh_quote "$d4_tmp")"
	[ -z "$d4_overlap" ] ||
		printf '案内: 取り込みで増えるパスが、ユーザのチェックアウトの ignored のファイルと重なっている（%s）。integrate-done の fast-forward は上書きせずに止まる。ユーザに退避を頼む\n' "${d4_overlap# }"
	[ -z "$d4_draft_notes" ] ||
		printf '案内: 取り込まない下書き: %s（取り込み結果には含まれず、作業ブランチにだけ残る。作業ブランチを片付けると消える。内容をユーザに伝え、必要なら先に別の場所へ写してもらう）\n' "$d4_draft_notes"
	if [ "$d4_guard" != no ]; then
		printf '案内: 自己防衛の対象（%s）を含む。integrate-done はユーザがターミナルで、エージェントのターンが終わってから打つ\n' "${d4_guard_paths:-対象を確かめられなかった}"
	fi
	if d4_origin_behind "$d4_base"; then
		printf '案内: origin/%s が手元より進んでいる。先に合わせると、古い上に載せずに済む（cd %s && git pull --ff-only origin %s）\n' \
			"$d4_base" "$(ccnavi_sh_quote "$ig_top")" "$d4_base"
	fi
	printf '次: 差分（%s diff %s %s）をユーザに見せ、了承を得たら %s integrate-done %s -m '"'"'<メッセージ>'"'"'。やめるなら %s integrate --abort %s\n' \
		"$SELF_Q" "$d4_head" "$d4_c" "$SELF_Q" "$ig_n" "$SELF_Q" "$ig_n"
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
	printf '案内: 直接作業の記録は残した。作業を続けて、もう一度 %s integrate %s を打てる\n' "$SELF_Q" "$ig_n"
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
	printf '案内: 取り込み自体は成功している。原因を片付けてから、ユーザのチェックアウトで %s integrate-done %s を再実行すると、済んだところを飛ばして続きから進む\n' "$SELF_Q" "$ig_n"
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
		printf '次: ユーザにターミナルで %s をリモートへ送るよう頼む（cd %s && git push origin %s）。ccnavi-git.sh push は取り込み先へ送らない\n' \
			"$d4_base" "$(ccnavi_sh_quote "$ig_top")" "$d4_base"
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
				printf '案内: 原因を直してから %s integrate-done %s -m '"'"'<メッセージ>'"'"' を再実行します。\n' "$SELF_Q" "$ig_n"
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
				printf '案内: フックが何を変えたかをユーザに確かめてもらう。%s integrate --abort %s で戻せる。\n' "$SELF_Q" "$ig_n"
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
		printf '案内: 原因を直してから、同じコマンド（%s integrate-done %s）を再実行すると、K（%s）から再開する。\n' "$SELF_Q" "$ig_n" "$(ig_short "$d4_m_k")"
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
	# `-O` / `--open-files-in-pager` はヒットしたファイルを外部コマンドで開く。まとめた短いオプション（`-iO`）と略も見る。
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
					reject grep-pager "$arg は一致したファイルを外部コマンドで開きます。使用できません。一致したファイルを見るなら、$SELF grep -l <型> で一覧を出し、Read で開いてください。"
				;;
			esac
			;;
		-?*)
			gr_rest="${arg#-}"
			while [ -n "$gr_rest" ]; do
				gr_ch="${gr_rest%"${gr_rest#?}"}"
				gr_rest="${gr_rest#?}"
				case "$gr_ch" in
				O) reject grep-pager "$arg には -O（--open-files-in-pager）が含まれます。一致したファイルを外部コマンドで開くので使用できません。一致したファイルを見るなら、$SELF grep -l <型> で一覧を出し、Read で開いてください。" ;;
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
			reject branch-delete-unmerged "branch -D は未マージのブランチを削除します。安全側の削除 ($SELF branch -d <名前>) を試し、それでも削除したいならユーザに依頼してください。"
			;;
		-M | -C | -c | --move | --copy)
			reject branch-force-move "branch $2 はブランチを改名するか複製し、同じ名前の既存のブランチを上書きすることがあります。ブランチの名前はワークツリーの名前とチケットの識別子に結び付いていて、変えると着手やレビューのときに名前からチケットを引けなくなります。必要な理由をユーザに伝えてください。"
			;;
		--force | --set-upstream-to | --unset-upstream | --edit-description)
			reject branch-force "branch $2 はブランチを強制的に削除するか、設定を書き換えます。安全側の削除 ($SELF branch -d <名前>) を試し、それでも必要ならユーザに依頼してください。"
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
		-*) reject tag-write "tag は一覧のみ使用できます ($arg は不可)。タグを作る・削除するのはユーザに依頼してください。" ;;
		*) tg_words="$arg" ;;
		esac
	done
	if [ -n "$tg_words" ] && [ "$tg_list" = no ]; then
		reject tag-write "tag $tg_words はタグを作ります。tag は一覧のみ使用できます（絞り込むなら $SELF tag -l <型>）。タグを作るのはユーザに依頼してください。"
	fi
	;;

remote)
	for arg in ${1+"$@"}; do
		case "$arg" in
		-v | --verbose | show | get-url) ;;
		-*) reject remote-option "remote は一覧のみ使用できます ($arg は不可)。一覧は $SELF remote -v、1 つの取得先は $SELF remote get-url <名前> で見られます。ほかの操作が必要ならユーザに依頼してください。" ;;
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
		# できる。そのワークツリーは tree_of の探す場所から外れる（ワークスペースルートは git のトップとは別で、ツリーの探索基準だから）。
		#
		# 書き換えずに止める。打ったパスと起きたことが食い違うと、記録を読んだ
		# ユーザが追えなくなる。
		# 行き先は「オプションでない最初の語」。値を取るオプションは値ごと読み飛ばす。
		# 知らないオプションは通さない。通すと行き先を取り違え、検査そのものが
		# 意味を失う。
		#
		# 行き先の名前とブランチ名を揃える。親のブランチ名は親の
		# 識別子で、ワークツリーの名前も同じ。`-B`（既存のブランチを別のコミットへ移すこと）・`--detach`（ブランチの
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
				reject worktree-force-branch "worktree add -B は、同じ名前の既存のブランチを別のコミットへ移します。そのブランチにしか無いコミットが外れ、親のブランチなら承認済みチケットの置き場ごと中身が変わります。新しく切るなら $SELF worktree add .claude/worktrees/<名前> -b <名前> <起点>、既にあるブランチを出すなら $SELF worktree add .claude/worktrees/<ブランチ> <ブランチ> です。リモートに合わせるのは $SYNC <P> です。"
				;;
			--detach | -d)
				reject worktree-detach "worktree add $wt_word はブランチの外（detached HEAD）にワークツリーを作ります。ワークツリーはブランチの上に作り、名前をブランチ名に揃えます（$SELF worktree add .claude/worktrees/<名前> -b <名前> <起点>）。"
				;;
			--force | -f)
				reject worktree-force "worktree add $wt_word は、他のワークツリーが出しているブランチや、登録の残った作成先を上書きします。$SELF worktree list で確かめ、不要な登録は $SELF worktree prune で外してから、オプション無しで実行してください。"
				;;
			--checkout | --no-checkout | --lock | \
				--guess-remote | --no-guess-remote | --track | --no-track | --quiet | -q) ;;
			-*)
				reject worktree-option "worktree add の $wt_word は使用できません。行き先を取り違えると、プロジェクトの中にワークツリーを作ってしまいます。使いたい形があれば、ユーザに伝えて一覧に追加してもらってください。"
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
		[ -n "$wt_dest" ] || reject worktree-no-dest "worktree add に作成先がありません。$SELF worktree add .claude/worktrees/<名前> -b <名前> <起点> の形で実行してください。"
		wt_abs=$(ccnavi_abs "$wt_dest") ||
			reject worktree-dest-unresolved "worktree add の作成先 ($wt_dest) を絶対パスに直せません。親のディレクトリが在るか確かめてください。"
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
			reject worktree-outside "ワークツリーはワークスペースの .claude/worktrees/ の下に 1 段で置きます（ワークツリーは git 管理外の固定の置き場）。$wt_dest は cwd から解くと $wt_abs になり、ワークスペースの外に出ます。$wt_spell と書いてください。"
		fi
		# 行き先の名前 = ブランチ名。-b が無く 2 つ目の語も無ければ、
		# git は行き先の名前でブランチを切るので揃う。
		wt_leaf="${wt_abs##*/}"
		# フォルダ名（識別子）= ブランチ名の / を - に置き換えたもの（ccnavi_branch_to_id）。
		# 識別子のブランチは作らず、ブランチを直接チェックアウトする。-b <ブランチ> は新しく切り、
		# -b が無く起点だけなら既にあるブランチ（手元か origin/）を出す。
		if [ -n "$wt_new" ]; then
			if [ "$(ccnavi_branch_to_id "$wt_new")" != "$wt_leaf" ]; then
				reject worktree-name "worktree add の新しいブランチ名（-b ${wt_new}）から作る識別子（${wt_new} の / を - に置き換えたもの）が作成先の名前（${wt_leaf}）と違います。ワークツリーの名前は、ブランチ名の / を - に置き換えたものにします。ccnavi は親チケットのワークツリーを、名前が親チケットの識別子で、そのブランチをチェックアウトしているものとして探します。名前が違うと、リモートでの承認を取り込む処理（ccnavi-sync.sh と、セッション開始時に ccnavi-fetch.sh が fast-forward で進める処理）がそのワークツリーを見つけられません。$SELF worktree add .claude/worktrees/$(ccnavi_branch_to_id "$wt_new") -b $wt_new <起点> の形にしてください。"
			fi
		elif [ -n "$wt_base" ]; then
			if [ "$(ccnavi_branch_to_id "$wt_base")" != "$wt_leaf" ]; then
				reject worktree-name "worktree add $wt_dest $wt_base は、$wt_base を名前の違う作成先（${wt_leaf}）に出すか、ブランチの外（detached HEAD）に作ります。ワークツリーの名前は、ブランチ名の / を - に置き換えたものにします。新しく切るなら $SELF worktree add $wt_dest -b $wt_leaf ${wt_base}、既にあるブランチ $wt_base を出すなら行き先を .claude/worktrees/$(ccnavi_branch_to_id "$wt_base") にしてください。"
			elif ! git show-ref --verify --quiet "refs/heads/$wt_base" &&
				! git show-ref --verify --quiet "refs/remotes/origin/$wt_base"; then
				# 名前が揃っていても、ブランチでない（タグ・sha）ならブランチの外に作る。
				reject worktree-detach "worktree add $wt_dest $wt_base の $wt_base は手元のブランチでも origin/$wt_base でもないので、ブランチの外（detached HEAD）にワークツリーを作ります。新しく切るなら $SELF worktree add $wt_dest -b $wt_base <起点> にしてください。"
			fi
			# 既にあるブランチをそのまま出す形は、親のワークツリーを作る形でもある。識別子が親に使えるか
			# （58 文字以内・末尾が -<2 桁> でない・字の種類・NFC）を実行ファイルに聞く（字数と字の種類は実行ファイルが見分ける）。
			# 子の識別子の形（<親>-<2 桁>-<2 桁>）は子のワークツリーを出し直す形なので、64 文字と字だけを見る。
			ccnavi_id_ask "$WS" "$wt_base" ||
				reject worktree-id-unchecked "worktree add $wt_dest $wt_base で、$wt_base から作る識別子が使えるかを確かめられません（ccnavi の実行ファイルが答えない）。build.py でビルドするか、scripts/ccnavi-setup.sh で実行ファイルを配置してから再実行してください。"
			if [ "$ccnavi_id_child" = yes ]; then
				wt_id_problem="$ccnavi_id_direct"
			else
				wt_id_problem="$ccnavi_id_problem"
			fi
			[ -z "$wt_id_problem" ] ||
				reject worktree-id "worktree add $wt_dest $wt_base は指定できません。$wt_base から作る識別子 $ccnavi_id_ident は親のワークツリーに指定できません（${wt_id_problem}）。既にあるブランチを親にするときは $START --mr <MR の番号> で始めてください（採用してよいかを確かめてから出します）。"
		fi
		# 統合先から切るなら、起点が古くないか確かめる（名前の検査を通ったあと）。
		wt_fresh
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
				reject worktree-remove-force "worktree remove --force は、未コミットの変更ごとツリーを削除します。中の変更を確かめ、必要なものを退避してからオプション無しの $SELF worktree remove を使ってください。"
				;;
			esac
		}
		wt_walk() {
			shift
			opt_walk wt_cb ${1+"$@"}
		}
		wt_walk ${1+"$@"}
		;;
	*) reject worktree-action "worktree $action は指定できません。指定できるのは list / add / prune / remove です。" ;;
	esac
	;;

stash)
	action="${1:-push}"
	case "$action" in
	list | show | push | save | pop | apply | -*) ;;
	drop | clear)
		reject stash-drop "stash $action は退避した変更を破棄します。中身を $SELF stash show -p で確かめ、不要と判断した理由をユーザに伝えてください。"
		;;
	*) reject stash-action "stash $action は指定できません。指定できるのは list / show / push / pop / apply です。" ;;
	esac
	;;

add)
	: # 索引を変えるだけ。ワークツリーは損なわれない
	;;

rm)
	# 消す側だが、消えるのは git が中身を持っているファイルだけ。git rm は
	# 索引や HEAD と食い違うファイルを既定で拒む。`rm -rf` の代わりとして
	# rules.yml が名指しで勧める経路なので、勧めた先が通る形にする。
	#
	# 通さないのは -f（略した --forc と、まとめた -rf も）。それを付けると、コミットしていない変更ごと消える。
	# git が守っている線がそこなので、こちらで引く線も同じ場所にする。
	# -r は通す。付けても、中の 1 つでも書きかけがあれば git が止める。
	[ "$#" -eq 0 ] && reject rm-no-path "rm は削除するファイルを名指ししてください ($SELF rm <パス>)。"
	# ccnavi 自身を守る場所は、消える形だけ止める。-n / --dry-run は何も消さないので通す
	# （オプションはパスの後ろにも置けるので、全部を読んでから決める）。
	ow_spec "cached quiet dry-run ignore-unmatch sparse pathspec-file-nul force" "pathspec-from-file" "" "rqnf" ""
	rm_dry=no
	rm_core=""
	rm_cb() {
		case "$1:${2:-}" in
		opt:--force | opt:-f)
			reject rm-force "rm の $2 (--force) はコミットしていない変更ごと消します。付けずに実行し、git が止めたなら、その中身を確かめてからユーザに伝えてください。"
			;;
		opt:--dry-run | opt:-n)
			rm_dry=yes
			;;
		pos:. | pos::/ | "pos:*" | "pos::/*" | pos:./)
			reject rm-whole-tree "rm にツリー全体 ($2) を渡すと、追跡されているファイルがまとめて消えます。削除するものを 1 つずつ名指ししてください。"
			;;
		opt:--pathspec-from-file)
			[ -n "$rm_core" ] || rm_core="--pathspec-from-file"
			;;
		pos:*)
			if [ -z "$rm_core" ]; then
				core_hit "$2"
				[ "$in_core" = yes ] && rm_core="$2"
			fi
			;;
		esac
		return 0
	}
	opt_walk rm_cb ${1+"$@"}
	if [ "$rm_dry" = no ] && [ -n "$rm_core" ]; then
		core_reject "$rm_core"
	fi
	;;

restore)
	# 作業中の変更を破棄する側。rules.yml が reset --hard の代わりに名指しで勧める
	# 経路でもあるので、対象を 1 つずつ名指しさせる形だけ通す。
	#
	# --ours / --theirs もここを通る。衝突したパスにしか使えない（普段は
	# エラーになる）ので、マージの最中だけ意味を持つ。ガード自身の設定が
	# 衝突したときに解く方法はここしかない。ルールファイルに衝突マーカーが
	# 入っていると YAML として読めず、判定は組み込みの既定を使っているが、
	# 既定もこの形は止めない（src/ccnavi/policy/builtin.py）。
	#
	# 別のコミットの中身で戻す形（--source）と、衝突を片側の中身で解く形（--ours / --theirs）は、
	# 承認済みチケットの置き場にヒットするパスには使わせない。
	# 置き場を過去の中身に戻すと、承認が無かったことにも、消えたマーカーが戻ったことにもなる。
	# 置き場の衝突は、どちらの承認を採るかをユーザが決める。オプションは許可リストで読む。
	[ "$#" -eq 0 ] && reject restore-no-path "restore は戻すファイルを名指ししてください ($SELF restore <パス>)。"
	rs_source=no
	rs_side=no
	rs_paths=""
	rs_core=""
	ow_spec "staged worktree ours theirs merge quiet progress no-progress overlay no-overlay ignore-unmerged ignore-skip-worktree-bits pathspec-file-nul recurse-submodules no-recurse-submodules" \
		"source conflict pathspec-from-file" "" "SWqm" "s"
	restore_cb() {
		case "$1" in
		opt)
			case "$2" in
			--source | -s) rs_source=yes ;;
			--ours | --theirs) rs_side=yes ;;
			--pathspec-from-file)
				rs_paths="--pathspec-from-file"
				rs_core="--pathspec-from-file"
				;;
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
			if [ -z "$rs_core" ]; then
				core_hit "$2"
				[ "$in_core" = yes ] && rs_core="$2"
			fi
			;;
		esac
		return 0
	}
	opt_walk restore_cb ${1+"$@"}
	if [ -n "$rs_paths" ]; then
		if [ "$rs_side" = yes ]; then
			reject restore-store-side "restore --ours / --theirs で承認済みチケットの置き場にヒットするパス ($rs_paths) の衝突を片側の内容で解決する形は使用できません。置き場の衝突は、どちらの承認を採るかをユーザが決めます。$SELF merge --abort で取り込みをやめ、衝突したパスをユーザに伝えてください。"
		fi
		if [ "$rs_source" = yes ]; then
			reject restore-store-source "restore --source で承認済みチケットの置き場にヒットするパス ($rs_paths) を別のコミットの中身に戻す形は使用できません。置き場を動かすのはユーザと ccnavi のスクリプトです。置き場の中身が食い違っているなら、ユーザに伝えてください（*?[ や: で始まる指定は、置き場にヒットするかを確かめられないので同じく使用できません。ファイルを1つずつ名指ししてください）。"
		fi
	fi
	# 別のコミットの中身で戻す形は、ccnavi 自身を守る場所にも使わせない。索引の中身に戻す形
	# （--ours / --theirs による衝突の解決を含む）は通す。
	if [ "$rs_source" = yes ] && [ -n "$rs_core" ]; then
		core_reject "$rs_core"
	fi
	;;

merge)
	# 早送り以外も通す。.ccnavi/docs/worktree.md の手順は、main が先に進んだ状態から
	# ブランチへ main を取り込む形を必ず通る。そこを --ff-only に絞ると、
	# 枝分かれした時点でブランチが永久に統合されない。衝突の解消はメインの仕事で、
	# 解こうとする操作をラッパースクリプトが止めてしまうと、先に進む方法が無くなる。
	#
	# 止めるのは、衝突をユーザが見ないまま片側を破棄する形だけ。`-X ours` と `-s ours` は
	# もう一方の変更を気づかないうちに消す。並行して動いている他セッションの書きかけが
	# そこに入っていることがあり、消えたことは差分にも記録にも残らない。
	# オプションは許可リストで読む（略した --strategy-o=ours・まとめた -sours も同じに読む）。
	ow_spec "ff no-ff ff-only edit no-edit commit no-commit stat no-stat no-log squash no-squash quiet verbose progress no-progress abort continue quit signoff no-signoff allow-unrelated-histories summary no-summary verify no-verify autostash no-autostash overwrite-ignore no-overwrite-ignore" \
		"message file strategy strategy-option" "log" "qvne" "mFsX"
	mg_finish=no
	merge_cb() {
		[ "$1" = opt ] || return 0
		case "$2" in
		--abort | --continue | --quit) mg_finish=yes ;;
		esac
		case "$2:${3:-}" in
		--strategy:ours | --strategy:theirs | -s:ours | -s:theirs | --strategy-option:ours | --strategy-option:theirs | -X:ours | -X:theirs)
			reject merge-discard-side "$2 $3 を使うと、衝突の片側が気づかないうちに破棄されます。他セッションの書きかけが入っていても差分に残りません。衝突は 1 つずつ中身を見て解いてください。"
			;;
		--no-verify:*)
			reject merge-no-verify "$2 はマージ前の検査を飛ばします。検査が失敗するなら、その原因を直してください。"
			;;
		esac
	}
	opt_walk merge_cb ${1+"$@"}
	# 統合先をチェックアウトしているユーザのチェックアウトでは、merge の全形（--ff-only・--squash・
	# --no-commit を含む）を止める。統合先は integrate が載せるもので、直接 merge すると事後の通知でしか止まらない。
	# 通すのは次の 3 つ。
	#  - --abort / --continue / --quit: 途中の merge を終わらせる操作で、新しく取り込まない。--continue は、
	#    ユーザが衝突を解いたあとに統合先で打つ形があり得るので、エージェントの入口としても止めない。
	#  - ワークツリーの中の merge（main を取り込む形を含む）。
	#  - 統合先でないブランチのチェックアウトの merge。detached HEAD も、統合先のブランチを動かさないので通す。
	# 統合先が決まらないとき（origin が無いなど）は、どれが統合先か言えないので止めない。
	if [ "$mg_finish" = no ]; then
		mg_top=$(git rev-parse --show-toplevel 2>/dev/null || :)
		if [ -n "$mg_top" ] && user_checkout_is "$mg_top"; then
			mg_integ=$(ccnavi_integration "$mg_top" || :)
			mg_cur=$(git symbolic-ref --quiet --short HEAD 2>/dev/null || :)
			if [ -n "$mg_integ" ] && [ "$mg_cur" = "$mg_integ" ]; then
				reject merge-on-target "統合先 $mg_integ のユーザのチェックアウトでは merge できません。統合先へ直接 merge しない決まりで、取り込みは integrate が行います。
次: $WT_SH_Q <名前> でワークツリーを切り、その中で $SELF_Q merge $(ccnavi_sh_quote "$mg_integ") を打って衝突を解いてコミットし、$SELF_Q integrate <名前> で取り込む
案内: 途中の merge を終わらせる --abort / --continue / --quit と、統合先でないブランチでの merge は、ここでも打てます。"
			fi
		fi
	fi
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
			reject merge-file-option "merge-file の $arg は使用できません。使用できるのは -p (--stdout) と --union / --ours / --theirs / --diff3 / --zdiff3 / --object-id / -L <ラベル> / --marker-size=<数> / -q だけで、短いオプションは 1 つずつ分けて書きます。"
			;;
		esac
	done
	if [ "$mf_stdout" = no ]; then
		reject merge-file-no-stdout "merge-file は -p (--stdout) を付けた形のみ使用できます。付けないと1つめのファイルを直に書き換えます。衝突の最中なら $SELF merge-file -p --union --object-id :2:<パス> :1:<パス> :3:<パス> で出力し、その結果を Edit で書いてください。"
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
			reject commit-no-verify "commit の $2 (--no-verify) はコミット前の検査を飛ばします。検査が失敗するなら、その原因を直してください。"
			;;
		--amend)
			reject commit-amend "commit --amend は直前のコミットを書き換えます。直すなら新しいコミットを積んでください。"
			;;
		esac
	}
	opt_walk commit_cb ${1+"$@"}
	;;

checkout | switch)
	# ブランチを移る形は通す。ワークツリーの中身を破棄する形だけ止める。
	# `git checkout -- .` は、書きかけを何も言わずに消す。取り返せない。
	#
	# オプションは許可リストで読む。略した長い
	# オプション（`--force-c`・`--det`・`--orph=`）は断り、まとめた短いオプションは 1 字ずつ読んで、
	# 値を取る字（checkout の b・B、switch の c・C）の後ろは値として扱う（`-qbnew` は -q -b new）。
	#
	# 既存のブランチを別のコミットへ移す形（checkout -B / switch -C・--force-create）は通さない。
	# 親のブランチを移すと、承認済みチケットの置き場ごと別の中身になる。
	#
	# `checkout <ref> <パス>` は `--` が無くてもパスを別のコミットの中身に戻す。承認済みチケットの
	# 置き場にヒットするパスは通さない。オプションでない最初の語が行き先か起点で、2 つ目からがパス。
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
			reject checkout-path "$sub にパスを渡す形は、そのファイルの書きかけを削除します。戻したいファイルがあるなら $SELF restore <パス> を名指しで使ってください。"
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
				reject checkout-store "$sub <ref> <パス> で承認済みチケットの置き場にヒットするパス ($2) を別のコミットの中身に戻す形は使用できません。置き場を動かすのはユーザと ccnavi のスクリプトです。置き場の中身が食い違っているなら、ユーザに伝えてください（*?[ や: で始まる指定は、置き場にヒットするかを確かめられないので同じく使用できません）。"
			fi
			core_hit "$2"
			[ "$in_core" = yes ] && core_reject "$2"
			;;
		opt)
			case "$sub:$2" in
			*:--force | *:-f)
				reject checkout-force "$sub の $2 (--force) は作業中の変更を破棄するので使用できません。退避は $SELF stash push -u です。"
				;;
			switch:--discard-changes | checkout:--ours | checkout:--theirs)
				reject checkout-discard "$2 は作業中の変更を破棄します。退避は $SELF stash push -u です。"
				;;
			checkout:-B | switch:-C | switch:--force-create)
				reject checkout-force-branch "$sub $2 は、同じ名前の既存のブランチを別のコミットへ移します。そのブランチにしか無いコミットが気づかないうちに外れ、親のブランチなら承認済みチケットの置き場ごと中身が変わります。リモートに合わせるなら、親のブランチは $SYNC <P>、ほかのブランチは $SELF merge <リモート>/<ブランチ> です。分かれていて進めないならユーザに伝えてください。新しいブランチは -b（switch は --create）で切ります。"
				;;
			checkout:--pathspec-from-file)
				reject checkout-store "$sub の $2 は、どのパスを戻すかをここで読めないので使用できません。戻したいファイルがあるなら $SELF restore <パス> を名指しで使ってください。"
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
	# 親チケットか提案があるかは ccnavi_parent_tree が実行ファイルに聞く。ccnavi-sync.sh・ccnavi-fetch.sh・
	# standing.home_tree はそれに加えて、ツリーの名前が識別子で、HEAD が同じ名前のブランチを指すことを求める。
	# 別のブランチに移ると、ccnavi-sync.sh とセッション開始時の ccnavi-fetch.sh は、リモートでの承認を
	# このツリーへ取り込まなくなる。実行ファイル（standing.standing）も親のワークツリーを決められず、
	# 親と子のチケットの承認・状態の操作・実行前の判定を止める。
	#
	co_top=$(ccnavi_phys "$(git rev-parse --show-toplevel 2>/dev/null || :)")
	case "$co_top" in
	"$WS_P"/.claude/worktrees/*)
		co_name="${co_top#"$WS_P"/.claude/worktrees/}"
		co_name="${co_name%%/*}"
		# 親チケットか提案があるかを決められない（2）ときも、親のワークツリーとして狭める。
		co_parent=0
		ccnavi_parent_tree "$co_top" "$co_name" || co_parent=$?
		if [ "$co_parent" -ne 1 ]; then
			co_to=""
			if [ -n "$co_new" ]; then
				co_to="$co_new"
			elif [ "$co_detach" = yes ]; then
				co_to="（ブランチの外）"
			elif [ "$co_words" -eq 1 ] || { [ "$co_words" -ge 2 ] && [ "$sub" = switch ]; }; then
				co_to="$co_one"
			fi
			# 移り先は、今のブランチか、ブランチ名の / を - に置き換えたものがフォルダ名（識別子）になる
			# ブランチだけ（HEAD がずれているときの戻しも通す）。
			case "$co_to" in
			'' | HEAD) ;;
			*)
				if [ "$(ccnavi_branch_to_id "$co_to")" != "$co_name" ]; then
					co_cur=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || :)
					[ "$co_to" = "$co_cur" ] ||
						reject parent-worktree-switch "親のワークツリー（.claude/worktrees/${co_name}）では別のブランチ（${co_to}）へ移れません。親のワークツリーは、ブランチ名の / を - に置き換えたものがフォルダ名（${co_name}）になるブランチの上に置きます。ccnavi は親チケットのワークツリーを、名前が親チケットの識別子で、そのブランチをチェックアウトしているものとして探します。別のブランチに移ると、リモートでの承認を取り込む処理（ccnavi-sync.sh と、セッション開始時に ccnavi-fetch.sh が fast-forward で進める処理）がこのワークツリーを飛ばします。親チケットのブランチを一度でも push したか ccnavi-sync.sh で取り込んだことがあると、親と子のチケットの承認・状態の操作（start・finish など）・実行前の判定も止まります。別の作業は別のワークツリーを切ってください（$SELF worktree add .claude/worktrees/<名前> -b <名前> <起点>）。"
				fi
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
				reject fetch-refspec "$2 は取ってきたものを手元の参照へ直に書く形（refspec）か URL です。取ってくるのはブランチ名だけで、$SELF $sub <リモート> <ブランチ> の形で書いてください。手元の ref は $SYNC <ブランチ> が進めます（親のワークツリー以外のブランチは、取ってきた後の$SELF merge <リモート>/<ブランチ>）。"
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
		reject detached-head "現在のHEADがブランチに属していません（detached HEAD）。送る先が決まらないので使用できません。"
	fi
	# 子チケットのワークツリーからは送らない。レビューはマージリクエストの実物に結び、
	# その実物は親ブランチに 1 本だけある。子の成果は親が手元で合流してから、親の
	# ツリーで親が送る。子が自分のブランチをリモートへ置くと、レビューの外に
	# あるブランチができ、ユーザが見た HEAD と合流した HEAD が食い違うことになる。
	# 見分けるのは承認済みチケット（各ツリーの `.ccnavi/approved/{doing,done}/<名前>.md` と
	# レビュー待ちの `wip/proposals/review/<名前>.md`）に `parent:` があるかだけ。
	# 承認済みチケットの無いツリー（チケットを使わないブランチ）は通す。
	# ワークツリーはワークスペースの .claude/worktrees/ の下にある。元リポジトリが
	# プロジェクトでも置き場はワークスペース（ワークツリーは git 管理外の固定の置き場）なので、git の
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
			push_projects=projects # 固定
			case "$push_projects" in
			/* | [A-Za-z]:*) ;;
			*) push_projects="$push_root/$push_projects" ;;
			esac
			for push_tree in "$push_root" "$push_projects"/* "$push_root"/.claude/worktrees/*; do
				[ -d "$push_tree" ] || continue
				# hook 側の既定（settings.py の DEFAULT_APPROVED・DEFAULT_TICKETS）と同じ場所を見る。
				# 食い違うと、この検査が別の場所を見て、気づかないうちに行われなくなる。
				push_copies="$push_tree/.ccnavi/approved" # 固定
				push_proposals="$push_tree/wip/proposals" # 固定
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
	# 固定のリストに無い名前の統合先（develop-v1.0.0 など）も止める。名前は ccnavi-fetch.sh が
	# ワークツリーの起点に使うのと同じ決め方（origin/HEAD → origin/main・master）で、このツリーが
	# 属するリポジトリ（プロジェクトならそのプロジェクト）について決める。決まらなければ上の固定の
	# リストだけで判定する。
	push_integ=$(ccnavi_integration "${push_top:-.}") || push_integ=""
	log_debug push の統合先 -- "integration=$push_integ"
	if [ -n "$push_integ" ] && [ "$push_branch" = "$push_integ" ]; then
		reject push-integration-branch "$push_branch は統合先です。統合はユーザがマージリクエストで行うので、ここへ直接は送りません。作業用のブランチから送ってください。"
	fi
	push_seen_remote=""
	for arg in ${1+"$@"}; do
		case "$arg" in
		-u | --set-upstream | --porcelain | --quiet | -q | --verbose | -v) ;;
		--no-verify)
			reject push-no-verify "$arg は送る前の検査を飛ばします。検査が失敗するなら原因を直してください。"
			;;
		--force-with-lease | --force-with-lease=* | --force-if-includes | -f | --force)
			reject push-force "$arg はリモートの履歴を書き換えます。送り直したい理由をユーザに伝えてください。"
			;;
		-d | --delete)
			reject push-delete "$arg はリモートのブランチを削除します。ユーザに依頼してください。"
			;;
		--all | --mirror | --tags | --follow-tags | --prune | --atomic)
			reject push-all "$arg は今のブランチ以外も動かします。使用できるのは、居るブランチをそのまま送る形だけです。"
			;;
		-*)
			reject push-option "push で $arg は使用できません。使用できるのは 'push [-u] [<リモート>] [$push_branch]' の形だけです。"
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
	# リモートから消えた親のブランチへは送らない（push の関門。write-and-push・ccnavi-user-push-approved.sh と同じ）。
	# 普通の push で作り直すと、消えた理由（改名・消し間違い・破棄した親子のチケット）を確かめないまま親子のチケットが
	# 動き出す。送り先が origin のときだけ見る。送り先は git と同じ順で解く: 引数のリモート →
	# branch.<b>.pushRemote → remote.pushDefault → branch.<b>.remote → origin。
	push_dest="${push_seen_remote:-}"
	if [ -z "$push_dest" ]; then
		push_dest=$(git config --get "branch.$push_branch.pushRemote" 2>/dev/null ||
			git config --get remote.pushDefault 2>/dev/null ||
			git config --get "branch.$push_branch.remote" 2>/dev/null || echo origin)
	fi
	# 設定済みのリモート名でない引数（`:` を含まない URL やローカルのパス）は、origin と同じ URL なら origin とみなす。
	# origin の URL が `:` を含まないローカルのパスのとき、`push <そのパス>` で関門を飛ばせてしまうため。
	if [ "$push_dest" != origin ]; then
		push_dest_url=$(git config --get "remote.$push_dest.url" 2>/dev/null || printf '%s' "$push_dest")
		push_origin_url=$(git config --get remote.origin.url 2>/dev/null || :)
		if [ -n "$push_origin_url" ] && [ "$push_dest_url" = "$push_origin_url" ]; then
			push_dest=origin
		fi
	fi
	if [ "$push_dest" = origin ] && [ -n "$push_root" ]; then
		case "$push_top" in
		"$push_root"/.claude/worktrees/*)
			push_gate_rc=0
			# 秒数は write-and-push・ccnavi-user-push-approved.sh と同じ既定（60 秒。CCNAVI_WRITE_AND_PUSH_TIMEOUT で変えられる）。
			case "${CCNAVI_WRITE_AND_PUSH_TIMEOUT:-}" in
			'' | *[!0-9]*) push_gate_secs=60 ;;
			*) push_gate_secs="$CCNAVI_WRITE_AND_PUSH_TIMEOUT" ;;
			esac
			ccnavi_push_gate "$push_top" "$push_branch" "${push_top##*/}" "$push_root" "$push_gate_secs" || push_gate_rc=$?
			case "$push_gate_rc" in
			0) ;;
			1) reject push-parent-missing "${ccnavi_pg_why}。エージェントは戻せない（生の git は利用できない）ので、ユーザに伝えて止まってください。" ;;
			*) reject push-parent-unverified "${ccnavi_pg_why}。直せなければユーザに伝えて止まってください（生の git で押し通さない）。" ;;
			esac
			;;
		esac
	fi
	;;
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
reset)
	reject reset "reset は作業中の変更やコミットを削除します。退避は $SELF stash push -u、戻すのは $SELF restore <パス> です。リモートに合わせたいなら、親のブランチは $SYNC <P> を実行してください（早送りか merge で取り込み、衝突したら取りやめます）。ほかのブランチは $SELF fetch <リモート> <ブランチ> のあと$SELF merge <リモート>/<ブランチ> です。分かれていて進めない（squash マージの後で fast-forward できない、など）なら、ブランチを移さずにユーザに伝えてください。"
	;;
clean)
	reject clean "$sub は作業中の変更を削除します。退避は $SELF stash push -u、戻すのは $SELF restore <パス> です。"
	;;
rebase | cherry-pick | revert | am | apply | bisect | filter-branch | replace | update-ref | symbolic-ref | reflog | gc | notes)
	reject history-rewrite "$sub は履歴か参照を書き換えます。使用できません。必要な理由をユーザに伝えてください。"
	;;
config)
	reject config "config は設定を読み書きします。値には資格情報が混ざるので使用できません。必要な値はユーザに尋ねてください。"
	;;
clone | submodule | lfs)
	reject import "$sub は外から中身を持ち込みます。使用できません。ユーザに依頼してください。"
	;;
*)
	reject not-allowed "$sub は許可リストにありません。指定できる形は sh .ccnavi/scripts/ccnavi-git.sh --help で確認してください。"
	;;
esac

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
	if [ "$sub" = worktree ] && [ -n "$wt_fresh_warn" ]; then
		printf '警告: %s\n' "$wt_fresh_warn"
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
		printf '案内: ワークツリーのディレクトリを削除できませんでした。Windows では、シェルの cwd がその中にあると削除できません（Bash ツールの cwd は呼び出しをまたいで残り、サブシェルの中の cd では動きません）。cwd をワークスペースルートに戻す cd を単独で打ち（cd %s）、%s worktree list で登録が外れたかを確かめてください。外れていて空のディレクトリだけが残っていれば rmdir %s で消し、登録が残っていれば同じ remove を再実行してください。中にファイルが残っているなら削除せずにユーザに報告してください。\n' "$WS" "$SELF" "${2:-<パス>}"
	fi
fi

# 世代で切る。新しい順に並べ、上限より後ろを消す。
ls -1t "$logdir"/git-*.log 2>/dev/null | awk -v keep="$KEEP_LOGS" 'NR > keep' |
	while IFS= read -r old; do rm -f "$old"; done

log_info 終わった -- "sub=$sub" "exit=$status" "lines=$lines" "log=$logrel"

[ "$status" -eq 0 ] || exit 1
exit 0
