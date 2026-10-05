#!/bin/sh
# ccnavi-setup は、対象プロジェクトの .claude/settings.json に、ccnavi が前提とする
# env と hook を登録する。ccnavi を新しいプロジェクトへ入れるときに、ユーザが 1 回実行する。
#
#   sh scripts/ccnavi-setup.sh [<ワークスペースルート>] [オプション]
#
#   --mode <enable|dry-run>  CCNAVI_MODE。既定は dry-run
#   --ticket-control <enable|disable>
#                            CCNAVI_TICKET_CONTROL。チケット制御を使うか。既定は enable
#   --deploy <ccnavi の根>   配布元。既定はこのスクリプトが入っている ccnavi の根
#   --no-deploy              配布物を置かず、settings.json だけを書く
#   --all                    既定値を持つ env も明示して書く（今は足すものが無い。置き場は固定）
#   --force                  明示した --mode / --ticket-control で、既にある値を置き換える。
#                            配るときも、配布先に既にあるものを入れ替える
#   --check                  書かずに、揃っていないところだけを並べる
#   --no-vscode              .vscode/settings.json には触らない
#   --no-fetch               セッション開始時の取り込み（ccnavi-fetch.sh）を hook に登録しない
#
# 何度実行しても同じ結果になる。既に登録されている hook は足さず、既にある env には
# 触らない。ccnavi と関係のない hook や設定はそのまま残す。例外は廃止した置き場の
# env 6 つで、既にあれば外す（置き場は固定）。
#
# 入れ終わったところで、ワークスペースの git の索引に projects/ の下が載っていないかを見て、
# 載っていれば --lint と同じ文面で知らせる（止めない。索引も変えない）。
#
# .claude/settings.json と一緒に .vscode/settings.json も確かめる。ccnavi は .claude/worktrees/
# の中で作業させるので、VS Code にそこを表示させる 1 行が無いと、エディタには main の
# 作業ツリーしか表示されないまま作業が進む。
#
# 配布物は、ccnavi を組み立てた場所（ccnavi のリポジトリ）から対象プロジェクトへコピーする。
# 設定を書くだけでは動かないのに、実行ファイルを置く手段がどこにも無かった。`dist/` は
# .gitignore に入っているので git では渡らない。CCNAVI_BIN_PATH は相対パスでしか書けないので、
# よそで組み立てた実行ファイルを指すこともできない。対象プロジェクトの中に実体を置く経路が
# ここに要る。
#
# 配布元は、既定ではこのスクリプト自身の置き場から決める。設定だけが書かれて実行ファイルが
# 無いと、hook が 7 つ登録されているのに何も起動しない。これが一番分かりにくい不具合の
# 出方になる。既定で配布物まで置けば、実行したユーザが `--deploy` を知っているかどうかで結果が
# 変わらない。よそから配りたいときだけ、`--deploy` で配布元を指定する。
#
# 既定の配布元が使えないとき（組み立てていない、配布先が ccnavi 自身）は、配布をやめて
# 理由を 1 行出し、settings.json は書く。指定された `--deploy` が使えないときだけ 2 で止める。
# ユーザが指定したものが無いのは、環境の誤りとして扱う。
#
# 置き場は 2 つに分けて固定する。CCNAVI_BIN_PATH が指すのは
# .ccnavi/scripts/ccnavi-launcher.sh（振り分けの sh。代わりに通る sh と同じ置き場）で、
# 実行ファイルは機械ごとに .ccnavi/bin/<os>-<arch>/ に置く。振り分けの sh は、自分の 1 つ上の
# bin/ から、hook を起動した機械に合うものを選ぶ。settings.json は Windows・WSL・Linux・macOS で
# 同じファイルを開く。1 行の command から機械ごとに違う実体を起動するには、sh の中で選ぶしかない。
# 置き場は動かないので、置き場を指定するオプションは持たない。
#
# CCNAVI_BIN_PATH が既定でないパスなら書き換えず、そのパスを挙げて知らせるだけにする。
#
# 配布先に既にあるものには触らない。入れ替えるのは `--force` を付けたときだけ。
# ルールファイルも代わりに通る sh も、入れた先で手直しされている前提のもの。断りなく上書き
# すると、そのプロジェクトが何を止めるかが、1 回の実行し直しで配布元の内容へ戻る。
#
# `disable` は受け付けない。監視される側が書けるファイルで監視を止めることになるので、
# ccnavi 自身がそれを error として報告する（README「設定の検証」）。止めたいときは、
# セッションを起動する側の環境から渡す。
#
# 終了コード: 0 成功 / 1 --check で揃っていない / 2 引数か環境の誤り

set -eu

SETTINGS_REL=".claude/settings.json"
# VS Code へ渡す設定。値ではなくキーがあるかどうかを見て、足りないものだけを足す。
# `git.detectWorktrees` は、.claude/worktrees/ の中のワークツリーをソース管理の
# ビューに表示する（README「ワークツリーを VS Code から見えるようにする」）。
VSCODE_REL=".vscode/settings.json"
VSCODE_KEYS='{"git.detectWorktrees": true}'
# hook はこの 1 行だけを登録する。どのイベントの処理を走らせるかは、payload にある
# イベント名を見て ccnavi 自身が選ぶ。
HOOK_COMMAND='"${CLAUDE_PROJECT_DIR}/${CCNAVI_BIN_PATH}"'
HOOK_TIMEOUT=10
# 7 つのイベントすべてに登録する。1 つでも欠けると、そのイベントでしかできない処理が、
# 気づかないうちに行われなくなる。何が行われなくなるかは README「設定」の表にある。
EVENTS="SessionStart UserPromptSubmit PreToolUse PostToolUse Stop SubagentStart SubagentStop"
# セッションの開始時に、承認済みチケットとマーカー（親ブランチに含まれて届く）と、ワークツリーの
# 起点になるデフォルトブランチを取得する sh。本体とは別の 1 行として SessionStart に登録する。
# 取得しないと、別の機械で承認したものが反映されず、古い main からブランチを切ることになる。
# 通信するので、要らないプロジェクトは --no-fetch で外す。上限は、sh の中の
# タイムアウト監視（1 回 15 秒）が重なっても収まる長さにしてある。
FETCH_COMMAND='sh "${CLAUDE_PROJECT_DIR}/.ccnavi/scripts/ccnavi-fetch.sh"'
FETCH_TIMEOUT=60

DEFAULT_MODE="dry-run"
# CCNAVI_BIN_PATH に書くパス。hook が起動する振り分けの sh で、固定。
# ccnavi ディレクトリ（.ccnavi/）の下に置けば、ccnavi ディレクトリを守るルールが、
# そのまま sh にも実行ファイルにも適用される。
BIN_PATH=".ccnavi/scripts/ccnavi-launcher.sh"
# 実行ファイルの置き場。<os>-<arch>/ はこの下に並ぶ。振り分けの sh はここを探す。
BUILD_ROOT=".ccnavi/bin"

