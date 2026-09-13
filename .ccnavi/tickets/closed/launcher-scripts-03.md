---
version: 1
ticket: launcher-scripts-03
parent: launcher-scripts
phase: 1
predecessors:
- launcher-scripts-01
title: 設計を直す（承認の経路の間接起動、binary_clause と launched_executable の条件の食い違い）
rationale: '敵対的レビュー（2026-09-13、Sonnet のサブエージェント）で見つかった設計の穴を

  `wip/design/launcher-scripts.md` に反映する。承認の経路の直し（3.4 節、D-5）は `sh`/`bash` を

  前に付けた形しか塞がず、`/bin/sh`・`env sh`・`command`/`exec`・`zsh`/`dash`・`sh -c`・`.`/`source`
  が

  通る。`binary_clause`（3.2 節）は 3 段以上の綴りでしか新しい形にならず、名前だけで切り替える

  `launched_executable`（3.3 節）と食い違って、浅い綴りで実体への書き込みが deny されない。

  あわせて 10 節の確かめる 1 行（126 と 127 を区別できない）と、11 節の「--bin 系の 6 本」を直す。

  コードには触らない。

  '
human_review:
  required: true
  reason: 承認の経路のガードの形と、自己保護の条件を決め直すため
allow:
- match: Write|Edit
  glob: wip/design/*
ccnavi_approved:
  approved_at: 2026-09-13T20:22:46+0900
  source_tree: launcher-scripts
  source_path: /Volumes/Data/git/ccnavi/.claude/worktrees/launcher-scripts/wip/tickets/todo/launcher-scripts-03.md
started_at: 2026-09-13T20:26:39+0900
completed_at: 2026-09-13T20:41:13+0900
base_sha: ea9b231105dfb37231c40d222a722e3b12af0895
cancelled_at: ''
cancel_reason: ''
---

# 設計を直す

## 入力

- `wip/design/launcher-scripts.md`（レビュー済みの版）
- 敵対的レビューの指摘 1〜4（下に要点）
- `ccnavi/phase.py`（`ticket_approval_rule`、`_CLI_FORMS`）、`ccnavi/shellread.py`（読み切れない形の扱い）、
  `ccnavi/judge.py`（`screen` の生の文字列の経路）、`ccnavi/selfguard.py`、`ccnavi/platformtag.py`

## 指摘の要点

1. **承認の経路（3.4 節）**: `((sh|bash)\s+)?` を足しても、`/bin/sh <sh> --approve --yes`、`env sh`、
   `command sh`、`exec sh`、`zsh`、`dash` は通る。`sh -c '…'`、`. <sh>`、`source <sh>` は shellread が
   読み切れない形として生の文字列に落とし、先頭の一致が効かない
2. **`binary_clause`（3.2 節）**: `len(parts) >= 3` のときだけ新しい形。`CCNAVI_BIN_PATH=scripts/ccnavi-launcher.sh`
   では、`launched_executable` が起動する `bin/<os>-<arch>/ccnavi` への書き込みが deny されない（控えと復元は効く）
3. **10 節の確かめる 1 行**: `echo $?` では 126（実行ビットが無い）と 127（実体が無い）を区別できない
4. **11 節**: `tests/test_setup.py` で `--bin` を使うのは関数 9 本とループ 1 か所。「6 本」は誤り

## 成果物

`wip/design/launcher-scripts.md` を直す。

- 3.4 節: 間接起動の形を実際に当てて表にし直す。塞ぎ方の案を 2 つ以上、メリットとデメリットを添えて出し、
  推す案を決める。読み切れない形（`sh -c`・`.`・`source`）を、既存の「読み切れないものは通さない」の扱いと
  どう揃えるかを書く。止めすぎる形（`--preview`、`grep` や `cat` の引数に綴りが出るだけの形）も表に入れる
- 3.1〜3.3 節: `binary_clause` と `launched_executable` の切り替え条件を 1 つに揃える。1 段・2 段の綴りで
  それぞれ何を守るかを書き、「緩めるところは無い」の主張を当て直す
- 10 節: 確かめる手順を「実行ビット（`[ -x ]`）→ 起動の終了コード（126 / 127 の読み方）」に分ける
- 11 節・12 節: `--bin` を使うテストを数ではなく洗い出し方で書く。対応する S が無い
  `test_does_not_ignore_the_rules_that_sit_next_to_the_executable` の扱いを決める
- 12 節: 指摘 1・2 を固定するテスト（A と G）を足す
- 15 節: D-5 の得るもの・失うものを直し、変わった決定があれば D を足す

## やらないこと

- コードとテストには触らない
- 15 節の決まった D-1〜D-4、D-6 は変えない（変える必要が出たら利用者に聞く）
