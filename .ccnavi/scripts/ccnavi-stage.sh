#!/bin/sh
# ccnavi-stage 書き込みを止められている場所のファイルを、下書きの置き場で直すための支度をする。
#
#   sh .ccnavi/scripts/ccnavi-stage.sh <パス>...
#   sh .ccnavi/scripts/ccnavi-stage.sh --commands <パス>...
#
# 保護対象のファイル（rules.yml・hook・ラッパースクリプトなど）は書けない。直すときは、cwd のツリーの
# wip/handoff/ に元ファイルを cp して、そちらを編集し、できたものを元の場所へ差し替えてもらう。
# cwd が .claude/worktrees/<名前> の中なら、そのワークツリーの wip/handoff/（ブランチに載るので、
# 非対話セッションで push でき、直接作業なら integrate が取り込みのときに外す）。それ以外はワークスペースルートの
# wip/handoff/。
# 差し替えの mv は、フルパスを手で組むと書き間違えやすいので、このスクリプトが組む。
#
# <パス> は元のファイル。cwd からの相対でも絶対でもよく、ワークスペースルートの下の実在するファイルだけを受け付ける
# （リンクと、途中のリンクでワークスペースの外に出るものは断る）。
# 下書きは <置き場>/wip/handoff/<ワークスペースルートからの相対パス> に置く（元と同じ階層を保つので、同じ名前が衝突しない）。
# 下書きが既にあれば上書きしない（編集中のものを消さないため）。
#
# --commands は、編集が済んだあとに打つ。下書きが元と違うものだけ、差し替えの mv をフルパスで 1 行ずつ出す。
# 元と同じ（まだ編集していない）もの、下書きがまだ無いもの、下書きを作った後に元が変わったもの
# （cp 時のコピー <下書き>.orig と比べる。mv すると他の人の変更を上書きする）は、その旨を標準エラーに言い、mv は出さない。
# 複数ファイルを直すときは、すべての下書きを作ってから --commands を打つ。
#
# 打ったあとの進め方（CLAUDE.md）:
#   対話セッション   mv をユーザに案内してターンを終える
#   非対話セッション wip/handoff/ の編集済みファイルを push し、ユーザに確認と差し替えを頼んでターンを終える
#
# 終了コード: 0 成功 / 1 前提の未充足（編集済みの下書きが 1 つも無い、など） / 2 引数か環境の誤り

set -eu

# 共通部分。ワークスペースルートの探し方はここにある。
. "$(dirname "$0")/ccnavi-common.sh"