# 配るもの。配布元での置き場は、build.py の出力（dist/ccnavi）と、ccnavi の
# リポジトリの .ccnavi/ の構成に合わせて決め打ちにしている。
DEPLOY_BIN_DIR="dist/ccnavi"
# どの機械向けに組み立てたかの目印。build.py が `<os>-<arch>` の 1 行で書く。
# dist/ccnavi/ の外にあるので、copy_tree が配布先へコピーすることはない。
DEPLOY_TARGET_FILE="dist/ccnavi.target"
DEPLOY_RULES=".ccnavi/common/rules.yml"
# 設定 3 本のひな形。rules と risk は汎用なので共通レイヤー（.ccnavi/common/）へ配る。
# phases はワークスペースのレイアウト（scope のパス）に依存するので、自身のレイヤー
# （.ccnavi/config/）へ配る。共通レイヤーに phases を置くと、その scope が
# projects/ の下のプロジェクトにも適用されてしまう（設計 11.2）。
DEPLOY_RISK=".ccnavi/common/risks.yml"
DEPLOY_PHASES=".ccnavi/config/phases.yml"
DEPLOY_SCRIPT_DIR=".ccnavi/scripts"
# ccnavi-common.sh は、3 本の sh が `.` で読み込む共通部分の入口。入口は同じディレクトリの部品
# ccnavi-common-{state,lock,c1,host,log}.sh を読む。どちらも配らないと、配布先で 3 本とも
# 起動時にエラーで止まる。部品は入口より先に配る。途中で失敗したときに、部品を読む新しい入口だけが
# あって部品が無い状態を作らないため。
# ccnavi-push-approved.sh は、ボードが承認のあとに端末へ送る 1 行の中身。
# 配らないと、配布先のボードは承認済みチケットをコミットして push できない。
# ccnavi-agree.sh は端末から承認するための sh。承認の案内（phase.py）がこのパスを表示するので、
# 配らないと、案内どおりに実行しても動かない。
# ccnavi-fetch.sh はセッション開始時に走る取り込み（FETCH_COMMAND）。ccnavi-sync.sh はユーザが実行する
# 取り込み（分かれた親ブランチの merge、消えた親ブランチの確認、取り込み状態の書き出し）。
# ccnavi-git.sh の拒否の文面が ccnavi-sync.sh を案内するので、配らないと案内どおりに実行しても動かない。
# ccnavi-clean.sh と ccnavi-clean.js は、ワークツリーを片付ける前に生成物を消すもの。Windows では
# node_modules などが残ると worktree remove が途中で止まる。js が本体で、sh は node を探して js を渡す。
# ccnavi-branches.sh は issue・MR に紐づくブランチを探す sh。UserPromptSubmit の hook が依頼文の
# issue・MR の指定を見つけるとこの表記を案内するので、配らないと案内どおりに実行しても動かない。
# ccnavi-start.sh は issue・MR の指定を受けた着手の入口で、hook がこの表記を案内する。中で ccnavi-branches.sh を呼ぶ。
# ccnavi-launcher.sh は hook が起動する振り分けの sh（BIN_PATH）。
# git で追跡する側に置き、代わりに通る sh と同じ手順で配る。配る順番も最後にする。途中で失敗したときに、
# hook が起動する sh だけがあって、代わりに通る sh が無い状態を作らないため。
DEPLOY_SCRIPTS="ccnavi-ticket.sh ccnavi-review.sh ccnavi-git.sh ccnavi-common-state.sh ccnavi-common-lock.sh ccnavi-common-c1.sh ccnavi-common-host.sh ccnavi-common-log.sh ccnavi-common.sh ccnavi-push-approved.sh ccnavi-agree.sh ccnavi-fetch.sh ccnavi-sync.sh ccnavi-clean.sh ccnavi-clean.js ccnavi-branches.sh ccnavi-start.sh ccnavi-launcher.sh"
LAUNCHER_NAME="ccnavi-launcher.sh"

mode="$DEFAULT_MODE"
# 明示されたかどうかを別の変数で持つ。--force で置き換えてよいのは、ユーザがこの実行で
# 指定した値だけ。既定で埋めただけの値まで置き換えると、`--all` を足すための実行し直しが、
# そのとき指定していない CCNAVI_MODE を既定の dry-run に戻す。
mode_given=no
# チケット制御。プロジェクトで「全体ルールだけ」を使うか「チケットまで」使うかを、導入の
# ときに決めてもらう。既定は enable で、書かなくても同じように動くが、常に書く。
# 切りたいユーザが、README ではなく設定ファイルの中でこの設定項目を見つけられるようにするため。
ticket_control="enable"
ticket_control_given=no
all=no
force=no
check=no
vscode=yes
fetch=yes
target=""
# 配布元。指定されたかどうかを別の変数で持つ。既定で埋めただけの配布元が使えないのは
# 「組み立てていない」で済むが、ユーザが指定したものが使えないのは誤り。
# 同じ変数で持つと、この 2 つを最後まで区別できない。
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
  --ticket-control <enable|disable>
                            CCNAVI_TICKET_CONTROL。チケット制御（提案・承認・フェーズ）を
                            使うか。全体ルールだけで足りるプロジェクトは disable。既定は enable
  --deploy <ccnavi の根>    配布元。既定はこのスクリプトが入っている ccnavi の根
  --no-deploy               配布物を置かず、settings.json だけを書く
  --all                     既定値を持つ env も明示して書く。置き場の env は廃止したので、
                            今は足すものが無い
  --force                   明示した --mode / --ticket-control で、既にある値を置き換える。
                            配るときも、配布先に既にあるものを入れ替える
  --check                   書かずに、揃っていないところだけを並べる
  --no-vscode               .vscode/settings.json には触らない
  --no-fetch                セッション開始時の取り込み（ccnavi-fetch.sh）を hook に登録しない。
                            取り込みは通信する。1 台だけで使い、リモートに合わせる必要が無いときに指定する

CCNAVI_BIN_PATH は .ccnavi/scripts/ccnavi-launcher.sh（振り分けの sh）に固定で、実行ファイルは
.ccnavi/bin/<os>-<arch>/ に置く。実行ファイル・設定 3 本（.ccnavi/common/rules.yml、
.ccnavi/common/risks.yml、.ccnavi/config/phases.yml）・代わりに通る sh と振り分けの sh
（.ccnavi/scripts/）は、既定で ccnavi の根から配る。配った実行ファイルの置き場と、
--docs の索引（**/index.jsonl）は、配布先の .gitignore に足す（index.jsonl を否定する
行があれば足さない）。

置き場（記録・控え・提案・承認済みチケット・プロジェクト・ccnavi ディレクトリ）は既定に
固定で、env では動かない。既存の env に CCNAVI_PROJECTS・CCNAVI_PROJECT_HOME・
CCNAVI_TICKETS_PROPOSAL・CCNAVI_TICKETS_APPROVED・CCNAVI_LOG・CCNAVI_STATE があれば外す。
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
	--no-fetch)
		fetch=no
		shift
		;;
	-h | --help | help)
		usage
		exit 0
		;;
	--)
		# ここから先は値として読む。`-` で始まる名前のディレクトリを対象にできる。
		shift
		[ "$#" -eq 1 ] || die "-- の後ろに指定できるのはワークスペースルート 1 つだけです。"
		target="$1"
		shift
		;;
	-*)
		die "$1 は使えないオプションです。使えるオプションは --help で確認できます。"
		;;
	*)
		# 空文字を「まだ受け取っていない」と見なすと、2 つ受け取れてしまう。そのため、
		# 受け取ったかどうかは別の目印で持つ。
		[ "$target" = "" ] || die "ワークスペースルートは 1 つだけ指定してください。"
		[ "$1" != "" ] || die "ワークスペースルートが空です。"
		target="$1"
		shift
		;;
	esac
done

case "$mode" in
enable | dry-run) ;;
disable)
	die "CCNAVI_MODE=disable は設定ファイルに書いても反映されません。止めたいときは、セッションを起動する側の環境から渡してください。"
	;;
*)
	die "--mode に使えるのは enable か dry-run です。"
	;;
esac

# チケット制御は enable か disable の 2 値。ccnavi 側は読めない値を enable として扱い、
# --lint が error にする。それでも、書く前に止めるほうが手間が少ない。
case "$ticket_control" in
enable | disable) ;;
*)
	die "--ticket-control に使えるのは enable か disable です。"
	;;
esac

command -v jq >/dev/null 2>&1 || die "jq が必要です。"

[ -n "$target" ] || target="."
[ -d "$target" ] || die "$target というディレクトリがありません。"
# 表示のために絶対パスにする。Git Bash では pwd -W が Windows 形式のパスを返すので、
# ユーザが設定ファイルを開くときにそのまま使える表記になる。
root=$(cd "$target" && { pwd -W 2>/dev/null || pwd; })
settings="$root/$SETTINGS_REL"
claude_dir=$(dirname "$settings")

# 配布元。対象の根を決めたあとで調べる。配布元と配布先を同じ方法で絶対パスにしてから
# 比べないと、両者が同じ場所かどうかを判定できない。
#
# 指定が無ければ、このスクリプトの置き場の 1 つ上を配布元にする。前提にするのは、
# このスクリプトが scripts/ の下にあるという 1 点だけ。ワークツリーから実行すれば、その
# ワークツリーの dist/ が配布元になるので、配布物と、そこでの自分の変更が食い違わない。
source_root=""
if [ "$deploy_off" = yes ]; then
	if [ "$deploy_given" = yes ]; then
		die "--deploy と --no-deploy は同時に指定できません。どちらを優先するかをこのスクリプトでは決められないので、どちらか一方を指定してください。"
	fi
	deploy=""
