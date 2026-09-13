#!/bin/sh
# ccnavi-setup — 対象プロジェクトの .claude/settings.json に、ccnavi が想定する
# env と hook を登録する。ccnavi を新しいプロジェクトへ入れるときに人が 1 回打つ。
#
#   sh scripts/ccnavi-setup.sh [<ワークスペースルート>] [オプション]
#
#   --mode <enable|dry-run>  CCNAVI_MODE。既定は dry-run
#   --bin <相対パス>         CCNAVI_BIN_PATH。既定は .ccnavi/bin/ccnavi
#   --ticket-control <enable|disable>
#                            CCNAVI_TICKET_CONTROL。チケット制御を使うか。既定は enable
#   --deploy <ccnavi の根>   配布元。既定はこのスクリプトが入っている ccnavi の根
#   --no-deploy              配布物を置かず、settings.json だけを書く
#   --all                    既定値を持つ env も明示して書く
#   --force                  明示した --mode / --bin / --ticket-control で、既にある値を置き換える。
#                            配るときも、配布先に既にあるものを入れ替える
#   --check                  書かずに、揃っていないところだけを並べる
#   --no-vscode              .vscode/settings.json には触らない
#
# 何度打っても同じ形に落ち着く。既に登録されている hook は足さないし、既にある
# env は触らない。ccnavi と関係のない hook や設定はそのまま残す。
#
# .claude/settings.json と一緒に .vscode/settings.json も見る。ccnavi は作業を
# .claude/worktrees/ の中でさせるので、VS Code にそれを見せる 1 行が無いと、
# エディタからは main の作業ツリーしか見えないまま作業が進む。
#
# 配布物は、ccnavi を組み立てたところ（ccnavi のリポジトリ）から対象プロジェクトへ
# 取る。設定を書くだけでは動かないのに、実行ファイルを置く手立てがどこにも
# 無かった。`dist/` は .gitignore に入っているので git では渡らず、CCNAVI_BIN_PATH は
# 相対でしか書けないので、よそで組んだ実行ファイルを指すこともできない。対象
# プロジェクトの中に実体を置く経路がここに要る。
#
# 配布元は既定でこのスクリプト自身の置き場から取る。設定だけ書かれて実行ファイルが
# 無い形は、hook が 7 つ登録されているのに何も起動しない、という一番分かりにくい
# 壊れ方になる。既定で配布物まで置けば、打った人が `--deploy` を知っているかどうかで
# そこが分かれない。よそから配りたいときだけ `--deploy` で配布元を名指しする。
#
# 既定の配布元が使えないとき（組み立てていない、配布先が ccnavi 自身）は、配るのを
# 諦めて理由を 1 行出し、settings.json は書く。名指しされた `--deploy` が使えない
# ときだけ 2 で断る。人が名指ししたものが無いのは、環境の誤りとして扱う。
#
# 実行ファイルは機械ごとのディレクトリに分けて置く（.ccnavi/bin/<os>-<arch>/）。
# CCNAVI_BIN_PATH が指すのはその 1 つ上の振り分けの sh で、hook が起動した機械に
# 合うものを sh が選ぶ。settings.json は Windows・WSL・Linux・macOS で同じものを開くので、
# 1 行の command から機械ごとに違う実体を起動するには、ここで選ぶしかない。
#
# 前の置き場（.claude/ccnavi/ の実行ファイルと _internal）は、新しい置き場にこの機械で
# 動くものが揃ってから消す。CCNAVI_BIN_PATH が前の既定の綴りのままなら、新しい綴りへ
# 書き換える。
#
# 配布先に既にあるものは触らない。入れ替えるのは `--force` を付けたときだけ。
# ルールファイルもゲートの sh も、入れた先で直されている前提のもの。黙って上書き
# すると、そのプロジェクトが何を止めるかを、打ち直し 1 回で配布元の形へ戻す。
#
# `disable` は受け付けない。監視される側が書けるファイルから監視を止める形に
# なるので、ccnavi 自身がそれを error として報告する（README「設定lint」）。
# 止めるならセッションを起動する側の環境から渡す。
#
# 終了コード: 0 成功 / 1 --check で揃っていない / 2 引数か環境の誤り

set -eu

SETTINGS_REL=".claude/settings.json"
# VS Code へ渡す設定。値ではなくキーの有無で見て、足りないものだけを足す。
# `git.detectWorktrees` は、.claude/worktrees/ の中の作業ツリーをソース管理の
# ビューに出す（README「worktreeをVSCODEで見えるようにする」）。
VSCODE_REL=".vscode/settings.json"
VSCODE_KEYS='{"git.detectWorktrees": true}'
# hook はこの 1 行だけを登録する。どのイベントを走らせるかは、payload が名乗る
# イベント名を見て ccnavi 自身が選ぶ。
HOOK_COMMAND='"${CLAUDE_PROJECT_DIR}/${CCNAVI_BIN_PATH}"'
HOOK_TIMEOUT=10
# 7 つすべてに登録する。1 つ欠けると、そのイベントでしかできない仕事が黙って
# 落ちる。何が落ちるかは README「設定」の表にある。
EVENTS="SessionStart UserPromptSubmit PreToolUse PostToolUse Stop SubagentStart SubagentStop"

DEFAULT_MODE="dry-run"
# 配布先での既定の置き場。配布元の dist/ は組み立ての出力で、.gitignore に
# 入っている場所。配られた側にとっては、そこは「自分が組み立てた物の置き場」
# ではなく、ccnavi が入っている場所。ccnavi ディレクトリ（.ccnavi/）の下に置けば、ゲートの sh と
# 同じ並びに収まり、ccnavi ディレクトリを守るルールがそのまま実行ファイルにも効く。
#
# 指すのは振り分けの sh。実体はその隣の `<os>-<arch>/` に入る。
DEFAULT_BIN=".ccnavi/bin/ccnavi"
# 前の既定の置き場。ここに残った実行ファイルは、新しい置き場が揃ってから消す。
# 同じディレクトリに rules.yml と risk.yml があるので、消すのは名前を決め打ちした 3 つだけ。
OLD_BIN_DIR=".claude/ccnavi"
OLD_BIN_PARTS="ccnavi ccnavi.exe _internal"

# 配るもの。配布元での置き場は build.py の出力（dist/ccnavi）と、ccnavi の
# リポジトリの .claude/ の形に決め打ちで対応する。配布先の綴りは --bin に
# 従うので、実行ファイルと振り分けの sh だけは行き先が動く。
DEPLOY_BIN_DIR="dist/ccnavi"
DEPLOY_LAUNCHER="scripts/ccnavi-launcher.sh"
# どの機械向けに組み立てたかの印。build.py が `<os>-<arch>` の 1 行で書く。
# dist/ccnavi/ の外にあるので、copy_tree が配布先へ写すことはない。
DEPLOY_TARGET_FILE="dist/ccnavi.target"
DEPLOY_RULES=".ccnavi/common/rules.yml"
# 設定 3 本のひな形。rules と risk は汎用なので共通層（.ccnavi/common/）へ、
# phases はワークスペースのレイアウト（scope の綴り）に付くので自身の層
# （.ccnavi/config/）へ配る。共通層に phases を置くと、その scope が
# projects/ の下のプロジェクトにも効いてしまう（設計 §25.12）。
DEPLOY_RISK=".ccnavi/common/risk.yml"
DEPLOY_PHASES=".ccnavi/config/phases.yml"
# 共通層の前の既定の置き場（ADR-0042）。ここに残った設定は新しい置き場へ移し、前の版の
# 導入スクリプトが env に書いた綴りも一緒に書き換える。記録と控えは logs/ に移ったが、
# 前の置き場に残ったものは読まれないだけなので、名前を挙げるだけにする。
OLD_COMMON_DIR=".claude/ccnavi"
NEW_COMMON_DIR=".ccnavi/common"
OLD_COMMON_FILES="rules.yml risk.yml phases.yml rule-samples.yml"
DEPLOY_SCRIPT_DIR=".ccnavi/scripts"
# ccnavi-common.sh は 3 本が `.` で読む共通部分。配らないと、配った先で 3 本とも
# 起動時に落ちる。
DEPLOY_SCRIPTS="ccnavi-ticket.sh ccnavi-review.sh ccnavi-git.sh ccnavi-common.sh"

mode="$DEFAULT_MODE"
bin="$DEFAULT_BIN"
# 明示されたかどうかを分けて持つ。--force が置き換えてよいのは、人がこの実行で
# 名指しした値だけ。既定で埋めただけの値まで置き換えると、`--all` を足しに来た
# 打ち直しが、その場で指定していない CCNAVI_MODE を既定の dry-run へ落とす。
mode_given=no
bin_given=no
# チケット制御。プロジェクトが「全体ルールだけ」か「チケットまで」かを、導入の
# ときに決めてもらう場所。既定は enable で、書かなくても同じに動くが、常に書く。
# 切りたい人が README ではなく設定ファイルの中でつまみを見つけられるように。
ticket_control="enable"
ticket_control_given=no
all=no
force=no
check=no
vscode=yes
target=""
# 配布元。名指しされたかどうかを分けて持つ。既定で埋めただけの配布元が使えない
# のは「組み立てていない」で済むが、人が名指ししたものが使えないのは誤り。
# 同じ変数で持つと、その 2 つを最後まで区別できない。
deploy=""
deploy_given=no
deploy_off=no
# 既定の配布元が使えなかった理由。空でなければ 1 行出す。
deploy_skipped=""

