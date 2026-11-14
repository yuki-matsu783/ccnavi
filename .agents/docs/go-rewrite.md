---
type: guide
title: 実行ファイルをGoに書き直す
description: ccnaviの実行ファイルをPythonからGo（go/）に書き直した作業の決まり、進み具合、切り替えのあとに残ったこと
tags: [process-implement]
keywords: [Go, 書き直し, 移行, 起動時間, go test, CCNAVI_TEST_EXE, 正規表現, YAML, testdata, 見本, golden, -update]
---

# 実行ファイルをGoに書き直す

## なぜ

実行ファイルは hook・sh・拡張から 1 操作に何度も起動される。Python は起動 1 回に約 0.25 秒かかり、そのうち約 0.2 秒は import だった（`ccnavi --version` 0.23 秒、素の Python 0.018 秒）。テストも同じで、`tests.sh.test_stage_sh` の 12 秒のうち約 8 秒が `python -m ccnavi` の 32 回の起動だった。Go なら起動 1 回は 0.01〜0.02 秒になる。

## 進め方

- 一括で書き直し、全部を Go にしてから切り替えた。配る実行ファイルは Go のバイナリで、Python の実行ファイル（PyInstaller）は組まない。`src/ccnavi/` は消した
- CLI の引数・標準出力（JSON を含む）・標準エラー・終了コードは変えない。拡張（TS）・sh・Chrome 拡張はそれを読むので、直さずに済ませる
- 書き直しを機にリファクタする。外から見える振る舞いは変えず、中の構成は今後の拡張がしやすい形に変える
- 下の段から移す。段の並び（`infra` → `records` → `policy` → `tickets` → `catalog` → `hook` → `entry`）と、依存を下へだけ向ける決まりは Python と同じ
- Go のパッケージは責務ごとにまとめる。Python のモジュールと 1 対 1 にしない。大きさだけで分けたモジュールは、1 つのパッケージの複数ファイルにする（例: `shellread_*` の 4 つは `infra/shell`、`gitcmd` と `gitstate` は `infra/git`）
- 同じことをしている関数は 1 つにまとめ、差はオプションで表す。呼び手の無い関数は移さない。公開する API は、上の段が使うものに絞る
- コメントの粒度は `environment.md` の「コードのコメントの粒度」に従う。docstring は丸写ししない。直感的でない処理（Python と答えを合わせるための分岐、順序に依存する手順、意外な既定値、照合や後戻りの仕掛け、OS ごとの違い）には、何をしているかとなぜそうするかの両方を書く。長い関数は、処理のまとまりごとに何をするかを 1 行で書き、上から読めば流れが分かるようにする。経緯、ほかのファイルの説明の繰り返し、1 行ごとの実況は書かない。パッケージの役割は `doc.go` に 1 段落で書く
- 上の段が同じ下ごしらえを繰り返さないように、下の段に土台を置く（YAML の値を型を確かめて取り出す関数、Python の `json.dumps` と同じバイト列を作る JSON の出力など）

## 書き方の標準

後で直す人が、値の意味と置き場をすぐ見つけられることを優先する。

### 定数

- 意味のある値は名前を付けた定数にし、同じ値を 2 か所に書かない。対象は、環境変数の名前、置き場のパスとファイル名、終了コード、時間の上限と既定値、件数・大きさの上限、ルールやフェーズの識別子、JSON のキー（構造体のタグで書けないもの）
- 置き場は、その値の責務を持つパッケージに 1 つ。使う側は import して参照する
  - 環境変数の名前は `infra/env` にすべて置く。値の読み方（既定値・範囲・単位）は、その値を使うパッケージが持つ。`CCNAVI_*`・`CLAUDE_*` を文字列で直書きすると `go/internal/conventions_test.go` が落ちる
  - git に渡す `GIT_*` と git の引数は `infra/git`
  - チケット・提案・承認済みの置き場のパスは `tickets/model`
