---
version: 1
ticket: feature-207-subagent-phase-session-rejects-orphan-worktree-04-01
parent: feature-207-subagent-phase-session-rejects-orphan-worktree
phase: 4
title: feature-207 の振り返りで拾ったパターン 6 件を wiki に足す
rationale: |
  足す。サブエージェントの報告とメインの作業の失敗から拾った 6 件を `docs/wiki/patterns/` のページにし、`index.md`・`skill-impact.md`・`log.md` に載せる。
  スキルの変更そのものは、親の範囲（src/ccnavi/*・tests/*・docs/*・README.md）に `.claude/skills/*` が無いので、
  `docs/claude/skill-review.md` の「候補の渡し方」（親の範囲に置き場が無い → 提案チケット）に従い、`skill-proposals` のツリーの提案チケット 4 本で出した。
  この子は wiki だけを入れ、`skill-impact.md` の各節はその 4 本の識別子を指す。
human_review:
  required: true
  reason: スキルの知識の置き場を変えるため
allow:
  - match: Write|Edit
    glob: "docs/wiki/*"
---
## やること

`docs/claude/skill-wiki.md` に従い、次の 4 か所を書く。frontmatter は `docs/claude/frontmatter.md` に従い、`type: skill-wiki` にする。
既存のパターン（external-name-fixtures）と重なるものは無い。`skill-impact.md` に却下された提案は無い。

`docs/wiki/` の外（`.claude/skills/`・`docs/claude/`）は直さない。スキルの変更は次の提案チケットにある（`.claude/worktrees/skill-proposals` の `wip/proposals/todo/`、未承認）。

| 識別子 | 対象 | 動機のパターン |
|---|---|---|
| `chore-208-skill-commit-git-entry-tests` | `commit/SKILL.md`・`commit/references/test-groups.md`・`commit/references/purpose.md` | ccnavi-git-relative-path、test-run-known-failure |
| `chore-209-skill-testing-manual-probes` | `testing/references/project/manual-probes.md`・`purpose.md` | stale-manual-probe |
| `chore-210-skill-designing-scan-and-terms` | `designing/references/project/diagnostic-scan.md`・`common/plain-terms.md`・各 `purpose.md` | diagnostic-scan-read-once、internal-names-in-prose |
| `chore-211-skill-reviewing-preexisting-failure` | `reviewing/references/common/preexisting-failures.md`・`purpose.md` | preexisting-failure-misattributed |
| `chore-212-worktree-doc-absolute-path` | `docs/claude/worktree.md`（スキルではない） | ccnavi-git-relative-path |

## 1. `docs/wiki/patterns/` に足すページ（6 本）

形は `external-name-fixtures.md` と同じ（説明・原因・操作の例・回避策）。

### `ccnavi-git-relative-path.md`

```markdown
---
title: ワークツリーから相対パスで ccnavi-git.sh を呼ぶ
type: skill-wiki
description: ワークツリーの中で ../../../.ccnavi/scripts/ccnavi-git.sh と相対パスで呼ぶと、cwd によって届かないパターン
tags: [skills, feedback]
keywords: [ccnavi-git.sh, 相対パス, 絶対パス, ワークツリー, cwd, git, 入口, DENY_COMMAND_NAME_EXPANSION]
---

# ワークツリーから相対パスで ccnavi-git.sh を呼ぶと届かない

## 説明

ワークツリーの中で `sh ../../../.ccnavi/scripts/ccnavi-git.sh` と打つと、cwd によってはスクリプトに届かない。

## 原因

相対パスは cwd がワークツリーの最上段のときしか合わない。サブディレクトリに cd した後や、cwd が別のツリーに戻ったときは外れる。
commit スキルの「git の入口」と `docs/claude/worktree.md` の「基本」の表がこの書き方で、git を直接呼んだときの拒否の案内（絶対パスの形）と食い違っている。

## 操作の例

feature-207 で、サブエージェント 4 本以上がこの書き方で失敗したか迷った。コマンド名を変数に入れて試すと `DENY_COMMAND_NAME_EXPANSION` で止まった。

## 回避策

- `sh <ワークスペースルートの絶対パス>/.ccnavi/scripts/ccnavi-git.sh` と直書きする
- コマンド名やパスを変数に入れない
```

### `test-run-known-failure.md`

```markdown
---
title: 既知の失敗がある間の全件テスト
type: skill-wiki
description: 既知の失敗で python -m tests が途中で止まり、bench が数分かかり、レビュー準備中は全件を回せないパターン
tags: [skills, feedback]
keywords: [テスト, 全件, 既知の失敗, python -m tests, discover, bench, レビュー準備中, finish]
---

# 既知の失敗がある間は全件の結果が見えない

## 説明

既知の失敗が 1 本あると、`python -m tests` は最初の失敗で新しいプロセスを起こすのをやめ、残りの結果が見えない。
`tests/core/bench_deadline.py` は数分かかる。親の最後のフェーズの子を finish した後のレビュー準備中は、シェルの実行が止まって全件を回せない。

## 原因

- `python -m tests` は落ちた 1 本の出力だけを出す作り
- 実ルールを使う bench は `*x*` 形の glob の照合が入力の長さの 2 乗で伸びる
- レビュー準備中はシェルの実行が止まる

## 操作の例

feature-207 で、既知の失敗 1 本のために全件の結果が見えず、bench を前で回して数分待ち、最後の子を finish した後に全件を回せなかった。

## 回避策

- 既知の失敗がある間は、グループごとに `discover -s tests/<グループ> -t .` で回し、失敗の数を既知と比べる
- bench は名指しで、ターンの中ならバックグラウンドで回す
- 全件は、親の最後のフェーズの子を finish する前に回す
```

### `stale-manual-probe.md`

```markdown
---
title: 古くなった手動の probe
type: skill-wiki
description: tests/manual/ の probe が仕様変更に追いつかず古くなり、止まった理由を見ずに偽陽性の PASS を出すパターン
tags: [skills, feedback]
keywords: [probe, manual, tests/manual, 手動, 偽陽性, PASS, 前提]
---

# 手動の probe は静かに古くなる

## 説明

`tests/manual/` の probe が、仕様の変更（親を先に start、`review request` の `--eli5`、`decide` の入力など）に追いつかず古くなっていた。
「止まる」ことを確かめる段で、止まったことだけを見て PASS とし、止まった理由が別だった。

## 原因

- `tests/manual/` は自動テストが呼ばないので、仕様が変わっても落ちない
- 止まった理由（拒否の文面やコード）まで見ないと、前提の崩れで止まったものも PASS に見える

## 操作の例

feature-207 で `tests/manual/` の probe を回したとき、手順の前提が仕様の変更に追いついておらず、「止まった」ことだけを見て PASS としたが、止まった理由は期待と別だった。

## 回避策

- 回す前に、probe の手順が今の前提に合っているかを読む
- 止まった理由まで期待と比べる
- 前提を直したら、probe の docstring の「用意するもの」も同じ変更で直す
```

### `diagnostic-scan-read-once.md`

```markdown
---
title: 診断に走査を足すときの読みの回数と lint のテスト
type: skill-wiki
description: --explain と --lint に走査を足すと、置き場の読み直しで test_raw_share が、新しい警告で test_lint_ops が落ちるパターン
tags: [skills, feedback]
keywords: [explain, lint, 診断, 走査, 置き場, copies, test_raw_share, test_lint_ops, 合流]
---

# 診断に走査を足すと、読みの回数と既存の lint のテストに当たる

## 説明

`--explain` と `--lint` に走査を足すとき、承認済みチケットの置き場を読み直す作りにすると `tests/ticket/test_raw_share.py` が落ちる。
診断に足した新しい警告は、名前で警告の行を拾う `tests/ticket/test_lint_ops.py` の既存テストに取り違えられ、並行した子を合流した後にだけ落ちた。

## 原因

- 置き場は 1 回の起動で 1 度だけ読む約束で、`test_raw_share` が読んだ回数を数えて固定している
- `test_lint_ops` は行を名前で拾うので、別の子が足した警告と区別できない。子ごとのブランチでは通る

## 操作の例

feature-207（#210）で `--explain` と `--lint` に元のプロジェクトが無いワークツリーの走査を足した。サブエージェントの報告で、約束を知らないと読み直す作りになりかけたこと、合流後に `test_lint_ops` が落ちたことが分かった。

## 回避策

- 1 度読んだ写し（copies）を走査に引数で渡す
- 診断に警告を足したら、並行した子を合流した後に `tests/ticket` を全件回す
```

### `internal-names-in-prose.md`

```markdown
---
title: 説明に内部の呼び名を使う
type: skill-wiki
description: issue のタイトルや作業中の呼び名をそのまま提案や説明に使い、ユーザに伝わらないパターン
tags: [skills, feedback]
keywords: [用語, 呼び名, issue, タイトル, 初出, 定義, 言い換え, 説明]
---

# 内部の呼び名をそのまま書くと伝わらない

## 説明

issue のタイトルの言い方（「孤児」のワークツリー）をそのまま提案と説明に使い、ユーザに伝わらなかった。

## 原因

issue のタイトルや作業中の呼び名（比喩・略語）は、書いた人と issue を読んだ人にしか通じない。

## 操作の例

feature-207（#210）の提案と説明で「孤児のワークツリー」と書き、言い直し（「元のプロジェクトが無いワークツリー」）が要った。
用語集にもこの言い方で項目を足した（`docs/CONTEXT.md`）。

## 回避策

- 何が起きている状態かを言葉で書く
- 短い呼び名を使い続けたいときは、初出で定義を添える
```

### `preexisting-failure-misattributed.md`

```markdown
---
title: 既存の失敗の取り違え
type: skill-wiki
description: 合流後の親のブランチで見た失敗を「既存」と報告し、実は他の子の変更が原因だったパターン
tags: [skills, feedback]
keywords: [既存の失敗, 既知の失敗, 再現, 統合先, 合流, 原因, 報告, レビュー]
---

# 合流後に見た「既存の失敗」は既存とは限らない

## 説明

子の報告に「既存の失敗」とあったテストが、実は他の子の変更が原因だった。

## 原因

親のブランチに他の子の変更が入った後で失敗を確かめ、変更前の状態（統合先）では確かめなかった。

## 操作の例

feature-207 で、子の報告に「既存の失敗」とあったテストは、親のブランチに他の子の変更が入った後に確かめたもので、実は他の子の変更が原因だった。

## 回避策

- 「既存」と書く前に、統合先で同じテストが落ちるかを確かめる
- 報告やレビューでは、どの ref で再現したかを添える
```

## 2. `docs/wiki/index.md` に足す行

既存の 1 行の後に足す。frontmatter の `keywords` に `ccnavi-git.sh`・`相対パス`・`probe`・`診断`・`用語`・`既存の失敗` を足す。

```markdown
- [ccnavi-git-relative-path](patterns/ccnavi-git-relative-path.md): ワークツリーから相対パスで ccnavi-git.sh を呼ぶと届かない。原因は cwd が最上段とは限らず、スキルの記述が拒否の案内と食い違うため。絶対パスを直書きし、コマンド名を変数に入れない
- [test-run-known-failure](patterns/test-run-known-failure.md): 既知の失敗があると `python -m tests` が途中で止まり、bench は数分かかり、レビュー準備中は全件を回せない。グループごとに discover し、全件は finish の前に回す
- [stale-manual-probe](patterns/stale-manual-probe.md): 手動の probe は仕様変更で静かに古くなり、偽陽性で PASS する。回す前に前提を確かめ、止まった理由まで見る
- [diagnostic-scan-read-once](patterns/diagnostic-scan-read-once.md): 診断に走査を足すと置き場の読み直しで test_raw_share が、新しい警告で test_lint_ops が落ちる。copies を渡し、合流後に tests/ticket を回す
- [internal-names-in-prose](patterns/internal-names-in-prose.md): issue のタイトルや内部の呼び名をそのまま書くと伝わらない。状態を言葉で書き、初出で定義を添える
- [preexisting-failure-misattributed](patterns/preexisting-failure-misattributed.md): 合流後に見た「既存の失敗」が他の子の変更が原因だった。統合先で再現を確かめてから既存と書く
```

## 3. `docs/wiki/skill-impact.md` に足す節

提案 1 本に 1 節。採否はユーザが決めるまで「承認待ち」。差分は各提案の本文と同じものを写す（却下されても差分が wiki に残るように）。
frontmatter の `keywords` に `testing`・`designing`・`reviewing`・`purpose` を足す。

````markdown
## commit の git の入口を絶対パスにし、既知の失敗がある間の回し方を足す

- 提案: `chore-208-skill-commit-git-entry-tests`
- 対象: `.claude/skills/commit/SKILL.md`・`.claude/skills/commit/references/test-groups.md`・`.claude/skills/commit/references/purpose.md`（新規）
- 要点: ワークツリーや `projects/<名前>` の中では ccnavi-git.sh を絶対パスで呼ぶ。既知の失敗がある間はグループごとに回し、bench は名指しでバックグラウンド、全件は最後の子を finish する前
- 動機のパターン: [ccnavi-git-relative-path](patterns/ccnavi-git-relative-path.md)、[test-run-known-failure](patterns/test-run-known-failure.md)
- 採否: 承認待ち（採否はユーザが決める）

差分:

```diff
 git は直接呼ばず、`ccnavi-git.sh` 経由で打つ（生の git は止められるため）。以下の例はワークスペースルートの形で書く。
-ワークツリーの中では先頭を `sh ../../../.ccnavi/scripts/ccnavi-git.sh` にする。cwd ごとの呼び方は `docs/claude/worktree.md` の「基本」を見る。
+ワークツリーや `projects/<名前>` の中では、先頭を `sh <ワークスペースルートの絶対パス>/.ccnavi/scripts/ccnavi-git.sh` と直書きする。
+相対パス（`../../../`）は cwd がワークツリーの最上段のときしか届かないため。コマンド名やパスを変数に入れない（`DENY_COMMAND_NAME_EXPANSION` で止まる）。
```

```diff
 落ちたら新しいプロセスを起こすのをやめ、落ちた 1 本の出力だけを出す。ターンの終わりの hook はこれを使わない。
+既知の失敗が残っている間は `python -m tests` で全件を見られないので、グループごとに `discover -s tests/<グループ> -t .` で回し、失敗の数を既知と比べる。
+
+`tests/<グループ>/bench_*.py` はテストではなく（`discover` は拾わない）、`uv run python -m tests.core.bench_deadline` のように名指しで回す。
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
- 要点: 診断の走査は 1 度読んだ写しを渡し、警告を足したら合流後に `tests/ticket` を回す。説明では内部の呼び名を使わず、状態を言葉で書く
- 動機のパターン: [diagnostic-scan-read-once](patterns/diagnostic-scan-read-once.md)、[internal-names-in-prose](patterns/internal-names-in-prose.md)
- 採否: 承認待ち（採否はユーザが決める）

差分（新規ファイルの本文。`project/diagnostic-scan.md`）:

```diff
+- `--explain` と `--lint` に走査を足すときは、承認済みチケットの置き場を読み直さず、1 度読んだ写し（copies）を走査に引数で渡す。置き場は 1 回の起動で 1 度だけ読む約束で、`tests/ticket/test_raw_share.py` が読んだ回数を数えて固定しているため
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
````

`chore-212-worktree-doc-absolute-path`（`docs/claude/worktree.md`）はスキルの提案ではないので `skill-impact.md` には節を作らず、`ccnavi-git-relative-path` のページの「原因」と `log.md` で触れるだけにする。

## 4. `docs/wiki/log.md` に足す節

```markdown
## 2026-10-12

- 拾ったもの: feature-207 の作業で、サブエージェントの報告とメインの失敗から 8 件。うち 6 件を候補にし、2 件（委任の手順・提案の手順）は docs/claude/ と ccnavi の案内の領分として除いた
- 足したパターン: [ccnavi-git-relative-path](patterns/ccnavi-git-relative-path.md)、[test-run-known-failure](patterns/test-run-known-failure.md)、[stale-manual-probe](patterns/stale-manual-probe.md)、[diagnostic-scan-read-once](patterns/diagnostic-scan-read-once.md)、[internal-names-in-prose](patterns/internal-names-in-prose.md)、[preexisting-failure-misattributed](patterns/preexisting-failure-misattributed.md)（6 件）
- 提案: スキルの変更は親の範囲に置き場が無いので、提案チケット 4 本（`chore-208-skill-commit-git-entry-tests`・`chore-209-skill-testing-manual-probes`・`chore-210-skill-designing-scan-and-terms`・`chore-211-skill-reviewing-preexisting-failure`。`skill-impact.md`、承認待ち）。同じ誤りを持つ `docs/claude/worktree.md` は `chore-212-worktree-doc-absolute-path`
```

## 確かめること

- 6 本のページが 10〜30 行に収まり、frontmatter が `type: skill-wiki` であること
- `index.md` のリンクがすべて実在のページを指すこと
- `docs/wiki/` の外を変えていないこと
