---
type: guide
title: 実行ファイルをGoに書き直す
description: ccnaviの実行ファイル（src/ccnavi/のPython）をGo（go/）に一括で書き直す作業の決まりと進み具合
tags: [process-implement]
keywords: [Go, 書き直し, 移行, 起動時間, go test, CCNAVI_TEST_EXE, 正規表現, YAML]
---

# 実行ファイルをGoに書き直す

## なぜ

実行ファイルは hook・sh・拡張から 1 操作に何度も起動される。Python は起動 1 回に約 0.25 秒かかり、そのうち約 0.2 秒は import だった（`ccnavi --version` 0.23 秒、素の Python 0.018 秒）。テストも同じで、`tests.sh.test_stage_sh` の 12 秒のうち約 8 秒が `python -m ccnavi` の 32 回の起動だった。Go なら起動 1 回は 0.01〜0.02 秒になる。

## 進め方

- 一括で書き直す。このブランチで全部を Go にしてから、Python を外して切り替える。途中で Python と Go を混ぜて配らない
- CLI の引数・標準出力（JSON を含む）・標準エラー・終了コードは変えない。拡張（TS）・sh・Chrome 拡張はそれを読むので、直さずに済ませる
- 書き直しを機にリファクタする。外から見える振る舞いは変えず、中の構成は今後の拡張がしやすい形に変える
- 下の段から移す。段の並び（`infra` → `records` → `policy` → `tickets` → `catalog` → `hook` → `entry`）と、依存を下へだけ向ける決まりは Python と同じ
- Go のパッケージは責務ごとにまとめる。Python のモジュールと 1 対 1 にしない。大きさだけで分けたモジュールは、1 つのパッケージの複数ファイルにする（例: `shellread_*` の 4 つは `infra/shell`、`gitcmd` と `gitstate` は `infra/git`）
- 同じことをしている関数は 1 つにまとめ、差はオプションで表す。呼び手の無い関数は移さない。公開する API は、上の段が使うものに絞る
- コメントは docstring を丸写ししない。読む人が知るべきこと（なぜそうするか、守る約束、落とし穴）だけを短く書く。経緯、ほかのファイルの説明の繰り返し、コードを読めば分かることは書かない。パッケージの役割は `doc.go` に 1 段落で書く
- 上の段が同じ下ごしらえを繰り返さないための土台を、下の段に置く（YAML の値を型を確かめて取り出す関数、Python の `json.dumps` と同じバイト列を作る JSON の出力など）

## テスト

- CLI を通すテスト（`tests.inproc.run_ccnavi` と sh のテスト）は Python のまま残し、`CCNAVI_TEST_EXE=<Goの実行ファイル>` で当てる。振る舞いが同じことは、これで確かめる
- 内部のモジュールを直に import する単体テスト（約 110 ファイル）は、そのモジュールを移すときに `go test`（同じディレクトリの `*_test.go`）へ移す。表で書けるものは表駆動にする
- Python 側の単体テストは、Python を外すときに一緒に消す。それまでは Python の実装に当てて回り続ける

## Python と Go で答えが変わりやすいところ

| 箇所 | 合わせ方 |
|---|---|
| `str.casefold()` | `golang.org/x/text/cases.Fold()`。`pathcase.Fold` を通す |
| `os.path.normcase` | Windows だけ `/` を `\` にして小文字にする。Linux・macOS は何もしない |
| 正規表現の先読み・後読み（`(?=` など。`src/` に約 50 か所） | Go の RE2 には無い。手で書いた照合か、2 段の照合に直す |
| YAML（PyYAML の SafeLoader、YAML 1.1） | `gopkg.in/yaml.v3` は 1.2 寄り。`yes`・`no`・`on`・`off` の真偽、`0o` と `0` 始まりの 8 進、時刻の扱いが違う。`infra/yamlread` で 1.1 の読みに合わせる |
| 辞書の順序 | Python の dict は入れた順。JSON を出すところは順序を持つ型で組む |
| JSON の出力 | Python の `json.dumps` の既定（`ensure_ascii`・区切りの空白）に合わせる。呼び出しごとに引数を確かめる |
| 文字列の長さ・切り出し | Python は符号位置で数える。Go の `len` はバイト。`[]rune` か `utf8` で数える |

## 進み具合

| パッケージ | 行数（Python） | 状態 |
|---|---|---|
| `infra` | 6,466 | 着手 |
| `records` | 4,562 | 未着手 |
| `policy` | 6,804 | 未着手 |
| `tickets` | 36,732 | 未着手 |
| `catalog` | 1,683 | 未着手 |
| `hook` | 10,631 | 未着手 |
| `entry` | 11,210 | 未着手 |

## 回す

```sh
cd go && go vet ./... && go test ./...
```

依存の向きは `go/internal/layers_test.go` が見る。パッケージを足したら、先にその表（`topTiers`・`subTiers`）を直す。

`go mod tidy` は、まだ使っていない依存（YAML・TOML）を消すので回さない。依存を足すときは `go mod edit -require` と `go mod download` で足す。
