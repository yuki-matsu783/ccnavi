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
- interface は使う側のパッケージで、使うメソッドだけを持つ形で定義する
- 略語は Go の慣習に従う（`ID`・`JSON`・`URL`・`SHA`）
- OS の違いは `ospath.Windows` で分ける（`runtime.GOOS` を直に比べない）。分ける場所は `infra` の中に閉じ、上の段に OS の分岐を書かない。build タグでファイルを分けるのはよい
- 外部のプロセスは `exec.CommandContext` で、期限を定数から渡して起動する

### 検査

- `gofmt` と `go vet` を通す
- 依存の向きは `go/internal/layers_test.go` が見る。見るのは本体のファイル（`_test.go` 以外）の import で、段は親のパスごとの表で決める。`topTiers` は `internal/` 直下の段、`subTiers` は親（`infra`・`tickets`・`tickets/config` など）ごとの子の段で、兄弟は段が小さい兄弟（とその下）だけを読める。表に親が無い所の兄弟どうしと、親と子のあいだは見ない。テストだけが読む道具（`tickets/testkit`）は親の表の一番上に置き、本体から読めないようにする。`go/internal/conventions_test.go` が見るのは、いまは環境変数の名前の直書き（`CCNAVI_*`・`CLAUDE_*`）だけで、ほかの定数の決まりは読む人が守る。機械で見られる決まりを足したら、`conventions_test.go` に検査も足す

## テスト

- CLI を通すテスト（`tests.inproc.run_ccnavi` と sh のテスト）は Python のまま残し、Go の実行ファイルを起こす。実行ファイルは `tests/exe.py` が `go build` で組み、ソースが同じなら組み直さない。`CCNAVI_TEST_EXE=<実行ファイル>` を渡すと、組まずにそれを使う
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
| 正規表現の照合の深さ | pyre は入れ子が `pyre.DepthLimit` を越えた照合を打ち切る（Python の sre は打ち切らない。Go はスタックがあふれるとプロセスごと落ちるため）。打ち切りは `SearchChecked` が知らせ、ルールの照合（`rules.Rule.Matches`）は止める側（deny・ask）を当たり、allow を外れにする。数 KB の通常のコマンドでは届かない |
| YAML（PyYAML の SafeLoader、YAML 1.1） | `gopkg.in/yaml.v3` は libyaml の写しで、1.1 の真偽・8 進・時刻の読みも、読める・読めないの境界も違う。そこで PyYAML の純 Python の読み手を `infra/yaml` に写した。上の段は `*yaml.Map` の取り出し（`Text`・`Strings`・`Map` など）で値を読む。壊れた YAML の苦情の文は純 Python の読み手（SafeLoader）に合わせる。libyaml（CSafeLoader）の文とは違う。書き出し（`yaml.safe_dump`）は純 Python の書き手を写した `yaml.Dump`（80 字の折り返しも同じ） |
| 辞書の順序 | Python の dict は入れた順。JSON を出すところは順序を持つ型で組む |
| JSON の出力 | Python の `json.dumps` の既定（`ensure_ascii`・区切りの空白）に合わせる。呼び出しごとに引数を確かめる |
| 文字列の長さ・切り出し | Python は符号位置で数える。Go の `len` はバイト。`[]rune` か `utf8` で数える |

## 進み具合