- 選択肢の決まった値（モード、フェーズの状態、判定の結果など）は、`type Mode string` のような型を作り、その型の定数で列挙する。文字列の比較を散らさない
- 時間は `time.Duration` で持つ（`HookDeadline` の既定は `3 * time.Second`）。秒の整数を受け渡さない
- 0・1・空文字列のように意味が自明な値は、定数にしなくてよい

### 文面

- ユーザとエージェントが読む文は、文面ファイル（`.ccnavi/messages/*.yml`）から引く。Go でも Python と同じ仕組みを使う（[文面ファイルに文を足す・直す](message-files.md)）
- Python でまだコードに直書きされている文は、同じ文を移す。そのときは文をパッケージの定数にまとめ、関数の中に散らさない。文面ファイルへの移行は、[文面の移行の一覧](message-migration.md)に沿って別に進める

### エラー

- 下の段は `error` を返し、包むときは `fmt.Errorf("…: %w", err)` を使う。呼び手が種類で分けるエラーは、`var ErrX = errors.New(…)` か型で定義し、`errors.Is`・`errors.As` で見る
- `panic` は使わない。想定外のエラーは入口（`entry`）で受け、Python の内部エラーと同じ出力にする
- 起動時に組む固定の式の `pyre.MustCompile` は `panic` の例外として許す（組めないのはビルドの不備で、テストが起動の時点で落ちる）

### 構成と名前

- パッケージ名は短い小文字の単数（`git`・`shell`・`fsio`）。役割は `doc.go` に 1 段落で書く
- ファイルは責務ごとに分ける。1 ファイルに型とその操作をまとめる
- interface は使う側のパッケージで、使うメソッドだけを含む形で定義する
- 略語は Go の慣習に従う（`ID`・`JSON`・`URL`・`SHA`）
- OS の違いは `ospath.Windows` で分ける（`runtime.GOOS` を直に比べない）。分ける場所は `infra` の中に閉じ、上の段に OS の分岐を書かない。build タグでファイルを分けるのはよい
- 外部のプロセスは `exec.CommandContext` で、期限を定数から渡して起動する

### 検査

- `gofmt` と `go vet` を通す
- 依存の向きは `go/internal/layers_test.go` が見る。見るのは本体のファイル（`_test.go` 以外）の import で、段は親のパスごとの表で決める。`topTiers` は `internal/` 直下の段、`subTiers` は親（`infra`・`tickets`・`tickets/config` など）ごとの子の段で、兄弟は段が小さい兄弟（とその下）だけを読める。表に親が無い所の兄弟どうしと、親と子のあいだは見ない。テストだけが読む道具（`tickets/testkit`）は親の表の一番上に置き、本体から読めないようにする。`go/internal/conventions_test.go` が見るのは、いまは環境変数の名前の直書き（`CCNAVI_*`・`CLAUDE_*`）だけで、ほかの定数の決まりは読む人が守る。機械で見られる決まりを足したら、`conventions_test.go` に検査も足す

## テスト

- CLI を通すテスト（`tests.inproc.run_ccnavi` と sh のテスト）は Python のまま残し、Go の実行ファイルを起こす。sh の判断と解釈を Go で実装したら、それは Go の表駆動のテストで確かめ、sh のテストは通しの確認だけにする（[実行ファイルの境界](exe-boundary.md)の「テスト」）。実行ファイルは `tests/exe.py` が `go build` で組み、ソースが同じなら組み直さない。`CCNAVI_TEST_EXE=<実行ファイル>` を渡すと、組まずにそれを使う。`tests/` は ccnavi の Python を読まない
- 内部のモジュールを直に import していた単体テストは、`go test`（同じディレクトリの `*_test.go`）へ移した。表で書けるものは表駆動にした
- `go test ./...` は Python を使わない。Python が無い機械でも、飛ばすテストは増えない

### 見本（testdata）

Python の実装と答えを突き合わせていたテストは、Python を外したときに、答えを `testdata/<名前>.json` の見本と比べる形にした。見本は Go の答えを正として守る。読む・比べる道具は `go/internal/infra/golden`（テストからだけ読む）。