else
	if [ "$deploy_given" = no ]; then
		deploy=$(dirname "$0")/..
	fi
	if [ ! -d "$deploy" ]; then
		[ "$deploy_given" = no ] ||
			die "$deploy というディレクトリがありません。"
		deploy_skipped="配布元が見つからないので配っていません（--deploy <ccnavi の根> で配布元を指定できます）。"
		deploy=""
	fi
fi
if [ -n "$deploy" ]; then
	source_root=$(cd "$deploy" && { pwd -W 2>/dev/null || pwd; })
	if [ "$source_root" = "$root" ]; then
		# 既定の配布元ではよく起きる。ccnavi のリポジトリ自身に対して実行すると、配布元と
		# 配布先が同じ場所になる。そこは配る先ではないので、設定だけ書いて配布はやめる。
		# 指定された配布元でこうなるなら、実行したユーザの思い違い。
		[ "$deploy_given" = no ] ||
			die "--deploy の配布元と配布先が同じです。自分自身へは配れません。"
		deploy_skipped="配布元と配布先が同じなので配っていません。"
		deploy=""
		source_root=""
	fi
fi
# 組み立てていない配布元のまま、何も言わずに先へ進まない。ここを報告だけにすると、
# 「配ったはずなのに実行ファイルが無い」ことが最後の一覧にしか出ず、実行したユーザは
# 配布できたものとして先へ進む。--deploy を指定した以上、実行ファイルが無いことは
# 環境の誤りとして 2 で止める。既定の配布元なら組み立てていないだけなので、配布を
# やめた理由を出して settings.json は書く。
if [ -n "$deploy" ] && [ ! -d "$source_root/$DEPLOY_BIN_DIR" ]; then
	[ "$deploy_given" = no ] ||
		die "$source_root/$DEPLOY_BIN_DIR がありません。配布元で 'uv run --with pyinstaller python build.py' で組み立ててから、もう一度このスクリプトを実行してください。"
	deploy_skipped="$source_root/$DEPLOY_BIN_DIR が無いので配っていません（配布元で 'uv run --with pyinstaller python build.py' を実行すると作られます）。"
	deploy=""
	source_root=""
fi

# 実行ファイルをどの機械向けの置き場へ入れるか。PyInstaller の実行ファイルは組み立てた
# 機械の OS と CPU でしか動かない。そのため配布先では `<os>-<arch>` のディレクトリに分けて
# 並べ、hook が起動する振り分けの sh（.ccnavi/scripts/ccnavi-launcher.sh）が起動時に選ぶ。
# 置き場の名前は、build.py が dist/ccnavi.target に書いた目印から取る。
#
# 別の機械向けに組み立てたものでも配る。そのディレクトリに入るだけで、この機械の実行ファイルは
# 上書きしないため。Windows と WSL で同じフォルダを開くなら、両方の組み立てを並べて
# 置ける。この機械で動くものが揃っていないことは、1 行の注記と、最後の「まだ無いもの」で知らせる。
#
# 目印が無いか読めない配布元からは、実行ファイルの置き場を決められない。推測で置くと、
# 別の機械向けのものをこの機械のディレクトリへ入れるおそれがある。
# 止め方は上の「組み立てていない」場合と揃える。指定された --deploy なら 2、既定の
# 配布元なら配布をやめて理由を出す。
host_target() {
	# 名前の付け方は build.py の build_target と揃える。
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
	# $1 この機械。この機械で動く組み立ての名前を、優先する順に空白区切りで並べる。
	# arm64 の macOS と Windows は x86_64 の実行ファイルを変換して動かす（Rosetta 2 /
	# Windows on Arm）。名前と順番は .ccnavi/scripts/ccnavi-launcher.sh と src/ccnavi/infra/platformtag.py と揃える。
	case "$1" in
	darwin-arm64) printf '%s' "$1 darwin-x86_64" ;;
	windows-arm64) printf '%s' "$1 windows-x86_64" ;;
	*) printf '%s' "$1" ;;
	esac
}

runs_here() {
	# $1 目印の値 / $2 runnable_targets のリスト
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
	# 目印の値はそのままディレクトリ名になる。区切り文字や `..` を含む値で、置き場の外へ書き込ませない。
	case "$built" in
	'' | *[!a-z0-9_-]* | -* | *-) built_ok=no ;;
	*-*) built_ok=yes ;;
	*) built_ok=no ;;
	esac
	if [ "$built_ok" = no ]; then
		[ "$deploy_given" = no ] ||
			die "$source_root/$DEPLOY_TARGET_FILE が無いか読めないので、実行ファイルをどの機械向けの置き場に入れるかを決められません。配布元で 'uv run --with pyinstaller python build.py' をもう一度実行してください。"
		deploy_skipped="$source_root/$DEPLOY_TARGET_FILE が無いか読めないので配っていません（配布元で 'uv run --with pyinstaller python build.py' をもう一度実行すると書かれます）。"
		deploy=""
		source_root=""
		built=""
	else
		case "$host" in
		unknown-* | *-unknown)
			deploy_target_note="この機械の OS か CPU を判別できない（${host}）ので、${built} 向けの実行ファイルがこの機械で動くかを確かめていません。"
			;;
		*)
			if ! runs_here "$built" "$runnable"; then
				deploy_target_note="配布元の実行ファイルは ${built} 向けで、この機械（${host}）では動きません。${built} の置き場へ入れます。"
			fi
			;;
		esac
	fi
fi

# 配るものを決める。配布先に既にあるものには触らない。入れ替えるのは --force の
# ときだけで、そのときも配布元にあるものだけを動かす。
#
# 「配布元に無い」ものを、何も言わずに飛ばさない。ルールファイルや代わりに通る sh が
# 欠けた配布元から配ると、判定するものだけが入り、何を止めるかが入らない。その状態は
# 最後の「まだ無いもの」にしか出ず、配った側の落ち度だと読み取れない。
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

# 配布元にあるか、配布先にあるか、--force か。この 3 つだけで決まる。
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
	# $1 verdict / $2 配布先のパス / $3 配布元のパス
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

# 振り分けの sh の行き先も、組み立ての置き場も固定。振り分けの sh は自分の 1 つ上の bin/ を探すので、
# 2 つの置き場が決まっていれば、設定に書くパスと実体を置く場所は食い違わない。
if [ -n "$deploy" ]; then
	build_dir_rel="$BUILD_ROOT/$built"
	# あるかどうかは 2 つの名前で確かめる。PyInstaller は Windows でだけ .exe を付けるため。
	if [ -f "$root/$build_dir_rel/ccnavi" ] || [ -f "$root/$build_dir_rel/ccnavi.exe" ]; then
		bin_there=yes
	else
		bin_there=no
	fi
	bin_verdict=$(verdict "$source_root/$DEPLOY_BIN_DIR" "$bin_there")
	note_deploy "$bin_verdict" "$build_dir_rel" "$DEPLOY_BIN_DIR"

	if [ -e "$root/$DEPLOY_RULES" ]; then
		rules_there=yes
	else
		rules_there=no
	fi
	rules_verdict=$(verdict "$source_root/$DEPLOY_RULES" "$rules_there")
	note_deploy "$rules_verdict" "$DEPLOY_RULES" "$DEPLOY_RULES"

	if [ -e "$root/$DEPLOY_RISK" ]; then
		risk_there=yes
	else
		risk_there=no
	fi
	risk_verdict=$(verdict "$source_root/$DEPLOY_RISK" "$risk_there")
	note_deploy "$risk_verdict" "$DEPLOY_RISK" "$DEPLOY_RISK"

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
		if [ "$name" = "$LAUNCHER_NAME" ]; then
			launcher_verdict="$script_verdict"
		fi
	done
fi

