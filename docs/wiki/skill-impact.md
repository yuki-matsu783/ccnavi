---
title: スキル提案の採否
type: learning-wiki
description: スキルへの提案ごとの対象・要点・差分・採否の記録
tags: [skills, feedback]
keywords: [提案, 採否, 却下, 差分, commit, test-groups, testing, designing, reviewing, purpose]
---

# 提案の記録

## commit/references/test-groups.md に fixtures の規則を足す

- 対象: `.claude/skills/commit/references/test-groups.md`
- 要点: `--json` のキーや CLI のオプションの改名では `core` と `guard` の両方を回す。拡張の fixtures を読む Python のテスト 2 本が落ちるため。表の `extensions/vscode/` の行も強める
- 動機のパターン: [external-name-fixtures](patterns/external-name-fixtures.md)
- 採否: 承認待ち（採否はユーザが決める）

差分:

```diff
-| `extensions/vscode/` | `ticket`（`core` の `test_test_json` も例を読む） |
+| `extensions/vscode/` | `ticket`（`core` の `test_test_json` と `guard` の `test_suggest` も `test/fixtures/` を読む） |
```

```diff
+`src/` の `--json` 出力のキーや CLI のオプションを変えるときは、`core` と `guard` の両方を回す。`extensions/vscode/ccnavi-board/test/fixtures/` を
+Python のテスト（`tests/core/test_test_json.py`・`tests/guard/test_suggest.py`）も読むので、fixtures の旧キーのままだと落ちる。
+拡張を別のフェーズに分けて計画するなら、前のフェーズでこの 2 本が落ちると分かったうえで分ける（直すのは fixtures を持つフェーズ）。
+
 `src/ccnavi/**/*.py` を変えたとき `e2e` は足さない（e2e は組み立て済みの実行ファイルを試す）。組み立て直したなら足す。
```

## commit の git の入口を絶対パスにし、既知の失敗がある間の回し方を足す

- 提案: `chore-208-skill-commit-git-entry-tests`
- 対象: `.claude/skills/commit/SKILL.md`・`.claude/skills/commit/references/test-groups.md`・`.claude/skills/commit/references/purpose.md`（新規）
- 要点: ワークツリーや `projects/<名前>` の中では ccnavi-git.sh を絶対パスで呼ぶ。既知の失敗がある間はグループごとに回し、bench は名指しでバックグラウンド、全件は最後の子を finish する前
- 動機のパターン: [ccnavi-git-relative-path](patterns/ccnavi-git-relative-path.md)、[test-run-known-failure](patterns/test-run-known-failure.md)
- 採否: 承認待ち（採否はユーザが決める）

差分:

```diff
 git は直接呼ばず、`ccnavi-git.sh` 経由で打つ（生の git は止められるため）。以下の例はワークスペースルートの形で書く。
-ワークツリーの中では先頭を `sh ../../../.ccnavi/scripts/ccnavi-git.sh` にする。cwd ごとの呼び方は `.ccnavi/docs/worktree.md` の「基本」を見る。
+ワークツリーや `projects/<名前>` の中では、先頭を `sh <ワークスペースルートの絶対パス>/.ccnavi/scripts/ccnavi-git.sh` と直書きする。
+相対パス（`../../../`）は cwd がワークツリーの最上段のときしか届かないため。コマンド名やパスを変数に入れない（`DENY_COMMAND_NAME_EXPANSION` で止まる）。
```

```diff
 落ちたら新しいプロセスを起こすのをやめ、落ちた 1 本の出力だけを出す。ターンの終わりの hook はこれを使わない。
+既知の失敗が残っている間は `python -m tests` で全件を見られないので、グループごとに `discover -s tests/<グループ> -t .` で回し、失敗の数を既知と比べる。
+
+`tests/<グループ>/bench_*.py` はテストではなく、`uv run python -m tests.core.bench_deadline` のように名指しで回す。
+実ルールを使う bench は `*x*` 形の glob の照合が入力の長さの 2 乗で伸び、小さい入力でも数分かかる。ターンの中で回すならバックグラウンドで回す。
```

