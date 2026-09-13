---
version: 1
ticket: launcher-scripts-02
parent: launcher-scripts
phase: 2
predecessors:
- launcher-scripts-01
- launcher-scripts-03
- launcher-scripts-04
title: 受入テストを書く（振り分けの sh の置き場、導入スクリプトの移し替え、自己保護、承認の経路）
rationale: '`wip/design/launcher-scripts.md`（フェーズ 1、launcher-scripts-03 と -04 の直しを含めてレビュー済みの版）の

  12 節のうち Python 側の L / S / G / A / N / B と、中で実行されるコマンドの U / W を tests/ に書く。

  言い方は MR #30 の判断の記録に従う（「包み」ではなく「実行役のコマンド」「中で実行されるコマンド」）。実装（フェーズ 3）と

  写す版（フェーズ 4）が無い間は赤のまま置き、`expectedFailure` は付けない（config-union-02 と同じ）。

  拡張側の E は vscode-extension/ がこの種類の範囲に無いので、フェーズ 3 で実装と一緒に書く。

  '
human_review:
  required: true
  reason: 振り分けの sh の置き場と、移し替えで何を消してよいかの期待を固定するため
allow:
- match: Write|Edit
  glob: tests/*
ccnavi_approved:
  approved_at: 2026-09-13T21:39:05+0900
  source_tree: launcher-scripts
  source_path: /Volumes/Data/git/ccnavi/.claude/worktrees/launcher-scripts/wip/tickets/todo/launcher-scripts-02.md
---

# 受入テストを書く

## 入力

- `wip/design/launcher-scripts.md`（特に 2〜5 節、12 節、15 節の決定。launcher-scripts-03 で直した 3 節と 10〜12 節）
- 手本と、今の配置に依存している既存のテスト: `tests/test_launcher.py`、`tests/test_setup.py`、
  `tests/test_selfguard.py`（`launcher_layout`）、`tests/test_repo_rules.py`（144 行付近）、
  `tests/test_lint.py`、`tests/test_config_union_guard.py`（偽の配布元に sh を置く箇所）

## 成果物

| ファイル | 書くこと |
|---|---|
| `tests/test_launcher.py` | `LAUNCHER` を `.ccnavi/scripts/ccnavi-launcher.sh` に。設計 12 節の L |
| `tests/test_setup.py` | `--bin` を使うテストを `grep -n '"--bin"' tests/test_setup.py` と `grep -n 'def test_.*bin'` で**全部**洗い出し、設計 11 節の扱い（消す／S4 に置き換える／前提を変えて書き直す）に従う。汎用のループ（`--mode` / `--bin`）も含む。そのうえで設計 12 節の S |
| `tests/test_selfguard.py` | 設計 12 節の G。今の `launcher_layout` のテストは前の形として残す |
| `tests/test_repo_rules.py` | 設計 12 節の A（間接起動の形を含む） |
| `tests/test_lint.py` | 設計 12 節の N（実行できない sh の error は Windows では skip） |
| `tests/test_build.py`（新規） | 設計 12 節の B |
| `tests/test_config_union_guard.py` | 偽の配布元に sh を置く綴りを `.ccnavi/scripts/ccnavi-launcher.sh` に |
| `tests/test_shellread.py`（無ければ新規） | 設計 12 節の U1–U3（実行役のコマンドを外し、中で実行されるコマンドを取り出す） |
| `tests/test_repo_rules.py` | 設計 12 節の W1–W4、W6（中で実行されるコマンドに、組み込みと利用者の deny / ask が当たり、allow と exempt には当たらない） |
| `tests/test_phase.py` | 設計 12 節の W5（サブエージェントの禁止） |
| `tests/test_audit.py`（無ければ新規） | 設計 12 節の W7（記録と文面） |

## 言い方

設計書の「包み」「包みを外した形」は、テストの名前・コメント・文面の期待値では使わない。MR #30 の判断の記録のとおり、
「実行役のコマンド」（`env`・`sudo`・`timeout`・`sh -c`・`xargs` など）と「中で実行されるコマンド」と書く。
文面の期待値は「`<実行役のコマンド>` が実行する `<中で実行されるコマンド>` に当たりました」の形にする。

## 書き方

- 振り分けの sh は最終の綴り（`.ccnavi/scripts/ccnavi-launcher.sh`）を見る。人が写す前に確かめたいときのために、
  環境変数 `CCNAVI_TEST_LAUNCHER` があればそちらを読む（フェーズ 4 の `wip/design/scripts/ccnavi-launcher.sh` を名指しできる）
- 導入スクリプトのテストは今の `SetupTest` の作り（一時ディレクトリの配布元と配布先、偽の `uname`）を使う
- hook の payload はファイルに書いてから読ませる（禁止語をコマンドに書かない）
- 書き終えたら今の実装で走らせ、赤が「新しく足したテスト」と「設計が振る舞いを変えると決めた既存のテスト」だけで、
  それ以外が緑のままであることを確かめ、赤の一覧を依頼文に載せる

## やらないこと

- `ccnavi/`、`scripts/`、`build.py`、`vscode-extension/` には触らない
- 拡張の E（フェーズ 3）

## 判断が要るかもしれない点

1. **赤のままコミットする。** commit スキルはテストが通ることを求めるが、受入テストの子は前例どおり赤で置く。
   上の確認をしてからコミットする
2. **実行ビットに依るテストは Windows（Git Bash）で skip する。** macOS / Linux で見る