# 配布先の振り分けの sh に実行ビットが無いかを確かめる。hook は sh を `sh` 経由ではなく直接
# 起動するので、実行ビットが無いと 126 で起動に失敗し、判定が 1 つも走らない。sh は git で
# 追跡するので、Windows で足した sh は 100644 で入り、別の機械で clone した直後は実行ビットが
# 無い（設計 launcher-scripts D-3）。
#
# 配る回だけでなく、配布先に既にあって配らない回（keep）でも付け直す。中身は入れ替えない。
# 配布を切った回（--no-deploy）と、配布元と配布先が同じ回には触らない。
launcher_mode_todo=""
if [ -n "$deploy" ] && [ -f "$root/$BIN_PATH" ] && [ ! -x "$root/$BIN_PATH" ]; then
	case "$launcher_verdict" in
	copy | replace) ;;
	*)
		launcher_mode_todo="$BIN_PATH
"
		;;
	esac
fi

# この機械で動く実行ファイルが、実行ファイルの置き場にあるか。振り分けの sh と同じ順で探す。
runnable_there() {
	for runnable_target in $runnable; do
		for runnable_name in ccnavi ccnavi.exe; do
			if [ -f "$root/$BUILD_ROOT/$runnable_target/$runnable_name" ]; then
				return 0
			fi
		done
	done
	return 1
}

# 書き込めない状態を、jq を実行する前に見つける。あとで mkdir が失敗すると、終了コードが
# 1（--check の「揃っていない」）と重なるうえ、生のエラーだけが出る。
if [ -e "$claude_dir" ] && [ ! -d "$claude_dir" ]; then
	die "$claude_dir がディレクトリではありません。"
fi

# 読み込む処理を 1 か所にまとめる。ファイルが無いときは空のオブジェクトとして扱う。
# ccnavi を入れる前のプロジェクトには settings.json が無いのが普通で、
# 異常ではない。
if [ -e "$settings" ]; then
	[ -f "$settings" ] || die "$settings がファイルではありません。"
	current=$(cat "$settings")
	# `jq -e` は使わない。`null` や `false` は JSON として正しいのに -e が
	# 偽を返すので、「読めません」という誤った理由で異常終了することになる。
	printf '%s' "$current" | jq . >/dev/null 2>&1 ||
		die "$SETTINGS_REL が JSON として読めません。直してから、もう一度実行してください。"
else
	current="{}"
fi

# 構造の検査。ここを通さずに進むと、想定と違う型に当たった jq が生のエラーを出して
# 終了コード 5 で止まる。5 はこのスクリプトが定めていない値なので、呼んだ側は
# 引数の誤りなのか環境の不足なのかを区別できない。
#
# 確かめるのは ccnavi が触る場所だけ。プロジェクトが置いている他のキーの構造は問わない。
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
[ -z "$shape" ] || die "$SETTINGS_REL の構造を扱えません: ${shape}。直してから、もう一度実行してください。"

# 必ず書く env。既定値を持たない CCNAVI_BIN_PATH と、保護の設定項目。設定ファイルを
# 見るだけで、何を起動するかと、どこまで止まるかが分かるようにする。
#
# 戻す働きを持つ 2 つは CCNAVI_MODE に合わせる。設定が無ければ enable で動くので、
# 書かないまま dry-run で導入すると、判定では止めないのに、戻す働きだけが enable のまま
# 動く。導入先が様子を見ている間に、いきなり手元のファイルが戻ることになる。
# 導入直後は同じ強さで揃っているほうが、何が起きるかを見通せる。
#
# 代償として、書いた時点で enable は既定ではなくなる。dry-run で入れたまま
# 忘れると、この 2 つも dry-run のまま残る。--mode enable で実行し直すか、
# 設定ファイルの 3 行を書き換えるまで、保護は弱いまま。
#
# 2 値の切り替えの環境変数（CCNAVI_GUARD_TICKET_APPROVAL と CCNAVI_GUARD_UNWATCHED）は、モードに合わせず
# enable で書く。どちらも enable か disable しか取らず、dry-run と書くと ccnavi の --lint が
# 指摘する。承認の経路の切り替えの環境変数（CCNAVI_GUARD_TICKET_APPROVAL）は、通ればそれで済んでしまい、
# 済んだことは報告しても戻らない。そのため「止めずに報告する」段を持てない。確認できるユーザが
# いないモードの切り替えの環境変数（CCNAVI_GUARD_UNWATCHED）は、止めずに報告する段を CCNAVI_MODE=dry-run が
# 受け持つので、こちらには要らない。
#
# 値は --arg で 1 つずつ渡す。行にまとめてから分けると、値に混ざった改行がそのまま
# 行の区切りになる。すると、ここで拒んだはずの CCNAVI_MODE=disable を、別の値を経由して
# 書き込めてしまう。
#
# 置き場（記録・控え・提案・承認済みチケット・プロジェクト・ccnavi ディレクトリ）の env は
# 書かない。置き場は既定に固定で、env では動かないので、書いても読まれない（共通レイヤーの
# 3 本も同じ）。読まれない語を設定項目の一覧に混ぜると、そこを直せば置き場が
# 動くと読める。`--all` はその 4 つ（CCNAVI_STATE・CCNAVI_TICKETS_PROPOSAL・
# CCNAVI_TICKETS_APPROVED・CCNAVI_PROJECT_HOME）を足すためのものだったので、今は足すものが無い。
# 打ち慣れた手順が断られないように、オプションとしては受ける。
env_json=$(jq -n --arg mode "$mode" --arg bin "$BIN_PATH" --arg ticket_control "$ticket_control" '{
	CCNAVI_MODE: $mode,
	CCNAVI_BIN_PATH: $bin,
	CCNAVI_RESTORE_IF_DENY: $mode,
	CCNAVI_GUARD_CORE_FILES: $mode,
	CCNAVI_GUARD_TICKET_APPROVAL: "enable",
	CCNAVI_GUARD_UNWATCHED: "enable",
	CCNAVI_TICKET_CONTROL: $ticket_control
}')

