---
type: adr
title: テストを全部 Go に移し、Python を消す
description: 実装が Go なのにテストが Python で、2 つの言語の道具と環境が要った。テストを全部 Go に移し、sh を通すテストを回す道具（harness）を作り、tests/ の Python・pyproject.toml・uv.lock を消した。hook は毎ターン軽い組だけ回し、全体は commit の前に回す
tags: [testing, go-rewrite, sh-scripts]
keywords: [テスト, Go, Python, harness, clitest, uv, pyproject, hook, go test, commit, 全件]
---
# ADR-0235: テストを全部 Go に移し、Python を消す

状態: 採用

## 状況

実行ファイルを Go に書き直したあとも、テストは Python（`tests/`、unittest、uv）のまま残っていた。実行ファイルを起こして外から確かめるテストと、sh を通すテスト、GitLab の模擬、契約テスト、受入シナリオの実行器がすべて Python だった。

- 実装は Go なのに、テストを回すには Python 3.12 以上と uv と PyYAML が要った。開発の道具が 2 つの言語に分かれ、環境の版の決まりと落とし穴（uv のハードリンクでワークツリーが消せない、など）も 2 つ分あった
- 同じ表（見本）を Go と Python の両方が読んで一致を確かめる形が増えた。片方を直すと、もう片方の写しを直す手間が要った
- テストの時間の大半は sh と git を起こす時間だった。全件は 4 並列で 8 分半かかり、セッションの時間の上限で打ち切られることがあった。Python の全件は `python -m tests` という自作の並列実行器に頼っていた
- sh を通すテストは、使い捨てのワークスペース・偽のホスト・実行ファイルのスタブを組む部品を Python に持っていた。Go から同じことをする道具が無く、Go に寄せられなかった

## 決定

**ccnavi のテストを全部 Go（`go/`）に移し、Python のテスト（`tests/` の `.py`）・`pyproject.toml`・`uv.lock` を消した。**

- 実行ファイルと配る sh を外から確かめるテストは `go/internal/clitest/` に置く。置き場は主題ごとのパッケージ（`core`・`repo`・`config`・`guard`・`ticket`・`sh`・`contract`・`scenarios`・`e2e`・`manual`）に分け、go test の並行で回す
- sh を通すテストのための道具を `go/internal/harness/` に作った。実行ファイルと偽の GitLab・GitHub・claude -p のスタブを組む部品、使い捨てのワークスペースと git のリポジトリを組む部品、受入シナリオの実行器、契約テストの比較器と録り直しの道具がある。起こす子の環境は `base.Main` が整える（機械の git の設定・`CCNAVI_*`・トークンを外し、PATH から ccnavi を外す）
- `tests/` に残すのは、Go が読むデータだけにする。見本（`fixtures/`）、契約テストの録画（`contract/`）、受入シナリオ（`scenarios/`）、手動の GitLab の compose と token の rb（`manual/`）
- hook は毎ターン、軽い組だけを回す。`gofmt -l`・`go vet`・clitest 以外の `go test`（go test が結果を保存するので、変えていなければ数秒）・`go test ./internal/clitest/repo/`。Go のテスト全体（`go test ./...`）は、commit の前に commit スキルが回す。統合先に取り込む前・MR に出す前も全件を回す

## 理由

- 実装とテストが同じ言語・同じ道具（go test・go vet・gofmt）になり、開発の環境は Go・Node・jq だけで済む。uv と Python の版と PyYAML の依存、それに伴う落とし穴が要らなくなる
- 見本の表は Go 1 か所で読める。写しの二重の保守が無くなる
- Go のテストは go test の並行と結果の保存を使える。変えていないパッケージは回し直さないので、毎ターンの hook を軽くできる
- 自作の並列実行器（`python -m tests`）と、それが拾うモジュールを守る試験（`test_layout`・`test_run_tests`）が要らなくなる

## 得たもの・失ったもの

得たもの:

- 言語が 1 つになり、環境・hook・commit の手順が単純になった
- 部品（harness）をパッケージにしたので、別のテストからも使える。層の依存は `layers_test.go` が見る

失ったもの:

- 移すときに、観点が重なるテストを整理して減らした。表に書けるものは表駆動にまとめた。移す前と本数は一致しない
- 全件の時間が長い（目安 165 秒。機械で変わる）。毎ターンは回さず、commit の前に回す形にした。ターンの途中で壊した clitest の失敗は、commit の前まで見つからないことがある
- この環境の rm の安全ラッパーでは、install・doctor・package の一部のテストが走らない。rm が本物の機械で回す必要がある
- スキル `yomiyasu` の道具（`yomiyasu_lint.py`）は標準ライブラリだけの Python で、スキルの一部として残る

## 採らなかった案

| 案 | 落とした理由 |
|---|---|
| Python のテストを残し、Go の実行ファイルだけを当てる（それまでの形） | 2 つの言語の道具と環境が要り続ける。見本の写しの保守も残る |
| Go のテストへ移すが、sh を通すものは Python に残す | sh を通すテストが全体の 7 割で、Python の環境が要る理由が残る。harness を作れば Go から回せた |
| hook が毎ターン `go test ./...` を全件回す | clitest は結果が保存されず毎回走るので、ターンごとに数分かかる。軽い組だけを毎ターン、全体を commit の前にした |
