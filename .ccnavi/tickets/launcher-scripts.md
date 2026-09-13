---
version: 1
ticket: launcher-scripts
title: 振り分けの sh を .ccnavi/scripts/ に移し、このリポジトリも経由する
rationale: '振り分けの sh（今の scripts/ccnavi-launcher.sh）を .ccnavi/scripts/ccnavi-launcher.sh
  に移し、 配布先でも同じ綴りに置く。.ccnavi/bin/ は機械ごとの実行ファイルだけの置き場にする。 このリポジトリの hook も dist/ccnavi/ccnavi
  を直に起動するのをやめ、sh を経由する。 置き場が固定になるので --bin は廃止する。ADR-0041 の改版。

  '
human_review:
  required: true
  reason: hook の起動先・自己保護の対象・配布物の置き場を変えるため
plan:
- design
- acceptance
- implement
- staging
- docs
allow:
- match: Write|Edit
  glob: wip/design/*
- match: Write|Edit
  glob: docs/*
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
- match: Write|Edit
  glob: README.md
- match: Write|Edit
  glob: ccnavi.md
- match: Write|Edit
  glob: requirements.md
- match: Write|Edit
  glob: CLAUDE.md
- match: Write|Edit
  glob: .claude/settings.json
started_at: ''
completed_at: ''
base_sha: ''
ccnavi_approved:
  approved_at: 2026-09-13T19:09:37+0900
  source_tree: launcher-scripts
  source_path: /Volumes/Data/git/ccnavi/.claude/worktrees/launcher-scripts/wip/tickets/todo/launcher-scripts.md
---

# 振り分けの sh を .ccnavi/scripts/ に移し、このリポジトリも経由する

2026-09-13 のセッションで決めたことの控え。

## なぜ

- `.ccnavi/scripts/` は「原本と配布先が同じ綴りで、置いた場所でそのまま動く sh」の置き場。
  振り分けの sh は配布先の hook が必ず通るのに、原本は `scripts/` にあり、配布先では
  `.ccnavi/bin/ccnavi` と名前も場所も変わる
- `.ccnavi/bin/` は実行ファイルの置き場のつもりだった。sh が混ざるのは想定外
- このリポジトリの hook は `dist/ccnavi/ccnavi` を直に起動していて、振り分けの sh を通らない。
  同じフォルダを Windows と WSL から開くと片方で hook が起動しない（ADR-0041 が配布先で
  解いた問題がここに残っている）。sh の不具合にも自分では気づけない

## 骨

| 何 | 今 | 後 |
|---|---|---|
| 振り分けの sh（原本） | `scripts/ccnavi-launcher.sh` | `.ccnavi/scripts/ccnavi-launcher.sh` |
| 振り分けの sh（配布先） | `.ccnavi/bin/ccnavi`（`--bin` で動く） | `.ccnavi/scripts/ccnavi-launcher.sh`（固定） |
| 実行ファイル | `.ccnavi/bin/<os>-<arch>/`（`--bin` の隣） | `.ccnavi/bin/<os>-<arch>/`（固定） |
| `CCNAVI_BIN_PATH` | `.ccnavi/bin/ccnavi` | `.ccnavi/scripts/ccnavi-launcher.sh` |
| このリポジトリの hook | `dist/ccnavi/ccnavi` を直に | 配布先と同じく sh を経由 |
| `--bin` | あり | 廃止 |

- sh は自分の隣ではなく `$here/../bin/<os>-<arch>/` を探す
- 導入スクリプトは sh を `DEPLOY_SCRIPTS` に加えて他の sh と同じ手順で配る

## 変える場所（設計フェーズで詰める）

- `scripts/ccnavi-setup.sh`: `--bin` と `DEFAULT_BIN` の削除、sh を `DEPLOY_SCRIPTS` へ、
  実行ファイルの行き先を `.ccnavi/bin/<os>-<arch>/` に固定、移し替え（下記）
- `ccnavi/platformtag.py`: `launched_executable` の探し方を `../bin/` に
- `ccnavi/selfguard.py`: `binary_clause` の「隣の `<os>-<arch>/`」を、`.ccnavi/bin/` の下に直す。
  sh は `.ccnavi/scripts/` の組み込み deny で止まるが、`CCNAVI_BIN_PATH` の指す先としても守る
- `ccnavi/settings.py` / `ccnavi/lint.py`: 既定値、前の既定の綴りを warn
- `vscode-extension/ccnavi-board/src/ccnavi.ts` / `core/locate.ts`: 実行ファイルの探し方
- `build.py`: `dist/ccnavi/` に加え、このリポジトリの `.ccnavi/bin/<os>-<arch>/` にも写す
- `.claude/settings.json`: `CCNAVI_BIN_PATH` を `.ccnavi/scripts/ccnavi-launcher.sh` に（implement で直接書く）
- テスト: `test_launcher` / `test_setup` / `test_selfguard` / `test_config_union_guard` /
  `test_lint` / `test_ticket` / `test_review_origin`
- 文書: README / ccnavi.md / requirements.md、ADR-0041 を改める ADR

## 移し替え（配布済みのワークスペース）

- `CCNAVI_BIN_PATH` が前の既定（`.ccnavi/bin/ccnavi`、さらに前の `.claude/ccnavi/ccnavi`）なら
  新しい綴りへ書き換え、新しい置き場で起動できると確かめてから `.ccnavi/bin/ccnavi` を消す
- 既定でない綴り（前に `--bin` で動かした）は書き換えずに名指しで報告する。導入は止めない

## 人が写すもの（staging）

エージェントは書けないので、完成品を `wip/design/scripts/` に全文で置き、人が写す。

- `.ccnavi/scripts/ccnavi-launcher.sh`（新規）

`.claude/settings.json` は、このチケットに限り人の承認で implement が直接書く（allow に明記）。

## 今回入れないもの

- 実行ファイルを onefile にする形
- `CCNAVI_BIN_PATH` という env の名前の変更（指す先が sh になっても名前は据え置く）
- `ccnavi.settings.local.json` で `dist/` を直に起動する上書き（残すかは設計で決める）

## 受け入れる代償

- このリポジトリでも、ツール呼び出しのたびに sh の起動と `uname` 1 回が乗る
- ビルドのあとに `.ccnavi/bin/<os>-<arch>/` へ置くまで、hook は新しい実行ファイルを起動しない
- `--bin` で置き場を動かしていた配布先は、手で戻す必要がある
- 自己保護の対象が `.ccnavi/scripts/` と `.ccnavi/bin/` の 2 か所に分かれる

## 人が決めたこと（2026-09-13）

- このリポジトリの実行ファイルは `build.py` が `.ccnavi/bin/<os>-<arch>/` にも写す（案 1）。
  代償は build.py が ccnavi ディレクトリに書くこと。案 2（自分に setup.sh を当てる）は
  ビルドのたびに 1 手増えるので採らない
- 既定でない `CCNAVI_BIN_PATH` は報告だけにし、導入は止めない
- `.claude/settings.json` は今回に限りエージェントが書いてよい
