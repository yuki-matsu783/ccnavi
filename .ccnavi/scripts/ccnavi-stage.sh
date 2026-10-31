#!/bin/sh
# ccnavi-stage 書き込みを止められている場所のファイルを、下書きの置き場で直すための支度をする。
#
#   sh .ccnavi/scripts/ccnavi-stage.sh <パス>...
#   sh .ccnavi/scripts/ccnavi-stage.sh --commands <パス>...
#
# 保護対象のファイル（rules.yml・hook・ラッパースクリプトなど）は書けない。直すときは、cwd のツリーの
# wip/handoff/ に元ファイルを cp して、そちらを編集し、できたものを元の場所へ取り込んでもらう。
# cwd が .claude/worktrees/<名前> の中なら、そのワークツリーの wip/handoff/（ブランチに載るので、
# 非対話セッションで push でき、直接作業なら integrate が取り込みのときに外す）。それ以外はワークスペースルートの
# wip/handoff/。
#
# <パス> は元のファイル。cwd からの相対でも絶対でもよく、置き場の根（cwd がワークツリーならそのワークツリー、
# それ以外はワークスペースルート）の下の実在するファイルだけを受け付ける（リンクと、途中のリンクで根の外に出るものは断る）。
# cwd がワークツリーのとき、ワークスペースルートの絶対パスを渡しても、同じ相対パスのワークツリー側のファイルを元にする
# （無ければ断る）。別のワークツリーのファイルと、下書きの置き場の中は断る。
# 下書きは <置き場の根>/wip/handoff/<置き場の根からの相対パス> に置く（元と同じ階層を保つので、違う元のファイルが同じ下書きに落ちない）。
# ただし、相対パスの中の .claude の階層は handoff-.claude に読み替えて置く（深さを問わない。
# .claude/settings.json → wip/handoff/handoff-.claude/settings.json）。Claude Code 本体は .claude という名前の
# 階層を機密パスとして扱い、非対話（claude -p）では編集を許可できないため。取り込み（ccnavi-user-handoff.sh）が
# 読み替えを戻す。読み替える名前の表は ccnavi-handoff-names.txt（実行ファイルも同じ表を読む）。
# 表の下書きでの名前（handoff-.claude）は予約した名前で、元のパスにその名前の階層があるファイルは断る。
# 下書きが既にあれば上書きしない（編集中のものを消さないため）。
#
# --commands は、編集が済んだあとに打つ。元と違う下書きに、取り込みの手順を出す（全種類が同じ手順。
# 種類の表は handoff.KINDS）。ユーザが ccnavi-user-handoff.sh で下見（検査・差分・ハッシュ）をしてから
# --yes --expect で取り込む。mv で差し替える案内は出さない（検査も差分も --expect も通らず、下書きを作った後に
# 直された元を黙って上書きするため）。
# 元と同じ（まだ編集していない）もの、下書きがまだ無いもの、表の外のもの（取り込めない）は、その旨を標準エラーに出す。
# 複数ファイルを直すときは、すべての下書きを作ってから --commands を打つ。
#
# 打ったあとの進め方（CLAUDE.md）:
#   対話セッション   取り込みの手順をユーザに案内してターンを終える
#   非対話セッション wip/handoff/ の編集済みファイルを push し、ユーザに確認と取り込みを頼んでターンを終える
#
# 終了コード: 0 成功 / 1 前提の未充足（編集済みの下書きが 1 つも無い、など） / 2 引数か環境の誤り

set -eu

# 共通部分。ワークスペースルートの探し方はここにある。
. "$(dirname "$0")/ccnavi-common.sh"

usage() {
	cat <<'USAGE'
sh .ccnavi/scripts/ccnavi-stage.sh <パス>...
sh .ccnavi/scripts/ccnavi-stage.sh --commands <パス>...

  <パス>      直したい元のファイル（置き場の根の下。根は cwd がワークツリーならそのワークツリー、それ以外はワークスペースルート）。cwd からの相対でも絶対でもよい
  --commands  編集が済んだあとに実行します。元と違う下書きだけを、種類ごとに分けて出します
              （取り込みは ccnavi-user-handoff.sh の手順。下見のあと --yes --expect で取り込みます）

下書きは <置き場の根>/wip/handoff/<置き場の根からの相対パス> に cp する
（置き場の根は、cwd がワークツリーならそのワークツリー、それ以外はワークスペースルート）
（既にあれば上書きしない）。.claude の階層は handoff-.claude に読み替えて置く
（.claude/settings.json → wip/handoff/handoff-.claude/settings.json）。取り込みで元に戻す。
複数ファイルを直すときは、すべての下書きを作ってから --commands を実行し、ターンを終える。
USAGE
}

