# CLAUDE.md

## 挨拶と言語

- 日本語でやりとりすること
- 最初の挨拶は自然な日本語で返すこと
- ですますなどの丁寧な口調は不要

### 実行環境

- Windows の Git Bash、Windows の WSL、Claude Code on the web (Linux) の 3 つ。どれでも動くように書く
- 使える道具は `jq` 1.6、Node 22 (pnpm 10)、Python 3.12 (uv)、go。これ以外がある前提で書かない
- Windows と Linux で挙動が変わるところ (パス区切り、改行、シンボリックリンク、大文字小文字) は知見側に明記する

### 呼び名

- **ワークスペースルート** = Claude Code を起動した場所（`CLAUDE_PROJECT_DIR`）
- **git プロジェクトルート** = `.git` がある場所。ワークスペース自身、`projects/` の下の各プロジェクト、
  作業ツリーのそれぞれが持つ
- 「プロジェクト根」「プロジェクトルート」「main の根」とは書かない。文書とコードの全体でこの 2 つに揃える

### ソースコード編集方法

- 他セッションでも並行して作業が進められるよう、worktreeを使って作業すること
- main の作業ツリーでの Write / Edit はルール（`main-tree`）が止める。編集は必ず worktree の中で
  行う。worktree の中でも `.claude/settings.json`・hook・スクリプト・写しは触らない

#### worktree の作り方

- 置き場は `.claude/worktrees/<ブランチ名>`。ワークスペースルートの**中**に置く。
  外に出すと、セッションのワークスペースルート・hook の登録・権限の範囲がすべて元の
  ディレクトリを指したままになり、ファイルツールが範囲外になる
- `git worktree add .claude/worktrees/<名前> -b <名前>` で作る。起点は `main` の HEAD
- `.claude/worktrees/` は `.gitignore` に入っている。置き場を変えるなら無視設定も一緒に直す
- 作業が完了したらmainにマージする。コンフリクトして判断に困る場合はユーザに確認する

#### 他セッションの作業を踏まないために

- **main の作業ツリーに未コミットの変更があったら、それは他セッションのものとして扱う。**
  自分のものと混ざっているなら、自分の分だけを取り除いて main を元の状態に戻し、
  worktree に移してから続ける
- 他セッションの書きかけを、コミットも `git checkout` も `git reset` もしない。
  ビルドが壊れていても直さない。何が壊れているかを報告して判断を仰ぐ。
  **これはサブエージェント側の規則。** 差し向けたメイン側は、サブエージェントの
  worktree とその成果物を直してよい。マージの衝突を解くのはメインの仕事で、
  そこを触れないとブランチが永久に統合されない
- 退避が要るときは `git stash push -u` を使う。消す前にスクラッチパッドへコピーも取る。
  stash は衝突しても消えないので、戻せる形が残る

#### 検査は編集したツリーで走る

hook は編集したファイルから、いちばん近い `pyproject.toml` を上に辿ってツリーを決める。
worktree の中を直せば worktree が検査される。手で走らせ直す必要はない。

- `PostToolUse` の `lint-py.sh` が、そのファイルのツリーで整形と検査をかける
- `Stop` の `test-py.sh` が、ターンの終わりに、そのターンで触ったツリーのテストを走らせる。
  触っていないツリーは巻き添えにしない。他セッションの書きかけでこちらが差し戻されないように

手で確かめたいときは同じことをこう書く。

```sh
cd .claude/worktrees/<名前>
uv run --with ruff ruff format --check . && uv run --with ruff ruff check . \
  && uv run python -m unittest discover -s tests -t .
```

実行ファイルはどちらの hook でも作り直さない。PyInstaller が 11 秒かかるので外してある。
動かして確かめるときに `uv run --with pyinstaller python build.py` を手で回す。

#### main へ戻す

- worktree のブランチが `main` の HEAD の直上なら fast-forward で入る
- main に他セッションの未コミット変更があると git は fast-forward を拒否する。
  スクラッチパッドへコピー、`git stash push -u`、`git merge --ff-only`、`git stash pop` の順で進め、
  両方の変更が生きていることをビルドとテストで確かめる
- 統合したら `git worktree remove <パス>` と `git branch -d <名前>` で片付ける

## 実行ファイルの境界（設計 §4 P11）

- ccnavi の実行ファイルは**ネットワークに出ない**。自分で見て判断するのは、プロジェクトの
  ディレクトリの中で把握できるもの（作業ツリー、git、承認済みの写し、印、hook の payload）だけ
- その外にあるもの（マージリクエスト、レビューのスレッド、ホストの API、認証）は
  `.claude/scripts/` の sh が取ってきて、写し（JSON）で `--result` に渡す。実行ファイルが持つのは
  写しを読んでチケットと印を動かすところだけ
- 実行ファイルに「外を見に行く」コードを足さない。足したくなったら sh の仕事に分け、
  実行ファイルには「その結果をどう読むか」だけを足す。写しの形が sh と実行ファイルの契約で、
  テストは sh の代わりに写しを渡す