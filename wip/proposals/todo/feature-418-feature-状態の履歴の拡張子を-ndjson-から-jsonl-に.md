---
version: 1
ticket: feature-418-feature-状態の履歴の拡張子を-ndjson-から-jsonl-に
issue: 418
mr: 421
title: "[Feature] 状態の履歴の拡張子を .ndjson から .jsonl に揃える"
phases:
  quick-work:
    kind: work
    title: 小さい作業
    review: mr
    scope: inherit
    when: 変更が 1〜2 ファイルに収まり、設計判断や並行作業が要らないとき
plan:
  - quick-work
rationale: |
  状態の履歴だけ拡張子が .ndjson で、ccnavi の他の JSON Lines の記録（logs/decisions.jsonl、ccnavi-index.jsonl）は
  .jsonl を使っている。同じ形式で拡張子が 2 種類あり、読み手が迷う。.jsonl に揃える（issue 418）。
allow:
  - match: Write|Edit
    glob: "src/*"
  - match: Write|Edit
    glob: "tests/*"
  - match: Write|Edit
    glob: "src/ccnavi/tickets/**"
  - match: Write|Edit
    glob: "tests/**"
  - match: Write|Edit
    glob: "extensions/**"
  - match: Write|Edit
    glob: "docs/**"
  - match: Write|Edit
    glob: "README.md"
  - match: Write|Edit
    glob: ".ccnavi/docs/projects.md"
  - match: Write|Edit
    glob: ".gitignore"
---
## 背景

issue 418。ADR-0086 は、`*.jsonl` を無視するリポジトリで履歴ファイルが git add から漏れるのを避けるため `.ndjson` を選んだ。
ユーザの判断（issue 418 のやり取り）で、他のリポジトリの `.gitignore` への手当ては考えない。互換も持たない。

## 改版の内容

計画を `implement` から `quick-work` に変える。`allow` は変えない。

承認済みの計画 `implement` の範囲は `src/*` と `tests/*` だけで、`allow` に書いた `docs/**`・`extensions/**`・`README.md`・
`.ccnavi/docs/projects.md`・`.gitignore` が承認しても書けなかった（子の提案の検査で分かった。原因は issue 424）。
`quick-work` は範囲が親の `allow` そのままなので、必要な範囲が書ける。変更の規模は 1〜2 ファイルを超えるが、設計判断は合意済みで、
拡張子の置き換えが中心のため、この型で足りる。

## 変更の内容

ユーザと合意済みの方針（改版前と同じ）:

- 履歴ファイルの拡張子を `.jsonl` にする。拡張子は `src/ccnavi/tickets/history.py` の `SUFFIX` の一箇所で決め、`c1.py`・`archive.py`・`approval_times.py` はそれを使う。
- 既存の `.ndjson` は読まず、移行も持たない（ADR-0115）。`--lint` の警告も足さない。
- 他のリポジトリの `.gitignore` への手当て（setup での例外追加・`git add -f`）はしない。
- このリポジトリの `.gitignore` は `*.jsonl` を外し、`/logs/` と `**/ccnavi-index.jsonl` に絞る。索引の項のコメントも直す。外したあと `git status` で、無視されていた他の `.jsonl` が追跡対象に出ないことを確かめる。
- ADR-0086 の「`.jsonl` にしない」判断は、今回に限り ADR-0086 を直接書き換えて改める。ADR-0093・0101・0104 の `.ndjson` の表記も揃える。
- 直す範囲: 実行ファイル（`src/ccnavi/tickets/`）、ボード拡張 `extensions/vscode/ccnavi-board/src/core/model.ts` と fixture、Chrome 拡張とそのテスト・fixture・VERIFY.md、`tests/` の該当、`README.md`、`docs/CONTEXT.md`、`docs/design/tickets/places.md`・`state-transitions.md`、`.ccnavi/docs/projects.md`。

## 計画の理由

小さい作業の型 1 項にまとめる。拡張子の置き換えが中心で、設計の分岐は合意済みのため。
