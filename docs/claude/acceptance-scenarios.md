---
title: 受入シナリオを claude -p で回す
type: guide
description: projects/test-repo などを使い、エージェントの振る舞いを claude -p で回してログで確かめるシナリオの書き方と回し方
tags: [test, scenario]
keywords: [受入シナリオ, シナリオ, claude -p, stream-json, decisions.jsonl, haiku, resume, 人間の操作, tests/scenarios, test-repo]
---

# 受入シナリオを claude -p で回す

ccnavi の振る舞い（ガードの拒否、チケットの流れ、サブエージェントへの委任など）は、人間が対話で打って確かめるのではなく、
`claude -p` で 1 本ずつ回し、ログと最終状態を機械的に確かめる。シナリオは `tests/scenarios/` に置き、資産として残して繰り返し使う。

## 基本の決まり

- **既定では回さない。** 明確な指示（「シナリオを回して」など）があったときだけ回す。普段の `uv run pytest` には含めない
- **普段のワークスペースルートで回す。** コピーは作らない（コピーの管理が、確かめたいことより重くなるため）
- **ccnavi 本体のリポジトリに、ブランチ・コミット・MR・issue を作るシナリオは書かない。** そういう作業は `projects/test-repo` で行う
  （MR やブランチが残ってよいプロジェクトとして用意したもの。後で片付ける）。ccnavi 本体に対するシナリオは、拒否されること（何も変わらないこと）の確認に限る
- **回し始める前に、人間に状態を確認する。** 今の状態がきれいで最新か、`projects/test-repo` にブランチや MR ができてよいかを、人間に問う（「回し方」の 1）
- **基本は Haiku で回す。** `--model haiku`。判定の確かめ方を変える理由がある場合だけ、シナリオに別のモデルを書く
- **毎回クリアなセッションで始める。** 続きが要るシナリオだけ `--resume` でつなぐ
- **人間しかできない操作は一時停止して人間に打ってもらう。** 下の「人間の操作」を見る
- **費用の上限を置く。** 1 本ごとに `--max-budget-usd` を付ける

## 置き場

| 置くもの | 場所 |
|---|---|
| シナリオ（1 本 1 ファイル） | `tests/scenarios/<ID>-<slug>.yml` |
| 回す道具 | `tests/scenarios/`（道具は作るときに足す） |
| シナリオの一覧、機能の層、ユースケースの表 | `tests/scenarios/README.md` |

シナリオの ID は UC の番号を引き継ぐ（例: `UC-01`）。

## シナリオの形

1 本のシナリオは、次の項目を持つ。

```yaml
id: UC-01
title: ワークツリーを切らずにルートを直接編集しようとすると拒否される
layer: 1                         # 機能の層（tests/scenarios/README.md）
reqs: [REQ-PRE-01, REQ-PRE-02]   # 引く要求
status: draft                    # draft（未実行）/ verified（実ログと突き合わせ済み）
model: haiku
budget_usd: 1.0
setup:
  files:                         # 回す前に作るファイル（ワークスペースルートからのパス: 中身）。回したあとに消す
    tmp-dir/keep.txt: "中身\n"
steps:
  - prompt: "ワークツリーは切らずに README.md を Edit ツールで直接編集して。拒否されたら迂回しないで"
    resume: false                # true なら直前のセッションを --resume でつなぐ
expect:
  decisions:                     # logs/decisions.jsonl に出ていること（すべて満たす）
    - event: PreToolUse
      tool: Edit
      decision: deny
      code: DENY_PATH
      rules_include: [workspace-root]
      subject_contains: README.md
      min_count: 1               # 省略時は 1
  decisions_none:                # 出ていてはいけない判定（同じ欄で書く）
    - decision: deny
      rules_include: [guard-approved-tickets]
  files_unchanged:               # 変わっていてはいけないもの
    - README.md
  state:                         # 全 step のあとに打つコマンドと、出力の期待
    - cwd: projects/test-repo    # 省略時はワークスペースルート
      run: ls tmp-dir/keep.txt
      expect_contains: keep.txt  # expect_not_contains も書ける
  transcript_required:           # stream-json のログに出ていること
    - worktree add
  transcript_forbidden:          # stream-json のログに出てはいけない操作や値
    - "git -C"
unconfirmed:                     # 初回に実ログと突き合わせて確かめる点。確かめたら外す
  - 拒否の応答が stream-json のどの行に出るか
```

- `steps` は上から順に回す。`resume: true` の step は、直前の step のセッション ID を `--resume` で渡す
- `expect` の 3 層（判定・最終状態・振る舞い）は、どれも機械的に比べられる形で書く。「それらしく見えた」は期待にしない
- `decisions` の欄の意味と取りうる値は `tests/scenarios/README.md` の「前提」にある。`decision_not` は「その値でない」を比べる
- 人間の操作が要る step には `human: true` を付け、`prompt` の代わりに `instruction`（人間に打ってもらうコマンド）と、必要なら `check`（画面で見てもらう項目）を書く
- 実ログと突き合わせていない期待は `unconfirmed` に書き、`status` は `draft` のままにする。1 本通して期待が確かめられたら `verified` にする

## 回し方

