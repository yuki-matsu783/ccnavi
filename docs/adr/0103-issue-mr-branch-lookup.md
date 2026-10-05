---
type: adr
title: issue・MR を指定された依頼では、紐づくブランチを探してユーザに確かめてから進める
description: UserPromptSubmit で依頼文の issue・MR の指定（#152・!5・URL など）を見つけ、ccnavi-branches.sh で紐づくブランチ（MR の元ブランチ・名前に番号を含むブランチ・issue を持つチケットの親のブランチ）を探してユーザに確かめる指示を足す。指示を足すだけで止めない。ホストへの接続は ccnavi-review.sh と共有する
tags: [ticket, sh-scripts, worktree]
keywords: [issue, MR, マージリクエスト, ブランチ, 既存のブランチ, UserPromptSubmit, additionalContext, ccnavi-branches.sh, branches, branch:, prompt, gh, glab, curl, GITHUB_TOKEN, GITLAB_TOKEN, ホスト]
---
# ADR-0103: issue・MR を指定された依頼では、紐づくブランチを探してユーザに確かめてから進める

状態: 採用（2026-10-04 に実装）

## 1. 状況

2026-10-04。ユーザは「#152 を直して」「!5 の指摘に対応して」のように、issue や MR を指定して作業を頼む。
そのとき、その issue や MR に紐づくブランチ（ユーザか別の機械が既に切った `feature/152-login`、MR の元ブランチ、
承認済みチケットの `branch:`）が既にあることがある。エージェントがそれを見ずに新しい `<先頭の語>-<番号>-<slug>`
（ADR-0102）を切ると、同じ作業が 2 本のブランチに分かれる。

ADR-0102 の 5 章で、親チケットは `branch:` キーで既存のブランチを名乗れるようになった。足りないのは、着手の前に
「既にあるか」を確かめる手順と、それをエージェントに思い出させる仕組み。

ユーザの決定:

1. hook は**指示を足すだけ**。作業を止めない（deny / ask にしない）
2. 候補があれば、エージェントはユーザに一覧を見せ、既存のブランチで続けるか・新しく切るか・やめるかを聞いて返事を待つ。
   候補が無ければそのまま進めてよい

## 2. 決定

| 決めたこと | なぜ | 採らなかった側 |
|---|---|---|
| UserPromptSubmit で依頼文（payload の `prompt`）から issue・MR の指定を探し、あれば `additionalContext` で指示を足す（`branchfind.prompt_context`）。判定は返さない | ユーザの決定 1。依頼の直後、エージェントが手を動かす前に届く経路はこれだけ | PreToolUse で `worktree add` を止める（決定 1 に反する。止めると判定が増える） |
| 指示の中身は「`sh {root}/.ccnavi/scripts/ccnavi-start.sh --issue N` / `--mr N` を打つ。`ccnavi-start.sh` が `ccnavi-branches.sh` で候補を探す。終了コード 0 は完了（候補が 1 件ならそのブランチのワークツリーのパスが出る。`--issue` で候補が 0 件なら Draft MR・ワークツリー・ブランチを作る）。3 は候補が複数などで何も作られていないので、一覧を見せて 3 択（既存のブランチで続ける・新しく切る・やめる）を聞き、返事を待つ。4 はホストに届かないので、出力の案内どおり MCP で代行して打ち直す。1・2 は出力の理由をユーザに伝える」。sh の表記はワークスペースルートの絶対パス（`settings.script_command`。ルールの `{root}` と同じ考え方） | プロジェクトやワークツリーの中からも打てる表記にする（docs/claude/projects.md） | 相対の `sh .ccnavi/scripts/...` |
| 既存のブランチで続けるときは、承認済みの `branch:` で使う（新しい親の提案に `branch:` を書き、承認の後に `ccnavi-git.sh switch <B>`）。承認前の提案の `branch:` は使わないことを指示に書く | ADR-0102 の 5.2 をそのまま使う。新しい経路を作らない | 指示の中で既存のブランチへ直に移らせる |
| チケット制御が disable なら指示を出さない。モードが disable なら hook 全体が何もしない（既存の入口）。dry-run でも出す | 指示が親の識別子と `branch:` の承認に寄る。承認の知らせ（`agree.news`）と同じ扱い。止めないので dry-run で黙る理由が無い | — |
| 紐づくブランチを探すのは新しい sh `ccnavi-branches.sh`。ホストは sh が読み、手元（ブランチ・ワークツリー・チケット）は実行ファイルの副命令 `ccnavi branches <issue\|mr> <番号> --result <json>` が読む | 実行ファイルはネットワークに出ない（docs/claude/exe-boundary.md）。チケットの読み方（承認済み・提案・本物とする側のあるツリー）を sh に写さない | 全部を sh で（チケットの YAML を sh で読むことになる） |
| ホストへの繋ぎ方（origin の読み方、gh / glab の疎通、curl とトークン、API の呼び方、ページ送り）を `ccnavi-common.sh` の「ホスト（GitHub / GitLab）への接続」に移し、`ccnavi-review.sh` と共有する | 同じ処理を 2 本に書くと片方だけが古くなる。失敗の文面は呼ぶ側が決める（review は止める、branches は「ホストは見ていない」と言って続ける） | 新しい共通ファイル（配る sh と、sh を写すテストの一覧がすべて増える） |
| ホストに繋げない（origin が無い・道具もトークンも無い・API が落ちた・開いた MR が 2000 本を超える）ときは止めず、手元の候補だけを出して「ホストは見ていない（理由）」と書く | 黙って空にすると「候補なし＝新しく切ってよい」と読まれる | 止める（ホストを持たない手元だけのリポジトリで使えない） |
| 出力は 1 候補 1 行（`候補 <ブランチ>  在りか=…  由来=…  MR=…  ワークツリー=…  チケット=…`）と `--json` | エージェントが読んでそのままユーザに見せられる。機械にも渡せる | — |

