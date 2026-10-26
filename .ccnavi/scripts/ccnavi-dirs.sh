#!/bin/sh
# ccnavi-dirs セッションの頭で、projects/ に clone 済みの外リポジトリを新しい順に最大 10 件、文脈へ入れる。
#
#   sh .ccnavi/scripts/ccnavi-dirs.sh
#
# 数えるのは projects/ 直下で `.git`（ディレクトリでもファイルでも）を持つもの。.git の無いディレクトリは
# 数えない。ワークスペースルートで起動する前提（CLAUDE_PROJECT_DIR の projects/ を見る）。
# 並びは最新コミットの時刻が新しい順（git log -1）。コミットが無い・読めないものは末尾に回す。
# sh だけで Windows (Git Bash / WSL)・Linux・macOS のどれでも同じに動く。
# 出力はモデルに届くので、プロジェクト名だけを出す。11 件以上あるときは、残りの件数だけ添える。該当が無ければ何も出さない。

set -eu

limit=10
root="${CLAUDE_PROJECT_DIR:-.}"
cd "$root/projects" 2>/dev/null || exit 0

# projects/ 直下のうち .git を持つものを、最新コミットの時刻（無ければ 0）と並べて、新しい順にする。
# ディレクトリが 0 個なら glob が残るので、先に破棄する。
all=$(ls -1d -- */ 2>/dev/null || true)
[ -n "$all" ] || exit 0
tab=$(printf '\t')
list=$(printf '%s\n' "$all" | while IFS= read -r d; do
  if [ -e "${d}.git" ]; then
    ts=$(git -C "$d" log -1 --format=%ct HEAD 2>/dev/null || true)
    case "$ts" in '' | *[!0-9]*) ts=0 ;; esac
    printf '%s\t%s\n' "$ts" "$d"
  fi
done | sort -t "$tab" -k1,1nr -k2,2 | cut -f2-)
[ -n "$list" ] || exit 0

total=$(printf '%s\n' "$list" | wc -l | tr -d ' ')

printf '案内: projects/ のプロジェクト（最新コミットが新しい順、最大 %s 件）\n' "$limit"
printf '%s\n' "$list" | head -n "$limit" | sed 's|/$||; s|^|  - |'
if [ "$total" -gt "$limit" ]; then
  printf '案内: ほかに %s 件あります。\n' "$((total - limit))"
fi
