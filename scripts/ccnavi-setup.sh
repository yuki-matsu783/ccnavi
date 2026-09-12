#!/bin/sh
# ccnavi-setup — 対象プロジェクトの .claude/settings.json に、ccnavi が想定する
# env と hook を登録する。ccnavi を新しいプロジェクトへ入れるときに人が 1 回打つ。
#
#   sh scripts/ccnavi-setup.sh [<ワークスペースルート>] [オプション]
#
#   --mode <enable|dry-run>  CCNAVI_MODE。既定は dry-run
#   --bin <相対パス>         CCNAVI_BIN_PATH。既定は .claude/ccnavi/ccnavi
#   --deploy <ccnavi の根>   配り元。既定はこのスクリプトが入っている ccnavi の根
#   --no-deploy              写しを取らず、settings.json だけを書く
#   --all                    既定値を持つ env も明示して書く
#   --force                  明示した --mode / --bin で、既にある値を置き換える。
#                            写しでは、配り先に既にあるものも入れ替える
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
# 写しは、ccnavi を組み立てたところ（ccnavi のリポジトリ）から対象プロジェクトへ
# 取る。設定を書くだけでは動かないのに、実行ファイルを置く手立てがどこにも
# 無かった。`dist/` は .gitignore に入っているので git では渡らず、CCNAVI_BIN_PATH は
# 相対でしか書けないので、よそで組んだ実行ファイルを指すこともできない。対象
# プロジェクトの中に実体を置く経路がここに要る。
#
# 配り元は既定でこのスクリプト自身の置き場から取る。設定だけ書かれて実行ファイルが
# 無い形は、hook が 7 つ登録されているのに何も起動しない、という一番分かりにくい
# 壊れ方になる。既定で写しまで取れば、打った人が `--deploy` を知っているかどうかで
# そこが分かれない。よそから配りたいときだけ `--deploy` で配り元を名指しする。
#
# 既定の配り元が使えないとき（組み立てていない、配り先が ccnavi 自身）は、写しを
# 諦めて理由を 1 行出し、settings.json は書く。名指しされた `--deploy` が使えない
# ときだけ 2 で断る。人が名指ししたものが無いのは、環境の誤りとして扱う。
#
# 配り先に既にあるものは触らない。入れ替えるのは `--force` を付けたときだけ。
# ルールファイルもゲートの sh も、入れた先で直されている前提のもの。黙って上書き
# すると、そのプロジェクトが何を止めるかを、打ち直し 1 回で配り元の形へ戻す。
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
# 配り先での既定の置き場。配り元の dist/ は組み立ての出力で、.gitignore に
# 入っている場所。配られた側にとっては、そこは「自分が組み立てた物の置き場」
# ではなく、ccnavi が入っている場所。.claude/ の下に置けば、rules.yml や
# ゲートの sh と同じ並びに収まる。
DEFAULT_BIN=".claude/ccnavi/ccnavi"

# 写すもの。配り元での置き場は build.py の出力（dist/ccnavi）と、ccnavi の
# リポジトリの .claude/ の形に決め打ちで対応する。配り先の綴りは --bin に
# 従うので、実行ファイルだけは行き先が動く。
DEPLOY_BIN_DIR="dist/ccnavi"
DEPLOY_RULES=".claude/ccnavi/rules.yml"
DEPLOY_SCRIPT_DIR=".claude/scripts"
DEPLOY_SCRIPTS="ccnavi-ticket.sh ccnavi-review.sh ccnavi-git.sh"

mode="$DEFAULT_MODE"
bin="$DEFAULT_BIN"
# 明示されたかどうかを分けて持つ。--force が置き換えてよいのは、人がこの実行で
# 名指しした値だけ。既定で埋めただけの値まで置き換えると、`--all` を足しに来た
# 打ち直しが、その場で指定していない CCNAVI_MODE を既定の dry-run へ落とす。
mode_given=no
bin_given=no
all=no
force=no
check=no
vscode=yes
target=""
# 配り元。名指しされたかどうかを分けて持つ。既定で埋めただけの配り元が使えない
# のは「組み立てていない」で済むが、人が名指ししたものが使えないのは誤り。
# 同じ変数で持つと、その 2 つを最後まで区別できない。
deploy=""
deploy_given=no
deploy_off=no
# 既定の配り元が使えなかった理由。空でなければ 1 行出す。
deploy_skipped=""

