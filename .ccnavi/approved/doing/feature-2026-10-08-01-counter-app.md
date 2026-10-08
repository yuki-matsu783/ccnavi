---
version: 1
ticket: feature-2026-10-08-01-counter-app
title: 簡単なカウンタアプリの追加
description: 'ボタンで数を増減できるシンプルなカウンタアプリを作る。

  設計・受入テスト・実装の一連の流れをチケットで確認するためのサンプル。

  '
rationale: '## この親チケットで対応する内容


  カウンタアプリの実装を通じて、ccnavi のチケット制御フローを一通り確認する。


  ### フェーズ構成

  1. **design** - 仕様・画面設計・API 設計

  2. **acceptance** - 受入テスト作成（増減・リセット・境界値）

  3. **implement** - React コンポーネント実装とテスト


  ### 成果物

  - `src/counter/Counter.tsx` - カウンタコンポーネント

  - `src/counter/Counter.test.tsx` - 単体テスト

  - `wip/design/counter-app.md` - 設計書

  '
plan:
- type: design
  review: mr
- type: acceptance
  review: mr
- type: implement
  review: mr
project: ''
branch: feature-2026-10-08-01-counter-app
allow:
- match: Write|Edit
  glob: src/counter/*
- match: Write|Edit
  glob: wip/design/*
- match: Write|Edit
  glob: docs/*
started_at: 2026-10-08T06:54:35+0900
base_sha: 236064c9fbc9e07427e550574e4e451d046fbcbc
---
