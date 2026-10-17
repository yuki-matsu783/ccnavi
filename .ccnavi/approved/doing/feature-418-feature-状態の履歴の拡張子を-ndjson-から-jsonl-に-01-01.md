---
version: 1
ticket: feature-418-feature-状態の履歴の拡張子を-ndjson-から-jsonl-に-01-01
parent: feature-418-feature-状態の履歴の拡張子を-ndjson-から-jsonl-に
phase: 1
title: 状態の履歴の拡張子を .jsonl に揃える（実装とテスト）
rationale: |
  親の提案（issue 418）の実装とテスト。履歴ファイルの拡張子を .ndjson から .jsonl に変え、
  このリポジトリの .gitignore の *.jsonl を絞り、ADR と文書、ボード・Chrome 拡張、テストを新しい名前に揃える。
allow:
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
## 変更の内容

親の提案の「変更の内容」のとおり。

- `src/ccnavi/tickets/history.py` の `SUFFIX` を `.jsonl` にし、`c1.py`・`archive.py`・`approval_times.py` の `.ndjson` 直書きを `SUFFIX` 参照に寄せる。
- `.gitignore` の `*.jsonl` を外し、`/logs/` と `**/ccnavi-index.jsonl` に絞る。索引の項のコメントも直す。`git status` で他の `.jsonl` が追跡対象に出ないことを確かめる。
- ADR-0086 は今回に限り直接書き換える。ADR-0093・0101・0104、`README.md`、`docs/CONTEXT.md`、`docs/design/tickets/places.md`・`state-transitions.md`、`.ccnavi/docs/projects.md` の表記を揃える。
- ボード（`extensions/vscode/ccnavi-board`）と Chrome 拡張（`extensions/chrome/ccnavi-approval`）の実装・テスト・fixture・VERIFY.md を揃える。
- `tests/` の該当を新しい名前にする。
