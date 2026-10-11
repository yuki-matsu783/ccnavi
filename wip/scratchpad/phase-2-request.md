フェーズ 2（refactor-296-rule-expectations-02-01）のレビュー依頼です。

## 変更
- VS Code 拡張（extensions/vscode/ccnavi-board）の「見本 / sample」を「期待する判定 / expectation(s)」に改名（18 ファイル、+111 -111）
- フェーズ 1 の CLI / JSON に合わせた: キー `samples` / `samples_path` → `expectations` / `expectations_path`、旗 `--test-expectations`
- 設定 `ccnaviBoard.samplesPath` → `ccnaviBoard.expectationsPath`（package.json、README）。旧名は読まない
- 画面の文言: 「期待する判定を一括で確かめる」「期待する判定を確かめ中…」。README の項目名「期待する判定の一括」
- fixtures: `test/fixtures/samples.json` → `expectations.json`、`suggest.json` のキー。`tests/core/test_test_json.py` の参照も追従
- webview のメッセージ名・data-action・CSS クラス・id は `expectations` 系に改名（チケットに指定が無く、サブエージェントが決めた）

## 見てほしい点
- 上のメッセージ名・CSS クラス・id の命名（`sampled` → `expectationsChecked`、`.expectations-head`、`expectations-result` など）
- 直さずに残した当たり（案内用ダミー `tour-sample.ts`、試験の代役、一般語の「見本」）が妥当か

## 旧名のまま固定したもの（フェーズ 3 で `mv` と同時に直す）
- `rule-samples.yml` の文字列: rules-panel.ts の既定値、package.json の default、test/helpers/rules.ts、README の「既定は …」
- `expectations.json` の `expectations_path` の値（`/ws\\samples.yml`）と、97 行目の `why`「食い違いの見本」は Python 側のテストが書く文言のため触っていない（フェーズ 3 または 4 で扱う）

## テスト
- 拡張: `pnpm test` 545 件が通った
- tests/guard 331 件が通った（フェーズ 1 から持ち越しだった `test_suggest` を含む）
- tests/core 670 件中 1 件が失敗: `test_check_rules.RepoExpectationsTest.test_the_repo_expectations_match_the_rules`。親ワークツリーの無変更状態でも同じ失敗で、この改名と無関係（`Write /repo/wip/scratchpad/a.md` が ask のはずが allow:worktrees になる）
- 持ち越しだった `test_test_json` は通った
- ruff の format / check は通っている