usage() {
	cat <<'USAGE'
sh scripts/ccnavi-setup.sh [<ワークスペースルート>] [オプション]

  <ワークスペースルート>      既定は現在の作業ディレクトリ
  --mode <enable|dry-run>   CCNAVI_MODE。既定は dry-run
  --bin <相対パス>          CCNAVI_BIN_PATH。既定は .claude/ccnavi/ccnavi
  --deploy <ccnavi の根>    配り元。既定はこのスクリプトが入っている ccnavi の根
  --no-deploy               写しを取らず、settings.json だけを書く
  --all                     既定値を持つ env も明示して書く
  --force                   明示した --mode / --bin で、既にある値を置き換える。
                            写しでは、配り先に既にあるものも入れ替える
  --check                   書かずに、揃っていないところだけを並べる
  --no-vscode               .vscode/settings.json には触らない

実行ファイル・ルール・ゲートの sh は、既定で ccnavi の根から写す。写した
実行ファイルと _internal は、配り先の .gitignore に足す。
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

command -v jq >/dev/null 2>&1 || die "jq が要ります。"

[ -n "$target" ] || target="."
[ -d "$target" ] || die "$target というディレクトリがありません。"
# 表示のために絶対化する。Git Bash では pwd -W が Windows 形式を返すので、
# 人が設定ファイルを開くときにそのまま使える綴りになる。
root=$(cd "$target" && { pwd -W 2>/dev/null || pwd; })
settings="$root/$SETTINGS_REL"
claude_dir=$(dirname "$settings")

# 配り元。対象の根を決めたあとで見る。同じ綴りの取り方で絶対化してから
# 突き合わせないと、配り元と配り先が同じかどうかを判定できない。
#
# 名指しが無ければ、このスクリプトの置き場の 1 つ上を配り元にする。scripts/ の
# 下に居るという 1 点だけに寄りかかる。作業ツリーから打てばその作業ツリーの
# dist/ が配り元になり、写しと、そこに居る自分の変更が食い違わない。
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
		deploy_skipped="配り元が見つからないので写していません（--deploy <ccnavi の根> で名指しできます）。"
		deploy=""
	fi
fi
if [ -n "$deploy" ]; then
	source_root=$(cd "$deploy" && { pwd -W 2>/dev/null || pwd; })
	if [ "$source_root" = "$root" ]; then
		# 既定の配り元では普通に起きる。ccnavi のリポジトリ自身に打つと、
		# 配り元と配り先が同じ場所になる。そこは写す先ではないので、
		# 設定だけ書いて写しは諦める。名指しなら、打った人の思い違い。
		[ "$deploy_given" = no ] ||
			die "--deploy の配り元と配り先が同じです。自分自身へは配れません。"
		deploy_skipped="配り元と配り先が同じなので写していません。"
		deploy=""
		source_root=""
	fi
fi
# 組み立てていない配り元で黙って進まない。ここを報告だけにすると、
# 「配ったはずなのに実行ファイルが無い」が最後の一覧にしか現れず、
# 打った人は配れたものとして先へ進む。--deploy を名指しした以上、実行ファイルが
# 無いことは環境の誤りとして 2 で断る。既定の配り元なら、組み立てていないだけ
# なので、諦めた理由を出して settings.json は書く。
if [ -n "$deploy" ] && [ ! -d "$source_root/$DEPLOY_BIN_DIR" ]; then
	[ "$deploy_given" = no ] ||
		die "$source_root/$DEPLOY_BIN_DIR がありません。配り元で 'uv run --with pyinstaller python build.py' を回してから打ち直してください。"
	deploy_skipped="$source_root/$DEPLOY_BIN_DIR が無いので写していません（配り元で 'uv run --with pyinstaller python build.py' を回すと作られます）。"
	deploy=""
	source_root=""
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
[ -z "$shape" ] || die "$SETTINGS_REL の形を扱えません: $shape。直してから打ち直してください。"

# 必ず書く env。既定を持たない CCNAVI_BIN_PATH、既定と同じでも書いておきたい
# 2 つのパス、そして 3 つの守りのつまみ。設定ファイルだけを見て、どこを読み書き
# するかと、どこまで止まるかが分かる形にする。
#
# つまみの値は CCNAVI_MODE に合わせる。3 つは設定が無ければ enable で動くので、
# 書かないまま dry-run で導入すると、判定は止めないのに戻す働きだけが本気で
# 動く。入れた先が様子を見ている間に、いきなり手元のファイルが戻ることになる。
# 導入直後は全部が同じ強さで並ぶほうが、何が起きるかを読める。
#
# 代償は、書いた瞬間から enable が既定ではなくなること。dry-run で入れたまま
# 忘れると、この 3 つも dry-run のまま残る。--mode enable で打ち直すか、
# 設定ファイルの 4 行を書き換えるまで、守りは弱いまま。
#
# 値は --arg で 1 つずつ渡す。行に組んでから割ると、値に混ざった改行がそのまま
# 行の区切りになり、ここで拒んだはずの CCNAVI_MODE=disable を --bin 経由で
# 書き込めてしまう。
env_json=$(jq -n --arg mode "$mode" --arg bin "$bin" '{
	CCNAVI_MODE: $mode,
	CCNAVI_RULES: ".claude/ccnavi/rules.yml",
	CCNAVI_LOG: ".claude/ccnavi/log.jsonl",
	CCNAVI_BIN_PATH: $bin,
	CCNAVI_RESTORE_IF_DENY: $mode,
	CCNAVI_GUARD_CORE_FILES: $mode,
	CCNAVI_GUARD_CLI: $mode
}')
# --all のときだけ足す、既定と同じ値の env。書かなくても同じように動く。
# 書く利点は、あとで値を変えたくなった人が、つまみの一覧を README ではなく
# 設定ファイルの中で見つけられること。
if [ "$all" = yes ]; then
	env_json=$(printf '%s' "$env_json" | jq '. + {
		CCNAVI_STATE: ".claude/ccnavi/state",
		CCNAVI_TICKETS: "wip/tickets",
		CCNAVI_APPROVED: ".claude/ccnavi/tickets",
		CCNAVI_PHASES: ".claude/ccnavi/phases.yml",
		CCNAVI_RISK: ".claude/ccnavi/risk.yml"
	}')
fi

events_json=$(printf '%s\n' $EVENTS | jq -R -s 'split("\n") | map(select(length > 0))')

# --force が置き換えてよいキー。人がこの実行で名指しした 2 つだけ。
forced=""
if [ "$force" = yes ]; then
	if [ "$mode_given" = yes ]; then
		forced="$forced CCNAVI_MODE"
	fi
	if [ "$bin_given" = yes ]; then
		forced="$forced CCNAVI_BIN_PATH"
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

# 写すものを決める。配り先に既にあるものは触らない。入れ替えるのは --force の
# ときだけで、そのときも配り元に在るものだけを動かす。
#
# 「配り元に無い」を黙って飛ばさない。ルールファイルやゲートの sh が欠けた
# 配り元から配ると、判定するものだけが入って何を止めるかが入らない。その形は
# 最後の「まだ無いもの」にしか出ず、配った側の落ち度に見えない。
deploy_new=""
deploy_replacing=""
deploy_kept=""
deploy_absent=""
bin_dir_rel=""
bin_verdict=""
rules_verdict=""
scripts_todo=""

# 配り元に在るか、配り先に在るか、--force か。この 3 つだけで決まる。
verdict() {
	# $1 配り元の絶対パス / $2 配り先に既にあるか（yes/no）
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
	# $1 verdict / $2 配り先の綴り / $3 配り元の綴り
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
		deploy_absent="$deploy_absent$2（配り元の $3 にありません）
"
		;;
	esac
}

if [ -n "$deploy" ]; then
	# 実行ファイルの行き先は --bin が決める。CCNAVI_BIN_PATH に書く綴りと、
	# 実体を置く場所を 1 つの値から出す。ここが割れると、設定は書けているのに
	# hook がどこにも無いものを起動する形になる。
	bin_dir_rel=$(dirname "$bin")
	# 在るかどうかは 2 つの綴りで見る。PyInstaller が Windows でだけ .exe を
	# 付けるので、同じ設定が環境によって違うファイル名に当たる。
	if [ -f "$root/$bin" ] || [ -f "$root/$bin.exe" ]; then
		bin_there=yes
	else
		bin_there=no
	fi
	bin_verdict=$(verdict "$source_root/$DEPLOY_BIN_DIR" "$bin_there")
	note_deploy "$bin_verdict" "$bin_dir_rel" "$DEPLOY_BIN_DIR"

	if [ -e "$root/$DEPLOY_RULES" ]; then
		rules_there=yes
	else
		rules_there=no
	fi
	rules_verdict=$(verdict "$source_root/$DEPLOY_RULES" "$rules_there")
	note_deploy "$rules_verdict" "$DEPLOY_RULES" "$DEPLOY_RULES"

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

# 写した実行ファイルを git に入れない。配り先は git で持ち回るのが普通なので、
# .gitignore に無いと、次のコミットで実行ファイルと _internal がまるごと履歴に
# 入る。入ってしまうと、消すには履歴を書き換えるしかない。
#
# 置き場ごと無視はしない。--bin の綴りによっては、実行ファイルの置き場が
# rules.yml と同じディレクトリ（.claude/ccnavi）になる。そこを丸ごと無視すると、
# そのプロジェクトが何を止めるかまで git から消える。
#
# 綴りは 3 つ。実行ファイルの 2 つの名前（PyInstaller が Windows でだけ .exe を
# 付ける）と、同梱物の _internal。同じリポジトリを 3 つの環境で開くので、
# 打った機械に在るほうだけでなく両方を書く。
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
	ignore_name=$(basename "$bin")
	# `--bin` に .exe まで書かれていたら、足すのはその 1 つだけ。
	case "$ignore_name" in
	*.exe) ignore_names="$ignore_name" ;;
	*) ignore_names="$ignore_name $ignore_name.exe" ;;
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
	for ignore_name in $ignore_names; do
		note_ignore "$ignore_prefix$ignore_name"
	done
	note_ignore "${ignore_prefix}_internal/"
