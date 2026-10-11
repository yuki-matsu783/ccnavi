---
version: 1
ticket: feature-207-subagent-phase-session-rejects-orphan-worktree
issue: 207
mr: 313
title: SubagentStart の局面・元のプロジェクトが無いワークツリーの名指し・セッション開始の却下提示・期限の実測
rationale: |
  HANDOVER.md の「未実装」「未了」から移した 4 件の課題(#203 #205 #207 #210)を 1 本の MR でまとめて直す。
  どれも判定は変えず、案内と診断の文面を足すか、これまで無かった測定を足す。
human_review:
  required: true
  reason: SessionStart の出力と --lint / --explain の文面が変わる(ユーザとのやり取りの形)ため
phases:
  acceptance:
    kind: work
    title: 受入テスト作成
    review: mr
    scope:
    - tests/*
    when: 振る舞いが変わるとき。実装と並行してよい（実装の項の `after` にこの項を書かなければ並行する）
  implement:
    kind: work
    title: 実装とテスト
    review: mr
    scope:
    - src/*
    - tests/*
    - build.py
    - pyproject.toml
    - scripts/*
    - .ccnavi/*
    - .ccnavi/common/phases.yml
    - extensions/*
    when: 振る舞いを変えるとき。受入テスト作成を先に置く（計画に受入テスト作成の項も置く）
  docs:
    kind: work
    title: 文書
    review: mr
    scope:
    - README.md
    - docs/*
    - extensions/vscode/ccnavi-board/docs/*
    - extensions/chrome/ccnavi-approval/docs/*
    - CLAUDE.md
    - .claude/skills/*
plan:
  - acceptance
  - {type: implement, after: [1]}
  - {type: docs, after: [2]}
allow:
  - match: Write|Edit
    glob: "src/ccnavi/*"
  - match: Write|Edit
    glob: "tests/*"
  - match: Write|Edit
    glob: "docs/*"
  - match: Write|Edit
    glob: "README.md"
started_at: "2026-10-12T03:09:43+0900"
completed_at: ""
base_sha: "8548fc14bfe6a07a792443d35f8c15550610d23f"
---
Closes #203
Closes #205
Closes #210

(`issue:` は 1 件しか書けないので、親の識別子と同じ #207 を欄に置き、残りは本文の Closes で結ぶ。)

## 4 件の方針(調査の結果と、ユーザの決定)

| 課題 | 何をするか | 判定が変わるか |
|---|---|---|
| #207 | `SubagentStart`(`hook/subagent.py` の `at_start`)が、子の行の「親 X、フェーズ N: タイトル」に続けて、親の局面を `phase.stage` で求めて案内文に足す。実装の局面の名前は変えない | 変わらない |
| #210 | 元のプロジェクトが無いワークツリー(プロジェクトを消したあとも残った、元のプロジェクトを失ったワークツリー)を `--lint`(warn)と `--explain`(1 節)が名指しする。列挙(`tree.worktrees`)と判定は変えない。`--explain --json` に欄は足さない | 変わらない |
| #203 | `SessionStart` の出力に、却下された要求を要求・理由・出所つきで示す(REQ-SES-02)。情報源は承認時の記録ではなく、承認済みチケットとルールからの再計算。REQ-SES-03 は作らず、要件書から外す | 変わらない(案内のみ) |
| #205 | 期限(`judge.DEADLINE_SECONDS`)を、決定的なテストと実測用の bench で測る。守れていない入力が見つかれば、直さずに報告して別の課題にする | 変わらない |

## 決定済みの論点

- #203 / REQ-SES-03: 作らない。上限を超えた許可は判定時に切り詰められて効かず、REQ-SES-02 と区別が付かないため。
  #203 は REQ-SES-02 の実装で閉じる。要件書 `docs/requirements/session-start.md` から REQ-SES-03 を外し(欠番の書き方は REQ-SES-04 の先例)、
  参照している文書(`docs/requirements/acceptance.md` の受け入れ条件 2 の REQ-SES-03 と「想定範囲外への許可」)も直す。文書フェーズで行う。
- #207 / 局面の名前: 実装は変えない。要件・設計・用語集の「閉じられる」を実装の「クローズ可」に合わせる(`クローズ可`、`クローズ可（フィードバック対応済み）`、早めに閉じた場合は `閉じられる（ユーザが早めに閉じた）` のまま実装に合わせる)。
  「全体計画待ち」は SubagentStart では起きず、実装にも無いので、SubagentStart では出さず、文書の一覧からも外す(REQ-TKT-35 の文言を含む)。文書フェーズで行う。
- #210 / 名指しの出し方: `--lint` は warn、`--explain` は 1 節、`--explain --json` に欄は足さない。
  文面の案(`--lint`): 「ワークツリー <名前> は元のプロジェクトが無い(<gitdir> の先が見つからない)。このパスはワークスペースルートとして判定され、そのプロジェクトの deny が効いていない。ワークツリーを消すか、プロジェクトを戻してください」。
  `--explain` は見出し「■ 元のプロジェクトが無いワークツリー」の下に、同じ内容を 1 件 1 行で出す。

## 子の出し方

計画は 1 受入テスト作成 → 2 実装とテスト → 3 文書。次のフェーズの子は、前のフェーズが閉じてレビューが済むまで承認の対象にならないので、
今回の提案に入れるのはフェーズ 1 の子(-01-01 #207、-01-02 #210、-01-03 #203、-01-04 #205)だけ。
フェーズ 2(#207 #210 #203 の実装)とフェーズ 3(文書 1 本)の子は、フェーズ 1 が閉じてから出す。

## 影響範囲と注意

- 出力が変わるのは `SessionStart`・`SubagentStart` の `additionalContext`/`systemMessage` と `--lint` / `--explain` の文面。ルール・範囲・承認の判定は変えない。
- `SessionStart` の文は起動・再開・compact・clear のたびに常駐の文脈へ入る(REQ-CMN-12)。件数と先頭の数件に絞り、詳細は `--explain` で引かせる。
- 文書フェーズで要件書(REQ-SES-03 の削除、REQ-TKT-35 の局面の一覧)を変える。要件の文言の変更なので、そのフェーズのレビューでユーザが確かめる。
- 親の識別子は 58 字で、48 字超の warn が出る(既存のワークツリー名をそのまま使うため)。子の識別子は上限の 64 字ちょうど。
