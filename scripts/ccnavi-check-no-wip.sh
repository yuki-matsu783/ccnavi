#!/bin/sh
# 統合先へ取り込む PR の差分に wip/ の追跡済みファイルが含まれていないかを確かめる。
# wip/handoff/ は引き継ぎ用の置き場なので対象にしない。
#
#   ccnavi-check-no-wip.sh <統合先の ref> [<PR の先頭の ref>]
#
# 先頭を省くと HEAD を見る。差分は「統合先...先頭」の 3 点記法で見る。
# wip/ の追跡済みファイルがあれば一覧を出して 1 で終わり、なければ 0 で終わる。
# チケット作業中の親ブランチは wip/proposals/ を追跡するのが正常なので、Draft の PR では呼ばない。
# Draft を外す判定は .github/workflows/no-wip.yml が行う。決まりでは `ccnavi-review.sh ready` が
# wip/ を消してから Draft を外すため、Draft を外した PR に wip/ が残っていれば、ready を通していないことになる。
set -eu

base=${1:?統合先の ref を渡す}
head=${2:-HEAD}

found=$(git diff --name-only --diff-filter=AMR "$base...$head" -- ':(icase)wip/' ':(icase,exclude)wip/handoff/' || :)
if [ -n "$found" ]; then
	printf '次の wip/ の追跡済みファイルが差分に入っています。統合先には入れない決まりです。\n' >&2
	printf '%s\n' "$found" | sed 's/^/  /' >&2
	printf '消してください。残す内容があれば docs/ か logs/ へ移してください。チケット作業中なら ccnavi-review.sh ready が消します。\n' >&2
	exit 1
fi
