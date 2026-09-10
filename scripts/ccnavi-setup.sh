#!/bin/sh
# ccnavi-setup — 対象プロジェクトの .claude/settings.json に、ccnavi が想定する
# env と hook を登録する。ccnavi を新しいプロジェクトへ入れるときに人が 1 回打つ。
#
#   sh scripts/ccnavi-setup.sh [<ワークスペースルート>] [オプション]
#
#   --mode <enable|dry-run>  CCNAVI_MODE。既定は dry-run
#   --bin <相対パス>         CCNAVI_BIN_PATH。既定は dist/ccnavi/ccnavi
#   --all                    既定値を持つ env も明示して書く
#   --force                  明示した --mode / --bin で、既にある値を置き換える
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
DEFAULT_BIN="dist/ccnavi/ccnavi"

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

usage() {
	cat <<'USAGE'
sh scripts/ccnavi-setup.sh [<ワークスペースルート>] [オプション]

  <ワークスペースルート>      既定は現在の作業ディレクトリ
  --mode <enable|dry-run>   CCNAVI_MODE。既定は dry-run
  --bin <相対パス>          CCNAVI_BIN_PATH。既定は dist/ccnavi/ccnavi
  --all                     既定値を持つ env も明示して書く
  --force                   明示した --mode / --bin で、既にある値を置き換える
  --check                   書かずに、揃っていないところだけを並べる
  --no-vscode               .vscode/settings.json には触らない
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

# 必ず書く env。既定を持たない CCNAVI_BIN_PATH と、既定と同じでも書いておきたい
# 2 つのパス。設定ファイルだけを見て、どこを読み書きするかが分かる形にする。
#
# 値は --arg で 1 つずつ渡す。行に組んでから割ると、値に混ざった改行がそのまま
# 行の区切りになり、ここで拒んだはずの CCNAVI_MODE=disable を --bin 経由で
# 書き込めてしまう。
env_json=$(jq -n --arg mode "$mode" --arg bin "$bin" '{
	CCNAVI_MODE: $mode,
	CCNAVI_RULES: ".claude/ccnavi/rules.yml",
	CCNAVI_LOG: ".claude/ccnavi/log.jsonl",
	CCNAVI_BIN_PATH: $bin
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
		CCNAVI_RISK: ".claude/ccnavi/risk.yml",
		CCNAVI_RESTORE_IF_DENY: "enable",
		CCNAVI_GUARD_CORE_FILES: "enable",
		CCNAVI_GUARD_CLI: "enable"
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

# 揃っているか。値の違いと、別の綴りの登録も「揃っていない」に数える。
# ここを不足の 2 つだけで決めると、CCNAVI_MODE=disable が書かれた設定に
# --check を打って「揃っています」と言うことになる。
settled=yes
if [ -n "$missing_env" ] || [ -n "$missing_hooks" ] ||
	[ -n "$replacing_env" ] || [ -n "$differing_env" ] || [ -n "$other_hooks" ] ||
	[ -n "$missing_vscode" ] || [ -n "$differing_vscode" ] || [ -n "$vscode_blocked" ]; then
	settled=no
fi

if [ "$check" = yes ]; then
	printf '%s\n' "$settings"
	report_missing
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

# 書くものがあるか。値が違うだけで名指しされていないもの、別の綴りの登録は、
# このスクリプトが触らないので書き込みには数えない。ただし黙って終わらせない。
if [ -z "$missing_env" ] && [ -z "$missing_hooks" ] && [ -z "$replacing_env" ] &&
	[ -z "$missing_vscode" ]; then
	printf '%s\n書き足すものはありません。\n' "$settings"
	report_missing
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
if [ -n "$missing_env" ] || [ -n "$missing_hooks" ] || [ -n "$replacing_env" ]; then
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
if [ -n "$missing_vscode" ]; then
	vscode_updated=$(printf '%s' "$vscode_current" | jq --argjson want "$VSCODE_KEYS" '$want + .')
	write_json "$vscode_settings" "$vscode_updated"
	vscode_backed_up=$backed_up
	vscode_linked=$linked
fi

printf '%s\n' "$settings"
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

# 登録しただけでは動かない。ここから先は人が置くものなので、無いものを挙げる。
# 挙げるだけで、取りに行ったり作ったりはしない。実行ファイルは PyInstaller が
# Windows でだけ .exe を付けるので、両方の綴りで探す（settings.py の _resolve_bin）。
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
fi

printf 'env の値はセッションを開き直すまで効きません。\n'
