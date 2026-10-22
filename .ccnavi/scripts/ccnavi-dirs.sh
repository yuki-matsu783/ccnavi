#!/bin/sh
# ccnavi-dirs セッションの頭で、プロジェクト直下のディレクトリを新しい順に最大 10 件、文脈へ入れる。
#
#   sh .ccnavi/scripts/ccnavi-dirs.sh
#
# 並びは更新が新しい順（ls -t）。sh だけで Windows (Git Bash / WSL)・Linux・macOS のどれでも
# 同じに動く。作成日時は sh だけでは取れないため、更新日時で代える。隠しディレクトリ（`.git` など）は数えない。
# 11 件以上あるときは、残りを見る ls を案内する。出力はモデルに届く。ディレクトリが無ければ何も出さない。

set -eu

limit=10
root="${CLAUDE_PROJECT_DIR:-.}"
cd "$root" 2>/dev/null || exit 0

# 直下のディレクトリを新しい順に並べる。ディレクトリが 0 個なら glob が残るので、先に捨てる。
list=$(ls -1dt -- */ 2>/dev/null || true)
[ -n "$list" ] || exit 0

total=$(printf '%s\n' "$list" | wc -l | tr -d ' ')

printf '案内: プロジェクト直下のディレクトリ（更新が新しい順、最大 %s 件）\n' "$limit"
printf '%s\n' "$list" | head -n "$limit" | sed 's|/$||; s|^|  - |'
if [ "$total" -gt "$limit" ]; then
  printf '案内: ほかに %s 件あります。全部は `ls -dt -- */` で見られます。\n' "$((total - limit))"
fi