行数は `find src/ccnavi/<段> -name '*.py' | xargs cat | wc -l` で数える（空行・コメントも含む）。

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
| 上の段を移すとき | 呼び手の無い公開名（`fsio`・`git`・`tree`・`shell` など、pyre の `Match`・`FullMatch`・`FindAll`・`Found`・`Named`・`Group`、`selfguard.Resolve`）を、使う段で絞る。あわせて rules の文面の出どころ（`Message`・`Text`・`DegradedMessage`）の優先の決めを 1 か所にまとめ、`compileExpr` の戻りを結果の種類の型にし、公開の可変グローバル（`rules.ScriptPlaceholder`・`Sections`・`SubjectFields`、`selfguard.Choices`・`GateChoices`）を絞る。lifecycle/worktrees の `Status` の定数 `Removed` とメソッド `Dropped.Removed()`（`Absent` も真）の名前の紛らわしさを、呼び手が揃ったところで分ける。「未コミットの変更」の判定（`integrate.Dirty` と `worktrees.status`）を 1 つに寄せる（`treegate.Dirty` は未追跡を数えない別の意味なので寄せない）。承認済み・閉じた・レビュー中の 3 回の走査の繰り返し（`retire.parentOf`・`worktrees.FamilyFor`・`remaining`・`branch.SerialNames`）を、1 回の走査の結果を渡す形にする |
| Python を外すとき | Python の policy・infra のファイルを指す設計書と sh（`docs/21_design/messages.md`・`startup-config.md`・`modules.md`・`diagnostics.md`（`docsearch.START_SECONDS`）・`multi-repo/skills.md`（`projskills`）、配る `.ccnavi/docs/skill-processes.md`（`projskills` の名前が出る）、`.agents/docs/environment.md`・`e2e-exploration.md`、`.ccnavi/scripts/ccnavi-launcher.sh`・`ccnavi-git.sh` のコメント）を Go の置き場に直す。hook の Python のファイルを指すもの（`.ccnavi/messages/*.yml` と `.ccnavi/config/common/rules.yml` のコメント（`hook/*.py` のパス）、設計書 `docs/21_design/modules.md`・`pre-check.md`・`rules.md`・`stop-review.md`・`tickets/subagents.md`（hook の Python のファイル名））も同じ |
| Python を外すとき | hook の横展開で、Python と答えを合わせるために寄せなかったもの。実行後チェックの取り込みの確かめ（`integrate.SquashedPaths` の `copyIsBound`）は `BoundCheck` と違い、既定の置き場を大文字小文字を畳まずに見て、設定・全ツリー・退避を見ない（Python の `post_findings._ticket_bound` のまま）。取り込みの記録の読み方は `ReadRecord` の `FirstKept`（`post_findings`）と `LastKept`（`stop_integrate`）で分かれる。付き添いの有無は hook の判定（`hookio.Unattended`）と引き継ぎの待機（`hookio.Headless`。SDK の入口も含む）で分かれる。揃えるなら Python と同時に決める |
| Python を外すとき | entry の Python のファイルを指す説明文（`docs/21_design/diagnostics.md`（version.py の COMPAT）・`stop-review.md`（`entry/cli_stopreview.py`）・`startup-config.md`（`entry/cli_idle.py`）・`modules.md`（`entry/cli.py`）、`.agents/docs/next-step-placement.md`（`entry/status.py`）、`.ccnavi/messages/next-cli.yml` のコメント、`scripts/ccnavi-setup.sh`（`lint_project.py`・`lint.py` の `_in_index`・`_SH_SPECIAL`。「2 か所を直す」と書いた手作業の同期は Go 側の 1 か所に寄せる）、`.ccnavi/scripts/ccnavi-common.sh`、`extensions/vscode/ccnavi-board/src/core/version.ts`）を Go の名前に直す。Go のコメントに残る「Python の …」の参照も、移植が終わったところでまとめて直す |
| Python を外すとき | 利用者に見える文で、Python の組み立てを前提にしたもの。`--version` の「ソースで動いている」の表示（`lint` の `compatFix` は組み立てを Go に切り替えたときに `scripts/ccnavi-build.sh` の文に直した）。互換の版に関わるので、文をどう変えるかをユーザと合意してから直す |
| Python を外すとき | entry の横展開で、Python と答えを合わせるために直さなかったもの（どれも Python も同じ）。ツリーの中かの判定が大文字小文字を畳まない・`..foo` を外と見る（`lint/flow.go`・`lint/places.go`・`diagnose/explain.go`。`cli/options.go` の `inside` は畳む）。`cli/record.go` の書き出し先のリンクの確かめ、`cli/stopreview.go` の送った版の `.md` の判定、`lint/branch.go` の未登録のワークツリーの鍵、`lint/ticket.go` の承認済みの置き場の確かめが大文字小文字を区別する。親チケットの見方が `shask` と `lint/layers.go` の `holdsParent` で 2 通り。巨大な整数（`--limit`・`--mr`・`ops begin` の mr・`idle check` の待ち秒。Go は int と `time.Duration` に収まらないものを読めず、Python は巨大な値のまま使う。実害は無い）。揃えるなら Python と同時に直す |
| Python を外すとき | `git.Undo` の案内でパスを `"` で囲むだけの引用を、`config` の引用と同じ規則にする（`$` やバッククォートを含むパスで壊れる。いまは Python と同じ出力を保つため残す） |
| Python を外すとき | sync/handoff の `Apply` で、書いた後の読み直しに失敗したエントリが戻されない。catalog の `Problems` が文の頭にパスを付ける形（パスで始まらない文にも付く）、索引の使い回しの鍵が秒の mtime だけ（同じ秒の書き換えで前の frontmatter が残る）、`readHead` に失敗した md の既存の行が消える（コメントの「残す」と食い違う）。`infra/git` が環境から `GIT_DIR`・`GIT_WORK_TREE`・`GIT_INDEX_FILE`・`GIT_COMMON_DIR` を外さない（どれも Python も同じ。直すなら Python と同時に直す） |
| 余裕のあるとき | entry の横展開の残り。`lint/claude.go` の `executable` は `os.Stat` で権限を読む（`ospath.Stat` は権限を返さない。Python は `os.access`）。`suggest` の一時ディレクトリは `os.MkdirTemp`・`os.RemoveAll` のまま（infra に写しが無い）。`.md` と `.git` の直書きは entry と `tickets/approval`・`archive` だけを `place.MdExt`・`tree.DotGit` に寄せ、ほかの段（`catalog`・`records`・`tickets/lifecycle` など）は残る。数字だけの語の判定は `pyfmt.IsASCIIDigits` に寄せたが、`argparse` の `allDigits`（Unicode の数字・空も真）と `shell` の `isDigits`（Python の `isdigit` の表）は意味が違うので寄せていない |
| 当面扱わない | `infra/pyre` の照合を速くする（Python の C 実装の約 5 倍遅い）、照合の深さの上限（`DepthLimit`）を外す、`(?i)ß` などの小さな食い違い。中身は [yuki-matsu783/ccnavi#806](https://github.com/yuki-matsu783/ccnavi/issues/806) |
| 余裕のあるとき | `hint` の `recentCommit`（直前のコミットと見なす古さ、10 分）は期限ではなく 1 か所でしか使わないので `hint` に残した。`str.split()` の並びを返す `strings.FieldsFunc(…, ospath.IsSpace)` の直書き（wiki・selfguard・useronly・learning・failsafe など）を `ospath` の 1 関数に寄せる。`infra/git/restore.go` は `strings.Fields`（U+001C〜U+001F を空白に数えない）で Python と違いうる |

## 回す

Go の版は `go/go.mod` の `go 1.26.0` に合わせる。

```sh
cd go && go vet ./... && go test ./...
```

`go/cmd/ccnavi/main.go` は標準入出力と引数を `entry/cli` の `Run` に渡して終わるだけで、Python の `python -m ccnavi` と同じに動く。配る実行ファイルは `scripts/ccnavi-build.sh` が組む（[組み立てと配布物](build-and-dist.md)）。組み立てて Python の CLI のテストを当てるには次のようにする（版のコミットは `-ldflags "-X ccnavi/internal/entry/version.commit=<sha>"` で入れる）。

```sh
uv run python -m unittest tests.guard.test_permission_mode   # tests/exe.py が組む
CCNAVI_TEST_EXE=/tmp/ccnavi-go uv run python -m unittest tests.guard.test_permission_mode   # 組んだものを使う
```

依存の向きは `go/internal/layers_test.go` が見る。パッケージを足したら、先にその表（`topTiers` と、親のパスごとの `subTiers`）に行を足す。

依存は `go mod tidy` で整える。外の依存は `golang.org/x/text` と `github.com/BurntSushi/toml` だけにする。
