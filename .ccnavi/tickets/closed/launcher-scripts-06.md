---
version: 1
ticket: launcher-scripts-06
parent: launcher-scripts
phase: 4
predecessors:
- launcher-scripts-05
title: 写す版を作る（.ccnavi/scripts/ccnavi-launcher.sh と、人が直す 2 か所の手順）
rationale: '振り分けの sh は保護された置き場（`.ccnavi/scripts/`）に入るので、エージェントは書けない。完成品を

  `wip/design/scripts/ccnavi-launcher.sh` に全文で置き、写す手順と確かめる手順を `wip/design/scripts/COPY.md`
  に書く。

  人が直すと決めた `.gitignore`（D-4）と `.claude/skills/ccnavi-config/SKILL.md`（D-6）、implement
  の範囲の外にある

  `.claude/settings.json` の `CCNAVI_BIN_PATH`（利用者の決定、2026-09-13）の直し方も同じ手順書に入れる。

  '
human_review:
  required: true
  reason: hook が起動する sh の完成品と、写す順序（写す → 組み立てる → 確かめる → 開き直す）を固定するため
allow:
- match: Write|Edit
  glob: wip/design/*
- match: Write|Edit
  glob: tests/*
ccnavi_approved:
  approved_at: 2026-09-13T23:05:21+0900
  source_tree: launcher-scripts
  source_path: /Volumes/Data/git/ccnavi/.claude/worktrees/launcher-scripts/wip/tickets/todo/launcher-scripts-06.md
started_at: 2026-09-13T23:06:08+0900
completed_at: 2026-09-13T23:14:14+0900
base_sha: 7e5c90aff67e45f835639456bdafd57823af6160
cancelled_at: ''
cancel_reason: ''
---

# 写す版を作る

## 入力

- 設計 `wip/design/launcher-scripts.md` の 2 節（探す順、見つからないときの文面と 127、実行ビット）と 10 節（切り替えの順序と確かめる手順）
- フェーズ 3（launcher-scripts-05）で `CCNAVI_TEST_LAUNCHER` を使って緑を確かめた sh の下書き
- 前例 `sh-ws-root` の `wip/design/scripts/COPY.md`

## 成果物

- `wip/design/scripts/ccnavi-launcher.sh`: 完成品の全文。bash 3.2 と BSD の道具で動く書き方（CLAUDE.md の「実行環境」、`tests/test_sh_portability.py`）
- `wip/design/scripts/COPY.md`:
  1. 写す前に見る: `diff` で差を確かめる（新規なので「写す先が無い」と出るのが正しい）
  2. 写す: `cp`、`chmod +x`、git に入れるときにモード 100755 で入れる手順
  3. `.gitignore` に `/.ccnavi/bin/` を足す（D-4）
  4. `.claude/skills/ccnavi-config/SKILL.md` の 46〜47 行の綴りを直す（D-6。直したあとの文面を全文で載せる）
  5. ワークスペースルートで `build.py` を回し、`.ccnavi/bin/<この機械>/` に実行ファイルを置く
  6. 確かめる（設計 10 節の 3 段）: 実行ビット → git のモード → 起動の終了コード（126 / 127 の読み方）
  7. `CCNAVI_TEST_LAUNCHER=wip/design/scripts/ccnavi-launcher.sh` でフェーズ 2 の sh のテストを回す手順と、写したあとに名指し無しで回す手順
  8. `.claude/settings.json` の env `CCNAVI_BIN_PATH` を `dist/ccnavi/ccnavi` から `.ccnavi/scripts/ccnavi-launcher.sh` に直す。
     **1〜6 が済んでから**直す（sh と実行ファイルが揃う前に直すと、開き直したセッションの hook が 127 で起動しない）。直したあとの env の全文を載せる
  9. セッションを開き直す（env の `CCNAVI_BIN_PATH` の変更はここで効く）。開き直したら、hook が動いていること（`logs/log.jsonl` に新しい行が増える）を確かめる
- `tests/`: 写す版の sh を名指しで確かめる手順に要る直しがあれば、ここで足す（`tests/test_sh_portability.py` が `wip/design/scripts/` を見るか確かめる）

## やらないこと

- `.ccnavi/scripts/`・`.gitignore`・`.claude/skills/` を直接書く（人が写す）
- 実装（フェーズ 3）と文書（フェーズ 5）