- `golden.Load`：Python の答えの写しとして取っていた見本を読み、テストが Go の答えと項目ごとに比べる。Go の振る舞いを意図して変えたときは、落ちたテストが示す Go の答えに合わせて JSON を手で直す。写しを取り直す口（`-update` で Python を起こす処理）と、Python が無いと飛ぶ分岐は消した
- `golden.Check`：Go の答えをまるごと見本と比べ、`go test -run <テスト> -update` で見本を書き直す。写しを持たずに毎回 Python に聞いていたテスト（文面ファイルの文を含む答え、ルートの場所が毎回変わる答え）は、Python と答えが揃っていた Go の答えからこの見本を取った。列と表は 1 件を 1 行に書くので、書き直した差分は git で件ごとに読める。一時置き場の名前と、ルートで始まって途中で切った引数は `golden.Check` が印に置き換える。ほかの機械ごとに変わる値（ルート・時刻・要約）は、テストが置き換えてから渡す
- `-update` は `golden.Check` を使うパッケージにだけある。文面ファイルの文を直すと、文を含む見本（`hook/reasons`・`hook/judge` など）が落ちるので、`-update` で書き直してから差分を読む
- 写しと比べる方法がないまま Python とだけ突き合わせていたもの（リポジトリの共通のルール・文面の全件の組み立て・乱数の種を毎回変える回）は、Go だけで確かめる形にするか消した

### 移すときに整理する

テストは移しながら減らす。残すのは、壊れたら困る約束（判定・出力の形・境界・安全に関わる読み）を 1 回ずつ確かめるものだけにする。

- 消すもの
  - 過去の処理が消えたこと、名前を変える前の名前が読まれないことを確かめるテスト（後方互換は考えないので、消えたものは確かめない）
  - 内部の実装の細部（呼ぶ順序、途中の値、私的な関数の形）に縛られたテスト。外から見える結果で確かめ直せるなら、そちらに寄せる
  - 同じ約束を入力だけ変えて何本も書いたもの。表駆動の 1 本にまとめ、境界と代表だけを残す
  - 文書・コメント・文字列の言い回しをそのまま比べるだけのもの（文面ファイルの検査が見るものを除く）
- 残すもの
  - 不具合を直したときに足した回帰のテスト。何の不具合かをテスト名か 1 行のコメントで言う
  - OS・大文字小文字・改行・文字コードの違いを確かめるもの
- 消したテストと理由は、移した単位ごとに報告にまとめる。CLI を通す Python のテストも、同じ基準で後でまとめて整理する

## Python と Go で答えが変わりやすいところ

