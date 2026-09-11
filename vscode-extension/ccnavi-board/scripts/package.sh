#!/bin/sh
# vsix を組み立てる。依存を入れ、コンパイルとテストを通してから、リポジトリの dist/ に出す。
# Windows の Git Bash、WSL、Linux のどれでも同じに動く。pnpm 10 と Node 22 が要る。
set -eu

here=$(cd "$(dirname "$0")/.." && pwd)
cd "$here"

pnpm install --frozen-lockfile
pnpm run compile
pnpm test

version=$(node -p "require('./package.json').version")
out="$(cd "$here/../.." && pwd)/dist"
mkdir -p "$out"
target="$out/ccnavi-board-$version.vsix"

# --no-dependencies: 実行時の依存が無いので node_modules を見に行かない（pnpm の配置も読まない）。
# --skip-license / --allow-missing-repository: ローカル配布なので Marketplace 向けの確認は要らない。
pnpm exec vsce package --no-dependencies --skip-license --allow-missing-repository -o "$target"

echo "$target"
echo "入れるには: code --install-extension \"$target\""
