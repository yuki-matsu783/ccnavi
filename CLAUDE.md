# CLAUDE.md

## 挨拶と言語

- 日本語でやりとりすること
- 最初の挨拶は自然な日本語で返すこと
- ですますなどの丁寧な口調は不要

### 実行環境

- Windows の Git Bash、Windows の WSL、Claude Code on the web (Linux) の 3 つ。どれでも動くように書く
- 使える道具は `jq` 1.6、Node 22 (pnpm 10)、Python 3.12 (uv)、go。これ以外がある前提で書かない
- Windows と Linux で挙動が変わるところ (パス区切り、改行、シンボリックリンク、大文字小文字) は知見側に明記する

### ソースコード編集方法

- 他セッションでも並行して作業が進められるよう、worktreeを使って作業すること

#### worktree の作り方

- 置き場は `.claude/worktrees/<ブランチ名>`。プロジェクト根の**中**に置く。
  外に出すと、セッションのプロジェクト根・hook の登録・権限の範囲がすべて元の
  ディレクトリを指したままになり、ファイルツールが範囲外になる
- `git worktree add .claude/worktrees/<名前> -b <名前>` で作る。起点は `main` の HEAD
- `.claude/worktrees/` は `.gitignore` に入っている。置き場を変えるなら無視設定も一緒に直す

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

#### 検査は worktree の中で自分で走らせる

`.claude/hooks/lint-py.sh` は `CLAUDE_PROJECT_DIR` へ `cd` してから検査する。
つまり worktree の Python ファイルを編集しても、検査されるのは main であって worktree ではない。
worktree では編集のたびに自分で通す。

```sh
cd .claude/worktrees/<名前>
uv run --with ruff ruff format --check . && uv run --with ruff ruff check . \
  && uv run python -m unittest discover -s tests -t .
```

実行ファイルは lint hook では作り直さない。PyInstaller が 11 秒かかるので外してある。
動かして確かめるときに `uv run --with pyinstaller python build.py` を手で回す。

#### main へ戻す

- worktree のブランチが `main` の HEAD の直上なら fast-forward で入る
- main に他セッションの未コミット変更があると git は fast-forward を拒否する。
  スクラッチパッドへコピー、`git stash push -u`、`git merge --ff-only`、`git stash pop` の順で進め、
  両方の変更が生きていることをビルドとテストで確かめる
- 統合したら `git worktree remove <パス>` と `git branch -d <名前>` で片付ける