# 廃止した置き場の env と、その既定（置き場は固定。設計 wip/design/i0064-fixed-places.md §1・§5）。
# 既に入っている settings.json の env にあれば外す。env は導入スクリプトが持つ欄なので、
# 読まれない語を残さない。外した値が既定と違っていたら、名前と値を 1 行ずつ出す。
# 既定と同じ値は黙って外す。導入は止めず、終了コードも変えない（--check では
# 「揃っていない」に数える。打ち直せば外れる）。
# CCNAVI_LOG の既定は改名後の logs/decisions.jsonl。以前の導入スクリプトが
# 書いた logs/log.jsonl は既定と違うものとして名指しする。記録の書き先が変わるので、黙らない。
PLACE_ENV_DEFAULTS='{
	"CCNAVI_PROJECTS": "projects",
	"CCNAVI_PROJECT_HOME": ".ccnavi",
	"CCNAVI_TICKETS_PROPOSAL": "wip/proposals",
	"CCNAVI_TICKETS_APPROVED": ".ccnavi/approved",
	"CCNAVI_LOG": "logs/decisions.jsonl",
	"CCNAVI_STATE": "logs/state"
}'
# 残っているもの全部（--check が並べる）と、そのうち既定と違うもの（書いたあとに名指しする）。
leftover_places=$(printf '%s' "$current" | jq -r --argjson places "$PLACE_ENV_DEFAULTS" '
	(.env // {}) as $cur | $places | keys_unsorted[] | . as $k | select($cur | has($k))
	| "\($k): \($cur[$k])"
')
removed_places=$(printf '%s' "$current" | jq -r --argjson places "$PLACE_ENV_DEFAULTS" '
	(.env // {}) as $cur | $places | to_entries[]
	| .key as $k | select(($cur | has($k)) and $cur[$k] != .value)
	| "\(.key) を .claude/settings.json から外しました（値: \($cur[.key])）。置き場は既定の \(.value) に固定されています。"
')

events_json=$(printf '%s\n' $EVENTS | jq -R -s 'split("\n") | map(select(length > 0))')

# 既定でない CCNAVI_BIN_PATH（ユーザが決めたパス）は書き換えず、そのパスを挙げた 1 行を出す。
# 導入は止めず、終了コードも変えない（--check では「揃っていない」に数える）。
current_bin=$(printf '%s' "$current" | jq -r '(.env // {}).CCNAVI_BIN_PATH // "" | if type == "string" then . else "" end')
bin_custom=""
if [ -n "$current_bin" ] && [ "$current_bin" != "$BIN_PATH" ]; then
	bin_custom="CCNAVI_BIN_PATH は既定と違う値（${current_bin}）です。書き換えていません。揃えるなら .claude/settings.json の値を ${BIN_PATH} に直してください。"
fi

# --force で置き換えてよいキー。ユーザがこの実行で指定した 2 つだけ。
forced=""
if [ "$force" = yes ]; then
	if [ "$mode_given" = yes ]; then
		forced="$forced CCNAVI_MODE"
	fi
	if [ "$ticket_control_given" = yes ]; then
		forced="$forced CCNAVI_TICKET_CONTROL"
	fi
fi
forced_json=$(printf '%s\n' $forced | jq -R -s 'split("\n") | map(select(length > 0))')

# 登録済みかどうかの見方。ccnavi の設定lint（lint_project.py の _registered）は、command に
# "ccnavi" が含まれるかどうかだけを見る。それだけだと、無関係な hook のパスに名前が
# 入っているプロジェクトでは、そのイベントが「登録済み」に見えたまま、いつまでも登録されない。
#
# そこで 3 通りに分ける。同じ表記で登録されている（exact）、ccnavi らしい別の表記で
# 登録されている（other）、登録が無い（none）。足すのは none だけ。other は足さずにユーザへ知らせる。
# 表記はプロジェクトごとに違うので、どちらが正しいかをここでは決められない。
# 知らせずに足すと判定が 2 回走り、知らせずに飛ばすとそのイベントは登録されないままになる。
# セッション開始時の取り込み（ccnavi-fetch.sh）は本体ではないので、other に数えない。数えると、
# 取り込みだけが登録された設定で、本体の SessionStart が「別の表記がある」と見なされて足されない。
# 取り込みのほうは、表記を問わず ccnavi-fetch を含む command があれば登録済みとする（fetching）。
REGISTERED='def commands($ev): [ (.hooks[$ev] // [])[] | (.hooks // [])[] | .command // "" ];
	def exact($ev; $cmd): [ commands($ev)[] | select(. == $cmd) ] | length > 0;
	def fetching($ev): [ commands($ev)[] | select(test("ccnavi-fetch")) ] | length > 0;
	def looks($ev): [ commands($ev)[] | ascii_downcase | select(test("ccnavi-fetch") | not)
		| select(test("(^|[^a-z0-9])ccnavi([^a-z0-9]|$)")) ] | length > 0;'

missing_env=$(printf '%s' "$current" | jq -r --argjson env "$env_json" '
	(.env // {}) as $cur | $env | keys_unsorted[] | select($cur[.] == null)
')
# 値が違う env を 2 つに分ける。指定されたものは置き換え、それ以外は残す。
# 残すほうも報告する。既に書かれている CCNAVI_MODE=disable のように、
# 揃っているように見えて保護が外れている状態は、ここにしか現れない。
replacing_env=$(printf '%s' "$current" | jq -r --argjson env "$env_json" --argjson forced "$forced_json" '
	(.env // {}) as $cur | $env | to_entries[]
	| select($cur[.key] != null and $cur[.key] != .value)
	| select(.key as $k | $forced | index($k) != null)
	| "\(.key): \($cur[.key]) -> \(.value)"
')
# CCNAVI_BIN_PATH は、既定でないパスのときに専用の 1 行で知らせる。ここに並べると、
# --force で置き換えられるかのような見出しの下に表示される。
bin_named=no
if [ -n "$bin_custom" ]; then
	bin_named=yes
fi
differing_env=$(printf '%s' "$current" | jq -r --argjson env "$env_json" --argjson forced "$forced_json" --arg bin_named "$bin_named" '
	(.env // {}) as $cur | $env | to_entries[]
	| select($cur[.key] != null and $cur[.key] != .value)
	| select(.key as $k | $forced | index($k) == null)
	| select(($bin_named == "yes" and .key == "CCNAVI_BIN_PATH") | not)
	| "\(.key): \($cur[.key])（このスクリプトが書くのは \(.value)）"
')
# 根を $root に取っておいてから回す。イベント名が `.` に入ったまま関数を呼ぶと、
# 関数の中の `.hooks` が文字列から値を引こうとする。
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
missing_fetch=""
if [ "$fetch" = yes ]; then
	missing_fetch=$(printf '%s' "$current" | jq -r "
		$REGISTERED
		if fetching(\"SessionStart\") then empty else \"SessionStart（.ccnavi/scripts/ccnavi-fetch.sh。セッション開始時の取り込み）\" end
	")
fi

# 配った実行ファイルを git に入れない。配布先は git で共有するのが普通なので、
# .gitignore に無いと、次のコミットで実行ファイルと _internal がまるごと履歴に
# 入る。入ってしまうと、消すには履歴を書き換えるしかない。
#
# 置き場全体（.ccnavi/ や .ccnavi/bin/）は無視しない。同じ .ccnavi/ の下に rules.yml が
# あるので、丸ごと無視すると、そのプロジェクトが何を止めるかまで git の管理から外れる。
#
# 足すパスは、配った組み立ての置き場（`.ccnavi/bin/<os>-<arch>/`）だけ。振り分けの sh は、
# 代わりに通る sh と同じく git で追跡する側に置く。無視すると、clone した先に sh が無く、
# hook が起動しない。置き場は配った機械のぶんだけ足す。別の機械で実行し直せば、その機械の
# ぶんが足される。
#
# 配った実行ファイルの `--docs` は、md のあるディレクトリごとに index.jsonl を書く。ただし書くのは
# git がそのファイルを無視しているときだけなので（README「ドキュメントの索引」）、無視する
# 行が無い配布先では索引が作られない。そこで、実行ファイルと同じ回に `**/index.jsonl` も足す。
# 見出しは実行ファイルの塊と分ける。同じ見出しの下に置くと、何のための行かが分からなくなる。
# `/` を含まない `index.jsonl` もどの階層にも当てはまるので、その行が既にあれば足さない。
# `index.jsonl` を否定する行（`!**/index.jsonl`、`!docs/index.jsonl` など）があれば、ユーザは
# 索引を追跡すると決めている。後ろに足すと git は後の行を優先するので、その否定を無効にして
# しまう。この場合は足さずに、その旨を伝える（「揃っていない」には数えない）。
IGNORE_HEADER="# ccnavi が配る実行ファイル（scripts/ccnavi-setup.sh）"
INDEX_IGNORE_HEADER="# ccnavi --docs が書く索引（scripts/ccnavi-setup.sh）"
INDEX_IGNORE_LINE="**/index.jsonl"
# ignore_todo は、表示と「揃っているか」の判定に使う全体。書き込むときは塊ごとに分けて持つ。
ignore_todo=""
ignore_bin_todo=""
ignore_index_todo=""
# ユーザが index.jsonl を否定している行（改行で終わる）。あれば索引の行は足さない。
ignore_index_negated=""
# .gitignore の行を、比べやすい形で出す。CRLF の `\r` と行末の空白を取り除く（git も行末の
# 空白は読まない）。ファイルが無ければ何も出さない。
gitignore_lines() {
	if [ -f "$root/.gitignore" ]; then
		sed 's/[[:space:]]*$//' "$root/.gitignore"
	fi
}
if [ -n "$deploy" ] && [ -e "$root/.git" ]; then
	ignored_already() {
		for spelling in "$@"; do
			if gitignore_lines | grep -qxF "$spelling"; then
				return 0
			fi
		done
		return 1
	}
	if ! ignored_already "/$BUILD_ROOT/$built/"; then
		ignore_bin_todo="/$BUILD_ROOT/$built/
"
	fi
	negated=$(gitignore_lines | grep -E '^!.*(index|\*)\.jsonl$' || true)
	if [ -n "$negated" ]; then
		ignore_index_negated="$negated
"
	elif ! ignored_already "$INDEX_IGNORE_LINE" "index.jsonl"; then
		ignore_index_todo="$INDEX_IGNORE_LINE
"
	fi
	ignore_todo="$ignore_bin_todo$ignore_index_todo"
fi

copy_tree() {
	# 中身を 1 つずつ配る。ディレクトリごと入れ替えないのは、配布先に既にあるフォルダでも、
	# 配布元にある名前のものにしか触れないようにするため。
	mkdir -p "$2"
	for entry in "$1"/*; do
		# 配布元が空だと glob が展開されずにそのまま残る。実在するものだけを配る。
		[ -e "$entry" ] || continue
		name=$(basename "$entry")
		# 古い組み立ての残りを持ち越さない。PyInstaller の同梱物は名前で探して
		# 読み込まれるので、前の版の .so が残っていると新しい実行ファイルがそれを読み込む。
		# 名前は配布元にある entry から取るので、空のパスを消すことはない。
		rm -rf "$2/$name"
		cp -R "$entry" "$2/$name"
	done
}

copy_file() {
	mkdir -p "$(dirname "$2")"
	cp "$1" "$2"
}

# .vscode/settings.json。ここは ccnavi の判定には関わらず、ユーザがエディタから
# worktree を見られるかどうかだけに関わる。
#
# 読めない内容（VS Code の設定ファイルにはコメントや末尾のカンマを書ける）でも
# die しない。die すると、ccnavi と関係のない書き方のせいで、肝心の .claude/settings.json まで
# 書けなくなる。このファイルには触らずにユーザへ任せ、残りの処理は進める。
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
	# 値が違うものは変えない。`false` と書いてある設定を true に戻すのは、このスクリプトの
	# 仕事ではない。そう書いたユーザの判断を消すことになる。
	differing_vscode=$(printf '%s' "$vscode_current" | jq -r --argjson want "$VSCODE_KEYS" '
		. as $cur | $want | to_entries[]
		| select($cur[.key] != null and $cur[.key] != .value)
		| "\(.key): \($cur[.key])（このスクリプトが書くのは \(.value)）"
	')
fi

# 書き込みの前と後で、同じ一覧を違う言葉で表示する。前は「足りない」、後は
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
	if [ -n "$missing_fetch" ]; then
		printf '%s hook（セッション開始時の取り込み。不要なら --no-fetch）:\n' "$4"
		printf '%s\n' "$missing_fetch" | sed 's/^/  /'
	fi
	if [ -n "$replacing_env" ]; then
		printf '%s env:\n' "$3"
		printf '%s\n' "$replacing_env" | sed 's/^/  /'
	fi
	if [ -n "$differing_env" ]; then
		printf '値が違う env（このスクリプトは変えません。変えるなら --mode / --ticket-control を指定して --force を付けてください）:\n'
		printf '%s\n' "$differing_env" | sed 's/^/  /'
	fi
	if [ -n "$other_hooks" ]; then
		printf 'ccnavi らしい別の表記で登録されている hook（二重に実行しないよう足していません。表記を確かめてください）:\n'
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
		printf '%s には触っていません: %s\n' "$VSCODE_REL" "$vscode_blocked"
		printf '  %s を自分で足すか、不要なら --no-vscode を付けてください。\n' "$VSCODE_KEYS"
	fi
}

report_missing() {
	report '足りない' 'ccnavi が登録されていない' '置き換える' '登録されていない'
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
	if [ -n "$ignore_index_negated" ]; then
		printf '.gitignore に index.jsonl を否定する行があり、ユーザが索引を追跡しているので %s は足していません:\n' "$INDEX_IGNORE_LINE"
		printf '%s' "$ignore_index_negated" | sed 's/^/  /'
	fi
	if [ -n "$launcher_mode_todo" ]; then
		printf '%s:\n' "$4"
		printf '%s' "$launcher_mode_todo" | sed 's/^/  /'
	fi
	if [ -n "$bin_custom" ]; then
		printf '%s\n' "$bin_custom"
	fi
	if [ -n "$deploy_skipped" ]; then
		printf '%s\n' "$deploy_skipped"
	fi
	if [ -n "$deploy_target_note" ]; then
		printf '%s\n' "$deploy_target_note"
	fi
}

launcher_mode_failed=""

report_deploy_plan() {
	report_deploy '配る' '入れ替える' '.gitignore に足す' '実行ビットを付ける'
}

# 揃っているかを判定する。値の違いと、別の表記での登録も「揃っていない」に数える。
# 不足の 2 つだけで決めると、CCNAVI_MODE=disable が書かれた設定に --check を
# 実行しても「揃っています」と表示することになる。
settled=yes
if [ -n "$missing_env" ] || [ -n "$missing_hooks" ] || [ -n "$missing_fetch" ] ||
	[ -n "$replacing_env" ] || [ -n "$differing_env" ] || [ -n "$other_hooks" ] ||
	[ -n "$missing_vscode" ] || [ -n "$differing_vscode" ] || [ -n "$vscode_blocked" ]; then
	settled=no
fi
# 配布物も「揃っていない」に数える。配布元に無いものも数える。実行ファイルが
# 欠けたまま「揃っています」と表示すると、--check を通過の条件にしている手順がそこを通してしまう。
# .gitignore の不足も数える。足りないまま通すと、次のコミットで実行ファイルが
# 履歴に入る。
if [ -n "$deploy_new" ] || [ -n "$deploy_replacing" ] || [ -n "$deploy_absent" ] ||
	[ -n "$ignore_todo" ] || [ -n "$launcher_mode_todo" ]; then
	settled=no
fi
# 既定でないパスも「揃っていない」に数える。
if [ -n "$bin_custom" ]; then
	settled=no
fi
# 廃止した置き場の env が残っていれば数える。既定と同じ値でも数える（読まれない語が残っている）。
if [ -n "$leftover_places" ]; then
	settled=no
fi

# 廃止した置き場の env のうち、残っているもの。--check が書かずに並べる。
report_places_plan() {
	if [ -n "$leftover_places" ]; then
		printf '外す env（置き場は既定に固定で、env では動かない。打ち直すと外します）:\n'
		printf '%s\n' "$leftover_places" | sed 's/^/  /'
	fi
}

# 外したもののうち、既定と違う値だったもの。既定と同じ値は黙って外す。
report_places_done() {
	if [ -n "$removed_places" ]; then
		printf '%s\n' "$removed_places"
	fi
}

# ワークスペースの git の索引に projects/ の下が載っているときの知らせ（設計 §4.1・§4.4）。
#
# 条件と文面は `ccnavi --lint` の `(projects)` の warn と同じ（src/ccnavi/entry/lint.py の _in_index）。
# 変えるなら 2 か所を直す。tests/sh/test_setup.py が 1 文目の一致を見る。
#
# - 通常のファイルが 1 本以上: ぶつかり。ワークスペースの projects/ を改名する案内
#   （gitlink もあれば 1 文足して名指しする）
# - gitlink（mode 160000）だけ: 載せ忘れ。索引から外して無視に入れる案内
#
# 知らせるだけで、止めず、終了コードも変えない。導入の不足ではないので「揃っていない」にも
# 数えない。索引も .gitignore も変えない（git rm --cached を打つのも /projects/ を足すのも人）。
#
# 案内のコマンドに載せるパスを、sh で 1 語として読める綴りにして標準出力に出す（改行は付けない）。
# sh が割る・展開する文字を含まなければそのまま、含めば '…' で囲み、中の ' は '\'' に置く。
# 文字の集合と綴りは src/ccnavi/entry/lint.py の _SH_SPECIAL・_sh_word と同じ。変えるなら 2 か所を直す。
sh_word() {
	w=$1
	q="'"
	case "$w" in
	*' '* | *"$tab"* | *"$q"* | *'"'* | *\\* | *'$'* | *'`'* | *'!'* | *'*'* | *'?'* | \
		*'['* | *']'* | *'('* | *')'* | *'{'* | *'}'* | *'<'* | *'>'* | *'|'* | *'&'* | \
		*';'* | *'#'* | *'~'*) ;;
	*)
		printf '%s' "$w"
		return 0
		;;
	esac
	out=""
	while :; do
		case "$w" in
		*"$q"*)
			head=${w%%"$q"*}
			out=$out$head$q\\$q$q
			w=${w#*"$q"}
			;;
		*)
			out=$out$w
			break
			;;
		esac
	done
	printf "'%s'" "$out"
}

# 標準入力に `git ls-files -s` の行（`<mode> <oid> <stage><タブ><path>`）を受ける。
# 表示に使うだけなので -z は使わない（bash 3.2・BSD の道具で読める形）。
projects_notice_of() {
	tab=$(printf '\t')
	first_file=""
	links=""
	links_code=""
	links_args=""
	first_link=""
	link_count=0
	last_link=""
	while IFS= read -r line; do
		[ -n "$line" ] || continue
		path=${line#*"$tab"}
		case "$line" in
		160000\ *)
			# 衝突中の段で同じパスが並ぶ（並びは綴り順なので隣り合う）。1 本に畳む。
			[ "$path" = "$last_link" ] && continue
			last_link=$path
			link_count=$((link_count + 1))
			if [ -z "$first_link" ]; then
				first_link=$path
				links_code="\`$path\`"
				links_args=$(sh_word "$path")
			else
				links_code="${links_code}、\`${path}\`"
				links_args="$links_args $(sh_word "$path")"
			fi
			;;
		*)
			if [ -z "$first_file" ]; then
				first_file=$path
			fi
			;;
		esac
	done
	lead='`projects/` はワークスペースの git が追跡している'
	if [ -n "$first_file" ]; then
		text="${lead}（例: \`${first_file}\`）。"
		text="${text}ccnavi はワークスペース直下の \`projects/\` をプロジェクトの置き場として使い、名前は変えられない。"
		text="${text}このままだと \`projects/\` の下で \`.git\` を持つディレクトリ（サブモジュールを含む）がプロジェクトとして数えられ、その中の設定が判定に使われる。"
		text="${text}直すには、ワークスペースの \`projects/\` を別の名前に移す（例: \`git mv projects apps\`）。"
		text="${text}ccnavi でプロジェクトを置かないなら、このままでも動く。そのときは \`projects/\` の下に\`.git\` を持つものを置かない"
		if [ "$link_count" -gt 0 ]; then
			text="${text}。索引には入れ子のリポジトリ（${links_code}）も載っている。"
			text="${text}ccnavi のプロジェクトとして使うなら、改名の前に \`git rm --cached ${links_args}\` で索引から外し、改名のあとで \`projects/\` の下へ戻す"
		fi
		printf '%s\n' "$text"
		return 0
	fi
	[ "$link_count" -gt 0 ] || return 0
	more=""
	if [ "$link_count" -gt 1 ]; then
		more=" ほか $((link_count - 1)) 件"
	fi
	text="${lead}（入れ子のリポジトリとして: \`${first_link}\`${more}）。"
	text="${text}\`.gitignore\` に入れる前に \`git add\` したものとみられる。"
	text="${text}プロジェクトは自分の git を持つので、ワークスペースの git には載せない。"
	text="${text}載せたままだと、ワークスペースのコミットがプロジェクトの版を記録し続け、\`.gitignore\` に \`/projects/\` を足しても追跡は外れない。"
	text="${text}直すには、ワークスペースで \`git rm -r --cached projects\` を打ち（ファイルは消えない。まだコミットしていなければ \`git reset -- projects\`）、"
	text="${text}\`.gitignore\` に \`/projects/\` を足して（既にあればそのまま）、コミットする"
	printf '%s\n' "$text"
}

# 索引を読めない（git が無い、git のリポジトリではない）ときは何も言わない。`--lint` と同じ。
projects_notice=""
if command -v git >/dev/null 2>&1; then
	projects_notice=$(git -c core.quotepath=false -C "$root" ls-files -s -- projects/ 2>/dev/null | projects_notice_of) || projects_notice=""
fi

report_projects() {
	if [ -n "$projects_notice" ]; then
		printf '%s\n' "$projects_notice"
	fi
}

if [ "$check" = yes ]; then
	printf '%s\n' "$settings"
	report_missing
	report_places_plan
	report_deploy_plan
	report_projects
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

# 書き込む作業があるか。値が違うだけで指定されていないものと、別の表記での
# 登録は、このスクリプトが触らないので数えない。ただし、何も言わずには終わらせない。
settings_work=no
if [ -n "$missing_env" ] || [ -n "$missing_hooks" ] || [ -n "$missing_fetch" ] ||
	[ -n "$replacing_env" ] || [ -n "$leftover_places" ]; then
	settings_work=yes
fi
vscode_work=no
if [ -n "$missing_vscode" ]; then
	vscode_work=yes
fi
deploy_work=no
if [ -n "$deploy_new" ] || [ -n "$deploy_replacing" ] || [ -n "$ignore_todo" ] ||
	[ -n "$launcher_mode_todo" ]; then
	deploy_work=yes
fi

printf '%s\n' "$settings"

if [ "$settings_work" = no ] && [ "$vscode_work" = no ] && [ "$deploy_work" = no ]; then
	printf '書き足すものはありません。\n'
	report_missing
	report_deploy_plan
	report_projects
	exit 0
fi

# 設定ファイルへの書き込みを 1 か所にまとめる。呼び出し元は .claude/settings.json と
# .vscode/settings.json の 2 か所。「バックアップは最初の 1 回だけ取る」「リンクは切らない」という
# 決まりを 2 か所に書き写すと、片方のコピーだけを直したときに、どちらの設定ファイルで正しく
# 動かなくなるかが、どちらのコピーを直したかで変わる。
#
# 結果は backed_up と linked に入れる。呼び出し元はそれを settings_* と vscode_* に移し、
# ファイルごとに報告する。
write_json() {
	# 導入前の内容を 1 つだけ残し、2 回目以降は上書きしない。毎回取り直すと、実行し直す
	# たびにバックアップが新しくなり、戻れるのは 1 回前までになる。その時点では既に ccnavi が
	# 入っているので、入れる前の設定へ戻す手段が無くなる。
	mkdir -p "$(dirname "$1")"
	backed_up=no
	if [ -f "$1" ] && [ ! -e "$1.bak" ]; then
		cp "$1" "$1.bak"
		backed_up=yes
	fi

	# リンクを切らずに書く。`mv` はディレクトリエントリを差し替えるので、設定を
	# 1 か所に置いて各プロジェクトからリンクを張っている構成だと、リンクが普通のファイルに
	# 置き換わり、実体には何も書き込まれない。リンクのときだけ中身を直接書き、それ以外は同じ
	# ディレクトリに書いてから移す（途中で切れた設定ファイルを残さないため）。
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
		# 途中で止まったときに書きかけを残さない。設定ファイルの隣に見慣れない
		# ファイルがあると、それが設定なのか残骸なのかをユーザが判断できない。
		trap 'rm -f "$tmp"' EXIT INT TERM
		printf '%s\n' "$2" >"$tmp"
		mv "$tmp" "$1"
		trap - EXIT INT TERM
	fi
}

settings_backed_up=no
settings_linked=no
if [ "$settings_work" = yes ]; then
	# 既存の内容を残す方向でマージする。env は既にある値を優先し、指定されたキーだけを
	# 後から上書きする。hook は登録が無いイベントにだけ足す。ccnavi と関係のない
	# hook の隣に並ぶので、他のツールの設定を消さない。
	updated=$(printf '%s' "$current" | jq --argjson env "$env_json" \
		--argjson events "$events_json" \
		--argjson forced "$forced_json" \
		--arg cmd "$HOOK_COMMAND" \
		--argjson timeout "$HOOK_TIMEOUT" \
		--arg fetch "$fetch" \
		--arg fetch_cmd "$FETCH_COMMAND" \
		--argjson fetch_timeout "$FETCH_TIMEOUT" \
		--argjson places "$PLACE_ENV_DEFAULTS" "
		$REGISTERED
		(\$env | with_entries(select(.key as \$k | \$forced | index(\$k) != null))) as \$overrides
		| .env = (\$env + (.env // {}) + \$overrides)
		| .env |= with_entries(select(.key as \$k | \$places | has(\$k) | not))
		| reduce \$events[] as \$ev (
			.;
			if (exact(\$ev; \$cmd)) or (looks(\$ev)) then .
			else .hooks[\$ev] = ((.hooks[\$ev] // []) + [{
				matcher: \"\",
				hooks: [{type: \"command\", command: \$cmd, timeout: \$timeout}]
			}])
			end
		)
		| if \$fetch == \"yes\" and (fetching(\"SessionStart\") | not) then
			.hooks.SessionStart = ((.hooks.SessionStart // []) + [{
				matcher: \"\",
				hooks: [{type: \"command\", command: \$fetch_cmd, timeout: \$fetch_timeout}]
			}])
		else . end
	")
	write_json "$settings" "$updated"
	settings_backed_up=$backed_up
	settings_linked=$linked
fi

# .vscode も同じ方針でマージする。既にあるキーを優先し、足りないものだけを足す。
# ccnavi と関係のない VS Code の設定はそのまま残る。
vscode_backed_up=no
vscode_linked=no
if [ "$vscode_work" = yes ]; then
	vscode_updated=$(printf '%s' "$vscode_current" | jq --argjson want "$VSCODE_KEYS" '$want + .')
	write_json "$vscode_settings" "$vscode_updated"
	vscode_backed_up=$backed_up
	vscode_linked=$linked
fi

report '足した' 'ccnavi を登録した' '置き換えた' '登録した'
report_places_done
if [ "$settings_backed_up" = yes ]; then
	printf '書き換える前の内容は %s.bak にあります（バックアップは最初の 1 回だけ取ります）。\n' "$SETTINGS_REL"
fi
if [ "$settings_linked" = yes ]; then
	printf '%s はリンクだったので、リンクを保ったまま中身を書きました。\n' "$SETTINGS_REL"
fi
if [ "$vscode_backed_up" = yes ]; then
	printf '書き換える前の内容は %s.bak にあります（バックアップは最初の 1 回だけ取ります）。\n' "$VSCODE_REL"
fi
if [ "$vscode_linked" = yes ]; then
	printf '%s はリンクだったので、リンクを保ったまま中身を書きました。\n' "$VSCODE_REL"
fi

# 配る。順は実行ファイル → 設定 3 本（ルール・リスクの配点・フェーズの種類）→
# 代わりに通る sh → 振り分けの sh。途中で失敗したときに、判定するものだけがあって
# 何を止めるかが無い、という状態にしないため。
if [ "$deploy_work" = yes ]; then
	case "$bin_verdict" in
	copy | replace)
		copy_tree "$source_root/$DEPLOY_BIN_DIR" "$root/$build_dir_rel"
		# 実行ビットを付け直す。cp は元のモードを umask で削ってコピーするので、
		# 配布元の置き方によっては実行ビットが外れる。外れていると、hook は
		# 「実行ファイルが無い」ではなく「起動できない」という理由で、気づかないうちに失敗する。
		for spelling in "$root/$build_dir_rel/ccnavi" "$root/$build_dir_rel/ccnavi.exe"; do
			if [ -f "$spelling" ]; then
				chmod +x "$spelling" 2>/dev/null || true
			fi
		done
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
		# hook は振り分けの sh を直接起動する。cp は元のモードをそのままコピーするとは限らないので付け直す。
		if [ "$name" = "$LAUNCHER_NAME" ]; then
			chmod +x "$root/$BIN_PATH" 2>/dev/null || true
		fi
	done
	# 配らなかった回でも、実行ビットが外れた sh には付け直す。付けられなかったときは報告する。
	if [ -n "$launcher_mode_todo" ]; then
		chmod +x "$root/$BIN_PATH" 2>/dev/null || true
		if [ ! -x "$root/$BIN_PATH" ]; then
			launcher_mode_todo=""
			launcher_mode_failed="$BIN_PATH
"
		fi
	fi
	# .gitignore には足すだけ。既にある行は書かず、ccnavi と関係のない行にも
	# 触らない。見出しは、その塊が何なのかを、あとで開いたユーザに伝えるためだけの
	# もの。既に同じ見出しがあれば重ねない。
	append_ignore() {
		# $1 見出し、$2 足す行（改行で終わる）。空なら何もしない。
		[ -n "$2" ] || return 0
		{
			if [ -s "$root/.gitignore" ]; then
				# 末尾に改行が無いファイルへ足すと、最後の行とつながって別の
				# 文字列になる。無視させるつもりの行が、誰も意図しない 1 行になってしまう。
				if [ -n "$(tail -c 1 "$root/.gitignore")" ]; then
					printf '\n'
				fi
				# もとからある行と、ここで足す塊を、空行 1 つで分ける。
				printf '\n'
			fi
			if ! gitignore_lines | grep -qxF "$1"; then
				printf '%s\n' "$1"
			fi
			printf '%s' "$2"
		} >>"$root/.gitignore"
	}
	append_ignore "$IGNORE_HEADER" "$ignore_bin_todo"
	append_ignore "$INDEX_IGNORE_HEADER" "$ignore_index_todo"
fi

report_deploy '配った' '入れ替えた' '.gitignore に足した' '実行ビットを付けた'
if [ -n "$launcher_mode_failed" ]; then
	printf '実行ビットを付けられなかった振り分けの sh（hook が起動しません。chmod +x で付けてください）:\n'
	printf '%s' "$launcher_mode_failed" | sed 's/^/  /'
fi

# 登録しただけでは動かない。配り終えたあとの状態をそのまま確かめて、まだ無いものを
# 挙げる。配布がうまくいっていれば、普通はここで何も出ない。出たときは、配布を切ったか、
# 配布元に無かったか、配布先に別のものが既にあって配っていないか、のどれか。
#
# ここでは取得も生成もしない。実行ファイルは、振り分けの sh と同じ順で、
# この機械で動くものの置き場を探す。
missing_parts=""
note_missing() {
	missing_parts="$missing_parts  $1
"
}
case "$host" in
unknown-* | *-unknown) ;;
*)
	if ! runnable_there; then
		note_missing "${BUILD_ROOT}/${host}/ccnavi（この機械で動く実行ファイル。この機械で build.py を実行して組み立ててから配る）"
	fi
	;;
esac
if [ ! -f "$root/$DEPLOY_RULES" ]; then
	note_missing "${DEPLOY_RULES}（何を止めるかのルール。無いと組み込みの既定だけで判定する）"
fi
if [ ! -f "$root/$DEPLOY_RISK" ]; then
	note_missing "${DEPLOY_RISK}（リスクの配点。無いと組み込みの配点で測る）"
fi
if [ ! -f "$root/$DEPLOY_PHASES" ]; then
	note_missing "${DEPLOY_PHASES}（フェーズの種類。無いと番号だけの挙動になる）"
fi
# 配るものの一覧（DEPLOY_SCRIPTS）を使って確かめる。ここで名前を決め打ちすると、配る sh を
# 足したときに、その sh が無いことをこの一覧だけが報告しなくなる。
for name in $DEPLOY_SCRIPTS; do
	if [ ! -f "$root/$DEPLOY_SCRIPT_DIR/$name" ]; then
		case "$name" in
		"$LAUNCHER_NAME")
			why="hook が起動する振り分けの sh"
			;;
		ccnavi-push-approved.sh)
			why="承認のあとにボードが端末で実行する、承認済みチケットのコミットと push"
			;;
		ccnavi-agree.sh)
			why="端末から承認するための sh"
			;;
		ccnavi-fetch.sh)
			why="セッション開始時の取り込み"
			;;
		ccnavi-sync.sh)
			why="ユーザが実行する取り込みと、親のブランチが消えたかの確認"
			;;
		ccnavi-clean.sh | ccnavi-clean.js)
			why="ワークツリーを片付ける前に生成物を消す"
			;;
		ccnavi-common-*.sh)
			why="代わりに通る sh が起動して最初に読む共通部の部品"
			;;
		*)
			why="止めている間に代わりに通る sh"
			;;
		esac
		note_missing "${DEPLOY_SCRIPT_DIR}/${name}（${why}）"
	fi
done
if [ -n "$missing_parts" ]; then
	printf 'まだ無いもの:\n%s' "$missing_parts"
	# 配布をやめた理由は report_deploy が既に出している。ここで足すのは、ユーザが自分で
	# 配布を切ったときだけ。理由を二重に出すと、どちらが今回の話か分からなくなる。
	if [ "$deploy_off" = yes ]; then
		# 書式の引数には置かない。`--` で始まる文字列は、printf がオプションとして
		# 読んでエラーになる。
		printf '%s\n' "--no-deploy を外すと、ccnavi の根から配ります。"
	fi
fi

# 入れ終わったところで、projects/ が索引に載っていないかを知らせる。入れる瞬間が
# いちばん気付きやすい。止めない。
report_projects

printf 'env の値は、セッションを開き直すまで反映されません。\n'
