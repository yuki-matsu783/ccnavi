---
type: guide
title: 実行環境とADRの扱い
description: ccnaviの開発で対応する実行環境（Windows、macOS、Linux）と、sh・Pythonを書くときの決まり、実測で分かった環境固有の落とし穴、文書やコメントでのADRへの触れ方
tags: [sh-scripts, environment]
keywords: [落とし穴, uv, jq, CP932, Docker, PyInstaller, gitdir, 大文字小文字, casefold, normcase, pathcase, 実行環境, 環境, Windows, macOS, Linux, Git Bash, WSL, sh, bash, BSD, ADR, ADR番号, コメント]
---

# 実行環境とADRの扱い

## 実行環境

- 対応する実行環境は、WindowsのGit Bash、WindowsのWSL、Claude Code on the web（Linux）、macOSの4つ。shはどの環境でも動くように書く
- macOSの`sh`はbash 3.2で、`sed`などのコマンドはBSD版。変数のすぐ後ろに全角文字を続けるときは`${var}`と括る。
  `$( )`の中には`case`を書かない。この2点は`tests/core/test_sh_portability.py`が検査する
- shでもPythonでも、ヒアドキュメント（`<<`）でファイルや入力を作らない。複数行のPythonによる書き換えや追記は、
  Writeでスクリプトをファイルに置いてから、そのファイルを実行する（`python3 <パス>`）。
  空のファイルを作るだけのときも同じで、Writeで作る。
  ヒアドキュメントで書いたファイルは、Write / Editに掛かる権限の宣言も、編集後の検査も通らないため
- 使える道具は`jq` 1.6、Node 22（pnpm 10）、Python 3.12（uv）、goだけ。これ以外の道具がある前提で書かない。
  例外は、ホスト（GitHub・GitLab）と話すsh（`ccnavi-review.sh`・`ccnavi-branches.sh`・`ccnavi-start.sh`など）で、
  `gh`・`glab`・`curl`を使う。無ければ何を用意すればよいかを案内して止まる。ソースで動かしているccnaviのリポジトリでは、
  shが実行ファイルの代わりに`uv run python -m ccnavi`で起こすことがある（`uv`が無ければその道は使わない）
- Claude Code on the webには`.ccnavi/bin`が無い。ccnaviのhookを動かすには、
  `uv run --with pyinstaller python build.py`でビルドする。ビルドしていない（PATHにも`ccnavi`が無い）とランチャーが終了コード127で終わり、
  hookは何もしない

## 実測で分かった環境固有の落とし穴

Claude Codeの振る舞いについて測った前提は設計書の付録C（[appendix.md](../21_design/appendix.md)）にある。ここには環境ごとの落とし穴を置く。

- **ワークツリーが消せない（Windows）。** uvのハードリンクで`.venv`の`_yaml.*.pyd`が全ツリーで同じ実体になり、
  誰かのテストが読み込んでいる間は消せない。`pyproject.toml`の`link-mode = "copy"`で分けてあるが、
  ハードリンクで作った`.venv`はそのままなので、テストが走っていないときに作り直す。自分のテストが走っている間は残る。
  失敗したら残ったディレクトリを`mv`で`.claude/worktrees/`の外へ出して`git worktree prune`。
  先に`sh .ccnavi/scripts/ccnavi-clean.sh <名前>`で生成物を消すとnode_modulesで止まる分は避けられる
- **PyInstallerは指定したスクリプトをパッケージの外で走らせる。** 相対importが解けないので、絶対importの`main.py`を渡す
- **Pythonの識別子に空白は入らない。** `def test_warn は…`のように英字と日本語の間に空白を入れると構文エラー
- **Windowsのコンソール経由で日本語を引数に渡すとCP932になり、`jq --arg`がUTF-8でないJSONを作る。** 本文はファイルで渡す。
  `jq`の実体は`C:\Program Files\jq\jq`で、`"$JQ"`と引用しないと空白で分かれる
- **Windowsの`gitdir:`の表記**（git 2.39.2、Git BashとPowerShell）。絶対パス、区切りは`/`、ドライブレターは大文字、
  `gitdir:`の後ろは半角空白1個。`ccnavi_project`（sh）と`tree.py`がこれを前提にしている
