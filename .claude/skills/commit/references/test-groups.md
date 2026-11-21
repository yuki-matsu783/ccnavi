---
type: skill-reference
title: テストの置き場と、回す範囲の決め方
description: ccnavi のテスト（Go）の置き場と、変更したファイルからどのパッケージを回すか決める基準
tags: [process-test, skills]
keywords: [テスト, go test, clitest, harness, contract, scenarios, e2e]
---

# テストの置き場と、回す範囲の決め方

## 置き場

テストは全部 Go（`go/`）にある。

| 置き場 | 主題 |
|---|---|
| `go/internal/<層>/<パッケージ>/` の `_test.go` | 部品の単体（判定・設定・チケット・基盤） |
| `go/internal/clitest/core/` | 実行ファイルの外から見える約束（`--lint`・`--test`・文面・ビルド・hook の入口） |
| `go/internal/clitest/repo/` | リポジトリそのものの決まり（ADR の番号・配る sh の書き方・期待する判定・Stop の場面） |
| `go/internal/clitest/config/`・`guard/`・`ticket/` | 設定の統合・判定・チケットの流れ |
| `go/internal/clitest/sh/` | 配る sh を通しで回す |
| `go/internal/clitest/contract/` | 本物から録った応答の形と、模擬・スタブの比べ |
| `go/internal/clitest/scenarios/`・`e2e/`・`manual/` | 受入シナリオ・組み立て済みの配布物・手動の道具の入口 |
| `go/internal/harness/` | テストの道具（偽の GitLab・claude のスタブ・sh の作業木など）。自身の試験も持つ |

```sh
cd go && go test ./...        # 全件
cd go && go test ./internal/clitest/sh/git/   # 1 パッケージ
```

理由と手順は[テストを回す](../../../../.agents/docs/testing.md)。

実行ファイル（`.ccnavi/bin/`、無ければ PATH の `ccnavi`）が要るテストは、`harness/exe` が `go build` で組む。組み立て済みの `dist/ccnavi` と配る sh を外から呼ぶ e2e は、`dist/ccnavi` が無ければ skip する（約 3 秒）。

## 回すもの

`go/`・`.ccnavi/`・`tests/`・`scripts/` のどれかを変えたら `cd go && gofmt -l . && go vet ./... && go test ./...` を commit の前に回す。sh・文面・見本の変更は Go のテストが守る。`-count=1` は要らない（clitest は go test が結果を保存しない）。

`docs/30_adr/`・`.claude/hooks/`・`scripts/` を変えたときは、少なくとも `go test ./internal/clitest/repo/` を回す（ADR の番号・配る sh の書き方・期待する判定・Stop の場面を見る）。

| 変えたもの | 足すもの |
|---|---|
| `scripts/ccnavi-build.sh` | `go test ./internal/clitest/core/`（`build_test.go`）`go test ./internal/clitest/e2e/` |
| `.ccnavi/scripts/` の sh・`scripts/ccnavi-setup.sh`、sh が実行ファイルに聞く部品（`go/internal/tickets/lifecycle/` ほか） | `go test ./internal/clitest/sh/... ./internal/clitest/e2e/` |
| `.claude/hooks/`（`test-go.sh`・`lint-go.sh`・`mark-ext.sh`・`test-ext.sh`） | `go test ./internal/clitest/core/`（`devhooks_test.go`）`go test ./internal/clitest/repo/ ./internal/clitest/e2e/` |
| `.claude/hooks/check-adr.sh` | `go test ./internal/clitest/core/`（`checkadrhook_test.go`） |
| `go/internal/harness/hostfixture`・`hoststub`・`fakegitlab`・`fakeclaude`・`tests/contract/` | `go test ./internal/clitest/contract/... ./internal/harness/...` |
| `go/internal/harness/`・`tests/scenarios/`（受入シナリオの実行器・スタブ・シナリオの yml） | `go test ./internal/harness/... ./internal/clitest/scenarios/` |
| `extensions/vscode/` | `go test ./internal/clitest/core/`（`testjson_test.go`）。拡張の分は下 |
| `extensions/vscode/ccnavi-board/scripts/test-groups.js` | その `pnpm test`（`test/shared/test-groups.test.ts`） |
| `extensions/chrome/ccnavi-approval/` | その `pnpm test`（下） |
| `docs/30_adr/`・`docs/30_adr.md`（枚を足す・番号を動かす） | `go test ./internal/clitest/repo/`（`adr_numbers_test.go`） |
| そのほかのドキュメントだけ（`*.md`・`docs/`） | 回さない |

統合先に取り込む前・MR に出す前は、`go test ./...` を全件回す。

## 拡張（`extensions/vscode/ccnavi-board`）のグループ

拡張側にも同じ分け方がある（`test/<グループ>/`。board / rules / risk / phases / projects / flow / workflow / templates / handoff / shared / devtools）。
こちらは表を引かない。変えたファイルを渡せば、関わるグループだけが回る。

```sh
cd extensions/vscode/ccnavi-board
pnpm test:plan src/core/rules-doc.ts   # 何を回すかだけ見る
pnpm test:for src/core/rules-doc.ts    # 回す
pnpm test                              # 全部
```

コンパイルは 1 回なので、変えたファイルは 1 回でまとめて渡す（`pnpm test:for a.ts b.ts`）。

## Chrome 拡張（`extensions/chrome/ccnavi-approval`）

グループに分けていない。拡張全体が 1 つのグループで、触ったら全部を回す。

```sh
cd extensions/chrome/ccnavi-approval
pnpm test        # 組み立て → 単体・WASM（Node）・手元向けの同じ入口との突き合わせ（node scripts/test.js）
pnpm test:e2e    # 拡張を読み込んだ Chromium での試験。Chromium が要る
```

ターンの終わりの hook（`.claude/hooks/test-ext.sh`）は、VS Code の拡張と同じく触ったときだけ回す。
回すのは `node scripts/test.js` だけで、`--e2e` は回さない。読み物（`*.md`・`docs/`）だけを触ったターンは回さない。
