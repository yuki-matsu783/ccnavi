#!/bin/sh
# ccnavi-dirs セッションの頭で、projects/ に clone 済みの外リポジトリを新しい順に最大 10 件、文脈へ入れる。
#
#   sh .ccnavi/scripts/ccnavi-dirs.sh
#
# 数えるのは projects/ 直下で `.git`（ディレクトリでもファイルでも）を持つもの。.git の無いディレクトリは
# 数えない。並びは更新が新しい順（ls -t）。sh だけで Windows (Git Bash / WSL)・Linux・macOS のどれでも
# 同じに動く。作成日時は sh だけでは取れないため、更新日時で代える。
# 11 件以上あるときは、残りを見る ls を案内する。出力はモデルに届く。該当が無ければ何も出さない。

set -eu

limit=10
root="${CLAUDE_PROJECT_DIR:-.}"
cd "$root/projects" 2>/dev/null || exit 0

# projects/ 直下のディレクトリを新しい順に並べ、.git を持つものだけ残す。0 個なら glob が残るので、先に破棄する。
all=$(ls -1dt -- */ 2>/dev/null || true)
[ -n "$all" ] || exit 0
list=$(printf '%s\n' "$all" | while IFS= read -r d; do
  if [ -e "${d}.git" ]; then printf '%s\n' "$d"; fi
done)
[ -n "$list" ] || exit 0

total=$(printf '%s\n' "$list" | wc -l | tr -d ' ')

printf '案内: projects/ に clone 済みのリポジトリ（更新が新しい順、最大 %s 件）\n' "$limit"
printf '%s\n' "$list" | head -n "$limit" | sed 's|/$||; s|^|  - |'
if [ "$total" -gt "$limit" ]; then
  printf '案内: ほかに %s 件あります。全部は `ls -dt projects/*/` で見られます。\n' "$((total - limit))"
fi
