#!/bin/sh
# ccnavi-review — レビューの依頼と確認。親（メインエージェント）だけが呼ぶ。
#
#   sh .claude/scripts/ccnavi-review.sh request --phase <N> --body-file <依頼文>
#   sh .claude/scripts/ccnavi-review.sh check   --phase <N>
#   sh .claude/scripts/ccnavi-review.sh note    --body-file <本文>
#
# request は前提（フェーズが終わっている・子ブランチが親に取り込まれている・未コミット
# 無し・push 済み・MR がある・未依頼）を全部確かめてから依頼コメントを投稿し、依頼の印を
# 置く。check は依頼より後のスレッドとレビューだけを見て、未解決が無ければレビュー済みの
# 印を置き、ゲートが開く。変更要求のレビューが立っている間は通らない。
# note はチャットで受けた判断を MR のコメントに写す。
#
# 親の作業ツリーの中で実行すること。どの親かは cwd から引く。
# 本体は ccnavi の `review` サブコマンド。ここは実行ファイルを探して渡すだけ。
# 終了コード: 0 成功 / 1 前提の未充足 / 2 引数か環境の誤り

set -eu

usage() {
	cat <<'USAGE'
sh .claude/scripts/ccnavi-review.sh <request|check|note> [--phase <N>] [--body-file <path>]

  request  --phase <N> --body-file <依頼文>   前提を確かめて依頼を投稿し、依頼の印を置く
  check    --phase <N>                         依頼より後の未解決スレッドが無ければ印を置く
  note     --body-file <本文>                  判断の記録を MR のコメントに写す

未解決を残したまま進める判断は利用者が端末で打つ:
  ccnavi --reviewed <N> --accept-unresolved --cwd <親の作業ツリー>
GITLAB_TOKEN / GITHUB_TOKEN が要る。どちらかはリモートの URL で決まる。
USAGE
}

[ "$#" -ge 1 ] || {
	usage
	exit 2
}
case "$1" in
request | check | note) ;;
-h | --help | help)
	usage
	exit 0
	;;
*)
	printf 'ccnavi-review: %s は通しません。使えるのは request / check / note です。\n' "$1" >&2
	exit 2
	;;
esac

common=$(git rev-parse --git-common-dir 2>/dev/null || :)
[ -z "$common" ] && {
	printf 'ccnavi-review: git リポジトリの中で実行してください。\n' >&2
	exit 2
}
case "$common" in
*/.git) root="${common%/.git}" ;;
.git) root="$(pwd -W 2>/dev/null || pwd)" ;;
*) root="$common" ;;
esac
here="$(pwd -W 2>/dev/null || pwd)"

bin="$root/${CCNAVI_BIN_PATH:-dist/ccnavi/ccnavi}"
if [ -x "$bin" ]; then
	exec "$bin" --root "$root" --cwd "$here" review "$@"
elif [ -x "$bin.exe" ]; then
	exec "$bin.exe" --root "$root" --cwd "$here" review "$@"
elif [ -f "$root/ccnavi/__main__.py" ]; then
	cd "$root"
	exec uv run python -m ccnavi --root "$root" --cwd "$here" review "$@"
fi
printf 'ccnavi-review: ccnavi の実行ファイルが無い (%s)。build.py で組み立ててください。\n' "$bin" >&2
exit 2