commands=""
case "${1:-}" in
-h | --help | help)
	usage
	exit 0
	;;
--commands)
	commands=1
	shift
	;;
-*)
	printf 'ccnavi-stage: %s は指定できません。指定できるのは --commands だけです。\n' "$1" >&2
	exit 2
	;;
esac

if [ "$#" -eq 0 ]; then
	usage >&2
	exit 2
fi

root=$(ccnavi_workspace) || {
	printf 'ccnavi-stage: ワークスペースルートが見つかりません（.ccnavi/scripts/ccnavi-common.sh を持つ親を cwd から上へ探しました）。ワークスペースの中で実行するか、CCNAVI_WORKSPACE にワークスペースルートの絶対パスを渡してください。\n' >&2
	exit 2
}
# 置き場の根 base。cwd が .claude/worktrees/<名前> の中ならそのワークツリー、それ以外はワークスペースルート。
# 元のファイルの相対パス・元の読み取り・下書きは、すべて base を根にする。
# cwd とルートは、リンクや表記の違い（macOS の /private、Windows の C:/ と /c/ など）を解いてから比べる。
here=$(ccnavi_abs .) || here=""
here=$(ccnavi_phys "$here")
root_phys=$(ccnavi_phys "$root")
case "$here" in
"$root_phys"/.claude/worktrees/*)
	stage_wt="${here#"$root_phys"/.claude/worktrees/}"
	base="$root/.claude/worktrees/${stage_wt%%/*}"
	;;
*) base="$root" ;;
esac
handoff="$base/wip/handoff"
# 置き場（か wip/）がリンクなら、cp と mkdir がリンクの先へ書いてしまう。ワークツリーの置き場はブランチの
# 内容で決まるので、リンクを含むブランチでは書き先が外へ出る。書かずに断る。
if [ -z "$commands" ] && { [ -L "$handoff" ] || [ -L "$(dirname "$handoff")" ]; }; then
	printf 'ccnavi-stage: 下書きの置き場（%s）か wip/ がシンボリックリンクです。リンクの先へは書きません。\n' "$handoff" >&2
	exit 2
fi

# 読み替える階層名の表（ccnavi-handoff-names.txt。実行ファイルも同じファイルを読む）。
# alias_words に「元(小文字) 下書き 下書き(小文字)」の 3 語組を並べる。名前に空白と / は入らない。
names_file="$(dirname "$0")/ccnavi-handoff-names.txt"
alias_words=""
# 表を読む。$@ を書き換えるので、関数の中で読む。
load_aliases() {
	if [ ! -f "$names_file" ]; then
		printf 'ccnavi-stage: 読み替えの表 %s がありません。ccnavi の配布（scripts/ccnavi-setup.sh --force）をやり直してください。\n' "$names_file" >&2
		exit 2
	fi
	while IFS= read -r al_line || [ -n "$al_line" ]; do
		al_line=$(printf '%s' "$al_line" | tr -d '\r')
		case "$al_line" in
		'' | '#'*) continue ;;
		esac
		set -f
		# shellcheck disable=SC2086
		set -- $al_line
		set +f
		if [ "$#" -ne 2 ]; then
			printf 'ccnavi-stage: %s の行を読めません: %s\n' "$names_file" "$al_line" >&2
			exit 2
		fi
		al_before=$(printf '%s' "$1" | LC_ALL=C tr 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' 'abcdefghijklmnopqrstuvwxyz')
		al_after=$(printf '%s' "$2" | LC_ALL=C tr 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' 'abcdefghijklmnopqrstuvwxyz')
		alias_words="$alias_words$al_before $2 $al_after "
	done <"$names_file"
}
load_aliases

# 元の相対パス <$1> の各要素（階層名）を表で読み替え、下書きの置き場の中の相対パスを ad_out に入れる。
# 予約した名前（表の下書きでの名前）の要素があれば、その要素を ad_reserved に入れる（無ければ空）。
# 比べ方は大文字小文字を区別しない ASCII だけ（Python 側は非 ASCII の要素を表に当てない）。
scan_parts() {
	ad_out=""
	ad_reserved=""
	ad_lines=$(printf '%s\n' "$1" | tr '/' '\n')
	while IFS= read -r ad_part; do
		ad_lc=$(printf '%s' "$ad_part" | LC_ALL=C tr 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' 'abcdefghijklmnopqrstuvwxyz')
		ad_new=$ad_part
		set -f
		# shellcheck disable=SC2086
		set -- $alias_words
		set +f
		while [ "$#" -ge 3 ]; do
			if [ "$ad_lc" = "$1" ]; then
				ad_new=$2
			fi
			if [ "$ad_lc" = "$3" ] && [ -z "$ad_reserved" ]; then
				ad_reserved=$ad_part
			fi
			shift 3
		done
		ad_out="${ad_out:+$ad_out/}$ad_new"
	done <<EOF
$ad_lines
EOF
}

# 元のファイルの、置き場の根 base からの相対パスを出す。使えないパスは理由を言って 1 を返す。
# base の下ならその相対パス。そうでなければワークスペースルートの下の相対パス（互換。base がワークツリー
# なら、base 側に同じ相対パスのファイルがあるものとして扱う）。
relative_of() {
	rl_abs=$(ccnavi_abs "$1") || {
		printf 'ccnavi-stage: %s を絶対パスに直せません。\n' "$1" >&2
		return 1
	}
	case "$rl_abs" in
	"$base"/*) rl_rel="${rl_abs#"$base"/}" ;;
	"$root"/*) rl_rel="${rl_abs#"$root"/}" ;;
	*)
		printf 'ccnavi-stage: %s はワークスペースルート（%s）の下にありません。\n' "$1" "$root" >&2
		return 1
		;;
	esac
	rl_lc=$(printf '%s' "$rl_rel" | LC_ALL=C tr 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' 'abcdefghijklmnopqrstuvwxyz')
	case "$rl_lc" in
	wip/handoff/*)
		printf 'ccnavi-stage: %s は下書きの置き場の中です。直したい元のファイルを渡してください。\n' "$1" >&2
		return 1
		;;
	.claude/worktrees/*)
		printf 'ccnavi-stage: %s は別のワークツリーのファイルです。そのワークツリーの中で実行してください。\n' "$1" >&2
		return 1
		;;
	esac
	case "$rl_rel" in
	*"'"* | *'
'*)
		printf 'ccnavi-stage: %s には引用符か改行が入っていて、取り込みコマンドの 1 行に安全に書けません。\n' "$1" >&2
		return 1
		;;
	esac
	scan_parts "$rl_rel"
	if [ -n "$ad_reserved" ]; then
		printf 'ccnavi-stage: %s の `%s` は、下書きでの階層名として予約されています（表: %s）。この名前の階層を含むファイルは下書きにできません。\n' "$base/$rl_rel" "$ad_reserved" "$names_file" >&2
		return 1
	fi
	if [ ! -f "$base/$rl_rel" ]; then
		printf 'ccnavi-stage: %s は実在するファイルではありません。\n' "$base/$rl_rel" >&2
		return 1
	fi
	# リンクは解かれた先が置き場の根の外かもしれない。元がリンクなら、mv がリンクを通常のファイルに
	# 置き換えてしまう。どちらも断る（リンクの先の実体を直接渡す）。
	if [ -L "$base/$rl_rel" ]; then
		printf 'ccnavi-stage: %s はシンボリックリンクです。リンクの先の実体を渡してください。\n' "$base/$rl_rel" >&2
		return 1
	fi
	rl_dir=$(ccnavi_phys "$(dirname "$base/$rl_rel")")
	rl_root=$(ccnavi_phys "$base")
	case "$rl_dir" in
	"$rl_root" | "$rl_root"/*) ;;
	*)
		printf 'ccnavi-stage: %s は、途中のリンクを解くと置き場の根（%s）の外（%s）に出ます。\n' "$base/$rl_rel" "$rl_root" "$rl_dir" >&2
		return 1
		;;
	esac
	printf '%s\n' "$rl_rel"
}

# 元の相対パス <$1> が、ccnavi-user-handoff.sh で取り込める種類（template・messages・layer-config・docs・defense）なら 0。
# 表は src/ccnavi/tickets/sync/handoff.py の KINDS（表の外は 1）。
# 比べ方は ASCII の大文字小文字を区別しない（Python 側は NFC と fold）。表を変えたら、test_stage_sh の例も直す。
is_importable() {
	im_k=$(printf '%s' "$1" | LC_ALL=C tr 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' 'abcdefghijklmnopqrstuvwxyz')
	im_b=$im_k
	case "$im_k" in
	projects/*)
		im_p=${im_k#projects/}
		im_n=${im_p%%/*}
		case "$im_p" in
		"$im_n"/.ccnavi/config/project/*) im_b=${im_p#"$im_n"/} ;;
		esac
		;;
	esac
	case "$im_b" in
	*/.yml | */.md) return 1 ;;
	.ccnavi/config/common/templates/*.yml | .ccnavi/config/project/templates/*.yml)
		case "${im_b#*/templates/}" in */*) return 1 ;; esac
		return 0
		;;
	.ccnavi/messages/*.yml)
		case "${im_b#*/messages/}" in */*) return 1 ;; esac
		return 0
		;;
	.ccnavi/config/common/rules.yml | .ccnavi/config/common/phases.yml | .ccnavi/config/common/risks.yml) return 0 ;;
	.ccnavi/config/project/rules.yml | .ccnavi/config/project/phases.yml | .ccnavi/config/project/risks.yml) return 0 ;;
	.ccnavi/docs/*.md | .ccnavi/ccnavi.md) return 0 ;;
	.ccnavi/scripts/* | .claude/hooks/* | .claude/settings.json | .claude/settings.local.json) return 0 ;;
	.claude/skills/*.md) return 0 ;;
	esac
	return 1
}

bad=0
made=""
imps=""
# 下書きがある（作った・既にあった）パスの並び。取り込みの次の一手に、そのまま埋める
staged=""
same=""
missing=""
outside=""
for arg in "$@"; do
	rel=$(relative_of "$arg") || {
		bad=1
		continue
	}
	orig="$base/$rel"
	scan_parts "$rel"
	draft="$handoff/$ad_out"
	if [ -n "$commands" ]; then
		if [ ! -f "$draft" ]; then
			missing="${missing:+$missing }$rel"
		elif cmp -s "$orig" "$draft"; then
			same="${same:+$same }$rel"
		else
			if is_importable "$rel"; then
				imps="${imps:+$imps }'$rel'"
			else
				outside="${outside:+$outside }$rel"
			fi
		fi
		continue
	fi
	staged="${staged:+$staged }$base/$rel"
	if [ -e "$draft" ]; then
		if cmp -s "$orig" "$draft"; then
			printf '下書きは既にある（元と同じ）: %s\n' "$draft"
		else
			printf '下書きは既にある（編集済み。上書きしない）: %s\n' "$draft"
		fi
	else
		mkdir -p "$(dirname "$draft")"
		cp "$orig" "$draft"
		printf '下書きを作った: %s\n' "$draft"
		made=1
	fi
done

[ "$bad" -eq 0 ] || exit 2

if [ -z "$commands" ]; then
	[ -z "$staged" ] || ccnavi_text "$root" NEXT_STAGE_EDIT "staged_q=$staged"
	exit 0
fi

[ -z "$missing" ] || printf 'ccnavi-stage: 下書きがまだ無い（先に <パス> だけで実行します）: %s\n' "$missing" >&2
[ -z "$same" ] || printf 'ccnavi-stage: 下書きが元と同じ（まだ編集していない）: %s\n' "$same" >&2
[ -z "$outside" ] || printf 'ccnavi-stage: 取り込みの対象外のパスです（種類の表にありません。取り込めないので、保護対象でなければ直接直します）: %s\n' "$outside" >&2
if [ -z "$imps" ]; then
	printf 'ccnavi-stage: 取り込む編集済みの下書きがありません。\n' >&2
	exit 1
fi
# 下書きのあるツリー base（ワークツリーの中で作った下書きは、そのワークツリーが根）
rootopt=""
[ "$base" = "$root" ] || rootopt=" --root '$base'"
hsh="sh '$root/.ccnavi/scripts/ccnavi-user-handoff.sh'$rootopt"
printf '取り込みは、ユーザが次の順で行います（検査・差分・ハッシュを見て、読んだうえで取り込む。差し替えはしない）:\n'
printf '%s %s\n' "$hsh" "$imps"
printf '%s %s --yes --expect <上のハッシュ>\n' "$hsh" "$imps"
[ -z "$missing$same$outside" ] || exit 1
ccnavi_text "$root" NEXT_STAGE_GUIDE "guide=取り込みの手順"
ccnavi_text "$root" NOTE_STAGE_NONINTERACTIVE
