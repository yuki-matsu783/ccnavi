フェーズ 1（refactor-296-rule-expectations-01-01）のレビュー依頼です。

## 変更
- 実行ファイル・scripts・tests の「見本 / sample」を「期待する判定 / expectation(s)」に改名（19 ファイル、+158 -147）
- CLI `--test-samples` を `--test-expectations` に置き換え。旧名は即廃止、別名なし
- JSON のキー: `--suggest --json` と `--test-expectations --json` の `samples` → `expectations`、`samples_path` → `expectations_path`
- ファイル名の文字列 `rule-samples.yml`（scripts/check_rules.py、configsync.py、tests/config/test_config_sync.py）は旧名のまま。フェーズ 3 で `mv` と同時に直す

## 見てほしい点
- 直さずに残した「見本」の当たり（ひな形・一般語・ホスト応答の fixtures など、約 50 件）が妥当か
- JSON のキー名の改名（チケット本文は suggest のキーのみ名指し。`--test-expectations --json` も揃えた）

## 既知の失敗（ユーザ了承済み）
- `tests.core.test_test_json`、`tests.guard.test_suggest` の 2 本。拡張の fixtures（`extensions/vscode/ccnavi-board/test/fixtures/samples.json`・`suggest.json`）が旧キーのため。フェーズ 2 で直す
- `test_the_repo_expectations_match_the_rules` は変更前のツリーでも落ちる（この改名と無関係）
- ticket グループの一部は、変更前との突き合わせをしていない

ruff の format / check は通っています。