fi

copy_tree() {
	# 中身を 1 つずつ写す。ディレクトリごと入れ替えないのは、配り先が既にある
	# 別のフォルダ（--bin の綴りによってはワークスペースルートそのもの）でも、
	# 配り元が持つ名前のものにしか手が届かないようにするため。
	mkdir -p "$2"
	for entry in "$1"/*; do
		# 配り元が空なら glob がそのまま残る。在るものだけを写す。
		[ -e "$entry" ] || continue
		name=$(basename "$entry")
		# 古い組み立ての残りを持ち越さない。PyInstaller の同梱物は名前で
		# 引かれるので、前の版の .so が残ると新しい実行ファイルがそれを掴む。
		# 名前は配り元に在る entry から取るので、空のパスを消すことはない。
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
		printf '値が違う env（このスクリプトは変えません。変えるなら --mode / --bin を名指しして --force）:\n'
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

# 写しの報告。env と同じく、写す前と後で言葉を変える。
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
		printf '配り先に既にあるので写していないもの（入れ替えるなら --force）:\n'
		printf '%s' "$deploy_kept" | sed 's/^/  /'
	fi
	if [ -n "$deploy_absent" ]; then
		printf '配り元に無くて写せないもの:\n'
		printf '%s' "$deploy_absent" | sed 's/^/  /'
	fi
	if [ -n "$ignore_todo" ]; then
		printf '%s:\n' "$3"
		printf '%s' "$ignore_todo" | sed 's/^/  /'
	fi
	if [ -n "$deploy_skipped" ]; then
		printf '%s\n' "$deploy_skipped"
	fi
}

report_deploy_plan() {
	report_deploy '写す' '入れ替える' '.gitignore に足す'
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
# 写しも「揃っていない」に数える。配り元に無いものも数える。実行ファイルが
# 欠けたまま「揃っています」と言うと、--check を門にしている手順がそこを通す。
# .gitignore の不足も数える。足りないまま通すと、次のコミットで実行ファイルが
# 履歴に入る。
if [ -n "$deploy_new" ] || [ -n "$deploy_replacing" ] || [ -n "$deploy_absent" ] ||
	[ -n "$ignore_todo" ]; then
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

if [ "$settings_work" = no ] && [ "$vscode_work" = no ] && [ "$deploy_work" = no ]; then
	printf '書き足すものはありません。\n'
	report_missing
	report_deploy_plan
	exit 0
fi

# 設定ファイルへの書き込みを 1 か所にする。写しは最初の 1 回だけ、リンクは
# 切らない、という約束を 2 か所に書き写すと、片方だけ直したときにどちらが
# 壊れるかが打つたびに変わる。
#
# 結果は backed_up と linked に置く。呼んだ側が、その綴りで報告する。
write_json() {
	# 導入前の姿を 1 つだけ残す。2 回目以降は上書きしない。毎回取り直すと、
	# 打ち直した数だけ写しが新しくなり、戻れるのは 1 手前まで――そこには既に
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
	printf '書き換える前の内容は %s.bak にあります（写しは最初の 1 回だけ取ります）。\n' "$SETTINGS_REL"
fi
if [ "$settings_linked" = yes ]; then
	printf '%s はリンクだったので、リンクを保ったまま中身を書きました。\n' "$SETTINGS_REL"
fi
if [ "$vscode_backed_up" = yes ]; then
	printf '書き換える前の内容は %s.bak にあります（写しは最初の 1 回だけ取ります）。\n' "$VSCODE_REL"
fi
if [ "$vscode_linked" = yes ]; then
	printf '%s はリンクだったので、リンクを保ったまま中身を書きました。\n' "$VSCODE_REL"
fi

# 写す。順は実行ファイル → ルール → ゲートの sh。途中で落ちたときに、判定する
# ものだけが在って何を止めるかが無い、という形にしないため。
if [ "$deploy_work" = yes ]; then
	case "$bin_verdict" in
	copy | replace)
		copy_tree "$source_root/$DEPLOY_BIN_DIR" "$root/$bin_dir_rel"
		# 実行の許しを付け直す。cp は元のモードを umask で削って写すので、
		# 配り元の側の置き方によってはここが落ちる。落ちていると hook は
		# 「実行ファイルが無い」ではなく「起動できない」で黙って死ぬ。
		for spelling in "$root/$bin" "$root/$bin.exe"; do
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

report_deploy '写した' '入れ替えた' '.gitignore に足した'

# 登録しただけでは動かない。写し終えたあとの姿をそのまま見て、まだ無いものを
# 挙げる。写しが通っていれば普通はここで何も出ない。出たときは、写しを切ったか、
# 配り元に無かったか、配り先に別のものが既にあって写していないか、のどれか。
#
# ここでは取りに行ったり作ったりはしない。実行ファイルは PyInstaller が Windows
# でだけ .exe を付けるので、両方の綴りで探す（settings.py の _resolve_bin）。
missing_parts=""
note_missing() {
	missing_parts="$missing_parts  $1
"
}
if [ ! -f "$root/$bin" ] && [ ! -f "$root/$bin.exe" ]; then
	note_missing "$bin（ccnavi の実行ファイル。build.py で組み立てる）"
fi
if [ ! -f "$root/.claude/ccnavi/rules.yml" ]; then
	note_missing ".claude/ccnavi/rules.yml（何を止めるか。無いと組み込みの既定だけで判定する）"
fi
for name in ccnavi-ticket.sh ccnavi-review.sh ccnavi-git.sh; do
	if [ ! -f "$root/.claude/scripts/$name" ]; then
		note_missing ".claude/scripts/$name（ゲートの中で通る形）"
	fi
done
if [ -n "$missing_parts" ]; then
	printf 'まだ無いもの:\n%s' "$missing_parts"
	# 諦めた理由は report_deploy が既に出している。ここで足すのは、人が自分で
	# 写しを切ったときだけ。理由を二重に出すと、どちらが今の話か分からなくなる。
	if [ "$deploy_off" = yes ]; then
		# 書式の側に置かない。`--` で始まる文字列は、printf がオプションとして
		# 読んで落ちる。
		printf '%s\n' "--no-deploy を外すと、ccnavi の根から写します。"
	fi
fi

printf 'env の値はセッションを開き直すまで効きません。\n'
