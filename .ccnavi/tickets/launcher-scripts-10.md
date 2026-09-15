---
version: 1
ticket: launcher-scripts-10
parent: launcher-scripts
phase: 5
predecessors:
- launcher-scripts-07
title: 文書を直す（フェーズ 5 の敵対的レビューの指摘）
rationale: 'launcher-scripts-07 の文書と、あとから足した写す作業の 3 コミットを sonnet で敵対的にレビューし、実装と `--test`
  の実測で

  文書の食い違いが 2 件見つかった。どちらも文書を実装に合わせて直す。コードは変えない。

  '
human_review:
  required: true
  reason: 判定のコードの説明（ccnavi.md・ADR-0044）を実装に合わせるため
allow:
- match: Write|Edit
  glob: ccnavi.md
- match: Write|Edit
  glob: docs/*
ccnavi_approved:
  approved_at: 2026-09-15T17:13:42+0900
  source_tree: launcher-scripts
  source_path: /Volumes/Data/git/ccnavi/.claude/worktrees/launcher-scripts/wip/tickets/todo/launcher-scripts-10.md
---

# 文書を直す（フェーズ 5 の敵対的レビュー）

## 直すこと

| # | 重大度 | 箇所 | 今の文面の問題 | 直し方 |
|---|---|---|---|---|
| 1 | 中 | `ccnavi.md` §6.3.1「コードと文面」、`docs/adr/0044-inner-commands.md`「決定」 | 「1 件でも元の読み切れない形で当たっていれば `PARSE_UNCERTAIN` のまま」と書いたが、承認のルール（`builtin-guard-ticket-approval`）が当たった群では、`judge.py` が読み切れたかどうかに依らず `DENY_TICKET_APPROVAL_CLI` に上書きする（今回より前からある挙動。実測: `true; ccnavi --approve --yes x 'unterminated` → `code: DENY_TICKET_APPROVAL_CLI`・`degraded: unterminated-quote`・`unwrapped: ""`） | 例外として「承認のルールが当たったときは、元の形が読み切れなくても `DENY_TICKET_APPROVAL_CLI` を優先する（判定は deny で変わらず、読み切れなかった断りの `note:` は付く）」を足す。書く前に `--test` で実測し直す |
| 2 | 低 | `ccnavi.md` 付録 B（記録の欄） | 一覧に `source` が無く、「全 21 欄」は実際は 22 欄（`audit.py` の `_as_dict`）。抜けは前からだが、今回 `unwrapped` を足すときに触った行 | 一覧に `source` を足し、欄の数を実装から数え直して書く |

## 採らなかった直し方

- 1 を実装側で直す（`judge.py` の上書きを読み切れたときに限る）。承認のルールに当たったときのコードの出方を変えることになり、文書のフェーズの範囲を超える。判定（deny）は今のままで正しいので、文書を実装に合わせる

## 確かめること

- 1 の例と、読み切れる形の承認（`env sh .ccnavi/scripts/ccnavi-launcher.sh --approve --yes x`、`CCNAVI_BIN_PATH` を新しい綴りで渡す）、読み切れない形で承認以外のルールに当たる例を `uv run python -m ccnavi --test Bash '<…>' --json --log "" --state ""` で出し、直した文面と一致する
- 付録 B の欄の数が `audit.py` と一致する
- README・requirements.md・ADR-0044 に、1 と同じ言い切りが他に残っていない（`grep -n PARSE_UNCERTAIN`）

## やらないこと

- コード・テスト・README・requirements.md の直し（上の grep で同じ言い切りが見つかった場合は、範囲に入る `docs/*` だけを直し、README・requirements.md にあれば依頼文に書く）
