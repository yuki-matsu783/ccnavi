---
version: 1
ticket: refactor-296-rule-expectations-02-01
parent: refactor-296-rule-expectations
phase: 2
title: VS Code 拡張の「見本」を「期待する判定」に改める
rationale: |
  フェーズ 2。フェーズ 1 で変わった CLI と JSON の契約（旗・キー）に VS Code 拡張を合わせる。
  設定名・型名・関数名・画面の文言・フィクスチャを新しい名前に置き換える。
human_review:
  required: true
  reason: 拡張の設定名 ccnaviBoard.samplesPath という外に見える名前と、画面の文言を変えるため
allow:
  - match: Write|Edit
    glob: "extensions/vscode/ccnavi-board/src/*"
  - match: Write|Edit
    glob: "extensions/vscode/ccnavi-board/test/*"
  - match: Write|Edit
    glob: "extensions/vscode/ccnavi-board/package.json"
  - match: Write|Edit
    glob: "extensions/vscode/ccnavi-board/README.md"
  - match: Write|Edit
    glob: "tests/core/test_test_json.py"
started_at: "2026-10-11T17:35:42+0900"
completed_at: "2026-10-11T17:45:12+0900"
base_sha: "5801bfb65bc0ec7753c610d88d3360bce0724817"
---
## 目的

フェーズ 1 で CLI と JSON が次のとおり変わった。拡張をこれに合わせる。旧名の別名・読み替え・warn / error は入れない（親の「決定済みの点」）。

| 項目 | 旧 | 新 |
|---|---|---|
| CLI の旗 | `--test-samples` | `--test-expectations` |
| `--test-expectations --json` のキー | `samples` / `samples_path` | `expectations` / `expectations_path` |
| `--suggest --json` の候補のキー | `samples` | `expectations` |
| 拡張の設定 | `ccnaviBoard.samplesPath` | `ccnaviBoard.expectationsPath` |

日本語の文書・コメント・画面の文言は「期待する判定」（短くしてよい所は「期待」）、英語側は expectation(s) に揃える。

## 先に洗った結果（`grep -rniE "sample|見本" extensions/vscode`）

`extensions/vscode/ccnavi-board` で 51 ファイル・364 行が当たる（node_modules・out・dist を除く）。`extensions/chrome/` は Chrome 拡張のホスト応答の見本で、この子の対象外。

- 直す: 17 ファイル・98 行
- 残す: 266 行（うち 12 行は拡張の `docs/` で、フェーズ 4 が文書として仕分ける）

### 直す（17 ファイル）

| ファイル | 行 | 内容 |
|---|---|---|
| `src/ccnavi.ts` | 14 | `runSamples` → `runExpectations`、旗 `--test-expectations`、`parseSamplesJson` / `SamplesJson` の参照、エラー文言、コメント |
| `src/rules-panel.ts` | 17 | `DEFAULT_SAMPLES`・`samplesPath`・`samplesRel`・`samplesSetting`、設定キー `expectationsPath`、メッセージ名 `samples` / `sampled` |
| `src/core/rules-view.ts` | 6 | `samplesPath` の欄、メッセージ型 `sampled` / `samples`、`which: "samples"` |
| `src/core/testmodel.ts` | 9 | `SampleJson` → `ExpectationJson`、`SamplesJson` → `ExpectationsJson`、`parseSamplesJson` → `parseExpectationsJson`、`samples_path` / `samples` のキー |
| `src/core/suggestmodel.ts` | 5 | `SuggestSampleJson` → `SuggestExpectationJson`、`samples` のキー、コメント |
| `src/core/commands.ts` | 1 | コメントの `--test-samples` |
| `src/webview/rules/App.tsx` | 13 | 状態名 `sampled`、メッセージ名、`data-action`、ボタン・状態の文言、コメント |
| `src/webview/rules/Judge.tsx` | 13 | `SamplesResult` → `ExpectationsResult`、`result.samples`、map の変数 `sample`、`id="samples-result"`、コメント |
| `src/webview/rules/App.css` | 1 | クラス `.samples-head`（`App.tsx` と一緒に改める） |
| `package.json` | 1 | 設定 `ccnaviBoard.samplesPath` → `ccnaviBoard.expectationsPath`、説明文 |
| `README.md` | 2 | 設定表の行（24）、手動確認表 17 の項目名と文言（135） |
| `test/fixtures/samples.json` | 2 | `samples_path` / `samples` のキー。**ファイル名も `expectations.json` に改める** |
| `test/fixtures/suggest.json` | 4 | `samples` のキー 2 箇所と、YAML 下書きの文字列（`samples:`）2 箇所。ファイル名は変えない |
| `test/rules/testmodel.test.ts` | 6 | `parseExpectationsJson`、フィクスチャ名、`.samples`、テスト名 |
| `test/rules/suggestmodel.test.ts` | 1 | `.samples[0]` |
| `test/rules/rules.dom.test.ts` | 2 | コメント（`sampled`）、メッセージ本体のキー `samples: []` |
| `test/helpers/rules.ts` | 1 | ページの欄 `samplesPath` → `expectationsPath`（54 行目。**値は旧名のまま**） |

