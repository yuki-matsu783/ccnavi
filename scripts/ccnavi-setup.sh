#!/bin/sh
# ccnavi-setup — 対象プロジェクトの .claude/settings.json に、ccnavi が想定する
# env と hook を登録する。ccnavi を新しいプロジェクトへ入れるときに人が 1 回打つ。
#
#   sh scripts/ccnavi-setup.sh [<プロジェクトルート>] [オプション]
#
#   --mode <enable|dry-run>  CCNAVI_MODE。既定は dry-run
#   --bin <相対パス>         CCNAVI_BIN_PATH。既定は dist/ccnavi/ccnavi
#   --all                    既定値を持つ env も明示して書く
#   --force                  既にある env の値を、このスクリプトの値で置き換える
#   --check                  書かずに、足りないものだけを並べる
#
# 何度打っても同じ形に落ち着く。既に登録されている hook は足さないし、既にある
# env は触らない（--force を付けたときだけ置き換える）。ccnavi と関係のない
# hook や設定はそのまま残す。
#
# `disable` は受け付けない。監視される側が書けるファイルから監視を止める形に
# なるので、ccnavi 自身がそれを error として報告する（README「設定lint」）。
# 止めるならセッションを起動する側の環境から渡す。
#
# 終了コード: 0 成功 / 1 --check で不足あり / 2 引数か環境の誤り

set -eu

SETTINGS_REL=".claude/settings.json"
# hook はこの 1 行だけを登録する。どのイベントを走らせるかは、payload が名乗る
# イベント名を見て ccnavi 自身が選ぶ。
HOOK_COMMAND='"${CLAUDE_PROJECT_DIR}/${CCNAVI_BIN_PATH}"'
HOOK_TIMEOUT=10
# 7 つすべてに登録する。1 つ欠けると、そのイベントでしかできない仕事が黙って
# 落ちる。何が落ちるかは README「設定」の表にある。
EVENTS="SessionStart UserPromptSubmit PreToolUse PostToolUse Stop SubagentStart SubagentStop"

mode="dry-run"
bin="dist/ccnavi/ccnavi"
all=no
force=no
check=no
target=""

usage() {
	cat <<'USAGE'
sh scripts/ccnavi-setup.sh [<プロジェクトルート>] [オプション]

  <プロジェクトルート>          既定は現在の作業ディレクトリ
  --mode <enable|dry-run>   CCNAVI_MODE。既定は dry-run
  --bin <相対パス>          CCNAVI_BIN_PATH。既定は dist/ccnavi/ccnavi
  --all                     既定値を持つ env も明示して書く
  --force                   既にある env の値を置き換える
  --check                   書かずに、足りないものだけを並べる
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
		shift 2
		;;
	--bin)
		[ "$#" -ge 2 ] || die "--bin に値がありません。"
		bin="$2"
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
	-h | --help | help)
		usage
		exit 0
		;;
	-*)
		die "$1 は知らないオプションです。使える形は --help に出ます。"
		;;
	*)
		[ -z "$target" ] || die "プロジェクトルートは 1 つだけ受け取ります。"
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