### 2.1 依頼文から拾う指定

拾う（`branchfind.prompt_refs`。番号は 0 で始まらない 1〜9 桁、後ろが英数字でない）:

- issue: `#152`・`＃152`、`issue 152`・`issue #152`・`Issue: 152`・`issue-152`、URL の `/issues/152`・`/-/issues/152`
- MR: `!5`（前が行頭・空白・開き括弧・句読点のときだけ）、`MR 5`・`PR #12`・`pull request 4`・`マージリクエスト 4`・`プルリク 8`、
  URL の `/pull/5`・`/pulls/5`・`/-/merge_requests/5`

拾わない（誤検知を抑える）:

- 囲みのコードブロック（```` ``` ````）の中
- `#` の前が英数字・`_`・`&`・`#`・`/`（`C#`・`&#123;`・`##12`・`page#5`）。`#` の後ろが空白（`# 見出し`）。番号でない（`#fff`）。
  0 で始まる（`#000`・`#012345`）。前が CSS の色の欄（`color: #333333`）
- `!` の前が字（`すごい!5`）や `!`（`!!5`）
- 語で読んだ分は `#`・`!` として読み直さない（`PR #12` は MR 12 だけ）。URL の中も同じ

同じ種類と番号は 1 つにまとめ、5 つまでにする。URL から読めた `owner/repo` は手がかりとして指示に添える。

### 2.2 候補の集め方

| 指定 | 候補 | 誰が読むか |
|---|---|---|
| MR | その MR の元ブランチ（GitHub は `pulls/<N>` の `head.ref`、GitLab は `merge_requests/<N>` の `source_branch`）。フォークから出たものは目印を付ける | sh（ホスト） |
| issue | その issue を参照している開いた MR の元ブランチ。GitHub は開いた PR を全部読み、題・本文の `#<N>`（前が英数字・`_`・`&`・`/` でない）か `issues/<N>`、元ブランチの名前の番号で選ぶ。GitLab は `issues/<N>/related_merge_requests` の `opened` | sh（ホスト） |
| issue | 名前に番号を含むブランチ（手元と origin。番号の前後が数字でないこと: `feature-152-x`・`fix/152-y`・`issue-152`・`x-152`） | 実行ファイル（git の ref） |
| issue | `issue: <N>` を持つ親チケット（同じリポジトリの課題だけ）の親のブランチ。承認済み（作業中・レビュー待ち・閉じた）は `branch:`（無ければ識別子）、承認待ちの提案は識別子（提案の `branch:` は使わない） | 実行ファイル（チケット） |

どの候補にも、そのブランチをチェックアウトしているツリー（元のツリーとワークツリー）と、結び付くチケット（親のブランチ名か
識別子がそのブランチのもの）を添える。順序はチケット由来・MR 由来・名前由来の順。

見るリポジトリは cwd のもの。`projects/<名前>/`（とそこから切ったワークツリー）の中から打てば、そのプロジェクトのリポジトリと
origin を見る。

### 2.3 sh と実行ファイルの間の JSON

sh が書いて `--result` で渡す（ccnavi.md の 9.13）。

```json
{"checked": true, "host": "github.com", "repo": "acme/widgets",
 "mrs": [{"number": 7, "branch": "topic/login", "state": "open", "url": "…", "title": "…", "fork": false}]}
{"checked": false, "reason": "origin が無い"}
```

実行ファイルは形の合わない要素（番号が整数でない、元ブランチが空）を落とし、制御文字を空白に置き換え、長さを切る。

## 3. 入れなかったもの

- 依頼文の指定で作業を止めること（ユーザの決定 1）
- 止めている間に通す sh への `ccnavi-branches.sh` の追加（判定が緩むため。止めている間は案内どおりの sh だけが通る）
- 別のリポジトリの参照（`other/repo#152`）を本文から拾うこと。URL の `owner/repo` は手がかりとして見せるだけで、
  どのリポジトリで探すかは cwd で決まる
- GitHub の issue の「Development」欄（GraphQL でしか読めない。ccnavi-review.sh と同じく GraphQL を塞ぐ環境がある）
- 互換の版（`CCNAVI_COMPAT`）を上げること。副命令を足しただけで既存の sh の形は変えない。古い実行ファイルでは
  `ccnavi-branches.sh` が「組み立て直すか配り直す」と言って落ちる

## 4. 試験

`tests/core/test_branchfind_refs.py`（拾う・拾わないの表、URL の数え方、まとめ方、番号の前後）、
`tests/ticket/test_branchfind.py`（UserPromptSubmit の指示と表記、チケット制御 disable・dry-run、副命令の手元の候補・
ワークツリー・承認前の `branch:` を使わない・承認済みの `branch:`・ホストの結果・MR・引数の誤り）、
`tests/sh/test_branches_sh.py`（curl の代役で GitHub と GitLab の MR の元ブランチ・issue を参照する MR、トークン無し・
origin 無し・API の失敗で「ホストは見ていない」、origin の資格情報を出さない、プロジェクトの中から打つ、引数の誤り）。
共有にしたホストへの接続は、既存の `tests/sh/test_review_*.py`・`tests/core/test_review_origin.py`・
`tests/core/test_review_403_hint.py` が ccnavi-review.sh の側から確かめる。