1. **回す前に、人間に確認する。** 確認が取れるまで、1 本も回さない。回すたびに取り直す（前の回の了承を使い回さない）。
   エージェントは先に次の事実を集め、そのまま見せて、人間に問う。
   - 事実: ワークスペースルートと `projects/test-repo` の `ccnavi-git.sh status`、origin との先行・遅れ、残っているワークツリーとブランチ、
     `projects/test-repo` の開いている MR と、回すシナリオの ID・作るもの（ブランチ・MR・ファイル）
   - 問い 1: 今の状態はきれいで、最新か（他のセッションが作業中のツリーや、未コミットの変更が無いか）
   - 問い 2: `projects/test-repo` にブランチや MR ができてよいか
   - 問い 3: ccnavi 本体にブランチ・コミット・MR が残らないことを前提にしてよいか
   「いいえ」や条件が付いたときは、人間が整えるまで待つ（エージェントが勝手に整えない）
2. **回す前の状態を確かめる。**
   - ワークスペースルートと `projects/test-repo` の作業ツリーが clean である（`ccnavi-git.sh status`）。他のセッションが作業中のツリーでは回さない
   - ビルド済みの実行ファイル（`.ccnavi/bin/`）があり、判定が `enable` モードで強制される（`decisions.jsonl` の `enforced` が `true`）
   - `projects/test-repo` を使うシナリオは、統合先が origin と合っていて、前の回のワークツリーとブランチが残っていない
   - `setup.files` があれば、先に作る（回したあとに消す）
3. **step を 1 つずつ `claude -p` で回す。** 例:

   ```sh
   claude -p "<prompt>" --model haiku --output-format stream-json --verbose \
     --max-budget-usd 1.0 --no-session-persistence
   ```

   `resume: true` の step では `--resume <セッション ID>` を足す。セッション ID は、最初の step の stream-json の初期化の行から取る。
   `--no-session-persistence` は、続きが要らない step だけに付ける
4. **人間の操作の step で止める。** 止まったら、人間に打ってもらう内容（`instruction`）と、人間に見てもらう項目（`check`）を出す。
   人間が「次へ」と言うまで次の step は回さない。直す必要があると言われたら、その step を回し直す
5. **3 層で確かめる。**
   - 判定: `logs/decisions.jsonl` は他のセッションの行も混ざるので、**回した step のセッション ID（stream-json の初期化の行）の行だけ**を取り出して、
     `expect.decisions` の欄を比べる
   - 最終状態: `expect.files_unchanged` と、`expect.state` のコマンド（ワークツリー・チケットの状態など）
   - 振る舞い: stream-json のログに `transcript_forbidden` の操作が無いかを見る
6. **後片付けをする。** `setup.files` を消し、シナリオで作ったワークツリー・ブランチ・MR は、`projects/test-repo` のものだけ残してよい（後でまとめて片付ける）。
   ccnavi 本体に変更が残っていたら、期待が破れたということなので、そのまま報告して直さない（見つけたものの扱いに従い issue にする）
7. **結果を残す。** 各 step の期待と実際、ログの場所、費用を 1 枚にまとめる。期待と違えば、どの層で違ったかを書く

## 人間の操作

次は人間しかできないので、シナリオの step にして一時停止する。

- `ccnavi --agree`（承認は人間の判断）
- `ccnavi-review.sh chat` / `decide` / `confirm`
- MR のマージ（squash）
- ホストの認証や、実物のリモートへの書き込みが要る操作
- 拡張機能の見た目など、画面を見て確かめること（`check` に書く）

人間に頼むときは、打つコマンド（`instruction`）、画面で確かめること（`check`）、
「次へ」と言えば続きを回すことを書く。`check` の項目は機械では比べず、人間が見て判断する。

人間が画面を見て直す必要があると言えば、その step を回し直す（直したあとに同じ `check` をもう一度見てもらう）。

## 見つけたものの扱い

受入シナリオを回して見つけた不具合・期待のずれ・道具の不足は、**その回の中では直さない。** 次の手順で issue にする。

1. **シナリオの誤りと ccnavi の誤りを判定する。** 期待が仕様と違うならシナリオの誤り。仕様どおりに動かないなら ccnavi の誤り。判定できないものは、判定できないと書く
2. **同じ問題の issue が既にあるか確かめる。** あれば新しく作らず、そこに回した ID・ログの場所を足す（`gh issue list --search <語>` など）
3. **無ければ 1 件ずつ issue を作る。** ccnavi のリポジトリに、既存の issue の題の形（`[Bug]` / `[Feature]`）とラベルに合わせて作る
4. **issue には次を書く。**
   - 回した ID と step、入力プロンプト
   - 期待と実際
   - 違った層（判定・最終状態・振る舞い）と、その根拠になるログの場所（`logs/decisions.jsonl` の該当行、stream-json のどの行か）
   - シナリオの誤りか ccnavi の誤りか、その判定の根拠
5. **報告には、作った issue の番号を並べる。** 直した箇所があれば、どの issue に当たるかを書く

この手順は、シナリオを回した者が自分の判断で issue を作る決まりである。issue を作ったあとも、シナリオの期待を勝手に緩めない。
期待どおりに止まらなかったときは、判定の結果を issue に残し、期待は変えない。
