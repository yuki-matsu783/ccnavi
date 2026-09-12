---
version: 1
ticket: config-union-01
parent: config-union
phase: 1
title: 設計 §25 の改版を書く（設定 3 本の和）
rationale: |
  親チケット config-union の決定一覧を、設計書 §25 の改版として `wip/design/config-union.md`
  に書く。置き場・合成の規則・守るもの・見せ方・入れないもの・代償を、今の §25 の節立てに
  合わせて書き直し、既存の節のどこを差し替えるかを節番号で示す。実装の入口
  （settings / ruleload / phasetypes / risk / selfguard / diagnose / lint / setup.sh）ごとに、
  何を変えるかを 1 行ずつ添える。コードには触らない。
human_review:
  required: true
  reason: 判定の読み込み経路と自己保護の対象を変える設計のため
allow:
  - match: Write|Edit
    glob: "wip/design/*"
started_at: "2026-09-12T18:36:19+0900"
completed_at: "2026-09-12T18:48:47+0900"
base_sha: "bc55428dfb1a8fe09878b26401fa7e14032a441b"
---

# 設計 §25 の改版を書く

## 入力

- 親チケット `wip/tickets/todo/config-union.md` の決定一覧
- 今の `ccnavi.md` §25（25.1〜25.11）と、selfguard.py 冒頭の方針

## 成果物

- `wip/design/config-union.md`。§25 の改版。次を含む
  - 改版後の §25.2 置き場の表（共通層・自身の層・プロジェクトの層・固有スクリプト）
  - §25.4 ルールの節の差し替え（和、重複の排除、同 id の扱い、`self:` 接頭辞）
  - phases / risk の合成の規則（新しい節）
  - §25.6 守るものの差し替え（`.ccnavi/config/` の 3 本を中核に、`.ccnavi/scripts/` は組み込み deny）
  - §25.8 の `{root}` と `script:` の解決先
  - §25.9 診断と記録（`--explain` の層ごとの全件、`source` 欄、`--lint` の項目）
  - §25.11 見ないもの・入れないもの
  - 実装の入口ごとの変更点の一覧（1 行ずつ）
  - requirements.md に足す・直す REQ の候補（番号は付けない。文面だけ）

## やらないこと

- コードと文書本体（ccnavi.md / README.md）には触らない。写すのは docs フェーズ
- phases.yml の scope は変えない（人の持ち物）