```diff
-- 統合先に取り込む前、MR に出す前
+- 統合先に取り込む前、MR に出す前（親の最後のフェーズの子を finish する前に済ませる。レビュー準備中はシェルの実行が止まるため）
```

## testing に手動の probe の reference を足す

- 提案: `chore-209-skill-testing-manual-probes`
- 対象: `.claude/skills/testing/references/project/manual-probes.md`・`purpose.md`（どちらも新規）
- 要点: probe を回す前に前提を確かめ、止まった理由まで見る。前提を直したら docstring も直す
- 動機のパターン: [stale-manual-probe](patterns/stale-manual-probe.md)
- 採否: 承認待ち（採否はユーザが決める）

差分（新規ファイルの本文）:

```diff
+- 回す前に、probe の手順が今の ccnavi の前提に合っているかを読む（一時リポジトリへの `.ccnavi/scripts/` の配置、親を先に start、`review request` の `--eli5`、`decide` の入力など）。`tests/manual/` は自動テストが呼ばないので、仕様が変わっても落ちずに古くなるため
+- 「止まる」ことを確かめる段は、止まったことだけでなく、止まった理由（拒否の文面やコード）が期待どおりかまで見る。前提の崩れで別の理由で止まっても PASS に見えるため
+- 前提を直したら、probe の docstring の「用意するもの」も同じ変更で直す
```

## designing に診断の走査と、内部の呼び名の reference を足す

- 提案: `chore-210-skill-designing-scan-and-terms`
- 対象: `.claude/skills/designing/references/project/diagnostic-scan.md`・`.claude/skills/designing/references/common/plain-terms.md`・各 `purpose.md`（すべて新規）
- 要点: 診断の走査は 1 度読んだコピーを渡し、警告を足したら合流後に `tests/ticket` を回す。説明では内部の呼び名を使わず、状態を言葉で書く
- 動機のパターン: [diagnostic-scan-read-once](patterns/diagnostic-scan-read-once.md)、[internal-names-in-prose](patterns/internal-names-in-prose.md)
- 採否: 承認待ち（採否はユーザが決める）

差分（新規ファイルの本文。`project/diagnostic-scan.md`）:

```diff
+- `--explain` と `--lint` に走査を足すときは、承認済みチケットの置き場を読み直さず、1 度読んだコピー（copies）を走査に引数で渡す。置き場は 1 回の起動で 1 度だけ読む約束で、`tests/ticket/test_raw_share.py` が読んだ回数を数えて固定しているため
+- 診断に新しい警告を足したら、並行した子を合流した後に `tests/ticket` を全件回す。`test_lint_ops` には名前で警告の行を拾うテストがあり、別の子の足した警告を取り違えて、合流後にだけ落ちるため
```

差分（新規ファイルの本文。`common/plain-terms.md`）:

```diff
+- issue のタイトルや作業中の呼び名（比喩・略語）を、提案・設計書・ユーザへの説明にそのまま使わない。書いた人と issue を読んだ人にしか通じないため
+- 何が起きている状態かを言葉で書く（例: 「孤児のワークツリー」ではなく「元のプロジェクトが無いワークツリー」）
+- 短い呼び名を使い続けたいときは、初出で定義を添える。用語集に入れるかは別に判断する
```

## reviewing に既存の失敗を確かめる reference を足す

- 提案: `chore-211-skill-reviewing-preexisting-failure`
- 対象: `.claude/skills/reviewing/references/common/preexisting-failures.md`・`purpose.md`（どちらも新規）
- 要点: 「既存の失敗」と書く前に統合先で再現を確かめ、どの ref で再現したかを添える
- 動機のパターン: [preexisting-failure-misattributed](patterns/preexisting-failure-misattributed.md)
- 採否: 承認待ち（採否はユーザが決める）

差分（新規ファイルの本文）:

```diff
+- 「既存の失敗」と書く前に、変更前の状態（統合先）で同じテストが落ちるかを確かめる。親のブランチに他の子の変更が入った後で見た失敗は、他の子が原因のことがあるため
+- 報告やレビューで「既存」と扱うときは、どの ref で再現したかを添える
```
