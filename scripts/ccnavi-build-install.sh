#!/bin/sh
# ccnavi (Python) と ccnavi-board (VS Code 拡張機能) をビルドし、このマシンにインストールする。
#
#   sh scripts/ccnavi-build-install.sh
#
# ワークツリーの外（チェックアウトしたブランチ直下）で実行したときは、ビルドの前に
# いまのブランチが追跡しているリモートブランチから、fast-forward だけで取り込む。古いソースをビルドしてインストールしないため。
# 追跡するリモートブランチが無いとき（detached HEAD や未設定のとき）は飛ばす。fast-forward できなければ止まる。
# ワークツリーの中で実行したときは、そのブランチのソースをそのままビルドするので取り込まない。
#
# ccnavi は、build.py がビルドと .ccnavi/bin/<os>-<arch>/ への配置を両方行う
# （scripts/../build.py 参照）。
#
# 拡張機能は、code にインストール済みのバージョンが extensions/vscode/ccnavi-board/package.json の
# バージョン以上なら、インストール済みのバージョンのパッチを 1 つ上げてからビルドする。
# package.json のほうが大きければ、そのままビルドする。VS Code は、インストール済みと同じか
# 古いバージョンの vsix を入れ直しとして受け付けない。そのため、インストール済みのバージョンを超える番号にする。
# code コマンドはまず PATH から探し、無ければ VS Code の既定のインストール先を探す。
# インストール時に「PATH へ追加」を外していると、端末からは code が見つからないため。
# 環境変数 CODE で場所を渡せば、それを使う。どこにも無いマシン（VS Code の入っていない Linux など）
# では、バージョンの比較を飛ばし、いまのバージョンのままビルドだけを行う。
#
# バージョンを上げるときは package.json を書き換えるだけで、コミットはしない。チェックアウトした
# ブランチ直下でこのスクリプトを実行すると、その書き換えが未コミットの変更として残る。
# docs/claude/worktree.md に従うなら、バージョンが上がりそうなときは先にワークツリーを切り、
# その中でこのスクリプトを実行すること。
#
# 処理はすべて main 関数に入れ、最後の 1 行で呼び出す。取り込みで、このスクリプト自身が
# 書き換わることがある。sh はファイルを読みながら実行するので、関数に入れずにいると、
# 書き換わったあとはずれた位置から読み続けて構文エラーになる。関数なら、実行前に全体を読み終えている。
# 書き換わったあとも、今回の実行は読み込んだ時点の内容のまま最後まで進む。
set -eu

# code コマンドの場所を出力する。見つからなければ何も出力しない。
find_code() {
  # 環境変数 CODE で場所を指定されていれば、それを優先する。
  if [ -n "${CODE:-}" ]; then
    echo "$CODE"
    return
  fi
  # PATH に code があれば、そのパスを使う。
  if command -v code >/dev/null 2>&1; then
    command -v code
    return
  fi
  # PATH に無ければ、VS Code の既定のインストール先を順に探す。
  # 上から Windows（ユーザー単位・全ユーザー）、WSL、macOS（全ユーザー・ユーザー単位）。
  for c in \
    "$HOME/AppData/Local/Programs/Microsoft VS Code/bin/code" \
    "/c/Program Files/Microsoft VS Code/bin/code" \
    "/mnt/c/Program Files/Microsoft VS Code/bin/code" \
    "/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code" \
    "$HOME/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code"; do
    if [ -f "$c" ]; then
      echo "$c"
      return
    fi
  done
  # どこにも無ければ、何も出力せずに終わる。
}