| 箇所 | 合わせ方 |
|---|---|
| `str.casefold()`・`unicodedata.normalize` | パスは `pathcase.Fold`、パスでない語は `pyfmt.Casefold`（区切りを変えない）。NFKC・NFC は `pyfmt.NFKC`・`pyfmt.NFC`。`golang.org/x/text` を直に使わない |
| `os.path.normcase` | Windows だけ `/` を `\` にして小文字にする。Linux・macOS は何もしない |
| 正規表現 | Python の re の答えが要る式は `infra/pyre`（Python 3.13 の re の写し）を使う。固定の式で RE2 で書けるものは `regexp`。pyre は RE2 の 5〜10 倍遅い |
| 正規表現の照合の深さ | pyre は入れ子が `pyre.depthLimit` を越えた照合を打ち切る（Python の sre は打ち切らない。Go はスタックがあふれるとプロセスごと落ちるため）。打ち切りは `SearchChecked` が知らせ、ルールの照合（`rules.Rule.Matches`）は止める側（deny・ask）を当たり、allow を外れにする。数 KB の通常のコマンドでは届かない |
| YAML（PyYAML の SafeLoader、YAML 1.1） | `gopkg.in/yaml.v3` は libyaml の写しで、1.1 の真偽・8 進・時刻の読みも、読める・読めないの境界も違う。そこで PyYAML の純 Python の読み手を `infra/yaml` に写した。上の段は `*yaml.Map` の取り出し（`Text`・`Map` など）で値を読む。壊れた YAML の苦情の文は純 Python の読み手（SafeLoader）に合わせる。libyaml（CSafeLoader）の文とは違う。書き出し（`yaml.safe_dump`）は純 Python の書き手を写した `yaml.Dump`（80 字の折り返しも同じ） |
| 辞書の順序 | Python の dict は入れた順。JSON を出すところは順序を保つ型で組む |
| JSON の出力 | Python の `json.dumps` の既定（`ensure_ascii`・区切りの空白）に合わせる。呼び出しごとに引数を確かめる |
| 文字列の長さ・切り出し | Python は符号位置で数える。Go の `len` はバイト。`[]rune` か `utf8` で数える |

## 進み具合

書き直しは終わった。配る実行ファイルは `scripts/ccnavi-build.sh` が組む Go のバイナリ、Chrome 拡張の判定は Go の WASM（`go/cmd/ccnavi-chrome`）、Python のテストが起こす ccnavi も Go のバイナリ（`tests/exe.py`）。Python の実行ファイル（`src/ccnavi/`）と、それを直に読む白箱のテストは消した。

行数は書き直す前の Python（消した `src/ccnavi/<段>` の `*.py`。空行・コメントも含む）の行数。

| パッケージ | 行数（Python） | 状態 |
|---|---|---|
| `infra` | 6,472 | 移した（レビュー済み）。Python の re の写し（`infra/pyre`）を含む |
| `records` | 4,570 | 移した（レビュー済み） |
| `policy` | 6,842 | 移した（レビュー済み） |
| `tickets` | 36,711 | 移した（レビュー済み） |
| `catalog` | 1,683 | 移した（レビュー済み） |
| `hook` | 10,497 | 移した（レビュー済み） |
| `entry` | 11,210 | 移した（レビュー済み）。Chrome 拡張の入口（Python の `entry/ext.py` と拡張の `py/ccnavi_chrome.py`）は `entry/chrome` と `cmd/ccnavi-chrome`（WASM）にした |

## 後で扱うこと

レビューで見つかり、いまは直さずに、扱う段に持ち越したもの。扱ったら行を消す。

| いつ | 何を |
|---|---|
| 当面扱わない | `infra/pyre` の照合を速くする（Python の C 実装の約 5 倍遅い）、照合の深さの上限（`depthLimit`）を外す、`(?i)ß` などの小さな食い違い。中身は [yuki-matsu783/ccnavi#806](https://github.com/yuki-matsu783/ccnavi/issues/806) |

## 回す

Go の版は `go/go.mod` の `go 1.26.0` に合わせる。

```sh
cd go && go vet ./... && go test ./...
cd go && go test ./internal/hook/reasons -run TestTextsMatchPython -update   # golden.Check の見本を書き直す
```

`go/cmd/ccnavi/main.go` は標準入出力と引数を `entry/cli` の `Run` に渡して終わるだけ。配る実行ファイルは `scripts/ccnavi-build.sh` が組む（[組み立てと配布物](build-and-dist.md)）。Python の CLI のテストは、次のように Go の実行ファイルに当てる（版のコミットは `-ldflags "-X ccnavi/internal/entry/version.commit=<sha>"` で入れる）。

```sh
uv run python -m unittest tests.guard.test_permission_mode   # tests/exe.py が組む
CCNAVI_TEST_EXE=/tmp/ccnavi-go uv run python -m unittest tests.guard.test_permission_mode   # 組んだものを使う
```

依存の向きは `go/internal/layers_test.go` が見る。パッケージを足したら、先にその表（`topTiers` と、親のパスごとの `subTiers`）に行を足す。

依存は `go mod tidy` で整える。外の依存は `golang.org/x/text` と `github.com/BurntSushi/toml` だけにする。
