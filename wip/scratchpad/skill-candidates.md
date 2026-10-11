# スキル候補（refactor-296-rule-expectations）

## 1. commit スキル test-groups.md: JSON キー・CLI 旗の改名は拡張の fixtures を読むテストを落とす

- 対象: `.claude/skills/commit/references/test-groups.md`（直す）
- 中身の下書き: 「`src/` の `--json` 出力のキーや CLI の旗を変えると、`extensions/vscode/ccnavi-board/test/fixtures/` を読む Python のテスト（`tests/core/test_test_json.py`、`tests/guard/test_suggest.py`）が落ちる。拡張を別フェーズに分けるときは、前のフェーズでこの 2 本が落ちると分かったうえで計画する。回すグループは `core` と `guard` の両方」
- 根拠: フェーズ 1（src・scripts・tests）で改名したところ、拡張の fixtures が旧キーのため 2 本が落ち、フェーズ 2 で fixtures を直して通った。サブエージェントの報告にも同じ候補があった
- wiki への反映: 未（フィードバック計画の前に、`docs/wiki/` のパターンへ溜める）
