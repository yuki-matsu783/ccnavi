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

### 構成と名前

- パッケージ名は短い小文字の単数（`git`・`shell`・`fsio`）。役割は `doc.go` に 1 段落で書く
- ファイルは責務ごとに分ける。1 ファイルに型とその操作をまとめる
- interface は使う側のパッケージで、使うメソッドだけを持つ形で定義する
- 略語は Go の慣習に従う（`ID`・`JSON`・`URL`・`SHA`）
- OS の違いは `ospath.Windows` で分ける（`runtime.GOOS` を直に比べない）。分ける場所は `infra` の中に閉じ、上の段に OS の分岐を書かない。build タグでファイルを分けるのはよい
- 外部のプロセスは `exec.CommandContext` で、期限を定数から渡して起動する

### 検査

- `gofmt` と `go vet` を通す
- 依存の向きは `go/internal/layers_test.go` が見る。見るのは本体のファイル（`_test.go` 以外）の import で、段は親のパスごとの表で決める。`topTiers` は `internal/` 直下の段、`subTiers` は親（`infra`・`tickets`・`tickets/config` など）ごとの子の段で、兄弟は段が小さい兄弟（とその下）だけを読める。表に親が無い所の兄弟どうしと、親と子のあいだは見ない。テストだけが読む道具（`tickets/testkit`）は親の表の一番上に置き、本体から読めないようにする。`go/internal/conventions_test.go` が見るのは、いまは環境変数の名前の直書き（`CCNAVI_*`・`CLAUDE_*`）だけで、ほかの定数の決まりは読む人が守る。機械で見られる決まりを足したら、`conventions_test.go` に検査も足す

## テスト

- CLI を通すテスト（`tests.inproc.run_ccnavi` と sh のテスト）は Python のまま残し、`CCNAVI_TEST_EXE=<Goの実行ファイル>` を指定して回す。振る舞いが同じことは、これで確かめる
- 内部のモジュールを直に import する単体テスト（約 110 ファイル）は、そのモジュールを移すときに `go test`（同じディレクトリの `*_test.go`）へ移す。表で書けるものは表駆動にする
- Python 側の単体テストは、Python を外すときに一緒に消す。それまでは Python の実装に対して回し続ける

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
| YAML（PyYAML の SafeLoader、YAML 1.1） | `gopkg.in/yaml.v3` は libyaml の写しで、1.1 の真偽・8 進・時刻の読みも、読める・読めないの境界も違う。そこで PyYAML の純 Python の読み手を `infra/yaml` に写した。上の段は `*yaml.Map` の取り出し（`Text`・`Strings`・`Map` など）で値を読む。壊れた YAML の苦情の文は純 Python の読み手（SafeLoader）に合わせる。libyaml（CSafeLoader）の文とは違う。書き出し（`yaml.safe_dump`）は純 Python の書き手を写した `yaml.Dump`（80 字の折り返しも同じ） |
| 辞書の順序 | Python の dict は入れた順。JSON を出すところは順序を持つ型で組む |
| JSON の出力 | Python の `json.dumps` の既定（`ensure_ascii`・区切りの空白）に合わせる。呼び出しごとに引数を確かめる |
| 文字列の長さ・切り出し | Python は符号位置で数える。Go の `len` はバイト。`[]rune` か `utf8` で数える |

## 進み具合

行数は `find src/ccnavi/<段> -name '*.py' | xargs cat | wc -l` で数える（空行・コメントも含む）。

| パッケージ | 行数（Python） | 状態 |
|---|---|---|
| `infra` | 6,468 | 移した（レビュー前）。Python の re の写し（`infra/pyre`）を含む |
| `records` | 4,562 | 移した（レビュー前） |
| `policy` | 6,794 | 移した（レビュー前） |
| `tickets` | 36,700 | 移した（レビュー前） |
| `catalog` | 1,683 | 未着手 |
| `hook` | 10,634 | 未着手 |
| `entry` | 11,210 | 未着手 |

## 後で扱うこと

レビューで見つかり、いまは直さずに、扱う段に持ち越したもの。扱ったら行を消す。

