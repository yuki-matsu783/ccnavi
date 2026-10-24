---
type: skill-reference
title: glab 以外の道具の入れ方
description: git・jq・curl・unzip・uv・Node.js と pnpm を、OS とパッケージマネージャーごとに入れる方法
tags: [ccnavi-install, skills]
keywords: [git, jq, curl, unzip, uv, node, pnpm, winget, scoop, brew, apt, dnf, パッケージマネージャー, プロキシ, PATH]
---

# glab 以外の道具

入れるのはユーザ。コマンドを示して打ってもらう。どのパッケージマネージャーが利用できるかは、doctor の `pkg-manager` 行で分かる。
glab は [glab.md](glab.md)。

## 必要な道具

| 道具 | 何に使うか | 要るとき |
|---|---|---|
| git | 取り込み・ブランチ。2.31 以上 | いつも（`git` 行） |
| jq | 設定の読み書きと API の応答の読み取り | いつも（`jq` 行） |
| curl | glab 無しで API に届くとき | glab を使わない代わりの道（`curl` 行） |
| unzip | zip の展開（Linux） | zip を展開するとき |
| uv | 実行ファイルの組み立て | ビルドから（`uv` 行） |
| Node.js と pnpm | 拡張機能の組み立て | ビルドから、かつ `--with-extensions`（`node` 行） |

## 入れ方

| 道具 | Windows（winget） | Windows（scoop） | macOS（brew） | Linux・WSL（apt） | Linux・WSL（dnf） |
|---|---|---|---|---|---|
| git | `winget install --id Git.Git -e` | `scoop install git` | `brew install git` | `sudo apt install git` | `sudo dnf install git` |
| jq | `winget install --id jqlang.jq -e` | `scoop install jq` | `brew install jq` | `sudo apt install jq` | `sudo dnf install jq` |
| curl | Windows 10 以降と Git Bash に最初から入っている | 同左 | 最初から入っている | `sudo apt install curl` | `sudo dnf install curl` |
| unzip | 不要（エクスプローラーで展開） | 不要 | 最初から入っている | `sudo apt install unzip` | `sudo dnf install unzip` |
| uv | `winget install --id astral-sh.uv -e` | `scoop install uv` | `brew install uv` | 下の注を見る | 下の注を見る |
| Node.js | `winget install --id OpenJS.NodeJS.LTS -e` | `scoop install nodejs-lts` | `brew install node` | 下の注を見る | 下の注を見る |

- パッケージマネージャーが無い macOS の git は `xcode-select --install`（コマンドラインツール）でも入る。
- uv（Linux・WSL）: uv の公式の導入手順（https://docs.astral.sh/uv/getting-started/installation/ ）に従う。`pipx install uv` でも入る。
- Node.js（Linux・WSL）: ディストリビューションの版は古いことが多い。Node.js 22 以上を、公式の配布か nvm で入れる。
- pnpm: Node.js を入れたあと、`corepack enable`（Node.js 22 に同梱）か `npm install -g pnpm`。
- パッケージマネージャーも利用できない、管理者権限も無い、外のサイトから取れないときは、社内の配布手順か管理者に頼んでもらう。

## 入れたあとに

- **Claude Code を終了して、新しいターミナルから起動し直してもらう。** Claude Code の Bash ツールの環境（PATH など）は、起動した時点のまま変わらない。
  入れた直後に doctor を打ち直しても、道具は見つからない。
- Windows の jq は、`Program Files` のように**パスに空白が入る**ことがある。ccnavi の sh はパスを引用して扱うので、
  そのまま使える。PATH に足すときは、フォルダ（`jq.exe` のある場所）を足す。
- プロキシの内側では、入れる前に環境変数 `HTTPS_PROXY`（と必要なら `HTTP_PROXY`）を設定する。
  Node.js が外へ出るコマンド（pnpm など）には `NODE_USE_ENV_PROXY=1` も要る。
