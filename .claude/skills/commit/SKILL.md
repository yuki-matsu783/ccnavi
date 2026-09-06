---
name: commit
description: >-
  Create one or more atomic git commits in this repository with a Conventional Commits prefix and a
  one-line Japanese description, after running the Go checks and filtering out credentials and build
  junk. Use whenever a commit is made here, both when the user types /commit and whenever the agent
  commits on its own after finishing a change. Not for writing or fixing the content itself, and not
  for push, branch, or PR operations.
---

# commit

変更内容を分析して Conventional Commits の prefix + 日本語 1 行のメッセージを作り、確認を挟まずコミットまで進める。
このリポジトリでコミットを作るときは、ユーザーが `/commit` と打った場合も、エージェントが作業の締めに自分でコミットする場合も、この手順に従う。

## 絶対ルール

- **フッターを付けない。** `Co-Authored-By` も `Generated with` も書かない。メッセージは 1 行だけ (このリポジトリの決定。ハーネス既定の帰属指示より優先する)
- **`git add .` / `git add -A` を使わない。** 必ずパスを個別指定する
- **`--no-verify` を使わない。** pre-commit が落ちたら原因を直す
- **`git commit --amend` を使わない。** 常に新規コミット
- **失敗しても `git reset` などで自動的に巻き戻さない。** 状況を報告して判断を仰ぐ
- **TodoWrite と Agent ツールを使わない**

## 手順

### 1. 現状を把握する

並列で実行する。

- `git status` (`-uall` は付けない)
- `git diff` と `git diff --cached`
- `git log --oneline -10` (既存のスタイル確認)

コミット対象の決め方。

- **既にステージ済みの変更があるとき** → それが意図された範囲。追加でステージしない。unstaged / untracked が残っていてもユーザーに聞かず対象外にする
- **何もステージされていないとき** → 作業ツリーの変更 (unstaged + untracked) を対象にし、手順 3 のフィルタを通してから個別に `git add` する
- **どちらも空のとき** → 「コミットする変更がありません」と伝えて終了する

### 2. 検査を通す

Go のファイルが変わっているなら、すべて通してからコミットする。error が残っている状態でコミットしない。

```sh
gofmt -l .              # 出力が空であること
go vet ./...
golangci-lint run
go test -count=1 ./...
```

`-count=1` は必須。受入テストが実行ファイルを外から叩くだけで内部パッケージを import しないため、
付けないとキャッシュが内部の変更に気づかず古い結果を返す。

`golangci-lint` が PATH に無い環境では、その 1 つを飛ばして残りを実行し、飛ばした旨をチャットに書く。

`.claude/hooks/lint-go.sh` が PostToolUse に登録されていれば編集のたびに同じ検査が走るが、
hook が無効な環境でも落ちないよう、コミット前に明示的に実行してよい。

### 3. prefix と論理的まとまりを決める

| prefix | このリポジトリでの対象 |
|---|---|
| `feat` | `main.go` / `internal/` への機能追加 |
| `fix` | `main.go` / `internal/` / hook スクリプトのバグ修正 |
| `refactor` | 挙動を変えないコード整理 |
| `test` | `main_test.go` / `*_test.go` / `testdata/` |
| `docs` | `README.md` / `requirements.md` / `ccnavi.md` / `HANDOVER.md` |
| `ai-asset` | `.claude/` 配下 (settings.json / hooks / skills / ccnavi のルール) と `CLAUDE.md`。エージェント向けの指示は docs ではなくこちら |
| `chore` | `.gitignore` / `.golangci.yml` / 雑務 |
| `build` | `go.mod` / `go.sum` |
| `ci` | CI 設定 |
| `perf` | 性能改善 |
| `style` | 意味に影響しない整形 |
| `revert` | 取り消し |

要件書と仕様書はどちらも `docs` だが、**外から観測できる約束 (`requirements.md`) と実装の理屈 (`ccnavi.md`) は別の主題**なので、
同時に変えたときは分けることを検討する。

prefix が変わるか、扱っている主題が別なら別コミットに分ける。**説明が 1 行に収まらないと感じたら、それはコミットを分ける合図**。

### 4. ファイルをフィルタする

`git add` の対象から自動的に除外する。除外にユーザーの確認は要らない。

**クレデンシャル (絶対に除外)**
`.env` / `.env.*` / `*.pem` / `*.key` / `*.p12` / `*.pfx` / `*.ppk` / `credentials.json` / `service-account*.json` / `id_rsa` / `id_ed25519` / `id_ecdsa` / `.aws/credentials` / `.netrc` / `secrets.yml` / `secrets.yaml`

**開発環境の副産物**
`.DS_Store` / `Thumbs.db` / `desktop.ini` / `*.swp` / `*.swo` / `*~` / `*.log` / `tmp/` / `*.tmp` / `*.tmp.*` / `*.bak` / `*.orig` / `*.stackdump` (Git Bash のクラッシュダンプ) / `.claude/settings.local.json`

**このリポジトリ固有の生成物**
`ccnavi` / `ccnavi.exe` / `ccnavi.new.exe` / `ccnavi.old.exe` (ビルド成果物。lint hook が差し替えのために `.new` と `.old` を作る) /
`*.jsonl` (ccnavi の判定記録。絶対パスとコマンド全文が入る) / `knowledge/` (参照専用の外部資料) / `dist/`

多くは `.gitignore` にも入っているが、**この一覧が最後の砦**。`.gitignore` に無い新種の副産物を見つけたら、この一覧と `.gitignore` の両方に足す。

**削除されたファイルは除外対象ではない。** `git status` で `D` になっているパスも、他と同じように `--` の後ろに並べて `git add` してよい。

除外したファイルがあれば、コミット前にチャットへ列挙する。

### 5. コミットする

**承認待ちをしない。** AskUserQuestion を挟まず、そのまま実行する。実行前に、メッセージ (複数なら分割案) と除外したファイルをチャット本文に書く。透明性のためであって確認のためではない。

```sh
git add -- <file1> <file2>
git commit -m "<prefix>: <日本語の説明>"
```

複数コミットに分けるときは、この 2 コマンドを組ごとに順番に繰り返す。分割案の書き方。

```
コミット1: feat: PreToolUse のルール照合と判定の記録を追加
  - main.go
  - internal/cli/cli.go
  - internal/rules/rules.go
コミット2: docs: モードの呼び名を判定しない・警告・ブロックに統一
  - requirements.md
  - README.md
```

## このリポジトリでの注意

- **ccnavi 自身が PreToolUse に登録されている。** `git push` や `git reset --hard` を含むコマンドは、
  ブロックモードなら自分の hook に止められる。**止められても迂回しない。** 別の道具に切り替えるか、原因を直す
- **禁止語を含む文字列を Bash のコマンドに書くと、それだけで判定に当たる。** 部分一致のため。
  ルールの動作確認をしたいときは、payload をファイルに書いてから読み込ませる
- **`.claude/` 配下への書き込みは、ccnavi のルールで警告が出る。** 意図した変更なら気にしなくてよい

## 失敗したとき

- **pre-commit が落ちた** → 出力をそのまま見せ、原因を直してから新規コミットを作る。`--amend` も `--no-verify` も使わない
- **複数コミットの途中で落ちた** → そこで停止する。`git status` を出して、どこまで完了したかを報告する。自動で巻き戻さない
- **ccnavi の hook に止められた** → 迂回せず、何に当たったかを報告する。`.claude/ccnavi/log.jsonl` に判定が 1 行残っているので、当たったルールの id が分かる