| いつ | 何を |
|---|---|
| 上の段を移すとき | 呼び手の無い公開名（`fsio`・`git`・`tree`・`shell` など、pyre の `Match`・`FullMatch`・`FindAll`・`Found`・`Named`・`Group`、`selfguard.Resolve`）を、使う段で絞る。あわせて rules の文面の出どころ（`Message`・`Text`・`DegradedMessage`）の優先の決めを 1 か所にまとめ、`compileExpr` の戻りを結果の種類の型にし、公開の可変グローバル（`rules.ScriptPlaceholder`・`Sections`・`SubjectFields`、`selfguard.Choices`・`GateChoices`）を絞る。lifecycle/worktrees の `Status` の定数 `Removed` とメソッド `Dropped.Removed()`（`Absent` も真）の名前の紛らわしさを、呼び手が揃ったところで分ける。「未コミットの変更」の判定（`integrate.Dirty` と `worktrees.status`）を 1 つに寄せる（`treegate.Dirty` は未追跡を数えない別の意味なので寄せない）。承認済み・閉じた・レビュー中の 3 回の走査の繰り返し（`retire.parentOf`・`worktrees.FamilyFor`・`remaining`・`branch.SerialNames`）を、1 回の走査の結果を渡す形にする |
| `entry` を移すとき | `config.LayerPath` の差し替えの表を、名前の大文字小文字を区別せずに引く（`pathcase.Fold`） |
| `entry` を移すとき | stopreview の呼び方が変わったもの（`log_event` → `LogNoKey`・`LogPlanTimeout`・`LogSuspected`、clear 3 つ → `turn.Clear`、stopeval の 3 つ → `Report`、`wiki.pending` → `PendingImpacts`）に呼び手を合わせる |
| hook を移すとき | records/prune が持つ上の段のファイル名の残りの写し（subagent-・turngate-・.turn・.accepted・.reported）を、持ち主の定数と突き合わせるテストを置く（Python は tests/core/test_prune.py が見ていた）。ops と direct- は `config` の定数を両方が読み、sent は oplog のテストが prune の数えで、nudged- と mirror- は持ち主（lifecycle/stop・sync/mirror）のテストが古い記録が消えることで確かめる |
| hook を移すとき | `infra/pyre` の照合に深さ（歩数）の上限を入れ、超えたら照合を打ち切って拒否側に倒す。いまは繰り返しを含む利用者の式を数 MB のコマンドに当てると、呼び出しが深くなってスタックがあふれ、hook が落ちる（落ちると止めずに通る）。判定の期限（Python も持たない）と一緒に設計する |
| Python を外すとき | Python の policy・infra のファイルを指す設計書と sh（`docs/21_design/messages.md`・`startup-config.md`・`modules.md`、`.agents/docs/environment.md`・`e2e-exploration.md`、`.ccnavi/scripts/ccnavi-launcher.sh`・`ccnavi-git.sh` のコメント）を Go の置き場に直す |
| Python を外すとき | Chrome 拡張（`extensions/chrome/ccnavi-approval`）は Pyodide で Python の `entry.ext` → `tickets.core.api` を読む。Go には移せないので、Pyodide 用に Python の判定一式を残して配るか、拡張の判定を TS か WASM に書き直すかを決める（ユーザと合意が要る）。それまでは Go と Python の判定のずれを Chrome の見本（`extensions/chrome/ccnavi-approval/test/fixtures/core-scenarios.json`）で見張る |
| Python を外すとき | `git.Undo` の案内でパスを `"` で囲むだけの引用を、`config` の引用と同じ規則にする（`$` やバッククォートを含むパスで壊れる。いまは Python と同じ出力を保つため残す） |
| Python を外すとき | sync/handoff の `Apply` で、書いた後の読み直しに失敗したエントリが戻されない（Python も同じ。直すなら Python と同時に直す） |
| 余裕のあるとき | `infra/pyre` の照合を速くする（先頭の文字の集合で開始位置を飛ばす、開始位置ごとのクロージャをやめる）。いまは Python の C 実装の約 5 倍遅い。数 KB の通常のコマンドでは問題にならない |
| 余裕のあるとき | pyre の `(?i)ß` が `ẞ` に当たらない（casefix に 1 組足す）。rules の unsupported が `(?P=name)` の後方参照を通す。`TranslateGlob` が文字クラスの中の `/` を壊す（Python と同じ。Python の globmatch の docstring も誤り）。`\N{名前}` の苦情を名指しの文にする |

## 回す

Go の版は `go/go.mod` の `go 1.26.0` に合わせる。

```sh
cd go && go vet ./... && go test ./...
```

`go/cmd/ccnavi/main.go` はまだ入口を持たない（`entry` を移すまで、起動すると移していないと言って終わる）。`build.py` と `.ccnavi/bin/` の組み立てには入らず、配る実行ファイルは Python のまま。

依存の向きは `go/internal/layers_test.go` が見る。パッケージを足したら、先にその表（`topTiers` と、親のパスごとの `subTiers`）に行を足す。

依存は `go mod tidy` で整える。外の依存は `golang.org/x/text` と `github.com/BurntSushi/toml` だけにする。