画面の文言は次のとおり改める（案）。

- 「サンプルを一括で判定」→「期待する判定を一括で確かめる」
- 「サンプルを判定中…」→「期待する判定を確かめ中…」
- README 17 の項目名「見本の一括」→「期待する判定の一括」

### 残す（266 行）

- `src/core/tour-sample.ts`（32）・`src/webview/Tour.css`（2）・`src/webview/{board,workflow,phases}/App.tsx`（28）: ボードの初回案内で出すダミー。ルールの期待ではない。
- `test/` の上の 17 ファイル以外（185 行）: `board`・`workflow`・`phases`・`flow`・`shared` などのテストと `helpers/*` の、案内用ダミーの `sample*`・試験の代役の変数・一般語の「見本」。
- `test/fixtures/samples.json` の 97 行目（1）: Python のテストが書く `why` の文言（「食い違いの見本」）。この子では触らない。
- `test/rules/rules.dom.test.ts` の 812 行目（1）、`test/helpers/rules.ts` の 24・45・70 行目（3）: 「誤りの見本」「見本のルール」という一般語の「見本」。
- `package.json` の `default`（1）、`README.md` の 175 行目（1）: 前者は旧ファイル名（下の固定）、後者は案内（吹き出し）の話。
- 拡張の `docs/`（12）: `design/structure.md`（7）・`design/screens.md`（2）・`requirements/rules.md`（2）・`requirements/workflow.md`（1）。フェーズ 4 で文書として直す。

`sample` の機械置換は使わない。上の「残す」を壊すため、ファイルごと・語ごとに当てる。

## 旧ファイル名の文字列はこの子で固定する

ファイル名の文字列 `.ccnavi/common/rule-samples.yml` は、フェーズ 3 の `mv` と同時に直す。この子では旧名のまま固定する。

- `src/rules-panel.ts` の `DEFAULT_EXPECTATIONS`（定数名は改めるが、値は `.ccnavi/common/rule-samples.yml` のまま）
- `package.json` の `ccnaviBoard.expectationsPath` の `default`
- `test/helpers/rules.ts` の `expectationsPath` の値
- `README.md` の設定表の「既定は …」（旧名のまま書く）

これで、フェーズ 2 だけが入った状態でも、拡張は旧名のファイルを既定で読み、実行ファイル（フェーズ 1）の `--test-expectations` は旧名のファイルをそのまま読める（フェーズ 1 の子で確認済み）。

## フィクスチャの改名と Python 側の追従

`test/fixtures/samples.json` を `expectations.json` に改める。この名前は Python 側の `tests/core/test_test_json.py`（290 行目の `_check_fixture("samples.json", …)` と 11 行目のコメント）が読むので、同じ区切りで 2 箇所だけ追従する。`tests/guard/test_suggest.py` は `suggest.json` を読み、ファイル名は変えないので触らない。

## 確かめること

- `extensions/vscode/ccnavi-board` で型検査・ビルドが通り、拡張のテストが通る。
- `python -m unittest tests.core.test_test_json tests.guard.test_suggest` が通る（フィクスチャの鍵が実行ファイルの出力と同じ）。
- `grep -rniE "samples?(Path|_path)|test-samples|parseSamplesJson|SamplesJson|SamplesResult|runSamples" extensions/vscode/ccnavi-board/src extensions/vscode/ccnavi-board/test extensions/vscode/ccnavi-board/package.json extensions/vscode/ccnavi-board/README.md` に残りがない。
- 手動確認（README の表 17）で、ルール管理画面の「期待する判定を一括で確かめる」が、旧名のファイルを読んで件数と食い違いを出す。