main() {
  # このスクリプトは scripts/ にあるので、1 つ上をリポジトリのルートとして扱う。
  ROOT=$(cd "$(dirname "$0")/.." && pwd)
  # 拡張機能のソースがあるディレクトリ。
  EXT_DIR="$ROOT/extensions/vscode/ccnavi-board"

  # .git がディレクトリなら、ワークツリーではなく本体で実行している。そのときだけリモートから取り込む。
  # （ワークツリーでは .git はファイルになる）
  if [ -d "$ROOT/.git" ]; then
    echo "== リモートから最新を取り込み =="
    # 追跡するリモートブランチが設定されているかを確かめる。設定されていなければ rev-parse が失敗する。
    if git -C "$ROOT" rev-parse --abbrev-ref '@{u}' >/dev/null 2>&1; then
      # ブランチが分岐していて fast-forward できないときは、マージせずに止める。
      if ! git -C "$ROOT" pull --ff-only; then
        echo "fast-forward で取り込めませんでした。手元のブランチをリモートとそろえてから、もう一度実行してください。" >&2
        exit 1
      fi
    else
      echo "追跡するリモートブランチが設定されていないため、取り込みをスキップします。"
    fi
    echo ""
  fi

  echo "== ccnavi (Python) のビルド =="
  # PyInstaller は uv で一時的に追加して使う。
  (cd "$ROOT" && uv run --with pyinstaller python build.py)

  echo ""
  echo "== ccnavi-board (VS Code 拡張機能) のビルド =="
  # ここから先は拡張機能のディレクトリで作業する。
  cd "$EXT_DIR"

  # package.json に書かれている現在のバージョンを読む。
  pkg_version=$(node -p "require('./package.json').version")

  code_cmd=$(find_code)
  if [ -n "${code_cmd}" ]; then
    # インストール済みの拡張機能を「ID@バージョン」の形で一覧し、ccnavi-board の行からバージョンだけを取り出す。
    # インストールされていなければ空になる。
    installed_version=$("$code_cmd" --list-extensions --show-versions 2>/dev/null | grep -i '^local\.ccnavi-board@' | sed 's/.*@//')
  else
    installed_version=""
    echo "code コマンドが見つからないため、インストール済みのバージョンとの比較をスキップします。code の場所は環境変数 CODE で指定できます。"
  fi

  # インストール済みのバージョンが package.json のバージョン以上なら、そのパッチを 1 つ上げた番号を出力する。
  # 上げる必要が無ければ何も出力しない。
  next_version=$(node -e '
// "1.2.3" を [1, 2, 3] に分ける。数字でない部分は 0 とみなす。
const parse = (v) => v.split(".").map((n) => parseInt(n, 10) || 0);
const [pkg, installed] = process.argv.slice(1);
// インストールされていなければ、上げる必要は無い。
if (!installed) process.exit(0);
const p = parse(pkg);
const i = parse(installed);
// メジャー・マイナー・パッチの順に比べ、最初に違った桁で大小を決める。
let cmp = 0;
for (let k = 0; k < 3 && cmp === 0; k++) cmp = (i[k] || 0) - (p[k] || 0);
// インストール済みのほうが古ければ、package.json のバージョンのままでよい。
if (cmp < 0) process.exit(0);
// インストール済みのバージョンのパッチを 1 つ上げた番号を出力する。
console.log(i[0] + "." + i[1] + "." + ((i[2] || 0) + 1));
' "$pkg_version" "$installed_version")

  if [ -n "${next_version}" ]; then
    echo "インストール済みのバージョン（${installed_version}）が package.json のバージョン（${pkg_version}）以上のため、バージョンを ${next_version} に上げます。"
    # package.json のバージョンを書き換える。--no-git-tag-version で、コミットとタグは作らない。
    pnpm version "${next_version}" --no-git-tag-version
    pkg_version="${next_version}"
  fi

  echo "バージョン ${pkg_version} でビルドします。"
  # vsix を作る。出力先はリポジトリ直下の dist/。
  pnpm run package

  # 期待したファイル名で vsix ができているかを確かめる。
  vsix="$ROOT/dist/ccnavi-board-${pkg_version}.vsix"
  if [ ! -f "$vsix" ]; then
    echo "ビルド後の vsix が見つかりません: ${vsix}" >&2
    exit 1
  fi

  # code があればそのままインストールし、無ければ手でインストールするためのコマンドを表示する。
  if [ -n "${code_cmd}" ]; then
    echo ""
    echo "== 拡張機能のインストール =="
    "$code_cmd" --install-extension "$vsix"
  else
    echo ""
    echo "code コマンドが見つからないため、インストールをスキップします。次のコマンドで手動でインストールしてください。"
    echo "  code --install-extension \"${vsix}\""
  fi
}

# exit $? を同じ行に置くのは、実行中にファイルが書き換わっても、この行より後ろを読まずに終わらせるため。
main "$@"; exit $?
