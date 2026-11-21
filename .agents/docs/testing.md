---
type: guide
title: ccnaviのテストを回す
description: ccnaviのテスト（Go）を回すときの決まり。変えたパッケージから絞って広げる。契約テストを回す条件、shを通すテストの書き方、待ち方
tags: [process-test]
keywords: [テスト, スタブ, STUB, 操作記録, 全件, go test, パッケージ, clitest, harness, 契約, 時間切れ, 並行, 重い]
---

# ccnaviのテストを回す

テストは全部 Go（`go/`）にある。判定・設定・チケット・sh・契約・受入シナリオは `go/internal/clitest/`、道具は `go/internal/harness/`。書き方の見本は `go/internal/clitest/doc.go`。Go の標準の回し方は[実行ファイルをGoに書き直す](go-rewrite.md)の「回す」にある。clitest の待ちが主のテストの並行の数は、`base.Main` が CPU の数（GOMAXPROCS）の 2 倍に上げる（`-parallel` を渡せばそちらが優先。`m.Run` の中の flag の解析が後から上書きする）。

## 全件は commit の前に回す

全件は `cd go && go test ./...` の 1 本。clitest は sh と実行ファイルを何度も起こすので、全体で数分かかる（目安は 165 秒。機械で変わる）。ターンごとには回さず、commit の前と、統合先に取り込む前・MR に出す前に回す。

ターンの終わりの hook は軽い組だけ回す（`gofmt -l`・`go vet`・clitest 以外の `go test`・`clitest/repo`。決まりは[開発用の hook](dev-hooks.md)）。clitest 以外のパッケージは go test が結果を保存するので、変えていなければ数秒で終わる。

## どのパッケージを回すか

変えたファイルから回す範囲を決める。表は `.claude/skills/commit/references/test-groups.md`にある。
表に該当しない変更や、統合先に取り込む前・MRに出す前は、全件を回す。

### 回す単位は、変えたパッケージまで絞る

最初は、変えたパッケージだけを回す（`go test ./internal/clitest/sh/stage/`、`go test ./internal/policy/rules/ -run TestX`）。通ったら、近いパッケージ、`./internal/clitest/...`、全件の順に広げる。広げる目安は、hook の判定や共通の関数のように、変えた動きが呼び出し元の多い所に及ぶとき。

clitest のテストは、本物の git のリポジトリを一時的に作り、本物のシェルを起動して確かめる。1本ごとに数秒かかるものがあり、数が増えると合計が伸びる。同じ木の鍵を直列に待つパッケージ（`clitest/ticket/life`）は 40〜60 秒かかる。

出力が長く出ないときは、待ち続けない。10 分を目安に止めて、範囲を狭めてやり直す。出力が無いままでは、通ったのか止まったのか分からない。通ったと扱わない（下の「待つとき」と同じ）。変更が大きいときや、取り込む前は、関係しそうな範囲を広めに選ぶ。絞ると、関係ないと思った場所への影響を見落とす。

GitLab の模擬（`go/internal/harness/fakegitlab`）、sh の試験が使う見本（`go/internal/harness/hostfixture`・`hoststub`）とその見本（`extensions/chrome/ccnavi-approval/test/fixtures/host/gitlab/`）の応答を変えたら、契約テスト（`go test ./internal/clitest/contract/...`。約 15 秒）を必ず回す。本物から録った応答の形と比べる試験で、模擬が本物とずれると落ちる。落ちたら本物に合わせて模擬を直す。仕様と録り直しは [gitlab-contract.md](gitlab-contract.md)。

`go/internal/harness/contractshape` と、`tests/contract/gitlab/` の `normalize-cases.json`・`requests.json`・`recorded.json` を直したら、`extensions/chrome/ccnavi-approval` の `pnpm test` も回す（TS の比較器が同じ表を読む）。Chrome 拡張の `MockGitLab` を直したときも `pnpm test` を回す。拡張だけの要求（`used_by` が `["chrome"]`）は Go の契約テストでは模擬に送らず、`pnpm test` でだけ `MockGitLab` と比べる。Go の契約テストは TS を回さないので、食い違いは `pnpm test` を回すまで見つからない。

`go/internal/harness/fakeclaude`（claude -p のスタブ）、`tests/contract/claude/recorded.json` を変えたら、同じく契約テスト（`go test ./internal/clitest/contract/claude/`）を回す。本物の `claude -p` から録った形と比べる。仕様と録り直しは [claude-contract.md](claude-contract.md)。

## sh のテストは通しの確認だけにする

sh のテスト 1 本の時間は、sh を 1 回走らせる時間でほぼ決まる。観点ごとに 1 本ずつ書くと、同じ実行を観点の数だけ繰り返すことになる。判断と解釈は Go で実装して表駆動のテストで確かめ、sh のテストは経路ごとに数本にする。1 回の実行で、終了コード・出力・作られた物・ホストへの要求をまとめて確かめる。sh と Go のどちらで実装するかは[実行ファイルの境界](exe-boundary.md)の「sh で実装するものと、Go で実装するもの」にある。

## sh の試験の実行ファイルのスタブ

sh を回す試験は、実行ファイルの代わりに小さな sh のスタブを `CCNAVI_ENTRY_PATH` に置くことがある。sh は実行ファイルの答えに依存するので、sh に新しい要求が足されたら、スタブも追従させる。追従が漏れると、スタブが黙って空の答えを返し、sh が「操作記録を作れなかった」などで止まる。

- `ops …` は本物の `ccnavi ops`（`harness/exe` が組んだ実行ファイル）へ渡す。sh は操作記録（`ops begin`・`show`・`stage-set`・`reconcile`・`applied`）を必須で使う
- 受け入れ計画（`--accept-plan` の JSON）は `op`（操作ID）を含む。スタブが書く計画には、本物の `oplog.Begin` で作った op を入れる
- 事前検査（`--check`）は、取り込みより先に sh が呼ぶ。スタブの `--check` が副作用（コミットを積むなど）を持たないようにする
- 文面ファイルを読む処理を試すための使い捨てのワークスペースには、`base.PutMessages` で文面を置く
- 子プロセスで `git merge`・`git commit` を呼ぶときは、`-c user.email=… -c user.name=…` を付ける。付けないと、識別子の無い環境で衝突とは別の理由で失敗する

見本は `go/internal/clitest/sh/wap/`。

## 待つとき

長く走るテストは、バックグラウンドで走らせて終了を待つ。フォアグラウンドで`sleep`を繰り返さない。
途中で打ち切られたときは、通ったとは扱わない。パッケージごとに分けて回し直す。
