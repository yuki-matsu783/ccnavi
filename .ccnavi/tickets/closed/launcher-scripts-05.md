---
version: 1
ticket: launcher-scripts-05
parent: launcher-scripts
phase: 3
predecessors:
- launcher-scripts-02
title: 実装する（振り分けの sh の置き場と --bin の廃止、実行役のコマンドの中で実行されるコマンドへの当て方）
rationale: '`wip/design/launcher-scripts.md`（フェーズ 1、レビュー済み）と MR #30 の判断の記録に従って実装し、

  フェーズ 2 の受入テスト（launcher-scripts-02）を緑にする。振り分けの sh 本体（`.ccnavi/scripts/ccnavi-launcher.sh`）は

  保護された置き場なのでここでは書かず、フェーズ 4 で写す版を作る。そのため sh に依るテスト（L1〜L4 など）は、

  写す前の版を `CCNAVI_TEST_LAUNCHER` で名指しして確かめる。

  '
human_review:
  required: true
  reason: 判定の当て方（すべての Bash の判定）、自己保護の対象、導入スクリプトの移し替えを変えるため
allow:
- match: Write|Edit
  glob: ccnavi/*
- match: Write|Edit
  glob: tests/*
- match: Write|Edit
  glob: scripts/*
- match: Write|Edit
  glob: build.py
- match: Write|Edit
  glob: vscode-extension/*
ccnavi_approved:
  approved_at: 2026-09-13T22:18:50+0900
  source_tree: launcher-scripts
  source_path: /Volumes/Data/git/ccnavi/.claude/worktrees/launcher-scripts/wip/tickets/todo/launcher-scripts-05.md
started_at: 2026-09-13T22:25:25+0900
base_sha: c88c399a92df08a4f54c5c73f640b1a69e47951b
completed_at: 2026-09-13T22:58:23+0900
---

# 実装する

## 入力

- 設計 `wip/design/launcher-scripts.md`（特に 2〜5・7・9・10・11 節、3.5 節）
- MR #30 の判断の記録（D-1〜D-10、言い方の決定）
- フェーズ 2 の受入テストと、その依頼文に書いた「テストで決めたこと」「設計との食い違い」

## 入口ごとにやること（設計 11 節）

| 入口 | やること |
|---|---|
| `ccnavi/shellread.py` | 実行役のコマンドを外す処理と一覧（D-9 で組み込み）、値を取るオプション、`Reading.unwrapped`。層は外側から内側の順、深さ 4 は元の形を含めずに数える |
| `ccnavi/judge.py` | deny・ask とサブエージェントの禁止に中で実行されるコマンドも当てる。allow・exempt・チケットの範囲には当てない |
| `ccnavi/audit.py`・`ccnavi/reasons.py` | 記録と `--test` の JSON の欄 `unwrapped`、文面「`<実行役のコマンド>` が実行する `<中で実行されるコマンド>` に当たりました」 |
| `ccnavi/phase.py` | `forbidden` が中で実行されるコマンドも受け取る。承認のルールの正規表現は変えない |
| `ccnavi/platformtag.py`・`ccnavi/selfguard.py` | `LAUNCHER_NAME`、`launched_executable` と `binary_clause` の 2 つの形（名前だけで切り替える） |
| `ccnavi/settings.py`・`ccnavi/lint.py` | `OLD_BIN_PATHS`。lint の N1（前の既定の綴りで warn）と N2（実行できない sh で error）。**どちらも `.claude/settings.json` の env を読む**（`CCNAVI_MODE=disable` の検査と同じ読み方） |
| `scripts/ccnavi-setup.sh` | `--bin` の廃止、sh を `DEPLOY_SCRIPTS` へ、移し替え、`.gitignore`、まだ無いもの。**前の `.ccnavi/bin/ccnavi` を消すのは、env が前の既定の綴りだったとき（書き換えたとき）だけ**（設計 4.5 節の 4 条件に足す。S10 と揃える） |
| `scripts/ccnavi-launcher.sh` | 消す |
| `build.py` | `install(dist_dir, root, target)`。`dist_dir` は写す元のフォルダ `dist/ccnavi/` そのもの |
| `vscode-extension/ccnavi-board/src/core/locate.ts` ほか | 設計 7 節。拡張のテスト E1〜E3（`locate.test.ts`）もここで書く |
| `.claude/settings.json` | **ここでは書かない。** implement の範囲（`phases.yml`）に入っていないので、`CCNAVI_BIN_PATH` の 1 行は人が直す。手順はフェーズ 4（launcher-scripts-06）の `COPY.md` に、sh を写して組み立てて確かめたあとの段として書く（利用者の決定、2026-09-13） |

## 確かめること

- `uv run python -m unittest discover -s tests -t .` で、フェーズ 2 の受入テストが緑になる。sh に依るテストは、フェーズ 4 で写す予定の sh の下書きを
  スクラッチパッドに置き、`CCNAVI_TEST_LAUNCHER` で名指しして緑を確かめる（下書きはこのチケットではコミットしない）
- `pnpm test`（`vscode-extension/ccnavi-board`）で E1〜E3 が緑
- `ruff format --check .` と `ruff check .`
- 既存のテストで赤になるものが無い

## やらないこと

- `.ccnavi/scripts/ccnavi-launcher.sh` を書く（フェーズ 4 で写す版を作り、人が写す）
- `.gitignore` と `.claude/skills/ccnavi-config/SKILL.md`（D-4・D-6。人が直す）
- `.claude/settings.json` の `CCNAVI_BIN_PATH`（implement の範囲の外。人が直す。フェーズ 4 の `COPY.md`）
- 文書（README・ccnavi.md・requirements.md・ADR。フェーズ 5）
- 設計書のコード例の整形（`ruff format --check` が挙げる `wip/design/launcher-scripts.md`）は範囲の外。残るなら依頼文に書く
