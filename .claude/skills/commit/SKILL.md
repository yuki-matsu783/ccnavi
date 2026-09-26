---
name: commit
description: >-
  Create one or more atomic git commits in this repository with a Conventional Commits prefix and a
  one-line Japanese description, after running the Python checks and filtering out credentials and
  build junk. Use whenever a commit is made here, both when the user types /commit and whenever the
  agent commits on its own after finishing a change. Not for writing or fixing the content itself,
  and not for push, branch, or PR operations.
---

# commit

変更を分析して Conventional Commits の prefix + 日本語 1 行のメッセージを作り、確認を挟まずコミットまで進める。
利用者が `/commit` と打った場合も、エージェントが作業の締めに自分でコミットする場合も、この手順に従う。

## 絶対ルール

- フッターを付けない。`Co-Authored-By` も `Generated with` も書かない。メッセージは 1 行だけ（このリポジトリの決定。ハーネス既定の帰属指示より優先する）
- `git add .` / `git add -A` を使わない。必ずパスを個別指定する
- `--no-verify` を使わない。pre-commit が落ちたら原因を直す
- `git commit --amend` を使わない。常に新規コミット
- 失敗しても `git reset` などで自動的に巻き戻さない。状況を報告して判断を仰ぐ
- TodoWrite と Agent ツールを使わない

## 手順

### 1. 現状を把握する

並列で実行する: `git status`（`-uall` は付けない）、`git diff` と `git diff --cached`、`git log --oneline -10`。

- 既にステージ済みの変更があるとき → それが対象。追加でステージせず、unstaged / untracked は聞かずに対象外にする
- 何もステージされていないとき → 作業ツリーの変更（unstaged + untracked）を手順 4 のフィルタに通してから個別に `git add` する
- どちらも空のとき → 「コミットする変更がありません」と伝えて終了する

### 2. 検査を通す

Python のファイルが変わっているなら、すべて通してからコミットする。error が残ったままコミットしない。

```sh
uv run --with ruff ruff format --check .
uv run --with ruff ruff check .
uv run python -m unittest discover -s tests/<グループ> -t .
```

回すグループは、変えたファイルを [references/test-groups.md](references/test-groups.md) の表に当てて決める。
表に当たらない変更や、統合先へ戻す前・MR に出す前は全件（`discover -s tests -t .`）を回す。
worktree で作業しているときは、そのツリーの中で実行する（`pyproject.toml` はツリーごと）。

拡張（`vscode-extension/ccnavi-board`）のファイルが変わっているなら、そのぶんも通す。

```sh
cd vscode-extension/ccnavi-board
pnpm install --frozen-lockfile                        # node_modules が無いときだけ
pnpm test:for src/core/rules-doc.ts src/webview/board/App.tsx   # 変えたファイルをまとめて渡す
pnpm test                                             # 統合先へ戻す前・MR に出す前
```

hook（`lint-py.sh` `test-py.sh` `mark-ext.sh` `test-ext.sh`）が同じ検査を走らせていても、コミット前に明示的に実行してよい。
実行ファイル（PyInstaller）はここでは作り直さない。要るときは `uv run --with pyinstaller python build.py` を手で回す。

### 3. prefix と論理的まとまりを決める

| prefix | このリポジトリでの対象 |
|---|---|
| `feat` | `main.py` / `ccnavi/` への機能追加 |
| `fix` | `main.py` / `ccnavi/` / hook スクリプトのバグ修正 |
| `refactor` | 挙動を変えないコード整理 |
| `test` | `tests/*.py` / `tests/fixtures/` |
| `docs` | `README.md` / `requirements.md` / `ccnavi.md` / `HANDOVER.md` |
| `ai-asset` | `.claude/` 配下（settings.json / hooks / skills / ccnavi のルール）と `CLAUDE.md`・`docs/claude/`。エージェント向けの指示は docs ではなくこちら |
| `chore` | `.gitignore` / 雑務 |
| `build` | `pyproject.toml` / `uv.lock` |
| `ci` | CI 設定 |
| `perf` | 性能改善 |
| `style` | 意味に影響しない整形 |
| `revert` | 取り消し |

prefix か主題が別なら別コミットに分ける。`requirements.md`（外から観測できる約束）と `ccnavi.md`（実装の理屈）は
別の主題として扱う。説明が 1 行に収まらないなら分ける。

### 4. ファイルをフィルタする

`git add` の対象から自動的に除外する（確認は要らない）。

- クレデンシャル（必ず除外）: `.env` / `.env.*` / `*.pem` / `*.key` / `*.p12` / `*.pfx` / `*.ppk` / `credentials.json` / `service-account*.json` / `id_rsa` / `id_ed25519` / `id_ecdsa` / `.aws/credentials` / `.netrc` / `secrets.yml` / `secrets.yaml`
- 開発環境の副産物: `.DS_Store` / `Thumbs.db` / `desktop.ini` / `*.swp` / `*.swo` / `*~` / `*.log` / `scratchpad/` / `tmp/` / `*.tmp` / `*.tmp.*` / `*.bak` / `*.orig` / `*.stackdump` / `.claude/settings.local.json`
- このリポジトリの生成物: `dist/` / `build/` / `__pycache__/` / `*.pyc` / `.venv/` / `.ruff_cache/` / `*.jsonl` / `logs/`（判定記録。絶対パスとコマンド全文が入る） / `knowledge/`（参照専用の外部資料）

`.gitignore` に無い新種の副産物を見つけたら、この一覧と `.gitignore` の両方に足す。
削除されたファイル（`git status` の `D`）は除外対象ではなく、他と同じく `git add --` に並べてよい。
除外したファイルがあれば、コミット前にチャットへ列挙する。

### 5. コミットする

承認待ちをしない（AskUserQuestion を挟まない）。実行前に、メッセージ（複数なら分割案）と除外したファイルをチャット本文に書く。

```sh
git add -- <file1> <file2>
git commit -m "<prefix>: <日本語の説明>"
```

複数コミットに分けるときは、この 2 コマンドを組ごとに繰り返す。分割案の書き方:

```
コミット1: feat: PreToolUse のルール照合と判定の記録を追加
  - main.py
  - ccnavi/rules.py
コミット2: docs: モードの呼び名を判定しない・警告・ブロックに統一
  - requirements.md
```

## このリポジトリでの注意

- ccnavi 自身が PreToolUse に登録されていて、`git push` や `git reset --hard` を含むコマンドは自分の hook に止められうる。止められても迂回しない。別の道具に切り替えるか、原因を直す
- 禁止語を含む文字列を Bash のコマンドに書くと、それだけで判定に当たる（部分一致）。ルールの動作確認は payload をファイルに書いてから読み込ませる
- `.claude/` 配下への書き込みには ccnavi のルールで警告が出る。意図した変更なら気にしなくてよい

## 失敗したとき

- pre-commit が落ちた → 出力をそのまま見せ、原因を直してから新規コミットを作る
- 複数コミットの途中で落ちた → そこで停止し、`git status` を出してどこまで完了したかを報告する
- ccnavi の hook に止められた → 迂回せず、何に当たったかを報告する。`logs/log.jsonl` の判定の行に当たったルールの id がある