case "$bin" in
/* | ?:*)
	die "--bin はプロジェクトルートからの相対で書いてください。env の値は \${CLAUDE_PROJECT_DIR} を展開しないので、絶対パスは 3 つの環境で綴りが変わります。"
	;;
esac

command -v jq >/dev/null 2>&1 || die "jq が要ります。"

[ -n "$target" ] || target="."
[ -d "$target" ] || die "$target というディレクトリがありません。"
# 表示のために絶対化する。Git Bash では pwd -W が Windows 形式を返すので、
# 人が設定ファイルを開くときにそのまま使える綴りになる。
root=$(cd "$target" && { pwd -W 2>/dev/null || pwd; })
settings="$root/$SETTINGS_REL"

# 読む側を 1 か所にする。ファイルが無いときは空のオブジェクトとして扱う。
# ccnavi を入れる前のプロジェクトには settings.json が無いのが普通で、
# それは異常ではない。
if [ -e "$settings" ]; then
	[ -f "$settings" ] || die "$settings がファイルではありません。"
	current=$(cat "$settings")
	printf '%s' "$current" | jq -e . >/dev/null 2>&1 ||
		die "$SETTINGS_REL が JSON として読めません。直してから打ち直してください。"
else
	current="{}"
fi

# 必ず書く env。既定を持たない CCNAVI_BIN_PATH と、既定と同じでも書いておきたい
# 2 つのパス。設定ファイルだけを見て、どこを読み書きするかが分かる形にする。
required_env() {
	cat <<ENV
CCNAVI_MODE=$mode
CCNAVI_RULES=.claude/ccnavi/rules.yml
CCNAVI_LOG=.claude/ccnavi/log.jsonl
CCNAVI_BIN_PATH=$bin
ENV
}

# --all のときだけ足す、既定と同じ値の env。書かなくても同じように動く。
# 書く利点は、あとで値を変えたくなった人が、つまみの一覧を README ではなく
# 設定ファイルの中で見つけられること。
optional_env() {
	cat <<'ENV'
CCNAVI_STATE=.claude/ccnavi/state
CCNAVI_TICKETS=wip/tickets
CCNAVI_APPROVED=.claude/ccnavi/tickets
CCNAVI_PHASES=.claude/ccnavi/phases.yml
CCNAVI_RISK=.claude/ccnavi/risk.yml
CCNAVI_RESTORE_IF_DENY=enable
CCNAVI_GUARD_CORE_FILES=enable
CCNAVI_GUARD_CLI=enable
ENV
}

collect_env() {
	required_env
	if [ "$all" = yes ]; then
		optional_env
	fi
}

# 値に "=" が入りうるので、最初の 1 つだけで割る。
env_json=$(collect_env | jq -R -s '
	split("\n") | map(select(length > 0)) | map(
		(index("=")) as $i | {(.[0:$i]): .[$i + 1:]}
	) | add
')
events_json=$(printf '%s\n' $EVENTS | jq -R -s 'split("\n") | map(select(length > 0))')

# 登録の見方は ccnavi の設定lint と揃えてある（lint.py の _registered）。
# command の綴りはプロジェクトごとに違うので、名前が入っているかどうかで見る。
# 大文字小文字を無視するのは、${CCNAVI_BIN_PATH} で書く形が普通にあるため。
REGISTERED='def registered($ev):
	[ (.hooks[$ev] // [])[] | (.hooks // [])[] | (.command // "") | ascii_downcase | test("ccnavi") ] | any;'

missing_env=$(printf '%s' "$current" | jq -r --argjson env "$env_json" '
	(.env // {}) as $cur | $env | keys_unsorted[] | select($cur[.] == null)
')
differing_env=$(printf '%s' "$current" | jq -r --argjson env "$env_json" '
	(.env // {}) as $cur | $env | to_entries[]
	| select($cur[.key] != null and $cur[.key] != .value)
	| "\(.key): \($cur[.key]) -> \(.value)"
')
# 根を $root に取り置いてから回す。イベント名を `.` に置いたまま registered を
# 呼ぶと、関数の中の `.hooks` が文字列を引くことになる。
missing_hooks=$(printf '%s' "$current" | jq -r --argjson events "$events_json" "
	$REGISTERED
	. as \$root | \$events[] as \$ev | select(\$root | registered(\$ev) | not) | \$ev
")

report() {
	if [ -n "$missing_env" ]; then
		printf '足りない env:\n'
		printf '%s\n' "$missing_env" | sed 's/^/  /'
	fi
	if [ -n "$missing_hooks" ]; then
		printf 'ccnavi が登録されていない hook:\n'
		printf '%s\n' "$missing_hooks" | sed 's/^/  /'
	fi
	if [ -n "$differing_env" ]; then
		if [ "$force" = yes ]; then
			printf '置き換える env:\n'
		else
			printf '値が違う env（--force を付けない限りそのまま）:\n'
		fi
		printf '%s\n' "$differing_env" | sed 's/^/  /'
	fi
}

if [ "$check" = yes ]; then
	printf '%s\n' "$settings"
	report
	if [ -z "$missing_env" ] && [ -z "$missing_hooks" ]; then
		printf 'env と hook は揃っています。\n'
		exit 0
	fi
	exit 1
fi

if [ -z "$missing_env" ] && [ -z "$missing_hooks" ] &&
	{ [ "$force" = no ] || [ -z "$differing_env" ]; }; then
	printf '%s\n変えるところがありません。\n' "$settings"
	exit 0
fi

if [ "$force" = yes ]; then
	force_json=true
else
	force_json=false
fi

# 既存を残す向きでマージする。env は既にある値を勝たせ（--force のときだけ逆）、
# hook は ccnavi が登録されていないイベントにだけ足す。ccnavi と関係のない
# hook の隣に並ぶ形になるので、他の道具の設定を消さない。
updated=$(printf '%s' "$current" | jq --argjson env "$env_json" \
	--argjson events "$events_json" \
	--arg cmd "$HOOK_COMMAND" \
	--argjson timeout "$HOOK_TIMEOUT" \
	--argjson force "$force_json" "
	$REGISTERED
	.env = (if \$force then ((.env // {}) + \$env) else (\$env + (.env // {})) end)
	| reduce \$events[] as \$ev (
		.;
		if registered(\$ev) then .
		else .hooks[\$ev] = ((.hooks[\$ev] // []) + [{
			matcher: \"\",
			hooks: [{type: \"command\", command: \$cmd, timeout: \$timeout}]
		}])
		end
	)
")

mkdir -p "$(dirname "$settings")"
# 書く前に写しを取る。書き損じたときに戻せる形が残っていないと、hook の登録を
# 失った状態から手で組み直すことになる。
if [ -f "$settings" ]; then
	cp "$settings" "$settings.bak"
	backed_up=yes
else
	backed_up=no
fi
# 同じディレクトリに書いてから動かす。別のファイルシステムをまたぐと mv が
# コピーに落ちて、途中で切れた設定ファイルが残りうる。
tmp="$settings.tmp.$$"
printf '%s\n' "$updated" >"$tmp"
mv "$tmp" "$settings"

printf '%s\n' "$settings"
report
if [ "$backed_up" = yes ]; then
	printf '前の内容は %s.bak にあります。\n' "$SETTINGS_REL"
fi

# 登録しただけでは動かない。ここから先は人が置くものなので、無いものを挙げる。
# 挙げるだけで、取りに行ったり作ったりはしない。実行ファイルは PyInstaller が
# Windows でだけ .exe を付けるので、両方の綴りで探す（settings.py の _resolve_bin）。
missing_parts=""
note_missing() {
	missing_parts="$missing_parts  $1
"
}
if [ ! -e "$root/$bin" ] && [ ! -e "$root/$bin.exe" ]; then
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