usage() {
	cat <<'USAGE'
sh scripts/ccnavi-setup.sh [<ワークスペースルート>] [オプション]

  <ワークスペースルート>      既定は現在の作業ディレクトリ
  --mode <enable|dry-run>   CCNAVI_MODE。既定は dry-run
  --bin <相対パス>          CCNAVI_BIN_PATH。既定は .ccnavi/bin/ccnavi。ここに振り分けの sh を、
                            隣の <os>-<arch>/ に実行ファイルを置く
  --ticket-control <enable|disable>
                            CCNAVI_TICKET_CONTROL。チケット制御（提案・承認・フェーズ）を
                            使うか。全体ルールだけで足りるプロジェクトは disable。既定は enable
  --deploy <ccnavi の根>    配布元。既定はこのスクリプトが入っている ccnavi の根
  --no-deploy               配布物を置かず、settings.json だけを書く
  --all                     既定値を持つ env も明示して書く
  --force                   明示した --mode / --bin / --ticket-control で、既にある値を置き換える。
                            配るときも、配布先に既にあるものを入れ替える
  --check                   書かずに、揃っていないところだけを並べる
  --no-vscode               .vscode/settings.json には触らない

実行ファイル・振り分けの sh・設定 3 本（.ccnavi/common/rules.yml、.ccnavi/common/risk.yml、
.ccnavi/config/phases.yml）・ゲートの sh は、既定で ccnavi の根から配る。配った
振り分けの sh と実行ファイルの置き場は、配布先の .gitignore に足す。前の置き場
（.claude/ccnavi/ の実行ファイル）は、新しい置き場が揃ってから消す。前の置き場の
共通層の設定（.claude/ccnavi/*.yml）は .ccnavi/common/ へ移し、env の綴りも書き換える。
USAGE
}

die() {
	printf 'ccnavi-setup: %s\n' "$1" >&2
	exit "${2:-2}"
}

while [ "$#" -gt 0 ]; do
	case "$1" in
	--mode)
		[ "$#" -ge 2 ] || die "--mode に値がありません。"
		mode="$2"
		mode_given=yes
		shift 2
		;;
	--bin)
		[ "$#" -ge 2 ] || die "--bin に値がありません。"
		bin="$2"
		bin_given=yes
		shift 2
		;;
	--ticket-control)
		[ "$#" -ge 2 ] || die "--ticket-control に値がありません。"
		ticket_control="$2"
		ticket_control_given=yes
		shift 2
		;;
	--deploy)
		[ "$#" -ge 2 ] || die "--deploy に値がありません。"
		[ "$2" != "" ] || die "--deploy が空です。"
		deploy="$2"
		deploy_given=yes
		shift 2
		;;
	--no-deploy)
		deploy_off=yes
		shift
		;;
	--all)
		all=yes
		shift
		;;
	--force)
		force=yes
		shift
		;;
	--check)
		check=yes
		shift
		;;
	--no-vscode)
		vscode=no
		shift
		;;
	-h | --help | help)
		usage
		exit 0
		;;
	--)
		# ここから先は値として読む。`-` で始まる名前のディレクトリを対象にできる。
		shift
		[ "$#" -eq 1 ] || die "-- のうしろはワークスペースルート 1 つだけです。"
		target="$1"
		shift
		;;
	-*)
		die "$1 は知らないオプションです。使える形は --help に出ます。"
		;;
	*)
		# 空文字を「まだ受け取っていない」と読むと 2 つ受け取れてしまうので、
		# 受け取ったかどうかは別の印で持つ。
		[ "$target" = "" ] || die "ワークスペースルートは 1 つだけ受け取ります。"
		[ "$1" != "" ] || die "ワークスペースルートが空です。"
		target="$1"
		shift
		;;
	esac
done

case "$mode" in
enable | dry-run) ;;
disable)
	die "CCNAVI_MODE=disable は設定ファイルに書いても効きません。止めるならセッションを起動する側の環境から渡してください。"
	;;
*)
	die "--mode に使えるのは enable か dry-run です。"
	;;
esac

# チケット制御は enable か disable の 2 値。ccnavi 側は読めない値を enable に倒し、
# --lint が error にするが、書く前に止めるほうが安い。
case "$ticket_control" in
enable | disable) ;;
*)
	die "--ticket-control に使えるのは enable か disable です。"
	;;
esac

# 実行ファイルの綴りは、hook が何を起動するかと、ccnavi が何を守るかの両方を
# 決める 1 行（selfguard.py 冒頭）。ここが差し替えられると、ルールを 1 行も
# 変えずに判定そのものを入れ替えられるので、`disable` と同じ重さで検査する。
[ "$bin" != "" ] || die "--bin が空です。空のまま書くと hook がワークスペースルートのディレクトリを起動しようとします。"
if [ "$(printf '%s' "$bin" | wc -l)" -ne 0 ]; then
	die "--bin に改行を含められません。"
fi
case "$bin" in
/* | ?:* | //* | '\\'*)
	die "--bin はワークスペースルートからの相対で書いてください。env の値は \${CLAUDE_PROJECT_DIR} を展開しないので、絶対パスは 3 つの環境で綴りが変わります。"
	;;
esac
# 区切りを "/" に寄せてから `..` を探す。相対で書かせる目的はプロジェクトの中に
# 閉じ込めることなので、外へ出る綴りは絶対パスと同じ理由で通さない。
case "/$(printf '%s' "$bin" | tr '\\' '/')/" in
*/../*)
	die "--bin に .. を含められません。ワークスペースルートの外にある実行ファイルは、ここからは指せません。"
	;;
esac
# 指すのは振り分けの sh で、.exe を付けるのは隣に置く実行ファイルの側。付けたまま
# 配ると、sh を .exe の名前で置くことになり、Windows ではそれを起動しようとして落ちる。
case "$bin" in
*.exe | *.EXE)
	die "--bin に .exe を付けないでください。指すのは振り分けの sh で、実行ファイルはその隣の <os>-<arch>/ に置きます。"
	;;
esac

command -v jq >/dev/null 2>&1 || die "jq が要ります。"

[ -n "$target" ] || target="."
[ -d "$target" ] || die "$target というディレクトリがありません。"
# 表示のために絶対化する。Git Bash では pwd -W が Windows 形式を返すので、
# 人が設定ファイルを開くときにそのまま使える綴りになる。
root=$(cd "$target" && { pwd -W 2>/dev/null || pwd; })
settings="$root/$SETTINGS_REL"
claude_dir=$(dirname "$settings")

# 配布元。対象の根を決めたあとで見る。同じ綴りの取り方で絶対化してから
# 突き合わせないと、配布元と配布先が同じかどうかを判定できない。
#
# 名指しが無ければ、このスクリプトの置き場の 1 つ上を配布元にする。scripts/ の
# 下に居るという 1 点だけに寄りかかる。作業ツリーから打てばその作業ツリーの
# dist/ が配布元になり、配布物と、そこに居る自分の変更が食い違わない。
source_root=""
if [ "$deploy_off" = yes ]; then
	if [ "$deploy_given" = yes ]; then
		die "--deploy と --no-deploy は一緒に使えません。どちらを通すかを、ここで決められません。"
	fi
	deploy=""
else
	if [ "$deploy_given" = no ]; then
		deploy=$(dirname "$0")/..
	fi
	if [ ! -d "$deploy" ]; then
		[ "$deploy_given" = no ] ||
			die "$deploy というディレクトリがありません。"
		deploy_skipped="配布元が見つからないので配っていません（--deploy <ccnavi の根> で名指しできます）。"
		deploy=""
	fi
fi
if [ -n "$deploy" ]; then
	source_root=$(cd "$deploy" && { pwd -W 2>/dev/null || pwd; })
	if [ "$source_root" = "$root" ]; then
		# 既定の配布元では普通に起きる。ccnavi のリポジトリ自身に打つと、
		# 配布元と配布先が同じ場所になる。そこは配る先ではないので、
		# 設定だけ書いて配るのは諦める。名指しなら、打った人の思い違い。
		[ "$deploy_given" = no ] ||
			die "--deploy の配布元と配布先が同じです。自分自身へは配れません。"
		deploy_skipped="配布元と配布先が同じなので配っていません。"
		deploy=""
		source_root=""
	fi
fi
# 組み立てていない配布元で黙って進まない。ここを報告だけにすると、
# 「配ったはずなのに実行ファイルが無い」が最後の一覧にしか現れず、
# 打った人は配れたものとして先へ進む。--deploy を名指しした以上、実行ファイルが
# 無いことは環境の誤りとして 2 で断る。既定の配布元なら、組み立てていないだけ
# なので、諦めた理由を出して settings.json は書く。
if [ -n "$deploy" ] && [ ! -d "$source_root/$DEPLOY_BIN_DIR" ]; then
	[ "$deploy_given" = no ] ||
		die "$source_root/$DEPLOY_BIN_DIR がありません。配布元で 'uv run --with pyinstaller python build.py' を回してから打ち直してください。"
	deploy_skipped="$source_root/$DEPLOY_BIN_DIR が無いので配っていません（配布元で 'uv run --with pyinstaller python build.py' を回すと作られます）。"
	deploy=""
	source_root=""
fi

# 実行ファイルをどの機械向けの置き場へ入れるか。PyInstaller の実行ファイルは組み立てた
# 機械の OS と CPU でしか動かないので、配布先では `<os>-<arch>` のディレクトリに分けて
# 並べ、hook が起動する振り分けの sh（scripts/ccnavi-launcher.sh）が起動の時に選ぶ。
# 置き場の名前は build.py が dist/ccnavi.target に書いた印から取る。
#
# 別の機械向けの組み立てでも配る。そのディレクトリに入るだけで、この機械の実行ファイルを
# 上書きしないから。Windows と WSL で同じフォルダを開くなら、両方の組み立てを並べて
# 置ける。この機械で動くものが揃っていないことは、1 行と、最後の「まだ無いもの」で言う。
#
# 印が無い（古い build.py で組んだ）か読めない配布元からは、実行ファイルの置き場を
# 決められない。推測で置くと、別の機械向けをこの機械のディレクトリへ入れうる。
# 断り方は上の「組み立てていない」と揃える。名指しの --deploy なら 2、既定の
# 配布元なら配るのを諦めて理由を出す。
host_target() {
	# 語は build.py の build_target と揃える。
	case "$(uname -s 2>/dev/null)" in
	Linux*) host_os=linux ;;
	Darwin*) host_os=darwin ;;
	MINGW* | MSYS* | CYGWIN*) host_os=windows ;;
	*) host_os=unknown ;;
	esac
	case "$(uname -m 2>/dev/null)" in
	x86_64 | amd64 | AMD64) host_arch=x86_64 ;;
	arm64 | aarch64 | ARM64) host_arch=arm64 ;;
	*) host_arch=unknown ;;
	esac
	printf '%s-%s' "$host_os" "$host_arch"
}

runnable_targets() {
	# $1 この機械。この機械で動く組み立ての語を、先に選ぶ順に空白で並べる。
	# arm64 の macOS と Windows は x86_64 の実行ファイルを変換して動かす（Rosetta 2 /
	# Windows on Arm）。語と順は scripts/ccnavi-launcher.sh と ccnavi/platformtag.py と揃える。
	case "$1" in
	darwin-arm64) printf '%s' "$1 darwin-x86_64" ;;
	windows-arm64) printf '%s' "$1 windows-x86_64" ;;
	*) printf '%s' "$1" ;;
	esac
}

runs_here() {
	# $1 印の値 / $2 runnable_targets の並び
	case " $2 " in
	*" $1 "*) return 0 ;;
	esac
	return 1
}

host=$(host_target)
runnable=$(runnable_targets "$host")
built=""
deploy_target_note=""
if [ -n "$deploy" ]; then
	if [ -f "$source_root/$DEPLOY_TARGET_FILE" ]; then
		built=$(head -n 1 "$source_root/$DEPLOY_TARGET_FILE" | tr -d '\r')
	fi
	# 印はそのままディレクトリ名になる。区切りや `..` を含む値で、置き場の外へ書かせない。
	case "$built" in
	'' | *[!a-z0-9_-]* | -* | *-) built_ok=no ;;
	*-*) built_ok=yes ;;
	*) built_ok=no ;;
	esac
	if [ "$built_ok" = no ]; then
		[ "$deploy_given" = no ] ||
			die "$source_root/$DEPLOY_TARGET_FILE が無いか読めないので、実行ファイルをどの機械向けの置き場に入れるかを決められません。配布元で 'uv run --with pyinstaller python build.py' を回し直してください。"
		deploy_skipped="$source_root/$DEPLOY_TARGET_FILE が無いか読めないので配っていません（配布元で 'uv run --with pyinstaller python build.py' を回し直すと書かれます）。"
		deploy=""
		source_root=""
		built=""
	else
		case "$host" in
		unknown-* | *-unknown)
			deploy_target_note="この機械の OS か CPU を読めない（${host}）ので、${built} 向けの実行ファイルがこの機械で動くかを確かめていません。"
			;;
		*)
			if ! runs_here "$built" "$runnable"; then
				deploy_target_note="配布元の実行ファイルは ${built} 向けで、この機械（${host}）では動きません。${built} の置き場へ入れます。"
			fi
			;;
		esac
	fi
fi

# 配るものを決める。配布先に既にあるものは触らない。入れ替えるのは --force の
# ときだけで、そのときも配布元に在るものだけを動かす。
#
# 「配布元に無い」を黙って飛ばさない。ルールファイルやゲートの sh が欠けた
# 配布元から配ると、判定するものだけが入って何を止めるかが入らない。その形は
# 最後の「まだ無いもの」にしか出ず、配った側の落ち度に見えない。
#
# 設定を書くより前に決める。CCNAVI_BIN_PATH を前の置き場から移してよいかが、
# 新しい置き場に何が揃うかで決まるから。
deploy_new=""
deploy_replacing=""
deploy_kept=""
deploy_absent=""
build_dir_rel=""
bin_verdict=""
launcher_verdict=""
rules_verdict=""
risk_verdict=""
phases_verdict=""
scripts_todo=""

# 配布元に在るか、配布先に在るか、--force か。この 3 つだけで決まる。
verdict() {
	# $1 配布元の絶対パス / $2 配布先に既にあるか（yes/no）
	if [ ! -e "$1" ]; then
		printf 'absent'
	elif [ "$2" = no ]; then
		printf 'copy'
	elif [ "$force" = yes ]; then
		printf 'replace'
	else
		printf 'keep'
	fi
}

note_deploy() {
	# $1 verdict / $2 配布先の綴り / $3 配布元の綴り
	case "$1" in
	copy)
		deploy_new="$deploy_new$2
"
		;;
	replace)
		deploy_replacing="$deploy_replacing$2
"
		;;
	keep)
		deploy_kept="$deploy_kept$2
"
		;;
	absent)
		deploy_absent="$deploy_absent$2（配布元の $3 にありません）
"
		;;
	esac
}

in_list() {
	# $1 空白区切りの並び / $2 探す語
	case " $1 " in
	*" $2 "*) return 0 ;;
	esac
	return 1
}

# 共通層の設定を前の置き場（.claude/ccnavi/）から新しい置き場（.ccnavi/common/）へ移すか。
#
# 移すのは、前の置き場に在って新しい置き場に無いものだけ。両方に在るなら触らずに言う。
# どちらが読まれているかは env が決めていて、どちらが正しいかをここでは決められない。
#
# ひな形を配るより先に決める。前の置き場にそのプロジェクトで育てたルールがあるのに、
# 新しい置き場へ汎用のひな形を配ると、env を書き換えた時点で育てたルールが読まれなくなり、
# 守りが黙ってひな形の強さに戻る。
#
# このスクリプトを打ったセッションの env がまだ前の綴りを読んでいるなら移さない。
# 移した瞬間からそのセッションの判定は読むファイルを失い、組み込みの既定に落ちる。
# そのファイルのひな形も配らない。配ると、開き直して打ち直したときに「両方に在る」に
# なって移せなくなる。
common_move=""
common_move_paths=""
common_move_lines=""
common_both=""
common_both_paths=""
common_held=""
common_held_note=""
for name in $OLD_COMMON_FILES; do
	if [ ! -f "$root/$OLD_COMMON_DIR/$name" ]; then
		continue
	fi
	if [ -e "$root/$NEW_COMMON_DIR/$name" ]; then
		common_both="$common_both$OLD_COMMON_DIR/$name
"
		common_both_paths="$common_both_paths $NEW_COMMON_DIR/$name"
		continue
	fi
	case "$name" in
	rules.yml) session_value="${CCNAVI_RULES:-}" ;;
	risk.yml) session_value="${CCNAVI_RISK:-}" ;;
	phases.yml) session_value="${CCNAVI_PHASES:-}" ;;
	*) session_value="" ;;
	esac
	if [ "$session_value" = "$OLD_COMMON_DIR/$name" ]; then
		common_held="$common_held $NEW_COMMON_DIR/$name"
		common_held_note="$common_held_note$OLD_COMMON_DIR/$name
"
		continue
	fi
	common_move="$common_move $name"
	common_move_paths="$common_move_paths $NEW_COMMON_DIR/$name"
	common_move_lines="$common_move_lines$OLD_COMMON_DIR/$name -> $NEW_COMMON_DIR/$name
"
done

common_arriving() {
	# $1 新しい置き場の綴り。前の置き場から移してくるか、移すのを待っているなら 0。
	# そういうものにはひな形を配らない。--force でも配らない。入れ替えると、移したばかりの
	# 育てたルールをひな形で潰す。
	if in_list "$common_move_paths" "$1" || in_list "$common_held" "$1"; then
		return 0
	fi
	return 1
}

# 振り分けの sh の行き先も、組み立ての置き場も --bin が決める。CCNAVI_BIN_PATH に
# 書く綴りと、実体を置く場所を 1 つの値から出す。ここが割れると、設定は書けているのに
# hook がどこにも無いものを起動する形になる。
bin_dir_rel=$(dirname "$bin")
if [ -n "$deploy" ]; then
	case "$bin_dir_rel" in
	.) build_dir_rel="$built" ;;
	*) build_dir_rel="$bin_dir_rel/$built" ;;
	esac
	# 在るかどうかは 2 つの綴りで見る。PyInstaller が Windows でだけ .exe を付ける。
	if [ -f "$root/$build_dir_rel/ccnavi" ] || [ -f "$root/$build_dir_rel/ccnavi.exe" ]; then
		bin_there=yes
	else
		bin_there=no
	fi
	bin_verdict=$(verdict "$source_root/$DEPLOY_BIN_DIR" "$bin_there")
	note_deploy "$bin_verdict" "$build_dir_rel" "$DEPLOY_BIN_DIR"

	if [ -f "$root/$bin" ]; then
		launcher_there=yes
	else
		launcher_there=no
	fi
	launcher_verdict=$(verdict "$source_root/$DEPLOY_LAUNCHER" "$launcher_there")
	note_deploy "$launcher_verdict" "$bin" "$DEPLOY_LAUNCHER"

	if ! common_arriving "$DEPLOY_RULES"; then
		if [ -e "$root/$DEPLOY_RULES" ]; then
			rules_there=yes
		else
			rules_there=no
		fi
		rules_verdict=$(verdict "$source_root/$DEPLOY_RULES" "$rules_there")
		note_deploy "$rules_verdict" "$DEPLOY_RULES" "$DEPLOY_RULES"
	fi

	if ! common_arriving "$DEPLOY_RISK"; then
		if [ -e "$root/$DEPLOY_RISK" ]; then
			risk_there=yes
		else
			risk_there=no
		fi
		risk_verdict=$(verdict "$source_root/$DEPLOY_RISK" "$risk_there")
		note_deploy "$risk_verdict" "$DEPLOY_RISK" "$DEPLOY_RISK"
	fi

	if [ -e "$root/$DEPLOY_PHASES" ]; then
		phases_there=yes
	else
		phases_there=no
	fi
	phases_verdict=$(verdict "$source_root/$DEPLOY_PHASES" "$phases_there")
	note_deploy "$phases_verdict" "$DEPLOY_PHASES" "$DEPLOY_PHASES"

	for name in $DEPLOY_SCRIPTS; do
		if [ -e "$root/$DEPLOY_SCRIPT_DIR/$name" ]; then
			script_there=yes
		else
			script_there=no
		fi
		script_verdict=$(verdict "$source_root/$DEPLOY_SCRIPT_DIR/$name" "$script_there")
		note_deploy "$script_verdict" "$DEPLOY_SCRIPT_DIR/$name" "$DEPLOY_SCRIPT_DIR/$name"
		case "$script_verdict" in
		copy | replace)
			scripts_todo="$scripts_todo $name"
			;;
		esac
	done
fi

# この機械で動く実行ファイルが、振り分けの sh の隣に在るか。sh と同じ順で探す。
runnable_there() {
	for runnable_target in $runnable; do
		for runnable_name in ccnavi ccnavi.exe; do
			if [ -f "$root/$bin_dir_rel/$runnable_target/$runnable_name" ]; then
				return 0
			fi
		done
	done
	return 1
}

# 配り終えたあと、新しい置き場で hook が起動できるか。振り分けの sh と、この機械で
# 動く実行ファイルの両方が要る。前の置き場から移すかどうかは、これだけで決める。
launcher_ready=no
if [ -f "$root/$bin" ]; then
	launcher_ready=yes
fi
case "$launcher_verdict" in
copy | replace) launcher_ready=yes ;;
esac
exe_ready=no
if runnable_there; then
	exe_ready=yes
elif [ -n "$built" ] && runs_here "$built" "$runnable"; then
	case "$bin_verdict" in
	copy | replace) exe_ready=yes ;;
	esac
fi
new_ready=no
if [ "$launcher_ready" = yes ] && [ "$exe_ready" = yes ]; then
	new_ready=yes
fi

# 書けない形を、jq を回す前に見つける。あとで mkdir が失敗すると、終了コードが
# 1（--check の「揃っていない」）と衝突したうえ、生のエラーだけが出る。
if [ -e "$claude_dir" ] && [ ! -d "$claude_dir" ]; then
	die "$claude_dir がディレクトリではありません。"
fi

# 読む側を 1 か所にする。ファイルが無いときは空のオブジェクトとして扱う。
# ccnavi を入れる前のプロジェクトには settings.json が無いのが普通で、
# それは異常ではない。
if [ -e "$settings" ]; then
	[ -f "$settings" ] || die "$settings がファイルではありません。"
	current=$(cat "$settings")
	# `jq -e` は使わない。`null` や `false` は JSON として正しいのに -e が
	# 偽を返すので、「読めません」という嘘の理由で死ぬことになる。
	printf '%s' "$current" | jq . >/dev/null 2>&1 ||
		die "$SETTINGS_REL が JSON として読めません。直してから打ち直してください。"
else
	current="{}"
fi

# 形の検査。ここを通さずに進むと、想定と違う型に当たった jq が生のエラーを吐いて
# 終了コード 5 で落ちる。5 はこのスクリプトが宣言していない値で、呼んだ側は
# 引数の誤りとも環境の不足とも区別が付かない。
#
# 見るのは ccnavi が触る場所だけ。プロジェクトが置いている他のキーの形は問わない。
shape=$(printf '%s' "$current" | jq -r '
	if type != "object" then "全体が JSON のオブジェクトではありません"
	elif (.env // {} | type) != "object" then ".env がオブジェクトではありません"
	elif (.hooks // {} | type) != "object" then ".hooks がオブジェクトではありません"
	elif (.hooks // {} | to_entries | any(.value | type != "array")) then
		".hooks の中に、配列でないイベントがあります"
	elif ([.hooks // {} | to_entries[] | .value[]] | any(type != "object")) then
		".hooks のイベントの中に、オブジェクトでない要素があります"
	elif ([.hooks // {} | to_entries[] | .value[] | .hooks // []] | any(type != "array")) then
		".hooks のエントリの中に、配列でない hooks があります"
	elif ([.hooks // {} | to_entries[] | .value[] | (.hooks // [])[]] | any(type != "object")) then
		".hooks の中に、オブジェクトでない hook があります"
	elif ([.hooks // {} | to_entries[] | .value[] | (.hooks // [])[] | .command // ""] | any(type != "string")) then
		".hooks の中に、文字列でない command があります"
	else "" end
')
[ -z "$shape" ] || die "$SETTINGS_REL の形を扱えません: ${shape}。直してから打ち直してください。"

# 必ず書く env。既定を持たない CCNAVI_BIN_PATH、既定と同じでも書いておきたい
# 2 つのパス、そして守りのつまみ。設定ファイルだけを見て、どこを読み書きするかと、
# どこまで止まるかが分かる形にする。
#
# 戻す働きの 2 つは CCNAVI_MODE に合わせる。設定が無ければ enable で動くので、
# 書かないまま dry-run で導入すると、判定は止めないのに戻す働きだけが本気で
# 動く。入れた先が様子を見ている間に、いきなり手元のファイルが戻ることになる。
# 導入直後は同じ強さで並ぶほうが、何が起きるかを読める。
#
# 代償は、書いた瞬間から enable が既定ではなくなること。dry-run で入れたまま
# 忘れると、この 2 つも dry-run のまま残る。--mode enable で打ち直すか、
# 設定ファイルの 3 行を書き換えるまで、守りは弱いまま。
#
# チケットの承認の経路だけは、モードに合わせず enable で書く。ここは enable か
# disable しか取らない。承認は通れば済んでしまい、済んだものは報告では戻らないので、
# 「止めずに報告する」段を持てない。dry-run と書くと ccnavi の --lint が error に
# するし、書いた人は止まらないつもりでいるのに実際は止まる。
#
# 値は --arg で 1 つずつ渡す。行に組んでから割ると、値に混ざった改行がそのまま
# 行の区切りになり、ここで拒んだはずの CCNAVI_MODE=disable を --bin 経由で
# 書き込めてしまう。
env_json=$(jq -n --arg mode "$mode" --arg bin "$bin" --arg ticket_control "$ticket_control" '{
	CCNAVI_MODE: $mode,
	CCNAVI_RULES: ".ccnavi/common/rules.yml",
	CCNAVI_LOG: "logs/log.jsonl",
	CCNAVI_BIN_PATH: $bin,
	CCNAVI_RESTORE_IF_DENY: $mode,
	CCNAVI_GUARD_CORE_FILES: $mode,
	CCNAVI_GUARD_TICKET_APPROVAL: "enable",
	CCNAVI_TICKET_CONTROL: $ticket_control
}')
# --all のときだけ足す、既定と同じ値の env。書かなくても同じように動く。
# 書く利点は、あとで値を変えたくなった人が、つまみの一覧を README ではなく
# 設定ファイルの中で見つけられること。
if [ "$all" = yes ]; then
	env_json=$(printf '%s' "$env_json" | jq '. + {
		CCNAVI_STATE: "logs/state",
		CCNAVI_TICKETS_PROPOSAL: "wip/tickets",
		CCNAVI_TICKETS_APPROVED: ".ccnavi/tickets",
		CCNAVI_PHASES: ".ccnavi/common/phases.yml",
		CCNAVI_RISK: ".ccnavi/common/risk.yml",
		CCNAVI_PROJECT_HOME: ".ccnavi"
	}')
fi

# 前の既定の綴りを指したままの env を、新しい既定へ書き換える（ADR-0042）。
#
# 書き換えるのは、値がこのスクリプト自身の前の既定と一字一句同じときだけ。人が別の
# 綴りを書いていれば触らない。設定 3 本は、書き換えたあとに新しい置き場で読めるときだけ
# 書き換える。読めないまま書き換えると、前の置き場に残したルールが読まれなくなる。
# 記録と控えは読めるかどうかに懸からないので、いつも書き換える。前の置き場の記録は
# 残るが、読まれないだけで判定には効かない。
rewrite_json='{}'
env_rewrite_blocked=""
common_ready() {
	# $1 新しい置き場の綴り / $2 そのファイルを配る判定（配らないなら空）
	if in_list "$common_held" "$1"; then
		return 1
	fi
	if [ -e "$root/$1" ] || in_list "$common_move_paths" "$1"; then
		return 0
	fi
	case "$2" in
	copy | replace) return 0 ;;
	esac
	return 1
}
rewrite_env() {
	# $1 キー / $2 前の綴り / $3 新しい綴り / $4 新しい綴りで読めるか（yes/no）
	rewrite_value=$(printf '%s' "$current" | jq -r --arg k "$1" '(.env // {})[$k] // "" | if type == "string" then . else "" end')
	if [ "$rewrite_value" != "$2" ]; then
		return 0
	fi
	if [ "$4" = no ]; then
		env_rewrite_blocked="$env_rewrite_blocked$1 は前の置き場（${2}）を指したままです。${3} に読めるものが揃わないので、書き換えていません。
"
		return 0
	fi
	rewrite_json=$(printf '%s' "$rewrite_json" | jq --arg k "$1" --arg v "$3" '. + {($k): $v}')
}
rewrite_common() {
	# $1 キー / $2 ファイル名 / $3 そのファイルを配る判定
	# 両方の置き場に在るものと、このセッションが読んでいて移すのを待っているものは、
	# env も触らない。どちらを読むかを変えると、読まれるルールがここで入れ替わる。
	# 理由はそれぞれの一覧で既に言う。
	if in_list "$common_both_paths" "$NEW_COMMON_DIR/$2" || in_list "$common_held" "$NEW_COMMON_DIR/$2"; then
		return 0
	fi
	if common_ready "$NEW_COMMON_DIR/$2" "$3"; then
		rewrite_env "$1" "$OLD_COMMON_DIR/$2" "$NEW_COMMON_DIR/$2" yes
	else
		rewrite_env "$1" "$OLD_COMMON_DIR/$2" "$NEW_COMMON_DIR/$2" no
	fi
}
rewrite_common CCNAVI_RULES rules.yml "$rules_verdict"
rewrite_common CCNAVI_RISK risk.yml "$risk_verdict"
rewrite_common CCNAVI_PHASES phases.yml ""
rewrite_env CCNAVI_LOG "$OLD_COMMON_DIR/log.jsonl" "logs/log.jsonl" yes
rewrite_env CCNAVI_STATE "$OLD_COMMON_DIR/state" "logs/state" yes
# 書き換える値は、置き換えてよいキーとして env の組み立てに混ぜる。値の違う env として
# 「変えません」と並べる側に出ないように。
env_json=$(jq -n --argjson a "$env_json" --argjson b "$rewrite_json" '$a + $b')

events_json=$(printf '%s\n' $EVENTS | jq -R -s 'split("\n") | map(select(length > 0))')

# 前の置き場からの移し替え。CCNAVI_BIN_PATH が前の既定の綴りのままなら、新しい既定へ
# 書き換える。「既にある env は触らない」の例外はここだけで、書かれているのが
# このスクリプト自身が前に書いた綴りだから。
#
# 移すのは、新しい置き場で hook が起動できるときだけ。揃わないまま移すと、前の置き場を
# 残していても hook は何も起動しなくなる。--bin を名指ししたときは移さない。人が決めた
# 綴りへ置き換えるのは、これまでどおり --force の仕事。
is_old_bin() {
	case "$1" in
	"$OLD_BIN_DIR/ccnavi" | "$OLD_BIN_DIR/ccnavi.exe") return 0 ;;
	esac
	return 1
}
current_bin=$(printf '%s' "$current" | jq -r '(.env // {}).CCNAVI_BIN_PATH // "" | if type == "string" then . else "" end')
migrate=no
migrate_blocked=""
if [ "$bin_given" = no ] && is_old_bin "$current_bin" && ! is_old_bin "$bin"; then
	if [ "$new_ready" = yes ]; then
		migrate=yes
	else
		migrate_blocked="CCNAVI_BIN_PATH は前の置き場（${current_bin}）を指したままです。${bin} とその隣にこの機械で動く実行ファイルが揃わないので、移していません。"
	fi
fi

# --force が置き換えてよいキー。人がこの実行で名指しした 3 つと、前の置き場からの移し替え。
forced=""
if [ "$force" = yes ]; then
	if [ "$mode_given" = yes ]; then
		forced="$forced CCNAVI_MODE"
	fi
	if [ "$bin_given" = yes ]; then
		forced="$forced CCNAVI_BIN_PATH"
	fi
	if [ "$ticket_control_given" = yes ]; then
		forced="$forced CCNAVI_TICKET_CONTROL"
	fi
fi
if [ "$migrate" = yes ]; then
	forced="$forced CCNAVI_BIN_PATH"
fi
for rewrite_key in $(printf '%s' "$rewrite_json" | jq -r 'keys[]'); do
	forced="$forced $rewrite_key"
done

# 前の置き場（.claude/ccnavi/ の実行ファイルと同梱物）を片付けるか。
#
# 消すのは、書き終えたあとの CCNAVI_BIN_PATH がもうそこを指さず、新しい置き場で hook が
# 起動できるときだけ。加えて、このスクリプトを打ったセッションの hook がまだ前の置き場を
# 起動しているなら消さない。env はセッションを開き直すまで変わらないので、消した瞬間から
# そのセッションの hook は何も起動しなくなり、守りが黙って消える。
#
# --bin が前の置き場そのもの（.claude/ccnavi/）なら何もしない。そこの ccnavi は振り分けの sh。
bin_after="$bin"
if [ -n "$current_bin" ]; then
	case " $forced " in
	*" CCNAVI_BIN_PATH "*) ;;
	*) bin_after="$current_bin" ;;
	esac
fi
old_todo=""
old_note=""
if [ "$(printf '%s' "$bin_dir_rel" | tr '\\' '/')" != "$OLD_BIN_DIR" ]; then
	old_found=""
	for name in $OLD_BIN_PARTS; do
		if [ -e "$root/$OLD_BIN_DIR/$name" ]; then
			old_found="$old_found$OLD_BIN_DIR/$name
"
		fi
	done
	if [ -n "$old_found" ] && [ -z "$migrate_blocked" ]; then
		if is_old_bin "$bin_after"; then
			old_note="前の置き場（${OLD_BIN_DIR}/）に実行ファイルが残っています。CCNAVI_BIN_PATH がまだそこを指しているので消していません。"
		elif [ "$new_ready" = no ]; then
			old_note="前の置き場（${OLD_BIN_DIR}/）に実行ファイルが残っています。${bin} の隣にこの機械で動く実行ファイルが揃うまで消しません。"
		elif is_old_bin "${CCNAVI_BIN_PATH:-}"; then
			old_note="前の置き場（${OLD_BIN_DIR}/）に実行ファイルが残っています。このセッションの hook はまだそこを起動しているので消していません。セッションを開き直してから打ち直すと消します。"
		else
			old_todo="$old_found"
		fi
	fi
fi
forced_json=$(printf '%s\n' $forced | jq -R -s 'split("\n") | map(select(length > 0))')

# 登録の見方。ccnavi の設定lint（lint.py の _registered）は command に "ccnavi" が
# 含まれるかどうかだけで見るが、それだけだと無関係な hook のパスに名前が入って
# いるプロジェクトで、そのイベントが「登録済み」に見えたまま永久に登録されない。
#
# そこで 3 つに分ける。同じ綴りで在る（exact）、ccnavi らしき別の綴りが在る
# （other）、無い（none）。足すのは none だけ。other は足さずに人へ見せる。
# 綴りはプロジェクトごとに違うので、どちらが正しいかをここで決められない。
# 黙って足すと判定が 2 回走り、黙って飛ばすとそのイベントが落ちたままになる。
REGISTERED='def commands($ev): [ (.hooks[$ev] // [])[] | (.hooks // [])[] | .command // "" ];
	def exact($ev; $cmd): [ commands($ev)[] | select(. == $cmd) ] | length > 0;
	def looks($ev): [ commands($ev)[] | ascii_downcase
		| select(test("(^|[^a-z0-9])ccnavi([^a-z0-9]|$)")) ] | length > 0;'

missing_env=$(printf '%s' "$current" | jq -r --argjson env "$env_json" '
	(.env // {}) as $cur | $env | keys_unsorted[] | select($cur[.] == null)
')
# 値が違う env を 2 つに分ける。名指しされた分は置き換え、それ以外は残す。
# 残すほうも黙らない。既に書かれている CCNAVI_MODE=disable のように、
# 揃っているように見えて防御が消えている形が、ここにしか現れない。
replacing_env=$(printf '%s' "$current" | jq -r --argjson env "$env_json" --argjson forced "$forced_json" '
	(.env // {}) as $cur | $env | to_entries[]
	| select($cur[.key] != null and $cur[.key] != .value)
	| select(.key as $k | $forced | index($k) != null)
	| "\(.key): \($cur[.key]) -> \(.value)"
')
differing_env=$(printf '%s' "$current" | jq -r --argjson env "$env_json" --argjson forced "$forced_json" '
	(.env // {}) as $cur | $env | to_entries[]
	| select($cur[.key] != null and $cur[.key] != .value)
	| select(.key as $k | $forced | index($k) == null)
	| "\(.key): \($cur[.key])（このスクリプトが書くのは \(.value)）"
')
# 根を $root に取り置いてから回す。イベント名を `.` に置いたまま関数を呼ぶと、
# 関数の中の `.hooks` が文字列を引くことになる。
missing_hooks=$(printf '%s' "$current" | jq -r --argjson events "$events_json" --arg cmd "$HOOK_COMMAND" "
	$REGISTERED
	. as \$root | \$events[] as \$ev
	| select((\$root | exact(\$ev; \$cmd)) | not)
	| select((\$root | looks(\$ev)) | not) | \$ev
")
other_hooks=$(printf '%s' "$current" | jq -r --argjson events "$events_json" --arg cmd "$HOOK_COMMAND" "
	$REGISTERED
	. as \$root | \$events[] as \$ev
	| select((\$root | exact(\$ev; \$cmd)) | not)
	| select(\$root | looks(\$ev)) | \$ev
")

# 配った実行ファイルを git に入れない。配布先は git で持ち回るのが普通なので、
# .gitignore に無いと、次のコミットで実行ファイルと _internal がまるごと履歴に
# 入る。入ってしまうと、消すには履歴を書き換えるしかない。
#
# 置き場ごと無視はしない。--bin の綴りによっては、振り分けの sh の置き場が
# rules.yml と同じディレクトリ（.claude/ccnavi）になる。そこを丸ごと無視すると、
# そのプロジェクトが何を止めるかまで git から消える。
#
# 綴りは 2 つ。振り分けの sh と、配った組み立ての置き場（`<os>-<arch>/`）。置き場は
# 配った機械のぶんだけ足す。別の機械で打ち直せば、その機械のぶんが足される。
IGNORE_HEADER="# ccnavi が配る実行ファイル（scripts/ccnavi-setup.sh）"
ignore_todo=""
ignore_kept=""
if [ -n "$deploy" ] && [ -e "$root/.git" ]; then
	# `dirname` は `ccnavi` のような直下の綴りに `.` を返す。そのまま並べると
	# `/./ccnavi` になり、git は読めても人には別の場所に見える。
	ignore_dir=$(printf '%s' "$bin_dir_rel" | tr '\\' '/')
	case "$ignore_dir" in
	. | "") ignore_prefix="/" ;;
	*) ignore_prefix="/$ignore_dir/" ;;
	esac
	note_ignore() {
		if [ -f "$root/.gitignore" ] && grep -qxF "$1" "$root/.gitignore"; then
			ignore_kept="$ignore_kept$1
"
		else
			ignore_todo="$ignore_todo$1
"
		fi
	}
	note_ignore "$ignore_prefix$(basename "$bin")"
	note_ignore "$ignore_prefix$built/"
fi

copy_tree() {
	# 中身を 1 つずつ配る。ディレクトリごと入れ替えないのは、配布先が既にある
	# 別のフォルダ（--bin の綴りによってはワークスペースルートそのもの）でも、
	# 配布元が持つ名前のものにしか手が届かないようにするため。
	mkdir -p "$2"
	for entry in "$1"/*; do
		# 配布元が空なら glob がそのまま残る。在るものだけを配る。
		[ -e "$entry" ] || continue
		name=$(basename "$entry")
		# 古い組み立ての残りを持ち越さない。PyInstaller の同梱物は名前で
		# 引かれるので、前の版の .so が残ると新しい実行ファイルがそれを掴む。
		# 名前は配布元に在る entry から取るので、空のパスを消すことはない。
		rm -rf "$2/$name"
		cp -R "$entry" "$2/$name"
	done
}

copy_file() {
	mkdir -p "$(dirname "$2")"
	cp "$1" "$2"
}

# .vscode/settings.json。ここは ccnavi の判定には関わらない。人がエディタから
# worktree を見られるかどうかだけを決める。
#
# 読めない形（VS Code の設定ファイルはコメントや末尾のカンマを書ける）に当たっても
# die しない。ccnavi と関係のない書き方のせいで、肝心の .claude/settings.json まで
# 書けなくなる。触らずに人へ渡して、残りは進める。
vscode_settings="$root/$VSCODE_REL"
vscode_current="{}"
missing_vscode=""
differing_vscode=""
vscode_blocked=""

if [ "$vscode" = yes ]; then
	vscode_dir=$(dirname "$vscode_settings")
	if [ -e "$vscode_dir" ] && [ ! -d "$vscode_dir" ]; then
		vscode_blocked="$vscode_dir がディレクトリではありません。"
	elif [ -e "$vscode_settings" ] && [ ! -f "$vscode_settings" ]; then
		vscode_blocked="$VSCODE_REL がファイルではありません。"
	elif [ -f "$vscode_settings" ]; then
		vscode_current=$(cat "$vscode_settings")
		if ! printf '%s' "$vscode_current" | jq . >/dev/null 2>&1; then
			vscode_blocked="JSON として読めません（コメントや末尾のカンマがあると読めません）。"
		elif [ "$(printf '%s' "$vscode_current" | jq -r 'type')" != object ]; then
			vscode_blocked="全体が JSON のオブジェクトではありません。"
		fi
	fi
fi

if [ "$vscode" = yes ] && [ -z "$vscode_blocked" ]; then
	missing_vscode=$(printf '%s' "$vscode_current" | jq -r --argjson want "$VSCODE_KEYS" '
		. as $cur | $want | keys_unsorted[] | select($cur[.] == null)
	')
	# 値が違うものは変えない。`false` と書いてある設定を true に戻すのは、
	# このスクリプトの仕事ではなく、そう書いた人の判断を消すことになる。
	differing_vscode=$(printf '%s' "$vscode_current" | jq -r --argjson want "$VSCODE_KEYS" '
		. as $cur | $want | to_entries[]
		| select($cur[.key] != null and $cur[.key] != .value)
		| "\(.key): \($cur[.key])（このスクリプトが書くのは \(.value)）"
	')
fi

# 書き込みの前と後で、同じ一覧を違う言葉で見せる。前は「足りない」、後は
# 「足した」。同じ文面のままだと、書けたのか書けなかったのかが読み取れない。
report() {
	if [ -n "$missing_env" ]; then
		printf '%s env:\n' "$1"
		printf '%s\n' "$missing_env" | sed 's/^/  /'
	fi
	if [ -n "$missing_hooks" ]; then
		printf '%s hook:\n' "$2"
		printf '%s\n' "$missing_hooks" | sed 's/^/  /'
	fi
	if [ -n "$replacing_env" ]; then
		printf '%s env:\n' "$3"
		printf '%s\n' "$replacing_env" | sed 's/^/  /'
	fi
	if [ -n "$differing_env" ]; then
		printf '値が違う env（このスクリプトは変えません。変えるなら --mode / --bin / --ticket-control を名指しして --force）:\n'
		printf '%s\n' "$differing_env" | sed 's/^/  /'
	fi
	if [ -n "$other_hooks" ]; then
		printf 'ccnavi らしき別の綴りが登録されている hook（二重に走らせないため足していません。綴りを確かめてください）:\n'
		printf '%s\n' "$other_hooks" | sed 's/^/  /'
	fi
	if [ -n "$missing_vscode" ]; then
		printf '%s %s:\n' "$1" "$VSCODE_REL"
		printf '%s\n' "$missing_vscode" | sed 's/^/  /'
	fi
	if [ -n "$differing_vscode" ]; then
		printf '値が違う %s（このスクリプトは変えません）:\n' "$VSCODE_REL"
		printf '%s\n' "$differing_vscode" | sed 's/^/  /'
	fi
	if [ -n "$vscode_blocked" ]; then
		printf '%s は触っていません: %s\n' "$VSCODE_REL" "$vscode_blocked"
		printf '  %s を自分で足すか、要らないなら --no-vscode を付けてください。\n' "$VSCODE_KEYS"
	fi
}

report_missing() {
	report '足りない' 'ccnavi が登録されていない' '置き換える'
}

# 配布物の報告。env と同じく、配る前と後で言葉を変える。
report_deploy() {
	if [ -n "$deploy_new" ]; then
		printf '%s:\n' "$1"
		printf '%s' "$deploy_new" | sed 's/^/  /'
	fi
	if [ -n "$deploy_replacing" ]; then
		printf '%s:\n' "$2"
		printf '%s' "$deploy_replacing" | sed 's/^/  /'
	fi
	if [ -n "$deploy_kept" ]; then
		printf '配布先に既にあるので配っていないもの（入れ替えるなら --force）:\n'
		printf '%s' "$deploy_kept" | sed 's/^/  /'
	fi
	if [ -n "$deploy_absent" ]; then
		printf '配布元に無くて配れないもの:\n'
		printf '%s' "$deploy_absent" | sed 's/^/  /'
	fi
	if [ -n "$ignore_todo" ]; then
		printf '%s:\n' "$3"
		printf '%s' "$ignore_todo" | sed 's/^/  /'
	fi
	if [ -n "$old_todo" ]; then
		printf '%s:\n' "$4"
		printf '%s' "$old_todo" | sed 's/^/  /'
	fi
	if [ -n "$old_failed" ]; then
		printf '前の置き場から消せなかったもの（使っているセッションがあるかもしれません。閉じてから打ち直してください）:\n'
		printf '%s' "$old_failed" | sed 's/^/  /'
	fi
	if [ -n "$common_move_lines" ]; then
		printf '%s:\n' "$5"
		printf '%s' "$common_move_lines" | sed 's/^/  /'
	fi
	if [ -n "$common_both" ]; then
		printf '前の置き場と新しい置き場（%s/）の両方にある共通層の設定（どちらも触っていません。env が読む側を確かめ、要らないほうを消してください）:\n' "$NEW_COMMON_DIR"
		printf '%s' "$common_both" | sed 's/^/  /'
	fi
	if [ -n "$common_held_note" ]; then
		printf 'このセッションがまだ読んでいるので移していない共通層の設定（セッションを開き直してから打ち直すと移します）:\n'
		printf '%s' "$common_held_note" | sed 's/^/  /'
	fi
	if [ -n "$env_rewrite_blocked" ]; then
		printf '%s' "$env_rewrite_blocked"
	fi
	if [ -n "$migrate_blocked" ]; then
		printf '%s\n' "$migrate_blocked"
	fi
	if [ -n "$old_note" ]; then
		printf '%s\n' "$old_note"
	fi
	if [ -n "$deploy_skipped" ]; then
		printf '%s\n' "$deploy_skipped"
	fi
	if [ -n "$deploy_target_note" ]; then
		printf '%s\n' "$deploy_target_note"
	fi
}

old_failed=""

report_deploy_plan() {
	report_deploy '配る' '入れ替える' '.gitignore に足す' '前の置き場から消す' '前の置き場から移す'
}

# 揃っているか。値の違いと、別の綴りの登録も「揃っていない」に数える。
# ここを不足の 2 つだけで決めると、CCNAVI_MODE=disable が書かれた設定に
# --check を打って「揃っています」と言うことになる。
settled=yes
if [ -n "$missing_env" ] || [ -n "$missing_hooks" ] ||
	[ -n "$replacing_env" ] || [ -n "$differing_env" ] || [ -n "$other_hooks" ] ||
	[ -n "$missing_vscode" ] || [ -n "$differing_vscode" ] || [ -n "$vscode_blocked" ]; then
	settled=no
fi
# 配布物も「揃っていない」に数える。配布元に無いものも数える。実行ファイルが
# 欠けたまま「揃っています」と言うと、--check を門にしている手順がそこを通す。
# .gitignore の不足も数える。足りないまま通すと、次のコミットで実行ファイルが
# 履歴に入る。
if [ -n "$deploy_new" ] || [ -n "$deploy_replacing" ] || [ -n "$deploy_absent" ] ||
	[ -n "$ignore_todo" ]; then
	settled=no
fi
# 前の置き場が片付いていないのも数える。残っている理由が何であれ、人かこのスクリプトが
# もう 1 度手を動かすまで、2 つの置き場が並んだまま。
if [ -n "$old_todo" ] || [ -n "$old_note" ] || [ -n "$migrate_blocked" ]; then
	settled=no
fi
if [ -n "$common_move" ] || [ -n "$common_both" ] || [ -n "$common_held" ] ||
	[ -n "$env_rewrite_blocked" ]; then
	settled=no
fi

if [ "$check" = yes ]; then
	printf '%s\n' "$settings"
	report_missing
	report_deploy_plan
	if [ "$settled" = yes ]; then
		if [ "$vscode" = yes ]; then
			printf 'env と hook と %s は揃っています。\n' "$VSCODE_REL"
		else
			printf 'env と hook は揃っています。\n'
		fi
		exit 0
	fi
	exit 1
fi

# 手を動かすものがあるか。値が違うだけで名指しされていないもの、別の綴りの
# 登録は、このスクリプトが触らないので数えない。ただし黙って終わらせない。
settings_work=no
if [ -n "$missing_env" ] || [ -n "$missing_hooks" ] || [ -n "$replacing_env" ]; then
	settings_work=yes
fi
vscode_work=no
if [ -n "$missing_vscode" ]; then
	vscode_work=yes
fi
deploy_work=no
if [ -n "$deploy_new" ] || [ -n "$deploy_replacing" ] || [ -n "$ignore_todo" ]; then
	deploy_work=yes
fi

printf '%s\n' "$settings"

old_work=no
if [ -n "$old_todo" ] || [ -n "$common_move" ]; then
	old_work=yes
fi

if [ "$settings_work" = no ] && [ "$vscode_work" = no ] && [ "$deploy_work" = no ] &&
	[ "$old_work" = no ]; then
	printf '書き足すものはありません。\n'
	report_missing
	report_deploy_plan
	exit 0
fi

# 設定ファイルへの書き込みを 1 か所にする。控えは最初の 1 回だけ、リンクは
# 切らない、という約束を 2 か所に書き写すと、片方だけ直したときにどちらが
# 壊れるかが打つたびに変わる。
#
# 結果は backed_up と linked に置く。呼んだ側が、その綴りで報告する。
write_json() {
	# 導入前の姿を 1 つだけ残す。2 回目以降は上書きしない。毎回取り直すと、
	# 打ち直した数だけ控えが新しくなり、戻れるのは 1 手前まで――そこには既に
	# ccnavi が入っている――になって、入れる前の設定へ戻す手立てが消える。
	mkdir -p "$(dirname "$1")"
	backed_up=no
	if [ -f "$1" ] && [ ! -e "$1.bak" ]; then
		cp "$1" "$1.bak"
		backed_up=yes
	fi

	# リンクを切らずに書く。`mv` はディレクトリエントリを差し替えるので、設定を
	# 1 か所で持って各プロジェクトから張っている置き方だと、リンクが普通のファイルに
	# なって実体には何も届かない。リンクのときだけ中身を書き、それ以外は同じ
	# ディレクトリに書いてから動かす（途中で切れた設定ファイルを残さないため）。
	linked=no
	if [ -L "$1" ]; then
		linked=yes
	elif [ -e "$1" ]; then
		count=$(ls -ld "$1" 2>/dev/null | awk '{print $2}')
		case "$count" in
		'' | *[!0-9]*) count=1 ;;
		esac
		if [ "$count" -gt 1 ]; then
			linked=yes
		fi
	fi

	if [ "$linked" = yes ]; then
		printf '%s\n' "$2" >"$1"
	else
		tmp="$1.tmp.$$"
		# 途中で落ちたときに書きかけを残さない。設定ファイルの隣に見慣れない
		# ファイルがあると、それが設定なのか残骸なのかを人が判断できない。
		trap 'rm -f "$tmp"' EXIT INT TERM
		printf '%s\n' "$2" >"$tmp"
		mv "$tmp" "$1"
		trap - EXIT INT TERM
	fi
}

# 共通層の設定を前の置き場から移す。env を書き換えるより先に回す。移す途中で落ちたときに、
# env だけが新しい置き場を指して、ルールが前の置き場に取り残される形を作らないため。
# 追跡しているファイルなら、git からは消えて足された形に見える。コミットは人がする。
if [ -n "$common_move" ]; then
	mkdir -p "$root/$NEW_COMMON_DIR"
	for name in $common_move; do
		mv "$root/$OLD_COMMON_DIR/$name" "$root/$NEW_COMMON_DIR/$name"
	done
fi

settings_backed_up=no
settings_linked=no
if [ "$settings_work" = yes ]; then
	# 既存を残す向きでマージする。env は既にある値を勝たせ、名指しされたキーだけを
	# 後勝ちで置き換える。hook は登録が無いイベントにだけ足す。ccnavi と関係のない
	# hook の隣に並ぶ形になるので、他の道具の設定を消さない。
	updated=$(printf '%s' "$current" | jq --argjson env "$env_json" \
		--argjson events "$events_json" \
		--argjson forced "$forced_json" \
		--arg cmd "$HOOK_COMMAND" \
		--argjson timeout "$HOOK_TIMEOUT" "
		$REGISTERED
		(\$env | with_entries(select(.key as \$k | \$forced | index(\$k) != null))) as \$overrides
		| .env = (\$env + (.env // {}) + \$overrides)
		| reduce \$events[] as \$ev (
			.;
			if (exact(\$ev; \$cmd)) or (looks(\$ev)) then .
			else .hooks[\$ev] = ((.hooks[\$ev] // []) + [{
				matcher: \"\",
				hooks: [{type: \"command\", command: \$cmd, timeout: \$timeout}]
			}])
			end
		)
	")
	write_json "$settings" "$updated"
	settings_backed_up=$backed_up
	settings_linked=$linked
fi

# .vscode も同じ向きでマージする。既にあるキーは勝たせ、足りないものだけを足す。
# ccnavi と関係のない VS Code の設定はそのまま残る。
vscode_backed_up=no
vscode_linked=no
if [ "$vscode_work" = yes ]; then
	vscode_updated=$(printf '%s' "$vscode_current" | jq --argjson want "$VSCODE_KEYS" '$want + .')
	write_json "$vscode_settings" "$vscode_updated"
	vscode_backed_up=$backed_up
	vscode_linked=$linked
fi

report '足した' 'ccnavi を登録した' '置き換えた'
if [ "$settings_backed_up" = yes ]; then
	printf '書き換える前の内容は %s.bak にあります（控えは最初の 1 回だけ取ります）。\n' "$SETTINGS_REL"
fi
if [ "$settings_linked" = yes ]; then
	printf '%s はリンクだったので、リンクを保ったまま中身を書きました。\n' "$SETTINGS_REL"
fi
if [ "$vscode_backed_up" = yes ]; then
	printf '書き換える前の内容は %s.bak にあります（控えは最初の 1 回だけ取ります）。\n' "$VSCODE_REL"
fi
if [ "$vscode_linked" = yes ]; then
	printf '%s はリンクだったので、リンクを保ったまま中身を書きました。\n' "$VSCODE_REL"
fi

# 配る。順は実行ファイル → 振り分けの sh → 設定 3 本（ルール・リスクの配点・
# フェーズの種類）→ ゲートの sh。途中で落ちたときに、判定するものだけが在って
# 何を止めるかが無い、という形にしないため。
if [ "$deploy_work" = yes ]; then
	case "$bin_verdict" in
	copy | replace)
		copy_tree "$source_root/$DEPLOY_BIN_DIR" "$root/$build_dir_rel"
		# 実行の許しを付け直す。cp は元のモードを umask で削って写すので、
		# 配布元の側の置き方によってはここが落ちる。落ちていると hook は
		# 「実行ファイルが無い」ではなく「起動できない」で黙って死ぬ。
		for spelling in "$root/$build_dir_rel/ccnavi" "$root/$build_dir_rel/ccnavi.exe"; do
			if [ -f "$spelling" ]; then
				chmod +x "$spelling" 2>/dev/null || true
			fi
		done
		;;
	esac
	case "$launcher_verdict" in
	copy | replace)
		copy_file "$source_root/$DEPLOY_LAUNCHER" "$root/$bin"
		chmod +x "$root/$bin" 2>/dev/null || true
		;;
	esac
	case "$rules_verdict" in
	copy | replace)
		copy_file "$source_root/$DEPLOY_RULES" "$root/$DEPLOY_RULES"
		;;
	esac
	case "$risk_verdict" in
	copy | replace)
		copy_file "$source_root/$DEPLOY_RISK" "$root/$DEPLOY_RISK"
		;;
	esac
	case "$phases_verdict" in
	copy | replace)
		copy_file "$source_root/$DEPLOY_PHASES" "$root/$DEPLOY_PHASES"
		;;
	esac
	for name in $scripts_todo; do
		copy_file "$source_root/$DEPLOY_SCRIPT_DIR/$name" "$root/$DEPLOY_SCRIPT_DIR/$name"
	done
	# .gitignore は足すだけ。既にある行は書かないし、ccnavi と関係のない行にも
	# 触らない。見出しは、この 3 行が何なのかを、あとで開いた人に伝えるためだけの
	# もの。既に同じ見出しがあれば重ねない。
	if [ -n "$ignore_todo" ]; then
		{
			if [ -s "$root/.gitignore" ]; then
				# 末尾に改行が無いファイルへ足すと、最後の行と繋がって別の
				# 綴りになる。無視のつもりの行が、誰も意図しない 1 行に化ける。
				if [ -n "$(tail -c 1 "$root/.gitignore")" ]; then
					printf '\n'
				fi
				# もとから在る行と、ここで足す塊を、空行 1 つで分ける。
				printf '\n'
			fi
			if ! { [ -f "$root/.gitignore" ] && grep -qxF "$IGNORE_HEADER" "$root/.gitignore"; }; then
				printf '%s\n' "$IGNORE_HEADER"
			fi
			printf '%s' "$ignore_todo"
		} >>"$root/.gitignore"
	fi
fi

# 前の置き場を片付ける。配り終えたあとに回す。新しい置き場が揃ったことは上で確かめて
# あるが、ここより前に消すと、配る途中で落ちたときに両方とも無い形が残る。
#
# 消せなかったものは並べて先へ進む。Windows では走っている実行ファイルを消せない。
# 別のセッションの hook がちょうど起動している瞬間に当たることがある。
if [ -n "$old_todo" ]; then
	old_done=""
	for name in $OLD_BIN_PARTS; do
		if [ ! -e "$root/$OLD_BIN_DIR/$name" ]; then
			continue
		fi
		rm -rf "$root/$OLD_BIN_DIR/$name" 2>/dev/null || true
		if [ -e "$root/$OLD_BIN_DIR/$name" ]; then
			old_failed="$old_failed$OLD_BIN_DIR/$name
"
		else
			old_done="$old_done$OLD_BIN_DIR/$name
"
		fi
	done
	old_todo="$old_done"
fi

report_deploy '配った' '入れ替えた' '.gitignore に足した' '前の置き場から消した' '前の置き場から移した'
if [ -n "$old_todo" ]; then
	printf '開いている Claude Code のセッションは、開き直すまで前の置き場を起動しようとして hook が動きません。開き直してください。\n'
fi

# 登録しただけでは動かない。配り終えたあとの姿をそのまま見て、まだ無いものを
# 挙げる。配布が通っていれば普通はここで何も出ない。出たときは、配るのを切ったか、
# 配布元に無かったか、配布先に別のものが既にあって配っていないか、のどれか。
#
# ここでは取りに行ったり作ったりはしない。実行ファイルは振り分けの sh と同じ順で、
# この機械で動く置き場を探す。
missing_parts=""
note_missing() {
	missing_parts="$missing_parts  $1
"
}
if [ ! -f "$root/$bin" ]; then
	note_missing "${bin}（hook が起動する振り分けの sh。ccnavi の根の scripts/ccnavi-launcher.sh を配る）"
fi
case "$host" in
unknown-* | *-unknown) ;;
*)
	if ! runnable_there; then
		note_missing "${bin_dir_rel}/${host}/ccnavi（この機械で動く実行ファイル。この機械で build.py を回して配る）"
	fi
	;;
esac
if [ ! -f "$root/$DEPLOY_RULES" ]; then
	note_missing "${DEPLOY_RULES}（何を止めるか。無いと組み込みの既定だけで判定する）"
fi
if [ ! -f "$root/$DEPLOY_RISK" ]; then
	note_missing "${DEPLOY_RISK}（リスクの配点。無いと組み込みの配点で測る）"
fi
if [ ! -f "$root/$DEPLOY_PHASES" ]; then
	note_missing "${DEPLOY_PHASES}（フェーズの種類。無いと番号だけの挙動になる）"
fi
for name in ccnavi-ticket.sh ccnavi-review.sh ccnavi-git.sh ccnavi-common.sh; do
	if [ ! -f "$root/.ccnavi/scripts/$name" ]; then
		note_missing ".ccnavi/scripts/${name}（ゲートの中で通る形）"
	fi
done
if [ -n "$missing_parts" ]; then
	printf 'まだ無いもの:\n%s' "$missing_parts"
	# 諦めた理由は report_deploy が既に出している。ここで足すのは、人が自分で
	# 配るのを切ったときだけ。理由を二重に出すと、どちらが今の話か分からなくなる。
	if [ "$deploy_off" = yes ]; then
		# 書式の側に置かない。`--` で始まる文字列は、printf がオプションとして
		# 読んで落ちる。
		printf '%s\n' "--no-deploy を外すと、ccnavi の根から配ります。"
	fi
fi

# 置き場を .claude/scripts/ から .ccnavi/scripts/ へ移した。前の配布で置いた写しは、
# もう誰にも読まれないまま残る。消すかどうかは人が決めるので、名前を挙げるだけにする。
old_scripts=""
for name in $DEPLOY_SCRIPTS; do
	if [ -f "$root/.claude/scripts/$name" ]; then
		old_scripts="$old_scripts  .claude/scripts/$name
"
	fi
done
if [ -n "$old_scripts" ]; then
	printf '前の置き場に残っているゲートの sh（今は %s/ を使います。要らなければ消してください）:\n%s' "$DEPLOY_SCRIPT_DIR" "$old_scripts"
fi

# 記録と控えは logs/ に移した（ADR-0042）。前の置き場に残ったものは読まれないまま残る。
# 記録には調べ物の手がかりが入っているので、消すかどうかは人が決める。
old_records=""
for name in log.jsonl state session; do
	if [ -e "$root/$OLD_COMMON_DIR/$name" ]; then
		old_records="$old_records  $OLD_COMMON_DIR/$name
"
	fi
done
if [ -n "$old_records" ]; then
	printf '前の置き場に残っている記録と控え（今は logs/ に書きます。要らなければ消してください）:\n%s' "$old_records"
fi

printf 'env の値はセッションを開き直すまで効きません。\n'
