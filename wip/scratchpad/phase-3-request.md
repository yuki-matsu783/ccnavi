フェーズ 3（refactor-296-rule-expectations-03-01）のレビュー依頼です。

## 変更
- `.ccnavi/common/rule-samples.yml` を `rule-expectations.yml` に改名（git mv、履歴は rename として追える）。判定の並びと件数は変えず、先頭コメントなどの「見本」の言い回しと自己参照の subject だけを直した（6 行）
- 旧名の文字列を新名に: src 3（configsync.py、suggest.py、cli_usage.py）、scripts/check_rules.py、tests 3、拡張 5（rules-panel.ts、package.json、README.md、test/helpers/rules.ts、test/fixtures/expectations.json の `expectations_path` と why）
- suggest.py の 1 行が 100 桁を超えたので 2 行に折り返した
- 13 ファイル、+27 -25

## 見てほしい点
- `.ccnavi/common/rule-expectations.yml` と旧ファイルの削除は、保護対象のため**ユーザが `git mv` と `cp` で行った**。ccnavi の範囲外の警告（POST_TICKET_SCOPE）が出ているのはこの 2 パスで、エージェントが書いたものではない
- `.env.sample` の名前を指す「見本」の語（5 行）は別の意味なので残した

## 残す／後で
- README・docs・スキル・ADR の言い回しはフェーズ 4

## テスト（今回起因の失敗なし）
- ruff の format / check は通った
- tests/config 316 件、tests/guard 331 件、拡張の `pnpm test` 545 件が通った
- tests/core 670 件中 1 件が失敗: `test_the_repo_expectations_match_the_rules`（`Write /repo/wip/scratchpad/a.md` が ask のはずが allow:worktrees になる）。変更前の親ワークツリーでも同じ。ルール側と期待のどちらを直すかは別件で、ユーザの判断
- tests/ticket 1088 件中 21 件が失敗（test_archive、test_branchfind、test_core、test_flow_hardening、test_phases、test_resume_review_warn、test_stale_proposals、test_sync_authority）。変更前の親ワークツリーで同じ 21 件が落ちることを確かめた
- `scripts/check_rules.py`（`rule-expectations.yml` を読む）: deny 60/60、ask 21/22、allow 28/28。ask の 1 件は上の a.md
