フェーズ 5（refactor-296-rule-expectations-05-01、フィードバック計画の skill-improve）のレビュー依頼です。

## 変更（5 ファイル、+90 -1。2 コミット）
- `ai-asset`: `.claude/skills/commit/references/test-groups.md`
  - 表の `extensions/vscode/` の行に、`guard` の `test_suggest` も `test/fixtures/` を読む旨を足した
  - 「`e2e` は足さない」の段落の前に補足 1 段落: `src/` の `--json` キーや CLI の旗を変えるときは `core` と `guard` の両方を回す。拡張を別フェーズに分けるなら、前のフェーズでこの 2 本が落ちると分かったうえで分ける（直すのは fixtures を持つフェーズ）
- `docs`: `docs/wiki/` を新しく作成（`index.md`、`patterns/external-name-fixtures.md`、`skill-impact.md`、`log.md`）。`skill-impact.md` の採否は「承認待ち」

## 見てほしい点
- 補足の文面（規則 + なぜの形、日付・番号・チケット名を入れていない）
- wiki を初回に 4 枚作る方針（`docs/claude/skill-wiki.md` に従う）。パターンの内容
- サブエージェントが入れた本文外の 1 行（「src 側は戻さない」）は削除した

## 範囲の外
- `test-groups.md` が `references/` 直下にあり、概念スキルの `common/` / `project/` の分け方に合わない件は別件。今回は分け直さない
- `docs/claude/skill-review.md` と `skill-wiki.md` に「wiki が無いときは最初の提案で 4 枚を作る」の一文を足す案は、このフェーズでは扱っていない

## 確認
- 文書とスキルのみの変更。`ccnavi --lint` の error は、承認済みの親が別ツリーにあるための 1 件のみ（これまでのフェーズと同じ）
