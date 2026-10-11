フェーズ 4（refactor-296-rule-expectations-04-01）のレビュー依頼です。最後のフェーズです。

## 変更（18 ファイル、+115 -75。2 コミット）
- `ai-asset`（4 ファイル）: スキル `ccnavi-config`（SKILL.md、references/check.md、references/add.md）、`commit/references/test-groups.md`
- `docs`（14 ファイル）: README、docs/design 6 本、docs/requirements 2 本、拡張の docs 3 本、docs/adr/README.md（一覧に 1 行）、新規 `docs/adr/0114-rule-expectations-rename.md`
- 言い回し: 日本語は「期待する判定」（短くしてよい所は「期待」）、英語側は `rule-expectations.yml` / `expectations:` / `--test-expectations` / `expectationsPath`
- 拡張の `screens.md` のメッセージ名 `samples` / `sampled` は、コードを読んで `expectations` / `expectationsChecked` に合わせた

## 見てほしい点
- 新 ADR-0114 の決定（旧名の別名は残さない、日本語は「期待する判定」、過去の ADR は書き換えない）と、番号（0113 の次）
- 残した「見本」11 行が別の意味か: 計画の見本の表（tickets/phases.md 3 行、approval.md 1 行）、拡張 structure.md の振り直しの見本の表、ホスト応答の見本（requirements/approval.md）、初回案内のダミー（structure.md、workflow.md）、ボードの JSON の見本ファイル（structure.md、screens.md）
- `docs/adr/README.md` の 0065 の行（`rule-samples.yml` から引かれている）は、「数は動かした時点のもの」という表の方針に従い書き換えていない

## 範囲の外（触っていない）
- 過去の ADR の本文（0016、0032、0035、0042、0051、0067）
- コード側のコメントに残る「見本 → 判定」（tests/guard/test_repo_rules.py の 5・97・352 行）

## 確認
- 旧名の grep（rule-samples、--test-samples、samplesPath、samples:）は、README・docs（adr を除く）・.claude/skills・拡張の docs で 0 件
- `tests.core.test_adr_numbers` は通った（3 件）
- 文書のみの変更

## 別件（この改名の外）
- フェーズ 1〜3 で報告したとおり、`test_the_repo_expectations_match_the_rules`（`wip/scratchpad/a.md` が ask のはずが allow:worktrees）と、tests/ticket の 21 件は、変更前から落ちている。直す向きは別途ユーザの判断