- **Docker Desktopを起動すると`restart=unless-stopped`のGitLabが勝手に上がり、2GBのVMではengineごと異常終了する。**
  GitLab CEには4GB要る
- **macOSの`/etc`・`/tmp`・`/var`は`/private`へのリンク。** `os.path.realpath`で実体化すると先頭に`private`が付く。
  実体化する関数（`tree.parts_under()`など）のテストに実在のリンクを渡さない。存在しないパスか、`realpath`を通した一時ディレクトリで組む
- **macOSのbashは`~/.bash_profile`、Linuxのbashは`~/.bashrc`を読む。** 導入スクリプトの案内は、bashだけ`uname -s`で分ける（zshは`~/.zshrc`）。
  テストは`uname`の代役をPATHの先頭に置き、どのOSで回しても両方の分岐を通す（`tests/sh/test_setup.py`の`fake_uname`）
- `dist/`、`build/`、`logs/`はgit管理外。記録には絶対パスとコマンド全文が入るのでコミットしない

## パスと名前の大文字小文字

大文字小文字だけが違うパス・名前は同じものとして扱い、両立させない。Windows と macOS の既定のファイルシステムは
区別しないので、Linux だけ区別する書き方をすると、Windows で clone したときにだけ判定が割れる。

- 比べるときは、どの機械でも区別しない。パスは`pathcase.fold()`（`src/ccnavi/infra/pathcase.py`）を通す。
  名前だけなら`.casefold()`でもよい。`os.path.normcase`を単独で使わない（Linux では何もしない）
- `fold()`の結果は比べるための文字列で、ファイルを開く表記には使わない。開く・表示する・書き戻すときは元の表記のまま使う。
  比べて残りの名前を取り出すときは`tree.parts_under()`を使う（小文字にしたパスから切り出さない）
- ワークツリーの名前は`tree.worktree_path()`で引く。在るディレクトリの実際の表記で返る。
  大文字小文字だけ違う別のワークツリー・識別子・ブランチを作らない。作ろうとしたら拒否または警告する
- テストで「大文字小文字だけ違うブランチ」をリモートに置くときは、`refs/heads/`の下のファイル（loose ref）にしない。
  区別しないFSでは同じファイルになって置けない。bareの`packed-refs`に2行並べる（`tests/sh/test_sync_sh.py`の`add_ref_differing_only_in_case`）
- 「表記まで同じであること」を求める判定を新しく足さない
- sh の`case`でパスを比べるときも、同じ場所の別の綴りを通すつもりで書く。ccnavi が自分で組んだ
  `<root>/.claude/worktrees/*`のようなパスは、綴りが決まっているので`case`のままでよい
- 作る側の検査（`ccnavi-git.sh worktree add`の作成先など）は、綴りが違うパスを通さず拒否する。通すと Linux で別のディレクトリができ、
  両立させないに反する
- 例外は、git が追跡する綴りに依る名前。状態のディレクトリ（`todo` `doing` `review` `done`）は綴りまで求め、
  違う綴りは読まずに警告する。ブランチ名は git が区別するので、そのまま比べる。`.gitignore`の`/scratchpad`も git が
  区別する。例外を足すときはこの節に書く（`wip/`の置き場は例外ではない。区別しない）

## 文書とコメントでのADRの扱い

- 文書・コメント・ユーザに見える文言（print、拒否文、`rules.yml`の`message`、lintの文言）では、ADR番号で言及しない。
  決めた中身と理由をその場で書く。読み手がADRを開かなくても、なぜそうなっているかが分かるようにする
- ADRの中の節番号や決定の記号（`D11`、`段階 2c`など）も同じ扱いにする
- 番号を使うのは、ADR同士の置き換え関係を書くとき（`docs/30_adr/`の中で「ADR-NNNNの置き場を置き換える」と書くなど）だけ
- 番号だけでは、読み手はADRを開くまで意味が分からない。ADRが後で改められても、番号を引いた側との食い違いに気づけない
