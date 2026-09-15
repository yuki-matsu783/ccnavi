---
version: 1
ticket: launcher-scripts-04
parent: launcher-scripts
phase: 1
predecessors:
- launcher-scripts-03
title: 設計を直す（包んだコマンドを外した形を、deny / ask とサブエージェントの禁止に当てる）
rationale: '実測（2026-09-13）で、組み込みの守りの多くがコマンドの先頭の語に固定されていて、

  `env` `FOO=1` `command` `exec` `nohup` `time` `sudo` `sudo -u me` `timeout 5` `nice
  -n 5`

  `stdbuf -o0` `/usr/bin/env` で包むと当たらないことが分かった。設定ファイルの守り

  （`rm` `mv` `tee` `sed -i` `cp` `truncate`）、チケットの状態の守り、承認の守りは ask に落ち、

  サブエージェントの禁止（`phase.forbidden`）は `env sh` `command sh` `/bin/sh` `sh -c` で素通りする。

  利用者のルールも、先頭に固定した書き方（`prefer-webfetch` など）は同じく抜ける。

  利用者の決定（2026-09-13）により、この穴を launcher-scripts の中でまとめて直す。

  `wip/design/launcher-scripts.md` に、shellread が各コマンドの包みを外した形を作り、

  deny / ask（組み込みと利用者のルール）とサブエージェントの禁止に当てる設計を書く。

  allow、ゲートの中で通す形（`phase.exempt`）、チケットの allow には当てない。

  D-8 の B（承認ルール専用のラッパの一覧）はこの仕組みに置き換える案として書き直す。コードには触らない。

  '
human_review:
  required: true
  reason: すべての Bash の判定で、ルールの当て方（どの形に当てるか）を変えるため
allow:
- match: Write|Edit
  glob: wip/design/*
ccnavi_approved:
  approved_at: 2026-09-13T21:08:40+0900
  source_tree: launcher-scripts
  source_path: /Volumes/Data/git/ccnavi/.claude/worktrees/launcher-scripts/wip/tickets/todo/launcher-scripts-04.md
started_at: 2026-09-13T21:12:25+0900
base_sha: a0359592dbc49507b90f8d954104fc3c98c38000
completed_at: 2026-09-13T21:18:39+0900
---

# 設計を直す（包んだコマンドを外す）

## 入力

- `wip/design/launcher-scripts.md`（launcher-scripts-03 まで）
- 実測の結果（`--test Bash … --json` を、このリポジトリの `rules.yml` と組み込みで打った表。設計書に写す）
- `ccnavi/shellread.py`（コマンドの切り分け、`_RUNS_A_STRING`、`_TAKES_CODE_FLAG`、読み切れない印）、
  `ccnavi/judge.py`（`screen`、ルールを当てる順、allow を読み切れない形に当てない扱い、`undeclared_verdict`）、
  `ccnavi/rules.py`（`Rule.matches`）、`ccnavi/selfguard.py`（`_WRITE_VERBS`、`_COPY_VERBS`）、
  `ccnavi/ticket.py`（`guard_rules`）、`ccnavi/phase.py`（`_FORBIDDEN_COMMAND`、`_EXEMPT_COMMAND`、`ticket_approval_rule`）、
  `tests/test_repo_rules.py`、`tests/test_shellread*.py`

## 成果物

`wip/design/launcher-scripts.md` に節を足し、関係する節を直す。

- **実測の表**: 組み込みの守り（設定ファイル・チケットの状態・承認・実行ファイル・記録・リダイレクト）、
  利用者のルール（`raw-git`・`recursive-delete`・`prefer-webfetch`）、サブエージェントの禁止、ゲートの中で通す形を、
  包み方ごとに並べる
- **外し方**: 包むコマンドの一覧（`env` `command` `exec` `nohup` `time` `nice` `sudo` `timeout` `stdbuf` など）、
  値を取るオプションの読み方（`sudo -u me`、`timeout 5`、`nice -n 5`、`env -u X`）、区切りの前の道筋（`/usr/bin/env`）、
  環境変数の代入（`FOO=1`）。一覧に無いものの扱い
- **読み切れない形**: `sh -c '…'` / `bash -lc "…"` の文字列を読み直すか、`xargs` と `find -exec` の後ろ、`.` / `source` の後ろを
  コマンドとして取り出すか。読み切れない印（allow を当てない、未宣言は ask）は残すこと
- **当てる先の線引き**: deny と ask（組み込みと利用者のルール）とサブエージェントの禁止には、元の形と外した形の両方を当てる。
  allow・ゲートの中で通す形・チケットの allow・`prefer-read-grep` のような「通すための」ルールには当てない。
  当てた場合に何が通るようになるか（`sudo cat x` など）を表で示す
- **当たりすぎ**: 外した形に当てることで、新しく止まる普通のコマンドを洗い出す（`time uv run pytest`、`nice make` など）
- **記録と文面**: どちらの形で当たったかを記録（`log.jsonl`）と拒否の文面に残すか
- **3.4 節と D-8**: 承認ルール専用のラッパの一覧（B）を、この仕組みに置き換える案に書き直す。置き換えたあとに残る穴を表にする
- **11〜14 節**: 変える入口（`shellread.py`・`judge.py`・`phase.py`・`selfguard.py`・`ticket.py`・`rules.yml` の見本）、
  受入テスト、ADR の骨子（判定の当て方の変更は ADR-0043 と分けるか）、REQ の候補
- **15 節**: 足した決定を D として並べる

## やらないこと

- コードとテストには触らない
- PowerShell（shellread で読めない）の扱いは変えない
- 15 節の D-1〜D-7 は変えない