usage() {
	cat <<'USAGE'
sh .ccnavi/scripts/ccnavi-stage.sh <パス>...
sh .ccnavi/scripts/ccnavi-stage.sh --commands <パス>...

  <パス>      直したい元のファイル（ワークスペースルートの下）。cwd からの相対でも絶対でもよい
  --commands  編集が済んだあとに実行します。下書きが元と違うものだけ、差し替えの mv をフルパスで出す

下書きは <cwd のワークツリー（無ければワークスペースルート）>/wip/handoff/<ワークスペースルートからの相対パス> に cp する
（既にあれば上書きしない）。
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
	printf 'ccnavi-stage: %s は使用できません。使えるのは --commands だけです。\n' "$1" >&2
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
# 下書きの置き場。cwd が .claude/worktrees/<名前> の中ならそのワークツリー、それ以外はワークスペースルート。
# cwd とルートは、リンクや表記の違い（macOS の /private、Windows の C:/ と /c/ など）を解いてから比べる。
here=$(ccnavi_abs .) || here=""
here=$(ccnavi_phys "$here")
root_phys=$(ccnavi_phys "$root")
case "$here" in
"$root_phys"/.claude/worktrees/*)
	stage_wt="${here#"$root_phys"/.claude/worktrees/}"
	handoff="$root/.claude/worktrees/${stage_wt%%/*}/wip/handoff"
	;;
*) handoff="$root/wip/handoff" ;;
esac
# 置き場（か wip/）がリンクなら、cp と mkdir がリンクの先へ書く。ワークツリーの置き場はブランチの
# 内容で決まるので、リンクを追跡しているブランチだと書き先が外へ出る。書かずに断る。
if [ -z "$commands" ] && { [ -L "$handoff" ] || [ -L "$(dirname "$handoff")" ]; }; then
	printf 'ccnavi-stage: 下書きの置き場（%s）か wip/ がシンボリックリンクです。リンクの先へは書きません。\n' "$handoff" >&2
	exit 2
fi

# 元のファイルの、ワークスペースルートからの相対パスを出す。使えないパスは理由を言って 1 を返す。
relative_of() {
	rl_abs=$(ccnavi_abs "$1") || {
		printf 'ccnavi-stage: %s を絶対パスに直せません。\n' "$1" >&2
		return 1
	}
	case "$rl_abs" in
	"$root"/*) rl_rel="${rl_abs#"$root"/}" ;;
	*)
		printf 'ccnavi-stage: %s はワークスペースルート（%s）の下にありません。\n' "$1" "$root" >&2
		return 1
		;;
	esac
	case "$rl_rel" in
	wip/handoff/* | .claude/worktrees/*/wip/handoff/*)
		printf 'ccnavi-stage: %s は下書きの置き場の中です。直したい元のファイルを渡してください。\n' "$1" >&2
		return 1
		;;
	*"'"* | *'
'*)
		printf 'ccnavi-stage: %s には引用符か改行が入っていて、mv の 1 行に安全に書けません。\n' "$1" >&2
		return 1
		;;
	esac
	if [ ! -f "$root/$rl_rel" ]; then
		printf 'ccnavi-stage: %s は実在するファイルではありません。\n' "$root/$rl_rel" >&2
		return 1
	fi
	# リンクは解かれた先がワークスペースの外かもしれない。元がリンクなら、mv がリンクを通常のファイルに
	# 置き換えてしまう。どちらも断る（リンクの先の実体を直接渡す）。
	if [ -L "$root/$rl_rel" ]; then
		printf 'ccnavi-stage: %s はシンボリックリンクです。リンクの先の実体を渡してください。\n' "$root/$rl_rel" >&2
		return 1
	fi
	rl_dir=$(ccnavi_phys "$(dirname "$root/$rl_rel")")
	rl_root=$(ccnavi_phys "$root")
	case "$rl_dir" in
	"$rl_root" | "$rl_root"/*) ;;
	*)
		printf 'ccnavi-stage: %s は、途中のリンクを解くとワークスペースルートの外（%s）に出ます。\n' "$root/$rl_rel" "$rl_dir" >&2
		return 1
		;;
	esac
	printf '%s\n' "$rl_rel"
}

bad=0
made=""
# 下書きがある（作った・既にあった）パスの並び。差し替えの次の一手に、そのまま埋める
staged=""
mvs=""
same=""
missing=""
changed=""
for arg in "$@"; do
	rel=$(relative_of "$arg") || {
		bad=1
		continue
	}
	orig="$root/$rel"
	draft="$handoff/$rel"
	if [ -n "$commands" ]; then
		if [ ! -f "$draft" ]; then
			missing="${missing:+$missing }$rel"
		elif cmp -s "$orig" "$draft"; then
			same="${same:+$same }$rel"
		elif [ -f "$draft.orig" ] && ! cmp -s "$orig" "$draft.orig"; then
			# 下書きを作った後に元が変わった。mv すると、その変更を黙って上書きする。
			changed="${changed:+$changed }$rel"
		else
			mvs="${mvs}mv '$draft' '$orig'
"
		fi
		continue
	fi
	staged="${staged:+$staged }$root/$rel"
	if [ -e "$draft" ]; then
		if cmp -s "$orig" "$draft"; then
			printf '下書きは既にある（元と同じ）: %s\n' "$draft"
		else
			printf '下書きは既にある（編集済み。上書きしない）: %s\n' "$draft"
		fi
	else
		mkdir -p "$(dirname "$draft")"
		cp "$orig" "$draft"
		# 作った時点の元のコピー。--commands が、元が後から変わっていないかを確かめるのに使う。
		cp "$orig" "$draft.orig"
		printf '下書きを作った: %s\n' "$draft"
		made=1
	fi
done

[ "$bad" -eq 0 ] || exit 2

if [ -z "$commands" ]; then
	[ -z "$staged" ] || printf '次: 下書きを編集する。すべて直し終えたら、下書きを作ったのと同じツリーの中で sh %s/.ccnavi/scripts/ccnavi-stage.sh --commands %s を打つ（差し替えの mv が出る）\n' "$root" "$staged"
	exit 0
fi

[ -z "$missing" ] || printf 'ccnavi-stage: 下書きがまだ無い（先に <パス> だけで実行します）: %s\n' "$missing" >&2
[ -z "$same" ] || printf 'ccnavi-stage: 下書きが元と同じ（まだ編集していない）: %s\n' "$same" >&2
[ -z "$changed" ] || printf 'ccnavi-stage: 下書きを作った後に元のファイルが変わっている（別のセッションかユーザの変更かもしれない。mv は出さない。元の差分を確かめ、下書きに取り込んでから、下書きと %s.orig を作り直す）: %s\n' '<下書き>' "$changed" >&2
if [ -z "$mvs" ]; then
	printf 'ccnavi-stage: 差し替える編集済みの下書きがありません。\n' >&2
	exit 1
fi
printf '差し替えは、ユーザが次を実行します（元のファイルは保護対象なので、エージェントは差し替えない）:\n'
printf '%s' "$mvs"
[ -z "$missing$same$changed" ] || exit 1
printf '次: 上の mv をユーザに案内してターンを終える\n'
printf '案内: 非対話セッションでは、編集済みの下書きを push し、確認と差し替えをユーザに頼んでターンを終える\n'
