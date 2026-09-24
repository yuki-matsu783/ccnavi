#!/bin/sh
# ccnavi (Python) と ccnavi-board (VS Code 拡張機能) を組み立て、この機械へ入れる。
#
#   sh scripts/ccnavi-build-install.sh
#
# ワークツリーの外（チェックアウトしたブランチ直下）で打ったときは、組み立てる前に
# 居るブランチの上流を fast-forward だけで取り込む。古いソースを組み立てて入れないため。
# 上流が無い（切り離し・上流未設定）ときは飛ばす。fast-forward できなければ止まる。
# ワークツリーの中で打ったときは、その枝のソースをそのまま組み立てるので取り込まない。
#
# ccnavi は build.py が組み立てと .ccnavi/bin/<os>-<arch>/ への設置を両方する
# （scripts/../build.py 参照）。
#
# 拡張機能は、いま code にインストール済みの版が vscode-extension/ccnavi-board/package.json の
# 版以上なら、インストール済みの版を基準にパッチ版を 1 つ上げてから組み立てる。package.json の
# 版のほうが大きければ、そのまま組み立てる。同じ版のまま vsix を作っても、VS Code は
# インストール済みの版と同じか古い版を「入れ直せない」として弾くので、インストール済みの版を超えさせる。
# code コマンドが無い機械（VS Code の入っていない Linux など）では版の比較を飛ばし、
# そのままの版で組み立てるだけにする。
#
# 版を上げるときは package.json を書き換えるだけで、コミットはしない。チェックアウトした
# ブランチ直下でこのスクリプトを打つと、その書き換えが未コミットの変更としてそこに残る。
# CLAUDE.md の「ソースコード編集方法」に従うなら、版が上がる見込みがあるときは先に
# ワークツリーを切り、その中でこのスクリプトを打つこと。
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
EXT_DIR="$ROOT/vscode-extension/ccnavi-board"

# ワークツリーでは .git がファイル、チェックアウトした本体では .git がディレクトリ。
if [ -d "$ROOT/.git" ]; then
  echo "== リモートの最新を取り込む =="
  if git -C "$ROOT" rev-parse --abbrev-ref '@{u}' >/dev/null 2>&1; then
    if ! git -C "$ROOT" pull --ff-only; then
      echo "fast-forward で取り込めませんでした。ブランチを揃えてからやり直してください" >&2
      exit 1
    fi
  else
    echo "上流のブランチが無いので、取り込みは飛ばします"
  fi
  echo ""
fi

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
  echo "code コマンドが無いので、インストール済みの版との比較は飛ばします"
fi

# インストール済みの版が package.json の版以上なら、インストール済みの版のパッチを 1 つ上げた版を出す。
# 上げる必要が無ければ何も出さない。
next_version=$(node -e '
const parse = (v) => v.split(".").map((n) => parseInt(n, 10) || 0);
const [pkg, installed] = process.argv.slice(1);
if (!installed) process.exit(0);
const p = parse(pkg);
const i = parse(installed);
let cmp = 0;
for (let k = 0; k < 3 && cmp === 0; k++) cmp = (i[k] || 0) - (p[k] || 0);
if (cmp < 0) process.exit(0);
console.log(i[0] + "." + i[1] + "." + ((i[2] || 0) + 1));
' "$pkg_version" "$installed_version")

if [ -n "${next_version}" ]; then
  echo "インストール済みの版（${installed_version}）が package.json の版（${pkg_version}）以上なので、${next_version} に上げます"
  pnpm version "${next_version}" --no-git-tag-version
  pkg_version="${next_version}"
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
