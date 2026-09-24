#!/bin/sh
# ccnavi (Python) と ccnavi-board (VS Code 拡張機能) を組み立て、この機械へ入れる。
#
#   sh scripts/ccnavi-build-install.sh
#
# ccnavi は build.py が組み立てと .ccnavi/bin/<os>-<arch>/ への設置を両方する
# （scripts/../build.py 参照）。
#
# 拡張機能は、いま code に入っている版と vscode-extension/ccnavi-board/package.json の
# 版が同じならパッチ版を 1 つ上げてから組み立てる。同じ版のまま vsix を作っても
# VS Code の Marketplace API は「同じ版は入れ直せない」として弾くので、上げてから作る。
# code コマンドが無い機械（VS Code の入っていない Linux など）では版の比較を飛ばし、
# そのままの版で組み立てるだけにする。
#
# 版を上げるときは vscode-extension/ccnavi-board/package.json を書き換えてそのまま
# コミットする。main などのチェックアウトしたブランチ直下でこのスクリプトを打つと、
# その書き換えとコミットが直にそこへ乗る。CLAUDE.md の「ソースコード編集方法」に
# 従うなら、版が上がる見込みがあるときは先にワークツリーを切り、その中でこの
# スクリプトを打つこと。
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
EXT_DIR="$ROOT/vscode-extension/ccnavi-board"

echo "== ccnavi (Python) を組み立てる =="
(cd "$ROOT" && uv run --with pyinstaller python build.py)

echo ""
echo "== ccnavi-board (VS Code 拡張機能) を組み立てる =="
cd "$EXT_DIR"

pkg_version=$(node -p "require('./package.json').version")

if command -v code >/dev/null 2>&1; then
  installed_version=$(code --list-extensions --show-versions 2>/dev/null | grep -i '^local\.ccnavi-board@' | sed 's/.*@//')
else
  installed_version=""
  echo "code コマンドが無いので、入っている版との比較は飛ばします"
fi

if [ "${installed_version}" = "${pkg_version}" ]; then
  echo "入っている版（${installed_version}）と package.json の版が同じなので、パッチ版を上げます"
  pnpm version patch --no-git-tag-version
  pkg_version=$(node -p "require('./package.json').version")
fi

echo "版 ${pkg_version} を組み立てます"
pnpm run package

vsix="$ROOT/dist/ccnavi-board-${pkg_version}.vsix"
if [ ! -f "$vsix" ]; then
  echo "組み立てたはずの vsix が見当たりません: ${vsix}" >&2
  exit 1
fi

if command -v code >/dev/null 2>&1; then
  echo ""
  echo "== 拡張機能を入れる =="
  code --install-extension "$vsix"
else
  echo ""
  echo "code コマンドが無いので、入れるのは飛ばします。入れるには:"
  echo "  code --install-extension \"${vsix}\""
fi
