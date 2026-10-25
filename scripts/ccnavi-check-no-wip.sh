#!/bin/sh
# 統合先へ取り込む PR の差分に、wip/ の追跡済みファイルが入っていないかを確かめる。
# wip/handoff/ は引き継ぎの置き場なので数えない。
#
#   ccnavi-check-no-wip.sh <統合先の ref> [<PR の先頭の ref>]   （先頭の既定は HEAD）
#
# 差分は 3 点（統合先...先頭）で見る。入っていれば一覧を出して 1、無ければ 0。
# チケット作業中の親のブランチは wip/proposals/ を追跡するのが正常なので、Draft の PR では呼ばない
# （.github/workflows/no-wip.yml が Draft を外す）。`ccnavi-review.sh ready` が wip/ を消してから
# Draft を外す決まりなので、Draft が外れた PR に wip/ が残っていれば、ready を通っていない。
set -eu

base=${1:?統合先の ref を渡す}
head=${2:-HEAD}

found=$(git diff --name-only --diff-filter=AMR "$base...$head" -- wip/ ':!wip/handoff/' || :)
if [ -n "$found" ]; then
	printf 'wip/ の追跡済みファイルが差分に入っている（統合先に入れない決まり）:\n' >&2
	printf '%s\n' "$found" | sed 's/^/  /' >&2
	printf '消す（内容が要るなら docs/ か logs/ へ移す）。チケット作業なら ccnavi-review.sh ready が消す。\n' >&2
	exit 1
fi
