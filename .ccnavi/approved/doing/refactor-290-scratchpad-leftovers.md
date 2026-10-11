---
version: 1
ticket: refactor-290-scratchpad-leftovers
title: scratchpad/ 廃止の取りこぼしを直す
human_review:
  required: false
  reason: 文言の置き換えだけで、判定や挙動は変えない
rationale: |
  refactor-288 で廃止した scratchpad/（無視される置き場）の記述が残っている場所を、wip/scratchpad/ に合わせる。
allow:
  - match: Write|Edit
    glob: "README.md"
  - match: Write|Edit
    glob: ".ccnavi/common/rule-samples.yml"
  - match: Write|Edit
    glob: ".claude/skills/*"
  - match: Write|Edit
    glob: "CLAUDE.md"
  - match: Write|Edit
    glob: "tests/*"
started_at: ""
completed_at: ""
base_sha: ""
---
## 背景

refactor-288（下書きの置き場を wip/scratchpad/ に一本化）の実装で、チケットの範囲に無く触れなかった場所がある。
288 の担当の報告に基づく。

## 直す場所

- `README.md`: 旧 `scratchpad/` の記述（363・1329・1607・1836・2088・2268 行付近）
- `.ccnavi/common/rule-samples.yml` 110 行付近の「scratchpad」
- `.claude/skills/` の commit・ccnavi-config・eli5・eli10・yomiyasu のうち、旧 `scratchpad/` を指している箇所
- `CLAUDE.md` の対応表: 「下書きや使い捨てのファイルを置く」の行（文言のままで成り立つなら変えない）
- reviewing スキルの references に「`wip/scratchpad/` 配下はレビュー対象外」を 1 行足す（references が無ければ common に作る。作り方は docs/claude/skill-concepts.md）

## 進め方

- 288 が取り込まれてから着手する（288 の変更と同じ文書に触れるため）
- 保護ファイル（`.claude/skills/` など）は、CLAUDE.md の手順どおり wip/scratchpad/ に写して直し、差し替えをユーザに頼む

## メリット

- 廃止した置き場の案内が残らず、読んだ人が `scratchpad/` に書いて迷うことが無くなる

## デメリット・リスク

- 保護ファイルが多く、差し替えの手間がユーザに掛かる
- 288 が長引くと、旧記述が残った状態が続く

## やらない場合

README と一部のスキルが、廃止した置き場を案内し続ける。
