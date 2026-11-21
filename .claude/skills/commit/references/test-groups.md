---
type: skill-reference
title: テストのグループと回すグループの決め方
description: ccnavi のテストを複数のグループに分けて管理する方法と、変更時にどのグループを実行すべきか決める基準
tags: [process-test, skills]
keywords: [テストグループ, tests, core, scenarios, e2e]
---

# テストのグループと、回すグループの決め方

## グループ

Python のテストは `tests/<グループ>/` に置く。残っているのは 2 つだけで、判定・設定・チケット・sh は Go（`go/internal/clitest/`）が守る。

| グループ | 主題 | 時間 |
|---|---|---|
| `core` | 部品の単体と、速い受入テスト | 約 13 秒 |
| `contract` | 本物から録った応答の形と、模擬・スタブの比べ（`tests/contract/`） | 約 15 秒 |

```sh
uv run python -m tests    # 全件（同時実行）。`discover -s tests` の 1 本は使わない
```

理由と手順は[テストを回す](../../../../.agents/docs/testing.md)。

実行ファイル（`.ccnavi/bin/`、無ければ PATH の `ccnavi`）が無い環境（Claude Code on the web、ビルド前）では、ランチャー経由の `--lint` と `ccnavi` コマンドが動かない。
`sh scripts/ccnavi-build.sh` で組み立ててから回す。

## 回すもの

Python か補助（`tests/` の下）を変えたら `uv run python -m tests`。

Go のテスト（`cd go && go test ./...`）は、`go/`・`.ccnavi/`・`tests/` のどれかを変えたら必ず回す。sh・文面・見本の変更は Go のテストが守る。`docs/30_adr/`・`.claude/hooks/`・`scripts/` を変えたときも `cd go && go test ./internal/clitest/repo/` を回す（ADR の番号・配る sh の書き方・期待する判定・Stop の場面を見る）。`-count=1` は要らない。Go の e2e（`go test ./internal/clitest/e2e/`）は組み立て済みの `dist/ccnavi` と配る sh を外から呼ぶ。`dist/ccnavi` が無ければ skip（約 3 秒）。

| 変えたもの | 足すもの |
|---|---|
| `scripts/ccnavi-build.sh` | `go test ./internal/clitest/core/`（`build_test.go`）`go test ./internal/clitest/e2e/` |
| `.ccnavi/scripts/` の sh・`scripts/ccnavi-setup.sh`、sh が実行ファイルに聞く部品（`go/internal/tickets/lifecycle/` ほか） | `go test ./internal/clitest/e2e/` |
| `.claude/hooks/test-py.sh` | `uv run python -m tests`（`core` の `test_test_py_hook`）`go test ./internal/clitest/e2e/` |
| `.claude/hooks/check-adr.sh` | `go test ./internal/clitest/core/`（`checkadrhook_test.go`） |
| `.claude/hooks/mark-ext.sh`・`test-ext.sh`・`extensions/vscode/ccnavi-board/scripts/test-groups.js` | `uv run python -m tests`（`core` の `test_ext_tests`） |
| `tests/host_stub.py`・`tests/fake_gitlab.py`・`tests/contract/` | `uv run python -m tests` |
| `go/internal/harness/`・`tests/scenarios/`（受入シナリオの実行器・スタブ・シナリオの yml） | `go test ./internal/harness/... ./internal/clitest/scenarios/` |
| `extensions/vscode/` | `go test ./internal/clitest/core/`（`testjson_test.go`）。拡張の分は下 |
| `extensions/chrome/ccnavi-approval/` | その `pnpm test`（下） |
| `docs/30_adr/`・`docs/30_adr.md`（枚を足す・番号を動かす） | `go test ./internal/clitest/repo/`（`adr_numbers_test.go`） |
| そのほかのドキュメントだけ（`*.md`・`docs/`） | 回さない |

自動テストで中身を確かめられないもの（変えたら手で動かして確かめる）:

- `.claude/hooks/lint-py.sh`
- `.claude/hooks/test-py.sh` の差し戻しの回数の上限

統合先に取り込む前・MR に出す前は、Python の全件と `go test ./...` の両方を回す。

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
