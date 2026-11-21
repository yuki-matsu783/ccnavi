---
type: guide
title: 開発用の hook
description: ccnavi 自身の開発で使う hook（編集時の Go の整形、ターンの終わりの Go のテスト、ADR の番号の検査、拡張のテスト）の役割、登録の有無、settings.local.json への足し方
tags: [development, testing]
keywords: [開発用 hook, lint-go, test-go, gofmt, go vet, go test, mark-ext, test-ext, check-adr, PostToolUse, Stop, settings.local.json, 差し戻し, test-groups.js, clitest]
---

# 開発用の hook

ccnavi のリポジトリ自身の開発に使う hook は `.claude/hooks/` にある。配布しない。ccnavi が配布先へ登録する hook とは別物。
どのテストをどの順で回すかの決まりは[テストを回す](testing.md)にある。

| hook | 契機 | 役割 | `.claude/settings.json` |
|---|---|---|---|
| `lint-go.sh` | `PostToolUse`（`Write\|Edit\|MultiEdit\|NotebookEdit`） | 編集した `.go` のファイルの整形（gofmt）と、`go/`・`.ccnavi/`・`docs/`・`wip/`・`.claude/`・`scripts/` を触った木の書き残し | 登録済み |
| `check-adr.sh` | `PostToolUse`（`Write\|Edit\|MultiEdit\|NotebookEdit`） | ADR の番号の重なりと置き場の間違いを、書いた直後に止める | 登録済み |
| `test-go.sh` | `Stop` | ターンの終わりに、触った木の Go の検査とテストを 1 回回す | 未登録 |
| `mark-ext.sh` | `PostToolUse`（`Write\|Edit`） | 拡張のファイルを触ったことの書き残し | 未登録 |
| `test-ext.sh` | `Stop` | ターンの終わりに、触った拡張のファイルに関わるテストのグループだけ回す | 未登録 |

未登録のものを回すなら、ユーザごとの `.claude/settings.local.json`（追跡しない）に足す。
hook の振る舞いは `go/internal/clitest/core/devhooks_test.go`（`check-adr.sh` は `checkadrhook_test.go`）が確かめる。

## Go の整形・検査とテスト

`lint-go.sh` は、編集した `.go` のファイルを `gofmt -l` で見る。テストは走らせない。
編集していないファイルの指摘は出ない。他のファイルを壊す変更（定義の削除で別ファイルの参照が壊れる等）はその場では出ず、ターンの終わりの `test-go.sh` が失敗すれば、そこで見つかる。
あわせて、`go/`・`.ccnavi/`・`docs/`・`wip/`・`.claude/`・`scripts/` のどれかを触ったら、そのファイルから上へ辿って `go/go.mod` を持つ木（ワークツリーかリポジトリの根）を `logs/session/<セッション>.go-trees` に書き残す。ターンで触ったのが Go と文書の定義だけでも、試験が決まりを見張っているため。

`test-go.sh` は、ターンの終わりに 1 回だけ、書き残した木の `go/` で次を順に回す。

1. `gofmt -l .`（空でなければ落ちる）
2. `go vet ./...` と `GOOS=windows go vet ./...`（OS で分けたファイルは、この機械の vet だけでは読まれない）
3. `go test $(go list ./... | grep -v /clitest/)`（`clitest` 以外の全部）
4. `go test ./internal/clitest/repo/`（リポジトリの決まりを見る試験）

`-count=1` は付けない。入力が変わっていない試験は、go の結果の保存が効く。
`clitest` の残り（sh や GitLab の模擬を起こす重いもの）と Go の試験全体は、commit スキルがコミット前に回す。この hook では回さない。
何も触っていないターン、`go` が無い機械では何もしない。

失敗していたら最初に落ちた段で差し戻すが、差し戻しは 3 回まで。上限に達したら止めて、判断をユーザへ返す。回数は `logs/session/<セッション>.retries` に数える。
1 時間を超えた他のセッションの記録は掃除する。自分のセッションの記録は、1 時間を超えたターンでも読む。

## 拡張のテスト

拡張（VS Code の `extensions/vscode/ccnavi-board`、Chrome の `extensions/chrome/ccnavi-approval`、共通の `extensions/shared`）のテストも同じ形で回す。`mark-ext.sh` が、拡張のファイルを触ったら `logs/session/<セッション>.ext-files` に書き残し、`test-ext.sh` がターンの終わりに 1 回回す。
回すのは触ったファイルが関わるグループだけで、決めるのは `extensions/vscode/ccnavi-board/scripts/test-groups.js`。テストの `import` を辿り、辿れないものだけをパスの規則で補う。拡張を触っていないターンは何もしない。
差し戻しは 3 回までで、回数は `test-go.sh` と別に数える。

`settings.local.json` には次のように足す。

```json
"PostToolUse": [
  { "matcher": "Write|Edit",
    "hooks": [{ "type": "command", "command": "sh \"${CLAUDE_PROJECT_DIR}/.claude/hooks/mark-ext.sh\"", "timeout": 10 }] }
],
"Stop": [
  { "matcher": "",
    "hooks": [{ "type": "command", "command": "sh \"${CLAUDE_PROJECT_DIR}/.claude/hooks/test-ext.sh\"", "timeout": 180 }] }
]
```

`test-go.sh` を回すときは、同じ形で `Stop` に足す。

## 気づけないこと

- 気づけるのは「同じ機械で 1 回回して失敗すること」だけ。混み具合で結果が変わる失敗（React の描き直しを待つテストなど）は見つけられない。CI も繰り返しの仕掛けも無い
- Bash で変えたもの（`sed -i`、`merge` で入った変更）は書き残されないので回らない
- 実行ファイルはどの hook でも作り直さない。確かめるときに手で組み立てる（[組み立てと配布物](build-and-dist.md)）
